"""RECHERCHE D'ADRESSE (géocodage) faite PAR LE SERVEUR, comme les tuiles (module pur, stdlib) : le navigateur ne contacte jamais le service externe.

Service par défaut : Nominatim d'OpenStreetMap, remplaçable (variable GYPLATFORM_GEOCODAGE_URL, modèle avec {q}, réponse au format Nominatim « jsonv2 » :
serveur local, réseau interne). Politique d'usage respectée : User-Agent identifié, une requête par seconde au plus, pas de recherche automatique à la frappe,
cache mémoire des réponses. Précision : détails d'adresse demandés (libellé court rue/quartier/commune), 10 résultats, classement par proximité de la fenêtre
visible de la carte (paramètre `vue`), requêtes de repli si rien n'est trouvé. Hors ligne : message clair, rien d'autre ne casse.
"""
from __future__ import annotations
from engine.i18n import t, tl

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

URL_DEFAUT = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=10&addressdetails=1&dedupe=1&q={q}"
AGENT = tl("engine/geo_adresses.py:g:19:9")
LIMITES = {"texte": 200, "delai_s": 6.0, "octets": 400_000, "cache": 300, "pause_s": 1.0, "attente_max_s": 4.0, "resultats": 10, "variantes": 3, "vue_lat": 40.0, "vue_lon": 60.0}
_verrou, _cache, _dernier = threading.Lock(), {}, [0.0]
# Abréviations courantes d'adresse, développées dans la requête de repli (« av. » ne retrouve rien, « avenue » oui).
_ABREV = {"av": "avenue", "bd": "boulevard", "bvd": "boulevard", "r": "rue", "qt": "quartier", "qtier": "quartier", "pl": "place",
          "imp": "impasse", "ch": "chemin", "rte": "route", "fbg": "faubourg", "st": "saint", "ste": "sainte"}


class ErreurAdresse(ValueError):
    """Recherche impossible : message prêt à afficher."""


def _vue_valide(vue):
    """Fenêtre visible de la carte [sud, ouest, nord, est] -> la même, arrondie (cache), ou None si absente, invalide ou trop large
    (carte dézoomée sur un continent : un biais géographique n'aiderait pas, le résultat resterait celui du monde entier)."""
    try:
        s, o, n, e = (float(v) for v in vue)
    except (TypeError, ValueError):
        return None
    if not (-90 <= s < n <= 90 and -180 <= o < e <= 180) or n - s > LIMITES["vue_lat"] or e - o > LIMITES["vue_lon"]:
        return None
    return [round(v, 2) for v in (s, o, n, e)]


def _variantes(q):
    """Requêtes à essayer dans l'ordre, de la plus fidèle à la plus large : un nom de ruelle saisi à la main est rarement exact
    (numéro devant, abréviation, quartier et ville mêlés). On s'arrête à la première qui donne des résultats."""
    sans_numero = re.sub(r"^\d+\s*(?:bis|ter)?\s*,?\s+", "", q, flags=re.I)
    developpee = " ".join(_ABREV.get(m.lower().rstrip("."), m) for m in sans_numero.split())
    parties = [p.strip() for p in q.split(",") if p.strip()]
    candidates = [q, developpee, sans_numero]
    if len(parties) > 2:
        candidates.append(parties[0] + ", " + parties[-1])    # « rue, quartier, commune, pays » -> « rue, pays »
    sortie = []
    for c in candidates:
        if len(c) >= 3 and c.lower() not in (v.lower() for v in sortie):
            sortie.append(c)
    return sortie[:LIMITES["variantes"]]


def _appeler(modele, q, vue):
    """Une requête au service (une à la fois, une par seconde), réponse JSON décodée ; ErreurAdresse si injoignable."""
    url = modele.replace("{q}", urllib.parse.quote(q))
    if vue:                                                            # biais de proximité Nominatim (non bloquant : le reste du monde reste trouvable)
        url += "&viewbox=" + urllib.parse.quote("%s,%s,%s,%s" % (vue[1], vue[2], vue[3], vue[0]), safe=",")
    with _verrou:   # le verrou ne sert qu'à RÉSERVER un créneau (1 requête/s pour tout le serveur) : jamais tenu pendant l'attente ni le réseau
        maintenant = time.time()
        creneau = max(maintenant, _dernier[0] + LIMITES["pause_s"])
        if creneau - maintenant > LIMITES["attente_max_s"]:   # file trop longue : on refuse tout de suite plutôt que d'immobiliser un fil serveur
            raise ErreurAdresse(t("engine/geo_adresses.py:occupe"))
        _dernier[0] = creneau
    time.sleep(max(0.0, creneau - maintenant))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept-Language": "fr,en"})
        with urllib.request.urlopen(req, timeout=LIMITES["delai_s"]) as r:
            brut = r.read(LIMITES["octets"] + 1)
        if len(brut) > LIMITES["octets"]:
            raise ValueError
        return json.loads(brut.decode("utf-8"))
    except (OSError, ValueError):
        raise ErreurAdresse(t("engine/geo_adresses.py:g:76:33"))


def _libelle(r):
    """Libellé court et lisible (rue, quartier, commune, pays) construit depuis les détails d'adresse ; display_name complet en repli."""
    a = r.get("address") if isinstance(r.get("address"), dict) else {}
    rue = a.get("road") or a.get("pedestrian") or a.get("footway") or a.get("path") or ""
    nom = str(r.get("name") or rue or "")
    if a.get("house_number") and nom == rue:
        nom = "%s %s" % (a["house_number"], rue)
    premier = lambda cles: next((str(a[k]) for k in cles if a.get(k)), "")
    parties = []
    for p in (nom, premier(("neighbourhood", "quarter", "suburb", "city_district")),
              premier(("city", "town", "village", "municipality", "county", "state")), str(a.get("country") or "")):
        if p and (not parties or p.lower() != parties[-1].lower()):
            parties.append(p)
    return ", ".join(parties)[:240] or str(r.get("display_name") or "")[:240]


def _extraire(reponse):
    """Réponse Nominatim -> [{nom, detail, lat, lon, type, emprise}] sans doublon (même libellé = même lieu pour l'utilisateur)."""
    sortie, vus = [], set()
    for r in reponse if isinstance(reponse, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
            bb = [float(v) for v in r.get("boundingbox") or []]
        except (KeyError, TypeError, ValueError):
            continue
        nom = _libelle(r)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or nom.lower() in vus:
            continue
        vus.add(nom.lower())
        sortie.append({"nom": nom, "detail": str(r.get("display_name") or "")[:240], "lat": lat, "lon": lon, "type": str(r.get("type") or "")[:40],
                       "emprise": bb if len(bb) == 4 else None})
        if len(sortie) >= LIMITES["resultats"]:
            break
    return sortie


def rechercher(texte, vue=None) -> list:
    """[{nom, detail, lat, lon, type, emprise [sud, nord, ouest, est] | None}] (10 au plus, ordre du service : lieux proches de `vue` d'abord),
    ou ErreurAdresse. `vue` = fenêtre visible de la carte [sud, ouest, nord, est], facultative. Si la requête telle quelle ne donne rien,
    jusqu'à 2 requêtes de repli (abréviations développées, numéro retiré, rue + pays) sont essayées."""
    q = " ".join(str(texte or "").split())[:LIMITES["texte"]]
    if len(q) < 3:
        raise ErreurAdresse(t("engine/geo_adresses.py:g:123:29"))
    vue = _vue_valide(vue)
    cle = (q.lower(), tuple(vue) if vue else None)
    if cle in _cache:
        return _cache[cle]
    modele = os.environ.get("GYPLATFORM_GEOCODAGE_URL", "").strip() or URL_DEFAUT
    if not modele.startswith(("http://", "https://")) or "{q}" not in modele:
        raise ErreurAdresse(t("engine/geo_adresses.py:g:130:29"))
    sortie = []
    for variante in _variantes(q):
        sortie = _extraire(_appeler(modele, variante, vue))
        if sortie:
            break
    if len(_cache) >= LIMITES["cache"]:
        _cache.clear()
    _cache[cle] = sortie
    return sortie


def premier_lieu(texte):
    """Meilleur résultat d'un nom de lieu (ruelle, quartier, endroit repérable) : (lat, lon, nom) ou None si introuvable.
    Lève ErreurAdresse si le service est injoignable ou mal configuré (jamais pour un simple « introuvable »)."""
    q = " ".join(str(texte or "").split())
    if len(q) < 3:
        return None
    res = rechercher(q)
    return (res[0]["lat"], res[0]["lon"], res[0]["nom"]) if res else None
