"""RECOMMANDATIONS fondées sur l'état fusionné (moteur de recommandation, volet 2/2).

Ensemble de règles PRÉÉTABLIES, pures (aucun accès base, aucune source externe) :
chaque règle lit uniquement le dictionnaire produit par engine.fusion.fusionner_etat
et rend des recommandations. On peut donc les tester avec un état fabriqué à la main.

Catégories : manque, surplus, tendance, diminution, concentration, anomalie,
obsolescence, isolement, objectif, integrite, dysfonctionnement, couverture.
Gravités : critique, attention, info. Tous les seuils sont dans SEUILS.

Ajouter une règle = une fonction décorée par @regle(code, categorie) qui yield
des _reco(...). Rien d'autre à brancher (l'API et la page listent le registre).
"""
from engine.i18n import t as _i18n_t
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf
import logging
import statistics
from collections import defaultdict
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)

SEUILS = {
    "min_lignes": 10,              # en dessous, une table est trop petite pour juger ses colonnes
    "remplissage_min": 0.5,        # colonne « peu remplie » sous ce ratio
    "remplissage_critique": 0.2,
    "relation_remplissage_min": 0.3,
    "surplus_lignes": 100000,      # table volumineuse : archivage à envisager
    "surplus_ratio_mediane": 10,   # ... ou 10x la médiane des autres tables (si > 1000 lignes)
    "notifications_non_lues": 50,
    "hausse_ratio": 1.5, "baisse_ratio": 0.5, "activite_min": 5,   # lignes/semaine minimum pour juger
    "rafale_ratio": 4, "rafale_min": 20,
    "concentration_valeur": 0.8, "concentration_createur": 0.9, "concentration_hub": 0.3,
    "concentration_volume": 0.8, "hub_degre_min": 10,
    "obsolete_jours": 30, "kpi_obsolete_jours": 14,
    "kpi_pente_min": 0.02,         # pente relative par point d'historique jugée significative
    "kpi_historique_min": 4,
    "aberrants_ratio_min": 0.0,    # au moins un point aberrant
    "tache_jamais_lancee_jours": 2,
    "suivi_delai_s": 900,          # avant ce délai, l'effet d'une action lancée n'est pas jugé
}
_BASE = {"critique": 80, "attention": 50, "info": 20}
GRAVITES = ("critique", "attention", "info")
CATEGORIES = ("manque", "surplus", "tendance", "diminution", "concentration", "anomalie",
              "obsolescence", "isolement", "objectif", "integrite", "dysfonctionnement", "couverture")
_REGLES = []
# Règles GÉOSPATIALES : registre à part (les 33 règles ci-dessus sont en correspondance 1-1 avec les 33 critères du réseau, dont la
# taille ne doit pas bouger). Elles réutilisent les catégories existantes, donc les leviers de la décision, et se lisent dans etat["geo"].
_REGLES_GEO = []
SEUILS_GEO = {"proba_attention": 0.2, "proba_critique": 0.6, "concentration": 0.6, "objets_min_concentration": 4}
# Règles d'ARCHITECTURE : même principe que le registre géospatial (le réseau garde ses 33 critères). Elles couvrent le reste de ce qui
# existe sur la plateforme — documents, skills, analyses, imports, connexions externes, clés API — et se lisent dans etat["architecture"].
_REGLES_ARCHI = []
SEUILS_ARCHI = {"cle_api_inutilisee_jours": 30, "synchro_obsolete_jours": 30}


def regle_archi(code, categorie):
    def _enregistrer(fonction):
        _REGLES_ARCHI.append((code, categorie, fonction))
        return fonction
    return _enregistrer


def regle_geo(code, categorie):
    def _enregistrer(fonction):
        _REGLES_GEO.append((code, categorie, fonction))
        return fonction
    return _enregistrer


def regle(code, categorie):
    def _enregistrer(fonction):
        _REGLES.append((code, categorie, fonction))
        return fonction
    return _enregistrer


def _reco(code, categorie, gravite, titre, message, cible=None, preuves=None, action="", bonus=0, lancable=False):
    """`lancable=True` = ACTION (lance une tâche existante sur `cible`) ; sinon DÉCISION (rien n'est lancé, l'humain décide)."""
    return {"code": code, "categorie": categorie, "gravite": gravite, "score": _BASE[gravite] + bonus,
            "type": "action" if lancable else "decision",
            "titre": titre, "message": message, "cible": cible or {}, "preuves": preuves or {}, "action": action}


def _cible_table(t):
    return {"type": "table", "id": t["id"], "nom": t["nom"]}


def _cible_champ(t, c):
    return {"type": "champ", "id": c["id"], "nom": f'{t["nom"]} → {c["nom"]}', "table_id": t["id"]}


def _cible_kpi(k):
    return {"type": "kpi", "id": k["id"], "nom": f'{k["table"]} → {k["nom"]}', "table_id": k["table_id"]}


def _pct(x):
    return f"{round(x * 100)} %"


def _jours(ts, maintenant):
    return int((maintenant - ts) / 86400) if ts else None


def _pente_relative(valeurs):
    """Pente des moindres carrés, rapportée à la valeur absolue moyenne (par point d'historique)."""
    n = len(valeurs)
    moy_x, moy_y = (n - 1) / 2, statistics.fmean(valeurs)
    den = sum((i - moy_x) ** 2 for i in range(n))
    if not den or not moy_y:
        return 0.0
    return sum((i - moy_x) * (v - moy_y) for i, v in enumerate(valeurs)) / den / abs(moy_y)


# ═══════════════════════════ MANQUE ═══════════════════════════

@regle("manque_table_vide", "manque")
def _table_vide(etat, s):
    for t in etat["tables"]:
        if t["nb_lignes"] == 0:
            yield _reco("manque_table_vide", "manque", "attention", tf("engine/recommandations.py:g:118:69", v1=t["nom"]),
                        _i18n_t("engine/recommandations.py:g:119:25"),
                        _cible_table(t), {"lignes": 0}, _i18n_t("engine/recommandations.py:g:120:57"))


@regle("manque_colonne_vide", "manque")
def _colonne_vide(etat, s):
    for t in etat["tables"]:
        if t["nb_lignes"] >= s["min_lignes"]:
            for c in t["champs"]:
                if c["rempli"] == 0:
                    yield _reco("manque_colonne_vide", "manque", "attention", tf("engine/recommandations.py:g:129:79", v1=c["nom"]),
                                tf("engine/recommandations.py:g:130:33", v1=t["nb_lignes"], v2=t["nom"]),
                                _cible_champ(t, c), {"lignes": t["nb_lignes"], "remplies": 0},
                                _i18n_t("engine/recommandations.py:g:132:33"))


@regle("manque_colonne_peu_remplie", "manque")
def _colonne_peu_remplie(etat, s):
    for t in etat["tables"]:
        if t["nb_lignes"] >= s["min_lignes"]:
            for c in t["champs"]:
                if c["type"] != "relation" and 0 < c["remplissage"] < s["remplissage_min"]:
                    grave = "attention" if c["remplissage"] < s["remplissage_critique"] else "info"
                    yield _reco("manque_colonne_peu_remplie", "manque", grave, tf("engine/recommandations.py:g:142:80", v1=c["nom"]),
                                tf("engine/recommandations.py:g:143:33", v1=_pct(c["remplissage"]), v2=t["nom"]),
                                _cible_champ(t, c), {"remplissage": round(c["remplissage"], 3), "lignes": t["nb_lignes"]},
                                _i18n_t("engine/recommandations.py:g:145:33"),
                                bonus=int((s["remplissage_min"] - c["remplissage"]) * 20))


@regle("manque_relation_non_renseignee", "manque")
def _relation_non_renseignee(etat, s):
    for t in etat["tables"]:
        if t["nb_lignes"] >= s["min_lignes"]:
            for c in t["champs"]:
                if c["type"] == "relation" and c["remplissage"] < s["relation_remplissage_min"]:
                    yield _reco("manque_relation_non_renseignee", "manque", "attention", tf("engine/recommandations.py:g:155:90", v1=c["nom"]),
                                tf("engine/recommandations.py:g:156:33", v1=_pct(c["remplissage"]), v2=t["nom"]),
                                _cible_champ(t, c), {"remplissage": round(c["remplissage"], 3)},
                                _i18n_t("engine/recommandations.py:g:158:33"))


@regle("manque_kpi_jamais_calcule", "manque")
def _kpi_jamais_calcule(etat, s):
    for k in etat["kpis"]:
        if k["statut"] == "en_attente" and k["valeur"] is None:
            yield _reco("manque_kpi_jamais_calcule", "manque", "info", tf("engine/recommandations.py:g:165:72", v1=k["nom"]),
                        _i18n_t("engine/recommandations.py:g:166:25"), _cible_kpi(k), {}, _i18n_t("engine/recommandations.py:g:166:88"), lancable=True)


@regle("manque_kpi_sans_reference", "manque")
def _kpi_sans_reference(etat, s):
    for k in etat["kpis"]:
        if k["cible"] is None and k["seuil_alerte"] is None:
            yield _reco("manque_kpi_sans_reference", "manque", "info", tf("engine/recommandations.py:g:173:72", v1=k["nom"]),
                        _i18n_t("engine/recommandations.py:g:174:25"),
                        _cible_kpi(k), {}, _i18n_t("engine/recommandations.py:g:175:44"))


# ═══════════════════════════ COUVERTURE ═══════════════════════════

@regle("couverture_table_sans_suivi", "couverture")
def _table_sans_suivi(etat, s):
    for t in etat["tables"]:
        if t["nb_lignes"] >= 2 * s["min_lignes"] and not t["nb_kpis"] and not t["nb_tableaux_bord"] and not t["clients"]:
            yield _reco("couverture_table_sans_suivi", "couverture", "info", tf("engine/recommandations.py:g:184:78", v1=t["nom"]),
                        tf("engine/recommandations.py:g:185:25", v1=t["nb_lignes"]),
                        _cible_table(t), {"lignes": t["nb_lignes"]}, _i18n_t("engine/recommandations.py:g:186:70"))


@regle("couverture_modele_inutilise", "couverture")
def _modele_inutilise(etat, s):
    for m in etat["modeles_ia"]:
        if m["statut"] == "entraine" and m["inferences"] == 0:
            yield _reco("couverture_modele_inutilise", "couverture", "info", tf("engine/recommandations.py:g:193:78", v1=m["nom"]),
                        _i18n_t("engine/recommandations.py:g:194:25"), {"type": "modele_ia", "id": m["id"], "nom": m["nom"]},
                        {}, _i18n_t("engine/recommandations.py:g:195:29"))


# ═══════════════════════════ SURPLUS ═══════════════════════════

@regle("surplus_volume", "surplus")
def _surplus_volume(etat, s):
    for t in etat["tables"]:
        autres = [x["nb_lignes"] for x in etat["tables"] if x["id"] != t["id"]] or [0]
        mediane = statistics.median(autres)
        trop_gros = t["nb_lignes"] >= s["surplus_lignes"] or (
            t["nb_lignes"] > 1000 and mediane and t["nb_lignes"] >= s["surplus_ratio_mediane"] * mediane)
        if trop_gros:
            yield _reco("surplus_volume", "surplus", "info", tf("engine/recommandations.py:g:208:62", v1=t["nom"]),
                        tf("engine/recommandations.py:g:209:25", v1=t["nb_lignes"]),
                        _cible_table(t), {"lignes": t["nb_lignes"], "mediane_autres": mediane},
                        _i18n_t("engine/recommandations.py:g:211:25"))


@regle("surplus_colonne_constante", "surplus")
def _colonne_constante(etat, s):
    for t in etat["tables"]:
        if t["nb_lignes"] >= 2 * s["min_lignes"]:
            for c in t["champs"]:
                if c["distincts"] == 1 and c["remplissage"] >= 0.9 and c["type"] not in ("booleen", "switch"):
                    yield _reco("surplus_colonne_constante", "surplus", "info", tf("engine/recommandations.py:g:220:81", v1=c["nom"]),
                                tf("engine/recommandations.py:g:221:33", v1=t["nom"]),
                                _cible_champ(t, c), {"valeur": c["top_valeur"]}, _i18n_t("engine/recommandations.py:g:222:82"))


@regle("surplus_notifications_non_lues", "surplus")
def _notifications_non_lues(etat, s):
    n = (etat["notifications"] or {}).get("non_lues", 0)
    if n >= s["notifications_non_lues"]:
        yield _reco("surplus_notifications_non_lues", "surplus", "attention", _i18n_t("engine/recommandations.py:g:229:79"),
                    tf("engine/recommandations.py:g:230:21", v1=n),
                    {"type": "notifications"}, {"non_lues": n, "anciennes": etat["notifications"].get("non_lues_anciennes", 0)},
                    _i18n_t("engine/recommandations.py:g:232:21"))


@regle("surplus_kpi_doublon", "surplus")
def _kpi_doublon(etat, s):
    vus = defaultdict(list)
    for k in etat["kpis"]:
        if k["champ_cible_id"] and k["formule_id"]:
            vus[(k["champ_cible_id"], k["formule_id"])].append(k)
    for doublons in vus.values():
        if len(doublons) > 1:
            yield _reco("surplus_kpi_doublon", "surplus", "info", _i18n_t("engine/recommandations.py:g:243:67"),
                        _i18n_t("engine/recommandations.py:g:244:25") + ", ".join(f'« {k["nom"]} »' for k in doublons) + ".",
                        _cible_kpi(doublons[0]), {"kpis": [k["id"] for k in doublons]}, _i18n_t("engine/recommandations.py:g:245:89"))


# ═══════════════════════════ TENDANCE / DIMINUTION ═══════════════════════════

@regle("tendance_hausse_activite", "tendance")
def _hausse_activite(etat, s):
    for t in etat["tables"]:
        if t["n7_prec"] >= 1 and t["n7"] >= s["activite_min"] and t["n7"] >= s["hausse_ratio"] * t["n7_prec"]:
            yield _reco("tendance_hausse_activite", "tendance", "info", tf("engine/recommandations.py:g:254:73", v1=t["nom"]),
                        tf("engine/recommandations.py:g:255:25", v1=t["n7"], v2=t["n7_prec"]),
                        _cible_table(t), {"n7": t["n7"], "n7_prec": t["n7_prec"]},
                        _i18n_t("engine/recommandations.py:g:257:25"))


@regle("diminution_activite", "diminution")
def _diminution_activite(etat, s):
    for t in etat["tables"]:
        if t["n7_prec"] >= s["activite_min"] and t["n7"] <= s["baisse_ratio"] * t["n7_prec"]:
            arret = t["n7"] == 0
            yield _reco("diminution_activite", "diminution", "attention" if arret else "info",
                        tf("engine/recommandations.py:g:266:25", v1=t["nom"]) if arret else tf("engine/recommandations.py:g:266:81", v1=t["nom"]),
                        tf("engine/recommandations.py:g:267:25", v1=t["n7"], v2=t["n7_prec"]),
                        _cible_table(t), {"n7": t["n7"], "n7_prec": t["n7_prec"]},
                        _i18n_t("engine/recommandations.py:g:269:25"),
                        bonus=10 if arret else 0)


@regle("diminution_blockchain", "diminution")
def _diminution_blockchain(etat, s):
    b = etat["blockchain"] or {}
    if b.get("blocs_7j_prec", 0) >= s["activite_min"] and b.get("blocs_7j", 0) <= s["baisse_ratio"] * b["blocs_7j_prec"]:
        yield _reco("diminution_blockchain", "diminution", "info", _i18n_t("engine/recommandations.py:g:277:68"),
                    tf("engine/recommandations.py:g:278:21", v1=b["blocs_7j"], v2=b["blocs_7j_prec"]),
                    {"type": "blockchain"}, {"blocs_7j": b["blocs_7j"], "blocs_7j_prec": b["blocs_7j_prec"]},
                    _i18n_t("engine/recommandations.py:g:280:21"))


def _kpi_pente(k, s):
    h = k["historique"]
    if len(h) < s["kpi_historique_min"]:
        return None
    pente = _pente_relative(h)
    return pente if abs(pente) >= s["kpi_pente_min"] else None


@regle("tendance_kpi_defavorable", "tendance")
def _kpi_defavorable(etat, s):
    for k in etat["kpis"]:
        pente = _kpi_pente(k, s)
        if pente is None or k["sens"] == "cible":
            continue
        if (pente < 0) == (k["sens"] == "hausse"):
            yield _reco("tendance_kpi_defavorable", "tendance", "attention", tf("engine/recommandations.py:g:298:78", v1=k["nom"]),
                        tf("engine/recommandations.py:g:299:25", v1=len(k["historique"]), v2="baisse" if pente < 0 else "monte", v3=_pct(abs(pente)), v4=_i18n_t("engine/recommandations.py:g:301:154") if k["sens"] == "hausse" else _i18n_t("engine/recommandations.py:g:301:205")),
                        _cible_kpi(k), {"pente_relative": round(pente, 4), "derniere": k["valeur"]},
                        _i18n_t("engine/recommandations.py:g:302:25"), bonus=int(min(abs(pente), 0.5) * 20))


@regle("tendance_kpi_favorable", "tendance")
def _kpi_favorable(etat, s):
    for k in etat["kpis"]:
        pente = _kpi_pente(k, s)
        if pente is None or k["sens"] == "cible":
            continue
        if (pente < 0) != (k["sens"] == "hausse"):
            yield _reco("tendance_kpi_favorable", "tendance", "info", _gtf("rg6.msg.101", v1=k["nom"]),
                        tf("engine/recommandations.py:g:313:25", v1=_pct(abs(pente)), v2=len(k["historique"])),
                        _cible_kpi(k), {"pente_relative": round(pente, 4)}, _i18n_t("engine/recommandations.py:g:314:77"))


# ═══════════════════════════ CONCENTRATION ═══════════════════════════

@regle("concentration_valeur", "concentration")
def _concentration_valeur(etat, s):
    for t in etat["tables"]:
        if t["nb_lignes"] >= 2 * s["min_lignes"]:
            for c in t["champs"]:
                if c["top_part"] >= s["concentration_valeur"] and c["distincts"] >= 2:
                    yield _reco("concentration_valeur", "concentration", "info", tf("engine/recommandations.py:g:325:82", v1=c["nom"]),
                                tf("engine/recommandations.py:g:326:33", v1=c["top_valeur"], v2=_pct(c["top_part"]), v3=t["nom"]),
                                _cible_champ(t, c), {"valeur": c["top_valeur"], "part": round(c["top_part"], 3)},
                                _i18n_t("engine/recommandations.py:g:328:33"))


@regle("concentration_createur", "concentration")
def _concentration_createur(etat, s):
    if sum((etat["membres"] or {}).get(r, 0) for r in ("admin", "agent")) < 2:
        return
    for t in etat["tables"]:
        total = sum(t["createurs"].values())
        if total > 0 and total >= 3 * s["min_lignes"]:   # total > 0 : min_lignes = 0 ne doit pas diviser par zéro
            uid, n = max(t["createurs"].items(), key=lambda kv: kv[1])
            if n / total >= s["concentration_createur"]:
                yield _reco("concentration_createur", "concentration", "info", tf("engine/recommandations.py:g:340:80", v1=t["nom"]),
                            tf("engine/recommandations.py:g:341:29", v1=_pct(n / total)),
                            _cible_table(t), {"utilisateur_id": uid, "part": round(n / total, 3)},
                            _i18n_t("engine/recommandations.py:g:343:29"))


@regle("concentration_relations", "concentration")
def _concentration_relations(etat, s):
    r = etat["relations"] or {}
    total = r.get("total_liens", 0)
    for h in r.get("hubs", [])[:3]:
        if h["degre"] >= s["hub_degre_min"] and total and h["degre"] / total >= s["concentration_hub"]:
            yield _reco("concentration_relations", "concentration", "attention", tf("engine/recommandations.py:g:352:82", v1=h["table"]),
                        tf("engine/recommandations.py:g:353:25", v1=h["enregistrement_id"], v2=h["table"], v3=h["degre"], v4=total, v5=_pct(h["degre"] / total)),
                        {"type": "ligne", "id": h["enregistrement_id"], "nom": h["table"], "table_id": h["table_id"]},
                        {"degre": h["degre"], "total_liens": total}, _i18n_t("engine/recommandations.py:g:356:70"))


@regle("concentration_volume", "concentration")
def _concentration_volume(etat, s):
    total = sum(t["nb_lignes"] for t in etat["tables"])
    if len(etat["tables"]) >= 3 and total >= 100:
        t = max(etat["tables"], key=lambda x: x["nb_lignes"])
        if t["nb_lignes"] / total >= s["concentration_volume"]:
            yield _reco("concentration_volume", "concentration", "info", tf("engine/recommandations.py:g:365:74", v1=t["nom"]),
                        tf("engine/recommandations.py:g:366:25", v1=_pct(t["nb_lignes"] / total)),
                        _cible_table(t), {"lignes": t["nb_lignes"], "total": total},
                        _i18n_t("engine/recommandations.py:g:368:25"))


# ═══════════════════════════ ANOMALIE ═══════════════════════════

@regle("anomalie_rafale_creation", "anomalie")
def _rafale(etat, s):
    for t in etat["tables"]:
        non_nuls = sorted(t["jours"].values())
        if len(non_nuls) >= 4:
            mediane, pic = statistics.median(non_nuls), non_nuls[-1]
            if pic >= s["rafale_min"] and mediane and pic >= s["rafale_ratio"] * mediane:
                jour = max(t["jours"], key=t["jours"].get)
                yield _reco("anomalie_rafale_creation", "anomalie", "info", tf("engine/recommandations.py:g:381:77", v1=t["nom"]),
                            tf("engine/recommandations.py:g:382:29", v1=pic, v2=jour, v3=mediane),
                            _cible_table(t), {"jour": jour, "pic": pic, "mediane": mediane}, _i18n_t("engine/recommandations.py:g:383:94"))


@regle("anomalie_valeurs_aberrantes", "anomalie")
def _aberrantes(etat, s):
    for t in etat["tables"]:
        for c in t["champs"]:
            n = c["num"]
            if n and n["aberrants"] > s["aberrants_ratio_min"]:
                yield _reco("anomalie_valeurs_aberrantes", "anomalie", "attention", tf("engine/recommandations.py:g:392:85", v1=c["nom"]),
                            tf("engine/recommandations.py:g:393:29", v1=n["aberrants"], v2=t["nom"], v3=n["moy"]),
                            _cible_champ(t, c), {"aberrants": n["aberrants"], "min": n["min"], "max": n["max"], "moyenne": n["moy"]},
                            _i18n_t("engine/recommandations.py:g:396:29"))


# ═══════════════════════════ OBSOLESCENCE / ISOLEMENT ═══════════════════════════

@regle("obsolescence_table", "obsolescence")
def _table_obsolete(etat, s):
    for t in etat["tables"]:
        age = _jours(t["derniere_ts"], etat["ts"])
        if t["nb_lignes"] >= s["min_lignes"] and age is not None and age >= s["obsolete_jours"]:
            yield _reco("obsolescence_table", "obsolescence", "info", tf("engine/recommandations.py:g:406:71", v1=t["nom"]),
                        tf("engine/recommandations.py:g:407:25", v1=age),
                        _cible_table(t), {"jours": age}, _i18n_t("engine/recommandations.py:g:408:58"))


@regle("obsolescence_kpi", "obsolescence")
def _kpi_obsolete(etat, s):
    for k in etat["kpis"]:
        age = _jours(k["calcule_ts"], etat["ts"])
        if k["statut"] == "pret" and age is not None and age >= s["kpi_obsolete_jours"]:
            yield _reco("obsolescence_kpi", "obsolescence", "info", tf("engine/recommandations.py:g:416:69", v1=k["nom"]),
                        tf("engine/recommandations.py:g:417:25", v1=age), _cible_kpi(k), {"jours": age}, _i18n_t("engine/recommandations.py:g:417:95"), lancable=True)


@regle("isolement_table", "isolement")
def _table_isolee(etat, s):
    if len(etat["tables"]) < 3:
        return
    degres = (etat["relations"] or {}).get("degres_tables", {})
    for t in etat["tables"]:
        if t["nb_lignes"] >= s["min_lignes"] and not t["clients"] and not degres.get(t["id"]) and not degres.get(str(t["id"])):
            yield _reco("isolement_table", "isolement", "info", tf("engine/recommandations.py:g:427:65", v1=t["nom"]),
                        _i18n_t("engine/recommandations.py:g:428:25"),
                        _cible_table(t), {"lignes": t["nb_lignes"]}, _i18n_t("engine/recommandations.py:g:429:70"))


# ═══════════════════════════ OBJECTIF ═══════════════════════════

@regle("objectif_kpi_hors_cible", "objectif")
def _kpi_hors_cible(etat, s):
    for k in etat["kpis"]:
        if k["etat"] in ("alerte", "danger"):
            danger = k["etat"] == "danger"
            yield _reco("objectif_kpi_hors_cible", "objectif", "critique" if danger else "attention",
                        f'KPI « {k["nom"]} » {_gt("engine/recommandations.py:442:49") if danger else "sous surveillance"}',
                        tf("engine/recommandations.py:g:441:25", v1=k["valeur"], v2=k["cible"]),
                        _cible_kpi(k), {"valeur": k["valeur"], "cible": k["cible"], "seuil_alerte": k["seuil_alerte"]},
                        _i18n_t("engine/recommandations.py:g:443:25"))


# ═══════════════════════════ INTÉGRITÉ / DYSFONCTIONNEMENT ═══════════════════════════

@regle("integrite_chaine_invalide", "integrite")
def _chaine_invalide(etat, s):
    if (etat["blockchain"] or {}).get("chaine_valide") is False:
        yield _reco("integrite_chaine_invalide", "integrite", "critique", _i18n_t("engine/recommandations.py:g:451:75"),
                    _i18n_t("engine/recommandations.py:g:452:21"),
                    {"type": "blockchain"}, {}, _i18n_t("engine/recommandations.py:g:453:49"), bonus=15)


@regle("integrite_blockchain_inactive", "integrite")
def _blockchain_inactive(etat, s):
    b = etat["blockchain"] or {}
    activite = sum(t["n7"] for t in etat["tables"])
    if b and activite >= s["activite_min"] and b.get("blocs_7j", 0) == 0:
        yield _reco("integrite_blockchain_inactive", "integrite", "attention", _i18n_t("engine/recommandations.py:g:461:80"),
                    tf("engine/recommandations.py:g:462:21", v1=activite),
                    {"type": "blockchain"}, {"lignes_7j": activite}, _i18n_t("engine/recommandations.py:g:463:70"))


@regle("dysfonctionnement_taches", "dysfonctionnement")
def _taches_en_erreur(etat, s):
    for t in etat["taches"]:
        if str(t["statut"]).startswith("echec"):
            yield _reco("dysfonctionnement_taches", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:470:87", v1=t["nom"]),
                        t["erreur"] or _i18n_t("engine/recommandations.py:g:471:40"), {"type": "tache", "id": t["id"], "nom": t["nom"]}, {},
                        _i18n_t("engine/recommandations.py:g:472:25") if t.get("a_script")
                        else _i18n_t("engine/recommandations.py:g:473:30"),
                        lancable=bool(t.get("a_script")))


@regle("dysfonctionnement_tache_jamais_lancee", "dysfonctionnement")
def _tache_jamais_lancee(etat, s):
    for t in etat["taches"]:
        age = _jours(t["cree_ts"], etat["ts"])
        if t["mode"] == "planifie" and t["actif"] and not t["derniere_ts"] and age is not None and age >= s["tache_jamais_lancee_jours"]:
            yield _reco("dysfonctionnement_tache_jamais_lancee", "dysfonctionnement", "info", tf("engine/recommandations.py:g:482:95", v1=t["nom"]),
                        tf("engine/recommandations.py:g:483:25", v1=age), {"type": "tache", "id": t["id"], "nom": t["nom"]}, {},
                        _i18n_t("engine/recommandations.py:g:484:25") if t.get("a_script") else _i18n_t("engine/recommandations.py:g:484:139"),
                        lancable=bool(t.get("a_script")))


@regle("dysfonctionnement_kpi_echec", "dysfonctionnement")
def _kpi_echec(etat, s):
    for k in etat["kpis"]:
        if k["statut"] == "echec":
            yield _reco("dysfonctionnement_kpi_echec", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:492:90", v1=k["nom"]),
                        (k["erreur"] or _i18n_t("engine/recommandations.py:g:493:41"))[:200], _cible_kpi(k), {}, _i18n_t("engine/recommandations.py:g:493:98"))


@regle("dysfonctionnement_modele_ia", "dysfonctionnement")
def _modele_en_erreur(etat, s):
    for m in etat["modeles_ia"]:
        if m["statut"] == "erreur":
            yield _reco("dysfonctionnement_modele_ia", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:500:90", v1=m["nom"]),
                        _i18n_t("engine/recommandations.py:g:501:25"), {"type": "modele_ia", "id": m["id"], "nom": m["nom"]}, {},
                        _i18n_t("engine/recommandations.py:g:502:25"))


def evaluer(etat, seuils=None):
    """Applique toutes les règles à `etat` : {recommandations (triées par score), par_categorie, par_gravite, erreurs_regles}."""
    s = {**SEUILS, **(seuils or {})}
    recos, erreurs = [], []
    for code, _cat, fonction in _REGLES + _REGLES_GEO + _REGLES_ARCHI:
        try:
            recos.extend(fonction(etat, s))
        except Exception as exc:  # une règle défaillante ne bloque pas les autres
            logger.exception(_i18n_t("engine/recommandations.py:g:511:30"), code)
            erreurs.append(f"{code} : {type(exc).__name__}")
    recos.sort(key=lambda r: (-r["score"], r["titre"]))
    return {
        "recommandations": recos,
        "par_categorie": {c: sum(1 for r in recos if r["categorie"] == c) for c in CATEGORIES},
        "par_gravite": {g: sum(1 for r in recos if r["gravite"] == g) for g in GRAVITES},
        "regles": len(_REGLES) + len(_REGLES_GEO) + len(_REGLES_ARCHI), "erreurs_regles": erreurs,
    }


def mesurer_effets(suivis, recos, maintenant, seuils=None):
    """Boucle fermée : compare chaque action lancée (score avant) aux recommandations actuelles.
    `suivis` = [{code, cible_type, cible_id, nom, ts, score}] ; effet : en_cours / resolu / ameliore / persiste / aggrave."""
    delai = {**SEUILS, **(seuils or {})}["suivi_delai_s"]
    actuel = {(r["code"], r["cible"].get("type"), r["cible"].get("id")): r for r in recos}
    effets = []
    for a in suivis:
        r = actuel.get((a["code"], a["cible_type"], a["cible_id"]))
        apres = r["score"] if r else None
        effet = ("en_cours" if maintenant - a["ts"] < delai else "resolu" if r is None
                 else "aggrave" if apres > a["score"] else "ameliore" if apres < a["score"] else "persiste")
        effets.append({**a, "score_apres": apres, "effet": effet})
    return effets


def catalogue():
    """Liste des 33 règles-critères (code, catégorie) — pour l'interface. Les règles géospatiales : `catalogue_geo()`."""
    return [{"code": code, "categorie": cat} for code, cat, _f in _REGLES]


def catalogue_geo():
    return [{"code": code, "categorie": cat} for code, cat, _f in _REGLES_GEO]


def catalogue_archi():
    return [{"code": code, "categorie": cat} for code, cat, _f in _REGLES_ARCHI]



# ── Règles d'ARCHITECTURE (etat["architecture"], voir engine/fusion.py::_fusion_architecture) ──────────────────────────────
def _archi(etat, nom, defaut=None):
    return (etat.get("architecture") or {}).get(nom, defaut)


def _cible_table_id(table_id, etat):
    nom = next((t["nom"] for t in etat.get("tables", []) if t["id"] == table_id), None)
    return {"type": "table", "id": table_id, "nom": nom} if table_id is not None else {"type": "plateforme"}


@regle_archi("archi_document_en_echec", "dysfonctionnement")
def _document_en_echec(etat, s):
    for d in (_archi(etat, "documents", {}) or {}).get("echecs", []):
        yield _reco("archi_document_en_echec", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:566:82", v1=d["titre"]),
                    d["erreur"] or _i18n_t("engine/recommandations.py:g:567:36"), _cible_table_id(d["table_id"], etat), {},
                    _i18n_t("engine/recommandations.py:g:568:21"))


@regle_archi("archi_skill_en_echec", "dysfonctionnement")
def _skill_en_echec(etat, s):
    for k in (_archi(etat, "skills", {}) or {}).get("echecs", []):
        yield _reco("archi_skill_en_echec", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:574:79", v1=k["nom"]),
                    k["erreur"] or _i18n_t("engine/recommandations.py:g:575:36"), _cible_table_id(k["table_id"], etat), {},
                    _i18n_t("engine/recommandations.py:g:576:21"))


@regle_archi("archi_analyse_en_echec", "dysfonctionnement")
def _analyse_en_echec(etat, s):
    for a in (_archi(etat, "analyses", {}) or {}).get("echecs", []):
        yield _reco("archi_analyse_en_echec", "dysfonctionnement", "info", tf("engine/recommandations.py:g:582:76", v1=a["texte"]),
                    a["erreur"] or _i18n_t("engine/recommandations.py:g:583:36"), _cible_table_id(a["table_id"], etat), {},
                    _i18n_t("engine/recommandations.py:g:584:21"))


@regle_archi("archi_import_en_echec", "dysfonctionnement")
def _import_en_echec(etat, s):
    for i in (_archi(etat, "imports", {}) or {}).get("echecs", []):
        yield _reco("archi_import_en_echec", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:590:80", v1=i["nom"]),
                    i["erreur"] or _i18n_t("engine/recommandations.py:g:591:36"), {"type": "plateforme"}, {"source": i["source"]},
                    _i18n_t("engine/recommandations.py:g:592:21"))


@regle_archi("archi_connexion_externe_en_echec", "dysfonctionnement")
def _connexion_externe_en_echec(etat, s):
    for c in _archi(etat, "connexions_externes", []) or []:
        if c.get("actif") and c.get("synchro_statut") == "echec":
            yield _reco("archi_connexion_externe_en_echec", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:599:95", v1=c["nom"]),
                        c.get("erreur") or _i18n_t("engine/recommandations.py:g:600:44"), {"type": "plateforme"}, {"moteur": c["moteur"]},
                        _i18n_t("engine/recommandations.py:g:601:25"))


@regle_archi("archi_connexion_externe_inutilisee", "couverture")
def _connexion_externe_inutilisee(etat, s):
    for c in _archi(etat, "connexions_externes", []) or []:
        if c.get("actif") and not c.get("tables_importees") and not c.get("synchro_ts"):
            yield _reco("archi_connexion_externe_inutilisee", "couverture", "info", tf("engine/recommandations.py:g:608:85", v1=c["nom"]),
                        _i18n_t("engine/recommandations.py:g:609:25"), {"type": "plateforme"}, {"moteur": c["moteur"]},
                        _i18n_t("engine/recommandations.py:g:610:25"))


@regle_archi("archi_cle_api_inutilisee", "obsolescence")
def _cle_api_inutilisee(etat, s):
    seuil = SEUILS_ARCHI["cle_api_inutilisee_jours"]
    for c in _archi(etat, "cles_api", []) or []:
        if c.get("profil"):
            continue   # jeton personnel de la page profil : propre à son membre, pas une clé d'intégration à révoquer
        age = _jours(c.get("utilisee_ts") or c.get("cree_ts"), etat["ts"])
        if age is not None and age >= seuil:
            yield _reco("archi_cle_api_inutilisee", "obsolescence", "info", _i18n_t("engine/recommandations.py:g:621:77"),
                        tf("engine/recommandations.py:g:622:25", v1=age),
                        {"type": "plateforme"}, {"jours": age}, _i18n_t("engine/recommandations.py:g:623:65"))

# ── Règles géospatiales ──────────────────────────────────────────────────────
def _tables_geo(etat):
    return (etat.get("geo") or {}).get("tables") or []


@regle_geo("geo_collision_prevue", "anomalie")
def _geo_collision_prevue(etat, s):
    for g in _tables_geo(etat):
        p = g.get("proba_max") or 0.0
        if p < SEUILS_GEO["proba_attention"] or not g.get("top_collisions"):
            continue
        c = g["top_collisions"][0]
        yield _reco("geo_collision_prevue", "anomalie", "critique" if p >= SEUILS_GEO["proba_critique"] else "attention",
                    tf("engine/recommandations.py:g:638:21", v1=c["a"], v2=c["b"]),
                    tf("engine/recommandations.py:g:639:21", v1=g["nom"], v2=g["collisions_prevues"], v3=g["seuil_m"], v4=c["a"], v5=c["b"], v6=_pct(p), v7=c["dans_s"], v8=c["dmin_m"]),
                    cible=_cible_table(g), preuves={"probabilite": p, "seuil_m": g["seuil_m"], "croisements": g["top_collisions"]},
                    action=_i18n_t("engine/recommandations.py:g:640:28"),
                    bonus=int(p * 10))


@regle_geo("geo_rencontre_rapprochee", "anomalie")
def _geo_rencontre(etat, s):
    for g in _tables_geo(etat):
        if not g.get("rencontres"):
            continue
        proche = g["rencontre_min_m"] is not None and g["rencontre_min_m"] < g["seuil_m"] / 2
        yield _reco("geo_rencontre_rapprochee", "anomalie", "attention" if proche else "info",
                    tf("engine/recommandations.py:g:653:21", v1=g["nom"], v2=g["rencontres"]),
                    tf("engine/recommandations.py:g:654:21", v1=g["seuil_m"], v2=g["rencontre_min_m"]),
                    cible=_cible_table(g), preuves={"rencontres": g["rencontres"], "minimum_m": g["rencontre_min_m"], "detail": g["top_rencontres"]},
                    action=_i18n_t("engine/recommandations.py:g:654:28"))


@regle_geo("geo_saut_position", "anomalie")
def _geo_saut(etat, s):
    for g in _tables_geo(etat):
        if g.get("sauts"):
            yield _reco("geo_saut_position", "anomalie", "attention", f'{g["nom"]} : positions aberrantes',
                        tf("engine/recommandations.py:g:664:25", v1=g["sauts"]),
                        cible=_cible_table(g), preuves={"sauts": g["sauts"]}, action=_i18n_t("engine/recommandations.py:g:663:86"))


@regle_geo("geo_position_manquante", "manque")
def _geo_manquante(etat, s):
    for g in _tables_geo(etat):
        if g.get("manquantes"):
            yield _reco("geo_position_manquante", "manque", "attention", tf("engine/recommandations.py:g:672:74", v1=g["nom"]),
                        tf("engine/recommandations.py:g:673:25", v1=g["manquantes"]),
                        cible=_cible_table(g), preuves={"objets": g["manquantes"]}, action=_i18n_t("engine/recommandations.py:g:672:92"))


@regle_geo("geo_objet_immobile", "diminution")
def _geo_immobile(etat, s):
    for g in _tables_geo(etat):
        if g.get("immobiles"):
            yield _reco("geo_objet_immobile", "diminution", "info", tf("engine/recommandations.py:g:681:69", v1=g["nom"]),
                        tf("engine/recommandations.py:g:682:25", v1=g["immobiles"]),
                        cible=_cible_table(g), preuves={"objets": g["immobiles"]}, action=_i18n_t("engine/recommandations.py:g:681:91"))


@regle_geo("geo_concentration_spatiale", "concentration")
def _geo_concentration(etat, s):
    for g in _tables_geo(etat):
        if g.get("objets", 0) >= SEUILS_GEO["objets_min_concentration"] and (g.get("concentration_part") or 0) >= SEUILS_GEO["concentration"]:
            yield _reco("geo_concentration_spatiale", "concentration", "info", f'{g["nom"]} : concentration spatiale',
                        tf("engine/recommandations.py:g:691:25", v1=_pct(g["concentration_part"])),
                        cible=_cible_table(g), preuves={"part": g["concentration_part"]}, action=_i18n_t("engine/recommandations.py:g:690:98"))


@regle_geo("geo_prevision_degradee", "dysfonctionnement")
def _geo_prevision(etat, s):
    for g in _tables_geo(etat):
        if g.get("gain_prevision") is not None and g["gain_prevision"] < 0:
            yield _reco("geo_prevision_degradee", "dysfonctionnement", "attention", tf("engine/recommandations.py:g:699:85", v1=g["nom"]),
                        tf("engine/recommandations.py:g:700:25", v1=g["erreur_prevision_m"]),
                        cible=_cible_table(g), preuves={"erreur_m": g["erreur_prevision_m"], "gain": g["gain_prevision"]},
                        action=_i18n_t("engine/recommandations.py:g:700:32"))


# ── Règles LOGISTIQUES (dernier plan enregistré, état["geo"]["logistique"]) ──────────────────────────────────────────────
SEUILS_LOGISTIQUE = {"validite_s": 86400, "utilisation_min": 0.5, "gain_depot": 0.15}


def _plan_recent(etat):
    p = (etat.get("geo") or {}).get("logistique")
    if not p or (etat.get("ts") or 0) - p.get("ts", 0) > SEUILS_LOGISTIQUE["validite_s"]:
        return None   # un plan de plus d'un jour ne dit plus rien de l'état présent
    return p


def _cible_plan(p):
    return {"type": "plateforme", "id": 0, "nom": _gtf("rg6.msg.102", v1=p.get("titre") or _gt("rg6.msg.103"))}


@regle_geo("logi_commandes_non_affectees", "couverture")
def _logi_non_affectees(etat, s):
    p = _plan_recent(etat)
    if p and p.get("non_affectees"):
        yield _reco("logi_commandes_non_affectees", "couverture", "critique", tf("engine/recommandations.py:g:724:79", v1=p["non_affectees"]),
                    tf("engine/recommandations.py:g:725:21", v1=p["non_affectees"], v2=p["commandes"], v3=p["capacite"], v4=p["vehicules"], v5=p["flotte_min"]),
                    cible=_cible_plan(p), preuves={"non_affectees": p["non_affectees"], "flotte_min": p["flotte_min"], "vehicules": p["vehicules"]},
                    action=_i18n_t("engine/recommandations.py:g:725:28"), bonus=min(10, p["non_affectees"]))


@regle_geo("logi_retards_prevus", "tendance")
def _logi_retards(etat, s):
    p = _plan_recent(etat)
    if p and p.get("retards"):
        yield _reco("logi_retards_prevus", "tendance", "attention", tf("engine/recommandations.py:g:735:69", v1=p["retards"]),
                    tf("engine/recommandations.py:g:736:21", v1=p["retards"], v2=p["retard_total_s"] / 60),
                    cible=_cible_plan(p), preuves={"retards": p["retards"], "retard_total_s": p["retard_total_s"]},
                    action=_i18n_t("engine/recommandations.py:g:735:28"))


@regle_geo("logi_flotte_sous_utilisee", "surplus")
def _logi_flotte(etat, s):
    p = _plan_recent(etat)
    if p and p.get("vehicules_utilises", 0) > p.get("flotte_min", 0) and p.get("utilisation", 1) < SEUILS_LOGISTIQUE["utilisation_min"] and not p.get("retards"):
        yield _reco("logi_flotte_sous_utilisee", "surplus", "info", _i18n_t("engine/recommandations.py:g:745:69"),
                    tf("engine/recommandations.py:g:746:21", v1=p["vehicules_utilises"], v2=_pct(p["utilisation"]), v3=p["flotte_min"]),
                    cible=_cible_plan(p), preuves={"utilisation": p["utilisation"], "flotte_min": p["flotte_min"]},
                    action=_i18n_t("engine/recommandations.py:g:745:28"))


@regle_geo("logi_depot_mal_place", "concentration")
def _logi_depot(etat, s):
    p = _plan_recent(etat)
    if p and (p.get("depot_gain") or 0) >= SEUILS_LOGISTIQUE["gain_depot"]:
        yield _reco("logi_depot_mal_place", "concentration", "info", _i18n_t("engine/recommandations.py:g:755:70"),
                    tf("engine/recommandations.py:g:756:21", v1=_pct(p["depot_gain"])),
                    cible=_cible_plan(p), preuves={"gain": p["depot_gain"]}, action=_i18n_t("engine/recommandations.py:g:754:85"))

