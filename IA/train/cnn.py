"""
IA/train/cnn.py — Entraînement des CNN 2D et N-D, moteur Flax/JAX.

Modèles :
  - CNN2D : classification d'images 5x5 binaire (croix, carré, diagonale, bordure, point)
  - CNNND : classification de volumes N-D (configurable)

Supporte un nombre configurable de couches de convolution via le paramètre
``num_conv_layers``. Le forward/backward est délégué à JAX (autodiff) via
jax.lax.conv_general_dilated (convolution "valid", un seul canal — même
sémantique que l'ancien moteur numpy) + optax. Les poids finaux sont
extraits vers les mêmes clés ('kernels', 'biases', 'w_fc', 'b_fc') qu'avant,
donc IA/infer/cnn.py n'a besoin d'aucune modification de format.
"""

import math
import numpy as np
import os
import logging

import jax
import jax.numpy as jnp
import optax

from ..config import get_config, MODELS_DIR, MODEL_EXTENSION, ensure_directories
from ..ia_format import save_model, serialize_model_dict
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)


# ==================================================================
# Convolution générique N-D "valid", un seul canal — via lax.conv_general_dilated
# ==================================================================

def _conv_valid_nd(x, kernel):
    """Convolution N-D 'valid', batch=1, canal=1, via JAX.

    x      : (*spatial_dims)
    kernel : (*kernel_spatial_dims)  — même nombre de dimensions que x.
    Retourne un tenseur (*out_spatial_dims), sans dimension batch/canal.
    """
    ndim = x.ndim
    lhs = x.reshape((1, 1) + x.shape)          # (N=1, C=1, *spatial)
    rhs = kernel.reshape((1, 1) + kernel.shape)  # (O=1, I=1, *kernel_spatial)
    spatial_dim_nums = tuple(range(2, 2 + ndim))
    dn = jax.lax.ConvDimensionNumbers(
        lhs_spec=(0, 1) + spatial_dim_nums,
        rhs_spec=(0, 1) + spatial_dim_nums,
        out_spec=(0, 1) + spatial_dim_nums,
    )
    out = jax.lax.conv_general_dilated(
        lhs, rhs, window_strides=(1,) * ndim, padding='VALID',
        dimension_numbers=dn,
    )
    return out.reshape(out.shape[2:])


# ==================================================================
# CNN 2D
# ==================================================================

def create_2d_pattern(shape, pattern_type="cross"):
    """Crée des motifs 2D variés pour l'entraînement."""
    img = np.zeros(shape)
    center_y, center_x = shape[0] // 2, shape[1] // 2

    if pattern_type == "cross":
        img[center_y, :] = 1
        img[:, center_x] = 1
    elif pattern_type == "square":
        img[center_y-1:center_y+2, center_x-1:center_x+2] = 1
    elif pattern_type == "diagonal":
        for i in range(min(shape)):
            img[i, i] = 1
    elif pattern_type == "border":
        img[0, :] = 1
        img[-1, :] = 1
        img[:, 0] = 1
        img[:, -1] = 1
    elif pattern_type == "random":
        img = np.random.randint(0, 2, shape)
    elif pattern_type == "dot":
        img[center_y, center_x] = 1
    return img


def _forward_stack(x, kernels, biases, w_fc, b_fc, num_conv_layers):
    """Forward pass commun 2D/N-D : couches conv (valid) + ReLU, puis FC + ReLU."""
    for layer_i in range(num_conv_layers):
        conv = _conv_valid_nd(x, kernels[layer_i]) + biases[layer_i]
        x = jax.nn.relu(conv)
    flat = x.reshape(1, -1)
    out = jax.nn.relu(flat @ w_fc + b_fc)
    return out


def train_cnn2d(X=None, y=None, input_shape=(5, 5), kernel_shape=(3, 3),
                lr=0.01, epochs=1000, early_stop_loss=0.001,
                save_path=None, seed=42, num_conv_layers=1, on_epoch=None):
    """
    Entraîne un CNN 2D pour la classification de motifs (moteur Flax/JAX).

    Args: identiques à la version précédente (voir docstrings historiques).

    Returns:
        dict: Paramètres du modèle entraîné et métriques.
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_cnn{MODEL_EXTENSION}")

    if X is None or y is None:
        X = np.array([
            create_2d_pattern(input_shape, "cross"),
            create_2d_pattern(input_shape, "random"),
            create_2d_pattern(input_shape, "square"),
            create_2d_pattern(input_shape, "diagonal"),
            create_2d_pattern(input_shape, "border"),
            create_2d_pattern(input_shape, "random"),
            create_2d_pattern(input_shape, "dot"),
            create_2d_pattern(input_shape, "cross"),
        ])
        y = np.array([[1], [0], [1], [1], [1], [0], [1], [1]])

    X_j = jnp.asarray(X, dtype=jnp.float64)
    y_j = jnp.asarray(y, dtype=jnp.float64)

    # --- Initialisation (Xavier, comme l'ancien moteur) ----------------------
    def _xavier(shape, sd_seed):
        key = jax.random.PRNGKey(sd_seed)
        fan_sum = sum(shape)
        std_dev = math.sqrt(2.0 / fan_sum)
        return jax.random.normal(key, shape, dtype=jnp.float64) * std_dev

    kernels = []
    biases = []
    current_shape = input_shape
    for layer_i in range(num_conv_layers):
        kernels.append(_xavier(kernel_shape, seed + layer_i))
        biases.append(jnp.zeros((1,), dtype=jnp.float64))
        current_shape = (current_shape[0] - kernel_shape[0] + 1,
                         current_shape[1] - kernel_shape[1] + 1)
    conv_shape = current_shape
    fc_input_size = math.prod(conv_shape)
    w_fc = _xavier((fc_input_size, 1), seed + num_conv_layers)
    b_fc = jnp.zeros((1,), dtype=jnp.float64)

    params = {'kernels': kernels, 'biases': biases, 'w_fc': w_fc, 'b_fc': b_fc}
    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    def loss_fn(p, img, target):
        out = _forward_stack(img, p['kernels'], p['biases'], p['w_fc'], p['b_fc'], num_conv_layers)
        error = target - out
        return jnp.sum(error ** 2), out

    grad_fn = jax.value_and_grad(loss_fn, has_aux=True)

    @jax.jit
    def train_step(p, opt_st, img, target):
        (loss, _out), grads = grad_fn(p, img, target)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        return p, opt_st, loss

    best_loss = float('inf')
    best_params = params
    history = []

    def _live_model_dict(p):
        return {
            'kernels': [np.asarray(k) for k in p['kernels']],
            'biases': [np.asarray(b) for b in p['biases']],
            'w_fc': np.asarray(p['w_fc']), 'b_fc': np.asarray(p['b_fc']),
            'input_shape': input_shape, 'kernel_shape': kernel_shape,
            'conv_shape': conv_shape, 'num_conv_layers': num_conv_layers,
        }

    for epoch in range(epochs):
        total_loss = 0.0
        for i in range(len(X)):
            params, opt_state, loss = train_step(params, opt_state, X_j[i], y_j[i])
            total_loss += float(loss)

        avg_loss = total_loss / len(X)
        history.append(avg_loss)

        if avg_loss < best_loss:
            best_loss = avg_loss
            best_params = jax.tree_util.tree_map(lambda a: a, params)

        if epoch % 100 == 0:
            logger.info("Epoch %d, Loss: %.6f", epoch, avg_loss)

        if on_epoch is not None:
            stop = on_epoch(epoch, {'loss': avg_loss}, _live_model_dict(params))
            if stop:
                logger.info(_gt("rg7.msg.001"), epoch)
                break

        if avg_loss < early_stop_loss:
            logger.info(_gt("rg7.msg.002"), epoch)
            break

    kernels = [np.asarray(k) for k in best_params['kernels']]
    biases = [np.asarray(b) for b in best_params['biases']]
    w_fc = np.asarray(best_params['w_fc'])
    b_fc = np.asarray(best_params['b_fc'])

    # --- Évaluation -----------------------------------------------------------
    correct = 0
    for i in range(len(X)):
        x = X[i]
        for layer_i in range(num_conv_layers):
            conv = np.asarray(_conv_valid_nd(jnp.asarray(x), jnp.asarray(kernels[layer_i]))) + biases[layer_i]
            x = np.maximum(0.0, conv)
        flat = x.flatten().reshape(1, -1)
        out = np.maximum(0.0, flat @ w_fc + b_fc)
        prediction = 1 if out[0, 0] > 0.5 else 0
        y_val = y[i][0] if hasattr(y[i], '__len__') else y[i]
        if prediction == y_val:
            correct += 1
    accuracy = correct / len(X) * 100

    model = {
        'kernels': kernels, 'biases': biases,
        'w_fc': w_fc, 'b_fc': b_fc,
        'input_shape': input_shape, 'kernel_shape': kernel_shape,
        'conv_shape': conv_shape, 'accuracy': accuracy,
        'num_conv_layers': num_conv_layers,
    }
    header, tensors = serialize_model_dict(model)
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.003"), save_path, accuracy)

    return {'model': model, 'save_path': save_path, 'accuracy': accuracy,
            'final_loss': best_loss, 'history': history}


# ==================================================================
# CNN N-D
# ==================================================================

def create_nd_volume(shape, pattern_type="center"):
    """Crée un volume ND avec différents motifs."""
    volume = np.zeros(shape)
    center = tuple(dim // 2 for dim in shape)

    if pattern_type == "center":
        if all(0 <= center[i] < shape[i] for i in range(len(shape))):
            volume[center] = 1
    elif pattern_type == "full":
        volume[:] = 1
    elif pattern_type == "random":
        volume = np.random.randint(0, 2, shape)
    elif pattern_type == "corner":
        corner = tuple(0 for _ in shape)
        volume[corner] = 1
    return volume


def train_cnn_nd(dimensions=4, volume_shape=None, kernel_shape=None,
                 lr=0.01, epochs=1000, save_path=None, seed=42,
                 num_conv_layers=1):
    """
    Entraîne un CNN N-D pour la classification de volumes (moteur Flax/JAX).

    Args: identiques à la version précédente.

    Returns:
        dict: Paramètres du modèle entraîné et métriques.
    """
    ensure_directories()

    if volume_shape is None:
        volume_shape = (3, 3, 3, 3)[:dimensions]
    if kernel_shape is None:
        kernel_shape = (2, 2, 2, 2)[:dimensions]
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_cnn_{dimensions}d{MODEL_EXTENSION}")

    X = np.array([
        create_nd_volume(volume_shape, "center"),
        create_nd_volume(volume_shape, "full"),
        create_nd_volume(volume_shape, "random"),
        create_nd_volume(volume_shape, "corner"),
        create_nd_volume(volume_shape, "center"),
        create_nd_volume(volume_shape, "random"),
    ])
    y = np.array([[1], [1], [0], [1], [1], [0]])
    X_j = jnp.asarray(X, dtype=jnp.float64)
    y_j = jnp.asarray(y, dtype=jnp.float64)

    def _randn_scaled(shape, sd_seed, scale=0.1):
        key = jax.random.PRNGKey(sd_seed)
        return jax.random.normal(key, shape, dtype=jnp.float64) * scale

    kernels = []
    biases = []
    current_shape = volume_shape
    for layer_i in range(num_conv_layers):
        kernels.append(_randn_scaled(tuple(kernel_shape), seed + layer_i * 2))
        biases.append(_randn_scaled((1,), seed + layer_i * 2 + 1))
        current_shape = tuple(
            current_shape[d] - kernel_shape[d] + 1 for d in range(dimensions)
        )
    conv_shape = current_shape
    fc_input_size = math.prod(conv_shape)
    w_fc = _randn_scaled((fc_input_size, 1), seed + num_conv_layers * 2)
    b_fc = _randn_scaled((1,), seed + num_conv_layers * 2 + 1)

    params = {'kernels': kernels, 'biases': biases, 'w_fc': w_fc, 'b_fc': b_fc}
    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    def loss_fn(p, volume, target):
        out = _forward_stack(volume, p['kernels'], p['biases'], p['w_fc'], p['b_fc'], num_conv_layers)
        error = target - out
        return jnp.sum(error ** 2)

    grad_fn = jax.value_and_grad(loss_fn)

    @jax.jit
    def train_step(p, opt_st, volume, target):
        loss, grads = grad_fn(p, volume, target)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        # Clip comme l'ancien moteur (kernels + w_fc dans [-1, 1])
        p = dict(p)
        p['kernels'] = [jnp.clip(k, -1.0, 1.0) for k in p['kernels']]
        p['w_fc'] = jnp.clip(p['w_fc'], -1.0, 1.0)
        return p, opt_st, loss

    history = []
    for epoch in range(epochs):
        total_loss = 0.0
        for i in range(len(X)):
            params, opt_state, loss = train_step(params, opt_state, X_j[i], y_j[i])
            total_loss += float(loss)
        avg_loss = total_loss / len(X)
        history.append(avg_loss)
        if epoch % 100 == 0:
            logger.info("Epoch %d, Loss: %.6f", epoch, avg_loss)

    kernels = [np.asarray(k) for k in params['kernels']]
    biases = [np.asarray(b) for b in params['biases']]
    w_fc = np.asarray(params['w_fc'])
    b_fc = np.asarray(params['b_fc'])

    correct = 0
    for i in range(len(X)):
        x = X[i]
        for layer_i in range(num_conv_layers):
            conv = np.asarray(_conv_valid_nd(jnp.asarray(x), jnp.asarray(kernels[layer_i]))) + biases[layer_i]
            x = np.maximum(0.0, conv)
        flat = x.flatten().reshape(1, -1)
        out = np.maximum(0.0, flat @ w_fc + b_fc)
        prediction = 1 if out[0, 0] > 0.5 else 0
        y_val = y[i][0] if hasattr(y[i], '__len__') else y[i]
        if prediction == y_val:
            correct += 1
    accuracy = correct / len(X) * 100

    model = {
        'kernels': kernels, 'biases': biases,
        'w_fc': w_fc, 'b_fc': b_fc,
        'volume_shape': volume_shape, 'kernel_shape': kernel_shape,
        'dimensions': dimensions, 'conv_shape': conv_shape,
        'accuracy': accuracy, 'num_conv_layers': num_conv_layers,
    }
    header, tensors = serialize_model_dict(model)
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.004"), dimensions, save_path, accuracy)

    return {'model': model, 'save_path': save_path, 'accuracy': accuracy, 'history': history}
