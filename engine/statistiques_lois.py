"""STATISTIQUES — lois de probabilité et calcul de probabilités (module pur : stdlib + fonctions exactes de series_temporelles, sans scipy).

Source unique des lois : le calculateur de probabilités ET les p-valeurs des tests (engine/statistiques.py) passent par les mêmes fonctions
`cdf` / `sf` / `quantile`. Rien n'est lu en base ni écrit : tout est calculé en mémoire.

Lois : normale, Student, khi-deux, Fisher, exponentielle, uniforme, gamma, bêta, log-normale, Weibull (continues) ;
binomiale, Poisson, géométrique, binomiale négative, hypergéométrique, uniforme discrète (discrètes).
Événements (indépendants, incompatibles, conditionnelle, union, Bayes), combinatoire et ajustement d'une loi à des données.
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import tl
from engine.i18n import t as _i18n_t, tf

import math
from statistics import NormalDist

from .series_temporelles import _beta_incomplete as _bi, _gamma_q as _gq, _repartition_t
from engine.i18n import t as _gt  # noqa: E402

INF = math.inf


def _lbeta(a, b):
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _lcomb(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _gp(a, x):
    """Gamma incomplète régularisée inférieure P(a, x)."""
    return 0.0 if x <= 0 else 1.0 - _gq(a, x)


def _t_sf(t, df):
    """Queue haute de Student, exacte même très loin dans la queue (pas de 1 - cdf)."""
    moitie = 0.5 * _bi(df / 2.0, 0.5, df / (df + t * t))
    return moitie if t > 0 else 1.0 - moitie


def _par(cle, libelle, defaut, lo=None, hi=None, strict_lo=False, strict_hi=False, entier=False):
    return {"cle": cle, "libelle": libelle, "defaut": defaut, "lo": lo, "hi": hi, "strict_lo": strict_lo, "strict_hi": strict_hi, "entier": entier}


def _norm(p):
    return NormalDist(p["mu"], p["sigma"])


def _pdf_student(p, x):
    v = p["nu"]
    return math.exp(math.lgamma((v + 1) / 2) - math.lgamma(v / 2)) / math.sqrt(v * math.pi) * (1 + x * x / v) ** (-(v + 1) / 2)


def _pdf_khi2(p, x):
    k = p["k"]
    if x < 0:
        return 0.0
    if x == 0:
        return INF if k < 2 else (0.5 if k == 2 else 0.0)
    return math.exp((k / 2 - 1) * math.log(x) - x / 2 - (k / 2) * math.log(2) - math.lgamma(k / 2))


def _pdf_fisher(p, x):
    d1, d2 = p["d1"], p["d2"]
    if x <= 0:
        return 0.0
    return math.exp(0.5 * (d1 * math.log(d1 * x) + d2 * math.log(d2) - (d1 + d2) * math.log(d1 * x + d2)) - math.log(x) - _lbeta(d1 / 2, d2 / 2))


def _pdf_gamma(p, x):
    k, th = p["k"], p["theta"]
    if x < 0:
        return 0.0
    if x == 0:
        return INF if k < 1 else (1.0 / th if k == 1 else 0.0)
    return math.exp((k - 1) * math.log(x) - x / th - math.lgamma(k) - k * math.log(th))


def _pdf_beta(p, x):
    a, b = p["a"], p["b"]
    if x < 0 or x > 1:
        return 0.0
    if x in (0.0, 1.0):
        e = a if x == 0 else b
        return INF if e < 1 else (math.exp(-_lbeta(a, b)) if e == 1 else 0.0)
    return math.exp((a - 1) * math.log(x) + (b - 1) * math.log1p(-x) - _lbeta(a, b))


def _pdf_weib(p, x):
    k, lam = p["k"], p["lam"]
    if x < 0:
        return 0.0
    if x == 0:
        return INF if k < 1 else (1.0 / lam if k == 1 else 0.0)
    return (k / lam) * (x / lam) ** (k - 1) * math.exp(-((x / lam) ** k))


def _pmf_binom(p, k):
    n, pr = p["n"], p["p"]
    if k < 0 or k > n:
        return 0.0
    if pr == 0.0:
        return 1.0 if k == 0 else 0.0
    if pr == 1.0:
        return 1.0 if k == n else 0.0
    return math.exp(_lcomb(n, k) + k * math.log(pr) + (n - k) * math.log1p(-pr))


def _cdf_binom(p, k):
    n, pr = p["n"], p["p"]
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if pr == 0.0:
        return 1.0
    if pr == 1.0:
        return 0.0
    return _bi(n - k, k + 1, 1.0 - pr)


def _pmf_geo(p, k):
    pr = p["p"]
    if k < 1:
        return 0.0
    return pr if pr == 1.0 else math.exp((k - 1) * math.log1p(-pr) + math.log(pr))


def _cdf_geo(p, k):
    pr = p["p"]
    if k < 1:
        return 0.0
    return 1.0 if pr == 1.0 else -math.expm1(k * math.log1p(-pr))


def _pmf_nb(p, k):
    r, pr = p["r"], p["p"]
    if k < 0:
        return 0.0
    if pr == 1.0:
        return 1.0 if k == 0 else 0.0
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(pr) + k * math.log1p(-pr))


def _cdf_nb(p, k):
    return 0.0 if k < 0 else (1.0 if p["p"] == 1.0 else _bi(p["r"], k + 1, p["p"]))


def _sup_hyper(p):
    return max(0, p["n"] - (p["N"] - p["K"])), min(p["n"], p["K"])


def _pmf_hyper(p, k):
    lo, hi = _sup_hyper(p)
    if k < lo or k > hi:
        return 0.0
    return math.exp(_lcomb(p["K"], k) + _lcomb(p["N"] - p["K"], p["n"] - k) - _lcomb(p["N"], p["n"]))


def _cdf_hyper(p, k):
    lo, hi = _sup_hyper(p)
    if k < lo:
        return 0.0
    if k >= hi:
        return 1.0
    return min(1.0, sum(_pmf_hyper(p, j) for j in range(lo, int(k) + 1)))


def _pmf_ud(p, k):
    return 1.0 / (p["b"] - p["a"] + 1) if p["a"] <= k <= p["b"] else 0.0


def _cdf_ud(p, k):
    return 0.0 if k < p["a"] else (1.0 if k >= p["b"] else (math.floor(k) - p["a"] + 1) / (p["b"] - p["a"] + 1))


# Chaque loi : nom, discrète ?, paramètres, densité/masse, répartition, (queue haute exacte), moyenne, variance, support, contrôle supplémentaire.
LOIS = {
    "normale": dict(nom="Normale N(μ, σ)", discrete=False, params=[_par("mu", tl("engine/statistiques_lois.py:g:178:79"), 0.0), _par("sigma", tl("engine/statistiques_lois.py:g:178:112"), 1.0, 0, strict_lo=True)],
                    pdf=lambda p, x: _norm(p).pdf(x), cdf=lambda p, x: _norm(p).cdf(x), sf=lambda p, x: _norm(p).cdf(2 * p["mu"] - x),
                    moy=lambda p: p["mu"], var=lambda p: p["sigma"] ** 2, sup=lambda p: (-INF, INF)),
    "student": dict(nom="Student t(ν)", discrete=False, params=[_par("nu", tl("engine/statistiques_lois.py:g:181:76"), 10.0, 0, strict_lo=True)],
                    pdf=_pdf_student, cdf=lambda p, x: 1.0 - _t_sf(x, p["nu"]), sf=lambda p, x: _t_sf(x, p["nu"]),
                    moy=lambda p: 0.0 if p["nu"] > 1 else None, var=lambda p: p["nu"] / (p["nu"] - 2) if p["nu"] > 2 else (INF if p["nu"] > 1 else None),
                    sup=lambda p: (-INF, INF)),
    "khi2": dict(nom="Khi-deux χ²(k)", discrete=False, params=[_par("k", tl("engine/statistiques_lois.py:g:185:74"), 5.0, 0, strict_lo=True)],
                 pdf=_pdf_khi2, cdf=lambda p, x: _gp(p["k"] / 2, x / 2), sf=lambda p, x: _gq(p["k"] / 2, x / 2),
                 moy=lambda p: p["k"], var=lambda p: 2 * p["k"], sup=lambda p: (0.0, INF)),
    "fisher": dict(nom="Fisher F(d1, d2)", discrete=False, params=[_par("d1", tl("engine/statistiques_lois.py:g:188:79"), 5.0, 0, strict_lo=True), _par("d2", tl("engine/statistiques_lois.py:g:188:139"), 10.0, 0, strict_lo=True)],
                   pdf=_pdf_fisher, cdf=lambda p, x: 0.0 if x <= 0 else _bi(p["d1"] / 2, p["d2"] / 2, p["d1"] * x / (p["d1"] * x + p["d2"])),
                   sf=lambda p, x: 1.0 if x <= 0 else _bi(p["d2"] / 2, p["d1"] / 2, p["d2"] / (p["d2"] + p["d1"] * x)),
                   moy=lambda p: p["d2"] / (p["d2"] - 2) if p["d2"] > 2 else None,
                   var=lambda p: 2 * p["d2"] ** 2 * (p["d1"] + p["d2"] - 2) / (p["d1"] * (p["d2"] - 2) ** 2 * (p["d2"] - 4)) if p["d2"] > 4 else None,
                   sup=lambda p: (0.0, INF)),
    "exponentielle": dict(nom="Exponentielle E(λ)", discrete=False, params=[_par("lam", "Taux λ", 1.0, 0, strict_lo=True)],
                          pdf=lambda p, x: p["lam"] * math.exp(-p["lam"] * x) if x >= 0 else 0.0, cdf=lambda p, x: -math.expm1(-p["lam"] * x) if x > 0 else 0.0,
                          sf=lambda p, x: math.exp(-p["lam"] * x) if x > 0 else 1.0, moy=lambda p: 1 / p["lam"], var=lambda p: 1 / p["lam"] ** 2,
                          sup=lambda p: (0.0, INF)),
    "uniforme": dict(nom="Uniforme continue U(a, b)", discrete=False, params=[_par("a", "Borne a", 0.0), _par("b", "Borne b", 1.0)],
                     pdf=lambda p, x: 1 / (p["b"] - p["a"]) if p["a"] <= x <= p["b"] else 0.0,
                     cdf=lambda p, x: min(1.0, max(0.0, (x - p["a"]) / (p["b"] - p["a"]))), moy=lambda p: (p["a"] + p["b"]) / 2,
                     var=lambda p: (p["b"] - p["a"]) ** 2 / 12, sup=lambda p: (p["a"], p["b"]),
                     controle=lambda p: None if p["b"] > p["a"] else _i18n_t("engine/statistiques_lois.py:g:201:70")),
    "gamma": dict(nom="Gamma Γ(k, θ)", discrete=False, params=[_par("k", "Forme k", 2.0, 0, strict_lo=True), _par("theta", tl("engine/statistiques_lois.py:g:203:124"), 1.0, 0, strict_lo=True)],
                  pdf=_pdf_gamma, cdf=lambda p, x: _gp(p["k"], x / p["theta"]), sf=lambda p, x: _gq(p["k"], x / p["theta"]),
                  moy=lambda p: p["k"] * p["theta"], var=lambda p: p["k"] * p["theta"] ** 2, sup=lambda p: (0.0, INF)),
    "beta": dict(nom=tl("engine/statistiques_lois.py:g:208:22"), discrete=False, params=[_par("a", "Forme a", 2.0, 0, strict_lo=True), _par("b", "Forme b", 2.0, 0, strict_lo=True)],
                 pdf=_pdf_beta, cdf=lambda p, x: 0.0 if x <= 0 else (1.0 if x >= 1 else _bi(p["a"], p["b"], x)),
                 moy=lambda p: p["a"] / (p["a"] + p["b"]), var=lambda p: p["a"] * p["b"] / ((p["a"] + p["b"]) ** 2 * (p["a"] + p["b"] + 1)),
                 sup=lambda p: (0.0, 1.0)),
    "lognormale": dict(nom="Log-normale LN(μ, σ)", discrete=False, params=[_par("mu", tl("engine/statistiques_lois.py:g:210:87"), 0.0), _par("sigma", tl("engine/statistiques_lois.py:g:210:136"), 1.0, 0, strict_lo=True)],
                       pdf=lambda p, x: math.exp(-((math.log(x) - p["mu"]) ** 2) / (2 * p["sigma"] ** 2)) / (x * p["sigma"] * math.sqrt(2 * math.pi)) if x > 0 else 0.0,
                       cdf=lambda p, x: NormalDist().cdf((math.log(x) - p["mu"]) / p["sigma"]) if x > 0 else 0.0,
                       moy=lambda p: math.exp(p["mu"] + p["sigma"] ** 2 / 2), var=lambda p: (math.exp(p["sigma"] ** 2) - 1) * math.exp(2 * p["mu"] + p["sigma"] ** 2),
                       sup=lambda p: (0.0, INF)),
    "weibull": dict(nom="Weibull W(k, λ)", discrete=False, params=[_par("k", "Forme k", 1.5, 0, strict_lo=True), _par("lam", tl("engine/statistiques_lois.py:g:215:126"), 1.0, 0, strict_lo=True)],
                    pdf=_pdf_weib, cdf=lambda p, x: -math.expm1(-((x / p["lam"]) ** p["k"])) if x > 0 else 0.0,
                    sf=lambda p, x: math.exp(-((x / p["lam"]) ** p["k"])) if x > 0 else 1.0,
                    moy=lambda p: p["lam"] * math.gamma(1 + 1 / p["k"]),
                    var=lambda p: p["lam"] ** 2 * (math.gamma(1 + 2 / p["k"]) - math.gamma(1 + 1 / p["k"]) ** 2), sup=lambda p: (0.0, INF)),
    "binomiale": dict(nom="Binomiale B(n, p)", discrete=True, params=[_par("n", tl("engine/statistiques_lois.py:g:220:81"), 10, 1, entier=True), _par("p", tl("engine/statistiques_lois.py:g:220:133"), 0.5, 0, 1)],
                      pdf=_pmf_binom, cdf=_cdf_binom, moy=lambda p: p["n"] * p["p"], var=lambda p: p["n"] * p["p"] * (1 - p["p"]), sup=lambda p: (0, p["n"])),
    "poisson": dict(nom="Poisson P(λ)", discrete=True, params=[_par("lam", tl("engine/statistiques_lois.py:g:222:76"), 4.0, 0, strict_lo=True)],
                    pdf=lambda p, k: math.exp(k * math.log(p["lam"]) - p["lam"] - math.lgamma(k + 1)) if k >= 0 else 0.0,
                    cdf=lambda p, k: _gq(math.floor(k) + 1, p["lam"]) if k >= 0 else 0.0, sf=lambda p, k: _gp(math.floor(k) + 1, p["lam"]) if k >= 0 else 1.0,
                    moy=lambda p: p["lam"], var=lambda p: p["lam"], sup=lambda p: (0, INF)),
    "geometrique": dict(nom=tl("engine/statistiques_lois.py:g:228:29"), discrete=True, params=[_par("p", tl("engine/statistiques_lois.py:g:226:107"), 0.3, 0, 1, strict_lo=True)],
                        pdf=_pmf_geo, cdf=_cdf_geo, moy=lambda p: 1 / p["p"], var=lambda p: (1 - p["p"]) / p["p"] ** 2, sup=lambda p: (1, INF)),
    "binomiale_negative": dict(nom=tl("engine/statistiques_lois.py:g:230:36"), discrete=True,
                               params=[_par("r", tl("engine/statistiques_lois.py:g:229:50"), 3, 1, entier=True), _par("p", tl("engine/statistiques_lois.py:g:229:102"), 0.5, 0, 1, strict_lo=True)],
                               pdf=_pmf_nb, cdf=_cdf_nb, moy=lambda p: p["r"] * (1 - p["p"]) / p["p"], var=lambda p: p["r"] * (1 - p["p"]) / p["p"] ** 2, sup=lambda p: (0, INF)),
    "hypergeometrique": dict(nom=tl("engine/statistiques_lois.py:g:233:34"), discrete=True,
                             params=[_par("N", tl("engine/statistiques_lois.py:g:232:48"), 50, 1, entier=True), _par("K", tl("engine/statistiques_lois.py:g:232:108"), 10, 0, entier=True), _par("n", tl("engine/statistiques_lois.py:g:232:170"), 5, 0, entier=True)],
                             pdf=_pmf_hyper, cdf=_cdf_hyper, moy=lambda p: p["n"] * p["K"] / p["N"],
                             var=lambda p: p["n"] * (p["K"] / p["N"]) * (1 - p["K"] / p["N"]) * (p["N"] - p["n"]) / (p["N"] - 1) if p["N"] > 1 else 0.0, sup=_sup_hyper,
                             controle=lambda p: None if p["K"] <= p["N"] and p["n"] <= p["N"] and p["N"] <= 2_000_000 else _i18n_t("engine/statistiques_lois.py:g:234:124")),
    "uniforme_discrete": dict(nom=tl("engine/statistiques_lois.py:g:238:35"), discrete=True, params=[_par("a", tl("engine/statistiques_lois.py:g:236:97"), 1, entier=True), _par("b", tl("engine/statistiques_lois.py:g:236:148"), 6, entier=True)],
                              pdf=_pmf_ud, cdf=_cdf_ud, moy=lambda p: (p["a"] + p["b"]) / 2, var=lambda p: ((p["b"] - p["a"] + 1) ** 2 - 1) / 12, sup=lambda p: (p["a"], p["b"]),
                              controle=lambda p: None if p["b"] >= p["a"] else _i18n_t("engine/statistiques_lois.py:g:237:80")),
}


def catalogue_lois() -> list[dict]:
    """Pour l'interface : une entrée par loi (clé, nom, discrète ?, paramètres avec défaut et bornes)."""
    return [{"cle": c, "nom": d["nom"], "discrete": d["discrete"], "params": d["params"]} for c, d in LOIS.items()]


def verifier(cle: str, brut: dict) -> dict:
    """Paramètres validés (ValueError avec un message lisible si l'un est absent, hors bornes ou non entier)."""
    if cle not in LOIS:
        raise ValueError(_gt("rg8.msg.029"))
    p = {}
    for s in LOIS[cle]["params"]:
        try:
            v = float(brut.get(s["cle"], s["defaut"]))
        except (TypeError, ValueError):
            raise ValueError(tf("engine/statistiques_lois.py:g:255:30", v1=s['libelle']))
        if not math.isfinite(v):
            raise ValueError(tf("engine/statistiques_lois.py:g:257:30", v1=s['libelle']))
        if s["entier"]:
            if v != int(v):
                raise ValueError(tf("engine/statistiques_lois.py:g:260:34", v1=s['libelle']))
            v = int(v)
        if (s["lo"] is not None and (v < s["lo"] or (s["strict_lo"] and v == s["lo"]))) or (s["hi"] is not None and (v > s["hi"] or (s["strict_hi"] and v == s["hi"]))):
            raise ValueError(tf("engine/statistiques_lois.py:g:263:30", v1=s['libelle']))
        p[s["cle"]] = v
    message = LOIS[cle].get("controle", lambda _p: None)(p)
    if message:
        raise ValueError(message)
    return p


def pdf(cle, p, x):
    """Densité (loi continue) ou masse (loi discrète ; 0 si x n'est pas un entier)."""
    if LOIS[cle]["discrete"] and x != math.floor(x):
        return 0.0
    try:
        return LOIS[cle]["pdf"](p, x)
    except (ValueError, OverflowError, ZeroDivisionError):
        return 0.0


def cdf(cle, p, x):
    """P(X ≤ x)."""
    if x == -INF:
        return 0.0
    if x == INF:
        return 1.0
    return min(1.0, max(0.0, LOIS[cle]["cdf"](p, math.floor(x) if LOIS[cle]["discrete"] else x)))


def sf(cle, p, x):
    """P(X > x), exacte loin dans la queue quand la loi sait la calculer."""
    if x == INF:
        return 0.0
    if x == -INF:
        return 1.0
    f = LOIS[cle].get("sf")
    if f:
        return min(1.0, max(0.0, f(p, math.floor(x) if LOIS[cle]["discrete"] else x)))
    return max(0.0, 1.0 - cdf(cle, p, x))


def quantile(cle, p, q):
    """Plus petite valeur x telle que P(X ≤ x) ≥ q (loi discrète) ; valeur exacte de x (loi continue)."""
    d = LOIS[cle]
    lo, hi = d["sup"](p)
    if q <= 0:
        return lo
    if q >= 1:
        return hi
    if d["discrete"]:
        borne = lo + 1
        while hi == INF:
            if cdf(cle, p, borne) >= q - 1e-12 or borne > 1e15:
                hi = borne
            else:
                borne = lo + (borne - lo) * 2
        a, b = int(lo), int(hi)
        while a < b:
            m = (a + b) // 2
            if cdf(cle, p, m) >= q - 1e-12:
                b = m
            else:
                a = m + 1
        return a
    if cle == "normale":
        return _norm(p).inv_cdf(q)
    if lo == -INF:
        lo = -1.0
        while cdf(cle, p, lo) > q and lo > -1e300:
            lo *= 2
    if hi == INF:
        hi = max(lo, 0.0) + 1.0
        while cdf(cle, p, hi) < q and hi < 1e300:
            hi *= 2
    for _ in range(200):
        m = (lo + hi) / 2
        if cdf(cle, p, m) < q:
            lo = m
        else:
            hi = m
        if hi - lo <= 1e-15 * max(1.0, abs(m)):
            break
    return (lo + hi) / 2


def _nb(v, chiffres: int = 6):
    if v is None:
        return "—"
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.{chiffres}g}"


def _courbe(cle, p, x, a, b):
    d = LOIS[cle]
    lo, hi = quantile(cle, p, 0.0005 if d["discrete"] else 0.001), quantile(cle, p, 0.9995 if d["discrete"] else 0.999)
    for v in (x, a, b):   # les valeurs demandées restent visibles sur le graphique
        if v is not None and math.isfinite(v):
            lo, hi = min(lo, v), max(hi, v)
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        lo, hi = lo if math.isfinite(lo) else -1.0, hi if math.isfinite(hi) else 1.0
        hi = hi if hi > lo else lo + 1.0
    if d["discrete"]:
        lo, hi = math.floor(lo), min(math.ceil(hi), math.floor(lo) + 149)
        xs = list(range(int(lo), int(hi) + 1))
    else:
        xs = [lo + (hi - lo) * i / 159 for i in range(160)]
    ys = [pdf(cle, p, v) for v in xs]
    return {"x": xs, "y": [(v if math.isfinite(v) else None) for v in ys], "cdf": [cdf(cle, p, v) for v in xs], "discrete": d["discrete"]}


def calculer_loi(cle: str, brut: dict, x=None, a=None, b=None, q=None, niveau=None) -> dict:
    """Toutes les probabilités d'une loi en un appel : en x (=, ≤, <, ≥, >), entre a et b, quantile de niveau q, intervalle central de niveau `niveau`."""
    p = verifier(cle, brut)
    d = LOIS[cle]
    disc, moy, var = d["discrete"], d["moy"](p), d["var"](p)
    res, avis = [], []
    if x is not None:
        if disc and x != math.floor(x):
            avis.append(tf("engine/statistiques_lois.py:g:380:25", v1=_nb(x)))
        xe = math.floor(x) if disc else x
        if disc:
            res += [("P(X = %s)" % _nb(x), pdf(cle, p, x)), ("P(X ≤ %s)" % _nb(x), cdf(cle, p, x)), ("P(X < %s)" % _nb(x), cdf(cle, p, xe - 1) if x == xe else cdf(cle, p, xe)),
                    ("P(X ≥ %s)" % _nb(x), sf(cle, p, xe - 1) if x == xe else sf(cle, p, xe)), ("P(X > %s)" % _nb(x), sf(cle, p, x))]
        else:
            res += [(_i18n_t("engine/statistiques_lois.py:g:386:22") % _nb(x), pdf(cle, p, x)), ("P(X ≤ %s)" % _nb(x), cdf(cle, p, x)), ("P(X ≥ %s)" % _nb(x), sf(cle, p, x))]
    if a is not None and b is not None:
        if b < a:
            a, b = b, a
        dans = (cdf(cle, p, b) - (cdf(cle, p, a - 1) if disc and a == math.floor(a) else cdf(cle, p, a))) if disc else (cdf(cle, p, b) - cdf(cle, p, a))
        dans = min(1.0, max(0.0, dans))
        res += [("P(%s ≤ X ≤ %s)" % (_nb(a), _nb(b)), dans), (_gt("rg6.msg.108") % (_nb(a), _nb(b)), 1.0 - dans)]
    if q is not None:
        res.append((_i18n_t("engine/statistiques_lois.py:g:394:21") % _nb(q), quantile(cle, p, q)))
    if niveau is not None:
        r = (1.0 - niveau) / 2
        res.append((_i18n_t("engine/statistiques_lois.py:g:397:21") % _nb(niveau * 100), quantile(cle, p, r)))
        res.append((_i18n_t("engine/statistiques_lois.py:g:398:21") % _nb(niveau * 100), quantile(cle, p, 1.0 - r)))
    return {"cle": cle, "nom": d["nom"], "discrete": disc, "params": p, "moyenne": moy, "variance": var,
            "ecart_type": math.sqrt(var) if var is not None and math.isfinite(var) else None,
            "resultats": [{"libelle": l, "valeur": v} for l, v in res], "courbe": _courbe(cle, p, x, a, b), "avertissements": avis,
            "marques": {"x": x, "a": a, "b": b}}


# ── Événements, Bayes, combinatoire ──────────────────────────────────────────
RELATIONS = {"independants": td("engine/statistiques_lois.py:g:408:30"), "incompatibles": td("engine/statistiques_lois.py:g:408:70"), "inclusion": td("engine/statistiques_lois.py:g:408:125"),
             "inter": "Je connais P(A ∩ B)", "a_sachant_b": "Je connais P(A | B)", "b_sachant_a": "Je connais P(B | A)"}


def _proba(v, nom):
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(tf("engine/statistiques_lois.py:g:414:26", v1=nom))
    if not 0.0 <= v <= 1.0:
        raise ValueError(tf("engine/statistiques_lois.py:g:416:26", v1=nom))
    return v


def evenements(pa, pb, relation, valeur=None) -> dict:
    """Probabilités de deux événements A et B à partir de P(A), P(B) et d'une relation (ou d'une probabilité connue en plus)."""
    pa, pb = _proba(pa, "P(A)"), _proba(pb, "P(B)")
    if relation == "independants":
        inter = pa * pb
    elif relation == "incompatibles":
        inter = 0.0
    elif relation == "inclusion":
        inter = min(pa, pb) if pa <= pb else None
        if inter is None:
            raise ValueError(_i18n_t("engine/statistiques_lois.py:g:430:30"))
    elif relation == "inter":
        inter = _proba(valeur, "P(A ∩ B)")
    elif relation == "a_sachant_b":
        inter = _proba(valeur, "P(A | B)") * pb
    elif relation == "b_sachant_a":
        inter = _proba(valeur, "P(B | A)") * pa
    else:
        raise ValueError(_gt("rg8.msg.030"))
    if inter < max(0.0, pa + pb - 1.0) - 1e-12 or inter > min(pa, pb) + 1e-12:
        raise ValueError(tf("engine/statistiques_lois.py:g:440:26", v1=_nb(max(0.0, pa + pb - 1.0)), v2=_nb(min(pa, pb))))
    union = pa + pb - inter
    cond = lambda num, den: (num / den) if den > 0 else None
    return {"resultats": [
        {"libelle": td("engine/statistiques_lois.py:g:446:21#2"), "valeur": inter}, {"libelle": td("engine/statistiques_lois.py:g:446:72"), "valeur": union},
        {"libelle": _gt("rg8.msg.031"), "valeur": cond(inter, pb)}, {"libelle": _gt("rg8.msg.032"), "valeur": cond(inter, pa)},
        {"libelle": _i18n_t("engine/statistiques_lois.py:g:446:21"), "valeur": 1.0 - pa}, {"libelle": _i18n_t("engine/statistiques_lois.py:g:446:66"), "valeur": 1.0 - pb},
        {"libelle": _i18n_t("engine/statistiques_lois.py:g:447:21"), "valeur": pa - inter}, {"libelle": _i18n_t("engine/statistiques_lois.py:g:447:77"), "valeur": pb - inter},
        {"libelle": _i18n_t("engine/statistiques_lois.py:g:448:21"), "valeur": 1.0 - union}, {"libelle": _i18n_t("engine/statistiques_lois.py:g:448:73"), "valeur": pa + pb - 2 * inter}],
        "independants": abs(inter - pa * pb) < 1e-9, "incompatibles": inter < 1e-12}


def bayes(hypotheses: list[dict]) -> dict:
    """Formule des probabilités totales et de Bayes : chaque hypothèse a une probabilité a priori et une vraisemblance P(observation | hypothèse)."""
    if len(hypotheses) < 2:
        raise ValueError(_i18n_t("engine/statistiques_lois.py:g:455:26"))
    if len(hypotheses) > 20:
        raise ValueError(_i18n_t("engine/statistiques_lois.py:g:457:26"))
    lignes = [(str(h.get("nom") or f"H{i + 1}"), _proba(h.get("prior"), tf("engine/statistiques_lois.py:g:461:73", v1=i + 1)), _proba(h.get("vraisemblance"), tf("engine/statistiques_lois.py:g:461:145", v1=i + 1)))
              for i, h in enumerate(hypotheses)]
    if abs(sum(l[1] for l in lignes) - 1.0) > 1e-6:
        raise ValueError(tf("engine/statistiques_lois.py:g:464:26#2", v1=_nb(sum(l[1] for l in lignes))))
    total = sum(p * v for _n, p, v in lignes)
    if total <= 0:
        raise ValueError(_i18n_t("engine/statistiques_lois.py:g:464:26"))
    return {"total": total, "lignes": [{"nom": n, "prior": p, "vraisemblance": v, "conjointe": p * v, "posterieure": p * v / total} for n, p, v in lignes]}


def combinatoire(n, k) -> dict:
    """Factorielles, arrangements, combinaisons (avec et sans répétition) : valeur exacte en texte + valeur approchée."""
    try:
        n, k = int(n), int(k)
    except (TypeError, ValueError):
        raise ValueError(_i18n_t("engine/statistiques_lois.py:g:473:26"))
    if n < 0 or k < 0 or n > 5000 or k > 5000:
        raise ValueError(_i18n_t("engine/statistiques_lois.py:g:475:26"))
    lignes = [(_i18n_t("engine/statistiques_lois.py:g:476:16"), math.factorial(n))]
    if k <= n:
        lignes += [(_i18n_t("engine/statistiques_lois.py:g:478:21"), math.perm(n, k)), (_i18n_t("engine/statistiques_lois.py:g:478:82"), math.comb(n, k))]
    lignes += [(_i18n_t("engine/statistiques_lois.py:g:479:17"), n ** k), (_i18n_t("engine/statistiques_lois.py:g:479:65"), math.comb(n + k - 1, k) if n + k > 0 else 1)]
    return {"resultats": [{"libelle": l, "exact": str(v) if len(str(v)) <= 60 else str(v)[:20] + _gt("rg6.msg.109") % len(str(v)), "valeur": float(v) if v < 10 ** 300 else None} for l, v in lignes]}


# ── Ajustement d'une loi à des données ───────────────────────────────────────
def _ks_p(d, n):
    lam = (math.sqrt(n) + 0.12 + 0.11 / math.sqrt(n)) * d
    if lam < 0.27:
        return 1.0
    return min(1.0, max(0.0, 2 * sum((-1) ** (j - 1) * math.exp(-2 * j * j * lam * lam) for j in range(1, 101))))


def ajuster(x: list[float]) -> list[dict]:
    """Ajuste les lois plausibles à un échantillon (moments / maximum de vraisemblance), classées par AIC. p(KS) est indicative (paramètres estimés)."""
    x = sorted(float(v) for v in x if v is not None and math.isfinite(v))
    n = len(x)
    if n < 8:
        raise ValueError(_i18n_t("engine/statistiques_lois.py:g:496:26"))
    m = sum(x) / n
    v = sum((t - m) ** 2 for t in x) / n
    if v <= 0:
        raise ValueError(_i18n_t("engine/statistiques_lois.py:g:500:26"))
    cand = [("normale", {"mu": m, "sigma": math.sqrt(v)})]
    entier = all(t == int(t) for t in x)
    if x[0] >= 0 and m > 0:
        cand.append(("exponentielle", {"lam": 1 / m}))
    if x[0] > 0:
        lg = [math.log(t) for t in x]
        ml = sum(lg) / n
        cand += [("lognormale", {"mu": ml, "sigma": math.sqrt(max(sum((t - ml) ** 2 for t in lg) / n, 1e-12))}), ("gamma", {"k": m * m / v, "theta": v / m})]
    if x[-1] > x[0]:
        cand.append(("uniforme", {"a": x[0], "b": x[-1]}))
    if entier and x[0] >= 0 and m > 0:
        cand.append(("poisson", {"lam": m}))
    sortie = []
    for cle, p in cand:
        try:
            dmax = 0.0
            for i, t in enumerate(x):
                f = cdf(cle, p, t)
                dmax = max(dmax, abs((i + 1) / n - f), abs(f - i / n))
            ll = sum(math.log(max(pdf(cle, p, t), 1e-300)) for t in x)
        except (ValueError, OverflowError, ZeroDivisionError):
            continue
        sortie.append({"cle": cle, "nom": LOIS[cle]["nom"], "params": p, "ks_d": dmax, "ks_p": _ks_p(dmax, n), "aic": 2 * len(p) - 2 * ll})
    return sorted(sortie, key=lambda r: r["aic"])
