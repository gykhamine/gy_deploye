"""GÉOSPATIAL — couche données : relie une table de la plateforme au moteur pur `engine/geospatial.py`.

  * Une colonne de type « geolocalisation » = un objet suivi ; une ligne = sa position (la première colonne date/datetime, s'il y en
    a une, donne l'axe du temps ; sinon l'ordre des lignes).
  * `analyser_table` lit ces colonnes (SQL ou NoSQL, via Enregistrement.to_dict) et renvoie l'analyse complète.
  * `enregistrer` range chaque simulation lancée par l'utilisateur comme une LIGNE de la table ordinaire « Analyses géospatiales »
    (même mécanisme que la mémoire de décision : blockchain, ontologie, KPI, dashboards, et échantillons TABLE du modèle fantôme).
  * `resume_entreprise` alimente l'état fusionné (engine/fusion.py, section « geo ») : c'est ce qui fait entrer les trajectoires
    dans les règles de recommandation, donc dans le pipeline de décision.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tf

import hashlib
import json
import logging
import re
import time

import numpy as np

from django.core.cache import cache
from django.utils import timezone

from . import geospatial as _g
from . import logistique as _l

logger = logging.getLogger(__name__)

TYPES_TEMPS = ("datetime", "horodatage", "date")
FUSION = {"tables": 10, "lignes": 500, "echantillons": 100}   # budget de l'analyse faite pour l'état fusionné (une par table)


# ── Table client : une position PAR CLIENT (jamais une suite linéaire de lignes) ─────────────────────────────────────────────────
HISTORIQUE_CLIENT = 50          # dernières positions gardées par client (analyse exploratoire en cas de problème)
NON_CLIENTS = ("agent", "administrateur")   # valeurs de la colonne « id_client » écrites pour un agent ou l'administrateur


def colonne_id_client(table):
    """Colonne « ID client » de la table (celle qui porte le numéro du client qui a enregistré la ligne), ou None."""
    return table.champs.filter(type_champ="id_client").first()


def client_de(ligne: dict, slug_id: str | None) -> str:
    """Numéro du client qui porte la ligne, « » si la colonne manque, est vide ou désigne un agent/l'administrateur (la ligne reste alors un point à part)."""
    v = str(ligne.get(slug_id) or "").strip() if slug_id else ""
    return "" if v.lower() in NON_CLIENTS else v


def historique_clients(lignes: list, slug_id: str, slugs_geo, parse_geo, slug_temps: str | None = None, garder: int = HISTORIQUE_CLIENT) -> dict:
    """lignes = [(id de ligne, dict)] en ordre chronologique -> {(client, colonne de position): [(id de ligne, (lat, lon, libellé), date), ...]}.
    Un client qui commande dix fois depuis le même endroit n'a qu'UNE position (mise à jour, pas de doublon) ; sinon la nouvelle position
    s'ajoute à son historique, limité à ses `garder` dernières. Le dernier élément est sa position actuelle."""
    hist = {}
    for eid, ligne in lignes:
        client = client_de(ligne, slug_id)
        for slug in slugs_geo if client else ():
            g = parse_geo(ligne.get(slug))
            if not g:
                continue
            suite = hist.setdefault((client, slug), [])
            pas = (eid, g, ligne.get(slug_temps) if slug_temps else None)
            if suite and suite[-1][1][:2] == g[:2]:
                suite[-1] = pas
            else:
                suite.append(pas)
                del suite[:-garder]
    return hist


def colonnes_geo(table, slugs=None) -> list:
    champs = list(table.champs.filter(type_champ="geolocalisation").order_by("ordre", "id"))
    return [c for c in champs if not slugs or c.slug in slugs]


def lecture(objets=None, fenetre=None) -> tuple[int | None, int | None]:
    """(objets lus, pas lus). Absents, vides ou invalides : None = TOUS, sans plafond. Une valeur donnée borne la lecture (au moins 1 objet, 10 pas).
    Un plafond n'existe que si le serveur en règle un dans LIMITES['objets_max'] / ['pas_max'] / ['cellules_max'] (None par défaut) ; alors il s'applique
    aussi aux valeurs absentes, et le budget de cellules fait rétrécir la fenêtre, jamais le nombre d'objets."""
    L = _g.LIMITES

    def _lu(v, mini, plafond):
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            n = 0
        return plafond if n <= 0 else max(mini, min(n, plafond) if plafond else n)
    o, p = _lu(objets, 1, L["objets_max"]), _lu(fenetre, 10, L["pas_max"])
    if L["cellules_max"] and o and p:
        p = max(10, min(p, L["cellules_max"] // o))
    return o, p


def colonne_temps(table, choix=None):
    """Colonne qui donne l'axe du temps. `choix` = slug choisi par la personne ; « - » = aucune (ordre des lignes) ; absent ou invalide = la
    première colonne date/datetime de la table (comportement historique, pour les appelants qui ne choisissent rien)."""
    if choix == "-":
        return None
    if choix:
        c = table.champs.filter(slug=choix, type_champ__in=TYPES_TEMPS).first()
        if c is not None:
            return c
    return table.champs.filter(type_champ__in=TYPES_TEMPS).order_by("ordre", "id").first()


def matrice_table(table, slugs=None, limite: int | None = None, pas_s: float | None = None, nommer_par_table: bool = True, objets: int | None = None,
                  choix_temps: str | None = None, par_client: bool = True):
    """(matrice, empreinte) ou (None, None) si la table n'a aucune colonne de position. L'empreinte change dès qu'une ligne s'ajoute.
    Quand la table n'a qu'UNE colonne de position, l'objet est la table : il prend le nom de la table (sinon chaque colonne garde son nom)."""
    from .visualization import _parse_geo
    champs = colonnes_geo(table, slugs)
    if not champs:
        return None, None
    from . import parquet_store
    lignes = parquet_store.lignes_analytiques(table, limite)   # [{"_id", **valeurs}] : fichier Parquet
    temps = colonne_temps(table, choix_temps)
    dicts, col_id = lignes, (colonne_id_client(table) if par_client else None)
    hist = historique_clients([(d["_id"], d) for d in dicts], col_id.slug, [c.slug for c in champs], _parse_geo, temps.slug if temps else None) if col_id else {}
    if hist:   # table client : un objet par client (ses 50 dernières positions distinctes), pas une suite de lignes
        multi, noms = len(champs) > 1, {c.slug: c.nom for c in champs}
        cols = [(f"{s}@{c}", tf("engine/geo_donnees.py:g:114:30", v1=c, v2=noms[s]) if multi else tf("engine/geo_donnees.py:g:114:70", v1=c)) for c, s in hist]
        etapes = sorted(((eid, f"{s}@{c}", g, d) for (c, s), suite in hist.items() for eid, g, d in suite), key=lambda x: x[0])
        mat = _g.construire([{k: f"{g[0]}, {g[1]}", "_t": d} for _e, k, g, d in etapes], cols, _parse_geo, "_t" if temps else None,
                            pas_s or _g.PARAMETRES["pas_defaut_s"], max_objets=objets, max_pas=limite)
        return mat, f"{len(lignes)}:{lignes[-1]["_id"] if lignes else 0}:{len(champs)}"
    mat = _g.construire(dicts, [(c.slug, c.nom) for c in champs], _parse_geo,
                        temps.slug if temps else None, pas_s or _g.PARAMETRES["pas_defaut_s"], max_objets=objets, max_pas=limite)
    if nommer_par_table and len(champs) == 1:
        mat["noms"] = [table.nom]
    mat["lignes_total"] = table.enregistrements_groupe.count()     # plage de temps : dit si la lecture est tronquée aux dernières lignes
    return mat, f"{len(lignes)}:{lignes[-1]["_id"] if lignes else 0}:{len(champs)}"


def empreinte_table(table, slugs=None, limite: int | None = None) -> str | None:
    """Même valeur que l'empreinte de `matrice_table`, sans lire les lignes : sert au rafraîchissement léger de la carte."""
    from django.db.models import Count, Max
    if not colonnes_geo(table, slugs):
        return None
    a = table.enregistrements_groupe.order_by().aggregate(n=Count("id"), dernier=Max("id"))
    return f"{min(a['n'], limite) if limite else a['n']}:{a['dernier'] or 0}:{len(colonnes_geo(table, slugs))}"


def fiche_table(t) -> dict:
    """Ce que la personne peut choisir dans une table : ses colonnes de position et ses colonnes de date (rien n'est choisi à sa place)."""
    positions = colonnes_geo(t)
    dates = list(t.champs.filter(type_champ__in=TYPES_TEMPS).order_by("ordre", "id"))
    from .vue_groupe import nom_vue
    return {"id": t.id, "nom": nom_vue(t), "nb": len(positions), "date": bool(dates),
            "positions": [{"slug": c.slug, "nom": c.nom} for c in positions], "temps": [{"slug": c.slug, "nom": c.nom} for c in dates]}


def tables_geo(entreprise, exclure_id=None) -> list[dict]:
    """Tables de l'entreprise qui portent au moins une colonne de position : candidates pour s'ajouter à l'analyse (une table = un objet)."""
    from .models import TableDynamique
    from .vue_groupe import ids_du_groupe, tables_generales   # un groupe = UNE table (et le groupe de la table ouverte est exclu en entier)
    return [fiche_table(t) for t in tables_generales(TableDynamique.objects.filter(entreprise=entreprise, champs__type_champ="geolocalisation")
            .exclude(id__in=ids_du_groupe(exclure_id)).distinct().order_by("nom"))[:99]]


def matrice_sources(sources: list, limite: int | None = None, pas_s: float | None = None, objets: int | None = None, temps: dict | None = None):
    """sources = [(table, slugs | None), ...]. Une seule table : chemin historique (identifiants = slugs de colonnes). Plusieurs : chaque table garde
    SON axe de temps, puis `geospatial.fusionner` les ramène sur un axe commun ; identifiants « <id table>:<slug> », nom = celui de la table
    (« table · colonne » si elle porte plusieurs positions). `temps` = {id table: slug de la colonne de date choisie, ou « - » pour l'ordre des lignes}.
    Retourne (matrice, empreinte) ou (None, None)."""
    temps = temps or {}
    if not sources:
        return None, None
    if len(sources) == 1:
        return matrice_table(sources[0][0], sources[0][1], limite, pas_s, objets=objets, choix_temps=temps.get(sources[0][0].id))
    mats, empreintes = [], []
    for table, slugs in sources:
        m, e = matrice_table(table, slugs, limite, pas_s, objets=objets, choix_temps=temps.get(table.id))
        if m is None:
            continue
        plusieurs = len(m["ids"]) > 1
        m["ids"] = [f"{table.id}:{i}" for i in m["ids"]]
        m["noms"] = [f"{table.nom} · {n}" if plusieurs else table.nom for n in m["noms"]]
        m["source"] = table.nom
        mats.append(m)
        empreintes.append(f"{table.id}={e}")
    mat = _g.fusionner(mats, max_objets=objets, max_pas=limite) if mats else None
    return (mat, "|".join(empreintes)) if mat is not None else (None, None)


def empreinte_sources(sources: list, limite: int | None = None) -> str | None:
    """Empreinte légère (sans lire les lignes) : la même valeur que celle de `matrice_sources`."""
    if len(sources) == 1:
        return empreinte_table(sources[0][0], sources[0][1], limite)
    morceaux = [f"{t.id}={e}" for t, slugs in sources if (e := empreinte_table(t, slugs, limite))]
    return "|".join(morceaux) or None


def analyser_sources(sources: list, limite: int | None = None, objets: int | None = None, temps: dict | None = None, **params) -> dict | None:
    debut = time.monotonic()
    mat, empreinte = matrice_sources(sources, limite, objets=objets, temps=temps)
    if mat is None:
        return None
    resultat = _g.analyser(mat, **params)
    resultat["empreinte"] = empreinte
    resultat["lecture"] = {"objets_lus": len(mat["ids"]), "objets_total": sum(len(colonnes_geo(t, s)) for t, s in sources), "pas_lus": len(mat["t"]), "fenetre": limite,
                           "duree_s": round(time.monotonic() - debut, 1)}
    resultat["sources"] = [{"id": t.id, "nom": t.nom} for t, _s in sources]
    return resultat


def analyser_table(table, slugs=None, limite: int | None = None, objets: int | None = None, **params) -> dict | None:
    mat, empreinte = matrice_table(table, slugs, limite, objets=objets)
    if mat is None:
        return None
    resultat = _g.analyser(mat, **params)
    resultat["empreinte"] = empreinte
    return resultat


def matrice_archive(table, lignes: list, noms_archives: dict | None = None, slugs=None, pas_s: float | None = None, objets: int | None = None):
    """Matrice d'analyse construite sur des lignes ARCHIVÉES [{"cle", "valeurs": {slug: valeur}}] (voir geo_archives), pour les rejouer comme des données
    vivantes. Objets = les colonnes de position de la table + celles que l'archive connaît encore (`noms_archives` : slug -> nom, colonnes.json) même si
    la colonne a été retirée de la table depuis. (matrice, empreinte) ou (None, None) s'il n'y a rien à rejouer."""
    from .visualization import _parse_geo
    if not lignes:
        return None, None
    dicts = [l["valeurs"] for l in lignes]
    champs = colonnes_geo(table, slugs)
    noms = {c.slug: c.nom for c in champs}
    temps = colonne_temps(table)
    vus = {k for d in dicts for k, v in d.items() if v not in (None, "")}
    for slug, nom in (noms_archives or {}).items():   # objet dont la colonne n'existe plus : l'archive le garde, on le rejoue aussi
        if slug in noms or (temps and slug == temps.slug) or slug not in vus or (slugs and slug not in slugs):
            continue
        exemple = next((d[slug] for d in dicts if d.get(slug) not in (None, "")), None)
        if _parse_geo(exemple):
            noms[slug] = nom
    colonnes = [(slug, nom) for slug, nom in noms.items() if slug in vus]
    if not colonnes:
        return None, None
    mat = _g.construire(dicts, colonnes, _parse_geo, temps.slug if temps else None, pas_s or _g.PARAMETRES["pas_defaut_s"], max_objets=objets,
                        max_pas=max(len(dicts), 10))
    return mat, f"archive:{lignes[0]['cle']}:{lignes[-1]['cle']}:{len(lignes)}"


def analyser_archive(table, lignes: list, noms_archives: dict | None = None, slugs=None, objets: int | None = None, **params) -> dict | None:
    mat, empreinte = matrice_archive(table, lignes, noms_archives, slugs, objets=objets)
    if mat is None:
        return None
    resultat = _g.analyser(mat, **params)
    resultat["empreinte"] = empreinte
    resultat["lecture"] = {"objets_lus": len(mat["ids"]), "objets_total": len(mat["ids"]), "pas_lus": len(mat["t"]), "fenetre": len(lignes), "duree_s": 0.0}
    resultat["sources"] = [{"id": table.id, "nom": table.nom}]
    return resultat


def analyser_scenario(**kw) -> dict:
    """Trajectoires simulées (aucune donnée lue ni écrite) : pour voir la carte vivre et tester les analyses."""
    scen = {k: kw.pop(k) for k in ("n_objets", "n_pas", "graine") if k in kw}
    resultat = _g.analyser(_g.scenario(**scen), **kw)
    resultat["empreinte"] = "scenario"
    return resultat


# ── Persistance (table ordinaire « Analyses géospatiales ») ─────────────────
def enregistrer(entreprise, utilisateur, table, resultat: dict, nom_source: str) -> int | None:
    """Range l'analyse/simulation comme une ligne. None si la même simulation vient d'être enregistrée (moins de 10 min)."""
    if resultat.get("vide"):
        return None
    r = resultat["resume"]
    signature = hashlib.sha1(json.dumps([entreprise.id, getattr(table, "id", 0), resultat.get("empreinte"), resultat["parametres"]],
                                        sort_keys=True).encode()).hexdigest()
    if not cache.add(f"geo_enreg:{signature}", 1, 600):
        return None
    from . import memoire_decision as _md
    enr = _md._ajouter_ligne(entreprise, utilisateur, "geo", {
        "titre": f"{nom_source} ({timezone.now():%Y-%m-%d %H:%M})", "table_source": nom_source, "nb_objets": r["objets"],
        "nb_positions": r["positions"], "distance_totale_m": r["distance_m"], "vitesse_moyenne_ms": r["vitesse_moy_ms"],
        "nb_rencontres": r["rencontres"], "nb_collisions_prevues": r["collisions_prevues"], "proba_collision_max": r["proba_max"],
        "erreur_prevision_m": r["erreur_prevision_m"], "gain_prevision": r["gain_prevision"], "indice_clark_evans": r["clark_evans"],
        "parametres": resultat["parametres"], "resume": {"resume": r, "validation": resultat.get("validation"),
                                                        "alertes": resultat["alertes"][:10]},
        "date": timezone.now().isoformat()})
    return enr.id


# ── État fusionné : entrée des trajectoires dans le pipeline de décision ────
def resume_entreprise(entreprise) -> list[dict]:
    """Une fiche compacte par table qui porte des colonnes de position (au plus FUSION['tables'])."""
    from .models import TableDynamique
    sortie = []
    for table in TableDynamique.objects.filter(entreprise=entreprise, champs__type_champ="geolocalisation").distinct().order_by("id")[:FUSION["tables"]]:
        try:
            res = analyser_table(table, limite=FUSION["lignes"], echantillons=FUSION["echantillons"])
        except Exception:   # une table illisible ne bloque jamais les autres
            logger.exception(_i18n_t("engine/geo_donnees.py:g:285:30"), table.id)
            continue
        if not res or res.get("vide"):
            continue
        noms = {o["id"]: o["nom"] for o in res["objets"]}
        r = res["resume"]
        sortie.append({
            "id": table.id, "nom": table.nom, "objets": r["objets"], "positions": r["positions"], "seuil_m": res["parametres"]["seuil_collision_m"],
            "proba_max": r["proba_max"], "collisions_prevues": r["collisions_prevues"], "rencontres": r["rencontres"],
            "rencontre_min_m": r["rencontre_min_m"], "sauts": r["sauts"], "immobiles": r["immobiles"], "manquantes": r["manquantes"],
            "concentration_part": r["concentration_part"], "erreur_prevision_m": r["erreur_prevision_m"], "gain_prevision": r["gain_prevision"],
            "top_collisions": [{"a": noms.get(c["a"], c["a"]), "b": noms.get(c["b"], c["b"]), "proba": c["proba"], "dans_s": c["dans_s"],
                                "dmin_m": c["dmin_moyen_m"]} for c in res["prevision"].get("collisions", [])[:3]],
            "top_rencontres": [{"a": noms.get(e["a"], e["a"]), "b": noms.get(e["b"], e["b"]), "dmin_m": e["dmin_m"]} for e in res["rencontres"][:3]]})
    return sortie


def resume_logistique(entreprise) -> dict | None:
    try:
        return dernier_plan(entreprise)
    except Exception:   # la mémoire logistique ne bloque jamais la fusion
        logger.exception("Fusion logistique : dernier plan illisible (entreprise=%s).", entreprise.id)
        return None


# ── Logistique : véhicules = objets suivis de la table ; commandes = lignes d'une autre table ───────────────────────────────
MOTS_DEMANDE = ("quantite", _gtfr("engine/geo_donnees.py:318:29"), "qte", "poids", "volume", "colis", "demande", "charge", "unites", _gtfr("engine/geo_donnees.py:318:108"))
MOTS_ECHEANCE = ("echeance", _gtfr("engine/geo_donnees.py:319:30"), "limite", "avant", "livraison", "deadline", "date_liv")
MOTS_DEBUT = ("debut", _gtfr("engine/geo_donnees.py:320:24"), "ouverture", "a_partir", "à_partir", "depuis", "fenetre", _gtfr("engine/geo_donnees.py:320:93"))   # ouverture de la fenêtre horaire (le véhicule attend)
MOTS_PRIORITE = ("priorite", _gtfr("engine/geo_donnees.py:321:30"), "urgence", "criticite", _gtfr("engine/geo_donnees.py:321:67"))
MOTS_SERVICE = ("duree_arret", "durée_arrêt", "temps_arret", "dechargement", _gtfr("engine/geo_donnees.py:322:80"), "service")   # minutes
MOTS_EXIGE = ("exige", "specialite", _gtfr("engine/geo_donnees.py:323:38"), "contrainte", "type_vehicule", "categorie", _gtfr("engine/geo_donnees.py:323:98"))   # spécialités du véhicule, séparées par , ; ou |


def tables_commandes(entreprise, exclure_id=None) -> list[dict]:
    """Tables candidates pour les commandes : au moins une colonne de position, autre que la table des véhicules."""
    from .models import TableDynamique
    from .vue_groupe import ids_du_groupe, nom_vue, tables_generales
    return [{"id": t.id, "nom": nom_vue(t)} for t in tables_generales(TableDynamique.objects.filter(entreprise=entreprise, champs__type_champ="geolocalisation")
            .exclude(id__in=ids_du_groupe(exclure_id)).distinct().order_by("nom"))[:50]]


def vehicules_table(table, slugs=None, limite: int = 300) -> list[dict]:
    """Un véhicule par colonne de position : dernière position connue et vitesse médiane observée (m/s)."""
    mat, _e = matrice_table(table, slugs, limite, nommer_par_table=False, par_client=False)   # la flotte est réglée par nom de colonne : on ne le change pas
    if mat is None:
        return []
    sortie = []
    for j, (vid, nom) in enumerate(zip(mat["ids"], mat["noms"])):
        ok = ~(np.isnan(mat["lat"][:, j]) | np.isnan(mat["lon"][:, j]))
        st = _g._stats_objet(mat["t"], mat["lat"][:, j], mat["lon"][:, j], _g.PARAMETRES) if ok.sum() >= 2 else None
        dernier = np.flatnonzero(ok)
        sortie.append({"id": vid, "nom": nom, "lat": float(mat["lat"][dernier[-1], j]) if len(dernier) else None,
                       "lon": float(mat["lon"][dernier[-1], j]) if len(dernier) else None,
                       "vitesse_ms": (st or {}).get("vitesse_med_ms") or None})
    return sortie


def _nombre_ligne(ligne, col, defaut, facteur: float = 1.0):
    """Valeur numérique de la colonne `col` dans la ligne (× facteur), `defaut` si la colonne manque ou n'est pas un nombre positif."""
    if col is None or ligne.get(col.slug) in (None, ""):
        return defaut
    try:
        v = float(str(ligne.get(col.slug)).replace(",", "."))
    except ValueError:
        return defaut
    return v * facteur if v >= 0 else defaut


def commandes_table(table, colonne_position=None, colonne_demande=None, limite: int = _l.LIMITES["commandes"]) -> list[dict]:
    """Lignes de `table` -> commandes. Position : première colonne de position (ou celle choisie) ; quantité : colonne choisie, sinon
    première colonne numérique dont le nom évoque une quantité, sinon 1 ; échéance : première colonne date dont le nom évoque
    une limite de livraison (secondes à partir de maintenant)."""
    from .visualization import _parse_geo
    champs = list(table.champs.order_by("ordre", "id"))
    pos = next((c for c in champs if c.type_champ == "geolocalisation" and (not colonne_position or c.slug == colonne_position)), None)
    if pos is None:
        return []
    nums = [c for c in champs if c.type_champ in ("nombre", "decimal", "entier", "monnaie", "pourcentage", "constante")]
    dem = next((c for c in nums if c.slug == colonne_demande), None) or next((c for c in nums if any(m in c.slug.lower() for m in MOTS_DEMANDE)), None)
    dates = [c for c in champs if c.type_champ in ("datetime", "horodatage", "date")]
    deb = next((c for c in dates if any(m in c.slug.lower() for m in MOTS_DEBUT)), None)
    ech = next((c for c in dates if c is not deb and any(m in c.slug.lower() for m in MOTS_ECHEANCE)), None)
    pri = next((c for c in nums if c is not dem and any(m in c.slug.lower() for m in MOTS_PRIORITE)), None)
    srv = next((c for c in nums if c is not dem and c is not pri and any(m in c.slug.lower() for m in MOTS_SERVICE)), None)
    exi = next((c for c in champs if c.type_champ == "texte" and c.slug != pos.slug and any(m in c.slug.lower() for m in MOTS_EXIGE)), None)
    nom = next((c for c in champs if c.type_champ in ("texte", "email", "url") and c.slug != pos.slug and c is not exi), None)
    maintenant = timezone.now().timestamp()
    sortie = []
    from . import parquet_store
    for ligne in parquet_store.lignes_analytiques(table, limite):
        g = _parse_geo(ligne.get(pos.slug))
        if not g:
            continue
        try:
            q = float(str(ligne.get(dem.slug)).replace(",", ".")) if dem and ligne.get(dem.slug) not in (None, "") else 1.0
        except ValueError:
            q = 1.0
        e = _g._temps(ligne.get(ech.slug)) if ech and ligne.get(ech.slug) not in (None, "") else None
        b = _g._temps(ligne.get(deb.slug)) if deb and ligne.get(deb.slug) not in (None, "") else None
        sortie.append({"id": ligne["_id"], "nom": str(ligne.get(nom.slug) or g[2] or f"Commande {ligne['_id']}") if nom else (g[2] or f"Commande {ligne['_id']}"),
                       "lat": g[0], "lon": g[1], "demande": max(q, 0.0), "echeance_s": max(e - maintenant, 0.0) if e is not None else None,
                       "debut_s": max(b - maintenant, 0.0) if b is not None else None, "priorite": _nombre_ligne(ligne, pri, 1.0),
                       "service_s": _nombre_ligne(ligne, srv, None, 60.0),
                       "exige": [x.strip().lower() for x in re.split(r"[,;|]", str(ligne.get(exi.slug) or "")) if x.strip()] if exi else []})
    return sortie


def appliquer_flotte(vehicules: list, flotte) -> list:
    """Attributs de flotte choisis par l'utilisateur, par véhicule (clé = id de la colonne ou nom) ou pour tous (clé « * ») :
    {mode, capacite, tags [..], autonomie_km, vitesse_kmh, disponible_min, depot [lat, lon]}. Les valeurs invalides sont ignorées."""
    if not isinstance(flotte, dict):
        return vehicules
    sortie = []
    for v in vehicules:
        spec = {**(flotte.get("*") or {}), **(flotte.get(v["id"]) or flotte.get(v["nom"]) or {})}
        w = dict(v)
        if spec.get("mode") in _l.MODES:
            w["mode"] = spec["mode"]
        for src, dst, f in (("capacite", "capacite", 1.0), ("autonomie_km", "autonomie_m", 1000.0), ("vitesse_kmh", "vitesse_ms", 1 / 3.6), ("disponible_min", "disponible_s", 60.0)):
            try:
                x = float(spec[src]) * f if spec.get(src) not in (None, "") else None
            except (TypeError, ValueError):
                x = None
            if x is not None and x >= 0 and (x > 0 or dst == "disponible_s"):
                w[dst] = x
        if isinstance(spec.get("tags"), list):
            w["tags"] = [str(t).strip().lower() for t in spec["tags"] if str(t).strip()][:20]
        try:
            d = spec.get("depot")
            if d and -90 <= float(d[0]) <= 90 and -180 <= float(d[1]) <= 180:
                w["depot"] = (float(d[0]), float(d[1]))
        except (TypeError, ValueError, IndexError):
            pass
        sortie.append(w)
    return sortie


def planifier_table(table, table_commandes, depot=None, slugs=None, colonne_demande=None, flotte=None, **params) -> dict:
    vehicules = appliquer_flotte(vehicules_table(table, slugs), flotte)
    commandes = commandes_table(table_commandes, colonne_demande=colonne_demande)
    return _l.planifier(commandes, [v for v in vehicules if v["lat"] is not None or not params.get("depuis_position", True)], depot, **params)


def planifier_scenario(depot=None, strategique=False, **params) -> dict:
    """Scénario simulé (jamais enregistré). `strategique` : théâtre étendu, flotte mixte, zone interdite (charge du moteur)."""
    if strategique:
        commandes, vehicules, centre, zones = _l.scenario_strategique()
        params.setdefault("zones", zones)
    else:
        commandes, vehicules, centre = _l.scenario()
    return _l.planifier(commandes, vehicules, depot or centre, **params)


def enregistrer_plan(entreprise, utilisateur, table, table_cmd, plan: dict) -> int | None:
    """Range la tournée planifiée comme une ligne de « Plans logistiques ». None si le même plan vient d'être enregistré (moins de 10 min)."""
    if plan.get("vide"):
        return None
    r = plan["resume"]
    signature = hashlib.sha1(json.dumps([entreprise.id, table.id, table_cmd.id, r["distance_m"], r["commandes"], plan["parametres"]], sort_keys=True).encode()).hexdigest()
    if not cache.add(f"log_enreg:{signature}", 1, 600):
        return None
    from . import memoire_decision as _md
    enr = _md._ajouter_ligne(entreprise, utilisateur, "log", {
        "titre": f"{table_cmd.nom} ({timezone.now():%Y-%m-%d %H:%M})", "table_source": table.nom, "table_commandes": table_cmd.nom,
        "nb_commandes": r["commandes"], "nb_vehicules": r["vehicules_utilises"], "distance_totale_m": r["distance_m"], "duree_max_s": r["duree_max_s"],
        "retard_total_s": r["retard_total_s"], "nb_retards": r["retards"], "nb_non_affectees": r["non_affectees"], "utilisation": r["utilisation"],
        "gain_pct": r["gain"], "gain_depot": r["depot_gain"], "parametres": plan["parametres"],
        "resume": {"resume": r, "alertes": [{k: a[k] for k in ("type", "gravite", "valeur") if k in a} for a in plan["alertes"]],
                   "routes": [{"vehicule": t["vehicule"], "commandes": [e["id"] for e in t["etapes"]], "distance_m": t["distance_m"]} for t in plan["routes"]]},
        "date": timezone.now().isoformat()})
    return enr.id


def dernier_plan(entreprise) -> dict | None:
    """Dernier plan enregistré (ligne de « Plans logistiques ») pour l'état fusionné : {resume, alertes, ts, table_commandes, ...} ou None."""
    from . import memoire_decision as _md
    lignes = _md._lignes(entreprise, "log")
    if not lignes:
        return None
    d = lignes[-1]
    contenu = _md._json(d.get("resume"), {})
    return {"ts": d["_cree_le"].timestamp(), "titre": d.get("titre"), "table_commandes": d.get("table_commandes"), "table_source": d.get("table_source"),
            **(contenu.get("resume") or {}), "alertes": contenu.get("alertes") or []}
