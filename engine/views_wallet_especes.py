"""API et pages du wallet en interne : agent habilité (dépôts et retraits en espèces), transferts internes, comptabilité (administrateur).
La logique est dans wallet_especes.py et comptabilite.py ; ici, seulement les droits, la lecture des requêtes et les réponses."""
from engine.montant import saisie
import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from engine.i18n import t, tf
from .delegation import a_pouvoir as _a_pouvoir
from . import comptabilite as _c, wallet as _w, wallet_especes as _e
from .models import MembreEntreprise
from .ratelimit import limiter
from .views import _entreprise_courante
from .views_wallet import _membre_courant

TYPES_ESPECES = ["depot_especes", "retrait_especes"]


def _json(request) -> dict:
    try:
        d = json.loads(request.body or b"{}")
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _erreur(message, statut=400):
    return JsonResponse({"error": message}, status=statut)


def _entier(v, defaut, mini, maxi):
    try:
        return max(mini, min(int(v), maxi))
    except (TypeError, ValueError):
        return defaut


def _montant(v):
    try:
        return saisie(v)
    except (TypeError, ValueError):
        raise ValueError(t("engine/views_wallet_especes.py:g:43:26"))


def _ctx(request):
    entreprise = _entreprise_courante(request)
    return entreprise, _membre_courant(request, entreprise)


def _membre_de(entreprise, valeur):
    try:
        return MembreEntreprise.objects.filter(entreprise=entreprise, pk=int(valeur)).select_related("utilisateur").first()
    except (TypeError, ValueError):
        return None


def _admin(request, domaine=None):
    """Administrateur ; avec `domaine`, aussi l'agent à qui ce domaine est délégué (delegation.py)."""
    entreprise, membre = _ctx(request)
    if not (_a_pouvoir(membre, domaine) if domaine else membre is not None and membre.role == "admin"):
        return None, None, _erreur(t("engine/views_wallet_especes.py:g:61:36"), 403)
    return entreprise, membre, None


def _habilite(request):
    entreprise, membre = _ctx(request)
    if not membre or not _e.est_habilite(membre):
        return None, None, _erreur(t("engine/views_wallet_especes.py:g:68:36"), 403)
    return entreprise, membre, None


# ── Pages ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

@login_required
@ensure_csrf_cookie
def vue_agent_flux(request):
    """Page de l'agent habilité : recevoir un dépôt, remettre un retrait, voir ses espèces et ses opérations."""
    entreprise, membre = _ctx(request)
    return render(request, "engine/agent_flux.html", {"entreprise": entreprise, "habilite": _e.est_habilite(membre)})


@login_required
@ensure_csrf_cookie
def vue_comptabilite_wallet(request):
    entreprise, membre = _ctx(request)
    if not _a_pouvoir(membre, "comptabilite"):
        return HttpResponseForbidden(t("engine/views_wallet_especes.py:g:87:38"))
    return render(request, "engine/comptabilite_wallet.html", {"entreprise": entreprise})


# ── Agent habilité ──────────────────────────────────────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
@login_required
@limiter("agent_membres", max_appels=120, periode_secondes=300)
def agent_membres(request):
    """Recherche du client (ou de l'agent) concerné, avec son agent responsable et son solde. Habilité seulement."""
    entreprise, membre, refus = _habilite(request)
    if refus:
        return refus
    return JsonResponse({"membres": _e.chercher_membres(entreprise, request.GET.get("q"), detail=True),
                         "agents": _e.agents_choisissables(entreprise)})


def _operation(request, fonction):
    entreprise, operateur, refus = _habilite(request)
    if refus:
        return refus
    d = _json(request)
    client = _membre_de(entreprise, d.get("client_id"))
    agent = _membre_de(entreprise, d.get("agent_id")) if d.get("agent_id") not in (None, "") else None
    try:
        r = fonction(entreprise, operateur, client, _montant(d.get("montant")), agent=agent, cle=d.get("cle"), motif=d.get("motif"))
    except _e.RefusPermission as ex:
        return _erreur(str(ex), 403)
    except ValueError as ex:
        return _erreur(str(ex))
    return JsonResponse({"ok": True, "deja_enregistre": r["deja"], "solde_client": r["solde"], "frais": r["frais"], "ecriture": _c.ecriture_en_dict(r["ecriture"])})


@require_http_methods(["POST"])
@csrf_protect
@login_required
@limiter("agent_depot", max_appels=60, periode_secondes=300, methodes={"POST"})
def agent_depot(request):
    """POST {client_id, montant, agent_id?, cle, motif?} — le client a remis des espèces à l'agent : son wallet est crédité."""
    return _operation(request, _e.enregistrer_depot_especes)


@require_http_methods(["POST"])
@csrf_protect
@login_required
@limiter("agent_retrait", max_appels=60, periode_secondes=300, methodes={"POST"})
def agent_retrait(request):
    """POST {client_id, montant, agent_id?, cle, motif?} — l'agent remet des espèces au client : son wallet est débité."""
    return _operation(request, _e.enregistrer_retrait_especes)


@require_http_methods(["GET"])
@login_required
def agent_operations(request):
    """Résumé de l'agent (espèces détenues, opérations du jour) et ses dernières opérations. L'administrateur voit toutes celles de l'entreprise."""
    entreprise, membre, refus = _habilite(request)
    if refus:
        return refus
    limite = _entier(request.GET.get("limite"), 50, 1, 200)
    toutes = membre.role == "admin"
    return JsonResponse({"resume": _e.resume_agent(entreprise, membre),
                         "operations": _c.lister(entreprise, types=TYPES_ESPECES, operateur=None if toutes else membre, limite=limite)})


# ── Tout membre : destinataires et transferts ──────────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
@login_required
@limiter("destinataires", max_appels=60, periode_secondes=300)
def destinataires(request):
    """Recherche d'un destinataire de transfert (nom, numéro de client ou téléphone) : jamais de solde ni de téléphone dans la réponse."""
    entreprise, membre = _ctx(request)
    if not membre:
        return _erreur(t("engine/views_wallet_especes.py:g:161:24"), 404)
    return JsonResponse({"membres": _e.chercher_membres(entreprise, request.GET.get("q"), exclure=membre)})


@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def politique_transfert(request):
    """GET : la politique de transferts (tout membre). POST (administrateur) : la modifie."""
    entreprise, membre = _ctx(request)
    if not membre:
        return _erreur(t("engine/views_wallet_especes.py:g:172:24"), 404)
    if request.method == "POST":
        if not _a_pouvoir(membre, "politique_transfert"):
            return _erreur(t("engine/views_wallet_especes.py:g:175:28"), 403)
        try:
            pol = _e.enregistrer_politique(entreprise, _json(request))
        except ValueError as ex:
            return _erreur(str(ex))
        return JsonResponse({"ok": True, "politique": _e.politique_en_dict(pol)})
    return JsonResponse({"politique": _e.politique_en_dict(_e.politique(entreprise))})


# ── Administrateur ──────────────────────────────────────────────────────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def habilitations(request):
    """GET : les agents et leur habilitation. POST {membre_id, actif} : donne ou retire le droit d'enregistrer des espèces."""
    entreprise, membre, refus = _admin(request, "especes_habilitations")
    if refus:
        return refus
    if request.method == "POST":
        d = _json(request)
        try:
            _e.accorder(entreprise, membre, _membre_de(entreprise, d.get("membre_id")), d.get("actif", True))
        except _e.RefusPermission as ex:
            return _erreur(str(ex), 403)
        except ValueError as ex:
            return _erreur(str(ex))
    return JsonResponse({"agents": _e.lister_habilites(entreprise)})


@require_http_methods(["POST"])
@csrf_protect
@login_required
def especes_mouvement(request):
    """Administrateur — POST {agent_id, montant, sens: remise|avance, motif?, cle?} : espèces reversées par un agent ou avancées à un agent."""
    entreprise, membre, refus = _admin(request, "especes_mouvements")
    if refus:
        return refus
    d = _json(request)
    try:
        e = _e.mouvement_especes_agent(entreprise, membre, _membre_de(entreprise, d.get("agent_id")), _montant(d.get("montant")),
                                       d.get("sens") or "remise", d.get("motif"), d.get("cle"))
    except _e.RefusPermission as ex:
        return _erreur(str(ex), 403)
    except ValueError as ex:
        return _erreur(str(ex))
    return JsonResponse({"ok": True, "ecriture": _c.ecriture_en_dict(e), "agents": _c.especes_par_agent(entreprise)})


@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def comptabilite(request):
    """Administrateur — GET ?jours=N&type=…&limite=N : le bilan comptable, les écritures et la cohérence. POST : réécrit les blocs manquants."""
    entreprise, membre, refus = _admin(request, "comptabilite")
    if refus:
        return refus
    if request.method == "POST":
        return JsonResponse({"ok": True, **_c.reparer_blocs(entreprise)})
    jours = _entier(request.GET.get("jours"), 30, 1, 3650)
    types = [x for x in request.GET.getlist("type") if x in _c.TYPES]
    return JsonResponse({"bilan": _c.bilan(entreprise, jours), "ecritures": _c.lister(entreprise, jours=jours, types=types or None, limite=_entier(request.GET.get("limite"), 200, 1, 500)),
                         "politique": _e.politique_en_dict(_e.politique(entreprise)), "agents": _e.lister_habilites(entreprise),
                         "types": [{"code": c, "libelle": _c.libelle_type(c)} for c in _c.TYPES]})
