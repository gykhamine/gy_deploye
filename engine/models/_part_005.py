from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tl
from engine.i18n import tf
from engine.i18n import t
from ._header import *  # noqa
from engine.montant_champs import MontantField, TauxField  # noqa
from .. import securite_dates as _securite_dates

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from ._part_004 import *  # noqa
from engine.i18n import td as _gtd  # noqa: E402
from engine.i18n import devise_affichage as _dev_aff  # noqa: E402


class MesureWidget(models.Model):
    """Colonne virtuelle calculée (style Power BI) attachée à un
    WidgetGraphiqueBI — voir engine/mesures.py (calculer_mesures) et
    tableau_bord_bi._colonnes_widget. Lue via widget.mesures."""
    widget = models.ForeignKey(WidgetGraphiqueBI, on_delete=models.CASCADE, related_name="mesures")
    nom = models.CharField(max_length=150)
    champ_cible = models.ForeignKey(
        ChampDynamique, on_delete=models.CASCADE, related_name="+",
        help_text=td("engine/models/_part_005.py:g:21:19"),
    )
    formule = models.ForeignKey(
        FormuleCalcul, on_delete=models.PROTECT, related_name="+",
        help_text=tl("engine/models/_part_005.py:23:20"),
    )
    ordre = models.PositiveIntegerField(default=0)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = t("engine/models/_part_005.py:30:25")

    def __str__(self):
        return f"{self.widget} / {self.nom}"


# ─────────────────────────────────────────────────────────────
# Indicateurs KPI : définis sur une table source, réutilisent la
# bibliothèque de FormuleCalcul (cible_type="colonne", ex: Somme,
# Moyenne, COUNT...) pour calculer une valeur scalaire, avec un objectif
# et des seuils optionnels, et un historique de points dans le temps
# (voir kpi.py) pour tracer une tendance — même mécanique de cache que
# WidgetGraphiqueBI (valeur_actuelle recalculée seulement si l'empreinte
# des données de la table source a changé).
# ─────────────────────────────────────────────────────────────
class IndicateurKPI(models.Model):
    """Un indicateur clé de performance suivi sur une TableDynamique."""
    SENS_CHOICES = [
        ("hausse", td("engine/models/_part_005.py:g:51:20")),
        ("baisse", td("engine/models/_part_005.py:g:52:20")),
        ("cible", td("engine/models/_part_005.py:g:53:19")),
    ]
    STATUT_CHOICES = [
        ("en_attente", tl("engine/models/_part_005.py:g:55:24")), ("pret", tl("engine/models/_part_005.py:g:55:48")), ("echec", tl("engine/models/_part_005.py:g:55:67")),
    ]

    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="kpis"
    )
    table_source = models.ForeignKey(
        TableDynamique, on_delete=models.CASCADE, related_name="kpis"
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    champ_cible = models.ForeignKey(
        ChampDynamique, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text=tl("engine/models/_part_005.py:66:20"),
    )
    formule = models.ForeignKey(
        FormuleCalcul, on_delete=models.PROTECT, related_name="+",
        help_text=td("engine/models/_part_005.py:g:73:19"),
    )
    # Formules enchaînées AVANT l'agrégation (comme la gestion des données) : [{"operateur", "operandes": [colonne|r1|r2…]}] ;
    # l'étape n produit la variable « rn » ligne par ligne, la formule d'agrégation s'applique à la dernière.
    etapes = models.JSONField(default=list, blank=True)
    unite = models.CharField(max_length=30, blank=True, default="", help_text=tl("engine/models/_part_005.py:73:80"))
    valeur_cible = models.FloatField(
        null=True, blank=True, help_text=tl("engine/models/_part_005.py:75:43"),
    )
    seuil_alerte = models.FloatField(
        null=True, blank=True,
        help_text=td("engine/models/_part_005.py:g:85:19"),
    )
    sens_objectif = models.CharField(max_length=10, choices=SENS_CHOICES, default="hausse")
    ordre = models.PositiveIntegerField(default=0)
    visible_client = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_005.py:86:23"),
        help_text=tl("engine/models/_part_005.py:87:20"),
    )

    # ── Cache de la valeur courante (même principe que WidgetGraphiqueBI) ──
    statut = models.CharField(max_length=15, choices=STATUT_CHOICES, default="en_attente")
    valeur_actuelle = models.FloatField(null=True, blank=True)
    erreur = models.TextField(blank=True, default="")
    empreinte_donnees = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text=tl("engine/models/_part_005.py:96:20"),
    )
    calcule_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    # Dernier `etat` ("alerte"/"danger") ayant déclenché une Notification —
    # évite de renotifier à chaque recalcul tant que l'état ne change pas
    # (voir kpi.calculer_kpi + notifications.notifier_kpi_alerte).
    dernier_etat_alerte = models.CharField(max_length=10, blank=True, default="")
    # True une fois la Notification « objectif atteint » envoyée ; remis à False dès que le KPI quitte l'état « succes »
    # (une future atteinte renotifiera). Champ distinct de dernier_etat_alerte : celui-ci alimente aussi les données
    # d'entraînement IA (fusion.py -> donnees_entrainement._feat_kpi : toute valeur non vide y compte comme « alerte »).
    objectif_notifie = models.BooleanField(default=False)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = t("engine/models/_part_005.py:107:25")

    def __str__(self):
        return f"{self.table_source.nom} / {self.nom}"

    def kpi_a_jour(self, empreinte_donnees_actuelle: str) -> bool:
        return bool(
            self.statut == "pret"
            and self.empreinte_donnees
            and self.empreinte_donnees == empreinte_donnees_actuelle
        )

    @property
    def etat(self):
        """'succes' / 'alerte' / 'danger' / None (pas assez d'info pour juger) —
        compare valeur_actuelle à valeur_cible/seuil_alerte selon sens_objectif.
        Sert uniquement à l'affichage (couleur du badge côté front)."""
        if self.statut != "pret" or self.valeur_actuelle is None or self.valeur_cible is None:
            return None
        v, cible = self.valeur_actuelle, self.valeur_cible
        if self.sens_objectif == "cible":
            ecart = abs(v - cible)
            reference = self.seuil_alerte if self.seuil_alerte is not None else abs(cible) * 0.1 or 1
            if ecart <= reference * 0.25:
                return "succes"
            if ecart <= reference:
                return "alerte"
            return "danger"
        atteint = v >= cible if self.sens_objectif == "hausse" else v <= cible
        if atteint:
            return "succes"
        if self.seuil_alerte is not None:
            en_alerte = v >= self.seuil_alerte if self.sens_objectif == "hausse" else v <= self.seuil_alerte
            if en_alerte:
                return "alerte"
        return "danger"

    @property
    def variation_pct(self):
        """Variation en % vs le point d'historique précédent (tendance)."""
        if self.statut != "pret" or self.valeur_actuelle is None:
            return None
        points = list(self.historique.order_by("-calcule_le")[:2])
        if len(points) < 2 or not points[1].valeur:
            return None
        return round((points[0].valeur - points[1].valeur) / abs(points[1].valeur) * 100, 1)


class ValeurKPIHistorique(models.Model):
    """Point d'historique d'un IndicateurKPI, ajouté à chaque recalcul dont
    la valeur diffère du dernier point enregistré (voir kpi.calculer_kpi) —
    sert à tracer la tendance du KPI dans le temps."""
    kpi = models.ForeignKey(IndicateurKPI, on_delete=models.CASCADE, related_name="historique")
    valeur = models.FloatField()
    calcule_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["calcule_le"]
        verbose_name = t("engine/models/_part_005.py:165:25")

    def __str__(self):
        return f"{self.kpi_id} @ {self.calcule_le:%Y-%m-%d %H:%M} = {self.valeur}"


# ── Compteur de tokens IA ──────────────────────────────────────────────────
class CompteurTokens(models.Model):
    """Compteur persistant de tokens LLM par entreprise (+ optionnellement par user).
    Se réinitialise automatiquement selon la période configurée."""

    PERIODES = [
        ('heure',   td("engine/models/_part_005.py:g:187:21")),
        ('jour',    td("engine/models/_part_005.py:g:188:21")),
        ('semaine', td("engine/models/_part_005.py:g:189:21")),
        ('mois',    td("engine/models/_part_005.py:g:190:21")),
    ]

    entreprise    = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name='compteurs_tokens')
    utilisateur   = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.CASCADE, related_name='compteurs_tokens',
        verbose_name=tl("engine/models/_part_005.py:187:23")
    )
    tokens_total  = models.BigIntegerField(default=0, verbose_name=tl("engine/models/_part_005.py:189:69"))
    tokens_entree = models.BigIntegerField(default=0, verbose_name=tl("engine/models/_part_005.py:190:69"))
    tokens_sortie = models.BigIntegerField(default=0, verbose_name=tl("engine/models/_part_005.py:191:69"))
    periode       = models.CharField(max_length=10, choices=PERIODES, default='mois',
                                     verbose_name=tl("engine/models/_part_005.py:193:52"))
    debut_periode = models.DateTimeField(auto_now_add=True, verbose_name=tl("engine/models/_part_005.py:194:75"))
    alerte_seuil  = models.BigIntegerField(default=0, verbose_name=tl("engine/models/_part_005.py:195:69"))
    alerte_active = models.BooleanField(default=False, verbose_name=tl("engine/models/_part_005.py:196:70"))

    class Meta:
        unique_together = [('entreprise', 'utilisateur')]
        verbose_name = t("engine/models/_part_005.py:200:25")

    def __str__(self):
        who = self.utilisateur.username if self.utilisateur else 'global'
        return f"{self.entreprise.nom} / {who} — {self.tokens_total} tokens ({self.periode})"

    # ── Helpers ────────────────────────────────────────────────────────────
    def _debut_periode_courante(self):
        """Retourne le datetime de début de la période courante (selon UTC)."""
        from django.utils import timezone
        now = timezone.now()
        if self.periode == 'heure':
            return now.replace(minute=0, second=0, microsecond=0)
        if self.periode == 'jour':
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.periode == 'semaine':
            return (now - timezone.timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0)
        # mois
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def verifier_reinitialisation(self):
        """Réinitialise les compteurs si la période courante a changé."""
        debut = self._debut_periode_courante()
        if self.debut_periode < debut:
            self.tokens_total  = 0
            self.tokens_entree = 0
            self.tokens_sortie = 0
            self.alerte_active = False
            self.debut_periode = debut
            self.save(update_fields=['tokens_total', 'tokens_entree', 'tokens_sortie',
                                     'alerte_active', 'debut_periode'])

    def ajouter(self, entree: int = 0, sortie: int = 0):
        """Ajoute des tokens et vérifie le seuil d'alerte."""
        self.verifier_reinitialisation()
        self.tokens_entree += entree
        self.tokens_sortie += sortie
        self.tokens_total  += entree + sortie
        if self.alerte_seuil > 0 and self.tokens_total >= self.alerte_seuil:
            self.alerte_active = True
        self.save(update_fields=['tokens_entree', 'tokens_sortie', 'tokens_total', 'alerte_active'])

    @classmethod
    def pour_entreprise(cls, entreprise):
        """Retourne (ou crée) le compteur global de l'entreprise."""
        obj, _ = cls.objects.get_or_create(entreprise=entreprise, utilisateur=None)
        obj.verifier_reinitialisation()
        return obj


# ── Remplacement d'un fichier (Image/FileField) : supprime l'ancien du
# disque, partout dans la plateforme, pour ne pas laisser de fichiers
# orphelins consommer de l'espace inutilement à chaque nouvel upload. ──
@receiver(pre_save)
def _purger_ancien_fichier(sender, instance, **kw):
    champs = [f for f in instance._meta.fields if isinstance(f, models.FileField)]
    if not instance.pk or not champs: return
    anc = sender.objects.filter(pk=instance.pk).values(*[f.name for f in champs]).first() or {}
    for f in champs:
        av, nv = anc.get(f.name), (getattr(instance, f.name).name if getattr(instance, f.name) else None)
        if av and av != nv: f.storage.delete(av)


# ── Tâche d'import de fichier (async) ─────────────────────────────────────
class ConnexionExterne(models.Model):
    """Connexion configurée par l'entreprise vers une base de données
    EXTERNE (hors de la base de l'application elle-même), pour aller y
    RÉCUPÉRER des données en lecture et les importer sous forme de
    TableDynamique — exactement comme un import de fichier CSV/Excel, mais
    la source est un serveur distant identifié par son adresse IP/hôte et
    son port plutôt qu'un fichier téléversé.

    Couvre les moteurs SQL classiques (PostgreSQL, MySQL/MariaDB, SQL
    Server, Oracle) ainsi que MongoDB (NoSQL orienté documents, où
    « table » désigne en réalité une collection) — voir connexion_externe.py
    pour le code qui utilise effectivement ces réglages pour se connecter.
    """

    MOTEUR_POSTGRESQL = "postgresql"
    MOTEUR_MYSQL = "mysql"
    MOTEUR_MSSQL = "mssql"
    MOTEUR_ORACLE = "oracle"
    MOTEUR_MONGODB = "mongodb"
    MOTEURS = [
        (MOTEUR_POSTGRESQL, "PostgreSQL"),
        (MOTEUR_MYSQL, "MySQL / MariaDB"),
        (MOTEUR_MSSQL, "SQL Server"),
        (MOTEUR_ORACLE, "Oracle"),
        (MOTEUR_MONGODB, "MongoDB"),
    ]

    PORTS_DEFAUT = {
        MOTEUR_POSTGRESQL: 5432,
        MOTEUR_MYSQL: 3306,
        MOTEUR_MSSQL: 1433,
        MOTEUR_ORACLE: 1521,
        MOTEUR_MONGODB: 27017,
    }

    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="connexions_externes"
    )
    nom = models.CharField(
        max_length=120,
        help_text=tl("engine/models/_part_005.py:305:20"),
    )
    moteur = models.CharField(max_length=20, choices=MOTEURS, default=MOTEUR_POSTGRESQL)
    hote = models.CharField(
        max_length=255, verbose_name=tl("engine/models/_part_005.py:309:39"),
        help_text=tl("engine/models/_part_005.py:310:20"),
    )
    port = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=tl("engine/models/_part_005.py:g:323:19"),
    )
    nom_base = models.CharField(
        max_length=150, verbose_name=tl("engine/models/_part_005.py:318:39"),
        help_text=tl("engine/models/_part_005.py:319:20"),
    )
    utilisateur = models.CharField(max_length=150, blank=True, default="")
    mot_de_passe = models.CharField(
        max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_005.py:g:333:19"),
    )
    ssl_actif = models.BooleanField(
        default=False, verbose_name=tl("engine/models/_part_005.py:328:38"),
    )
    parametres_supplementaires = models.JSONField(
        default=dict, blank=True,
        verbose_name=tl("engine/models/_part_005.py:332:23"),
        help_text=tl("engine/models/_part_005.py:g:342:19"),
    )
    actif = models.BooleanField(default=True)
    derniere_synchro_le = models.DateTimeField(null=True, blank=True)
    derniere_synchro_statut = models.CharField(max_length=20, blank=True, default="")
    derniere_synchro_erreur = models.TextField(blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = t("engine/models/_part_005.py:344:25")
        verbose_name_plural = t("engine/models/_part_005.py:345:32")
        unique_together = ("entreprise", "nom")
        ordering = ["nom"]

    def __str__(self):
        return f"{self.nom} ({self.get_moteur_display()} — {self.hote}:{self.port_effectif})"

    @property
    def port_effectif(self):
        return self.port or self.PORTS_DEFAUT.get(self.moteur)

    @property
    def est_mongodb(self):
        return self.moteur == self.MOTEUR_MONGODB


class TacheImport(models.Model):
    """Suivi d'un import (fichier CSV/Excel OU synchronisation d'une base de
    données externe) lancé en tâche de fond (Celery).
    Créée avant le .delay(), consultée par polling du frontend."""

    STATUTS = [
        ('en_attente', tl("engine/models/_part_005.py:g:376:24")),
        ('en_cours',   tl("engine/models/_part_005.py:g:377:24")),
        ('termine',    tl("engine/models/_part_005.py:g:378:24")),
        ('echec',      tl("engine/models/_part_005.py:g:379:24")),
    ]

    SOURCE_FICHIER = "fichier"
    SOURCE_BASE_EXTERNE = "base_externe"
    SOURCES = [
        (SOURCE_FICHIER, tl("engine/models/_part_005.py:g:385:26")),
        (SOURCE_BASE_EXTERNE, tl("engine/models/_part_005.py:g:386:31")),
    ]

    entreprise  = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name='taches_import')
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source      = models.CharField(max_length=20, choices=SOURCES, default=SOURCE_FICHIER)
    nom_fichier = models.CharField(
        max_length=255, blank=True, default="",
        help_text=td("engine/models/_part_005.py:g:391:19"),
    )
    connexion_externe = models.ForeignKey(
        ConnexionExterne, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="taches_import",
        help_text=tl("engine/models/_part_005.py:391:20"),
    )
    table_distante = models.CharField(
        max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_005.py:395:20"),
    )
    statut      = models.CharField(max_length=20, choices=STATUTS, default='en_attente')
    progression = models.PositiveSmallIntegerField(default=0)  # 0-100
    table       = models.ForeignKey('TableDynamique', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='tache_import',
                                    help_text=td("engine/models/_part_005.py:g:407:47"))
    groupe      = models.ForeignKey('GroupeTables', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='taches_import',
                                    help_text=td("engine/models/_part_005.py:g:411:47"))
    erreur      = models.TextField(blank=True, default='')
    cree_le     = models.DateTimeField(auto_now_add=True)
    termine_le  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-cree_le']
        verbose_name = t("engine/models/_part_005.py:414:25")

    def __str__(self):
        return f"{self.nom_fichier} ({self.statut})"


# ─────────────────────────────────────────────────────────────
# BLOCKCHAIN (moteur repris tel quel du prototype gy-social/wallet.Block —
# Proof of Authority, chaine de hash SHA-256) : UN SEUL fil par entreprise.
# Ce n'est pas trois blockchains distinctes : les vues "table" et "agent"
# (voir ontologie.construire_graphe) ne sont que des filtres sur les
# transactions de cette meme chaine — la vue admin (entreprise) est la
# seule a voir la chaine complete, non filtree. Meme quand une ligne est
# supprimee, la suppression devient elle-meme une transaction : la chaine
# ne s'arrete jamais, elle continue jusqu'a la fin de l'activite du client.
# ─────────────────────────────────────────────────────────────
class Bloc(models.Model):
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="blocs")
    index = models.PositiveIntegerField()
    transactions = models.JSONField(default=list)
    previous_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)
    timestamp = models.FloatField(default=time.time)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("entreprise", "index")
        ordering = ["entreprise_id", "index"]
        verbose_name = t("engine/models/_part_005.py:442:25")

    def __str__(self):
        return f"Bloc #{self.index} [{self.hash[:12]}…] — {self.entreprise}"

    @staticmethod
    def _hash(index, transactions, previous_hash, timestamp):
        contenu = json.dumps(
            {"index": index, "transactions": transactions,
             "previous_hash": previous_hash, "timestamp": timestamp},
            sort_keys=True,
        )
        return hashlib.sha256(contenu.encode()).hexdigest()

    @classmethod
    def _committer_bloc(cls, entreprise_id, transactions: list):
        """Crée UN bloc contenant `transactions` (une ou plusieurs), chaîné
        sur le dernier bloc de l'entreprise. Ne doit être appelé que sous
        `_bloc_lock` (voir ajouter_transaction/ajouter_transactions).

        `_bloc_lock` (un threading.Lock Python) ne protège qu'UN SEUL
        processus : en déploiement réel (plusieurs workers gunicorn +
        Celery, chacun un processus distinct), _bloc_lock ne les sérialise
        pas entre eux. On sérialise donc réellement au niveau BD en
        verrouillant la ligne Entreprise correspondante via
        `select_for_update()` avant de lire le dernier bloc : sur
        PostgreSQL, un second processus qui tente de committer un bloc pour
        la même entreprise attend que la transaction du premier se termine
        (commit ou rollback) avant de lire à son tour "dernier bloc" — il ne
        peut donc plus voir un état périmé et tenter de recréer le même
        index. (SQLite ignore `select_for_update()` — pas de verrou réel
        multi-process dans ce cas ; c'est une limitation connue de SQLite,
        pas de ce correctif.) La boucle de retry sur IntegrityError est
        conservée en filet de sécurité pour toute course résiduelle
        (SQLite, ou une transaction concurrente qui n'aurait pas pris le
        même verrou), afin de ne jamais renvoyer une erreur 500 pour une
        transaction déjà réelle."""
        from django.db import IntegrityError
        from django.db import transaction as _db_transaction

        derniere_erreur = None
        for _tentative in range(5):
            try:
                with _db_transaction.atomic():
                    # Verrouille la ligne Entreprise : tout autre processus
                    # committant un bloc pour la même entreprise doit
                    # attendre ici (PostgreSQL) avant de lire "dernier bloc".
                    Entreprise.objects.select_for_update().get(pk=entreprise_id)

                    dernier = cls.objects.filter(entreprise_id=entreprise_id).order_by("-index").first()
                    idx = (dernier.index + 1) if dernier else 0
                    prev_hash = dernier.hash if dernier else "0" * 64
                    ts = time.time()
                    # Sécurité des dates : jamais antérieur au dernier bloc (voir securite_dates.py).
                    _securite_dates.exiger(ts, dernier.timestamp if dernier else None)
                    h = cls._hash(idx, transactions, prev_hash, ts)
                    return cls.objects.create(
                        entreprise_id=entreprise_id, index=idx, transactions=transactions,
                        previous_hash=prev_hash, hash=h, timestamp=ts,
                    )
            except IntegrityError as exc:
                derniere_erreur = exc
                continue
        raise derniere_erreur

    @classmethod
    def ajouter_transaction(cls, entreprise_id, tx: dict):
        """Empile la transaction puis valide immediatement un nouveau bloc
        (comme wallet.Block.forcer_bloc) : chaque evenement metier produit
        sa propre preuve, tracable independamment des autres."""
        with _verrou_blocs(entreprise_id):
            return cls._committer_bloc(entreprise_id, [tx])

    @classmethod
    def ajouter_transactions(cls, entreprise_id, txs: list):
        """Comme ajouter_transaction, mais regroupe PLUSIEURS transactions
        dans UN SEUL bloc. Utilisé par l'import en masse
        (import_fichier.py) : créer un bloc par ligne importée — verrou,
        lecture du dernier bloc, hash, écriture, à CHAQUE ligne — était très
        lent sur les gros fichiers. Aucune preuve n'est perdue : chaque
        transaction du lot reste individuellement présente dans le champ
        `transactions` du bloc, exactement comme avant, juste regroupées."""
        if not txs:
            return None
        with _verrou_blocs(entreprise_id):
            return cls._committer_bloc(entreprise_id, list(txs))

    @classmethod
    def chaine_valide(cls, entreprise_id):
        blocs = list(cls.objects.filter(entreprise_id=entreprise_id).order_by("index"))
        for i in range(1, len(blocs)):
            b, prev = blocs[i], blocs[i - 1]
            if b.previous_hash != prev.hash or b.timestamp < prev.timestamp:
                return False
            if b.hash != cls._hash(b.index, b.transactions, b.previous_hash, b.timestamp):
                return False
        return True

    @classmethod
    def combler_lacunes(cls, entreprise_id, intervalle_minimale=None):
        """Filet de sécurité : détecte les enregistrements jamais authentifiés
        (signal manqué — import direct en base, script externe...) et crée
        un bloc de rattrapage, horodaté sur leur date réelle (cree_le), pas sur
        l'instant présent — la preuve reste datée de l'événement d'origine.

        Le parcours de la chaîne se fait par lots (sans instancier les blocs) et la différence « lignes existantes −
        lignes déjà authentifiées » est calculée en Python : plus de `exclude(id__in=…)` géant envoyé à la base.

        `intervalle_minimale` (secondes, optionnel) : si fourni, le contrôle n'est refait qu'une fois par intervalle et par
        entreprise (cache Django, partagé entre processus). À utiliser depuis les pages qui s'affichent souvent (ontologie) :
        relire toute la chaîne à chaque affichage ralentit le serveur à mesure qu'elle s'allonge. Sans cet argument
        (page blockchain), le contrôle est refait à chaque appel, comme avant."""
        cle = None
        if intervalle_minimale:
            from django.core.cache import cache
            try:
                if not cache.add(f"bloc_combler_lacunes:{entreprise_id}", 1, intervalle_minimale):
                    return   # contrôle déjà fait récemment pour cette entreprise
                cle = f"bloc_combler_lacunes:{entreprise_id}"
            except Exception:
                cle = None   # cache indisponible : on exécute le contrôle normalement
        try:
            vus = set()
            for transactions in (cls.objects.filter(entreprise_id=entreprise_id)
                                 .values_list("transactions", flat=True).iterator(chunk_size=500)):
                for tx in transactions or []:
                    eid = tx.get("enregistrement_id") if isinstance(tx, dict) else None
                    if eid:
                        try:
                            vus.add(int(eid))
                        except (TypeError, ValueError):
                            pass
            existants = set(Enregistrement.objects.filter(table__entreprise_id=entreprise_id).values_list("id", flat=True))
            manquants = sorted(existants - vus)
            for i in range(0, len(manquants), 500):   # lots de 500 : limite de variables SQL (SQLite)
                for e in Enregistrement.objects.filter(id__in=manquants[i:i + 500]).select_related("table").order_by("id"):
                    cls.ajouter_transaction(entreprise_id, {
                        "type": "rattrapage", "table_id": e.table_id, "table": e.table.nom,
                        "enregistrement_id": e.id, "agent_id": e.cree_par_id,
                        "date_origine": e.cree_le.isoformat(),
                        # Etat REEL de la ligne au moment du rattrapage (pas juste ses
                        # identifiants) : c'est justement parce que ces lignes n'ont
                        # jamais ete authentifiees normalement qu'il faut y capturer
                        # le contenu, sinon le bloc de rattrapage ne prouve rien.
                        "donnees": e.to_dict_lisible(),
                    })
        except Exception:
            if cle:   # échec : on ne bloque pas le prochain essai pendant tout l'intervalle
                try:
                    from django.core.cache import cache
                    cache.delete(cle)
                except Exception:
                    pass
            raise

    @classmethod
    def historique_cible(cls, entreprise_id, cible_type, cible_id):
        """Lignée d'un objet précis : toutes les transactions "action:*"
        (voir engine.actions.executer_action, Phase 4 du cahier des charges
        Ontology — Provenance/Lineage) qui concernent CETTE cible, quel que
        soit son type. Même principe que
        views_blockchain.historique_blockchain_table (qui filtre par
        table_id), mais généralisé : ne suppose pas que la cible soit une
        table — reste un simple filtre sur la même chaîne, aucun second
        registre."""
        return [
            {
                "index": b.index, "timestamp": b.timestamp,
                "action": t.get("action"), "utilisateur_id": t.get("utilisateur_id"),
                "statut": t.get("statut"), "hash_bloc": b.hash,
            }
            for b in cls.objects.filter(entreprise_id=entreprise_id).order_by("index")
            for t in b.transactions
            if t.get("cible_type") == cible_type and str(t.get("cible_id")) == str(cible_id)
        ]


# ─────────────────────────────────────────────────────────────
# WALLET FCFA — solde, conversion multi-devises, frais configurables par
# l'agent. L'historique des mouvements vit dans Bloc (type "wallet_*"),
# pas de second système d'historique dupliqué.
# ─────────────────────────────────────────────────────────────
class Wallet(models.Model):
    """Portefeuille FCFA d'un membre (client ou agent) — solde courant."""
    membre = models.OneToOneField(MembreEntreprise, on_delete=models.CASCADE, related_name="wallet")
    solde = MontantField(default=0, verbose_name=tl("engine/models/_part_005.py:589:56"))
    maj_le = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet {self.membre} — {self.solde} {_dev_aff()}"


class TauxChange(models.Model):
    """Taux de conversion vers le FCFA (devise pivot), configurable par l'agent."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="taux_change")
    devise = models.CharField(max_length=10, verbose_name=tl("engine/models/_part_005.py:599:60"))
    taux_vers_fcfa = TauxField(verbose_name=tl("engine/models/_part_005.py:600:54"))
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("entreprise", "devise")
        verbose_name = t("engine/models/_part_005.py:605:25")

    def __str__(self):
        return f"1 {self.devise} = {self.taux_vers_fcfa} {_dev_aff()}"


def pages_client_fermees_defaut():
    """Par défaut aucune page d'analyse n'est ouverte aux clients (l'agent les ouvre une à une)."""
    from .. import wallet
    return sorted(wallet.PAGES_CLIENT)


class FraisWallet(models.Model):
    """Frais/taxes wallet (dépôt, retrait, transfert) et taux d'intérêt,
    configurables par l'agent — un seul jeu de taux par entreprise."""
    entreprise = models.OneToOneField(Entreprise, on_delete=models.CASCADE, related_name="frais_wallet")
    taux_depot = TauxField(default=0, verbose_name=tl("engine/models/_part_005.py:615:61"))
    taux_retrait = TauxField(default=0, verbose_name=tl("engine/models/_part_005.py:616:63"))
    taux_transfert = TauxField(default=0, verbose_name=tl("engine/models/_part_005.py:617:65"))
    taux_interet = TauxField(default=0, verbose_name=tl("engine/models/_part_005.py:618:63"))
    # TVA (%) ajoutée par-dessus les taxes ci-dessous et les prix des services payants ; réglée par l'admin (page de configuration des paiements).
    taux_tva = TauxField(default=0)
    taux_tva_actif = models.BooleanField(default=False)
    # Taxe de chaque événement de paiement mobile : l'agent coche le pourcentage et/ou le montant fixe (FCFA) à prélever.
    taux_depot_actif = models.BooleanField(default=True)
    taux_retrait_actif = models.BooleanField(default=True)
    taux_transfert_actif = models.BooleanField(default=True)
    fixe_depot = MontantField(default=0)
    fixe_retrait = MontantField(default=0)
    fixe_transfert = MontantField(default=0)
    fixe_depot_actif = models.BooleanField(default=False)
    fixe_retrait_actif = models.BooleanField(default=False)
    fixe_transfert_actif = models.BooleanField(default=False)
    # Prix des services payants mis à disposition des clients : {catégorie: montant fixe | {"mode": "taux", "valeur": % du wallet}}.
    services = models.JSONField(default=dict, blank=True)
    # Pages d'analyse FERMÉES aux clients (liste de catégories, ex. ["anova"]). Par défaut TOUTES sont fermées : le client n'en voit aucune
    # tant que l'agent ne coche pas « Ouvert au client » (page Paiement / services).
    pages_client_fermees = models.JSONField(default=pages_client_fermees_defaut, blank=True)
    maj_le = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Frais wallet — {self.entreprise}"


class RemunerationAgent(models.Model):
    """Rémunération d'un agent, fixée par l'administrateur : un salaire mensuel versé automatiquement le jour de paie, OU un pourcentage
    de ce que paient ses clients (services, abonnements), OU rien. L'argent vient de la caisse de l'entreprise (engine/remuneration.py)."""
    MODES = [("aucun", td("engine/models/_part_005.py:g:646:24")), ("salaire", "Salaire mensuel"), ("pourcentage", td("engine/models/_part_005.py:g:646:83"))]
    membre = models.OneToOneField(MembreEntreprise, on_delete=models.CASCADE, related_name="remuneration")
    mode = models.CharField(max_length=12, choices=MODES, default="aucun")
    salaire = MontantField(default=0)
    jour_paie = models.PositiveSmallIntegerField(default=28)          # 1-28 : existe tous les mois
    pourcentage = TauxField(default=0)                        # 0-100
    actif_depuis = models.DateField(null=True, blank=True)            # pas de salaire rétroactif avant cette date
    derniere_periode = models.CharField(max_length=7, blank=True, default="")   # « AAAA-MM » du dernier salaire versé
    maj_le = models.DateTimeField(auto_now=True)

    def __str__(self):
        return tf("engine/models/_part_005.py:g:659:16", v1=self.membre, v2=self.mode)


class FournisseurPaiement(models.Model):
    """Fournisseur de paiement configuré par le super administrateur (réglages UNIQUES pour tout le serveur, comme la SIM USSD).
    Types : voir fournisseurs_paiement.TYPES. Sans ligne « ussd », le moteur USSD reste actif (comportement historique)."""
    TYPES = [("ussd", td("engine/models/_part_005.py:g:663:23")), ("rest", td("engine/models/_part_005.py:g:663:58")), ("django_payments", td("engine/models/_part_005.py:g:663:111"))]
    code = models.SlugField(max_length=40, unique=True)
    libelle = models.CharField(max_length=80)
    type = models.CharField(max_length=20, choices=TYPES)
    actif = models.BooleanField(default=False)
    config = models.JSONField(default=dict, blank=True)
    maj_le = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.libelle} ({self.type})"


class DepotPaiement(models.Model):
    """Dépôt initié chez un fournisseur externe. `statut` passe à « credite » UNE seule fois, dans la même transaction que le
    crédit du wallet (wallet.crediter_depot_fournisseur) : webhook rejoué, double clic ou webhook + vérification ne créditent jamais deux fois."""
    STATUTS = [("en_attente", tl("engine/models/_part_005.py:g:681:31")), ("credite", tl("engine/models/_part_005.py:g:681:58")), ("echec", tl("engine/models/_part_005.py:g:681:80"))]
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="depots_paiement")
    fournisseur = models.ForeignKey(FournisseurPaiement, on_delete=models.PROTECT, related_name="depots")
    reference = models.CharField(max_length=64)
    reference_externe = models.CharField(max_length=200, blank=True, default="", db_index=True)
    montant = MontantField()
    statut = models.CharField(max_length=12, choices=STATUTS, default="en_attente")
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("fournisseur", "reference")

    def __str__(self):
        return f"{self.fournisseur.code}:{self.reference} {self.montant} ({self.statut})"


class MoyenRetrait(models.Model):
    """Compte mobile money où un membre (agent ou client) reçoit ses retraits. Jamais de numéro de carte : il est refusé à
    l'enregistrement (wallet_flux.valider_numero). Au plus un moyen « par défaut » par membre ; modifié par son propriétaire seul."""
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="moyens_retrait")
    operateur = models.CharField(max_length=40)
    numero = models.CharField(max_length=20)
    libelle = models.CharField(max_length=60, blank=True, default="")
    par_defaut = models.BooleanField(default=False)
    actif = models.BooleanField(default=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-par_defaut", "id"]
        unique_together = ("membre", "operateur", "numero")

    def __str__(self):
        return f"{self.operateur} {self.numero}"


class RetraitWallet(models.Model):
    """Trace en base de chaque retrait demandé par un membre (effectué ou en échec) ; le retrait effectué est aussi dans la blockchain
    (bloc « wallet_retrait », dont l'index est gardé ici). Un échec ne débite rien."""
    STATUTS = [("effectue", _gtfr("engine/models/_part_005.py:765:29")), ("echec", _gtfr("engine/models/_part_005.py:765:53"))]
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="retraits_wallet")
    moyen = models.ForeignKey(MoyenRetrait, on_delete=models.SET_NULL, null=True, blank=True, related_name="retraits")
    operateur = models.CharField(max_length=40)
    numero_destination = models.CharField(max_length=30)
    montant = MontantField()
    frais = MontantField(default=0)
    statut = models.CharField(max_length=12, choices=STATUTS, default="effectue")
    reponse_operateur = models.TextField(blank=True, default="")
    motif_echec = models.CharField(max_length=200, blank=True, default="")
    bloc_index = models.IntegerField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-id"]


class ChargeWallet(models.Model):
    """Charge ou coût payé par l'entreprise : sortie d'argent débitée de la caisse (wallet de l'administrateur) et inscrite dans la
    blockchain (bloc « wallet_charge »). Réservé à l'administrateur ; jamais de découvert (wallet_flux.enregistrer_charge)."""
    CATEGORIES = [("loyer", "Loyer"), ("personnel", "Personnel hors wallet"), ("fournisseur", "Fournisseurs"),
                  ("telecom", _gtd("rg6.msg.044")), ("materiel", _gtd("rg6.msg.045")), ("impots", _gtd("rg6.msg.046")),
                  ("marketing", "Marketing"), ("frais_prestataire", _gtd("rg6.msg.047")), ("autre", "Autre")]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="charges_wallet")
    categorie = models.CharField(max_length=24, choices=CATEGORIES, default="autre")
    libelle = models.CharField(max_length=120)
    beneficiaire = models.CharField(max_length=120, blank=True, default="")
    montant = MontantField()
    date_charge = models.DateField()
    cree_par = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    bloc_index = models.IntegerField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_charge", "-id"]


class EvenementPrestataire(models.Model):
    """Journal des événements d'un prestataire de paiement (dépôt initié, wallet crédité, échec, mis en attente de
    décision) : une ligne en base ET un bloc « wallet_prestataire » dans la blockchain pour chacun (wallet_flux.noter_evenement)."""
    TYPES = [("initie", _gtd("rg6.msg.048")), ("credite", _gtd("rg6.msg.049")),
             ("echec", _gtd("rg6.msg.050")), ("suspect", _gtd("rg6.msg.051"))]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="evenements_prestataire")
    fournisseur = models.ForeignKey(FournisseurPaiement, on_delete=models.SET_NULL, null=True, blank=True, related_name="evenements")
    fournisseur_code = models.CharField(max_length=40, blank=True, default="")
    depot = models.ForeignKey(DepotPaiement, on_delete=models.SET_NULL, null=True, blank=True, related_name="evenements")
    type = models.CharField(max_length=12, choices=TYPES)
    reference = models.CharField(max_length=64, blank=True, default="")
    reference_externe = models.CharField(max_length=200, blank=True, default="")
    montant = MontantField(null=True, blank=True)
    detail = models.CharField(max_length=200, blank=True, default="")
    bloc_index = models.IntegerField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-id"]


class AbonnementMensuel(models.Model):
    """Accès mensuel glissant (30 jours depuis le paiement) d'un membre
    aux services premium : dashboard_bi, ontologie, ia.
    Un seul enregistrement actif par (membre, categorie) — mis à jour
    (expire_le repoussé) à chaque nouveau paiement."""
    CATEGORIES = [("dashboard_bi", "Dashboard BI"), ("ontologie", "Ontologie"), ("ia", "Intelligence Artificielle"),
                  ("geolocalisation", tl("engine/models/_part_005.py:g:704:39")), ("series_temporelles", tl("engine/models/_part_005.py:g:704:82")), ("statistiques", "Statistiques"),
                  ("anova", "ANOVA"), ("analyse_factorielle", tl("engine/models/_part_005.py:g:705:63"))]
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="abonnements")
    categorie = models.CharField(max_length=32, choices=CATEGORIES)
    expire_le = models.DateTimeField()
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("membre", "categorie")
        verbose_name = t("engine/models/_part_005.py:638:25")

    def est_valide(self):
        from django.utils import timezone
        return self.expire_le > timezone.now()


class ModemSms(models.Model):
    """Surcharge par modem (mmcli) — schéma de la migration 0136_operateurs_sms."""
    id = models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name=tl("engine/models/_part_005.py:647:95"))
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="modems_sms")
    modem_id = models.PositiveIntegerField(
        help_text=tl("engine/models/_part_005.py:650:20"), verbose_name=tl("engine/models/_part_005.py:650:92"))
    numero_agence = models.CharField(
        blank=True, default="", max_length=30, verbose_name=tl("engine/models/_part_005.py:652:62"),
        help_text=tl("engine/models/_part_005.py:653:20"))
    operateur = models.CharField(
        blank=True, default="", max_length=60, verbose_name=tl("engine/models/_part_005.py:655:62"),
        help_text=tl("engine/models/_part_005.py:656:20"))

    class Meta:
        verbose_name = t("engine/models/_part_005.py:659:25")
        verbose_name_plural = t("engine/models/_part_005.py:660:32")
        ordering = ["modem_id"]
        unique_together = (("entreprise", "modem_id"),)


class PrefixeOperateur(models.Model):
    """Préfixe de numéro -> opérateur — schéma de la migration 0136_operateurs_sms."""
    id = models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name=tl("engine/models/_part_005.py:667:95"))
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="prefixes_operateurs")
    prefixe = models.CharField(
        max_length=12, verbose_name=tl("engine/models/_part_005.py:670:38"),
        help_text=tl("engine/models/_part_005.py:671:20"))
    operateur = models.CharField(max_length=60, verbose_name=tl("engine/models/_part_005.py:672:63"))

    class Meta:
        verbose_name = t("engine/models/_part_005.py:675:25")
        verbose_name_plural = t("engine/models/_part_005.py:676:32")
        ordering = ["-prefixe"]
        unique_together = (("entreprise", "prefixe"),)


class TachePlanifiee(models.Model):
    """Tâche à script Python (généré par l'IA ou écrit à la main), exécutable une fois
    ou à intervalle — schéma de la migration 0139_tacheplanifiee.

    Étendue (voir engine/declencheurs.py) avec un second mode de déclenchement :
    au lieu d'une planification horaire (`mode_declenchement="planifie"`), la tâche
    peut être `"condition"` — lancée automatiquement quand un événement réel sur les
    données de l'entreprise se produit (nouvel enregistrement, nouveau client,
    recherche effectuée, relation/liaison créée dans l'ontologie, densité de
    relations franchissant un seuil, KPI entrant en alerte, nouvel événement
    blockchain). Les mêmes champs `intervalle_type`/`intervalle_valeur` sont alors
    réinterprétés comme un délai de repos minimum entre deux déclenchements
    (anti-rafale), pas comme une répétition planifiée."""
    INTERVALLE_CHOICES = [("unique", tl("engine/models/_part_005.py:g:769:38")), ("secondes", tl("engine/models/_part_005.py:g:769:72")),
                          ("minutes", tl("engine/models/_part_005.py:g:770:39")), ("heures", tl("engine/models/_part_005.py:g:770:75")),
                          ("jours", tl("engine/models/_part_005.py:g:771:37"))]
    STATUT_CHOICES = [("en_attente", tl("engine/models/_part_005.py:g:772:38")), ("script_genere", tl("engine/models/_part_005.py:g:772:71")),
                      ("en_cours", tl("engine/models/_part_005.py:g:773:36")), ("execute", tl("engine/models/_part_005.py:g:773:71")),
                      ("echec_generation", tl("engine/models/_part_005.py:g:774:44")),
                      ("echec_execution", tl("engine/models/_part_005.py:g:775:43")), ("echec", tl("engine/models/_part_005.py:g:775:75"))]
    MODE_DECLENCHEMENT_CHOICES = [
        ("planifie", tl("engine/models/_part_005.py:g:777:22")),
        ("condition", tl("engine/models/_part_005.py:g:778:23")),
    ]
    CONDITION_CATEGORIE_CHOICES = [
        ("enregistrement", td("engine/models/_part_005.py:g:778:28")),
        ("client", td("engine/models/_part_005.py:g:779:20")),
        ("recherche", td("engine/models/_part_005.py:g:780:23")),
        ("relation", td("engine/models/_part_005.py:g:781:22")),
        ("densite_relation", td("engine/models/_part_005.py:g:782:30")),
        ("kpi", td("engine/models/_part_005.py:g:783:17")),
        ("blockchain", td("engine/models/_part_005.py:g:784:24")),
    ]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="taches")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    nom = models.CharField(blank=True, default="", max_length=255)
    instructions = models.TextField(blank=True, default="", help_text=tl("engine/models/_part_005.py:717:72"))
    script_genere = models.TextField(blank=True)
    fichier_script = models.CharField(blank=True, max_length=500)
    modifie_manuellement = models.BooleanField(default=False)
    intervalle_type = models.CharField(choices=INTERVALLE_CHOICES, default="unique", max_length=20)
    intervalle_valeur = models.PositiveIntegerField(default=0, help_text=tl("engine/models/_part_005.py:722:75"))
    actif = models.BooleanField(default=True)
    prochaine_execution = models.DateTimeField(blank=True, null=True)
    derniere_execution = models.DateTimeField(blank=True, null=True)
    statut = models.CharField(choices=STATUT_CHOICES, default="en_attente", max_length=20)
    erreur = models.TextField(blank=True)
    resume = models.TextField(blank=True, help_text=tl("engine/models/_part_005.py:728:54"))
    fichiers_sortie = models.JSONField(blank=True, default=list, help_text=tl("engine/models/_part_005.py:729:77"))
    # ── Déclenchement conditionnel (voir engine/declencheurs.py) ──
    mode_declenchement = models.CharField(
        choices=MODE_DECLENCHEMENT_CHOICES, default="planifie", max_length=15,
        help_text=td("engine/models/_part_005.py:g:805:19"),
    )
    condition_categorie = models.CharField(
        choices=CONDITION_CATEGORIE_CHOICES, blank=True, default="", max_length=20,
        help_text=tl("engine/models/_part_005.py:738:20"),
    )
    condition_table = models.ForeignKey(
        "TableDynamique", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="taches_conditionnelles",
        help_text=tl("engine/models/_part_005.py:743:20"),
    )
    condition_parametres = models.JSONField(
        blank=True, default=dict,
        help_text=tl("engine/models/_part_005.py:g:822:19"),
    )
    condition_derniere_alerte = models.CharField(
        blank=True, default="", max_length=64,
        help_text=td("engine/models/_part_005.py:g:823:19"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-cree_le"]


# ─────────────────────────────────────────────────────────────
# TEST D'UTILISABILITÉ STATISTIQUE (2026-09-23) — voir engine/usabilite.py
# Mesures automatiques pendant l'usage réel (jamais le contenu saisi) + questionnaire SUS standard.
# ─────────────────────────────────────────────────────────────
class MesureUsage(models.Model):
    TYPE_CHOICES = [
        ("page", td("engine/models/_part_005.py:g:839:18")), ("action", _gtd("rg6.msg.052")),
        ("ouverture", td("engine/models/_part_005.py:g:840:23")),   # cible « table:<id>:<onglet> » : usage réel PAR TABLE (fantome_usage.py)
        ("erreur_api", td("engine/models/_part_005.py:g:841:24")), ("erreur_js", td("engine/models/_part_005.py:g:841:57")),
        ("clic_rageur", td("engine/models/_part_005.py:g:842:25")), ("tache", td("engine/models/_part_005.py:g:842:67")),
    ]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="mesures_usage")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    role = models.CharField(max_length=20, blank=True, default="")
    session = models.CharField(max_length=32, blank=True, default="")
    type_mesure = models.CharField(max_length=20, choices=TYPE_CHOICES)
    page = models.CharField(max_length=200, blank=True, default="")
    cible = models.CharField(max_length=200, blank=True, default="")
    succes = models.BooleanField(null=True, blank=True)
    duree_ms = models.PositiveIntegerField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["entreprise", "type_mesure", "cree_le"])]


class EvaluationSUS(models.Model):
    """Réponse au System Usability Scale (10 affirmations, échelle 1 à 5) ; score 0–100."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="evaluations_sus")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    role = models.CharField(max_length=20, blank=True, default="")
    reponses = models.JSONField()
    score = models.FloatField()
    commentaire = models.TextField(blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)


class JournalTransformationColonne(models.Model):
    """Journal d'une transformation de colonne (regex, casse, préfixe…) appliquée d'un coup à toutes les cellules
    d'une colonne (engine/transformation_colonne.py). `anciennes_valeurs` {id_enregistrement: valeur brute avant}
    permet d'annuler la transformation tant que les cellules n'ont pas été modifiées depuis."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="transformations_colonne")
    table = models.ForeignKey(TableDynamique, on_delete=models.CASCADE, related_name="transformations_colonne")
    champ = models.ForeignKey(ChampDynamique, on_delete=models.CASCADE, related_name="transformations")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    operation = models.JSONField(default=dict)
    anciennes_valeurs = models.JSONField(default=dict)
    nouvelles_valeurs = models.JSONField(default=dict)
    nb_modifiees = models.PositiveIntegerField(default=0)
    annule = models.BooleanField(default=False)
    # Historique façon « Annuler / Rétablir » (voir views_transformation) : `genre` = « valeurs » (cellules modifiées sur place) ou
    # « colonnes » (nouvelles colonnes créées : cloner, découper, lieux → coordonnées ; annuler = les retirer). `refaisable` : une
    # transformation annulée se rétablit tant qu'aucune nouvelle transformation n'a été faite depuis (comme Photoshop ou Word).
    genre = models.CharField(max_length=10, default="valeurs")
    colonnes_creees = models.JSONField(default=list)
    refaisable = models.BooleanField(default=True)
    libelle = models.CharField(max_length=200, blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-cree_le"]


# ─────────────────────────────────────────────────────────────
# BLOCKCHAIN SYSTÈME — UNE seule chaîne pour toute la plateforme, réservée au super-administrateur Django.
# Les `Bloc` ci-dessus sont par entreprise ; celle-ci est au-dessus des entreprises : elle prouve ce que le
# super-utilisateur a fait sur l'infrastructure (configuration du cluster, bascule d'un serveur, connexions).
# Même chaîne de hash SHA-256 que `Bloc` (on réutilise Bloc._hash : un seul algorithme à auditer).
# Si la base est injoignable (c'est justement quand on répare le cluster), l'événement est mis de côté dans un
# journal fichier (cluster.journaliser_hors_ligne) puis rejoué, dans l'ordre, à la prochaine écriture réussie.
# ─────────────────────────────────────────────────────────────
class BlocSysteme(models.Model):
    index = models.PositiveIntegerField(unique=True)
    transactions = models.JSONField(default=list)
    previous_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)
    timestamp = models.FloatField(default=time.time)
    auteur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["index"]

    def __str__(self):
        return tf("engine/models/_part_005.py:g:922:16", v1=self.index, v2=self.hash[:12])

    @classmethod
    def _committer(cls, txs, auteur_id=None):
        from django.db import IntegrityError, transaction as _db_transaction
        derniere = None
        for _ in range(5):
            try:
                with _db_transaction.atomic():
                    dernier = cls.objects.select_for_update().order_by("-index").first()
                    idx = dernier.index + 1 if dernier else 0
                    prev = dernier.hash if dernier else "0" * 64
                    ts = time.time()
                    _securite_dates.exiger(ts, dernier.timestamp if dernier else None)
                    return cls.objects.create(index=idx, transactions=txs, previous_hash=prev, timestamp=ts,
                                              hash=Bloc._hash(idx, txs, prev, ts), auteur_id=auteur_id)
            except IntegrityError as exc:  # deux nœuds ont pris le même index : on relit et on recommence
                derniere = exc
        raise derniere

    @classmethod
    def enregistrer(cls, utilisateur, action, details=None):
        """Ajoute un bloc ; renvoie le bloc, ou None si la base est injoignable (événement alors mis de côté)."""
        import socket
        from django.db import DatabaseError
        from .. import journal_hors_ligne as _cluster
        tx = {"type": "superadmin", "action": action, "details": details or {},
              "utilisateur_id": getattr(utilisateur, "pk", None), "utilisateur": getattr(utilisateur, "get_username", lambda: "")(),
              "noeud": socket.gethostname(), "ts": time.time()}
        en_attente = _cluster.reprendre_journal()  # d'abord ce qui attendait, dans l'ordre
        try:
            while en_attente:
                ancien = en_attente[0]
                cls._committer([dict(ancien, hors_ligne=True)], ancien.get("utilisateur_id"))
                en_attente.pop(0)
            return cls._committer([tx], tx["utilisateur_id"])
        except DatabaseError:
            for restant in [*en_attente, tx]:  # rien n'est perdu : tout retourne au journal, dans l'ordre
                _cluster.journaliser_hors_ligne(restant)
            return None

    @classmethod
    def chaine_valide(cls):
        """(valide, index du premier bloc fautif ou None)."""
        prec = None
        for b in cls.objects.order_by("index").iterator():
            attendu = prec.hash if prec else "0" * 64
            if (b.previous_hash != attendu or (prec and b.timestamp < prec.timestamp)
                    or b.hash != Bloc._hash(b.index, b.transactions, b.previous_hash, b.timestamp)):
                return False, b.index
            prec = b
        return True, None


from django.contrib.auth.signals import user_logged_in as _user_logged_in  # noqa: E402


@receiver(_user_logged_in, dispatch_uid="blockchain_systeme_connexion_superuser")
def _tracer_connexion_superuser(sender, request, user, **kwargs):
    """Chaque connexion d'un super-administrateur entre dans la chaîne système (adresse IP comprise)."""
    if getattr(user, "is_superuser", False):
        from ..ratelimit import adresse_ip_client
        ip = (adresse_ip_client(request) if request is not None else "") or ""
        BlocSysteme.enregistrer(user, "connexion", {"ip": ip})


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
