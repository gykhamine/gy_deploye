"""100 formules « data science » (2026-09-23) — ajoutées au catalogue de formules_calcul.py.

Même contrat que les formules existantes : un script `def formule(cible):` exécuté dans le bac à sable
(canal formule : pandas, numpy, scipy, statsmodels, math, statistics, decimal). Les formules « colonne »
reçoivent une pandas.Series, les formules « table » un pandas.DataFrame dont elles repèrent elles-mêmes
les colonnes utiles (numériques, dates, catégories) — ordre des colonnes = ordre de la table.

Familles : qualité des données, statistique descriptive robuste, tests d'hypothèse (avec conclusion en
français), tailles d'effet, régression, séries temporelles, segmentation, détection d'anomalies,
finance / commerce, probabilités. Chaque résultat est un nombre, une liste ou un dictionnaire lisible
(« conclusion », « p_valeur », « interpretation »…), jamais un objet interne.

Les préambules communs sont concaténés ici en Python (chaque script reste autonome dans le bac à sable).
"""
from engine.i18n import tl

TYPES_NUMERIQUES = "nombre,decimal,monnaie,pourcentage,barre_progression,note"
TYPES_CATEGORIEL = "choix_unique,couleur,texte"
TYPES_TOUS = ""

# Série numérique propre : x (numpy float64, sans vide), s (pandas), n
_PRE_NUM = (
    "    import math\n"
    "    import numpy as np\n"
    "    import pandas as pd\n"
    "    s = pd.to_numeric(pd.Series(cible), errors='coerce').dropna().astype('float64')\n"
    "    x = s.to_numpy()\n"
    "    n = len(x)\n"
    "    r = lambda v, d=6: None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), d)\n"
)
# Série quelconque : v (pandas, sans vide)
_PRE_ALL = (
    "    import math\n"
    "    import numpy as np\n"
    "    import pandas as pd\n"
    "    brut = pd.Series(cible)\n"
    "    v = brut.dropna().astype(str)\n"
    "    v = v[v.str.strip() != '']\n"
    "    r = lambda a, d=6: None if a is None or (isinstance(a, float) and math.isnan(a)) else round(float(a), d)\n"
)
# Table : df, num (colonnes numériques), dat (dates), cat (catégories), boo (booléens)
_PRE_TAB = (
    "    import math\n"
    "    import numpy as np\n"
    "    import pandas as pd\n"
    "    df = pd.DataFrame(cible).copy()\n"
    "    df = df[[c for c in df.columns if not str(c).startswith('_')]]\n"
    "    boo = [c for c in df.columns if pd.api.types.is_bool_dtype(df[c])]\n"
    "    dat = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]\n"
    "    num = [c for c in df.columns if c not in boo and c not in dat and pd.api.types.is_numeric_dtype(df[c])]\n"
    "    cat = [c for c in df.columns if c not in boo and c not in dat and c not in num\n"
    "           and 1 < df[c].dropna().astype(str).nunique() <= max(2, min(50, len(df) // 2))]\n"
    "    r = lambda a, d=6: None if a is None or (isinstance(a, float) and math.isnan(a)) else round(float(a), d)\n"
)
# Conclusion d'un test au seuil de 5 %
_CONCLURE = (
    tl("engine/formules_science.py:g:56:5")
)


def _fc(slug, nom, cat, desc, corps, types=TYPES_NUMERIQUES, pre=_PRE_NUM):
    return {"slug": slug, "nom": nom, "categorie": cat, "cible_type": "colonne",
            "types_cibles_compatibles": types, "description": desc,
            "code": "def formule(cible):\n" + pre + corps}


def _ft(slug, nom, cat, desc, corps):
    return {"slug": slug, "nom": nom, "categorie": cat, "cible_type": "table",
            "types_cibles_compatibles": TYPES_TOUS, "description": desc,
            "code": "def formule(cible):\n" + _PRE_TAB + _CONCLURE + corps}


FORMULES_SCIENCE = [
    # ═══════════════════ QUALITÉ DES DONNÉES ═══════════════════
    _fc("profil_colonne", tl("engine/formules_science.py:g:77:27"), "qualite",
        tl("engine/formules_science.py:g:78:9"),
        tl("engine/formules_science.py:g:79:9"),
        types=TYPES_TOUS, pre=_PRE_ALL),
    _fc("taux_doublons", tl("engine/formules_science.py:g:87:26"), "qualite",
        tl("engine/formules_science.py:g:88:9"),
        "    return r(100 * v.duplicated().sum() / len(v), 2) if len(v) else None\n", types=TYPES_TOUS, pre=_PRE_ALL),
    _fc("nombre_doublons", tl("engine/formules_science.py:g:90:28"), "qualite",
        tl("engine/formules_science.py:g:91:9"),
        "    return int(v.duplicated().sum())\n", types=TYPES_TOUS, pre=_PRE_ALL),
    _fc("dominance_modale", tl("engine/formules_science.py:g:93:29"), "qualite",
        tl("engine/formules_science.py:g:94:9"),
        "    return r(100 * v.value_counts().iloc[0] / len(v), 2) if len(v) else None\n", types=TYPES_TOUS, pre=_PRE_ALL),
    _fc("entropie_shannon", tl("engine/formules_science.py:g:96:29"), "qualite",
        tl("engine/formules_science.py:g:97:9"),
        "    p = v.value_counts(normalize=True).to_numpy()\n"
        "    return r(-(p * np.log2(p)).sum(), 4) if len(p) else None\n", types=TYPES_TOUS, pre=_PRE_ALL),
    _fc("entropie_normalisee", tl("engine/formules_science.py:g:100:32"), "qualite",
        tl("engine/formules_science.py:g:101:9"),
        "    p = v.value_counts(normalize=True).to_numpy()\n"
        "    if len(p) < 2:\n"
        "        return 0.0 if len(p) else None\n"
        "    return r(-(p * np.log2(p)).sum() / math.log2(len(p)), 4)\n", types=TYPES_TOUS, pre=_PRE_ALL),
    _fc("cardinalite_relative", tl("engine/formules_science.py:g:106:33"), "qualite",
        tl("engine/formules_science.py:g:107:9"),
        "    return r(v.nunique() / len(v), 4) if len(v) else None\n", types=TYPES_TOUS, pre=_PRE_ALL),
    _fc("taux_valeurs_aberrantes_iqr", tl("engine/formules_science.py:g:109:40"), "qualite",
        tl("engine/formules_science.py:g:110:9"),
        "    if n < 4:\n"
        "        return None\n"
        "    q1, q3 = np.percentile(x, [25, 75]); e = q3 - q1\n"
        "    return r(100 * ((x < q1 - 1.5 * e) | (x > q3 + 1.5 * e)).mean(), 2)\n"),
    _fc("taux_valeurs_invalides", tl("engine/formules_science.py:g:115:35"), "qualite",
        tl("engine/formules_science.py:g:116:9"),
        "    conv = pd.to_numeric(v, errors='coerce')\n"
        "    return r(100 * conv.isna().mean(), 2) if len(v) else None\n", types=TYPES_TOUS, pre=_PRE_ALL),
    _ft("score_qualite_table", tl("engine/formules_science.py:g:119:32"), "qualite",
        tl("engine/formules_science.py:g:120:9"),
        tl("engine/formules_science.py:g:121:9")),
    _ft("lignes_dupliquees", tl("engine/formules_science.py:g:131:30"), "qualite",
        tl("engine/formules_science.py:g:132:9"),
        "    return int(df.astype(str).duplicated().sum())\n"),
    _ft("colonnes_peu_remplies", tl("engine/formules_science.py:g:134:34"), "qualite",
        tl("engine/formules_science.py:g:135:9"),
        "    t = df.notna().mean() * 100\n"
        "    return {str(c): r(t[c], 1) for c in df.columns if t[c] < 50}\n"),

    # ═══════════════════ STATISTIQUE DESCRIPTIVE ROBUSTE ═══════════════════
    _fc("moyenne_tronquee_10", tl("engine/formules_science.py:g:140:32"), "statistique",
        tl("engine/formules_science.py:g:141:9"),
        "    from scipy import stats\n"
        "    return r(stats.trim_mean(x, 0.1)) if n else None\n"),
    _fc("moyenne_winsorisee_5", tl("engine/formules_science.py:g:144:33"), "statistique",
        tl("engine/formules_science.py:g:145:9"),
        "    if not n:\n"
        "        return None\n"
        "    lo, hi = np.percentile(x, [5, 95])\n"
        "    return r(np.clip(x, lo, hi).mean())\n"),
    _fc("ecart_absolu_median", tl("engine/formules_science.py:g:150:32"), "statistique",
        tl("engine/formules_science.py:g:151:9"),
        "    return r(np.median(np.abs(x - np.median(x)))) if n else None\n"),
    _fc("ecart_type_robuste", tl("engine/formules_science.py:g:153:31"), "statistique",
        tl("engine/formules_science.py:g:154:9"),
        "    return r(1.4826 * np.median(np.abs(x - np.median(x)))) if n else None\n"),
    _fc("ecart_moyen_absolu", tl("engine/formules_science.py:g:156:31"), "statistique",
        tl("engine/formules_science.py:g:157:9"),
        "    return r(np.abs(x - x.mean()).mean()) if n else None\n"),
    _fc("erreur_standard_moyenne", tl("engine/formules_science.py:g:159:36"), "statistique",
        tl("engine/formules_science.py:g:160:9"),
        "    return r(x.std(ddof=1) / math.sqrt(n)) if n > 1 else None\n"),
    _fc("percentile_5", "5e centile", "statistique", tl("engine/formules_science.py:g:162:54"),
        "    return r(np.percentile(x, 5)) if n else None\n"),
    _fc("percentile_95", "95e centile", "statistique", tl("engine/formules_science.py:g:164:56"),
        "    return r(np.percentile(x, 95)) if n else None\n"),
    _fc("rapport_interdecile", tl("engine/formules_science.py:g:166:32"), "statistique",
        tl("engine/formules_science.py:g:167:9"),
        "    if not n:\n"
        "        return None\n"
        "    d1, d9 = np.percentile(x, [10, 90])\n"
        "    return r(d9 / d1, 3) if d1 else None\n"),
    _fc("indice_gini", tl("engine/formules_science.py:g:172:24"), "statistique",
        tl("engine/formules_science.py:g:173:9"),
        "    y = np.sort(x[x >= 0])\n"
        "    if len(y) < 2 or y.sum() == 0:\n"
        "        return None\n"
        "    k = np.arange(1, len(y) + 1)\n"
        "    return r((2 * (k * y).sum()) / (len(y) * y.sum()) - (len(y) + 1) / len(y), 4)\n"),
    _fc("part_top_20_pourcent", tl("engine/formules_science.py:g:179:33"), "statistique",
        tl("engine/formules_science.py:g:180:9"),
        "    y = np.sort(x)[::-1]\n"
        "    if not n or y.sum() == 0:\n"
        "        return None\n"
        "    k = max(1, int(math.ceil(0.2 * n)))\n"
        "    return r(100 * y[:k].sum() / y.sum(), 2)\n"),
    _fc("indice_herfindahl", tl("engine/formules_science.py:g:186:30"), "statistique",
        tl("engine/formules_science.py:g:187:9"),
        "    y = x[x > 0]\n"
        "    return r(((y / y.sum()) ** 2).sum(), 4) if len(y) else None\n"),
    _fc("nombre_valeurs_extremes_z", tl("engine/formules_science.py:g:190:38"), "statistique",
        tl("engine/formules_science.py:g:191:9"),
        "    if n < 3 or x.std() == 0:\n"
        "        return 0\n"
        "    return int((np.abs((x - x.mean()) / x.std(ddof=1)) > 3).sum())\n"),
    _fc("score_z_maximal", "Score z maximal", "statistique",
        tl("engine/formules_science.py:g:196:9"),
        "    if n < 2 or x.std() == 0:\n"
        "        return None\n"
        "    return r(np.abs((x - x.mean()) / x.std(ddof=1)).max(), 3)\n"),
    _fc("intervalle_confiance_moyenne_99", tl("engine/formules_science.py:g:200:44"), "statistique",
        tl("engine/formules_science.py:g:201:9"),
        "    from scipy import stats\n"
        "    if n < 2:\n"
        "        return None\n"
        "    m, e = x.mean(), stats.sem(x)\n"
        "    lo, hi = stats.t.interval(0.99, n - 1, loc=m, scale=e)\n"
        "    return {'moyenne': r(m), 'bas': r(lo), 'haut': r(hi)}\n"),
    _fc("intervalle_confiance_mediane_bootstrap", tl("engine/formules_science.py:g:208:51"), "statistique",
        tl("engine/formules_science.py:g:209:9"),
        "    if n < 5:\n"
        "        return None\n"
        "    rng = np.random.default_rng(42)\n"
        "    meds = np.median(rng.choice(x, size=(2000, n), replace=True), axis=1)\n"
        "    lo, hi = np.percentile(meds, [2.5, 97.5])\n"
        "    return {'mediane': r(np.median(x)), 'bas': r(lo), 'haut': r(hi)}\n"),

    # ═══════════════════ TESTS D'HYPOTHÈSE — une colonne ═══════════════════
    _fc("test_jarque_bera", tl("engine/formules_science.py:g:218:29"), "test",
        tl("engine/formules_science.py:g:219:9"),
        _CONCLURE +
        tl("engine/formules_science.py:g:221:9")),
    _fc("test_kolmogorov_smirnov_normale", tl("engine/formules_science.py:g:227:44"), "test",
        tl("engine/formules_science.py:g:228:9"),
        _CONCLURE +
        tl("engine/formules_science.py:g:230:9")),
    _fc("test_moyenne_nulle", tl("engine/formules_science.py:g:236:31"), "test",
        tl("engine/formules_science.py:g:237:9"),
        _CONCLURE +
        tl("engine/formules_science.py:g:239:9")),
    _fc("test_mediane_nulle_wilcoxon", tl("engine/formules_science.py:g:245:40"), "test",
        tl("engine/formules_science.py:g:246:9"),
        _CONCLURE +
        tl("engine/formules_science.py:g:248:9")),
    _fc("test_aleatoire_runs", tl("engine/formules_science.py:g:255:32"), "test",
        tl("engine/formules_science.py:g:256:9"),
        _CONCLURE +
        tl("engine/formules_science.py:g:258:9")),
    _fc("taille_echantillon_requise", tl("engine/formules_science.py:g:272:39"), "test",
        tl("engine/formules_science.py:g:273:9"),
        "    if n < 2 or x.mean() == 0:\n"
        "        return None\n"
        "    marge = 0.05 * abs(x.mean())\n"
        "    besoin = int(math.ceil((1.96 * x.std(ddof=1) / marge) ** 2))\n"
        "    return {'necessaire': besoin, 'disponible': n, 'suffisant': n >= besoin}\n"),

    # ═══════════════════ TESTS D'HYPOTHÈSE — plusieurs colonnes ═══════════════════
    _ft("test_student_deux_groupes", tl("engine/formules_science.py:g:281:38"), "test",
        tl("engine/formules_science.py:g:282:9"),
        tl("engine/formules_science.py:g:283:9")),
    _ft("test_mann_whitney", tl("engine/formules_science.py:g:295:30"), "test",
        tl("engine/formules_science.py:g:296:9"),
        tl("engine/formules_science.py:g:297:9")),
    _ft("test_kruskal_wallis", tl("engine/formules_science.py:g:308:32"), "test",
        tl("engine/formules_science.py:g:309:9"),
        tl("engine/formules_science.py:g:310:9")),
    _ft("test_levene_variances", tl("engine/formules_science.py:g:321:34"), "test",
        tl("engine/formules_science.py:g:322:9"),
        tl("engine/formules_science.py:g:323:9")),
    _ft("test_chi2_independance", tl("engine/formules_science.py:g:334:35"), "test",
        tl("engine/formules_science.py:g:335:9"),
        tl("engine/formules_science.py:g:336:9")),
    _ft("v_de_cramer", tl("engine/formules_science.py:g:344:24"), "effet",
        tl("engine/formules_science.py:g:345:9"),
        "    from scipy import stats\n"
        "    if len(cat) < 2:\n"
        "        return None\n"
        "    tab = pd.crosstab(df[cat[0]].astype(str), df[cat[1]].astype(str))\n"
        "    chi2 = stats.chi2_contingency(tab, correction=False)[0]\n"
        "    k = min(tab.shape) - 1\n"
        "    return r(math.sqrt(chi2 / (tab.to_numpy().sum() * k)), 4) if k > 0 else None\n"),
    _ft("test_fisher_exact", tl("engine/formules_science.py:g:353:30"), "test",
        tl("engine/formules_science.py:g:354:9"),
        tl("engine/formules_science.py:g:355:9")),
    _ft("test_student_apparie", tl("engine/formules_science.py:g:363:33"), "test",
        tl("engine/formules_science.py:g:364:9"),
        tl("engine/formules_science.py:g:365:9")),
    _ft("test_wilcoxon_apparie", tl("engine/formules_science.py:g:372:34"), "test",
        tl("engine/formules_science.py:g:373:9"),
        tl("engine/formules_science.py:g:374:9")),
    _ft("correlation_kendall", tl("engine/formules_science.py:g:384:32"), "multivarie",
        tl("engine/formules_science.py:g:385:9"),
        tl("engine/formules_science.py:g:386:9")),
    _ft("correlation_avec_intervalle", tl("engine/formules_science.py:g:393:40"), "multivarie",
        tl("engine/formules_science.py:g:394:9"),
        tl("engine/formules_science.py:g:395:9")),
    _ft("correlation_partielle", tl("engine/formules_science.py:g:406:34"), "multivarie",
        tl("engine/formules_science.py:g:407:9"),
        "    if len(num) < 3:\n"
        "        return None\n"
        "    c = df[num[:3]].dropna().corr().to_numpy()\n"
        "    rxy, rxz, ryz = c[0, 1], c[0, 2], c[1, 2]\n"
        "    den = math.sqrt((1 - rxz ** 2) * (1 - ryz ** 2))\n"
        "    return {'correlation_brute': r(rxy, 4), 'correlation_partielle': r((rxy - rxz * ryz) / den, 4) if den else None,\n"
        "            'variable_controlee': str(num[2])}\n"),
    _ft("d_de_cohen", tl("engine/formules_science.py:g:415:23"), "effet",
        tl("engine/formules_science.py:g:416:9"),
        tl("engine/formules_science.py:g:417:9")),
    _ft("eta_carre", tl("engine/formules_science.py:g:427:22"), "effet",
        tl("engine/formules_science.py:g:428:9"),
        "    if not cat or not num:\n"
        "        return None\n"
        "    d = df[[cat[0], num[0]]].dropna()\n"
        "    y = d[num[0]]; m = y.mean()\n"
        "    sct = ((y - m) ** 2).sum()\n"
        "    sce = sum(len(v) * (v.mean() - m) ** 2 for _, v in y.groupby(d[cat[0]].astype(str)))\n"
        "    return r(sce / sct, 4) if sct else None\n"),

    # ═══════════════════ RÉGRESSION ET MODÉLISATION ═══════════════════
    _ft("regression_lineaire_multiple", tl("engine/formules_science.py:g:438:41"), "regression",
        tl("engine/formules_science.py:g:439:9"),
        tl("engine/formules_science.py:g:440:9")),
    _ft("regression_simple_r2", tl("engine/formules_science.py:g:452:33"), "regression",
        tl("engine/formules_science.py:g:453:9"),
        "    from scipy import stats\n"
        "    if len(num) < 2:\n"
        "        return None\n"
        "    d = df[num[:2]].dropna()\n"
        "    res = stats.linregress(d[num[0]], d[num[1]])\n"
        "    return {'pente': r(res.slope), 'ordonnee': r(res.intercept), 'r2': r(res.rvalue ** 2, 4), 'p_valeur': r(res.pvalue, 6)}\n"),
    _ft("regression_polynomiale_2", tl("engine/formules_science.py:g:460:37"), "regression",
        tl("engine/formules_science.py:g:461:9"),
        "    if len(num) < 2:\n"
        "        return None\n"
        "    d = df[num[:2]].dropna()\n"
        "    if len(d) < 4:\n"
        "        return None\n"
        "    xs, ys = d[num[0]].to_numpy('float64'), d[num[1]].to_numpy('float64')\n"
        "    a, b, c = np.polyfit(xs, ys, 2)\n"
        "    pred = a * xs ** 2 + b * xs + c\n"
        "    r2 = 1 - ((ys - pred) ** 2).sum() / ((ys - ys.mean()) ** 2).sum()\n"
        "    sommet = -b / (2 * a) if a else None\n"
        "    return {'a': r(a), 'b': r(b), 'c': r(c), 'r2': r(r2, 4), 'x_optimum': r(sommet)}\n"),
    _ft("elasticite", tl("engine/formules_science.py:g:473:23"), "regression",
        tl("engine/formules_science.py:g:474:9"),
        tl("engine/formules_science.py:g:475:9")),
    _ft("facteurs_inflation_variance", tl("engine/formules_science.py:g:485:40"), "regression",
        tl("engine/formules_science.py:g:486:9"),
        "    from statsmodels.stats.outliers_influence import variance_inflation_factor\n"
        "    import statsmodels.api as sm\n"
        "    if len(num) < 2:\n"
        "        return None\n"
        "    X = sm.add_constant(df[num].dropna(), has_constant='add')\n"
        "    return {str(c): r(variance_inflation_factor(X.to_numpy('float64'), i), 3) for i, c in enumerate(X.columns) if c != 'const'}\n"),
    _ft("regression_logistique", tl("engine/formules_science.py:g:493:34"), "regression",
        tl("engine/formules_science.py:g:494:9"),
        tl("engine/formules_science.py:g:495:9")),
    _ft("importance_variables", tl("engine/formules_science.py:g:507:33"), "regression",
        tl("engine/formules_science.py:g:508:9"),
        "    if len(num) < 2:\n"
        "        return None\n"
        "    c = df[num].corr()[num[-1]].drop(num[-1]).abs().sort_values(ascending=False)\n"
        "    return {str(k): r(v, 4) for k, v in c.items()}\n"),
    _ft("matrice_correlation", tl("engine/formules_science.py:g:513:32"), "multivarie",
        tl("engine/formules_science.py:g:514:9"),
        "    if len(num) < 2:\n"
        "        return None\n"
        "    m = df[num].corr()\n"
        "    return {str(a): {str(b): r(m.loc[a, b], 4) for b in num} for a in num}\n"),
    _ft("paires_fortement_correlees", tl("engine/formules_science.py:g:519:39"), "multivarie",
        tl("engine/formules_science.py:g:520:9"),
        "    if len(num) < 2:\n"
        "        return []\n"
        "    m = df[num].corr()\n"
        "    out = []\n"
        "    for i, a in enumerate(num):\n"
        "        for b in num[i + 1:]:\n"
        "            if abs(m.loc[a, b]) >= 0.7:\n"
        "                out.append({'colonnes': [str(a), str(b)], 'r': r(m.loc[a, b], 4)})\n"
        "    return sorted(out, key=lambda e: -abs(e['r']))\n"),

    # ═══════════════════ SÉRIES TEMPORELLES ═══════════════════
    _fc("taux_croissance_annuel_moyen", tl("engine/formules_science.py:g:532:41"), "temporel",
        tl("engine/formules_science.py:g:533:9"),
        "    if n < 2 or x[0] <= 0 or x[-1] <= 0:\n"
        "        return None\n"
        "    return r(100 * ((x[-1] / x[0]) ** (1 / (n - 1)) - 1), 3)\n"),
    _fc("variation_derniere_periode", tl("engine/formules_science.py:g:537:39"), "temporel",
        tl("engine/formules_science.py:g:538:9"),
        "    return r(100 * (x[-1] - x[-2]) / abs(x[-2]), 2) if n >= 2 and x[-2] else None\n"),
    _fc("autocorrelation_lag1", tl("engine/formules_science.py:g:540:33"), "temporel",
        tl("engine/formules_science.py:g:541:9"),
        "    return r(np.corrcoef(x[:-1], x[1:])[0, 1], 4) if n > 3 and x.std() else None\n"),
    _fc("autocorrelation_saisonniere", tl("engine/formules_science.py:g:543:40"), "temporel",
        tl("engine/formules_science.py:g:544:9"),
        "    if n < 12 or not x.std():\n"
        "        return None\n"
        "    ac = {}\n"
        "    for k in range(2, min(25, n // 2)):\n"
        "        ac[k] = np.corrcoef(x[:-k], x[k:])[0, 1]\n"
        "    k = max(ac, key=lambda z: ac[z])\n"
        "    return {'periode': int(k), 'autocorrelation': r(ac[k], 4), 'saisonnalite_nette': bool(ac[k] > 0.5)}\n"),
    _fc("prevision_lissage_exponentiel", tl("engine/formules_science.py:g:552:42"), "temporel",
        tl("engine/formules_science.py:g:553:9"),
        "    from statsmodels.tsa.holtwinters import ExponentialSmoothing\n"
        "    if n < 6:\n"
        "        return None\n"
        "    m = ExponentialSmoothing(x, trend='add').fit()\n"
        "    return [r(v, 4) for v in m.forecast(3)]\n"),
    _fc("moyenne_mobile_exponentielle", tl("engine/formules_science.py:g:559:41"), "temporel",
        tl("engine/formules_science.py:g:560:9"),
        "    return r(s.ewm(span=5, adjust=False).mean().iloc[-1]) if n else None\n"),
    _fc("volatilite", tl("engine/formules_science.py:g:562:23"), "temporel",
        tl("engine/formules_science.py:g:563:9"),
        "    ch = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()\n"
        "    return r(100 * ch.std(), 3) if len(ch) > 1 else None\n"),
    _fc("baisse_maximale", tl("engine/formules_science.py:g:566:28"), "temporel",
        tl("engine/formules_science.py:g:567:9"),
        "    if n < 2:\n"
        "        return None\n"
        "    pic = np.maximum.accumulate(x)\n"
        "    dd = np.where(pic != 0, (x - pic) / np.abs(pic), 0)\n"
        "    return r(100 * dd.min(), 2)\n"),
    _fc("tendance_mann_kendall", tl("engine/formules_science.py:g:573:34"), "temporel",
        tl("engine/formules_science.py:g:574:9"),
        _CONCLURE +
        tl("engine/formules_science.py:g:576:9")),
    _fc("point_de_rupture", tl("engine/formules_science.py:g:583:29"), "temporel",
        tl("engine/formules_science.py:g:584:9"),
        "    if n < 6:\n"
        "        return None\n"
        "    cs = np.cumsum(x - x.mean())\n"
        "    k = int(np.argmax(np.abs(cs[:-1]))) + 1\n"
        "    return {'ligne_rupture': k + 1, 'moyenne_avant': r(x[:k].mean()), 'moyenne_apres': r(x[k:].mean())}\n"),
    _fc("pente_tendance_theil_sen", tl("engine/formules_science.py:g:590:37"), "temporel",
        tl("engine/formules_science.py:g:591:9"),
        tl("engine/formules_science.py:g:592:9")),
    _ft("total_par_mois", tl("engine/formules_science.py:g:597:27"), "temporel",
        tl("engine/formules_science.py:g:598:9"),
        tl("engine/formules_science.py:g:599:9")),
    _ft("variation_sur_12_mois", tl("engine/formules_science.py:g:604:34"), "temporel",
        tl("engine/formules_science.py:g:605:9"),
        "    if not dat or not num:\n"
        "        return None\n"
        "    d = df[[dat[0], num[0]]].dropna()\n"
        "    fin = d[dat[0]].max()\n"
        "    a = d[d[dat[0]] > fin - pd.DateOffset(months=12)][num[0]].sum()\n"
        "    b = d[(d[dat[0]] <= fin - pd.DateOffset(months=12)) & (d[dat[0]] > fin - pd.DateOffset(months=24))][num[0]].sum()\n"
        "    return {'12_derniers_mois': r(a, 2), '12_mois_precedents': r(b, 2), 'variation_pct': r(100 * (a - b) / abs(b), 2) if b else None}\n"),
    _ft("activite_par_jour_semaine", tl("engine/formules_science.py:g:613:38"), "temporel",
        tl("engine/formules_science.py:g:614:9"),
        "    if not dat or not num:\n"
        "        return None\n"
        "    jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']\n"
        "    d = df[[dat[0], num[0]]].dropna()\n"
        "    t = d.groupby(d[dat[0]].dt.dayofweek)[num[0]].sum()\n"
        "    return {jours[int(k)]: r(v, 2) for k, v in t.items()}\n"),
    _ft("force_tendance_saisonnalite", tl("engine/formules_science.py:g:621:40"), "temporel",
        tl("engine/formules_science.py:g:622:9"),
        tl("engine/formules_science.py:g:623:9")),

    # ═══════════════════ SEGMENTATION ET ANOMALIES ═══════════════════
    _ft("segments_kmeans", "Segmentation automatique (k-means, 3 groupes)", "segmentation",
        tl("engine/formules_science.py:g:639:9"),
        "    from scipy.cluster.vq import kmeans2\n"
        "    if not num:\n"
        "        return None\n"
        "    d = df[num].dropna().astype(float)\n"
        "    if len(d) < 6:\n"
        "        return None\n"
        "    z = ((d - d.mean()) / d.std(ddof=0).replace(0, 1)).to_numpy()\n"
        "    _c, lab = kmeans2(z, 3, seed=42, minit='++')\n"
        "    out = {}\n"
        "    for k in range(3):\n"
        "        part = d[lab == k]\n"
        "        out['groupe_' + str(k + 1)] = {'lignes': int(len(part)), 'profil_moyen': {str(c): r(part[c].mean(), 3) for c in num}}\n"
        "    return out\n"),
    _ft("classification_hierarchique", tl("engine/formules_science.py:g:653:40"), "segmentation",
        tl("engine/formules_science.py:g:654:9"),
        "    from scipy.cluster.hierarchy import linkage, fcluster\n"
        "    if not num:\n"
        "        return None\n"
        "    d = df[num].dropna().astype(float).head(2000)\n"
        "    if len(d) < 6:\n"
        "        return None\n"
        "    z = ((d - d.mean()) / d.std(ddof=0).replace(0, 1)).to_numpy()\n"
        "    lab = fcluster(linkage(z, 'ward'), 3, criterion='maxclust')\n"
        "    return {'groupe_' + str(k): {'lignes': int((lab == k).sum()), 'profil_moyen': {str(c): r(d[lab == k][c].mean(), 3) for c in num}}\n"
        "            for k in sorted(set(lab.tolist()))}\n"),
    _ft("segmentation_rfm", tl("engine/formules_science.py:g:665:29"), "segmentation",
        tl("engine/formules_science.py:g:666:9"),
        tl("engine/formules_science.py:g:667:9")),
    _ft("analyse_abc", tl("engine/formules_science.py:g:686:24"), "segmentation",
        tl("engine/formules_science.py:g:687:9"),
        tl("engine/formules_science.py:g:688:9")),
    _fc("anomalies_mad", tl("engine/formules_science.py:g:698:26"), "anomalie",
        tl("engine/formules_science.py:g:699:9"),
        "    brut_num = pd.to_numeric(pd.Series(cible), errors='coerce').reset_index(drop=True)\n"
        "    if n < 5:\n"
        "        return []\n"
        "    med = np.median(x); mad = np.median(np.abs(x - med))\n"
        "    if mad == 0:\n"
        "        return []\n"
        "    z = 0.6745 * (brut_num - med) / mad\n"
        "    return [int(i) + 1 for i in z.index[z.abs() > 3.5]][:200]\n"),
    _fc("anomalies_iqr", tl("engine/formules_science.py:g:708:26"), "anomalie",
        tl("engine/formules_science.py:g:709:9"),
        "    brut_num = pd.to_numeric(pd.Series(cible), errors='coerce').reset_index(drop=True)\n"
        "    if n < 4:\n"
        "        return []\n"
        "    q1, q3 = np.percentile(x, [25, 75]); e = q3 - q1\n"
        "    masque = (brut_num < q1 - 1.5 * e) | (brut_num > q3 + 1.5 * e)\n"
        "    return [int(i) + 1 for i in masque.index[masque]][:200]\n"),
    _ft("anomalies_multivariees", tl("engine/formules_science.py:g:716:35"), "anomalie",
        tl("engine/formules_science.py:g:717:9"),
        "    from scipy import stats\n"
        "    if len(num) < 2:\n"
        "        return None\n"
        "    d = df[num].dropna().astype(float)\n"
        "    if len(d) <= len(num) + 2:\n"
        "        return None\n"
        "    xc = d.to_numpy() - d.to_numpy().mean(axis=0)\n"
        "    inv = np.linalg.pinv(np.cov(xc, rowvar=False))\n"
        "    m2 = np.einsum('ij,jk,ik->i', xc, inv, xc)\n"
        "    seuil = stats.chi2.ppf(0.99, len(num))\n"
        "    lignes = [int(df.index.get_loc(i)) + 1 for i in d.index[m2 > seuil]]\n"
        "    return {'nombre': len(lignes), 'lignes': lignes[:200], 'seuil_distance2': r(seuil, 3)}\n"),
    _fc("courbe_de_lorenz", tl("engine/formules_science.py:g:730:29"), "segmentation",
        tl("engine/formules_science.py:g:731:9"),
        "    y = np.sort(x[x >= 0])\n"
        "    if not len(y) or y.sum() == 0:\n"
        "        return None\n"
        "    c = np.concatenate([[0], np.cumsum(y) / y.sum()])\n"
        "    idx = np.linspace(0, len(y), 11).astype(int)\n"
        "    return [{'part_lignes_pct': int(10 * k), 'part_total_pct': r(100 * c[i], 2)} for k, i in enumerate(idx)]\n"),
    _ft("concentration_par_categorie", tl("engine/formules_science.py:g:738:40"), "segmentation",
        tl("engine/formules_science.py:g:739:9"),
        tl("engine/formules_science.py:g:740:9")),
    _ft("profil_par_categorie", tl("engine/formules_science.py:g:750:33"), "segmentation",
        tl("engine/formules_science.py:g:751:9"),
        "    if not cat or not num:\n"
        "        return None\n"
        "    g = df.groupby(df[cat[0]].astype(str))[num[0]]\n"
        "    return {str(k): {'n': int(v.count()), 'moyenne': r(v.mean(), 3), 'mediane': r(v.median(), 3), 'ecart_type': r(v.std(), 3)} for k, v in g}\n"),
    _ft("tableau_croise", tl("engine/formules_science.py:g:756:27"), "segmentation",
        tl("engine/formules_science.py:g:757:9"),
        "    if len(cat) < 2:\n"
        "        return None\n"
        "    t = pd.crosstab(df[cat[0]].astype(str), df[cat[1]].astype(str))\n"
        "    return {str(a): {str(b): int(t.loc[a, b]) for b in t.columns} for a in t.index}\n"),

    # ═══════════════════ FINANCE ET COMMERCE ═══════════════════
    _fc("valeur_actuelle_nette_10", tl("engine/formules_science.py:g:764:37"), "finance",
        tl("engine/formules_science.py:g:765:9"),
        "    return r((x / (1.10 ** np.arange(n))).sum(), 2) if n else None\n"),
    _fc("taux_rendement_interne", tl("engine/formules_science.py:g:767:35"), "finance",
        tl("engine/formules_science.py:g:768:9"),
        "    if n < 2 or not (x < 0).any() or not (x > 0).any():\n"
        "        return None\n"
        "    van = lambda t: (x / (1 + t) ** np.arange(n)).sum()\n"
        "    lo, hi = -0.99, 10.0\n"
        "    if van(lo) * van(hi) > 0:\n"
        "        return None\n"
        "    for _i in range(200):\n"
        "        mid = (lo + hi) / 2\n"
        "        if van(lo) * van(mid) <= 0:\n"
        "            hi = mid\n"
        "        else:\n"
        "            lo = mid\n"
        "    return r(100 * (lo + hi) / 2, 3)\n"),
    _fc("delai_recuperation", tl("engine/formules_science.py:g:782:31"), "finance",
        tl("engine/formules_science.py:g:783:9"),
        "    c = np.cumsum(x)\n"
        "    pos = np.where(c >= 0)[0]\n"
        "    return int(pos[0]) if len(pos) and n and x[0] < 0 else None\n"),
    _fc("rendement_moyen", tl("engine/formules_science.py:g:787:28"), "finance",
        tl("engine/formules_science.py:g:788:9"),
        "    ch = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()\n"
        "    return r(100 * ch.mean(), 4) if len(ch) else None\n"),
    _fc("ratio_de_sharpe", tl("engine/formules_science.py:g:791:28"), "finance",
        tl("engine/formules_science.py:g:792:9"),
        "    ch = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()\n"
        "    return r(ch.mean() / ch.std(), 4) if len(ch) > 1 and ch.std() else None\n"),
    _fc("valeur_a_risque_95", tl("engine/formules_science.py:g:795:31"), "finance",
        tl("engine/formules_science.py:g:796:9"),
        "    ch = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna()\n"
        "    return r(-100 * np.percentile(ch, 5), 3) if len(ch) >= 20 else None\n"),
    _ft("marge_moyenne", tl("engine/formules_science.py:g:799:26"), "finance",
        tl("engine/formules_science.py:g:800:9"),
        "    if len(num) < 2:\n"
        "        return None\n"
        "    d = df[num[:2]].dropna()\n"
        "    ca, cout = d[num[0]].sum(), d[num[1]].sum()\n"
        "    return {'marge_totale': r(ca - cout, 2), 'taux_marge_pct': r(100 * (ca - cout) / ca, 2) if ca else None,\n"
        "            'taux_marque_pct': r(100 * (ca - cout) / cout, 2) if cout else None}\n"),
    _ft("panier_moyen_par_client", tl("engine/formules_science.py:g:807:36"), "finance",
        tl("engine/formules_science.py:g:808:9"),
        "    if not cat or not num:\n"
        "        return None\n"
        "    t = df.groupby(df[cat[0]].astype(str))[num[0]].sum().sort_values(ascending=False)\n"
        "    if t.empty or t.sum() == 0:\n"
        "        return None\n"
        "    k = max(1, int(math.ceil(0.1 * len(t))))\n"
        "    return {'clients': int(len(t)), 'moyenne': r(t.mean(), 2), 'mediane': r(t.median(), 2),\n"
        "            'part_top_10_pct': r(100 * t.iloc[:k].sum() / t.sum(), 2)}\n"),
    _ft("frequence_et_delai_achat", tl("engine/formules_science.py:g:817:37"), "finance",
        tl("engine/formules_science.py:g:818:9"),
        tl("engine/formules_science.py:g:819:9")),
    _ft("taux_retention_mensuel", tl("engine/formules_science.py:g:826:35"), "finance",
        tl("engine/formules_science.py:g:827:9"),
        "    if not cat or not dat:\n"
        "        return None\n"
        "    d = df[[cat[0], dat[0]]].dropna()\n"
        "    d['mois'] = d[dat[0]].dt.to_period('M')\n"
        "    ens = d.groupby('mois')[cat[0]].apply(lambda s: set(s.astype(str)))\n"
        "    taux = [len(ens.iloc[i] & ens.iloc[i + 1]) / len(ens.iloc[i]) for i in range(len(ens) - 1) if len(ens.iloc[i])]\n"
        "    return {'retention_moyenne_pct': r(100 * np.mean(taux), 2) if taux else None, 'attrition_moyenne_pct': r(100 * (1 - np.mean(taux)), 2) if taux else None,\n"
        "            'mois_observes': int(len(ens))}\n"),
    _ft("valeur_vie_client", tl("engine/formules_science.py:g:836:30"), "finance",
        tl("engine/formules_science.py:g:837:9"),
        tl("engine/formules_science.py:g:838:9")),

    # ═══════════════════ PROBABILITÉS ET DISTRIBUTIONS ═══════════════════
    _fc("loi_la_mieux_ajustee", tl("engine/formules_science.py:g:854:33"), "probabilite",
        tl("engine/formules_science.py:g:855:9"),
        "    from scipy import stats\n"
        "    if n < 8:\n"
        "        return None\n"
        "    lois = {'normale': stats.norm}\n"
        "    if (x > 0).all():\n"
        "        lois.update({'log-normale': stats.lognorm, 'exponentielle': stats.expon, 'gamma': stats.gamma, 'weibull': stats.weibull_min})\n"
        "    res = {}\n"
        "    for nom, loi in lois.items():\n"
        "        p = loi.fit(x) if nom == 'normale' else loi.fit(x, floc=0)\n"
        "        ll = loi.logpdf(x, *p).sum()\n"
        "        if np.isfinite(ll):\n"
        "            res[nom] = {'aic': r(2 * len(p) - 2 * ll, 2), 'parametres': [r(v, 5) for v in p]}\n"
        "    meilleure = min(res, key=lambda k: res[k]['aic'])\n"
        "    return {'meilleure_loi': meilleure, 'comparaison': res}\n"),
    _fc("probabilite_depasser_moyenne_plus_2et", tl("engine/formules_science.py:g:870:50"), "probabilite",
        tl("engine/formules_science.py:g:871:9"),
        "    if n < 3:\n"
        "        return None\n"
        "    seuil = x.mean() + 2 * x.std(ddof=1)\n"
        "    return {'observe_pct': r(100 * (x > seuil).mean(), 2), 'loi_normale_pct': 2.28, 'seuil': r(seuil)}\n"),
    _fc("intervalle_prediction_95", tl("engine/formules_science.py:g:876:37"), "probabilite",
        tl("engine/formules_science.py:g:877:9"),
        "    from scipy import stats\n"
        "    if n < 3:\n"
        "        return None\n"
        "    e = stats.t.ppf(0.975, n - 1) * x.std(ddof=1) * math.sqrt(1 + 1 / n)\n"
        "    return {'bas': r(x.mean() - e), 'haut': r(x.mean() + e)}\n"),
    _fc("intervalle_tolerance_empirique", tl("engine/formules_science.py:g:883:43"), "probabilite",
        tl("engine/formules_science.py:g:884:9"),
        "    if n < 5:\n"
        "        return None\n"
        "    lo, hi = np.percentile(x, [2.5, 97.5])\n"
        "    return {'bas': r(lo), 'haut': r(hi)}\n"),
    _fc("probabilite_valeur_positive", tl("engine/formules_science.py:g:889:40"), "probabilite",
        tl("engine/formules_science.py:g:890:9"),
        "    if not n:\n"
        "        return None\n"
        "    k = int((x > 0).sum()); p = k / n; z = 1.96\n"
        "    c = (p + z * z / (2 * n)) / (1 + z * z / n)\n"
        "    e = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)\n"
        "    return {'probabilite_pct': r(100 * p, 2), 'bas_pct': r(100 * max(0, c - e), 2), 'haut_pct': r(100 * min(1, c + e), 2)}\n"),
    _fc("score_z_derniere_valeur", tl("engine/formules_science.py:g:897:36"), "probabilite",
        tl("engine/formules_science.py:g:898:9"),
        "    if n < 4:\n"
        "        return None\n"
        "    h = x[:-1]\n"
        "    if not h.std():\n"
        "        return None\n"
        "    z = (x[-1] - h.mean()) / h.std(ddof=1)\n"
        "    return {'z': r(z, 3), 'lecture': 'exceptionnelle' if abs(z) > 3 else 'inhabituelle' if abs(z) > 2 else 'normale'}\n"),
]


# 10 formules économiques (TVA, rentabilité, budget, remises, stocks, endettement) — catégorie « finance ».
# Zones A (montants) et B (UNE cellule : le taux) du sélecteur de cible → cible = {"zone_a": …, "zone_b": …} (calculs.py).
_PRE_ZONES = ("    import numpy as np\n    import pandas as pd\n    z = cible if isinstance(cible, dict) else {}\n"
              "    v = lambda k: pd.to_numeric(pd.Series(np.ravel(pd.DataFrame(z.get(k)).to_numpy())), errors='coerce').dropna().to_numpy(dtype='float64')\n"
              "    h, t = v('zone_a'), v('zone_b')\n    if not len(h) or not len(t): return None\n    t = t[0] / 100 if t[0] > 1 else t[0]\n"
              "    r = lambda a, d=6: round(float(a), d)\n")


def _eco(slug, corps, colonne=False, zones=False):
    cle = f"engine/formules_science.py:eco:{slug}"
    f = (_fc if colonne or zones else _ft)(slug, tl(cle + ":nom"), "finance", tl(cle + ":desc"), corps,
                                           **({"types": TYPES_TOUS, "pre": _PRE_ZONES} if zones else {}))
    return dict(f, cible_type="table") if zones else f


FORMULES_SCIENCE += [
    _eco("tva_depuis_ht", "    return {'taux_tva_pct': r(100 * t, 2), 'total_ht': r(h.sum(), 2), 'tva': r(h.sum() * t, 2), 'total_ttc': r(h.sum() * (1 + t), 2)}\n", zones=True),
    _eco("ht_depuis_ttc", "    ht = h.sum() / (1 + t)\n    return {'taux_tva_pct': r(100 * t, 2), 'total_ttc': r(h.sum(), 2), 'total_ht': r(ht, 2), 'tva_contenue': r(h.sum() - ht, 2)}\n", zones=True),
    _eco("chiffre_affaires_prix_quantite", "    if len(num) < 2: return None\n    d = df[num[:2]].dropna()\n"
         "    return {'chiffre_affaires': r((d[num[0]] * d[num[1]]).sum(), 2), 'quantite_totale': r(d[num[1]].sum(), 2), 'lignes': int(len(d))}\n"),
    _eco("prix_moyen_pondere", "    if len(num) < 2: return None\n    d = df[num[:2]].dropna()\n    q = d[num[1]].sum()\n"
         "    return r((d[num[0]] * d[num[1]]).sum() / q, 4) if q else None\n"),
    _eco("seuil_rentabilite", "    if len(num) < 3: return None\n    p, cv, cf = [df[c].dropna().mean() for c in num[:3]]\n    m = p - cv\n"
         "    return {'marge_unitaire': r(m, 4), 'seuil_quantite': r(cf / m, 2), 'seuil_chiffre_affaires': r(cf / m * p, 2)} if m > 0 else None\n"),
    _eco("retour_sur_investissement", "    if len(num) < 2: return None\n    g, c = df[num[0]].sum(), df[num[1]].sum()\n"
         "    return {'gain_net': r(g - c, 2), 'roi_pct': r(100 * (g - c) / c, 2)} if c else None\n"),
    _eco("ecart_budgetaire", "    if len(num) < 2: return None\n    b, a = df[num[0]].sum(), df[num[1]].sum()\n"
         "    return {'ecart': r(a - b, 2), 'ecart_pct': r(100 * (a - b) / b, 2), 'taux_execution_pct': r(100 * a / b, 2)} if b else None\n"),
    _eco("taux_remise_moyen", "    if len(num) < 2: return None\n    d = df[num[:2]].dropna()\n    d = d[d[num[0]] > 0]\n"
         "    return {'remise_moyenne_pct': r(100 * (1 - d[num[1]] / d[num[0]]).mean(), 2), 'remise_totale': r((d[num[0]] - d[num[1]]).sum(), 2)} if len(d) else None\n"),
    _eco("rotation_stocks", "    if len(num) < 2: return None\n    d = df[num[:2]].dropna()\n    s0 = d[num[1]].mean()\n    rot = d[num[0]].sum() / s0 if s0 else None\n"
         "    return {'rotation': r(rot, 2), 'duree_stockage_jours': r(365 / rot, 1) if rot else None} if rot is not None else None\n"),
    _eco("taux_endettement", "    if len(num) < 2: return None\n    d = df[num[:2]].dropna()\n    if not len(d) or not d[num[1]].iloc[-1]: return None\n"
         "    return {'taux_endettement_pct': r(100 * d[num[0]].iloc[-1] / d[num[1]].iloc[-1], 2), 'dettes': r(d[num[0]].iloc[-1], 2), 'capitaux_propres': r(d[num[1]].iloc[-1], 2)}\n"),
]
