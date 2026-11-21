from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tl
from engine.i18n import tf
from engine.i18n import t
from ._header import *  # noqa
from engine.i18n import t as _gt  # noqa: E402

class Entreprise(models.Model):
    nom = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(unique=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    inscription_ouverte = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_001.py:9:23"),
        help_text=tl("engine/models/_part_001.py:10:20"),
    )
    # Accès API externe (clés Bearer, voir CleAPI / CleAPIAuthMiddleware) : l'admin l'ouvre ou le ferme. Ne concerne JAMAIS le navigateur.
    api_ouverte = models.BooleanField(default=False)   # fermé par défaut : l'admin l'ouvre explicitement (page Employés, carte « Accès API externe »)
    # Saison restaurée (cycle.restaurer_en_saison) : entreprise « sœur » de la saison en cours ; lecture seule tant que l'admin n'ouvre pas l'écriture.
    saison_de = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="saisons")
    ecriture_ouverte = models.BooleanField(default=False)
    logo = models.ImageField(
        upload_to="logos/", blank=True, null=True,
        verbose_name=tl("engine/models/_part_001.py:14:23"),
    )

    # Personnalisation de la marque (page « Personnalisation », engine/marque.py) : slogan, slogan2 et couleurs de l'interface.
    # Vide = identité d'origine de l'application ; seules les clés présentes remplacent le défaut.
    marque = models.JSONField(default=dict, blank=True)

    # Langue de l'interface choisie PAR L'ADMINISTRATEUR (page « Personnalisation ») : code d'un catalogue de engine/i18n_defaults/ (« fr », « en »…).
    # Vide = automatique (langue du serveur détectée). Lue par engine/i18n.py (langue_admin) à chaque démarrage du serveur puis régulièrement,
    # appliquée à tous les utilisateurs qui n'ont pas choisi leur propre langue dans le Profil.
    langue_interface = models.CharField(max_length=10, blank=True, default="")

    # ── Coordonnées — configurées une seule fois ici (page employés),
    # puis injectées automatiquement dans CHAQUE document généré (reports.py
    # ::generer_pdf_document), sans dépendre de ce que le script IA invente
    # pour le bloc "émetteur". Tous ces champs sont optionnels : un champ
    # vide est simplement omis à l'affichage sur le PDF. ──
    adresse = models.TextField(
        blank=True, default="",
        verbose_name=tl("engine/models/_part_001.py:24:23"),
        help_text=tl("engine/models/_part_001.py:25:20"),
    )
    telephone_service_client = models.CharField(
        max_length=30, blank=True, default="",
        verbose_name=tl("engine/models/_part_001.py:29:23"),
        help_text=tl("engine/models/_part_001.py:30:20"),
    )
    email_contact = models.EmailField(
        blank=True, default="",
        verbose_name=tl("engine/models/_part_001.py:34:23"),
    )
    site_web = models.URLField(
        blank=True, default="",
        verbose_name=tl("engine/models/_part_001.py:38:23"),
    )
    # Cle de chiffrement propre a l'entreprise, utilisee pour chiffrer le
    # contenu des QR codes (recherche securisee) — voir engine/qr_secure.py.
    # Vide = QR generes en clair (aucun chiffrement applique).
    cle_chiffrement_qr = models.CharField(
        max_length=128, blank=True, default="",
        verbose_name=tl("engine/models/_part_001.py:45:23"),
        help_text=tl("engine/models/_part_001.py:46:20"),
    )

    # ── Essai et abonnement (page super-administrateur /plateforme/entreprises/, engine/acces_entreprise.py) ──
    # acces_jusqua : fin de l'accès (None = sans limite, cas des entreprises d'avant l'essai). bloquee : blocage manuel immédiat.
    # en_essai : True pendant l'essai d'une semaine (nouvelle entreprise), False dès le premier déblocage payant.
    acces_jusqua = models.DateTimeField(null=True, blank=True, db_index=True)
    bloquee = models.BooleanField(default=False)
    en_essai = models.BooleanField(default=False)
    # Plafond de personnes inscrites (agents + clients) de l'entreprise, fixé par le super-administrateur (page /plateforme/entreprises/) :
    # le trafic, donc le tarif, croît avec ce nombre. 5 par défaut ; l'augmenter est un service payant. Voir acces_entreprise.place_disponible.
    max_connexions = models.PositiveIntegerField(default=5)
    # Plafonds ABSOLUS de taille d'une table pour cette entreprise, fixés par le super-administrateur : ils bornent toujours les réglages de
    # l'administrateur de l'entreprise (ConfigurationIA.limite_lignes / limite_colonnes, « 0 = illimité » compris). Voir TableDynamique.plafond_absolu_*.
    plafond_lignes_max = models.PositiveIntegerField(default=100_000)
    plafond_colonnes_max = models.PositiveIntegerField(default=1000)

    def __str__(self):
        return self.nom

    def save(self, *args, **kwargs):
        if self.pk is None and self.acces_jusqua is None:   # nouvelle entreprise : début de l'essai (saison restaurée : même échéance que l'entreprise d'origine)
            if self.saison_de_id:
                self.acces_jusqua = Entreprise.objects.filter(pk=self.saison_de_id).values_list("acces_jusqua", flat=True).first()
            else:
                from ..acces_entreprise import fin_essai_par_defaut
                self.acces_jusqua = fin_essai_par_defaut()
                self.en_essai = self.acces_jusqua is not None
        super().save(*args, **kwargs)

    def coordonnees_document(self) -> str:
        """Ligne de contact condensée (tél · email · site) pour l'en-tête
        des documents générés. Omet silencieusement les champs vides."""
        parties = []
        if self.telephone_service_client:
            parties.append(_gtf("engine/models/_part_001.py:76:28", v1=(self.telephone_service_client)))
        if self.email_contact:
            parties.append(self.email_contact)
        if self.site_web:
            parties.append(self.site_web)
        return "  ·  ".join(parties)


class MembreEntreprise(models.Model):
    """Isole chaque utilisateur dans son espace entreprise."""
    ROLE_CHOICES = [("admin", "Administrateur"), ("caisse", "Caisse entreprise"), ("agent", tl("engine/models/_part_001.py:g:73:60")), ("lecteur", tl("engine/models/_part_001.py:g:73:82"))]
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="membres")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="lecteur")
    photo_profil = models.ImageField(upload_to="profils/", blank=True, null=True, verbose_name=tl("engine/models/_part_001.py:71:97"))
    indicatif_pays = models.CharField(max_length=10, blank=True, default="+242", verbose_name=tl("engine/models/_part_001.py:72:96"))
    telephone = models.CharField(max_length=30, blank=True, default="", verbose_name=tl("engine/models/_part_001.py:73:87"))
    numero_client = models.PositiveIntegerField(null=True, blank=True, verbose_name=tl("engine/models/_part_001.py:74:86"))
    # Permissions dynamiques : liste de slugs de vues autorisées, ex.
    # ["dashboard","donnees","documents","calcul","ia","configuration_documents"].
    # None/vide = pas de restriction (comportement par role inchange). Ignore
    # pour role="admin" (voit toujours tout) et role="lecteur" (deja filtre
    # par accessible_client). Concerne uniquement role="agent".
    vues_autorisees = models.JSONField(null=True, blank=True, verbose_name=tl("engine/models/_part_001.py:80:77"))
    # Pouvoirs de l'administrateur confiés à CET agent (liste de domaines, voir engine/delegation.py). Indépendant de vues_autorisees.
    delegations = models.JSONField(null=True, blank=True)
    # Phase 9 (cahier des charges Ontology, « SÉCURITÉ FINE ») : granularité
    # objet/champ AU-DELÀ du rôle global et de vues_autorisees (qui ne
    # restreint que des ONGLETS d'interface, pas des tables précises).
    # None/vide = pas de restriction (même convention que vues_autorisees) ;
    # ignoré pour role="admin" (voit toujours tout) et role="lecteur" (déjà
    # régi par accessible_client + table_cliente/enregistrement_client) —
    # concerne uniquement role="agent", comme vues_autorisees.
    tables_autorisees = models.JSONField(
        null=True, blank=True, verbose_name=tl("engine/models/_part_001.py:89:46"),
        help_text=td("engine/models/_part_001.py:g:97:19"),
    )
    champs_masques = models.JSONField(
        null=True, blank=True, verbose_name=tl("engine/models/_part_001.py:94:46"),
        help_text=tl("engine/models/_part_001.py:g:101:19"),
    )
    # Phase 9 (suite, cahier des charges Ontology, « SÉCURITÉ FINE ») : la
    # granularité Objet (tables_autorisees) et Champ (champs_masques)
    # restreignait déjà QUELLES tables/colonnes un agent voit dans le
    # graphe Ontology et dans la grille. Il manquait la granularité
    # « Relation » : restreindre un TYPE DE LIEN précis (ex. "mesure",
    # "calcule", "affiche" — voir ontologie.DESCRIPTIONS_RELATIONS) sans
    # retirer les nœuds eux-mêmes. Utile pour un agent qui doit voir les
    # tables et leurs colonnes mais pas, par exemple, les KPI/dashboards
    # qui en dérivent (relations "mesure"/"affiche"), ou la lignée de
    # calcul ("calcule"). Même convention que les deux champs ci-dessus :
    # None/vide = aucune relation masquée (comportement par défaut) ;
    # ignoré pour role="admin" (voit toujours tout) et role="lecteur"
    # (déjà régi par accessible_client) — concerne uniquement role="agent".
    relations_masquees = models.JSONField(
        null=True, blank=True, verbose_name=tl("engine/models/_part_001.py:112:46"),
        help_text=tl("engine/models/_part_001.py:g:119:19"),
    )
    table_cliente = models.ForeignKey(
        "TableDynamique", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="membres_clients", verbose_name=tl("engine/models/_part_001.py:120:55"),
        help_text=tl("engine/models/_part_001.py:121:20"),
    )
    enregistrement_client = models.ForeignKey(
        "Enregistrement", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="membres_clients", verbose_name=tl("engine/models/_part_001.py:125:55"),
        help_text=tl("engine/models/_part_001.py:g:132:19"),
    )

    actif = models.BooleanField(default=True)   # environnement (saison) actif de l'utilisateur : les .first() existants le renvoient grâce au tri ci-dessous

    class Meta:
        unique_together = ("utilisateur", "entreprise")
        ordering = ("-actif", "pk")

    def save(self, *args, **kwargs):
        """Isolation atomique des clients : la table et la ligne d'un client appartiennent TOUJOURS à l'entreprise du membre."""
        for nom in ("table_cliente", "enregistrement_client"):
            cible = getattr(self, nom, None)
            if cible is None:
                continue
            e_cible = cible.entreprise_id if nom == "table_cliente" else cible.table.entreprise_id
            if e_cible != self.entreprise_id:
                raise ValueError(f"MembreEntreprise.{nom} : appartient à une autre entreprise")
        super().save(*args, **kwargs)


class CleAPI(models.Model):
    """Clé API permettant à une solution externe indépendante d'appeler les
    endpoints /api/... de ce moteur SANS session navigateur (voir
    engine/middleware.py::CleAPIAuthMiddleware).

    Principe central : la clé n'est PAS un nouvel utilisateur fantôme avec
    ses propres droits — elle INCARNE un MembreEntreprise réel et existant.
    Une fois authentifiée, la requête a exactement le même rôle, les mêmes
    vues_autorisees et passe par les mêmes vérifications d'appartenance déjà
    en place (ex. _interdit_lecteur, _interdit_non_proprietaire_tache) qu'un
    appel fait depuis le navigateur de ce membre — aucune vue existante n'a
    besoin d'être dupliquée ou réécrite pour l'accès externe.

    Seul un admin de l'entreprise peut créer/révoquer une clé (pour
    lui-même ou pour un autre membre de son entreprise).

    Le secret n'est JAMAIS stocké en clair : seul son hash SHA-256 est
    conservé (`cle_hachee`), comme un mot de passe — la valeur en clair
    n'est montrée qu'UNE SEULE FOIS, à la création (voir
    views.api_creer_cle_api)."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="cles_api")
    membre = models.ForeignKey(
        MembreEntreprise, on_delete=models.CASCADE, related_name="cles_api",
        help_text=tl("engine/models/_part_001.py:159:20"),
    )
    nom = models.CharField(max_length=100, blank=True, default="", help_text=tl("engine/models/_part_001.py:161:79"))
    prefixe = models.CharField(max_length=12, editable=False, help_text=tl("engine/models/_part_001.py:162:74"))
    cle_hachee = models.CharField(max_length=64, unique=True, editable=False)
    actif = models.BooleanField(default=True)
    cree_par = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)
    derniere_utilisation = models.DateTimeField(null=True, blank=True)
    profil = models.BooleanField(
        default=False,
        help_text=td("engine/models/_part_001.py:g:170:19"),
    )
    cle_visible = models.CharField(
        max_length=64, blank=True, default="", editable=False,
        help_text=tl("engine/models/_part_001.py:g:180:19"),
    )

    class Meta:
        ordering = ["-cree_le"]

    def __str__(self):
        return f"{self.nom or _gt('rg6.msg.034')} ({self.prefixe}…)"

    @staticmethod
    def jeton_profil(membre, cree_par=None):
        """Jeton de profil actif du membre (créé au premier affichage s'il n'existe pas encore)."""
        existant = CleAPI.objects.filter(membre=membre, profil=True, actif=True).exclude(cle_visible="").first()
        if existant is not None:
            return existant
        return CleAPI.regenerer_profil(membre, cree_par or membre.utilisateur)

    @staticmethod
    def regenerer_profil(membre, cree_par):
        """Révoque les jetons de profil actuels du membre et en crée un nouveau, visible sur sa page profil."""
        CleAPI.objects.filter(membre=membre, profil=True, actif=True).update(actif=False)
        instance, _secret = CleAPI.generer(membre.entreprise, membre, t("engine/models/_part_001.py:g:201:71"), cree_par, profil=True)
        return instance

    @staticmethod
    def generer(entreprise, membre, nom, cree_par, profil=False):
        """Génère un nouveau secret aléatoire, crée l'enregistrement (hash
        uniquement) et retourne (instance, secret_en_clair) — le secret en
        clair n'est JAMAIS récupérable ensuite, ni par l'admin lui-même."""
        import hashlib
        import secrets
        if membre.entreprise_id != entreprise.id:   # isolation atomique : une clé est rangée dans l'entreprise de son membre, jamais ailleurs
            raise ValueError("CleAPI : le membre n'appartient pas à l'entreprise de la clé")
        secret = secrets.token_urlsafe(32)
        instance = CleAPI.objects.create(
            entreprise=entreprise, membre=membre, nom=(nom or "").strip(),
            prefixe=secret[:8], cle_hachee=hashlib.sha256(secret.encode()).hexdigest(),
            cree_par=cree_par, profil=profil, cle_visible=secret if profil else "",
        )
        return instance, secret

    @staticmethod
    def authentifier(secret):
        """Retourne la CleAPI active correspondant à `secret`, ou None."""
        import hashlib
        if not secret:
            return None
        hachee = hashlib.sha256(secret.encode()).hexdigest()
        from django.db.models import F
        return (CleAPI.objects.filter(cle_hachee=hachee, actif=True, membre__entreprise_id=F("entreprise_id"))   # clé incohérente = inconnue
                .select_related("membre__utilisateur", "entreprise").first())


# ─────────────────────────────────────────────────────────────
# MOTEUR DE DONNEES DYNAMIQUES (data_engine + form_engine fusionnes)
# ─────────────────────────────────────────────────────────────
class GroupeTables(models.Model):
    """Regroupe plusieurs TableDynamique en un seul ensemble logique
    (« grande table »), pour DEUX usages distincts qui partagent le même
    mécanisme :

    1. Découpage automatique — quand un import (fichier CSV/Excel OU
       synchronisation d'une base de données externe) dépasse la limite de
       lignes configurée pour l'entreprise (ConfigurationIA.limite_lignes),
       on ne rejette plus le fichier : on crée autant de TableDynamique
       « parties » que nécessaire (voir import_fichier.py) et on les
       rattache toutes à un même GroupeTables (cree_automatiquement=True),
       chaque partie sachant en plus, via TableDynamique.partie_precedente,
       de quelle partie elle est la suite directe.

    2. Rattachement manuel — un utilisateur peut aussi lier entre elles des
       tables déjà existantes et totalement indépendantes (créées à des
       moments différents, par des imports différents) pour les traiter et
       les interroger comme un seul ensemble cohérent, sans dupliquer ni
       fusionner physiquement leurs données.

    Dans les deux cas, chaque TableDynamique membre reste une table
    physique à part entière (ses propres ChampDynamique/Enregistrement) :
    le GroupeTables n'est qu'un regroupement logique, jamais une fusion.
    """

    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="groupes_tables"
    )
    nom = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    cree_automatiquement = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_001.py:235:23"),
        help_text=tl("engine/models/_part_001.py:g:266:19"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)
    # FUSION (voir groupe_fusion.py) : quand le groupe est fusionné, une table unique (TableDynamique.groupe_fusionne) porte les
    # lignes de toutes ses parties ; les parties restent intactes (tables, lignes, fichiers Parquet) pour pouvoir défusionner.
    # L'état « fusionné » est DÉRIVÉ (existence de cette table), jamais stocké : supprimer la table fusionnée = défusionner.
    fusionne_le = models.DateTimeField(null=True, blank=True)
    empreinte_fusion = models.CharField(max_length=200, blank=True, default="")   # état de la table fusionnée à la fusion (détecte les modifications faites depuis)

    class Meta:
        verbose_name = t("engine/models/_part_001.py:244:25")
        verbose_name_plural = t("engine/models/_part_001.py:245:32")
        ordering = ["nom"]

    def __str__(self):
        return self.nom

    @property
    def nb_tables(self):
        return self.tables.count()

    @property
    def table_fusionnee(self):
        """La table unique qui réunit toutes les parties du groupe (groupe fusionné), ou None."""
        return self.tables_de_fusion.order_by("id").first()

    @property
    def est_fusionne(self):
        return self.tables_de_fusion.exists()

    @property
    def nb_lignes_total(self):
        """Somme des lignes de TOUTES les tables membres — c'est ce nombre,
        et non celui d'une seule partie, qui représente le volume réel de
        « la grande table » que ce groupe matérialise."""
        total = 0
        for table in self.tables.all():
            total += table.enregistrements.count()
        return total


class ConteneurTables(models.Model):
    """Associe des tables et des groupes HÉTÉROGÈNES et indépendants (structures différentes) sous un
    même nom. Contrairement à GroupeTables (même structure, lignes agrégées), un conteneur ne fusionne
    ni ne relie rien : c'est un simple rangement. Un GroupeTables y compte comme UNE table (jamais ses
    parties une à une) ; chaque membre reste analysable seul (dashboards, statistiques, IA...)."""

    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="conteneurs_tables")
    nom = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    tables = models.ManyToManyField("TableDynamique", blank=True, related_name="conteneurs")
    groupes = models.ManyToManyField(GroupeTables, blank=True, related_name="conteneurs")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class TableDynamique(models.Model):
    """Une 'table' definie par le client, sans migration de code."""

    LIMITE_LIGNES_DEFAUT = 1000
    PLAFOND_ABSOLU_LIGNES = 100_000   # mesure de sécurité : jamais plus de 100 000 lignes par table (au-delà, calculs très lents) ; seuil d'alerte : SEUIL_ALERTE_LIGNES
    SEUIL_ALERTE_LIGNES = 90_000

    STOCKAGE_SQL = "sql"
    STOCKAGE_NOSQL = "nosql"
    STOCKAGE_CHOICES = [
        (STOCKAGE_SQL, "SQL (relationnel)"),
        (STOCKAGE_NOSQL, "NoSQL (JSON)"),
    ]
    type_stockage = models.CharField(
        max_length=10, choices=STOCKAGE_CHOICES, default=STOCKAGE_SQL,
        verbose_name=tl("engine/models/_part_001.py:279:23"),
        help_text=td("engine/models/_part_001.py:g:320:19"),
    )

    @property
    def est_nosql(self):
        return self.type_stockage == self.STOCKAGE_NOSQL
    """Plafond de lignes de secours, utilise UNIQUEMENT si l'entreprise n'a
    pas encore de ConfigurationIA (cas limite, avant tout enregistrement).
    Le reglage effectif et editable vit desormais sur ConfigurationIA.limite_lignes
    (un seul reglage, global a toute l'entreprise, configure depuis le
    panneau lateral) : il s'applique de facon identique a TOUTE table quelle
    que soit son origine, table "native" creee via le constructeur de table
    (formulaire "Nouvelle table") comme table issue d'un import de fichier
    (provient_fichier=True). Il n'existe plus de reglage par table."""

    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="tables")
    nom = models.CharField(max_length=120)
    slug = models.SlugField()
    description = models.TextField(blank=True)
    taille_echantillon = models.PositiveIntegerField(
        default=5,
        help_text=tl("engine/models/_part_001.py:308:20"),
    )
    prompt_rapport = models.TextField(
        blank=True, default="",
        help_text=td("engine/models/_part_001.py:g:352:19"),
    )
    prompt_rapport_document = models.TextField(
        blank=True, default="",
        help_text=tl("engine/models/_part_001.py:g:370:19"),
    )
    # Couleurs des colonnes supprimées {slug: "#rrggbb"} : une colonne recréée plus tard sous le même slug retrouve sa couleur.
    couleurs_retirees = models.JSONField(blank=True, default=dict)
    timeout_execution_secondes = models.PositiveIntegerField(
        default=30,
        help_text=tl("engine/models/_part_001.py:g:379:19"),
    )
    timeout_inference_secondes = models.PositiveIntegerField(
        default=120,
        help_text=tl("engine/models/_part_001.py:g:384:19"),
    )
    provient_fichier = models.BooleanField(
        default=False,
        help_text=tl("engine/models/_part_001.py:g:389:19"),
    )
    nom_fichier_origine = models.CharField(
        max_length=255, blank=True,
        help_text=tl("engine/models/_part_001.py:342:20"),
    )
    connexion_externe_origine = models.ForeignKey(
        "ConnexionExterne", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="tables_importees",
        help_text=tl("engine/models/_part_001.py:g:399:19"),
    )
    nom_source_distante = models.CharField(
        max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_001.py:g:405:19"),
    )
    url_source = models.CharField(max_length=500, blank=True, default="")   # lien d'origine : Google Sheet ou fichier en ligne CSV/Excel/Parquet (indépendant du nom du fichier ou de la table)
    jeton_source = models.TextField(blank=True, default="")   # « Online Sheet sécurisé » : jeton d'accès CHIFFRÉ (source_en_ligne.chiffrer_jeton), jamais en clair ni renvoyé au navigateur
    jeton_a_reconfigurer = models.BooleanField(default=False)   # le serveur distant a refusé le jeton (régénéré / révoqué) : relecture automatique suspendue jusqu'à ce qu'un nouveau jeton soit saisi (bouton 🔑)
    lignes_source = models.PositiveIntegerField(default=0)   # lignes de la source (Google Sheet / fichier en ligne) déjà lues : la relecture ne prend que la suite
    intervalle_source = models.PositiveIntegerField(default=5)   # minutes entre deux relectures automatiques à l'ouverture ; 0 = manuel (bouton ↻)

    # ── Rattachement à un ensemble logique de tables (« grande table »),
    # que ce rattachement soit automatique (découpage d'un import trop
    # volumineux) ou manuel (tables indépendantes liées entre elles par
    # l'utilisateur) — voir GroupeTables ci-dessus. ──
    groupe = models.ForeignKey(
        GroupeTables, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="tables",
        verbose_name=tl("engine/models/_part_001.py:364:23"),
        help_text=td("engine/models/_part_001.py:g:398:19"),
    )
    partie_index = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name=tl("engine/models/_part_001.py:370:23"),
        help_text=td("engine/models/_part_001.py:g:404:19"),
    )
    partie_total = models.PositiveIntegerField(
        null=True, blank=True,
        verbose_name=tl("engine/models/_part_001.py:378:23"),
        help_text=tl("engine/models/_part_001.py:g:431:19"),
    )
    partie_precedente = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="partie_suivante",
        verbose_name=tl("engine/models/_part_001.py:387:23"),
        help_text=tl("engine/models/_part_001.py:g:440:19"),
    )
    # Table issue de la FUSION d'un groupe (groupe_fusion.py) : le groupe dont elle réunit toutes les parties.
    groupe_fusionne = models.ForeignKey(
        GroupeTables, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="tables_de_fusion",
    )
    # ── Extension par colonnes (limite de colonnes atteinte) ────────────
    # Distinct de GroupeTables (qui exige la même structure pour toutes ses
    # tables — voir _erreur_structure_groupe) : ici les deux tables ont au
    # contraire des colonnes DIFFÉRENTES (la suite qui n'entrait plus dans
    # la première). Purement organisationnel : jamais lu par les moteurs de
    # calcul/dashboard/IA, juste un renvoi affiché à l'utilisateur.
    table_extension_colonnes = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="table_source_colonnes",
        verbose_name=tl("engine/models/_part_001.py:402:23"),
        help_text=tl("engine/models/_part_001.py:g:455:19"),
    )
    expose_client = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_001.py:408:23"),
        help_text=td("engine/models/_part_001.py:g:435:19"),
    )
    masquer_formulaire_client = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_001.py:415:23"),
        help_text=td("engine/models/_part_001.py:g:442:19"),
    )
    # Formulaire conditionnel (engine/formulaire_conditionnel.py) : sections + renvois selon les réponses, pour le formulaire de
    # saisie client. N'altère JAMAIS les colonnes de la table : les champs des sections non traversées restent simplement vides.
    structure_formulaire = models.JSONField(default=dict, blank=True)
    est_table_membres = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_001.py:425:23"),
        help_text=td("engine/models/_part_001.py:g:452:19"),
    )
    # Table de PRODUITS : variante de la table agents et clients (matricule, QR, cartes/étiquettes identiques)
    # SANS identifiant de connexion (code_acces_client) : un produit n'a pas de compte. Ne s'utilise
    # qu'avec est_table_membres=True ; décochée = table agents/clients classique.
    est_table_produits = models.BooleanField(default=False)
    # Table « système » : vue en lecture seule d'une table du code (membres, wallet, paiement, messagerie — voir engine/tables_systeme.py).
    # Vide = table dynamique ordinaire. Renseigné = clé de la source ; les lignes ne sont jamais dans l'EAV, elles sont lues dans le modèle
    # Django au moment de l'analyse : les pages d'analyse la traitent comme n'importe quelle table, sans page spécialisée.
    source_systeme = models.CharField(max_length=40, blank=True, default="", db_index=True)
    denaturee = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_001.py:436:23"),
        help_text=td("engine/models/_part_001.py:g:463:19"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    @property
    def a_identifiants_connexion(self):
        """True pour une table agents et clients classique (code d'accès individuel par ligne) ;
        False pour une table de produits ou une table ordinaire."""
        return bool(self.est_table_membres and not self.est_table_produits)

    def detail_denaturation(self):
        """Détail (dérivé, jamais stocké) du statut 'dénaturée' : fusions multi-cellules, cellules
        seules retypées, cellules « formule »."""
        champs = list(self.champs.order_by("ordre"))
        d = {"fusions": 0, "retypees": 0, "formules": 0}
        for f in self.fusions_cellules.all():
            if f.ligne_fin > f.ligne_debut or f.col_fin > f.col_debut:
                d["fusions"] += 1
            elif f.type_affiche != (champs[f.col_debut].type_champ if f.col_debut < len(champs) else None):
                d["retypees"] += 1
            d["formules"] += f.type_affiche == "formule"
        return d

    def recalculer_denaturee(self):
        """Recalcule et persiste automatiquement le statut 'dénaturée' de la table.

        Une table est DÉNATURÉE dès qu'elle sert à composer un DOCUMENT (reçu, facture,
        bulletin…) plutôt qu'à être une grille de données linéaire, c'est-à-dire quand au
        moins une fusion (FusionCelluleTable) : couvre plusieurs cellules, OU donne à une
        cellule un type différent de celui de sa colonne (dont « formule »). Les colonnes
        ne respectent alors plus leur rôle : la table est ignorée par les calculs
        automatiques et les dashboards (compromis assumé), ses données restant consultables
        et la structure étant reproduite telle quelle dans les PDF. Aucun toggle manuel :
        le statut est entièrement dérivé de la structure réelle. À appeler après toute
        création / modification / suppression de fusion."""
        d = self.detail_denaturation()
        nouvelle_valeur = bool(d["fusions"] or d["retypees"])
        if nouvelle_valeur != self.denaturee:
            self.denaturee = nouvelle_valeur
            self.save(update_fields=["denaturee"])
        return self.denaturee

    @property
    def parties_du_groupe(self):
        """Toutes les tables du même GroupeTables (celle-ci comprise),
        triées par position — pratique pour parcourir « la grande table »
        complète, découpage automatique ou rattachement manuel confondus.
        Liste vide si cette table n'appartient à aucun groupe."""
        if not self.groupe_id:
            return TableDynamique.objects.none()
        return self.groupe.tables.order_by("partie_index", "id")

    @property
    def est_derniere_du_groupe(self):
        """True si la table est autonome ou la DERNIÈRE de son GroupeTables (plus grand partie_index)."""
        if not self.groupe_id:
            return True
        derniere = self.groupe.tables.order_by(models.F("partie_index").desc(nulls_last=True), "-id").first()
        return derniere is None or derniere.pk == self.pk

    @property
    def signature_structure(self):
        """Structure de la table : ensemble de ses colonnes (slug, type). Deux tables ont « la même
        structure » quand leurs signatures sont égales — condition pour être dans un même GroupeTables."""
        return frozenset(self.champs.values_list("slug", "type_champ"))

    @property
    def enregistrements_groupe(self):
        """Lignes de la table — ou, si elle appartient à un GroupeTables, de TOUTES les tables du
        groupe (hors tables dénaturées), dans l'ordre des parties puis chronologique. Point d'entrée
        unique des traitements agrégés (calculs, dashboards, KPI, analyses IA, résumé PDF). Ne PAS
        l'utiliser pour écrire des lignes, ni là où une ligne doit rester rattachée à SA table
        (grille, colonnes formule, jeux d'entraînement IA indexés par id de champ)."""
        if not self.groupe_id:
            return self.enregistrements.order_by("cree_le", "id")
        from ._part_002 import Enregistrement  # import local (réf. avant sa définition)
        return Enregistrement.objects.filter(
            table__groupe_id=self.groupe_id, table__denaturee=False
        ).order_by("table__partie_index", "table_id", "cree_le", "id")   # départage total : même ordre que les fichiers Parquet (parquet_store)

    @property
    def accessible_client(self):
        """True si la table doit être accessible cote client (role Lecteur) :
        des lors qu'AU MOINS une des deux bascules (formulaire ou recherche)
        est active. Si les deux sont desactivees, la table est totalement
        invisible/inaccessible cote client, meme si elle contient des donnees."""
        # La table des agents et clients (table de membres) n'est JAMAIS accessible à un client, même exposée.
        if self.est_table_membres:
            return False
        return bool(self.expose_client or self.masquer_formulaire_client)

    @cached_property
    def plafond_lignes(self):
        """Plafond de lignes effectif pour cette table : lit desormais le
        reglage GLOBAL de l'entreprise (ConfigurationIA.limite_lignes), le
        meme pour toutes les tables (natives ou issues d'un import de
        fichier) — il n'y a plus de valeur par table. Retourne None si
        aucune limite ne s'applique (reglage global = 0, illimite).
        Source unique de verite : reference par api_lignes (ajout ligne par
        ligne, admin ou client), apps.py (propagation GroupePartage) et par
        import_fichier.py (import en masse) pour ne jamais avoir deux
        limites divergentes."""
        return self.plafond_pour_entreprise(self.entreprise_id)

    @classmethod
    def plafond_pour_entreprise(cls, entreprise):
        """Nombre MAXIMUM de lignes d'UNE table pour cette entreprise (objet ou id) : le réglage de l'entreprise, toujours borné par\nPLAFOND_ABSOLU_LIGNES (0 = « illimité » devient le plafond absolu). Source unique : ajout de ligne, import de fichier, mise à jour\net base externe s'en servent tous, donc un import qui dépasse la limite crée AUTOMATIQUEMENT une table suivante pour le reste."""
        from ._part_003 import ConfigurationIA  # import local (réf. avant sa définition)
        cfg = ConfigurationIA.objects.filter(entreprise=entreprise).first()
        limite = cfg.limite_lignes if cfg is not None else cls.LIMITE_LIGNES_DEFAUT
        absolu = cls.plafond_absolu_lignes(entreprise)
        if limite == 0:   # « illimité » réglé par l'entreprise : le plafond absolu (fixé par le super-administrateur) s'applique quand même
            return absolu
        return min(limite, absolu)

    @classmethod
    def plafond_absolu_lignes(cls, entreprise):
        """Plafond absolu de lignes d'une table pour cette entreprise (objet ou id) : celui du super-administrateur (Entreprise.plafond_lignes_max)."""
        v = Entreprise.objects.filter(pk=getattr(entreprise, "pk", entreprise)).values_list("plafond_lignes_max", flat=True).first()
        return v or cls.PLAFOND_ABSOLU_LIGNES

    @classmethod
    def plafond_absolu_colonnes(cls, entreprise):
        v = Entreprise.objects.filter(pk=getattr(entreprise, "pk", entreprise)).values_list("plafond_colonnes_max", flat=True).first()
        return v or 1000

    @cached_property
    def plafond_colonnes(self):
        """Plafond de colonnes effectif pour cette table : lit le réglage
        GLOBAL de l'entreprise (ConfigurationIA.limite_colonnes), le même
        pour toutes les tables (natives ou issues d'un import de fichier).
        Retourne None si aucune limite (réglage = 0, illimité)."""
        from ._part_003 import ConfigurationIA  # import local (réf. avant sa définition)
        cfg = ConfigurationIA.objects.filter(entreprise_id=self.entreprise_id).first()
        limite = cfg.limite_colonnes if cfg is not None else ConfigurationIA.LIMITE_COLONNES_DEFAUT
        absolu = self.plafond_absolu_colonnes(self.entreprise_id)   # fixé par le super-administrateur : borne aussi « 0 = illimité »
        return absolu if limite == 0 else min(limite, absolu)

    # ── Photo d'identité propre à cette table ──────────────────────────────
    afficher_photo_identite_document = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_001.py:538:23"),
    )
    photo_identite_document = models.ImageField(
        upload_to="tables/photo_identite/", blank=True, null=True,
        verbose_name=tl("engine/models/_part_001.py:542:23"),
        help_text=tl("engine/models/_part_001.py:543:20"),
    )

    # ── Carte d'identité générée (recto/verso) ──────────────────────────────
    # Réutilise entièrement le moteur existant : la photo de l'agent est un
    # champ de type "image" comme un autre (donc déjà capturable caméra, cf.
    # bouton "📷 Photo" générique des champs image), les données viennent de
    # ValeurChamp. Ici on stocke juste le thème (image de fond fournie par
    # l'admin, pas de recréation de design) et le positionnement des champs.
    theme_carte_recto = models.ImageField(
        upload_to="cartes/themes/", blank=True, null=True, verbose_name=tl("engine/models/_part_001.py:553:74"),
    )
    theme_carte_verso = models.ImageField(
        upload_to="cartes/themes/", blank=True, null=True, verbose_name=tl("engine/models/_part_001.py:556:74"),
    )
    config_carte = models.JSONField(
        blank=True, null=True, verbose_name=tl("engine/models/_part_001.py:559:46"),
        help_text=td("engine/models/_part_001.py:g:611:19"),
    )
    # Signature et cachet reutilisables sur la carte, configures directement
    # depuis l'interface de la carte (pas besoin de passer par la config
    # documents generale) — memes principes que le logo entreprise (Entreprise.logo) :
    # une image fixe, positionnee automatiquement, activable/desactivable par face
    # via config_carte (signature_recto/verso, cachet_recto/verso).
    carte_signature_image = models.ImageField(
        upload_to="cartes/signatures/", blank=True, null=True, verbose_name=tl("engine/models/_part_001.py:577:78"),
    )
    carte_signature_nom = models.CharField(
        max_length=150, blank=True, default="", verbose_name=tl("engine/models/_part_001.py:580:63"),
    )
    carte_cachet_image = models.ImageField(
        upload_to="cartes/cachets/", blank=True, null=True, verbose_name=tl("engine/models/_part_001.py:583:75"),
    )
    # ── QR code : la generation dynamique (cv2 + lib "qrcode") reste possible
    # (config_carte.qrcode_mode: "hash"/"resume"/"champ", voir reports.py),
    # mais s'avere capricieuse selon l'environnement (lib absente -> case
    # vide sur la carte). Systeme de secours simple et fiable : un QR deja
    # genere ailleurs, uploade tel quel, meme principe que signature/cachet
    # (image fixe, collee automatiquement). Actif quand config_carte
    # ("qrcode_source": "upload").
    carte_qrcode_image = models.ImageField(
        upload_to="cartes/qrcodes/", blank=True, null=True, verbose_name=tl("engine/models/_part_001.py:593:75"),
    )
    # ── Matricule global agents : identifiant obligatoire sur chaque carte,
    # unique sur TOUTES les tables d'agents de l'entreprise (pas seulement
    # celle-ci). Le prefixe est toujours choisi par l'administrateur ; le
    # reste est un suffixe aleatoire (voir Enregistrement.generer_matricule).
    matricule_prefixe = models.CharField(
        max_length=20, blank=True, default="", verbose_name=tl("engine/models/_part_001.py:600:62"),
    )

    class Meta:
        unique_together = ("entreprise", "slug")

    def __str__(self):
        return f"{self.entreprise.nom} / {self.nom}"


class GroupePartage(models.Model):
    """Une "colonne partagée" : regroupe un ou plusieurs ChampDynamique,
    typiquement de tables DIFFÉRENTES (mais peu importe), qui doivent rester
    synchronisés.

    Contrairement au mécanisme `relation` + `relation_bidirectionnelle` (qui
    exige de choisir une ligne précise dans une table liée via un menu
    déroulant), un champ membre d'un GroupePartage reste un champ NORMAL
    dans le formulaire — texte, nombre, date, etc. — avec sa propre saisie
    directe. C'est la propagation (voir apps.propager_groupe_partage) qui se
    charge de retrouver automatiquement, dans chaque autre table du groupe,
    la ligne à mettre à jour :
      1. une ligne existante qui n'a PAS ENCORE de valeur sur cette colonne
         précise est complétée en priorité (même si ses autres colonnes
         sont déjà remplies) ;
      2. à défaut, une nouvelle ligne est créée dans la table cible.
    """
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="groupes_partage"
    )
    nom = models.CharField(
        max_length=150, blank=True, default="",
        help_text=tl("engine/models/_part_001.py:632:20"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nom or tf("engine/models/_part_001.py:g:713:28", v1=self.pk)


class GroupePartageLigne(models.Model):
    """Un groupe de LIGNES partagées (synchronisées) entre tables — même
    principe que GroupePartage, mais au niveau d'une ligne (Enregistrement)
    entière plutôt que d'une colonne précise. Chaque ligne membre reste une
    ligne normale de sa propre table ; LignePartagee relie les
    enregistrements entre eux au sein d'un même groupe.

    Schéma figé par la migration 0142_partage_ligne_cellule (déjà présente
    dans le projet) : les noms de champs/related_name ci-dessous doivent
    rester identiques à celle-ci."""
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="groupes_partage_ligne"
    )
    nom = models.CharField(
        max_length=150, blank=True, default="",
        help_text=tl("engine/models/_part_001.py:655:20"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nom or tf("engine/models/_part_001.py:g:736:28", v1=self.pk)


class LignePartagee(models.Model):
    """Un membre (une ligne) d'un GroupePartageLigne."""
    groupe = models.ForeignKey(
        GroupePartageLigne, on_delete=models.CASCADE, related_name="lignes"
    )
    enregistrement = models.ForeignKey(
        "Enregistrement", on_delete=models.CASCADE, related_name="partages_ligne"
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("groupe", "enregistrement")
        indexes = [models.Index(fields=["enregistrement"])]

    def __str__(self):
        return tf("engine/models/_part_001.py:g:754:16", v1=self.enregistrement_id, v2=self.groupe_id)


class GroupePartageCellule(models.Model):
    """Un groupe de CELLULES partagées (synchronisées) — même principe que
    GroupePartageLigne, mais au niveau d'une cellule précise (ligne + champ),
    sans contrainte de nom de colonne ni de type de champ entre les deux
    cellules reliées."""
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="groupes_partage_cellule"
    )
    nom = models.CharField(
        max_length=150, blank=True, default="",
        help_text=tl("engine/models/_part_001.py:691:20"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nom or tf("engine/models/_part_001.py:g:772:28", v1=self.pk)


class CellulePartagee(models.Model):
    """Un membre (une cellule = ligne + champ) d'un GroupePartageCellule."""
    groupe = models.ForeignKey(
        GroupePartageCellule, on_delete=models.CASCADE, related_name="cellules"
    )
    enregistrement = models.ForeignKey(
        "Enregistrement", on_delete=models.CASCADE, related_name="partages_cellule"
    )
    champ = models.ForeignKey(
        "ChampDynamique", on_delete=models.CASCADE, related_name="partages_cellule"
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("groupe", "enregistrement", "champ")
        indexes = [models.Index(fields=["enregistrement", "champ"])]

    def __str__(self):
        return tf("engine/models/_part_001.py:g:793:16", v1=self.enregistrement_id, v2=self.champ_id, v3=self.groupe_id)


class FusionCelluleTable(models.Model):
    """Fusion de cellules façon Excel dans la grille de données d'une
    TableDynamique. Schéma figé par la migration 0143_fusioncellulettable
    (déjà présente dans le projet) : les noms de champs/related_name
    ci-dessous doivent rester identiques à celle-ci."""
    TYPE_CHOICES = [
        ('texte', td("engine/models/_part_001.py:g:777:19")), ('texte_long', td("engine/models/_part_001.py:g:777:44")),
        ('nombre', td("engine/models/_part_001.py:g:778:20")), ('decimal', td("engine/models/_part_002.py:type_decimal")),
        ('date', td("engine/models/_part_001.py:g:779:18")), ('heure', td("engine/models/_part_001.py:g:779:37")), ('datetime', td("engine/models/_part_001.py:g:779:60")), ('horodatage', td("engine/models/_part_001.py:g:779:93")), ('duree', td("engine/models/_part_002.py:type_duree")),
        ('booleen', td("engine/models/_part_002.py:type_booleen")), ('switch', td("engine/models/_part_002.py:type_switch")),
        ('barre_progression', td("engine/models/_part_001.py:g:781:31")),
        ('relation', td("engine/models/_part_002.py:type_relation")),
        ('email', td("engine/models/_part_002.py:type_email")), ('url', td("engine/models/_part_002.py:type_url")), ('telephone', td("engine/models/_part_002.py:type_telephone")),
        ('couleur', td("engine/models/_part_002.py:type_couleur")),
        ('choix_unique', td("engine/models/_part_002.py:type_choix_unique")), ('choix_multiple', td("engine/models/_part_002.py:type_choix_multiple")),
        ('monnaie', td("engine/models/_part_002.py:type_monnaie")), ('pourcentage', td("engine/models/_part_002.py:type_pourcentage")), ('note', td("engine/models/_part_002.py:type_note")),
        ('fichier', td("engine/models/_part_001.py:g:787:21")), ('image', td("engine/models/_part_002.py:type_image")), ('son', td("engine/models/_part_002.py:type_son")), ('video', td("engine/models/_part_001.py:g:787:79")),
        ('vecteur', td("engine/models/_part_002.py:type_vecteur")), ('matrice', td("engine/models/_part_002.py:type_matrice")),
        ('json', td("engine/models/_part_002.py:type_json")),
        ('formule', td("engine/models/_part_002.py:g:45:21")),
    ]
    table = models.ForeignKey(
        "TableDynamique", on_delete=models.CASCADE, related_name="fusions_cellules"
    )
    ligne_debut = models.PositiveIntegerField(help_text=tl("engine/models/_part_001.py:743:58"))
    ligne_fin = models.PositiveIntegerField()
    col_debut = models.PositiveIntegerField(help_text=tl("engine/models/_part_001.py:745:56"))
    col_fin = models.PositiveIntegerField()
    type_affiche = models.CharField(
        max_length=20, choices=TYPE_CHOICES, default='texte', blank=True,
        verbose_name=tl("engine/models/_part_001.py:749:23"),
    )
    valeur_affichee = models.TextField(
        blank=True, default='',
        verbose_name=tl("engine/models/_part_001.py:753:23"),
        help_text=td("engine/models/_part_001.py:g:806:19"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)
    # Ancrage sur les enregistrements (ids, pas de FK : supprimer une ligne ne supprime pas la fusion) :
    # les rangs ligne_debut/ligne_fin en sont re-dérivés (fusion_cellule.resynchroniser).
    enreg_debut = models.PositiveBigIntegerField(null=True, blank=True, default=None, db_index=True)
    enreg_fin = models.PositiveBigIntegerField(null=True, blank=True, default=None, db_index=True)
    # Repère (cree_le, id) des lignes d'ancrage : si une ligne disparaît (suppression en masse, SQL brut,
    # cascade), l'ancrage se replie sur la survivante la plus proche DANS la fusion, sans signal.
    ancre_debut_le = models.DateTimeField(null=True, blank=True, default=None)
    ancre_fin_le = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        ordering = ['ligne_debut', 'col_debut']

    def chevauche(self, ligne_debut, ligne_fin, col_debut, col_fin):
        """True si le rectangle donné (mêmes bornes incluses que ci-dessus)
        chevauche celui de cette fusion."""
        return (self.ligne_debut <= ligne_fin and ligne_debut <= self.ligne_fin and
                self.col_debut <= col_fin and col_debut <= self.col_fin)

    def __str__(self):
        return tf("engine/models/_part_001.py:g:854:16", v1=self.ligne_debut, v2=self.ligne_fin, v3=self.col_debut, v4=self.col_fin, v5=self.table_id)


class PartageInterAgent(models.Model):
    """Autorise UNE entreprise (émettrice) à partager un objet précis avec
    UNE AUTRE entreprise (destinataire) — deux comptes/agents distincts de
    la plateforme, alors que GroupePartage synchronise des colonnes ENTRE
    TABLES D'UNE MÊME entreprise.

    Deux régimes selon type_objet :
      - "colonne" : le GroupePartage visé devient utilisable par l'entreprise
        cible (elle peut y rattacher un ChampDynamique de SES propres
        tables) ; la synchronisation se fait alors dans les deux sens, comme
        pour un GroupePartage interne (voir apps.propager_groupe_partage).
      - "table" / "dashboard" / "ontologie" : l'entreprise cible obtient un
        accès EN LECTURE SEULE (recherche, cartes de calcul/documents pour
        une table ; simple consultation pour un dashboard ou une page
        d'ontologie) — jamais d'édition de structure ni des données par
        l'entreprise destinataire. Seul l'administrateur de l'entreprise
        émettrice modifie l'objet source.

    Un seul des 4 champs cible (groupe_partage/table/tableau_bord/
    page_ontologie) doit être renseigné, celui correspondant à type_objet —
    voir clean()."""
    TYPE_CHOICES = [
        ("colonne", td("engine/models/_part_001.py:g:854:21")),
        ("table", td("engine/models/_part_001.py:g:855:19")),
        ("dashboard", td("engine/models/_part_001.py:g:856:23")),
        ("ontologie", td("engine/models/_part_001.py:g:857:23")),
    ]

    entreprise_source = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="partages_emis",
        help_text=tl("engine/models/_part_001.py:802:20"),
    )
    entreprise_cible = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="partages_recus",
        help_text=tl("engine/models/_part_001.py:806:20"),
    )
    type_objet = models.CharField(max_length=12, choices=TYPE_CHOICES)

    groupe_partage = models.ForeignKey(
        "GroupePartage", null=True, blank=True, on_delete=models.CASCADE,
        related_name="partages_inter_agents",
    )
    table = models.ForeignKey(
        "TableDynamique", null=True, blank=True, on_delete=models.CASCADE,
        related_name="partages_inter_agents",
    )
    tableau_bord = models.ForeignKey(
        "TableauBordBI", null=True, blank=True, on_delete=models.CASCADE,
        related_name="partages_inter_agents",
    )
    page_ontologie = models.ForeignKey(
        "PageOntologie", null=True, blank=True, on_delete=models.CASCADE,
        related_name="partages_inter_agents",
    )

    actif = models.BooleanField(default=True)
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    CHAMP_PAR_TYPE = {
        "colonne": "groupe_partage",
        "table": "table",
        "dashboard": "tableau_bord",
        "ontologie": "page_ontologie",
    }

    class Meta:
        verbose_name = t("engine/models/_part_001.py:841:25")
        verbose_name_plural = t("engine/models/_part_001.py:842:32")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(entreprise_source=models.F("entreprise_cible")),
                name="partage_inter_agent_entreprises_distinctes",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        champ_attendu = self.CHAMP_PAR_TYPE.get(self.type_objet)
        if not champ_attendu:
            raise ValidationError({"type_objet": t("engine/models/_part_001.py:g:939:50")})
        for nom_champ in self.CHAMP_PAR_TYPE.values():
            valeur = getattr(self, f"{nom_champ}_id", None)
            attendu = (nom_champ == champ_attendu)
            if attendu and not valeur:
                raise ValidationError({nom_champ: t("engine/models/_part_001.py:g:944:51")})
            if not attendu and valeur:
                raise ValidationError({nom_champ: t("engine/models/_part_001.py:g:946:51")})

    @property
    def objet(self):
        return getattr(self, self.CHAMP_PAR_TYPE[self.type_objet])

    @property
    def modifiable_par_cible(self):
        """Seul le partage de type 'colonne' autorise l'entreprise cible à
        modifier (via son propre champ rattaché au groupe partagé)."""
        return self.type_objet == "colonne"

    def __str__(self):
        return (f"{self.entreprise_source.nom} → {self.entreprise_cible.nom} "
                f"({self.get_type_objet_display()})")




class LangueUtilisateur(models.Model):
    """Langue d'interface choisie par CHAQUE utilisateur (administrateur, agent, client) dans son Profil : propre au compte, donc la même sur
    tous ses appareils. Absente = il suit la langue par défaut du système (réglée par l'administrateur, Entreprise.langue_interface)."""
    utilisateur = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="langue_interface")
    code = models.CharField(max_length=10)


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
