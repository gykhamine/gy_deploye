"""Page « Sauvegarde et restauration » de la plateforme — super-administrateur uniquement (mêmes contrôles que le SIEM :
superuser, jamais par clé API, IP autorisée) + mot de passe redemandé pour chaque action."""
import json
import re
import tempfile
from pathlib import Path

from django.contrib.auth import authenticate
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import sauvegarde_plateforme as sp
from . import siem_noyau as siem
from . import acces_entreprise as acces
from .i18n import t, tf
from .views_siem import _garde_sans_reauth

_CLES = ("titre", "intro", "mot_de_passe", "sauvegarder", "restaurer", "fichier", "attention", "moteur")


def _textes():
    return {c: t(f"plateforme.sauvegarde.{c}") for c in _CLES}


def _mot_de_passe_ok(request) -> bool:
    return authenticate(request, username=request.user.get_username(), password=str(request.POST.get("mot_de_passe", ""))) is not None


@_garde_sans_reauth
@ensure_csrf_cookie
@require_http_methods(["GET"])
def vue_sauvegarde(request):
    return render(request, "engine/sauvegarde_plateforme.html",
                  {"textes": _textes(), "moteur": sp._moteur(), "message": request.GET.get("m", "")[:200]})


@_garde_sans_reauth
@require_http_methods(["POST"])
def api_sauvegarde_creer(request):
    ip = siem.adresse_ip(request)
    if not _mot_de_passe_ok(request):
        return JsonResponse({"erreur": t("plateforme.sauvegarde.mdp_incorrect")}, status=403)
    chemin = sp.creer()
    siem._bloc_systeme(request.user, "sauvegarde_plateforme", {"evenement": "creation", "fichier": chemin.name, "ip": ip})
    return FileResponse(open(chemin, "rb"), as_attachment=True, filename=chemin.name, content_type="application/zip")


@_garde_sans_reauth
@require_http_methods(["POST"])
def api_sauvegarde_restaurer(request):
    ip = siem.adresse_ip(request)
    if not _mot_de_passe_ok(request):
        return JsonResponse({"erreur": t("plateforme.sauvegarde.mdp_incorrect")}, status=403)
    fichier = request.FILES.get("fichier")
    if fichier is None:
        return JsonResponse({"erreur": t("plateforme.sauvegarde.fichier_manquant")}, status=400)
    with tempfile.NamedTemporaryFile(suffix=".zip", dir=sp.dossier_sauvegardes(), delete=False) as tmp:
        for morceau in fichier.chunks():
            tmp.write(morceau)
    chemin = Path(tmp.name)
    try:
        resultat = sp.restaurer(chemin)
    except sp.SauvegardeErreur as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    finally:
        chemin.unlink(missing_ok=True)
    try:
        siem._bloc_systeme(request.user, "sauvegarde_plateforme", {"evenement": "restauration", "ip": ip, "securite": Path(resultat["securite"]).name})
    except Exception:   # la base vient d'être remplacée : l'inscription ne doit jamais faire échouer la réponse
        pass
    return JsonResponse({"ok": True, "securite": Path(resultat["securite"]).name, "message": t("plateforme.sauvegarde.restauree")})


# ── Essai, blocage et déblocage des entreprises (super-administrateur) ─────────────────────────────────────────────────────

_CLES_ENTREPRISES = ("titre", "intro", "col_nom", "col_etat", "col_fin", "col_actions", "etat_illimite", "etat_essai", "etat_actif",
                     "etat_echu", "etat_bloquee", "exemptee", "debloquer", "bloquer", "prolonger", "jours", "reglages_titre", "numero", "email",
                     "message", "essai_jours", "prolongation_jours", "enregistrer", "enregistre", "aucune", "erreur", "confirmer_bloquer")


def _textes_entreprises():
    return {c: t(f"plateforme.entreprises.{c}") for c in _CLES_ENTREPRISES}


def _corps_json(request):
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return {}
    return corps if isinstance(corps, dict) else {}


def _entreprise_json(e):
    return {"id": e.id, "nom": e.nom or "—", "etat": acces.etat(e), "fin": e.acces_jusqua.strftime("%Y-%m-%d") if e.acces_jusqua else "",
            "exemptee": acces.exemptee(e)}


def _reglage_json(r):
    return {"whatsapp_numero": r.whatsapp_numero, "email_contact": r.email_contact, "whatsapp_message": r.whatsapp_message, "essai_jours": r.essai_jours,
            "prolongation_jours": r.prolongation_jours}


@_garde_sans_reauth
@ensure_csrf_cookie
@require_http_methods(["GET"])
def vue_entreprises(request):
    textes = _textes_entreprises()
    textes["debloquer"] = tf("plateforme.entreprises.debloquer", jours="{jours}")
    return render(request, "engine/plateforme_entreprises.html", {"textes": textes})


@_garde_sans_reauth
@require_http_methods(["GET"])
def api_entreprises(request):
    from .models import Entreprise, ReglagePlateforme
    return JsonResponse({"entreprises": [_entreprise_json(e) for e in Entreprise.objects.order_by("id")],
                         "reglage": _reglage_json(ReglagePlateforme.charger())})


@_garde_sans_reauth
@require_http_methods(["POST"])
def api_entreprise_action(request, entreprise_id):
    from .models import Entreprise
    entreprise = get_object_or_404(Entreprise, pk=entreprise_id)
    corps = _corps_json(request)
    action = corps.get("action")
    if action == "bloquer":
        acces.bloquer(entreprise)
    elif action in ("debloquer", "prolonger"):
        try:
            jours = int(corps.get("jours") or 0) or None
        except (TypeError, ValueError):
            return JsonResponse({"erreur": t("plateforme.entreprises.erreur")}, status=400)
        if jours is not None and not 1 <= jours <= 3650:
            return JsonResponse({"erreur": t("plateforme.entreprises.erreur")}, status=400)
        acces.debloquer(entreprise, jours)
    else:
        return JsonResponse({"erreur": t("plateforme.entreprises.erreur")}, status=400)
    siem._bloc_systeme(request.user, "acces_entreprise", {"entreprise": entreprise.pk, "action": action, "fin": str(entreprise.acces_jusqua),
                                                         "ip": siem.adresse_ip(request)})
    return JsonResponse({"entreprise": _entreprise_json(entreprise)})


@_garde_sans_reauth
@require_http_methods(["POST"])
def api_reglage_plateforme(request):
    from .models import ReglagePlateforme
    corps = _corps_json(request)
    reglage = ReglagePlateforme.charger()
    numero = str(corps.get("whatsapp_numero", "")).strip()
    if numero and not acces.numero_whatsapp(numero):
        return JsonResponse({"erreur": t("plateforme.entreprises.erreur")}, status=400)
    email = str(corps.get("email_contact", "")).strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return JsonResponse({"erreur": t("plateforme.entreprises.erreur")}, status=400)
    try:
        essai, prolongation = int(corps.get("essai_jours", reglage.essai_jours)), int(corps.get("prolongation_jours", reglage.prolongation_jours))
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("plateforme.entreprises.erreur")}, status=400)
    if not (0 <= essai <= 3650 and 1 <= prolongation <= 3650):
        return JsonResponse({"erreur": t("plateforme.entreprises.erreur")}, status=400)
    reglage.whatsapp_numero, reglage.email_contact, reglage.whatsapp_message = numero, email[:254], str(corps.get("whatsapp_message", ""))[:1000]
    reglage.essai_jours, reglage.prolongation_jours = essai, prolongation
    reglage.save()
    siem._bloc_systeme(request.user, "reglage_plateforme", {"ip": siem.adresse_ip(request)})
    return JsonResponse({"reglage": _reglage_json(reglage), "message": t("plateforme.entreprises.enregistre")})


# ── Page vue par l'entreprise bloquée ─────────────────────────────────────────────────────────────────────────────────────

_CLES_SUSPENDU = ("titre", "consigne", "contacter", "contact_absent", "deconnexion")


@require_http_methods(["GET"])
def vue_acces_suspendu(request):
    if not request.user.is_authenticated:
        return redirect("/login/")
    entreprise = acces.AccesEntrepriseMiddleware._entreprise(request)
    if entreprise is None or not acces.est_refusee(entreprise):
        return redirect("/")
    fin = acces.date_affichee(entreprise.acces_jusqua)
    cle = "bloquee" if entreprise.bloquee else "echu"
    textes = {c: t(f"plateforme.acces.suspendu.{c}") for c in _CLES_SUSPENDU}
    textes["raison"] = tf(f"plateforme.acces.suspendu.{cle}", entreprise=entreprise.nom or "", fin=fin)
    response = render(request, "engine/acces_suspendu.html", {"textes": textes, "lien": acces.lien_whatsapp(entreprise)})
    response["Cache-Control"] = "no-store"
    return response
