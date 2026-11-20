"""
IA/train/slm.py — Entraînement des Small Language Models (SLM), moteur Flax/JAX.

Modèles :
  - train_slm_next_word : Prédiction du mot suivant (vocabulaire français, 10 mots).
  - train_slm_classifier : Classification de texte générique (voir SLM_TACHES) —
    tache='emotion' (6 classes), 'humeur' (8 classes), 'type_phrase' (5 classes)
    ou 'sentiment' (3 classes, défaut).
  - train_slm_emotion / train_slm_mood / train_slm_statement / train_slm_sentiment :
    alias de compatibilite de train_slm_classifier pour chaque tache.

Le réseau (SLMClassifier : embedding + blocs [self-attention -> Add&Norm ->
FFN(GELU) -> Add&Norm] -> pooling -> tête de classification) est entraîné en
Flax (flax.linen) par autodiff + optax (SGD), au lieu de la rétropropagation
manuelle — plus de dépendance à IA/backend.py (comme cnn.py/rnn.py/
transformer.py/gan.py/ldm.py/mlp.py/vision.py). Les poids finaux sont extraits
du pytree Flax vers les mêmes clés qu'avant :
  - num_blocks == 1 : embedding, W_q, W_k, W_v, gamma_attn, beta_attn,
    gamma_ff, beta_ff, W_ff1, b_ff1, W_ff2, b_ff2, W_cls, b_cls
  - num_blocks > 1  : embedding, W_q_i, W_k_i, W_v_i, gamma_attn_i, beta_attn_i,
    gamma_ff_i, beta_ff_i, W_ff1_i, b_ff1_i, W_ff2_i, b_ff2_i, W_cls, b_cls
donc IA/infer/slm.py et IA/trainer.py (_predict_slm) n'ont besoin d'aucune
modification de format — le fichier .gy sauvegardé est strictement identique.

Note sur gamma_attn/beta_attn/gamma_ff/beta_ff : comme dans train/transformer.py,
ces facteurs de layer-norm étaient initialisés à 1/0 dans l'ancienne
implémentation manuelle et n'étaient JAMAIS mis à jour par la rétropropagation
(aucun gradient calculé) — de purs no-op tout au long de l'entraînement. Le
layer-norm Flax ci-dessous est donc volontairement SANS échelle/biais appris
(comportement identique), et gamma_attn/beta_attn/gamma_ff/beta_ff ne sont
écrits dans le modèle final qu'avec leurs valeurs constantes d'origine (1 et 0),
puisque le format .gy les exige (voir IA/infer/slm.py).

SLMClassifier.forward() (utilisé pour la reconstruction/l'évaluation d'un
modèle depuis ses params, jamais dans la boucle de gradient) a été réécrit en
numpy pur ; SLMClassifier.backward() (rétropropagation manuelle, plus jamais
appelée) a été retirée.
"""

import math
import numpy as np
import os
import logging

import jax
import jax.numpy as jnp
import optax
import flax.linen as nn

from ..config import MODELS_DIR, MODEL_EXTENSION, ensure_directories
from ..ia_format import save_model, serialize_model_dict
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tfr as _gtfr  # noqa: E402

logger = logging.getLogger(__name__)


class _SeedGenerator:
    """Générateur de seeds pour les initialisations numpy (SLM)."""
    __slots__ = ('_counter',)
    def __init__(self, start=0):
        self._counter = start
    def next(self):
        s = self._counter
        self._counter += 1
        return s

_seed_gen = _SeedGenerator()

def _next_seed():
    return _seed_gen.next()


# ==================================================================
# Fonctions communes (numpy pur — plus de dépendance à backend.py)
# ==================================================================

def softmax(x):
    x = np.asarray(x, dtype=np.float64)
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / np.sum(e, axis=-1, keepdims=True)


def layer_norm(x, eps=1e-8):
    x = np.asarray(x, dtype=np.float64)
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(var + eps)


def gelu(x):
    return 0.5 * x * (1 + np.tanh(math.sqrt(2 / math.pi) * (x + 0.044715 * x ** 3)))


def xavier_init(shape):
    shape = tuple(shape)
    rng = np.random.RandomState(int(_next_seed()))
    fan_sum = sum(shape)
    std_dev = np.sqrt(2.0 / fan_sum) if fan_sum else 1.0
    return rng.standard_normal(shape) * std_dev


# ==================================================================
# TextPreprocessor
# ==================================================================

class TextPreprocessor:
    """Préprocesseur de texte simple : tokenisation whitespace + padding."""

    def __init__(self):
        self.word_to_idx = {}
        self.vocab_size = 0

    def fit(self, texts):
        """Construit le vocabulaire à partir d'une liste de textes."""
        all_words = set()
        for text in texts:
            all_words.update(self._tokenize(text))
        self.word_to_idx = {w: i + 2 for i, w in enumerate(sorted(all_words))}
        self.word_to_idx['PAD'] = 0
        self.word_to_idx['UNK'] = 1
        self.vocab_size = len(self.word_to_idx)

    def _tokenize(self, text):
        """Tokenisation simple : espace + minuscules."""
        return text.lower().split()

    def encode(self, text, seq_len):
        """Tokenise et pad/tronque à seq_len."""
        tokens = self._tokenize(text)
        indices = [self.word_to_idx.get(t, 1) for t in tokens]
        if len(indices) < seq_len:
            indices += [0] * (seq_len - len(indices))
        else:
            indices = indices[:seq_len]
        return np.array(indices)

    def get_params(self):
        return {'word_to_idx': self.word_to_idx, 'vocab_size': self.vocab_size}

    @classmethod
    def from_params(cls, params):
        p = cls()
        p.word_to_idx = params['word_to_idx']
        p.vocab_size = params['vocab_size']
        return p


# ==================================================================
# SLMClassifier
# ==================================================================

class SLMClassifier:
    """Classifieur SLM : Embedding -> [Self-Attention -> Add&Norm -> FFN(GELU) -> Add&Norm] * num_blocks -> Pool -> Softmax.

    Supports configurable depth via ``num_blocks`` (default 1).

    - When ``num_blocks=1``: parameters are stored with flat names
      (``W_q``, ``W_k``, …) for backward-compatible serialisation.
    - When ``num_blocks>1``: each block's parameters are stored in
      indexed lists (``_W_q[i]``, ``_K_k[i]``, …).

    L'entraînement de ce réseau se fait désormais via Flax/JAX (voir
    ``_train_slm_core`` ci-dessous) ; cette classe et sa méthode ``forward()``
    (numpy pur) ne servent plus qu'à représenter/évaluer un modèle depuis un
    dictionnaire de paramètres (``from_params``/``get_params``/``save``/``load``).
    """

    # Parameter key groups used per transformer block
    _BLOCK_PARAM_NAMES = [
        'W_q', 'W_k', 'W_v',
        'gamma_attn', 'beta_attn',
        'gamma_ff', 'beta_ff',
        'W_ff1', 'b_ff1', 'W_ff2', 'b_ff2',
    ]

    def __init__(self, vocab_size, seq_len, embed_dim, num_classes,
                 ff_dim=None, num_blocks=1, seed=42):
        self.num_blocks = num_blocks
        self.vocab_size = vocab_size
        self.seq_len = seq_len
        self.embed_dim = embed_dim
        self.num_classes = num_classes
        self.ff_dim = ff_dim if ff_dim is not None else embed_dim * 2

        self.embedding = xavier_init((vocab_size, embed_dim))

        # ----- Block parameters (always stored as lists internally) -----
        self._W_q = [xavier_init((embed_dim, embed_dim)) for _ in range(num_blocks)]
        self._W_k = [xavier_init((embed_dim, embed_dim)) for _ in range(num_blocks)]
        self._W_v = [xavier_init((embed_dim, embed_dim)) for _ in range(num_blocks)]
        self._gamma_attn = [np.ones((1, embed_dim)) for _ in range(num_blocks)]
        self._beta_attn = [np.zeros((1, embed_dim)) for _ in range(num_blocks)]
        self._gamma_ff = [np.ones((1, embed_dim)) for _ in range(num_blocks)]
        self._beta_ff = [np.zeros((1, embed_dim)) for _ in range(num_blocks)]
        self._W_ff1 = [xavier_init((embed_dim, self.ff_dim)) for _ in range(num_blocks)]
        self._b_ff1 = [np.zeros((1, self.ff_dim)) for _ in range(num_blocks)]
        self._W_ff2 = [xavier_init((self.ff_dim, embed_dim)) for _ in range(num_blocks)]
        self._b_ff2 = [np.zeros((1, embed_dim)) for _ in range(num_blocks)]

        # Backward-compatible flat attributes when num_blocks == 1
        if num_blocks == 1:
            self.W_q = self._W_q[0]
            self.W_k = self._W_k[0]
            self.W_v = self._W_v[0]
            self.gamma_attn = self._gamma_attn[0]
            self.beta_attn = self._beta_attn[0]
            self.gamma_ff = self._gamma_ff[0]
            self.beta_ff = self._beta_ff[0]
            self.W_ff1 = self._W_ff1[0]
            self.b_ff1 = self._b_ff1[0]
            self.W_ff2 = self._W_ff2[0]
            self.b_ff2 = self._b_ff2[0]

        # Classification head (shared across all blocks)
        self.W_cls = xavier_init((embed_dim, num_classes))
        self.b_cls = np.zeros((1, num_classes))

    # ------------------------------------------------------------------
    # Forward (numpy pur — évaluation/inférence seulement)
    # ------------------------------------------------------------------
    def forward(self, x, pad_mask=None):
        """x: (seq_len,) token indices. Returns (num_classes,) softmax probabilities."""
        h = self.embedding[x]  # (seq_len, embed_dim)

        for i in range(self.num_blocks):
            W_q, W_k, W_v = self._W_q[i], self._W_k[i], self._W_v[i]
            gamma_attn, beta_attn = self._gamma_attn[i], self._beta_attn[i]
            gamma_ff, beta_ff = self._gamma_ff[i], self._beta_ff[i]
            W_ff1, b_ff1 = self._W_ff1[i], self._b_ff1[i]
            W_ff2, b_ff2 = self._W_ff2[i], self._b_ff2[i]

            Q = h @ W_q
            K = h @ W_k
            V = h @ W_v
            attn_scores = (Q @ K.T) / math.sqrt(self.embed_dim)

            if pad_mask is not None:
                mask_2d = pad_mask[:, None] * pad_mask[None, :]
                attn_scores = attn_scores + (~mask_2d).astype(float) * (-1e9)

            attn_weights = softmax(attn_scores)
            attn_output = attn_weights @ V

            x_attn = h + attn_output
            x_ln1 = layer_norm(x_attn)
            h_attn_norm = x_ln1 * gamma_attn + beta_attn

            ff_hidden = h_attn_norm @ W_ff1 + b_ff1
            ff_act = gelu(ff_hidden)
            ff_out = ff_act @ W_ff2 + b_ff2

            x_ff = h_attn_norm + ff_out
            x_ln2 = layer_norm(x_ff)
            h = x_ln2 * gamma_ff + beta_ff

        if pad_mask is not None:
            mask_exp = pad_mask[:, None].astype(float)
            x_masked = h * mask_exp
            valid_count = max(np.sum(pad_mask), 1)
            pooled = (x_masked.sum(axis=0) / valid_count).reshape(1, -1)
        else:
            pooled = h.mean(axis=0).reshape(1, -1)

        logits = pooled @ self.W_cls + self.b_cls
        out = softmax(logits)[0]
        return out

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def get_params(self):
        """Return a dict of all model parameters.

        For ``num_blocks=1`` the block parameters use the original flat
        key names (``W_q``, ``W_k``, …) so that previously-saved models
        can still be loaded.  For ``num_blocks>1`` indexed keys are used
        (``W_q_0``, ``W_q_1``, …).
        """
        p = {
            'vocab_size': self.vocab_size, 'seq_len': self.seq_len,
            'embed_dim': self.embed_dim, 'num_classes': self.num_classes,
            'ff_dim': self.ff_dim, 'num_blocks': self.num_blocks,
            'embedding': self.embedding.copy(),
            'W_cls': self.W_cls.copy(), 'b_cls': self.b_cls.copy(),
        }
        if self.num_blocks == 1:
            # Backward-compatible flat keys
            p['W_q'] = self._W_q[0].copy()
            p['W_k'] = self._W_k[0].copy()
            p['W_v'] = self._W_v[0].copy()
            p['gamma_attn'] = self._gamma_attn[0].copy()
            p['beta_attn'] = self._beta_attn[0].copy()
            p['gamma_ff'] = self._gamma_ff[0].copy()
            p['beta_ff'] = self._beta_ff[0].copy()
            p['W_ff1'] = self._W_ff1[0].copy()
            p['b_ff1'] = self._b_ff1[0].copy()
            p['W_ff2'] = self._W_ff2[0].copy()
            p['b_ff2'] = self._b_ff2[0].copy()
        else:
            # Indexed keys for multi-block
            for i in range(self.num_blocks):
                p[f'W_q_{i}'] = self._W_q[i].copy()
                p[f'W_k_{i}'] = self._W_k[i].copy()
                p[f'W_v_{i}'] = self._W_v[i].copy()
                p[f'gamma_attn_{i}'] = self._gamma_attn[i].copy()
                p[f'beta_attn_{i}'] = self._beta_attn[i].copy()
                p[f'gamma_ff_{i}'] = self._gamma_ff[i].copy()
                p[f'beta_ff_{i}'] = self._beta_ff[i].copy()
                p[f'W_ff1_{i}'] = self._W_ff1[i].copy()
                p[f'b_ff1_{i}'] = self._b_ff1[i].copy()
                p[f'W_ff2_{i}'] = self._W_ff2[i].copy()
                p[f'b_ff2_{i}'] = self._b_ff2[i].copy()
        return p

    @classmethod
    def from_params(cls, params):
        """Reconstruct a model from a parameter dict (produced by ``get_params``)."""
        num_blocks = params.get('num_blocks', 1)
        model = cls(params['vocab_size'], params['seq_len'], params['embed_dim'],
                    params['num_classes'], ff_dim=params.get('ff_dim'),
                    num_blocks=num_blocks, seed=0)
        model.embedding = params['embedding']
        model.W_cls = params['W_cls']
        model.b_cls = params['b_cls']

        if num_blocks == 1:
            for key in cls._BLOCK_PARAM_NAMES:
                setattr(model, key, params[key])
            model._W_q[0] = params['W_q']
            model._W_k[0] = params['W_k']
            model._W_v[0] = params['W_v']
            model._gamma_attn[0] = params['gamma_attn']
            model._beta_attn[0] = params['beta_attn']
            model._gamma_ff[0] = params['gamma_ff']
            model._beta_ff[0] = params['beta_ff']
            model._W_ff1[0] = params['W_ff1']
            model._b_ff1[0] = params['b_ff1']
            model._W_ff2[0] = params['W_ff2']
            model._b_ff2[0] = params['b_ff2']
        else:
            for i in range(num_blocks):
                model._W_q[i] = params[f'W_q_{i}']
                model._W_k[i] = params[f'W_k_{i}']
                model._W_v[i] = params[f'W_v_{i}']
                model._gamma_attn[i] = params[f'gamma_attn_{i}']
                model._beta_attn[i] = params[f'beta_attn_{i}']
                model._gamma_ff[i] = params[f'gamma_ff_{i}']
                model._beta_ff[i] = params[f'beta_ff_{i}']
                model._W_ff1[i] = params[f'W_ff1_{i}']
                model._b_ff1[i] = params[f'b_ff1_{i}']
                model._W_ff2[i] = params[f'W_ff2_{i}']
                model._b_ff2[i] = params[f'b_ff2_{i}']
        return model

    def save(self, filename):
        params = self.get_params()
        header, tensors = serialize_model_dict(params)
        save_model(filename, header, tensors)

    @classmethod
    def load(cls, filename):
        from ..ia_format import load_model as _lm
        header, tensors = _lm(filename)
        d = dict(header)
        for k in header.get('_tensors', []):
            d[k] = tensors[k]
        d.pop('_tensors', None)
        return cls.from_params(d)


# ==================================================================
# Flax : bloc transformer + réseau complet (entraînement)
# ==================================================================

def _flax_layer_norm(x, eps=1e-8):
    """Layer-norm Flax SANS échelle/biais appris (voir note de module)."""
    mean = jnp.mean(x, axis=-1, keepdims=True)
    var = jnp.var(x, axis=-1, keepdims=True)
    return (x - mean) / jnp.sqrt(var + eps)


class _SLMBlock(nn.Module):
    """Un bloc transformer : self-attention (sans biais/projection de sortie,
    comme l'ancienne implémentation) + FFN(GELU), connexions résiduelles et
    layer-norm. Opère sur une séquence SANS dimension de batch
    (seq_len, embed_dim) — le batch est géré par jax.vmap (voir plus bas)."""
    embed_dim: int
    ff_dim: int

    @nn.compact
    def __call__(self, x, mask=None):
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
        if mask is not None:
            mask_2d = mask[:, None] * mask[None, :]
            attn_scores = attn_scores + (1.0 - mask_2d.astype(x.dtype)) * (-1e9)
        attn_weights = jax.nn.softmax(attn_scores, axis=-1)
        attn_output = attn_weights @ V
        x = x + attn_output
        x = _flax_layer_norm(x)

        ff1 = jax.nn.gelu(x @ W_ff1 + b_ff1, approximate=True)
        ff2 = ff1 @ W_ff2 + b_ff2
        x = x + ff2
        x = _flax_layer_norm(x)
        return x


class _SLMNet(nn.Module):
    """Réseau complet : embedding + num_blocks blocs + pooling (moyen, ou
    masqué si ``use_mask``) + tête de classification. Opère sur UNE séquence
    de tokens (seq_len,) — vmap-ée sur l'axe batch par ``_train_slm_core``."""
    vocab_size: int
    embed_dim: int
    ff_dim: int
    num_classes: int
    num_blocks: int
    use_mask: bool

    @nn.compact
    def __call__(self, tokens, mask=None):
        init = nn.initializers.xavier_uniform()
        embedding = self.param('embedding', init, (self.vocab_size, self.embed_dim))
        x = embedding[tokens]
        for b in range(self.num_blocks):
            block = _SLMBlock(self.embed_dim, self.ff_dim, name=f'block_{b}')
            x = block(x, mask) if self.use_mask else block(x)

        if self.use_mask:
            mask_f = mask[:, None].astype(x.dtype)
            pooled = jnp.sum(x * mask_f, axis=0, keepdims=True) / jnp.maximum(jnp.sum(mask), 1.0)
        else:
            pooled = jnp.mean(x, axis=0, keepdims=True)

        W_cls = self.param('W_cls', init, (self.embed_dim, self.num_classes))
        b_cls = self.param('b_cls', nn.initializers.zeros, (1, self.num_classes))
        return pooled @ W_cls + b_cls


def _extract_slm_params(params, num_blocks):
    """Convertit le pytree Flax {'block_i': {...}, 'W_cls', 'b_cls', 'embedding'}
    vers le format historique (clés à plat pour num_blocks==1, clés indexées
    pour num_blocks>1). gamma_attn/beta_attn/gamma_ff/beta_ff sont écrits avec
    leurs valeurs constantes d'origine (jamais appris, voir note de module)."""
    embedding = np.asarray(params['embedding'])
    embed_dim = embedding.shape[1]
    d = {'embedding': embedding}

    def _block_dict(blk):
        return {
            'W_q': np.asarray(blk['W_q']), 'W_k': np.asarray(blk['W_k']),
            'W_v': np.asarray(blk['W_v']),
            'gamma_attn': np.ones((1, embed_dim)), 'beta_attn': np.zeros((1, embed_dim)),
            'gamma_ff': np.ones((1, embed_dim)), 'beta_ff': np.zeros((1, embed_dim)),
            'W_ff1': np.asarray(blk['W_ff1']), 'b_ff1': np.asarray(blk['b_ff1']),
            'W_ff2': np.asarray(blk['W_ff2']), 'b_ff2': np.asarray(blk['b_ff2']),
        }

    if num_blocks == 1:
        d.update(_block_dict(params['block_0']))
    else:
        for i in range(num_blocks):
            bd = _block_dict(params[f'block_{i}'])
            for k, v in bd.items():
                d[f'{k}_{i}'] = v

    d['W_cls'] = np.asarray(params['W_cls'])
    d['b_cls'] = np.asarray(params['b_cls'])
    return d


def _clip_slm_weights(params, num_blocks):
    """Clippe les matrices de poids (W_q/W_k/W_v/W_ff1/W_ff2/W_cls) à [-1, 1]
    après chaque mise à jour — comme l'ancien moteur manuel. Les biais et
    l'embedding ne sont jamais clippés."""
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


# ==================================================================
# Données par défaut
# ==================================================================

DEFAULT_VOCAB = {
    _gtfr("IA/train/slm.py:509:5"): 0, _gtfr("IA/train/slm.py:509:19"): 1, _gtfr("IA/train/slm.py:509:28"): 2, _gtfr("IA/train/slm.py:509:39"): 3, _gtfr("IA/train/slm.py:509:51"): 4,
    _gtfr("IA/train/slm.py:510:5"): 5, _gtfr("IA/train/slm.py:510:18"): 6, _gtfr("IA/train/slm.py:510:31"): 7, _gtfr("IA/train/slm.py:510:42"): 8, "PAD": 9,
}

DEFAULT_NEXT_WORD_EXAMPLES = [
    ([0, 1, 2, 9], 3),   # "bonjour le chat" -> "mange"
    ([1, 2, 3, 9], 4),   # "le chat mange" -> "la"
    ([2, 3, 4, 9], 5),   # "chat mange la" -> "souris"
    ([3, 4, 5, 9], 6),   # "mange la souris" -> "petite"
    ([4, 5, 6, 9], 7),   # "la souris petite" -> "dort"
    ([5, 6, 7, 9], 8),   # "souris petite dort" -> "sur"
    ([6, 7, 8, 9], 1),   # "petite dort sur" -> "le"
    ([7, 8, 1, 9], 2),   # "dort sur le" -> "chat"
    ([8, 1, 2, 9], 3),   # "sur le chat" -> "mange"
    ([0, 1, 2, 3], 9),   # "bonjour le chat mange" -> PAD
    ([1, 2, 3, 4], 9),   # "le chat mange la" -> PAD
    ([2, 3, 4, 5], 9),   # "chat mange la souris" -> PAD
    ([3, 4, 5, 6], 9),   # "mange la souris petite" -> PAD
    ([4, 5, 6, 7], 9),   # "la souris petite dort" -> PAD
    ([0, 1, 6, 9], 2),   # "bonjour le petite" -> "chat"
    ([0, 2, 8, 9], 5),   # "bonjour chat sur" -> "souris"
    ([1, 4, 2, 9], 3),   # "le la chat" -> "mange"
    ([4, 6, 5, 9], 7),   # "la petite souris" -> "dort"
    ([2, 6, 7, 9], 8),   # "chat petite dort" -> "sur"
    ([1, 2, 7, 9], 3),   # "le chat dort" -> "mange"
]

EMOTION_CLASSES = [_gtfr("IA/train/slm.py:536:20"), _gtfr("IA/train/slm.py:536:28"), _gtfr("IA/train/slm.py:536:41"), _gtfr("IA/train/slm.py:536:51"), _gtfr("IA/train/slm.py:536:59"), _gtfr("IA/train/slm.py:536:71")]
EMOTION_SENTENCES = [
    _gtfr("IA/train/slm.py:538:5"),
    _gtfr("IA/train/slm.py:539:5"),
    _gtfr("IA/train/slm.py:540:5"),
    _gtfr("IA/train/slm.py:541:5"),
    _gtfr("IA/train/slm.py:542:5"),
    _gtfr("IA/train/slm.py:543:5"),
    _gtfr("IA/train/slm.py:544:5"),
    _gtfr("IA/train/slm.py:545:5"),
    _gtfr("IA/train/slm.py:546:5"),
    _gtfr("IA/train/slm.py:547:5"),
    _gtfr("IA/train/slm.py:548:5"),
    _gtfr("IA/train/slm.py:549:5"),
    _gtfr("IA/train/slm.py:550:5"),
    _gtfr("IA/train/slm.py:551:5"),
    _gtfr("IA/train/slm.py:552:5"),
    _gtfr("IA/train/slm.py:553:5"),
    _gtfr("IA/train/slm.py:554:5"),
    _gtfr("IA/train/slm.py:555:5"),
    _gtfr("IA/train/slm.py:556:5"),
    _gtfr("IA/train/slm.py:557:5"),
    _gtfr("IA/train/slm.py:558:5"),
    _gtfr("IA/train/slm.py:559:5"),
    _gtfr("IA/train/slm.py:560:5"),
    _gtfr("IA/train/slm.py:561:5"),
    _gtfr("IA/train/slm.py:562:5"),
    _gtfr("IA/train/slm.py:563:5"),
    _gtfr("IA/train/slm.py:564:5"),
    _gtfr("IA/train/slm.py:565:5"),
    _gtfr("IA/train/slm.py:566:5"),
    _gtfr("IA/train/slm.py:567:5"),
    _gtfr("IA/train/slm.py:568:5"),
    _gtfr("IA/train/slm.py:569:5"),
    _gtfr("IA/train/slm.py:570:5"),
    _gtfr("IA/train/slm.py:571:5"),
    _gtfr("IA/train/slm.py:572:5"),
    _gtfr("IA/train/slm.py:573:5"),
]
EMOTION_LABELS = [0]*6 + [1]*6 + [2]*6 + [3]*6 + [4]*6 + [5]*6

MOOD_CLASSES = [_gtfr("IA/train/slm.py:577:17"), _gtfr("IA/train/slm.py:577:27"), _gtfr("IA/train/slm.py:577:37"), _gtfr("IA/train/slm.py:577:50"), _gtfr("IA/train/slm.py:577:59"), _gtfr("IA/train/slm.py:577:70"), _gtfr("IA/train/slm.py:577:81"), _gtfr("IA/train/slm.py:577:92")]
MOOD_SENTENCES = [
    _gtfr("IA/train/slm.py:579:5"),
    _gtfr("IA/train/slm.py:580:5"),
    _gtfr("IA/train/slm.py:581:5"),
    _gtfr("IA/train/slm.py:582:5"),
    _gtfr("IA/train/slm.py:583:5"),
    _gtfr("IA/train/slm.py:584:5"),
    _gtfr("IA/train/slm.py:585:5"),
    _gtfr("IA/train/slm.py:586:5"),
    _gtfr("IA/train/slm.py:587:5"),
    _gtfr("IA/train/slm.py:588:5"),
    _gtfr("IA/train/slm.py:589:5"),
    _gtfr("IA/train/slm.py:590:5"),
    _gtfr("IA/train/slm.py:591:5"),
    _gtfr("IA/train/slm.py:592:5"),
    _gtfr("IA/train/slm.py:593:5"),
    _gtfr("IA/train/slm.py:594:5"),
    _gtfr("IA/train/slm.py:595:5"),
    _gtfr("IA/train/slm.py:596:5"),
    _gtfr("IA/train/slm.py:597:5"),
    _gtfr("IA/train/slm.py:598:5"),
    _gtfr("IA/train/slm.py:599:5"),
    _gtfr("IA/train/slm.py:600:5"),
    _gtfr("IA/train/slm.py:601:5"),
    _gtfr("IA/train/slm.py:602:5"),
    _gtfr("IA/train/slm.py:603:5"),
    _gtfr("IA/train/slm.py:604:5"),
    _gtfr("IA/train/slm.py:605:5"),
    _gtfr("IA/train/slm.py:606:5"),
    _gtfr("IA/train/slm.py:607:5"),
    _gtfr("IA/train/slm.py:608:5"),
    _gtfr("IA/train/slm.py:609:5"),
    _gtfr("IA/train/slm.py:610:5"),
    _gtfr("IA/train/slm.py:611:5"),
    _gtfr("IA/train/slm.py:612:5"),
    _gtfr("IA/train/slm.py:613:5"),
    _gtfr("IA/train/slm.py:614:5"),
    _gtfr("IA/train/slm.py:615:5"),
    _gtfr("IA/train/slm.py:616:5"),
    _gtfr("IA/train/slm.py:617:5"),
    _gtfr("IA/train/slm.py:618:5"),
]
MOOD_LABELS = [0]*5 + [1]*5 + [2]*5 + [3]*5 + [4]*5 + [5]*5 + [6]*5 + [7]*5

STATEMENT_CLASSES = [_gtfr("IA/train/slm.py:622:22"), _gtfr("IA/train/slm.py:622:34"), _gtfr("IA/train/slm.py:622:49"), _gtfr("IA/train/slm.py:622:58"), _gtfr("IA/train/slm.py:622:69")]
STATEMENT_SENTENCES = [
    _gtfr("IA/train/slm.py:624:5"),
    _gtfr("IA/train/slm.py:625:5"),
    _gtfr("IA/train/slm.py:626:5"),
    _gtfr("IA/train/slm.py:627:5"),
    _gtfr("IA/train/slm.py:628:5"),
    _gtfr("IA/train/slm.py:629:5"),
    _gtfr("IA/train/slm.py:630:5"),
    _gtfr("IA/train/slm.py:631:5"),
    _gtfr("IA/train/slm.py:632:5"),
    _gtfr("IA/train/slm.py:633:5"),
    _gtfr("IA/train/slm.py:634:5"),
    _gtfr("IA/train/slm.py:635:5"),
    _gtfr("IA/train/slm.py:636:5"),
    _gtfr("IA/train/slm.py:637:5"),
    _gtfr("IA/train/slm.py:638:5"),
    _gtfr("IA/train/slm.py:639:5"),
    _gtfr("IA/train/slm.py:640:5"),
    _gtfr("IA/train/slm.py:641:5"),
    _gtfr("IA/train/slm.py:642:5"),
    _gtfr("IA/train/slm.py:643:5"),
    _gtfr("IA/train/slm.py:644:5"),
    _gtfr("IA/train/slm.py:645:5"),
    _gtfr("IA/train/slm.py:646:5"),
    _gtfr("IA/train/slm.py:647:5"),
    _gtfr("IA/train/slm.py:648:5"),
    _gtfr("IA/train/slm.py:649:5"),
    _gtfr("IA/train/slm.py:650:5"),
    _gtfr("IA/train/slm.py:651:5"),
    _gtfr("IA/train/slm.py:652:5"),
    _gtfr("IA/train/slm.py:653:5"),
    _gtfr("IA/train/slm.py:654:5"),
    _gtfr("IA/train/slm.py:655:5"),
    _gtfr("IA/train/slm.py:656:5"),
    _gtfr("IA/train/slm.py:657:5"),
    _gtfr("IA/train/slm.py:658:5"),
    _gtfr("IA/train/slm.py:659:5"),
    _gtfr("IA/train/slm.py:660:5"),
    _gtfr("IA/train/slm.py:661:5"),
    _gtfr("IA/train/slm.py:662:5"),
    _gtfr("IA/train/slm.py:663:5"),
    _gtfr("IA/train/slm.py:664:5"),
    _gtfr("IA/train/slm.py:665:5"),
    _gtfr("IA/train/slm.py:666:5"),
    _gtfr("IA/train/slm.py:667:5"),
    _gtfr("IA/train/slm.py:668:5"),
    _gtfr("IA/train/slm.py:669:5"),
    _gtfr("IA/train/slm.py:670:5"),
    _gtfr("IA/train/slm.py:671:5"),
]
STATEMENT_LABELS = [0]*10 + [1]*10 + [2]*10 + [3]*10 + [4]*10

SENTIMENT_CLASSES = [_gtfr("IA/train/slm.py:675:22"), _gtfr("IA/train/slm.py:675:33"), _gtfr("IA/train/slm.py:675:44")]
SENTIMENT_SENTENCES = [
    _gtfr("IA/train/slm.py:677:5"),
    _gtfr("IA/train/slm.py:678:5"),
    _gtfr("IA/train/slm.py:679:5"),
    _gtfr("IA/train/slm.py:680:5"),
    _gtfr("IA/train/slm.py:681:5"),
    _gtfr("IA/train/slm.py:682:5"),
    _gtfr("IA/train/slm.py:683:5"),
    _gtfr("IA/train/slm.py:684:5"),
    _gtfr("IA/train/slm.py:685:5"),
    _gtfr("IA/train/slm.py:686:5"),
    _gtfr("IA/train/slm.py:687:5"),
    _gtfr("IA/train/slm.py:688:5"),
    _gtfr("IA/train/slm.py:689:5"),
    _gtfr("IA/train/slm.py:690:5"),
    _gtfr("IA/train/slm.py:691:5"),
    _gtfr("IA/train/slm.py:692:5"),
    _gtfr("IA/train/slm.py:693:5"),
    _gtfr("IA/train/slm.py:694:5"),
    _gtfr("IA/train/slm.py:695:5"),
    _gtfr("IA/train/slm.py:696:5"),
    _gtfr("IA/train/slm.py:697:5"),
    _gtfr("IA/train/slm.py:698:5"),
    _gtfr("IA/train/slm.py:699:5"),
    _gtfr("IA/train/slm.py:700:5"),
    _gtfr("IA/train/slm.py:701:5"),
    _gtfr("IA/train/slm.py:702:5"),
    _gtfr("IA/train/slm.py:703:5"),
    _gtfr("IA/train/slm.py:704:5"),
    _gtfr("IA/train/slm.py:705:5"),
    _gtfr("IA/train/slm.py:706:5"),
]
SENTIMENT_LABELS = [0]*10 + [1]*10 + [2]*10


# ==================================================================
# Fonctions d'entrainement SLM
# ==================================================================

def _train_slm_core(vocab_size, seq_len, embed_dim, ff_dim, num_classes, num_blocks,
                    X_train, y_train, pad_masks, epochs, lr, save_path, class_names,
                    seed=42, preprocessor_params=None, extra_meta=None, on_epoch=None):
    """Boucle d'entrainement Flax/JAX commune pour tous les modeles SLM de
    classification (remplace l'ancienne boucle manuelle échantillon par
    échantillon — voir note de module)."""
    use_mask = pad_masks is not None

    model_def = _SLMNet(vocab_size=vocab_size, embed_dim=embed_dim, ff_dim=ff_dim,
                        num_classes=num_classes, num_blocks=num_blocks, use_mask=use_mask)
    key = jax.random.PRNGKey(seed)
    if use_mask:
        params = model_def.init(key, jnp.zeros((seq_len,), dtype=jnp.int32),
                                jnp.ones((seq_len,), dtype=jnp.bool_))['params']
    else:
        params = model_def.init(key, jnp.zeros((seq_len,), dtype=jnp.int32))['params']

    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    X_j = jnp.asarray(X_train, dtype=jnp.int32)
    y_j = jnp.asarray(y_train, dtype=jnp.int32)
    mask_j = jnp.asarray(pad_masks, dtype=jnp.bool_) if use_mask else None

    def _apply_batch(p, Xb, maskb):
        if use_mask:
            return jax.vmap(lambda toks, m: model_def.apply({'params': p}, toks, m))(Xb, maskb)
        return jax.vmap(lambda toks: model_def.apply({'params': p}, toks))(Xb)

    def _loss_fn(p, Xb, yb, maskb):
        logits = _apply_batch(p, Xb, maskb).reshape(-1, num_classes)
        log_probs = jax.nn.log_softmax(logits)
        return -jnp.mean(jnp.take_along_axis(log_probs, yb[:, None], axis=1))

    grad_fn = jax.value_and_grad(_loss_fn)

    @jax.jit
    def train_step(p, opt_st, Xb, yb, maskb):
        loss, grads = grad_fn(p, Xb, yb, maskb)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        p = _clip_slm_weights(p, num_blocks)
        return p, opt_st, loss

    def _live_model_dict(current_params):
        """Construit un dict modèle depuis l'état courant (pour checkpoint)."""
        d = {
            'network_params': _extract_slm_params(current_params, num_blocks),
            'class_names': class_names,
            'seq_len': seq_len,
            'embed_dim': embed_dim,
            'ff_dim': ff_dim,
            'num_classes': num_classes,
            'vocab_size': vocab_size,
            'num_blocks': num_blocks,
        }
        if preprocessor_params is not None:
            d['preprocessor_params'] = preprocessor_params
        if extra_meta is not None:
            d.update(extra_meta)
        return d

    best_loss = float('inf')
    best_params = params
    history = []

    for epoch in range(epochs):
        params, opt_state, loss = train_step(params, opt_state, X_j, y_j, mask_j)
        avg_loss = float(loss)
        history.append(avg_loss)

        if avg_loss < best_loss:
            best_loss = avg_loss
            best_params = jax.tree_util.tree_map(lambda a: a, params)

        if epoch % 100 == 0:
            logger.info("Epoch %d, Loss: %.6f", epoch, avg_loss)

        if on_epoch is not None:
            stop = on_epoch(epoch, {'loss': float(avg_loss)}, _live_model_dict(params))
            if stop:
                logger.info(_gt("rg7.msg.001"), epoch)
                break

        if avg_loss < 0.01:
            logger.info("Convergence a l'epoch %d", epoch)
            break

    # Évaluation avec les meilleurs paramètres
    logits = _apply_batch(best_params, X_j, mask_j).reshape(-1, num_classes)
    preds = np.asarray(jnp.argmax(logits, axis=-1))
    accuracy = float((preds == np.asarray(y_j)).mean() * 100)

    network_params = _extract_slm_params(best_params, num_blocks)
    model_dict = {
        'network_params': network_params,
        'class_names': class_names,
        'accuracy': accuracy,
        'seq_len': seq_len,
        'embed_dim': embed_dim,
        'ff_dim': ff_dim,
        'num_classes': num_classes,
        'vocab_size': vocab_size,
        'num_blocks': num_blocks,
    }
    if preprocessor_params is not None:
        model_dict['preprocessor_params'] = preprocessor_params
    if extra_meta is not None:
        model_dict.update(extra_meta)

    header, tensors = serialize_model_dict(model_dict)
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.052"), save_path, accuracy)

    return {'model': model_dict, 'save_path': save_path, 'accuracy': accuracy, 'history': history}


def train_slm_next_word(vocab=None, seq_len=4, embed_dim=16, ff_dim=32,
                        lr=0.01, epochs=1000, save_path=None, seed=42,
                        num_blocks=1, on_epoch=None):
    """
    Entraîne un SLM pour la prédiction du mot suivant.

    Args:
        vocab: Dictionnaire mot -> index (défaut : 10 mots français).
        seq_len: Longueur des séquences d'entrée.
        embed_dim: Dimension d'embedding.
        ff_dim: Dimension du feed-forward.
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        num_blocks: Nombre de blocs transformer (défaut 1, backward compatible).
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: Modèle et métriques.
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"slm_next_word{MODEL_EXTENSION}")

    if vocab is None:
        vocab = DEFAULT_VOCAB

    vocab_size = len(vocab)
    pad_idx = vocab.get("PAD", vocab_size - 1)

    examples = DEFAULT_NEXT_WORD_EXAMPLES
    X_train = np.array([np.array(e[0]) for e in examples])
    y_train = np.array([e[1] for e in examples])
    pad_masks = np.array([[t != pad_idx for t in e[0]] for e in examples])

    return _train_slm_core(
        vocab_size, seq_len, embed_dim, ff_dim, vocab_size, num_blocks,
        X_train, y_train, pad_masks, epochs, lr, save_path, list(vocab.keys()),
        seed=seed, extra_meta={'vocab': vocab}, on_epoch=on_epoch,
    )


# ==================================================================
# Classification de texte generique — englobe emotion/humeur/type de
# phrase/sentiment, qui partagent tous le meme pipeline (TextPreprocessor
# + _train_slm_core), seuls les classes/phrases par defaut changent. `tache`
# selectionne ce jeu de defauts ; `sentences`/`labels` permet de les
# remplacer par un vrai dataset, du moment que chaque valeur de `labels`
# est un indice valide dans les classes de la tache choisie.
# ==================================================================
SLM_TACHES = {
    "emotion": (EMOTION_CLASSES, EMOTION_SENTENCES, EMOTION_LABELS),
    "humeur": (MOOD_CLASSES, MOOD_SENTENCES, MOOD_LABELS),
    "type_phrase": (STATEMENT_CLASSES, STATEMENT_SENTENCES, STATEMENT_LABELS),
    "sentiment": (SENTIMENT_CLASSES, SENTIMENT_SENTENCES, SENTIMENT_LABELS),
}


def train_slm_classifier(tache="sentiment", sentences=None, labels=None,
                         seq_len=10, embed_dim=16, ff_dim=32, lr=0.01,
                         epochs=500, save_path=None, seed=42, num_blocks=1,
                         on_epoch=None):
    """
    Entraîne un SLM de classification de texte générique.

    Remplace les anciennes fonctions dédiées (train_slm_emotion,
    train_slm_mood, train_slm_statement, train_slm_sentiment) : ces 4
    tâches partagent exactement le même pipeline d'entraînement, seul
    l'ensemble de classes et les phrases par défaut changent selon `tache`.

    Args:
        tache: 'emotion' (6 classes), 'humeur' (8 classes), 'type_phrase'
            (5 classes) ou 'sentiment' (3 classes, défaut).
        sentences: Liste de phrases. Défaut : phrases françaises intégrées
            pour la tâche choisie.
        labels: Liste d'indices de classe, alignée sur `sentences` (index
            dans la liste de classes de la tâche choisie).
        seq_len: Longueur des séquences.
        embed_dim: Dimension d'embedding.
        ff_dim: Dimension du feed-forward.
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        num_blocks: Nombre de blocs transformer (défaut 1, backward compatible).

    Returns:
        dict: Modèle et métriques.
    """
    if tache not in SLM_TACHES:
        raise ValueError(
            f"tache inconnue : {tache!r}. Valeurs possibles : {list(SLM_TACHES)}."
        )
    classes, sentences_defaut, labels_defaut = SLM_TACHES[tache]

    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"slm_{tache}{MODEL_EXTENSION}")

    if sentences is None or labels is None:
        sentences = sentences_defaut
        labels = labels_defaut

    num_classes = len(classes)
    preprocessor = TextPreprocessor()
    preprocessor.fit(sentences)

    X_train = np.array([preprocessor.encode(s, seq_len) for s in sentences])
    y_train = np.array(labels)

    return _train_slm_core(
        preprocessor.vocab_size, seq_len, embed_dim, ff_dim, num_classes, num_blocks,
        X_train, y_train, None, epochs, lr, save_path, classes,
        seed=seed, preprocessor_params=preprocessor.get_params(), on_epoch=on_epoch,
    )


# ── Wrappers de compatibilite (API publique historique) ────────────
# Conserves pour ne pas casser du code important IA.train_slm_xxx
# directement ; deleguent tous a train_slm_classifier.

def train_slm_emotion(sentences=None, labels=None, seq_len=10, embed_dim=16,
                      ff_dim=32, lr=0.01, epochs=500, save_path=None, seed=42,
                      num_blocks=1, on_epoch=None):
    """Alias de train_slm_classifier(tache="emotion", ...)."""
    return train_slm_classifier(
        tache="emotion", sentences=sentences, labels=labels, seq_len=seq_len,
        embed_dim=embed_dim, ff_dim=ff_dim, lr=lr, epochs=epochs,
        save_path=save_path, seed=seed, num_blocks=num_blocks, on_epoch=on_epoch,
    )


def train_slm_mood(sentences=None, labels=None, seq_len=10, embed_dim=16,
                   ff_dim=32, lr=0.01, epochs=500, save_path=None, seed=42,
                   num_blocks=1, on_epoch=None):
    """Alias de train_slm_classifier(tache="humeur", ...)."""
    return train_slm_classifier(
        tache="humeur", sentences=sentences, labels=labels, seq_len=seq_len,
        embed_dim=embed_dim, ff_dim=ff_dim, lr=lr, epochs=epochs,
        save_path=save_path, seed=seed, num_blocks=num_blocks, on_epoch=on_epoch,
    )


def train_slm_statement(sentences=None, labels=None, seq_len=10, embed_dim=16,
                        ff_dim=32, lr=0.01, epochs=500, save_path=None, seed=42,
                        num_blocks=1, on_epoch=None):
    """
    Entraîne un SLM pour la classification de type de phrase.

    5 classes : question, affirmation, ordre, conseil, exclamation.

    Args:
        sentences: Liste de phrases (défaut : 50 phrases françaises).
        labels: Liste d'indices de classe.
        seq_len: Longueur des séquences.
        embed_dim: Dimension d'embedding.
        ff_dim: Dimension du feed-forward.
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        num_blocks: Nombre de blocs transformer (défaut 1, backward compatible).
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: Modèle et métriques.
    """
    return train_slm_classifier(
        tache="type_phrase", sentences=sentences, labels=labels, seq_len=seq_len,
        embed_dim=embed_dim, ff_dim=ff_dim, lr=lr, epochs=epochs,
        save_path=save_path, seed=seed, num_blocks=num_blocks, on_epoch=on_epoch,
    )


def train_slm_sentiment(sentences=None, labels=None, seq_len=10, embed_dim=16,
                        ff_dim=32, lr=0.01, epochs=500, save_path=None, seed=42,
                        num_blocks=1, on_epoch=None):
    """
    Entraîne un SLM pour l'analyse de sentiment.

    3 classes : positif, négatif, neutre.

    Args:
        sentences: Liste de phrases (défaut : 30 phrases françaises).
        labels: Liste d'indices de classe.
        seq_len: Longueur des séquences.
        embed_dim: Dimension d'embedding.
        ff_dim: Dimension du feed-forward.
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        num_blocks: Nombre de blocs transformer (défaut 1, backward compatible).
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: Modèle et métriques.
    """
    return train_slm_classifier(
        tache="sentiment", sentences=sentences, labels=labels, seq_len=seq_len,
        embed_dim=embed_dim, ff_dim=ff_dim, lr=lr, epochs=epochs,
        save_path=save_path, seed=seed, num_blocks=num_blocks, on_epoch=on_epoch,
    )
