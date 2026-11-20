"""SÉRIES TEMPORELLES — moteur pur (numpy + bibliothèque standard : aucune base, aucun Django, aucune dépendance ajoutée).

Entrée : des dates (texte ISO, date, datetime ou secondes) et des valeurs. Sortie : un dict JSON-able (voir `analyser`).
La chaîne complète s'exécute d'elle-même, selon le cas rencontré :
  1. préparation : dates lues en UTC, tri, fréquence détectée (heure, jour, semaine, mois, trimestre, année ou pas fixe), points d'une même
     période regroupés (moyenne, somme ou dernière valeur), trous comblés par interpolation linéaire (comptés et signalés) ;
  2. saisonnalité : période imposée, sinon période naturelle de la fréquence (12 par mois, 7 par jour…), sinon recherchée dans les données ;
     retenue seulement si elle explique au moins 25 % de la variance restante après retrait de la tendance ;
  3. modèles essayés : {moindres carrés, moyennes mobiles} x {additif, multiplicatif} x {tendance droite, tendance parabole} ;
       - moindres carrés : tendance ajustée sur la série, coefficients saisonniers tirés des écarts (additif) ou rapports (multiplicatif) à
         cette tendance, le tout repris jusqu'à stabilité (c'est la régression complète tendance + saison) ;
       - moyennes mobiles : moyenne mobile centrée d'ordre = période (2 x période si elle est paire) -> coefficients saisonniers -> série
         corrigée des variations saisonnières -> droite (ou parabole) des moindres carrés, qui seule permet de prolonger la tendance ;
  4. choix : chaque combinaison est validée sur les dernières valeurs mises de côté ; la plus juste est retenue (à erreur voisine, la plus
     simple) ; le multiplicatif n'est essayé que si toutes les valeurs sont strictement positives ;
  5. estimation : les `horizon` prochaines valeurs avec leur intervalle de prédiction (loi de Student, niveau `confiance`), tenant compte de
     l'incertitude de la droite qui grandit avec l'éloignement. L'incertitude des coefficients saisonniers n'y entre pas : l'intervalle est
     approché ; sa couverture réelle est mesurée sur les valeurs mises de côté et rendue dans `qualite.validation.couverture`.
  6. modèles alternatifs, mis en concurrence sur les mêmes valeurs mises de côté (le classique reste préféré à erreur voisine) :
       - Holt-Winters, additif (intervalle par poids de la forme ETS) ou multiplicatif (intervalle par simulation de 3000 trajectoires
         avec les équations mêmes du modèle, graine fixe) ; départ : saison mesurée autour de la droite du 1er cycle, pas autour de sa moyenne ;
         paramètres alpha/beta/gamma : grille grossière puis affinage continu de Nelder-Mead borné (jamais pire que la grille) ;
       - ARIMA : autorégressif + moyenne mobile (+ moyenne mobile saisonnière au retard de la période), différence simple et saisonnière
         choisies à variance minimale, estimation de Hannan-Rissanen reprise 3 fois puis affinée par moindres carrés conditionnels
         (Levenberg-Marquardt, stationnarité et inversibilité imposées à chaque pas), puis par VRAISEMBLANCE EXACTE (filtre de Kalman) quand la série
         différenciée a au plus 600 points et que l'état reste de dimension ≤ 24 (au-delà : conditionnelle, équivalente asymptotiquement), ordre par AICc ; variante multiplicative = même ARIMA sur
         le logarithme (estimation = moyenne log-normale, bornes remises à l'échelle) ;
       - si l'un d'eux l'emporte, prévision, ajustement, résidus, diagnostic ET tendance, équation et coefficients saisonniers sont les siens
         (Holt-Winters : niveau lissé et coefficients finaux ; ARIMA : décomposition de sa propre courbe ajustée) ;
  7. diagnostic des résidus et verdict de fiabilité (bonne / moyenne / faible) en langage courant.
  8. effets connus, AVANT les modèles (`_effets`) : rupture de tendance, variables explicatives, jours fériés (France ou liste donnée), saisonnalités
     secondaires (périodes naturelles autres que la principale, en harmoniques de Fourier ; une période principale de plus de 60 avec moins de 8
     cycles, 365 jours par exemple, est traitée elle aussi en harmoniques puis la période restante est cherchée sur le reste). Un seul mécanisme : régression de la série désaisonnalisée
     sur [1, t, t²] + le bloc ; le bloc n'est gardé que si le test F de Fisher est significatif à 1 % (corrigé du nombre de points de rupture essayés) ;
     les effets gardés sont retirés de la série, les modèles travaillent sur le reste, puis les effets (connus aussi dans l'avenir) sont remis dans
     l'ajustement et les estimations. Saison multiplicative : effets estimés sur le logarithme, combinés par produit (facteur exp(effet)).
     Ruptures : jusqu'à 3, cherchées l'une après l'autre (la recherche s'arrête à la première non significative). Jours fériés : 11 pays (PAYS_FERIES), leurs
     régions (REGIONS_FERIES : « DE-BY »), fêtes à date lunaire calculées (LUNAIRES : « FR+islam+juif+chine+orthodoxe », ±1 jour) ou liste de dates. Saisons libres :
     jusqu'à 3 périodes secondaires cherchées dans les données (périodogramme, hors harmoniques de la principale et des saisons déjà gardées), l'une après l'autre.
     La validation réestime les effets SANS les valeurs mises de côté, puis les extrapole sur elles : l'erreur mesurée est celle de la série entière.
  9. courbes de variation (`variations`) : évolution en % d'une période à l'autre, glissement sur un cycle, série corrigée des variations saisonnières
     (et des effets calendaires / saisonniers secondaires), toutes prolongées par les estimations.
 10. validation glissante (`glissante=n`) : la chaîne COMPLÈTE, choix du modèle compris, est relancée sur les données arrêtées à n origines successives,
     puis comparée à ce qui s'est réellement passé et à la prévision naïve (répéter le dernier cycle) ; chaque méthode est aussi classée seule
     (`par_methode`), ses prévisions étant lues dans le même passage (une seule chaîne par origine, pas une par méthode).
"""
from __future__ import annotations
from engine.i18n import td, dec
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t, tf, tl

import math
from datetime import date, datetime, timedelta, timezone
from statistics import NormalDist

import numpy as np

LIMITES = {"points": None, "horizon": 120, "periode": 366, "recherche_periode": 60, "saisons_libres": 3}
PARAMETRES = {"confiance": 0.95, "seuil_saison": 0.25, "alpha_saison": 0.01, "points_min": 8, "part_trous_max": 0.3, "tolerance_ecart": 1.05,
              "alpha_regresseur": 0.01, "plis_max": 6}
METHODES = {"mco": tl("engine/series_temporelles.py:g:58:20"), "mm": "Moyennes mobiles"}
ALTERNATIVES = {"hw": "Lissage exponentiel (Holt-Winters)", "arima": "ARIMA"}
_LIB = {**METHODES, **ALTERNATIVES}
_AGREGATS = ("moyenne", "somme", "dernier")
_PAS_USUELS = (60, 300, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800, 1209600)
_LIBELLES = {"mois": "mensuelle", "trimestre": "trimestrielle", "annee": "annuelle", "jour": "quotidienne", "semaine": "hebdomadaire",
             "heure": "horaire", "pas": tl("engine/series_temporelles.py:g:64:41")}
_UNITES = {"mois": tl("engine/series_temporelles.py:g:65:20"), "trimestre": tl("engine/series_temporelles.py:g:65:45"), "annee": tl("engine/series_temporelles.py:g:65:71"), "jour": tl("engine/series_temporelles.py:g:65:89"), "semaine": tl("engine/series_temporelles.py:g:65:112"),
           "heure": tl("engine/series_temporelles.py:g:66:21"), "pas": td("engine/series_temporelles.py:g:67:72")}
_MOIS = ("janv.", _gtfr("engine/series_temporelles.py:69:19"), "mars", "avr.", "mai", "juin", "juil.", _gtfr("engine/series_temporelles.py:69:69"), "sept.", "oct.", "nov.", _gtfr("engine/series_temporelles.py:69:103"))
_JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")


# ── Lecture des valeurs ──────────────────────────────────────────────────────
def _secondes(v):
    """Date -> secondes UTC, None si illisible. Une date sans fuseau est lue comme UTC : aucun décalage d'heure d'été."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, date):
        d = datetime(v.year, v.month, v.day)
    else:
        s, d = str(v).strip(), None
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            for f in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
                try:
                    d = datetime.strptime(s, f)
                    break
                except ValueError:
                    continue
        if d is None:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    try:
        return d.astimezone(timezone.utc).timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _reel(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(str(v).replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _nb(v, chiffres: int = 4) -> str:
    """Nombre à la française (virgule décimale, 4 chiffres significatifs)."""
    if v is None or not math.isfinite(v):
        return "—"
    s = f"{v:.{chiffres}g}"
    if "e" in s:
        s = f"{v:.0f}"
    return dec(s)


def _liste(a, nd: int = 6) -> list:
    return [None if not math.isfinite(float(v)) else round(float(v), nd) for v in a]


# ── Loi de Student (répartition exacte, sans scipy) ──────────────────────────
def _fraction_continue(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        c = 1.0 + aa / c
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _beta_incomplete(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    devant = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return devant * _fraction_continue(a, b, x) / a
    return 1.0 - devant * _fraction_continue(b, a, 1.0 - x) / b


def _repartition_t(t: float, df: float) -> float:
    p = 0.5 * _beta_incomplete(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - p if t > 0 else p


def quantile_t(q: float, df: float) -> float:
    """Quantile de la loi de Student à `df` degrés de liberté (dichotomie sur la répartition exacte)."""
    if q < 0.5:
        return -quantile_t(1.0 - q, df)
    if df > 1e6:
        return NormalDist().inv_cdf(q)
    bas, haut = 0.0, 1.0
    while _repartition_t(haut, df) < q and haut < 1e9:
        bas, haut = haut, haut * 2.0
    for _ in range(90):
        milieu = (bas + haut) / 2.0
        bas, haut = (milieu, haut) if _repartition_t(milieu, df) < q else (bas, milieu)
    return (bas + haut) / 2.0


# ── Préparation : fréquence, regroupement, trous ─────────────────────────────
def _frequence(ts):
    """(genre, pas en secondes) d'après l'écart médian entre dates distinctes."""
    d = np.diff(np.unique(ts))
    med = float(np.median(d)) if len(d) else 86400.0
    jours = med / 86400.0
    if 26 <= jours <= 33:
        return "mois", 0.0
    if 85 <= jours <= 97:
        return "trimestre", 0.0
    if 350 <= jours <= 380:
        return "annee", 0.0
    pas = float(next((p for p in _PAS_USUELS if abs(med - p) <= 0.1 * p), max(1.0, round(med))))
    return {3600.0: "heure", 86400.0: "jour", 604800.0: "semaine"}.get(pas, "pas"), pas


def _indice(t, genre, pas, t0):
    """Numéro de la période qui contient l'instant `t` (mois, trimestre et année : calendrier ; sinon pas fixe depuis la première date)."""
    if genre in ("mois", "trimestre", "annee"):
        d = datetime.fromtimestamp(t, timezone.utc)
        return d.year * 12 + d.month - 1 if genre == "mois" else (d.year * 4 + (d.month - 1) // 3 if genre == "trimestre" else d.year)
    return int(round((t - t0) / pas))


def _etiquette(i, genre, pas, t0):
    if genre == "mois":
        return f"{i // 12:04d}-{i % 12 + 1:02d}"
    if genre == "trimestre":
        return f"{i // 4:04d}-T{i % 4 + 1}"
    if genre == "annee":
        return f"{i:04d}"
    d = datetime.fromtimestamp(t0 + i * pas, timezone.utc)
    return d.strftime("%Y-%m-%d") if pas >= 86400 else d.strftime("%Y-%m-%d %H:%M")


def _preparer(dates, valeurs, agregat):
    """(y régulier, genre, pas, t0, i0, bilan). ValueError avec un message lisible si la série est inutilisable."""
    couples = []
    for d, v in zip(dates, valeurs):
        t, x = _secondes(d), _reel(v)
        if t is not None and x is not None and -2.2e9 < t < 7.3e9:
            couples.append((t, x))
    if len(couples) < PARAMETRES["points_min"]:
        raise ValueError(tf("engine/series_temporelles.py:g:228:26", v1=len(couples), v2=len(dates), v3=PARAMETRES['points_min']))
    couples.sort(key=lambda c: c[0])
    ts = np.array([c[0] for c in couples])
    genre, pas = _frequence(ts)
    t0 = float(ts[0])
    groupes: dict = {}
    for t, x in couples:
        groupes.setdefault(_indice(t, genre, pas, t0), []).append(x)
    i1 = max(groupes)
    i0 = min(groupes) if LIMITES["points"] is None else max(min(groupes), i1 - LIMITES["points"] + 1)   # None = toute la série, sans fenêtre
    i0 = min(i for i in groupes if i >= i0)
    grille = np.full(i1 - i0 + 1, np.nan)
    agr = {"moyenne": np.mean, "somme": np.sum, "dernier": lambda a: a[-1]}[agregat]
    for i, xs in groupes.items():
        if i >= i0:
            grille[i - i0] = agr(np.array(xs))
    trous = np.isnan(grille)
    n_trous = int(trous.sum())
    if n_trous:
        pos = np.arange(len(grille))
        grille[trous] = np.interp(pos[trous], pos[~trous], grille[~trous])
    retenus = sum(len(xs) for i, xs in groupes.items() if i >= i0)
    bilan = {"recus": len(dates), "ignores": len(dates) - len(couples), "regroupes": retenus - (len(grille) - n_trous), "interpoles": n_trous}
    return grille, genre, pas, t0, i0, bilan


# ── Saisonnalité ─────────────────────────────────────────────────────────────
def _naturelles(genre, pas):
    if genre == "pas" and pas < 86400 and 86400 % pas == 0:
        return [int(86400 / pas)] + ([int(604800 / pas)] if 604800 / pas <= LIMITES["periode"] else [])
    return {"mois": [12], "trimestre": [4], "jour": [7, 365], "heure": [24, 168], "semaine": [52]}.get(genre, [])


def _force_saison(y, p):
    """(force, p-valeur). Force = part de la variance (corrigée du nombre de phases) expliquée par les moyennes de phase, une fois la tendance
    parabolique retirée ; p-valeur = test F de Fisher « les phases ont toutes la même moyenne » (3 degrés de liberté rendus à la tendance)."""
    n = len(y)
    d1, d2 = p - 1, n - p - 3
    if p < 2 or n < 2 * p or d2 < 1:
        return 0.0, 1.0
    u = np.linspace(-1.0, 1.0, n)
    r = y - np.polyval(np.polyfit(u, y, 2), u)
    sst = float(np.sum((r - r.mean()) ** 2))
    if sst <= 1e-12 * max(1.0, float(np.sum(y ** 2))):
        return 0.0, 1.0
    ph = np.arange(n) % p
    moy = np.array([r[ph == k].mean() for k in range(p)])
    ssw = float(np.sum((r - moy[ph]) ** 2))
    force = max(0.0, 1.0 - (ssw / (n - p)) / (sst / (n - 1)))
    if ssw <= 1e-12 * sst:
        return force, 0.0
    F = ((sst - ssw) / d1) / (ssw / d2)
    return force, _beta_incomplete(d2 / 2.0, d1 / 2.0, d2 / (d2 + d1 * max(F, 0.0)))


def _detecter_periode(y, genre, pas):
    """(période, force, origine) ; période 1 = pas de saisonnalité. Une période n'est retenue que si elle explique au moins `seuil_saison` de la
    variance ET si le test F est significatif à 1 % (corrigé du nombre de périodes essayées quand elle est cherchée dans les données, qui
    exigent en plus trois cycles complets : sinon le hasard « trouve » des saisons dans un bruit court)."""
    n, seuil, alpha = len(y), PARAMETRES["seuil_saison"], PARAMETRES["alpha_saison"]
    nat = {p: _force_saison(y, p) for p in _naturelles(genre, pas) if 2 * p <= n}
    bons = [p for p, (f, pv) in nat.items() if f >= seuil and pv < alpha]
    if bons:
        p = max(bons, key=lambda q: nat[q][0])
        return p, nat[p][0], _gt("engine/series_temporelles.py:294:30")
    cand = {p: _force_saison(y, p) for p in range(2, min(n // 3, LIMITES["recherche_periode"]) + 1)}
    valides = {p: f for p, (f, pv) in cand.items() if f >= seuil and pv < alpha / max(1, len(cand))}
    if valides:
        meilleur = max(valides.values())
        p = min(q for q, f in valides.items() if f >= 0.9 * meilleur)
        return p, valides[p], _gt("engine/series_temporelles.py:300:31")
    return 1, max([f for f, _pv in [*cand.values(), *nat.values()]], default=0.0), None


def _lien_niveau_amplitude(y, p):
    """Corrélation, cycle par cycle, entre le niveau moyen et l'amplitude (écart-type) : forte = les écarts saisonniers grandissent avec le niveau."""
    m = len(y) // p
    if p < 2 or m < 3:
        return None
    blocs = y[len(y) - m * p:].reshape(m, p)
    niv, amp = blocs.mean(axis=1), blocs.std(axis=1)
    if niv.std() < 1e-12 or amp.std() < 1e-12:
        return None
    return float(np.corrcoef(niv, amp)[0, 1])


# ── Décomposition ────────────────────────────────────────────────────────────
def _moyenne_mobile(y, q):
    """Moyenne mobile centrée d'ordre q (q pair : 2 x q, extrémités de poids 1/2). NaN sur les bords qui n'ont pas assez de voisins."""
    n = len(y)
    w = np.ones(q) / q if q % 2 else np.concatenate(([0.5 / q], np.ones(q - 1) / q, [0.5 / q]))
    k = len(w) // 2
    sortie = np.full(n, np.nan)
    if n >= len(w):
        sortie[k:n - k] = np.convolve(y, w, mode="valid")
    return sortie


def _indices(ecart, p, mode):
    """Coefficient saisonnier de chaque phase : moyenne des écarts (additif, somme recentrée à 0) ou des rapports (multiplicatif, moyenne 1)."""
    ph = np.arange(len(ecart)) % p
    s = np.array([np.nanmean(ecart[ph == k]) if np.any(~np.isnan(ecart[ph == k])) else np.nan for k in range(p)])
    s = np.where(np.isnan(s), 0.0 if mode == "additif" else 1.0, s)
    return s - s.mean() if mode == "additif" else s / s.mean()


def _mco(u, d, deg):
    X = np.vander(u, deg + 1, increasing=True)
    beta = np.linalg.lstsq(X, d, rcond=None)[0]
    return beta, np.linalg.pinv(X.T @ X), X


def _ajuster(y, p, methode, modele, deg):
    """Une combinaison complète, ou None si elle est impossible (multiplicatif sur des valeurs <= 0, trop peu de points)."""
    n = len(y)
    mult = modele == "multiplicatif"
    saison = p > 1
    if mult and (not saison or np.any(y <= 0)):
        return None
    e, c = max(1.0, n / 2.0), (n - 1) / 2.0
    u = (np.arange(n) - c) / e
    ph = np.arange(n) % p
    s = np.ones(p) if mult else np.zeros(p)

    def corrige(coef):
        return y / coef[ph] if mult else y - coef[ph]

    if not saison:
        beta, inv, X = _mco(u, y, deg)
    elif methode == "mco":
        for _ in range(60):
            beta, inv, X = _mco(u, corrige(s), deg)
            T = X @ beta
            if mult and np.any(T <= 0):
                return None
            s_nouveau = _indices(y / T if mult else y - T, p, modele)
            delta = float(np.max(np.abs(s_nouveau - s)))
            s = s_nouveau
            if delta < 1e-10 * (1.0 + float(np.max(np.abs(y)))):
                break
        beta, inv, X = _mco(u, corrige(s), deg)
    else:
        T_mm = _moyenne_mobile(y, p)
        if mult and np.any(T_mm[~np.isnan(T_mm)] <= 0):
            return None
        s = _indices(y / T_mm if mult else y - T_mm, p, modele)
        beta, inv, X = _mco(u, corrige(s), deg)
    T = X @ beta
    ajuste = T * s[ph] if mult else T + s[ph]
    df = n - (deg + 1) - (p - 1 if saison else 0)
    if df < 1:
        return None
    sigma = math.sqrt(float(np.sum((corrige(s) - T) ** 2)) / df)
    return {"methode": methode, "modele": modele if saison else "aucun", "degre": deg, "p": p, "n": n, "beta": beta, "inv": inv, "s": s, "e": e,
            "c": c, "df": df, "sigma": sigma, "tendance": T, "ajuste": ajuste, "mult": mult,
            "rmse_ajustement": math.sqrt(float(np.sum((y - ajuste) ** 2)) / df)}


def _predire(m, h, confiance):
    """(estimation, borne basse, borne haute) des h périodes suivantes."""
    i = np.arange(m["n"], m["n"] + h)
    X = np.vander((i - m["c"]) / m["e"], m["degre"] + 1, increasing=True)
    T = X @ m["beta"]
    marge = quantile_t(1.0 - (1.0 - confiance) / 2.0, m["df"]) * m["sigma"] * np.sqrt(1.0 + np.einsum("ij,jk,ik->i", X, m["inv"], X))
    s = m["s"][i % m["p"]]
    if m["mult"]:
        return T * s, (T - marge) * s, (T + marge) * s
    return T + s, T + s - marge, T + s + marge


def _valider(y, p, methode, modele, deg, k, confiance, e=None, rel=False):
    """Ajuste sur y[:-k], prédit les k dernières valeurs et mesure l'écart. `e` = effets connus estimés SANS ces k valeurs (remis dans la prévision
    et dans la valeur réelle : l'erreur est celle de la série complète, effets compris)."""
    m = _ajuster(y[:-k], p, methode, modele, deg)
    if m is None:
        return None
    est, bas, haut = _predire(m, k, confiance)
    reel = y[-k:]
    if e is not None:
        reel, est, bas, haut = (_rem(a, e, rel) for a in (reel, est, bas, haut))
    err = reel - est
    utile = np.abs(reel) > 1e-12
    return {"k": k, "rmse": math.sqrt(float(np.mean(err ** 2))), "mae": float(np.mean(np.abs(err))),
            "mape": float(np.mean(np.abs(err[utile] / reel[utile])) * 100.0) if utile.all() else None,
            "couverture": float(np.mean((reel >= bas) & (reel <= haut)))}


def _taille_validation(n, p, horizon):
    k = min(max(horizon, p if p > 1 else 0), n // 4)
    while k >= 2 and n - k < 2 * p + 3:
        k -= 1
    return k if k >= 2 else 0


# ── Modèles alternatifs : lissage exponentiel (Holt-Winters) et ARIMA autorégressif ──────────────────────────────────────────────
def _hw_passe(y, p, mult, a, b, g):
    """Une passe de Holt-Winters (niveau, pente, saison) : erreurs à un pas, état final et niveau à chaque date. None si le calcul diverge."""
    n, saison = len(y), p > 1
    if saison:      # départ : niveau moyen du 1er cycle, pente entre les 2 premiers cycles, saison = écart à la droite (et non à la moyenne)
        moy = float(np.mean(y[:p]))
        pente = (float(np.mean(y[p:2 * p])) - moy) / p
        droite = moy + pente * (np.arange(p) - (p - 1) / 2.0)
        if mult and np.any(droite <= 1e-12):
            droite = np.full(p, moy)
        s = list(y[:p] / droite if mult else y[:p] - droite)
        niv = moy - pente * ((p - 1) / 2.0 + 1.0)       # niveau « avant » la première date : niv + pente redonne la droite en 0
    else:
        k = min(6, n)
        pente, origine = (float(v) for v in np.polyfit(np.arange(k), y[:k], 1))
        niv, s = origine - pente, []
    e, niveaux = np.empty(n), np.empty(n)
    for i in range(n):
        sc = s[i % p] if saison else (1.0 if mult else 0.0)
        pas = niv + pente
        e[i] = y[i] - (pas * sc if mult else pas + sc)
        nouveau = a * (y[i] / sc if mult else y[i] - sc) + (1 - a) * pas
        pente = b * (nouveau - niv) + (1 - b) * pente
        if saison:
            s[i % p] = g * (y[i] / pas if mult else y[i] - pas) + (1 - g) * sc
        niv = nouveau
        niveaux[i] = niv
    return (e, niv, pente, s, niveaux) if np.all(np.isfinite(e)) and np.isfinite(niv) else None


def _nelder_mead(f, x0, bas, haut, evaluations):
    """Minimisation de Nelder-Mead bornée (les points sont ramenés dans [bas, haut]) : aucune dépendance, quelques dizaines d'évaluations."""
    bas, haut = np.asarray(bas, float), np.asarray(haut, float)
    n = len(x0)
    borne = lambda v: np.minimum(np.maximum(v, bas), haut)
    pts = [borne(np.asarray(x0, float))]
    for i in range(n):
        v = pts[0].copy()
        v[i] += 0.15 * (haut[i] - bas[i]) * (1.0 if v[i] + 0.15 * (haut[i] - bas[i]) <= haut[i] else -1.0)
        pts.append(borne(v))
    vals = [f(v) for v in pts]
    n_eval = len(pts)
    while n_eval < evaluations:
        ordre = np.argsort(vals)
        pts, vals = [pts[i] for i in ordre], [vals[i] for i in ordre]
        if abs(vals[-1] - vals[0]) <= 1e-10 * (abs(vals[0]) + 1e-300) or float(np.max(np.abs(pts[-1] - pts[0]))) < 1e-4:
            break
        centre = np.mean(pts[:-1], axis=0)
        refl = borne(centre + (centre - pts[-1]))
        fr = f(refl)
        n_eval += 1
        if fr < vals[0]:
            exp_ = borne(centre + 2.0 * (centre - pts[-1]))
            fe = f(exp_)
            n_eval += 1
            pts[-1], vals[-1] = (exp_, fe) if fe < fr else (refl, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = refl, fr
        else:
            con = borne(centre + 0.5 * ((refl if fr < vals[-1] else pts[-1]) - centre))
            fc = f(con)
            n_eval += 1
            if fc < min(fr, vals[-1]):
                pts[-1], vals[-1] = con, fc
            else:
                for i in range(1, n + 1):
                    pts[i] = borne(pts[0] + 0.5 * (pts[i] - pts[0]))
                    vals[i] = f(pts[i])
                    n_eval += 1
    i = int(np.argmin(vals))
    return pts[i], vals[i]


_SIMULATIONS_HW = 3000
_GRAINE_HW = 20260930


def _hw_simuler(niv, pente, sais, n, p, a, b, g, sigma, df, h, confiance, est):
    """Intervalle de Holt-Winters multiplicatif : `_SIMULATIONS_HW` trajectoires futures tirées avec les équations mêmes du modèle (l'erreur
    est proportionnelle au niveau, elle se propage dans le niveau, la pente et la saison), graine fixe donc résultat reproductible. Les bornes
    sont les centiles de ces trajectoires : plus de formule approchée."""
    rng = np.random.default_rng(_GRAINE_HW)
    N = _SIMULATIONS_HW
    S = np.tile(np.asarray(sais, float), (N, 1))
    niv_, pente_ = np.full(N, float(niv)), np.full(N, float(pente))
    sortie = np.empty((h, N))
    eps = rng.standard_t(df, size=(h, N)) * math.sqrt((df - 2.0) / df) if df > 2 else rng.normal(size=(h, N))   # variance 1, queues de Student
    garde = lambda v: np.where(np.abs(v) > 1e-12, v, 1e-12)
    for k in range(1, h + 1):
        idx = (n + k - 1) % p
        sc = S[:, idx]
        pas = niv_ + pente_
        y_ = pas * sc * (1.0 + sigma * eps[k - 1])
        sortie[k - 1] = y_
        nouveau = a * (y_ / garde(sc)) + (1.0 - a) * pas
        pente_ = b * (nouveau - niv_) + (1.0 - b) * pente_
        S[:, idx] = g * (y_ / garde(pas)) + (1.0 - g) * sc
        niv_ = nouveau
    alpha = (1.0 - confiance) / 2.0
    bas, haut = np.quantile(sortie, [alpha, 1.0 - alpha], axis=1)
    return np.minimum(bas, est), np.maximum(haut, est)


def _hw_ajuster(y, p, mult, confiance):
    """Holt-Winters : paramètres choisis sur une grille (erreur à un pas minimale). Intervalle par poids de la forme ETS (additif) ou par
    simulation des trajectoires (multiplicatif)."""
    n, saison = len(y), p > 1
    if n < (2 * p + 2 if saison else 6) or (mult and (not saison or np.any(y <= 0))):
        return None
    court = n > 600
    grille = [(a, b, g) for a in ((0.2, 0.6) if court else (0.1, 0.3, 0.5, 0.8)) for b in ((0.02,) if court else (0.0, 0.05, 0.15))
              for g in (((0.1, 0.4) if court else (0.05, 0.2, 0.5)) if saison else (0.0,))]
    def critere(r):
        """Somme des carrés des erreurs à un pas (relatives au niveau prédit si le modèle est multiplicatif : l'erreur est proportionnelle)."""
        e_ = r[0] / np.where(np.abs(y - r[0]) > 1e-12, y - r[0], 1e-12) if mult else r[0]
        return float(e_ @ e_)
    best = None
    for a, b, g in grille:      # 1) grille grossière : point de départ
        r = _hw_passe(y, p, mult, a, b, g)
        if r is not None and (best is None or critere(r) < best[0]):
            best = (critere(r), a, b, g, r)
    if best is None:
        return None
    # 2) affinage continu (Nelder-Mead, bornes alpha 0,01-0,99 ; beta 0-0,5 ; gamma 0-1) autour du meilleur point de la grille
    libres = [0, 1] + ([2] if saison else [])

    def objectif(v):
        r_ = _hw_passe(y, p, mult, float(v[0]), float(v[1]), float(v[2]) if saison else 0.0)
        return critere(r_) if r_ is not None else 1e300
    x0 = np.array([best[1], best[2], best[3]])[libres]
    x1, v1 = _nelder_mead(objectif, x0, np.array([0.01, 0.0, 0.0])[libres], np.array([0.99, 0.5, 1.0])[libres], 30 if court else 90)
    if v1 < best[0]:
        a1, b1, g1 = float(x1[0]), float(x1[1]), float(x1[2]) if saison else 0.0
        r1 = _hw_passe(y, p, mult, a1, b1, g1)
        if r1 is not None:
            best = (v1, a1, b1, g1, r1)
    _, a, b, g, (e, niv, pente, sais, niveaux) = best
    df = max(1, n - (p if saison else 0) - 4)
    ajuste = y - e
    rel = e / np.where(np.abs(ajuste) > 1e-12, ajuste, 1e-12) if mult else e
    sigma = math.sqrt(float(rel @ rel) / df)

    def predire(h):
        j = np.arange(1, h + 1)
        base = niv + j * pente
        sc = np.array([sais[(n + k - 1) % p] for k in j]) if saison else (1.0 if mult else 0.0)
        est = base * sc if mult else base + sc
        if mult:
            bas, haut = _hw_simuler(niv, pente, sais, n, p, a, b, g, sigma, df, h, confiance, est)
            return est, bas, haut
        c = a * (1 + np.arange(1, h) * b) + (g * (np.arange(1, h) % p == 0) if saison else 0.0)
        marge = quantile_t(1.0 - (1.0 - confiance) / 2.0, df) * sigma * np.sqrt(1.0 + np.concatenate(([0.0], np.cumsum(c ** 2))))
        return est, est - marge, est + marge
    saison_finale, niveaux_c = None, niveaux
    if saison:
        sf = np.asarray(sais, float)
        mu = float(sf.mean())
        if mult and abs(mu) > 1e-12:
            saison_finale, niveaux_c = sf / mu, niveaux * mu
        else:
            saison_finale, niveaux_c = sf - mu, niveaux + mu
    return {"ajuste": ajuste, "sigma": sigma, "df": df, "predire": predire, "modele": "multiplicatif" if mult else ("additif" if saison else "aucun"),
            "relatif": mult, "tendance": niveaux_c, "saison": saison_finale, "parametres": {"alpha": a, "beta": b, "gamma": g if saison else None}}


# ── ARIMA complet : autorégressif + moyenne mobile (+ terme de moyenne mobile saisonnier), sur la série ou sur son logarithme ──────
def _retard(v, j, t0):
    """v[t - j] pour t = t0..len(v)-1, complété par 0 avant le début."""
    m = len(v)
    sortie = np.zeros(m - t0)
    debut = max(t0, j)
    if debut < m:
        sortie[debut - t0:] = v[debut - j:m - j]
    return sortie


try:
    from scipy.signal import lfilter as _lfilter   # facultatif : sans scipy, repli sur la boucle Python (résultats identiques)
except Exception:  # pragma: no cover
    _lfilter = None


def _innovations(w, c, phi, theta, lags_ma):
    """Erreurs à un pas d'un ARMA sur w (conditionnel : les erreurs d'avant le premier point utilisable valent 0)."""
    m, pp = len(w), len(phi)
    u = w - c
    for i, f in enumerate(phi, 1):
        u = u - f * _retard(w, i, 0)
    e = np.zeros(m)
    if not len(theta):
        e[pp:] = u[pp:]
        return e
    if _lfilter is not None and m - pp > 64:
        # Même récurrence e[t] = u[t] - somme(th_j * e[t-j]), calculée en C (filtre récursif) : sur une longue série la boucle Python
        # ci-dessous, appelée des milliers de fois par l'ajustement ARIMA, dépassait le délai du serveur (worker tué).
        den = np.zeros(max(lags_ma) + 1)
        den[0] = 1.0
        for j, th in zip(lags_ma, theta):
            den[j] += th
        e[pp:] = _lfilter([1.0], den, u[pp:])
        return e
    for t in range(pp, m):
        v = u[t]
        for j, th in zip(lags_ma, theta):
            if t - j >= 0:
                v -= th * e[t - j]
        e[t] = v
    return e


def _racines_ok(coefs):
    """Vrai si toutes les racines du polynôme z^k + coefs[0] z^(k-1) + ... sont dans le disque unité (stationnarité de l'AR, inversibilité de la MA)."""
    if not len(coefs):
        return True
    r = np.roots(np.concatenate(([1.0], coefs)))
    return bool(np.all(np.isfinite(r)) and float(np.max(np.abs(r))) < 0.995)


def _kalman_residus(x, phi, theta, lags_ma):
    """Erreurs à un pas NORMALISÉES d'un ARMA centré x par le filtre de Kalman (forme d'état de Harvey, état initial = variance stationnaire par
    doublement) : leur somme des carrés est, à une transformation croissante près, moins deux fois la log-vraisemblance gaussienne EXACTE
    concentrée (n log(Σ v²/f) + Σ log f), donc minimisable par moindres carrés. None si le filtre échoue (non stationnaire)."""
    phi, m = np.asarray(phi, float), len(x)
    r = max(len(phi), (max(lags_ma) if lags_ma else 0) + 1)
    T, R = np.zeros((r, r)), np.zeros(r)
    T[:len(phi), 0] = phi
    T[np.arange(r - 1), np.arange(1, r)] = 1.0
    R[0] = 1.0
    for j, th in zip(lags_ma, theta):
        R[j] = th
    RR, A, P = np.outer(R, R), T.copy(), np.outer(R, R)
    for _ in range(60):      # P = T P T' + R R' par doublement : P <- P + A P A', A <- A²
        P2 = P + A @ P @ A.T
        A = A @ A
        fini = float(np.abs(P2 - P).max()) <= 1e-14 * float(np.abs(P2).max())
        P = P2
        if fini or not np.all(np.isfinite(P)):
            break
    a, K, fige = np.zeros(r), np.zeros(r), False
    v, f = np.empty(m), np.empty(m)
    for t in range(m):
        ft = float(P[0, 0])
        if not (ft > 1e-9 and np.isfinite(ft)):
            return None
        if not fige:
            K = (T @ P[:, 0]) / ft
        v[t], f[t] = x[t] - a[0], ft
        a = T @ a + K * v[t]
        if not fige:      # une fois la covariance stable, le gain l'est aussi : le reste de la série ne coûte plus qu'un produit matrice-vecteur
            Pn = T @ P @ T.T + RR - np.outer(K, K) * ft
            fige = float(np.abs(Pn - P).max()) < 1e-12 * ft
            P = Pn
    return v / np.sqrt(f) * math.exp(0.5 * float(np.mean(np.log(f))))


def _affiner_css(w, c, phi, theta, lags_ma, exacte=False):
    """Affine les paramètres ARMA par moindres carrés conditionnels (somme des carrés des erreurs à un pas) : Levenberg-Marquardt, jacobien
    numérique, chaque pas doit garder l'AR stationnaire et la MA inversible et faire baisser la somme des carrés. Remplace l'estimation en
    deux étapes (Hannan-Rissanen) par son point d'arrivée exact ; équivalent asymptotique du maximum de vraisemblance. Avec `exacte`, le critère est la vraisemblance gaussienne EXACTE
    (filtre de Kalman, `_kalman_residus`) au lieu de la somme des carrés conditionnelle ; 8 itérations au plus, départ = l'optimum conditionnel."""
    pp, q = len(phi), len(theta)
    x = np.concatenate(([c], phi, theta)).astype(float)
    if len(x) == 1:
        return c, phi, theta

    def valide(v):
        ma = np.zeros(max(lags_ma) if lags_ma else 0)
        for j, th in zip(lags_ma, v[1 + pp:]):
            ma[j - 1] = th
        return _racines_ok(-v[1:1 + pp]) and _racines_ok(ma)

    def residus(v):
        if not exacte:
            return _innovations(w, float(v[0]), v[1:1 + pp], v[1 + pp:], lags_ma)[pp:]
        s = 1.0 - float(np.sum(v[1:1 + pp]))      # moyenne du processus = constante / (1 - somme des coefficients AR)
        e = _kalman_residus(w - v[0] / s, v[1:1 + pp], v[1 + pp:], lags_ma) if abs(s) > 1e-6 else None
        return e if e is not None else np.full(len(w), np.inf)
    r = residus(x)
    if not np.all(np.isfinite(r)):
        return c, phi, theta
    sse, lam = float(r @ r), 1e-2
    for _ in range(8 if exacte else 25):
        J = np.empty((len(r), len(x)))
        for i in range(len(x)):
            h = 1e-6 * max(1.0, abs(x[i]))
            xp = x.copy()
            xp[i] += h
            col = (residus(xp) - r) / h
            J[:, i] = np.where(np.isfinite(col), col, 0.0)
        A, g = J.T @ J, J.T @ r
        amelioration = False
        for _ in range(8):
            try:
                pas = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), -g)
            except np.linalg.LinAlgError:
                lam *= 10.0
                continue
            essai = x + pas
            if valide(essai):
                r2 = residus(essai)
                sse2 = float(r2 @ r2)
                if np.isfinite(sse2) and sse2 < sse:
                    gain = (sse - sse2) / max(sse, 1e-300)
                    x, r, sse, lam, amelioration = essai, r2, sse2, max(lam / 5.0, 1e-9), True
                    break
            lam *= 10.0
        if not amelioration or gain < 1e-9:
            break
    return float(x[0]), x[1:1 + pp], x[1 + pp:]


def _arima_z(z, p, confiance):
    """ARIMA(p,d,q) avec, si la série est saisonnière, différence saisonnière et terme de moyenne mobile saisonnier au retard p.
    Estimation de Hannan-Rissanen : autorégression longue -> erreurs estimées -> régression sur les retards de la série et de ces erreurs,
    reprise 3 fois avec les erreurs recalculées par le modèle lui-même. Ordre par AICc parmi les spécifications stationnaires et inversibles."""
    n = len(z)
    saison = p > 1 and n >= 3 * p
    essais = [(0, 0), (1, 0)] + ([(0, 1), (1, 1)] if saison else [])
    choix = None
    for d, D in essais:
        op = np.convolve([1.0, -1.0] if d else [1.0], ([1.0] + [0.0] * (p - 1) + [-1.0]) if D else [1.0])
        if n - len(op) < 20:
            continue
        w = np.convolve(z, op)[len(op) - 1:n]
        if choix is None or float(np.var(w)) < choix[0] * 0.9:
            choix = (float(np.var(w)), d, D, op, w)
    if choix is None:
        return None
    _, d, D, op, w = choix
    m, decal = len(w), len(op) - 1
    if float(np.var(w)) < 1e-24:
        return None
    pmax = max(0, min(p + 1 if saison else 4, 14, (m - 8) // 6))
    L = min(max(8, pmax + 2), 40, (m - 4) // 3)
    pmax = min(pmax, L)
    if L < 3 or m - L < 12:
        return None
    X = np.column_stack([np.ones(m - L)] + [_retard(w, i, L) for i in range(1, L + 1)])
    b0, *_ = np.linalg.lstsq(X, w[L:], rcond=None)
    e0 = np.zeros(m)
    e0[L:] = w[L:] - X @ b0
    sma_ok = saison and p > 3
    lignes = m - L

    def regresser(pp, q, sma, e):
        cols = [np.ones(lignes)] + [_retard(w, i, L) for i in range(1, pp + 1)] + [_retard(e, j, L) for j in range(1, q + 1)] \
               + ([_retard(e, p, L)] if sma else [])
        A = np.column_stack(cols)
        beta, *_ = np.linalg.lstsq(A, w[L:], rcond=None)
        return beta, float(np.sum((w[L:] - A @ beta) ** 2))

    classement = []
    for pp in range(0, pmax + 1):
        for q in range(0, 4):
            for sma in ((0, 1) if sma_ok else (0,)):
                k = 1 + pp + q + sma
                if lignes - k < 6:
                    continue
                _, sse = regresser(pp, q, sma, e0)
                aicc = lignes * math.log(max(sse, 1e-300) / lignes) + 2 * k * lignes / (lignes - k - 1)
                classement.append((aicc, pp, q, sma))
    classement.sort()
    retenu = None
    for _, pp, q, sma in classement[:12] + [(0.0, 1, 0, 0)]:
        lags_ma = list(range(1, q + 1)) + ([p] if sma else [])
        e, dernier = e0, None
        for _ in range(4):
            beta, _s = regresser(pp, q, sma, e)
            c, phi, theta = float(beta[0]), beta[1:1 + pp], beta[1 + pp:]
            coef_ma = np.zeros(max(lags_ma) if lags_ma else 0)
            for j, th in zip(lags_ma, theta):
                coef_ma[j - 1] = th
            if not (_racines_ok(-phi) and _racines_ok(coef_ma)):
                break
            e_nouveau = _innovations(w, c, phi, theta, lags_ma)
            if not np.all(np.isfinite(e_nouveau)) or float(np.max(np.abs(e_nouveau))) > 1e3 * (float(np.std(w)) + 1e-12):
                break
            dernier, e = (c, phi, theta, e_nouveau), e_nouveau
        if dernier is not None:
            retenu = (pp, q, sma, *dernier[:3], lags_ma, dernier[3])
            break
    if retenu is None:
        return None
    pp, q, sma, c, phi, theta, lags_ma, e = retenu
    c, phi, theta = _affiner_css(w, c, phi, theta, lags_ma)
    exacte = len(w) <= 600 and max(pp, (max(lags_ma) if lags_ma else 0) + 1) <= 24
    if exacte:      # vraisemblance exacte tant que le filtre reste petit ; au-delà, la conditionnelle (équivalente asymptotiquement)
        c, phi, theta = _affiner_css(w, c, phi, theta, lags_ma, True)
    e = _innovations(w, c, phi, theta, lags_ma)
    sse = float(np.sum(e[pp:] ** 2))
    df = max(1, (m - pp) - (1 + pp + q + sma))
    sigma = math.sqrt(sse / df)
    Phi = -np.convolve(np.concatenate(([1.0], -phi)), op)[1:]
    th_arr = np.zeros((max(lags_ma) if lags_ma else 0) + 1)
    th_arr[0] = 1.0
    for j, th in zip(lags_ma, theta):
        th_arr[j] = th
    ajuste = np.full(n, np.nan)
    ajuste[decal + pp:] = z[decal + pp:] - e[pp:]

    def predire(h, variance=False):
        w_ext, e_ext, z_ext = list(w), list(e), list(z)
        for _ in range(h):
            v = c + sum(phi[i - 1] * w_ext[-i] for i in range(1, pp + 1)) + sum(th * e_ext[-j] for j, th in zip(lags_ma, theta) if j <= len(e_ext))
            w_ext.append(v)
            e_ext.append(0.0)
            z_ext.append(v - sum(op[i] * z_ext[-i] for i in range(1, len(op))))
        est = np.array(z_ext[-h:])
        psi = [1.0]
        for j in range(1, h):
            psi.append((th_arr[j] if j < len(th_arr) else 0.0) + sum(Phi[i - 1] * psi[j - i] for i in range(1, min(j, len(Phi)) + 1)))
        var = sigma ** 2 * np.cumsum(np.array(psi) ** 2)
        marge = quantile_t(1.0 - (1.0 - confiance) / 2.0, df) * np.sqrt(var)
        return (est, est - marge, est + marge, var) if variance else (est, est - marge, est + marge)
    nom = f"ARIMA({pp},{d},{q})" + (tf("engine/series_temporelles.py:g:822:37", v1=p) + (_i18n_t("engine/series_temporelles.py:g:822:73") if sma else "") + ")" if D else
                                    (_i18n_t("engine/series_temporelles.py:g:823:38") if sma else ""))
    return {"ajuste": ajuste, "sigma": sigma, "df": df, "predire": predire, "modele": nom, "ordre": (pp, d, q), "saisonnier": bool(D), "sma": bool(sma), "exacte": bool(exacte)}


def _arima_ajuster(y, p, confiance, mult=False):
    """ARIMA sur la série (additif) ou sur son logarithme (multiplicatif : valeurs strictement positives). Sur le logarithme, l'estimation est
    la MOYENNE de la loi log-normale (exp(µ + variance/2), et non la médiane exp(µ)) ; les bornes sont celles de l'intervalle du logarithme
    remises à l'échelle, et l'estimation est ramenée dans ses bornes si la variance est très grande."""
    y = np.asarray(y, float)
    if not mult:
        r = _arima_z(y, p, confiance)
        return {**r, "relatif": False, "tendance": None, "saison": None} if r else None
    if np.any(y <= 0):
        return None
    r = _arima_z(np.log(y), p, confiance)
    if r is None:
        return None
    predire_log = r["predire"]

    def predire(h):
        mu, bas, haut, var = predire_log(h, variance=True)
        bas, haut = np.exp(bas), np.exp(haut)
        return np.clip(np.exp(mu + var / 2.0), bas, haut), bas, haut
    return {**r, "ajuste": np.exp(r["ajuste"] + r["sigma"] ** 2 / 2.0), "predire": predire, "modele": r["modele"] + td("engine/series_temporelles.py:g:846:117"), "relatif": True, "tendance": None,
            "saison": None}


def _decrire_alternative(alt, y, p, deg):
    """Tendance, coefficients saisonniers et équation propres au modèle alternatif retenu (et non ceux de la décomposition classique).
    Holt-Winters : son niveau lissé et ses coefficients saisonniers finaux. ARIMA : décomposition (tendance + saison, mêmes équations que le
    classique) de sa propre courbe ajustée, complétée par les valeurs observées là où le modèle n'a pas encore d'historique.
    Renvoie un dict compatible avec `_equation` ou None."""
    n = len(y)
    mult = bool(alt.get("relatif"))
    saison = p > 1
    if alt.get("tendance") is not None:
        T, s = np.asarray(alt["tendance"], float), alt.get("saison")
        e, c = max(1.0, n / 2.0), (n - 1) / 2.0
        beta, inv, _ = _mco((np.arange(n) - c) / e, T, deg)
        return {"n": n, "e": e, "c": c, "beta": beta, "inv": inv, "degre": deg, "tendance": T, "s": s, "mult": mult, "p": p}
    z = np.where(np.isfinite(alt["ajuste"]), alt["ajuste"], y)
    d = _ajuster(z, p, "mco", "multiplicatif" if mult and saison else "additif", deg)
    if d is None:
        return None
    return {**d, "s": d["s"] if saison else None, "mult": mult}


def _valider_alt(fit, y, p, k, confiance, e=None, rel=False):
    """Même validation que `_valider` : ajustement sur y[:-k], estimation des k dernières valeurs mises de côté."""
    m = fit(y[:-k])
    if m is None:
        return None
    est, bas, haut = m["predire"](k)
    reel = y[-k:]
    if e is not None:
        reel, est, bas, haut = (_rem(a, e, rel) for a in (reel, est, bas, haut))
    err = reel - est
    utile = np.abs(reel) > 1e-12
    return {"k": k, "rmse": math.sqrt(float(np.mean(err ** 2))), "mae": float(np.mean(np.abs(err))),
            "mape": float(np.mean(np.abs(err[utile] / reel[utile])) * 100.0) if utile.all() else None,
            "couverture": float(np.mean((reel >= bas) & (reel <= haut)))}


# ── Diagnostic des résidus (l'intervalle de confiance n'est fiable que si les résidus ressemblent à du bruit) ─
def _gamma_q(a, x):
    """Loi du khi-deux : part de probabilité au-delà de x (gamma incomplète régularisée supérieure, sans scipy)."""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        terme = somme = 1.0 / a
        for k in range(1, 500):
            terme *= x / (a + k)
            somme += terme
            if terme < somme * 1e-14:
                break
        return max(0.0, 1.0 - somme * math.exp(-x + a * math.log(x) - math.lgamma(a)))
    b, c, d = x + 1.0 - a, 1e300, 1.0 / (x + 1.0 - a)
    h = d
    for k in range(1, 500):
        an = -k * (k - a)
        b += 2.0
        d = 1.0 / (an * d + b if abs(an * d + b) > 1e-300 else 1e-300)
        c = b + an / c if abs(b + an / c) > 1e-300 else 1e-300
        h *= d * c
        if abs(d * c - 1.0) < 1e-14:
            break
    return min(1.0, h * math.exp(-x + a * math.log(x) - math.lgamma(a)))


def _acf_pacf(r, h):
    """Autocorrélations et autocorrélations partielles (Durbin-Levinson) des retards 1..h."""
    r = r - r.mean()
    v = float(r @ r)
    if v < 1e-300:
        return [0.0] * h, [0.0] * h
    rho = np.array([float(r[:-k] @ r[k:]) / v for k in range(1, h + 1)])
    phi, pacf = np.zeros(h), []
    for k in range(h):
        pk = (rho[0] if k == 0 else (rho[k] - phi[:k] @ rho[:k][::-1]) / (1.0 - phi[:k] @ rho[:k]))
        phi[:k] = phi[:k] - pk * phi[:k][::-1]
        phi[k] = pk
        pacf.append(float(pk))
    return _liste(rho), pacf


def _dickey_fuller(y):
    """Test de Dickey-Fuller augmenté (constante, retards de différences) ; seuils de MacKinnon. None si trop peu de points."""
    n = len(y)
    lag = min(int(round(n ** (1.0 / 3.0))), (n - 4) // 3)
    if lag < 0 or n - lag - 1 < lag + 6:
        return None
    dy = np.diff(y)
    cible = dy[lag:]
    X = np.column_stack([np.ones(len(cible)), y[lag:-1]] + [dy[lag - j:len(dy) - j] for j in range(1, lag + 1)])
    try:
        beta, *_ = np.linalg.lstsq(X, cible, rcond=None)
        inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    ddl = len(cible) - X.shape[1]
    s2 = float(np.sum((cible - X @ beta) ** 2)) / ddl if ddl > 0 else 0.0
    se = math.sqrt(max(s2 * inv[1, 1], 0.0))
    if se < 1e-300:
        return None
    m = len(cible)
    seuils = {"1%": -3.4336 - 5.999 / m - 29.25 / m ** 2, "5%": -2.8621 - 2.738 / m - 8.36 / m ** 2, "10%": -2.5671 - 1.438 / m - 4.48 / m ** 2}
    stat = float(beta[1]) / se
    return {"statistique": stat, "seuils": {k: round(v, 3) for k, v in seuils.items()}, "retards": lag, "stationnaire": stat < seuils["5%"]}


def _diagnostiquer(y, res, p, x):
    """Diagnostic des résidus `res` de la série `y` : autocorrélation, blancheur (Ljung-Box), normalité (Jarque-Bera), stationnarité,
    valeurs aberrantes ; verdict de fiabilité en langage courant."""
    ok = np.flatnonzero(np.isfinite(res))
    res_complet, res = res, res[ok]
    n = len(res)
    h = max(1, min(max(10, 2 * p), n // 3))
    acf, pacf = _acf_pacf(res, h)
    q = n * (n + 2) * sum(a * a / (n - k) for k, a in enumerate(acf, 1))
    p_lb = _gamma_q(h / 2.0, q / 2.0)
    sigma = float(np.std(res))
    z = (res - res.mean()) / sigma if sigma > 1e-12 else np.zeros(n)
    asym, kurt = float(np.mean(z ** 3)), float(np.mean(z ** 4))
    jb = n / 6.0 * (asym ** 2 + (kurt - 3.0) ** 2 / 4.0)
    p_jb = math.exp(-jb / 2.0)
    med = float(np.median(res))
    mad = 1.4826 * float(np.median(np.abs(res - med)))
    ref = mad if mad > 1e-12 else sigma
    aberr = [j for j in range(n) if ref > 1e-12 and abs(res[j] - med) > 3.5 * ref][:10]
    df = _dickey_fuller(y)
    blanc, normal = p_lb > 0.05, p_jb > 0.05
    phrases = [_i18n_t("engine/series_temporelles.py:g:974:16")
               if blanc else _i18n_t("engine/series_temporelles.py:g:975:30")]
    phrases.append(_i18n_t("engine/series_temporelles.py:g:977:20") if normal else
                   _i18n_t("engine/series_temporelles.py:g:978:20"))
    if aberr:
        phrases.append(tf("engine/series_temporelles.py:g:980:24", v1=len(aberr)) + ", ".join(x[ok[j]] for j in aberr[:3]) + ("…" if len(aberr) > 3 else "") + _i18n_t("engine/series_temporelles.py:g:980:162"))
    if df is not None:
        phrases.append(_i18n_t("engine/series_temporelles.py:g:982:24") if df["stationnaire"] else
                       _i18n_t("engine/series_temporelles.py:g:983:24"))
    points = int(blanc) + int(normal) + int(not aberr)
    niveau = "bonne" if blanc and points == 3 else "moyenne" if blanc or points >= 2 else "faible"
    return {"autocorrelation": {"acf": acf, "pacf": pacf, "retards": h, "seuil": 1.96 / math.sqrt(n)},
            "blancheur": {"statistique": q, "p": p_lb, "ok": blanc}, "normalite": {"statistique": jb, "p": p_jb, "ok": normal, "asymetrie": asym, "aplatissement": kurt},
            "stationnarite": df, "aberrantes": [{"x": x[ok[j]], "ecart": float(res[j])} for j in aberr], "residus": _liste(res_complet),
            "fiabilite": {"niveau": niveau, "phrases": phrases}}


# ── Présentation ─────────────────────────────────────────────────────────────
def _noms_phases(genre, p, i0, t0, pas):
    if genre == "mois" and p == 12:
        return [_MOIS[(i0 + k) % 12] for k in range(12)]
    if genre == "trimestre" and p == 4:
        return [f"T{(i0 + k) % 4 + 1}" for k in range(4)]
    if genre == "jour" and p == 7:
        premier = datetime.fromtimestamp(t0 + i0 * pas, timezone.utc).weekday()
        return [_JOURS[(premier + k) % 7] for k in range(7)]
    if genre == "heure" and p == 24:
        premiere = datetime.fromtimestamp(t0 + i0 * pas, timezone.utc).hour
        return [f"{(premiere + k) % 24:02d} h" for k in range(24)]
    return [str(k + 1) for k in range(p)]


def _equation(m):
    n, e = m["n"], m["e"]
    from numpy.polynomial import Polynomial   # u = (t - (n + 1) / 2) / e avec t = 1 pour la première période
    coef = Polynomial(m["beta"])(Polynomial([-((n + 1) / 2.0) / e, 1.0 / e])).coef
    coef = np.concatenate((coef, np.zeros(m["degre"] + 1 - len(coef))))
    termes = [_nb(coef[0])] + [f"{'+' if c >= 0 else '−'} {_nb(abs(c))}·t" + ("²" if k == 2 else "") for k, c in enumerate(coef[1:], 1)]
    return "y = " + " ".join(termes), [float(c) for c in coef]


def _sens(m, y, seuil=None):
    """Sens de la tendance. `seuil` (modèle alternatif) : variation minimale pour parler de hausse ou de baisse."""
    T = m["tendance"]
    variation = float(T[-1] - T[0])
    if abs(variation) <= 1e-9 * max(1.0, float(np.max(np.abs(y)))):   # rien à distinguer du bruit numérique (série constante)
        return "stable", variation
    if seuil is not None:
        significatif = abs(variation) > seuil
    elif m["degre"] == 1:
        t = abs(float(m["beta"][1])) / (m["sigma"] * math.sqrt(max(float(m["inv"][1, 1]), 1e-300)) + 1e-300)
        significatif = t > quantile_t(0.975, m["df"])
    else:
        significatif = abs(variation) > m["sigma"]
    return ("hausse" if variation > 0 else "baisse") if significatif else "stable", variation


# ── Effets connus (rupture, variables explicatives, jours fériés, saisons secondaires), variations, validation glissante ────────
def _p_f(F, d1, d2):
    """p-valeur du test F de Fisher (queue haute)."""
    return _beta_incomplete(d2 / 2.0, d1 / 2.0, d2 / (d2 + d1 * max(F, 0.0)))


def _paques(a):
    b, c = divmod(a, 100)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * (a % 19) + b - b // 4 - g + 15) % 30
    l = (32 + 2 * (b % 4) + 2 * (c // 4) - h - c % 4) % 7
    q = h + l - 7 * ((a % 19 + 11 * h + 22 * l) // 451) + 114
    return date(a, q // 31, q % 31 + 1)


# Jours fériés nationaux (ni ponts ni reports ; régions : _REGIONS ; fêtes à date lunaire : _LUNAIRES) : (dates fixes, décalages en jours par rapport à Pâques, (mois, jour de semaine
# 0 = lundi, rang) ; rang -1 = dernier). Les jours fériés d'un autre pays s'ajoutent ici, ou se donnent en liste de dates (`feries=[...]`).
_FERIES = {
    "FR": (((1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)), (1, 39, 50), ()),
    "BE": (((1, 1), (5, 1), (7, 21), (8, 15), (11, 1), (11, 11), (12, 25)), (1, 39, 50), ()),
    "LU": (((1, 1), (5, 1), (5, 9), (6, 23), (8, 15), (11, 1), (12, 25), (12, 26)), (1, 39, 50), ()),
    "DE": (((1, 1), (5, 1), (10, 3), (12, 25), (12, 26)), (-2, 1, 39, 50), ()),
    "AT": (((1, 1), (1, 6), (5, 1), (8, 15), (10, 26), (11, 1), (12, 8), (12, 25), (12, 26)), (1, 39, 50, 60), ()),
    "CH": (((1, 1), (8, 1), (12, 25), (12, 26)), (-2, 1, 39, 50), ()),
    "ES": (((1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1), (12, 6), (12, 8), (12, 25)), (-2,), ()),
    "PT": (((1, 1), (4, 25), (5, 1), (6, 10), (8, 15), (10, 5), (11, 1), (12, 1), (12, 8), (12, 25)), (-2, 0, 60), ()),
    "IT": (((1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)), (1,), ()),
    "GB": (((1, 1), (12, 25), (12, 26)), (-2, 1), ((5, 0, 1), (5, 0, -1), (8, 0, -1))),
    "US": (((1, 1), (6, 19), (7, 4), (11, 11), (12, 25)), (), ((1, 0, 3), (2, 0, 3), (5, 0, -1), (9, 0, 1), (10, 0, 2), (11, 3, 4))),
}
PAYS_FERIES = tuple(_FERIES)
# Jours propres à une région, en plus de ceux du pays (même codage) : code « PAYS-RÉGION ». Les jours communaux ne sont pas couverts.
_REGIONS = {
    "FR-AM": (((12, 26),), (-2,), ()),      # Alsace-Moselle : Vendredi saint et 26 décembre
    "DE-BW": (((1, 6), (11, 1)), (60,), ()), "DE-BY": (((1, 6), (11, 1)), (60,), ()), "DE-NW": (((11, 1),), (60,), ()), "DE-HE": ((), (60,), ()),
    "DE-BE": (((3, 8),), (), ()), "DE-ST": (((1, 6), (10, 31)), (), ()),
    "ES-CT": (((6, 24), (9, 11), (12, 26)), (1,), ()), "ES-MD": (((5, 2),), (-3,), ()),
    "IT-RM": (((6, 29),), (), ()), "IT-MI": (((12, 7),), (), ()),
}
REGIONS_FERIES = tuple(_REGIONS)


def _nieme(a, m, jour, rang):
    """Date du `rang`-ième `jour` de semaine (0 = lundi) du mois `m` ; rang -1 = le dernier."""
    if rang > 0:
        d = date(a, m, 1)
        return d + timedelta(days=(jour - d.weekday()) % 7 + 7 * (rang - 1))
    d = date(a + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - jour) % 7)


def jours_feries(a, pays="FR"):
    """Jours fériés de l'année `a` : nationaux (PAYS_FERIES ; France : fixes + lundi de Pâques, Ascension, lundi de Pentecôte), ceux d'une région
    (« DE-BY » : pays + région, REGIONS_FERIES) et fêtes à date lunaire (« FR+islam+juif+chine+orthodoxe », LUNAIRES : calculées, ±1 jour possible)."""
    c = code_feries(pays)
    if c is None:
        raise KeyError(pays)
    tete, lune = c
    jours = []
    for cle in dict.fromkeys((tete[:2], tete)):      # le pays, puis la région
        fixes, paques, nieme = _FERIES.get(cle) or _REGIONS[cle]
        jours += [date(a, m, j) for m, j in fixes] + [_paques(a) + timedelta(days=k) for k in paques] + [_nieme(a, m, w, r) for m, w, r in nieme]
    return sorted(set(jours + [d for x in lune for d in _LUNAIRES[x](a)]))


# Fêtes à date lunaire, calculées (aucune table) : chaque fonction rend les dates de l'année `a`.
def _islam(a):
    """Fêtes musulmanes tombant en `a` (calendrier civil tabulaire ; la date observée localement diffère de 1 à 2 jours) : nouvel an, Mawlid, Aïd el-Fitr, Aïd el-Kébir."""
    h0, jdn = int((a - 622) * 33 / 32), lambda y, m, d: d + math.ceil(29.5 * (m - 1)) + (y - 1) * 354 + (3 + 11 * y) // 30 + 1948439
    jours = [date.fromordinal(jdn(y, m, j) - 1721425) for y in range(h0 - 1, h0 + 3) for m, j in ((1, 1), (3, 12), (10, 1), (12, 10))]
    return [d for d in jours if d.year == a]


def _juif(a):
    """Fêtes juives tombant en `a` (calendrier hébraïque, calcul exact) : Pessah (1er jour), Roch Hachana, Yom Kippour."""
    def ecoules(y):
        mois = (235 * y - 234) // 19
        j = 29 * mois + (12084 + 13753 * mois) // 25920
        return j + ((3 * (j + 1)) % 7 < 3)

    def nouvel_an(y):
        return -1373427 + ecoules(y) + (2 if ecoules(y + 1) - ecoules(y) == 356 else 1 if ecoules(y) - ecoules(y - 1) == 382 else 0)
    rh = nouvel_an(a + 3761)
    return [date.fromordinal(rh - 163), date.fromordinal(rh), date.fromordinal(rh + 9)]


def _lune_nouvelle(k):
    """Jour julien (TT) de la k-ième nouvelle lune depuis le 6 janvier 2000 (Meeus, chap. 49, termes principaux : précision de quelques minutes)."""
    T, r, s = k / 1236.85, math.radians, math.sin
    E = 1 - 0.002516 * T - 0.0000074 * T * T
    M, Mp = r(2.5534 + 29.1053567 * k - 0.0000014 * T * T), r(201.5643 + 385.81693528 * k + 0.0107582 * T * T)
    F, O = r(160.7108 + 390.67050284 * k - 0.0016118 * T * T), r(124.7746 - 1.56375588 * k + 0.0020672 * T * T)
    corr = (-0.4072 * s(Mp) + 0.17241 * E * s(M) + 0.01608 * s(2 * Mp) + 0.01039 * s(2 * F) + 0.00739 * E * s(Mp - M) - 0.00514 * E * s(Mp + M)
            + 0.00208 * E * E * s(2 * M) - 0.00111 * s(Mp - 2 * F) - 0.00057 * s(Mp + 2 * F) + 0.00056 * E * s(2 * Mp + M) - 0.00042 * s(3 * Mp)
            + 0.00042 * E * s(M + 2 * F) + 0.00038 * E * s(M - 2 * F) - 0.00024 * E * s(2 * Mp - M) - 0.00017 * s(O))
    return 2451550.09766 + 29.530588861 * k + 0.00015437 * T * T + corr


def _chine(a):
    """Nouvel an chinois de `a` : première nouvelle lune à partir du 21 janvier, en heure de Chine (UTC+8) ; ±1 jour aux cas limites."""
    k0 = int((a - 2000) * 12.3685)
    for k in range(k0, k0 + 4):
        d = date.fromordinal(math.floor(_lune_nouvelle(k) - 0.0008 + 1 / 3 + 0.5) - 1721425)      # TT -> UTC (69 s) puis UTC+8
        if d >= date(a, 1, 21):
            return [d]
    return []


def _orthodoxe(a):
    """Vendredi saint et lundi de Pâques orthodoxes (comput julien ramené au calendrier grégorien)."""
    d = (19 * (a % 19) + 15) % 30
    n = d + (2 * (a % 4) + 4 * (a % 7) - d + 34) % 7 + 114
    paques = date(a, n // 31, n % 31 + 1) + timedelta(days=a // 100 - a // 400 - 2)
    return [paques - timedelta(days=2), paques + timedelta(days=1)]


_LUNAIRES = {"islam": _islam, "juif": _juif, "chine": _chine, "orthodoxe": _orthodoxe}
LUNAIRES = tuple(_LUNAIRES)


def code_feries(code):
    """(pays ou région, fêtes lunaires) d'un code « FR », « DE-BY » ou « FR+islam+chine » (pays ou région, puis fêtes à date lunaire : LUNAIRES) ; None si un morceau est inconnu."""
    tete, *lune = str(code).replace(" ", "").split("+")
    tete, lune = tete.upper(), [x.lower() for x in lune]
    return (tete, lune) if (tete in _FERIES or tete in _REGIONS) and all(x in _LUNAIRES for x in lune) else None


def _ret(a, e, rel):
    """Retire l'effet `e` de `a` : par soustraction, ou par division par exp(e) quand les effets sont relatifs (saison multiplicative)."""
    return a / np.exp(e) if rel else a - e


def _rem(a, e, rel):
    """Remet l'effet `e` dans `a` (contraire de `_ret`)."""
    return a * np.exp(e) if rel else a + e


def _ols(A, z):
    b = np.linalg.lstsq(A, z, rcond=None)[0]
    r = z - A @ b
    return b, float(r @ r)


def _grille(d, v, genre, pas, t0, i0, N):
    """(valeurs de la variable sur les N périodes de la grille, périodes sans valeur). Moyenne par période ; trou ou avenir inconnu : valeur connue
    la plus proche. (None, N) si aucune valeur n'est exploitable."""
    s, c = np.zeros(N), np.zeros(N)
    for a, b in zip(d, v):
        t, x = _secondes(a), _reel(b)
        j = _indice(t, genre, pas, t0) - i0 if t is not None and x is not None else -1
        if 0 <= j < N:
            s[j] += x
            c[j] += 1
    vide = c == 0
    if vide.all():
        return None, vide
    g, k = np.where(vide, 0.0, s / np.maximum(c, 1)), np.arange(N)
    g[vide] = np.interp(k[vide], k[~vide], g[~vide])
    return g, vide


def _rupture(z, A, N):
    """Bloc {pente après le point, saut de niveau} au point qui réduit le plus l'erreur (60 points essayés, marges de 12 %) ; None si trop court."""
    n = len(z)
    if n < 24:
        return None
    k, marge = np.arange(N), max(6, n // 8)
    cand = np.unique(np.linspace(marge, n - marge, 60).astype(int))
    forme = lambda c: np.column_stack([np.maximum(0, k - c) / n, (k >= c).astype(float)])
    c = min(cand, key=lambda c: _ols(np.column_stack([A, forme(c)[:n]]), z)[1])
    return {"nom": _i18n_t("engine/series_temporelles.py:g:1202:20"), "type": "rupture", "X": forme(c), "alpha": PARAMETRES["alpha_regresseur"] / len(cand), "position": int(c)}


def _libre(z, A, N, p, exclu=()):
    """Bloc de la saison la plus nette du reste (périodogramme de ce que [1, t, t²] + les blocs déjà gardés laissent), période libre d'au moins
    2,5 et d'au plus n/3 périodes, hors harmoniques de la période principale `p` et des fréquences `exclu` (cycles sur n points, saisons libres déjà gardées), affinée par balayage fin ; None si la série est trop courte.
    Seuil de Fisher divisé par le nombre de fréquences essayées."""
    n = len(z)
    if n < 24:
        return None
    r = z - A @ np.linalg.lstsq(A, z, rcond=None)[0]
    P = np.abs(np.fft.rfft(r - r.mean())) ** 2
    harm = n / p * np.arange(1, p // 2 + 1) if p > 1 else np.zeros(0)
    ks = [k for k in range(3, min(int(n / 2.5), len(P) - 1) + 1) if (not len(harm) or float(np.abs(k - harm).min()) >= 1.5) and all(abs(k - f) >= 1.5 for f in exclu)]
    if not ks:
        return None
    t = np.arange(n)
    ondes = lambda kf: np.column_stack([np.sin(2 * math.pi * kf * t / n), np.cos(2 * math.pi * kf * t / n)])
    kk = max(ks, key=lambda k: P[k])
    kf = min(np.linspace(kk - 0.75, kk + 0.75, 13), key=lambda kf: _ols(ondes(kf), r)[1])
    q = n / kf
    w = 2 * math.pi * np.arange(N)[:, None] * np.arange(1, (2 if q >= 4 else 1) + 1) / q
    return {"nom": tf("engine/series_temporelles.py:g:1224:20", v1=_nb(q, 3)), "type": "saisonnalite", "X": np.hstack([np.sin(w), np.cos(w)]),
            "alpha": PARAMETRES["alpha_regresseur"] / (2 * len(ks)), "kf": float(kf)}


def _effets(y, p, genre, pas, t0, i0, h, explicatives, feries, ruptures):
    """Effets connus significatifs (voir l'étape 8 du docstring) : ({"tous", "cvs", "tendance"} -> effet sur les n + h périodes, rapport par bloc,
    relatif). `relatif` = la saison est multiplicative : les effets sont estimés sur le logarithme et se combinent par produit (facteur exp(effet)),
    `_ret` / `_rem` les retirent / les remettent dans la bonne forme."""
    n, N = len(y), len(y) + h
    k = np.arange(N)
    B, ph = np.column_stack([np.ones(N), k / max(1.0, n), (k / max(1.0, n)) ** 2]), k[:n] % p
    modeles = []
    for nom, (d, v) in (explicatives or {}).items():
        g, vide = _grille(d, v, genre, pas, t0, i0, N)
        if g is not None:
            modeles.append({"nom": nom, "type": "explicative", "X": g[:, None], "manque": int(vide[n:].sum())})
    if feries and (genre in ("jour", "semaine", "mois", "trimestre") or genre == "pas" and pas >= 86400):
        a, b = (int(_etiquette(i0 + j, genre, pas, t0)[:4]) for j in (0, N - 1))
        jours = [f for an in range(a, b + 1) for f in jours_feries(an, "FR" if feries is True else feries)] if feries is True or isinstance(feries, str) else [
            datetime.fromtimestamp(u, timezone.utc).date() for u in map(_secondes, feries) if u is not None]
        c = np.zeros(N)
        for f in jours:
            j = _indice(datetime(f.year, f.month, f.day, tzinfo=timezone.utc).timestamp(), genre, pas, t0) - i0
            if 0 <= j < N:
                c[j] += 1
        modeles.append({"nom": _i18n_t("engine/series_temporelles.py:g:1249:32"), "type": "calendrier", "X": c[:, None]})
    for q in _naturelles(genre, pas):
        if q != p and 2 * q <= n:
            w = 2 * math.pi * k[:, None] * np.arange(1, min(q // 2, 4 if q < 100 else 6) + 1) / q
            modeles.append({"nom": tf("engine/series_temporelles.py:g:1253:36", v1=q), "type": "saisonnalite", "X": np.hstack([np.sin(w), np.cos(w)])})
    nb = 0 if not ruptures else 3 if ruptures is True else min(3, int(ruptures))
    modeles += ["L"] * (LIMITES["saisons_libres"] if p > 1 else 0) + ["R"] * nb      # cherchés en dernier, sur ce que les autres effets laissent : "L" = saison libre (jusqu'à 3), "R" = rupture (jusqu'à 3)

    def passe(z, rel):
        """Un bloc après l'autre sur [1, t, t²] : gardé si Fisher < 1 % (écarts corrélés : degrés de liberté réduits, Bartlett). `z` est en logarithme si `rel`."""
        blocs, A, echelle = [dict(m) if isinstance(m, dict) else m for m in modeles], B[:n], float(np.sum((z - z.mean()) ** 2))
        b, rss = _ols(A, z)
        arret, libres = set(), []      # un bloc cherché ("L" ou "R") non significatif arrête la recherche des suivants du même genre ; `libres` = fréquences déjà prises
        for j, bl in enumerate(blocs):
            if isinstance(bl, str):
                lettre = bl
                bl = blocs[j] = None if lettre in arret else (_rupture(z, A, N) if lettre == "R" else _libre(z, A, N, p, libres))
                if bl is None:
                    arret.add(lettre)
                    continue
                bl["lettre"] = lettre
                if bl["type"] == "rupture":
                    bl["nom"] += " (" + _etiquette(i0 + bl["position"], genre, pas, t0) + ")"
            X = bl["X"] - bl["X"][:n].mean(axis=0)
            A2, d1 = np.column_stack([A, X[:n]]), X.shape[1]
            d2 = n - A2.shape[1]
            ok = d2 >= 5 and float(X[:n].std(axis=0).min()) > 1e-9
            b2, rss2 = _ols(A2, z) if ok else (b, rss)
            r2 = z - A2 @ b2 if ok else z
            rho = max(0.0, float(np.corrcoef(r2[1:], r2[:-1])[0, 1])) if ok and r2.std() > 1e-12 else 0.0
            c = (1.0 - rho) / (1.0 + rho)
            bl["p"] = _p_f(c * ((rss - rss2) / d1) / max(rss2 / d2, 1e-300), d1, max(1.0, c * d2)) if ok and rss - rss2 > 1e-9 * echelle else 1.0
            bl["retenu"], bl["Xc"] = bool(bl["p"] < bl.get("alpha", PARAMETRES["alpha_regresseur"])), X
            if bl["retenu"]:
                bl["cols"], A, b, rss = slice(A.shape[1], A2.shape[1]), A2, b2, rss2
            if bl.get("lettre") and not bl["retenu"]:
                arret.add(bl["lettre"])
            elif bl.get("kf") is not None:
                libres += [bl["kf"], 2 * bl["kf"]]
        eff, rapport = {c: np.zeros(N) for c in ("tous", "cvs", "tendance")}, []
        conv = np.expm1 if rel else float      # en relatif, le poids se lit en fraction de la valeur (0,1 = +10 %)
        for bl in [x for x in blocs if x]:
            e = bl["Xc"] @ b[bl["cols"]] if bl["retenu"] else np.zeros(N)
            eff["tous"] += e
            eff["cvs"] += e if bl["type"] in ("saisonnalite", "calendrier") else 0.0
            eff["tendance"] += e if bl["type"] == "rupture" else 0.0
            rapport.append({"nom": bl["nom"], "type": bl["type"], "p": float(bl["p"]), "retenu": bl["retenu"], "manque": bl.get("manque", 0),
                            "coef": float(conv(b[bl["cols"]][0])) if bl["retenu"] and bl["X"].shape[1] == 1 else None,
                            "amplitude": float(conv(np.ptp(e[:n]))) if bl["retenu"] else None, "relatif": bool(rel)})
        return eff, rapport

    eff, rapport = passe(y, False)
    rel = False
    for _ in range(3 if p > 1 else 0):      # la saison est mesurée sur la série sans les effets, les effets sur la série sans la saison, jusqu'à stabilité
        u = _ret(y, eff["tous"][:n], rel)
        mm = _moyenne_mobile(u, p)
        choix = [(y - _indices(u - mm, p, "additif")[ph], False)] + (
            [(y / _indices(u / mm, p, "multiplicatif")[ph], True)] if np.all(y > 0) and np.all(u > 0) else [])
        z, rel = min(choix, key=lambda c: _ols(B[:n], c[0])[1])
        eff, rapport = passe(np.log(z) if rel else z, rel)
    return eff, rapport, rel


def _variation(a, k):
    """Variation en % de chaque point par rapport à celui d'il y a k périodes (None quand rien à comparer)."""
    v, d = np.full(len(a), np.nan), np.abs(a[:-k])
    v[k:] = np.where(d > 1e-12, (a[k:] - a[:-k]) / np.where(d > 1e-12, d, 1.0) * 100.0, np.nan)
    return _liste(v)


def _glisser(y, x, horizon, p, plis, relancer):
    """Validation glissante : `relancer(recul)` refait toute l'analyse sur y[:-recul] ; ses estimations sont comparées à y et à la prévision naïve
    (dernier cycle répété, dernière valeur sans saison). None si la série est trop courte pour au moins un pli."""
    n = len(y)
    depart = max(n // 2, 2 * p + 3, PARAMETRES["points_min"])
    h = min(horizon, n - depart)
    if h < 1 or plis < 1:
        return None
    E, N_, R, C, lignes = [], [], [], [], []
    for r in range(min(plis, (n - depart) // h) * h, 0, -h):
        res = relancer(r, h)
        if res.get("erreur"):
            continue
        reel, base, pv = y[n - r:n - r + h], y[:n - r], res["prevision"]
        est = np.array(pv["valeur"], float)
        E.append(reel - est)
        N_.append(reel - np.array([base[len(base) - p + j % p] if p > 1 else base[-1] for j in range(h)]))
        R.append(reel)
        C.append((reel >= np.array(pv["bas"], float)) & (reel <= np.array(pv["haut"], float)))
        lignes.append({"apres": x[n - r - 1], "methode": res["modele_retenu"]["methode_libelle"], "rmse": math.sqrt(float(np.mean(E[-1] ** 2))),
                       "mae": float(np.mean(np.abs(E[-1])))})
    if not E:
        return None
    e, en, reel = np.array(E), np.array(N_), np.array(R)
    rm = lambda a: math.sqrt(float(np.mean(a ** 2)))
    return {"horizon": h, "plis": len(E), "origines": lignes, "rmse": rm(e), "mae": float(np.mean(np.abs(e))),
            "mape": float(np.mean(np.abs(e / reel)) * 100.0) if np.all(np.abs(reel) > 1e-12) else None, "couverture": float(np.mean(C)),
            "rmse_naif": rm(en), "gain_naif": 1.0 - rm(e) / rm(en) if rm(en) > 1e-12 else None, "par_pas": _liste([rm(e[:, j]) for j in range(h)])}


def _score(c):
    return c["rmse_validation"] if c["rmse_validation"] is not None else c["rmse_ajustement"]


def _droite(T):
    """Droite des moindres carrés ajustée sur une tendance (rompue ou parabolique) : la tendance affichée est toujours rectiligne.
    Valeurs non finies ignorées ; moins de 2 points utilisables : la tendance est rendue telle quelle."""
    T = np.asarray(T, float)
    t = np.arange(T.size, dtype=float)
    ok = np.isfinite(T)
    if ok.sum() < 2:
        return T
    b, a = np.polyfit(t[ok], T[ok], 1)
    return a + b * t


def _horizon_auto(n: int, p: int, saison: bool) -> int:
    """Horizon choisi quand l'utilisateur n'en donne pas : un cinquième de la série (au moins 3), jusqu'à LIMITES["horizon"] ; avec une saison,
    au moins un cycle complet, arrondi au cycle supérieur (avant : 12 valeurs au plus, soit 12 jours sur une série quotidienne de 700 points)."""
    h = max(3, min(LIMITES["horizon"], n // 5))
    if saison:
        h = max(h, p)
        h = min(h + (-h) % p, LIMITES["horizon"]) if p <= LIMITES["horizon"] else p
    return int(h)


def analyser(dates, valeurs, nom: str = tl("engine/series_temporelles.py:g:1353:41"), periode=None, horizon=None, confiance: float = PARAMETRES["confiance"],
             methode: str = "auto", modele: str = "auto", agregat: str = "moyenne", explicatives=None, feries=True, ruptures: bool = True,
             glissante: int = 0, _recul: int = 0) -> dict:
    """Analyse complète d'une série. Renvoie {"nom", "erreur"} si la série est inutilisable ; sinon le dict décrit dans le README
    (x/y, moyenne_mobile, ajustement, tendance, saison, prevision, qualite, candidats, modele_retenu, resume, avertissements, regresseurs,
    variations, glissante). `explicatives` = {nom: (dates, valeurs)} (valeurs futures utilisées pour l'estimation) ; `feries` = True (France), un code de pays, de région ou avec fêtes lunaires
    (« FR », « DE-BY », « FR+islam+chine » ; voir jours_feries), False ou une liste de dates ; `ruptures` = recherche de ruptures de tendance (jusqu'à 3 ; un nombre 1 à 3 borne la recherche) ; `glissante` = nombre d'origines de la validation glissante (0 = aucune) ;
    `_recul` (interne) = retire les dernières périodes, pour la validation glissante."""
    confiance = min(0.999, max(0.5, float(confiance)))
    inconnu = feries if isinstance(feries, str) and code_feries(feries) is None else None
    feries = False if inconnu else feries.upper() if isinstance(feries, str) else feries
    args = dict(periode=periode, confiance=confiance, methode=methode, modele=modele, agregat=agregat, explicatives=explicatives, feries=feries,
                ruptures=ruptures)
    methode = methode if methode in _LIB else "auto"
    impose = methode if methode in ALTERNATIVES else None
    methode = "auto" if impose else methode
    modele = modele if modele in ("additif", "multiplicatif") else "auto"
    agregat = agregat if agregat in _AGREGATS else "moyenne"
    lignes = [(d, v) for d, v in zip(dates, valeurs) if not explicatives or v not in (None, "")]   # avec des variables explicatives, une ligne sans valeur est l'avenir, pas une erreur
    try:
        y, genre, pas, t0, i0, bilan = _preparer([d for d, _v in lignes], [v for _d, v in lignes], agregat)
    except ValueError as exc:
        return {"nom": nom, "erreur": str(exc)}
    y = y[:len(y) - _recul]
    n, avert = len(y), []
    if inconnu:
        avert.append(tf("engine/series_temporelles.py:g:1379:22", v1=inconnu, v2=', '.join(PAYS_FERIES + REGIONS_FERIES), v3='/'.join(LUNAIRES)))
    if bilan["interpoles"]:
        avert.append(tf("engine/series_temporelles.py:g:1381:22", v1=bilan['interpoles']))
        if bilan["interpoles"] > PARAMETRES["part_trous_max"] * n:
            avert.append(_i18n_t("engine/series_temporelles.py:g:1383:26"))
    if bilan["ignores"]:
        avert.append(tf("engine/series_temporelles.py:g:1385:22", v1=bilan['ignores']))
    if bilan["regroupes"] > 0:
        avert.append(tf("engine/series_temporelles.py:g:1387:22", v1=bilan['regroupes'], v2=agregat))

    p, force, origine = 1, 0.0, None
    if periode is not None and int(periode) >= 1:
        p = int(periode)
        if p > 1 and 2 * p > n:
            avert.append(tf("engine/series_temporelles.py:g:1393:26", v1=p, v2=n))
            p = 1
        else:
            force, origine = _force_saison(y, p)[0], "imposee"
    if origine is None:
        p, force, origine = _detecter_periode(y, genre, pas)
    saison = p > 1

    auto = not horizon      # avant : calculé APRÈS l'horizon, donc toujours faux
    horizon = max(1, min(LIMITES["horizon"], int(horizon))) if horizon else _horizon_auto(n, p, saison)
    y_obs = y
    longue = origine != "imposee" and p > LIMITES["recherche_periode"] and n < 8 * p      # trop peu de cycles pour des moyennes de phase : harmoniques
    pe = 1 if longue else p
    eff, rapport, mult = _effets(y, pe, genre, pas, t0, i0, horizon, explicatives, feries, ruptures)
    y = _ret(y_obs, eff["tous"][:n], mult)
    if longue:      # les saisons longues sont maintenant dans les effets : la période restante est cherchée sur le reste
        p, force, origine = _detecter_periode(y, genre, pas)
        saison = p > 1
        horizon = min(horizon, _horizon_auto(n, p, saison)) if auto else horizon      # jamais au-delà de ce pour quoi les effets (n + horizon périodes) ont été construits
        eff = {c: a[:n + horizon] for c, a in eff.items()}
    avert += [tf("engine/series_temporelles.py:g:1412:15", v1=r['nom'], v2=r['manque']) for r in rapport if r["retenu"] and r["manque"]]
    modeles = ("additif", "multiplicatif") if saison and modele == "auto" else ((modele,) if saison else ("aucun",))
    methodes = tuple(METHODES) if saison and methode == "auto" else ((methode,) if saison and methode != "auto" else ("mco",))
    degres = (1, 2) if n >= max(12, 3 * p) else (1,)
    k = _taille_validation(n, p, horizon)
    yv, ev, mv = y, None, False
    if k:      # effets réestimés SANS les k valeurs mises de côté (sinon la validation les connaîtrait) puis extrapolés sur elles
        e_v, _r, mv = _effets(y_obs[:n - k], pe, genre, pas, t0, i0, horizon + k, explicatives, feries, ruptures)
        yv, ev = _ret(y_obs, e_v["tous"][:n], mv), e_v["tous"][n - k:n]
    candidats = []
    for me in methodes:
        for mo in modeles:
            for deg in degres:
                m = _ajuster(y, p, me, "additif" if mo == "aucun" else mo, deg)
                if m is None:
                    continue
                v = _valider(yv, p, me, "additif" if mo == "aucun" else mo, deg, k, confiance, ev, mv) if k else None
                candidats.append({"methode": me, "modele": mo, "degre": deg, "rmse_ajustement": m["rmse_ajustement"],
                                  "rmse_validation": v["rmse"] if v else None, "validation": v, "_m": m})
    for cle, fabrique in (("hw", lambda mo: (lambda z: _hw_ajuster(z, p, mo == "multiplicatif", confiance))),
                          ("arima", lambda mo: (lambda z: _arima_ajuster(z, p, confiance, mo == "multiplicatif")))):
        if methode != "auto":
            continue
        for mo in (modeles if saison else ("additif",)):
            ajusteur = fabrique(mo)
            a = ajusteur(y)
            if a is None:
                continue
            v = _valider_alt(ajusteur, yv, p, k, confiance, ev, mv) if k else None
            res_a = y - a["ajuste"]
            rm = math.sqrt(float(np.nanmean(res_a ** 2)))
            candidats.append({"methode": cle, "modele": a["modele"], "degre": None, "rmse_ajustement": rm,
                              "rmse_validation": v["rmse"] if v else None, "validation": v, "_alt": a})
    if not any("_m" in c for c in candidats):
        return {"nom": nom, "erreur": _i18n_t("engine/series_temporelles.py:g:1446:39")}
    meilleur = min(_score(c) for c in candidats)
    pool = [c for c in candidats if _score(c) <= meilleur * PARAMETRES["tolerance_ecart"] + 1e-12]
    cle_tri = lambda c: ("_alt" in c, c["degre"] or 0, c["modele"] == "multiplicatif", c["methode"] != "mco", _score(c))
    if impose and any(c["methode"] == impose for c in candidats):
        pool = [c for c in candidats if c["methode"] == impose]
        pool = [c for c in pool if _score(c) <= min(_score(d) for d in pool) * PARAMETRES["tolerance_ecart"] + 1e-12]
    retenu = min(pool, key=cle_tri)
    classique = min([c for c in pool if "_m" in c] or [c for c in candidats if "_m" in c], key=cle_tri)
    m = classique["_m"]
    est, bas, haut = _predire(m, horizon, confiance) if "_m" in retenu else retenu["_alt"]["predire"](horizon)
    est, bas, haut = (_rem(a, eff["tous"][n:], mult) for a in (est, bas, haut))
    ajuste = m["ajuste"] if "_m" in retenu else retenu["_alt"]["ajuste"]
    sigma, ddl = (m["sigma"], m["df"]) if "_m" in retenu else (retenu["_alt"]["sigma"], retenu["_alt"]["df"])
    decrit, propre, seuil = m, False, None      # ce que décrivent tendance, équation et coefficients saisonniers : le modèle retenu lui-même
    if "_alt" in retenu:
        d_alt = _decrire_alternative(retenu["_alt"], y, p, classique["degre"] or 1)
        if d_alt is not None:
            decrit, propre = d_alt, True
            seuil = quantile_t(0.975, ddl) * sigma * math.sqrt(2.0) * (float(np.mean(np.abs(decrit["tendance"]))) if retenu["_alt"].get("relatif") else 1.0)
    modele_desc = ("multiplicatif" if decrit["mult"] else "additif") if saison else "aucun"

    q = _lien_niveau_amplitude(y, p) if saison else None
    raison = (tf("engine/series_temporelles.py:g:1470:15", v1=k, v2=_nb(retenu['rmse_validation']), v3=len(candidats)) if k else
              tf("engine/series_temporelles.py:g:1472:15", v1=len(candidats)))
    if q is not None:
        raison += (_i18n_t("engine/series_temporelles.py:g:1474:20") + _nb(q, 2) + ")." if q > 0.5 else
                   _i18n_t("engine/series_temporelles.py:g:1475:20") + _nb(q, 2) + ")." if q < 0.2 else "")
    raison += _i18n_t("engine/series_temporelles.py:g:1476:15") if retenu.get("_alt", {}).get("exacte") else ""
    equation, coefs = _equation(decrit)
    T_tot, rupt = _rem(decrit["tendance"], eff["tendance"][:n], mult), bool(np.any(eff["tendance"]))
    sens, variation = _sens({**decrit, "tendance": T_tot, "degre": 0} if rupt else decrit, y, seuil)
    equation += _i18n_t("engine/series_temporelles.py:g:1480:17") if rupt else ""
    T_morceaux = T_tot      # tendance avec ruptures (et parabole éventuelle) : gardée à part, l'ajustement et les prévisions l'utilisent toujours
    T_tot = _droite(T_tot)  # la courbe « tendance » affichée est UNE droite (moindres carrés sur cette tendance)
    pente = (float(T_tot[-1] - T_tot[0]) / (n - 1)) if n > 1 else 0.0
    residus = y - ajuste
    bon = np.isfinite(residus)
    ss_tot = float(np.sum((y_obs[bon] - y_obs[bon].mean()) ** 2))
    utile = np.abs(y) > 1e-12
    q_mm = p if saison else (3 if n < 30 else 5)
    x = [_etiquette(i0 + j, genre, pas, t0) for j in range(n)]
    diag = _diagnostiquer(y, residus, p, x)
    if mult:      # erreurs dans l'unité de la série (le diagnostic reste fait sur l'écart relatif, où le bruit est comparable d'un bout à l'autre)
        residus = y_obs - _rem(ajuste, eff["tous"][:n], True)
    residus, y_ok = residus[bon], y_obs[bon]
    utile = np.abs(y_ok) > 1e-12
    if diag["fiabilite"]["niveau"] == "faible":
        avert.append(_i18n_t("engine/series_temporelles.py:g:1494:22"))
    x_prev = [_etiquette(i0 + n + j, genre, pas, t0) for j in range(horizon)]
    phrase_tendance = (_i18n_t("engine/series_temporelles.py:g:1496:24") if sens == "stable" else
                       tf("engine/series_temporelles.py:g:1497:24", v1=sens, v2=_nb(abs(pente)), v3=_UNITES.get(genre, td("engine/series_temporelles.py:g:1494:120"))))
    phrase_saison = (tf("engine/series_temporelles.py:g:1498:22", v1=p, v2=modele_desc) if saison else _i18n_t("engine/series_temporelles.py:g:1498:96"))
    resume = (tf("engine/series_temporelles.py:g:1499:15", v1=nom, v2=_LIBELLES[genre], v3=n, v4=phrase_tendance, v5=phrase_saison, v6=horizon, v7=_nb(float(est[0])), v8=x_prev[0], v9=_nb(float(est[-1])), v10=x_prev[-1], v11=round(confiance * 100), v12=_LIB[retenu['methode']].lower(), v13=diag['fiabilite']['niveau']))
    gardes = [r["nom"] for r in rapport if r["retenu"]]
    resume += (_i18n_t("engine/series_temporelles.py:g:1503:16") + ", ".join(gardes) + ".") if gardes else ""
    tout, cvs = np.concatenate([y_obs, est]), None
    if saison:
        brut, ph_tout = _ret(tout, eff["cvs"], mult), np.arange(n + horizon) % p
        cvs = brut / m["s"][ph_tout] if m["mult"] else brut - m["s"][ph_tout]
    variations = {"separation": n, "periode": _variation(tout, 1), "glissement": _variation(tout, p) if saison else None,
                  "cvs": _liste(cvs) if saison else None, "cvs_periode": _variation(cvs, 1) if saison else None}
    pm = {} if _recul else None
    for me in _LIB if _recul else ():      # validation glissante : la prévision de CHAQUE méthode sur ces mêmes données (une seule chaîne par origine)
        ens = [c for c in candidats if c["methode"] == me]
        if ens:
            ens = [c for c in ens if _score(c) <= min(_score(d) for d in ens) * PARAMETRES["tolerance_ecart"] + 1e-12]
            c = min(ens, key=cle_tri)
            pm[me] = tuple(_rem(a, eff["tous"][n:], mult) for a in (_predire(c["_m"], horizon, confiance) if "_m" in c else c["_alt"]["predire"](horizon)))
    gl = None
    if glissante and not _recul:
        plis = min(int(glissante), PARAMETRES["plis_max"])
        cache = {}

        def relancer(me):      # me = "auto" : la chaîne complète ; sinon la méthode seule, lue dans le même passage (une chaîne par origine, pas une par méthode)
            def f(r, h):
                if (r, h) not in cache:
                    cache[(r, h)] = analyser(dates, valeurs, nom, horizon=h, glissante=0, _recul=r, **{**args, "methode": "auto"})
                res = cache[(r, h)]
                if me == "auto" or res.get("erreur"):
                    return res
                v = res["_par_methode"].get(me)
                return {"erreur": True} if v is None else {"prevision": {"valeur": v[0], "bas": v[1], "haut": v[2]}, "modele_retenu": {"methode_libelle": _LIB[me]}}
            return f
        gl = _glisser(y_obs, x, horizon, p, plis, relancer(args["methode"] if args["methode"] in _LIB else "auto"))
        if gl:      # chaque méthode est aussi classée seule sur les mêmes origines : ses prévisions viennent du même passage
            gl["par_methode"], gl["incomplet"] = [], False
            for me in _LIB:
                sm = _glisser(y_obs, x, horizon, p, plis, relancer(me))
                if sm:
                    gl["par_methode"].append({"methode": me, "methode_libelle": _LIB[me], "rmse": sm["rmse"], "mae": sm["mae"],
                                              "couverture": sm["couverture"], "gain_naif": sm["gain_naif"]})
            gl["par_methode"].sort(key=lambda d: d["rmse"])
    return {
        "nom": nom, "n": n, "frequence": {"genre": genre, "libelle": _LIBELLES[genre], "pas_s": pas},
        "periode": p, "saisonnalite": {"detectee": saison, "periode": p, "force": round(force, 4), "origine": origine},
        "donnees": bilan, "x": x, "y": _liste(y_obs), "moyenne_mobile": _liste(_moyenne_mobile(y_obs, q_mm)), "ordre_moyenne_mobile": q_mm,
        "ajustement": _liste(_rem(ajuste, eff["tous"][:n], mult)), "tendance_serie": _liste(T_tot), "tendance_par_morceaux": _liste(T_morceaux),
        "modele_retenu": {"methode": retenu["methode"], "methode_libelle": _LIB[retenu["methode"]], "modele": retenu["modele"],
                          "degre": retenu["degre"], "raison": raison},
        "tendance": {"equation": equation, "coefficients": coefs, "sens": sens, "pente_par_periode": pente, "variation_totale": variation,
                     "unite": _UNITES.get(genre, td("engine/series_temporelles.py:g:1544:50")), "propre_au_modele": propre},
        "saison": {"indices": _liste(decrit["s"]), "phases": _noms_phases(genre, p, i0, t0, pas), "modele": modele_desc,
                   "propre_au_modele": propre} if saison else None,
        "prevision": {"x": x_prev, "valeur": _liste(est), "bas": _liste(bas), "haut": _liste(haut), "confiance": confiance, "horizon": horizon},
        "qualite": {"rmse": math.sqrt(float(np.mean(residus ** 2))), "mae": float(np.mean(np.abs(residus))),
                    "mape": float(np.mean(np.abs(residus[utile] / y_ok[utile])) * 100.0) if utile.all() else None,
                    "r2": (1.0 - float(np.sum(residus ** 2)) / ss_tot) if ss_tot > 1e-12 else None, "sigma": sigma, "ddl": ddl,
                    "validation": retenu["validation"]},
        "diagnostic": {"lien_niveau_amplitude": q, **diag}, "regresseurs": rapport, "variations": variations, "glissante": gl,
        "candidats": [{"methode": c["methode"], "methode_libelle": _LIB[c["methode"]], "modele": c["modele"], "degre": c["degre"],
                       "rmse_validation": c["rmse_validation"], "rmse_ajustement": c["rmse_ajustement"], "retenu": c is retenu} for c in candidats],
        "avertissements": avert, "resume": resume, **({"_par_methode": pm} if pm else {})}


# ── Séries de démonstration (jamais lues dans une table, jamais enregistrées) ─
def demo(graine: int = 7, n: int = 60) -> list[tuple[str, list, list]]:
    """Deux séries mensuelles : l'une multiplicative (l'amplitude saisonnière suit le niveau), l'autre additive (amplitude fixe)."""
    rng = np.random.default_rng(graine)
    i = np.arange(n)
    saison = np.sin(2 * math.pi * (i % 12) / 12) + 0.4 * np.cos(4 * math.pi * (i % 12) / 12)
    dates = [f"{2021 + m // 12}-{m % 12 + 1:02d}-01" for m in range(n)]
    multiplicative = (100 + 2.2 * i) * (1 + 0.22 * saison) * (1 + rng.normal(0, 0.02, n))
    additive = 250 + 1.4 * i + 18 * saison + rng.normal(0, 3, n)
    return [(_gt("engine/series_temporelles.py:1568:14"), dates, _liste(multiplicative, 2)), (_i18n_t("engine/series_temporelles.py:g:1572:82"), dates, _liste(additive, 2))]
