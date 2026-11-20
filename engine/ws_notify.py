"""Notifications « temps réel » — version sans WebSocket (Django pur).

L'hébergement visé (PythonAnywhere) ne sert pas les WebSocket : plus de Channels, de Daphne ni de Redis. Les fonctions
ci-dessous gardent leur signature et leurs contrôles de forme (les appelants n'ont pas changé) mais ne diffusent plus rien :
le temps réel était un confort, jamais une condition. Toutes les informations restent disponibles par les requêtes HTTP
habituelles (cloche et notifications lues en base, statut des tâches interrogé par polling, messages relus à l'ouverture).
"""
from engine.i18n import t
import logging

logger = logging.getLogger(__name__)


def _group_send(group_name, message):
    """Sans couche temps réel : rien à diffuser (aucune erreur, aucun blocage)."""
    logger.debug("temps réel désactivé : %s ignoré", group_name)


def notifier_progression_tache(task_id, *, statut, progression, message=None):
    """Progression d'une tâche (validation de forme conservée ; l'écran interroge le statut en base)."""
    if not isinstance(statut, str) or not statut:
        raise ValueError(t("engine/ws_notify.py:g:45:26"))
    if not isinstance(progression, int) or not (0 <= progression <= 100):
        raise ValueError(t("engine/ws_notify.py:g:47:26"))
    if message is not None and not isinstance(message, str):
        raise ValueError(t("engine/ws_notify.py:g:49:26"))
    _group_send(f"task_{task_id}", {"statut": statut, "progression": progression, "message": message})


def notifier_utilisateur(user_id, *, message, id=None, titre=None, lien=None, type_notification=None):
    """Notification d'un utilisateur (déjà enregistrée en base : la cloche la relit)."""
    if not isinstance(message, str) or not message:
        raise ValueError(t("engine/ws_notify.py:g:69:26"))
    _group_send(f"notif_{user_id}", {"message": message, "id": id})


def notifier_entreprise(entreprise_id, *, evenement, donnees=None):
    """Événement d'entreprise (validation de forme conservée)."""
    if not isinstance(evenement, str) or not evenement:
        raise ValueError(t("engine/ws_notify.py:g:89:26"))
    if donnees is not None and not isinstance(donnees, dict):
        raise ValueError(t("engine/ws_notify.py:g:91:26"))
    _group_send(f"entreprise_{entreprise_id}", {"evenement": evenement, "donnees": donnees or {}})


def notifier_appel_entrant(user_id, *, conversation_id, de, video, titre=""):
    """Sonnerie d'appel (validation de forme conservée)."""
    if not isinstance(conversation_id, int):
        raise ValueError(t("engine/ws_notify.py:g:108:26"))
    if not isinstance(de, str) or not de:
        raise ValueError(t("engine/ws_notify.py:g:110:26"))
    _group_send(f"notif_{user_id}", {"type": "appel_entrant", "conversation_id": conversation_id})
