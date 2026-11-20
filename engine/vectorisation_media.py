"""VECTORISATION DES IMAGES ET DES SONS — on entraîne sur des caractéristiques, pas sur des pixels ni des échantillons bruts.

Un humain ne mémorise pas une image pixel par pixel : il retient des contours, des ombres, des différences de couleur, des
densités de couleur. Ce module fait la même chose AVANT l'entraînement : chaque image (ou son) est convertie une fois en un
court vecteur de nombres, et le modèle apprend sur ces vecteurs. Le même module sert à l'inférence (flux caméra, micros,
analyses) : un modèle entraîné sur des vecteurs doit voir exactement les mêmes vecteurs quand on l'utilise.

Pas de Django ici, seulement numpy (+ PIL pour redimensionner une image) : facile à tester et à appeler de partout.

IMAGE (DIM_IMAGE nombres, tous dans [0, 1] ou proches) — image réduite à 64 × 64, découpée en grille 4 × 4 :
  * contours      : histogramme d'orientations des gradients (8 directions) par case, + densité de contours par case et globale ;
  * ombres        : luminance moyenne par case, statistiques globales (moyenne, écart, déciles), histogramme de luminance
                    (8 niveaux : les ombres sont les premiers), sens de l'éclairage (gauche/droite, haut/bas) ;
  * différences de couleur : axes opposés rouge-vert et jaune-bleu moyens par case, contraste de couleur entre les cases,
                    force des transitions de couleur ;
  * densité de couleurs : histogramme des teintes (12) pondéré par saturation et valeur, histogramme des saturations (4),
                    diversité des teintes (entropie).
SON (DIM_AUDIO nombres) — au plus 10 s, fenêtres de 25 ms / pas de 10 ms :
  * énergie par bande de fréquence (20 bandes, échelle logarithmique) : moyenne et variation dans le temps ;
  * forme du spectre : centroïde, étalement, plat/bruité (flatness), fréquence de coupure à 85 % ;
  * passages par zéro, énergie (RMS), flux spectral (vitesse de variation) ;
  * enveloppe temporelle (8 segments) et 12 coefficients cepstraux (timbre, comme les MFCC).

VERSION_VECTEURS : à changer si la recette change ; elle est mémorisée avec chaque modèle pour qu'un ancien modèle continue
de voir des vecteurs de son temps (jamais ré-interprétés silencieusement).
"""
import math

import numpy as np
from engine.i18n import t as _gt  # noqa: E402

VERSION_VECTEURS = 1

TAILLE_IMAGE = 64          # côté de l'image de travail
GRILLE = 4                 # cases par côté
N_ORIENT = 8               # directions de contour
N_TEINTES = 12
N_SATURATIONS = 4
N_LUM = 8

N_BANDES = 20
N_CEPSTRE = 12
N_ENVELOPPE = 8
DUREE_MAX_S = 10.0
FREQ_MIN_HZ = 50.0
FREQ_MAX_HZ = 8000.0

DIM_IMAGE = (GRILLE * GRILLE * N_ORIENT + GRILLE * GRILLE + 1          # contours
             + GRILLE * GRILLE + 5 + N_LUM + 2                          # ombres
             + GRILLE * GRILLE * 2 + 2 + 2                              # différences de couleur
             + N_TEINTES + N_SATURATIONS + 1)                           # densité de couleurs
DIM_AUDIO = N_BANDES * 2 + 4 + 2 + 3 + 1 + N_ENVELOPPE + N_CEPSTRE


class VectorisationError(ValueError):
    """Entrée illisible (image vide, son sans échantillon...)."""


def dimension(type_media):
    return {"image": DIM_IMAGE, "audio": DIM_AUDIO}[type_media]


# ── IMAGE ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def vers_pil(img, bgr=False):
    """PIL.Image ou ndarray (H, W), (H, W, 3) ou (H, W, 4), uint8 ou flottant -> image PIL RGB (`bgr` : ordre OpenCV)."""
    from PIL import Image
    if isinstance(img, np.ndarray):
        a = img
        if a.ndim == 2:
            a = np.stack([a] * 3, axis=-1)
        if a.ndim != 3 or a.shape[2] not in (3, 4) or a.shape[0] < 1 or a.shape[1] < 1:
            raise VectorisationError("image : forme inattendue %s" % (img.shape,))
        a = a[:, :, :3]
        if bgr:
            a = a[:, :, ::-1]
        if a.dtype != np.uint8:
            m = float(np.nanmax(a)) if a.size else 1.0
            a = np.clip(a * (255.0 if m <= 1.0 else 1.0), 0, 255).astype(np.uint8)
        pil = Image.fromarray(np.ascontiguousarray(a), "RGB")
    else:
        pil = img
    if getattr(pil, "size", (0, 0))[0] < 1 or pil.size[1] < 1:
        raise VectorisationError("image vide")
    return pil.convert("RGB")


def _vers_rgb_64(img, bgr=False):
    """Toute image acceptée par vers_pil -> ndarray (64, 64, 3) flottant dans [0, 1]."""
    from PIL import Image
    pil = vers_pil(img, bgr=bgr).resize((TAILLE_IMAGE, TAILLE_IMAGE), Image.BILINEAR)
    return np.asarray(pil, dtype=np.float64) / 255.0


def _cases(a, n=GRILLE):
    """(64, 64[, c]) -> (n, n, 64/n, 64/n[, c]) : découpage en grille."""
    p = a.shape[0] // n
    if a.ndim == 2:
        return a.reshape(n, p, n, p).transpose(0, 2, 1, 3)
    return a.reshape(n, p, n, p, a.shape[2]).transpose(0, 2, 1, 3, 4)


def _hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    v = rgb.max(axis=-1)
    mn = rgb.min(axis=-1)
    d = v - mn
    s = np.where(v > 1e-9, d / np.maximum(v, 1e-9), 0.0)
    h = np.zeros_like(v)
    ok = d > 1e-9
    dd = np.where(ok, d, 1.0)
    rm = ok & (v == r)
    gm = ok & (v == g) & ~rm
    bm = ok & ~rm & ~gm
    h = np.where(rm, ((g - b) / dd) % 6.0, h)
    h = np.where(gm, (b - r) / dd + 2.0, h)
    h = np.where(bm, (r - g) / dd + 4.0, h)
    return (h / 6.0) % 1.0, s, v


def vecteur_image(img, bgr=False):
    """Image -> vecteur (DIM_IMAGE,) : contours, ombres, différences de couleur, densités de couleur (voir l'en-tête)."""
    rgb = _vers_rgb_64(img, bgr=bgr)
    lum = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    sortie = []

    # contours : Sobel, orientation non signée (un contour est le même dans les deux sens), pondérée par la force
    p = np.pad(lum, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    mag = np.hypot(gx, gy) / 4.0                                     # ≈ [0, 1.41]
    ang = np.mod(np.arctan2(gy, gx), math.pi)
    cl = np.minimum((ang / math.pi * N_ORIENT).astype(int), N_ORIENT - 1)
    cm, cc = _cases(mag), _cases(cl)
    for i in range(GRILLE):
        for j in range(GRILLE):
            h = np.bincount(cc[i, j].ravel(), weights=cm[i, j].ravel(), minlength=N_ORIENT)
            n = float(np.linalg.norm(h))
            sortie.extend((h / n if n > 1e-9 else h).tolist())
    dens = np.clip(cm.mean(axis=(2, 3)) * 2.0, 0.0, 1.0)
    sortie.extend(dens.ravel().tolist())
    sortie.append(float(np.clip(mag.mean() * 2.0, 0.0, 1.0)))

    # ombres : luminance par case, statistiques, histogramme, sens de l'éclairage
    sortie.extend(_cases(lum).mean(axis=(2, 3)).ravel().tolist())
    q = np.percentile(lum, [10, 50, 90])
    sortie.extend([float(lum.mean()), float(min(1.0, lum.std() * 2.0)), float(q[0]), float(q[1]), float(q[2])])
    hl, _ = np.histogram(lum, bins=N_LUM, range=(0.0, 1.0))
    sortie.extend((hl / lum.size).tolist())
    moitie = TAILLE_IMAGE // 2
    sortie.append(float(np.clip(0.5 + (lum[:, moitie:].mean() - lum[:, :moitie].mean()), 0.0, 1.0)))
    sortie.append(float(np.clip(0.5 + (lum[:moitie].mean() - lum[moitie:].mean()), 0.0, 1.0)))

    # différences de couleur : axes opposés rouge-vert / jaune-bleu (bornés à [-1, 1] puis ramenés à [0, 1])
    rg = rgb[..., 0] - rgb[..., 1]
    yb = (rgb[..., 0] + rgb[..., 1]) / 2.0 - rgb[..., 2]
    mrg, myb = _cases(rg).mean(axis=(2, 3)), _cases(yb).mean(axis=(2, 3))
    sortie.extend(((mrg.ravel() + 1.0) / 2.0).tolist())
    sortie.extend(((myb.ravel() + 1.0) / 2.0).tolist())
    sortie.append(float(min(1.0, mrg.std() * 2.0)))
    sortie.append(float(min(1.0, myb.std() * 2.0)))
    sortie.append(float(min(1.0, (np.abs(np.diff(rg, axis=1)).mean() + np.abs(np.diff(rg, axis=0)).mean()) * 2.0)))
    sortie.append(float(min(1.0, (np.abs(np.diff(yb, axis=1)).mean() + np.abs(np.diff(yb, axis=0)).mean()) * 2.0)))

    # densité de couleurs : teintes pondérées par saturation × valeur (le gris n'a pas de teinte), saturations
    h, s, v = _hsv(rgb)
    poids = s * v
    ht = np.bincount(np.minimum((h * N_TEINTES).astype(int), N_TEINTES - 1).ravel(), weights=poids.ravel(), minlength=N_TEINTES)
    tot = float(ht.sum())
    ht = ht / tot if tot > 1e-9 else ht
    sortie.extend(ht.tolist())
    hs, _ = np.histogram(s, bins=N_SATURATIONS, range=(0.0, 1.0))
    sortie.extend((hs / s.size).tolist())
    nz = ht[ht > 1e-12]
    sortie.append(float(-(nz * np.log(nz)).sum() / math.log(N_TEINTES)) if nz.size else 0.0)

    vec = np.asarray(sortie, dtype=np.float64)
    if vec.size != DIM_IMAGE or not np.all(np.isfinite(vec)):
        raise VectorisationError(_gt("rg5.msg.013") % vec.size)
    return vec


# ── SON ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _bandes(rate, n_fft):
    """Bornes (indices de bins) de N_BANDES bandes log-espacées entre FREQ_MIN_HZ et min(FREQ_MAX_HZ, Nyquist)."""
    haut = min(FREQ_MAX_HZ, rate / 2.0 - 1.0)
    bas = min(FREQ_MIN_HZ, haut / 4.0)
    bords = np.geomspace(max(bas, 1.0), max(haut, 2.0), N_BANDES + 1)
    idx = np.round(bords / (rate / n_fft)).astype(int)
    for k in range(1, idx.size):                       # chaque bande contient au moins un bin
        idx[k] = max(idx[k], idx[k - 1] + 1)
    return np.minimum(idx, n_fft // 2 + 1)


def vecteur_audio(onde, rate):
    """Onde mono (flottants dans [-1, 1]) + fréquence d'échantillonnage -> vecteur (DIM_AUDIO,) (voir l'en-tête)."""
    x = np.asarray(onde, dtype=np.float64).ravel()
    rate = float(rate)
    if x.size == 0 or rate < 2000:
        raise VectorisationError(_gt("rg5.msg.011"))
    x = np.nan_to_num(x[: int(DUREE_MAX_S * rate)])
    taille = max(64, int(0.025 * rate))
    pas = max(16, int(0.010 * rate))
    if x.size < taille:
        x = np.pad(x, (0, taille - x.size))
    n_trames = 1 + (x.size - taille) // pas
    ind = np.arange(taille)[None, :] + pas * np.arange(n_trames)[:, None]
    trames = x[ind]
    fen = np.hanning(taille)
    n_fft = 1 << int(math.ceil(math.log2(taille)))
    spec = np.abs(np.fft.rfft(trames * fen, n=n_fft, axis=1)) ** 2 / float(fen.sum() ** 2)     # puissance normalisée
    bords = _bandes(rate, n_fft)
    bandes = np.stack([spec[:, bords[k]:bords[k + 1]].sum(axis=1) for k in range(N_BANDES)], axis=1)
    lb = np.log10(bandes + 1e-10)
    lb01 = np.clip((lb + 10.0) / 10.0, 0.0, 1.0)                  # 1e-10 → 0, 1 → 1
    sortie = list(lb01.mean(axis=0)) + list(np.clip(lb01.std(axis=0) * 2.0, 0.0, 1.0))

    freqs = np.arange(spec.shape[1]) * (rate / n_fft)
    somme = spec.sum(axis=1) + 1e-12
    cent = (spec * freqs).sum(axis=1) / somme
    etal = np.sqrt(((freqs[None, :] - cent[:, None]) ** 2 * spec).sum(axis=1) / somme)
    cumul = np.cumsum(spec, axis=1)
    roll = freqs[np.minimum((cumul < 0.85 * somme[:, None]).sum(axis=1), freqs.size - 1)]
    plat = np.exp(np.log(spec + 1e-12).mean(axis=1)) / (spec.mean(axis=1) + 1e-12)
    nyq = rate / 2.0
    sortie += [float(cent.mean() / nyq), float(etal.mean() / nyq), float(roll.mean() / nyq), float(np.clip(plat.mean(), 0.0, 1.0))]

    zcr = (np.diff(np.signbit(trames), axis=1) != 0).mean(axis=1)
    rms = np.sqrt((trames ** 2).mean(axis=1))
    sortie += [float(zcr.mean()), float(min(1.0, zcr.std() * 4.0))]
    sortie += [float(min(1.0, rms.mean() * 4.0)), float(min(1.0, rms.std() * 8.0)), float(min(1.0, rms.max() * 2.0))]
    if n_trames > 1:
        norme = spec / (np.sqrt((spec ** 2).sum(axis=1, keepdims=True)) + 1e-12)
        flux = float(np.sqrt((np.diff(norme, axis=0) ** 2).sum(axis=1)).mean() / math.sqrt(2.0))
    else:
        flux = 0.0
    sortie.append(min(1.0, flux))

    segs = np.array_split(rms, N_ENVELOPPE)
    env = np.array([s.mean() if s.size else 0.0 for s in segs])
    pic = float(env.max())
    sortie += (env / pic if pic > 1e-9 else env).tolist()

    # cepstre : DCT-II des bandes (log) moyennes → timbre, indépendant du niveau global
    k = np.arange(N_BANDES)
    base = np.cos(math.pi / N_BANDES * (k[None, :] + 0.5) * np.arange(1, N_CEPSTRE + 1)[:, None])
    cep = base @ lb.mean(axis=0) / N_BANDES
    sortie += np.clip(0.5 + cep / 10.0, 0.0, 1.0).tolist()

    vec = np.asarray(sortie, dtype=np.float64)
    if vec.size != DIM_AUDIO or not np.all(np.isfinite(vec)):
        raise VectorisationError(_gt("rg5.msg.014") % vec.size)
    return vec


def vecteur_audio_pcm16(pcm, rate):
    """PCM 16 bits little-endian mono (ce que produisent les flux micro) -> vecteur."""
    brut = bytes(pcm)
    brut = brut[: len(brut) - len(brut) % 2]
    return vecteur_audio(np.frombuffer(brut, dtype="<i2").astype(np.float64) / 32768.0, rate)


def vecteur(type_media, donnee, **kw):
    """Point d'entrée commun : type_media "image" (PIL/ndarray) ou "audio" (onde, rate=...)."""
    if type_media == "image":
        return vecteur_image(donnee, bgr=bool(kw.get("bgr")))
    if type_media == "audio":
        return vecteur_audio(donnee, kw["rate"])
    raise VectorisationError(_gt("rg5.msg.012") % type_media)


def descripteur(type_media):
    """Ce qu'on mémorise avec le modèle : de quoi savoir quelle recette a produit ses entrées."""
    return {"version": VERSION_VECTEURS, "type": type_media, "dim": dimension(type_media)}
