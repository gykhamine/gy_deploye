"""CHATBOT DES AGENTS — chaque agent (ou le chef) configure SON assistant, en deux portées indépendantes :
  - `interne` : répond aux membres de l'équipe qui lui écrivent en discussion à deux ;
  - `externe` : répond à ses clients qui lui écrivent en discussion à deux.
Le chatbot répond AU NOM de l'agent (message marqué `auto`, affiché « (assistant) ») ; il ne se déclenche jamais sur un message
automatique (pas de boucle entre deux chatbots) ni dans les groupes. Le moteur est celui de la plateforme
(inference.MoteurInference.generer_texte : llama local ou moteur externe configuré).

Apprentissage sur les conversations, CLOISONNÉ : le contexte est (1) la discussion en cours et (2) quelques messages que l'agent
a lui-même écrits dans la même portée, comme exemples de style. Jamais un message d'une autre personne hors de la discussion
en cours, jamais une discussion à laquelle l'agent ne participe pas. Voir aussi engine/fusion.py (_fusion_communication) :
le modèle fantôme, lui, n'apprend que des STATISTIQUES de communication, jamais le contenu des messages.
"""
from engine.i18n import td
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf
import logging
import threading

from . import messagerie as mg
from .models import ChatbotAgent, Message, Participant

logger = logging.getLogger(__name__)

NB_CONTEXTE = 12        # derniers messages de la discussion en cours
NB_EXEMPLES = 6         # messages passés de l'agent, comme exemples de style
LONGUEUR_EXEMPLE = 300
TONS = {"neutre": td("engine/chatbot.py:g:26:19"), "chaleureux": td("engine/chatbot.py:g:26:59"), "formel": td("engine/chatbot.py:g:26:104"),
        "concis": td("engine/chatbot.py:g:27:19")}


def portee_de(expediteur, destinataire):
    """Portée du chatbot de `destinataire` face à `expediteur` (None : aucun chatbot ne répond, ex. un agent écrivant à un client)."""
    if not mg.est_equipe(destinataire):
        return None
    return "interne" if mg.est_equipe(expediteur) else "externe"


def configuration(agent, portee):
    return ChatbotAgent.objects.filter(agent=agent, portee=portee).first()


def declencher(msg, asynchrone=True):
    """Appelé après l'envoi d'un message humain. Lance (ou exécute) la réponse automatique si le destinataire en a une d'active."""
    conv = msg.conversation
    if msg.auto or conv.type != "direct" or msg.expediteur_id is None:
        return None
    dest = mg.autre_participant(conv, msg.expediteur)
    if dest is None:
        return None
    portee = portee_de(msg.expediteur, dest)
    cfg = configuration(dest, portee) if portee else None
    if cfg is None or not cfg.actif:
        return None
    if not asynchrone:
        return repondre(msg.id, cfg.id)
    threading.Thread(target=_thread, args=(msg.id, cfg.id), daemon=True).start()
    return None


def _thread(message_id, config_id):
    from django.db import connection
    try:
        repondre(message_id, config_id)
    except Exception:
        logger.exception(_i18n_t("engine/chatbot.py:g:63:26"), message_id)
    finally:
        connection.close()


def _texte(x):
    if x.supprime:
        return ""
    return x.texte or f"[{x.type_media} : {x.nom_fichier}]"


def exemples_de_style(agent, portee):
    """Messages que l'AGENT a écrits lui-même (jamais automatiques) dans la portée, courts, les plus récents d'abord."""
    recents = list(Message.objects.filter(expediteur=agent, auto=False, supprime=False).exclude(texte="")
                   .select_related("conversation").order_by("-id")[:80])
    directes = [x.conversation_id for x in recents if x.conversation.type in ("direct", "salon")]
    avec_client = set(Participant.objects.filter(conversation_id__in=directes, membre__role="lecteur").values_list("conversation_id", flat=True))
    res = []
    for x in recents:
        t = x.conversation.type
        externe = t == "groupe_clients" or (t in ("direct", "salon") and x.conversation_id in avec_client)
        if (portee == "externe") == externe and len(x.texte) <= LONGUEUR_EXEMPLE:
            res.append(x.texte)
        if len(res) >= NB_EXEMPLES:
            break
    return res


def construire(cfg, msg):
    """(prompt, système) pour répondre à `msg`."""
    agent, conv = cfg.agent, msg.conversation
    nom_agent = mg.noms([agent])[agent.id]
    historique = list(conv.messages.select_related("expediteur__utilisateur", "expediteur__enregistrement_client").order_by("-id")[:NB_CONTEXTE])[::-1]
    cache = mg.noms([x.expediteur for x in historique if x.expediteur is not None])
    systeme = (tf("engine/chatbot.py:g:97:16", v1=cfg.nom or 'Assistant', v2=nom_agent, v3=TONS.get(cfg.ton, TONS['neutre']), v4=nom_agent))
    if cfg.consignes.strip():
        systeme += _i18n_t("engine/chatbot.py:g:101:20") + cfg.consignes.strip()[:3000]
    parties = []
    if cfg.utilise_historique:
        ex = exemples_de_style(agent, cfg.portee)
        if ex:
            parties.append(tf("engine/chatbot.py:g:105:28", v1=nom_agent) + "\n".join(f"- {t}" for t in ex))
    parties.append(_i18n_t("engine/chatbot.py:g:107:20") + "\n".join(f"{cache.get(x.expediteur_id, 'Ancien membre')} : {_texte(x)}" for x in historique if _texte(x)))
    parties.append(tf("engine/chatbot.py:g:108:20", v1=cfg.nom or 'Assistant'))
    return "\n\n".join(parties), systeme


def repondre(message_id, config_id):
    """Génère et envoie la réponse automatique. Renvoie le message créé ou None (moteur indisponible : l'agent répondra lui-même)."""
    msg = Message.objects.select_related("conversation", "expediteur").get(id=message_id)
    cfg = ChatbotAgent.objects.select_related("agent__utilisateur", "agent__entreprise").get(id=config_id)
    if not cfg.actif:
        return None
    from .inference import MoteurInference
    prompt, systeme = construire(cfg, msg)
    texte, erreur = MoteurInference(entreprise=cfg.agent.entreprise).generer_texte(prompt, systeme=systeme)
    if erreur or not texte:
        logger.warning(_i18n_t("engine/chatbot.py:g:122:24"), cfg.agent_id, erreur)
        return None
    rep, _err = mg.envoyer(cfg.agent, msg.conversation, texte[:5000], auto=True)
    return rep


def serialiser(cfg):
    return {"portee": cfg.portee, "actif": cfg.actif, "nom": cfg.nom, "consignes": cfg.consignes, "ton": cfg.ton,
            "utilise_historique": cfg.utilise_historique}


def lire(agent):
    """Les deux configurations de l'agent (valeurs par défaut si elles n'existent pas encore)."""
    res = {}
    for portee, _lib in ChatbotAgent.PORTEES:
        cfg = configuration(agent, portee)
        res[portee] = serialiser(cfg) if cfg else {"portee": portee, "actif": False, "nom": "Assistant", "consignes": "", "ton": "neutre",
                                                  "utilise_historique": True}
    return res


def enregistrer(agent, portee, donnees):
    """Crée ou met à jour la configuration ; renvoie (config, erreur)."""
    if portee not in dict(ChatbotAgent.PORTEES):
        return None, _i18n_t("engine/chatbot.py:g:145:22")
    ton = donnees.get("ton", "neutre")
    cfg, _ = ChatbotAgent.objects.get_or_create(agent=agent, portee=portee)
    cfg.actif = bool(donnees.get("actif"))
    cfg.nom = (str(donnees.get("nom") or "Assistant")).strip()[:60] or "Assistant"
    cfg.consignes = str(donnees.get("consignes") or "")[:3000]
    cfg.ton = ton if ton in TONS else "neutre"
    cfg.utilise_historique = bool(donnees.get("utilise_historique", True))
    cfg.save()
    return cfg, None
