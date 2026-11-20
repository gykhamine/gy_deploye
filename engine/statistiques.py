"""STATISTIQUES — descriptives, relations entre variables, inférence et échantillonnage (module pur : numpy + stdlib, sans scipy).

Les lois (p-valeurs, quantiles) viennent de `engine/statistiques_lois.py`, source unique. Rien n'est lu en base ni écrit ici : on reçoit des
listes de valeurs (la « table de calcul » construite en mémoire par `engine/statistiques_donnees.py`) et on renvoie des dictionnaires.

Types de variables (GENRES) : quantitative continue, quantitative discrète, qualitative nominale, qualitative ordinale.
Tests : Student (1 et 2 échantillons, apparié), z, proportions, variances (khi-deux, Fisher, Brown-Forsythe), ANOVA + comparaisons deux à deux,
ANOVA à deux facteurs ou plus (type II), Mann-Whitney, Wilcoxon, Kruskal-Wallis, khi-deux (indépendance, adéquation), Fisher exact, corrélation (Pearson, Spearman), régression linéaire
multiple, polynomiale, non linéaire (Levenberg-Marquardt) et logistique, normalité (D'Agostino-Pearson), bootstrap, tailles d'échantillon ; `auto` choisit le test adapté aux variables et le justifie.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td, dec  # noqa: F401
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t
from engine.i18n import td
from engine.i18n import t as _i18n_t, tf, tl

import ast
import functools
import itertools
import math
import random
import re
import time
from collections import Counter
from statistics import NormalDist

import numpy as np

from . import statistiques_lois as L
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402
from engine.i18n import td as _gtd  # noqa: E402

GENRES = {"quanti_continue": td("rg.genre.quanti_continue"), "quanti_discrete": td("engine/statistiques.py:g:28:74"),
          "quali_nominale": td("rg.genre.quali_nominale"), "quali_ordinale": td("rg.genre.quali_ordinale"), "date": td("engine/statistiques.py:g:29:103")}
TYPES_QUANTI = {"nombre", "decimal", "monnaie", "pourcentage", "barre_progression", "constante", "formule"}
TYPES_ORDINAL = {"note"}
TYPES_DATE = {"date", "datetime", "horodatage"}
TYPES_QUALI = {"texte", "choix_unique", "booleen", "switch", "couleur"}
LIMITES = {"modalites": 40, "modalites_croisees": 30, "points_graphique": 400, "colonnes_relations": 15, "groupes": 30, "distincts_discrets": 30, "exact_rangs": 50}
_ND = NormalDist()
_ALT = {"different": "≠", "superieur": ">", "inferieur": "<"}


# ── Utilitaires ──────────────────────────────────────────────────────────────
def _reel(v):
    """float fini ou None (les booléens valent 0/1 ; texte, vide, NaN et infini donnent None)."""
    if v is None or isinstance(v, str):
        if isinstance(v, str):
            try:
                v = float(v.replace(",", ".").strip())
            except ValueError:
                return None
        else:
            return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _nombres(valeurs) -> np.ndarray:
    if isinstance(valeurs, np.ndarray):      # colonne déjà convertie (NaN = écarté), par exemple rassemblée par tranches (spark_descriptives)
        return valeurs[np.isfinite(valeurs)]
    return np.array([r for r in (_reel(v) for v in valeurs) if r is not None], dtype=float)


def etiquette(v) -> str | None:
    """Libellé d'une modalité (Oui/Non pour un booléen, entier sans « .0 »), None si la valeur est vide."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        return _i18n_t("engine/statistiques.py:g:65:16") if v else _i18n_t("engine/statistiques.py:g:65:32")
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return str(v).strip()


def _quantile(s, q):
    """Quantile par interpolation linéaire (méthode usuelle des tableurs) sur un tableau trié."""
    return float(np.quantile(s, q))


def _rangs(x):
    """Rangs moyens (les ex æquo partagent la moyenne de leurs rangs)."""
    x = np.asarray(x, dtype=float)
    ordre = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[ordre] = np.arange(1, len(x) + 1)
    _v, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    somme = np.bincount(inv, weights=r)
    return (somme / cnt)[inv]


def _corr_ex(x, _t=None):
    """Somme des (t³ − t) sur les groupes d'ex æquo de x (correction des ex æquo des tests de rangs)."""
    return float(sum(c ** 3 - c for c in np.unique(x, return_counts=True)[1]))


def _p_t(t, df, alt):
    if alt == "superieur":
        return L._t_sf(t, df)
    if alt == "inferieur":
        return L._t_sf(-t, df)
    return min(1.0, 2 * L._t_sf(abs(t), df))


def _p_z(z, alt):
    if alt == "superieur":
        return _ND.cdf(-z)
    if alt == "inferieur":
        return _ND.cdf(z)
    return min(1.0, 2 * _ND.cdf(-abs(z)))


def _p_fmt(p):
    return dec("< 0.0001") if p < 1e-4 else dec(f"{p:.4f}")


def _t_crit(df, conf):
    return L.quantile("student", {"nu": float(df)}, 1 - (1 - conf) / 2)


def _force(r):
    a = abs(r)
    return td("rg.force.nulle") if a < 0.1 else td("rg.force.faible") if a < 0.3 else td("rg.force.moderee") if a < 0.5 else td("rg.force.forte") if a < 0.7 else td("engine/statistiques.py:g:119:111")


def nettoyer(o):
    """Structure JSON sûre : types numpy convertis, NaN/infini remplacés par None."""
    if isinstance(o, dict):
        return {str(k): nettoyer(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [nettoyer(v) for v in o]
    if isinstance(o, np.ndarray):
        return nettoyer(o.tolist())
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


# ── Classification des variables ─────────────────────────────────────────────
def classer(valeurs, type_champ: str, forcer: str | None = None) -> str:
    """Genre d'une colonne (clé de GENRES, ou « exclue » : vide, identifiant, texte libre, type non statistique).
    Entiers avec 30 valeurs distinctes au plus → discrète ; sinon continue. Une note (1-5) est ordinale. Texte : nominal s'il a peu de modalités."""
    plein = [v for v in valeurs if v is not None and not (isinstance(v, str) and not v.strip())]
    if not plein:
        return "exclue"
    nums = [r for r in (_reel(v) for v in plein) if r is not None]
    if forcer in GENRES:
        if forcer.startswith("quanti") and len(nums) < 0.9 * len(plein):
            forcer = None
        elif forcer == "quali_ordinale" or forcer == "quali_nominale":
            return forcer
    if forcer in ("quanti_continue", "quanti_discrete"):
        return forcer
    if type_champ in TYPES_DATE:      # colonne de dates : répartition par année, mois, jour (decrire_date) ; pas de tests ni de liaisons
        from .series_temporelles import _secondes
        return "date" if sum(1 for v in plein if _secondes(v) is not None) >= 0.9 * len(plein) else "exclue"
    if type_champ in TYPES_ORDINAL:
        return "quali_ordinale"
    if type_champ in TYPES_QUANTI:
        if len(nums) >= 0.9 * len(plein):
            entiers = all(v == int(v) for v in nums)
            return "quanti_discrete" if entiers and len(set(nums)) <= LIMITES["distincts_discrets"] else "quanti_continue"
        return "exclue"
    if type_champ in TYPES_QUALI:
        etiquettes = {etiquette(v) for v in plein}
        if type_champ == "texte" and (len(etiquettes) > 50 or (len(plein) > 20 and len(etiquettes) > 0.8 * len(plein))):
            return "exclue"
        return "quali_nominale"
    return "exclue"


def est_quanti(genre):
    return str(genre).startswith("quanti")


# ── Statistiques descriptives ────────────────────────────────────────────────
def _normalite_dagostino(s):
    """(K², p) du test de D'Agostino-Pearson, None si moins de 8 valeurs ou variance nulle."""
    n = len(s)
    if n < 8:
        return None
    m = s - s.mean()
    m2 = float((m ** 2).mean())
    if m2 <= 0:
        return None
    return _dagostino(n, float((m ** 3).mean()) / m2 ** 1.5, float((m ** 4).mean()) / m2 ** 2)


def _dagostino(n, g1, b2):
    """(K², p, nombre de tests) à partir de l'effectif, de l'asymétrie g1 et du kurtosis b2 : ce que donnent aussi les moments fusionnés des tranches (spark_descriptives)."""
    y = g1 * math.sqrt((n + 1) * (n + 3) / (6.0 * (n - 2)))
    beta2 = 3.0 * (n * n + 27 * n - 70) * (n + 1) * (n + 3) / ((n - 2.0) * (n + 5) * (n + 7) * (n + 9))
    w2 = -1 + math.sqrt(2 * (beta2 - 1))
    delta = 1 / math.sqrt(0.5 * math.log(w2))
    alpha = math.sqrt(2 / (w2 - 1))
    z1 = delta * math.asinh(y / alpha)
    if n < 20:   # le test du kurtosis n'est défini qu'à partir de 20 valeurs : en dessous on ne garde que l'asymétrie
        return z1 * z1, L.sf("khi2", {"k": 1.0}, z1 * z1), 1
    e = 3.0 * (n - 1) / (n + 1)
    varb2 = 24.0 * n * (n - 2) * (n - 3) / ((n + 1.0) ** 2 * (n + 3) * (n + 5))
    x = (b2 - e) / math.sqrt(varb2)
    sb1 = 6.0 * (n * n - 5 * n + 2) / ((n + 7) * (n + 9)) * math.sqrt(6.0 * (n + 3) * (n + 5) / (n * (n - 2.0) * (n - 3)))
    a = 6.0 + 8.0 / sb1 * (2.0 / sb1 + math.sqrt(1 + 4.0 / (sb1 * sb1)))
    den = 1 + x * math.sqrt(2 / (a - 4.0))
    t2 = 0.0 if den == 0 else math.copysign(((1 - 2 / a) / abs(den)) ** (1 / 3), den)
    z2 = ((1 - 2 / (9 * a)) - t2) / math.sqrt(2 / (9 * a))
    k2 = z1 * z1 + z2 * z2
    return k2, math.exp(-k2 / 2), 2


def _indices_qq(n):
    return range(0, n, max(1, n // 120))


def _indices_ogive(n):
    return sorted({int(j) for j in np.linspace(0, n - 1, min(n, 120))})


def _classes_histogramme(n, mn, mx, iqr):
    """Nombre de classes de l'histogramme (règle de Freedman-Diaconis, sinon Sturges), entre 5 et 40."""
    if iqr > 0:
        return int(min(40, max(5, math.ceil((mx - mn) / (2 * iqr * n ** (-1 / 3))))))
    return int(min(40, max(5, math.ceil(1 + math.log2(n)))))


def _qq_points(n, moy, sd, valeur):
    """Points du diagramme quantile-quantile ; `valeur(i)` = i-ième plus petite valeur."""
    return [[_ND.inv_cdf((i + 0.5) / n), float((valeur(i) - moy) / sd)] for i in _indices_qq(n)]


def _qq(s):
    return _qq_points(len(s), s.mean(), float(s.std(ddof=1)) or 1.0, lambda i: s[i])


def _normalite_sortie(nm, qq):
    return {"statistique": nm[0], "p": nm[1], "test": td("engine/statistiques.py:g:254:74") if nm[2] == 2 else td("engine/statistiques.py:g:254:114"), "qq": qq,
            "conclusion": td("engine/statistiques.py:g:255:46") if nm[1] >= 0.05 else td("engine/statistiques.py:g:255:102")}


def _frequences_discretes(valeurs, effectifs, n):
    """(tableau des 200 premières valeurs, modes) d'une variable discrète : une passe par valeur distincte (valeurs arrondies à 10 décimales)."""
    cpt = Counter()
    for v, c in zip(valeurs, effectifs):
        cpt[round(float(v), 10)] += int(c)
    cum = 0
    tab = []
    for v in sorted(cpt):
        cum += cpt[v]
        tab.append({"valeur": v, "effectif": cpt[v], "frequence": cpt[v] / n * 100, "cumul": cum / n * 100})
    maxi = max(cpt.values())
    return tab[:200], [v for v in sorted(cpt) if cpt[v] == maxi][:10]


def _lecture_quanti(asym, cv, moy, med, sd, hors_n, bas, haut):
    lecture = []
    if asym is not None:
        lecture.append(_i18n_t("engine/statistiques.py:g:277:24") if abs(asym) < 0.5 else (_i18n_t("engine/statistiques.py:g:277:87") if asym > 0 else _i18n_t("engine/statistiques.py:g:277:169")))
    if cv is not None and moy:
        lecture.append(_i18n_t("engine/statistiques.py:g:279:24") % (_i18n_t("rg.cv.faible") if cv < 15 else _i18n_t("rg.cv.moderee") if cv < 30 else _i18n_t("rg.cv.forte"), dec(f"{cv:.1f}")))
    if hors_n:
        lecture.append(tf("engine/statistiques.py:g:281:24", v1=int(hors_n), v2=_fmt(bas), v3=_fmt(haut)))
    if abs(moy - med) > 0.1 * (sd or 0) and sd:
        lecture.append(_i18n_t("engine/statistiques.py:g:283:24"))
    return lecture


def decrire_quanti(valeurs, discrete: bool = False, confiance: float = 0.95) -> dict:
    x = _nombres(valeurs)
    n = int(x.size)
    sortie = {"genre": "quanti_discrete" if discrete else "quanti_continue", "n": n, "manquants": len(valeurs) - n}
    if n == 0:
        return {**sortie, "erreur": _i18n_t("engine/statistiques.py:g:220:37")}
    s = np.sort(x)
    moy, med = float(s.mean()), float(np.median(s))
    q1, q3 = _quantile(s, 0.25), _quantile(s, 0.75)
    iqr = q3 - q1
    var = float(s.var(ddof=1)) if n > 1 else None
    sd = math.sqrt(var) if var is not None else None
    m2 = float(((s - moy) ** 2).mean())
    g1 = float(((s - moy) ** 3).mean()) / m2 ** 1.5 if m2 > 0 else None
    g2 = float(((s - moy) ** 4).mean()) / m2 ** 2 - 3 if m2 > 0 else None
    asym = g1 * math.sqrt(n * (n - 1.0)) / (n - 2) if g1 is not None and n > 2 else None
    aplat = (n - 1.0) / ((n - 2) * (n - 3)) * ((n + 1) * g2 + 6) if g2 is not None and n > 3 else None
    se = sd / math.sqrt(n) if sd is not None else None
    ic = None
    if se:
        marge = _t_crit(n - 1, confiance) * se
        ic = [moy - marge, moy + marge]
    bas, haut = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    hors = s[(s < bas) | (s > haut)]
    dedans = s[(s >= bas) & (s <= haut)]
    sortie.update({
        "somme": float(s.sum()), "moyenne": moy, "mediane": med, "min": float(s[0]), "max": float(s[-1]), "etendue": float(s[-1] - s[0]),
        "q1": q1, "q3": q3, "iqr": iqr, "percentiles": {str(p): _quantile(s, p / 100) for p in (5, 10, 20, 30, 40, 60, 70, 80, 90, 95)},
        "denombrement": {"distincts": int(np.unique(s).size), "doublons": n - int(np.unique(s).size), "zeros": int((s == 0).sum()), "negatives": int((s < 0).sum()), "positives": int((s > 0).sum())},
        "variance": var, "ecart_type": sd, "variance_pop": m2, "ecart_type_pop": math.sqrt(m2), "cv": (sd / abs(moy) * 100) if sd is not None and moy else None,
        "erreur_standard": se, "ic_moyenne": ic, "confiance": confiance, "asymetrie": asym, "aplatissement": aplat,
        "moyenne_geometrique": float(math.exp(np.log(s).mean())) if s[0] > 0 else None, "moyenne_harmonique": float(n / (1 / s).sum()) if s[0] > 0 else None,
        "aberrantes": {"bornes": [bas, haut], "n": int(hors.size), "valeurs": [float(v) for v in hors[:20]], "extremes": int(((s < q1 - 3 * iqr) | (s > q3 + 3 * iqr)).sum())},
        "boite": {"min": float(dedans[0]) if dedans.size else float(s[0]), "q1": q1, "mediane": med, "q3": q3, "max": float(dedans[-1]) if dedans.size else float(s[-1]),
                  "aberrantes": [float(v) for v in hors[:60]]}})
    nm = _normalite_dagostino(s)
    if nm:
        sortie["normalite"] = _normalite_sortie(nm, _qq(s))
    if discrete:
        tab, modes = _frequences_discretes(*np.unique(s, return_counts=True), n)
        sortie.update({"frequences": tab, "modes": modes})
    else:
        k = _classes_histogramme(n, s[0], s[-1], iqr)
        eff, bornes = np.histogram(s, bins=k) if s[-1] > s[0] else (np.array([n]), np.array([s[0] - 0.5, s[0] + 0.5]))
        sortie["histogramme"] = {"bornes": bornes.tolist(), "effectifs": eff.tolist()}
        j = int(np.argmax(eff))
        sortie["classe_modale"] = [float(bornes[j]), float(bornes[j + 1])]
        cum = np.cumsum(eff)
        sortie["classes"] = [{"de": float(bornes[i]), "a": float(bornes[i + 1]), "effectif": int(eff[i]), "frequence": float(eff[i] / n * 100), "cumul": float(cum[i] / n * 100)} for i in range(len(eff))]
        sortie["ogive"] = [[float(s[i]), float((i + 1) / n * 100)] for i in _indices_ogive(n)]
    sortie["lecture"] = _lecture_quanti(asym, sortie["cv"], moy, med, sd, hors.size, bas, haut)
    return sortie














_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = ("janvier", _gtfr("engine/statistiques.py:444:21"), "mars", "avril", "mai", "juin", "juillet", _gtfr("engine/statistiques.py:444:76"), "septembre", "octobre", "novembre", _gtfr("engine/statistiques.py:444:121"))


def decrire_date(valeurs) -> dict:
    """Colonne de dates : étendue, répartition par année / mois / jour de la semaine / heure, évolution dans le temps (nombre de lignes par mois, ou par an
    au-delà de 120 mois), délais entre dates consécutives et plus long intervalle sans enregistrement."""
    from datetime import datetime, timezone
    from .series_temporelles import _secondes
    ts = sorted(t for t in (_secondes(v) for v in valeurs) if t is not None and -2.2e9 < t < 7.3e9)
    n = len(ts)
    sortie = {"genre": "date", "n": n, "manquants": len(valeurs) - n}
    if n == 0:
        return {**sortie, "erreur": _i18n_t("engine/statistiques.py:g:301:37")}
    d = [datetime.fromtimestamp(t, timezone.utc) for t in ts]
    heures = any(x.hour or x.minute for x in d)
    fd = lambda x: x.strftime("%d/%m/%Y %H:%M" if heures else "%d/%m/%Y")
    def rep(cpt, cles, noms):
        return [{"modalite": noms(k), "effectif": cpt.get(k, 0), "frequence": cpt.get(k, 0) / n * 100} for k in cles]
    an = Counter(x.year for x in d)
    sortie.update({"min": fd(d[0]), "max": fd(d[-1]), "etendue_jours": (ts[-1] - ts[0]) / 86400, "doublons": n - len(set(ts)),
                   "par_annee": rep(an, range(min(an), max(an) + 1), str), "par_mois": rep(Counter(x.month for x in d), range(1, 13), lambda k: _MOIS[k - 1]),
                   "par_jour": rep(Counter(x.weekday() for x in d), range(7), lambda k: _JOURS[k])})
    if heures:
        sortie["par_heure"] = rep(Counter(x.hour for x in d), range(24), lambda k: f"{k} h")
    mois = Counter((x.year, x.month) for x in d)
    if (d[-1].year - d[0].year) * 12 + d[-1].month - d[0].month < 120:
        cles = [(y, m) for y in range(d[0].year, d[-1].year + 1) for m in range(1, 13) if (y, m) >= (d[0].year, d[0].month) and (y, m) <= (d[-1].year, d[-1].month)]
        sortie["evolution"] = [{"modalite": f"{m:02d}/{y}", "effectif": mois.get((y, m), 0), "frequence": mois.get((y, m), 0) / n * 100} for y, m in cles]
    else:
        sortie["evolution"] = sortie["par_annee"]
    lecture = [tf("engine/statistiques.py:g:319:16", v1=fd(d[0]), v2=fd(d[-1]), v3=_fmt(sortie['etendue_jours'], 4))]
    if n > 1:
        ec = np.diff(ts) / 86400
        k = int(np.argmax(ec))
        sortie.update({"delai_median": float(np.median(ec)), "delai_moyen": float(ec.mean()), "delai_max": float(ec[k])})
        if ec[k] > 0 and ec[k] > 5 * max(float(np.median(ec)), 1e-9):
            lecture.append(tf("engine/statistiques.py:g:325:28", v1=_fmt(ec[k], 4), v2=fd(d[k]), v3=fd(d[k + 1])))
    j = max(sortie["par_jour"], key=lambda t: t["effectif"])
    m = max(sortie["par_mois"], key=lambda t: t["effectif"])
    lecture.append(tf("engine/statistiques.py:g:328:20", v1=j['modalite'], v2=_fmt(j['frequence'], 3), v3=m['modalite'], v4=_fmt(m['frequence'], 3)))
    sortie["lecture"] = lecture
    return sortie


def _fmt(v, chiffres=4):
    if v is None:
        return "—"
    return str(int(v)) if float(v) == int(v) and abs(v) < 1e15 else dec(f"{v:.{chiffres}g}")


def decrire_quali(valeurs, ordinal: bool = False, ordre: list | None = None) -> dict:
    """Tableau de fréquences, mode, diversité ; pour une variable ordinale : cumuls dans l'ordre des modalités, médiane et quartiles."""
    etiq = [e for e in (etiquette(v) for v in valeurs) if e is not None]
    n = len(etiq)
    sortie = {"genre": "quali_ordinale" if ordinal else "quali_nominale", "n": n, "manquants": len(valeurs) - n}
    if n == 0:
        return {**sortie, "erreur": _i18n_t("engine/statistiques.py:g:345:37")}
    cpt = Counter(etiq)
    if ordinal:
        rang = {m: i for i, m in enumerate(ordre or [])}
        mods = sorted(cpt, key=lambda m: (rang.get(m, 10 ** 6), _reel(m) if _reel(m) is not None else 0, m))
    else:
        mods = sorted(cpt, key=lambda m: (-cpt[m], m))
    cum, tab = 0, []
    for m in mods:
        cum += cpt[m]
        ligne = {"modalite": m, "effectif": cpt[m], "frequence": cpt[m] / n * 100, "cumul": cum / n * 100}      # nominale triée par effectif décroissant : le cumul est celui du diagramme de Pareto
        tab.append(ligne)
    reste = tab[LIMITES["modalites"]:]
    if reste:
        tab = tab[:LIMITES["modalites"]] + [{"modalite": tf("engine/statistiques.py:g:359:58", v1=len(reste)), "effectif": sum(r["effectif"] for r in reste), "frequence": sum(r["frequence"] for r in reste)}]
    p = [c / n for c in cpt.values()]
    h = -sum(q * math.log2(q) for q in p)
    maxi = max(cpt.values())
    sortie.update({"distincts": len(cpt), "tableau": tab, "modes": [m for m in mods if cpt[m] == maxi][:10], "entropie": h,
                   "entropie_relative": h / math.log2(len(cpt)) if len(cpt) > 1 else 0.0, "gini_simpson": 1 - sum(q * q for q in p),
                   "denombrement": {"uniques": sum(1 for c in cpt.values() if c == 1), "rares": sum(1 for c in cpt.values() if c / n < 0.05), "part_rares": sum(c for c in cpt.values() if c / n < 0.05) / n * 100}})
    lecture = [tf("engine/statistiques.py:g:366:16", v1=len(cpt), v2=sortie['modes'][0], v3=_fmt(maxi / n * 100, 3))]
    if ordinal:
        acc, med, q1, q3 = 0, None, None, None
        for m in mods:
            acc += cpt[m]
            q1 = q1 if q1 is not None else (m if acc / n >= 0.25 else None)
            med = med if med is not None else (m if acc / n >= 0.5 else None)
            q3 = q3 if q3 is not None else (m if acc / n >= 0.75 else None)
        sortie.update({"mediane": med, "q1": q1, "q3": q3})
        lecture.append(tf("engine/statistiques.py:g:375:24", v1=med, v2=q1, v3=q3))
    if len(cpt) > 1:
        lecture.append(_i18n_t("engine/statistiques.py:g:377:24") % (_i18n_t("engine/statistiques.py:g:377:65") if sortie["entropie_relative"] < 0.5 else _i18n_t("engine/statistiques.py:g:377:125") if sortie["entropie_relative"] < 0.9 else _i18n_t("engine/statistiques.py:g:377:uniforme")))
    sortie["lecture"] = lecture
    return sortie


def _cramer_v(table):
    t = np.asarray(table, dtype=float)
    n = t.sum()
    e = np.outer(t.sum(1), t.sum(0)) / n
    khi2 = float(((t - e) ** 2 / np.where(e > 0, e, 1)).sum())
    d = min(t.shape) - 1
    return khi2, (math.sqrt(khi2 / (n * d)) if d > 0 and n > 0 else 0.0), (t.shape[0] - 1) * (t.shape[1] - 1)


def _pearson(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _p_corr(r, n):
    if r is None or n < 3:
        return None
    return 0.0 if abs(r) >= 1 else _p_t(r * math.sqrt((n - 2) / (1 - r * r)), n - 2, "different")


def _g_nuage(x, y, r, nx, ny):
    """Nuage de points (250 au plus) et droite des moindres carrés d'une paire quanti × quanti."""
    xa, ya = np.array(x, dtype=float), np.array(y, dtype=float)
    pas = max(1, len(x) // 250)
    sx = float(xa.std(ddof=1)) if len(x) > 1 else 0.0
    droite = {"pente": r * float(ya.std(ddof=1)) / sx, "ordonnee": float(ya.mean()) - r * float(ya.std(ddof=1)) / sx * float(xa.mean())} if sx > 0 else None
    return {"type": "nuage", "x": xa[::pas].tolist(), "y": ya[::pas].tolist(), "nom_x": nx, "nom_y": ny, "droite": droite}


def _g_groupes(g, nq, nc):
    """Résumé en cinq nombres de chaque groupe d'une paire quali × quanti (boîtes à moustaches)."""
    sortie = []
    for m, v in g.items():
        a = np.sort(np.array(v, dtype=float))
        sortie.append({"nom": m, "n": int(a.size), "boite": {"min": float(a[0]), "q1": _quantile(a, 0.25), "mediane": _quantile(a, 0.5), "q3": _quantile(a, 0.75), "max": float(a[-1]), "aberrantes": []}})
    return {"type": "groupes", "nom_q": nq, "nom_c": nc, "groupes": sortie}


def relations(colonnes: list[dict]) -> dict:
    """Liaisons deux à deux (lignes alignées : une colonne = {nom, genre, valeurs}). Quanti×quanti : Pearson et Spearman ; quali×quali : V de Cramér ;
    quali×quanti : rapport de corrélation η². Les lignes où l'une des deux valeurs manque sont ignorées pour cette paire."""
    cols = colonnes[:LIMITES["colonnes_relations"]]
    paires, quantis = [], [c for c in cols if est_quanti(c["genre"])]
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            qa, qb = est_quanti(a["genre"]), est_quanti(b["genre"])
            try:
                if qa and qb:
                    lignes = [(_reel(u), _reel(v)) for u, v in zip(a["valeurs"], b["valeurs"])]
                    lignes = [(u, v) for u, v in lignes if u is not None and v is not None]
                    if len(lignes) < 3:
                        continue
                    x, y = zip(*lignes)
                    r = _pearson(x, y)
                    rs = _pearson(_rangs(x), _rangs(y))
                    if r is None:
                        continue
                    paires.append({"a": a["nom"], "b": b["nom"], "mesure": "Pearson r", "valeur": r, "spearman": rs, "p": _p_corr(r, len(x)), "n": len(x), "force": _force(r), "_g": _g_nuage(x, y, r, a["nom"], b["nom"])})
                elif not qa and not qb:
                    lignes = [(etiquette(u), etiquette(v)) for u, v in zip(a["valeurs"], b["valeurs"])]
                    lignes = [(u, v) for u, v in lignes if u is not None and v is not None]
                    ma, mb = sorted({u for u, _ in lignes}), sorted({v for _, v in lignes})
                    if len(lignes) < 5 or not 1 < len(ma) <= LIMITES["modalites_croisees"] or not 1 < len(mb) <= LIMITES["modalites_croisees"]:
                        continue
                    cpt = Counter(lignes)
                    khi2, v, ddl = _cramer_v([[cpt[(u, w)] for w in mb] for u in ma])
                    paires.append({"a": a["nom"], "b": b["nom"], "mesure": _i18n_t("engine/statistiques.py:g:450:76"), "valeur": v, "p": L.sf("khi2", {"k": float(ddl)}, khi2), "n": len(lignes), "force": _force(v),
                                   "_g": {"type": "croise", "lignes": ma, "colonnes": mb, "effectifs": [[cpt[(u, w)] for w in mb] for u in ma], "nom_l": a["nom"], "nom_c": b["nom"]}})
                else:
                    q, c = (a, b) if qa else (b, a)
                    lignes = [(_reel(u), etiquette(v)) for u, v in zip(q["valeurs"], c["valeurs"])]
                    lignes = [(u, v) for u, v in lignes if u is not None and v is not None]
                    g = {}
                    for u, v in lignes:
                        g.setdefault(v, []).append(u)
                    if len(lignes) < 5 or not 1 < len(g) <= LIMITES["groupes"]:
                        continue
                    tout = np.array([u for u, _ in lignes])
                    sst = float(((tout - tout.mean()) ** 2).sum())
                    ssb = sum(len(v) * (np.mean(v) - tout.mean()) ** 2 for v in g.values())
                    eta2 = float(ssb / sst) if sst > 0 else 0.0
                    k, N = len(g), len(tout)
                    f = (ssb / (k - 1)) / ((sst - ssb) / (N - k)) if N > k and sst - ssb > 0 else None
                    paires.append({"a": q["nom"], "b": c["nom"], "mesure": _i18n_t("engine/statistiques.py:g:467:76"), "valeur": eta2, "p": L.sf("fisher", {"d1": k - 1.0, "d2": N - k + 0.0}, f) if f is not None else None,
                                   "n": N, "force": _force(math.sqrt(eta2)), "_g": _g_groupes(g, q["nom"], c["nom"])})
            except (ValueError, ZeroDivisionError, OverflowError):
                continue
    paires.sort(key=lambda r: -abs(r["valeur"]))
    for i, pr in enumerate(paires):      # un graphique par lien, pour les 12 plus marqués (les autres restent dans le tableau)
        g = pr.pop("_g", None)
        if g is not None and i < 12:
            pr["graphique"] = g
    sortie = {"paires": paires}
    if len(quantis) >= 2:
        noms = [c["nom"] for c in quantis]
        mp = [[None] * len(quantis) for _ in quantis]
        ms = [[None] * len(quantis) for _ in quantis]
        for i, a in enumerate(quantis):
            for j, b in enumerate(quantis):
                if i == j:
                    mp[i][j] = ms[i][j] = 1.0
                    continue
                lignes = [(_reel(u), _reel(v)) for u, v in zip(a["valeurs"], b["valeurs"])]
                lignes = [(u, v) for u, v in lignes if u is not None and v is not None]
                if len(lignes) >= 3:
                    x, y = zip(*lignes)
                    mp[i][j], ms[i][j] = _pearson(x, y), _pearson(_rangs(x), _rangs(y))
        sortie["matrice"] = {"noms": noms, "pearson": mp, "spearman": ms}
    return sortie


def synthese(variables: list[dict], relations_tables: list[dict]) -> dict:
    """Constats en langage clair pour un décideur non statisticien, déduits UNIQUEMENT des descriptives et des liaisons déjà calculées (rien d'inventé) :
    qualité des données (manquants, colonnes constantes, identifiants), points d'attention (aberrantes, dispersion, déséquilibre) et liens notables."""
    constats, qualite = [], []
    def note(niveau, texte): constats.append({"niveau": niveau, "texte": texte})
    for v in variables:
        nom, n, manq = v.get("nom"), v.get("n") or 0, v.get("manquants") or 0
        if v.get("erreur") or not nom or not n:
            continue
        taux = manq / (n + manq) * 100
        qualite.append({"nom": nom, "n": n, "manquants": manq, "taux_manquants": taux})
        if taux >= 5:
            note("attention", tf("engine/statistiques.py:g:507:31", v1=nom, v2=_fmt(taux, 3)))
        if est_quanti(v["genre"]):
            if v["etendue"] == 0:
                note("attention", tf("engine/statistiques.py:g:510:35", v1=nom, v2=_fmt(v['min'])))
                continue
            if v["aberrantes"]["n"]:
                a = v["aberrantes"]
                note("attention", tf("engine/statistiques.py:g:514:35", v1=nom, v2=a['n'], v3=_fmt(a['bornes'][0]), v4=_fmt(a['bornes'][1])))
            if v.get("cv") is not None and v["cv"] >= 30:
                note("info", tf("engine/statistiques.py:g:516:30", v1=nom, v2=_fmt(v['cv'], 3)))
            if v.get("asymetrie") is not None and abs(v["asymetrie"]) > 1:
                note("info", tf("engine/statistiques.py:g:518:30", v1=nom, v2=_fmt(v['mediane'])) % ("grandes" if v["asymetrie"] > 0 else "petites"))
        elif v["genre"] != "date":
            t = [x for x in (v.get("tableau") or []) if not str(x["modalite"]).startswith(_i18n_t("engine/statistiques.py:g:359:58").split("{v1}")[0])]
            if v.get("distincts") == n and n > 20:
                note("attention", tf("engine/statistiques.py:g:522:35", v1=nom))
            elif v.get("distincts") == 1:
                note("attention", tf("engine/statistiques.py:g:524:35", v1=nom))
            elif t and max(x["frequence"] for x in t) >= 70:
                dom = max(t, key=lambda x: x["frequence"])
                note("info", tf("engine/statistiques.py:g:527:30", v1=nom, v2=dom['modalite'], v3=_fmt(dom['frequence'], 3)))
    liens = [p for r in relations_tables for p in r["paires"] if p.get("p") is not None and p["p"] < 0.05 and p["force"] in (td("rg.force.forte"), td("engine/statistiques.py:g:529:135"))]
    for p in sorted(liens, key=lambda q: -abs(q["valeur"]))[:8]:
        sens = (_i18n_t("engine/statistiques.py:g:530:17") if p["valeur"] > 0 else _i18n_t("engine/statistiques.py:g:530:71")) if p["mesure"] == "Pearson r" else ""
        note("info", tf("engine/statistiques.py:g:531:22", v1=p['a'], v2=p['b'], v3=p['force'].replace('forte', 'fort'), v4=p['mesure'], v5=_fmt(p['valeur'], 2), v6=sens))
    if liens:
        note("info", _i18n_t("engine/statistiques.py:g:533:22"))
    constats.sort(key=lambda c: c["niveau"] != "attention")
    return {"constats": constats[:40], "qualite": qualite}


# ── Échantillonnage (en mémoire : la table n'est jamais modifiée) ────────────
METHODES_ECHANTILLON = {"aleatoire": td("engine/statistiques.py:g:540:38"), "systematique": td("engine/statistiques.py:g:540:74"), "stratifie": td("engine/statistiques.py:g:540:121"),
                        "premieres": td("engine/statistiques.py:g:541:38")}


def echantillonner(n: int, methode: str, taille: int, graine: int | None = None, strates: list | None = None, remise: bool = False) -> list[int]:
    """Indices (triés) des lignes retenues parmi `n`. `strates` = la modalité de chaque ligne (méthode stratifiée)."""
    taille = int(taille)
    if taille < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:547:26"))
    if n == 0:
        return []
    rng = random.Random(graine if graine is not None else 12345)
    if methode == "premieres":
        return list(range(min(taille, n)))
    if methode == "systematique":
        taille = min(taille, n)
        pas = n / taille
        debut = rng.random() * pas
        return sorted({min(n - 1, int(debut + i * pas)) for i in range(taille)})
    if methode == "stratifie":
        if not strates or len(strates) != n:
            raise ValueError(_i18n_t("engine/statistiques.py:g:560:30"))
        classes: dict = {}
        for i, s in enumerate(strates):
            classes.setdefault(etiquette(s) or "(vide)", []).append(i)
        taille = min(taille, n)
        sortie = []
        for _m, idx in classes.items():
            k = max(1, round(taille * len(idx) / n))
            sortie += rng.choices(idx, k=k) if remise else rng.sample(idx, min(k, len(idx)))
        return sorted(sortie)
    if methode == "aleatoire":
        return sorted(rng.choices(range(n), k=taille) if remise else rng.sample(range(n), min(taille, n)))
    raise ValueError(_i18n_t("engine/statistiques.py:g:572:22"))


# ── Inférence ────────────────────────────────────────────────────────────────
def _ALPHA():
    return {"cle": "alpha", "libelle": _i18n_t("engine/statistiques.py:g:577:40"), "defaut": "0.05", "type": "select", "options": [["0.10", "10 %"], ["0.05", "5 %"], ["0.01", "1 %"]]}


def _ALTP():
    return {"cle": "alt", "libelle": _i18n_t("engine/statistiques.py:g:581:38"), "defaut": "different", "type": "select", "options": [["different", _i18n_t("engine/statistiques.py:g:581:130")], ["superieur", _i18n_t("rg.superieur")], ["inferieur", _i18n_t("rg.inferieur")]]}


def _role(cle, libelle, genre="any", facultatif=False, multiple=False):
    return {"cle": cle, "libelle": libelle, "genre": genre, "facultatif": facultatif, "multiple": multiple}


def _num(cle, libelle, defaut):
    return {"cle": cle, "libelle": libelle, "defaut": str(defaut), "type": "number"}


# Modèles prédéfinis : (formule, libellé). Les paramètres sont les noms de la formule (hors x), dans l'ordre d'apparition.
MODELES_NL = {
    "exponentielle": ("a * exp(b * x)", tl("engine/statistiques.py:g:594:41")),
    "exp_asymptote": ("a + b * exp(-c * x)", tl("engine/statistiques.py:g:595:46")),
    "puissance": ("a * x ** b", "Puissance : a·x^b"),
    "logistique": ("a / (1 + exp(-b * (x - c)))", tl("engine/statistiques.py:g:597:51")),
    "gompertz": ("a * exp(-b * exp(-c * x))", "Gompertz : a·exp(−b·exp(−c·x))"),
    "michaelis": ("a * x / (b + x)", "Saturation (Michaelis-Menten) : a·x / (b + x)"),
    "hill": ("a * x ** n / (k ** n + x ** n)", "Hill : a·x^n / (k^n + x^n)"),
    "gaussienne": ("a * exp(-(x - b) ** 2 / (2 * c ** 2))", "Cloche (gaussienne) : a·exp(−(x − b)² / (2c²))"),
    "logarithmique": ("a + b * log(x)", "Logarithmique : a + b·ln(x)"),
    "cobb_douglas": ("a * x ** b * x2 ** c", tl("engine/statistiques.py:g:603:46")),
    "saturations": ("a * x / (b + x) * x2 / (c + x2)", tl("engine/statistiques.py:g:604:56")),
    "inhibition": ("a * x / (b * (1 + x2 / c) + x)", tl("engine/statistiques.py:g:605:54")),
    "perso": ("", tl("engine/statistiques.py:g:606:19")),
}
MODELES_NX = {"cobb_douglas": 2, "saturations": 2, "inhibition": 2}      # nombre de variables explicatives des modèles prédéfinis (1 par défaut)


_GRP = _role("groupe", td("rg.groupes_quali"), "quali", True)
CATALOGUE_TESTS = [
    {"cle": "auto", "nom": td("engine/statistiques.py:g:614:28"), "famille": td("rg.famille.assistant"), "aide": td("engine/statistiques.py:g:614:93"),
     "roles": [_role("a", td("engine/statistiques.py:g:615:27")), _role("b", td("engine/statistiques.py:g:615:59"), "any", True), _GRP], "params": [_ALPHA()]},
    {"cle": "t1", "nom": td("engine/statistiques.py:g:616:26"), "famille": td("rg.famille.moyennes"), "aide": td("engine/statistiques.py:g:616:104"),
     "roles": [_role("a", td("engine/statistiques.py:g:617:27"), "quanti")], "params": [_num("mu0", td("engine/statistiques.py:g:617:84"), 0), _ALTP(), _ALPHA()]},
    {"cle": "z1", "nom": td("engine/statistiques.py:g:618:26"), "famille": td("rg.famille.moyennes"), "aide": td("engine/statistiques.py:g:618:103"),
     "roles": [_role("a", td("engine/statistiques.py:g:619:27"), "quanti")], "params": [_num("mu0", td("engine/statistiques.py:g:619:84"), 0), _num("sigma", td("engine/statistiques.py:g:619:125"), 1), _ALTP(), _ALPHA()]},
    {"cle": "t2", "nom": td("engine/statistiques.py:g:620:26"), "famille": td("rg.famille.moyennes"), "aide": td("engine/statistiques.py:g:620:111"),
     "roles": [_role("a", td("engine/statistiques.py:g:621:27"), "quanti"), _role("b", td("engine/statistiques.py:g:621:71"), "quanti", True), _GRP],
     "params": [{"cle": "egal_var", "libelle": td("rg.lib.variances"), "defaut": "0", "type": "select", "options": [["0", td("engine/statistiques.py:g:622:112")], ["1", td("engine/statistiques.py:g:622:158")]]}, _ALTP(), _ALPHA()]},
    {"cle": "tapparie", "nom": td("engine/statistiques.py:g:623:32"), "famille": td("rg.famille.moyennes"), "aide": td("engine/statistiques.py:g:623:113"),
     "roles": [_role("a", td("engine/statistiques.py:g:624:27"), "quanti"), _role("b", td("engine/statistiques.py:g:624:68"), "quanti")], "params": [_ALTP(), _ALPHA()]},
    {"cle": "anova", "nom": td("engine/statistiques.py:g:625:29"), "famille": td("rg.famille.moyennes"), "aide": td("engine/statistiques.py:g:625:112"),
     "roles": [_role("a", td("engine/statistiques.py:g:626:27"), "quanti"), _role("groupe", td("rg.groupes_quali"), "quali")], "params": [_ALPHA()]},
    {"cle": "anova2", "nom": td("engine/statistiques.py:g:627:30"), "famille": td("rg.famille.moyennes"), "aide": td("engine/statistiques.py:g:627:105"),
     "roles": [_role("a", td("engine/statistiques.py:g:628:27"), "quanti"), _role("groupe", td("rg.facteur1_quali"), "quali"), _role("groupe2", td("rg.facteur2_quali"), "quali")],
     "params": [{"cle": "interaction", "libelle": td("rg.lib.interaction"), "defaut": "1", "type": "select", "options": [["1", td("engine/statistiques.py:g:629:117")], ["0", td("engine/statistiques.py:g:629:157")]]}, _ALPHA()]},
    {"cle": "anovan", "nom": td("engine/statistiques.py:g:630:30"), "famille": td("rg.famille.moyennes"), "aide": td("engine/statistiques.py:g:630:111"),
     "roles": [_role("a", td("engine/statistiques.py:g:631:27"), "quanti"), _role("explicatives", td("engine/statistiques.py:g:631:82"), "quali", False, True)],
     "params": [{"cle": "ordre", "libelle": td("rg.lib.interactions"), "defaut": "2", "type": "select", "options": [["1", td("engine/statistiques.py:g:632:112")], ["2", td("engine/statistiques.py:g:632:157")], ["3", td("engine/statistiques.py:g:632:201")], ["4", td("engine/statistiques.py:g:632:242")], ["10", td("engine/statistiques.py:g:632:286")]]}, _ALPHA()]},
    {"cle": "prop1", "nom": td("engine/statistiques.py:g:633:29"), "famille": td("rg.famille.proportions"), "aide": td("engine/statistiques.py:g:633:103"),
     "roles": [_role("a", td("rg.var_quali"), "quali")], "params": [{"cle": "succes", "libelle": td("engine/statistiques.py:g:634:102"), "defaut": "", "type": "text"}, _num("p0", td("engine/statistiques.py:g:634:194"), 0.5), _ALTP(), _ALPHA()]},
    {"cle": "prop2", "nom": td("rg.nom.deux_proportions"), "famille": td("rg.famille.proportions"), "aide": td("engine/statistiques.py:g:635:92"),
     "roles": [_role("a", td("rg.var_quali"), "quali"), _role("groupe", td("engine/statistiques.py:g:636:77"), "quali")], "params": [{"cle": "succes", "libelle": td("engine/statistiques.py:g:636:153"), "defaut": "", "type": "text"}, _ALTP(), _ALPHA()]},
    {"cle": "var1", "nom": td("engine/statistiques.py:g:637:28"), "famille": td("rg.famille.variances"), "aide": td("engine/statistiques.py:g:637:109"),
     "roles": [_role("a", td("engine/statistiques.py:g:638:27"), "quanti")], "params": [_num("sigma0", td("engine/statistiques.py:g:638:87"), 1), _ALTP(), _ALPHA()]},
    {"cle": "var2", "nom": td("rg.nom.deux_variances"), "famille": td("rg.famille.variances"), "aide": td("engine/statistiques.py:g:639:96"),
     "roles": [_role("a", td("engine/statistiques.py:g:640:27"), "quanti"), _role("b", td("engine/statistiques.py:g:640:71"), "quanti", True), _GRP], "params": [_ALPHA()]},
    {"cle": "levene", "nom": td("engine/statistiques.py:g:641:30"), "famille": td("rg.famille.variances"), "aide": td("engine/statistiques.py:g:641:108"),
     "roles": [_role("a", td("engine/statistiques.py:g:642:27"), "quanti"), _role("groupe", td("rg.groupes_quali"), "quali")], "params": [_ALPHA()]},
    {"cle": "mannwhitney", "nom": td("engine/statistiques.py:g:643:35"), "famille": td("engine/statistiques.py:g:643:103"), "aide": td("engine/statistiques.py:g:643:132"),
     "roles": [_role("a", td("engine/statistiques.py:g:644:27")), _role("b", td("engine/statistiques.py:g:644:75"), "any", True), _GRP], "params": [_ALTP(), _ALPHA()]},
    {"cle": "wilcoxon", "nom": td("engine/statistiques.py:g:645:32"), "famille": td("engine/statistiques.py:g:645:104"), "aide": td("engine/statistiques.py:g:645:133"),
     "roles": [_role("a", td("engine/statistiques.py:g:646:27")), _role("b", td("engine/statistiques.py:g:646:58"))], "params": [_ALTP(), _ALPHA()]},
    {"cle": "kruskal", "nom": td("engine/statistiques.py:g:647:31"), "famille": td("engine/statistiques.py:g:647:96"), "aide": td("engine/statistiques.py:g:647:125"),
     "roles": [_role("a", td("engine/statistiques.py:g:648:27")), _role("groupe", td("rg.groupes_quali"), "quali")], "params": [_ALPHA()]},
    {"cle": "khi2_indep", "nom": td("engine/statistiques.py:g:649:34"), "famille": td("engine/statistiques.py:g:649:103"), "aide": td("engine/statistiques.py:g:649:131"),
     "roles": [_role("a", td("engine/statistiques.py:g:650:27"), "quali"), _role("b", td("engine/statistiques.py:g:650:81"), "quali")], "params": [_ALPHA()]},
    {"cle": "khi2_adeq", "nom": td("engine/statistiques.py:g:651:33"), "famille": td("engine/statistiques.py:g:651:96"), "aide": td("engine/statistiques.py:g:651:124"),
     "roles": [_role("a", td("rg.var_quali"), "quali")], "params": [{"cle": "attendues", "libelle": td("engine/statistiques.py:g:652:105"), "defaut": "", "type": "text"}, _ALPHA()]},
    {"cle": "fisher", "nom": td("engine/statistiques.py:g:653:30"), "famille": td("engine/statistiques.py:g:653:79"), "aide": td("engine/statistiques.py:g:653:107"),
     "roles": [_role("a", td("engine/statistiques.py:g:654:27"), "quali"), _role("b", td("engine/statistiques.py:g:654:86"), "quali")], "params": [_ALTP(), _ALPHA()]},
    {"cle": "correlation", "nom": td("engine/statistiques.py:g:655:35"), "famille": "Relations", "aide": td("engine/statistiques.py:g:655:114"),
     "roles": [_role("a", td("engine/statistiques.py:g:656:27"), "quanti"), _role("b", td("engine/statistiques.py:g:656:70"), "quanti")],
     "params": [{"cle": "methode", "libelle": _gtd("rg8.msg.027"), "defaut": "pearson", "type": "select", "options": [["pearson", td("engine/statistiques.py:g:657:120")], ["spearman", _gtd("rg8.msg.028")]]}, _ALTP(), _ALPHA()]},
    {"cle": "regression", "nom": td("engine/statistiques.py:g:658:34"), "famille": "Relations", "aide": td("engine/statistiques.py:g:658:110"),
     "roles": [_role("a", td("engine/statistiques.py:g:659:27"), "quanti"), _role("explicatives", "Variables explicatives", "any", False, True)], "params": [_ALPHA()]},
    {"cle": "polynomiale", "nom": td("engine/statistiques.py:g:660:35"), "famille": "Relations", "aide": td("engine/statistiques.py:g:660:102"),
     "roles": [_role("a", td("engine/statistiques.py:g:661:27"), "quanti"), _role("b", "Variable explicative", "quanti")], "params": [_num("degre", td("engine/statistiques.py:g:661:134"), 2), _ALPHA()]},
    {"cle": "nonlineaire", "nom": td("engine/statistiques.py:g:662:35"), "famille": "Relations", "aide": td("engine/statistiques.py:g:662:120"),
     "roles": [_role("a", td("engine/statistiques.py:g:663:27"), "quanti"), _role("b", "Variable explicative (x)", "quanti"), _role("explicatives", td("engine/statistiques.py:g:663:134"), "quanti", True, True)],
     "params": [{"cle": "modele", "libelle": td("engine/statistiques.py:g:664:46"), "defaut": "exponentielle", "type": "select", "options": [[c, m[1]] for c, m in MODELES_NL.items()]},
                {"cle": "formule", "libelle": td("engine/statistiques.py:g:665:47"), "defaut": "", "type": "text"},
                {"cle": "depart", "libelle": td("engine/statistiques.py:g:666:46"), "defaut": "", "type": "text"},
                {"cle": "pglob", "libelle": td("engine/statistiques.py:g:667:45"), "defaut": "approchee", "type": "select", "options": [["approchee", td("engine/statistiques.py:g:667:132")], ["permutation", td("engine/statistiques.py:g:667:179")]]},
                _num("permutations", td("engine/statistiques.py:g:668:38"), 199),
                {"cle": "erreurs", "libelle": td("engine/statistiques.py:g:669:47"), "defaut": "asymptotiques", "type": "select", "options": [["asymptotiques", td("engine/statistiques.py:g:669:157")], ["bootstrap", td("engine/statistiques.py:g:669:204")]]},
                _num("bootstraps", td("engine/statistiques.py:g:670:36"), 399), _num("temps_max", td("engine/statistiques.py:g:670:110"), 12), _ALPHA()]},
    {"cle": "logistique", "nom": td("engine/statistiques.py:g:671:34"), "famille": "Relations", "aide": td("engine/statistiques.py:g:671:107"),
     "roles": [_role("a", td("engine/statistiques.py:g:672:27"), "quali"), _role("explicatives", "Variables explicatives", "any", False, True)],
     "params": [{"cle": "succes", "libelle": td("engine/statistiques.py:g:673:46"), "defaut": "", "type": "text"}, _ALPHA()]},
    {"cle": "normalite", "nom": td("engine/statistiques.py:g:674:33"), "famille": "Conditions", "aide": td("engine/statistiques.py:g:674:115"),
     "roles": [_role("a", td("engine/statistiques.py:g:675:27"), "quanti")], "params": [_ALPHA()]},
    {"cle": "bootstrap", "nom": td("engine/statistiques.py:g:676:33"), "famille": "Estimation", "aide": td("engine/statistiques.py:g:676:127"),
     "roles": [_role("a", td("engine/statistiques.py:g:677:27"), "quanti")],
     "params": [{"cle": "stat", "libelle": td("rg.lib.statistique"), "defaut": "moyenne", "type": "select", "options": [["moyenne", td("engine/statistiques.py:g:678:122")], ["mediane", td("engine/statistiques.py:g:678:146")], ["ecart_type", td("rg.ecart_type")]]}, _num("confiance", td("engine/statistiques.py:g:678:208"), 0.95), _num("repetitions", td("engine/statistiques.py:g:678:276"), 2000)]},
    {"cle": "taille_moy", "nom": td("engine/statistiques.py:g:679:34"), "famille": "Estimation", "aide": td("engine/statistiques.py:g:679:116"),
     "roles": [], "params": [_num("sigma", td("engine/statistiques.py:g:680:44"), 1), _num("marge", td("engine/statistiques.py:g:680:83"), 0.1), _num("confiance", td("engine/statistiques.py:g:680:135"), 0.95)]},
    {"cle": "taille_prop", "nom": td("engine/statistiques.py:g:681:35"), "famille": "Estimation", "aide": td("engine/statistiques.py:g:681:120"),
     "roles": [], "params": [_num("p", td("engine/statistiques.py:g:682:40"), 0.5), _num("marge", td("engine/statistiques.py:g:682:110"), 0.05), _num("confiance", td("engine/statistiques.py:g:682:171"), 0.95)]},
]
_CAT = {t["cle"]: t for t in CATALOGUE_TESTS}


def _sortie(nom, h0, h1, stat_nom, stat, ddl, p, alpha, phrases, **extra):
    sig = p < alpha
    return {"test": nom, "h0": h0, "h1": h1, "statistique": {"nom": stat_nom, "valeur": stat}, "ddl": ddl, "p": p, "alpha": alpha, "significatif": sig,
            "conclusion": tf("engine/statistiques.py:g:689:27", v1=_i18n_t("engine/statistiques.py:g:691:68") if sig else _i18n_t("engine/statistiques.py:g:691:104"), v2=_fmt(alpha * 100), v3=_p_fmt(p), v4=phrases[0] if sig else phrases[1]),
            "avertissements": extra.pop("avertissements", []), **extra}


def _param(params, cle, defaut=None, cast=float):
    v = params.get(cle, defaut)
    if v is None or (isinstance(v, str) and not v.strip()):
        v = defaut
    try:
        return cast(v)
    except (TypeError, ValueError):
        raise ValueError(tf("engine/statistiques.py:g:700:26", v1=cle))


def _var(v, nom=tl("engine/statistiques.py:g:703:17")):
    if not v:
        raise ValueError(_gtf("rg8.msg.026", nom=nom))
    return v


def _quant(v):
    x = _nombres(v["valeurs"])
    if x.size == 0 or x.size < 0.5 * max(1, sum(1 for e in v["valeurs"] if e is not None and e != "")):
        raise ValueError(tf("engine/statistiques.py:g:712:26", v1=v['nom']))
    return x


def _groupes(q, g):
    """{modalité: tableau numérique} d'une variable numérique `q` selon les modalités de `g` (lignes alignées, valeurs manquantes ignorées)."""
    sortie: dict = {}
    for u, m in zip(q["valeurs"], g["valeurs"]):
        u, m = _reel(u), etiquette(m)
        if u is not None and m is not None:
            sortie.setdefault(m, []).append(u)
    if len(sortie) > LIMITES["groupes"]:
        raise ValueError(tf("engine/statistiques.py:g:724:26", v1=g['nom'], v2=len(sortie), v3=LIMITES['groupes']))
    return {m: np.array(v) for m, v in sorted(sortie.items())}


def _deux(variables):
    """(nom_x, x, nom_y, y) : deux échantillons indépendants, soit par deux variables, soit par les deux modalités de `groupe`."""
    a, b, g = _var(variables.get("a"), _i18n_t("engine/statistiques.py:g:730:40")), variables.get("b"), variables.get("groupe")
    if g:
        grp = _groupes(a, g)
        if len(grp) != 2:
            raise ValueError(tf("engine/statistiques.py:g:734:30", v1=g['nom'], v2=len(grp)) + (f" : {', '.join(list(grp)[:6])}." if grp else "."))
        (m1, x), (m2, y) = grp.items()
        return f"{a['nom']} — {m1}", x, f"{a['nom']} — {m2}", y
    if b:
        return a["nom"], _quant(a), b["nom"], _quant(b)
    raise ValueError(_i18n_t("engine/statistiques.py:g:739:22"))


def _paires(a, b):
    lignes = [(_reel(u), _reel(v)) for u, v in zip(a["valeurs"], b["valeurs"])]
    lignes = [(u, v) for u, v in lignes if u is not None and v is not None]
    return np.array([u for u, _ in lignes]), np.array([v for _, v in lignes])


def _resume(x):
    return {"n": int(x.size), "moyenne": float(x.mean()), "ecart_type": float(x.std(ddof=1)) if x.size > 1 else None, "mediane": float(np.median(x)), "min": float(x.min()), "max": float(x.max())}


def _normal_ok(x, alpha=0.05):
    """True / False / None (trop peu de valeurs pour juger)."""
    nm = _normalite_dagostino(np.sort(x))
    return None if nm is None else nm[1] >= alpha


def _t_un(x, mu0, alt, alpha, nom, libelle=tl("engine/statistiques.py:g:758:44")):
    n = x.size
    if n < 2:
        raise ValueError(_i18n_t("engine/statistiques.py:g:761:26"))
    m, s = float(x.mean()), float(x.std(ddof=1))
    if s == 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:764:26"))
    se = s / math.sqrt(n)
    t = (m - mu0) / se
    marge = _t_crit(n - 1, 1 - alpha) * se
    return _sortie(tf("engine/statistiques.py:g:768:20", v1=nom), tf("engine/statistiques.py:g:768:50", v1=_fmt(mu0)), tf("engine/statistiques.py:g:768:82", v1=_ALT[alt], v2=_fmt(mu0)), libelle, t, n - 1, _p_t(t, n - 1, alt), alpha,
                   (tf("engine/statistiques.py:g:769:21", v1=_fmt(m), v2=_fmt(mu0)), tf("engine/statistiques.py:g:769:72", v1=_fmt(m), v2=_fmt(mu0))),
                   estimation={"moyenne": m, "ecart_type": s, "n": n, "erreur_standard": se}, intervalle={"libelle": tf("engine/statistiques.py:g:772:118", v1=_fmt((1 - alpha) * 100)), "bas": m - marge, "haut": m + marge},
                   effet={"nom": _i18n_t("engine/statistiques.py:g:773:34"), "valeur": (m - mu0) / s}, avertissements=([] if n >= 30 or _normal_ok(x) else [_i18n_t("engine/statistiques.py:g:773:127")]))


def _test_t1(v, p, alpha, alt):
    return _t_un(_quant(_var(v.get("a"), _i18n_t("engine/statistiques.py:g:775:42"))), _param(p, "mu0", 0.0), alt, alpha, _var(v.get("a"))["nom"])


def _test_z1(v, p, alpha, alt):
    x = _quant(_var(v.get("a"), td("engine/statistiques.py:g:780:33")))
    sigma, mu0 = _param(p, "sigma", 1.0), _param(p, "mu0", 0.0)
    if sigma <= 0 or x.size < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:782:26"))
    m, n = float(x.mean()), x.size
    se = sigma / math.sqrt(n)
    z = (m - mu0) / se
    marge = _ND.inv_cdf(1 - alpha / 2) * se
    return _sortie(_gtf("engine/statistiques.py:942:20", v1=(v['a']['nom'])), tf("engine/statistiques.py:g:787:49", v1=_fmt(mu0)), tf("engine/statistiques.py:g:787:81", v1=_ALT[alt], v2=_fmt(mu0)), "z", z, None, _p_z(z, alt), alpha,
                   (tf("engine/statistiques.py:g:788:21", v1=_fmt(m), v2=_fmt(mu0)), tf("engine/statistiques.py:g:788:72", v1=_fmt(m), v2=_fmt(mu0))),
                   estimation={"moyenne": m, "n": n, "erreur_standard": se}, intervalle={"libelle": tf("engine/statistiques.py:g:791:101", v1=_fmt((1 - alpha) * 100)), "bas": m - marge, "haut": m + marge})


def _test_t2(v, p, alpha, alt):
    nx, x, ny, y = _deux(v)
    if x.size < 2 or y.size < 2:
        raise ValueError(_i18n_t("engine/statistiques.py:g:795:26"))
    mx, my, vx, vy = float(x.mean()), float(y.mean()), float(x.var(ddof=1)), float(y.var(ddof=1))
    if vx == 0 and vy == 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:798:26"))
    if _param(p, "egal_var", 0, int) == 1:
        sp2 = ((x.size - 1) * vx + (y.size - 1) * vy) / (x.size + y.size - 2)
        se, df, methode = math.sqrt(sp2 * (1 / x.size + 1 / y.size)), x.size + y.size - 2.0, _i18n_t("engine/statistiques.py:g:801:94")
    else:
        a, b = vx / x.size, vy / y.size
        se = math.sqrt(a + b)
        df = (a + b) ** 2 / (a * a / (x.size - 1) + b * b / (y.size - 1))
        methode = "Welch"
    t = (mx - my) / se
    marge = _t_crit(df, 1 - alpha) * se
    sp = math.sqrt(((x.size - 1) * vx + (y.size - 1) * vy) / (x.size + y.size - 2))
    avis = [] if min(x.size, y.size) >= 30 or (_normal_ok(x) and _normal_ok(y)) else [_i18n_t("engine/statistiques.py:g:810:87")]
    return _sortie(tf("engine/statistiques.py:g:811:20", v1=methode), _i18n_t("engine/statistiques.py:g:811:65"), tf("engine/statistiques.py:g:811:98", v1=_ALT[alt]), "t", t, df, _p_t(t, df, alt), alpha,
                   (tf("engine/statistiques.py:g:812:21", v1=_fmt(mx), v2=_fmt(my)), tf("engine/statistiques.py:g:812:80", v1=_fmt(mx), v2=_fmt(my))),
                   groupes=[{"nom": nx, **_resume(x)}, {"nom": ny, **_resume(y)}], intervalle={"libelle": tf("engine/statistiques.py:g:815:107", v1=_fmt((1 - alpha) * 100)), "bas": mx - my - marge, "haut": mx - my + marge},
                   effet={"nom": _i18n_t("engine/statistiques.py:g:816:34"), "valeur": (mx - my) / sp if sp else None}, avertissements=avis)


def _test_tapparie(v, p, alpha, alt):
    a, b = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:818:29")), _var(v.get("b"), _i18n_t("engine/statistiques.py:g:818:69"))
    x, y = _paires(a, b)
    if x.size < 2:
        raise ValueError(_i18n_t("engine/statistiques.py:g:821:26"))
    r = _t_un(x - y, 0.0, alt, alpha, tf("engine/statistiques.py:g:822:39", v1=a['nom'], v2=b['nom']))
    r["test"] = td("engine/statistiques.py:g:825:17")
    r["h0"], r["h1"] = _i18n_t("engine/statistiques.py:g:824:24"), tf("engine/statistiques.py:g:824:59", v1=_ALT[alt])
    r["groupes"] = [{"nom": a["nom"], **_resume(x)}, {"nom": b["nom"], **_resume(y)}, {"nom": td("engine/statistiques.py:g:827:95"), **_resume(x - y)}]
    return r


def _succes(modalites, p, etiq):
    s = etiquette(p.get("succes"))
    if s is None:
        s = Counter(etiq).most_common(1)[0][0]
    if s not in modalites:
        raise ValueError(tf("engine/statistiques.py:g:834:26", v1=s, v2=', '.join(sorted(modalites)[:8])))
    return s


def _wilson(k, n, conf):
    z = _ND.inv_cdf(1 - (1 - conf) / 2)
    ph = k / n
    c = (ph + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, c - h), min(1.0, c + h)


def _test_prop1(v, p, alpha, alt):
    a = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:847:26"))
    etiq = [e for e in (etiquette(u) for u in a["valeurs"]) if e is not None]
    if not etiq:
        raise ValueError(_i18n_t("engine/statistiques.py:g:850:26"))
    s = _succes(set(etiq), p, etiq)
    p0 = _param(p, "p0", 0.5)
    if not 0 < p0 < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:854:26"))
    n, k = len(etiq), sum(1 for e in etiq if e == s)
    ph = k / n
    exact = n * p0 < 10 or n * (1 - p0) < 10
    bin_p = {"n": n, "p": p0}
    if exact:
        if alt == "superieur":
            pv = L.sf("binomiale", bin_p, k - 1)
        elif alt == "inferieur":
            pv = L.cdf("binomiale", bin_p, k)
        else:
            pk = L.pdf("binomiale", bin_p, k) * (1 + 1e-7)
            pv = min(1.0, sum(L.pdf("binomiale", bin_p, j) for j in range(n + 1) if L.pdf("binomiale", bin_p, j) <= pk))
        stat_nom, stat, methode = _i18n_t("engine/statistiques.py:g:867:35"), float(k), _gt("engine/statistiques.py:1022:89")
    else:
        stat = (ph - p0) / math.sqrt(p0 * (1 - p0) / n)
        pv, stat_nom, methode = _p_z(stat, alt), "z", _gt("engine/statistiques.py:1025:55")
    lo, hi = _wilson(k, n, 1 - alpha)
    return _sortie(tf("engine/statistiques.py:g:872:20", v1=s, v2=methode), tf("engine/statistiques.py:g:872:58", v1=s, v2=_fmt(p0)), tf("engine/statistiques.py:g:872:103", v1=s, v2=_ALT[alt], v3=_fmt(p0)), stat_nom, stat, None, pv, alpha,
                   (tf("engine/statistiques.py:g:873:21", v1=_fmt(ph * 100, 4), v2=_fmt(p0 * 100, 4)), tf("engine/statistiques.py:g:873:106", v1=_fmt(ph * 100, 4), v2=_fmt(p0 * 100, 4))),
                   estimation={"succes": k, "n": n, "proportion": ph}, intervalle={"libelle": tf("engine/statistiques.py:g:876:95", v1=_fmt((1 - alpha) * 100)), "bas": lo, "haut": hi})


def _test_prop2(v, p, alpha, alt):
    a, g = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:878:29")), _var(v.get("groupe"), _i18n_t("engine/statistiques.py:g:878:79"))
    lignes = [(etiquette(u), etiquette(w)) for u, w in zip(a["valeurs"], g["valeurs"])]
    lignes = [(u, w) for u, w in lignes if u is not None and w is not None]
    mods = sorted({w for _, w in lignes})
    if len(mods) != 2:
        raise ValueError(tf("engine/statistiques.py:g:883:26", v1=len(mods)))
    s = _succes({u for u, _ in lignes}, p, [u for u, _ in lignes])
    n = [sum(1 for _, w in lignes if w == m) for m in mods]
    k = [sum(1 for u, w in lignes if w == m and u == s) for m in mods]
    if min(n) < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:888:26"))
    p1, p2, pp = k[0] / n[0], k[1] / n[1], sum(k) / sum(n)
    avis = []
    if pp in (0.0, 1.0):
        raise ValueError(_i18n_t("engine/statistiques.py:g:892:26"))
    if min(n[0] * pp, n[0] * (1 - pp), n[1] * pp, n[1] * (1 - pp)) < 5:
        avis.append(_i18n_t("engine/statistiques.py:g:894:21"))
    z = (p1 - p2) / math.sqrt(pp * (1 - pp) * (1 / n[0] + 1 / n[1]))
    se = math.sqrt(p1 * (1 - p1) / n[0] + p2 * (1 - p2) / n[1])
    marge = _ND.inv_cdf(1 - alpha / 2) * se
    return _sortie(tf("engine/statistiques.py:g:898:20", v1=s), _i18n_t("engine/statistiques.py:g:898:67"), tf("engine/statistiques.py:g:898:103", v1=mods[0], v2=_ALT[alt], v3=mods[1]), "z", z, None, _p_z(z, alt), alpha,
                   (tf("engine/statistiques.py:g:899:21", v1=_fmt(p1 * 100, 4), v2=_fmt(p2 * 100, 4)), tf("engine/statistiques.py:g:899:105", v1=_fmt(p1 * 100, 4), v2=_fmt(p2 * 100, 4))),
                   groupes=[{"nom": mods[i], "n": n[i], "succes": k[i], "proportion": k[i] / n[i]} for i in range(2)],
                   intervalle={"libelle": tf("engine/statistiques.py:g:903:43", v1=_fmt((1 - alpha) * 100)), "bas": p1 - p2 - marge, "haut": p1 - p2 + marge}, avertissements=avis)


def _test_var1(v, p, alpha, alt):
    x = _quant(_var(v.get("a"), td("engine/statistiques.py:g:906:33")))
    s0 = _param(p, "sigma0", 1.0)
    if s0 <= 0 or x.size < 2:
        raise ValueError(_i18n_t("engine/statistiques.py:g:908:26"))
    n, s2 = x.size, float(x.var(ddof=1))
    stat = (n - 1) * s2 / (s0 * s0)
    c, q = L.cdf("khi2", {"k": n - 1.0}, stat), L.sf("khi2", {"k": n - 1.0}, stat)
    pv = q if alt == "superieur" else c if alt == "inferieur" else min(1.0, 2 * min(c, q))
    bas = (n - 1) * s2 / L.quantile("khi2", {"k": n - 1.0}, 1 - alpha / 2)
    haut = (n - 1) * s2 / L.quantile("khi2", {"k": n - 1.0}, alpha / 2)
    return _sortie(_i18n_t("engine/statistiques.py:g:915:20"), tf("engine/statistiques.py:g:915:51", v1=_fmt(s0)), tf("engine/statistiques.py:g:915:84", v1=_ALT[alt], v2=_fmt(s0)), "χ²", stat, n - 1, pv, alpha,
                   (tf("engine/statistiques.py:g:916:21", v1=_fmt(math.sqrt(s2)), v2=_fmt(s0)), tf("engine/statistiques.py:g:916:106", v1=_fmt(math.sqrt(s2)), v2=_fmt(s0))),
                   intervalle={"libelle": tf("engine/statistiques.py:g:919:43", v1=_fmt((1 - alpha) * 100)), "bas": math.sqrt(bas), "haut": math.sqrt(haut)},
                   avertissements=[] if n >= 30 or _normal_ok(x) else [_i18n_t("engine/statistiques.py:g:920:72")])


def _test_var2(v, p, alpha, alt):
    nx, x, ny, y = _deux(v)
    if x.size < 2 or y.size < 2:
        raise ValueError(_i18n_t("engine/statistiques.py:g:924:26"))
    vx, vy = float(x.var(ddof=1)), float(y.var(ddof=1))
    if vy == 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:927:26"))
    f, d1, d2 = vx / vy, x.size - 1.0, y.size - 1.0
    c, q = L.cdf("fisher", {"d1": d1, "d2": d2}, f), L.sf("fisher", {"d1": d1, "d2": d2}, f)
    return _sortie(_i18n_t("engine/statistiques.py:g:930:20"), _i18n_t("engine/statistiques.py:g:930:62"), _i18n_t("engine/statistiques.py:g:930:96"), "F", f, f"{int(d1)} ; {int(d2)}", min(1.0, 2 * min(c, q)), alpha,
                   (_i18n_t("engine/statistiques.py:g:931:21"), _i18n_t("engine/statistiques.py:g:931:51")), groupes=[{"nom": nx, **_resume(x)}, {"nom": ny, **_resume(y)}],
                   avertissements=[] if min(x.size, y.size) >= 30 or (_normal_ok(x) and _normal_ok(y)) else [_i18n_t("engine/statistiques.py:g:934:110")])


def _anova(groupes):
    tout = np.concatenate(list(groupes.values()))
    N, k = tout.size, len(groupes)
    gm = tout.mean()
    ssb = sum(g.size * (g.mean() - gm) ** 2 for g in groupes.values())
    ssw = sum(((g - g.mean()) ** 2).sum() for g in groupes.values())
    if ssw <= 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:942:26"))
    d1, d2 = k - 1.0, N - k + 0.0
    f = (ssb / d1) / (ssw / d2)
    return float(ssb), float(ssw), d1, d2, float(f), L.sf("fisher", {"d1": d1, "d2": d2}, f)


def _grp_k(v, minimum=2):
    a, g = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:949:29")), _var(v.get("groupe"), _i18n_t("engine/statistiques.py:g:949:77"))
    grp = {m: x for m, x in _groupes(a, g).items() if x.size >= 2 or minimum == 1}
    if len(grp) < minimum:
        raise ValueError(tf("engine/statistiques.py:g:952:26", v1=minimum, v2=g['nom'], v3=len(grp)))
    return a, g, grp


def _test_anova(v, p, alpha, alt):
    a, g, grp = _grp_k(v, 2)
    ssb, ssw, d1, d2, f, pv = _anova(grp)
    sst = ssb + ssw
    msw = ssw / d2
    avis = []
    if any(x.size < 30 for x in grp.values()) and not all(_normal_ok(x) is not False for x in grp.values()):
        avis.append(_i18n_t("engine/statistiques.py:g:963:21"))
    lev = _levene(grp)
    if lev[1] < 0.05:
        avis.append(_i18n_t("engine/statistiques.py:g:966:21"))
    paires = []
    if pv < alpha and len(grp) <= 10:
        noms = list(grp)
        m = len(noms) * (len(noms) - 1) // 2
        for i, u in enumerate(noms):
            for w in noms[i + 1:]:
                d = float(grp[u].mean() - grp[w].mean())
                t = d / math.sqrt(msw * (1 / grp[u].size + 1 / grp[w].size))
                pp = min(1.0, _p_t(t, d2, "different") * m)
                paires.append({"a": u, "b": w, "difference": d, "p": pp, "significatif": pp < alpha})
    return _sortie(tf("engine/statistiques.py:g:977:20", v1=a['nom'], v2=g['nom']), _i18n_t("engine/statistiques.py:g:977:73"), _i18n_t("engine/statistiques.py:g:977:119"), "F", f, f"{int(d1)} ; {int(d2)}", pv, alpha,
                   (_i18n_t("engine/statistiques.py:g:978:21"), _i18n_t("engine/statistiques.py:g:978:105")),
                   groupes=[{"nom": m, **_resume(x)} for m, x in grp.items()], effet={"nom": _i18n_t("engine/statistiques.py:g:981:94"), "valeur": ssb / sst},
                   tableau_anova=[{"source": td("rg.entre_groupes"), "ss": ssb, "ddl": d1, "ms": ssb / d1, "f": f, "p": pv}, {"source": td("engine/statistiques.py:g:981:130"), "ss": ssw, "ddl": d2, "ms": msw}, {"source": _gt("engine/statistiques.py:1135:222"), "ss": sst, "ddl": d1 + d2}],
                   comparaisons={"libelle": _i18n_t("engine/statistiques.py:g:983:45"), "lignes": paires}, avertissements=avis)


def _levene(grp):
    """Brown-Forsythe : ANOVA sur les écarts absolus à la médiane de chaque groupe → (F, p, ddl1, ddl2)."""
    ecarts = {m: np.abs(x - np.median(x)) for m, x in grp.items()}
    try:
        _ssb, _ssw, d1, d2, f, pv = _anova(ecarts)
    except ValueError:
        return 0.0, 1.0, len(grp) - 1, 0
    return f, pv, d1, d2


def _test_levene(v, p, alpha, alt):
    a, g, grp = _grp_k(v, 2)
    f, pv, d1, d2 = _levene(grp)
    return _sortie(_i18n_t("engine/statistiques.py:g:997:20"), _i18n_t("engine/statistiques.py:g:997:66"), _i18n_t("engine/statistiques.py:g:997:115"), "F", f, f"{int(d1)} ; {int(d2)}", pv, alpha,
                   (_i18n_t("engine/statistiques.py:g:998:21"), _i18n_t("engine/statistiques.py:g:998:63")), groupes=[{"nom": m, **_resume(x)} for m, x in grp.items()])


def _test_kruskal(v, p, alpha, alt):
    a, g, grp = _grp_k(v, 2)
    tout = np.concatenate(list(grp.values()))
    N = tout.size
    r = _rangs(tout)
    h, i = 0.0, 0
    for x in grp.values():
        h += r[i:i + x.size].sum() ** 2 / x.size
        i += x.size
    h = 12.0 / (N * (N + 1)) * h - 3 * (N + 1)
    corr = 1 - _corr_ex(tout, None) / (N ** 3 - N)
    if corr <= 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1013:26"))
    h /= corr
    ddl = len(grp) - 1
    avis = [] if min(x.size for x in grp.values()) >= 5 else [_i18n_t("engine/statistiques.py:g:1016:63")]
    return _sortie(_i18n_tf("rg2.statistiques.1", v1=a['nom'], v2=g['nom']), _i18n_t("engine/statistiques.py:g:1017:69"), _i18n_t("engine/statistiques.py:g:1017:109"), "H", h, ddl, L.sf("khi2", {"k": float(ddl)}, h), alpha,
                   (_i18n_t("engine/statistiques.py:g:1018:21"), _i18n_t("engine/statistiques.py:g:1018:47")), groupes=[{"nom": m, **_resume(x)} for m, x in grp.items()],
                   effet={"nom": _i18n_t("engine/statistiques.py:g:1021:34"), "valeur": h / (N - 1)}, avertissements=avis)


def _p_rangs_exact(rangs, k, obs, alt):
    """p-valeur EXACTE d'un test de rangs par dénombrement des permutations (valable avec ex æquo : on permute les rangs observés).
    `rangs` : rangs moyens ; `k` : taille du groupe (Mann-Whitney) ou None (Wilcoxon : toute combinaison de signes) ; `obs` : somme observée des rangs."""
    r2 = [int(round(2 * v)) for v in rangs]          # rangs doublés = entiers (un rang moyen vaut au plus une demi-unité)
    tot, o2 = sum(r2), int(round(2 * obs))
    if k is None:
        dist = np.zeros(tot + 1)
        dist[0] = 1.0
        for r in r2:
            dist[r:] = dist[r:] + dist[:tot + 1 - r]
    else:
        cnt = np.zeros((k + 1, tot + 1))
        cnt[0, 0] = 1.0
        for r in r2:
            cnt[1:, r:] = cnt[1:, r:] + cnt[:-1, :tot + 1 - r]
        dist = cnt[k]
    tous = dist.sum()
    p_inf, p_sup = float(dist[:o2 + 1].sum() / tous), float(dist[o2:].sum() / tous)
    return p_sup if alt == "superieur" else p_inf if alt == "inferieur" else min(1.0, 2 * min(p_inf, p_sup))


def _test_mannwhitney(v, p, alpha, alt):
    nx, x, ny, y = _deux(v)
    n1, n2 = x.size, y.size
    if n1 < 1 or n2 < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1047:26"))
    N = n1 + n2
    tout = np.concatenate([x, y])
    r = _rangs(tout)
    u1 = float(r[:n1].sum() - n1 * (n1 + 1) / 2)
    mu = n1 * n2 / 2
    s2 = n1 * n2 / 12.0 * ((N + 1) - _corr_ex(tout, None) / (N * (N - 1)))
    if s2 <= 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1055:26"))
    sg = math.sqrt(s2)
    z = (u1 - mu - 0.5) / sg if alt == "superieur" else (u1 - mu + 0.5) / sg if alt == "inferieur" else math.copysign(max(0.0, abs(u1 - mu) - 0.5) / sg, u1 - mu)
    exact = N <= LIMITES["exact_rangs"]
    p_val = _p_rangs_exact(r, n1, float(r[:n1].sum()), alt) if exact else _p_z(z, alt)
    return _sortie(_i18n_t("engine/statistiques.py:g:1060:20") + (_i18n_t("engine/statistiques.py:g:1060:46") if exact else ""), _i18n_t("engine/statistiques.py:g:1060:86"), tf("engine/statistiques.py:g:1062:175", v1=_i18n_t("rg.differentes") if alt == 'different' else _i18n_t("engine/statistiques.py:g:1063:259") if alt == 'superieur' else _i18n_t("engine/statistiques.py:g:1063:301")),
                   _i18n_t("rg.u_premier_groupe"), u1, None, p_val, alpha, (tf("engine/statistiques.py:g:1061:63", v1=_fmt(float(np.median(x))), v2=_fmt(float(np.median(y)))), _i18n_t("engine/statistiques.py:g:1061:160")),
                   groupes=[{"nom": nx, **_resume(x)}, {"nom": ny, **_resume(y)}], effet={"nom": _i18n_t("engine/statistiques.py:g:1064:98"), "valeur": u1 / (n1 * n2)},
                   avertissements=[] if exact or min(n1, n2) >= 8 else [_i18n_t("engine/statistiques.py:g:1065:73")])


def _test_wilcoxon(v, p, alpha, alt):
    a, b = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1067:29")), _var(v.get("b"), _i18n_t("engine/statistiques.py:g:1067:69"))
    x, y = _paires(a, b)
    d = (x - y)[(x - y) != 0]
    n = d.size
    if n < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1072:26"))
    r = _rangs(np.abs(d))
    w = float(r[d > 0].sum())
    mu = n * (n + 1) / 4
    s2 = n * (n + 1) * (2 * n + 1) / 24 - _corr_ex(np.abs(d), None) / 48
    sg = math.sqrt(s2)
    z = (w - mu - 0.5) / sg if alt == "superieur" else (w - mu + 0.5) / sg if alt == "inferieur" else math.copysign(max(0.0, abs(w - mu) - 0.5) / sg, w - mu)
    exact = n <= LIMITES["exact_rangs"]
    p_val = _p_rangs_exact(r, None, w, alt) if exact else _p_z(z, alt)
    return _sortie(_i18n_t("engine/statistiques.py:g:1081:20") + (_i18n_t("engine/statistiques.py:g:1081:57") if exact else ""), _i18n_t("engine/statistiques.py:g:1081:97"), tf("engine/statistiques.py:g:1081:142", v1=_ALT[alt]), "W+", w, None, p_val, alpha,
                   (_i18n_t("engine/statistiques.py:g:1082:21"), _i18n_t("engine/statistiques.py:g:1082:52")), groupes=[{"nom": a["nom"], **_resume(x)}, {"nom": b["nom"], **_resume(y)}],
                   avertissements=[] if exact or n >= 10 else [_i18n_t("engine/statistiques.py:g:1085:64")])


def _croiser(a, b):
    lignes = [(etiquette(u), etiquette(w)) for u, w in zip(a["valeurs"], b["valeurs"])]
    lignes = [(u, w) for u, w in lignes if u is not None and w is not None]
    ma, mb = sorted({u for u, _ in lignes}), sorted({w for _, w in lignes})
    if len(ma) < 2 or len(mb) < 2:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1091:26"))
    if len(ma) > LIMITES["modalites_croisees"] or len(mb) > LIMITES["modalites_croisees"]:
        raise ValueError(tf("engine/statistiques.py:g:1093:26", v1=LIMITES['modalites_croisees']))
    cpt = Counter(lignes)
    return ma, mb, np.array([[cpt[(u, w)] for w in mb] for u in ma], dtype=float)


def _test_khi2_indep(v, p, alpha, alt):
    a, b = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1099:29")), _var(v.get("b"), _i18n_t("engine/statistiques.py:g:1099:71"))
    ma, mb, t = _croiser(a, b)
    n = t.sum()
    e = np.outer(t.sum(1), t.sum(0)) / n
    khi2, cv, ddl = _cramer_v(t)
    avis = []
    if (e < 5).mean() > 0.2 or e.min() < 1:
        avis.append(_i18n_t("engine/statistiques.py:g:1106:21") + (_i18n_t("engine/statistiques.py:g:1106:108") if t.shape == (2, 2) else "."))
    return _sortie(tf("engine/statistiques.py:g:1107:20", v1=a['nom'], v2=b['nom']), _i18n_t("engine/statistiques.py:g:1107:74"), _i18n_t("engine/statistiques.py:g:1107:115"), "χ²", khi2, ddl, L.sf("khi2", {"k": float(ddl)}, khi2), alpha,
                   (tf("engine/statistiques.py:g:1108:21", v1=_fmt(cv, 3), v2=_force(cv)), _i18n_t("engine/statistiques.py:g:1108:105")),
                   effet={"nom": _i18n_t("engine/statistiques.py:g:1111:34"), "valeur": cv}, contingence={"lignes": ma, "colonnes": mb, "observes": t.tolist(), "attendus": e.tolist(), "residus": ((t - e) / np.sqrt(np.where(e > 0, e, 1))).tolist()}, avertissements=avis)


def _test_khi2_adeq(v, p, alpha, alt):
    a = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1113:26"))
    etiq = [e for e in (etiquette(u) for u in a["valeurs"]) if e is not None]
    cpt = Counter(etiq)
    mods = sorted(cpt)
    if len(mods) < 2:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1118:26"))
    texte = str(p.get("attendues") or "").strip()
    if texte:
        att = {}
        for jeton in texte.replace(";", ",").split(","):
            nom, _s, val = jeton.partition("=")
            try:
                att[nom.strip()] = float(val.replace(",", ".").replace("%", ""))
            except ValueError:
                raise ValueError(tf("engine/statistiques.py:g:1127:34", v1=jeton.strip()))
        inconnues = [m for m in att if m not in cpt]
        if inconnues:
            raise ValueError(tf("engine/statistiques.py:g:1130:30", v1=', '.join(inconnues[:5])))
        if any(m not in att for m in mods):
            raise ValueError(_i18n_t("engine/statistiques.py:g:1132:30") + ", ".join(mods[:10]) + ".")
        tot = sum(att.values())
        if tot <= 0:
            raise ValueError(_i18n_t("engine/statistiques.py:g:1135:30"))
        props = [att[m] / tot for m in mods]
    else:
        props = [1 / len(mods)] * len(mods)
    n = len(etiq)
    obs = np.array([cpt[m] for m in mods], dtype=float)
    att = np.array(props) * n
    khi2 = float(((obs - att) ** 2 / att).sum())
    ddl = len(mods) - 1
    return _sortie(tf("engine/statistiques.py:g:1144:20", v1=a['nom']), _i18n_t("engine/statistiques.py:g:1144:66"), _i18n_t("engine/statistiques.py:g:1144:122"), "χ²", khi2, ddl,
                   L.sf("khi2", {"k": float(ddl)}, khi2), alpha, (_i18n_t("engine/statistiques.py:g:1145:67"), _i18n_t("engine/statistiques.py:g:1145:121")),
                   tableau=[{"modalite": m, "observe": float(o), "attendu": float(e), "residu": float((o - e) / math.sqrt(e))} for m, o, e in zip(mods, obs, att)],
                   avertissements=[] if att.min() >= 5 else [_i18n_t("engine/statistiques.py:g:1149:62")])


def _test_fisher(v, p, alpha, alt):
    a, b = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1151:29")), _var(v.get("b"), _i18n_t("engine/statistiques.py:g:1151:71"))
    ma, mb, t = _croiser(a, b)
    if t.shape != (2, 2):
        raise ValueError(_i18n_t("engine/statistiques.py:g:1154:26"))
    (x, y), (z, w) = t.astype(int).tolist()
    N, K, n = x + y + z + w, x + y, x + z
    par = {"N": N, "K": K, "n": n}
    obs = L.pdf("hypergeometrique", par, x)
    lo, hi = L._sup_hyper(par)
    if alt == "superieur":
        pv = L.sf("hypergeometrique", par, x - 1)
    elif alt == "inferieur":
        pv = L.cdf("hypergeometrique", par, x)
    else:
        pv = min(1.0, sum(q for q in (L.pdf("hypergeometrique", par, j) for j in range(lo, hi + 1)) if q <= obs * (1 + 1e-7)))
    odds = (x * w) / (y * z) if y * z else math.inf
    return _sortie(tf("engine/statistiques.py:g:1167:20", v1=a['nom'], v2=b['nom']), _i18n_t("engine/statistiques.py:g:1167:71"), tf("engine/statistiques.py:g:1167:135", v1=_ALT[alt]), _i18n_t("engine/statistiques.py:g:1167:170"), odds, None, pv, alpha,
                   (_i18n_t("engine/statistiques.py:g:1168:21"), _i18n_t("engine/statistiques.py:g:1168:55")), contingence={"lignes": ma, "colonnes": mb, "observes": t.tolist()})


def _test_correlation(v, p, alpha, alt):
    a, b = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1172:29")), _var(v.get("b"), _i18n_t("engine/statistiques.py:g:1172:71"))
    x, y = _paires(a, b)
    n = x.size
    if n < 3:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1176:26"))
    spearman = str(p.get("methode") or "pearson") == "spearman"
    xx, yy = (_rangs(x), _rangs(y)) if spearman else (x, y)
    r = _pearson(xx, yy)
    if r is None:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1181:26"))
    t = r * math.sqrt((n - 2) / (1 - r * r)) if abs(r) < 1 else math.copysign(1e9, r)
    pv = _p_t(t, n - 2, alt)
    ic = None
    if n > 3 and abs(r) < 1:
        zz = math.atanh(r)
        h = _ND.inv_cdf(1 - alpha / 2) / math.sqrt(n - 3)
        ic = {"libelle": tf("engine/statistiques.py:g:1188:26", v1=_fmt((1 - alpha) * 100)), "bas": math.tanh(zz - h), "haut": math.tanh(zz + h)}
    pas = max(1, n // LIMITES["points_graphique"])
    pente = float(np.polyfit(x, y, 1)[0]) if x.std() > 0 else None
    return _sortie(tf("engine/statistiques.py:g:1191:20", v1='Spearman' if spearman else 'Pearson', v2=a['nom'], v3=b['nom']), _i18n_t("engine/statistiques.py:g:1191:105"), tf("engine/statistiques.py:g:1191:133", v1=_ALT[alt]), "t", t, n - 2, pv, alpha,
                   (tf("engine/statistiques.py:g:1192:21", v1='rho' if spearman else 'r', v2=_fmt(r, 3), v3=_force(r), v4=_i18n_t("rg.positif") if r > 0 else _i18n_t("rg.negatif")), tf("engine/statistiques.py:g:1192:160", v1='rho' if spearman else 'r', v2=_fmt(r, 3))),
                   estimation={"coefficient": r, "n": n, "r2": r * r, "pente": pente, "ordonnee": float(y.mean() - pente * x.mean()) if pente is not None else None}, intervalle=ic,
                   nuage={"x": x[::pas].tolist(), "y": y[::pas].tolist(), "nom_x": a["nom"], "nom_y": b["nom"]},
                   avertissements=([_i18n_t("engine/statistiques.py:g:1197:37")] if spearman and n < 10 else []) + ([_i18n_t("engine/statistiques.py:g:1197:147")]))


def _indicatrices(v):
    """Colonnes 0/1 d'une variable qualitative (la modalité la plus fréquente sert de référence) → (noms, colonnes, référence)."""
    etiq = [etiquette(u) for u in v["valeurs"]]
    cpt = Counter(e for e in etiq if e is not None)
    if len(cpt) < 2:
        return [], [], None
    if len(cpt) > 10:
        raise ValueError(tf("engine/statistiques.py:g:1205:26", v1=v['nom'], v2=len(cpt)))
    ref = cpt.most_common(1)[0][0]
    mods = [m for m in sorted(cpt) if m != ref]
    return [f"{v['nom']} = {m}" for m in mods], [[None if e is None else (1.0 if e == m else 0.0) for e in etiq] for m in mods], ref


def _test_regression(v, p, alpha, alt):
    y_var = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1212:30"))
    expl = v.get("explicatives") or []
    if not expl:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1215:26"))
    if len(expl) > 10:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1217:26"))
    noms, cols, refs = [], [], []
    for e in expl:
        if _nombres(e["valeurs"]).size >= 0.9 * max(1, sum(1 for u in e["valeurs"] if u is not None and u != "")) and e.get("genre", "quanti_continue").startswith("quanti"):
            noms.append(e["nom"])
            cols.append([_reel(u) for u in e["valeurs"]])
        else:
            n2, c2, ref = _indicatrices(e)
            noms += n2
            cols += c2
            if ref is not None:
                refs.append(tf("engine/statistiques.py:g:1228:29", v1=e['nom'], v2=ref))
    if not cols:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1230:26"))
    yv = [_reel(u) for u in y_var["valeurs"]]
    n_tot = len(yv)
    if any(len(c) != n_tot for c in cols):
        raise ValueError(_i18n_t("engine/statistiques.py:g:1234:26"))
    garde = [i for i in range(n_tot) if yv[i] is not None and all(c[i] is not None for c in cols)]
    n, k = len(garde), len(cols)
    if n < k + 3:
        raise ValueError(tf("engine/statistiques.py:g:1238:26", v1=n, v2=k, v3=k + 3))
    y = np.array([yv[i] for i in garde])
    X = np.column_stack([np.ones(n)] + [np.array([c[i] for i in garde]) for c in cols])
    if np.linalg.matrix_rank(X) < X.shape[1]:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1242:26"))
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    fit = X @ beta
    res = y - fit
    sse, sst = float((res ** 2).sum()), float(((y - y.mean()) ** 2).sum())
    if sst <= 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1248:26"))
    dfe = n - k - 1
    s2 = sse / dfe
    cov = s2 * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    tcrit = _t_crit(dfe, 1 - alpha)
    coefs = []
    for i, nom in enumerate(["Constante"] + noms):
        t = float(beta[i] / se[i]) if se[i] > 0 else 0.0
        coefs.append({"nom": nom, "coef": float(beta[i]), "se": float(se[i]), "t": t, "p": _p_t(t, dfe, "different"), "bas": float(beta[i] - tcrit * se[i]), "haut": float(beta[i] + tcrit * se[i])})
    r2 = 1 - sse / sst
    f = (r2 / k) / ((1 - r2) / dfe) if r2 < 1 else math.inf
    pf = L.sf("fisher", {"d1": float(k), "d2": float(dfe)}, f) if math.isfinite(f) else 0.0
    dw = float((np.diff(res) ** 2).sum() / (res ** 2).sum()) if sse > 0 else None
    nm = _normalite_dagostino(np.sort(res))
    avis = list(refs)
    if n < 10 * k:
        avis.append(_i18n_t("engine/statistiques.py:g:1265:21"))
    if nm and nm[1] < 0.05:
        avis.append(_i18n_t("engine/statistiques.py:g:1267:21"))
    if dw is not None and (dw < 1.0 or dw > 3.0):
        avis.append(tf("engine/statistiques.py:g:1269:21", v1=_fmt(dw, 3)))
    pas = max(1, n // LIMITES["points_graphique"])
    eq = f"{y_var['nom']} = {_fmt(beta[0], 5)}" + "".join(f" {'+' if b >= 0 else '−'} {_fmt(abs(b), 5)} × {nom}" for b, nom in zip(beta[1:], noms))
    return _sortie(tf("engine/statistiques.py:g:1272:20", v1=y_var['nom']), _i18n_t("engine/statistiques.py:g:1272:61"), _i18n_t("engine/statistiques.py:g:1272:157"), "F", f, f"{k} ; {dfe}", pf, alpha,
                   (tf("engine/statistiques.py:g:1273:21", v1=_fmt(r2 * 100, 3), v2=y_var['nom']), tf("engine/statistiques.py:g:1273:105", v1=y_var['nom'])),
                   coefficients=coefs, equation=eq, estimation={"n": n, "r2": r2, "r2_ajuste": 1 - (1 - r2) * (n - 1) / dfe, "erreur_type_residuelle": math.sqrt(s2), "durbin_watson": dw},
                   nuage={"x": fit[::pas].tolist(), "y": y[::pas].tolist(), "nom_x": _i18n_t("engine/statistiques.py:g:1277:86"), "nom_y": y_var["nom"]}, avertissements=avis)


def _test_normalite(v, p, alpha, alt):
    a = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1279:26"))
    x = _quant(a)
    s = np.sort(x)
    nm = _normalite_dagostino(s)
    if nm is None:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1284:26"))
    return _sortie(tf("engine/statistiques.py:g:1285:20", v1=a['nom']), _i18n_t("engine/statistiques.py:g:1285:47"), _i18n_t("engine/statistiques.py:g:1285:86"), "K²" if nm[2] == 2 else _i18n_t("engine/statistiques.py:g:1285:156"), nm[0], nm[2], nm[1], alpha,
                   (_i18n_t("engine/statistiques.py:g:1286:21"), _i18n_t("engine/statistiques.py:g:1286:105")), qq=_qq(s),
                   estimation={"n": int(s.size), "moyenne": float(s.mean()), "ecart_type": float(s.std(ddof=1))},
                   avertissements=([_i18n_t("engine/statistiques.py:g:1290:37")] if nm[2] == 1 else []) + ([_i18n_t("engine/statistiques.py:g:1290:119")] if s.size > 500 else []))


def _test_bootstrap(v, p, alpha, alt):
    a = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1292:26"))
    x = _quant(a)
    if x.size < 5:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1295:26"))
    conf = _param(p, "confiance", 0.95)
    B = int(min(5000, max(200, _param(p, "repetitions", 2000))))
    if not 0.5 <= conf < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1299:26"))
    stat = str(p.get("stat") or "moyenne")
    fn = {"moyenne": lambda m: m.mean(1), "mediane": lambda m: np.median(m, 1), "ecart_type": lambda m: m.std(1, ddof=1)}.get(stat)
    if fn is None:
        raise ValueError(_gt("rg8.msg.024"))
    rng = np.random.default_rng(int(_param(p, "graine", 12345)))
    dist = np.sort(fn(x[rng.integers(0, x.size, size=(B, x.size))]))
    est = float(fn(x[None, :])[0])
    bas, haut = float(np.quantile(dist, (1 - conf) / 2)), float(np.quantile(dist, 1 - (1 - conf) / 2))
    eff, bornes = np.histogram(dist, bins=30)
    return {"test": tf("engine/statistiques.py:g:1309:21", v1=stat.replace('_', ' '), v2=a['nom']), "h0": None, "h1": None, "statistique": {"nom": stat.replace("_", " "), "valeur": est}, "ddl": None, "p": None, "alpha": None, "significatif": None,
            "conclusion": tf("engine/statistiques.py:g:1310:27", v1=_fmt(conf * 100), v2=_fmt(bas), v3=_fmt(haut), v4=_fmt(est)),
            "intervalle": {"libelle": tf("engine/statistiques.py:g:1311:39", v1=_fmt(conf * 100), v2=B), "bas": bas, "haut": haut}, "estimation": {"n": int(x.size), "erreur_standard": float(dist.std(ddof=1))},
            "histogramme": {"bornes": bornes.tolist(), "effectifs": eff.tolist()}, "avertissements": []}


def _outil(nom, n, phrase, **extra):
    return {"test": nom, "h0": None, "h1": None, "statistique": {"nom": "taille n", "valeur": float(n)}, "ddl": None, "p": None, "alpha": None, "significatif": None, "conclusion": phrase, "avertissements": [], **extra}


def _test_taille_moy(v, p, alpha, alt):
    sg, marge, conf = _param(p, "sigma", 1.0), _param(p, "marge", 0.1), _param(p, "confiance", 0.95)
    if sg <= 0 or marge <= 0 or not 0.5 <= conf < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1322:26"))
    n = math.ceil((_ND.inv_cdf(1 - (1 - conf) / 2) * sg / marge) ** 2)
    return _outil(_i18n_t("engine/statistiques.py:g:1324:19"), n, tf("engine/statistiques.py:g:1324:56", v1=n, v2=_fmt(marge), v3=_fmt(conf * 100), v4=_fmt(sg)))


def _test_taille_prop(v, p, alpha, alt):
    pr, marge, conf = _param(p, "p", 0.5), _param(p, "marge", 0.05), _param(p, "confiance", 0.95)
    if not 0 < pr < 1 or not 0 < marge < 1 or not 0.5 <= conf < 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1330:26"))
    n = math.ceil(_ND.inv_cdf(1 - (1 - conf) / 2) ** 2 * pr * (1 - pr) / marge ** 2)
    return _outil(_i18n_t("engine/statistiques.py:g:1332:19"), n, tf("engine/statistiques.py:g:1332:59", v1=n, v2=_fmt(pr * 100), v3=_fmt(marge * 100), v4=_fmt(conf * 100)))


def _test_auto(v, p, alpha, alt):
    a, b, g = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1336:32")), v.get("b"), v.get("groupe")
    if b and g:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1338:26"))
    autre = b or g
    qa = est_quanti(a.get("genre", "")) or _nombres(a["valeurs"]).size >= 0.9 * max(1, sum(1 for u in a["valeurs"] if u is not None))
    if autre:
        qb = est_quanti(autre.get("genre", ""))
        if b and qa and qb and len(a["valeurs"]) != len(b["valeurs"]):   # deux tables de tailles différentes : échantillons indépendants
            x, y = _quant(a), _quant(b)
            cle = "t2" if all(t.size >= 30 or _normal_ok(t) for t in (x, y)) else "mannwhitney"
            r = _DISPATCH[cle]({"a": a, "b": b}, p, alpha, alt)
            r["choix_auto"] = {"test": cle, "nom": _CAT[cle]["nom"], "raison": td("engine/statistiques.py:g:1349:80") + ("moyennes (Welch)." if cle == "t2" else td("engine/statistiques.py:g:1349:244"))}
            return r
        if not qa and qb:
            a, autre, qa, qb = autre, a, True, False
        w = {"a": a}
        if qa and qb:
            x, y = _paires(a, autre)
            ok = all(_normal_ok(t) is not False and (t.size >= 30 or _normal_ok(t)) for t in (x, y))
            cle = "correlation"
            p = {**p, "methode": "pearson" if ok else "spearman"}
            raison = _i18n_t("engine/statistiques.py:g:1357:22") + (_i18n_t("engine/statistiques.py:g:1357:82") if ok else _i18n_t("engine/statistiques.py:g:1357:161"))
            w["b"] = autre
        elif qa:
            w["groupe"] = autre
            grp = _groupes(a, autre)
            k = len(grp)
            if k < 2:
                raise ValueError(tf("engine/statistiques.py:g:1364:34", v1=autre['nom']))
            ok = all(x.size >= 30 or _normal_ok(x) for x in grp.values())
            if k == 2:
                cle = "t2" if ok else "mannwhitney"
                raison = tf("engine/statistiques.py:g:1370:26", v1=_i18n_t("engine/statistiques.py:g:1371:68") if ok else _i18n_t("engine/statistiques.py:g:1371:155"))
            else:
                cle = "anova" if ok else "kruskal"
                raison = tf("engine/statistiques.py:g:1373:26", v1=k, v2=_i18n_t("engine/statistiques.py:g:1374:74") if ok else _i18n_t("engine/statistiques.py:g:1374:135"))
        else:
            w["b"] = autre
            cle = "khi2_indep"
            try:
                _ma, _mb, t = _croiser(a, autre)
                if t.shape == (2, 2) and (np.outer(t.sum(1), t.sum(0)) / t.sum()).min() < 5:
                    cle = "fisher"
            except ValueError:
                pass
            raison = _i18n_t("engine/statistiques.py:g:1381:22") + (_i18n_t("engine/statistiques.py:g:1381:77") if cle == "fisher" else _i18n_t("engine/statistiques.py:g:1381:166"))
    else:
        w = {"a": a}
        cle = "normalite" if qa else "khi2_adeq"
        raison = _i18n_t("engine/statistiques.py:g:1385:18") if qa else _i18n_t("engine/statistiques.py:g:1385:136")
    r = _DISPATCH[cle](w, p, alpha, alt)
    r["choix_auto"] = {"test": cle, "nom": _CAT[cle]["nom"], "raison": raison}
    return r


# ── ANOVA à deux facteurs, régression polynomiale, régression logistique ─────
def _colonnes_explicatives(expl):
    """(noms, colonnes, références) : variables numériques telles quelles, qualitatives en indicatrices 0/1."""
    noms, cols, refs = [], [], []
    for e in expl:
        if _nombres(e["valeurs"]).size >= 0.9 * max(1, sum(1 for u in e["valeurs"] if u is not None and u != "")) and e.get("genre", "quanti_continue").startswith("quanti"):
            noms.append(e["nom"])
            cols.append([_reel(u) for u in e["valeurs"]])
        else:
            n2, c2, ref = _indicatrices(e)
            noms += n2
            cols += c2
            if ref is not None:
                refs.append(tf("engine/statistiques.py:g:1404:29", v1=e['nom'], v2=ref))
    return noms, cols, refs


def _sse_modele(y, cols):
    """(somme des carrés résiduelle, rang) du modèle constante + colonnes."""
    X = np.column_stack([np.ones(y.size)] + list(cols)) if cols else np.ones((y.size, 1))
    beta, _, rang, _ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    return float(res @ res), int(rang)


_NB_FACTEURS = {2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six", 7: "sept", 8: "huit", 9: "neuf", 10: "dix"}
_ANOVA_MAX_FACTEURS, _ANOVA_MAX_MODALITES, _ANOVA_BUDGET, _ANOVA_MAX_CELLULES_AFFICHEES = 10, 100, 5e10, 300


def _anova_plan(a, facteurs, ordre, alpha, etiquettes=None):
    """ANOVA à k facteurs (2 à 10), interactions jusqu'à l'ordre `ordre`, sommes des carrés de type II (plans déséquilibrés admis).
    Pour chaque terme T : SS(T) = SCR(termes qui ne contiennent pas T) − SCR(ces termes + T). Moteur unique de « anova2 » et « anovan »."""
    k = len(facteurs)
    if not 2 <= k <= _ANOVA_MAX_FACTEURS:
        raise ValueError(tf("engine/statistiques.py:g:1425:26", v1=_NB_FACTEURS[_ANOVA_MAX_FACTEURS]))
    if len({len(f["valeurs"]) for f in facteurs} | {len(a["valeurs"])}) > 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1427:26"))
    gardees = []
    for lig in zip(a["valeurs"], *[f["valeurs"] for f in facteurs]):
        u, mods = _reel(lig[0]), [etiquette(m) for m in lig[1:]]
        if u is not None and None not in mods:
            gardees.append((u, *mods))
    y = np.array([g[0] for g in gardees], dtype=float)
    niveaux = [[g[1 + i] for g in gardees] for i in range(k)]
    mods = [sorted(set(col)) for col in niveaux]
    if any(len(m) < 2 for m in mods):
        raise ValueError(_i18n_t("engine/statistiques.py:g:1437:26"))
    if any(len(m) > _ANOVA_MAX_MODALITES for m in mods):
        raise ValueError(tf("engine/statistiques.py:g:1439:26", v1=_ANOVA_MAX_MODALITES))
    ordre = max(1, min(int(ordre), k))
    noms = [f["nom"] for f in facteurs]
    etiq = etiquettes or noms
    # Le calcul porte sur les CELLULES (combinaisons de modalités observées), pas sur les lignes : pour un modèle linéaire de facteurs,
    # SCR(modèle) = variation à l'intérieur des cellules + SCR des moyennes de cellules pondérées par leurs effectifs. Coût indépendant de n.
    n = y.size
    codes = np.zeros(n, dtype=np.int64)
    for i in range(k):
        rang_mod = {m: j for j, m in enumerate(mods[i])}
        codes = codes * len(mods[i]) + np.array([rang_mod[m] for m in niveaux[i]], dtype=np.int64)
    uniques, inverse = np.unique(codes, return_inverse=True)
    m_cel = uniques.size
    eff = np.bincount(inverse, minlength=m_cel).astype(float)
    moy = np.bincount(inverse, weights=y, minlength=m_cel) / eff
    ssw = float(((y - moy[inverse]) ** 2).sum())
    niv_cel, reste = [], uniques.copy()
    for i in range(k - 1, -1, -1):
        niv_cel.insert(0, reste % len(mods[i]))
        reste //= len(mods[i])
    sw = np.sqrt(eff)
    ind = [[(niv_cel[i] == j).astype(float) for j in range(1, len(mods[i]))] for i in range(k)]
    sst = float(((y - y.mean()) ** 2).sum())
    larg = [len(m) - 1 for m in mods]

    def termes_ordre(o):
        return [c for r in range(1, o + 1) for c in itertools.combinations(range(k), r)]

    def colonnes_modele(o):
        return 1 + sum(math.prod(larg[i] for i in t) for t in termes_ordre(o))

    def cols(ts):
        return [c for t in ts for c in colonnes[t]]

    def sse_modele(ts):
        X = np.column_stack([np.ones(m_cel)] + cols(ts)) * sw[:, None]
        beta, _, rang, _ = np.linalg.lstsq(X, moy * sw, rcond=None)
        res = moy * sw - X @ beta
        return ssw + float(res @ res), int(rang)
    # Repli automatique : si le plan demandé est trop lourd ou saturé, on retire les interactions d'ordre le plus élevé, une par une, jusqu'à ce que le calcul soit possible.
    ordre_demande, retraits, termes = ordre, [], []
    for o in range(ordre_demande, 0, -1):
        termes = termes_ordre(o)
        p_tot = colonnes_modele(o)
        if m_cel * p_tot > 2e7 or 2 * len(termes) * m_cel * p_tot ** 2 > _ANOVA_BUDGET:
            retraits.append((o, "lourd", tf("engine/statistiques.py:g:1484:42", v1=len(termes), v2=p_tot - 1, v3=m_cel)))
            continue
        colonnes = {t: [functools.reduce(np.multiply, combi) for combi in itertools.product(*[ind[i] for i in t])] for t in termes}
        sse_f, r_f = sse_modele(termes)
        dfe = n - r_f
        if dfe <= 0 or sse_f <= 1e-12 * max(sst, 1.0):
            retraits.append((o, "sature", _i18n_t("engine/statistiques.py:g:1490:43")))
            continue
        ordre = o
        break
    else:
        if retraits[-1][1] == "lourd":
            raise ValueError(tf("engine/statistiques.py:g:1496:30", v1=retraits[-1][2]))
        raise ValueError(_i18n_t("engine/statistiques.py:g:1497:26"))
    mse = sse_f / dfe
    tableau, ps = [], {}
    for t in termes:
        sans = [u for u in termes if not set(t) <= set(u)]
        sse_a, r_a = sse_modele(sans)
        sse_b, r_b = sse_modele(sans + [t])
        nom = etiq[t[0]] if len(t) == 1 else "Interaction " + " × ".join(noms[i] for i in t)
        ss, ddl = max(0.0, sse_a - sse_b), r_b - r_a
        if ddl <= 0:
            tableau.append({"source": nom, "ss": ss, "ddl": ddl})
            continue
        f = (ss / ddl) / mse
        ps[t] = L.sf("fisher", {"d1": float(ddl), "d2": float(dfe)}, f)
        tableau.append({"source": nom, "ss": ss, "ddl": ddl, "ms": ss / ddl, "f": f, "p": ps[t]})
    tableau += [{"source": td("engine/statistiques.py:g:1513:28"), "ss": sse_f, "ddl": dfe, "ms": mse}]
    ps_regroupe = None
    if retraits and n > m_cel:
        # Les interactions d'ordre supérieur retirées sont testées en bloc : le modèle complet (une moyenne par cellule) a pour SCR exacte ssw (variation à l'intérieur des cellules),
        # donc SS(bloc) = SCR(modèle retenu) − ssw, ddl = cellules − rang du modèle retenu, contre l'erreur pure ssw / (n − cellules).
        ddl_b, dfe_p = m_cel - r_f, n - m_cel
        if ddl_b > 0 and ssw > 1e-12 * max(sst, 1.0):
            ss_b = max(0.0, sse_f - ssw)
            f_b = (ss_b / ddl_b) / (ssw / dfe_p)
            ps_regroupe = L.sf("fisher", {"d1": float(ddl_b), "d2": float(dfe_p)}, f_b)
            tableau.append({"source": tf("engine/statistiques.py:g:1524:39#2", v1=ordre), "ss": ss_b, "ddl": ddl_b, "ms": ss_b / ddl_b, "f": f_b, "p": ps_regroupe})
            tableau.append({"source": td("engine/statistiques.py:g:1524:39"), "ss": ssw, "ddl": dfe_p, "ms": ssw / dfe_p})
    tableau += [{"source": _gt("engine/statistiques.py:1679:28"), "ss": sst, "ddl": n - 1}]
    d_mod = r_f - 1
    ss_mod = max(0.0, sst - sse_f)
    f_glob = (ss_mod / d_mod) / mse
    p_glob = L.sf("fisher", {"d1": float(d_mod), "d2": float(dfe)}, f_glob)
    cellules = {}
    for g in gardees:
        cellules.setdefault(" × ".join(g[1:]), []).append(g[0])
    cell = {c: np.array(x) for c, x in sorted(cellules.items())}
    avis = []
    if retraits:
        o_r, _, raison = retraits[-1]       # le dernier ordre refusé est le plus informatif
        avis.append(tf("engine/statistiques.py:g:1536:21", v1=ordre, v2=ordre_demande, v3=o_r, v4=raison) + (_i18n_t("engine/statistiques.py:g:1536:179") + _p_fmt(ps_regroupe) + ")" + (_i18n_t("engine/statistiques.py:g:1536:298") if ps_regroupe < alpha else _i18n_t("engine/statistiques.py:g:1536:450")) if ps_regroupe is not None else _i18n_t("engine/statistiques.py:g:1536:520")))
    if len({x.size for x in cell.values()}) > 1:
        avis.append(_i18n_t("engine/statistiques.py:g:1538:21"))
    if len(cell) < math.prod(len(m) for m in mods):
        avis.append(_i18n_t("engine/statistiques.py:g:1540:21"))
    signif = [t for t in termes if len(t) > 1 and ps.get(t, 1.0) < alpha]
    if signif:
        avis.append("Interaction significative (" + ", ".join(" × ".join(noms[i] for i in t) for t in signif[:4]) + _i18n_t("engine/statistiques.py:g:1543:117"))
    if len(cell) > _ANOVA_MAX_CELLULES_AFFICHEES:
        avis.append(tf("engine/statistiques.py:g:1545:21", v1=len(cell), v2=_ANOVA_MAX_CELLULES_AFFICHEES))
    gl = {c: x for c, x in cell.items() if x.size >= 2}
    if len(gl) >= 2 and _levene(gl)[1] < 0.05:
        avis.append(_i18n_t("engine/statistiques.py:g:1548:21"))
    sujet = f"{a['nom']} selon " + ", ".join(noms[:-1]) + f" et {noms[-1]}"
    return _sortie(tf("engine/statistiques.py:g:1550:20", v1=_NB_FACTEURS[k], v2=sujet), _i18n_t("engine/statistiques.py:g:1550:69") + (_i18n_t("engine/statistiques.py:g:1550:116") if ordre > 1 else ""),
                   _i18n_t("engine/statistiques.py:g:1551:20") + (_i18n_t("engine/statistiques.py:g:1551:45") if ordre > 1 else "") + _i18n_t("engine/statistiques.py:g:1551:93"), "F", f_glob, f"{d_mod} ; {dfe}", p_glob, alpha,
                   (_i18n_t("engine/statistiques.py:g:1552:21"), _i18n_t("engine/statistiques.py:g:1552:128")),
                   groupes=[{"nom": c, **_resume(x)} for c, x in cell.items()] if len(cell) <= _ANOVA_MAX_CELLULES_AFFICHEES else [], effet={"nom": _i18n_t("engine/statistiques.py:g:1555:149"), "valeur": ss_mod / sst if sst > 0 else None},
                   tableau_anova=tableau, avertissements=avis)


def _test_anova2(v, p, alpha, alt):
    a = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1558:26"))
    f1, f2 = _var(v.get("groupe"), _i18n_t("engine/statistiques.py:g:1559:36")), _var(v.get("groupe2"), _i18n_t("engine/statistiques.py:g:1559:82"))
    return _anova_plan(a, [f1, f2], 2 if str(p.get("interaction", "1")) == "1" else 1, alpha, [f"{f1['nom']} (facteur 1)", f"{f2['nom']} (facteur 2)"])


def _test_anovan(v, p, alpha, alt):
    return _anova_plan(_var(v.get("a"), _i18n_t("engine/statistiques.py:g:1564:41")), _var(v.get("explicatives"), _i18n_t("engine/statistiques.py:g:1564:95")), _param(p, "ordre", 2, int), alpha)


def _mco(y, X, noms, alpha):
    """Moindres carrés ordinaires : coefficients, erreurs standard, IC, R², F global."""
    n, k1 = X.shape
    k = k1 - 1
    if np.linalg.matrix_rank(X) < k1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1572:26"))
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    fit = X @ beta
    res = y - fit
    sse, sst = float(res @ res), float(((y - y.mean()) ** 2).sum())
    if sst <= 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1578:26"))
    dfe = n - k1
    s2 = sse / dfe
    se = np.sqrt(np.diag(s2 * np.linalg.inv(X.T @ X)))
    tcrit = _t_crit(dfe, 1 - alpha)
    coefs = []
    for i, nom in enumerate(["Constante"] + noms):
        t = float(beta[i] / se[i]) if se[i] > 0 else 0.0
        coefs.append({"nom": nom, "coef": float(beta[i]), "se": float(se[i]), "t": t, "p": _p_t(t, dfe, "different"), "bas": float(beta[i] - tcrit * se[i]), "haut": float(beta[i] + tcrit * se[i])})
    r2 = 1 - sse / sst
    f = (r2 / k) / ((1 - r2) / dfe) if r2 < 1 else math.inf
    pf = L.sf("fisher", {"d1": float(k), "d2": float(dfe)}, f) if math.isfinite(f) else 0.0
    return beta, fit, res, coefs, r2, f, pf, dfe, s2


def _test_polynomiale(v, p, alpha, alt):
    y_var, x_var = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1594:37")), _var(v.get("b"), _i18n_t("engine/statistiques.py:g:1594:82"))
    degre = int(_param(p, "degre", 2))
    if not 2 <= degre <= 8:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1597:26"))
    if len(y_var["valeurs"]) != len(x_var["valeurs"]):
        raise ValueError(_i18n_t("engine/statistiques.py:g:1599:26"))
    paires = [(_reel(a), _reel(b)) for a, b in zip(y_var["valeurs"], x_var["valeurs"])]
    paires = [(a, b) for a, b in paires if a is not None and b is not None]
    n = len(paires)
    if n < degre + 3:
        raise ValueError(tf("engine/statistiques.py:g:1604:26", v1=n, v2=degre, v3=degre + 3))
    y, x = np.array([a for a, _ in paires]), np.array([b for _, b in paires])
    if np.unique(x).size <= degre:
        raise ValueError(tf("engine/statistiques.py:g:1607:26", v1=x_var['nom'], v2=degre))
    xc = x - x.mean()
    X = np.column_stack([xc ** j for j in range(degre + 1)])
    noms = [td("engine/statistiques.py:g:1611:13") if j == 1 else tf("engine/statistiques.py:g:1612:67", v1=j) for j in range(1, degre + 1)]
    beta, fit, res, coefs, r2, f, pf, dfe, s2 = _mco(y, X, noms, alpha)
    ordre = np.argsort(x)
    pas = max(1, n // LIMITES["points_graphique"])
    grille = np.linspace(x.min(), x.max(), 200)
    avis = [tf("engine/statistiques.py:g:1615:13", v1=_fmt(x.mean(), 5))]
    if degre >= 4 and n < 10 * degre:
        avis.append(_i18n_t("engine/statistiques.py:g:1617:21"))
    if coefs[-1]["p"] >= alpha:
        avis.append(tf("engine/statistiques.py:g:1619:21", v1=_p_fmt(coefs[-1]['p'])))
    nm = _normalite_dagostino(np.sort(res))
    if nm and nm[1] < 0.05:
        avis.append(_i18n_t("engine/statistiques.py:g:1622:21"))
    eq = f"{y_var['nom']} = {_fmt(beta[0], 5)}" + "".join(f" {'+' if b >= 0 else '−'} {_fmt(abs(b), 5)} × {nom}" for b, nom in zip(beta[1:], noms))
    return _sortie(tf("engine/statistiques.py:g:1624:20", v1=degre, v2=y_var['nom'], v3=x_var['nom']), _i18n_t("engine/statistiques.py:g:1624:101"), _i18n_t("engine/statistiques.py:g:1624:139"), "F", f, f"{degre} ; {dfe}", pf, alpha,
                   (tf("engine/statistiques.py:g:1625:21", v1=degre, v2=_fmt(r2 * 100, 3), v3=y_var['nom']), _i18n_t("engine/statistiques.py:g:1625:122")),
                   coefficients=coefs, equation=eq, estimation={"n": n, "r2": r2, "r2_ajuste": 1 - (1 - r2) * (n - 1) / dfe, "erreur_type_residuelle": math.sqrt(s2)},
                   nuage={"x": x[ordre][::pas].tolist(), "y": y[ordre][::pas].tolist(), "nom_x": x_var["nom"], "nom_y": y_var["nom"],
                          "courbe": {"x": grille.tolist(), "y": (np.column_stack([(grille - x.mean()) ** j for j in range(degre + 1)]) @ beta).tolist()}}, avertissements=avis)


# ── Régression non linéaire générale (Levenberg-Marquardt, numpy seul) ────────────────────────────────────────────
_FONCTIONS_NL = {"exp": np.exp, "log": np.log, "ln": np.log, "log10": np.log10, "sqrt": np.sqrt, "abs": np.abs, "sin": np.sin, "cos": np.cos, "tan": np.tan,
                 "tanh": np.tanh, "sinh": np.sinh, "cosh": np.cosh, "arctan": np.arctan, "atan": np.arctan}
_BINOPS_NL = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power}
_NB_X_MAX = 12
_VARS_X = {"x": 0, **{f"x{i}": i - 1 for i in range(1, _NB_X_MAX + 1)}}      # x = x1 ; x2, x3… = variables explicatives supplémentaires


def _formule_nl(texte, nx=1):
    """Compile une formule en x (x1, x2…) et en paramètres : (évaluateur(X, theta), noms des paramètres, formule). X = tableau (nx, n). Seuls nombres, + − × ÷ ^, x, pi, paramètres et
    fonctions de _FONCTIONS_NL sont admis (arbre syntaxique vérifié nœud par nœud : aucun appel, attribut ni accès autre ne passe)."""
    t = str(texte or "").strip().replace("^", "**").replace("×", "*").replace("·", "*").replace("−", "-").replace("÷", "/").replace("²", "**2").replace("³", "**3").replace(",", ".")
    if not t:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1644:26"))
    if len(t) > 200:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1646:26"))
    try:
        arbre = ast.parse(t, mode="eval").body
    except SyntaxError:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1650:26"))
    noms = []

    def verifier(n):
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS_NL:
            verifier(n.left), verifier(n.right)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            verifier(n.operand)
        elif isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return
        elif isinstance(n, ast.Name):
            if n.id in _VARS_X:
                if _VARS_X[n.id] >= nx:
                    raise ValueError(tf("engine/statistiques.py:g:1663:38", v1=n.id, v2=nx) + (_i18n_t("engine/statistiques.py:g:1663:119") if nx > 1 else _i18n_t("engine/statistiques.py:g:1663:170")))
            elif n.id != "pi" and n.id not in noms:
                if n.id in _FONCTIONS_NL:
                    raise ValueError(tf("engine/statistiques.py:g:1666:38", v1=n.id, v2=n.id))
                noms.append(n.id)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FONCTIONS_NL and len(n.args) == 1 and not n.keywords:
            verifier(n.args[0])
        else:
            raise ValueError(_i18n_t("engine/statistiques.py:g:1671:30")
                             + ", ".join(sorted(_FONCTIONS_NL)) + ".")
    verifier(arbre)
    if not noms:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1675:26"))
    if len(noms) > 20:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1677:26"))
    utilisees = {_VARS_X[n.id] for n in ast.walk(arbre) if isinstance(n, ast.Name) and n.id in _VARS_X}
    if not utilisees:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1680:26"))
    if nx > 1 and len(utilisees) < nx:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1682:26") + ", x".join(str(i + 1) for i in range(nx) if i not in utilisees) + " manque).")

    def evaluer(n, x, env):
        if isinstance(n, ast.BinOp):
            return _BINOPS_NL[type(n.op)](evaluer(n.left, x, env), evaluer(n.right, x, env))
        if isinstance(n, ast.UnaryOp):
            v = evaluer(n.operand, x, env)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Constant):
            return np.float64(n.value)
        if isinstance(n, ast.Name):
            return x[_VARS_X[n.id]] if n.id in _VARS_X else np.float64(math.pi) if n.id == "pi" else env[n.id]
        return _FONCTIONS_NL[n.func.id](evaluer(n.args[0], x, env))

    def f(x, theta):
        with np.errstate(all="ignore"):
            return np.asarray(evaluer(arbre, x, dict(zip(noms, theta))), dtype=float) * np.ones(x.shape[-1])
    return f, list(noms), t


def _jacobien(f, x, theta):
    cols = []
    for j in range(len(theta)):
        h = 6e-6 * max(abs(theta[j]), 1e-3)
        tp, tm = theta.copy(), theta.copy()
        tp[j] += h
        tm[j] -= h
        cols.append((f(x, tp) - f(x, tm)) / (2 * h))
    return np.column_stack(cols)


def _lm(f, x, y, theta, iterations=300):
    """Levenberg-Marquardt : (theta, SCR, convergé ?, itérations). SCR = inf si la formule n'est pas calculable au départ."""
    def scr(t):
        r = y - f(x, t)
        with np.errstate(all="ignore"):
            return (r, float(r @ r)) if np.all(np.isfinite(r)) else (r, math.inf)
    theta = np.array(theta, dtype=float)
    r, s = scr(theta)
    if not math.isfinite(s):
        return theta, math.inf, False, 0
    lam, converge, it = 1e-3, False, 0
    for it in range(1, iterations + 1):
        J = _jacobien(f, x, theta)
        if not np.all(np.isfinite(J)):
            break
        A, g = J.T @ J, J.T @ r
        accepte = False
        for _ in range(40):
            try:
                delta = np.linalg.solve(A + lam * np.diag(np.maximum(np.diag(A), 1e-12)), g)
            except np.linalg.LinAlgError:
                lam *= 10
                continue
            rn, sn = scr(theta + delta)
            if sn < s:
                accepte = True
                break
            lam *= 4
        if not accepte:
            converge = True      # plus aucune amélioration possible depuis ce point
            break
        gain, theta, r, s, lam = s - sn, theta + delta, rn, sn, max(lam / 3, 1e-12)
        if gain <= 1e-13 * max(s, 1e-300) or np.linalg.norm(delta) <= 1e-10 * (np.linalg.norm(theta) + 1e-10):
            converge = True
            break
    return theta, s, converge, it


def _departs_nl(cle, noms, X, y):
    """Valeurs de départ raisonnables pour les modèles prédéfinis (lues dans les données) ; 1 pour tout le reste. X = variables explicatives (nx, n)."""
    x = X[0]
    ordre = np.argsort(x)
    xs, ys = x[ordre], y[ordre]
    etendue, ymax, ymin, ymed = float(np.ptp(x)) or 1.0, float(y.max()), float(y.min()), float(np.median(y))
    pente = 1.0 if ys[-1] >= ys[0] else -1.0
    xmed = float(np.median(x))

    def droite(u, v):
        return np.polyfit(u, v, 1) if np.unique(u).size > 1 else (0.0, float(np.mean(v)))
    if cle == "cobb_douglas":
        if np.all(y > 0) and np.all(X[:2] > 0):
            M = np.column_stack([np.ones(y.size), np.log(X[0]), np.log(X[1])])
            if np.linalg.matrix_rank(M) == 3:
                c = np.linalg.lstsq(M, np.log(y), rcond=None)[0]
                return [math.exp(c[0]), c[1], c[2]]
        return [1.0, 1.0, 1.0]
    if cle in ("saturations", "inhibition"):
        m2 = float(np.median(X[1])) if float(np.median(X[1])) > 0 else 1.0
        return [ymax * (2.0 if cle == "saturations" else 1.3) if ymax > 0 else 1.0, xmed if xmed > 0 else 1.0, m2]
    if cle == "exponentielle":
        if np.all(y > 0):
            b, c = droite(x, np.log(y))
            return [math.exp(c), b]
        if np.all(y < 0):
            b, c = droite(x, np.log(-y))
            return [-math.exp(c), b]
        return [float(ys[0]) or 1.0, 0.01 * pente]
    if cle == "exp_asymptote":
        return [float(ys[-1]), float(ys[0] - ys[-1]) or 1.0, 3.0 / etendue]
    if cle == "puissance":
        if np.all(x > 0) and np.all(y > 0):
            b, c = droite(np.log(x), np.log(y))
            return [math.exp(c), b]
        return [1.0, 1.0]
    if cle == "logistique":
        return [ymax + 0.05 * (ymax - ymin), 4.0 / etendue * pente, float(xs[np.searchsorted(ys, (ymax + ymin) / 2)] if pente > 0 else xmed)]
    if cle == "gompertz":
        return [ymax * 1.1 if ymax > 0 else 1.0, 1.0, 3.0 / etendue * pente]
    if cle == "michaelis":
        return [ymax * 1.2 if ymax > 0 else 1.0, xmed if xmed > 0 else 1.0]
    if cle == "hill":
        return [ymax if ymax > 0 else 1.0, 1.5, xmed if xmed > 0 else 1.0]
    if cle == "gaussienne":
        return [ymax - ymin or 1.0, float(x[int(np.argmax(y))]), etendue / 4.0]
    if cle == "logarithmique":
        if np.all(x > 0):
            b, c = droite(np.log(x), y)
            return [c, b]
        return [ymed, 1.0]
    return [1.0] * len(noms)


def _ajuster_nl(f, X, y, cle, noms, imposes, complet=True, graine=12345):
    """Meilleurs ajustements (triés par SCR croissante) : départs lus dans les données + perturbations (+ départs aléatoires pour une formule libre).
    complet=False : version allégée (utilisée pour les permutations)."""
    k = len(noms)
    base = np.array(_departs_nl(cle, noms, X, y), dtype=float)
    for i, nom in enumerate(noms):
        if nom in imposes:
            base[i] = imposes[nom]
    rng = np.random.default_rng(graine)
    departs = [base] + [base * np.exp(rng.normal(0, 0.8, k)) for _ in range(10 if complet else 2)]
    if cle == "perso" and not imposes:
        departs += [rng.choice([-1.0, 1.0], k) * 10 ** rng.uniform(-2, 2, k) for _ in range(30 if complet else 3)]
    essais = []
    for d in departs:
        th, s, conv, it = _lm(f, X, y, d, 300 if complet else 80)
        if math.isfinite(s):
            essais.append((s, th, conv, it))
    essais.sort(key=lambda e: e[0])
    return essais


def _p_permutation_nl(f, X, y, cle, noms, imposes, nb, budget_s=12.0):
    """p-valeur par permutation de la variable à expliquer (H0 : aucune relation avec les variables explicatives) : part des tirages dont le modèle
    ajusté est au moins aussi bon que sur les données (+1 au numérateur et au dénominateur). Les données et les tirages sont ajustés par la MÊME
    procédure (allégée, déterministe) : c'est ce qui rend le test valide (les données observées sont alors un tirage parmi les autres sous H0).
    Retourne (p, nombre de permutations réalisées)."""
    rng = np.random.default_rng(2026)
    obs = _ajuster_nl(f, X, y, cle, noms, imposes, complet=False)
    rss_obs = obs[0][0] if obs else math.inf
    debut, atteints, faits = time.perf_counter(), 0, 0
    for _ in range(nb):
        if faits >= 19 and time.perf_counter() - debut > budget_s:
            break
        essais = _ajuster_nl(f, X, rng.permutation(y), cle, noms, imposes, complet=False)
        faits += 1
        if essais and essais[0][0] <= rss_obs * (1 + 1e-9):
            atteints += 1
    return (atteints + 1) / (faits + 1), faits


def _se_robuste_nl(f, X, y, th):
    """Erreurs standard robustes (sandwich HC1) : (JᵀJ)⁻¹ Jᵀ diag(r²) J (JᵀJ)⁻¹ · n/(n−k) ; valables si la variance du bruit change avec le niveau. None si non calculable."""
    n, k = y.size, len(th)
    if n <= k:
        return None
    J = _jacobien(f, X, th)
    if not np.all(np.isfinite(J)) or np.linalg.matrix_rank(J) < k:
        return None
    r = y - f(X, th)
    try:
        A = np.linalg.inv(J.T @ J)
    except np.linalg.LinAlgError:
        return None
    se = np.sqrt(np.maximum(np.diag(A @ (J.T * (r ** 2)) @ J @ A) * n / (n - k), 0.0))
    return se if np.all(np.isfinite(se)) and np.all(se > 0) else None


def _bootstrap_nl(f, X, y, theta, nb, budget_s=12.0, graine=4242):
    """Bootstrap studentisé (percentile-t) sur les lignes : couples variables explicatives / variable à expliquer rééchantillonnés avec remise, modèle réajusté
    à chaque tirage en partant de l'estimation observée. Pour chaque tirage on garde le paramètre ET son erreur standard asymptotique : le pivot
    t* = (θ* − θ̂) / se* corrige l'étroitesse du simple bootstrap par percentiles. Seuls les ajustements convergés et identifiables sont gardés.
    Retourne (θ*, se*, nombre de tirages réalisés)."""
    rng = np.random.default_rng(graine)
    n, k = y.size, len(theta)
    debut, ths, ses, faits = time.perf_counter(), [], [], 0
    for _ in range(nb):
        if faits >= 49 and time.perf_counter() - debut > budget_s:
            break
        idx = rng.integers(0, n, n)
        Xb, yb = X[:, idx], y[idx]
        th, sc, conv, _ = _lm(f, Xb, yb, theta, 100)
        faits += 1
        if not (conv and math.isfinite(sc) and np.all(np.isfinite(th)) and n > k):
            continue
        with np.errstate(all="ignore"):
            se = _se_robuste_nl(f, Xb, yb, th)
        if se is not None:
            ths.append(th)
            ses.append(se)
    return (np.array(ths) if ths else np.empty((0, k))), (np.array(ses) if ses else np.empty((0, k))), faits


def _lire_departs(texte, noms):
    sortie = {}
    for nom, val in re.findall(r"([A-Za-z_]\w*)\s*[=:]\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)", str(texte or "")):
        if nom not in noms:
            raise ValueError(tf("engine/statistiques.py:g:1891:30", v1=nom, v2=', '.join(noms)))
        sortie[nom] = float(val.replace(",", "."))
    return sortie


def _test_nonlineaire(v, p, alpha, alt):
    y_var, x_var = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:1897:37")), _var(v.get("b"), _i18n_t("engine/statistiques.py:g:1897:82"))
    xs_var = [x_var] + list(v.get("explicatives") or [])
    for u in xs_var[1:]:
        if not est_quanti(u["genre"]):
            raise ValueError(tf("engine/statistiques.py:g:1901:30", v1=u['nom']))
    nx = len(xs_var)
    if nx > _NB_X_MAX:
        raise ValueError(tf("engine/statistiques.py:g:1904:26", v1=_NB_X_MAX))
    cle = str(p.get("modele") or "exponentielle")
    if cle not in MODELES_NL:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1907:26"))
    if cle != "perso" and nx != MODELES_NX.get(cle, 1):
        raise ValueError(tf("engine/statistiques.py:g:1909:26", v1=MODELES_NX.get(cle, 1), v2=nx))
    f, noms, formule = _formule_nl(p.get("formule") if cle == "perso" else MODELES_NL[cle][0], nx)
    if len({len(u["valeurs"]) for u in xs_var} | {len(y_var["valeurs"])}) > 1:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1912:26"))
    paires = [tuple(_reel(u) for u in lig) for lig in zip(y_var["valeurs"], *[u["valeurs"] for u in xs_var])]
    paires = [q for q in paires if None not in q]
    n, k = len(paires), len(noms)
    if n < k + 2:
        raise ValueError(tf("engine/statistiques.py:g:1917:26", v1=n, v2=k, v3=k + 2))
    y, X = np.array([q[0] for q in paires], dtype=float), np.array([[q[1 + i] for q in paires] for i in range(nx)], dtype=float)
    x = X[0]
    if np.unique(X.T, axis=0).shape[0] < k:
        raise ValueError(tf("engine/statistiques.py:g:1921:26", v1=k))
    sst = float(((y - y.mean()) ** 2).sum())
    if sst <= 0:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1924:26"))
    imposes = _lire_departs(p.get("depart"), noms)
    debut_ajustement = time.perf_counter()
    essais = _ajuster_nl(f, X, y, cle, noms, imposes)
    duree_ajustement = time.perf_counter() - debut_ajustement
    if not essais:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1930:26"))
    rss, theta, converge, iterations = essais[0]
    differents = [e for e in essais[1:] if abs(e[0] - rss) <= 1e-6 * rss and np.linalg.norm(e[1] - theta) > 1e-3 * (np.linalg.norm(theta) + 1e-9)]
    dfe = n - k
    s2 = rss / dfe
    J = _jacobien(f, X, theta)
    avis = []
    identifiable = bool(np.all(np.isfinite(J)) and np.linalg.matrix_rank(J) == k)
    cov = None
    if identifiable:
        try:
            cov = s2 * np.linalg.inv(J.T @ J)
        except np.linalg.LinAlgError:      # JᵀJ numériquement singulière malgré un rang apparent complet (ex. courbe plate sur des données sans relation)
            identifiable = False
    if identifiable:
        se = np.sqrt(np.maximum(np.diag(cov), 0.0))
        corr = cov / np.outer(se, se) if np.all(se > 0) else None
        if corr is not None and k > 1 and np.max(np.abs(corr[np.triu_indices(k, 1)])) > 0.995:
            avis.append(_i18n_t("engine/statistiques.py:g:1948:25"))
    else:
        se = np.full(k, np.nan)
        avis.append(_i18n_t("engine/statistiques.py:g:1951:21"))
    tcrit = _t_crit(dfe, 1 - alpha)
    coefs = []
    for i, nom in enumerate(noms):
        if math.isfinite(se[i]) and se[i] > 0:
            t = float(theta[i] / se[i])
            coefs.append({"nom": nom, "coef": float(theta[i]), "se": float(se[i]), "t": t, "p": _p_t(t, dfe, "different"), "bas": float(theta[i] - tcrit * se[i]), "haut": float(theta[i] + tcrit * se[i])})
        else:
            coefs.append({"nom": nom, "coef": float(theta[i]), "se": None, "t": None, "p": 1.0, "bas": None, "haut": None})
    boot = None
    temps_max = _param(p, "temps_max", 12, float)
    if not 2 <= temps_max <= 300:
        raise ValueError(_i18n_t("engine/statistiques.py:g:1963:26"))
    if str(p.get("erreurs") or "asymptotiques") == "bootstrap":
        nb_b = _param(p, "bootstraps", 399, int)
        if not 99 <= nb_b <= 9999:
            raise ValueError(_i18n_t("engine/statistiques.py:g:1967:30"))
        thb, seb, faits_b = _bootstrap_nl(f, X, y, theta, nb_b, budget_s=temps_max)
        m = thb.shape[0]
        if m < 50 or not identifiable:
            avis.append(tf("engine/statistiques.py:g:1971:25", v1=m, v2=faits_b) + (_i18n_t("engine/statistiques.py:g:1971:118") if not identifiable else _i18n_t("engine/statistiques.py:g:1971:198")) + _i18n_t("engine/statistiques.py:g:1971:243"))
        else:
            boot = m
            se_rob = _se_robuste_nl(f, X, y, theta)
            if se_rob is None:
                se_rob = se
            tstar = (thb - theta) / seb                       # pivots studentisés
            q_bas, q_haut = np.quantile(tstar, [alpha / 2, 1 - alpha / 2], axis=0)
            for i, nom in enumerate(noms):
                se_i = float(se_rob[i])
                t0 = float(theta[i] / se_i)
                pb = (int((np.abs(tstar[:, i]) >= abs(t0)).sum()) + 1) / (m + 1)
                coefs[i] = {"nom": nom, "coef": float(theta[i]), "se": se_i, "t": t0, "p": pb, "bas": float(theta[i] - q_haut[i] * se_i), "haut": float(theta[i] - q_bas[i] * se_i)}
            avis.append(tf("engine/statistiques.py:g:1984:25", v1=boot, v2=faits_b)
                        + (tf("engine/statistiques.py:g:1985:28", v1=faits_b - boot) if faits_b > boot else "."))
            if faits_b < nb_b:
                avis.append(tf("engine/statistiques.py:g:1987:29", v1=_fmt(temps_max, 3), v2=faits_b, v3=nb_b))
    r2 = 1 - rss / sst
    d1 = max(1, k - 1)
    f_glob = max(0.0, (sst - rss) / d1) / s2 if s2 > 0 else math.inf
    p_glob = L.sf("fisher", {"d1": float(d1), "d2": float(dfe)}, f_glob) if math.isfinite(f_glob) else 0.0
    perm = None
    if str(p.get("pglob") or "approchee") == "permutation":
        nb = _param(p, "permutations", 199, int)
        if not 19 <= nb <= 9999:
            raise ValueError(_i18n_t("engine/statistiques.py:g:1996:30"))
        p_glob, faits = _p_permutation_nl(f, X, y, cle, noms, imposes, nb, budget_s=temps_max)
        perm = faits
        if faits < nb:
            avis.append(tf("engine/statistiques.py:g:2000:25", v1=_fmt(temps_max, 3), v2=faits, v3=nb, v4=_fmt(1 / (faits + 1), 3)))
    if cle == "gaussienne":
        avis.append(_i18n_t("engine/statistiques.py:g:2002:21"))
    if not converge:
        avis.append(_i18n_t("engine/statistiques.py:g:2004:21"))
    if differents:
        avis.append(_i18n_t("engine/statistiques.py:g:2006:21"))
    if n < 5 * k:
        avis.append(tf("engine/statistiques.py:g:2008:21", v1=n, v2=k))
    if perm is None:
        avis.append(tf("engine/statistiques.py:g:2010:21", v1=d1, v2=dfe))
    else:
        avis.append(tf("engine/statistiques.py:g:2012:21", v1=perm) + (_i18n_t("engine/statistiques.py:g:2012:209") if boot else _i18n_t("engine/statistiques.py:g:2012:283")))
    ajuste = f(X, theta)
    res = y - ajuste
    nm = _normalite_dagostino(np.sort(res))
    if nm and nm[1] < 0.05:
        avis.append(_i18n_t("engine/statistiques.py:g:2017:21"))
    pas = max(1, n // LIMITES["points_graphique"])
    coupes = []
    if nx == 1:
        ordre = np.argsort(x)
        grille = np.linspace(x.min(), x.max(), 200)
        yg = f(grille[None, :], theta)
        bon = np.isfinite(yg)
        nuage = {"x": x[ordre][::pas].tolist(), "y": y[ordre][::pas].tolist(), "nom_x": x_var["nom"], "nom_y": y_var["nom"], "courbe": {"x": grille[bon].tolist(), "y": yg[bon].tolist()}}
        legende_x = f"x = {x_var['nom']}"
    else:       # plusieurs variables : pas de courbe unique, on trace valeurs observées contre valeurs ajustées
        ordre = np.argsort(ajuste)
        nuage = {"x": ajuste[ordre][::pas].tolist(), "y": y[ordre][::pas].tolist(), "nom_x": f"{y_var['nom']} {_i18n_t('rg.ajustee')}", "nom_y": f"{y_var['nom']} {_i18n_t('rg.observee')}",
                 "courbe": {"x": [float(ajuste.min()), float(ajuste.max())], "y": [float(ajuste.min()), float(ajuste.max())]}}
        legende_x = ", ".join(f"{'x' if i == 0 else f'x{i + 1}'} = {u['nom']}" for i, u in enumerate(xs_var))
        avis.append(_i18n_t("engine/statistiques.py:g:2032:21"))
        med = np.median(X, axis=1)
        for i, u in enumerate(xs_var[:6]):
            Xi = np.tile(med[:, None], (1, n))
            Xi[i] = X[i]
            yp = f(Xi, theta) + res
            ord_i = np.argsort(X[i])
            gi = np.linspace(X[i].min(), X[i].max(), 120)
            Xg = np.tile(med[:, None], (1, gi.size))
            Xg[i] = gi
            yg_i = f(Xg, theta)
            ok = np.isfinite(yp[ord_i][::pas]) & True
            bon_i = np.isfinite(yg_i)
            coupes.append({"x": X[i][ord_i][::pas][ok].tolist(), "y": yp[ord_i][::pas][ok].tolist(), "nom_x": u["nom"], "nom_y": tf("engine/statistiques.py:g:2045:130", v1=y_var['nom']),
                           "courbe": {"x": gi[bon_i].tolist(), "y": yg_i[bon_i].tolist()}})
    cartes = []
    if nx >= 2:      # carte de chaleur pour CHAQUE couple de variables : valeur ajustée, les autres variables à leur médiane ; classées par amplitude décroissante
        med = np.median(X, axis=1)
        idx = np.arange(0, n, pas)
        for i, j in itertools.combinations(range(nx), 2):
            gx, gy = np.linspace(X[i].min(), X[i].max(), 24), np.linspace(X[j].min(), X[j].max(), 24)
            GX, GY = np.meshgrid(gx, gy)
            Xg = np.tile(med[:, None], (1, GX.size))
            Xg[i], Xg[j] = GX.ravel(), GY.ravel()
            with np.errstate(all="ignore"):
                z = f(Xg, theta).reshape(GX.shape)
            if not np.isfinite(z).any():
                continue
            amplitude = float(np.nanmax(np.where(np.isfinite(z), z, np.nan)) - np.nanmin(np.where(np.isfinite(z), z, np.nan)))
            cartes.append({"nom_x": xs_var[i]["nom"], "nom_y": xs_var[j]["nom"], "nom_z": tf("engine/statistiques.py:g:2061:91", v1=y_var['nom']), "amplitude": amplitude,
                           "x": gx.tolist(), "y": gy.tolist(), "z": [[float(u) if np.isfinite(u) else None for u in lig] for lig in z],
                           "pts": {"x": X[i][idx].tolist(), "y": X[j][idx].tolist()}})
        cartes.sort(key=lambda c: -c["amplitude"])
        if cartes:
            avis.append(tf("engine/statistiques.py:g:2066:25", v1=len(cartes)))
    eq = f"{y_var['nom']} = {formule}   ({legende_x} ; " + ", ".join(f"{c['nom']} = {_fmt(c['coef'], 5)}" for c in coefs) + ")"
    return _sortie(tf("engine/statistiques.py:g:2068:20", v1=y_var['nom']) + ", ".join(u["nom"] for u in xs_var), _i18n_t("engine/statistiques.py:g:2068:110"), _i18n_t("engine/statistiques.py:g:2068:178"), "F", f_glob, f"{d1} ; {dfe}", p_glob, alpha,
                   (tf("engine/statistiques.py:g:2069:21", v1=_fmt(r2 * 100, 3), v2=y_var['nom']), _i18n_t("engine/statistiques.py:g:2069:105")),
                   coefficients=coefs, equation=eq,
                   estimation={"n": n, "r2": r2, "r2_ajuste": 1 - (1 - r2) * (n - 1) / dfe, "erreur_type_residuelle": math.sqrt(s2), "somme_carres_residuelle": rss,
                               "aic": n * math.log(max(rss, 1e-300) / n) + 2 * (k + 1), "iterations": iterations, "convergence": "oui" if converge else "non",
                               **({"permutations": perm} if perm else {}), **({"bootstrap": boot} if boot else {})},
                   nuage=nuage, **({"coupes": coupes} if coupes else {}), **({"cartes": cartes} if cartes else {}), avertissements=avis)


def _auc(score, y):
    """Aire sous la courbe ROC par la statistique de rangs (ex æquo = 1/2)."""
    pos, neg = score[y == 1], score[y == 0]
    if pos.size == 0 or neg.size == 0:
        return None
    ordre = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    tout = np.concatenate([pos, neg])[ordre]
    rangs = np.empty(tout.size)
    i = 0
    while i < tout.size:
        j = i
        while j + 1 < tout.size and tout[j + 1] == tout[i]:
            j += 1
        rangs[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    r_pos = rangs[np.argsort(ordre)][:pos.size].sum()
    return float((r_pos - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def _test_logistique(v, p, alpha, alt):
    y_var = _var(v.get("a"), _i18n_t("engine/statistiques.py:g:2097:30"))
    expl = v.get("explicatives") or []
    if not expl:
        raise ValueError(_i18n_t("engine/statistiques.py:g:2100:26"))
    if len(expl) > 10:
        raise ValueError(_i18n_t("engine/statistiques.py:g:2102:26"))
    noms, cols, refs = _colonnes_explicatives(expl)
    if not cols:
        raise ValueError(_i18n_t("engine/statistiques.py:g:2105:26"))
    etiq = [etiquette(u) for u in y_var["valeurs"]]
    mods = sorted({e for e in etiq if e is not None})
    if len(mods) != 2:
        raise ValueError(tf("engine/statistiques.py:g:2109:26", v1=y_var['nom'], v2=len(mods)))
    succes = str(p.get("succes") or "").strip()
    if succes and succes not in mods:
        raise ValueError(tf("engine/statistiques.py:g:2112:26", v1=succes, v2=y_var['nom'], v3=', '.join(mods)))
    if not succes:
        cpt = Counter(e for e in etiq if e is not None)
        succes = min(mods, key=lambda m: (cpt[m], m))   # l'issue la plus rare est le « succès » par défaut
    if any(len(c) != len(etiq) for c in cols):
        raise ValueError(_i18n_t("engine/statistiques.py:g:2117:26"))
    garde = [i for i in range(len(etiq)) if etiq[i] is not None and all(c[i] is not None for c in cols)]
    n, k = len(garde), len(cols)
    y = np.array([1.0 if etiq[i] == succes else 0.0 for i in garde])
    n1 = int(y.sum())
    if n1 == 0 or n1 == n:
        raise ValueError(_i18n_t("engine/statistiques.py:g:2123:26"))
    if n < k + 3:
        raise ValueError(tf("engine/statistiques.py:g:2125:26", v1=n, v2=k))
    X = np.column_stack([np.ones(n)] + [np.array([c[i] for i in garde]) for c in cols])
    if np.linalg.matrix_rank(X) < X.shape[1]:
        raise ValueError(_i18n_t("engine/statistiques.py:g:2128:26"))
    beta, converge = np.zeros(X.shape[1]), False
    for _ in range(100):
        mu = 1 / (1 + np.exp(-np.clip(X @ beta, -30, 30)))
        W = np.clip(mu * (1 - mu), 1e-10, None)
        try:
            delta = np.linalg.solve(X.T @ (X * W[:, None]), X.T @ (y - mu))
        except np.linalg.LinAlgError:
            raise ValueError(_i18n_t("engine/statistiques.py:g:2136:30"))
        beta = beta + delta
        if float(np.abs(delta).max()) < 1e-8:
            converge = True
            break
    mu = np.clip(1 / (1 + np.exp(-np.clip(X @ beta, -30, 30))), 1e-12, 1 - 1e-12)
    W = mu * (1 - mu)
    ll = float((y * np.log(mu) + (1 - y) * np.log(1 - mu)).sum())
    p0 = n1 / n
    ll0 = n1 * math.log(p0) + (n - n1) * math.log(1 - p0)
    lr = max(0.0, 2 * (ll - ll0))
    plr = L.sf("khi2", {"k": float(k)}, lr)
    se = np.sqrt(np.diag(np.linalg.inv(X.T @ (X * W[:, None]))))
    zc = _ND.inv_cdf(1 - alpha / 2)
    coefs = []
    for i, nom in enumerate(["Constante"] + noms):
        z = float(beta[i] / se[i]) if se[i] > 0 else 0.0
        b, h = float(beta[i] - zc * se[i]), float(beta[i] + zc * se[i])
        coefs.append({"nom": nom, "coef": float(beta[i]), "se": float(se[i]), "t": z, "p": 2 * (1 - _ND.cdf(abs(z))), "bas": b, "haut": h,
                      "ou": math.exp(min(beta[i], 700)), "ou_bas": math.exp(min(b, 700)), "ou_haut": math.exp(min(h, 700))})
    classe = (mu >= 0.5).astype(float)
    vp, vn = float(((classe == 1) & (y == 1)).sum()), float(((classe == 0) & (y == 0)).sum())
    avis = list(refs)
    avis.append(tf("engine/statistiques.py:g:2161:17", v1=succes, v2=next(m for m in mods if m != succes)))
    if not converge:
        avis.append(_i18n_t("engine/statistiques.py:g:2161:21"))
    if float(np.abs(beta).max()) > 15:
        avis.append(_i18n_t("engine/statistiques.py:g:2163:21"))
    if min(n1, n - n1) < 10 * k:
        avis.append(_i18n_t("engine/statistiques.py:g:2165:21"))
    eq = f"logit P({y_var['nom']} = {succes}) = {_fmt(beta[0], 5)}" + "".join(f" {'+' if b >= 0 else '−'} {_fmt(abs(b), 5)} × {nom}" for b, nom in zip(beta[1:], noms))
    return _sortie(tf("engine/statistiques.py:g:2167:20", v1=y_var['nom'], v2=succes), _i18n_t("engine/statistiques.py:g:2167:74"), _i18n_t("engine/statistiques.py:g:2167:146"), _i18n_t("engine/statistiques.py:g:2167:194"), lr, str(k), plr, alpha,
                   (tf("engine/statistiques.py:g:2168:21", v1=succes), _i18n_t("engine/statistiques.py:g:2168:92")),
                   coefficients=coefs, equation=eq,
                   estimation={"n": n, "succes": n1, "r2_mcfadden": 1 - ll / ll0, "auc": _auc(mu, y), "taux_bien_classes": (vp + vn) / n, "sensibilite": vp / n1, "specificite": vn / (n - n1), "log_vraisemblance": ll}, avertissements=avis)


_DISPATCH = {"t1": _test_t1, "z1": _test_z1, "t2": _test_t2, "tapparie": _test_tapparie, "anova": _test_anova, "prop1": _test_prop1, "prop2": _test_prop2, "var1": _test_var1, "var2": _test_var2,
             "levene": _test_levene, "mannwhitney": _test_mannwhitney, "wilcoxon": _test_wilcoxon, "kruskal": _test_kruskal, "khi2_indep": _test_khi2_indep, "khi2_adeq": _test_khi2_adeq,
             "fisher": _test_fisher, "correlation": _test_correlation, "regression": _test_regression, "anova2": _test_anova2, "anovan": _test_anovan, "polynomiale": _test_polynomiale, "nonlineaire": _test_nonlineaire, "logistique": _test_logistique, "normalite": _test_normalite, "bootstrap": _test_bootstrap,
             "taille_moy": _test_taille_moy, "taille_prop": _test_taille_prop, "auto": _test_auto}


def executer(cle: str, variables: dict, params: dict | None = None) -> dict:
    """Exécute un test. `variables` = {a, b, groupe: {nom, genre, valeurs}, explicatives: [..]} (lignes alignées) ; `params` = paramètres du catalogue.
    ValueError (message lisible) si les variables ne conviennent pas au test."""
    if cle not in _DISPATCH:
        raise ValueError(_gt("rg8.msg.025"))
    params = params or {}
    alpha = _param(params, "alpha", 0.05)
    if not 0.0001 <= alpha <= 0.5:
        raise ValueError(_i18n_t("engine/statistiques.py:g:2187:26"))
    alt = str(params.get("alt") or "different")
    if alt not in _ALT:
        raise ValueError(_i18n_t("engine/statistiques.py:g:2190:26"))
    return nettoyer(_DISPATCH[cle](variables or {}, params, alpha, alt))
