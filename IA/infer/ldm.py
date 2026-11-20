"""
IA/infer/ldm.py — Inférence des modèles de diffusion (LDM).

Fonctions :
  - load_ldm_image / generate_ldm_image : Diffusion conditionnelle pour images 2D
  - load_ldm_audio / generate_ldm_audio : Diffusion conditionnelle pour signaux audio
"""
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf

import math
import logging
from typing import Any, Dict, Tuple

import numpy as np

from .._utils import merge_header, validate_model_path
from ..ia_format import load_model

logger = logging.getLogger(__name__)


# ==================================================================
# Helpers (numpy pur — plus de dépendance à IA/backend.py)
# ==================================================================

def _predict_noise_from_params(model: Dict[str, Any], x_noisy: np.ndarray,
                                class_id: int) -> np.ndarray:
    """Reconstruit la passe avant du réseau de prédiction de bruit."""
    flat = x_noisy.flatten().reshape(1, -1)
    num_classes = model['num_classes']

    class_embedding = model['class_embedding']
    c_embed = class_embedding[class_id % num_classes].reshape(1, -1)
    x_concat = np.concatenate([flat, c_embed], axis=1)

    i = 0
    while f'W_{i}' in model:
        i += 1
    n_layers = i

    h = x_concat
    for layer_i in range(n_layers):
        W = model[f'W_{layer_i}']
        b = model[f'b_{layer_i}'].reshape(1, -1)
        z = np.matmul(h, W) + b
        if layer_i < n_layers - 1:
            h = np.maximum(z, 0.0)
        else:
            h = z

    return h.reshape(x_noisy.shape)


def _load_ldm(path: str) -> Dict[str, Any]:
    """Charge un modèle LDM depuis un fichier .gy (format V2/V3)."""
    path = validate_model_path(path)
    header, tensors = load_model(path)
    model = merge_header(header, tensors)
    logger.info(_i18n_t("IA/infer/ldm.py:g:59:17"), path)
    return model


# ==================================================================
# LDM Image
# ==================================================================

def load_ldm_image(path: str) -> Dict[str, Any]:
    """Charge un modèle de diffusion d'image depuis un fichier .gy."""
    model = _load_ldm(path)
    logger.info(_i18n_t("IA/infer/ldm.py:g:70:17"), path)
    return model


def generate_ldm_image(model: Dict[str, Any], class_id: int,
                       shape: Tuple[int, int] = (8, 8),
                       num_steps: int = 50) -> Dict[str, Any]:
    """
    Génère une image par débruitage DDPM itératif.

    Args:
        model: Paramètres du modèle (issu de load_ldm_image).
        class_id: Identifiant de classe pour la génération conditionnelle.
        shape: Forme de l'image à générer (H, W).
        num_steps: Nombre d'étapes de débruitage.

    Returns:
        dict: {'generated', 'class_id', 'num_steps'}
    """
    if num_steps < 1:
        raise ValueError(_i18n_t("IA/infer/ldm.py:g:89:26"))

    betas = model['betas']
    alpha_cumprod = model['alpha_cumprod']
    if num_steps > len(alpha_cumprod):
        raise ValueError(tf("IA/infer/ldm.py:g:94:26", v1=num_steps, v2=len(alpha_cumprod)))

    x = np.random.randn(*shape)

    for t in reversed(range(num_steps)):
        predicted_noise = _predict_noise_from_params(model, x, class_id)
        alpha_t = alpha_cumprod[t]
        alpha_t_prev = alpha_cumprod[t - 1] if t > 0 else 1.0
        x_pred = (x - math.sqrt(1 - alpha_t) * predicted_noise) / math.sqrt(alpha_t)
        x = x_pred * math.sqrt(alpha_t_prev) + math.sqrt(1 - alpha_t_prev) * np.random.randn(*shape)

    logger.info(_i18n_t("IA/infer/ldm.py:g:106:17"), class_id, shape, num_steps)
    return {'generated': x, 'class_id': int(class_id), 'num_steps': int(num_steps)}


# ==================================================================
# LDM Audio
# ==================================================================

def load_ldm_audio(path: str) -> Dict[str, Any]:
    """Charge un modèle de diffusion audio depuis un fichier .gy."""
    model = _load_ldm(path)
    logger.info(_i18n_t("IA/infer/ldm.py:g:117:17"), path)
    return model


def generate_ldm_audio(model: Dict[str, Any], class_id: int,
                       signal_length: int = 64,
                       num_steps: int = 50) -> Dict[str, Any]:
    """
    Génère un signal audio par débruitage DDPM itératif.

    Args:
        model: Paramètres du modèle (issu de load_ldm_audio).
        class_id: Identifiant de classe pour la génération conditionnelle.
        signal_length: Longueur du signal à générer.
        num_steps: Nombre d'étapes de débruitage.

    Returns:
        dict: {'generated', 'class_id', 'num_steps'}
    """
    if num_steps < 1:
        raise ValueError(_i18n_t("IA/infer/ldm.py:g:136:26"))

    betas = model['betas']
    alpha_cumprod = model['alpha_cumprod']
    if num_steps > len(alpha_cumprod):
        raise ValueError(tf("IA/infer/ldm.py:g:141:26", v1=num_steps, v2=len(alpha_cumprod)))

    shape = (signal_length,)
    x = np.random.randn(*shape)

    for t in reversed(range(num_steps)):
        predicted_noise = _predict_noise_from_params(model, x, class_id)
        alpha_t = alpha_cumprod[t]
        alpha_t_prev = alpha_cumprod[t - 1] if t > 0 else 1.0
        x_pred = (x - math.sqrt(1 - alpha_t) * predicted_noise) / math.sqrt(alpha_t)
        x = x_pred * math.sqrt(alpha_t_prev) + math.sqrt(1 - alpha_t_prev) * np.random.randn(*shape)

    logger.info(_i18n_t("IA/infer/ldm.py:g:154:17"), class_id, signal_length, num_steps)
    return {'generated': x, 'class_id': int(class_id), 'num_steps': int(num_steps)}