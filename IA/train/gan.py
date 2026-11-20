"""
IA/train/gan.py — Entraînement des GAN, moteur Flax/JAX.

Modèles :
  - train_gan_1d : GAN 1D, génère une valeur scalaire (mélange de 2 gaussiennes).
  - train_gan_nd : GAN N-D, génère des vecteurs de dimension DATA_DIM.
  - train_gan_3d : GAN 3D, génère des volumes 4x4x4 (64 voxels).
  - train_gan_rgb : GAN RGB, génère des images 32x32x3 (3072 pixels).

Architecture configurable via generator_layers et discriminator_layers.
Si None, architecture par défaut à 3 couches (comportement identique
à la version originale pour la rétrocompatibilité).

Le forward/backward est délégué à Flax (flax.linen) + optax (SGD) : plus de
rétropropagation manuelle. Les poids finaux du générateur sont extraits du
pytree Flax vers les mêmes clés indexées ('G_W_0'/'G_b_0', …, plus les alias
'G_W1'/'G_b1', … quand l'architecture par défaut est utilisée) qu'avant,
donc IA/infer/gan.py et IA/trainer.py (_predict_gan) n'ont besoin d'aucune
modification — le format .gy sauvegardé est strictement identique. Le
discriminateur, lui, n'a jamais été sauvegardé (générateur seul).
"""

import os
import logging
import random

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
# Réseau générique (générateur ou discriminateur), nombre de couches
# variable : Dense(h)->leaky_relu pour chaque couche cachée, puis
# Dense(n_out)->activation_sortie.
# ==================================================================

class _GANMLP(nn.Module):
    """MLP Flax générique pour G ou D.

    layer_sizes : tuple complète (dim_entrée, h1, h2, …, dim_sortie).
    Seules les tailles à partir de l'index 1 deviennent des couches
    Dense (dim_entrée sert uniquement de référence/documentation).
    output_activation : 'tanh', 'sigmoid' ou None (identité).
    """
    layer_sizes: tuple
    output_activation: str = None

    @nn.compact
    def __call__(self, x):
        sizes = self.layer_sizes[1:]
        n = len(sizes)
        for i, out_size in enumerate(sizes):
            x = nn.Dense(out_size)(x)
            if i < n - 1:
                x = nn.leaky_relu(x, negative_slope=0.01)
            elif self.output_activation == 'tanh':
                x = jnp.tanh(x)
            elif self.output_activation == 'sigmoid':
                x = nn.sigmoid(x)
        return x


def _extract_indexed_params(params, n_layers, prefix):
    """Convertit le pytree Flax {'Dense_i': {'kernel','bias'}, ...} vers le
    format indexé historique ('{prefix}_W_i', '{prefix}_b_i')."""
    d = {}
    for i in range(n_layers):
        layer = params[f'Dense_{i}']
        d[f'{prefix}_W_{i}'] = np.asarray(layer['kernel'])
        d[f'{prefix}_b_{i}'] = np.asarray(layer['bias'])
    return d


# ==================================================================
# Boucle d'entraînement GAN partagée
# ==================================================================

def _train_gan_core(data_dim, latent_dim, hidden_dim, lr, epochs, batch_size,
                    save_path, seed, filename, apply_tanh,
                    real_data_fn, extra_save_keys,
                    generator_layers=None, discriminator_layers=None,
                    on_epoch=None):
    """
    Boucle d'entraînement GAN commune à toutes les variantes (moteur Flax/JAX).

    Le discriminateur est entraîné 2 fois par étape de générateur.
    Objectif : log-loss GAN standard (non-saturating pour G).

    Args:
        data_dim: Dimension des données de sortie.
        latent_dim: Dimension de l'espace latent.
        hidden_dim: Dimension cachée (utilisé uniquement si les
            *layers correspondants sont None).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        batch_size: Taille de batch.
        save_path: Chemin de sauvegarde (None = défaut).
        seed: Graine aléatoire.
        filename: Nom de fichier pour la sauvegarde.
        apply_tanh: Appliquer tanh sur la sortie du générateur.
        real_data_fn: Fonction(batch_size) → données réelles.
        extra_save_keys: Clés supplémentaires à sauvegarder.
        generator_layers: Liste des tailles de couches du générateur,
            p.ex. [latent_dim, 128, 64, data_dim]. Dernier élément =
            dimension de sortie. Si None, utilise l'architecture par
            défaut à 3 couches.
        discriminator_layers: Liste des tailles de couches du
            discriminateur, p.ex. [data_dim, 128, 64, 1]. Dernier
            élément = 1. Si None, utilise l'architecture par défaut
            à 3 couches.
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.
    """
    np.random.seed(seed)
    random.seed(seed)

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"{filename}{MODEL_EXTENSION}")

    # --- Résoudre les architectures de couches ---
    gen_was_none = generator_layers is None
    disc_was_none = discriminator_layers is None

    if generator_layers is None:
        generator_layers = [latent_dim, hidden_dim, hidden_dim, data_dim]
    if discriminator_layers is None:
        discriminator_layers = [data_dim, hidden_dim, hidden_dim, 1]

    n_G = len(generator_layers) - 1   # nombre de couches de poids G
    n_D = len(discriminator_layers) - 1  # nombre de couches de poids D

    # --- Modèles Flax + optimiseurs ---
    G_module = _GANMLP(layer_sizes=tuple(generator_layers),
                        output_activation='tanh' if apply_tanh else None)
    D_module = _GANMLP(layer_sizes=tuple(discriminator_layers),
                        output_activation='sigmoid')

    key = jax.random.PRNGKey(seed)
    key_g, key_d = jax.random.split(key)
    G_params = G_module.init(key_g, jnp.zeros((1, latent_dim), dtype=jnp.float64))['params']
    D_params = D_module.init(key_d, jnp.zeros((1, data_dim), dtype=jnp.float64))['params']

    tx_g = optax.sgd(lr)
    tx_d = optax.sgd(lr)
    opt_g_state = tx_g.init(G_params)
    opt_d_state = tx_d.init(D_params)

    def _d_loss(d_params, g_params, real_data, z):
        D_real = D_module.apply({'params': d_params}, real_data)
        fake_data = jax.lax.stop_gradient(G_module.apply({'params': g_params}, z))
        D_fake = D_module.apply({'params': d_params}, fake_data)
        return (-jnp.mean(jnp.log(D_real + 1e-8))
                - jnp.mean(jnp.log(1.0 - D_fake + 1e-8)))

    def _g_loss(g_params, d_params, z):
        fake_data = G_module.apply({'params': g_params}, z)
        D_fake = D_module.apply({'params': d_params}, fake_data)
        return -jnp.mean(jnp.log(D_fake + 1e-8))

    d_grad_fn = jax.value_and_grad(_d_loss)
    g_grad_fn = jax.value_and_grad(_g_loss)

    @jax.jit
    def d_step(d_params, g_params, opt_st, real_data, z):
        loss, grads = d_grad_fn(d_params, g_params, real_data, z)
        updates, opt_st = tx_d.update(grads, opt_st, d_params)
        d_params = optax.apply_updates(d_params, updates)
        return d_params, opt_st, loss

    @jax.jit
    def g_step(g_params, d_params, opt_st, z):
        loss, grads = g_grad_fn(g_params, d_params, z)
        updates, opt_st = tx_g.update(grads, opt_st, g_params)
        g_params = optax.apply_updates(g_params, updates)
        return g_params, opt_st, loss

    history_D = []
    history_G = []

    log_interval = max(1, epochs // 10)

    def _live_model_dict(current_G_params):
        d = _extract_indexed_params(current_G_params, n_G, 'G')
        if gen_was_none:
            for i in range(n_G):
                d[f'G_W{i + 1}'] = d[f'G_W_{i}']
                d[f'G_b{i + 1}'] = d[f'G_b_{i}']
        d['latent_dim'] = latent_dim
        d['hidden_dim'] = hidden_dim
        if not gen_was_none:
            d['generator_layers'] = generator_layers
        if not disc_was_none:
            d['discriminator_layers'] = discriminator_layers
        d.update(extra_save_keys)
        return d

    for epoch in range(epochs):
        # ---- Entraîner le discriminateur 2 fois ----
        for _ in range(2):
            real_data = jnp.asarray(real_data_fn(batch_size))
            z = jnp.asarray(np.random.randn(batch_size, latent_dim))
            D_params, opt_d_state, D_loss = d_step(
                D_params, G_params, opt_d_state, real_data, z)

        # ---- Entraîner le générateur ----
        z = jnp.asarray(np.random.randn(batch_size, latent_dim))
        G_params, opt_g_state, G_loss = g_step(G_params, D_params, opt_g_state, z)

        D_loss = float(D_loss)
        G_loss = float(G_loss)
        history_D.append(D_loss)
        history_G.append(G_loss)

        if epoch % log_interval == 0:
            logger.info(
                "Epoch %d/%d  D_loss: %.6f  G_loss: %.6f",
                epoch, epochs, D_loss, G_loss,
            )

        if on_epoch is not None:
            metrics = {'loss': G_loss, 'g_loss': G_loss, 'd_loss': D_loss}
            stop = on_epoch(epoch, metrics, _live_model_dict(G_params))
            if stop:
                logger.info(_gt("rg7.msg.001"), epoch)
                break

    # --- Évaluation : précision du discriminateur ---
    n_eval = 200
    real_eval = jnp.asarray(real_data_fn(n_eval))
    z_eval = jnp.asarray(np.random.randn(n_eval, latent_dim))
    fake_eval = G_module.apply({'params': G_params}, z_eval)
    D_real_eval = np.asarray(D_module.apply({'params': D_params}, real_eval))
    D_fake_eval = np.asarray(D_module.apply({'params': D_params}, fake_eval))
    accuracy = float(
        (np.mean(D_real_eval > 0.5) + np.mean(D_fake_eval < 0.5)) / 2.0
    )

    # --- Construction du modèle sauvegardé (générateur seul) ---
    model = _extract_indexed_params(G_params, n_G, 'G')

    # Clés de rétrocompatibilité quand l'architecture par défaut
    # (3 couches) est utilisée
    if gen_was_none:
        for i in range(n_G):
            model[f'G_W{i + 1}'] = model[f'G_W_{i}']
            model[f'G_b{i + 1}'] = model[f'G_b_{i}']

    model['latent_dim'] = latent_dim
    model['hidden_dim'] = hidden_dim

    # Sauvegarder la configuration des couches si non par défaut
    if not gen_was_none:
        model['generator_layers'] = generator_layers
    if not disc_was_none:
        model['discriminator_layers'] = discriminator_layers

    model.update(extra_save_keys)

    header, tensors = serialize_model_dict(model)
    save_model(save_path, header, tensors)

    logger.info(
        _gt("rg7.msg.017"), filename, save_path, accuracy
    )

    history = {'D_loss': history_D, 'G_loss': history_G}

    return {
        'model': model,
        'save_path': save_path,
        'accuracy': accuracy,
        'history': history,
    }


# ==================================================================
# GAN 1D
# ==================================================================

def _real_data_1d(batch_size):
    """Distribution réelle : mélange de 2 gaussiennes N(-2, 0.5) et N(+2, 0.5)."""
    mask = (np.random.rand(batch_size, 1) > 0.5).astype(float)
    samples = (mask * (np.random.randn(batch_size, 1) * 0.5 + 2.0)
               + (1.0 - mask) * (np.random.randn(batch_size, 1) * 0.5 - 2.0))
    return samples


def train_gan_1d(latent_dim=2, hidden_dim=16, lr=0.001, epochs=5000,
                 batch_size=32, save_path=None, seed=42,
                 generator_layers=None, discriminator_layers=None):
    """
    Entraîne un GAN 1D générant une valeur scalaire.

    Données réelles : mélange de 2 gaussiennes (moyennes -2 et +2,
    écart-type 0.5). Pas de tanh sur la sortie du générateur.

    Args:
        latent_dim: Dimension de l'espace latent.
        hidden_dim: Dimension cachée (si *layers est None).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        batch_size: Taille de batch.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        generator_layers: Liste des tailles de couches du générateur.
            Si None, [latent_dim, hidden_dim, hidden_dim, 1].
        discriminator_layers: Liste des tailles de couches du
            discriminateur. Si None, [1, hidden_dim, hidden_dim, 1].

    Returns:
        dict: {'model', 'save_path', 'accuracy', 'history'}
    """
    return _train_gan_core(
        data_dim=1,
        latent_dim=latent_dim,
        hidden_dim=hidden_dim,
        lr=lr,
        epochs=epochs,
        batch_size=batch_size,
        save_path=save_path,
        seed=seed,
        filename="gan_1d",
        apply_tanh=False,
        real_data_fn=_real_data_1d,
        extra_save_keys={},
        generator_layers=generator_layers,
        discriminator_layers=discriminator_layers,
    )


# ==================================================================
# GAN N-D
# ==================================================================

def _real_data_nd(batch_size, data_dim):
    """Distribution réelle N-D : chaque dimension échantillonnée
    indépendamment de N(-2, 0.5) ou N(+2, 0.5)."""
    mask = (np.random.rand(batch_size, data_dim) > 0.5).astype(float)
    samples = (mask * (np.random.randn(batch_size, data_dim) * 0.5 + 2.0)
               + (1.0 - mask) * (np.random.randn(batch_size, data_dim) * 0.5 - 2.0))
    return samples


def train_gan_nd(data_dim=16, latent_dim=16, hidden_dim=64, lr=0.001,
                 epochs=5000, batch_size=32, save_path=None, seed=42,
                 generator_layers=None, discriminator_layers=None,
                 on_epoch=None):
    """
    Entraîne un GAN N-D générant des vecteurs de dimension data_dim.

    Données réelles : mélange de gaussiennes par dimension.
    Tanh sur la sortie du générateur.

    Args:
        data_dim: Dimension des données.
        latent_dim: Dimension de l'espace latent.
        hidden_dim: Dimension cachée (si *layers est None).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        batch_size: Taille de batch.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        generator_layers: Liste des tailles de couches du générateur.
            Si None, [latent_dim, hidden_dim, hidden_dim, data_dim].
        discriminator_layers: Liste des tailles de couches du
            discriminateur. Si None, [data_dim, hidden_dim, hidden_dim, 1].
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement.

    Returns:
        dict: {'model', 'save_path', 'accuracy', 'history'}
    """
    fn = lambda bs: _real_data_nd(bs, data_dim)

    return _train_gan_core(
        data_dim=data_dim,
        latent_dim=latent_dim,
        hidden_dim=hidden_dim,
        lr=lr,
        epochs=epochs,
        batch_size=batch_size,
        save_path=save_path,
        seed=seed,
        filename="gan_nd",
        apply_tanh=True,
        real_data_fn=fn,
        extra_save_keys={'data_dim': data_dim},
        generator_layers=generator_layers,
        discriminator_layers=discriminator_layers,
        on_epoch=on_epoch,
    )


# ==================================================================
# GAN 3D
# ==================================================================

def _mgrid3d(size):
    """Grille de coordonnées 3D (x, y, z), équivalent pur numpy de
    l'ancien backend.get_core().mgrid3d — plus de dépendance à IA/backend.py."""
    size = int(size)
    z_idx, y_idx, x_idx = np.indices((size, size, size))
    return (x_idx.astype(np.float64), y_idx.astype(np.float64), z_idx.astype(np.float64))


def _real_data_3d(batch_size, volume_size):
    """Distribution réelle 3D : sphères ou cubes avec bruit."""
    data_dim = volume_size ** 3
    gx, gy, gz = _mgrid3d(volume_size)
    coords = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])
    center = (volume_size - 1) / 2.0
    distances = np.sqrt(np.sum((coords - center) ** 2, axis=1))

    batches = np.zeros((batch_size, data_dim))
    for i in range(batch_size):
        shape_type = random.randint(0, 1)
        noise = np.random.randn(data_dim) * 0.1
        if shape_type == 0:
            # Sphère
            radius = random.uniform(0.8, 1.8)
            volumes = (distances <= radius).astype(float)
        else:
            # Cube
            half_size = random.uniform(0.5, 1.5)
            inside = np.all(np.abs(coords - center) <= half_size, axis=1)
            volumes = inside.astype(float)
        batches[i] = volumes + noise

    # Normaliser vers [-1, 1]
    batches = np.clip(batches * 2.0 - 1.0, -1.0, 1.0)
    return batches


def train_gan_3d(volume_size=4, latent_dim=16, hidden_dim=128, lr=0.0005,
                 epochs=5000, batch_size=32, save_path=None, seed=42,
                 generator_layers=None, discriminator_layers=None):
    """
    Entraîne un GAN 3D générant des volumes 4x4x4 (64 voxels).

    Données réelles : sphères ou cubes avec bruit.
    Tanh sur la sortie du générateur.

    Args:
        volume_size: Taille du volume (volume_size^3 voxels).
        latent_dim: Dimension de l'espace latent.
        hidden_dim: Dimension cachée (si *layers est None).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        batch_size: Taille de batch.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        generator_layers: Liste des tailles de couches du générateur.
            Si None, [latent_dim, hidden_dim, hidden_dim, data_dim].
        discriminator_layers: Liste des tailles de couches du
            discriminateur. Si None, [data_dim, hidden_dim, hidden_dim, 1].

    Returns:
        dict: {'model', 'save_path', 'accuracy', 'history'}
    """
    data_dim = volume_size ** 3
    fn = lambda bs: _real_data_3d(bs, volume_size)

    extra = {
        'VOLUME_H': volume_size,
        'VOLUME_W': volume_size,
        'VOLUME_D': volume_size,
        'volume_shape': (volume_size, volume_size, volume_size),
    }

    return _train_gan_core(
        data_dim=data_dim,
        latent_dim=latent_dim,
        hidden_dim=hidden_dim,
        lr=lr,
        epochs=epochs,
        batch_size=batch_size,
        save_path=save_path,
        seed=seed,
        filename="gan_3d",
        apply_tanh=True,
        real_data_fn=fn,
        extra_save_keys=extra,
        generator_layers=generator_layers,
        discriminator_layers=discriminator_layers,
    )


# ==================================================================
# GAN RGB
# ==================================================================

def _real_data_rgb(batch_size, image_size, channels):
    """Distribution réelle RGB : motifs de couleur synthétiques."""
    flat_dim = image_size * image_size * channels
    batches = np.zeros((batch_size, flat_dim))

    for i in range(batch_size):
        pattern = random.randint(0, 4)
        img = np.zeros((image_size, image_size, channels))

        if pattern == 0:
            # Dégradé horizontal
            for c in range(channels):
                start = random.uniform(-0.8, 0.8)
                end = random.uniform(-0.8, 0.8)
                ls = np.linspace(start, end, image_size)
                img[:, :, c] = ls.reshape(-1, 1)

        elif pattern == 1:
            # Formes géométriques colorées
            img = np.random.uniform(-0.3, 0.3, (image_size, image_size, channels))
            margin = max(1, image_size // 8)
            cx = random.randint(margin, max(margin + 1, image_size - margin) - 1)
            cy = random.randint(margin, max(margin + 1, image_size - margin) - 1)
            r = random.randint(1, max(2, image_size // 2) - 1)
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if dy * dy + dx * dx <= r * r:
                        py, px = cy + dy, cx + dx
                        if 0 <= py < image_size and 0 <= px < image_size:
                            img[py, px] = np.random.uniform(0.5, 1.0, channels)

        elif pattern == 2:
            # Rayures verticales
            width = random.randint(2, 4)
            color_a = np.random.uniform(-0.8, 0.8, channels)
            color_b = np.random.uniform(-0.8, 0.8, channels)
            for j in range(image_size):
                c = color_a if (j // width) % 2 == 0 else color_b
                img[:, j, :] = c

        elif pattern == 3:
            # Bruit coloré
            img = np.random.uniform(-1.0, 1.0, (image_size, image_size, channels))

        elif pattern == 4:
            # Motif mixte : deux moitiés avec couleurs différentes
            half = image_size // 2
            img[:half, :, :] = np.random.uniform(0.3, 1.0, (half, image_size, channels))
            img[half:, :, :] = np.random.uniform(-1.0, -0.3, (image_size - half, image_size, channels))

        batches[i] = img.flatten()

    batches = np.clip(batches, -1.0, 1.0)
    return batches


def train_gan_rgb(image_size=32, channels=3, latent_dim=200, hidden_dim=512,
                  lr=0.00002, epochs=10, batch_size=16, save_path=None, seed=42,
                  generator_layers=None, discriminator_layers=None):
    """
    Entraîne un GAN RGB générant des images 32x32x3 (3072 pixels).

    Données réelles : motifs de couleur synthétiques (dégradés, formes,
    rayures, bruit, mixte). Tanh sur la sortie du générateur.

    Args:
        image_size: Taille des images (carrées).
        channels: Nombre de canaux de couleur.
        latent_dim: Dimension de l'espace latent.
        hidden_dim: Dimension cachée (si *layers est None).
        lr: Taux d'apprentissage.
        epochs: Nombre d'époques.
        batch_size: Taille de batch.
        save_path: Chemin de sauvegarde.
        seed: Graine aléatoire.
        generator_layers: Liste des tailles de couches du générateur.
            Si None, [latent_dim, hidden_dim, hidden_dim, data_dim].
        discriminator_layers: Liste des tailles de couches du
            discriminateur. Si None, [data_dim, hidden_dim, hidden_dim, 1].

    Returns:
        dict: {'model', 'save_path', 'accuracy', 'history'}
    """
    data_dim = image_size * image_size * channels
    fn = lambda bs: _real_data_rgb(bs, image_size, channels)

    extra = {
        'IMAGE_H': image_size,
        'IMAGE_W': image_size,
        'CHANNELS': channels,
        'image_shape': (image_size, image_size, channels),
    }

    return _train_gan_core(
        data_dim=data_dim,
        latent_dim=latent_dim,
        hidden_dim=hidden_dim,
        lr=lr,
        epochs=epochs,
        batch_size=batch_size,
        save_path=save_path,
        seed=seed,
        filename="gan_rgb",
        apply_tanh=True,
        real_data_fn=fn,
        extra_save_keys=extra,
        generator_layers=generator_layers,
        discriminator_layers=discriminator_layers,
    )
