from engine.i18n import tf, tl
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
import secrets
"""
INTERFACE UNIQUE, esprit Claude Code : une seule page (index.html), tout le
reste — listes de tables, tableaux de donnees, formulaires, resultats — est
construit dynamiquement en JS via cette API JSON. Aucune "page" par table.
"""
import csv
import io
import json
import os
import re
import uuid

from django.conf import settings as dj_settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, F, Max, Q
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from django.contrib.auth import get_user_model

from ..agent import (
    generer_pdf_demande, modifier_script_demande, sauvegarder_script_demande,
    modifier_script_document,
)
from ..forms import ChangePasswordForm, CreerEmployeForm, InscriptionForm, LoginForm, ProfileForm
from ..import_fichier import importer_fichier_vers_table, lire_noms_feuilles
from .. import tasks as _taches
from .. import actions, actions_fichiers  # noqa: F401 — enregistre les 4 actions fichier


from ..metadata import empreinte_donnees
from ..ratelimit import adresse_ip_client, limiter
from ..securite import membre_de_requete
from ..image_universelle import EXTENSIONS as EXTENSIONS_IMAGES
from ..models import (ArchiveCycle, ConfigurationIA, ConfigurationDocumentContexte,
    CellulePartagee, ChampDynamique, CompteurTokens, ConnexionExterne, CoucheOntologie, CouleurOntologie, DemandeAgent, DocumentGenere, Enregistrement, Entreprise,
    FormuleCalcul, FormuleChamp, FormuleLigne, FormuleLigneCible, GenerationFormule, GenerationGraphiqueBI, GenerationCoucheOntologie, GraphiqueBI, GroupePartage,
    ConteneurTables, GroupePartageCellule, GroupePartageLigne, GroupeTables, IndicateurKPI,
    JournalAudit, LienChamp, LigneCalcul, LignePartagee,
    MembreEntreprise, Notification, PageOntologie, PartageInterAgent, PersonnalisationDocumentMembre, SignatureDocument, Skill, TableauBordBI, TableCalcul, TableDynamique,
    TacheImport, ValeurChamp, ValeurKPIHistorique, WidgetGraphiqueBI, FusionCelluleTable,
    
)
from django.db import IntegrityError, transaction
from .. import notifications as _notifications
from ..skills import executer_skill, generer_skill, regenerer_skill, serialiser_skill
from engine.i18n import tl as _gtl  # noqa: E402


VUES_DISPONIBLES = [
    ("dashboard", "Dashboard"),
    ("donnees", tl("engine/views/_header.py:g:53:17")),
    ("documents", _gtfr("engine/views/_header.py:57:19")),
    ("calcul", _gtfr("engine/views/_header.py:58:16")),
    ("ia", tl("engine/views/_header.py:g:56:12")),
    ("configuration_documents", tl("engine/views/_header.py:g:57:33")),
    ("documents_administratifs", tl("engine/views/_header.py:vue_documents_administratifs")),
    ("dashboard_bi", tl("engine/views/_header.py:g:58:22")),
    ("geolocalisation", tl("engine/views/_header.py:g:59:25")),
    ("series_temporelles", tl("engine/views/_header.py:g:60:28")),
    ("statistiques", "Statistiques"),
    ("analyse_media", tl("engine/views_analyse_media.py:titre")),
    ("anova", "ANOVA"),
    ("analyse_factorielle", _gtfr("engine/views/_header.py:68:29")),
    ("syntheses", tl("engine/views/_header.py:vue_syntheses")),
    ("skills", "Skills"),
    ("kpi", "Indicateurs (KPI)"),
    ("exports", "Exports (tableur, PDF, cartes)"),
    ("ontologie", "Ontologie"),
    ("blockchain", "Blockchain"),
    ("messagerie", "Messagerie"),
    ("publicites", _gtfr("engine/views/_header.py:75:20")),
    ("agenda", "Agenda"),
    ("chatbot", "Chatbot"),
    ("taches", _gtfr("engine/views/_header.py:78:16")),
    ("sms", "SMS"),
    ("recommandations", "Recommandations"),
    ("flux", "Flux"),
    ("paiements", "Paiements"),
    ("wallet", "Wallet"),
    ("finances", tl("engine/templates/engine/finances.html:titre")),
    ("partages_agents", tl("engine/templates/engine/partages_agents.html:12:5")),
    ("connexions_externes", _gtl("rg6.msg.129")),
]


_CHAMPS_IMAGE_CONFIG_DOC_CONTEXTE = {
    "cachet_image_actif":   EXTENSIONS_IMAGES,
    "cachet_image_inactif": EXTENSIONS_IMAGES,
    "image_entite":         EXTENSIONS_IMAGES,
}


import logging

from .. import ia_integration as _ia
from ..exceptions_ia import DatasetError, EntrainementError
from ..models import InferenceIA, ModeleIA

_logger_ia = logging.getLogger(__name__)


_CHAMPS_IMAGE_CONFIG_DOC_CONTEXTE = {
    "cachet_image_actif":   EXTENSIONS_IMAGES,
    "cachet_image_inactif": EXTENSIONS_IMAGES,
    "image_entite":         EXTENSIONS_IMAGES,
}


def _pdf_erreur_json(vue):
    """Export PDF/ZIP : une erreur de generation devient un message lisible (JSON 500, journalisee) au lieu
    d'une page 500 HTML que le bouton ne sait pas afficher. Se place SOUS @login_required/@require_http_methods."""
    import functools
    from django.core.exceptions import PermissionDenied

    @functools.wraps(vue)
    def _enveloppe(request, *args, **kwargs):
        try:
            return vue(request, *args, **kwargs)
        except (Http404, PermissionDenied):
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Export PDF : %s", vue.__name__)
            return JsonResponse({"erreur": tf("engine/views/_header.py:g:104:44", v1=exc)}, status=500)
    return _enveloppe





# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
