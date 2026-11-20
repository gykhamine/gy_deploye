"""Création des notifications internes (agents/admin).

Déclenchée pour l'instant uniquement par la soumission d'un formulaire client
(voir views.api_lignes, juste après l'écriture du bloc blockchain) : chaque
agent/admin de l'entreprise reçoit sa PROPRE Notification.
"""
from engine.i18n import t, tf, devise_affichage
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
import logging

from .models import ConfigurationIA, MembreEntreprise, Notification, RecoNotifiee
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)
MAX_INDIVIDUELLES = 3   # au-delà, une seule notification de synthèse (jamais d'avalanche)


def notifier_nouvelle_ligne_client(table, enregistrement, client_membre=None):
    """Crée une Notification pour chaque agent/admin de l'entreprise de
    `table` lorsqu'un client (rôle lecteur) vient de soumettre une ligne sur
    cette table (formulaire exposé, voir TableDynamique.accessible_client).

    `client_membre` : le MembreEntreprise du client à l'origine de la
    soumission, si connu — utilisé uniquement pour personnaliser le message.

    Retourne la liste des Notification créées (peut être vide si l'entreprise
    n'a encore aucun agent/admin)."""
    qui = t("engine/notifications.py:g:33:11")
    if client_membre is not None and client_membre.utilisateur_id:
        nom_client = client_membre.utilisateur.get_full_name() or client_membre.utilisateur.get_username()
        if nom_client:
            qui = nom_client
    return _creer_notifications(
        table.entreprise, "nouvelle_ligne_client",
        titre=_gtf("engine/notifications.py:43:15", v1=(table.nom)),
        message=tf("engine/notifications.py:g:41:17", v1=qui, v2=table.nom),
        table=table, enregistrement=enregistrement,
    )




def notifier_sortie_especes(entreprise, libelle, montant):
    """Notifie l'équipe d'une sortie d'espèces (charge payée, retrait client en espèces)."""
    return _creer_notifications(entreprise, "sortie_especes", titre=_gtf("rg6.msg.062", v1=montant), message=f"{libelle} ({montant:g} {devise_affichage()}).")


def notifier_kpi_alerte(kpi):
    """Crée une Notification pour chaque agent/admin de l'entreprise quand un
    IndicateurKPI passe en "alerte" ou "danger" (voir kpi.calculer_kpi —
    déclenché une seule fois par entrée dans l'état, pas à chaque recalcul).
    Même mécanisme (et même relais SMS optionnel) que les autres
    notifications, aucune deuxième implémentation."""
    etat = kpi.etat
    libelle = "danger" if etat == "danger" else "alerte"
    titre = tf("engine/notifications.py:g:69:13", v1=libelle, v2=kpi.nom)
    valeur = f"{kpi.valeur_actuelle:g}{kpi.unite}" if kpi.valeur_actuelle is not None else "?"
    cible = f"{kpi.valeur_cible:g}{kpi.unite}" if kpi.valeur_cible is not None else None
    message = tf("engine/notifications.py:g:72:15", v1=kpi.nom, v2=valeur) + (f" (objectif {cible})" if cible else "") + "."
    return _creer_notifications(kpi.entreprise, "kpi_alerte", titre=titre, message=message, table=kpi.table_source)


def notifier_seuil_lignes(table, nb_lignes):
    """Alerte (notification interne, i18n) quand une table atteint le seuil d'alerte (9 000) puis le plafond absolu (10 000) de lignes.
    Même mécanisme que les autres notifications (_creer_notifications), aucune deuxième implémentation."""
    cle = "seuil_lignes_plafond" if nb_lignes >= table.PLAFOND_ABSOLU_LIGNES else "seuil_lignes_alerte"
    return _creer_notifications(table.entreprise, "kpi_alerte", titre=tf(f"engine/notifications.py:{cle}:titre", v1=table.nom),
                                message=tf(f"engine/notifications.py:{cle}:message", v1=table.nom, v2=nb_lignes, v3=table.PLAFOND_ABSOLU_LIGNES),
                                table=table)


def notifier_kpi_objectif_atteint(kpi):
    """Crée une Notification pour chaque agent/admin de l'entreprise quand un IndicateurKPI atteint son objectif
    (état « succes », voir kpi._notifier_si_entree_en_alerte — une seule fois par atteinte, pas à chaque recalcul).
    Même mécanisme (et même relais SMS optionnel) que les autres notifications, aucune deuxième implémentation."""
    valeur = f"{kpi.valeur_actuelle:g}{kpi.unite}" if kpi.valeur_actuelle is not None else "?"
    cible = f"{kpi.valeur_cible:g}{kpi.unite}" if kpi.valeur_cible is not None else None
    message = tf("engine/notifications.py:g:82:15", v1=kpi.nom, v2=valeur) + (f" (objectif {cible})" if cible else "") + "."
    return _creer_notifications(kpi.entreprise, "kpi_objectif", titre=f"Objectif atteint — {kpi.nom}", message=message, table=kpi.table_source)


def notifier_tache_terminee(tache, succes, erreur="", resume=""):
    """Crée une Notification pour chaque agent/admin de l'entreprise quand
    une TachePlanifiee (voir engine/agent.py::executer_tache) vient de finir
    de s'exécuter — succès ou échec, dans les deux cas, avec l'explication
    de l'échec le cas échéant. Même mécanisme (et même relais SMS optionnel)
    que les autres notifications, aucune deuxième implémentation."""
    nom = tache.nom or tf("engine/notifications.py:g:92:24", v1=tache.id)
    if succes:
        type_notification = "tache_reussie"
        titre = tf("engine/notifications.py:g:95:17", v1=nom)
        message = tf("engine/notifications.py:g:96:19", v1=nom)
        if resume:
            message += f" {resume}"
    else:
        type_notification = "tache_echec"
        titre = tf("engine/notifications.py:g:101:17", v1=nom)
        message = tf("engine/notifications.py:g:102:19", v1=nom)
        if erreur:
            message += f" Raison : {erreur}"
    return _creer_notifications(tache.entreprise, type_notification, titre=titre, message=message)


def notifier_alertes_geo(table, alertes: list[dict]):
    """Notifie les alertes géospatiales (rapprochement / collision prévue) déjà filtrées et dédoublonnées par geo_surveillance :
    une notification chacune (jusqu'à MAX_INDIVIDUELLES), sinon une seule de synthèse. Même chemin (et même relais SMS optionnel)
    que les autres notifications, aucune deuxième implémentation. `alertes` : [{"type", "titre", "message"}]."""
    if not alertes:
        return []
    creees = []
    for a in alertes[:MAX_INDIVIDUELLES]:
        creees += _creer_notifications(table.entreprise, a["type"], titre=a["titre"], message=a["message"], table=table)
    reste = alertes[MAX_INDIVIDUELLES:]
    if reste:
        creees += _creer_notifications(
            table.entreprise, "geo_collision" if any(a["type"] == "geo_collision" for a in reste) else "geo_rencontre",
            titre=tf("engine/notifications.py:g:121:19", v1=table.nom, v2=len(reste)),
            message=t("engine/notifications.py:g:122:21"), table=table)
    return creees


def notifier_recommandations_urgentes(entreprise, recos: list[dict]):
    """Notifie les recommandations « à lancer » qui viennent d'apparaître : une notification chacune (jusqu'à
    MAX_INDIVIDUELLES), sinon une seule de synthèse. Même chemin (et même relais SMS optionnel) que les autres."""
    if not recos:
        return []
    if len(recos) > MAX_INDIVIDUELLES:
        titres = " ; ".join(r.get("titre", "") for r in recos[:MAX_INDIVIDUELLES])
        return _creer_notifications(
            entreprise, "recommandation", titre=tf("engine/notifications.py:g:134:49", v1=len(recos)),
            message=tf("engine/notifications.py:g:135:21", v1=titres))
    creees = []
    for r in recos:
        cible, dec = r.get("cible") or {}, r.get("decision") or {}
        message = f"{r.get('message') or r.get('titre', '')}".strip()
        if dec.get("risque") is not None:
            message += f" (risque {dec['risque']:g}/100)"
        if cible.get("nom"):
            message = f"{cible['nom']} — {message}"
        creees += _creer_notifications(entreprise, "recommandation", titre=tf("engine/notifications.py:g:144:76", v1=r.get('titre', 'Recommandation')), message=message)
    return creees


def synchroniser_recommandations(entreprise, etat, graphe, resultat_reco):
    """Appelé à chaque passage d'entretien (engine/memoire_decision.entretenir) : notifie les recommandations passées à
    « agir » depuis le dernier passage, réarme celles qui ne le sont plus. Mémoire = RecoNotifiee (voir decision.changements_alertes).
    Jamais bloquant. Retourne le nombre de recommandations notifiées."""
    from . import decision
    try:
        pilote = decision.piloter(etat, graphe, resultat_reco)
        urgentes = decision.urgentes(pilote["recommandations"])
        lignes = {ligne.cle: ligne for ligne in RecoNotifiee.objects.filter(entreprise=entreprise)}
        nouvelles, a_rearmer = decision.changements_alertes(urgentes, [c for c, ligne in lignes.items() if ligne.actif])
        if a_rearmer:
            RecoNotifiee.objects.filter(entreprise=entreprise, cle__in=a_rearmer).update(actif=False)
        # marquer AVANT d'envoyer : une panne d'envoi ne doit jamais provoquer de doublon au passage suivant
        for cle in nouvelles:
            if cle in lignes:
                RecoNotifiee.objects.filter(entreprise=entreprise, cle=cle).update(actif=True)
            else:
                RecoNotifiee.objects.create(entreprise=entreprise, cle=cle, actif=True)
        notifier_recommandations_urgentes(entreprise, [urgentes[c] for c in nouvelles])
        return len(nouvelles)
    except Exception:
        logger.exception(t("engine/notifications.py:g:170:26"), getattr(entreprise, "id", None))
        return 0


def notifier_decision_enregistree(entreprise, option, utilisateur=None):
    """Une décision stratégique vient d'être tranchée : ses conséquences seront mesurées automatiquement."""
    qui = ""
    if utilisateur is not None:
        qui = (utilisateur.get_full_name() or utilisateur.get_username() or "").strip()
    return _creer_notifications(
        entreprise, "decision_strategique", titre=tf("engine/notifications.py:g:179:51", v1=option),
        message=(f"{qui} a choisi" if qui else t("engine/notifications.py:g:180:48")) + tf("engine/notifications.py:g:180:70", v1=option))


def notifier_decision_mesuree(entreprise, option, horizon_jours, risque_avant, risque_apres):
    """Les conséquences d'une décision viennent d'être mesurées (horizon écoulé) : risque avant / après sur ses objets."""
    def _pct(x):
        return "?" if x is None else f"{round(float(x) * 100)} %"
    if risque_avant is not None and risque_apres is not None:
        verdict = t("engine/notifications.py:g:188:19") if risque_apres < risque_avant else (t("engine/notifications.py:g:188:68") if risque_apres > risque_avant else "stable")
    else:
        verdict = t("engine/notifications.py:g:190:19")
    return _creer_notifications(
        entreprise, "decision_strategique", titre=tf("engine/notifications.py:g:192:51", v1=horizon_jours, v2=option),
        message=tf("engine/notifications.py:g:193:17", v1=_pct(risque_avant), v2=_pct(risque_apres), v3=verdict))


def _creer_notifications(entreprise, type_notification, titre, message, table=None, enregistrement=None):
    """Dépendance partagée : crée UNE Notification par agent/admin de
    `entreprise` (jamais partagée) + relais SMS meilleur-effort optionnel
    (voir ConfigurationIA.notifications_sms_actif) — factorisée pour que
    tout déclencheur (nouvelle ligne, dépôt vérifié...) réutilise le même
    chemin plutôt que de dupliquer la boucle destinataires/SMS."""
    cfg = ConfigurationIA.objects.filter(entreprise=entreprise).first()
    destinataires = MembreEntreprise.objects.filter(entreprise=entreprise, role__in=("agent", "admin"))
    return [notifier_membre(membre, type_notification, titre, message, cfg=cfg, table=table, enregistrement=enregistrement)
            for membre in destinataires]


def lien_notification(notif):
    """Page à ouvrir quand on clique sur la notification (None : aucune). Source unique : la liste (API) et la poussée temps réel."""
    t = notif.type_notification
    return ("/messagerie/" if t == "message_recu"
            else "/agenda/" if t.startswith("agenda_")
            else "/recommandations/" if t in ("recommandation", "decision_strategique")
            else f"/tables/{notif.table_id}/dashboard-bi/" if t in ("kpi_alerte", "kpi_objectif") and notif.table_id
            else f"/tables/{notif.table_id}/geolocalisation/" if t in ("geo_rencontre", "geo_collision") and notif.table_id
            else None)


def pousser_notification(notif):
    """Poussée temps réel (WebSocket /ws/notifications/) d'une Notification déjà créée : le navigateur de l'utilisateur
    l'affiche aussitôt comme notification du navigateur. Meilleur effort : un échec n'empêche jamais la création."""
    try:
        from .ws_notify import notifier_utilisateur
        user_id = notif.destinataire.utilisateur_id
        if user_id:
            notifier_utilisateur(user_id, message=notif.message or notif.titre or "-", id=notif.id, titre=notif.titre,
                                 lien=lien_notification(notif), type_notification=notif.type_notification)
    except Exception:   # noqa: BLE001 — couche temps réel absente ou arrêtée : la cloche (interrogation) prend le relais
        logger.debug(_gt("rg5.msg.059"), getattr(notif, "pk", None), exc_info=True)


def notifier_membre(membre, type_notification, titre, message, cfg=None, **liens):
    """UNE Notification pour UN membre — agent, admin OU client (rôle lecteur) — + relais SMS meilleur-effort s'il est activé
    et que le membre a un numéro. `liens` : table=, enregistrement=, conversation=, agenda=. Chemin unique de création :
    `_creer_notifications` (équipe) et l'agenda (engine/agenda.py, équipe et clients) passent tous deux par ici."""
    cfg = cfg or ConfigurationIA.objects.filter(entreprise_id=membre.entreprise_id).first()
    notif = Notification.objects.create(
        entreprise_id=membre.entreprise_id, destinataire=membre, type_notification=type_notification,
        titre=titre, message=message, **liens)
    pousser_notification(notif)
    return notif


