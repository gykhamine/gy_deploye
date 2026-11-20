"""Éléments PDF (reportlab) de l'Analyse des médias : synthèse, étapes appliquées, puis pour chaque résultat les mêmes courbes que l'écran, avec axes et légendes
(onde et spectre FFT en dB par son séparé, spectrogramme à l'endroit, une carte par composante). Ne dépend ni de Django ni des gabarits (testable seul) : le bandeau
de l'entreprise et le pied de page sont posés par reports/_part_006.py (mêmes PDF que le reste de la plateforme). Les textes viennent tous de `t` (traductions)."""
import base64, datetime, io, json
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, Spacer

LARGEUR_FIG, MAX_IMAGES_VIDEO = 7.2, 24


def _fig(dessiner, hauteur):   # figure matplotlib -> (PNG, rapport hauteur/largeur)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(LARGEUR_FIG, hauteur), dpi=130)
    dessiner(ax)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    b = io.BytesIO()
    fig.savefig(b, format="png")
    plt.close(fig)
    b.seek(0)
    return b, hauteur / LARGEUR_FIG


def _courbes(series, xlim=None, xlabel="", ylabel="", noms=None, zero=False):
    import numpy as np
    from engine.graphiques_degrade import trace_degrade
    def dessiner(ax):
        for k, s in enumerate(series):
            y = np.array([np.nan if v is None else v for v in s], float)
            # courbe = évolution : dégradé de sa couleur, du plus sombre (début) au plus clair (fin)
            trace_degrade(ax, np.linspace(*(xlim or (0, max(len(y) - 1, 1))), len(y)), y, "C%d" % (k % 10), lw=1, label=noms[k] if noms else None)
        ax.set_xlabel(xlabel, fontsize=8)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.grid(alpha=.3)
        if zero:   # amplitude : l'axe part de 0 (sinon un petit écart paraît énorme)
            ax.set_ylim(bottom=0)
        if noms:
            ax.legend(fontsize=7)
    return _fig(dessiner, 2.2)


def _carte(png, extent, xlabel, ylabel):   # spectrogramme : l'image serveur (ligne 0 = fréquence la plus basse) posée avec ses vraies bornes
    import matplotlib.pyplot as plt
    im = plt.imread(io.BytesIO(base64.b64decode(png)), format="png")
    def dessiner(ax):
        ax.imshow(im, aspect="auto", origin="lower", extent=extent)
        ax.set_xlabel(xlabel, fontsize=8)
        ax.set_ylabel(ylabel, fontsize=8)
    return _fig(dessiner, 3.0)


def _img(fig, largeur):
    return Image(fig[0], width=largeur, height=largeur * fig[1])


def _stats(x, S, t):
    c = " · ".join(f"{k} : {json.dumps(x[k])}" for k in ("shape", "dtype", "min", "max", "mean", "echelle") if x.get(k) is not None)
    return [Paragraph(escape(c), S["petit"])] if c else []


def _noeud(x, i, n, W, S, t, resume, son):   # i, n : rang et nombre de résultats frères (sons séparés, composantes…)
    nom = lambda: t("composante") + (f" {i + 1}/{n}" if n > 1 else "")
    tete = lambda s: [Paragraph(escape(s), S["Heading3"])]
    if isinstance(x, list):
        if len(x) == 3 and all(isinstance(e, dict) for e in x) and x[0].get("series") and x[1].get("series") and x[2].get("png") \
                and x[0]["shape"][0] == x[2]["shape"][0] and x[1]["shape"][0] == x[2]["shape"][1]:
            f, tm, z = x   # (fréquences, instants, spectrogramme) : axes réels
            return tete(t("spectre_db")) + [_img(_carte(z["png"], (tm["min"], tm["max"], f["min"], f["max"]), t("temps_s"), t("freq_hz")), W)] + _stats(z, S, t)
        return [e for k, v in enumerate(x) for e in _noeud(v, k, len(x), W, S, t, resume, son)]
    if isinstance(x, dict) and "spectre" in x:
        return tete(nom()) + [_img(_courbes([x["onde"]], (0, x["duree_ms"] / 1000), t("temps_s"), t("onde"), zero=True), W),
                              _img(_courbes([x["spectre"]], (0, x["fmax"]), t("freq_hz"), t("spectre_db")), W), Paragraph(escape(resume(x)[0]), S["petit"])]
    if isinstance(x, dict) and (x.get("png") or x.get("series")):
        out, ser = (tete(nom()) if n > 1 else []), x.get("series") or []
        if x.get("png"):
            h, w = x["shape"][:2]
            l = min(W, 12 * cm * w / h)
            out.append(Image(io.BytesIO(base64.b64decode(x["png"])), width=l, height=l * h / w))
        if ser and son and len(ser) > 1:   # son : chaque canal / ligne séparé = un son à part
            for k, s in enumerate(ser):
                out += tete(t("composante") + f" {k + 1}/{len(ser)}") + [_img(_courbes([s]), W)]
        elif ser:
            out.append(_img(_courbes(ser, noms=[f"#{k + 1}" for k in range(len(ser))] if len(ser) > 1 else None), W))
        return out + _stats(x, S, t)
    return [Paragraph(escape(json.dumps(x, ensure_ascii=False, default=str)[:1500]), S["petit"])]


def elements(res, etapes, synthese, titre, largeur, S, accent, t, resume, typ):
    """Flowables du rapport. res = la liste `resultats` de l'API (id, r | frames | erreur) ; etapes = textes « module.fonction(args) » ; S = feuille de styles."""
    N = S["Normal"]
    S = {"Normal": N, "Title": S["Title"], "petit": ParagraphStyle("PetitMedia", parent=N, fontSize=8, textColor=colors.HexColor("#6b7280")),
         **{k: ParagraphStyle(k + "Media", parent=S[k], keepWithNext=1) for k in ("Heading2", "Heading3")}}   # un titre reste avec son graphique
    out = [Paragraph(escape(titre), ParagraphStyle("TitreMedia", parent=S["Title"], fontSize=16, textColor=accent)),
           Paragraph(datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), S["petit"]), Spacer(1, .4 * cm)]
    if synthese:
        out += [Paragraph(f"<b>{escape(t('synthese'))}</b> {escape(synthese)}", S["Normal"]), Spacer(1, .3 * cm)]
    out += [Paragraph(escape(t("fonction")), S["Heading3"])] + [Paragraph(escape(e), S["petit"]) for e in etapes]
    for r in res:
        out.append(Paragraph(f"#{escape(str(r.get('id')))}", S["Heading2"]))
        if r.get("erreur"):
            out.append(Paragraph(escape(r["erreur"]), S["Normal"]))
        elif r.get("frames"):
            fr = r["frames"]
            v = [(f["i"], f["s"]) for f in fr if f.get("s") is not None]
            if len(v) > 1:
                out.append(_img(_courbes([[s for _i, s in v]], (v[0][0], v[-1][0]), "#"), largeur))
            for f in fr[::max(1, -(-len(fr) // MAX_IMAGES_VIDEO))]:
                out += [Paragraph(f"#{f['i']}", S["Heading3"])] + _noeud(f["r"], 0, 1, largeur, S, t, resume, False)
        else:
            out += _noeud(r.get("r"), 0, 1, largeur, S, t, resume, typ == "son")
    return out
