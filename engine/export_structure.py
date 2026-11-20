"""STRUCTURE VISUELLE D'UN EXPORT DE TABLE : couleurs et cellules fusionnées, QUELLE QUE SOIT LA VUE EXPORTÉE.

Problème résolu : la grille à l'écran montre des couleurs (colonne, ligne, cellule) et des cellules fusionnées, mais les exports ne les
reprenaient que dans un cas étroit : le PDF seulement, et seulement pour la table ENTIÈRE dans l'ordre par défaut (ni recherche, ni filtre,
ni tri, ni plafond de lignes, ni découpage en parties de colonnes) ; l'export Excel n'en reprenait rien.

Principe : une fusion est ancrée sur des LIGNES (identifiants d'enregistrements, voir fusion_cellule.resynchroniser) et non sur une position
dans la vue. Pour une vue exportée (liste ordonnée d'identifiants de lignes : filtrée, triée, tronquée…), `fusions_vue` retrouve les
lignes de chaque fusion qui sont dans la vue et en fait un RECTANGLE par suite de lignes consécutives (« série »). Une fusion dont les
lignes sont séparées par le filtre ou le tri donne donc plusieurs rectangles — jamais une fusion fausse qui couvrirait des lignes
étrangères, jamais une fusion perdue. Même résultat pour le PDF (SPAN reportlab) et l'Excel (merge_cells).

Module sans dépendance reportlab/openpyxl ; seule `fusions_vue` lit la base (un rang -> identifiant de ligne).
"""
from types import SimpleNamespace as NS

from .couleur_colonne import CLE_CELLULES, CLE_LIGNE, sur_blanc, teinte_claire, teinte_marque

FOND_FUSION_DEFAUT = "#eef2ff"   # fusion dont la cellule d'ancrage n'a aucune couleur (colonne, ligne ou cellule)


def _series(positions):
    """[(debut, fin)] des suites de positions consécutives (positions triées, sans doublon)."""
    series, debut, prec = [], None, None
    for p in positions:
        if debut is None:
            debut = prec = p
        elif p == prec + 1:
            prec = p
        else:
            series.append((debut, prec))
            debut = prec = p
    if debut is not None:
        series.append((debut, prec))
    return series


def fusions_vue(table, fusions, ids_vue, champs, calculs=None):
    """Rectangles de fusion pour une vue exportée.

    `ids_vue`  : identifiants des lignes exportées, DANS L'ORDRE de l'export (index 0 = 1re ligne de données).
    `champs`   : colonnes exportées dans l'ordre (index = colonne). Les fusions sont repérées par rang de colonne dans la table ENTIÈRE :
                 `champs` doit donc être la liste complète des colonnes de la table, ou à défaut garder cet ordre (une colonne retirée
                 de l'export, par ex. vecteur/matrice, retire aussi sa place : voir `decaler_colonnes`).
    `calculs`  : {fusion.id -> {"valeur_calculee", "erreur_calcul"}} des fusions « formule » (fusion_cellule.calculer_fusions sur la
                 table ENTIÈRE : une formule lit des lignes que la vue peut ne pas contenir). Absent : valeur saisie.
    Renvoie une liste de NS(id, haut, bas, col_debut, col_fin, texte, type_affiche) : `haut`/`bas` = index de ligne dans la vue (inclus),
    `col_debut`/`col_fin` = index de colonne (inclus). Rectangles réduits à UNE cellule : omis (rien à fusionner)."""
    from .fusion_cellule import texte_affiche
    from .models import Enregistrement

    fusions = list(fusions or [])
    if not fusions or not ids_vue:
        return []
    chrono = list(Enregistrement.objects.filter(table_id=table.id).order_by("cree_le", "id").values_list("id", flat=True))
    position = {eid: i for i, eid in enumerate(ids_vue)}
    nb_col = len(champs)
    sortie = []
    for f in fusions:
        if f.col_debut >= nb_col:
            continue
        col_fin = min(f.col_fin, nb_col - 1)
        positions = sorted({position[i] for i in chrono[f.ligne_debut:f.ligne_fin + 1] if i in position})
        texte = texte_affiche(f, calculs)
        for haut, bas in _series(positions):
            if haut == bas and f.col_debut == col_fin:
                continue
            sortie.append(NS(id=f.id, haut=haut, bas=bas, col_debut=f.col_debut, col_fin=col_fin, texte=texte, type_affiche=f.type_affiche))
    return sortie


def calculs_formules(table, fusions, champs, limite=100000):
    """Résultats des fusions « formule » (fusion_cellule.calculer_fusions) évalués sur la table ENTIÈRE, quelle que soit la vue exportée :
    une formule lit des lignes que la vue (filtre, tri, plafond) peut ne pas contenir. {} s'il n'y a aucune formule ou si la table dépasse
    `limite` lignes (les rangs ne seraient plus fiables)."""
    fusions = list(fusions or [])
    if not any(f.type_affiche == "formule" for f in fusions):
        return {}
    from . import parquet_store
    from .fusion_cellule import calculer_fusions
    from .models import Enregistrement
    tous = list(Enregistrement.objects.filter(table_id=table.id).order_by("-cree_le", "-id")[:limite + 1])
    if len(tous) > limite:
        return {}
    lignes, _ = parquet_store.lignes_affichage(table, tous)
    return calculer_fusions(fusions, champs, lignes)


def fusions_defaut(fusions, nb_lignes, champs, lignes, nb_total=None):
    """Équivalent de `fusions_vue` pour les appelants qui impriment la table entière, du plus récent au plus ancien (Résumé général) :
    `fusions` = FusionCelluleTable déjà resynchronisées, `lignes` = valeurs de la vue (même ordre). `nb_total` = nombre de lignes de la
    table avant plafonnement (rang chronologique r -> index de vue nb_total-1-r) ; défaut = len(lignes). Sans accès à la base."""
    from .fusion_cellule import calculer_fusions, texte_affiche
    fusions = list(fusions or [])
    if not fusions or not nb_lignes:
        return []
    total = max(nb_total or 0, nb_lignes)
    calculs = calculer_fusions(fusions, champs, lignes) if total == nb_lignes else {}
    sortie = []
    for f in fusions:
        haut, bas = total - 1 - f.ligne_fin, total - 1 - f.ligne_debut
        if bas < 0 or haut >= nb_lignes:
            continue
        haut, bas = max(haut, 0), min(bas, nb_lignes - 1)
        col_fin = min(f.col_fin, len(champs) - 1)
        if f.col_debut > col_fin or (haut == bas and f.col_debut == col_fin):
            continue
        sortie.append(NS(id=f.id, haut=haut, bas=bas, col_debut=f.col_debut, col_fin=col_fin, texte=texte_affiche(f, calculs),
                         type_affiche=f.type_affiche))
    return sortie


def decaler_colonnes(fusions_rectangles, colonnes_tout, colonnes_exportees):
    """Fusions repérées sur `colonnes_tout` (slugs de toutes les colonnes de la table) ramenées aux `colonnes_exportees` (sous-liste
    ordonnée) : une colonne absente de l'export est retirée du rectangle, qui se contracte ; un rectangle sans aucune colonne exportée
    disparaît. Sans effet quand les deux listes sont identiques."""
    if list(colonnes_tout) == list(colonnes_exportees):
        return fusions_rectangles
    nouvel_index = {s: i for i, s in enumerate(colonnes_exportees)}
    sortie = []
    for r in fusions_rectangles:
        gardees = [nouvel_index[s] for s in colonnes_tout[r.col_debut:r.col_fin + 1] if s in nouvel_index]
        if not gardees:
            continue
        sortie.append(NS(**{**vars(r), "col_debut": min(gardees), "col_fin": max(gardees)}))
    return sortie


def decouper_colonnes(rectangles, c_deb, c_fin):
    """Rectangles ramenés à la partie de colonnes [c_deb, c_fin) d'un export découpé (table large) : une fusion à cheval sur deux
    parties est COUPÉE (chaque partie en garde le morceau qui la concerne, avec le même texte) au lieu d'être perdue. Colonnes relatives
    à la partie. Un morceau réduit à une seule cellule est omis."""
    sortie = []
    for r in rectangles:
        d, f = max(r.col_debut, c_deb), min(r.col_fin, c_fin - 1)
        if d > f or (r.haut == r.bas and d == f):
            continue
        sortie.append(NS(**{**vars(r), "col_debut": d - c_deb, "col_fin": f - c_deb}))
    return sortie


def borner_lignes(rectangles, nb_lignes):
    """Rectangles tronqués aux `nb_lignes` premières lignes de la vue (plafond d'un PDF) : coupés au lieu d'être perdus."""
    sortie = []
    for r in rectangles:
        if r.haut >= nb_lignes:
            continue
        bas = min(r.bas, nb_lignes - 1)
        if r.haut == bas and r.col_debut == r.col_fin:
            continue
        sortie.append(NS(**{**vars(r), "bas": bas}))
    return sortie


# ── Couleurs ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def fond_cellule_export(couleur_colonne, forme, slug):
    """« #rrggbb » (opaque) du fond d'une cellule d'export, ou « » : cellule > ligne > colonne — même règle que la grille et que le PDF
    (couleur_colonne.fond_cellule), mais prêt pour reportlab/openpyxl (une couleur à opacité est posée sur du blanc)."""
    forme = forme or {}
    cellule = (forme.get(CLE_CELLULES) or {}).get(slug)
    if cellule:
        return sur_blanc(teinte_marque(cellule))
    if forme.get(CLE_LIGNE):
        return sur_blanc(teinte_marque(forme[CLE_LIGNE]))
    if couleur_colonne:
        return sur_blanc(teinte_claire(couleur_colonne))
    return ""


def fond_fusion(couleur_colonne, forme, slug):
    """Fond d'UNE cellule d'un rectangle fusionné : sa propre couleur (cellule > ligne > colonne) ; teinte par défaut si elle n'en a pas."""
    return fond_cellule_export(couleur_colonne, forme, slug) or FOND_FUSION_DEFAUT


def zones_fusion(rect, fond_de):
    """Zones de couleur d'un rectangle fusionné : [(haut, bas, col, « #rrggbb »)] (index de vue, inclus).
    CHAQUE cellule fusionnée garde SA couleur — la colonne est la colonne, la cellule est la cellule : la couleur d'une colonne ne déteint
    pas sur les autres colonnes de la fusion, ni celle d'une ligne sur les autres lignes. `fond_de(ligne, col)` donne le fond d'une cellule
    (fond_fusion). Les lignes consécutives de même couleur dans une colonne sont regroupées (peu de commandes même pour une longue fusion)."""
    zones = []
    for col in range(rect.col_debut, rect.col_fin + 1):
        debut, courante = rect.haut, None
        for ligne in range(rect.haut, rect.bas + 1):
            fond = fond_de(ligne, col)
            if courante is None:
                courante = fond
            elif fond != courante:
                zones.append((debut, ligne - 1, col, courante))
                debut, courante = ligne, fond
        zones.append((debut, rect.bas, col, courante))
    return zones


# ── Couleur de PARTIE (PDF d'une table large découpée en parties de colonnes) ─────────────────────────────────────────
# Chaque partie a sa couleur (bandeau, en-tête, colonne « # », bordure, repère de colonnes) pour qu'on distingue les coupes d'un coup d'œil.
# C'est le système de coloriage DE BASE : les couleurs déclarées dans la grille (colonne, ligne, cellule, fusion) sont posées PAR-DESSUS et
# l'emportent toujours ; la couleur de partie ne colore que ce qui n'a pas de couleur déclarée.
COULEURS_PARTIES = ("#1d4ed8", "#b45309", "#047857", "#7e22ce", "#be123c", "#0e7490")   # bleu, ambre, vert, violet, rose, cyan (cycle)


def couleur_partie(no_partie):
    """« #rrggbb » de la partie n° `no_partie` (1, 2, 3…) : le cycle recommence après la dernière couleur."""
    return COULEURS_PARTIES[(max(1, no_partie) - 1) % len(COULEURS_PARTIES)]
