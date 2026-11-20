"""
IA/trainer.py — Classe Trainer : point d'entrée unifié pour entraîner tous les modèles.

Usage :
    from IA import Trainer

    trainer = Trainer(verbose=True)
    model = trainer.train(type='rnn', epochs=500, lr=0.01)
    y_pred = model.predict(X_new)
    model.save('mon_rnn.gy')

    # Plus tard :
    model2 = Trainer.load('mon_rnn.gy')
    y_pred2 = model2.predict(X_new)

Architecture :
  Trainer.train() dispatche vers la fonction train_xxx correspondante,
  en injectant les callbacks à travers un wrapper qui convertit le
  format "history list" interne au format "callback events".

  Chaque train_xxx existant reste inchangé et peut être appelé directement
  pour rétro-compatibilité. Le Trainer ajoute par-dessus :
    - dispatch unifié par type
    - callbacks Keras-like (EarlyStopping, ModelCheckpoint, ...)
    - construction automatique d'un objet Model avec predict_fn enregistrée
    - sauvegarde .gy (binaire safe, seul format supporté)
"""
import inspect
import math
import os
import logging
from typing import Any, Callable, Dict, List, Optional, Union

import numpy as np

from .. import ia_format
from ..callbacks import Callback, CallbackList, default_callbacks
from ..model import Model, register_predict_fn, _PREDICT_REGISTRY
from ..config import ensure_directories, MODELS_DIR, MODEL_EXTENSION
from ..exceptions import (
    ConfigurationError, InferenceError, ModelFormatError, TrainingError,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Registre des types de modèles supportés
# ============================================================================
# Chaque entrée contient :
#   'train_fn'   : fonction train_xxx du paquet IA.train
#   'predict_fn' : fonction d'inférence (predict_fn(weights, config, X) -> y)
#   'default_kwargs' : kwargs additionnels à passer à train_fn
#   'extract_weights' : fonction (train_result) -> dict {nom: ndarray}
#   'extract_config'  : fonction (train_result) -> dict de config
#   'default_save_name' : nom de fichier par défaut (sans extension)

_REGISTRY: Dict[str, Dict[str, Any]] = {}
# Types dont le module d'entraînement n'a pas pu être importé : {code: raison lisible} (rempli par _register_all).
_INDISPONIBLES: Dict[str, str] = {}




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
