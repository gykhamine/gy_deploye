"""Tracé d'une ÉVOLUTION en dégradé d'UNE seule couleur, du plus sombre (début) au plus clair (fin).

Règle voulue pour tous les graphiques d'analyse : tout ce qui représente une évolution (courbe dans le temps, cumul, prévision,
sparkline…) prend la couleur de sa série en dégradé — même teinte et même saturation, seule la luminosité monte du début à la fin.
Même contrat que static/engine/js/gy_couleur.js (gyNuance : luminosité de 0,26 à 0,80, jamais de noir ni de blanc purs).

`trace_degrade` est injectée dans l'espace du bac à sable des graphiques BI sous le nom `gy_trace_degrade` (voir
executor._executer_dans_sandbox_graphique) : les scripts `def dessiner(dataframe, colonnes)` l'appellent à la place de `ax.plot`.
Elle est aussi utilisée directement par visualization.generer_graphiques.
"""
import colorsys
import math

L_SOMBRE = 0.26
L_CLAIR = 0.80
MAX_TRONCONS = 120   # nombre maximal de tracés par courbe (un tracé = une nuance) : pas de lenteur sur les longues séries
MAX_MARQUEURS = 1500   # nombre maximal de marqueurs dessinés par courbe : au-delà, des points régulièrement espacés (32 000 points = 32 000 marqueurs illisibles ET lents)


def nuance(couleur, t):
    """Nuance (r, g, b) de `couleur` (nom, '#rrggbb', 'C0'…) pour t de 0 (le plus sombre) à 1 (le plus clair)."""
    from matplotlib.colors import to_rgb
    r, g, b = to_rgb(couleur)
    teinte, _, saturation = colorsys.rgb_to_hls(r, g, b)
    t = min(1.0, max(0.0, float(t)))
    return colorsys.hls_to_rgb(teinte, L_SOMBRE + (L_CLAIR - L_SOMBRE) * t, saturation)


def trace_degrade(ax, x, y, couleur="C0", lw=1.8, marker=None, ms=4, label=None, linestyle="-", etape=False, zorder=2):
    """Trace y(x) sur `ax` en dégradé de `couleur`, dans l'ordre des points (début = nuance la plus sombre, fin = la plus claire).

    marker : repère chaque point de sa nuance ; etape=True : courbe en escalier (palier après chaque point) ; label : entrée de légende
    (nuance médiane). Les valeurs manquantes (NaN) laissent un trou, comme ax.plot."""
    import numpy as np
    xs = list(x)
    ys_np = np.asarray(y, dtype="float64")
    ys = ys_np.tolist()   # (avant : float(v) valeur par valeur)
    n = len(xs)
    if n == 0:
        return
    px, py = xs, ys
    if etape and n > 1:
        px, py = [xs[0]], [ys[0]]
        for i in range(1, n):
            px.append(xs[i]); py.append(ys[i - 1])
            px.append(xs[i]); py.append(ys[i])
    total = len(px) - 1
    if total < 1:
        ax.plot(px, py, color=nuance(couleur, 0.5), marker=marker or "o", ms=ms, lw=lw, zorder=zorder)
    else:
        par = max(1, int(math.ceil(total / float(MAX_TRONCONS))))
        for debut in range(0, total, par):
            fin = min(total, debut + par)
            ax.plot(px[debut:fin + 1], py[debut:fin + 1], color=nuance(couleur, (debut + fin) / 2.0 / total), lw=lw,
                    linestyle=linestyle, solid_capstyle="round", zorder=zorder)
    if marker and n > 1 and not etape:
        # OPTIMISATION : les nuances ne dépendent que de la position → table de 256 nuances calculée UNE fois (avant : colorsys +
        # matplotlib.to_rgb appelés n fois), et au plus MAX_MARQUEURS marqueurs, régulièrement répartis, première et dernière incluses.
        if n > MAX_MARQUEURS:
            pos = np.unique(np.linspace(0, n - 1, MAX_MARQUEURS).astype(int))
        else:
            pos = np.arange(n)
        palette = np.array([nuance(couleur, k / 255.0) for k in range(256)])
        couleurs = palette[np.rint(pos / float(n - 1) * 255).astype(int)]
        ax.scatter([xs[i] for i in pos], ys_np[pos], c=couleurs, s=ms ** 2, marker=marker, zorder=zorder + 1)
    if label is not None:
        ax.plot([], [], color=nuance(couleur, 0.5), lw=lw, linestyle=linestyle, marker=marker, ms=ms, label=label)
