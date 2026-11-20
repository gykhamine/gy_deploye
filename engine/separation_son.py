"""Séparation de sons, exposée dans la page Analyse des médias (source « engine.separation_son »). Chaque fonction prend seg (le son) et renvoie une LISTE de sons :
chacun est ensuite affiché avec sa propre onde, son propre spectre FFT, et peut être écouté à part. Rien n'est écrit sur disque."""
import numpy as np
from scipy import ndimage, signal


def _mono(seg):
    m = seg.set_channels(1).set_sample_width(2)
    return m, np.array(m.get_array_of_samples(), float)


def _sons(m, voies):   # tableaux -> sons (AudioSegment mono 16 bits)
    from pydub import AudioSegment
    return [AudioSegment(np.clip(v, -32768, 32767).astype(np.int16).tobytes(), sample_width=2, frame_rate=m.frame_rate, channels=1) for v in voies]


def _par_masques(seg, masques):   # masques (0..1) appliqués au spectrogramme complexe (STFT), puis reconstruction de chaque son
    m, a = _mono(seg)
    _f, _t, Z = signal.stft(a, m.frame_rate, nperseg=1024)
    return _sons(m, [signal.istft(Z * k, m.frame_rate, nperseg=1024)[1] for k in masques(np.abs(Z))])


def harmonique_percussif(seg, taille=17):
    """Sépare en 2 sons : [harmonique (voix, notes, tonalités continues), percussif (chocs, claquements, bruit bref)] par filtrage médian du spectrogramme."""
    def masques(S):
        H, P = ndimage.median_filter(S, (1, int(taille))) ** 2, ndimage.median_filter(S, (int(taille), 1)) ** 2
        return [H / (H + P + 1e-12), P / (H + P + 1e-12)]
    return _par_masques(seg, masques)


def composantes(seg, n=3, iterations=100):
    """Sépare en n sons (2 à 8) par factorisation non négative (NMF) du spectrogramme : chaque son a son profil de fréquences et son rythme propre (sources distinctes : voix, machine, fond…)."""
    n = max(2, min(int(n), 8))
    def masques(S):
        r = np.random.default_rng(0)
        W, H = r.random((S.shape[0], n)) + .1, r.random((n, S.shape[1])) + .1
        for _ in range(max(10, min(int(iterations), 300))):
            H *= (W.T @ S) / (W.T @ W @ H + 1e-12)
            W *= (S @ H.T) / (W @ H @ H.T + 1e-12)
        M = W @ H + 1e-12
        return [np.outer(W[:, k], H[k]) / M for k in range(n)]
    return _par_masques(seg, masques)


def par_bandes(seg, coupures=(300, 3000)):
    """Sépare en bandes de fréquences (Hz) : avec (300, 3000) -> graves (< 300), médiums (300 à 3000), aigus (> 3000)."""
    m, a = _mono(seg)
    nyq = m.frame_rate / 2
    c = [0] + sorted(float(x) for x in coupures if 0 < x < nyq) + [nyq]
    if len(c) < 3:
        raise ValueError("coupures")
    voies = []
    for lo, hi in zip(c, c[1:]):
        t, w = ("low", hi) if lo == 0 else ("high", lo) if hi == nyq else ("band", (lo, hi))
        voies.append(signal.sosfiltfilt(signal.butter(8, w, t, fs=m.frame_rate, output="sos"), a))
    return _sons(m, voies)
