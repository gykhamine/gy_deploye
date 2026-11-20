"""Cache DÉDIÉ au SIEM (profils de comportement, verrous de compte, blocs d'IP, dédoublonnage des alertes, échéances, battements).

Pourquoi un cache à part : le cache par défaut reçoit aussi les clés de limitation de débit, qu'un attaquant peut multiplier à volonté ;
au-delà de MAX_ENTRIES il supprime des entrées, et l'état du SIEM (un verrou de compte, un profil appris) pouvait partir avec. Ici, seul le SIEM
écrit : une inondation de requêtes ne peut plus l'évincer. Table séparée (`cache_siem`, voir settings.CACHES["siem"]), créée automatiquement.

Sans entrée « siem » dans CACHES, retombe sur le cache par défaut (comportement d'avant). Si la table n'existe pas encore (installation neuve,
mise à jour), elle est créée une fois par processus puis l'appel est rejoué ; sans y parvenir, l'erreur remonte comme avant.
"""
import logging

from django.conf import settings
from django.core.cache import caches
from django.db import DatabaseError
from engine.i18n import tf as _siem_tf  # noqa: E402

logger = logging.getLogger("engine.siem")
_table_tentee = False


def _backend():
    return caches["siem"] if "siem" in settings.CACHES else caches["default"]


def _creer_table() -> bool:
    """createcachetable (idempotent) une seule fois par processus. True si la tentative a eu lieu sans erreur."""
    global _table_tentee
    if _table_tentee:
        return False
    _table_tentee = True
    try:
        from django.core.management import call_command
        call_command("createcachetable", verbosity=0)
        return True
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.291"))
        return False


class _CacheSiem:
    def __getattr__(self, nom):
        methode = getattr(_backend(), nom)
        if not callable(methode):
            return methode

        def appel(*args, **kwargs):
            try:
                return methode(*args, **kwargs)
            except DatabaseError:
                if _creer_table():
                    return getattr(_backend(), nom)(*args, **kwargs)
                raise
        return appel


cache = _CacheSiem()
