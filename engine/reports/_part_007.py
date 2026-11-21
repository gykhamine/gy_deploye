"""RAPPORT GÉNÉRAL COMPLET — un seul PDF qui réunit TOUT ce qui existe comme analyse sur une ou plusieurs tables.

Portée choisie par l'utilisateur (voir views/_part_010.py) :
  * une table ;
  * toutes ses tables (son activité) ;
  * toute l'entreprise (super-administrateur seulement).

Pour CHAQUE table, dans cet ordre (chaque section est isolée : une section en échec donne une ligne d'explication, jamais un PDF cassé) :
  1. structure (origine, lignes, colonnes) ;  2. indicateurs KPI ;  3. dashboards = analyses exécutées, avec leurs graphiques ;
  4. skills exécutés ;  5. tableaux de bord BI, disposés comme à l'écran ;  6. statistiques descriptives (tableaux + graphiques + corrélations) ;
  7. ANOVA automatique ;  8. analyses factorielles (ACP / ACM / AFC) avec leurs graphiques ;  9. séries temporelles ;  10. géospatial ;
  11. SYNTHÈSE — toujours la dernière section : toutes les colonnes, des graphiques de synthèse, autant de pages que nécessaire (cochée seule : un PDF autonome).
Les calculs lisent les données ACTUELLES de la table avec les mêmes moteurs que les pages d'analyse (statistiques_donnees, analyses_auto…).
"""
from engine.i18n import t as _pt, tl as _ptl, tf as _ptf
import logging as _logging
import threading as _threading

from ._header import *  # noqa
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from ._part_004 import *  # noqa
from ..vue_groupe import nom_vue as _nom_vue, tables_generales as _tables_generales, table_generale as _table_generale  # noqa: E402 — un groupe de parties = UNE table
from ._palette import suite as _pal_suite, raz as _pal_raz  # noqa: E402 — palette des pages d'analyse (gy_couleur.js)
from ._page_garde import peintre_page_garde as _peintre_page_garde
from engine.i18n import t as _gt, dec as _dec  # noqa: E402

MAX_TABLES_RAPPORT = 50          # au-delà, le rapport s'arrête et le signale sur la page de garde
MAX_COLONNES_STATS = 40          # colonnes décrites au maximum par table
MAX_GRAPHIQUES_GRILLE = 6        # histogrammes / diagrammes par figure
_log = _logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Outils communs
# ─────────────────────────────────────────────────────────────
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _image_figure(fig, largeur, dpi=220):  # [graphiques-nets] 220 dpi : texte net à l'impression
    """Figure matplotlib → flowable ReportLab à la largeur demandée (hauteur selon les proportions de la figure)."""
    from reportlab.lib.utils import ImageReader
    plt = _plt()
    tampon = io.BytesIO()
    try:
        fig.savefig(tampon, format="png", dpi=dpi, bbox_inches="tight", pil_kwargs={"optimize": True})   # PNG compressé : un rapport net reste léger
    except TypeError:                                                                                   # matplotlib ancien : sans option
        tampon = io.BytesIO()
        fig.savefig(tampon, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    tampon.seek(0)
    iw, ih = ImageReader(tampon).getSize()   # proportions réelles de l'image enregistrée (bbox serrée)
    tampon.seek(0)
    img = RLImage(tampon, width=largeur, height=largeur * (ih / iw) if iw else largeur * 0.6)
    img.hAlign = "CENTER"
    return img


def _nombre(v, d=2):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return _pt("rg.oui") if v else _pt("rg.non")
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v != v:
            return "—"
        return f"{v:,.{d}f}".replace(",", " ")
    return str(v)


def _p_txt(p):
    if p is None:
        return "—"
    return _dec("< 0.0001") if p < 0.0001 else _dec(f"{p:.4f}")


def _styles_rg(couleurs):
    base = _feuille_styles()
    return {
        "base": base,
        "table": ParagraphStyle("RGTable", parent=base["Heading1"], fontSize=17, textColor=couleurs["accent"], spaceBefore=0, spaceAfter=6),
        "section": ParagraphStyle("RGSection", parent=base["Heading2"], fontSize=13, textColor=couleurs["entete"], spaceBefore=10, spaceAfter=6),
        "sous": ParagraphStyle("RGSous", parent=base["Heading3"], fontSize=10.5, textColor=couleurs["accent"], spaceBefore=8, spaceAfter=4),
        "normal": ParagraphStyle("RGNormal", parent=base["Normal"], fontSize=9, leading=12),
        "muted": ParagraphStyle("RGMuted", parent=base["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#6b7280")),
        "tete": ParagraphStyle("RGTete", parent=base["Normal"], fontSize=7.5, leading=9, textColor=colors.white, fontName=_pol("gras")),
        "cell": ParagraphStyle("RGCell", parent=base["Normal"], fontSize=7.5, leading=9),
    }


def _tableau(entetes, lignes, largeur, couleurs, st, largeurs=None):
    """Tableau à en-tête coloré, cellules en Paragraph (retour à la ligne automatique), en-tête répété d'une page à l'autre."""
    donnees = [[Paragraph(_esc_rg(h), st["tete"]) for h in entetes]]
    from reportlab.platypus import Flowable
    donnees += [[v if isinstance(v, Flowable) else Paragraph(_esc_rg(v if v not in (None, "") else "—"), st["cell"]) for v in ligne] for ligne in lignes]
    n = max(len(entetes), 1)
    tbl = Table(donnees, colWidths=largeurs or [largeur / n] * n, repeatRows=1)
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), couleurs["entete"]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 3),
    ])
    for i in range(2, len(donnees), 2):
        style.add("BACKGROUND", (0, i), (-1, i), couleurs["ligne_paire"])
    tbl.setStyle(style)
    return tbl


def _esc_rg(v):
    from xml.sax.saxutils import escape
    return escape(str(v))


def _section(titre, construire, st, elements, numero=None):
    """Ajoute le titre d'une section puis son contenu ; une erreur devient une ligne d'indisponibilité (journalisée)."""
    elements.append(Paragraph(_esc_rg(f"{numero} {titre}" if numero else titre), st["section"]))
    try:
        contenu = construire()
        elements += contenu if contenu else [Paragraph(_pt("engine/reports/_part_007.py:pdf:114:55"), st["muted"])]
    except Exception:
        _log.exception(_gt("rg5.msg.079"), titre)
        elements.append(Paragraph(_pt("engine/reports/_part_007.py:pdf:117:34"), st["muted"]))


# ─────────────────────────────────────────────────────────────
# 1. Structure
# ─────────────────────────────────────────────────────────────
def _bloc_structure(table, largeur, couleurs, st):
    nb_lignes = None
    try:
        nb_lignes = table.enregistrements_groupe.count()   # simple COUNT : ne charge pas les 32 000 lignes d'un groupe en mémoire
    except Exception:
        _log.exception(_gt("rg5.msg.076"), table.id)
    try:
        from .. import tables_systeme
        origine = _pt("engine/reports/_part_007.py:pdf:132:18") if tables_systeme.est_systeme(table) else _pt("engine/reports/_part_007.py:pdf:132:77")
    except Exception:
        origine = _pt("engine/reports/_part_007.py:pdf:134:18")
    out = [Paragraph(_esc_rg(_ptf("engine/reports/_part_007.py:pdf:135:29", v1=origine, v2='—' if nb_lignes is None else nb_lignes)), st["normal"])]
    if getattr(table, "description", ""):
        out.append(Paragraph(_esc_rg(table.description), st["muted"]))
    champs = list(table.champs.order_by("ordre"))
    out.append(Spacer(1, 0.2 * cm))
    out.append(_tableau([_pt("engine/reports/_part_007.py:pdf:140:25"), _pt("engine/reports/_part_007.py:pdf:140:36")], [[c.nom, str(c.get_type_champ_display())] for c in champs], largeur, couleurs, st,
                        largeurs=[largeur * 0.6, largeur * 0.4]) if champs else Paragraph(_pt("engine/reports/_part_007.py:pdf:141:90"), st["muted"]))
    return out


# ─────────────────────────────────────────────────────────────
# 3-4. Analyses exécutées (dashboards) et skills
# ─────────────────────────────────────────────────────────────
def _blocs_analyses(table, largeur, doc_height):
    from ..models import Skill
    entreprise = table.entreprise
    out = []
    analyses = [d for d in DemandeAgent.objects.filter(entreprise=entreprise, table_cible=table, statut="execute")
                .exclude(texte__startswith="[SKILL #").order_by("cree_le", "id").prefetch_related("graphiques") if d.resultat_json]
    for i, d in enumerate(analyses):
        if i:
            out.append(PageBreak())
        out += _elements_rapport_demande(
            d, _ptf("engine/reports/_part_007.py:pdf:158:15", v1=_nom_vue(table)), d.resultat_json,
            [{"titre": g.legende, "image_base64": g.image_base64} for g in d.graphiques.all()], largeur)
    return out, _skills_executes(table)


def _skills_executes(table):
    """Skills exécutés d'une table, sous la forme [(skill, None)] — indépendant des analyses, pour pouvoir cocher l'un sans l'autre."""
    from ..models import Skill
    skills = [s for s in Skill.objects.filter(entreprise=table.entreprise, table=table, statut="execute").order_by("id") if s.resultat_json]
    return [(s, None) for s in skills]


# ─────────────────────────────────────────────────────────────
# 6. Statistiques descriptives
# ─────────────────────────────────────────────────────────────
def _grille_histogrammes(variables, largeur):
    """Un histogramme par variable quantitative, en grille 2 colonnes — dessiné à partir du RÉSULTAT de la page Statistiques
    (`histogramme` = bornes + effectifs), sans relire la table. Plus de 6 variables : plusieurs figures, aucune n'est écartée.
    Renvoie la liste des images."""
    plt = _plt()
    choisies = []
    for v in variables:
        h = v.get("histogramme") or {}
        if len(h.get("bornes") or []) >= 2 and h.get("effectifs"):
            choisies.append((v.get("nom"), h["bornes"], h["effectifs"]))
    images = []
    for debut in range(0, len(choisies), MAX_GRAPHIQUES_GRILLE):
        lot = choisies[debut:debut + MAX_GRAPHIQUES_GRILLE]
        lignes = (len(lot) + 1) // 2
        fig, axes = plt.subplots(lignes, 2, figsize=(8.2, 2.5 * lignes), squeeze=False)
        for ax in axes.ravel():
            ax.set_visible(False)
        for k, (nom, bornes, eff) in enumerate(lot):
            ax = axes[k // 2][k % 2]
            ax.set_visible(True)
            largeurs = [bornes[j + 1] - bornes[j] for j in range(len(eff))]
            barres_h = ax.bar(bornes[:len(eff)], eff, width=largeurs, align="edge", edgecolor="white")
            for patch, coul in zip(barres_h, _pal_suite(len(barres_h))):   # une couleur par classe, comme la page Statistiques
                patch.set_facecolor(coul); patch.set_alpha(0.85)
            ax.set_title(str(nom)[:40], fontsize=9)
            ax.tick_params(labelsize=7)
            _legende_couleurs(ax, [(barres_h[0].get_facecolor(), "Classes de valeurs de %s à %s (une couleur par classe)" % (_fmt_page(bornes[0]), _fmt_page(bornes[-1])), "carre")], bas=-0.2, taille=5.5)
        fig.tight_layout()
        images.append(_image_figure(fig, largeur))
    return images


def _grille_barres(descriptions, largeur):
    """Un diagramme en barres par variable qualitative (effectifs des modalités les plus fréquentes). Plus de 6 variables :
    plusieurs figures, aucune n'est écartée. Renvoie la liste des images."""
    plt = _plt()
    choisies = [(nom, d["tableau"][:8]) for nom, d in descriptions if d.get("tableau")]
    images = []
    for debut in range(0, len(choisies), MAX_GRAPHIQUES_GRILLE):
        lot = choisies[debut:debut + MAX_GRAPHIQUES_GRILLE]
        lignes = (len(lot) + 1) // 2
        fig, axes = plt.subplots(lignes, 2, figsize=(8.2, 2.7 * lignes), squeeze=False)
        for ax in axes.ravel():
            ax.set_visible(False)
        for k, (nom, tab) in enumerate(lot):
            ax = axes[k // 2][k % 2]
            ax.set_visible(True)
            etiquettes = [str(r.get("modalite"))[:18] for r in tab]
            ax.barh(range(len(tab)), [r.get("effectif") or 0 for r in tab], color=_pal_suite(len(tab)))
            ax.set_yticks(range(len(tab)))
            ax.set_yticklabels(etiquettes, fontsize=7)
            ax.invert_yaxis()
            ax.set_title(str(nom)[:40], fontsize=9)
            ax.tick_params(axis="x", labelsize=7)
            _legende_couleurs(ax, [(p.get_facecolor(), e, "carre") for p, e in zip(ax.patches, etiquettes)], bas=-0.2, ncol=2, taille=5.5)
        fig.tight_layout()
        images.append(_image_figure(fig, largeur))
    return images


def _matrice_correlations(matrice, largeur):
    noms = matrice.get("noms") or []
    pe = matrice.get("pearson") or []
    if len(noms) < 2 or not pe:
        return None
    import numpy as np
    plt = _plt()
    m = np.array([[np.nan if c is None else float(c) for c in ligne] for ligne in pe], float)
    n = len(noms)
    cote = min(7.2, max(3.6, 0.45 * n + 2))
    fig, ax = plt.subplots(figsize=(cote, cote * 0.85))
    im = ax.imshow(m, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(n)); ax.set_yticks(range(n))
    ax.set_xticklabels([str(e)[:14] for e in noms], rotation=60, ha="right", fontsize=7)
    ax.set_yticklabels([str(e)[:14] for e in noms], fontsize=7)
    if n <= 12:
        for i in range(n):
            for j in range(n):
                if not np.isnan(m[i][j]):
                    ax.text(j, i, f"{m[i][j]:.2f}", ha="center", va="center", fontsize=6)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04).set_label(_pt("rg.syn.g_corr_label"), fontsize=7)
    ax.set_title(_pt("engine/reports/_part_007.py:pdf:239:17"), fontsize=9)
    fig.tight_layout()
    return _image_figure(fig, min(largeur, 13 * cm))


_JS = "engine/static/engine/js/statistiques.js:"   # libellés de la page Statistiques : même catalogue, donc exactement les mêmes mots


def _tj(cle, **kw):
    return _ptf(_JS + cle, **kw) if kw else _pt(_JS + cle)


def _fmt_page(v):
    """Même format de nombre que la page (statistiques.js `fmt` : 4 chiffres significatifs)."""
    import math
    if v is None:
        return "—"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, float) and not math.isfinite(v):
        return "—"
    if v == 0:
        return "0"
    x = float(f"{v:.4g}")
    d = max(0, 3 - int(math.floor(math.log10(abs(x)))))
    s = f"{x:,.{d}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return _dec(s.replace(",", " "))


def _pct_page(v):
    return "—" if v is None else _fmt_page(round(v * 10) / 10) + " %"


def _style_axes(ax):
    ax.tick_params(labelsize=7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _nb_exact(v):
    """Effectif affiché tel quel (16 119, jamais arrondi à 4 chiffres comme les mesures) ; une mesure décimale garde le format de la page."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) == int(v):
        return f"{int(v):,}".replace(",", " ")
    return _fmt_page(v)


def _court(texte, n=24):
    """Libellé raccourci avec « … » seulement s'il dépasse vraiment `n` caractères."""
    s = str(texte)
    return s if len(s) <= n else s[:max(1, n - 1)].rstrip() + "…"


def _legende_figure(fig, entrees, taille=7.5, ncol=None):
    """Légende UNIQUE, en bas d'une figure à plusieurs graphiques : chaque couleur y porte son libellé, une seule fois."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    poignees = []
    for coul, lib, genre in [e for e in entrees if e and e[1] not in (None, "")]:
        if genre == "ligne":
            poignees.append(Line2D([0], [0], color=coul, lw=2, label=str(lib)))
        elif genre == "cercle":
            poignees.append(Line2D([0], [0], marker="o", ls="", markerfacecolor="none", markeredgecolor=coul, markersize=4, label=str(lib)))
        else:
            poignees.append(Patch(facecolor=coul, label=str(lib)))
    if poignees:
        fig.legend(handles=poignees, loc="lower center", ncol=ncol or len(poignees), fontsize=taille, frameon=False, handlelength=1.4, columnspacing=1.6)


def _legende_couleurs(ax, entrees, bas=-0.22, ncol=None, taille=6.5, max_entrees=40, droite=False, titre=None):
    """Légende : chaque couleur du graphique porte son libellé.
    `entrees` = [(couleur, libellé, genre)] ; genre = « carre » | « ligne » | « tirets » | « point » | « cercle »."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    entrees = [e for e in entrees if e and e[1] not in (None, "")]
    if not entrees:
        return
    if len(entrees) > max_entrees:
        entrees = entrees[:max_entrees - 1] + [("#9ca3af", "… +%d" % (len(entrees) - max_entrees + 1), "carre")]
    poignees = []
    for coul, lib, genre in entrees:
        if genre == "ligne":
            poignees.append(Line2D([0], [0], color=coul, lw=2, label=str(lib)))
        elif genre == "tirets":
            poignees.append(Line2D([0], [0], color=coul, lw=1.4, ls="--", label=str(lib)))
        elif genre == "point":
            poignees.append(Line2D([0], [0], marker="o", ls="", color=coul, markersize=4, label=str(lib)))
        elif genre == "cercle":
            poignees.append(Line2D([0], [0], marker="o", ls="", markerfacecolor="none", markeredgecolor=coul, markersize=4, label=str(lib)))
        else:
            poignees.append(Patch(facecolor=coul, label=str(lib)))
    n = len(poignees)
    ancienne = ax.get_legend()
    if ancienne is not None:
        ax.add_artist(ancienne)   # une légende déjà présente est conservée
    if droite:
        place = dict(loc="center left", bbox_to_anchor=(1.01, 0.5), ncol=ncol or 1)
    else:
        place = dict(loc="upper center", bbox_to_anchor=(0.5, bas), ncol=ncol or min(n, 6 if n <= 12 else 8))
    ax.legend(handles=poignees, fontsize=taille, frameon=False, handlelength=1.2, columnspacing=1.0, handletextpad=0.4,
              title=titre, title_fontsize=taille, **place)


def _fig_histogramme(h, largeur):
    plt = _plt()
    b, e = h["bornes"], h["effectifs"]
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    for p, c in zip(ax.bar(b[:len(e)], e, width=[b[j + 1] - b[j] for j in range(len(e))], align="edge", edgecolor="white"), _pal_suite(len(e))):
        p.set_facecolor(c); p.set_alpha(0.85)
    _legende_couleurs(ax, [(p.get_facecolor(), "%s – %s" % (_fmt_page(b[j]), _fmt_page(b[j + 1])), "carre") for j, p in enumerate(ax.patches)], bas=-0.3, ncol=8, taille=5.5, titre="Classes de valeurs (une couleur par classe)")
    _style_axes(ax); fig.tight_layout()
    return _image_figure(fig, largeur)


def _fig_ogive(pts, largeur):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=_pal_suite(1)[0], lw=2, label="Part des valeurs ≤ x (%)")
    ax.legend(fontsize=7, loc="lower right", frameon=False)
    ax.set_ylim(0, 100); _style_axes(ax); fig.tight_layout()
    return _image_figure(fig, largeur)


def _fig_barres(etiq, vals, largeur, horizontal=False):
    plt = _plt()
    n, pal = len(etiq), _pal_suite(len(etiq))
    if horizontal:
        fig, ax = plt.subplots(figsize=(7.2, max(1.2, 0.26 * n + 0.5)))
        ax.barh(range(n), vals, color=pal, alpha=0.85)
        ax.set_yticks(range(n)); ax.set_yticklabels([str(e)[:21] + ("…" if len(str(e)) > 22 else "") for e in etiq], fontsize=7); ax.invert_yaxis()
        for i, v in enumerate(vals):
            ax.text(v, i, " " + _fmt_page(v), va="center", fontsize=6.5)
        _bas_leg = -0.45 / max(1.2, 0.26 * n + 0.5)
    else:
        fig, ax = plt.subplots(figsize=(7.2, 2.4))
        ax.bar(range(n), vals, color=pal, alpha=0.85)
        pas = max(1, -(-n // 24))
        ax.set_xticks(range(0, n, pas)); ax.set_xticklabels([str(etiq[i])[:8] for i in range(0, n, pas)], fontsize=7, rotation=45 if n > 8 else 0, ha="right" if n > 8 else "center")
        _bas_leg = -0.30 if n > 8 else -0.18
    _legende_couleurs(ax, [(c_, str(e_)[:24], "carre") for c_, e_ in zip(pal, etiq)], bas=_bas_leg, taille=6)
    _style_axes(ax); fig.tight_layout()
    return _image_figure(fig, largeur)


def _fig_secteurs(etiq, vals, largeur):
    plt = _plt()
    tot = sum(vals) or 1
    fig, ax = plt.subplots(figsize=(6.0, 2.6))
    ax.pie(vals[:12], colors=_pal_suite(min(len(vals), 12)), startangle=90, counterclock=False, wedgeprops={"width": 0.45, "edgecolor": "white"})
    ax.legend(labels=[f"{str(e)[:20]} ({_fmt_page(round(v / tot * 1000) / 10)} %)" for e, v in zip(etiq[:12], vals[:12])], loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=7, frameon=False)
    return _image_figure(fig, largeur * 0.75)


def _fig_pareto(etiq, vals, cumuls, largeur):
    plt = _plt()
    n = len(etiq)
    pal = _pal_suite(n + 1)
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    ax.bar(range(n), vals, color=pal[:n], alpha=0.85)
    pas = max(1, -(-n // 24))
    ax.set_xticks(range(0, n, pas)); ax.set_xticklabels([str(etiq[i])[:8] for i in range(0, n, pas)], fontsize=7, rotation=45 if n > 8 else 0, ha="right" if n > 8 else "center")
    ax2 = ax.twinx()
    ax2.plot(range(n), cumuls, color=pal[n], lw=2); ax2.set_ylim(0, 100); ax2.tick_params(labelsize=7)
    _legende_couleurs(ax, [(c_, str(e_)[:24], "carre") for c_, e_ in zip(pal[:n], etiq)] + [(pal[n], "Cumul des effectifs (%, axe de droite)", "ligne")], bas=-0.30 if n > 8 else -0.18, taille=6)
    _style_axes(ax); fig.tight_layout()
    return _image_figure(fig, largeur)


def _dessiner_boite(ax, b, pal):
    from matplotlib.patches import Rectangle
    ex = b.get("aberrantes") or []
    ax.hlines(1, b["min"], b["q1"], color=pal[0], lw=2); ax.hlines(1, b["q3"], b["max"], color=pal[1], lw=2)
    ax.vlines(b["min"], 0.8, 1.2, color=pal[0], lw=2); ax.vlines(b["max"], 0.8, 1.2, color=pal[1], lw=2)
    ax.add_patch(Rectangle((b["q1"], 0.65), max(b["q3"] - b["q1"], 1e-9), 0.7, facecolor=pal[2], alpha=0.55, edgecolor=pal[2]))
    ax.vlines(b["mediane"], 0.65, 1.35, color=pal[3], lw=2)
    for i, x in enumerate(ex):
        ax.scatter([x], [1], facecolors="none", edgecolors=pal[4 + i], s=18)
    pts = [b["min"], b["max"]] + list(ex)
    lo, hi = min(pts), max(pts)
    pad = (hi - lo) * 0.03 or 1
    ax.set_xlim(lo - pad, hi + pad); ax.set_ylim(0.4, 1.6); ax.set_yticks([])
    _legende_couleurs(ax, [(pal[0], "Minimum → Q1", "ligne"), (pal[1], "Q3 → maximum", "ligne"), (pal[2], "Boîte Q1–Q3", "carre"), (pal[3], "Médiane", "ligne")]
                      + ([(pal[4], "Valeur aberrante", "cercle")] if ex else []), droite=True, taille=5.5)
    _style_axes(ax)


def _fig_boites(liste, largeur):
    """Une boîte à moustaches par entrée (titre, boite) — une seule figure, chaque boîte à sa propre échelle comme sur la page."""
    plt = _plt()
    fig, axes = plt.subplots(len(liste), 1, figsize=(7.2, 0.95 * len(liste) + 0.2), squeeze=False)
    for (titre, b), ax in zip(liste, axes[:, 0]):
        _dessiner_boite(ax, b, _pal_suite(4 + len(b.get("aberrantes") or [])))
        if titre:
            ax.set_title(str(titre), fontsize=7.5, loc="left")
    fig.tight_layout()
    return _image_figure(fig, largeur)


def _fig_qq(pts, largeur):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(3.6, 3.2))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    lo, hi = min(xs + ys), max(xs + ys)
    pal, lc = _pal_suite(len(pts)), _pal_suite(1)
    ax.plot([lo, hi], [lo, hi], "--", color=lc[0], lw=1)
    ax.scatter(xs, ys, c=pal, s=6)
    _legende_couleurs(ax, [(pal[0], "Quantiles observés", "point"), (lc[0], "Droite de la loi normale", "tirets")], bas=-0.2, ncol=1, taille=6)
    ax.set_xlabel(_tj("327:104"), fontsize=7); _style_axes(ax); fig.tight_layout()
    return _image_figure(fig, largeur * 0.5)


def _fig_nuage(g, largeur):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    if g.get("x"):
        pal, lc = _pal_suite(len(g["x"])), _pal_suite(2)
        ax.scatter(g["x"], g["y"], c=pal, s=7, alpha=0.85)
        d = g.get("droite")
        if d:
            lx, hx = min(g["x"]), max(g["x"])
            ax.plot([lx, hx], [d["ordonnee"] + d["pente"] * lx, d["ordonnee"] + d["pente"] * hx], color=lc[1], lw=2)
        _legende_couleurs(ax, [(pal[0], "Observations", "point")] + ([(lc[1], "Droite de régression", "ligne")] if d else []), bas=-0.18, taille=6.5)
    ax.set_xlabel(str(g.get("nom_x") or ""), fontsize=8); ax.set_ylabel(str(g.get("nom_y") or ""), fontsize=8)
    _style_axes(ax); fig.tight_layout()
    return _image_figure(fig, largeur)


def _fig_empilees(lignes, colonnes, eff, largeur):
    plt = _plt()
    pal = _pal_suite(len(colonnes))
    fig, ax = plt.subplots(figsize=(7.2, max(1.6, 0.3 * len(lignes) + 0.9)))
    for i, ligne in enumerate(eff):
        tot, x = sum(ligne) or 1, 0
        for j, e in enumerate(ligne):
            ax.barh(i, e / tot * 100, left=x, color=pal[j], label=str(colonnes[j])[:14] if i == 0 else None); x += e / tot * 100
    ax.set_yticks(range(len(lignes))); ax.set_yticklabels([str(l)[:22] for l in lignes], fontsize=7); ax.invert_yaxis(); ax.set_xlim(0, 100)
    ax.legend(fontsize=6.5, ncol=min(len(colonnes), 6), loc="upper center", bbox_to_anchor=(0.5, -0.12), frameon=False)
    _style_axes(ax); fig.tight_layout()
    return _image_figure(fig, largeur)


def _grille_paires(paires, largeur, couleurs, st, par_ligne=3):
    """Paires libellé / valeur (comme `paires()` de la page), `par_ligne` paires par ligne."""
    cases = []
    for k, v in paires:
        cases += [Paragraph(_esc_rg(k), st["muted"]), Paragraph("<b>" + _esc_rg("—" if v is None else (_fmt_page(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)) + "</b>", st["cell"])]
    lignes = []
    for i in range(0, len(cases), 2 * par_ligne):
        ligne = cases[i:i + 2 * par_ligne]
        lignes.append(ligne + [""] * (2 * par_ligne - len(ligne)))
    if not lignes:
        return Spacer(1, 0)
    tbl = Table(lignes, colWidths=[largeur * 0.19, largeur * 0.14] * par_ligne)
    tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 3)]
                            + [("BACKGROUND", (c, 0), (c, -1), colors.HexColor("#f3f4f6")) for c in range(0, 2 * par_ligne, 2)]))
    return tbl


def _sure(fonction, table, out):
    """Un graphique en échec n'arrête ni la carte ni le rapport : il est journalisé et sauté."""
    try:
        out.append(fonction())
    except Exception:
        _log.exception(_gt("rg5.msg.080"), table.id)


def _carte_titre(v, st, titre=None):
    from .. import statistiques as _st
    genre = _st.GENRES.get(v.get("genre"), v.get("genre")) if v.get("genre") else ""
    t = f"<b>{_esc_rg(titre or v.get('nom') or v.get('ref'))}</b>" + (f" · {_esc_rg(genre)}" if genre else "") + (f" · {_esc_rg(v['table'])}" if v.get("table") else "")
    return Paragraph(t, st["sous"])


def _carte_par_groupe(p, table, largeur, couleurs, st):
    if p.get("erreur"):
        return [Paragraph(_esc_rg(p["erreur"]), st["muted"])]
    out = [Paragraph(_esc_rg(_tj("selon_variable", v1=p.get("variable_groupe"))), st["sous"])]
    if p.get("quanti"):
        out.append(_tableau([_tj("lbl_groupe"), "n", _tj("420:39"), _tj("420:50"), _tj("421:147"), "Min", "Max"],
                            [[g.get("groupe"), g.get("n"), _fmt_page(g.get("moyenne")), _fmt_page(g.get("mediane")), _fmt_page(g.get("ecart_type")), _fmt_page(g.get("min")), _fmt_page(g.get("max"))] for g in p["quanti"]], largeur, couleurs, st))
        boites = [(g.get("groupe"), g["boite"]) for g in p["quanti"] if g.get("boite")]
        if boites:
            _sure(lambda: _fig_boites(boites, largeur), table, out)
    elif p.get("croise"):
        out.append(_tableau([_tj("lbl_groupe"), _tj("424:34"), *p.get("modalites", [])],
                            [[g.get("groupe"), g.get("total"), *[f"{e} ({_fmt_page(round(e / g['total'] * 1000) / 10) if g.get('total') else 0} %)" for e in g.get("effectifs", [])]] for g in p["croise"]], largeur, couleurs, st))
    return out


def _carte_quanti(v, table, largeur, couleurs, st):
    from reportlab.platypus import KeepTogether
    d = v.get("denombrement") or {}
    paires = [(_tj("lbl_effectif_n"), v.get("n")), (_tj("372:43"), v.get("manquants")), (_tj("372:80"), v.get("somme")), (_tj("372:100"), v.get("moyenne")), (_tj("372:124"), v.get("mediane")),
              (_tj("372:148"), v.get("min")), (_tj("372:168"), v.get("max")), (_tj("372:188"), v.get("etendue")), ("1er quartile", v.get("q1")), ("3e quartile", v.get("q3")),
              (_tj("lbl_intervalle_interquartile"), v.get("iqr")), (_tj("373:91"), v.get("ecart_type")), (_tj("373:135"), v.get("variance")), (_tj("373:175"), v.get("ecart_type_pop")),
              (_tj("374:7"), None if v.get("cv") is None else _pct_page(v["cv"])), (_tj("374:73"), v.get("erreur_standard")), (_tj("374:113"), v.get("asymetrie")), (_tj("lbl_aplatissement"), v.get("aplatissement")),
              (_tj("375:7"), v.get("moyenne_geometrique")), (_tj("375:55"), v.get("moyenne_harmonique"))]
    paires += [(_tj("lbl_centile_k", v1=k), x) for k, x in (v.get("percentiles") or {}).items()]
    paires += [(_tj("376:7"), d.get("distincts")), (_tj("376:57"), d.get("doublons")), (_tj("376:115"), d.get("zeros")), (_tj("376:163"), d.get("negatives")), (_tj("376:212"), d.get("positives"))]
    out = [KeepTogether([_carte_titre(v, st), _grille_paires(paires, largeur, couleurs, st)])]
    if v.get("ic_moyenne"):
        ic = v["ic_moyenne"]
        out.append(Paragraph(_esc_rg(_tj("377:61", v1=_fmt_page((v.get("confiance") or 0) * 100), v2=_fmt_page(ic[0]), v3=_fmt_page(ic[1]))), st["muted"]))
    out += [Paragraph("• " + _esc_rg(x), st["normal"]) for x in v.get("lecture") or []]
    h = v.get("histogramme")
    if h and len(h.get("bornes") or []) >= 2 and h.get("effectifs"):
        out.append(Paragraph(_esc_rg(_tj("lbl_histogramme")), st["sous"]))
        _sure(lambda: _fig_histogramme(h, largeur), table, out)
        out.append(Paragraph(_esc_rg(_tj("380:36")), st["sous"]))
        out.append(_tableau([_tj("lbl_de"), _tj("lbl_a"), _tj("lbl_effectif"), _tj("380:96"), _tj("380:177")],
                            [[_fmt_page(k.get("de")), _fmt_page(k.get("a")), k.get("effectif"), _pct_page(k.get("frequence")), _pct_page(k.get("cumul"))] for k in v.get("classes") or []], largeur, couleurs, st))
        if v.get("ogive"):
            out.append(Paragraph(_esc_rg(_tj("381:36")), st["sous"]))
            _sure(lambda: _fig_ogive(v["ogive"], largeur), table, out)
    fr = v.get("frequences")
    if fr:
        out.append(Paragraph(_esc_rg(_tj("382:93") + ", ".join(_fmt_page(m) for m in v.get("modes") or []) + ")"), st["sous"]))
        _sure(lambda: _fig_barres([_fmt_page(f.get("valeur")) for f in fr], [f.get("effectif") for f in fr], largeur), table, out)
        out.append(_tableau([_tj("383:15"), _tj("lbl_effectif"), _tj("383:37"), _tj("383:135")], [[f.get("valeur"), f.get("effectif"), _pct_page(f.get("frequence")), _pct_page(f.get("cumul"))] for f in fr], largeur, couleurs, st))
    if v.get("boite"):
        ab = v.get("aberrantes") or {}
        out.append(Paragraph(_esc_rg(_tj("384:88", v1=ab.get("n")) + (_tj("384:179", v1=ab["extremes"]) if ab.get("extremes") else "")), st["sous"]))
        _sure(lambda: _fig_boites([(None, v["boite"])], largeur), table, out)
    nm = v.get("normalite")
    if nm:
        out.append(Paragraph(_esc_rg(_tj("385:92", v1=nm.get("test"), v2=_p_txt(nm.get("p")), v3=nm.get("conclusion"))), st["sous"]))
        if nm.get("qq"):
            _sure(lambda: _fig_qq(nm["qq"], largeur), table, out)
    if v.get("par"):
        out += _carte_par_groupe(v["par"], table, largeur, couleurs, st)
    return out


def _carte_date(v, table, largeur, couleurs, st):
    paires = [(_tj("lbl_effectif_n"), v.get("n")), (_tj("392:43"), v.get("manquants")), (_tj("392:80"), v.get("min")), (_tj("392:106"), v.get("max")), (_tj("392:132"), v.get("etendue_jours")),
              (_tj("392:170"), v.get("doublons")), (_tj("393:7"), v.get("delai_median")), (_tj("393:61"), v.get("delai_moyen")), (_tj("393:101"), v.get("delai_max"))]
    out = [_carte_titre(v, st), _grille_paires(paires, largeur, couleurs, st)]
    out += [Paragraph("• " + _esc_rg(x), st["normal"]) for x in v.get("lecture") or []]
    for titre, cle, avec_tab in ((_tj("395:36"), "evolution", False), (_tj("395:116"), "par_annee", True), (_tj("395:162"), "par_mois", True), (_tj("396:9"), "par_jour", True), (_tj("396:67"), "par_heure", True)):
        t = v.get(cle)
        if t and len(t) > 1:
            out.append(Paragraph(_esc_rg(titre), st["sous"]))
            _sure(lambda: _fig_barres([x.get("modalite") for x in t], [x.get("effectif") for x in t], largeur), table, out)
            if avec_tab:
                out.append(_tableau([_tj("398:28"), _tj("lbl_effectif"), _tj("398:51")], [[x.get("modalite"), x.get("effectif"), _pct_page(x.get("frequence"))] for x in t], largeur, couleurs, st))
    return out


def _carte_quali(v, table, largeur, couleurs, st):
    from reportlab.platypus import KeepTogether
    from reportlab.graphics.shapes import Drawing, Rect
    d = v.get("denombrement") or {}
    paires = [(_tj("lbl_effectif_n"), v.get("n")), (_tj("404:43"), v.get("manquants")), (_tj("404:80"), v.get("distincts")), ("Mode", ", ".join(str(m) for m in v.get("modes") or [])),
              (_tj("lbl_entropie_bits"), v.get("entropie")), (_tj("404:179"), v.get("entropie_relative")), (_tj("404:223"), v.get("gini_simpson")), (_tj("404:270"), d.get("uniques")),
              (_tj("404:327"), d.get("rares")), (_tj("404:378"), _pct_page(d.get("part_rares")))]
    if "mediane" in v:
        paires += [(_tj("405:38"), v.get("mediane")), ("1er quartile", v.get("q1")), ("3e quartile", v.get("q3"))]
    out = [KeepTogether([_carte_titre(v, st), _grille_paires(paires, largeur, couleurs, st)])]
    out += [Paragraph("• " + _esc_rg(x), st["normal"]) for x in v.get("lecture") or []]
    tab = v.get("tableau") or []
    if tab:
        ordo = v.get("genre") == "quali_ordinale"
        mods, effs = [t.get("modalite") for t in tab], [t.get("effectif") or 0 for t in tab]
        sans_autres = all(t.get("cumul") is not None for t in tab)
        _sure(lambda: _fig_barres(mods, effs, largeur, horizontal=len(tab) > 8 and not ordo), table, out)
        if len(tab) > 1:
            out.append(Paragraph(_esc_rg(_tj("410:64")), st["sous"]))
            m12, e12 = (mods[:11] + [_tj("lbl_autres", v1=len(mods) - 11)], effs[:11] + [sum(effs[11:])]) if len(mods) > 12 else (mods, effs)
            _sure(lambda: _fig_secteurs(m12, e12, largeur), table, out)
        if not ordo and sans_autres and len(tab) > 2:
            out.append(Paragraph(_esc_rg(_tj("411:87")), st["sous"]))
            _sure(lambda: _fig_pareto(mods, effs, [t.get("cumul") for t in tab], largeur), table, out)
        pal = _pal_suite(len(tab))
        lc = largeur * 0.28

        def barre(pct, coul):
            dr = Drawing(lc, 7)
            dr.add(Rect(0, 0, lc * max(0, min(pct or 0, 100)) / 100, 7, fillColor=colors.HexColor(coul), strokeColor=None))
            return dr
        out.append(_tableau([_tj("412:15"), _tj("lbl_effectif"), _tj("383:37"), "", _tj("383:135")],
                            [[t.get("modalite"), t.get("effectif"), _pct_page(t.get("frequence")), barre(t.get("frequence"), pal[i]), "—" if t.get("cumul") is None else _pct_page(t["cumul"])] for i, t in enumerate(tab)],
                            largeur, couleurs, st, largeurs=[largeur * 0.3, largeur * 0.12, largeur * 0.12, lc + 6, largeur * 0.12]))
    if v.get("par"):
        out += _carte_par_groupe(v["par"], table, largeur, couleurs, st)
    return out


def _matrice_page(m, cle, largeur, couleurs, st):
    """Matrice de corrélations colorée comme la page (vert si positif, rouge si négatif, opacité = |valeur|)."""
    noms = m.get("noms") or []
    n = len(noms)

    def teinte(val):
        if val is None:
            return colors.white
        a, (r, g, b) = abs(val) * 0.55, ((0x2e, 0xcc, 0x71) if val >= 0 else (0xe7, 0x4c, 0x3c))
        return colors.Color((255 * (1 - a) + r * a) / 255, (255 * (1 - a) + g * a) / 255, (255 * (1 - a) + b * a) / 255)
    donnees = [[""] + [Paragraph(_esc_rg(x), st["tete"]) for x in noms]]
    for i, lig in enumerate(m.get(cle) or []):
        donnees.append([Paragraph("<b>" + _esc_rg(noms[i]) + "</b>", st["cell"])] + [Paragraph("—" if x is None else _dec(f"{x:.2f}"), st["cell"]) for x in lig])
    tbl = Table(donnees, colWidths=[largeur * 0.2] + [largeur * 0.8 / max(n, 1)] * n, repeatRows=1)
    sty = [("BACKGROUND", (1, 0), (-1, 0), couleurs["entete"]), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")), ("PADDING", (0, 0), (-1, -1), 2)]
    for i, lig in enumerate(m.get(cle) or []):
        for j, x in enumerate(lig):
            sty.append(("BACKGROUND", (j + 1, i + 1), (j + 1, i + 1), teinte(x)))
    tbl.setStyle(TableStyle(sty))
    return tbl


def _carte_relations(r, table, largeur, couleurs, st):
    out = [Paragraph("<b>" + _esc_rg(_tj("liens_entre_variables", v1=r.get("table"))) + "</b>", st["sous"])]
    paires = r.get("paires") or []
    if paires:
        out.append(_tableau([_tj("lbl_var1"), _tj("lbl_var2"), _tj("lbl_mesure"), _tj("464:85"), "p", "n", _tj("lbl_force")],
                            [[p.get("a"), p.get("b"), p.get("mesure"), _fmt_page(p.get("valeur")), _p_txt(p.get("p")), p.get("n"), p.get("force")] for p in paires], largeur, couleurs, st))
    avec = [p for p in paires if p.get("graphique")]
    if avec:
        out.append(Paragraph(_esc_rg(_tj("466:104", v1=len(avec))), st["muted"]))
    for p in avec:
        g = p["graphique"]
        out.append(Paragraph(_esc_rg(_tj("lien_mesure", v1=p.get("a"), v2=p.get("b"), v3=p.get("mesure"), v4=_fmt_page(round((p.get("valeur") or 0) * 1000) / 1000), v5=p.get("force"))), st["sous"]))
        if g.get("type") == "nuage":
            _sure(lambda: _fig_nuage(g, largeur), table, out)
        elif g.get("type") == "croise":
            _sure(lambda: _fig_empilees(g["lignes"], g["colonnes"], g["effectifs"], largeur), table, out)
            out.append(_tableau([""] + [str(c) for c in g["colonnes"]], [[l] + list(g["effectifs"][i]) for i, l in enumerate(g["lignes"])], largeur, couleurs, st))
        else:
            _sure(lambda: _fig_boites([(f"{q.get('nom')} (n = {q.get('n')})", q["boite"]) for q in g.get("groupes") or []], largeur), table, out)
    m = r.get("matrice")
    if m:
        out += [Paragraph(_esc_rg(_tj("468:62")), st["sous"]), _matrice_page(m, "pearson", largeur, couleurs, st),
                Paragraph(_esc_rg(_tj("468:155")), st["sous"]), _matrice_page(m, "spearman", largeur, couleurs, st)]
    return out


def _bloc_statistiques(table, largeur, couleurs, st, cache=None):
    """Statistiques descriptives du rapport = ce que la page Statistiques reçoit de `calculer_descriptives` (même fonction, toutes les colonnes,
    sans échantillon), présenté dans le même ordre et avec les mêmes libellés : bandeaux, synthèse, une carte par variable, liens entre variables."""
    _pal_raz()   # comme `_curseur = 0` au lancement de la page
    st = {**st, "sous": ParagraphStyle("RGSousSuite", parent=st["sous"], keepWithNext=1)}   # un sous-titre ne reste jamais seul en bas de page
    from .. import statistiques_donnees as sd
    if not sd.decrire(table):
        return [Paragraph(_pt("engine/reports/_part_007.py:pdf:248:26"), st["normal"])]
    from ..views_statistiques import calculer_descriptives
    try:
        res = calculer_descriptives([table], _refs_page(table))
    except ValueError as e:
        return [Paragraph(_esc_rg(e), st["normal"])]
    if cache is not None:
        cache["statistiques"] = res   # relu par la synthèse, sans recalcul
    out = []
    ech = res.get("echantillon")
    if ech:
        out.append(Paragraph(_esc_rg(_tj("475:65", v1=ech.get("methode"), v2=ech.get("taille"), v3=_tj("475:161") if ech.get("remise") else "", v4="—" if ech.get("graine") is None else ech["graine"])), st["muted"]))
    tc = res.get("tables_de_calcul") or []
    for t_ in tc:
        if len(tc) > 1 or t_.get("lignes") != t_.get("lignes_avant"):
            out.append(Paragraph(_esc_rg(_tj("476:154", v1=" + ".join(map(str, t_.get("tables") or [])), v2=t_.get("lignes")) + (_tj("sur_n_avant", v1=t_.get("lignes_avant")) if t_.get("lignes") != t_.get("lignes_avant") else "") + "."), st["muted"]))
    if res.get("homogene") is False:
        out.append(Paragraph(_esc_rg(_tj("477:72")), st["muted"]))
    out += [Paragraph(_esc_rg(a), st["muted"]) for a in res.get("avertissements") or []]
    syn = res.get("synthese")
    if syn:
        out.append(Paragraph(_esc_rg(_tj("428:20")), st["sous"]))
        constats = syn.get("constats") or []
        out += [Paragraph("• " + _esc_rg(c.get("texte", "")), st["normal"]) for c in constats] if constats else [Paragraph(_esc_rg(_tj("429:209")), st["normal"])]
        if syn.get("qualite"):
            out += [Paragraph(_esc_rg(_tj("430:65")), st["sous"]),
                    _tableau([_tj("430:98"), _tj("430:109"), "Manquantes"], [[q.get("nom"), q.get("n"), f"{q['manquants']} ({_pct_page(q.get('taux_manquants'))})" if q.get("manquants") else "0"] for q in syn["qualite"]], largeur, couleurs, st)]
    for v in res.get("variables") or []:
        g = str(v.get("genre") or "")
        try:
            if not g or v.get("erreur"):
                out += [_carte_titre(v, st), Paragraph(_esc_rg(v.get("erreur") or ""), st["muted"])]
            elif g.startswith("quanti"):
                out += _carte_quanti(v, table, largeur, couleurs, st)
            elif g == "date":
                out += _carte_date(v, table, largeur, couleurs, st)
            else:
                out += _carte_quali(v, table, largeur, couleurs, st)
        except Exception:
            _log.exception(_gt("rg5.msg.080"), table.id)
    for r in res.get("relations") or []:
        try:
            out += _carte_relations(r, table, largeur, couleurs, st)
        except Exception:
            _log.exception(_gt("rg5.msg.086"), table.id)
    return out


# ─────────────────────────────────────────────────────────────
# 7-8. ANOVA et analyses factorielles : sélection automatique des colonnes
# ─────────────────────────────────────────────────────────────
def _refs_page(table):
    """Colonnes cochées par défaut à l'ouverture d'une page d'analyse : toutes celles de la table (types reconnus par la page)."""
    from .. import statistiques_donnees as sd
    fiche = sd.decrire(table)
    return [f"{table.id}:{c['slug']}" for c in (fiche or {}).get("colonnes", [])]


def _bloc_anova(table, largeur, couleurs, st, cache=None):
    from ..views_analyses import calculer_anova
    out, echecs, faits = [], [], 0
    plt = _plt()
    _pal_raz()   # nouvelle analyse = palette depuis le début, comme la page ANOVA
    try:
        res = calculer_anova([table], _refs_page(table))   # = ce que la page ANOVA affiche, même fonction
    except ValueError as e:
        return [Paragraph(_esc_rg(e), st["normal"])]
    if cache is not None:
        cache["anova"] = res   # relu par la synthèse, sans recalcul
    for a in res["analyses"]:
        reponse = {"nom": a["reponse"]}
        facteurs = [{"nom": u.get("facteur")} for u in a.get("un_facteur") or []]
        faits += 1
        out.append(Paragraph(_esc_rg(_ptf("engine/reports/_part_007.py:pdf:375:37", v1=a['reponse'], v2=', '.join(f['nom'] for f in facteurs))), st["sous"]))
        for u in a.get("un_facteur") or []:
            out.append(Paragraph("• " + _esc_rg(u.get("synthese", "")), st["normal"]))
            out.append(Paragraph(_esc_rg(_ptf("engine/reports/_part_007.py:pdf:378:41", v1=u.get('retenu'), v2=u.get('raison', ''))), st["muted"]))
            hyp = [[h.get("nom"), _p_txt(h.get("p")), ("✔ " if h.get("ok") else "✘ ") + str(h.get("texte", ""))] for h in u.get("hypotheses") or []]
            tests = [[k.split(" — ")[0], f"{v['statistique']['nom']} = {_nombre(v['statistique']['valeur'])}", v.get("ddl"), _p_txt(v.get("p")), "✔" if v.get("significatif") else "✘"] for k, v in (u.get("resultats") or {}).items()]
            out += ([_tableau(["Condition", "p", "Verdict"], hyp, largeur, couleurs, st)] if hyp else []) + ([_tableau(["Test", "Statistique", "ddl", "p", "Verdict"], tests, largeur, couleurs, st)] if tests else [])
            tab = ((u.get("resultats") or {}).get(u.get("retenu")) or {}).get("tableau_anova") or []
            pairs = (((u.get("resultats") or {}).get(u.get("retenu")) or {}).get("comparaisons") or {}).get("lignes") or []
            out += ([_tableau(["Source", "SS", "ddl", "MS", "F", "p"], [[l.get("source"), _nombre(l.get("ss")), l.get("ddl"), _nombre(l.get("ms")), _nombre(l.get("f")), _p_txt(l.get("p"))] for l in tab], largeur, couleurs, st)] if tab else []) + ([_tableau(["A", "B", "Δ", "p", "Verdict"], [[x.get("a"), x.get("b"), _nombre(x.get("difference")), _p_txt(x.get("p")), "✔" if x.get("significatif") else "✘"] for x in pairs[:30]], largeur, couleurs, st)] if pairs else [])
            groupes = u.get("groupes") or []
            if groupes:
                out.append(Spacer(1, 0.1 * cm))
                out.append(_tableau([_pt("engine/reports/_part_007.py:pdf:382:37"), "n", _pt("engine/reports/_part_007.py:pdf:382:52"), _pt("engine/reports/_part_007.py:pdf:382:63"), _pt("engine/reports/_part_007.py:pdf:382:78"), _pt("engine/reports/_part_007.py:pdf:382:90"), _pt("engine/reports/_part_007.py:pdf:382:97")],
                                    [[g.get("nom"), g.get("n"), _nombre(g.get("moyenne")), _nombre(g.get("ecart_type")), _nombre(g.get("mediane")),
                                      _nombre(g.get("min")), _nombre(g.get("max"))] for g in groupes[:12]], largeur, couleurs, st))
                retenu = (u.get("resultats") or {}).get(u.get("retenu")) or {}
                try:
                    fig, ax = plt.subplots(figsize=(7.2, 2.6))
                    noms = [str(g.get("nom"))[:16] for g in groupes[:12]]
                    moy = [g.get("moyenne") or 0 for g in groupes[:12]]
                    err = [[(g.get("moyenne") or 0) - (g.get("ic_bas") if g.get("ic_bas") is not None else (g.get("moyenne") or 0)) for g in groupes[:12]],
                           [(g.get("ic_haut") if g.get("ic_haut") is not None else (g.get("moyenne") or 0)) - (g.get("moyenne") or 0) for g in groupes[:12]]]
                    ng = len(moy)
                    pal = _pal_suite(5 * ng)   # comme la page : 5 couleurs par groupe (barre = pal[i], intervalle = pal[3n+i], moyenne = pal[4n+i])
                    ax.bar(range(ng), moy, color=pal[:ng])
                    for i in range(ng):
                        ax.errorbar([i], [moy[i]], yerr=[[err[0][i]], [err[1][i]]], fmt="none", ecolor=pal[3 * ng + i], elinewidth=1.6, capsize=3, zorder=3)
                        ax.plot([i], [moy[i]], "o", color=pal[4 * ng + i], ms=4, zorder=4)
                    ax.set_xticks(range(len(noms)))
                    ax.set_xticklabels(noms, rotation=30, ha="right", fontsize=7)
                    ax.set_title(_ptf("engine/reports/_part_007.py:pdf:397:33", v1=a['reponse'], v2=u.get('facteur')), fontsize=9)
                    ax.tick_params(axis="y", labelsize=7)
                    if ng:
                        _legende_couleurs(ax, [(pal[i], noms[i], "carre") for i in range(ng)] + [(pal[3 * ng], "Intervalle de confiance 95 % (trait)", "ligne"), (pal[4 * ng], "Moyenne du groupe (point)", "point")], bas=-0.42, taille=6)
                    fig.tight_layout()
                    out.append(_image_figure(fig, largeur))
                except Exception:
                    _log.exception(_gt("rg5.msg.087"), table.id)
        multi = a.get("multi")
        if multi:
            out.append(Paragraph(_pt("engine/reports/_part_007.py:pdf:405:33") + _esc_rg(multi.get("synthese", "")), st["normal"]))
            termes = (multi.get("resultat") or {}).get("tableau_anova") or []   # lignes Résidu / Total sans F ni p : conservées (ddl)
            if termes:
                out.append(_tableau([_pt("engine/reports/_part_007.py:pdf:408:37"), "ddl", "F", "p"],
                                    [[t_.get("source"), t_.get("ddl"), _nombre(t_.get("f", t_.get("F"))), _p_txt(t_.get("p"))] for t_ in termes[:12]],
                                    largeur, couleurs, st, largeurs=[largeur * 0.4, largeur * 0.2, largeur * 0.2, largeur * 0.2]))
        for ig in a.get("ignores") or []:
            echecs.append([ig.get("facteur"), ig.get("raison")])
    echecs += [[e["reponse"], e["raison"]] for e in res.get("echecs") or []]
    if echecs:
        out += [Spacer(1, 0.2 * cm), Paragraph(_pt("engine/reports/_part_007.py:pdf:414:47"), st["sous"]),
                _tableau([_pt("engine/reports/_part_007.py:pdf:415:26"), _pt("engine/reports/_part_007.py:pdf:415:48")], echecs[:15], largeur, couleurs, st, largeurs=[largeur * 0.3, largeur * 0.7])]
    return out if faits or out else [Paragraph(_pt("engine/reports/_part_007.py:pdf:416:47"), st["normal"])]


def _tableau_valeurs_propres(analyse, largeur, couleurs, st):
    vps = analyse.get("valeurs_propres") or []
    return _tableau([_pt("engine/reports/_part_007.py:pdf:421:21"), _pt("engine/reports/_part_007.py:pdf:421:28"), _pt("rg.pct_variance"), _pt("rg.pct_cumule")],
                    [[v.get("axe"), _nombre(v.get("valeur"), 3), _nombre(v.get("pct"), 1), _nombre(v.get("pct_cumule"), 1)] for v in vps[:8]],
                    largeur, couleurs, st, largeurs=[largeur * 0.15, largeur * 0.28, largeur * 0.28, largeur * 0.29])


def _figure_eboulis(analyse, largeur):
    plt = _plt()
    vps = (analyse.get("valeurs_propres") or [])[:10]
    if not vps:
        return None
    from engine.graphiques_degrade import trace_degrade
    fig, ax = plt.subplots(figsize=(6.2, 2.4))
    pal = _pal_suite(len(vps) + 1)   # une couleur par barre + une pour la courbe cumulée (page : analyses_commun.js)
    ax.bar([v["axe"] for v in vps], [v.get("pct") or 0 for v in vps], color=pal[:len(vps)])
    trace_degrade(ax, [v["axe"] for v in vps], [v.get("pct_cumule") or 0 for v in vps], pal[len(vps)], marker="o", ms=3)   # inertie cumulée = évolution : dégradé sombre -> clair
    _legende_couleurs(ax, [(pal[i], "Axe %s" % v["axe"], "carre") for i, v in enumerate(vps)] + [(pal[len(vps)], "% cumulé de variance", "ligne")], bas=-0.3, taille=6)
    ax.set_title(_pt("engine/reports/_part_007.py:pdf:434:17"), fontsize=9)
    ax.set_xlabel(_pt("engine/reports/_part_007.py:pdf:435:18"), fontsize=8)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    return _image_figure(fig, min(largeur, 14 * cm))


def _figure_plan(titre, points, largeur, fleches=None):
    """Plan factoriel 1-2 : `points` = [(étiquette, x, y)], `fleches` = cercle des corrélations (variables)."""
    plt = _plt()
    if not points and not fleches:
        return None
    fig, ax = plt.subplots(figsize=(5.6, 5.0))
    if fleches:
        ax.add_patch(plt.Circle((0, 0), 1, fill=False, color="#9ca3af"))
        pal_f = _pal_suite(len(fleches))   # une flèche = une couleur, différente des autres
        for (nom, x, y), coul in zip(fleches, pal_f):
            ax.annotate("", xy=(x, y), xytext=(0, 0), arrowprops={"arrowstyle": "->", "color": coul, "lw": 1})
            ax.text(x * 1.06, y * 1.06, str(nom)[:16], fontsize=7, color=coul, ha="center", va="center")
        ax.set_xlim(-1.15, 1.15); ax.set_ylim(-1.15, 1.15); ax.set_aspect("equal")
    if points:
        xs = [p[1] for p in points]; ys = [p[2] for p in points]
        etiquettes = len(points) <= 40
        ax.scatter(xs, ys, s=10 if not etiquettes else 18, color=_pal_suite(len(points)), alpha=0.7)   # un point = une couleur
        if etiquettes:
            for nom, x, y in points:
                ax.text(x, y, str(nom)[:16], fontsize=6.5, ha="left", va="bottom")
    if points:
        _legende_couleurs(ax, [(ax.collections[-1].get_facecolor()[0], "Individus (une observation par point)", "point")], bas=-0.12, taille=7)
    ax.axhline(0, color="#d1d5db", lw=0.8); ax.axvline(0, color="#d1d5db", lw=0.8)
    ax.set_xlabel(_pt("engine/reports/_part_007.py:pdf:461:18"), fontsize=8); ax.set_ylabel(_pt("engine/reports/_part_007.py:pdf:461:54"), fontsize=8)
    ax.set_title(titre, fontsize=9)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    return _image_figure(fig, min(largeur, 12.5 * cm))


# ─────────────────────────────────────────────────────────────
# 6 bis. Statistiques groupées : plusieurs variables ensemble (onglet « Groupes de variables » de la page Statistiques)
# ─────────────────────────────────────────────────────────────
MAX_QUALI_RAPPORT = 3        # variables qualitatives qui définissent les groupes dans le rapport (les plus discriminantes)
MAX_CANDIDATES_RAPPORT = 8   # variables qualitatives essayées avant d'en retenir MAX_QUALI_RAPPORT


def _analyses_groupes(table, cache=None):
    """Analyses groupées du rapport = ce que l'onglet « Groupes de variables » reçoit de `donnees.ensembles` (même fonction, toutes les colonnes de la
    table, une seule lecture des lignes) : continues entre elles, qualitatives entre elles, et les continues selon les variables qualitatives qui les
    séparent le mieux. Un mode sans objet pour cette table (pas assez de variables du bon genre) vaut None. Résultat posé dans le `cache` (relu par la synthèse)."""
    if cache is not None and "groupes" in cache:
        return cache["groupes"]
    from .. import statistiques_donnees as sd
    res = {"continues": None, "qualitatives": None, "par": []}
    fiche = sd.decrire(table)
    if fiche:
        refs = _refs_page(table)
        memo = sd.memoire([table], None, colonnes=refs)
        for mode in ("continues", "qualitatives"):
            try:
                res[mode] = sd.ensembles(memo, mode, refs)
            except ValueError:
                pass
        essais = []
        for c in [c for c in fiche["colonnes"] if c.get("famille") != "quanti"][:MAX_CANDIDATES_RAPPORT]:
            try:
                r = sd.ensembles(memo, "continues_par_quali", refs, None, f"{table.id}:{c['slug']}")
            except ValueError:
                continue
            essais.append((sum(1 for a in r["analyses"] for v in a["variables"] if v.get("p_ajuste") is not None and v["p_ajuste"] < 0.05), r))
        res["par"] = [r for _n, r in sorted(essais, key=lambda e: -e[0])[:MAX_QUALI_RAPPORT]]
    if cache is not None:
        cache["groupes"] = res
    return res


def _matrice_rect_page(lignes, colonnes, valeurs, echelle, largeur, couleurs, st):
    """Matrice lignes × colonnes teintée comme la page (vert si positif, rouge si négatif, opacité = |valeur| / échelle)."""
    def teinte(val):
        if val is None:
            return colors.white
        a, (r, g, b) = min(abs(val) / echelle, 1.0) * 0.55, ((0x2e, 0xcc, 0x71) if val >= 0 else (0xe7, 0x4c, 0x3c))
        return colors.Color((255 * (1 - a) + r * a) / 255, (255 * (1 - a) + g * a) / 255, (255 * (1 - a) + b * a) / 255)
    donnees = [[""] + [Paragraph(_esc_rg(x), st["tete"]) for x in colonnes]]
    for i, lig in enumerate(valeurs):
        donnees.append([Paragraph("<b>" + _esc_rg(lignes[i]) + "</b>", st["cell"])] + [Paragraph("—" if x is None else _dec(f"{x:.2f}"), st["cell"]) for x in lig])
    n = max(len(colonnes), 1)
    tbl = Table(donnees, colWidths=[largeur * 0.2] + [largeur * 0.8 / n] * n, repeatRows=1)
    sty = [("BACKGROUND", (1, 0), (-1, 0), couleurs["entete"]), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")), ("PADDING", (0, 0), (-1, -1), 2)]
    sty += [("BACKGROUND", (j + 1, i + 1), (j + 1, i + 1), teinte(x)) for i, lig in enumerate(valeurs) for j, x in enumerate(lig)]
    tbl.setStyle(TableStyle(sty))
    return tbl


def _entete_groupes(a, cle, st):
    return ([Paragraph(_esc_rg(_ptf(cle, v1=a.get("groupe", ""), v2=a.get("table", ""))), st["sous"])]
            + [Paragraph("• " + _esc_rg(x), st["normal"]) for x in a.get("lecture") or []]
            + [Paragraph(_esc_rg(x), st["muted"]) for x in a.get("avertissements") or []])


def _pdf_continues(a, table, largeur, couleurs, st):
    g, out = a["global"], _entete_groupes(a, "sg.titre.continues", st)
    out.append(_tableau([_pt("sg.col.variable"), _pt("sg.col.moyenne"), _pt("sg.col.ecart_type"), "VIF", "MSA"],
                        [[v["nom"], _nombre(v["moyenne"]), _nombre(v["ecart_type"]), ("(!) " if v["redondante"] else "") + _nombre(v["vif"]), _nombre(v["msa"])] for v in a["variables"]],
                        largeur, couleurs, st))
    out += [Paragraph(_esc_rg(_tj("468:62")), st["sous"]), _matrice_page(a["matrice"], "pearson", largeur, couleurs, st),
            Paragraph(_esc_rg(_tj("468:155")), st["sous"]), _matrice_page(a["matrice"], "spearman", largeur, couleurs, st)]
    out += [Paragraph(_esc_rg(_pt("sg.titre.paires")), st["sous"]),
            _tableau([_tj("lbl_var1"), _tj("lbl_var2"), "Pearson r", "Spearman", _pt("sg.col.p"), _pt("sg.col.force")],
                     [[q["a"], q["b"], _nombre(q["valeur"]), _nombre(q["spearman"]), _p_txt(q["p"]), q["force"]] for q in a["paires"]], largeur, couleurs, st)]
    bart = g.get("bartlett") or {}
    out += [Paragraph(_esc_rg(_pt("sg.titre.indicateurs")), st["sous"]),
            _tableau(["KMO", _pt("sg.col.bartlett"), _pt("sg.col.alpha"), _pt("sg.col.dimensions"), _pt("sg.col.n")],
                     [[_nombre(g.get("kmo")), _p_txt(bart.get("p")), _nombre(g.get("alpha_standardise")), g.get("kaiser"), a.get("n")]], largeur, couleurs, st),
            Paragraph(_esc_rg(_pt("sg.titre.valeurs_propres")), st["sous"]),
            _tableau([_pt("sg.col.composante"), _pt("sg.col.valeur_propre"), _pt("sg.col.pct"), _pt("sg.col.cumul")],
                     [[i + 1, _nombre(v["valeur"]), _pct_page(v["pct"]), _pct_page(v["cumul"])] for i, v in enumerate(g["valeurs_propres"])], largeur, couleurs, st)]
    if a["aberrantes"]["n"]:
        out += [Paragraph(_esc_rg(_ptf("sg.titre.aberrantes", v1=a["aberrantes"]["n"])), st["sous"]),
                _tableau([_pt("sg.col.ligne"), _pt("sg.col.d2")], [[x["ligne"], _nombre(x["d2"])] for x in a["aberrantes"]["lignes"]], largeur, couleurs, st)]
    return out


def _pdf_qualitatives(a, table, largeur, couleurs, st):
    out = _entete_groupes(a, "sg.titre.qualitatives", st)
    out.append(_tableau([_pt("sg.col.variable"), _pt("sg.col.modalites"), _pt("sg.col.diversite"), _pt("sg.col.v_moyen"), _pt("sg.col.plus_lie")],
                        [[v["nom"], v["modalites"], _nombre(v["diversite"]), _nombre(v["v_moyen"]), v["plus_lie"]] for v in a["variables"]], largeur, couleurs, st))
    out += [Paragraph(_esc_rg(_pt("sg.titre.matrice_v")), st["sous"]), _matrice_page(a["matrice"], "cramer", largeur, couleurs, st),
            Paragraph(_esc_rg(_pt("sg.titre.matrice_theil")), st["sous"]), _matrice_page(a["matrice"], "theil", largeur, couleurs, st),
            Paragraph(_esc_rg(_pt("sg.titre.paires")), st["sous"]),
            _tableau([_tj("lbl_var1"), _tj("lbl_var2"), _pt("sg.col.v"), _pt("sg.col.v_corrige"), _pt("sg.col.ddl"), _pt("sg.col.p"), _pt("sg.col.p_ajuste"), _pt("sg.col.force")],
                     [[q["a"], q["b"], _nombre(q["valeur"]), _nombre(q["v_corrige"]), q["ddl"], _p_txt(q["p"]), _p_txt(q["p_ajuste"]), q["force"]] for q in a["paires"]], largeur, couleurs, st)]
    if a.get("profils"):
        out += [Paragraph(_esc_rg(_ptf("sg.titre.profils", v1=a.get("n_profils"))), st["sous"]),
                _tableau([_pt("sg.col.profil"), _pt("sg.col.effectif"), _pt("sg.col.frequence")],
                         [[" · ".join(f"{k} = {v}" for k, v in p["modalites"].items()), p["effectif"], _pct_page(p["frequence"])] for p in a["profils"]], largeur, couleurs, st)]
    return out


def _pdf_par_quali(a, table, largeur, couleurs, st):
    out, m = _entete_groupes(a, "sg.titre.par_quali", st), a.get("multivarie")
    if m:
        out += [Paragraph(_esc_rg(_pt("sg.titre.manova")), st["sous"]),
                _tableau([_pt("sg.col.test"), _pt("sg.col.statistique"), "F", _pt("sg.col.ddl"), _pt("sg.col.p")],
                         [["Wilks Λ", _nombre(m["wilks"]["lambda"]), _nombre(m["wilks"]["f"]), f"{_nombre(m['wilks']['ddl1'], 0)} ; {_nombre(m['wilks']['ddl2'], 0)}", _p_txt(m["wilks"]["p"])],
                          ["Pillai V", _nombre(m["pillai"]["trace"]), _nombre(m["pillai"]["f"]), f"{_nombre(m['pillai']['ddl1'], 0)} ; {_nombre(m['pillai']['ddl2'], 0)}", _p_txt(m["pillai"]["p"])]],
                         largeur, couleurs, st)]
    out += [Paragraph(_esc_rg(_pt("sg.titre.tests_variable")), st["sous"]),
            _tableau([_pt("sg.col.variable"), "F", _pt("sg.col.p"), _pt("sg.col.p_ajuste"), "η²", "Kruskal-Wallis p", _pt("sg.col.levene"), _pt("sg.col.plus_haut"), _pt("sg.col.plus_bas")],
                     [[v["nom"], _nombre(v["f"]), _p_txt(v["p"]), _p_txt(v["p_ajuste"]), _nombre(v["eta2"]), _p_txt(v["kruskal_p"]), _p_txt(v["levene_p"]), v["plus_haut"], v["plus_bas"]] for v in a["variables"]],
                     largeur, couleurs, st),
            Paragraph(_esc_rg(_pt("sg.titre.profil_groupes")), st["sous"]), Paragraph(_esc_rg(_pt("sg.aide.profil")), st["muted"]),
            _matrice_rect_page(a["profil"]["groupes"], a["profil"]["variables"], a["profil"]["z"], 1.5, largeur, couleurs, st)]
    if a.get("distances"):
        out += [Paragraph(_esc_rg(_pt("sg.titre.distances")), st["sous"]), Paragraph(_esc_rg(_pt("sg.aide.distances")), st["muted"]),
                _matrice_rect_page(a["distances"]["groupes"], a["distances"]["groupes"], a["distances"]["d2"], 4, largeur, couleurs, st)]
    for v in [v for v in a["variables"] if v.get("graphique")][:3]:     # boîtes à moustaches des variables qui séparent le mieux les groupes
        out.append(Paragraph(_esc_rg(v["nom"]), st["sous"]))
        _sure(lambda v=v: _fig_boites([(f"{q.get('nom')} (n = {q.get('n')})", q["boite"]) for q in v["graphique"].get("groupes") or []], largeur), table, out)
    return out


def _bloc_groupes(table, largeur, couleurs, st, cache=None):
    """Statistiques groupées du rapport : variables continues entre elles, qualitatives entre elles, continues selon les variables qualitatives."""
    _pal_raz()
    st = {**st, "sous": ParagraphStyle("RGSousGroupes", parent=st["sous"], keepWithNext=1)}
    res = _analyses_groupes(table, cache)
    out = []
    for cle, rendu in (("continues", _pdf_continues), ("qualitatives", _pdf_qualitatives)):
        for a in (res.get(cle) or {}).get("analyses") or []:
            try:
                out += rendu(a, table, largeur, couleurs, st)
            except Exception:
                _log.exception(_gt("rg5.msg.080"), table.id)
    for r in res.get("par") or []:
        for a in r.get("analyses") or []:
            try:
                out += _pdf_par_quali(a, table, largeur, couleurs, st)
            except Exception:
                _log.exception(_gt("rg5.msg.080"), table.id)
    return out or [Paragraph(_esc_rg(_pt("sg.pdf.aucune")), st["normal"])]


def _bloc_factoriel(table, largeur, couleurs, st, cache=None):
    _pal_raz()   # nouvelle analyse = palette depuis le début, comme à l'ouverture de la page
    from ..views_analyses import calculer_factorielle
    out = []
    try:
        res = calculer_factorielle([table], _refs_page(table))   # = ce que la page Analyse factorielle affiche (méthode automatique), même fonction
    except ValueError as e:
        return [Paragraph(_esc_rg(e), st["normal"])]
    if cache is not None:
        cache["factoriel"] = res   # relu par la synthèse, sans recalcul
    cle, a = res["methode"], res["analyse"]
    out.append(Paragraph(_esc_rg(cle.upper()), st["sous"]))
    out.append(Paragraph(_esc_rg(res["pourquoi"]), st["muted"]))
    for av in (a.get("avertissements") or [])[:5]:
        out.append(Paragraph("⚠ " + _esc_rg(av), st["muted"]))
    if True:
        axes = a.get("axes_retenus") or {}
        if axes:
            out.append(Paragraph(_esc_rg(_pt("engine/reports/_part_007.py:pdf:507:41") + ", ".join(f"{k} = {v}" for k, v in axes.items())), st["normal"]))
        out.append(Spacer(1, 0.1 * cm))
        out.append(_tableau_valeurs_propres(a, largeur, couleurs, st))
        try:
            eb = _figure_eboulis(a, largeur)
            if eb is not None:
                out += [Spacer(1, 0.1 * cm), eb]
        except Exception:
            _log.exception(_gt("rg5.msg.083"), table.id)
        try:
            if cle == "acp":
                fleches = [(v["nom"], v["coord"][0], v["coord"][1]) for v in a.get("variables") or [] if len(v.get("coord") or []) >= 2]
                pts = [(f"#{i['ligne']}", i["coord"][0], i["coord"][1]) for i in a.get("individus") or [] if len(i.get("coord") or []) >= 2]
                for titre_fig, p, f in ((_pt("engine/reports/_part_007.py:pdf:520:41"), None, fleches), (_pt("engine/reports/_part_007.py:pdf:520:97"), pts, None)):
                    img = _figure_plan(titre_fig, p, largeur, f)
                    if img is not None:
                        out += [Spacer(1, 0.1 * cm), img]
                if a.get("variables"):
                    out += [Spacer(1, 0.1 * cm), _tableau([_pt("engine/reports/_part_007.py:pdf:525:59"), _pt("engine/reports/_part_007.py:pdf:525:71"), _pt("engine/reports/_part_007.py:pdf:525:80"), _pt("engine/reports/_part_007.py:pdf:525:89"), _pt("engine/reports/_part_007.py:pdf:525:111")],
                            [[v["nom"], _nombre((v.get("coord") or [None])[0], 3), _nombre((v.get("coord") or [None, None])[1] if len(v.get("coord") or []) > 1 else None, 3),
                              _nombre((v.get("contrib") or [None])[0], 1), _nombre((v.get("contrib") or [None, None])[1] if len(v.get("contrib") or []) > 1 else None, 1)]
                             for v in a["variables"][:12]], largeur, couleurs, st)]
            elif cle == "acm":
                pts = [(f"{m['variable']}: {m['modalite']}", m["coord"][0], m["coord"][1]) for m in a.get("modalites") or [] if len(m.get("coord") or []) >= 2]
                img = _figure_plan(_pt("engine/reports/_part_007.py:pdf:531:35"), pts[:60], largeur)
                if img is not None:
                    out += [Spacer(1, 0.1 * cm), img]
            else:
                pts = [(f"L: {m['modalite']}", m["coord"][0], m["coord"][1]) for m in a.get("lignes") or [] if len(m.get("coord") or []) >= 2]
                pts += [(f"C: {m['modalite']}", m["coord"][0], m["coord"][1]) for m in a.get("colonnes") or [] if len(m.get("coord") or []) >= 2]
                img = _figure_plan(_pt("engine/reports/_part_007.py:pdf:537:35"), pts[:60], largeur)
                if img is not None:
                    out += [Spacer(1, 0.1 * cm), img]
                kh = a.get("khi2") or {}
                if kh:
                    out.append(Paragraph(_esc_rg(_ptf("engine/reports/_part_007.py:pdf:542:49", v1=_nombre(kh.get('valeur')), v2=kh.get('ddl'), v3=_p_txt(kh.get('p')), v4=_nombre(kh.get('cramer'), 3))), st["normal"]))
        except Exception:
            _log.exception(_gt("rg5.msg.084"), table.id)
        for av in (a.get("avertissements") or [])[:5]:
            out.append(Paragraph("⚠ " + _esc_rg(av), st["muted"]))
    return out


# ─────────────────────────────────────────────────────────────
# 9. Séries temporelles : la page « Séries temporelles » telle quelle (mêmes appels, mêmes blocs, mêmes libellés)
# ─────────────────────────────────────────────────────────────
def _L(cle, **kw):
    """Libellé de la page Séries temporelles (même catalogue que son JavaScript) : le PDF parle comme la page."""
    cle = "engine/static/engine/js/series_temporelles.js:" + cle
    return _ptf(cle, **kw) if kw else _pt(cle)


def _f4(v):
    return "—" if v is None else (f"{v:.4g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))


def _pct(v):
    return "—" if v is None else _f4(v) + " %"


def _courbes_serie(lignes, largeur, titre=None, hauteur=2.3, seuil=None, barres=None, repere=None, lib_barres=None, lib_seuil=None):
    """Figure de courbes [(valeurs, etiquette, style)] ou de barres, aux couleurs du rapport — l'équivalent des SVG de la page."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.4, hauteur))
    pal = _pal_suite(len(lignes) + ((len(barres) + (1 if seuil is not None else 0)) if barres is not None else (1 if seuil is not None else 0)))
    k = 0
    for vals, nom, sty in lignes:
        ax.plot(range(len(vals)), [float("nan") if v is None else v for v in vals], sty or "-", lw=1.3, label=nom, color=pal[k]); k += 1
    if barres is not None:
        ax.bar(range(len(barres)), barres, color=pal[k:k + len(barres)]); k += len(barres)   # une couleur par barre
    if seuil is not None:
        ax.axhspan(-seuil, seuil, color=pal[k], alpha=0.15)   # bande : sa propre couleur
    if repere is not None:
        ax.axvline(repere, color="#9ca3af", ls="--", lw=0.8)
    ax.axhline(0, color="#d1d5db", lw=0.6)
    ax.tick_params(labelsize=7)
    if titre:
        ax.set_title(titre, fontsize=8.5)
    if any(n for _v, n, _s in lignes):
        ax.legend(fontsize=7, loc="best")
    _ent = []
    if barres is not None and lib_barres:
        _ent += [(pal[len(lignes) + i], lib_barres(i), "carre") for i in range(len(barres))]
    if seuil is not None and lib_seuil:
        _ent.append((pal[len(lignes) + (len(barres) if barres is not None else 0)], lib_seuil, "carre"))
    if _ent:
        _legende_couleurs(ax, _ent, bas=-0.22, ncol=8, taille=5.5)
    fig.tight_layout()
    return _image_figure(fig, largeur)


def _carte_serie(res, largeur, couleurs, st):
    """Une série, dans l'ordre exact de la carte de la page (series_temporelles.js : carte())."""
    out = [Paragraph(_esc_rg(res.get("colonne") or res.get("nom")), st["sous"])]
    if res.get("erreur"):
        return out + [Paragraph(_esc_rg(res["erreur"]), st["muted"])]
    mr, q, tend, pr, dg = res["modele_retenu"], res["qualite"], res["tendance"], res["prevision"], res.get("diagnostic") or {}
    v = q.get("validation")
    badges = [res["frequence"]["libelle"], (_L("saisonnalite_periode") + str(res.get("periode"))) if res["saisonnalite"].get("detectee") else _L("383:93"),
              mr["methode_libelle"], None if mr.get("modele") == "aucun" else _L("385:39") + str(mr.get("modele")),
              None if mr.get("degre") is None else (_L("lbl_tendance_parabolique") if mr["degre"] == 2 else _L("lbl_tendance_droite")), tend.get("sens")]
    out += [Paragraph(_esc_rg(" · ".join(str(b) for b in badges if b)), st["muted"]), Paragraph(_esc_rg(res.get("resume", "")), st["normal"]),
            Paragraph(_esc_rg(_L("407:132") + str(mr.get("raison", ""))), st["muted"])]
    out += [Paragraph("⚠ " + _esc_rg(a), st["muted"]) for a in res.get("avertissements") or []]
    n = len(res["y"])   # graphique principal : série, ajustement, tendance (réglage par défaut de la page), estimations + intervalle
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    pg = _pal_suite(5)   # série, ajustement, tendance, moyenne mobile, estimations (comme series_temporelles.js)
    ax.plot(range(n), [float("nan") if e is None else e for e in res["y"]], color=pg[0], lw=1.5, label=_L("391:88").strip())
    ax.plot(range(n), [float("nan") if e is None else e for e in res["ajustement"]], "--", color=pg[1], lw=1.1, label=_L("lbl_ajustement"))
    ax.plot(range(n), [float("nan") if e is None else e for e in res["tendance_serie"]], ":", color=pg[2], lw=1.2, label=_L("lbl_tendance"))
    xs = list(range(n - 1, n + len(pr["valeur"])))
    ax.fill_between(xs, [res["y"][-1]] + pr["bas"], [res["y"][-1]] + pr["haut"], color=pg[4], alpha=0.25)
    ax.plot(xs, [res["y"][-1]] + pr["valeur"], color=pg[4], lw=2, label=_L("394:92").strip() + " " + str(round(pr["confiance"] * 100)) + " %")
    ax.axvline(n - 1, color="#9ca3af", ls="--", lw=0.8, label="Début des estimations")
    etiq = list(res["x"]) + list(pr["x"])
    ticks = sorted({0, (len(etiq) - 1) // 2, len(etiq) - 1})
    ax.set_xticks(ticks); ax.set_xticklabels([str(etiq[i]) for i in ticks], fontsize=7); ax.tick_params(axis="y", labelsize=7)
    ax.legend(fontsize=7, loc="best"); fig.tight_layout()
    out.append(_image_figure(fig, largeur))
    sa = res.get("saison")
    if sa:
        out += [Paragraph(_esc_rg(_L("coeff_saisonniers_ouvrant") + (_L("lbl_coefficient_multiplicateur") if sa.get("modele") == "multiplicatif" else _L("409:167")) + ")"), st["sous"]),
                _courbes_serie([], largeur, hauteur=1.8, barres=sa["indices"], repere=None, lib_barres=lambda i: str((sa.get("phases") or [])[i]) if i < len(sa.get("phases") or []) else "Phase %d" % (i + 1))]
    va = res.get("variations")
    if va:
        jeux = [(va[c], t, None) for c, t in (("periode", _L("233:30")), ("glissement", _L("233:102")), ("cvs_periode", _L("234:22"))) if va.get(c)]
        out += [Paragraph(_esc_rg(_L("411:39")), st["sous"]), _courbes_serie(jeux, largeur, hauteur=2.4, repere=va.get("separation", 1) - 1)]
    reg = res.get("regresseurs") or []
    if any(r.get("retenu") or r.get("type") == "explicative" for r in reg):
        nature = {"rupture": _L("250:29"), "explicative": _L("nature_explicative"), "calendrier": _L("nature_calendrier"), "saisonnalite": _L("nature_saisonnalite")}
        out += [Paragraph(_esc_rg(_L("412:110")), st["sous"]), _tableau([_L("lbl_effet"), _L("lbl_nature"), _L("lbl_poids"), _L("251:48")],
                [[r["nom"], nature.get(r["type"], r["type"]), (_f4(r.get("coef")) if r.get("retenu") else "—"), _L("oui") if r.get("retenu") else _L("253:115") + _f4(r.get("p")) + ")"] for r in reg], largeur, couleurs, st)]
    g = res.get("glissante")
    if g:
        out += [Paragraph(_esc_rg(_L("lbl_validation_glissante")), st["sous"]),
                Paragraph(_esc_rg(_L("257:55", v1=g["plis"], v2=g["horizon"], v3=_f4(g["rmse"]), v4=_f4(g["mae"]), v5="" if g.get("mape") is None else ", " + _pct(g["mape"]))
                                  + _L("258:6", v1=round(g["couverture"] * 100), v2=_f4(g.get("rmse_naif")))), st["normal"]),
                _tableau([_L("260:15"), _L("260:37"), "RMSE", "MAE"], [[o["apres"], o["methode"], _f4(o["rmse"]), _f4(o["mae"])] for o in g.get("origines") or []], largeur, couleurs, st)]
        if len(g.get("par_methode") or []) > 1:
            out += [Paragraph(_esc_rg(_L("261:88")), st["sous"]), _tableau([_L("262:17"), "RMSE", "MAE", _L("lbl_intervalle_juste"), _L("262:63")],
                    [[m["methode_libelle"], _f4(m["rmse"]), _f4(m["mae"]), str(round(m["couverture"] * 100)) + " %", "—" if m.get("gain_naif") is None else _pct(m["gain_naif"] * 100)] for m in g["par_methode"]], largeur, couleurs, st)]
        out += [_tableau([_L("265:82"), "RMSE"], [[k + 1, _f4(x)] for k, x in enumerate(g.get("par_pas") or [])], largeur, couleurs, st)]
    if dg:
        f = dg.get("fiabilite") or {}
        out += [Paragraph(_esc_rg(_L("414:39")), st["sous"]), Paragraph(_esc_rg(" ".join([{"bonne": _L("268:64"), "moyenne": _L("268:92"), "faible": _L("268:121")}.get(f.get("niveau"), "")] + list(f.get("phrases") or []))), st["normal"]),
                _courbes_serie([(dg.get("residus") or [], "Écart entre le modèle et les données", "-")], largeur, titre=_L("270:39"), hauteur=1.9, seuil=2 * (q.get("sigma") or 1), lib_seuil="Bande d'écart normal (±2 écarts-types)")]
        ac, d_ = dg.get("autocorrelation") or {}, dg
        if ac.get("acf"):
            out += [Paragraph(_esc_rg(_L("272:41")), st["muted"]), _courbes_serie([], largeur, titre=_L("272:188"), hauteur=1.8, seuil=ac.get("seuil"), barres=ac["acf"], lib_barres=lambda i: "Décalage %d" % i, lib_seuil="Zone sans autocorrélation significative")]
        stat = d_.get("stationnarite")
        out.append(_tableau([_L("273:17"), _L("273:29")], [
            [_L("274:22"), (_L("oui") if d_["blancheur"]["ok"] else _L("non")) + " — p = " + _f4(d_["blancheur"]["p"])],
            [_L("275:22"), (_L("oui") if d_["normalite"]["ok"] else _L("non")) + " — p = " + _f4(d_["normalite"]["p"])],
            [_L("276:22"), ((_L("oui") if stat["stationnaire"] else _L("non")) + _L("276:149") + _f4(stat["statistique"]) + _L("276:176") + _f4(stat["seuils"]["5%"]) + ")") if stat else _L("276:239")],
            [_L("277:22"), ", ".join(str(a["x"]) for a in d_.get("aberrantes") or []) or "—"]], largeur, couleurs, st))
    out += [Paragraph(_esc_rg(_L("lbl_indicateurs")), st["sous"]), _tableau([_L("lbl_indicateur"), _L("395:41")], [
        [_L("396:50") if tend.get("propre_au_modele") else _L("lbl_tendance"), str(tend.get("equation")) + _L("396:125")],
        [_L("397:18"), _f4(tend.get("pente_par_periode")) + " " + str(tend.get("unite"))], [_L("398:18"), _f4(q.get("rmse")) + " / " + _f4(q.get("mae"))],
        [_L("399:18"), _pct(q.get("mape"))], [_L("400:18"), "—" if q.get("r2") is None else _f4(q["r2"] * 100) + " %"],
        [_L("401:18") + str(v["k"] if v else 0) + _L("401:58"), ("RMSE " + _f4(v["rmse"]) + _L("401:183") + str(round(v["couverture"] * 100)) + _L("401:166")) if v else _L("401:182")],
        [_L("402:18"), str(res["n"]) + _L("402:37") + str(res["donnees"]["interpoles"]) + _L("403:172") + str(res["donnees"]["ignores"]) + _L("403:212")]], largeur, couleurs, st)]
    e = res.get("exploration")
    if e:
        s, c = e["statistiques"], e["croissance"]
        out += [Paragraph(_esc_rg(_L("416:62")), st["sous"]), Paragraph(_esc_rg(_L("lbl_statistiques_descriptives")), st["muted"]), _tableau([_L("320:15"), _L("320:29")], [
            [_L("321:20"), _f4(s.get("moyenne")) + " / " + _f4(s.get("mediane"))], [_L("322:20"), _f4(s.get("ecart_type"))],
            [_L("323:20"), _f4(s.get("min")) + " (" + str(s.get("x_min")) + ")"], [_L("323:79"), _f4(s.get("max")) + " (" + str(s.get("x_max")) + ")"], [_L("324:20"), _f4(s.get("etendue"))],
            [_L("325:20"), _f4(s.get("q25")) + _L("lbl_a") + _f4(s.get("q75"))], [_L("326:20"), _f4(s.get("q05")) + " / " + _f4(s.get("q95"))],
            [_L("327:20"), _f4(s.get("asymetrie"))], [_L("lbl_concentration"), _f4(s.get("aplatissement"))], [_L("329:20") + str(s.get("n")), _f4(s.get("somme"))]], largeur, couleurs, st),
            Paragraph(_esc_rg(_L("lbl_croissance")), st["muted"]), _tableau([_L("331:15"), _L("331:29")], [
            [_L("332:20"), _pct(c.get("variation_totale"))], [_L("333:20"), _L("333:73") if c.get("tcac") is None else _pct(c["tcac"])],
            [_L("334:20"), f"{c.get('hausses')} / {c.get('baisses')} / {c.get('stables')}"], [_L("335:20"), f"{c.get('serie_hausses')} / {c.get('serie_baisses')}" + _L("336:124")],
            [_L("lbl_meilleure_variation"), "—" if not c.get("meilleure") else _pct(c["meilleure"]["pct"]) + " (" + str(c["meilleure"]["x"]) + ")"],
            [_L("lbl_pire_variation"), "—" if not c.get("pire") else _pct(c["pire"]["pct"]) + " (" + str(c["pire"]["x"]) + ")"]], largeur, couleurs, st),
            Paragraph(_esc_rg(_L("338:39")), st["muted"])]
        out.append(_tableau([_L("339:36"), _L("339:47"), _L("339:57"), _L("339:76")], [[a["x"], _f4(a.get("valeur")), _f4(a.get("attendu")), _f4(a.get("z"))] for a in e["atypiques"]], largeur, couleurs, st) if e.get("atypiques") else Paragraph(_esc_rg(_L("340:41")), st["muted"]))
        if e.get("retournements"):
            out += [Paragraph(_esc_rg(_L("341:69")), st["muted"]), _tableau([_L("342:17"), _L("lbl_nature"), _L("342:38")], [[r["x"], r["type"], _f4(r["valeur"])] for r in e["retournements"]], largeur, couleurs, st)]
        if e.get("cycles"):
            out += [Paragraph(_esc_rg(_L("lbl_cycles_dominants")), st["muted"]), _tableau([_L("345:17"), _L("345:29")], [[_f4(k["periode"]) + _L("346:186"), _pct(k["part"])] for k in e["cycles"]], largeur, couleurs, st)]
        h = e.get("histogramme")
        if h:
            out += [Paragraph(_esc_rg(_L("343:42")), st["muted"]), _courbes_serie([], largeur, hauteur=1.8, barres=h["comptes"], lib_barres=lambda i: ("%s – %s" % (_f4(h["bornes"][i]), _f4(h["bornes"][i + 1]))) if len(h.get("bornes") or []) > i + 1 else "Classe %d" % (i + 1))]
        if e.get("autocorrelation"):
            out += [Paragraph(_esc_rg(_L("346:67")), st["muted"]), _courbes_serie([], largeur, titre=_L("347:72"), hauteur=1.8, seuil=e["autocorrelation"].get("seuil"), barres=e["autocorrelation"]["acf"], lib_barres=lambda i: "Décalage %d" % i, lib_seuil="Zone sans autocorrélation significative")]
        if e.get("volatilite"):
            out += [Paragraph(_esc_rg(_L("348:42") + str(e["volatilite"]["fenetre"]) + _L("349:126")), st["muted"]), _courbes_serie([(e["volatilite"]["valeurs"], "Volatilité (écart-type glissant des variations)", "-")], largeur, hauteur=1.8)]
        if e.get("base100"):
            out += [Paragraph(_esc_rg(_L("lbl_indice_base_100")), st["muted"]), _courbes_serie([(e["base100"], "Indice base 100", "-")], largeur, hauteur=1.8)]
        pf = e.get("profil")
        if pf:
            out += [Paragraph(_esc_rg(_L("350:58")), st["muted"]), _tableau(["Cycle"] + list(pf["phases"]), [[l["x"]] + [_f4(x) for x in l["valeurs"]] for l in pf["lignes"]] + [[_L("312:41")] + [_f4(x) for x in pf["moyenne"]]], largeur, couleurs, st)]
    out += [Paragraph(_esc_rg(_L("417:39") + str(pr["horizon"]) + _L("417:75")), st["sous"]),
            _tableau([_L("404:29"), _L("lbl_estimation"), "Bas", _L("404:110")], [[x, _f4(pr["valeur"][k]), _f4(pr["bas"][k]), _f4(pr["haut"][k])] for k, x in enumerate(pr["x"])], largeur, couleurs, st),
            Paragraph(_esc_rg(_L("418:42") + str(len(res.get("candidats") or [])) + ")"), st["muted"]),
            _tableau([_L("405:29"), _L("405:40"), _L("lbl_tendance"), _L("405:62"), _L("405:86")], [[c_["methode_libelle"], c_["modele"], "—" if c_.get("degre") is None else ("parabole" if c_["degre"] == 2 else "droite"), _f4(c_.get("rmse_validation")), _f4(c_.get("rmse_ajustement"))] for c_ in res.get("candidats") or []], largeur, couleurs, st)]
    return out


def _comparaison_series(c, largeur, couleurs, st):
    """Bloc « Liens entre les séries » de la page (blocComparaison) : matrice des corrélations + paires les plus liées."""
    def phrase(p):
        d = p.get("decalage")
        return "—" if d is None else _L("354:70") if d == 0 else (f"« {p['a']}" + _L("354:119") + f"{p['b']}" + _L("guillemet_de") + f"{d}" + _L("355:253")) if d > 0 else (f"« {p['b']}" + _L("354:196") + f"{p['a']}" + _L("guillemet_de") + f"{-d}" + _L("355:377"))
    return [Paragraph(_esc_rg(_L("356:85")), st["sous"]), Paragraph(_esc_rg(_L("357:35")), st["muted"]),
            _tableau([""] + list(c["noms"]), [[n] + [_f4(x) for x in c["matrice"][i]] for i, n in enumerate(c["noms"])], largeur, couleurs, st),
            Paragraph(_esc_rg(_L("360:39")), st["muted"]),
            _tableau([_L("361:15"), _L("361:26"), _L("lbl_variations"), _L("lbl_niveaux"), _L("361:62"), _L("lbl_lien_net")],
                     [[p["a"], p["b"], _f4(p["correlation_variations"]), _f4(p["correlation_niveaux"]), phrase(p) + ((_L("363:34") + _f4(p["correlation_decalee"]) + ")") if p.get("decalage") else ""), _L("oui") if p.get("significatif") else _L("non")] for p in c["paires"]], largeur, couleurs, st)]


def _bloc_series(table, largeur, couleurs, st, cache=None):
    """Ce que la page Séries temporelles affiche à l'ouverture : mêmes paramètres (95 %, jours fériés FR), mêmes fonctions, mêmes blocs."""
    from django.http import QueryDict
    from .. import series_donnees as sd
    _pal_raz()   # un seul compteur pour toute la page Séries temporelles
    from ..views_series import _parametres
    tables = sd.analyser_tables([table], **_parametres(QueryDict("confiance=0.95&pays=FR")))
    if cache is not None:
        cache["series"] = tables   # relu par la synthèse, sans recalcul
    out = []
    for t_ in tables:
        if t_.get("vide"):
            out.append(Paragraph(_esc_rg(t_["message"]), st["normal"]))
        for s in t_.get("series") or []:
            out += _carte_serie(s, largeur, couleurs, st)
    comp = sd.comparer_resultats(tables)
    if comp:
        out += _comparaison_series(comp, largeur, couleurs, st)
    return out


# ─────────────────────────────────────────────────────────────
# 11. Synthèse : la vue d'ensemble du décideur final — autant de pages que nécessaire, jamais de contenu qui se superpose
# ─────────────────────────────────────────────────────────────
# Rien n'est réécrit ici : la synthèse RELIT les résultats que les autres sections viennent de calculer (le `cache` d'une table, rempli par
# _bloc_statistiques / _bloc_anova / _bloc_factoriel / _bloc_series) et, s'ils n'existent pas encore (synthèse cochée seule), les calcule
# avec les MÊMES fonctions que les pages d'analyse. Elle résume TOUTES les colonnes (un tableau « une ligne par colonne », puis des graphiques
# de synthèse), pas seulement la première : chiffres clés, indicateurs, ce qu'il faut retenir, points d'attention, colonnes, graphiques,
# une ligne par analyse. Le contenu s'écoule normalement d'une page à l'autre (tableaux coupés avec en-tête répété, figures bornées à la page) :
# AUCUN KeepInFrame « réducteur », qui superposait les lignes quand un tableau à paragraphes multi-lignes était réduit.
MAX_MESSAGES_SYNTHESE = 12      # « ce qu'il faut retenir »
MAX_ATTENTION_SYNTHESE = 15     # points d'attention
MAX_KPI_SYNTHESE = 12           # indicateurs listés (les plus dégradés d'abord)
SEUIL_MANQUANTS_SYNTHESE = 20   # % de valeurs manquantes à partir duquel une colonne devient un point d'attention
SEUIL_CV_SYNTHESE = 30          # % d'écart relatif autour de la moyenne à partir duquel une colonne est dite très dispersée
MAX_NOMS_CITES_SYNTHESE = 8     # colonnes nommées dans une phrase de synthèse (« … et N autre(s) » au-delà)
COLONNES_PAR_FIGURE_SYNTHESE = 25   # colonnes par graphique à barres : au-delà, le graphique est coupé en plusieurs figures lisibles
_LECTURE_NET, _LECTURE_SURVEILLER, _LECTURE_RIEN = "net", "surveiller", ""


def _phrase_resultat(resultat, n=170):
    """Phrase de conclusion d'un résultat libre (analyse exécutée, skill) : première clé de type synthèse/résumé/conclusion qui est un texte, sinon rien."""
    if not isinstance(resultat, dict):
        return ""
    for mot in ("synthese", "resume", "conclusion", "interpretation", "recommandation", "constat"):
        for cle, valeur in resultat.items():
            if mot in str(cle).lower() and isinstance(valeur, str) and valeur.strip():
                return _court(" ".join(valeur.split()), n)
    return ""


def _memo(cache, cle, calcul):
    """Résultat déjà posé dans le `cache` par la section correspondante ; sinon `calcul()` (même fonction que la page), posé à son tour.
    Une ValueError de la page (« pas assez de colonnes »…) devient {"_erreur": message} : la synthèse l'affiche en une ligne."""
    if cle not in cache:
        try:
            cache[cle] = calcul()
        except ValueError as e:
            cache[cle] = {"_erreur": str(e)}
    return cache[cle]


def _donnees_synthese(table, cache=None):
    """Ce qui sert à résumer UNE table : {table, lignes, colonnes, kpis, messages, attention, analyses}.
    `messages` / `attention` = [(section, texte)] ; `analyses` = [(section, ce qui en ressort, lecture)], une entrée par section du rapport, dans l'ordre."""
    cache = {} if cache is None else cache
    lib = dict(sections_rapport_general())
    d = {"table": _nom_vue(table), "lignes": None, "colonnes": None, "kpis": [], "messages": [], "attention": [], "analyses": [],
         "detail_colonnes": {}, "liens": [], "matrice": None}   # detail_colonnes : {nom de colonne: fiche} — UNE fiche par colonne, toutes analyses confondues

    def col(nom):
        """Fiche de synthèse d'une colonne (créée à la première mention, dans l'ordre d'apparition)."""
        nom = str(nom)
        return d["detail_colonnes"].setdefault(nom, {"nom": nom, "type": "autre", "taux_manquants": None, "cv": None, "aberrantes": 0, "resume": "",
                                                     "anova_n": 0, "anova_sig": 0, "fact_n": 0, "fact_sig": 0, "serie": "", "alerte": False})

    def noms_cites(noms):
        """« A, B, C … et N autre(s) » : jamais une phrase de 40 colonnes."""
        noms = [str(n) for n in noms]
        txt = ", ".join(noms[:MAX_NOMS_CITES_SYNTHESE])
        return txt + (" " + _ptf("rg.syn.autres", v1=len(noms) - MAX_NOMS_CITES_SYNTHESE) if len(noms) > MAX_NOMS_CITES_SYNTHESE else "")

    def ligne(i, resume, lecture=_LECTURE_RIEN):
        d["analyses"].append((lib.get(i, i), _court(resume, 420), lecture))

    def msg(i, texte):
        if texte:
            d["messages"].append((lib.get(i, i), _court(texte, 420)))

    def att(i, texte):
        if texte:
            d["attention"].append((lib.get(i, i), _court(texte, 380)))

    def utilisable(i, res):
        """Vrai si `res` est un vrai résultat ; sinon écrit la ligne « sans objet » (ou le message de la page) et renvoie faux."""
        if not res:
            ligne(i, _pt("rg.syn.na"))
        elif isinstance(res, dict) and res.get("_erreur"):
            ligne(i, res["_erreur"])
        else:
            return True
        return False

    def structure():
        d["lignes"] = table.enregistrements_groupe.count()
        d["colonnes"] = table.champs.count()
        ligne("structure", _ptf("rg.syn.structure", v1=d["lignes"], v2=d["colonnes"]))

    def kpi():
        kpis = list(table.kpis.select_related("champ_cible").order_by("ordre", "id"))
        if not kpis:
            return ligne("kpi", _pt("rg.syn.kpi_aucun"))
        n = {"succes": 0, "alerte": 0, "danger": 0}
        for k in kpis:
            unite = f" {k.unite}" if k.unite else ""
            valeur = None if (k.statut == "echec" or k.valeur_actuelle is None) else _fmt_page(k.valeur_actuelle) + unite
            cible = None if k.valeur_cible is None else _fmt_page(k.valeur_cible) + unite
            d["kpis"].append({"nom": k.nom, "valeur": valeur, "cible": cible, "etat": k.etat})
            if k.etat in n:
                n[k.etat] += 1
            if k.etat in ("alerte", "danger"):
                att("kpi", _ptf("rg.syn.kpi_attention", v1=k.nom, v2=valeur or "—", v3=cible or "—"))
        ligne("kpi", _ptf("rg.syn.kpi", v1=len(kpis), v2=n["succes"], v3=n["alerte"], v4=n["danger"]),
              _LECTURE_SURVEILLER if n["alerte"] or n["danger"] else _LECTURE_NET)

    def dashboards():
        if "demandes" not in cache:
            cache["demandes"] = [x for x in DemandeAgent.objects.filter(entreprise=table.entreprise, table_cible=table, statut="execute")
                                 .exclude(texte__startswith="[SKILL #").order_by("cree_le", "id") if x.resultat_json]
        demandes = cache["demandes"]
        if not demandes:
            return ligne("dashboards", _pt("rg.syn.dashboards_aucun"))
        for x in demandes[:2]:
            phrase = _phrase_resultat(x.resultat_json)
            if phrase:
                msg("dashboards", f"{_court(x.texte, 70)} — {phrase}")
        ligne("dashboards", _ptf("rg.syn.dashboards", v1=len(demandes)), _LECTURE_NET)

    def skills():
        if "skills" not in cache:
            cache["skills"] = _skills_executes(table)
        executes = [s for s, _x in cache["skills"]]
        if not executes:
            return ligne("skills", _pt("rg.syn.skills_aucun"))
        for s in executes[:2]:
            phrase = _phrase_resultat(s.resultat_json)
            if phrase:
                msg("skills", f"{s.nom} — {phrase}")
        ligne("skills", _ptf("rg.syn.skills", v1=len(executes), v2=", ".join(s.nom for s in executes[:4])), _LECTURE_NET)

    def bi():
        from ..vue_groupe import ids_groupe
        from ..models import TableauBordBI
        tableaux = list(TableauBordBI.objects.filter(table_source_id__in=ids_groupe(table)).order_by("ordre", "id"))
        ligne("bi", _ptf("rg.syn.bi", v1=len(tableaux), v2=", ".join(tb.nom for tb in tableaux[:4])) if tableaux else _pt("rg.syn.bi_aucun"),
              _LECTURE_NET if tableaux else _LECTURE_RIEN)

    def statistiques():
        from .. import statistiques_donnees as sd
        from .. import statistiques as _stat
        from ..views_statistiques import calculer_descriptives
        res = _memo(cache, "statistiques", lambda: calculer_descriptives([table], _refs_page(table)) if sd.decrire(table) else None)
        if not utilisable("statistiques", res):
            return
        syn = res.get("synthese") or {}
        variables = res.get("variables") or []
        dispersees, aberrantes = [], []
        for v in variables:                                       # UNE fiche par colonne décrite, pas seulement les premières
            nom = v.get("nom")
            if not nom:
                continue
            c = col(nom)
            n_ok, manq = v.get("n") or 0, v.get("manquants") or 0
            c["taux_manquants"] = (manq / (n_ok + manq) * 100) if (n_ok + manq) else None
            if v.get("erreur"):
                c["resume"] = str(v["erreur"])
                continue
            genre = str(v.get("genre") or "")
            if _stat.est_quanti(genre):
                c["type"] = "quanti"
                cv, nb_ab = v.get("cv"), int((v.get("aberrantes") or {}).get("n") or 0)
                c["cv"], c["aberrantes"] = cv, nb_ab
                c["resume"] = _ptf("rg.syn.col_quanti", v1=_fmt_page(v.get("moyenne")), v2=_fmt_page(v.get("mediane")), v3="—" if cv is None else _pct_page(cv), v4=nb_ab)
                if cv is not None and cv >= SEUIL_CV_SYNTHESE:
                    dispersees.append(nom)
                if nb_ab:
                    aberrantes.append(nom)
                c["alerte"] = c["alerte"] or bool(nb_ab) or (cv is not None and cv >= SEUIL_CV_SYNTHESE)
            elif genre == "date":
                c["type"] = "date"
                c["resume"] = _pt("rg.syn.col_date")
            else:
                c["type"] = "quali"
                modalites = [x for x in (v.get("tableau") or []) if not str(x.get("modalite")).startswith(_pt("engine/statistiques.py:g:359:58").split("{v1}")[0])]   # « Autres (… » / « Others (… » selon la langue
                dom = max(modalites, key=lambda x: x.get("frequence") or 0) if modalites else None
                c["resume"] = _ptf("rg.syn.col_quali", v1=v.get("distincts") if v.get("distincts") is not None else "—",
                                   v2=dom.get("modalite") if dom else "—", v3=_pct_page(dom.get("frequence")) if dom else "—")
                if (v.get("distincts") == n_ok and n_ok > 20) or v.get("distincts") == 1:
                    c["alerte"] = True
            if (c["taux_manquants"] or 0) >= SEUIL_MANQUANTS_SYNTHESE:
                c["alerte"] = True
        if dispersees:
            msg("statistiques", _ptf("rg.syn.stats_dispersion", v1=SEUIL_CV_SYNTHESE, v2=noms_cites(dispersees)))
        if aberrantes:
            msg("statistiques", _ptf("rg.syn.stats_aberrantes", v1=noms_cites(aberrantes)))
        liens = []
        for r in res.get("relations") or []:
            for p_ in r.get("paires") or []:
                if p_.get("p") is not None and p_["p"] < 0.05 and p_.get("valeur") is not None:
                    liens.append(p_)
        liens.sort(key=lambda q: -abs(q["valeur"]))
        d["matrice"] = next((r["matrice"] for r in res.get("relations") or [] if len((r.get("matrice") or {}).get("noms") or []) >= 2), None)
        d["liens"] = [{"a": q["a"], "b": q["b"], "valeur": q["valeur"], "mesure": q.get("mesure")} for q in liens[:10]]
        if liens:
            msg("statistiques", _ptf("rg.syn.stats_liens", v1="; ".join(f"{q['a']} ↔ {q['b']} ({_fmt_page(q['valeur'])})" for q in liens[:4])))
        constats = [c_.get("texte") for c_ in syn.get("constats") or [] if c_.get("texte")]
        manquantes = [q for q in syn.get("qualite") or [] if (q.get("taux_manquants") or 0) >= SEUIL_MANQUANTS_SYNTHESE]
        for q in manquantes[:MAX_NOMS_CITES_SYNTHESE]:
            att("statistiques", _ptf("rg.syn.manquantes", v1=q.get("nom"), v2=_pct_page(q.get("taux_manquants"))))
        avertissements = res.get("avertissements") or []
        for a in avertissements[:3]:
            att("statistiques", a)
        ligne("statistiques", _ptf("rg.syn.stats", v1=len(variables), v2=len(constats), v3=len(res.get("relations") or [])),
              _LECTURE_SURVEILLER if manquantes or avertissements or dispersees or aberrantes else (_LECTURE_NET if variables else _LECTURE_RIEN))

    def groupes():
        res = _analyses_groupes(table, cache)
        analyses = [a for cle in ("continues", "qualitatives") for a in (res.get(cle) or {}).get("analyses") or []] + [a for r in res.get("par") or [] for a in r.get("analyses") or []]
        if not analyses:
            return ligne("groupes", _pt("sg.syn.aucun"))
        liens = sum(1 for a in analyses for q in a.get("paires") or [] if abs(q["valeur"]) >= 0.7 and q.get("p") is not None and q["p"] < 0.05)
        effets = sum(1 for a in analyses for v in a.get("variables") or [] if v.get("p_ajuste") is not None and v["p_ajuste"] < 0.05 and a.get("mode") == "continues_par_quali")
        for a in analyses[:MAX_NOMS_CITES_SYNTHESE]:
            if a.get("lecture"):
                msg("groupes", a["lecture"][0] if a["mode"] != "continues_par_quali" or len(a["lecture"]) < 2 else a["lecture"][1])
        for a in analyses:
            for w in (a.get("avertissements") or [])[:1]:
                att("groupes", w)
        ligne("groupes", _ptf("sg.syn.groupes", v1=len(analyses), v2=liens, v3=effets), _LECTURE_NET if liens or effets else _LECTURE_RIEN)

    def anova():
        from ..views_analyses import calculer_anova
        res = _memo(cache, "anova", lambda: calculer_anova([table], _refs_page(table)))
        if not utilisable("anova", res):
            return
        n_tests, n_sig, effets = 0, 0, []
        for a in res.get("analyses") or []:
            reponse = a.get("reponse")
            for u in a.get("un_facteur") or []:
                n_tests += 1
                sig = bool(((u.get("resultats") or {}).get(u.get("retenu")) or {}).get("significatif"))
                if reponse:
                    col(reponse)["anova_n"] += 1
                    col(reponse)["anova_sig"] += 1 if sig else 0
                if u.get("facteur"):
                    col(u["facteur"])["fact_n"] += 1
                    col(u["facteur"])["fact_sig"] += 1 if sig else 0
                if sig:
                    n_sig += 1
                    effets.append(f"{u.get('facteur')} → {reponse}")
        if effets:
            msg("anova", _ptf("rg.syn.anova_effets", v1=n_sig, v2="; ".join(effets[:MAX_NOMS_CITES_SYNTHESE]) + (" " + _ptf("rg.syn.autres", v1=len(effets) - MAX_NOMS_CITES_SYNTHESE) if len(effets) > MAX_NOMS_CITES_SYNTHESE else "")))
        elif n_tests:
            msg("anova", _pt("rg.syn.anova_aucun"))
        ligne("anova", _ptf("rg.syn.anova", v1=n_sig, v2=n_tests), _LECTURE_NET if n_sig else _LECTURE_RIEN)

    def factoriel():
        from ..views_analyses import calculer_factorielle
        res = _memo(cache, "factoriel", lambda: calculer_factorielle([table], _refs_page(table)))
        if not utilisable("factoriel", res):
            return
        a, methode = res.get("analyse") or {}, str(res.get("methode") or "").upper()
        vps = a.get("valeurs_propres") or []
        cumul = (vps[1] if len(vps) > 1 else vps[0]).get("pct_cumule") if vps else None
        texte = _ptf("rg.syn.factoriel", v1=methode, v2=_pct_page(cumul)) if cumul is not None else f"{methode} — {res.get('pourquoi', '')}"
        msg("factoriel", texte)
        avertissements = a.get("avertissements") or []
        for av in avertissements[:2]:
            att("factoriel", av)
        ligne("factoriel", texte, _LECTURE_SURVEILLER if avertissements else _LECTURE_NET)

    def series():
        def calcul():
            from django.http import QueryDict
            from .. import series_donnees as sd
            from ..views_series import _parametres
            return sd.analyser_tables([table], **_parametres(QueryDict("confiance=0.95&pays=FR")))
        res = _memo(cache, "series", calcul)
        if not isinstance(res, list):
            return utilisable("series", res)
        liste = [s for t_ in res if not t_.get("vide") for s in t_.get("series") or [] if not s.get("erreur")]
        if not liste:
            return ligne("series", _pt("rg.syn.na"))
        niveaux = [((s.get("diagnostic") or {}).get("fiabilite") or {}).get("niveau") for s in liste]
        for s, niveau in zip(liste, niveaux):
            nom = s.get("colonne") or s.get("nom")
            if nom:
                col(nom)["serie"] = niveau or ""
                if niveau == "faible":
                    col(nom)["alerte"] = True
                    att("series", _ptf("rg.syn.series_faible", v1=nom))
        faibles = niveaux.count("faible")
        texte = _ptf("rg.syn.series", v1=len(liste), v2=niveaux.count("bonne"), v3=faibles)
        msg("series", texte)
        ligne("series", texte, _LECTURE_SURVEILLER if faibles else _LECTURE_NET)

    def geospatial():
        from .. import geo_donnees
        res = _memo(cache, "geospatial", lambda: geo_donnees.analyser_table(table))
        if not utilisable("geospatial", res):
            return
        if res.get("vide"):
            return ligne("geospatial", res.get("message") or _pt("rg.syn.na"))
        r = res.get("resume") or {}
        texte = _ptf("rg.syn.geo", v1=r.get("objets"), v2=r.get("positions"), v3=_fmt_page((r.get("distance_m") or 0) / 1000),
                     v4=r.get("rencontres"), v5=r.get("collisions_prevues"))
        msg("geospatial", texte)
        collisions = r.get("collisions_prevues") or 0
        if collisions:
            att("geospatial", _ptf("rg.syn.geo_collisions", v1=collisions, v2=_pct_page((r.get("proba_max") or 0) * 100)))
        ligne("geospatial", texte, _LECTURE_SURVEILLER if collisions else _LECTURE_NET)

    for identifiant, construire in (("structure", structure), ("kpi", kpi), ("dashboards", dashboards), ("skills", skills), ("bi", bi),
                                    ("statistiques", statistiques), ("groupes", groupes), ("anova", anova), ("factoriel", factoriel), ("series", series),
                                    ("geospatial", geospatial)):
        try:
            construire()
        except Exception:
            _log.exception(_gt("rg.syn.log"), identifiant, table.id)   # une section en échec = une ligne d'explication, jamais une synthèse cassée
            d["analyses"].append((lib.get(identifiant, identifiant), _pt("rg.syn.indisponible"), _LECTURE_RIEN))
    return d


def _choisir_messages(messages, maxi):
    """Au plus `maxi` messages, répartis entre les sections (le 1ᵉʳ de chacune d'abord, puis le 2ᵉ…), puis remis dans l'ordre du rapport."""
    par_section = {}
    for rang, (section, texte) in enumerate(messages):
        par_section.setdefault(section, []).append((rang, section, texte))
    choisis, tour = [], 0
    while len(choisis) < maxi and any(len(v) > tour for v in par_section.values()):
        for v in par_section.values():
            if len(v) > tour and len(choisis) < maxi:
                choisis.append(v[tour])
        tour += 1
    return [(s, t_) for _r, s, t_ in sorted(choisis)]


def _styles_synthese(st, couleurs):
    return {"theme": ParagraphStyle("RGSynTheme", parent=st["cell"], fontName=_pol("gras"), fontSize=8, leading=10, textColor=couleurs["accent"]),
            "texte": ParagraphStyle("RGSynTexte", parent=st["normal"], fontSize=9, leading=11.5)}


def _liste_themes(lignes, largeur, sy):
    """[(thème, texte)] en deux colonnes sans quadrillage : le thème en gras à gauche, le texte à droite."""
    if not lignes:
        return []
    tbl = Table([[Paragraph(_esc_rg(a), sy["theme"]), Paragraph(_esc_rg(b), sy["texte"])] for a, b in lignes], colWidths=[largeur * 0.2, largeur * 0.8])
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("PADDING", (0, 0), (-1, -1), 3),
                             ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e7eb"))]))
    return [tbl]


def _lecture_txt(lecture):
    return {_LECTURE_NET: _pt("rg.syn.l_net"), _LECTURE_SURVEILLER: _pt("rg.syn.l_surveiller")}.get(lecture, _pt("rg.syn.l_rien"))


def _tableau_kpi_synthese(kpis, largeur, couleurs, st):
    """Indicateurs clés : les plus dégradés d'abord (danger, alerte, atteint). Mêmes libellés d'état que la section KPI."""
    etat_txt = {"succes": _pt("engine/reports/_part_003.py:pdf:714:27"), "alerte": _pt("engine/reports/_part_003.py:pdf:714:61"),
                "danger": _gt("engine/reports/_part_003.py:g:678:82")}
    ordre = {"danger": 0, "alerte": 1, "succes": 2}
    tries = sorted(kpis, key=lambda k: ordre.get(k.get("etat"), 3))
    out = [_tableau([_pt("rg.syn.col_kpi_nom"), _pt("rg.syn.col_valeur"), _pt("rg.syn.col_objectif"), _pt("rg.syn.col_etat")],
                    [[k["nom"], k["valeur"] or "—", k["cible"] or "—", etat_txt.get(k.get("etat"), "—")] for k in tries[:MAX_KPI_SYNTHESE]],
                    largeur, couleurs, st, largeurs=[largeur * 0.4, largeur * 0.2, largeur * 0.2, largeur * 0.2])]
    if len(tries) > MAX_KPI_SYNTHESE:
        out.append(Paragraph(_esc_rg(_ptf("rg.syn.autres", v1=len(tries) - MAX_KPI_SYNTHESE)), st["muted"]))
    return out


def _hex_rl(c):
    """Couleur reportlab → '#rrggbb' pour matplotlib."""
    return "#" + c.hexval()[2:].lower()


def _borner_image(img, largeur, hauteur):
    """Réduit une figure (proportions gardées) pour qu'elle tienne TOUJOURS dans la zone utile d'une page : jamais « flowable trop grand »."""
    if img.drawWidth > largeur:
        k = largeur / img.drawWidth
        img.drawWidth, img.drawHeight = img.drawWidth * k, img.drawHeight * k
    if img.drawHeight > hauteur:
        k = hauteur / img.drawHeight
        img.drawWidth, img.drawHeight = img.drawWidth * k, img.drawHeight * k
    return img


def _figures_barres(titre, noms, valeurs, etiquette_x, couleurs, largeur, hauteur, seuil=None, entier=False):
    """Diagramme à barres horizontales « une barre par colonne », coupé en plusieurs figures lisibles s'il y a beaucoup de colonnes."""
    plt = _plt()
    accent = _hex_rl(couleurs["accent"])
    paires = [(n, v) for n, v in zip(noms, valeurs) if v is not None]
    if not paires:
        return []
    morceaux = [paires[i:i + COLONNES_PAR_FIGURE_SYNTHESE] for i in range(0, len(paires), COLONNES_PAR_FIGURE_SYNTHESE)]
    images = []
    for rang, morceau in enumerate(morceaux, 1):
        fig, ax = plt.subplots(figsize=(7.2, max(1.7, 0.3 * len(morceau) + 1.0)))
        ys = list(range(len(morceau)))
        vals = [float(v) for _n, v in morceau]
        ax.barh(ys, vals, color=["#dc2626" if (seuil is not None and v >= seuil) else accent for v in vals])
        ax.set_yticks(ys)
        ax.set_yticklabels([_court(n, 30) for n, _v in morceau], fontsize=7)
        ax.invert_yaxis()
        ax.set_xlabel(etiquette_x, fontsize=7)
        ax.tick_params(axis="x", labelsize=7)
        maxi = max(vals + [seuil or 0, 1e-9])
        ax.set_xlim(0, maxi * 1.18)
        for y, v in zip(ys, vals):
            ax.text(v + maxi * 0.01, y, _dec(f"{int(v)}" if entier else f"{v:.1f}"), va="center", fontsize=6.5)
        if seuil is not None:
            ax.axvline(seuil, color="#6b7280", linestyle="--", linewidth=0.8)
        for bord in ("top", "right"):
            ax.spines[bord].set_visible(False)
        ax.set_title(titre if len(morceaux) == 1 else f"{titre} ({_ptf('rg.syn.g_partie', v1=rang, v2=len(morceaux))})", fontsize=9)
        fig.tight_layout()
        images.append(_borner_image(_image_figure(fig, largeur), largeur, hauteur))
    return images


def _figure_lectures(d, couleurs, largeur, hauteur):
    """Deux petits diagrammes côte à côte : lecture des colonnes (à surveiller / sans alerte) et des analyses (net / à surveiller / sans objet)."""
    plt = _plt()
    colonnes = list(d["detail_colonnes"].values())
    analyses = [lec for _a, _r, lec in d["analyses"]]
    if not colonnes and not analyses:
        return None
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.5))
    vert, orange, gris = "#16a34a", "#f59e0b", "#9ca3af"
    jeux = [(_pt("rg.syn.g_lecture_titre"), [(_pt("rg.syn.g_sans_alerte"), sum(1 for c in colonnes if not c["alerte"]), vert),
                                             (_pt("rg.syn.g_surveiller"), sum(1 for c in colonnes if c["alerte"]), orange)]),
            (_pt("rg.syn.g_lecture_analyses_titre"), [(_pt("rg.syn.g_net"), analyses.count(_LECTURE_NET), vert),
                                                      (_pt("rg.syn.g_surveiller"), analyses.count(_LECTURE_SURVEILLER), orange),
                                                      (_pt("rg.syn.g_rien"), analyses.count(_LECTURE_RIEN), gris)])]
    for ax, (titre, barres) in zip(axes, jeux):
        ax.bar(range(len(barres)), [b[1] for b in barres], color=[b[2] for b in barres])
        ax.set_xticks(range(len(barres)))
        ax.set_xticklabels([_court(b[0], 16) for b in barres], fontsize=7)
        ax.tick_params(axis="y", labelsize=7)
        ax.set_ylabel(_pt("rg.syn.g_effectif_y"), fontsize=7)
        for i, b in enumerate(barres):
            ax.text(i, b[1], str(b[1]), ha="center", va="bottom", fontsize=7)
        ax.set_title(titre, fontsize=9)
        for bord in ("top", "right"):
            ax.spines[bord].set_visible(False)
    fig.tight_layout()
    return _borner_image(_image_figure(fig, largeur), largeur, hauteur)


def _figure_kpi(kpis, couleurs, largeur, hauteur):
    """État de tous les indicateurs : atteints / en alerte / en danger."""
    if not kpis:
        return None
    plt = _plt()
    etats = [k.get("etat") for k in kpis]
    barres = [(_pt("rg.syn.g_kpi_succes"), etats.count("succes"), "#16a34a"), (_pt("rg.syn.g_kpi_alerte"), etats.count("alerte"), "#f59e0b"),
              (_pt("rg.syn.g_kpi_danger"), etats.count("danger"), "#dc2626")]
    fig, ax = plt.subplots(figsize=(4.6, 2.3))
    ax.bar(range(3), [b[1] for b in barres], color=[b[2] for b in barres])
    ax.set_xticks(range(3))
    ax.set_xticklabels([b[0] for b in barres], fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    for i, b in enumerate(barres):
        ax.text(i, b[1], str(b[1]), ha="center", va="bottom", fontsize=7)
    ax.set_title(_pt("rg.syn.g_kpi_titre"), fontsize=9)
    for bord in ("top", "right"):
        ax.spines[bord].set_visible(False)
    fig.tight_layout()
    return _borner_image(_image_figure(fig, min(largeur, 11 * cm)), largeur, hauteur)


def _graphiques_synthese(d, largeur, hauteur, couleurs, nom_table=""):
    """Les graphiques de synthèse d'UNE table, calculés sur les mêmes données que le tableau des colonnes : manquants, dispersion, effets ANOVA,
    corrélations, état des KPI, lecture des colonnes et des analyses. Un graphique en échec est journalisé et sauté, jamais un PDF cassé."""
    cols = list(d["detail_colonnes"].values())
    sorties = []

    def faire(nom, f):
        try:
            r = f()
            sorties.extend(r if isinstance(r, list) else ([r] if r is not None else []))
        except Exception:
            _log.exception(_gt("rg.syn.log_graphique"), nom, nom_table)

    faire("manquants", lambda: _figures_barres(_pt("rg.syn.g_manquants_titre"), [c["nom"] for c in cols], [c["taux_manquants"] for c in cols],
                                               _pt("rg.syn.g_manquants_x"), couleurs, largeur, hauteur, seuil=SEUIL_MANQUANTS_SYNTHESE))
    quanti = [c for c in cols if c["type"] == "quanti" and c["cv"] is not None]
    faire("dispersion", lambda: _figures_barres(_pt("rg.syn.g_cv_titre"), [c["nom"] for c in quanti], [abs(c["cv"]) for c in quanti],
                                                _pt("rg.syn.g_cv_x"), couleurs, largeur, hauteur, seuil=SEUIL_CV_SYNTHESE))
    influentes = [c for c in cols if (c["anova_sig"] + c["fact_sig"]) > 0]
    faire("anova", lambda: _figures_barres(_pt("rg.syn.g_anova_titre"), [c["nom"] for c in influentes], [c["anova_sig"] + c["fact_sig"] for c in influentes],
                                           _pt("rg.syn.g_anova_x"), couleurs, largeur, hauteur, entier=True))
    if d.get("matrice"):
        def corr():
            img = _matrice_correlations(d["matrice"], largeur)
            return _borner_image(img, largeur, hauteur) if img is not None else None
        faire("correlations", corr)
    faire("kpi", lambda: _figure_kpi(d["kpis"], couleurs, largeur, hauteur))
    faire("lectures", lambda: _figure_lectures(d, couleurs, largeur, hauteur))
    return sorties


_TYPES_COLONNE = {"quanti": "rg.syn.type_quanti", "quali": "rg.syn.type_quali", "date": "rg.syn.type_date"}


def _tableau_colonnes_synthese(d, largeur, couleurs, st):
    """UNE ligne par colonne de la table (toutes, pas seulement la première) : type, manquants, ce qui la caractérise, ANOVA, série, lecture.
    Tableau ordinaire : il se coupe d'une page à l'autre avec son en-tête répété, donc rien ne se superpose."""
    cols = list(d["detail_colonnes"].values())
    if not cols:
        return [Paragraph(_esc_rg(_pt("rg.syn.colonnes_aucune")), st["normal"])]
    lignes = []
    for c in cols:
        anova = []
        if c["anova_n"]:
            anova.append(_ptf("rg.syn.anova_rep", v1=c["anova_sig"], v2=c["anova_n"]))
        if c["fact_n"]:
            anova.append(_ptf("rg.syn.anova_fact", v1=c["fact_sig"], v2=c["fact_n"]))
        serie = _pt("rg.syn.serie_" + c["serie"]) if c["serie"] in ("bonne", "moyenne", "faible") else (c["serie"] or "—")
        lecture_txt = _lecture_txt(_LECTURE_SURVEILLER) if c["alerte"] else (_pt("rg.syn.l_sans_alerte") if c["resume"] else _lecture_txt(_LECTURE_RIEN))
        lignes.append([c["nom"], _pt(_TYPES_COLONNE.get(c["type"], "rg.syn.type_autre")), "—" if c["taux_manquants"] is None else _pct_page(c["taux_manquants"]),
                       c["resume"] or "—", " ; ".join(anova) or "—", serie, lecture_txt])
    return [_tableau([_pt("rg.syn.col_colonne"), _pt("rg.syn.col_type"), _pt("rg.syn.col_manquants"), _pt("rg.syn.col_resume"), _pt("rg.syn.col_anova"),
                      _pt("rg.syn.col_serie"), _pt("rg.syn.col_lecture")], lignes, largeur, couleurs, st,
                     largeurs=[largeur * 0.14, largeur * 0.11, largeur * 0.10, largeur * 0.26, largeur * 0.16, largeur * 0.12, largeur * 0.11])]


def _titre_suivi(texte, st, style="sous"):
    """Titre qui n'est jamais seul en bas de page : on saute de page s'il reste moins de 4 cm."""
    from reportlab.platypus import CondPageBreak
    return [CondPageBreak(4 * cm), Paragraph(_esc_rg(texte), st[style])]


def _elements_synthese_table(d, largeur, hauteur, couleurs, st, entete, integre):
    """Synthèse d'UNE table, sur autant de pages qu'il en faut. `entete` = titre déjà prêt (section du rapport, ou bandeau + titre du PDF seul)."""
    sy = _styles_synthese(st, couleurs)
    out = list(entete) + [Paragraph(_esc_rg(_pt("rg.syn.chapeau")), st["muted"]),
                          Paragraph(_esc_rg(_ptf("rg.syn.chiffres", v1="—" if d["lignes"] is None else d["lignes"], v2="—" if d["colonnes"] is None else d["colonnes"],
                                                 v3=len(d["analyses"]))), st["normal"])]
    if d["kpis"]:
        out += _titre_suivi(_pt("rg.syn.h_kpi"), st) + _tableau_kpi_synthese(d["kpis"], largeur, couleurs, st)
    retenir = _choisir_messages(d["messages"], MAX_MESSAGES_SYNTHESE)
    out += _titre_suivi(_pt("rg.syn.h_retenir"), st) + (_liste_themes(retenir, largeur, sy) or [Paragraph(_esc_rg(_pt("rg.syn.aucun_message")), st["normal"])])
    out += _titre_suivi(_pt("rg.syn.h_attention"), st) + (_liste_themes(d["attention"][:MAX_ATTENTION_SYNTHESE], largeur, sy)
                                                          or [Paragraph(_esc_rg(_pt("rg.syn.rien_a_signaler")), st["normal"])])
    if len(d["attention"]) > MAX_ATTENTION_SYNTHESE:
        out.append(Paragraph(_esc_rg(_ptf("rg.syn.autres", v1=len(d["attention"]) - MAX_ATTENTION_SYNTHESE)), st["muted"]))
    out += _titre_suivi(_pt("rg.syn.h_colonnes"), st) + [Paragraph(_esc_rg(_pt("rg.syn.chapeau_colonnes")), st["muted"])] + _tableau_colonnes_synthese(d, largeur, couleurs, st)
    graphiques = _graphiques_synthese(d, largeur, hauteur, couleurs, d["table"])
    if graphiques:
        out += _titre_suivi(_pt("rg.syn.h_graphiques"), st)
        for g in graphiques:
            out += [Spacer(1, 0.2 * cm), g]
    out += _titre_suivi(_pt("rg.syn.h_analyses"), st) + [
        _tableau([_pt("rg.syn.col_analyse"), _pt("rg.syn.col_resume"), _pt("rg.syn.col_lecture")],
                 [[a, r, _lecture_txt(lec)] for a, r, lec in d["analyses"]], largeur, couleurs, st, largeurs=[largeur * 0.18, largeur * 0.64, largeur * 0.18]),
        Spacer(1, 0.2 * cm), Paragraph(_esc_rg(_pt("rg.syn.pied_integre" if integre else "rg.syn.pied_seul")), st["muted"])]
    return out


MAX_TABLES_GRAPHIQUES_SYNTHESE = 6   # synthèse de plusieurs tables : les graphiques détaillés de chaque table ne sont posés que jusqu'à ce nombre de tables


def _elements_synthese_multi(datas, largeur, hauteur, couleurs, st, entete, ignorees=0):
    """Synthèse de PLUSIEURS tables (portée « toute mon activité » / « toute l'entreprise »), sur autant de pages qu'il en faut :
    chiffres d'ensemble, ce qu'il faut retenir, points d'attention, graphiques d'ensemble, une ligne par table, puis les colonnes de CHAQUE table."""
    sy = _styles_synthese(st, couleurs)
    lignes_total = sum(d["lignes"] or 0 for d in datas)
    avec_attention = sum(1 for d in datas if d["attention"])
    kpis = [k for d in datas for k in d["kpis"]]
    etats = [k.get("etat") for k in kpis]
    out = list(entete) + [Paragraph(_esc_rg(_pt("rg.syn.chapeau")), st["muted"]),
                          Paragraph(_esc_rg(_ptf("rg.syn.chiffres_multi", v1=len(datas), v2=lignes_total, v3=avec_attention)), st["normal"])]
    if etats:
        out.append(Paragraph(_esc_rg(_ptf("rg.syn.kpi", v1=len(etats), v2=etats.count("succes"), v3=etats.count("alerte"), v4=etats.count("danger"))), st["normal"]))
    retenir = [(d["table"], f"{section} — {texte}") for d in datas for section, texte in d["messages"][:2]][:MAX_MESSAGES_SYNTHESE * 3]
    out += _titre_suivi(_pt("rg.syn.h_retenir"), st) + (_liste_themes(retenir, largeur, sy) or [Paragraph(_esc_rg(_pt("rg.syn.aucun_message")), st["normal"])])
    attention = [(d["table"], f"{section} — {texte}") for d in datas for section, texte in d["attention"]]
    out += _titre_suivi(_pt("rg.syn.h_attention"), st) + (_liste_themes(attention[:MAX_ATTENTION_SYNTHESE * 3], largeur, sy)
                                                          or [Paragraph(_esc_rg(_pt("rg.syn.rien_a_signaler")), st["normal"])])
    if len(attention) > MAX_ATTENTION_SYNTHESE * 3:
        out.append(Paragraph(_esc_rg(_ptf("rg.syn.autres", v1=len(attention) - MAX_ATTENTION_SYNTHESE * 3)), st["muted"]))
    graphiques = []
    try:
        graphiques += _figures_barres(_pt("rg.syn.g_tables_titre"), [d["table"] for d in datas], [len(d["attention"]) for d in datas], _pt("rg.syn.g_tables_x"),
                                      couleurs, largeur, hauteur, entier=True)
        g_kpi = _figure_kpi(kpis, couleurs, largeur, hauteur)
        graphiques += [g_kpi] if g_kpi is not None else []
    except Exception:
        _log.exception(_gt("rg.syn.log_graphique"), "tables", "*")
    if graphiques:
        out += _titre_suivi(_pt("rg.syn.h_graphiques"), st)
        for g in graphiques:
            out += [Spacer(1, 0.2 * cm), g]
    classees = sorted(datas, key=lambda d: -len(d["attention"]))   # les tables qui demandent de l'attention d'abord — TOUTES, aucune n'est omise
    lignes = []
    for d in classees:
        etat = [k.get("etat") for k in d["kpis"]]
        lignes.append([d["table"], "—" if d["lignes"] is None else d["lignes"], "—" if d["colonnes"] is None else d["colonnes"],
                       f"{etat.count('succes')} / {etat.count('alerte')} / {etat.count('danger')}" if etat else "—",
                       " ; ".join(texte for _s, texte in d["messages"][:2]) or "—",
                       (f"{len(d['attention'])} — " + d["attention"][0][1]) if d["attention"] else "—"])
    out += _titre_suivi(_pt("rg.syn.h_tables"), st) + [
        _tableau([_pt("rg.syn.col_table"), _pt("rg.syn.col_lignes"), _pt("rg.syn.col_nb_colonnes"), _pt("rg.syn.col_kpi"), _pt("rg.syn.col_resume"), _pt("rg.syn.col_surveiller")], lignes,
                 largeur, couleurs, st, largeurs=[largeur * 0.14, largeur * 0.07, largeur * 0.08, largeur * 0.11, largeur * 0.36, largeur * 0.24])]
    if ignorees > 0:
        out.append(Paragraph(_esc_rg(_ptf("rg.syn.autres", v1=ignorees)), st["muted"]))
    for d in classees:                                      # les colonnes de CHAQUE table : la synthèse ne s'arrête pas aux premières
        out += _titre_suivi(_ptf("rg.syn.h_colonnes_table", v1=d["table"]), st) + _tableau_colonnes_synthese(d, largeur, couleurs, st)
        if len(datas) <= MAX_TABLES_GRAPHIQUES_SYNTHESE:
            for g in _graphiques_synthese(d, largeur, hauteur, couleurs, d["table"]):
                out += [Spacer(1, 0.2 * cm), g]
    out += [Spacer(1, 0.2 * cm), Paragraph(_esc_rg(_pt("rg.syn.pied_seul")), st["muted"])]
    return out


def _section_synthese(table, doc, st, couleurs, cache, numero):
    """Dernière section d'une table dans le rapport général : saut de page avant, puis autant de pages que la synthèse en demande."""
    largeur, hauteur = doc.width - 14, doc.height - 16   # la zone utile d'une page, moins le remplissage du cadre de reportlab
    entete = [Paragraph(_esc_rg(f"{numero} {_pt('rg.syn.section')}"), st["section"]), Paragraph(_esc_rg(_nom_vue(table)), st["muted"])]
    try:
        return [PageBreak()] + _elements_synthese_table(_donnees_synthese(table, cache), largeur, hauteur, couleurs, st, entete, True)
    except Exception:
        _log.exception(_gt("rg.syn.log"), "synthese", table.id)
        return entete + [Paragraph(_pt("rg.syn.indisponible"), st["muted"])]


def _pdf_synthese_seule(entreprise, tables, intitule, portee_libelle=""):
    """Rapport général dont SEULE la synthèse est cochée : un PDF sans page de garde ni sommaire, de la longueur qu'il faut (la synthèse reprend seule)."""
    _police_activer(entreprise)
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    couleurs = _couleurs_rapport(entreprise)
    st = _styles_rg(couleurs)
    largeur, hauteur = doc.width - 14, doc.height - 16
    tables = _tables_generales(list(tables))
    ignorees = max(0, len(tables) - MAX_TABLES_RAPPORT)
    tables = tables[:MAX_TABLES_RAPPORT]
    une = len(tables) == 1
    entete = []
    _bandeau_entreprise(entreprise, largeur, entete, table=tables[0] if une else None, contexte="table" if une else "ia")
    entete.append(Paragraph(_esc_rg(_ptf("rg.syn.titre_doc", v1=_nom_vue(tables[0]) if une else getattr(entreprise, "nom", ""))), st["table"]))
    if not une and portee_libelle:
        entete.append(Paragraph(_esc_rg(portee_libelle), st["muted"]))
    datas = []
    for tb in tables:
        try:
            datas.append(_donnees_synthese(tb))
        except Exception:
            _log.exception(_gt("rg.syn.log"), "synthese", tb.id)
    if not datas:
        elements = entete + [Paragraph(_esc_rg(_pt("rg.syn.aucune_table")), st["normal"])]
    elif une:
        elements = _elements_synthese_table(datas[0], largeur, hauteur, couleurs, st, entete, False)
    else:
        elements = _elements_synthese_multi(datas, largeur, hauteur, couleurs, st, entete, ignorees)
    cb = _decor_page(entreprise)
    doc.build(elements, onFirstPage=cb, onLaterPages=cb)
    tampon.seek(0)
    return tampon.read()


# ─────────────────────────────────────────────────────────────
# Une table complète
# ─────────────────────────────────────────────────────────────
def _elements_table_complete(table, doc, styles_rg, couleurs, premiere=False, sections=None):
    """Toutes les analyses d'UNE table (voir l'ordre en tête de fichier). Chaque section est isolée.
    `sections` : identifiants (SECTIONS_RAPPORT_GENERAL_IDS) à garder ; None ou vide = toutes (comportement historique)."""
    st = styles_rg
    actives = set(sections) if sections else None

    def veut(identifiant):
        return actives is None or identifiant in actives

    largeur = doc.width
    elements = []
    if not premiere:
        elements.append(PageBreak())
    _bandeau_entreprise(table.entreprise, largeur, elements, table=table, contexte="table")
    elements.append(Paragraph(_esc_rg(_ptf("engine/reports/_part_007.py:pdf:561:38", v1=_nom_vue(table))), st["table"]))
    n = [0]

    def num():
        n[0] += 1
        return f"{n[0]}."

    if veut("structure"):
        _section(_pt("engine/reports/_part_007.py:pdf:568:13"), lambda: _bloc_structure(table, largeur, couleurs, st), st, elements, num())
    if veut("kpi"):
        _section(_pt("engine/reports/_part_007.py:pdf:569:13"), lambda: _elements_kpis_table(table, largeur), st, elements, num())

    analyses_skills = {}
    cache = analyses_skills   # résultats des sections, relus par la synthèse (dernière section) au lieu d'être recalculés

    def _dashboards():
        out, skills = _blocs_analyses(table, largeur, doc.height)
        analyses_skills["skills"] = skills
        return out
    if veut("dashboards"):
        _section(_pt("engine/reports/_part_007.py:pdf:577:13"), _dashboards, st, elements, num())

    def _skills():
        out = []
        if "skills" not in analyses_skills:   # « Dashboards » non coché : les skills se lisent seuls, sans construire les analyses
            analyses_skills["skills"] = _skills_executes(table)
        for i, (s, _x) in enumerate(analyses_skills.get("skills") or []):
            if i:
                out.append(PageBreak())
            out += _elements_rapport_skill(s, largeur)
        return out
    if veut("skills"):
        _section(_pt("engine/reports/_part_007.py:pdf:586:13"), _skills, st, elements, num())

    def _bi():
        from ..vue_groupe import ids_groupe
        from ..models import TableauBordBI
        out = []
        for tb in TableauBordBI.objects.filter(table_source_id__in=ids_groupe(table)).order_by("ordre", "id"):
            out.append(PageBreak())
            out += _elements_dashboard_bi(tb, largeur, avec_kpis=True, hauteur_page=doc.height - 6 * cm)
        return out
    if veut("bi"):
        _section(_pt("engine/reports/_part_007.py:pdf:596:13"), _bi, st, elements, num())

    if veut("statistiques"):
        _section(_pt("engine/reports/_part_007.py:pdf:598:13"), lambda: _bloc_statistiques(table, largeur, couleurs, st, cache), st, elements, num())
    if veut("groupes"):
        _section(_pt("sg.section"), lambda: _bloc_groupes(table, largeur, couleurs, st, cache), st, elements, num())
    if veut("anova"):
        _section(_pt("engine/reports/_part_007.py:pdf:599:13"), lambda: _bloc_anova(table, largeur, couleurs, st, cache), st, elements, num())
    if veut("factoriel"):
        _section(_pt("engine/reports/_part_007.py:pdf:600:13"), lambda: _bloc_factoriel(table, largeur, couleurs, st, cache), st, elements, num())
    if veut("series"):
        _section(_pt("engine/reports/_part_007.py:pdf:601:13"), lambda: _bloc_series(table, largeur, couleurs, st, cache), st, elements, num())
    if veut("geospatial"):
        _section(_pt("engine/reports/_part_007.py:pdf:603:13"), lambda: _elements_analyses_donnees(table, largeur, couleurs, inclure=("geospatial",), avec_bandeau=False, saut_page=False, avec_titre=False),
                 st, elements, num())
    if veut("synthese"):   # TOUJOURS la dernière section : elle résume toutes les autres (toutes les colonnes), sur autant de pages qu'il faut
        elements += _section_synthese(table, doc, st, couleurs, cache, num())
    return elements


SECTIONS_RAPPORT_GENERAL = [_ptl("engine/reports/_part_007.py:pdf:608:28"), _ptl("engine/reports/_part_007.py:pdf:608:41"), _ptl("engine/reports/_part_007.py:pdf:608:62"), _ptl("engine/reports/_part_007.py:pdf:608:101"), _ptl("engine/reports/_part_007.py:pdf:608:122"),
                            _ptl("engine/reports/_part_007.py:pdf:609:28"), _ptl("sg.section"), _ptl("engine/reports/_part_007.py:pdf:609:44"), _ptl("engine/reports/_part_007.py:pdf:609:53"), _ptl("engine/reports/_part_007.py:pdf:609:94"), _ptl("engine/reports/_part_007.py:pdf:609:117"), _ptl("rg.syn.section")]


# Identifiants des sections du rapport, DANS L'ORDRE de SECTIONS_RAPPORT_GENERAL (libellés) et de _elements_table_complete.
SECTIONS_RAPPORT_GENERAL_IDS = ("structure", "kpi", "dashboards", "skills", "bi", "statistiques", "groupes", "anova", "factoriel", "series", "geospatial", "synthese")


def sections_rapport_general():
    """[(identifiant, libellé traduit)] des sections que l'utilisateur peut cocher dans la fenêtre « Rapport général »."""
    return [(i, str(lib)) for i, lib in zip(SECTIONS_RAPPORT_GENERAL_IDS, SECTIONS_RAPPORT_GENERAL)]


def sections_valides(demandees):
    """Identifiants demandés, filtrés (inconnus ignorés) et remis dans l'ordre du rapport. Rien de valable ou rien de coché = None = TOUT."""
    if not demandees:
        return None
    voulus = {str(d).strip() for d in demandees}
    gardees = [i for i in SECTIONS_RAPPORT_GENERAL_IDS if i in voulus]
    return gardees or None


def _chemin_logo_defaut():
    """Chemin disque du logo d'origine de l'application (celui que l'interface montre tant que l'entreprise n'a pas posé le sien)."""
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "engine", "img", "logo_agent_gy.png")


def _chemin_logo_garde(entreprise):
    """Chemin disque du logo pour la page de garde : celui de l'entreprise s'il existe, SINON le logo d'origine de l'application
    (comme l'interface). None seulement si l'entreprise a choisi de masquer le logo de ses documents, ou si aucun fichier n'existe."""
    import os
    cfg = getattr(entreprise, "configuration_ia", None)
    if cfg is not None and not getattr(cfg, "afficher_logo_document", True):
        return None
    try:
        if getattr(entreprise, "logo", None):
            chemin = entreprise.logo.path
            if os.path.isfile(chemin):
                return chemin
    except Exception:
        pass
    defaut = _chemin_logo_defaut()
    return defaut if os.path.isfile(defaut) else None


def _coordonnees_garde(entreprise):
    """Adresse et coordonnées de l'entreprise sur une ligne (même source que le bandeau des documents)."""
    adresse = (getattr(entreprise, "adresse", "") or "").replace("\n", ", ").strip()
    try:
        contact = entreprise.coordonnees_document() or ""
    except Exception:
        contact = ""
    return "  ·  ".join(x for x in (adresse, contact) if x)


def _theme_garde(entreprise, couleurs):
    """Thème de la page de garde : celui de l'INTERFACE (personnalisation de la marque de l'entreprise : couleur d'accent et fond),
    et, tant que rien n'a été personnalisé, le thème par défaut de l'interface (accent bleu clair sur fond noir) — jamais la couleur
    d'accent des documents (jaune), qui ne concerne que les autres PDF. Renvoie (couleurs, nom) : le nom est celui que l'entreprise a
    choisi (réglage d'affichage des documents, sinon nom de la marque), à défaut le nom de l'application, comme dans l'interface."""
    from .. import marque as _marque
    cfg = getattr(entreprise, "configuration_ia", None)
    sortie = dict(couleurs)
    try:
        marque = _marque._couleurs(entreprise) or {}
    except Exception:
        marque = {}
    defauts = _marque._DEFAUTS
    accent = _hex_ou_repli((marque.get("accent") or defauts["accent"])[:7], couleurs["accent"])
    fond = _hex_ou_repli((marque.get("bg") or defauts["bg"])[:7], colors.black)
    sortie["accent"] = accent
    sortie["entete"] = fond                      # liseré et filet du pied : le fond de l'interface (noir par défaut)
    nom = ((getattr(cfg, "nom_affichage_document", "") or "").strip()
           or _marque.nom_marque(entreprise) or _marque.t(_marque.CLE_NOM))
    return sortie, nom


# Table des matières NAVIGABLE du rapport général : chaque titre (table), section et sous-titre est repéré à la construction du PDF, reçoit un
# signet (volet « Signets » du lecteur PDF) et une ligne cliquable de la table des matières (numéro de page exact, calculé par multiBuild).
# Niveau de la table des matières selon le style du paragraphe : 0 = titre de table, 1 = section, 2 = sous-titre (les titres de cartes d'analyse
# en Heading2 / Heading3 sont aussi des sous-titres).
_NIVEAUX_TITRES_RG = {"RGTable": 0, "Heading1": 0, "RGSection": 1, "RGSous": 2, "Heading2": 2, "Heading3": 2}


class _DocRapportGeneral(SimpleDocTemplate):
    """SimpleDocTemplate qui alimente la table des matières et les signets avec les titres rencontrés."""

    def beforeDocument(self):
        self._rg_seq = 0          # clés de signet déterministes : identiques à chaque passe de multiBuild (les liens de la table restent valides)
        self._rg_dernier = -1     # un signet ne peut pas sauter de niveau : on borne au niveau précédent + 1

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        niveau = _NIVEAUX_TITRES_RG.get(getattr(flowable.style, "name", ""))
        if niveau is None:
            return
        texte = " ".join(flowable.getPlainText().split())
        if not texte:
            return
        cle = f"rgt{self._rg_seq}"
        self._rg_seq += 1
        niveau_signet = min(niveau, self._rg_dernier + 1)
        self._rg_dernier = niveau_signet
        self.canv.bookmarkPage(cle)
        self.canv.addOutlineEntry(texte, cle, level=niveau_signet, closed=niveau_signet >= 1)
        self.notify("TOCEntry", (niveau, texte, self.page, cle))


def _table_des_matieres_rg(st, largeur):
    """(titre, aide, table) : la table des matières à trois niveaux (tables, sections, sous-titres), lignes cliquables avec points de conduite."""
    from reportlab.platypus.tableofcontents import TableOfContents
    base = st["normal"]
    toc = TableOfContents()
    toc.dotsMinLevel = 0
    toc.levelStyles = [
        ParagraphStyle("RGToc0", parent=base, fontSize=10.5, leading=14, fontName=_pol("gras"), spaceBefore=6, leftIndent=0, firstLineIndent=0),
        ParagraphStyle("RGToc1", parent=base, fontSize=9.5, leading=12.5, leftIndent=14, firstLineIndent=0),
        ParagraphStyle("RGToc2", parent=base, fontSize=8.5, leading=11, leftIndent=28, firstLineIndent=0, textColor=colors.HexColor("#4b5563")),
    ]
    titre = Paragraph(_esc_rg(_pt("engine/reports/_part_007.py:pdf:toc_titre")), ParagraphStyle("RGTocTitre", parent=st["section"]))   # nom différent : jamais repris dans la table elle-même
    aide = Paragraph(_esc_rg(_pt("engine/reports/_part_007.py:pdf:toc_aide")), st["muted"])
    return titre, aide, toc


def generer_pdf_rapport_general_tables(entreprise, tables, intitule, portee_libelle="", sections=None) -> bytes:
    """PDF unique « Rapport général » de `tables` (une, plusieurs, ou toute l'entreprise) : page de garde + sommaire, puis, table après table,
    toutes les analyses (voir _elements_table_complete). `intitule` = titre de la page de garde, `portee_libelle` = portée choisie.
    `sections` : identifiants des sections cochées (voir sections_rapport_general) ; None ou vide = toutes, comme avant."""
    from datetime import datetime as _dt
    sections = sections_valides(sections)
    if sections == ["synthese"]:   # synthèse cochée seule : on la « reprend seule » = PDF autonome, sans page de garde ni sommaire
        return _pdf_synthese_seule(entreprise, tables, intitule, portee_libelle)
    _police_activer(entreprise)
    tampon = io.BytesIO()
    doc = _DocRapportGeneral(tampon, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    couleurs = _couleurs_rapport(entreprise)
    st = _styles_rg(couleurs)
    tables = _tables_generales(list(tables))   # une grande table découpée en « parties » = UNE table (nom du groupe, toutes les lignes) ; jamais une partie seule
    tronque = len(tables) > MAX_TABLES_RAPPORT
    tables = tables[:MAX_TABLES_RAPPORT]

    # Page 1 : VRAIE page de garde (logo, titre, thème de l'entreprise), dessinée sur le canevas — voir _page_garde.py.
    # Page 2 : sommaire (bandeau de l'entreprise, tables incluses, contenu de chaque table).
    cfg = getattr(entreprise, "configuration_ia", None)
    _couleurs_garde, _nom_garde = _theme_garde(entreprise, couleurs)   # thème, logo et nom de l'entreprise sur la couverture
    garde = _peintre_page_garde(
        couleurs=_couleurs_garde, polices=_polices_entete(),
        nom_entreprise=_nom_garde,
        coordonnees=_coordonnees_garde(entreprise),
        chemin_logo=_chemin_logo_garde(entreprise),
        libelle_type=_pt("engine/reports/_part_007.py:pdf:garde_type"),
        intitule=str(intitule),
        reperes=[(_pt("engine/reports/_part_007.py:pdf:garde_date"), _dt.now().strftime('%d/%m/%Y %H:%M')),
                 (_pt("engine/reports/_part_007.py:pdf:garde_portee"), portee_libelle or "—"),
                 (_pt("engine/reports/_part_007.py:pdf:garde_tables"), str(len(tables)))],
        note=_ptf("engine/reports/_part_007.py:pdf:635:42", v1=MAX_TABLES_RAPPORT) if tronque else "",
        pied=(getattr(cfg, "texte_pied_page_document", "") or "").strip(),
        couleur_fond=_hex_ou_repli(getattr(cfg, "couleur_fond_document", None), colors.white),
    )
    elements = [Spacer(1, 1), PageBreak()]          # la page 1 est entièrement peinte par `garde`
    _bandeau_entreprise(entreprise, doc.width, elements, contexte="ia")
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph(_pt("engine/reports/_part_007.py:pdf:638:30"), st["section"]))
    for i, tb in enumerate(tables, 1):
        elements.append(Paragraph(_esc_rg(f"{i}. {_nom_vue(tb)}"), st["normal"]))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph(_esc_rg(_pt("engine/reports/_part_007.py:pdf:642:38") + " · ".join(lib for i, lib in sections_rapport_general() if sections is None or i in sections) + "."), st["muted"]))
    if tronque:
        elements.append(Paragraph(_esc_rg(_ptf("engine/reports/_part_007.py:pdf:635:42", v1=MAX_TABLES_RAPPORT)), st["muted"]))
    if not tables:
        elements.append(Paragraph(_pt("engine/reports/_part_007.py:pdf:644:34"), st["normal"]))
    else:   # table des matières navigable : titres, sections ET sous-titres, avec liens et signets
        _toc_titre, _toc_aide, _toc = _table_des_matieres_rg(st, doc.width)
        elements += [Spacer(1, 0.4 * cm), _toc_titre, _toc_aide, Spacer(1, 0.2 * cm), _toc]

    for i, tb in enumerate(tables):
        try:
            elements += _elements_table_complete(tb, doc, st, couleurs, premiere=False, sections=sections)
        except Exception:
            _log.exception(_gt("rg5.msg.085"), tb.id)
            elements += [PageBreak(), Paragraph(_esc_rg(_ptf("engine/reports/_part_007.py:pdf:651:56", v1=_nom_vue(tb))), st["table"]),
                         Paragraph(_pt("engine/reports/_part_007.py:pdf:652:35"), st["muted"])]

    doc.multiBuild(elements, onFirstPage=garde, onLaterPages=_decor_page(entreprise))   # = _construire() avec la page de garde ; plusieurs passes : les numéros de page de la table des matières sont exacts
    tampon.seek(0)
    return tampon.read()


def generer_pdf_rapport_general(table):
    """Bouton « Rapport général » d'UNE table : même rapport complet, limité à cette table. Renvoie les octets du PDF."""
    table = _table_generale(table)
    return generer_pdf_rapport_general_tables(table.entreprise, [table], _ptf("engine/reports/_part_007.py:pdf:661:73", v1=_nom_vue(table)), _pt("engine/reports/_part_007.py:pdf:661:111"))


__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
