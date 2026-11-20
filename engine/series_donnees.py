"""SÉRIES TEMPORELLES — couche données : relie les tables de la plateforme au moteur pur `engine/series_temporelles.py`.

Structure attendue d'une table : au moins UNE colonne de type date ou datetime + au moins UNE colonne numérique
(types de `fusion.TYPES_NUM`, source unique). La colonne date et les colonnes de valeurs sont CHOISIES par l'utilisateur dans la page
(paramètre `temps`) ; sans choix explicite (appel d'API sans `temps`), la première colonne date est utilisée. Chaque colonne numérique est une série. Une table qui n'a pas cette structure est simplement
ignorée (message explicite si c'est la table ouverte). Plusieurs tables s'analysent ensemble dès qu'elles ont la structure attendue.
Les lignes sont lues par `enregistrements_groupe` (traitement agrégé : un groupe de tables = une grande table).
Rien n'est enregistré : le résultat est renvoyé à l'utilisateur.
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t

import logging

from .vue_groupe import nom_vue
from . import series_extras as _x
from . import series_temporelles as _s
from .fusion import TYPES_NUM
from .geo_donnees import TYPES_TEMPS

logger = logging.getLogger(__name__)

LIMITES = {"lignes": None, "tables": 10, "series_par_table": 12, "tables_listees": 200}
MESSAGE_STRUCTURE = (td("engine/series_donnees.py:g:25:22"))


def structure(table, temps_slug: str | None = None) -> dict | None:
    """{"temps": champ, "temps_colonnes": [champs date], "valeurs": [champs numériques]} si la table a la structure attendue, sinon None.
    `temps_slug` = colonne date choisie par l'utilisateur ; absente ou inconnue, la première colonne date sert de repli."""
    champs = sorted(table.champs.all(), key=lambda c: (c.ordre, c.id))
    dates = [c for c in champs if c.type_champ in TYPES_TEMPS]
    temps = next((c for c in dates if temps_slug and c.slug == temps_slug), dates[0] if dates else None)
    valeurs = [c for c in champs if c.type_champ in TYPES_NUM]
    return {"temps": temps, "temps_colonnes": dates, "valeurs": valeurs} if temps and valeurs else None


def decrire(table) -> dict | None:
    """Fiche d'une table compatible pour l'interface, None si la structure attendue manque."""
    st = structure(table)
    if not st:
        return None
    from .vue_groupe import nom_vue
    return {"id": table.id, "nom": nom_vue(table), "temps": st["temps"].nom,
            "temps_colonnes": [{"slug": c.slug, "nom": c.nom} for c in st["temps_colonnes"]],
            "colonnes": [{"slug": c.slug, "nom": c.nom} for c in st["valeurs"][:LIMITES["series_par_table"]]]}


def tables_compatibles(entreprise) -> list[dict]:
    """Tables de l'entreprise qui ont la structure attendue (les autres ne sont pas proposées)."""
    from .models import TableDynamique
    from .vue_groupe import tables_generales   # un groupe = UNE table dans la liste de choix
    fiches = (decrire(t) for t in tables_generales(TableDynamique.objects.filter(entreprise=entreprise).order_by("nom").prefetch_related("champs"))
              [:LIMITES["tables_listees"]])
    return [f for f in fiches if f]


def series_table(table, slugs=None, limite: int | None = LIMITES["lignes"], exog=None, temps=None) -> list[dict]:
    """Une entrée par colonne numérique retenue : {table_id, table, colonne, slug, dates, valeurs, explicatives}. [] sans la structure attendue.
    `exog` = slugs des colonnes de cette table servant de variables explicatives aux autres séries ({nom: (dates, valeurs)}) : leurs lignes à venir
    (colonne à analyser vide) servent aux estimations. `temps` = slug de la colonne date choisie (repli : la première colonne date)."""
    st = structure(table, temps)
    if not st:
        return []
    colonnes = [c for c in st["valeurs"] if not slugs or c.slug in slugs][:LIMITES["series_par_table"]]
    if not colonnes:
        return []
    from . import parquet_store
    lignes = parquet_store.lignes_analytiques(table, limite)   # les `limite` dernières lignes, fichier Parquet
    dates = [ligne.get(st["temps"].slug) for ligne in lignes]
    col = lambda c: [ligne.get(c.slug) for ligne in lignes]
    return [{"table_id": table.id, "table": nom_vue(table), "colonne": c.nom, "slug": c.slug, "dates": dates, "valeurs": col(c),
             "explicatives": {e.nom: (dates, col(e)) for e in st["valeurs"] if e.slug in (exog or ()) and e.slug != c.slug}} for c in colonnes]


def _analyser_series(series: list[dict], **params) -> list[dict]:
    sortie = []
    for s in series:
        try:
            r = _s.analyser(s["dates"], s["valeurs"], nom=s["colonne"], explicatives=s.get("explicatives") or None, **params)
        except Exception:   # une série défaillante ne bloque jamais les autres
            logger.exception(_i18n_t("engine/series_donnees.py:g:81:30"), s["colonne"], s["table_id"])
            r = {"nom": s["colonne"], "erreur": _i18n_t("engine/series_donnees.py:g:81:49")}
        if not r.get("erreur"):     # exploration étendue : jamais bloquante, l'analyse principale reste livrée sans elle
            try:
                r["exploration"] = _x.explorer(r["x"], r["y"], r["frequence"]["genre"], r["periode"], (r.get("saison") or {}).get("phases"),
                                               r.get("moyenne_mobile"), r.get("ordre_moyenne_mobile") or 3)
            except Exception:
                logger.exception(_i18n_t("engine/series_donnees.py:g:88:34"), s["colonne"], s["table_id"])
                r["exploration"] = None
        r.update(table_id=s["table_id"], table=s["table"], colonne=s["colonne"], slug=s["slug"])
        sortie.append(r)
    return sortie


def analyser_table(table, slugs=None, exog=None, temps=None, **params) -> dict:
    """Analyse complète de toutes les colonnes numériques (ou de `slugs`) d'une table, avec les colonnes `exog` comme variables explicatives
    et la colonne date `temps` choisie."""
    series = series_table(table, slugs, exog=exog, temps=temps)
    if not series:
        return {"table_id": table.id, "table": nom_vue(table), "vide": True, "message": MESSAGE_STRUCTURE}
    return {"table_id": table.id, "table": nom_vue(table), "series": _analyser_series(series, **params)}


def analyser_tables(tables: list, slugs_par_table: dict | None = None, exog_par_table: dict | None = None,
                    temps_par_table: dict | None = None, **params) -> list[dict]:
    """Une entrée par table (au plus LIMITES['tables']) ; `slugs_par_table` = {id de table: {slugs}} (absent = toutes les colonnes),
    `exog_par_table` = {id de table: {slugs des variables explicatives}}, `temps_par_table` = {id de table: {slug de la colonne date}}."""
    slugs_par_table, exog_par_table, temps_par_table = slugs_par_table or {}, exog_par_table or {}, temps_par_table or {}
    return [analyser_table(t, slugs_par_table.get(t.id), exog_par_table.get(t.id), next(iter(temps_par_table.get(t.id) or ()), None), **params)
            for t in tables[:LIMITES["tables"]]]


def analyser_demo(**params) -> dict:
    """Séries simulées (aucune donnée lue ni écrite) : voir l'onglet à l'œuvre."""
    series = [{"table_id": 0, "table": td("engine/series_donnees.py:g:116:40"), "colonne": nom, "slug": f"demo{i}", "dates": d, "valeurs": v}
              for i, (nom, d, v) in enumerate(_s.demo())]
    return {"table_id": 0, "table": td("engine/series_donnees.py:g:118:37"), "series": _analyser_series(series, **params)}


def comparer_resultats(resultats: list[dict]) -> dict | None:
    """Liens entre toutes les séries analysées ensemble (`analyser_tables`/`analyser_demo`) : corrélations, décalage, matrice."""
    try:
        return _x.comparer([s for t in resultats for s in t.get("series", [])])
    except Exception:
        logger.exception(_i18n_t("engine/series_donnees.py:g:125:26"))
        return None
