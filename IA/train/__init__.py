"""
IA/train — Sous-paquet entraînement.

Fournit les fonctions d'entraînement de tous les modèles : MLP, CNN, RNN, Transformer, GAN, LDM, SLM, Vision
(Flax/JAX/optax) 9 modèles scikit-learn supervisés et les modèles scikit-learn non supervisés (groupes).

Chaque sous-module est importé SÉPARÉMENT : une dépendance absente ou cassée (jax, flax, optax, scikit-learn, Pillow...)
n'empêche plus les autres modèles de se charger. Les modules en échec sont listés dans ERREURS_IMPORT (nom du
sous-module -> exception) ; MODULE_DE donne, pour chaque nom public, le sous-module qui le fournit (sert à expliquer
précisément pourquoi un type de modèle est indisponible).
"""
import importlib
import logging

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
        logger.warning("%s.%s indisponible : %s: %s", __name__, sous_module, type(e).__name__, e)
        return
    for nom in noms:
        globals()[nom] = getattr(mod, nom)


_importer("mlp", ["train_mlp"])
_importer("cnn", ["train_cnn2d", "train_cnn_nd"])
_importer("rnn", ["train_rnn"])
_importer("transformer", ["train_transformer", "train_transformer3d", "MiniTransformer3D"])
_importer("gan", ["train_gan_1d", "train_gan_nd", "train_gan_3d", "train_gan_rgb"])
_importer("ldm", ["train_ldm_image", "train_ldm_audio"])
_importer("slm", ["train_slm_next_word", "train_slm_classifier", "SLM_TACHES", "train_slm_emotion", "train_slm_mood", "train_slm_statement", "train_slm_sentiment", "SLMClassifier", "TextPreprocessor"])
_importer("vision", ["train_image_classifier", "train_speech_classifier"])
_importer("sklearn_models", ["train_sklearn_rf", "train_sklearn_gbm", "train_sklearn_svm", "train_sklearn_knn", "train_sklearn_lr", "train_sklearn_nb", "train_sklearn_dt", "train_sklearn_ridge", "train_sklearn_lasso", "_predict_sklearn_generic", "_extract_sklearn"])
_importer("sklearn_nonsup", ["train_sklearn_kmeans", "train_sklearn_dbscan", "train_sklearn_hierarchique", "train_sklearn_gmm",
                              "train_sklearn_acp", "train_sklearn_ica", "train_sklearn_isoforest", "train_sklearn_ocsvm", "train_sklearn_lof"])

__all__ = ["ERREURS_IMPORT", "MODULE_DE"] + [n for n in MODULE_DE if n in globals()]
