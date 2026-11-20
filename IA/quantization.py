"""
IA/quantization.py — Quantification réelle des paramètres (4, 8, 16, 32, 64 bits).

Les poids d'un modèle peuvent être stockés (et transportés dans le fichier
.gy) à une largeur de bits réellement plus petite que le float64 utilisé
pendant le calcul :

  - 64 bits : float64 natif (aucune perte, aucun changement).
  - 32 bits : float32 natif (numpy).
  - 16 bits : float16 natif (numpy).
  -  8 bits : quantification affine (asymétrique) sur uint8, avec un
              (scale, zero_point) par tenseur. 1 octet/valeur.
  -  4 bits : quantification affine sur des entiers 0..15, empaquetés
              2 valeurs par octet (nibble haut / nibble bas). 0.5 octet/valeur.

La quantification est *réelle* : le nombre d'octets réellement écrits sur
le disque (et manipulés en mémoire pour le stockage) correspond bien à la
largeur de bits demandée — ce n'est pas juste un changement de dtype
cosmétique. Le calcul (forward/backward) continue lui de se faire en
float64 pour la stabilité numérique : on déquantifie juste avant utilisation.
"""
from engine.i18n import tf

import numpy as np

from .exceptions import ConfigurationError

SUPPORTED_BITS = (4, 8, 16, 32, 64)


def validate_bits(bits: int) -> int:
    bits = int(bits)
    if bits not in SUPPORTED_BITS:
        raise ConfigurationError(
            tf("IA/quantization.py:g:34:13", v1=bits, v2=SUPPORTED_BITS)
        )
    return bits


class QuantizedArray:
    """Représentation compacte d'un tableau, réellement stockée sur `bits` bits/valeur.

    Attributs :
        bits        : 4, 8, 16, 32 ou 64.
        shape       : forme logique du tableau original.
        packed      : bytes bruts (taille réelle = ceil(n*bits/8) pour 4/8 bits,
                      n*2 pour 16 bits, n*4 pour 32 bits, n*8 pour 64 bits).
        scale       : facteur d'échelle (None pour 16/32/64 bits, natifs).
        zero_point  : point zéro entier (None pour 16/32/64 bits).
    """

    __slots__ = ("bits", "shape", "packed", "scale", "zero_point")

    def __init__(self, bits, shape, packed, scale=None, zero_point=None):
        self.bits = bits
        self.shape = tuple(shape)
        self.packed = packed
        self.scale = scale
        self.zero_point = zero_point

    @property
    def nbytes(self) -> int:
        return len(self.packed)

    def dequantize(self) -> np.ndarray:
        return dequantize(self)


# ---------------------------------------------------------------------------
# Quantification affine (4 et 8 bits)
# ---------------------------------------------------------------------------

def _affine_qparams(arr: np.ndarray, n_levels: int):
    """Calcule (scale, zero_point) pour quantifier arr sur [0, n_levels-1]."""
    lo = float(np.min(arr)) if arr.size else 0.0
    hi = float(np.max(arr)) if arr.size else 1.0
    if hi == lo:
        hi = lo + 1e-8
    scale = (hi - lo) / (n_levels - 1)
    zero_point = lo
    return scale, zero_point


def _affine_quantize(arr: np.ndarray, n_levels: int):
    scale, zero_point = _affine_qparams(arr, n_levels)
    q = np.round((arr - zero_point) / scale)
    q = np.clip(q, 0, n_levels - 1).astype(np.uint8)
    return q, scale, zero_point


def _affine_dequantize(q: np.ndarray, scale: float, zero_point: float) -> np.ndarray:
    return q.astype(np.float64) * scale + zero_point


def _pack_uint4(q: np.ndarray) -> bytes:
    """Empaquette un tableau uint8 (valeurs 0..15) en nibbles, 2 valeurs/octet."""
    flat = q.ravel()
    n = flat.size
    if n % 2 == 1:
        flat = np.concatenate([flat, np.zeros(1, dtype=np.uint8)])
    lo = flat[0::2] & 0x0F
    hi = flat[1::2] & 0x0F
    packed = (hi << 4) | lo
    return packed.astype(np.uint8).tobytes()


def _unpack_uint4(data: bytes, n: int) -> np.ndarray:
    packed = np.frombuffer(data, dtype=np.uint8)
    lo = packed & 0x0F
    hi = (packed >> 4) & 0x0F
    out = np.empty(packed.size * 2, dtype=np.uint8)
    out[0::2] = lo
    out[1::2] = hi
    return out[:n]


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def quantize(arr, bits: int) -> QuantizedArray:
    """Quantifie `arr` (n'importe quel array-like) sur `bits` bits réels/valeur."""
    bits = validate_bits(bits)
    arr = np.asarray(arr, dtype=np.float64)
    shape = arr.shape

    if bits == 64:
        return QuantizedArray(64, shape, arr.astype(np.float64).tobytes())
    if bits == 32:
        return QuantizedArray(32, shape, arr.astype(np.float32).tobytes())
    if bits == 16:
        return QuantizedArray(16, shape, arr.astype(np.float16).tobytes())
    if bits == 8:
        q, scale, zero_point = _affine_quantize(arr, n_levels=256)
        return QuantizedArray(8, shape, q.tobytes(), scale, zero_point)
    if bits == 4:
        q, scale, zero_point = _affine_quantize(arr, n_levels=16)
        return QuantizedArray(4, shape, _pack_uint4(q), scale, zero_point)
    raise ConfigurationError(tf("IA/quantization.py:g:139:30", v1=bits))


def dequantize(qarr: QuantizedArray) -> np.ndarray:
    """Reconstruit un ndarray float64 (pour le calcul) depuis un QuantizedArray."""
    n = 1
    for d in qarr.shape:
        n *= d

    if qarr.bits == 64:
        return np.frombuffer(qarr.packed, dtype=np.float64).reshape(qarr.shape).copy()
    if qarr.bits == 32:
        return np.frombuffer(qarr.packed, dtype=np.float32).astype(np.float64).reshape(qarr.shape)
    if qarr.bits == 16:
        return np.frombuffer(qarr.packed, dtype=np.float16).astype(np.float64).reshape(qarr.shape)
    if qarr.bits == 8:
        q = np.frombuffer(qarr.packed, dtype=np.uint8)[:n]
        return _affine_dequantize(q, qarr.scale, qarr.zero_point).reshape(qarr.shape)
    if qarr.bits == 4:
        q = _unpack_uint4(qarr.packed, n)
        return _affine_dequantize(q, qarr.scale, qarr.zero_point).reshape(qarr.shape)
    raise ConfigurationError(tf("IA/quantization.py:g:160:30", v1=qarr.bits))


def bytes_for(shape, bits: int) -> int:
    """Nombre d'octets réellement occupés par un tableau de cette forme à `bits` bits/valeur."""
    bits = validate_bits(bits)
    n = 1
    for d in shape:
        n *= d
    if bits in (4,):
        return (n + 1) // 2
    return n * (bits // 8)
