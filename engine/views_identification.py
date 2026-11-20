"""API de l'identification des personnes (voir identification.py). Réservée à l'administrateur pour tout ce qui touche aux données
biométriques (réglage, enregistrement) ; les agents peuvent identifier sur un flux ; un client (lecteur) n'a accès à rien."""
from engine.i18n import t
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import flux_media as fm
from . import identification as ident
from .models import ChampDynamique, MembreEntreprise, TableDynamique
from .views import _entreprise_courante, _interdit_lecteur
from .delegation import a_pouvoir as _a_pouvoir


def _admin(request):
    """None si l'utilisateur est administrateur de l'entreprise, sinon une réponse 403."""
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=_entreprise_courante(request)).first()
    if request.user.is_superuser or _a_pouvoir(membre, "identification"):
        return None
    return JsonResponse({"erreur": t("engine/views_identification.py:admin_requis")}, status=403)


def _corps(request):
    try:
        c = json.loads(request.body or "{}")
        return c if isinstance(c, dict) else {}
    except ValueError:
        return {}


def _visages_json(visages):
    return [{"boite": list(v["boite"]), "connu": v["connu"], "nom": v["nom"], "score": v["score"], "categorie": v["categorie"], "infos": v["infos"]} for v in visages]


@login_required
@require_http_methods(["GET", "POST"])
def api_identification_config(request):
    """GET : réglage, moteur actif (et sa fiabilité), nombre de personnes enregistrées. POST : enregistre le réglage."""
    bloque = _admin(request)
    if bloque:
        return bloque
    e = _entreprise_courante(request)
    if request.method == "POST":
        cfg, erreur = ident.valider_config(_corps(request))
        if erreur:
            return JsonResponse({"erreur": erreur}, status=400)
        tables = set(TableDynamique.objects.filter(entreprise=e, id__in=[x["table_id"] for x in cfg["tables"]]).values_list("id", flat=True))
        if len(tables) != len({x["table_id"] for x in cfg["tables"]}):
            return JsonResponse({"erreur": t("engine/views_identification.py:table_inconnue")}, status=400)
        for x in cfg["tables"]:
            ident.assurer_colonne_empreinte(x["table_id"], x["colonne_empreinte"])
        ident.ecrire_config(e.id, cfg)
    moteur, erreur = ident.moteur_disponible(ident.lire_config(e.id).get("moteur", "auto"))
    reg, err_reg = ident.registre(e.id) if ident.lire_config(e.id).get("tables") else (None, None)
    return JsonResponse({"config": ident.lire_config(e.id), "categories": list(ident.CATEGORIES), "inconnus": list(ident.INCONNUS),
                         "moteur": {"nom": moteur.nom if moteur else "", "fiable": bool(moteur and moteur.fiable), "erreur": erreur or err_reg or ""},
                         "personnes": len(reg) if reg is not None else 0})


@login_required
@require_http_methods(["POST"])
def api_identification_enregistrer(request):
    """Calcule l'empreinte d'une ligne dont la photo vient d'être ajoutée : {table_id, ligne_id}."""
    bloque = _admin(request)
    if bloque:
        return bloque
    e, c = _entreprise_courante(request), _corps(request)
    table = get_object_or_404(TableDynamique, id=c.get("table_id"), entreprise=e)
    if not table.enregistrements.filter(id=c.get("ligne_id")).exists():
        return JsonResponse({"erreur": t("engine/views_identification.py:ligne_inconnue")}, status=404)
    n, code = ident.enregistrer_ligne(e.id, table.id, c["ligne_id"])
    if code:
        return JsonResponse({"erreur": t("engine/views_identification.py:err_" + code) if code in ("aucun_visage", "plusieurs_visages", "trop_petit") else code, "code": code}, status=422)
    return JsonResponse({"empreintes": n})


@login_required
@require_http_methods(["POST"])
def api_identification_photo(request):
    """Identifie les visages d'une photo envoyée (champ de formulaire « image »), sans rien enregistrer."""
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    f = request.FILES.get("image")
    if f is None:
        return JsonResponse({"erreur": t("engine/views_identification.py:image_requise")}, status=400)
    img = ident.lire_image_octets(f.read())
    if img is None:
        return JsonResponse({"erreur": t("engine/views_identification.py:image_requise")}, status=400)
    visages, moteur, erreur = ident.identifier_image(_entreprise_courante(request).id, img)
    if erreur:
        return JsonResponse({"erreur": erreur}, status=503)
    return JsonResponse({"visages": _visages_json(visages), "moteur": moteur.nom, "fiable": moteur.fiable})


@login_required
@require_http_methods(["POST"])
def api_flux_identifier(request, champ_id):
    """Identifie les visages visibles MAINTENANT sur un flux (caméra, drone, téléphone), sans rien enregistrer ni activer la surveillance."""
    champ = get_object_or_404(ChampDynamique, id=champ_id, type_champ__in=fm.TYPES_VIDEO, table__entreprise=_entreprise_courante(request))
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    try:
        h = fm.hub(champ, audio=False)
        _, jpg = h.attendre(0, delai=8)
    except (ValueError, ImportError, OSError) as e:
        return JsonResponse({"erreur": str(e)}, status=503)
    img = ident.lire_image_octets(jpg)
    if img is None:
        return JsonResponse({"erreur": h.erreur or t("engine/views_identification.py:pas_d_image")}, status=503)
    visages, moteur, erreur = ident.identifier_image(champ.table.entreprise_id, img)
    if erreur:
        return JsonResponse({"erreur": erreur}, status=503)
    return JsonResponse({"visages": _visages_json(visages), "moteur": moteur.nom, "fiable": moteur.fiable, "taille": [img.shape[1], img.shape[0]]})
