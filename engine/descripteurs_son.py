"""Descripteurs sonores, exposés dans la page Analyse des médias (source « engine.descripteurs_son »). NumPy + SciPy uniquement (rien à installer en plus).
Chaque fonction publique prend le son sous forme de signal mono flottant (variable `mono`, valeurs entre -1 et 1) et sa fréquence d'échantillonnage (`sr`) ;
`correlation_stereo` prend `data` (toutes les voies). Elles renvoient des courbes dans le temps (1-D), des cartes temps × fréquence (2-D) ou un dictionnaire de mesures.
Rien n'est écrit sur disque ; les paramètres facultatifs sont réglables dans la zone d'arguments (ex. : mfcc(mono, sr, n_mfcc=20))."""
import numpy as np
from numpy.lib import stride_tricks as _st
from scipy import signal

_MAX_TRAMES = 4000   # au-delà, le pas est élargi : la durée de l'analyse reste bornée quelle que soit la longueur du son


def _mono(x):
    x = np.asarray(x, float)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if x.size < 8:
        raise ValueError("trop_court")
    return np.nan_to_num(x)


def _trames(x, n, pas):   # (nb_trames, n) ; pas élargi si trop de trames ; son plus court qu'une trame -> complété par des zéros
    x = _mono(x)
    n, pas = int(n), max(1, int(pas))
    if x.size < n:
        x = np.pad(x, (0, n - x.size))
    pas = max(pas, int(np.ceil((x.size - n) / _MAX_TRAMES)) or 1)
    return _st.sliding_window_view(x, n)[::pas], pas


def _spec(x, n_fft, pas):   # amplitude (nb_trames, n_fft/2+1) avec fenêtre de Hann, + pas réel
    t, pas = _trames(x, n_fft, pas)
    return np.abs(np.fft.rfft(t * np.hanning(int(n_fft)), axis=1)), pas


def _db(v, ref=None):
    v = np.asarray(v, float)
    return 20 * np.log10(np.maximum(v, 1e-12) / (ref if ref else 1.0))


def _mel_banque(sr, n_fft, n_mels, fmin=0.0, fmax=None):
    fmax = fmax or sr / 2
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    imel = lambda m: 700 * (10 ** (m / 2595) - 1)
    pts = imel(np.linspace(mel(fmin), mel(fmax), int(n_mels) + 2))
    f = np.fft.rfftfreq(int(n_fft), 1 / sr)
    b = np.zeros((int(n_mels), f.size))
    for i in range(int(n_mels)):
        lo, mi, hi = pts[i], pts[i + 1], pts[i + 2]
        b[i] = np.maximum(0, np.minimum((f - lo) / max(mi - lo, 1e-9), (hi - f) / max(hi - mi, 1e-9)))
    return b


# ── mesures globales ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def caracteristiques(mono, sr):
    """Fiche de synthèse du son : durée, niveau efficace (RMS) et crête en dBFS, facteur de crête, décalage continu, taux de passages par zéro, centroïde / roll-off / platitude spectraux, taux d'écrêtage, part de silence."""
    x = _mono(mono)
    rms, crete = float(np.sqrt(np.mean(x ** 2))), float(np.max(np.abs(x)))
    S, _ = _spec(x, 2048, 1024)
    m = S.mean(axis=0) + 1e-12
    f = np.fft.rfftfreq(2048, 1 / sr)
    c = np.cumsum(m ** 2) / np.sum(m ** 2)
    r = np.sqrt(np.mean(_trames(x, 1024, 512)[0] ** 2, axis=1))
    return {"duree_s": x.size / sr, "rms_dbfs": float(_db(rms)), "crete_dbfs": float(_db(crete)), "facteur_crete_db": float(_db(crete / (rms or 1e-12))),
            "decalage_continu": float(x.mean()), "passages_par_zero_par_s": float(np.sum(np.diff(np.signbit(x)) != 0) / (x.size / sr)),
            "centroide_hz": float(np.sum(f * m) / np.sum(m)), "rolloff_85_hz": float(f[min(np.searchsorted(c, .85), f.size - 1)]),
            "platitude_spectrale": float(np.exp(np.mean(np.log(m))) / np.mean(m)), "taux_ecretage_pct": float(100 * np.mean(np.abs(x) >= 0.999 * (crete or 1)) if crete >= .99 else 0.0),
            "part_silence_pct": float(100 * np.mean(_db(r) < -50))}


def statistiques_amplitude(mono):
    """Distribution des amplitudes : moyenne, écart-type, asymétrie (skewness), aplatissement (kurtosis), quantiles, entropie de l'histogramme (un son très bruité ou saturé se voit ici)."""
    x = _mono(mono)
    h, _ = np.histogram(x, bins=256)
    p = h[h > 0] / h.sum()
    s = x.std() or 1e-12
    q = np.quantile(x, [.01, .05, .25, .5, .75, .95, .99])
    return {"moyenne": float(x.mean()), "ecart_type": float(x.std()), "asymetrie": float(np.mean(((x - x.mean()) / s) ** 3)), "kurtosis": float(np.mean(((x - x.mean()) / s) ** 4) - 3),
            "quantiles_1_5_25_50_75_95_99": [float(v) for v in q], "entropie_bits": float(-np.sum(p * np.log2(p)))}


def volume_sonore_lufs(mono, sr):
    """Sonie intégrée en LUFS (norme ITU-R BS.1770 : filtre K, blocs de 400 ms, double seuil -70 LUFS puis -10 LU) + sonie à court terme maximale (3 s). Pour un son mono."""
    x = _mono(mono)
    def bq(b, a):
        return np.array(b) / a[0], np.array(a) / a[0]
    A, w0 = 10 ** (3.999843853973347 / 40), 2 * np.pi * 1681.9744509555319 / sr
    al, c, sa = np.sin(w0) / (2 * 0.7071752369554196), np.cos(w0), 2 * np.sqrt(10 ** (3.999843853973347 / 40))
    sa *= al
    b1, a1 = bq([A * ((A + 1) + (A - 1) * c + sa), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sa)],
                [(A + 1) - (A - 1) * c + sa, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sa])
    w1 = 2 * np.pi * 38.13547087613982 / sr
    al2, c2 = np.sin(w1) / (2 * 0.5003270373253953), np.cos(w1)
    b2, a2 = bq([(1 + c2) / 2, -(1 + c2), (1 + c2) / 2], [1 + al2, -2 * c2, 1 - al2])
    y = signal.lfilter(b2, a2, signal.lfilter(b1, a1, x))
    n, pas = int(.4 * sr), int(.1 * sr)
    if y.size < n:
        raise ValueError("trop_court")
    z = np.mean(_st.sliding_window_view(y, n)[::pas] ** 2, axis=1)
    l = -0.691 + 10 * np.log10(z + 1e-20)
    g = z[l > -70]
    if not g.size:
        return {"lufs_integre": None, "lufs_court_terme_max": None}
    rel = -0.691 + 10 * np.log10(g.mean()) - 10
    g2 = z[l > max(-70, rel)]
    nc = int(3 * sr)
    ct = None
    if y.size >= nc:
        zc = np.mean(_st.sliding_window_view(y, nc)[::pas] ** 2, axis=1)
        ct = float(-0.691 + 10 * np.log10(zc.max() + 1e-20))
    return {"lufs_integre": float(-0.691 + 10 * np.log10(g2.mean())) if g2.size else None, "lufs_court_terme_max": ct}


def bruit_snr(mono, sr, trame_ms=50):
    """Estimation du rapport signal/bruit (dB) : énergie des trames fortes (90e centile) contre trames faibles (10e centile) ; renvoie aussi le plancher de bruit en dBFS."""
    t, _ = _trames(mono, max(16, int(sr * trame_ms / 1000)), max(8, int(sr * trame_ms / 2000)))
    e = np.mean(t ** 2, axis=1) + 1e-20
    s, b = np.percentile(e, 90), np.percentile(e, 10)
    return {"snr_db": float(10 * np.log10(s / b)), "plancher_bruit_dbfs": float(10 * np.log10(b)), "niveau_signal_dbfs": float(10 * np.log10(s))}


def ecretage(mono, seuil=0.99):
    """Détecte la saturation : nombre d'échantillons au-delà de `seuil` (fraction de la pleine échelle) et séries consécutives (≥ 3 échantillons = écrêtage probable)."""
    x = np.abs(_mono(mono)) >= float(seuil)
    d = np.diff(np.concatenate([[0], x.astype(int), [0]]))
    long = (np.nonzero(d == -1)[0] - np.nonzero(d == 1)[0])
    return {"echantillons_satures": int(x.sum()), "pct": float(100 * x.mean()), "series_consecutives": int(np.sum(long >= 3)), "plus_longue_serie": int(long.max()) if long.size else 0}


def silences(mono, sr, seuil_db=-40, duree_min_ms=200, trame_ms=20):
    """Plages de silence : liste de (début_s, fin_s) où le niveau RMS reste sous `seuil_db` (dBFS) pendant au moins `duree_min_ms`. Renvoie aussi la part de silence."""
    x = _mono(mono)
    n = max(8, int(sr * trame_ms / 1000))
    t, pas = _trames(x, n, n)
    bas = _db(np.sqrt(np.mean(t ** 2, axis=1))) < float(seuil_db)
    d = np.diff(np.concatenate([[0], bas.astype(int), [0]]))
    deb, fin = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    pl = [(float(a * pas / sr), float(b * pas / sr)) for a, b in zip(deb, fin) if (b - a) * pas / sr * 1000 >= float(duree_min_ms)]
    return {"plages_s": pl, "nombre": len(pl), "part_silence_pct": float(100 * sum(b - a for a, b in pl) / (x.size / sr))}


def correlation_stereo(data):
    """Image stéréo (variable `data`, 2 voies ou plus) : corrélation gauche/droite (+1 mono, 0 large, -1 en opposition de phase), écart de niveau G/D en dB, rapport milieu/côtés, compatibilité mono (perte en dB si on additionne les voies)."""
    d = np.asarray(data, float)
    if d.ndim < 2 or d.shape[1] < 2:
        raise ValueError("stereo")
    g, r = d[:, 0], d[:, 1]
    eg, er = np.sum(g ** 2) + 1e-20, np.sum(r ** 2) + 1e-20
    m, s = (g + r) / 2, (g - r) / 2
    return {"correlation": float(np.sum(g * r) / np.sqrt(eg * er)), "ecart_niveau_db_g_moins_d": float(10 * np.log10(eg / er)), "milieu_sur_cotes_db": float(10 * np.log10((np.sum(m ** 2) + 1e-20) / (np.sum(s ** 2) + 1e-20))),
            "perte_mono_db": float(10 * np.log10((np.sum(m ** 2) + 1e-20) / ((eg + er) / 2)))}


# ── courbes dans le temps (1-D) ────────────────────────────────────────────────────────────────────────────────────────────────

def rms_trame(mono, sr, trame_ms=25, pas_ms=10):
    """Niveau efficace (RMS) par trame, en dBFS : l'évolution de l'intensité dans le temps."""
    t, _ = _trames(mono, max(8, int(sr * trame_ms / 1000)), max(1, int(sr * pas_ms / 1000)))
    return _db(np.sqrt(np.mean(t ** 2, axis=1)))


def passages_par_zero(mono, sr, trame_ms=25, pas_ms=10):
    """Taux de passages par zéro par trame (0 à 1) : élevé pour le bruit et les sifflantes, bas pour les sons graves et tonals."""
    t, _ = _trames(mono, max(8, int(sr * trame_ms / 1000)), max(1, int(sr * pas_ms / 1000)))
    return np.mean(np.diff(np.signbit(t), axis=1) != 0, axis=1)


def enveloppe_hilbert(mono):
    """Enveloppe d'amplitude instantanée (module du signal analytique de Hilbert) : contour du son, attaques et extinctions."""
    return np.abs(signal.hilbert(_mono(mono)))


def centroide_spectral(mono, sr, n_fft=2048, pas=512):
    """Centroïde spectral (Hz) par trame : le « centre de gravité » des fréquences, mesure de la brillance du son."""
    S, _ = _spec(mono, n_fft, pas)
    return (S * np.fft.rfftfreq(int(n_fft), 1 / sr)).sum(axis=1) / (S.sum(axis=1) + 1e-12)


def largeur_bande_spectrale(mono, sr, n_fft=2048, pas=512):
    """Largeur de bande spectrale (Hz) par trame : écart-type des fréquences autour du centroïde (son pur = étroit, bruit = large)."""
    S, _ = _spec(mono, n_fft, pas)
    f = np.fft.rfftfreq(int(n_fft), 1 / sr)
    c = (S * f).sum(axis=1) / (S.sum(axis=1) + 1e-12)
    return np.sqrt((S * (f[None] - c[:, None]) ** 2).sum(axis=1) / (S.sum(axis=1) + 1e-12))


def rolloff_spectral(mono, sr, pourcentage=0.85, n_fft=2048, pas=512):
    """Roll-off spectral (Hz) par trame : fréquence sous laquelle se trouve `pourcentage` de l'énergie."""
    S, _ = _spec(mono, n_fft, pas)
    c = np.cumsum(S ** 2, axis=1)
    i = (c < float(pourcentage) * c[:, -1:]).sum(axis=1)
    return np.fft.rfftfreq(int(n_fft), 1 / sr)[np.minimum(i, S.shape[1] - 1)]


def platitude_spectrale(mono, sr, n_fft=2048, pas=512):
    """Platitude spectrale par trame (0 à 1) : proche de 1 pour un bruit blanc, proche de 0 pour un son tonal (notes, voix voisée)."""
    S, _ = _spec(mono, n_fft, pas)
    P = S ** 2 + 1e-12
    return np.exp(np.mean(np.log(P), axis=1)) / P.mean(axis=1)


def entropie_spectrale(mono, sr, n_fft=2048, pas=512):
    """Entropie spectrale par trame, normalisée (0 à 1) : désordre de la répartition de l'énergie en fréquence."""
    S, _ = _spec(mono, n_fft, pas)
    p = S ** 2 + 1e-12
    p /= p.sum(axis=1, keepdims=True)
    return -np.sum(p * np.log2(p), axis=1) / np.log2(p.shape[1])


def flux_spectral(mono, sr, n_fft=2048, pas=512):
    """Flux spectral par trame : variation du spectre d'une trame à la suivante (pics = attaques, changements de note, chocs)."""
    S, _ = _spec(mono, n_fft, pas)
    L = np.log1p(S * 100)
    return np.concatenate([[0], np.sqrt(np.sum(np.maximum(0, np.diff(L, axis=0)) ** 2, axis=1))])


def hauteur_yin(mono, sr, fmin=60, fmax=1000, trame=2048, pas=512, seuil=0.15):
    """Fréquence fondamentale (Hz) par trame, méthode YIN (différence normalisée) : hauteur de la voix ou de la note ; 0 = trame non voisée (bruit, silence)."""
    t, _ = _trames(mono, trame, pas)
    n = int(trame)
    t = t - t.mean(axis=1, keepdims=True)
    tmax = min(int(sr / float(fmin)), n // 2 - 1)
    tmin = max(2, int(sr / float(fmax)))
    F = np.fft.rfft(t, 2 * n, axis=1)
    ac = np.fft.irfft(F * np.conj(F), axis=1)[:, :tmax + 1]
    cs = np.concatenate([np.zeros((t.shape[0], 1)), np.cumsum(t ** 2, axis=1)], axis=1)
    tau = np.arange(tmax + 1)
    energie = cs[:, n:n + 1]   # énergie totale de la trame
    dec = cs[:, n:n + 1] - cs[:, tau]   # énergie de x[tau:] pour chaque retard
    deb = cs[:, n - tau]                # énergie de x[:n-tau]
    diff = deb + dec - 2 * ac
    diff[:, 0] = 0
    cm = diff[:, 1:] * tau[1:] / (np.cumsum(diff[:, 1:], axis=1) + 1e-12)
    cm = np.concatenate([np.ones((t.shape[0], 1)), cm], axis=1)
    f0 = np.zeros(t.shape[0])
    for i in range(t.shape[0]):
        c = cm[i, tmin:]
        bas = np.nonzero(c < float(seuil))[0]
        k = bas[0] if bas.size else int(np.argmin(c))
        while bas.size and k + 1 < c.size and c[k + 1] < c[k]:   # descend jusqu'au minimum local
            k += 1
        if c[k] < 0.35 and energie[i, 0] > 1e-8:
            f0[i] = sr / (k + tmin)
    return f0


# ── cartes temps × fréquence (2-D) ─────────────────────────────────────────────────────────────────────────────────────────────

def melspectrogramme(mono, sr, n_mels=64, n_fft=2048, pas=512):
    """Mélspectrogramme en dB (fréquences en échelle de Mel, proche de l'oreille) : lignes = bandes de Mel, colonnes = temps."""
    S, _ = _spec(mono, n_fft, pas)
    M = _mel_banque(sr, n_fft, n_mels) @ (S.T ** 2)
    return 10 * np.log10(np.maximum(M, 1e-10) / max(M.max(), 1e-10))


def mfcc(mono, sr, n_mfcc=13, n_mels=40, n_fft=2048, pas=512):
    """MFCC (coefficients cepstraux en échelle de Mel) : empreinte du timbre, base de la reconnaissance vocale et de la classification de sons. Lignes = coefficients, colonnes = temps."""
    from scipy.fft import dct
    M = melspectrogramme(mono, sr, n_mels, n_fft, pas)
    return dct(M, type=2, axis=0, norm="ortho")[:max(1, int(n_mfcc))]


def chromagramme(mono, sr, n_fft=4096, pas=1024):
    """Chromagramme : énergie des 12 notes (do, do#, ré…) au fil du temps, repliées sur une octave. Révèle la tonalité et les accords."""
    S, _ = _spec(mono, n_fft, pas)
    f = np.fft.rfftfreq(int(n_fft), 1 / sr)
    ok = (f >= 27.5) & (f <= min(5000, sr / 2))
    pc = np.round(69 + 12 * np.log2(np.where(ok, f, 440) / 440)).astype(int) % 12
    C = np.zeros((12, f.size))
    C[pc[ok], np.nonzero(ok)[0]] = 1
    out = C @ (S.T ** 2)
    return out / (out.max(axis=0, keepdims=True) + 1e-12)


def bandes_tiers_octave(mono, sr):
    """Niveau (dB) par bande de tiers d'octave (de 25 Hz à 20 kHz) à partir de la densité spectrale de Welch : la courbe d'analyse acoustique classique."""
    f, p = signal.welch(_mono(mono), sr, nperseg=min(8192, max(256, int(_mono(mono).size))))
    cs = [c for c in 1000 * 2 ** (np.arange(-16, 14) / 3) if c < sr / 2 / 2 ** (1 / 6)]
    niv = [10 * np.log10(p[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].sum() + 1e-20) for c in cs]
    return {"freq_hz": np.array(cs), "niveau_db": np.array(niv)}


def spectre_moyen(mono, sr, n_fft=4096):
    """Densité spectrale de puissance moyenne (méthode de Welch, en dB) : le spectre global du son, plus stable qu'une simple FFT."""
    f, p = signal.welch(_mono(mono), sr, nperseg=min(int(n_fft), int(_mono(mono).size)))
    return {"freq_hz": f, "niveau_db": 10 * np.log10(np.maximum(p, 1e-20) / max(p.max(), 1e-20))}


def pics_spectraux(mono, sr, n=10, n_fft=8192):
    """Les `n` raies dominantes du spectre : fréquences (Hz) et niveaux (dB relatifs) — identifie ronflement du secteur (50/60 Hz), sifflements, moteurs, tonalités."""
    x = _mono(mono)
    S, _ = _spec(x, min(int(n_fft), x.size), max(1, int(n_fft) // 2))
    m = S.mean(axis=0)
    f = np.fft.rfftfreq(2 * (m.size - 1), 1 / sr)
    i, _ = signal.find_peaks(m, prominence=m.max() * 0.02)
    i = i[np.argsort(m[i])[::-1][:int(n)]]
    return {"freq_hz": [float(f[k]) for k in i], "niveau_db": [float(_db(m[k], m.max())) for k in i]}


def distorsion_harmonique(mono, sr, fondamentale=None, n_harmoniques=8):
    """Distorsion harmonique totale (THD) : rapport entre l'énergie des harmoniques et celle de la fondamentale (cherchée automatiquement si non fournie). Utile pour un son pur / de test."""
    x = _mono(mono)
    n = min(x.size, 1 << 18)
    S = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    f = np.fft.rfftfreq(n, 1 / sr)
    k0 = int(np.argmax(S[1:])) + 1 if fondamentale is None else int(round(float(fondamentale) * n / sr))
    pic = lambda k: S[max(0, k - 3):k + 4].max() if k < S.size else 0.0
    h1 = pic(k0) or 1e-12
    hs = [pic(k0 * j) for j in range(2, int(n_harmoniques) + 2) if k0 * j < S.size - 3]
    thd = float(np.sqrt(np.sum(np.square(hs))) / h1)
    return {"fondamentale_hz": float(f[k0]), "thd_pct": 100 * thd, "thd_db": float(_db(thd)), "harmoniques_db": [float(_db(h, h1)) for h in hs]}


# ── rythme ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def detection_attaques(mono, sr, sensibilite=1.0, n_fft=2048, pas=512):
    """Instants (secondes) des attaques / départs de notes : pics du flux spectral au-dessus de la moyenne + `sensibilite` × écart-type (plus petit = plus d'attaques)."""
    fl = flux_spectral(mono, sr, n_fft, pas)
    _, pas = _trames(mono, n_fft, pas)
    seuil = fl.mean() + float(sensibilite) * fl.std()
    i, _ = signal.find_peaks(fl, height=seuil, distance=max(1, int(0.05 * sr / pas)))
    return i * pas / sr


def tempo_bpm(mono, sr, bpm_min=40, bpm_max=220):
    """Tempo estimé en battements par minute (autocorrélation du flux spectral) + les 3 candidats les plus probables avec leur force."""
    fl = flux_spectral(mono, sr, 1024, 256)
    _, pas = _trames(mono, 1024, 256)
    fl = fl - fl.mean()
    ac = np.correlate(fl, fl, "full")[fl.size - 1:]
    lo, hi = int(60 * sr / pas / float(bpm_max)), int(60 * sr / pas / float(bpm_min))
    hi = min(hi, ac.size - 1)
    if hi <= lo + 2:
        raise ValueError("trop_court")
    seg = ac[lo:hi + 1] / (ac[0] or 1)
    i, _ = signal.find_peaks(seg)
    i = i[np.argsort(seg[i])[::-1][:3]] if i.size else np.array([int(np.argmax(seg))])
    bpm = lambda k: float(60 * sr / pas / (k + lo))
    return {"bpm": bpm(i[0]), "candidats": [{"bpm": bpm(k), "force": float(seg[k])} for k in i]}


def autocorrelation(mono, sr, retard_max_ms=50):
    """Autocorrélation normalisée du son (jusqu'à `retard_max_ms`) : un pic à un retard donné trahit une périodicité (hauteur d'une note, écho, résonance). L'axe est en échantillons (retard en s = indice / sr)."""
    x = _mono(mono)[:1 << 18]
    x = x - x.mean()
    F = np.fft.rfft(x, 2 * x.size)
    ac = np.fft.irfft(F * np.conj(F))[:x.size]
    return (ac / (ac[0] or 1))[:max(2, min(x.size, int(float(retard_max_ms) * sr / 1000)))]


def cepstre(mono, n_max=2000):
    """Cepstre réel (log du spectre, retransformé) sur les `n_max` premiers coefficients : sépare l'excitation (hauteur) du conduit (timbre), repère les échos."""
    x = _mono(mono)[:1 << 16]
    return np.fft.irfft(np.log(np.abs(np.fft.rfft(x * np.hanning(x.size))) + 1e-12))[:int(n_max)]


# ── fonctions qui s'ajoutent seulement si librosa est installé (pip install librosa) ────────────────────────────────────────────
import importlib.util as _iu

if _iu.find_spec("librosa"):
    def hauteur_pyin(mono, sr, fmin=60, fmax=1000):
        """Fréquence fondamentale (Hz) par trame, méthode pYIN (probabiliste, plus robuste que YIN dans le bruit) : 0 = non voisé. Demande librosa."""
        import librosa
        f0, _v, _p = librosa.pyin(_mono(mono).astype(np.float32), fmin=float(fmin), fmax=float(fmax), sr=sr)
        return np.nan_to_num(f0)

    def cqt_db(mono, sr, n_bins=84, bins_par_octave=12):
        """Transformée à Q constant en dB (résolution musicale : une ligne par demi-ton) : lignes = notes, colonnes = temps. Demande librosa."""
        import librosa
        C = np.abs(librosa.cqt(_mono(mono).astype(np.float32), sr=sr, n_bins=int(n_bins), bins_per_octave=int(bins_par_octave)))
        return 20 * np.log10(np.maximum(C, 1e-10) / max(C.max(), 1e-10))
