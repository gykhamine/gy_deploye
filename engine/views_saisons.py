"""Saisons (environnements de données) : liste, bascule par utilisateur, ouverture de l'écriture (admin). Voir cycle.restaurer_en_saison."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from .delegation import a_pouvoir as _a_pouvoir

from .models import MembreEntreprise
from engine.i18n import t as _gt  # noqa: E402


def _membres(utilisateur):
    return list(MembreEntreprise.objects.filter(utilisateur=utilisateur).select_related("entreprise"))   # tri (-actif, pk) : le premier est l'environnement actif


@login_required
def api_saisons(request):
    """GET : environnements de l'utilisateur. POST {entreprise_id} : bascule. POST {ecriture: bool} : admin, saison restaurée active."""
    membres = _membres(request.user)
    if request.method == "POST" and membres:
        corps = json.loads(request.body or "{}")
        if "ecriture" in corps:
            if not _a_pouvoir(membres[0], "saisons") or not membres[0].entreprise.saison_de_id:
                return JsonResponse({"erreur": _gt("rg5.msg.028")}, status=403)
            membres[0].entreprise.ecriture_ouverte = bool(corps["ecriture"])
            membres[0].entreprise.save(update_fields=["ecriture_ouverte"])
        elif any(m.entreprise_id == corps.get("entreprise_id") for m in membres):
            for m in membres:
                MembreEntreprise.objects.filter(pk=m.pk).update(actif=m.entreprise_id == corps["entreprise_id"])
        membres = _membres(request.user)
    return JsonResponse({"saisons": [{"entreprise_id": m.entreprise_id, "nom": m.entreprise.nom, "restauree": bool(m.entreprise.saison_de_id),
                                      "ecriture": m.entreprise.ecriture_ouverte, "actif": i == 0, "admin": m.role == "admin"} for i, m in enumerate(membres)]})
