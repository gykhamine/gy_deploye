"""API de gestion des données : renommer une table, la découper en plusieurs parties (règles : engine/decoupage_table.py).

  POST /api/tables/<id>/renommer/   {"nom": "..."}
  POST /api/tables/<id>/decouper/   {"parties": [{"nom": "...", "nb_lignes": 5000}, {"nom": "..."}], "grouper": false, "nom_groupe": "..."}
                                    ou {"nb_parties": 3, "noms": ["A", "B", "C"]} (répartition égale ; noms facultatifs)
  GET  /api/tables/<id>/decouper/   aperçu : nombre de lignes, plafond d'une partie, découpage égal proposé

Les messages sont écrits dans la langue de l'utilisateur (catalogues engine/i18n_defaults, clés « decoupage.* »).
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import decoupage_table as _dec
from . import tables_systeme
from .models import Bloc, TableDynamique
from .views import _entreprise_courante, _interdit_lecteur
from .views._part_006 import _serialiser_groupe_tables, _serialiser_table   # _part_006 n'exporte pas ses noms en « _ » par import *
from engine.i18n import t as _gt, tf as _gtf  # noqa: E402


def _erreur(e, statut=400):
    return JsonResponse({"erreur": _gtf(e.cle, **e.params), "code": e.cle}, status=statut)


def _table_modifiable(request, table_id):
    """(table, None) si la table de l'entreprise courante peut être modifiée par cet utilisateur, sinon (None, réponse d'erreur)."""
    refus = _interdit_lecteur(request)
    if refus:
        return None, refus
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=_entreprise_courante(request))
    refus = tables_systeme.refus_ecriture(table)
    return (None, refus) if refus else (table, None)


def _corps(request):
    try:
        corps = json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return corps if isinstance(corps, dict) else None


@login_required
@require_http_methods(["POST"])
def api_renommer_table(request, table_id):
    table, refus = _table_modifiable(request, table_id)
    if refus:
        return refus
    corps = _corps(request)
    if corps is None:
        return JsonResponse({"erreur": _gt("decoupage.err_requete")}, status=400)
    ancien = table.nom
    try:
        _dec.renommer_table(table, corps.get("nom"))
    except _dec.DecoupageError as e:
        return _erreur(e)
    if table.nom != ancien:
        Bloc.ajouter_transaction(table.entreprise_id, {"type": "renommer_table", "table_id": table.id, "ancien_nom": ancien, "nom": table.nom})
    return JsonResponse({"table": _serialiser_table(table)})


def _parties_depuis_corps(table, corps, total):
    """Liste de parties {"nom", "nb_lignes"} : soit donnée telle quelle (`parties`), soit une répartition égale (`nb_parties`)."""
    if isinstance(corps.get("parties"), list):
        return corps["parties"]
    try:
        n = int(corps.get("nb_parties"))
    except (TypeError, ValueError):
        raise _dec.DecoupageError("decoupage.err_nb_parties", min=2, max=_dec.PARTIES_MAX)
    if not 2 <= n <= _dec.PARTIES_MAX:
        raise _dec.DecoupageError("decoupage.err_nb_parties", min=2, max=_dec.PARTIES_MAX)
    noms = corps.get("noms") if isinstance(corps.get("noms"), list) else []
    defauts = _dec.noms_par_defaut(table.nom, n, _gt("decoupage.modele_nom"))
    return [{"nom": (noms[i] if i < len(noms) and str(noms[i]).strip() else defauts[i]), "nb_lignes": taille}
            for i, taille in enumerate(_dec.repartir_egalement(total, n))]


@login_required
@require_http_methods(["GET", "POST"])
def api_decouper_table(request, table_id):
    if request.method == "GET":
        refus = _interdit_lecteur(request)
        if refus:
            return refus
        table = get_object_or_404(TableDynamique, id=table_id, entreprise=_entreprise_courante(request))
        total = table.enregistrements.count()
        try:
            _dec.verifier_decoupable(table)
            raison = None
        except _dec.DecoupageError as e:
            raison = _gtf(e.cle, **e.params)
        return JsonResponse({"nom": table.nom, "nb_lignes": total, "plafond_partie": _dec.PLAFOND_PARTIE, "parties_max": _dec.PARTIES_MAX,
                             "decoupable": raison is None and not tables_systeme.est_systeme(table), "raison": raison})

    table, refus = _table_modifiable(request, table_id)
    if refus:
        return refus
    corps = _corps(request)
    if corps is None:
        return JsonResponse({"erreur": _gt("decoupage.err_requete")}, status=400)
    try:
        parties = _parties_depuis_corps(table, corps, table.enregistrements.count())
        groupe, tables = _dec.decouper_table(table, parties, grouper=bool(corps.get("grouper")),
                                              nom_groupe=(corps.get("nom_groupe") or "").strip() or None,
                                              modele_description=_gt("decoupage.groupe_description"))
    except _dec.DecoupageError as e:
        return _erreur(e)
    Bloc.ajouter_transaction(table.entreprise_id, {"type": "decouper_table", "table_id": table.id, "groupe_id": groupe.id if groupe else None,
                                                   "parties": [{"id": t.id, "nom": t.nom} for t in tables]})
    return JsonResponse({"groupe": _serialiser_groupe_tables(groupe) if groupe else None, "tables": [_serialiser_table(t) for t in tables]}, status=201)
