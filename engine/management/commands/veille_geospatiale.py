"""Veille géospatiale en arrière-plan : surveille toutes les tables de positions, une alerte (rapprochement, collision prévue) devient une notification.

    python manage.py veille_geospatiale              # une passe
    python manage.py veille_geospatiale --boucle 60  # en service (à planifier comme tâche programmée de l'hébergeur web au premier appel)

Une seule veille à la fois (verrou de fichier). Voir engine/geo_veille.py et engine/geo_surveillance.py.
"""
from engine.i18n import t, tf
from engine.i18n import tl
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from engine import geo_veille, geo_surveillance


class Command(BaseCommand):
    help = tl("engine/management/commands/veille_geospatiale.py:g:18:12")

    def add_arguments(self, p):
        p.add_argument("--boucle", type=int, default=0, help=t("engine/management/commands/veille_geospatiale.py:g:22:62"))

    def _passe(self):
        from engine.models import TableDynamique
        close_old_connections()   # processus long : la connexion à la base a pu être fermée
        n = 0
        for table in TableDynamique.objects.filter(champs__type_champ="geolocalisation").select_related("entreprise").distinct().order_by("id")[:500]:
            n += geo_surveillance.surveiller(table)
        return n

    def handle(self, *args, **o):
        geo_veille.DOSSIER.mkdir(parents=True, exist_ok=True)
        geo_veille.declarer_processus()
        try:
            while True:
                try:
                    n = self._passe()
                    geo_veille.declarer_processus()
                    if n:
                        self.stdout.write(tf("engine/management/commands/veille_geospatiale.py:g:46:43", v1=n))
                except Exception as e:   # une passe qui échoue (base indisponible...) n'arrête jamais la veille
                    self.stderr.write(tf("engine/management/commands/veille_geospatiale.py:g:48:39", v1=e))
                if not o["boucle"]:
                    break
                time.sleep(max(o["boucle"], 10))
        except KeyboardInterrupt:
            pass
