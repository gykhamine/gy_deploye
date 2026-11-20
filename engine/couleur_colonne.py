"""COULEUR DE COLONNE : une colonne d'une table dynamique peut porter une couleur, de l'écran jusqu'au PDF.

Module pur (aucune base, aucun Django) : une seule définition des couleurs pour l'écran (palette envoyée au navigateur),
le serveur (validation) et le PDF (en-tête de colonne + teinte des cellules).

La couleur se donne de quatre façons, toutes ramenées à « #rrggbb » ou, avec un canal alpha (opacité), « #rrggbbaa » (RGBA) :
  - un NOM (français ou anglais, accents et majuscules ignorés) : « bleu », « Rouge », « bordeaux », « navy »…
  - un code HEXADÉCIMAL : « #1e90ff », « 1e90ff » ou la forme courte « #fa0 » ;
  - un choix dans la PALETTE (COULEURS_NOMMEES, proposée par l'écran), qui revient à un nom ;
  - une notation RGBA : « rgba(30, 144, 255, 0.5) » ou un hexadécimal à 4 ou 8 chiffres (« #1e90ff80 »).
Texte vide = pas de couleur (la colonne retrouve l'apparence normale).

OPACITÉ : sans canal alpha, la couleur garde sa teinte standard (colonne 18 %, ligne ou cellule 35 %, en-tête pleine). Avec un canal alpha
EXPLICITE (« #rrggbbaa », même « ff »), c'est cette opacité qui est appliquée partout : à l'écran (rgba) et dans le PDF (la couleur est
alors posée sur du blanc, voir sur_blanc : reportlab lit mal « #rrggbbaa »).
"""
from engine.i18n import tf
import re
import unicodedata

# nom (sans accent, minuscules) -> « #rrggbb ». Les noms français d'usage courant d'abord (ce sont eux que la palette affiche),
# puis les noms anglais usuels (CSS) pour qui les saisit à la main.
COULEURS_NOMMEES = {
    "rouge": "#e53935", "bordeaux": "#800020", "corail": "#ff7f50", "saumon": "#fa8072", "rose": "#ec407a",
    "magenta": "#d500f9", "violet": "#8e24aa", "lavande": "#b39ddb", "indigo": "#3f51b5", "bleu": "#1e88e5",
    "bleu marine": "#0d2b6b", "bleu ciel": "#4fc3f7", "turquoise": "#00bfa5", "cyan": "#00bcd4", "menthe": "#69f0ae",
    "vert": "#43a047", "vert fonce": "#1b5e20", "olive": "#808000", "citron": "#cddc39", "jaune": "#fdd835",
    "or": "#ffc107", "orange": "#fb8c00", "marron": "#6d4c41", "beige": "#d7ccc8", "gris": "#9e9e9e",
    "gris clair": "#e0e0e0", "gris fonce": "#455a64", "argent": "#c0c0c0", "noir": "#111111", "blanc": "#ffffff",
    # anglais
    "red": "#e53935", "blue": "#1e88e5", "green": "#43a047", "yellow": "#fdd835", "purple": "#8e24aa",
    "pink": "#ec407a", "brown": "#6d4c41", "grey": "#9e9e9e", "gray": "#9e9e9e", "black": "#111111",
    "white": "#ffffff", "navy": "#0d2b6b", "teal": "#00897b", "gold": "#ffc107", "silver": "#c0c0c0",
    "maroon": "#800000", "lime": "#cddc39", "aqua": "#00bcd4", "coral": "#ff7f50", "salmon": "#fa8072",
}

# Les 24 couleurs que la palette de l'écran propose (ordre d'affichage) : un nom de COULEURS_NOMMEES chacune.
PALETTE = (
    "rouge", "bordeaux", "corail", "rose", "magenta", "violet", "lavande", "indigo",
    "bleu", "bleu marine", "bleu ciel", "turquoise", "menthe", "vert", "vert fonce", "olive",
    "citron", "jaune", "or", "orange", "marron", "beige", "gris", "noir",
)

_HEX = re.compile(r"#?([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_RGBA = re.compile(r"rgba?\(\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*[,\s]\s*(\d{1,3})\s*(?:[,/]\s*([0-9]*\.?[0-9]+%?)\s*)?\)", re.I)


def _sans_accent(texte):
    return "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn")


def normaliser_couleur(texte):
    """« bleu » / « #1E90FF » / « fa0 » -> « #rrggbb » minuscule ; « #1e90ff80 » / « rgba(30,144,255,0.5) » -> « #rrggbbaa ».
    Vide (None, espaces) -> « » (aucune couleur). ValueError (message lisible) si ce n'est ni un nom connu, ni un code hexadécimal,
    ni une notation rgba() valide."""
    brut = "" if texte is None else str(texte).strip()
    if not brut:
        return ""
    nom = " ".join(_sans_accent(brut).lower().replace("-", " ").replace("_", " ").split())
    if nom in COULEURS_NOMMEES:
        return COULEURS_NOMMEES[nom]
    r = _RGBA.fullmatch(brut)
    if r:
        rvb = [int(r.group(i)) for i in (1, 2, 3)]
        if max(rvb) > 255:
            raise ValueError(tf("engine/couleur_colonne.py:g:65:30", v1=brut))
        code = "".join("%02x" % v for v in rvb)
        if r.group(4) is not None:
            a = r.group(4)
            a = float(a[:-1]) / 100 if a.endswith("%") else float(a)
            code += "%02x" % round(min(max(a, 0.0), 1.0) * 255)
        return "#" + code
    m = _HEX.fullmatch(brut)
    if not m:
        raise ValueError(tf("engine/couleur_colonne.py:g:74:26", v1=brut))
    code = m.group(1).lower()
    if len(code) in (3, 4):
        code = "".join(c * 2 for c in code)
    return "#" + code


def _rvb(couleur):
    return tuple(int(couleur[i:i + 2], 16) for i in (1, 3, 5))


def alpha(couleur):
    """Opacité (0 à 1) d'une couleur « #rrggbbaa » ; None si elle n'a pas de canal alpha (teinte standard)."""
    return int(couleur[7:9], 16) / 255 if couleur and len(couleur) == 9 else None


def sur_blanc(couleur):
    """« #rrggbb » opaque : la couleur posée sur du blanc selon son opacité (inchangée sans canal alpha). À utiliser pour tout ce que
    reportlab doit lire : colors.HexColor ne comprend pas « #rrggbbaa » (il l'interprète de travers, sans erreur)."""
    if not couleur:
        return ""
    a = alpha(couleur)
    if a is None:
        return couleur[:7]
    return "#%02x%02x%02x" % tuple(round(255 - (255 - v) * a) for v in _rvb(couleur))


def couleur_texte(couleur):
    """Noir ou blanc, selon la luminosité du fond : le texte d'un en-tête coloré reste toujours lisible (fond vu sur du blanc)."""
    r, g, b = _rvb(sur_blanc(couleur))
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def teinte_claire(couleur, part=0.18):
    """La couleur très atténuée (mélangée au blanc) : fond des cellules de la colonne dans le PDF, texte noir lisible.
    Une couleur à opacité explicite (« #rrggbbaa ») impose son opacité : `part` n'est alors plus utilisé."""
    if alpha(couleur) is not None:
        part = alpha(couleur)
    return "#%02x%02x%02x" % tuple(round(255 - (255 - v) * part) for v in _rvb(couleur))


# ── COULEUR D'UNE LIGNE ET D'UNE CELLULE ──────────────────────────────────────────────────────────────────────────
# En plus de la colonne, une LIGNE entière (Enregistrement.couleur) et une CELLULE précise (Enregistrement.couleurs_cellules,
# {slug de colonne: « #rrggbb »}) se colorient, chacune INDÉPENDAMMENT des deux autres : colorier une cellule ne touche pas
# la ligne, colorier une ligne n'efface aucune couleur de cellule, et l'inverse. Quand plusieurs couleurs se superposent sur
# une même cellule, la plus précise gagne : cellule > ligne > colonne. L'écran (grille) et le PDF suivent cette même règle.
# Une couleur posée à la main (ligne ou cellule) est teintée plus fort (PART_TEINTE_MARQUE) que celle d'une colonne
# (teinte_claire, 0,18) : un repère individuel reste visible même sur une colonne colorée. Le texte reste noir (lisible).
PART_TEINTE_MARQUE = 0.35
CLE_LIGNE = "_couleur"        # dans une « mise en forme » : couleur de la ligne
CLE_CELLULES = "_couleurs"    # dans une « mise en forme » : {slug: couleur} des cellules


def teinte_marque(couleur):
    """Fond d'une ligne ou d'une cellule coloriée à la main (mélange au blanc, plus marqué que teinte_claire)."""
    return teinte_claire(couleur, PART_TEINTE_MARQUE)


def normaliser_couleurs_cellules(valeur, slugs_valides=None):
    """Nettoie le dictionnaire {slug: couleur} lu en base : couleurs illisibles ignorées, et (si `slugs_valides` est donné)
    colonnes qui n'existent plus ignorées. Jamais d'exception : une donnée abîmée ne doit pas casser l'écran ni le PDF."""
    if not isinstance(valeur, dict):
        return {}
    propre = {}
    for slug, brut in valeur.items():
        if slugs_valides is not None and slug not in slugs_valides:
            continue
        try:
            couleur = normaliser_couleur(brut)
        except (ValueError, TypeError):
            continue
        if couleur:
            propre[str(slug)] = couleur
    return propre


def mise_en_forme(couleur_ligne, couleurs_cellules):
    """La mise en forme d'UNE ligne telle que l'échangent la grille et le PDF : {} si rien n'est colorié, sinon
    {CLE_LIGNE: « #rrggbb »} et/ou {CLE_CELLULES: {slug: « #rrggbb »}} (seulement les parties présentes)."""
    forme = {}
    try:
        ligne = normaliser_couleur(couleur_ligne)
    except (ValueError, TypeError):
        ligne = ""
    if ligne:
        forme[CLE_LIGNE] = ligne
    cellules = normaliser_couleurs_cellules(couleurs_cellules)
    if cellules:
        forme[CLE_CELLULES] = cellules
    return forme


def fond_cellule(couleur_colonne, forme, slug):
    """(fond « #rrggbb » teinté, origine) d'une cellule : origine = « cellule », « ligne », « colonne » ou « » (aucun fond).
    La cellule prime sur la ligne, la ligne sur la colonne."""
    forme = forme or {}
    cellule = (forme.get(CLE_CELLULES) or {}).get(slug)
    if cellule:
        return teinte_marque(cellule), "cellule"
    if forme.get(CLE_LIGNE):
        return teinte_marque(forme[CLE_LIGNE]), "ligne"
    if couleur_colonne:
        return teinte_claire(couleur_colonne), "colonne"
    return "", ""


def zones_fond_pdf(formes, slugs):
    """Rectangles de fond à poser par-dessus les fonds de colonne dans le PDF (hors en-tête), dans l'ordre où ils doivent
    s'appliquer : [(index_ligne, col_debut, col_fin, « #rrggbb » teinté)], index_ligne = rang dans `formes` (0 = 1re ligne
    de données). Pour chaque ligne coloriée : d'abord UN rectangle sur toute la ligne, puis un par cellule coloriée à part
    (la cellule recouvre la ligne). Peu de commandes même pour une grande table : seules les lignes coloriées en génèrent."""
    zones = []
    colonne = {s: i for i, s in enumerate(slugs)}
    for i, forme in enumerate(formes or []):
        if not forme or not slugs:
            continue
        if forme.get(CLE_LIGNE):
            zones.append((i, 0, len(slugs) - 1, teinte_marque(forme[CLE_LIGNE])))
        for slug, couleur in (forme.get(CLE_CELLULES) or {}).items():
            if slug in colonne:
                zones.append((i, colonne[slug], colonne[slug], teinte_marque(couleur)))
    return zones
