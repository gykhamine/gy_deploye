"""Services payants internes : prix des services et pages fermées aux clients (le wallet se recharge en espèces chez un agent habilité)."""
import json
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required

@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def services_payants(request):
    """Récupère ou enregistre les prix des services payants (enregistrés en base, FraisWallet.services) : montant fixe ou
    % du wallet du client par catégorie (frais_taxes.SERVICES_CATEGORIES).

    Lecture (GET) ouverte aux clients (affichage des prix). Écriture
    (POST) réservée aux agents/admin (faille corrigée 24/09 : un client
    authentifié pouvait jusqu'ici fixer lui-même les prix des services
    payants de toute l'entreprise, faute de contrôle de rôle)."""
    from .views import _entreprise_courante, _interdit_lecteur, _interdit_non_admin
    entreprise = _entreprise_courante(request)
    from . import wallet as _wallet
    if request.method == 'GET':
        return JsonResponse(_wallet.services_payants(entreprise))

    _bloque_lecteur = _interdit_non_admin(request, "paiements_config")
    if _bloque_lecteur:
        return _bloque_lecteur
    try:
        _wallet.enregistrer_services(entreprise, json.loads(request.body))
        return JsonResponse({'ok': True})
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

@require_http_methods(["GET", "POST"])
@login_required
def pages_client(request):
    """Pages d'analyse fermées aux clients. Lecture ouverte à tous les connectés ; écriture réservée à l'admin.
    Corps POST : {"fermees": ["anova", ...]}."""
    from .views import _entreprise_courante, _interdit_non_admin
    from . import wallet as _wallet
    entreprise = _entreprise_courante(request)
    if request.method == 'GET':
        return JsonResponse({'fermees': sorted(_wallet.pages_client_fermees(entreprise)), 'pages': list(_wallet.PAGES_CLIENT)})
    _bloque = _interdit_non_admin(request, "paiements_config")
    if _bloque:
        return _bloque
    try:
        fermees = _wallet.enregistrer_pages_client(entreprise, json.loads(request.body).get('fermees'))
        return JsonResponse({'ok': True, 'fermees': fermees})
    except (ValueError, AttributeError) as e:
        return JsonResponse({'error': str(e)}, status=400)














# ── Faux positifs : dépôts mis en attente de décision (super administrateur) ─────────────────────────────────────────



