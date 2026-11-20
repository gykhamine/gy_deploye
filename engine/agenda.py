"""Agenda : tâches datées, pour soi et pour ses clients, avec rappels — c'est le destinataire qui valide.

Qui planifie pour qui : un agent (ou l'administrateur) pour LUI-MÊME et pour SES clients (messagerie.clients_de, la même
frontière que la messagerie, les publicités et le wallet) ; un client seulement pour lui-même.

Deux rappels distincts, chacun envoyé UNE fois par tâche, par le chemin unique des notifications (cloche, relais SMS) :
  1. À L'HEURE — dès que l'échéance arrive (`agenda_rappel`). Émis par n'importe quel passage (commande `rappels_agenda`, ou la
     simple ouverture de la cloche / de l'agenda par quelqu'un de l'entreprise) : le relais SMS part donc même si personne
     du destinataire n'est connecté.
  2. EN RETARD — quand le destinataire se connecte alors que l'échéance est dépassée (au-delà de MARGE) et qu'il n'a pas répondu
     (`agenda_retard`). Il REMPLACE le rappel à l'heure resté sans lecture (un seul message à la reconnexion, pas deux) ; si le
     destinataire avait déjà lu le rappel à l'heure, il n'est pas harcelé : la tâche reste simplement affichée « en retard ».
Quand le destinataire répond (fait / pas fait), ses notifications de la tâche sont lues et, si c'est son agent qui l'avait planifiée,
celui-ci reçoit la réponse (`agenda_retour`).

`rappel_le` / `retard_le` sont posés par un UPDATE conditionnel AVANT l'envoi : deux workers ne notifient jamais deux fois, et une
panne d'envoi ne provoque jamais de doublon au passage suivant (même principe que notifications.synchroniser_recommandations).
"""
import datetime

from django.db.models import Q
from django.utils import timezone

from engine.i18n import t, tf

from . import messagerie as mg
from . import notifications
from .models import ConfigurationIA, Notification, TacheAgenda

MARGE = datetime.timedelta(minutes=10)   # au-delà, l'échéance est « dépassée » : rappel de retard, plus rappel à l'heure
LIMITE_A_FAIRE = 300
LIMITE_CLOSES = 50


# ── Qui peut planifier pour qui ───────────────────────────────────────────────
def cibles(m):
    """Membres pour qui `m` peut planifier : lui-même, plus ses clients s'il fait partie de l'équipe."""
    return [m] + (list(mg.clients_de(m)) if mg.est_equipe(m) else [])


def taches_de(m):
    """Tâches visibles de `m` : celles qu'il doit faire et celles qu'il a planifiées."""
    return TacheAgenda.objects.filter(Q(destinataire=m) | Q(createur=m), entreprise_id=m.entreprise_id) \
        .select_related("destinataire__utilisateur", "createur__utilisateur")


def peut_modifier(m, tache):
    """Le créateur modifie/supprime (l'administrateur aussi) ; le destinataire seul répond."""
    return tache.createur_id == m.id or m.role == "admin"


# ── Rappels ───────────────────────────────────────────────────────────────────
def _quand(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M")


def _reclamer(tache, champ, maintenant):
    """Pose `champ` s'il est encore vide : True pour UN SEUL appelant, qui seul enverra le rappel."""
    return TacheAgenda.objects.filter(pk=tache.pk, statut="a_faire", **{champ + "__isnull": True}).update(**{champ: maintenant}) == 1


def _annoncer(tache, type_notification, titre, message, cfg):
    de = "" if tache.createur_id == tache.destinataire_id else tf("engine/agenda.py:63:67", v1=mg.noms([tache.createur])[tache.createur_id])
    detail = f" {tache.description.strip()[:160]}" if tache.description.strip() else ""
    return notifications.notifier_membre(tache.destinataire, type_notification, titre, message + de + detail, cfg=cfg, agenda=tache)


def emettre(entreprise, membre=None, maintenant=None):
    """Émet les rappels dus. Sans `membre` (commande planifiée) : rappels à l'heure seulement. Avec `membre` (sa connexion : cloche,
    page agenda) : en plus, rappels de retard de SES tâches. Retourne le nombre de notifications créées."""
    maintenant = maintenant or timezone.now()
    cfg = ConfigurationIA.objects.filter(entreprise=entreprise).first()
    echues = TacheAgenda.objects.filter(entreprise=entreprise, statut="a_faire", echeance__lte=maintenant) \
        .select_related("destinataire__utilisateur", "createur__utilisateur")
    n = 0
    for tache in echues.filter(rappel_le__isnull=True, echeance__gt=maintenant - MARGE):
        if _reclamer(tache, "rappel_le", maintenant):
            _annoncer(tache, "agenda_rappel", tf("engine/agenda.py:78:50", v1=tache.titre),
                      tf("engine/agenda.py:79:26", v1=_quand(tache.echeance)), cfg)
            n += 1
    if membre is not None:
        for tache in echues.filter(destinataire=membre, retard_le__isnull=True, echeance__lte=maintenant - MARGE):
            if not _reclamer(tache, "retard_le", maintenant):
                continue
            rappels = tache.notifications.filter(type_notification="agenda_rappel")
            if rappels.filter(lue=True).exists():
                continue            # il a déjà vu le rappel : pas de second message, la tâche reste « en retard » dans l'agenda
            rappels.update(lue=True, lue_le=maintenant)
            _annoncer(tache, "agenda_retard", tf("engine/agenda.py:89:50", v1=tache.titre),
                      tf("engine/agenda.py:90:26", v1=_quand(tache.echeance)), cfg)
            n += 1
    return n


def valider(m, tache, fait):
    """Réponse du destinataire (et de lui seul). Retourne la tâche, ou None si `m` n'est pas le destinataire / déjà répondu."""
    if tache.destinataire_id != m.id or tache.statut != "a_faire":
        return None
    maintenant = timezone.now()
    tache.statut, tache.valide_le = ("fait" if fait else "non_fait"), maintenant
    tache.save(update_fields=["statut", "valide_le"])
    tache.notifications.filter(lue=False).update(lue=True, lue_le=maintenant)
    if tache.createur_id != m.id:       # son agent apprend la réponse
        agent = tache.createur
        qui = mg.noms([m])[m.id]
        notifications.notifier_membre(
            agent, "agenda_retour",
            tf("engine/agenda.py:108:16", v1=tache.titre, v2=t("engine/agenda.py:108:63") if fait else t("engine/agenda.py:108:91")),
            tf("engine/agenda.py:109:16",
               v1=qui, v2=_quand(tache.echeance), v3=t("engine/agenda.py:110:56") if fait else t("engine/agenda.py:110:85")),
            agenda=tache)
    return tache


def reprogrammer(tache, echeance):
    """Nouvelle échéance : la tâche redevient « à faire » et ses rappels sont réarmés (ils repartiront à la nouvelle date)."""
    tache.echeance, tache.statut, tache.valide_le, tache.rappel_le, tache.retard_le = echeance, "a_faire", None, None, None
    tache.save(update_fields=["echeance", "statut", "valide_le", "rappel_le", "retard_le"])
    tache.notifications.filter(lue=False).update(lue=True, lue_le=timezone.now())


# ── Présentation ──────────────────────────────────────────────────────────────
def serialiser(tache, m, noms, maintenant):
    return {
        "id": tache.id, "titre": tache.titre, "description": tache.description, "echeance": tache.echeance.isoformat(),
        "statut": tache.statut, "en_retard": tache.statut == "a_faire" and tache.echeance <= maintenant,
        "valide_le": tache.valide_le.isoformat() if tache.valide_le else None,
        "destinataire": {"id": tache.destinataire_id, "nom": noms[tache.destinataire_id]},
        "createur": {"id": tache.createur_id, "nom": noms[tache.createur_id]},
        "pour_moi": tache.destinataire_id == m.id, "modifiable": peut_modifier(m, tache),
    }


def lister(m):
    """Tâches de `m` : toutes celles à faire (les plus proches d'abord) puis les dernières closes, avec les noms en UNE requête."""
    maintenant = timezone.now()
    base = taches_de(m)
    liste = list(base.filter(statut="a_faire")[:LIMITE_A_FAIRE]) + list(base.exclude(statut="a_faire").order_by("-valide_le", "-id")[:LIMITE_CLOSES])
    noms = mg.noms({x.destinataire for x in liste} | {x.createur for x in liste})
    return [serialiser(x, m, noms, maintenant) for x in liste]


def alertes(m):
    """Notifications d'agenda non lues de `m` (équipe comme client) : sert l'annonce à la reconnexion et la bannière du client."""
    qs = Notification.objects.filter(destinataire=m, type_notification__startswith="agenda_", lue=False).order_by("-cree_le")
    return [{"id": n.id, "type": n.type_notification, "titre": n.titre, "message": n.message, "agenda_id": n.agenda_id,
             "date": n.cree_le.isoformat()} for n in qs[:30]], qs.count()


def marquer_alertes_lues(m):
    return Notification.objects.filter(destinataire=m, type_notification__startswith="agenda_", lue=False).update(lue=True, lue_le=timezone.now())
