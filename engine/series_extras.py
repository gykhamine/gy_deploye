"""SÉRIES TEMPORELLES — exploration étendue (module pur : numpy uniquement, aucune base, aucun Django).

Complète `engine/series_temporelles.py` (estimations) par ce que l'utilisateur veut AUSSI savoir de ses données :
  explorer()  une série  -> statistiques descriptives, croissance (TCAC), records, répartition (histogramme), valeurs atypiques,
                            points de retournement, cycles dominants (périodogramme), mémoire (autocorrélation), volatilité glissante,
                            indice base 100, profil par cycle (une ligne par année, une colonne par mois…).
  comparer()  plusieurs séries -> corrélation des niveaux et des variations, décalage le plus net (qui précède qui), matrice.
Rien n'est enregistré ; une série trop courte ou inutilisable renvoie None (jamais d'erreur).
"""
from __future__ import annotations

import math

import numpy as np

PAR_AN = {"mois": 12, "trimestre": 4, "annee": 1, "semaine": 52, "jour": 365, "heure": 8760}
LIMITES = {"retards": 36, "cycles": 5, "atypiques": 15, "classes": 20, "retournements": 8, "paires": 30, "series": 12, "decalage": 12,
           "recouvrement": 8, "profil_phases": 31, "profil_cycles": 12}


def _f(v, nd: int = 6):
    return None if v is None or not np.isfinite(v) else round(float(v), nd)


def _l(a, nd: int = 6) -> list:
    return [_f(v, nd) for v in a]


def _corr(u, v):
    """Corrélation de Pearson, None si une série est constante ou trop courte."""
    if len(u) < 3:
        return None
    su, sv = float(np.std(u)), float(np.std(v))
    return None if su < 1e-12 or sv < 1e-12 else float(np.mean((u - u.mean()) * (v - v.mean())) / (su * sv))


def _plus_longue(signe) -> int:
    """Plus longue suite de valeurs vraies consécutives."""
    meilleur = courant = 0
    for s in signe:
        courant = courant + 1 if s else 0
        meilleur = max(meilleur, courant)
    return meilleur


def _atypiques(x, y, p: int) -> list[dict]:
    """Valeurs qui s'écartent nettement de ce que donnent la tendance et la saison (écart robuste médiane/MAD > 4) : tendance = médiane glissante
    des voisins sur un cycle, saison = écart médian de chaque phase à cette tendance."""
    n = len(y)
    saison = p > 1 and n >= 3 * p
    if saison:      # moyenne mobile centrée d'un cycle entier (2×p si p est pair) : la saison s'y annule ; les deux bouts, au voisinage amputé, ne sont pas jugés
        poids = np.ones(p + 1) / p if p % 2 == 0 else np.ones(p) / p
        if p % 2 == 0:
            poids[[0, -1]] = 0.5 / p
        bord = len(poids) // 2
        tend = np.full(n, np.nan)
        tend[bord: n - bord] = np.convolve(y, poids, mode="valid")
        cadre = np.arange(bord, n - bord)
        r = y - tend
        r[cadre] -= np.array([np.median(r[cadre][(cadre % p) == k]) for k in range(p)])[cadre % p]
    else:           # sans saison : médiane des 8 voisins les plus proches (la valeur ne se juge pas avec elle-même)
        cadre = np.arange(n)
        r = y - np.array([np.median(np.delete(y[max(0, i - 4): i + 5], min(i, 4))) for i in range(n)])
    mad = float(np.median(np.abs(r[cadre] - np.median(r[cadre]))))
    echelle = 1.4826 * mad if mad > 1e-12 else float(np.std(r[cadre]))
    if echelle < 1e-12:
        return []
    z = np.zeros(n)
    z[cadre] = (r[cadre] - np.median(r[cadre])) / echelle
    ordre = [i for i in np.argsort(-np.abs(z)) if abs(z[i]) > 4][:LIMITES["atypiques"]]
    return [{"x": x[i], "valeur": _f(y[i]), "attendu": _f(y[i] - r[i]), "z": _f(z[i], 2)} for i in sorted(ordre)]


def _cycles_dominants(y) -> list[dict]:
    """Périodogramme de la série sans sa droite de tendance : les périodes qui portent le plus de variation (en nombre de périodes)."""
    n = len(y)
    if n < 16:
        return []
    t = np.arange(n)
    z = y - np.polyval(np.polyfit(t, y, 1), t)
    puissance = np.abs(np.fft.rfft(z * np.hanning(n))) ** 2
    total = float(puissance[1:].sum())
    if total < 1e-24:
        return []
    pics = [k for k in range(2, len(puissance) - 1) if puissance[k] >= puissance[k - 1] and puissance[k] >= puissance[k + 1]]
    pics = sorted(pics, key=lambda k: -puissance[k])[:LIMITES["cycles"]]
    return [{"periode": _f(n / k, 1), "part": _f(100.0 * puissance[k] / total, 1)} for k in pics]


def _autocorrelation(y) -> dict | None:
    n = len(y)
    h = min(LIMITES["retards"], n // 2)
    c = y - y.mean()
    d = float(np.sum(c ** 2))
    if h < 2 or d < 1e-24:
        return None
    return {"acf": _l([float(np.sum(c[k:] * c[:-k])) / d for k in range(1, h + 1)], 4), "seuil": _f(1.96 / math.sqrt(n), 4)}


def _retournements(x, lisse, largeur: int) -> list[dict]:
    """Sommets et creux de la série lissée (un point est un sommet s'il est le plus haut sur `largeur` périodes de chaque côté)."""
    n, sortie = len(lisse), []
    for i in range(largeur, n - largeur):
        v = lisse[i]
        if v is None:
            continue
        voisins = [w for w in lisse[i - largeur: i + largeur + 1] if w is not None]
        if len(voisins) > 2 and v == max(voisins) and v > min(voisins):
            sortie.append({"x": x[i], "type": "sommet", "valeur": _f(v)})
        elif len(voisins) > 2 and v == min(voisins) and v < max(voisins):
            sortie.append({"x": x[i], "type": "creux", "valeur": _f(v)})
    return sortie[-LIMITES["retournements"]:]


def _profil_cycles(x, y, p: int, phases) -> dict | None:
    """Une ligne par cycle (année pour du mensuel…), une colonne par phase (mois…) : où se place chaque valeur d'un cycle à l'autre."""
    n = len(y)
    if not phases or not 2 <= p <= LIMITES["profil_phases"] or n < 2 * p:
        return None
    debut = max(0, -(-n // p) - LIMITES["profil_cycles"])
    lignes = [{"x": x[k * p], "valeurs": [_f(y[k * p + c]) if k * p + c < n else None for c in range(p)]} for k in range(debut, -(-n // p))]
    return {"phases": list(phases), "lignes": lignes, "moyenne": _l([float(np.mean(y[c::p])) for c in range(p)])}


def explorer(x, y, genre: str = "pas", periode: int = 1, phases=None, moyenne_mobile=None, ordre_mm: int = 3) -> dict | None:
    """Exploration d'UNE série déjà préparée (x = étiquettes de période, y = valeurs sans trou). None si inutilisable."""
    try:
        y = np.array([np.nan if v is None else v for v in y], float)
    except (TypeError, ValueError):
        return None
    n = len(y)
    if n < 6 or len(x) != n or not np.all(np.isfinite(y)):
        return None
    p = int(periode or 1)
    moy, med = float(y.mean()), float(np.median(y))
    sd = float(y.std(ddof=1))
    c = y - moy
    m2 = float(np.mean(c ** 2))
    q = lambda a: _f(np.percentile(y, a))
    i_min, i_max = int(np.argmin(y)), int(np.argmax(y))
    stats = {"n": n, "somme": _f(y.sum()), "moyenne": _f(moy), "mediane": _f(med), "ecart_type": _f(sd), "cv": _f(100 * sd / abs(moy)) if abs(moy) > 1e-12 else None,
             "min": _f(y[i_min]), "x_min": x[i_min], "max": _f(y[i_max]), "x_max": x[i_max], "etendue": _f(y[i_max] - y[i_min]),
             "q05": q(5), "q25": q(25), "q75": q(75), "q95": q(95), "iqr": _f(np.percentile(y, 75) - np.percentile(y, 25)),
             "asymetrie": _f(float(np.mean(c ** 3)) / m2 ** 1.5) if m2 > 1e-24 else None,
             "aplatissement": _f(float(np.mean(c ** 4)) / m2 ** 2 - 3) if m2 > 1e-24 else None}
    d = np.diff(y)
    ok = np.abs(y[:-1]) > 1e-12
    pct = np.where(ok, d / np.where(ok, np.abs(y[:-1]), 1.0) * 100.0, np.nan)
    an = PAR_AN.get(genre)
    cro = {"variation_totale": _f((y[-1] / y[0] - 1) * 100) if abs(y[0]) > 1e-12 else None,
           "tcac": _f(((y[-1] / y[0]) ** (an / (n - 1)) - 1) * 100) if an and y[0] > 0 and y[-1] > 0 else None,
           "hausses": int((d > 0).sum()), "baisses": int((d < 0).sum()), "stables": int((d == 0).sum()),
           "serie_hausses": _plus_longue(d > 0), "serie_baisses": _plus_longue(d < 0),
           "meilleure": {"x": x[int(np.nanargmax(pct)) + 1], "pct": _f(np.nanmax(pct))} if np.isfinite(pct).any() else None,
           "pire": {"x": x[int(np.nanargmin(pct)) + 1], "pct": _f(np.nanmin(pct))} if np.isfinite(pct).any() else None}
    classes = min(LIMITES["classes"], max(5, int(math.ceil(1 + math.log2(n)))))
    comptes, bornes = np.histogram(y, bins=classes)
    w = max(3, min(p if p > 1 else 5, n // 2))
    vol = [None] * w + [_f(float(np.std(d[i - w: i]))) for i in range(w, n)]
    return {"statistiques": stats, "croissance": cro, "histogramme": {"bornes": _l(bornes), "comptes": [int(k) for k in comptes]},
            "atypiques": _atypiques(x, y, p), "retournements": _retournements(x, list(moyenne_mobile or []), max(2, int(ordre_mm or 3) // 2)) if moyenne_mobile else [],
            "cycles": _cycles_dominants(y), "autocorrelation": _autocorrelation(y),
            "volatilite": {"fenetre": w, "valeurs": vol}, "base100": _l(y / y[0] * 100.0, 2) if y[0] > 0 else None,
            "profil": _profil_cycles(x, y, p, phases)}


def _decale(a, b, k: int):
    """Corrélation de a(t) avec b(t + k)."""
    return _corr(a[: len(a) - k], b[k:]) if k >= 0 else _corr(a[-k:], b[: len(b) + k])


def comparer(resultats: list[dict]) -> dict | None:
    """Liens entre les séries analysées ensemble (tables différentes comprises, alignées sur les mêmes périodes). None s'il y a moins de deux séries."""
    s = [r for r in resultats if r and not r.get("erreur") and r.get("y") and r.get("x")][: LIMITES["series"]]
    if len(s) < 2:
        return None
    plusieurs = len({r.get("table_id") for r in s}) > 1
    noms = [f"{r.get('colonne') or r['nom']} ({r.get('table')})" if plusieurs else (r.get("colonne") or r["nom"]) for r in s]
    matrice = [[1.0 if i == j else None for j in range(len(s))] for i in range(len(s))]
    paires = []
    for i in range(len(s)):
        da = dict(zip(s[i]["x"], s[i]["y"]))
        for j in range(i + 1, len(s)):
            db = dict(zip(s[j]["x"], s[j]["y"]))
            communs = [k for k in s[i]["x"] if k in db and da[k] is not None and db[k] is not None]
            if len(communs) < LIMITES["recouvrement"]:
                continue
            u, v = np.array([da[k] for k in communs], float), np.array([db[k] for k in communs], float)
            du, dv = np.diff(u), np.diff(v)
            cv, cn = _corr(du, dv), _corr(u, v)
            matrice[i][j] = matrice[j][i] = _f(cv, 3)
            K = min(LIMITES["decalage"], len(du) // 4)
            lien = [(k, _decale(du, dv, k)) for k in range(-K, K + 1)]
            lien = [(k, r) for k, r in lien if r is not None]
            k, r = max(lien, key=lambda t: abs(t[1])) if lien else (0, None)
            seuil = 1.96 / math.sqrt(max(1, len(du) - abs(k)))
            paires.append({"a": noms[i], "b": noms[j], "periodes_communes": len(communs), "correlation_niveaux": _f(cn, 3), "correlation_variations": _f(cv, 3),
                           "decalage": k if r is not None else None, "correlation_decalee": _f(r, 3), "significatif": bool(r is not None and abs(r) > seuil)})
    paires.sort(key=lambda d: -abs(d["correlation_variations"] or 0))
    return {"noms": noms, "matrice": matrice, "paires": paires[: LIMITES["paires"]]} if paires else None
