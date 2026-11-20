"""ITINÉRAIRE PAR LES ROUTES, calculé PAR LE SERVEUR (module pur, stdlib uniquement), comme les tuiles et la recherche d'adresse :
le navigateur ne contacte jamais le service externe.

Le tracé « à vol d'oiseau » (engine/geo_outils.py, route) reste la référence et n'est jamais remplacé : ce module AJOUTE le tracé qui suit le
réseau routier réel d'OpenStreetMap (ruelles, avenues, sens des routes, routes praticables pour le mode choisi).
  * service par défaut : OSRM d'OpenStreetMap (routing.openstreetmap.de, profils voiture / vélo / à pied), remplaçable par la variable
    d'environnement GYPLATFORM_ROUTAGE_URL (modèle avec {profil} et {coords}, réponse au format OSRM « route »). ATTENTION : un serveur OSRM local ne
    connaît qu'UN profil ; pour que voiture, vélo et à pied donnent des tracés DIFFÉRENTS, une instance par mode, déclarée par
    GYPLATFORM_ROUTAGE_URL_VOITURE / _VELO / _PIED (ex. http://localhost:5000/route/v1/driving/{coords}?overview=full&geometries=geojson&steps=true,
    :5001 pour le vélo, :5002 pour la marche). Sans cela, le même serveur répond aux trois modes : un avertissement l'indique à l'écran ;
  * zones non navigables : OSRM ne passe que par des routes. Mer, lac, île sans pont ou point trop loin de tout chemin -> message clair,
    jamais de tracé inventé ; un départ ou une arrivée légèrement hors route est « accroché » à la route la plus proche, et l'écart est indiqué ;
  * politique d'usage respectée : User-Agent identifié, une requête à la fois, cache mémoire (une heure), pas de calcul en masse ;
  * hors ligne : message clair, la ligne droite reste affichée.
Consignes pas à pas (« Tournez à gauche sur … ») : lues dans les « steps » d'OSRM et traduites en français ici ; un service local dont le modèle
d'adresse n'a pas steps=true renvoie simplement une liste vide (la page garde distance, durée et tracé).
La durée est celle du profil OSRM (vitesses moyennes selon le type de route) : sans trafic, sans feux, sans horaires.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf, tl

import json
import logging
import math
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import geo_outils
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

URL_DEFAUT = "https://routing.openstreetmap.de/routed-{profil}/route/v1/driving/{coords}?overview=full&geometries=geojson&steps=true&alternatives=3"
AGENT = tl("engine/geo_routage.py:g:31:9")
PROFILS = {"voiture": "car", "velo": "bike", "pied": "foot"}
BATEAU = "bateau"           # voies d'eau : OSRM public n'a PAS de profil bateau -> serveur de routage maritime/fluvial optionnel, sinon ligne droite sur l'eau
VITESSE_BATEAU_MS = 5.0     # ~18 km/h : pirogue motorisée / vedette fluviale, sans courant
MOTO = "moto"               # moto-taxi / livreur motorisé : voiture + ruelles
RESEAUX_MIXTES = {"raccourcis": ("voiture", "velo", "pied"), "moto": ("voiture", "velo")}   # réseaux comparés par mode
COULOIR = "couloir"         # mode strict : chemin le plus court qui reste à moins de `couloir_m` (80 m) de la ligne à vol d'oiseau ; AUCUN autre mode n'a cette obligation
RACCOURCIS = "raccourcis"   # mode livreur : compare plusieurs routes et plusieurs réseaux (voiture + ruelles), garde la plus rapide
VITESSE_RUELLES_MS = 6.9    # ~25 km/h : vitesse moyenne d'un livreur (moto) sur le réseau de ruelles, pour comparer au réseau voiture
LIBELLES = {COULOIR: "couloir 80 m", "bateau": "bateau", "voiture": "voiture", "velo": _gtfr("engine/geo_routage.py:50:88"), "pied": tl("engine/geo_routage.py:g:40:104"), RACCOURCIS: "raccourcis", MOTO: "moto"}
LIMITES = {"pas_fil_m": 100.0, "points_fil": 60, "ecart_repasse_m": 80.0, "delai_s": 12.0, "octets": 8_000_000, "cache": 300, "duree_cache_s": 3600, "points": 4000, "distance_max_m": 3_000_000, "accroche_alerte_m": 200.0, "couloir_m": 80.0, "pas_couloir_min_m": 40.0}
PENALITE_VIRAGE_M = 30.0                           # mode couloir : un virage « vaut » 30 m de détour (préfère le tracé simple à un chemin en zigzag)
RESEAUX_COULOIR = ("voiture", "velo", "pied")   # réseaux comparés par le mode couloir
_verrou, _cache = threading.Lock(), {}


def _modele(profil: str) -> tuple:
    """(modèle d'URL, distingue_les_modes) pour un profil. Ordre : GYPLATFORM_ROUTAGE_URL_<VOITURE|VELO|PIED> (un serveur de routage par mode),
    puis GYPLATFORM_ROUTAGE_URL, puis le service public. Un serveur OSRM local ne connaît QU'UN profil (celui avec lequel il a été préparé) :
    si le modèle générique n'a pas de {profil} et qu'aucune variable propre au mode n'existe, voiture, vélo et à pied interrogent le MÊME serveur
    et rendent donc le même tracé -> distingue_les_modes = False (l'appelant ajoute un avertissement)."""
    propre = os.environ.get("GYPLATFORM_ROUTAGE_URL_" + str(profil).upper(), "").strip()
    if propre:
        return propre, True
    generique = os.environ.get("GYPLATFORM_ROUTAGE_URL", "").strip()
    if generique:
        return generique, "{profil}" in generique
    return URL_DEFAUT, True


def _avertir_modes(res: dict, profil: str, distingue: bool) -> dict:
    """Ajoute l'avertissement « le serveur ne distingue pas les modes » (tracé identique voiture / vélo / à pied)."""
    if not distingue and profil in PROFILS:
        res["avertissements"] = list(res.get("avertissements") or []) + [tf("engine/geo_routage.py:profil_non_distingue", v1=LIBELLES.get(profil, profil))]
    return res


def largeur_couloir() -> float:
    """Largeur du couloir en mètres : 80 m par défaut, réglable par GYPLATFORM_COULOIR_M (entre 20 et 500 m)."""
    try:
        v = float(os.environ.get("GYPLATFORM_COULOIR_M", "").strip() or LIMITES["couloir_m"])
    except ValueError:
        v = LIMITES["couloir_m"]
    return max(20.0, min(500.0, v)) if math.isfinite(v) else LIMITES["couloir_m"]


class ErreurRoute(ValueError):
    """Itinéraire impossible : message prêt à afficher (la ligne droite reste affichée par la page)."""


def _point(p, nom):
    try:
        lat, lon = float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError, KeyError):
        raise ErreurRoute(f"{nom} invalide.")
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErreurRoute(tf("engine/geo_routage.py:g:65:27", v1=nom))
    return lat, lon


def _alleger(coords, maxi):
    """Au plus `maxi` sommets en gardant le premier et le dernier (un itinéraire de plusieurs centaines de km compte des dizaines de milliers de points)."""
    if len(coords) <= maxi:
        return coords
    pas = math.ceil(len(coords) / maxi)
    sortie = coords[::pas]
    if sortie[-1] != coords[-1]:
        sortie.append(coords[-1])
    return sortie


_journal = logging.getLogger(__name__)


def _raison_reseau(e) -> str:
    """Motif technique court d'un échec réseau (DNS, délai, certificat SSL, connexion refusée, proxy), pour le diagnostic."""
    cause = getattr(e, "reason", e)                       # URLError enveloppe la vraie exception dans .reason
    if isinstance(cause, (socket.timeout, TimeoutError)):
        return _i18n_tf("rg2.geo_routage.1", v1=LIMITES['delai_s'])
    if isinstance(cause, ssl.SSLCertVerificationError):
        return _i18n_tf("rg2.geo_routage.2", v1=cause)
    if isinstance(cause, ssl.SSLError):
        return _gtf("rg6.msg.017", v1=cause)
    if isinstance(cause, socket.gaierror):
        return _gtf("rg6.msg.018", v1=cause)
    if isinstance(cause, ConnectionRefusedError):
        return _i18n_t("rg2.geo_routage.3")
    return f"{type(cause).__name__} : {cause}"[:160]


def _interroger(url):
    """Appelle le service de routage ; renvoie le JSON, ou lève ErreurRoute (réseau absent, refus, réponse illisible)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=LIMITES["delai_s"]) as r:
            brut = r.read(LIMITES["octets"] + 1)
    except urllib.error.HTTPError as e:
        # OSRM répond 400 avec un JSON {"code": "NoRoute", ...} : on lit quand même le corps pour donner le vrai motif.
        try:
            return json.loads(e.read(LIMITES["octets"]).decode("utf-8", "replace"))
        except (ValueError, OSError):
            raise ErreurRoute(tf("engine/geo_routage.py:g:91:31", v1=e.code))
    except (OSError, ValueError) as e:
        raison = _raison_reseau(e)
        _journal.warning(_gt("rg5.msg.057"), urllib.parse.urlsplit(url).netloc, raison)
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:93:27") + f" [{raison}]")   # motif technique ajouté : sans lui, DNS, certificat, délai et proxy se ressemblent tous
    if len(brut) > LIMITES["octets"]:
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:95:27"))
    try:
        return json.loads(brut.decode("utf-8"))
    except ValueError:
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:99:27"))


_MODIF = {"uturn": "faites demi-tour", "sharp right": tl("engine/geo_routage.py:g:102:55"), "right": tl("engine/geo_routage.py:g:102:96"), "slight right": tl("engine/geo_routage.py:g:102:132"),
          "straight": "continuez tout droit", "slight left": tl("engine/geo_routage.py:g:103:62"), "left": tl("engine/geo_routage.py:g:103:101"), "sharp left": tl("engine/geo_routage.py:g:103:135")}


def _consigne(type_, modif, nom, sortie=None) -> str:
    """Phrase française d'une manœuvre OSRM (type + modificateur + rue d'arrivée). Jamais d'exception : repli « Continuez »."""
    sur = f" sur {nom}" if nom else ""
    cote = _i18n_t("engine/geo_routage.py:g:110:12") if "left" in (modif or "") else _i18n_t("engine/geo_routage.py:g:110:55") if "right" in (modif or "") else ""
    if type_ == "depart":
        t = "partez" + sur
    elif type_ == "arrive":
        t = _i18n_t("engine/geo_routage.py:g:114:13") + (f", destination {cote}" if cote else "")
    elif type_ in ("roundabout", "rotary", "roundabout turn"):
        n = int(sortie) if isinstance(sortie, (int, float)) and sortie >= 1 else 0
        t = tf("engine/geo_routage.py:g:117:13", v1=n, v2='re' if n == 1 else 'e', v3=sur) if n else tf("engine/geo_routage.py:g:117:92", v1=_MODIF.get(modif, 'continuez'), v2=sur)
    elif type_ == "exit roundabout":
        t = _i18n_t("engine/geo_routage.py:g:119:13") + sur
    elif type_ == "merge":
        t = _gt("engine/geo_routage.py:173:13") + sur
    elif type_ == "on ramp":
        t = _i18n_t("engine/geo_routage.py:g:123:13") + sur
    elif type_ == "off ramp":
        t = _i18n_t("engine/geo_routage.py:g:125:13") + sur
    elif type_ == "fork":
        t = tf("engine/geo_routage.py:g:127:13", v1=cote, v2=sur) if cote else "continuez" + sur
    elif type_ == "end of road":
        t = (tf("engine/geo_routage.py:g:129:14", v1=cote) if cote else _i18n_t("engine/geo_routage.py:g:129:66")) + sur
    elif type_ in ("turn", "new name", "continue") and modif in _MODIF and not (type_ != "turn" and modif == "straight"):
        t = _MODIF[modif] + sur
    else:
        t = "continuez" + sur
    return t[:1].upper() + t[1:]


def _etapes(route) -> list:
    """Consignes pas à pas [{texte, rue, distance_m, duree_s, lat, lon, type, modifier}] depuis les « steps » OSRM ; liste vide si absentes ou illisibles."""
    sortie = []
    try:
        for leg in route.get("legs") or []:
            for st in leg.get("steps") or []:
                m = st.get("maneuver") or {}
                lon, lat = (float(v) for v in m["location"])
                nom = str(st.get("name") or "").strip()[:80]
                sortie.append({"texte": _consigne(m.get("type"), m.get("modifier"), nom, m.get("exit")), "rue": nom, "distance_m": round(float(st.get("distance") or 0), 1),
                               "duree_s": round(float(st.get("duration") or 0), 1), "lat": lat, "lon": lon, "type": str(m.get("type") or ""), "modifier": str(m.get("modifier") or "")})
                if len(sortie) >= 500:
                    return sortie
    except (KeyError, TypeError, ValueError, AttributeError):
        return []
    return sortie


def _xy(lat, lon, lat0):
    """Projection locale en mètres (équirectangulaire, suffisante sur quelques dizaines de km)."""
    return math.radians(lon) * 6371000.0 * math.cos(math.radians(lat0)), math.radians(lat) * 6371000.0


def ecart_ligne(geometrie, depart, arrivee) -> tuple:
    """(écart moyen, écart max) en mètres entre le tracé et la ligne rouge (segment départ-arrivée). Tracé rééchantillonné tous les ~20 m."""
    lat0 = (depart[0] + arrivee[0]) / 2
    ax, ay = _xy(depart[0], depart[1], lat0)
    bx, by = _xy(arrivee[0], arrivee[1], lat0)
    dx, dy = bx - ax, by - ay
    n2 = dx * dx + dy * dy or 1.0
    somme, maxi, nb = 0.0, 0.0, 0
    pts = [_xy(la, lo, lat0) for la, lo in geometrie]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        k = max(1, int(math.hypot(x2 - x1, y2 - y1) // 20))
        for i in range(k):
            x, y = x1 + (x2 - x1) * i / k, y1 + (y2 - y1) * i / k
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / n2))
            d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
            somme += d; maxi = max(maxi, d); nb += 1
    return (somme / nb if nb else 0.0), maxi


def virages(geometrie, seuil_deg: float = 35.0, segment_min_m: float = 15.0) -> int:
    """Nombre de VIRAGES d'un tracé (changement de cap d'au moins `seuil_deg`, segments de moins de `segment_min_m` ignorés : bruit de la géométrie).
    Sert à préférer, à écart égal, le tracé le plus simple à suivre."""
    pts, n = [], 0
    for c in geometrie:
        if not pts or geo_outils.haversine(pts[-1][0], pts[-1][1], c[0], c[1]) >= segment_min_m:
            pts.append(c)
    caps = [geo_outils.inverse(a[0], a[1], b[0], b[1])["cap_initial"] for a, b in zip(pts, pts[1:])]
    for c1, c2 in zip(caps, caps[1:]):
        if abs((c2 - c1 + 180.0) % 360.0 - 180.0) >= seuil_deg:
            n += 1
    return n


def _sans_boucles(coords):
    """Supprime les allers-retours (impasse, demi-tour au point de passage) : si un sommet est revu, on coupe tout ce qui est entre les deux passages."""
    sortie, vu = [], {}
    for c in coords:
        cle = (round(c[0], 5), round(c[1], 5))
        if cle in vu:
            del sortie[vu[cle] + 1:]
            vu = {(round(p[0], 5), round(p[1], 5)): i for i, p in enumerate(sortie)}
        else:
            vu[cle] = len(sortie); sortie.append(c)
    return sortie


def _longueur(coords) -> float:
    return sum(geo_outils.haversine(a[0], a[1], b[0], b[1]) for a, b in zip(coords, coords[1:]))


def _suivre_ligne(depart, arrivee, profil: str, pas_m: float):
    """Tracé qui COLLE à la ligne à vol d'oiseau : des points de passage sont posés sur la ligne rouge tous les `pas_m` mètres, accrochés à la route la plus proche,
    et le service de routage doit les traverser dans l'ordre (la route tourne dès qu'une voie l'y autorise, ne s'écarte que si aucune voie ne le permet).
    Un point de passage accroché loin de la ligne (> 3 × pas) est abandonné. Les allers-retours d'impasse sont supprimés ; distance et durée sont recalculées."""
    la1, lo1 = _point(depart, _i18n_t("engine/geo_routage.py:g:199:31"))
    la2, lo2 = _point(arrivee, "Destination")
    inv = geo_outils.inverse(la1, lo1, la2, lo2)
    direct = inv["distance_m"]
    if direct < 2 * pas_m:
        return None
    n = min(LIMITES["points_fil"], int(direct // pas_m))
    inter = [geo_outils.destination(la1, lo1, inv["cap_initial"], direct * i / n)[:2] for i in range(1, n)]
    cle = ("fil", profil, round(pas_m), round(la1, 5), round(lo1, 5), round(la2, 5), round(lo2, 5))
    with _verrou:
        trouve = _cache.get(cle)
        if trouve and time.time() - trouve[0] < LIMITES["duree_cache_s"]:
            return trouve[1]
        modele, distingue = _modele(profil)
        modele = modele.replace("&alternatives=3", "&alternatives=false")
        if not modele.startswith(("http://", "https://")):
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:214:31"))
        pts = [(la1, lo1)] + [tuple(p) for p in inter] + [(la2, lo2)]
        try:
            url = modele.format(profil=PROFILS[profil], coords=";".join(f"{lo:.6f},{la:.6f}" for la, lo in pts))
        except (KeyError, IndexError, ValueError):
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:219:31"))
        rep = _interroger(url)
    if not isinstance(rep, dict) or rep.get("code") != "Ok" or not rep.get("routes"):
        return None                                   # un point de passage inaccessible : l'appelant garde les autres tracés
    res = _avertir_modes(_construire(rep["routes"][0], rep, profil, la1, lo1, la2, lo2, direct), profil, distingue)
    coords = _sans_boucles(res["geometrie"])
    ancien = res["distance_m"] or 1.0
    res["geometrie"] = coords
    res["distance_m"] = round(_longueur(coords), 1)
    res["duree_s"] = round(res["duree_s"] * res["distance_m"] / ancien, 1)
    res["detour"] = round(res["distance_m"] / direct, 2) if direct > 1 else None
    vus = {(round(c[0], 5), round(c[1], 5)) for c in coords}
    et = [e for e in res["etapes"] if (round(e["lat"], 5), round(e["lon"], 5)) in vus and (e["type"] not in ("depart", "arrive"))]
    res["etapes"] = [e for e in res["etapes"] if e["type"] == "depart"][:1] + et + [e for e in res["etapes"] if e["type"] == "arrive"][-1:]
    with _verrou:
        _cache[cle] = (time.time(), res)
    return res


def _tracer_fidele(depart, arrivee, profil: str) -> list:
    """Meilleur tracé « fidèle à la ligne rouge » d'un réseau : pas fin, puis pas deux fois plus fin si un écart de plus de 80 m subsiste."""
    direct = geo_outils.inverse(depart[0], depart[1], arrivee[0], arrivee[1])["distance_m"]
    pas = max(LIMITES["pas_fil_m"], direct / LIMITES["points_fil"])
    res = _suivre_ligne(depart, arrivee, profil, pas)
    if res and ecart_ligne(res["geometrie"], depart, arrivee)[1] > LIMITES["ecart_repasse_m"] and pas / 2 >= 40:
        fin = _suivre_ligne(depart, arrivee, profil, max(40.0, pas / 2))
        if fin and ecart_ligne(fin["geometrie"], depart, arrivee)[0] < ecart_ligne(res["geometrie"], depart, arrivee)[0]:
            res = fin
    return [res] if res else []


def itineraire(depart, arrivee, profil: str = "voiture") -> dict:
    """Itinéraire par les routes. Mode « raccourcis » : voiture, ruelles, marche ; on garde le tracé qui s'éloigne le moins de la ligne à vol d'oiseau
    (le calcul voiture seul préfère les grands axes et peut doubler la distance ; les ruelles sont plus courtes). Les autres modes : un seul réseau."""
    if profil == BATEAU:
        return _bateau(depart, arrivee)
    if profil == COULOIR:
        return _couloir(depart, arrivee)
    if profil not in RESEAUX_MIXTES:
        if profil not in PROFILS:
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:259:31"))
        return _itineraire_simple(depart, arrivee, profil)
    cle = (profil, *(round(float(v), 5) for v in (*depart[:2], *arrivee[:2]))) if _valide(depart, arrivee) else None
    if cle and cle in _cache and time.time() - _cache[cle][0] < LIMITES["duree_cache_s"]:
        return _cache[cle][1]
    candidats, erreurs = [], []
    for reseau in RESEAUX_MIXTES[profil]:   # raccourcis : voiture + ruelles + marche ; moto : voiture + ruelles
        try:
            candidats.extend((reseau, r) for r in _variantes(depart, arrivee, reseau))
        except ErreurRoute as e:
            erreurs.append(e)
        try:                                 # + tracé qui colle à la ligne à vol d'oiseau (points de passage sur la ligne rouge)
            candidats.extend((reseau, r) for r in _tracer_fidele(depart, arrivee, reseau))
        except ErreurRoute:
            pass
    if not candidats:
        raise erreurs[0]
    def duree_equitable(c):    # durée comparable : le réseau de ruelles est recalculé à la vitesse d'un livreur, pas à celle d'un vélo
        reseau, r = c
        return r["distance_m"] / VITESSE_RUELLES_MS if reseau == "velo" else r["duree_s"]
    ecarts = {id(c[1]): ecart_ligne(c[1]["geometrie"], depart, arrivee) for c in candidats}
    # on garde le tracé qui s'éloigne LE MOINS de la ligne rouge (écart moyen), à égalité le plus court
    reseau, res = min(candidats, key=lambda c: (round(ecarts[id(c[1])][0] / 5), c[1]["distance_m"]))
    res = dict(res, profil=profil, reseau=reseau, duree_s=round(duree_equitable((reseau, res)), 1), ecart_moyen_m=round(ecarts[id(res)][0], 1), ecart_max_m=round(ecarts[id(res)][1], 1),
               comparaison=[{"reseau": n, "distance_m": r["distance_m"], "duree_s": round(duree_equitable((n, r)), 1), "ecart_moyen_m": round(ecarts[id(r)][0], 1)} for n, r in candidats])
    if res.get("detour") and res["detour"] > 2.0:
        res["avertissements"] = list(res.get("avertissements") or []) + [tf("engine/geo_routage.py:g:287:74", v1=res['detour'])]
    if cle:
        with _verrou:
            _cache[cle] = (time.time(), res)
    return res


def _tracer_couloir(depart, arrivee, reseau: str) -> list:
    """Tracés qui collent à la ligne rouge pour UN réseau, à plusieurs pas (points de passage de plus en plus serrés jusqu'à 40 m) : plus le pas est fin,
    plus le tracé est tenu près de la ligne ; le mode couloir garde ensuite le plus court de ceux qui restent dans la largeur autorisée."""
    direct = geo_outils.inverse(depart[0], depart[1], arrivee[0], arrivee[1])["distance_m"]
    pas = max(LIMITES["pas_fil_m"], direct / LIMITES["points_fil"])
    plancher, sortie, deja = LIMITES["pas_couloir_min_m"], [], set()
    while True:
        if round(pas) not in deja:
            deja.add(round(pas))
            res = _suivre_ligne(depart, arrivee, reseau, pas)
            if res:
                sortie.append(res)
        if pas <= plancher:
            break
        pas = max(plancher, pas / 2)
    return sortie


def _couloir(depart, arrivee) -> dict:
    """MODE COULOIR : le chemin le plus court possible qui reste dans un couloir de 80 m de part et d'autre de la ligne à vol d'oiseau.
    C'est une OBLIGATION de ce mode seulement (les autres modes restent libres, sans quoi un détour nécessaire serait refusé partout) :
    si aucun tracé ne tient dans le couloir (rivière sans pont, pas de rue à moins de 80 m…), le mode NE REFUSE PAS : il garde le tracé le plus proche de
    la ligne rouge (écart minimal, puis le moins de virages) et l'indique (`hors_couloir`, avertissement) ; il n'invente aucun tracé.
    Méthode : les réseaux voiture, ruelles et marche sont essayés (route normale, alternatives, tracés collés à la ligne à plusieurs pas) ; on écarte
    ceux dont l'écart maximal à la ligne dépasse le couloir, puis on garde le plus court. Le service de routage ne sait pas « router dans un couloir » :
    la contrainte est vérifiée sur chaque tracé, pas imposée au calcul ; un chemin plus court dans le couloir que ceux trouvés peut donc exister."""
    la1, lo1 = _point(depart, _i18n_t("engine/geo_routage.py:g:317:31"))
    la2, lo2 = _point(arrivee, "Destination")
    largeur = largeur_couloir()
    cle = (COULOIR, round(largeur), round(la1, 5), round(lo1, 5), round(la2, 5), round(lo2, 5))
    with _verrou:
        trouve = _cache.get(cle)
        if trouve and time.time() - trouve[0] < LIMITES["duree_cache_s"]:
            return trouve[1]
    direct = geo_outils.inverse(la1, lo1, la2, lo2)["distance_m"]
    candidats, erreurs = [], []
    for reseau in RESEAUX_COULOIR:
        try:
            candidats.extend((reseau, r) for r in _variantes(depart, arrivee, reseau))
        except ErreurRoute as e:
            erreurs.append(e)
        try:
            candidats.extend((reseau, r) for r in _tracer_couloir(depart, arrivee, reseau))
        except ErreurRoute:
            pass
    if not candidats:
        raise erreurs[0] if erreurs else ErreurRoute(_i18n_t("engine/geo_routage.py:g:337:54"))
    ecarts = {id(r): ecart_ligne(r["geometrie"], depart, arrivee) for _n, r in candidats}
    tournants = {id(r): virages(r["geometrie"]) for _n, r in candidats}
    dedans = [(n, r) for n, r in candidats if ecarts[id(r)][1] <= largeur]
    def duree(c):   # même équité que « raccourcis » : le réseau de ruelles est recalculé à la vitesse d'un livreur
        n, r = c
        return r["distance_m"] / VITESSE_RUELLES_MS if n == "velo" else r["duree_s"]
    hors_couloir = not dedans
    if dedans:
        # le plus court, mais un virage « coûte » PENALITE_VIRAGE_M mètres : à longueur voisine, le tracé le plus simple à suivre l'emporte
        reseau, res = min(dedans, key=lambda c: (c[1]["distance_m"] + PENALITE_VIRAGE_M * tournants[id(c[1])], duree(c)))
    else:
        # AUCUNE route ne tient dans le couloir (pas de rue à moins de 80 m de la ligne) : ce n'est pas une erreur. On garde le tracé qui s'éloigne
        # LE MOINS de la ligne rouge (par paliers de 10 m), puis celui qui a le moins de virages, puis le plus court : jamais un détour au large.
        reseau, res = min(candidats, key=lambda c: (round(ecarts[id(c[1])][1] / 10), tournants[id(c[1])], c[1]["distance_m"]))
    ecart_choisi = ecarts[id(res)]                            # mesuré avant que `res` ne devienne une copie enrichie
    res = dict(res, profil=COULOIR, reseau=reseau, duree_s=round(duree((reseau, res)), 1), couloir_m=largeur, hors_couloir=hors_couloir, virages=tournants[id(res)],
               ecart_moyen_m=round(ecarts[id(res)][0], 1), ecart_max_m=round(ecarts[id(res)][1], 1),
               comparaison=[{"reseau": n, "distance_m": r["distance_m"], "duree_s": round(duree((n, r)), 1), "ecart_max_m": round(ecarts[id(r)][1], 1),
                             "dans_le_couloir": ecarts[id(r)][1] <= largeur} for n, r in candidats])
    res["avertissements"] = list(res.get("avertissements") or [])
    if hors_couloir:
        res["avertissements"].append(_i18n_tf("rg2.geo_routage.4", v1=largeur, v2=ecart_choisi[1], v3=res['virages']))
    elif direct > 1 and res["distance_m"] > direct * 1.5:
        res["avertissements"].append(tf("engine/geo_routage.py:g:354:38", v1=res['distance_m'] / direct, v2=largeur))
    with _verrou:
        if len(_cache) >= LIMITES["cache"]:
            _cache.pop(next(iter(_cache)))
        _cache[cle] = (time.time(), res)
    return res


def _bateau(depart, arrivee) -> dict:
    """Mode bateau (eau). Le service public OSRM ne connaît que la terre : AUCUN tracé fluvial/maritime n'est inventé.
      * si GYPLATFORM_ROUTAGE_BATEAU_URL est définie (OSRM/serveur de routage avec un profil nautique, modèle avec {coords}), on l'utilise ;
      * sinon : ligne géodésique (plus court chemin sur la sphère) entre les deux points, durée à vitesse de bateau, et avertissement clair :
        rives, bancs de sable, rapides, courants, ponts et permis de navigation ne sont PAS vérifiés."""
    la1, lo1 = _point(depart, _i18n_t("engine/geo_routage.py:g:367:31"))
    la2, lo2 = _point(arrivee, "Destination")
    inv = geo_outils.inverse(la1, lo1, la2, lo2)
    direct = inv["distance_m"]
    modele = os.environ.get("GYPLATFORM_ROUTAGE_BATEAU_URL", "").strip()
    if modele:
        if not modele.startswith(("http://", "https://")):
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:374:31"))
        try:
            url = modele.format(profil="boat", coords=f"{lo1:.6f},{la1:.6f};{lo2:.6f},{la2:.6f}")
        except (KeyError, IndexError, ValueError):
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:378:31"))
        rep = _interroger(url)
        if not isinstance(rep, dict) or rep.get("code") != "Ok" or not rep.get("routes"):
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:381:31"))
        res = _construire(rep["routes"][0], rep, "velo", la1, lo1, la2, lo2, direct)
        res.update(profil=BATEAU, reseau="eau", duree_s=round(res["distance_m"] / VITESSE_BATEAU_MS, 1))
        return res
    if direct > LIMITES["distance_max_m"]:
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:386:27"))
    n = max(2, min(200, int(direct // 500) + 2))
    geo = [[la1, lo1]] + [list(geo_outils.destination(la1, lo1, inv["cap_initial"], direct * i / (n - 1))[:2]) for i in range(1, n - 1)] + [[la2, lo2]]
    return {"profil": BATEAU, "reseau": "ligne_eau", "geometrie": geo, "distance_m": round(direct, 1), "duree_s": round(direct / VITESSE_BATEAU_MS, 1), "direct_m": round(direct, 1),
            "detour": 1.0 if direct > 1 else None, "depart_accroche": [la1, lo1, 0.0], "arrivee_accroche": [la2, lo2, 0.0], "etapes": [],
            "avertissements": [_i18n_t("engine/geo_routage.py:g:391:32")]}


def _valide(depart, arrivee) -> bool:
    try:
        _point(depart, _i18n_t("engine/geo_routage.py:g:397:24")); _point(arrivee, "Destination")
        return True
    except ErreurRoute:
        return False


def _variantes(depart, arrivee, profil) -> list:
    """Toutes les variantes (route principale + alternatives) d'un réseau, sous la même forme que _itineraire_simple."""
    r = _itineraire_simple(depart, arrivee, profil, toutes=True)
    return r


def _itineraire_simple(depart, arrivee, profil: str = "voiture", toutes: bool = False):
    """Itinéraire par les routes entre deux points [lat, lon].
    Renvoie {profil, etapes (consignes), geometrie: [[lat, lon], ...], distance_m, duree_s, direct_m, detour, depart_accroche: [lat, lon, ecart_m],
    arrivee_accroche: [lat, lon, ecart_m], avertissements: [...]} ; lève ErreurRoute avec un message affichable."""
    if profil not in PROFILS:
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:414:27"))
    la1, lo1 = _point(depart, _i18n_t("engine/geo_routage.py:g:415:31"))
    la2, lo2 = _point(arrivee, "Destination")
    direct = geo_outils.inverse(la1, lo1, la2, lo2)["distance_m"]
    if direct > LIMITES["distance_max_m"]:
        raise ErreurRoute(tf("engine/geo_routage.py:g:419:27", v1=direct / 1000))
    cle = (profil, round(la1, 5), round(lo1, 5), round(la2, 5), round(lo2, 5), toutes)
    with _verrou:                                           # une requête à la fois : politique d'usage du service public
        trouve = _cache.get(cle)
        if trouve and time.time() - trouve[0] < LIMITES["duree_cache_s"]:
            return trouve[1]
        modele, distingue = _modele(profil)
        if not modele.startswith(("http://", "https://")):
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:427:31"))
        try:
            url = modele.format(profil=PROFILS[profil], coords=f"{lo1:.6f},{la1:.6f};{lo2:.6f},{la2:.6f}")
        except (KeyError, IndexError, ValueError):
            raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:431:31"))
        rep = _interroger(url)
    code = rep.get("code") if isinstance(rep, dict) else None
    if code == "NoRoute":
        raise ErreurRoute(tf("engine/geo_routage.py:g:435:27", v1=LIBELLES[profil]))
    if code == "NoSegment":
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:437:27"))
    if code != "Ok" or not rep.get("routes"):
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:439:27"))
    sorties = []
    for r in (rep["routes"] if toutes else rep["routes"][:1]):
        sorties.append(_avertir_modes(_construire(r, rep, profil, la1, lo1, la2, lo2, direct), profil, distingue))
    res = sorties if toutes else sorties[0]
    with _verrou:
        if len(_cache) >= LIMITES["cache"]:
            _cache.pop(next(iter(_cache)))
        _cache[cle] = (time.time(), res)
    return res


def _construire(r, rep, profil, la1, lo1, la2, lo2, direct):
    try:
        coords = [[float(c[1]), float(c[0])] for c in r["geometry"]["coordinates"]]
        distance, duree = float(r["distance"]), float(r["duration"])
    except (KeyError, TypeError, ValueError, IndexError):
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:456:27"))
    if len(coords) < 2:
        raise ErreurRoute(_i18n_t("engine/geo_routage.py:g:458:27"))
    coords = _alleger(coords, LIMITES["points"])
    accroches, alertes = [], []
    for i, (nom, (la, lo)) in enumerate(((_gt("engine/geo_routage.py:536:43"), (la1, lo1)), ("destination", (la2, lo2)))):
        # waypoints[i].location = point de la route le plus proche ; son écart à notre point = distance réelle d'accroche
        try:
            wlon, wlat = (float(v) for v in rep["waypoints"][i]["location"])
            ecart = geo_outils.inverse(la, lo, wlat, wlon)["distance_m"]
        except (KeyError, TypeError, ValueError, IndexError):
            wlat, wlon, ecart = la, lo, 0.0
        accroches.append([wlat, wlon, round(ecart, 1)])
        if ecart > LIMITES["accroche_alerte_m"]:
            alertes.append(tf("engine/geo_routage.py:g:470:28", v1=nom, v2=ecart))
    res = {"profil": profil, "geometrie": coords, "distance_m": round(distance, 1), "duree_s": round(duree, 1), "direct_m": round(direct, 1),
           "detour": round(distance / direct, 2) if direct > 1 else None, "depart_accroche": accroches[0], "arrivee_accroche": accroches[1], "avertissements": alertes, "etapes": _etapes(r)}
    return res
