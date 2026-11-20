"""SQLite : UN FICHIER PAR ENTREPRISE (mode optionnel GYPLATFORM_BASE=sqlite_par_entreprise).

Principe
  - La base « default » (media/systeme/db.sqlite3) reste la base SYSTÈME : comptes, sessions, entreprises, membres, clés API,
    SIEM, partages inter-agents, et tout modèle qui référence un utilisateur.
  - Chaque entreprise a sa propre base media/systeme/entreprises/<id>.sqlite3 pour ses données (tables, enregistrements,
    graphiques, ontologie, documents…) : tout modèle de l'app « engine » rattaché à une entreprise (par un champ `entreprise`
    ou par une chaîne de clés étrangères) et qui ne référence aucun utilisateur.
  - L'entreprise courante est portée par une variable de contexte (contextvars) : posée par le middleware (requêtes web), par
    `_entreprise_courante` et par `dans_entreprise()` ; les threads et pools de threads la reçoivent à leur création.
  - Les clés étrangères entre fichiers ne sont pas contrôlées par SQLite (PRAGMA foreign_keys=OFF sur les bases d'entreprise) ;
    Django émule déjà les suppressions en cascade côté Python.
  - Une copie de la ligne Entreprise est tenue dans chaque fichier pour que les jointures `…__entreprise` continuent de marcher.

Hors de ce mode (SQLite simple ou PostgreSQL), rien de ce module n'agit : ACTIF vaut False et le routeur répond « pas d'avis ».
"""
import contextlib
import contextvars
import logging
import threading
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("engine.base_entreprise")

PREFIXE = "ent_"
_courante = contextvars.ContextVar("gyplatform_entreprise_courante", default=None)
_verrou = threading.RLock()
_pretes = set()          # alias déjà créés/migrés dans ce processus
_en_preparation = set()  # alias en cours de migration (évite la récursion du routeur)
_cache_locataire = {}    # modèle -> bool

# Modèles toujours dans la base système, même s'ils portent un champ `entreprise`.
SYSTEME = {
    "Entreprise", "MembreEntreprise", "PartageInterAgent",            # identité et rattachements
    "CleAPI", "JournalAudit", "EvaluationSUS", "MesureUsage", "LangueUtilisateur",   # lus AVANT de connaître l'entreprise, ou globaux
    # argent et paiements : niveau plateforme (surveillance des dépôts et tâches de fond sans requête)
    "Wallet", "ChargeWallet", "EcritureWallet", "FraisWallet", "RetraitWallet", "MoyenRetrait", "RemunerationAgent",
    "DepotPaiement", "FournisseurPaiement", "EvenementPrestataire", "AbonnementMensuel", "CompteurTokens", "TauxChange",
    "ModemSms", "PrefixeOperateur",
}
# Modèles système dont les lignes de l'entreprise sont AUSSI copiées dans son fichier (lecture seule, pour les jointures).
COPIES = ("Entreprise", "MembreEntreprise")
DECALAGE_IDENTIFIANTS = 10 ** 9   # les nouvelles lignes d'une entreprise commencent à id_entreprise × 10⁹ : plus de collision d'id entre fichiers


def actif() -> bool:
    return getattr(settings, "GYPLATFORM_BASE_MOTEUR", "") == "sqlite_par_entreprise"


def dossier() -> Path:
    d = Path(getattr(settings, "GYPLATFORM_DOSSIER_ENTREPRISES", Path(settings.MEDIA_ROOT) / "systeme" / "entreprises"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def alias(entreprise_id) -> str:
    return f"{PREFIXE}{int(entreprise_id)}"


def chemin(entreprise_id) -> Path:
    return dossier() / f"{int(entreprise_id)}.sqlite3"


def courante():
    return _courante.get()


def definir(entreprise_id):
    """Pose l'entreprise courante pour le reste du contexte (requête ou thread) ; None = base système seule."""
    if not actif():
        return None
    _courante.set(int(entreprise_id) if entreprise_id else None)
    return _courante.get()


@contextlib.contextmanager
def dans_entreprise(entreprise_id):
    """Exécute le bloc sur la base d'une entreprise précise (tâches de fond, commandes, lecture d'un partage)."""
    jeton = _courante.set(int(entreprise_id) if (actif() and entreprise_id) else None)
    try:
        yield
    finally:
        _courante.reset(jeton)


def est_locataire(modele) -> bool:
    """True si les lignes de ce modèle vivent dans la base de l'entreprise."""
    if modele in _cache_locataire:
        return _cache_locataire[modele]
    _cache_locataire[modele] = False   # garde-fou contre les cycles de clés étrangères
    resultat = _calculer_locataire(modele)
    _cache_locataire[modele] = resultat
    return resultat


def _calculer_locataire(modele, vus=()) -> bool:
    meta = modele._meta
    if meta.app_label != "engine" or meta.proxy or modele.__name__ in SYSTEME:
        return False
    cles = [f for f in meta.get_fields() if getattr(f, "many_to_one", False) or getattr(f, "one_to_one", False)]
    cles = [f for f in cles if f.concrete and f.related_model is not None]
    # Un lien vers un compte (cree_par, auteur…) n'exclut PAS un modèle : une copie sans mot de passe des comptes de l'entreprise
    # est tenue dans son fichier (voir copier_entreprise), les jointures vers l'utilisateur marchent.
    for f in cles:
        if f.related_model.__name__ == "Entreprise":
            return True
    for f in cles:
        autre = f.related_model
        if autre is not modele and autre not in vus and autre._meta.app_label == "engine" and autre.__name__ not in SYSTEME:
            if _calculer_locataire(autre, vus + (modele,)):
                return True
    return False


def modeles_locataires():
    from django.apps import apps
    return [m for m in apps.get_app_config("engine").get_models(include_auto_created=True) if est_locataire(m)]


def enregistrer(entreprise_id) -> str:
    """Déclare la connexion de l'entreprise (idempotent) et retourne son alias."""
    from django.db import connections
    nom = alias(entreprise_id)
    if nom in connections.databases:
        return nom
    with _verrou:
        if nom not in connections.databases:
            base = dict(settings.DATABASES["default"])
            base["NAME"] = str(chemin(entreprise_id))
            base["CONN_MAX_AGE"] = 0
            connections.databases[nom] = base
            settings.DATABASES[nom] = base
    return nom


def preparer(entreprise_id) -> str:
    """Crée le fichier de l'entreprise s'il n'existe pas, le met à jour (migrations en attente) et retourne son alias."""
    nom = enregistrer(entreprise_id)
    if nom in _pretes or nom in _en_preparation:
        return nom
    with _verrou:
        if nom in _pretes:
            return nom
        _en_preparation.add(nom)
        try:
            from django.core.management import call_command
            from django.db import connections
            from django.db.migrations.executor import MigrationExecutor
            executeur = MigrationExecutor(connections[nom])
            if executeur.migration_plan(executeur.loader.graph.leaf_nodes()):   # fichier neuf OU migrations de code en attente
                call_command("migrate", database=nom, verbosity=0, interactive=False)
            decaler_identifiants(entreprise_id)
            copier_entreprise(entreprise_id)
            _pretes.add(nom)
        finally:
            _en_preparation.discard(nom)
    return nom


def decaler_identifiants(entreprise_id):
    """Fait démarrer les identifiants automatiques de ce fichier à id_entreprise × 10⁹ (tables encore jamais utilisées)."""
    from django.db import connections
    nom = alias(entreprise_id)
    depart = int(entreprise_id) * DECALAGE_IDENTIFIANTS
    with connections[nom].cursor() as c:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
        if not c.fetchone():
            return
        for m in modeles_locataires():
            pk = m._meta.pk
            if not pk.get_internal_type() in ("AutoField", "BigAutoField", "SmallAutoField"):
                continue
            table = m._meta.db_table
            c.execute("SELECT seq FROM sqlite_sequence WHERE name=%s", [table])
            ligne = c.fetchone()
            if ligne is None:
                c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (%s, %s)", [table, depart])
            elif ligne[0] < depart:
                c.execute("UPDATE sqlite_sequence SET seq=%s WHERE name=%s", [depart, table])


def copier_entreprise(entreprise_id):
    """Copie (ou met à jour) dans le fichier de l'entreprise sa ligne Entreprise et ses membres, pour les jointures."""
    from .models import Entreprise, MembreEntreprise
    nom = alias(entreprise_id)
    ent = Entreprise.objects.using("default").filter(pk=entreprise_id).first()
    if ent is None:
        return
    existe = Entreprise.objects.using(nom).filter(pk=ent.pk).exists()
    ent.save(using=nom, force_insert=not existe, force_update=existe)
    MembreEntreprise.objects.using(nom).all().delete()
    membres = list(MembreEntreprise.objects.using("default").filter(entreprise_id=entreprise_id))
    MembreEntreprise.objects.using(nom).bulk_create(membres)
    from django.contrib.auth import get_user_model
    Utilisateur = get_user_model()
    comptes = list(Utilisateur.objects.using("default").filter(pk__in=[m.utilisateur_id for m in membres]))
    Utilisateur.objects.using(nom).filter(pk__in=[u.pk for u in comptes]).delete()
    for u in comptes:
        u.password = "!"   # jamais de mot de passe ni de jeton dans un fichier d'entreprise : copie pour les jointures seulement
    Utilisateur.objects.using(nom).bulk_create(comptes)


class RouteurEntreprise:
    """Routeur de bases : données d'entreprise → fichier de l'entreprise courante, le reste → base système."""

    def _vers(self, modele):
        if not actif() or not est_locataire(modele):
            return None
        eid = _courante.get()
        if not eid:
            return None   # contexte inconnu (commande, démarrage) : base système, jamais un fichier au hasard
        return preparer(eid)

    def db_for_read(self, modele, **hints):
        return self._vers(modele)

    def db_for_write(self, modele, **hints):
        return self._vers(modele)

    def allow_relation(self, a, b, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if not actif() or not db.startswith(PREFIXE):
            return None   # base système : comportement habituel (elle garde aussi les tables d'avant la découpe)
        if not model_name:
            return False
        if f"{app_label}.{model_name}" == settings.AUTH_USER_MODEL.lower():
            return True   # table des comptes : copie sans mot de passe, pour les jointures
        if app_label != "engine":
            return False
        from django.apps import apps
        try:
            modele = apps.get_model(app_label, model_name)
        except LookupError:
            return False
        return est_locataire(modele) or modele.__name__ in COPIES or f"{app_label}.{model_name}" == settings.AUTH_USER_MODEL.lower()


def _regler_connexion(sender, connection, **kw):
    """Bases d'entreprise : clés étrangères libres entre fichiers, WAL, attente en cas de verrou."""
    if not connection.alias.startswith(PREFIXE):
        return
    with connection.cursor() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")


def _rafraichir_copie(sender, instance, using=None, **kw):
    """Entreprise ou membre modifié/supprimé : la copie tenue dans le fichier de l'entreprise suit."""
    if (using or "default") != "default":
        return
    eid = instance.pk if sender.__name__ == "Entreprise" else getattr(instance, "entreprise_id", None)
    if eid and alias(eid) in _pretes:
        try:
            copier_entreprise(eid)
        except Exception:
            logger.exception("copie d'entreprise non rafraîchie")


def _rafraichir_comptes(sender, instance, using=None, **kw):
    if (using or "default") != "default":
        return
    from .models import MembreEntreprise
    for eid in set(MembreEntreprise.objects.using("default").filter(utilisateur_id=instance.pk).values_list("entreprise_id", flat=True)):
        if alias(eid) in _pretes:
            try:
                copier_entreprise(eid)
            except Exception:
                logger.exception("copie de compte non rafraîchie")


def installer():
    """Appelé au démarrage (apps.ready) : réglage des connexions + héritage du contexte par les threads."""
    if not actif():
        return
    from django.db.backends.signals import connection_created
    connection_created.connect(_regler_connexion, dispatch_uid="gy_base_entreprise_connexion")
    from django.db.models.signals import post_save
    from .models import Entreprise
    from django.db.models.signals import post_delete
    from .models import MembreEntreprise
    from django.contrib.auth import get_user_model
    post_save.connect(_rafraichir_comptes, sender=get_user_model(), dispatch_uid="gy_base_entreprise_comptes")
    for modele in (Entreprise, MembreEntreprise):
        post_save.connect(_rafraichir_copie, sender=modele, dispatch_uid=f"gy_base_entreprise_copie_{modele.__name__}")
        post_delete.connect(_rafraichir_copie, sender=modele, dispatch_uid=f"gy_base_entreprise_copie_suppr_{modele.__name__}")
    _propager_contexte()


def _propager_contexte():
    """Un Thread / une tâche de pool part avec le contexte (entreprise courante) de celui qui le lance."""
    import concurrent.futures
    if getattr(threading.Thread, "_gy_contexte", False):
        return
    start_origine = threading.Thread.start

    def start(self, *a, **k):
        ctx = contextvars.copy_context()
        run_origine = self.run

        def run_dans_contexte():
            return ctx.run(run_origine)
        self.run = run_dans_contexte
        return start_origine(self, *a, **k)
    threading.Thread.start = start
    threading.Thread._gy_contexte = True

    submit_origine = concurrent.futures.ThreadPoolExecutor.submit

    def submit(self, fn, /, *a, **k):
        ctx = contextvars.copy_context()
        return submit_origine(self, ctx.run, fn, *a, **k)
    concurrent.futures.ThreadPoolExecutor.submit = submit


class BaseEntrepriseMiddleware:
    """Pose l'entreprise de la requête (clé API ou entreprise choisie en session) avant toute vue."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not actif():
            return self.get_response(request)
        jeton = _courante.set(self._entreprise(request))
        try:
            return self.get_response(request)
        finally:
            _courante.reset(jeton)

    @staticmethod
    def _entreprise(request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        try:
            from .securite import membre_de_requete
            membre = membre_de_requete(request)
            return membre.entreprise_id if membre is not None else None
        except Exception:
            logger.exception("entreprise de la requête illisible")
            return None
