"""HISTORIQUE DES TRACES D'ENQUÊTE (page d'ontologie) : la trace (les nœuds cliqués successivement et les liens entre eux) n'est plus perdue quand on
change de page ; elle est mémorisée au fil de l'enquête et les anciennes traces sont gardées dans un historique pour y revenir.

  GET    /api/ontologie/traces/?page=<id>        {"courante": {...} | null, "historique": [...]} — les traces de l'utilisateur pour cette page
  POST   /api/ontologie/traces/                  corps {page_id, noeuds, liens, focus, courante: true}  → réécrit la trace en cours
                                                 corps {page_id, noeuds, liens, focus, nom}              → enregistre sous un nom dans l'historique
  POST   /api/ontologie/traces/archiver/         corps {page_id, noeuds?, liens?, focus?, nom?} → la trace en cours (ou celle envoyée) passe dans
                                                 l'historique (si elle compte au moins 2 nœuds) et il n'y a plus de trace en cours
  PATCH  /api/ontologie/traces/<id>/             corps {nom?, epinglee?}
  DELETE /api/ontologie/traces/<id>/

Personnel : chaque utilisateur ne voit et ne touche que ses propres traces, par entreprise et par page d'ontologie. Droits d'accès = ceux de la page
d'ontologie (vue « dashboard_bi »). Le serveur ne stocke que des identifiants de nœuds et de relations, jamais le contenu des tables.
"""
from engine.i18n import t
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import MembreEntreprise, TraceOntologie
from .views import _entreprise_courante, _vue_autorisee

MAX_NOEUDS = 500           # nœuds par trace
MAX_LIENS = 1500           # liens par trace
MAX_HISTORIQUE = 40        # entrées non épinglées gardées par page (les plus anciennes sont retirées)
MAX_EPINGLEES = 100        # entrées épinglées par page
TAILLE_MAX_CORPS = 400_000


def _contexte(request):
    """(entreprise, refus éventuel)."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not _vue_autorisee(membre, "dashboard_bi"):
        return None, JsonResponse({"erreur": t("engine/views_traces_ontologie.py:interdit")}, status=403)
    return entreprise, None


def _page_ref(valeur):
    try:
        return int(valeur) if valeur not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _corps(request):
    if len(request.body or b"") > TAILLE_MAX_CORPS:
        return None
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return None
    return corps if isinstance(corps, dict) else None


def _nettoyer(corps):
    """(noeuds, liens, focus) validés et bornés — jamais de confiance dans ce que le navigateur envoie."""
    noeuds, vus = [], set()
    for n in (corps.get("noeuds") or [])[:MAX_NOEUDS]:
        if isinstance(n, str) and n and len(n) <= 200 and n not in vus:
            vus.add(n); noeuds.append(n)
    liens = []
    for l in (corps.get("liens") or [])[:MAX_LIENS]:
        if not isinstance(l, dict):
            continue
        de, vers = l.get("de"), l.get("vers")
        if de not in vus or vers not in vus:
            continue
        a = l.get("a")
        if isinstance(a, (list, tuple)) and len(a) == 3 and all(isinstance(x, str) and len(x) <= 200 for x in a):
            a = list(a)
        else:
            a = None
        liens.append({"de": de, "vers": vers, "a": a})
    focus = corps.get("focus") if isinstance(corps.get("focus"), str) and corps.get("focus") in vus else (noeuds[-1] if noeuds else "")
    return noeuds, liens, focus


def _nom(corps):
    return str(corps.get("nom") or "").strip()[:150]


def _json(tr):
    return {
        "id": tr.id, "nom": tr.nom, "noeuds": tr.noeuds, "liens": tr.liens, "focus": tr.focus,
        "courante": tr.courante, "epinglee": tr.epinglee,
        "maj_le": tr.maj_le.isoformat(), "cree_le": tr.cree_le.isoformat(),
    }


def _base(entreprise, request, page_ref):
    return TraceOntologie.objects.filter(entreprise=entreprise, utilisateur=request.user, page_ref=page_ref)


def _elaguer(entreprise, request, page_ref):
    """Garde les MAX_HISTORIQUE entrées non épinglées les plus récentes ; les épinglées ne s'effacent jamais d'elles-mêmes."""
    ids = list(_base(entreprise, request, page_ref).filter(courante=False, epinglee=False).order_by("-maj_le", "-id").values_list("id", flat=True))
    if len(ids) > MAX_HISTORIQUE:
        TraceOntologie.objects.filter(id__in=ids[MAX_HISTORIQUE:]).delete()


def _archiver(entreprise, request, page_ref, noeuds, liens, focus, nom):
    """Fait passer dans l'historique la trace donnée (sinon la trace en cours) ; retire la trace en cours. Renvoie l'entrée créée ou None."""
    base = _base(entreprise, request, page_ref)
    courante = base.filter(courante=True).first()
    if len(noeuds) < 2 and courante is not None and len(courante.noeuds) >= 2:
        noeuds, liens, focus = courante.noeuds, courante.liens, courante.focus   # rien (ou presque rien) d'envoyé : on garde ce qui était mémorisé
        nom = nom or courante.nom
    base.filter(courante=True).delete()
    if len(noeuds) < 2:
        return None
    derniere = base.filter(courante=False).order_by("-maj_le", "-id").first()
    if derniere is not None and derniere.noeuds == noeuds:     # même trace que la dernière archivée : pas de doublon
        if nom and not derniere.nom:
            derniere.nom = nom; derniere.save(update_fields=["nom", "maj_le"])
        return derniere
    tr = TraceOntologie.objects.create(
        entreprise=entreprise, utilisateur=request.user, page_ref=page_ref,
        nom=nom, noeuds=noeuds, liens=liens, focus=focus, courante=False,
    )
    _elaguer(entreprise, request, page_ref)
    return tr


@login_required
@require_http_methods(["GET", "POST"])
def api_traces_ontologie(request):
    entreprise, refus = _contexte(request)
    if refus:
        return refus
    if request.method == "GET":
        page_ref = _page_ref(request.GET.get("page"))
        base = _base(entreprise, request, page_ref)
        courante = base.filter(courante=True).first()
        histo = base.filter(courante=False).order_by("-epinglee", "-maj_le", "-id")
        return JsonResponse({"courante": _json(courante) if courante else None, "historique": [_json(x) for x in histo]})

    corps = _corps(request)
    if corps is None:
        return JsonResponse({"erreur": t("engine/views_traces_ontologie.py:invalide")}, status=400)
    page_ref = _page_ref(corps.get("page_id"))
    noeuds, liens, focus = _nettoyer(corps)
    if not noeuds:
        return JsonResponse({"erreur": t("engine/views_traces_ontologie.py:vide")}, status=400)
    base = _base(entreprise, request, page_ref)
    if corps.get("courante"):
        tr = base.filter(courante=True).first()
        if tr is None:
            tr = TraceOntologie(entreprise=entreprise, utilisateur=request.user, page_ref=page_ref, courante=True)
        tr.noeuds, tr.liens, tr.focus = noeuds, liens, focus
        tr.save()
        return JsonResponse({"ok": True, "trace": _json(tr)})
    # Enregistrement sous un nom dans l'historique (la trace en cours continue).
    if base.filter(courante=False).count() >= MAX_HISTORIQUE + MAX_EPINGLEES:
        return JsonResponse({"erreur": t("engine/views_traces_ontologie.py:plein")}, status=400)
    tr = TraceOntologie.objects.create(
        entreprise=entreprise, utilisateur=request.user, page_ref=page_ref,
        nom=_nom(corps), noeuds=noeuds, liens=liens, focus=focus, courante=False,
    )
    _elaguer(entreprise, request, page_ref)
    return JsonResponse({"ok": True, "trace": _json(tr)})


@login_required
@require_http_methods(["POST"])
def api_traces_ontologie_archiver(request):
    entreprise, refus = _contexte(request)
    if refus:
        return refus
    corps = _corps(request)
    if corps is None:
        return JsonResponse({"erreur": t("engine/views_traces_ontologie.py:invalide")}, status=400)
    page_ref = _page_ref(corps.get("page_id"))
    noeuds, liens, focus = _nettoyer(corps)
    tr = _archiver(entreprise, request, page_ref, noeuds, liens, focus, _nom(corps))
    return JsonResponse({"ok": True, "trace": _json(tr) if tr else None})


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_trace_ontologie_detail(request, trace_id):
    entreprise, refus = _contexte(request)
    if refus:
        return refus
    tr = TraceOntologie.objects.filter(id=trace_id, entreprise=entreprise, utilisateur=request.user).first()
    if tr is None:
        return JsonResponse({"erreur": t("engine/views_traces_ontologie.py:introuvable")}, status=404)
    if request.method == "DELETE":
        tr.delete()
        return JsonResponse({"ok": True})
    corps = _corps(request)
    if corps is None:
        return JsonResponse({"erreur": t("engine/views_traces_ontologie.py:invalide")}, status=400)
    champs = []
    if "nom" in corps:
        tr.nom = _nom(corps); champs.append("nom")
    if "epinglee" in corps:
        veut = bool(corps["epinglee"])
        if veut and not tr.epinglee and _base(entreprise, request, tr.page_ref).filter(epinglee=True).count() >= MAX_EPINGLEES:
            return JsonResponse({"erreur": t("engine/views_traces_ontologie.py:plein")}, status=400)
        tr.epinglee = veut; champs.append("epinglee")
    if champs:
        tr.save(update_fields=champs + ["maj_le"])
    return JsonResponse({"ok": True, "trace": _json(tr)})
