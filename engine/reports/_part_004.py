from engine.i18n import t as _pt, tl as _ptl, tf as _ptf
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf
from ._header import *  # noqa
from xml.sax.saxutils import escape  # noqa  (explicite : le code ci-dessous en dépend, ne pas compter sur un import * en amont)
from ..couleur_colonne import sur_blanc as _flat  # noqa  (couleur « #rrggbbaa » -> « #rrggbb » lisible par reportlab)

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa


from reportlab.platypus import Flowable as _FlowableBI, KeepInFrame as _KeepInFrameBI


class _CanevasBI(_FlowableBI):
    """Une bande du canevas d'un tableau de bord BI, dessinée COMME À L'ÉCRAN : chaque widget occupe sa place sur la grille
    (position_x, position_y, largeur, hauteur en unités de grille), avec ses vraies proportions, son cadre coloré éventuel et son
    contenu réel (image du graphique, texte, carte KPI). Le contenu de chaque cellule est fabriqué à la taille de sa case."""

    def __init__(self, cellules, y0, hauteur_u, cw, row_h, largeur, gap, pad):
        super().__init__()
        self.cellules, self.y0, self.hauteur_u = cellules, y0, hauteur_u
        self.cw, self.row_h, self.largeur, self.gap, self.pad = cw, row_h, largeur, gap, pad
        self.haut = hauteur_u * row_h

    def wrap(self, largeur_dispo, hauteur_dispo):
        return self.largeur, self.haut

    def draw(self):
        from reportlab.lib import colors as _c
        from ..couleur_colonne import teinte_claire
        cv = self.canv
        for cel in self.cellules:
            x = cel["x"] * self.cw + self.gap / 2
            hu = min(cel["h"], self.hauteur_u - (cel["y"] - self.y0))
            if hu <= 0:
                continue
            w = self.cw * cel["l"] - self.gap
            h = self.row_h * hu - self.gap
            bas = self.haut - (cel["y"] - self.y0) * self.row_h - self.gap / 2 - h
            cv.saveState()
            if cel["couleur"]:
                cv.setFillColor(_c.HexColor(teinte_claire(cel["couleur"])))
                cv.setStrokeColor(_c.HexColor(_flat(cel["couleur"])))
                cv.setLineWidth(1.8)
                cv.rect(x, bas, w, h, stroke=1, fill=1)
            else:
                cv.setStrokeColor(_c.HexColor("#e5e7eb"))
                cv.setLineWidth(0.5)
                cv.rect(x, bas, w, h, stroke=1, fill=0)
            cv.restoreState()
            iw, ih = w - 2 * self.pad, h - 2 * self.pad
            if iw <= 0 or ih <= 0:
                continue
            cadre = _KeepInFrameBI(iw, ih, cel["fabriquer"](iw, ih), mode="shrink", hAlign="LEFT", vAlign="TOP")
            cadre.wrapOn(cv, iw, ih)
            cadre.drawOn(cv, x + self.pad, bas + self.pad)


def _elements_dashboard_bi(tableau_bord, largeur, avec_kpis=True, hauteur_page=None) -> list:
    """Flowables reportlab d'un tableau de bord BI (bandeau, titre, KPI, widgets en grille, cachet, signatures),
    `largeur` = largeur utile de la page. Partagé par generer_pdf_dashboard_bi (PDF du tableau seul) et par le
    Résumé général (un tableau de bord BI par page) : une seule implémentation."""
    import io, base64
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, Image as RLImage
    from reportlab.lib import colors

    styles = _feuille_styles()
    entreprise = getattr(tableau_bord, "entreprise", None)
    table_source = getattr(tableau_bord, "table_source", None)
    couleurs = _couleurs_rapport(entreprise)
    elements = []

    _bandeau_entreprise(entreprise, largeur, elements, table=table_source, contexte="table")

    titre_style = ParagraphStyle("TitreDashBI", parent=styles["Title"], fontSize=14,
                                 textColor=couleurs["accent"])
    sous_titre_style = ParagraphStyle("SousTitreDashBI", parent=styles["Normal"],
                                      fontSize=8, textColor=colors.HexColor("#6b7280"))
    elements.append(Paragraph(tableau_bord.nom, titre_style))
    if getattr(tableau_bord, "description", ""):
        elements.append(Paragraph(tableau_bord.description, sous_titre_style))
    elements.append(Paragraph(
        tf("engine/reports/_part_004.py:g:36:9", v1=__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')),
        sous_titre_style))
    elements.append(Spacer(1, 0.4*cm))

    # ── KPI : widgets du canevas (type_widget='kpi'), rendus dans la grille ci-dessous, à leur place
    # sur le canevas, comme les graphiques. `avec_kpis=False` : le Résumé général les présente une fois
    # pour toutes dans sa section « KPI », ces widgets sont alors sautés ici (pas de doublon).

    # Récupérer les widgets (graphiques ET zones de texte) dans l'ordre du
    # canevas, avec image rendue pour les widgets graphiques.
    widgets = list(tableau_bord.widgets.select_related("graphique", "kpi").order_by("position_y","position_x"))
    # Canevas réel : la disposition du tableau de bord à l'écran (grille de `colonnes_grille` colonnes, chaque widget à sa position et à sa
    # taille), pas une grille uniforme. Les bandes de hauteur `hauteur_page` se suivent d'une page à l'autre.
    colonnes_grille = max(1, int(getattr(tableau_bord, "colonnes_grille", 12) or 12))
    cw = largeur / colonnes_grille
    row_h = cw * 0.61           # même rapport qu'à l'écran : unité de hauteur de 56 px pour une colonne d'environ 92 px
    gap, pad = 0.2 * cm, 0.15 * cm
    hauteur_page = hauteur_page or 12.5 * cm
    legende_h = 0.45 * cm

    texte_style = {
        "titre": ParagraphStyle("TexteBITitre", parent=styles["Heading2"], fontSize=13,
                                textColor=couleurs["accent"]),
        "sous_titre": ParagraphStyle("TexteBISousTitre", parent=styles["Heading3"], fontSize=10,
                                     textColor=colors.HexColor("#374151")),
        "paragraphe": ParagraphStyle("TexteBIParagraphe", parent=styles["Normal"], fontSize=9,
                                     textColor=colors.HexColor("#374151"), leading=12),
    }
    legende_style = ParagraphStyle("LegendeBI", parent=sous_titre_style, fontSize=7, leading=8.5)

    def _cellule_texte(widget):
        def fabriquer(iw, ih):
            style = texte_style.get(widget.style_texte, texte_style["paragraphe"])
            lignes = (widget.contenu_texte or "").split("\n")   # chaque ligne saisie reste une ligne, comme dans l'éditeur du dashboard
            return [Paragraph(ligne, style) for ligne in lignes if ligne.strip()] or [Paragraph("", style)]
        return fabriquer

    def _cellule_kpi(widget):
        def fabriquer(iw, ih):
            return _elements_kpis_table(table_source, iw, kpis=[widget.kpi], colonnes=1) or [Paragraph("", styles["Normal"])]
        return fabriquer

    def _cellule_image(widget):
        nom = (widget.titre or (widget.graphique.nom if widget.graphique else "")) or ""
        def fabriquer(iw, ih):
            img_b64 = getattr(widget, "resultat_image_base64", "") or ""
            tete = [Paragraph(nom, legende_style)] if nom else []
            reste = max(ih - (legende_h if nom else 0), 0.5 * cm)
            if img_b64:
                try:
                    ri = RLImage(io.BytesIO(base64.b64decode(img_b64)), width=iw, height=reste, kind="proportional")
                    ri.hAlign = "CENTER"
                    return tete + [ri]
                except Exception:
                    pass
            return tete + [Paragraph(tf("engine/reports/_part_004.py:g:98:28", v1=nom or "widget"), styles["Normal"])]
        return fabriquer

    cellules = []
    for widget in widgets:
        if widget.type_widget == "kpi":
            if not avec_kpis or widget.kpi is None:
                continue
            fabriquer = _cellule_kpi(widget)
        elif widget.type_widget == "texte":
            fabriquer = _cellule_texte(widget)
        else:
            fabriquer = _cellule_image(widget)
        x = max(0, min(int(widget.position_x or 0), colonnes_grille - 1))
        cellules.append({"x": x, "l": max(1, min(int(widget.largeur or 1), colonnes_grille - x)),
                         "y": max(0, int(widget.position_y or 0)), "h": max(1, int(widget.hauteur or 1)),
                         "couleur": widget.couleur or "", "fabriquer": fabriquer})

    if cellules:
        hmax_u = max(1, int(hauteur_page // row_h))
        cellules.sort(key=lambda c: (c["y"], c["x"]))
        bandes, courante, y0 = [], [], None
        for c in cellules:
            if courante and (c["y"] + c["h"]) - y0 > hmax_u:
                bandes.append((y0, courante)); courante, y0 = [], None
            if y0 is None:
                y0 = c["y"]
            courante.append(c)
        if courante:
            bandes.append((y0, courante))
        for y0, bande in bandes:
            hauteur_u = min(hmax_u, max(c["y"] + c["h"] for c in bande) - y0)
            elements.append(_CanevasBI(bande, y0, hauteur_u, cw, row_h, largeur, gap, pad))
    else:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:139:35"), styles["Normal"]))

    elements.append(Spacer(1, 0.5*cm))
    _bloc_cachet_simple(entreprise, elements, table=table_source, contexte="table")
    _bloc_signatures_pied(entreprise, elements, table=table_source, contexte="table")
    return elements


def generer_pdf_dashboard_bi(tableau_bord, orientation="paysage") -> bytes:
    """Construit un PDF du tableau de bord BI : bandeau entreprise, titre,
    images des widgets (base64 PNG) disposées en grille, puis signatures
    et cachet. `orientation` : 'portrait', 'paysage' (défaut), 'paysage_large' (A3),
    'paysage_A5'."""
    _police_activer(getattr(tableau_bord, "entreprise", None))
    import io
    from reportlab.lib.pagesizes import A4, A3, A5, landscape, portrait
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate

    _ORIENTATIONS = {
        "portrait":      portrait(A4),
        "paysage":       landscape(A4),
        "paysage_large": landscape(A3),
        "paysage_A5":    landscape(A5),
    }
    pagesize = _ORIENTATIONS.get(orientation, landscape(A4))

    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=pagesize,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            leftMargin=1.5*cm, rightMargin=1.5*cm)
    _construire(doc, _elements_dashboard_bi(tableau_bord, doc.width, hauteur_page=doc.height - 4.5 * cm), getattr(tableau_bord, "entreprise", None))
    tampon.seek(0)
    return tampon.read()


# ── Fonctions fusionnées depuis gscode ──

def _carte_fusions(rectangles, nb_lignes):
    """Construit deux lookups à partir des rectangles de fusion d'une vue (voir export_structure.fusions_vue : `haut`/`bas` = index de
    ligne DANS LA VUE exportée, `col_debut`/`col_fin` = index de colonne) :
    - `ancres`  : {(idx_ligne, col) -> rectangle} pour la cellule en haut à gauche de chaque fusion (celle qui porte réellement le SPAN
      reportlab et le contenu) ;
    - `couvertes` : ensemble des (idx_ligne, col) couvertes par une fusion mais qui ne sont PAS l'ancre (à vider dans le tableau).
    `idx_ligne` = DONNÉES UNIQUEMENT (0 = première ligne de données, sans l'en-tête), même ordre que `lignes`."""
    ancres, couvertes = {}, set()
    for f in rectangles or []:
        if f.haut < 0 or f.bas >= nb_lignes or f.haut > f.bas:
            continue
        for idx in range(f.haut, f.bas + 1):
            for col in range(f.col_debut, f.col_fin + 1):
                if idx == f.haut and col == f.col_debut:
                    ancres[(idx, col)] = f
                else:
                    couvertes.add((idx, col))
    return ancres, couvertes



def _formater_valeur_fusion(fusion, calculs=None):
    """Rend le contenu texte d'une cellule fusionnée : la valeur saisie par l'utilisateur, ou
    — pour une fusion de type « formule » — son résultat évalué (voir fusion_cellule.py)."""
    from ..fusion_cellule import texte_affiche
    return texte_affiche(fusion, calculs)



def limite_lignes_pdf() -> int:
    """Nombre maximal de lignes imprimées dans un PDF de liste de table : AUCUN plafond par défaut (toutes les lignes de la vue sont
    imprimées). Un plafond reste possible en posant GYPLATFORM_PDF_MAX_LIGNES (entier > 0) ; absent, vide ou 0 = illimité."""
    import os
    illimite = 10 ** 9
    try:
        valeur = int(os.environ.get("GYPLATFORM_PDF_MAX_LIGNES", 0) or 0)
    except (TypeError, ValueError):
        return illimite
    return max(50, valeur) if valeur > 0 else illimite


def _largeurs_naturelles(champs, lignes) -> list:
    """Largeur estimée (points) de chaque colonne d'un PDF de liste : titre (en gras, plafonné à 18 caractères : il passe à la ligne aux
    espaces) et plus longue valeur des 300 premières lignes (plafonnée à 40 caractères, la cellule passe à la ligne au-delà). Sert à
    décider du découpage par colonnes."""
    largeurs = []
    for c in champs:
        titre = min(len(str(c.nom)), 18) * 5.0
        valeurs = 10 * 4.3 if c.type_champ == "image" else 0
        if c.type_champ != "image":
            for ligne in lignes[:300]:
                v = ligne.get(c.slug)
                if v is None:
                    continue
                texte = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
                valeurs = max(valeurs, min(len(texte), 40) * 4.3)
        largeurs.append(max(titre, valeurs, 4 * 4.3) + 12)   # +12 : marges intérieures de la cellule (5 + 5) et filet
    return largeurs


def _decouper_colonnes(largeurs, disponible) -> list:
    """Parties de colonnes [(début, fin_exclue)] : on remplit chaque partie, dans l'ordre, tant que la largeur estimée tient sur la page
    (une partie a toujours au moins une colonne)."""
    parties, debut, cumul = [], 0, 0.0
    for i, w in enumerate(largeurs):
        w = min(w, disponible)
        if cumul + w > disponible and i > debut:
            parties.append((debut, i))
            debut, cumul = i, 0.0
        cumul += w
    parties.append((debut, len(largeurs)))
    return parties


def _elements_liste_table(table, champs: list, lignes: list, pagesize, couleurs, tables_calcul: list | None = None, fusions: list | None = None, mises_en_forme: list | None = None, total_lignes: int | None = None, fusions_vue: list | None = None) -> list:
    """Construit la liste de flowables reportlab de la structure/liste d'une
    table (bandeau, titre, tableau des lignes, tables de calcul, cachet,
    signatures) — factorisé hors de generer_pdf_liste_table pour pouvoir
    réutiliser EXACTEMENT le même rendu comme première section du PDF
    "Résumé général" (generer_pdf_resume_general), sans dupliquer la logique
    ni dépendre d'une fusion de PDF séparés (pas de pypdf).

    `fusions` (optionnel) = liste de FusionCelluleTable de cette table (voir
    models.py) : les cellules fusionnées dans la grille "Données" à l'écran
    sont reproduites ici via des SPAN reportlab, avec leur propre contenu
    (valeur_affichee) — même structure conservée entre l'écran et le PDF,
    que ce soit ce tableau de liste ou le "Résumé général" qui le réutilise.

    `fusions_vue` (optionnel) = rectangles de fusion DÉJÀ calculés pour la vue exportée (export_structure.fusions_vue) : valable pour
    n'importe quelle vue (recherche, filtre, tri, lignes limitées) ; il l'emporte sur `fusions`. Les fusions ne sont jamais abandonnées :
    coupées à la dernière ligne imprimée et, sur une table large, coupées entre les parties de colonnes.

    `mises_en_forme` (optionnel) = couleur de ligne / de cellules de chaque élément de `lignes` (même ordre, voir
    couleur_colonne.mise_en_forme) : imprimées comme à l'écran, cellule > ligne > colonne ; une cellule fusionnée prend la couleur de
    sa cellule d'ancrage."""
    styles = _feuille_styles()
    elements = []

    # Plafond de lignes (voir limite_lignes_pdf) : filet de sécurité pour TOUS les appelants (PDF de la table, Résumé général, ontologie).
    # `total_lignes` = nombre de lignes de la vue avant un éventuel plafonnement fait par l'appelant.
    total_vue = max(total_lignes or 0, len(lignes))
    plafond = limite_lignes_pdf()
    from ..export_structure import borner_lignes, couleur_partie, decouper_colonnes, fond_fusion, fusions_defaut, zones_fusion
    if fusions_vue is not None:
        rects = list(fusions_vue)
    else:   # table entière du plus récent au plus ancien (Résumé général) : rang chronologique -> index de ligne
        rects = fusions_defaut(fusions, len(lignes), champs, lignes, nb_total=total_vue)
    if len(lignes) > plafond:
        lignes = lignes[:plafond]
        if mises_en_forme:
            mises_en_forme = mises_en_forme[:plafond]
    tronque = total_vue > len(lignes)
    rects = borner_lignes(rects, len(lignes))   # une fusion qui dépasse la dernière ligne imprimée (plafond, vue réduite) est coupée, pas perdue

    entreprise_table = getattr(table, "entreprise", None)
    bleu = couleurs["entete"]
    fond_ligne_paire = couleurs["ligne_paire"]

    titre_style = ParagraphStyle("TitreListe", parent=styles["Title"], fontSize=16,
        textColor=couleurs["accent"])
    sous_titre_style = ParagraphStyle("SousTitreListe", parent=styles["Normal"],
        fontSize=9, textColor=colors.HexColor("#6b7280"))

    # Equivalent de doc.width pour les marges utilisées par generer_pdf_liste_table
    # / generer_pdf_resume_general (1.5cm de chaque côté) : évite de dépendre
    # d'un objet `doc` que cette fonction, factorisée, ne construit plus elle-même.
    largeur_disponible = pagesize[0] - 3 * cm

    _bandeau_entreprise(entreprise_table, largeur_disponible, elements, table=table, contexte="table")

    elements.append(Paragraph(table.nom, titre_style))
    elements.append(Paragraph(
        tf("engine/reports/_part_004.py:g:251:9", v1=len(lignes), v2=len(champs), v3=__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')),
        sous_titre_style,
    ))
    if tronque:
        elements.append(Paragraph(
            tf("engine/reports/_part_004.py:pdf_tronque", v1=len(lignes), v2=total_vue),
            ParagraphStyle("NoteTronque", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#b45309"))))
    elements.append(Spacer(1, 0.5 * cm))

    if not lignes:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:258:35"), styles["Normal"]))
    else:
        import copy
        from reportlab.platypus import PageBreak
        largeur_dispo = largeur_disponible
        largeur_num = 1.4 * cm   # colonne « # » (numéro de ligne), répétée dans chaque partie d'une table large
        # Table LARGE : si les colonnes ne tiennent pas côte à côte sur la page (largeur estimée d'après les titres et les valeurs), elle est
        # découpée en PARTIES de colonnes, une par page ; le numéro de ligne « # » est répété dans chaque partie pour retrouver la même ligne.
        naturelles = _largeurs_naturelles(champs, lignes)
        parties = _decouper_colonnes(naturelles, largeur_dispo - largeur_num) if sum(naturelles) > largeur_dispo else [(0, len(champs))]
        multi = len(parties) > 1
        n_parties, n_colonnes = len(parties), len(champs)

        def _cellule_texte(v):
            if v is None:
                return ""
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            return str(v)

        def _nom_du_fichier(v):
            """Colonnes fichier / son / vidéo : seulement le NOM du fichier (jamais le chemin « /media/champs/... »)."""
            from urllib.parse import unquote
            if not v:
                return ""
            noms = [unquote(str(x).split("#")[0].split("?")[0].replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]) for x in (v if isinstance(v, list) else [v]) if x]
            return ", ".join(n for n in noms if n)

        def _cellule_image(v, taille_vignette):
            """Vignette RLImage d'une valeur de colonne 'image', ou None si vide / illisible sur disque."""
            if not v:
                return None
            chemin_local = _chemin_local_media(str(v))
            try:
                if chemin_local:
                    return RLImage(chemin_local, width=taille_vignette, height=taille_vignette, kind="proportional")
                if str(v).startswith(("http://", "https://")):
                    return RLImage(str(v), width=taille_vignette, height=taille_vignette, kind="proportional")
            except Exception:
                return None
            return None

        # Les cellules passent par Paragraph (texte) ou RLImage (colonnes de type "image") : retour à la ligne et rendu réel des images.
        cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8, leading=10, textColor=couleurs["texte"])   # couleur de texte de la table (marque)
        head_style = ParagraphStyle("Head", parent=styles["Normal"], fontSize=8,
            leading=10, textColor=colors.white, fontName=_pol("gras"))
        note_style = ParagraphStyle("NotePartie", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#6b7280"))
        titre_partie_style = ParagraphStyle("TitrePartie", parent=styles["Normal"], fontSize=11, leading=14, fontName=_pol("gras"), textColor=couleurs["accent"], spaceAfter=2)

        cell_style_fusion = ParagraphStyle("CellFusion", parent=cell_style, fontName=_pol("gras"))
        from ..couleur_colonne import couleur_texte, teinte_claire, zones_fond_pdf
        couleurs_col_tout = [getattr(c, "couleur", "") or "" for c in champs]   # « #rrggbb » choisi sur la colonne dans la grille (vide = aucune)

        for no_partie, (c_deb, c_fin) in enumerate(parties, 1):
            champs_p = champs[c_deb:c_fin]
            d = 1 if multi else 0   # décalage des colonnes du tableau reportlab : la colonne « # » passe en premier
            if multi:
                if no_partie > 1:
                    elements.append(PageBreak())
                # Bandeau de la partie, dans SA couleur (couleur_partie) : titre + légende en blanc, puis repère de position (les colonnes de
                # cette partie en plein, les autres en gris). Les couleurs déclarées dans la grille s'ajoutent par-dessus dans le tableau.
                coul_p = colors.HexColor(couleur_partie(no_partie))
                st_titre = ParagraphStyle(f"TitrePartie{no_partie}", parent=titre_partie_style, textColor=colors.white)
                st_leg = ParagraphStyle(f"LegPartie{no_partie}", parent=note_style, textColor=colors.white)
                titre_txt = tf("engine/reports/_part_004.py:pdf_part_suite" if no_partie > 1 else "engine/reports/_part_004.py:pdf_part_titre", v1=no_partie, v2=n_parties)
                leg_txt = tf("engine/reports/_part_004.py:pdf_part_legende", v1=c_deb + 1, v2=c_fin, v3=n_colonnes, v4=no_partie, v5=n_parties)
                bandeau = Table([[Paragraph(escape(titre_txt), st_titre)], [Paragraph(escape(leg_txt), st_leg)]], colWidths=[largeur_dispo])
                bandeau.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), coul_p), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, 0), 6),
                                             ("BOTTOMPADDING", (0, 0), (-1, 0), 0), ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
                elements.append(bandeau)
                st_rep = ParagraphStyle(f"Rep{no_partie}", parent=note_style, fontSize=6, leading=8, alignment=1)
                st_rep_on = ParagraphStyle(f"RepOn{no_partie}", parent=st_rep, textColor=colors.white, fontName=_pol("gras"))
                repere = Table([[Paragraph(str(i), st_rep_on if c_deb < i <= c_fin else st_rep) for i in range(1, n_colonnes + 1)]],
                               colWidths=[largeur_dispo / n_colonnes] * n_colonnes)
                repere.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f4f6")), ("BACKGROUND", (c_deb, 0), (c_fin - 1, 0), coul_p),
                                            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1)]))
                elements.append(repere)
                elements.append(Spacer(1, 0.2 * cm))
                total_nat = sum(naturelles[c_deb:c_fin]) or 1
                largeurs = [largeur_num] + [(largeur_dispo - largeur_num) * naturelles[i] / total_nat for i in range(c_deb, c_fin)]
            else:
                largeurs = [largeur_dispo / max(len(champs_p), 1)] * len(champs_p)
            larg_champs = largeurs[d:]

            # Fusions de cellules : une fusion à cheval sur deux parties de colonnes est COUPÉE (chaque partie en garde son morceau).
            fusions_p = decouper_colonnes(rects, c_deb, c_fin) if multi else rects
            ancres_fusion, couvertes_fusion = _carte_fusions(fusions_p, len(lignes))

            couleurs_col = couleurs_col_tout[c_deb:c_fin]
            # Texte d'en-tête noir ou blanc selon le fond : lisible sur toutes les couleurs.
            styles_entete = [ParagraphStyle(f"Head{i}", parent=head_style, textColor=colors.HexColor(couleur_texte(k))) if k else head_style
                             for i, k in enumerate(couleurs_col)]
            entete = ([Paragraph(f"P{no_partie}/{n_parties}", head_style)] if multi else []) + [Paragraph(str(c.nom), styles_entete[i]) for i, c in enumerate(champs_p)]
            data_wrapped = [entete]
            for idx_ligne, ligne in enumerate(lignes):
                row = [Paragraph(str(idx_ligne + 1), cell_style)] if multi else []
                for ci, c in enumerate(champs_p):
                    cle = (idx_ligne, ci)
                    if cle in couvertes_fusion:
                        row.append(Paragraph("", cell_style))   # couverte par une fusion mais pas son ancre : le SPAN reportlab s'en charge
                        continue
                    fusion = ancres_fusion.get(cle)
                    if fusion is not None:
                        row.append(Paragraph(escape(_cellule_texte(fusion.texte)), cell_style_fusion))
                        continue
                    valeur = ligne.get(c.slug)
                    if c.type_champ == "image":
                        img = _cellule_image(valeur, min(1.6 * cm, max(0.8 * cm, larg_champs[ci] - 0.3 * cm)))
                        row.append(img if img is not None else Paragraph("", cell_style))
                    elif c.type_champ in ("fichier", "son", "video"):
                        row.append(Paragraph(escape(_nom_du_fichier(valeur)), cell_style))
                    else:
                        row.append(Paragraph(_cellule_texte(valeur), cell_style))
                data_wrapped.append(row)

            tbl = Table(data_wrapped, colWidths=largeurs, repeatRows=1)
            style_tbl = TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(couleur_partie(no_partie)) if multi else bleu),   # en-tête : couleur de la partie (ou de la marque)
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ])
            for i in range(1, len(data_wrapped)):
                if i % 2 == 0:
                    style_tbl.add("BACKGROUND", (0, i), (-1, i), fond_ligne_paire)
            if multi:   # colonne « # » et bordure dans la couleur de la partie (avant les couleurs déclarées, qui passent par-dessus)
                style_tbl.add("BACKGROUND", (0, 1), (0, -1), colors.HexColor(teinte_claire(couleur_partie(no_partie))))
                style_tbl.add("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor(couleur_partie(no_partie)))
            # Colonnes colorées : en-tête en couleur pleine, cellules en teinte claire (après les lignes paires, avant les fusions qui gardent leur fond).
            for ci, k in enumerate(couleurs_col):
                if k:
                    style_tbl.add("BACKGROUND", (ci + d, 0), (ci + d, 0), colors.HexColor(_flat(k)))
                    if len(data_wrapped) > 1:
                        style_tbl.add("BACKGROUND", (ci + d, 1), (ci + d, -1), colors.HexColor(teinte_claire(k)))
            # Lignes et cellules coloriées à la main dans la grille : posées APRÈS les couleurs de colonne, AVANT les fusions (qui gardent leur fond).
            for idx_ligne, col_debut, col_fin, fond in zones_fond_pdf(mises_en_forme, [c.slug for c in champs_p]):
                style_tbl.add("BACKGROUND", (col_debut + d, idx_ligne + 1), (col_fin + d, idx_ligne + 1), colors.HexColor(fond))   # +1 : la ligne 0 est l'en-tête
            # Structure « Excel » conservée dans le PDF : chaque fusion devient un SPAN reportlab sur le même rectangle (+1 : ligne 0 = en-tête).
            # Chaque cellule d'une fusion garde SA couleur (cellule > ligne > colonne, teinte par défaut sinon) : le SPAN ne fait que joindre
            # le contenu ; le fond est posé zone par zone (export_structure.zones_fusion), la couleur d'une colonne ne déteint pas sur une autre.
            def _fond_zone(i, c):
                forme_c = mises_en_forme[i] if mises_en_forme and i < len(mises_en_forme) else None
                return fond_fusion(couleurs_col[c], forme_c, champs_p[c].slug)
            for (idx_haut, col), fusion in ancres_fusion.items():
                style_tbl.add("SPAN", (fusion.col_debut + d, idx_haut + 1), (fusion.col_fin + d, fusion.bas + 1))
                for z_haut, z_bas, z_col, z_fond in zones_fusion(fusion, _fond_zone):
                    style_tbl.add("BACKGROUND", (z_col + d, z_haut + 1), (z_col + d, z_bas + 1), colors.HexColor(z_fond))
            tbl.setStyle(style_tbl)
            elements.append(tbl)
            if multi:
                if no_partie < n_parties:
                    s_deb, s_fin = parties[no_partie]
                    suite = tf("engine/reports/_part_004.py:pdf_part_continue", v1=no_partie + 1, v2=n_parties, v3=s_deb + 1, v4=s_fin)
                else:
                    suite = t("engine/reports/_part_004.py:pdf_part_fin")
                elements.append(Spacer(1, 0.15 * cm))
                elements.append(Paragraph(escape(suite), note_style))

    _bloc_tables_calcul(tables_calcul or [], styles, elements, couleurs=couleurs)

    _bloc_cachet_simple(entreprise_table, elements, table=table, contexte="table")
    _bloc_signatures_pied(entreprise_table, elements, table=table, contexte="table")

    return elements



def _construire_graphe_ontologie(entreprise, table_courante=None):
    """Construit le graphe des tables de l'entreprise : un noeud par
    TableDynamique, une arête par champ de type "relation" (ChampDynamique.
    relation_vers). Retourne (image_png_bytes, nb_noeuds, nb_aretes, densite).
    Rendu quelle que soit la densité du graphe (aucun seuil/filtrage) —
    seule la taille de la figure s'adapte au nombre de tables. `densite`
    suit la définition standard de networkx (E / (N*(N-1)) pour un graphe
    orienté, 0 si moins de 2 noeuds)."""
    import networkx as nx
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    tables = list(entreprise.tables.all()) if entreprise else []
    graphe = nx.DiGraph()
    for t in tables:
        graphe.add_node(t.id)
    for t in tables:
        for champ in t.champs.filter(type_champ="relation", relation_vers__isnull=False):
            graphe.add_edge(t.id, champ.relation_vers_id)

    n_noeuds = graphe.number_of_nodes()
    n_aretes = graphe.number_of_edges()
    densite = nx.density(graphe) if n_noeuds > 1 else 0.0

    if n_noeuds == 0:
        return None, 0, 0, 0.0

    cote = max(8.0, min(22.0, 3.0 + n_noeuds * 0.7))
    fig, ax = plt.subplots(figsize=(cote, cote * 0.6))
    pos = nx.spring_layout(graphe, seed=42)
    couleurs_noeuds = [
        "#f97316" if (table_courante and nid == table_courante.id) else "#3b82f6"
        for nid in graphe.nodes()
    ]
    nx.draw_networkx_edges(
        graphe, pos, ax=ax, arrows=True, arrowstyle="-|>", arrowsize=14,
        edge_color="#9ca3af", connectionstyle="arc3,rad=0.08",
    )
    nx.draw_networkx_nodes(graphe, pos, ax=ax, node_color=couleurs_noeuds, node_size=1500)
    labels = {t.id: t.nom for t in tables}
    nx.draw_networkx_labels(graphe, pos, labels=labels, ax=ax, font_size=8, font_color="white")
    ax.set_axis_off()
    from matplotlib.lines import Line2D as _L2D
    _ent_leg = ([_L2D([0], [0], marker="o", ls="", markerfacecolor="#f97316", markeredgecolor="#f97316", markersize=7, label="Table courante")] if table_courante else []) \
        + [_L2D([0], [0], marker="o", ls="", markerfacecolor="#3b82f6", markeredgecolor="#3b82f6", markersize=7, label="Autres tables liées")]
    ax.legend(handles=_ent_leg, loc="lower left", fontsize=8, frameon=False)

    tampon = io.BytesIO()
    fig.savefig(tampon, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    tampon.seek(0)
    return tampon.read(), n_noeuds, n_aretes, densite



def _tableau_stats_descriptives(champs: list, lignes: list):
    """Résumé statistique descriptif (pandas describe) des colonnes de la
    table : [entetes, *lignes] prêt à passer à un Table reportlab, ou None
    si aucune ligne. Les colonnes des types numériques du module (nombre,
    decimal, monnaie, pourcentage, note, barre_progression) sont castées en
    numérique avant describe() ; les autres restent traitées comme du
    texte/catégoriel (count/unique/top/freq)."""
    import pandas as pd

    if not lignes:
        return None

    donnees = {c.slug: [ligne.get(c.slug) for ligne in lignes] for c in champs}
    from engine.moteur_donnees import dataframe_depuis
    df = dataframe_depuis(donnees)
    if df.empty:
        return None

    types_numeriques = {"nombre", "decimal", "monnaie", "pourcentage", "note", "barre_progression", "constante"}
    noms_colonnes = {}
    for c in champs:
        if c.type_champ in types_numeriques:
            df[c.slug] = pd.to_numeric(df[c.slug], errors="coerce")
        noms_colonnes[c.slug] = c.nom

    try:
        description = df.describe(include="all").transpose()
    except Exception:
        return None

    description = description.reset_index().rename(columns={"index": "colonne"})
    description["colonne"] = description["colonne"].map(lambda s: noms_colonnes.get(s, s))
    colonnes_ordre = ["colonne"] + [
        col for col in ["count", "unique", "top", "freq", "mean", "std", "min", "25%", "50%", "75%", "max"]
        if col in description.columns
    ]
    description = description[colonnes_ordre]
    return [list(description.columns)] + description.values.tolist()



def _texte_stat(valeur) -> str:
    import pandas as pd

    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)):
        return ""
    if isinstance(valeur, float):
        return f"{valeur:.3g}"
    return str(valeur)



def _elements_blockchain_calculs(table, couleurs) -> list:
    """Lie, dans un même bloc, la densité de la table (nombre de lignes
    authentifiées sur la blockchain d'entreprise par rapport au total),
    l'état de la chaîne de blocs (models.Bloc — une seule chaîne par
    entreprise, filtrée ici sur les transactions concernant CETTE table) et
    les tables de calcul actives. Complète la densité du graphe d'ontologie
    (section précédente) avec une densité d'un autre ordre : celle de la
    preuve blockchain, pas celle des relations entre tables."""
    styles = _feuille_styles()
    normal_style = ParagraphStyle("BlockchainCalculNormal", parent=styles["Normal"], fontSize=9, leading=12)
    elements = []

    nb_lignes = table.enregistrements_groupe.count()
    blocs_entreprise = list(Bloc.objects.filter(entreprise_id=table.entreprise_id).order_by("index"))
    transactions_table = [
        tx for b in blocs_entreprise for tx in b.transactions if tx.get("table_id") == table.id
    ]
    blocs_concernes = {
        b.index for b in blocs_entreprise
        if any(tx.get("table_id") == table.id for tx in b.transactions)
    }
    lignes_authentifiees = {
        tx.get("enregistrement_id") for tx in transactions_table if tx.get("enregistrement_id")
    }
    densite_preuve = (len(lignes_authentifiees) / nb_lignes) if nb_lignes else 0.0
    chaine_valide = Bloc.chaine_valide(table.entreprise_id) if blocs_entreprise else None

    if not blocs_entreprise:
        elements.append(Paragraph(
            _i18n_t("engine/reports/_part_004.py:g:505:13"),
            normal_style,
        ))
    else:
        etat = _pt("engine/reports/_part_004.py:pdf:545:15") if chaine_valide else _i18n_t("engine/reports/_part_004.py:g:510:48")
        elements.append(Paragraph(
            tf("engine/reports/_part_004.py:g:511:13", v1=len(transactions_table), v2=len(blocs_concernes), v3=len(lignes_authentifiees), v4=nb_lignes, v5=densite_preuve, v6=len(blocs_entreprise), v7=etat),
            normal_style,
        ))

    tables_calcul_actives = list(table.tables_calcul.filter(inclure_dans_pdf=True))
    if tables_calcul_actives:
        noms = ", ".join(t.nom for t in tables_calcul_actives)
        elements.append(Spacer(1, 0.15 * cm))
        elements.append(Paragraph(
            tf("engine/reports/_part_004.py:g:524:13", v1=len(tables_calcul_actives), v2=noms),
            normal_style,
        ))
    else:
        elements.append(Spacer(1, 0.15 * cm))
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:531:35"), normal_style))

    return elements


def _elements_dashboards_ontologie_stats(table, champs, lignes, couleurs, membre=None) -> list:
    """Construit les sections 2 (dashboards), 3 (ontologie) et 4 (stats
    descriptives) du résumé général — voir docstring de generer_pdf_resume_general.
    `membre` (facultatif) : sert uniquement à filtrer les actions disponibles
    de la section « Fiche objet » (3 bis) par rôle, comme la vue live
    (ontologie_fiche.assembler_fiche_objet) — None (ex. génération hors
    requête utilisateur, voir cycle.py) laisse simplement cette liste vide."""
    from reportlab.platypus import PageBreak
    from reportlab.lib.pagesizes import landscape
    from reportlab.lib.styles import ParagraphStyle
    from xml.sax.saxutils import escape

    entreprise = table.entreprise
    styles = _feuille_styles()
    titre_style = ParagraphStyle("TitreResume", parent=styles["Title"], fontSize=16, textColor=couleurs["accent"])
    section_style = ParagraphStyle("SectionResume", parent=styles["Heading2"], fontSize=13,
                                    textColor=couleurs["entete"], spaceAfter=8)
    normal_style = ParagraphStyle("NormalResume", parent=styles["Normal"], fontSize=9, leading=12)
    muted_style = ParagraphStyle("MutedResume", parent=styles["Normal"], fontSize=8,
                                  textColor=colors.HexColor("#6b7280"))
    head_style = ParagraphStyle("HeadResume", parent=styles["Normal"], fontSize=8, leading=10,
                                 textColor=colors.white, fontName=_pol("gras"))
    cell_style = ParagraphStyle("CellResume", parent=styles["Normal"], fontSize=8, leading=10)

    largeur_disponible = landscape(A4)[0] - 3 * cm
    elements = []

    # ── Section 2 : un dashboard (analyse DemandeAgent exécutée, celle
    # affichée sous l'onglet "📊 Dashboard" de l'interface — PAS le modèle
    # Skill, qui n'a pas d'écran dédié) par page. La définition des KPI
    # (resultat_json["kpi"]) est lue DEPUIS CETTE ANALYSE PRÉCISE et rendue
    # sur SA page : elle change donc automatiquement avec la page dès qu'on
    # passe à l'analyse suivante, jamais une définition partagée entre pages.
    analyses = list(
        DemandeAgent.objects.filter(entreprise=table.entreprise, table_cible=table, statut="execute")
        .order_by("cree_le").prefetch_related("graphiques")
    )
    # ── Section 1 bis : KPI de la table (models.IndicateurKPI), présentés UNE seule fois ici, avant les
    # dashboards. Les pages BI ci-dessous n'en répètent plus le bandeau (avec_kpis=False), et les KPI propres
    # à chaque analyse restent sur la page de cette analyse (section suivante).
    elements.append(PageBreak())
    _bandeau_entreprise(entreprise, largeur_disponible, elements, table=table, contexte="table")
    elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:578:31"), section_style))
    elements_kpis = _elements_kpis_table(table, largeur_disponible)
    if elements_kpis:
        elements += elements_kpis
    else:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:583:35"), normal_style))

    elements.append(PageBreak())
    _bandeau_entreprise(entreprise, largeur_disponible, elements, table=table, contexte="table")
    elements.append(Paragraph(_pt("engine/reports/_part_004.py:pdf:617:30"), section_style))
    if not analyses:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:589:35"), normal_style))
    for i, analyse in enumerate(analyses):
        if i > 0:
            elements.append(PageBreak())
            _bandeau_entreprise(entreprise, largeur_disponible, elements, table=table, contexte="table")
        elements.append(Paragraph(_ptf("engine/reports/_part_004.py:pdf:624:34", v1=analyse.texte), titre_style))
        resultat = analyse.resultat_json or {}
        for cle, libelle in (("pourquoi", _pt("engine/reports/_part_004.py:pdf:626:42")), ("comment", _pt("engine/reports/_part_004.py:pdf:626:67")),
                              ("explication", _pt("engine/reports/_part_004.py:pdf:627:46")), ("analyse_predictive", _i18n_t("engine/reports/_part_004.py:g:597:86")),
                              ("conclusion", _pt("engine/reports/_part_004.py:pdf:628:45")), ("recommandations", _pt("engine/reports/_part_004.py:pdf:628:80"))):
            valeur = resultat.get(cle)
            if valeur:
                elements.append(Paragraph(f"<b>{libelle} :</b> {valeur}", normal_style))
        # KPI : toujours rendu ICI, sur la page de CETTE analyse — jamais
        # factorisé/mutualisé ailleurs dans le document — pour que la
        # définition affichée soit sans ambiguïté celle de ce dashboard précis.
        # Rendu INDÉPENDANT des graphiques : une analyse sans aucun graphique affiche quand même ses KPI.
        kpi = resultat.get("kpi")
        if kpi:
            def _fmt_v(v):
                return f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", " ") if isinstance(v, float) else str(v)
            if isinstance(kpi, dict):
                texte_kpi = ", ".join(f"{k} : {_fmt_v(v)}" for k, v in kpi.items())
            elif isinstance(kpi, (list, tuple)):
                texte_kpi = ", ".join(_fmt_v(v) for v in kpi)
            else:
                texte_kpi = str(kpi)
            elements.append(Paragraph(tf("engine/reports/_part_004.py:g:615:39", v1=escape(texte_kpi)), normal_style))
        elements.append(Spacer(1, 0.3 * cm))

        graphiques = [
            {"image_base64": g.image_base64, "titre": g.legende} for g in analyse.graphiques.all()
        ]
        largeur_img = (largeur_disponible - 1 * cm) / 2
        lignes_images, ligne_courante = [], []
        for g in graphiques:
            try:
                img = RLImage(io.BytesIO(base64.b64decode(g["image_base64"])),
                              width=largeur_img, height=largeur_img * 0.6, kind="proportional")
            except Exception:
                continue
            ligne_courante.append([img, Paragraph(g.get("titre", ""), muted_style)])
            if len(ligne_courante) == 2:
                lignes_images.append(ligne_courante)
                ligne_courante = []
        if ligne_courante:
            ligne_courante.append("")
            lignes_images.append(ligne_courante)
        if lignes_images:
            tbl_img = Table(lignes_images, colWidths=[largeur_img + 0.3 * cm] * 2)
            tbl_img.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]))
            elements.append(tbl_img)

    # ── Section 2 bis : tableaux de bord BI de cette table, un par page (même rendu que leur export PDF) ──
    from ..vue_groupe import ids_groupe
    from ..models import TableauBordBI
    for tableau_bord in TableauBordBI.objects.filter(table_source_id__in=ids_groupe(table)).order_by("ordre", "id"):
        elements.append(PageBreak())
        elements += _elements_dashboard_bi(tableau_bord, largeur_disponible, avec_kpis=False,
                                           hauteur_page=landscape(A4)[1] - 3.3 * cm - 4.5 * cm)   # hauteur utile d'une page paysage moins bandeau et titre

    # ── Section 3 : ontologie (graphe complet des tables + densité) ────────
    elements.append(PageBreak())
    _bandeau_entreprise(entreprise, largeur_disponible, elements, table=table, contexte="table")
    elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:656:31"), section_style))
    image_graphe, n_noeuds, n_aretes, densite = _construire_graphe_ontologie(entreprise, table_courante=table)
    if image_graphe:
        largeur_graphe = largeur_disponible * 0.85
        elements.append(RLImage(io.BytesIO(image_graphe), width=largeur_graphe,
                                 height=largeur_graphe * 0.6, kind="proportional"))
        elements.append(Spacer(1, 0.3 * cm))
        elements.append(Paragraph(
            tf("engine/reports/_part_004.py:g:663:13", v1=n_noeuds, v2=n_aretes, v3=densite, v4=table.nom),
            normal_style,
        ))
    else:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:670:35"), normal_style))

    elements.append(Spacer(1, 0.4 * cm))
    elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:673:31"), section_style))
    elements += _elements_blockchain_calculs(table, couleurs)

    # ── Section 3 bis : fiche objet (relations, actions disponibles,
    # documents, activité récente) — cahier des charges Ontology section 10
    # (« Applications object-centric ») : ce PDF ne doit pas se contenter
    # d'afficher la structure/les données de la table, il doit aussi
    # exposer ce que l'Ontology sait de CET objet précis, comme l'onglet
    # "Fiche" de l'interface Ontologie (même assemblage, zéro duplication —
    # voir ontologie_fiche.assembler_fiche_objet).
    from ..ontologie_fiche import assembler_fiche_objet
    fiche = assembler_fiche_objet(entreprise, f"table:{table.id}", membre=membre)
    elements.append(Spacer(1, 0.4 * cm))
    elements.append(Paragraph(_pt("engine/reports/_part_004.py:pdf:715:30"), section_style))
    if "erreur" in fiche:
        elements.append(Paragraph(fiche["erreur"], normal_style))
    else:
        rel = fiche["relations"]
        elements.append(Paragraph(
            tf("engine/reports/_part_004.py:g:691:13", v1=rel.get('total', 0)),
            normal_style,
        ))
        actions_disp = fiche["actions_disponibles"]
        if actions_disp:
            elements.append(Paragraph(
                _pt("engine/reports/_part_004.py:pdf:727:16") + ", ".join(escape(a["nom"]) for a in actions_disp) + ".",
                normal_style,
            ))
        documents = fiche["documents"]
        if documents:
            elements.append(Paragraph(
                tf("engine/reports/_part_004.py:g:704:17", v1=len(documents)) +
                ", ".join(escape(d.get("titre") or d["type_document"]) for d in documents) + ".",
                normal_style,
            ))
        activite = fiche["activite_recente"]
        if activite:
            elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:710:39"), normal_style))
            for a in activite[:8]:
                elements.append(Paragraph(
                    f"— {escape(a['action'])} ({a['horodatage'].strftime('%d/%m/%Y %H:%M')})",
                    muted_style,
                ))

    # ── Section 4 : résumé statistique descriptif ───────────────────────────
    elements.append(PageBreak())
    _bandeau_entreprise(entreprise, largeur_disponible, elements, table=table, contexte="table")
    elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:720:31"), section_style))
    tableau_stats = _tableau_stats_descriptives(champs, lignes)
    if not tableau_stats or len(tableau_stats) <= 1:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:723:35"), normal_style))
    else:
        entetes, *corps = tableau_stats
        data_wrapped = [[Paragraph(str(h).capitalize(), head_style) for h in entetes]]
        data_wrapped += [[Paragraph(_texte_stat(v), cell_style) for v in row] for row in corps]
        largeur_col = largeur_disponible / len(entetes)
        tbl_stats = Table(data_wrapped, colWidths=[largeur_col] * len(entetes), repeatRows=1)
        style_stats = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), couleurs["entete"]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ])
        for i in range(1, len(data_wrapped)):
            if i % 2 == 0:
                style_stats.add("BACKGROUND", (0, i), (-1, i), couleurs["ligne_paire"])
        tbl_stats.setStyle(style_stats)
        elements.append(tbl_stats)

    return elements


# ─────────────────────────────────────────────────────────────
# Section 5 du résumé général : « tout ce qui existe » autour de la table.
# L'architecture est figée : chaque fonctionnalité rattachée à une table (ou à
# son entreprise) doit y apparaître. Chaque sous-section est isolée : une erreur
# dans l'une n'empêche jamais la génération du PDF ni des autres sous-sections.
# ─────────────────────────────────────────────────────────────
def _tableau_resume_simple(entetes, lignes, largeur, couleurs, head_style, cell_style):
    """Tableau paysage à en-tête coloré, cellules en Paragraph (retour à la ligne automatique)."""
    from xml.sax.saxutils import escape
    donnees = [[Paragraph(escape(str(h)), head_style) for h in entetes]]
    donnees += [[Paragraph(escape(str(v if v not in (None, "") else "—")), cell_style) for v in ligne] for ligne in lignes]
    tbl = Table(donnees, colWidths=[largeur / len(entetes)] * len(entetes), repeatRows=1)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), couleurs["entete"]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 4),
    ])
    for i in range(1, len(donnees)):
        if i % 2 == 0:
            style.add("BACKGROUND", (0, i), (-1, i), couleurs["ligne_paire"])
    tbl.setStyle(style)
    return tbl


def _sous_section_resume(titre, construire, styles_resume, elements):
    """Exécute `construire()` (retourne une liste d'éléments reportlab) ; en cas d'erreur, écrit une
    ligne d'indisponibilité à la place au lieu de faire échouer tout le PDF."""
    import logging
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph(titre, styles_resume["sous_titre"]))
    try:
        elements += construire()
    except Exception:
        logging.getLogger(__name__).exception(_i18n_t("engine/reports/_part_004.py:g:778:47"), titre)
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:780:35"), styles_resume["muted"]))


def _elements_analyses_donnees(table, largeur, couleurs, inclure=("series", "statistiques", "geospatial"), avec_bandeau=True, saut_page=True, avec_titre=True) -> list:
    """Section « Analyses de la table » du résumé général : séries temporelles, statistiques et géospatial, lus EN DIRECT sur les données
    actuelles de la table (mêmes moteurs que les onglets : engine/series_donnees, statistiques_donnees, geo_donnees). Chaque bloc est isolé :
    un moteur en échec ou une table sans la structure attendue donne une ligne d'explication, jamais un PDF cassé."""
    from datetime import datetime
    from reportlab.platypus import PageBreak
    from reportlab.lib.styles import ParagraphStyle
    from xml.sax.saxutils import escape

    base = _feuille_styles()
    st = {
        "section": ParagraphStyle("SectionAna", parent=base["Heading2"], fontSize=13, textColor=couleurs["entete"], spaceAfter=8),
        "sous_titre": ParagraphStyle("SousTitreAna", parent=base["Heading3"], fontSize=10.5, textColor=couleurs["accent"], spaceAfter=4),
        "normal": ParagraphStyle("NormalAna", parent=base["Normal"], fontSize=9, leading=12),
        "muted": ParagraphStyle("MutedAna", parent=base["Normal"], fontSize=8, textColor=colors.HexColor("#6b7280")),
        "head": ParagraphStyle("HeadAna", parent=base["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName=_pol("gras")),
        "cell": ParagraphStyle("CellAna", parent=base["Normal"], fontSize=8, leading=10),
    }
    nombre = lambda v, d=2: "—" if v is None else (f"{v:,.{d}f}".replace(",", " ") if isinstance(v, (int, float)) else str(v))
    quand = lambda ts: "—" if ts is None else datetime.fromtimestamp(float(ts)).strftime("%d/%m/%Y %H:%M")
    tableau = lambda entetes, lignes: _tableau_resume_simple(entetes, lignes, largeur, couleurs, st["head"], st["cell"])
    elements = [PageBreak()] if saut_page else []
    if avec_bandeau:
        _bandeau_entreprise(table.entreprise, largeur, elements, table=table, contexte="table")
    if avec_titre:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:806:31"), st["section"]))

    def series():
        from .. import series_donnees
        if not series_donnees.decrire(table):
            return [Paragraph(_i18n_t("engine/reports/_part_004.py:g:811:31"), st["normal"])]
        res = series_donnees.analyser_table(table)
        lignes = []
        for r in res.get("series") or []:
            if r.get("erreur"):
                lignes.append([r.get("colonne") or r.get("nom"), "—", "—", r.get("erreur") if isinstance(r.get("erreur"), str) else _i18n_t("engine/reports/_part_004.py:g:816:133"), "—", "—"])
                continue
            tend, q, mod, prev = r.get("tendance") or {}, r.get("qualite") or {}, r.get("modele_retenu") or {}, r.get("prevision") or {}
            val = prev.get("valeur") or []
            lignes.append([r.get("colonne") or r.get("nom"), _ptf("engine/reports/_part_004.py:pdf:851:61", v1=r.get('n', '—'), v2=(r.get('frequence') or {}).get('libelle', '—')),
                           f"{tend.get('sens', '—')} ({nombre(tend.get('variation_totale'))} {tend.get('unite', '')})".strip(),
                           mod.get("methode_libelle", "—"),
                           _ptf("engine/reports/_part_004.py:pdf:854:27", v1=nombre(q.get('rmse'))) + (_ptf("engine/reports/_part_004.py:pdf:854:62", v1=nombre(q.get('mape'), 1)) if q.get("mape") is not None else ""),
                           tf("engine/reports/_part_004.py:g:824:28", v1=nombre(val[-1]), v2=prev.get('horizon')) if val else "—"])
        if not lignes:
            return [Paragraph(_i18n_t("engine/reports/_part_004.py:g:826:31"), st["normal"])]
        return [tableau([_i18n_t("engine/reports/_part_004.py:g:827:26"), _i18n_t("engine/reports/_part_004.py:g:827:35"), _pt("engine/reports/_part_004.py:pdf:858:123"), _i18n_t("engine/reports/_part_004.py:g:827:58"), _i18n_t("engine/reports/_part_004.py:g:827:77"), _i18n_t("engine/reports/_part_004.py:g:827:88")], lignes)]
    if "series" in inclure:
        elements += [Spacer(1, 0.2 * cm), Paragraph(_i18n_t("engine/reports/_part_004.py:g:828:49"), st["sous_titre"])]
        try:
            elements += series()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(_i18n_t("engine/reports/_part_004.py:g:832:47"), table.id)
            elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:834:35"), st["muted"]))

    def statistiques():
        from .. import statistiques_donnees as sd
        fiche = sd.decrire(table)
        if not fiche:
            return [Paragraph(_i18n_t("engine/reports/_part_004.py:g:840:31"), st["normal"])]
        from ..views_statistiques import calculer_descriptives
        res = calculer_descriptives([table], [f"{table.id}:{c['slug']}" for c in fiche["colonnes"]])   # même fonction que la page Statistiques
        syn = res.get("synthese") or {}
        out = []
        qualite = [[q.get("nom"), q.get("n"), q.get("manquants"), f"{nombre(q.get('taux_manquants'), 1)} %"] for q in (syn.get("qualite") or [])[:30]]
        if qualite:
            out.append(tableau([_i18n_t("engine/reports/_part_004.py:g:847:33"), _i18n_t("engine/reports/_part_004.py:g:847:44"), _pt("engine/reports/_part_004.py:pdf:879:130"), _pt("engine/reports/_part_004.py:pdf:879:144")], qualite))
            out.append(Spacer(1, 0.2 * cm))
        for c in (syn.get("constats") or [])[:15]:
            out.append(Paragraph("• " + escape(str(c.get("texte", ""))), st["normal"]))
        return out or [Paragraph(_i18n_t("engine/reports/_part_004.py:g:851:34"), st["normal"])]
    if "statistiques" in inclure:
        elements += [Spacer(1, 0.3 * cm), Paragraph(_pt("engine/reports/_part_004.py:pdf:885:52"), st["sous_titre"])]
        try:
            elements += statistiques()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(_i18n_t("engine/reports/_part_004.py:g:856:47"), table.id)
            elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:858:35"), st["muted"]))

    def geospatial():
        from .. import geo_donnees
        res = geo_donnees.analyser_table(table)
        if res is None:
            return [Paragraph(_i18n_t("engine/reports/_part_004.py:g:864:31"), st["normal"])]
        if res.get("vide"):
            return [Paragraph(res.get("message") or _i18n_t("engine/reports/_part_004.py:g:866:53"), st["normal"])]
        r, pl = res["resume"], res.get("plage") or {}
        out = [tableau([_pt("engine/reports/_part_004.py:pdf:901:24"), _pt("engine/reports/_part_004.py:pdf:901:41"), _pt("engine/reports/_part_004.py:pdf:901:54"), _pt("engine/reports/_part_004.py:pdf:901:73"), _i18n_t("engine/reports/_part_004.py:g:868:88"), _i18n_t("engine/reports/_part_004.py:g:868:110")],
                       [[r["objets"], r["positions"], _ptf("engine/reports/_part_004.py:pdf:902:54", v1=nombre((r.get('distance_m') or 0) / 1000, 1)), r["rencontres"], r["collisions_prevues"],
                         f"{nombre((r.get('proba_max') or 0) * 100, 0)} %"]])]
        if pl.get("debut_ts") is not None:
            out += [Spacer(1, 0.2 * cm), Paragraph(tf("engine/reports/_part_004.py:g:872:52", v1=quand(pl['debut_ts']), v2=quand(pl['fin_ts']))
                                                   + (_i18n_t("engine/reports/_part_004.py:g:873:55") if pl.get("tronquee") else ""), st["normal"])]
        objets = [[o.get("nom") or o.get("id"), o.get("positions"), quand(o.get("premiere_ts")), quand(o.get("derniere_ts")),
                   f"{o['actuelle'][0]:.5f}, {o['actuelle'][1]:.5f}" if o.get("actuelle") else "—"] for o in (pl.get("objets") or [])[:25]]
        if objets:
            out += [Spacer(1, 0.2 * cm), tableau([_pt("engine/reports/_part_004.py:pdf:910:50"), _pt("engine/reports/_part_004.py:pdf:910:59"), _i18n_t("engine/reports/_part_004.py:g:877:73"), _i18n_t("engine/reports/_part_004.py:g:877:94"), _pt("engine/reports/_part_004.py:pdf:910:170")], objets)]
        alertes = [[a.get("type", "").replace("_", " "), a.get("gravite", ""), a.get("a", ""), a.get("b", "")] for a in (res.get("alertes") or [])[:10]]
        if alertes:
            out += [Spacer(1, 0.2 * cm), tableau([_pt("engine/reports/_part_004.py:pdf:913:50"), _i18n_t("engine/reports/_part_004.py:g:880:61"), _pt("engine/reports/_part_004.py:pdf:913:109"), _pt("engine/reports/_part_004.py:pdf:913:118")], alertes)]
        return out
    if "geospatial" in inclure:
        elements += [Spacer(1, 0.3 * cm), Paragraph(_i18n_t("engine/reports/_part_004.py:g:882:49"), st["sous_titre"])]
        try:
            elements += geospatial()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(_i18n_t("engine/reports/_part_004.py:g:886:47"), table.id)
            elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:888:35"), st["muted"]))
    return elements


def _elements_architecture_complete(table, largeur, couleurs) -> list:
    """Section 5 — structure, colonnes calculées, documents, skills, modèles IA, tâches, partages,
    imports, accès client, puis vue d'ensemble de l'entreprise (comptages uniquement, jamais de secret)."""
    from reportlab.platypus import PageBreak
    from reportlab.lib.styles import ParagraphStyle
    from ..models import (ChampDynamique, DocumentGenere, FormuleChamp, LienChamp, ModeleIA, PartageInterAgent,
                          SignatureDocument, Skill, TacheImport, TachePlanifiee)

    base = _feuille_styles()
    styles_resume = {
        "section": ParagraphStyle("SectionArchi", parent=base["Heading2"], fontSize=13, textColor=couleurs["entete"], spaceAfter=8),
        "sous_titre": ParagraphStyle("SousTitreArchi", parent=base["Heading3"], fontSize=10.5, textColor=couleurs["accent"], spaceAfter=4),
        "normal": ParagraphStyle("NormalArchi", parent=base["Normal"], fontSize=9, leading=12),
        "muted": ParagraphStyle("MutedArchi", parent=base["Normal"], fontSize=8, textColor=colors.HexColor("#6b7280")),
        "head": ParagraphStyle("HeadArchi", parent=base["Normal"], fontSize=8, leading=10, textColor=colors.white, fontName=_pol("gras")),
        "cell": ParagraphStyle("CellArchi", parent=base["Normal"], fontSize=8, leading=10),
    }
    entreprise = table.entreprise
    elements = [PageBreak()]
    _bandeau_entreprise(entreprise, largeur, elements, table=table, contexte="table")
    elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:912:31"), styles_resume["section"]))

    def tableau(entetes, lignes):
        return _tableau_resume_simple(entetes, lignes, largeur, couleurs, styles_resume["head"], styles_resume["cell"])

    def vide(message):
        return [Paragraph(message, styles_resume["normal"])]

    def date(valeur):
        return valeur.strftime("%d/%m/%Y %H:%M") if valeur else "—"

    def structure():
        lignes = [
            (_pt("engine/reports/_part_004.py:pdf:959:13"), _pt("engine/reports/_part_004.py:pdf:959:25") if table.est_nosql else _pt("engine/reports/_part_004.py:pdf:959:64")),
            (_i18n_t("engine/reports/_part_004.py:g:926:14"), tf("engine/reports/_part_004.py:g:926:52", v1=table.champs.count(), v2=table.enregistrements_groupe.count())),
            (_pt("engine/reports/_part_004.py:pdf:961:13"), _i18n_t("engine/reports/_part_004.py:g:927:25") + table.nom_fichier_origine if table.provient_fichier and table.nom_fichier_origine
             else (_ptf("engine/reports/_part_004.py:pdf:962:19", v1=table.connexion_externe_origine.nom, v2=table.nom_source_distante)
                   if table.connexion_externe_origine_id else _i18n_t("engine/reports/_part_004.py:g:929:63"))),
            (_i18n_t("engine/reports/_part_004.py:g:930:14"), (_ptf("engine/reports/_part_004.py:pdf:964:63", v1=table.groupe.nom, v2=table.partie_index, v3=table.partie_total)
                                  if table.groupe_id else _i18n_t("engine/reports/_part_004.py:g:931:59"))),
            (_i18n_t("engine/reports/_part_004.py:g:932:14"), (_ptf("engine/reports/_part_004.py:pdf:966:63", v1=table.table_extension_colonnes.nom)
                                       if table.table_extension_colonnes_id else _i18n_t("engine/reports/_part_004.py:g:933:82"))),
            (_i18n_t("engine/reports/_part_004.py:g:934:14"), str(table.fusions_cellules.count())),
            (_i18n_t("engine/reports/_part_004.py:g:935:14"), _i18n_t("engine/reports/_part_004.py:g:935:30") if table.expose_client else _i18n_t("engine/reports/_part_004.py:g:935:80")),
            (_pt("engine/reports/_part_004.py:pdf:970:13"), str(SignatureDocument.objects.filter(table=table).count())),
        ]
        return [tableau([_i18n_t("engine/reports/_part_004.py:g:938:26"), _i18n_t("engine/reports/_part_004.py:g:938:37")], lignes)]
    _sous_section_resume(_i18n_t("engine/reports/_part_004.py:g:939:26"), structure, styles_resume, elements)

    def colonnes_calculees():
        formules = FormuleChamp.objects.filter(table=table).select_related("champ_cible")
        liens = LienChamp.objects.filter(champ_source__table=table).count()
        lignes = [(f.champ_cible.nom if f.champ_cible_id else "—", f.operateur, ", ".join(f.operandes_slugs or [])) for f in formules]
        elements_loc = [Paragraph(tf("engine/reports/_part_004.py:g:944:35", v1=len(lignes), v2=liens), styles_resume["normal"])]
        if lignes:
            elements_loc.append(tableau([_i18n_t("engine/reports/_part_004.py:g:947:42"), _i18n_t("engine/reports/_part_004.py:g:947:53"), _i18n_t("engine/reports/_part_004.py:g:947:66")], lignes))
        return elements_loc
    _sous_section_resume(_i18n_t("engine/reports/_part_004.py:g:949:26"), colonnes_calculees, styles_resume, elements)

    def documents():
        docs = list(DocumentGenere.objects.filter(table_cible=table).order_by("-cree_le")[:40])
        if not docs:
            return vide(_i18n_t("engine/reports/_part_004.py:g:954:25"))
        return [tableau([_i18n_t("engine/reports/_part_004.py:g:955:26"), _pt("engine/reports/_part_004.py:pdf:989:74"), _i18n_t("engine/reports/_part_004.py:g:955:43"), _i18n_t("engine/reports/_part_004.py:g:955:53")],
                        [(d.titre, d.type_document, d.statut, date(d.cree_le)) for d in docs])]
    _sous_section_resume(_i18n_t("engine/reports/_part_004.py:g:957:26"), documents, styles_resume, elements)

    def skills():
        liste = list(Skill.objects.filter(table=table).order_by("-cree_le")[:40])
        if not liste:
            return vide(_i18n_t("engine/reports/_part_004.py:g:962:25"))
        return [tableau([_i18n_t("engine/reports/_part_004.py:g:963:26"), _i18n_t("engine/reports/_part_004.py:g:963:33"), _i18n_t("engine/reports/_part_004.py:g:963:43")], [(k.nom, k.statut, date(k.execute_le)) for k in liste])]
    _sous_section_resume(_pt("engine/reports/_part_004.py:pdf:998:25"), skills, styles_resume, elements)

    def modeles_ia():
        liste = list(ModeleIA.objects.filter(entreprise=entreprise, tables_source=table).order_by("-cree_le")[:40])
        if not liste:
            return vide(_i18n_t("engine/reports/_part_004.py:g:969:25"))
        return [tableau([_i18n_t("engine/reports/_part_004.py:g:970:26"), _pt("engine/reports/_part_004.py:pdf:1004:74"), _pt("engine/reports/_part_004.py:pdf:1004:82"), _i18n_t("engine/reports/_part_004.py:g:970:50"), _i18n_t("engine/reports/_part_004.py:g:970:60")],
                        [(m.nom, m.type_modele, m.colonne_cible, m.statut, date(m.entraine_le)) for m in liste])]
    _sous_section_resume(_i18n_t("engine/reports/_part_004.py:g:972:26"), modeles_ia, styles_resume, elements)

    def taches():
        liste = list(TachePlanifiee.objects.filter(entreprise=entreprise, condition_table=table).order_by("nom")[:40])
        if not liste:
            return vide(_i18n_t("engine/reports/_part_004.py:g:977:25"))
        return [tableau([_i18n_t("engine/reports/_part_004.py:g:978:26"), _i18n_t("engine/reports/_part_004.py:g:978:33"), _pt("engine/reports/_part_004.py:pdf:1012:123"), _i18n_t("engine/reports/_part_004.py:g:978:60"), _i18n_t("engine/reports/_part_004.py:g:978:70")],
                        [(x.nom, x.mode_declenchement, "oui" if x.actif else "non", x.statut, date(x.derniere_execution)) for x in liste])]
    _sous_section_resume(_i18n_t("engine/reports/_part_004.py:g:980:26"), taches, styles_resume, elements)

    def partages():
        liste = list(PartageInterAgent.objects.filter(table=table).select_related("entreprise_cible").order_by("-cree_le")[:40])
        if not liste:
            return vide(_i18n_t("engine/reports/_part_004.py:g:985:25"))
        return [tableau([_pt("engine/reports/_part_004.py:pdf:1020:25"), _pt("engine/reports/_part_004.py:pdf:1020:52"), _i18n_t("engine/reports/_part_004.py:g:986:61"), _pt("engine/reports/_part_004.py:pdf:1020:109")],
                        [(p.entreprise_cible.nom, p.type_objet, "oui" if p.actif else "non", date(p.cree_le)) for p in liste])]
    _sous_section_resume(_pt("engine/reports/_part_004.py:pdf:1022:25"), partages, styles_resume, elements)

    def imports():
        liste = list(TacheImport.objects.filter(table=table).order_by("-cree_le")[:20])
        if not liste:
            return vide(_i18n_t("engine/reports/_part_004.py:g:993:25"))
        return [tableau([_pt("engine/reports/_part_004.py:pdf:1028:25"), _i18n_t("engine/reports/_part_004.py:g:994:36"), _i18n_t("engine/reports/_part_004.py:g:994:47"), _i18n_t("engine/reports/_part_004.py:g:994:57")],
                        [(i.source, i.nom_fichier, i.statut, date(i.termine_le)) for i in liste])]
    _sous_section_resume(_i18n_t("engine/reports/_part_004.py:g:996:26"), imports, styles_resume, elements)

    # ── Vue d'ensemble de l'entreprise : comptages uniquement ──
    elements.append(PageBreak())
    _bandeau_entreprise(entreprise, largeur, elements, table=table, contexte="table")
    elements.append(Paragraph(_i18n_t("engine/reports/_part_004.py:g:1001:31"), styles_resume["section"]))

    def compter(nom_modele, **filtres):
        from .. import models as _m
        try:
            return str(getattr(_m, nom_modele).objects.filter(**filtres).count())
        except Exception:
            return "n/d"

    def vue_entreprise():
        from ..models import MembreEntreprise
        roles = {code: MembreEntreprise.objects.filter(entreprise=entreprise, role=code).count()
                 for code, _libelle in MembreEntreprise.ROLE_CHOICES}
        libelles = dict(MembreEntreprise.ROLE_CHOICES)
        lignes = [
            (_i18n_t("engine/reports/_part_004.py:g:1016:14"), compter("TableDynamique", entreprise=entreprise)),
            (_i18n_t("engine/reports/_part_004.py:g:1017:14"), compter("GroupeTables", entreprise=entreprise)),
            (_pt("engine/reports/_part_004.py:pdf:1052:13"), " · ".join(f"{libelles[c]} : {n}" for c, n in roles.items())),
            (_i18n_t("engine/reports/_part_004.py:g:1019:14"),
             f"{compter('GroupePartage', entreprise=entreprise)} / {compter('GroupePartageLigne', entreprise=entreprise)} / "
             f"{compter('GroupePartageCellule', entreprise=entreprise)}"),
            (_i18n_t("engine/reports/_part_004.py:g:1022:14"), f"{compter('PartageInterAgent', entreprise_source=entreprise)} / {compter('PartageInterAgent', entreprise_cible=entreprise)}"),
            ("KPI", compter("IndicateurKPI", entreprise=entreprise)),
            (_i18n_t("engine/reports/_part_004.py:g:1024:14"), compter("PageOntologie", entreprise=entreprise)),
            (_i18n_t("engine/reports/_part_004.py:g:1025:14"), compter("ModeleIA", entreprise=entreprise)),
            (_i18n_t("engine/reports/_part_004.py:g:1026:14"), compter("TachePlanifiee", entreprise=entreprise)),
            (_i18n_t("engine/reports/_part_004.py:g:1027:14"), compter("ConnexionExterne", entreprise=entreprise)),
            (_i18n_t("engine/reports/_part_004.py:g:1028:14"), compter("Bloc", entreprise_id=entreprise.id)),
            (_pt("engine/reports/_part_004.py:pdf:1063:13"), compter("Wallet", membre__entreprise=entreprise)),
            (_i18n_t("engine/reports/_part_004.py:g:1030:14"), compter("CleAPI", entreprise=entreprise, actif=True)),
            (_i18n_t("engine/reports/_part_004.py:g:1031:14"), compter("ArchiveCycle", entreprise=entreprise)),
        ]
        return [tableau([_i18n_t("engine/reports/_part_004.py:g:1033:26"), _i18n_t("engine/reports/_part_004.py:g:1033:37")], lignes)]
    _sous_section_resume(_i18n_t("engine/reports/_part_004.py:g:1034:26"), vue_entreprise, styles_resume, elements)
    return elements



def generer_pdf_resume_general(table, membre=None, utilisateur=None) -> bytes:
    """Bouton "Résumé général" (un par table, tous types confondus). PDF
    paysage unique combinant, dans cet ordre : (1) la structure/liste de la
    table telle qu'elle est déjà rendue par generer_pdf_liste_table, (1 bis) une section « KPI » unique (KPI de
    la table, avant les dashboards ; les pages BI n'en répètent plus le bandeau), (2) un
    dashboard par page (chaque analyse exécutée de cette table — celle de
    l'onglet "📊 Dashboard" —, avec ses graphiques et SA propre définition de
    KPI, liée à cette page précise) puis un tableau de bord BI par page
    (bouton "📈 BI", même rendu que son export PDF), (3) l'ontologie : image du graphe complet des
    tables de l'entreprise et ses métriques de densité, PUIS un bloc liant
    cette densité à la blockchain d'entreprise (models.Bloc, transactions de
    CETTE table, intégrité de la chaîne) et aux tables de calcul actives —
    voir _elements_blockchain_calculs —, (4) un résumé statistique descriptif
    de la table, (5) l'architecture complète (structure, colonnes calculées, documents, skills, modèles IA, tâches,
    partages, imports) et la vue d'ensemble de l'entreprise. Pas de cartes (retirées à la demande, 2026-09-24). Voir
    _elements_dashboards_ontologie_stats pour le détail des sections 2 à 4.
    Tout est assemblé en un seul doc.build() : aucune fusion de PDF.
    `utilisateur` (facultatif) : résumé limité à l'environnement de cet agent (lignes qu'il a créées) ; None = toutes les lignes (administrateur)."""
    _police_activer(getattr(table, "entreprise", None))
    from reportlab.lib.pagesizes import landscape

    champs = list(table.champs.order_by("ordre"))
    qs_enregistrements = table.enregistrements_groupe  # tableau : tout le groupe
    if utilisateur is not None:
        from .. import propriete_tables
        qs_enregistrements = propriete_tables.filtrer_lignes(qs_enregistrements, utilisateur)
    enregistrements = list(qs_enregistrements.order_by("-cree_le"))
    from .. import parquet_store
    lignes, mises_en_forme = parquet_store.lignes_affichage_pdf(table, enregistrements)   # valeurs : Parquet → texte CSV (pandas) → table par regex (pdf_texte.py) ; couleurs de lignes/cellules, même ordre que `lignes`
    tables_calcul = list(
        table.tables_calcul.filter(inclure_dans_pdf=True)
        .prefetch_related("lignes__formule", "lignes__champ_cible", "lignes__champs_cibles")
    )
    fusions = list(table.fusions_cellules.all())
    from ..fusion_cellule import resynchroniser
    resynchroniser(table, fusions)
    from .. import export_structure   # fusions retrouvées par ligne : valable aussi pour un résumé limité à l'environnement d'un agent
    fusions_vue = export_structure.fusions_vue(table, fusions, [e.id for e in enregistrements], champs,
                                               calculs=export_structure.calculs_formules(table, fusions, champs))

    pagesize, marges = _format_page("resume_general")     # A4 paysage, plusieurs pages possibles
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=pagesize, **marges)
    couleurs = _couleurs_rapport(table.entreprise)

    elements = _elements_liste_table(table, champs, lignes, pagesize, couleurs, tables_calcul=tables_calcul, mises_en_forme=mises_en_forme, fusions_vue=fusions_vue)
    elements += _elements_dashboards_ontologie_stats(table, champs, lignes, couleurs, membre=membre)
    elements += _elements_analyses_donnees(table, doc.width, couleurs)   # séries temporelles, statistiques, géospatial (données actuelles)
    elements += _elements_architecture_complete(table, pagesize[0] - 3 * cm, couleurs)   # section 5 : tout le reste de l'architecture

    _construire(doc, elements, table.entreprise)
    tampon.seek(0)
    return tampon.read()


# ─────────────────────────────────────────────────────────────
# EXPORT PDF DES MEMBRES DE L'ÉQUIPE (page "employes")
# Même principe que generer_pdf_liste_table : rendu tabulaire brut,
# une ligne par membre, colonnes fixes (pas de champs dynamiques ici).
# ─────────────────────────────────────────────────────────────


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
