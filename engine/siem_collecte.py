"""SIEM — collecte exhaustive, branchée sur l'existant (aucun modèle).

  - SiemMiddleware : tous les refus 4xx (GET compris, pas seulement les modifications), les lectures sensibles (exports, wallet, données
    clients), l'empreinte de session (IP + user-agent), le blocage d'IP/de compte, le lockdown. Placé AVANT CleAPIAuthMiddleware pour voir
    aussi les 401 des clés API invalides. Les modifications restent journalisées par AuditMiddleware (déjà en place, non dupliqué).
  - Signaux de connexion de Django : échecs (sans toucher à vue_login), connexions réussies, voyage impossible.
  - Protection de JournalAudit : une ligne scellée ne peut plus être modifiée ni supprimée par l'ORM (sauf purge signée).
  - Profils de comportement (lectures + rôle) : voir « Profils » plus bas ; l'historique des modifications reste dans JournalAudit.
  - Collecteurs de l'interrupteur de l'homme mort : le middleware bat « web » ; il vérifie aussi que la surveillance bat « surveillance ».
"""
import datetime
import hashlib
import ipaddress
import logging
import re
import time

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from .siem_cache import cache
from django.db.models.signals import pre_delete, pre_save

from . import siem_noyau as siem
from .i18n import t, tf
from engine.i18n import tf as _siem_tf  # noqa: E402

logger = logging.getLogger("engine.siem")

IGNORES = ("/static/", "/media/", "/sante/", "/api/usabilite/")
EXEMPTS_LOCKDOWN = ("/login", "/logout", "/siem/", "/api/siem/", "/sante/", "/api/usabilite/")
SENSIBLES = tuple(re.compile(p) for p in (
    r"^/api/blockchain/export", r"^/api/wallet/", r"^/api/documents-administratifs/export", r"/exporter", r"/export(\.|/|$)",
    r"/resume-general-pdf", r"/rapport-general-pdf", r"^/api/employes/", r"^/wallets-clients/", r"^/comptabilite-wallet/",
    r"^/api/lignes", r"^/api/clients", r"^/api/cluster/etat", r"^/media/(rapports|documents|archives_cycle)/"))
METHODES_ECRITURE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_derniers = {"battement": 0.0, "controle": 0.0}   # par processus : évite une écriture de cache à CHAQUE requête
PERIODE_BATTEMENT_S = 10
PERIODE_CONTROLE_S = 60


def _est_sensible(chemin: str) -> bool:
    extra = tuple(getattr(settings, "GYPLATFORM_SIEM_LECTURES_SENSIBLES", ()))
    return any(p.search(chemin) for p in SENSIBLES) or any(chemin.startswith(x) for x in extra)


def _empreinte_ua(request) -> str:
    return hashlib.sha256(request.META.get("HTTP_USER_AGENT", "").encode()).hexdigest()[:16]


def _entreprise_id(utilisateur):
    """Entreprise du membre (mise en cache courte : appelée à chaque événement collecté)."""
    if utilisateur is None or not utilisateur.is_authenticated:
        return None
    cle = f"siem:ent:{utilisateur.pk}"
    valeur = cache.get(cle)
    if valeur is None:
        from .models import MembreEntreprise
        m = MembreEntreprise.objects.filter(utilisateur=utilisateur).only("entreprise_id").first()
        valeur = m.entreprise_id if m else 0
        cache.set(cle, valeur, timeout=300)
    return valeur or None


def _role_membre(utilisateur, entreprise_id=None) -> str:
    """Rôle du membre dans son entreprise (admin, caisse, agent, lecteur), en cache court comme l'entreprise."""
    if utilisateur is None or not utilisateur.is_authenticated:
        return ""
    cle = f"siem:role:{utilisateur.pk}:{entreprise_id or 0}"
    valeur = cache.get(cle)
    if valeur is None:
        from .models import MembreEntreprise
        m = MembreEntreprise.objects.filter(utilisateur=utilisateur, **({"entreprise_id": entreprise_id} if entreprise_id else {})).only("role").first()
        valeur = (m.role if m else "") or "-"
        cache.set(cle, valeur, timeout=300)
    return "" if valeur == "-" else valeur


def _reseau(ip: str) -> str:
    try:
        adr = ipaddress.ip_address(ip)
        return str(ipaddress.ip_network(f"{ip}/{24 if adr.version == 4 else 48}", strict=False))
    except ValueError:
        return ip


def verdict_session(connu: dict, actuel: dict) -> dict:
    """Calcul pur : que vaut le changement d'empreinte d'une session ({"ip", "ua"} avant / après) ?

    - même navigateur, même réseau (/24 en IPv4, /48 en IPv6) : l'adresse a seulement tourné (renouvellement d'adresse, CGNAT d'un même
      opérateur) → rien à signaler (`ecart` faux) ;
    - autre RÉSEAU, même navigateur : Wi-Fi ↔ 4G, déplacement. Signal faible (moyenne, 15 points), jamais de révocation à lui seul : sur
      mobile, deux changements en 30 min suffisaient à verrouiller un utilisateur légitime (40 + 20 ≥ 60) ;
    - autre NAVIGATEUR : un cookie de session copié ne garde pas toujours l'empreinte du navigateur d'origine → signal fort (haute, 40 points),
      révocation possible en mode actif.
    """
    reseau = _reseau(connu.get("ip", "")) != _reseau(actuel.get("ip", ""))
    navigateur = connu.get("ua") != actuel.get("ua")
    raison = ([_siem_tf("rg4.siem.msg.225")] if reseau else []) + ([_siem_tf("rg4.siem.msg.226")] if navigateur else [])
    return {"ecart": bool(raison), "raison": raison, "gravite": "haute" if navigateur else "moyenne",
            "points": 40 if navigateur else 15, "revocable": navigateur}


def _refus_reponse(request, statut, message):
    from django.http import JsonResponse
    from .views_erreur import reponse_erreur
    if request.path.startswith("/api/") or request.headers.get("Accept", "").startswith("application/json"):
        return JsonResponse({"erreur": message}, status=statut)
    return reponse_erreur(request, statut, message=message)


class SiemMiddleware:
    """Collecte + contrôles d'entrée. Ne fait jamais échouer une requête sur une panne du SIEM (best effort, journalisé)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        chemin = request.path
        ip = siem.adresse_ip(request)
        try:
            refus = self._controles_entree(request, chemin, ip)
        except Exception:
            logger.exception(_siem_tf("rg4.siem.msg.234"))
            refus = None
        if refus is not None:
            return refus
        reponse = self.get_response(request)
        try:
            self._collecter(request, reponse, chemin, ip)
        except Exception:
            logger.exception(_siem_tf("rg4.siem.msg.235"), chemin)
        return reponse

    # — entrée —
    def _controles_entree(self, request, chemin, ip):
        maintenant = time.time()
        if maintenant - _derniers["battement"] >= PERIODE_BATTEMENT_S:
            _derniers["battement"] = maintenant
            siem.battement("web")
        if chemin.startswith(("/static/",)):
            return None
        reste = siem.ip_bloquee(ip)
        if reste > 0 and not chemin.startswith("/sante/"):
            return _refus_reponse(request, 403, tf("engine/siem_collecte.py:g:143:51", v1=int(reste // 60) + 1))
        utilisateur = getattr(request, "user", None)
        if utilisateur is not None and utilisateur.is_authenticated:
            if siem.compte_verrouille(utilisateur.pk):
                logout(request)
                return _refus_reponse(request, 403, t("engine/siem_collecte.py:g:148:54"))
            if self._session_changee(request, utilisateur, ip):
                logout(request)
                return _refus_reponse(request, 401, t("engine/siem_collecte.py:g:151:54"))
        if (request.method in METHODES_ECRITURE and siem.lockdown_actif()
                and not chemin.startswith(EXEMPTS_LOCKDOWN)):
            return _refus_reponse(request, 503, t("engine/siem_collecte.py:g:154:50"))
        if maintenant - _derniers["controle"] >= PERIODE_CONTROLE_S:
            _derniers["controle"] = maintenant
            self._surveiller_surveillance()
        return None

    def _session_changee(self, request, utilisateur, ip) -> bool:
        cle_session = request.session.session_key if hasattr(request, "session") else None
        if not cle_session:
            return False
        actuel = {"ip": ip, "ua": _empreinte_ua(request)}
        k = f"siem:sess:{cle_session}"
        connu = cache.get(k)
        if connu is None:
            cache.set(k, actuel, timeout=86400)
            return False
        if connu == actuel:
            return False
        v = verdict_session(connu, actuel)
        if not v["ecart"]:   # même réseau et même navigateur : on retient la nouvelle adresse, sans alerte
            cache.set(k, actuel, timeout=86400)
            return False
        score = siem.ajouter_risque("compte", utilisateur.pk, v["points"])
        siem.alerter("session_changement", v["gravite"], _siem_tf("rg4.siem.msg.01", v1=_siem_tf("rg4.siem.msg.227").join(v['raison']), v2=utilisateur.get_username()),
                     utilisateur=utilisateur, ip=ip, entreprise_id=_entreprise_id(utilisateur),
                     details={"avant": connu, "apres": actuel})
        siem.appliquer_reponse("compte", utilisateur.pk, score, utilisateur=utilisateur, ip=ip, entreprise_id=_entreprise_id(utilisateur))
        cfg = siem.config()
        revoquer = v["revocable"] and cfg["session_stricte"] and cfg["mode"] == "actif" and not siem.est_protege(utilisateur)
        if not revoquer:
            cache.set(k, actuel, timeout=86400)   # observation, compte protégé ou simple changement de réseau : la nouvelle empreinte devient la référence
        return revoquer

    def _surveiller_surveillance(self):
        """Le collecteur « surveillance » (commande siem_surveiller) doit battre : s'il se tait, le SIEM est peut-être arrêté."""
        if not cache.add("siem:controle_surveillance", 1, timeout=60):
            return
        dernier = cache.get("siem:vie:surveillance")
        limite = siem.config()["silence_collecteur_s"]
        if dernier is None:
            if cache.add("siem:surveillance_jamais_vue", 1, timeout=3600):
                siem.alerter("collecteur_muet", "haute", _siem_tf("rg4.siem.msg.11"),
                             dedoublonner_s=3600)
        elif time.time() - dernier > limite:
            siem.alerter("collecteur_muet", "critique", _siem_tf("rg4.siem.msg.12", v1=int(time.time() - dernier)), dedoublonner_s=900)

    # — sortie —
    def _collecter(self, request, reponse, chemin, ip):
        if chemin.startswith(IGNORES):
            return
        utilisateur = getattr(request, "user", None)
        connecte = utilisateur is not None and utilisateur.is_authenticated
        statut = reponse.status_code
        seuils = siem.config()["seuils"]
        entreprise_id = None
        if connecte:   # entreprise de CETTE requête (clé API ou entreprise choisie), pas le premier rattachement du compte
            from .securite import membre_de_requete
            from .models import MembreEntreprise
            _m = membre_de_requete(request, MembreEntreprise.objects.only("entreprise_id"))
            entreprise_id = _m.entreprise_id if _m else _entreprise_id(utilisateur)
        details = {"chemin": chemin[:200], "methode": request.method, "statut": statut, "ip": ip, "ua": _empreinte_ua(request),
                   "cle_api": bool(getattr(request, "_cle_api", None))}
        if statut >= 400:
            siem.evenement("refus", utilisateur=utilisateur if connecte else None, entreprise_id=entreprise_id, ip=ip, details=details,
                           resultat="refus")
            self._compter_refus(utilisateur if connecte else None, entreprise_id, ip, statut, chemin, seuils)
            try:
                from . import siem_correlation
                siem_correlation.noter_refus(utilisateur if connecte else None, ip, chemin, statut)
            except Exception:
                logger.exception(_siem_tf("rg4.siem.msg.236"))
        elif request.method in ("GET", "HEAD") and connecte and _est_sensible(chemin):
            siem.evenement("lecture_sensible", utilisateur=utilisateur, entreprise_id=entreprise_id, ip=ip, details=details, resultat="lecture")
            n = siem.compter(f"lec:{utilisateur.pk}", seuils["lectures_fenetre_s"])
            if n == seuils["lectures_sensibles"] + 1:
                siem.alerter("lectures_sensibles", "moyenne", _siem_tf("rg4.siem.msg.17", v1=utilisateur.get_username(), n=n, v3=seuils['lectures_fenetre_s'] // 60), utilisateur=utilisateur, ip=ip,
                             entreprise_id=entreprise_id, details={"dernier_chemin": chemin[:120]})
                score = siem.ajouter_risque("compte", utilisateur.pk, 15)
                siem.appliquer_reponse("compte", utilisateur.pk, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)
        if connecte and statut < 400:
            self._profils(request, utilisateur, entreprise_id, ip, chemin, seuils)

    def _profils(self, request, utilisateur, entreprise_id, ip, chemin, seuils):
        try:
            if request.method in ("GET", "HEAD"):
                _profil_lecture(utilisateur, entreprise_id, ip, chemin)
                _profil_role(utilisateur, entreprise_id, ip, chemin)
            _habitudes(request, utilisateur, entreprise_id, ip, chemin, seuils)
        except Exception:
            logger.exception(_siem_tf("rg4.siem.msg.237"))

    def _compter_refus(self, utilisateur, entreprise_id, ip, statut, chemin, seuils):
        n_ip = siem.compter(f"ref:ip:{ip}", seuils["refus_fenetre_s"])
        if n_ip == seuils["refus"] + 1:
            siem.alerter("refus_repetes_ip", "moyenne", _siem_tf("rg4.siem.msg.07", n_ip=n_ip, v2=seuils['refus_fenetre_s'] // 60, ip=ip, statut=statut, v5=chemin[:80]), ip=ip, entreprise_id=entreprise_id)
            score = siem.ajouter_risque("ip", ip, 25)
            siem.appliquer_reponse("ip", ip, score, ip=ip, entreprise_id=entreprise_id)
        if utilisateur is not None:
            n_u = siem.compter(f"ref:u:{utilisateur.pk}", seuils["refus_fenetre_s"])
            if n_u == seuils["refus"] + 1:
                siem.alerter("refus_repetes_compte", "haute", _siem_tf("rg4.siem.msg.13", v1=utilisateur.get_username(), n_u=n_u, v3=seuils['refus_fenetre_s'] // 60), utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)
                score = siem.ajouter_risque("compte", utilisateur.pk, 25)
                siem.appliquer_reponse("compte", utilisateur.pk, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)


# ── Profils (lectures par heure ; profil par rôle) ──────────────────────────
# Les modifications et les connexions sont déjà profilées depuis JournalAudit (siem_regles.surveiller_comportements). Les LECTURES — le premier
# geste d'un compte volé est de regarder — n'y sont pas : elles sont comptées ici, dans le cache partagé, sur 35 jours glissants :
#   - par compte et par heure du jour → « activité à une heure quasi absente de son historique » (sur les lectures, pas seulement les écritures) ;
#   - par rôle (admin, caisse, agent, lecteur) de chaque entreprise : jours de la semaine, et tables ouvertes. Un compte qui ouvre une table
#     que personne de son rôle n'a ouverte, ou qui travaille un jour où son rôle ne travaille jamais (week-end), est signalé.
# Silencieux tant que l'historique est trop mince pour juger (même principe que anomalies_profil) et pour les rôles à moins de 3 comptes.
DUREE_PROFIL_S = 35 * 86400
_RE_TABLE = re.compile(r"^/api/tables/(\d+)/")
MIN_LECTURES_COMPTE = 200
MIN_LECTURES_ROLE = 300
MIN_COMPTES_ROLE = 3


def anomalie_heure_lecture(par_heure: dict, heure: int, minimum=MIN_LECTURES_COMPTE, part_min=0.02) -> str:
    """Calcul pur : texte d'anomalie si `heure` pèse moins de `part_min` des lectures du compte (historique suffisant), sinon ''.
    `par_heure` : {heure: nombre de lectures}, la lecture en cours comprise."""
    total = sum(par_heure.values())
    if total < minimum:
        return ""
    if par_heure.get(heure, 0) / total < part_min:
        return _siem_tf("rg4.siem.msg.080", heure=heure)
    return ""


def anomalie_jour_role(par_jour: dict, jour: int, total_role: int, minimum=MIN_LECTURES_ROLE, part_min=0.02) -> str:
    """Calcul pur : le rôle ne travaille (quasiment) jamais ce jour de la semaine (0 = lundi). `par_jour` : {jour: lectures}, la lecture en cours comprise."""
    if total_role < minimum:
        return ""
    if par_jour.get(jour, 0) / total_role < part_min:
        return _siem_tf("rg4.siem.msg.081")
    return ""


def _profil_lecture(utilisateur, entreprise_id, ip, chemin):
    lt = time.localtime()
    uid, h = utilisateur.pk, lt.tm_hour
    siem.incrementer(f"lh:{uid}:{h}", DUREE_PROFIL_S)
    if not cache.add(f"siem:prof:verif:{uid}:{lt.tm_yday}:{h}", 1, timeout=3600):   # une évaluation par compte et par heure
        return
    par_heure = {x: v for x, v in zip(range(24), (cache.get(f"siem:prof:lh:{uid}:{x}") or 0 for x in range(24))) if v}
    texte = anomalie_heure_lecture(par_heure, h)
    if texte:
        siem.alerter("profil_lecture_heure", "moyenne", f"{utilisateur.get_username()} : {texte}", utilisateur=utilisateur, ip=ip,
                     entreprise_id=entreprise_id, dedoublonner_s=3600)
        score = siem.ajouter_risque("compte", uid, 10)
        siem.appliquer_reponse("compte", uid, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)


def _profil_role(utilisateur, entreprise_id, ip, chemin):
    role = _role_membre(utilisateur, entreprise_id)
    if not entreprise_id or not role:
        return
    lt = time.localtime()
    base = f"{entreprise_id}:{role}"
    uid, jour = utilisateur.pk, lt.tm_wday
    # comptes distincts du rôle (liste bornée) : un rôle porté par moins de 3 comptes n'a pas de « norme » à comparer
    cle_comptes = f"siem:prof:rc:{base}"
    comptes = cache.get(cle_comptes) or []
    if uid not in comptes:
        cache.set(cle_comptes, (comptes + [uid])[-200:], timeout=DUREE_PROFIL_S)
        comptes = comptes + [uid]
    n_total = siem.incrementer(f"rt:{base}", DUREE_PROFIL_S)
    siem.incrementer(f"rj:{base}:{jour}", DUREE_PROFIL_S)
    table = _RE_TABLE.match(chemin)
    deja_vue = None
    if table:
        cle_table = f"rtab:{base}:{table.group(1)}"
        deja_vue = cache.get(f"siem:prof:{cle_table}") or 0
        siem.incrementer(cle_table, DUREE_PROFIL_S)
    if len(comptes) < MIN_COMPTES_ROLE:
        return
    total = cache.get(f"siem:prof:rt:{base}") or n_total
    if table and deja_vue == 0 and total >= MIN_LECTURES_ROLE:
        siem.alerter("role_table_inedite", "moyenne", _siem_tf("rg4.siem.msg.02", v1=utilisateur.get_username(), role=role, v3=table.group(1)), utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id, details={"table": int(table.group(1)), "role": role},
                     dedoublonner_s=86400)
        score = siem.ajouter_risque("compte", uid, 10)
        siem.appliquer_reponse("compte", uid, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)
    if cache.add(f"siem:prof:verifj:{base}:{uid}:{lt.tm_yday}", 1, timeout=86400):   # une évaluation par compte et par jour
        par_jour = {j: cache.get(f"siem:prof:rj:{base}:{j}") or 0 for j in range(7)}
        texte = anomalie_jour_role(par_jour, jour, sum(par_jour.values()))
        if texte:
            siem.alerter("role_jour_inhabituel", "moyenne", _siem_tf("rg4.siem.msg.08", v1=utilisateur.get_username(), role=role, texte=texte), utilisateur=utilisateur, ip=ip,
                         entreprise_id=entreprise_id, details={"role": role, "jour": jour}, dedoublonner_s=86400)
            score = siem.ajouter_risque("compte", uid, 10)
            siem.appliquer_reponse("compte", uid, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)


# ── Habitudes de chaque compte ──────────────────────────────────────────────
# Ce que fait d'habitude CHAQUE compte, appris sur 35 jours glissants (cache partagé), et ce qui s'en écarte. Toutes les règles s'appellent
# « profil_… » (couche comportement de l'incident) et ajoutent des points au score de risque du compte : plusieurs écarts qui s'additionnent
# déclenchent la réponse graduée existante, un écart isolé ne fait que lever une alerte.
#   tables           table jamais ouverte par ce compte (profil_table_inedite) ; nombreuses tables différentes en 10 min (profil_balayage_tables)
#   volume           lectures du jour très au-dessus de sa moyenne (profil_volume_lecture) ; exports du jour au-dessus de son habitude (profil_export_inhabituel)
#   jours            compte actif un jour de la semaine où il ne l'est quasi jamais (profil_jour_inhabituel)
#   rythme           plus de N requêtes par minute sans clé API : script ou robot (profil_rythme_robot) ; suppressions en rafale (profil_suppressions)
#   appareil         premier navigateur/appareil inconnu à la connexion, après 5 connexions d'historique (profil_appareil_nouveau)
#   dormant          compte qui se réveille après 30 jours sans connexion (profil_compte_dormant)
MIN_TABLES_COMPTE = 300     # requêtes d'historique avant de juger les tables ouvertes
DORMANT_JOURS = 30
_RE_EXPORT = re.compile(r"export|pdf|telecharg", re.I)


def anomalie_volume_jour(historique: list, aujourdhui: int, x: float, minimum: int) -> str:
    """Calcul pur : `historique` = comptes des jours précédents (jours sans activité = 0, ignorés). Texte si aujourd'hui > x fois la moyenne
    (au moins 5 jours d'historique et `minimum` actions aujourd'hui), sinon ''."""
    jours = [v for v in historique if v > 0]
    if len(jours) < 5:
        return ""
    moyenne = sum(jours) / len(jours)
    if moyenne > 0 and aujourdhui > x * moyenne and aujourdhui >= minimum:
        return _siem_tf("rg4.siem.msg.082", aujourdhui=aujourdhui, moyenne=moyenne)
    return ""


def anomalie_jour_compte(par_jour: dict, jour: int, minimum=MIN_LECTURES_COMPTE, part_min=0.02) -> str:
    """Calcul pur : ce jour de la semaine (0 = lundi) pèse moins de `part_min` de l'activité du compte (historique suffisant), sinon ''."""
    total = sum(par_jour.values())
    if total < minimum:
        return ""
    if par_jour.get(jour, 0) / total < part_min:
        return _siem_tf("rg4.siem.msg.083")
    return ""


def _signaler(utilisateur, entreprise_id, ip, regle, gravite, texte, points, details=None, dedoublonner_s=3600):
    siem.alerter(regle, gravite, f"{utilisateur.get_username()} : {texte}", utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id,
                 details=details, dedoublonner_s=dedoublonner_s)
    score = siem.ajouter_risque("compte", utilisateur.pk, points)
    siem.appliquer_reponse("compte", utilisateur.pk, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)


def _historique_jours(prefixe: str, uid, ordinal: int, n=29) -> list:
    cles = [f"siem:prof:{prefixe}:{uid}:{ordinal - i}" for i in range(1, n + 1)]
    lu = cache.get_many(cles)
    return [lu.get(c) or 0 for c in cles]


def _habitudes(request, utilisateur, entreprise_id, ip, chemin, seuils):
    lt = time.localtime()
    uid, ordinal = utilisateur.pk, datetime.date.today().toordinal()
    lecture = request.method in ("GET", "HEAD")
    siem.incrementer(f"uj:{uid}:{lt.tm_wday}", DUREE_PROFIL_S)
    # rythme : un humain ne fait pas 150 requêtes par minute ; une clé API (automatisation légitime) est exemptée
    if not getattr(request, "_cle_api", None):
        n = siem.compter(f"rpm:{uid}", 60)
        if n == int(seuils.get("requetes_minute", 150)) + 1:
            _signaler(utilisateur, entreprise_id, ip, "profil_rythme_robot", "haute", _siem_tf("rg4.siem.msg.09", n=n), 20,
                      dedoublonner_s=900)
    if lecture:
        siem.incrementer(f"ld:{uid}:{ordinal}", DUREE_PROFIL_S)
        if _est_sensible(chemin) and _RE_EXPORT.search(chemin):
            siem.incrementer(f"xd:{uid}:{ordinal}", DUREE_PROFIL_S)
    if request.method == "DELETE" or "/supprimer" in chemin:
        n = siem.compter(f"sup:{uid}", 600)
        if n == int(seuils.get("suppressions", 20)) + 1:
            _signaler(utilisateur, entreprise_id, ip, "profil_suppressions", "haute", _siem_tf("rg4.siem.msg.10", n=n), 25, dedoublonner_s=900)
    table = _RE_TABLE.match(chemin)
    if table:
        t = int(table.group(1))
        connues = cache.get(f"siem:prof:ut:{uid}") or []
        if t not in connues:
            total = sum(cache.get(f"siem:prof:uj:{uid}:{j}") or 0 for j in range(7))
            if total >= MIN_TABLES_COMPTE and len(connues) >= 3:
                _signaler(utilisateur, entreprise_id, ip, "profil_table_inedite", "moyenne", _siem_tf("rg4.siem.msg.14", t=t), 5,
                          details={"table": t}, dedoublonner_s=86400)
            cache.set(f"siem:prof:ut:{uid}", (connues + [t])[-300:], timeout=DUREE_PROFIL_S)
        cle_fenetre = f"siem:hab:tw:{uid}:{int(time.time() // 600)}"
        vues = set(cache.get(cle_fenetre) or ())
        if t not in vues:
            vues.add(t)
            cache.set(cle_fenetre, sorted(vues)[:200], timeout=660)
            if len(vues) == int(seuils.get("tables_distinctes", 15)):
                _signaler(utilisateur, entreprise_id, ip, "profil_balayage_tables", "haute", _siem_tf("rg4.siem.msg.15", v1=len(vues)),
                          20, details={"tables": sorted(vues)[:30]}, dedoublonner_s=1800)
    # évaluations lentes : une fois par heure et par compte
    if not cache.add(f"siem:prof:hab:{uid}:{ordinal}:{lt.tm_hour}", 1, timeout=3600):
        return
    auj = cache.get(f"siem:prof:ld:{uid}:{ordinal}") or 0
    texte = anomalie_volume_jour(_historique_jours("ld", uid, ordinal), auj, float(seuils.get("volume_x", 5)), 100)
    if texte:
        _signaler(utilisateur, entreprise_id, ip, "profil_volume_lecture", "moyenne", _siem_tf("rg4.siem.msg.232", v1=texte), 15, dedoublonner_s=7200)
    auj_x = cache.get(f"siem:prof:xd:{uid}:{ordinal}") or 0
    texte = anomalie_volume_jour(_historique_jours("xd", uid, ordinal), auj_x, float(seuils.get("volume_x", 5)), 5)
    if texte:
        _signaler(utilisateur, entreprise_id, ip, "profil_export_inhabituel", "haute", _siem_tf("rg4.siem.msg.233", v1=texte), 20, dedoublonner_s=7200)
    if cache.add(f"siem:prof:habj:{uid}:{ordinal}", 1, timeout=86400):
        texte = anomalie_jour_compte({j: cache.get(f"siem:prof:uj:{uid}:{j}") or 0 for j in range(7)}, lt.tm_wday)
        if texte:
            _signaler(utilisateur, entreprise_id, ip, "profil_jour_inhabituel", "moyenne", texte, 10, dedoublonner_s=86400)


DUREE_CONNEXION_S = 90 * 86400   # l'horaire de connexion se apprend sur 90 jours (un rythme mensuel ou de congés tient dans cette mémoire)
MIN_CONNEXIONS = 15              # connexions d'historique avant de juger l'horaire d'un compte
_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def anomalies_horaire_connexion(par_heure: dict, par_jour: dict, heure: int, jour: int, minimum=MIN_CONNEXIONS, part_min=0.03) -> list:
    """Calcul pur : écarts de la connexion courante à l'horaire habituel du compte. `par_heure` {0..23: connexions} et `par_jour` {0..6: connexions}
    comptent les connexions PRÉCÉDENTES (la courante exclue). L'heure est jugée avec ses deux voisines (une connexion à 8 h 59 au lieu de 9 h 01
    n'est pas un écart). Retourne une liste de (code, texte) — vide tant que l'historique est trop mince pour juger."""
    sortie = []
    total_h = sum(par_heure.values())
    if total_h >= minimum:
        voisines = sum(par_heure.get((heure + d) % 24, 0) for d in (-1, 0, 1))
        if voisines / total_h < part_min:
            actives = sorted(h for h, v in par_heure.items() if v / total_h >= 0.05)
            habituel = _siem_tf("rg4.siem.msg.085", v1=actives[0], v2=actives[-1] + 1) if actives else ""
            nuit = _siem_tf("rg4.siem.msg.086") if heure < 5 or heure >= 23 else ""
            sortie.append(("heure", _siem_tf("rg4.siem.msg.089", heure=heure, nuit=nuit, habituel=habituel)))
    total_j = sum(par_jour.values())
    if total_j >= minimum and par_jour.get(jour, 0) / total_j < part_min:
        sortie.append(("jour", _siem_tf("rg4.siem.msg.087", v1=_JOURS[jour])))
    return sortie


def _habitudes_connexion(utilisateur, request, ip, entreprise_id):
    """À la connexion : horaire inhabituel (heure, jour), appareil et IP jamais vus, rafale de connexions, compte dormant. Deux écarts ou plus dans la
    même connexion = connexion atypique (haute) : une heure bizarre seule peut être une nuit blanche, une heure bizarre depuis un appareil inconnu non."""
    uid, maintenant = utilisateur.pk, time.time()
    lt = time.localtime(maintenant)
    ecarts = []
    precedent = cache.get(f"siem:prof:vu:{uid}")
    cache.set(f"siem:prof:vu:{uid}", maintenant, timeout=400 * 86400)
    if precedent and maintenant - precedent > DORMANT_JOURS * 86400:
        _signaler(utilisateur, entreprise_id, ip, "profil_compte_dormant", "haute", _siem_tf("rg4.siem.msg.03", v1=int((maintenant - precedent) // 86400)),
                  25, dedoublonner_s=3600)
        ecarts.append(_siem_tf("rg4.siem.msg.228"))
    # horaire : lu AVANT d'ajouter la connexion courante, puis mis à jour
    cles_h = [f"siem:prof:ch:{uid}:{h}" for h in range(24)]
    cles_j = [f"siem:prof:cj:{uid}:{j}" for j in range(7)]
    lu = cache.get_many(cles_h + cles_j)
    par_heure = {h: lu.get(cles_h[h]) or 0 for h in range(24)}
    par_jour = {j: lu.get(cles_j[j]) or 0 for j in range(7)}
    for code, texte in anomalies_horaire_connexion(par_heure, par_jour, lt.tm_hour, lt.tm_wday):
        _signaler(utilisateur, entreprise_id, ip, f"profil_connexion_{code}", "moyenne", texte, 10, dedoublonner_s=3600)
        ecarts.append(texte.split(",")[0])
    siem.incrementer(f"ch:{uid}:{lt.tm_hour}", DUREE_CONNEXION_S)
    siem.incrementer(f"cj:{uid}:{lt.tm_wday}", DUREE_CONNEXION_S)
    # rafale de connexions (bourrage, session qui se reconnecte en boucle, jeton rejoué)
    n = siem.compter(f"login:ok:{uid}", 600)
    if n == int(siem.config()["seuils"].get("connexions_rafale", 5)) + 1:
        _signaler(utilisateur, entreprise_id, ip, "profil_connexions_rafale", "moyenne", _siem_tf("rg4.siem.msg.04", n=n), 10, dedoublonner_s=1800)
    # appareil et IP : jamais vus (l'historique doit exister : on n'alerte pas sur les premières connexions)
    total_logins = siem.incrementer(f"nl:{uid}", 400 * 86400)
    ua = _empreinte_ua(request) if request is not None else ""
    if ua:
        connus = cache.get(f"siem:prof:ua:{uid}") or []
        if ua not in connus:
            if connus and total_logins >= 5:
                _signaler(utilisateur, entreprise_id, ip, "profil_appareil_nouveau", "moyenne", _siem_tf("rg4.siem.msg.16"), 10,
                          dedoublonner_s=3600)
                ecarts.append(_siem_tf("rg4.siem.msg.229"))
            cache.set(f"siem:prof:ua:{uid}", (connus + [ua])[-20:], timeout=400 * 86400)
    if ip:
        ips = cache.get(f"siem:prof:li:{uid}") or []
        if ip not in ips:
            if ips and total_logins >= 5:
                ecarts.append(_siem_tf("rg4.siem.msg.230"))
            cache.set(f"siem:prof:li:{uid}", (ips + [ip])[-50:], timeout=400 * 86400)
    if len(ecarts) >= 2:
        _signaler(utilisateur, entreprise_id, ip, "profil_connexion_atypique", "haute", _siem_tf("rg4.siem.msg.231") + " + ".join(ecarts), 25,
                  details={"ecarts": ecarts}, dedoublonner_s=1800)


# ── Signaux de connexion ────────────────────────────────────────────────────

def _pays(ip: str):
    """Pays de l'IP si une base GeoLite2 est configurée (GYPLATFORM_SIEM_GEOIP) et geoip2 installé ; sinon None."""
    base = getattr(settings, "GYPLATFORM_SIEM_GEOIP", "")
    if not base:
        return None
    try:
        import geoip2.database
        with geoip2.database.Reader(base) as lecteur:
            return lecteur.country(ip).country.iso_code
    except Exception:
        return None


def _controle_voyage(utilisateur, ip, entreprise_id):
    """Voyage impossible (pays différent en moins de 2 h) ; sans base GeoIP, changement de réseau en moins de 5 min."""
    k = f"siem:dernier_login:{utilisateur.pk}"
    avant, maintenant = cache.get(k), time.time()
    pays = _pays(ip)
    cache.set(k, {"ip": ip, "t": maintenant, "pays": pays}, timeout=7 * 86400)
    if not avant or avant["ip"] == ip:
        return
    ecart = maintenant - avant["t"]
    if pays and avant.get("pays") and pays != avant["pays"] and ecart < 7200:
        regle, grav, msg = "voyage_impossible", "haute", (_siem_tf("rg4.siem.msg.084", v1=utilisateur.get_username(), pays=pays, v3=int(ecart // 60), v4=avant['pays']))
    elif not pays and _reseau(ip) != _reseau(avant["ip"]) and ecart < 300:
        regle, grav, msg = "changement_reseau_rapide", "moyenne", (_siem_tf("rg4.siem.msg.088", v1=utilisateur.get_username(), v2=_reseau(avant['ip']), v3=_reseau(ip), v4=int(ecart)))
    else:
        return
    siem.alerter(regle, grav, msg, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)
    score = siem.ajouter_risque("compte", utilisateur.pk, 30)
    siem.appliquer_reponse("compte", utilisateur.pk, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)


def _a_la_connexion_reussie(sender, request, user, **kwargs):
    ip = siem.adresse_ip(request) if request is not None else ""
    ent = _entreprise_id(user)
    siem.evenement("connexion", utilisateur=user, entreprise_id=ent, ip=ip,
                   details={"ip": ip, "ua": _empreinte_ua(request) if request is not None else ""}, resultat="succes")
    if ip:
        _controle_voyage(user, ip, ent)
    try:
        from . import siem_correlation
        siem_correlation.succes_apres_echecs(user, ip, ent)
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.238"))
    try:
        _habitudes_connexion(user, request, ip, ent)
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.239"))


def _a_l_echec_de_connexion(sender, credentials, request=None, **kwargs):
    ip = siem.adresse_ip(request) if request is not None else "inconnue"
    nom = str((credentials or {}).get("username", ""))[:150].lower()
    s = siem.config()["seuils"]
    try:
        from . import siem_correlation
        siem_correlation.noter_echec(nom, ip)
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.240"))
    n_ip = siem.compter(f"login:ip:{ip}", s["echecs_fenetre_s"])
    n_nom = siem.compter(f"login:nom:{nom}", s["echecs_fenetre_s"])
    if n_ip == s["echecs_connexion"] or n_nom == s["echecs_connexion"]:
        # nombre exact (pas « ≥ ») : une alerte par franchissement de seuil, pas une par tentative
        siem.alerter("echecs_connexion", "moyenne", _siem_tf("rg4.siem.msg.05", v1=max(n_ip, n_nom), v2=s['echecs_fenetre_s'] // 60, nom=nom, ip=ip), ip=ip, details={"compte": nom})
        score = siem.ajouter_risque("ip", ip, 20)
        siem.appliquer_reponse("ip", ip, score, ip=ip)
    if n_ip > s["echecs_connexion"] * 3:   # bourrage d'identifiants : escalade
        score = siem.ajouter_risque("ip", ip, 10)
        siem.appliquer_reponse("ip", ip, score, ip=ip)
    try:   # un compte réel qui subit des échecs monte en risque (sans révéler à l'appelant s'il existe)
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.filter(username__iexact=nom).first()
        if u is not None and n_nom >= s["echecs_connexion"]:
            score = siem.ajouter_risque("compte", u.pk, 10)
            siem.appliquer_reponse("compte", u.pk, score, utilisateur=u, ip=ip, entreprise_id=_entreprise_id(u))
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.241"))


# ── JournalAudit : lecture seule une fois scellée ───────────────────────────

def _garde_audit_avant_ecriture(sender, instance, **kwargs):
    """Modifier une ligne d'audit existante est interdit (les lignes sont créées une fois, jamais mises à jour)."""
    if instance.pk and not siem.purge_est_autorisee():
        raise PermissionDenied(t("rg4.siem.audit_modif_refusee"))


def _garde_audit_avant_suppression(sender, instance, **kwargs):
    if siem.purge_est_autorisee():
        return
    _b, dernier = siem.dernier_dans_chaine("siem_scellement")
    if dernier and instance.pk <= int(dernier["details"]["a"]):
        siem.alerter("suppression_audit", "critique", _siem_tf("rg4.siem.msg.06", pk=instance.pk), dedoublonner_s=60)
        raise PermissionDenied(t("rg4.siem.audit_suppr_refusee"))


def brancher():
    """Branche les signaux (appelé par EngineConfig.ready). Idempotent grâce aux dispatch_uid."""
    from django.contrib.auth.signals import user_logged_in, user_login_failed
    from .models import JournalAudit
    user_logged_in.connect(_a_la_connexion_reussie, dispatch_uid="siem_connexion")
    user_login_failed.connect(_a_l_echec_de_connexion, dispatch_uid="siem_echec_connexion")
    pre_save.connect(_garde_audit_avant_ecriture, sender=JournalAudit, dispatch_uid="siem_audit_ajout_seul")
    pre_delete.connect(_garde_audit_avant_suppression, sender=JournalAudit, dispatch_uid="siem_audit_non_supprimable")
