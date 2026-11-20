"""GÉOSPATIAL — surveillance en arrière-plan : les alertes (rapprochement, collision prévue) deviennent des notifications.

Avant : les alertes n'existaient que dans le résultat de l'analyse affiché par la page ; changer d'onglet les faisait disparaître.
Maintenant : à chaque écriture de positions dans une table (commande `suivre_flux_positions`, import, envoi depuis la carte BI), `surveiller(table)`
relance l'analyse sur la fenêtre récente et notifie les agents/admin. Aucune page n'a besoin d'être ouverte.

Garde-fous :
  * au plus une évaluation par table et par DELAI_MIN_S secondes (le flux écrit souvent, l'analyse coûte) ;
  * une même alerte (même type, même paire) n'est renotifiée qu'après COOLDOWN_S, dédoublonnage lu en base (la commande et le serveur
    web sont deux processus : un cache en mémoire ne suffirait pas) ;
  * seules les alertes « critique » et « attention » sont notifiées ; ne jamais faire échouer l'écriture des positions (tout est capturé).
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf

import logging
import time
from datetime import timedelta

logger = logging.getLogger(__name__)

DELAI_MIN_S = 60.0
COOLDOWN_S = 3600.0
FENETRE_LIGNES = 300      # dernières lignes relues (le calcul reste borné quel que soit l'historique)
ECHANTILLONS = 100        # tirages Monte-Carlo (200 par défaut pour la page : ici on privilégie la vitesse)
_DERNIERE: dict = {}      # table_id -> instant (monotonic) de la dernière évaluation, par processus


def _paire(a, b) -> str:
    return " ↔ ".join(sorted((str(a), str(b))))


def formuler(alerte: dict, noms: dict) -> dict | None:
    """Alerte du moteur -> {"type", "titre", "message", "cle"} ; None si elle ne se notifie pas (info, autre type)."""
    t = alerte.get("type")
    if t not in ("rencontre", "collision_prevue") or alerte.get("gravite") not in ("critique", "attention"):
        return None
    a, b = noms.get(alerte["a"], alerte["a"]), noms.get(alerte.get("b"), alerte.get("b"))
    paire = _paire(a, b)
    if t == "rencontre":
        return {"type": "geo_rencontre", "titre": f"Rapprochement — {paire}",
                "message": tf("engine/geo_surveillance.py:g:41:28", v1=paire, v2=alerte['valeur']) + (_i18n_t("engine/geo_surveillance.py:g:41:95") if alerte.get("interpole") else "."),
                "cle": f"geo_rencontre|{paire}"}
    dans = alerte.get("dans_s")
    quand = tf("engine/geo_surveillance.py:g:44:13", v1=dans) if dans is not None else ""
    return {"type": "geo_collision", "titre": tf("engine/geo_surveillance.py:g:45:47", v1=paire),
            "message": tf("engine/geo_surveillance.py:g:46:24", v1=paire, v2=alerte['valeur'] * 100, v3=quand),
            "cle": f"geo_collision|{paire}"}


def a_notifier(alertes: list, noms: dict, deja_titres: set) -> list:
    """Filtre pur : formule, écarte les alertes déjà notifiées (titre présent dans `deja_titres`) et les doublons du lot, critiques d'abord."""
    vues, sortie = set(deja_titres), []
    ordre = sorted(alertes, key=lambda a: 0 if a.get("gravite") == "critique" else 1)
    for al in ordre:
        f = formuler(al, noms)
        if f is None or (f["type"], f["titre"]) in vues:
            continue
        vues.add((f["type"], f["titre"]))
        sortie.append(f)
    return sortie


REEVALUATION_S = 600.0    # même sans nouvelle donnée, une table est réévaluée au plus toutes les 10 min (le temps passe : une collision se rapproche)


def _etat_alertes(alertes: list, noms: dict) -> list:
    """Alertes de position gardées pour la page (déjà triées par le moteur : critiques d'abord) : de quoi les retrouver sur la carte."""
    sortie = []
    for a in sorted(alertes, key=lambda x: 0 if x.get("gravite") == "critique" else 1):
        f = formuler(a, noms)
        if f is None:
            continue
        sortie.append({"type": a["type"], "gravite": a["gravite"], "titre": f["titre"], "message": f["message"], "a": a["a"], "b": a.get("b"),
                       "lat": a.get("lat"), "lon": a.get("lon"), "t": a.get("t"), "dans_s": a.get("dans_s")})
    return sortie[:30]


def surveiller(table, force: bool = False) -> int:
    """Évalue `table` et notifie les nouvelles alertes. Renvoie le nombre de notifications créées (0 si rien, trop tôt, écartée ou erreur).
    Respecte les réglages de la veille (table écartée, objets écartés, paramètres d'analyse enregistrés depuis la page) et n'analyse que si les
    données ont changé (empreinte) ou si REEVALUATION_S s'est écoulé. L'état de la dernière évaluation est écrit pour la page."""
    try:
        maintenant = time.monotonic()
        if not force and maintenant - _DERNIERE.get(table.id, -1e9) < DELAI_MIN_S:
            return 0
        from django.utils import timezone
        from . import geo_donnees, geo_veille
        reg = geo_veille.reglages(table.id)
        if not reg["active"] and not force:
            return 0
        slugs = [c.slug for c in geo_donnees.colonnes_geo(table) if c.slug not in reg["exclus"]]
        if not slugs:
            return 0
        _DERNIERE[table.id] = maintenant
        empreinte = geo_donnees.empreinte_table(table, slugs, FENETRE_LIGNES)
        avant = geo_veille.etat(table.id) or {}
        if not force and avant.get("empreinte") == empreinte and avant.get("ts_epoch", 0) > time.time() - REEVALUATION_S:
            return 0
        from .models import Notification
        from .notifications import notifier_alertes_geo
        params = {"echantillons": ECHANTILLONS, **reg["parametres"]}
        res = geo_donnees.analyser_table(table, slugs, limite=FENETRE_LIGNES, **params)
        if not res or res.get("vide"):
            geo_veille.ecrire_etat(table.id, {"ts": timezone.now().isoformat(timespec="seconds"), "ts_epoch": time.time(), "empreinte": empreinte,
                                              "objets": 0, "positions": 0, "alertes": []})
            return 0
        noms = {o["id"]: o["nom"] for o in res["objets"]}
        geo_veille.ecrire_etat(table.id, {"ts": timezone.now().isoformat(timespec="seconds"), "ts_epoch": time.time(), "empreinte": empreinte,
                                          "objets": res["resume"]["objets"], "positions": res["resume"]["positions"], "alertes": _etat_alertes(res["alertes"], noms)})
        if not res.get("alertes"):
            return 0
        depuis = timezone.now() - timedelta(seconds=COOLDOWN_S)
        deja = {(n["type_notification"], n["titre"]) for n in Notification.objects.filter(
            entreprise=table.entreprise, table=table, cree_le__gte=depuis,
            type_notification__in=("geo_rencontre", "geo_collision")).values("type_notification", "titre")}
        nouvelles = a_notifier(res["alertes"], noms, deja)
        return len(notifier_alertes_geo(table, nouvelles))
    except Exception:   # la surveillance ne doit jamais bloquer l'écriture des positions
        logger.exception(_i18n_t("engine/geo_surveillance.py:g:120:26"), getattr(table, "id", "?"))
        return 0
