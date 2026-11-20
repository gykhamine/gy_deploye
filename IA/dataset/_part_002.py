from engine.i18n import t
from engine.i18n import tf
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402


def _load_audio_file(path, sr=None, mono=True, normalize=True,
                     max_len=None):
    """Charge un fichier audio en numpy array float64.

    Priorité : soundfile → pydub → scipy.io.wavfile (wav) → wave stdlib.

    Args:
        path     : chemin vers le fichier audio.
        sr       : sample rate cible (None = original).
        mono     : convertir en mono si True.
        normalize: normaliser vers [-1, 1].
        max_len  : nombre max d'échantillons (tronque ou padde).

    Returns:
        (signal float64 1D, sample_rate int)
    """
    ext = os.path.splitext(path)[1].lower()
    signal = None
    file_sr = None

    # --- soundfile (priorité : supporte flac, ogg, wav) ---------------------
    try:
        import soundfile as sf
        data, file_sr = sf.read(path, always_2d=True)
        if mono:
            data = data.mean(axis=1)
        else:
            data = data.flatten()
        signal = data.astype(np.float64)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(t("IA/dataset/_part_002.py:g:40:24"), e)

    # --- pydub (mp3 et autres via ffmpeg) -----------------------------------
    if signal is None:
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(path)
            if mono:
                audio = audio.set_channels(1)
            file_sr = audio.frame_rate
            raw = np.array(audio.get_array_of_samples(), dtype=np.float64)
            max_val = float(2 ** (audio.sample_width * 8 - 1))
            signal = raw / max_val
        except ImportError:
            pass
        except Exception as e:
            logger.warning(t("IA/dataset/_part_002.py:g:56:28"), e)

    # --- scipy.io.wavfile (fallback wav) ------------------------------------
    if signal is None and ext == ".wav":
        try:
            from scipy.io import wavfile
            file_sr, data = wavfile.read(path)
            data = data.astype(np.float64)
            if data.ndim > 1 and mono:
                data = data.mean(axis=1)
            max_val = float(np.iinfo(np.int16).max)
            signal = data / max_val
        except ImportError:
            pass
        except Exception as e:
            logger.warning(t("IA/dataset/_part_002.py:g:71:28"), e)

    # --- wave stdlib (dernier recours, wav uniquement) ----------------------
    if signal is None and ext == ".wav":
        import wave, struct
        with wave.open(path, "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            n_frames = wf.getnframes()
            file_sr = wf.getframerate()
            raw = wf.readframes(n_frames)
        fmt = {1: "b", 2: "h", 4: "i"}.get(sampwidth, "h")
        samples = np.array(
            struct.unpack(f"<{n_frames * n_channels}{fmt}", raw),
            dtype=np.float64
        )
        max_val = float(2 ** (8 * sampwidth - 1))
        samples = samples / max_val
        if mono and n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)
        signal = samples

    if signal is None:
        raise RuntimeError(
            tf("IA/dataset/_part_002.py:g:94:13", v1=path)
        )

    # --- Rééchantillonnage --------------------------------------------------
    if sr is not None and file_sr is not None and sr != file_sr:
        ratio = sr / file_sr
        new_len = int(len(signal) * ratio)
        indices = np.linspace(0, len(signal) - 1, new_len)
        signal = np.interp(indices, np.arange(len(signal)), signal)
        file_sr = sr

    # --- Normalisation ------------------------------------------------------
    if normalize:
        peak = np.abs(signal).max()
        if peak > 0:
            signal = signal / peak

    # --- Troncature / padding -----------------------------------------------
    if max_len is not None:
        if len(signal) > max_len:
            signal = signal[:max_len]
        elif len(signal) < max_len:
            signal = np.pad(signal, (0, max_len - len(signal)))

    return signal.astype(np.float64), int(file_sr) if file_sr else 0


@_register(_AUDIO_EXTS)
def _load_audio(path, target=None, sr=None, mono=True, normalize=True,
                max_len=22050, **kw):
    """Charge un fichier audio unique.

    Returns:
        (signal 1D float64, sample_rate) via le tuple (X, y, meta)
        où X = signal, y = None, meta = sample_rate.
    """
    signal, file_sr = _load_audio_file(
        path, sr=sr, mono=mono, normalize=normalize,
        max_len=max_len or kw.get("max_len")
    )
    return signal, None, file_sr


def load_audio_dataset(path, sr=16000, mono=True, normalize=True,
                       max_len=16000, extensions=None):
    """Charge un dossier audio organisé par classe.

    Structure attendue :
        path/
            classe_0/
                clip1.wav
                clip2.flac
            classe_1/
                clip3.wav

    Args:
        path      : chemin vers le dossier racine.
        sr        : sample rate cible (rééchantillonnage si nécessaire).
        mono      : convertir en mono.
        normalize : normaliser vers [-1, 1].
        max_len   : longueur fixe en échantillons (tronque ou padde).
        extensions: set d'extensions autorisées. None = _AUDIO_EXTS.

    Returns:
        (X, y, class_names)
        X          : array float64 (n_samples, max_len).
        y          : array int64 (n_samples, 1).
        class_names: liste des noms de classes.
    """
    if extensions is None:
        extensions = _AUDIO_EXTS

    if not os.path.isdir(path):
        raise NotADirectoryError(tf("IA/dataset/_part_002.py:g:169:34", v1=path))

    subdirs = sorted([
        d for d in os.listdir(path)
        if os.path.isdir(os.path.join(path, d))
    ])

    if not subdirs:
        # Dossier plat
        files = sorted([
            f for f in os.listdir(path)
            if os.path.splitext(f)[1].lower() in extensions
        ])
        if not files:
            raise ValueError(tf("IA/dataset/_part_002.py:g:183:30", v1=path))
        X_list = []
        for fname in files:
            sig, _ = _load_audio_file(os.path.join(path, fname),
                                      sr=sr, mono=mono, normalize=normalize,
                                      max_len=max_len)
            X_list.append(sig)
        X = np.array(X_list, dtype=np.float64)
        logger.info(_gt("rg8.msg.063"), len(files), X.shape)
        return X, None, []

    class_names = subdirs
    X_list, y_list = [], []

    for label_idx, label in enumerate(class_names):
        label_dir = os.path.join(path, label)
        files = sorted([
            f for f in os.listdir(label_dir)
            if os.path.splitext(f)[1].lower() in extensions
        ])
        for fname in files:
            fpath = os.path.join(label_dir, fname)
            try:
                sig, _ = _load_audio_file(fpath, sr=sr, mono=mono,
                                          normalize=normalize, max_len=max_len)
                X_list.append(sig)
                y_list.append(label_idx)
            except Exception as e:
                logger.warning(t("IA/dataset/_part_002.py:g:210:32"), fpath, e)

    if not X_list:
        raise ValueError(tf("IA/dataset/_part_002.py:g:214:26", v1=path))

    X = np.array(X_list, dtype=np.float64)
    y = np.array(y_list, dtype=np.int64).reshape(-1, 1)
    logger.info(_gt("rg8.msg.062"), len(X_list),
                len(class_names), X.shape)
    return X, y, class_names


# ============================================================================
# load_folder — alias unifié images + audio
# ============================================================================

def load_folder(path, image_size=None, target_size=None, sr=16000,
                max_len=16000, mono=True, normalize=True, extensions=None):
    """Charge un dossier de fichiers (images ou audio) organisés par label.

    Détecte automatiquement si le contenu est des images ou de l'audio.
    Alias pratique pour load_image_dataset / load_audio_dataset.

    Returns:
        (X, y) — class_names non retourné pour compatibilité trainer.py.
    """
    if extensions is None:
        all_exts = _IMAGE_EXTS | _AUDIO_EXTS
    else:
        all_exts = extensions

    # Détecter le type dominant
    sample_files = []
    for root, dirs, files in os.walk(path):
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in all_exts:
                sample_files.append(ext)
        if len(sample_files) >= 10:
            break

    img_count = sum(1 for e in sample_files if e in _IMAGE_EXTS)
    aud_count = sum(1 for e in sample_files if e in _AUDIO_EXTS)

    if img_count >= aud_count:
        X, y, _ = load_image_dataset(
            path, image_size=image_size or target_size,
            normalize=normalize, extensions=extensions
        )
    else:
        X, y, _ = load_audio_dataset(
            path, sr=sr, mono=mono, normalize=normalize,
            max_len=max_len, extensions=extensions
        )
    return X, y


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
