from engine.i18n import td
from engine.i18n import tl
from engine.i18n import tf
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from engine.i18n import tl as _gtl  # noqa: E402


class ModeleIA(models.Model):
    """Une configuration de modele de machine learning : type, table(s)
    source(s) (= dataset, sans copie), colonne cible, et TOUS les
    hyperparametres du module IA (dict libre, valide dynamiquement contre
    la signature reelle de la fonction d'entrainement — voir
    ia_integration.types_modeles_disponibles)."""
    TYPE_CHOICES = [
        ("mlp", "MLP — classification / regression tabulaire"),
        ("rnn", "RNN — sequences"),
        ("cnn", "CNN — images / grilles"),
        ("transformer", "Transformer — sequences (attention)"),
        ("gan", "GAN — generation adversariale"),
        ("ldm", td("engine/models/_part_004.py:g:24:17")),
        ("slm", td("engine/models/_part_004.py:g:25:17")),
        ("slm_texte", "SLM Texte — classification (emotion / humeur / sentiment)"),
        ("image_classifier", td("engine/models/_part_004.py:g:27:30")),
        ("speech_classifier", "Classifieur audio"),
        # Modèles scikit-learn
        ("sklearn_rf",    "sklearn — Random Forest (classification)"),
        ("sklearn_gbm",   "sklearn — Gradient Boosting (classification)"),
        ("sklearn_svm",   "sklearn — SVM (classification)"),
        ("sklearn_knn",   "sklearn — k-Nearest Neighbors (classification)"),
        ("sklearn_lr",    "sklearn — Regression Logistique (classification)"),
        ("sklearn_nb",    "sklearn — Naive Bayes Gaussien (classification)"),
        ("sklearn_dt",    td("engine/models/_part_004.py:g:36:27")),
        ("sklearn_ridge", "sklearn — Ridge (regression L2)"),
        ("sklearn_lasso", "sklearn — Lasso (regression L1)"),
        # Modèles scikit-learn non supervisés (sans colonne cible) : groupes
        ("sklearn_kmeans",       td("ia.nonsup.type_kmeans")),
        ("sklearn_dbscan",       td("ia.nonsup.type_dbscan")),
        ("sklearn_hierarchique", td("ia.nonsup.type_hierarchique")),
        ("sklearn_gmm",          td("ia.nonsup.type_gmm")),
        # Modèles scikit-learn non supervisés (lot 2) : projection et anomalies
        ("sklearn_acp",          td("ia.nonsup.type_acp")),
        ("sklearn_ica",          td("ia.nonsup.type_ica")),
        ("sklearn_isoforest",    td("ia.nonsup.type_isoforest")),
        ("sklearn_ocsvm",        td("ia.nonsup.type_ocsvm")),
        ("sklearn_lof",          td("ia.nonsup.type_lof")),
    ]
    STATUT_CHOICES = [
        ("brouillon",    "Brouillon"),
        ("entrainement", tl("engine/models/_part_004.py:g:41:26")),
        ("entraine",     "Entraine"),
        ("erreur",       tl("engine/models/_part_004.py:g:43:26")),
        ("annule",       "Entrainement annule"),
    ]
    entreprise    = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="modeles_ia")
    nom           = models.CharField(max_length=150)
    slug          = models.SlugField(max_length=160)
    type_modele   = models.CharField(max_length=30, choices=TYPE_CHOICES)
    tables_source = models.ManyToManyField(
        TableDynamique, related_name="modeles_ia",
        help_text=td("engine/models/_part_004.py:g:53:19"),
    )
    colonne_cible = models.CharField(
        max_length=120, blank=True, default="",
        help_text=td("engine/models/_part_004.py:g:58:19"),
    )
    colonnes_entree = models.JSONField(
        default=list, blank=True,
        help_text=td("engine/models/_part_004.py:g:64:19"),
    )
    colonnes_sortie = models.JSONField(
        default=list, blank=True,
        help_text=td("engine/models/_part_004.py:g:75:19"),
    )
    parametres = models.JSONField(
        default=dict, blank=True,
        help_text=td("engine/models/_part_004.py:g:85:19"),
    )
    lignes_exclues = models.JSONField(
        default=dict, blank=True,
        help_text=_gtl("rg6.msg.043"),
    )
    plage_lignes = models.JSONField(
        default=dict, blank=True,
        help_text="Plage de lignes à entraîner : {\"debut\": 10001, \"fin\": 20000} (positions 1-based, bornes incluses, dans l'ordre "
                  "de la table). Vide = toutes les lignes. Appliquée à l'entraînement initial (pas au fine-tuning).",
    )
    statut       = models.CharField(max_length=20, choices=STATUT_CHOICES, default="brouillon")
    erreur       = models.TextField(blank=True)
    fichier_modele = models.CharField(
        max_length=500, blank=True,
        help_text=tl("engine/models/_part_004.py:89:20"),
    )
    metriques    = models.JSONField(default=dict, blank=True,
                       help_text=tl("engine/models/_part_004.py:92:35"))
    structure_entree = models.JSONField(
        default=dict, blank=True,
        help_text=tl("engine/models/_part_004.py:g:97:19"),
    )
    progression = models.JSONField(
        default=dict, blank=True,
        help_text=td("engine/models/_part_004.py:g:102:19"),
    )
    demande_annulation = models.BooleanField(
        default=False,
        help_text=tl("engine/models/_part_004.py:g:112:19"),
    )
    demande_pause = models.BooleanField(
        default=False,
        help_text=tl("engine/models/_part_004.py:g:121:19"),
    )
    jalon_lignes = models.JSONField(
        default=dict, blank=True,
        help_text=tl("engine/models/_part_004.py:g:127:19"),
    )
    visible_client = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_004.py:visible_client"),
        help_text=tl("engine/models/_part_004.py:g:133:19"),
    )
    cree_le      = models.DateTimeField(auto_now_add=True)
    modifie_le   = models.DateTimeField(auto_now=True)
    entraine_le  = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ("entreprise", "slug")
        verbose_name = t("engine/models/_part_004.py:123:25")

    def __str__(self):
        return f"{self.nom} ({self.type_modele})"


class InferenceIA(models.Model):
    """Journal d'un appel d'inference sur un ModeleIA entraine."""
    modele  = models.ForeignKey(ModeleIA, on_delete=models.CASCADE, related_name="inferences")
    entree  = models.JSONField(default=dict, blank=True)
    sortie  = models.JSONField(default=dict, blank=True)
    erreur  = models.TextField(blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = t("engine/models/_part_004.py:138:25")
        ordering = ["-cree_le"]

    def __str__(self):
        return f"Inference {self.modele.nom} — {self.cree_le:%Y-%m-%d %H:%M}"


# ─────────────────────────────────────────────────────────────
# TABLE DE CALCUL : équivalent simplifié d'une feuille de calcul Excel,
# INDÉPENDANTE de la table dynamique source (aucune colonne/champ n'est
# ajouté à celle-ci). Chaque `LigneCalcul` applique une `FormuleCalcul`
# (un script Python `def formule(cible): ...` exécuté dans le même bac à
# sable que executor.py) à une cible tirée de la table source : une
# colonne entière, une ligne précise, ou une plage de lignes. Le résultat
# est mis en cache (resultat_json) et la table de calcul est ajoutée en
# supplément au PDF d'export de liste de sa table source (voir
# reports.generer_pdf_liste_table / _bloc_tables_calcul).
# ─────────────────────────────────────────────────────────────
class FormuleCalcul(models.Model):
    """Bibliothèque de formules disponibles pour les tables de calcul.

    `entreprise` vide (None) = formule prédéfinie, fournie par la
    plateforme, partagée par toutes les entreprises, non supprimable
    depuis l'interface. Une entreprise peut aussi créer ses propres
    formules personnalisées (entreprise renseignée).

    `cible_type` détermine ce que reçoit `def formule(cible):` :
      - "colonne" : cible est une pandas.Series (une seule colonne,
        castée selon son type EAV) — nécessite qu'une LigneCalcul
        renseigne `champ_cible`.
      - "table"   : cible est un pandas.DataFrame (toutes les colonnes
        de la table source, éventuellement restreint à une plage de
        lignes) — `champ_cible` n'est pas utilisé ; utile pour les
        formules qui doivent elles-mêmes repérer plusieurs colonnes
        (ex: prévision temporelle croisant une colonne date et une
        colonne numérique).
    """
    CATEGORIE_CHOICES = [
        ("agregat", td("engine/models/_part_004.py:g:184:21")),
        ("statistique", "Statistique"),
        ("temporel", td("engine/models/_part_004.py:g:186:22")),
        ("texte", td("engine/models/_part_004.py:g:187:19")),
        ("qualite", td("engine/models/_part_004.py:g:188:21")),
        ("test", td("engine/models/_part_004.py:g:189:18")),
        ("effet", td("engine/models/_part_004.py:g:190:19")),
        ("regression", td("engine/models/_part_004.py:g:191:24")),
        ("multivarie", td("engine/models/_part_004.py:g:192:24")),
        ("segmentation", "Segmentation"),
        ("anomalie", td("engine/models/_part_004.py:g:194:22")),
        ("finance", "Finance / commerce"),
        ("probabilite", td("engine/models/_part_004.py:g:196:25")),
        ("inferentiel", td("engine/models/_part_004.py:g:197:25")),
        ("date", "Dates"), ("duree", td("engine/models/_part_004.py:g:198:38")), ("booleen", td("engine/models/_part_004.py:g:198:61")),
        ("categoriel", td("engine/models/_part_004.py:g:199:24")), ("tenseur", "Vecteurs / matrices"),
        ("personnalisee", td("engine/models/_part_004.py:g:200:27")),
    ]
    CIBLE_TYPE_CHOICES = [
        ("colonne", td("engine/models/_part_004.py:g:203:21")),
        ("table", td("engine/models/_part_004.py:g:204:19")),
    ]

    entreprise = models.ForeignKey(
        Entreprise, null=True, blank=True, on_delete=models.CASCADE,
        related_name="formules_calcul",
        help_text=td("engine/models/_part_004.py:g:210:19"),
    )
    nom = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    description = models.TextField(blank=True)
    categorie = models.CharField(max_length=20, choices=CATEGORIE_CHOICES, default="agregat")
    cible_type = models.CharField(max_length=10, choices=CIBLE_TYPE_CHOICES, default="colonne")
    code = models.TextField(
        help_text=td("engine/models/_part_004.py:g:219:19"),
    )
    types_cibles_compatibles = models.CharField(
        max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_004.py:g:238:19"),
    )
    predefinie = models.BooleanField(
        default=False,
        help_text=tl("engine/models/_part_004.py:223:20"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("entreprise", "slug")
        ordering = ["categorie", "nom"]
        verbose_name = t("engine/models/_part_004.py:230:25")

    def __str__(self):
        return self.nom

    @property
    def liste_types_compatibles(self):
        return [t.strip() for t in self.types_cibles_compatibles.split(",") if t.strip()]

    def compatible_avec_type_champ(self, type_champ: str) -> bool:
        """True si cette formule peut être appliquée à un champ de ce type_champ."""
        if self.cible_type == "table":
            return True
        types = self.liste_types_compatibles
        return not types or type_champ in types


class FormuleChamp(models.Model):
    """Formule persistante d'une colonne calculée façon Excel (bouton "∑"
    sur l'en-tête, vue Données) — voir calc_champ.py pour la résolution et
    l'exécution. Déclare le schéma créé par la migration 0137_formulechamp
    (modèle manquant jusqu'ici : la table existait en base sans classe)."""
    operateur = models.CharField(max_length=15, choices=FORMULE_CHAMP_OPERATEUR_CHOICES, default="somme")
    operandes_slugs = models.JSONField(default=list)
    parametres = models.JSONField(blank=True, null=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)
    champ_cible = models.OneToOneField(ChampDynamique, on_delete=models.CASCADE, related_name="formule_champ")
    formule = models.ForeignKey(FormuleCalcul, blank=True, null=True, on_delete=models.PROTECT, related_name="+")
    table = models.ForeignKey(TableDynamique, on_delete=models.CASCADE, related_name="formules_champs")

    class Meta:
        verbose_name = t("engine/models/_part_004.py:262:25")

    def __str__(self):
        return f"{self.operateur} → champ {self.champ_cible_id}"


class FormuleLigne(models.Model):
    """Formule persistante partagée par plusieurs champs d'UNE même ligne
    (mode "ligne", symétrique à FormuleChamp) — voir calc_champ.py. Déclare
    le schéma créé par la migration 0138_formuleligne (modèle manquant
    jusqu'ici)."""
    operateur = models.CharField(max_length=15, choices=FORMULE_CHAMP_OPERATEUR_CHOICES, default="somme")
    operandes_slugs = models.JSONField(default=list)
    parametres = models.JSONField(blank=True, null=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)
    enregistrement = models.ForeignKey(Enregistrement, on_delete=models.CASCADE, related_name="formules_lignes")
    formule = models.ForeignKey(FormuleCalcul, blank=True, null=True, on_delete=models.PROTECT, related_name="+")
    table = models.ForeignKey(TableDynamique, on_delete=models.CASCADE, related_name="formules_lignes")

    class Meta:
        verbose_name = t("engine/models/_part_004.py:283:25")

    def __str__(self):
        return tf("engine/models/_part_004.py:g:305:16", v1=self.operateur, v2=self.enregistrement_id)


class FormuleLigneCible(models.Model):
    """Champ cible d'une FormuleLigne — une ligne par champ ciblé. Déclare
    le schéma créé par la migration 0138_formuleligne (modèle manquant
    jusqu'ici)."""
    champ_cible = models.ForeignKey(ChampDynamique, on_delete=models.CASCADE, related_name="formules_lignes_cibles")
    enregistrement = models.ForeignKey(Enregistrement, on_delete=models.CASCADE, related_name="+")
    formule_ligne = models.ForeignKey(FormuleLigne, on_delete=models.CASCADE, related_name="cibles")

    class Meta:
        verbose_name = t("engine/models/_part_004.py:298:25")
        unique_together = ("enregistrement", "champ_cible")


class CoucheOntologie(models.Model):
    """Extension du graphe d'ontologie (engine/ontologie.py) définie par
    l'entreprise elle-même : un script Python `def etendre(graphe): ...`
    qui reçoit le networkx.DiGraph déjà construit (nœuds/arêtes standards),
    peut y ajouter ses propres nœuds (g.add_node) et arêtes (g.add_edge,
    au sens large : une nouvelle "couche" ou de nouvelles relations entre
    nœuds existants), puis DOIT `return graphe`.

    Même principe que FormuleCalcul : on enregistre le script une fois,
    il est ensuite appliqué automatiquement à chaque (re)construction du
    graphe (voir ontologie.construire_graphe), exécuté dans le même bac à
    sable que les autres canaux (executor.executer_couche_ontologie_securisee).
    entreprise vide (None) = couche prédéfinie partagée par toutes les
    entreprises ; renseignée = couche personnalisée d'une entreprise."""
    entreprise = models.ForeignKey(
        Entreprise, null=True, blank=True, on_delete=models.CASCADE,
        related_name="couches_ontologie",
    )
    nom = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    description = models.TextField(blank=True)
    code = models.TextField(
        help_text=tl("engine/models/_part_004.py:g:344:19"),
    )
    actif = models.BooleanField(default=True)
    predefinie = models.BooleanField(default=False)
    pages = models.ManyToManyField(
        "PageOntologie", blank=True, related_name="couches",
        help_text=td("engine/models/_part_004.py:g:337:19"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("entreprise", "slug")
        ordering = ["nom"]
        verbose_name = t("engine/models/_part_004.py:347:25")
        verbose_name_plural = t("engine/models/_part_004.py:348:32")

    def __str__(self):
        return self.nom


class PageOntologie(models.Model):
    """Une page d'ontologie indépendante : même principe que TableauBordBI
    pour le dashboard BI, appliqué au graphe d'ontologie. Une entreprise
    peut ainsi tenir plusieurs graphes d'ontologie séparés (ex: un par
    filiale, par processus métier...) au lieu d'un graphe unique.

    Le graphe standard (nœuds/arêtes de base construits par
    ontologie.construire_graphe) reste commun à toutes les pages ; ce qui
    diffère d'une page à l'autre, ce sont les CoucheOntologie qui lui sont
    explicitement rattachées via CoucheOntologie.pages (voir ce champ) — une
    couche non rattachée à une page précise ne s'applique qu'aux pages qui
    la référencent. Les couches prédéfinies (CoucheOntologie.predefinie=True,
    entreprise=None) s'appliquent elles à TOUTES les pages, comme avant."""
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="pages_ontologie"
    )
    nom = models.CharField(max_length=150)
    slug = models.SlugField(max_length=160)
    description = models.TextField(blank=True)
    ordre = models.PositiveIntegerField(default=0)
    visible_client = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_004.py:376:23"),
        help_text=tl("engine/models/_part_004.py:g:397:19"),
    )
    MODE_CHOICES = [("auto", "Automatique"), ("manuel", "Manuel")]
    mode = models.CharField(
        max_length=10, choices=MODE_CHOICES, default="auto",
        help_text=td("engine/models/_part_004.py:g:382:19"),
    )
    tables_manuelles = models.ManyToManyField(
        "TableDynamique", blank=True, related_name="pages_ontologie_manuelles",
        help_text=tl("engine/models/_part_004.py:393:20"),
    )
    champs_manuels = models.ManyToManyField(
        "ChampDynamique", blank=True, related_name="pages_ontologie_manuelles",
        help_text=tl("engine/models/_part_004.py:397:20"),
    )
    relations_manuelles = models.JSONField(
        default=list, blank=True,
        help_text=tl("engine/models/_part_004.py:g:421:19"),
    )
    descriptions_noeuds = models.JSONField(
        default=dict, blank=True,
        help_text=tl("engine/models/_part_004.py:g:426:19"),
    )
    descriptions_relations = models.JSONField(
        default=dict, blank=True,
        help_text=tl("engine/models/_part_004.py:g:432:19"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("entreprise", "slug")
        ordering = ["ordre", "id"]
        verbose_name = t("engine/models/_part_004.py:420:25")
        verbose_name_plural = t("engine/models/_part_004.py:421:32")

    def __str__(self):
        return f"{self.entreprise.nom} / {self.nom}"


class CouleurOntologie(models.Model):
    """Personnalisation de l'apparence du graphe d'ontologie (engine/ontologie.py),
    par entreprise : redéfinit la couleur d'un type de nœud ou de relation —
    y compris un type déjà prédéfini par la plateforme (table, agent, lien...)
    — ou déclare la couleur/description d'un TOUT NOUVEAU type/relation, avant
    même qu'une CoucheOntologie ne l'utilise réellement dans le graphe (il
    apparaît alors immédiatement dans les sélecteurs "Entité source/cible" et
    "Nom de la relation" du panneau Couches, prêt à être référencé par un
    script `def etendre(graphe): ...`).

    Priorité de couleur appliquée par ontologie.graphe_vers_json : cette
    table (par entreprise) > couleur explicite posée par une CoucheOntologie
    (g.add_node(..., couleur=...)) > couleur générique par défaut (gris)."""
    CIBLE_CHOICES = [("noeud", tl("engine/models/_part_004.py:g:460:32")), ("arete", tl("engine/models/_part_004.py:g:460:59"))]

    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="couleurs_ontologie",
    )
    cible = models.CharField(max_length=10, choices=CIBLE_CHOICES)
    cle = models.SlugField(
        max_length=60,
        help_text=td("engine/models/_part_004.py:g:442:19"),
    )
    couleur = models.CharField(max_length=9, help_text=tl("engine/models/_part_004.py:452:57"))
    label = models.CharField(
        max_length=120, blank=True, default="",
        help_text=td("engine/models/_part_004.py:g:449:19"),
    )
    description = models.CharField(max_length=255, blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("entreprise", "cible", "cle")
        ordering = ["cible", "cle"]
        verbose_name = t("engine/models/_part_004.py:464:25")
        verbose_name_plural = t("engine/models/_part_004.py:465:32")

    def __str__(self):
        return f"{self.entreprise_id} / {self.cible}:{self.cle} = {self.couleur}"


class CouleurNoeudOntologie(models.Model):
    """Couleur d'UN nœud précis du graphe d'ontologie (ex. « table:5 »), par entreprise.
    Prioritaire sur la couleur du type (CouleurOntologie) : voir ontologie.graphe_vers_json."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="couleurs_noeuds_ontologie")
    noeud = models.CharField(max_length=120)
    couleur = models.CharField(max_length=9)

    class Meta:
        unique_together = ("entreprise", "noeud")


class CouleurBloc(models.Model):
    """Couleur d'un bloc de la blockchain (cle « bloc:<index> ») ou de TOUS les blocs d'un type de transaction (cle « type:<type> »),
    par entreprise. Pure mise en forme, rangée À PART : le contenu d'un bloc est scellé par son hash, la couleur n'y entre jamais.
    Elle suit le bloc dans toutes les portées (globale, portable, agent) et sous tous les filtres de recherche ; la couleur d'un
    bloc précis est prioritaire sur celle de son type (comme CouleurNoeudOntologie sur CouleurOntologie)."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="couleurs_blocs")
    cle = models.CharField(max_length=120)
    couleur = models.CharField(max_length=9)

    class Meta:
        unique_together = ("entreprise", "cle")

    def __str__(self):
        return f"{self.entreprise_id} / {self.cle} = {self.couleur}"


class GenerationFormule(models.Model):
    """Suivi d'une génération de formule via l'IA (bac de brouillon avant
    "Enregistrer la formule" côté client). Même pattern que DemandeAgent /
    DocumentGenere : un objet en base avec un statut "en_cours" posé AVANT
    l'appel .delay(), pour que le polling voie tout de suite l'état
    transitoire ; la tâche Celery se contente de mettre à jour cet objet.

    Ne remplace pas FormuleCalcul (qui est la formule définitive, enregistrée
    par l'utilisateur) : ceci n'est qu'un brouillon, jetable, propre à une
    session de génération."""
    STATUT_CHOICES = [
        ("en_cours", tl("engine/models/_part_004.py:g:513:22")),
        ("pret",     tl("engine/models/_part_004.py:g:514:22")),
        ("echec",    tl("engine/models/_part_004.py:g:515:22")),
    ]
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="generations_formule",
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
    )
    table_reference = models.ForeignKey(
        TableDynamique, on_delete=models.SET_NULL, null=True, blank=True,
        help_text=tl("engine/models/_part_004.py:494:20"),
    )
    description = models.TextField(
        help_text=tl("engine/models/_part_004.py:497:20"),
    )
    cible_type = models.CharField(max_length=10, default="colonne")
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default="en_cours")
    code = models.TextField(blank=True, default="")
    erreur = models.TextField(blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = t("engine/models/_part_004.py:507:25")

    def __str__(self):
        return tf("engine/models/_part_004.py:g:540:16", v1=self.id, v2=self.statut)


class GenerationGraphiqueBI(models.Model):
    """Suivi d'une génération de graphique BI personnalisé via l'IA (bac de
    brouillon avant "Enregistrer le graphique" côté client). Même pattern
    que GenerationFormule / DemandeAgent / DocumentGenere : un objet en base
    avec un statut "en_cours" posé AVANT l'appel .delay(), pour que le
    polling voie tout de suite l'état transitoire ; la tâche Celery se
    contente de mettre à jour cet objet.

    Ne remplace pas GraphiqueBI (qui est le graphique définitif, enregistré
    par l'utilisateur) : ceci n'est qu'un brouillon, jetable, propre à une
    session de génération."""
    STATUT_CHOICES = [
        ("en_cours", tl("engine/models/_part_004.py:g:556:22")),
        ("pret",     tl("engine/models/_part_004.py:g:557:22")),
        ("echec",    tl("engine/models/_part_004.py:g:558:22")),
    ]
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="generations_graphique_bi",
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
    )
    table_reference = models.ForeignKey(
        TableDynamique, on_delete=models.SET_NULL, null=True, blank=True,
        help_text=tl("engine/models/_part_004.py:537:20"),
    )
    description = models.TextField(
        help_text=tl("engine/models/_part_004.py:540:20"),
    )
    cible_type = models.CharField(max_length=20, default="colonne_unique")
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default="en_cours")
    code = models.TextField(blank=True, default="")
    erreur = models.TextField(blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = t("engine/models/_part_004.py:550:25")

    def __str__(self):
        return tf("engine/models/_part_004.py:g:583:16", v1=self.id, v2=self.statut)


class GenerationCoucheOntologie(models.Model):
    """Suivi d'une génération de couche d'ontologie via l'IA (bac de brouillon
    avant "Enregistrer et appliquer" côté client). Même pattern que
    GenerationFormule / GenerationGraphiqueBI : un objet en base avec un
    statut "en_cours" posé AVANT l'appel .delay(), pour que le polling voie
    tout de suite l'état transitoire ; la tâche Celery se contente de mettre
    à jour cet objet.

    Contrairement à une formule/un graphique (qui portent sur UNE table),
    une couche d'ontologie porte sur TOUT le graphe de l'entreprise : pas de
    `table_reference`, mais une description libre du type de relation ou
    d'événement souhaité (voir engine.ontologie.contexte_pour_generation_couche
    pour le contexte de types de nœuds/relations transmis à l'IA).

    Ne remplace pas CoucheOntologie (qui est la couche définitive, enregistrée
    par l'utilisateur) : ceci n'est qu'un brouillon, jetable, propre à une
    session de génération."""
    STATUT_CHOICES = [
        ("en_cours", tl("engine/models/_part_004.py:g:605:22")),
        ("pret",     tl("engine/models/_part_004.py:g:606:22")),
        ("echec",    tl("engine/models/_part_004.py:g:607:22")),
    ]
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="generations_couche_ontologie",
    )
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
    )
    description = models.TextField(
        help_text=tl("engine/models/_part_004.py:585:20"),
    )
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default="en_cours")
    code = models.TextField(blank=True, default="")
    erreur = models.TextField(blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]
        verbose_name = t("engine/models/_part_004.py:594:25")

    def __str__(self):
        return tf("engine/models/_part_004.py:g:627:16", v1=self.id, v2=self.statut)


class TableCalcul(models.Model):
    """Table de calcul indépendante, liée à une TableDynamique source dont
    elle tire ses données. Ne modifie jamais la table source."""
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="tables_calcul"
    )
    table_source = models.ForeignKey(
        TableDynamique, on_delete=models.CASCADE, related_name="tables_calcul"
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    inclure_dans_pdf = models.BooleanField(
        default=True,
        verbose_name=tl("engine/models/_part_004.py:613:23"),
        help_text=tl("engine/models/_part_004.py:g:645:19"),
    )
    ordre = models.PositiveIntegerField(default=0)
    # Couleur de la table de calcul « #rrggbb » (vide = aucune) : écran + PDF. Voir couleur_colonne.py.
    couleur = models.CharField(max_length=9, blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = t("engine/models/_part_004.py:622:25")

    def __str__(self):
        return f"{self.table_source.nom} / {self.nom}"


class LigneCalcul(models.Model):
    """Une ligne de la table de calcul : un nom, une formule choisie dans la
    bibliothèque, et une cible tirée de la table source."""
    TYPE_CIBLE_CHOICES = [
        ("champ", td("engine/models/_part_004.py:g:638:19")),
        ("ligne", td("engine/models/_part_004.py:g:639:19")),
        ("plage", td("engine/models/_part_004.py:g:640:19")),
        ("resultats", td("engine/models/_part_004.py:g:641:23")),
    ]

    table_calcul = models.ForeignKey(TableCalcul, on_delete=models.CASCADE, related_name="lignes")
    nom = models.CharField(max_length=150)
    # Vide pour une ligne « programme » (éditeur visuel, voir `programme`).
    formule = models.ForeignKey(FormuleCalcul, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    # Programme de formules (même éditeur et même moteur que la table dynamique et les KPI : formules_programme.py) :
    # briques enchaînées, conditions, boucles ; opérandes = colonnes de la table source OU résultats d'autres calculs
    # de la même table de calcul (cités par leur nom). `lignes_sources` en est dérivé à l'enregistrement, pour que la
    # cascade de recalcul (ordre, cycles) serve les programmes comme les itérations.
    programme = models.JSONField(null=True, blank=True)
    type_cible = models.CharField(max_length=10, choices=TYPE_CIBLE_CHOICES, default="champ")
    champ_cible = models.ForeignKey(
        ChampDynamique, null=True, blank=True, on_delete=models.CASCADE, related_name="+",
        help_text=td("engine/models/_part_004.py:g:656:19"),
    )
    # Pour les formules cible_type="table" uniquement : sous-ensemble de colonnes
    # de la table source à inclure dans le DataFrame passé à la formule (issu de
    # la sélection graphique — clic sur un ou plusieurs en-têtes de colonne dans
    # la vue Données). Vide = toutes les colonnes de la table (comportement
    # historique, avant l'ajout de la sélection graphique).
    champs_cibles = models.ManyToManyField(
        ChampDynamique, blank=True, related_name="+",
        help_text=td("engine/models/_part_004.py:g:666:19"),
    )
    # Pour type_cible="ligne" : numéro de ligne (1-based, ordre chronologique
    # croissant — la ligne n°1 est la plus ancienne). Pour type_cible="plage" :
    # bornes [plage_debut, plage_fin] inclusives, mêmes conventions. Vide pour
    # type_cible="champ" (toute la colonne / toute la table).
    plage_debut = models.PositiveIntegerField(null=True, blank=True)
    plage_fin = models.PositiveIntegerField(null=True, blank=True)
    # Sélections discontinues (Ctrl+clic dans le sélecteur graphique).
    # Structure : [{"champ_id": int, "debut": int|null, "fin": int|null}, ...]
    # debut/fin sont 1-based ; null = colonne entière.
    # Utilisé uniquement pour cible_type="table" avec formules multi-colonnes.
    # Si vide/null : comportement classique (champs_cibles + plage_debut/fin).
    cibles_json = models.JSONField(null=True, blank=True)
    # Pour type_cible="resultats" : autres lignes de calcul (même table_calcul
    # ou non) dont le resultat_json sert de série d'entrée — permet d'enchaîner
    # les calculs (ex: moyennes par matière, puis moyenne générale de ces
    # moyennes). recalculer_table_calcul() réitère plusieurs passes tant que
    # les résultats évoluent encore, pour que l'ordre de calcul n'ait pas
    # besoin d'être manuel.
    lignes_sources = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="lignes_dependantes",
        help_text=tl("engine/models/_part_004.py:678:20"),
    )
    ordre = models.PositiveIntegerField(default=0)
    # Couleur du calcul « #rrggbb » (vide = aucune) : écran + PDF.
    couleur = models.CharField(max_length=9, blank=True, default="")

    resultat_json = models.JSONField(null=True, blank=True)
    erreur = models.TextField(blank=True, default="")
    resultat_calcule_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = t("engine/models/_part_004.py:689:25")

    def __str__(self):
        return f"{self.table_calcul.nom} / {self.nom}"


# ─────────────────────────────────────────────────────────────
# TABLEAU DE BORD BI (style Power BI) : canevas plein écran, purement
# graphique, toujours lié à UNE TableDynamique. Même architecture que les
# tables de calcul (FormuleCalcul/TableCalcul/LigneCalcul) : un catalogue de
# graphiques (prédéfinis + personnalisés par code), un "tableau de bord" qui
# instancie des widgets positionnés sur une grille, chaque widget choisissant
# ses colonnes. AUCUNE image n'est jamais écrite sur disque : le rendu
# (base64 PNG) ne vit qu'en base (texte), recalculé à la demande — voir
# tableau_bord_bi.py et executor.executer_graphique_bi_securise.
# ─────────────────────────────────────────────────────────────
class GraphiqueBI(models.Model):
    """Bibliothèque de graphiques disponibles pour les tableaux de bord BI.

    Même principe que FormuleCalcul : `entreprise` vide (None) = graphique
    prédéfini fourni par la plateforme (au moins 25, voir graphiques_bi.py),
    non supprimable depuis l'interface. Une entreprise peut aussi créer ses
    propres graphiques personnalisés (code Python fourni par l'admin).

    `cible_type` détermine ce que reçoit `def dessiner(dataframe, colonnes):`
      - "colonne_unique" : le graphique n'a besoin que d'UNE colonne
        (ex: histogramme, camembert) — `colonnes` est une liste à 1 élément.
      - "colonnes_multiples" : le graphique croise plusieurs colonnes
        (ex: nuage de points x/y, courbes multiples, heatmap) — `colonnes`
        contient tous les slugs sélectionnés, dans l'ordre choisi.
    """
    CATEGORIE_CHOICES = [
        ("comparaison", td("engine/models/_part_004.py:g:735:25")),
        ("evolution", td("engine/models/_part_004.py:g:736:23")),
        ("repartition", td("engine/models/_part_004.py:g:737:25")),
        ("distribution", "Distribution statistique"),
        ("relation", td("engine/models/_part_004.py:g:739:22")),
        ("indicateur", td("engine/models/_part_004.py:g:740:24")),
        ("tendance", "Tendance"), ("correlation", td("engine/models/_part_004.py:g:741:51")), ("composition", "Composition"),
        ("qualite", td("engine/models/_part_004.py:g:742:21")), ("segmentation", "Segmentation"),
        ("anomalie", "Anomalies"), ("finance", "Finance"),
        ("personnalisee", td("engine/models/_part_004.py:g:744:27")),
    ]
    CIBLE_TYPE_CHOICES = [
        ("colonne_unique", td("engine/models/_part_004.py:g:747:28")),
        ("colonnes_multiples", td("engine/models/_part_004.py:g:748:32")),
    ]

    entreprise = models.ForeignKey(
        Entreprise, null=True, blank=True, on_delete=models.CASCADE,
        related_name="graphiques_bi",
        help_text=td("engine/models/_part_004.py:g:754:19"),
    )
    nom = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    description = models.TextField(blank=True)
    categorie = models.CharField(max_length=20, choices=CATEGORIE_CHOICES, default="comparaison")
    cible_type = models.CharField(max_length=20, choices=CIBLE_TYPE_CHOICES, default="colonne_unique")
    nombre_colonnes_min = models.PositiveIntegerField(
        default=1, help_text=tl("engine/models/_part_004.py:749:31"),
    )
    nombre_colonnes_max = models.PositiveIntegerField(
        null=True, blank=True,
        help_text=tl("engine/models/_part_004.py:753:20"),
    )
    code = models.TextField(
        help_text=td("engine/models/_part_004.py:g:770:19"),
    )
    types_cibles_compatibles = models.CharField(
        max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_004.py:g:805:19"),
    )
    predefini = models.BooleanField(
        default=False,
        help_text=tl("engine/models/_part_004.py:769:20"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("entreprise", "slug")
        ordering = ["categorie", "nom"]
        verbose_name = t("engine/models/_part_004.py:776:25")

    def __str__(self):
        return self.nom

    @property
    def liste_types_compatibles(self):
        return [t.strip() for t in self.types_cibles_compatibles.split(",") if t.strip()]


class TableauBordBI(models.Model):
    """Un tableau de bord BI : canevas plein écran, purement graphique,
    lié à UNE TableDynamique dont il tire ses données. Ne modifie jamais la
    table source. Plusieurs tableaux de bord BI peuvent exister par table."""
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="tableaux_bord_bi"
    )
    table_source = models.ForeignKey(
        TableDynamique, on_delete=models.CASCADE, related_name="tableaux_bord_bi"
    )
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    # Dimensions de la grille du canevas (nombre de colonnes/lignes de la
    # grille sous-jacente) : chaque widget se positionne en unités de grille,
    # pas en pixels, pour rester responsive quelle que soit la taille écran.
    colonnes_grille = models.PositiveIntegerField(default=12)
    ordre = models.PositiveIntegerField(default=0)
    visible_client = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_004.py:805:23"),
        help_text=tl("engine/models/_part_004.py:g:847:19"),
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = t("engine/models/_part_004.py:813:25")

    def __str__(self):
        return f"{self.table_source.nom} / {self.nom}"


class WidgetGraphiqueBI(models.Model):
    """Un graphique positionné sur le canevas d'un TableauBordBI : un
    GraphiqueBI choisi dans la bibliothèque, une ou plusieurs colonnes de la
    table source, une position/taille sur la grille. Le rendu (PNG base64)
    est mis en cache tant que l'empreinte des données de la table source n'a
    pas changé — jamais de fichier sur disque : uniquement du texte (base64) en base."""
    TYPE_WIDGET_CHOICES = [
        ("graphique", tl("engine/models/_part_004.py:g:867:23")),
        ("texte", tl("engine/models/_part_004.py:g:868:19")),
        ("carte", tl("engine/models/_part_004.py:g:869:19")),
        ("kpi", "Indicateur KPI"),
    ]
    STYLE_TEXTE_CHOICES = [
        ("titre", td("engine/models/_part_004.py:g:844:19")),
        ("sous_titre", "Sous-titre"),
        ("paragraphe", "Paragraphe"),
    ]

    tableau_bord = models.ForeignKey(TableauBordBI, on_delete=models.CASCADE, related_name="widgets")
    type_widget = models.CharField(
        max_length=10, choices=TYPE_WIDGET_CHOICES, default="graphique",
        help_text=td("engine/models/_part_004.py:g:852:19"),
    )
    # Null uniquement pour un widget de type 'texte' (voir clean()).
    graphique = models.ForeignKey(
        GraphiqueBI, on_delete=models.PROTECT, related_name="+", null=True, blank=True,
    )
    # Renseigné uniquement pour un widget de type 'kpi' : le KPI (models.IndicateurKPI) affiché
    # directement sur le canevas. Supprimer le KPI supprime son widget (CASCADE) ; retirer le
    # widget du canevas ne supprime pas le KPI.
    kpi = models.ForeignKey(
        "IndicateurKPI", on_delete=models.CASCADE, related_name="widgets_canevas",
        null=True, blank=True,
    )
    contenu_texte = models.TextField(
        blank=True, default="",
        help_text=tl("engine/models/_part_004.py:g:898:19"),
    )
    style_texte = models.CharField(
        max_length=12, choices=STYLE_TEXTE_CHOICES, default="paragraphe",
        help_text=tl("engine/models/_part_004.py:854:20"),
    )
    titre = models.CharField(max_length=200, blank=True, default="")
    # Couleur de la carte du widget « #rrggbb » (vide = apparence normale) : en-tête et cadre à l'écran, cadre + teinte dans le PDF.
    # Ne touche jamais au rendu interne du graphique ou de la carte. Voir couleur_colonne.py.
    couleur = models.CharField(max_length=9, blank=True, default="")
    description = models.TextField(
        blank=True, default="",
        help_text=tl("engine/models/_part_004.py:g:911:19"),
    )
    # Colonnes sélectionnées, dans l'ordre choisi par l'utilisateur (ordre
    # significatif pour les graphiques "colonnes_multiples", ex: x puis y).
    champs_cibles = models.ManyToManyField(
        ChampDynamique, blank=True, related_name="+",
        help_text=tl("engine/models/_part_004.py:866:20"),
    )
    ordre_champs = models.JSONField(
        null=True, blank=True,
        help_text=tl("engine/models/_part_004.py:g:922:19"),
    )
    # ── Position/taille sur la grille du canevas (unités de grille, pas px) ──
    position_x = models.PositiveIntegerField(default=0)
    position_y = models.PositiveIntegerField(default=0)
    largeur = models.PositiveIntegerField(default=4)
    hauteur = models.PositiveIntegerField(default=3)
    ordre_z = models.PositiveIntegerField(default=0, help_text=tl("engine/models/_part_004.py:878:65"))

    options_json = models.JSONField(
        null=True, blank=True,
        help_text=tl("engine/models/_part_004.py:882:20"),
    )

    STATUT_CHOICES = [
        ("en_attente", tl("engine/models/_part_004.py:g:938:24")), ("en_cours", tl("engine/models/_part_004.py:g:938:52")),
        ("pret", tl("engine/models/_part_004.py:g:939:18")), ("echec", tl("engine/models/_part_004.py:g:939:37")),
    ]
    statut = models.CharField(max_length=15, choices=STATUT_CHOICES, default="en_attente")
    # ── Rendu en cache — texte uniquement, jamais de fichier (voir docstring) ──
    resultat_image_base64 = models.TextField(blank=True, default="")
    resultat_html = models.TextField(blank=True, default="")
    empreinte_donnees = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text=td("engine/models/_part_004.py:g:915:19"),
    )
    erreur = models.TextField(blank=True, default="")
    rendu_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordre_z", "id"]
        verbose_name = t("engine/models/_part_004.py:905:25")

    def __str__(self):
        if self.type_widget == "texte":
            return tf("engine/models/_part_004.py:g:960:20", v1=self.tableau_bord.nom, v2=self.titre or self.contenu_texte[:30])
        if self.type_widget == "kpi":
            return f"{self.tableau_bord.nom} / [kpi] {self.titre or (self.kpi.nom if self.kpi_id else '')}"
        return f"{self.tableau_bord.nom} / {self.titre or (self.graphique.nom if self.graphique_id else 'carte')}"

    def widget_a_jour(self, empreinte_donnees_actuelle: str) -> bool:
        # Une zone de texte n'a aucune dépendance aux données de la table
        # source : elle est toujours "à jour" une fois enregistrée, jamais
        # relancée par le rafraîchissement live ni par widgets_a_recalculer.
        # carte : suit l'empreinte des donnees comme un graphique (recalculee quand les coordonnees changent).
        # kpi : la valeur vient du KPI lui-même (kpi.kpis_a_recalculer, recalcul sur son propre cache) ;
        # le widget n'a rien à rendre, comme une zone de texte.
        if self.type_widget in ("texte", "kpi"):
            return self.statut == "pret"
        statuts_ok = ("pret", "echec") if self.type_widget == "carte" else ("pret",)
        return bool(
            self.statut in statuts_ok
            and self.empreinte_donnees
            and self.empreinte_donnees == empreinte_donnees_actuelle
        )




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
