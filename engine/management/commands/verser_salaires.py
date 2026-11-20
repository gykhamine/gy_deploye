"""Verse les salaires des agents arrivés à échéance (de la caisse de l'entreprise vers leur wallet, tracé dans la blockchain).

    python manage.py verser_salaires              # une fois
    python manage.py verser_salaires --boucle 3600  # en service : contrôle toutes les heures (le travail est idempotent : un salaire n'est versé qu'une fois par mois)
"""
from engine.i18n import t, tf
from engine.i18n import tl
import time

from django.core.management.base import BaseCommand

from engine import remuneration
from engine.models import Entreprise


class Command(BaseCommand):
    help = tl("engine/management/commands/verser_salaires.py:g:15:12")

    def add_arguments(self, p):
        p.add_argument("--boucle", type=int, default=0, help=t("engine/management/commands/verser_salaires.py:g:19:62"))

    def handle(self, *args, **o):
        try:
            while True:
                n = sum(remuneration.verser_dus(e) for e in Entreprise.objects.all())
                if n or not o["boucle"]:
                    self.stdout.write(tf("engine/management/commands/verser_salaires.py:g:26:39", v1=n))
                if not o["boucle"]:
                    break
                time.sleep(max(o["boucle"], 60))
        except KeyboardInterrupt:
            pass
