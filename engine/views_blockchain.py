"""Endpoints pour paiement des données + blockchain ledger.

Unifié sur models.Bloc (la blockchain principale, persistée, par
entreprise) — l'ancien ledger local en mémoire (blockchain_ledger.py)
est supprimé pour ne plus avoir deux blockchains parallèles."""
from engine.i18n import t
import json
import hashlib
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.core.cache import cache
from .models import Bloc, TableDynamique
from .views import _entreprise_courante










@require_http_methods(["GET"])
@login_required
def historique_blockchain_table(request):
    """Retourne l'historique blockchain d'une table (models.Bloc)."""
    table_id = request.GET.get('table_id', '')
    if not table_id:
        return JsonResponse({'error': t("engine/views_blockchain.py:203:40")}, status=400)
    
    entreprise = _entreprise_courante(request)
    historique = [
        {"index": b.index, "timestamp": b.timestamp, "type": t.get("type"),
         "montant": t.get("donnees", {}).get("montant_paiement", 0), "hash_bloc": b.hash}
        for b in Bloc.objects.filter(entreprise=entreprise).order_by("index")
        for t in b.transactions if str(t.get("table_id")) == str(table_id)
    ]
    
    return JsonResponse({
        'table_id': table_id,
        'historique': historique,
        'integrite_verifiee': Bloc.chaine_valide(entreprise.id),
        'nombre_blocs': Bloc.objects.filter(entreprise=entreprise).count(),
    })

@require_http_methods(["GET"])
@login_required
def historique_action_cible(request):
    """Lignée d'un objet précis, tous types confondus (ex: cible_type=
    TableDynamique&cible_id=42) : « d'où vient cette donnée, qui l'a
    modifiée, par quelle Action, quand ? » (voir models.Bloc.historique_cible,
    Phase 4 du cahier des charges Ontology — Provenance). Généralise
    historique_blockchain_table (limité aux tables) à n'importe quelle
    cible d'Action (engine.actions.executer_action)."""
    cible_type = request.GET.get('cible_type', '').strip()
    cible_id = request.GET.get('cible_id', '').strip()
    if not cible_type or not cible_id:
        return JsonResponse({'error': t("engine/views_blockchain.py:232:40")}, status=400)

    entreprise = _entreprise_courante(request)
    return JsonResponse({
        'cible_type': cible_type,
        'cible_id': cible_id,
        'historique': Bloc.historique_cible(entreprise.id, cible_type, cible_id),
        'integrite_verifiee': Bloc.chaine_valide(entreprise.id),
    })


@require_http_methods(["GET"])
@login_required
def export_blockchain(request):
    """Exporte toute la blockchain (models.Bloc) de l'entreprise en JSON."""
    entreprise = _entreprise_courante(request)
    blocs = Bloc.objects.filter(entreprise=entreprise).order_by("index")
    return JsonResponse({
        'blockchain': [
            {"index": b.index, "timestamp": b.timestamp, "hash": b.hash,
             "previous_hash": b.previous_hash, "transactions": b.transactions}
            for b in blocs
        ],
        'integrite': Bloc.chaine_valide(entreprise.id),
        'nombre_blocs': blocs.count(),
    })
