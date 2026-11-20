"""
IA/train/rnn.py — Entraînement du RNN vanilla (empilé), moteur Flax/JAX.

Architecture : RNN empilé (stacked) pour la classification de séquences
binaires. Le réseau (cellules RNN vanilla + tête de sortie) est implémenté
en Flax (flax.linen, via nn.scan sur l'axe temporel) et entraîné par
autodiff + optax (SGD), au lieu de la rétropropagation manuelle (BPTT).

Les poids finaux sont extraits du pytree Flax vers les mêmes clés qu'avant :
  - num_layers == 1  : W_xh, W_hh, b_h, W_hy, b_y
  - num_layers > 1   : W_xh_0..N, W_hh_0..N, b_h_0..N, W_hy, b_y
donc IA/infer/rnn.py n'a besoin d'aucune modification de format — le
fichier .gy sauvegardé est strictement identique. infer/rnn.py a par
ailleurs été nettoyé de toute dépendance à backend.py (passe avant
réimplémentée en numpy pur, comme cnn.py/ldm.py).
"""

import os
import logging

import numpy as np
import jax
import jax.numpy as jnp
import optax
import flax.linen as nn

from ..config import MODELS_DIR, MODEL_EXTENSION, ensure_directories
from ..ia_format import save_model, serialize_model_dict
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)


# ==================================================================
# Cellule RNN vanilla (Flax) + réseau empilé
# ==================================================================

class _RNNCell(nn.Module):
    """Une cellule RNN vanilla : h_new = tanh(x @ W_xh + h_prev @ W_hh + b_h).

    Paramètres nommés explicitement (W_xh, W_hh, b_h) pour correspondre
    directement aux clés historiques du format de sauvegarde.
    """
    hidden_size: int

    @nn.compact
    def __call__(self, h_prev, x_t):
        fan_in = x_t.shape[-1]
        W_xh = self.param('W_xh', nn.initializers.normal(stddev=0.1), (fan_in, self.hidden_size))
        W_hh = self.param('W_hh', nn.initializers.normal(stddev=0.1), (self.hidden_size, self.hidden_size))
        b_h = self.param('b_h', nn.initializers.zeros, (1, self.hidden_size))
        h_new = jnp.tanh(jnp.matmul(x_t, W_xh) + jnp.matmul(h_prev, W_hh) + b_h)
        return h_new, h_new


# Variante "scannée" de la cellule sur l'axe temporel (axe 1 de X : batch, seq, feat)
_ScanRNNCell = nn.scan(
    _RNNCell,
    variable_broadcast='params',
    split_rngs={'params': False},
    in_axes=1,
    out_axes=1,
)


class _StackedRNNNet(nn.Module):
    """RNN empilé : num_layers cellules scannées + tête de sortie linéaire."""
    hidden_size: int
    num_layers: int

    @nn.compact
    def __call__(self, X):
        batch = X.shape[0]
        layer_in = X
        last_h = jnp.zeros((batch, self.hidden_size))
        for l in range(self.num_layers):
            h0 = jnp.zeros((batch, self.hidden_size))
            last_h, outputs = _ScanRNNCell(hidden_size=self.hidden_size, name=f'cell_{l}')(h0, layer_in)
            layer_in = outputs
        logits = nn.Dense(1, name='out')(last_h)
        return logits


def _extract_rnn_params(params, num_layers):
    """Convertit le pytree Flax {'cell_i': {...}, 'out': {...}} vers le
    format historique (W_xh[_i], W_hh[_i], b_h[_i], W_hy, b_y)."""
    d = {}
    if num_layers == 1:
        cell = params['cell_0']
        d['W_xh'] = np.asarray(cell['W_xh'])
        d['W_hh'] = np.asarray(cell['W_hh'])
        d['b_h'] = np.asarray(cell['b_h'])
    else:
        for l in range(num_layers):
            cell = params[f'cell_{l}']
            d[f'W_xh_{l}'] = np.asarray(cell['W_xh'])
            d[f'W_hh_{l}'] = np.asarray(cell['W_hh'])
            d[f'b_h_{l}'] = np.asarray(cell['b_h'])
    d['W_hy'] = np.asarray(params['out']['kernel'])
    d['b_y'] = np.asarray(params['out']['bias']).reshape(1, 1)
    return d


def _clip_kernel_grads(grads, num_layers, grad_clip):
    """Clippe uniquement les gradients des matrices de poids (W_xh, W_hh,
    W_hy) — jamais les biais — comme le faisait le BPTT manuel d'origine."""
    out = {}
    for l in range(num_layers):
        key = 'cell_0' if num_layers == 1 else f'cell_{l}'
        cell = grads[key]
        out[key] = {
            'W_xh': jnp.clip(cell['W_xh'], -grad_clip, grad_clip),
            'W_hh': jnp.clip(cell['W_hh'], -grad_clip, grad_clip),
            'b_h': cell['b_h'],
        }
    out['out'] = {
        'kernel': jnp.clip(grads['out']['kernel'], -grad_clip, grad_clip),
        'bias': grads['out']['bias'],
    }
    return out


def train_rnn(X=None, y=None, hidden_size=4, lr=0.1, epochs=1000,
              save_path=None, seed=42, grad_clip=1.0, num_layers=1,
              on_epoch=None):
    """
    Entraîne un RNN vanilla (empilé) pour la classification de séquences.

    Args:
        X: Données d'entrée (batch, seq_len, input_size).
           Par défaut : 2 séquences, 5 pas de temps, 2 features.
        y: Labels (batch, 1). Par défaut : [[1], [0]].
        hidden_size: Taille de l'état caché (identique pour toutes les couches).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques maximum.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        grad_clip: Valeur maximale des gradients (W_xh, W_hh, W_hy uniquement).
        num_layers: Nombre de couches RNN empilées (défaut 1).
            Quand num_layers=1, clés du modèle : W_xh, W_hh, b_h (identique
            à l'ancienne version monocouche). Pour num_layers>1 : W_xh_0,
            W_xh_1, …, W_hh_0, …, b_h_0, … .
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: Paramètres du modèle et métriques.
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_rnn{MODEL_EXTENSION}")

    if X is None or y is None:
        X = np.array([
            [[1, 0], [0, 1], [1, 0], [0, 1], [1, 0]],
            [[0, 1], [1, 0], [0, 1], [1, 0], [0, 1]],
        ])
        y = np.array([[1], [0]])

    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).reshape(-1, 1)
    seq_len = X.shape[1]
    input_size = X.shape[2]
    n_samples = len(X)

    # ------------------------------------------------------------------ #
    #  Modèle Flax + optimiseur                                          #
    # ------------------------------------------------------------------ #
    model_def = _StackedRNNNet(hidden_size=hidden_size, num_layers=num_layers)
    key = jax.random.PRNGKey(seed)
    params = model_def.init(key, jnp.zeros((1, seq_len, input_size), dtype=jnp.float64))['params']

    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    def _loss_fn(p, X_j, y_j):
        logits = model_def.apply({'params': p}, X_j)
        pred = jax.nn.sigmoid(logits)
        error = y_j - pred
        # facteur 0.5 : reproduit exactement la règle de mise à jour du
        # BPTT manuel d'origine (dy = error * sigmoid_deriv(pred)).
        loss = 0.5 * jnp.sum(error ** 2)
        return loss, pred

    grad_fn = jax.value_and_grad(_loss_fn, has_aux=True)

    @jax.jit
    def train_step(p, opt_st, X_j, y_j):
        (loss, pred), grads = grad_fn(p, X_j, y_j)
        grads = _clip_kernel_grads(grads, num_layers, grad_clip)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        return p, opt_st, loss, pred

    X_j = jnp.asarray(X)
    y_j = jnp.asarray(y)

    def _live_model_dict(current_params):
        d = {
            'num_layers': num_layers,
            'hidden_size': hidden_size,
            'seq_len': seq_len,
            'input_size': input_size,
            'activations': {'hidden': 'tanh', 'output': 'sigmoid'},
        }
        d.update(_extract_rnn_params(current_params, num_layers))
        return d

    history = []

    for epoch in range(epochs):
        params, opt_state, loss, pred = train_step(params, opt_state, X_j, y_j)
        # total_loss équivalent à l'ancienne version (sum(error**2), sans le
        # facteur 0.5 utilisé uniquement pour aligner la règle de mise à jour)
        avg_loss = float(2.0 * loss) / n_samples
        history.append(avg_loss)

        if epoch % 200 == 0:
            logger.info("Epoch %d, Loss: %.6f", epoch, avg_loss)

        if on_epoch is not None:
            stop = on_epoch(epoch, {'loss': avg_loss}, _live_model_dict(params))
            if stop:
                logger.info(_gt("rg7.msg.001"), epoch)
                break

    # ------------------------------------------------------------------ #
    #  Évaluation                                                        #
    # ------------------------------------------------------------------ #
    final_logits = model_def.apply({'params': params}, X_j)
    final_pred = np.asarray(jax.nn.sigmoid(final_logits))
    correct = 0
    for i in range(n_samples):
        pred_cls = 1 if final_pred[i, 0] > 0.5 else 0
        y_val = y[i, 0]
        if pred_cls == y_val:
            correct += 1
    accuracy = correct / n_samples * 100

    # ------------------------------------------------------------------ #
    #  Sauvegarde du modèle                                              #
    # ------------------------------------------------------------------ #
    model = {
        'num_layers': num_layers,
        'hidden_size': hidden_size,
        'seq_len': seq_len,
        'input_size': input_size,
        'activations': {'hidden': 'tanh', 'output': 'sigmoid'},
        'accuracy': accuracy,
    }
    model.update(_extract_rnn_params(params, num_layers))

    header, tensors = serialize_model_dict(model)
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.026"), save_path, accuracy)

    return {'model': model, 'save_path': save_path, 'accuracy': accuracy, 'history': history}
