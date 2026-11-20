"""Generation de rapports professionnels : titres, tableaux, graphiques,
resume automatique, conclusions. PDF via ReportLab, export brut en CSV.

Principe : le PDF ne reinterprete PAS le resultat. Il rend EXACTEMENT ce que
le script d'analyse a renvoye (memes cles, memes valeurs), qu'elles soient
'titre', 'conclusion', 'kpi' ou toute autre cle que le script
aura choisi de produire. Aucune cle n'est filtree ou privilegiee."""
from engine.i18n import t as _pt, tl as _ptl, tf as _ptf
from engine.i18n import tl
from engine.i18n import t
import base64
import csv
import io

from django.core.files.base import ContentFile
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
)

from ..models import RapportGenere, Bloc, DemandeAgent
from ..models import signatures_document_pour  # noqa  (signatures de l'agent qui génère, sinon de l'entreprise)
from ..image_universelle import activer as _activer_images
_activer_images()  # SVG & co : tout format que PIL ne lit pas est converti en PNG avant ReportLab


# ─────────────────────────────────────────────────────────────
# BANDEAU ENTREPRISE — OBLIGATOIRE SUR CHAQUE PDF, SANS EXCEPTION
# Injecté en tout premier élément de CHAQUE document PDF produit par la
# plateforme (rapport d'analyse, rapport de skill, document formel, reçu
# de soumission, export de liste, export employés). Le nom et les
# coordonnées apparaissent toujours (coordonnees_document() omet déjà
# silencieusement les champs vides côté modèle) ; le logo respecte
# ConfigurationIA.afficher_logo_document (affiché par défaut si aucune
# config n'existe encore, cf. default=True sur le modèle) — même toggle
# que celui utilisé pour les documents formels générés par IA, pour un
# comportement cohérent sur TOUS les PDF de la plateforme.
# ─────────────────────────────────────────────────────────────
CLES_TEXTE_LIBRE = {
    "conclusion", "resume", "recommandation", "recommandations",
    "titre", "pourquoi", "comment", "explication", "analyse_predictive",
}


ORDRE_SECTIONS_SKILL = [
    "titre", "pourquoi", "comment", "table_statistiques",
    "explication", "analyse_predictive", "kpi",
    "conclusion", "recommandations",
]

TITRES_SECTIONS = {
    "titre": t("engine/reports/_header.py:49:15"),
    "pourquoi": tl("engine/reports/_header.py:g:51:17"),
    "comment": tl("engine/reports/_header.py:g:52:16"),
    "table_statistiques": _ptl("engine/reports/_header.py:pdf:57:26"),
    "explication": tl("engine/reports/_header.py:g:54:20"),
    "analyse_predictive": tl("engine/reports/_header.py:g:55:27"),
    "kpi": tl("engine/reports/_header.py:g:56:12"),
    "conclusion": _ptl("engine/reports/_header.py:pdf:61:18"),
    "recommandations": _ptl("engine/reports/_header.py:pdf:62:23"),
}


from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import HRFlowable


_COULEUR_HEADER  = colors.HexColor("#0d1117")   # fond entête (repli si config absente)
_COULEUR_ACCENT  = colors.HexColor("#e8a33d")   # ligne décorative / highlights (repli)
_COULEUR_LIGNE_P = colors.HexColor("#14171b")   # lignes paires tableau
_COULEUR_TEXTE   = colors.HexColor("#1a1a2e")   # texte principal
_COULEUR_MUTED   = colors.HexColor("#6b7280")   # texte secondaire
_BLANC           = colors.white


from reportlab.platypus import PageBreak

_ALIGN_RL = {"gauche": "LEFT", "droite": "RIGHT", "centre": "CENTER"}




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
