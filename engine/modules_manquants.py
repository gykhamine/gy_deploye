"""Module facultatif absent : l'import ne bloque jamais le démarrage ; à l'endroit où le module est requis, l'utilisateur voit un pop-up
traduit (« fonctionnalité bloquée, contactez l'administrateur ») avec les canaux de contact configurés (WhatsApp, e-mail).

Un seul point, aucune vue modifiée : le middleware attrape le ModuleNotFoundError d'une vue, ou un message « No module named '…' » renvoyé
en JSON d'erreur par une vue qui l'avait déjà capturé, et répond 424 (en-tête X-Module-Manquant). static/engine/js/module_manquant.js,
injecté dans chaque page HTML, ouvre le pop-up pour tout fetch / XMLHttpRequest qui reçoit cette réponse ; une navigation reçoit une page qui
l'ouvre elle-même. Les contacts se règlent dans ReglagePlateforme (page « Entreprises » du super-administrateur)."""
import json
import re
from urllib.parse import quote

from django.http import HttpResponse, JsonResponse

from .i18n import t, tf

LOCAUX = {"engine", "IA", "gyplatform"}   # un module du projet manquant est un bogue, pas une option non installée
FONCTIONS = {m: f for f, ms in {
    "vision": ("cv2",), "audio": ("pydub", "soundfile", "librosa", "noisereduce", "pywt"), "video": ("imageio_ffmpeg",),
    "cartes": ("folium",), "images": ("cairosvg", "pillow_heif", "svglib", "trimesh"), "ia": ("jax", "jaxlib", "flax", "optax", "h5py"),
    "base": ("psycopg", "psycopg2", "pymysql", "pymongo", "oracledb"), "stats": ("statsmodels", "polars", "sklearn"),
    "excel": ("python_calamine", "xlrd"), "securite": ("geoip2",)}.items() for m in ms}
_MOT = re.compile(r"No module named '([\w.]+)'")
_CLES = ("titre", "contacter", "fermer", "absent", "whatsapp", "mail")


def _racine(module):
    return (module or "").split(".")[0]


def _contacts(texte, sujet):
    """(lien WhatsApp, lien e-mail) selon les réglages de la plateforme ; '' pour un canal non configuré."""
    try:
        from .acces_entreprise import _reglage, numero_whatsapp
        reglage = _reglage()
        numero, adresse = numero_whatsapp(reglage.whatsapp_numero), (reglage.email_contact or "").strip()
    except Exception:   # base indisponible : le pop-up s'affiche quand même, sans contact
        return "", ""
    return (f"https://wa.me/{numero}?text={quote(texte)}" if numero else "",
            f"mailto:{quote(adresse)}?subject={quote(sujet)}&body={quote(texte)}" if adresse else "")


def donnees(module):
    fonction = t("module.manquant.fonction." + FONCTIONS.get(_racine(module), "autre"))
    texte = tf("module.manquant.message", fonction=fonction, module=module)
    wa, mail = _contacts(tf("module.manquant.wa_message", fonction=fonction, module=module),
                         tf("module.manquant.mail_sujet", fonction=fonction))
    return {"erreur": texte, "code": "module_manquant", "module": module, "whatsapp": wa, "mail": mail,
            "textes": {c: t("module.manquant." + c) for c in _CLES}}


class ModuleManquantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _reponse(self, request, module):
        d = donnees(module)
        if "text/html" in request.headers.get("Accept", "") and not request.path.startswith("/api/"):
            page = ("<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'></head><body>"
                    "<script type=application/json id=gy-mm>" + json.dumps(d).replace("<", "\\u003c") + "</script></body></html>")
            r = HttpResponse(page, status=424)
        else:
            r = JsonResponse(d, status=424)
        r["X-Module-Manquant"] = module
        r["Cache-Control"] = "no-store"
        return r

    def process_exception(self, request, exception):
        if isinstance(exception, ModuleNotFoundError) and exception.name and _racine(exception.name) not in LOCAUX:
            return self._reponse(request, exception.name)

    def __call__(self, request):
        r = self.get_response(request)
        if getattr(r, "streaming", False) or request.path.startswith("/admin/"):
            return r
        type_ = r.get("Content-Type", "")
        if r.status_code >= 400 and type_.startswith("application/json") and len(r.content) < 20000:   # vue qui a capturé l'erreur elle-même
            m = _MOT.search(r.content.decode("utf-8", "ignore"))
            if m and _racine(m.group(1)) not in LOCAUX:
                r, type_ = self._reponse(request, m.group(1)), ""
        if type_.startswith("text/html") and r.status_code in (200, 424) and b"module_manquant.js" not in r.content:
            from django.templatetags.static import static
            balise = f'<script src="{static("engine/js/module_manquant.js")}"></script>'.encode()
            tete = re.search(rb"<head(?:\s[^>]*)?>", r.content[:30000], re.I)
            if tete:
                r.content = r.content[:tete.end()] + balise + r.content[tete.end():]
                if r.has_header("Content-Length"):
                    r["Content-Length"] = str(len(r.content))
        return r
