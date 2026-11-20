"""
AGENT CENTRAL — pipeline unique du module :
Collecter -> Comprendre -> Analyser -> Visualiser -> Generer un rapport -> Decider

Le modele IA n'est pas un moteur de calcul permanent : c'est un ingenieur
analytique a la demande. Il ecrit un script UNE FOIS pour une demande donnee.

PAS DE CACHE AUTOMATIQUE : contrairement a une ancienne version qui
dedoublonnait les scripts par "signature" (intention + structure de table),
chaque DemandeAgent a desormais SON PROPRE script, genere une seule fois puis
ecrit immediatement sur disque (media/scripts_agent/<table_slug>/), que
l'execution reussisse ou echoue ensuite. Le systeme ne le remplace jamais de
lui-meme : seule une action explicite de l'utilisateur le change (bouton
"Regenerer" -> nouvel appel IA, ou edition manuelle directe).
"""
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t
from engine.i18n import td
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf
import base64
import json
import logging
import math
import os
from datetime import datetime

from django.conf import settings

logger = logging.getLogger("gyplatform.agent")

from .executor import executer_script_securise
from .inference import MoteurInference
from .metadata import contexte_pour_agent, empreinte_donnees, schema_pour_executor
from .models import DemandeAgent, JournalAudit
from .reports import generer_rapport_pdf
from .visualization import generer_graphiques


# ── Contexte agent enrichi de l'ontologie de l'entreprise ──────────────────
# Le script genere ne portait jusqu'ici que sur table_cible (contexte_pour_agent) :
# l'IA ignorait les autres tables/relations de l'entreprise. On y ajoute
# l'aperçu ontologie (deja utilise pour generer_couche_ontologie, voir
# ontologie.contexte_pour_generation_couche) pour qu'elle sache traverser les
# relations existantes plutot que d'inventer
# des jointures. Ontology reste lecture seule ici : aucune ecriture, aucun
# contournement des permissions existantes.
def _contexte_agent_avec_ontologie(table, entreprise) -> str:
    from .ontologie import contexte_pour_generation_couche
    contexte_table = contexte_pour_agent(table)
    try:
        contexte_ontologie = contexte_pour_generation_couche(entreprise)
    except Exception:
        logger.warning(_i18n_t("engine/agent.py:g:50:24"), exc_info=True)
        return contexte_table
    return f"{contexte_table}\n\n{contexte_ontologie}"


# ── Sanitisation du résultat retourné par le script IA ─────────────────────

def _sanitiser_json(obj):
    """Convertit recursivement tout objet non serialisable en JSON en un type
    natif Python (dict/list/str/int/float/bool/None).
    Gere notamment les pandas.Series, DataFrame, numpy scalaires et Timestamp
    que le script IA peut accidentellement laisser dans son resultat."""
    # Types natifs JSON : retour immediat
    if obj is None or isinstance(obj, bool) or isinstance(obj, str):
        return obj
    if isinstance(obj, float):
        import math
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, int):
        return obj

    # Numpy — chaque groupe dans son propre try pour eviter les court-circuits
    try:
        import numpy as np
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            f = float(obj)
            return None if (math.isnan(f) or math.isinf(f)) else f
        if isinstance(obj, np.ndarray):
            return [_sanitiser_json(v) for v in obj.tolist()]
    except ImportError:
        pass

    # Pandas
    try:
        import pandas as pd
        if isinstance(obj, pd.Series):
            return [_sanitiser_json(v) for v in obj.tolist()]
        if isinstance(obj, pd.DataFrame):
            return [
                {str(k): _sanitiser_json(v) for k, v in row.items()}
                for row in obj.to_dict(orient="records")
            ]
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        try:
            if pd.isna(obj):
                return None
        except (TypeError, ValueError):
            pass
    except ImportError:
        pass

    # Recursion sur les conteneurs
    if isinstance(obj, dict):
        return {str(k): _sanitiser_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitiser_json(v) for v in obj]

    # Dernier recours
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return str(obj)


# ── Persistance sur disque : un dossier par table, comme pour les Skills ────

def _dossier_scripts_agent(table) -> str:
    """Dossier media/scripts_agent/<table_slug>/ — miroir de media/skills/."""
    dossier = os.path.join(settings.MEDIA_ROOT, "scripts_agent", table.slug)
    os.makedirs(dossier, exist_ok=True)
    return dossier


def _chemin_script(demande: DemandeAgent) -> str:
    return os.path.join(_dossier_scripts_agent(demande.table_cible), f"demande_{demande.id}.py")


def _chemin_resultat(demande: DemandeAgent) -> str:
    return os.path.join(_dossier_scripts_agent(demande.table_cible), f"resultat_{demande.id}.json")


def _ecrire_script_sur_disque(demande: DemandeAgent, code: str) -> str:
    """Ecrit (ou ecrase) le fichier .py de cette demande. Toujours le meme
    fichier pour une demande donnee : pas de multiplication de versions."""
    chemin = _chemin_script(demande)
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(tf("engine/agent.py:g:144:17", v1=demande.id))
        f.write(tf("engine/agent.py:g:145:17", v1=demande.table_cible.slug))
        f.write(f"# Intention : {demande.texte}\n")
        f.write(tf("engine/agent.py:g:147:17", v1=datetime.now().isoformat()))
        f.write(code)
    return chemin


def _ecrire_graphiques_sur_disque(demande: DemandeAgent, graphiques: list) -> list:
    """Sauvegarde chaque graphique en PNG a cote du script. Retourne la liste
    [{titre, fichier}] pour reference dans le fichier de resultat."""
    dossier = _dossier_scripts_agent(demande.table_cible)
    references = []
    for i, g in enumerate(graphiques):
        nom_fichier = f"demande_{demande.id}_graphique_{i}.png"
        chemin = os.path.join(dossier, nom_fichier)
        with open(chemin, "wb") as f:
            f.write(base64.b64decode(g["image_base64"]))
        references.append({"titre": g["titre"], "fichier": nom_fichier})
    return references


def _ecrire_resultat_sur_disque(demande: DemandeAgent, resultat: dict, graphiques_references: list) -> str:
    chemin = _chemin_resultat(demande)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump({
            "demande_id": demande.id,
            "intention": demande.texte,
            "resultat": resultat,
            "graphiques": graphiques_references,
            "calcule_le": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)
    return chemin


def sauvegarder_script_demande(demande: DemandeAgent) -> str:
    """Reecrit le fichier .py sur demande explicite (bouton 'Sauvegarder').
    Le fichier existe deja normalement (ecrit automatiquement a la generation) ;
    ceci force juste une resynchronisation si besoin."""
    if not demande.script_genere:
        raise ValueError(_i18n_t("engine/agent.py:g:181:26"))
    if demande.table_cible is None:
        raise ValueError(_i18n_t("engine/agent.py:g:183:26"))
    chemin = _ecrire_script_sur_disque(demande, demande.script_genere)
    demande.fichier_script = chemin
    demande.save(update_fields=["fichier_script"])
    return chemin


# ── Generation (UN SEUL appel IA par demande) ────────────────────────────────

def _tenter_operation_connue(demande: DemandeAgent) -> DemandeAgent | None:
    """Phase 5 (Ontology -> Actions, voir agent_ontology.py) : avant
    d'ecrire un script d'ANALYSE, verifie si la demande correspond a une
    OPERATION CONNUE deja cablee en Action. Si oui, court-circuite
    completement la generation de script (aucun appel IA supplementaire pour
    ecrire du code inutile) et retourne la demande mise a jour ; sinon
    retourne None et le pipeline d'analyse habituel s'applique, inchange."""
    from .models import MembreEntreprise
    membre = MembreEntreprise.objects.filter(
        entreprise=demande.entreprise, utilisateur=demande.utilisateur
    ).first()
    if membre is None:
        return None

    from . import agent_ontology
    try:
        match = agent_ontology.tenter_action(demande, membre)
    except Exception:
        logger.warning(_i18n_t("engine/agent.py:g:211:24"), exc_info=True)
        return None
    if match is None:
        return None

    if match["statut"] == "execute":
        demande.statut = "execute"
        demande.resultat_json = {
            "type": "action_executee", "action": match["nom"],
            "description": match["description"], "resume": match["resume"],
        }
    else:  # "proposee"
        demande.statut = "action_proposee"
        demande.resultat_json = {
            "type": "proposition_action", "nom": match["nom"],
            "description": match["description"], "parametres": match["parametres"],
            # Lot 2 (agent_ontology) : identité de la cible réelle (membre/
            # kpi/table_calcul/table, ou None pour une cible = entreprise),
            # RE-résolue par confirmer_et_executer au moment de la
            # confirmation — jamais un objet gardé tel quel entre deux
            # requêtes HTTP distinctes.
            "cible_type": match.get("cible_type"), "cible_id": match.get("cible_id"),
        }
    demande.script_genere = ""
    demande.erreur = ""
    demande.save()
    return demande


def generer_script_demande(demande: DemandeAgent) -> DemandeAgent:
    """PHASE 1 : demande a l'IA d'ecrire le script pour CETTE demande precise.
    Toujours un nouvel appel IA (pas de recherche de script existant ailleurs).
    Le script est ecrit sur disque immediatement, succes ou echec d'execution
    a venir n'y change rien : il reste la version de reference jusqu'a ce que
    l'utilisateur clique explicitement sur 'Regenerer' ou l'edite a la main.

    N'appelle PAS l'IA de generation de script si _tenter_operation_connue
    reconnait une operation deja cablee en Action (Phase 5) : cette demande
    n'a alors jamais besoin d'un script."""
    resultat_action = _tenter_operation_connue(demande)
    if resultat_action is not None:
        return resultat_action

    table = demande.table_cible
    if table is None:
        demande.statut = "echec"
        demande.erreur = _i18n_t("engine/agent.py:g:259:26")
        demande.save()
        return demande

    contexte = _contexte_agent_avec_ontologie(table, demande.entreprise)
    moteur = MoteurInference(entreprise=demande.entreprise)
    script, erreur = moteur.generer(
        tache="analysis_script",
        contexte_donnees=contexte,
        demande_utilisateur=demande.texte,
        mode="script",
        instructions_table=table.prompt_rapport,
        timeout=table.timeout_inference_secondes,
    )
    if erreur:
        demande.statut = "echec"
        demande.erreur = erreur
        demande.save()
        return demande

    demande.script_genere = script
    demande.modifie_manuellement = False
    demande.statut = "script_genere"
    demande.erreur = ""
    demande.save()

    demande.fichier_script = _ecrire_script_sur_disque(demande, script)
    demande.save(update_fields=["fichier_script"])
    return demande


def insister_sur_demande(demande: DemandeAgent) -> DemandeAgent:
    """Bouton 'Insister' : le script precedent a echoue (format non respecte ou
    erreur d'execution). Regenere depuis l'IA en injectant l'erreur exacte
    comme feedback. Action EXPLICITE de l'utilisateur -> ecrase le fichier de
    cette demande avec la version corrigee."""
    table = demande.table_cible
    if table is None:
        demande.statut = "echec"
        demande.erreur = _i18n_t("engine/agent.py:g:298:26")
        demande.save()
        return demande

    erreur_feedback = demande.erreur or _i18n_t("engine/agent.py:g:299:41")
    contexte = _contexte_agent_avec_ontologie(table, demande.entreprise)
    moteur = MoteurInference(entreprise=demande.entreprise)
    script, erreur = moteur.generer(
        tache="analysis_script",
        contexte_donnees=contexte,
        demande_utilisateur=demande.texte,
        mode="script",
        instructions_table=table.prompt_rapport,
        erreur_precedente=erreur_feedback,
        timeout=table.timeout_inference_secondes,
    )

    if erreur:
        demande.statut = "echec"
        demande.erreur = erreur
        demande.save()
        return demande

    demande.script_genere = script
    demande.modifie_manuellement = False
    demande.statut = "script_genere"
    demande.erreur = ""
    demande.save()

    demande.fichier_script = _ecrire_script_sur_disque(demande, script)
    demande.save(update_fields=["fichier_script"])
    return demande


def modifier_script_demande(demande: DemandeAgent, nouveau_code: str) -> DemandeAgent:
    """Edition manuelle directe : remplace le code de cette demande, l'ecrit
    sur disque, puis relance l'execution. Le code est TOUJOURS conserve, que
    l'execution qui suit reussisse ou echoue -- l'utilisateur peut continuer a
    l'ajuster sans jamais le perdre."""
    demande.script_genere = nouveau_code
    demande.modifie_manuellement = True
    demande.statut = "script_genere"
    demande.erreur = ""
    demande.save()
    demande.fichier_script = _ecrire_script_sur_disque(demande, nouveau_code)
    demande.save(update_fields=["fichier_script"])
    return executer_demande(demande)


# ── Execution (JAMAIS d'appel IA) ────────────────────────────────────────────

def executer_demande(demande: DemandeAgent) -> DemandeAgent:
    """PHASE 2, declenchee par 'Lancer' / 'Actualiser' / apres une edition
    manuelle : execute le script DEJA genere pour cette demande. N'appelle
    JAMAIS l'IA — c'est la meme demande, donc le meme script, execute a
    nouveau sur les donnees actuelles."""
    table = demande.table_cible
    if table is None or not demande.script_genere:
        demande.statut = "echec"
        demande.erreur = _i18n_t("engine/agent.py:g:357:26")
        demande.save()
        return demande

    # Lecture analytique : point d'entrée unique (fichier Parquet de la table ; le repli EAV est géré dans parquet_store).
    from . import parquet_store
    donnees_dict = parquet_store.donnees_dict(table)
    schema_eav = schema_pour_executor(table)
    from .executor import parse_modules_autorises, DEFAULT_MODULES_AUTORISES_SCRIPT
    from .models import ConfigurationIA
    cfg = ConfigurationIA.objects.filter(entreprise=table.entreprise).first()
    modules_autorises = parse_modules_autorises(
        cfg.modules_autorises_script if cfg else "", DEFAULT_MODULES_AUTORISES_SCRIPT
    )
    resultat = executer_script_securise(
        demande.script_genere, donnees_dict, schema_eav,
        timeout_secondes=table.timeout_execution_secondes,
        modules_autorises=modules_autorises,
    )

    if not resultat.get("ok"):
        demande.statut = "echec"
        demande.erreur = resultat.get("erreur", td("engine/agent.py:g:376:49"))
        demande.save()
        return demande

    contenu = _sanitiser_json(resultat["resultat"] or {})
    demande.resultat_json = contenu
    demande.statut = "execute"
    demande.erreur = ""
    demande.empreinte_donnees = empreinte_donnees(table)
    demande.save()

    # On repart d'une liste de graphiques propre a chaque (re)execution de
    # cette meme demande, pour ne pas accumuler les anciennes images.
    demande.graphiques.all().delete()
    graphiques = generer_graphiques(contenu.get("figures", []))
    for g in graphiques:
        demande.graphiques.create(image_base64=g["image_base64"], legende=g["titre"])

    graphiques_references = _ecrire_graphiques_sur_disque(demande, graphiques)
    demande.fichier_resultat = _ecrire_resultat_sur_disque(demande, contenu, graphiques_references)
    demande.save(update_fields=["fichier_resultat"])

    agent_utilise = ""
    if cfg is not None:
        agent_utilise = f"{cfg.moteur_type}:{cfg.moteur_modele}" if cfg.moteur_modele else cfg.moteur_type
    JournalAudit.enregistrer(
        entreprise=demande.entreprise,
        utilisateur=demande.utilisateur,
        action="demande_agent_executee",
        details={
            "demande_id": demande.id, "table": table.slug,
            "statut": demande.statut, "modifie_manuellement": demande.modifie_manuellement,
        },
        type_acteur="agent_ia", source="agent", cible_type="table", cible_id=table.id,
        agent_utilise=agent_utilise, fonction_utilisee="executer_script_securise",
        resultat="succes" if demande.statut == "execute" else "echec",
    )

    return demande


def generer_pdf_demande(demande: DemandeAgent):
    """Action explicite (bouton 'Generer le PDF' dans l'interface) : construit
    le PDF a partir du RESULTAT EXACT deja calcule (demande.resultat_json,
    tel quel, sans filtrer de cles) et des graphiques deja calcules. Ne
    repasse jamais par l'IA ni par l'execution du script -- et n'est jamais
    declenchee automatiquement : uniquement sur action de l'utilisateur."""
    if not demande.resultat_json:
        return None
    table = demande.table_cible
    graphiques = [
        {"titre": g.legende, "image_base64": g.image_base64} for g in demande.graphiques.all()
    ]
    return generer_rapport_pdf(
        demande=demande,
        titre=tf("engine/agent.py:g:429:15", v1=table.nom if table else ''),
        resultat=demande.resultat_json,
        graphiques=graphiques,
    )


# ═══════════════════════════════════════════════════════════════════════
# PIPELINE DOCUMENTS : factures, reçus, bons, devis, etc.
# Même architecture que DemandeAgent mais prompt et résultat orientés
# document formel. Le script pandas retourne un dict structuré :
#   type_document, titre, emetteur (dict), destinataire (dict),
#   reference (str), date (str), lignes (list[dict]),
#   totaux (dict), notes (str), pied_page (str)
# + une clé optionnelle "blocs" (composition libre : texte, image, tableau
# générique, graphique, section récursive, signature, cachet, saut_page —
# contrat complet dans inference.DEFAULT_REGLE_DOCUMENT) pour les documents
# dont la structure ne correspond pas au modèle facture/devis (bulletin,
# certificat, catalogue...). Si "blocs" est absent/vide, reports.py rend le
# document depuis les 10 clés classiques (comportement historique, fallback
# de sécurité) ; si "blocs" est fourni, reports.py le rend à la place.
# L'agent génère le PDF à partir de ce dict via reports.py.
# ═══════════════════════════════════════════════════════════════════════



def _ecrire_script_document(doc, code: str) -> str:
    """Écrit le script du document sur disque, retourne le chemin absolu."""
    import os
    table_slug = doc.table_cible.slug if doc.table_cible else "sans_table"
    dossier = os.path.join(settings.MEDIA_ROOT, "scripts_documents", table_slug)
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, f"document_{doc.id}.py")
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(code)
    return chemin



def _contexte_document(doc) -> str:
    """Schéma (contexte informatif) + données entité cible — le script doit rester
    générique : le schéma sert à COMPRENDRE la table, pas à écrire des slugs en dur."""
    from .metadata import schema_colonnes
    table = doc.table_cible
    enreg = doc.enregistrement_cible

    if not table:
        return _i18n_t("engine/agent.py:g:475:16")

    schema = schema_colonnes(table)
    lignes_schema = []
    for c in schema:
        ligne = f"  - {c['slug']} | type={c['type_eav']} | {c['description']}"
        if c.get("options"):
            ligne += f" | options={c['options']}"
        lignes_schema.append(ligne)

    bloc_schema = (
        tf("engine/agent.py:g:486:9", v1=table.slug, v2=table.description or '(aucune)') + (schema[0]['slug'] if schema else 'xxx') + _i18n_t("engine/agent.py:g:487:135")
        + "\n".join(lignes_schema)
    )

    if enreg:
        donnees = enreg.to_dict()
        donnees["id"] = enreg.id
        lignes_donnees = [f"  {k}: {repr(v)}" for k, v in donnees.items()]
        bloc_entite = (
            tf("engine/agent.py:g:499:13", v1=enreg.id)
            + "\n".join(lignes_donnees)
            + _i18n_t("engine/agent.py:g:498:15")
            + _i18n_t("engine/agent.py:g:499:15")
            + _i18n_t("engine/agent.py:g:500:15")
            + _i18n_t("engine/agent.py:g:501:15")
        )
    else:
        bloc_entite = (
            _i18n_t("engine/agent.py:g:505:13")
        )

    return bloc_schema + bloc_entite


def _contexte_mise_en_page_document(doc) -> str:
    """Décrit à l'IA la mise en page/branding configurés par l'entreprise
    pour le canal document (structure attendue, ce qui sera réellement
    affiché) — informatif : les 10 clés du dict restent obligatoires
    quelle que soit la mise en page, seul l'affichage PDF en tient compte."""
    from .models import ConfigurationIA
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=doc.entreprise)
    # Contexte 'ia' : ce prompt décrit exclusivement la mise en page de la
    # carte de document générée par le moteur IA — config indépendante des
    # exports de table et des reçus/liste employés (voir
    # ConfigurationIA.configuration_pour et reports.generer_pdf_document,
    # qui utilise le même contexte pour le rendu réel).
    cfg = cfg.configuration_pour("ia")

    nom_entreprise = cfg.nom_affichage_document or doc.entreprise.nom
    sections = cfg.sections_document_actives()
    libelles = cfg.SECTIONS_LIBELLES
    lignes_sections = [f"  - {s} ({libelles.get(s, s)})" for s in sections]

    bloc = (
        tf("engine/agent.py:g:533:9", v1=nom_entreprise, v2=_i18n_t("engine/agent.py:g:534:61") + cfg.position_logo_document + ')' if cfg.afficher_logo_document else _i18n_t("engine/agent.py:g:534:143"))
        + "\n".join(lignes_sections)
    )
    if cfg.afficher_photo_entite and cfg.image_entite:
        bloc += (
            tf("engine/agent.py:g:546:13", v1=cfg.position_photo_entite)
        )
    if cfg.afficher_cachet_document:
        bloc += (
            tf("engine/agent.py:g:551:13", v1=cfg.position_cachet_document, v2=cfg.cachet_texte_actif, v3=cfg.cachet_texte_inactif, v4=cfg.champ_statut_cachet)
        )
    if cfg.afficher_signatures_document:
        bloc += tf("engine/agent.py:g:556:17", v1=cfg.position_signatures_document)
    bloc += (
        _i18n_t("engine/agent.py:g:550:9")
    )
    return bloc


def generer_script_document(doc) -> "DocumentGenere":
    """Phase 1 : génère le script Python pour construire le document
    à partir d'une entité précise (une ligne de la table).
    """
    from .models import DocumentGenere
    from .inference import MoteurInference

    contexte = _contexte_document(doc) + "\n\n" + _contexte_mise_en_page_document(doc)

    params_str = ""
    if doc.parametres:
        lignes_params = [f"  - {k} : {v}" for k, v in doc.parametres.items()]
        params_str = _i18n_t("engine/agent.py:g:575:22") + "\n".join(lignes_params) + "\n"

    demande_complete = (
        tf("engine/agent.py:g:586:9", v1=doc.get_type_document_display(), v2=doc.titre or _i18n_t("engine/agent.py:g:570:91"), v3=params_str, v4=doc.instructions or _i18n_t("engine/agent.py:g:570:158"))
    )

    table = doc.table_cible
    timeout = getattr(table, "timeout_inference_secondes", 120) if table else 120

    moteur = MoteurInference(entreprise=doc.entreprise)
    script, erreur = moteur.generer(
        tache="document",
        contexte_donnees=contexte,
        demande_utilisateur=demande_complete,
        mode="document",
        instructions_table=getattr(table, "prompt_rapport_document", "") or "",
        timeout=timeout,
    )

    if erreur:
        doc.statut = "echec_generation"
        doc.erreur = erreur
        # Script IA vide/invalide : laisser script_genere tel quel (peut être vide)
        doc.save()
        return doc

    doc.script_genere = script
    doc.modifie_manuellement = False
    doc.statut = "script_genere"
    doc.erreur = ""
    doc.save()
    doc.fichier_script = _ecrire_script_document(doc, script)
    doc.save(update_fields=["fichier_script"])
    return doc


def executer_document(doc) -> "DocumentGenere":
    """Phase 2 : exécute executer(enregistrement) et génère le PDF.
    La fonction du script reçoit UN dict Python (une seule entité),
    pas un dataframe.
    """
    from datetime import datetime as _dt
    from .models import DocumentGenere
    from .reports import generer_pdf_document
    from .executor import executer_script_securise_dict

    table = doc.table_cible
    if not doc.script_genere:
        doc.statut = "echec_execution"
        doc.erreur = _i18n_t("engine/agent.py:g:616:22")
        doc.save()
        return doc

    # Construire le dict de l'entité cible
    enreg = doc.enregistrement_cible
    if enreg:
        donnees = enreg.to_dict()
        donnees["id"] = enreg.id
    else:
        donnees = dict(doc.parametres or {})

    timeout = table.timeout_execution_secondes if table else 30

    from .executor import parse_modules_autorises, DEFAULT_MODULES_AUTORISES_DOCUMENT
    from .models import ConfigurationIA
    cfg = ConfigurationIA.objects.filter(entreprise=doc.entreprise).first()
    modules_autorises = parse_modules_autorises(
        cfg.modules_autorises_document if cfg else "", DEFAULT_MODULES_AUTORISES_DOCUMENT
    )

    resultat_brut, erreur_exec = executer_script_securise_dict(
        doc.script_genere, donnees, timeout_secondes=timeout,
        modules_autorises=modules_autorises,
    )

    if erreur_exec:
        doc.statut = "echec_execution"
        doc.erreur = erreur_exec
        # IMPORTANT : script_genere est préservé — l'utilisateur peut le corriger
        doc.save()
        return doc

    # Sanitiser
    resultat = _sanitiser_json(resultat_brut)
    doc.resultat_json = resultat
    doc.execute_le = _dt.now()

    # Générer le PDF
    try:
        chemin_pdf = generer_pdf_document(doc, resultat)
        doc.fichier_pdf.name = chemin_pdf
    except Exception as exc:
        doc.statut = "echec_execution"
        doc.erreur = tf("engine/agent.py:g:660:22", v1=exc)
        # script_genere préservé
        doc.save()
        return doc

    doc.statut = "execute"
    doc.erreur = ""
    doc.save()
    return doc


def modifier_script_document(doc, nouveau_code: str):
    """Édition manuelle du script, puis re-exécution."""
    doc.script_genere = nouveau_code
    doc.modifie_manuellement = True
    doc.statut = "script_genere"
    doc.erreur = ""
    doc.save()
    doc.fichier_script = _ecrire_script_document(doc, nouveau_code)
    doc.save(update_fields=["fichier_script"])
    return executer_document(doc)


def regenerer_script_document(doc):
    """Régénère le script depuis l'IA (réinitialise les modifications)."""
    doc.modifie_manuellement = False
    doc.statut = "en_cours"
    doc.save()
    return generer_script_document(doc)


# ── Fonctions fusionnées depuis gscode ──

def _ecrire_script_tache(tache, code: str) -> str:
    """Écrit le script de la tâche dans un dossier PRIVÉ du compte du serveur. §étanchéité (audit 2026-11-20) : l'ancien dossier
    /tmp/gyplatform_taches (nom fixe, droits par défaut) pouvait être créé d'avance ou modifié par un autre compte de la machine,
    et son contenu était ensuite exécuté SANS bac à sable : le dossier doit appartenir à ce compte et n'être accessible qu'à lui."""
    import os, stat, tempfile
    dossier = os.path.join(tempfile.gettempdir(), f"gyplatform_taches_{os.getuid()}" if hasattr(os, "getuid") else "gyplatform_taches")
    os.makedirs(dossier, mode=0o700, exist_ok=True)
    info = os.lstat(dossier)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
        raise PermissionError("Dossier des scripts de tâches non sûr : il n'appartient pas au compte du serveur.")
    if info.st_mode & 0o077:
        os.chmod(dossier, 0o700)
    chemin = os.path.join(dossier, f"tache_{int(tache.id)}.py")
    descripteur = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descripteur, "w", encoding="utf-8") as f:
        f.write(code)
    return chemin



def generer_script_tache(tache):
    """Phase 1 : génère le script Python pour la tâche via l'IA.

    En mode conditionnel (mode_declenchement="condition"), le script est
    généré UNE SEULE FOIS puis réutilisé à chaque déclenchement (voir
    engine/declencheurs.py) : il doit donc être écrit pour recevoir la
    donnée précise à chaque exécution — via `executer(contexte)` — plutôt
    que de la coder en dur. On lui donne ici le schéma de la table
    surveillée (condition_table) pour qu'il sache quelles colonnes attendre
    une fois qu'il aura chargé l'Enregistrement désigné par
    contexte['enregistrement_id']."""
    from .models import TachePlanifiee
    from .inference import MoteurInference

    moteur = MoteurInference(entreprise=tache.entreprise)

    contexte = tf("engine/agent.py:g:739:16", v1=tache.nom or _i18n_t("engine/agent.py:g:720:63"))
    if tache.mode_declenchement == "condition":
        contexte += (
            tf("engine/agent.py:g:742:13", v1=tache.condition_categorie)
        )
        if tache.condition_table_id:
            contexte += _i18n_t("engine/agent.py:g:734:25")
            contexte += contexte_pour_agent(tache.condition_table)
        else:
            contexte += _i18n_t("engine/agent.py:g:737:25")

    # ── Modèles IA entraînés accessibles depuis le script ──────────────────
    from .models import ModeleIA
    from . import ia_integration as _ia
    modeles_entraine = ModeleIA.objects.filter(
        entreprise=tache.entreprise, statut="entraine"
    ).prefetch_related("tables_source")
    if modeles_entraine.exists():
        contexte += _i18n_t("engine/agent.py:g:746:21")
        for m in modeles_entraine:
            tables_noms = ", ".join(t.nom for t in m.tables_source.all())
            libelle = _ia.LIBELLES_TYPES.get(m.type_modele, m.type_modele)
            contexte += (
                f"  • id={m.id}  slug={m.slug}  nom={m.nom}  type={libelle}"
                f"  tables_source=[{tables_noms}]"
                f"  colonnes_entree={m.colonnes_entree}"
                f"  colonnes_sortie={m.colonnes_sortie or []}"
                f"  colonne_cible={m.colonne_cible or _i18n_t('rg.aucune')}\n"
            )
        contexte += (
            _i18n_t("engine/agent.py:g:758:13")
        )

    # ── Graphes d'ontologie et graphiques BI accessibles depuis le script ──
    contexte += (
        tf("engine/agent.py:g:767:9", v1=tache.entreprise_id)
    )

    demande = tache.instructions or _i18n_t("engine/agent.py:g:800:37")

    script, erreur = moteur.generer(
        tache="tache",
        contexte_donnees=contexte,
        demande_utilisateur=demande,
        mode="tache",
    )

    if erreur:
        tache.statut = "echec_generation"
        tache.erreur = erreur
        tache.save()
        return tache

    tache.script_genere = script
    tache.modifie_manuellement = False
    tache.statut = "script_genere"
    tache.erreur = ""
    tache.save()
    tache.fichier_script = _ecrire_script_tache(tache, script)
    tache.save(update_fields=["fichier_script"])
    return tache



def _emettre_agent_execute(tache, succes):
    """Événement "AgentExecuted" (voir evenements.py) : un événement par fin
    d'exécution de TachePlanifiee, succès ou échec — même moment que
    notifications.notifier_tache_terminee, aucune deuxième implémentation du
    suivi de fin d'exécution."""
    from . import evenements as _evenements
    _evenements.emettre(
        tache.entreprise_id, "AgentExecuted",
        table_id=tache.condition_table_id, tache_id=tache.id, succes=succes,
    )


def executer_tache(tache, contexte_evenement=None):
    """Phase 2 : exécute executer() directement (sans sandbox).

    `contexte_evenement` (dict, optionnel) : en mode conditionnel, l'id de
    l'enregistrement (et le cas échéant du champ) qui a déclenché CETTE
    exécution précise (voir engine/declencheurs.py) — permet au script de
    travailler sur la donnée réelle plutôt que sur la table entière. Passé à
    `executer(contexte)` si le script généré accepte un paramètre ; sinon
    (script écrit avant cette fonctionnalité, ou tâche planifiée classique)
    `executer()` est appelé sans argument, comme avant — rétro-compatible."""
    from datetime import datetime as _dt
    import importlib.util, inspect, sys, os

    if not tache.script_genere:
        tache.statut = "echec_execution"
        tache.erreur = _i18n_t("engine/agent.py:g:811:24")
        tache.save()
        from .notifications import notifier_tache_terminee
        notifier_tache_terminee(tache, succes=False, erreur=tache.erreur)
        _emettre_agent_execute(tache, succes=False)
        return tache

    # §étanchéité (audit 2026-11-20) : une tâche s'exécute SANS bac à sable ; seule une tâche définie (créée, importée, éditée ou
    # régénérée) par un super-administrateur Django actif est exécutée, quel que soit le chemin qui la déclenche (bouton,
    # planification, événement). Une tâche plus ancienne, créée par un autre compte, est refusée jusqu'à ce qu'un super-admin
    # relise son script (modifier / importer / régénérer) : elle devient alors la sienne.
    auteur = getattr(tache, "utilisateur", None)
    if not (auteur is not None and auteur.is_active and auteur.is_superuser):
        tache.statut = "echec_execution"
        tache.erreur = ("Tâche non exécutée : seul un script défini par un super-administrateur Django peut s'exécuter "
                        "(un super-administrateur doit relire et enregistrer son script).")
        tache.save()
        from .notifications import notifier_tache_terminee
        notifier_tache_terminee(tache, succes=False, erreur=tache.erreur)
        _emettre_agent_execute(tache, succes=False)
        return tache

    # Le script exécuté est TOUJOURS celui de la base (jamais un fichier laissé sur disque, qui aurait pu être modifié)
    tache.fichier_script = _ecrire_script_tache(tache, tache.script_genere)
    tache.save(update_fields=["fichier_script"])

    try:
        spec = importlib.util.spec_from_file_location(
            f"tache_{tache.id}", tache.fichier_script
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            accepte_contexte = bool(inspect.signature(module.executer).parameters)
        except (TypeError, ValueError):
            accepte_contexte = False
        resultat = module.executer(contexte_evenement) if accepte_contexte else module.executer()
    except Exception as exc:
        tache.statut = "echec_execution"
        tache.erreur = str(exc)
        tache.derniere_execution = _dt.now()
        tache.save()
        from .notifications import notifier_tache_terminee
        notifier_tache_terminee(tache, succes=False, erreur=tache.erreur)
        _emettre_agent_execute(tache, succes=False)
        return tache

    # Planifier la prochaine exécution
    prochaine = None
    if tache.intervalle_type != "unique" and tache.intervalle_valeur > 0:
        from datetime import timedelta
        delta_map = {
            "secondes": timedelta(seconds=tache.intervalle_valeur),
            "minutes":  timedelta(minutes=tache.intervalle_valeur),
            "heures":   timedelta(hours=tache.intervalle_valeur),
            "jours":    timedelta(days=tache.intervalle_valeur),
        }
        delta = delta_map.get(tache.intervalle_type)
        if delta:
            prochaine = _dt.now() + delta

    tache.resume = resultat.get("resume", "") if isinstance(resultat, dict) else str(resultat)
    tache.fichiers_sortie = resultat.get("fichiers", []) if isinstance(resultat, dict) else []
    tache.statut = "execute"
    tache.erreur = ""
    tache.derniere_execution = _dt.now()
    tache.prochaine_execution = prochaine
    tache.save()

    from .notifications import notifier_tache_terminee
    notifier_tache_terminee(tache, succes=True, resume=tache.resume)
    _emettre_agent_execute(tache, succes=True)

    # Reboucle réellement la tâche RÉPÉTITIVE (mode "planifie" avec un
    # intervalle) : `prochaine` n'était jusqu'ici qu'une date affichée, jamais
    # consommée par personne (aucun Celery beat, aucune commande de
    # planification) — la tâche s'arrêtait donc après sa première exécution
    # malgré un intervalle configuré. On reprogramme ici l'exécution suivante
    # via Celery (countdown), tant que la tâche reste active et planifiée.
    if prochaine and tache.mode_declenchement == "planifie" and tache.actif:
        from . import tasks as _taches
        delai = max(1, int((prochaine - _dt.now()).total_seconds()))
        _taches.executer_tache_planifiee_tache.apply_async((tache.id,), countdown=delai)

    return tache



def regenerer_script_tache(tache):
    """Régénère le script depuis l'IA."""
    tache.modifie_manuellement = False
    tache.statut = "en_attente"
    tache.save()
    return generer_script_tache(tache)
