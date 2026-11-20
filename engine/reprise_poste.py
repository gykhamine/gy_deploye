"""Retrait d'un agent sans perte de son travail.

Règle métier : quand l'administrateur retire un agent (page « Entreprise et employés »), seul l'ACCÈS de l'agent disparaît
(identifiants rendus inutilisables). Tout ce qu'il avait construit — clients, lignes de tables, publicités, chatbots, tâches,
skills, KPI, solde de wallet — est repris par l'administrateur, le temps de créer un remplaçant. L'administrateur cède ensuite
le poste au remplaçant EN UN CLIC (`ceder_poste`) : tout lui est rendu comme si rien n'avait changé.

Les identifiants ne sont jamais choisis par l'agent : le mot de passe est toujours donné par l'administrateur.
"""
from engine.i18n import t
from .delegation import a_pouvoir as _a_pouvoir
from django.db import transaction
from django.db.models import F
from django.utils import timezone


class ReprisePosteErreur(Exception):
    pass


def _nom_complet(u):
    return f"{u.first_name} {u.last_name}".strip() or u.username


@transaction.atomic
def retirer_agent(entreprise, admin_membre, user_id):
    """Retire l'agent `user_id` : crée une ReprisePoste, bascule ses ressources sur l'administrateur, révoque son accès.
    Retourne la ReprisePoste, ou None si le membre n'existe pas. Pour un membre qui n'est pas un agent (client, administrateur),
    la suppression habituelle de sa ligne de membre est conservée (retourne True)."""
    from .models import (ChatbotAgent, Conversation, CleAPI, Enregistrement, IndicateurKPI, Message, MembreEntreprise,
                         Publicite, ReprisePoste, Skill, TachePlanifiee, Wallet)
    cible = MembreEntreprise.objects.select_related("utilisateur").filter(entreprise=entreprise, utilisateur_id=user_id).first()
    if cible is None:
        return None
    if not _a_pouvoir(admin_membre, "reprises_poste") or admin_membre.entreprise_id != entreprise.id:
        raise ReprisePosteErreur(t("engine/reprise_poste.py:g:34:34"))
    if cible.id == admin_membre.id:
        raise ReprisePosteErreur(t("engine/reprise_poste.py:g:36:34"))
    if cible.role != "agent":
        cible.delete()
        return True

    ancien = cible.utilisateur
    admin_user = admin_membre.utilisateur
    reprise = ReprisePoste.objects.create(
        entreprise=entreprise, admin=admin_user, nom_agent=_nom_complet(ancien), identifiant_ancien=ancien.username,
        telephone_ancien=cible.telephone or "",
    )
    elements = {}

    # Lignes de tables (clients, données) : l'agent d'un client = Enregistrement.cree_par → l'administrateur.
    reprise.nb_lignes = Enregistrement.objects.filter(table__entreprise=entreprise, cree_par=ancien).update(
        cree_par=admin_user, reprise=reprise)

    # Publicités.
    ids = list(Publicite.objects.filter(agent=cible).values_list("id", flat=True))
    Publicite.objects.filter(id__in=ids).update(agent=admin_membre)
    elements["publicites"] = ids

    # Chatbots : repris seulement si l'administrateur n'a pas déjà le sien pour cette portée (contrainte d'unicité).
    ids = []
    for c in ChatbotAgent.objects.filter(agent=cible):
        if not ChatbotAgent.objects.filter(agent=admin_membre, portee=c.portee).exists():
            ChatbotAgent.objects.filter(pk=c.pk).update(agent=admin_membre)
            ids.append(c.pk)
    elements["chatbots"] = ids

    # Espace de discussion des clients de l'agent : repris tel quel, ou fusionné dans celui de l'administrateur s'il existe.
    ids = []
    for conv in Conversation.objects.filter(agent=cible, type="groupe_clients"):
        existante = Conversation.objects.filter(agent=admin_membre, type="groupe_clients").first()
        if existante is None:
            Conversation.objects.filter(pk=conv.pk).update(agent=admin_membre)
            ids.append(conv.pk)
        else:
            Message.objects.filter(conversation=conv).update(conversation=existante)
            conv.delete()
    elements["conversations"] = ids

    # Tâches planifiées, skills, KPI créés par l'agent.
    ids = list(TachePlanifiee.objects.filter(utilisateur=ancien).values_list("id", flat=True))
    TachePlanifiee.objects.filter(id__in=ids).update(utilisateur=admin_user)
    elements["taches"] = ids
    ids = list(Skill.objects.filter(cree_par=ancien).values_list("id", flat=True))
    Skill.objects.filter(id__in=ids).update(cree_par=admin_user)
    elements["skills"] = ids
    ids = list(IndicateurKPI.objects.filter(cree_par=ancien).values_list("id", flat=True))
    IndicateurKPI.objects.filter(id__in=ids).update(cree_par=admin_user)
    elements["kpis"] = ids

    # Solde du wallet de l'agent → caisse (wallet de l'administrateur) ; non restitué lors de la cession.
    w = Wallet.objects.filter(membre=cible).first()
    if w is not None and w.solde:
        reprise.solde_verse = w.solde
        wa, _ = Wallet.objects.get_or_create(membre=admin_membre)
        Wallet.objects.filter(pk=wa.pk).update(solde=F("solde") + w.solde)

    reprise.elements = elements
    reprise.save()

    # Clés API : elles incarnent le membre → révoquées avec lui (cascade). Le membre disparaît, puis l'accès est neutralisé.
    CleAPI.objects.filter(membre=cible).update(actif=False)
    cible.delete()
    _revoquer_acces(ancien)
    return reprise


def _revoquer_acces(user):
    """L'utilisateur ne peut plus se connecter et son identifiant redevient disponible (le remplaçant peut le reprendre)."""
    ancien = user.username
    user.set_unusable_password()
    user.is_active = False
    user.username = f"retire_{user.pk}_{ancien}"[:150]
    user.email = ""
    user.save()


@transaction.atomic
def ceder_poste(reprise, nouvel_agent_user_id, admin_membre):
    """Cède en un clic tout ce que l'administrateur a repris au nouvel agent. Retourne le nombre de lignes cédées."""
    from .models import (ChatbotAgent, Conversation, IndicateurKPI, MembreEntreprise, Publicite, Skill, TachePlanifiee)
    if not _a_pouvoir(admin_membre, "reprises_poste") or admin_membre.entreprise_id != reprise.entreprise_id:
        raise ReprisePosteErreur(t("engine/reprise_poste.py:g:121:34"))
    if reprise.statut == "cede":
        raise ReprisePosteErreur(t("engine/reprise_poste.py:g:123:34"))
    nouveau = MembreEntreprise.objects.select_related("utilisateur").filter(
        entreprise_id=reprise.entreprise_id, utilisateur_id=nouvel_agent_user_id, role="agent").first()
    if nouveau is None:
        raise ReprisePosteErreur(t("engine/reprise_poste.py:g:127:34"))

    from .models import Enregistrement
    n = Enregistrement.objects.filter(reprise=reprise, cree_par=admin_membre.utilisateur).update(
        cree_par=nouveau.utilisateur, reprise=None)
    # Lignes que l'administrateur a pu modifier entre-temps : elles restent à lui, mais le marquage est levé.
    Enregistrement.objects.filter(reprise=reprise).update(reprise=None)

    el = reprise.elements or {}
    admin_user = admin_membre.utilisateur
    Publicite.objects.filter(id__in=el.get("publicites", []), agent=admin_membre).update(agent=nouveau)
    for pk in el.get("chatbots", []):
        c = ChatbotAgent.objects.filter(pk=pk, agent=admin_membre).first()
        if c and not ChatbotAgent.objects.filter(agent=nouveau, portee=c.portee).exists():
            ChatbotAgent.objects.filter(pk=pk).update(agent=nouveau)
    for pk in el.get("conversations", []):
        if not Conversation.objects.filter(agent=nouveau, type="groupe_clients").exists():
            Conversation.objects.filter(pk=pk, agent=admin_membre, type="groupe_clients").update(agent=nouveau)
    TachePlanifiee.objects.filter(id__in=el.get("taches", []), utilisateur=admin_user).update(utilisateur=nouveau.utilisateur)
    Skill.objects.filter(id__in=el.get("skills", []), cree_par=admin_user).update(cree_par=nouveau.utilisateur)
    IndicateurKPI.objects.filter(id__in=el.get("kpis", []), cree_par=admin_user).update(cree_par=nouveau.utilisateur)

    reprise.statut = "cede"
    reprise.cede_a = nouveau.utilisateur
    reprise.cede_le = timezone.now()
    reprise.save()
    return n
