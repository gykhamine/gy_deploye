"""
SKILLS — scripts d'analyse générés UNE FOIS par l'IA, persistés sur disque
dans media/skills/<table_slug>/<skill_id>.py, et ré-exécutables à tout moment.

Cycle :
  1. Clic "Analyser" → l'agent construit le prompt, force le modèle à générer
     un script structuré (titre, pourquoi, comment, stats, figures,
     explication, analyse prédictive, kpi, conclusion, recommandations).
  2. Le script est filtré, sauvegardé sur disque dans media/skills/.
  3. Un snapshot CSV de la table est exporté dans le même dossier.
  4. Le script est exécuté via l'exécuteur sécurisé (sandbox).
  5. Le résultat est stocké en JSON + les graphiques en base64 sur le Skill.

Deux tables ne se connaissent pas : chaque skill est isolé dans son dossier.
"""
from engine.i18n import t
from engine.i18n import td
from engine.i18n import tf
import csv
import io
import os
from datetime import datetime

from django.conf import settings

from .executor import executer_script_securise
from .inference import MoteurInference
from .metadata import contexte_pour_agent
from .models import Skill, TableDynamique
from .visualization import generer_graphiques


# ── Helpers ────────────────────────────────────────────────────────────────

def _dossier_skill(table: TableDynamique) -> str:
    """Dossier isolé par table dans media/skills/."""
    dossier = os.path.join(settings.MEDIA_ROOT, "skills", table.slug)
    os.makedirs(dossier, exist_ok=True)
    return dossier


def _exporter_csv_table(table: TableDynamique, dossier: str, skill_id) -> str:
    """Exporte un snapshot CSV de la table au moment de la génération du skill."""
    nom_fichier = f"table_{table.slug}_{skill_id}.csv"
    chemin = os.path.join(dossier, nom_fichier)
    colonnes = [c.slug for c in table.champs.all()]
    from . import parquet_store
    enregistrements = parquet_store.lignes_analytiques(table)
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colonnes)
        writer.writeheader()
        for ligne in enregistrements:
            writer.writerow({col: ligne.get(col, "") for col in colonnes})
    return chemin


# ── API publique ────────────────────────────────────────────────────────────

def generer_skill(table: TableDynamique, entreprise, utilisateur, nom: str = "", description: str = "") -> Skill:
    """
    Phase 1 : demande au modèle de générer le script d'analyse structuré.
    Sauvegarde le script sur disque. Ne l'exécute PAS encore.
    Retourne le Skill avec statut 'genere'.
    """
    contexte = contexte_pour_agent(table)
    nom_skill = nom or tf("engine/skills.py:g:62:24", v1=table.nom)

    # Construction du prompt : on demande explicitement la structure complète
    demande_texte = (
        tf("engine/skills.py:g:67:9", v1=table.nom, v2=table.description or t("engine/skills.py:g:68:77"), v3=table.prompt_rapport or t("rg.aucune"))
    )

    moteur = MoteurInference(entreprise=entreprise)
    script, erreur = moteur.generer(
        tache="skill_script",
        contexte_donnees=contexte,
        demande_utilisateur=demande_texte,
        mode="script",
        instructions_table=table.prompt_rapport,
        timeout=table.timeout_inference_secondes,
    )

    # Créer le Skill même en cas d'erreur (pour pouvoir régénérer)
    skill = Skill.objects.create(
        entreprise=entreprise,
        table=table,
        cree_par=utilisateur,
        nom=nom_skill,
        description=description,
        fichier_script="",
        statut="echec" if erreur else "genere",
        erreur=erreur or "",
    )

    if erreur:
        return skill

    # Sauvegarder le script sur disque
    dossier = _dossier_skill(table)
    nom_fichier = f"skill_{skill.id}.py"
    chemin_script = os.path.join(dossier, nom_fichier)
    with open(chemin_script, "w", encoding="utf-8") as f:
        f.write(f"# Skill : {nom_skill}\n")
        f.write(tf("engine/skills.py:g:102:17", v1=table.slug))
        f.write(tf("engine/skills.py:g:103:17", v1=datetime.now().isoformat()))
        f.write(script)

    skill.fichier_script = chemin_script
    skill.save(update_fields=["fichier_script", "statut", "erreur"])
    return skill


def executer_skill(skill: Skill) -> Skill:
    """
    Phase 2 : exécute le script sauvegardé sur disque via le sandbox,
    génère les graphiques, stocke tout sur le Skill.
    """
    if not skill.fichier_script or not os.path.exists(skill.fichier_script):
        skill.statut = "echec"
        skill.erreur = t("engine/skills.py:g:118:24")
        skill.save()
        return skill

    with open(skill.fichier_script, "r", encoding="utf-8") as f:
        code = f.read()

    # Exporter snapshot CSV de la table
    dossier = _dossier_skill(skill.table)
    chemin_csv = _exporter_csv_table(skill.table, dossier, skill.id)
    skill.fichier_csv_table = chemin_csv

    # Exécuter dans le sandbox
    from . import parquet_store
    donnees_dict = parquet_store.donnees_dict(skill.table)
    from .executor import parse_modules_autorises, DEFAULT_MODULES_AUTORISES_SCRIPT
    from .models import ConfigurationIA
    cfg = ConfigurationIA.objects.filter(entreprise=skill.table.entreprise).first()
    modules_autorises = parse_modules_autorises(
        cfg.modules_autorises_script if cfg else "", DEFAULT_MODULES_AUTORISES_SCRIPT
    )
    resultat = executer_script_securise(
        code, donnees_dict, timeout_secondes=skill.table.timeout_execution_secondes,
        modules_autorises=modules_autorises,
    )

    if not resultat.get("ok"):
        skill.statut = "echec"
        skill.erreur = resultat.get("erreur", td("engine/skills.py:g:144:47"))
        skill.save()
        return skill

    contenu = resultat["resultat"] or {}
    skill.resultat_json = contenu

    # Générer les graphiques
    graphiques = generer_graphiques(contenu.get("figures", []))
    skill.graphiques_json = [
        {"titre": g["titre"], "image_base64": g["image_base64"]}
        for g in graphiques
    ]

    skill.statut = "execute"
    skill.erreur = ""
    skill.execute_le = datetime.now()
    skill.save()
    return skill


def regenerer_skill(skill: Skill) -> Skill:
    """
    Supprime l'ancien script sur disque et régénère depuis l'IA.
    Utile si on veut modifier l'analyse ou si le script était défaillant.
    """
    # Supprimer l'ancien fichier
    if skill.fichier_script and os.path.exists(skill.fichier_script):
        try:
            os.remove(skill.fichier_script)
        except OSError:
            pass

    # Réinitialiser
    skill.fichier_script = ""
    skill.resultat_json = None
    skill.graphiques_json = None
    skill.statut = "genere"
    skill.erreur = ""
    skill.save()

    # Régénérer
    return generer_skill(
        table=skill.table,
        entreprise=skill.entreprise,
        utilisateur=skill.cree_par,
        nom=skill.nom,
        description=skill.description,
    )


def serialiser_skill(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "nom": skill.nom,
        "description": skill.description,
        "table_slug": skill.table.slug,
        "table_nom": skill.table.nom,
        "statut": skill.statut,
        "erreur": skill.erreur,
        "resultat": skill.resultat_json,
        "couleurs": skill.couleurs or {},
        "graphiques": [g["image_base64"] for g in (skill.graphiques_json or [])],
        "graphiques_legendes": [g.get("titre", "") for g in (skill.graphiques_json or [])],
        "fichier_script": skill.fichier_script,
        "fichier_csv_table": skill.fichier_csv_table,
        "cree_le": skill.cree_le.isoformat(),
        "execute_le": skill.execute_le.isoformat() if skill.execute_le else None,
    }
