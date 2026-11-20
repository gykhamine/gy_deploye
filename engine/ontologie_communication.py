"""Couche « communication » de l'ontologie : ce que l'entreprise dit à ses clients et ce que ses clients lui répondent.

Nœuds : publicite (avec ses j'aime / je n'aime pas / commentaires comptés), conversation (avec son nombre de messages).
Relations : publie (agent → publicité), reagit (client → publicité), participe (membre → discussion), echange (expéditeur →
destinataire d'une discussion à deux, poids = messages), influence (agent → client) et partage_publicite (client ↔ client).

Rien n'est inventé : « influence » et « partage_publicite » sont des volumes OBSERVÉS (réactions et messages réels), pas une
causalité. L'influence n'est affirmée qu'à partir de SEUIL_INFLUENCE interactions (un seul j'aime n'en est pas une) ; le texte
des commentaires et des messages n'entre jamais dans le graphe (seulement des nombres). Chaque spectateur ne voit que sa part :
le client ses propres réactions et discussions, l'agent ses publicités et ses discussions, l'administrateur tout.
"""
from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t, tl
import logging
from collections import defaultdict
from itertools import combinations

logger = logging.getLogger(__name__)

SEUIL_INFLUENCE = 2             # interactions minimales avant d'affirmer une influence
MAX_CLIENTS_PAR_PUBLICITE = 30  # borne des paires client ↔ client (435 au plus par publicité)
MAX_CONVERSATIONS = 200         # discussions les plus récentes seulement

TYPES_NOEUDS_COMMUNICATION = {
    "publicite":    ("#ff5d8f", tl("engine/ontologie_communication.py:g:24:33")),
    "conversation": ("#00b8a9", tl("engine/ontologie_communication.py:g:26:33")),
}
RELATIONS_COMMUNICATION = {
    "publie":            ("#c2185b", tl("engine/ontologie_communication.py:g:29:38")),
    "reagit":            ("#ff7a45", tl("engine/ontologie_communication.py:g:30:38")),
    "participe":         ("#d9480f", tl("engine/ontologie_communication.py:g:32:38")),
    "echange":           ("#7e22ce", tl("engine/ontologie_communication.py:g:34:38")),
    "influence":         ("#9f1239", tl("engine/ontologie_communication.py:g:35:38")),
    "partage_publicite": ("#6d28d9", tl("engine/ontologie_communication.py:g:39:38")),
}


def liens_communication(reactions, proprietaire_pub, echanges, seuil=SEUIL_INFLUENCE, max_par_pub=MAX_CLIENTS_PAR_PUBLICITE):
    """Calcul pur (sans base) des liens dérivés.

    reactions : {(client, pub): (likes, dislikes, commentaires)} ; proprietaire_pub : {pub: propriétaire} ;
    echanges : {(client, propriétaire): messages échangés dans leur discussion à deux}.
    Renvoie {"influence": {(propriétaire, client): (poids, score)}, "co_reaction": {(client_a, client_b): publicités communes}}.
    """
    poids, score = defaultdict(int), defaultdict(int)
    for (cl, pub), (j, d, c) in reactions.items():
        prop = proprietaire_pub.get(pub)
        if prop is not None:
            poids[(prop, cl)] += j + d + c
            score[(prop, cl)] += j - d
    for (cl, prop), nb in echanges.items():
        poids[(prop, cl)] += nb
    influence = {k: (p, score[k]) for k, p in poids.items() if p >= seuil}
    par_pub = defaultdict(set)
    for (cl, pub) in reactions:
        par_pub[pub].add(cl)
    co = defaultdict(int)
    for clients in par_pub.values():
        for a, b in combinations(sorted(clients)[:max_par_pub], 2):
            co[(a, b)] += 1
    return {"influence": influence, "co_reaction": dict(co)}


def enrichir_communication(g, entreprise, racine, prefixe_role, membre=None):
    """Ajoute au graphe la couche communication, dans la portée de `membre` (None = tout, comme l'administrateur).
    Chaque partie est indépendante et jamais bloquante ; ne s'accroche qu'aux nœuds personne déjà présents."""
    from django.db.models import Count
    from .messagerie import agent_de
    from .models import Conversation, Message, Participant, Publicite, ReactionPublicite

    role = getattr(membre, "role", None)
    nid = lambda m_id, m_role: f"{prefixe_role.get(m_role, 'agent')}:{m_id}"   # noqa: E731
    pubs = Publicite.objects.filter(entreprise=entreprise).select_related("agent")
    if role == "agent":
        pubs = pubs.filter(agent=membre)
    elif role == "lecteur":
        ag = agent_de(membre)
        pubs = pubs.filter(agent=ag, publiee=True) if ag is not None else pubs.none()
    pubs = list(pubs)
    prop_pub = {p.id: p.agent_id for p in pubs}
    role_prop = {p.agent_id: p.agent.role for p in pubs}
    reactions = {}

    def publicites():
        brut = defaultdict(lambda: [0, 0, 0])
        qs = ReactionPublicite.objects.filter(publicite_id__in=list(prop_pub)).values("publicite_id", "membre_id", "valeur") \
            .annotate(nb=Count("id")).order_by()
        total = defaultdict(lambda: [0, 0, 0])
        for r in qs:
            i = {1: 0, -1: 1, 0: 2}[r["valeur"]]
            total[r["publicite_id"]][i] += r["nb"]
            if role != "lecteur" or r["membre_id"] == membre.id:   # un client ne voit que ses propres liens
                brut[(r["membre_id"], r["publicite_id"])][i] += r["nb"]
        for p in pubs:
            j, d, c = total[p.id]
            g.add_node(f"publicite:{p.id}", type="publicite", label=p.titre, likes=j, dislikes=d, commentaires=c, publiee=p.publiee)
            if g.has_node(nid(p.agent_id, p.agent.role)):
                g.add_edge(nid(p.agent_id, p.agent.role), f"publicite:{p.id}", relation="publie")
        for (cl, pub), (j, d, c) in brut.items():
            reactions[(cl, pub)] = (j, d, c)
            if g.has_node(f"client:{cl}"):
                g.add_edge(f"client:{cl}", f"publicite:{pub}", relation="reagit", likes=j, dislikes=d, commentaires=c, poids=j + d + c)

    echanges = {}

    def discussions():
        convs = Conversation.objects.filter(entreprise=entreprise)
        if role in ("agent", "lecteur"):
            convs = convs.filter(participants__membre=membre)
        convs = list(convs.distinct()[:MAX_CONVERSATIONS])
        ids = [c.id for c in convs]
        parts = defaultdict(list)
        for p in Participant.objects.filter(conversation_id__in=ids).select_related("membre"):
            parts[p.conversation_id].append(p.membre)
        envoyes = {(r["conversation_id"], r["expediteur_id"]): r["nb"] for r in
                   Message.objects.filter(conversation_id__in=ids, supprime=False, expediteur__isnull=False)
                   .values("conversation_id", "expediteur_id").annotate(nb=Count("id")).order_by()}
        totaux = defaultdict(int)
        for (ci, _), nb in envoyes.items():
            totaux[ci] += nb
        cles_conv = {"direct": "engine/ontologie_communication.py:g:120:31", "groupe_entreprise": "engine/ontologie_communication.py:g:120:73", "groupe_clients": "engine/ontologie_communication.py:g:120:117", "salon": "engine/ontologie_communication.py:g:120:147"}
        libelles = {"direct": td("engine/ontologie_communication.py:g:120:31"), "groupe_entreprise": td("engine/ontologie_communication.py:g:120:73"), "groupe_clients": td("engine/ontologie_communication.py:g:120:117"), "salon": td("engine/ontologie_communication.py:g:120:147")}
        for c in convs:
            g.add_node(f"conversation:{c.id}", type="conversation", label=f"{libelles.get(c.type, 'Discussion')} #{c.id}",
                       nb_messages=totaux[c.id], sous_type=c.type,
                       **({"label_cle": cles_conv[c.type], "label_vars": {}, "label_suffixe": f" #{c.id}"} if c.type in cles_conv else {}))
            for m in parts[c.id]:
                if g.has_node(nid(m.id, m.role)):
                    g.add_edge(nid(m.id, m.role), f"conversation:{c.id}", relation="participe", poids=envoyes.get((c.id, m.id), 0))
            deux = parts[c.id]
            if c.type == "direct" and len(deux) == 2:
                for a, b in (deux, deux[::-1]):
                    nb = envoyes.get((c.id, a.id), 0)
                    if nb and g.has_node(nid(a.id, a.role)) and g.has_node(nid(b.id, b.role)):
                        g.add_edge(nid(a.id, a.role), nid(b.id, b.role), relation="echange", poids=nb)
                    cl, eq = (a, b) if a.role == "lecteur" else (b, a)
                    if cl.role == "lecteur" and eq.role != "lecteur":
                        echanges[(cl.id, eq.id)] = echanges.get((cl.id, eq.id), 0) + nb
                        role_prop.setdefault(eq.id, eq.role)

    def derives():
        liens = liens_communication(reactions, prop_pub, echanges)
        for (prop, cl), (poids, sc) in liens["influence"].items():
            u, v = nid(prop, role_prop.get(prop, "agent")), f"client:{cl}"
            if g.has_node(u) and g.has_node(v) and u != v:
                # Un graphe orienté ne porte qu'UNE arête par couple : si l'agent a écrit au client (echange), l'influence reprend cette
                # arête en gardant le nombre de messages (attribut messages) ; toute autre relation déjà présente n'est jamais écrasée.
                anc = g.get_edge_data(u, v)
                if anc is None:
                    g.add_edge(u, v, relation="influence", poids=poids, score=sc)
                elif anc.get("relation") == "echange":
                    g.add_edge(u, v, relation="influence", poids=poids, score=sc, messages=anc.get("poids", 0))
        if role != "lecteur":
            for (a, b), nb in liens["co_reaction"].items():
                if g.has_node(f"client:{a}") and g.has_node(f"client:{b}"):
                    g.add_edge(f"client:{a}", f"client:{b}", relation="partage_publicite", poids=nb)

    for nom, fn in ((_gt("engine/ontologie_communication.py:156:22"), publicites), ("discussions", discussions), (t("engine/ontologie_communication.py:g:161:80"), derives)):
        try:
            fn()
        except Exception:
            logger.exception(t("engine/ontologie_communication.py:g:161:30"), nom)


__all__ = ["TYPES_NOEUDS_COMMUNICATION", "RELATIONS_COMMUNICATION", "liens_communication", "enrichir_communication"]
