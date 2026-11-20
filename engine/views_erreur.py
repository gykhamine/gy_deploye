"""Pages d'erreur personnalisées (400, 401, 403, 404, 405, 408, 413, 429, 500, 502, 503, 504 + échec CSRF).

Même identité que les autres pages : logo, nom, slogan et second slogan de l'entreprise (posés par marque.js sur les éléments
`.intro-logo-img` / `.intro-logo` / `.intro-sub` / `.intro-tagline`), thème (/marque.css) et langue (catalogues i18n : t() / {% t %}).
Une requête de navigateur (en-tête Accept contenant text/html, hors /api/) reçoit la page ; tout autre appel (API, fetch, XHR, curl)
reçoit du JSON {"erreur", "code"} comme le reste de l'API. Le gestionnaire 500 ne doit JAMAIS échouer : aucun accès base, repli minimal.

Branchement : handler400/403/404/500 dans gyplatform/urls.py, CSRF_FAILURE_VIEW dans settings.py, 429 dans ratelimit.py.
Avec DEBUG actif Django affiche ses propres pages techniques : /erreur/<code>/ montre la page personnalisée (statut 200) pour la vérifier.
"""
import logging

from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils.html import escape

from .i18n import langue_courante, t

logger = logging.getLogger(__name__)

_P = "engine/views_erreur.py:"
CODES = (400, 401, 403, 404, 405, 408, 413, 429, 500, 502, 503, 504)
REESSAYER = frozenset({408, 429, 500, 502, 503, 504})   # pour ceux-là, un bouton « Réessayer » recharge la page


def _veut_json(request) -> bool:
    """Page seulement pour un navigateur qui navigue : jamais pour /api/, une requête XHR ou un client qui n'attend pas du HTML."""
    if request.path.startswith("/api/") or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    return "text/html" not in request.META.get("HTTP_ACCEPT", "")


def _texte(cle: str, defaut: str = "") -> str:
    try:
        return str(t(cle))
    except Exception:
        return defaut


def reponse_erreur(request, code: int, message: str | None = None, csrf: bool = False, statut: int | None = None):
    """Page (ou JSON) d'erreur pour `code`. `message` remplace le texte par défaut (ex. message JSON historique d'une limite de débit)."""
    suffixe = "csrf" if csrf else str(code if code in CODES else (500 if code >= 500 else 400))
    titre = _texte(_P + "titre_" + suffixe, str(code))
    texte = message or _texte(_P + "message_" + suffixe)
    statut = code if statut is None else statut
    if _veut_json(request):
        return JsonResponse({"erreur": texte, "code": code}, status=statut)
    try:
        html = render_to_string("engine/erreur.html", {
            "code": code, "titre": titre, "message": texte, "langue": langue_courante(),
            "reessayer": csrf or code in REESSAYER,
        })
    except Exception:   # la page d'erreur ne doit jamais provoquer une seconde erreur
        try:
            logger.exception(t(_P + "log_rendu"), code)
        except Exception:
            pass
        html = f"<!DOCTYPE html><meta charset=\"utf-8\"><title>{code}</title><h1>{code}</h1><p>{escape(titre)}</p><p>{escape(texte)}</p>"
    return HttpResponse(html, status=statut)


def erreur_400(request, exception=None):
    return reponse_erreur(request, 400)


def erreur_403(request, exception=None):
    return reponse_erreur(request, 403)


def erreur_404(request, exception=None):
    return reponse_erreur(request, 404)


def erreur_500(request):
    return reponse_erreur(request, 500)


def erreur_csrf(request, reason=""):
    """CSRF_FAILURE_VIEW : formulaire ou session expiré (403)."""
    return reponse_erreur(request, 403, csrf=True)


def apercu(request, code):
    """Montre la page d'erreur `code` (ou « csrf ») avec le statut 200 : utile en DEBUG, où Django garde ses pages techniques."""
    if code == "csrf":
        return reponse_erreur(request, 403, csrf=True, statut=200)
    if code.isdigit() and int(code) in CODES:
        return reponse_erreur(request, int(code), statut=200)
    return reponse_erreur(request, 404)
