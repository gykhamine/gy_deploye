"""Choix de la langue de l'interface (Profil) : liste = fichiers de
engine/i18n_defaults/, nom du fichier = nom de la langue, défaut = anglais."""
import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from . import i18n


@require_http_methods(["GET", "POST"])
def api_langue(request):
    """GET : langues disponibles, langue courante, choix personnel (« » = langue par défaut du système) et langue du système.
    POST {"langue": "<nom du fichier>"} : choix PERSONNEL de l'utilisateur ; {"langue": ""} = revenir à la langue par défaut du système.
    Connecté : enregistré sur son compte (base de données), donc le même sur tous ses appareils ; toujours aussi en session + cookie
    d'un an (visiteur de la page de connexion)."""
    if request.method == "POST":
        try:
            corps = json.loads(request.body or b"{}")
        except ValueError:
            corps = {}
        demande = str(corps.get("langue", "") or "").strip()
        if demande and demande not in i18n.langues_disponibles():
            return JsonResponse({"erreur": i18n.t("engine/views_langue.py:inconnue")}, status=400)
        if request.user.is_authenticated:
            i18n.enregistrer_langue_utilisateur(request.user.pk, demande)
        if demande:
            i18n.definir_langue_session(request, demande)
        else:
            request.session.pop("langue", None)
        reponse = JsonResponse({"ok": True, "langue": demande or i18n.langue_serveur_effective(request), "choisie": demande})
        if demande:
            reponse.set_cookie("langue", demande, max_age=365 * 24 * 3600, samesite="Lax")
        else:
            reponse.delete_cookie("langue")
        return reponse
    return JsonResponse({
        "langues": list(i18n.langues_disponibles()),
        "noms": {c: i18n.NOMS_LANGUES.get(c, c) for c in i18n.langues_disponibles()},
        "courante": i18n.langue_courante(),
        "choisie": i18n.langue_choisie(request),
        "defaut": i18n.LANGUE_PAR_DEFAUT,
        "serveur": i18n.langue_entreprise(request) or i18n.langue_admin(),   # langue réglée par l'administrateur de l'entreprise (« » = automatique)
        "serveur_detectee": i18n.detecter_langue_serveur(),
    })


@require_http_methods(["POST"])
def api_langue_serveur(request):
    """POST {"langue": "<code>"} : l'administrateur règle la langue de l'interface de TOUT le serveur ; {"langue": ""} = automatique
    (langue détectée du serveur). Enregistrée en base (Entreprise.langue_interface) : elle revient d'elle-même à chaque démarrage."""
    from .views import _entreprise_courante
    from .views_marque import _est_admin
    if not request.user.is_authenticated or not _est_admin(request, _entreprise_courante(request)):
        return JsonResponse({"erreur": i18n.t("engine/views_langue.py:admin_requis")}, status=403)
    try:
        corps = json.loads(request.body or b"{}")
    except ValueError:
        corps = {}
    try:
        code = i18n.enregistrer_langue_admin(str(corps.get("langue", "") or ""), entreprise=_entreprise_courante(request))
    except ValueError:
        return JsonResponse({"erreur": i18n.t("engine/views_langue.py:inconnue")}, status=400)
    return JsonResponse({"ok": True, "serveur": code, "effective": i18n.langue_serveur_effective(request)})
