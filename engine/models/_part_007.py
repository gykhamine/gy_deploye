"""Comptabilité automatique du wallet : agents habilités à enregistrer des espèces, politique de transfert interne, journal comptable.

Toutes les données du wallet restent en interne : aucun prestataire externe n'est nécessaire pour les entrées, les sorties et les transferts."""
from django.db import models

from ._header import *  # noqa
from engine.montant_champs import MontantField, TauxField  # noqa
from ._part_001 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import td as _gtd  # noqa: E402


class HabilitationFlux(models.Model):
    """Permission donnée par l'administrateur à un agent d'enregistrer des dépôts et retraits en espèces dans le wallet d'un client ou
    d'un autre agent (comptabilité : wallet_especes.py). Sans ligne active, un agent ne peut rien enregistrer."""
    membre = models.OneToOneField("MembreEntreprise", on_delete=models.CASCADE, related_name="habilitation_flux")
    accorde_par = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    actif = models.BooleanField(default=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Habilitation {self.membre} ({'active' if self.actif else _gt("engine/models/_part_007.py:23:75")})"


class PolitiqueTransfert(models.Model):
    """Types de transferts internes autorisés par l'entreprise. Aucune limite de montant. Appliqué sous verrou par wallet_especes.transferer_interne."""
    entreprise = models.OneToOneField("Entreprise", on_delete=models.CASCADE, related_name="politique_transfert")
    client_vers_client = models.BooleanField(default=True)
    client_vers_agent = models.BooleanField(default=True)
    agent_vers_client = models.BooleanField(default=True)
    agent_vers_agent = models.BooleanField(default=True)
    maj_le = models.DateTimeField(auto_now=True)

    def __str__(self):
        return _gtf("engine/models/_part_007.py:36:16", v1=(self.entreprise))


class EcritureWallet(models.Model):
    """Journal comptable du wallet. Une ligne par mouvement, écrite DANS la transaction qui bouge l'argent (jamais après) : il n'existe
    donc pas de mouvement sans ligne. Une ligne ne se modifie ni ne se supprime. `sens` : « entree » (argent qui entre dans l'entreprise),
    « sortie » (argent qui en sort) ou « interne » (de wallet à wallet). `touche_wallet` faux : mouvement d'espèces sans effet sur un solde
    (remise ou avance d'espèces entre un agent et l'entreprise). `cle` rend l'enregistrement idempotent (double clic, rejeu)."""
    TYPES = [("ouverture", _gtfr("engine/models/_part_007.py:44:28")), ("depot_especes", _gtd("rg6.msg.053")), ("retrait_especes", _gtd("rg6.msg.054")),
             ("depot_prestataire", _gtd("rg6.msg.055")), ("retrait_prestataire", _gtfr("engine/models/_part_007.py:45:82")),
             ("transfert", "Transfert interne"), ("paiement_caisse", _gtd("rg6.msg.056")), ("salaire", "Salaire"),
             ("service", _gtd("rg6.msg.057")), ("commission", _gtfr("engine/models/_part_007.py:47:63")), ("charge", _gtd("rg6.msg.058")),
             ("remise_especes", _gtd("rg6.msg.059")), ("avance_especes", _gtd("rg6.msg.060")), ("entree_caisse", _gtd("rg6.msg.061"))]
    SENS = [("entree", _gtfr("engine/models/_part_007.py:49:24")), ("sortie", "Sortie"), ("interne", "Interne")]
    entreprise = models.ForeignKey("Entreprise", on_delete=models.CASCADE, related_name="ecritures_wallet")
    type = models.CharField(max_length=24, choices=TYPES)
    sens = models.CharField(max_length=8, choices=SENS)
    montant = MontantField()
    frais = MontantField(default=0)
    touche_wallet = models.BooleanField(default=True)
    membre_source = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    membre_dest = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    client = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    agent_responsable = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    operateur = models.ForeignKey("MembreEntreprise", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    categorie = models.CharField(max_length=24, blank=True, default="")
    motif = models.CharField(max_length=200, blank=True, default="")
    cle = models.CharField(max_length=64, null=True, blank=True)
    bloc_index = models.IntegerField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-id"]
        unique_together = (("entreprise", "cle"),)
        indexes = [models.Index(fields=["entreprise", "type", "cree_le"], name="idx_ecriture_ent_type_date")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RuntimeError(_gt("rg5.msg.073"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError(_gt("rg5.msg.072"))

    def __str__(self):
        return f"{self.type} {self.montant} ({self.sens})"


class TraceOntologie(models.Model):
    """Trace d'enquête de la page d'ontologie (les nœuds cliqués successivement + les liens entre eux), mémorisée pour ne pas être perdue
    quand on quitte la page, et historisée pour y revenir plus tard. Personnelle (un utilisateur ne voit que les siennes).

    courante=True : la trace en cours (UNE par utilisateur et par page, réécrite au fil de l'enquête).
    courante=False : une entrée de l'historique (trace archivée ou enregistrée sous un nom).
    page_ref : id de la PageOntologie (None = graphe complet sans page) ; simple entier, pour couvrir aussi une page partagée par une autre entreprise."""
    entreprise = models.ForeignKey("Entreprise", on_delete=models.CASCADE, related_name="traces_ontologie")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="traces_ontologie")
    page_ref = models.PositiveIntegerField(null=True, blank=True)
    nom = models.CharField(max_length=150, blank=True, default="")
    noeuds = models.JSONField(default=list)      # identifiants des nœuds, dans l'ordre de l'enquête
    liens = models.JSONField(default=list)       # [{"de": id, "vers": id, "a": [source, cible, relation] | None}]
    focus = models.CharField(max_length=200, blank=True, default="")
    courante = models.BooleanField(default=False)
    epinglee = models.BooleanField(default=False)
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-maj_le", "-id"]
        indexes = [models.Index(fields=["entreprise", "utilisateur", "page_ref", "courante"], name="idx_trace_onto")]

    def __str__(self):
        return _gtf("engine/models/_part_007.py:108:16", v1=(self.nom or 'trace'), v2=(len(self.noeuds)))


class ProprieteTable(models.Model):
    """Propriété partageable d'une table (agents) : l'administrateur la retire à son créateur et la confie à un ou plusieurs agents.

    Aucun propriétaire = règle d'origine (chaque agent voit les lignes qu'il a créées). Dès qu'il y en a, SEULS les propriétaires et
    l'administrateur accèdent à la table, et les propriétaires en voient toutes les lignes (voir engine/propriete_tables.py)."""
    table = models.ForeignKey("TableDynamique", on_delete=models.CASCADE, related_name="proprietaires")
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tables_possedees")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("table", "utilisateur")


class ReglagePlateforme(models.Model):
    """Réglages de la plateforme, contrôlés par le super-administrateur Django (une seule ligne, pk=1) : contact WhatsApp
    affiché à une entreprise bloquée, durée de l'essai et durée d'un déblocage. Base système, jamais par entreprise."""
    whatsapp_numero = models.CharField(max_length=30, blank=True, default="")
    email_contact = models.CharField(max_length=254, blank=True, default="")   # contact par e-mail (pop-up « fonctionnalité bloquée »)
    whatsapp_message = models.TextField(blank=True, default="")   # vide = message par défaut traduit ; {entreprise} et {fin} sont remplacés
    essai_jours = models.PositiveIntegerField(default=7)
    prolongation_jours = models.PositiveIntegerField(default=30)

    @classmethod
    def charger(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
