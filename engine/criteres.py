"""CRITÈRES : mesure CONTINUE des 33 critères de recommandation, sur chaque colonne, table, KPI... qu'ils concernent.

Les 33 règles de engine/recommandations.py ne parlent que lorsqu'un seuil est franchi. Ici, chaque critère (même code, même
ordre figé : `CODES`) rend, pour chaque objet auquel il s'applique, une INTENSITÉ v dans [0, 1] :
    0 = rien à signaler, 1 = le critère est pleinement atteint ; le seuil de la règle n'est qu'un point sur cette échelle.
Une règle silencieuse a donc mesuré quelque chose (« 0,3 : loin du seuil »), et ce nombre nourrit la décision et le réseau.

Un critère décrit un problème, sauf `declaration_options.OPPORTUNITES` (activité en hausse, KPI qui progresse) : pour ceux-là,
v mesure l'ampleur de l'opportunité. Rien ici ne remplace les règles : mêmes seuils (recommandations.SEUILS), mêmes états.

  mesurer(etat) -> {"mesures": {code: [{cible, table_id, v}]}, "erreurs": [...]}
      cible : « table:1 », « champ:7 », « kpi:3 », « modele_ia:2 », « tache:5 » ou « plateforme » (table_id None).
  Index(etat).vecteur(cibles, tables) -> 33 nombres (max des intensités sur le périmètre + la plateforme) : entrée du réseau.

ÉCHELLES : les constantes de mesure qui n'ont PAS de seuil de règle (`ECHELLES_DEFAUT`) sont explicites. `calibrer(etat)` estime,
depuis les données de l'entreprise, celles qui dépendent d'une distribution (taux « normal » de valeurs aberrantes, taille de
référence d'une table), bornées à un facteur de l'échelle par défaut pour qu'elles ne dérivent jamais. `diagnostic(etat)` dit,
critère par critère, si la mesure est vivante, morte (toujours 0) ou saturée (toujours ~1) sur les données réelles.

Module PUR (aucun import Django) : testable avec des états fabriqués à la main.
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t as _i18n_t
from engine.i18n import tf

import logging
import statistics

from . import recommandations as _rec

logger = logging.getLogger(__name__)

# Ordre FIGÉ : la position d'un critère est celle de sa colonne dans le vecteur du réseau (ne jamais réordonner, ajouter à la fin).
CODES = (
    "manque_table_vide", "manque_colonne_vide", "manque_colonne_peu_remplie", "manque_relation_non_renseignee",
    "manque_kpi_jamais_calcule", "manque_kpi_sans_reference", "couverture_table_sans_suivi", "couverture_modele_inutilise",
    "surplus_volume", "surplus_colonne_constante", "surplus_notifications_non_lues", "surplus_kpi_doublon",
    "tendance_hausse_activite", "diminution_activite", "diminution_blockchain", "tendance_kpi_defavorable",
    "tendance_kpi_favorable", "concentration_valeur", "concentration_createur", "concentration_relations",
    "concentration_volume", "anomalie_rafale_creation", "anomalie_valeurs_aberrantes", "obsolescence_table",
    "obsolescence_kpi", "isolement_table", "objectif_kpi_hors_cible", "integrite_chaine_invalide",
    "integrite_blockchain_inactive", "dysfonctionnement_taches", "dysfonctionnement_tache_jamais_lancee",
    "dysfonctionnement_kpi_echec", "dysfonctionnement_modele_ia")
NB = len(CODES)
PLATEFORME = "plateforme"
# Constantes de mesure sans seuil de règle : (défaut, plancher, plafond de calibrage).
ECHELLES_DEFAUT = {
    "aberrants_taux": 0.01,       # part de valeurs aberrantes jugée normale : à ce taux, l'intensité vaut 1 (d'où 1 % -> 1)
    "suivi_lignes": 20,           # taille d'une table sans suivi pour laquelle l'intensité vaut 1 (2 x min_lignes)
    "exposant_vide": 8,           # (1 - remplissage) ** e : plus e est grand, plus la colonne doit être vide pour approcher 1
    "exposant_concentration": 8,  # part_dominante ** e : idem pour une colonne qui ne prend presque qu'une seule valeur
    "exposant_isolement": 2,      # 1 / (1 + degré) ** e : vitesse à laquelle chaque lien retire de l'isolement d'une table
    "plafond_objectif": 0.4,      # intensité maximale due au seul écart relatif à l'objectif d'un KPI encore sous l'alerte
}
# Bornes (bas, haut) acceptées pour chaque échelle : les deux premières servent aussi de garde-fou au calibrage.
_BORNES_ECHELLES = {"aberrants_taux": (0.005, 0.05), "suivi_lignes": (20, 5000), "exposant_vide": (1, 16),
                    "exposant_concentration": (1, 16), "exposant_isolement": (1, 4),
                    "plafond_objectif": (0.05, 0.6)}   # 0,6 = intensité d'un KPI en alerte : l'écart seul ne doit jamais la dépasser
_MESURES: dict = {}


def _mesure(code):
    def _enregistrer(fonction):
        _MESURES[code] = fonction
        return fonction
    return _enregistrer


def _clip(x: float) -> float:
    return 0.0 if x <= 0 else 1.0 if x >= 1 else float(x)


def _t(t) -> str:
    return f"table:{t['id']}"


def _c(c) -> str:
    return f"champ:{c['id']}"


def _k(k) -> str:
    return f"kpi:{k['id']}"


def _age(ts, maintenant):
    return _rec._jours(ts, maintenant)


# ═══════════════════════════ MANQUE ═══════════════════════════
@_mesure("manque_table_vide")
def _m_table_vide(etat, s):
    for t in etat.get("tables", []):
        yield _t(t), t["id"], _clip(1 - t["nb_lignes"] / s["min_lignes"])


@_mesure("manque_colonne_vide")
def _m_colonne_vide(etat, s):
    for t in etat.get("tables", []):
        if t["nb_lignes"] >= s["min_lignes"]:
            for c in t["champs"]:
                yield _c(c), t["id"], _clip((1 - c["remplissage"]) ** s["exposant_vide"])   # ~1 seulement quand la colonne est (presque) vide


@_mesure("manque_colonne_peu_remplie")
def _m_peu_remplie(etat, s):
    for t in etat.get("tables", []):
        if t["nb_lignes"] >= s["min_lignes"]:
            for c in t["champs"]:
                if c["type"] != "relation":
                    yield _c(c), t["id"], _clip((s["remplissage_min"] - c["remplissage"]) / s["remplissage_min"])


@_mesure("manque_relation_non_renseignee")
def _m_relation(etat, s):
    for t in etat.get("tables", []):
        if t["nb_lignes"] >= s["min_lignes"]:
            for c in t["champs"]:
                if c["type"] == "relation":
                    yield _c(c), t["id"], _clip((s["relation_remplissage_min"] - c["remplissage"]) / s["relation_remplissage_min"])


@_mesure("manque_kpi_jamais_calcule")
def _m_kpi_jamais(etat, s):
    for k in etat.get("kpis", []):
        yield _k(k), k.get("table_id"), 1.0 if (k["statut"] == "en_attente" and k["valeur"] is None) else 0.0


@_mesure("manque_kpi_sans_reference")
def _m_kpi_sans_ref(etat, s):
    for k in etat.get("kpis", []):
        yield _k(k), k.get("table_id"), 0.5 * (k["cible"] is None) + 0.5 * (k["seuil_alerte"] is None)


# ═══════════════════════════ COUVERTURE ═══════════════════════════
@_mesure("couverture_table_sans_suivi")
def _m_sans_suivi(etat, s):
    for t in etat.get("tables", []):
        suivie = bool(t["nb_kpis"] or t["nb_tableaux_bord"] or t["clients"])
        yield _t(t), t["id"], 0.0 if suivie else _clip(t["nb_lignes"] / s["suivi_lignes"])


@_mesure("couverture_modele_inutilise")
def _m_modele_inutilise(etat, s):
    for m in etat.get("modeles_ia", []):
        yield f"modele_ia:{m['id']}", None, 1.0 if (m["statut"] == "entraine" and m["inferences"] == 0) else 0.0


# ═══════════════════════════ SURPLUS ═══════════════════════════
@_mesure("surplus_volume")
def _m_surplus_volume(etat, s):
    for t in etat.get("tables", []):
        autres = [x["nb_lignes"] for x in etat.get("tables", []) if x["id"] != t["id"]] or [0]
        mediane = statistics.median(autres)
        r = t["nb_lignes"] / s["surplus_lignes"]
        if t["nb_lignes"] > 1000 and mediane:
            r = max(r, t["nb_lignes"] / (s["surplus_ratio_mediane"] * mediane))
        yield _t(t), t["id"], _clip(r)


@_mesure("surplus_colonne_constante")
def _m_constante(etat, s):
    for t in etat.get("tables", []):
        if t["nb_lignes"] >= 2 * s["min_lignes"]:
            for c in t["champs"]:
                exclu = c["type"] in ("booleen", "switch")
                yield _c(c), t["id"], 0.0 if exclu else _clip(c["top_part"] ** s["exposant_concentration"] * c["remplissage"])


@_mesure("surplus_notifications_non_lues")
def _m_notifications(etat, s):
    yield PLATEFORME, None, _clip(((etat.get("notifications") or {}).get("non_lues", 0)) / s["notifications_non_lues"])


@_mesure("surplus_kpi_doublon")
def _m_doublon(etat, s):
    vus = {}
    for k in etat.get("kpis", []):
        if k["champ_cible_id"] and k["formule_id"]:
            vus.setdefault((k["champ_cible_id"], k["formule_id"]), []).append(k)
    en_double = {k["id"] for lot in vus.values() if len(lot) > 1 for k in lot}
    for k in etat.get("kpis", []):
        yield _k(k), k.get("table_id"), 1.0 if k["id"] in en_double else 0.0


# ═══════════════════════════ TENDANCE / DIMINUTION ═══════════════════════════
@_mesure("tendance_hausse_activite")   # opportunité
def _m_hausse(etat, s):
    for t in etat.get("tables", []):
        ok = t["n7_prec"] >= 1 and t["n7"] >= s["activite_min"]
        yield _t(t), t["id"], _clip((t["n7"] / t["n7_prec"] - 1) / (s["hausse_ratio"] - 1)) if ok else 0.0


@_mesure("diminution_activite")
def _m_diminution(etat, s):
    for t in etat.get("tables", []):
        ok = t["n7_prec"] >= s["activite_min"]
        yield _t(t), t["id"], _clip((1 - t["n7"] / t["n7_prec"]) / (1 - s["baisse_ratio"])) if ok else 0.0


@_mesure("diminution_blockchain")
def _m_dim_blockchain(etat, s):
    b = etat.get("blockchain") or {}
    prec = b.get("blocs_7j_prec", 0)
    yield PLATEFORME, None, _clip((1 - b.get("blocs_7j", 0) / prec) / (1 - s["baisse_ratio"])) if prec >= s["activite_min"] else 0.0


def _pente_kpi(k, s):
    h = k["historique"]
    return _rec._pente_relative(h) if len(h) >= s["kpi_historique_min"] else None


@_mesure("tendance_kpi_defavorable")
def _m_kpi_def(etat, s):
    for k in etat.get("kpis", []):
        p = _pente_kpi(k, s)
        v = 0.0
        if p is not None and k["sens"] != "cible" and (p < 0) == (k["sens"] == "hausse"):
            v = _clip(abs(p) / (2 * s["kpi_pente_min"]))
        yield _k(k), k.get("table_id"), v


@_mesure("tendance_kpi_favorable")   # opportunité
def _m_kpi_fav(etat, s):
    for k in etat.get("kpis", []):
        p = _pente_kpi(k, s)
        v = 0.0
        if p is not None and k["sens"] != "cible" and (p < 0) != (k["sens"] == "hausse"):
            v = _clip(abs(p) / (2 * s["kpi_pente_min"]))
        yield _k(k), k.get("table_id"), v


# ═══════════════════════════ CONCENTRATION ═══════════════════════════
@_mesure("concentration_valeur")
def _m_conc_valeur(etat, s):
    for t in etat.get("tables", []):
        if t["nb_lignes"] >= 2 * s["min_lignes"]:
            for c in t["champs"]:
                yield _c(c), t["id"], _clip((c["top_part"] - 0.5) / 0.5) if c["distincts"] >= 2 else 0.0


@_mesure("concentration_createur")
def _m_conc_createur(etat, s):
    if sum((etat.get("membres") or {}).get(r, 0) for r in ("admin", "agent")) < 2:
        return
    for t in etat.get("tables", []):
        total = sum(t["createurs"].values())
        if total >= 3 * s["min_lignes"]:
            yield _t(t), t["id"], _clip((max(t["createurs"].values()) / total - 0.5) / 0.5)


@_mesure("concentration_relations")
def _m_conc_relations(etat, s):
    r = etat.get("relations") or {}
    total, par_table = r.get("total_liens", 0), {}
    for h in r.get("hubs", [])[:3]:
        if total:
            v = _clip(h["degre"] / total / s["concentration_hub"]) * _clip(h["degre"] / s["hub_degre_min"])
            par_table[h["table_id"]] = max(par_table.get(h["table_id"], 0.0), v)
    for tid, v in par_table.items():
        yield f"table:{tid}", tid, v


@_mesure("concentration_volume")
def _m_conc_volume(etat, s):
    total = sum(t["nb_lignes"] for t in etat.get("tables", []))
    if len(etat.get("tables", [])) >= 3 and total >= 100:
        for t in etat.get("tables", []):
            yield _t(t), t["id"], _clip((t["nb_lignes"] / total - 0.5) / 0.5)


# ═══════════════════════════ ANOMALIE ═══════════════════════════
@_mesure("anomalie_rafale_creation")
def _m_rafale(etat, s):
    for t in etat.get("tables", []):
        non_nuls = sorted(t["jours"].values())
        if len(non_nuls) >= 4:
            mediane, pic = statistics.median(non_nuls), non_nuls[-1]
            yield _t(t), t["id"], (_clip(pic / mediane / s["rafale_ratio"]) * _clip(pic / s["rafale_min"]) if mediane else 0.0)


@_mesure("anomalie_valeurs_aberrantes")
def _m_aberrantes(etat, s):
    for t in etat.get("tables", []):
        for c in t["champs"]:
            n = c["num"]
            if n:
                yield _c(c), t["id"], _clip(n["aberrants"] / max(1.0, s["aberrants_taux"] * t["nb_lignes"] * c["remplissage"]))


# ═══════════════════════════ OBSOLESCENCE / ISOLEMENT ═══════════════════════════
@_mesure("obsolescence_table")
def _m_obsolete_table(etat, s):
    for t in etat.get("tables", []):
        age = _age(t["derniere_ts"], etat.get("ts"))
        if t["nb_lignes"] >= s["min_lignes"] and age is not None:
            yield _t(t), t["id"], _clip(age / s["obsolete_jours"])


@_mesure("obsolescence_kpi")
def _m_obsolete_kpi(etat, s):
    for k in etat.get("kpis", []):
        age = _age(k["calcule_ts"], etat.get("ts"))
        yield _k(k), k.get("table_id"), _clip(age / s["kpi_obsolete_jours"]) if (k["statut"] == "pret" and age is not None) else 0.0


@_mesure("isolement_table")
def _m_isolement(etat, s):
    if len(etat.get("tables", [])) < 3:
        return
    degres = (etat.get("relations") or {}).get("degres_tables", {})
    for t in etat.get("tables", []):
        if t["nb_lignes"] >= s["min_lignes"]:
            deg = degres.get(t["id"]) or degres.get(str(t["id"])) or 0
            yield _t(t), t["id"], 0.0 if t["clients"] else 1 / (1 + deg) ** s["exposant_isolement"]


# ═══════════════════════════ OBJECTIF ═══════════════════════════
@_mesure("objectif_kpi_hors_cible")
def _m_objectif(etat, s):
    for k in etat.get("kpis", []):
        v = 1.0 if k["etat"] == "danger" else 0.6 if k["etat"] == "alerte" else 0.0
        val, cible = k.get("valeur"), k.get("cible")
        if isinstance(val, (int, float)) and isinstance(cible, (int, float)) and cible:
            ecart = (cible - val) if k["sens"] == "hausse" else (val - cible) if k["sens"] == "baisse" else abs(val - cible)
            v = max(v, s["plafond_objectif"] * _clip(ecart / abs(cible)))   # sous l'alerte : l'écart relatif à l'objectif compte jusqu'au plafond réglé (0,4 par défaut)
        yield _k(k), k.get("table_id"), v


# ═══════════════════════════ INTÉGRITÉ / DYSFONCTIONNEMENT ═══════════════════════════
@_mesure("integrite_chaine_invalide")
def _m_chaine(etat, s):
    yield PLATEFORME, None, 1.0 if (etat.get("blockchain") or {}).get("chaine_valide") is False else 0.0


@_mesure("integrite_blockchain_inactive")
def _m_bc_inactive(etat, s):
    b = etat.get("blockchain") or {}
    if b:
        activite = sum(t["n7"] for t in etat.get("tables", []))
        yield PLATEFORME, None, _clip(1 - b.get("blocs_7j", 0) / max(activite, 1)) * _clip(activite / s["activite_min"])


@_mesure("dysfonctionnement_taches")
def _m_taches(etat, s):
    for t in etat.get("taches", []):
        yield f"tache:{t['id']}", None, 1.0 if str(t["statut"]).startswith("echec") else 0.0


@_mesure("dysfonctionnement_tache_jamais_lancee")
def _m_jamais_lancee(etat, s):
    for t in etat.get("taches", []):
        age = _age(t["cree_ts"], etat.get("ts"))
        ok = t["mode"] == "planifie" and t["actif"] and not t["derniere_ts"] and age is not None
        yield f"tache:{t['id']}", None, _clip(age / s["tache_jamais_lancee_jours"]) if ok else 0.0


@_mesure("dysfonctionnement_kpi_echec")
def _m_kpi_echec(etat, s):
    for k in etat.get("kpis", []):
        yield _k(k), k.get("table_id"), 1.0 if k["statut"] == "echec" else 0.0


@_mesure("dysfonctionnement_modele_ia")
def _m_modele_erreur(etat, s):
    for m in etat.get("modeles_ia", []):
        yield f"modele_ia:{m['id']}", None, 1.0 if m["statut"] == "erreur" else 0.0


# ─────────────────────────────────────────────────────────────────────────────
def _borner(nom: str, valeur: float) -> float:
    bas, haut = _BORNES_ECHELLES[nom]
    return min(haut, max(bas, valeur))


def normaliser_echelles(brut) -> tuple[dict, list[str]]:
    """Réglages saisis -> ({echelle: valeur} des seules échelles connues et valides, erreurs). Une valeur hors bornes ou non numérique
    est REFUSÉE (jamais corrigée en silence) ; une clé inconnue est ignorée ; une valeur vide veut dire « laisser le calibrage »."""
    propre, erreurs = {}, []
    for nom, (bas, haut) in _BORNES_ECHELLES.items():
        v = (brut or {}).get(nom) if isinstance(brut, dict) else None
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not bas <= v <= haut:
            erreurs.append(tf("engine/criteres.py:g:382:28", v1=nom, v2=bas, v3=haut))
            continue
        propre[nom] = int(v) if isinstance(ECHELLES_DEFAUT[nom], int) and float(v).is_integer() else float(v)
    return propre, erreurs


def calibrer(etat: dict, seuils: dict | None = None, reglages: dict | None = None) -> dict:
    """Échelles estimées sur les données de l'entreprise (médianes, donc robustes), bornées ; repli sur le défaut faute d'assez d'objets.
      * aberrants_taux : 3 x la médiane, sur les colonnes numériques, de la part de valeurs aberrantes (5 colonnes au moins) ;
      * suivi_lignes   : 3 x la médiane du nombre de lignes des tables assez remplies pour être jugées (3 tables au moins).
    L'intensité 1 se place donc à 3 fois le niveau TYPIQUE de l'entreprise : la colonne ou la table médiane vaut environ 0,33, jamais 1."""
    s = {**_rec.SEUILS, **(seuils or {})}
    e = dict(ECHELLES_DEFAUT)
    fixes = normaliser_echelles(reglages)[0]   # réglages de l'entreprise : une valeur fixée remplace l'estimation (les autres restent calibrées)
    taux, tailles = [], []
    for t in etat.get("tables", []):
        if t.get("nb_lignes", 0) >= s["min_lignes"]:
            tailles.append(t["nb_lignes"])
            for c in t.get("champs", []):
                n = c.get("num")
                if n and c.get("remplissage", 0) > 0:
                    taux.append(n["aberrants"] / (t["nb_lignes"] * c["remplissage"]))
    if len(taux) >= 5 and "aberrants_taux" not in fixes:
        e["aberrants_taux"] = _borner("aberrants_taux", 3 * statistics.median(taux))
    if len(tailles) >= 3 and "suivi_lignes" not in fixes:
        e["suivi_lignes"] = _borner("suivi_lignes", 3 * statistics.median(tailles))
    e.update(fixes)
    return e


def mesurer(etat: dict, seuils: dict | None = None, codes=None, echelles: dict | None = None) -> dict:
    """Intensité de chacun des 33 critères (ou seulement `codes`) sur chaque objet concerné. Un critère défaillant n'empêche pas les autres.
    `echelles` : constantes de mesure (défaut : `ECHELLES_DEFAUT`)."""
    s = {**_rec.SEUILS, **ECHELLES_DEFAUT, **(echelles or {}), **(seuils or {})}
    mesures, erreurs, voulus = {}, [], set(codes) if codes is not None else None
    for code in CODES:
        if voulus is not None and code not in voulus:
            mesures[code] = []
            continue
        try:
            mesures[code] = [{"cible": c, "table_id": tid, "v": round(float(v), 4)} for c, tid, v in _MESURES[code](etat, s)]
        except Exception as exc:  # noqa: BLE001 — un état incomplet ne doit pas bloquer la décision
            logger.warning(_i18n_t("engine/criteres.py:g:425:28"), code, type(exc).__name__)
            mesures[code] = []
            erreurs.append(f"{code} : {type(exc).__name__}")
    return {"mesures": mesures, "erreurs": erreurs}


def diagnostic(etat: dict, seuils: dict | None = None, echelles: dict | None = None, reglages: dict | None = None) -> dict:
    """Santé de chaque mesure sur les données réelles : {code: {objets, non_nuls, mediane, max, etat}}.
    etat : « sans objet » (rien à mesurer ici), « morte » (toujours 0), « saturee » (médiane des non nuls >= 0,95), « vivante »."""
    mesures = mesurer(etat, seuils, echelles=echelles if echelles is not None else calibrer(etat, seuils, reglages))["mesures"]
    sortie = {}
    for code in CODES:
        v = [r["v"] for r in mesures[code]]
        actifs = [x for x in v if x > 0]
        med = statistics.median(actifs) if actifs else 0.0
        sortie[code] = {"objets": len(v), "non_nuls": len(actifs), "mediane": round(med, 3), "max": round(max(v, default=0.0), 3),
                        "etat": td("engine/criteres.py:g:442:33") if not v else "morte" if not actifs else "saturee" if med >= 0.95 else "vivante"}
    return sortie


class Index:
    """Mesures des 33 critères indexées une fois, puis résumées en vecteur de 33 nombres pour un périmètre (table, colonne, KPI...).
    `echelles` : None = calibrées sur l'état (`calibrer`) ; un dict = échelles imposées (états reconstruits du passé : défaut, pas de calibrage)."""

    def __init__(self, etat: dict, noeuds: dict | None = None, seuils: dict | None = None, codes=None, echelles: dict | None = None,
                 reglages: dict | None = None):
        self.echelles = echelles if echelles is not None else calibrer(etat, seuils, reglages)
        self.mesures = mesurer(etat, seuils, codes, self.echelles)["mesures"]
        self.noeuds = noeuds or {}

    def portee(self, cibles=None, tables=None) -> tuple[set, set]:
        """(clés exactes, tables entières) visées : une table entière englobe ses colonnes et KPI ; une colonne ou un KPI
        vise sa propre mesure et celle de sa table (« table:<id> ») ; un autre objet (modèle IA...) vise sa propre mesure."""
        exactes, tables_entieres = set(), {i for i in (tables or ()) if i is not None}
        for nid in cibles or ():
            n = self.noeuds.get(nid) or {}
            if n.get("type") == "table":
                tables_entieres.add(n.get("id"))
            else:
                exactes.add(nid)
                if n.get("table_id") is not None:
                    exactes.add(f"table:{n['table_id']}")
        return exactes, tables_entieres

    def vecteur(self, cibles=None, tables=None) -> list[float]:
        exactes, entieres = self.portee(cibles, tables)
        out = []
        for code in CODES:
            rows = self.mesures[code]
            out.append(max((r["v"] for r in rows if r["cible"] == PLATEFORME or r["cible"] in exactes
                            or (r["table_id"] is not None and r["table_id"] in entieres)), default=0.0))
        return out

    def detail(self, cibles=None, tables=None, minimum: float = 0.01) -> dict:
        """{code: intensité} des critères non nuls sur le périmètre (affichage et traçabilité)."""
        return {c: round(v, 3) for c, v in zip(CODES, self.vecteur(cibles, tables)) if v >= minimum}
