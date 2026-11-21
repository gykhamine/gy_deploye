"""Maintenance du modèle fantôme (jamais visible de l'interface).

  manage.py modele_fantome --entreprise 1              # état du modèle (réseaux, paramètres, échantillons appris)
  manage.py modele_fantome --entreprise 1 --entretenir  # mesure les conséquences dues + un pas de fine-tuning paresseux (jeux d'entraînement construits automatiquement)
  manage.py modele_fantome --entreprise 1 --structure   # ce que le fantôme voit de la structure complète (familles de tables : lignes, récentes) et de l'argent (TVA, wallets)
  manage.py modele_fantome --tous --entretenir          # toutes les entreprises (à brancher sur cron/systemd timer)
"""
from engine.i18n import tf
from engine.i18n import t, tl
from django.core.management.base import BaseCommand, CommandError

from engine import fantome_construction, memoire_decision, portail
from engine.fusion import fusionner_etat
from engine.models import Entreprise
from engine.recommandations import evaluer
from engine.i18n import t as _gt  # noqa: E402


class Command(BaseCommand):
    help = tl("engine/management/commands/modele_fantome.py:g:16:12")

    def add_arguments(self, parser):
        parser.add_argument("--entreprise", type=int)
        parser.add_argument("--tous", action="store_true")
        parser.add_argument("--entretenir", action="store_true")
        parser.add_argument("--structure", action="store_true")
        parser.add_argument("--metier", action="store_true", help=t("engine/management/commands/modele_fantome.py:g:23:67"))

    def handle(self, *args, **o):
        if bool(o["entreprise"]) == bool(o["tous"]):
            raise CommandError(t("engine/management/commands/modele_fantome.py:g:26:32"))
        entreprises = Entreprise.objects.all() if o["tous"] else Entreprise.objects.filter(id=o["entreprise"])
        if not entreprises.exists():
            raise CommandError(_gt("rg5.msg.088"))
        for e in entreprises:
            self._traiter(e, o)

    def _traiter(self, e, o):
        """Une entreprise à la fois, sur SA base (mode « une base par entreprise ») : rien ne passe d'une entreprise à la suivante."""
        from engine import base_par_entreprise as bpe
        with bpe.dans_entreprise(e.id):
            if o["entretenir"]:
                etat = fusionner_etat(e, forcer=True)
                bilan = memoire_decision.entretenir(e, etat, portail.graphe_dependances(e, forcer=True), evaluer(etat))
                self.stdout.write(f"[{e.id}] entretien : {bilan}")
            self.stdout.write(f"[{e.id}] {memoire_decision.etat_modele(e)}")
            self.stdout.write(f"[{e.id}] construction : {fantome_construction.resume(e)}")
            if o["metier"]:
                self.stdout.write(tf("engine/management/commands/modele_fantome.py:g:39:35", v1=e.id, v2=fantome_construction.charger_registre(fantome_construction._chemin_registre(e)).get('metier')))
            if o["structure"]:
                from engine import fantome_schema
                vecteur, rapport = fantome_schema.observer(e)
                self.stdout.write(f"[{e.id}] structure : {rapport}")
                self.stdout.write(f"[{e.id}] argent : {dict(zip(('tva', 'taux_tva', 'taxes', 'services', 'part_wallet', 'part_commission', 'depots_credites', 'remuneres'), vecteur[-fantome_schema.NB_ARGENT:]))}")
