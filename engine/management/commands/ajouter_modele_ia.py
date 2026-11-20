"""Ajoute un type de modèle DL/ML au Trainer depuis un script (canal modele_ia).

  manage.py ajouter_modele_ia mon_modele.py --entreprise 1
  manage.py ajouter_modele_ia --generer "perceptron à deux couches" --entreprise 1
"""
from engine.i18n import tl
from django.core.management.base import BaseCommand, CommandError

from engine.i18n import t, tf
from engine.executor import modules_modele_ia_a_jour
from engine.inference import MoteurInference
from engine.models import ConfigurationIA, Entreprise
from IA.plugins_modeles import installer


class Command(BaseCommand):
    help = tl("engine/management/commands/ajouter_modele_ia.py:g:15:12")

    def add_arguments(self, parser):
        parser.add_argument("fichier", nargs="?")
        parser.add_argument("--generer", help=t("engine/management/commands/ajouter_modele_ia.py:g:20:47"))
        parser.add_argument("--entreprise", type=int, required=True)

    def handle(self, *args, **o):
        entreprise = Entreprise.objects.filter(id=o["entreprise"]).first()
        if not entreprise or bool(o["fichier"]) == bool(o["generer"]):
            raise CommandError(t("engine/management/commands/ajouter_modele_ia.py:entreprise"))
        if o["generer"]:
            code, erreur = MoteurInference(entreprise=entreprise).generer_modele_ia(o["generer"])
            if erreur:
                raise CommandError(erreur)
        else:
            with open(o["fichier"], encoding="utf-8") as f:
                code = f.read()
        cfg = ConfigurationIA.objects.filter(entreprise=entreprise).first()
        nom, erreur = installer(code, modules_modele_ia_a_jour(cfg.modules_autorises_modele_ia if cfg else ""))
        if erreur:
            raise CommandError(erreur)
        self.stdout.write(tf("engine/management/commands/ajouter_modele_ia.py:installe", nom=nom))
