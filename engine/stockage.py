"""Stockage — UN seul point d'entrée : MEDIA_ROOT.

Tout ce que la plateforme produit (fichiers, modèles entraînés, archives, base SQLite) vit sous MEDIA_ROOT : un seul
dossier — demain monté en NFS — porte l'état complet d'un système live (sans lui, un modèle entraîné perdrait ses
connaissances à chaque redémarrage). Les fichiers INTERNES sont rangés dans MEDIA_ROOT/systeme/, que securite.servir_media
ne sert jamais par HTTP (base, réseaux du modèle fantôme, images contenant des clés API : rien ne doit être téléchargeable).
Module sans dépendance Django au chargement : utilisable depuis settings.py."""
import shutil
from pathlib import Path

SYSTEME = "systeme"
# Ce qui vivait à côté du code (BASE_DIR) avant ce regroupement.
_ANCIENS_DOSSIERS = ("modeles_fantomes", "archives_geo", "images_auto", "flux_actifs", "geo_tuiles")
_ANCIENS_FICHIERS = ("double_facteur.json",)


def racine_systeme(media_root, base_dir) -> Path:
    return Path(media_root or Path(base_dir) / "media") / SYSTEME


def dossier_systeme(*sous) -> Path:
    """MEDIA_ROOT/systeme/<sous…> (non créé : chaque écrivain fait son mkdir)."""
    from django.conf import settings
    return racine_systeme(getattr(settings, "MEDIA_ROOT", ""), settings.BASE_DIR).joinpath(*sous)


def rapatrier(base_dir, media_root) -> Path:
    """Crée MEDIA_ROOT/systeme/ et y déménage, UNE fois, une installation existante. Ne remplace jamais ce qui existe déjà
    à destination. Les journaux -wal/-shm de SQLite suivent la base (seuls, des écritures non reportées seraient perdues)."""
    dest = racine_systeme(media_root, base_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        return dest
    base = Path(base_dir)
    for nom in ("db.sqlite3", *_ANCIENS_DOSSIERS, *_ANCIENS_FICHIERS):
        src, cible = base / nom, dest / nom
        if not src.exists() or cible.exists():
            continue
        try:
            shutil.move(str(src), str(cible))   # rename atomique sur le même disque, copie sinon
        except OSError:
            continue
        if nom == "db.sqlite3":
            for suffixe in ("-wal", "-shm"):
                if (base / (nom + suffixe)).exists():
                    try:
                        shutil.move(str(base / (nom + suffixe)), str(dest / (nom + suffixe)))
                    except OSError:
                        pass
    return dest
