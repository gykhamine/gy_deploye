"""
MOTEUR UNIQUE : collecter -> comprendre -> analyser -> visualiser -> rapporter -> decider
Aucun modele metier n'est ecrit en dur. Tout passe par des metadonnees (EAV).
"""
from engine.i18n import tl
import hashlib
import json
import secrets
import threading
import time
import uuid
from functools import cached_property
from django.conf import settings
from django.db import models
from django.utils import timezone

# Script d'exemple par défaut injecté dans chaque prompt IA comme référence de
# style et de conventions. Source unique, utilisée à la fois par le modèle
# (default du champ `script_exemple`) et par le moteur d'inférence (fallback
# final si le champ est vide en base). Définie dans `inference.py` parce que
# c'est là que vit toute la logique d'inférence — `models.py` ne fait que la
# référencer.
from ..inference import (  # noqa: E402  (import bas volontaire : pas de cycle, models → inference)
    EXEMPLE_SCRIPT_PYTHON, EXEMPLE_SCRIPT_DOCUMENT, EXEMPLE_FORMULE_COLONNE, EXEMPLE_FORMULE_TABLE,
    EXEMPLE_GRAPHIQUE_BI_COLONNE, EXEMPLE_GRAPHIQUE_BI_MULTIPLES, EXEMPLE_COUCHE_ONTOLOGIE,
    EXEMPLE_MODELE_IA, DEFAULT_ROLES, DEFAULT_REGLE_MODELE_IA, DEFAULT_CONTRAINTES_MODELE_IA,
    DEFAULT_REGLE_TACHE, DEFAULT_CONTRAINTES_TACHE, EXEMPLE_TACHE,
)


# ─────────────────────────────────────────────────────────────
# ISOLATION MULTI-ENTREPRISE (CORE minimal, fusionne dans le module unique)
# ─────────────────────────────────────────────────────────────
FORMULE_CHAMP_OPERATEUR_CHOICES = [
    ("somme", tl("engine/models/_header.py:g:34:15")),
    ("difference", tl("engine/models/_header.py:g:35:20")),
    ("produit", "Produit (×)"),
    ("quotient", "Quotient (premier ÷ suivants)"),
    ("moyenne", tl("engine/models/_header.py:g:38:17")),
    ("pourcentage", "Pourcentage (premier / second × 100)"),
    ("min", tl("engine/models/_header.py:g:40:13")),
    ("max", tl("engine/models/_header.py:g:41:13")),
    ("concatenation", tl("engine/models/_header.py:g:42:23")),
    ("personnalisee", tl("engine/models/_header.py:g:43:23")),
]


from django.db.models.signals import pre_save
from django.dispatch import receiver
_bloc_lock = threading.Lock()   # conservé pour compatibilité ; plus utilisé pour sérialiser les blocs (voir _verrou_blocs)
_verrous_blocs: dict = {}


def _verrou_blocs(entreprise_id):
    """Un verrou PAR entreprise : l'écriture d'un bloc chez l'une n'attend plus celle d'une autre entreprise."""
    return _verrous_blocs.setdefault(entreprise_id, threading.Lock())




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
