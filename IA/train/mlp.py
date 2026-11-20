"""
IA/train/mlp.py — Entraînement MLP (Multi-Layer Perceptron), moteur Flax/JAX.

Architecture : couches denses empilées avec ReLU + sortie sigmoid/softmax.
Conçu pour données tabulaires : le MLP choisit seul entre classification et
régression selon la cible (IA/tache_cible.py : classes -> classification,
valeur continue -> régression ; sortie linéaire, perte MSE, cible
standardisée dont moyenne et écart-type sont mémorisés dans le modèle).

Le forward/backward est délégué à Flax (flax.linen) + optax (SGD) : plus de
rétropropagation manuelle. Les poids finaux sont extraits du pytree Flax
vers les mêmes clés ('weights': liste de W, 'biases': liste de b) qu'avant,
donc IA/infer/mlp.py et IA/trainer.py (_predict_mlp) n'ont besoin d'aucune
modification — le format .gy sauvegardé est strictement identique.

Usage démo :
    from IA.train.mlp import train_mlp
    result = train_mlp(epochs=100)

Usage réel :
    result = train_mlp(X=X, y=y, hidden_sizes=[64, 32], epochs=500, lr=0.01)
"""

import os
import logging

import numpy as np
import jax
import jax.numpy as jnp
import optax
import flax.linen as nn

from ..config import get_config, MODELS_DIR, MODEL_EXTENSION, ensure_directories
from ..ia_format import save_model, serialize_model_dict
from ..tache_cible import TACHES, detecter_tache
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)


class _MLPFlax(nn.Module):
    """MLP Flax : Dense(h)->ReLU pour chaque couche cachée, puis Dense(n_out)
    (les logits ; l'activation de sortie sigmoid/softmax est appliquée hors
    du module, comme dans l'ancienne implémentation numpy)."""
    hidden_sizes: tuple
    n_out: int

    @nn.compact
    def __call__(self, x):
        for h in self.hidden_sizes:
            x = nn.Dense(h, param_dtype=jnp.float64)(x)
            x = nn.relu(x)
        return nn.Dense(self.n_out, param_dtype=jnp.float64)(x)


def _extract_weights_biases(params, n_layers):
    """Convertit le pytree Flax {'Dense_0': {'kernel','bias'}, ...} en deux
    listes (weights, biases) dans l'ordre des couches — même convention que
    l'ancienne implémentation numpy (W : (fan_in, fan_out), b : (1, fan_out))."""
    weights, biases = [], []
    for i in range(n_layers):
        layer = params[f'Dense_{i}']
        weights.append(np.asarray(layer['kernel']))
        biases.append(np.asarray(layer['bias']).reshape(1, -1))
    return weights, biases


def train_mlp(X=None, y=None, hidden_sizes=None, lr=0.01, epochs=500,
              save_path=None, seed=42, batch_size=256, dropout=0.0,
              on_epoch=None, init_model=None, task=None):
    """
    Entraîne un MLP pour classification/régression tabulaire (moteur Flax/JAX).

    Args:
        X: array (n_samples, n_features). None = données démo.
        y: array (n_samples, 1). None = données démo.
           Si les valeurs sont entières > 1, mode multi-classes activé.
        hidden_sizes: liste de tailles de couches cachées.
                      Défaut : [64, 32]
        lr: taux d'apprentissage.
        epochs: nombre d'époques.
        save_path: chemin de sauvegarde (.gy).
        seed: graine aléatoire.
        batch_size: taille de mini-batch (256 par défaut).
        dropout: taux de dropout entre couches cachées (0 = désactivé).
        init_model: dict {'weights': {...}, 'config': {...}} d'un MLP déjà
                    entraîné (fine-tuning) : l'architecture et les classes
                    sont reprises du modèle, seuls les poids repartent de lui.
        task: 'classification', 'regression' ou None/'auto' (défaut : choisi
              d'après la cible, voir IA/tache_cible.py). Un fine-tuning garde
              la tâche du modèle d'origine.
        on_epoch: callback optionnel appelé après chaque époque avec
                  (epoch, metrics_dict, model_dict). Si le callback
                  retourne True, l'entraînement s'arrête immédiatement
                  (utilisé par le Trainer pour brancher EarlyStopping,
                  ModelCheckpoint et la progression live).

    Returns:
        dict : model, save_path, accuracy, history
        (régression : `accuracy` = R² × 100 borné à 0 ; `r2` et `rmse` sont
        aussi dans model)
    """
    ensure_directories()

    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_mlp{MODEL_EXTENSION}")

    if hidden_sizes is None:
        hidden_sizes = [64, 32]
    hidden_sizes = list(hidden_sizes)

    # --- Données démo --------------------------------------------------------
    if X is None or y is None:
        rng = np.random.default_rng(seed)
        X = rng.standard_normal((200, 4))
        y = (X[:, 0] + X[:, 1] > 0).astype(np.float64).reshape(-1, 1)
        logger.info(_gt("rg7.msg.019"), X.shape, y.shape)

    X = np.array(X, dtype=np.float64)
    y = np.array(y, dtype=np.float64)
    if y.ndim == 1:
        y = y.reshape(-1, 1)

    n_samples, n_features = X.shape

    # --- Détecter la tâche (classification ou régression) --------------------
    init_cfg = (init_model or {}).get('config')
    init_w = (init_model or {}).get('weights')
    if init_cfg is not None:
        task = init_cfg.get('task', 'classification')   # anciens .gy = classification
    elif task not in TACHES:
        task = detecter_tache(y)
    regression = (task == 'regression')

    if regression:
        unique_classes = np.array([], dtype=int)
        n_classes = 0
        multiclass = False
        n_out = y.shape[1]
        if init_cfg is not None:
            if int(init_cfg['n_features']) != n_features:
                raise ValueError(
                    _gtf("rg7.msg.020", v1=init_cfg['n_features'], v2=n_features))
            if int(init_cfg['n_out']) != n_out:
                raise ValueError(
                    _gtf("rg7.msg.021", v1=init_cfg['n_out'], v2=n_out))
            hidden_sizes = [int(h) for h in init_cfg['hidden_sizes']]
            # Normalisation du modèle d'origine : jamais recalculée sur le delta.
            y_mean = np.asarray(init_cfg['y_mean'], dtype=np.float64).reshape(1, -1)
            y_std = np.asarray(init_cfg['y_std'], dtype=np.float64).reshape(1, -1)
        else:
            y_mean = y.mean(axis=0, keepdims=True)
            y_std = y.std(axis=0, keepdims=True)
            y_std = np.where(y_std < 1e-12, 1.0, y_std)
        y_enc = (y - y_mean) / y_std
        logger.info(_gt("rg7.msg.022"), n_out)
    else:
        y_mean = y_std = None
        unique_classes = np.unique(y).astype(int)
        n_classes = len(unique_classes)
        multiclass = n_classes > 2

        if init_cfg is not None:
            # Fine-tuning : on garde l'architecture et l'encodage des classes du
            # modèle d'origine, sinon les poids appris n'auraient plus de sens.
            if int(init_cfg['n_features']) != n_features:
                raise ValueError(
                    _gtf("rg7.msg.020", v1=init_cfg['n_features'], v2=n_features))
            hidden_sizes = [int(h) for h in init_cfg['hidden_sizes']]
            unique_classes = np.array(init_cfg['unique_classes'], dtype=int)
            n_classes = len(unique_classes)
            multiclass = bool(init_cfg.get('multiclass', n_classes > 2))
            inconnues = set(np.unique(y).astype(int).tolist()) - set(unique_classes.tolist())
            if inconnues:
                raise ValueError(
                    _gtf("rg7.msg.023", v1=sorted(inconnues)))

        if multiclass:
            n_out = n_classes
            class_map = {v: i for i, v in enumerate(unique_classes)}
            y_enc = np.zeros((n_samples, n_out), dtype=np.float64)
            for i, val in enumerate(y.flatten()):
                y_enc[i, class_map[int(val)]] = 1.0
            logger.info("Mode multi-classes : %d classes", n_classes)
        else:
            n_out = 1
            y_enc = y
            logger.info("Mode binaire")

    layer_sizes = [n_features] + hidden_sizes + [n_out]
    n_layers = len(layer_sizes) - 1

    # --- Modèle Flax + optimiseur --------------------------------------------
    model_def = _MLPFlax(hidden_sizes=tuple(hidden_sizes), n_out=n_out)
    key = jax.random.PRNGKey(seed)
    params = model_def.init(key, jnp.zeros((1, n_features), dtype=jnp.float64))['params']
    if init_w is not None:
        params = {f'Dense_{i}': {
            'kernel': jnp.asarray(np.asarray(init_w[f'W_{i}'], dtype=np.float64)),
            'bias': jnp.asarray(np.asarray(init_w[f'b_{i}'], dtype=np.float64).reshape(-1)),
        } for i in range(n_layers)}
    tx = optax.sgd(lr)
    opt_state = tx.init(params)

    # Dropout appliqué en amont sur les mini-batchs (inverted-dropout, voir
    # boucle d'entraînement ci-dessous) plutôt que via nn.Dropout, pour
    # garder ce module Flax minimal et son pytree de params strictement
    # identique à l'ancienne convention (une seule couche 'Dense_i' par
    # couche dense, pas de sous-état supplémentaire).
    def _perte(p, Xb, yb):
        sortie = model_def.apply({'params': p}, Xb)
        if regression:
            return jnp.mean((sortie - yb) ** 2)
        if multiclass:
            return -jnp.mean(jnp.sum(yb * jax.nn.log_softmax(sortie), axis=-1))
        return jnp.mean(optax.sigmoid_binary_cross_entropy(sortie, yb))

    grad_fn = jax.value_and_grad(_perte)

    @jax.jit
    def train_step(p, opt_st, Xb, yb):
        loss, grads = grad_fn(p, Xb, yb)
        updates, opt_st = tx.update(grads, opt_st, p)
        p = optax.apply_updates(p, updates)
        return p, opt_st, loss

    history = []
    rng_np = np.random.default_rng(seed)

    def _live_model_dict(current_params):
        w, b = _extract_weights_biases(current_params, n_layers)
        return {
            'weights': w, 'biases': b,
            'hidden_sizes': hidden_sizes, 'n_features': n_features,
            'n_out': n_out, 'n_classes': n_classes, 'multiclass': multiclass,
            'unique_classes': unique_classes.tolist(), 'layer_sizes': layer_sizes,
            'task': task,
            'y_mean': None if y_mean is None else y_mean.ravel().tolist(),
            'y_std': None if y_std is None else y_std.ravel().tolist(),
        }

    for epoch in range(epochs):
        idx = rng_np.permutation(n_samples)
        X_shuf = X[idx]
        y_shuf = y_enc[idx]

        epoch_loss = 0.0
        n_batches = 0
        for start in range(0, n_samples, batch_size):
            Xb = jnp.asarray(X_shuf[start:start + batch_size])
            yb = jnp.asarray(y_shuf[start:start + batch_size])
            if Xb.shape[0] == 0:
                continue
            # Inverted dropout appliqué sur l'entrée du batch (approximation
            # simple et suffisante pour ce module de démonstration).
            if dropout > 0.0:
                mask_key = jax.random.fold_in(key, epoch * 100000 + start)
                mask = jax.random.bernoulli(mask_key, 1.0 - dropout, Xb.shape)
                Xb = Xb * mask / (1.0 - dropout)
            params, opt_state, loss = train_step(params, opt_state, Xb, yb)
            epoch_loss += float(loss)
            n_batches += 1

        avg_loss = epoch_loss / max(1, n_batches)
        history.append(avg_loss)

        if epoch % max(1, epochs // 10) == 0:
            logger.info("Epoch %d/%d  loss=%.6f", epoch, epochs, avg_loss)

        if on_epoch is not None:
            stop = on_epoch(epoch, {'loss': avg_loss}, _live_model_dict(params))
            if stop:
                logger.info(_gt("rg7.msg.001"), epoch)
                break

    # --- Évaluation ---------------------------------------------------------
    logits = model_def.apply({'params': params}, jnp.asarray(X))
    extra = {}
    if regression:
        pred = np.asarray(logits) * y_std + y_mean
        ss_res = float(np.sum((y - pred) ** 2))
        ss_tot = float(np.sum((y - y.mean(axis=0, keepdims=True)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0
        accuracy = max(0.0, r2) * 100.0
        extra = {'r2': r2, 'rmse': float(np.sqrt(np.mean((y - pred) ** 2)))}
    else:
        a = np.asarray(jax.nn.softmax(logits) if multiclass else jax.nn.sigmoid(logits))

        if multiclass:
            preds = a.argmax(axis=1)
            trues = y.flatten().astype(int)
            trues_mapped = np.array([class_map[int(v)] for v in trues])
            accuracy = (preds == trues_mapped).mean() * 100.0
        else:
            preds = (a > 0.5).astype(int).flatten()
            trues = y.flatten().astype(int)
            accuracy = (preds == trues).mean() * 100.0

    logger.info(_gt("rg7.msg.024"), accuracy)

    # --- Sauvegarde (format .gy inchangé) ------------------------------------
    weights, biases = _extract_weights_biases(params, n_layers)
    model = {
        'weights': weights,
        'biases': biases,
        'hidden_sizes': hidden_sizes,
        'n_features': n_features,
        'n_out': n_out,
        'n_classes': n_classes,
        'multiclass': multiclass,
        'unique_classes': unique_classes.tolist(),
        'layer_sizes': layer_sizes,
        'accuracy': accuracy,
        'task': task,
        'y_mean': None if y_mean is None else y_mean.ravel().tolist(),
        'y_std': None if y_std is None else y_std.ravel().tolist(),
        **extra,
    }

    header, tensors = serialize_model_dict(model)
    save_model(save_path, header, tensors)
    logger.info(_gt("rg7.msg.025"), save_path, accuracy)

    return {
        'model': model,
        'save_path': save_path,
        'accuracy': accuracy,
        'history': history,
    }
