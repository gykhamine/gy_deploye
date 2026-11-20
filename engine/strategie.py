"""DÉCISION STRATÉGIQUE : arbitre entre les OPTIONS de l'utilisateur, à sa place.

Le moteur de décision (engine/decision.py) dit quoi traiter d'abord ; celui-ci répond à une autre question :
« parmi MES options, laquelle choisirais-je si j'étais à la place de l'utilisateur ? ». Rien n'est écrit
option par option : tout est calculé depuis l'état fusionné (engine/fusion.py), la toile de dépendances et
les risques déjà estimés par decision.py (aucune seconde implémentation, on réutilise ses outils).

Pour chaque option DÉCLARÉE (libellé, levier, effort 1-5, objets visés dans la toile — engine/declaration_options.py ; le libellé
est du texte pour l'utilisateur, jamais interprété ; le levier dit quelles catégories de risques l'option traite réellement) :
  bénéfice   = w_risque × (risque déjà présent sur ses objets, combiné « ou bruité »)
             + w_potentiel × (importance des objets dans la toile × élan récent d'activité)
  confiance  = qualité des données visées (remplissage × fraîcheur) et capacité à MESURER l'effet (KPI)
  coût       = effort ÷ capacité de gestion actuelle (elle baisse quand le risque global monte)
  utilité    = bénéfice × confiance − λ × coût ;  « ne rien faire » = utilité 0 (référence).
Les pondérations w, λ, l'effort réel et la confiance sont INCERTAINS : on les tire au hasard (graine fixe
tirée des options, donc résultat reproductible) sur `simulations` scénarios. Quand un MODÈLE FANTÔME est fourni
(engine/modele_fantome.py : réseau de neurones fine-tuné sur les conséquences mesurées des décisions passées et sur toute la
structure de la plateforme), c'est lui qui donne l'utilité (tête de récompense tirée au hasard parmi 4 + bruit d'exploration) :
sans modèle, la formule ci-dessus sert de référence. La recommandation est l'option
qui arrive en tête le plus souvent (probabilité, regret, intervalle 10-90 %), pas celle d'un calcul unique.

Module PUR (aucun import Django, aucun accès base) : testable avec des dictionnaires.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t, tf, tl
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7

import json
import math
import random
import statistics
import zlib

from . import declaration_options as _decl
from . import decision as _d
from . import donnees_entrainement as _dn
from . import modele_fantome as _mf
from . import reglages_criteres as _rc
from engine.i18n import tf as _gtf  # noqa: E402

PARAMETRES = {
    "simulations": 500,
    "poids": (0.6, 0.4),          # centre du tirage (risque traité, potentiel)
    "dispersion": 20,             # concentration du tirage des poids (grand = peu de variation)
    "lambda": (0.1, 0.4),         # poids du coût, tirage uniforme
    "derive_effort": (0.9, 1.5),  # un effort est rarement surestimé : facteur tiré sur cet intervalle
    "confiance_defaut": 0.25,     # option non reliée aux données : évaluation prudente
    "fraicheur_jours": 30,
    "capacite_min": 0.25,
    "seuil_dominance": 0.5,       # probabilité d'être première pour parler de choix net
    "ecart_net": 0.15,            # écart de probabilité avec la 2e pour parler de choix net
    "max_options": 8, "max_cibles": 10,
}
_EFFORTS = {1: tl("engine/strategie.py:g:51:16"), 2: _gtfr("engine/strategie.py:53:54"), 3: "moyen", 4: "lourd", 5: tl("engine/strategie.py:g:51:69")}
_PREPARATIFS = {"manque", "obsolescence", "anomalie", "integrite"}
_ATTENTE = "__attente__"


# ── Entrée ──────────────────────────────────────────────────────────────────
def normaliser_options(options, graphe: dict, parametres: dict | None = None) -> tuple[list[dict], list[str]]:
    """Options déclarées -> [{nom (= libellé), levier, effort, cibles:[noeud], ignorees:[texte]}] + erreurs bloquantes.
    Le libellé est du texte pour l'utilisateur, jamais interprété ; le levier (vocabulaire fermé, engine/declaration_options.py)
    et au moins une cible reconnue dans la toile sont IMPOSÉS."""
    p = {**PARAMETRES, **(parametres or {})}
    if not isinstance(options, list) or not options:
        return [], [_i18n_t("engine/strategie.py:g:63:21")]
    if len(options) > p["max_options"]:
        return [], [tf("engine/strategie.py:g:65:21", v1=p['max_options'])]
    propres, noms = [], set()
    for i, o in enumerate(options, 1):
        opt, erreur = _decl.traduire(o, i)
        if erreur:
            return [], [erreur]
        nom = opt["nom"]
        if nom.lower() in noms:
            return [], [tf("engine/strategie.py:g:73:25", v1=nom)]
        noms.add(nom.lower())
        cibles, ignorees = [], []
        for c in (opt.pop("cibles_brutes"))[: p["max_cibles"]]:
            nid = _d._noeud_cible(c if isinstance(c, dict) else dict(zip(("type", "id"), str(c).split(":", 1))), graphe)
            if nid and nid not in cibles:   # table, KPI, dashboard, modèle IA... ou COLONNE (« champ:<id> »)
                cibles.append(nid)
            elif not nid:
                ignorees.append(str(c)[:40])
        if not cibles:
            return [], [tf("engine/strategie.py:g:83:25", v1=nom)]
        criteres = [str(c)[:60] for c in (o.get("criteres") or [])[:40]] if isinstance(o, dict) and isinstance(o.get("criteres"), list) else []
        propres.append({**opt, "cibles": cibles, "ignorees": ignorees, "criteres": criteres})   # criteres : codes de règles, affichage seul
    return propres, []


# ── Mesures sur les données (réutilisent decision.py) ───────────────────────
def _table_de(nid: str, graphe: dict):
    n = graphe["noeuds"].get(nid, {})
    return n.get("table_id") if n.get("table_id") is not None else (n.get("id") if n.get("type") == "table" else None)


def _qualite(t: dict, ts: float | None, p: dict) -> float:
    if not t.get("nb_lignes"):
        return 0.0
    champs = t.get("champs") or []
    remplissage = statistics.fmean(c.get("remplissage", 0.0) for c in champs) if champs else 0.0
    derniere = t.get("derniere_ts")
    jours = (ts - derniere) / 86400 if (ts and derniere) else 365.0
    return remplissage * math.exp(-max(jours, 0.0) / p["fraicheur_jours"])


def _situation(etat: dict, graphe: dict, resultat_reco: dict, p: dict) -> dict:
    recos = resultat_reco.get("recommandations", [])
    profil = _d.comprendre(etat, graphe)
    risques = _d.evaluer_risques(recos, profil, graphe)
    objets = _d.risques_par_objet(recos, risques, {"top_objets": 10 ** 6})
    top = [o["risque_cumule"] for o in objets[:5]]
    risque_global = round(statistics.fmean(top), 1) if top else 0.0
    return {"profil": profil, "recos": recos, "risques": risques, "objets": objets, "risque_global": risque_global,
            "capacite": max(p["capacite_min"], 1 - risque_global / 100), "enfants": _d._enfants(graphe),
            "vue": _dn.Vue(etat, graphe, p.get("reglages_criteres"))}


def _mesurer(option: dict, etat: dict, graphe: dict, sit: dict, p: dict, modele: dict | None = None) -> dict:
    """Composantes 0..1 d'une option : risque traité, potentiel, confiance, coût de base + preuves."""
    tables = {t["id"]: t for t in etat.get("tables", [])}
    kpis = {k.get("table_id") for k in etat.get("kpis", [])}
    perimetre = set(option["cibles"])
    for nid in option["cibles"]:  # agir sur une table traite aussi ses propres colonnes
        if graphe["noeuds"][nid]["type"] == "table":
            perimetre |= {n for n, v in graphe["noeuds"].items() if v["type"] == "champ" and v.get("table_id") == graphe["noeuds"][nid]["id"]}
    cats = _decl.categories_du_levier(option["levier"]) if option.get("levier") else None   # traducteur du levier
    traites = [(r, k) for r, k in zip(sit["recos"], sit["risques"])
               if k["objet"] in perimetre and r.get("code") not in _decl.OPPORTUNITES and (cats is None or r.get("categorie") in cats)]
    risque = _d._ou_bruite([k["risque"] for _r, k in traites]) / 100

    potentiels, confiances, elans, tids = [], [], [], []
    for nid in option["cibles"]:
        tid = _table_de(nid, graphe)
        pr = sit["profil"]["tables"].get(tid)
        propagation = 1 - 1 / (1 + _d._poids_aval(_d._en_aval(nid, sit["enfants"], _d.PARAMETRES), _d.PARAMETRES))
        importance = 0.5 * (pr["importance"] if pr else 0.0) + 0.5 * propagation
        elan = _mf.elan(modele, tables[tid], sit["vue"]) if tid in tables else 0.0
        tids.append(tid)
        potentiels.append(importance * (0.5 + 0.5 * elan))
        elans.append(elan)
        mesurable = 1.0 if graphe["noeuds"][nid]["type"] == "kpi" or tid in kpis else 0.0
        confiances.append(0.6 * (_qualite(tables[tid], etat.get("ts"), p) if tid in tables else 0.5) + 0.4 * mesurable)
    relie = bool(option["cibles"])
    mesure = {"risque": risque, "potentiel": statistics.fmean(potentiels) if relie else 0.0,
              "confiance": statistics.fmean(confiances) if relie else p["confiance_defaut"],
              "cout": (0.2 + 0.8 * (option["effort"] - 1) / 4) / sit["capacite"], "relie": relie,
              "problemes": [r for r, _k in traites], "elan": statistics.fmean(elans) if elans else 0.0}
    mesure["phi"] = _mf.phi(mesure, mesure["elan"])
    mesure["x"] = sit["vue"].x_option(mesure["phi"], tids, option["cibles"])   # φ + structure + contexte + 33 critères (entrée du réseau)
    mesure["criteres_mesures"] = sit["vue"].criteres.detail(cibles=option["cibles"])
    mesure["apport_criteres"] = _mf.apport_criteres(mesure["x"][_dn.I_CRIT:], _rc.parametres_modele(p.get("reglages_criteres")))   # même utilité a priori que le noyau du réseau
    return mesure


def instantane(cibles: list[str], etat: dict, graphe: dict, resultat_reco: dict, parametres: dict | None = None) -> dict:
    """Photographie mesurable des objets visés (risque, activité, KPI) : le « avant » d'une décision et son « après »
    (comparés par modele_fantome.recompense). Même calcul aux deux instants, sur les mêmes cibles."""
    p = {**PARAMETRES, **(parametres or {})}
    cibles = [c for c in cibles if c in graphe.get("noeuds", {})]
    m = _mesurer({"cibles": cibles, "effort": 3}, etat, graphe, _situation(etat, graphe, resultat_reco, p), p)
    tables = {t["id"]: t for t in etat.get("tables", [])}
    ids = {_table_de(c, graphe) for c in cibles}
    activite = [tables[i].get("n7", 0) for i in ids if i in tables]
    return {"risque": round(m["risque"], 4), "activite": statistics.fmean(activite) if activite else None,
            "kpis": [{"id": k["id"], "valeur": k.get("valeur"), "cible": k.get("cible"), "sens": k.get("sens")}
                     for k in etat.get("kpis", []) if k.get("table_id") in ids or f"kpi:{k['id']}" in cibles]}


# ── Simulation ──────────────────────────────────────────────────────────────
def _tirer(rng: random.Random, mesures: list[dict], p: dict, echantillonneur=None) -> list[float]:
    """Un scénario : utilité de chaque option. Avec un modèle fantôme : une tête du réseau tirée au hasard (+ bruit résiduel) ;
    sinon pondérations, effort réel et confiance perturbés autour de la formule de référence."""
    if echantillonneur is not None:
        return echantillonneur.tirer(rng)
    g1, g2 = rng.gammavariate(p["dispersion"] * p["poids"][0], 1), rng.gammavariate(p["dispersion"] * p["poids"][1], 1)
    w_r, lam = g1 / (g1 + g2), rng.uniform(*p["lambda"])
    sorties = []
    for m in mesures:
        confiance = m["confiance"] * (1 - 0.3 * (1 - m["confiance"]) * rng.random())
        benefice = w_r * m["risque"] + (1 - w_r) * m["potentiel"] + m.get("apport_criteres", 0.0)
        sorties.append(benefice * confiance - lam * m["cout"] * rng.uniform(*p["derive_effort"]))
    return sorties


def _percentile(valeurs: list[float], q: float) -> float:
    v = sorted(valeurs)
    return v[min(len(v) - 1, int(q * len(v)))]


def _simuler(mesures: list[dict], graine: int, p: dict, echantillonneur=None) -> list[dict]:
    """Probabilité d'être première (« ne rien faire » = 0 inclus), utilité moyenne, intervalle 10-90 %, regret."""
    rng = random.Random(graine)
    n = len(mesures)
    victoires, utilites, regrets = [0] * (n + 1), [[] for _ in range(n)], [0.0] * (n + 1)
    for _ in range(p["simulations"]):
        u = _tirer(rng, mesures, p, echantillonneur) + [0.0]
        meilleur = max(u)
        victoires[u.index(meilleur)] += 1
        for i, v in enumerate(u):
            regrets[i] += meilleur - v
            if i < n:
                utilites[i].append(v)
    s = p["simulations"]
    return [{"proba_premiere": victoires[i] / s, "regret": regrets[i] / s,
             "utilite": statistics.fmean(utilites[i]) if i < n else 0.0,
             "utilite_p10": _percentile(utilites[i], 0.1) if i < n else 0.0,
             "utilite_p90": _percentile(utilites[i], 0.9) if i < n else 0.0} for i in range(n + 1)]


# ── Point d'entrée ──────────────────────────────────────────────────────────
def arbitrer(options, etat: dict, graphe: dict, resultat_reco: dict, parametres: dict | None = None,
             modele: dict | None = None) -> dict:
    """Recommandation stratégique : {verdict, recommandation, options (classées), a_ta_place, preparatifs,
    situation, avertissements, _trace}. `options` = liste brute saisie ; `resultat_reco` = sortie de recommandations.evaluer() ;
    `modele` = état du modèle fantôme (dict) ou None. `_trace` (par option : φ, vecteur d'entrée du réseau x, cibles, « avant ») est destiné à la mémoire
    de décision et ne doit pas être renvoyé au navigateur."""
    p = {**PARAMETRES, **(parametres or {})}
    opts, erreurs = normaliser_options(options, graphe, p)
    if erreurs:
        return {"erreurs": erreurs}
    sit = _situation(etat, graphe, resultat_reco, p)
    mesures = [_mesurer(o, etat, graphe, sit, p, modele) for o in opts]
    graine = zlib.crc32(json.dumps([[o["levier"], o["effort"], o["cibles"]] for o in opts], sort_keys=True).encode())
    stats = _simuler(mesures, graine, p, _mf.Echantillonneur(modele, [m["x"] for m in mesures]) if modele else None)

    classees = []
    for o, m, s in zip(opts, mesures, stats):
        classees.append({**o, "effort_libelle": _EFFORTS[o["effort"]], **{k: round(v, 3) for k, v in s.items()},
                         "composantes": {k: round(m[k], 3) for k in ("risque", "potentiel", "confiance", "cout")},
                         "relie_aux_donnees": m["relie"], "nb_problemes_traites": len(m["problemes"]),
                         "criteres_mesures": m["criteres_mesures"],
                         "problemes": [{"titre": r.get("titre"), "categorie": r.get("categorie")} for r in m["problemes"]][:5],
                         "_m": m})
    attente = stats[-1]
    classees.sort(key=lambda c: (-c["proba_premiere"], -c["utilite"]))
    tete = classees[0]
    deuxieme = classees[1] if len(classees) > 1 else None
    proba_tete = max(tete["proba_premiere"], attente["proba_premiere"])
    if attente["proba_premiere"] >= tete["proba_premiere"]:
        verdict = "attendre"
    elif tete["proba_premiere"] >= p["seuil_dominance"] and (tete["proba_premiere"] - (deuxieme or {"proba_premiere": 0})["proba_premiere"]) >= p["ecart_net"]:
        verdict = "choisir"
    elif deuxieme and tete["proba_premiere"] - deuxieme["proba_premiere"] < p["ecart_net"]:
        verdict = "arbitrage_serre"
    else:
        verdict = "choisir_avec_reserve"

    avert = []
    if len(etat.get("tables", [])) < 3:
        avert.append(_i18n_t("engine/strategie.py:g:249:22"))
    avert += [tf("engine/strategie.py:g:250:15", v1=c['nom'], v2=round(p['confiance_defaut'] * 100))
              for c in classees if not c["relie_aux_donnees"]]
    avert += [tf("engine/strategie.py:g:252:15", v1=c['nom'], v2=', '.join(c['ignorees'])) for c in classees if c["ignorees"]]

    a_ta_place = _raconter(verdict, tete, deuxieme, classees, attente, sit, p)
    preparatifs = [] if verdict == "attendre" else [
        {"titre": r.get("titre"), "categorie": r.get("categorie")} for r in tete["_m"]["problemes"] if r.get("categorie") in _PREPARATIFS][:3]
    trace = {c["nom"]: {"phi": [round(x, 6) for x in c["_m"]["phi"]], "x": [round(x, 6) for x in c["_m"]["x"]], "cibles": c["cibles"],
                        "avant": instantane(c["cibles"], etat, graphe, resultat_reco, p)} for c in classees}
    for c in classees:
        c.pop("_m")
    return {"_trace": trace,
        "verdict": verdict, "recommandation": None if verdict == "attendre" else tete["nom"],
        "probabilite": round(proba_tete, 2), "simulations": p["simulations"], "attendre": {k: round(v, 3) for k, v in attente.items()},
        "options": classees, "ordre": [c["nom"] for c in classees if c["utilite"] > 0],
        "a_ta_place": a_ta_place, "preparatifs": preparatifs, "avertissements": avert,
        "situation": {"risque_global": sit["risque_global"], "capacite": round(sit["capacite"], 2),
                      "objets_exposes": [{"nom": o["nom"], "risque_cumule": o["risque_cumule"]} for o in sit["objets"][:3]]},
    }


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


def _raconter(verdict, tete, deuxieme, classees, attente, sit, p) -> list[str]:
    """Le raisonnement en phrases, comme le tiendrait l'utilisateur à sa propre place."""
    s = p["simulations"]
    if verdict == "attendre":
        return [tf("engine/strategie.py:g:279:17", v1=_pct(attente['proba_premiere']), v2=s),
                tf("engine/strategie.py:g:280:17", v1=_pct(sit['capacite'])),
                _i18n_t("engine/strategie.py:g:282:17")]
    m, c = tete["composantes"], tete
    phrases = [tf("engine/strategie.py:g:284:16", v1=c['nom'], v2=_pct(c['proba_premiere']), v3=s)]
    if c["nb_problemes_traites"]:
        phrases.append(tf("engine/strategie.py:g:287:24", v1=c['nb_problemes_traites'], v2=_pct(m['risque'])))
    phrases.append(tf("engine/strategie.py:g:288:20", v1=_pct(m['potentiel']), v2=_pct(m['confiance']), v3=c['effort_libelle'], v4=_pct(sit['capacite']), v5=sit['risque_global']))
    if verdict == "arbitrage_serre" and deuxieme:
        ecarts = {k: abs(tete["composantes"][k] - deuxieme["composantes"][k]) for k in ("risque", "potentiel", "confiance", "cout")}
        libelles = {"risque": _i18n_t("engine/strategie.py:g:292:31"), "potentiel": _i18n_t("engine/strategie.py:g:292:85"),
                    "confiance": _i18n_t("engine/strategie.py:g:293:34"), "cout": _i18n_t("engine/strategie.py:g:293:70")}
        phrases.append(tf("engine/strategie.py:g:294:24", v1=deuxieme['nom'], v2=_pct(deuxieme['proba_premiere']), v3=libelles[max(ecarts, key=ecarts.get)]))
    elif verdict == "choisir_avec_reserve":
        phrases.append(_i18n_t("engine/strategie.py:g:297:24"))
    for autre in classees[1:]:
        m2 = autre["composantes"]
        faible = min(("risque", "potentiel", "confiance"), key=lambda k: m2[k] - m[k])
        motif = {"risque": _i18n_t("engine/strategie.py:g:301:28"), "potentiel": _i18n_t("engine/strategie.py:g:301:85"),
                 "confiance": _i18n_t("engine/strategie.py:g:302:31")}[faible]
        if m2["cout"] > m["cout"]:
            motif += _i18n_t("engine/strategie.py:g:304:22")
        phrases.append(_gtf("rg6.msg.110", v1=autre['nom'], v2=_pct(autre['proba_premiere']), v3=motif))
    if attente["proba_premiere"] >= 0.2:
        phrases.append(tf("engine/strategie.py:g:307:24", v1=_pct(attente['proba_premiere'])))
    return phrases
