"""Hiérarchie d'affichage des tables : TABLE seule  <  GROUPE  <  CONTENEUR — sans doublon.

Règle (une seule « place » par élément) :
  * une table seule est visible seule ;
  * une table qui entre dans un groupe n'est plus visible seule : seulement dans son groupe ;
  * un groupe rangé dans un conteneur n'est plus visible seul : seulement dans son conteneur ;
  * une table d'un groupe n'est jamais rangée directement dans un conteneur (c'est son groupe qui l'est).

Quand un élément change de niveau (table → groupe, groupe dissous, table détachée…), il garde sa place
dans le conteneur : le groupe prend la place de ses tables, et inversement à la dissolution.
Toutes les fonctions sont sans effet de bord hors de la base et idempotentes.
"""
from engine.i18n import t as _i18n_t
from django.db import transaction

from .models import ConteneurTables, GroupeTables, TableDynamique


def _conteneurs_de_tables(entreprise, table_ids):
    """Conteneurs qui contiennent DIRECTEMENT l'une de ces tables."""
    return list(ConteneurTables.objects.filter(entreprise=entreprise, tables__in=list(table_ids)).distinct())


def erreur_entree_groupe(groupe, tables):
    """Message d'erreur si `tables` ne peuvent pas rejoindre `groupe` sans créer de doublon, sinon None.
    `groupe` peut être None (groupe à créer)."""
    ids = [t.id for t in tables]
    deja = [t.nom for t in tables if t.groupe_id and (groupe is None or t.groupe_id != groupe.id)]
    if deja:
        return (_i18n_t("engine/hierarchie.py:g:29:17") + ", ".join(sorted(deja)) + _i18n_t("engine/hierarchie.py:g:29:75"))
    if not ids:
        return None
    entreprise = tables[0].entreprise
    cibles = {c.pk: c for c in _conteneurs_de_tables(entreprise, ids)}
    if groupe is not None:
        cibles.update({c.pk: c for c in groupe.conteneurs.all()})
    if len(cibles) > 1:
        return (_i18n_t("engine/hierarchie.py:g:38:17") + ", ".join(sorted(c.nom for c in cibles.values())) +
                _i18n_t("engine/hierarchie.py:g:39:17"))
    return None


@transaction.atomic
def placer_tables_dans_groupe(groupe, tables):
    """À appeler APRÈS avoir fixé table.groupe : les tables quittent leurs conteneurs, et le groupe y prend
    leur place (s'il n'est pas déjà rangé quelque part). Aucun doublon table/groupe ne subsiste."""
    ids = [t.id for t in tables]
    if not ids:
        return
    conteneurs = _conteneurs_de_tables(groupe.entreprise, ids)
    for c in conteneurs:
        c.tables.remove(*ids)
    if conteneurs and not groupe.conteneurs.exists():
        conteneurs[0].groupes.add(groupe)


def _pleine(table):
    plafond = table.plafond_lignes
    return plafond is not None and table.enregistrements.count() >= plafond


@transaction.atomic
def ranger_parties(groupe, nouvelles):
    """Rattache `nouvelles` à `groupe` et renumérote les parties. La DERNIÈRE partie reçoit les nouvelles lignes : c'est donc toujours
    celle qui n'est pas encore pleine. Une table pleine qui rejoint le groupe s'insère juste AVANT cette dernière (avant-dernière) ;
    une table non pleine devient la dernière. La chaîne (partie_precedente / partie_total) n'est refaite que si le groupe en avait une."""
    from django.db.models import F
    ids = {t.id for t in nouvelles}
    ordre = list(groupe.tables.exclude(id__in=ids).order_by(F("partie_index").asc(nulls_last=True), "id"))
    chainee = any(t.partie_precedente_id or t.partie_total for t in ordre)
    pleines = [t for t in nouvelles if _pleine(t)]
    if pleines:
        pos = len(ordre) - 1 if ordre and not _pleine(ordre[-1]) else len(ordre)
        ordre[pos:pos] = pleines
    ordre += [t for t in nouvelles if t.id not in {p.id for p in pleines}]
    if chainee:   # OneToOne : on coupe tous les liens avant de les recoller
        TableDynamique.objects.filter(id__in=[t.id for t in ordre]).update(partie_precedente=None)
    for rang, t in enumerate(ordre, start=1):
        valeurs = {"groupe": groupe, "partie_index": rang}
        if chainee:
            valeurs.update(partie_precedente_id=ordre[rang - 2].id if rang > 1 else None, partie_total=len(ordre))
        TableDynamique.objects.filter(id=t.id).update(**valeurs)


@transaction.atomic
def sortir_tables_du_groupe(groupe, tables):
    """À appeler quand des tables quittent `groupe` (détachement ou dissolution) : si le groupe était rangé
    dans un conteneur, elles y restent (en tant que tables seules) — sinon elles réapparaîtraient « ailleurs »."""
    if not tables:
        return
    for c in groupe.conteneurs.all():
        c.tables.add(*tables)
    _reparer_chaine_apres_depart(groupe, tables)


def _reparer_chaine_apres_depart(groupe, tables):
    """Les tables qui quittent un groupe emportaient des liens de chaîne (partie_precedente, partie_total, partie_index) :
    laissés en place, la table restante la plus proche gardait pour « partie précédente » une table sortie (« partie_suivante »
    d'un autre groupe, totaux faux : « 3/3 » pour 2 tables). On coupe les liens des partantes, on recolle la chaîne des
    restantes (la suivante pointe sur la précédente restante la plus proche) et on remet les totaux (et la numérotation d'un
    groupe créé par découpage) en ordre."""
    from django.db.models import F
    partants = {t.id for t in tables}
    precedente = dict(TableDynamique.objects.filter(groupe=groupe).values_list("id", "partie_precedente_id"))
    restantes = list(TableDynamique.objects.filter(groupe=groupe).exclude(id__in=partants)
                     .order_by(F("partie_index").asc(nulls_last=True), "id"))
    TableDynamique.objects.filter(id__in=partants).update(partie_precedente=None, partie_total=None, partie_index=None)

    def _restante_precedente(pid):
        while pid in partants:
            pid = precedente.get(pid)
        return pid

    for r in restantes:
        voulue = _restante_precedente(precedente.get(r.id))
        if voulue != precedente.get(r.id):
            TableDynamique.objects.filter(id=r.id).update(partie_precedente_id=voulue)
    if restantes and any(r.partie_total is not None for r in restantes):
        TableDynamique.objects.filter(id__in=[r.id for r in restantes]).update(partie_total=len(restantes))
        if groupe.cree_automatiquement:
            for rang, r in enumerate(restantes, start=1):
                TableDynamique.objects.filter(id=r.id).update(partie_index=rang)


@transaction.atomic
def normaliser(entreprise):
    """Remet d'aplomb une hiérarchie déjà incohérente (données antérieures) : chaque table n'a qu'une place.
    - table groupée rangée seule dans un conteneur → retirée, son groupe prend sa place si besoin ;
    - table ou groupe présent dans plusieurs conteneurs → gardé dans le premier seulement."""
    conteneurs = list(ConteneurTables.objects.filter(entreprise=entreprise).order_by("id"))
    for c in conteneurs:
        for t in c.tables.filter(groupe__isnull=False).select_related("groupe"):
            c.tables.remove(t)
            if not t.groupe.conteneurs.exists():
                c.groupes.add(t.groupe)
    vus_t, vus_g = set(), set()
    for c in conteneurs:
        for t in c.tables.all():
            if t.id in vus_t:
                c.tables.remove(t)
            vus_t.add(t.id)
        for g in c.groupes.all():
            if g.id in vus_g:
                c.groupes.remove(g)
            vus_g.add(g.id)


@transaction.atomic
def remplacer_groupe_par_table(groupe, table):
    """FUSION du groupe : là où le groupe était rangé (conteneurs), la table fusionnée prend sa place — une seule place, pas de doublon."""
    for c in list(groupe.conteneurs.all()):
        c.groupes.remove(groupe)
        c.tables.add(table)


@transaction.atomic
def remplacer_table_par_groupe(groupe, tables):
    """DÉFUSION : là où la table fusionnée était rangée, le groupe reprend sa place."""
    ids = [t.id for t in tables]
    for c in _conteneurs_de_tables(groupe.entreprise, ids):
        c.tables.remove(*ids)
        c.groupes.add(groupe)
