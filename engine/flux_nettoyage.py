"""MÉNAGE DES CAPTURES DE FLUX : rien ne reste derrière quand une ligne, une colonne ou un flux disparaît.

  * une LIGNE supprimée (ou une colonne média vidée de ses valeurs) emporte le fichier de sa capture — sauf si une autre ligne
    s'y réfère encore (une valeur recopiée par une relation, par exemple) ;
  * supprimer une colonne média (flux_image / flux_video / flux_son) d'une table supprime le FLUX qui y enregistre : un flux ne
    vit pas sans la colonne qui reçoit ses captures. Supprimer la table liée a donc le même effet ;
  * un flux supprimé emporte aussi ses captures encore en attente d'une ligne (son dossier d'archives).
Les fichiers ne sont retirés qu'une fois la transaction validée (un retour en arrière ne perd rien). Branché par apps.py.
"""
import logging
import os
import re
from types import SimpleNamespace
from urllib.parse import unquote

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete, pre_delete

from . import flux_media as fm
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)
_COLONNES = "|".join(fm.SLUG_COLONNE.values())


def _chemin_relatif(url):
    """Chemin sous MEDIA_ROOT d'une capture de colonne média (champs/<table>/flux_xxx/<fichier>), sinon None."""
    if not url or not str(url).startswith(settings.MEDIA_URL):
        return None
    rel = unquote(str(url)[len(settings.MEDIA_URL):])
    return rel if re.fullmatch(r"champs/[^/]+/(%s)/[^/]+" % _COLONNES, rel) else None


def _retirer_si_orpheline(url, rel):
    from .models import ValeurChamp
    if ValeurChamp.objects.filter(valeur=url).exists():      # une autre ligne s'y réfère encore
        return
    try:
        default_storage.delete(rel)
    except OSError:
        logger.warning(_gt("rg6.msg.011"), rel)


def _valeur_supprimee(sender, instance, **kwargs):
    rel = _chemin_relatif(instance.valeur)
    if rel:
        url = instance.valeur
        transaction.on_commit(lambda: _retirer_si_orpheline(url, rel))


def _purger_archives(entreprise_id, flux_id):
    ancien = SimpleNamespace(id=flux_id, table=SimpleNamespace(entreprise_id=entreprise_id))
    for f in fm.lister_fichiers(ancien):
        try:
            os.remove(fm.dossier(entreprise_id) / f["nom"])
        except OSError:
            logger.warning(_gt("rg6.msg.012"), f["nom"])


def _champ_supprime(sender, instance, **kwargs):
    from .models import ChampDynamique, TableDynamique
    if instance.type_champ in fm.TYPES_FLUX:               # le flux disparaît : ses captures en attente aussi
        entreprise_id, flux_id = instance.table.entreprise_id, instance.id
        transaction.on_commit(lambda: _purger_archives(entreprise_id, flux_id))
        return
    if instance.type_champ not in fm.GENRES or instance.slug != fm.SLUG_COLONNE[instance.type_champ]:
        return
    table = instance.table
    ids = {p.id for p in fm._parties(table)}
    for flux in ChampDynamique.objects.filter(type_champ__in=fm.TYPES_FLUX, table__entreprise_id=table.entreprise_id):
        try:
            lie = int(fm.config_champ(flux).get("table_id") or 0)
        except (TypeError, ValueError):
            continue
        if lie in ids and instance.type_champ in fm.genres_flux(flux):
            TableDynamique.objects.filter(id=flux.table_id).delete()      # la table du flux (une cellule) et son champ


def brancher():
    from .models import ChampDynamique, ValeurChamp
    post_delete.connect(_valeur_supprimee, sender=ValeurChamp, dispatch_uid="flux_nettoyage_valeur")
    pre_delete.connect(_champ_supprime, sender=ChampDynamique, dispatch_uid="flux_nettoyage_champ")
