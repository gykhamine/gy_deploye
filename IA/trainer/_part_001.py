from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t
from ._header import *  # noqa

def register_model_type(model_type: str, train_fn: Callable,
                        predict_fn: Callable,
                        extract_weights: Callable,
                        extract_config: Callable,
                        default_save_name: str,
                        default_kwargs: Optional[Dict[str, Any]] = None):
    """Enregistre un type de modèle dans le registre du Trainer."""
    _REGISTRY[model_type] = {
        'train_fn': train_fn,
        'predict_fn': predict_fn,
        'extract_weights': extract_weights,
        'extract_config': extract_config,
        'default_save_name': default_save_name,
        'default_kwargs': default_kwargs or {},
    }
    # Enregistre aussi la predict_fn dans le registre de Model
    register_predict_fn(model_type)(predict_fn)


# ============================================================================
# Fonctions d'inférence pour chaque type de modèle
# ============================================================================
#Ces fonctions reproduisent le forward pass en utilisant les poids stockés
# dans le Model. Elles sont volontairement simples : elles couvrent le cas
# d'usage principal de chaque type (pas tous les cas particuliers).

@register_predict_fn('mlp')
def _predict_mlp(weights, config, X):
    """Inférence MLP : forward pass couches denses."""
    X = np.asarray(X, dtype=np.float64)
    # Reconstruction des listes depuis le dict aplati (format .gy)
    n_layers = config['n_layers']
    W_list = [weights[f'W_{i}'] for i in range(n_layers)]
    b_list = [weights[f'b_{i}'] for i in range(n_layers)]
    multiclass = config.get('multiclass', False)
    regression = config.get('task') == 'regression'
    a = X
    for i in range(n_layers):
        z = a @ W_list[i] + b_list[i]
        if i < n_layers - 1:
            a = np.maximum(0.0, z)  # relu
        else:
            if regression:
                # sortie linéaire, remise à l'échelle d'origine de la cible
                a = z * np.asarray(config['y_std'], dtype=np.float64).reshape(1, -1) \
                    + np.asarray(config['y_mean'], dtype=np.float64).reshape(1, -1)
            elif multiclass:
                e = np.exp(z - z.max(axis=1, keepdims=True))
                a = e / e.sum(axis=1, keepdims=True)  # softmax
            else:
                a = 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))  # sigmoid
    return a


@register_predict_fn('rnn')
def _predict_rnn(weights, config, X):
    """Inférence RNN vanilla (mono ou multi-couche) : forward sur (batch, seq_len, input_size)
    (numpy pur — plus de dépendance à IA/backend.py, comme _predict_cnn/_predict_transformer)."""
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 2:
        X = X[np.newaxis, ...]
    hidden_size = config['hidden_size']
    seq_len = config.get('seq_len', X.shape[1])
    num_layers = config.get('num_layers', 1)
    W_hy = weights['W_hy']; b_y = weights['b_y']
    preds = []
    for i in range(X.shape[0]):
        h_list = [np.zeros((1, hidden_size)) for _ in range(num_layers)]
        for t in range(seq_len):
            x_t = X[i, t].reshape(1, -1)
            layer_input = x_t
            for l in range(num_layers):
                if num_layers == 1:
                    W_xh = weights['W_xh']
                    W_hh = weights['W_hh']
                    b_h = weights['b_h']
                else:
                    W_xh = weights[f'W_xh_{l}']
                    W_hh = weights[f'W_hh_{l}']
                    b_h = weights[f'b_h_{l}']
                h_list[l] = np.tanh(layer_input @ W_xh + h_list[l] @ W_hh + b_h)
                layer_input = h_list[l]
        y_pred = 1.0 / (1.0 + np.exp(-np.clip(h_list[-1] @ W_hy + b_y, -500, 500)))
        preds.append(y_pred[0, 0])
    return np.array(preds).reshape(-1, 1)


def _np_convolve_valid(volume, kernel):
    """Convolution N-D 'valid', pure numpy (remplace l'ancien C.convolve2d /
    C.convolve_nd — CNN ne dépend plus de IA/backend.py)."""
    windows = np.lib.stride_tricks.sliding_window_view(volume, kernel.shape)
    k_axes = tuple(range(volume.ndim, volume.ndim + kernel.ndim))
    return np.tensordot(windows, kernel, axes=(k_axes, tuple(range(kernel.ndim))))


def _np_softmax_2d(x):
    """Softmax numpy pur (remplace C.softmax — Transformer ne dépend plus
    de IA/backend.py)."""
    e = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e / np.sum(e, axis=-1, keepdims=True)


def _np_layer_norm_2d(x, eps=1e-8):
    """Layer-norm numpy pur (remplace C.layer_norm — Transformer ne dépend
    plus de IA/backend.py)."""
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(var + eps)


@register_predict_fn('cnn')
def _predict_cnn(weights, config, X):
    """Inférence CNN 2D/N-D (mono ou multi-couches) : conv + relu + FC + relu."""
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 2:
        X = X[np.newaxis, ...]
    w_fc = weights['w_fc']; b_fc = weights['b_fc']

    # Handle both list format (kernels=[arr, ...]) and indexed format (kernels_0, kernels_1, ...)
    if 'kernels' in weights and isinstance(weights['kernels'], list):
        kernels = weights['kernels']
        biases = weights['biases']
    else:
        num_conv = config.get('num_conv_layers', 1)
        kernels = [weights[f'kernels_{i}'] for i in range(num_conv)]
        biases = [weights[f'biases_{i}'] for i in range(num_conv)]

    preds = []
    for i in range(X.shape[0]):
        img = X[i]
        x = img
        for li in range(len(kernels)):
            conv = _np_convolve_valid(x, kernels[li]) + biases[li]
            x = np.maximum(0.0, conv)
        flat = x.flatten().reshape(1, -1)
        out = np.maximum(0.0, flat @ w_fc + b_fc)
        preds.append(out[0, 0])
    return np.array(preds).reshape(-1, 1)


@register_predict_fn('transformer')
def _predict_transformer(weights, config, X):
    """Inférence Transformer : self-attention complet + FFN + pooling
    (numpy pur — plus de dépendance à IA/backend.py, comme _predict_cnn/
    _predict_gan/_predict_ldm).

    Le moteur ne supportant que le matmul 2D, chaque échantillon du
    batch est traité individuellement pour garantir la compatibilité.
    """
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 1:
        X = X[np.newaxis, ...]

    embed = weights.get('embedding')
    if embed is None:
        return np.zeros((X.shape[0], 1))

    seq_len = X.shape[1]
    num_blocks = config.get('num_blocks', 1)
    batch_size = X.shape[0]

    preds = []
    for sample_idx in range(batch_size):
        # (seq_len, embed_dim) — toujours 2D pour le moteur
        x = embed[X[sample_idx].astype(int)]

        for b_idx in range(num_blocks):
            # Support both flat keys (num_blocks=1) and indexed keys (num_blocks>1)
            if num_blocks == 1:
                wq = weights.get('W_q')
                wk = weights.get('W_k')
                wv = weights.get('W_v')
                gamma_attn = weights.get('gamma_attn')
                beta_attn = weights.get('beta_attn')
                wff1 = weights.get('W_ff1')
                bff1 = weights.get('b_ff1')
                wff2 = weights.get('W_ff2')
                bff2 = weights.get('b_ff2')
                gamma_ff = weights.get('gamma_ff')
                beta_ff = weights.get('beta_ff')
            else:
                # Format réellement sauvegardé pour num_blocks>1 (voir
                # train/transformer.py::_extract_transformer_params et
                # trainer.py::_extract_transformer) : des LISTES indexées
                # par bloc ('W_q_list', ...), pas des clés à plat suffixées
                # 'W_q_{i}' (qui n'ont jamais existé dans le format .gy —
                # bug préexistant, non introduit par la migration Flax).
                def _at(list_key):
                    lst = weights.get(list_key)
                    return lst[b_idx] if lst is not None and b_idx < len(lst) else None
                wq = _at('W_q_list')
                wk = _at('W_k_list')
                wv = _at('W_v_list')
                gamma_attn = _at('gamma_attn_list')
                beta_attn = _at('beta_attn_list')
                wff1 = _at('W_ff1_list')
                bff1 = _at('b_ff1_list')
                wff2 = _at('W_ff2_list')
                bff2 = _at('b_ff2_list')
                gamma_ff = _at('gamma_ff_list')
                beta_ff = _at('beta_ff_list')

            if wq is None or wk is None or wv is None:
                break

            d = x.shape[-1]
            # Self-attention (2D)
            Q = x @ wq
            K = x @ wk
            V = x @ wv
            attn_scores = (Q @ K.T) / math.sqrt(d)
            attn_weights = _np_softmax_2d(attn_scores)
            attn_output = attn_weights @ V

            # Add & Norm (attention)
            x_attn = x + attn_output
            x_norm = _np_layer_norm_2d(x_attn)
            if gamma_attn is not None and beta_attn is not None:
                x = x_norm * gamma_attn + beta_attn
            else:
                x = x_norm

            # FFN
            if wff1 is not None and wff2 is not None:
                b1 = np.asarray(bff1).flatten().reshape(1, -1) if bff1 is not None else 0
                b2 = np.asarray(bff2).flatten().reshape(1, -1) if bff2 is not None else 0
                ff1 = np.maximum(0.0, x @ wff1 + b1)
                ff2 = ff1 @ wff2 + b2

                # Add & Norm (FFN)
                x_ff = x + ff2
                x_ff_norm = _np_layer_norm_2d(x_ff)
                if gamma_ff is not None and beta_ff is not None:
                    x = x_ff_norm * gamma_ff + beta_ff
                else:
                    x = x_ff_norm

        # Mean pooling -> classify
        pooled = np.mean(x, axis=0).reshape(1, -1)
        W_cls = weights.get('W_cls')
        b_cls = weights.get('b_cls')
        if W_cls is None or b_cls is None:
            preds.append(0)
            continue
        b_cls_r = np.asarray(b_cls).flatten().reshape(1, -1)
        logits = pooled @ W_cls + b_cls_r
        probs = _np_softmax_2d(logits)
        preds.append(int(np.argmax(probs[0])))

    return np.array(preds)


@register_predict_fn('gan')
def _predict_gan(weights, config, X):
    """Inférence GAN : génère des échantillons depuis le générateur.
    X est interprété comme du bruit latent (ou ignoré si None).
    Format indexé uniquement (G_W_0/G_W_1/…).
    """
    latent_dim = weights['G_W_0'].shape[0]
    if X is not None:
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 2 and X.shape[1] == latent_dim:
            z = X
        else:
            z = np.random.randn(1, latent_dim)
    else:
        z = np.random.randn(1, latent_dim)
    # Forward du générateur
    def _bias_row(b, cols):
        b = np.asarray(b).flatten()
        return b.reshape(1, cols)
    li = 0
    while f'G_W_{li}' in weights:
        li += 1
    n_layers = li
    h = z
    for layer_i in range(n_layers):
        W = weights[f'G_W_{layer_i}']
        b = _bias_row(weights[f'G_b_{layer_i}'], W.shape[1])
        z_l = h @ W + b
        if layer_i < n_layers - 1:
            h = np.where(z_l > 0, z_l, 0.01 * z_l)  # leaky_relu
        else:
            out = z_l
    if config.get('apply_tanh', True):
        out = np.tanh(out)
    return out


@register_predict_fn('ldm')
def _predict_ldm(weights, config, X):
    """Inférence LDM : prédit le bruit depuis (x_noisy, class_embedding).
    X est l'ID de classe (ou ignoré).
    Utilise le class_embedding appris et les couches indexées W_0/b_0/…
    (numpy pur — plus de dépendance à IA/backend.py).
    """
    num_classes = config.get('num_classes', 5)
    input_dim = config.get('input_dim', 8)
    class_id = int(X) if X is not None else 0
    # Embedding de classe
    if 'class_embedding' in weights:
        class_emb = np.asarray(weights['class_embedding'])
        c_emb = class_emb[class_id % num_classes].reshape(1, -1)
    else:
        emb_dim = config.get('class_embedding_dim', num_classes)
        c_emb = np.zeros((1, emb_dim))
        c_emb[0, class_id % num_classes] = 1.0
    # Bruit initial
    x = np.random.randn(1, input_dim)
    x_concat = np.concatenate([x, c_emb], axis=1)
    # Couches indexées W_0, b_0, W_1, b_1, …
    li = 0
    while f'W_{li}' in weights:
        li += 1
    n_layers = li
    h = x_concat
    for layer_i in range(n_layers):
        W = weights[f'W_{layer_i}']
        b = np.asarray(weights[f'b_{layer_i}']).flatten().reshape(1, W.shape[1])
        z = np.matmul(h, W) + b
        if layer_i < n_layers - 1:
            h = np.maximum(z, 0.0)
        else:
            h = z
    pred_noise = h
    return pred_noise


@register_predict_fn('slm')
def _predict_slm(weights, config, X):
    """Inférence SLM : prédit la classe d'une séquence.
    X est une séquence d'indices (1D ou 2D).
    (numpy pur — plus de dépendance à IA/backend.py, comme _predict_cnn/
    _predict_transformer/_predict_rnn).
    """
    X = np.asarray(X).astype(int).flatten()
    embed = weights.get('embedding')
    if embed is None:
        return np.array([0])
    num_blocks = config.get('num_blocks', 1)
    seq = embed[X]
    x = seq
    for b_idx in range(num_blocks):
        W_q = weights.get(f'W_q_{b_idx}') if num_blocks > 1 else weights.get('W_q')
        W_k = weights.get(f'W_k_{b_idx}') if num_blocks > 1 else weights.get('W_k')
        W_v = weights.get(f'W_v_{b_idx}') if num_blocks > 1 else weights.get('W_v')
        W_ff1 = weights.get(f'W_ff1_{b_idx}') if num_blocks > 1 else weights.get('W_ff1')
        b_ff1 = weights.get(f'b_ff1_{b_idx}') if num_blocks > 1 else weights.get('b_ff1')
        W_ff2 = weights.get(f'W_ff2_{b_idx}') if num_blocks > 1 else weights.get('W_ff2')
        b_ff2 = weights.get(f'b_ff2_{b_idx}') if num_blocks > 1 else weights.get('b_ff2')
        gamma_attn = weights.get(f'gamma_attn_{b_idx}') if num_blocks > 1 else weights.get('gamma_attn')
        beta_attn = weights.get(f'beta_attn_{b_idx}') if num_blocks > 1 else weights.get('beta_attn')
        gamma_ff = weights.get(f'gamma_ff_{b_idx}') if num_blocks > 1 else weights.get('gamma_ff')
        beta_ff = weights.get(f'beta_ff_{b_idx}') if num_blocks > 1 else weights.get('beta_ff')
        if W_q is not None and W_k is not None and W_v is not None:
            d = x.shape[-1]
            q = x @ W_q; k = x @ W_k; v = x @ W_v
            scores = (q @ k.T) / math.sqrt(d)
            attn = _np_softmax_2d(scores)
            ctx = attn @ v
            x = x + ctx
            x_norm = _np_layer_norm_2d(x)
            if gamma_attn is not None and beta_attn is not None:
                x = x_norm * gamma_attn + beta_attn
            else:
                x = x_norm
        if W_ff1 is not None and W_ff2 is not None:
            b1 = np.asarray(b_ff1).flatten().reshape(1, -1) if b_ff1 is not None else 0
            b2 = np.asarray(b_ff2).flatten().reshape(1, -1) if b_ff2 is not None else 0
            h = np.maximum(0.0, x @ W_ff1 + b1)
            ff_out = h @ W_ff2 + b2
            x = x + ff_out
            x_ff_norm = _np_layer_norm_2d(x)
            if gamma_ff is not None and beta_ff is not None:
                x = x_ff_norm * gamma_ff + beta_ff
            else:
                x = x_ff_norm
    pooled = x.mean(axis=0, keepdims=True)
    W_cls = weights.get('W_cls')
    b_cls = weights.get('b_cls')
    if W_cls is None or b_cls is None:
        return np.array([0])
    b_cls_r = np.asarray(b_cls).flatten().reshape(1, -1)
    logits = pooled @ W_cls + b_cls_r
    probs = _np_softmax_2d(logits)
    return np.array([int(np.argmax(probs[0]))])


@register_predict_fn('image_classifier')
def _predict_image_classifier(weights, config, X):
    """Inférence classifieur d'images : FC variable + softmax.
    X est une image (H, W) ou un batch (N, H, W) ou déjà flatten (N, F).
    Format multi-couches uniquement (W_0/W_1/…/W_out).
    (numpy pur — plus de dépendance à IA/backend.py, comme _predict_cnn/
    _predict_transformer/_predict_rnn/_predict_slm).
    """
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 2:
        X = X[np.newaxis, ...]
    hidden_layers = config.get('hidden_layers')
    if hidden_layers is None:
        hidden_layers = [config.get('hidden_dim', 64)]  # default single layer
    n_hidden = len(hidden_layers)
    input_dim = config.get('feature_dim', weights.get('feature_dim', 128))
    preds = []
    for i in range(X.shape[0]):
        feat = X[i].flatten().reshape(1, -1)
        # Padding/troncature à la dimension d'entrée
        if feat.shape[1] < input_dim:
            feat = np.pad(feat, ((0, 0), (0, input_dim - feat.shape[1])))
        elif feat.shape[1] > input_dim:
            feat = feat[:, :input_dim]
        a = feat
        for j in range(n_hidden):
            z = a @ weights[f'W_{j}'] + weights[f'b_{j}']
            a = np.maximum(0.0, z)
        out = a @ weights['W_out'] + weights['b_out']
        probs = _np_softmax_2d(out)
        preds.append(int(np.argmax(probs[0])))
    return np.array(preds)


@register_predict_fn('speech_classifier')
def _predict_speech_classifier(weights, config, X):
    """Inférence classifieur de parole : conv1d -> relu -> pool -> [FC -> relu]* -> softmax.
    Supporte : avec ou sans convolution, avec ou sans couches FC cachées.
    (numpy pur — plus de dépendance à IA/backend.py, comme _predict_cnn/
    _predict_transformer/_predict_rnn/_predict_slm/_predict_image_classifier)."""
    X = np.asarray(X, dtype=np.float64)
    if X.ndim == 1:
        X = X[np.newaxis, ...]

    conv_W = weights.get('conv_W')
    conv_b = weights.get('conv_b')
    kernel_size = int(config.get('conv_kernel_size', 3))
    dilation = int(config.get('conv_dilation', 1))

    hidden_layers = config.get('hidden_layers')
    n_fc = len(hidden_layers) if hidden_layers else 0

    # Déterminer la dimension d'entrée des couches FC
    if conv_W is not None:
        fc_input_dim = int(conv_W.shape[0])  # conv_out_channels
    elif n_fc > 0:
        first_fc = weights.get('W_fc_0')
        fc_input_dim = int(first_fc.shape[0]) if first_fc is not None else int(config.get('hidden_dim', 64))
    else:
        out_w = weights.get('W_fc_out')
        fc_input_dim = int(out_w.shape[0]) if out_w is not None else int(config.get('hidden_dim', 64))

    preds = []
    for i in range(X.shape[0]):
        x_2d = X[i].reshape(1, -1)
        if conv_W is not None:
            out_channels = int(conv_W.shape[0])
            out_len = max(1, x_2d.shape[1] - (kernel_size - 1) * dilation)
            conv_out = np.zeros((out_channels, out_len))
            for co in range(out_channels):
                for j in range(out_len):
                    total = 0.0
                    for ki in range(kernel_size):
                        idx = j + ki * dilation
                        if idx < x_2d.shape[1]:
                            total += x_2d[0, idx] * conv_W[co, 0, ki]
                    conv_out[co, j] = total + (float(conv_b[co]) if conv_b is not None else 0.0)
            conv_act = np.maximum(0.0, conv_out)
            pooled = conv_act.mean(axis=1).reshape(1, -1)
        else:
            # Padding/troncature à la dimension attendue
            if x_2d.shape[1] < fc_input_dim:
                pooled = np.pad(x_2d, ((0, 0), (0, fc_input_dim - x_2d.shape[1])))
            else:
                pooled = x_2d[:, :fc_input_dim]

        a = pooled
        for j in range(n_fc):
            w_j = weights.get(f'W_fc_{j}')
            b_j = weights.get(f'b_fc_{j}')
            if w_j is None or b_j is None:
                break
            z = np.asarray(a @ w_j + b_j)
            a = np.maximum(0.0, z)

        fc_out_w = weights.get('W_fc_out') if weights.get('W_fc_out') is not None else weights.get('W_fc')
        fc_out_b = weights.get('b_fc_out') if weights.get('b_fc_out') is not None else weights.get('b_fc')
        if fc_out_w is None or fc_out_b is None:
            preds.append(0)
            continue
        z_fc = np.asarray(a @ fc_out_w + fc_out_b)
        out = _np_softmax_2d(z_fc)
        preds.append(int(np.argmax(out[0])))
    return np.array(preds)


# ============================================================================
# Fonctions d'extraction des poids et config depuis les résultats train_xxx
# ============================================================================

def _extract_mlp(result):
    m = result['model']
    # Aplatir les listes de poids/biais en dict individuel pour le format .gy
    w = {}
    for i, (W, b) in enumerate(zip(m['weights'], m['biases'])):
        w[f'W_{i}'] = np.asarray(W, dtype=np.float64)
        w[f'b_{i}'] = np.asarray(b, dtype=np.float64)
    config = {
        'hidden_sizes': m['hidden_sizes'],
        'n_features': m['n_features'],
        'n_out': m['n_out'],
        'n_classes': m['n_classes'],
        'multiclass': m['multiclass'],
        'unique_classes': m['unique_classes'],
        'n_layers': len(m['weights']),
        'task': m.get('task', 'classification'),
    }
    if config['task'] == 'regression':
        config.update(y_mean=m['y_mean'], y_std=m['y_std'],
                      r2=m.get('r2'), rmse=m.get('rmse'))
    return (w, config)

def _extract_rnn(result):
    m = result['model']
    num_layers = m.get('num_layers', 1)
    w = {}
    if num_layers == 1:
        w = {k: m[k] for k in ['W_xh', 'W_hh', 'b_h', 'W_hy', 'b_y']}
    else:
        for l in range(num_layers):
            w[f'W_xh_{l}'] = m[f'W_xh_{l}']
            w[f'W_hh_{l}'] = m[f'W_hh_{l}']
            w[f'b_h_{l}'] = m[f'b_h_{l}']
        w['W_hy'] = m['W_hy']
        w['b_y'] = m['b_y']
    return (w,
            {'hidden_size': m['hidden_size'], 'seq_len': m['seq_len'],
             'input_size': m['input_size'], 'num_layers': num_layers})

def _extract_cnn(result):
    m = result['model']
    # Normalize keys: singular -> plural for backward compat
    if 'kernel' in m and 'kernels' not in m:
        m['kernels'] = m.pop('kernel')
    if 'bias' in m and 'biases' not in m:
        m['biases'] = m.pop('bias')
    w = {'kernels': m['kernels'], 'biases': m['biases'],
         'w_fc': m['w_fc'], 'b_fc': m['b_fc']}
    config = {
        'input_shape': list(m.get('input_shape', m.get('volume_shape', (5, 5)))),
        'kernel_shape': list(m.get('kernel_shape', (3, 3))),
        'dimensions': m.get('dimensions', 2),
        'num_conv_layers': m.get('num_conv_layers', 1),
    }
    # Préserver conv_shape et accuracy pour compatibilité load_cnn2d/load_cnn_nd
    if 'conv_shape' in m:
        config['conv_shape'] = list(m['conv_shape'])
    if 'accuracy' in m:
        config['accuracy'] = m['accuracy']
    if 'volume_shape' in m:
        config['volume_shape'] = list(m['volume_shape'])
    return (w, config)

def _extract_transformer(result):
    m = result['model']
    weights = {}
    num_blocks = m.get('num_blocks', 1)
    if num_blocks == 1:
        for k in ['embedding', 'W_q', 'W_k', 'W_v', 'W_ff1', 'W_ff2',
                  'b_ff1', 'b_ff2', 'W_cls', 'b_cls']:
            if k in m:
                weights[k] = m[k]
    else:
        weights['embedding'] = m['embedding']
        weights['W_q_list'] = m['W_q_list']
        weights['W_k_list'] = m['W_k_list']
        weights['W_v_list'] = m['W_v_list']
        weights['W_ff1_list'] = m['W_ff1_list']
        weights['b_ff1_list'] = m['b_ff1_list']
        weights['W_ff2_list'] = m['W_ff2_list']
        weights['b_ff2_list'] = m['b_ff2_list']
        weights['W_cls'] = m['W_cls']
        weights['b_cls'] = m['b_cls']
        weights['num_blocks'] = num_blocks
    return (weights, {'seq_len': m.get('seq_len', 4),
                      'vocab_size': m.get('vocab_size', 6),
                      'embed_dim': m.get('embed_dim', 8),
                      'num_blocks': num_blocks})

def _extract_gan(result):
    m = result['model']
    weights = {}
    for k, v in m.items():
        if isinstance(v, np.ndarray):
            weights[k] = v
    config = {'latent_dim': m.get('latent_dim', 4),
              'data_dim': m.get('data_dim', 1),
              'hidden_dim': m.get('hidden_dim', 16)}
    # Préserver les métadonnées pour load_gan_*
    for key in ('volume_shape', 'image_shape', 'generator_layers',
                'discriminator_layers'):
        if key in m:
            config[key] = m[key]
    return weights, config

def _extract_ldm(result):
    m = result['model']
    weights = {k: v for k, v in m.items() if isinstance(v, np.ndarray)}
    class_emb = m.get('class_embedding')
    class_emb_dim = class_emb.shape[1] if class_emb is not None and hasattr(class_emb, 'shape') else None
    config = {'timesteps': m.get('timesteps', 50),
              'num_classes': m.get('num_classes', 5),
              'input_dim': m.get('input_dim', 8),
              'class_embedding_dim': class_emb_dim}
    # Préserver hidden_sizes (liste, pas ndarray) dans la config
    hs = m.get('hidden_sizes')
    if hs is not None:
        config['hidden_sizes'] = hs
    return (weights, config)

def _extract_slm(result):
    m = result['model']
    # Les poids sont dans 'network_params' (sous-dict de ndarrays)
    params = m.get('network_params', {})
    weights = {k: v for k, v in params.items() if isinstance(v, np.ndarray)}
    # Inclure num_blocks dans les poids pour le predict_fn
    num_blocks = params.get('num_blocks', 1)
    if num_blocks > 1:
        weights['num_blocks'] = num_blocks
    config = {'vocab_size': m.get('vocab_size', 16),
              'seq_len': m.get('seq_len', 4),
              'embed_dim': m.get('embed_dim', 16),
              'num_blocks': num_blocks}
    # Préserver les métadonnées pour load_slm
    if 'class_names' in m:
        config['class_names'] = m['class_names']
    if 'preprocessor_params' in m:
        config['preprocessor_params'] = m['preprocessor_params']
    if 'num_classes' in m:
        config['num_classes'] = m['num_classes']
    if 'vocab' in m:
        # Mapping mot -> index (train_slm_next_word) : indispensable pour
        # reconstituer les séquences en inférence après rechargement, car
        # seul vocab_size (un entier) était préservé jusqu'ici.
        config['vocab'] = m['vocab']
    if 'ff_dim' in m:
        config['ff_dim'] = m['ff_dim']
    return weights, config

def _extract_image_classifier(result):
    m = result['model']
    hidden_layers = m.get('hidden_layers')
    hidden_dim = m.get('hidden_dim', 32)

    if hidden_layers is None:
        # Legacy format: W1/b1 (hidden) + W2/b2 (output) → indexed format
        hidden_layers = [hidden_dim]
        w = {
            'W_0': m['W1'], 'b_0': m['b1'],
            'W_out': m['W2'], 'b_out': m['b2'],
        }
    else:
        w = {k: v for k, v in m.items() if isinstance(v, np.ndarray)}

    config = {
        'num_classes': m.get('num_classes', 3),
        'feature_dim': m.get('feature_dim', 128),
        'hidden_dim': hidden_dim,
        'hidden_layers': hidden_layers,
    }
    # Préserver class_names et accuracy pour load_image_classifier
    if 'class_names' in m:
        config['class_names'] = m['class_names']
    if 'accuracy' in m:
        config['accuracy'] = m['accuracy']
    return (w, config)


def _extract_speech_classifier(result):
    m = result['model']
    weights = {}

    # Extraire les poids conv depuis conv_params (sous-dict)
    conv_params = m.get('conv_params', {})
    if isinstance(conv_params, dict):
        for ck in ('W', 'b'):
            if ck in conv_params and isinstance(conv_params[ck], np.ndarray):
                weights[f'conv_{ck}'] = conv_params[ck]
        for meta_key in ('kernel_size', 'out_channels', 'in_channels', 'dilation'):
            if meta_key in conv_params:
                weights[f'conv_{meta_key}'] = conv_params[meta_key]
    elif isinstance(conv_params, (list, tuple)) and len(conv_params) > 0:
        # Ancien format : conv_params était une liste
        if isinstance(conv_params[0], np.ndarray):
            weights['conv_W'] = conv_params[0]
            weights['conv_b'] = conv_params[1] if len(conv_params) > 1 else np.zeros(1)

    # Extraire les poids FC
    hidden_layers = m.get('hidden_layers')
    if hidden_layers and len(hidden_layers) > 0:
        n_fc = len(hidden_layers)
        for i in range(n_fc):
            w = m.get(f'W_fc_{i}')
            b = m.get(f'b_fc_{i}')
            if w is not None:
                weights[f'W_fc_{i}'] = w
            if b is not None:
                weights[f'b_fc_{i}'] = b
        w_out = m.get('W_fc_out')
        b_out = m.get('b_fc_out')
        if w_out is not None:
            weights['W_fc_out'] = w_out
        if b_out is not None:
            weights['b_fc_out'] = b_out
    else:
        # Format sans hidden layers : W_fc/b_fc sont la sortie directe
        w_fc = m.get('W_fc')
        b_fc = m.get('b_fc')
        if w_fc is not None:
            weights['W_fc_out'] = w_fc
        if b_fc is not None:
            weights['b_fc_out'] = b_fc

    # Config
    config = {
        'feature_dim': m.get('feature_dim', 26),
        'hidden_dim': m.get('hidden_dim', 64),
        'num_classes': m.get('num_classes', 3),
        'class_names': m.get('class_names', ["voyelle", "consonne", "silence"]),
        'hidden_layers': hidden_layers if hidden_layers is not None else [],
        'accuracy': m.get('accuracy', 0),
        'num_features': m.get('num_features', 26),
    }
    for ck in ('conv_in_channels', 'conv_out_channels', 'conv_kernel_size', 'conv_dilation'):
        if ck in m:
            config[ck] = m[ck]
    # Propager les métadonnées conv depuis conv_params si pas déjà dans config
    for meta_key in ('kernel_size', 'out_channels', 'in_channels', 'dilation'):
        ck = f'conv_{meta_key}'
        if meta_key in conv_params and ck not in config:
            config[ck] = conv_params[meta_key]
    return (weights, config)


# ============================================================================
# Predict / Extract pour les 9 modèles scikit-learn
# (délèguent à la fonction générique de sklearn_models.py)
# ============================================================================

@register_predict_fn('sklearn_rf')
def _predict_sklearn_rf(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_gbm')
def _predict_sklearn_gbm(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_svm')
def _predict_sklearn_svm(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_knn')
def _predict_sklearn_knn(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_lr')
def _predict_sklearn_lr(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_nb')
def _predict_sklearn_nb(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_dt')
def _predict_sklearn_dt(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_ridge')
def _predict_sklearn_ridge(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)

@register_predict_fn('sklearn_lasso')
def _predict_sklearn_lasso(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_kmeans')
def _predict_sklearn_kmeans(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_dbscan')
def _predict_sklearn_dbscan(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_hierarchique')
def _predict_sklearn_hierarchique(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_gmm')
def _predict_sklearn_gmm(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_acp')
def _predict_sklearn_acp(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_ica')
def _predict_sklearn_ica(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_isoforest')
def _predict_sklearn_isoforest(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_ocsvm')
def _predict_sklearn_ocsvm(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


@register_predict_fn('sklearn_lof')
def _predict_sklearn_lof(weights, config, X):
    from ..train.sklearn_models import _predict_sklearn_generic
    return _predict_sklearn_generic(weights, config, X)


def _extract_sklearn_wrap(result):
    """Extrait (weights, config) depuis un résultat sklearn pour le Trainer."""
    from ..train.sklearn_models import _extract_sklearn
    return _extract_sklearn(result)


# ============================================================================
# Enregistrement automatique au chargement du module
# ============================================================================

def _raison_indisponible(nom_train):
    """Pourquoi la fonction `nom_train` n'a pas pu être chargée (message lisible : dépendance manquante précise)."""
    from .. import train as _tr
    module = getattr(_tr, "MODULE_DE", {}).get(nom_train)
    err = getattr(_tr, "ERREURS_IMPORT", {}).get(module) if module else None
    if err is None:
        return _i18n_t("IA/trainer/_part_001.py:g:803:16") % nom_train
    manque = getattr(err, "name", None)
    if isinstance(err, ImportError) and manque:
        return _i18n_t("IA/trainer/_part_001.py:g:806:16") % (manque, module)
    return "%s: %s (module IA.train.%s)" % (type(err).__name__, err, module)


def _register_all():
    """Enregistre tous les types supportés. Appelé paresseusement.

    Chaque type est enregistré INDÉPENDAMMENT : si le module d'un type ne se charge pas (jax/flax/optax absents ou
    incompatibles, scikit-learn absent...), seuls les types qui en dépendent sont marqués indisponibles
    (_INDISPONIBLES[type] = raison) ; les autres restent entraînables, et l'erreur affichée nomme la vraie cause
    au lieu d'un « type de modèle inconnu » identique pour tous."""
    if _REGISTRY:
        # Un algorithme installe depuis la page d'entrainement (ou la commande ajouter_modele_ia) apres le demarrage
        # de CE processus (worker Celery, autre worker web) doit y devenir connu sans redemarrage.
        try:
            from ..plugins_modeles import charger_scripts
            charger_scripts()
        except Exception as e:
            logger.warning(_i18n_t("IA/trainer/_part_001.py:g:856:24"), e)
        return
    from .. import train as _tr
    _INDISPONIBLES.clear()
    types = (
        ('mlp', 'train_mlp', _predict_mlp, _extract_mlp, _extract_mlp, 'mini_mlp'),
        ('rnn', 'train_rnn', _predict_rnn, _extract_rnn, _extract_rnn, 'mini_rnn'),
        ('cnn', 'train_cnn2d', _predict_cnn, _extract_cnn, _extract_cnn, 'mini_cnn'),
        ('transformer', 'train_transformer', _predict_transformer, _extract_transformer, _extract_transformer, 'mini_transformer'),
        ('gan', 'train_gan_nd', _predict_gan, _extract_gan, _extract_gan, 'mini_gan'),
        ('ldm', 'train_ldm_image', _predict_ldm, _extract_ldm, _extract_ldm, 'mini_ldm'),
        ('slm', 'train_slm_next_word', _predict_slm, _extract_slm, _extract_slm, 'mini_slm'),
        ('slm_texte', 'train_slm_classifier', _predict_slm, _extract_slm, _extract_slm, 'mini_slm_texte'),
        ('image_classifier', 'train_image_classifier', _predict_image_classifier, _extract_image_classifier, _extract_image_classifier, 'mini_image_classifier'),
        ('speech_classifier', 'train_speech_classifier', _predict_speech_classifier, _extract_speech_classifier, _extract_speech_classifier, 'mini_speech_classifier'),
        ('sklearn_rf', 'train_sklearn_rf', _predict_sklearn_rf, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_rf'),
        ('sklearn_gbm', 'train_sklearn_gbm', _predict_sklearn_gbm, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_gbm'),
        ('sklearn_svm', 'train_sklearn_svm', _predict_sklearn_svm, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_svm'),
        ('sklearn_knn', 'train_sklearn_knn', _predict_sklearn_knn, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_knn'),
        ('sklearn_lr', 'train_sklearn_lr', _predict_sklearn_lr, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_lr'),
        ('sklearn_nb', 'train_sklearn_nb', _predict_sklearn_nb, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_nb'),
        ('sklearn_dt', 'train_sklearn_dt', _predict_sklearn_dt, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_dt'),
        ('sklearn_ridge', 'train_sklearn_ridge', _predict_sklearn_ridge, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_ridge'),
        ('sklearn_lasso', 'train_sklearn_lasso', _predict_sklearn_lasso, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_lasso'),
        ('sklearn_kmeans', 'train_sklearn_kmeans', _predict_sklearn_kmeans, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_kmeans'),
        ('sklearn_dbscan', 'train_sklearn_dbscan', _predict_sklearn_dbscan, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_dbscan'),
        ('sklearn_hierarchique', 'train_sklearn_hierarchique', _predict_sklearn_hierarchique, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_hierarchique'),
        ('sklearn_gmm', 'train_sklearn_gmm', _predict_sklearn_gmm, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_gmm'),
        ('sklearn_acp', 'train_sklearn_acp', _predict_sklearn_acp, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_acp'),
        ('sklearn_ica', 'train_sklearn_ica', _predict_sklearn_ica, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_ica'),
        ('sklearn_isoforest', 'train_sklearn_isoforest', _predict_sklearn_isoforest, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_isoforest'),
        ('sklearn_ocsvm', 'train_sklearn_ocsvm', _predict_sklearn_ocsvm, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_ocsvm'),
        ('sklearn_lof', 'train_sklearn_lof', _predict_sklearn_lof, _extract_sklearn_wrap, _extract_sklearn_wrap, 'mini_sklearn_lof'),
    )
    for code, nom_train, predict_fn, extract_w, extract_c, defaut in types:
        train_fn = getattr(_tr, nom_train, None)
        if train_fn is None:
            _INDISPONIBLES[code] = _raison_indisponible(nom_train)
            logger.warning(_i18n_t("IA/trainer/_part_001.py:g:847:28"), code, _INDISPONIBLES[code])
            continue
        register_model_type(code, train_fn, predict_fn, extract_w, extract_c, defaut)

    # Scripts de modèles DL/ML ajoutés par l'utilisateur (canal modele_ia, IA/plugins_modeles.py).
    try:
        from ..plugins_modeles import charger_scripts
        charger_scripts()
    except Exception as e:
        logger.warning(_i18n_t("IA/trainer/_part_001.py:g:856:24"), e)


# ============================================================================
# Classe Trainer
# ============================================================================



# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
