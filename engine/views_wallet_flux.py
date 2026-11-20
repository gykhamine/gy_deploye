"""API des flux du wallet : moyens de retrait (chaque membre gère les siens), historique des retraits, charges et coûts de l'entreprise
(administrateur), journal des prestataires et bilan des entrées et sorties (administrateur). La logique est dans wallet_flux.py."""
from engine.montant import saisie
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from .delegation import a_pouvoir as _a_pouvoir
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from . import wallet_flux as _flux
from .ratelimit import limiter
from .views import _entreprise_courante
from .views_wallet import _membre_courant
from engine.i18n import t as _gt  # noqa: E402


def _json(request) -> dict:
    try:
        donnees = json.loads(request.body or b"{}")
    except Exception:
        return {}
    return donnees if isinstance(donnees, dict) else {}


def _erreur(message, statut=400):
    return JsonResponse({"error": message}, status=statut)


def _entier(valeur, defaut, mini, maxi):
    try:
        return max(mini, min(int(valeur), maxi))
    except (TypeError, ValueError):
        return defaut


def _admin(request, droit):
    """(entreprise, membre, None) pour l'administrateur de l'entreprise ou l'agent à qui le `droit` demandé est confié ; sinon (None, None, réponse d'erreur)."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not _a_pouvoir(membre, droit):
        return None, None, _erreur(_i18n_t("rg2.views__part_008.1"), 403)
    return entreprise, membre, None






@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def charges_wallet(request):
    """Administrateur — charges et coûts de l'entreprise, payés depuis la caisse.
    GET ?jours=N : la liste (N jours, sinon tout) et le total. POST {categorie, libelle, montant, date, beneficiaire} : une charge de plus."""
    entreprise, membre, refus = _admin(request, "caisse_sorties")
    if refus:
        return refus
    if request.method == "POST":
        data = _json(request)
        try:
            montant = saisie(data.get("montant"))
        except (TypeError, ValueError):
            return _erreur(_gt("rg5.msg.058"))
        try:
            charge = _flux.enregistrer_charge(entreprise, membre, data.get("categorie"), data.get("libelle"), montant, data.get("date"), data.get("beneficiaire"))
        except ValueError as e:
            return _erreur(str(e))
        return JsonResponse({"ok": True, "charge": _flux.charge_en_dict(charge)})
    jours = _entier(request.GET.get("jours"), 0, 0, 3650)
    charges = _flux.lister_charges(entreprise, jours or None)
    return JsonResponse({"charges": charges, "total": sum(c["montant"] for c in charges),
                         "categories": [{"code": c, "libelle": l} for c, l in _flux.ChargeWallet.CATEGORIES]})




@require_http_methods(["GET"])
@login_required
def bilan_wallet(request):
    """Administrateur — entrées et sorties de l'entreprise sur `?jours=N` (30 par défaut)."""
    entreprise, _membre, refus = _admin(request, "journal_bilan")
    if refus:
        return refus
    return JsonResponse(_flux.bilan(entreprise, _entier(request.GET.get("jours"), 30, 1, 3650)))
