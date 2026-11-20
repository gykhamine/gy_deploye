"""Émet les rappels d'agenda arrivés à l'échéance (notification + relais SMS éventuel), même si personne n'est connecté.

    python manage.py rappels_agenda              # une fois
    python manage.py rappels_agenda --boucle 60  # en service : un rappel part dans la minute qui suit l'échéance

Sans cette commande, les rappels à l'heure partent quand même dès que quelqu'un de l'entreprise ouvre la cloche ou l'agenda ; elle
sert surtout au relais SMS d'un destinataire absent. Le rappel de RETARD, lui, n'est jamais émis ici : il l'est à la reconnexion du destinataire.
"""
from engine.i18n import t, tf, tl
import time

from django.core.management.base import BaseCommand

from engine import agenda
from engine.models import Entreprise


class Command(BaseCommand):
    help = tl("engine/management/commands/rappels_agenda.py:19:15")

    def add_arguments(self, p):
        p.add_argument("--boucle", type=int, default=0, help=t("engine/management/commands/rappels_agenda.py:22:64"))

    def handle(self, *args, **o):
        try:
            while True:
                n = sum(agenda.emettre(e) for e in Entreprise.objects.all())
                if n or not o["boucle"]:
                    self.stdout.write(tf("engine/management/commands/rappels_agenda.py:29:42", v1=n))
                if not o["boucle"]:
                    break
                time.sleep(max(o["boucle"], 5))
        except KeyboardInterrupt:
            pass
