"""
IA/infer — Sous-paquet inférence.

Fournit les fonctions de chargement et de prédiction de tous les modèles.

Chaque sous-module est importé SÉPARÉMENT : une dépendance absente ou cassée (jax, flax, optax, scikit-learn, Pillow...)
n'empêche plus les autres modèles de se charger. Les modules en échec sont listés dans ERREURS_IMPORT (nom du
sous-module -> exception) ; MODULE_DE donne, pour chaque nom public, le sous-module qui le fournit (sert à expliquer
précisément pourquoi un type de modèle est indisponible).
"""
import importlib
import logging
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

ERREURS_IMPORT = {}
MODULE_DE = {}


def _importer(sous_module, noms):
    for nom in noms:
        MODULE_DE[nom] = sous_module
    try:
        mod = importlib.import_module("." + sous_module, __name__)
    except Exception as e:  # ImportError (dépendance absente) mais aussi toute erreur à l'import (version incompatible...)
        ERREURS_IMPORT[sous_module] = e
        logger.warning(_gt("rg8.msg.064"), __name__, sous_module, type(e).__name__, e)
        return
    for nom in noms:
        globals()[nom] = getattr(mod, nom)


_importer("cnn", ["load_cnn2d", "predict_cnn2d", "load_cnn_nd", "predict_cnn_nd"])
_importer("rnn", ["load_rnn", "predict_rnn"])
_importer("transformer", ["load_transformer", "predict_transformer", "load_transformer3d", "predict_transformer3d"])
_importer("gan", ["load_gan_1d", "generate_gan_1d", "load_gan_nd", "generate_gan_nd", "load_gan_3d", "generate_gan_3d", "load_gan_rgb", "generate_gan_rgb"])
_importer("ldm", ["load_ldm_image", "generate_ldm_image", "load_ldm_audio", "generate_ldm_audio"])
_importer("slm", ["load_slm", "predict_slm", "predict_slm_next_word"])
_importer("vision", ["load_image_classifier", "predict_image", "load_speech_classifier", "predict_speech"])
_importer("mlp", ["load_mlp", "predict_mlp"])

__all__ = ["ERREURS_IMPORT", "MODULE_DE"] + [n for n in MODULE_DE if n in globals()]
