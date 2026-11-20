"""APPELS AUDIO / VIDÉO DE LA MESSAGERIE (agentgy) — WebRTC, signalisation par WebSocket, AUCUN modèle ni migration.

Principe : le média (micro, caméra, écran) circule de navigateur à navigateur (WebRTC, chiffré DTLS-SRTP) ; le serveur ne fait que
relayer la signalisation (offre / réponse / candidats ICE) — nécessite un canal temps réel (WebSocket), absent d'un hébergement mutualisé. Aucune
écoute ni enregistrement côté serveur. Un appel n'existe que tant que quelqu'un est connecté à la salle : rien n'est stocké.

Droits : une SEULE règle, celle de la messagerie (messagerie.peut_acceder). Qui peut lire/écrire dans une discussion peut y appeler,
et personne d'autre (un client n'entre jamais dans un appel de l'équipe ni dans l'espace d'un autre agent). Appel individuel =
discussion directe ; appel de groupe = groupe de l'entreprise, groupe clients de l'agent ou DISCUSSION PRIVÉE (salon : le créateur
choisit les participants, huis clos).

Topologie : maillage (chaque participant envoie son flux à chacun des autres). Au-delà de MAX_PARTICIPANTS la qualité s'effondre
côté navigateur : la salle est refusée (côté client ET côté serveur). Pour de gros groupes il faudra un SFU (hors périmètre).

Réglages par environnement : GYPLATFORM_STUN (défaut : stun public), GYPLATFORM_TURN_URL(S), GYPLATFORM_TURN_SECRET (coturn « use-auth-secret » :
identifiants temporaires) ou GYPLATFORM_TURN_USER / GYPLATFORM_TURN_PASSWORD. Sans TURN, certains réseaux (NAT symétrique, 4G
d'opérateur) n'établiront pas l'appel : à prévoir en production.
"""
from engine.i18n import t
import base64
import hashlib
import hmac
import json
import os
import time

MAX_PARTICIPANTS = 10
TAILLE_MAX_SIGNAL = 16000          # caractères d'une offre SDP / candidat sérialisés
TYPES_SIGNAL = ("offer", "answer", "candidate")
VALIDITE_TURN = 3600               # secondes (identifiants temporaires)


def reglages_studio(duree=60):
    """Réglages d'appel saisis dans Gykhamine Studio (GTK), en lecture seule, mis en cache. Jamais bloquant : {} au moindre souci."""
    global _CACHE_STUDIO
    if time.monotonic() - _CACHE_STUDIO[0] < duree:
        return _CACHE_STUDIO[1]
    reglages = {}   # réglages par variables d'environnement uniquement (voir serveurs_ice)
    _CACHE_STUDIO = (time.monotonic(), reglages or {})
    return _CACHE_STUDIO[1]


_CACHE_STUDIO = (-1e9, {})


def hote_de(valeur):
    """Nom ou adresse (sans port) de l'hôte par lequel le client est arrivé : '10.0.0.5:8000' → '10.0.0.5' ; '[::1]:8000' → '[::1]'."""
    v = str(valeur or "").strip()
    if v.startswith("["):
        return v[:v.index("]") + 1] if "]" in v else ""
    return v.split(":", 1)[0]


def serveurs_ice(membre_id, env=None, maintenant=None, studio=None, hote=""):
    """Liste `iceServers` pour RTCPeerConnection. Priorité : variables d'environnement, puis réglages de Studio, puis STUN public.
    Pure (env, studio et horloge injectables) pour les tests."""
    env = os.environ if env is None else env
    studio = studio or {}

    def val(cle_env, cle_studio, defaut=""):
        return env.get(cle_env) or studio.get(cle_studio) or defaut

    stun = [u.strip() for u in val("GYPLATFORM_STUN", "stun_urls", "stun:stun.l.google.com:19302").split(",") if u.strip()]
    res = [{"urls": stun}] if stun else []
    turn = [u.strip() for u in (env.get("GYPLATFORM_TURN_URLS") or env.get("GYPLATFORM_TURN_URL") or studio.get("turn_urls") or "").split(",") if u.strip()]
    turn = [u for u in turn if u.lower() != "auto"]
    secret = val("GYPLATFORM_TURN_SECRET", "turn_secret")
    if not turn and secret and hote:
        turn = [f"turn:{hote}:3478"]       # coturn géré par Studio : suit l'adresse par laquelle le client est arrivé (aucune IP figée)
    if turn:
        if secret:
            fin = int(maintenant if maintenant is not None else time.time()) + VALIDITE_TURN
            nom = f"{fin}:{membre_id}"
            mdp = base64.b64encode(hmac.new(secret.encode(), nom.encode(), hashlib.sha1).digest()).decode()
            res.append({"urls": turn, "username": nom, "credential": mdp})
        elif env.get("GYPLATFORM_TURN_USER"):
            res.append({"urls": turn, "username": env["GYPLATFORM_TURN_USER"], "credential": env.get("GYPLATFORM_TURN_PASSWORD", "")})
    return res


def signal_valide(data):
    """Un signal est un dict {type: offer|answer|candidate, ...} de taille bornée ; sinon il est ignoré par le relais."""
    if not isinstance(data, dict) or data.get("type") not in TYPES_SIGNAL:
        return False
    try:
        return len(json.dumps(data)) <= TAILLE_MAX_SIGNAL
    except (TypeError, ValueError):
        return False


def groupe_appel(conversation_id):
    return f"appel_{int(conversation_id)}"


def libelle_appel(video):
    return t("engine/appels.py:g:99:12") if video else t("engine/appels.py:g:99:48")


def sonner(m, conv, video):
    """Prévient les destinataires (sourdine respectée) : message dans le fil (visible même hors ligne, notification classique)
    + sonnerie temps réel sur le WebSocket de ceux qui ont la page ouverte. Renvoie le nombre de personnes jointes."""
    from . import messagerie as mg
    from . import ws_notify

    msg, _ = mg.envoyer(m, conv, libelle_appel(video), appel=True)
    de = mg.noms([m])[m.id]
    n = 0
    for dest in mg.destinataires(conv, m):
        if mg.en_sourdine(dest, m, conv):
            continue
        ws_notify.notifier_appel_entrant(dest.utilisateur_id, conversation_id=conv.id, de=de, video=bool(video), titre=mg.titre_conversation(conv, dest))
        n += 1
    return n
