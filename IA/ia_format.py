"""
IA/ia_format.py — Format binaire natif .gy pour la sérialisation des modèles.

Format :
  Offset  Taille  Champ
  0       4       Magique : "IAV3" (4 bytes)
  4       2       Version majeure (uint16 LE)
  6       2       Version mineure (uint16 LE)
  8       4       Taille du header JSON (uint32 LE)
  12      N       Header JSON (UTF-8) : type, config, accuracy, history...
  12+N    4       Nombre de tenseurs (uint32 LE)
  16+N    ...     Pour chaque tenseur :
                    - 1 byte  : dtype (0=f32, 1=f64, 2=i64, 3=f16, 4=q8, 5=q4)
                    - [si dtype quantifié 4 ou 5] 16 bytes : scale (f64) + zero_point (f64)
                    - 4 bytes : ndim (uint32 LE)
                    - 8*ndim bytes : shape (int64 LE)
                    - 4 bytes : taille du nom (uint32 LE)
                    - M bytes : nom (UTF-8)
                    - K bytes : données, K dépendant réellement du dtype :
                        f32/f64/i64/f16 : produit(shape) * sizeof(dtype)
                        q8              : produit(shape) * 1 octet
                        q4              : ceil(produit(shape) / 2) octets (2 valeurs/octet)

Les types q8/q4 sont une quantification affine réelle (voir IA/quantization.py) :
le nombre d'octets effectivement écrits sur le disque correspond à la largeur
de bits demandée, ce n'est pas un simple changement de dtype cosmétique.

Le format est volontairement simple et sans dépendance (juste struct + json).
Il peut être lu depuis n'importe quel langage via memcpy/fread sans bibliothèque externe.
Les fichiers portent l'extension .gy.
"""
from engine.i18n import tf
import struct
import json
import numpy as np
from typing import Any, Dict, List, Tuple
import os

from .exceptions import ModelFormatError
from . import quantization as _quant
from engine.i18n import tf as _gtf  # noqa: E402

MAGIC = b"IAV3"
VERSION_MAJOR = 1
VERSION_MINOR = 0

_DTYPE_MAP = {
    'float32': 0,
    'float64': 1,
    'int64':   2,
    'float16': 3,
}
_DTYPE_REV = {v: k for k, v in _DTYPE_MAP.items()}
_DTYPE_SIZE = {0: 4, 1: 8, 2: 8, 3: 2}

# Identifiants dtype pour les tenseurs quantifiés (4/8 bits, affine).
_QDTYPE_Q8 = 4
_QDTYPE_Q4 = 5
_BITS_TO_DTYPE_ID = {64: 1, 32: 0, 16: 3, 8: _QDTYPE_Q8, 4: _QDTYPE_Q4}
_DTYPE_ID_TO_BITS = {1: 64, 0: 32, 3: 16, 8: 8, _QDTYPE_Q8: 8, _QDTYPE_Q4: 4}


def save_model(path: str, header: Dict[str, Any], tensors: Dict[str, np.ndarray],
               bits=None, tensor_bits: Dict[str, int] = None) -> None:
    """Sauvegarde un modèle au format .gy.

    Args:
        path: chemin du fichier (.gy recommandé).
        header: dictionnaire JSON-sérialisable contenant la config
                (type, hyperparams, accuracy, history...).
        tensors: dictionnaire {nom: ndarray} des poids du modèle.
        bits: largeur de bits par défaut pour tous les tenseurs float
              (4, 8, 16, 32 ou 64). None = comportement historique
              (float64 tel quel, ou float32/int64 s'ils sont déjà dans ce dtype).
        tensor_bits: override par tenseur, ex. {'W_0': 8, 'b_0': 32}.
                     Prioritaire sur `bits`.
    """
    header_bytes = json.dumps(header, ensure_ascii=False, default=_json_default).encode('utf-8')

    _dir = os.path.dirname(path)
    if _dir:
        os.makedirs(_dir, exist_ok=True)

    tensor_bits = tensor_bits or {}

    with open(path, 'wb') as f:
        # Magique + version
        f.write(MAGIC)
        f.write(struct.pack('<HH', VERSION_MAJOR, VERSION_MINOR))
        # Header
        f.write(struct.pack('<I', len(header_bytes)))
        f.write(header_bytes)
        # Tenseurs
        f.write(struct.pack('<I', len(tensors)))
        for name, arr in tensors.items():
            arr = np.asarray(arr)
            requested_bits = tensor_bits.get(name, bits)

            if arr.dtype.name == 'int64' and requested_bits is None:
                # Les tenseurs entiers (index, vocab...) ne sont pas quantifiés.
                dtype_id = 2
                f.write(struct.pack('<B', dtype_id))
                f.write(struct.pack('<I', arr.ndim))
                for dim in arr.shape:
                    f.write(struct.pack('<q', int(dim)))
                name_bytes = name.encode('utf-8')
                f.write(struct.pack('<I', len(name_bytes)))
                f.write(name_bytes)
                f.write(np.ascontiguousarray(arr).tobytes())
                continue

            if requested_bits is None:
                # Comportement historique : float32 natif conservé tel quel,
                # tout le reste force en float64.
                if arr.dtype.name == 'float32':
                    dtype_id = 0
                    f.write(struct.pack('<B', dtype_id))
                    f.write(struct.pack('<I', arr.ndim))
                    for dim in arr.shape:
                        f.write(struct.pack('<q', int(dim)))
                    name_bytes = name.encode('utf-8')
                    f.write(struct.pack('<I', len(name_bytes)))
                    f.write(name_bytes)
                    f.write(np.ascontiguousarray(arr).tobytes())
                    continue
                requested_bits = 64

            requested_bits = _quant.validate_bits(requested_bits)
            qarr = _quant.quantize(arr, requested_bits)
            dtype_id = _BITS_TO_DTYPE_ID[requested_bits]

            f.write(struct.pack('<B', dtype_id))
            if dtype_id in (_QDTYPE_Q8, _QDTYPE_Q4):
                f.write(struct.pack('<dd', float(qarr.scale), float(qarr.zero_point)))
            f.write(struct.pack('<I', arr.ndim))
            for dim in arr.shape:
                f.write(struct.pack('<q', int(dim)))
            name_bytes = name.encode('utf-8')
            f.write(struct.pack('<I', len(name_bytes)))
            f.write(name_bytes)
            f.write(qarr.packed)


def load_model(path: str) -> Tuple[Dict[str, Any], Dict[str, np.ndarray]]:
    """Charge un modèle .gy.

    Returns:
        (header, tensors) : header est un dict, tensors est {nom: ndarray}.
    """
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != MAGIC:
            raise ModelFormatError(
                _gtf("rg6.msg.001", v1=magic, v2=MAGIC))
        vmaj, vmin = struct.unpack('<HH', f.read(4))
        if vmaj != VERSION_MAJOR:
            raise ModelFormatError(
                f"Version .gy incompatible : {vmaj}.{vmin} (attendu {VERSION_MAJOR}.x)")
        header_len = struct.unpack('<I', f.read(4))[0]
        header = json.loads(f.read(header_len).decode('utf-8'))
        n_tensors = struct.unpack('<I', f.read(4))[0]
        tensors = {}
        tensors_bits = {}
        for _ in range(n_tensors):
            dtype_id = struct.unpack('<B', f.read(1))[0]
            scale = zero_point = None
            if dtype_id in (_QDTYPE_Q8, _QDTYPE_Q4):
                scale, zero_point = struct.unpack('<dd', f.read(16))
            ndim = struct.unpack('<I', f.read(4))[0]
            shape = tuple(struct.unpack('<q', f.read(8))[0] for _ in range(ndim))
            name_len = struct.unpack('<I', f.read(4))[0]
            name = f.read(name_len).decode('utf-8')
            n_elem = 1
            for d in shape:
                n_elem *= d

            if dtype_id in (_QDTYPE_Q8, _QDTYPE_Q4):
                bits = _DTYPE_ID_TO_BITS[dtype_id]
                n_bytes = _quant.bytes_for(shape, bits)
                data = f.read(n_bytes)
                qarr = _quant.QuantizedArray(bits, shape, data, scale, zero_point)
                arr = qarr.dequantize()
            else:
                dtype = np.dtype(_DTYPE_REV[dtype_id])
                data = f.read(n_elem * _DTYPE_SIZE[dtype_id])
                arr = np.frombuffer(data, dtype=dtype).reshape(shape).copy()

            tensors[name] = arr
            tensors_bits[name] = _DTYPE_ID_TO_BITS.get(dtype_id, 64)
    header['_tensor_bits'] = tensors_bits
    return header, tensors


def _json_default(o):
    """Conversion par défaut pour les types non-JSON."""
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(tf("IA/ia_format.py:g:202:21", v1=type(o)))


def model_info(path: str) -> Dict[str, Any]:
    """Retourne un résumé du modèle sans charger les tenseurs (léger)."""
    with open(path, 'rb') as f:
        magic = f.read(4)
        if magic != MAGIC:
            raise ModelFormatError(tf("IA/ia_format.py:g:210:36"))
        vmaj, vmin = struct.unpack('<HH', f.read(4))
        header_len = struct.unpack('<I', f.read(4))[0]
        header = json.loads(f.read(header_len).decode('utf-8'))
    header['_format_version'] = f"{vmaj}.{vmin}"
    return header


# ========================================================================
# Utilitaire de sérialisation partagé (remplace le code dupliqué dans
# les 8 fichiers train/*.py).  Chaque train_xxx construit un dict
# « model » mélangé (ndarrays + scalaires + listes de ndarrays).
# Cette fonction le sépare proprement pour ia_format.save_model().
# ========================================================================

def serialize_model_dict(model_dict: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, np.ndarray]]:
    """Sépare un dictionnaire modèle en (header, tensors) pour save_model().

    Règles :
      - Les valeurs ``np.ndarray`` deviennent des tenseurs.
      - Les valeurs ``list`` de ndarrays sont aplaties en clés indexées
        (``k_0``, ``k_1``, …) et un résumé des shapes est conservé dans
        le header sous la clé originale.
      - Les sous-dicts de ndarrays sont aussi aplanis récursivement.
      - Tout le reste (scalars, strings, listes scalaires, None) reste
        dans le header.

    Args:
        model_dict: Dictionnaire mélangé issu d'un train_xxx.

    Returns:
        (header, tensors) prêts pour ``save_model(path, header, tensors)``.
    """
    tensors: Dict[str, np.ndarray] = {}
    header: Dict[str, Any] = {}

    for k, v in model_dict.items():
        if isinstance(v, np.ndarray):
            tensors[k] = v
        elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], np.ndarray):
            for i, arr in enumerate(v):
                tensors[f'{k}_{i}'] = np.asarray(arr)
            header[k] = [list(a.shape) for a in v]
        elif isinstance(v, dict):
            # Sous-dict de ndarrays (ex. network_params)
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, np.ndarray):
                    tensors[sub_k] = sub_v
                elif isinstance(sub_v, list) and len(sub_v) > 0 and isinstance(sub_v[0], np.ndarray):
                    for i, arr in enumerate(sub_v):
                        tensors[f'{sub_k}_{i}'] = np.asarray(arr)
                    header[sub_k] = [list(a.shape) for a in sub_v]
                else:
                    header[sub_k] = sub_v
        else:
            header[k] = v

    header['_tensors'] = list(tensors.keys())
    return header, tensors
