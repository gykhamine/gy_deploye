"""
IA/train/vision.py — Entraînement des modèles de classification perceptionnelle,
moteur Flax/JAX.

Modèles :
  - train_image_classifier : Classification d'images à partir de features extraites.
  - train_speech_classifier : Classification de phonèmes à partir de features audio.

Le forward/backward des deux classifieurs est délégué à Flax (flax.linen) /
JAX (autodiff) + optax (SGD) : plus de rétropropagation manuelle et plus de
dépendance à IA/backend.py (comme cnn.py/rnn.py/transformer.py/gan.py/ldm.py/
mlp.py). Les poids finaux sont extraits vers les mêmes clés qu'avant :
  - train_image_classifier, hidden_layers=None : W1, b1, W2, b2
  - train_image_classifier, hidden_layers=[...] : W_0, b_0, …, W_out, b_out
  - train_speech_classifier, hidden_layers=None : conv_params (sous-dict), W_fc, b_fc
  - train_speech_classifier, hidden_layers=[...] : conv_params (sous-dict),
    W_fc_0, b_fc_0, …, W_fc_out, b_fc_out
donc IA/infer/vision.py et IA/trainer.py (_predict_image_classifier /
_predict_speech_classifier) n'ont besoin d'aucune modification de format —
les fichiers .gy sauvegardés sont strictement identiques.

Les extracteurs de features (SimpleFeatureExtractor, AudioFeatureExtractor)
et TemporalConv1D.forward() ne sont pas des modèles entraînés — ce sont des
calculs déterministes/de prétraitement — ils sont donc réimplémentés en numpy
pur (au lieu de IA/backend.py) mais ne passent pas par Flax/JAX.
"""

import math
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
# Fonctions communes (numpy pur — plus de dépendance à backend.py)
# ==================================================================

def softmax(x):
    x = np.asarray(x, dtype=np.float64)
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / np.sum(e, axis=-1, keepdims=True)


def relu(x):
    return np.maximum(0.0, x)


def xavier_init(shape, seed=42):
    shape = tuple(shape)
    rng = np.random.RandomState(int(seed))
    fan_sum = sum(shape)
    std_dev = np.sqrt(2.0 / fan_sum) if fan_sum else 1.0
    return rng.standard_normal(shape) * std_dev


# ==================================================================
# SimpleFeatureExtractor
# ==================================================================

class SimpleFeatureExtractor:
    """Extraction de features basiques à partir d'images RGB (numpy arrays)."""

    @staticmethod
    def extract_color_histogram(image, bins=16):
        """Histogramme RGB normalisé. image: (H, W, 3). Retourne 48 features."""
        features = []
        for c in range(min(image.shape[-1], 3)):
            channel = image[:, :, c].ravel()
            hist, _ = np.histogram(channel, bins=bins, range=(0, 256))
            hist = hist.astype(float) / (hist.sum() + 1e-8)
            features.append(hist)
        return np.concatenate(features)

    @staticmethod
    def extract_gradient(image):
        """Magnitude de gradient moyenne (Sobel simplifié). Retourne 1 feature."""
        gray = np.mean(image.astype(float), axis=2)
        gx = np.diff(gray, axis=1)
        gy = np.diff(gray, axis=0)
        min_h = min(gx.shape[0], gy.shape[0])
        min_w = min(gx.shape[1], gy.shape[1])
        mag = np.sqrt(gx[:min_h, :min_w] ** 2 + gy[:min_h, :min_w] ** 2)
        return np.array([np.mean(mag)])

    @staticmethod
    def extract_lbp(image):
        """Texture LBP simplifiée (histogramme 16 bins). Retourne 16 features."""
        gray = np.mean(image.astype(float), axis=2)
        h, w = gray.shape
        lbp = np.zeros((h, w))
        for i in range(1, h - 1):
            for j in range(1, w - 1):
                center = gray[i, j]
                code = 0
                neighbors = [
                    gray[i - 1, j - 1], gray[i - 1, j], gray[i - 1, j + 1],
                    gray[i, j + 1], gray[i + 1, j + 1], gray[i + 1, j],
                    gray[i + 1, j - 1], gray[i, j - 1],
                ]
                for k, n in enumerate(neighbors):
                    if n >= center:
                        code |= (1 << k)
                lbp[i, j] = code
        hist, _ = np.histogram(lbp, bins=16, range=(0, 256))
        hist = hist.astype(float) / (hist.sum() + 1e-8)
        return hist

    @staticmethod
    def extract_edges(image):
        """Détection de bords par différence simple sur grille 4x4. Retourne 16 features."""
        gray = np.mean(image.astype(float), axis=2)
        edges_h = np.abs(np.diff(gray, axis=0))
        edges_v = np.abs(np.diff(gray, axis=1))
        eh, ew = edges_h.shape
        evh, evw = edges_v.shape
        rh, rw = min(eh, evh), min(ew, evw)
        features = []
        for i in range(4):
            for j in range(4):
                hs = i * rh // 4
                he = (i + 1) * rh // 4
                ws = j * rw // 4
                we = (j + 1) * rw // 4
                region_h = edges_h[hs:he, :rw]
                region_v = edges_v[:rh, ws:we]
                features.append(np.mean(region_h) + np.mean(region_v))
        return np.array(features)

    @staticmethod
    def extract(image, target_dim=128):
        """Combine toutes les features et pad/tronque à target_dim."""
        color = SimpleFeatureExtractor.extract_color_histogram(image)
        grad = SimpleFeatureExtractor.extract_gradient(image)
        lbp = SimpleFeatureExtractor.extract_lbp(image)
        edges = SimpleFeatureExtractor.extract_edges(image)
        combined = np.concatenate([color, grad, lbp, edges])
        if len(combined) < target_dim:
            combined = np.pad(combined, (0, target_dim - len(combined)))
        else:
            combined = combined[:target_dim]
        return combined


# ==================================================================
# AudioFeatureExtractor
# ==================================================================

class AudioFeatureExtractor:
    """Extraction de features audio MFCC-like à partir de signaux 1D (numpy arrays)."""

    @staticmethod
    def extract_mfcc_features(audio_signal, num_mfcc=13):
        """MFCC simplifié. Retourne (n_frames, num_mfcc)."""
        frame_length = 256
        hop_length = 128
        n_fft = 256
        n_mels = 20
        signal = audio_signal.astype(float)
        sig_len = len(signal)

        if sig_len < frame_length:
            signal = np.pad(signal, (0, frame_length - sig_len))
            sig_len = frame_length

        n_frames = max(1, (sig_len - frame_length) // hop_length + 1)
        window = 0.5 * (1 - np.cos(2 * np.pi * np.arange(frame_length) / frame_length))

        # Filtre mel
        mel_low = 0.0
        mel_high = 2595.0 * np.log10(1.0 + 4000.0 / 700.0)
        mel_points = np.linspace(mel_low, mel_high, n_mels + 2)
        hz_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1)
        fft_bins = np.floor((n_fft + 1) * hz_points / 4000.0).astype(int)
        fft_bins = np.clip(fft_bins, 0, n_fft // 2)

        features = []
        for i in range(n_frames):
            start = i * hop_length
            end = start + frame_length
            frame = signal[start:end] * window

            spectrum = np.abs(np.fft.rfft(frame, n=n_fft))
            spectrum = spectrum[:n_fft // 2 + 1]

            # Énergies mel
            mel_energies = np.zeros(n_mels)
            for m in range(n_mels):
                left = fft_bins[m]
                center = fft_bins[m + 1]
                right = fft_bins[m + 2]
                for k in range(left, center):
                    if k < len(spectrum) and center > left:
                        mel_energies[m] += spectrum[k] * (k - left) / (center - left + 1e-8)
                for k in range(center, right):
                    if k < len(spectrum) and right > center:
                        mel_energies[m] += spectrum[k] * (right - k) / (right - center + 1e-8)

            log_mel = np.log(mel_energies + 1e-8)

            # DCT-II simplifié
            mfcc = np.zeros(num_mfcc)
            for k in range(num_mfcc):
                for n in range(n_mels):
                    mfcc[k] += log_mel[n] * np.cos(np.pi * k * (2 * n + 1) / (2 * n_mels))
            features.append(mfcc)

        return np.array(features)

    @staticmethod
    def extract_delta(features):
        """Delta de premier ordre. Retourne tableau de même forme."""
        if len(features) < 3:
            return np.zeros_like(features)
        delta = np.zeros_like(features)
        for t in range(1, len(features) - 1):
            delta[t] = 0.5 * (features[t + 1] - features[t - 1])
        delta[0] = delta[1]
        delta[-1] = delta[-2]
        return delta

    @staticmethod
    def extract(audio, sr=None):
        """Combine MFCC statiques + delta, moyenne temporelle. Retourne (26,)."""
        mfcc = AudioFeatureExtractor.extract_mfcc_features(audio, num_mfcc=13)
        delta = AudioFeatureExtractor.extract_delta(mfcc)
        combined = np.concatenate([mfcc, delta], axis=-1)
        return np.mean(combined, axis=0)


# ==================================================================
# TemporalConv1D
# ==================================================================
# forward() reste en numpy pur (utilisé uniquement pour l'évaluation finale
# après entraînement Flax/JAX, voir train_speech_classifier ci-dessous —
# jamais dans la boucle de gradient). backward() est retirée (code mort,
# la rétropropagation manuelle est remplacée par autodiff JAX + optax).

def _np_conv1d_forward(x, W, b, kernel_size, dilation):
    """x: (in_channels, seq_len). Retourne (out_channels, out_len) — convolution
    "valid" avec dilation, même sémantique que l'ancien IA/backend.py."""
    out_c, in_c, ks = W.shape
    seq_len = x.shape[1]
    eff_ks = (kernel_size - 1) * dilation + 1
    out_len = max(1, seq_len - eff_ks + 1)
    out = np.zeros((out_c, out_len), dtype=np.float64)
    for k in range(kernel_size):
        idx = np.arange(out_len) + k * dilation
        valid = idx < seq_len
        if not np.any(valid):
            continue
        x_slice = x[:, idx[valid]]
        out[:, valid] += W[:, :, k] @ x_slice
    out += b[:, None]
    return out


def _jax_conv1d_forward(x, W, b, kernel_size, dilation):
    """Équivalent JAX (différentiable) de _np_conv1d_forward, utilisé pendant
    l'entraînement. x: (in_channels, seq_len). W: (out_c, in_c, kernel_size)."""
    lhs = x[None, :, :]
    out = jax.lax.conv_general_dilated(
        lhs, W, window_strides=(1,), padding='VALID',
        rhs_dilation=(dilation,),
        dimension_numbers=('NCH', 'OIH', 'NCH'),
    )[0]
    return out + b[:, None]


class TemporalConv1D:
    """Convolution 1D temporelle avec dilation."""

    def __init__(self, in_channels, out_channels, kernel_size=3, dilation=1, seed=42):
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.dilation = dilation
        self.W = xavier_init((out_channels, in_channels, kernel_size), seed=seed)
        self.b = np.zeros((out_channels,))
        self._cache = None

    def forward(self, x):
        """x: (in_channels, seq_len). Retourne (out_channels, out_seq_len)."""
        self._cache = {'x': x}
        return _np_conv1d_forward(x, self.W, self.b, self.kernel_size, self.dilation)

    def get_params(self):
        return {
            'in_channels': self.in_channels, 'out_channels': self.out_channels,
            'kernel_size': self.kernel_size, 'dilation': self.dilation,
            'W': self.W.copy(), 'b': self.b.copy(),
        }

    @classmethod
    def from_params(cls, params):
        layer = cls(params['in_channels'], params['out_channels'],
                    params['kernel_size'], params['dilation'], seed=0)
        layer.W = params['W']
        layer.b = params['b']
        return layer


# ==================================================================
# train_image_classifier — moteur Flax/JAX
# ==================================================================

class _ImageClassifierNet(nn.Module):
    """Dense(h)->ReLU pour chaque couche cachée, puis Dense(num_classes)
    (logits ; softmax appliqué hors du module, comme l'ancienne implémentation)."""
    hidden_sizes: tuple
    num_classes: int

    @nn.compact
    def __call__(self, x):
        for h in self.hidden_sizes:
            x = nn.Dense(h)(x)
            x = nn.relu(x)
        return nn.Dense(self.num_classes)(x)


def _extract_image_params(params, hidden_layers, n_hidden):
    """Convertit le pytree Flax {'Dense_i': {'kernel','bias'}, ...} vers le
    format historique (W1/b1/W2/b2 ou W_i/b_i + W_out/b_out)."""
    if hidden_layers is None:
        return {
            'W1': np.asarray(params['Dense_0']['kernel']),
            'b1': np.asarray(params['Dense_0']['bias']).reshape(1, -1),
            'W2': np.asarray(params[f'Dense_{n_hidden}']['kernel']),
            'b2': np.asarray(params[f'Dense_{n_hidden}']['bias']).reshape(1, -1),
        }
    d = {}
    for i in range(n_hidden):
        d[f'W_{i}'] = np.asarray(params[f'Dense_{i}']['kernel'])
        d[f'b_{i}'] = np.asarray(params[f'Dense_{i}']['bias']).reshape(1, -1)
    d['W_out'] = np.asarray(params[f'Dense_{n_hidden}']['kernel'])
    d['b_out'] = np.asarray(params[f'Dense_{n_hidden}']['bias']).reshape(1, -1)
    return d


def _clip_image_params(params, n_hidden):
    """Clippe uniquement les matrices de poids (kernel) à [-1, 1] — comme
    l'ancien moteur (les biais ne sont jamais clippés)."""
    new_params = {}
    for i in range(n_hidden + 1):
        key = f'Dense_{i}'
        layer = params[key]
        new_params[key] = {'kernel': jnp.clip(layer['kernel'], -1, 1), 'bias': layer['bias']}
    return new_params


def train_image_classifier(X=None, y=None, num_classes=3, feature_dim=128,
                           hidden_dim=64, lr=0.01, epochs=500,
                           save_path=None, seed=42, hidden_layers=None,
                           on_epoch=None):
    """
    Entraîne un classifieur d'images à partir de features extraites (moteur Flax/JAX).

    Architecture configurable en profondeur :

      - ``hidden_layers=None`` (défaut) : input -> FC1 -> ReLU -> FC2 -> softmax.
        Comportement identique à l'original ; les paramètres sont sauvegardés
        sous les clés ``W1``, ``b1``, ``W2``, ``b2`` (rétrocompatible).

      - ``hidden_layers=[128, 64]`` : input -> FC(128) -> ReLU -> FC(64) -> ReLU
        -> FC(num_classes) -> softmax.  Les paramètres sont sauvegardés sous les
        clés ``W_0``, ``b_0``, ``W_1``, ``b_1``, …, ``W_out``, ``b_out``.

    Args:
        X: Features d'entrée (N, feature_dim).
        y: Labels (N,).
        num_classes: Nombre de classes.
        feature_dim: Dimension des features.
        hidden_dim: Dimension de l'unique couche cachée (utilisé uniquement
            si ``hidden_layers is None``).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        hidden_layers: Liste d'entiers décrivant les dimensions des couches
            cachées successives.  Si ``None``, une seule couche cachée de
            dimension ``hidden_dim`` est utilisée (comportement par défaut).
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: Dictionnaire contenant ``'model'``, ``'save_path'``,
        ``'accuracy'`` et ``'history'``.
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"image_classifier{MODEL_EXTENSION}")

    if X is None or y is None:
        rng = np.random.RandomState(seed)
        n_per_class = 50
        X = rng.standard_normal((num_classes * n_per_class, feature_dim)) * 0.1
        y = np.zeros(num_classes * n_per_class, dtype=int)
        for c in range(num_classes):
            start = c * n_per_class
            end = (c + 1) * n_per_class
            X[start:end, :feature_dim // num_classes] += c * 0.5
            y[start:end] = c

    if hidden_layers is None:
        layer_sizes = [hidden_dim]
    else:
        layer_sizes = list(hidden_layers)
    n_hidden = len(layer_sizes)

    IMAGE_CLASSES = [f'Classe {i}' for i in range(num_classes)]

    X = np.asarray(X, dtype=np.float64)
    y_int = np.asarray(y, dtype=np.int32)

    # --- Modèle Flax + optimiseur --------------------------------------------
    model_def = _ImageClassifierNet(hidden_sizes=tuple(layer_sizes), num_classes=num_classes)
    key = jax.random.PRNGKey(seed)
    params = model_def.init(key, jnp.zeros((1, feature_dim), dtype=jnp.float64))['params']
    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    X_j = jnp.asarray(X)
    y_j = jnp.asarray(y_int)

    def _loss_fn(p, Xb, yb):
        logits = model_def.apply({'params': p}, Xb)
        log_probs = jax.nn.log_softmax(logits)
        return -jnp.mean(jnp.take_along_axis(log_probs, yb[:, None], axis=1))

    grad_fn = jax.value_and_grad(_loss_fn)

    @jax.jit
    def train_step(p, opt_st, Xb, yb):
        loss, grads = grad_fn(p, Xb, yb)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        p = _clip_image_params(p, n_hidden)
        return p, opt_st, loss

    best_loss = float('inf')
    best_params = params
    history = []

    def _live_model_dict(current_params):
        d = _extract_image_params(current_params, hidden_layers, n_hidden)
        d.update({
            'feature_dim': feature_dim, 'hidden_dim': hidden_dim,
            'num_classes': num_classes, 'class_names': IMAGE_CLASSES,
            'hidden_layers': hidden_layers,
        })
        return d

    for epoch in range(epochs):
        params, opt_state, loss = train_step(params, opt_state, X_j, y_j)
        avg_loss = float(loss)
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

        if avg_loss < 0.01:
            logger.info("Convergence a l'epoch %d", epoch)
            break

    # ---- Évaluation ----
    logits = model_def.apply({'params': best_params}, X_j)
    preds = np.asarray(jnp.argmax(logits, axis=-1))
    accuracy = float((preds == y_int).mean() * 100)

    # ---- Construction du dictionnaire modèle ----
    model = _extract_image_params(best_params, hidden_layers, n_hidden)
    model.update({
        'feature_dim': feature_dim, 'hidden_dim': hidden_dim,
        'num_classes': num_classes, 'accuracy': accuracy,
        'hidden_layers': hidden_layers, 'class_names': IMAGE_CLASSES,
    })

    header, tensors = serialize_model_dict(model)
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.056"), save_path, accuracy)

    return {'model': model, 'save_path': save_path, 'accuracy': accuracy, 'history': history}


# ==================================================================
# train_speech_classifier — moteur Flax/JAX
# ==================================================================

SPEECH_CLASSES = ["voyelle", "consonne", "silence"]


def _speech_forward_single(p, xi, hidden_layers, n_fc, kernel_size, dilation):
    """Forward JAX (différentiable) pour un seul échantillon.
    xi: (feature_dim,). Retourne les logits (num_classes,)."""
    x = xi.reshape(1, -1)  # (in_channels=1, feature_dim)
    conv_out = _jax_conv1d_forward(x, p['conv_W'], p['conv_b'], kernel_size, dilation)
    conv_act = jax.nn.relu(conv_out)
    pooled = jnp.mean(conv_act, axis=1).reshape(1, -1)  # (1, hidden_dim)

    if hidden_layers is None:
        z = pooled @ p['W_fc'] + p['b_fc']
    else:
        a = pooled
        for i in range(n_fc):
            a = jax.nn.relu(a @ p[f'W_fc_{i}'] + p[f'b_fc_{i}'])
        z = a @ p['W_fc_out'] + p['b_fc_out']
    return z[0]


def _clip_speech_params(p, hidden_layers, n_fc):
    """Clippe conv_W et les matrices FC (jamais les biais) — comme l'ancien moteur."""
    new_p = dict(p)
    new_p['conv_W'] = jnp.clip(p['conv_W'], -1, 1)
    if hidden_layers is None:
        new_p['W_fc'] = jnp.clip(p['W_fc'], -1, 1)
    else:
        for i in range(n_fc):
            new_p[f'W_fc_{i}'] = jnp.clip(p[f'W_fc_{i}'], -1, 1)
        new_p['W_fc_out'] = jnp.clip(p['W_fc_out'], -1, 1)
    return new_p


def train_speech_classifier(X=None, y=None, num_classes=3, feature_dim=26,
                            hidden_dim=64, lr=0.01, epochs=500,
                            save_path=None, seed=42, hidden_layers=None,
                            on_epoch=None):
    """
    Entraîne un classifieur de phonèmes à partir de features audio (moteur Flax/JAX).

    Architecture configurable en profondeur :

      - ``hidden_layers=None`` (défaut) : TemporalConv1D -> ReLU -> global avg pool
        -> FC -> softmax.  Comportement identique à l'original ; les paramètres FC
        sont sauvegardés sous les clés ``W_fc``, ``b_fc`` (rétrocompatible).

      - ``hidden_layers=[64, 32]`` : TemporalConv1D -> ReLU -> global avg pool
        -> FC(64) -> ReLU -> FC(32) -> ReLU -> FC(num_classes) -> softmax.
        Les paramètres FC sont sauvegardés sous les clés ``W_fc_0``, ``b_fc_0``,
        ``W_fc_1``, ``b_fc_1``, ..., ``W_fc_out``, ``b_fc_out``.

    Args:
        X: Features d'entrée (N, feature_dim).
        y: Labels (N,).
        num_classes: Nombre de classes (défaut : voyelle, consonne, silence).
        feature_dim: Dimension des features (défaut : 26).
        hidden_dim: Dimension de la convolution (nombre de filtres).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        hidden_layers: Liste d'entiers décrivant les dimensions des couches FC
            cachées successives après le pooling. Si ``None``, une seule couche FC
            de dimension ``hidden_dim`` vers ``num_classes`` est utilisée.
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: Modèle et métriques.
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"speech_classifier{MODEL_EXTENSION}")

    if X is None or y is None:
        rng = np.random.RandomState(seed)
        n_per_class = 30
        X = rng.standard_normal((num_classes * n_per_class, feature_dim)) * 0.1
        y = np.zeros(num_classes * n_per_class, dtype=int)
        for c in range(num_classes):
            start = c * n_per_class
            end = (c + 1) * n_per_class
            X[start:end, :13] += (c - 1) * 0.3
            y[start:end] = c

    kernel_size, dilation = 3, 1
    conv_out_len = max(1, feature_dim - (kernel_size - 1) * dilation)

    if hidden_layers is None:
        fc_sizes = []
    else:
        fc_sizes = list(hidden_layers)
    n_fc = len(fc_sizes)

    X = np.asarray(X, dtype=np.float64)
    y_int = np.asarray(y, dtype=np.int32)

    # --- Initialisation des paramètres (xavier, comme l'ancien moteur) -------
    key = jax.random.PRNGKey(seed)
    n_keys = 2 + n_fc + 1
    keys = jax.random.split(key, n_keys)

    def _xavier(shape, k):
        fan_sum = sum(shape)
        std = math.sqrt(2.0 / fan_sum) if fan_sum else 1.0
        return jax.random.normal(k, shape, dtype=jnp.float64) * std

    params = {
        'conv_W': _xavier((hidden_dim, 1, kernel_size), keys[0]),
        'conv_b': jnp.zeros((hidden_dim,), dtype=jnp.float64),
    }
    prev_dim = hidden_dim
    for i, size in enumerate(fc_sizes):
        params[f'W_fc_{i}'] = _xavier((prev_dim, size), keys[2 + i])
        params[f'b_fc_{i}'] = jnp.zeros((1, size), dtype=jnp.float64)
        prev_dim = size
    if hidden_layers is None:
        params['W_fc'] = _xavier((prev_dim, num_classes), keys[1])
        params['b_fc'] = jnp.zeros((1, num_classes), dtype=jnp.float64)
    else:
        params['W_fc_out'] = _xavier((prev_dim, num_classes), keys[2 + n_fc])
        params['b_fc_out'] = jnp.zeros((1, num_classes), dtype=jnp.float64)

    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    batched_forward = jax.vmap(
        _speech_forward_single, in_axes=(None, 0, None, None, None, None)
    )

    def _loss_fn(p, Xb, yb):
        logits = batched_forward(p, Xb, hidden_layers, n_fc, kernel_size, dilation)
        log_probs = jax.nn.log_softmax(logits)
        return -jnp.mean(jnp.take_along_axis(log_probs, yb[:, None], axis=1))

    grad_fn = jax.value_and_grad(_loss_fn)

    @jax.jit
    def train_step(p, opt_st, Xb, yb):
        loss, grads = grad_fn(p, Xb, yb)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        p = _clip_speech_params(p, hidden_layers, n_fc)
        return p, opt_st, loss

    X_j = jnp.asarray(X)
    y_j = jnp.asarray(y_int)

    best_loss = float('inf')
    best_params = params
    history = []

    def _live_model_dict(current_params):
        conv_params = {
            'in_channels': 1, 'out_channels': hidden_dim,
            'kernel_size': kernel_size, 'dilation': dilation,
            'W': np.asarray(current_params['conv_W']),
            'b': np.asarray(current_params['conv_b']),
        }
        d = {
            'conv_params': conv_params,
            'feature_dim': feature_dim, 'hidden_dim': hidden_dim,
            'num_classes': num_classes, 'class_names': SPEECH_CLASSES,
            'hidden_layers': hidden_layers if hidden_layers is not None else [],
            'num_features': feature_dim,
        }
        if hidden_layers is None:
            d['W_fc'] = np.asarray(current_params['W_fc'])
            d['b_fc'] = np.asarray(current_params['b_fc'])
        else:
            for i in range(n_fc):
                d[f'W_fc_{i}'] = np.asarray(current_params[f'W_fc_{i}'])
                d[f'b_fc_{i}'] = np.asarray(current_params[f'b_fc_{i}'])
            d['W_fc_out'] = np.asarray(current_params['W_fc_out'])
            d['b_fc_out'] = np.asarray(current_params['b_fc_out'])
        return d

    for epoch in range(epochs):
        params, opt_state, loss = train_step(params, opt_state, X_j, y_j)
        avg_loss = float(loss)
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

        if avg_loss < 0.01:
            logger.info("Convergence a l'epoch %d", epoch)
            break

    # ---- Évaluation ----
    logits = batched_forward(best_params, X_j, hidden_layers, n_fc, kernel_size, dilation)
    preds = np.asarray(jnp.argmax(logits, axis=-1))
    accuracy = float((preds == y_int).mean() * 100)

    # ---- Sauvegarde ----
    # Le speech classifier a une structure imbriquée (conv_params sous-dict)
    # qui nécessite un traitement spécifique pour la rétro-compatibilité
    # avec load_speech_classifier(). On ne peut pas utiliser serialize_model_dict
    # ici car conv_params doit rester un sous-dict non aplatit.
    conv_params = {
        'in_channels': 1, 'out_channels': hidden_dim,
        'kernel_size': kernel_size, 'dilation': dilation,
        'W': np.asarray(best_params['conv_W']),
        'b': np.asarray(best_params['conv_b']),
    }
    model = {
        'conv_params': conv_params,
        'feature_dim': feature_dim, 'hidden_dim': hidden_dim,
        'num_classes': num_classes, 'class_names': SPEECH_CLASSES,
        'accuracy': accuracy,
        'hidden_layers': hidden_layers if hidden_layers is not None else [],
        'num_features': feature_dim,
    }
    if hidden_layers is None:
        model['W_fc'] = np.asarray(best_params['W_fc'])
        model['b_fc'] = np.asarray(best_params['b_fc'])
    else:
        for i in range(n_fc):
            model[f'W_fc_{i}'] = np.asarray(best_params[f'W_fc_{i}'])
            model[f'b_fc_{i}'] = np.asarray(best_params[f'b_fc_{i}'])
        model['W_fc_out'] = np.asarray(best_params['W_fc_out'])
        model['b_fc_out'] = np.asarray(best_params['b_fc_out'])

    # Sérialisation manuelle (conv_params reste un sous-dict dans le header)
    tensors = {}
    header = {}
    for k, v in model.items():
        if isinstance(v, np.ndarray):
            tensors[k] = v
        elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], np.ndarray):
            for i, arr in enumerate(v):
                tensors[f'{k}_{i}'] = np.asarray(arr)
            header[k] = [list(a.shape) for a in v]
        else:
            header[k] = v
    header['_tensors'] = list(tensors.keys())
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.057"), save_path, accuracy)

    return {'model': model, 'save_path': save_path, 'accuracy': accuracy, 'history': history}
