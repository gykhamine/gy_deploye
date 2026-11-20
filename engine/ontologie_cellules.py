"""engine/ontologie_cellules.py — CHAQUE CELLULE EST UN NŒUD de l'ontologie.

Une cellule (valeur d'une colonne sur une ligne) est le nœud `cellule:<id ligne>:<slug colonne>`. Elle est reliée comme les autres nœuds :

  ligne ──valeur──▶ cellule            la ligne qui porte la valeur (et, par elle, sa table, son créateur…)
  colonne ──valeur_de_colonne──▶ cellule   la colonne (nœud `champ:`) dont c'est une valeur
  cellule ──meme_valeur── cellule      la même valeur ailleurs (autre ligne, autre colonne, autre table) ;
                                       chaque correspondance porte aussi les autres cellules de SA ligne (ligne ──valeur──▶ cellule)

Pourquoi à la demande et non dans l'état complet : 10 000 lignes × 12 colonnes feraient 120 000 nœuds en plus dans l'état construit en arrière-plan.
Les cellules sont donc matérialisées pour la ligne que l'on regarde (racine de l'aperçu, fiche) et pour la valeur cherchée : l'ontologie reste
bornée à l'affichage, et toute valeur est atteignable par la recherche, qui renvoie d'abord les CELLULES.

Droits : une cellule n'apparaît que si sa ligne est dans le graphe reçu (déjà restreint aux droits de l'utilisateur) et, quand le graphe porte
les colonnes, que sa colonne y figure aussi.
"""
import unicodedata

import networkx as nx

LIMITE_MEME_VALEUR = 15      # cellules « même valeur » ajoutées autour d'une cellule racine
LONGUEUR_LABEL = 80
_IGNOREES = {"matricule", "code_acces_client", "_id"}


def id_cellule(eid, slug):
    return f"cellule:{eid}:{slug}"


def decoder(nid):
    """'cellule:12:nom' → (12, 'nom') ; autre chose → None."""
    if not isinstance(nid, str) or not nid.startswith("cellule:"):
        return None
    _, _, reste = nid.partition(":")
    eid, _, slug = reste.partition(":")
    return (int(eid), slug) if eid.isdigit() and slug else None


def ligne_de(nid):
    """Identifiant du nœud ligne d'un nœud cellule ou ligne ; sinon None."""
    if isinstance(nid, str) and nid.startswith("enregistrement:"):
        return nid
    d = decoder(nid)
    return f"enregistrement:{d[0]}" if d else None


def _plier(texte):
    d = unicodedata.normalize("NFD", str(texte).strip().lower())
    return "".join(c for c in d if not unicodedata.combining(c))


def _texte(valeur):
    if valeur is None:
        return ""
    return str(valeur).strip()


def _lire_ligne(entreprise, eid):
    """(Enregistrement, {slug: champ}, {slug: valeur}) ou None. Les valeurs vides et les clés techniques sont écartées."""
    from . import parquet_store
    from .models import Enregistrement
    e = Enregistrement.objects.select_related("table").filter(id=eid, table__entreprise=entreprise).first()
    if e is None:
        return None
    champs = {c.slug: c for c in e.table.champs.all()}
    try:
        valeurs = parquet_store.valeurs_lignes(e.table, [e])[0]
    except Exception:
        valeurs = e.to_dict()
    valeurs = {s: v for s, v in valeurs.items() if s in champs and s not in _IGNOREES and _texte(v) != ""}
    return e, champs, valeurs


def _copier_noeud(cible, source, nid):
    if nid in source and nid not in cible:
        cible.add_node(nid, **dict(source.nodes[nid]))


def _copier_arete(cible, source, u, v):
    if source.has_edge(u, v) and not cible.has_edge(u, v):
        cible.add_edge(u, v, **dict(source.edges[u, v]))


def _ajouter_ligne_et_contexte(cible, graphe, nid_ligne):
    """La ligne, sa table, et les nœuds directement reliés à la ligne (créateur, blocs…), avec leurs relations."""
    _copier_noeud(cible, graphe, nid_ligne)
    for v in set(graphe.predecessors(nid_ligne)) | set(graphe.successors(nid_ligne)):
        if v.startswith("cellule:"):
            continue
        _copier_noeud(cible, graphe, v)
        _copier_arete(cible, graphe, v, nid_ligne)
        _copier_arete(cible, graphe, nid_ligne, v)


def _noeud_cellule(cible, eid, slug, nom_colonne, valeur, nom_table):
    nid = id_cellule(eid, slug)
    if nid not in cible:
        texte = _texte(valeur)
        cible.add_node(nid, type="cellule", label=texte[:LONGUEUR_LABEL] + ("…" if len(texte) > LONGUEUR_LABEL else ""), taille=7,
                       valeur=texte, colonne=nom_colonne, table=nom_table, ligne_id=eid)
    return nid


def _membres_de_ligne(cible, df, idx, eid, noms, nom_table):
    """Cellules de la ligne `idx` du DataFrame lu (déjà en mémoire, aucune requête de plus), reliées à leur ligne :
    chaque correspondance « même valeur » montre ainsi les autres membres de SA ligne, pour distinguer les lignes entre elles."""
    nid_ligne = f"enregistrement:{eid}"
    for slug, nom_colonne in noms.items():
        if slug in _IGNOREES or slug not in df.columns:
            continue
        v = df.at[idx, slug]
        if v is None or v != v or _texte(v) == "":   # None, NaN, vide
            continue
        cible.add_edge(nid_ligne, _noeud_cellule(cible, eid, slug, nom_colonne, v, nom_table), relation="valeur")


def ajouter_cellules_de_ligne(cible, graphe, entreprise, nid_ligne):
    """Ajoute à `cible` toutes les cellules de la ligne `nid_ligne` (si elle est dans `graphe`), reliées à la ligne et à leur colonne.
    Retourne {slug: id de cellule} (vide si la ligne est absente du graphe, introuvable ou sans valeur)."""
    if nid_ligne not in graphe:
        return {}
    eid = int(nid_ligne.split(":", 1)[1])
    lu = _lire_ligne(entreprise, eid)
    if lu is None:
        return {}
    e, champs, valeurs = lu
    _ajouter_ligne_et_contexte(cible, graphe, nid_ligne)
    nom_table = getattr(e.table, "nom", "") or e.table.slug
    colonnes_visibles = {s for s, c in champs.items() if graphe.has_node(f"champ:{c.id}")} or set(champs)
    ids = {}
    for slug, valeur in valeurs.items():
        if slug not in colonnes_visibles:
            continue
        nid = _noeud_cellule(cible, eid, slug, champs[slug].nom, valeur, nom_table)
        cible.add_edge(nid_ligne, nid, relation="valeur")
        nid_champ = f"champ:{champs[slug].id}"
        if nid_champ in graphe:
            _copier_noeud(cible, graphe, nid_champ)
            cible.add_edge(nid_champ, nid, relation="valeur_de_colonne")
        ids[slug] = nid
    nid_table = f"table:{e.table_id}"
    if nid_table in graphe:
        _copier_noeud(cible, graphe, nid_table)
        _copier_arete(cible, graphe, nid_table, nid_ligne)
    return ids


def ajouter_meme_valeur(cible, graphe, entreprise, nid_cellule, lectures, limite=LIMITE_MEME_VALEUR):
    """Relie la cellule `nid_cellule` aux cellules de même valeur (casse et accents ignorés) trouvées dans les autres lignes/colonnes/tables.
    `lectures` : itérable de (DataFrame avec `_id`, {slug: nom de colonne}, nom de table). Seules les lignes présentes dans `graphe` comptent."""
    d = decoder(nid_cellule)
    if d is None or nid_cellule not in cible:
        return 0
    cible_plie = _plier(cible.nodes[nid_cellule].get("valeur", ""))
    if not cible_plie:
        return 0
    ajoutees = 0
    lignes_detaillees = set()   # lignes dont les membres sont déjà ajoutés (une ligne peut correspondre par plusieurs colonnes)
    for df, noms, nom_table in lectures:
        if df is None or "_id" not in df.columns:
            continue
        df = df.reset_index(drop=True)
        for slug, nom_colonne in noms.items():
            if slug not in df.columns or slug in _IGNOREES:
                continue
            serie = df[slug]
            serie = serie[serie.notna()]
            if serie.empty:
                continue
            textes = serie.astype(str)
            masque = textes.map(_plier) == cible_plie
            for idx in textes.index[masque.values]:
                eid = int(df.at[idx, "_id"])
                nid_ligne = f"enregistrement:{eid}"
                nid = id_cellule(eid, slug)
                if nid == nid_cellule or nid_ligne not in graphe:
                    continue
                _ajouter_ligne_et_contexte(cible, graphe, nid_ligne)
                nid_table = next((p for p in graphe.predecessors(nid_ligne) if str(p).startswith("table:")), None)
                if nid_table:
                    _copier_noeud(cible, graphe, nid_table)
                    _copier_arete(cible, graphe, nid_table, nid_ligne)
                if nid not in cible:
                    cible.add_node(nid, type="cellule", label=cible.nodes[nid_cellule]["label"], taille=7, valeur=cible.nodes[nid_cellule]["valeur"],
                                   colonne=nom_colonne, table=nom_table, ligne_id=eid)
                cible.add_edge(nid_ligne, nid, relation="valeur")
                cible.add_edge(nid_cellule, nid, relation="meme_valeur")
                if nid_ligne not in lignes_detaillees:
                    lignes_detaillees.add(nid_ligne)
                    _membres_de_ligne(cible, df, idx, eid, noms, nom_table)
                ajoutees += 1
                if ajoutees >= limite:
                    return ajoutees
    return ajoutees


def enrichir_apercu(affiche, graphe, entreprise, racine, lectures=None):
    """Appelée par la page : `affiche` est l'aperçu borné (DiGraph), `racine` l'identifiant demandé (ligne ou cellule).
    Ajoute les cellules de la ligne ; pour une racine cellule, ajoute aussi ses « même valeur ». Retourne le nombre de nœuds ajoutés."""
    nid_ligne = ligne_de(racine)
    if nid_ligne is None or nid_ligne not in graphe:
        return 0
    avant = affiche.number_of_nodes()
    ids = ajouter_cellules_de_ligne(affiche, graphe, entreprise, nid_ligne)
    d = decoder(racine)
    if d is not None and lectures is not None and ids.get(d[1]):
        try:
            ajouter_meme_valeur(affiche, graphe, entreprise, ids[d[1]], lectures)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("ontologie : cellules de même valeur impossibles")
    return affiche.number_of_nodes() - avant


def sous_graphe_cellule(graphe, entreprise, nid, lectures=None):
    """Mini-graphe d'une cellule ou d'une ligne pour sa fiche : la ligne, son contexte direct, ses cellules et les cellules de même valeur.
    Retourne un DiGraph (vide si la cellule n'existe pas ou n'est pas visible)."""
    mini = nx.DiGraph()
    enrichir_apercu(mini, graphe, entreprise, nid, lectures)
    return mini
