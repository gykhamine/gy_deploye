"""STATISTIQUES — couche données : relie les tables de la plateforme aux moteurs purs `engine/statistiques.py` et `engine/statistiques_lois.py`.

TABLE DE CALCUL EN MÉMOIRE : les lignes des tables choisies sont lues (par `enregistrements_groupe` : un groupe de tables = une grande table),
éventuellement échantillonnées, et gardées en mémoire le temps de l'analyse. Aucune structure n'est modifiée, rien n'est écrit en base.

TABLE HOMOGÈNE / HÉTÉROGÈNE : des tables de même structure (`signature_structure` : mêmes colonnes et mêmes types) forment UNE table de calcul
(lignes empilées, colonne « Table d'origine » ajoutée pour comparer les tables entre elles). Des tables de structures différentes restent
séparées : chaque colonne est alors un échantillon, et deux colonnes de tables différentes ne s'apparient que si elles ont le même nombre de lignes
(sauf pour les comparaisons d'échantillons indépendants).
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t as _i18n_t, tf

import hashlib
import logging
import re

import numpy as np

from . import identifiants_analyse as _ident   # matricule / clé d'accès : colonnes de LECTURE (voir ce module)
from . import statistiques as _s
from . import statistiques_lois as _l
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)

# None = illimité. Surchargeable dans settings.py : STATISTIQUES_LIMITES = {"lignes": 2_000_000, "tables": 50, ...}
LIMITES_DEFAUT = {"lignes": None, "lignes_total": None, "tables": None, "colonnes": None, "tables_listees": None, "variables": None,
                  "seuil_vides_pct": 5.0}   # table de calcul dont moins de ce % de lignes ont une valeur vide (sur les colonnes analysées) : ces lignes sont retirées ; 0 = jamais retirées (voir _retirer_vides)
LECTURE_PAR_PAQUET = 5000   # lignes lues par requête : la mémoire ne croît qu'avec les lignes réellement gardées


def _limites() -> dict:
    try:
        from django.conf import settings
        perso = getattr(settings, "STATISTIQUES_LIMITES", None) or {}
    except Exception:  # hors Django (tests purs)
        perso = {}
    return {**LIMITES_DEFAUT, **perso}


def _tranche(seq, borne):
    return seq if borne is None else seq[:borne]
TYPES_ANALYSABLES = _s.TYPES_QUANTI | _s.TYPES_ORDINAL | _s.TYPES_QUALI
TABLE_ORIGINE = "__table__"
INDEPENDANTS = ("t2", "var2", "mannwhitney", "auto")   # deux échantillons indépendants : des tailles différentes sont permises sans colonne de groupes
MESSAGE_STRUCTURE = (td("engine/statistiques_donnees.py:g:42:22"))


# ── Description des tables (sans lire les lignes) ────────────────────────────
def _signature(table) -> str:
    return hashlib.md5(repr(sorted((c.slug, c.type_champ) for c in table.champs.all())).encode()).hexdigest()[:8]


def decrire(table) -> dict | None:
    """Fiche d'une table pour l'interface : colonnes analysables avec leur famille probable, signature de structure. None si rien n'est analysable."""
    champs = sorted(table.champs.all(), key=lambda c: (c.ordre, c.id)) + _ident.champs_virtuels(table)   # matricule / clé : choisissables comme n'importe quelle colonne
    colonnes = [{"slug": c.slug, "nom": c.nom, "type": c.type_champ,
                 "famille": "quanti" if c.type_champ in _s.TYPES_QUANTI else "ordinal" if c.type_champ in _s.TYPES_ORDINAL else "quali"}
                for c in champs if c.type_champ in TYPES_ANALYSABLES]
    colonnes = _tranche(colonnes, _limites()["colonnes"])
    if not colonnes:
        return None
    from .vue_groupe import nom_vue
    return {"id": table.id, "nom": nom_vue(table), "groupe": bool(table.groupe_id), "signature": _signature(table), "colonnes": colonnes}


def tables_disponibles(entreprise) -> list[dict]:
    from .models import TableDynamique
    qs = TableDynamique.objects.filter(entreprise=entreprise).order_by("nom").prefetch_related("champs")
    from .vue_groupe import tables_generales   # un groupe = UNE table dans la liste de choix
    fiches = (decrire(t) for t in _tranche(tables_generales(qs), _limites()["tables_listees"]))
    return [f for f in fiches if f]


# ── Table de calcul en mémoire ───────────────────────────────────────────────
def _lire(table, limite: int | None):
    """Lit les lignes par paquets de LECTURE_PAR_PAQUET (jamais en une seule requête). limite=None : toutes ; sinon les `limite` dernières."""
    from . import parquet_store
    df = parquet_store.donnees_analytiques(table, identifiants=True)   # fichier Parquet de la table (ou du groupe) + matricule / clé d'accès (lecture)
    total = len(df)
    if limite is not None:
        df = df.tail(limite)
    return parquet_store.en_lignes(df), total


def _est_vide(v) -> bool:
    """Cellule vide : None, NaN, texte blanc."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    try:
        return v != v      # NaN
    except Exception:
        return False


def _seuil_vides() -> float:
    try:
        return max(0.0, float(_limites().get("seuil_vides_pct", 5.0)))
    except (TypeError, ValueError):
        return 5.0


def _slugs_classe(c: dict, colonnes: list) -> list[str]:
    """Colonnes « id:slug » demandées qui appartiennent à la table de calcul `c` (colonne « Table d'origine » et références invalides ignorées)."""
    slugs = []
    for ref in colonnes:
        try:
            tid, slug = _ref(ref)
        except ValueError:
            continue
        if tid in c["ids"] and slug != TABLE_ORIGINE and slug in c["champs"] and slug not in slugs:
            slugs.append(slug)
    return slugs


def _retirer_vides(c: dict, slugs: list[str], seuil: float, avis: list) -> None:
    """Valeurs vides dans une table de calcul : si la part de lignes incomplètes (au moins une valeur vide parmi `slugs`) est STRICTEMENT inférieure
    à `seuil` %, ces lignes sont retirées de l'analyse (en mémoire seulement, la table n'est pas modifiée). Sinon elles sont gardées et chaque calcul
    ignore seulement les valeurs vides qui le concernent, comme avant. Une colonne entièrement vide n'entre pas dans le calcul (elle est exclue de
    toute façon) ; la décision se prend table de calcul par table de calcul, jamais sur l'ensemble."""
    lignes = c["lignes"]
    n = len(lignes)
    if not n or not slugs:
        return
    utiles = [s for s in slugs if any(not _est_vide(l.get(s)) for l in lignes)]
    if not utiles:
        return
    garder = [i for i, l in enumerate(lignes) if not any(_est_vide(l.get(s)) for s in utiles)]
    retirees = n - len(garder)
    if not retirees:
        return
    taux = retirees / n * 100
    noms = ", ".join(c["noms"])
    if seuil > 0 and taux < seuil and garder:
        c["lignes"] = [lignes[i] for i in garder]
        c["origine"] = [c["origine"][i] for i in garder]
        avis.append(tf("stat.vides.retirees", v1=noms, v2=retirees, v3=n, v4=f"{taux:.2f}", v5=f"{seuil:g}"))
    else:
        avis.append(tf("stat.vides.conservees", v1=noms, v2=retirees, v3=n, v4=f"{taux:.2f}", v5=f"{seuil:g}"))


def memoire(tables: list, echantillon: dict | None = None, colonnes: list | None = None) -> dict:
    """{classes: [{cle, ids, noms, champs, lignes, origine, total, n_avant}], homogene, avertissements}. `echantillon` = {methode, taille, graine, remise, strate}.
    `colonnes` = références « id:slug » de l'analyse : si elle est donnée, les lignes à valeur vide sur ces colonnes sont retirées quand elles sont rares (voir _retirer_vides)."""
    lim = _limites()
    classes, par_groupe, avis, lues = {}, {}, [], 0
    for t in _tranche(tables, lim["tables"]):
        if t.groupe_id and t.groupe_id in par_groupe:   # même groupe déjà lu : ses lignes sont déjà là
            par_groupe[t.groupe_id]["ids"].append(t.id)
            continue
        if lim["lignes_total"] is not None and lues >= lim["lignes_total"]:
            avis.append(tf("engine/statistiques_donnees.py:g:99:25", v1=t.nom, v2=lim['lignes_total']))
            continue
        borne = lim["lignes"]
        if lim["lignes_total"] is not None:
            borne = min(x for x in (borne, lim["lignes_total"] - lues) if x is not None)
        lignes, total = _lire(t, borne)
        lues += len(lignes)
        c = classes.setdefault(_signature(t), {"cle": _signature(t), "ids": [], "noms": [], "champs": {}, "lignes": [], "origine": [], "total": 0})
        c["ids"].append(t.id)
        c["noms"].append(t.nom)
        c["champs"].update({ch.slug: ch for ch in _ident.champs_analysables(t)})
        c["lignes"] += lignes
        c["origine"] += [t.nom] * len(lignes)
        c["total"] += total
        if total > len(lignes):
            avis.append(tf("engine/statistiques_donnees.py:g:114:25", v1=t.nom, v2=len(lignes), v3=total))
        if t.groupe_id:
            par_groupe[t.groupe_id] = c
    info = None
    seuil = _seuil_vides()
    for c in classes.values():
        c["n_avant"] = len(c["lignes"])
        if colonnes:
            _retirer_vides(c, _slugs_classe(c, colonnes), seuil, avis)      # avant l'échantillon : on tire parmi des lignes complètes
        if echantillon and echantillon.get("methode") in _s.METHODES_ECHANTILLON and c["lignes"]:
            methode, strates = echantillon["methode"], None
            if methode == "stratifie":
                slug = echantillon.get("strate")
                if slug in c["champs"]:
                    strates = [ligne.get(slug) for ligne in c["lignes"]]
                else:
                    methode = "aleatoire"
                    avis.append(tf("engine/statistiques_donnees.py:g:128:33", v1=', '.join(c['noms'])))
            idx = _s.echantillonner(len(c["lignes"]), methode, echantillon["taille"], echantillon.get("graine"), strates, bool(echantillon.get("remise")))
            c["lignes"] = [c["lignes"][i] for i in idx]
            c["origine"] = [c["origine"][i] for i in idx]
            info = {"methode": _s.METHODES_ECHANTILLON[methode], "taille": echantillon["taille"], "graine": echantillon.get("graine"), "remise": bool(echantillon.get("remise"))}
    return {"classes": list(classes.values()), "homogene": len(classes) <= 1, "avertissements": avis, "echantillon": info}


def resume_memoire(memo: dict) -> dict:
    return {"homogene": memo["homogene"], "avertissements": memo["avertissements"], "echantillon": memo["echantillon"],
            "tables_de_calcul": [{"tables": c["noms"], "lignes": len(c["lignes"]), "lignes_avant": c["n_avant"]} for c in memo["classes"]]}


def _ref(texte) -> tuple[int, str]:
    tid, _sep, slug = str(texte or "").partition(":")
    if not tid.strip().isdigit() or not slug.strip():
        raise ValueError(_i18n_t("engine/statistiques_donnees.py:g:144:26"))
    return int(tid), slug.strip()


def _ordre(champ) -> list[str] | None:
    texte = getattr(champ, "options_choix", "") or ""
    return [o.strip() for o in re.split(r"[\n,]", texte) if o.strip()] or None


def variable(memo: dict, ref: str, forces: dict | None = None) -> dict:
    """{nom, genre, valeurs, ref, ordre, classe, table} d'une colonne « id:slug ». ValueError (message lisible) si elle n'est pas analysable."""
    tid, slug = _ref(ref)
    classe = next((c for c in memo["classes"] if tid in c["ids"]), None)
    if classe is None:
        raise ValueError(_i18n_t("engine/statistiques_donnees.py:g:158:26"))
    if slug == TABLE_ORIGINE:
        valeurs, nom, type_champ, champ = classe["origine"], _i18n_t("engine/statistiques_donnees.py:g:160:62"), "texte", None
    else:
        champ = classe["champs"].get(slug)
        if champ is None:
            raise ValueError(_i18n_t("engine/statistiques_donnees.py:g:164:30"))
        valeurs, nom, type_champ = [ligne.get(slug) for ligne in classe["lignes"]], champ.nom, champ.type_champ
    genre = _s.classer(valeurs, type_champ, (forces or {}).get(ref))
    if genre == "exclue":
        raise ValueError(tf("engine/statistiques_donnees.py:g:168:26", v1=nom))
    return {"nom": nom + (f" ({classe['noms'][0]})" if len(memo["classes"]) > 1 else ""), "genre": genre, "valeurs": valeurs, "ref": ref,
            "ordre": _ordre(champ) if champ is not None and genre == "quali_ordinale" and champ.type_champ == "choix_unique" else None,
            "classe": classe["cle"], "table": ", ".join(classe["noms"])}


# ── Descriptives ─────────────────────────────────────────────────────────────
def _decrire(v: dict) -> dict:
    if v["genre"] == "date":
        return _s.decrire_date(v["valeurs"])
    if _s.est_quanti(v["genre"]):
        return _s.decrire_quanti(v["valeurs"], v["genre"] == "quanti_discrete")
    return _s.decrire_quali(v["valeurs"], v["genre"] == "quali_ordinale", v["ordre"])


def _par_groupe(v: dict, g: dict) -> dict:
    """Variable décrite modalité par modalité de `g` (résumé quanti par groupe, ou tableau croisé pour une variable qualitative)."""
    if g["classe"] != v["classe"]:
        raise ValueError(_i18n_t("engine/statistiques_donnees.py:g:186:26"))
    if _s.est_quanti(g["genre"]):
        raise ValueError(_i18n_t("engine/statistiques_donnees.py:g:188:26"))
    paires = [(valeur, _s.etiquette(m)) for valeur, m in zip(v["valeurs"], g["valeurs"])]
    groupes: dict = {}
    for valeur, m in paires:
        if m is not None:
            groupes.setdefault(m, []).append(valeur)
    if len(groupes) > _s.LIMITES["groupes"]:
        raise ValueError(tf("engine/statistiques_donnees.py:g:195:26", v1=g['nom'], v2=len(groupes), v3=_s.LIMITES['groupes']))
    if _s.est_quanti(v["genre"]):
        cles = ("n", "moyenne", "mediane", "ecart_type", "min", "max", "q1", "q3", "boite")
        lignes = []
        for m, vals in sorted(groupes.items()):
            d = _s.decrire_quanti(vals, v["genre"] == "quanti_discrete")
            lignes.append({"groupe": m, **{k: d.get(k) for k in cles}})
        return {"variable_groupe": g["nom"], "quanti": lignes}
    mods = sorted({e for e in (_s.etiquette(x) for x in v["valeurs"]) if e is not None})
    croise = []
    for m, vals in sorted(groupes.items()):
        etiq = [_s.etiquette(x) for x in vals]
        tot = sum(1 for e in etiq if e is not None)
        croise.append({"groupe": m, "total": tot, "effectifs": [sum(1 for e in etiq if e == mod) for mod in mods]})
    return {"variable_groupe": g["nom"], "modalites": mods, "croise": croise}


def _decrire_lot(variables: list) -> list:
    """Descriptives de plusieurs colonnes, calculées ici une à une."""
    return [_decrire(v) for v in variables]


def descriptives(memo: dict, refs: list[str], forces: dict | None = None, par: str | None = None) -> dict:
    """Descriptives complètes de chaque colonne choisie (+ découpage par groupes, + liaisons entre colonnes d'une même table de calcul)."""
    groupe = None
    if par:
        try:
            groupe = variable(memo, par, forces)
        except ValueError as e:
            groupe = {"erreur": str(e)}
    sortie, par_classe = [], {}
    choisies = []
    for ref in _tranche(refs, _limites()["variables"]):
        try:
            choisies.append((ref, variable(memo, ref, forces), None))
        except ValueError as e:
            choisies.append((ref, None, str(e)))
    decrites = iter(_decrire_lot([v for _, v, _ in choisies if v is not None]))
    for ref, v, erreur in choisies:
        if v is None:
            sortie.append({"ref": ref, "erreur": erreur})
            continue
        d = next(decrites)
        if groupe is not None and v["genre"] != "date":
            try:
                if "erreur" in groupe:
                    raise ValueError(groupe["erreur"])
                d["par"] = _par_groupe(v, groupe) if v["ref"] != groupe["ref"] else None
            except ValueError as e:
                d["par"] = {"erreur": str(e)}
        sortie.append({"ref": ref, "nom": v["nom"], "table": v["table"], **d})
        if v["genre"] != "date":      # une colonne de dates n'entre pas dans les liaisons (les séries temporelles s'en chargent)
            par_classe.setdefault(v["classe"], []).append(v)
    relations = []
    for classe, vs in par_classe.items():
        if len(vs) >= 2:
            rel = _s.relations([{"nom": v["nom"], "genre": v["genre"], "valeurs": v["valeurs"]} for v in vs])
            relations.append({"table": vs[0]["table"], **rel})
    return _s.nettoyer({"variables": sortie, "relations": relations, "synthese": _s.synthese(sortie, relations), **resume_memoire(memo)})


# ── Analyses GROUPÉES : plusieurs variables ensemble (voir engine/statistiques_groupes.py) ──────────────────────────────────────────
MODES_GROUPES = ("continues", "qualitatives", "continues_par_quali")


def ensembles(memo: dict, mode: str, refs: list[str], forces: dict | None = None, par: str | None = None) -> dict:
    """Analyse groupée des colonnes choisies : `continues` (variables continues entre elles), `qualitatives` (variables qualitatives entre elles) ou
    `continues_par_quali` (les variables continues comparées entre les groupes de la colonne qualitative `par`). Une analyse par table de calcul
    (les colonnes doivent être alignées ligne à ligne) ; les colonnes qui ne conviennent pas au mode sont simplement laissées de côté."""
    from . import statistiques_groupes as _g
    if mode not in MODES_GROUPES:
        raise ValueError(_gt("sg.err.mode"))
    variables, avert = [], []
    for ref in _tranche(refs, _limites()["variables"]):
        try:
            variables.append(variable(memo, ref, forces))
        except ValueError as e:
            avert.append(_gtf("sg.avert.ignoree", v1=ref, v2=str(e)))
    groupe = None
    if mode == "continues_par_quali":
        if not par:
            raise ValueError(_gt("sg.err.une_quali_groupe"))
        groupe = variable(memo, par, forces)
        variables = [v for v in variables if v["ref"] != par and v["classe"] == groupe["classe"]]
    par_classe = {}
    for v in variables:
        par_classe.setdefault(v["classe"], []).append(v)
    analyses, derniere = [], None
    for classe, vs in par_classe.items():
        try:
            if mode == "continues":
                res = _g.continues(vs)
            elif mode == "qualitatives":
                res = _g.qualitatives(vs)
            else:
                res = _g.continues_par_qualitative(vs, groupe)
        except ValueError as e:
            derniere = str(e)
            continue
        analyses.append({"table": vs[0]["table"], **res})
    if not analyses:
        raise ValueError(derniere or _gt("sg.err.aucune"))
    sortie = {"mode": mode, "analyses": analyses, **resume_memoire(memo)}
    sortie["avertissements"] = list(sortie.get("avertissements") or []) + avert
    return _s.nettoyer(sortie)


# ── Descriptives d'une grande table : un lot = UNE colonne (ou les liaisons d'une classe), lue dans le Parquet par celui qui calcule ─────
# Le lot ne contient que des références ; le worker lit lui-même sa colonne (lecture seule), applique l'échantillon éventuel et appelle
# les MÊMES fonctions (`_decrire`, `_par_groupe`, `_s.relations`). Aucune ligne ne passe par Spark ni par le nœud initiateur.























# ── Inférence ────────────────────────────────────────────────────────────────
def tester(memo: dict, cle: str, refs: dict, params: dict, forces: dict | None = None) -> dict:
    """Exécute un test sur les colonnes choisies : refs = {a, b, groupe, explicatives: [..]} (références « id:slug »)."""
    variables = {}
    for role in ("a", "b", "groupe", "groupe2"):
        if refs.get(role):
            variables[role] = variable(memo, refs[role], forces)
    if refs.get("explicatives"):
        variables["explicatives"] = [variable(memo, r, forces) for r in refs["explicatives"][:12]]
    tous = [v for k, v in variables.items() if k != "explicatives"] + variables.get("explicatives", [])
    for v in tous:
        if v["genre"] == "date":
            raise ValueError(tf("engine/statistiques_donnees.py:g:258:30", v1=v['nom']))
    tailles = {len(v["valeurs"]) for v in tous}
    independants = cle in INDEPENDANTS and not variables.get("groupe")
    if len(tailles) > 1 and not independants:
        detail = " ; ".join(tf("engine/statistiques_donnees.py:g:262:29", v1=v['nom'], v2=len(v['valeurs'])) for v in tous[:4])
        raise ValueError(tf("engine/statistiques_donnees.py:g:263:26", v1=detail))
    r = _s.executer(cle, variables, params)
    r["variables"] = [{"role": k, "nom": v["nom"], "genre": _s.GENRES[v["genre"]], "n": len(v["valeurs"])} for k, v in variables.items() if k != "explicatives"] + \
                     [{"role": "explicative", "nom": v["nom"], "genre": _s.GENRES[v["genre"]], "n": len(v["valeurs"])} for v in variables.get("explicatives", [])]
    r.update(resume_memoire(memo))
    return _s.nettoyer(r)


# ── Probabilités liées aux données ───────────────────────────────────────────
def ajuster_colonne(memo: dict, ref: str, forces: dict | None = None, x=None) -> dict:
    """Lois qui s'ajustent le mieux à une colonne numérique (classées par AIC) + probabilité empirique P(X ≤ x) lue dans les données."""
    v = variable(memo, ref, forces)
    if not _s.est_quanti(v["genre"]):
        raise ValueError(tf("engine/statistiques_donnees.py:g:276:26", v1=v['nom']))
    vals = _s._nombres(v["valeurs"])
    sortie = {"nom": v["nom"], "n": int(vals.size), "moyenne": float(vals.mean()) if vals.size else None,
              "ecart_type": float(vals.std(ddof=1)) if vals.size > 1 else None, "ajustements": _l.ajuster(vals.tolist())}
    if x is not None and vals.size:
        sortie["empirique"] = {"x": x, "p_inf": float((vals <= x).mean()), "p_sup": float((vals >= x).mean())}
    return _s.nettoyer({**sortie, **resume_memoire(memo)})
