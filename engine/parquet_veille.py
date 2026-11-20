"""Vérification périodique, en arrière-plan, de la cohérence entre l'EAV et les fichiers Parquet des tables dynamiques.

L'EAV est la source de vérité absolue : c'est elle qui retient les valeurs. Les fichiers Parquet n'en sont que le miroir analytique
(voir parquet_store.py). Chaque écriture met déjà le fichier à jour (saisie, modification, suppression, import) et chaque lecture
analytique contrôle sa fraîcheur ; cette veille rattrape en plus ce qui échapperait aux deux (écriture directe en base, fichier abîmé
ou effacé) : toutes les heures elle compare le CONTENU de chaque fichier à l'EAV et reconstruit depuis l'EAV ceux qui diffèrent.

Réglages (variables d'environnement) :
  GYPLATFORM_PARQUET_VERIF_SECONDES   intervalle entre deux passages (défaut 3600, minimum 60 ; 0 = veille désactivée)
  GYPLATFORM_PARQUET_VERIF_THREAD=0   n'active pas le fil d'arrière-plan (utiliser alors `manage.py verifier_parquet --boucle`)

Un seul fil par machine (verrou fichier), démarré avec l'application — même principe que image_auto.py.
"""
import logging
import os
import sys
import threading
import time
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

INTERVALLE_DEFAUT = 3600
INTERVALLE_MIN = 60
DELAI_PREMIER_PASSAGE = 120   # laisse le serveur finir de démarrer avant le premier passage
_fil = None
_verrou_fichier = None   # garde le verrou ouvert pendant toute la vie du processus


def intervalle() -> int:
    """Secondes entre deux passages ; 0 = veille désactivée."""
    try:
        valeur = int(os.environ.get("GYPLATFORM_PARQUET_VERIF_SECONDES", INTERVALLE_DEFAUT))
    except ValueError:
        return INTERVALLE_DEFAUT
    return 0 if valeur <= 0 else max(valeur, INTERVALLE_MIN)


def passage() -> list:
    """Un passage de vérification sur toutes les tables ; journalise les écarts réparés. Retourne les rapports."""
    from . import parquet_store
    rapports = parquet_store.verifier_toutes(reparer=True)
    ecarts = [r for r in rapports if r["etat"] != "ok" and r["etat"] != "ignoree"]
    if ecarts:
        logger.warning(_gt("rg6.msg.098"), len(ecarts), len(rapports),
                       [(r["table"], r["etat"]) for r in ecarts])
    mal_saisies = [r for r in rapports if r.get("non_convertibles") or r.get("colonnes_mal_typees")]
    if mal_saisies:
        logger.warning(_gt("rg6.msg.099"), len(mal_saisies),
                       [(r["table"], r["non_convertibles"], r["colonnes_mal_typees"]) for r in mal_saisies])
    return rapports


def _boucle(secondes):
    global _verrou_fichier
    from django.conf import settings
    from django.db import connections
    try:
        import fcntl
        dossier = os.path.join(settings.MEDIA_ROOT, "parquet_tables")
        os.makedirs(dossier, exist_ok=True)
        _verrou_fichier = open(os.path.join(dossier, ".veille.lock"), "w")
        fcntl.flock(_verrou_fichier, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError):
        return   # un autre processus fait déjà la veille (ou OS sans fcntl)
    time.sleep(DELAI_PREMIER_PASSAGE)
    while True:
        try:
            passage()
        except Exception:
            logger.exception(_gt("rg6.msg.100"))   # un passage raté ne doit jamais arrêter la veille
        finally:
            connections.close_all()
        time.sleep(secondes)


def demarrer():
    """Démarre la veille (idempotent). Sans effet si désactivée (intervalle 0 ou GYPLATFORM_PARQUET_VERIF_THREAD=0)."""
    global _fil
    secondes = intervalle()
    if not secondes or os.environ.get("GYPLATFORM_PARQUET_VERIF_THREAD") == "0":
        return
    if _fil is not None and _fil.is_alive():
        return
    _fil = threading.Thread(target=_boucle, args=(secondes,), name="parquet-veille", daemon=True)
    _fil.start()


def demarrer_au_boot():
    """Appelé par apps.ready : pas pendant migrate/test/commandes manage.py (hors runserver)."""
    argv = sys.argv
    if any(a in argv for a in ("migrate", "makemigrations", "test", "collectstatic", "shell", "check", "verifier_parquet")):
        return
    if argv and argv[0].endswith("manage.py"):
        if len(argv) < 2 or argv[1] != "runserver" or os.environ.get("RUN_MAIN") != "true":
            return
    demarrer()
