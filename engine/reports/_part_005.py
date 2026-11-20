"""PDF d'un document administratif rédigé dans l'éditeur (note de service, attestation, lettre…).

Composition : entête officiel (bloc gauche / logo / bloc droite — modèle d'entête choisi, complété par l'agent) ->
titre -> corps (blocs dans l'ordre saisi) -> date -> cachet + signatures (nom, fonction, date ; jamais coupés entre deux pages). Tout ce qui n'est pas le titre et le corps
vient de la configuration du contexte « courrier » : l'utilisateur n'a rien d'autre à régler.

Blocs du corps (TYPES_BLOCS) : titre, sous-titre, paragraphe ; objet / destinataire / copie à ; vu / considérant /
article (numérotés automatiquement) ; liste numérotée ; tableau simple ; saut de page. Dans le texte : **gras**,
*italique*, __souligné__ (une balise mal fermée reste du texte, jamais d'erreur).
Pagination : l'entête officiel n'est qu'un flowable placé au début, donc imprimé en page 1 seulement ; les pages
suivantes ne portent que le numéro de référence rappelé en haut (document émis).
"""
from engine.i18n import t, tf
from xml.sax.saxutils import escape as _esc

from ._header import *  # noqa
from ._part_001 import (  # noqa
    _bandeau_entreprise, _bloc_cachet_simple, _bloc_signatures_pied, _police_activer, _feuille_styles,
    _construire, _pol, _couleurs_rapport, _decor_page,
)
import re
from reportlab.platypus import KeepTogether, Table, TableStyle, Spacer, PageBreak
from reportlab.lib.enums import TA_JUSTIFY

CONTEXTE_DOCUMENT_ADMINISTRATIF = "courrier"
TYPES_BLOCS = ("titre", "sous_titre", "paragraphe", "objet", "destinataire", "copie", "vu", "considerant", "article",
               "liste", "tableau", "saut_page")
BLOCS_SANS_TEXTE = ("saut_page",)
MAX_BLOCS, MAX_TEXTE_BLOC, MAX_TITRE = 200, 20000, 200


def normaliser_corps(brut):
    """Corps saisi -> liste propre de blocs {type, texte}. Type inconnu = paragraphe ; blocs vides ignorés (sauf
    sauts de ligne internes, conservés) ; jamais d'erreur : un corps mal formé donne simplement moins de blocs."""
    if not isinstance(brut, (list, tuple)):
        return []
    blocs = []
    for b in brut:
        if isinstance(b, str):
            b = {"type": "paragraphe", "texte": b}
        if not isinstance(b, dict):
            continue
        type_bloc = b.get("type") if b.get("type") in TYPES_BLOCS else "paragraphe"
        texte = str(b.get("texte") or "").replace("\r\n", "\n").strip()[:MAX_TEXTE_BLOC]
        if not texte and type_bloc not in BLOCS_SANS_TEXTE:
            continue
        blocs.append({"type": type_bloc, "texte": "" if type_bloc in BLOCS_SANS_TEXTE else texte})
        if len(blocs) >= MAX_BLOCS:
            break
    return blocs


_MARQUES = ((re.compile(r"\*\*(.+?)\*\*", re.S), "b"), (re.compile(r"__(.+?)__", re.S), "u"),
            (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.S), "i"))


def _html(texte):
    """Texte saisi -> markup ReportLab : échappé, sauts de ligne conservés, **gras** / *italique* / __souligné__.
    Le texte est échappé AVANT de poser les balises (aucune balise saisie ne passe). Si les balises obtenues se
    chevauchent (`*a **b* c**`), on garde le texte sans mise en forme plutôt que de faire échouer le PDF."""
    brut = _esc(texte)
    formate = brut
    for motif, balise in _MARQUES:
        formate = motif.sub(lambda m, b=balise: f"<{b}>{m.group(1)}</{b}>", formate)
    pile = []
    for fermeture, nom in re.findall(r"<(/?)([biu])>", formate):
        if not fermeture:
            pile.append(nom)
        elif not pile or pile.pop() != nom:
            return brut.replace("\n", "<br/>")
    if pile:
        return brut.replace("\n", "<br/>")
    return formate.replace("\n", "<br/>")


_PREFIXES_BLOC = {"vu": "engine/reports/_part_005.py:prefixe_vu", "considerant": "engine/reports/_part_005.py:prefixe_considerant"}


def _tableau_simple(texte, largeur, style_cellule, style_tete, couleur):
    """Tableau simple : une ligne de texte = une ligne du tableau, cellules séparées par « | » ; la première ligne est
    l'en-tête. Colonnes de largeur égale, lignes de tête répétées si le tableau passe sur la page suivante."""
    lignes = [[c.strip() for c in l.split("|")] for l in texte.split("\n") if l.strip()]
    if not lignes:
        return None
    n = max(len(l) for l in lignes)
    donnees = [[Paragraph(_html(c), style_tete if i == 0 else style_cellule) for c in l + [""] * (n - len(l))]
               for i, l in enumerate(lignes)]
    tbl = Table(donnees, colWidths=[largeur / n] * n, repeatRows=1 if len(lignes) > 1 else 0)
    tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, couleur), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.93, 0.93, 0.93)),
                             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    tbl.spaceBefore, tbl.spaceAfter = 6, 8
    return tbl


def elements_document_administratif(titre, corps, largeur, couleurs, date_txt="", lieu="", reference="", date_position="bas"):
    """Titre + corps (+ « Fait à …, le … ») sous forme de flowables, sans entête ni cachet ni signatures.
    `reference` : numéro imprimé au-dessus du titre (document émis seulement).
    `date_position` : « bas » (après le corps), « haut » (à droite, avant la référence et le titre) ou « aucune »."""
    base = _feuille_styles()
    couleur_texte = couleurs["texte"]
    style_titre = ParagraphStyle("AdmTitre", parent=base["Normal"], fontName=_pol("gras"), fontSize=14, leading=18,
                                 alignment=TA_CENTER, textColor=couleur_texte, spaceBefore=18, spaceAfter=16)
    styles = {
        "titre": ParagraphStyle("AdmBlocTitre", parent=base["Normal"], fontName=_pol("gras"), fontSize=12, leading=15,
                                textColor=couleur_texte, spaceBefore=12, spaceAfter=5),
        "sous_titre": ParagraphStyle("AdmBlocSousTitre", parent=base["Normal"], fontName=_pol("gras_italique"), fontSize=10.5,
                                     leading=14, textColor=couleur_texte, spaceBefore=8, spaceAfter=3),
        "paragraphe": ParagraphStyle("AdmParagraphe", parent=base["Normal"], fontName=_pol("normal"), fontSize=10.5, leading=15,
                                     alignment=TA_JUSTIFY, textColor=couleur_texte, spaceAfter=7),
    }
    paragraphe = styles["paragraphe"]
    styles["objet"] = ParagraphStyle("AdmObjet", parent=paragraphe, alignment=TA_LEFT, spaceBefore=8, spaceAfter=10)
    styles["destinataire"] = ParagraphStyle("AdmDestinataire", parent=paragraphe, alignment=TA_LEFT,
                                            leftIndent=largeur * 0.5, spaceBefore=10, spaceAfter=10)
    styles["copie"] = ParagraphStyle("AdmCopie", parent=paragraphe, alignment=TA_LEFT, fontSize=9.5, leading=13, spaceBefore=10)
    styles["liste"] = ParagraphStyle("AdmListe", parent=paragraphe, leftIndent=0.9 * cm, bulletIndent=0.2 * cm, spaceAfter=3)
    styles["cellule"] = ParagraphStyle("AdmCellule", parent=paragraphe, fontSize=9.5, leading=12.5, alignment=TA_LEFT, spaceAfter=0)
    styles["cellule_tete"] = ParagraphStyle("AdmCelluleTete", parent=styles["cellule"], fontName=_pol("gras"))
    elements = []

    def ligne_date(**espaces):
        texte_date = (tf("engine/reports/_part_005.py:fait_a_le", v1=lieu, v2=date_txt) if lieu
                      else tf("engine/reports/_part_005.py:fait_le", v1=date_txt))
        return Paragraph(_esc(texte_date), ParagraphStyle("AdmDate", parent=base["Normal"], fontName=_pol("normal"), fontSize=10.5,
                                                          alignment=TA_RIGHT, textColor=couleur_texte, **espaces))
    if date_txt and date_position == "haut":
        elements.append(ligne_date(spaceBefore=4, spaceAfter=4))
    if reference:
        elements.append(Paragraph(_esc(reference), ParagraphStyle(
            "AdmReference", parent=base["Normal"], fontName=_pol("gras"), fontSize=10.5, leading=14,
            alignment=TA_LEFT, textColor=couleur_texte, spaceBefore=6)))
    titre = (titre or "").strip()
    if titre:
        elements.append(Paragraph(f"<u>{_esc(titre.upper())}</u>", style_titre))
        elements.append(HRFlowable(width="100%", thickness=0, color=colors.white))
    n_article = 0
    for b in normaliser_corps(corps):
        genre, texte = b["type"], b["texte"]
        if genre == "saut_page":
            elements.append(PageBreak())
        elif genre == "tableau":
            tableau = _tableau_simple(texte, largeur, styles["cellule"], styles["cellule_tete"], couleur_texte)
            if tableau is not None:
                elements.append(tableau)
        elif genre == "liste":
            for i, ligne in enumerate([x for x in texte.split("\n") if x.strip()], 1):
                elements.append(Paragraph(_html(ligne.strip()), styles["liste"], bulletText=f"{i}."))
        elif genre == "article":
            n_article += 1
            elements.append(Paragraph(f"<b>{_esc(tf('engine/reports/_part_005.py:article_n', v1=n_article))}</b> {_html(texte)}",
                                      styles["paragraphe"]))
        elif genre in _PREFIXES_BLOC:
            etiquette = t(_PREFIXES_BLOC[genre])
            deja = re.match(re.escape(etiquette) + r"\b", texte, re.I)      # « Vu … » saisi en entier : pas de doublon
            elements.append(Paragraph(_html(texte if deja else f"{etiquette} {texte}"), styles["paragraphe"]))
        elif genre in ("objet", "copie"):
            etiquette = t(f"engine/reports/_part_005.py:etiquette_{genre}")
            elements.append(Paragraph(f"<b>{_esc(etiquette)}</b> {_html(texte)}", styles[genre]))
        else:
            elements.append(Paragraph(_html(texte), styles[genre]))
    if date_txt and date_position == "bas":
        elements.append(Spacer(1, 0.6 * cm))
        elements.append(ligne_date())
    return elements


def dessin_qr(url, taille):
    """QR code (ReportLab, sans dépendance externe) encodant `url`, carré de `taille` points."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    w = QrCodeWidget(url)
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(taille, taille, transform=[taille / (x1 - x0), 0, 0, taille / (y1 - y0), 0, 0])
    d.add(w)
    return d


def bloc_verification(url, reference, largeur, couleur):
    """QR + « Document authentifié » + adresse de vérification, en bas du document émis."""
    style = ParagraphStyle("AdmVerif", fontName=_pol("normal"), fontSize=7.5, leading=10, textColor=couleur)
    texte = Paragraph(
        "<b>%s</b><br/>%s<br/>%s" % (_esc(tf("engine/reports/_part_005.py:verif_titre", v1=reference)),
                                     _esc(t("engine/reports/_part_005.py:verif_aide")), _esc(url)), style)
    tbl = Table([[dessin_qr(url, 2.4 * cm), texte]], colWidths=[2.8 * cm, largeur - 2.8 * cm])
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return KeepTogether([Spacer(1, 0.5 * cm), tbl])


def _decor_document(entreprise, brouillon, reference=""):
    """(décor de la 1re page, décor des pages suivantes). Décor habituel + filigrane « BROUILLON » tant que le document
    n'est pas émis ; les pages suivantes rappellent en plus le numéro de référence en haut (l'entête complet reste
    en page 1 seulement)."""
    base = _decor_page(entreprise)

    def _filigrane(canvas_, doc_rl):
        largeur, hauteur = doc_rl.pagesize
        canvas_.saveState()
        canvas_.setFillColor(colors.Color(0.5, 0.5, 0.5, alpha=0.16))
        canvas_.setFont(_pol("gras"), 84)
        canvas_.translate(largeur / 2, hauteur / 2)
        canvas_.rotate(45)
        canvas_.drawCentredString(0, 0, t("engine/reports/_part_005.py:filigrane_brouillon"))
        canvas_.restoreState()

    def _rappel(canvas_, doc_rl):
        if not reference:
            return
        largeur, hauteur = doc_rl.pagesize
        canvas_.saveState()
        canvas_.setFont(_pol("gras"), 8.5)
        canvas_.setFillColor(colors.Color(0.35, 0.35, 0.35))
        canvas_.drawRightString(largeur - doc_rl.rightMargin, hauteur - 0.9 * cm, reference)
        canvas_.restoreState()

    def _premiere(canvas_, doc_rl):
        base(canvas_, doc_rl)
        if brouillon:
            _filigrane(canvas_, doc_rl)

    def _suivantes(canvas_, doc_rl):
        _premiere(canvas_, doc_rl)
        _rappel(canvas_, doc_rl)
    return _premiere, _suivantes


def generer_pdf_document_administratif(doc, emission=None) -> bytes:
    """Octets du PDF d'un DocumentAdministratif (entête officiel, référence, titre, corps, lieu et date, cachet,
    signatures, QR de vérification).

    `emission` = None : épreuve de BROUILLON (filigrane, ni numéro ni QR, date du jour) — jamais une pièce officielle.
    `emission` = {"reference", "lieu", "date", "url"} : version d'ÉMISSION, appelée UNE seule fois (voir
    emission_documents.emettre) ; le résultat est alors figé et re-téléchargé tel quel, jamais régénéré."""
    from django.utils import timezone
    entreprise = doc.entreprise
    _police_activer(entreprise)
    brouillon = emission is None
    date_txt = (emission["date"] if emission else timezone.localdate()).strftime("%d/%m/%Y")
    tampon = io.BytesIO()
    pdf = SimpleDocTemplate(tampon, pagesize=A4, topMargin=1.5 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm, title=((emission or {}).get("reference") or doc.titre or "")[:100])
    elements = []
    # Entête : le modèle choisi dans l'éditeur (vide = modèle par défaut). À l'émission, ce PDF est figé : il n'est plus jamais régénéré.
    # Numéro de la dernière ligne de l'entête gauche : celui attribué à l'émission (figé avec le PDF) ; sur une épreuve de brouillon,
    # le prochain numéro, sans consommer le compteur. Les réglages (interrupteur, numérotation) sont ceux de l'AUTEUR du document.
    if emission:
        numero_entete = emission.get("numero_entete", "")
    else:
        from ..entete_officiel import numero_apercu
        numero_entete = numero_apercu(entreprise.id, doc.auteur_id, doc.type_document)
    _bandeau_entreprise(entreprise, pdf.width, elements, contexte=CONTEXTE_DOCUMENT_ADMINISTRATIF, modele_entete_id=doc.modele_entete_id,
                        membre_entete_id=doc.auteur_id, numero_entete=numero_entete)
    couleurs = _couleurs_rapport(entreprise)
    elements += elements_document_administratif(
        doc.titre, doc.corps, pdf.width, couleurs, date_txt,
        lieu=(emission["lieu"] if emission else (doc.lieu or "")), reference=(emission["reference"] if emission else ""),
        date_position=doc.date_position)
    # Cachet + signatures (nom, fonction, date sous chacune) : un seul bloc, jamais coupé entre deux pages. Signataires du
    # type de ce document ; « P.O. » / « P/ » devant la fonction quand la signature est une délégation.
    bloc_signature = []
    _bloc_cachet_simple(entreprise, bloc_signature, contexte=CONTEXTE_DOCUMENT_ADMINISTRATIF)
    _bloc_signatures_pied(entreprise, bloc_signature, contexte=CONTEXTE_DOCUMENT_ADMINISTRATIF, largeur_max=pdf.width,
                          type_document=doc.type_document, detail=True,
                          mention=doc.MENTIONS_DELEGATION.get(doc.delegation, ""),
                          date_txt="" if doc.date_position == "aucune" else date_txt)
    if bloc_signature:
        elements.append(KeepTogether(bloc_signature))
    if emission and emission.get("url"):
        elements.append(bloc_verification(emission["url"], emission["reference"], pdf.width, couleurs["texte"]))
    premiere, suivantes = _decor_document(entreprise, brouillon, reference=(emission or {}).get("reference") or "")
    pdf.build(elements, onFirstPage=premiere, onLaterPages=suivantes)
    tampon.seek(0)
    return tampon.read()
