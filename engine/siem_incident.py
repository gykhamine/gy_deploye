"""SIEM — couche 3 : « suis-je attaqué en ce moment ? » (incident = regroupement des alertes en un seul état).

Trois états : calme · suspect · attaque.
  - Chaque alerte émise par siem.alerter() est un SIGNAL, rangé dans l'une des trois couches : serveur (SSH, journaux, processus, réseau,
    fichiers), application (échecs de connexion web, refus, code, chaînes, collecteurs), comportement (profils des comptes et des rôles).
  - suspect : un signal de gravité haute (ou 3 signaux moyens) dans la fenêtre → l'incident s'ouvre, affiché sur la page SIEM, sans notification
    supplémentaire (les alertes individuelles continuent d'arriver comme avant).
  - attaque : un signal critique, OU des signaux de deux couches différentes dans la même fenêtre → UNE notification groupée (cloche + SMS des
    super-administrateurs) : ce qui se passe, depuis quand, quelles IP, ce que le SIEM a déjà fait. Tant que l'incident est en « attaque », les alertes
    individuelles ne notifient plus (elles sont dans l'incident) ; la notification n'est renvoyée que si la situation s'aggrave (nouvelle couche,
    nouveau signal critique, nombre d'IP au moins doublé).
  - Clôture : 30 minutes sans aucun signal (ni nouveau, ni renouvelé). Ouverture, aggravation et clôture sont inscrites dans la blockchain système.

État courant dans le cache partagé (cluster) ; les preuves, dans la blockchain système. Aucun modèle.
"""
import logging
import re
import secrets
import time

from .siem_cache import cache

from . import siem_noyau as siem
from engine.i18n import tf as _siem_tf  # noqa: E402

logger = logging.getLogger("engine.siem")

FENETRE_S = 900          # les signaux de plus de 15 min ne comptent plus pour franchir un niveau
CALME_S = 1800           # clôture après 30 min sans signal
MAX_SIGNAUX = 300
NIVEAUX = ("calme", "suspect", "attaque")
_CLE = "siem:incident"
_CLE_HISTORIQUE = "siem:incidents_recents"

# Règles qui ne sont pas des signaux d'attaque (conséquences ou états administratifs)
_IGNOREES = {"reponse_automatique", "lockdown"}
_COUCHES = (
    ("serveur", re.compile(r"^(ssh_|sudo_|su_|compte_systeme|groupe_admin|service_echec|oom$|worker_|scanner_web|rafale_404|scan_ports|sortie_|reverse_shell|"
                           r"processus_tmp|mineur|cpu_inconnu|fichier_serveur|ld_preload|suid_|journal_|port_|connexion_ip_nouvelle|derive_horloge)")),
    ("application", re.compile(r"^(integrite_code|script_modele_installe|chaine_rompue|suppression_audit|horloge_recul|collecteur_muet|etape_|"
                               r"echecs_connexion|refus_repetes_ip)")),
    ("comportement", re.compile(r"^(profil_|role_|correlation_|ip_partagee|voyage_impossible|changement_reseau_rapide|session_changement|lectures_sensibles|"
                                r"refus_repetes_compte)")),
)


def couche_de(regle: str) -> str:
    """Couche d'une règle d'alerte ('' si elle ne compte pas)."""
    if regle in _IGNOREES:
        return ""
    for nom, motif in _COUCHES:
        if motif.match(regle):
            return nom
    return "application"


# ── Calcul pur ──────────────────────────────────────────────────────────────

def evaluer_niveau(signaux: list, maintenant: float) -> str:
    """Calcul pur : niveau justifié par les signaux de la fenêtre. signaux : dicts {t, regle, gravite, couche}."""
    recents = [s for s in signaux if maintenant - s["t"] <= FENETRE_S and s["gravite"] in ("moyenne", "haute", "critique")]
    if any(s["gravite"] == "critique" for s in recents):
        return "attaque"
    if len({s["couche"] for s in recents if s["gravite"] in ("haute", "moyenne")}) >= 2 and len(recents) >= 2:
        return "attaque"
    if any(s["gravite"] == "haute" for s in recents) or len(recents) >= 3:
        return "suspect"
    return "calme"


def aggravation(avant: dict, apres: dict) -> str:
    """Calcul pur : motif d'aggravation entre deux résumés {couches, critiques, nb_ips} ('' si aucune)."""
    if set(apres["couches"]) - set(avant["couches"]):
        return _siem_tf("rg4.siem.msg.173") + ", ".join(sorted(set(apres["couches"]) - set(avant["couches"])))
    if set(apres["critiques"]) - set(avant["critiques"]):
        return _siem_tf("rg4.siem.msg.250") + ", ".join(sorted(set(apres["critiques"]) - set(avant["critiques"]))[:3])
    if apres["nb_ips"] >= max(avant["nb_ips"] * 2, avant["nb_ips"] + 3):
        return _siem_tf("rg4.siem.msg.171", v1=apres['nb_ips'], v2=avant['nb_ips'])
    return ""


def _resume(inc: dict) -> dict:
    s = inc["signaux"]
    return {"couches": sorted({x["couche"] for x in s}), "critiques": sorted({x["regle"] for x in s if x["gravite"] == "critique"}),
            "nb_ips": len({x["ip"] for x in s if x.get("ip")})}


# ── État ────────────────────────────────────────────────────────────────────

def _verrou():
    """Verrou court dans le cache partagé (meilleur effort : sans lui, au pire une notification de plus ou de moins)."""
    for _ in range(20):
        try:
            if cache.add("siem:incident:verrou", 1, timeout=5):
                return True
        except Exception:
            return False
        time.sleep(0.05)
    return False


def _liberer():
    try:
        cache.delete("siem:incident:verrou")
    except Exception:
        pass


def courant():
    try:
        return cache.get(_CLE)
    except Exception:
        return None


def _sauver(inc):
    cache.set(_CLE, inc, timeout=7 * 86400)


def _heure(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def _texte_notification(inc: dict, motif: str) -> str:
    r = _resume(inc)
    ips = []
    for x in inc["signaux"]:
        if x.get("ip") and x["ip"] not in ips:
            ips.append(x["ip"])
    reps = "; ".join(inc["reponses"][-3:]) or _siem_tf("rg4.siem.msg.172")
    nouveau = _siem_tf("rg4.siem.msg.251", motif=motif) if motif else ""
    return (_siem_tf("rg4.siem.msg.170", v1=_heure(inc['ouvert']), v2=', '.join(r['couches']), v3=len(inc['signaux']), v4=', '.join(ips[:4]) or _siem_tf("rg4.siem.msg.252"), v5='…' if len(ips) > 4 else '', reps=reps, nouveau=nouveau))


def _notifier(inc: dict, motif: str):
    """Une seule notification groupée : cloche + SMS (la gravité « critique » déclenche le SMS dans siem._notifier)."""
    try:
        siem._notifier("critique", "incident", _texte_notification(inc, motif))
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.255"))


# ── Interface appelée par siem.alerter() ────────────────────────────────────

def signal(regle: str, gravite: str, message: str, ip: str = "", utilisateur_id=None) -> bool:
    """Enregistre une alerte comme signal. Retourne True si l'alerte est « absorbée » par un incident en état attaque (pas de notification
    individuelle : elle sera dans la notification groupée). Ne lève jamais d'exception."""
    try:
        couche = couche_de(regle)
        maintenant = time.time()
        avec_verrou = _verrou()
        try:
            inc = courant()
            if not couche:   # conséquence (ex. blocage automatique) : on la garde comme « ce que le SIEM a fait »
                if inc is not None and regle == "reponse_automatique":
                    inc["reponses"] = (inc["reponses"] + [message[:160]])[-20:]
                    _sauver(inc)
                return bool(inc and inc["niveau"] == "attaque")
            if gravite == "info":
                return bool(inc and inc["niveau"] == "attaque")
            nouveau = {"t": maintenant, "regle": regle, "gravite": gravite, "couche": couche, "ip": ip, "message": message[:200], "compte": utilisateur_id}
            signaux = ((inc or {}).get("signaux") or []) + [nouveau]
            niveau = evaluer_niveau(signaux, maintenant)
            if inc is None:
                if niveau == "calme":
                    # signaux isolés : on les garde un instant pour qu'un 2e signal d'une autre couche les retrouve
                    memoire = [s for s in (cache.get("siem:signaux_isoles") or []) if maintenant - s["t"] <= FENETRE_S] + [nouveau]
                    cache.set("siem:signaux_isoles", memoire[-50:], timeout=FENETRE_S + 60)
                    niveau = evaluer_niveau(memoire, maintenant)
                    if niveau == "calme":
                        return False
                    signaux = memoire
                inc = {"id": secrets.token_hex(4), "ouvert": signaux[0]["t"], "dernier": maintenant, "niveau": niveau, "signaux": signaux[-MAX_SIGNAUX:],
                       "reponses": [], "notifie": None}
                cache.delete("siem:signaux_isoles")
                siem._bloc_systeme(None, "siem_incident", {"id": inc["id"], "evenement": "ouverture", "niveau": niveau, "couches": _resume(inc)["couches"],
                                                           "regle": regle, "message": message[:200]})
                if niveau == "attaque":
                    inc["notifie"] = _resume(inc)
                    _sauver(inc)
                    _notifier(inc, "")
                else:
                    _sauver(inc)
                return niveau == "attaque"
            avant = _resume(inc)
            inc["signaux"] = signaux[-MAX_SIGNAUX:]
            inc["dernier"] = maintenant
            monte = NIVEAUX.index(niveau) > NIVEAUX.index(inc["niveau"])
            if monte:
                inc["niveau"] = niveau
                siem._bloc_systeme(None, "siem_incident", {"id": inc["id"], "evenement": "escalade", "niveau": niveau, "couches": _resume(inc)["couches"],
                                                           "regle": regle})
            motif = ""
            if inc["niveau"] == "attaque":
                if inc["notifie"] is None:
                    inc["notifie"] = _resume(inc)
                    _sauver(inc)
                    _notifier(inc, "")
                    return True
                motif = aggravation(inc["notifie"], _resume(inc))
                if motif:
                    inc["notifie"] = _resume(inc)
                    siem._bloc_systeme(None, "siem_incident", {"id": inc["id"], "evenement": "aggravation", "motif": motif})
                    _sauver(inc)
                    _notifier(inc, motif)
                    return True
            _sauver(inc)
            return inc["niveau"] == "attaque"
        finally:
            if avec_verrou:
                _liberer()
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.256"), regle)
        return False


def rappel(regle: str, ip: str = ""):
    """Une alerte déjà connue se répète (dédoublonnée) : la situation dure, l'incident ne doit pas se clore dessus."""
    try:
        inc = courant()
        if inc is not None and any(s["regle"] == regle for s in inc["signaux"][-60:]):
            inc["dernier"] = time.time()
            _sauver(inc)
    except Exception:
        pass


def evaluer(force_maintenant=None):
    """Passage du cycle de surveillance : clôture d'un incident calme depuis 30 min."""
    inc = courant()
    maintenant = force_maintenant or time.time()
    if inc is None or maintenant - inc["dernier"] < CALME_S:
        return
    _cloturer(inc, _siem_tf("rg4.siem.msg.253"), None, "")


def _cloturer(inc: dict, motif: str, utilisateur, ip: str):
    r = _resume(inc)
    resume = {"id": inc["id"], "ouvert": inc["ouvert"], "ferme": time.time(), "niveau": inc["niveau"], "couches": r["couches"], "nb_signaux": len(inc["signaux"]),
              "ips": sorted({s["ip"] for s in inc["signaux"] if s.get("ip")})[:20], "reponses": inc["reponses"][-5:], "motif": motif}
    siem._bloc_systeme(utilisateur, "siem_incident", {"evenement": "cloture", **resume, "ip_admin": ip})
    historique = (cache.get(_CLE_HISTORIQUE) or [])[-19:] + [resume]
    cache.set(_CLE_HISTORIQUE, historique, timeout=None)
    cache.delete(_CLE)
    if inc["niveau"] == "attaque":   # information seule (cloche, pas de SMS)
        try:
            siem._notifier("info", "incident", _siem_tf("rg4.siem.msg.174", motif=motif, v2=_heure(inc['ouvert']), v3=len(inc['signaux']), v4=', '.join(r['couches'])))
        except Exception:
            logger.exception(_siem_tf("rg4.siem.msg.257"))


def clore(utilisateur=None, ip="") -> bool:
    inc = courant()
    if inc is None:
        return False
    _cloturer(inc, _siem_tf("rg4.siem.msg.254"), utilisateur, ip)
    return True


def etat() -> dict:
    """Pour la page SIEM : {etat, incident, recents}."""
    inc = courant()
    recents = list(reversed(cache.get(_CLE_HISTORIQUE) or []))[:10]
    if inc is None:
        return {"etat": "calme", "incident": None, "recents": recents}
    r = _resume(inc)
    ips = []
    for s in inc["signaux"]:
        if s.get("ip") and s["ip"] not in ips:
            ips.append(s["ip"])
    return {"etat": inc["niveau"], "recents": recents, "incident": {
        "id": inc["id"], "ouvert": inc["ouvert"], "dernier": inc["dernier"], "couches": r["couches"], "ips": ips[:20], "reponses": inc["reponses"][-10:],
        "calme_dans_s": max(0, int(CALME_S - (time.time() - inc["dernier"]))),
        "signaux": [{"t": s["t"], "regle": s["regle"], "gravite": s["gravite"], "couche": s["couche"], "ip": s.get("ip", ""), "message": s["message"]}
                    for s in reversed(inc["signaux"][-40:])]}}
