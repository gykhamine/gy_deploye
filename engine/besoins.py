"""BESOINS : le moteur trouve LUI-MÊME les options, à partir des 33 critères mesurés sur les données existantes.

Les recommandations (engine/recommandations.py) sont 33 règles, chacune un CRITÈRE mesuré sur une colonne, une table ou un KPI
(manque, surplus, tendance, diminution, concentration, anomalie, obsolescence, isolement, objectif, intégrité, dysfonctionnement,
couverture). Le risque n'est qu'un critère parmi d'autres : ici, tous les critères qui relèvent quelque chose engendrent des options.

Le client peut désigner les objets qui comptent pour lui (une table, un KPI, un dashboard, un modèle IA) : le périmètre est alors
ces objets + ce qui les alimente en amont. Sans désignation, le périmètre est toute la toile de l'entreprise.

Pas de recommandation « pour le plaisir » : une option n'est générée que si un critère a MESURÉ quelque chose sur le périmètre.
Rien de mesuré => aucune option, et le verdict honnête est « rien à faire ».

Comment une option naît (tout est calculé, rien n'est écrit objet par objet) :
  * CIBLE — l'objet réellement mesuré : la COLONNE si le critère porte sur une colonne, sinon la table / le KPI.
  * LEVIER — traducteur inverse du critère (declaration_options.levier_de_critere) : un problème relève de son levier de catégorie,
    une opportunité (critère favorable : activité en hausse, KPI qui progresse) relève de « developper », jamais du risque.
  * UNE OPTION PAR (levier, cible) portant au moins un critère relevé ; `criteres` liste les codes de règles concernés.
  * EFFORT — 1 + log2(1 + nombre de critères), +1 si au moins un n'est pas une action lançable (décision manuelle), borné à 1..5.
  * Au plus `max_options` options, les plus pesantes d'abord (probabilité × impact, combinés « ou bruité »).
Module PUR (aucun import Django) : testable avec des dictionnaires.
"""
from __future__ import annotations
from engine.i18n import t, tf

import math
from collections import defaultdict

from . import declaration_options as _decl
from . import decision as _d

PARAMETRES = {"profondeur_amont": 2, "max_options": 8, "max_besoin": 10}


def _parents(graphe: dict) -> dict[str, list[str]]:
    parents: dict[str, list[str]] = defaultdict(list)
    for a in graphe.get("aretes", []):
        parents[a["cible"]].append(a["source"])
    return parents


def _ramener(nid: str | None, graphe: dict) -> str | None:
    """Colonne -> sa table ; sinon le nœud lui-même ; None s'il n'existe pas dans la toile."""
    noeud = graphe.get("noeuds", {}).get(nid or "")
    if noeud is None:
        return None
    if noeud.get("type") == "champ":
        cible = f"table:{noeud.get('table_id')}"
        return cible if cible in graphe["noeuds"] else None
    return nid


def perimetre(besoin: list[str], graphe: dict, profondeur: int) -> set[str]:
    """Objets du besoin + leurs sources en amont (BFS borné)."""
    parents, vus, courant = _parents(graphe), set(besoin), set(besoin)
    for _ in range(profondeur):
        courant = {s for n in courant for s in parents.get(n, ()) if s not in vus}
        vus |= courant
    return {n for n in (_ramener(x, graphe) for x in vus) if n}


def _effort(nb_problemes: int, tout_lancable: bool) -> int:
    return min(5, max(1, 1 + round(math.log2(1 + nb_problemes)) + (0 if tout_lancable else 1)))


def proposer(besoin, etat: dict, graphe: dict, resultat_reco: dict, parametres: dict | None = None) -> tuple[list[dict], list[str]]:
    """Besoin (liste d'objets « type:id », facultative) -> (options déclarées, erreurs). Options vides (sans erreur) = rien à faire."""
    p = {**PARAMETRES, **(parametres or {})}
    noeuds = graphe.get("noeuds", {})
    if besoin is not None and not isinstance(besoin, list):
        return [], [t("engine/besoins.py:g:69:21")]
    retenus = []
    for b in (besoin or [])[: p["max_besoin"]]:
        nid = str(b)
        if nid not in noeuds:
            return [], [tf("engine/besoins.py:g:74:25", v1=nid[:40])]
        retenus.append(nid)
    zone = perimetre(retenus, graphe, p["profondeur_amont"]) if retenus else None   # None = toute la toile

    recos = resultat_reco.get("recommandations", [])
    risques = _d.evaluer_risques(recos, _d.comprendre(etat, graphe), graphe)
    groupes: dict[tuple[str, str], list[tuple[dict, dict]]] = defaultdict(list)
    for r, k in zip(recos, risques):
        objet, levier = k.get("objet"), _decl.levier_de_critere(r.get("code"), r.get("categorie"))
        if objet in noeuds and levier and k["risque"] > 0 and (zone is None or _ramener(objet, graphe) in zone):
            groupes[(objet, levier)].append((r, k))

    options = []
    for (objet, levier), lot in groupes.items():
        poids = _d._ou_bruite([k["risque"] for _r, k in lot])
        options.append((poids, {
            "libelle": f"{_decl.libelle_court(levier)} — {noeuds[objet].get('label') or objet}"[: _decl.MAX_LIBELLE],
            "levier": levier, "cibles": [objet], "criteres": sorted({r["code"] for r, _k in lot}),
            "effort": _effort(len(lot), all(r.get("type") == "action" for r, _k in lot))}))
    options.sort(key=lambda o: (-o[0], o[1]["libelle"]))
    return [o for _r, o in options[: p["max_options"]]], []


def bilan_criteres(resultat_reco: dict) -> dict:
    """Couverture des critères : combien des règles connues ont relevé quelque chose (les autres ont mesuré, sans rien à signaler)."""
    from . import recommandations as _rec
    connus = [c["code"] for c in _rec.catalogue()]
    releves = {r.get("code") for r in resultat_reco.get("recommandations", [])}
    return {"evalues": len(connus), "releves": len([c for c in connus if c in releves]),
            "sans_signal": [c for c in connus if c not in releves]}
