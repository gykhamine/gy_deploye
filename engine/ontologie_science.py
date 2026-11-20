"""Couche « science des données » de l'ontologie (2026-09-23) : 25 types de nœuds, 30 relations.

Le graphe standard (ontologie.py) décrit la STRUCTURE et l'ACTIVITÉ (tables, colonnes, agents, calculs…).
Cette couche y ajoute ce qu'un data scientist cherche en premier dans chaque table, calculé sur les
vraies données (5 000 lignes les plus récentes au plus) : qualité, forme des distributions, valeurs
aberrantes, tendances, saisons, ruptures, dérives, corrélations, dépendances, segments, clés probables
entre tables, et des recommandations concrètes. Résultat mis en cache tant que les données ne changent
pas (metadata.empreinte_donnees).

`analyser_table(df, champs)` et `analyser_liens(tables)` sont PURES (pandas/numpy, sans Django) ;
`enrichir_science(g, entreprise)` les branche sur le graphe networkx.
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tf
from engine.i18n import t

import logging
import math

logger = logging.getLogger("gyplatform.ontologie")

LIGNES_MAX = None   # None = toutes les lignes (plus d'échantillon des 5 000 dernières)

# ── 25 types de nœuds ─────────────────────────────────────────────────
TYPES_NOEUDS_SCIENCE = {
    "profil_qualite": ("#0f766e", td("engine/ontologie_science.py:g:26:35")),
    "valeurs_manquantes": ("#b45309", td("engine/ontologie_science.py:g:27:39")),
    "valeur_aberrante": ("#dc2626", td("engine/ontologie_science.py:g:28:37")),
    "distribution": ("#7c3aed", td("engine/ontologie_science.py:g:29:33")),
    "variable_constante": ("#6b7280", td("engine/ontologie_science.py:g:30:39")),
    "identifiant_candidat": ("#1d4ed8", td("engine/ontologie_science.py:g:31:41")),
    "variable_categorielle": ("#0891b2", td("engine/ontologie_science.py:g:32:42")),
    "variable_temporelle": ("#4338ca", td("engine/ontologie_science.py:g:33:40")),
    "tendance": ("#16a34a", td("engine/ontologie_science.py:g:34:29")),
    "saisonnalite": ("#65a30d", td("engine/ontologie_science.py:g:35:33")),
    "rupture": ("#ea580c", td("engine/ontologie_science.py:g:36:28")),
    "groupe_correle": ("#9333ea", td("engine/ontologie_science.py:g:37:35")),
    "segment": ("#db2777", td("engine/ontologie_science.py:g:38:28")),
    "anomalie_multivariee": ("#991b1b", td("engine/ontologie_science.py:g:39:41")),
    "doublon_lignes": ("#c2410c", td("engine/ontologie_science.py:g:40:35")),
    "desequilibre": ("#a16207", td("engine/ontologie_science.py:g:41:33")),
    "concentration": ("#be123c", td("engine/ontologie_science.py:g:42:34")),
    "cible_candidate": ("#0369a1", td("engine/ontologie_science.py:g:43:36")),
    "facteur_explicatif": ("#15803d", td("engine/ontologie_science.py:g:44:39")),
    "dependance_fonctionnelle": ("#57534e", td("engine/ontologie_science.py:g:45:45")),
    "cle_etrangere_candidate": ("#2563eb", td("engine/ontologie_science.py:g:46:44")),
    "derive_donnees": ("#f59e0b", td("engine/ontologie_science.py:g:47:35")),
    "fraicheur": ("#0e7490", td("engine/ontologie_science.py:g:48:30")),
    "croissance_volume": ("#059669", td("engine/ontologie_science.py:g:49:38")),
    "recommandation": ("#e11d48", td("engine/ontologie_science.py:g:50:35")),
}

# ── 30 relations ──────────────────────────────────────────────────────
RELATIONS_SCIENCE = {
    "a_profil": ("#14b8a6", td("engine/ontologie_science.py:g:55:29")),
    "manque_dans": ("#f59e0b", td("engine/ontologie_science.py:g:57:32")),
    "aberrant_dans": ("#ef4444", td("engine/ontologie_science.py:g:57:34")),
    "suit_distribution": ("#a78bfa", td("engine/ontologie_science.py:g:58:38")),
    "est_constante": ("#9ca3af", td("engine/ontologie_science.py:g:59:34")),
    "identifie_lignes": ("#3b82f6", td("engine/ontologie_science.py:g:60:37")),
    "est_categorielle": ("#22d3ee", td("engine/ontologie_science.py:g:61:37")),
    "horodate": ("#6366f1", td("engine/ontologie_science.py:g:62:29")),
    "evolue_selon": ("#22c55e", td("engine/ontologie_science.py:g:63:33")),
    "cycle_de": ("#84cc16", td("engine/ontologie_science.py:g:64:29")),
    "rompt": ("#fb923c", td("engine/ontologie_science.py:g:65:26")),
    "correle_positivement": ("#10b981", td("engine/ontologie_science.py:g:66:41")),
    "correle_negativement": ("#f43f5e", td("engine/ontologie_science.py:g:67:41")),
    "membre_du_groupe": ("#c084fc", td("engine/ontologie_science.py:g:68:37")),
    "segmente_en": ("#f472b6", td("engine/ontologie_science.py:g:69:32")),
    "caracterise": ("#ec4899", td("engine/ontologie_science.py:g:70:32")),
    "anomalie_detectee_dans": ("#b91c1c", td("engine/ontologie_science.py:g:71:43")),
    "duplique": ("#ea580c", td("engine/ontologie_science.py:g:72:29")),
    "desequilibre_de": ("#ca8a04", td("engine/ontologie_science.py:g:73:36")),
    "concentre": ("#e11d48", td("engine/ontologie_science.py:g:74:30")),
    "cible_de": ("#0284c7", td("engine/ontologie_science.py:g:75:29")),
    "explique": ("#16a34a", td("engine/ontologie_science.py:g:76:29")),
    "determine": ("#78716c", td("engine/ontologie_science.py:g:77:30")),
    "est_determine_par": ("#a8a29e", td("engine/ontologie_science.py:g:78:38")),
    "reference_probable": ("#1d4ed8", td("engine/ontologie_science.py:g:79:39")),
    "derive_dans": ("#fbbf24", td("engine/ontologie_science.py:g:80:32")),
    "fraicheur_de": ("#06b6d4", td("engine/ontologie_science.py:g:81:33")),
    "croissance_de": ("#34d399", td("engine/ontologie_science.py:g:82:34")),
    "recommande": ("#fb7185", td("engine/ontologie_science.py:g:83:31")),
    "associe_a": ("#38bdf8", td("engine/ontologie_science.py:g:84:30")),
}

_TYPES_NUM = {"nombre", "decimal", "monnaie", "pourcentage", "barre_progression", "note", "formule", "constante"}
_TYPES_DATE = {"date", "datetime", "horodatage"}


def _r(v, d=3):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), d)


# ── Analyse pure d'une table ──────────────────────────────────────────
def analyser_table(df, champs: dict) -> list[dict]:
    """df : pandas.DataFrame casté ; champs : {slug: {"id": int, "nom": str, "type": type_champ}}.
    Retourne une liste de constats {"type", "cle", "label", "champ"|None, "attrs", "liens": [...]} —
    "liens" = [(relation, "champ:<slug>" | "table" | "constat:<cle>", sens)] où sens = "vers" (constat →
    cible) ou "depuis" (cible → constat)."""
    import numpy as np
    import pandas as pd

    constats = []

    def ajouter(type_, cle, label, liens, champ=None, **attrs):
        constats.append({"type": type_, "cle": cle, "label": label, "champ": champ,
                         "attrs": {k: v for k, v in attrs.items() if v is not None}, "liens": liens})

    cols = [c for c in df.columns if c in champs]
    if df.empty or not cols:
        return constats
    n = len(df)
    num = [c for c in cols if champs[c]["type"] in _TYPES_NUM or
           (pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]))]
    dat = [c for c in cols if champs[c]["type"] in _TYPES_DATE or pd.api.types.is_datetime64_any_dtype(df[c])]
    num = [c for c in num if c not in dat]
    serie = {c: pd.to_numeric(df[c], errors="coerce").to_numpy(dtype="float64", na_value=np.nan) for c in num}

    # 1. Profil de qualité
    remplissage = float(df[cols].notna().mean().mean())
    doublons = int(df[cols].astype(str).duplicated().sum())
    constantes = [c for c in cols if df[c].nunique(dropna=True) <= 1 and df[c].notna().any()]
    score = 100 * (0.6 * remplissage + 0.25 * (1 - doublons / n) + 0.15 * (1 - len(constantes) / len(cols)))
    ajouter("profil_qualite", "profil", tf("engine/ontologie_science.py:g:124:41", v1=score), [("a_profil", "table", "depuis")],
            score=_r(score, 1), remplissage_pct=_r(100 * remplissage, 1), lignes=n)
    if score < 75:
        ajouter("recommandation", "reco_qualite", t("engine/ontologie_science.py:g:127:51"),
                [("recommande", "constat:profil", "depuis")],
                detail=t("engine/ontologie_science.py:g:129:24"))

    # 2-7. Constats par colonne
    for c in cols:
        lien_c = f"champ:{c}"
        nom = champs[c]["nom"]
        rempli = float(df[c].notna().mean())
        if rempli < 0.8:
            ajouter("valeurs_manquantes", f"manque_{c}", f"{nom} : {100 * rempli:.0f} % rempli",
                    [("manque_dans", lien_c, "vers")], champ=c, taux=_r(100 * rempli, 1))
        valeurs = df[c].dropna()
        if valeurs.empty:
            continue
        texte = valeurs.astype(str)
        nuniq = texte.nunique()
        if nuniq == 1:
            ajouter("variable_constante", f"const_{c}", f"{nom} constante", [("est_constante", lien_c, "depuis")], champ=c)
            continue
        if nuniq >= 0.99 * len(texte) and len(texte) >= 5 and c not in num and c not in dat:  # tolère quelques doublons
            ajouter("identifiant_candidat", f"id_{c}", f"{nom} = identifiant", [("identifie_lignes", "table", "vers")], champ=c)
        elif c not in num and c not in dat and nuniq <= 20:
            ajouter("variable_categorielle", f"cat_{c}", tf("engine/ontologie_science.py:g:150:58", v1=nom, v2=nuniq),
                    [("est_categorielle", lien_c, "depuis")], champ=c, modalites=nuniq)
            part = float(texte.value_counts(normalize=True).iloc[0])
            if part > 0.8:
                ajouter("desequilibre", f"deseq_{c}", tf("engine/ontologie_science.py:g:154:55", v1=nom, v2=100 * part),
                        [("desequilibre_de", lien_c, "vers")], champ=c, part_pct=_r(100 * part, 1))
        if c in dat:
            d = pd.to_datetime(valeurs, errors="coerce").dropna()
            if len(d):
                ajouter("variable_temporelle", f"temps_{c}", f"{nom} : {d.min():%d/%m/%Y} → {d.max():%d/%m/%Y}",
                        [("horodate", "table", "vers")], champ=c, debut=str(d.min().date()), fin=str(d.max().date()),
                        jours=int((d.max() - d.min()).days))

    # Colonnes numériques : distribution, aberrantes, concentration, dérive, tendance, saison, rupture
    for c in num:
        x = serie[c][~np.isnan(serie[c])]
        nom, lien_c = champs[c]["nom"], f"champ:{c}"
        if len(x) < 8 or not x.std():
            continue
        m, s = x.mean(), x.std(ddof=1)
        asym = float(((x - m) ** 3).mean() / s ** 3)
        aplat = float(((x - m) ** 4).mean() / s ** 4 - 3)
        hist, _b = np.histogram(x, bins=min(20, max(5, len(x) // 10)))
        pics = int(((hist[1:-1] > hist[:-2]) & (hist[1:-1] > hist[2:]) & (hist[1:-1] > 0.3 * hist.max())).sum())
        forme = (t("engine/ontologie_science.py:g:174:18") if pics >= 2 else
                 t("engine/ontologie_science.py:g:175:18") if asym > 1 else
                 t("engine/ontologie_science.py:g:176:18") if asym < -1 else
                 t("engine/ontologie_science.py:g:177:18") if aplat > 3 else t("engine/ontologie_science.py:g:177:77"))
        ajouter("distribution", f"distrib_{c}", f"{nom} : {forme.split(' (')[0]}",
                [("suit_distribution", lien_c, "depuis")], champ=c, forme=forme, asymetrie=_r(asym), aplatissement=_r(aplat))
        q1, q3 = np.percentile(x, [25, 75]); e = q3 - q1
        nb_ab = int(((x < q1 - 1.5 * e) | (x > q3 + 1.5 * e)).sum()) if e > 0 else 0
        if nb_ab:
            ajouter("valeur_aberrante", f"aberr_{c}", tf("engine/ontologie_science.py:g:183:55", v1=nom, v2=nb_ab),
                    [("aberrant_dans", lien_c, "vers")], champ=c, nombre=nb_ab, bas=_r(q1 - 1.5 * e), haut=_r(q3 + 1.5 * e))
            if nb_ab / len(x) > 0.02:
                ajouter("recommandation", f"reco_aberr_{c}", tf("engine/ontologie_science.py:g:186:62", v1=nb_ab, v2=nom),
                        [("recommande", f"constat:aberr_{c}", "depuis")],
                        detail=t("engine/ontologie_science.py:g:188:32"))
        pos = np.sort(x[x >= 0])[::-1]
        if len(pos) >= 10 and pos.sum() > 0:
            part = pos[:max(1, int(math.ceil(0.2 * len(pos))))].sum() / pos.sum()
            if part >= 0.8:
                ajouter("concentration", f"conc_{c}", tf("engine/ontologie_science.py:g:193:55", v1=nom, v2=100 * part),
                        [("concentre", lien_c, "vers")], champ=c, part_top20_pct=_r(100 * part, 1))
        if len(x) >= 30:
            k = int(len(x) * 0.8)
            a, b = x[:k], x[k:]
            sp = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)) if len(b) > 1 else 0
            if sp and abs(b.mean() - a.mean()) / sp > 3 and abs(b.mean() - a.mean()) > 0.2 * abs(a.mean() or 1):
                ajouter("derive_donnees", f"derive_{c}", tf("engine/ontologie_science.py:g:200:58", v1=nom),
                        [("derive_dans", lien_c, "vers")], champ=c, moyenne_ancienne=_r(a.mean()), moyenne_recente=_r(b.mean()))
        # Séries dans l'ordre chronologique des lignes (df trié du plus ancien au plus récent)
        if len(x) >= 12:
            xs = x[np.linspace(0, len(x) - 1, min(len(x), 600)).astype(int)]  # tau de Kendall sur ≤ 600 points
            rang = np.arange(len(xs))     # (pas « t » : masquerait la fonction de traduction t() de tout le module)
            concord = np.sign(np.subtract.outer(xs, xs)) * np.sign(np.subtract.outer(rang, rang))
            tau = float(concord[np.triu_indices(len(xs), 1)].mean())
            z = 3 * tau * math.sqrt(len(xs) * (len(xs) - 1)) / math.sqrt(2 * (2 * len(xs) + 5))
            if abs(z) > 1.96 and abs(tau) > 0.1:
                sens = "hausse" if tau > 0 else "baisse"
                ajouter("tendance", f"tend_{c}", f"{nom} : {sens}", [("evolue_selon", lien_c, "vers")],
                        champ=c, sens=sens, tau=_r(tau), z=_r(z, 2))
            # Saisonnalité sur la série DIFFÉRENCIÉE (sinon une tendance ou une rupture font croire à un cycle
            # de 2) : on retient le pic local d'autocorrélation le plus haut entre 3 et 24.
            dx = np.diff(x); dx = dx - dx.mean()
            den = (dx ** 2).sum() or 1.0
            ac = {lag: float((dx[:-lag] * dx[lag:]).sum() / den) for lag in range(2, min(26, len(dx) // 2))}
            pics = {k: v for k, v in ac.items() if 3 <= k <= 24 and k - 1 in ac and k + 1 in ac
                    and v > ac[k - 1] and v >= ac[k + 1]}
            if pics:
                lag = max(pics, key=pics.get)
                if pics[lag] > max(0.3, 2.5 / math.sqrt(len(dx))):
                    ajouter("saisonnalite", f"saison_{c}", tf("engine/ontologie_science.py:g:223:60", v1=nom, v2=lag), [("cycle_de", lien_c, "vers")],
                            champ=c, periode=lag, autocorrelation=_r(ac[lag]))
            lo_, hi_ = np.percentile(x, [2, 98])
            xw = np.clip(x, lo_, hi_)  # rupture de NIVEAU, pas quelques valeurs extrêmes
            cs = np.cumsum(xw - xw.mean())
            kk = int(np.argmax(np.abs(cs[:-1]))) + 1
            av, ap = xw[:kk], xw[kk:]
            if 3 <= kk <= len(x) - 3:
                sp = math.sqrt(av.var(ddof=1) / len(av) + ap.var(ddof=1) / len(ap))
                if sp and abs(ap.mean() - av.mean()) / sp > 4:
                    ajouter("rupture", f"rupt_{c}", tf("engine/ontologie_science.py:g:233:53", v1=nom, v2=kk + 1), [("rompt", lien_c, "vers")],
                            champ=c, ligne=kk + 1, moyenne_avant=_r(av.mean()), moyenne_apres=_r(ap.mean()))

    # Corrélations, groupes redondants, cible et facteurs
    if len(num) >= 2:
        dn = pd.DataFrame({c: serie[c] for c in num})
        corr = dn.corr(min_periods=8)
        adj = {c: set() for c in num}
        for i, a in enumerate(num):
            for b in num[i + 1:]:
                rr = corr.loc[a, b]
                if rr is None or (isinstance(rr, float) and math.isnan(rr)):
                    continue
                if rr >= 0.5:
                    ajouter("_arete", f"corr_{a}_{b}", "", [("correle_positivement", f"champ:{b}", "depuis_champ:" + a)], r=_r(rr))
                elif rr <= -0.5:
                    ajouter("_arete", f"corr_{a}_{b}", "", [("correle_negativement", f"champ:{b}", "depuis_champ:" + a)], r=_r(rr))
                if abs(rr) >= 0.7:
                    adj[a].add(b); adj[b].add(a)
        vus = set()
        for c in num:
            if c in vus or not adj[c]:
                continue
            groupe, pile = set(), [c]
            while pile:
                v = pile.pop()
                if v not in groupe:
                    groupe.add(v); pile.extend(adj[v] - groupe)
            vus |= groupe
            cle = "groupe_" + "_".join(sorted(groupe))[:60]
            ajouter("groupe_correle", cle, tf("engine/ontologie_science.py:g:263:44", v1=len(groupe)),
                    [("membre_du_groupe", f"champ:{g}", "depuis") for g in sorted(groupe)], colonnes=sorted(groupe))
            ajouter("recommandation", "reco_" + cle, t("engine/ontologie_science.py:g:265:54"),
                    [("recommande", f"constat:{cle}", "depuis")],
                    detail=t("engine/ontologie_science.py:g:267:28"))
        moy = corr.abs().where(~np.eye(len(num), dtype=bool)).mean()
        cible = moy.idxmax() if moy.notna().any() and moy.max() >= 0.3 else None
        if cible is not None:
            ajouter("cible_candidate", f"cible_{cible}", f"Cible : {champs[cible]['nom']}", [("cible_de", f"champ:{cible}", "vers")],
                    champ=cible, raison=t("engine/ontologie_science.py:g:274:41"))
            for c in num:
                if c != cible and abs(corr.loc[c, cible]) >= 0.4:
                    ajouter("facteur_explicatif", f"fact_{c}", f"{champs[c]['nom']} explique {champs[cible]['nom']}",
                            [("explique", f"constat:cible_{cible}", "vers")], champ=c, r=_r(corr.loc[c, cible]))
    for c in cols:
        if c in num or c in dat:
            continue
        niv = df[c].dropna().astype(str)
        if niv.nunique() == 2 and champs[c]["type"] in ("booleen", "switch", "choix_unique", "texte"):
            ajouter("cible_candidate", f"cible_{c}", tf("engine/ontologie_science.py:g:282:54", v1=champs[c]['nom']), [("cible_de", f"champ:{c}", "vers")],
                    champ=c, raison=t("engine/ontologie_science.py:g:285:37"))

    # Associations et dépendances entre colonnes catégorielles
    cats = [c for c in cols if c not in num and c not in dat and 2 <= df[c].dropna().astype(str).nunique() <= 50]
    for i, a in enumerate(cats[:15]):
        for b in cats[i + 1:15]:
            d = df[[a, b]].dropna().astype(str)
            if len(d) < 20:
                continue
            tab = pd.crosstab(d[a], d[b]).to_numpy(dtype="float64")
            att = tab.sum(axis=1, keepdims=True) * tab.sum(axis=0, keepdims=True) / tab.sum()
            chi2 = float(((tab - att) ** 2 / np.where(att == 0, 1, att)).sum())
            kmin = min(tab.shape) - 1
            v = math.sqrt(chi2 / (tab.sum() * kmin)) if kmin > 0 else 0
            if v >= 0.3:
                ajouter("_arete", f"assoc_{a}_{b}", "", [("associe_a", f"champ:{b}", "depuis_champ:" + a)], v_cramer=_r(v))
            ab = d[a].nunique() < len(d) and d.groupby(a)[b].nunique().max() == 1 and d[b].nunique() > 1
            ba = d[b].nunique() < len(d) and d.groupby(b)[a].nunique().max() == 1 and d[a].nunique() > 1
            for x_, y_, ok in ((a, b, ab), (b, a, ba and not ab)):  # équivalence : une seule dépendance
                if ok:
                    lib = "≡" if ab and ba else "→"
                    ajouter("dependance_fonctionnelle", f"dep_{x_}_{y_}", f"{champs[x_]['nom']} {lib} {champs[y_]['nom']}",
                            [("determine", f"champ:{x_}", "depuis"), ("est_determine_par", f"champ:{y_}", "vers")],
                            equivalence=bool(ab and ba))

    # Doublons, anomalies multivariées, segments
    if doublons:
        ajouter("doublon_lignes", "doublons", tf("engine/ontologie_science.py:g:310:47", v1=doublons), [("duplique", "table", "vers")], nombre=doublons)
        ajouter("recommandation", "reco_doublons", f"Fusionner {doublons} doublon(s)", [("recommande", "constat:doublons", "depuis")],
                detail=t("engine/ontologie_science.py:g:312:24"))
    if len(num) >= 2:
        mtx = np.column_stack([serie[c] for c in num])
        mtx = mtx[~np.isnan(mtx).any(axis=1)]
        if len(mtx) > len(num) + 10:
            mu = mtx.mean(axis=0); inv = np.linalg.pinv(np.cov(mtx, rowvar=False))
            d2 = np.einsum("ij,jk,ik->i", mtx - mu, inv, mtx - mu)
            seuil = len(num) + 3 * math.sqrt(2 * len(num))  # ≈ quantile élevé du khi² à p degrés de liberté
            nb = int((d2 > seuil).sum())
            if nb:
                ajouter("anomalie_multivariee", "anomalies", tf("engine/ontologie_science.py:g:322:62", v1=nb), [("anomalie_detectee_dans", "table", "vers")],
                        nombre=nb, seuil=_r(seuil))
            if len(mtx) >= 30:
                z = (mtx - mu) / np.where(mtx.std(axis=0) == 0, 1, mtx.std(axis=0))
                rng = np.random.default_rng(42)
                centres = z[rng.choice(len(z), 3, replace=False)]
                for _i in range(40):
                    lab = np.argmin(((z[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2), axis=1)
                    centres = np.array([z[lab == k].mean(axis=0) if (lab == k).any() else centres[k] for k in range(3)])
                for k in range(3):
                    if not (lab == k).any():
                        continue
                    j = int(np.argmax(np.abs(centres[k])))
                    sens = _gt("engine/ontologie_science.py:337:28") if centres[k][j] > 0 else "faible"
                    ajouter("segment", f"segment_{k + 1}", f"Segment {k + 1} : {champs[num[j]]['nom']} {sens}",
                            [("segmente_en", "table", "depuis"), ("caracterise", f"champ:{num[j]}", "depuis")],
                            lignes=int((lab == k).sum()), variable_distinctive=num[j], niveau=sens)
    return constats


def analyser_liens(tables: dict) -> list[tuple]:
    """tables : {table_id: {"df": DataFrame, "champs": {...}, "ids": [slug identifiants]}}.
    Retourne [(table_source, slug, table_cible, slug_id, couverture)] : les valeurs de la colonne se
    retrouvent (≥ 90 %) dans l'identifiant candidat d'une autre table → clé étrangère probable."""
    liens = []
    identifiants = {}
    for tid, t in tables.items():
        for slug in t["ids"]:
            identifiants[(tid, slug)] = set(t["df"][slug].dropna().astype(str))
    for tid, t in tables.items():
        for slug in t["champs"]:
            if slug in t["ids"] or slug not in t["df"].columns:
                continue
            vals = set(t["df"][slug].dropna().astype(str))
            if len(vals) < 3:
                continue
            for (tid2, slug2), ref in identifiants.items():
                if tid2 == tid or not ref:
                    continue
                couverture = len(vals & ref) / len(vals)
                if couverture >= 0.9:
                    liens.append((tid, slug, tid2, slug2, round(couverture, 3)))
    return liens


# ── Branchement sur le graphe ─────────────────────────────────────────
def _charger_table(table):
    from . import parquet_store
    df = parquet_store.donnees_analytiques(table)
    return (df if LIGNES_MAX is None else df.tail(LIGNES_MAX)).reset_index(drop=True)   # fichier Parquet, lignes en ordre chronologique


def enrichir_science(g, entreprise) -> None:
    """Ajoute la couche science des données aux nœuds table déjà présents dans `g`. Constats par table et
    clés étrangères entre tables mis en cache tant que les données ne changent pas."""
    import hashlib
    from django.core.cache import cache
    from .metadata import empreinte_donnees
    from .models import TableDynamique

    ids = [int(n.split(":", 1)[1]) for n, a in g.nodes(data=True)
           if a.get("type") == "table" and n.startswith("table:") and n.split(":", 1)[1].isdigit()]
    tables, empreintes = {}, []
    for table in TableDynamique.objects.filter(id__in=ids, entreprise=entreprise).prefetch_related("champs")[:40]:
        champs = {c.slug: {"id": c.id, "nom": c.nom, "type": c.type_champ} for c in table.champs.all()}
        emp = empreinte_donnees(table)
        empreintes.append(f"{table.id}:{emp}")
        cle = f"onto_sci:v1:{table.id}:{emp}"
        resultat = cache.get(cle)
        df = None
        if resultat is None:
            try:
                df = _charger_table(table)
                constats = analyser_table(df, champs)
                resultat = {"constats": constats, "ids": [c["champ"] for c in constats if c["type"] == "identifiant_candidat"]}
                cache.set(cle, resultat, 3600)
            except Exception:
                logger.exception(t("engine/ontologie_science.py:g:405:34"), table.id)
                continue
        tables[table.id] = {"table": table, "df": df, "champs": champs, "ids": resultat["ids"]}
        _poser_constats(g, table, champs, resultat["constats"])

    # Clés étrangères probables entre tables (cache commun à toutes les tables de l'entreprise)
    if len(tables) >= 2 and any(t["ids"] for t in tables.values()):
        cle_fk = "onto_sci_fk:v1:" + hashlib.sha256("|".join(sorted(empreintes)).encode()).hexdigest()
        liens = cache.get(cle_fk)
        if liens is None:
            try:
                for tb in tables.values():
                    if tb["df"] is None:
                        tb["df"] = _charger_table(tb["table"])
                liens = analyser_liens({k: v for k, v in tables.items() if not v["df"].empty})
                cache.set(cle_fk, liens, 3600)
            except Exception:
                logger.exception(t("engine/ontologie_science.py:g:422:34"))
                liens = []
        for tid, slug, tid2, slug2, couv in liens:
            champ = tables[tid]["champs"].get(slug)
            if champ is None or f"table:{tid2}" not in g:
                continue
            nid = f"sci:fk:{tid}:{slug}"
            g.add_node(nid, type="cle_etrangere_candidate", label=f"{champ['nom']} → {tables[tid2]['table'].nom}",
                       couleur=TYPES_NOEUDS_SCIENCE["cle_etrangere_candidate"][0], taille=9, couverture=couv, colonne_cible=slug2)
            src = f"champ:{champ['id']}" if f"champ:{champ['id']}" in g else f"table:{tid}"
            coul = RELATIONS_SCIENCE["reference_probable"][0]
            g.add_edge(src, nid, relation="reference_probable", couleur=coul)
            g.add_edge(nid, f"table:{tid2}", relation="reference_probable", couleur=coul, couverture=couv)

    # Fraîcheur et croissance du volume (requêtes légères, sans DataFrame)
    _fraicheur_et_croissance(g, ids, entreprise)


def _poser_constats(g, table, champs, constats):
    prefixe = f"sci:{table.id}"
    table_nid = f"table:{table.id}"

    def cible(ref):
        if ref == "table":
            return table_nid
        if ref.startswith("champ:"):
            slug = ref[6:]
            nid = f"champ:{champs[slug]['id']}" if slug in champs else table_nid
            return nid if nid in g else table_nid
        if ref.startswith("constat:"):
            return f"{prefixe}:{ref[8:]}"
        return table_nid

    for c in constats:
        if c["type"] != "_arete":
            g.add_node(f"{prefixe}:{c['cle']}", type=c["type"], label=c["label"],
                       couleur=TYPES_NOEUDS_SCIENCE[c["type"]][0], taille=9, table_id=table.id, **c["attrs"])
    for c in constats:
        moi = f"{prefixe}:{c['cle']}"
        for relation, ref, sens in c["liens"]:
            coul = RELATIONS_SCIENCE[relation][0]
            if sens.startswith("depuis_champ:"):  # arête champ → champ (corrélation, association)
                a, b = cible("champ:" + sens[13:]), cible(ref)
                if a != b:
                    g.add_edge(a, b, relation=relation, couleur=coul, **c["attrs"])
            elif sens == "depuis":
                src, dst = cible(ref), moi
                if src in g and dst in g:
                    g.add_edge(src, dst, relation=relation, couleur=coul)
            else:
                dst = cible(ref)
                if moi in g and dst in g:
                    g.add_edge(moi, dst, relation=relation, couleur=coul)


def _fraicheur_et_croissance(g, ids, entreprise):
    from datetime import timedelta
    from django.db.models import Count, Max
    from django.db.models.functions import TruncMonth
    from django.utils import timezone
    from .models import Enregistrement

    # Un groupe de tables = UNE table : fraîcheur et croissance sont calculées sur toutes ses parties et rattachées à l'ancre (1re partie).
    from .models import TableDynamique
    ancre_de, premiers = {}, {}
    for tid, gid, _pi in TableDynamique.objects.filter(id__in=list(ids), groupe__isnull=False).order_by("partie_index", "id").values_list("id", "groupe_id", "partie_index"):
        premiers.setdefault(gid, tid)
        ancre_de[tid] = premiers[gid]
    ancre = lambda tid: ancre_de.get(tid, tid)

    derniers = {}
    for tid, m in Enregistrement.objects.filter(table_id__in=ids, table__entreprise=entreprise).values("table_id").annotate(m=Max("cree_le")).values_list("table_id", "m"):
        a = ancre(tid)
        if m is not None and (derniers.get(a) is None or m > derniers[a]):
            derniers[a] = m
        derniers.setdefault(a, m)
    maintenant = timezone.now()
    depuis = maintenant - timedelta(days=62)
    mois = (Enregistrement.objects.filter(table_id__in=ids, table__entreprise=entreprise, cree_le__gte=depuis)
            .annotate(mo=TruncMonth("cree_le")).values("table_id", "mo").annotate(n=Count("id")))
    par_table = {}
    for m in mois:
        d = par_table.setdefault(ancre(m["table_id"]), {})
        d[m["mo"]] = d.get(m["mo"], 0) + m["n"]
    for tid, dernier in derniers.items():
        if dernier is None or f"table:{tid}" not in g:
            continue
        jours = (maintenant - dernier).days
        nid = f"sci:{tid}:fraicheur"
        g.add_node(nid, type="fraicheur", label=tf("engine/ontologie_science.py:g:510:49", v1=jours) if jours else td("engine/ontologie_science.py:g:511:116"),
                   label_cle=("engine/ontologie_science.py:g:510:49" if jours else "engine/ontologie_science.py:g:511:116"), label_vars=({"v1": jours} if jours else {}),
                   couleur=TYPES_NOEUDS_SCIENCE["fraicheur"][0], taille=8, jours=jours, table_id=tid)
        g.add_edge(nid, f"table:{tid}", relation="fraicheur_de", couleur=RELATIONS_SCIENCE["fraicheur_de"][0])
        if jours > 90:
            rid = f"sci:{tid}:reco_fraicheur"
            g.add_node(rid, type="recommandation", label=t("engine/ontologie_science.py:501:59"), label_cle="engine/ontologie_science.py:501:59", label_vars={},
                       couleur=TYPES_NOEUDS_SCIENCE["recommandation"][0], taille=9,
                       detail=td("engine/ontologie_science.py:g:518:31"))
            g.add_edge(nid, rid, relation="recommande", couleur=RELATIONS_SCIENCE["recommande"][0])
        vals = [v for _k, v in sorted(par_table.get(tid, {}).items())]
        if len(vals) >= 2 and vals[-2]:
            var = 100 * (vals[-1] - vals[-2]) / vals[-2]
            cid = f"sci:{tid}:croissance"
            g.add_node(cid, type="croissance_volume", label=tf("engine/ontologie_science.py:g:523:61", v1=var), label_cle="engine/ontologie_science.py:g:523:61", label_vars={"v1": var},
                       couleur=TYPES_NOEUDS_SCIENCE["croissance_volume"][0], taille=8, variation_pct=round(var, 1))
            g.add_edge(cid, f"table:{tid}", relation="croissance_de", couleur=RELATIONS_SCIENCE["croissance_de"][0])
