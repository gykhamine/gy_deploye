"""Sécurité des dates : le temps de la plateforme ne remonte JAMAIS.

Règle unique : la date d'une nouvelle action (quelle que soit sa nature : ligne, wallet, import,
configuration, connexion super-admin…) ne peut pas être antérieure à celle de la dernière action
déjà enregistrée. Sinon : refus.

Où elle s'applique (un seul module, trois points d'appui) :
  1. `Bloc._committer_bloc` / `BlocSysteme._committer` — sous verrou, juste avant d'écrire le bloc :
     c'est la garantie forte (aucune course possible entre deux workers/nœuds). Chaque action métier
     écrit un bloc, donc « la dernière action » = le dernier bloc de la chaîne.
  2. `DateSecuriteMiddleware` — AVANT la vue : une requête qui modifierait des données alors que
     l'horloge est revenue en arrière est refusée (409) sans rien toucher, au lieu d'écrire des lignes
     puis d'échouer au moment de sceller le bloc.
  3. `Bloc.chaine_valide` / `BlocSysteme.chaine_valide` — l'historique complet depuis le début
     doit lui aussi être croissant (une date remontée dans la chaîne = chaîne invalide).

Les dates sont des epoch UTC (`time.time()`) : le fuseau horaire n'entre jamais en jeu.
`GYPLATFORM_TOLERANCE_HORLOGE` (secondes, défaut 0 = strict) existe pour un cluster dont les
nœuds auraient un léger décalage NTP ; elle n'est pas cumulative (le refus reste sur la plus
récente date connue).
"""
import os
import time
from engine.i18n import tf as _gtf  # noqa: E402


class DateRetrograde(Exception):
    """Levée quand une action serait datée avant la dernière action enregistrée."""

    def __init__(self, derniere, maintenant):
        self.derniere, self.maintenant = derniere, maintenant
        super().__init__(
            _gtf("rg6.msg.104", v1=_iso(derniere), v2=_iso(maintenant))
        )


def _iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _tolerance():
    try:
        return max(0.0, float(os.environ.get("GYPLATFORM_TOLERANCE_HORLOGE", "0")))
    except ValueError:
        return 0.0


def exiger(maintenant, derniere):
    """Lève DateRetrograde si `maintenant` < `derniere` (None = aucune action avant : tout est permis)."""
    if derniere is not None and maintenant + _tolerance() < derniere:
        raise DateRetrograde(derniere, maintenant)


def derniere_action(entreprise_id):
    """Epoch de la toute dernière action de l'entreprise : le plus récent entre le dernier bloc et
    la dernière ligne d'audit (une requête refusée ou purement consultative n'y figure pas)."""
    from .models import Bloc, JournalAudit
    dates = []
    ts_bloc = Bloc.objects.filter(entreprise_id=entreprise_id).order_by("-index").values_list("timestamp", flat=True).first()
    if ts_bloc is not None:
        dates.append(ts_bloc)
    h = JournalAudit.objects.filter(entreprise_id=entreprise_id).order_by("-id").values_list("horodatage", flat=True).first()
    if h is not None:
        dates.append(h.timestamp())
    return max(dates) if dates else None


def controler(entreprise_id):
    """Contrôle en amont d'une opération longue (import, restauration…) : refuse avant d'écrire."""
    exiger(time.time(), derniere_action(entreprise_id))

