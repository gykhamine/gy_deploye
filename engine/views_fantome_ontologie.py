"""MODÈLE FANTÔME D'ONTOLOGIE (page d'ontologie) : deux points d'entrée, aucun dialogue avec l'utilisateur.

  GET  /api/ontologie/fantome/            l'état du modèle de l'entreprise (le même pour tous ses utilisateurs) : phase (observation | assistance), compréhension,
                                          poids du réseau (inférence faite dans la page), mémoire de parcours.
  POST /api/ontologie/fantome/observer/   corps {"episodes": [...], "pas": [...], "recherches": [...]} : ce que la page a observé
                                          (voir engine/fantome_ontologie.py, Fantome.observer) ; apprend quelques pas, renvoie le nouvel état.

Droits = ceux de la page d'ontologie (vue « dashboard_bi »). UN SEUL modèle par entreprise, partagé par tous ses utilisateurs : il apprend de tous les parcours ensemble
et mène l'ontologie pour tout le monde. Jamais partagé entre entreprises. Le serveur ne reçoit que des identifiants de nœuds, leur type et des nombres : jamais le
contenu des tables.
"""
from engine.i18n import t
import fcntl
import json
import logging
import os
from contextlib import contextmanager

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from . import fantome_ontologie as fo
from .models import MembreEntreprise
from .ratelimit import limiter
from .stockage import dossier_systeme
from .views import _entreprise_courante, _vue_autorisee

logger = logging.getLogger(__name__)
TAILLE_MAX_CORPS = 400_000


def chemin_modele(entreprise_id) -> str:
    return str(dossier_systeme("modeles_fantomes", f"ontologie_e{int(entreprise_id)}.agentgy"))


def _cle() -> bytes:
    return str(settings.SECRET_KEY).encode("utf-8")


@contextmanager
def _verrou(chemin: str):
    """Un seul écrivain à la fois par modèle (tous les utilisateurs de l'entreprise écrivent dans le même fichier)."""
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin + ".verrou", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _contexte(request):
    """(chemin du modèle, refus éventuel)."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not _vue_autorisee(membre, "dashboard_bi"):
        return None, JsonResponse({"erreur": t("engine/views_fantome_ontologie.py:interdit")}, status=403)
    return chemin_modele(entreprise.id), None


@login_required
@require_http_methods(["GET"])
def api_fantome_ontologie(request):
    chemin, refus = _contexte(request)
    if refus:
        return refus
    with _verrou(chemin):
        modele = fo.charger(chemin, _cle())
        if modele.sale:   # modèle neuf, ou fichier migré : écrit tout de suite pour que le prochain GET le retrouve
            fo.ecrire(chemin, modele, _cle())
    return JsonResponse(modele.etat_public())


@login_required
@require_http_methods(["POST"])
@limiter("fantome_ontologie", max_appels=120, periode_secondes=60)
def api_fantome_ontologie_observer(request):
    chemin, refus = _contexte(request)
    if refus:
        return refus
    if len(request.body or b"") > TAILLE_MAX_CORPS:
        return JsonResponse({"erreur": t("engine/views_fantome_ontologie.py:trop_gros")}, status=413)
    try:
        corps = json.loads(request.body or b"{}")
    except ValueError:
        corps = None
    if not isinstance(corps, dict):
        return JsonResponse({"erreur": t("engine/views_fantome_ontologie.py:corps_invalide")}, status=400)
    with _verrou(chemin):
        modele = fo.charger(chemin, _cle())
        modele.observer(corps.get("episodes"), corps.get("pas"), corps.get("recherches"))
        fo.ecrire(chemin, modele, _cle())
    return JsonResponse(modele.etat_public())
