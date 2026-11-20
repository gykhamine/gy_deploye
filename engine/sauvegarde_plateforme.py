"""Sauvegarde / restauration COMPLÈTE de la plateforme dans un seul zip — réservée au super-administrateur.

Contenu du zip :
  VERSION.json     : format, date, moteur de base (sqlite | postgresql), version de Django, entreprises.
  base/db.sqlite3  : SQLite — copie à chaud par l'API de sauvegarde SQLite (cohérente même en mode WAL) ;
  base/donnees.json: PostgreSQL — dumpdata de toutes les entreprises (base unifiée) ;
  media/…          : tous les fichiers (sauf la base SQLite vivante et le dossier des sauvegardes).
Attention : media/systeme/ contient aussi la clé secrète et les clés du SIEM : le zip est confidentiel.

Restauration : le zip est contrôlé (format, moteur de base identique, aucun chemin hors du dossier média), une sauvegarde de sécurité
« avant_restauration » de l'état actuel est faite d'abord, puis la base est remplacée et les fichiers du zip sont remis en place.
Relancer l'application après une restauration.
"""
import json
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, connections, transaction

logger = logging.getLogger("engine.sauvegarde")

FORMAT = 1
BASE_SQLITE = "base/db.sqlite3"
BASE_JSON = "base/donnees.json"
EXCLUS_DUMP = ("contenttypes", "auth.permission", "sessions.session")


class SauvegardeErreur(Exception):
    """Zip invalide ou restauration impossible : message affichable tel quel."""


def dossier_sauvegardes() -> Path:
    d = Path(getattr(settings, "GYPLATFORM_SAUVEGARDES", Path(settings.BASE_DIR) / "sauvegardes"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _moteur() -> str:
    return "sqlite" if connection.vendor == "sqlite" else "postgresql"


def _media_a_inclure():
    media = Path(settings.MEDIA_ROOT)
    sauvegardes = dossier_sauvegardes().resolve()
    if not media.is_dir():
        return
    for f in media.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(media).as_posix()
        if rel.startswith("systeme/db.sqlite3") or rel.startswith("systeme/entreprises/"):   # bases vivantes (+ -wal, -shm) : copies cohérentes ajoutées à part
            continue
        try:
            if sauvegardes in f.resolve().parents:
                continue
        except OSError:
            continue
        yield f, rel


def creer(destination: Path = None, prefixe: str = "sauvegarde") -> Path:
    """Écrit le zip complet (nom temporaire puis renommage : jamais de zip à moitié écrit) et retourne son chemin."""
    destination = Path(destination) if destination else dossier_sauvegardes()
    destination.mkdir(parents=True, exist_ok=True)
    final = destination / f"{prefixe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    temporaire = destination / f".{final.name}.partiel"
    from .models import Entreprise
    manifeste = {"format": FORMAT, "date": datetime.now().isoformat(timespec="seconds"), "moteur": _moteur(),
                 "django": __import__("django").get_version(),
                 "entreprises": list(Entreprise.objects.order_by("id").values_list("slug", flat=True))}
    try:
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(temporaire, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            if _moteur() == "sqlite":
                copie = Path(tmp) / "db.sqlite3"
                source = sqlite3.connect(str(settings.DATABASES["default"]["NAME"]))
                dest = sqlite3.connect(str(copie))
                with dest:
                    source.backup(dest)
                dest.close()
                source.close()
                z.write(copie, BASE_SQLITE)
            else:
                dump = Path(tmp) / "donnees.json"
                with open(dump, "w", encoding="utf-8") as f:
                    call_command("dumpdata", "--natural-foreign", "--natural-primary", "--indent", "0",
                                 *[f"--exclude={x}" for x in EXCLUS_DUMP], stdout=f)
                z.write(dump, BASE_JSON)
            if _moteur() == "sqlite":   # SQLite par entreprise : un fichier par entreprise, copié à chaud de façon cohérente
                from . import base_par_entreprise as _bpe
                for fichier in sorted(_bpe.dossier().glob("*.sqlite3")):
                    copie_e = Path(tmp) / f"ent_{fichier.name}"
                    src_e, dst_e = sqlite3.connect(str(fichier)), sqlite3.connect(str(copie_e))
                    with dst_e:
                        src_e.backup(dst_e)
                    dst_e.close()
                    src_e.close()
                    z.write(copie_e, f"media/systeme/entreprises/{fichier.name}")
            for f, rel in _media_a_inclure():
                z.write(f, f"media/{rel}")
            z.writestr("VERSION.json", json.dumps(manifeste, ensure_ascii=False, indent=2))
        temporaire.rename(final)
    except Exception:
        temporaire.unlink(missing_ok=True)
        raise
    return final


def _lire_manifeste(z: zipfile.ZipFile) -> dict:
    try:
        manifeste = json.loads(z.read("VERSION.json"))
    except (KeyError, ValueError):
        raise SauvegardeErreur("Ce fichier n'est pas une sauvegarde de la plateforme (VERSION.json absent ou illisible).")
    if manifeste.get("format") != FORMAT:
        raise SauvegardeErreur("Format de sauvegarde non pris en charge.")
    if manifeste.get("moteur") != _moteur():
        raise SauvegardeErreur(f"Cette sauvegarde vient d'une base {manifeste.get('moteur')} ; la base actuelle est {_moteur()}.")
    return manifeste


def verifier(chemin_zip) -> dict:
    """Contrôle un zip SANS rien modifier ; retourne son manifeste."""
    try:
        with zipfile.ZipFile(chemin_zip) as z:
            manifeste = _lire_manifeste(z)
            noms = z.namelist()
            attendu = BASE_SQLITE if _moteur() == "sqlite" else BASE_JSON
            if attendu not in noms:
                raise SauvegardeErreur("La base est absente de la sauvegarde.")
            media = (Path(settings.MEDIA_ROOT)).resolve()
            for n in noms:
                if n.startswith("/") or ".." in Path(n).parts:
                    raise SauvegardeErreur("Chemin interdit dans la sauvegarde.")
                if n.startswith("media/") and not (media / n[len("media/"):]).resolve().is_relative_to(media):
                    raise SauvegardeErreur("Chemin interdit dans la sauvegarde.")
            if z.testzip() is not None:
                raise SauvegardeErreur("Sauvegarde corrompue.")
            return manifeste
    except zipfile.BadZipFile:
        raise SauvegardeErreur("Ce fichier n'est pas un zip valide.")


def restaurer(chemin_zip) -> dict:
    """Remplace la base et les fichiers par ceux du zip. Retourne {"securite": chemin de la sauvegarde d'avant, "manifeste": …}."""
    manifeste = verifier(chemin_zip)
    securite = creer(prefixe="avant_restauration")   # filet : l'état actuel reste récupérable
    media = Path(settings.MEDIA_ROOT)
    with zipfile.ZipFile(chemin_zip) as z, tempfile.TemporaryDirectory() as tmp:
        if _moteur() == "sqlite":
            z.extract(BASE_SQLITE, tmp)
            connections.close_all()
            source = sqlite3.connect(str(Path(tmp) / BASE_SQLITE))
            dest = sqlite3.connect(str(settings.DATABASES["default"]["NAME"]), timeout=60)
            try:
                with dest:
                    source.backup(dest)   # remplace le contenu de la base vivante, de façon atomique
            finally:
                dest.close()
                source.close()
        else:
            z.extract(BASE_JSON, tmp)
            with transaction.atomic():
                call_command("flush", interactive=False)
                call_command("loaddata", str(Path(tmp) / BASE_JSON))
        if _moteur() == "sqlite":   # fichiers d'entreprise : ceux de la sauvegarde remplacent ceux d'aujourd'hui (+ journaux WAL)
            from . import base_par_entreprise as _bpe
            connections.close_all()
            dans_zip = {Path(n).name for n in z.namelist() if n.startswith("media/systeme/entreprises/")}
            for vivant in list(_bpe.dossier().glob("*.sqlite3*")):
                if vivant.name.split(".sqlite3")[0] + ".sqlite3" not in dans_zip or vivant.name.endswith(("-wal", "-shm")):
                    vivant.unlink(missing_ok=True)
        for info in z.infolist():
            if not info.filename.startswith("media/") or info.is_dir():
                continue
            rel = info.filename[len("media/"):]
            if rel.startswith("systeme/db.sqlite3"):
                continue
            cible = media / rel
            cible.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(cible, "wb") as out:
                shutil.copyfileobj(src, out)
    try:
        cache.clear()
    except Exception:
        logger.debug("restaurer: cache non vidé", exc_info=True)
    return {"securite": str(securite), "manifeste": manifeste}
