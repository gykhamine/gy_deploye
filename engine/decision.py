"""MOTEUR DE DÉCISION : pilote les recommandations à partir de la toile de données liées.

Trois étages, tous des algorithmes (aucune règle écrite « au cas par cas », aucune valeur par objet) :

1. COMPRENDRE (statistique) — `comprendre(etat, graphe)`
   Pour chaque table : volume, activité récente, remplissage moyen, fraîcheur, et surtout son
   EXPOSITION dans la toile (ce qui dépend d'elle, directement ou en cascade : dashboards, KPI,
   modèles IA, tables de calcul...). Chaque mesure est située par rapport aux autres tables
   (score robuste médiane/MAD et rang percentile) : « important » veut dire « haut dans la
   distribution de CETTE entreprise », pas « dépasse un seuil fixe ».

2. RISQUER — `evaluer_risques(recommandations, profil, graphe)`
   Les recommandations (engine/recommandations.py) disent CE QUI ne va pas. Le moteur estime
   ce que ça COÛTE : risque = probabilité × impact. La probabilité vient de la gravité et du
   score de la recommandation, majorée si la table est statistiquement atypique. L'impact vient
   de l'importance de la cible et de la propagation du problème en aval dans la toile
   (amortie de moitié à chaque saut). Les risques d'un même objet se combinent en « ou
   bruité » (1 − Π(1 − r)) : trois problèmes moyens sur la même table pèsent plus qu'un seul.

3. DÉCIDER — `decider(...)`
   Verdict par recommandation : « agir » (action lançable et risque élevé), « décider »
   (arbitrage humain requis), « surveiller » (risque faible). Les recommandations sont
   réordonnées par priorité de décision. Chaque verdict porte ses raisons chiffrées.

Fantôme : aucun modèle, aucune migration, rien n'est stocké — tout est recalculé à la demande
depuis l'état fusionné (engine/fusion.py) et la toile (engine/portail.graphe_dependances).
Module PUR (aucun import Django, aucun accès base) : testable avec des dictionnaires.
"""
from __future__ import annotations
from engine.i18n import tf, tl

import math
import statistics
from collections import defaultdict, deque

PARAMETRES = {
    "amortissement": 0.5,       # part du problème transmise à chaque saut en aval
    "profondeur_max": 4,        # sauts maximum de propagation
    "poids_importance": {"exposition": 0.6, "activite": 0.25, "volume": 0.15},
    "proba_gravite": {"critique": 0.9, "attention": 0.6, "info": 0.25},
    "bonus_atypie": 0.05,       # majoration de probabilité si la table est atypique (|z| > seuil)
    "seuil_atypie": 2.0,
    "seuil_agir": 40,           # risque (0-100) au-dessus duquel une action lançable est à lancer
    "seuil_decider": 30,        # idem pour une décision à arbitrer par un humain
    "bonus_action": 0.25,       # une action lançable est prioritaire à risque égal
    "top_objets": 10,
}
_LIBELLES_TYPES = {"dashboard": "dashboard", "kpi": "KPI", "modele_ia": tl("engine/decision.py:g:47:73"), "calcul": tl("engine/decision.py:g:47:96"),
                   "skill": "skill", "table": "table", "champ": "colonne"}


# ── Outils statistiques (pur Python) ────────────────────────────────────────
def _z_robuste(valeurs: list[float]) -> list[float]:
    """Score z robuste : (x − médiane) / (1,4826 × MAD). Repli sur l'écart-type si MAD = 0 ;
    tout à 0 si la série est constante ou trop courte (rien d'atypique à dire)."""
    if len(valeurs) < 3:
        return [0.0] * len(valeurs)
    med = statistics.median(valeurs)
    mad = statistics.median(abs(v - med) for v in valeurs) * 1.4826
    if not mad:
        mad = statistics.pstdev(valeurs)
    return [(v - med) / mad if mad else 0.0 for v in valeurs]


def _rang_percentile(valeurs: list[float]) -> list[float]:
    """Rang percentile 0..1 de chaque valeur (ex æquo = rang moyen). Une seule valeur => 0.5."""
    n = len(valeurs)
    if n <= 1:
        return [0.5] * n
    ordre = sorted(valeurs)
    rangs = []
    for v in valeurs:
        bas, egal = sum(1 for x in ordre if x < v), sum(1 for x in ordre if x == v)
        rangs.append((bas + (egal - 1) / 2) / (n - 1))
    return rangs


def _ou_bruite(risques: list[float]) -> float:
    """Combinaison de risques indépendants en 0..100 : 1 − Π(1 − r/100)."""
    reste = 1.0
    for r in risques:
        reste *= 1 - max(0.0, min(r, 100.0)) / 100
    return 100 * (1 - reste)


# ── Toile : indexation et propagation ───────────────────────────────────────
def _enfants(graphe: dict) -> dict[str, list[str]]:
    """noeud -> ce qui en dépend (sens des arêtes : source → cible = « cible dépend de source »)."""
    enfants: dict[str, list[str]] = defaultdict(list)
    for a in graphe.get("aretes", []):
        enfants[a["source"]].append(a["cible"])
    return enfants


def _en_aval(noeud: str, enfants: dict, p: dict) -> dict[str, int]:
    """Nœuds atteints en aval de `noeud` avec leur distance (BFS borné, sans cycle infini)."""
    vus, file = {noeud: 0}, deque([noeud])
    while file:
        courant = file.popleft()
        if vus[courant] >= p["profondeur_max"]:
            continue
        for e in enfants.get(courant, ()):
            if e not in vus:
                vus[e] = vus[courant] + 1
                file.append(e)
    vus.pop(noeud)
    return vus


def _poids_aval(distances: dict[str, int], p: dict) -> float:
    """Poids d'un ensemble d'objets en aval : Σ amortissement^(distance−1). Un dashboard
    directement branché vaut 1, un objet à deux sauts 0,5, etc."""
    return sum(p["amortissement"] ** (d - 1) for d in distances.values())


# ── 1. COMPRENDRE ───────────────────────────────────────────────────────────
def comprendre(etat: dict, graphe: dict, parametres: dict | None = None) -> dict:
    """Profil statistique de chaque table + résumé de la toile.

    Retour : {tables:{table_id:{nom, exposition, dependants, importance, z:{volume, activite,
    remplissage, fraicheur}, atypique}}, toile:{noeuds, aretes, densite, tables_isolees}}."""
    p = {**PARAMETRES, **(parametres or {})}
    enfants = _enfants(graphe)
    tables = etat.get("tables", [])
    maintenant = etat.get("ts")

    volumes, activites, remplissages, fraicheurs = [], [], [], []
    for t in tables:
        volumes.append(math.log1p(t.get("nb_lignes", 0)))
        activites.append(math.log1p(t.get("n30", 0)))
        champs = t.get("champs") or []
        remplissages.append(statistics.fmean(c.get("remplissage", 0.0) for c in champs) if champs else 0.0)
        derniere = t.get("derniere_ts")
        fraicheurs.append(((maintenant - derniere) / 86400) if (maintenant and derniere) else 365.0)

    aval = {t["id"]: _en_aval(f"table:{t['id']}", enfants, p) for t in tables}
    expositions = [_poids_aval(aval[t["id"]], p) for t in tables]

    z_vol, z_act = _z_robuste(volumes), _z_robuste(activites)
    z_rem, z_fra = _z_robuste(remplissages), _z_robuste(fraicheurs)
    r_exp, r_act, r_vol = _rang_percentile(expositions), _rang_percentile(activites), _rang_percentile(volumes)
    w = p["poids_importance"]

    profil = {}
    for i, t in enumerate(tables):
        importance = w["exposition"] * r_exp[i] + w["activite"] * r_act[i] + w["volume"] * r_vol[i]
        z = {k: round(max(-50.0, min(50.0, v)), 2) for k, v in
             (("volume", z_vol[i]), ("activite", z_act[i]), ("remplissage", z_rem[i]), ("fraicheur", z_fra[i]))}
        profil[t["id"]] = {
            "nom": t.get("nom", ""), "exposition": round(expositions[i], 2), "dependants": len(aval[t["id"]]),
            "importance": round(importance, 3), "z": z,
            "atypique": max(abs(v) for v in z.values()) > p["seuil_atypie"],
        }
    noeuds, aretes = len(graphe.get("noeuds", {})), len(graphe.get("aretes", []))
    return {
        "tables": profil,
        "toile": {"noeuds": noeuds, "aretes": aretes,
                  "densite": round(aretes / noeuds, 2) if noeuds else 0.0,
                  "tables_isolees": sum(1 for pr in profil.values() if pr["dependants"] == 0)},
    }


# ── 2. RISQUER ──────────────────────────────────────────────────────────────
def _noeud_cible(cible: dict, graphe: dict) -> str | None:
    """Nœud de la toile visé par une recommandation (ou None si la cible n'est pas dans la toile)."""
    if not cible:
        return None
    type_c, id_c = cible.get("type"), cible.get("id")
    nid = f"{type_c}:{id_c}"
    if nid in graphe.get("noeuds", {}):
        return nid
    if cible.get("table_id") and f"table:{cible['table_id']}" in graphe.get("noeuds", {}):
        return f"table:{cible['table_id']}"
    return None


def _table_de_cible(cible: dict, noeud: str | None, graphe: dict):
    if noeud and graphe["noeuds"][noeud].get("table_id"):
        return graphe["noeuds"][noeud]["table_id"]
    return (cible or {}).get("table_id") or ((cible or {}).get("id") if (cible or {}).get("type") == "table" else None)


def _decrire_aval(distances: dict[str, int], graphe: dict) -> str:
    compte: dict[str, int] = defaultdict(int)
    for nid in distances:
        compte[graphe["noeuds"][nid]["type"]] += 1
    compte.pop("champ", None)  # les colonnes sont du détail, pas un « objet touché » lisible
    morceaux = [f"{n} {_LIBELLES_TYPES.get(t, t)}" for t, n in sorted(compte.items(), key=lambda x: -x[1])]
    return ", ".join(morceaux)


def evaluer_risques(recommandations: list[dict], profil: dict, graphe: dict, parametres: dict | None = None) -> list[dict]:
    """Une entrée de risque par recommandation, dans l'ordre reçu :
    {code, cible, probabilite, impact, risque, aval, pourquoi:[...]}."""
    p = {**PARAMETRES, **(parametres or {})}
    enfants = _enfants(graphe)
    resultats = []
    for r in recommandations:
        cible = r.get("cible") or {}
        noeud = _noeud_cible(cible, graphe)
        table_id = _table_de_cible(cible, noeud, graphe)
        pr = (profil.get("tables") or {}).get(table_id) if table_id is not None else None
        distances = _en_aval(noeud, enfants, p) if noeud else {}
        poids = _poids_aval(distances, p)

        base = p["proba_gravite"].get(r.get("gravite"), 0.25)
        bonus_score = max(0, r.get("score", 0) - {"critique": 80, "attention": 50, "info": 20}.get(r.get("gravite"), 20)) / 100
        proba = base + min(bonus_score, 0.1) + (p["bonus_atypie"] if pr and pr["atypique"] else 0.0)
        proba = min(proba, 0.99)

        importance = pr["importance"] if pr else 0.0
        propagation = 1 - 1 / (1 + poids)          # 0 sans dépendant, tend vers 1 quand la cascade grandit
        impact = 0.5 * importance + 0.5 * propagation
        risque = round(100 * proba * impact, 1)

        pourquoi = []
        if distances:
            decrit = _decrire_aval(distances, graphe)
            if decrit:
                pourquoi.append(tf("engine/decision.py:g:219:33", v1=decrit))
        if pr:
            pourquoi.append(tf("engine/decision.py:g:221:29", v1=round(importance * 100)))
            if pr["atypique"]:
                extremes = sorted(pr["z"].items(), key=lambda kv: -abs(kv[1]))[0]
                pourquoi.append(tf("engine/decision.py:g:224:33", v1=extremes[0], v2=extremes[1]))
        resultats.append({"code": r.get("code"), "cible": cible, "objet": noeud or (f"table:{table_id}" if table_id else None),
                          "probabilite": round(proba, 2), "impact": round(impact, 2), "risque": risque,
                          "aval": len(distances), "pourquoi": pourquoi})
    return resultats


def risques_par_objet(recommandations: list[dict], risques: list[dict], parametres: dict | None = None) -> list[dict]:
    """Regroupe les risques par objet (combinaison « ou bruité ») : classement des objets les
    plus exposés et détection des accumulations (plusieurs problèmes sur le même objet)."""
    p = {**PARAMETRES, **(parametres or {})}
    par_objet: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for r, k in zip(recommandations, risques):
        if k["objet"]:
            par_objet[k["objet"]].append((r, k))
    objets = []
    for objet, paires in par_objet.items():
        cumule = _ou_bruite([k["risque"] for _r, k in paires])
        objets.append({"objet": objet, "nom": (paires[0][0].get("cible") or {}).get("nom", objet),
                       "nb_recommandations": len(paires), "risque_cumule": round(cumule, 1),
                       "accumulation": len(paires) >= 3})
    objets.sort(key=lambda o: -o["risque_cumule"])
    return objets[: p["top_objets"]]


# ── 3. DÉCIDER ──────────────────────────────────────────────────────────────
def decider(recommandations: list[dict], risques: list[dict], parametres: dict | None = None) -> list[dict]:
    """Annote chaque recommandation d'une clé `decision` et les renvoie triées par priorité.

    decision = {verdict: agir|decider|surveiller, risque, priorite, probabilite, impact, pourquoi}.
    Priorité = risque × (1 + bonus si action lançable) : à risque égal, ce qu'on peut lancer
    tout de suite passe avant ce qui demande un arbitrage. Les recommandations d'origine ne
    sont pas modifiées (copies)."""
    p = {**PARAMETRES, **(parametres or {})}
    annotees = []
    for r, k in zip(recommandations, risques):
        action = r.get("type") == "action"
        priorite = round(k["risque"] * (1 + (p["bonus_action"] if action else 0.0)), 1)
        if action and k["risque"] >= p["seuil_agir"]:
            verdict = "agir"
        elif not action and k["risque"] >= p["seuil_decider"]:
            verdict = "decider"
        else:
            verdict = "surveiller"
        annotees.append({**r, "decision": {
            "verdict": verdict, "risque": k["risque"], "priorite": priorite,
            "probabilite": k["probabilite"], "impact": k["impact"], "pourquoi": k["pourquoi"]}})
    annotees.sort(key=lambda r: (-r["decision"]["priorite"], -r.get("score", 0), r.get("titre", "")))
    return annotees


def piloter(etat: dict, graphe: dict, resultat_reco: dict, parametres: dict | None = None) -> dict:
    """Point d'entrée : pilote le résultat de recommandations.evaluer() avec la toile.

    Retour : {recommandations (réordonnées, annotées), comprehension, objets_a_risque, resume}.
    `resultat_reco` n'est pas modifié."""
    recos = resultat_reco.get("recommandations", [])
    profil = comprendre(etat, graphe, parametres)
    risques = evaluer_risques(recos, profil, graphe, parametres)
    objets = risques_par_objet(recos, risques, parametres)
    decisions = decider(recos, risques, parametres)
    compte = defaultdict(int)
    for d in decisions:
        compte[d["decision"]["verdict"]] += 1
    top = [o["risque_cumule"] for o in objets[:5]]
    return {
        "recommandations": decisions,
        "comprehension": {"toile": profil["toile"],
                          "tables_atypiques": [{"table_id": tid, "nom": pr["nom"], "z": pr["z"]}
                                               for tid, pr in profil["tables"].items() if pr["atypique"]],
                          "tables_importantes": sorted(
                              ({"table_id": tid, "nom": pr["nom"], "importance": pr["importance"], "dependants": pr["dependants"]}
                               for tid, pr in profil["tables"].items()), key=lambda x: -x["importance"])[:5]},
        "objets_a_risque": objets,
        "resume": {"agir": compte["agir"], "decider": compte["decider"], "surveiller": compte["surveiller"],
                   "risque_global": round(statistics.fmean(top), 1) if top else 0.0},
    }


# ── Alertes : quelles recommandations méritent une notification ─────────────
def cle_reco(reco: dict) -> str:
    """Clé stable d'une recommandation : son code + l'objet visé (indépendante du score, qui bouge à chaque recalcul)."""
    cible = reco.get("cible") or {}
    return f"{reco.get('code', '?')}|{cible.get('type', '')}:{cible.get('id', '')}"[:200]


def urgentes(recommandations: list[dict]) -> dict[str, dict]:
    """{clé: recommandation} de celles que `decider` a classées « agir » (action lançable et risque élevé)."""
    return {cle_reco(r): r for r in recommandations if (r.get("decision") or {}).get("verdict") == "agir"}


def changements_alertes(urgentes_cles, connues_actives) -> tuple[list[str], list[str]]:
    """(à notifier, à réarmer) : à notifier = urgentes pas encore actives ; à réarmer = actives qui ne sont plus urgentes
    (elles seront renotifiées si elles reviennent). Ordres stables (tri)."""
    urgentes_cles, connues_actives = set(urgentes_cles), set(connues_actives)
    return sorted(urgentes_cles - connues_actives), sorted(connues_actives - urgentes_cles)
