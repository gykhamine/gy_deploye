"""ANALYSES AUTOMATIQUES — ANOVA complète et analyses factorielles ACP / ACM / AFC (module pur : numpy + stdlib, sans scipy).

L'utilisateur ne saisit aucune formule : il coche des colonnes, le type de chaque variable est reconnu (engine/statistiques.classer) et la méthode
adaptée est choisie ET justifiée ici. Les tests d'ANOVA (Fisher, Kruskal-Wallis, Brown-Forsythe, plans à plusieurs facteurs) et les lois (p-valeurs)
viennent de engine/statistiques.py et engine/statistiques_lois.py (réutilisés, jamais recopiés). Rien n'est lu en base ni écrit : on reçoit des
variables {nom, valeurs} (lignes alignées) et on renvoie des dictionnaires JSON-sûrs.

Conventions : valeurs manquantes = ligne retirée (suppression par ligne, comptée et signalée) ; au plus MAX_AXES axes renvoyés ; au plus MAX_INDIVIDUS
individus tracés (échantillon régulier, les calculs portent sur toutes les lignes).
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t, td
from engine.i18n import t as _i18n_t, tf

import math
from collections import Counter

import numpy as np

from . import statistiques as S
from . import statistiques_lois as L

MAX_AXES = 10
MAX_INDIVIDUS = 400
MAX_MODALITES = 30      # au-delà, les modalités les plus rares sont regroupées dans « Autres »
FACTEURS_MAX_MULTI = 4


def _pct(x):
    return float(x) * 100.0


def _niveau_effet(eta2):
    return _i18n_t("engine/analyses_auto.py:g:32:12") if eta2 < 0.01 else _i18n_t("rg.effet.faible") if eta2 < 0.06 else _i18n_t("rg.effet.moyen") if eta2 < 0.14 else _i18n_t("rg.effet.fort")


# ═════════════════════════════════════════════════════════════════════════════
# ANOVA AUTOMATIQUE
# ═════════════════════════════════════════════════════════════════════════════
def _resumes_groupes(grp, conf=0.95):
    sortie = []
    for m, x in grp.items():
        n = x.size
        et = float(x.std(ddof=1)) if n > 1 else None
        demi = S._t_crit(n - 1, conf) * et / math.sqrt(n) if et is not None and n > 1 else None
        q1, med, q3 = (float(v) for v in np.percentile(x, [25, 50, 75]))
        sortie.append({"nom": m, "n": int(n), "moyenne": float(x.mean()), "ecart_type": et, "ic_bas": None if demi is None else float(x.mean()) - demi,
                       "ic_haut": None if demi is None else float(x.mean()) + demi, "min": float(x.min()), "q1": q1, "mediane": med, "q3": q3, "max": float(x.max())})
    return sortie


def _welch(grp, alpha):
    """ANOVA de Welch (variances inégales) + comparaisons deux à deux de Welch (Bonferroni). None si une variance est nulle."""
    k = len(grp)
    n = np.array([g.size for g in grp.values()], float)
    m = np.array([g.mean() for g in grp.values()])
    v = np.array([g.var(ddof=1) for g in grp.values()])
    if k < 2 or (v <= 0).any():
        return None
    w = n / v
    W = w.sum()
    mw = (w * m).sum() / W
    lam = ((1 - w / W) ** 2 / (n - 1)).sum()
    f = ((w * (m - mw) ** 2).sum() / (k - 1)) / (1 + 2 * (k - 2) / (k * k - 1) * lam)
    d1, d2 = k - 1.0, (k * k - 1) / (3 * lam)
    p = L.sf("fisher", {"d1": d1, "d2": d2}, f)
    paires = []
    if p < alpha and k <= 10:
        noms = list(grp)
        nb = k * (k - 1) // 2
        for i, a in enumerate(noms):
            for b in noms[i + 1:]:
                va, vb = v[i] / n[i], v[noms.index(b)] / n[noms.index(b)]
                j = noms.index(b)
                se = math.sqrt(va + vb)
                df = (va + vb) ** 2 / (va ** 2 / (n[i] - 1) + vb ** 2 / (n[j] - 1))
                d = float(m[i] - m[j])
                pp = min(1.0, S._p_t(d / se, df, "different") * nb)
                paires.append({"a": a, "b": b, "difference": d, "p": pp, "significatif": pp < alpha})
    return S._sortie(_i18n_t("engine/analyses_auto.py:g:78:22"), _i18n_t("engine/analyses_auto.py:g:78:61"), _i18n_t("engine/analyses_auto.py:g:78:107"), _i18n_t("engine/analyses_auto.py:g:78:139"), f, f"{int(d1)} ; {d2:.1f}", p, alpha,
                     (_i18n_t("engine/analyses_auto.py:g:79:23"), _i18n_t("engine/analyses_auto.py:g:79:107")),
                     comparaisons={"libelle": _i18n_t("engine/analyses_auto.py:g:81:47"), "lignes": paires})


def _hypotheses(grp, alpha):
    """(normalité des résidus, homogénéité des variances) + lignes de diagnostic lisibles."""
    res = np.concatenate([x - x.mean() for x in grp.values()])
    nm = S._normalite_dagostino(np.sort(res))
    petit = min(x.size for x in grp.values())
    normal = None if nm is None else nm[1] >= alpha
    normal_ok = normal is not False or petit >= 30      # grands groupes : le théorème central limite protège la moyenne
    f, p, d1, d2 = S._levene(grp)
    homogene = p >= alpha
    diag = [{"nom": _i18n_t("engine/analyses_auto.py:g:92:21"), "p": None if nm is None else nm[1], "ok": normal_ok,
             "texte": _i18n_t("engine/analyses_auto.py:g:93:23") if nm is None else (_i18n_t("engine/analyses_auto.py:g:93:76") if normal else (_i18n_t("engine/analyses_auto.py:g:93:116") if petit >= 30 else _i18n_t("engine/analyses_auto.py:g:93:193")))},
            {"nom": _i18n_t("engine/analyses_auto.py:g:94:21"), "p": p, "ok": homogene, "texte": "variances comparables" if homogene else _i18n_t("engine/analyses_auto.py:g:94:141")}]
    return normal_ok, homogene, diag


def _anova_un_facteur(reponse, facteur, alpha):
    grp = {m: x for m, x in S._groupes(reponse, facteur).items() if x.size >= 2}
    if len(grp) < 2:
        raise ValueError(tf("engine/analyses_auto.py:g:101:26", v1=facteur['nom'], v2=len(grp)))
    normal_ok, homogene, diag = _hypotheses(grp, alpha)
    variables = {"a": reponse, "groupe": facteur}
    res = {"anova": S.executer("anova", variables, {"alpha": alpha}), "kruskal": S.executer("kruskal", variables, {"alpha": alpha})}
    welch = _welch(grp, alpha)
    if welch is not None:
        res["welch"] = welch
    if normal_ok and homogene:
        retenu, raison = "anova", _i18n_t("engine/analyses_auto.py:g:109:35")
    elif normal_ok and welch is not None:
        retenu, raison = "welch", _i18n_t("engine/analyses_auto.py:g:111:35")
    else:
        retenu, raison = "kruskal", _i18n_t("engine/analyses_auto.py:g:113:37")
    r = res[retenu]
    eta2 = res["anova"].get("effet", {}).get("valeur")
    p_txt = S._p_fmt(r['p']) if S._p_fmt(r['p']).startswith('<') else '= ' + S._p_fmt(r['p'])
    phrase = tf("rg3.analyses_auto.differe" if r['significatif'] else "rg3.analyses_auto.ne_differe_pas",
                nom=reponse['nom'], facteur=facteur['nom'], test=r['test'].split(' — ')[0], p=p_txt)
    if eta2 is not None:
        phrase += tf("engine/analyses_auto.py:g:118:19", v1=_niveau_effet(eta2), v2=eta2, v3=eta2 * 100)
    sig = [f"{c['a']} ≠ {c['b']}" for c in (r.get("comparaisons") or {}).get("lignes", []) if c.get("significatif")]
    if sig:
        phrase += _i18n_t("engine/analyses_auto.py:g:121:19") + " ; ".join(sig[:8]) + "."
    return {"facteur": facteur["nom"], "n": int(sum(x.size for x in grp.values())), "retenu": retenu, "raison": raison, "hypotheses": diag, "resultats": res,
            "groupes": _resumes_groupes(grp), "synthese": phrase}


def _anova_multi(reponse, facteurs, alpha):
    y = [S._reel(v) for v in reponse["valeurs"]]
    etiq = [[S.etiquette(v) for v in f["valeurs"]] for f in facteurs]
    cellules: dict = {}
    for i, u in enumerate(y):
        mods = tuple(e[i] for e in etiq)
        if u is not None and None not in mods:
            cellules.setdefault(mods, []).append(u)
    grp = {" × ".join(k): np.array(v) for k, v in sorted(cellules.items()) if len(v) >= 2}
    avis = []
    if len(grp) >= 2:
        res = np.concatenate([x - x.mean() for x in grp.values()])
        nm = S._normalite_dagostino(np.sort(res))
        if nm is not None and nm[1] < alpha and min(x.size for x in grp.values()) < 30:   # l'égalité des variances est déjà signalée par le moteur du plan
            avis.append(_i18n_t("engine/analyses_auto.py:g:140:25"))
    if len(facteurs) == 2:
        r = S.executer("anova2", {"a": reponse, "groupe": facteurs[0], "groupe2": facteurs[1]}, {"alpha": alpha, "interaction": "1"})
    else:
        r = S.executer("anovan", {"a": reponse, "explicatives": facteurs}, {"alpha": alpha, "ordre": 2})
    croise = None
    if len(facteurs) >= 2:
        m1, m2 = sorted({k[0] for k in cellules}), sorted({k[1] for k in cellules})
        if len(m1) <= 12 and len(m2) <= 12:
            croise = {"facteur_x": facteurs[0]["nom"], "facteur_serie": facteurs[1]["nom"], "x": m1, "series": [
                {"nom": b, "moyennes": [float(np.mean([u for k, v in cellules.items() if k[0] == a and k[1] == b for u in v])) if any(k[0] == a and k[1] == b for k in cellules) else None for a in m1]} for b in m2]}
    termes = [t for t in r.get("tableau_anova", []) if t.get("p") is not None]
    sig = [t["source"] for t in termes if t["p"] < alpha]
    phrase = (tf("engine/analyses_auto.py:g:153:15", v1=reponse['nom']) + " ; ".join(sig) + ".") if sig else tf("engine/analyses_auto.py:g:153:122", v1=reponse['nom'])
    return {"facteurs": [f["nom"] for f in facteurs], "resultat": r, "croise": croise, "avertissements": avis, "synthese": phrase}


def anova_auto(reponse: dict, facteurs: list[dict], alpha: float = 0.05) -> dict:
    """ANOVA complète d'une variable numérique selon un ou plusieurs facteurs qualitatifs : chaque facteur seul (test choisi et justifié d'après les
    hypothèses), puis le plan à plusieurs facteurs avec interactions (jusqu'à 4 facteurs). Un facteur inutilisable est signalé sans bloquer les autres."""
    if not facteurs:
        raise ValueError(_i18n_t("engine/analyses_auto.py:g:161:26"))
    seuls, ignores = [], []
    for f in facteurs:
        try:
            seuls.append(_anova_un_facteur(reponse, f, alpha))
        except ValueError as e:
            ignores.append({"facteur": f["nom"], "raison": str(e)})
    multi = None
    if len(facteurs) >= 2:
        try:
            multi = _anova_multi(reponse, facteurs[:FACTEURS_MAX_MULTI], alpha)
            if len(facteurs) > FACTEURS_MAX_MULTI:
                multi["avertissements"].append(tf("engine/analyses_auto.py:g:173:48", v1=FACTEURS_MAX_MULTI))
        except ValueError as e:
            ignores.append({"facteur": " × ".join(f["nom"] for f in facteurs), "raison": str(e)})
    if not seuls and multi is None:
        raise ValueError(ignores[0]["raison"] if ignores else _i18n_t("engine/analyses_auto.py:g:177:63"))
    return S.nettoyer({"reponse": reponse["nom"], "alpha": alpha, "un_facteur": seuls, "multi": multi, "ignores": ignores})


# ═════════════════════════════════════════════════════════════════════════════
# ANALYSES FACTORIELLES
# ═════════════════════════════════════════════════════════════════════════════
def _valeurs_propres(vp, total, extra=None):
    cum, sortie = 0.0, []
    for k, v in enumerate(vp[:MAX_AXES]):
        pc = _pct(v / total) if total > 0 else 0.0
        cum += pc
        sortie.append({"axe": k + 1, "valeur": float(v), "pct": pc, "pct_cumule": cum, **((extra or {}).get(k) or {})})
    return sortie


def _echantillon_regulier(n, maxi=MAX_INDIVIDUS):
    return np.arange(n) if n <= maxi else np.unique(np.linspace(0, n - 1, maxi).astype(int))


def _regrouper(valeurs, maxi=MAX_MODALITES):
    """Garde les `maxi - 1` modalités les plus fréquentes ; les autres → « Autres ». Renvoie (valeurs, regroupé ?)."""
    cpt = Counter(valeurs)
    if len(cpt) <= maxi:
        return list(valeurs), False
    garde = {m for m, _ in cpt.most_common(maxi - 1)}
    return [v if v in garde else "Autres" for v in valeurs], True


def _signe(vec):
    """Convention de signe : somme de chaque axe positive (résultat reproductible)."""
    s = np.where(vec.sum(0) < 0, -1.0, 1.0)
    return vec * s, s


def _adequation(R, n):
    """KMO global et test de sphéricité de Bartlett d'une matrice de corrélations."""
    p = R.shape[0]
    sortie = {"kmo": None, "bartlett": None, "verdict": None}
    sign, logdet = np.linalg.slogdet(R)
    if sign > 0 and n > 1 + (2 * p + 5) / 6:
        chi2 = -(n - 1 - (2 * p + 5) / 6) * logdet
        ddl = p * (p - 1) / 2
        sortie["bartlett"] = {"chi2": float(chi2), "ddl": ddl, "p": L.sf("khi2", {"k": ddl}, chi2)}
    try:
        inv = np.linalg.inv(R)
        d = np.sqrt(np.outer(np.diag(inv), np.diag(inv)))
        part = -inv / d
        hors = ~np.eye(p, dtype=bool)
        r2, q2 = (R[hors] ** 2).sum(), (part[hors] ** 2).sum()
        sortie["kmo"] = float(r2 / (r2 + q2)) if r2 + q2 > 0 else None
    except np.linalg.LinAlgError:
        pass
    k = sortie["kmo"]
    if k is not None:
        sortie["verdict"] = (_i18n_t("rg.kmo.excellente") if k >= 0.9 else _i18n_t("rg.kmo.bonne") if k >= 0.8 else _i18n_t("rg.kmo.correcte") if k >= 0.7 else _i18n_t("rg.kmo.mediocre") if k >= 0.6 else _i18n_t("rg.kmo.faible") if k >= 0.5 else _i18n_t("rg.kmo.inadequate")) + td("engine/analyses_auto.py:g:233:183")
    return sortie


def acp(variables: list[dict], supplementaires: list[dict] | None = None, reduire: bool = True) -> dict:
    """ACP normée (variables centrées-réduites) : valeurs propres, cercle des corrélations, contributions, cos², individus ; `supplementaires` =
    variables qualitatives dont les modalités sont projetées au barycentre de leurs individus (sans influencer les axes)."""
    if len(variables) < 2:
        raise ValueError(_i18n_t("engine/analyses_auto.py:g:240:26"))
    cols = [[S._reel(v) for v in var["valeurs"]] for var in variables]
    garde = [i for i in range(len(cols[0])) if all(c[i] is not None for c in cols)]
    n, p = len(garde), len(variables)
    if n < max(p + 1, 5):
        raise ValueError(tf("engine/analyses_auto.py:g:245:26", v1=n, v2=p, v3=max(p + 1, 5)))
    X = np.array([[c[i] for c in cols] for i in garde], float)
    moy, sd = X.mean(0), X.std(0)
    for j, s in enumerate(sd):
        if s <= 0:
            raise ValueError(tf("engine/analyses_auto.py:g:250:30", v1=variables[j]['nom']))
    Z = (X - moy) / (sd if reduire else 1.0)
    R = Z.T @ Z / n
    vp, vec = np.linalg.eigh(R)
    o = np.argsort(vp)[::-1]
    vp, vec = np.clip(vp[o], 0, None), vec[:, o]
    vec, _s = _signe(vec)
    total = float(vp.sum())
    racine = np.sqrt(vp)
    coord_var = vec * racine
    cos_var = coord_var ** 2 / np.maximum((coord_var ** 2).sum(1, keepdims=True), 1e-300)
    F = Z @ vec
    d2 = (Z ** 2).sum(1)
    cos_ind = F ** 2 / np.maximum(d2, 1e-300)[:, None]
    contrib_ind = F ** 2 / (n * np.maximum(vp, 1e-300)) * 100
    moyenne = total / p
    kaiser = max(2, int((vp > moyenne + 1e-12).sum()))
    baton = [sum(1.0 / i for i in range(k, p + 1)) / p for k in range(1, p + 1)]
    bs = max(1, int(sum(1 for k in range(p) if vp[k] / total > baton[k])))
    na = min(p, MAX_AXES)
    nb_aff = min(p, 5)
    idx = _echantillon_regulier(n)
    individus = [{"ligne": int(garde[i]) + 1, "coord": F[i, :nb_aff].tolist(), "cos2": cos_ind[i, :nb_aff].tolist()} for i in idx]
    tops = []
    for k in range(min(2, p)):
        for i in np.argsort(contrib_ind[:, k])[::-1][:8]:
            tops.append({"axe": k + 1, "ligne": int(garde[i]) + 1, "contrib": float(contrib_ind[i, k]), "coord": float(F[i, k])})
    sup = []
    for var in (supplementaires or [])[:3]:
        lab = [S.etiquette(var["valeurs"][i]) for i in garde]
        for m, c in Counter(v for v in lab if v is not None).most_common(12):
            if c >= 2:
                sel = np.array([l == m for l in lab])
                sup.append({"variable": var["nom"], "modalite": m, "n": int(c), "coord": F[sel, :nb_aff].mean(0).tolist()})
    return S.nettoyer({
        "methode": "acp", "n": n, "p": p, "lignes_retirees": len(cols[0]) - n, "reduite": reduire, "variance_totale": total,
        "valeurs_propres": _valeurs_propres(vp, total, {k: {"kaiser": bool(vp[k] > moyenne), "baton": bool(vp[k] / total > baton[k])} for k in range(na)}),
        "axes_retenus": {"kaiser": kaiser, "baton_brise": bs},
        "variables": [{"nom": variables[j]["nom"], "coord": coord_var[j, :na].tolist(), "cos2": cos_var[j, :na].tolist(), "contrib": (vec[j, :na] ** 2 * 100).tolist(),
                       "moyenne": float(moy[j]), "ecart_type": float(sd[j])} for j in range(p)],
        "individus": individus, "individus_total": n, "individus_extremes": tops, "supplementaires": sup, "adequation": _adequation(np.corrcoef(X, rowvar=False), n),
        "avertissements": ([tf("engine/analyses_auto.py:g:291:29", v1=len(cols[0]) - n)] if n < len(cols[0]) else []) + ([_i18n_t("engine/analyses_auto.py:g:291:145")] if not reduire else [])})


def acm(variables: list[dict]) -> dict:
    """ACM sur le tableau disjonctif complet : valeurs propres avec corrections de Benzécri et de Greenacre, coordonnées / contributions / cos² des
    modalités, rapports de corrélation (η²) des variables, individus. Modalités au-delà de MAX_MODALITES regroupées dans « Autres »."""
    Q = len(variables)
    if Q < 2:
        raise ValueError(_i18n_t("engine/analyses_auto.py:g:299:26"))
    etiq = [[S.etiquette(v) for v in var["valeurs"]] for var in variables]
    garde = [i for i in range(len(etiq[0])) if all(e[i] is not None for e in etiq)]
    n = len(garde)
    if n < 10:
        raise ValueError(tf("engine/analyses_auto.py:g:304:26", v1=n))
    avis, noms_mod, var_de, colonnes = [], [], [], []
    for q, var in enumerate(variables):
        vals, regroupe = _regrouper([etiq[q][i] for i in garde])
        mods = sorted(set(vals))
        if len(mods) < 2:
            raise ValueError(tf("engine/analyses_auto.py:g:310:30", v1=var['nom']))
        if regroupe:
            avis.append(tf("engine/analyses_auto.py:g:312:25", v1=var['nom'], v2=MAX_MODALITES))
        for m in mods:
            colonnes.append(np.array([v == m for v in vals], float))
            noms_mod.append(m)
            var_de.append(q)
    Zd = np.column_stack(colonnes)
    J = Zd.shape[1]
    nj = Zd.sum(0)
    for j in range(J):
        if nj[j] < 0.05 * n:
            avis.append(tf("engine/analyses_auto.py:g:322:25", v1=variables[var_de[j]]['nom'], v2=noms_mod[j], v3=nj[j] / n * 100))
    c = nj / (n * Q)
    Sm = np.sqrt(n) * (Zd / (n * Q) - c[None, :] / n) / np.sqrt(c)[None, :]
    vp, V = np.linalg.eigh(Sm.T @ Sm)
    o = np.argsort(vp)[::-1]
    vp, V = np.clip(vp[o], 0, None), V[:, o]
    K = max(1, min(J - Q, int((vp > 1e-12).sum())))
    vp, V = vp[:K], V[:, :K]
    V, _s = _signe(V)
    racine = np.sqrt(vp)
    Fm = V * racine / np.sqrt(c)[:, None]                 # coordonnées principales des modalités
    G = np.sqrt(n) * (Sm @ V)                              # coordonnées principales des individus
    inertie = (J - Q) / Q
    seuil = 1.0 / Q
    benz = np.where(vp > seuil, (Q / (Q - 1.0)) ** 2 * (vp - seuil) ** 2, 0.0)
    greenacre_den = (Q / (Q - 1.0)) * (float((vp ** 2).sum()) - (J - Q) / Q ** 2)
    extra = {k: {"benzecri": _pct(benz[k] / benz.sum()) if benz.sum() > 0 else None, "greenacre": _pct(benz[k] / greenacre_den) if greenacre_den > 0 else None} for k in range(min(K, MAX_AXES))}
    na, nb_aff = min(K, MAX_AXES), min(K, 5)
    cos2 = Fm ** 2 / np.maximum((Fm ** 2).sum(1, keepdims=True), 1e-300)
    modalites = [{"variable": variables[var_de[j]]["nom"], "modalite": noms_mod[j], "n": int(nj[j]), "pct": float(nj[j] / n * 100), "coord": Fm[j, :na].tolist(),
                  "contrib": (V[j, :na] ** 2 * 100).tolist(), "cos2": cos2[j, :na].tolist()} for j in range(J)]
    eta = []
    for q, var in enumerate(variables):
        cols_q = [j for j in range(J) if var_de[j] == q]
        e = []
        for k in range(na):
            s = sum(nj[j] * (float(Zd[:, j] @ G[:, k]) / nj[j]) ** 2 for j in cols_q)
            e.append(float(s / (n * vp[k])) if vp[k] > 0 else 0.0)
        eta.append({"nom": var["nom"], "eta2": e})
    idx = _echantillon_regulier(n)
    retenus = max(2, int((vp > seuil).sum()))
    return S.nettoyer({
        "methode": "acm", "n": n, "variables_n": Q, "modalites_n": J, "lignes_retirees": len(etiq[0]) - n, "inertie_totale": inertie, "valeur_propre_moyenne": seuil,
        "valeurs_propres": _valeurs_propres(vp, inertie, extra), "axes_retenus": {"moyenne": retenus, "benzecri": int((vp > seuil).sum())},
        "modalites": modalites, "eta2": eta,
        "individus": [{"ligne": int(garde[i]) + 1, "coord": G[i, :nb_aff].tolist()} for i in idx], "individus_total": n,
        "avertissements": avis[:12] + ([tf("engine/analyses_auto.py:g:358:41", v1=len(etiq[0]) - n)] if n < len(etiq[0]) else [])})


def afc(a: dict, b: dict) -> dict:
    """AFC du tableau de contingence de deux variables qualitatives : khi-deux d'indépendance, V de Cramér, axes, profils lignes / colonnes
    (coordonnées, contributions, cos²), résidus standardisés (attractions et répulsions entre modalités)."""
    ea, eb = [S.etiquette(v) for v in a["valeurs"]], [S.etiquette(v) for v in b["valeurs"]]
    garde = [i for i in range(len(ea)) if ea[i] is not None and eb[i] is not None]
    n = len(garde)
    if n < 10:
        raise ValueError(tf("engine/analyses_auto.py:g:368:26", v1=n))
    va, ra = _regrouper([ea[i] for i in garde])
    vb, rb = _regrouper([eb[i] for i in garde])
    lig, col = sorted(set(va)), sorted(set(vb))
    if len(lig) < 2 or len(col) < 2:
        raise ValueError(_i18n_t("engine/analyses_auto.py:g:373:26"))
    il, ic = {m: i for i, m in enumerate(lig)}, {m: i for i, m in enumerate(col)}
    N = np.zeros((len(lig), len(col)))
    for u, v in zip(va, vb):
        N[il[u], ic[v]] += 1
    P = N / n
    r, c = P.sum(1), P.sum(0)
    E = np.outer(r, c) * n
    Sm = (P - np.outer(r, c)) / np.sqrt(np.outer(r, c))
    U, s, Vt = np.linalg.svd(Sm, full_matrices=False)
    K = min(len(lig), len(col)) - 1
    s, U, V = s[:K], U[:, :K], Vt.T[:, :K]
    sg = np.where(U.sum(0) < 0, -1.0, 1.0)
    U, V = U * sg, V * sg
    vp = s ** 2
    inertie = float(vp.sum())
    chi2, ddl = n * inertie, (len(lig) - 1) * (len(col) - 1)
    Fl, Fc = U * s / np.sqrt(r)[:, None], V * s / np.sqrt(c)[:, None]
    cos_l, cos_c = Fl ** 2 / np.maximum((Fl ** 2).sum(1, keepdims=True), 1e-300), Fc ** 2 / np.maximum((Fc ** 2).sum(1, keepdims=True), 1e-300)
    na = min(K, MAX_AXES)
    faibles = float((E < 5).mean())
    avis = [tf("engine/analyses_auto.py:g:394:13", v1=a['nom'])] if ra else []
    avis += [tf("engine/analyses_auto.py:g:395:14", v1=b['nom'])] if rb else []
    if faibles > 0.2:
        avis.append(tf("engine/analyses_auto.py:g:397:21", v1=faibles * 100))

    def _mods(noms, eff, F, V_, cs):
        return [{"modalite": noms[i], "n": int(eff[i]), "pct": float(eff[i] / n * 100), "coord": F[i, :na].tolist(), "contrib": (V_[i, :na] ** 2 * 100).tolist(), "cos2": cs[i, :na].tolist()} for i in range(len(noms))]
    return S.nettoyer({
        "methode": "afc", "n": n, "variable_lignes": a["nom"], "variable_colonnes": b["nom"], "lignes_retirees": len(ea) - n,
        "khi2": {"valeur": float(chi2), "ddl": ddl, "p": L.sf("khi2", {"k": float(ddl)}, float(chi2)), "cramer": float(math.sqrt(inertie / min(len(lig) - 1, len(col) - 1)))},
        "inertie_totale": inertie, "valeurs_propres": _valeurs_propres(vp, inertie), "axes_retenus": {"moyenne": max(1, int((vp > inertie / K).sum()))},
        "lignes": _mods(lig, N.sum(1), Fl, U, cos_l), "colonnes": _mods(col, N.sum(0), Fc, V, cos_c),
        "tableau": {"lignes": lig, "colonnes": col, "effectifs": N, "residus": (N - E) / np.sqrt(E)}, "avertissements": avis})
