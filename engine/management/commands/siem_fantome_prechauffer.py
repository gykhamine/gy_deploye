"""Préchauffe le modèle fantôme du SIEM avec l'historique, pour qu'il ne démarre pas à froid.

    python manage.py siem_fantome_prechauffer                       # 30 jours de messages, avis et activité des comptes
    python manage.py siem_fantome_prechauffer --jours 14 --messages 2000 --instants 150
    python manage.py siem_fantome_prechauffer --forcer              # même si le modèle a déjà appris (l'historique compte alors deux fois)

À lancer UNE fois, avant ou juste après la mise en service : les règles de base servent de professeur sur le passé, les avis déjà donnés de juge.
Le modèle ne se met à alerter qu'une fois « mature » (voir la page SIEM) ; cette commande ne déclenche aucune alerte.
"""
from django.core.management.base import BaseCommand

from engine import siem_fantome_suivi as suivi
from engine.i18n import tf as _siem_tf  # noqa: E402


class Command(BaseCommand):
    @property
    def help(self):
        return _siem_tf("rg4.siem.msg.210")

    def add_arguments(self, parser):
        parser.add_argument("--jours", type=int, default=30, help=_siem_tf("rg4.siem.msg.065"))
        parser.add_argument("--messages", type=int, default=5000, help=_siem_tf("rg4.siem.msg.066"))
        parser.add_argument("--instants", type=int, default=300, help=_siem_tf("rg4.siem.msg.067"))
        parser.add_argument("--forcer", action="store_true", help=_siem_tf("rg4.siem.msg.068"))

    def handle(self, *args, **options):
        resume = suivi.prechauffer(jours=options["jours"], max_messages=options["messages"], max_instants=options["instants"], forcer=options["forcer"])
        if resume.get("deja_appris"):
            self.stdout.write(self.style.WARNING(_siem_tf("rg4.siem.msg.070")))
            return
        self.stdout.write(self.style.SUCCESS(_siem_tf("rg4.siem.msg.069", v1=resume['messages'], v2=resume['avis'], v3=resume['comptes'])))
