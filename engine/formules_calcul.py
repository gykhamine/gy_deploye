"""
Bibliothèque de formules prédéfinies pour les tables de calcul (voir
models.FormuleCalcul / TableCalcul / LigneCalcul).

Chaque formule est un script Python définissant `def formule(cible): ...`,
exécuté dans le même bac à sable que les scripts d'analyse (voir
executor.executer_formule_securisee). Les formules "colonne" reçoivent une
pandas.Series ; les formules "table" reçoivent un pandas.DataFrame (toutes
les colonnes de la plage de lignes ciblée) et repèrent elles-mêmes les
colonnes qui les intéressent (ex: une colonne date + une colonne numérique).

`synchroniser_formules_predefinies()` est appelé au démarrage (voir apps.py)
pour créer/mettre à jour ces formules en base (entreprise=None). Une formule
prédéfinie déjà modifiée manuellement par une entreprise n'est jamais
écrasée : seules les formules avec entreprise=None et predefinie=True sont
synchronisées ici.
"""
from engine.i18n import tf
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tl

# Types de champ EAV regroupés par compatibilité (voir ChampDynamique.TYPE_CHOICES).
from .formules_science import TYPES_NUMERIQUES  # source unique (aussi utilisée par fusion_cellule)
TYPES_TEXTE = "texte,texte_long,email,url,telephone,choix_unique,choix_multiple"
TYPES_TOUS = ""  # vide = tous types acceptés
# Groupes supplémentaires (voir executor._cast_serie pour le dtype pandas
# obtenu après casting : datetime64 pour date/datetime, timedelta64 pour
# duree, boolean nullable pour booleen/switch, object[list] pour
# choix_multiple, object[list JSON] pour vecteur/matrice).
TYPES_DATE = "date,datetime"
TYPES_DUREE = "duree"
TYPES_BOOLEEN = "booleen,switch"
TYPES_CHOIX_MULTIPLE = "choix_multiple"
TYPES_CATEGORIEL = "choix_unique,couleur,texte"
TYPES_TENSEUR = "vecteur,matrice"


FORMULES_PREDEFINIES = [
    # ── Agrégats numériques ──────────────────────────────────────────
    {
        "slug": "somme",
        "nom": tl("engine/formules_calcul.py:g:39:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:43:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce')\n"
            "    return float(np.nansum(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "moyenne",
        "nom": tl("engine/formules_calcul.py:g:54:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:58:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(np.mean(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "mediane",
        "nom": tl("engine/formules_calcul.py:g:71:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:75:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(np.median(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "minimum",
        "nom": tl("engine/formules_calcul.py:g:88:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:92:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(serie.min())\n"
        ),
    },
    {
        "slug": "maximum",
        "nom": tl("engine/formules_calcul.py:g:104:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:108:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(serie.max())\n"
        ),
    },
    {
        "slug": "ecart_type",
        "nom": _gtfr("engine/formules_calcul.py:122:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:124:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if len(serie) < 2:\n"
            "        return None\n"
            "    return float(np.std(serie.to_numpy(dtype='float64'), ddof=1))\n"
        ),
    },
    {
        "slug": "variance",
        "nom": tl("engine/formules_calcul.py:g:137:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:141:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if len(serie) < 2:\n"
            "        return None\n"
            "    return float(np.var(serie.to_numpy(dtype='float64'), ddof=1))\n"
        ),
    },
    # ── Statistiques génériques (tous types) ────────────────────────
    {
        "slug": "nombre_valeurs",
        "nom": tl("engine/formules_calcul.py:g:155:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:159:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible)\n"
            "    return int(serie.notna().sum())\n"
        ),
    },
    {
        "slug": "taux_remplissage",
        "nom": tl("engine/formules_calcul.py:g:169:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:173:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible)\n"
            "    if len(serie) == 0:\n"
            "        return None\n"
            "    return round(float(serie.notna().sum()) / len(serie) * 100, 2)\n"
        ),
    },
    {
        "slug": "valeurs_uniques",
        "nom": tl("engine/formules_calcul.py:g:185:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:189:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    return int(serie.astype(str).nunique())\n"
        ),
    },
    {
        "slug": "mode_frequent",
        "nom": tl("engine/formules_calcul.py:g:199:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:203:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return serie.astype(str).mode().iloc[0]\n"
        ),
    },
    # ── Texte ────────────────────────────────────────────────────────
    {
        "slug": "concatenation",
        "nom": tl("engine/formules_calcul.py:g:216:16"),
        "categorie": "texte",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TEXTE,
        "description": tl("engine/formules_calcul.py:g:220:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    return ', '.join(str(v) for v in serie.tolist())\n"
        ),
    },
    # ── Temporel / série (cible_type='table' : la formule repère elle-même
    #   les colonnes date et numérique dans le DataFrame reçu) ──────────
    {
        "slug": "tendance_lineaire",
        "nom": tl("engine/formules_calcul.py:g:232:16"),
        "categorie": "temporel",
        "cible_type": "table",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": (
            tl("engine/formules_calcul.py:g:237:13")
        ),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    df = pd.DataFrame(cible)\n"
            "    colonnes_num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    if not colonnes_num:\n"
            "        return None\n"
            "    serie = pd.to_numeric(df[colonnes_num[0]], errors='coerce').dropna()\n"
            "    if len(serie) < 2:\n"
            "        return None\n"
            "    x = np.arange(len(serie), dtype='float64')\n"
            "    y = serie.to_numpy(dtype='float64')\n"
            "    resultat = stats.linregress(x, y)\n"
            "    return {\n"
            "        'colonne': colonnes_num[0],\n"
            "        'pente': float(resultat.slope),\n"
            "        'ordonnee_origine': float(resultat.intercept),\n"
            "        'r2': float(resultat.rvalue ** 2),\n"
            "    }\n"
        ),
    },
    {
        "slug": "prevision_prophet",
        "nom": tl("engine/formules_calcul.py:g:266:16"),
        "categorie": "temporel",
        "cible_type": "table",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": (
            tl("engine/formules_calcul.py:g:271:13")
        ),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from prophet import Prophet\n"
            "    df = pd.DataFrame(cible)\n"
            "    colonnes_date = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]\n"
            "    colonnes_num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    if not colonnes_date or not colonnes_num:\n"
            "        return None\n"
            "    entrainement = pd.DataFrame({\n"
            "        'ds': pd.to_datetime(df[colonnes_date[0]]),\n"
            "        'y': pd.to_numeric(df[colonnes_num[0]], errors='coerce'),\n"
            "    }).dropna()\n"
            "    if len(entrainement) < 3:\n"
            "        return None\n"
            "    modele = Prophet()\n"
            "    modele.fit(entrainement)\n"
            "    futur = modele.make_future_dataframe(periods=7)\n"
            "    prevision = modele.predict(futur).tail(7)\n"
            "    return [\n"
            "        {'date': str(row.ds.date()), 'valeur_prevue': float(row.yhat)}\n"
            "        for row in prevision.itertuples()\n"
            "    ]\n"
        ),
    },
    # ── Agrégats numériques (suite) ──────────────────────────────────
    {
        "slug": "somme_cumulee",
        "nom": tl("engine/formules_calcul.py:g:303:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:307:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce')\n"
            "    return [float(v) for v in np.nancumsum(serie.to_numpy(dtype='float64'))]\n"
        ),
    },
    {
        "slug": "produit",
        "nom": "Produit",
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:322:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(np.prod(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "nombre_valeurs_positives",
        "nom": tl("engine/formules_calcul.py:g:335:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:339:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    return int((serie > 0).sum())\n"
        ),
    },
    {
        "slug": "nombre_valeurs_negatives",
        "nom": tl("engine/formules_calcul.py:g:349:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:353:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    return int((serie < 0).sum())\n"
        ),
    },
    {
        "slug": "nombre_zeros",
        "nom": tl("engine/formules_calcul.py:g:363:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:367:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    return int((serie == 0).sum())\n"
        ),
    },
    {
        "slug": "somme_valeurs_positives",
        "nom": tl("engine/formules_calcul.py:g:377:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:381:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    positives = serie[serie > 0]\n"
            "    if positives.empty:\n"
            "        return 0.0\n"
            "    return float(np.sum(positives.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "somme_valeurs_negatives",
        "nom": tl("engine/formules_calcul.py:g:395:16"),
        "categorie": "agregat",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:399:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    negatives = serie[serie < 0]\n"
            "    if negatives.empty:\n"
            "        return 0.0\n"
            "    return float(np.sum(negatives.to_numpy(dtype='float64')))\n"
        ),
    },
    # ── Statistiques (suite) ──────────────────────────────────────────
    {
        "slug": "etendue",
        "nom": tl("engine/formules_calcul.py:g:414:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:418:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(serie.max() - serie.min())\n"
        ),
    },
    {
        "slug": "quartile_1",
        "nom": "1er quartile (Q1)",
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:434:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(np.percentile(serie.to_numpy(dtype='float64'), 25))\n"
        ),
    },
    {
        "slug": "quartile_3",
        "nom": "3e quartile (Q3)",
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:451:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(np.percentile(serie.to_numpy(dtype='float64'), 75))\n"
        ),
    },
    {
        "slug": "ecart_interquartile",
        "nom": tl("engine/formules_calcul.py:g:464:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:468:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    valeurs = serie.to_numpy(dtype='float64')\n"
            "    q1, q3 = np.percentile(valeurs, [25, 75])\n"
            "    return float(q3 - q1)\n"
        ),
    },
    {
        "slug": "coefficient_variation",
        "nom": tl("engine/formules_calcul.py:g:483:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:487:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if len(serie) < 2:\n"
            "        return None\n"
            "    valeurs = serie.to_numpy(dtype='float64')\n"
            "    moyenne = np.mean(valeurs)\n"
            "    if moyenne == 0:\n"
            "        return None\n"
            "    return round(float(np.std(valeurs, ddof=1) / abs(moyenne) * 100), 2)\n"
        ),
    },
    {
        "slug": "asymetrie",
        "nom": tl("engine/formules_calcul.py:g:504:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:508:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if len(serie) < 3:\n"
            "        return None\n"
            "    return float(stats.skew(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "aplatissement",
        "nom": "Aplatissement (kurtosis)",
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:525:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if len(serie) < 4:\n"
            "        return None\n"
            "    return float(stats.kurtosis(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "moyenne_geometrique",
        "nom": tl("engine/formules_calcul.py:g:538:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:542:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    serie = serie[serie > 0]\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(stats.gmean(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "moyenne_harmonique",
        "nom": tl("engine/formules_calcul.py:g:556:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:560:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    serie = serie[serie > 0]\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return float(stats.hmean(serie.to_numpy(dtype='float64')))\n"
        ),
    },
    {
        "slug": "nombre_valeurs_manquantes",
        "nom": tl("engine/formules_calcul.py:g:574:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:578:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible)\n"
            "    return int(serie.isna().sum())\n"
        ),
    },
    {
        "slug": "taux_valeurs_distinctes",
        "nom": tl("engine/formules_calcul.py:g:588:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:592:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return round(float(serie.astype(str).nunique()) / len(serie) * 100, 2)\n"
        ),
    },
    {
        "slug": "premiere_valeur",
        "nom": tl("engine/formules_calcul.py:g:604:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:608:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return serie.iloc[0]\n"
        ),
    },
    {
        "slug": "derniere_valeur",
        "nom": tl("engine/formules_calcul.py:g:620:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:624:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return serie.iloc[-1]\n"
        ),
    },
    {
        "slug": "nombre_valeurs_aberrantes",
        "nom": tl("engine/formules_calcul.py:g:636:16"),
        "categorie": "statistique",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": (
            tl("engine/formules_calcul.py:g:641:13")
        ),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if len(serie) < 4:\n"
            "        return 0\n"
            "    valeurs = serie.to_numpy(dtype='float64')\n"
            "    q1, q3 = np.percentile(valeurs, [25, 75])\n"
            "    iqr = q3 - q1\n"
            "    borne_basse = q1 - 1.5 * iqr\n"
            "    borne_haute = q3 + 1.5 * iqr\n"
            "    return int(((valeurs < borne_basse) | (valeurs > borne_haute)).sum())\n"
        ),
    },
    # ── Texte (suite) ────────────────────────────────────────────────
    {
        "slug": "longueur_moyenne_texte",
        "nom": tl("engine/formules_calcul.py:g:662:16"),
        "categorie": "texte",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TEXTE,
        "description": tl("engine/formules_calcul.py:g:666:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna().astype(str)\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return round(float(serie.str.len().mean()), 2)\n"
        ),
    },
    {
        "slug": "nombre_mots_total",
        "nom": tl("engine/formules_calcul.py:g:678:16"),
        "categorie": "texte",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TEXTE,
        "description": tl("engine/formules_calcul.py:g:682:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna().astype(str)\n"
            "    return int(serie.str.split().str.len().sum())\n"
        ),
    },
    # ── Temporel / série (suite) ───────────────────────────────────────
    {
        "slug": "variation_pourcentage",
        "nom": tl("engine/formules_calcul.py:g:693:16"),
        "categorie": "temporel",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:697:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
            "    if len(serie) < 2 or serie.iloc[0] == 0:\n"
            "        return None\n"
            "    return round(float((serie.iloc[-1] - serie.iloc[0]) / abs(serie.iloc[0]) * 100), 2)\n"
        ),
    },
    {
        "slug": "moyenne_mobile",
        "nom": tl("engine/formules_calcul.py:g:709:16"),
        "categorie": "temporel",
        "cible_type": "table",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": (
            tl("engine/formules_calcul.py:g:714:13")
        ),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    df = pd.DataFrame(cible)\n"
            "    colonnes_num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    if not colonnes_num:\n"
            "        return None\n"
            "    serie = pd.to_numeric(df[colonnes_num[0]], errors='coerce')\n"
            "    mobile = serie.rolling(window=3, min_periods=1).mean()\n"
            "    return [None if pd.isna(v) else float(v) for v in mobile.tolist()]\n"
        ),
    },
    # ── Dates / datetime ────────────────────────────────────────────────
    {
        "slug": "date_min",
        "nom": tl("engine/formules_calcul.py:g:732:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:736:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return str(serie.min())\n"
        ),
    },
    {
        "slug": "date_max",
        "nom": tl("engine/formules_calcul.py:g:748:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:752:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return str(serie.max())\n"
        ),
    },
    {
        "slug": "etendue_jours",
        "nom": tl("engine/formules_calcul.py:g:764:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:768:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return int((serie.max() - serie.min()).days)\n"
        ),
    },
    {
        "slug": "jour_semaine_frequent",
        "nom": tl("engine/formules_calcul.py:g:780:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:784:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    jours = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']\n"
            "    indice = int(serie.dt.dayofweek.mode().iloc[0])\n"
            "    return jours[indice]\n"
        ),
    },
    {
        "slug": "nombre_dates_futures",
        "nom": tl("engine/formules_calcul.py:g:798:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:802:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    return int((serie > pd.Timestamp.now()).sum())\n"
        ),
    },
    {
        "slug": "nombre_dates_passees",
        "nom": tl("engine/formules_calcul.py:g:812:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:816:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    return int((serie < pd.Timestamp.now()).sum())\n"
        ),
    },
    {
        "slug": "age_moyen_jours",
        "nom": tl("engine/formules_calcul.py:g:826:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:830:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    ecarts = (pd.Timestamp.now() - serie).dt.days\n"
            "    return round(float(ecarts.mean()), 2)\n"
        ),
    },
    {
        "slug": "jours_ouvres_total",
        "nom": tl("engine/formules_calcul.py:g:843:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:847:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    return int((serie.dt.dayofweek < 5).sum())\n"
        ),
    },
    {
        "slug": "mois_plus_frequent",
        "nom": tl("engine/formules_calcul.py:g:857:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:861:24"),
        "code": (
            tl("engine/formules_calcul.py:g:858:13")
        ),
    },
    {
        "slug": "nombre_jours_uniques",
        "nom": tl("engine/formules_calcul.py:g:876:16"),
        "categorie": "date",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DATE,
        "description": tl("engine/formules_calcul.py:g:880:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_datetime(pd.Series(cible), errors='coerce').dropna()\n"
            "    return int(serie.dt.date.astype(str).nunique())\n"
        ),
    },
    # ── Durée ────────────────────────────────────────────────────────
    {
        "slug": "somme_duree",
        "nom": tl("engine/formules_calcul.py:g:891:16"),
        "categorie": "duree",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DUREE,
        "description": tl("engine/formules_calcul.py:g:895:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_timedelta(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return round(float(serie.sum().total_seconds() / 3600), 2)\n"
        ),
    },
    {
        "slug": "duree_moyenne",
        "nom": tl("engine/formules_calcul.py:g:907:16"),
        "categorie": "duree",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_DUREE,
        "description": tl("engine/formules_calcul.py:g:911:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.to_timedelta(pd.Series(cible), errors='coerce').dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return round(float(serie.mean().total_seconds() / 3600), 2)\n"
        ),
    },
    # ── Booléen / switch ────────────────────────────────────────────────
    {
        "slug": "nombre_vrai",
        "nom": tl("engine/formules_calcul.py:g:924:16"),
        "categorie": "booleen",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_BOOLEEN,
        "description": tl("engine/formules_calcul.py:g:928:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible)\n"
            "    return int((serie == True).sum())\n"
        ),
    },
    {
        "slug": "nombre_faux",
        "nom": tl("engine/formules_calcul.py:g:938:16"),
        "categorie": "booleen",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_BOOLEEN,
        "description": tl("engine/formules_calcul.py:g:942:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible)\n"
            "    return int((serie == False).sum())\n"
        ),
    },
    {
        "slug": "taux_vrai",
        "nom": tl("engine/formules_calcul.py:g:952:16"),
        "categorie": "booleen",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_BOOLEEN,
        "description": tl("engine/formules_calcul.py:g:956:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return round(float((serie == True).sum()) / len(serie) * 100, 2)\n"
        ),
    },
    # ── Choix multiple ──────────────────────────────────────────────────
    {
        "slug": "frequence_options",
        "nom": tl("engine/formules_calcul.py:g:969:16"),
        "categorie": "categoriel",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_CHOIX_MULTIPLE,
        "description": tl("engine/formules_calcul.py:g:973:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from collections import Counter\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    compteur = Counter()\n"
            "    for options in serie:\n"
            "        if isinstance(options, (list, tuple)):\n"
            "            compteur.update(options)\n"
            "        elif options:\n"
            "            compteur.update(str(options).split(','))\n"
            "    if not compteur:\n"
            "        return None\n"
            "    return dict(compteur)\n"
        ),
    },
    {
        "slug": "nombre_options_moyen",
        "nom": tl("engine/formules_calcul.py:g:992:16"),
        "categorie": "categoriel",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_CHOIX_MULTIPLE,
        "description": tl("engine/formules_calcul.py:g:996:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    tailles = serie.map(lambda v: len(v) if isinstance(v, (list, tuple)) else len(str(v).split(',')) if v else 0)\n"
            "    return round(float(tailles.mean()), 2)\n"
        ),
    },
    # ── Catégoriel (couleur / choix unique / texte court) ────────────────
    {
        "slug": "repartition_categories",
        "nom": tl("engine/formules_calcul.py:g:1010:16"),
        "categorie": "categoriel",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_CATEGORIEL,
        "description": tl("engine/formules_calcul.py:g:1014:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna().astype(str)\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return serie.value_counts().to_dict()\n"
        ),
    },
    {
        "slug": "couleur_dominante",
        "nom": "Couleur dominante",
        "categorie": "categoriel",
        "cible_type": "colonne",
        "types_cibles_compatibles": "couleur",
        "description": tl("engine/formules_calcul.py:g:1030:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna().astype(str)\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return serie.mode().iloc[0]\n"
        ),
    },
    # ── Vecteur / matrice (module IA) ────────────────────────────────────
    {
        "slug": "norme_moyenne_vecteur",
        "nom": tl("engine/formules_calcul.py:g:1043:16"),
        "categorie": "tenseur",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TENSEUR,
        "description": tl("engine/formules_calcul.py:g:1047:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    normes = [float(np.linalg.norm(np.asarray(v, dtype='float64'))) for v in serie]\n"
            "    return round(float(np.mean(normes)), 4)\n"
        ),
    },
    {
        "slug": "dimension_vecteur",
        "nom": tl("engine/formules_calcul.py:g:1061:16"),
        "categorie": "tenseur",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TENSEUR,
        "description": tl("engine/formules_calcul.py:g:1065:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    return list(np.asarray(serie.iloc[0], dtype='float64').shape)\n"
        ),
    },
    {
        "slug": "somme_totale_tenseur",
        "nom": tl("engine/formules_calcul.py:g:1078:16"),
        "categorie": "tenseur",
        "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_TENSEUR,
        "description": tl("engine/formules_calcul.py:g:1082:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    serie = pd.Series(cible).dropna()\n"
            "    if serie.empty:\n"
            "        return None\n"
            "    total = sum(float(np.sum(np.asarray(v, dtype='float64'))) for v in serie)\n"
            "    return round(total, 4)\n"
        ),
    },
    # ── Table (deux colonnes ou plus) ────────────────────────────────────
    {
        "slug": "correlation_pearson",
        "nom": tl("engine/formules_calcul.py:g:1097:16"),
        "categorie": "statistique",
        "cible_type": "table",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:1101:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    df = pd.DataFrame(cible)\n"
            "    colonnes_num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    if len(colonnes_num) < 2:\n"
            "        return None\n"
            "    paire = df[[colonnes_num[0], colonnes_num[1]]].apply(pd.to_numeric, errors='coerce').dropna()\n"
            "    if len(paire) < 2:\n"
            "        return None\n"
            "    resultat = stats.pearsonr(paire[colonnes_num[0]], paire[colonnes_num[1]])\n"
            "    return {\n"
            "        'colonnes': [colonnes_num[0], colonnes_num[1]],\n"
            "        'coefficient': float(resultat.statistic),\n"
            "        'p_value': float(resultat.pvalue),\n"
            "    }\n"
        ),
    },
    {
        "slug": "correlation_spearman",
        "nom": tl("engine/formules_calcul.py:g:1123:16"),
        "categorie": "statistique",
        "cible_type": "table",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:1127:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    df = pd.DataFrame(cible)\n"
            "    colonnes_num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    if len(colonnes_num) < 2:\n"
            "        return None\n"
            "    paire = df[[colonnes_num[0], colonnes_num[1]]].apply(pd.to_numeric, errors='coerce').dropna()\n"
            "    if len(paire) < 2:\n"
            "        return None\n"
            "    resultat = stats.spearmanr(paire[colonnes_num[0]], paire[colonnes_num[1]])\n"
            "    return {\n"
            "        'colonnes': [colonnes_num[0], colonnes_num[1]],\n"
            "        'coefficient': float(resultat.statistic),\n"
            "        'p_value': float(resultat.pvalue),\n"
            "    }\n"
        ),
    },
    {
        "slug": "duree_entre_dates",
        "nom": tl("engine/formules_calcul.py:g:1149:16"),
        "categorie": "date",
        "cible_type": "table",
        "types_cibles_compatibles": TYPES_TOUS,
        "description": (
            tl("engine/formules_calcul.py:g:1154:13")
        ),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    df = pd.DataFrame(cible)\n"
            "    colonnes_date = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]\n"
            "    if len(colonnes_date) < 2:\n"
            "        return None\n"
            "    ecarts = (pd.to_datetime(df[colonnes_date[1]], errors='coerce')\n"
            "              - pd.to_datetime(df[colonnes_date[0]], errors='coerce')).dt.days.dropna()\n"
            "    if ecarts.empty:\n"
            "        return None\n"
            "    return {\n"
            "        'colonnes': [colonnes_date[0], colonnes_date[1]],\n"
            "        'ecart_moyen_jours': round(float(ecarts.mean()), 2),\n"
            "    }\n"
        ),
    },
]

# ── Statistiques inférentielles : tests d'hypothèse sur une colonne ────────
_MODELE_TEST_1_ECH = (
    "def formule(cible):\n"
    "    import pandas as pd\n"
    "    from scipy import stats\n"
    "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna().to_numpy(dtype='float64')\n"
    "    if len(serie) < {n_min}:\n"
    "        return None\n"
    "    r = stats.{fn}(serie{args})\n"
    "    return {{'statistique': float(r.statistic), 'p_valeur': float(r.pvalue)}}\n"
)
_TESTS_1_ECHANTILLON = [
    ("test_normalite_shapiro", tl("engine/formules_calcul.py:g:1188:32"), "shapiro", 3, ""),
    ("test_normalite_dagostino", tl("engine/formules_calcul.py:g:1189:34"), "normaltest", 8, ""),
    ("test_normalite_jarque_bera", tl("engine/formules_calcul.py:g:1190:36"), "jarque_bera", 3, ""),
    ("test_t_un_echantillon", tl("engine/formules_calcul.py:g:1191:31"), "ttest_1samp", 2, ", 0"),
    ("test_wilcoxon", tl("engine/formules_calcul.py:g:1192:23"), "wilcoxon", 3, ""),
    ("test_ks_normal", tl("engine/formules_calcul.py:g:1193:24"), "kstest", 3, ", 'norm'"),
]
for _slug, _nom, _fn, _n, _args in _TESTS_1_ECHANTILLON:
    FORMULES_PREDEFINIES.append({
        "slug": _slug, "nom": _nom, "categorie": "inferentiel", "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tf("engine/formules_calcul.py:g:1193:24#2", v1=_nom, v2=_fn),
        "code": _MODELE_TEST_1_ECH.format(fn=_fn, n_min=_n, args=_args),
    })

# ── Probabilités : ajustement de lois usuelles sur une colonne ─────────────
_MODELE_LOI = (
    "def formule(cible):\n"
    "    import pandas as pd\n"
    "    from scipy import stats\n"
    "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna().to_numpy(dtype='float64')\n"
    "    if len(serie) < 5:\n"
    "        return None\n"
    "    params = stats.{dist}.fit(serie)\n"
    "    ks = stats.kstest(serie, '{dist}', args=params)\n"
    "    return {{'parametres': [round(float(p), 4) for p in params],\n"
    "            'ks_stat': float(ks.statistic), 'ks_p_valeur': float(ks.pvalue)}}\n"
)
_LOIS_PROBABILITE = [
    ("norm", "Ajustement loi normale"), ("expon", "Ajustement loi exponentielle"),
    ("uniform", "Ajustement loi uniforme"), ("gamma", "Ajustement loi gamma"),
    ("lognorm", "Ajustement loi log-normale"), ("weibull_min", tl("engine/formules_calcul.py:g:1219:64")),
    ("chi2", tl("engine/formules_calcul.py:g:1220:14")), ("t", tl("engine/formules_calcul.py:g:1220:45")),
]
for _dist, _nom in _LOIS_PROBABILITE:
    FORMULES_PREDEFINIES.append({
        "slug": f"loi_{_dist}", "nom": _nom, "categorie": "probabilite", "cible_type": "colonne",
        "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tf("engine/formules_calcul.py:g:1220:24", v1=_nom),
        "code": _MODELE_LOI.format(dist=_dist),
    })

FORMULES_PREDEFINIES += [
    {
        "slug": "intervalle_confiance_moyenne_95", "nom": tl("engine/formules_calcul.py:g:1232:59"),
        "categorie": "inferentiel", "cible_type": "colonne", "types_cibles_compatibles": TYPES_NUMERIQUES,
        "description": tl("engine/formules_calcul.py:g:1234:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna().to_numpy(dtype='float64')\n"
            "    if len(serie) < 2:\n"
            "        return None\n"
            "    m, sem = float(np.mean(serie)), stats.sem(serie)\n"
            "    b = stats.t.interval(0.95, len(serie) - 1, loc=m, scale=sem)\n"
            "    return {'moyenne': m, 'borne_basse': float(b[0]), 'borne_haute': float(b[1])}\n"
        ),
    },
    {
        "slug": "test_stationnarite_adf", "nom": tl("engine/formules_calcul.py:g:1249:50"),
        "categorie": "temporel", "cible_type": "table", "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:1251:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from statsmodels.tsa.stattools import adfuller\n"
            "    df = pd.DataFrame(cible)\n"
            "    cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    if not cols:\n"
            "        return None\n"
            "    serie = pd.to_numeric(df[cols[0]], errors='coerce').dropna()\n"
            "    if len(serie) < 8:\n"
            "        return None\n"
            "    r = adfuller(serie.to_numpy(dtype='float64'))\n"
            "    return {'colonne': cols[0], 'statistique': float(r[0]), 'p_valeur': float(r[1]), 'stationnaire': bool(r[1] < 0.05)}\n"
        ),
    },
    # ── Analyses multivariées : ANOVA, ACP, AFC, ACM ────────────────────
    {
        "slug": "anova_un_facteur", "nom": tl("engine/formules_calcul.py:g:1269:44"),
        "categorie": "multivarie", "cible_type": "table", "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:1271:24"),
        "code": (
            "def formule(cible):\n"
            "    import pandas as pd\n"
            "    from scipy import stats\n"
            "    df = pd.DataFrame(cible)\n"
            "    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    cat_cols = [c for c in df.columns if c not in num_cols]\n"
            "    if not num_cols or not cat_cols:\n"
            "        return None\n"
            "    d = df[[cat_cols[0], num_cols[0]]].dropna()\n"
            "    groupes = [g[num_cols[0]].to_numpy(dtype='float64') for _, g in d.groupby(cat_cols[0]) if len(g) >= 2]\n"
            "    if len(groupes) < 2:\n"
            "        return None\n"
            "    r = stats.f_oneway(*groupes)\n"
            "    return {'facteur': cat_cols[0], 'variable': num_cols[0], 'nb_groupes': len(groupes), 'statistique_f': float(r.statistic), 'p_valeur': float(r.pvalue)}\n"
        ),
    },
    {
        "slug": "acp", "nom": tl("engine/formules_calcul.py:g:1290:31"),
        "categorie": "multivarie", "cible_type": "table", "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:1292:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    df = pd.DataFrame(cible)\n"
            "    cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    d = df[cols].apply(pd.to_numeric, errors='coerce').dropna()\n"
            "    if len(cols) < 2 or len(d) < 3:\n"
            "        return None\n"
            "    x = (d - d.mean()) / d.std(ddof=0).replace(0, 1)\n"
            "    _, s, vt = np.linalg.svd(x.to_numpy(dtype='float64'), full_matrices=False)\n"
            "    var_exp = (s ** 2) / np.sum(s ** 2)\n"
            "    cp1 = {c: round(float(v), 4) for c, v in zip(cols, vt[0])}\n"
            "    cp2 = {c: round(float(v), 4) for c, v in zip(cols, vt[1])} if len(cols) > 1 else None\n"
            "    return {'colonnes': cols, 'variance_expliquee': [round(float(v), 4) for v in var_exp], 'contributions_cp1': cp1, 'contributions_cp2': cp2}\n"
        ),
    },
    {
        "slug": "afc", "nom": tl("engine/formules_calcul.py:g:1311:31"),
        "categorie": "multivarie", "cible_type": "table", "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:1313:24"),
        "code": (
            "def formule(cible):\n"
            "    import numpy as np\n"
            "    import pandas as pd\n"
            "    from scipy.stats import chi2_contingency\n"
            "    df = pd.DataFrame(cible)\n"
            "    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
            "    cat_cols = [c for c in df.columns if c not in num_cols]\n"
            "    if len(cat_cols) < 2:\n"
            "        return None\n"
            "    table = pd.crosstab(df[cat_cols[0]], df[cat_cols[1]])\n"
            "    if table.shape[0] < 2 or table.shape[1] < 2:\n"
            "        return None\n"
            "    chi2, p, ddl, _ = chi2_contingency(table)\n"
            "    total = table.values.sum()\n"
            "    profils = table / total - np.outer(table.sum(1), table.sum(0)) / total ** 2\n"
            "    _, s, _ = np.linalg.svd(profils.to_numpy(dtype='float64'), full_matrices=False)\n"
            "    return {'variables': [cat_cols[0], cat_cols[1]], 'chi2': float(chi2), 'p_valeur': float(p), 'ddl': int(ddl), 'valeurs_propres': [round(float(v ** 2), 4) for v in s]}\n"
        ),
    },
    {
        "slug": "acm", "nom": tl("engine/formules_calcul.py:g:1335:31"),
        "categorie": "multivarie", "cible_type": "table", "types_cibles_compatibles": TYPES_TOUS,
        "description": tl("engine/formules_calcul.py:g:1337:24"),
        "code": (
            tl("engine/formules_calcul.py:g:1333:13")
        ),
    },
]


# 100 formules « data science » (qualité, tests, effets, régression, séries, segmentation, anomalies,
# finance, probabilités) — voir engine/formules_science.py.
from .formules_science import FORMULES_SCIENCE  # noqa: E402
FORMULES_PREDEFINIES += FORMULES_SCIENCE


def synchroniser_formules_predefinies():
    """Crée ou met à jour (code, description, catégorie) les formules
    prédéfinies (entreprise=None, predefinie=True) en base. Idempotent :
    à appeler au démarrage de l'application (voir apps.py)."""
    from .catalogue_sync import synchroniser_catalogue
    from .models import FormuleCalcul

    synchroniser_catalogue(FormuleCalcul, "formules", FORMULES_PREDEFINIES, lambda f: {
        "nom": f["nom"], "categorie": f["categorie"], "cible_type": f["cible_type"],
        "types_cibles_compatibles": f["types_cibles_compatibles"], "description": f["description"],
        "code": f["code"], "predefinie": True,
    })
