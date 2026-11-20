"""PARTAGE DE POSITION : une personne (livreur, agent) envoie SA position, qui est écrite dans une table de positions (module PUR, aucun import Django).

Une table de positions range une colonne « position » PAR OBJET (ici par personne) et une ligne par instant (engine/geo_flux.py) : la position partagée devient
donc la dernière valeur de la colonne de cette personne, créée au premier envoi. Les autres personnes, l'itinéraire et la carte la relisent ensuite comme
n'importe quel objet suivi de la table. Ce module ne fait que valider ce que le téléphone envoie et le mettre sous la forme JSON que le décodeur de flux
(engine/flux_positions.py) sait lire ; l'écriture, les droits et la limite de débit restent dans engine/views_geo.py.
"""
from __future__ import annotations
from engine.i18n import t

import json
import math

LONGUEUR_ID = 60


class ErreurPartage(ValueError):
    """Envoi refusé : message prêt à afficher."""


def construire(donnees) -> dict:
    """{id, nom?, lat, lon, precision_m?, cap?, vitesse_kmh?} -> {id, nom, lat, lon, texte} (texte = JSON du flux). Lève ErreurPartage si l'envoi est inexploitable."""
    if not isinstance(donnees, dict):
        raise ErreurPartage(t("engine/geo_partage.py:g:23:29"))
    ident = " ".join(str(donnees.get("id") or "").split())[:LONGUEUR_ID]
    if not ident:
        raise ErreurPartage(t("engine/geo_partage.py:g:26:29"))
    try:
        lat, lon = float(donnees.get("lat")), float(donnees.get("lon"))
    except (TypeError, ValueError):
        raise ErreurPartage(t("engine/geo_partage.py:g:30:29"))
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErreurPartage(t("engine/geo_partage.py:g:32:29"))
    precision = donnees.get("precision_m")
    try:
        precision = float(precision) if precision is not None else None
    except (TypeError, ValueError):
        precision = None
    if precision is not None and precision > 200:
        raise ErreurPartage(t("engine/geo_partage.py:g:39:29"))
    nom = " ".join(str(donnees.get("nom") or ident).split())[:LONGUEUR_ID]
    corps = {"id": ident, "nom": nom, "lat": round(lat, 6), "lon": round(lon, 6)}
    for cle_source, cle in (("cap", "cap"), ("vitesse_kmh", "vitesse_kmh")):
        try:
            v = float(donnees.get(cle_source))
            if math.isfinite(v):
                corps[cle] = round(v, 1)
        except (TypeError, ValueError):
            pass
    return {"id": ident, "nom": nom, "lat": corps["lat"], "lon": corps["lon"], "texte": json.dumps(corps, ensure_ascii=False)}
