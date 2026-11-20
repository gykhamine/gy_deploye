"""
IA — Module d'Intelligence Artificielle.

Architecture modulaire pour l'entrainement et l'inference de modeles deep
learning. L'entrainement est en cours de migration, module par module, de
l'ancien moteur numpy manuel (backend.py, retropropagation ecrite a la main)
vers Flax/JAX + optax (autodiff, optimiseurs standards). La persistance des
modeles (format binaire .gy, voir ia_format.py) est INCHANGEE dans les deux
cas : Model.save()/Model.load() et les fonctions predict_fn de chaque
architecture continuent de lire/ecrire les memes cles de poids qu'avant.

Statut de la migration Flax (mis a jour au fil des conversions) :
  - MLP : converti (train/mlp.py)
  - CNN 2D / N-D : converti (train/cnn.py, infer/cnn.py ne depend plus de backend.py)
  - RNN, Transformer, GAN, LDM, SLM, Vision : pas encore convertis, utilisent
    toujours backend.py (moteur numpy manuel).
  - backend.py sera supprime une fois que plus aucun module n'en dependra.

Organisation :
  - backend.py  : Moteur de calcul Python/numpy — encore utilise par les
                  architectures pas encore migrees vers Flax (voir statut ci-dessus).
  - train/      : Entrainement de tous les modeles (CNN, RNN, Transformer, GAN, LDM, SLM, Vision).
  - infer/      : Chargement et inference de tous les modeles entraines.
  - dataset.py  : Chargement multi-formats (CSV, JSON, Parquet, HDF5, TXT, etc.)
  - trainer.py  : Classe Trainer unifiee (V3) + Model + callbacks
  - model.py    : Classe Model (wrapper d'inference + persistance .gy)
  - callbacks.py: Callbacks Keras-like (EarlyStopping, ModelCheckpoint, ...)
  - ia_format.py: Format binaire natif .gy pour la persistance des modeles

API unifiee (V3) :
    from IA import Trainer
    trainer = Trainer()
    model = trainer.train(type='rnn', epochs=500)
    y = model.predict(X)
    model.save('mon_modele.gy')
    model2 = Trainer.load('mon_modele.gy')

API bas niveau (rétro-compatible V2) :
    from IA import train_rnn
    result = train_rnn(epochs=500)
"""

import logging

# --- Précision float64 pour JAX ---
# Le code des architectures Flax/JAX (train/*.py) déclare explicitement des
# poids/tenseurs en jnp.float64 / np.float64 (précision demandée pour la
# reproductibilité numérique avec l'ancien moteur numpy manuel). Par défaut,
# JAX force TOUT en float32 (silencieusement, sans avertissement ni erreur),
# quelle que soit la précision explicitement demandée dans le code appelant.
# Cet enable_x64 doit être activé avant tout usage de jax/jax.numpy — donc ici,
# avant même l'import de `backend` ci-dessous, et a fortiori avant l'import de
# n'importe quel module de train/*.py ou infer/*.py qui importe jax.
try:
    import jax as _jax
    _jax.config.update("jax_enable_x64", True)
except ImportError:
    # jax n'est installé que pour les architectures déjà migrées vers Flax ;
    # si absent (environnement n'utilisant que les architectures encore sur
    # backend.py, ou sandbox sans jax), on ne bloque pas l'import du package.
    pass

_logging_configured = False


def _setup_logging():
    global _logging_configured
    if _logging_configured:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _logging_configured = True


_setup_logging()

# --- Config ---
from .config import get_config, ensure_directories, MODELS_DIR, MODEL_EXTENSION, PARAM_BITS

# --- Moteur de calcul ---
from . import backend
from .backend import get_core

# --- Quantification des paramètres (4/8/16/32/64 bits) ---
from . import quantization
from .quantization import quantize, dequantize, SUPPORTED_BITS

# --- Sous-modules ---
from .dataset import load_dataset, supported_formats, dataset_info

from . import train, infer

# --- V3 : Trainer, Model, callbacks, format .gy ---
from .trainer import Trainer
from .model import Model
from . import callbacks
from .callbacks import (
    Callback, CallbackList,
    EarlyStopping, ModelCheckpoint, ProgressPrinter, CSVLogger,
)
from . import ia_format
from .exceptions import (
    IAError, ModelNotFoundError, ModelFormatError,
    InferenceError, TrainingError, ConfigurationError, DatasetError,
)

# Les raccourcis ne sont créés que pour les sous-modules qui se sont chargés (voir train.ERREURS_IMPORT / infer.ERREURS_IMPORT) :
# un module manquant (ex. jax absent) ne doit pas empêcher d'importer IA ni d'entraîner les autres modèles.
for _alias, _paquet, _nom in [('train_cnn2d', 'train', 'train_cnn2d'), ('train_cnn_nd', 'train', 'train_cnn_nd'), ('train_rnn', 'train', 'train_rnn'), ('train_transformer', 'train', 'train_transformer'), ('train_transformer3d', 'train', 'train_transformer3d'), ('train_gan_1d', 'train', 'train_gan_1d'), ('train_gan_nd', 'train', 'train_gan_nd'), ('train_gan_3d', 'train', 'train_gan_3d'), ('train_gan_rgb', 'train', 'train_gan_rgb'), ('train_ldm_image', 'train', 'train_ldm_image'), ('train_ldm_audio', 'train', 'train_ldm_audio'), ('train_slm_next_word', 'train', 'train_slm_next_word'), ('train_slm_classifier', 'train', 'train_slm_classifier'), ('SLM_TACHES', 'train', 'SLM_TACHES'), ('train_slm_emotion', 'train', 'train_slm_emotion'), ('train_slm_mood', 'train', 'train_slm_mood'), ('train_slm_statement', 'train', 'train_slm_statement'), ('train_slm_sentiment', 'train', 'train_slm_sentiment'), ('train_image_classifier', 'train', 'train_image_classifier'), ('train_speech_classifier', 'train', 'train_speech_classifier'), ('train_mlp', 'train', 'train_mlp'), ('load_cnn2d', 'infer', 'load_cnn2d'), ('predict_cnn2d', 'infer', 'predict_cnn2d'), ('load_cnn_nd', 'infer', 'load_cnn_nd'), ('predict_cnn_nd', 'infer', 'predict_cnn_nd'), ('load_rnn', 'infer', 'load_rnn'), ('predict_rnn', 'infer', 'predict_rnn'), ('load_transformer', 'infer', 'load_transformer'), ('predict_transformer', 'infer', 'predict_transformer'), ('load_transformer3d', 'infer', 'load_transformer3d'), ('predict_transformer3d', 'infer', 'predict_transformer3d'), ('load_gan_1d', 'infer', 'load_gan_1d'), ('generate_gan_1d', 'infer', 'generate_gan_1d'), ('load_gan_nd', 'infer', 'load_gan_nd'), ('generate_gan_nd', 'infer', 'generate_gan_nd'), ('load_gan_3d', 'infer', 'load_gan_3d'), ('generate_gan_3d', 'infer', 'generate_gan_3d'), ('load_gan_rgb', 'infer', 'load_gan_rgb'), ('generate_gan_rgb', 'infer', 'generate_gan_rgb'), ('load_ldm_image', 'infer', 'load_ldm_image'), ('generate_ldm_image', 'infer', 'generate_ldm_image'), ('load_ldm_audio', 'infer', 'load_ldm_audio'), ('generate_ldm_audio', 'infer', 'generate_ldm_audio'), ('load_slm', 'infer', 'load_slm'), ('predict_slm', 'infer', 'predict_slm'), ('predict_slm_next_word', 'infer', 'predict_slm_next_word'), ('load_image_classifier', 'infer', 'load_image_classifier'), ('predict_image', 'infer', 'predict_image'), ('load_speech_classifier', 'infer', 'load_speech_classifier'), ('predict_speech', 'infer', 'predict_speech'), ('load_mlp', 'infer', 'load_mlp'), ('predict_mlp', 'infer', 'predict_mlp')]:
    _src = train if _paquet == "train" else infer
    if hasattr(_src, _nom):
        globals()[_alias] = getattr(_src, _nom)
del _alias, _paquet, _nom, _src

# --- Raccourcis entraînement (rétro-compatibilité V2) ---

# --- Raccourcis inférence ---

__all__ = [
    # Config
    "get_config", "ensure_directories", "MODELS_DIR", "MODEL_EXTENSION", "PARAM_BITS",
    # Moteur de calcul
    "get_core",
    # Quantification des paramètres
    "quantization", "quantize", "dequantize", "SUPPORTED_BITS",
    # V3 : API unifiée
    "Trainer", "Model",
    "callbacks", "ia_format",
    "Callback", "CallbackList",
    "EarlyStopping", "ModelCheckpoint", "ProgressPrinter", "CSVLogger",
    # Entraînement CNN
    "train_cnn2d", "train_cnn_nd",
    # Entraînement RNN
    "train_rnn",
    # Entraînement Transformer
    "train_transformer", "train_transformer3d",
    # Entraînement GAN
    "train_gan_1d", "train_gan_nd", "train_gan_3d", "train_gan_rgb",
    # Entraînement LDM
    "train_ldm_image", "train_ldm_audio",
    # Entraînement SLM
    "train_slm_next_word", "train_slm_classifier", "SLM_TACHES",
    "train_slm_emotion", "train_slm_mood",
    "train_slm_statement", "train_slm_sentiment",
    # Entraînement Vision
    "train_image_classifier", "train_speech_classifier",
    # Entraînement MLP
    "train_mlp",
    # Inférence CNN
    "load_cnn2d", "predict_cnn2d", "load_cnn_nd", "predict_cnn_nd",
    # Inférence RNN
    "load_rnn", "predict_rnn",
    # Inférence Transformer
    "load_transformer", "predict_transformer",
    "load_transformer3d", "predict_transformer3d",
    # Inférence GAN
    "load_gan_1d", "generate_gan_1d",
    "load_gan_nd", "generate_gan_nd",
    "load_gan_3d", "generate_gan_3d",
    "load_gan_rgb", "generate_gan_rgb",
    # Inférence LDM
    "load_ldm_image", "generate_ldm_image",
    "load_ldm_audio", "generate_ldm_audio",
    # Inférence SLM
    "load_slm", "predict_slm", "predict_slm_next_word",
    # Inférence Vision
    "load_image_classifier", "predict_image",
    "load_speech_classifier", "predict_speech",
    # Inférence MLP
    "load_mlp", "predict_mlp",
    # Sous-modules
    "load_dataset", "supported_formats", "dataset_info",
    "train", "infer",
]

__all__ = [_n for _n in __all__ if _n in globals()]
