from engine.i18n import td
from engine.i18n import tl
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from engine.i18n import td as _gtd  # noqa: E402
from engine.i18n import tfr as _gtfr_p9  # noqa: E402  # i18n-p9


class ChampDynamique(models.Model):
    TYPE_CHOICES = [
        ("texte", td("engine/models/_part_002.py:g:11:19")), ("texte_long", td("engine/models/_part_002.py:g:11:44")),
        ("nombre", td("engine/models/_part_002.py:g:12:20")), ("decimal", td("engine/models/_part_002.py:type_decimal")),
        ("date", td("engine/models/_part_002.py:g:13:18")), ("heure", td("engine/models/_part_002.py:g:13:37")), ("datetime", td("engine/models/_part_002.py:g:13:60")), ("duree", td("engine/models/_part_002.py:type_duree")),
        ("booleen", td("engine/models/_part_002.py:type_booleen")),
        # `switch` est stocké comme un booleen (mêmes valeurs `"1"`/`"0"`) mais
        # est rendu en UI par un toggle au lieu d'une checkbox. On garde la
        # distinction pour pouvoir proposer un visuel different plus tard sans
        # casser les donnees existantes.
        ("switch", td("engine/models/_part_002.py:type_switch")),
        # `barre_progression` stocke un entier 0..100 dans `valeur`. La
        # validation 0-100 est faite dans `valeur_typee()` et `clean_valeur_*`.
        ("barre_progression", td("engine/models/_part_002.py:g:22:31")),
        ("relation", td("engine/models/_part_002.py:type_relation")),
        ("email", td("engine/models/_part_002.py:type_email")), ("url", td("engine/models/_part_002.py:type_url")), ("telephone", td("engine/models/_part_002.py:type_telephone")),
        ("couleur", td("engine/models/_part_002.py:type_couleur")),
        ("choix_unique", td("engine/models/_part_002.py:type_choix_unique")), ("choix_multiple", td("engine/models/_part_002.py:type_choix_multiple")),
        ("monnaie", td("engine/models/_part_002.py:type_monnaie")), ("pourcentage", td("engine/models/_part_002.py:type_pourcentage")), ("note", td("engine/models/_part_002.py:type_note")),
        ("fichier", td("engine/models/_part_002.py:g:28:21")), ("image", td("engine/models/_part_002.py:type_image")), ("son", td("engine/models/_part_002.py:type_son")), ("video", td("engine/models/_part_002.py:g:28:79")),
        # ── Types tenseurs (module IA) : la valeur stockée est un JSON
        # (liste, ou liste de listes) representant un ndarray numpy. La
        # forme (dimensions) attendue est configuree UNE FOIS sur le champ,
        # via `options_choix` reutilise en "forme" pour ces deux types
        # (ex: "10" pour un vecteur de taille 10, "3,4" pour une matrice
        # 3x4). Voir `ChampDynamique.forme_tenseur`.
        ("vecteur", td("engine/models/_part_002.py:type_vecteur")), ("matrice", td("engine/models/_part_002.py:type_matrice")),
        ("geolocalisation", td("engine/models/_part_002.py:type_geolocalisation")),
        # `json` : la valeur stockee est un document JSON arbitraire (objet
        # ou liste), pas contraint a une forme de tenseur fixe comme
        # vecteur/matrice. Sert a construire un dataset "documents" (un
        # document par ligne) directement exploitable par l'entraineur pour
        # les modeles qui attendent une structure libre plutot qu'une grille
        # de features (ex: slm, ou tout pipeline qui parse lui-meme le JSON).
        # Voir TYPES_DOCUMENT et ia_integration._table_documents_json.
        ("json", td("engine/models/_part_002.py:type_json")),
        ("formule", td("engine/models/_part_002.py:g:45:21")),
        # `constante` : UNE valeur numérique fixe (ex. taux de TVA 0.2) saisie une fois dans la structure
        # (options_choix), recopiée sur TOUTES les lignes (calculs.assurer_constantes) : pour tous les
        # moteurs (formules, KPI, PDF, IA...) c'est une colonne numérique ordinaire ; jamais saisie par ligne.
        ("constante", td("engine/models/_part_002.py:g:49:23")),
        # `horodatage` : date et heure du SERVEUR, ecrites une fois a la creation de la ligne (calculs.assurer_constantes),
        # jamais saisies ni modifiees par le client ; pour tous les moteurs c'est un `datetime` ordinaire.
        ("horodatage", td("engine/models/_part_002.py:g:52:24")),
        # `id_client` : identifiant de QUI enregistre la ligne (numéro du client ; "agent" ou "administrateur"
        # si c'est un agent / l'admin), écrit une fois par le serveur (calculs.assurer_constantes), jamais saisi.
        ("id_client", td("engine/models/_part_002.py:g:55:23")),
        # `colonne` : menu des valeurs ACTUELLES d'une colonne d'une autre table (relation_vers, relation_colonne_source),
        # avec sa colonne nominale (relation_champ_affichage, exigee, non utilisee). Valeur stockee = texte ; pour les
        # moteurs c'est un choix unique (encodage_champs), jamais un nombre.
        ("colonne", td("engine/models/_part_002.py:g:59:21")),
        # Champs de flux TEMPS RÉEL (voir flux_media.py) : jamais une donnée, table à une seule cellule, ignorés partout.
        ("micro", td("engine/models/_part_002.py:g:61:19")), ("camera", td("engine/models/_part_002.py:g:61:59")),
        ("diffusion_micro", td("engine/models/_part_002.py:g:62:29")), ("diffusion_camera", td("engine/models/_part_002.py:g:62:73")),
    ]
    TYPES_CHOIX = ("choix_unique", "choix_multiple")
    TYPES_FICHIER = ("fichier", "image", "son", "video")
    # Types dont une table a UNE SEULE colonne de ce type est resolue en
    # liste de documents JSON (un par ligne) plutot qu'en matrice numerique —
    # voir ia_integration.resoudre_dataset / _table_documents_json.
    TYPES_DOCUMENT = ("texte", "texte_long", "json")
    TYPES_TENSEUR = ("vecteur", "matrice")
    # Champs dont la valeur stockee est une REFERENCE vers une image (URL
    # http(s) ou chemin relatif sous MEDIA_ROOT), pas les pixels eux-memes.
    # Pour l'entrainement IA (ia_integration.py), ces champs sont
    # telecharges/lus puis decodes en pixels a la volee. `forme_tenseur`
    # (reutilise via `options_choix`, comme pour TYPES_TENSEUR) configure la
    # taille de redimensionnement, ex "64,64" (niveaux de gris) ou
    # "64,64,3" (RGB).
    TYPES_IMAGE_REFERENCE = ("url", "image")
    # Champs dont la valeur stockee est une REFERENCE vers un fichier AUDIO
    # (URL http(s) ou chemin relatif sous MEDIA_ROOT). Meme principe que
    # TYPES_IMAGE_REFERENCE : decode a la volee en features numeriques pour
    # l'entrainement IA (voir ia_integration._charger_features_audio).
    # Formats geres : WAV nativement, MP3/OGG/etc. si pydub + ffmpeg sont
    # installes cote serveur.
    TYPES_AUDIO_REFERENCE = ("son",)
    # Champs dont la valeur stockee est une REFERENCE vers un fichier VIDEO
    # (URL http(s) ou chemin relatif sous MEDIA_ROOT). Meme principe que
    # TYPES_IMAGE_REFERENCE/TYPES_AUDIO_REFERENCE : jamais de copie des
    # donnees, le chemin/URL stocke EST le moyen d'acceder au fichier —
    # decode a la volee en frames a l'entrainement IA (voir
    # ia_integration._charger_features_video). `forme_tenseur` : ex
    # "8,64,64" (8 frames, niveaux de gris) ou "8,64,64,3" (RGB).
    TYPES_VIDEO_REFERENCE = ("video",)
    # Le switch partage le format de stockage de booleen (string "1" ou "0")
    # pour rester compatible avec tout ce qui lit `valeur_typee()`.
    TYPES_BOOLEEN = ("booleen", "switch")
    # Types numeriques stockes comme un nombre (entier 0-100 pour la
    # barre de progression).
    TYPES_PROGRESSION = ("barre_progression",)

    table = models.ForeignKey(TableDynamique, on_delete=models.CASCADE, related_name="champs")
    nom = models.CharField(max_length=120)
    slug = models.SlugField()
    type_champ = models.CharField(max_length=20, choices=TYPE_CHOICES)
    obligatoire = models.BooleanField(default=True)
    options_choix = models.TextField(
        blank=True,
        help_text=tl("engine/models/_part_002.py:93:20"),
    )
    relation_vers = models.ForeignKey(
        TableDynamique, null=True, blank=True, on_delete=models.SET_NULL, related_name="champs_relies"
    )
    # Slug du champ affiché comme label dans le dropdown (ex: "nom", "reference")
    relation_champ_affichage = models.CharField(
        max_length=120, blank=True, default="",
        help_text=tl("engine/models/_part_002.py:101:20"),
    )
    # Slug du champ dont la VALEUR est copiée dans la table enfant (ex: "prix", "code")
    relation_colonne_source = models.CharField(
        max_length=120, blank=True, default="",
        help_text=tl("engine/models/_part_002.py:106:20"),
    )
    # Rend le lien bidirectionnel : au lieu d'une simple copie a sens unique
    # (source -> enfant), la valeur peut aussi etre modifiee cote enfant et
    # remonte alors vers la ligne source, qui la repropage ensuite a TOUTES
    # ses autres tables liees (voir apps.propager_liens_champ). Cas d'usage
    # typique : une table centrale qui agrege les notes de plusieurs tables
    # "professeur" — modifier la note depuis la table centrale la met aussi
    # a jour dans la table du professeur concerne, et inversement.
    relation_bidirectionnelle = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_002.py:117:23"),
        help_text=tl("engine/models/_part_002.py:g:132:19"),
    )
    # ── Colonne partagée (GroupePartage) ──────────────────────────────
    # Indépendant du mécanisme relation/relation_bidirectionnelle ci-dessus :
    # pas de menu déroulant, ce champ reste un champ normal (texte, nombre,
    # date...) dans le formulaire. Voir GroupePartage et
    # apps.propager_groupe_partage pour la logique de synchronisation.
    groupe_partage = models.ForeignKey(
        GroupePartage, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="champs",
        verbose_name=tl("engine/models/_part_002.py:130:23"),
        help_text=td("engine/models/_part_002.py:g:144:19"),
    )
    ordre = models.PositiveIntegerField(default=0)
    # Taille maximale (Mo) d'un fichier téléversé dans ce champ (fichier / image / son / vidéo). NULL = AUCUNE limite (valeur par défaut) :
    # seul l'espace disque borne alors le téléversement. Réglée à la déclaration du champ ou à la restructuration de la table.
    taille_max_mo = models.PositiveIntegerField(null=True, blank=True, default=None)
    # Couleur de la colonne « #rrggbb » (vide = aucune) : écran + PDF. Voir couleur_colonne.py.
    couleur = models.CharField(max_length=9, blank=True, default="")

    @staticmethod
    def normaliser_constante(texte):
        """Texte canonique d'une valeur de constante (« 0,2 » -> « 0.2 »), ou None si ce n'est pas un nombre fini."""
        import math
        try:
            v = float(str(texte).strip().replace(",", "."))
        except (TypeError, ValueError):
            return None
        return repr(v) if math.isfinite(v) else None

    # ── Champ calculé (type_champ="formule") ───────────────────────────
    # Script Python `def formule(ligne): ...` exécuté dans le même bac à
    # sable que les formules de TableCalcul (executor.executer_formule_securisee),
    # mais avec UNE SEULE différence de cible : `ligne` est un dict des
    # autres valeurs de CETTE ligne (Enregistrement.to_dict(), champ courant
    # exclu), pas une colonne/table entière. Recalculé automatiquement à
    # chaque sauvegarde de la ligne (voir apps.py, signal post_save sur
    # ValeurChamp) et stocké comme un champ normal (ValeurChamp) : il
    # apparaît donc sans aucun changement dans la grille de la table, les
    # exports et les PDF, exactement comme un champ saisi manuellement,
    # simplement en lecture seule côté formulaire.
    formule_expression = models.TextField(
        blank=True, default="",
        verbose_name=tl("engine/models/_part_002.py:161:23"),
        help_text=tl("engine/models/_part_002.py:g:178:19"),
    )
    # ── Assistant visuel du champ calculé (constructeur pas-à-pas) ──────
    # Liste d'étapes ordonnées, ex:
    #   [{"operation": "addition", "termes": [{"type":"champ","slug":"note1"},
    #                                          {"type":"champ","slug":"note2"}]},
    #    {"operation": "multiplication", "termes": [{"type":"precedent"},
    #                                                {"type":"champ","slug":"coefficient"}]}]
    # `termes` de type "precedent" référence le résultat de l'étape
    # d'avant (interdit à l'étape 1) ; de type "constante" ({"type":"constante","valeur":0.2}) = nombre fixe.
    # Régénère formule_expression via
    # assistant_formule.generer_code_formule() à chaque sauvegarde de la
    # structure — voir views.api_modifier_structure_table. Null/vide =
    # champ en mode "code libre" : formule_expression n'est alors plus
    # touché automatiquement.
    formule_etapes_json = models.JSONField(
        blank=True, null=True, default=None,
        verbose_name=tl("engine/models/_part_002.py:184:23"),
    )

    class Meta:
        unique_together = ("table", "slug")
        ordering = ["ordre"]

    def __str__(self):
        return f"{self.table.nom}.{self.nom} ({self.type_champ})"

    @property
    def liste_options(self):
        return [o.strip() for o in self.options_choix.split(",") if o.strip()]

    @property
    def forme_tenseur(self):
        """Pour vecteur/matrice : dimensions attendues, ex (10,) ou (3, 4).

        Reutilise `options_choix` (champ deja existant, pas de migration
        supplementaire) : entiers separes par des virgules, ex "10" ou "3,4".
        Vide/invalide -> None (forme libre, pas de validation de dimension).
        """
        if (self.type_champ not in self.TYPES_TENSEUR
                and self.type_champ not in self.TYPES_IMAGE_REFERENCE
                and self.type_champ not in self.TYPES_AUDIO_REFERENCE
                and self.type_champ not in self.TYPES_VIDEO_REFERENCE):
            return None
        try:
            dims = tuple(int(d.strip()) for d in self.options_choix.split(",") if d.strip())
        except ValueError:
            return None
        return dims or None


class Enregistrement(models.Model):
    """Une ligne de la table dynamique. Les valeurs vivent dans ValeurChamp (EAV)."""
    table = models.ForeignKey(TableDynamique, on_delete=models.CASCADE, related_name="enregistrements")
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)
    # Createur de la ligne : sert a l'isolation entre agents (chaque agent
    # ne voit que ce qu'il a produit, l'admin voit tout). Nul pour les lignes
    # creees hors requete utilisateur (import en masse, automatisations).
    cree_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="enregistrements_crees",
    )
    # Poste d'agent retiré dont cette ligne a été reprise par l'administrateur (voir engine/reprise_poste.py) :
    # tant qu'il est renseigné, la ligne attend d'être cédée à un remplaçant en un clic.
    reprise = models.ForeignKey(
        "ReprisePoste", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="enregistrements",
    )
    # LISTE NOIRE : la ligne reste dans la table (visible dans le tableau, jamais supprimée) mais toutes les analyses l'ignorent
    # (voir parquet_store.donnees_analytiques). Se coche depuis les listes de l'analyse exploratoire (vides, aberrantes basses / hautes).
    liste_noire = models.BooleanField(default=False, db_index=True)
    # Token d'idempotence : fourni par le client lors de l'insertion pour
    # éviter les doublons en cas de double-clic ou de retry réseau.
    # Vide ("") si aucun token n'a été fourni (les insertions sans token
    # ne se dédoublonnent pas entre elles — comportement voulu pour les
    # imports en masse et les insertions programmatiques).
    idem_key = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        verbose_name=tl("engine/models/_part_002.py:241:23"),
    )

    # Identifiant agent obligatoire (voir TableDynamique.matricule_prefixe),
    # unique parmi TOUTES les tables d'agents de l'entreprise. Vide pour les
    # lignes de tables non-agents (jamais genere ni affiche dans ce cas).
    matricule = models.CharField(max_length=40, blank=True, default="", db_index=True)

    # QR code genere automatiquement (config_carte.qrcode_source == "auto") :
    # stocke comme une VRAIE image (comme un champ upload) au lieu d'etre
    # recalcule en memoire a chaque generation de PDF — voir reports.py.
    # Regenere uniquement si le contenu encode change (qrcode_hash).
    qrcode_genere = models.ImageField(upload_to="cartes/qrcodes_generes/", blank=True, null=True)
    qrcode_hash = models.CharField(max_length=64, blank=True, default="")

    # Empreinte (hash) persistee automatiquement pour le mode QR "hash" —
    # necessaire pour rendre ce mode retrouvable par recherche (contrairement
    # au matricule, l'empreinte n'existait auparavant qu'en memoire au moment
    # de la generation du PDF, donc introuvable ensuite lors d'un scan).
    empreinte_carte = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # Code d'accès individuel (6 hex), genere UNIQUEMENT quand cette ligne
    # appartient a une table agents et clients (table.est_table_membres=True). Remplace
    # l'ancien TableDynamique.code_acces_client (un seul code partage par
    # TOUS les clients de la table — faille de securite : un client pouvait
    # se servir de ce code pour se faire passer pour n'importe quel autre
    # client de la meme table). Desormais chaque client a son propre code,
    # communique individuellement par l'agent a CE client uniquement — voir
    # vue_acces_client (views.py) et generer_code_acces_client ci-dessous.
    code_acces_client = models.CharField(
        max_length=6, blank=True, default="", db_index=True,
        verbose_name=tl("engine/models/_part_002.py:272:23"),
        help_text=tl("engine/models/_part_002.py:g:295:19"),
    )
    # Mise en forme de la grille « Données » (reprise telle quelle dans les PDF) : couleur de la LIGNE entière et couleurs de
    # CELLULES précises {slug de colonne: "#rrggbb"}. Les deux sont indépendantes (voir engine/couleur_colonne.py : cellule >
    # ligne > colonne) et ne touchent jamais aux valeurs ni à to_dict() — uniquement de la présentation.
    couleur = models.CharField(max_length=9, blank=True, default="")
    couleurs_cellules = models.JSONField(blank=True, default=dict)

    # Stockage NoSQL (table.type_stockage == "nosql") UNIQUEMENT : toutes les
    # valeurs de champ de cette ligne, {slug_champ: valeur}, en un seul bloc
    # JSON — remplace entièrement ValeurChamp pour ce type de table (voir
    # engine/nosql_store.py). Vide/None pour une ligne de table SQL.
    valeurs_json = models.JSONField(
        null=True, blank=True, default=None,
        verbose_name=tl("engine/models/_part_002.py:283:23"),
        help_text=tl("engine/models/_part_002.py:284:20"),
    )

    class Meta:
        constraints = [
            # Contrainte BD : (table, idem_key) unique UNIQUEMENT quand
            # idem_key est non vide. Les insertions sans token (idem_key="")
            # ne se dédoublonnent pas entre elles — voulu pour imports CSV
            # et insertions programmatiques.
            models.UniqueConstraint(
                fields=["table", "idem_key"],
                condition=models.Q(idem_key__gt=""),
                name="enregistrement_table_idem_key_uniq",
            ),
        ]

    def generer_matricule(self):
        """Prefixe fixe (donne par l'admin sur la table) + suffixe aleatoire
        (module random, 6 chiffres) ; unicite verifiee sur TOUTES les tables
        d'agents/accessibles-client de l'entreprise, pas seulement celle-ci."""
        import random
        prefixe = (self.table.matricule_prefixe or "").strip()
        for _ in range(50):
            candidat = f"{prefixe}{random.randint(100000, 999999)}"
            existe = Enregistrement.objects.filter(
                table__entreprise_id=self.table.entreprise_id, matricule=candidat,
            ).exclude(pk=self.pk).exists()
            if not existe:
                return candidat
        return f"{prefixe}{uuid.uuid4().hex[:8].upper()}"

    def generer_code_acces_client(self):
        """Code hex 6 caracteres unique parmi TOUTES les lignes de tables
        clientes de l'entreprise (memes garanties d'unicite que le code de
        table qu'il remplace, mais un code par client au lieu d'un code
        partage par toute la table)."""
        for _ in range(200):
            candidat = secrets.token_hex(3).upper()
            existe = Enregistrement.objects.filter(
                table__entreprise_id=self.table.entreprise_id, code_acces_client=candidat,
            ).exclude(pk=self.pk).exists()
            if not existe:
                return candidat
        # Jamais de code plus long que max_length=6 (refusé par PostgreSQL, tronqué donc dupliqué par SQLite).
        raise RuntimeError(t("engine/models/_part_002.py:g:356:28"))

    def save(self, *args, **kwargs):
        # Identifiant unique obligatoire pour chaque ligne d'une table
        # d'agents ET pour chaque ligne accessible cote client (sert de
        # reference a la recherche par QR code — voir qr_secure.py).
        if not self.matricule and self.table_id and (
            self.table.est_table_membres or self.table.accessible_client
        ):
            self.matricule = self.generer_matricule()
        # Code d'acces individuel : uniquement pour les lignes d'une table
        # cliente (registre de clients). Chaque client recoit SON code, plus
        # jamais un code partage avec les autres clients de la table.
        if not self.code_acces_client and self.table_id and self.table.a_identifiants_connexion:
            self.code_acces_client = self.generer_code_acces_client()
        super().save(*args, **kwargs)

    def to_dict(self):
        if self.table.est_nosql:
            d = dict(self.valeurs_json or {})
        else:
            # Valeurs prechargees (prefetch_related("valeurs__champ")) : aucune requete par ligne. Sinon : une requete.
            deja_charge = "valeurs" in getattr(self, "_prefetched_objects_cache", {})
            valeurs = self.valeurs.all() if deja_charge else self.valeurs.select_related("champ")
            d = {v.champ.slug: v.valeur_typee() for v in valeurs}
        if self.matricule:
            d["matricule"] = self.matricule
        if self.code_acces_client:
            d["code_acces_client"] = self.code_acces_client
        return d

    def couleurs_mise_en_forme(self):
        """Couleur de la ligne et des cellules de CETTE ligne, au format d'échange de la grille et du PDF
        ({} si rien n'est colorié) — voir couleur_colonne.mise_en_forme. Séparé de to_dict() : jamais mêlé aux valeurs."""
        from ..couleur_colonne import mise_en_forme
        return mise_en_forme(self.couleur, self.couleurs_cellules)

    def to_dict_lisible(self):
        """Comme to_dict(), mais avec le NOM du champ (ex. « Nom du client »)
        comme clé plutot que son slug technique — utilise uniquement pour
        l'affichage humain (voir models.Bloc, blockchain.html)."""
        if self.table.est_nosql:
            noms_par_slug = {c.slug: c.nom for c in self.table.champs.all()}
            d = {noms_par_slug.get(slug, slug): valeur for slug, valeur in (self.valeurs_json or {}).items()}
        else:
            d = {v.champ.nom: v.valeur_typee() for v in self.valeurs.select_related("champ")}
        if self.matricule:
            d["Matricule"] = self.matricule
        if self.code_acces_client:
            d[_gtfr_p9("engine/models/_part_002.py:cle_code_acces_client")] = self.code_acces_client  # clé de DONNÉE (stockée en bloc, json.dumps exige une str) : jamais td()
        return d


class ValeurChamp(models.Model):
    enregistrement = models.ForeignKey(Enregistrement, on_delete=models.CASCADE, related_name="valeurs")
    champ = models.ForeignKey(ChampDynamique, on_delete=models.CASCADE)
    valeur = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = ("enregistrement", "champ")

    def valeur_typee(self):
        if self.valeur is None:
            return None
        t = self.champ.type_champ
        try:
            if t == "nombre":
                return int(self.valeur)
            if t in ("decimal", "pourcentage", "constante"):
                return float(self.valeur)
            if t == "monnaie":
                # Comme "decimal"/"pourcentage" : on renvoie un float, pas un
                # Decimal. Un Decimal est sérialisé en CHAÎNE par le JSON
                # encoder Django (DjangoJSONEncoder), alors que les autres
                # champs numériques arrivent en nombre JS — incohérence de
                # type qui cassait le formatage/tri côté front (index.html).
                return float(self.valeur)
            if t == "note":
                return int(self.valeur)
            if t in ChampDynamique.TYPES_BOOLEEN:
                # `switch` partage le format de booleen ("1" / "0" / "true" /
                # "false" / "vrai"). Si la valeur est vide, on renvoie False
                # plutôt que None pour rester cohérent avec l'ancien
                # comportement de booleen.
                if self.valeur == "":
                    return False
                return str(self.valeur).lower() in ("1", "true", "vrai")
            if t == "barre_progression":
                # On borne à 0..100, mais on NE lève PAS d'exception : une
                # valeur pourrie (texte, NaN, etc.) est simplement clampée
                # silencieusement, comme pour les autres types numériques
                # (Decimal, int, etc. en cas d'erreur on renvoie la string
                # brute).
                nombre = int(float(self.valeur))
                return max(0, min(100, nombre))
            if t == "choix_multiple":
                return [v for v in self.valeur.split(",") if v]
            if t in ChampDynamique.TYPES_TENSEUR:
                # Stocke en JSON (liste ou liste de listes) dans le meme
                # champ texte que tous les autres types (EAV) — voir
                # ChampDynamique.forme_tenseur pour la forme configuree.
                if not self.valeur.strip():
                    return None
                return json.loads(self.valeur)
            if t == "json":
                # Document JSON libre (objet ou liste), pas de forme
                # imposee. Meme principe que vecteur/matrice : stocke en
                # texte brut (EAV), parse a la lecture. Une valeur vide ou
                # invalide retombe sur la chaine brute (voir except plus
                # bas), comme pour tous les autres types.
                if not self.valeur.strip():
                    return None
                return json.loads(self.valeur)
            if t == "formule":
                # Résultat calculé (voir calculs.recalculer_champ_formule),
                # toujours stocké en JSON pour préserver le type réel
                # (nombre, texte, booléen...) plutôt qu'une chaîne brute.
                if not self.valeur.strip():
                    return None
                return json.loads(self.valeur)
        except (ValueError, TypeError, json.JSONDecodeError):
            return self.valeur
        return self.valeur


# ─────────────────────────────────────────────────────────────
# REGISTRE DE LIENS INTER-TABLES
# Trace chaque valeur copiée depuis une table source vers une
# table enfant. Quand la valeur source change, tous les enfants
# listés ici sont mis à jour automatiquement (signal post_save
# sur ValeurChamp dans apps.py).
#
# Principe :
#   - valeur_enfant.valeur  == valeur_source.valeur  (copie physique)
#   - Si valeur_source change → valeur_enfant change aussi
#   - Les deux tables restent indépendantes (pas de FK entre données)
# ─────────────────────────────────────────────────────────────
class LienChamp(models.Model):
    """Registre d'un lien entre une valeur enfant et sa valeur source."""
    # Côté enfant (la valeur qui reçoit la copie)
    valeur_enfant = models.OneToOneField(
        "ValeurChamp", on_delete=models.CASCADE,
        related_name="lien_source",
        help_text=tl("engine/models/_part_002.py:461:20"),
    )
    # Côté source (l'enregistrement original dans la table liée)
    enregistrement_source = models.ForeignKey(
        Enregistrement, on_delete=models.SET_NULL, null=True,
        related_name="liens_enfants",
        help_text=tl("engine/models/_part_002.py:467:20"),
    )
    champ_source = models.ForeignKey(
        "ChampDynamique", on_delete=models.SET_NULL, null=True,
        related_name="liens_enfants",
        help_text=tl("engine/models/_part_002.py:472:20"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["enregistrement_source", "champ_source"]),
        ]

    def __str__(self):
        return f"Lien {self.valeur_enfant_id} ← {self.enregistrement_source_id}.{self.champ_source_id}"


# ─────────────────────────────────────────────────────────────
# NOTIFICATIONS INTERNES (agents/admin) + relais SMS optionnel
# ─────────────────────────────────────────────────────────────
class Notification(models.Model):
    """Notification adressée à un agent (ou l'admin) de l'entreprise,
    générée automatiquement par un événement métier — pour l'instant :
    soumission d'un formulaire par un client (rôle lecteur) sur une table
    qui lui est exposée (voir TableDynamique.accessible_client et
    views.api_lignes). Chaque agent/admin de l'entreprise reçoit sa PROPRE
    ligne (pas de notification partagée), pour pouvoir suivre l'état
    lue/non-lue individuellement.

    Le relais SMS est optionnel (voir ConfigurationIA.notifications_sms_actif)
    et réutilise le moteur mmcli déjà utilisé par la page SMS (sms_mmcli.py) —
    voir notifications.notifier_nouvelle_ligne_client."""

    TYPE_CHOICES = [
        ("nouvelle_ligne_client", td("engine/models/_part_002.py:g:532:35")),
        ("fichier_enregistre", td("engine/models/_part_002.py:g:533:32")),
        ("depot_verifie", td("engine/models/_part_002.py:g:534:27")),
        ("sortie_especes", _gtd("rg6.msg.035")),
        ("kpi_alerte", td("engine/models/_part_002.py:g:535:24")),
        ("kpi_objectif", td("engine/models/_part_002.py:type_kpi_objectif")),
        ("tache_reussie", td("engine/models/_part_002.py:g:537:27")),
        ("tache_echec", td("engine/models/_part_002.py:g:538:25")),
        ("recommandation", td("engine/models/_part_002.py:g:539:28")),
        ("decision_strategique", td("engine/models/_part_002.py:g:540:34")),
        ("geo_rencontre", td("engine/models/_part_002.py:g:541:27")),
        ("geo_collision", td("engine/models/_part_002.py:g:542:27")),
        ("message_recu", td("engine/models/_part_002.py:g:543:26")),
        ("agenda_rappel", td("engine/models/_part_002.py:542:30")),
        ("agenda_retard", td("engine/models/_part_002.py:543:30")),
        ("agenda_retour", td("engine/models/_part_002.py:544:30")),
        ("alerte_vision", td("engine/models/_part_002.py:alerte_vision")),
    ]

    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="notifications")
    destinataire = models.ForeignKey(
        MembreEntreprise, on_delete=models.CASCADE, related_name="notifications",
        verbose_name=tl("engine/models/_part_002.py:512:23"),
    )
    type_notification = models.CharField(
        max_length=30, choices=TYPE_CHOICES, default="nouvelle_ligne_client",
    )
    titre = models.CharField(max_length=200)
    message = models.TextField(blank=True, default="")
    table = models.ForeignKey(
        TableDynamique, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    enregistrement = models.ForeignKey(
        Enregistrement, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    # Notification « message_recu » : discussion concernée (une seule notification non lue par discussion et par destinataire).
    conversation = models.ForeignKey("Conversation", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    # Notifications « agenda_* » : tâche concernée (voir engine/agenda.py).
    agenda = models.ForeignKey("TacheAgenda", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    lue = models.BooleanField(default=False, verbose_name=tl("engine/models/_part_002.py:527:60"))
    lue_le = models.DateTimeField(null=True, blank=True)
    # Traçabilité de l'envoi SMS : ne bloque jamais la notification elle-même
    # (voir notifications.py) — un échec mmcli ne fait que rester visible ici.
    sms_envoye = models.BooleanField(default=False, verbose_name=tl("engine/models/_part_002.py:531:67"))
    sms_erreur = models.CharField(max_length=255, blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-cree_le"]
        indexes = [models.Index(fields=["destinataire", "lue"])]
        verbose_name = t("engine/models/_part_002.py:538:25")

    def marquer_lue(self):
        if not self.lue:
            self.lue = True
            self.lue_le = timezone.now()
            self.save(update_fields=["lue", "lue_le"])

    def __str__(self):
        return f"{self.titre} → {self.destinataire}"


class RecoNotifiee(models.Model):
    """Mémoire anti-doublon des notifications de recommandations (voir notifications.synchroniser_recommandations).

    Une ligne par recommandation « à lancer » déjà notifiée (clé stable code + objet visé). `actif` reste vrai tant que la
    recommandation reste à lancer : elle n'est pas renotifiée à chaque recalcul ; quand elle disparaît puis revient,
    `actif` repasse à faux puis à vrai et une nouvelle notification est envoyée."""

    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="recos_notifiees")
    cle = models.CharField(max_length=200)
    actif = models.BooleanField(default=True)
    notifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entreprise", "cle"], name="uniq_reco_notifiee")]


# ─────────────────────────────────────────────────────────────
# AGENT + CODE_ENGINE (fusionnes) : historique conversation + scripts generes
# ─────────────────────────────────────────────────────────────
class DemandeAgent(models.Model):
    """Une demande utilisateur en langage naturel + le resultat du pipeline.

    PAS DE CACHE : chaque demande a SON PROPRE script, genere une seule fois
    par l'IA puis ecrit immediatement sur disque (media/scripts_agent/<table>/),
    que l'execution reussisse ou echoue ensuite. Le script n'est jamais
    remplace automatiquement par le systeme -- seule une action explicite de
    l'utilisateur (Regenerer, ou edition manuelle) le modifie."""
    STATUT_CHOICES = [
        ("en_attente", tl("engine/models/_part_002.py:g:625:24")), ("en_cours", tl("engine/models/_part_002.py:g:625:52")),
        ("script_genere", "Script genere"),
        ("execute", "Execute"), ("echec", "Echec"),
        # Phase 5 (Ontology -> Actions, agent_ontology.py) : la demande a ete
        # reconnue comme une operation deja cablee en Action plutot que
        # comme une analyse -- pas de script. Executee directement, elle
        # reutilise le statut "execute" ci-dessus (resultat_json contient
        # alors {"type": "action_executee", ...}) ; en attente de
        # confirmation humaine (effet sensible), elle prend ce nouveau
        # statut (resultat_json contient {"type": "proposition_action", ...},
        # voir views.api_confirmer_action_demande).
        ("action_proposee", "Action proposee (confirmation requise)"),
        # Carte préparée d'avance par le système (fantome_propositions.py) : AUCUN script, rien n'est exécuté ; l'utilisateur la garde
        # (bouton « Préparer l'analyse » : l'IA écrit le script, puis « Lancer ») ou la retire.
        ("proposee", "Proposee (a preparer)"),
    ]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="demandes")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    table_cible = models.ForeignKey(TableDynamique, on_delete=models.SET_NULL, null=True, blank=True)
    texte = models.TextField()
    script_genere = models.TextField(blank=True)
    fichier_script = models.CharField(
        max_length=500, blank=True,
        help_text=tl("engine/models/_part_002.py:582:20"),
    )
    fichier_resultat = models.CharField(
        max_length=500, blank=True,
        help_text=tl("engine/models/_part_002.py:586:20"),
    )
    modifie_manuellement = models.BooleanField(
        default=False, help_text=tl("engine/models/_part_002.py:589:35"),
    )
    resultat_json = models.JSONField(blank=True, null=True)
    # Couleurs de la carte d'analyse : {"_carte": "#rrggbb", "<section>": "#rrggbb"} (écran + PDF). Voir couleur_colonne.py.
    couleurs = models.JSONField(blank=True, default=dict)
    empreinte_donnees = models.CharField(
        max_length=64, blank=True,
        help_text=tl("engine/models/_part_002.py:594:20"),
    )
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="en_attente")
    erreur = models.TextField(blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)


class GraphiqueDemande(models.Model):
    """Un graphique (image PNG en base64 + legende) produit par UNE demande.
    Lu via demande.graphiques (agent.py, views.py, reports.py)."""
    demande = models.ForeignKey(DemandeAgent, on_delete=models.CASCADE, related_name="graphiques")
    image_base64 = models.TextField()
    legende = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["id"]


class RapportGenere(models.Model):
    FORMAT_CHOICES = [("pdf", "PDF"), ("csv", "CSV")]
    demande = models.ForeignKey(DemandeAgent, on_delete=models.CASCADE, related_name="rapports")
    format_fichier = models.CharField(max_length=10, choices=FORMAT_CHOICES)
    fichier = models.FileField(upload_to="rapports/%Y/%m/")
    cree_le = models.DateTimeField(auto_now_add=True)


class ArchiveCycle(models.Model):
    """Archive d'un cycle clos (bouton « Clôturer ») : zip contenant le
    résumé général PDF de chaque table de l'entreprise + un instantané JSON
    des groupes/tables/champs/lignes/valeurs — voir cycle.py. Permet de
    redémarrer l'interface de zéro puis de tout restaurer plus tard."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="archives_cycle")
    fichier = models.FileField(upload_to="archives_cycle/%Y/%m/")
    cree_par = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]


# ─────────────────────────────────────────────────────────────
# SKILLS : scripts d'analyse persistés sur disque, générés une fois
# par l'IA et réutilisables. Chaque skill est lié à UNE table.
# Structure de sortie imposée au modèle : titre, pourquoi, comment,
# table_statistiques, figures, explication, analyse_predictive,
# kpi, conclusion, recommandations.
# ─────────────────────────────────────────────────────────────
class Skill(models.Model):
    STATUT_CHOICES = [
        ("genere", tl("engine/models/_part_002.py:g:711:20")),
        ("execute", tl("engine/models/_part_002.py:g:712:21")),
        ("echec", tl("engine/models/_part_002.py:g:713:19")),
    ]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="skills")
    table = models.ForeignKey(TableDynamique, on_delete=models.CASCADE, related_name="skills")
    cree_par = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    nom = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    fichier_script = models.CharField(max_length=500, help_text=tl("engine/models/_part_002.py:652:66"))
    fichier_csv_table = models.CharField(max_length=500, blank=True, help_text=tl("engine/models/_part_002.py:653:81"))
    resultat_json = models.JSONField(blank=True, null=True)
    graphiques_json = models.JSONField(blank=True, null=True, help_text=tl("engine/models/_part_002.py:655:74"))
    # Couleurs du rapport : {"_carte": "#rrggbb", "<section>": "#rrggbb"} (écran + PDF). Voir couleur_colonne.py.
    couleurs = models.JSONField(blank=True, default=dict)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default="genere")
    erreur = models.TextField(blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    execute_le = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.table.nom} / {self.nom}"




# ─────────────────────────────────────────────────────────────
# CONFIGURATION IA : tous les prompts, rôles et paramètres du
# moteur d'inférence, éditables par l'administrateur de l'entreprise.
# Un seul enregistrement par entreprise (singleton via get_or_create).
# ─────────────────────────────────────────────────────────────


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
