"""Déclenchement CONDITIONNEL des tâches (voir models.TachePlanifiee,
mode_declenchement="condition").

Complète le premier volet des tâches (répétitives, planifiées sur une heure
ou un intervalle) par un second volet : une tâche peut être lancée
automatiquement quand un événement réel se produit sur les données de
l'entreprise, plutôt qu'à une date fixe. Sept catégories (voir
TachePlanifiee.CONDITION_CATEGORIE_CHOICES) :

  - "enregistrement"    : un nouvel enregistrement est créé (n'importe quelle table)
  - "client"             : un nouvel enregistrement est créé dans une table client
                            (TableDynamique.est_table_membres=True)
  - "recherche"           : une recherche est exécutée (engine.views.api_lignes)
  - "relation"            : une nouvelle relation/liaison est créée dans l'ontologie
                            (ChampDynamique.type_champ="relation") — condition_parametres
                            peut préciser {"champ_id":…} pour restreindre à UN type de
                            relation, et {"sous_type":"surveillee"|"interdite"} pour de
                            la documentation/priorité côté utilisateur (la mécanique de
                            déclenchement est la même, seul le libellé change).
  - "densite_relation"    : le NOMBRE de relations (mêmes filtres que ci-dessus) franchit
                            le seuil condition_parametres["seuil"] — état mémorisé dans
                            condition_derniere_alerte pour ne pas redéclencher en boucle.
  - "kpi"                 : un IndicateurKPI (condition_parametres["kpi_id"]) entre en
                            alerte/danger OU atteint son objectif — appelé depuis
                            kpi._notifier_si_entree_en_alerte, qui dédoublonne déjà (via
                            IndicateurKPI.dernier_etat_alerte / objectif_notifie).
                            condition_parametres["evenement"] choisit lequel : "alerte"
                            (défaut, et seul comportement des tâches créées avant l'ajout
                            de ce paramètre), "objectif" ou "les_deux". Le script reçoit
                            aussi contexte["evenement"] ("alerte" / "objectif") pour savoir
                            ce qui l'a déclenché.
  - "blockchain"          : un nouveau Bloc est ajouté à la chaîne de l'entreprise —
                            condition_parametres["type_evenement"] filtre optionnellement
                            sur le type de transaction (voir models.Bloc).

Chaque déclenchement respecte un délai de repos anti-rafale (voir
_respecte_cooldown) réutilisant intervalle_type/intervalle_valeur, PUIS lance
la tâche via Celery : génération IA + exécution au tout premier déclenchement
(script_genere vide), simple exécution du script déjà généré ensuite — exactement
le même script est donc réutilisé à chaque nouvel événement, comme pour une
tâche planifiée répétitive.
"""
from engine.i18n import t
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

_DELTA_SECONDES = {"secondes": 1, "minutes": 60, "heures": 3600, "jours": 86400}


def _respecte_cooldown(tache):
    """En mode conditionnel, intervalle_type/intervalle_valeur = délai de repos
    minimum entre deux déclenchements (0 ou "unique" = aucun repos, toujours
    autorisé)."""
    if tache.intervalle_type == "unique" or not tache.intervalle_valeur:
        return True
    if not tache.derniere_execution:
        return True
    facteur = _DELTA_SECONDES.get(tache.intervalle_type)
    if not facteur:
        return True
    ecoule = (timezone.now() - tache.derniere_execution).total_seconds()
    return ecoule >= facteur * tache.intervalle_valeur


def _lancer(tache, contexte_execution=None):
    """Lance la tâche : génère le script au tout premier déclenchement
    (aucun script encore prêt), sinon réexécute directement le script déjà
    généré — même logique que les boutons ▶ Exécuter / 🔁 Régénérer côté UI.

    `contexte_execution` (dict JSON-sérialisable, optionnel) est transmis tel
    quel jusqu'à `agent.executer_tache`, qui le passe à `executer(contexte)`
    dans le script généré — c'est ce qui permet au script de retrouver la
    donnée PRÉCISE (enregistrement_id...) qui a déclenché CETTE exécution,
    en plus de la table (condition_table) à laquelle la tâche reste liée.

    Le passage à "en_cours" est un UPDATE atomique unique (exclude + update),
    pas un save() après lecture (voir bug corrigé 2026-09-24) : deux
    événements quasi simultanés sur le même déclencheur lisaient chacun
    l'ancien statut avant que l'autre n'ait sauvegardé, et lançaient la
    tâche deux fois. Ici, seul l'appel dont l'UPDATE affecte réellement une
    ligne (donc qui a "gagné" la course) poursuit."""
    from . import tasks as _taches
    from .models import TachePlanifiee

    maj = TachePlanifiee.objects.filter(id=tache.id).exclude(statut="en_cours").update(statut="en_cours")
    if not maj:
        return  # deja en cours (ou verrouillee par un evenement concurrent) : on ne double pas
    if tache.script_genere:
        _taches.executer_tache_planifiee_tache.delay(tache.id, contexte_execution)
    else:
        _taches.generer_et_executer_tache_tache.delay(tache.id, contexte_execution)


def _taches_actives(entreprise_id, categorie, table_id=None):
    from .models import TachePlanifiee

    qs = TachePlanifiee.objects.filter(
        entreprise_id=entreprise_id, actif=True,
        mode_declenchement="condition", condition_categorie=categorie,
    )
    if table_id is not None:
        from django.db.models import Q
        qs = qs.filter(Q(condition_table_id=table_id) | Q(condition_table__isnull=True))
    return list(qs)


def _contexte_execution(categorie, table_id, contexte):
    """Construit le dict transmis au script (voir _lancer / agent.executer_tache) :
    reprend `contexte` (déjà utilisé ci-dessus pour le filtrage par tâche) en
    l'assainissant pour Celery (JSON), et y ajoute `categorie`/`table_id` pour
    que le script sache toujours dans quel cadre il a été appelé, même s'il
    n'utilise pas `enregistrement_id`."""
    c = {k: v for k, v in (contexte or {}).items()}
    if isinstance(c.get("types_evenement"), set):
        c["types_evenement"] = sorted(c["types_evenement"])  # set -> liste (JSON-sérialisable)
    c["categorie"] = categorie
    if table_id is not None:
        c["table_id"] = table_id
    return c


def evaluer_conditions(entreprise_id, categorie, table_id=None, contexte=None):
    """Point d'entrée générique : évalue et déclenche toutes les tâches
    conditionnelles actives de l'entreprise pour `categorie` (et éventuellement
    restreintes à `table_id`). `contexte` est un dict libre propre à chaque
    catégorie (ex: {"champ_id":…} pour "relation", {"kpi_id":…} pour "kpi"),
    utilisé ici pour le filtrage PAR TÂCHE, et transmis ensuite (assaini par
    _contexte_execution) jusqu'au script exécuté — voir _lancer."""
    try:
        taches = _taches_actives(entreprise_id, categorie, table_id)
        if not taches:
            return
        contexte = contexte or {}
        for tache in taches:
            try:
                if categorie == "relation":
                    champ_filtre = tache.condition_parametres.get("champ_id")
                    if champ_filtre and str(champ_filtre) != str(contexte.get("champ_id")):
                        continue
                elif categorie == "kpi":
                    kpi_filtre = tache.condition_parametres.get("kpi_id")
                    if kpi_filtre and str(kpi_filtre) != str(contexte.get("kpi_id")):
                        continue
                    voulu = (tache.condition_parametres or {}).get("evenement") or "alerte"
                    if voulu != "les_deux" and voulu != (contexte.get("evenement") or "alerte"):
                        continue
                elif categorie == "blockchain":
                    type_filtre = tache.condition_parametres.get("type_evenement")
                    types_evenement = contexte.get("types_evenement")
                    if types_evenement is not None:
                        # Plusieurs types regroupés dans UN SEUL bloc (voir Bloc.
                        # ajouter_transactions, import en masse) : un seul passage
                        # par tâche pour tout le bloc (bug corrigé 2026-09-24 — une
                        # tâche sans filtre se relançait sinon une fois par type
                        # distinct présent dans le même bloc).
                        if type_filtre and type_filtre not in types_evenement:
                            continue
                    elif type_filtre and type_filtre != contexte.get("type_evenement"):
                        continue
                if not _respecte_cooldown(tache):
                    continue
                _lancer(tache, _contexte_execution(categorie, table_id, contexte))
            except Exception:
                logger.exception(t("engine/declencheurs.py:g:166:34"), tache.id, categorie)
    except Exception:
        logger.exception(t("engine/declencheurs.py:g:168:26"), entreprise_id, categorie)


def evaluer_densite_relation(entreprise_id, table_id=None, champ_id=None):
    """Catégorie à seuil ("densite_relation") : pull, pas événementielle pure —
    compare le nombre ACTUEL de relations (filtré comme la tâche le demande) au
    seuil configuré, et ne redéclenche que lors d'un FRANCHISSEMENT (mémorisé
    dans condition_derniere_alerte), jamais tant que le compte reste au-dessus."""
    from .models import ValeurChamp

    taches = _taches_actives(entreprise_id, "densite_relation", table_id)
    if not taches:
        return
    for tache in taches:
        try:
            params = tache.condition_parametres or {}
            seuil = params.get("seuil")
            if seuil is None:
                continue
            qs = ValeurChamp.objects.filter(
                champ__type_champ="relation", valeur__gt="",
                champ__table__entreprise_id=entreprise_id,
            )
            # BUG corrigé (2026-09-24) : `champ_id` ici est le champ précis qui vient de
            # déclencher l'évaluation (voir apps.py, appel systématique avec champ_id=champ.id).
            # L'utiliser en repli quand la tâche n'a PAS choisi de champ_id dans ses paramètres
            # réduisait à tort le décompte à ce seul champ à chaque fois — l'option "Toutes les
            # relations (table ci-dessus)" ne comptait donc jamais que le champ qui venait de
            # changer, jamais le total de la table. Seul condition_parametres["champ_id"]
            # (le choix explicite de la tâche) doit restreindre le décompte ; `champ_id`
            # (paramètre de fonction) ne sert plus qu'à sélectionner les tâches actives (table_id).
            cible_champ = params.get("champ_id")
            if cible_champ:
                qs = qs.filter(champ_id=cible_champ)
            elif tache.condition_table_id:
                qs = qs.filter(champ__table_id=tache.condition_table_id)
            total = qs.count()
            etat = "au_dessus" if total >= int(seuil) else "en_dessous"
            if etat == "au_dessus" and tache.condition_derniere_alerte != "au_dessus":
                if _respecte_cooldown(tache):
                    _lancer(tache, _contexte_execution("densite_relation", table_id, {"champ_id": cible_champ, "seuil": seuil, "total": total}))
                tache.condition_derniere_alerte = "au_dessus"
                tache.save(update_fields=["condition_derniere_alerte"])
            elif etat == "en_dessous" and tache.condition_derniere_alerte != "en_dessous":
                tache.condition_derniere_alerte = "en_dessous"
                tache.save(update_fields=["condition_derniere_alerte"])
        except Exception:
            logger.exception(t("engine/declencheurs.py:g:215:30"), tache.id)
