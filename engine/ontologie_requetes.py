"""Moteur de requêtes sur le graphe d'ontologie.

Toutes les fonctions prennent un graphe networkx déjà construit
(ontologie.construire_graphe) et retournent des données sérialisables
(dict/list, prêtes pour JsonResponse).

API disponible (voir views.py + urls.py) :
  POST api/ontologie/recherche/   — chercher des nœuds ou des relations
  POST api/ontologie/explorer/    — explorer depuis un nœud (voisins,
                                    chemins, récursion, stats locales)
"""
from __future__ import annotations
from engine.i18n import tf
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t

import math
from typing import Any


# ────────────────────────────────────────────────────────────────────────
# Helpers internes
# ────────────────────────────────────────────────────────────────────────

def _noeud_dict(nid: str, attrs: dict) -> dict:
    return {"id": nid, "type": attrs.get("type", "?"),
            "label": attrs.get("label", nid), "slug": attrs.get("slug", ""),
            "couleur": attrs.get("couleur", "#888888"),
            "taille": attrs.get("taille", 10), **{
                k: v for k, v in attrs.items()
                if k not in ("type", "label", "slug", "couleur", "taille")
                and isinstance(v, (str, int, float, bool, type(None)))
            }}


def _arete_dict(u: str, v: str, a: dict) -> dict:
    return {"source": u, "cible": v, "relation": a.get("relation", ""),
            "poids": a.get("poids", 1), "couleur": a.get("couleur", "#888888"),
            "cle": f"{u}→{v}:{a.get('relation', '')}"}


def _match_texte(valeur: Any, terme: str) -> bool:
    """Correspondance insensible à la casse sur str(valeur)."""
    return terme.lower() in str(valeur).lower()


# ────────────────────────────────────────────────────────────────────────
# 1. RECHERCHE — nœuds et/ou relations
# ────────────────────────────────────────────────────────────────────────

def rechercher(graphe, params: dict) -> dict:
    """Recherche multi-critères dans le graphe.

    Paramètres (tous optionnels, combinés en ET) :
      texte        str   — recherche dans label, slug, et tous les attrs str
      types        list  — types de nœuds à inclure (ex. ["table", "champ"])
      relations    list  — types de relations à inclure (arêtes)
      attribut     str   — nom d'un attribut à tester
      valeur       any   — valeur à rechercher dans cet attribut
      valeur_min   num   — valeur minimale (attribut numérique)
      valeur_max   num   — valeur maximale (attribut numérique)
      limit        int   — max de résultats (défaut 200)
      cible        str   — "noeuds" | "aretes" | "tout" (défaut "tout")
    """
    terme = (params.get("texte") or "").strip()
    types = set(params.get("types") or [])
    relations = set(params.get("relations") or [])
    attr_cle = params.get("attribut")
    attr_val = params.get("valeur")
    attr_min = params.get("valeur_min")
    attr_max = params.get("valeur_max")
    limit = min(int(params.get("limit") or 200), 1000)
    cible = params.get("cible") or "tout"

    noeuds_res, aretes_res = [], []

    # — Nœuds —
    if cible in ("noeuds", "tout"):
        for nid, attrs in graphe.nodes(data=True):
            if types and attrs.get("type") not in types:
                continue
            if terme:
                haystack = " ".join(str(v) for v in [nid, attrs.get("label", ""), attrs.get("slug", "")]
                                    + [v for v in attrs.values() if isinstance(v, str)])
                if not _match_texte(haystack, terme):
                    continue
            if attr_cle:
                v = attrs.get(attr_cle)
                if v is None:
                    continue
                if attr_val is not None and not _match_texte(v, str(attr_val)):
                    continue
                if attr_min is not None:
                    try:
                        if float(v) < float(attr_min):
                            continue
                    except (TypeError, ValueError):
                        continue
                if attr_max is not None:
                    try:
                        if float(v) > float(attr_max):
                            continue
                    except (TypeError, ValueError):
                        continue
            noeuds_res.append(_noeud_dict(nid, attrs))
            if len(noeuds_res) >= limit:
                break

    # — Arêtes —
    if cible in ("aretes", "tout"):
        for u, v, a in graphe.edges(data=True):
            rel = a.get("relation", "")
            if relations and rel not in relations:
                continue
            if terme and not (_match_texte(u, terme) or _match_texte(v, terme) or _match_texte(rel, terme)):
                continue
            if attr_cle:
                av = a.get(attr_cle)
                if av is None:
                    continue
                if attr_val is not None and not _match_texte(av, str(attr_val)):
                    continue
            aretes_res.append(_arete_dict(u, v, a))
            if len(aretes_res) >= limit:
                break

    return {
        "noeuds": noeuds_res,
        "aretes": aretes_res,
        "total_noeuds": len(noeuds_res),
        "total_aretes": len(aretes_res),
    }


# ────────────────────────────────────────────────────────────────────────
# 2. EXPLORATION depuis un nœud
# ────────────────────────────────────────────────────────────────────────

def explorer(graphe, noeud_id: str, params: dict) -> dict:
    """Exploration du graphe depuis un nœud central.

    Paramètres :
      mode          str   — "voisins" | "chemins" | "recursion" | "stats"
                            (défaut "voisins")
      profondeur    int   — nombre de sauts (défaut 1, max 10)
      direction     str   — "sortant" | "entrant" | "tout" (défaut "tout")
      relations     list  — filtrer sur ces types de relations
      types         list  — filtrer les nœuds résultants par type
      cible_id      str   — nœud destination (mode "chemins" seulement)
      max_chemins   int   — nombre max de chemins simples (défaut 5, max 20)
      tri           str   — "label" | "type" | "degre" (défaut "label")
      limit         int   — max nœuds retournés (défaut 200)
      min_degre     int   — degré minimum pour inclure un voisin
      max_degre     int   — degré maximum
    """
    if noeud_id not in graphe:
        return {"erreur": tf("engine/ontologie_requetes.py:g:155:27", v1=noeud_id)}

    mode = params.get("mode") or "voisins"
    profondeur = max(1, min(int(params.get("profondeur") or 1), 10))
    direction = params.get("direction") or "tout"
    relations_filtre = set(params.get("relations") or [])
    types_filtre = set(params.get("types") or [])
    limit = min(int(params.get("limit") or 200), 1000)
    tri = params.get("tri") or "label"
    min_degre = params.get("min_degre")
    max_degre = params.get("max_degre")

    if mode == "voisins":
        return _explorer_voisins(graphe, noeud_id, profondeur, direction,
                                  relations_filtre, types_filtre, limit, tri,
                                  min_degre, max_degre)
    if mode == "chemins":
        cible_id = params.get("cible_id") or ""
        max_chemins = min(int(params.get("max_chemins") or 5), 20)
        return _explorer_chemins(graphe, noeud_id, cible_id, profondeur, max_chemins)
    if mode == "recursion":
        return _explorer_recursion(graphe, noeud_id, profondeur, direction,
                                    relations_filtre, types_filtre, limit)
    if mode == "stats":
        return _stats_locale(graphe, noeud_id, profondeur)
    return {"erreur": _gtf("engine/ontologie_requetes.py:181:23", v1=(mode))}


# ── 2a. Voisins directs ou à N sauts ────────────────────────────────────

def _voisins_a_saut(graphe, nid, direction, relations_filtre):
    """Retourne les (voisin, relation, sens) accessibles depuis nid en 1 saut."""
    resultats = []
    if direction in ("sortant", "tout"):
        for _, v, a in graphe.out_edges(nid, data=True):
            rel = a.get("relation", "")
            if relations_filtre and rel not in relations_filtre:
                continue
            resultats.append((v, rel, "sortant"))
    if direction in ("entrant", "tout"):
        for u, _, a in graphe.in_edges(nid, data=True):
            rel = a.get("relation", "")
            if relations_filtre and rel not in relations_filtre:
                continue
            resultats.append((u, rel, "entrant"))
    return resultats


def _explorer_voisins(graphe, noeud_id, profondeur, direction,
                       relations_filtre, types_filtre, limit, tri,
                       min_degre, max_degre) -> dict:
    """BFS jusqu'à `profondeur` sauts ; retourne nœuds + arêtes du sous-graphe."""
    visites = {noeud_id: 0}   # nid → distance
    frontiere = [noeud_id]
    aretes_collectees = []

    for saut in range(1, profondeur + 1):
        prochaine = []
        for nid in frontiere:
            for voisin, rel, sens in _voisins_a_saut(graphe, nid, direction, relations_filtre):
                if voisin not in visites:
                    visites[voisin] = saut
                    prochaine.append(voisin)
                # Arête (toujours dans le sens original du graphe)
                u, v = (nid, voisin) if sens == "sortant" else (voisin, nid)
                cle = f"{u}→{v}:{rel}"
                if not any(a["cle"] == cle for a in aretes_collectees):
                    if graphe.has_edge(u, v):
                        aretes_collectees.append(_arete_dict(u, v, graphe[u][v]))
        frontiere = prochaine
        if not frontiere or len(visites) >= limit:
            break

    # Filtrer par type et degré
    noeuds_res = []
    non_oriente = graphe.to_undirected()
    for nid, dist in visites.items():
        attrs = graphe.nodes[nid]
        if types_filtre and attrs.get("type") not in types_filtre:
            continue
        deg = non_oriente.degree(nid)
        if min_degre is not None and deg < int(min_degre):
            continue
        if max_degre is not None and deg > int(max_degre):
            continue
        d = _noeud_dict(nid, attrs)
        d["distance"] = dist
        d["degre"] = deg
        noeuds_res.append(d)

    # Tri
    if tri == "type":
        noeuds_res.sort(key=lambda n: (str(n["type"]), str(n["label"])))
    elif tri == "degre":
        noeuds_res.sort(key=lambda n: -n["degre"])
    else:
        noeuds_res.sort(key=lambda n: (n["distance"], str(n["label"])))   # str() : un label None/int/texte paresseux ne doit pas lever TypeError

    return {
        "noeud_central": _noeud_dict(noeud_id, graphe.nodes[noeud_id]),
        "noeuds": noeuds_res[:limit],
        "aretes": aretes_collectees,
        "profondeur_reelle": max((n["distance"] for n in noeuds_res), default=0),
        "total": len(noeuds_res),
    }


# ── 2b. Chemins entre deux nœuds ────────────────────────────────────────

def _explorer_chemins(graphe, source: str, cible: str, profondeur: int, max_chemins: int) -> dict:
    """Tous les chemins simples de source → cible (longueur ≤ profondeur)."""
    if not cible:
        return {"erreur": t("engine/ontologie_requetes.py:266:28")}
    if cible not in graphe:
        return {"erreur": tf("engine/ontologie_requetes.py:g:269:27", v1=cible)}

    import networkx as nx
    try:
        chemins_bruts = list(nx.all_simple_paths(graphe, source, cible, cutoff=profondeur))
    except nx.NetworkXNoPath:
        chemins_bruts = []
    except Exception:
        # Graphe non-orienté : on tente sur la version non-orientée
        try:
            chemins_bruts = list(nx.all_simple_paths(
                graphe.to_undirected(), source, cible, cutoff=profondeur))
        except Exception:
            chemins_bruts = []

    chemins_bruts = chemins_bruts[:max_chemins]
    chemins = []
    for chemin in chemins_bruts:
        noeuds_c = [_noeud_dict(n, graphe.nodes[n]) for n in chemin]
        aretes_c = []
        for i in range(len(chemin) - 1):
            u, v = chemin[i], chemin[i + 1]
            if graphe.has_edge(u, v):
                aretes_c.append(_arete_dict(u, v, graphe[u][v]))
            elif graphe.has_edge(v, u):
                aretes_c.append(_arete_dict(v, u, graphe[v][u]))
        chemins.append({"noeuds": noeuds_c, "aretes": aretes_c, "longueur": len(chemin) - 1})

    # Plus court chemin
    plus_court = None
    try:
        import networkx as nx
        ps = nx.shortest_path(graphe, source, cible)
        plus_court = len(ps) - 1
    except Exception:
        try:
            ps = nx.shortest_path(graphe.to_undirected(), source, cible)
            plus_court = len(ps) - 1
        except Exception:
            pass

    return {
        "source": _noeud_dict(source, graphe.nodes[source]),
        "cible": _noeud_dict(cible, graphe.nodes[cible]),
        "chemins": chemins,
        "total": len(chemins),
        "plus_court": plus_court,
    }


# ── 2c. Récursion — sous-graphe complet atteignable ─────────────────────

def _explorer_recursion(graphe, noeud_id, profondeur, direction,
                         relations_filtre, types_filtre, limit) -> dict:
    """DFS récursif : collecte TOUS les nœuds atteignables jusqu'à
    `profondeur` sauts, avec leur arbre d'appel complet (parents/enfants).
    Utile pour voir la fermeture transitive d'un nœud."""
    arbre = {}   # nid → {profondeur, parent, enfants}

    def _dfs(nid, prof, parent):
        if nid in arbre or prof > profondeur or len(arbre) >= limit:
            return
        arbre[nid] = {"profondeur": prof, "parent": parent, "enfants": []}
        if parent and parent in arbre:
            arbre[parent]["enfants"].append(nid)
        for voisin, rel, _ in _voisins_a_saut(graphe, nid, direction, relations_filtre):
            _dfs(voisin, prof + 1, nid)

    _dfs(noeud_id, 0, None)

    noeuds_res = []
    for nid, meta in arbre.items():
        attrs = graphe.nodes[nid]
        if types_filtre and attrs.get("type") not in types_filtre:
            continue
        d = _noeud_dict(nid, attrs)
        d.update(meta)
        noeuds_res.append(d)

    # Arêtes internes au sous-graphe
    nids = set(arbre)
    aretes_res = [_arete_dict(u, v, a)
                  for u, v, a in graphe.edges(data=True)
                  if u in nids and v in nids]

    return {
        "noeud_racine": _noeud_dict(noeud_id, graphe.nodes[noeud_id]),
        "noeuds": noeuds_res,
        "aretes": aretes_res,
        "profondeur_max": profondeur,
        "total": len(noeuds_res),
        "tronque": len(arbre) >= limit,
    }


# ── 2d. Stats locales autour d'un nœud ──────────────────────────────────

def _stats_locale(graphe, noeud_id, profondeur) -> dict:
    """Statistiques topologiques centrées sur un nœud :
    degrés, types des voisins, relations entrantes/sortantes, centralité."""
    import networkx as nx

    attrs = graphe.nodes[noeud_id]
    non_oriente = graphe.to_undirected()

    # Voisinage direct
    successeurs = list(graphe.successors(noeud_id))
    predecesseurs = list(graphe.predecessors(noeud_id))

    # Types des voisins
    types_voisins: dict[str, int] = {}
    for nid in successeurs + predecesseurs:
        t = graphe.nodes[nid].get("type", "?")
        types_voisins[t] = types_voisins.get(t, 0) + 1

    # Relations sortantes / entrantes
    rels_sortantes: dict[str, int] = {}
    for _, _, a in graphe.out_edges(noeud_id, data=True):
        r = a.get("relation", "?")
        rels_sortantes[r] = rels_sortantes.get(r, 0) + 1
    rels_entrantes: dict[str, int] = {}
    for _, _, a in graphe.in_edges(noeud_id, data=True):
        r = a.get("relation", "?")
        rels_entrantes[r] = rels_entrantes.get(r, 0) + 1

    # Centralité (sur la composante contenant le nœud, pour rester rapide)
    composantes = list(nx.connected_components(non_oriente))
    comp = next((c for c in composantes if noeud_id in c), {noeud_id})
    sous_g = non_oriente.subgraph(comp)
    centralite = {}
    if len(sous_g) <= 2000:   # skip sur les très gros graphes
        try:
            centralite["degre"] = round(nx.degree_centrality(sous_g).get(noeud_id, 0), 4)
        except Exception:
            pass
        try:
            centralite["betweenness"] = round(
                nx.betweenness_centrality(sous_g, k=min(50, len(sous_g))).get(noeud_id, 0), 4)
        except Exception:
            pass
        try:
            centralite["closeness"] = round(nx.closeness_centrality(sous_g, noeud_id), 4)
        except Exception:
            pass

    # Voisinage élargi à `profondeur` sauts (comptage uniquement)
    visites = {noeud_id}
    frontiere = [noeud_id]
    par_saut = {}
    for saut in range(1, profondeur + 1):
        prochaine = []
        for nid in frontiere:
            for v in list(graphe.successors(nid)) + list(graphe.predecessors(nid)):
                if v not in visites:
                    visites.add(v)
                    prochaine.append(v)
        par_saut[saut] = len(prochaine)
        frontiere = prochaine
        if not frontiere:
            break

    return {
        "noeud": _noeud_dict(noeud_id, attrs),
        "degre_sortant": graphe.out_degree(noeud_id),
        "degre_entrant": graphe.in_degree(noeud_id),
        "degre_total": non_oriente.degree(noeud_id),
        "types_voisins": dict(sorted(types_voisins.items(), key=lambda x: -x[1])),
        "relations_sortantes": dict(sorted(rels_sortantes.items(), key=lambda x: -x[1])),
        "relations_entrantes": dict(sorted(rels_entrantes.items(), key=lambda x: -x[1])),
        "centralite": centralite,
        "voisins_par_saut": par_saut,
        "taille_composante": len(comp),
    }


# ────────────────────────────────────────────────────────────────────────
# 3. STATS GLOBALES du graphe
# ────────────────────────────────────────────────────────────────────────

def stats_globales(graphe) -> dict:
    """Distribution des types de nœuds et de relations, top nœuds par
    degré, densité, diamètre estimé — complète ontologie.metriques()
    avec les détails utiles pour l'exploration."""
    import networkx as nx

    n = graphe.number_of_nodes()
    m = graphe.number_of_edges()

    # Distribution types nœuds
    types_noeuds: dict[str, int] = {}
    for _, a in graphe.nodes(data=True):
        t = a.get("type", "?")
        types_noeuds[t] = types_noeuds.get(t, 0) + 1

    # Distribution types relations
    types_relations: dict[str, int] = {}
    for _, _, a in graphe.edges(data=True):
        r = a.get("relation", "?")
        types_relations[r] = types_relations.get(r, 0) + 1

    # Top 20 nœuds par degré
    non_oriente = graphe.to_undirected()
    top_noeuds = sorted(
        [{"id": nid, "label": graphe.nodes[nid].get("label", nid),
          "type": graphe.nodes[nid].get("type", "?"),
          "degre": non_oriente.degree(nid)}
         for nid in graphe.nodes()],
        key=lambda x: -x["degre"]
    )[:20]

    # Composantes connexes
    composantes = list(nx.connected_components(non_oriente))
    taille_composantes = sorted([len(c) for c in composantes], reverse=True)[:10]

    res = {
        "noeuds": n, "aretes": m,
        "types_noeuds": dict(sorted(types_noeuds.items(), key=lambda x: -x[1])),
        "types_relations": dict(sorted(types_relations.items(), key=lambda x: -x[1])),
        "top_noeuds_par_degre": top_noeuds,
        "nb_composantes": len(composantes),
        "tailles_composantes": taille_composantes,
        "densite": round(nx.density(graphe), 6) if n > 1 else 0,
    }
    if composantes and max(len(c) for c in composantes) <= 500:
        try:
            sg = non_oriente.subgraph(max(composantes, key=len))
            res["diametre"] = nx.diameter(sg)
        except Exception:
            res["diametre"] = None
    return res


# ────────────────────────────────────────────────────────────────────────
# 4. LIGNÉE bout-en-bout (cahier des charges Ontology, section 7 —
#    « d'où vient cette valeur ? »)
# ────────────────────────────────────────────────────────────────────────
# Chaque maillon (import, calcul, KPI, dashboard, document) est déjà TRACÉ
# dans le graphe (ontologie.construire_graphe : arêtes "calcule", "mesure",
# "affiche", "produit"/"genere", provenance sur le nœud "table" — voir
# ontologie._provenance_table). Ce qui manquait : une requête UNIQUE qui
# remonte (amont, "d'où ça vient") et descend (aval, "où ça sert") toute la
# chaîne depuis un nœud donné, sans que l'appelant ait à enchaîner
# lui-même plusieurs appels à `explorer`. N'introduit aucun nouveau moteur :
# c'est un simple parcours (BFS) du graphe déjà construit.

# Ordre canonique d'affichage de la chaîne (le graphe reste la source de
# vérité ; cet ordre ne sert qu'à présenter les maillons trouvés dans un
# sens lisible quand plusieurs types se mélangent à la même distance).
_ORDRE_CHAINE = (
    "connexion_externe", "import_donnees", "table", "champ", "calcul",
    "modele_ia", "kpi", "dashboard", "skill", "document", "rapport",
)


def _rang_chaine(type_noeud: str) -> int:
    try:
        return _ORDRE_CHAINE.index(type_noeud)
    except ValueError:
        return len(_ORDRE_CHAINE)


def _parcours_lignee(graphe, noeud_id: str, sens: str, limite: int) -> list[dict]:
    """BFS dans un seul sens ("amont" = predecesseurs, "aval" = successeurs)
    depuis `noeud_id`, retourne les nœuds atteints (hors noeud_id lui-même)
    avec leur distance et la relation qui les relie à leur parent le plus
    proche dans ce parcours."""
    voisins = graphe.predecessors if sens == "amont" else graphe.successors
    vus = {noeud_id: 0}
    resultat = []
    frontiere = [noeud_id]
    distance = 0
    while frontiere and len(resultat) < limite:
        distance += 1
        suivante = []
        for n in frontiere:
            for v in voisins(n):
                if v in vus:
                    continue
                vus[v] = distance
                attrs = graphe.nodes[v]
                relation = (
                    graphe.edges[v, n].get("relation") if sens == "amont"
                    else graphe.edges[n, v].get("relation")
                )
                resultat.append(_noeud_dict(v, attrs) | {
                    "distance": distance, "relation": relation, "depuis": n,
                })
                suivante.append(v)
                if len(resultat) >= limite:
                    break
            if len(resultat) >= limite:
                break
        frontiere = suivante
    return resultat


def tracer_lineage(graphe, noeud_id: str, limite: int = 100) -> dict:
    """Point d'entrée unique pour « d'où vient cette valeur ? » / « à quoi
    sert-elle ensuite ? ». Retourne :
      {noeud, amont: [...], aval: [...], chaine: [...]}
    - amont/aval : tous les ancêtres/descendants atteignables, triés par
      distance croissante (chacun avec sa relation d'un maillon au suivant).
    - chaine : la même information mise à plat dans l'ordre de lecture
      "origine → ... → noeud → ... → usages", pratique pour un simple
      affichage texte ou PDF (voir reports.py) sans recalcul côté appelant.

    Limite documentée (comme ontologie_fiche.assembler_fiche_objet) : un
    « rapport » (models.RapportGenere) n'est aujourd'hui rattaché qu'à une
    DemandeAgent en texte libre, pas structurellement à une table/un KPI
    précis — il n'apparaît donc dans la chaîne que via ses documents liés
    (DocumentGenere, déjà graphés), pas comme un maillon "rapport" à part
    entière tant que ce lien direct n'existe pas.
    """
    if noeud_id not in graphe:
        return {"erreur": t("engine/ontologie_requetes.py:582:28")}

    amont = _parcours_lignee(graphe, noeud_id, "amont", limite)
    aval = _parcours_lignee(graphe, noeud_id, "aval", limite)

    # Chaîne lisible : amont trié du plus lointain au plus proche (ordre de
    # lecture "origine d'abord"), puis le nœud central, puis aval du plus
    # proche au plus lointain — avec l'ordre canonique des types en
    # départage pour les nœuds à égale distance.
    amont_tries = sorted(amont, key=lambda n: (-n["distance"], _rang_chaine(n["type"])))
    aval_tries = sorted(aval, key=lambda n: (n["distance"], _rang_chaine(n["type"])))
    noeud_central = _noeud_dict(noeud_id, dict(graphe.nodes[noeud_id]))

    return {
        "noeud": noeud_central,
        "amont": amont, "aval": aval,
        "chaine": [
            {"id": n["id"], "type": n["type"], "label": n["label"], "relation": n["relation"]}
            for n in amont_tries
        ] + [
            {"id": noeud_central["id"], "type": noeud_central["type"], "label": noeud_central["label"], "relation": None}
        ] + [
            {"id": n["id"], "type": n["type"], "label": n["label"], "relation": n["relation"]}
            for n in aval_tries
        ],
    }
