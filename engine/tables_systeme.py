"""TABLES SYSTÈME — les tables définies dans le code (membres, wallet, paiement, messagerie) accessibles comme des tables dynamiques.

Objectif : toutes les pages d'analyse (dashboards BI, KPI, statistiques, séries temporelles, ontologie, IA, cartes…) travaillent sur une
`TableDynamique`. Plutôt que de réécrire une page spécialisée par table du code, chaque table du code est exposée comme une
`TableDynamique` de lecture seule (`source_systeme` renseigné) : même sélecteur de table, mêmes analyses, dès l'installation.

Fonctionnement :
  * `REGISTRE` décrit chaque source (modèle Django, filtre d'entreprise, colonnes). Ajouter une table = ajouter une entrée.
  * `assurer(entreprise)` crée (ou met à jour) les TableDynamique + ChampDynamique de l'entreprise. Idempotent. `garantir(request, entreprise)`
    l'appelle au premier accès (une fois par processus et par version du registre).
  * Les LIGNES ne sont jamais copiées : `dataframe(table)` les lit dans le modèle Django à chaque analyse (jamais périmées), typées comme
    les colonnes d'une table dynamique. `parquet_store._lire_eav` y renvoie, donc TOUS les modules d'analyse les lisent sans changement.
  * Lecture seule : aucune ligne ni colonne ne s'écrit (voir `refus_ecriture`). Pas de fichier Parquet, pas d'EAV.
  * Accès : administrateur seulement par défaut (un agent n'y accède que si l'administrateur l'a listée dans ses tables autorisées ; un
    client jamais — voir views._table_autorisee_agent).

CONFIDENTIALITÉ : ne sont exposés ni secrets (config des fournisseurs de paiement), ni numéros de téléphone de retrait, ni réponses brutes des
opérateurs, ni le TEXTE des messages (seulement leur nature, taille, date, expéditeur), ni les éléments privés (favoris, avis, sourdines).
"""
from __future__ import annotations

import logging
import threading
from decimal import Decimal
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tfr as _gtfr  # noqa: E402

logger = logging.getLogger(__name__)

VERSION = 2   # à incrémenter quand REGISTRE change : les colonnes des tables existantes sont alors resynchronisées une fois par processus
PREFIXE_SLUG = "systeme_"
PREFIXE_NOM = _gtfr("engine/tables_systeme.py:31:15")


# ── Description d'une colonne ───────────────────────────────────────────────────────────────────────────────────
def _lire(objet, chemin):
    """Valeur de `objet.a.b.c` ; None dès qu'un maillon manque (clé étrangère vide)."""
    for part in chemin.split("."):
        if objet is None:
            return None
        objet = getattr(objet, part, None)
    return objet


def _c(slug, nom, type_champ, chemin=None, longueur=False):
    """`longueur` : la colonne donne la LONGUEUR du texte de `chemin`, jamais le texte lui-même (messages, consignes, scripts…)."""
    return {"slug": slug, "nom": nom, "type": type_champ, "chemin": chemin or slug, "longueur": longueur}


def _membre(slug, nom, chemin=None):
    """Une clé étrangère vers MembreEntreprise → deux colonnes : le nom d'utilisateur (texte) et l'identifiant (nombre, pour croiser)."""
    chemin = chemin or slug
    return [_c(slug, nom, "texte", f"{chemin}.utilisateur.username"), _c(f"{slug}_id", f"{nom} (id)", "nombre", f"{chemin}_id")]


def _id():
    return _c("id", _gtfr("engine/tables_systeme.py:56:21"), "nombre", "pk")


# ── Registre ───────────────────────────────────────────────────────────────────────────────────────────────────
# cle : identifiant stable de la source (ne jamais le changer : il relie la table aux dashboards/KPI construits dessus)
# filtre : entreprise → QuerySet ; liees : select_related (une seule requête par source)
def _registre():
    from . import models as m
    return [
        # ── Clients, agents, administrateurs ──
        {"cle": "membres", "nom": _gtfr("engine/tables_systeme.py:66:35"), "modele": m.MembreEntreprise,
         "filtre": lambda e: m.MembreEntreprise.objects.filter(entreprise=e), "liees": ["utilisateur"],
         "colonnes": [_id(), _c("utilisateur", _gtfr("engine/tables_systeme.py:68:48"), "texte", "utilisateur.username"), _c("role", _gtfr("engine/tables_systeme.py:68:108"), "choix_unique"),
                      _c("numero_client", _gtfr("engine/tables_systeme.py:69:43"), "nombre"), _c("indicatif_pays", _gtfr("engine/tables_systeme.py:69:93"), "texte"),
                      _c("actif", _gtfr("engine/tables_systeme.py:70:35"), "booleen"), _c("table_cliente_id", _gtfr("engine/tables_systeme.py:70:79"), "nombre"),
                      _c("enregistrement_client_id", _gtfr("engine/tables_systeme.py:71:54"), "nombre")]},
        # ── Wallet ──
        {"cle": "wallets", "nom": _gtfr("engine/tables_systeme.py:73:35"), "modele": m.Wallet,
         "filtre": lambda e: m.Wallet.objects.filter(membre__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), *_membre("membre", _gtfr("engine/tables_systeme.py:75:49")), _c("solde", _gtfr("engine/tables_systeme.py:75:72"), "monnaie"), _c("maj_le", _gtfr("engine/tables_systeme.py:75:106"), "datetime")]},
        {"cle": "ecritures_wallet", "nom": _gtfr("engine/tables_systeme.py:76:44"), "modele": m.EcritureWallet,
         "filtre": lambda e: m.EcritureWallet.objects.filter(entreprise=e),
         "liees": ["membre_source__utilisateur", "membre_dest__utilisateur", "client__utilisateur", "agent_responsable__utilisateur", "operateur__utilisateur"],
         "colonnes": [_id(), _c("type", _gtfr("engine/tables_systeme.py:79:41"), "choix_unique"), _c("sens", _gtfr("engine/tables_systeme.py:79:77"), "choix_unique"), _c("montant", _gtfr("engine/tables_systeme.py:79:116"), "monnaie"),
                      _c("frais", _gtfr("engine/tables_systeme.py:80:35"), "monnaie"), _c("touche_wallet", _gtfr("engine/tables_systeme.py:80:76"), "booleen"),
                      *_membre("membre_source", _gtfr("engine/tables_systeme.py:81:49")), *_membre("membre_dest", _gtfr("engine/tables_systeme.py:81:84")), *_membre("client", _gtfr("engine/tables_systeme.py:81:120")),
                      *_membre("agent_responsable", _gtfr("engine/tables_systeme.py:82:53")), *_membre("operateur", _gtfr("engine/tables_systeme.py:82:97")),
                      _c("categorie", _gtfr("engine/tables_systeme.py:83:39"), "texte"), _c("motif", _gtfr("engine/tables_systeme.py:83:75"), "texte"), _c("bloc_index", _gtfr("engine/tables_systeme.py:83:111"), "nombre"),
                      _c("cree_le", _gtfr("engine/tables_systeme.py:84:37"), "datetime")]},
        {"cle": "charges_wallet", "nom": _gtfr("engine/tables_systeme.py:85:42"), "modele": m.ChargeWallet,
         "filtre": lambda e: m.ChargeWallet.objects.filter(entreprise=e), "liees": ["cree_par__utilisateur"],
         "colonnes": [_id(), _c("categorie", _gtfr("engine/tables_systeme.py:87:46"), "choix_unique"), _c("libelle", _gtfr("engine/tables_systeme.py:87:91"), "texte"), _c("beneficiaire", _gtfr("engine/tables_systeme.py:87:132"), "texte"),
                      _c("montant", _gtfr("engine/tables_systeme.py:88:37"), "monnaie"), _c("date_charge", _gtfr("engine/tables_systeme.py:88:78"), "date"), *_membre("cree_par", _gtfr("engine/tables_systeme.py:88:129")),
                      _c("bloc_index", _gtfr("engine/tables_systeme.py:89:40"), "nombre"), _c("cree_le", _gtfr("engine/tables_systeme.py:89:84"), "datetime")]},
        {"cle": "taux_change", "nom": _gtfr("engine/tables_systeme.py:90:39"), "modele": m.TauxChange,
         "filtre": lambda e: m.TauxChange.objects.filter(entreprise=e), "liees": [],
         "colonnes": [_id(), _c("devise", _gtfr("engine/tables_systeme.py:92:43"), "texte"), _c("taux_vers_fcfa", _gtfr("engine/tables_systeme.py:92:84"), "decimal"), _c("maj_le", _gtfr("engine/tables_systeme.py:92:127"), "datetime")]},
        {"cle": "frais_wallet", "nom": _gtfr("engine/tables_systeme.py:93:40"), "modele": m.FraisWallet,
         "filtre": lambda e: m.FraisWallet.objects.filter(entreprise=e), "liees": [],
         "colonnes": [_id(), _c("taux_depot", _gtfr("engine/tables_systeme.py:95:47"), "pourcentage"), _c("taux_retrait", _gtfr("engine/tables_systeme.py:95:102"), "pourcentage"),
                      _c("taux_transfert", _gtfr("engine/tables_systeme.py:96:44"), "pourcentage"), _c("taux_interet", _gtfr("engine/tables_systeme.py:96:101"), "pourcentage"),
                      _c("taux_tva", _gtfr("engine/tables_systeme.py:97:38"), "pourcentage"), _c("taux_tva_actif", _gtfr("engine/tables_systeme.py:97:86"), "booleen"),
                      _c("fixe_depot", _gtfr("engine/tables_systeme.py:98:40"), "monnaie"), _c("fixe_retrait", _gtfr("engine/tables_systeme.py:98:87"), "monnaie"),
                      _c("fixe_transfert", _gtfr("engine/tables_systeme.py:99:44"), "monnaie"), _c("maj_le", _gtfr("engine/tables_systeme.py:99:87"), "datetime")]},
        {"cle": "remunerations", "nom": _gtfr("engine/tables_systeme.py:100:41"), "modele": m.RemunerationAgent,
         "filtre": lambda e: m.RemunerationAgent.objects.filter(membre__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), *_membre("membre", _gtfr("engine/tables_systeme.py:102:49")), _c("mode", _gtfr("engine/tables_systeme.py:102:70"), "choix_unique"), _c("salaire", _gtfr("engine/tables_systeme.py:102:109"), "monnaie"),
                      _c("jour_paie", _gtfr("engine/tables_systeme.py:103:39"), "nombre"), _c("pourcentage", _gtfr("engine/tables_systeme.py:103:84"), "pourcentage"),
                      _c("actif_depuis", _gtfr("engine/tables_systeme.py:104:42"), "date"), _c("derniere_periode", _gtfr("engine/tables_systeme.py:104:90"), "texte")]},
        {"cle": "habilitations_flux", "nom": _gtfr("engine/tables_systeme.py:105:46"), "modele": m.HabilitationFlux,
         "filtre": lambda e: m.HabilitationFlux.objects.filter(membre__entreprise=e), "liees": ["membre__utilisateur", "accorde_par__utilisateur"],
         "colonnes": [_id(), *_membre("membre", _gtfr("engine/tables_systeme.py:107:49")), *_membre("accorde_par", _gtfr("engine/tables_systeme.py:107:83")), _c("actif", _gtfr("engine/tables_systeme.py:107:112"), "booleen"),
                      _c("cree_le", _gtfr("engine/tables_systeme.py:108:37"), "datetime")]},
        {"cle": "politique_transfert", "nom": _gtfr("engine/tables_systeme.py:109:47"), "modele": m.PolitiqueTransfert,
         "filtre": lambda e: m.PolitiqueTransfert.objects.filter(entreprise=e), "liees": [],
         "colonnes": [_id(), _c("client_vers_client", _gtfr("engine/tables_systeme.py:111:55"), "booleen"), _c("client_vers_agent", _gtfr("engine/tables_systeme.py:111:112"), "booleen"),
                      _c("agent_vers_client", _gtfr("engine/tables_systeme.py:112:47"), "booleen"), _c("agent_vers_agent", _gtfr("engine/tables_systeme.py:112:102"), "booleen"),
                      _c("maj_le", _gtfr("engine/tables_systeme.py:113:36"), "datetime")]},
        # ── Paiement ──
        {"cle": "depots_paiement", "nom": _gtfr("engine/tables_systeme.py:115:43"), "modele": m.DepotPaiement,
         "filtre": lambda e: m.DepotPaiement.objects.filter(membre__entreprise=e), "liees": ["membre__utilisateur", "fournisseur"],
         "colonnes": [_id(), *_membre("membre", _gtfr("engine/tables_systeme.py:117:49")), _c("fournisseur", _gtfr("engine/tables_systeme.py:117:78"), "texte", "fournisseur.code"),
                      _c("reference", _gtfr("engine/tables_systeme.py:118:39"), "texte"), _c("montant", _gtfr("engine/tables_systeme.py:118:78"), "monnaie"), _c("statut", _gtfr("engine/tables_systeme.py:118:114"), "choix_unique"),
                      _c("cree_le", _gtfr("engine/tables_systeme.py:119:37"), "datetime"), _c("maj_le", _gtfr("engine/tables_systeme.py:119:76"), "datetime")]},
        {"cle": "retraits_wallet", "nom": _gtfr("engine/tables_systeme.py:120:43"), "modele": m.RetraitWallet,
         "filtre": lambda e: m.RetraitWallet.objects.filter(membre__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), *_membre("membre", _gtfr("engine/tables_systeme.py:122:49")), _c("operateur", _gtfr("engine/tables_systeme.py:122:76"), "texte"), _c("montant", _gtfr("engine/tables_systeme.py:122:114"), "monnaie"),
                      _c("frais", _gtfr("engine/tables_systeme.py:123:35"), "monnaie"), _c("statut", _gtfr("engine/tables_systeme.py:123:69"), "choix_unique"), _c("motif_echec", _gtfr("engine/tables_systeme.py:123:114"), "texte"),
                      _c("bloc_index", _gtfr("engine/tables_systeme.py:124:40"), "nombre"), _c("cree_le", _gtfr("engine/tables_systeme.py:124:84"), "datetime")]},
        {"cle": "moyens_retrait", "nom": _gtfr("engine/tables_systeme.py:125:42"), "modele": m.MoyenRetrait,
         "filtre": lambda e: m.MoyenRetrait.objects.filter(membre__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), *_membre("membre", _gtfr("engine/tables_systeme.py:127:49")), _c("operateur", _gtfr("engine/tables_systeme.py:127:76"), "texte"), _c("par_defaut", _gtfr("engine/tables_systeme.py:127:117"), "booleen"),
                      _c("actif", _gtfr("engine/tables_systeme.py:128:35"), "booleen"), _c("cree_le", _gtfr("engine/tables_systeme.py:128:70"), "datetime")]},
        {"cle": "evenements_prestataire", "nom": _gtfr("engine/tables_systeme.py:129:50"), "modele": m.EvenementPrestataire,
         "filtre": lambda e: m.EvenementPrestataire.objects.filter(entreprise=e), "liees": [],
         "colonnes": [_id(), _c("fournisseur_code", _gtfr("engine/tables_systeme.py:131:53"), "texte"), _c("type", _gtfr("engine/tables_systeme.py:131:89"), "choix_unique"), _c("reference", _gtfr("engine/tables_systeme.py:131:130"), "texte"),
                      _c("montant", _gtfr("engine/tables_systeme.py:132:37"), "monnaie"), _c("detail", _gtfr("engine/tables_systeme.py:132:73"), "texte"), _c("bloc_index", _gtfr("engine/tables_systeme.py:132:111"), "nombre"),
                      _c("cree_le", _gtfr("engine/tables_systeme.py:133:37"), "datetime")]},
        {"cle": "abonnements", "nom": _gtfr("engine/tables_systeme.py:134:39"), "modele": m.AbonnementMensuel,
         "filtre": lambda e: m.AbonnementMensuel.objects.filter(membre__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), *_membre("membre", _gtfr("engine/tables_systeme.py:136:49")), _c("categorie", _gtfr("engine/tables_systeme.py:136:76"), "choix_unique"), _c("expire_le", _gtfr("engine/tables_systeme.py:136:120"), "datetime"),
                      _c("maj_le", _gtfr("engine/tables_systeme.py:137:36"), "datetime")]},
        {"cle": "fournisseurs_paiement", "nom": _gtfr("engine/tables_systeme.py:138:49"), "modele": m.FournisseurPaiement,
         "filtre": lambda e: m.FournisseurPaiement.objects.all(), "liees": [],
         "colonnes": [_id(), _c("code", _gtfr("engine/tables_systeme.py:140:41"), "texte"), _c("libelle", _gtfr("engine/tables_systeme.py:140:73"), "texte"), _c("type", _gtfr("engine/tables_systeme.py:140:106"), "choix_unique"),
                      _c("actif", _gtfr("engine/tables_systeme.py:141:35"), "booleen"), _c("maj_le", _gtfr("engine/tables_systeme.py:141:69"), "datetime")]},
        # ── Messagerie ──
        {"cle": "conversations", "nom": _gtfr("engine/tables_systeme.py:143:41"), "modele": m.Conversation,
         "filtre": lambda e: m.Conversation.objects.filter(entreprise=e), "liees": ["agent__utilisateur", "createur__utilisateur"],
         "colonnes": [_id(), _c("type", _gtfr("engine/tables_systeme.py:145:41"), "choix_unique"), *_membre("agent", _gtfr("engine/tables_systeme.py:145:84")), *_membre("createur", _gtfr("engine/tables_systeme.py:145:115")),
                      _c("titre", _gtfr("engine/tables_systeme.py:146:35"), "texte"), _c("cree_le", _gtfr("engine/tables_systeme.py:146:68"), "datetime"), _c("dernier_message_le", _gtfr("engine/tables_systeme.py:146:120"), "datetime")]},
        {"cle": "participants", "nom": _gtfr("engine/tables_systeme.py:147:40"), "modele": m.Participant,
         "filtre": lambda e: m.Participant.objects.filter(conversation__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), _c("conversation_id", _gtfr("engine/tables_systeme.py:149:52"), "nombre"), *_membre("membre", _gtfr("engine/tables_systeme.py:149:101")),
                      _c("dernier_lu_id", _gtfr("engine/tables_systeme.py:150:43"), "nombre")]},
        {"cle": "messages", "nom": _gtfr("engine/tables_systeme.py:151:36"), "modele": m.Message,
         "filtre": lambda e: m.Message.objects.filter(conversation__entreprise=e), "liees": ["expediteur__utilisateur"],
         "colonnes": [_id(), _c("conversation_id", _gtfr("engine/tables_systeme.py:153:52"), "nombre"), *_membre("expediteur", _gtfr("engine/tables_systeme.py:153:105")),
                      _c("type_media", _gtfr("engine/tables_systeme.py:154:40"), "choix_unique"), _c("longueur_texte", _gtfr("engine/tables_systeme.py:154:96"), "nombre", "texte", longueur=True),
                      _c("taille", _gtfr("engine/tables_systeme.py:155:36"), "nombre"), _c("supprime", _gtfr("engine/tables_systeme.py:155:83"), "booleen"), _c("auto", _gtfr("engine/tables_systeme.py:155:119"), "booleen"),
                      _c("structure", _gtfr("engine/tables_systeme.py:156:39"), "booleen"), _c("transfere", _gtfr("engine/tables_systeme.py:156:90"), "booleen"),
                      _c("cree_le", _gtfr("engine/tables_systeme.py:157:37"), "datetime")]},
        {"cle": "reactions_messages", "nom": _gtfr("engine/tables_systeme.py:158:46"), "modele": m.ReactionMessage,
         "filtre": lambda e: m.ReactionMessage.objects.filter(message__conversation__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), _c("message_id", _gtfr("engine/tables_systeme.py:160:47"), "nombre"), *_membre("membre", _gtfr("engine/tables_systeme.py:160:93")), _c("emoji", _gtfr("engine/tables_systeme.py:160:116"), "texte"),
                      _c("cree_le", _gtfr("engine/tables_systeme.py:161:37"), "datetime")]},
        {"cle": "publicites", "nom": _gtfr("engine/tables_systeme.py:162:38"), "modele": m.Publicite,
         "filtre": lambda e: m.Publicite.objects.filter(entreprise=e), "liees": ["agent__utilisateur"],
         "colonnes": [_id(), *_membre("agent", _gtfr("engine/tables_systeme.py:164:48")), _c("titre", _gtfr("engine/tables_systeme.py:164:70"), "texte"), _c("type_media", _gtfr("engine/tables_systeme.py:164:106"), "choix_unique"),
                      _c("publiee", _gtfr("engine/tables_systeme.py:165:37"), "booleen"), _c("table_id", _gtfr("engine/tables_systeme.py:165:76"), "nombre"), _c("cree_le", _gtfr("engine/tables_systeme.py:165:121"), "datetime")]},
        {"cle": "reactions_publicites", "nom": _gtfr("engine/tables_systeme.py:166:48"), "modele": m.ReactionPublicite,
         "filtre": lambda e: m.ReactionPublicite.objects.filter(publicite__entreprise=e), "liees": ["membre__utilisateur"],
         "colonnes": [_id(), _c("publicite_id", _gtfr("engine/tables_systeme.py:168:49"), "nombre"), *_membre("membre", _gtfr("engine/tables_systeme.py:168:98")),
                      _c("valeur", _gtfr("engine/tables_systeme.py:169:36"), "nombre"), _c("cree_le", _gtfr("engine/tables_systeme.py:169:94"), "datetime")]},
    ]


# ── Tous les autres modèles du code, générés depuis leurs champs ─────────────────────────────────────────────────
import re

# Jamais exposés : le cœur des tables dynamiques (déjà analysées comme tables ordinaires) et les clés d'accès.
MODELES_EXCLUS = {_gtfr("engine/tables_systeme.py:177:19"), _gtfr("engine/tables_systeme.py:177:37"), _gtfr("engine/tables_systeme.py:177:55"), _gtfr("engine/tables_systeme.py:177:73"), _gtfr("engine/tables_systeme.py:177:88")}
# Champs jamais exposés (secrets, identifiants personnels, empreintes) : repérés par leur nom.
_SECRET = re.compile(r"(pass|secret|token|jeton|cle|key|hash|empreinte|signature|iban|carte|card|numero|telephone|phone|email|courriel|"
                     r"adresse|identifiant|credential|auth|code_acces|(?:^|_)(?:pin|otp)(?:$|_))", re.I)


def _chemin_entreprise(modele, profondeur=4, vus=()):
    """Chemin ORM jusqu'à l'entreprise (« membre__entreprise »…), "" pour Entreprise elle-même, None si le modèle n'est pas rattaché à une entreprise."""
    from .models import Entreprise
    if modele is Entreprise:
        return ""
    if profondeur == 0:
        return None
    for f in modele._meta.concrete_fields:
        cible = getattr(f, "related_model", None)
        if not f.many_to_one and not f.one_to_one or cible is None or cible in vus:
            continue
        if cible is Entreprise:
            return f.name
    for f in modele._meta.concrete_fields:
        cible = getattr(f, "related_model", None)
        if (f.many_to_one or f.one_to_one) and cible is not None and cible not in vus and cible is not modele:
            sous = _chemin_entreprise(cible, profondeur - 1, vus + (modele,))
            if sous is not None:
                return f.name if sous == "" else f"{f.name}__{sous}"
    return None


def _type_de(f):
    from django.db import models as dm
    from .montant_champs import MontantField, TauxField
    if isinstance(f, TauxField):
        return "decimal"
    if isinstance(f, MontantField):
        return "monnaie"
    if isinstance(f, (dm.DecimalField, dm.FloatField)):
        return "decimal"
    if isinstance(f, dm.BooleanField):
        return "booleen"
    if isinstance(f, dm.DateTimeField):
        return "datetime"
    if isinstance(f, dm.DateField):
        return "date"
    if isinstance(f, dm.IntegerField):
        return "nombre"
    return "choix_unique" if getattr(f, "choices", None) else "texte"


def _etiquette(f):
    try:
        e = str(f.verbose_name).strip()
    except Exception:
        e = ""
    return (e if e and len(e) <= 100 and "engine/" not in e else f.name.replace("_", " ").capitalize())[:100]


def _source_generique(modele):
    from django.db import models as dm
    from .models import MembreEntreprise
    chemin = _chemin_entreprise(modele)
    colonnes, liees = [_id()], []
    for f in modele._meta.concrete_fields:
        if f.primary_key or isinstance(f, (dm.FileField, dm.BinaryField, dm.JSONField)) or _SECRET.search(f.name):
            continue
        nom = _etiquette(f)
        if f.is_relation and (f.many_to_one or f.one_to_one):
            if getattr(f, "related_model", None) is MembreEntreprise:
                colonnes.append(_c(f.name, nom, "texte", f"{f.name}.utilisateur.username"))
                liees.append(f"{f.name}__utilisateur")
            colonnes.append(_c(f.attname, f"{nom} (id)", "nombre", f.attname))
        elif isinstance(f, dm.TextField):
            colonnes.append(_c(f"{f.name}_longueur", f"{nom} (longueur)", "nombre", f.name, longueur=True))
        else:
            colonnes.append(_c(f.name, nom, _type_de(f)))
    if len(colonnes) == 1:
        return None
    pluriel = str(modele._meta.verbose_name_plural or modele.__name__)
    if "engine/" in pluriel:
        pluriel = modele.__name__
    return {"cle": modele._meta.model_name, "nom": f"{pluriel[:1].upper()}{pluriel[1:]} ({modele.__name__})", "modele": modele,
            "filtre": (lambda e, m=modele: m._default_manager.filter(pk=e.pk)) if chemin == "" else
                      (lambda e, m=modele, ch=chemin: m._default_manager.filter(**{ch: e})) if chemin else
                      (lambda e, m=modele: m._default_manager.all()),
            "liees": liees, "colonnes": colonnes}


def _registre_complet() -> list:
    from django.apps import apps
    explicites = _registre()
    deja = {s["modele"] for s in explicites}
    generiques = []
    for modele in sorted(apps.get_app_config("engine").get_models(), key=lambda x: x.__name__):
        if modele in deja or modele.__name__ in MODELES_EXCLUS or modele._meta.proxy or modele._meta.abstract:
            continue
        try:
            src = _source_generique(modele)
        except Exception:
            logger.exception(_gt("rg6.msg.111"), modele.__name__)
            continue
        if src:
            generiques.append(src)
    return explicites + generiques


_cache_registre: list | None = None


def registre() -> list:
    global _cache_registre
    if _cache_registre is None:
        _cache_registre = _registre_complet()
    return _cache_registre


def source(cle: str) -> dict | None:
    return next((s for s in registre() if s["cle"] == cle), None)


def est_systeme(table) -> bool:
    return bool(getattr(table, "source_systeme", ""))


def refus_ecriture(table):
    """Réponse 403 JSON si `table` est une table système (lecture seule), sinon None — à appeler au début de toute écriture."""
    if not est_systeme(table):
        return None
    from django.http import JsonResponse
    return JsonResponse({"erreur": _gt("rg5.msg.004"),
                         "systeme": True}, status=403)


# ── Lecture des lignes ──────────────────────────────────────────────────────────────────────────────────────────
def _valeur(brute, type_champ):
    """Valeur Python simple, prête pour pandas : Decimal → float, date-heure sans fuseau (heure du serveur), None conservé."""
    if brute is None:
        return None
    if isinstance(brute, Decimal):
        return float(brute)
    if type_champ == "texte" and not isinstance(brute, str):
        return str(brute)
    if type_champ == "datetime" and hasattr(brute, "tzinfo"):
        try:
            from django.utils import timezone
            if timezone.is_aware(brute):
                brute = timezone.make_naive(brute)
        except Exception:
            brute = brute.replace(tzinfo=None)
    return brute


def _ligne(objet, colonnes) -> dict:
    ligne = {}
    for c in colonnes:
        brute = _lire(objet, c["chemin"])
        if c.get("longueur"):   # un texte n'est jamais exposé : seulement sa longueur
            ligne[c["slug"]] = len(str(brute or ""))
        else:
            ligne[c["slug"]] = _valeur(brute, c["type"])
    return ligne


def _queryset(table):
    src = source(table.source_systeme)
    if src is None:
        return None, None
    qs = src["filtre"](table.entreprise)
    if src["liees"]:
        qs = qs.select_related(*src["liees"])
    return src, qs.order_by("pk")


def lignes(table) -> list:
    """[{**valeurs par slug, "_id": pk}] de la table système, ordre chronologique (pk croissant)."""
    src, qs = _queryset(table)
    if src is None:
        return []
    resultat = []
    for objet in qs.iterator(chunk_size=2000):
        ligne = _ligne(objet, src["colonnes"])
        ligne["_id"] = objet.pk
        resultat.append(ligne)
    return resultat


def dataframe(table, avec_id=False):
    """DataFrame typé de la table système : une colonne par champ, dans l'ordre des champs, typé comme une table dynamique
    (parquet_store._normaliser). C'est ce que `parquet_store._lire_eav` renvoie pour toute table système."""
    import pandas as pd
    from . import parquet_store
    slugs = [c.slug for c in table.champs.all().order_by("ordre", "id")]
    donnees = lignes(table)
    df = pd.DataFrame({s: pd.Series([l.get(s) for l in donnees], dtype=object) for s in slugs})
    df = parquet_store._normaliser(table, df)
    if avec_id:
        df.insert(0, parquet_store.COLONNE_ID, pd.Series([l["_id"] for l in donnees], dtype="int64"))
    return df


def _json(valeur):
    if valeur is None or isinstance(valeur, (str, int, float, bool)):
        return valeur
    if hasattr(valeur, "isoformat"):
        return valeur.isoformat()
    return str(valeur)


def page_grille(table, page=1, taille=200, q=""):
    """(lignes JSON, total) pour la grille de données : pages de `taille` lignes, les plus récentes d'abord ; `q` filtre sur tout le texte."""
    tout = lignes(table)
    tout.reverse()
    if q:
        needle = q.lower()
        tout = [l for l in tout if any(needle in str(v).lower() for k, v in l.items() if k != "_id" and v is not None)]
    total = len(tout)
    debut = (page - 1) * taille
    return [{k: _json(v) for k, v in l.items()} for l in tout[debut:debut + taille]], total


# ── Création / mise à jour des tables de l'entreprise ───────────────────────────────────────────────────────────
_verrou = threading.Lock()
_faits: dict = {}


def _slug_libre(entreprise, base):
    from .models import TableDynamique
    slug, n = base, 2
    while TableDynamique.objects.filter(entreprise=entreprise, slug=slug).exists():
        slug, n = f"{base}_{n}", n + 1
    return slug


def assurer(entreprise) -> int:
    """Crée les tables système manquantes de l'entreprise et resynchronise leurs colonnes avec le registre. Idempotent ; retourne le nombre
    de tables créées ou modifiées. Ne touche jamais aux lignes (il n'y en a pas) ni aux tables dynamiques ordinaires."""
    from django.db import transaction
    from .models import ChampDynamique, TableDynamique
    modifiees = 0
    existantes = {t.source_systeme: t for t in TableDynamique.objects.filter(entreprise=entreprise).exclude(source_systeme="")}
    with transaction.atomic():
        for src in registre():
            table = existantes.get(src["cle"])
            nom = PREFIXE_NOM + src["nom"]
            if table is None:
                # bulk_create : aucun signal. Une table système n'est ni une « création de table » à inscrire dans la blockchain, ni une
                # nouvelle donnée pour le modèle fantôme, ni un fichier Parquet à construire (elle n'a pas de lignes propres).
                slug = _slug_libre(entreprise, PREFIXE_SLUG + src["cle"])
                TableDynamique.objects.bulk_create([TableDynamique(
                    entreprise=entreprise, nom=nom, slug=slug, source_systeme=src["cle"], type_stockage=TableDynamique.STOCKAGE_SQL,
                    description=_gtfr("engine/tables_systeme.py:425:33"))])
                table = TableDynamique.objects.get(entreprise=entreprise, slug=slug)
                modifiees += 1
            elif table.nom != nom:
                TableDynamique.objects.filter(pk=table.pk).update(nom=nom)
            champs = {c.slug: c for c in table.champs.all()}
            voulus = {c["slug"]: (i, c) for i, c in enumerate(src["colonnes"])}
            a_creer = [ChampDynamique(table=table, nom=c["nom"], slug=c["slug"], type_champ=c["type"], obligatoire=False, ordre=i)
                       for i, c in voulus.values() if c["slug"] not in champs]
            a_maj = []
            for slug, champ in champs.items():
                if slug not in voulus:
                    continue
                i, c = voulus[slug]
                if (champ.nom, champ.type_champ, champ.ordre) != (c["nom"], c["type"], i):
                    champ.nom, champ.type_champ, champ.ordre = c["nom"], c["type"], i
                    a_maj.append(champ)
            obsoletes = [champ.pk for slug, champ in champs.items() if slug not in voulus]
            if a_creer:
                ChampDynamique.objects.bulk_create(a_creer)
            if a_maj:
                ChampDynamique.objects.bulk_update(a_maj, ["nom", "type_champ", "ordre"])
            if obsoletes:
                ChampDynamique.objects.filter(pk__in=obsoletes).delete()
            if a_creer or a_maj or obsoletes:
                modifiees += 1
    return modifiees


def garantir(request, entreprise) -> None:
    """Appelé à l'accès d'un utilisateur à son entreprise : les tables système existent (création automatique à l'installation ou à la première
    visite d'une entreprise existante). Une seule vérification par requête ; les colonnes ne sont resynchronisées qu'une fois par processus et
    par VERSION. Ne fait jamais échouer la requête."""
    if entreprise is None or getattr(request, "_tables_systeme_ok", False):
        return
    try:
        from .models import TableDynamique
        complet = TableDynamique.objects.filter(entreprise=entreprise).exclude(source_systeme="").count() >= len(registre())
        if not complet or _faits.get(entreprise.pk) != VERSION:
            with _verrou:
                assurer(entreprise)
                _faits[entreprise.pk] = VERSION
        request._tables_systeme_ok = True
    except Exception:
        logger.exception(_gt("rg6.msg.112"), getattr(entreprise, "pk", "?"))
