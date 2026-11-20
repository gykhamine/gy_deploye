"""Système d'événements — vocabulaire commun aux transactions déjà
journalisées dans la chaîne d'entreprise (voir models.Bloc).

Aucune nouvelle infrastructure : Bloc EST déjà le registre d'événements
(chaque Bloc.ajouter_transaction()/ajouter_transactions() en écrit un ou
plusieurs), ontologie.construire_graphe EST déjà la couche Ontology (elle lit
bloc.transactions pour construire le graphe, voir la boucle "blocs_entreprise"),
et declencheurs.evaluer_conditions(entreprise_id, "blockchain", ...) EST déjà
la couche Applications : le signal post_save(Bloc) (voir apps.
declencheur_tache_blockchain) réévalue automatiquement, pour CHAQUE nouvelle
transaction quel que soit son type, les TachePlanifiee conditionnelles dont
condition_categorie="blockchain" — qui relancent un script via agent.
executer_tache (couche Agent). La chaîne
Modification → Event → Ontology → Applications → Agent existe donc déjà de
bout en bout ; ce module ne fait qu'unifier le NOM des événements encore
journalisés sous des libellés français ad hoc ("creation_ligne",
"action_executee"...) éparpillés dans views.py/apps.py/actions.py, et couvre
les quelques types annoncés qui n'étaient pas encore journalisés du tout.

EVENT_TYPES : vocabulaire indicatif (non exhaustif — tout type_evenement
libre reste utilisable, exactement comme avant), avec le point du projet qui
l'émet déjà ou l'émet via emettre() ci-dessous.
"""
from engine.i18n import t
from engine.i18n import tl
EVENT_TYPES = {
    "ObjectCreated":        tl("engine/evenements.py:g:25:29"),
    "ObjectUpdated":        tl("engine/evenements.py:g:26:29"),
    "ObjectDeleted":        tl("engine/evenements.py:g:27:29"),
    "LinkCreated":          tl("engine/evenements.py:g:28:29"),
    "ActionExecuted":       tl("engine/evenements.py:g:29:29"),
    "CalculationCompleted": tl("engine/evenements.py:g:30:29"),
    "DocumentGenerated":    tl("engine/evenements.py:g:31:29"),
    "DatasetImported":      tl("engine/evenements.py:g:32:29"),
    "AgentExecuted":        tl("engine/evenements.py:g:33:29"),
    # Communication (messagerie.journaliser) : le texte n'entre jamais dans la chaîne, seulement texte_sha256 ; cibles publicite_id / conversation_id.
    "PublicityPublished":   tl("engine/evenements.py:g:35:29"),
    "PublicityUpdated":     tl("engine/evenements.py:g:36:29"),
    "PublicityDeleted":     tl("engine/evenements.py:g:37:29"),
    "PublicityLiked":       tl("engine/evenements.py:g:38:29"),
    "PublicityDisliked":    tl("engine/evenements.py:g:39:29"),
    "PublicityReactionRemoved": tl("engine/evenements.py:g:40:33"),
    "PublicityCommented":   tl("engine/evenements.py:g:41:29"),
    "MessageSent":          tl("engine/evenements.py:g:42:29"),
    "MessageEdited":        tl("engine/evenements.py:g:43:29"),
    "MessageForwarded":     tl("engine/evenements.py:g:44:29"),
    "ContactMuted":         tl("engine/evenements.py:g:45:29"),
    "ContactUnmuted":       tl("engine/evenements.py:g:46:29"),
    "ConversationMuted":    tl("engine/evenements.py:g:47:29"),
    "ConversationUnmuted":  tl("engine/evenements.py:g:48:29"),
}


def emettre(entreprise_id, evenement, **donnees):
    """Journalise un événement typé dans le registre existant (models.Bloc).

    Le reste de la chaîne (apps.declencheur_tache_blockchain ->
    declencheurs.evaluer_conditions catégorie "blockchain" ->
    agent.executer_tache) s'en charge ensuite automatiquement — aucun
    abonnement supplémentaire à écrire ici. `donnees` alimente aussi
    ontologie.construire_graphe si elle contient enregistrement_id ou
    table_id (voir la boucle sur bloc.transactions).
    Best-effort : une erreur de journalisation ne doit jamais faire
    échouer l'opération métier qui vient de se produire.
    """
    from .models import Bloc
    from .securite_dates import DateRetrograde
    try:
        return Bloc.ajouter_transaction(entreprise_id, {"type": evenement, **donnees})
    except DateRetrograde:
        raise   # la sécurité des dates n'est jamais « best-effort » : l'action est refusée
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            t("engine/evenements.py:g:71:13"), evenement, entreprise_id,
        )
        return None
