from engine.i18n import td
from engine.i18n import tl
from engine.i18n import t
from ._header import *  # noqa
from engine.montant_champs import MontantField, TauxField  # noqa
from ._part_001 import *  # noqa

# Messagerie interne (discussions, groupes, pièces jointes) et publicités des agents : voir engine/messagerie.py.

TYPES_MEDIA = [("texte", tl("engine/models/_part_006.py:g:7:26")), ("image", "Image"), ("audio", "Son"), ("video", tl("engine/models/_part_006.py:g:7:84")), ("fichier", td("engine/models/_part_006.py:g:8:170"))]


class Conversation(models.Model):
    """Fil de discussion. `direct` : deux membres ; `groupe_entreprise` : tous les administrateurs et agents ;
    `groupe_clients` : un agent et l'ensemble de ses clients (son espace)."""
    TYPE_CHOICES = [("direct", td("engine/models/_part_006.py:g:14:32")), ("groupe_entreprise", td("engine/models/_part_006.py:g:14:76")),
                    ("groupe_clients", td("engine/models/_part_006.py:g:15:40")),
                    ("salon", td("engine/models/_part_006.py:g:16:31"))]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="conversations")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    agent = models.ForeignKey("MembreEntreprise", null=True, blank=True, on_delete=models.CASCADE,
                              related_name="conversations_clients")
    titre = models.CharField(max_length=120, blank=True, default="")   # salon : nom donné par son créateur
    createur = models.ForeignKey("MembreEntreprise", null=True, blank=True, on_delete=models.SET_NULL, related_name="salons_crees")
    cle = models.CharField(max_length=60, blank=True, default="")   # direct : "<id petit>-<id grand>", évite les doublons
    cree_le = models.DateTimeField(auto_now_add=True)
    dernier_message_le = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-dernier_message_le", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["entreprise", "cle"], condition=~models.Q(cle=""), name="conversation_cle_unique"),
            models.UniqueConstraint(fields=["entreprise"], condition=models.Q(type="groupe_entreprise"),
                                    name="conversation_groupe_entreprise_unique"),
            models.UniqueConstraint(fields=["agent"], condition=models.Q(type="groupe_clients"),
                                    name="conversation_groupe_clients_unique"),
        ]


class Participant(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="participants")
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="participations")
    dernier_lu_id = models.BigIntegerField(default=0)   # dernier message lu (les suivants comptent « non lus »)

    class Meta:
        unique_together = ("conversation", "membre")


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    expediteur = models.ForeignKey("MembreEntreprise", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    texte = models.TextField(blank=True, default="")
    fichier = models.FileField(upload_to="messagerie/%Y/%m/", null=True, blank=True)
    nom_fichier = models.CharField(max_length=200, blank=True, default="")
    type_media = models.CharField(max_length=10, choices=TYPES_MEDIA, default="texte")
    taille = models.BigIntegerField(default=0)
    supprime = models.BooleanField(default=False)
    auto = models.BooleanField(default=False)   # réponse du chatbot de l'expéditeur (voir engine/chatbot.py)
    # Structure partagée (zip « modèle » généré PAR LE SERVEUR à l'envoi, jamais un zip téléversé) : le destinataire de l'équipe peut
    # l'importer en un clic (voir messagerie.envoyer_structure / importer_structure et cycle.importer_modele).
    structure = models.BooleanField(default=False)
    # Fiche (ligne de table) que l'expéditeur a CHOISI de lier à son message : seulement une fiche qu'il a créée lui-même.
    enregistrement = models.ForeignKey("Enregistrement", null=True, blank=True, on_delete=models.SET_NULL, related_name="messages_lies")
    # Réponse à un message précédent de la MÊME conversation (citation, comme WhatsApp).
    reponse_a = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reponses")
    modifie_le = models.DateTimeField(null=True, blank=True)   # texte corrigé par son auteur (affiché « modifié »)
    transfere = models.BooleanField(default=False)             # copie d'un message transféré depuis une autre discussion
    cree_le = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["conversation", "id"], name="message_conv_id_idx")]


class ReactionMessage(models.Model):
    """Réaction emoji d'un membre à un message (une seule par membre et par message ; la même une seconde fois la retire)."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reactions")
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="+")
    emoji = models.CharField(max_length=8)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "membre"], name="reaction_message_unique")]


class FavoriMessage(models.Model):
    """Message mis en favori (étoile) par un membre : privé, jamais visible de l'expéditeur ni des autres."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="favoris")
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "membre"], name="favori_message_unique")]


class AvisMessage(models.Model):
    """Avis PRIVÉ d'un membre sur un message reçu : +1 (bon) ou -1 (mauvais : spam, message malveillant). Un seul par membre et par message.
    Jamais montré à l'expéditeur ni aux autres : c'est le juge du modèle fantôme du SIEM (engine/siem_fantome.py), qui en tire l'apprentissage."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="avis")
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="+")
    valeur = models.SmallIntegerField(choices=[(1, "bon"), (-1, "mauvais")])
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "membre"], name="avis_message_unique")]


class Sourdine(models.Model):
    """Sourdine d'un membre : soit une PERSONNE (`cible` : ses messages ne notifient plus et ne comptent plus comme non lus,
    et sont repliés dans le fil), soit une DISCUSSION entière (`conversation`). Exactement l'un des deux (vérifié dans messagerie.mettre_en_sourdine). L'administrateur (le
    chef) ne peut jamais être mis en sourdine : ses messages arrivent toujours (voir messagerie.mettre_en_sourdine)."""
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="sourdines")
    cible = models.ForeignKey("MembreEntreprise", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["membre", "cible"], condition=models.Q(cible__isnull=False), name="sourdine_cible_unique"),
            models.UniqueConstraint(fields=["membre", "conversation"], condition=models.Q(conversation__isnull=False), name="sourdine_conversation_unique"),
        ]


class Publicite(models.Model):
    """Publicité configurée par un agent (ou l'administrateur) ; vue uniquement par les clients de son espace."""
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="publicites")
    agent = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="publicites")
    titre = models.CharField(max_length=200)
    contexte = models.TextField(blank=True, default="")
    fichier = models.FileField(upload_to="publicites/%Y/%m/", null=True, blank=True)
    nom_fichier = models.CharField(max_length=200, blank=True, default="")
    type_media = models.CharField(max_length=10, choices=TYPES_MEDIA, default="texte")
    publiee = models.BooleanField(default=True)
    # Formulaire (table) lié par l'agent : le client voit un bouton « Remplir le formulaire » SEULEMENT si la table lui est toujours
    # mise à disposition (expose_client et pas réservée à d'autres clients). Table supprimée → la publicité reste, sans bouton.
    table = models.ForeignKey("TableDynamique", null=True, blank=True, on_delete=models.SET_NULL, related_name="publicites")
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]


class ReactionPublicite(models.Model):
    """Réaction d'un client à une publicité de son agent : j'aime (1), je n'aime pas (-1) ou commentaire (0). Un seul avis
    (j'aime / je n'aime pas) par client et par publicité ; les commentaires sont illimités. Voir messagerie.reagir."""
    VALEURS = [(1, tl("engine/models/_part_006.py:g:130:20")), (-1, tl("engine/models/_part_006.py:g:130:36")), (0, "Commentaire")]
    publicite = models.ForeignKey(Publicite, on_delete=models.CASCADE, related_name="reactions")
    membre = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="reactions_publicite")
    valeur = models.SmallIntegerField(choices=VALEURS)
    texte = models.TextField(blank=True, default="")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["publicite", "membre"], condition=~models.Q(valeur=0), name="reaction_avis_unique")]


class ChatbotAgent(models.Model):
    """Chatbot d'un agent (ou du chef). Deux portées indépendantes : `interne` (répond aux membres de l'équipe qui lui écrivent)
    et `externe` (répond à ses clients). Voir engine/chatbot.py."""
    PORTEES = [("interne", td("engine/models/_part_006.py:g:145:28")), ("externe", td("engine/models/_part_006.py:g:145:61"))]
    TONS = [("neutre", "Neutre"), ("chaleureux", "Chaleureux"), ("formel", "Formel"), ("concis", "Concis")]
    agent = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="chatbots")
    portee = models.CharField(max_length=10, choices=PORTEES)
    actif = models.BooleanField(default=False)
    nom = models.CharField(max_length=60, blank=True, default="Assistant")
    consignes = models.TextField(blank=True, default="")
    ton = models.CharField(max_length=12, choices=TONS, default="neutre")
    utilise_historique = models.BooleanField(default=True)   # s'inspire des messages passés de l'agent dans cette portée
    cree_le = models.DateTimeField(auto_now_add=True)
    maj_le = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["agent", "portee"], name="chatbot_agent_portee_unique")]


class ReprisePoste(models.Model):
    """Poste d'un agent retiré de l'entreprise. Retirer un agent ne supprime pas son travail : seul son accès (identifiants)
    disparaît ; ses clients, lignes, publicités, chatbots, tâches… sont repris par l'administrateur, le temps de créer un
    remplaçant à qui l'administrateur les cède ensuite en un clic. Voir engine/reprise_poste.py.
    `elements` : {"publicites": [ids], "chatbots": [ids], "conversations": [ids], "taches": [ids], "skills": [ids], "kpis": [ids]}.
    Les lignes (Enregistrement) sont repérées par Enregistrement.reprise (potentiellement très nombreuses)."""
    STATUTS = [("en_attente", tl("engine/models/_part_006.py:g:167:31")), ("cede", tl("engine/models/_part_006.py:g:167:71"))]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="reprises_poste")
    admin = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    nom_agent = models.CharField(max_length=200, blank=True, default="")
    identifiant_ancien = models.CharField(max_length=150, blank=True, default="")
    telephone_ancien = models.CharField(max_length=30, blank=True, default="")
    elements = models.JSONField(default=dict, blank=True)
    nb_lignes = models.PositiveIntegerField(default=0)
    solde_verse = MontantField(default=0)
    statut = models.CharField(max_length=12, choices=STATUTS, default="en_attente")
    cede_a = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)
    cede_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-cree_le", "-id"]


class TacheAgenda(models.Model):
    """Tâche datée de l'agenda : un agent (ou l'administrateur) la planifie pour LUI-MÊME ou pour l'un de SES clients ; un client peut aussi
    planifier la sienne. Le destinataire est rappelé à l'échéance (Notification « agenda_rappel ») puis, s'il se connecte une fois l'échéance
    dépassée sans avoir répondu, averti qu'il ne l'a pas faite (« agenda_retard »). C'est TOUJOURS lui qui valide : fait ou pas fait.
    `rappel_le` / `retard_le` : horodatage posé AVANT l'envoi de chaque rappel, ce qui le rend unique même avec plusieurs workers. Voir engine/agenda.py."""
    STATUTS = [("a_faire", tl("engine/models/_part_006.py:191:31")), ("fait", tl("engine/models/_part_006.py:191:62")), ("non_fait", tl("engine/models/_part_006.py:191:93"))]
    entreprise = models.ForeignKey(Entreprise, on_delete=models.CASCADE, related_name="agenda")
    createur = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="agenda_creee")
    destinataire = models.ForeignKey("MembreEntreprise", on_delete=models.CASCADE, related_name="agenda_recue")
    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    echeance = models.DateTimeField(db_index=True)
    statut = models.CharField(max_length=10, choices=STATUTS, default="a_faire")
    rappel_le = models.DateTimeField(null=True, blank=True)
    retard_le = models.DateTimeField(null=True, blank=True)
    valide_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["echeance", "id"]
        indexes = [models.Index(fields=["destinataire", "statut", "echeance"], name="agenda_dest_stat_ech"),
                   models.Index(fields=["statut", "echeance"], name="agenda_stat_ech")]

    def __str__(self):
        return f"{self.titre} ({self.echeance:%Y-%m-%d %H:%M})"


__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
