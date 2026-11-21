"""STATISTIQUES GROUPÉES — analyser des variables ENSEMBLE, pas une par une (module pur : numpy + lois maison, sans scipy).

Trois façons de regrouper (une fonction chacune, mêmes conventions que `statistiques.relations` : on reçoit des variables
{nom, genre, valeurs} alignées ligne à ligne, on renvoie un dictionnaire JSON sûr avec des phrases de lecture déjà traduites) :

  continues(variables)                  variables CONTINUES entre elles : corrélations (Pearson, Spearman, p), multicolinéarité (VIF), sphéricité de
                                        Bartlett, KMO, alpha de Cronbach, valeurs propres, observations aberrantes multivariées (Mahalanobis).
  qualitatives(variables)               variables QUALITATIVES entre elles : V de Cramér (brut et corrigé), khi-deux, p ajustés (Holm), pouvoir prédictif
                                        (incertitude de Theil), diversité de chaque variable, profils les plus fréquents.
  continues_par_qualitative(vars, g)    plusieurs variables CONTINUES comparées entre les groupes d'UNE variable qualitative : un test par variable
                                        (ANOVA, Kruskal-Wallis, Levene, η², p ajustés), profils standardisés des groupes, MANOVA (Wilks, Pillai) et
                                        distances entre groupes (Mahalanobis).

Les calculs univariés réutilisent `statistiques._anova` / `_levene` / `_cramer_v` / `_rangs` : une seule implémentation de chaque test.
"""
from __future__ import annotations

import math
from collections import Counter

import numpy as np

from engine.i18n import t as _t, tf

from . import statistiques as _s
from . import statistiques_lois as L

MAX_VARIABLES = 20          # variables d'une analyse groupée (les suivantes sont ignorées)
MAX_PAIRES = 20             # paires listées
MAX_PROFILS = 10            # profils les plus fréquents
MAX_VARIABLES_PROFIL = 6    # variables combinées pour les profils
MAX_GROUPES_DISTANCES = 12  # groupes dans la matrice des distances


# ── Utilitaires ──────────────────────────────────────────────────────────────
def _sf(cle, params, x):
    try:
        return float(L.sf(cle, params, x))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _p_f(f, d1, d2):
    return _sf("fisher", {"d1": float(d1), "d2": float(d2)}, f) if f is not None and d1 > 0 and d2 > 0 else None


def _p_phrase(p):
    """« < 0,0001 » ou « = 0,0123 » : se lit après la lettre p dans une phrase."""
    t = str(_s._p_fmt(p))
    return t if t.startswith("<") else "= " + t


def _holm(ps):
    """p ajustés de Holm-Bonferroni (None conservés)."""
    idx = sorted((i for i, p in enumerate(ps) if p is not None), key=lambda i: ps[i])
    sortie, maxi, m = [None] * len(ps), 0.0, len(idx)
    for rang, i in enumerate(idx):
        maxi = max(maxi, min(1.0, ps[i] * (m - rang)))
        sortie[i] = maxi
    return sortie


def _reels(valeurs):
    return [_s._reel(v) for v in valeurs]


def _lignes_completes(colonnes):
    """Indices des lignes où TOUTES les colonnes (listes alignées) ont une valeur."""
    return [i for i in range(len(colonnes[0])) if all(c[i] is not None for c in colonnes)]


def _force_nom(v):
    return _s._force(v)


# ══ 1. Variables continues entre elles ═══════════════════════════════════════
def continues(variables: list[dict]) -> dict:
    cols = [c for c in variables if _s.est_quanti(c["genre"])][:MAX_VARIABLES]
    if len(cols) < 2:
        raise ValueError(_t("sg.err.deux_continues"))
    brut = [_reels(c["valeurs"]) for c in cols]
    garde = _lignes_completes(brut)
    n_total = len(brut[0])
    X = np.array([[b[i] for b in brut] for i in garde], dtype=float)
    avert = []
    if len(garde) < n_total:
        avert.append(tf("sg.avert.lignes_perdues", v1=n_total - len(garde), v2=n_total))
    if X.shape[0] >= 2:
        sd = X.std(axis=0, ddof=1)
        constantes = [cols[j]["nom"] for j in range(len(cols)) if sd[j] == 0]
        if constantes:
            avert.append(tf("sg.avert.constantes", v1=", ".join(constantes)))
            ok = [j for j in range(len(cols)) if sd[j] > 0]
            cols, X = [cols[j] for j in ok], X[:, ok]
    n, p = X.shape
    if p < 2:
        raise ValueError(_t("sg.err.deux_continues"))
    if n < p + 2 or n < 5:
        raise ValueError(tf("sg.err.lignes", v1=n))
    noms = [c["nom"] for c in cols]

    R = np.corrcoef(X, rowvar=False)
    S = np.cov(X, rowvar=False)
    RS = np.corrcoef(np.apply_along_axis(_s._rangs, 0, X), rowvar=False)
    P = [[None if i == j else _s._p_corr(float(R[i, j]), n) for j in range(p)] for i in range(p)]
    det = float(np.linalg.det(R))
    Rinv = np.linalg.pinv(R)
    diag = np.clip(np.diag(Rinv), 1.0, None)
    vif = diag.tolist()
    if det < 1e-10:
        avert.append(_t("sg.avert.colinearite"))

    # Sphéricité de Bartlett : les variables sont-elles liées du tout ? (H0 : matrice de corrélations = identité)
    bartlett = None
    if det > 0:
        khi2 = -(n - 1 - (2 * p + 5) / 6) * math.log(det)
        ddl = p * (p - 1) // 2
        bartlett = {"khi2": khi2, "ddl": ddl, "p": _sf("khi2", {"k": float(ddl)}, khi2)}

    # KMO : les corrélations partielles sont-elles faibles devant les corrélations ? (> 0,7 : l'ensemble forme un tout cohérent)
    d = np.sqrt(np.diag(Rinv))
    Q = -Rinv / np.outer(d, d)
    hors = ~np.eye(p, dtype=bool)
    r2, q2 = float((R[hors] ** 2).sum()), float((Q[hors] ** 2).sum())
    kmo = r2 / (r2 + q2) if r2 + q2 > 0 else None
    msa = []
    for j in range(p):
        rj, qj = float((np.delete(R[j], j) ** 2).sum()), float((np.delete(Q[j], j) ** 2).sum())
        msa.append(rj / (rj + qj) if rj + qj > 0 else None)

    # Alpha de Cronbach : les variables mesurent-elles un même thème ? (à lire seulement si c'est le cas)
    somme_cov = float(S.sum())
    alpha_brut = p / (p - 1) * (1 - float(np.trace(S)) / somme_cov) if p > 1 and somme_cov > 0 else None
    rbar = float(R[hors].mean())
    alpha_std = p * rbar / (1 + (p - 1) * rbar) if 1 + (p - 1) * rbar > 0 else None

    # Valeurs propres de R : combien de dimensions « indépendantes » dans l'ensemble ?
    ev = np.clip(np.linalg.eigvalsh(R)[::-1], 0, None)
    cumul, valeurs_propres = 0.0, []
    for v in ev:
        cumul += v / p * 100
        valeurs_propres.append({"valeur": float(v), "pct": float(v / p * 100), "cumul": float(min(cumul, 100.0))})
    kaiser = int((ev > 1).sum())

    # Aberrantes multivariées : une ligne peut être normale variable par variable et très inhabituelle par la COMBINAISON de ses valeurs
    Xc = X - X.mean(axis=0)
    d2 = np.einsum("ij,jk,ik->i", Xc, np.linalg.pinv(S), Xc)
    seuil = float(L.quantile("khi2", {"k": float(p)}, 0.999))
    ordre = [int(i) for i in np.argsort(-d2) if d2[i] > seuil][:5]
    aberrantes = {"seuil": seuil, "n": int((d2 > seuil).sum()), "lignes": [{"ligne": garde[i] + 1, "d2": float(d2[i])} for i in ordre]}

    paires = []
    for i in range(p):
        for j in range(i + 1, p):
            paires.append({"a": noms[i], "b": noms[j], "mesure": "Pearson r", "valeur": float(R[i, j]), "spearman": float(RS[i, j]), "p": P[i][j], "n": n,
                           "force": _force_nom(float(R[i, j]))})
    paires.sort(key=lambda r: -abs(r["valeur"]))
    paires = paires[:MAX_PAIRES]
    for q in paires[:6]:     # nuage de points pour les six liens les plus marqués
        i, j = noms.index(q["a"]), noms.index(q["b"])
        q["graphique"] = _s._g_nuage(X[:, i].tolist(), X[:, j].tolist(), q["valeur"], q["a"], q["b"])

    forts = sum(1 for q in paires if abs(q["valeur"]) >= 0.7)
    lecture = [tf("sg.lec.continues", v1=p, v2=n, v3=forts)]
    if bartlett and bartlett["p"] is not None:
        lecture.append(tf("sg.lec.bartlett_oui" if bartlett["p"] < 0.05 else "sg.lec.bartlett_non", v1=_p_phrase(bartlett["p"])))
    if kmo is not None:
        lecture.append(tf("sg.lec.kmo", v1=f"{kmo:.2f}", v2=_t("sg.kmo.bon" if kmo >= 0.7 else "sg.kmo.moyen" if kmo >= 0.5 else "sg.kmo.faible")))
    redondantes = [noms[j] for j in range(p) if vif[j] >= 10]
    if redondantes:
        lecture.append(tf("sg.lec.vif", v1=", ".join(redondantes)))
    lecture.append(tf("sg.lec.dimensions", v1=kaiser, v2=p))
    if aberrantes["n"]:
        lecture.append(tf("sg.lec.aberrantes", v1=aberrantes["n"]))
    if alpha_std is not None:
        lecture.append(tf("sg.lec.alpha", v1=f"{alpha_std:.2f}"))

    return {"mode": "continues", "n": n, "n_total": n_total, "p": p,
            "variables": [{"nom": noms[j], "moyenne": float(X[:, j].mean()), "ecart_type": float(X[:, j].std(ddof=1)), "vif": float(vif[j]), "tolerance": float(1 / vif[j]),
                           "msa": msa[j], "redondante": vif[j] >= 10} for j in range(p)],
            "matrice": {"noms": noms, "pearson": R.tolist(), "spearman": RS.tolist(), "p": P}, "covariance": S.tolist(),
            "paires": paires,
            "global": {"determinant": det, "bartlett": bartlett, "kmo": kmo, "alpha_brut": alpha_brut, "alpha_standardise": alpha_std, "kaiser": kaiser,
                       "valeurs_propres": valeurs_propres},
            "aberrantes": aberrantes, "lecture": lecture, "avertissements": avert}


# ══ 2. Variables qualitatives entre elles ════════════════════════════════════
def _entropie(effectifs):
    t = float(np.sum(effectifs))
    p = np.asarray(effectifs, dtype=float)
    p = p[p > 0] / t
    return float(-(p * np.log(p)).sum())


def _theil(T):
    """Incertitude de Theil U(ligne | colonne) : part d'incertitude sur la ligne ôtée quand on connaît la colonne (0 : rien, 1 : tout)."""
    h = _entropie(T.sum(axis=1))
    if h <= 0:
        return None
    n = T.sum()
    cond = 0.0
    for j in range(T.shape[1]):
        cj = T[:, j].sum()
        if cj > 0:
            cond += cj / n * _entropie(T[:, j])
    return float(max(0.0, (h - cond) / h))


def _croiser(a, b):
    lignes = [(u, v) for u, v in zip(a, b) if u is not None and v is not None]
    if len(lignes) < 5:
        return None
    ma, mb = sorted({u for u, _ in lignes}), sorted({v for _, v in lignes})
    if len(ma) < 2 or len(mb) < 2:
        return None
    cpt = Counter(lignes)
    return ma, mb, np.array([[cpt[(u, w)] for w in mb] for u in ma], dtype=float), len(lignes)


def qualitatives(variables: list[dict]) -> dict:
    maxi = _s.LIMITES["modalites_croisees"]
    avert, cols = [], []
    for c in variables[:MAX_VARIABLES]:
        if _s.est_quanti(c["genre"]) or c["genre"] == "date":
            continue
        etiq = [_s.etiquette(v) for v in c["valeurs"]]
        k = len({e for e in etiq if e is not None})
        if not 2 <= k <= maxi:
            avert.append(tf("sg.avert.modalites", v1=c["nom"], v2=maxi, v3=k))
            continue
        cols.append({"nom": c["nom"], "etiq": etiq, "k": k})
    if len(cols) < 2:
        raise ValueError(tf("sg.err.deux_quali", v1=maxi))
    p = len(cols)
    noms = [c["nom"] for c in cols]
    V = [[1.0 if i == j else None for j in range(p)] for i in range(p)]
    PV = [[None] * p for _ in range(p)]
    U = [[None] * p for _ in range(p)]      # U[i][j] : variable i expliquée par la variable j
    paires = []
    for i in range(p):
        for j in range(i + 1, p):
            cr = _croiser(cols[i]["etiq"], cols[j]["etiq"])
            if cr is None:
                continue
            ma, mb, T, n = cr
            khi2, v, ddl = _s._cramer_v(T)
            phi2 = khi2 / n
            r, c = T.shape
            phi2c = max(0.0, phi2 - (r - 1) * (c - 1) / (n - 1))
            rc, cc = r - (r - 1) ** 2 / (n - 1), c - (c - 1) ** 2 / (n - 1)
            v_corr = math.sqrt(phi2c / min(rc - 1, cc - 1)) if min(rc, cc) > 1 and phi2c > 0 else 0.0
            pv = _sf("khi2", {"k": float(ddl)}, khi2)
            V[i][j] = V[j][i] = float(v)
            PV[i][j] = PV[j][i] = pv
            U[i][j], U[j][i] = _theil(T), _theil(T.T)
            paires.append({"a": noms[i], "b": noms[j], "mesure": _t("engine/statistiques.py:g:450:76"), "valeur": float(v), "v_corrige": float(v_corr), "khi2": float(khi2),
                           "ddl": int(ddl), "p": pv, "n": n, "force": _force_nom(v), "theil_ab": U[i][j], "theil_ba": U[j][i],
                           "_g": {"type": "croise", "lignes": ma, "colonnes": mb, "effectifs": T.astype(int).tolist(), "nom_l": noms[i], "nom_c": noms[j]}})
    for q, pa in zip(paires, _holm([q["p"] for q in paires])):
        q["p_ajuste"] = pa
    paires.sort(key=lambda r: -r["valeur"])
    for rang, q in enumerate(paires):
        g = q.pop("_g")
        if rang < 6:
            q["graphique"] = g
    paires = paires[:MAX_PAIRES * 2]

    resume = []
    for i, c in enumerate(cols):
        effectifs = list(Counter(e for e in c["etiq"] if e is not None).values())
        autres = [(V[i][j], noms[j]) for j in range(p) if j != i and V[i][j] is not None]
        resume.append({"nom": c["nom"], "modalites": c["k"], "n": int(sum(effectifs)), "diversite": _entropie(effectifs) / math.log(c["k"]),
                       "v_moyen": float(np.mean([v for v, _ in autres])) if autres else None, "plus_lie": max(autres)[1] if autres else None})

    # Profils : les combinaisons de modalités les plus fréquentes sur l'ensemble des variables (les moins diverses d'abord : profils lisibles)
    choisies = sorted(range(p), key=lambda i: cols[i]["k"])[:MAX_VARIABLES_PROFIL]
    complet = [i for i in range(len(cols[0]["etiq"])) if all(cols[j]["etiq"][i] is not None for j in choisies)]
    profils = []
    if complet:
        cpt = Counter(tuple(cols[j]["etiq"][i] for j in choisies) for i in complet)
        profils = [{"modalites": dict(zip([noms[j] for j in choisies], combo)), "effectif": e, "frequence": e / len(complet) * 100} for combo, e in cpt.most_common(MAX_PROFILS)]

    liens = [q for q in paires if q["p_ajuste"] is not None and q["p_ajuste"] < 0.05]
    lecture = [tf("sg.lec.quali", v1=p, v2=len(paires), v3=len(liens))]
    if liens:
        top = liens[0]
        lecture.append(tf("sg.lec.quali_top", v1=top["a"], v2=top["b"], v3=f"{top['valeur']:.2f}", v4=top["force"]))
    jumeaux = [q for q in paires if q["valeur"] >= 0.8]
    if jumeaux:
        lecture.append(tf("sg.lec.quali_redondantes", v1="; ".join(f"{q['a']} ↔ {q['b']}" for q in jumeaux[:4])))
    predictifs = sorted(((q["theil_ab"], q["a"], q["b"]) for q in paires if q["theil_ab"] is not None), reverse=True)
    predictifs += sorted(((q["theil_ba"], q["b"], q["a"]) for q in paires if q["theil_ba"] is not None), reverse=True)
    if predictifs:
        u, expliquee, explicative = max(predictifs)
        if u >= 0.1:
            lecture.append(tf("sg.lec.quali_theil", v1=explicative, v2=expliquee, v3=f"{u * 100:.0f}"))
    if profils:
        lecture.append(tf("sg.lec.quali_profil", v1=f"{profils[0]['frequence']:.1f}"))
    return {"mode": "qualitatives", "p": p, "variables": resume, "matrice": {"noms": noms, "cramer": V, "p": PV, "theil": U}, "paires": paires, "profils": profils,
            "n_profils": len(complet), "lecture": lecture, "avertissements": avert}


# ══ 3. Plusieurs variables continues selon une variable qualitative ══════════
def _kruskal_p(groupes):
    tout = np.concatenate(groupes)
    N = tout.size
    rangs = _s._rangs(tout)
    h, debut = 0.0, 0
    for g in groupes:
        h += rangs[debut:debut + g.size].sum() ** 2 / g.size
        debut += g.size
    h = 12 / (N * (N + 1)) * h - 3 * (N + 1)
    corr = 1 - _s._corr_ex(tout) / (N ** 3 - N) if N > 1 else 1
    if corr <= 0:
        return None
    return _sf("khi2", {"k": float(len(groupes) - 1)}, h / corr)


def _manova(X, etiq, groupes):
    """MANOVA à un facteur : Wilks (approximation de Rao) et Pillai (approximation F). None + raison si le calcul est impossible."""
    n, p = X.shape
    k = len(groupes)
    mu = X.mean(axis=0)
    W = np.zeros((p, p))
    B = np.zeros((p, p))
    centres = {}
    for m in groupes:
        Xg = X[[i for i, e in enumerate(etiq) if e == m]]
        mg = Xg.mean(axis=0)
        centres[m] = mg
        W += (Xg - mg).T @ (Xg - mg)
        B += len(Xg) * np.outer(mg - mu, mg - mu)
    ve, vh = n - k, k - 1
    if ve <= p:
        return None, W, centres, _t("sg.manova.raison_lignes")
    if abs(np.linalg.det(W)) < 1e-12 * max(1.0, float(np.trace(W)) ** p):
        return None, W, centres, _t("sg.manova.raison_singuliere")
    lam = np.clip(np.linalg.eigvals(np.linalg.solve(W, B)).real, 0, None)
    wilks = float(np.prod(1 / (1 + lam)))
    pillai = float((lam / (1 + lam)).sum())
    # Wilks, approximation F de Rao
    pq = p * vh
    denom = p * p + vh * vh - 5
    t = math.sqrt((p * p * vh * vh - 4) / denom) if denom > 0 else 1.0
    w = ve + vh - (p + vh + 1) / 2
    df1, df2 = pq, w * t - (pq - 2) / 2
    racine = wilks ** (1 / t) if wilks > 0 else 0.0
    f_w = (1 - racine) / racine * df2 / df1 if racine > 0 and df2 > 0 else None
    # Pillai
    s = min(p, vh)
    m, nn = (abs(p - vh) - 1) / 2, (ve - p - 1) / 2
    d1p, d2p = s * (2 * m + s + 1), s * (2 * nn + s + 1)
    f_p = (2 * nn + s + 1) / (2 * m + s + 1) * pillai / (s - pillai) if s - pillai > 0 else None
    return {"wilks": {"lambda": wilks, "f": f_w, "ddl1": df1, "ddl2": df2, "p": _p_f(f_w, df1, df2)},
            "pillai": {"trace": pillai, "f": f_p, "ddl1": d1p, "ddl2": d2p, "p": _p_f(f_p, d1p, d2p)},
            "eta2": float(1 - wilks ** (1 / s)) if s > 0 and wilks > 0 else None}, W, centres, None


def continues_par_qualitative(variables: list[dict], groupe: dict) -> dict:
    if groupe["genre"] == "date" or _s.est_quanti(groupe["genre"]):
        raise ValueError(tf("sg.err.groupe_quali", v1=groupe["nom"]))
    cols = [c for c in variables if _s.est_quanti(c["genre"]) and c is not groupe][:MAX_VARIABLES]
    if not cols:
        raise ValueError(_t("sg.err.une_continue"))
    maxg = _s.LIMITES["groupes"]
    etiq_brut = [_s.etiquette(v) for v in groupe["valeurs"]]
    brut = [_reels(c["valeurs"]) for c in cols]
    n_total = len(etiq_brut)
    avert = []

    def groupes_de(indices):
        cpt = Counter(etiq_brut[i] for i in indices)
        return sorted(m for m, e in cpt.items() if e >= 2)

    # Un test par variable : lignes disponibles pour CETTE variable et le groupe
    resultats = []
    for c, b in zip(cols, brut):
        idx = [i for i in range(n_total) if b[i] is not None and etiq_brut[i] is not None]
        mod = groupes_de(idx)
        if not 2 <= len(mod) <= maxg:
            avert.append(tf("sg.err.groupes", v1=groupe["nom"], v2=maxg, v3=len(mod)))
            continue
        grp = {m: np.array([b[i] for i in idx if etiq_brut[i] == m], dtype=float) for m in mod}
        tout = np.concatenate(list(grp.values()))
        mg, sg = float(tout.mean()), float(tout.std(ddof=1)) if tout.size > 1 else 0.0
        r = {"nom": c["nom"], "n": int(tout.size), "groupes": [{"nom": m, "n": int(g.size), "moyenne": float(g.mean()), "ecart_type": float(g.std(ddof=1)), "mediane": float(np.median(g)),
                                                              "z": (float(g.mean()) - mg) / sg if sg > 0 else None} for m, g in grp.items()]}
        try:
            ssb, ssw, d1, d2, f, pv = _s._anova(grp)
            r.update({"f": f, "ddl": [d1, d2], "p": pv, "eta2": ssb / (ssb + ssw), "omega2": max(0.0, (ssb - d1 * ssw / d2) / (ssb + ssw + ssw / d2))})
        except ValueError:      # variance nulle dans tous les groupes : rien à comparer
            r.update({"f": None, "ddl": None, "p": None, "eta2": None, "omega2": None})
        r["kruskal_p"] = _kruskal_p(list(grp.values()))
        r["levene_p"] = _s._levene(grp)[1]
        hauts = max(r["groupes"], key=lambda q: q["moyenne"])
        bas = min(r["groupes"], key=lambda q: q["moyenne"])
        r["plus_haut"], r["plus_bas"] = hauts["nom"], bas["nom"]
        resultats.append(r)
    if not resultats:
        raise ValueError(tf("sg.err.groupes", v1=groupe["nom"], v2=maxg, v3=0))
    for r, pa in zip(resultats, _holm([r["p"] for r in resultats])):
        r["p_ajuste"] = pa
    resultats.sort(key=lambda r: -(r["eta2"] or 0))
    for r in resultats[:6]:     # boîtes par groupe pour les six variables qui séparent le mieux les groupes
        i = next(j for j, c in enumerate(cols) if c["nom"] == r["nom"])
        g = {m: [brut[i][k] for k in range(n_total) if etiq_brut[k] == m and brut[i][k] is not None] for m in (q["nom"] for q in r["groupes"])}
        r["graphique"] = _s._g_groupes(g, groupe["nom"], r["nom"])

    # Profil des groupes : écart de chaque moyenne de groupe à la moyenne générale, en écarts-types (comparable d'une variable à l'autre)
    noms_v = [r["nom"] for r in resultats]
    noms_g = sorted({q["nom"] for r in resultats for q in r["groupes"]})
    profil = [[next((q["z"] for q in r["groupes"] if q["nom"] == m), None) for r in resultats] for m in noms_g]

    # Multivarié : lignes complètes sur toutes les variables + le groupe
    sortie_multi, distances, raison = None, None, None
    complet = [i for i in range(n_total) if etiq_brut[i] is not None and all(b[i] is not None for b in brut)]
    if len(complet) < n_total:
        avert.append(tf("sg.avert.lignes_perdues", v1=n_total - len(complet), v2=n_total))
    mod = groupes_de(complet)
    if len(cols) >= 2 and 2 <= len(mod) <= maxg:
        etiq = [etiq_brut[i] for i in complet if etiq_brut[i] in mod]
        X = np.array([[b[i] for b in brut] for i in complet if etiq_brut[i] in mod], dtype=float)
        sd = X.std(axis=0, ddof=1)
        X = X[:, sd > 0]
        if X.shape[1] >= 2:
            sortie_multi, W, centres, raison = _manova(X, etiq, mod)
            if sortie_multi is not None and len(mod) <= MAX_GROUPES_DISTANCES:
                Sw = np.linalg.pinv(W / (X.shape[0] - len(mod)))
                distances = {"groupes": mod, "d2": [[float((centres[a] - centres[b]) @ Sw @ (centres[a] - centres[b])) for b in mod] for a in mod]}
    elif len(cols) >= 2:
        raison = _t("sg.manova.raison_lignes")
    if raison:
        avert.append(tf("sg.avert.manova_non", v1=raison))
    petits = [m for m in noms_g if any(q["nom"] == m and q["n"] < 5 for r in resultats for q in r["groupes"])]
    if petits:
        avert.append(tf("sg.avert.petits_groupes", v1=", ".join(petits[:6])))

    sig = [r for r in resultats if r["p_ajuste"] is not None and r["p_ajuste"] < 0.05]
    lecture = [tf("sg.lec.par", v1=len(resultats), v2=groupe["nom"], v3=len(noms_g), v4=len(sig))]
    if sig:
        top = sig[0]
        lecture.append(tf("sg.lec.par_top", v1=top["nom"], v2=f"{(top['eta2'] or 0) * 100:.0f}", v3=top["plus_haut"], v4=top["plus_bas"]))
    if sortie_multi and sortie_multi["wilks"]["p"] is not None:
        lecture.append(tf("sg.lec.manova_oui" if sortie_multi["wilks"]["p"] < 0.05 else "sg.lec.manova_non", v1=groupe["nom"], v2=_p_phrase(sortie_multi["wilks"]["p"])))
    if distances:
        d2 = distances["d2"]
        g = distances["groupes"]
        pr = [(d2[i][j], g[i], g[j]) for i in range(len(g)) for j in range(i + 1, len(g))]
        if pr:
            lecture.append(tf("sg.lec.par_proches", v1=min(pr)[1], v2=min(pr)[2], v3=max(pr)[1], v4=max(pr)[2]))
    return {"mode": "continues_par_quali", "groupe": groupe["nom"], "n": len(complet), "n_total": n_total, "variables": resultats,
            "profil": {"groupes": noms_g, "variables": noms_v, "z": profil}, "multivarie": sortie_multi, "distances": distances, "lecture": lecture, "avertissements": avert}
