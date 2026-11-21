"""SYNTHÈSES RÉDIGÉES — le même principe que les documents administratifs (page éditeur, corps fait de blocs, PDF avec entête officiel, cachet et
signatures du contexte « courrier »), appliqué à l'analyse d'UNE table.

Une synthèse ne peut pas être automatisée pour tous les métiers et tous les types de données : c'est donc la personne qui la RÉDIGE, avec ce que le
moteur a déjà calculé. Rien n'est recalculé ici et aucune formule n'est réécrite : les sections du rapport général (structure, KPI, dashboards, skills,
BI, statistiques, groupes de variables, ANOVA, analyses factorielles, séries temporelles, géospatial) sont construites par les MÊMES fonctions
(reports/_part_007.py), puis découpées en CARTES (un titre + ce qui le suit) contenant des ÉLÉMENTS : tableau, graphique, texte.

Blocs du corps (TYPES_BLOCS_SYNTHESE) :
  * texte        : titre, sous-titre, paragraphe, liste numérotée, tableau simple, saut de page (comme les documents administratifs) ;
  * rédaction    : commentaire (ce que la personne constate), explication (comment lire le résultat), à retenir (la conclusion) ;
  * analyse      : une carte de l'analyse (ou seulement certains de ses éléments : un graphique, un tableau…) + une légende facultative.
Une carte est repérée par son titre (« Titre#n », n = rang parmi les titres identiques), un élément par son genre et son rang (t0, g1, p0…).
Si l'analyse change et que la carte n'existe plus, le PDF le dit en une ligne au lieu d'échouer.
"""
from engine.i18n import t as _pt, tf as _ptf
import io
import logging as _logging
import re
import threading as _threading
import time as _time

from ._header import *  # noqa
from ._part_001 import (  # noqa
    _bandeau_entreprise, _bloc_cachet_simple, _bloc_signatures_pied, _police_activer, _feuille_styles,
    _pol, _couleurs_rapport, _elements_rapport_skill,
)
from ._part_003 import _elements_kpis_table  # noqa
from ._part_004 import _elements_dashboard_bi, _elements_analyses_donnees  # noqa
from ._part_005 import _html, _tableau_simple, _decor_document, MAX_TITRE, MAX_TEXTE_BLOC, MAX_BLOCS  # noqa
from ._part_007 import (  # noqa
    SECTIONS_RAPPORT_GENERAL_IDS, sections_rapport_general, _styles_rg, _bloc_structure, _blocs_analyses, _skills_executes,
    _bloc_statistiques, _bloc_groupes, _bloc_anova, _bloc_factoriel, _bloc_series,
)
from ..vue_groupe import nom_vue as _nom_vue_syn, table_generale as _table_generale_syn  # noqa: E402
from reportlab.lib.enums import TA_JUSTIFY  # noqa: E402
from reportlab.platypus import KeepTogether, PageBreak, SimpleDocTemplate, Spacer  # noqa: E402
from reportlab.graphics.shapes import Drawing as _Drawing  # noqa: E402

_log = _logging.getLogger(__name__)

CONTEXTE_SYNTHESE = "courrier"          # entête officiel, cachet et signatures : les réglages des documents administratifs
TYPE_SIGNATURE_SYNTHESE = "synthese"    # une signature réservée à certains types de documents ne s'imprime pas sur une synthèse
TYPES_BLOCS_TEXTE = ("titre", "sous_titre", "paragraphe", "commentaire", "explication", "a_retenir", "liste", "tableau", "saut_page")
TYPES_BLOCS_SYNTHESE = TYPES_BLOCS_TEXTE + ("analyse",)
SECTIONS_SYNTHESE = tuple(i for i in SECTIONS_RAPPORT_GENERAL_IDS if i != "synthese")   # la synthèse automatique reste celle du rapport général
SECTIONS_AVEC_BANDEAU = frozenset({"dashboards", "skills", "bi"})   # ces sections ouvrent chaque analyse par le bandeau de l'entreprise : jamais proposé comme contenu
MAX_CARTE, MAX_ELEMENTS_BLOC, MAX_LEGENDE = 200, 60, 300
DUREE_CACHE_CATALOGUE = 300     # secondes : l'aperçu du catalogue n'est pas recalculé à chaque clic ; le PDF, lui, relit toujours les données actuelles
MAX_ENTREES_CACHE = 24

_RE_ELEMENT = re.compile(r"^[tgpa]\d{1,3}$")
_PREFIXE_GENRE = {"texte": "p", "tableau": "t", "graphique": "g", "autre": "a"}


# ─────────────────────────────────────────────────────────────
# Corps : normalisation (jamais d'erreur : un corps mal formé donne simplement moins de blocs)
# ─────────────────────────────────────────────────────────────
def normaliser_corps_synthese(brut):
    """Corps saisi -> liste propre de blocs. Texte : {type, texte}. Analyse : {type: "analyse", section, carte, elements, legende, avec_titre}.
    Type inconnu = paragraphe ; bloc vide ou analyse sans section/carte reconnue : ignoré."""
    if not isinstance(brut, (list, tuple)):
        return []
    blocs = []
    for b in brut:
        if isinstance(b, str):
            b = {"type": "paragraphe", "texte": b}
        if not isinstance(b, dict):
            continue
        genre = b.get("type") if b.get("type") in TYPES_BLOCS_SYNTHESE else "paragraphe"
        if genre == "analyse":
            section = str(b.get("section") or "").strip()
            carte = " ".join(str(b.get("carte") or "").split())[:MAX_CARTE]
            if section not in SECTIONS_SYNTHESE or not carte:
                continue
            elements = []
            for e in b.get("elements") or []:
                if isinstance(e, str) and _RE_ELEMENT.match(e) and e not in elements:
                    elements.append(e)
            blocs.append({"type": "analyse", "section": section, "carte": carte, "elements": elements[:MAX_ELEMENTS_BLOC],
                          "legende": " ".join(str(b.get("legende") or "").split())[:MAX_LEGENDE], "avec_titre": b.get("avec_titre") is not False})
        else:
            texte = str(b.get("texte") or "").replace("\r\n", "\n").strip()[:MAX_TEXTE_BLOC]
            if not texte and genre != "saut_page":
                continue
            blocs.append({"type": genre, "texte": "" if genre == "saut_page" else texte})
        if len(blocs) >= MAX_BLOCS:
            break
    return blocs


# ─────────────────────────────────────────────────────────────
# Découpage d'une section en cartes et en éléments
# ─────────────────────────────────────────────────────────────
_NOMS_TITRES = frozenset({"Title", "Heading1", "Heading2", "Heading3", "RapportTitre", "TitreDashBI", "SousTitreAna"})
_FLOWABLES_VIDES = ("Spacer", "HRFlowable", "PageBreak", "CondPageBreak", "FrameBreak", "Indenter", "UseUpSpace", "NullDraw")


def _aplatir(flowables):
    """Les KeepTogether sont ouverts : leur contenu est découpé comme le reste."""
    for f in flowables or []:
        if isinstance(f, KeepTogether):
            yield from _aplatir(getattr(f, "_content", []))
        elif isinstance(f, (list, tuple)):
            yield from _aplatir(f)
        else:
            yield f


def _est_titre(f):
    if not isinstance(f, Paragraph):
        return False
    nom = getattr(f.style, "name", "") or ""
    return nom.startswith("RGSous") or nom in _NOMS_TITRES


def _texte_de(f):
    try:
        return " ".join(f.getPlainText().split())
    except Exception:
        return ""


def _cellules(table):
    """Cellules d'un Table, chacune ramenée à une liste de flowables ou de textes (une cellule peut être une liste)."""
    for ligne in getattr(table, "_cellvalues", None) or []:
        for c in ligne:
            yield c if isinstance(c, (list, tuple)) else [c]


def _contient_image(f, profondeur=0):
    if isinstance(f, (RLImage, _Drawing)):
        return True
    if profondeur > 4:
        return False
    if isinstance(f, Table):
        return any(_contient_image(x, profondeur + 1) for cellule in _cellules(f) for x in cellule)
    if isinstance(f, (list, tuple)):
        return any(_contient_image(x, profondeur + 1) for x in f)
    return False


def _genre(f):
    if isinstance(f, Paragraph):
        return "texte"
    if _contient_image(f):
        return "graphique"
    if isinstance(f, Table):
        return "tableau"
    return "autre"


def _est_sceau(f):
    """Cachet ou signatures que `_elements_rapport_demande` / `_elements_rapport_skill` ajoutent en fin de rapport : jamais proposés comme contenu
    (la synthèse porte les siens, ceux du contexte « courrier »)."""
    if isinstance(f, RLImage):
        return getattr(f, "hAlign", "") == "RIGHT"
    if isinstance(f, Table):
        for cellule in _cellules(f):
            for x in cellule:
                if isinstance(x, HRFlowable):
                    return True
                if isinstance(x, Paragraph) and getattr(x.style, "name", "") in ("CachetSimple", "SignatureNomPied", "SignatureFonctionPied"):
                    return True
    return False


def _decouper(flowables, section):
    """Flowables d'une section -> cartes [{cle, titre, titre_flowable, elements: [{id, genre, flowable}]}].
    Une carte commence à chaque titre ; un saut de page referme la carte en cours. Ce qui précède le premier titre d'une analyse (bandeau de
    l'entreprise) est écarté pour les sections qui en ouvrent chaque analyse ; ailleurs, c'est une carte « introduction » (titre = la section)."""
    libelle = dict(sections_rapport_general()).get(section, section)
    brutes, courante, avant = [], None, []
    for f in _aplatir(flowables):
        nom = type(f).__name__
        if nom == "PageBreak":
            if courante is not None:
                brutes.append(courante)
                courante = None
            if section in SECTIONS_AVEC_BANDEAU:
                avant = []
            continue
        if _est_titre(f):
            if courante is not None:
                brutes.append(courante)
            courante = {"titre": _texte_de(f)[:120] or libelle, "titre_flowable": f, "items": []}
            continue
        if nom in _FLOWABLES_VIDES or _est_sceau(f):
            continue
        (avant if courante is None else courante["items"]).append(f)
    if courante is not None:
        brutes.append(courante)
    if avant and section not in SECTIONS_AVEC_BANDEAU:
        brutes.insert(0, {"titre": str(libelle), "titre_flowable": None, "items": avant})
    cartes, vus = [], {}
    for b in brutes:
        compteurs, elements = {}, []
        for f in b["items"]:
            genre = _genre(f)
            n = compteurs.get(genre, 0)
            compteurs[genre] = n + 1
            elements.append({"id": f"{_PREFIXE_GENRE[genre]}{n}", "genre": genre, "flowable": f})
        if not elements:
            continue
        rang = vus.get(b["titre"], 0)
        vus[b["titre"]] = rang + 1
        cartes.append({"cle": f"{b['titre']}#{rang}", "titre": b["titre"], "titre_flowable": b["titre_flowable"], "elements": elements})
    return cartes


def _octets_image(f):
    """Octets PNG d'une image de rapport (ReportLab garde le fichier lu dans son ImageReader), sinon None (graphique composite : grille, dessin)."""
    if not isinstance(f, RLImage):
        return None
    lecteur = getattr(f, "_img", None)
    try:
        fp = getattr(lecteur, "fp", None)
        if fp is not None and hasattr(fp, "getvalue"):
            return fp.getvalue()
        image = getattr(lecteur, "_image", None)
        if image is not None:
            tampon = io.BytesIO()
            image.save(tampon, "PNG")
            return tampon.getvalue()
    except Exception:
        _log.exception("synthese: image illisible")
    return None


def _apercu(element):
    """Ce que l'éditeur montre d'un élément pour choisir : texte (début), tableau (premières lignes), graphique (l'image, servie à part)."""
    f, genre = element["flowable"], element["genre"]
    if genre == "texte":
        return {"texte": _texte_de(f)[:400]}
    if genre == "tableau":
        lignes = []
        for ligne in getattr(f, "_cellvalues", None) or []:
            lignes.append([" ".join(" ".join(_texte_de(x) if isinstance(x, Paragraph) else (str(x) if isinstance(x, (str, int, float)) else "")
                                            for x in (c if isinstance(c, (list, tuple)) else [c])).split())[:60] for c in ligne][:6])
        return {"lignes": lignes[:8], "nb_lignes": len(lignes), "nb_colonnes": max((len(l) for l in lignes), default=0)}
    if genre == "graphique":
        return {"image": _octets_image(f) is not None}
    return {}


# ─────────────────────────────────────────────────────────────
# Construction d'une section (mêmes fonctions que le rapport général)
# ─────────────────────────────────────────────────────────────
class _Composeur:
    """Construit les sections d'UNE table avec les moteurs du rapport général, les découpe, et fournit les flowables d'une carte.
    Une section n'est construite qu'une fois par composeur ; si le même élément est demandé deux fois (deux blocs), la section est reconstruite :
    un flowable ReportLab ne se pose jamais deux fois."""

    def __init__(self, table):
        self.table = _table_generale_syn(table)
        entreprise = self.table.entreprise
        _police_activer(entreprise)
        self.largeur = A4[0] - 4 * cm
        self.hauteur = A4[1] - 3.5 * cm
        self.couleurs = _couleurs_rapport(entreprise)
        self.st = _styles_rg(self.couleurs)
        self._memo, self._utilises = {}, set()

    def _construire(self, section):
        table, largeur, couleurs, st, cache = self.table, self.largeur, self.couleurs, self.st, {}
        if section == "structure":
            return _bloc_structure(table, largeur, couleurs, st)
        if section == "kpi":
            return _elements_kpis_table(table, largeur)
        if section == "dashboards":
            return _blocs_analyses(table, largeur, self.hauteur)[0]
        if section == "skills":
            out = []
            for i, (s, _x) in enumerate(_skills_executes(table)):
                if i:
                    out.append(PageBreak())
                out += _elements_rapport_skill(s, largeur)
            return out
        if section == "bi":
            from ..vue_groupe import ids_groupe
            from ..models import TableauBordBI
            out = []
            for tb in TableauBordBI.objects.filter(table_source_id__in=ids_groupe(table)).order_by("ordre", "id"):
                out.append(PageBreak())
                out += _elements_dashboard_bi(tb, largeur, avec_kpis=True, hauteur_page=self.hauteur - 6 * cm)
            return out
        if section == "statistiques":
            return _bloc_statistiques(table, largeur, couleurs, st, cache)
        if section == "groupes":
            return _bloc_groupes(table, largeur, couleurs, st, cache)
        if section == "anova":
            return _bloc_anova(table, largeur, couleurs, st, cache)
        if section == "factoriel":
            return _bloc_factoriel(table, largeur, couleurs, st, cache)
        if section == "series":
            return _bloc_series(table, largeur, couleurs, st, cache)
        if section == "geospatial":
            return _elements_analyses_donnees(table, largeur, couleurs, inclure=("geospatial",), avec_bandeau=False, saut_page=False, avec_titre=False)
        raise ValueError(section)

    def cartes(self, section):
        if section not in SECTIONS_SYNTHESE:
            raise ValueError(section)
        if section not in self._memo:
            self._memo[section] = _decouper(self._construire(section), section)
        return self._memo[section]

    def fournir(self, section, cle, elements=(), avec_titre=True):
        """Flowables de la carte `cle` (tous ses éléments, ou seulement ceux de `elements`), ou None si elle n'existe plus."""
        for essai in (0, 1):
            carte = next((c for c in self.cartes(section) if c["cle"] == cle), None)
            if carte is None:
                return None
            choisis = [e for e in carte["elements"] if not elements or e["id"] in elements]
            if not choisis:
                return None
            marques = {(section, cle, e["id"]) for e in choisis}
            if essai == 0 and marques & self._utilises:
                self._memo.pop(section, None)      # déjà posé ailleurs dans ce document : on reconstruit des flowables neufs
                continue
            self._utilises |= marques
            sortie = [carte["titre_flowable"]] if avec_titre and carte["titre_flowable"] is not None else []
            return sortie + [e["flowable"] for e in choisis]
        return None


# ─────────────────────────────────────────────────────────────
# Catalogue pour l'éditeur (aperçu, mis en cache quelques minutes)
# ─────────────────────────────────────────────────────────────
_CACHE, _VERROU_CACHE = {}, _threading.Lock()


def catalogue_sections():
    """[{id, libelle}] : les types d'analyse que l'on peut rédiger (sections du rapport général, sans la synthèse automatique)."""
    return [{"id": i, "libelle": lib} for i, lib in sections_rapport_general() if i in SECTIONS_SYNTHESE]


def catalogue_cartes(table, section, forcer=False):
    """{cartes: [{cle, titre, elements: [{id, genre, apercu}]}]} d'une section de l'analyse de la table, ou {erreur}. Lève ValueError si la section est inconnue.
    Mis en cache DUREE_CACHE_CATALOGUE secondes (`forcer` : recalcul) ; les images servent à part (image_element)."""
    if section not in SECTIONS_SYNTHESE:
        raise ValueError(section)
    cle = (table.entreprise_id, table.id, section)
    with _VERROU_CACHE:
        entree = _CACHE.get(cle)
    if entree and not forcer and _time.time() - entree["t"] < DUREE_CACHE_CATALOGUE:
        return entree["donnees"]
    try:
        cartes = _Composeur(table).cartes(section)
    except Exception:
        _log.exception(_pt("engine/reports/_part_008.py:log_catalogue"), table.id, section)
        return {"cartes": [], "erreur": _pt("engine/reports/_part_008.py:section_erreur")}
    donnees = {"cartes": [{"cle": c["cle"], "titre": c["titre"],
                           "elements": [{"id": e["id"], "genre": e["genre"], "apercu": _apercu(e)} for e in c["elements"]]} for c in cartes]}
    images = {(c["cle"], e["id"]): _octets_image(e["flowable"]) for c in cartes for e in c["elements"] if e["genre"] == "graphique"}
    with _VERROU_CACHE:
        if len(_CACHE) >= MAX_ENTREES_CACHE:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k]["t"]), None)
        _CACHE[cle] = {"t": _time.time(), "donnees": donnees, "images": {k: v for k, v in images.items() if v}}
    return donnees


def image_element(table, section, carte, element):
    """Octets PNG du graphique `element` de la carte `carte`, ou None (introuvable, ou graphique composite sans image directe)."""
    cle = (table.entreprise_id, table.id, section)
    with _VERROU_CACHE:
        entree = _CACHE.get(cle)
    if entree is None:
        catalogue_cartes(table, section)
        with _VERROU_CACHE:
            entree = _CACHE.get(cle)
    return ((entree or {}).get("images") or {}).get((carte, element))


# ─────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────
def _teinte(couleur, k=0.9):
    """Couleur mélangée à du blanc (k = part de blanc) : fond d'encadré lisible à l'impression."""
    return colors.Color(couleur.red + (1 - couleur.red) * k, couleur.green + (1 - couleur.green) * k, couleur.blue + (1 - couleur.blue) * k)


def _styles_synthese(couleurs):
    base = _feuille_styles()
    texte = couleurs["texte"]
    paragraphe = ParagraphStyle("SynParagraphe", parent=base["Normal"], fontName=_pol("normal"), fontSize=10.5, leading=15,
                                alignment=TA_JUSTIFY, textColor=texte, spaceAfter=7)
    return {
        "titre_doc": ParagraphStyle("SynTitreDoc", parent=base["Normal"], fontName=_pol("gras"), fontSize=14, leading=18, alignment=TA_CENTER,
                                    textColor=texte, spaceBefore=18, spaceAfter=6),
        "meta": ParagraphStyle("SynMeta", parent=base["Normal"], fontName=_pol("normal"), fontSize=9, leading=12, alignment=TA_CENTER,
                               textColor=colors.HexColor("#6b7280"), spaceAfter=12),
        "titre": ParagraphStyle("SynTitre", parent=base["Normal"], fontName=_pol("gras"), fontSize=12, leading=15, textColor=couleurs["accent"],
                                spaceBefore=12, spaceAfter=5, keepWithNext=1),
        "sous_titre": ParagraphStyle("SynSousTitre", parent=base["Normal"], fontName=_pol("gras_italique"), fontSize=10.5, leading=14,
                                     textColor=texte, spaceBefore=8, spaceAfter=3, keepWithNext=1),
        "paragraphe": paragraphe,
        "liste": ParagraphStyle("SynListe", parent=paragraphe, leftIndent=0.9 * cm, bulletIndent=0.2 * cm, spaceAfter=3),
        "cellule": ParagraphStyle("SynCellule", parent=paragraphe, fontSize=9.5, leading=12.5, alignment=TA_LEFT, spaceAfter=0),
        "cellule_tete": ParagraphStyle("SynCelluleTete", parent=paragraphe, fontName=_pol("gras"), fontSize=9.5, leading=12.5, alignment=TA_LEFT, spaceAfter=0),
        "encadre": ParagraphStyle("SynEncadre", parent=paragraphe, alignment=TA_LEFT, spaceAfter=0),
        "legende": ParagraphStyle("SynLegende", parent=base["Normal"], fontName=_pol("italique"), fontSize=9,
                                  leading=12, alignment=TA_CENTER, textColor=colors.HexColor("#4b5563"), spaceBefore=2, spaceAfter=9),
        "discret": ParagraphStyle("SynDiscret", parent=base["Normal"], fontName=_pol("normal"), fontSize=8.5, leading=11,
                                  textColor=colors.HexColor("#6b7280"), spaceAfter=6),
    }


def _encadre(genre, texte, largeur, couleurs, styles):
    """Commentaire (barre d'accent), explication (cadre gris-bleu) ou à retenir (fond teinté + barre) : étiquette en gras, puis le texte."""
    accent = couleurs["accent"]
    etiquette = _pt(f"engine/reports/_part_008.py:etiquette_{genre}")
    contenu = Paragraph(f"<b>{_esc_syn(etiquette)}</b><br/>{_html(texte)}", styles["encadre"])
    tbl = Table([[contenu]], colWidths=[largeur])
    regles = [("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
              ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]
    if genre == "commentaire":
        regles += [("LINEBEFORE", (0, 0), (0, -1), 3, accent)]
    elif genre == "explication":
        regles += [("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.94, 0.96, 0.98)), ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(0.78, 0.82, 0.88))]
    else:
        regles += [("BACKGROUND", (0, 0), (-1, -1), _teinte(accent, 0.88)), ("LINEBEFORE", (0, 0), (0, -1), 3, accent)]
    tbl.setStyle(TableStyle(regles))
    tbl.spaceBefore, tbl.spaceAfter = 4, 9
    return tbl


def _esc_syn(texte):
    from xml.sax.saxutils import escape
    return escape(str(texte))


def _elements_bloc_texte(b, largeur, couleurs, styles):
    genre, texte = b["type"], b["texte"]
    if genre == "saut_page":
        return [PageBreak()]
    if genre == "tableau":
        tableau = _tableau_simple(texte, largeur, styles["cellule"], styles["cellule_tete"], couleurs["texte"])
        return [tableau] if tableau is not None else []
    if genre == "liste":
        return [Paragraph(_html(l.strip()), styles["liste"], bulletText=f"{i}.") for i, l in enumerate([x for x in texte.split("\n") if x.strip()], 1)]
    if genre in ("commentaire", "explication", "a_retenir"):
        return [_encadre(genre, texte, largeur, couleurs, styles)]
    return [Paragraph(_html(texte), styles[genre])]


def _elements_bloc_analyse(b, composeur, styles):
    libelle = dict(sections_rapport_general()).get(b["section"], b["section"])
    carte_titre = b["carte"].rsplit("#", 1)[0]
    try:
        flowables = composeur.fournir(b["section"], b["carte"], b["elements"], b.get("avec_titre", True))
    except Exception:
        _log.exception(_pt("engine/reports/_part_008.py:log_bloc"), b["section"], b["carte"])
        flowables = None
    if not flowables:
        return [Paragraph(_esc_syn(_ptf("engine/reports/_part_008.py:analyse_indisponible", v1=libelle, v2=carte_titre)), styles["discret"])]
    sortie = list(flowables)
    if b.get("legende"):
        sortie.append(Paragraph(_html(b["legende"]), styles["legende"]))
    return sortie


def generer_pdf_synthese(synthese, final=False) -> bytes:
    """Octets du PDF d'une SyntheseRedigee : entête officiel (modèle choisi), titre, table analysée, corps (blocs dans l'ordre), cachet et signatures.
    `final=False` : épreuve de BROUILLON (filigrane). `final=True` : version définitive, appelée UNE fois à la finalisation (le PDF est alors figé)."""
    from django.utils import timezone
    entreprise = synthese.entreprise
    table = synthese.table
    _police_activer(entreprise)
    tampon = io.BytesIO()
    pdf = SimpleDocTemplate(tampon, pagesize=A4, topMargin=1.5 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
                            title=(synthese.titre or _nom_vue_syn(table) or "")[:100])
    elements = []
    _bandeau_entreprise(entreprise, pdf.width, elements, contexte=CONTEXTE_SYNTHESE, modele_entete_id=synthese.modele_entete_id,
                        membre_entete_id=synthese.auteur_id, numero_entete="")
    couleurs = _couleurs_rapport(entreprise)
    styles = _styles_synthese(couleurs)
    date_txt = (synthese.finalisee_le if final and synthese.finalisee_le else timezone.now()).astimezone(timezone.get_current_timezone()).strftime("%d/%m/%Y")
    titre = (synthese.titre or "").strip()
    if titre:
        elements.append(Paragraph(f"<u>{_esc_syn(titre.upper())}</u>", styles["titre_doc"]))
    elements.append(Paragraph(_esc_syn(_ptf("engine/reports/_part_008.py:meta", v1=_nom_vue_syn(table), v2=date_txt)), styles["meta"]))
    composeur = _Composeur(table)
    for b in normaliser_corps_synthese(synthese.corps):
        try:
            if b["type"] == "analyse":
                elements += _elements_bloc_analyse(b, composeur, styles)
            else:
                elements += _elements_bloc_texte(b, pdf.width, couleurs, styles)
        except Exception:
            _log.exception(_pt("engine/reports/_part_008.py:log_bloc"), b.get("type"), b.get("carte", ""))
    bloc_signature = []
    _bloc_cachet_simple(entreprise, bloc_signature, contexte=CONTEXTE_SYNTHESE)
    _bloc_signatures_pied(entreprise, bloc_signature, contexte=CONTEXTE_SYNTHESE, largeur_max=pdf.width, type_document=TYPE_SIGNATURE_SYNTHESE,
                          detail=True, mention="", date_txt=date_txt)
    if bloc_signature:
        elements.append(KeepTogether(bloc_signature))
    premiere, suivantes = _decor_document(entreprise, not final)
    pdf.build(elements, onFirstPage=premiere, onLaterPages=suivantes)
    tampon.seek(0)
    return tampon.read()


__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
