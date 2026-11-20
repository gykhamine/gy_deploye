import sys
import threading

from django.apps import AppConfig
from django.db import transaction
from django.utils import timezone as django_timezone
from engine.i18n import t as _gt  # noqa: E402

# Pile locale au thread : mémorise, pour UNE cascade de propagation
# "GroupePartage" (un seul edit utilisateur), l'ensemble des tables déjà
# synchronisées. Sans ça, une propagation en étoile (T1 -> T2 -> T1 -> T2...)
# créerait une nouvelle ligne à chaque aller-retour, indéfiniment — voir
# propager_groupe_partage ci-dessous.
_cascade_groupe_partage = threading.local()


def _propager_vers_table_cible(champ_cible, valeur):
    """Propage `valeur` vers `champ_cible` dans sa table, sans créer de doublon.

    Stratégie robuste face à la concurrence (plusieurs workers/onglets) :
    - Tout le bloc lecture+création est enveloppé dans un SELECT FOR UPDATE
      sur la table cible, ce qui sérialise les workers au niveau BD.
    - Si malgré tout un doublon est créé entre deux transactions (cas SQLite
      sans FOR UPDATE réel), update_or_create garantit l'idempotence de la
      valeur sans écraser un enregistrement existant.
    """
    from .models import Enregistrement, ValeurChamp

    with transaction.atomic():
        # Verrouillage pessimiste : on bloque les autres workers sur la table
        # cible pendant toute la durée du SELECT + éventuel CREATE.
        table_verrouillee = (
            champ_cible.table.__class__.objects
            .select_for_update()
            .get(pk=champ_cible.table_id)
        )

        valeurs_existantes = ValeurChamp.objects.filter(champ=champ_cible)
        cible = (
            Enregistrement.objects
            .filter(table_id=table_verrouillee.pk)
            .exclude(id__in=valeurs_existantes.values("enregistrement_id"))
            .order_by("-cree_le")
            .first()
        )
        if cible is None:
            plafond = table_verrouillee.plafond_lignes
            if not table_verrouillee.est_derniere_du_groupe:   # partie pleine d'un groupe : elle ne reçoit plus aucune ligne
                return
            if plafond is not None and table_verrouillee.enregistrements.count() >= plafond:
                # Plafond atteint : on ne crée pas, on laisse la table non synchronisée.
                return
            cible = Enregistrement.objects.create(table_id=table_verrouillee.pk)

        ValeurChamp.objects.update_or_create(
            enregistrement=cible, champ=champ_cible,
            defaults={"valeur": valeur},
        )


class EngineConfig(AppConfig):
    name = "engine"
    default_auto_field = "django.db.models.BigAutoField"

    @staticmethod
    def _prechauffer_ia():
        """Charge le module IA (torch, sklearn...) en arriere-plan des le demarrage du serveur : le premier appel de
        /api/ia/types-modeles/ ne bloque plus le thread synchrone d'asgiref (>10 s -> requete tuee par Daphne).
        Seulement pour un serveur (runserver / daphne), jamais pour les autres commandes manage.py ni le parent du
        rechargeur automatique. Desactivable : GYPLATFORM_PRECHAUFFE_IA=0 (force : =1)."""
        import os
        import threading
        choix = os.environ.get("GYPLATFORM_PRECHAUFFE_IA", "")
        if choix == "0":
            return
        serveur = "runserver" in sys.argv or os.path.basename(sys.argv[0]).startswith(("uvicorn", "gunicorn"))
        if choix != "1" and not serveur:
            return
        if "runserver" in sys.argv and "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return   # parent du rechargeur : c'est le processus enfant qui sert les requetes

        def _faire():
            from .ia_integration import prechauffer_types_modeles
            prechauffer_types_modeles()
        threading.Thread(target=_faire, name="prechauffage-ia", daemon=True).start()

    @staticmethod
    def _appliquer_langue_interface():
        """Langue de l'interface réglée par l'administrateur (Entreprise.langue_interface, page Personnalisation) : lue en base dès le
        démarrage du serveur, sans aucune action manuelle (engine/i18n.py::langue_admin, relue ensuite régulièrement). Fait en arrière-plan :
        pas d'accès à la base pendant l'initialisation des applications. Serveur seulement (mêmes conditions que le préchauffage IA)."""
        import os
        import threading
        serveur = "runserver" in sys.argv or os.path.basename(sys.argv[0]).startswith(("uvicorn", "gunicorn"))
        if not serveur:
            return
        if "runserver" in sys.argv and "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        def _faire():
            import logging
            try:
                from . import i18n
                i18n.oublier_langue_admin()
                code = i18n.langue_admin()
                logging.getLogger("engine.i18n").info(_gt("rg5.msg.052"), code or "automatique (%s)" % i18n.detecter_langue_serveur())
            except Exception:
                logging.getLogger("engine.i18n").exception(_gt("rg5.msg.055"))
            finally:
                try:
                    from django.db import connection
                    connection.close()
                except Exception:
                    pass
        threading.Thread(target=_faire, name="langue-interface", daemon=True).start()

    def ready(self):
        """Enregistre les signaux de propagation des liens inter-tables."""
        from .base_par_entreprise import installer as _installer_bases   # SQLite par entreprise (sans effet sinon)
        _installer_bases()
        # Aucun signal metier pendant migrate/makemigrations (evite requetes/verrous inutiles).
        if "migrate" in sys.argv or "makemigrations" in sys.argv:
            return
        self._appliquer_langue_interface()
        self._prechauffer_ia()
        from django.db.models.signals import post_save
        from django.dispatch import receiver

        try:  # SIEM : signaux de connexion et protection de JournalAudit (engine/siem_collecte.py)
            from .siem_collecte import brancher as _siem_brancher
            _siem_brancher()
        except Exception:
            import logging
            logging.getLogger("engine.siem").exception(_gt("rg5.msg.053"))

        try:  # captures des flux média : ménage quand une ligne, une colonne ou un flux est supprimé (flux_nettoyage.py)
            from .flux_nettoyage import brancher as _flux_nettoyage_brancher
            _flux_nettoyage_brancher()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(_gt("rg5.msg.054"))

        try:  # images automatiques (si l'administrateur les a activées) : voir image_auto.py
            from .image_auto import demarrer_au_boot
            demarrer_au_boot()
        except Exception:
            pass

        try:  # veille Parquet : compare de temps en temps le contenu des fichiers à l'EAV (source de vérité) et les reconstruit au besoin (parquet_veille.py)
            from .parquet_veille import demarrer_au_boot as _parquet_veille_au_boot
            _parquet_veille_au_boot()
        except Exception:
            pass

        try:  # surveillance intelligente des flux média : reprise automatique des analyses actives (flux_vision.py)
            from .flux_vision import demarrer_au_boot as _vision_au_boot
            _vision_au_boot()
        except Exception:
            pass

        try:  # fond de carte : le serveur complète et renouvelle son cache de tuiles tout seul (geo_tuiles.py)
            from .geo_tuiles import demarrer_au_boot as _tuiles_au_boot
            _tuiles_au_boot()
        except Exception:
            pass

        # Import différé pour éviter les imports circulaires au boot
        from .models import ValeurChamp, LienChamp, ChampDynamique, Enregistrement  # noqa: F401

        @receiver(post_save, sender=ValeurChamp, dispatch_uid="toucher_maj_ligne_sur_valeur")
        def toucher_maj_ligne_sur_valeur(sender, instance, **kwargs):
            """Répercute la modification d'une cellule sur
            Enregistrement.maj_le de SA ligne.

            metadata.empreinte_donnees (fraîcheur du cache KPI/dashboard BI)
            se base sur le nombre de lignes ET sur le max(maj_le) des
            Enregistrement de la table. Or éditer une seule cellule ne
            touchait, avant ce correctif, QUE la ValeurChamp elle-même —
            jamais l'Enregistrement parent — donc l'empreinte ne changeait
            pas après une édition et les KPI/graphiques en cache restaient
            « à jour » avec d'anciennes valeurs.

            .update() (pas .save()) : évite de redéclencher Enregistrement.
            save() (matricule/code d'accès) et tout signal post_save sur ce
            modèle, sans repasser par le cycle de validation complet — on
            ne veut ici QUE rafraîchir l'horodatage."""
            if kwargs.get("raw"):
                return   # chargement brut (restauration d'une archive, loaddata) : maj_le est déjà celui de l'archive ; le retoucher rendrait périmés les fichiers Parquet restaurés
            from .models import Enregistrement
            Enregistrement.objects.filter(pk=instance.enregistrement_id).update(
                maj_le=django_timezone.now()
            )

        @receiver(post_save, sender=ValeurChamp, dispatch_uid="propager_liens_champ")
        def propager_liens_champ(sender, instance, created, **kwargs):
            """Quand une ValeurChamp source est modifiée, propage la nouvelle
            valeur à tous les enfants enregistrés dans LienChamp.

            Gère aussi le sens inverse : si la ValeurChamp modifiée est elle-
            même l'enfant d'un lien dont le champ est marqué
            `relation_bidirectionnelle`, la nouvelle valeur remonte vers la
            ligne source — qui, via son propre `.save()`, redéclenche ce
            même signal et repropage donc automatiquement à TOUTES les
            autres tables reliées à cette source (effet "colonne partagée").
            """
            # On ne propage que si c'est une mise à jour (pas une création).
            if created:
                return

            # ── Sens 1 : cette ValeurChamp est une SOURCE → vers ses enfants ──
            liens = LienChamp.objects.filter(
                enregistrement_source=instance.enregistrement,
                champ_source=instance.champ,
            ).select_related("valeur_enfant")
            for lien in liens:
                ve = lien.valeur_enfant
                if ve.valeur != instance.valeur:
                    # .update() (pas .save()) pour éviter une boucle récursive
                    # de signaux — mais .update() ne déclenche PAS post_save,
                    # donc toucher_maj_ligne_sur_valeur ne s'exécute pas pour
                    # cette ValeurChamp enfant : son Enregistrement.maj_le
                    # restait périmé après une propagation automatique, alors
                    # que la ligne avait bien changé (même bug que celui
                    # corrigé par toucher_maj_ligne_sur_valeur pour l'édition
                    # directe). On rafraîchit donc ici explicitement, par le
                    # même mécanisme .update() ciblé.
                    ValeurChamp.objects.filter(pk=ve.pk).update(valeur=instance.valeur)
                    Enregistrement.objects.filter(pk=ve.enregistrement_id).update(
                        maj_le=django_timezone.now()
                    )

            # ── Sens 2 : cette ValeurChamp est un ENFANT bidirectionnel → vers sa source ──
            if not getattr(instance.champ, "relation_bidirectionnelle", False):
                return
            lien_parent = getattr(instance, "lien_source", None)
            if not lien_parent or not lien_parent.enregistrement_source_id or not lien_parent.champ_source_id:
                return
            source = ValeurChamp.objects.filter(
                enregistrement_id=lien_parent.enregistrement_source_id,
                champ_id=lien_parent.champ_source_id,
            ).first()
            if source and source.valeur != instance.valeur:
                # .save() (pas .update()) pour redéclencher ce signal et donc
                # repropager au reste de la famille de tables liées.
                source.valeur = instance.valeur
                source.save(update_fields=["valeur"])

        @receiver(post_save, sender=ValeurChamp, dispatch_uid="propager_groupe_partage")
        def propager_groupe_partage(sender, instance, created, **kwargs):
            """Synchronise une "colonne partagée" (GroupePartage) : quand un
            champ membre du groupe est renseigné/modifié sur une ligne,
            répercute la même valeur dans CHAQUE AUTRE table du groupe.

            Contrairement à `propager_liens_champ` (qui suit un lien explicite
            LienChamp vers UNE ligne précise), il n'y a ici aucune ligne
            désignée à l'avance dans les autres tables : la ligne cible est
            retrouvée automatiquement, table par table :
              1. une ligne qui n'a PAS ENCORE de valeur pour ce champ précis
                 est complétée en priorité (même si le reste de la ligne est
                 déjà rempli) ;
              2. sinon une nouvelle ligne est créée (dans la limite du
                 plafond de lignes de la table).

            Propage aussi bien sur création que sur modification (une
            colonne partagée doit se propager dès sa toute première saisie,
            pas seulement lors d'une mise à jour ultérieure).
            """
            champ = instance.champ
            if not champ.groupe_partage_id or champ.type_champ in ("constante", "horodatage", "id_client"):
                # Une constante est recopiée par assurer_constantes : la propager créerait des lignes fantômes ailleurs.
                return

            # ── Garde anti-boucle : une cascade ne visite chaque table
            # qu'une seule fois, quel que soit le nombre d'allers-retours
            # entre champs du même groupe (voir _cascade_groupe_partage).
            cascade_racine = not hasattr(_cascade_groupe_partage, "tables")
            if cascade_racine:
                _cascade_groupe_partage.tables = {champ.table_id}
            try:
                deja_visitees = _cascade_groupe_partage.tables
                # NB : on ne filtre PAS `table_id__in=deja_visitees` ici. Le
                # QuerySet ne serait évalué (donc figé) qu'au premier accès
                # de la boucle ci-dessous, c'est-à-dire AVANT que la
                # première itération n'ait pu marquer quoi que ce soit comme
                # visité. Une propagation imbriquée (déclenchée par le
                # `.save()` interne de `_propager_vers_table_cible`, qui
                # redéclenche ce même signal) peut alors déjà avoir visité
                # une table plus loin dans cette liste figée : sans un
                # second contrôle fait a chaque itération, la boucle
                # extérieure la revisiterait quand même et créerait une
                # ligne en double avec la même valeur.
                autres_champs = (
                    ChampDynamique.objects
                    .filter(groupe_partage_id=champ.groupe_partage_id)
                    .exclude(pk=champ.pk)
                    .select_related("table")
                )
                for autre in autres_champs:
                    if autre.table_id in deja_visitees:
                        # Déjà synchronisée par une cascade imbriquée
                        # survenue pendant cette même boucle : on saute,
                        # sinon on duplique la ligne qui vient d'être créée.
                        continue
                    deja_visitees.add(autre.table_id)
                    _propager_vers_table_cible(autre, instance.valeur)
            finally:
                if cascade_racine:
                    del _cascade_groupe_partage.tables

        @receiver(post_save, sender=ValeurChamp, dispatch_uid="recalculer_champs_formule")
        def recalculer_champs_formule(sender, instance, created, **kwargs):
            """Recalcule automatiquement tous les champs calculés
            (type_champ='formule', voir ChampDynamique.formule_expression)
            de la table à chaque sauvegarde d'un champ NON calculé de la
            même ligne — voir calculs.recalculer_champs_formule_ligne.
            Le garde-fou `!= 'formule'` empêche toute boucle : sauvegarder
            le résultat d'une formule ne redéclenche pas ce même recalcul."""
            if instance.champ.type_champ in ("formule", "constante", "horodatage", "id_client"):
                return
            from .calculs import assurer_constantes, recalculer_champs_formule_ligne
            # Constantes d'abord : une ligne créée par n'importe quel chemin les reçoit avant le calcul des formules.
            assurer_constantes(instance.enregistrement)
            recalculer_champs_formule_ligne(instance.enregistrement)

        # ── Blockchain (voir models.Bloc) : chaque table creee produit une
        # transaction sur la chaine de SON entreprise. Un seul fil par
        # entreprise ; les vues "table"/"agent" (voir ontologie.
        # construire_graphe) ne sont que des filtres sur ces memes
        # transactions.
        #
        # Les creations/modifications/suppressions de LIGNE ne sont PAS
        # tracees ici via signal : au moment ou post_save(Enregistrement)
        # se declenche, les ValeurChamp (donnees EAV) ne sont pas encore
        # ecrites (creation) ou n'ont jamais change (modification passe
        # uniquement par ValeurChamp.save(), jamais par Enregistrement.
        # save()) — un signal ici ne peut produire qu'un bloc VIDE. Ces
        # trois evenements sont donc journalises explicitement, une fois
        # les vraies valeurs connues, dans views.py (api_ajouter_ligne,
        # api_modifier_ligne, api_supprimer_ligne) et import_fichier.py
        # (import en masse). Le filet de securite pour tout ce qui
        # contournerait ces points d'entree (admin Django, shell...) est
        # Bloc.combler_lacunes, qui capture aussi les vraies donnees.
        from .models import Bloc, TableDynamique

        # ── Fichier Parquet des tables EAV (parquet_store.py) ────────────────────────────────────────────────────────
        # Création d'une table, ajout/suppression/changement d'une colonne : le fichier est créé ou mis à jour à la validation de
        # la transaction (UNE synchronisation par table et par transaction, même si 50 colonnes sont créées d'un coup). Suppression
        # d'une table : son fichier est retiré. Les lignes, elles, sont synchronisées à l'enregistrement (views api_lignes) et, pour
        # tout le reste (édition de cellule, import, flux), à la prochaine lecture analytique (jamais de fichier périmé lu).
        from django.db.models.signals import post_delete

        def _planifier_synchro_parquet(table_id):
            from . import parquet_store

            def _faire():
                table = TableDynamique.objects.filter(pk=table_id).first()
                if table is not None:
                    parquet_store.synchroniser_table(table)
            # dédoublonné sur les rappels réellement en attente : l'ancien ensemble tenu sur la connexion restait « plein » après un
            # rollback (le rappel n'était jamais exécuté, donc jamais retiré) et bloquait toute synchronisation suivante de la table
            parquet_store.sur_validation(("table", table_id), _faire)

        @receiver(post_save, sender=TableDynamique, dispatch_uid="parquet_creation_table")
        def parquet_creation_table(sender, instance, created, **kwargs):
            if created:
                _planifier_synchro_parquet(instance.pk)

        @receiver([post_save, post_delete], sender=ChampDynamique, dispatch_uid="parquet_colonnes_table")
        def parquet_colonnes_table(sender, instance, **kwargs):
            if instance.table_id:
                _planifier_synchro_parquet(instance.table_id)

        @receiver(post_delete, sender=TableDynamique, dispatch_uid="parquet_suppression_table")
        def parquet_suppression_table(sender, instance, **kwargs):
            def _retirer():
                from . import parquet_store
                parquet_store.supprimer_fichier(instance)
            transaction.on_commit(_retirer)

        @receiver(post_save, sender=TableDynamique, dispatch_uid="blockchain_creation_table")
        def blockchain_creation_table(sender, instance, created, **kwargs):
            if created:
                Bloc.ajouter_transaction(instance.entreprise_id, {
                    "type": "creation_table", "table_id": instance.id, "table": instance.nom,
                })

        # ── Tâches CONDITIONNELLES (voir engine/declencheurs.py + models.
        # TachePlanifiee.mode_declenchement="condition") : ces trois signaux
        # couvrent les événements "enregistrement"/"client" (nouvelle ligne),
        # "relation"+"densite_relation" (nouvelle valeur sur une colonne de
        # type relation) et "blockchain" (nouveau bloc), quel que soit le
        # point d'entrée réel (formulaire agent, formulaire client, import en
        # masse, synchronisation externe...) — plus fiable que d'appeler le
        # déclencheur depuis chaque vue une par une. "recherche" et "kpi"
        # sont déclenchés explicitement (views.api_lignes / kpi._notifier_si_
        # entree_en_alerte) car ce ne sont pas des créations de ligne.
        from . import declencheurs as _declencheurs

        @receiver(post_save, sender=Enregistrement, dispatch_uid="declencheur_tache_enregistrement")
        def declencheur_tache_enregistrement(sender, instance, created, **kwargs):
            if not created:
                return
            entreprise_id = instance.table.entreprise_id
            # enregistrement_id transmis au script exécuté (voir agent.executer_tache) :
            # la tâche reste liée à la TABLE (condition_table) pour savoir QUAND se
            # déclencher, mais doit aussi connaître la LIGNE précise qui vient d'être
            # créée pour agir sur ces données-là, pas sur la table entière.
            contexte = {"enregistrement_id": instance.id}
            _declencheurs.evaluer_conditions(
                entreprise_id, "enregistrement", table_id=instance.table_id, contexte=contexte,
            )
            if instance.table.a_identifiants_connexion:
                _declencheurs.evaluer_conditions(
                    entreprise_id, "client", table_id=instance.table_id, contexte=contexte,
                )

        @receiver(post_save, sender=ValeurChamp, dispatch_uid="declencheur_tache_relation")
        def declencheur_tache_relation(sender, instance, created, **kwargs):
            champ = instance.champ
            if champ.type_champ != "relation" or not instance.valeur:
                return
            if not created:
                return  # une modification de relation existante n'est pas une nouvelle liaison
            entreprise_id = champ.table.entreprise_id
            table_id = champ.table_id
            _declencheurs.evaluer_conditions(
                entreprise_id, "relation", table_id=table_id,
                # enregistrement_id : la ligne PORTANT la relation (celle où le champ a
                # été renseigné) — transmise au script exécuté pour la même raison que
                # ci-dessus (voir agent.executer_tache).
                contexte={"champ_id": champ.id, "enregistrement_id": instance.enregistrement_id},
            )
            _declencheurs.evaluer_densite_relation(entreprise_id, table_id=table_id, champ_id=champ.id)
            # Événement "LinkCreated" (voir evenements.py) : journalisé APRÈS
            # evaluer_conditions ci-dessus (catégorie "relation", déjà dédiée)
            # pour ne rien changer à son déclenchement ; ce Bloc supplémentaire
            # réévalue en plus, via le signal post_save(Bloc), la catégorie
            # "blockchain" pour les tâches qui filtrent sur type_evenement="LinkCreated".
            from . import evenements as _evenements
            _evenements.emettre(
                entreprise_id, "LinkCreated", table_id=table_id,
                enregistrement_id=instance.enregistrement_id, champ_id=champ.id,
            )

        @receiver(post_save, sender=Bloc, dispatch_uid="declencheur_tache_blockchain")
        def declencheur_tache_blockchain(sender, instance, created, **kwargs):
            if not created:
                return
            types = {t.get("type") for t in (instance.transactions or []) if isinstance(t, dict)}
            # Un seul passage par tâche pour CE bloc, même s'il regroupe plusieurs
            # types d'événements (voir Bloc.ajouter_transactions, import en masse) —
            # evaluer_conditions dédoublonne en interne sur `types_evenement`
            # (bug corrigé 2026-09-24 : une tâche sans filtre de type se relançait
            # avant une fois par type distinct présent dans le même bloc).
            _declencheurs.evaluer_conditions(
                instance.entreprise_id, "blockchain",
                contexte={"types_evenement": types} if types else None,
            )

        # ── Modèle fantôme : chaque nouvelle table / nouvelle ligne déclenche (avec coalescence, en tâche de fond) un
        # fine-tuning paresseux du modèle qui pilote les recommandations (engine/memoire_decision.py) ──
        def modele_fantome_nouvelle_donnee(sender, instance, created, **kwargs):
            if created:
                from . import memoire_decision as _memoire
                _memoire.planifier_entretien(getattr(instance, "entreprise_id", None) or instance.table.entreprise_id)

        post_save.connect(modele_fantome_nouvelle_donnee, sender=Enregistrement, dispatch_uid="modele_fantome_nouvelle_ligne")
        post_save.connect(modele_fantome_nouvelle_donnee, sender=TableDynamique, dispatch_uid="modele_fantome_nouvelle_table")


# ── Fonctions fusionnées depuis gscode ──

_cascade_partage = threading.local()

def _cascade_entrer():
    """Assure l'existence de l'état de cascade partagé pour ce thread.
    Retourne True si CET appel en est la racine (donc responsable de le
    détruire une fois sa propagation terminée), False s'il était déjà
    présent (cascade imbriquée)."""
    est_racine = not hasattr(_cascade_partage, "tables_colonne")
    if est_racine:
        _cascade_partage.tables_colonne = set()
        _cascade_partage.cellules = set()
    return est_racine



def _cascade_sortir(est_racine):
    if est_racine:
        del _cascade_partage.tables_colonne
        del _cascade_partage.cellules


