"""SUPPRESSION EN MASSE de tables et de groupes de tables.

Pourquoi ce module : `table.delete()` fait remonter TOUTES les lignes et TOUTES leurs valeurs EAV en mémoire (un signal `post_delete`
est branché sur ValeurChamp : flux_nettoyage), puis envoie un signal par valeur. Pour un groupe de plusieurs tables de ~32 000 lignes,
c'est des millions d'objets Python dans UNE transaction : des heures, et à la fin rien n'est supprimé (coupure, retour en arrière).

Ici :
  * les lignes partent par PAQUETS de LOT, chacun validé à part : une coupure ne ramène jamais en arrière, et relancer la suppression
    reprend où elle s'était arrêtée ;
  * chaque paquet est supprimé en SQL direct, en suivant les relations du modèle (CASCADE → on descend, SET_NULL → mise à NULL). Dès
    qu'un modèle de la chaîne a un signal de suppression, ou une règle inhabituelle (PROTECT…), on retombe sur la suppression ORM
    classique pour CE paquet : toujours correct, simplement plus lent ;
  * le seul signal court-circuité volontairement — le ménage des captures média de ValeurChamp — est rejoué à la main.
"""
import logging
import os
import shutil

from django.conf import settings
from django.db import models, transaction
from django.db.models import signals
from django.db.models.deletion import get_candidate_relations_to_delete

logger = logging.getLogger(__name__)

LOT = 2000   # lignes par paquet (× colonnes = valeurs EAV supprimées par requête)


def _a_des_signaux(modele) -> bool:
    return signals.pre_delete.has_listeners(modele) or signals.post_delete.has_listeners(modele)


def _purger(qs) -> None:
    """Supprime `qs` et tout ce qui en dépend, en SQL direct quand c'est sûr (voir l'en-tête du module)."""
    from .models import ValeurChamp
    modele = qs.model
    if modele is not ValeurChamp and _a_des_signaux(modele):   # ValeurChamp : son seul signal est rejoué plus bas
        qs.delete()
        return
    for rel in get_candidate_relations_to_delete(modele._meta):
        regle = rel.field.remote_field.on_delete
        if regle is models.DO_NOTHING:
            continue
        enfants = rel.related_model._base_manager.filter(**{rel.field.name + "__in": qs})
        if regle is models.CASCADE and rel.related_model is not modele:
            _purger(enfants)
        elif regle is models.SET_NULL:
            enfants.update(**{rel.field.name: None})
        else:   # PROTECT, RESTRICT, SET(...), auto-référence : l'ORM sait faire, lentement
            qs.delete()
            return
    if modele is ValeurChamp:
        from . import flux_nettoyage   # captures média : le fichier part (une fois le paquet validé) si plus aucune ligne n'y renvoie
        for v in qs.filter(valeur__startswith=settings.MEDIA_URL + "champs/").only("valeur"):
            flux_nettoyage._valeur_supprimee(ValeurChamp, v)
    qs._raw_delete(qs.db)


def purger_lignes(table, avance=None) -> int:
    """Vide `table` de ses lignes, paquet par paquet. `avance(n)` est appelé après chaque paquet. Retourne le nombre de lignes retirées."""
    from .models import Enregistrement
    total = 0
    while True:
        ids = list(Enregistrement.objects.filter(table=table).values_list("id", flat=True)[:LOT])
        if not ids:
            return total
        with transaction.atomic():
            _purger(Enregistrement.objects.filter(id__in=ids))
        total += len(ids)
        if avance:
            avance(len(ids))


def tables_du_groupe(groupe) -> list:
    """Tout ce qu'un groupe possède : sa table fusionnée éventuelle (qui ne figure pas parmi ses parties), puis les parties."""
    return list(groupe.tables_de_fusion.all()) + list(groupe.tables.all())


def supprimer_tables(tables, groupe=None, agent_id=None, progression_cb=None) -> None:
    """Supprime les tables (et le groupe, s'il est donné) avec toutes leurs données, leurs scripts, leurs fichiers Parquet.
    `progression_cb(pct)` : 0-99, pour le suivi d'une tâche de fond. Chaque suppression laisse une trace dans la blockchain."""
    from .models import Bloc, Enregistrement
    tables = list(tables)
    total = max(1, Enregistrement.objects.filter(table__in=tables).count())
    fait = [0]

    def avance(n):
        fait[0] += n
        if progression_cb:
            progression_cb(min(99, fait[0] * 100 // total))

    entreprise_id = tables[0].entreprise_id if tables else (groupe.entreprise_id if groupe else None)
    for table in tables:
        purger_lignes(table, avance)
        slug, nom, table_id = table.slug, table.nom, table.id
        with transaction.atomic():
            table.delete()   # les lignes sont déjà parties : il ne reste que les colonnes et le reste de la structure
        Bloc.ajouter_transaction(entreprise_id, {"type": "suppression_table", "table_id": table_id, "table": nom, "agent_id": agent_id})
        shutil.rmtree(os.path.join(settings.MEDIA_ROOT, "scripts_agent", slug), ignore_errors=True)   # best-effort, comme avant
    if groupe is not None:
        trace = {"type": "suppression_groupe_tables", "groupe_id": groupe.id, "groupe": groupe.nom, "nb_tables": len(tables), "agent_id": agent_id}
        groupe.delete()
        Bloc.ajouter_transaction(entreprise_id, trace)
        from .groupe_fusion import _invalider_caches
        _invalider_caches(entreprise_id)


def lancer(entreprise, utilisateur, tables, groupe=None):
    """Point d'entrée des vues. Petit volume : supprimé dans la requête. Gros volume (SUPPRESSION_ASYNC_A_PARTIR_DE lignes, 2 000 par
    défaut) : tâche de fond suivie comme un import (TacheImport, polling /api/import/<id>/statut/) — la requête rend la main tout de
    suite. Celery injoignable : on supprime dans la requête plutôt que d'échouer. Retourne (dict JSON, code HTTP)."""
    from .models import Enregistrement, TacheImport
    tables = list(tables)
    seuil = getattr(settings, "SUPPRESSION_ASYNC_A_PARTIR_DE", 2000)
    if Enregistrement.objects.filter(table__in=tables).count() >= seuil:
        nom = groupe.nom if groupe else (tables[0].nom if tables else "")
        tache = TacheImport.objects.create(entreprise=entreprise, utilisateur=utilisateur, nom_fichier=nom[:255], statut="en_attente")
        try:
            from . import tasks
            tasks.supprimer_tables_async.delay(tache.id, [t.id for t in tables], groupe.id if groupe else None)
            return {"tache_id": tache.id, "nom_fichier": nom, "asynchrone": True}, 202
        except Exception:
            logger.exception("Suppression en masse : tâche de fond impossible, suppression dans la requête")
            tache.delete()
    supprimer_tables(tables, groupe, utilisateur.id)
    return {"ok": True}, 200
