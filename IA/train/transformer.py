"""
IA/train/transformer.py - Entraînement des Transformers, moteur Flax/JAX.

Modèles :
  - MiniTransformer : single-head, classification de séquences token.
    Supporte un nombre configurable de blocs transformer (num_blocks).
  - MiniTransformer3D : multi-head batché, classification de séquences.

Les deux réseaux sont implémentés en Flax (flax.linen) et entraînés par
autodiff + optax (SGD), au lieu de la rétropropagation manuelle. Les poids
finaux sont extraits du pytree Flax vers les mêmes clés qu'avant :
  - single-head, num_blocks == 1 : embedding, W_q, W_k, W_v, W_ff1, b_ff1,
    W_ff2, b_ff2, W_cls, b_cls
  - single-head, num_blocks > 1  : embedding, W_q_list, W_k_list, W_v_list,
    W_ff1_list, b_ff1_list, W_ff2_list, b_ff2_list, W_cls, b_cls
  - MiniTransformer3D : embedding, W_q, W_k, W_v, W_o, gamma_attn, beta_attn,
    gamma_ff, beta_ff, W_ff1, b_ff1, W_ff2, b_ff2, W_cls, b_cls
donc IA/infer/transformer.py n'a besoin d'aucune modification de format —
le fichier .gy sauvegardé est strictement identique.

Note sur gamma_attn/beta_attn/gamma_ff/beta_ff : dans l'ancienne
implémentation manuelle, ces facteurs de layer-norm étaient initialisés à
1/0 et n'étaient JAMAIS mis à jour par la rétropropagation (aucun dW/dbeta
calculé) — donc mathématiquement de purs no-op tout au long de
l'entraînement. Le layer-norm Flax ci-dessous est donc volontairement
SANS échelle/biais appris (comportement identique), et gamma_attn/
beta_attn/gamma_ff/beta_ff ne sont écrits dans le modèle final que pour
MiniTransformer3D (dont le format .gy les exige — voir `.save()`/`.load()`
ci-dessous), avec leurs valeurs constantes d'origine (1 et 0).

`infer/transformer.py` et `trainer.py::_predict_transformer` ont par
ailleurs été nettoyés de toute dépendance à `backend.py` (passe avant
réimplémentée en numpy pur, comme cnn.py/gan.py/ldm.py/rnn.py) — voir
IA/infer/transformer.py. MiniTransformer3D.forward()/`_multi_head_attention`
(utilisés à l'inférence par infer/transformer.py) sont eux aussi réécrits en
numpy pur ci-dessous ; `.backward()` (rétropropagation manuelle, plus
jamais appelée — l'entraînement passe désormais par Flax/optax) a été
retirée plutôt que laissée comme code mort couplé à backend.py.
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
# Aides numpy pures (inférence de MiniTransformer3D uniquement — plus de
# dépendance à IA/backend.py, comme pour cnn.py/gan.py/ldm.py/rnn.py)
# ==================================================================

def _np_softmax(x):
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / np.sum(e, axis=-1, keepdims=True)


def _np_layer_norm(x, eps=1e-8):
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(var + eps)


def _np_relu(x):
    return np.maximum(0.0, x)


def _np_xavier(shape, rng):
    """Init xavier légère, sans dépendance à backend.py — seulement utilisée
    pour les valeurs de départ de `MiniTransformer3D.__init__`, TOUJOURS
    écrasées ensuite par `.load()` (chemin d'inférence) ou par le résultat
    de l'entraînement Flax (chemin d'entraînement, voir train_transformer3d
    ci-dessous)."""
    fan_sum = sum(shape)
    std_dev = np.sqrt(2.0 / fan_sum) if fan_sum else 1.0
    return rng.standard_normal(shape) * std_dev


# ==================================================================
# Layer-norm Flax sans échelle/biais appris (voir note de module ci-dessus)
# ==================================================================

def _flax_layer_norm(x, eps=1e-8):
    mean = jnp.mean(x, axis=-1, keepdims=True)
    var = jnp.var(x, axis=-1, keepdims=True)
    return (x - mean) / jnp.sqrt(var + eps)


# ==================================================================
# MiniTransformer (single-head)
# ==================================================================

class _TransformerBlock(nn.Module):
    """Un bloc transformer single-head : attention (sans biais, sans
    projection de sortie — comme l'ancienne implémentation) + FFN, avec
    connexions résiduelles et layer-norm. Opère sur une séquence SANS
    dimension de batch (seq_len, embed_dim) — le batch est géré par
    jax.vmap dans train_transformer (§ voir plus bas), pour rester au plus
    près de la boucle « un échantillon à la fois » d'origine."""
    embed_dim: int
    ff_dim: int

    @nn.compact
    def __call__(self, x):
        init = nn.initializers.xavier_uniform()
        W_q = self.param('W_q', init, (self.embed_dim, self.embed_dim))
        W_k = self.param('W_k', init, (self.embed_dim, self.embed_dim))
        W_v = self.param('W_v', init, (self.embed_dim, self.embed_dim))
        W_ff1 = self.param('W_ff1', init, (self.embed_dim, self.ff_dim))
        b_ff1 = self.param('b_ff1', nn.initializers.zeros, (1, self.ff_dim))
        W_ff2 = self.param('W_ff2', init, (self.ff_dim, self.embed_dim))
        b_ff2 = self.param('b_ff2', nn.initializers.zeros, (1, self.embed_dim))

        Q = x @ W_q
        K = x @ W_k
        V = x @ W_v
        attn_scores = (Q @ K.T) / jnp.sqrt(jnp.array(self.embed_dim, dtype=x.dtype))
        attn_weights = jax.nn.softmax(attn_scores, axis=-1)
        attn_output = attn_weights @ V
        x = x + attn_output
        x = _flax_layer_norm(x)

        ff1 = jax.nn.relu(x @ W_ff1 + b_ff1)
        ff2 = ff1 @ W_ff2 + b_ff2
        x = x + ff2
        x = _flax_layer_norm(x)
        return x


class _MiniTransformerNet(nn.Module):
    """Réseau complet single-head : embedding + num_blocks blocs + tête de
    classification. Opère sur UNE séquence de tokens (seq_len,) — vmap-é
    sur l'axe batch par train_transformer."""
    vocab_size: int
    embed_dim: int
    ff_dim: int
    num_blocks: int

    @nn.compact
    def __call__(self, tokens):
        init = nn.initializers.xavier_uniform()
        embedding = self.param('embedding', init, (self.vocab_size, self.embed_dim))
        x = embedding[tokens]
        for b in range(self.num_blocks):
            x = _TransformerBlock(self.embed_dim, self.ff_dim, name=f'block_{b}')(x)
        pooled = jnp.mean(x, axis=0, keepdims=True)
        W_cls = self.param('W_cls', init, (self.embed_dim, 1))
        b_cls = self.param('b_cls', nn.initializers.zeros, (1, 1))
        return pooled @ W_cls + b_cls


def _extract_transformer_params(params, num_blocks):
    """Convertit le pytree Flax {'block_i': {...}, 'W_cls', 'b_cls',
    'embedding'} vers le format historique (clés à plat pour num_blocks==1,
    clés `_list` pour num_blocks>1)."""
    d = {'embedding': np.asarray(params['embedding'])}
    if num_blocks == 1:
        blk = params['block_0']
        d['W_q'] = np.asarray(blk['W_q'])
        d['W_k'] = np.asarray(blk['W_k'])
        d['W_v'] = np.asarray(blk['W_v'])
        d['W_ff1'] = np.asarray(blk['W_ff1'])
        d['b_ff1'] = np.asarray(blk['b_ff1'])
        d['W_ff2'] = np.asarray(blk['W_ff2'])
        d['b_ff2'] = np.asarray(blk['b_ff2'])
    else:
        keys = ('W_q', 'W_k', 'W_v', 'W_ff1', 'b_ff1', 'W_ff2', 'b_ff2')
        lists = {k: [] for k in keys}
        for b in range(num_blocks):
            blk = params[f'block_{b}']
            for k in keys:
                lists[k].append(np.asarray(blk[k]))
        for k in keys:
            d[f'{k}_list'] = lists[k]
    d['W_cls'] = np.asarray(params['W_cls'])
    d['b_cls'] = np.asarray(params['b_cls'])
    return d


def _clip_transformer_weights(params, num_blocks):
    """Clippe les MATRICES DE POIDS à [-1, 1] après chaque mise à jour —
    comme le faisait l'ancienne implémentation manuelle (elle clippait la
    VALEUR des poids après chaque échantillon, pas le gradient — à la
    différence de rnn.py). embedding et biais jamais clippés."""
    def clip_block(blk):
        return {
            'W_q': jnp.clip(blk['W_q'], -1, 1),
            'W_k': jnp.clip(blk['W_k'], -1, 1),
            'W_v': jnp.clip(blk['W_v'], -1, 1),
            'W_ff1': jnp.clip(blk['W_ff1'], -1, 1),
            'b_ff1': blk['b_ff1'],
            'W_ff2': jnp.clip(blk['W_ff2'], -1, 1),
            'b_ff2': blk['b_ff2'],
        }
    new_params = dict(params)
    for b in range(num_blocks):
        new_params[f'block_{b}'] = clip_block(params[f'block_{b}'])
    new_params['W_cls'] = jnp.clip(params['W_cls'], -1, 1)
    return new_params


def train_transformer(X=None, y=None, seq_len=4, vocab_size=6,
                       embed_dim=8, ff_dim=16, lr=0.01, epochs=2000,
                       early_stop_loss=0.001, save_path=None, seed=42,
                       num_blocks=1, on_epoch=None):
    """
    Entraîne un Mini-Transformer single-head pour la classification de séquences.

    Args:
        X: Tokens d'entrée (batch, seq_len).
        y: Labels (batch, 1).
        seq_len: Longueur des séquences.
        vocab_size: Taille du vocabulaire.
        embed_dim: Dimension d'embedding.
        ff_dim: Dimension du feed-forward.
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        early_stop_loss: Seuil de convergence.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        num_blocks: Nombre de blocs transformer (défaut 1). Quand
            num_blocks=1, le comportement est identique à la version
            originale (rétrocompatible).
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: Modèle et métriques.
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_transformer{MODEL_EXTENSION}")

    if X is None or y is None:
        X = np.array([
            [1, 2, 3, 4], [4, 3, 2, 1], [2, 2, 2, 2], [1, 3, 1, 3],
            [0, 1, 2, 3], [5, 4, 3, 2], [1, 1, 2, 2], [3, 2, 3, 2],
        ])
        y = np.array([[1], [0], [1], [0], [1], [0], [1], [0]])

    X = np.asarray(X, dtype=np.int32)
    y = np.asarray(y, dtype=np.float64).reshape(-1, 1)
    n_samples = len(X)

    # ------------------------------------------------------------------ #
    #  Modèle Flax + optimiseur                                          #
    # ------------------------------------------------------------------ #
    model_def = _MiniTransformerNet(vocab_size=vocab_size, embed_dim=embed_dim,
                                     ff_dim=ff_dim, num_blocks=num_blocks)
    key = jax.random.PRNGKey(seed)
    params = model_def.init(key, jnp.zeros((seq_len,), dtype=jnp.int32))['params']

    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    def _apply_batch(p, X_j):
        return jax.vmap(lambda toks: model_def.apply({'params': p}, toks))(X_j)

    def _loss_fn(p, X_j, y_j):
        logits = _apply_batch(p, X_j).reshape(-1, 1)
        pred = jax.nn.sigmoid(logits)
        error = y_j - pred
        # facteur 0.5 : reproduit exactement la règle de mise à jour de
        # l'ancienne rétropropagation manuelle (dy = error * sigmoid_deriv).
        loss = 0.5 * jnp.sum(error ** 2)
        return loss, pred

    grad_fn = jax.value_and_grad(_loss_fn, has_aux=True)

    @jax.jit
    def train_step(p, opt_st, X_j, y_j):
        (loss, pred), grads = grad_fn(p, X_j, y_j)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        p = _clip_transformer_weights(p, num_blocks)
        return p, opt_st, loss, pred

    X_j = jnp.asarray(X)
    y_j = jnp.asarray(y)

    def _live_model_dict(current_params):
        d = {'num_blocks': num_blocks, 'seq_len': seq_len,
             'vocab_size': vocab_size, 'embed_dim': embed_dim}
        d.update(_extract_transformer_params(current_params, num_blocks))
        return d

    best_loss = float('inf')
    best_params = params
    history = []

    for epoch in range(epochs):
        params, opt_state, loss, pred = train_step(params, opt_state, X_j, y_j)
        avg_loss = float(2.0 * loss) / n_samples
        history.append(avg_loss)

        if avg_loss < best_loss:
            best_loss = avg_loss
            best_params = params

        if epoch % 200 == 0:
            logger.info("Epoch %d, Loss: %.6f", epoch, avg_loss)

        if on_epoch is not None:
            stop = on_epoch(epoch, {'loss': avg_loss}, _live_model_dict(params))
            if stop:
                logger.info(_gt("rg7.msg.001"), epoch)
                break

        if avg_loss < early_stop_loss:
            logger.info(_gt("rg7.msg.053"), epoch)
            break

    # ------------------------------------------------------------------ #
    #  Évaluation (via le même model_def.apply — pas de reformulation)    #
    # ------------------------------------------------------------------ #
    final_logits = _apply_batch(best_params, X_j).reshape(-1, 1)
    final_pred = np.asarray(jax.nn.sigmoid(final_logits))
    correct = 0
    for i in range(n_samples):
        pred_cls = 1 if final_pred[i, 0] > 0.5 else 0
        y_val = y[i, 0]
        if pred_cls == y_val:
            correct += 1
    accuracy = correct / n_samples * 100

    model = {'num_blocks': num_blocks, 'seq_len': seq_len,
             'vocab_size': vocab_size, 'embed_dim': embed_dim,
             'accuracy': accuracy}
    model.update(_extract_transformer_params(best_params, num_blocks))

    header, tensors = serialize_model_dict(model)
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.054"), save_path, accuracy)

    return {'model': model, 'save_path': save_path, 'accuracy': accuracy, 'history': history}


# ==================================================================
# MiniTransformer3D (multi-head batché)
# ==================================================================

class _MultiHeadTransformerNet(nn.Module):
    """Réseau multi-head batché — opère directement sur (batch, seq_len) de
    tokens, sans vmap (le batch est géré nativement par les einsum)."""
    vocab_size: int
    embed_dim: int
    ff_dim: int
    num_heads: int

    @nn.compact
    def __call__(self, tokens):
        head_dim = self.embed_dim // self.num_heads
        init = nn.initializers.xavier_uniform()

        embedding = self.param('embedding', init, (self.vocab_size, self.embed_dim))
        x_emb = embedding[tokens]  # (batch, seq, embed_dim)

        W_q = self.param('W_q', init, (self.num_heads, self.embed_dim, head_dim))
        W_k = self.param('W_k', init, (self.num_heads, self.embed_dim, head_dim))
        W_v = self.param('W_v', init, (self.num_heads, self.embed_dim, head_dim))
        W_o = self.param('W_o', init, (self.num_heads * head_dim, self.embed_dim))

        Q = jnp.einsum('bse,hef->bhsf', x_emb, W_q)
        K = jnp.einsum('bse,hef->bhsf', x_emb, W_k)
        V = jnp.einsum('bse,hef->bhsf', x_emb, W_v)
        scores = jnp.einsum('bhsf,bhtf->bhst', Q, K) / jnp.sqrt(jnp.array(head_dim, dtype=x_emb.dtype))
        attn_weights = jax.nn.softmax(scores, axis=-1)
        head_out = jnp.einsum('bhst,bhtf->bhsf', attn_weights, V)
        batch, seq_len = tokens.shape
        concat = jnp.transpose(head_out, (0, 2, 1, 3)).reshape(batch, seq_len, self.num_heads * head_dim)
        attn_output = concat @ W_o

        x = x_emb + attn_output
        x = _flax_layer_norm(x)

        W_ff1 = self.param('W_ff1', init, (self.embed_dim, self.ff_dim))
        b_ff1 = self.param('b_ff1', nn.initializers.zeros, (1, self.ff_dim))
        W_ff2 = self.param('W_ff2', init, (self.ff_dim, self.embed_dim))
        b_ff2 = self.param('b_ff2', nn.initializers.zeros, (1, self.embed_dim))
        ff1 = jax.nn.relu(x @ W_ff1 + b_ff1)
        ff2 = ff1 @ W_ff2 + b_ff2
        x = x + ff2
        x = _flax_layer_norm(x)

        pooled = jnp.mean(x, axis=1)
        W_cls = self.param('W_cls', init, (self.embed_dim, 1))
        b_cls = self.param('b_cls', nn.initializers.zeros, (1, 1))
        logits = pooled @ W_cls + b_cls
        return logits


def _clip_transformer3d_weights(params):
    """Clippe W_q/W_k/W_v/W_o/W_ff1/W_ff2/W_cls à [-1, 1] après chaque mise
    à jour — comme le faisait l'ancien `backward()` manuel. embedding et
    biais jamais clippés."""
    new_params = dict(params)
    for k in ('W_q', 'W_k', 'W_v', 'W_o', 'W_ff1', 'W_ff2', 'W_cls'):
        new_params[k] = jnp.clip(params[k], -1, 1)
    return new_params


class MiniTransformer3D:
    """Transformer 3D multi-head avec batch — forward pass numpy pur (plus
    de dépendance à IA/backend.py, comme cnn.py/gan.py/ldm.py/rnn.py).
    L'entraînement réel se fait désormais via Flax/optax dans
    train_transformer3d ci-dessous ; cette classe reste l'objet chargé/
    utilisé à l'inférence par IA/infer/transformer.py (`.load()`/
    `.forward()`), et le réceptacle des poids entraînés au moment de la
    sauvegarde (`.save()`, peuplé par train_transformer3d)."""

    def __init__(self, vocab_size, seq_len, embed_dim, ff_dim, num_heads=2, seed=42):
        self.vocab_size = vocab_size
        self.seq_len = seq_len
        self.embed_dim = embed_dim
        self.ff_dim = ff_dim
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads

        # Valeurs de départ légères (numpy pur) — toujours écrasées ensuite
        # par `.load()` (inférence) ou par le résultat Flax (entraînement,
        # voir train_transformer3d) : pas besoin d'un vrai xavier ici.
        rng = np.random.RandomState(int(seed))
        self.embedding = _np_xavier((vocab_size, embed_dim), rng)
        self.W_q = _np_xavier((num_heads, embed_dim, self.head_dim), rng)
        self.W_k = _np_xavier((num_heads, embed_dim, self.head_dim), rng)
        self.W_v = _np_xavier((num_heads, embed_dim, self.head_dim), rng)
        self.W_o = _np_xavier((num_heads * self.head_dim, embed_dim), rng)
        self.gamma_attn = np.ones((1, embed_dim))
        self.beta_attn = np.zeros((1, embed_dim))
        self.gamma_ff = np.ones((1, embed_dim))
        self.beta_ff = np.zeros((1, embed_dim))
        self.W_ff1 = _np_xavier((embed_dim, ff_dim), rng)
        self.b_ff1 = np.zeros((1, ff_dim))
        self.W_ff2 = _np_xavier((ff_dim, embed_dim), rng)
        self.b_ff2 = np.zeros((1, embed_dim))
        self.W_cls = _np_xavier((embed_dim, 1), rng)
        self.b_cls = np.zeros((1, 1))

    def forward(self, x):
        """Forward pass numpy pur. x: (batch_size, seq_len)."""
        batch_size, seq_len = x.shape
        x_emb = self.embedding[x]
        attn_output, attn_weights = self._multi_head_attention(x_emb)
        x_attn = x_emb + attn_output
        # gamma_attn/beta_attn restent à 1/0 (jamais appris — voir note de
        # module) : layer-norm pur, appliqué par position comme avant.
        x_attn_norm = np.zeros_like(x_attn)
        for b in range(batch_size):
            for pos in range(seq_len):
                x_attn_norm[b, pos] = _np_layer_norm(x_attn[b, pos]) * self.gamma_attn[0] + self.beta_attn[0]
        ff1 = _np_relu(x_attn_norm @ self.W_ff1 + self.b_ff1)
        ff2 = ff1 @ self.W_ff2 + self.b_ff2
        x_ff = x_attn_norm + ff2
        x_ff_norm = np.zeros_like(x_ff)
        for b in range(batch_size):
            for pos in range(seq_len):
                x_ff_norm[b, pos] = _np_layer_norm(x_ff[b, pos]) * self.gamma_ff[0] + self.beta_ff[0]
        pooled = x_ff_norm.mean(axis=1)
        logits = pooled @ self.W_cls + self.b_cls
        out = 1.0 / (1.0 + np.exp(-np.clip(logits, -500, 500)))
        cache = {
            'x_emb': x_emb, 'attn_output': attn_output, 'attn_weights': attn_weights,
            'x_attn_norm': x_attn_norm, 'ff1': ff1, 'ff2': ff2, 'x_ff_norm': x_ff_norm,
            'pooled': pooled, 'logits': logits,
        }
        return out, cache

    def _multi_head_attention(self, x):
        batch_size, seq_len, embed_dim = x.shape
        head_outputs = []
        attn_weights_list = []
        for head in range(self.num_heads):
            Q = x @ self.W_q[head]
            K = x @ self.W_k[head]
            V = x @ self.W_v[head]
            attn_scores = np.matmul(Q, K.transpose(0, 2, 1)) / np.sqrt(self.head_dim)
            attn_weights = _np_softmax(attn_scores)
            head_output = attn_weights @ V
            head_outputs.append(head_output)
            attn_weights_list.append(attn_weights)
        multi_head_output = np.concatenate(head_outputs, axis=-1)
        output = multi_head_output @ self.W_o
        return output, attn_weights_list

    def save(self, filename):
        model_data = {k: getattr(self, k) for k in [
            'vocab_size', 'seq_len', 'embed_dim', 'ff_dim', 'num_heads', 'head_dim',
            'embedding', 'W_q', 'W_k', 'W_v', 'W_o',
            'gamma_attn', 'beta_attn', 'gamma_ff', 'beta_ff',
            'W_ff1', 'b_ff1', 'W_ff2', 'b_ff2', 'W_cls', 'b_cls']}
        header, tensors = serialize_model_dict(model_data)
        save_model(filename, header, tensors)

    @classmethod
    def load(cls, filename):
        from ..ia_format import load_model as _lm
        header, tensors = _lm(filename)
        d = dict(header)
        for k in header.get('_tensors', []):
            d[k] = tensors[k]
        d.pop('_tensors', None)
        model = cls(d['vocab_size'], d['seq_len'], d['embed_dim'], d['ff_dim'], d['num_heads'])
        for k in ['embedding', 'W_q', 'W_k', 'W_v', 'W_o',
                   'gamma_attn', 'beta_attn', 'gamma_ff', 'beta_ff',
                   'W_ff1', 'b_ff1', 'W_ff2', 'b_ff2', 'W_cls', 'b_cls']:
            setattr(model, k, d[k])
        return model


def train_transformer3d(X=None, y=None, vocab_size=6, seq_len=4,
                         embed_dim=8, ff_dim=16, num_heads=2,
                         lr=0.01, epochs=5000, convergence_loss=0.01,
                         save_path=None, seed=42):
    """
    Entraîne un MiniTransformer3D multi-head batché (moteur Flax/JAX —
    autodiff + optax, au lieu de la rétropropagation manuelle).

    Args:
        X: Tokens (batch, seq_len).
        y: Labels (batch, 1).
        vocab_size, seq_len, embed_dim, ff_dim, num_heads: Hyperparamètres.
        lr, epochs: Optimisation.
        convergence_loss: Seuil de convergence.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.

    Returns:
        dict: Modèle et métriques.
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"transformer_3d{MODEL_EXTENSION}")

    if X is None or y is None:
        X = np.array([[1, 2, 3, 4], [4, 3, 2, 1], [2, 2, 2, 2], [1, 3, 1, 3]])
        y = np.array([[1], [0], [1], [0]])

    X = np.asarray(X, dtype=np.int32)
    y = np.asarray(y, dtype=np.float64).reshape(-1, 1)

    model_def = _MultiHeadTransformerNet(vocab_size=vocab_size, embed_dim=embed_dim,
                                          ff_dim=ff_dim, num_heads=num_heads)
    key = jax.random.PRNGKey(seed)
    params = model_def.init(key, jnp.zeros((1, seq_len), dtype=jnp.int32))['params']

    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    def _loss_fn(p, X_j, y_j):
        logits = model_def.apply({'params': p}, X_j)
        pred = jax.nn.sigmoid(logits)
        error = y_j - pred
        loss = 0.5 * jnp.sum(error ** 2)
        return loss, pred

    grad_fn = jax.value_and_grad(_loss_fn, has_aux=True)

    @jax.jit
    def train_step(p, opt_st, X_j, y_j):
        (loss, pred), grads = grad_fn(p, X_j, y_j)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        p = _clip_transformer3d_weights(p)
        return p, opt_st, loss, pred

    X_j = jnp.asarray(X)
    y_j = jnp.asarray(y)
    history = []

    for epoch in range(epochs):
        params, opt_state, loss, pred = train_step(params, opt_state, X_j, y_j)
        # Perte reportée = mse_loss(pred, y), comme l'ancienne implémentation
        # (découplée de la perte interne 0.5*sum(error**2) utilisée pour le
        # gradient — même écart que dans l'ancien code manuel).
        reported_loss = float(jnp.mean((pred - y_j) ** 2))
        history.append(reported_loss)

        if epoch % 50 == 0:
            logger.info("Epoch %d, Loss: %.6f", epoch, reported_loss)
        if reported_loss < convergence_loss:
            logger.info(_gt("rg7.msg.053"), epoch)
            break

    # ------------------------------------------------------------------ #
    #  Évaluation (via le même model_def.apply)                          #
    # ------------------------------------------------------------------ #
    final_logits = model_def.apply({'params': params}, X_j)
    final_pred = np.asarray(jax.nn.sigmoid(final_logits))
    predictions = (final_pred > 0.5).astype(int)
    accuracy = np.sum(predictions == y) / predictions.size * 100

    # ------------------------------------------------------------------ #
    #  Peuplement de l'objet MiniTransformer3D (format .gy inchangé) et   #
    #  sauvegarde — gamma_attn/beta_attn/gamma_ff/beta_ff restent à       #
    #  leur valeur constante d'origine (jamais appris, voir note module). #
    # ------------------------------------------------------------------ #
    head_dim = embed_dim // num_heads
    model = MiniTransformer3D(vocab_size, seq_len, embed_dim, ff_dim, num_heads, seed)
    model.embedding = np.asarray(params['embedding'])
    model.W_q = np.asarray(params['W_q'])
    model.W_k = np.asarray(params['W_k'])
    model.W_v = np.asarray(params['W_v'])
    model.W_o = np.asarray(params['W_o'])
    model.gamma_attn = np.ones((1, embed_dim))
    model.beta_attn = np.zeros((1, embed_dim))
    model.gamma_ff = np.ones((1, embed_dim))
    model.beta_ff = np.zeros((1, embed_dim))
    model.W_ff1 = np.asarray(params['W_ff1'])
    model.b_ff1 = np.asarray(params['b_ff1'])
    model.W_ff2 = np.asarray(params['W_ff2'])
    model.b_ff2 = np.asarray(params['b_ff2'])
    model.W_cls = np.asarray(params['W_cls'])
    model.b_cls = np.asarray(params['b_cls'])

    model.save(save_path)
    logger.info(_gt("rg7.msg.055"), save_path, accuracy)

    return {'model': model, 'save_path': save_path, 'accuracy': accuracy, 'history': history}
