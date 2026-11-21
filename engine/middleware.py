"""
Middleware qui reproduit le comportement zero-config d'avant (creation
automatique des tables `engine` si elles n'existent pas) mais SANS toucher
la base pendant l'init de l'app Django.

Pourquoi un middleware ?
  - Django 5+ a ajoute un RuntimeWarning explicite : il est DECONSEILLE de
    faire des requetes SQL dans `AppConfig.ready()` ou pendant l'import des
    modules. La regle : l'app registry ne doit faire QUE declarer des
    modeles / signaux, pas interroger la DB.
  - En deplacant ce check au premier passage de requete HTTP, on est
    sur que :
      1. Le registre d'apps est completement initialise (les autres apps,
         notament auth/sessions, sont deja pretes).
      2. La connexion DB est ouverte "normalement" (pas pendant le boot).
      3. Le warning disparait.
  - L'operation est faite UNE SEULE FOIS (flag `_initialized`) pour ne pas
    payer le check a chaque requete.

Quand ce middleware s'execute-t-il ?
  - A la PREMIERE requete HTTP qui traverse la stack (donc apres l'init
    complete de Django, y compris le warning deprecation le cas echeant).
  - PAS pendant les commandes de management (migrate, makemigrations,
    collectstatic, test, shell) -> court-circuit via sys.argv.
"""
import logging
import os
import sys

from .i18n import t   # t() est utilisé dans les `except` ci-dessous : sans cet import, un NameError y masquait l'erreur réelle et faisait un 500
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7

logger = logging.getLogger(__name__)

# Commandes de management ou on ne veut PAS declencher l'auto-init (Django
# gere deja tout ou on n'a pas besoin d'un schema pret).
_COMMANDS_SKIP = {
    "migrate", "makemigrations", "collectstatic", "test", "shell",
    "dbshell", "createsuperuser", "showmigrations", "sqlmigrate",
    "check", "loaddata", "dumpdata", "inspectdb", "compilemessages",
    "makemessages", "diffsettings", "sendtestemail", "help", "version",
}


def _should_skip() -> bool:
    """True si la commande en cours est une commande de management qui ne
    beneficie pas de l'auto-init (et ou le declencher genererait du bruit)."""
    if len(sys.argv) < 2:
        return False
    return sys.argv[1] in _COMMANDS_SKIP


class EnsureEngineSchemaMiddleware:
    """Au premier appel HTTP, verifie que les tables `engine` existent.
    Si non, les cree via le schema editor et marque les migrations comme
    appliquees (pour qu'un futur `migrate` ne tente pas de les recreer)."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._initialized = False

    def __call__(self, request):
        if not self._initialized and not _should_skip():
            self._ensure_schema_once()
            self._initialized = True
        return self.get_response(request)

    # ── helpers ────────────────────────────────────────────────────────────

    def _ensure_schema_once(self):
        """Verifie puis cree le schema engine si necessaire. Best-effort :
        en cas d'erreur, on log et on continue (le serveur demarre quand
        meme, et l'erreur apparaitra plus tard a la premiere vraie requete
        sur un modele, ce qui est preferable a un crash au boot)."""
        try:
            # Table du cache (limitation de débit, verrou de connexion) : sans elle, la page de
            # connexion plantait (500) sur une installation neuve où `createcachetable` n'a pas été lancé.
            from django.core.management import call_command
            call_command("createcachetable", verbosity=0)  # idempotent
        except Exception as e:
            logger.warning(t("engine/middleware.py:g:88:28"), e)
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='engine_entreprise'"
                )
                schema_present = bool(cursor.fetchone())
            if not schema_present:
                self._creer_tables()
        except Exception as e:
            logger.warning(t("engine/middleware.py:g:100:28"), e)
        # Base déjà initialisée mais en retard sur les modèles (livraison avec de nouvelles colonnes/tables, migration
        # jamais lancée ou marquée « appliquée » sans avoir touché la base) : on rattrape, sans jamais bloquer le démarrage.
        try:
            self._rattraper_schema()
        except Exception as e:
            logger.warning(t("engine/middleware.py:g:106:28"), e)

    @staticmethod
    def _migrations_en_attente():
        """Migrations `engine` pas encore enregistrées comme appliquées (liste de noms, dans l'ordre)."""
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executeur = MigrationExecutor(connection)
        cibles = [n for n in executeur.loader.graph.leaf_nodes() if n[0] == "engine"]
        return [m.name for m, _retour in executeur.migration_plan(cibles) if m.app_label == "engine"]

    def _rattraper_schema(self):
        """1) applique les migrations `engine` en attente (le chemin normal, données comprises) ;
        2) si la base est encore en retard sur les modèles (migration en échec, ou marquée appliquée sans l'avoir été),
        ajoute les tables/colonnes manquantes ; 3) si ce rattrapage a servi, marque les migrations restantes comme
        appliquées pour qu'un `migrate` manuel ne bute pas sur « duplicate column »."""
        from django.core.management import call_command
        try:
            en_attente = self._migrations_en_attente()
        except Exception as e:                 # graphe de migrations illisible : le rattrapage des colonnes reste possible
            logger.warning(t("engine/middleware.py:g:126:28"), e)
            en_attente = []
        if en_attente:
            logger.warning(t("engine/middleware.py:g:129:28"), en_attente)
            try:
                call_command("migrate", "engine", interactive=False, verbosity=0)
            except Exception as e:
                logger.warning(t("engine/middleware.py:g:133:32"), e)
        a_ajoute = self._reparer_schema()
        if a_ajoute:
            from django.db import connection
            from django.db.migrations.recorder import MigrationRecorder
            recorder = MigrationRecorder(connection)
            recorder.ensure_schema()
            try:
                for nom in self._migrations_en_attente():
                    recorder.record_applied("engine", nom)
            except Exception as e:
                logger.warning(t("engine/middleware.py:g:144:32"), e)

    def _reparer_schema(self):
        """Ajoute à la base ce qui manque par rapport aux modèles `engine` : tables absentes (create_model) et colonnes
        absentes (add_field). Strictement additif : ne supprime, ne renomme et ne modifie rien. Corrige les
        « no such column: engine_xxx.yyy » (500) quand `migrate` n'a pas été (bien) appliqué. Renvoie True si quelque chose a été ajouté."""
        from django.apps import apps
        from django.db import connection

        modeles = list(apps.get_app_config("engine").get_models())
        if apps.is_installed("engine.paiements_carte"):   # table du paiement par carte : `makemigrations` ne peut pas la produire ici (pas de migration initiale engine)
            modeles += list(apps.get_app_config("paiements_carte").get_models())
        tables = set(connection.introspection.table_names())
        tables_a_creer, colonnes_a_ajouter = [], []
        with connection.cursor() as cursor:
            for modele in modeles:
                table = modele._meta.db_table
                if table not in tables:
                    tables_a_creer.append(modele)
                    continue
                presentes = {c.name for c in connection.introspection.get_table_description(cursor, table)}
                for champ in modele._meta.local_concrete_fields:
                    if champ.column not in presentes:
                        colonnes_a_ajouter.append((modele, champ))
        ajoute = False
        if tables_a_creer:
            try:
                with connection.schema_editor() as editeur:       # un seul éditeur : les clés étrangères entre tables neuves sont différées
                    for modele in tables_a_creer:
                        editeur.create_model(modele)
                        logger.warning(_gt("rg8.msg.052"), modele._meta.db_table)
                ajoute = True
            except Exception as e:
                logger.warning(t("engine/middleware.py:g:175:32"), e)
        for modele, champ in colonnes_a_ajouter:
            try:
                with connection.schema_editor() as editeur:       # un éditeur par colonne : un échec n'annule pas les autres
                    editeur.add_field(modele, champ)
                logger.warning(_gt("rg8.msg.049"), modele._meta.db_table, champ.column)
                ajoute = True
            except Exception as e:
                logger.warning(_gt("rg8.msg.050"), modele._meta.db_table, champ.column, e)
        return ajoute

    def _creer_tables(self):
        """Cree toutes les tables de l'app engine via schema_editor, puis
        marque les migrations 'engine' comme deja appliquees pour qu'un
        `manage.py migrate` ulterieur (auth, sessions) ne tente pas de
        recreer ces tables et n'echoue pas sur 'already exists'."""
        from django.db import connection
        from django.apps import apps
        from django.db.migrations.recorder import MigrationRecorder

        app_config = apps.get_app_config("engine")
        with connection.schema_editor() as schema_editor:
            for model in app_config.get_models():
                try:
                    schema_editor.create_model(model)
                    logger.info(_gt("rg8.msg.051"), model._meta.db_table)
                except Exception as e:
                    logger.debug("engine: table %s already present (%s).", model._meta.db_table, e)

        try:
            recorder = MigrationRecorder(connection)
            recorder.ensure_schema()
            for nom_migration in self._noms_migrations():
                recorder.record_applied("engine", nom_migration)
        except Exception as e:
            logger.debug("engine: marquage migrations ignore (%s).", e)

    @staticmethod
    def _noms_migrations():
        import os
        dossier = os.path.join(os.path.dirname(__file__), "migrations")
        return sorted(
            f[:-3] for f in os.listdir(dossier)
            if f.endswith(".py") and f != "__init__.py"
        )


class LangueMiddleware:
    """Positionne la langue courante pour t()/tf() (engine/i18n.py) à chaque
    requête : choix PERSONNEL de l'utilisateur (langue de son compte enregistrée
    en base depuis son Profil, sinon session ; cookie « langue » pour un visiteur non connecté), sinon la langue réglée par l'administrateur
    (base de données, page Personnalisation), sinon la langue du serveur détectée
    automatiquement (i18n.detecter_langue_serveur), sinon l'anglais si aucun
    catalogue n'existe pour elle. La langue du navigateur n'est
    volontairement pas consultée : on change de langue dans le Profil.

    Doit être placé APRÈS SessionMiddleware (on lit request.session). La
    langue est portée par un contextvar (pas une variable globale, comme
    côté GTK) car un même process Django sert plusieurs requêtes/utilisateurs
    en parallèle — voir le docstring de engine/i18n.py pour le détail."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import i18n

        # Choix personnel (compte en base, session, cookie du visiteur), sinon langue par défaut du système (administrateur, puis serveur).
        code = i18n.langue_choisie(request) or i18n.langue_serveur_effective(request)
        request.langue = i18n.utiliser_langue(code)
        return self.get_response(request)


class ApiErrorMiddleware:
    """Convertit toute exception non gerée levée par une vue `/api/...` en
    reponse JSON propre, au lieu de la page HTML 500 de Django (ou d'un
    crash brut qui casse le JS front, lequel attend toujours du JSON).

    Ne touche PAS aux vues "page" classiques (login, inscription, espace
    client...) : elles gardent le comportement Django standard (page 500,
    ou page de debug si DEBUG=True), qui reste le plus utile pour un rendu
    HTML.

    Trois cas geres :
      - JSON malforme dans le corps de la requete (`json.loads` qui plante)
        -> 400, message clair, pas de trace dans les logs (frequent et
        sans gravite : simplement un appel mal forme).
      - Corps de requete trop volumineux (au-dela de
        DATA_UPLOAD_MAX_MEMORY_SIZE) -> 413.
      - Toute autre exception -> loggee avec la trace complete (utile pour
        deboguer), 500 générique renvoyé au client SANS la trace (qui
        pourrait révéler des détails internes en prod).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not request.path.startswith("/api/"):
            return None  # laisse Django gerer les vues "page" normalement

        import json as _json
        from django.conf import settings as _settings
        from django.core.exceptions import RequestDataTooBig
        from django.http import JsonResponse
        from .i18n import t
        from .ontologie import OntologiePasPrete

        if isinstance(exception, OntologiePasPrete):
            # L'ontologie se construit en arrière-plan et n'a encore aucun état : ce n'est pas une panne. Le JS de la page affiche son pop-up
            # (en-tête X-Ontologie) et réessaie tout seul ; les autres clients lisent « erreur ».
            reponse = JsonResponse({"erreur": str(exception), "ontologie_pas_prete": True}, status=503)
            reponse["X-Ontologie"] = "pas-prete"
            reponse["Retry-After"] = "5"
            return reponse

        if isinstance(exception, _json.JSONDecodeError):
            logger.info(t("engine/middleware.py:g:289:25"), request.path, exception)
            return JsonResponse({"erreur": t("engine/middleware.py:190:45")}, status=400)

        # Http404 / PermissionDenied sont des réponses NORMALES (objet d'une autre entreprise, ligne d'un
        # autre agent…) : les convertir en 500 faisait passer un refus d'accès pour une panne serveur.
        from django.core.exceptions import PermissionDenied
        from django.http import Http404
        from .securite_dates import DateRetrograde
        if isinstance(exception, DateRetrograde):   # course résiduelle : refusée au scellement du bloc
            logger.warning(_gt("rg6.msg.033"), request.path, exception)
            return JsonResponse({"erreur": str(exception), "code": "date_retrograde"}, status=409)
        if isinstance(exception, Http404):
            return JsonResponse({"erreur": t("engine/middleware.py:197:45")}, status=404)
        if isinstance(exception, PermissionDenied):
            return JsonResponse({"erreur": t("engine/middleware.py:199:45")}, status=403)

        if isinstance(exception, RequestDataTooBig):
            logger.info(t("engine/middleware.py:g:302:25"), request.path)
            return JsonResponse({"erreur": t("engine/middleware.py:203:45")}, status=413)

        logger.exception(t("engine/middleware.py:g:305:26"), request.path)
        if _settings.DEBUG:
            return None  # laisse la page de debug Django habituelle s'afficher
        return JsonResponse(
            {"erreur": t("engine/middleware.py:209:25")},
            status=500,
        )


class Erreur405Middleware:
    """Django répond lui-même au 405 (`require_GET`/`require_POST`…) par une réponse vide : on la remplace par la page d'erreur
    (JSON pour l'API, comme les autres codes). L'en-tête `Allow` est conservé."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reponse = self.get_response(request)
        from django.http import HttpResponseNotAllowed
        if reponse.status_code == 405 and isinstance(reponse, HttpResponseNotAllowed):
            from .views_erreur import reponse_erreur
            nouvelle = reponse_erreur(request, 405)
            if reponse.get("Allow"):
                nouvelle["Allow"] = reponse["Allow"]
            return nouvelle
        return reponse


class DateSecuriteMiddleware:
    """Sécurité des dates (voir securite_dates.py) : toute requête qui MODIFIE des données est refusée
    (409, avant la vue, donc sans rien écrire ni auditer) si l'horloge est revenue avant la dernière
    action enregistrée de l'entreprise. Doit rester AVANT AuditMiddleware : une requête refusée ne doit
    pas laisser, dans le journal, une ligne datée dans le passé."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.method in AuditMiddleware.METHODES and not request.path.startswith(AuditMiddleware.IGNORES)
                and not request.path.endswith(AuditMiddleware.IGNORES_FIN)
                and getattr(request, "user", None) is not None and request.user.is_authenticated):
            from django.http import JsonResponse
            from .models import MembreEntreprise
            from .securite_dates import DateRetrograde, controler
            from .securite import membre_de_requete
            _m = membre_de_requete(request, MembreEntreprise.objects.only("entreprise_id"))
            eid = _m.entreprise_id if _m else None
            if eid is not None:
                try:
                    controler(eid)
                except DateRetrograde as exc:
                    logger.warning(_gt("rg6.msg.033"), request.path, exc)
                    return JsonResponse({"erreur": str(exc), "code": "date_retrograde"}, status=409)
        return self.get_response(request)


class VuesAutoriseesMiddleware:
    """Les vues autorisées d'un agent (MembreEntreprise.vues_autorisees, réglées par l'admin dans Employés)
    n'étaient appliquées qu'à l'AFFICHAGE (onglets masqués, pages redirigées) : un agent privé de
    « Documents » pouvait toujours appeler /api/tables/<id>/documents/ directement. Ce middleware applique
    la même règle côté serveur à toutes les API propres à une vue (403 JSON). Admin, lecteur et agent sans
    restriction (vues_autorisees vide) : inchangés. « donnees » ne restreint que les ÉCRITURES de lignes
    (la lecture des lignes sert aussi aux autres onglets : calcul, documents, tableau de bord)."""

    import re as _re
    REGLES = [
        (_re.compile(r"^/api/tables/\d+/tableau-bord/"), "dashboard", None),
        (_re.compile(r"^/api/(tables/\d+/documents/|documents/)"), "documents", None),
        (_re.compile(r"^/api/(tables/\d+/calculs/|calculs/|lignes-calcul/)"), "calcul", None),
        (_re.compile(r"^/api/ia/"), "ia", None),
        (_re.compile(r"^/api/(configuration-documents|entete-officiel)/"), "configuration_documents", None),
        (_re.compile(r"^/api/documents-administratifs/"), "documents_administratifs", None),
        (_re.compile(r"^/api/(tables/\d+/tableaux-bord-bi/|tableaux-bord-bi/|widgets-bi/)"), "dashboard_bi", None),
        (_re.compile(r"^/api/tables/\d+/(lignes/|fusions/)"), "donnees", frozenset({"POST", "PUT", "PATCH", "DELETE"})),
        # ── Toutes les autres fonctionnalités : l'administrateur peut aussi les accorder ou les retirer à un agent ──
        # (pages ET API). Toujours ouverts : profil, langue, notifications, déconnexion, espace client, santé.
        (_re.compile(r"^/api/tables/\d+/(geolocalisation|config-carte|logistique)/|^/api/geolocalisation/|^/tables/\d+/geolocalisation/"), "geolocalisation", None),
        (_re.compile(r"^/api/tables/\d+/series-temporelles/|^/tables/\d+/series-temporelles/"), "series_temporelles", None),
        (_re.compile(r"^/api/tables/\d+/(statistiques|stats)/|^/tables/\d+/statistiques/"), "statistiques", None),
        (_re.compile(r"^/api/tables/\d+/analyse-media/|^/tables/\d+/analyse-media/"), "analyse_media", None),
        (_re.compile(r"^/api/tables/\d+/anova/|^/tables/\d+/anova/"), "anova", None),
        (_re.compile(r"^/api/tables/\d+/syntheses/|^/tables/\d+/syntheses/"), "syntheses", None),
        (_re.compile(r"^/api/tables/\d+/analyse-factorielle/|^/tables/\d+/analyse-factorielle/"), "analyse_factorielle", None),
        (_re.compile(r"^/api/(graphiques-bi|demandes)/|^/tables/\d+/dashboard-bi/"), "dashboard_bi", None),
        (_re.compile(r"^/api/(formules-calcul|transformations-colonne)/|^/api/tables/\d+/transformations/"), "calcul", None),
        (_re.compile(r"^/api/tables/\d+/skills/|^/api/skills/"), "skills", None),
        (_re.compile(r"^/api/tables/\d+/kpis/|^/api/kpis/"), "kpi", None),
        (_re.compile(r"^/api/tables/\d+/(exporter-tableur|exporter-pdf|rapport-general-pdf|resume-general-pdf|generer-cartes|generer-cartes-zip)/"), "exports", None),
        (_re.compile(r"^/api/(import|groupes-tables|conteneurs-tables|reprises-poste|limite-lignes|limite-colonnes|fusion)/"), "donnees", frozenset({"POST", "PUT", "PATCH", "DELETE"})),
        (_re.compile(r"^/(api/)?ontologie/"), "ontologie", None),
        (_re.compile(r"^/(api/)?blockchain/"), "blockchain", None),
        (_re.compile(r"^/(api/)?messagerie/"), "messagerie", None),
        (_re.compile(r"^/(api/)?publicites/"), "publicites", None),
        (_re.compile(r"^/(api/)?agenda/"), "agenda", None),
        (_re.compile(r"^/(api/)?chatbot/"), "chatbot", None),
        (_re.compile(r"^/(api/)?taches(-actives)?/"), "taches", None),
        (_re.compile(r"^/(api/)?sms/"), "sms", None),
        (_re.compile(r"^/(api/)?(recommandations|decision-strategique)/"), "recommandations", None),
        (_re.compile(r"^/(api/)?flux/"), "flux", None),
        (_re.compile(r"^/(paiement-config/|api/paiement/(?!webhook/))"), "paiements", None),
        (_re.compile(r"^/(wallets-clients/|api/wallet/)"), "wallet", None),
        (_re.compile(r"^/(finances/|api/finances/)"), "finances", None),
        (_re.compile(r"^/(api/)?partages-agents/"), "partages_agents", None),
        (_re.compile(r"^/api/(connexions-externes|cles-api|tokens)/"), "connexions_externes", None),
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Qui génère les documents pendant cette requête (signatures et cachet personnels de l'agent) ; remis à zéro à chaque requête.
        from .models import definir_utilisateur_document
        _u = getattr(request, "user", None)
        definir_utilisateur_document(_u.id if _u is not None and _u.is_authenticated else None)
        chemin = request.path
        if (not chemin.startswith(("/static/", "/media/")) and getattr(request, "user", None) is not None
                and request.user.is_authenticated):
            for motif, vue, methodes in self.REGLES:
                if motif.match(chemin) and (methodes is None or request.method in methodes):
                    from .models import MembreEntreprise
                    from .securite import membre_de_requete
                    membre = membre_de_requete(request, MembreEntreprise.objects.only("role", "vues_autorisees"))
                    if membre and membre.role == "agent" and membre.vues_autorisees and vue not in membre.vues_autorisees:
                        if not chemin.startswith("/api/"):
                            from django.shortcuts import redirect
                            return redirect("/profile/")      # page d'une fonctionnalité non accordée
                        from django.http import JsonResponse
                        from .i18n import t
                        return JsonResponse({"erreur": t("engine/middleware.py:246:57"),
                                             "vue": vue}, status=403)
                    break
        return self.get_response(request)


class CleAPIAuthMiddleware:
    """Authentifie les requêtes /api/... portant un en-tête
    `Authorization: Bearer <clé>` (voir models.CleAPI), pour permettre à une
    solution externe indépendante d'utiliser ce moteur SANS session
    navigateur — SANS dupliquer ni réécrire aucune vue existante.

    Principe : la clé incarne un MembreEntreprise réel. Quand elle est
    valide, la requête est authentifiée comme SON utilisateur Django
    (request.user = membre.utilisateur) et la vérification CSRF (pensée
    pour un navigateur, pas pour un appel serveur-à-serveur muni d'un
    secret) est désactivée pour cette requête. Tout le reste —
    login_required, _entreprise_courante, _interdit_lecteur,
    _interdit_non_proprietaire_tache, VuesAutoriseesMiddleware,
    JournalAudit... — s'applique alors EXACTEMENT comme pour un appel fait
    depuis le navigateur de ce membre, sans aucune modification des vues.

    Ne touche jamais une requête déjà authentifiée par session (le cookie
    du navigateur reste toujours prioritaire) ni une requête sans en-tête
    Authorization (comportement inchangé). Doit être placé APRÈS
    AuthenticationMiddleware (pour que request.user reflète déjà la
    session) et AVANT VuesAutoriseesMiddleware/AuditMiddleware (qui lisent
    request.user)."""

    PREFIXE_ENTETE = "Bearer "

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.path.startswith("/api/")
                and getattr(request, "user", None) is not None
                and not request.user.is_authenticated):
            entete = request.META.get("HTTP_AUTHORIZATION", "")
            if entete.startswith(self.PREFIXE_ENTETE):
                from .models import CleAPI
                from .i18n import t
                from .views_erreur import reponse_erreur

                secret = entete[len(self.PREFIXE_ENTETE):].strip()
                cle = CleAPI.authentifier(secret)
                if cle is None:
                    return reponse_erreur(request, 401, message=t("engine/middleware.py:293:53"))
                if not cle.membre.utilisateur_id:
                    return reponse_erreur(request, 401, message=t("engine/middleware.py:295:53"))
                if cle.membre.entreprise_id != cle.entreprise_id:   # isolation atomique : une clé n'incarne JAMAIS un membre d'une autre entreprise que la sienne
                    return reponse_erreur(request, 401, message=t("engine/middleware.py:293:53"))

                if not cle.entreprise.api_ouverte:   # l'admin a fermé l'accès API : refus net, la clé n'est pas consommée
                    return reponse_erreur(request, 403, message=t("engine/middleware.py:api_fermee"))

                # Verrou SIEM : SiemMiddleware passe AVANT ce middleware, où request.user est encore anonyme pour une requête à clé API ;
                # sans ce contrôle, un compte verrouillé gardait ses clés actives. Refus avant toute consommation de la clé.
                try:
                    from . import siem_noyau as _siem
                    verrouille = _siem.compte_verrouille(cle.membre.utilisateur_id)
                except Exception:   # le SIEM ne fait jamais échouer une requête sur sa propre panne
                    verrouille = False
                if verrouille:
                    return reponse_erreur(request, 403, message=t("engine/middleware.py:api_compte_verrouille"))

                request.user = cle.membre.utilisateur
                request._dont_enforce_csrf_checks = True
                request._cle_api = cle
                from django.utils import timezone
                CleAPI.objects.filter(pk=cle.pk).update(derniere_utilisation=timezone.now())   # USE_TZ=True : datetime « aware » (datetime.now() était naïf)
        return self.get_response(request)


class AuditMiddleware:
    """Traçabilité d'entreprise (audit 2026-09-23) : toute requête qui MODIFIE des données
    (POST/PUT/PATCH/DELETE) par un utilisateur connecté est inscrite au JournalAudit de son entreprise :
    qui, quelle adresse, quelle action (méthode + chemin), résultat (code HTTP), depuis quelle IP.
    Jamais le contenu de la requête (mots de passe, données personnelles). Un échec d'écriture du
    journal ne bloque jamais la requête (best effort, journalisé)."""

    METHODES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
    IGNORES = ("/sante/", "/static/", "/media/", "/api/usabilite/")  # mesures d'usage : pas des modifications métier
    IGNORES_FIN = ("/geolocalisation/recevoir/",)   # trames d'un tracker distant (une requête par seconde et par tracker) : ni modification métier, ni ligne d'audit par trame

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reponse = self.get_response(request)
        if (request.method in self.METHODES and not request.path.startswith(self.IGNORES) and not request.path.endswith(self.IGNORES_FIN)
                and getattr(request, "user", None) is not None and request.user.is_authenticated):
            try:
                from .models import JournalAudit, MembreEntreprise
                from .securite import membre_de_requete
                membre = membre_de_requete(request, MembreEntreprise.objects.only("entreprise_id", "role"))
                if membre is not None:
                    from .ratelimit import adresse_ip_client
                    ip = adresse_ip_client(request)
                    # Phase 13 (audit) : type_acteur distingue une clé API
                    # (voir CleAuthentificationMiddleware, request._cle_api)
                    # d'une session navigateur classique.
                    JournalAudit.enregistrer(
                        entreprise=membre.entreprise_id, utilisateur=request.user,
                        action=f"{request.method} {request.path}"[:255],
                        details={"statut": reponse.status_code, "role": membre.role, "ip": ip},
                        type_acteur="cle_api" if getattr(request, "_cle_api", None) else "utilisateur",
                        source="api" if request.path.startswith("/api/") else "web",
                        resultat="succes" if reponse.status_code < 400 else "echec", ip=ip,
                    )
            except Exception:
                logger.exception(t("engine/middleware.py:g:441:34"), request.path)
        return reponse



class OntologieEtatMiddleware:
    """Ontologie vivante : toute requête qui MODIFIE des données (POST/PUT/PATCH/DELETE) signale la modification à l'état de l'ontologie, qui
    se met à jour tout de suite et en priorité en arrière-plan (plusieurs modifications simultanées = un seul passage). La requête n'attend
    jamais : le signal n'est qu'un compteur et un réveil. Une réponse 5xx est signalée aussi (l'écriture a pu aboutir à moitié). Sans le module
    d'ontologie (dépendance absente), le middleware ne fait rien et ne gêne rien."""

    METHODES = AuditMiddleware.METHODES
    IGNORES = AuditMiddleware.IGNORES
    IGNORES_FIN = AuditMiddleware.IGNORES_FIN

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reponse = self.get_response(request)
        if (request.method in self.METHODES and not request.path.startswith(self.IGNORES) and not request.path.endswith(self.IGNORES_FIN)
                and (reponse.status_code < 400 or reponse.status_code >= 500)):
            try:
                from .ontologie import signaler_modification
                signaler_modification()
            except Exception:
                logger.debug("signal de modification de l'ontologie indisponible", exc_info=True)
        return reponse


class UsabiliteMiddleware:
    """Injecte static/engine/js/usabilite.js (mesure d'utilisabilité, engine/usabilite.py) dans toute page
    HTML servie à un utilisateur connecté — un seul point au lieu de modifier chaque gabarit. Désactivable
    par GYPLATFORM_USABILITE=0. Jamais sur l'administration Django ni sur une réponse non HTML."""

    def __init__(self, get_response):
        import os
        self.get_response = get_response
        self.actif = os.environ.get("GYPLATFORM_USABILITE", "1") != "0"

    def __call__(self, request):
        reponse = self.get_response(request)
        if (self.actif and reponse.status_code == 200 and not getattr(reponse, "streaming", False)
                and reponse.get("Content-Type", "").startswith("text/html")
                and not request.path.startswith("/admin/")
                and getattr(request, "user", None) is not None and request.user.is_authenticated):
            from django.templatetags.static import static
            balise = f'<script src="{static("engine/js/usabilite.js")}" defer></script></body>'.encode()
            contenu = reponse.content
            i = contenu.rfind(b"</body>")
            if i != -1:
                reponse.content = contenu[:i] + balise + contenu[i + len(b"</body>"):]
                if reponse.has_header("Content-Length"):
                    reponse["Content-Length"] = str(len(reponse.content))
        return reponse


class ChargementMiddleware:
    """Injecte static/engine/js/chargement_global.js (cercle animé + bouton Annuler pendant tout chargement : requêtes et navigations) dans
    toute page HTML, connectée ou non — un seul point au lieu de modifier chaque gabarit. Les deux textes (déjà traduits dans la langue de la
    requête) passent en attributs data-* de la balise. Désactivable par GYPLATFORM_CHARGEMENT=0. Jamais sur l'administration Django."""

    def __init__(self, get_response):
        import os
        self.get_response = get_response
        self.actif = os.environ.get("GYPLATFORM_CHARGEMENT", "1") != "0"

    def __call__(self, request):
        reponse = self.get_response(request)
        if (self.actif and reponse.status_code == 200 and not getattr(reponse, "streaming", False)
                and reponse.get("Content-Type", "").startswith("text/html") and not request.path.startswith("/admin/")):
            contenu = reponse.content
            import re   # GYCHARG-TETE : balise en tête de <head> (cercle dès le chargement) ; repli avant </body>
            i = contenu.rfind(b"</body>")
            tete = re.search(rb"<head(?:\s[^>]*)?>", contenu[:30000], re.I)
            if i != -1 or tete:
                from html import escape
                from django.templatetags.static import static
                attr_intro = ' data-intro="1"' if b'id="intro-overlay"' in contenu else ""
                balise = (_gtf("engine/middleware.py:600:27", v1=(static("engine/js/chargement_global.js")), v2=("" if tete else " defer"), v3=(escape(t("engine/static/engine/js/chargement_global.js:chargement"))), v4=(escape(t("engine/static/engine/js/chargement_global.js:annuler"))), v5=(attr_intro))).encode()
                if tete:
                    reponse.content = contenu[:tete.end()] + balise + contenu[tete.end():]
                else:
                    reponse.content = contenu[:i] + balise + contenu[i:]
                if reponse.has_header("Content-Length"):
                    reponse["Content-Length"] = str(len(reponse.content))
        return reponse


class CaptureMediaMiddleware:
    """Messagerie : son enregistré VISIBLE (barre rouge, durée, niveau) et caméra directe (photo / vidéo) au lieu du sélecteur de fichiers.
    Injecte static/engine/js/capture_messagerie.js dans la page qui contient les boutons de la messagerie (#msg-camera) — un seul point,
    aucun gabarit modifié. Sans API caméra/micro (page non sécurisée, ancien navigateur) ou accès refusé, le script retombe sur le champ
    fichier d'origine. Textes déjà traduits (t()) transmis en JSON dans data-textes. Désactivable par GYPLATFORM_CAPTURE_MEDIA=0."""
    IDS = ("enregistrement", "envoyer", "annuler", "micro_refuse", "camera", "photo", "video", "basculer", "utiliser", "reprendre",
           "fichier", "fermer", "capturer", "demarrer", "arreter", "camera_refuse", "repli", "erreur")

    def __init__(self, get_response):
        import os
        self.get_response = get_response
        self.actif = os.environ.get("GYPLATFORM_CAPTURE_MEDIA", "1") != "0"

    def __call__(self, request):
        reponse = self.get_response(request)
        if (self.actif and reponse.status_code == 200 and not getattr(reponse, "streaming", False)
                and reponse.get("Content-Type", "").startswith("text/html") and not request.path.startswith("/admin/")):
            contenu = reponse.content
            i = contenu.rfind(b"</body>")
            if i != -1 and b'id="msg-camera"' in contenu:
                import json
                from html import escape
                from django.templatetags.static import static
                textes = json.dumps({k: t("engine/static/engine/js/capture_messagerie.js:" + k) for k in self.IDS}, ensure_ascii=False)
                balise = f'<script src="{static("engine/js/capture_messagerie.js")}" data-textes="{escape(textes)}" defer></script>'.encode()
                reponse.content = contenu[:i] + balise + contenu[i:]
                if reponse.has_header("Content-Length"):
                    reponse["Content-Length"] = str(len(reponse.content))
        return reponse


class SaisonMiddleware:
    """Saisons restaurées (cycle.restaurer_en_saison) : (1) toute modification est refusée (409, avant la vue) tant que la saison restaurée
    active est en lecture seule (Entreprise.ecriture_ouverte) ; (2) injecte static/engine/js/saisons.js, le bandeau permanent + sélecteur de saison,
    dans toute page HTML d'un utilisateur ayant plusieurs environnements. Sans saison restaurée : aucun effet."""
    LIBRES = ("/api/saisons/", "/api/entreprise/cloturer/", "/api/usabilite/", "/logout/", "/api/langue/", "/static/", "/media/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or request.path.startswith("/admin/"):
            return self.get_response(request)
        from .models import MembreEntreprise
        if MembreEntreprise.objects.filter(utilisateur=user).count() < 2:
            return self.get_response(request)
        actif = MembreEntreprise.objects.filter(utilisateur=user).select_related("entreprise").first().entreprise
        if (actif.saison_de_id and not actif.ecriture_ouverte and request.method in AuditMiddleware.METHODES and not request.path.startswith(self.LIBRES)):
            from django.http import JsonResponse
            return JsonResponse({"erreur": _gt("rg5.msg.024"), "code": "saison_lecture_seule"}, status=409)
        reponse = self.get_response(request)
        if reponse.status_code == 200 and not getattr(reponse, "streaming", False) and reponse.get("Content-Type", "").startswith("text/html"):
            from django.templatetags.static import static
            i = reponse.content.rfind(b"</body>")
            if i != -1:
                reponse.content = reponse.content[:i] + f'<script src="{static("engine/js/saisons.js")}" defer></script>'.encode() + reponse.content[i:]
                if reponse.has_header("Content-Length"):
                    reponse["Content-Length"] = str(len(reponse.content))
        return reponse


class FichiersMiddleware:
    """Contrôle TOUT fichier téléversé (securite.verifier_fichier) avant la vue : messagerie, logo, photos, imports, formulaires… un seul point.
    Exemptés : archives de restauration et scripts de l'administrateur (du code par nature). Les vues de lignes recontrôlent avec le type exact du champ."""
    EXEMPTES = {"api_restaurer_cycle", "api_geo_tuiles_restaurer", "api_restaurer_image_auto", "api_importer_script_tache"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method not in ("POST", "PUT", "PATCH") or not (request.content_type or "").startswith("multipart/"):
            return None
        if getattr(request.resolver_match, "url_name", None) in self.EXEMPTES:
            return None
        from django.http import JsonResponse
        from .securite import verifier_fichier
        for cle, f in request.FILES.items():
            refus = verifier_fichier(f)
            if refus:
                return JsonResponse({"erreur": refus, "champs": {cle: refus}}, status=400)
        return None
