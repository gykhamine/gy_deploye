"""Réunit dans la base unique les données d'anciens fichiers d'entreprise (media/systeme/entreprises/<id>.sqlite3).

    python manage.py reunir_base_entreprises            # copie ; les fichiers ne sont ni modifiés ni supprimés
    python manage.py reunir_base_entreprises --entreprise 3

Les lignes déjà présentes dans la base unique (même identifiant) ne sont jamais écrasées. Sans fichier d'entreprise : ne fait rien.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from engine import base_par_entreprise as bpe
from engine.management.commands._textes import m


class Command(BaseCommand):
    @property
    def help(self):
        return m("commandes.reunir.help")

    def add_arguments(self, parser):
        parser.add_argument("--entreprise", type=int, default=None)

    def handle(self, *args, **o):
        modeles = bpe.modeles_locataires()
        for f in sorted(bpe.dossier().glob("*.sqlite3")):
            if not f.stem.isdigit() or (o["entreprise"] and int(f.stem) != o["entreprise"]):
                continue
            nom = bpe.enregistrer(int(f.stem))
            total = 0
            with transaction.atomic(using="default"):
                for mod in modeles:
                    deja = set(mod._base_manager.using("default").values_list("pk", flat=True))
                    lot = [x for x in mod._base_manager.using(nom).all().iterator() if x.pk not in deja]
                    if lot:
                        mod._base_manager.using("default").bulk_create(lot, batch_size=500)
                        total += len(lot)
            self.stdout.write(m("commandes.reunir.fait", id=f.stem, n=total))
