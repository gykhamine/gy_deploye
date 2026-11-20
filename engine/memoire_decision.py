"""MÉMOIRE DE DÉCISION : la boucle « recommander, décider, mesurer, recommencer » stockée comme des données.

Trois tables ordinaires de la plateforme (TableDynamique, créées à la première utilisation, éditables et
analysables comme les autres, donc visibles des KPI, dashboards, ontologie et de l'IA) :
  « Recommandations stratégiques » : ce que le moteur a recommandé (verdict, option, probabilité, contexte JSON) ;
  « Décisions »                    : ce que l'utilisateur a choisi, relié à la recommandation ;
  « Mesures de décision »          : les conséquences mesurées (risque, activité, KPI, revenu), reliées à la décision.
Chaque ligne passe par la blockchain (Bloc « creation_ligne », comme une saisie manuelle) et les relations
alimentent l'ontologie (ChampDynamique de type relation + LienChamp). Les lignes de « Mesures » non encore
apprises nourrissent le MODÈLE FANTÔME (engine/modele_fantome.py) : c'est la table qui fait foi, pas une file cachée.

Tout est automatique et jamais fait par l'utilisateur : `entretenir()` (mesure ce qui est dû, construit les jeux
d'entraînement depuis toute la structure de la plateforme, puis UN pas de fine-tuning paresseux) est appelé par les vues de
recommandation, par la commande `modele_fantome` et, à chaque nouvelle table ou nouvelle ligne, par la tâche Celery
`engine.entretenir_modele_fantome` (planifiée par `planifier_entretien`, avec coalescence : un seul passage par minute).
Fichiers du modèle : <MEDIA_ROOT>/systeme/modeles_fantomes/entreprise_<id>.agentgy, puis entreprise_<id>_1.agentgy... (un réseau
de plus dès que le précédent atteint 10 000 paramètres ; ils se complètent).
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t
from engine.i18n import tl

import fcntl
import json
import logging
import os
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from . import donnees_entrainement as dn
from . import modele_fantome as mf
from . import reglages_criteres as _rc
from . import strategie
from .stockage import dossier_systeme

logger = logging.getLogger(__name__)

HORIZONS_JOURS = (7, 30)   # une mesure de conséquence par horizon écoulé
GRACE_JOURS = 1            # une mesure attend ce délai avant d'être apprise (le revenu peut être saisi entre-temps)
TABLES = {
    "reco": (tl("engine/memoire_decision.py:g:44:14"), [
        ("titre", tl("engine/memoire_decision.py:g:45:19"), "texte", None), ("verdict", "Verdict", "texte", None), ("option_recommandee", tl("engine/memoire_decision.py:g:45:106"), "texte", None),
        ("probabilite", tl("engine/memoire_decision.py:g:46:25"), "decimal", None), ("risque_global", "Risque global", "decimal", None),
        ("options", "Options", "json", None), ("contexte", "Contexte", "json", None), ("date", tl("engine/memoire_decision.py:g:47:96"), "datetime", None)]),
    "decision": (tl("engine/memoire_decision.py:g:48:18"), [
        ("titre", tl("engine/memoire_decision.py:g:49:19"), "texte", None), ("option_choisie", "Option choisie", "texte", None), ("suit_reco", tl("engine/memoire_decision.py:g:49:111"), "nombre", None),
        ("reco_id", "Recommandation (identifiant)", "nombre", None), ("recommandation", "Recommandation", "relation", "reco"),
        ("date", tl("engine/memoire_decision.py:g:51:18"), "datetime", None)]),
    "mesure": (tl("engine/memoire_decision.py:g:52:16"), [
        ("titre", tl("engine/memoire_decision.py:g:53:19"), "texte", None), ("decision_id", tl("engine/memoire_decision.py:g:53:60"), "nombre", None), ("decision", tl("engine/memoire_decision.py:g:53:116"), "relation", "decision"),
        ("horizon_jours", tl("engine/memoire_decision.py:g:54:27"), "nombre", None), ("risque_avant", "Risque avant", "decimal", None), ("risque_apres", tl("engine/memoire_decision.py:g:54:131"), "decimal", None),
        ("apres", tl("engine/memoire_decision.py:g:55:19"), "json", None), ("revenu", "Revenu / rendu", "decimal", None), ("recompense", tl("engine/memoire_decision.py:g:55:110"), "decimal", None),
        ("apprise", tl("engine/memoire_decision.py:g:56:21"), "nombre", None), ("date", tl("engine/memoire_decision.py:g:56:89"), "datetime", None)]),
    # Créée seulement à la première simulation géospatiale (engine/geo_donnees.py) : jamais avec les trois tables de décision.
    "geo": (tl("engine/memoire_decision.py:g:58:13"), [
        ("titre", tl("engine/memoire_decision.py:g:59:19"), "texte", None), ("table_source", tl("engine/memoire_decision.py:g:59:61"), "texte", None), ("nb_objets", "Objets suivis", "nombre", None),
        ("nb_positions", "Positions", "nombre", None), ("distance_totale_m", "Distance totale (m)", "decimal", None),
        ("vitesse_moyenne_ms", tl("engine/memoire_decision.py:g:61:32"), "decimal", None), ("nb_rencontres", tl("engine/memoire_decision.py:g:61:93"), "nombre", None),
        ("nb_collisions_prevues", tl("engine/memoire_decision.py:g:62:35"), "nombre", None), ("proba_collision_max", tl("engine/memoire_decision.py:g:62:98"), "decimal", None),
        ("erreur_prevision_m", tl("engine/memoire_decision.py:g:63:32"), "decimal", None), ("gain_prevision", tl("engine/memoire_decision.py:g:63:96"), "decimal", None),
        ("indice_clark_evans", tl("engine/memoire_decision.py:g:64:32"), "decimal", None), ("parametres", tl("engine/memoire_decision.py:g:64:90"), "json", None),
        ("resume", tl("engine/memoire_decision.py:g:65:20"), "json", None), ("date", tl("engine/memoire_decision.py:g:65:54"), "datetime", None)]),
    # Créée à la première annotation posée sur la carte (engine/geo_annotations.py) : points, trajectoires et zones tracés par l'utilisateur.
    "carte": ("Annotations cartographiques", [
        ("titre", tl("engine/memoire_decision.py:g:68:19"), "texte", None), ("type", "Type (point, trajectoire, zone)", "texte", None), ("table_id", tl("engine/memoire_decision.py:g:68:117"), "nombre", None),
        ("geometrie", tl("engine/memoire_decision.py:g:69:23"), "json", None), ("couleur", "Couleur", "texte", None), ("note", "Note", "texte_long", None),
        ("longueur_m", tl("engine/memoire_decision.py:g:70:24"), "decimal", None), ("surface_m2", "Surface (m²)", "decimal", None), ("date", tl("engine/memoire_decision.py:g:70:129"), "datetime", None),
        ("affiliation", "Affiliation (symbologie)", "texte", None), ("dimension", "Dimension (symbologie)", "texte", None), ("echelon", tl("engine/memoire_decision.py:g:71:137"), "texte", None),
        ("nature", "Nature (strategique, danger, contexte)", "texte", None), ("rayon_m", tl("engine/memoire_decision.py:g:72:90"), "decimal", None), ("alerte", tl("engine/memoire_decision.py:g:72:141"), "texte", None),
        ("origine", tl("engine/memoire_decision.py:g:73:21"), "texte", None)]),
    # Créée à la première tournée enregistrée (engine/geo_donnees.enregistrer_plan).
    "log": ("Plans logistiques", [
        ("titre", tl("engine/memoire_decision.py:g:76:19"), "texte", None), ("table_source", tl("engine/memoire_decision.py:g:76:61"), "texte", None), ("table_commandes", tl("engine/memoire_decision.py:g:76:118"), "texte", None),
        ("nb_commandes", "Commandes", "nombre", None), ("nb_vehicules", tl("engine/memoire_decision.py:g:77:73"), "nombre", None),
        ("distance_totale_m", "Distance totale (m)", "decimal", None), ("duree_max_s", tl("engine/memoire_decision.py:g:78:88"), "decimal", None),
        ("retard_total_s", tl("engine/memoire_decision.py:g:79:28"), "decimal", None), ("nb_retards", tl("engine/memoire_decision.py:g:79:82"), "nombre", None),
        ("nb_non_affectees", tl("engine/memoire_decision.py:g:80:30"), "nombre", None), ("utilisation", tl("engine/memoire_decision.py:g:80:90"), "decimal", None),
        ("gain_pct", tl("engine/memoire_decision.py:g:81:22"), "decimal", None), ("gain_depot", tl("engine/memoire_decision.py:g:81:82"), "decimal", None),
        ("parametres", tl("engine/memoire_decision.py:g:82:24"), "json", None), ("resume", tl("engine/memoire_decision.py:g:82:64"), "json", None), ("date", tl("engine/memoire_decision.py:g:82:98"), "datetime", None)]),
}
CLES_DECISION = ("reco", "decision", "mesure")


# ── Tables et lignes (réutilise le modèle EAV, le lien de relation et la blockchain existants) ──
def _tables(entreprise, cles=CLES_DECISION) -> dict:
    """Les tables demandées (par défaut les trois de la décision) de l'entreprise, créées si absentes. Retour : {cle: (table, {slug: champ})}."""
    from .models import ChampDynamique, TableDynamique
    sortie = {}
    for cle, (nom, colonnes) in TABLES.items():
        if cle not in cles:
            continue
        table = TableDynamique.objects.filter(entreprise=entreprise, slug=slugify(nom)).first()
        if table is None:
            table = TableDynamique.objects.create(
                entreprise=entreprise, nom=nom, slug=slugify(nom),
                description=td("engine/memoire_decision.py:g:101:29"))
            for ordre, (slug, libelle, type_champ, relie) in enumerate(colonnes):
                ChampDynamique.objects.create(
                    table=table, nom=libelle, slug=slug, type_champ=type_champ, obligatoire=False, ordre=ordre,
                    relation_vers=sortie[relie][0] if relie else None, relation_colonne_source="titre" if relie else "")
        elif cle == "carte":   # colonnes ajoutées depuis la création de la table (symbologie, nature et alerte des annotations) : champs ordinaires, jamais de migration
            presents = set(table.champs.values_list("slug", flat=True))
            for ordre, (slug, libelle, type_champ, relie) in enumerate(colonnes):
                if slug not in presents and not relie:
                    ChampDynamique.objects.create(table=table, nom=libelle, slug=slug, type_champ=type_champ, obligatoire=False, ordre=ordre)
        sortie[cle] = (table, {c.slug: c for c in table.champs.all()})
    return sortie


def _ajouter_ligne(entreprise, utilisateur, cle: str, valeurs: dict, liens: dict | None = None):
    """Crée une ligne (valeurs par slug ; liens = {slug_relation: id de la ligne source}), la journalise dans la
    blockchain exactement comme une saisie manuelle, puis renvoie l'Enregistrement."""
    from .models import Bloc, Enregistrement, ValeurChamp
    table, champs = _tables(entreprise, (cle,) if cle not in CLES_DECISION else CLES_DECISION)[cle]
    with transaction.atomic():
        enr = Enregistrement.objects.create(table=table, cree_par=utilisateur)
        for slug, valeur in {**valeurs, **{s: v for s, v in (liens or {}).items()}}.items():
            if slug in champs and valeur is not None:
                texte = json.dumps(valeur, ensure_ascii=False) if isinstance(valeur, (dict, list)) else str(valeur)
                vc = ValeurChamp.objects.create(enregistrement=enr, champ=champs[slug], valeur=texte)
                if champs[slug].type_champ == "relation" and valeur:
                    _lier(vc, champs[slug], valeur)
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "creation_ligne", "table_id": table.id, "table": table.nom, "enregistrement_id": enr.id,
            "agent_id": getattr(utilisateur, "id", None), "donnees": enr.to_dict_lisible()})
    from . import parquet_store
    parquet_store.planifier_synchro(table)   # fichier Parquet de la table (aucun effet pour une table NoSQL)
    return enr


def _lier(valeur_chp, champ, source_id) -> None:
    """Lien d'ontologie (LienChamp) : même fonction que la saisie manuelle, jamais une seconde implémentation."""
    try:
        from .views import _creer_lien_champ
        _creer_lien_champ(valeur_chp, champ, source_id)
    except Exception:
        logger.exception(t("engine/memoire_decision.py:g:139:26"), champ.slug, source_id)


def _lignes(entreprise, cle: str) -> list[dict]:
    """Lignes d'une des trois tables : [{_id, cree_le, **valeurs par slug}] (vide si la table n'existe pas encore)."""
    from .models import Enregistrement, TableDynamique
    nom = TABLES[cle][0]
    table = TableDynamique.objects.filter(entreprise=entreprise, slug=slugify(nom)).first()
    if table is None:
        return []
    from . import parquet_store   # valeurs lues dans le fichier Parquet (une lecture) au lieu d'une requête EAV par ligne
    enrs = list(Enregistrement.objects.filter(table=table).order_by("id"))
    return [{**v, "_id": e.id, "_cree_le": e.cree_le} for e, v in zip(enrs, parquet_store.valeurs_lignes(table, enrs))]


def _json(valeur, defaut):
    """Colonne JSON lue soit en texte, soit déjà décodée par to_dict() : les deux formes sont acceptées."""
    if isinstance(valeur, (dict, list)):
        return valeur
    try:
        return json.loads(valeur) if valeur else defaut
    except (TypeError, ValueError):
        return defaut


def _nombre(valeur):
    try:
        return float(str(valeur).replace(",", ".")) if valeur not in (None, "") else None
    except ValueError:
        return None


# ── Enregistrer la recommandation, puis le choix ────────────────────────────
def enregistrer_recommandation(entreprise, utilisateur, sortie: dict) -> int:
    """La recommandation stratégique EST une donnée. `sortie` = résultat de strategie.arbitrer (avec _trace)."""
    enr = _ajouter_ligne(entreprise, utilisateur, "reco", {
        "titre": f"{sortie['recommandation'] or 'Attendre'} ({timezone.now():%Y-%m-%d %H:%M})",
        "verdict": sortie["verdict"], "option_recommandee": sortie["recommandation"] or "", "probabilite": sortie["probabilite"],
        "risque_global": sortie["situation"]["risque_global"],
        "options": [{k: o[k] for k in ("nom", "levier", "effort", "cibles", "proba_premiere", "utilite")} for o in sortie["options"]],
        "contexte": {"trace": sortie["_trace"], "situation": sortie["situation"], "attendre": sortie["attendre"]},
        "date": timezone.now().isoformat()})
    return enr.id


def enregistrer_choix(entreprise, utilisateur, reco_id: int, option: str):
    """La décision de l'utilisateur (peut différer de la recommandation). None si la recommandation ou l'option est inconnue,
    ou si cette recommandation a déjà été tranchée (une décision par recommandation)."""
    reco = next((r for r in _lignes(entreprise, "reco") if r["_id"] == reco_id), None)
    if reco is None or option not in _json(reco.get("contexte"), {}).get("trace", {}):
        return None
    if any(_nombre(d.get("reco_id")) == reco_id for d in _lignes(entreprise, "decision")):
        return None
    return _ajouter_ligne(entreprise, utilisateur, "decision", {
        "titre": f"{option} ({timezone.now():%Y-%m-%d %H:%M})", "option_choisie": option,
        "suit_reco": 1 if option == reco.get("option_recommandee") else 0, "reco_id": reco_id,
        "date": timezone.now().isoformat()}, {"recommandation": reco_id})


# ── Mesurer les conséquences (automatique) ──────────────────────────────────
def mesurer_dues(entreprise, etat: dict, graphe: dict, resultat_reco: dict, maintenant=None) -> int:
    """Pour chaque décision dont un horizon est écoulé sans mesure : photographie « après » des mêmes objets, enregistrée
    comme ligne de « Mesures de décision » (non apprise). Rend le nombre de mesures créées."""
    maintenant = maintenant or timezone.now()
    recos = {r["_id"]: r for r in _lignes(entreprise, "reco")}
    faites = {(_nombre(m.get("decision_id")), _nombre(m.get("horizon_jours"))) for m in _lignes(entreprise, "mesure")}
    creees = 0
    for d in _lignes(entreprise, "decision"):
        reco = recos.get(_nombre(d.get("reco_id")))
        trace = _json((reco or {}).get("contexte"), {}).get("trace", {}).get(d.get("option_choisie"))
        if not trace:
            continue
        for horizon in HORIZONS_JOURS:
            if (float(d["_id"]), float(horizon)) in faites or maintenant < d["_cree_le"] + timedelta(days=horizon):
                continue
            apres = strategie.instantane(trace["cibles"], etat, graphe, resultat_reco)
            _ajouter_ligne(entreprise, None, "mesure", {
                "titre": f"{d.get('option_choisie')} à {horizon} j", "decision_id": d["_id"], "horizon_jours": horizon,
                "risque_avant": trace["avant"].get("risque"), "risque_apres": apres.get("risque"), "apres": apres,
                "apprise": 0, "date": maintenant.isoformat()}, {"decision": d["_id"]})
            creees += 1
            try:   # notification meilleur-effort : ne bloque jamais la mesure
                from . import notifications
                notifications.notifier_decision_mesuree(entreprise, d.get("option_choisie"), horizon, trace["avant"].get("risque"), apres.get("risque"))
            except Exception:
                logger.exception(t("engine/memoire_decision.py:g:222:34"), entreprise.id)
    return creees


# ── Modèle fantôme : fichiers .agentgy et entraînement paresseux ────────────
COALESCENCE_S = 65   # une nouvelle donnée déclenche UN passage, 65 s plus tard, qui absorbe tout ce qui est arrivé entre-temps


def _base(entreprise) -> str:
    return str(dossier_systeme("modeles_fantomes", f"entreprise_{entreprise.id}"))


def _chemin(entreprise) -> str:
    return mf.chemin_coeur(_base(entreprise), 0)


def _cle() -> bytes:
    return str(settings.SECRET_KEY).encode("utf-8")


def reglages_criteres(entreprise) -> dict:
    """Réglages de mesure des critères de l'entreprise (engine/reglages_criteres.py) : seules les valeurs valides ; {} = défauts."""
    from .models import ConfigurationIA
    cfg = ConfigurationIA.objects.filter(entreprise=entreprise).only("reglages_criteres").first()
    return _rc.normaliser(cfg.reglages_criteres if cfg else None)[0]


def charger_modele(entreprise) -> mf.Modele:
    """Modèle de l'entreprise : ses réseaux lus depuis les fichiers .agentgy ; un noyau neuf s'il n'existe pas ; un fichier
    altéré est mis de côté (renommé .invalide, avec les réseaux qui en dépendent), jamais chargé."""
    return mf.charger(_base(entreprise), _cle(), time.time(), _rc.parametres_modele(reglages_criteres(entreprise)))


def planifier_entretien(entreprise_id) -> None:
    """Appelé à chaque nouvelle table ou nouvelle ligne (signal) : planifie un passage de fine-tuning en tâche de fond.
    Coalescence : tant qu'un passage est planifié, les nouvelles données n'en planifient pas d'autre."""
    from django.core.cache import cache
    try:
        if not entreprise_id or not cache.add(f"modele_fantome_planifie:{entreprise_id}", 1, COALESCENCE_S + 30):
            return
        from . import tasks
        transaction.on_commit(lambda: tasks.entretenir_modele_fantome.apply_async(args=[entreprise_id], countdown=COALESCENCE_S))
    except Exception:
        logger.exception(t("engine/memoire_decision.py:g:265:26"), entreprise_id)


def _observations(entreprise) -> list[dict]:
    """Mesures à apprendre : [{ligne, phi, avant, apres, revenu}] (celles dont le délai de grâce est écoulé, ou qui ont un revenu)."""
    recos = {r["_id"]: r for r in _lignes(entreprise, "reco")}
    decisions = {d["_id"]: d for d in _lignes(entreprise, "decision")}
    maintenant, sorties = timezone.now(), []
    for m in _lignes(entreprise, "mesure"):
        if _nombre(m.get("apprise")) != 0:
            continue
        revenu = _nombre(m.get("revenu"))
        if revenu is None and maintenant < m["_cree_le"] + timedelta(days=GRACE_JOURS):
            continue
        d = decisions.get(_nombre(m.get("decision_id")))
        trace = _json((recos.get(_nombre((d or {}).get("reco_id"))) or {}).get("contexte"), {}).get("trace", {}).get((d or {}).get("option_choisie"))
        if trace:
            x = dn.completer(trace["x"]) if trace.get("x") else dn.x_depuis_phi(trace.get("phi") or [])   # traces d'avant le réseau : φ seul, structure à zéro
            sorties.append({"ligne": m["_id"], "cle": f"m{m['_id']}", "x": x, "avant": trace["avant"], "apres": _json(m.get("apres"), {}), "revenu": revenu})
        else:  # décision ou recommandation disparue : rien à apprendre, on ne la reprend pas à chaque passage
            sorties.append({"ligne": m["_id"], "cle": f"m{m['_id']}", "x": [], "avant": {}, "apres": {}, "revenu": None, "orpheline": True})
    return sorties


def _marquer(champ_valeurs: dict, ligne_id: int, valeurs: dict) -> None:
    from .models import ValeurChamp
    for slug, valeur in valeurs.items():
        ValeurChamp.objects.update_or_create(enregistrement_id=ligne_id, champ=champ_valeurs[slug], defaults={"valeur": str(valeur)})


class _Verrou:
    """Verrou de fichier (un seul entraînement à la fois par entreprise, même entre processus)."""

    def __init__(self, base: str):
        os.makedirs(os.path.dirname(base), exist_ok=True)
        self.fichier = open(base + ".verrou", "a")

    def __enter__(self):
        try:
            fcntl.flock(self.fichier, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fichier.close()
            return None
        return self

    def __exit__(self, *_exc):
        if not self.fichier.closed:
            fcntl.flock(self.fichier, fcntl.LOCK_UN)
            self.fichier.close()


def entretenir(entreprise, etat: dict, graphe: dict, resultat_reco: dict) -> dict:
    """Passage automatique : mesure les conséquences dues, construit les jeux d'entraînement depuis toute la structure
    (tables, KPI, ontologie, modèles IA, décisions), puis UN pas de fine-tuning paresseux du réseau. Jamais bloquant (les
    erreurs sont journalisées) et jamais visible de l'interface. Rend un petit bilan (pour la commande de maintenance)."""
    from django.core.cache import cache
    bilan = {"mesures_creees": 0, "observations": 0}
    if not cache.add(f"memoire_decision_entretien:{entreprise.id}", 1, 60):  # au plus un entretien par minute
        return bilan
    try:   # recommandations passées à « à lancer » -> notifications (jamais bloquant, dédoublonné)
        from . import notifications
        bilan["recos_notifiees"] = notifications.synchroniser_recommandations(entreprise, etat, graphe, resultat_reco)
    except Exception:
        logger.exception(t("engine/memoire_decision.py:g:328:26"), entreprise.id)
    try:   # le modèle prépare l'interface (graphiques, KPI, pages, tables de formules) et l'annonce par recommandation
        from . import fantome_construction
        bilan["construction"] = fantome_construction.passer(entreprise, etat)
    except Exception:
        logger.exception(t("engine/memoire_decision.py:g:333:26"), entreprise.id)
    try:
        with _Verrou(_base(entreprise)) as verrou:
            if verrou is None:   # un autre processus entraîne déjà ce modèle
                return bilan
            bilan["mesures_creees"] = mesurer_dues(entreprise, etat, graphe, resultat_reco)
            modele, maintenant = charger_modele(entreprise), time.time()
            a_apprendre = _observations(entreprise)
            utiles = [o for o in a_apprendre if not o.get("orpheline")]
            absorbees = mf.entrainer_pas(modele, a_apprendre, dn.Vue(etat, graphe, _rc.echelles(reglages_criteres(entreprise))), maintenant)
            mf.sauvegarder(modele, _base(entreprise), _cle())
            if absorbees:
                _, champs = _tables(entreprise)["mesure"]
                for o in absorbees:
                    _marquer(champs, o["ligne"], {"apprise": 1, **({"recompense": round(o["recompense"], 4)} if o.get("recompense") is not None else {})})
            bilan["observations"] = sum(1 for o in absorbees if o.get("recompense") is not None)
            bilan["en_attente"] = max(0, len(utiles) - bilan["observations"])
            bilan["modele"] = modele.resume()
    except Exception:
        logger.exception(t("engine/memoire_decision.py:g:352:26"), entreprise.id)
    return bilan


def etat_modele(entreprise) -> dict:
    """Résumé pour la commande de maintenance (jamais exposé à l'interface) : réseaux, paramètres, échantillons appris."""
    modele = charger_modele(entreprise)
    return {"fichier": _chemin(entreprise), **modele.resume(), "parametres_total": modele.nb_parametres(),
            "echantillons_appris": modele.actif.meta["n_abs"] + sum(c.meta["n_abs"] for c in modele.coeurs[:-1])}
