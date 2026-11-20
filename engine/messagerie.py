"""MESSAGERIE INTERNE ET PUBLICITÉS DES AGENTS — logique et droits (vues dans views_messagerie.py).

Qui peut parler à qui (une seule entreprise par installation) :
  - l'ADMINISTRATEUR (le chef) : tout le monde — l'équipe et TOUS les clients de TOUS ses agents.
  - un AGENT : tous les membres de l'équipe, en discussion à deux ou dans le groupe de l'entreprise ;
    plus ses PROPRES clients (son « espace »).
  - un CLIENT (rôle lecteur) : son agent, l'administrateur, et les autres clients du même agent, en discussion à deux
    ou dans le groupe de l'espace de l'agent. Jamais un autre agent, jamais le groupe de l'entreprise.
L'agent d'un client = le créateur de sa fiche (Enregistrement.cree_par) s'il est administrateur/agent, sinon le chef.

Une seule règle d'accès (peut_parler / peut_acceder) sert à lister, ouvrir, lire, écrire et servir les fichiers :
aucune vue ne refait son propre contrôle. Fichiers : /media/messagerie/… et /media/publicites/… passent par
securite.servir_media, qui appelle fichier_accessible().
"""
from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from engine.i18n import t, tf, tl
from .delegation import a_pouvoir as _a_pouvoir
import datetime
import hashlib
import io
import json
import mimetypes
import os
import zipfile

from django.conf import settings

from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone

from .models import (AvisMessage, Conversation, Enregistrement, Entreprise, FavoriMessage, MembreEntreprise, Message, Notification, Participant, Publicite,
                     ReactionMessage, ReactionPublicite, Sourdine, TableDynamique, ValeurChamp)
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

ROLES_EQUIPE = ("admin", "agent")
# Plafond optionnel (settings.MESSAGERIE_TAILLE_MAX_MO, en Mo) ; absent = AUCUNE limite, seul l'espace disque borne l'envoi.
TAILLE_MAX = (int(getattr(settings, "MESSAGERIE_TAILLE_MAX_MO", 0) or 0) * 1024 * 1024) or None
LIMITE_CONTACTS = 30
LIMITE_CONVERSATIONS = 80
LIMITE_MESSAGES = 200
DELAI_MODIFICATION = datetime.timedelta(hours=24)   # un message ne se corrige que dans les 24 h qui suivent son envoi
LIMITE_TRANSFERT = 5        # discussions visées par un seul transfert
EMOJIS = ("👍", "❤️", "😂", "😮", "😢", "🙏")


def est_equipe(m):
    return m.role in ROLES_EQUIPE


def chef(entreprise):
    """Le patron : l'administrateur super-administrateur Django en priorité, sinon le premier administrateur."""
    return (MembreEntreprise.objects.filter(entreprise=entreprise, role="admin").select_related("utilisateur")
            .order_by("-utilisateur__is_superuser", "id").first())


def clients_de(agent):
    """Clients de l'espace de `agent` (queryset de MembreEntreprise, rôle lecteur). Le chef reçoit en plus les clients
    sans agent identifiable (fiche importée, créateur supprimé ou devenu simple client)."""
    q = Q(enregistrement_client__cree_par=agent.utilisateur_id)
    c = chef(agent.entreprise)
    if c is not None and c.id == agent.id:
        equipe = MembreEntreprise.objects.filter(entreprise=agent.entreprise, role__in=ROLES_EQUIPE).values("utilisateur_id")
        q |= Q(enregistrement_client__isnull=True) | Q(enregistrement_client__cree_par__isnull=True) \
            | ~Q(enregistrement_client__cree_par__in=equipe)
    return (MembreEntreprise.objects.filter(entreprise=agent.entreprise, role="lecteur").filter(q)
            .select_related("utilisateur", "enregistrement_client"))


def agent_de(client):
    """Agent responsable du client (jamais None tant que l'entreprise a un administrateur)."""
    enr = client.enregistrement_client
    if enr is not None and enr.cree_par_id:
        a = MembreEntreprise.objects.filter(entreprise=client.entreprise_id, utilisateur_id=enr.cree_par_id,
                                            role__in=ROLES_EQUIPE).select_related("utilisateur").first()
        if a is not None:
            return a
    return chef(client.entreprise)


def noms_clients(membres):
    """{membre.id: nom} pour des clients, en UNE requête : valeur du champ « nom » de leur fiche, sinon premier texte."""
    par_enr = {m.enregistrement_client_id: m.id for m in membres if m.enregistrement_client_id}
    meilleurs = {}
    if par_enr:
        valeurs = (ValeurChamp.objects.filter(enregistrement_id__in=list(par_enr), champ__type_champ__in=("texte", "texte_long"))
                   .exclude(valeur__isnull=True).exclude(valeur="").select_related("champ").order_by("champ_id"))
        for v in valeurs:
            slug = (v.champ.slug or "").lower()
            rang = 0 if slug == "nom" else 1 if slug.startswith("nom") else 2 if "nom" in slug else 3
            if v.enregistrement_id not in meilleurs or rang < meilleurs[v.enregistrement_id][0]:
                meilleurs[v.enregistrement_id] = (rang, v.valeur.strip()[:60])
    return {m.id: (meilleurs[m.enregistrement_client_id][1] if m.enregistrement_client_id in meilleurs
                   else tf("engine/messagerie.py:g:86:25", v1=m.numero_client or m.enregistrement_client_id or m.id)) for m in membres if m.role == "lecteur"}


def nom_equipe(m):
    u = m.utilisateur
    return (u.get_full_name() or u.username).strip()


def noms(membres):
    """{membre.id: nom affiché} pour une liste mixte, sans requête par membre."""
    membres = list(membres)
    res = noms_clients([m for m in membres if m.role == "lecteur"])
    res.update({m.id: nom_equipe(m) for m in membres if m.role != "lecteur"})
    return res


def peut_parler(a, b):
    """Règle de base de toute la messagerie (symétrique)."""
    if a.id == b.id or a.entreprise_id != b.entreprise_id:
        return False
    if est_equipe(a) and est_equipe(b):
        return True
    if est_equipe(a) or est_equipe(b):
        agent, client = (a, b) if est_equipe(a) else (b, a)
        if agent.role == "admin":
            return True     # l'administrateur communique avec tous les clients de tous ses agents
        ag = agent_de(client)
        return ag is not None and ag.id == agent.id
    ag_a, ag_b = agent_de(a), agent_de(b)
    return ag_a is not None and ag_b is not None and ag_a.id == ag_b.id


def _clients_contenant(clients, q):
    """Clients dont la fiche contient `q` dans une cellule : lu dans le fichier Parquet de la table de la fiche quand c'est possible
    (créé / mis à jour au besoin), sinon la recherche SQL sur ValeurChamp d'avant — même résultat."""
    from . import parquet_recherche
    from .models import Enregistrement, TableDynamique
    sql = Q(enregistrement_client__valeurs__valeur__icontains=q)
    try:
        ids_tables = set(Enregistrement.objects.filter(id__in=clients.values("enregistrement_client_id")).values_list("table_id", flat=True))
        if not ids_tables:
            return clients.none()
        condition = Q()
        for table in TableDynamique.objects.filter(id__in=ids_tables):
            trouves = parquet_recherche.ids_correspondants(table, [parquet_recherche.predicat_texte(q)])
            if trouves and trouves[0] is not None:
                condition |= Q(enregistrement_client_id__in=list(trouves[0]))
            else:
                condition |= Q(enregistrement_client__table_id=table.id) & sql
        return clients.filter(condition).distinct()
    except Exception:
        import logging
        logging.getLogger(__name__).exception(_gt("rg5.msg.009"))
        return clients.filter(sql).distinct()


def interlocuteurs(m, q="", limite=LIMITE_CONTACTS):
    """Personnes que `m` peut contacter (recherche par nom, courriel ou contenu de la fiche client)."""
    q = (q or "").strip()
    base = MembreEntreprise.objects.filter(entreprise=m.entreprise_id).exclude(id=m.id).exclude(role="caisse").select_related("utilisateur", "enregistrement_client")
    if m.role == "admin":
        staff, clients = base.filter(role__in=ROLES_EQUIPE), base.filter(role="lecteur")   # tous les clients de tous ses agents
    elif est_equipe(m):
        staff, clients = base.filter(role__in=ROLES_EQUIPE), clients_de(m).exclude(id=m.id)
    else:
        ag = agent_de(m)
        ids_staff = [x.id for x in (ag, chef(m.entreprise)) if x is not None]   # son agent + l'administrateur, pas les autres agents
        staff = base.filter(id__in=ids_staff)
        clients = clients_de(ag).exclude(id=m.id) if ag is not None else base.none()
    if q:
        staff = staff.filter(Q(utilisateur__username__icontains=q) | Q(utilisateur__first_name__icontains=q)
                             | Q(utilisateur__last_name__icontains=q) | Q(utilisateur__email__icontains=q))
        clients = _clients_contenant(clients, q)
    liste = list(staff.order_by("utilisateur__first_name", "utilisateur__username")[:limite])
    liste += list(clients.order_by("id")[:max(0, limite - len(liste))])
    return liste


# ── Conversations ─────────────────────────────────────────────────────────────
def _ajouter(conv, m):
    """Inscrit `m` comme participant ; un nouvel arrivant ne voit pas tout l'historique en « non lu »."""
    if Participant.objects.filter(conversation=conv, membre=m).exists():
        return
    dernier = conv.messages.order_by("-id").values_list("id", flat=True).first() or 0
    Participant.objects.get_or_create(conversation=conv, membre=m, defaults={"dernier_lu_id": dernier})


def conversation_directe(a, b):
    cle = f"{min(a.id, b.id)}-{max(a.id, b.id)}"
    conv, _ = Conversation.objects.get_or_create(entreprise_id=a.entreprise_id, cle=cle, defaults={"type": "direct"})
    _ajouter(conv, a)
    _ajouter(conv, b)
    return conv


def groupe_entreprise(m):
    conv, _ = Conversation.objects.get_or_create(entreprise_id=m.entreprise_id, type="groupe_entreprise")
    _ajouter(conv, m)
    return conv


def groupe_clients(agent, m):
    conv, _ = Conversation.objects.get_or_create(entreprise_id=agent.entreprise_id, type="groupe_clients", agent=agent)
    _ajouter(conv, m)
    return conv


def synchroniser_groupes(m):
    """Garantit que `m` est inscrit à ses groupes (appelé à chaque ouverture de la messagerie)."""
    if est_equipe(m):
        groupe_entreprise(m)
        groupe_clients(m, m)
    else:
        ag = agent_de(m)
        if ag is not None:
            groupe_clients(ag, m)
            groupe_clients(ag, ag)


# ── Discussions privées (« salons ») : le créateur choisit qui participe — messages et appels à huis clos ─────────────
LIMITE_SALON = 50           # participants au plus (un appel, lui, est borné par appels.MAX_PARTICIPANTS)


def peut_gerer_salon(m, conv):
    """Le créateur (et l'administrateur, le chef) peuvent ajouter ou retirer des participants."""
    return conv.type == "salon" and peut_acceder(m, conv) and (conv.createur_id == m.id or _a_pouvoir(m, "messagerie_admin"))


def participants_salon(conv):
    """[(membre, nom)] des participants d'un salon, créateur en tête."""
    membres = [x.membre for x in conv.participants.select_related("membre__utilisateur", "membre__enregistrement_client")]
    cache = noms(membres)
    membres.sort(key=lambda x: (x.id != conv.createur_id, cache[x.id].lower()))
    return [(x, cache[x.id]) for x in membres]


def _invitables(m, ids):
    """Membres que `m` a le droit d'inviter parmi `ids` : la règle de la messagerie (peut_parler), jamais une autre."""
    try:
        ids = {int(i) for i in (ids or [])}
    except (TypeError, ValueError):
        return None
    ids.discard(m.id)
    trouves = [x for x in MembreEntreprise.objects.filter(id__in=ids, entreprise_id=m.entreprise_id).select_related("utilisateur", "enregistrement_client")
               if peut_parler(m, x)]
    return trouves if len(trouves) == len(ids) else None


def creer_salon(m, titre, membre_ids):
    """Crée une discussion privée. Renvoie (conversation, None) ou (None, message d'erreur)."""
    invites = _invitables(m, membre_ids)
    if invites is None:
        return None, t("engine/messagerie.py:g:209:22")
    if not invites:
        return None, t("engine/messagerie.py:g:211:22")
    if len(invites) + 1 > LIMITE_SALON:
        return None, tf("engine/messagerie.py:g:213:22", v1=LIMITE_SALON)
    titre = " ".join(str(titre or "").split())[:120]
    if not titre:
        cache = noms(invites)
        titre = ", ".join(cache[x.id] for x in invites[:3]) + (f" +{len(invites) - 3}" if len(invites) > 3 else "")
    conv = Conversation.objects.create(entreprise_id=m.entreprise_id, type="salon", titre=titre, createur=m)
    for x in [m] + invites:
        _ajouter(conv, x)
    return conv, None


def ajouter_au_salon(m, conv, membre_ids):
    if not peut_gerer_salon(m, conv):
        return t("engine/messagerie.py:g:226:16")
    invites = _invitables(m, membre_ids)
    if invites is None or not invites:
        return t("engine/messagerie.py:g:229:16")
    if conv.participants.count() + len(invites) > LIMITE_SALON:
        return tf("engine/messagerie.py:g:231:16", v1=LIMITE_SALON)
    for x in invites:
        _ajouter(conv, x)
    return None


def retirer_du_salon(m, conv, membre_id):
    """Retire un participant (le créateur ne peut pas être retiré) ; chacun peut se retirer lui-même (`quitter`)."""
    try:
        membre_id = int(membre_id)
    except (TypeError, ValueError):
        return _gt("engine/messagerie.py:281:16")
    if membre_id != m.id and not peut_gerer_salon(m, conv):
        return t("engine/messagerie.py:g:244:16")
    if membre_id == conv.createur_id and membre_id != m.id:
        return t("engine/messagerie.py:g:246:16")
    if not peut_acceder(m, conv):
        return t("engine/messagerie.py:g:248:16")
    conv.participants.filter(membre_id=membre_id).delete()
    return None


def autre_participant(conv, m):
    p = conv.participants.exclude(membre_id=m.id).select_related("membre__utilisateur", "membre__enregistrement_client").first()
    return p.membre if p else None


def peut_acceder(m, conv):
    """Règle d'accès à une conversation (lecture et écriture)."""
    if conv.entreprise_id != m.entreprise_id:
        return False
    if conv.type == "salon":
        return conv.participants.filter(membre_id=m.id).exists()    # huis clos : seuls les participants choisis par le créateur
    if conv.type == "groupe_entreprise":
        return est_equipe(m)
    if conv.type == "groupe_clients":
        return m.id == conv.agent_id or (m.role == "lecteur" and (agent_de(m) or m).id == conv.agent_id)
    if not conv.participants.filter(membre_id=m.id).exists():
        return False
    autre = autre_participant(conv, m)
    return autre is not None and peut_parler(m, autre)


def titre_conversation(conv, m):
    if conv.type == "salon":
        return conv.titre or t("engine/messagerie.py:g:276:30")
    if conv.type == "groupe_entreprise":
        return t("engine/messagerie.py:g:278:16")
    if conv.type == "groupe_clients":
        if m.id == conv.agent_id:
            return t("engine/messagerie.py:g:281:20")
        return t("engine/messagerie.py:g:282:16") + noms([conv.agent])[conv.agent_id]
    autre = autre_participant(conv, m)
    return (noms([autre])[autre.id] if autre else t("engine/messagerie.py:g:284:51"))


def _apercu(msg):
    if msg is None:
        return ""
    if msg.supprime:
        return t("engine/messagerie.py:g:291:16")
    if msg.structure:
        return (msg.texte[:80] if msg.texte else t("engine/messagerie.py:g:293:50"))
    return (msg.texte[:80] if msg.texte else t("engine/messagerie.py:g:294:46") if msg.enregistrement_id and not msg.fichier else {"image": "Image", "audio": t("engine/messagerie.py:g:294:137"), "video": t("engine/messagerie.py:g:294:163")}.get(msg.type_media, msg.nom_fichier or td("engine/messagerie.py:g:295:281")))


def _cible(conv, m):
    """Sous-onglet de la barre latérale où ranger la conversation : « clients », « equipe » (agents) ou « autres » (salons, entre clients)."""
    if conv.type == "groupe_entreprise":
        return "equipe"
    if conv.type == "groupe_clients":
        return "clients" if est_equipe(m) else "equipe"      # un client n'a qu'un interlocuteur : l'équipe
    if conv.type == "direct":
        autre = autre_participant(conv, m)
        if autre is None:
            return "equipe"
        if est_equipe(m):
            return "equipe" if est_equipe(autre) else "clients"
        return "equipe" if est_equipe(autre) else "autres"   # client : l'équipe d'un côté, les autres clients de l'autre
    return "autres"


def lister(m, synchroniser=True):
    """Conversations de `m` (les plus récentes d'abord) avec aperçu et nombre de messages non lus.
    `synchroniser` : inscrit d'abord `m` à ses groupes (premier appel de la page ; les rafraîchissements l'évitent)."""
    if synchroniser:
        synchroniser_groupes(m)
    lus = dict(Participant.objects.filter(membre=m).values_list("conversation_id", "dernier_lu_id"))
    muets, convs_muettes = sourdines_de(m)
    convs = list(Conversation.objects.filter(id__in=list(lus)).select_related("agent__utilisateur")[:LIMITE_CONVERSATIONS * 2])
    res = []
    for c in convs:
        if not peut_acceder(m, c):
            continue
        dernier = c.messages.order_by("-id").first()
        if dernier is None and c.type == "direct":
            continue          # discussion ouverte mais encore vide : n'encombre pas la liste
        non_lus_qs = c.messages.filter(id__gt=lus[c.id], supprime=False).exclude(expediteur_id=m.id).exclude(expediteur_id__in=muets)
        if c.id in convs_muettes:
            non_lus_qs = non_lus_qs.filter(expediteur__role="admin")   # discussion en sourdine : seul l'administrateur peut encore sonner
        res.append({"id": c.id, "type": c.type, "cible": _cible(c, m), "titre": titre_conversation(c, m), "apercu": _apercu(dernier),
                    "date": (dernier.cree_le if dernier else c.cree_le).isoformat(), "non_lus": non_lus_qs.count(),
                    "sourdine": c.id in convs_muettes})
    res.sort(key=lambda r: r["date"], reverse=True)
    return res[:LIMITE_CONVERSATIONS]


def sourdines_de(m):
    """(ensemble des ids de personnes en sourdine, ensemble des ids de discussions en sourdine) pour `m`. L'administrateur n'y figure
    jamais, même si une ancienne ligne existait : sa voix passe toujours."""
    lignes = list(Sourdine.objects.filter(membre=m).values_list("cible_id", "conversation_id"))
    personnes = {c for c, _ in lignes if c}
    if personnes:
        personnes -= set(MembreEntreprise.objects.filter(id__in=personnes, role="admin").values_list("id", flat=True))
    return personnes, {v for _, v in lignes if v}


def en_sourdine(dest, expediteur, conv):
    """True si `dest` ne doit pas être dérangé par un message de `expediteur` dans `conv` (jamais pour un administrateur)."""
    if expediteur.role == "admin":
        return False
    return Sourdine.objects.filter(membre=dest).filter(Q(cible_id=expediteur.id) | Q(conversation_id=conv.id)).exists()


def _contexte_messages(m, liste):
    """Données groupées pour sérialiser un lot de messages sans requête par message : réactions, favoris, sourdines, citations."""
    ids = [x.id for x in liste]
    reactions = {}
    for r in ReactionMessage.objects.filter(message_id__in=ids).order_by("id"):
        d = reactions.setdefault(r.message_id, {})
        d.setdefault(r.emoji, []).append(r.membre_id)
    muets, _convs = sourdines_de(m)
    return {"reactions": reactions, "favoris": set(FavoriMessage.objects.filter(membre=m, message_id__in=ids).values_list("message_id", flat=True)),
            "muets": muets, "mes_avis": dict(AvisMessage.objects.filter(membre=m, message_id__in=ids).values_list("message_id", "valeur")),
            "suspects": _suspects(ids)}


def _suspects(ids) -> set:
    """Messages que le modèle fantôme du SIEM a signalés (lus dans le cache du SIEM ; jamais de blocage de la messagerie si le SIEM est indisponible)."""
    try:
        from . import siem_fantome_suivi
        return siem_fantome_suivi.suspects(ids)
    except Exception:
        return set()


def _citation(msg, muets, noms_cache):
    """Aperçu du message auquel `msg` répond (None s'il n'y répond pas)."""
    cible = msg.reponse_a
    if cible is None:
        return None
    if cible.expediteur_id in muets:
        return {"id": cible.id, "de": "", "apercu": t("engine/messagerie.py:g:357:53")}
    nom = noms_cache.get(cible.expediteur_id) or (noms([cible.expediteur])[cible.expediteur_id] if cible.expediteur_id else "Ancien membre")
    return {"id": cible.id, "de": nom, "apercu": _apercu(cible)}


def serialiser_message(msg, m, noms_cache, contexte=None):
    contexte = contexte or {"reactions": {}, "favoris": set(), "muets": set(), "mes_avis": {}, "suspects": set()}
    exp = msg.expediteur
    moi = exp is not None and exp.id == m.id
    if msg.expediteur_id in contexte["muets"] and not moi:
        # Personne en sourdine : le contenu ne parvient pas à `m` (il le retrouve en levant la sourdine).
        return {"id": msg.id, "moi": False, "expediteur_id": msg.expediteur_id, "expediteur_admin": False,
                "de": noms_cache.get(msg.expediteur_id, "Ancien membre"), "auto": msg.auto, "texte": "", "type": "texte",
                "supprime": False, "sourdine": True, "url": "", "nom_fichier": "", "taille": 0, "date": msg.cree_le.isoformat(), "structure": False,
                "enregistrement": None, "reponse_a": None, "modifie": False, "transfere": False, "reactions": [], "favori": False, "modifiable": False,
                "mon_avis": 0, "suspect": False}
    brut = contexte["reactions"].get(msg.id, {})
    return {"id": msg.id, "moi": moi, "expediteur_id": msg.expediteur_id, "expediteur_admin": exp is not None and exp.role == "admin",
            "de": ((noms_cache.get(msg.expediteur_id, "Ancien membre") + " (assistant)") if msg.auto else noms_cache.get(msg.expediteur_id, "Ancien membre")),
            "auto": msg.auto, "texte": "" if msg.supprime else msg.texte,
            "type": "texte" if msg.supprime else msg.type_media, "supprime": msg.supprime, "sourdine": False,
            "url": msg.fichier.url if (msg.fichier and not msg.supprime) else "", "nom_fichier": "" if msg.supprime else msg.nom_fichier,
            "taille": msg.taille, "date": msg.cree_le.isoformat(),
            "structure": bool(msg.structure and not msg.supprime),
            "enregistrement": resume_fiche(msg.enregistrement) if (msg.enregistrement_id and not msg.supprime) else None,
            "reponse_a": None if msg.supprime else _citation(msg, contexte["muets"], noms_cache),
            "modifie": bool(msg.modifie_le) and not msg.supprime, "transfere": msg.transfere and not msg.supprime,
            "reactions": [] if msg.supprime else [{"emoji": e, "nb": len(ids), "moi": m.id in ids} for e, ids in brut.items()],
            "favori": msg.id in contexte["favoris"], "modifiable": peut_modifier(m, msg),
            "mon_avis": 0 if moi else contexte["mes_avis"].get(msg.id, 0), "suspect": (not moi) and (not msg.supprime) and msg.id in contexte["suspects"]}


def messages(m, conv, apres=0):
    """Messages de la conversation après l'id `apres` (les LIMITE_MESSAGES derniers au premier appel) ; marque comme lus."""
    qs = conv.messages.select_related("expediteur__utilisateur", "expediteur__enregistrement_client", "enregistrement__table", "reponse_a")
    if apres:
        liste = list(qs.filter(id__gt=apres)[:LIMITE_MESSAGES])
    else:
        liste = list(reversed(list(qs.order_by("-id")[:LIMITE_MESSAGES])))
    cache = noms([x.expediteur for x in liste if x.expediteur is not None])
    if liste:
        Participant.objects.filter(conversation=conv, membre=m, dernier_lu_id__lt=liste[-1].id).update(dernier_lu_id=liste[-1].id)
        marquer_notifications_lues(m, conv)
    contexte = _contexte_messages(m, liste)
    return [serialiser_message(x, m, cache, contexte) for x in liste]


def type_media(nom, type_mime):
    """image / audio / video / fichier d'après le type MIME déclaré, sinon l'extension."""
    mime = (type_mime or mimetypes.guess_type(nom or "")[0] or "").lower()
    base = mime.split("/")[0]
    return base if base in ("image", "audio", "video") else "fichier"


def nettoyer_nom(nom):
    return (os.path.basename((nom or "").replace("\\", "/")).strip() or "fichier")[:200]


def fiche_liable(m, enregistrement_id):
    """La fiche que `m` peut lier à un message : seulement une fiche qu'il a créée lui-même, dans son entreprise (jamais celle d'un autre)."""
    try:
        eid = int(enregistrement_id or 0)
    except (TypeError, ValueError):
        return None
    return (Enregistrement.objects.select_related("table").filter(id=eid, cree_par_id=m.utilisateur_id, table__entreprise_id=m.entreprise_id).first()
            if eid else None)


def _libelles(e):
    """Valeurs lisibles de la fiche, sans identifiants d'accès : [(nom du champ, valeur)], 5 au plus."""
    d = e.to_dict_lisible()
    for cle in ("Matricule", td("engine/messagerie.py:g:427:30")):
        d.pop(cle, None)
    return [(str(k)[:40], str(v)[:80]) for k, v in d.items() if v not in (None, "", [], {})][:5]


def resume_fiche(e):
    return {"id": e.id, "table": e.table.nom, "lignes": [{"champ": k, "valeur": v} for k, v in _libelles(e)]}


def fiches_de(m, q="", limite=30):
    """Fiches que `m` a créées (les plus récentes d'abord), filtrables par un mot-clé : sert au sélecteur « Lier une fiche »."""
    q = (q or "").strip().lower()
    res = []
    for e in Enregistrement.objects.select_related("table").filter(cree_par_id=m.utilisateur_id, table__entreprise_id=m.entreprise_id).order_by("-id")[:200]:
        lib = _libelles(e)
        if q and q not in (e.table.nom + " " + " ".join(v for _k, v in lib)).lower():
            continue
        res.append({"id": e.id, "table": e.table.nom, "titre": lib[0][1] if lib else f"Fiche {e.id}", "date": e.cree_le.isoformat()})
        if len(res) >= limite:
            break
    return res


def envoyer(m, conv, texte="", fichier=None, auto=False, enregistrement=None, structure=False, reponse_a=None, transfere=False, appel=False):
    """Crée le message ; renvoie (message, None) ou (None, erreur lisible). Notifie les destinataires ; `auto` = réponse du
    chatbot de `m` (elle ne déclenche jamais de nouvelle réponse automatique)."""
    texte = (texte or "").strip()[:5000]
    if not texte and fichier is None and enregistrement is None:
        return None, t("engine/messagerie.py:g:454:22")
    if fichier is not None and TAILLE_MAX and fichier.size > TAILLE_MAX:
        return None, _gtf("rg5.msg.089", v1=TAILLE_MAX // (1024 * 1024))
    if reponse_a is not None and (reponse_a.conversation_id != conv.id or reponse_a.supprime):
        reponse_a = None       # on ne répond qu'à un message existant de la même discussion
    msg = Message(conversation=conv, expediteur=m, texte=texte, auto=auto, enregistrement=enregistrement, structure=structure,
                  reponse_a=reponse_a, transfere=transfere)
    if fichier is not None:
        msg.nom_fichier = nettoyer_nom(fichier.name)
        msg.type_media = type_media(msg.nom_fichier, getattr(fichier, "content_type", ""))
        msg.taille = fichier.size
        msg.fichier = fichier
    msg.save()
    journaliser(m, "MessageSent", conversation_id=conv.id, message_id=msg.id, type_conversation=conv.type, auto=auto,
                structure=structure, fichier=bool(fichier is not None), texte_sha256=empreinte(texte), longueur=len(texte),
               reponse_a=reponse_a.id if reponse_a is not None else None, transfere=transfere)
    Conversation.objects.filter(id=conv.id).update(dernier_message_le=msg.cree_le)
    Participant.objects.filter(conversation=conv, membre=m).update(dernier_lu_id=msg.id)
    try:   # la notification ou le chatbot ne font jamais échouer l'envoi
        notifier_message(msg, conv, m)
        if not auto and not structure and not appel:   # un message d'appel ne déclenche pas le chatbot
            from . import chatbot
            chatbot.declencher(msg)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/messagerie.py:g:480:47"), msg.id)
    return msg, None


# ── Structures partagées entre agents (zip « modèle » : voir cycle.py) ────────────────────────────────────────
# Droits : l'administrateur peut tout (toute l'entreprise, ou l'espace de quelques agents) ; un agent est CONFINÉ à son espace
# (ses tables autorisées) en envoi comme en import. L'import est libre (aucune validation du chef). Une structure ne circule qu'entre
# membres de l'ÉQUIPE : jamais dans un groupe de clients ni vers un client. Le zip est généré par le serveur au moment de l'envoi
# (Message.structure) : un zip quelconque téléversé n'est jamais importable comme « structure ».
ERREURS_STRUCTURE = {
    "espace_non_defini": tl("engine/messagerie.py:g:489:26"),
    "aucune_table": tl("engine/messagerie.py:g:490:21"),
    "droits_insuffisants": "Droits insuffisants.",
    "structure_vide": tl("engine/messagerie.py:g:492:23"),
    "utilisateur_requis": tl("engine/messagerie.py:g:493:27"),
}


def conversation_structure_ok(m, conv):
    """Une structure s'échange entre membres de l'équipe : discussion à deux équipe/équipe, ou groupe de l'entreprise."""
    if not est_equipe(m) or not peut_acceder(m, conv):
        return False
    if conv.type == "groupe_entreprise":
        return True
    if conv.type == "direct":
        autre = autre_participant(conv, m)
        return autre is not None and est_equipe(autre)
    return False


def agents_ciblables(m):
    """Agents dont l'administrateur peut envoyer l'espace : [(id du membre, nom, nombre de tables autorisées)] (vide pour un agent)."""
    if not _a_pouvoir(m, "messagerie_admin"):
        return []
    liste = list(MembreEntreprise.objects.filter(entreprise_id=m.entreprise_id, role="agent").select_related("utilisateur"))
    return [{"id": a.id, "nom": nom_equipe(a), "tables": len(a.tables_autorisees or [])} for a in liste]


def envoyer_structure(m, conv, agents=None, texte=""):
    """Génère la structure de l'espace de `m` et l'envoie dans `conv`. Administrateur : toute l'entreprise, ou l'espace des `agents` choisis
    (identifiants de membres). Agent : son espace seulement (`agents` ignoré). Renvoie (message, None) ou (None, erreur lisible)."""
    from django.core.files.base import ContentFile
    from . import cycle
    if not conversation_structure_ok(m, conv):
        return None, t("engine/messagerie.py:g:523:22")
    try:
        ids = [int(a) for a in (agents or [])] if _a_pouvoir(m, "messagerie_admin") else None
        tables = cycle.perimetre_tables(m.entreprise, m, ids)
    except (TypeError, ValueError) as exc:
        return None, ERREURS_STRUCTURE.get(str(exc), td("engine/messagerie.py:g:529:54"))
    tampon = io.BytesIO()
    comptage = cycle.generer_zip_modele_vers(m.entreprise, tampon, tables=tables)
    n = comptage.get("engine.tabledynamique", 0)
    if n == 0:
        return None, t("engine/messagerie.py:g:533:22")
    nom = f"structure_{'espace_' if tables is not None else ''}{m.entreprise.slug}.zip"
    resume = f"{n} table{'s' if n > 1 else ''}" + (t("engine/messagerie.py:g:535:52") if m.role != "admin" else (t("engine/messagerie.py:g:535:101") if tables is not None else t("engine/messagerie.py:g:535:158")))
    texte = (texte or "").strip()[:300]
    return envoyer(m, conv, tf("engine/messagerie.py:g:537:29", v1=texte, v2=resume).strip() if texte else tf("engine/messagerie.py:g:537:93", v1=resume),
                   ContentFile(tampon.getvalue(), name=nom), structure=True)


def importer_structure(m, msg):
    """Importe (libre, sans validation) la structure d'un message dans l'espace de `m`. Administrateur : tout ; agent : confiné (seulement
    les tables et ce qui en dépend, ajoutés à son espace). Renvoie ({objets, renommees, tables, confine}, None) ou (None, erreur lisible)."""
    from . import cycle
    if not est_equipe(m):
        return None, t("engine/messagerie.py:g:546:22")
    if msg.supprime or not msg.structure or not msg.fichier:
        return None, t("engine/messagerie.py:g:548:22")
    if not conversation_structure_ok(m, msg.conversation):
        return None, t("engine/messagerie.py:g:550:22")
    if msg.expediteur_id == m.id:
        return None, t("engine/messagerie.py:g:552:22")
    try:
        with msg.fichier.open("rb") as f, zipfile.ZipFile(f) as zf:
            manifeste = json.loads(zf.read(cycle.NOM_MANIFESTE).decode("utf-8"))
            if manifeste.get("mode") != "modele":
                return None, td("engine/messagerie.py:g:558:30")
            return cycle.importer_modele(m.entreprise, zf, m.utilisateur, membre=m), None
    except ValueError as exc:
        return None, ERREURS_STRUCTURE.get(str(exc), td("engine/messagerie.py:g:561:54"))
    except (zipfile.BadZipFile, KeyError, OSError):
        return None, t("engine/messagerie.py:g:562:22")


# ── Notifications de messages (équipe ET clients : le client les retrouve à sa reconnexion) ─────────────────
def destinataires(conv, expediteur):
    """Membres à notifier d'un nouveau message de `expediteur` dans `conv` (ceux qui y ont accès, sauf l'expéditeur)."""
    if conv.type == "direct":
        cibles = [x.membre for x in conv.participants.select_related("membre__utilisateur", "membre__enregistrement_client")]
    elif conv.type == "salon":
        cibles = [x.membre for x in conv.participants.select_related("membre__utilisateur", "membre__enregistrement_client")]
    elif conv.type == "groupe_entreprise":
        cibles = list(MembreEntreprise.objects.filter(entreprise_id=conv.entreprise_id, role__in=ROLES_EQUIPE).select_related("utilisateur"))
    else:
        cibles = [conv.agent] + list(clients_de(conv.agent)) if conv.agent_id else []
    return [c for c in cibles if c.id != expediteur.id and peut_acceder(c, conv)]


def _pousser(notif):
    from .notifications import pousser_notification
    pousser_notification(notif)


def notifier_message(msg, conv, expediteur):
    """Une SEULE notification non lue par discussion et par destinataire : elle est mise à jour (compteur, aperçu) tant qu'elle
    n'est pas lue, puis une nouvelle apparaît au message suivant."""
    de = noms([expediteur])[expediteur.id]
    apercu = _apercu(msg)
    for dest in destinataires(conv, expediteur):
        if en_sourdine(dest, expediteur, conv):
            continue       # sourdine : ni notification ni pastille (l'administrateur passe toujours)
        titre = tf("engine/messagerie.py:g:587:17", v1=de) if conv.type == "direct" else tf("engine/messagerie.py:g:587:74", v1=titre_conversation(conv, dest))
        notif = Notification.objects.filter(destinataire=dest, conversation=conv, type_notification="message_recu", lue=False).first()
        if notif is None:
            notif = Notification.objects.create(entreprise_id=conv.entreprise_id, destinataire=dest, type_notification="message_recu",
                                                titre=titre, message=apercu, conversation=conv)
            _pousser(notif)
        else:
            n = conv.messages.filter(id__gt=Participant.objects.filter(conversation=conv, membre=dest).values_list("dernier_lu_id", flat=True).first() or 0,
                                     supprime=False).exclude(expediteur_id=dest.id).count()
            Notification.objects.filter(id=notif.id).update(titre=titre, message=(tf("engine/messagerie.py:g:595:83", v1=n) if n > 1 else "") + apercu,
                                                            cree_le=timezone.now())
            _pousser(Notification.objects.select_related("destinataire").get(id=notif.id))


def notifications_non_lues(m, limite=30):
    """Notifications de messages non lues de `m` (équipe comme client) : sert à l'annonce à la reconnexion."""
    qs = Notification.objects.filter(destinataire=m, type_notification="message_recu", lue=False).order_by("-cree_le")
    return [{"id": n.id, "titre": n.titre, "message": n.message, "conversation_id": n.conversation_id, "date": n.cree_le.isoformat()}
            for n in qs[:limite]], qs.count()


def marquer_notifications_lues(m, conv=None):
    qs = Notification.objects.filter(destinataire=m, type_notification="message_recu", lue=False)
    if conv is not None:
        qs = qs.filter(conversation=conv)
    return qs.update(lue=True, lue_le=timezone.now())


def supprimer_message(m, msg):
    """Seul l'expéditeur retire son message (le fichier est effacé du disque, la trace « supprimé » reste)."""
    if msg.expediteur_id != m.id:
        return False
    if msg.fichier:
        msg.fichier.delete(save=False)
    Message.objects.filter(id=msg.id).update(supprime=True, texte="", fichier="", nom_fichier="", enregistrement=None, structure=False,
                                             reponse_a=None, modifie_le=None)
    ReactionMessage.objects.filter(message_id=msg.id).delete()
    FavoriMessage.objects.filter(message_id=msg.id).delete()
    AvisMessage.objects.filter(message_id=msg.id).delete()
    return True


def rechercher(m, q, limite=40):
    """Recherche de texte / nom de fichier dans les conversations accessibles à `m`."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    ids = list(Participant.objects.filter(membre=m).values_list("conversation_id", flat=True))
    muets, _convs = sourdines_de(m)
    trouves = (Message.objects.filter(conversation_id__in=ids, supprime=False).exclude(expediteur_id__in=muets)
               .filter(Q(texte__icontains=q) | Q(nom_fichier__icontains=q))
               .select_related("conversation__agent__utilisateur", "expediteur__utilisateur", "expediteur__enregistrement_client").order_by("-id")[:limite * 2])
    res = []
    for x in trouves:
        if peut_acceder(m, x.conversation):
            res.append({"conversation_id": x.conversation_id, "titre": titre_conversation(x.conversation, m), "message_id": x.id,
                        "de": noms([x.expediteur])[x.expediteur_id] if x.expediteur else "Ancien membre", "apercu": _apercu(x), "date": x.cree_le.isoformat()})
        if len(res) >= limite:
            break
    return res


# ── Corriger, transférer, réagir, favoris, sourdine ──────────────────────────────────────────────────────────
def peut_modifier(m, msg):
    """Seul l'auteur corrige son message, dans les 24 h, s'il n'est ni supprimé, ni une réponse automatique, ni une structure partagée."""
    return (msg.expediteur_id == m.id and not msg.supprime and not msg.auto and not msg.structure
            and timezone.now() - msg.cree_le <= DELAI_MODIFICATION)


def modifier_message(m, msg, texte):
    """Corrige le texte de son message : (True, None) ou (False, erreur lisible). Un message sans pièce ni fiche ne peut pas devenir vide."""
    texte = (texte or "").strip()[:5000]
    if msg.expediteur_id != m.id or msg.supprime or msg.auto or msg.structure:
        return False, t("engine/messagerie.py:g:657:23")
    if timezone.now() - msg.cree_le > DELAI_MODIFICATION:
        return False, t("engine/messagerie.py:g:659:23")
    if not texte and not msg.fichier and not msg.enregistrement_id:
        return False, t("engine/messagerie.py:g:661:23")
    if texte == msg.texte:
        return True, None
    Message.objects.filter(id=msg.id).update(texte=texte, modifie_le=timezone.now())
    journaliser(m, "MessageEdited", conversation_id=msg.conversation_id, message_id=msg.id,
                texte_sha256=empreinte(texte), longueur=len(texte))
    return True, None


def transferer(m, msg, conversation_ids):
    """Transfère une copie de `msg` (texte, pièce jointe, fiche si elle est la sienne) vers d'autres discussions auxquelles `m` a accès.
    Renvoie (liste des ids de nouveaux messages, erreur lisible ou None). Une structure partagée ne se transfère jamais."""
    if not peut_acceder(m, msg.conversation) or msg.supprime:
        return [], t("engine/messagerie.py:g:674:20")
    if msg.structure:
        return [], t("engine/messagerie.py:g:676:20")
    ids = []
    for brut in conversation_ids if isinstance(conversation_ids, list) else []:
        try:
            ids.append(int(brut))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))[:LIMITE_TRANSFERT]
    cibles = [c for c in Conversation.objects.filter(id__in=ids) if peut_acceder(m, c)]
    if not cibles:
        return [], t("engine/messagerie.py:g:686:20")
    fiche = fiche_liable(m, msg.enregistrement_id) if msg.enregistrement_id else None
    nouveaux = []
    for conv in cibles:
        copie = None
        if msg.fichier:
            try:
                with msg.fichier.open("rb") as f:
                    copie = ContentFile(f.read(), name=msg.nom_fichier or "fichier")
            except (OSError, ValueError):
                copie = None
        if not msg.texte and copie is None and fiche is None:
            continue       # rien de transférable (par ex. fiche d'un autre, ou fichier introuvable)
        nouveau, erreur = envoyer(m, conv, msg.texte, copie, enregistrement=fiche, transfere=True)
        if nouveau is not None:
            nouveaux.append(nouveau.id)
    if not nouveaux:
        return [], t("engine/messagerie.py:g:703:20")
    journaliser(m, "MessageForwarded", message_id=msg.id, conversation_id=msg.conversation_id, destinations=[c.id for c in cibles], copies=nouveaux)
    return nouveaux, None


def reagir_message(m, msg, emoji):
    """Réaction emoji : une par membre et par message ; la même une seconde fois la retire, une autre la remplace. Renvoie (None, erreur) ou (état, None)."""
    if emoji not in EMOJIS:
        return None, t("engine/messagerie.py:g:711:22")
    if msg.supprime or not peut_acceder(m, msg.conversation):
        return None, t("engine/messagerie.py:g:713:22")
    actuelle = ReactionMessage.objects.filter(message=msg, membre=m).first()
    if actuelle is not None and actuelle.emoji == emoji:
        actuelle.delete()
    elif actuelle is not None:
        ReactionMessage.objects.filter(id=actuelle.id).update(emoji=emoji)
    else:
        ReactionMessage.objects.create(message=msg, membre=m, emoji=emoji)
    return resume_reactions(m, msg.id), None


def resume_reactions(m, message_id):
    brut = {}
    for r in ReactionMessage.objects.filter(message_id=message_id).order_by("id"):
        brut.setdefault(r.emoji, []).append(r.membre_id)
    return [{"emoji": e, "nb": len(ids), "moi": m.id in ids} for e, ids in brut.items()]


def donner_avis(m, msg, valeur):
    """Avis privé sur un message REÇU : 1 = bon, -1 = mauvais, 0 = le retirer ; le même avis une seconde fois le retire. Le modèle fantôme du
    SIEM l'apprend au prochain passage (siem_fantome_suivi). Renvoie (avis en vigueur, None) ou (None, erreur)."""
    try:
        valeur = int(valeur)
    except (TypeError, ValueError):
        valeur = None
    if valeur not in (-1, 0, 1):
        return None, _gt("engine/messagerie.py:798:22")
    if msg.supprime or not peut_acceder(m, msg.conversation):
        return None, t("engine/messagerie.py:g:734:22")
    if msg.expediteur_id == m.id:
        return None, _i18n_t("rg2.messagerie.1")
    actuel = AvisMessage.objects.filter(message=msg, membre=m).first()
    if valeur == 0 or (actuel is not None and actuel.valeur == valeur):
        AvisMessage.objects.filter(message=msg, membre=m).delete()
        return 0, None
    AvisMessage.objects.update_or_create(message=msg, membre=m, defaults={"valeur": valeur})
    return valeur, None


def basculer_favori(m, msg):
    """Met / retire l'étoile (privée). Renvoie (état favori, erreur)."""
    if msg.supprime or not peut_acceder(m, msg.conversation):
        return None, t("engine/messagerie.py:g:734:22")
    deja = FavoriMessage.objects.filter(message=msg, membre=m)
    if deja.exists():
        deja.delete()
        return False, None
    FavoriMessage.objects.create(message=msg, membre=m)
    return True, None


def favoris_de(m, limite=60):
    """Mes messages favoris (les plus récents d'abord), encore accessibles et non supprimés."""
    res = []
    muets, _convs = sourdines_de(m)
    for f in (FavoriMessage.objects.filter(membre=m, message__supprime=False).exclude(message__expediteur_id__in=muets)
              .select_related("message__conversation__agent__utilisateur", "message__expediteur__utilisateur", "message__expediteur__enregistrement_client")
              .order_by("-id")[:limite * 2]):
        x = f.message
        if peut_acceder(m, x.conversation):
            res.append({"conversation_id": x.conversation_id, "titre": titre_conversation(x.conversation, m), "message_id": x.id,
                        "de": noms([x.expediteur])[x.expediteur_id] if x.expediteur else "Ancien membre", "apercu": _apercu(x), "date": x.cree_le.isoformat()})
        if len(res) >= limite:
            break
    return res


def mettre_en_sourdine(m, membre_id=None, conversation_id=None, actif=True):
    """Sourdine d'une personne (`membre_id`) ou d'une discussion (`conversation_id`) ; `actif=False` la lève. Renvoie (True, None) ou (False, erreur).
    Règles : jamais soi-même ; jamais un administrateur (ses messages arrivent toujours) ; seulement une personne ou une discussion accessible."""
    if bool(membre_id) == bool(conversation_id):
        return False, t("engine/messagerie.py:g:763:23")
    if membre_id:
        cible = MembreEntreprise.objects.filter(id=membre_id, entreprise_id=m.entreprise_id).first()
        if cible is None or cible.id == m.id:
            return False, _gt("rg5.msg.010")
        if actif and cible.role == "admin":
            return False, t("engine/messagerie.py:g:769:27")
        if actif and not peut_parler(m, cible):
            return False, t("engine/messagerie.py:g:771:27")
        if actif:
            Sourdine.objects.get_or_create(membre=m, cible=cible)
        else:
            Sourdine.objects.filter(membre=m, cible=cible).delete()
        journaliser(m, "ContactMuted" if actif else "ContactUnmuted", cible_membre_id=cible.id)
        return True, None
    conv = Conversation.objects.filter(id=conversation_id).first()
    if conv is None or not peut_acceder(m, conv):
        return False, _gt("rg5.msg.008")
    if actif:
        Sourdine.objects.get_or_create(membre=m, conversation=conv)
    else:
        Sourdine.objects.filter(membre=m, conversation=conv).delete()
    journaliser(m, "ConversationMuted" if actif else "ConversationUnmuted", conversation_id=conv.id)
    return True, None


def sourdines_liste(m):
    """Personnes en sourdine : [{id, nom}] (sert à les lever depuis la page)."""
    personnes, _convs = sourdines_de(m)
    membres = list(MembreEntreprise.objects.filter(id__in=personnes).select_related("utilisateur", "enregistrement_client"))
    cache = noms(membres)
    return [{"id": x.id, "nom": cache[x.id]} for x in membres]


# ── Publicités ────────────────────────────────────────────────────────────────
def serialiser_publicite(p, avec_agent=False, cache=None, resume=None, client=None):
    """`avec_agent` = vue de l'équipe (nom de l'agent, table liée même si elle n'est plus proposée aux clients).
    `client` = le membre lecteur qui regarde : il reçoit `formulaire` (table liée, seulement si elle lui est toujours ouverte)
    et `agent_id` (pour ouvrir la conversation avec son agent)."""
    d = {"id": p.id, "titre": p.titre, "contexte": p.contexte, "type": p.type_media, "url": p.fichier.url if p.fichier else "",
         "nom_fichier": p.nom_fichier, "publiee": p.publiee, "date": p.cree_le.isoformat()}
    if avec_agent:
        d["agent"] = (cache or noms([p.agent]))[p.agent_id]
        d["table_id"] = p.table_id
        d["table_nom"] = p.table.nom if p.table_id else ""
        d["table_ouverte"] = bool(p.table_id and p.table.expose_client)
    if client is not None:
        t_ = p.table if p.table_id else None
        d["formulaire"] = {"id": t_.id, "nom": t_.nom} if t_ is not None and formulaire_ouvert_au_client(client, t_) else None
        d["agent_id"] = p.agent_id
    if resume is not None:
        d["reactions"] = resume
    return d


# ── Lien publicité → formulaire (table) ───────────────────────────────────────
def formulaire_ouvert_au_client(client, table):
    """True si `client` (lecteur) peut remplir le formulaire de `table` : la table est mise à disposition des clients
    (expose_client = formulaire visible), c'est une table ordinaire de SON entreprise, et l'agent ne la réserve pas à d'autres clients.
    Même règle que l'espace client (views.espace_client) : le bouton de la publicité n'ouvre jamais plus que le menu du client."""
    from .views import _table_permise_client
    if table is None or client is None or table.entreprise_id != client.entreprise_id:
        return False
    if not table.expose_client or table.est_table_membres or getattr(table, "est_table_produits", False):
        return False
    return _table_permise_client(client, table)


def tables_liables(m):
    """Tables qu'une personne de l'équipe peut lier à une publicité : celles qu'elle voit et qui sont mises à disposition des
    clients sous forme de formulaire (un groupe de tables = une seule entrée, comme dans « Tables » du client)."""
    from .views import _table_autorisee_agent
    from .vue_groupe import tables_generales
    qs = (TableDynamique.objects.filter(entreprise_id=m.entreprise_id, expose_client=True)
          .exclude(est_table_membres=True).order_by("nom"))
    return tables_generales([x for x in qs if _table_autorisee_agent(m, x)])


def table_liable(m, table_id):
    """(table ou None, erreur ou None) : `table_id` vide = pas de lien ; sinon la table doit faire partie de `tables_liables(m)`."""
    if table_id in (None, "", 0, "0"):
        return None, None
    try:
        tid = int(table_id)
    except (TypeError, ValueError):
        return None, _gt("engine/messagerie.py:928:22")
    for x in tables_liables(m):
        if x.id == tid:
            return x, None
    return None, _i18n_t("rg2.messagerie.2")


def publicites_pour(m):
    """Ce que voit le client : les publicités publiées de son agent (rien pour l'équipe : elle les configure seulement)."""
    if est_equipe(m):
        return []
    ag = agent_de(m)
    return [] if ag is None else list(Publicite.objects.filter(agent=ag, publiee=True).select_related("table"))


def publicites_gerees(m):
    """Publicités qu'une personne de l'équipe configure : les siennes ; toutes celles de l'entreprise pour le chef."""
    qs = Publicite.objects.filter(entreprise_id=m.entreprise_id).select_related("agent__utilisateur", "table")
    c = chef(m.entreprise)
    return qs if (c is not None and c.id == m.id) else qs.filter(agent=m)


def diffuseurs(entreprise_id):
    """Ids des membres dont les publicités publiées sont aussi visibles de TOUS les agents : l'administrateur, et chaque agent à qui il a
    délégué le droit `publicites_equipe` (ou la liberté absolue)."""
    equipe = MembreEntreprise.objects.filter(entreprise_id=entreprise_id, role__in=ROLES_EQUIPE).select_related("utilisateur")
    return [x.id for x in equipe if _a_pouvoir(x, "publicites_equipe")]


def publicites_de_l_administration(m):
    """Ce que voit un AGENT en plus de ses propres publicités : seulement les publicités publiées par l'administrateur ou par une personne à
    qui il a délégué le droit (jamais celles des autres agents). Consultation seule. Liste vide pour un client ; l'administrateur n'en a pas
    besoin (il voit déjà toutes les publicités de l'entreprise) ; les siennes ne sont jamais répétées."""
    c = chef(m.entreprise)
    if not est_equipe(m) or (c is not None and c.id == m.id):
        return []
    ids = [i for i in diffuseurs(m.entreprise_id) if i != m.id]
    return list(Publicite.objects.filter(entreprise_id=m.entreprise_id, agent_id__in=ids, publiee=True)
                .select_related("agent__utilisateur", "table")) if ids else []


def creer_publicite(m, titre, contexte, fichier, table_id=None):
    titre = (titre or "").strip()[:200]
    if not titre:
        return None, t("engine/messagerie.py:g:826:22")
    if fichier is not None and TAILLE_MAX and fichier.size > TAILLE_MAX:
        return None, _gtf("rg5.msg.089", v1=TAILLE_MAX // (1024 * 1024))
    table, erreur = table_liable(m, table_id)
    if erreur:
        return None, erreur
    p = Publicite(entreprise_id=m.entreprise_id, agent=m, titre=titre, contexte=(contexte or "").strip()[:5000], table=table)
    if fichier is not None:
        p.nom_fichier = nettoyer_nom(fichier.name)
        p.type_media = type_media(p.nom_fichier, getattr(fichier, "content_type", ""))
        p.fichier = fichier
    p.save()
    journaliser(m, "PublicityPublished", publicite_id=p.id, publicite=p.titre, type_media=p.type_media, texte_sha256=empreinte(p.contexte),
                table_id=p.table_id)
    return p, None


# ── Journal de la chaîne et réactions des clients ─────────────────────────────
def empreinte(texte):
    """SHA-256 d'un texte : la chaîne prouve ce qui a été écrit sans le recopier (un bloc ne s'efface jamais)."""
    return hashlib.sha256((texte or "").encode("utf-8")).hexdigest()


def journaliser(m, evenement, **donnees):
    """Une transaction de la chaîne de l'entreprise (evenements.emettre : jamais bloquant). `agent_id` = utilisateur auteur : c'est la
    clé par laquelle l'ontologie relie la personne à la transaction ; `publicite_id` / `conversation_id` désignent la cible."""
    from . import evenements
    return evenements.emettre(m.entreprise_id, evenement, agent_id=m.utilisateur_id, membre_id=m.id, role=m.role, **donnees)


def _publicite_du_client(m, publicite_id):
    ag = agent_de(m) if m.role == "lecteur" else None
    return Publicite.objects.filter(id=publicite_id, agent=ag, publiee=True).first() if ag is not None else None


def reagir(m, publicite_id, valeur, texte=""):
    """Un client réagit à une publicité de SON agent : (réaction ou None, erreur lisible ou None). 1 / -1 = avis (le même avis une
    seconde fois le retire, l'autre le remplace) ; 0 = commentaire (texte obligatoire, 1000 caractères au plus)."""
    from django.db import IntegrityError
    p = _publicite_du_client(m, publicite_id)
    if p is None:
        return None, t("engine/messagerie.py:g:863:22")
    texte = (texte or "").strip()[:1000] if valeur == 0 else ""
    if valeur == 0 and not texte:
        return None, t("engine/messagerie.py:g:866:22")
    try:
        avis = None if valeur == 0 else ReactionPublicite.objects.filter(publicite=p, membre=m).exclude(valeur=0).first()
        if avis is not None and avis.valeur == valeur:
            avis.delete()
            r, evenement = None, "PublicityReactionRemoved"
        elif avis is not None:
            avis.valeur = valeur
            avis.save(update_fields=["valeur"])
            r, evenement = avis, "PublicityLiked" if valeur == 1 else "PublicityDisliked"
        else:
            r = ReactionPublicite.objects.create(publicite=p, membre=m, valeur=valeur, texte=texte)
            evenement = "PublicityCommented" if valeur == 0 else "PublicityLiked" if valeur == 1 else "PublicityDisliked"
    except IntegrityError:
        return None, t("engine/messagerie.py:g:880:22")
    journaliser(m, evenement, publicite_id=p.id, publicite=p.titre, agent_publicite_id=p.agent.utilisateur_id, valeur=valeur,
                texte_sha256=empreinte(texte), longueur=len(texte))
    return r, None


def resumes_reactions(ids, m=None):
    """{publicité: {likes, dislikes, commentaires, mon_avis}} en UNE requête (mon_avis : l'avis de `m`, 0 s'il n'en a pas)."""
    from django.db.models import Count
    res = {i: {"likes": 0, "dislikes": 0, "commentaires": 0, "mon_avis": 0} for i in ids}
    for r in ReactionPublicite.objects.filter(publicite_id__in=list(ids)).values("publicite_id", "valeur").annotate(nb=Count("id")).order_by():
        res[r["publicite_id"]][{1: "likes", -1: "dislikes", 0: "commentaires"}[r["valeur"]]] = r["nb"]
    if m is not None:
        for pid, v in ReactionPublicite.objects.filter(publicite_id__in=list(ids), membre=m).exclude(valeur=0).values_list("publicite_id", "valeur"):
            res[pid]["mon_avis"] = v
    return res


def commentaires_publicite(m, publicite_id):
    """Commentaires d'une publicité (None si elle n'est pas accessible) : l'équipe qui la gère voit tous les commentaires avec le
    nom du client ; un client ne voit que les siens (jamais ceux des autres clients)."""
    if m.role == "lecteur":
        p = _publicite_du_client(m, publicite_id)
    else:
        p = publicites_gerees(m).filter(id=publicite_id).first()
    if p is None:
        return None
    qs = ReactionPublicite.objects.filter(publicite=p, valeur=0).select_related("membre__enregistrement_client")
    liste = list((qs.filter(membre=m) if m.role == "lecteur" else qs).order_by("-id")[:100])
    cache = noms_clients({r.membre_id: r.membre for r in liste}.values())
    return [{"id": r.id, "client": "Vous" if m.role == "lecteur" else cache.get(r.membre_id, ""), "texte": r.texte,
             "date": r.cree_le.isoformat()} for r in liste]


def supprimer_publicite(p):
    if p.fichier:
        p.fichier.delete(save=False)
    p.delete()


# ── Fichiers servis par securite.servir_media ─────────────────────────────────
def fichier_accessible(user, rel):
    """True si `user` peut télécharger /media/<rel> (messagerie/… ou publicités/…) ; le super-administrateur : toujours."""
    m = MembreEntreprise.objects.filter(utilisateur=user).select_related("entreprise", "enregistrement_client").first()
    if m is None:
        return False
    if rel.startswith("messagerie/"):
        msg = Message.objects.filter(fichier=rel, supprime=False).select_related("conversation").first()
        return msg is not None and peut_acceder(m, msg.conversation)
    if rel.startswith("publicites/"):
        p = Publicite.objects.filter(fichier=rel).first()
        if p is None or p.entreprise_id != m.entreprise_id:
            return False
        if est_equipe(m):
            c = chef(m.entreprise)
            return m.id == p.agent_id or (c is not None and c.id == m.id) or (p.publiee and p.agent_id in diffuseurs(m.entreprise_id))
        return p.publiee and (agent_de(m) or m).id == p.agent_id
    return True
