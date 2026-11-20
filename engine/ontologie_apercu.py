"""engine/ontologie_apercu.py — APERÇU HIÉRARCHIQUE de l'ontologie.

Problème : la page d'ontologie sérialisait et dessinait TOUT le graphe. Au-delà de 10 000 (et plus encore 20 000) lignes elle devenait
lente — non pas un bug, un défaut de conception : une page ne peut pas tout afficher.

Principe :
  * L'ontologie complète continue de se construire en arrière-plan, inchangée (ontologie.consulter_graphe). Rien n'est supprimé ni
    ignoré dans l'état : seul l'AFFICHAGE est borné.
  * La page ne reçoit qu'un APERÇU : au plus LIMITE_APERCU nœuds et LIMITE_APERCU relations, pris en largeur d'abord (niveau par
    niveau) à partir d'une RACINE. Par défaut la racine est le nœud de l'entreprise ; la présentation est donc un arbre qu'on parcourt
    de branche en branche, au lieu d'avoir toute l'ontologie plaquée devant soi.
  * Chercher un nœud absent de l'aperçu le rend RACINE : les nœuds affichés (au plus LIMITE_APERCU) partent alors de lui.
  * Le fil d'Ariane (chemin entreprise → … → racine) dit où l'on se trouve dans l'ontologie.
  * TRACE CONTINUE : en suivant une trace, chaque nœud atteint devient la racine du nouvel aperçu, et les derniers nœuds de la trace (`inclure`,
    LIMITE_TRACE au plus) restent affichés avec leurs liens : la trace se poursuit de branche en branche sans jamais disparaître.
  * VOISINS COMPLETS : `voisins_complets` renvoie TOUS les voisins d'un nœud (même absents de l'aperçu, sans rendu) pour les options de navigation.

Aucune dépendance Django ici : uniquement networkx, donc testable sans base.
"""
import unicodedata
from collections import deque

import networkx as nx

LIMITE_APERCU = 200           # nœuds ET relations affichés, au maximum
LIMITE_CHEMIN = 12            # longueur maximale du fil d'Ariane renvoyé
LIMITE_RECHERCHE = 30         # résultats maximum d'une recherche de nœud
LIMITE_TRACE = 60             # derniers nœuds de la trace gardés dans l'aperçu quand on la poursuit (le reste de la trace reste mémorisé, sans rendu)
LIMITE_VOISINS = 500          # voisins renvoyés au maximum pour les options de navigation d'un nœud


def noeud_entreprise(graphe):
    """Identifiant du nœud « entreprise » (la base de l'arbre), ou None si le graphe n'en a pas."""
    for n, a in graphe.nodes(data=True):
        if a.get("type") == "entreprise":
            return n
    return None


def _voisins(graphe, n):
    """Voisins non orientés de `n`, dans un ordre stable (les mieux reliés d'abord : ils portent le plus de sens dans un aperçu tronqué)."""
    vus = set(graphe.successors(n)) | set(graphe.predecessors(n))
    vus.discard(n)
    return sorted(vus, key=lambda v: (-graphe.degree(v), str(v)))


def apercu_hierarchique(graphe, racine=None, limite=LIMITE_APERCU, inclure=None):
    """Retourne (sous_graphe, meta).

    sous_graphe : copie bornée du graphe (≤ `limite` nœuds, ≤ `limite` relations), chaque nœud annoté de `profondeur` (distance à la racine),
                  `parent` (id du nœud par lequel on y arrive, None pour la racine) et `nb_caches` (voisins non affichés).
    meta        : {racine, entreprise, total_noeuds, total_aretes, noeuds_affiches, aretes_affichees, tronque, chemin}.

    Les relations de l'arbre (parent → enfant) sont gardées en priorité ; les autres relations entre nœuds affichés complètent
    jusqu'à `limite`. Le graphe d'origine n'est jamais modifié.

    `inclure` : identifiants (ordre de la trace) à garder dans l'aperçu en plus du parcours en largeur — les LIMITE_TRACE derniers présents dans le
    graphe, avec les relations qui les relient entre eux et à la racine (prioritaires). Le total reste borné par `limite`.
    """
    limite = max(1, int(limite))
    entreprise = noeud_entreprise(graphe)
    if racine is None or racine not in graphe:
        racine = entreprise if entreprise is not None else next(iter(graphe.nodes), None)
    total_n, total_a = graphe.number_of_nodes(), graphe.number_of_edges()
    if racine is None:   # graphe vide
        return nx.DiGraph(), {"racine": None, "entreprise": entreprise, "total_noeuds": 0, "total_aretes": 0,
                              "noeuds_affiches": 0, "aretes_affichees": 0, "tronque": False, "chemin": []}

    # Nœuds de la trace à garder : leur place est réservée, le parcours en largeur prend le reste.
    inclus = [n for n in dict.fromkeys(inclure or []) if n in graphe and n != racine][-min(LIMITE_TRACE, max(0, limite - 1)):]

    # Parcours en largeur, niveau par niveau, jusqu'à `limite` nœuds (moins la place réservée à la trace).
    budget = max(1, limite - len(inclus))
    profondeur, parent = {racine: 0}, {racine: None}
    file = deque([racine])
    while file and len(profondeur) < budget:
        n = file.popleft()
        for v in _voisins(graphe, n):
            if v in profondeur:
                continue
            profondeur[v] = profondeur[n] + 1
            parent[v] = n
            file.append(v)
            if len(profondeur) >= budget:
                break
    for n in inclus:   # la trace : rattachée à un voisin déjà affiché si possible
        if n in profondeur:
            continue
        pere = next((v for v in _voisins(graphe, n) if v in profondeur), None)
        profondeur[n] = profondeur[pere] + 1 if pere is not None else 1
        parent[n] = pere

    gardes = set(profondeur)
    # Un graphe en plusieurs morceaux : les composantes non atteignables depuis la racine sont rattachées à la racine par la
    # pensée seulement si la place le permet (elles sont sinon accessibles par la recherche, qui change de racine).
    if len(gardes) < min(limite, total_n):
        for n in graphe.nodes:
            if n in gardes:
                continue
            if graphe.degree(n) == 0 and len(gardes) < limite:   # nœuds isolés : ils n'ont pas de parent possible, on les montre en bout de course
                profondeur[n], parent[n] = 1, None
                gardes.add(n)

    sous = nx.DiGraph()
    for n in gardes:
        a = dict(graphe.nodes[n])
        a["profondeur"], a["parent"] = profondeur[n], parent[n]
        sous.add_node(n, **a)

    # Relations : d'abord celles de l'arbre, puis les autres entre nœuds affichés, sans dépasser `limite`.
    cles_arbre = set()
    for n, p in parent.items():
        if p is None:
            continue
        if graphe.has_edge(p, n):
            cles_arbre.add((p, n))
        if graphe.has_edge(n, p):
            cles_arbre.add((n, p))
    if inclus:   # relations de la trace (entre ses nœuds et la racine) : gardées en priorité
        cle_trace = set(inclus) | {racine}
        for u, v in graphe.edges(cle_trace):
            if u in cle_trace and v in cle_trace:
                cles_arbre.add((u, v))
    nb = 0
    for u, v in sorted(cles_arbre, key=lambda e: (profondeur.get(e[0], 0) + profondeur.get(e[1], 0), str(e))):
        if nb >= limite:
            break
        sous.add_edge(u, v, **graphe.edges[u, v])
        nb += 1
    if nb < limite:
        for u, v, a in graphe.edges(data=True):
            if nb >= limite:
                break
            if u in gardes and v in gardes and not sous.has_edge(u, v):
                sous.add_edge(u, v, **a)
                nb += 1

    for n in gardes:
        cache = sum(1 for v in _voisins(graphe, n) if v not in gardes)
        sous.nodes[n]["nb_caches"] = cache

    meta = {
        "racine": racine, "entreprise": entreprise,
        "total_noeuds": total_n, "total_aretes": total_a,
        "noeuds_affiches": sous.number_of_nodes(), "aretes_affichees": sous.number_of_edges(),
        "tronque": sous.number_of_nodes() < total_n or sous.number_of_edges() < total_a,
        "chemin": chemin_depuis_entreprise(graphe, racine, entreprise),
    }
    return sous, meta


def voisins_complets(graphe, n, libelle=None, limite=LIMITE_VOISINS):
    """Options de navigation d'un nœud : TOUS ses voisins dans le graphe complet, y compris ceux qui ne sont pas dans l'aperçu (aucun rendu : seulement
    des identifiants, libellés, types, relations et sens). Retourne ([{id, label, type, relation, sens}], total) ; `sens` = « out » (n → voisin) ou
    « in » (voisin → n) ; les mieux reliés d'abord, au plus `limite` entrées, `total` = nombre réel de relations."""
    if n not in graphe:
        return [], 0
    libelle = libelle or (lambda attrs, i: attrs.get("label", i))
    liens = [(v, "out", graphe.edges[n, v].get("relation", "")) for v in graphe.successors(n) if v != n]
    liens += [(v, "in", graphe.edges[v, n].get("relation", "")) for v in graphe.predecessors(n) if v != n]
    liens.sort(key=lambda x: (-graphe.degree(x[0]), str(libelle(graphe.nodes[x[0]], x[0])).lower(), str(x[0]), x[1]))
    sortie = [{"id": v, "label": str(libelle(graphe.nodes[v], v)), "type": graphe.nodes[v].get("type", "?"), "relation": rel or "", "sens": sens}
              for v, sens, rel in liens[:max(1, int(limite))]]
    return sortie, len(liens)


def chemin_depuis_entreprise(graphe, racine, entreprise=None):
    """Identifiants du fil d'Ariane : entreprise → … → racine (plus court chemin non orienté), tronqué aux LIMITE_CHEMIN derniers.
    Liste [racine] si l'entreprise est introuvable ou inaccessible."""
    entreprise = entreprise if entreprise is not None else noeud_entreprise(graphe)
    if racine is None:
        return []
    if entreprise is None or entreprise == racine or entreprise not in graphe or racine not in graphe:
        return [racine]
    try:
        chemin = nx.shortest_path(graphe.to_undirected(as_view=True), entreprise, racine)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return [entreprise, racine]
    return chemin[-LIMITE_CHEMIN:]


def _plier(texte):
    """Minuscules sans accents : « Urgences », « urgences » et « Úrgences » se valent dans une recherche."""
    d = unicodedata.normalize("NFD", str(texte).lower())
    return "".join(c for c in d if not unicodedata.combining(c))


def rechercher_noeuds(graphe, texte, libelle=None, limite=LIMITE_RECHERCHE):
    """Nœuds de TOUT le graphe (pas seulement de l'aperçu) dont le libellé contient `texte` (sans tenir compte de la casse ni des accents),
    classés : exact, début, contenu. `libelle(attrs, id)` donne le libellé affiché (par défaut le label stocké). Retourne [{id, label, type}]."""
    t = _plier((texte or "").strip())
    if not t:
        return []
    libelle = libelle or (lambda attrs, n: attrs.get("label", n))
    trouves = []
    for n, a in graphe.nodes(data=True):
        lab = str(libelle(a, n))
        bas = _plier(lab)
        if t not in bas:
            continue
        rang = 0 if bas == t else 1 if bas.startswith(t) else 2
        trouves.append((rang, len(lab), lab, n, a.get("type", "?")))
    trouves.sort(key=lambda x: (x[0], x[1], x[2]))
    return [{"id": n, "label": lab, "type": typ} for _, _, lab, n, typ in trouves[:max(1, int(limite))]]


def rechercher_cellules(graphe, texte, lectures, libelle=None, limite=LIMITE_RECHERCHE):
    """CELLULES dont la valeur contient `texte` (casse et accents ignorés) : chaque cellule est un nœud `cellule:<id ligne>:<slug colonne>`
    (voir ontologie_cellules.py). Valeur exacte d'abord, puis début de valeur, puis valeur contenant le texte.

    `lectures` : itérable de (DataFrame avec la colonne `_id`, {slug de colonne: nom affiché}[, nom de la table]) — une table physique chacun.
    Seules les lignes qui ont un nœud `enregistrement:<id>` dans `graphe` sont renvoyées : le graphe a déjà été restreint aux droits de
    l'utilisateur, donc aucune ligne interdite ne peut sortir. Retourne [{id, label, type: "cellule", detail}] (une entrée par cellule, au plus `limite`)."""
    t = _plier((texte or "").strip())
    if not t:
        return []
    libelle = libelle or (lambda attrs, n: attrs.get("label", n))
    limite = max(1, int(limite))
    trouves = []   # (rang, longueur, valeur, id, type, détail)
    for lecture in lectures:
        df, noms = lecture[0], lecture[1]
        nom_table = lecture[2] if len(lecture) > 2 else ""
        if df is None or "_id" not in df.columns:
            continue
        df = df.reset_index(drop=True)   # index unique : `.at[idx]` désigne une seule cellule
        for slug, nom in noms.items():
            if slug not in df.columns:
                continue
            serie = df[slug]
            serie = serie[serie.notna()]
            if serie.empty:
                continue
            textes = serie.astype(str)
            pliees = textes.map(_plier)
            masque = pliees.str.contains(t, regex=False)
            for idx in textes.index[masque.values]:
                nid_ligne = f"enregistrement:{df.at[idx, '_id']}"
                if not graphe.has_node(nid_ligne):
                    continue
                valeur = textes.at[idx]
                bas = pliees.at[idx]
                rang = 0 if bas == t else 1 if bas.startswith(t) else 2
                ligne = str(libelle(graphe.nodes[nid_ligne], nid_ligne))
                detail = " › ".join(x for x in (nom_table, nom) if x) + f" · {ligne}"
                trouves.append((rang, len(valeur), valeur, f"cellule:{df.at[idx, '_id']}:{slug}", "cellule", detail))
    trouves.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [{"id": n, "label": v[:80], "type": typ, "detail": d} for _, _, v, n, typ, d in trouves[:limite]]
