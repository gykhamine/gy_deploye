"""SIEM — détection stricte et cycle de surveillance (commande `siem_surveiller`).

Tout est calculé sur ce qui existe : JournalAudit (collecte), Bloc / BlocSysteme (preuves), les fichiers du projet (intégrité du code),
et l'horloge (NTP). Aucune table nouvelle : les références (empreintes du code)
sont elles-mêmes inscrites dans la blockchain système, donc infalsifiables comme le reste.

  comportement   profil par compte (heures, IP, volume) à partir de JournalAudit ; IP partagées entre comptes (graphe de l'ontologie)
  code           empreinte de engine/, IA/, gyplatform/, manage.py (.py, .js, .html, .css) et des statiques servis ; tout nouveau fichier de IA/modeles_scripts/ = code exécuté → alerte critique
  horloge        dérive NTP et recul de l'horloge (les preuves sont datées : une horloge truquée fausse tout)
  collecteurs    silence d'un collecteur = alerte (interrupteur de l'homme mort)
  correlation    siem_correlation.py : compte volé, fraude wallet, aspiration de données, élévation de privilèges, clés API détournées
  fantome        modèle fantôme (siem_fantome.py / siem_fantome_suivi.py) : apprend des comportements, de la messagerie (avis bon / mauvais) et de l'état du système ; les 50 règles lui servent de professeur
  incident       couche 3 (siem_incident.py) : regroupe les alertes en un état calme / suspect / attaque, clôture après 30 min de calme
"""
import hashlib
import logging
import os
import re
import socket
import struct
import threading
import time
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from .siem_cache import cache
from django.utils import timezone

from . import siem_noyau as siem
from engine.i18n import tf as _siem_tf  # noqa: E402

logger = logging.getLogger("engine.siem")

DOSSIERS_CODE = ("engine", "IA", "gyplatform")   # gyplatform : settings, urls, wsgi (un urls.py modifié détourne toutes les routes)
FICHIERS_CODE = ("manage.py",)
# Code exécuté côté serveur (.py) ET côté navigateur (.js, .html, .css) : un script ou un gabarit modifié vole des sessions et des
# retraits aussi bien qu'un .py. Les tests sont inclus : un fichier ajouté n'importe pas, mais un fichier importé par du code modifié, si.
EXTENSIONS_CODE = (".py", ".js", ".html", ".css")
EXCLUS_CODE = ("__pycache__", ".pyc", "/i18n_defaults/")   # catalogues de langue : modifiés par l'interface, pas du code
DOSSIER_SCRIPTS_MODELES = "IA/modeles_scripts"


# ── Comportement ────────────────────────────────────────────────────────────

def ips_partagees(evenements, seuil: int) -> dict:
    """Calcul pur : {ip: ensemble de comptes} pour les IP vues avec au moins `seuil` comptes distincts (bourrage d'identifiants,
    compte partagé ou compromis). `evenements` : itérable de (utilisateur_id, ip). Sert aussi à la couche ontologique."""
    par_ip = defaultdict(set)
    for uid, ip in evenements:
        if uid and ip:
            par_ip[ip].add(uid)
    return {ip: comptes for ip, comptes in par_ip.items() if len(comptes) >= seuil}


def ips_partagees_entre_entreprises(evenements, seuil: int) -> dict:
    """Calcul pur : comme ips_partagees, mais seulement les IP où les comptes viennent d'au moins DEUX entreprises (un compte sans entreprise
    compte pour une). Plusieurs employés d'une même boutique derrière le même routeur, c'est l'usage normal ; des comptes d'entreprises
    sans rapport depuis une même IP, c'est du bourrage d'identifiants. `evenements` : itérable de (utilisateur_id, ip, entreprise_id)."""
    triplets = [(u, i, e) for u, i, e in evenements if u and i]
    groupe = {u: (e or f"u{u}") for u, _i, e in triplets}
    partagees = ips_partagees([(u, i) for u, i, _e in triplets], seuil)
    return {ip: comptes for ip, comptes in partagees.items() if len({groupe[u] for u in comptes}) >= 2}


def _local(h):
    """Heure locale (TIME_ZONE) d'un horodatage : la base rend de l'UTC, alors que l'heure courante est comparée en heure locale."""
    return timezone.localtime(h) if timezone.is_aware(h) else h


def profil_comportement(lignes) -> dict:
    """Calcul pur du profil d'un compte : lignes = itérable de (horodatage, ip). Retourne heures, ips, par_jour (en heure locale)."""
    heures, ips, par_jour = defaultdict(int), defaultdict(int), defaultdict(int)
    for h, ip in lignes:
        h = _local(h)
        heures[h.hour] += 1
        ips[ip] += 1
        par_jour[h.date()] += 1
    return {"heures": dict(heures), "ips": dict(ips), "par_jour": dict(par_jour), "total": sum(heures.values())}


def anomalies_profil(profil: dict, heure: int, ip: str, volume_jour: int, x: float) -> list:
    """Calcul pur : écarts du comportement courant au profil passé. Silencieux tant que l'historique est trop mince pour juger."""
    sortie = []
    total = profil["total"]
    if total >= 20 and ip and ip not in profil["ips"]:
        sortie.append(("ip_nouvelle", 10, _siem_tf("rg4.siem.msg.191", ip=ip)))
    if total >= 50 and profil["heures"].get(heure, 0) / total < 0.02:
        sortie.append(("heure_inhabituelle", 10, _siem_tf("rg4.siem.msg.192", heure=heure)))
    jours = [v for v in profil["par_jour"].values()]
    if len(jours) >= 5:
        moyenne = sum(jours) / len(jours)
        if moyenne > 0 and volume_jour > x * moyenne and volume_jour >= 30:
            sortie.append(("volume_anormal", 20, _siem_tf("rg4.siem.msg.193", volume_jour=volume_jour, moyenne=moyenne)))
    return sortie


def surveiller_comportements(fenetre_s=600):
    """Compare l'activité récente de chaque compte à son profil des 30 derniers jours, et cherche les IP partagées."""
    from django.contrib.auth import get_user_model
    from .models import JournalAudit
    cfg = siem.config()
    maintenant = timezone.now()
    recent = list(JournalAudit.objects.filter(horodatage__gte=maintenant - timezone.timedelta(seconds=fenetre_s), utilisateur__isnull=False)
                  .exclude(ip="").values_list("utilisateur_id", "ip", "entreprise_id"))
    for ip, comptes in ips_partagees_entre_entreprises(recent, cfg["seuils"]["ips_partagees"]).items():
        emise = siem.alerter("ip_partagee", "haute", _siem_tf("rg4.siem.msg.27", v1=len(comptes), ip=ip),
                             ip=ip, details={"comptes": sorted(comptes)[:20]})
        if emise:   # le risque suit l'alerte (une fois par fenêtre de dédoublonnage) : la situation dure 10 min, le cycle repasse chaque minute
            score = siem.ajouter_risque("ip", ip, 20)
            siem.appliquer_reponse("ip", ip, score, ip=ip)
    User = get_user_model()
    for uid in {u for u, _i, _e in recent}:
        debut = maintenant - timezone.timedelta(days=30)
        passe = JournalAudit.objects.filter(utilisateur_id=uid, horodatage__gte=debut, horodatage__lt=maintenant - timezone.timedelta(seconds=fenetre_s)) \
            .exclude(ip="").values_list("horodatage", "ip")
        profil = profil_comportement(passe)
        aujourdhui = JournalAudit.objects.filter(utilisateur_id=uid, horodatage__date=timezone.localdate(maintenant)).count()
        u = User.objects.filter(pk=uid).first()
        if u is None:
            continue
        derniere = next((i for x, i, e in reversed(recent) if x == uid), "")
        ent = next((e for x, i, e in reversed(recent) if x == uid), None)
        for code, points, texte in anomalies_profil(profil, timezone.localtime(maintenant).hour, derniere, aujourdhui, cfg["seuils"]["volume_x"]):
            emise = siem.alerter(f"profil_{code}", "moyenne", f"{u.get_username()} : {texte}", utilisateur=u, ip=derniere, entreprise_id=ent,
                                 dedoublonner_s=3600)
            if emise:   # sans cela, le même écart rajoutait ses points à chaque cycle d'une minute tant que le compte restait actif (verrou en ~6 min)
                score = siem.ajouter_risque("compte", uid, points)
                siem.appliquer_reponse("compte", uid, score, utilisateur=u, ip=derniere, entreprise_id=ent)


# ── Intégrité du code ───────────────────────────────────────────────────────

def _racine_projet() -> Path:
    return Path(settings.BASE_DIR)


def _dossier_statiques_servis():
    """STATIC_ROOT (sortie de collectstatic) : c'est ce que WhiteNoise sert réellement, pas les sources de engine/static."""
    racine = getattr(settings, "STATIC_ROOT", None)
    return Path(racine) if racine and Path(racine).is_dir() else None


def _fichiers_surveilles() -> list:
    """[(chemin relatif, chemin absolu)] du code surveillé : dossiers de code, fichiers racine, statiques servis."""
    racine = _racine_projet()
    candidats = []
    for d in DOSSIERS_CODE:
        if (racine / d).is_dir():
            candidats += [(p.relative_to(racine).as_posix(), p) for p in (racine / d).rglob("*") if p.is_file() and p.suffix in EXTENSIONS_CODE]
    candidats += [(f, racine / f) for f in FICHIERS_CODE if (racine / f).is_file()]
    servis = _dossier_statiques_servis()
    if servis is not None:
        for p in servis.rglob("*"):
            if p.is_file() and p.suffix in EXTENSIONS_CODE:
                try:
                    rel = p.relative_to(racine).as_posix()
                except ValueError:   # STATIC_ROOT hors du projet
                    rel = "static_root/" + p.relative_to(servis).as_posix()
                candidats.append((rel, p))
    return candidats


def empreintes_code() -> dict:
    """{chemin relatif: sha256} du code Python, des gabarits, des JS/CSS et des statiques servis (voir EXTENSIONS_CODE)."""
    sortie = {}
    for rel, p in _fichiers_surveilles():
        if any(x in "/" + rel for x in EXCLUS_CODE):
            continue
        try:
            sortie[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
    return sortie


def comparer_empreintes(reference: dict, courant: dict) -> dict:
    """Calcul pur : fichiers ajoutés, retirés, modifiés."""
    return {"ajoutes": sorted(set(courant) - set(reference)),
            "retires": sorted(set(reference) - set(courant)),
            "modifies": sorted(k for k in set(reference) & set(courant) if reference[k] != courant[k])}


def poser_reference_code(utilisateur=None, ip="") -> dict:
    """Nouvelle référence du code (premier passage, ou après un déploiement légitime) : inscrite dans la blockchain système."""
    empreintes = empreintes_code()
    siem._bloc_systeme(utilisateur, "siem_reference_code", {"fichiers": empreintes, "nb": len(empreintes), "ip": ip})
    return empreintes


def controler_code():
    _b, ref = siem.dernier_dans_chaine("siem_reference_code")
    if ref is None:
        poser_reference_code()
        siem.alerter("reference_initiale", "info", _siem_tf("rg4.siem.msg.28"),
                     details={"reference": "code"}, dedoublonner_s=3600)
        return
    ecart = comparer_empreintes(ref["details"]["fichiers"], empreintes_code())
    if not any(ecart.values()):
        return
    scripts = [f for f in ecart["ajoutes"] + ecart["modifies"] if f.startswith(DOSSIER_SCRIPTS_MODELES)]
    if scripts:   # chaque script de modèle ajouté est du code exécuté
        siem.alerter("script_modele_installe", "critique", _siem_tf("rg4.siem.msg.29", DOSSIER_SCRIPTS_MODELES=DOSSIER_SCRIPTS_MODELES)
                     + ", ".join(scripts[:5]), details={"fichiers": scripts[:50]}, dedoublonner_s=3600)
    siem.alerter("integrite_code", "critique", _siem_tf("rg4.siem.msg.26", v1=len(ecart['modifies']), v2=len(ecart['ajoutes']), v3=len(ecart['retires'])), details={k: v[:50] for k, v in ecart.items()},
                 dedoublonner_s=3600)


# ── Horloge ─────────────────────────────────────────────────────────────────

def decalage_ntp(serveur: str, delai_s=3.0):
    """Décalage (s) de l'horloge locale par rapport à `serveur` NTP (SNTP simple), ou None si injoignable."""
    paquet = b"\x1b" + 47 * b"\0"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(delai_s)
            t0 = time.time()
            s.sendto(paquet, (serveur, 123))
            donnees, _ = s.recvfrom(48)
            t3 = time.time()
        sec, frac = struct.unpack("!II", donnees[40:48])
        serveur_t = sec - 2208988800 + frac / 2 ** 32
        return serveur_t - (t0 + t3) / 2
    except (OSError, struct.error):
        return None


def controler_horloge():
    seuil = siem.config()["seuils"]["derive_horloge_s"]
    for serveur in getattr(settings, "GYPLATFORM_SIEM_NTP", ["pool.ntp.org"]):
        d = decalage_ntp(serveur)
        if d is not None:
            if abs(d) > seuil:
                siem.alerter("derive_horloge", "haute", _siem_tf("rg4.siem.msg.36", d=d, serveur=serveur),
                             details={"serveur": serveur, "decalage_s": round(d, 2)}, dedoublonner_s=1800)
            break
    try:
        from .models import BlocSysteme
        dernier = BlocSysteme.objects.order_by("-index").first()
        if dernier is not None and time.time() < dernier.timestamp - 1:
            siem.alerter("horloge_recul", "critique", _siem_tf("rg4.siem.msg.35"),
                         details={"dernier_bloc": dernier.index}, dedoublonner_s=900)
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.282"))


# ── Collecteurs (interrupteur de l'homme mort) ──────────────────────────────

def sonder_web():
    """Provoque une requête réelle sur le serveur web (GYPLATFORM_SIEM_URL_SANTE) : si le collecteur « web » ne bat pas, c'est qu'il est coupé.
    Sans URL configurée, « web » n'est surveillé que par l'activité réelle (une plateforme calme la nuit n'est pas une alerte)."""
    url = getattr(settings, "GYPLATFORM_SIEM_URL_SANTE", "")
    if not url:
        return
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=10).read(64)
    except Exception:
        logger.warning(_siem_tf("rg4.siem.msg.283"), url, exc_info=True)
    dernier = cache.get("siem:vie:web")
    if dernier is None or time.time() - dernier > siem.config()["silence_collecteur_s"]:
        siem.alerter("collecteur_muet", "critique", _siem_tf("rg4.siem.msg.31"), dedoublonner_s=900)


# ── Cycle ───────────────────────────────────────────────────────────────────

_ECHEANCES_LOCALES: dict = {}


def _echu(nom: str, periode_s: int) -> bool:
    """Vrai si la tâche `nom` est due (état dans le cache partagé : plusieurs nœuds ne la doublent pas)."""
    try:
        return bool(cache.add(f"siem:echeance:{nom}", 1, timeout=periode_s))
    except Exception:
        # Cache indisponible : la surveillance NE S'ARRÊTE PAS. Les échéances sont tenues en mémoire du processus (même cadence), et la panne est signalée.
        logger.warning(_siem_tf("rg4.siem.msg.284"))
        maintenant = time.monotonic()
        if maintenant < _ECHEANCES_LOCALES.get(nom, 0):
            return False
        _ECHEANCES_LOCALES[nom] = maintenant + periode_s
        return True


def _incident():
    from . import siem_incident
    siem_incident.evaluer()


def _correlations():
    from . import siem_correlation
    siem_correlation.surveiller_correlations()


_ETAPE = {"nom": "", "debut": 0.0}   # étape en cours (lue par le fil de pulsation)
ETAPE_MAX_S = 1800                    # au-delà, une étape est tenue pour bloquée


def _pulsation(periode_s=20):
    """Fil de la boucle `siem_surveiller --boucle` : le battement ne dépend plus de la durée des étapes (vérification des chaînes,
    empreintes du code, modèle fantôme... toutes en série, alors que le silence toléré est de 180 s). Si UNE étape dépasse ETAPE_MAX_S, le fil
    cesse de battre, lève « etape_bloquee » en nommant l'étape, et arrête le processus : il est relancé par l'hébergeur (tâche « toujours active ») au lieu de rester « vivant » donc jamais relancé — c'était la cause d'un collecteur muet pendant des heures."""
    while True:
        nom, debut = _ETAPE["nom"], _ETAPE["debut"]
        if nom and time.time() - debut > ETAPE_MAX_S:
            duree = int(time.time() - debut)
            logger.error(_siem_tf("rg4.siem.msg.285"), nom, duree)
            try:
                siem.alerter("etape_bloquee", "haute", _siem_tf("rg4.siem.msg.37", nom=nom, duree=duree), dedoublonner_s=1800)
            except Exception:
                pass
            os._exit(3)
        siem.battement("surveillance")
        time.sleep(periode_s)


def demarrer_pulsation():
    threading.Thread(target=_pulsation, name="siem-pulsation", daemon=True).start()


def cycle(force=False) -> dict:
    """Un passage de surveillance. Chaque étape est indépendante : une panne n'arrête pas les autres."""
    try:
        siem.battement("surveillance")
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.286"))
    try:
        cfg = siem.config()
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.287"))
        cfg = dict(siem.CONFIG_DEFAUT)
    fait = {}
    etapes = (
        ("scellement", 60, siem.sceller),
        ("ancrage", max(60, int(cfg["ancrage_minutes"]) * 60), siem.ancrer),
        ("comportement", 60, surveiller_comportements),
        ("correlations", 60, _correlations),
        ("fantome", 60, _fantome),
        ("collecteurs", 60, sonder_web),
        ("horloge", 300, controler_horloge),
        ("code", 3600, controler_code),
        ("chaines", 3600, _verifier_chaines_et_alerter),
        ("incident", 60, _incident),
    )
    for nom, periode, fonction in etapes:
        if not (force or _echu(nom, periode)):
            continue
        _ETAPE.update(nom=nom, debut=time.time())
        try:
            fonction()
            fait[nom] = True
        except Exception:
            logger.exception(_siem_tf("rg4.siem.msg.288"), nom)
            fait[nom] = False
            try:
                siem.alerter(f"etape_{nom}", "haute", _siem_tf("rg4.siem.msg.38", nom=nom), dedoublonner_s=1800)
            except Exception:
                # L'alerte elle-même dépend du cache/de la base : si elle tombe aussi, on continue avec les étapes suivantes.
                logger.exception(_siem_tf("rg4.siem.msg.289"), nom)
        finally:
            _ETAPE.update(nom="", debut=0.0)
    return fait


def _fantome():
    from . import siem_fantome_suivi
    siem_fantome_suivi.passage()


def _verifier_chaines_et_alerter():
    ok, anomalies = siem.verifier_chaines()
    cache.set("siem:derniere_verification", {"t": time.time(), "ok": ok, "anomalies": anomalies[:50]}, timeout=None)
    if not ok:
        siem.alerter("chaine_rompue", "critique", _siem_tf("rg4.siem.msg.32", v1=len(anomalies), v2=anomalies[0].get('source'), v3=anomalies[0].get('motif')), details={"anomalies": anomalies[:20]}, dedoublonner_s=1800)
