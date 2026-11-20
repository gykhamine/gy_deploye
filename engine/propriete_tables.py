"""Propriété partageable des tables.

L'administrateur (ou l'agent qui détient le droit `toutes_tables`) peut retirer une table à un agent — même s'il l'a créée — et la
confier à un ou plusieurs autres agents, sans toucher au compte de l'agent. Règle unique, appliquée partout où les lignes sont isolées :
  · table SANS propriétaire : règle d'origine, chaque agent voit les lignes qu'il a créées ;
  · table AVEC propriétaires : seuls les propriétaires (et l'administrateur) y accèdent, et ils en voient TOUTES les lignes.
La propriété s'applique à toutes les parties d'un groupe de tables. Les lignes gardent leur auteur (`cree_par`) : l'audit ne change pas.
"""
from django.db import transaction
from django.db.models import Q

from .models import MembreEntreprise, ProprieteTable
from .vue_groupe import ids_groupe


class ProprieteErreur(Exception):
    pass


def filtrer_lignes(qs, utilisateur):
    """Lignes (Enregistrement) qu'un AGENT peut voir : celles des tables qu'il possède, plus ses propres lignes des tables sans propriétaire."""
    avec_proprietaire = ProprieteTable.objects.values("table_id")
    possedees = ProprieteTable.objects.filter(utilisateur=utilisateur).values("table_id")
    return qs.filter(Q(table_id__in=possedees) | (Q(cree_par=utilisateur) & ~Q(table_id__in=avec_proprietaire)))


def table_fermee(utilisateur, table):
    """Vrai si la table a des propriétaires et que `utilisateur` n'en fait pas partie (à n'appliquer qu'à un agent)."""
    p = ProprieteTable.objects.filter(table_id=table.id)
    return p.exists() and not p.filter(utilisateur=utilisateur).exists()


def ids_fermees(utilisateur):
    """Ids des tables que `utilisateur` (agent) ne peut plus ouvrir : elles ont des propriétaires, dont il n'est pas."""
    miennes = ProprieteTable.objects.filter(utilisateur=utilisateur).values("table_id")
    return set(ProprieteTable.objects.exclude(table_id__in=miennes).values_list("table_id", flat=True))


def proprietaires_de(table):
    return sorted(ProprieteTable.objects.filter(table_id=table.id).values_list("utilisateur_id", flat=True))


@transaction.atomic
def definir_proprietaires(table, utilisateurs_ids):
    """Remplace les propriétaires de `table` (et de son groupe) par ces agents ; liste vide = retour à la règle d'origine.
    Un agent qui reçoit la table et dont la liste blanche `tables_autorisees` n'est pas vide l'y voit ajoutée (vide = aucune restriction)."""
    ids = {int(i) for i in utilisateurs_ids or ()}
    agents = {m.utilisateur_id: m for m in MembreEntreprise.objects.filter(entreprise_id=table.entreprise_id, role="agent", utilisateur_id__in=ids)}
    if ids - set(agents):
        raise ProprieteErreur("agent_inconnu")
    tables = ids_groupe(table)
    ProprieteTable.objects.filter(table_id__in=tables).exclude(utilisateur_id__in=ids).delete()
    for tid in tables:
        for uid in ids:
            ProprieteTable.objects.get_or_create(table_id=tid, utilisateur_id=uid)
    for m in agents.values():
        if m.tables_autorisees:
            m.tables_autorisees = sorted(set(m.tables_autorisees) | set(tables))
            m.save(update_fields=["tables_autorisees"])
    return sorted(ids)
