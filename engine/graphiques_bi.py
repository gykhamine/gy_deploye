"""
Bibliothèque de graphiques prédéfinis pour les tableaux de bord BI (voir
models.GraphiqueBI / TableauBordBI / WidgetGraphiqueBI).

Même principe que formules_calcul.py : chaque graphique est un script Python
définissant `def dessiner(dataframe, colonnes): ...` qui retourne une figure
matplotlib, exécuté dans le bac à sable dédié (voir
executor.executer_graphique_bi_securise). `colonnes` est la liste ordonnée
des slugs sélectionnés par l'utilisateur pour CE widget (dans l'ordre choisi
côté canevas) ; `dataframe` contient TOUJOURS toutes les colonnes de la
table source, déjà castées selon leur type EAV — chaque fonction ne touche
qu'aux colonnes qui lui ont été passées dans `colonnes`.

Convention commune à toutes les fonctions ci-dessous :
- jamais de plt.show() (backend Agg, la figure est convertie en PNG base64
  juste après par le sandbox) ;
- toujours fig.tight_layout() avant de retourner, pour un rendu propre à
  n'importe quelle taille de widget sur le canevas ;
- les colonnes manquantes/vides sont gérées silencieusement (dropna) plutôt
  que de lever une exception, pour ne jamais casser un widget sur une ligne
  mal saisie.

`synchroniser_graphiques_bi_predefinis()` est appelé au premier accès à la
liste des graphiques (voir views.api_graphiques_bi), même pattern paresseux
que synchroniser_formules_predefinies(). Un graphique prédéfini déjà modifié
manuellement par une entreprise n'est jamais écrasé : seuls les graphiques
avec entreprise=None et predefini=True sont synchronisés ici.
"""
from engine.i18n import tl
from engine.i18n import t

TYPES_NUMERIQUES = "nombre,decimal,monnaie,pourcentage,barre_progression,note"
TYPES_CATEGORIEL = "choix_unique,couleur,texte"
TYPES_DATE = "date,datetime"
TYPES_TOUS = ""

# ── Helpers partagés ────────────────────────────────────────────────────
def _g(slug, nom, categorie, desc, code, nc_min=2, nc_max=2, types=None):
    return {"slug": slug, "nom": nom, "categorie": categorie,
            "cible_type": "colonnes_multiples",
            "nombre_colonnes_min": nc_min, "nombre_colonnes_max": nc_max,
            "types_cibles_compatibles": types if types is not None else TYPES_TOUS,
            "description": desc, "code": code}

def _g1(slug, nom, categorie, desc, code, types=None):
    return _g(slug, nom, categorie, desc, code, nc_min=1, nc_max=1, types=types)

def _g3(slug, nom, categorie, desc, code, types=None):
    return _g(slug, nom, categorie, desc, code, nc_min=3, nc_max=3, types=types)


GRAPHIQUES_BI_PREDEFINIS = [
    # ═══════════════════════ COMPARAISON ═══════════════════════
    {
        "slug": "barres_verticales",
        "nom": "Barres verticales",
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:60:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_col = colonnes[0], colonnes[1]
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna(subset=[val_col])
    df[cat_col] = df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str)
    agg = df.groupby(cat_col)[val_col].mean().sort_values(ascending=False).head(30)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(agg.index.astype(str), agg.values, color="#4C72B0")
    ax.set_xlabel(cat_col); ax.set_ylabel("moyenne de " + val_col)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "barres_horizontales",
        "nom": "Barres horizontales",
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:86:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_col = colonnes[0], colonnes[1]
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna().sort_values(val_col)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.barh(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), df[val_col], color="#55A868")
    ax.set_xlabel(val_col); ax.set_ylabel(cat_col)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "barres_groupees",
        "nom": tl("engine/graphiques_bi.py:g:106:16"),
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:111:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_cols = colonnes[0], colonnes[1:]
    df = dataframe[[cat_col] + val_cols].copy()
    for c in val_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=val_cols, how="all")
    x = np.arange(len(df))
    largeur = 0.8 / max(len(val_cols), 1)
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, c in enumerate(val_cols):
        ax.bar(x + i * largeur, df[c], largeur, label=c)
    ax.set_xticks(x + largeur * (len(val_cols) - 1) / 2)
    ax.set_xticklabels(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), rotation=45, ha="right")
    ax.legend()
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "barres_empilees",
        "nom": tl("engine/graphiques_bi.py:g:137:16"),
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:142:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_cols = colonnes[0], colonnes[1:]
    df = dataframe[[cat_col] + val_cols].copy()
    for c in val_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    fig, ax = plt.subplots(figsize=(6, 4))
    bas = np.zeros(len(df))
    for c in val_cols:
        ax.bar(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), df[c], bottom=bas, label=c)
        bas = bas + df[c].to_numpy(dtype="float64")
    ax.tick_params(axis="x", rotation=45)
    ax.legend()
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "barres_100_pourcent",
        "nom": tl("engine/graphiques_bi.py:g:166:16"),
        "categorie": "comparaison",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/graphiques_bi.py:g:170:24"),
        "code": tl("engine/graphiques_bi.py:g:172:17"),
    },
    {
        "slug": "lollipop",
        "nom": "Lollipop (tige + point)",
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:198:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_col = colonnes[0], colonnes[1]
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna().sort_values(val_col)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hlines(y=df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), xmin=0, xmax=df[val_col], color="#8172B2")
    ax.plot(df[val_col], df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), "o", color="#8172B2")
    ax.set_xlabel(val_col)
    fig.tight_layout()
    return fig
""",
    },

    # ═══════════════════════ ÉVOLUTION TEMPORELLE ═══════════════════════
    {
        "slug": "courbe",
        "nom": tl("engine/graphiques_bi.py:g:221:16"),
        "categorie": "evolution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:226:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_col = colonnes[0], colonnes[1]
    df = dataframe[[x_col, y_col]].copy()
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna().sort_values(x_col)
    fig, ax = plt.subplots(figsize=(6, 4))
    gy_trace_degrade(ax, df[x_col], df[y_col], "#4C72B0", marker="o")   # évolution : dégradé de la couleur, du plus sombre au plus clair
    ax.set_xlabel(x_col); ax.set_ylabel(y_col)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "courbe_multiple",
        "nom": "Courbes multiples",
        "categorie": "evolution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:252:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_cols = colonnes[0], colonnes[1:]
    df = dataframe[[x_col] + y_cols].copy()
    for c in y_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=[x_col]).sort_values(x_col)
    fig, ax = plt.subplots(figsize=(7, 4))
    if df.empty:
        fig.tight_layout()
        return fig
    for i, c in enumerate(y_cols):
        gy_trace_degrade(ax, df[x_col], df[c], "C%d" % (i % 10), marker=".", ms=3, label=c)   # une couleur par courbe, chacune en dégradé sombre -> clair
    ax.set_xlabel(x_col)
    ax.tick_params(axis="x", rotation=45)
    ax.legend()
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "aire",
        "nom": "Aire",
        "categorie": "evolution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:284:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_col = colonnes[0], colonnes[1]
    df = dataframe[[x_col, y_col]].copy()
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna().sort_values(x_col)
    fig, ax = plt.subplots(figsize=(6, 4))
    if df.empty:
        fig.tight_layout()
        return fig
    ax.fill_between(df[x_col], df[y_col], color="#4C72B0", alpha=0.4)
    gy_trace_degrade(ax, df[x_col], df[y_col], "#4C72B0")   # contour de l'aire : dégradé sombre -> clair
    ax.set_xlabel(x_col); ax.set_ylabel(y_col)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "aire_empilee",
        "nom": tl("engine/graphiques_bi.py:g:309:16"),
        "categorie": "evolution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:314:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_cols = colonnes[0], colonnes[1:]
    df = dataframe[[x_col] + y_cols].copy()
    for c in y_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df = df.dropna(subset=[x_col]).sort_values(x_col)
    fig, ax = plt.subplots(figsize=(6, 4))
    if df.empty:
        fig.tight_layout()
        return fig
    ax.stackplot(df[x_col], [df[c] for c in y_cols], labels=y_cols)
    ax.tick_params(axis="x", rotation=45)
    ax.legend(loc="upper left")
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "courbe_cumulative",
        "nom": "Courbe cumulative",
        "categorie": "evolution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:344:24"),
        "code": tl("engine/graphiques_bi.py:g:346:17"),
    },
    {
        "slug": "pas_escalier",
        "nom": tl("engine/graphiques_bi.py:g:371:16"),
        "categorie": "evolution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:376:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_col = colonnes[0], colonnes[1]
    df = dataframe[[x_col, y_col]].copy()
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna().sort_values(x_col)
    fig, ax = plt.subplots(figsize=(6, 4))
    gy_trace_degrade(ax, df[x_col], df[y_col], "#DD8452", etape=True)   # escalier : dégradé sombre -> clair
    ax.set_xlabel(x_col); ax.set_ylabel(y_col)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig
""",
    },

    # ═══════════════════════ RÉPARTITION ═══════════════════════
    {
        "slug": "camembert",
        "nom": "Camembert",
        "categorie": "repartition",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/graphiques_bi.py:g:403:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    col = colonnes[0]
    compte = dataframe[col].where(dataframe[col].notna(), "(vide)").astype(str).value_counts()
    fig, ax = plt.subplots(figsize=(5, 5))
    ax.pie(compte.values, labels=compte.index, autopct="%1.0f%%", startangle=90)
    ax.axis("equal")
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "anneau",
        "nom": "Anneau (donut)",
        "categorie": "repartition",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/graphiques_bi.py:g:425:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    col = colonnes[0]
    compte = dataframe[col].where(dataframe[col].notna(), "(vide)").astype(str).value_counts()
    fig, ax = plt.subplots(figsize=(5, 5))
    wedges, _, _ = ax.pie(
        compte.values, labels=compte.index, autopct="%1.0f%%", startangle=90,
        wedgeprops=dict(width=0.4),
    )
    ax.axis("equal")
    fig.tight_layout()
    return fig
""",
    },

    # ═══════════════════════ DISTRIBUTION STATISTIQUE ═══════════════════════
    {
        "slug": "histogramme",
        "nom": "Histogramme",
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:452:24"),
        "code": tl("engine/graphiques_bi.py:g:454:17"),
    },
    {
        "slug": "boite_moustaches",
        "nom": tl("engine/graphiques_bi.py:g:470:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:474:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    col = colonnes[0]
    serie = pd.to_numeric(dataframe[col], errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=(4, 5))
    ax.boxplot(serie, vert=True, patch_artist=True,
               boxprops=dict(facecolor="#4C72B0", alpha=0.6))
    ax.set_ylabel(col)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "violon",
        "nom": "Violon",
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:497:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    col = colonnes[0]
    serie = pd.to_numeric(dataframe[col], errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=(4, 5))
    if serie.empty:
        fig.tight_layout()
        return fig
    ax.violinplot(serie, showmedians=True)
    ax.set_ylabel(col)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "densite",
        "nom": tl("engine/graphiques_bi.py:g:518:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:522:24"),
        "code": tl("engine/graphiques_bi.py:g:524:17"),
    },
    {
        "slug": "comptage_categoriel",
        "nom": tl("engine/graphiques_bi.py:g:551:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/graphiques_bi.py:g:555:24"),
        "code": tl("engine/graphiques_bi.py:g:557:17"),
    },

    # ═══════════════════════ RELATION ENTRE COLONNES ═══════════════════════
    {
        "slug": "nuage_points",
        "nom": tl("engine/graphiques_bi.py:g:576:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:581:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_col = colonnes[0], colonnes[1]
    df = dataframe[[x_col, y_col]].apply(pd.to_numeric, errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(df[x_col], df[y_col], alpha=0.7, color="#4C72B0")
    ax.set_xlabel(x_col); ax.set_ylabel(y_col)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "bulles",
        "nom": tl("engine/graphiques_bi.py:g:599:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 3,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:604:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_col, taille_col = colonnes[0], colonnes[1], colonnes[2]
    df = dataframe[[x_col, y_col, taille_col]].apply(pd.to_numeric, errors="coerce").dropna()
    tailles = df[taille_col]
    tailles_norm = 30 + 300 * (tailles - tailles.min()) / max((tailles.max() - tailles.min()), 1e-9)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(df[x_col], df[y_col], s=tailles_norm, alpha=0.5, color="#DD8452")
    ax.set_xlabel(x_col); ax.set_ylabel(y_col)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "hexbin",
        "nom": tl("engine/graphiques_bi.py:g:624:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:629:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    x_col, y_col = colonnes[0], colonnes[1]
    df = dataframe[[x_col, y_col]].apply(pd.to_numeric, errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=(6, 4))
    hb = ax.hexbin(df[x_col], df[y_col], gridsize=20, cmap="Blues")
    fig.colorbar(hb, ax=ax, label="Nombre de lignes")
    ax.set_xlabel(x_col); ax.set_ylabel(y_col)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "heatmap_correlation",
        "nom": tl("engine/graphiques_bi.py:g:648:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:653:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    df = dataframe[colonnes].apply(pd.to_numeric, errors="coerce")
    corr = df.corr()
    fig, ax = plt.subplots(figsize=(5.5, 5))
    im = ax.imshow(corr.values, cmap="coolwarm", vmin=-1, vmax=1)
    ax.set_xticks(range(len(colonnes))); ax.set_xticklabels(colonnes, rotation=45, ha="right")
    ax.set_yticks(range(len(colonnes))); ax.set_yticklabels(colonnes)
    for i in range(len(colonnes)):
        for j in range(len(colonnes)):
            ax.text(j, i, f"{corr.values[i, j]:.2f}", ha="center", va="center", fontsize=8)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "radar",
        "nom": tl("engine/graphiques_bi.py:g:676:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:681:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    moyennes = [pd.to_numeric(dataframe[c], errors="coerce").mean() for c in colonnes]
    moyennes = [0 if pd.isna(v) else float(v) for v in moyennes]
    angles = np.linspace(0, 2 * np.pi, len(colonnes), endpoint=False).tolist()
    valeurs = moyennes + moyennes[:1]
    angles += angles[:1]
    fig, ax = plt.subplots(figsize=(5, 5), subplot_kw=dict(polar=True))
    ax.plot(angles, valeurs, color="#4C72B0")
    ax.fill(angles, valeurs, color="#4C72B0", alpha=0.25)
    ax.set_xticks(angles[:-1]); ax.set_xticklabels(colonnes)
    fig.tight_layout()
    return fig
""",
    },

    # ═══════════════════════ INDICATEUR CLÉ (KPI) ═══════════════════════
    {
        "slug": "kpi_carte",
        "nom": tl("engine/graphiques_bi.py:g:705:16"),
        "categorie": "indicateur",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:709:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    col = colonnes[0]
    serie = pd.to_numeric(dataframe[col], errors="coerce").dropna()
    total = serie.sum() if not serie.empty else 0
    moyenne = serie.mean() if not serie.empty else 0
    fig, ax = plt.subplots(figsize=(4, 2.5))
    ax.axis("off")
    ax.text(0.5, 0.62, f"{total:,.0f}".replace(",", " "), ha="center", va="center",
            fontsize=32, fontweight="bold", color="#4C72B0")
    ax.text(0.5, 0.28, f"{col}  ·  moyenne {moyenne:,.1f}".replace(",", " "),
            ha="center", va="center", fontsize=11, color="#555555")
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "jauge",
        "nom": "Jauge (gauge)",
        "categorie": "indicateur",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:736:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    col = colonnes[0]
    serie = pd.to_numeric(dataframe[col], errors="coerce").dropna()
    if serie.empty:
        valeur, mini, maxi = 0, 0, 1
    else:
        valeur, mini, maxi = float(serie.mean()), float(serie.min()), float(serie.max())
    maxi = maxi if maxi > mini else mini + 1
    fraction = max(0.0, min(1.0, (valeur - mini) / (maxi - mini)))
    fig, ax = plt.subplots(figsize=(5, 3), subplot_kw=dict(polar=True))
    ax.set_theta_zero_location("W"); ax.set_theta_direction(-1)
    ax.set_thetamin(0); ax.set_thetamax(180)
    ax.bar([np.pi / 2], [1], width=np.pi, color="#E8E8E8", bottom=0)
    ax.bar([np.pi * (1 - fraction) / 2 + np.pi * fraction], [1], width=np.pi * fraction,
           color="#4C72B0", bottom=0)
    ax.set_yticklabels([]); ax.set_xticklabels([])
    ax.spines["polar"].set_visible(False)
    ax.text(np.pi / 2, -0.3, f"{valeur:,.1f}".replace(",", " "), ha="center", fontsize=18, fontweight="bold")
    fig.tight_layout()
    return fig
""",
    },

    # ═══════════════════════ AUTRES / SPÉCIALISÉS ═══════════════════════
    {
        "slug": "entonnoir",
        "nom": "Entonnoir (funnel)",
        "categorie": "personnalisee",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:773:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    etape_col, val_col = colonnes[0], colonnes[1]
    df = dataframe[[etape_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna().sort_values(val_col, ascending=False)
    fig, ax = plt.subplots(figsize=(6, 4))
    maxi = df[val_col].max() or 1
    for i, (_, ligne) in enumerate(df.iterrows()):
        largeur = ligne[val_col] / maxi
        ax.barh(i, largeur, left=(1 - largeur) / 2, height=0.7, color="#4C72B0")
        ax.text(0.5, i, f"{ligne[etape_col]} ({ligne[val_col]:,.0f})".replace(",", " "),
                ha="center", va="center", color="white", fontsize=9)
    ax.set_yticks([]); ax.set_xticks([]); ax.set_xlim(0, 1)
    ax.invert_yaxis()
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "pareto",
        "nom": tl("engine/graphiques_bi.py:g:799:16"),
        "categorie": "personnalisee",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:804:24"),
        "code": tl("engine/graphiques_bi.py:g:806:17"),
    },

] + [
    # ═══════════════════════ NOUVEAUX GRAPHIQUES ═══════════════════════
    _g("treemap", "Treemap (surface proportionnelle)", "comparaison",
       tl("engine/graphiques_bi.py:g:833:8"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt, matplotlib.patches as mpatches
    import pandas as pd, numpy as np
    cat_col, val_col = colonnes
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna().groupby(cat_col, as_index=False)[val_col].sum().sort_values(val_col, ascending=False).head(20)
    total = df[val_col].sum()
    if total == 0: return plt.figure()
    cmap = plt.cm.get_cmap("tab20", len(df))
    fig, ax = plt.subplots(figsize=(7, 4)); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
    y = 0.0
    for i, (_, row) in enumerate(df.iterrows()):
        h = row[val_col] / total
        rect = mpatches.FancyBboxPatch((0, y), 1, h, boxstyle="square,pad=0", color=cmap(i), ec="white", lw=1)
        ax.add_patch(rect)
        if h > 0.04: ax.text(0.5, y+h/2, f"{row[cat_col]}\\n{row[val_col]:.0f}", ha="center", va="center", fontsize=7, color="white", fontweight="bold")
        y += h
    fig.tight_layout(); return fig
"""),
    _g("waterfall", "Waterfall (cascade)", "comparaison",
       tl("engine/graphiques_bi.py:g:856:8"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, numpy as np
    cat_col, val_col = colonnes
    df = dataframe[[cat_col, val_col]].dropna().copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce").fillna(0)
    labels = df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str).values; vals = df[val_col].values
    starts = np.concatenate([[0], np.cumsum(vals[:-1])])
    colors = ["#27ae60" if v >= 0 else "#e74c3c" for v in vals]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(labels, vals, bottom=starts, color=colors, edgecolor="white")
    ax.axhline(0, color="black", linewidth=0.6); ax.tick_params(axis="x", rotation=45)
    fig.tight_layout(); return fig
"""),
    _g("barres_divergentes", "Barres divergentes", "comparaison",
       tl("engine/graphiques_bi.py:g:873:8"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    cat_col, val_col = colonnes
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna().groupby(cat_col, as_index=False)[val_col].sum().sort_values(val_col)
    colors = ["#e74c3c" if v < 0 else "#27ae60" for v in df[val_col]]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.barh(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), df[val_col], color=colors)
    ax.axvline(0, color="black", linewidth=0.8)
    fig.tight_layout(); return fig
"""),
    _g3("bullet", tl("engine/graphiques_bi.py:g:888:19"), "comparaison",
        tl("engine/graphiques_bi.py:g:889:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    cat_col, real_col, tgt_col = colonnes
    df = dataframe[[cat_col, real_col, tgt_col]].copy()
    for c in [real_col, tgt_col]: df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna()
    fig, ax = plt.subplots(figsize=(7, max(3, len(df)*0.55)))
    for i, (_, row) in enumerate(df.iterrows()):
        ax.barh(i, row[real_col], height=0.5, color="#4C72B0", alpha=0.8)
        ax.plot([row[tgt_col]]*2, [i-0.35, i+0.35], color="#e74c3c", linewidth=2.5)
    ax.set_yticks(range(len(df))); ax.set_yticklabels(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str))
    fig.tight_layout(); return fig
"""),
    _g("courbe_prevision", tl("engine/graphiques_bi.py:g:905:28"), "tendance",
       tl("engine/graphiques_bi.py:g:906:8"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, numpy as np
    x_col, y_col = colonnes
    df = dataframe[[x_col, y_col]].copy()
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna().sort_values(x_col)
    fig, ax = plt.subplots(figsize=(7, 4))
    if len(df) < 2:
        fig.tight_layout(); return fig
    x_num = np.arange(len(df)); coef = np.polyfit(x_num, df[y_col], 1)
    gy_trace_degrade(ax, df[x_col].where(df[x_col].notna(), "(vide)").astype(str), df[y_col], "C0", marker="o", label="Réel")
    ax.plot(df[x_col].where(df[x_col].notna(), "(vide)").astype(str), np.polyval(coef, x_num), linestyle="--", color="#e74c3c", label="Tendance")
    ax.tick_params(axis="x", rotation=45); ax.legend()
    fig.tight_layout(); return fig
"""),
    _g("heatmap_calendrier", "Heatmap calendrier", "tendance",
       tl("engine/graphiques_bi.py:g:925:8"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    date_col, val_col = colonnes
    df = dataframe[[date_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna().groupby(date_col, as_index=False)[val_col].sum()
    if df.empty: return plt.figure()
    df["week"] = df[date_col].dt.isocalendar().week.astype(int)
    df["dow"] = df[date_col].dt.dayofweek
    pivot = df.pivot_table(index="dow", columns="week", values=val_col, aggfunc="sum")
    fig, ax = plt.subplots(figsize=(max(7, len(pivot.columns)*0.4), 3))
    im = ax.imshow(pivot, aspect="auto", cmap="YlGn")
    ax.set_yticks(range(7)); ax.set_yticklabels(["Lun","Mar","Mer","Jeu","Ven","Sam","Dim"])
    plt.colorbar(im, ax=ax); fig.tight_layout(); return fig
"""),
    _g3("ligne_seuil", tl("engine/graphiques_bi.py:g:944:24"), "tendance",
        tl("engine/graphiques_bi.py:g:945:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    x_col, y_col, s_col = colonnes
    df = dataframe[[x_col, y_col, s_col]].copy()
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df[s_col] = pd.to_numeric(df[s_col], errors="coerce")
    df = df.dropna(subset=[y_col]).sort_values(x_col)
    xs = df[x_col].where(df[x_col].notna(), "(vide)").astype(str)
    fig, ax = plt.subplots(figsize=(7, 4))
    gy_trace_degrade(ax, xs, df[y_col], "C0", marker="o", label="Valeur")
    if df[s_col].notna().any():
        ax.plot(xs, df[s_col], linestyle="--", color="#e74c3c", label="Seuil")
        ax.fill_between(xs, df[y_col], df[s_col], where=df[y_col] < df[s_col], alpha=0.15, color="#e74c3c")
    ax.tick_params(axis="x", rotation=45); ax.legend()
    fig.tight_layout(); return fig
"""),
    _g1("nuage_mots", tl("engine/graphiques_bi.py:g:964:23"), "distribution",
        tl("engine/graphiques_bi.py:g:965:9"),
tl("engine/graphiques_bi.py:g:966:1")),
    _g1("histogramme_freq", tl("engine/graphiques_bi.py:g:983:29"), "distribution",
        tl("engine/graphiques_bi.py:g:984:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    col = colonnes[0]
    s = pd.to_numeric(dataframe[col], errors="coerce").dropna()
    if s.empty: return plt.figure()
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(s, bins="auto", color="#4C72B0", edgecolor="white", alpha=0.85)
    ax.axvline(s.mean(), color="#e74c3c", linestyle="--", label=f"Moy. {s.mean():.1f}")
    ax.legend(); fig.tight_layout(); return fig
"""),
    _g3("barres_erreur", tl("engine/graphiques_bi.py:g:997:26"), "distribution",
        tl("engine/graphiques_bi.py:g:998:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    cat_col, val_col, err_col = colonnes
    df = dataframe[[cat_col, val_col, err_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df[err_col] = pd.to_numeric(df[err_col], errors="coerce")
    df = df.dropna(subset=[val_col])
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), df[val_col], yerr=df[err_col].fillna(0),
           capsize=4, color="#4C72B0", alpha=0.85, edgecolor="white", ecolor="#e74c3c")
    ax.tick_params(axis="x", rotation=45); fig.tight_layout(); return fig
"""),
    _g3("scatter_couleur", tl("engine/graphiques_bi.py:g:1013:28"), "correlation",
        tl("engine/graphiques_bi.py:g:1014:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    x_col, y_col, c_col = colonnes
    df = dataframe[[x_col, y_col, c_col]].copy()
    df[x_col] = pd.to_numeric(df[x_col], errors="coerce")
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna(subset=[x_col, y_col])
    cats = df[c_col].where(df[c_col].notna(), "(vide)").astype(str).unique()
    cmap = plt.cm.get_cmap("tab10", len(cats))
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, cat in enumerate(cats):
        sub = df[df[c_col].where(df[c_col].notna(), "(vide)").astype(str) == cat]
        ax.scatter(sub[x_col], sub[y_col], label=cat, color=cmap(i), alpha=0.7)
    ax.legend(fontsize=7); fig.tight_layout(); return fig
"""),
    _g3("heatmap_croisee", tl("engine/graphiques_bi.py:g:1032:28"), "correlation",
        tl("engine/graphiques_bi.py:l:heatmap_aide"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    row_col, col_col, val_col = colonnes
    df = dataframe[[row_col, col_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    # Limite : les 15 valeurs les plus frequentes par axe (evite une figure et une matrice gigantesques)
    df = df[df[row_col].isin(df[row_col].value_counts().head(15).index) & df[col_col].isin(df[col_col].value_counts().head(15).index)]
    pivot = df.groupby([row_col, col_col])[val_col].sum().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(min(12, max(5, len(pivot.columns)*0.8)), min(12, max(3, len(pivot)*0.6))))
    im = ax.imshow(pivot.values, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns.astype(str), rotation=45, ha="right")
    ax.set_yticks(range(len(pivot))); ax.set_yticklabels(pivot.index.astype(str))
    plt.colorbar(im, ax=ax)
    for i in range(len(pivot)):
        for j in range(len(pivot.columns)):
            ax.text(j, i, f"{pivot.values[i,j]:.0f}", ha="center", va="center", fontsize=7)
    fig.tight_layout(); return fig
"""),
    _g("regression_lineaire", tl("engine/graphiques_bi.py:g:1052:31"), "correlation",
       "Col 1 = X, col 2 = Y. Nuage + droite + R².",
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, numpy as np
    x_col, y_col = colonnes
    df = dataframe[[x_col, y_col]].copy()
    df[x_col] = pd.to_numeric(df[x_col], errors="coerce")
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna()
    if len(df) < 2: return plt.figure()
    coef = np.polyfit(df[x_col], df[y_col], 1)
    pred = np.polyval(coef, df[x_col])
    ss_res = ((df[y_col]-pred)**2).sum(); ss_tot = ((df[y_col]-df[y_col].mean())**2).sum()
    r2 = 1 - ss_res/ss_tot if ss_tot else 0
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.scatter(df[x_col], df[y_col], alpha=0.6, s=30)
    xs = np.linspace(df[x_col].min(), df[x_col].max(), 100)
    ax.plot(xs, np.polyval(coef, xs), color="#e74c3c", label=f"R²={r2:.3f}")
    ax.legend(); fig.tight_layout(); return fig
"""),
    _g("matrice_confusion", tl("engine/graphiques_bi.py:g:1074:29"), "correlation",
       tl("engine/graphiques_bi.py:g:1075:8"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, numpy as np
    a_col, b_col = colonnes
    df = dataframe[[a_col, b_col]].dropna().copy()
    df["_a_txt"] = df[a_col].astype(str)
    df["_b_txt"] = df[b_col].astype(str)
    # Limite : les 15 valeurs les plus frequentes de chaque colonne (evite une matrice et une figure gigantesques)
    top_a = df["_a_txt"].value_counts().head(15).index
    top_b = df["_b_txt"].value_counts().head(15).index
    df = df[df["_a_txt"].isin(top_a) & df["_b_txt"].isin(top_b)]
    tab = pd.crosstab(df["_a_txt"], df["_b_txt"])
    mat = tab.values
    lignes, colonnes_txt = list(tab.index), list(tab.columns)
    fig, ax = plt.subplots(figsize=(min(12, max(4, 0.7 * len(colonnes_txt) + 2)), min(12, max(4, 0.7 * len(lignes) + 2))))
    im = ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(colonnes_txt))); ax.set_xticklabels(colonnes_txt, rotation=45, ha="right")
    ax.set_yticks(range(len(lignes))); ax.set_yticklabels(lignes)
    ax.set_xlabel(b_col); ax.set_ylabel(a_col)
    for i in range(len(lignes)):
        for j in range(len(colonnes_txt)): ax.text(j, i, mat[i, j], ha="center", va="center", fontsize=9)
    plt.colorbar(im, ax=ax); fig.tight_layout(); return fig
"""),
    _g("barres_rangees_cumul", tl("engine/graphiques_bi.py:g:1096:32"), "composition",
       tl("engine/graphiques_bi.py:g:1097:8"),
       nc_min=3, nc_max=6,
       code=
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, numpy as np
    cat_col = colonnes[0]; val_cols = colonnes[1:]
    df = dataframe[[cat_col] + val_cols].copy()
    for c in val_cols: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df = df.groupby(cat_col, as_index=False)[val_cols].sum()
    x = np.arange(len(df)); w = 0.8/len(val_cols)
    fig, ax1 = plt.subplots(figsize=(7, 4))
    cmap = plt.cm.get_cmap("tab10", len(val_cols))
    for i, col in enumerate(val_cols):
        ax1.bar(x+i*w-0.4+w/2, df[col], width=w, label=col, color=cmap(i))
    ax1.set_xticks(x); ax1.set_xticklabels(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), rotation=45)
    ax2 = ax1.twinx()
    gy_trace_degrade(ax2, x, df[val_cols].sum(axis=1).cumsum(), "#333333", marker="o", linestyle="--", label="Cumulé")   # cumul = évolution
    ax1.legend(loc="upper left", fontsize=7); ax2.legend(loc="upper right", fontsize=7)
    fig.tight_layout(); return fig
"""),
    _g1("rosace", "Rosace (camembert radial)", "composition",
        tl("engine/graphiques_bi.py:g:1120:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, numpy as np
    col = colonnes[0]
    vc = dataframe[col].dropna().astype(str).value_counts().head(12)
    if vc.empty: return plt.figure()
    N = len(vc); angles = np.linspace(0, 2*np.pi, N, endpoint=False)
    vals = vc.values / vc.values.max()
    fig, ax = plt.subplots(figsize=(5, 5), subplot_kw=dict(polar=True))
    ax.bar(angles, vals, width=2*np.pi/N*0.85, alpha=0.8, color=plt.cm.tab20(np.linspace(0,1,N)))
    ax.set_xticks(angles); ax.set_xticklabels(vc.index, fontsize=7); ax.set_yticks([])
    fig.tight_layout(); return fig
"""),
    _g1("sparkline", "Sparkline (mini-courbe)", "tendance",
        tl("engine/graphiques_bi.py:g:1136:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    col = colonnes[0]
    s = pd.to_numeric(dataframe[col], errors="coerce").dropna().reset_index(drop=True)
    if s.empty: return plt.figure()
    fig, ax = plt.subplots(figsize=(7, 2))
    gy_trace_degrade(ax, s.index, s, "#4C72B0", lw=1.5)   # évolution : dégradé sombre -> clair
    ax.fill_between(range(len(s)), s, alpha=0.15, color="#4C72B0")
    ax.scatter([s.idxmin(), s.idxmax()], [s.min(), s.max()], color=["#e74c3c","#27ae60"], zorder=5, s=40)
    ax.text(s.idxmin(), s.min(), f" {s.min():.1f}", fontsize=8, color="#e74c3c", va="top")
    ax.text(s.idxmax(), s.max(), f" {s.max():.1f}", fontsize=8, color="#27ae60", va="bottom")
    ax.axis("off"); fig.tight_layout(); return fig
"""),
    _g3("progression_objectif", tl("rg8.msg.002"), "composition",
        tl("engine/graphiques_bi.py:g:1153:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    cat_col, real_col, obj_col = colonnes
    df = dataframe[[cat_col, real_col, obj_col]].copy()
    df[real_col] = pd.to_numeric(df[real_col], errors="coerce")
    df[obj_col] = pd.to_numeric(df[obj_col], errors="coerce")
    df = df.dropna()
    df["_pct"] = (df[real_col] / df[obj_col].replace(0, float("nan")) * 100).clip(0, 150)
    fig, ax = plt.subplots(figsize=(7, max(3, len(df)*0.55)))
    colors = ["#27ae60" if p >= 100 else "#f39c12" if p >= 75 else "#e74c3c" for p in df["_pct"]]
    bars = ax.barh(df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str), df["_pct"], color=colors, height=0.55)
    ax.axvline(100, color="black", linestyle="--", linewidth=0.8)
    for bar, p in zip(bars, df["_pct"]):
        ax.text(bar.get_width()+1, bar.get_y()+bar.get_height()/2, f"{p:.0f}%", va="center", fontsize=8)
    ax.set_xlabel("% objectif"); fig.tight_layout(); return fig
"""),
    _g1("distribution_cumulee", tl("engine/graphiques_bi.py:g:1172:33"), "distribution",
        tl("engine/graphiques_bi.py:g:1173:9"),
tl("engine/graphiques_bi.py:g:1174:1")),
    _g3("comparaison_deux_periodes", tl("engine/graphiques_bi.py:g:1188:38"), "tendance",
        tl("engine/graphiques_bi.py:g:1189:9"),
"""def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    x_col, y_col, g_col = colonnes
    df = dataframe[[x_col, y_col, g_col]].copy()
    df[y_col] = pd.to_numeric(df[y_col], errors="coerce")
    df = df.dropna(subset=[y_col])
    groupes = df[g_col].where(df[g_col].notna(), "(vide)").astype(str).unique()[:2]
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, g in enumerate(groupes):
        sub = df[df[g_col].where(df[g_col].notna(), "(vide)").astype(str) == g].sort_values(x_col)
        gy_trace_degrade(ax, sub[x_col].where(sub[x_col].notna(), "(vide)").astype(str), sub[y_col], ["#4C72B0","#e74c3c"][i], marker="o", label=g)
    ax.tick_params(axis="x", rotation=45); ax.legend()
    fig.tight_layout(); return fig
"""),

    # ═══════════════════ STATISTIQUES DESCRIPTIVES (complément) ═══════════════════
    {
        "slug": "boite_moustaches_groupee",
        "nom": tl("engine/graphiques_bi.py:g:1209:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:1214:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_col = colonnes[0], colonnes[1]
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna()
    groupes = df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str).unique()[:15]
    data = [df[df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str) == g][val_col].values for g in groupes]
    fig, ax = plt.subplots(figsize=(max(5, 0.9 * len(groupes)), 4.5))
    if data:
        ax.boxplot(data, labels=groupes, patch_artist=True,
                   boxprops=dict(facecolor="#4C72B0", alpha=0.6))
    ax.set_xlabel(cat_col); ax.set_ylabel(val_col)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "boites_multiples",
        "nom": tl("engine/graphiques_bi.py:g:1239:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 6,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1244:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    data, labels = [], []
    for col in colonnes:
        serie = pd.to_numeric(dataframe[col], errors="coerce").dropna()
        if len(serie) > 0:
            data.append(serie.values)
            labels.append(col)
    fig, ax = plt.subplots(figsize=(max(5, 1.3 * len(labels)), 4.5))
    if data:
        ax.boxplot(data, labels=labels, patch_artist=True,
                   boxprops=dict(facecolor="#55A868", alpha=0.6))
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "resume_cinq_nombres",
        "nom": tl("engine/graphiques_bi.py:g:1268:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1272:24"),
        "code": tl("engine/graphiques_bi.py:g:1274:17"),
    },
    {
        "slug": "barres_erreur_moyenne",
        "nom": tl("engine/graphiques_bi.py:g:1295:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:1300:24"),
        "code": tl("engine/graphiques_bi.py:g:1302:17"),
    },
    {
        "slug": "histogramme_groupe",
        "nom": tl("engine/graphiques_bi.py:g:1322:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:1327:24"),
        "code": tl("engine/graphiques_bi.py:g:1329:17"),
    },
    {
        "slug": "diagramme_quantile_normal",
        "nom": tl("engine/graphiques_bi.py:g:1360:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1364:24"),
        "code": tl("engine/graphiques_bi.py:g:1366:17"),
    },
    {
        "slug": "nuage_points_par_categorie",
        "nom": tl("engine/graphiques_bi.py:g:1413:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:1418:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_col = colonnes[0], colonnes[1]
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna()
    groupes = df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str).unique()[:15]
    fig, ax = plt.subplots(figsize=(max(5, 0.8 * len(groupes)), 4.5))
    rng = np.random.default_rng(0)
    for i, g in enumerate(groupes):
        vals = df[df[cat_col].where(df[cat_col].notna(), "(vide)").astype(str) == g][val_col].values
        if len(vals) == 0:
            continue
        gigue = rng.uniform(-0.15, 0.15, size=len(vals))
        ax.scatter(np.full(len(vals), i) + gigue, vals, alpha=0.5, s=15, color="#4C72B0")
        ax.plot([i - 0.2, i + 0.2], [vals.mean(), vals.mean()], color="#C44E52", linewidth=2)
    ax.set_xticks(range(len(groupes)))
    ax.set_xticklabels(groupes, rotation=45)
    ax.set_ylabel(val_col)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "barres_asymetrie_aplatissement",
        "nom": tl("engine/graphiques_bi.py:g:1448:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1452:24"),
        "code": tl("engine/graphiques_bi.py:g:1454:17"),
    },
    _g3("matrice_nuages_points", tl("engine/graphiques_bi.py:g:1478:34"), "distribution",
        tl("engine/graphiques_bi.py:g:1479:9"),
        """def dessiner(dataframe, colonnes):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    df = dataframe[list(colonnes)].apply(pd.to_numeric, errors="coerce").dropna()
    n = len(colonnes)
    fig, axes = plt.subplots(n, n, figsize=(2.3 * n, 2.3 * n))
    for i in range(n):
        for j in range(n):
            ax = axes[i][j]
            if i == j:
                ax.hist(df[colonnes[i]], bins=15, color="#8172B2")
            else:
                ax.scatter(df[colonnes[j]], df[colonnes[i]], s=6, alpha=0.5, color="#4C72B0")
            if i == n - 1:
                ax.set_xlabel(colonnes[j], fontsize=8)
            if j == 0:
                ax.set_ylabel(colonnes[i], fontsize=8)
            ax.tick_params(labelsize=6)
    fig.tight_layout()
    return fig
""", types=TYPES_NUMERIQUES),
    {
        "slug": "coefficient_variation_barres",
        "nom": tl("engine/graphiques_bi.py:g:1504:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:1509:24"),
        "code": tl("engine/graphiques_bi.py:g:1511:17"),
    },

    # ═══════════════ STATISTIQUES DESCRIPTIVES VISUELLES (dix nouveaux) ═══════════
    # Ces graphiques se limitent à des statistiques descriptives de base (moyenne,
    # médiane, mode, écart-type, quartiles, étendue...) affichées directement sur
    # le graphique lui-même : aucun test d'hypothèse ni formule inférentielle
    # (contrairement à la section ANOVA plus bas).
    {
        "slug": "fiche_resume_statistique",
        "nom": tl("engine/graphiques_bi.py:g:1539:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1543:24"),
        "code": tl("engine/graphiques_bi.py:g:1545:17"),
    },
    {
        "slug": "histogramme_reperes_centraux",
        "nom": tl("engine/graphiques_bi.py:g:1596:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1600:24"),
        "code": tl("engine/graphiques_bi.py:g:1602:17"),
    },
    {
        "slug": "bandes_ecart_type",
        "nom": tl("engine/graphiques_bi.py:g:1629:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1633:24"),
        "code": tl("engine/graphiques_bi.py:g:1635:17"),
    },
    {
        "slug": "comparaison_tendance_centrale",
        "nom": tl("engine/graphiques_bi.py:g:1665:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1669:24"),
        "code": tl("engine/graphiques_bi.py:g:1671:17"),
    },
    {
        "slug": "amplitude_par_groupe",
        "nom": tl("engine/graphiques_bi.py:g:1698:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:1703:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    cat_col, val_col = colonnes[0], colonnes[1]
    df = dataframe[[cat_col, val_col]].copy()
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna()
    stats = df.groupby(cat_col)[val_col].agg(["min", "max", "mean"])
    fig, ax = plt.subplots(figsize=(7, max(3, 0.5 * len(stats))))
    if stats.empty:
        fig.tight_layout()
        return fig
    positions = np.arange(len(stats))
    ax.hlines(positions, stats["min"], stats["max"], color="#4C72B0", linewidth=6, alpha=0.5)
    ax.scatter(stats["mean"], positions, color="#C44E52", zorder=5, label="Moyenne", s=50)
    ax.scatter(stats["min"], positions, color="#333333", zorder=5, s=20)
    ax.scatter(stats["max"], positions, color="#333333", zorder=5, s=20)
    ax.set_yticks(positions)
    ax.set_yticklabels(stats.index.astype(str))
    ax.set_xlabel(val_col)
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "diagramme_tige_feuille",
        "nom": "Diagramme tige-feuille (stem-and-leaf)",
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1738:24"),
        "code": tl("engine/graphiques_bi.py:g:1740:17"),
    },
    {
        "slug": "diagramme_dispersion_centree",
        "nom": tl("engine/graphiques_bi.py:g:1773:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1777:24"),
        "code": tl("engine/graphiques_bi.py:g:1779:17"),
    },
    {
        "slug": "diagramme_position_valeurs",
        "nom": tl("engine/graphiques_bi.py:g:1808:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1812:24"),
        "code": tl("engine/graphiques_bi.py:g:1814:17"),
    },
    {
        "slug": "petits_multiples_histogrammes",
        "nom": tl("engine/graphiques_bi.py:g:1842:16"),
        "categorie": "distribution",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 6,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1847:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    n = len(colonnes)
    fig, axes = plt.subplots(1, n, figsize=(3.2 * n, 3.2))
    if n == 1:
        axes = [axes]
    for ax, col in zip(axes, colonnes):
        serie = pd.to_numeric(dataframe[col], errors="coerce").dropna()
        if serie.empty:
            ax.axis("off")
            continue
        ax.hist(serie, bins=min(20, max(5, int(len(serie) ** 0.5))), color="#4C72B0", alpha=0.75, edgecolor="white")
        ax.axvline(serie.mean(), color="#C44E52", linestyle="--", linewidth=1.5)
        ax.set_title(col, fontsize=9)
        ax.tick_params(labelsize=7)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "carte_valeurs_atypiques",
        "nom": tl("engine/graphiques_bi.py:g:1873:16"),
        "categorie": "distribution",
        "cible_type": "colonne_unique",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1877:24"),
        "code": tl("engine/graphiques_bi.py:g:1879:17"),
    },

    # ═══════════════════ ANALYSE MULTIVARIÉE (ACP / AFC / ACM) ═══════════════════
    {
        "slug": "acp_eboulis",
        "nom": tl("engine/graphiques_bi.py:g:1913:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 8,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1918:24"),
        "code": tl("engine/graphiques_bi.py:g:1920:17"),
    },
    {
        "slug": "acp_individus",
        "nom": tl("engine/graphiques_bi.py:g:1952:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 8,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1957:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    df = dataframe[list(colonnes)].apply(pd.to_numeric, errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=(6, 6))
    if len(df) < 3 or df.shape[1] < 2:
        fig.tight_layout()
        return fig
    ecarts = df.std(ddof=0).replace(0, 1)
    X = (df - df.mean()) / ecarts
    corr = np.corrcoef(X.values, rowvar=False)
    valeurs_propres, vecteurs = np.linalg.eigh(corr)
    ordre = np.argsort(valeurs_propres)[::-1]
    valeurs_propres, vecteurs = valeurs_propres[ordre], vecteurs[:, ordre]
    coords = X.values @ vecteurs[:, :2]
    total = np.clip(valeurs_propres, 0, None).sum()
    inertie = np.clip(valeurs_propres, 0, None) / total * 100 if total > 0 else valeurs_propres * 0
    ax.scatter(coords[:, 0], coords[:, 1], s=18, alpha=0.6, color="#4C72B0")
    ax.axhline(0, color="#888888", linewidth=0.6)
    ax.axvline(0, color="#888888", linewidth=0.6)
    ax.set_xlabel(f"Axe 1 ({inertie[0]:.1f}%)")
    ax.set_ylabel(f"Axe 2 ({inertie[1]:.1f}%)" if len(inertie) > 1 else "Axe 2")
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "acp_cercle_correlation",
        "nom": tl("engine/graphiques_bi.py:g:1989:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 8,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:1994:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    df = dataframe[list(colonnes)].apply(pd.to_numeric, errors="coerce").dropna()
    fig, ax = plt.subplots(figsize=(6, 6))
    if len(df) < 3 or df.shape[1] < 2:
        fig.tight_layout()
        return fig
    ecarts = df.std(ddof=0).replace(0, 1)
    X = (df - df.mean()) / ecarts
    corr = np.corrcoef(X.values, rowvar=False)
    valeurs_propres, vecteurs = np.linalg.eigh(corr)
    ordre = np.argsort(valeurs_propres)[::-1]
    valeurs_propres, vecteurs = valeurs_propres[ordre], vecteurs[:, ordre]
    racines = np.sqrt(np.clip(valeurs_propres[:2], 0, None))
    loadings = vecteurs[:, :2] * racines
    theta = np.linspace(0, 2 * np.pi, 200)
    ax.plot(np.cos(theta), np.sin(theta), color="#888888", linewidth=0.8)
    ax.axhline(0, color="#888888", linewidth=0.6)
    ax.axvline(0, color="#888888", linewidth=0.6)
    for i, col in enumerate(colonnes):
        x, y = loadings[i, 0], loadings[i, 1]
        ax.annotate("", xy=(x, y), xytext=(0, 0), arrowprops=dict(arrowstyle="->", color="#4C72B0"))
        ax.text(x * 1.08, y * 1.08, col, fontsize=8, ha="center", color="#C44E52")
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)
    ax.set_aspect("equal")
    ax.set_xlabel("Axe 1"); ax.set_ylabel("Axe 2")
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "acp_contribution_variables",
        "nom": tl("engine/graphiques_bi.py:g:2032:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 8,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:2037:24"),
        "code": tl("engine/graphiques_bi.py:g:2039:17"),
    },
    {
        "slug": "matrice_correlation_cercles",
        "nom": tl("engine/graphiques_bi.py:g:2069:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 10,
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/graphiques_bi.py:g:2074:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    df = dataframe[list(colonnes)].apply(pd.to_numeric, errors="coerce")
    corr = df.corr().values
    n = len(colonnes)
    fig, ax = plt.subplots(figsize=(max(4, 0.7 * n) + 1, max(4, 0.7 * n) + 1))
    cmap = plt.get_cmap("coolwarm")
    for i in range(n):
        for j in range(n):
            r = corr[i, j] if not np.isnan(corr[i, j]) else 0
            couleur = cmap((r + 1) / 2)
            taille = abs(r) * 500
            ax.scatter(j, n - 1 - i, s=taille, color=couleur, edgecolors="#222222", linewidths=0.4)
    ax.set_xticks(range(n)); ax.set_xticklabels(colonnes, rotation=45, ha="right")
    ax.set_yticks(range(n)); ax.set_yticklabels(list(colonnes)[::-1])
    ax.set_xlim(-0.5, n - 0.5); ax.set_ylim(-0.5, n - 0.5)
    ax.set_aspect("equal")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(-1, 1))
    sm.set_array([])
    fig.colorbar(sm, ax=ax, shrink=0.8)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "afc_nuage_modalites",
        "nom": tl("engine/graphiques_bi.py:g:2105:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/graphiques_bi.py:g:2110:24"),
        "code": """def dessiner(dataframe, colonnes):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import numpy as np
    col1, col2 = colonnes[0], colonnes[1]
    df = dataframe[[col1, col2]].dropna().astype(str)
    fig, ax = plt.subplots(figsize=(6.5, 6))
    table = pd.crosstab(df[col1], df[col2])
    if table.shape[0] < 2 or table.shape[1] < 2 or table.values.sum() == 0:
        fig.tight_layout()
        return fig
    N = table.values.astype(float)
    total = N.sum()
    P = N / total
    r = P.sum(axis=1); c = P.sum(axis=0)
    r_safe = np.where(r == 0, 1, r); c_safe = np.where(c == 0, 1, c)
    E = np.outer(r, c)
    S = (P - E) / np.sqrt(np.outer(r_safe, c_safe))
    U, D, Vt = np.linalg.svd(S, full_matrices=False)
    inertie_totale = (D ** 2).sum()
    inertie = (D ** 2) / inertie_totale * 100 if inertie_totale > 0 else D * 0
    coords_lignes = (U * D) / np.sqrt(r_safe)[:, None]
    coords_colonnes = (Vt.T * D) / np.sqrt(c_safe)[:, None]
    if coords_lignes.shape[1] < 2:
        coords_lignes = np.pad(coords_lignes, ((0, 0), (0, 2 - coords_lignes.shape[1])))
        coords_colonnes = np.pad(coords_colonnes, ((0, 0), (0, 2 - coords_colonnes.shape[1])))
        inertie = np.pad(inertie, (0, 2 - len(inertie)))
    ax.scatter(coords_lignes[:, 0], coords_lignes[:, 1], color="#4C72B0", s=40, label=col1)
    for i, nom in enumerate(table.index):
        ax.annotate(str(nom), (coords_lignes[i, 0], coords_lignes[i, 1]), fontsize=8, color="#4C72B0")
    ax.scatter(coords_colonnes[:, 0], coords_colonnes[:, 1], color="#C44E52", marker="^", s=40, label=col2)
    for j, nom in enumerate(table.columns):
        ax.annotate(str(nom), (coords_colonnes[j, 0], coords_colonnes[j, 1]), fontsize=8, color="#C44E52")
    ax.axhline(0, color="#888888", linewidth=0.6)
    ax.axvline(0, color="#888888", linewidth=0.6)
    ax.set_xlabel(f"Axe 1 ({inertie[0]:.1f}%)")
    ax.set_ylabel(f"Axe 2 ({inertie[1]:.1f}%)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig
""",
    },
    {
        "slug": "acm_modalites",
        "nom": tl("engine/graphiques_bi.py:g:2157:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 6,
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/graphiques_bi.py:g:2162:24"),
        "code": tl("engine/graphiques_bi.py:g:2164:17"),
    },
    {
        "slug": "acm_valeurs_propres",
        "nom": tl("engine/graphiques_bi.py:g:2214:16"),
        "categorie": "relation",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 6,
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/graphiques_bi.py:g:2219:24"),
        "code": tl("engine/graphiques_bi.py:g:2221:17"),
    },
    {
        "slug": "anova_comparaison_moyennes",
        "nom": tl("engine/graphiques_bi.py:g:2261:16"),
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:2266:24"),
        "code": tl("engine/graphiques_bi.py:g:2268:17"),
    },
    {
        "slug": "anova_boxplot_test",
        "nom": tl("engine/graphiques_bi.py:g:2302:16"),
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:2307:24"),
        "code": tl("engine/graphiques_bi.py:g:2309:17"),
    },
    {
        "slug": "anova_residus_ajustees",
        "nom": tl("engine/graphiques_bi.py:g:2342:16"),
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:2347:24"),
        "code": tl("engine/graphiques_bi.py:g:2349:17"),
    },
    {
        "slug": "anova_qqplot_residus",
        "nom": tl("engine/graphiques_bi.py:g:2376:16"),
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 2, "nombre_colonnes_max": 2,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:2381:24"),
        "code": tl("engine/graphiques_bi.py:g:2383:17"),
    },
    {
        "slug": "anova_interaction",
        "nom": tl("engine/graphiques_bi.py:g:2445:16"),
        "categorie": "comparaison",
        "cible_type": "colonnes_multiples",
        "nombre_colonnes_min": 3, "nombre_colonnes_max": 3,
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/graphiques_bi.py:g:2450:24"),
        "code": tl("engine/graphiques_bi.py:g:2452:17"),
    },
]



# 50 graphiques « data science » (qualité, distributions, régression, séries, segmentation, anomalies,
# finance, comparaison statistique) — voir engine/graphiques_science.py.
from .graphiques_science import GRAPHIQUES_SCIENCE  # noqa: E402
GRAPHIQUES_BI_PREDEFINIS += GRAPHIQUES_SCIENCE


def synchroniser_graphiques_bi_predefinis():
    """Crée ou met à jour (code, description, catégorie) les graphiques BI
    prédéfinis (entreprise=None, predefini=True) en base. Idempotent : à
    appeler paresseusement au premier accès à la liste (voir
    views.api_graphiques_bi), même pattern que synchroniser_formules_predefinies()."""
    from .catalogue_sync import synchroniser_catalogue
    from .models import GraphiqueBI

    synchroniser_catalogue(GraphiqueBI, "graphiques_bi", GRAPHIQUES_BI_PREDEFINIS, lambda g: {
        "nom": g["nom"], "categorie": g["categorie"], "cible_type": g["cible_type"],
        "nombre_colonnes_min": g.get("nombre_colonnes_min", 1), "nombre_colonnes_max": g.get("nombre_colonnes_max"),
        "types_cibles_compatibles": g["types_cibles_compatibles"], "description": g["description"],
        "code": g["code"], "predefini": True,
    })
