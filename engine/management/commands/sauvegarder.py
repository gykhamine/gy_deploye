"""Sauvegarde complète et cohérente : base de données + fichiers médias, avec rotation.

    python manage.py sauvegarder            # une archive dans GYPLATFORM_SAUVEGARDES (défaut BASE_DIR/sauvegardes)
    python manage.py sauvegarder --garder 30

Archive : sauvegarde_AAAAMMJJ_HHMMSS.tar.gz contenant
  - db.sqlite3   : copie À CHAUD via l'API de sauvegarde SQLite (cohérente même en mode WAL, pendant
                   que l'application tourne) — ou donnees.json (dumpdata) pour une autre base ;
  - media/       : tous les fichiers (photos, documents, rapports, modèles entraînés, modèle fantôme dans
                   media/systeme/) — sans le fichier vivant media/systeme/db.sqlite3 : la copie à chaud ci-dessus le remplace ;
  - VERSION      : date, version de Django, nombre de lignes par table principale.
Écrite d'abord sous un nom temporaire puis renommée : jamais d'archive à moitié écrite prise pour
une bonne sauvegarde. Les plus anciennes au-delà de --garder sont supprimées.

Restauration : arrêter l'application, extraire l'archive, remettre media/ puis db.sqlite3 dans media/systeme/
(ou `python manage.py loaddata donnees.json`), relancer. À planifier une fois par jour (cron/systemd).
"""
from engine.i18n import t
from engine.i18n import tf, tl
import io
import json
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

import django
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = tl("engine/management/commands/sauvegarder.py:g:34:12")

    def add_arguments(self, parser):
        parser.add_argument("--garder", type=int, default=getattr(settings, "GYPLATFORM_SAUVEGARDES_GARDER", 14),
                            help=t("engine/management/commands/sauvegarder.py:g:39:34"))

    def handle(self, *args, **options):
        dossier = Path(getattr(settings, "GYPLATFORM_SAUVEGARDES", Path(settings.BASE_DIR) / "sauvegardes"))
        dossier.mkdir(parents=True, exist_ok=True)
        horodatage = datetime.now().strftime("%Y%m%d_%H%M%S")
        finale = dossier / f"sauvegarde_{horodatage}.tar.gz"
        temporaire = dossier / f".{finale.name}.partiel"
        try:
            with tempfile.TemporaryDirectory() as tmp, tarfile.open(temporaire, "w:gz") as archive:
                base = self._sauver_base(Path(tmp))
                archive.add(base, arcname=base.name)
                media = Path(settings.MEDIA_ROOT)
                if media.is_dir():
                    archive.add(media, arcname="media", filter=self._sans_base_vivante)
                if connection.vendor == "sqlite":   # SQLite par entreprise : copie à chaud cohérente de chaque fichier d'entreprise
                    from engine import base_par_entreprise as bpe
                    for fichier in sorted(bpe.dossier().glob("*.sqlite3")):
                        copie = Path(tmp) / f"ent_{fichier.name}"
                        src, dst = sqlite3.connect(str(fichier)), sqlite3.connect(str(copie))
                        with dst:
                            src.backup(dst)
                        dst.close()
                        src.close()
                        archive.add(copie, arcname=f"media/systeme/entreprises/{fichier.name}")
                info = json.dumps(self._resume(), ensure_ascii=False, indent=2).encode()
                entree = tarfile.TarInfo("VERSION")
                entree.size = len(info)
                archive.addfile(entree, io.BytesIO(info))
            temporaire.rename(finale)
        except Exception as exc:
            temporaire.unlink(missing_ok=True)
            raise CommandError(tf("engine/management/commands/sauvegarder.py:g:60:32", v1=exc))
        anciennes = sorted(dossier.glob("sauvegarde_*.tar.gz"))[:-max(1, options["garder"])]
        for vieille in anciennes:
            vieille.unlink(missing_ok=True)
        taille = finale.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(tf("engine/management/commands/sauvegarder.py:g:65:46", v1=finale, v2=taille, v3=len(anciennes))))

    @staticmethod
    def _sans_base_vivante(info):
        """La base SQLite est dans media/systeme/ : sa copie brute (journal WAL compris) serait incohérente."""
        return None if info.name.startswith(("media/systeme/db.sqlite3", "media/systeme/entreprises/")) else info

    def _sauver_base(self, tmp: Path) -> Path:
        if connection.vendor == "sqlite":
            cible = tmp / "db.sqlite3"
            source = sqlite3.connect(str(settings.DATABASES["default"]["NAME"]))
            dest = sqlite3.connect(str(cible))
            with dest:
                source.backup(dest)  # copie cohérente à chaud (API officielle SQLite)
            dest.close()
            source.close()
            return cible
        cible = tmp / "donnees.json"
        with open(cible, "w", encoding="utf-8") as f:
            call_command("dumpdata", "--natural-foreign", "--natural-primary", "--indent", "0", stdout=f)
        return cible

    def _resume(self) -> dict:
        from engine.models import Entreprise, TableDynamique, Enregistrement, DocumentGenere
        return {
            "date": datetime.now().isoformat(timespec="seconds"),
            "django": django.get_version(),
            "entreprises": Entreprise.objects.count(),
            "tables": TableDynamique.objects.count(),
            "lignes": Enregistrement.objects.count(),
            "documents": DocumentGenere.objects.count(),
        }
