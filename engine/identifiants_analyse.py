"""Matricule et clé d'accès (code d'accès client) : colonnes de LECTURE pour les analyses.

Dans une table d'agents, de clients ou de produits (TableDynamique.est_table_membres), le matricule et la clé d'accès (code d'accès
client) ne sont pas des champs dynamiques : ils vivent sur l'Enregistrement (voir Enregistrement.to_dict). Ils n'étaient donc ni dans le
fichier Parquet ni dans la liste des colonnes des analyses, et les statistiques les écartaient expressément — impossible de regrouper
par matricule, de compter par client, de croiser deux tables sur leur matricule…

Règle : ils sont VISIBLES dans toute lecture (statistiques, ANOVA, tableaux de bord BI, KPI, calculs) et INTERDITS à l'écriture.
Ce module ne fait que LIRE : il ajoute deux colonnes à un DataFrame déjà construit, jamais l'inverse. Les chemins d'écriture
(calc_champ, formules exécutées pendant l'enregistrement d'une ligne, import, fusion, entraînement) ne l'utilisent pas et n'ont pas
changé : un matricule ou une clé ne peut toujours être ni saisi, ni calculé, ni écrasé par une analyse.

  - matricule         : toute table d'agents, de clients ou de produits ;
  - code_acces_client : tables d'agents et de clients seulement (un produit n'a pas de compte : voir TableDynamique.a_identifiants_connexion).
"""
from engine.i18n import t
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7

MATRICULE = "matricule"
CLE = "code_acces_client"
SLUGS = (MATRICULE, CLE)


def slugs_pour(table) -> list:
    """Slugs des colonnes d'identification que `table` offre aux analyses ([] pour une table ordinaire ou une table système)."""
    if table is None or not getattr(table, "est_table_membres", False) or getattr(table, "source_systeme", ""):
        return []
    return [MATRICULE] + ([CLE] if getattr(table, "a_identifiants_connexion", False) else [])


# Un widget BI désigne ses colonnes par id de ChampDynamique : ces colonnes n'en ont pas, elles prennent des ids NÉGATIFS fixes (jamais
# en base, jamais en collision avec un vrai champ) que le serveur retraduit en slug (tableau_bord_bi._colonnes_widget).
ID_VIRTUEL = {MATRICULE: -1, CLE: -2}
SLUG_PAR_ID = {v: k for k, v in ID_VIRTUEL.items()}


def ids_valides(table, ids) -> list:
    """Parmi `ids` (liste d'entiers venue du navigateur), les ids de colonnes d'identification que `table` offre vraiment."""
    offerts = {ID_VIRTUEL[s] for s in slugs_pour(table)}
    return [i for i in (ids or []) if isinstance(i, int) and not isinstance(i, bool) and i in offerts]


def libelle(slug: str) -> str:
    return t("engine/identifiants_analyse.py:libelle:" + slug)


def champs_virtuels(table) -> list:
    """ChampDynamique NON enregistrés (jamais sauvés : lecture seule) qui décrivent ces colonnes, à la suite des vrais champs ; type
    « texte » (qualitatif). Même interface que les vrais champs pour les modules qui ne lisent que slug / nom / type_champ / ordre."""
    from .models import ChampDynamique
    return [ChampDynamique(slug=s, nom=libelle(s), type_champ="texte", ordre=10 ** 6 + i) for i, s in enumerate(slugs_pour(table))]


def champs_analysables(table) -> list:
    """Les champs de `table` (dans leur ordre) puis, s'il y en a, ses colonnes d'identification : ce que les analyses proposent."""
    return list(table.champs.all()) + champs_virtuels(table)


def ajouter(df, table, groupe: bool = True):
    """`df` (DataFrame avec la colonne technique `_id`, voir parquet_store.COLONNE_ID) + colonnes matricule / clé de `table`, lues dans
    l'Enregistrement (une seule requête pour toute la table ou tout le groupe). Valeur vide → None, comme une cellule non renseignée.
    Sans `_id`, sans colonne à ajouter ou en cas d'erreur : `df` rendu tel quel — une analyse ne casse jamais à cause de ces colonnes."""
    slugs = [s for s in slugs_pour(table) if s not in df.columns]
    from .parquet_store import COLONNE_ID, _parties_analytiques
    if not slugs or COLONNE_ID not in df.columns:
        return df
    try:
        from .models import Enregistrement
        parties = _parties_analytiques(table, groupe)
        carte = {i: (m, c) for i, m, c in
                 Enregistrement.objects.filter(table__in=parties).values_list("id", MATRICULE, CLE)}
        df = df.copy()
        ids = df[COLONNE_ID].tolist()
        for s in slugs:
            pos = 0 if s == MATRICULE else 1
            df[s] = [(carte.get(i, ("", ""))[pos] or None) for i in ids]
        return df
    except Exception:   # base indisponible, table sans enregistrements… : les colonnes manquent, l'analyse continue
        import logging
        logging.getLogger(__name__).exception(_gt("engine/identifiants_analyse.py:79:47"), getattr(table, "pk", "?"))
        return df
