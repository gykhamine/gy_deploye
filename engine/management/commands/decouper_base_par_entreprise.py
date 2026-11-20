"""Découpe la base actuelle en UN FICHIER SQLite PAR ENTREPRISE (mode GYPLATFORM_BASE=sqlite_par_entreprise).

    python manage.py decouper_base_par_entreprise              # copie ; la base système n'est PAS modifiée (reste une sauvegarde)
    python manage.py decouper_base_par_entreprise --entreprise 3
    python manage.py decouper_base_par_entreprise --ecraser    # refait un fichier déjà rempli
    python manage.py decouper_base_par_entreprise --purger     # après copie ET vérification des comptes de lignes, retire les
                                                               # données d'entreprise de la base système

Préalable : sauvegarder (/plateforme/sauvegarde/), arrêter l'application, lancer avec GYPLATFORM_BASE=sqlite_par_entreprise.
Sûreté : la copie se contrôle ligne par ligne (compte par modèle) ; rien n'est purgé si un seul compte diffère.
"""
from django.core.management.base import BaseCommand, CommandError

from engine import base_par_entreprise as bpe
from engine.management.commands._textes import m
from engine.models import Entreprise
from engine.securite import _chemin_entreprise


class Command(BaseCommand):
    @property
    def help(self):
        return m("commandes.decouper.help")

    def add_arguments(self, parser):
        parser.add_argument("--entreprise", type=int, help=m("commandes.decouper.arg_entreprise"))
        parser.add_argument("--ecraser", action="store_true", help=m("commandes.decouper.arg_ecraser"))
        parser.add_argument("--purger", action="store_true", help=m("commandes.decouper.arg_purger"))

    def handle(self, *args, **o):
        if not bpe.actif():
            raise CommandError(m("commandes.bpe.non_actif"))
        modeles = bpe.modeles_locataires()
        chemins = {mod: _chemin_entreprise(mod) for mod in modeles}
        sans_chemin = [mod.__name__ for mod, c in chemins.items() if not c]
        if sans_chemin:
            raise CommandError(m("commandes.decouper.sans_chemin", liste=", ".join(sans_chemin)))
        entreprises = Entreprise.objects.using("default").order_by("id")
        if o["entreprise"]:
            entreprises = entreprises.filter(pk=o["entreprise"])
        for ent in entreprises:
            self._une(ent, modeles, chemins, o)

    def _une(self, ent, modeles, chemins, o):
        nom = bpe.preparer(ent.pk)
        self.stdout.write(m("commandes.decouper.entreprise_vers", id=ent.pk, chemin=bpe.chemin(ent.pk)))
        ecarts = []
        for mod in modeles:
            src = mod._base_manager.using("default").filter(**{chemins[mod]: ent.pk})
            dst = mod._base_manager.using(nom)
            if dst.exists():
                if not o["ecraser"]:
                    self.stdout.write(m("commandes.decouper.deja_rempli", modele=mod.__name__))
                    continue
                dst.all().delete()
            lot = []
            for obj in src.iterator(chunk_size=500):
                lot.append(obj)
                if len(lot) >= 500:
                    mod._base_manager.using(nom).bulk_create(lot)
                    lot = []
            if lot:
                mod._base_manager.using(nom).bulk_create(lot)
            a, b = src.count(), mod._base_manager.using(nom).count()
            if a != b:
                ecarts.append(f"{mod.__name__}: {a} → {b}")
        if ecarts:
            raise CommandError(m("commandes.decouper.ecarts", id=ent.pk, ecarts="; ".join(ecarts)))
        if o["purger"]:
            for mod in reversed(modeles):
                mod._base_manager.using("default").filter(**{chemins[mod]: ent.pk}).delete()
            self.stdout.write(m("commandes.decouper.purge"))
        self.stdout.write(self.style.SUCCESS(m("commandes.decouper.copies", n=len(modeles))))
