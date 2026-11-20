"""Un GROUPE de tables s'ouvre et se traite comme UNE table (la « table générale »).

Source unique de la règle « table générale » pour la grille, les exports et les actions par ligne :
  * `tables_vue(table)`        : les tables physiques qui composent la vue (le groupe entier hors tables dénaturées,
                                 dans l'ordre des parties — ou la table seule si elle n'est pas groupée) ;
  * `enregistrements_vue(table)` : toutes leurs lignes ;
  * `champs_vue(table)`        : colonnes de la table générale, par slug (la structure est commune à tout le groupe) ;
  * `ids_champs_par_slug(table)` : pour chaque slug, les ids des colonnes homonymes de chaque partie (les valeurs d'une ligne
                                 sont rattachées au champ de SA partie : filtres et tri doivent viser toutes ces colonnes).
Les analyses (stats, BI, KPI, IA) lisent déjà le groupe via `TableDynamique.enregistrements_groupe` : ce module ne fait que
donner la même vision à la grille. Les écritures restent rattachées à la table physique de la ligne.
"""
from django.db import models

from .models import Enregistrement


def tables_vue(table):
    if not table.groupe_id:
        return [table]
    membres = list(table.groupe.tables.filter(denaturee=False).order_by("partie_index", "id"))
    return membres or [table]


def enregistrements_vue(table):
    if not table.groupe_id:
        return table.enregistrements.all()
    return Enregistrement.objects.filter(table__in=tables_vue(table))


def champs_vue(table):
    """Colonnes de la table générale : celles de la 1re partie (structure commune à tout le groupe)."""
    return list(tables_vue(table)[0].champs.all())


def ids_champs_par_slug(table):
    ids = {}
    for t in tables_vue(table):
        for c in t.champs.all():
            ids.setdefault(c.slug, []).append(c.id)
    return ids


def derniere_partie(table):
    """Partie qui reçoit les NOUVELLES lignes quand le groupe est ouvert comme une table : la dernière du groupe
    (même ordre que TableDynamique.est_derniere_du_groupe). Table non groupée : elle-même."""
    if not table.groupe_id:
        return table
    derniere = table.groupe.tables.order_by(models.F("partie_index").desc(nulls_last=True), "-id").first()
    return derniere or table


def nom_vue(table):
    """Nom sous lequel la table générale se présente : celui du groupe pour l'ancre d'un groupe, sinon celui de la table."""
    nom = getattr(table, "_nom_vue", None)
    if nom:
        return nom
    return table.groupe.nom if table.groupe_id else table.nom


def table_generale(table):
    """Table sous laquelle la vue ouverte est listée dans les analyses : l'ancre (1re partie) du groupe, sous le nom du groupe,
    quelle que soit la partie ouverte — de sorte que la liste de choix reconnaît la table ouverte et n'ajoute pas de doublon."""
    if not table.groupe_id:
        return table
    ancre = table.groupe.tables.order_by("partie_index", "id").first() or table
    ancre._nom_vue = table.groupe.nom
    return ancre


def tables_generales(tables):
    """Liste de choix des analyses : chaque GROUPE n'y figure qu'une fois, comme une table (son ancre = sa première partie,
    la même que la barre latérale), sous le nom du groupe ; ses parties n'y sont plus proposées une à une."""
    vus, resultat = set(), []
    for t in tables:
        if not t.groupe_id:
            resultat.append(t)
            continue
        if t.groupe_id in vus:
            continue
        vus.add(t.groupe_id)
        if t.groupe.est_fusionne:   # groupe fusionné en une table (groupe_fusion.py) : seule la table fusionnée est proposée, pas ses parties
            continue
        ancre = t.groupe.tables.order_by("partie_index", "id").first() or t
        ancre._nom_vue = t.groupe.nom
        resultat.append(ancre)
    return resultat


def ids_du_groupe(table_id):
    """Ids de toutes les parties du groupe de `table_id` (ou [table_id] si elle n'est pas groupée) — pour exclure un groupe entier."""
    from .models import TableDynamique
    t = TableDynamique.objects.filter(id=table_id).only("id", "groupe_id").first() if table_id else None
    if t is None or not t.groupe_id:
        return [table_id] if table_id else []
    return list(TableDynamique.objects.filter(groupe_id=t.groupe_id).values_list("id", flat=True))


def ancre_groupe(table):
    """Première partie du groupe (la table sous laquelle le groupe se présente et porte ses objets : dashboards, KPI…).
    Table non groupée : elle-même."""
    if not table.groupe_id:
        return table
    return table.groupe.tables.order_by("partie_index", "id").first() or table


def ids_groupe(table):
    """Ids de toutes les parties du groupe de `table` (denaturées comprises : un objet déjà rattaché à une partie reste visible)."""
    if not table.groupe_id:
        return [table.id]
    from .models import TableDynamique
    return list(TableDynamique.objects.filter(groupe_id=table.groupe_id).values_list("id", flat=True))
