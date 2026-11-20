"""Personnalisation de la marque : feuille de couleurs, données publiques, enregistrement (admin) et page de réglage.
La logique est dans marque.py ; le logo se téléverse par la route existante /api/entreprise/logo/ ; le nom est celui de
l'entreprise ; nom et coordonnées se règlent aussi ici (route /api/entreprise/identite/), plus dans la page Équipe."""
import json
from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import marque
from . import i18n
from .i18n import t
from .models import Entreprise, MembreEntreprise
from .views import _entreprise_courante
from .views_erreur import reponse_erreur


def _entreprise_publique(request=None):
    """Entreprise dont le thème, le logo et le nom s'affichent pour CETTE requête (voir securite.entreprise_de_requete) : jamais celle d'un
    autre. Sans requête : l'entreprise d'une installation qui n'en compte qu'une, sinon aucune (identité d'origine)."""
    if request is None:
        une = list(Entreprise.objects.order_by("id")[:2])
        return une[0] if len(une) == 1 else None
    from .securite import entreprise_de_requete
    return entreprise_de_requete(request)


def _est_admin(request, entreprise):
    if request.user.is_superuser:
        return True
    from .delegation import a_pouvoir
    m = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    return a_pouvoir(m, "marque")


@require_http_methods(["GET"])
def marque_css(request):
    """La page qui demande la feuille (en-tête Referer, même origine) sert à y joindre les règles propres à cette page."""
    page = urlparse(request.META.get("HTTP_REFERER", "")).path or None
    rep = HttpResponse(marque.css(_entreprise_publique(request), page), content_type="text/css; charset=utf-8")
    rep["Cache-Control"] = "private, no-cache"   # propre à l'entreprise du compte : jamais servi par un cache partagé à une autre
    rep["Vary"] = "Referer, Cookie"
    return rep


@ensure_csrf_cookie
@require_http_methods(["GET", "POST"])
def api_marque(request):
    """GET : public (la page de connexion en a besoin). POST : administrateur seulement."""
    if request.method == "GET":
        entreprise_vue = _entreprise_publique(request)
        # ?edition=1 : réglages ENREGISTRÉS même si le thème est désactivé (page de personnalisation, administrateur seulement) ;
        # sans cela l'éditeur afficherait l'identité d'origine et un « Enregistrer » écraserait les réglages conservés.
        brut = request.GET.get("edition") == "1" and request.user.is_authenticated and entreprise_vue is not None and _est_admin(request, entreprise_vue)
        donnees = marque.lire(entreprise_vue, brut=brut)
        donnees["logo_defaut"] = static("engine/img/logo_agent_gy.png")   # même adresse que {% static %} dans les gabarits (nom haché inclus) : les pages sans logo dans leur gabarit l'utilisent
        if request.user.is_authenticated:
            entreprise = _entreprise_courante(request)
            if _est_admin(request, entreprise):   # l'éditeur visuel n'est offert qu'à l'administrateur
                donnees.update(admin=True, libelles=marque.libelles(), proprietes=marque.PROPRIETES, ombres=marque.OMBRES)
            from .views_modules import icone_modules_visible
            if icone_modules_visible(request, entreprise):   # icône « Modules » de l'en-tête : connecté seulement ; client : seulement s'il a des pages ouvertes
                donnees["modules"] = {"libelle": t("engine/marque.py:modules_icone")}
        rep = JsonResponse(donnees)
        rep["Cache-Control"] = "private, no-cache"
        rep["Vary"] = "Cookie"
        return rep
    if not request.user.is_authenticated:
        return JsonResponse({"erreur": t("engine/views_marque.py:erreur_admin")}, status=403)
    entreprise = _entreprise_courante(request)
    if not _est_admin(request, entreprise):
        return JsonResponse({"erreur": t("engine/views_marque.py:erreur_admin")}, status=403)
    try:
        corps = json.loads(request.body or "{}")
        if not isinstance(corps, dict):
            raise ValueError
    except ValueError:
        return JsonResponse({"erreur": t("engine/views_marque.py:erreur_corps")}, status=400)
    try:
        marque.enregistrer(entreprise, corps)
    except ValueError as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({"ok": True, **marque.lire(entreprise, brut=True)})   # l'administrateur qui règle voit ses réglages même thème désactivé


_CHAMPS_IDENTITE = (("nom", 100), ("adresse", 500), ("telephone_service_client", 60), ("email_contact", 254), ("site_web", 200))


@require_http_methods(["GET", "POST"])
def api_entreprise_identite(request):
    """Nom et coordonnées de l'entreprise (reprises sur chaque document généré) : réglés dans la page de personnalisation, administrateur seulement."""
    if not request.user.is_authenticated:
        return JsonResponse({"erreur": t("engine/views_marque.py:erreur_admin")}, status=403)
    entreprise = _entreprise_courante(request)
    if not _est_admin(request, entreprise):
        return JsonResponse({"erreur": t("engine/views_marque.py:erreur_admin")}, status=403)
    if request.method == "POST":
        try:
            corps = json.loads(request.body or "{}")
            if not isinstance(corps, dict):
                raise ValueError
        except ValueError:
            return JsonResponse({"erreur": t("engine/views_marque.py:erreur_corps")}, status=400)
        modifies = []
        for champ, longueur in _CHAMPS_IDENTITE:
            if champ in corps:
                valeur = corps[champ]
                if not isinstance(valeur, str):
                    return JsonResponse({"erreur": t("engine/views_marque.py:erreur_corps")}, status=400)
                setattr(entreprise, champ, valeur.strip()[:longueur])
                modifies.append(champ)
        if modifies:
            entreprise.save(update_fields=modifies)
    return JsonResponse({c: getattr(entreprise, c, "") or "" for c, _ in _CHAMPS_IDENTITE})


@require_http_methods(["POST"])
def api_marque_image(request):
    """Dépôt d'une image d'apparence (fond d'écran, motif) : administrateur seulement ; rend son adresse."""
    if not request.user.is_authenticated or not _est_admin(request, _entreprise_courante(request)) or not request.FILES.get("image"):
        return JsonResponse({"erreur": t("engine/views_marque.py:erreur_admin")}, status=403)
    try:
        return JsonResponse({"ok": True, "url": marque.enregistrer_image(request.FILES["image"])})
    except ValueError as e:
        return JsonResponse({"erreur": str(e)}, status=400)


@login_required
@ensure_csrf_cookie
def vue_personnalisation(request):
    entreprise = _entreprise_courante(request)
    if not _est_admin(request, entreprise):
        return reponse_erreur(request, 403, message=t("engine/views_marque.py:erreur_admin"))
    pref = "engine/templates/engine/personnalisation.html:couleur_"
    return render(request, "engine/personnalisation.html", {
        "entreprise": entreprise,
        "langues": [{"code": c, "nom": i18n.NOMS_LANGUES.get(c, c)} for c in i18n.langues_disponibles()],
        "langue_serveur": i18n.langue_admin(),
        "langue_detectee": i18n.NOMS_LANGUES.get(i18n.detecter_langue_serveur(), i18n.detecter_langue_serveur()),
        "variables": [{"nom": n, "defaut": d, "libelle": t(pref + n)} for n, d in marque.VARIABLES_FOND],
        "variables_fonds": [{"nom": n, "defaut": d, "libelle": t(pref + n)} for n, d in marque.VARIABLES_FONDS],
        "variables_bordures": [{"nom": n, "defaut": d, "libelle": t(pref + n)} for n, d in marque.VARIABLES_BORDURES],
        "variables_formes": [{"nom": n, "defaut": d, "libelle": t(pref + n)} for n, d in marque.VARIABLES_FORMES],
        "variables_document": [{"nom": n, "defaut": d, "libelle": t(pref + n)} for n, d in marque.VARIABLES_DOCUMENT],
        "variables_texte": [{"nom": n, "defaut": d, "libelle": t(pref + n)} for n, d in marque.VARIABLES_TEXTE],
        "polices": [{"cle": k, "libelle": t(pref.replace("couleur_", "police_") + k), "pile": v} for k, v in marque.POLICES.items()],
        "pages": [{"chemin": reverse("engine:" + n, **kw), "libelle": t("engine/marque.py:page_" + n)} for n, kw in (
            ("espace_client", {}), ("paiement_client", {}), ("publicites_client", {}), ("login", {}),
            ("acces_client", {"kwargs": {"slug_entreprise": entreprise.slug}}))],
        "limites": {"taille": (marque.TAILLE_MIN, marque.TAILLE_MAX), "barre": (marque.BARRE_MIN, marque.BARRE_MAX), "css": marque.LONGUEUR_CSS},
    })
