"""Applique les migrations à la base système PUIS à chaque fichier d'entreprise (mode sqlite_par_entreprise).

    python manage.py migrer_entreprises

À lancer après chaque mise à jour du code. (Un fichier en retard est aussi rattrapé à sa première utilisation, mais mieux vaut
migrer tout de suite et voir une éventuelle erreur ici plutôt qu'en production.)
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from engine import base_par_entreprise as bpe
from engine.management.commands._textes import m
from engine.models import Entreprise


class Command(BaseCommand):
    @property
    def help(self):
        return m("commandes.migrer.help")

    def handle(self, *args, **o):
        if not bpe.actif():
            raise CommandError(m("commandes.bpe.non_actif"))
        call_command("migrate", database="default", interactive=False, verbosity=1)
        echecs = []
        for eid in Entreprise.objects.using("default").order_by("id").values_list("pk", flat=True):
            try:
                bpe.preparer(eid)
                self.stdout.write(m("commandes.migrer.a_jour", id=eid))
            except Exception as exc:   # une entreprise en échec ne bloque pas les autres
                echecs.append(f"{eid}: {exc}")
                self.stderr.write(m("commandes.migrer.echec", id=eid, erreur=exc))
        if echecs:
            raise CommandError(m("commandes.migrer.non_migres", liste=" | ".join(echecs)))
