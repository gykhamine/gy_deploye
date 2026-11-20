"""
IA/backend.py — Moteur de calcul 100% Python (numpy).

Remplace l'ancien moteur C++ (IA/cpp/, ctypes + g++). Toutes les operations
mathematiques utilisees par train/ et infer/ sont ici implementees avec
numpy, en respectant exactement la semantique de l'ancien moteur C++
(memes formes de sortie, memes valeurs de stabilisation numerique, etc.)
"""

import numpy as np

_BACKEND = "numpy"
_MAX_KERNEL_ND = 8


def _asf64(x):
    return np.asarray(x, dtype=np.float64)


class _NumpyEngine:
    """Moteur de calcul pur Python/numpy — meme API que l'ancien _CppEngine."""

    VERSION = "2.0.0"
    BACKEND = "numpy"

    # ---- 2. Activations ----
    def relu(self, x):
        x = _asf64(x)
        return np.maximum(x, 0.0)

    def relu_deriv(self, x):
        x = _asf64(x)
        return (x > 0).astype(np.float64)

    def sigmoid(self, x):
        x = np.clip(_asf64(x), -10.0, 10.0)
        return 1.0 / (1.0 + np.exp(-x))

    def sigmoid_deriv(self, x):
        s = self.sigmoid(x)
        return s * (1.0 - s)

    def tanh(self, x):
        return np.tanh(_asf64(x))

    def tanh_deriv(self, x):
        t = np.tanh(_asf64(x))
        return 1.0 - t * t

    def leaky_relu(self, x, alpha=0.01):
        x = _asf64(x)
        return np.where(x > 0, x, alpha * x)

    def leaky_relu_deriv(self, x, alpha):
        x = _asf64(x)
        return np.where(x > 0, 1.0, alpha).astype(np.float64)

    def softmax(self, x):
        x = _asf64(x)
        m = np.max(x, axis=-1, keepdims=True)
        e = np.exp(x - m)
        return e / np.sum(e, axis=-1, keepdims=True)

    def gelu(self, x):
        x = _asf64(x)
        c = np.sqrt(2.0 / np.pi)
        inner = c * (x + 0.044715 * x ** 3)
        return 0.5 * x * (1.0 + np.tanh(inner))

    def gelu_deriv(self, x):
        x = _asf64(x)
        c = np.sqrt(2.0 / np.pi)
        inner = c * (x + 0.044715 * x ** 3)
        t = np.tanh(inner)
        sech2 = 1.0 - t * t
        return 0.5 * (1.0 + t) + 0.5 * x * sech2 * c * (1.0 + 3.0 * 0.044715 * x ** 2)

    # ---- 3. Algebre lineaire ----
    def matmul(self, a, b):
        a = _asf64(a)
        b = _asf64(b)
        if a.ndim == 1 and b.ndim == 1:
            return np.array([float(np.dot(a, b))])
        return np.matmul(a, b)

    def add(self, a, b):
        return _asf64(a) + _asf64(b)

    def sub(self, a, b):
        return _asf64(a) - _asf64(b)

    def mul(self, a, b):
        return _asf64(a) * _asf64(b)

    def div(self, a, b):
        return _asf64(a) / (_asf64(b) + 1e-12)

    def maximum(self, a, b):
        return np.maximum(_asf64(a), _asf64(b))

    def outer(self, a, b):
        a = _asf64(a)
        b = _asf64(b)
        if b.ndim == 1:
            return np.outer(a, b)
        if b.ndim == 2:
            return np.outer(a, b.sum(axis=0))
        raise ValueError("outer: unsupported B dimensions")

    def concatenate(self, a, b):
        a = _asf64(a)
        b = _asf64(b)
        return np.concatenate([a, b], axis=0)

    def scale(self, x, s):
        return _asf64(x) * float(s)

    def add_scalar(self, x, s):
        return _asf64(x) + float(s)

    def clip(self, x, lo, hi):
        return np.clip(_asf64(x), lo, hi)

    def exp(self, x):
        return np.exp(_asf64(x))

    def log(self, x):
        return np.log(_asf64(x) + 1e-12)

    def sqrt(self, x):
        return np.sqrt(np.maximum(0.0, _asf64(x)))

    def pow(self, x, n):
        return np.power(_asf64(x), n)

    def abs(self, x):
        return np.abs(_asf64(x))

    def sign(self, x):
        return np.sign(_asf64(x))

    def neg(self, x):
        return -_asf64(x)

    def tile(self, x, n):
        x = _asf64(x)
        reps = [1] * x.ndim
        reps[0] = int(n)
        return np.tile(x, reps)

    def repeat(self, x, n):
        x = _asf64(x)
        return np.repeat(x.ravel(), int(n))

    def transpose2d(self, x):
        x = _asf64(x)
        return x.T.copy()

    # ---- 4. Convolutions ----
    def convolve2d(self, img, kernel):
        img = _asf64(img)
        kernel = _asf64(kernel)
        ih, iw = img.shape
        kh, kw = kernel.shape
        oh, ow = ih - kh + 1, iw - kw + 1
        windows = np.lib.stride_tricks.sliding_window_view(img, (kh, kw))
        return np.tensordot(windows, kernel, axes=([2, 3], [0, 1]))[:oh, :ow]

    def convolve2d_backward(self, img, kernel, d_conv):
        # d_kernel[m,n] = sum_{i,j} img[i+m,j+n] * d_conv[i,j]
        # C'est exactement convolve2d(img, d_conv) (voir note technique).
        return self.convolve2d(img, d_conv)

    def conv1d_forward(self, x, W, b, kernel_size, dilation):
        x = _asf64(x)
        W = _asf64(W)
        b = _asf64(b)
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
            # x_slice: (in_c, n_valid) ; W[:, :, k]: (out_c, in_c)
            x_slice = x[:, idx[valid]]
            out[:, valid] += W[:, :, k] @ x_slice
        out += b[:, None]
        return out

    def conv1d_backward(self, x, W, d_out, kernel_size, dilation):
        x = _asf64(x)
        W = _asf64(W)
        d_out = _asf64(d_out)
        out_c, in_c, ks = W.shape
        seq_len = x.shape[1]
        out_len = d_out.shape[1]
        d_W = np.zeros_like(W)
        d_b = d_out.sum(axis=1)
        d_x = np.zeros_like(x)
        for k in range(kernel_size):
            idx = np.arange(out_len) + k * dilation
            valid = idx < seq_len
            if not np.any(valid):
                continue
            x_slice = x[:, idx[valid]]          # (in_c, n_valid)
            dout_slice = d_out[:, valid]         # (out_c, n_valid)
            d_W[:, :, k] += dout_slice @ x_slice.T
            d_x[:, idx[valid]] += W[:, :, k].T @ dout_slice
        return d_x, d_W, d_b

    def convolve_nd(self, volume, kernel):
        volume = _asf64(volume)
        kernel = _asf64(kernel)
        windows = np.lib.stride_tricks.sliding_window_view(volume, kernel.shape)
        k_axes = tuple(range(volume.ndim, volume.ndim + kernel.ndim))
        return np.tensordot(windows, kernel, axes=(k_axes, tuple(range(kernel.ndim))))

    def convolve_nd_backward(self, volume, kernel, d_conv):
        # Meme principe que convolve2d_backward, generalise a N dimensions.
        return self.convolve_nd(volume, d_conv)

    # ---- 5. Normalisation ----
    def layer_norm(self, x, eps=1e-8):
        x = _asf64(x)
        mean = x.mean(axis=-1, keepdims=True)
        var = x.var(axis=-1, keepdims=True)
        return (x - mean) / np.sqrt(var + eps)

    # ---- 6. Pertes ----
    def mse_loss(self, pred, target):
        pred = _asf64(pred)
        target = _asf64(target)
        return float(np.mean((pred - target) ** 2))

    def mse_loss_grad(self, pred, target):
        pred = _asf64(pred)
        target = _asf64(target)
        n = pred.size
        return 2.0 * (pred - target) / n

    def cross_entropy_loss(self, logits, target_idx):
        logits = _asf64(logits)
        m = np.max(logits)
        e = np.exp(logits - m)
        soft = e / np.sum(e)
        return float(-np.log(soft[int(target_idx)] + 1e-12))

    # ---- 7. Initialisation ----
    def zeros(self, shape):
        return np.zeros(tuple(shape), dtype=np.float64)

    def ones(self, shape):
        return np.ones(tuple(shape), dtype=np.float64)

    def xavier_init(self, shape, seed=42):
        shape = tuple(shape)
        rng = np.random.RandomState(int(seed))
        fan_sum = sum(shape)
        std_dev = np.sqrt(2.0 / fan_sum)
        return rng.standard_normal(shape) * std_dev

    def randn(self, shape, seed=42):
        rng = np.random.RandomState(int(seed))
        return rng.standard_normal(tuple(shape))

    def uniform(self, shape, lo, hi, seed=42):
        rng = np.random.RandomState(int(seed))
        return rng.uniform(float(lo), float(hi), tuple(shape))

    def permutation(self, n, seed=42):
        rng = np.random.RandomState(int(seed))
        return rng.permutation(int(n)).tolist()

    # ---- 8. Reductions scalaires ----
    def sum(self, x):
        return float(np.sum(_asf64(x)))

    def mean(self, x):
        return float(np.mean(_asf64(x)))

    def max_val(self, x):
        return float(np.max(_asf64(x)))

    def argmax(self, x):
        return int(np.argmax(_asf64(x)))

    def dot1d(self, a, b):
        return float(np.dot(_asf64(a), _asf64(b)))

    # ---- 8b. Reductions sur axe ----
    def sum_axis(self, x, axis):
        x = _asf64(x)
        ndim = x.ndim
        if axis == -1 or axis == ndim - 1:
            return x.sum(axis=-1).reshape(-1)
        if axis == 0:
            return x.sum(axis=0)
        raise ValueError("sum_axis: unsupported axis")

    def mean_axis(self, x, axis):
        x = _asf64(x)
        ndim = x.ndim
        if axis == -1 or axis == ndim - 1:
            return x.mean(axis=-1).reshape(-1)
        if axis == 0:
            return x.mean(axis=0)
        if axis == 1:
            if ndim != 2:
                raise ValueError("mean_axis(1): requires 2D")
            return x.mean(axis=1)
        raise ValueError("mean_axis: unsupported axis")

    def var_axis(self, x, axis):
        x = _asf64(x)
        ndim = x.ndim
        if axis == -1 or axis == ndim - 1:
            return x.var(axis=-1).reshape(-1)
        raise ValueError("var_axis: unsupported axis")

    def max_axis(self, x):
        x = _asf64(x)
        return x.max(axis=-1).reshape(-1)

    # ---- 9. FFT ----
    def fft_rfft(self, x, n_fft):
        x = _asf64(x)
        spec = np.fft.rfft(x, n=int(n_fft))
        return np.abs(spec)

    # ---- 10. Operations specifiques ----
    def add_noise(self, x0, sqrt_alpha, sqrt_one_minus):
        x0 = _asf64(x0)
        sqrt_alpha = _asf64(sqrt_alpha)
        sqrt_one_minus = _asf64(sqrt_one_minus)
        # seed "temporaire" fixe a 0, comme dans l'implementation C++ d'origine.
        noise = self.randn(x0.shape, seed=0)
        return sqrt_alpha.flat[0] * x0 + sqrt_one_minus.flat[0] * noise

    def linear(self, x, W, b):
        return self.matmul(x, W) + _asf64(b)

    def ldm_predict_noise(self, x_noisy, class_embedding, class_id, W1, b1, W2, b2):
        x_noisy = _asf64(x_noisy)
        class_embedding = _asf64(class_embedding)
        c_embed = class_embedding[int(class_id)]
        x_concat = np.concatenate([x_noisy, c_embed], axis=0)
        z1 = self.matmul(x_concat, W1) + _asf64(b1)
        h1 = self.relu(z1)
        output = self.matmul(h1, W2) + _asf64(b2)
        return output, x_concat, z1, h1

    def pad1d(self, x, target_len):
        x = _asf64(x)
        if x.size >= target_len:
            return x.copy()
        out = np.zeros(int(target_len), dtype=np.float64)
        out[:x.size] = x
        return out

    def diff_axis(self, x, axis):
        x = _asf64(x)
        if x.ndim == 1:
            return np.diff(x)
        if x.ndim == 2 and axis in (0, 1):
            return np.diff(x, axis=axis)
        raise ValueError("diff_axis: unsupported")

    def linspace(self, start, end, num):
        return np.linspace(float(start), float(end), int(num))

    def arange(self, start, end, step):
        return np.arange(float(start), float(end), float(step))

    def mgrid3d(self, size):
        size = int(size)
        z_idx, y_idx, x_idx = np.indices((size, size, size))
        return (x_idx.astype(np.float64),
                y_idx.astype(np.float64),
                z_idx.astype(np.float64))

    def gather(self, x, indices):
        x = _asf64(x)
        idx = np.asarray(indices, dtype=np.int64)
        return x[idx]

    def dct2(self, x, num_coeffs):
        x = _asf64(x)
        n = x.size
        K = min(int(num_coeffs), n)
        k = np.arange(K)[:, None]
        n_idx = np.arange(n)[None, :]
        basis = np.cos(np.pi * k * (2 * n_idx + 1) / (2 * n))
        return basis @ x

    def histogram(self, x, n_bins, range_lo, range_hi):
        x = _asf64(x)
        n_bins = int(n_bins)
        bin_width = (range_hi - range_lo) / n_bins
        bins = ((x - range_lo) / bin_width).astype(np.int64)
        counts = np.zeros(n_bins, dtype=np.float64)
        valid = (bins >= 0) & (bins < n_bins)
        np.add.at(counts, bins[valid], 1.0)
        return counts


# ---------------------------------------------------------------------------
# Instance singleton + API publique (meme interface que l'ancien module cpp)
# ---------------------------------------------------------------------------
_engine = _NumpyEngine()


def get_backend():
    return _BACKEND


def is_cpp_available():
    """Conserve pour compatibilite : il n'y a plus de moteur C++, toujours False."""
    return False


def get_core():
    return _engine


__all__ = ["get_backend", "is_cpp_available", "get_core", "_BACKEND"]
