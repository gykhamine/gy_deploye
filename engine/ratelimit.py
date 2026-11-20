"""Limitation de débit basique pour les points d'entrée publics/sensibles
(login, inscription, code d'accès client...), SANS dépendance externe :
repose sur le cache Django configuré en base de données (voir
CACHES/`cache_gyplatform` dans settings.py), donc partagé entre tous les
workers Gunicorn — contrairement à un cache mémoire locale qui serait
propre à chaque process et laisserait passer largement plus que la limite
affichée dès qu'il y a plusieurs workers.

Fenêtre fixe (pas de vrai "sliding window") : suffisant pour bloquer un
brute-force ou un script de spam, sans la complexité d'une implémentation
plus fine. Compte par adresse IP.
"""
from engine.i18n import t
import logging
import time

from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger("engine.ratelimit")


def _adresse_ip(request):
    """Adresse IP du client, en tenant compte d'un eventuel reverse-proxy
    (X-Forwarded-For, pose par nginx/Caddy — seulement si GYPLATFORM_DERRIERE_PROXY
    est actif cote infra ; sinon REMOTE_ADDR reste la seule source fiable)."""
    from django.conf import settings
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff and getattr(settings, "GYPLATFORM_DERRIERE_PROXY", False):
        # Le proxy AJOUTE l'adresse réelle en dernier : la dernière entrée est la seule fiable
        # (les premières peuvent être inventées par le client pour contourner la limite).
        return xff.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "inconnue")


def adresse_ip_client(request) -> str:
    """Adresse IP réelle du client pour les journaux, l'audit et le SIEM (même règle que la limitation de débit : X-Forwarded-For seulement
    derrière un proxy déclaré). Chaîne vide si inconnue. Derrière nginx/Caddy, REMOTE_ADDR serait l'adresse du proxy pour tout le monde."""
    ip = _adresse_ip(request)
    return "" if ip == "inconnue" else ip


def limiter(cle_prefixe, max_appels=10, periode_secondes=60, methodes=None):
    """Decorateur : au plus `max_appels` requetes par `periode_secondes` et
    par IP pour la vue decoree. Au-dela, renvoie HTTP 429 (page ou JSON
    selon ce que la vue renvoie habituellement — ici toujours JSON, ce qui
    convient aussi aux vues "page" appelees en POST classique puisque le
    frontend/formulaire sait afficher un message d'erreur generique).

    `methodes` : si fourni (ex. {"POST"}), seules ces methodes HTTP sont
    comptees/limitees — un simple GET (affichage de la page de login,
    d'inscription...) n'est jamais compte comme une "tentative". Par
    defaut (None), toutes les methodes sont limitees."""

    def decorateur(vue):
        def enveloppe(request, *args, **kwargs):
            if methodes is not None and request.method not in methodes:
                return vue(request, *args, **kwargs)
            ip = _adresse_ip(request)
            fenetre = int(time.time() // periode_secondes)
            cle = f"ratelimit:{cle_prefixe}:{ip}:{fenetre}"
            try:
                try:
                    compte = cache.incr(cle)
                except ValueError:
                    # Cle absente (premiere requete de la fenetre) : incr()
                    # leve ValueError si la cle n'existe pas encore.
                    cache.set(cle, 1, timeout=periode_secondes + 5)
                    compte = 1
            except Exception:
                # Cache indisponible (table absente, base verrouillée) : le service reste accessible,
                # l'incident est tracé dans logs/securite.log.
                logger.exception(t("engine/ratelimit.py:g:66:34"), cle_prefixe)
                compte = 0
            if compte > max_appels:
                logger.warning(
                    t("engine/ratelimit.py:g:70:21"),
                    cle_prefixe, ip, request.path,
                )
                from .views_erreur import reponse_erreur   # page personnalisée pour un navigateur, JSON (message inchangé) pour le reste
                return reponse_erreur(request, 429, message=t("engine/ratelimit.py:73:33"))
            return vue(request, *args, **kwargs)

        enveloppe.__name__ = getattr(vue, "__name__", "vue")
        enveloppe.__doc__ = vue.__doc__
        return enveloppe

    return decorateur
