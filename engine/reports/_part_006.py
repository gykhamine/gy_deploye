from engine.i18n import t as _i18n_t
from ._header import *  # noqa
from ._part_001 import *  # noqa


def generer_pdf_analyse_media(table, resultats, etapes, synthese="", typ="son") -> bytes:
    """PDF de l'Analyse des médias (son / image / vidéo) : bandeau de l'entreprise, synthèse en clair, étapes appliquées, courbes avec axes (pdf_media.py).
    Même gabarit que les autres PDF : polices, couleurs, pied de page et numérotation de la configuration de l'entreprise."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate
    from .. import pdf_media
    from ..views_analyse_media import _resume_son

    entreprise = getattr(table, "entreprise", None)
    _police_activer(entreprise)
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm)
    elements = []
    _bandeau_entreprise(entreprise, doc.width, elements, table=table, contexte="table")
    t = lambda k: _i18n_t("engine/views_analyse_media.py:" + k)
    elements += pdf_media.elements(resultats, etapes, synthese, f"{t('titre')} — {table.nom}", doc.width, _feuille_styles(),
                                   _couleurs_rapport(entreprise)["accent"], t, _resume_son, typ)
    _construire(doc, elements, entreprise)
    return tampon.getvalue()


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" : on force la reexportation complete (comme les autres fichiers du package).
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
