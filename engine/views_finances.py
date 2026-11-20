"""Page et API des finances (caisse de l'entreprise) — logique dans finances.py, comptabilité dans comptabilite.py.

Réservé à l'administrateur et aux agents à qui il a accordé la vue « finances » (Employés). Les analyses ne sont pas refaites ici :
la page renvoie vers les pages d'analyse existantes.

  GET  /finances/                       la page
  GET  /api/finances/                   ?du=&au=&sens=&membre=&limite= : caisse, totaux de la période, écritures, catégories, habilitations
  POST /api/finances/entree/            {categorie, motif, montant, cle}   : entrée de caisse
  POST /api/finances/sortie/            {categorie, libelle, beneficiaire, montant, date} : sortie de caisse (charge)
  GET  /api/finances/portefeuilles/     qui a quoi, depuis quand, jusqu'à quand
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from engine.i18n import t
from . import comptabilite as _c, finances as _f, wallet as _w, wallet_especes as _e, wallet_flux as _flux
from .delegation import a_pouvoir as _a_pouvoir, liste_droits as _liste_droits
from .ratelimit import limiter
from .views_wallet_especes import _ctx, _entier, _erreur, _json, _membre_de, _montant


def _acces(request, *droits):
    """(entreprise, membre, None) si le membre a AU MOINS UN des `droits` de délégation demandés (administrateur : toujours)."""
    entreprise, membre = _ctx(request)
    if not _a_pouvoir(membre, *droits):
        return None, None, _erreur(t("engine/views_finances.py:refus"), 403)
    return entreprise, membre, None


@login_required
@ensure_csrf_cookie
def page_finances(request):
    entreprise, membre = _ctx(request)
    if not _f.peut_gerer(membre):
        from django.shortcuts import redirect
        return redirect("/profile/")
    return render(request, "engine/finances.html", {"entreprise": entreprise, "est_admin": membre.role == "admin", "droits": _liste_droits(membre),
                                                     "voit_journal": _a_pouvoir(membre, "caisse_entrees", "caisse_sorties", "journal_bilan", "comptabilite")})


@require_http_methods(["GET"])
@login_required
def api_finances(request):
    entreprise, membre, refus = _acces(request, "caisse_entrees", "caisse_sorties", "journal_bilan", "comptabilite")
    if refus:
        return refus
    cible = _membre_de(entreprise, request.GET.get("membre")) if request.GET.get("membre") else None
    caisse = _w.membre_caisse(entreprise)
    donnees = _f.releve(entreprise, request.GET.get("du"), request.GET.get("au"), request.GET.get("sens"), cible, _entier(request.GET.get("limite"), 300, 1, 1000))
    donnees.update({"caisse": _w.obtenir_wallet(caisse).solde if caisse else 0.0, "categories": _f.categories(),
                    "est_admin": membre.role == "admin", "droits": _liste_droits(membre),
                    "agents": _e.lister_habilites(entreprise) if _a_pouvoir(membre, "especes_habilitations") else []})
    return JsonResponse(donnees)


@require_http_methods(["POST"])
@csrf_protect
@login_required
@limiter("finances_ecriture", max_appels=60, periode_secondes=300, methodes={"POST"})
def api_entree(request):
    entreprise, membre, refus = _acces(request, "caisse_entrees")
    if refus:
        return refus
    d = _json(request)
    try:
        e, deja = _f.enregistrer_entree(entreprise, membre, d.get("categorie"), d.get("motif"), _montant(d.get("montant")), d.get("cle"))
    except ValueError as ex:
        return _erreur(str(ex))
    return JsonResponse({"ok": True, "deja_enregistre": deja, "ecriture": _c.ecriture_en_dict(e)})


@require_http_methods(["POST"])
@csrf_protect
@login_required
@limiter("finances_ecriture", max_appels=60, periode_secondes=300, methodes={"POST"})
def api_sortie(request):
    entreprise, membre, refus = _acces(request, "caisse_sorties")
    if refus:
        return refus
    d = _json(request)
    try:
        charge = _flux.enregistrer_charge(entreprise, membre, d.get("categorie"), d.get("libelle"), _montant(d.get("montant")), d.get("date"), d.get("beneficiaire"))
    except ValueError as ex:
        return _erreur(str(ex))
    return JsonResponse({"ok": True, "charge": _flux.charge_en_dict(charge)})


@require_http_methods(["GET"])
@login_required
def api_portefeuilles(request):
    entreprise, membre, refus = _acces(request, "portefeuilles")
    if refus:
        return refus
    return JsonResponse({"portefeuilles": _f.portefeuilles(entreprise)})
