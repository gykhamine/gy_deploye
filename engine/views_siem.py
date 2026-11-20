"""Page SIEM (super-administrateur) + endpoint d'ingestion signé.

Protection du SIEM lui-même (« assume breach ») :
  - super-administrateur Django, par SESSION (une clé API Bearer, même d'un super-admin, est refusée) ;
  - double authentification SMS : le SIEM s'adapte — si elle est activée (profil du super-administrateur), la connexion exige déjà le code SMS ; sinon il fonctionne quand même ;
  - ré-authentification récente (mot de passe, 10 min) pour consulter les données et pour toute action ;
  - liste d'IP autorisées (GYPLATFORM_SIEM_IPS, notation CIDR) ; vide = pas de restriction ;
  - chaque consultation de la page, chaque ré-authentification et chaque changement de règle entrent dans la blockchain système ;
  - AUCUNE route de suppression d'événement : seule existe la purge de rétention signée (commande siem_surveiller --purger).
Ingestion : POST /api/siem/ingerer signé HMAC (clé, horodatage, nonce anti-rejeu, limite de débit), pour les collecteurs d'autres machines.
"""
import hashlib
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
import hmac
import ipaddress
import json
import os
import time
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate
from .siem_cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from . import siem_noyau as siem
from . import siem_incident
from . import siem_recherche
from . import siem_regles
from .i18n import t
from .ratelimit import limiter
from .views_erreur import reponse_erreur
from engine.i18n import tf as _siem_tf  # noqa: E402

DELAI_REAUTH_S = 600
TOLERANCE_ING_S = 60
MAX_CORPS_ING = 64 * 1024


def _corps(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


def _ip_autorisee(ip: str) -> bool:
    liste = list(getattr(settings, "GYPLATFORM_SIEM_IPS", []))
    if not liste:
        return True
    try:
        adr = ipaddress.ip_address(ip)
        return any(adr in ipaddress.ip_network(x, strict=False) for x in liste)
    except ValueError:
        return False


def _reauth_recente(request) -> bool:
    return time.time() - float(request.session.get("siem_reauth", 0)) < DELAI_REAUTH_S


def _construire_garde(vue, page, reauth):
    """Superuser + session + IP autorisée (+ ré-authentification récente si `reauth`)."""
    @wraps(vue)
    def enveloppe(request, *args, **kwargs):
        ip = siem.adresse_ip(request)
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}") if page else JsonResponse({"erreur": t("engine/views_siem.py:g:69:115")}, status=401)
        refus = refus_audit = None      # refus : texte affiché, dans la langue de l'utilisateur ; refus_audit : texte français, stable, pour le journal d'audit
        if not request.user.is_superuser or request.META.get("HTTP_AUTHORIZATION", "").startswith("Bearer "):
            refus, refus_audit = t("engine/views_siem.py:g:76:25"), _i18n_t("rg2.views_siem.1")
        elif not _ip_autorisee(ip):
            refus, refus_audit = t("engine/views_siem.py:g:78:25"), _i18n_t("rg2.views_siem.2")
        if refus:
            siem.alerter("siem_acces_refuse", "haute", _siem_tf("rg4.siem.msg.64", v1=request.user.get_username(), refus_audit=refus_audit), utilisateur=request.user,
                         ip=ip, dedoublonner_s=300)
            return reponse_erreur(request, 403, message=refus) if page else JsonResponse({"erreur": refus}, status=403)
        if reauth and not page and not _reauth_recente(request):
            return JsonResponse({"erreur": t("engine/views_siem.py:g:84:48"), "reauth": True}, status=403)
        return vue(request, *args, **kwargs)
    return enveloppe


def _garde(vue):
    """Données et actions : superuser + 2FA + IP + ré-authentification récente."""
    return _construire_garde(vue, page=False, reauth=True)


def _garde_sans_reauth(vue):
    """Ré-authentification elle-même : mêmes contrôles, sans exiger une ré-authentification déjà faite."""
    return _construire_garde(vue, page=False, reauth=False)


def _garde_page(vue):
    return _construire_garde(vue, page=True, reauth=False)


# ── Page ────────────────────────────────────────────────────────────────────

@_garde_page
@ensure_csrf_cookie
def vue_siem(request):
    siem._bloc_systeme(request.user, "siem_consultation", {"vue": "page", "ip": siem.adresse_ip(request)})
    return render(request, "engine/siem.html", {"delai_reauth": DELAI_REAUTH_S})




@_garde_sans_reauth
@require_POST
def api_siem_reauth(request):
    mdp = str(_corps(request).get("mot_de_passe", ""))
    ip = siem.adresse_ip(request)
    n = siem.compter(f"siem_reauth:{request.user.pk}", 600)
    if n > 5:
        siem.alerter("siem_reauth_repetee", "haute", _siem_tf("rg4.siem.msg.60", v1=request.user.get_username()), utilisateur=request.user,
                     ip=ip, dedoublonner_s=600)
        return JsonResponse({"erreur": t("engine/views_siem.py:g:123:44")}, status=429)
    if authenticate(request, username=request.user.get_username(), password=mdp) is None:
        return JsonResponse({"erreur": t("engine/views_siem.py:g:125:44")}, status=403)
    request.session["siem_reauth"] = time.time()
    siem._bloc_systeme(request.user, "siem_consultation", {"vue": "reauth", "ip": ip})
    return JsonResponse({"ok": True, "valide_s": DELAI_REAUTH_S})




# ── Lecture ─────────────────────────────────────────────────────────────────

def _lignes_chaine(actions, limite=200):
    """Dernières transactions SIEM de la blockchain système (source de vérité des alertes), les plus récentes d'abord."""
    from .models import BlocSysteme
    sortie = []
    for b in BlocSysteme.objects.order_by("-index")[:3000]:
        for tx in reversed(b.transactions or []):
            if isinstance(tx, dict) and tx.get("action") in actions:
                sortie.append({"bloc": b.index, "hash": b.hash[:16], "ts": tx.get("ts") or b.timestamp, "action": tx["action"],
                               "utilisateur": tx.get("utilisateur", ""), **(tx.get("details") or {})})
                if len(sortie) >= limite:
                    return sortie
    return sortie


@_garde
@require_GET
def api_siem_etat(request):
    from .models import BlocSysteme
    cfg = siem.config()
    alertes = _lignes_chaine(("siem_alerte",), 500)
    par_gravite = {g: 0 for g in siem.GRAVITES}
    limite = time.time() - 86400
    for a in alertes:
        if a["ts"] >= limite:
            par_gravite[a.get("gravite", "moyenne")] = par_gravite.get(a.get("gravite", "moyenne"), 0) + 1
    ok_chaine, premier = BlocSysteme.chaine_valide()
    verif = cache.get("siem:derniere_verification")
    lots = siem.lots_masques()
    incident = siem_incident.etat()   # les compteurs et l'incident en cours ne sont JAMAIS masqués : seul l'historique l'est
    if not request.GET.get("avec_masques"):
        incident["recents"] = [r for r in incident.get("recents", []) if not siem_recherche.masque(_el_incident(r), lots)]
    return JsonResponse({
        "lots_masques": [{"id": l["id"], "nom": l["nom"], "par": l["par"], "ts": l["ts"], "jusqua": l["jusqua"]} for l in lots],
        "mode": cfg["mode"], "lockdown": cfg["lockdown"], "jours_observation": round(siem.jours_observation(), 1),
        "observation_requise_jours": siem.OBSERVATION_JOURS, "session_stricte": cfg["session_stricte"],
        "seuils": cfg["seuils"], "ips_exemptes": cfg["ips_exemptes"], "bris_de_glace": cfg["bris_de_glace"],
        "chaine_systeme": {"valide": ok_chaine, "premier_bloc_fautif": premier, "blocs": BlocSysteme.objects.count()},
        "derniere_verification": verif, "dernier_ancrage": cache.get("siem:dernier_ancrage"),
        "battements": siem.derniers_battements(), "silence_collecteur_s": cfg["silence_collecteur_s"], "maintenant": time.time(),
        "alertes_24h": par_gravite, "incident": incident, "protections": {
            "ip_restreintes": bool(getattr(settings, "GYPLATFORM_SIEM_IPS", [])),
            "double_facteur": bool(getattr(settings, "GYPLATFORM_SIEM_SANS_2FA", False)),
            "ancrage_fichier": bool(getattr(settings, "GYPLATFORM_SIEM_ANCRAGE_FICHIER", "")),
            "ancrage_distant": bool(getattr(settings, "GYPLATFORM_SIEM_ANCRAGE_URL", "")),
            "cle_environnement": bool(os.environ.get("GYPLATFORM_SIEM_CLE", "").strip())},
    })




def _limite(request):
    try:
        return max(1, min(int(request.GET.get("limite", 100)), 500))
    except ValueError:
        return 100


def _el_alerte(a):
    """Alerte (ligne de la blockchain système) → élément de recherche."""
    return {"ts": a["ts"], "numero": a["bloc"], "type": a.get("regle") or a["action"],
            "champs": {k: v for k, v in a.items() if k not in ("ts", "hash")}, "hash": a["hash"]}


def _el_evenement(e):
    """Événement collecté (JournalAudit « SIEM:… ») → élément de recherche."""
    d = e.get("details") or {}
    return {"ts": e["ts"], "numero": e["id"], "type": e["type"],
            "champs": {"utilisateur": e["utilisateur"], "entreprise": e["entreprise"], "ip": e["ip"],
                       **{k: (json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v) for k, v in d.items()}}}


def _el_incident(r):
    return {"ts": r.get("ouvert") or 0, "type": r.get("niveau", ""), "champs": {k: (", ".join(v) if isinstance(v, list) else v) for k, v in r.items()}}


def _recherche(request):
    """(critères de la recherche en cours, lots masqués actifs, afficher aussi les masqués ?). Les mêmes paramètres que la page blockchain."""
    return siem_recherche.criteres(request.GET), siem.lots_masques(), bool(request.GET.get("avec_masques"))


def _resultat(request, charger, vers_el, limite):
    """Recherche + masquage sur ce que `charger(n)` ramène. Sans recherche ni lot, on ne charge que la page affichée ; sinon on regarde plus loin."""
    c, lots, avec = _recherche(request)
    lignes = charger(5000 if (c["actifs"] or lots) else limite)
    types = siem_recherche.types_presents([vers_el(x) for x in lignes])
    gardees, caches = siem_recherche.filtrer(lignes, vers_el, c, lots, avec)
    return {"evenements": gardees[:limite], "types": types, "nb": len(gardees), "nb_masques": caches}


@_garde
@require_GET
def api_siem_alertes(request):
    limite = _limite(request)
    actions = ("siem_alerte", "siem_reponse", "siem_lockdown", "siem_config", "siem_rotation_cle", "siem_consultation", "siem_purge",
               "siem_masquage", "siem_reaffichage") if request.GET.get("tout") else ("siem_alerte", "siem_reponse")
    return JsonResponse(_resultat(request, lambda n: _lignes_chaine(actions, n), _el_alerte, limite))


def _evenements(n, type_=""):
    from .models import JournalAudit
    qs = JournalAudit.objects.filter(action__startswith="SIEM:").order_by("-id")
    if type_:
        qs = qs.filter(action="SIEM:" + type_[:40])
    return [{"id": j.id, "ts": j.horodatage.timestamp(), "type": j.action[5:], "utilisateur": j.utilisateur_id, "entreprise": j.entreprise_id,
             "ip": j.ip, "details": j.details} for j in qs[:n]]


@_garde
@require_GET
def api_siem_evenements(request):
    """Événements collectés (JournalAudit, action « SIEM:… »), du plus récent au plus ancien."""
    return JsonResponse(_resultat(request, lambda n: _evenements(n, request.GET.get("type", "")), _el_evenement, _limite(request)))


@_garde
@require_POST
def api_siem_masquer(request):
    """Masque un lot : tout ce qui répond aux critères de la recherche en cours (sans critère : tout l'historique existant). Rien n'est supprimé ;
    le masquage est inscrit dans la chaîne (qui, quand, quels critères). Refusé si l'inscription échoue."""
    corps = _corps(request)
    cr = corps.get("criteres") if isinstance(corps.get("criteres"), dict) else {}
    c, deja = siem_recherche.criteres(cr), siem.lots_masques()
    n = (len(siem_recherche.filtrer(_lignes_chaine(("siem_alerte", "siem_reponse"), 3000), _el_alerte, c, deja)[0])
         + len(siem_recherche.filtrer(_evenements(5000), _el_evenement, c, deja)[0]))
    lot = siem.masquer_lot(request.user, cr, siem.adresse_ip(request))
    if not lot:
        return JsonResponse({"erreur": t("engine/views_siem.py:masquer_echec")}, status=503)
    return JsonResponse({"ok": True, "lot": lot["lot"], "n": n})


@_garde
@require_POST
def api_siem_reafficher(request):
    """Réaffiche un lot (`lot` = son identifiant) ou tout (`lot` = « tout »)."""
    lot = str(_corps(request).get("lot") or "")
    if not lot:
        return JsonResponse({"erreur": t("engine/views_siem.py:reafficher_lot_manquant")}, status=400)
    if not siem.reafficher_lot(request.user, lot, siem.adresse_ip(request)):
        return JsonResponse({"erreur": t("engine/views_siem.py:reafficher_echec")}, status=503)
    return JsonResponse({"ok": True})


@_garde
@require_GET
def api_siem_risques(request):
    """Scores de risque en cours (comptes et IP) : lus dans le cache partagé, du plus élevé au plus faible."""
    from django.contrib.auth import get_user_model
    from .models import JournalAudit
    candidats_ip = set(JournalAudit.objects.filter(source="siem").exclude(ip="").order_by("-id").values_list("ip", flat=True)[:300])
    candidats_u = set(JournalAudit.objects.filter(source="siem", utilisateur__isnull=False).order_by("-id")
                      .values_list("utilisateur_id", flat=True)[:300])
    noms = dict(get_user_model().objects.filter(pk__in=candidats_u).values_list("pk", "username"))
    comptes = [{"cle": u, "nom": noms.get(u, str(u)), "score": round(siem.score_risque("compte", u), 1), "verrouille": siem.compte_verrouille(u)}
               for u in candidats_u]
    ips = [{"cle": i, "score": round(siem.score_risque("ip", i), 1), "bloquee_s": int(siem.ip_bloquee(i))} for i in candidats_ip]
    tri = lambda lst: sorted([x for x in lst if x["score"] >= 1 or x.get("verrouille") or x.get("bloquee_s")], key=lambda x: -x["score"])[:50]  # noqa: E731
    return JsonResponse({"comptes": tri(comptes), "ips": tri(ips)})


# ── Actions (toutes derrière la ré-authentification récente) ────────────────

@_garde
@require_POST
def api_siem_config(request):
    corps = _corps(request)
    try:
        cfg = siem.modifier_config(corps.get("config") or {}, request.user, siem.adresse_ip(request), forcer_actif=bool(corps.get("forcer")))
    except (ValueError, TypeError) as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({"ok": True, "mode": cfg["mode"]})


@_garde
@require_POST
def api_siem_lockdown(request):
    siem.definir_lockdown(bool(_corps(request).get("actif")), request.user, siem.adresse_ip(request))
    return JsonResponse({"ok": True, "lockdown": siem.lockdown_actif()})


@_garde
@require_POST
def api_siem_verifier(request):
    ok, anomalies = siem.verifier_chaines()
    cache.set("siem:derniere_verification", {"t": time.time(), "ok": ok, "anomalies": anomalies[:50]}, timeout=None)
    if not ok:
        siem.alerter("chaine_rompue", "critique", _siem_tf("rg4.siem.msg.61", v1=len(anomalies)), details={"anomalies": anomalies[:20]},
                     dedoublonner_s=60)
    return JsonResponse({"ok": ok, "anomalies": anomalies[:100]})


@_garde
@require_GET
def api_siem_sante(request):
    """Santé du moteur (base, caches, Redis, fichiers Parquet, mémoire de recherche, erreurs récentes) — voir siem_sante.py."""
    from . import siem_sante
    return JsonResponse(siem_sante.collecter())


@_garde
@require_POST
def api_siem_sante_parquet(request):
    """Vérifie (et, si demandé, répare) les fichiers Parquet des tables EAV ; chaque passage entre dans la blockchain système."""
    from . import siem_sante
    corps = _corps(request)
    reparer = bool(corps.get("reparer"))
    try:
        apres = max(0, int(corps.get("apres") or 0))
    except (TypeError, ValueError):
        apres = 0
    siem._bloc_systeme(request.user, "siem_consultation", {"vue": "sante_parquet", "reparer": reparer, "apres": apres, "ip": siem.adresse_ip(request)})
    return JsonResponse(siem_sante.verifier_parquet(reparer=reparer, apres=apres))


@_garde
@require_POST
def api_siem_cycle(request):
    return JsonResponse({"etapes": siem_regles.cycle(force=True)})


@_garde
@require_POST
def api_siem_rebaser(request):
    cible = _corps(request).get("cible")
    ip = siem.adresse_ip(request)
    if cible == "code":
        n = len(siem_regles.poser_reference_code(request.user, ip))
        return JsonResponse({"ok": True, "fichiers": n})
    return JsonResponse({"erreur": t("engine/views_siem.py:g:303:40")}, status=400)


@_garde
@require_POST
def api_siem_incident_clore(request):
    """Clôture manuelle de l'incident en cours (inscrite dans la blockchain système avec le nom de l'administrateur)."""
    return JsonResponse({"ok": siem_incident.clore(request.user, siem.adresse_ip(request))})


@_garde
@require_POST
def api_siem_rotation(request):
    try:
        cle_id = siem.faire_tourner_cle(request.user, siem.adresse_ip(request))
    except ValueError as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({"ok": True, "cle_id": cle_id})


@_garde
@require_GET
def api_siem_fantome(request):
    """Modèle fantôme du SIEM : maturité de ses trois têtes, comptes et messages les plus suspects, état du système (jamais de texte de message)."""
    from . import siem_fantome_suivi
    return JsonResponse(siem_fantome_suivi.etat())


@_garde
@require_POST
def api_siem_deverrouiller(request):
    corps = _corps(request)
    if corps.get("type") == "compte":
        siem.deverrouiller_compte(corps.get("cle"))
        from . import siem_fantome_suivi
        siem_fantome_suivi.retour_humain("compte", corps.get("cle"), 0.0)   # verrou levé : le modèle fantôme apprend que ce compte était normal
    elif corps.get("type") == "ip":
        siem.debloquer_ip(str(corps.get("cle", "")))
    else:
        return JsonResponse({"erreur": t("engine/views_siem.py:g:332:41")}, status=400)
    siem._bloc_systeme(request.user, "siem_reponse", {"deverrouillage": corps.get("type"), "cle": str(corps.get("cle")), "ip": siem.adresse_ip(request)})
    return JsonResponse({"ok": True})


# ── Ingestion signée (collecteurs d'autres machines) ────────────────────────

def cle_ingestion(cle_id) -> bytes | None:
    """Secret d'ingestion dérivé de la clé du SIEM (jamais la clé de scellement elle-même) : à donner au collecteur distant."""
    cle = siem.cle_par_id(cle_id)
    return None if cle is None else hmac.new(cle, b"ingestion", hashlib.sha256).hexdigest().encode()


@csrf_exempt
@require_POST
@limiter("siem_ingestion", max_appels=120, periode_secondes=60, methodes={"POST"})
def api_siem_ingerer(request):
    """En-têtes : X-Siem-Cle (identifiant de clé), X-Siem-Ts (secondes), X-Siem-Nonce (unique), X-Siem-Mac = HMAC-SHA256(secret_ingestion,
    ts + "." + nonce + "." + corps). Corps JSON : {"source", "type", "gravite", "message", "entreprise_id"?, "ip"?, "details"?}.
    Ajoute un signal de vie pour la source ; une alerte si `gravite` ≥ moyenne ; sinon un simple événement."""
    corps = request.body or b""
    if len(corps) > MAX_CORPS_ING:
        return JsonResponse({"erreur": _i18n_t("rg4.siem.corps_trop_volumineux")}, status=413)
    secret = cle_ingestion(request.headers.get("X-Siem-Cle", ""))
    ts, nonce, mac = request.headers.get("X-Siem-Ts", ""), request.headers.get("X-Siem-Nonce", ""), request.headers.get("X-Siem-Mac", "")
    ip = siem.adresse_ip(request)
    try:
        frais = abs(time.time() - int(ts)) <= TOLERANCE_ING_S
    except ValueError:
        frais = False
    attendu = hmac.new(secret, f"{ts}.{nonce}.".encode() + corps, hashlib.sha256).hexdigest() if secret else ""
    if not (secret and frais and 8 <= len(nonce) <= 64 and hmac.compare_digest(attendu, mac)):
        siem.alerter("ingestion_refusee", "moyenne", _siem_tf("rg4.siem.msg.62", ip=ip), ip=ip, dedoublonner_s=300)
        return JsonResponse({"erreur": _i18n_t("rg4.siem.signature_invalide")}, status=403)
    if not cache.add(f"siem:nonce:{nonce}", 1, timeout=2 * TOLERANCE_ING_S + 5):
        siem.alerter("ingestion_rejeu", "haute", _siem_tf("rg4.siem.msg.63", ip=ip), ip=ip, dedoublonner_s=300)
        return JsonResponse({"erreur": _i18n_t("rg2.views_siem.3")}, status=409)
    donnees = _corps(request)
    source = str(donnees.get("source", "externe"))[:40]
    siem.battement(source)
    gravite = donnees.get("gravite") if donnees.get("gravite") in siem.GRAVITES else "info"
    message = str(donnees.get("message", ""))[:400]
    details = dict(donnees.get("details") or {}, source=source)
    entreprise_id = donnees.get("entreprise_id") if isinstance(donnees.get("entreprise_id"), int) else None
    if gravite in ("moyenne", "haute", "critique"):
        siem.alerter(f"ext_{str(donnees.get('type', 'evenement'))[:40]}", gravite, f"[{source}] {message}", ip=str(donnees.get("ip", ip))[:45],
                     entreprise_id=entreprise_id, details=details, dedoublonner_s=60)
    else:
        siem.evenement(f"ext_{str(donnees.get('type', 'evenement'))[:40]}", entreprise_id=entreprise_id, ip=str(donnees.get("ip", ip))[:45], details=details)
    return JsonResponse({"ok": True})
