"""engine/actions.py — Couche "Action" (cahier des charges Phase 3).

Ceci n'introduit AUCUN nouveau moteur : une Action ici est un enregistrement
mince autour d'une fonction déjà existante du projet (voir declarer_action),
qui unifie ce qui était jusqu'ici décidé au cas par cas dans chaque vue :
permission requise (réutilise le rôle MembreEntreprise + la même convention
que middleware.VuesAutoriseesMiddleware — vues_autorisees ne s'applique qu'au
rôle "agent"), validation optionnelle avant exécution, et audit (réutilise
models.JournalAudit — enrichi Phase 13 : type_acteur/cible/agent/fonction/
resultat, via JournalAudit.enregistrer, point d'écriture unique).

Aucune migration, aucun nouveau modèle : le registre vit en mémoire process
(rechargé au démarrage), comme les autres registres du projet (ex. executor.
MODULES_AUTORISES). Une Action ne remplace pas les vues existantes — elle
n'est pour l'instant appelée par aucune (voir étape/expérience associées) :
c'est la fondation, câblée à une première Action de démonstration.
"""
from __future__ import annotations
from engine.i18n import tl
from engine.i18n import t as _i18n_t, tf

import datetime
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .models import ConfigurationIA, JournalAudit
from . import fonctions

logger = logging.getLogger("gyplatform.actions")

_NIVEAU_ROLE = {"lecteur": 0, "agent": 1, "admin": 2}

# ── Cliché avant/après (Phase 13 — audit : ancienne_valeur/nouvelle_valeur) ──
# Champs volumineux ou non pertinents pour un diff d'audit : jamais capturés.
_TYPES_EXCLUS_SNAPSHOT = frozenset({"FileField", "ImageField", "BinaryField", "TextField"})


def _valeur_json_safe(v):
    if v is None or isinstance(v, (str, int, float, bool, list, dict)):
        return v
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return str(v)


def _snapshot_champs(instance):
    """Cliché best-effort des champs simples d'une instance Django (Phase 13) :
    exclut les relations (pas de requête ni de récursion supplémentaire) et
    les champs volumineux (fichiers, texte libre) — uniquement les champs
    scalaires utiles à un diff d'audit. None si `instance` n'est pas un
    modèle Django (ex. dict/liste renvoyé par une Action de lecture, ou
    cible=None)."""
    if instance is None or not hasattr(instance, "_meta"):
        return None
    donnees = {}
    for champ in instance._meta.concrete_fields:
        if champ.is_relation or champ.get_internal_type() in _TYPES_EXCLUS_SNAPSHOT:
            continue
        try:
            donnees[champ.name] = _valeur_json_safe(getattr(instance, champ.name))
        except Exception:
            continue
    return donnees


def _diff_snapshots(avant, apres):
    """Ne garde que les champs qui ont réellement changé entre les deux
    clichés — évite de dupliquer tout l'objet dans JournalAudit à chaque
    Action réussie."""
    if avant is None or apres is None:
        return None, None
    anc, nouv = {}, {}
    for cle in set(avant) | set(apres):
        va, vn = avant.get(cle), apres.get(cle)
        if va != vn:
            anc[cle] = va
            nouv[cle] = vn
    return (anc or None), (nouv or None)


class ActionRefusee(Exception):
    """Permission insuffisante ou validation échouée : jamais journalisé comme
    une erreur d'exécution, distinct d'une exception levée par la fonction."""


@dataclass(frozen=True)
class ActionDescriptor:
    nom: str
    description: str
    fonction: Callable[..., Any]
    role_minimum: str = "agent"
    vue_slug: Optional[str] = None
    validation: Optional[Callable[..., None]] = None
    trace_lineage: bool = True
    types_objet: Optional[tuple[str, ...]] = None


_REGISTRE: dict[str, ActionDescriptor] = {}


def declarer_action(nom, description, role_minimum="agent", vue_slug=None, validation=None,
                     trace_lineage=True, types_objet=None):
    """Décorateur : enregistre une fonction déjà existante comme Action nommée,
    sans changer son comportement pour ses appelants directs actuels.

    `trace_lineage=False` exempte une Action de lignée blockchain (voir
    executer_action) : réservé aux actions de pure LECTURE (lister/lire),
    qui ne produisent ni transaction "métier" ni artefact — l'audit
    JournalAudit, lui, reste inconditionnel.

    `types_objet` déclare à quel(s) type(s de nœud du graphe Ontology (voir
    ontologie.py : "entreprise", "table", "enregistrement", "agent",
    "admin", "client", "config_ia", "tokens"...) cette Action s'applique,
    pour le filtrage de api_ontologie_fiche (Phase 6). `None` (par défaut)
    signifie "non catégorisée" : l'Action reste visible quel que soit le
    nœud consulté, exactement comme avant l'ajout de ce champ — aucune
    régression pour une Action existante qui ne le renseigne pas."""
    def _decorateur(fonction):
        if nom in _REGISTRE:
            raise ValueError(tf("engine/actions.py:g:120:30", v1=nom))
        types = tuple(types_objet) if types_objet else None
        _REGISTRE[nom] = ActionDescriptor(nom, description, fonction, role_minimum, vue_slug,
                                           validation, trace_lineage, types)
        return fonction
    return _decorateur


def _table_id_cible(cible):
    """Résout un id de TableDynamique depuis une cible d'Action, sans requête
    supplémentaire (réutilise pk/table_id/table_source_id — Phase 12 :
    IndicateurKPI/TableCalcul/TableauBordBI utilisent tous `table_source`,
    pas `table_id`, d'où le second fallback). Point unique : _reperer_cible
    ci-dessous le réutilise plutôt que de dupliquer la même résolution."""
    if cible is None:
        return None
    if cible.__class__.__name__ == "TableDynamique":
        return getattr(cible, "pk", None)
    return getattr(cible, "table_id", None) or getattr(cible, "table_source_id", None)


# Droit de délégation (delegation.py) qui ouvre chaque action de niveau administrateur à un agent ; toute autre action de ce niveau
# (y compris une action ajoutée plus tard) reste sous le droit `actions_admin`.
_DROIT_PAR_ACTION = {
    **{n: "reglages_entreprise" for n in ("modifier_limite_lignes", "modifier_limite_colonnes", "modifier_inscription_ouverte", "modifier_api_ouverte")},
    **{n: "marque" for n in ("uploader_logo", "uploader_logo_base64")},
    **{n: "config_ia" for n in ("modifier_config_ia", "reinitialiser_tokens")},
    **{n: "cartes" for n in ("configurer_carte_table", "generer_cartes", "generer_cartes_images", "generer_cartes_zip",
                             "configurer_carte_base64", "generer_cartes_agent", "generer_cartes_zip_agent")},
    **{n: "cloture_cycle" for n in ("cloturer_cycle", "restaurer_cycle", "lister_archives_cycle")},
    "synchroniser_wallet_table": "portefeuilles",
    **{n: "employes" for n in ("exporter_employes_pdf", "lister_employes", "creer_employe", "supprimer_employe", "modifier_role_employe",
                               "modifier_employe", "lire_permissions_membre", "modifier_permissions_membre")},
}


def _permission_autorisee(membre, descripteur: ActionDescriptor, cible=None) -> bool:
    if membre is None:
        return False
    from .delegation import a_pouvoir
    niveau = _NIVEAU_ROLE["admin"] if a_pouvoir(membre, _DROIT_PAR_ACTION.get(descripteur.nom, "actions_admin")) else _NIVEAU_ROLE.get(membre.role, -1)   # agent délégué : niveau administrateur, pour CETTE action seulement
    if niveau < _NIVEAU_ROLE.get(descripteur.role_minimum, 0):
        return False
    # Même convention que middleware.VuesAutoriseesMiddleware : ne restreint
    # que le rôle "agent", ignoré pour admin/lecteur.
    if (descripteur.vue_slug and membre.role == "agent"
            and membre.vues_autorisees and descripteur.vue_slug not in membre.vues_autorisees):
        return False
    # Phase 9 (cahier des charges Ontology, « SÉCURITÉ FINE ») : granularité
    # Objet — un agent restreint par tables_autorisees ne peut exécuter
    # aucune Action dont la cible appartient à une table hors de cette
    # liste. `cible=None` (catalogue_pour_agent, verifier_permission avant
    # résolution de la cible) : non applicable, aucune régression pour les
    # appelants existants qui ne passent pas encore de cible ici.
    if membre.role == "agent" and membre.tables_autorisees:
        table_id = _table_id_cible(cible)
        if table_id is not None and table_id not in membre.tables_autorisees:
            return False
    return True


def verifier_permission(nom: str, membre) -> None:
    """Vérifie la permission d'une Action sans l'exécuter ni auditer : utile
    quand une vue doit refuser l'accès (403) avant de faire un travail
    supplémentaire (ex. chercher l'objet cible), comme le faisait son
    contrôle en dur d'origine avant la migration vers la couche Action."""
    descripteur = _REGISTRE.get(nom)
    if descripteur is None:
        raise ActionRefusee(f"Action inconnue : {nom}")
    if not _permission_autorisee(membre, descripteur):
        raise ActionRefusee(tf("engine/actions.py:g:173:29", v1=nom))


def _reperer_cible(cible):
    """Best-effort : type + id de la cible, et table_id si déductible (pour
    qu'une Action sur un enregistrement/une table apparaisse aussi dans
    l'historique blockchain PAR TABLE déjà existant, voir
    views_blockchain.historique_blockchain_table). Aucune hypothèse forte :
    une cible sans .pk ni .table_id/.table_source_id retombe simplement sur
    None. Réutilise _table_id_cible (même résolution que la vérification de
    permission) plutôt que de la dupliquer."""
    if cible is None:
        return None, None, None
    cible_type = cible.__class__.__name__
    cible_id = getattr(cible, "pk", None)
    return cible_type, cible_id, _table_id_cible(cible)

def _verrou_modules_sandbox(membre, cible, corps):
    """§étanchéité (audit 2026-11-20) : les listes `modules_autorises_*` décident ce que le code du bac à sable peut importer.
    Un administrateur d'entreprise ne doit pas pouvoir les ÉLARGIR (ajouter « os », « socket »…) : seul le super-administrateur
    Django le peut. Sont acceptés : renvoyer la liste déjà en vigueur (formulaire complet renvoyé tel quel) ou la RESTREINDRE ;
    tout module qui n'est pas déjà autorisé est refusé. MODULES_JAMAIS_AUTORISES (executor) reste appliqué même au super-admin."""
    if getattr(getattr(membre, "utilisateur", None), "is_superuser", False) or not isinstance(corps, dict):
        return
    from . import executor as _ex
    cfg = cible if isinstance(cible, ConfigurationIA) else getattr(cible, "configuration_ia", None)
    defauts = {"script": _ex.DEFAULT_MODULES_AUTORISES_SCRIPT, "document": _ex.DEFAULT_MODULES_AUTORISES_DOCUMENT,
               "formule": _ex.DEFAULT_MODULES_AUTORISES_FORMULE, "graphique_bi": _ex.DEFAULT_MODULES_AUTORISES_GRAPHIQUE_BI,
               "couche_ontologie": _ex.DEFAULT_MODULES_AUTORISES_ONTOLOGIE, "modele_ia": _ex.DEFAULT_MODULES_AUTORISES_MODELE_IA}

    def _ensemble(texte):
        return {m.strip() for m in str(texte or "").split(",") if m.strip()}

    for champ, valeur in corps.items():
        if not isinstance(champ, str) or not champ.startswith("modules_autorises_"):
            continue
        canal = champ[len("modules_autorises_"):]
        stocke = _ensemble(getattr(cfg, champ, "") if cfg is not None else "")
        en_vigueur = stocke or set(defauts.get(canal, ()))
        if canal == "modele_ia":   # ancien défaut sans jax/flax/optax : remplacé à l'affichage par le défaut actuel (executor.modules_modele_ia_a_jour)
            en_vigueur |= _ensemble(_ex.modules_modele_ia_a_jour(", ".join(sorted(stocke))))
        if not _ensemble(valeur) <= en_vigueur:
            raise ActionRefusee("Seul le super-administrateur peut élargir les modules autorisés du bac à sable.")


def executer_action(nom: str, membre, cible=None, *, origine: str = "utilisateur", **params) -> Any:
    """Point de passage unique proposé pour toute Action : permission -> 
    validation -> fonction -> audit -> lignée. Lève ActionRefusee (403 côté
    vue) ou laisse remonter l'exception de la fonction (journalisée en
    échec).

    `origine` (Phase 13 — audit) : "utilisateur" (défaut, appel depuis une
    vue sur une session humaine) ou "agent_ia" (exécution automatique par
    agent_ontology.tenter_action, sans confirmation humaine — voir
    _CIBLE_AUTO) ; reporté tel quel dans JournalAudit.type_acteur.

    ancienne_valeur/nouvelle_valeur (Phase 13 — audit) : cliché des champs
    scalaires de `cible` avant/après l'appel (_snapshot_champs), réduit aux
    seuls champs qui ont changé (_diff_snapshots) ; None pour toute Action
    de lecture (trace_lineage=False) ou dont la cible n'est pas un modèle
    Django (dict/liste, cible=None).

    Lignée (Phase 4 du cahier des charges Ontology — Provenance/Events) :
    réutilise TEL QUEL le registre blockchain existant (models.Bloc, déjà
    utilisé pour les tables/lignes/wallet) au lieu d'un second système —
    chaque Action réussie devient une transaction "action:<nom>" dans LA
    MÊME chaîne. Cela authentifie l'action (horodatage, chaînage SHA256,
    voir Bloc.chaine_valide) ET déclenche gratuitement les tâches
    conditionnelles existantes (apps.py::declencheur_tache_blockchain ->
    declencheurs.evaluer_conditions(..., "blockchain", ...)) : un
    utilisateur peut déjà configurer une TachePlanifiee sur
    condition_categorie="blockchain" avec type_evenement="action:<nom>"
    (champ libre, voir templates/engine/taches.html) sans aucun changement
    d'interface. Uniquement sur SUCCÈS : un échec ne change aucune donnée
    (voir les fonctions du groupe SMS/Cartes, qui valident avant d'écrire),
    donc rien à tracer dans le registre de lignée — l'échec reste visible
    dans JournalAudit, inconditionnel, juste au-dessus."""
    descripteur = _REGISTRE.get(nom)
    if descripteur is None:
        raise ActionRefusee(f"Action inconnue : {nom}")
    if not _permission_autorisee(membre, descripteur, cible):
        raise ActionRefusee(tf("engine/actions.py:g:227:29", v1=nom))
    if nom == "modifier_config_ia":
        _verrou_modules_sandbox(membre, cible, params.get("corps"))
    if descripteur.validation is not None:
        descripteur.validation(cible, **params)

    entreprise = getattr(membre, "entreprise", None)
    cible_type, cible_id, table_id = _reperer_cible(cible)
    agent_utilise = ""
    if origine == "agent_ia" and entreprise is not None:
        config = getattr(entreprise, "configuration_ia", None)
        if config is not None:
            agent_utilise = f"{config.moteur_type}:{config.moteur_modele}" if config.moteur_modele else config.moteur_type
    try:
        snapshot_avant = _snapshot_champs(cible) if descripteur.trace_lineage else None
    except Exception:
        snapshot_avant = None
    try:
        resultat = descripteur.fonction(cible, **params) if cible is not None else descripteur.fonction(**params)
    except Exception as exc:
        ancienne_valeur, nouvelle_valeur = None, None
        if snapshot_avant is not None:
            try:
                ancienne_valeur, nouvelle_valeur = _diff_snapshots(snapshot_avant, _snapshot_champs(cible))
            except Exception:
                pass
        if entreprise is not None:
            JournalAudit.enregistrer(
                entreprise=entreprise, utilisateur=getattr(membre, "utilisateur", None),
                action=f"action:{nom}", details={"statut": "echec", "erreur": str(exc)},
                type_acteur=origine, source=origine, cible_type=cible_type or "", cible_id=cible_id,
                agent_utilise=agent_utilise, fonction_utilisee=descripteur.fonction.__name__, resultat="echec",
                ancienne_valeur=ancienne_valeur, nouvelle_valeur=nouvelle_valeur,
            )
        raise
    ancienne_valeur, nouvelle_valeur = None, None
    if snapshot_avant is not None:
        try:
            ancienne_valeur, nouvelle_valeur = _diff_snapshots(snapshot_avant, _snapshot_champs(cible))
        except Exception:
            pass
    if entreprise is not None:
        JournalAudit.enregistrer(
            entreprise=entreprise, utilisateur=getattr(membre, "utilisateur", None),
            action=f"action:{nom}", details={"statut": "succes"},
            type_acteur=origine, source=origine, cible_type=cible_type or "", cible_id=cible_id,
            agent_utilise=agent_utilise, fonction_utilisee=descripteur.fonction.__name__, resultat="succes",
            ancienne_valeur=ancienne_valeur, nouvelle_valeur=nouvelle_valeur,
        )
        if descripteur.trace_lineage:
            from .models import Bloc
            tx = {
                "type": f"action:{nom}", "action": nom,
                "cible_type": cible_type, "cible_id": cible_id,
                "utilisateur_id": getattr(membre, "utilisateur_id", None),
                "statut": "succes",
            }
            if table_id is not None:
                tx["table_id"] = table_id
            Bloc.ajouter_transaction(entreprise.id, tx)
    return resultat


# ── Première Action câblée : régénérer le script d'une demande ────────────
# Reprend tel quel le corps de views.api_regenerer_script (statut + tâche
# Celery existante) : aucune nouvelle logique, l'exécution reste asynchrone,
# seule la décision permission+audit est désormais unifiée. Appelée depuis
# views.api_regenerer_script.
def _regenerer_script_demande(demande):
    from . import tasks
    demande.statut = "en_cours"
    demande.save(update_fields=["statut"])
    tasks.generer_script_demande_tache.delay(demande.id)
    return demande


declarer_action(
    "regenerer_script_demande",
    tl("engine/actions.py:g:304:5"),
    role_minimum="agent",
    # DemandeAgent n'a pas (encore) de nœud dans le graphe Ontology : pas de
    # type_objet possible aujourd'hui — reste non catégorisée (visible partout).
)(_regenerer_script_demande)


# ── Groupe "Configuration plateforme" : réglages globaux réservés à l'admin,
# sauvegarde synchrone (pas de Celery). Reprend tel quel le corps des vues
# views.api_limite_lignes_sauvegarder / api_limite_colonnes_sauvegarder :
# aucune nouvelle logique, seule la décision permission+audit est unifiée.
def _modifier_limite_lignes(entreprise, limite):
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    cfg.limite_lignes = limite
    cfg.save(update_fields=["limite_lignes"])
    return cfg


def _modifier_limite_colonnes(entreprise, limite):
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    cfg.limite_colonnes = limite
    cfg.save(update_fields=["limite_colonnes"])
    return cfg


declarer_action(
    "modifier_limite_lignes",
    tl("engine/actions.py:g:331:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_modifier_limite_lignes)

declarer_action(
    "modifier_limite_colonnes",
    tl("engine/actions.py:g:338:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_modifier_limite_colonnes)


# ── Suite du groupe "Configuration plateforme" ─────────────────────────────
def _modifier_inscription_ouverte(entreprise, ouverte):
    """Reprend tel quel views.api_inscription_ouverte."""
    entreprise.inscription_ouverte = ouverte
    entreprise.save(update_fields=["inscription_ouverte"])
    return entreprise


declarer_action(
    "modifier_inscription_ouverte",
    tl("engine/actions.py:g:354:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_modifier_inscription_ouverte)


def _modifier_api_ouverte(entreprise, ouverte):
    """Ouvre ou ferme l'accès API externe (clés Bearer) de l'entreprise ; le navigateur n'est pas concerné."""
    entreprise.api_ouverte = ouverte
    entreprise.save(update_fields=["api_ouverte"])
    return entreprise


declarer_action(
    "modifier_api_ouverte",
    tl("engine/actions.py:api_ouverte"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_modifier_api_ouverte)


def _uploader_logo(entreprise, fichier):
    """Reprend tel quel views.api_upload_logo (validation d'extension et
    lecture du fichier restent dans la vue ; ici uniquement l'écriture et
    le nettoyage de l'ancien logo)."""
    ancien_logo = entreprise.logo.name if entreprise.logo else None
    entreprise.logo = fichier
    entreprise.save(update_fields=["logo"])
    if ancien_logo:
        try:
            from django.core.files.storage import default_storage
            if default_storage.exists(ancien_logo):
                default_storage.delete(ancien_logo)
        except Exception:
            pass
    return entreprise


declarer_action(
    "uploader_logo",
    tl("engine/actions.py:g:379:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_uploader_logo)


_CHAMPS_CONFIG_IA_AUTORISES = [
    "system_prompt", "role_analysis_script", "role_skill_script",
    "role_report_summary", "role_other", "role_document",
    "regle_script", "regle_document",
    "contraintes_syntaxe", "contraintes_document",
    "script_exemple", "script_exemple_document",
    "modules_autorises_script", "modules_autorises_document",
    "role_formule", "regle_formule", "contraintes_formule",
    "script_exemple_formule_colonne", "script_exemple_formule_table",
    "modules_autorises_formule",
    "role_graphique_bi", "regle_graphique_bi", "contraintes_graphique_bi",
    "script_exemple_graphique_bi_colonne", "script_exemple_graphique_bi_multiples",
    "modules_autorises_graphique_bi",
    "role_couche_ontologie", "regle_couche_ontologie", "contraintes_couche_ontologie",
    "script_exemple_couche_ontologie", "modules_autorises_couche_ontologie",
    "role_modele_ia", "regle_modele_ia", "contraintes_modele_ia",
    "script_exemple_modele_ia", "modules_autorises_modele_ia",
    "role_tache", "regle_tache", "contraintes_tache",
    "script_exemple_tache", "modules_autorises_tache",
    "moteur_type", "moteur_host", "moteur_port", "moteur_url_externe", "moteur_modele",
]


def _modifier_config_ia(entreprise, corps):
    """Reprend tel quel views.api_config_ia_sauvegarder, liste de champs
    autorisés comprise (déplacée depuis la vue, inchangée)."""
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    for champ in _CHAMPS_CONFIG_IA_AUTORISES:
        if champ in corps:
            setattr(cfg, champ, corps[champ])
    if corps.get("moteur_cle_api"):
        cfg.moteur_cle_api = corps["moteur_cle_api"]
    cfg.save()
    return cfg


declarer_action(
    "modifier_config_ia",
    tl("engine/actions.py:g:423:5"),
    role_minimum="admin",
    types_objet=("entreprise", "config_ia"),
)(_modifier_config_ia)


def _reinitialiser_tokens(entreprise):
    """Reprend tel quel views.api_tokens_reinitialiser."""
    from django.utils import timezone
    from .models import CompteurTokens
    compteur, _ = CompteurTokens.objects.get_or_create(entreprise=entreprise, utilisateur=None)
    compteur.tokens_total = 0
    compteur.tokens_entree = 0
    compteur.tokens_sortie = 0
    compteur.alerte_active = False
    compteur.debut_periode = timezone.now()
    compteur.save()
    return compteur


declarer_action(
    "reinitialiser_tokens",
    tl("engine/actions.py:g:445:5"),
    role_minimum="admin",
    types_objet=("entreprise", "tokens"),
)(_reinitialiser_tokens)


# ── Groupe "Cartes / Cycles" : configuration + génération des cartes
# d'agents, clôture/restauration de cycle, exports PDF. Reprend tel quel le
# corps des vues correspondantes ; la validation de format (JSON, ids) reste
# côté vue comme pour les groupes précédents, seules la mutation/génération
# et la décision permission+audit passent par la couche Action.
class ArchiveInvalide(Exception):
    """Le zip fourni n'appartient pas à l'entreprise, ou est structurellement
    invalide (400 côté vue) — distingue une erreur de données d'un refus de
    permission."""


def _configurer_carte_table(table, fichiers, champs_texte, config_carte):
    """Reprend tel quel views.api_table_config_carte (fichiers/valeurs déjà
    extraits de request.FILES/request.POST côté vue ; config_carte déjà
    parsé en JSON côté vue, ou None si absent)."""
    maj = []
    for champ_fichier, attr in (
        ("theme_recto", "theme_carte_recto"), ("theme_verso", "theme_carte_verso"),
        ("signature", "carte_signature_image"), ("cachet", "carte_cachet_image"),
        ("qrcode", "carte_qrcode_image"),
    ):
        if champ_fichier in fichiers:
            setattr(table, attr, fichiers[champ_fichier])
            maj.append(attr)
    if "signature_nom" in champs_texte:
        table.carte_signature_nom = champs_texte["signature_nom"][:150]
        maj.append("carte_signature_nom")
    if "matricule_prefixe" in champs_texte:
        table.matricule_prefixe = champs_texte["matricule_prefixe"].strip()[:20]
        maj.append("matricule_prefixe")
    if config_carte is not None:
        table.config_carte = config_carte
        maj.append("config_carte")
    if maj:
        table.save(update_fields=maj)
    return table


declarer_action(
    "configurer_carte_table",
    tl("engine/actions.py:g:623:5"),
    role_minimum="admin",
    types_objet=("table",),
)(_configurer_carte_table)


def _generer_cartes(table, enregistrements, champs_par_slug):
    """Reprend tel quel views.api_generer_cartes (validation ids/config déjà
    faite côté vue)."""
    from .reports import generer_pdf_cartes_agents
    return generer_pdf_cartes_agents(table, champs_par_slug, enregistrements, table.config_carte)


declarer_action(
    "generer_cartes",
    tl("engine/actions.py:g:638:5"),
    role_minimum="admin",
    types_objet=("table",),
)(_generer_cartes)


def _generer_cartes_images(table, enregistrements, champs_par_slug):
    """Cartes exportées individuellement en PNG (config_carte["sortie"] == "images") : (octets, type MIME, nom de fichier)."""
    from .reports import generer_cartes_images
    return generer_cartes_images(table, champs_par_slug, enregistrements, table.config_carte or {})


declarer_action(
    "generer_cartes_images",
    tl("rg8.msg.001"),
    role_minimum="admin",
    types_objet=("table",),
)(_generer_cartes_images)


def _generer_cartes_zip(table, enregistrements, champs_par_slug):
    """Reprend tel quel views.api_generer_cartes_zip (découpage par lots de 10)."""
    from .reports import generer_pdf_cartes_agents
    import io as _io
    import zipfile
    tampon_zip = _io.BytesIO()
    with zipfile.ZipFile(tampon_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for lot_num, debut in enumerate(range(0, len(enregistrements), 10), start=1):
            lot = enregistrements[debut:debut + 10]
            pdf_bytes = generer_pdf_cartes_agents(table, champs_par_slug, lot, table.config_carte)
            zf.writestr(f"cartes_{table.slug}_page{lot_num}.pdf", pdf_bytes)
    return tampon_zip.getvalue()


declarer_action(
    "generer_cartes_zip",
    tl("engine/actions.py:g:660:5"),
    role_minimum="admin",
    types_objet=("table",),
)(_generer_cartes_zip)


def _cloturer_cycle(entreprise, utilisateur):
    """Reprend tel quel views.api_cloturer_cycle."""
    import tempfile
    from django.core.files import File
    from .cycle import generer_zip_cloture_vers, reinitialiser_cycle
    from .models import ArchiveCycle
    # Archive complète (données + modèles entraînés + fichiers) écrite sur disque : jamais
    # chargée en mémoire d'un bloc, même avec de gros modèles.
    with tempfile.TemporaryFile() as tmp:
        generer_zip_cloture_vers(entreprise, tmp, avec_tuiles=True)
        tmp.seek(0)
        archive = ArchiveCycle.objects.create(entreprise=entreprise.saison_de or entreprise, cree_par=utilisateur)   # l'archive reste à l'entreprise d'origine
        archive.fichier.save(f"cloture_{entreprise.id}_{archive.id}.zip", File(tmp))
    if entreprise.saison_de_id:   # saison restaurée : reclôturée = retirée (la saison en cours et ses fichiers ne sont jamais touchés)
        from django.db import transaction
        from .cycle import _supprimer_donnees_entreprise
        with transaction.atomic():
            _supprimer_donnees_entreprise(entreprise, membres_aussi=True)
            entreprise.delete()
    else:
        reinitialiser_cycle(entreprise)
    return archive


declarer_action(
    "cloturer_cycle",
    tl("engine/actions.py:g:685:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_cloturer_cycle)


def _synchroniser_wallet_table(entreprise):
    """Reprend tel quel views_wallet.api_synchroniser_wallet_table."""
    from . import wallet as _wallet
    return _wallet.synchroniser_wallet_vers_table(entreprise)


declarer_action(
    "synchroniser_wallet_table",
    tl("engine/actions.py:g:699:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_synchroniser_wallet_table)


def _restaurer_cycle(entreprise, source, utilisateur=None, remplacer=False):
    """Reprend tel quel views.api_restaurer_cycle (ValueError/KeyError
    d'origine traduites en ArchiveInvalide pour que la vue les distingue
    d'un refus de permission)."""
    from .cycle import restaurer_zip_cycle
    try:
        restaurer_zip_cycle(entreprise, source, garder_utilisateur=utilisateur, remplacer=remplacer)
    except ValueError:
        raise ArchiveInvalide(_i18n_t("engine/actions.py:g:712:31"))
    except KeyError:
        raise ArchiveInvalide(_i18n_t("engine/actions.py:g:714:31"))


declarer_action(
    "restaurer_cycle",
    tl("engine/actions.py:g:720:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_restaurer_cycle)


def _lister_archives_cycle(entreprise):
    """Reprend tel quel views.api_liste_archives_cycle."""
    from .models import ArchiveCycle
    return list(ArchiveCycle.objects.filter(entreprise=entreprise).values("id", "cree_le"))


declarer_action(
    "lister_archives_cycle",
    tl("engine/actions.py:g:734:5"),
    role_minimum="admin",
    trace_lineage=False,
    types_objet=("entreprise",),
)(_lister_archives_cycle)


def _tracer_lineage_valeur(entreprise, noeud_id, page_id=None):
    """Phase 7 du cahier des charges Ontology — « d'où vient cette valeur ? »
    en une seule requête. Reconstruit le graphe (respectant l'isolation de
    l'agent — voir ontologie.construire_graphe) puis délègue le parcours à
    ontologie_requetes.tracer_lineage. Lecture seule, aucune trace de
    lignée à écrire pour un simple parcours (trace_lineage=False)."""
    from . import ontologie as _onto
    from .ontologie_requetes import tracer_lineage
    from .models import PageOntologie

    page = None
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()
    # Le membre courant n'est pas transmis ici (signature Action = cible +
    # paramètres texte, voir executer_action) : l'isolation par agent est
    # réappliquée côté vue (views.api_ontologie_lineage), qui reconstruit le
    # graphe avec le membre avant d'appeler tracer_lineage directement — cette
    # fonction-ci ne sert que le chemin Action générique (agent conversationnel,
    # audit unifié) où l'isolation à date n'a pas encore été branchée pour ce
    # cas précis (limite documentée, comme ontologie_fiche pour page/membre).
    graphe = (
        _onto.construire_graphe_manuel(page, entreprise)
        if page is not None and page.mode == "manuel"
        else _onto.construire_graphe(entreprise, page=page)
    )
    return tracer_lineage(graphe, noeud_id)


declarer_action(
    "tracer_lineage_valeur",
    tl("engine/actions.py:g:771:5"),
    role_minimum="agent",
    trace_lineage=False,
    types_objet=("entreprise",),
)(_tracer_lineage_valeur)


def _exporter_employes_pdf(entreprise):
    """Reprend tel quel views.api_exporter_employes_pdf."""
    from .reports import generer_pdf_liste_employes
    from .models import MembreEntreprise
    membres = MembreEntreprise.objects.filter(entreprise=entreprise).select_related('utilisateur')
    return generer_pdf_liste_employes(entreprise, membres)


declarer_action(
    "exporter_employes_pdf",
    tl("engine/actions.py:g:788:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_exporter_employes_pdf)


# ── Groupe "Employés / permissions" : liste+création, suppression, rôle,
# édition complète, et droits de vues (vues_autorisees) d'un membre. Reprend
# tel quel le corps des vues correspondantes ; les validations de format
# (formulaire de création, rôle/valeurs autorisées, unicité du username, 404
# du membre ciblé) restent côté vue comme pour les groupes précédents, seules
# les requêtes/mutations et la décision permission+audit passent ici.
def _lister_employes(entreprise, page, taille):
    """Reprend tel quel views.api_employes (partie GET : requête paginée)."""
    from .models import MembreEntreprise
    membres_qs = MembreEntreprise.objects.filter(entreprise=entreprise).select_related(
        'utilisateur'
    ).order_by('utilisateur__date_joined')
    total = membres_qs.count()
    pages = max(1, (total + taille - 1) // taille)
    page = min(page, pages)
    offset = (page - 1) * taille
    membres = list(membres_qs[offset: offset + taille])
    return {"membres": membres, "page": page, "taille": taille, "total": total, "pages": pages}


declarer_action(
    "lister_employes",
    tl("engine/actions.py:g:816:5"),
    role_minimum="admin",
    trace_lineage=False,
    types_objet=("entreprise",),
)(_lister_employes)


def _creer_employe(entreprise, donnees):
    """Reprend tel quel views.api_employes (partie POST ; formulaire
    CreerEmployeForm déjà validé côté vue, rôle toujours "agent")."""
    from django.contrib.auth import get_user_model
    from .models import MembreEntreprise
    from . import acces_entreprise
    if not acces_entreprise.place_disponible(entreprise):
        raise ActionRefusee(acces_entreprise.message_limite(entreprise))
    User = get_user_model()
    employe = User.objects.create_user(
        username=donnees['username'],
        password=donnees['password'],
        first_name=donnees['first_name'],
        last_name=donnees['last_name'],
    )
    MembreEntreprise.objects.create(
        utilisateur=employe, entreprise=entreprise, role='agent',
        indicatif_pays=donnees.get('indicatif_pays', '+242'),
        telephone=donnees.get('telephone', ''),
        numero_client=None,
    )
    return employe


declarer_action(
    "creer_employe",
    tl("engine/actions.py:g:846:5"),
    role_minimum="admin",
    types_objet=("entreprise",),
)(_creer_employe)


def _supprimer_employe(entreprise, user_id, admin_membre=None):
    """Retire un employé. Pour un AGENT, on ne supprime pas son travail mais son accès : ses ressources sont reprises par
    l'administrateur en attendant un remplaçant (voir engine/reprise_poste.py). Retourne True si un membre a été retiré."""
    from .models import MembreEntreprise
    from . import reprise_poste
    if admin_membre is None:
        admin_membre = MembreEntreprise.objects.filter(entreprise=entreprise, role="admin").order_by("id").first()
    resultat = reprise_poste.retirer_agent(entreprise, admin_membre, user_id)
    return resultat is not None


declarer_action(
    "supprimer_employe",
    tl("engine/actions.py:g:865:5"),
    role_minimum="admin",
    types_objet=("agent", "admin", "client"),
)(_supprimer_employe)


def _modifier_role_employe(entreprise, user_id, nouveau_role):
    """Reprend tel quel views.api_modifier_role_employe (rôle déjà validé
    côté vue)."""
    from .models import MembreEntreprise
    MembreEntreprise.objects.filter(entreprise=entreprise, utilisateur_id=user_id).update(role=nouveau_role)


declarer_action(
    "modifier_role_employe",
    tl("engine/actions.py:g:880:5"),
    role_minimum="admin",
    types_objet=("agent", "admin", "client"),
)(_modifier_role_employe)


def _modifier_employe(membre_cible, corps):
    """Reprend tel quel views.api_modifier_employe (lookup 404 et unicité du
    username déjà vérifiés côté vue ; ne restent ici que les mutations sur
    l'utilisateur et le membre)."""
    cible = membre_cible.utilisateur
    if "first_name" in corps:
        cible.first_name = corps["first_name"].strip()
    if "last_name" in corps:
        cible.last_name = corps["last_name"].strip()
    if "username" in corps and corps["username"].strip():
        cible.username = corps["username"].strip()
    if "password" in corps and corps["password"].strip():
        cible.set_password(corps["password"])
    cible.save()

    if "role" in corps and corps["role"] in ("admin", "agent", "lecteur"):
        membre_cible.role = corps["role"]
    if "telephone" in corps:
        membre_cible.telephone = corps["telephone"].strip()
    if "indicatif_pays" in corps:
        membre_cible.indicatif_pays = corps["indicatif_pays"].strip()
    membre_cible.save()
    return membre_cible


declarer_action(
    "modifier_employe",
    tl("engine/actions.py:g:913:5"),
    role_minimum="admin",
    types_objet=("agent", "admin", "client"),
)(_modifier_employe)


def _lire_permissions_membre(cible):
    """Reprend tel quel views.api_membre_permissions (partie GET), étendu
    Phase 9 : tables_autorisees et champs_masques (granularité Objet/Champ,
    au-delà de vues_autorisees qui ne restreint que des onglets)."""
    return {
        "vues_autorisees": cible.vues_autorisees, "role": cible.role,
        "tables_autorisees": cible.tables_autorisees,
        "champs_masques": cible.champs_masques,
        "relations_masquees": cible.relations_masquees,
    }


declarer_action(
    "lire_permissions_membre",
    tl("engine/actions.py:g:933:5"),
    role_minimum="admin",
    trace_lineage=False,
    types_objet=("agent", "admin", "client"),
)(_lire_permissions_membre)


def _modifier_permissions_membre(cible, vues, tables=None, champs_masques=None, relations=None):
    """Reprend tel quel views.api_membre_permissions (partie POST) —
    None/absent = pas de restriction, sinon ne garde que les slugs connus de
    VUES_DISPONIBLES (import tardif pour éviter toute dépendance circulaire
    avec views.py). Phase 9 : `tables` (liste d'IDs TableDynamique) et
    `champs_masques` (dict {str(table_id): [slugs]}) suivent la même
    convention None/vide = pas de restriction — validés a minima (types
    attendus) pour ne jamais stocker une valeur qui ferait planter
    tables_autorisees/champs_masques ailleurs (grille.masquer_champs,
    actions._permission_autorisee). Phase 9 suite (granularité Relation) :
    `relations` (liste de types de relations, ex. ["mesure","affiche"]) ne
    garde que les types connus de ontologie.DESCRIPTIONS_RELATIONS —
    empêche de stocker une faute de frappe qui ne masquerait jamais rien
    silencieusement."""
    from .views import VUES_DISPONIBLES
    from .ontologie import DESCRIPTIONS_RELATIONS
    cible.vues_autorisees = None if vues is None else [
        s for s, _n in VUES_DISPONIBLES if s in vues
    ]
    cible.tables_autorisees = None if not tables else [
        int(t) for t in tables if str(t).lstrip("-").isdigit()
    ] or None
    if not champs_masques:
        cible.champs_masques = None
    else:
        cible.champs_masques = {
            str(int(table_id)): [str(s) for s in (slugs or [])]
            for table_id, slugs in champs_masques.items()
            if str(table_id).lstrip("-").isdigit() and slugs
        } or None
    cible.relations_masquees = None if not relations else [
        r for r in relations if r in DESCRIPTIONS_RELATIONS
    ] or None
    cible.save(update_fields=[
        "vues_autorisees", "tables_autorisees", "champs_masques", "relations_masquees",
    ])
    return cible


declarer_action(
    "modifier_permissions_membre",
    tl("engine/actions.py:g:981:5"),
    role_minimum="admin",
    types_objet=("agent", "admin", "client"),
)(_modifier_permissions_membre)


# ── Groupe "BI / Calculs" (cahier des charges Ontology, Phase 12) : ces
# Actions n'introduisent AUCUN nouveau moteur de calcul — elles enveloppent
# kpi.calculer_kpi et calculs.recalculer_table_calcul, déjà utilisés tels
# quels par les vues Django existantes (views.api_recalculer_kpi,
# views.api_recalculer_table_calcul, inchangées, aucune régression). Ce
# qui change : le même calcul devient aussi appelable via la couche
# Action (permission + audit + lignée unifiés) — donc depuis l'agent et
# depuis la fiche objet Ontology (Phase 10, types_objet="kpi"/"calcul",
# déjà des types de nœuds existants dans ontologie.construire_graphe) sans
# dupliquer la logique de recalcul à un troisième endroit.
def _recalculer_kpi(kpi):
    valeur, erreur = fonctions.appeler("calculer_kpi", kpi)
    return {"valeur": valeur, "erreur": erreur}


declarer_action(
    "recalculer_kpi",
    tl("engine/actions.py:g:1004:5"),
    role_minimum="agent",
    types_objet=("kpi",),
)(_recalculer_kpi)


def _recalculer_table_calcul(table_calcul):
    """Retourne les lignes elles-mêmes (pas un résumé) : c'est ce que
    views.api_recalculer_table_calcul sérialise pour le front — aucun autre
    appelant à ce jour, donc rien d'autre à préserver."""
    return fonctions.appeler("recalculer_table_calcul", table_calcul)


declarer_action(
    "recalculer_table_calcul",
    tl("engine/actions.py:g:1019:5"),
    role_minimum="agent",
    types_objet=("calcul",),
)(_recalculer_table_calcul)


# ── Catalogue pour l'agent (Phase 5 — connexion Ontology/Actions) ──────────
# Introspection PURE des Actions déjà déclarées ci-dessus : aucune Action
# n'est réécrite ni dupliquée pour l'exposer à l'agent. Le premier paramètre
# de chaque fonction est toujours la "cible" (entreprise/table/membre — voir
# les appels executer_action(...) dans views.py) et n'est jamais demandé au
# LLM : la résolution de la cible reste la responsabilité de l'appelant
# (voir agent_ontology.py), exactement comme pour un appel direct depuis une
# vue. Ceci ne change RIEN au comportement existant des Actions.
def catalogue_pour_agent(membre, noms_inclus, type_objet=None) -> list[dict]:
    """Catalogue JSON-sérialisable des Actions dans `noms_inclus` que
    `membre` a le droit d'utiliser (même vérification que verifier_permission),
    avec leurs paramètres réels (hors cible). Utilisé pour construire le
    prompt de classification d'intention — jamais pour exécuter quoi que ce
    soit directement.

    `type_objet` (optionnel, ex. "table", "entreprise" — voir
    declarer_action(types_objet=...)) restreint en plus le catalogue aux
    Actions applicables à ce type de nœud Ontology : utilisé par
    views.api_ontologie_fiche (Phase 6) pour ne proposer, sur la fiche d'un
    objet, que les Actions pertinentes pour son type. Une Action non
    catégorisée (types_objet=None) reste toujours incluse, quel que soit
    `type_objet` — l'absence de déclaration n'exclut jamais une Action,
    elle ne fait que ne pas la filtrer. `type_objet=None` (défaut) désactive
    ce filtre et reproduit le comportement historique (aucun appelant
    existant ne le passe)."""
    catalogue = []
    for nom in noms_inclus:
        descripteur = _REGISTRE.get(nom)
        if descripteur is None or not _permission_autorisee(membre, descripteur):
            continue
        if type_objet is not None and descripteur.types_objet is not None and type_objet not in descripteur.types_objet:
            continue
        try:
            parametres = list(inspect.signature(descripteur.fonction).parameters)[1:]
        except (TypeError, ValueError):
            parametres = []
        catalogue.append({"nom": nom, "description": descripteur.description, "parametres": parametres})
    return catalogue
