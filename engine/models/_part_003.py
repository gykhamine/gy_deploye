from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tfr as _gtfr  # noqa: E402
from engine.i18n import tl
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402
from engine.i18n import td as _gtd  # noqa: E402


class ConfigurationIA(models.Model):
    entreprise = models.OneToOneField(
        Entreprise, on_delete=models.CASCADE, related_name="configuration_ia"
    )

    # ── Plafond de lignes — reglage UNIQUE et GLOBAL pour toute l'entreprise,
    # configure depuis le panneau lateral (au-dessus des boutons "+ nouvelle
    # table" / "+ importer un fichier" / "Configuration IA"). S'applique de
    # facon identique a TOUTE table (native ou issue d'un import de fichier) :
    # voir TableDynamique.plafond_lignes, seul point de lecture. ──
    limite_lignes = models.PositiveIntegerField(
        default=TableDynamique.LIMITE_LIGNES_DEFAUT,
        verbose_name=tl("engine/models/_part_003.py:20:23"),
        help_text=tl("engine/models/_part_003.py:g:22:19"),
    )

    # ── Plafond de colonnes — même logique que limite_lignes, configuré par
    # l'admin depuis le panneau latéral. S'applique à TOUTE table (native ou
    # issue d'un import) : voir plafond_colonnes sur TableDynamique. ──
    LIMITE_COLONNES_DEFAUT = 50
    limite_colonnes = models.PositiveIntegerField(
        default=50,
        verbose_name=tl("engine/models/_part_003.py:33:23"),
        help_text=tl("engine/models/_part_003.py:g:35:19"),
    )

    # ── Plafond d'enregistrements individuels affichés dans le graphe
    # d'ontologie — voir engine/ontologie.py, LIMITE_ENREGISTREMENTS_GRAPHE
    # (valeur par défaut si l'entreprise n'a pas encore de ConfigurationIA).
    # Même convention que limite_lignes : 0 = illimité (déconseillé sur une
    # grosse table, le graphe deviendrait illisible et lent à recalculer). ──
    limite_enregistrements_ontologie = models.PositiveIntegerField(
        default=0,
        verbose_name=tl("engine/models/_part_003.py:47:23"),
        help_text=tl("engine/models/_part_003.py:g:49:19"),
    )

    # ── Moteur d'inférence — local (llama.cpp/IP:port) ou externe (toute API
    # compatible OpenAI : OpenAI, Anthropic, ou tout autre fournisseur). Une
    # fois configuré, le reste du moteur (agent.py, inference.py) fonctionne
    # à l'identique côté client : voir MoteurInference._appeler_llm. ──
    MOTEUR_TYPE_CHOICES = [("local", "Local (IP + port)"), ("externe", "Externe (API compatible OpenAI)")]
    moteur_type = models.CharField(max_length=10, choices=MOTEUR_TYPE_CHOICES, default="local")
    moteur_host = models.CharField(max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_003.py:60:20"))
    moteur_port = models.PositiveIntegerField(null=True, blank=True,
        help_text=tl("engine/models/_part_003.py:62:20"))
    moteur_url_externe = models.CharField(max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_003.py:g:65:19"))
    moteur_modele = models.CharField(max_length=120, blank=True, default="",
        help_text=tl("engine/models/_part_003.py:67:20"))
    moteur_cle_api = models.CharField(max_length=255, blank=True, default="",
        help_text=tl("engine/models/_part_003.py:g:70:19"))

    # ── System prompt (rôle fixe envoyé dans messages[0]) ──
    system_prompt = models.TextField(
        default=tl("engine/models/_part_003.py:g:76:17"),
        help_text=tl("engine/models/_part_003.py:76:20")
    )

    # ── Rôles (injectés dans le user prompt selon la tâche) ──
    role_analysis_script = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:83:13")
        )
    )
    role_skill_script = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:89:13")
        )
    )
    role_report_summary = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:98:13")
        )
    )
    role_other = models.TextField(default=tl("engine/models/_part_003.py:g:102:43"))

    # ── Rôle + règle + contraintes spécifiques au canal document ──
    role_document = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:107:13")
        )
    )
    regle_document = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:115:13")
        )
    )
    contraintes_document = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:124:13")
        )
    )

    # ── Règle de format — mode script (une seule structure désormais,
    # celle qui était propre au skill : le mode skill a été fusionné ici) ──
    regle_script = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:137:13")
        )
    )

    # ── Contraintes de syntaxe Python ──
    contraintes_syntaxe = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:164:13")
        )
    )

    script_exemple = models.TextField(
        blank=True, default=EXEMPLE_SCRIPT_PYTHON,
        help_text=tl("engine/models/_part_003.py:185:20"),
    )
    modules_autorises_script = models.TextField(
        blank=True,
        default="pandas, numpy, scipy, statsmodels, sklearn, matplotlib, matplotlib.pyplot, networkx, math, statistics, json, prophet",
        help_text=tl("engine/models/_part_003.py:g:191:19"),
    )
    modules_autorises_document = models.TextField(
        blank=True,
        default="datetime, math, json, decimal",
        help_text=tl("engine/models/_part_003.py:197:20"),
    )
    modules_autorises_formule = models.TextField(
        blank=True,
        default="pandas, numpy, scipy, statsmodels, math, statistics, decimal, prophet",
        help_text=tl("engine/models/_part_003.py:g:203:19"),
    )
    modules_autorises_graphique_bi = models.TextField(
        blank=True,
        default="pandas, numpy, scipy, matplotlib, matplotlib.pyplot, math, statistics, prophet",
        help_text=tl("engine/models/_part_003.py:g:210:19"),
    )
    modules_autorises_couche_ontologie = models.TextField(
        blank=True,
        default="networkx, math, statistics, collections, itertools",
        help_text=tl("engine/models/_part_003.py:g:217:19"),
    )
    script_exemple_document = models.TextField(
        blank=True, default=EXEMPLE_SCRIPT_DOCUMENT,
        help_text=tl("engine/models/_part_003.py:223:20"),
    )

    # ── Rôle + règle + contraintes spécifiques au canal FORMULE (tables de
    # calcul / FormuleCalcul.code, voir models.py plus bas et
    # inference.py::MoteurInference.generer_formule_calcul). Même principe
    # de configuration que les canaux analyse et document ci-dessus. ──
    role_formule = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:233:13")
        )
    )
    regle_formule = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:242:13")
        )
    )
    contraintes_formule = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:258:13")
        )
    )
    script_exemple_formule_colonne = models.TextField(
        blank=True, default=EXEMPLE_FORMULE_COLONNE,
        help_text=tl("engine/models/_part_003.py:g:273:19"),
    )
    script_exemple_formule_table = models.TextField(
        blank=True, default=EXEMPLE_FORMULE_TABLE,
        help_text=tl("engine/models/_part_003.py:g:278:19"),
    )

    # ── Rôle + règle + contraintes spécifiques au canal GRAPHIQUE_BI (bibliothèque
    # de graphiques des tableaux de bord BI, voir GraphiqueBI.code et
    # inference.py::MoteurInference.generer_graphique_bi). Même principe de
    # configuration que les canaux formule/document/analyse ci-dessus — permet de
    # générer via l'IA un nouveau graphique personnalisé, comme pour une formule. ──
    role_graphique_bi = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:289:13")
        )
    )
    regle_graphique_bi = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:298:13")
        )
    )
    contraintes_graphique_bi = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:317:13")
        )
    )
    script_exemple_graphique_bi_colonne = models.TextField(
        blank=True, default=EXEMPLE_GRAPHIQUE_BI_COLONNE,
        help_text=tl("engine/models/_part_003.py:g:335:19"),
    )
    script_exemple_graphique_bi_multiples = models.TextField(
        blank=True, default=EXEMPLE_GRAPHIQUE_BI_MULTIPLES,
        help_text=tl("engine/models/_part_003.py:g:340:19"),
    )

    # ── Rôle + règle + contraintes spécifiques au canal COUCHE_ONTOLOGIE
    # (couches personnalisées du graphe d'entourage, voir CoucheOntologie.code
    # et inference.py::MoteurInference.generer_couche_ontologie). Même
    # principe de configuration que les canaux formule/graphique_bi
    # ci-dessus — permet de générer via l'IA une nouvelle couche/relation
    # personnalisée, décrite en langage naturel, plutôt que de l'écrire
    # à la main. ──
    role_couche_ontologie = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:353:13")
        )
    )
    regle_couche_ontologie = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:364:13")
        )
    )
    contraintes_couche_ontologie = models.TextField(
        default=(
            tl("engine/models/_part_003.py:g:385:13")
        )
    )
    script_exemple_couche_ontologie = models.TextField(
        blank=True, default=EXEMPLE_COUCHE_ONTOLOGIE,
        help_text=tl("engine/models/_part_003.py:401:20"),
    )

    # ── Canal MODELE_IA : scripts qui ajoutent un type de modèle DL/ML au Trainer
    # (IA/plugins_modeles.py ; inference.py::MoteurInference.generer_modele_ia). ──
    role_modele_ia = models.TextField(default=DEFAULT_ROLES["modele_ia"])
    regle_modele_ia = models.TextField(default=DEFAULT_REGLE_MODELE_IA)
    contraintes_modele_ia = models.TextField(default=DEFAULT_CONTRAINTES_MODELE_IA)
    script_exemple_modele_ia = models.TextField(blank=True, default=EXEMPLE_MODELE_IA)
    modules_autorises_modele_ia = models.TextField(
        blank=True, default="numpy, sklearn, math, statistics, json",
        help_text=tl("engine/models/_part_003.py:g:413:19"),
    )

    # ── Canal TACHE : scripts de tâches planifiées / conditionnelles générés par l'IA
    # (agent.py::generer_script_tache → inference.py::_build_prompt, mode="tache"). ──
    role_tache = models.TextField(default=DEFAULT_ROLES["tache"])
    regle_tache = models.TextField(default=DEFAULT_REGLE_TACHE)
    contraintes_tache = models.TextField(default=DEFAULT_CONTRAINTES_TACHE)
    script_exemple_tache = models.TextField(blank=True, default=EXEMPLE_TACHE)
    modules_autorises_tache = models.TextField(
        blank=True, default="pandas, numpy, math, statistics, json, datetime",
        help_text=tl("engine/models/_part_003.py:g:424:19"),
    )

    # ── Mise en page & branding du document (canal DOCUMENT) ──
    # Ces champs pilotent à la fois le rendu PDF (reports.py) et le contexte
    # transmis à l'IA (agent.py::_contexte_document), pour que le script
    # généré reste cohérent avec ce que l'entreprise a configuré.
    POSITION_CHOICES = [
        ("gauche", "Gauche"), ("centre", "Centre"), ("droite", "Droite"),
    ]
    SECTIONS_DEFAUT = ["entete", "parties", "lignes", "totaux", "notes", "pied_page"]
    SECTIONS_LIBELLES = {
        "entete": tl("engine/models/_part_003.py:g:436:19"),
        "parties": tl("engine/models/_part_003.py:g:437:20"),
        "lignes": tl("engine/models/_part_003.py:g:438:19"),
        "totaux": "Totaux",
        "notes": "Notes & conditions",
        "pied_page": tl("engine/models/_part_003.py:g:441:22"),
    }

    nom_affichage_document = models.CharField(
        max_length=200, blank=True, default="",
        help_text=tl("engine/models/_part_003.py:423:20"),
    )
    afficher_logo_document = models.BooleanField(
        default=True, verbose_name=tl("engine/models/_part_003.py:426:37"),
    )
    position_logo_document = models.CharField(
        max_length=10, choices=POSITION_CHOICES, default="gauche",
    )
    couleur_accent_document = models.CharField(
        max_length=7, default="#e8a33d",
        help_text=tl("engine/models/_part_003.py:433:20"),
    )
    couleur_entete_document = models.CharField(
        max_length=7, default="#1f2937",
        help_text=tl("engine/models/_part_003.py:437:20"),
    )
    couleur_tableau_entete_document = models.CharField(
        max_length=7, default="#0d1117",
        help_text=tl("engine/models/_part_003.py:441:20"),
    )
    couleur_lignes_alternees_document = models.CharField(
        max_length=7, default="#f3f4f6",
        help_text=tl("engine/models/_part_003.py:445:20"),
    )
    couleur_texte_document = models.CharField(
        max_length=7, default="#1a1a2e",
        help_text=tl("engine/models/_part_003.py:449:20"),
    )
    couleur_fond_document = models.CharField(
        max_length=7, default="#ffffff",
        help_text=tl("engine/models/_part_003.py:453:20"),
    )
    couleur_corps_document = models.CharField(
        max_length=7, default="#ffffff",
        help_text=tl("engine/models/_part_003.py:457:20"),
    )
    couleur_pied_page_document = models.CharField(
        max_length=7, default="#f3f4f6",
        help_text=tl("engine/models/_part_003.py:461:20"),
    )
    # Police, texte de pied de page et numéro de page : les mêmes sur TOUS les PDF de l'entreprise (reçus, résumé général, listes, rapports, cartes).
    POLICES_DOCUMENT_CHOICES = [
        ("helvetica", td("engine/models/_part_003.py:g:321:23")), ("times", td("engine/models/_part_003.py:g:321:66")), ("courier", "Chasse fixe (Courier)"),
    ]
    police_document = models.CharField(max_length=12, choices=POLICES_DOCUMENT_CHOICES, default="helvetica")
    texte_pied_page_document = models.CharField(max_length=150, blank=True, default="")
    numeroter_pages_document = models.BooleanField(default=True)
    # NOTE : la photo d'entité, les signatures et le cachet ne sont PLUS
    # sur ce modèle — ils sont désormais exclusivement sur
    # ConfigurationDocumentContexte (indépendants par contexte 'table' /
    # 'ia' / 'systeme'), voir configuration_pour() ci-dessous. Ancienne
    # duplication retirée par migration 0006 (les valeurs enregistrées
    # depuis la page dédiée /configuration-documents/ étaient déjà
    # exclusivement lues depuis ConfigurationDocumentContexte ; ces champs
    # ici n'avaient plus aucun effet sur le rendu PDF).

    modifie_le = models.DateTimeField(auto_now=True)

    def configuration_pour(self, contexte: str):
        """Retourne un proxy fusionnant :
          - les réglages OBLIGATOIRES SUR TOUT DOCUMENT (logo, couleurs,
            position du logo, nom d'affichage) — restent sur CE modèle
            (ConfigurationIA), partagés par tous les contextes ;
          - les réglages INDÉPENDANTS PAR CONTEXTE (photo de l'entité,
            sections actives, signatures, cachet) — tirés de
            ConfigurationDocumentContexte pour `contexte`.

        `contexte` doit être l'une des valeurs de
        ConfigurationDocumentContexte.CONTEXTE_CHOICES :
          - 'table'   : exports PDF de table dynamique
          - 'ia'      : cartes de document unique générées par le moteur IA
                        (factures/devis/etc.) et rapports d'analyse
          - 'systeme' : reçus de soumission + liste des employés

        Le proxy expose exactement les mêmes noms d'attribut/méthode que
        ConfigurationIA (afficher_cachet_document, cachet_pour_etat, etc.),
        donc les fonctions de reports.py qui consommaient `cfg` directement
        n'ont pas besoin d'être réécrites — seul CE QU'ON LEUR PASSE change."""
        sous_config, _ = ConfigurationDocumentContexte.objects.get_or_create(
            entreprise_id=self.entreprise_id, contexte=contexte,
        )
        return _ConfigurationDocumentProxy(self, sous_config, personnalisation_document(self.entreprise_id, contexte))

    # ── SMS (page "sms/", voir sms_mmcli.py) — déclarés ici car déjà
    # ajoutés en base par la migration 0135 mais jamais reportés sur le
    # modèle (bug préexistant : les vues qui les lisaient/écrivaient
    # fonctionnaient par accident, Django autorisant l'accès dynamique aux
    # colonnes chargées par une requête, mais toute instance construite en
    # mémoire — ConfigurationIA() — plantait). Corrigé au passage car les
    # notifications SMS (notifications_sms_actif ci-dessous) réutilisent
    # ce même modem. ──
    sms_modem_id = models.PositiveIntegerField(
        blank=True, null=True,
        help_text=tl("engine/models/_part_003.py:g:539:19"),
        verbose_name=tl("engine/models/_part_003.py:511:23"),
    )
    sms_message_modele = models.TextField(
        blank=True, default="",
        help_text=td("engine/models/_part_003.py:g:377:19"),
        verbose_name=tl("engine/models/_part_003.py:517:23"),
    )

    # ── Notifications internes (agents/admin) ───────────────────────────
    notifications_sms_actif = models.BooleanField(
        default=False,
        verbose_name=tl("engine/models/_part_003.py:523:23"),
        help_text=tl("engine/models/_part_003.py:g:554:19"),
    )

    # ── Réglages de la mesure des critères (engine/reglages_criteres.py) : exposants, plafond de l'écart à
    # l'objectif, échelles calibrables et apport a priori du modèle. Vide = défauts / calibrage automatique.
    # Validé à l'écriture (api_reglages_criteres) et relu avec la même validation. ──
    reglages_criteres = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = t("engine/models/_part_003.py:532:25")

    def __str__(self):
        return f"Config IA — {self.entreprise.nom}"


class ConfigurationDocumentContexte(models.Model):
    """Réglages de mise en page PDF indépendants par contexte de document —
    voir ConfigurationIA.configuration_pour pour l'usage. Trois contextes :
    'table' (exports de table), 'ia' (cartes de document / rapports générés
    par le moteur IA), 'systeme' (reçus + liste des employés). Les réglages
    obligatoires sur TOUT document (logo, couleurs, position du logo, nom
    d'affichage) restent sur ConfigurationIA — volontairement absents ici."""
    CONTEXTE_CHOICES = [
        ("table", td("engine/models/_part_003.py:g:409:19")),
        ("ia", td("engine/models/_part_003.py:g:410:16")),
        ("systeme", td("engine/models/_part_003.py:g:411:21")),
        ("courrier", td("engine/models/_part_003.py:ctx_courrier")),   # documents rédigés dans l'éditeur (notes de service, attestations…)
    ]

    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE,
        related_name="configurations_document_contexte",
    )
    contexte = models.CharField(max_length=10, choices=CONTEXTE_CHOICES)

    # ── Photo de l'entité (indépendante par contexte) ──
    afficher_photo_entite = models.BooleanField(
        default=False, verbose_name=tl("engine/models/_part_003.py:559:38"),
    )
    image_entite = models.ImageField(
        upload_to="documents/photo_entite/", blank=True, null=True,
        verbose_name=tl("engine/models/_part_003.py:563:23"),
    )
    position_photo_entite = models.CharField(
        max_length=20,
        choices=[("entete", _gtfr("engine/models/_part_003.py:434:29")), ("partie_destinataire", "Bloc destinataire")],
        default="entete",
    )

    # ── Sections actives (n'a de sens que pour le contexte 'ia', mais reste
    # disponible partout pour rester générique) ──
    sections_document = models.JSONField(
        default=list, blank=True,
        help_text=tl("engine/models/_part_003.py:575:20"),
    )

    # ── Entête officiel (lignes gauche / droite, voir entete_officiel.py) : activé PAR CONTEXTE par l'administrateur ;
    # toujours actif pour le contexte « courrier » dès que des lignes sont définies. ──
    afficher_entete_officiel = models.BooleanField(default=False)
    afficher_coordonnees_entete = models.BooleanField(default=False)   # nom + coordonnées de l'entreprise sous l'entête

    # ── Signatures ──
    afficher_signatures_document = models.BooleanField(
        default=False, verbose_name=tl("engine/models/_part_003.py:580:38"),
    )
    position_signatures_document = models.CharField(
        max_length=20,
        choices=[("notes", tl("engine/models/_part_003.py:g:447:28")), ("pied_page", tl("engine/models/_part_003.py:g:447:62"))],
        default="pied_page",
    )

    # ── Cachet ──
    afficher_cachet_document = models.BooleanField(
        default=False, verbose_name=tl("engine/models/_part_003.py:590:38"),
    )
    cachet_image_actif = models.ImageField(
        upload_to="documents/cachets/", blank=True, null=True,
        verbose_name=tl("engine/models/_part_003.py:594:23"),
    )
    cachet_image_inactif = models.ImageField(
        upload_to="documents/cachets/", blank=True, null=True,
        verbose_name=tl("engine/models/_part_003.py:598:23"),
    )
    cachet_texte_actif = models.CharField(
        max_length=40, blank=True, default=_gtfr("engine/models/_part_003.py:473:44"), verbose_name=tl("engine/models/_part_003.py:601:66"),
    )
    cachet_texte_inactif = models.CharField(
        max_length=40, blank=True, default=_gtfr("engine/models/_part_003.py:476:44"), verbose_name=tl("engine/models/_part_003.py:604:70"),
    )
    champ_statut_cachet = models.CharField(
        max_length=120, blank=True, default="",
        help_text=td("engine/models/_part_003.py:g:471:19"),
    )
    position_cachet_document = models.CharField(
        max_length=20,
        choices=[("entete", _gtfr("engine/models/_part_003.py:484:29")), ("totaux", "Bloc totaux"), ("pied_page", tl("engine/models/_part_003.py:g:475:82"))],
        default="totaux",
    )
    table_cachet_document = models.ForeignKey(
        "TableDynamique", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=tl("engine/models/_part_003.py:618:41"),
        help_text=tl("engine/models/_part_003.py:g:654:19"),
    )
    type_document_cachet = models.CharField(
        max_length=20, blank=True, default="",
        verbose_name=tl("engine/models/_part_003.py:624:23"),
        help_text=td("engine/models/_part_003.py:g:487:19"),
    )
    document_cachet = models.ForeignKey(
        "DocumentGenere", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=tl("engine/models/_part_003.py:633:41"),
        help_text=tl("engine/models/_part_003.py:g:669:19"),
    )
    demande_cachet = models.ForeignKey(
        "DemandeAgent", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name=tl("engine/models/_part_003.py:642:41"),
        help_text=tl("engine/models/_part_003.py:g:678:19"),
    )

    modifie_le = models.DateTimeField(auto_now=True)

    def sections_document_actives(self):
        """Retourne la liste ordonnée des sections valides, avec repli sur l'ordre par défaut."""
        valides = [s for s in (self.sections_document or []) if s in ConfigurationIA.SECTIONS_DEFAUT]
        return valides or list(ConfigurationIA.SECTIONS_DEFAUT)

    def cachet_pour_etat(self, actif: bool):
        if actif:
            return (self.cachet_texte_actif or _gtfr("engine/models/_part_003.py:517:48")), self.cachet_image_actif
        return (self.cachet_texte_inactif or t("engine/models/_part_003.py:g:695:46")), self.cachet_image_inactif

    class Meta:
        unique_together = [("entreprise", "contexte")]
        verbose_name = t("engine/models/_part_003.py:664:25")
        verbose_name_plural = t("engine/models/_part_003.py:665:32")

    def __str__(self):
        return f"Config document [{self.get_contexte_display()}] — {self.entreprise.nom}"


class _ConfigurationDocumentProxy:
    """Objet retourné par ConfigurationIA.configuration_pour(contexte) — ne
    correspond à aucune table ; fusionne à la lecture ConfigurationIA (champs
    partagés/obligatoires) et ConfigurationDocumentContexte (champs propres
    au contexte demandé). Voir docstring de configuration_pour."""

    _CHAMPS_CONTEXTE = (
        "afficher_entete_officiel", "afficher_coordonnees_entete",
        "afficher_photo_entite", "image_entite", "position_photo_entite",
        "sections_document",
        "afficher_signatures_document", "position_signatures_document",
        "afficher_cachet_document", "cachet_image_actif", "cachet_image_inactif",
        "cachet_texte_actif", "cachet_texte_inactif", "champ_statut_cachet",
        "position_cachet_document", "table_cachet_document", "table_cachet_document_id",
        "type_document_cachet", "document_cachet", "document_cachet_id",
        "demande_cachet", "demande_cachet_id",
    )

    # Réglages que l'agent peut personnaliser (vide = hérite de l'entreprise).
    _PERSO_SIMPLES = ("afficher_signatures_document", "position_signatures_document",
                      "afficher_cachet_document", "position_cachet_document")

    def __init__(self, cfg_ia, sous_config, perso=None):
        object.__setattr__(self, "_cfg_ia", cfg_ia)
        object.__setattr__(self, "_sous", sous_config)
        object.__setattr__(self, "_perso", perso)

    def __getattr__(self, nom):
        # Appelé seulement si l'attribut n'existe pas déjà sur le proxy
        # lui-même (donc jamais pour _cfg_ia / _sous / _perso).
        if nom in self._CHAMPS_CONTEXTE:
            if self._perso is not None and nom in self._PERSO_SIMPLES:
                valeur = getattr(self._perso, nom)
                if valeur not in (None, ""):
                    return valeur
            return getattr(self._sous, nom)
        return getattr(self._cfg_ia, nom)

    def sections_document_actives(self):
        return self._sous.sections_document_actives()

    def cachet_pour_etat(self, actif):
        texte, image = self._sous.cachet_pour_etat(actif)
        p = self._perso
        if p is not None:
            texte_p, image_p = ((p.cachet_texte_actif, p.cachet_image_actif) if actif
                                else (p.cachet_texte_inactif, p.cachet_image_inactif))
            texte, image = (texte_p or texte), (image_p or image)
        return texte, image

    @property
    def contexte(self):
        return self._sous.contexte


class SignatureDocument(models.Model):
    """Une signature (image + nom/fonction) utilisable sur les documents
    générés. Une entreprise peut en avoir plusieurs (ex. Directeur Général
    + Responsable Comptable) ; chacune s'active/se désactive individuellement
    sans supprimer les autres — voir ConfigurationIA.afficher_signatures_document
    pour l'interrupteur global du bloc."""
    entreprise = models.ForeignKey(
        Entreprise, on_delete=models.CASCADE, related_name="signatures_document",
    )
    table = models.ForeignKey(
        "TableDynamique", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="signatures_document", verbose_name=tl("engine/models/_part_003.py:721:59"),
        help_text=td("engine/models/_part_003.py:g:576:19"),
    )
    document = models.ForeignKey(
        "DocumentGenere", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="signatures_document", verbose_name=tl("engine/models/_part_003.py:728:59"),
        help_text=tl("engine/models/_part_003.py:g:764:19"),
    )
    demande = models.ForeignKey(
        "DemandeAgent", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="signatures_document", verbose_name=tl("engine/models/_part_003.py:736:59"),
        help_text=td("engine/models/_part_003.py:g:588:19"),
    )
    nom = models.CharField(
        max_length=150, blank=True, default="",
        verbose_name=tl("engine/models/_part_003.py:745:23"),
        help_text=tl("engine/models/_part_003.py:746:20"),
    )
    image = models.ImageField(
        upload_to="documents/signatures/", verbose_name=tl("engine/models/_part_003.py:749:58"),
    )
    actif = models.BooleanField(
        default=True, verbose_name=tl("engine/models/_part_003.py:752:37"),
    )
    ordre = models.PositiveIntegerField(default=0)
    # Fonction imprimée sous la signature (« Le Directeur Général ») et types de documents administratifs pour lesquels
    # cette personne signe (liste séparée par des virgules ; vide = tous les types).
    fonction = models.CharField(max_length=150, blank=True, default="")
    types_document = models.CharField(max_length=200, blank=True, default="")
    # Vide = signature de l'ENTREPRISE (posée par l'administrateur, valable pour tous). Renseigné = signature PERSONNELLE
    # d'un agent : elle n'est utilisée que sur les documents qu'il génère lui-même (voir signatures_document_pour).
    membre = models.ForeignKey(
        "MembreEntreprise", on_delete=models.CASCADE, null=True, blank=True, related_name="signatures_perso",
    )

    class Meta:
        ordering = ["ordre", "id"]
        verbose_name = t("engine/models/_part_003.py:758:25")

    def __str__(self):
        return self.nom or f"Signature #{self.pk}"

    def liste_types(self):
        return [x for x in (self.types_document or "").split(",") if x]

    def applique_au_type(self, type_document):
        """Document administratif de ce type : la signature s'imprime-t-elle ? Sans restriction : toujours. Hors documents
        administratifs (type_document=None), une signature réservée à certains types ne s'imprime pas."""
        types = self.liste_types()
        if not types:
            return True
        return type_document is not None and type_document in types


# ─────────────────────────────────────────────────────────────
# AUDIT (cahier des charges Ontology, section 13 — « AUDIT » / Phase 13 :
# renforce JournalAudit avec les champs structures demandes — acteur/type
# d'acteur, objet cible, ancienne/nouvelle valeur, source, IP, agent et
# fonction utilises, resultat — sans toucher a l'ecriture inconditionnelle
# deja en place (actions.py, middleware.py, agent.py). Ces trois points
# d'ecriture construisaient chacun leur propre dict pour
# JournalAudit.objects.create (duplication) : `enregistrer()` ci-dessous
# est desormais le point unique, reutilise par les trois.
# ─────────────────────────────────────────────────────────────
class JournalAudit(models.Model):
    ACTEUR_CHOICES = [
        ("utilisateur", tl("engine/models/_part_003.py:g:811:25")),
        ("agent_ia", tl("engine/models/_part_003.py:g:812:22")),
        ("cle_api", tl("engine/models/_part_003.py:g:813:21")),
        ("systeme", tl("engine/models/_part_003.py:g:814:21")),
    ]

    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="journaux")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=255)
    details = models.JSONField(blank=True, null=True)
    horodatage = models.DateTimeField(auto_now_add=True)

    type_acteur = models.CharField(max_length=20, choices=ACTEUR_CHOICES, default="utilisateur",
                                    verbose_name=tl("engine/models/_part_003.py:789:51"))
    source = models.CharField(max_length=20, blank=True, default="", verbose_name=tl("engine/models/_part_003.py:790:84"))
    cible_type = models.CharField(max_length=50, blank=True, default="", verbose_name=tl("engine/models/_part_003.py:791:88"))
    cible_id = models.PositiveIntegerField(null=True, blank=True, verbose_name=tl("engine/models/_part_003.py:792:81"))
    agent_utilise = models.CharField(max_length=150, blank=True, default="", verbose_name=tl("engine/models/_part_003.py:793:92"))
    fonction_utilisee = models.CharField(max_length=150, blank=True, default="", verbose_name=tl("engine/models/_part_003.py:794:96"))
    resultat = models.CharField(max_length=20, blank=True, default="succes", verbose_name=tl("engine/models/_part_003.py:795:92"))
    ip = models.CharField(max_length=45, blank=True, default="", verbose_name=tl("engine/models/_part_003.py:796:80"))
    ancienne_valeur = models.JSONField(blank=True, null=True, verbose_name=tl("engine/models/_part_003.py:797:77"))
    nouvelle_valeur = models.JSONField(blank=True, null=True, verbose_name=tl("engine/models/_part_003.py:798:77"))

    @classmethod
    def enregistrer(cls, *, entreprise, action, utilisateur=None, details=None,
                     type_acteur="utilisateur", source="", cible_type="", cible_id=None,
                     agent_utilise="", fonction_utilisee="", resultat="succes",
                     ip="", ancienne_valeur=None, nouvelle_valeur=None):
        """Point d'ecriture unique de l'audit (Phase 13). Reprend exactement
        les memes 5 arguments que les 3 sites d'appel existants passaient
        deja a .objects.create (entreprise/utilisateur/action/details), en
        ajoutant les champs structures ci-dessus (tous optionnels, valeurs
        par defaut neutres) — aucune regression pour un appelant qui ne les
        renseigne pas encore. `entreprise` accepte soit une instance, soit
        un id (ex. middleware.AuditMiddleware, qui n'a chargé que
        membre.entreprise_id via .only(), pour éviter une requête de plus)."""
        cle_entreprise = {"entreprise_id": entreprise} if isinstance(entreprise, int) else {"entreprise": entreprise}
        return cls.objects.create(
            **cle_entreprise, utilisateur=utilisateur, action=action[:255], details=details,
            type_acteur=type_acteur, source=source[:20], cible_type=cible_type[:50], cible_id=cible_id,
            agent_utilise=agent_utilise[:150], fonction_utilisee=fonction_utilisee[:150],
            resultat=resultat[:20], ip=ip[:45],
            ancienne_valeur=ancienne_valeur, nouvelle_valeur=nouvelle_valeur,
        )


# ─────────────────────────────────────────────────────────────
# DOCUMENTS GÉNÉRÉS : factures, reçus, bons, devis, etc.
# L'IA génère un script pandas qui retourne un dict structuré
# {type_document, titre, destinataire, emetteur, lignes,
#  totaux, notes, pied_page}. L'agent construit le PDF avec
# mise en page professionnelle via reports.py.
# ─────────────────────────────────────────────────────────────
class DocumentGenere(models.Model):
    TYPE_CHOICES = [
        ("facture",  "Facture"),
        ("recu",     td("engine/models/_part_003.py:g:684:22")),
        ("bon",      td("engine/models/_part_003.py:g:685:22")),
        ("devis",    "Devis"),
        ("rapport",  _gtfr("engine/models/_part_003.py:722:22")),
        ("autre",    "Autre"),
    ]
    STATUT_CHOICES = [
        ("en_cours",          tl("engine/models/_part_003.py:g:875:31")),
        ("script_genere",     tl("engine/models/_part_003.py:g:876:31")),
        ("execute",           tl("engine/models/_part_003.py:g:877:31")),
        ("echec_generation",  tl("engine/models/_part_003.py:g:878:31")),
        ("echec_execution",   tl("engine/models/_part_003.py:g:879:31")),
        ("echec",             tl("engine/models/_part_003.py:g:880:31")),
        ("brouillon",        tl("engine/models/_part_003.py:g:881:30")),   # préparé d'avance (fantome_propositions.py) : ni script ni fichier, rien n'est généré
    ]
    entreprise   = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="documents")
    utilisateur  = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    table_cible  = models.ForeignKey(TableDynamique, on_delete=models.SET_NULL, null=True, blank=True)
    enregistrement_cible = models.ForeignKey(
        "Enregistrement", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents",
        help_text=tl("engine/models/_part_003.py:853:20"),
    )
    type_document = models.CharField(max_length=20, choices=TYPE_CHOICES, default="facture")
    titre        = models.CharField(max_length=255, default="", blank=True)
    instructions = models.TextField(default="", blank=True,
                       help_text=tl("engine/models/_part_003.py:858:35"))
    parametres   = models.JSONField(default=dict, blank=True,
                       help_text=tl("engine/models/_part_003.py:860:35"))
    script_genere = models.TextField(blank=True)
    fichier_script = models.CharField(max_length=500, blank=True)
    resultat_json  = models.JSONField(blank=True, null=True)
    fichier_pdf    = models.FileField(upload_to="documents/%Y/%m/", blank=True, null=True)
    modifie_manuellement = models.BooleanField(default=False)
    couleur      = models.CharField(max_length=9, blank=True, default="")   # couleur de la carte (« #rrggbb » ou « #rrggbbaa »), comme les cartes de rapport
    statut       = models.CharField(max_length=20, choices=STATUT_CHOICES, default="en_cours")
    erreur       = models.TextField(blank=True)
    cree_le      = models.DateTimeField(auto_now_add=True)
    execute_le   = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.get_type_document_display()} — {self.titre or self.id}"


# ─────────────────────────────────────────────────────────────
# ENTRAINEMENT / INFERENCE IA (module externe `IA`, greffe sur le moteur
# de tables existant : les tables dynamiques SONT les datasets, sans
# stockage supplementaire. Voir engine/ia_integration.py pour la logique
# de resolution dataset -> ndarray et d'orchestration entrainement/inference,
# qui reutilise les fonctions deja presentes dans views.py (entreprise
# courante, controle d'acces lecteur, etc.).
# ─────────────────────────────────────────────────────────────


class PersonnalisationDocumentMembre(models.Model):
    """Personnalisation des documents PAR AGENT (selon son poste) : cachet, affichage et position du cachet et des
    signatures. Tout ce qui est vide (None / "") HÉRITE de l'entreprise (ConfigurationDocumentContexte, fixée par
    l'administrateur). Ne contient jamais ce qui identifie l'entreprise (logo, couleurs, nom, pied de page, sections),
    réservé à l'administrateur. Ses signatures personnelles sont des SignatureDocument avec `membre`."""
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="personnalisations_document")
    contexte = models.CharField(max_length=10)   # 'table' / 'ia' / 'systeme' (voir ConfigurationDocumentContexte)
    afficher_signatures_document = models.BooleanField(null=True, blank=True)
    position_signatures_document = models.CharField(max_length=20, blank=True, default="")
    afficher_cachet_document = models.BooleanField(null=True, blank=True)
    position_cachet_document = models.CharField(max_length=20, blank=True, default="")
    cachet_texte_actif = models.CharField(max_length=40, blank=True, default="")
    cachet_texte_inactif = models.CharField(max_length=40, blank=True, default="")
    cachet_image_actif = models.ImageField(upload_to="documents/cachets/", blank=True, null=True)
    cachet_image_inactif = models.ImageField(upload_to="documents/cachets/", blank=True, null=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("membre", "contexte")]

    def __str__(self):
        return f"Personnalisation document [{self.contexte}] — membre {self.membre_id}"


class EnteteOfficiel(models.Model):
    """Lignes de l'entête OFFICIEL d'un document : bloc gauche (ministère, direction, unité…) et bloc droite
    (république, devise…). Sans `membre` = lignes IMPOSÉES par l'administrateur (toujours imprimées en premier) ;
    avec `membre` = complément de cet agent (imprimé à la suite, jamais à la place). Chaque ligne est un dictionnaire
    {texte, gras, italique, souligne, align} — voir entete_officiel.normaliser_lignes."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="entetes_officiels")
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, null=True, blank=True,
                               related_name="entetes_officiels")
    lignes_gauche = models.JSONField(default=list, blank=True)
    lignes_droite = models.JSONField(default=list, blank=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["entreprise"], condition=models.Q(membre__isnull=True),
                                    name="entete_officiel_unique_entreprise"),
            models.UniqueConstraint(fields=["membre"], condition=models.Q(membre__isnull=False),
                                    name="entete_officiel_unique_membre"),
        ]

    def __str__(self):
        return _gtf("rg6.msg.036", v1=_gt("rg6.msg.037") if self.membre_id is None else _gtf("rg6.msg.038", v1=self.membre_id))


class ModeleEntete(models.Model):
    """Modèle d'entête NOMMÉ (« Note de service », « Attestation », « Courrier »…) : bloc gauche et bloc droite imposés par
    l'administrateur, toujours imprimés en premier. L'administrateur les crée, les modifie, les supprime et fixe celui par
    défaut ; l'agent choisit seulement (DocumentAdministratif.modele_entete, vide = modèle par défaut). Le complément
    personnel de l'agent (EnteteOfficiel avec membre) s'ajoute toujours à leur suite. Chaque ligne est un dictionnaire
    {texte, gras, italique, souligne, align} — voir entete_officiel.normaliser_lignes."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="modeles_entete")
    nom = models.CharField(max_length=100)
    lignes_gauche = models.JSONField(default=list, blank=True)
    lignes_droite = models.JSONField(default=list, blank=True)
    ordre = models.PositiveIntegerField(default=0)
    par_defaut = models.BooleanField(default=False)

    class Meta:
        ordering = ["ordre", "id"]
        constraints = [
            models.UniqueConstraint(fields=["entreprise", "nom"], name="modele_entete_nom_unique_entreprise"),
            models.UniqueConstraint(fields=["entreprise"], condition=models.Q(par_defaut=True),
                                    name="modele_entete_defaut_unique_entreprise"),
        ]

    def __str__(self):
        return self.nom


class ReglageEntete(models.Model):
    """Réglages d'entête d'UNE personne (administrateur ou agent) : l'interrupteur « en-têtes gauche et droite » de l'onglet Action
    et la NUMÉROTATION des documents administratifs, imprimée en dernière ligne du bloc gauche.

    La numérotation n'est PAS un moteur générique : `numerotation_modele` est un texte libre que la personne écrit
    (« N°…/MFPP/DGID/2026 »). Seule l'aide facultative `numerotation_auto` remplace {n} (compteur) et {année} dans ce texte ;
    le compteur n'avance qu'à l'ÉMISSION du document (voir entete_officiel.attribuer_numero), jamais à l'aperçu."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="reglages_entete")
    membre = models.OneToOneField("MembreEntreprise", on_delete=models.CASCADE, related_name="reglage_entete")
    entetes_actifs = models.BooleanField(default=True)          # éteint : les blocs gauche / droite ne sont pas pris en compte
    numerotation_active = models.BooleanField(default=False)    # allumé : une ligne de numéro termine le bloc gauche
    numerotation_modele = models.CharField(max_length=120, blank=True, default="")
    numerotation_auto = models.BooleanField(default=False)      # aide : compteur automatique ({n}, {année}) à l'émission
    numerotation_annee = models.PositiveSmallIntegerField(default=0)
    numerotation_dernier = models.PositiveIntegerField(default=0)


class DocumentAdministratif(models.Model):
    """Document rédigé dans l'éditeur (note de service, attestation, lettre…) : un titre et un corps fait de blocs
    ordonnés {type, texte} (types : voir TYPES_BLOCS dans reports/_part_005.py). L'entête officiel, le cachet et les signatures ne sont
    PAS stockés ici : ils sont ajoutés à chaque génération du PDF selon les réglages du contexte « courrier »."""
    STATUT_BROUILLON, STATUT_SOUMIS, STATUT_EMIS, STATUT_ANNULE = "brouillon", "soumis", "emis", "annule"
    STATUT_CHOICES = [(STATUT_BROUILLON, "Brouillon"), (STATUT_SOUMIS, _gtd("rg6.msg.039")), (STATUT_EMIS, _gtfr("engine/models/_part_003.py:867:108")),
                      (STATUT_ANNULE, _gtfr("engine/models/_part_003.py:868:39"))]
    # Seuls ces champs peuvent encore changer une fois le document émis (l'annulation, l'archivage) ; tout le reste est figé.
    CHAMPS_ANNULATION = frozenset({"statut", "annule_le", "annule_par", "motif_annulation", "modifie_le"})
    CHAMPS_ARCHIVAGE = frozenset({"archive", "archive_le", "modifie_le"})
    # Le nom de classement (libellé interne, jamais imprimé) se change à tout moment, quel que soit le statut.
    CHAMPS_RENOMMAGE = frozenset({"nom", "modifie_le"})
    CHAMPS_POST_EMISSION = CHAMPS_ANNULATION | CHAMPS_ARCHIVAGE | CHAMPS_RENOMMAGE
    # Types de documents (un compteur de référence et des signataires par type) et mentions de délégation de signature.
    TYPES_DOCUMENT = (("courrier", "Courrier"), ("note_service", _gtfr("engine/models/_part_003.py:877:66")), ("attestation", "Attestation"),
                      ("arrete", _gtfr("engine/models/_part_003.py:877:34")), ("decision", _gtfr("engine/models/_part_003.py:877:60")))
    DELEGATION_CHOICES = [("", "—"), ("po", "P.O."), ("p", "P/")]
    MENTIONS_DELEGATION = {"po": "P.O.", "p": "P/"}
    POSITIONS_DATE = [("bas", _gtd("rg6.msg.040")), ("haut", _gtd("rg6.msg.041")), ("aucune", _gtd("rg6.msg.042"))]

    @classmethod
    def normaliser_type(cls, valeur):
        valeur = str(valeur or "").strip()
        return valeur if valeur in dict(cls.TYPES_DOCUMENT) else "courrier"

    @classmethod
    def normaliser_date_position(cls, valeur):
        valeur = str(valeur or "").strip().lower()
        return valeur if valeur in dict(cls.POSITIONS_DATE) else "bas"

    @classmethod
    def normaliser_delegation(cls, valeur):
        valeur = str(valeur or "").strip().lower()
        return valeur if valeur in cls.MENTIONS_DELEGATION else ""

    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="documents_administratifs")
    auteur = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="documents_administratifs")
    titre = models.CharField(max_length=200, blank=True, default="")
    # Nom de classement : ce qu'on voit dans la liste. Modifiable à tout moment (même émis ou archivé) car il n'est jamais
    # imprimé : le titre, lui, fait partie du PDF figé. Vide = on affiche le titre.
    nom = models.CharField(max_length=120, blank=True, default="")
    corps = models.JSONField(default=list, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    # ── Valeur légale : cycle de vie, numéro, date et lieu figés à l'émission, PDF figé et son empreinte ──
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default=STATUT_BROUILLON, db_index=True)
    type_document = models.CharField(max_length=30, default="courrier")       # un compteur de référence par type
    reference = models.CharField(max_length=120, blank=True, default="")      # attribuée à l'émission, jamais avant
    lieu = models.CharField(max_length=120, blank=True, default="")           # « Fait à … » (saisi, figé à l'émission)
    date_emission = models.DateField(null=True, blank=True)                   # « …, le … » (date du serveur à l'émission)
    emis_le = models.DateTimeField(null=True, blank=True)
    emis_par = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="documents_administratifs_emis")
    pdf_fige = models.FileField(upload_to="documents/administratifs/%Y/", null=True, blank=True)
    empreinte_sha256 = models.CharField(max_length=64, blank=True, default="")
    code_verification = models.CharField(max_length=32, null=True, blank=True, unique=True)   # pour le QR : la page publique
    version_de = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="corrections")
    annule_le = models.DateTimeField(null=True, blank=True)
    annule_par = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="documents_administratifs_annules")
    motif_annulation = models.CharField(max_length=300, blank=True, default="")

    # ── Signature : « P.O. » (par ordre) ou « P/ » (pour) imprimé devant la fonction du signataire ──
    delegation = models.CharField(max_length=2, blank=True, default="", choices=DELEGATION_CHOICES)
    # ── Place de la date (« Fait à …, le … ») dans le document : en bas (défaut), en haut à droite ou pas de date du tout ──
    date_position = models.CharField(max_length=6, default="bas", choices=POSITIONS_DATE)
    # ── Entête : modèle nommé choisi dans l'éditeur (brouillon seulement). Vide = modèle par défaut de l'entreprise. Le PDF
    # figé à l'émission garde l'entête imprimé à ce moment-là : supprimer un modèle ne change jamais un document émis. ──
    modele_entete = models.ForeignKey("ModeleEntete", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")
    # ── Numéro imprimé en dernière ligne de l'entête gauche (ReglageEntete de l'auteur), figé à l'émission avec le PDF ──
    numero_entete = models.CharField(max_length=120, blank=True, default="")
    # ── Circuit de validation : l'agent rédige et soumet, l'administrateur valide (= émet) ou renvoie ──
    soumis_le = models.DateTimeField(null=True, blank=True)
    valide_par = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="documents_administratifs_valides")
    valide_le = models.DateTimeField(null=True, blank=True)
    motif_refus = models.CharField(max_length=300, blank=True, default="")
    # ── Verrou d'édition : une seule personne modifie un brouillon à la fois (expire seul s'il n'est plus renouvelé) ──
    verrou_par = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verrou_jusqu_a = models.DateTimeField(null=True, blank=True)
    # ── Archivage : un document émis ne se supprime pas, il s'archive (masqué de la liste par défaut) ──
    archive = models.BooleanField(default=False, db_index=True)
    archive_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-modifie_le", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["entreprise", "reference"], condition=~models.Q(reference=""),
                                    name="doc_admin_reference_unique_entreprise"),
        ]

    def __str__(self):
        return self.reference or self.libelle or _gtf("engine/models/_part_003.py:957:50", v1=(self.pk))

    @property
    def libelle(self):
        return self.nom or self.titre

    @property
    def est_fige(self):
        """Émis ou annulé : pièce officielle, PDF figé. (Un document « à valider » n'est pas figé : il attend sa décision.)"""
        return self.statut in (self.STATUT_EMIS, self.STATUT_ANNULE)

    def save(self, *args, **kwargs):
        """Un document émis ou annulé n'est plus modifiable : seuls l'annulation et l'archivage (champs CHAMPS_POST_EMISSION,
        avec update_fields explicite) sont permis. Un document « à valider » ne change pas non plus tant qu'il reste dans
        cet état (il est émis ou renvoyé en brouillon). Garde-fou de dernier recours, valable pour toute vue ou commande."""
        if self.pk:
            ancien = type(self).objects.filter(pk=self.pk).values_list("statut", flat=True).first()
            champs = kwargs.get("update_fields")
            if ancien in (self.STATUT_EMIS, self.STATUT_ANNULE):
                if champs is None or not set(champs) <= self.CHAMPS_POST_EMISSION:
                    raise DocumentFigeError(_gt("rg5.msg.069"))
            elif ancien == self.STATUT_SOUMIS and self.statut == self.STATUT_SOUMIS:
                if champs is None or not set(champs) <= self.CHAMPS_RENOMMAGE:
                    raise DocumentFigeError(_gt("rg5.msg.070"))
        super().save(*args, **kwargs)

    def delete(self, *args, force=False, **kwargs):
        """Un document émis/annulé ne se supprime pas par défaut (garde-fou). `force=True` : suppression volontaire par
        l'administrateur (vue api_document_administratif, tracée dans le journal d'audit) — jamais implicite."""
        if self.est_fige and not force:
            raise DocumentFigeError(_gt("rg5.msg.068"))
        # Django n'efface jamais le fichier d'un FileField : sans ceci, le PDF figé resterait orphelin sur le disque.
        nom_pdf, stockage = (self.pdf_fige.name, self.pdf_fige.storage) if self.pdf_fige else (None, None)
        resultat = super().delete(*args, **kwargs)
        if nom_pdf:
            from django.db import transaction

            def _effacer_pdf():
                try:
                    stockage.delete(nom_pdf)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(_gt("rg5.msg.071"), nom_pdf)
            transaction.on_commit(_effacer_pdf)       # seulement si la suppression en base est validée
        return resultat


class DocumentFigeError(Exception):
    """Tentative de modifier ou de supprimer un document administratif émis."""


class ModeleDocument(models.Model):
    """Modèle de document : type, titre et corps (blocs {type, texte}) pré-remplis, avec des variables ({date}, {destinataire},
    {objet}, {lieu}, {auteur}) remplacées à la CRÉATION du brouillon (jamais à l'émission) ; entête optionnel repris par le
    brouillon. L'administrateur gère les modèles, l'agent les voit et les utilise (« Nouveau à partir d'un modèle »)."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="modeles_documents")
    nom = models.CharField(max_length=100)
    type_document = models.CharField(max_length=30, default="courrier")
    titre = models.CharField(max_length=200, blank=True, default="")
    corps = models.JSONField(default=list, blank=True)
    modele_entete = models.ForeignKey(ModeleEntete, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="modeles_documents")

    class Meta:
        ordering = ["nom", "id"]
        constraints = [models.UniqueConstraint(fields=["entreprise", "nom"], name="modele_document_nom_unique_entreprise")]

    def __str__(self):
        return self.nom


class CompteurReference(models.Model):
    """Dernier numéro attribué, par entreprise, par type de document et par année. Incrémenté sous verrou dans la
    transaction d'émission : un numéro n'est consommé que si le document est effectivement émis (pas de trou), et
    jamais deux fois (pas de doublon : verrou de ligne + contrainte d'unicité sur DocumentAdministratif.reference)."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="compteurs_reference")
    type_document = models.CharField(max_length=30)
    annee = models.PositiveSmallIntegerField()
    dernier = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entreprise", "type_document", "annee"],
                                               name="compteur_reference_unique")]


class ReglageEmission(models.Model):
    """Réglages d'émission imposés par l'administrateur : format du numéro de référence et lieu par défaut.
    Format : `N°{n:04d}/MFPP/DGID/{année}` — champs permis : {n}, {annee} / {année}, {type}."""
    FORMAT_DEFAUT = _gtfr("engine/models/_part_003.py:1044:21")
    entreprise = models.OneToOneField(Entreprise, on_delete=models.CASCADE, related_name="reglage_emission")
    format_reference = models.CharField(max_length=120, default=FORMAT_DEFAUT)
    lieu_emission = models.CharField(max_length=120, blank=True, default="")
    # Vrai : un agent ne peut plus émettre lui-même, il soumet et l'administrateur valide (circuit de validation).
    validation_obligatoire = models.BooleanField(default=False)


class SyntheseRedigee(models.Model):
    """Synthèse / analyse RÉDIGÉE à la main sur UNE table, sur le modèle des documents administratifs : un titre et un corps de blocs ordonnés
    (voir reports/_part_008.py : texte, commentaire, explication, à retenir, et blocs « analyse » qui reprennent une carte, un tableau ou un graphique
    DÉJÀ calculé par le moteur — aucune formule n'est réécrite). L'entête officiel, le cachet et les signatures ne sont pas stockés ici : ils viennent des
    réglages du contexte « courrier », comme pour les documents administratifs.

    Cycle de vie : BROUILLON (modifiable, PDF filigrané) -> FINALISÉE (PDF généré une seule fois, figé avec son empreinte SHA-256 : les chiffres ne
    bougent plus quand les données de la table changent). Une synthèse finalisée se rouvre (retour en brouillon) ou s'archive ; elle ne se modifie pas."""
    STATUT_BROUILLON, STATUT_FINALISEE = "brouillon", "finalisee"
    STATUT_CHOICES = [(STATUT_BROUILLON, "Brouillon"), (STATUT_FINALISEE, "Finalisée")]
    CHAMPS_POST_FINALISATION = frozenset({"archive", "archive_le", "modifie_le"})

    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="syntheses_redigees")
    table = models.ForeignKey("TableDynamique", on_delete=models.CASCADE, related_name="syntheses_redigees")
    auteur = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="syntheses_redigees")
    titre = models.CharField(max_length=200, blank=True, default="")
    corps = models.JSONField(default=list, blank=True)
    # Entête : modèle nommé choisi dans l'éditeur (brouillon seulement). Vide = modèle par défaut de l'entreprise.
    modele_entete = models.ForeignKey("ModeleEntete", on_delete=models.SET_NULL, null=True, blank=True, related_name="syntheses")
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default=STATUT_BROUILLON, db_index=True)
    finalisee_le = models.DateTimeField(null=True, blank=True)
    finalisee_par = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="syntheses_finalisees")
    pdf_fige = models.FileField(upload_to="documents/syntheses/%Y/", null=True, blank=True)
    empreinte_sha256 = models.CharField(max_length=64, blank=True, default="")
    archive = models.BooleanField(default=False, db_index=True)
    archive_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-modifie_le", "-id"]

    def __str__(self):
        return self.titre or _gtf("engine/models/_part_003.py:synthese_sans_titre", v1=(self.pk))

    @property
    def est_figee(self):
        return self.statut == self.STATUT_FINALISEE

    def save(self, *args, **kwargs):
        """Une synthèse finalisée n'est plus modifiable : seul l'archivage (champs CHAMPS_POST_FINALISATION, avec update_fields explicite) est permis.
        Garde-fou de dernier recours, valable pour toute vue ou commande ; la réouverture passe par engine/syntheses.rouvrir (UPDATE direct)."""
        if self.pk:
            ancien = type(self).objects.filter(pk=self.pk).values_list("statut", flat=True).first()
            champs = kwargs.get("update_fields")
            if ancien == self.STATUT_FINALISEE and (champs is None or not set(champs) <= self.CHAMPS_POST_FINALISATION):
                raise DocumentFigeError(_gt("engine/models/_part_003.py:synthese_figee"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        nom_pdf, stockage = (self.pdf_fige.name, self.pdf_fige.storage) if self.pdf_fige else (None, None)
        resultat = super().delete(*args, **kwargs)
        if nom_pdf:      # Django n'efface jamais le fichier d'un FileField : sans ceci, le PDF figé resterait orphelin
            from django.db import transaction

            def _effacer_pdf():
                try:
                    stockage.delete(nom_pdf)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception(_gt("rg5.msg.071"), nom_pdf)
            transaction.on_commit(_effacer_pdf)
        return resultat


import contextvars as _contextvars

# Utilisateur qui génère le document : posé à chaque requête par VuesAutoriseesMiddleware. Sans requête (tâche planifiée,
# commande), la valeur reste vide et les documents n'utilisent que les réglages de l'entreprise.
_utilisateur_document = _contextvars.ContextVar("utilisateur_document", default=None)


def definir_utilisateur_document(utilisateur_id):
    return _utilisateur_document.set(utilisateur_id)


def membre_agent_document(entreprise_id):
    """Le MembreEntreprise AGENT qui génère le document en cours, sinon None (administrateur, tâche automatique…)."""
    uid = _utilisateur_document.get()
    if not uid:
        return None
    return MembreEntreprise.objects.filter(utilisateur_id=uid, entreprise_id=entreprise_id, role="agent").first()


def personnalisation_document(entreprise_id, contexte):
    m = membre_agent_document(entreprise_id)
    if m is None:
        return None
    return PersonnalisationDocumentMembre.objects.filter(membre=m, contexte=contexte).first()


def signatures_document_pour(entreprise):
    """Signatures à imprimer : celles de l'agent qui génère le document s'il en a (même désactivées : c'est ainsi qu'il
    en retire une), sinon celles de l'entreprise. Retourne un queryset (à filtrer ensuite par actif=True)."""
    base = SignatureDocument.objects.filter(entreprise=entreprise)
    m = membre_agent_document(entreprise.id)
    if m is not None and base.filter(membre=m).exists():
        return base.filter(membre=m)
    return base.filter(membre__isnull=True)

# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
