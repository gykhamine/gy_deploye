"""Page de garde du rapport général : une vraie couverture, dessinée directement sur le canevas de la première page.

Composition (A4 portrait, mais toutes les cotes suivent la taille réelle de la page) :
  * en haut, un grand bandeau aux couleurs de l'entreprise (couleur d'accent), à bord inférieur incliné, avec un liseré
    de la couleur d'en-tête et des cercles translucides en décor ;
  * au centre du bandeau, le LOGO sur une carte blanche à coins arrondis (ou, sans logo, un monogramme avec les initiales),
    puis le nom de l'entreprise et ses coordonnées ;
  * sous le bandeau : l'étiquette « RAPPORT GÉNÉRAL », le TITRE en grand, une règle d'accent, puis trois repères (date, portée,
    nombre de tables) et, si besoin, une note ;
  * en bas, une bande de pied aux couleurs de l'entreprise (avec son texte de pied de page).

Module volontairement sans dépendance à Django : il ne reçoit que des valeurs simples (couleurs, polices, textes, chemin du logo).
Le texte posé sur une couleur est automatiquement clair ou foncé selon la luminosité de cette couleur : un thème très clair
ou très sombre reste lisible.
"""
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph

_ENCRE = colors.HexColor("#1a1a2e")
_GRIS = colors.HexColor("#6b7280")
_FILET = colors.HexColor("#d9dde3")


def _luminance(c):
    return 0.299 * c.red + 0.587 * c.green + 0.114 * c.blue


def _texte_sur(fond):
    """Blanc sur un fond sombre, encre sur un fond clair."""
    return _ENCRE if _luminance(fond) > 0.62 else colors.white


def _lisible_sur_blanc(c, repli):
    """`c` s'il se lit sur du blanc, sinon `repli` (un thème à en-tête presque blanc ne doit pas donner un titre invisible)."""
    return c if _luminance(c) < 0.72 else repli


def _melange(c, autre, part):
    """Couleur `c` éclaircie/assombrie vers `autre` (part 0..1)."""
    return colors.Color(c.red + (autre.red - c.red) * part, c.green + (autre.green - c.green) * part,
                        c.blue + (autre.blue - c.blue) * part)


def _initiales(nom):
    mots = [m for m in (nom or "").replace("-", " ").split() if m[:1].isalnum()]
    if not mots:
        return "•"
    if len(mots) == 1:
        return mots[0][:2].upper()
    return (mots[0][0] + mots[1][0]).upper()


def _paragraphe(texte, police, taille, couleur, largeur, alignement=0, interligne=None, espacement=0):
    st = ParagraphStyle("garde", fontName=police, fontSize=taille, leading=interligne or taille * 1.22, textColor=couleur,
                        alignment=alignement, charSpace=espacement)
    p = Paragraph(escape(str(texte)), st)
    _, h = p.wrap(largeur, 10_000)
    return p, h


def peintre_page_garde(*, couleurs, polices, nom_entreprise="", coordonnees="", chemin_logo=None, libelle_type="", intitule="",
                       reperes=(), note="", pied="", couleur_fond=None):
    """Retourne le callback `onFirstPage(canvas, doc)` qui dessine la couverture.

    couleurs : {"accent": Color, "entete": Color, "texte": Color} (thème de l'entreprise) ;
    polices  : {"normal": …, "gras": …, "italique": …} (police de l'entreprise) ;
    reperes  : [(étiquette, valeur), …] (3 au plus sont affichés côte à côte) ;
    note     : ligne d'avertissement facultative (ex. rapport tronqué) ; pied : texte de la bande du bas."""
    accent = couleurs["accent"]
    entete = couleurs["entete"]
    encre_titre = _lisible_sur_blanc(entete, couleurs.get("texte") or _ENCRE)
    reperes = [r for r in list(reperes)[:3]]

    def _peindre(canvas_, doc_rl):
        W, H = doc_rl.pagesize
        M = max(1.6 * cm, W * 0.105)
        u = W / (21 * cm)            # échelle : tout est proportionné à la largeur de la page
        canvas_.saveState()

        # ── Fond de page ─────────────────────────────────────────────
        if couleur_fond is not None and couleur_fond != colors.white:
            canvas_.setFillColor(couleur_fond)
            canvas_.rect(0, 0, W, H, stroke=0, fill=1)

        # ── Bandeau haut incliné, avec son liseré ────────────────────
        y_g, y_d = H * 0.50, H * 0.60      # bord inférieur : plus bas à gauche, plus haut à droite
        liseré = 0.32 * cm * u
        def _poly(dy):
            p = canvas_.beginPath()
            p.moveTo(0, H); p.lineTo(W, H); p.lineTo(W, y_d - dy); p.lineTo(0, y_g - dy); p.close()
            return p
        canvas_.setFillColor(entete)
        canvas_.drawPath(_poly(liseré), stroke=0, fill=1)
        canvas_.setFillColor(accent)
        canvas_.drawPath(_poly(0), stroke=0, fill=1)

        # Décor : cercles translucides rognés au bandeau
        canvas_.saveState()
        canvas_.clipPath(_poly(0), stroke=0, fill=0)
        clair = colors.white if _luminance(accent) < 0.62 else _ENCRE
        canvas_.setFillColor(clair)
        canvas_.setStrokeColor(clair)
        canvas_.setFillAlpha(0.10)
        canvas_.circle(W * 0.90, H * 0.95, 6.2 * cm * u, stroke=0, fill=1)
        canvas_.setFillAlpha(0.07)
        canvas_.circle(W * 0.04, H * 0.60, 4.6 * cm * u, stroke=0, fill=1)
        canvas_.setFillAlpha(0.06)
        canvas_.circle(W * 0.18, H * 1.0, 3.2 * cm * u, stroke=0, fill=1)
        canvas_.setStrokeAlpha(0.16)
        canvas_.setLineWidth(1.1)
        canvas_.circle(W * 0.90, H * 0.95, 8.6 * cm * u, stroke=1, fill=0)
        canvas_.circle(W * 0.90, H * 0.95, 10.6 * cm * u, stroke=1, fill=0)
        canvas_.restoreState()

        # ── Carte du logo (ou monogramme) ────────────────────────────
        carte = 4.7 * cm * u
        cx = W / 2
        cy = H * 0.755
        x0, y0 = cx - carte / 2, cy - carte / 2
        rayon = 0.7 * cm * u
        for k, a in ((3, 0.05), (2, 0.07), (1, 0.09)):      # ombre douce
            canvas_.setFillColor(colors.black)
            canvas_.setFillAlpha(a)
            canvas_.roundRect(x0 - k * 1.2, y0 - k * 2.2, carte + k * 2.4, carte + k * 1.6, rayon + k, stroke=0, fill=1)
        canvas_.setFillAlpha(1)
        canvas_.setFillColor(colors.white)
        canvas_.roundRect(x0, y0, carte, carte, rayon, stroke=0, fill=1)
        logo_ok = False
        if chemin_logo and os.path.isfile(chemin_logo):
            try:
                marge_logo = 0.55 * cm * u
                canvas_.drawImage(chemin_logo, x0 + marge_logo, y0 + marge_logo, carte - 2 * marge_logo, carte - 2 * marge_logo,
                                  preserveAspectRatio=True, anchor="c", mask="auto")
                logo_ok = True
            except Exception:
                logo_ok = False
        if not logo_ok:                                       # pas de logo : monogramme aux couleurs de l'entreprise
            canvas_.setFillColor(accent)
            canvas_.circle(cx, cy, carte * 0.34, stroke=0, fill=1)
            canvas_.setFillColor(_texte_sur(accent))
            taille = carte * 0.30
            canvas_.setFont(polices["gras"], taille)
            canvas_.drawCentredString(cx, cy - taille * 0.34, _initiales(nom_entreprise))

        # ── Nom et coordonnées de l'entreprise ───────────────────────
        sur_bandeau = _texte_sur(accent)
        y = y0 - 0.55 * cm * u
        if nom_entreprise:
            p, h = _paragraphe(nom_entreprise, polices["gras"], 17 * u + 1, sur_bandeau, W - 2 * M, alignement=1, espacement=0.4)
            y -= h
            p.drawOn(canvas_, M, y)
        if coordonnees:
            p, h = _paragraphe(coordonnees, polices["normal"], 8.2, sur_bandeau, W - 2.6 * M, alignement=1)
            canvas_.setFillAlpha(0.88)
            y -= h + 0.12 * cm
            p.drawOn(canvas_, 1.3 * M, y)
            canvas_.setFillAlpha(1)

        # ── Étiquette, titre, règle d'accent ─────────────────────────
        yt = y_g - liseré - 1.45 * cm * u
        if libelle_type:
            p, h = _paragraphe(libelle_type.upper(), polices["gras"], 9.5, accent if _luminance(accent) < 0.72 else encre_titre,
                               W - 2 * M, espacement=3)
            p.drawOn(canvas_, M, yt - h)
            yt -= h + 0.45 * cm
        taille_titre = 31 if len(intitule) <= 40 else 25 if len(intitule) <= 80 else 20
        p, h = _paragraphe(intitule, polices["gras"], taille_titre * u + 0.5, encre_titre, W - 2 * M,
                           interligne=(taille_titre * u + 0.5) * 1.16)
        p.drawOn(canvas_, M, yt - h)
        yt -= h + 0.55 * cm
        canvas_.setFillColor(accent if _luminance(accent) < 0.72 else encre_titre)
        canvas_.roundRect(M, yt - 4, 3.4 * cm * u, 4, 2, stroke=0, fill=1)
        yt -= 0.5 * cm

        # ── Repères (date · portée · nombre de tables) ───────────────
        if reperes:
            y_haut = max(yt - 1.6 * cm * u, 6.4 * cm * u)
            n = len(reperes)
            colonne = (W - 2 * M) / n
            canvas_.setStrokeColor(_FILET)
            canvas_.setLineWidth(0.8)
            canvas_.line(M, y_haut + 0.7 * cm, W - M, y_haut + 0.7 * cm)
            hauteur_max = 0
            blocs = []
            for i, (etiquette, valeur) in enumerate(reperes):
                pe, he = _paragraphe(etiquette.upper(), polices["gras"], 7, _GRIS, colonne - 0.5 * cm, espacement=1.2)
                pv, hv = _paragraphe(valeur if valeur not in (None, "") else "—", polices["gras"], 11.5, encre_titre, colonne - 0.5 * cm,
                                     interligne=14)
                blocs.append((pe, he, pv, hv))
                hauteur_max = max(hauteur_max, he + 0.12 * cm + hv)
            for i, (pe, he, pv, hv) in enumerate(blocs):
                x = M + i * colonne
                if i:
                    canvas_.setStrokeColor(_FILET)
                    canvas_.line(x - 0.25 * cm, y_haut - hauteur_max + 0.15 * cm, x - 0.25 * cm, y_haut + 0.25 * cm)
                pe.drawOn(canvas_, x + (0.25 * cm if i else 0), y_haut - he)
                pv.drawOn(canvas_, x + (0.25 * cm if i else 0), y_haut - he - 0.12 * cm - hv)
            if note:
                pn, hn = _paragraphe(note, polices.get("italique", polices["normal"]), 8, _GRIS, W - 2 * M - 0.5 * cm)
                y_note = y_haut - hauteur_max - 0.7 * cm - hn
                canvas_.setFillColor(accent if _luminance(accent) < 0.72 else encre_titre)
                canvas_.rect(M, y_note + hn - 8.5, 3, 8.5, stroke=0, fill=1)
                pn.drawOn(canvas_, M + 0.3 * cm, y_note)

        # ── Bande de pied ────────────────────────────────────────────
        bande = 1.25 * cm * u
        canvas_.setFillColor(entete)
        canvas_.rect(0, bande, W, 2.2, stroke=0, fill=1)
        canvas_.setFillColor(accent)
        canvas_.rect(0, 0, W, bande, stroke=0, fill=1)
        if pied:
            canvas_.setFillColor(_texte_sur(accent))
            canvas_.setFont(polices["normal"], 8)
            canvas_.drawCentredString(W / 2, bande * 0.36, str(pied)[:150])
        canvas_.restoreState()

    return _peindre
