"""
IA/dataset.py — Chargement de datasets multi-formats.

Formats supportes (detection automatique par extension) :
  - CSV, TSV        : csv/tsv (standard Python)
  - JSON, JSONL     : json (standard Python)
  - NPY, NPZ        : numpy
  - TXT             : texte brut (mots/lignes)
  - Parquet         : pyarrow (optionnel)
  - HDF5 / H5       : h5py (optionnel)
  - XLSX / XLS      : openpyxl (optionnel)
  - Pickle / PKL    : pickle (standard Python)

Usage :
    from IA.dataset import load_dataset
    X, y = load_dataset("donnees.csv", target="label")
    X, y = load_dataset("donnees.json", target="classe")
    data = load_dataset("features.npy")  # retourne X seul si pas de target

Convention :
  - Les colonnes numeriques deviennent les features (X).
  - La colonne "target" specifiee devient le label (y).
  - Si target=None, retourne (data, None).
  - Toutes les donnees sont converties en float64 numpy arrays.
"""

import os
import csv
import json
import logging
import struct
import io
import pickle
from typing import Optional, Tuple, Union, List, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# §sécurité (audit 2026-09-26) — désérialisation non fiable via pickle.
#
# `_load_pickle` ci-dessous accepte, par conception, un fichier ".pkl" fourni
# par l'utilisateur comme "dataset" (docstring du module : load_dataset("donnees.pkl")).
# Un tel fichier peut provenir de n'importe où (dataset téléchargé, reçu d'un
# collègue...) — ce n'est PAS un format interne à GSCode comme le .gy des
# modèles sklearn (cf. IA/train/sklearn_models.py, qui documente la même
# limite). `pickle.load()` sur des octets non fiables exécute du code
# arbitraire au moment même de la désérialisation (via __reduce__), avant
# même que le type du contenu soit inspecté : ouvrir un ".pkl" malveillant
# suffit à compromettre le process.
#
# Correctif : un Unpickler restreint à la liste blanche minimale requise par
# ce loader (numpy ndarray/dtype/scalar, et les conteneurs Python de base) —
# toute autre classe référencée dans le flux pickle est refusée AVANT
# exécution, ce qui élimine la classe de faille tout en gardant la
# compatibilité avec les fichiers .pkl légitimes (array, dict, tuple, liste).
_PICKLE_CLASSES_AUTORISEES = {
    ("builtins", "dict"), ("builtins", "list"), ("builtins", "tuple"),
    ("builtins", "set"), ("builtins", "frozenset"), ("builtins", "range"),
    ("builtins", "complex"), ("builtins", "bytearray"),
    ("numpy", "ndarray"), ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy.core.multiarray", "scalar"),
    ("numpy._core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "scalar"),
    ("numpy.core.numeric", "_frombuffer"), ("numpy._core.numeric", "_frombuffer"),
}

_LOADERS = {}


try:
    import gzip
    _HAS_GZIP = True
except ImportError:
    _HAS_GZIP = False


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tiff", ".tif"}


_AUDIO_EXTS = {".wav", ".flac", ".ogg", ".mp3", ".aiff", ".aif"}




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
