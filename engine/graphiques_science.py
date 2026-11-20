"""50 graphiques « data science » (2026-09-23) — ajoutés à GRAPHIQUES_BI_PREDEFINIS (graphiques_bi.py).

Même contrat : `def dessiner(dataframe, colonnes)` → figure matplotlib, exécuté dans le bac à sable du canal
graphique (pandas, numpy, matplotlib, math, statistics — PAS de scipy) : régressions, k-means, lissages,
autocorrélations, distances de Mahalanobis… sont donc calculés en numpy. `colonnes` = colonnes choisies
par l'utilisateur dans l'ordre ; chaque description dit le rôle de chaque colonne.

Familles : qualité des données, distributions, relations et régression, séries temporelles, segmentation
et composition, anomalies, finance et indicateurs, comparaison statistique.
"""
from engine.i18n import tl
from engine.i18n import t

TYPES_TOUS = ""

_PRE = tl("engine/graphiques_science.py:g:15:8")


def _g(slug, nom, categorie, desc, corps, nc_min=1, nc_max=1):
    return {"slug": slug, "nom": nom, "categorie": categorie, "cible_type": "colonnes_multiples",
            "nombre_colonnes_min": nc_min, "nombre_colonnes_max": nc_max,
            "types_cibles_compatibles": TYPES_TOUS, "description": desc, "code": _PRE + corps}


GRAPHIQUES_SCIENCE = [
    # ═══════════════════ QUALITÉ DES DONNÉES ═══════════════════
    _g("carte_valeurs_manquantes", tl("engine/graphiques_science.py:g:45:36"), "qualite",
       tl("engine/graphiques_science.py:g:46:8"),
       tl("engine/graphiques_science.py:g:47:8"), 1, None),
    _g("taux_remplissage_colonnes", tl("engine/graphiques_science.py:g:57:37"), "qualite",
       tl("engine/graphiques_science.py:g:58:8"),
       tl("engine/graphiques_science.py:g:59:8"), 1, None),
    _g("doublons_par_colonne", tl("engine/graphiques_science.py:g:68:32"), "qualite",
       tl("engine/graphiques_science.py:g:69:8"),
       tl("engine/graphiques_science.py:g:70:8"), 1, None),
    _g("cardinalite_colonnes", tl("engine/graphiques_science.py:g:77:32"), "qualite",
       tl("engine/graphiques_science.py:g:78:8"),
       tl("engine/graphiques_science.py:g:79:8"), 1, None),
    _g("valeurs_frequentes_top10", tl("engine/graphiques_science.py:g:86:36"), "qualite",
       tl("engine/graphiques_science.py:g:87:8"),
       '''    v = txt(colonnes[0]).value_counts()
    if v.empty:
        return vide()
    top = v.head(10)[::-1]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.barh(top.index, top.to_numpy(), color="#4C72B0")
    for i, k in enumerate(top.to_numpy()):
        ax.text(k, i, f" {100 * k / v.sum():.1f} %", va="center", fontsize=8)
    ax.set_xlabel("Occurrences")
    fig.tight_layout()
    return fig
'''),

    # ═══════════════════ DISTRIBUTIONS ═══════════════════
    _g("histogramme_courbe_normale", tl("engine/graphiques_science.py:g:102:38"), "distribution",
       tl("engine/graphiques_science.py:g:103:8"),
       '''    x = num(colonnes[0]); x = x[~np.isnan(x)]
    if len(x) < 3 or not x.std():
        return vide()
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(x, bins="auto", density=True, color="#4C72B0", alpha=0.6, label="Observé")
    m, s = x.mean(), x.std(ddof=1)
    g = np.linspace(x.min(), x.max(), 200)
    ax.plot(g, np.exp(-0.5 * ((g - m) / s) ** 2) / (s * math.sqrt(2 * math.pi)), color="#C44E52", lw=2, label="Loi normale")
    ax.set_xlabel(colonnes[0]); ax.legend()
    fig.tight_layout()
    return fig
'''),
    _g("histogramme_echelle_log", tl("engine/graphiques_science.py:g:116:35"), "distribution",
       tl("engine/graphiques_science.py:g:117:8"),
       tl("engine/graphiques_science.py:g:118:8")),
    _g("densites_par_groupe", tl("engine/graphiques_science.py:g:127:31"), "distribution",
       tl("engine/graphiques_science.py:g:128:8"),
       '''    g, y = txt(colonnes[0]), num(colonnes[1])
    fig, ax = plt.subplots(figsize=(6, 4))
    trace = 0
    for i, k in enumerate(g.value_counts().index[:8]):
        x = y[(g == k).to_numpy()]; x = x[~np.isnan(x)]
        if len(x) < 3 or not x.std():
            continue
        h = 1.06 * x.std() * len(x) ** -0.2
        grille = np.linspace(np.nanmin(y), np.nanmax(y), 200)
        d = np.exp(-0.5 * ((grille[:, None] - x[None, :]) / h) ** 2).sum(axis=1) / (len(x) * h * math.sqrt(2 * math.pi))
        ax.plot(grille, d, color=COUL[i % 10], lw=2, label=f"{k} (n={len(x)})"); trace += 1
    if not trace:
        return vide()
    ax.set_xlabel(colonnes[1]); ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("crete_par_groupe", tl("engine/graphiques_science.py:g:146:28"), "distribution",
       tl("engine/graphiques_science.py:g:147:8"),
       '''    g, y = txt(colonnes[0]), num(colonnes[1])
    niv = [k for k in g.value_counts().index[:12]]
    grille = np.linspace(np.nanmin(y), np.nanmax(y), 200) if np.isfinite(np.nanmax(y)) else None
    if grille is None or not niv:
        return vide()
    fig, ax = plt.subplots(figsize=(6, 0.5 * len(niv) + 2))
    for i, k in enumerate(niv):
        x = y[(g == k).to_numpy()]; x = x[~np.isnan(x)]
        if len(x) < 3 or not x.std():
            continue
        h = 1.06 * x.std() * len(x) ** -0.2
        d = np.exp(-0.5 * ((grille[:, None] - x[None, :]) / h) ** 2).sum(axis=1)
        d = 0.9 * d / d.max()
        ax.fill_between(grille, i, i + d, color=COUL[i % 10], alpha=0.7)
    ax.set_yticks(range(len(niv))); ax.set_yticklabels(niv, fontsize=8); ax.set_xlabel(colonnes[1])
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("points_disperses_par_groupe", tl("engine/graphiques_science.py:g:166:39"), "distribution",
       tl("engine/graphiques_science.py:g:167:8"),
       '''    g, y = txt(colonnes[0]), num(colonnes[1])
    niv = list(g.value_counts().index[:12])
    rng = np.random.default_rng(0)
    fig, ax = plt.subplots(figsize=(6, 4))
    for i, k in enumerate(niv):
        x = y[(g == k).to_numpy()]; x = x[~np.isnan(x)]
        ax.scatter(i + rng.uniform(-0.2, 0.2, len(x)), x, s=10, alpha=0.6, color=COUL[i % 10])
        if len(x):
            ax.plot([i - 0.3, i + 0.3], [np.median(x)] * 2, color="black", lw=2)
    ax.set_xticks(range(len(niv))); ax.set_xticklabels(niv, rotation=45, ha="right"); ax.set_ylabel(colonnes[1])
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("nuage_de_pluie", tl("engine/graphiques_science.py:g:181:26"), "distribution",
       tl("engine/graphiques_science.py:g:182:8"),
       '''    g, y = txt(colonnes[0]), num(colonnes[1])
    niv = list(g.value_counts().index[:8])
    rng = np.random.default_rng(0)
    fig, ax = plt.subplots(figsize=(6, 0.9 * len(niv) + 1.5))
    for i, k in enumerate(niv):
        x = y[(g == k).to_numpy()]; x = x[~np.isnan(x)]
        if len(x) < 3 or not x.std():
            continue
        h = 1.06 * x.std() * len(x) ** -0.2
        grille = np.linspace(x.min(), x.max(), 150)
        d = np.exp(-0.5 * ((grille[:, None] - x[None, :]) / h) ** 2).sum(axis=1); d = 0.35 * d / d.max()
        ax.fill_between(grille, i + 0.1, i + 0.1 + d, color=COUL[i % 10], alpha=0.6)
        ax.boxplot(x, positions=[i], vert=False, widths=0.12, showfliers=False)
        ax.scatter(x, i - 0.2 + rng.uniform(-0.08, 0.08, len(x)), s=6, alpha=0.5, color=COUL[i % 10])
    ax.set_yticks(range(len(niv))); ax.set_yticklabels(niv); ax.set_xlabel(colonnes[1])
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("quantiles_deux_variables", tl("engine/graphiques_science.py:g:201:36"), "distribution",
       tl("engine/graphiques_science.py:g:202:8"),
       '''    a, b = num(colonnes[0]), num(colonnes[1]); a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return vide()
    q = np.linspace(1, 99, 99)
    qa, qb = np.percentile(a, q), np.percentile(b, q)
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.scatter(qa, qb, s=12, color="#4C72B0")
    lo, hi = min(qa.min(), qb.min()), max(qa.max(), qb.max())
    ax.plot([lo, hi], [lo, hi], "--", color="grey")
    ax.set_xlabel("Quantiles " + colonnes[0]); ax.set_ylabel("Quantiles " + colonnes[1])
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("densite_2d_contours", tl("engine/graphiques_science.py:g:216:31"), "distribution",
       tl("engine/graphiques_science.py:g:217:8"),
       '''    a, b = num(colonnes[0]), num(colonnes[1]); ok = ~(np.isnan(a) | np.isnan(b))
    if ok.sum() < 10:
        return vide()
    h, xe, ye = np.histogram2d(a[ok], b[ok], bins=25)
    fig, ax = plt.subplots(figsize=(6, 4.5))
    cs = ax.contourf((xe[:-1] + xe[1:]) / 2, (ye[:-1] + ye[1:]) / 2, h.T, levels=10, cmap="viridis")
    fig.colorbar(cs, ax=ax, label="Nombre de lignes")
    ax.set_xlabel(colonnes[0]); ax.set_ylabel(colonnes[1])
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("profil_des_centiles", tl("engine/graphiques_science.py:g:229:31"), "distribution",
       tl("engine/graphiques_science.py:g:230:8"),
       '''    x = num(colonnes[0]); x = x[~np.isnan(x)]
    if len(x) < 3:
        return vide()
    q = np.arange(1, 100); v = np.percentile(x, q)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(q, v, color="#4C72B0", lw=2)
    for p in (10, 25, 50, 75, 90):
        ax.axvline(p, color="grey", ls=":", lw=1); ax.text(p, v[p - 1], f" P{p}={v[p - 1]:.3g}", fontsize=7)
    ax.set_xlabel("Centile (%)"); ax.set_ylabel(colonnes[0])
    fig.tight_layout()
    return fig
'''),

    # ═══════════════════ RELATIONS ET RÉGRESSION ═══════════════════
    _g("regression_bande_confiance", tl("engine/graphiques_science.py:g:245:38"), "relation",
       tl("engine/graphiques_science.py:g:246:8"),
       '''    a, b = num(colonnes[0]), num(colonnes[1]); ok = ~(np.isnan(a) | np.isnan(b)); a, b = a[ok], b[ok]
    n = len(a)
    if n < 4 or not a.std():
        return vide()
    pente, orig = np.polyfit(a, b, 1)
    pred = pente * a + orig
    s = math.sqrt(((b - pred) ** 2).sum() / (n - 2))
    g = np.linspace(a.min(), a.max(), 100)
    e = 1.96 * s * np.sqrt(1 / n + (g - a.mean()) ** 2 / ((a - a.mean()) ** 2).sum())
    sst = ((b - b.mean()) ** 2).sum()
    r2 = 1 - ((b - pred) ** 2).sum() / sst if sst else 0.0   # b constant : R² indéfini (nan) -> 0
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(a, b, s=12, alpha=0.6, color="#4C72B0")
    ax.plot(g, pente * g + orig, color="#C44E52", lw=2, label=f"y = {pente:.3g}·x + {orig:.3g}   R² = {r2:.3f}")
    ax.fill_between(g, pente * g + orig - e, pente * g + orig + e, color="#C44E52", alpha=0.2)
    ax.set_xlabel(colonnes[0]); ax.set_ylabel(colonnes[1]); ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("residus_regression", tl("engine/graphiques_science.py:g:265:30"), "relation",
       tl("engine/graphiques_science.py:g:266:8"),
       tl("engine/graphiques_science.py:g:267:8"), 2, 2),
    _g("regression_polynomiale", tl("engine/graphiques_science.py:g:279:34"), "relation",
       tl("engine/graphiques_science.py:g:280:8"),
       tl("engine/graphiques_science.py:g:281:8"), 2, 2),
    _g("moyenne_par_tranche", tl("engine/graphiques_science.py:g:295:31"), "relation",
       tl("engine/graphiques_science.py:g:296:8"),
       tl("engine/graphiques_science.py:g:297:8"), 2, 2),
    _g("nuage_log_log", tl("engine/graphiques_science.py:g:309:25"), "relation",
       tl("engine/graphiques_science.py:g:310:8"),
       tl("engine/graphiques_science.py:g:311:8"), 2, 2),
    _g("coordonnees_paralleles", tl("engine/graphiques_science.py:g:323:34"), "relation",
       tl("engine/graphiques_science.py:g:324:8"),
       tl("engine/graphiques_science.py:g:325:8"), 3, 9),
    _g("correlations_avec_cible", tl("engine/graphiques_science.py:g:341:35"), "relation",
       tl("engine/graphiques_science.py:g:342:8"),
       tl("engine/graphiques_science.py:g:343:8"), 3, 12),

    # ═══════════════════ SÉRIES TEMPORELLES ═══════════════════
    _g("serie_moyennes_mobiles", tl("engine/graphiques_science.py:g:356:34"), "evolution",
       tl("engine/graphiques_science.py:g:357:8"),
       '''    d = pd.DataFrame({"d": dates(colonnes[0]), "v": num(colonnes[1])}).dropna()
    if d.empty:
        return vide()
    s = d.groupby(d["d"].dt.floor("D"))["v"].sum().asfreq("D", fill_value=0)
    fig, ax = plt.subplots(figsize=(7, 4))
    gy_trace_degrade(ax, s.index, s.to_numpy(), "#999999", lw=1, label="Par jour")   # évolution : dégradé sombre -> clair
    gy_trace_degrade(ax, s.index, s.rolling(7).mean().to_numpy(), "#4C72B0", lw=2, label="Moyenne 7 j")
    gy_trace_degrade(ax, s.index, s.rolling(30).mean().to_numpy(), "#C44E52", lw=2, label="Moyenne 30 j")
    ax.legend(fontsize=8); fig.autofmt_xdate()
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("decomposition_tendance_saison", tl("engine/graphiques_science.py:g:370:41"), "evolution",
       tl("engine/graphiques_science.py:g:371:8"),
       tl("engine/graphiques_science.py:g:372:8"), 2, 2),
    _g("autocorrelogramme", tl("engine/graphiques_science.py:g:388:29"), "evolution",
       tl("engine/graphiques_science.py:g:389:8"),
       tl("engine/graphiques_science.py:g:390:8")),
    _g("heatmap_mois_annee", tl("engine/graphiques_science.py:g:405:30"), "evolution",
       tl("engine/graphiques_science.py:g:406:8"),
       '''    d = pd.DataFrame({"d": dates(colonnes[0]), "v": num(colonnes[1])}).dropna()
    if d.empty:
        return vide()
    t = d.pivot_table(index=d["d"].dt.year, columns=d["d"].dt.month, values="v", aggfunc="sum")
    t = t.reindex(columns=range(1, 13))
    fig, ax = plt.subplots(figsize=(7, 0.5 * len(t) + 2))
    im = ax.imshow(t.to_numpy(dtype="float64", na_value=np.nan), aspect="auto", cmap="YlOrRd")
    ax.set_xticks(range(12)); ax.set_xticklabels(["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"])
    ax.set_yticks(range(len(t))); ax.set_yticklabels([str(a) for a in t.index])
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("heatmap_jour_heure", tl("engine/graphiques_science.py:g:420:30"), "evolution",
       tl("engine/graphiques_science.py:g:421:8"),
       '''    dd = dates(colonnes[0])
    v = num(colonnes[1]) if len(colonnes) > 1 else np.ones(len(dd))
    d = pd.DataFrame({"j": dd.dt.dayofweek, "h": dd.dt.hour, "v": v}).dropna()
    if d.empty:
        return vide()
    t = d.pivot_table(index="j", columns="h", values="v", aggfunc="sum").reindex(index=range(7), columns=range(24))
    fig, ax = plt.subplots(figsize=(8, 3.5))
    im = ax.imshow(t.to_numpy(dtype="float64", na_value=np.nan), aspect="auto", cmap="Blues")
    ax.set_yticks(range(7)); ax.set_yticklabels(["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"])
    ax.set_xticks(range(0, 24, 2)); ax.set_xlabel("Heure"); fig.colorbar(im, ax=ax)
    fig.tight_layout()
    return fig
''', 1, 2),
    _g("cumul_annuel_compare", tl("engine/graphiques_science.py:g:435:32"), "evolution",
       tl("engine/graphiques_science.py:g:436:8"),
       tl("engine/graphiques_science.py:g:437:8"), 2, 2),
    _g("variation_mensuelle_barres", tl("engine/graphiques_science.py:g:448:38"), "evolution",
       tl("engine/graphiques_science.py:g:449:8"),
       '''    d = pd.DataFrame({"d": dates(colonnes[0]), "v": num(colonnes[1])}).dropna()
    s = d.groupby(d["d"].dt.to_period("M"))["v"].sum()
    ch = (s.pct_change() * 100).replace([np.inf, -np.inf], np.nan).dropna()
    if ch.empty:
        return vide()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar([str(p) for p in ch.index], ch.to_numpy(), color=["#55A868" if v >= 0 else "#C44E52" for v in ch])
    ax.axhline(0, color="black", lw=1); ax.set_ylabel("Variation (%)"); ax.tick_params(axis="x", rotation=60, labelsize=7)
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("prevision_holt", tl("engine/graphiques_science.py:g:461:26"), "evolution",
       tl("engine/graphiques_science.py:g:462:8"),
       tl("engine/graphiques_science.py:g:463:8"), 2, 2),
    _g("carte_de_controle", tl("engine/graphiques_science.py:g:486:29"), "evolution",
       tl("engine/graphiques_science.py:g:487:8"),
       tl("engine/graphiques_science.py:g:488:8")),
    _g("carte_cusum", tl("engine/graphiques_science.py:g:503:23"), "evolution",
       tl("engine/graphiques_science.py:g:504:8"),
       tl("engine/graphiques_science.py:g:505:8")),

    # ═══════════════════ SEGMENTATION ET COMPOSITION ═══════════════════
    _g("courbe_de_lorenz", tl("engine/graphiques_science.py:g:519:28"), "segmentation",
       tl("engine/graphiques_science.py:g:520:8"),
       tl("engine/graphiques_science.py:g:521:8")),
    _g("pareto_classes_abc", tl("engine/graphiques_science.py:g:533:30"), "segmentation",
       tl("engine/graphiques_science.py:g:534:8"),
       tl("engine/graphiques_science.py:g:535:8"), 2, 2),
    _g("diagramme_mosaique", tl("engine/graphiques_science.py:g:550:30"), "segmentation",
       tl("engine/graphiques_science.py:g:551:8"),
       tl("engine/graphiques_science.py:g:552:8"), 2, 2),
    _g("residus_khi2_tableau_croise", tl("engine/graphiques_science.py:g:576:39"), "segmentation",
       tl("engine/graphiques_science.py:g:577:8"),
       '''    t = pd.crosstab(txt(colonnes[0]), txt(colonnes[1]))
    if t.shape[0] < 2 or t.shape[1] < 2:
        return vide()
    t = t.iloc[:15, :15]
    o = t.to_numpy(dtype="float64"); e = o.sum(axis=1, keepdims=True) * o.sum(axis=0, keepdims=True) / o.sum()
    res = (o - e) / np.sqrt(np.where(e == 0, 1, e))
    lim = max(2.5, np.abs(res).max())
    fig, ax = plt.subplots(figsize=(7, 0.4 * len(t) + 2))
    im = ax.imshow(res, cmap="RdBu", vmin=-lim, vmax=lim, aspect="auto")
    for i in range(res.shape[0]):
        for j in range(res.shape[1]):
            ax.text(j, i, f"{res[i, j]:.1f}", ha="center", va="center", fontsize=7)
    ax.set_xticks(range(t.shape[1])); ax.set_xticklabels(t.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(t.shape[0])); ax.set_yticklabels(t.index, fontsize=8); fig.colorbar(im, ax=ax)
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("segments_kmeans_nuage", tl("engine/graphiques_science.py:g:595:33"), "segmentation",
       tl("engine/graphiques_science.py:g:596:8"),
       '''    a, b = num(colonnes[0]), num(colonnes[1]); ok = ~(np.isnan(a) | np.isnan(b)); a, b = a[ok], b[ok]
    if len(a) < 6:
        return vide()
    z = np.column_stack([(a - a.mean()) / (a.std() or 1), (b - b.mean()) / (b.std() or 1)])
    rng = np.random.default_rng(42); c = z[rng.choice(len(z), 3, replace=False)]
    for _k in range(50):
        lab = np.argmin(((z[:, None, :] - c[None, :, :]) ** 2).sum(axis=2), axis=1)
        c = np.array([z[lab == k].mean(axis=0) if (lab == k).any() else c[k] for k in range(3)])
    fig, ax = plt.subplots(figsize=(6, 4.5))
    for k in range(3):
        ax.scatter(a[lab == k], b[lab == k], s=12, alpha=0.6, color=COUL[k], label=f"Segment {k + 1} ({int((lab == k).sum())})")
    ax.scatter(c[:, 0] * (a.std() or 1) + a.mean(), c[:, 1] * (b.std() or 1) + b.mean(), marker="X", s=150, color="black")
    ax.set_xlabel(colonnes[0]); ax.set_ylabel(colonnes[1]); ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("matrice_rfm", tl("engine/graphiques_science.py:g:613:23"), "segmentation",
       tl("engine/graphiques_science.py:g:614:8"),
       tl("engine/graphiques_science.py:g:615:8"), 3, 3),
    _g("cohortes_retention", tl("engine/graphiques_science.py:g:636:30"), "segmentation",
       tl("engine/graphiques_science.py:g:637:8"),
       tl("engine/graphiques_science.py:g:638:8"), 2, 2),
    _g("parts_categories_dans_le_temps", tl("engine/graphiques_science.py:g:657:42"), "segmentation",
       tl("engine/graphiques_science.py:g:658:8"),
       tl("engine/graphiques_science.py:g:659:8"), 2, 3),

    # ═══════════════════ ANOMALIES ═══════════════════
    _g("anomalies_dans_la_serie", tl("engine/graphiques_science.py:g:676:35"), "anomalie",
       tl("engine/graphiques_science.py:g:677:8"),
       '''    x = num(colonnes[0]); idx = np.arange(1, len(x) + 1); ok = ~np.isnan(x); x, idx = x[ok], idx[ok]
    if len(x) < 5:
        return vide()
    med = np.median(x); mad = np.median(np.abs(x - med)) or 1e-9
    z = 0.6745 * (x - med) / mad; a = np.abs(z) > 3.5
    fig, ax = plt.subplots(figsize=(7, 4))
    gy_trace_degrade(ax, idx, x, "#4C72B0", lw=1)   # évolution : dégradé sombre -> clair
    ax.scatter(idx[a], x[a], s=60, facecolors="none", edgecolors="#C44E52", lw=2, label=f"Anomalies ({int(a.sum())})")
    for i, v in zip(idx[a][:15], x[a][:15]):
        ax.annotate(str(i), (i, v), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax.set_xlabel("Ligne"); ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
'''),
    _g("anomalies_deux_variables", tl("engine/graphiques_science.py:g:692:36"), "anomalie",
       tl("engine/graphiques_science.py:g:693:8"),
       '''    a, b = num(colonnes[0]), num(colonnes[1]); ok = ~(np.isnan(a) | np.isnan(b)); a, b = a[ok], b[ok]
    if len(a) < 6:
        return vide()
    m = np.column_stack([a, b]); mu = m.mean(axis=0); cv = np.cov(m, rowvar=False); inv = np.linalg.pinv(cv)
    d2 = np.einsum("ij,jk,ik->i", m - mu, inv, m - mu); seuil = 7.378
    atyp = d2 > seuil
    val, vec = np.linalg.eigh(cv); t = np.linspace(0, 2 * math.pi, 200)
    ell = (vec @ np.diag(np.sqrt(np.maximum(val, 0) * seuil)) @ np.vstack([np.cos(t), np.sin(t)])).T + mu
    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.scatter(a[~atyp], b[~atyp], s=12, alpha=0.5, color="#4C72B0", label="Normales")
    ax.scatter(a[atyp], b[atyp], s=30, color="#C44E52", label=f"Atypiques ({int(atyp.sum())})")
    ax.plot(ell[:, 0], ell[:, 1], "--", color="grey")
    ax.set_xlabel(colonnes[0]); ax.set_ylabel(colonnes[1]); ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
''', 2, 2),
    _g("boite_moustaches_etiquetee", tl("engine/graphiques_science.py:g:710:38"), "anomalie",
       tl("engine/graphiques_science.py:g:711:8"),
       '''    x = num(colonnes[0]); idx = np.arange(1, len(x) + 1); ok = ~np.isnan(x); x, idx = x[ok], idx[ok]
    if len(x) < 4:
        return vide()
    q1, q3 = np.percentile(x, [25, 75]); e = q3 - q1; hors = (x < q1 - 1.5 * e) | (x > q3 + 1.5 * e)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.boxplot(x, vert=False, widths=0.5)
    for i, v in zip(idx[hors][:25], x[hors][:25]):
        ax.annotate(f"L{i}", (v, 1), xytext=(0, 12), textcoords="offset points", fontsize=7, rotation=60, color="#C44E52")
    ax.set_yticks([]); ax.set_xlabel(colonnes[0]); ax.set_title(f"{int(hors.sum())} valeur(s) aberrante(s)", fontsize=9)
    fig.tight_layout()
    return fig
'''),
    _g("scores_z_par_ligne", tl("engine/graphiques_science.py:g:724:30"), "anomalie",
       tl("engine/graphiques_science.py:g:725:8"),
       '''    x = num(colonnes[0])
    if np.isnan(x).all() or not np.nanstd(x):
        return vide()
    z = (x - np.nanmean(x)) / np.nanstd(x, ddof=1)
    coul = ["#C44E52" if abs(v) > 3 else "#DD8452" if abs(v) > 2 else "#4C72B0" for v in np.nan_to_num(z)]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(np.arange(1, len(z) + 1), np.nan_to_num(z), color=coul, width=1.0)
    for s in (2, 3):
        ax.axhline(s, ls="--", color="grey", lw=1); ax.axhline(-s, ls="--", color="grey", lw=1)
    ax.set_xlabel("Ligne"); ax.set_ylabel("Score z")
    fig.tight_layout()
    return fig
'''),

    # ═══════════════════ FINANCE ET INDICATEURS ═══════════════════
    _g("flux_tresorerie_cumules", tl("engine/graphiques_science.py:g:741:35"), "finance",
       tl("engine/graphiques_science.py:g:742:8"),
       tl("engine/graphiques_science.py:g:743:8")),
    _g("chandeliers_mensuels", tl("engine/graphiques_science.py:g:757:32"), "finance",
       tl("engine/graphiques_science.py:g:758:8"),
       tl("engine/graphiques_science.py:g:759:8"), 2, 2),
    _g("graphique_baisse_depuis_sommet", tl("engine/graphiques_science.py:g:774:42"), "finance",
       tl("engine/graphiques_science.py:g:775:8"),
       tl("engine/graphiques_science.py:g:776:8")),
    _g("bandes_de_bollinger", tl("engine/graphiques_science.py:g:787:31"), "finance",
       tl("engine/graphiques_science.py:g:788:8"),
       '''    s = pd.Series(num(colonnes[0])).dropna().reset_index(drop=True)
    if len(s) < 21:
        return vide(t("rg8.msg.011"))
    m = s.rolling(20).mean(); e = s.rolling(20).std()
    fig, ax = plt.subplots(figsize=(7, 4))
    gy_trace_degrade(ax, s.index, s.to_numpy(), "#4C72B0", lw=1, label=colonnes[0])   # évolution : dégradé sombre -> clair
    gy_trace_degrade(ax, m.index, m.to_numpy(), "#555555", lw=1, label="Moyenne 20")
    ax.fill_between(m.index, (m - 2 * e).to_numpy(), (m + 2 * e).to_numpy(), color="grey", alpha=0.2, label="±2 écarts-types")
    hors = (s > m + 2 * e) | (s < m - 2 * e)
    ax.scatter(s.index[hors], s[hors], color="#C44E52", s=15, zorder=3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
'''),
    _g("tableau_indicateurs", tl("engine/graphiques_science.py:g:803:31"), "indicateur",
       tl("engine/graphiques_science.py:g:804:8"),
       tl("engine/graphiques_science.py:g:805:8"), 1, 4),

    # ═══════════════════ COMPARAISON STATISTIQUE ═══════════════════
    _g("proportions_avec_intervalles", tl("engine/graphiques_science.py:g:822:40"), "comparaison",
       tl("engine/graphiques_science.py:g:823:8"),
       tl("engine/graphiques_science.py:g:824:8"), 2, 2),
    _g("bland_altman", tl("engine/graphiques_science.py:g:844:24"), "comparaison",
       tl("engine/graphiques_science.py:g:845:8"),
       tl("engine/graphiques_science.py:g:846:8"), 2, 2),
]
