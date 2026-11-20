"""
IA/infer/transformer.py — Inférence des Transformers (numpy pur, plus de
dépendance à IA/backend.py — comme cnn.py/gan.py/ldm.py/rnn.py).

Fonctions :
  - load_transformer / predict_transformer       : Transformer single-head
  - load_transformer3d / predict_transformer3d   : Transformer3D multi-head
"""
from engine.i18n import t

import math
import logging
from typing import Any, Dict, List, Optional, Union

import numpy as np

from .._utils import merge_header, validate_model_path, validate_ndarray
from ..ia_format import load_model

from ..train.transformer import MiniTransformer3D

logger = logging.getLogger(__name__)


# ==================================================================
# Helpers (numpy pur)
# ==================================================================

def softmax(x):
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / np.sum(e, axis=-1, keepdims=True)

def layer_norm(x, eps=1e-8):
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(var + eps)

def relu(x):
    return np.maximum(0.0, x)



# ==================================================================
# MiniTransformer (single-head)
# ==================================================================

def load_transformer(path: str) -> Dict[str, Any]:
    """
    Charge un modèle Transformer single-head depuis un fichier .gy.

    Gère les formats V2 et V3.
    """
    path = validate_model_path(path)
    header, tensors = load_model(path)
    model = merge_header(header, tensors)

    for key in ('volume_shape', 'image_shape'):
        if key in model and isinstance(model[key], list):
            model[key] = tuple(model[key])

    logger.info(t("IA/infer/transformer.py:g:61:17"), path)
    return model


def predict_transformer(model: Dict[str, Any], tokens: List[int]) -> Dict[str, Any]:
    """
    Prédiction binaire sur une séquence de tokens avec un Transformer single-head.

    Args:
        model: Dictionnaire de paramètres (issu de load_transformer).
        tokens: Liste d'entiers représentant les tokens.

    Returns:
        dict: {'probability', 'class', 'confidence', 'attention_weights'}
    """
    if not tokens or len(tokens) == 0:
        raise ValueError(t("IA/infer/transformer.py:g:76:26"))

    embedding = model['embedding']
    W_cls = model['W_cls']
    b_cls = model['b_cls']
    embed_dim = model['embed_dim']
    num_blocks = model.get('num_blocks', 1)

    token_indices = np.array(tokens)
    x_emb = embedding[token_indices]
    x = x_emb

    last_attn_weights = None
    for b_idx in range(num_blocks):
        if num_blocks == 1:
            wq = model['W_q']
            wk = model['W_k']
            wv = model['W_v']
            wff1 = model['W_ff1']
            bff1 = model['b_ff1']
            wff2 = model['W_ff2']
            bff2 = model['b_ff2']
            gamma_attn = model.get('gamma_attn')
            beta_attn = model.get('beta_attn')
            gamma_ff = model.get('gamma_ff')
            beta_ff = model.get('beta_ff')
        else:
            wq = model['W_q_list'][b_idx]
            wk = model['W_k_list'][b_idx]
            wv = model['W_v_list'][b_idx]
            wff1 = model['W_ff1_list'][b_idx]
            bff1 = model['b_ff1_list'][b_idx]
            wff2 = model['W_ff2_list'][b_idx]
            bff2 = model['b_ff2_list'][b_idx]
            gamma_attn = model.get('gamma_attn_list', [None] * num_blocks)[b_idx]
            beta_attn = model.get('beta_attn_list', [None] * num_blocks)[b_idx]
            gamma_ff = model.get('gamma_ff_list', [None] * num_blocks)[b_idx]
            beta_ff = model.get('beta_ff_list', [None] * num_blocks)[b_idx]

        Q = x @ wq
        K = x @ wk
        V = x @ wv
        attn_scores = (Q @ K.T) / math.sqrt(embed_dim)
        attention_weights = softmax(attn_scores)
        last_attn_weights = attention_weights
        attn_output = attention_weights @ V

        x_attn = x + attn_output
        x_norm = layer_norm(x_attn)
        if gamma_attn is not None and beta_attn is not None:
            x = x_norm * gamma_attn + beta_attn
        else:
            x = x_norm

        ff1 = relu(x @ wff1 + bff1)
        ff2 = ff1 @ wff2 + bff2

        x_ff = x + ff2
        x_ff_norm = layer_norm(x_ff)
        if gamma_ff is not None and beta_ff is not None:
            x = x_ff_norm * gamma_ff + beta_ff
        else:
            x = x_ff_norm

    pooled = np.mean(x, axis=0).reshape(1, -1)
    logits = pooled @ W_cls + b_cls
    probability = float(1.0 / (1.0 + np.exp(-np.clip(logits, -500, 500)))[0, 0])

    predicted_class = 'Classe 1' if probability > 0.5 else 'Classe 0'
    confidence = float(abs(probability - 0.5) * 2)

    logger.info(t("IA/infer/transformer.py:g:148:17"), probability, predicted_class)

    return {
        'probability': probability,
        'class': predicted_class,
        'confidence': confidence,
        'attention_weights': last_attn_weights,
    }


# ==================================================================
# MiniTransformer3D (multi-head)
# ==================================================================

def load_transformer3d(path: str):
    """
    Charge un modèle MiniTransformer3D multi-head depuis un fichier .gy.

    Args:
        path: Chemin vers le fichier .gy sauvegardé.

    Returns:
        MiniTransformer3D: Instance du modèle chargée.
    """
    path = validate_model_path(path)
    model = MiniTransformer3D.load(path)
    logger.info(t("IA/infer/transformer.py:g:174:17"), path)
    return model


def predict_transformer3d(model, tokens: List[int]) -> Dict[str, Any]:
    """
    Prédiction binaire sur une séquence de tokens avec un Transformer3D multi-head.

    Args:
        model: Instance de MiniTransformer3D (issu de load_transformer3d).
        tokens: Liste d'entiers représentant les tokens.

    Returns:
        dict: {'probability', 'class', 'confidence', 'attention_weights'}
    """
    if not tokens or len(tokens) == 0:
        raise ValueError(t("IA/infer/transformer.py:g:189:26"))

    token_array = np.array(tokens).reshape(1, -1)
    out, cache = model.forward(token_array)

    probability = float(out[0, 0])
    predicted_class = 'Classe 1' if probability > 0.5 else 'Classe 0'
    confidence = float(abs(probability - 0.5) * 2)
    attention_weights = [aw.copy() for aw in cache['attn_weights']]

    logger.info(t("IA/infer/transformer.py:g:200:17"), probability, predicted_class)

    return {
        'probability': probability,
        'class': predicted_class,
        'confidence': confidence,
        'attention_weights': attention_weights,
    }