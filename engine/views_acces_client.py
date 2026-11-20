"""Tables réservées à un client, réglées par l'AGENT depuis la table des agents et clients (bouton « Tables » de la ligne du client).

Même effet que MembreEntreprise.tables_autorisees posé sur un lecteur (voir views._tables_permises_client) : vide = toutes les tables
exposées ; sinon le client ne voit que les tables cochées, avec leurs lignes, tableaux de bord, KPI et pages d'analyse.
Droits : l'administrateur pour tout client ; un agent seulement pour les clients dont il a créé la fiche (agent responsable).
Le compte du client n'existe qu'à sa première connexion par code : il est créé ici, à l'identique, si l'agent règle ses tables avant.
"""
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .i18n import t
from .models import Enregistrement, MembreEntreprise, TableDynamique
from .views import _entreprise_courante, _interdit_table_non_exposee, _table_autorisee_agent
from .vue_groupe import tables_generales


def _membre_client(entreprise, enregistrement, creer=False):
    membre = MembreEntreprise.objects.filter(enregistrement_client=enregistrement).first()
    if membre or not creer:
        return membre
    utilisateur, cree = get_user_model().objects.get_or_create(username=f"client_e{entreprise.id}_r{enregistrement.id}")
    if cree:
        utilisateur.set_unusable_password()
        utilisateur.save(update_fields=["password"])
    membre, _ = MembreEntreprise.objects.get_or_create(
        utilisateur=utilisateur, entreprise=entreprise,
        defaults={"role": "lecteur", "table_cliente": enregistrement.table, "enregistrement_client": enregistrement})
    return membre


def _tables_proposees(chef, entreprise):
    """Tables que l'on peut réserver à un client : exposées aux clients, ordinaires, visibles par l'agent (un groupe = une table)."""
    qs = (TableDynamique.objects.filter(entreprise=entreprise).filter(Q(expose_client=True) | Q(masquer_formulaire_client=True))
          .exclude(est_table_membres=True).order_by("nom"))
    return [x for x in tables_generales([x for x in qs if _table_autorisee_agent(chef, x)])]


@login_required
@require_http_methods(["GET", "POST"])
def api_tables_client_ligne(request, table_id, ligne_id):
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise, est_table_membres=True, est_table_produits=False)
    enr = get_object_or_404(Enregistrement, id=ligne_id, table=table)
    chef = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if chef is None or chef.role not in ("admin", "agent") or (chef.role == "agent" and enr.cree_par_id != request.user.id):
        return JsonResponse({"erreur": t("engine/views_acces_client.py:g:56:41")}, status=403)
    refus = _interdit_table_non_exposee(request, table)
    if refus:
        return refus
    proposees = _tables_proposees(chef, entreprise)
    if request.method == "GET":
        membre = _membre_client(entreprise, enr)
        return JsonResponse({
            "tables": [{"id": x.id, "nom": getattr(x, "_nom_vue", None) or x.nom} for x in proposees],
            "autorisees": sorted(membre.tables_autorisees or []) if membre else [],
        })
    try:
        demandees = {int(i) for i in (json.loads(request.body or "{}").get("tables") or [])}
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({"erreur": t("engine/views_acces_client.py:g:70:41")}, status=400)
    valides = sorted(demandees & {x.id for x in proposees})
    membre = _membre_client(entreprise, enr, creer=True)
    membre.tables_autorisees = valides or None
    membre.save(update_fields=["tables_autorisees"])
    return JsonResponse({"autorisees": valides})
