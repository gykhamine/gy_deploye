"""Essai d'une semaine, blocage à l'échéance, déblocage de 30 jours — état d'accès d'une entreprise.

États : illimite (aucune échéance, entreprises d'avant l'essai) · essai · actif (échéance future) · echu (échéance passée) · bloquee
(blocage manuel). Une entreprise echue ou bloquee est refusée par AccesEntrepriseMiddleware : pages → /acces-suspendu/ (bouton
« Contacter » WhatsApp), API → 402 JSON. Jamais bloqués : le super-administrateur Django et les entreprises dont il est membre.
Numéro, message, durée de l'essai et d'un déblocage : page /plateforme/entreprises/ (super-administrateur).
"""
import re
from datetime import timedelta
from urllib.parse import quote

from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone

from .i18n import langue_courante, t, tf

_LANGUES_JJMMAAAA = ("fr", "es", "pt", "de", "it", "nl", "ru")


def date_affichee(d) -> str:
    """Date lisible dans la langue courante : jj/mm/aaaa (fr, es, pt, de, it, nl, ru), sinon aaaa-mm-jj."""
    if not d:
        return ""
    return d.strftime("%d/%m/%Y" if langue_courante() in _LANGUES_JJMMAAAA else "%Y-%m-%d")

LIBRES = ("/acces-suspendu/", "/api/acces-suspendu/", "/logout/", "/login/", "/api/langue/", "/static/", "/admin/")


def _reglage():
    from .models import ReglagePlateforme
    return ReglagePlateforme.charger()


def fin_essai_par_defaut():
    """Échéance d'une entreprise qui vient d'être créée : maintenant + durée de l'essai (7 jours par défaut ; 0 = pas d'essai limité)."""
    try:
        jours = _reglage().essai_jours
    except Exception:   # table pas encore migrée : le comportement d'avant (pas d'échéance) vaut mieux qu'une erreur
        return None
    return timezone.now() + timedelta(days=jours) if jours else None


def etat(entreprise, maintenant=None) -> str:
    maintenant = maintenant or timezone.now()
    if entreprise.bloquee:
        return "bloquee"
    if entreprise.acces_jusqua is None:
        return "illimite"
    if entreprise.acces_jusqua < maintenant:
        return "echu"
    return "essai" if entreprise.en_essai else "actif"


def est_refusee(entreprise) -> bool:
    """True si l'accès doit être refusé (échu ou bloqué), hors exemption du super-administrateur."""
    if etat(entreprise) not in ("echu", "bloquee"):
        return False
    return not exemptee(entreprise)


def exemptee(entreprise) -> bool:
    from .models import MembreEntreprise
    return MembreEntreprise.objects.filter(entreprise=entreprise, utilisateur__is_superuser=True).exists()


def debloquer(entreprise, jours=None):
    """Lève le blocage et fixe l'échéance à jours (défaut : durée d'un déblocage, 30) après la fin actuelle, ou après maintenant si elle est passée."""
    jours = int(jours) if jours else _reglage().prolongation_jours
    base = max(timezone.now(), entreprise.acces_jusqua or timezone.now())
    entreprise.bloquee = False
    entreprise.en_essai = False
    entreprise.acces_jusqua = base + timedelta(days=jours)
    entreprise.save(update_fields=["bloquee", "en_essai", "acces_jusqua"])


def bloquer(entreprise):
    entreprise.bloquee = True
    entreprise.save(update_fields=["bloquee"])


def numero_whatsapp(brut) -> str:
    """Chiffres seuls au format international (sans +, espaces ni tirets), '' si le numéro est inutilisable."""
    chiffres = re.sub(r"\D", "", str(brut or ""))
    return chiffres if 8 <= len(chiffres) <= 15 else ""


def lien_whatsapp(entreprise) -> str:
    reglage = _reglage()
    numero = numero_whatsapp(reglage.whatsapp_numero)
    if not numero:
        return ""
    fin = date_affichee(entreprise.acces_jusqua)
    modele = reglage.whatsapp_message.strip() or t("plateforme.acces.message_defaut")
    try:
        message = modele.format(entreprise=entreprise.nom or "", fin=fin)
    except (KeyError, IndexError, ValueError):
        message = modele   # accolades inconnues saisies par le super-administrateur : texte tel quel
    return f"https://wa.me/{numero}?text={quote(message)}"


class AccesEntrepriseMiddleware:
    """Refuse toute requête d'un utilisateur dont l'entreprise est échue ou bloquée (après authentification et clé API)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or user.is_superuser or request.path.startswith(LIBRES):
            return self.get_response(request)
        entreprise = self._entreprise(request)
        if entreprise is None or not est_refusee(entreprise):
            return self.get_response(request)
        if request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({"erreur": t("plateforme.acces.api_suspendu"), "code": "acces_suspendu", "url": "/acces-suspendu/"}, status=402)
        return redirect("/acces-suspendu/")

    @staticmethod
    def _entreprise(request):
        from .models import MembreEntreprise
        from .securite import membre_de_requete
        membre = membre_de_requete(request, MembreEntreprise.objects.select_related("entreprise"))
        return membre.entreprise if membre is not None else None
