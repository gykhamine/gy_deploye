"""GÉOSPATIAL — veille continue : réglages et état par table, et processus unique qui surveille toutes les tables de positions.

Au démarrage du serveur (premier appel HTTP, voir middleware.EnsureEngineSchemaMiddleware) ou par `python manage.py veille_geospatiale`, UN processus
détaché surveille en arrière-plan toutes les tables qui portent des colonnes de position (voir geo_surveillance.surveiller). Il survit à la
fermeture de la page et à tout changement d'onglet ; une alerte devient une notification, et la page géospatiale relit l'état à l'ouverture.

Fichiers dans `<MEDIA_ROOT>/systeme/flux_actifs/` (dossier d'exécution hors git, aucune table ni migration) :
  veille.json            processus de veille {pid, demarre} ; veille.verrou : verrou tenu par le processus pendant toute sa vie (une seule veille) ;
  <table>.veille.json    réglages de la table {active, exclus: [slug de colonne écartée], parametres: {seuil_collision_m, ...}} ;
  <table>.etat.json      dernière évaluation {ts, empreinte, objets, positions, alertes: [...]}.
Une table est surveillée par défaut (active) : on peut l'écarter entièrement, ou écarter seulement certains objets (colonnes).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
try:
    from .stockage import dossier_systeme
except ImportError:   # module chargé hors du paquet (tests isolés) : engine/ est alors sur sys.path
    from stockage import dossier_systeme

DOSSIER = dossier_systeme("flux_actifs")
PARAMETRES_AUTORISES = ("seuil_collision_m", "horizon_pas", "sigma_mesure_m", "echantillons", "mode_positions")
REGLAGES_DEFAUT = {"active": True, "exclus": [], "parametres": {}}


def _chemin(table_id: int, suffixe: str) -> Path:
    return DOSSIER / f"{int(table_id)}.{suffixe}.json"


def _lire_json(chemin: Path):
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ecrire_json(chemin: Path, donnees) -> None:
    DOSSIER.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, chemin)   # remplacement atomique : la page ne lit jamais un fichier à moitié écrit


def reglages(table_id: int) -> dict:
    d = _lire_json(_chemin(table_id, "veille")) or {}
    return {"active": bool(d.get("active", True)), "exclus": [str(x) for x in d.get("exclus", [])],
            "parametres": {k: v for k, v in (d.get("parametres") or {}).items() if k in PARAMETRES_AUTORISES}}


def enregistrer_reglages(table_id: int, active=None, exclus=None, parametres=None) -> dict:
    r = reglages(table_id)
    if active is not None:
        r["active"] = bool(active)
    if exclus is not None:
        r["exclus"] = sorted({str(x) for x in exclus})
    if parametres is not None:
        r["parametres"] = {k: v for k, v in parametres.items() if k in PARAMETRES_AUTORISES}
    _ecrire_json(_chemin(table_id, "veille"), r)
    return r


def etat(table_id: int) -> dict | None:
    return _lire_json(_chemin(table_id, "etat"))


def ecrire_etat(table_id: int, donnees: dict) -> None:
    try:
        _ecrire_json(_chemin(table_id, "etat"), donnees)
    except OSError:
        pass   # l'état est un confort pour la page : jamais une raison de faire échouer la veille


# ── Processus de veille ─────────────────────────────────────────────────────
def processus() -> dict:
    """État de la veille : « actif » si une passe a eu lieu il y a moins de 5 minutes (la commande `veille_geospatiale` écrit sa dernière passe)."""
    d = _lire_json(DOSSIER / "veille.json") or {}
    derniere = d.get("derniere")
    actif = isinstance(derniere, (int, float)) and (time.time() - derniere) < 300
    return {"actif": bool(actif), "pid": None, "demarre": d.get("demarre")}


def declarer_processus(demarre=None) -> None:
    """Note la dernière passe de la veille (appelée par la commande `veille_geospatiale` à chaque passe)."""
    ancien = _lire_json(DOSSIER / "veille.json") or {}
    _ecrire_json(DOSSIER / "veille.json", {"derniere": time.time(), "demarre": demarre or ancien.get("demarre") or datetime.now().isoformat(timespec="seconds")})
