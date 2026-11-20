# ─────────────────────────────────────────────────────────────
# IMAGES AUTOMATIQUES : « image disque » (comme un dd) de l'environnement de l'entreprise,
# activable par l'administrateur. Toutes les minutes (réglable), si quelque chose a changé,
# une image est écrite dans un dossier dédié ; seules les 10 dernières sont gardées.
# Une image automatique NE REMET RIEN À ZÉRO (≠ clôture) : c'est le même zip que la clôture
# (cycle.generer_zip_cloture_vers), donc restaurable avec la même fonction, sans ligne de
# commande ni migration. Dossier : GYPLATFORM_IMAGES_AUTO (défaut MEDIA_ROOT/systeme/images_auto/<id>),
# dossier système (les images contiennent clés API et wallets : jamais servies au web, voir securite.chemin_media_sur).
# Un seul planificateur par machine (verrou fichier), démarré avec l'application.
# ─────────────────────────────────────────────────────────────
import datetime
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from django.conf import settings
from .stockage import dossier_systeme
from django.db import connections, transaction

GARDER = 10
INTERVALLE_DEFAUT = 60
INTERVALLE_MIN = 10
_NOM = re.compile(r"^image_\d{8}_\d{6}\.zip$")
_fil = None
_verrou_fichier = None  # garde le verrou ouvert pendant toute la vie du processus


def _racine():
    return Path(getattr(settings, "GYPLATFORM_IMAGES_AUTO", dossier_systeme("images_auto")))


def dossier(entreprise):
    d = _racine() / str(entreprise.pk)
    d.mkdir(parents=True, exist_ok=True)
    return d


def lire_config(entreprise):
    try:
        cfg = json.loads((dossier(entreprise) / "config.json").read_text())
    except (OSError, ValueError):
        cfg = {}
    return {"actif": bool(cfg.get("actif")),
            "intervalle": max(INTERVALLE_MIN, int(cfg.get("intervalle") or INTERVALLE_DEFAUT)),
            "dernier": float(cfg.get("dernier") or 0), "empreinte": cfg.get("empreinte") or ""}


def _ecrire_config(entreprise, cfg):
    d = dossier(entreprise)
    tmp = d / ".config.json.tmp"
    tmp.write_text(json.dumps(cfg))
    os.replace(tmp, d / "config.json")


def regler(entreprise, actif, intervalle=None):
    cfg = lire_config(entreprise)
    cfg["actif"] = bool(actif)
    if intervalle:
        cfg["intervalle"] = max(INTERVALLE_MIN, int(intervalle))
    _ecrire_config(entreprise, cfg)
    if cfg["actif"]:
        demarrer()
    return cfg


def lister(entreprise):
    images = []
    for f in sorted(dossier(entreprise).glob("image_*.zip"), reverse=True):
        st = f.stat()
        images.append({"nom": f.name, "taille": st.st_size,
                       "cree_le": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
    return images


def creer_image(entreprise):
    """Écrit une image si l'environnement a changé depuis la précédente. Renvoie son nom ou None."""
    from .cycle import generer_zip_cloture_vers
    d = dossier(entreprise)
    cfg = lire_config(entreprise)
    final = d / f"image_{datetime.datetime.now():%Y%m%d_%H%M%S}.zip"
    tmp = d / f".{final.name}.partiel"   # jamais d'image à moitié écrite prise pour une bonne
    try:
        with open(tmp, "wb") as f, transaction.atomic():   # lecture cohérente de la base
            empreinte = generer_zip_cloture_vers(entreprise, f, si_different_de=cfg["empreinte"])
        if empreinte is None:
            tmp.unlink(missing_ok=True)
            return None
        os.replace(tmp, final)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    cfg = lire_config(entreprise)   # relu : l'administrateur a pu changer le réglage entre-temps
    cfg["empreinte"] = empreinte
    _ecrire_config(entreprise, cfg)
    for ancienne in sorted(d.glob("image_*.zip"))[:-GARDER]:
        ancienne.unlink(missing_ok=True)
    return final.name


def chemin_image(entreprise, nom):
    """Chemin d'une image existante ; nom validé (jamais de « ../ »). None si introuvable."""
    if not _NOM.match(nom or ""):
        return None
    p = dossier(entreprise) / nom
    return p if p.is_file() else None


def restaurer_image(entreprise, nom, utilisateur):
    from .cycle import restaurer_zip_cycle
    p = chemin_image(entreprise, nom)
    if p is None:
        raise FileNotFoundError(nom)
    restaurer_zip_cycle(entreprise, str(p), garder_utilisateur=utilisateur)


# ── Planificateur ────────────────────────────────────────────────────────
def _passage():
    from .models import Entreprise
    maintenant = time.time()
    for sous in _racine().glob("*/config.json"):
        if not sous.parent.name.isdigit():
            continue
        try:
            cfg = json.loads(sous.read_text())
        except (OSError, ValueError):
            continue
        if not cfg.get("actif"):
            continue
        entreprise = Entreprise.objects.filter(pk=int(sous.parent.name)).first()
        if entreprise is None:
            continue
        cfg = lire_config(entreprise)
        if maintenant - cfg["dernier"] < cfg["intervalle"]:
            continue
        cfg["dernier"] = maintenant   # avant d'écrire : un échec ne se rejoue pas en boucle serrée
        _ecrire_config(entreprise, cfg)
        creer_image(entreprise)


def _boucle():
    global _verrou_fichier
    try:
        import fcntl
        _racine().mkdir(parents=True, exist_ok=True)
        _verrou_fichier = open(_racine() / ".planificateur.lock", "w")
        fcntl.flock(_verrou_fichier, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        return   # un autre processus est déjà le planificateur (ou OS sans fcntl)
    while True:
        time.sleep(5)
        try:
            _passage()
        except Exception:
            pass   # une image ratée ne doit jamais arrêter le planificateur
        finally:
            connections.close_all()


def demarrer():
    """Démarre le planificateur (idempotent). Sans effet si GYPLATFORM_IMAGES_AUTO_THREAD=0."""
    global _fil
    if os.environ.get("GYPLATFORM_IMAGES_AUTO_THREAD") == "0":
        return
    if _fil is not None and _fil.is_alive():
        return
    _fil = threading.Thread(target=_boucle, name="images-auto", daemon=True)
    _fil.start()


def demarrer_au_boot():
    """Appelé par apps.ready : pas pendant migrate/test/commandes manage.py (hors runserver)."""
    argv = sys.argv
    if any(a in argv for a in ("migrate", "makemigrations", "test", "collectstatic", "shell", "check")):
        return
    if argv and argv[0].endswith("manage.py"):
        if len(argv) < 2 or argv[1] != "runserver" or os.environ.get("RUN_MAIN") != "true":
            return
    if any(_racine().glob("*/config.json")):
        demarrer()
