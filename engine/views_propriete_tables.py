"""Propriété partageable d'une table (engine/propriete_tables.py) : l'administrateur choisit à qui confier la table depuis les droits d'un agent."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import propriete_tables
from .delegation import a_pouvoir
from .models import Bloc, MembreEntreprise, TableDynamique
from .views import _entreprise_courante
from engine.i18n import t as _gt  # noqa: E402


@login_required
@require_http_methods(["GET", "POST"])
def api_proprietaires_table(request, table_id):
    entreprise = _entreprise_courante(request)
    admin = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if admin is None or not a_pouvoir(admin, "toutes_tables"):
        return JsonResponse({"erreur": _gt("rg5.msg.061")}, status=403)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    if request.method == "POST":
        try:
            avant = propriete_tables.proprietaires_de(table)
            apres = propriete_tables.definir_proprietaires(table, (json.loads(request.body or "{}")).get("proprietaires"))
        except (propriete_tables.ProprieteErreur, ValueError, TypeError):
            return JsonResponse({"erreur": _gt("rg5.msg.062")}, status=400)
        Bloc.ajouter_transaction(entreprise.id, {"type": "propriete_table", "table_id": table.id, "table": table.nom,
                                                 "agent_id": request.user.id, "avant": avant, "apres": apres})
    agents = MembreEntreprise.objects.filter(entreprise=entreprise, role="agent").select_related("utilisateur")
    return JsonResponse({
        "proprietaires": propriete_tables.proprietaires_de(table),
        "agents": [{"id": m.utilisateur_id, "nom": f"{m.utilisateur.first_name} {m.utilisateur.last_name}".strip() or m.utilisateur.username} for m in agents],
    })
