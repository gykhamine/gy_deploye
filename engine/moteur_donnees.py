"""Point d'entrée UNIQUE du choix Polars / pandas.

Règle : à partir de SEUIL_POLARS lignes (1 000), Polars peut ASSISTER certains calculs (si installé) ; en dessous — ou si Polars
est absent ou échoue sur ces données — pandas seul. Toute construction de DataFrame à partir de lignes de table passe par
`dataframe_depuis`, tout choix de moteur par `utiliser_polars` : changer le seuil ou la règle se fait ICI, nulle part ailleurs.

PANDAS EST LE MAÎTRE : toute donnée qui ENTRE (fichier, base externe, lignes de table) et toute donnée qui SORT est pandas.
Polars n'existe qu'À L'INTÉRIEUR d'un calcul, via `calcul_polars` (seule porte) : pandas entre, un tableau numpy sort, aucun objet
Polars (DataFrame, LazyFrame, Series) ne traverse jamais une fonction. Les scripts, formules, graphiques et rapports sont écrits pour pandas.
"""

SEUIL_POLARS = 1_000   # lignes (d'une table, d'un fichier, d'une analyse) à partir desquelles Polars prend le relais


def polars_disponible() -> bool:
    try:
        import polars  # noqa: F401
        return True
    except ImportError:
        return False


def utiliser_polars(nb_lignes) -> bool:
    return (nb_lignes or 0) >= SEUIL_POLARS and polars_disponible()


def _nb_lignes(donnees) -> int:
    if isinstance(donnees, dict):
        return max((len(v) for v in donnees.values() if hasattr(v, "__len__") and not isinstance(v, str)), default=0)
    try:
        return len(donnees)
    except TypeError:
        return 0


def dataframe_depuis(donnees):
    """DataFrame pandas depuis une liste de dicts (lignes) ou un dict de colonnes. Toujours construit par pandas :
    Polars ne prend jamais une donnée en entrée (types déduits différemment = bugs de compatibilité)."""
    import pandas as pd

    return pd.DataFrame(donnees)


def calcul_polars(entree_pandas, calcul):
    """SEULE porte de Polars. `entree_pandas` : DataFrame/Series pandas ; `calcul(objet_polars)` : renvoie une Series Polars.
    Retourne un tableau numpy (jamais un objet Polars), ou None si Polars est absent, refuse les données (types mêlés...)
    ou échoue : l'appelant retombe alors sur pandas, inchangé."""
    try:
        import polars as pl
        resultat = calcul(pl.from_pandas(entree_pandas))
        return resultat.to_numpy() if isinstance(resultat, pl.Series) else None
    except Exception:
        return None


# ── Bascule du calcul des scripts (formules, analyses, graphiques BI) ────────────────────────────────────────────
# Les scripts sont écrits pour pandas et ne sont PAS réécrits. Le seul point de bascule est ici : dans le processus
# sandbox (jamais dans le serveur), `pandas.to_numeric(..., errors="coerce")` — le calcul le plus répété des scripts —
# passe par Polars pour les Series TEXTE d'au moins SEUIL_POLARS lignes. Tout le reste (Series déjà numériques,
# listes, autres options, moins de SEUIL_POLARS lignes, Polars absent, la moindre exception) délègue à pandas, inchangé.
# Interrupteur ci-dessous : True = bascule active (mettre False pour revenir à pandas seul).

BASCULE_SCRIPTS = False


def activer_bascule_scripts() -> bool:
    """À appeler dans le processus sandbox, avant exec() du script. Renvoie True si la bascule est active."""
    if not BASCULE_SCRIPTS or not polars_disponible():
        return False
    import pandas as pd

    if getattr(pd.to_numeric, "_bascule_polars", False):
        return True
    to_numeric_pandas = pd.to_numeric

    def _numerique(s):                                # s : Series Polars, vue seulement dans calcul_polars
        import polars as pl
        if s.dtype != pl.String:
            from engine.i18n import tfr as _gtfr  # noqa: E402  (import local : ce module reste autonome)
            raise TypeError(_gtfr("engine/moteur_donnees.py:79:29"))
        s = s.str.strip_chars()
        entiers = s.drop_nulls().str.contains(r"^[+-]?\d{1,15}$").all()
        return s.cast(pl.Int64 if entiers and s.null_count() == 0 else pl.Float64, strict=False)   # pandas : int64 si tout est entier

    def to_numeric(arg, errors="raise", downcast=None, **kwargs):
        if (errors == "coerce" and downcast is None and not kwargs and isinstance(arg, pd.Series)
                and arg.dtype == object and utiliser_polars(len(arg))):
            valeurs = calcul_polars(arg, _numerique)
            if valeurs is not None:
                return pd.Series(valeurs, index=arg.index, name=arg.name)
        return to_numeric_pandas(arg, errors=errors, downcast=downcast, **kwargs)

    to_numeric._bascule_polars = True
    pd.to_numeric = to_numeric
    # Symbiose : Polars calcule l'ORDRE (permutation), pandas l'applique (index, dtypes et colonnes restent ceux de pandas).
    tri_pandas = pd.DataFrame.sort_values

    def sort_values(self, by=None, *, axis=0, ascending=True, **kwargs):
        try:
            cols = [by] if isinstance(by, str) else list(by)
            asc = [ascending] * len(cols) if isinstance(ascending, bool) else list(ascending)
            if (not kwargs and axis == 0 and len(asc) == len(cols) and self.columns.is_unique and utiliser_polars(len(self))
                    and all(isinstance(c, str) and c in self.columns and not isinstance(self[c].dtype, pd.CategoricalDtype) for c in cols)):
                ordre = calcul_polars(self[cols].reset_index(drop=True), lambda d: d.with_row_index("__ordre__").sort(
                    cols, descending=[not a for a in asc], nulls_last=True, maintain_order=True)["__ordre__"])
                if ordre is not None:
                    return self.iloc[ordre]
        except Exception:
            pass
        return tri_pandas(self, by=by, axis=axis, ascending=ascending, **kwargs)

    pd.DataFrame.sort_values = sort_values
    return True
