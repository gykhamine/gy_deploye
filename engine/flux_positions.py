"""FLUX DE POSITIONS : décodage AIS, GPS (NMEA), ADS-B et JSON/CSV en positions normalisées (module PUR, aucun import Django).

Une position normalisée = {id, nom, lat, lon, ts (secondes epoch UTC), vitesse_ms, cap, source}. Le rangement dans les tables de la
plateforme (une colonne de position par objet, une ligne par pas de temps) est dans engine/geo_flux.py ; la lecture continue d'une
source réseau, série ou fichier est dans la commande `suivre_flux_positions`.

Formats (`format` = « auto » pour deviner ligne par ligne) :
  * ais  : phrases NMEA `!AIVDM`/`!AIVDO` (types 1, 2, 3, 18, 19 = positions ; 5, 19, 24 = noms des navires ; phrases multiples
           recollées). Un horodatage placé avant la phrase (epoch ou ISO) est utilisé s'il existe, sinon l'heure de réception.
  * nmea : `$..RMC` (position, vitesse, cap, date et heure GPS), `$..GGA` (fix valide), `$..GLL`. Contrôle de somme vérifié quand il est présent.
  * sbs  : ADS-B BaseStation (dump1090 port 30003, `MSG,3,...`) ; vitesse et cap des messages `MSG,4` rattachés à l'avion.
           Les date/heure d'un message SBS sont lues comme UTC (récepteur en heure locale : choisir l'heure de réception).
  * json : `aircraft.json` (dump1090/readsb), OpenSky `states`, GeoJSON (Point), objet ou liste d'objets, ou une ligne JSON par position.
  * csv  : première ligne = en-têtes contenant latitude et longitude (lat/lon/lng, id, ts/time/date, vitesse, cap...).
Rien n'est deviné en dehors de ces formats : une ligne qui n'en est pas une est comptée comme rejetée, jamais inventée.
"""
from __future__ import annotations
from engine.i18n import t

import csv
import json
import math
import re
from datetime import datetime, timezone

NOEUD = 0.514444   # m/s
LIMITES = {"ligne": 4096, "fragments": 200}   # longueur d'une ligne, phrases AIS en attente : plus de plafond sur le NOMBRE de lignes ni de positions
FORMATS = ("auto", "ais", "nmea", "sbs", "json", "csv")
_RE_PHRASE = re.compile(r"[!$][A-Z]{2}[A-Z]{3},")
_CLES = {
    "id": ("id", "mmsi", "icao", "icao24", "hex", "device", "device_id", "imei", "objet", "vehicule", "tracker", "name", "nom"),
    "nom": ("nom", "name", "libelle", "callsign", "flight", "flight_no"),
    "lat": ("lat", "latitude"), "lon": ("lon", "lng", "long", "longitude"),
    "ts": ("ts", "time", "timestamp", "horodatage", "datetime", "date", "t"),
    "cap": ("cap", "heading", "course", "cog", "track", "bearing"),
}


# ── Petits utilitaires ───────────────────────────────────────────────────────
def _nombre(v):
    try:
        x = float(str(v).strip().replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _instant(v):
    """epoch (s ou ms), datetime ou texte ISO -> secondes epoch UTC ; None si illisible. Un instant sans fuseau est lu comme UTC."""
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).timestamp()
    x = _nombre(v)
    if x is None:
        try:
            d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00").replace("/", "-"))
        except ValueError:
            return None
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    if x > 1e11:
        x /= 1000.0   # millisecondes
    return x if x > 0 else None


def _position(id_, lat, lon, ts, source, nom=None, vitesse_ms=None, cap=None):
    lat, lon = _nombre(lat), _nombre(lon)
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0) or ts is None:
        return None   # (0, 0) est la valeur « sans fix » de la plupart des récepteurs
    id_ = str(id_).strip()
    if not id_:
        return None
    return {"id": id_, "nom": (str(nom).strip() if nom else "") or id_, "lat": lat, "lon": lon, "ts": float(ts), "vitesse_ms": vitesse_ms,
            "cap": cap, "source": source}


def _controle_ok(phrase: str) -> bool:
    """Somme de contrôle NMEA (XOR des caractères entre le premier symbole et « * »). Absente = tolérée (beaucoup de journaux la retirent)."""
    corps, etoile, cs = phrase[1:].partition("*")
    if not etoile:
        return True
    try:
        attendu = int(cs[:2], 16)
    except ValueError:
        return False
    x = 0
    for c in corps:
        x ^= ord(c)
    return x == attendu


# ── AIS : bits et texte 6 bits ───────────────────────────────────────────────
def _bits(payload: str, remplissage: int = 0) -> str:
    sortie = []
    for ch in payload:
        v = ord(ch) - 48
        if v > 40:
            v -= 8
        if not 0 <= v < 64:
            raise ValueError(t("engine/flux_positions.py:g:99:30"))
        sortie.append(format(v, "06b"))
    s = "".join(sortie)
    return s[:len(s) - remplissage] if remplissage else s


def _u(s, a, n):
    seg = s[a:a + n]
    return int(seg, 2) if len(seg) == n else None


def _i(s, a, n):
    v = _u(s, a, n)
    return None if v is None else (v - (1 << n) if v >= (1 << (n - 1)) else v)


def _texte6(s, a, n):
    sortie = []
    for k in range(a, min(a + n, len(s)) - 5, 6):
        v = int(s[k:k + 6], 2)
        sortie.append(chr(v + 64) if v < 32 else chr(v))
    return "".join(sortie).rstrip("@ ").strip()


# ── Décodeur à état (recollage AIS, noms, date GPS, vitesse ADS-B) ──────────
class Decodeur:
    """`alimenter(ligne)` ou `alimenter_texte(texte)` -> liste de positions. L'état (phrases AIS à recoller, noms de navires, dernière date GPS,
    vitesse/cap/indicatif des avions, en-têtes CSV) vit dans l'instance : la même sert pour un fichier entier et pour un flux continu.
    `horodatage` : « source » = dates présentes dans les données (repli : heure de réception) ; « reception » = toujours l'heure de réception."""

    def __init__(self, format: str = "auto", id_defaut: str = "gps", horodatage: str = "source"):
        self.format = format if format in FORMATS else "auto"
        self.id_defaut = (id_defaut or "gps").strip() or "gps"
        self.horodatage = "reception" if horodatage == "reception" else "source"
        self.noms: dict = {}
        self._fragments: dict = {}
        self._jour_gps = None
        self._avions: dict = {}
        self._entete = None
        self._delim = ","
        self.compteurs = {"lignes": 0, "positions": 0, "rejetees": 0, "ignorees": 0}

    # -- entrées ----------------------------------------------------------------
    def alimenter_texte(self, texte: str, maintenant: float) -> list:
        """Document entier (réponse d'une API, fichier collé) : JSON complet si le texte en est un, sinon ligne par ligne."""
        brut = (texte or "").strip()
        if self.format in ("auto", "json") and brut[:1] in ("{", "["):
            try:
                doc = json.loads(brut)
            except ValueError:
                doc = None
            if doc is not None:
                self.compteurs["lignes"] += 1
                sortie = self._json(doc, maintenant)
                self.compteurs["positions"] += len(sortie)
                if not sortie:
                    self.compteurs["ignorees"] += 1
                return sortie
        sortie = []
        for ligne in brut.splitlines():
            sortie.extend(self.alimenter(ligne, maintenant))
        return sortie

    def alimenter(self, ligne: str, maintenant: float) -> list:
        ligne = (ligne or "").strip()[:LIMITES["ligne"]]
        if not ligne or ligne.startswith("#"):
            return []
        self.compteurs["lignes"] += 1
        try:
            sortie = self._ligne(ligne, maintenant)
        except Exception:   # une ligne illisible ne bloque jamais le flux
            self.compteurs["rejetees"] += 1
            return []
        self.compteurs["positions"] += len(sortie)
        if not sortie:
            self.compteurs["ignorees"] += 1
        return sortie

    def amorcer(self, premiere_ligne: str) -> None:
        """Reprise au milieu d'un fichier : relit l'en-tête CSV (première ligne), rien d'autre n'est décodé ni compté."""
        ligne = (premiere_ligne or "").strip()
        if ligne and self._entete is None and (self.format == "csv" or (self.format == "auto" and self._deviner(ligne) == "csv")):
            try:
                self._csv(ligne, 0)
            except Exception:
                pass

    # -- aiguillage -----------------------------------------------------------
    def _ligne(self, ligne, maintenant):
        prefixe = None
        m = _RE_PHRASE.search(ligne) if self.format in ("auto", "ais", "nmea") else None
        if m and m.start() > 0:
            prefixe, ligne = _instant(ligne[:m.start()].strip(" ,;|\t")), ligne[m.start():]
        fmt = self.format if self.format != "auto" else self._deviner(ligne)
        if fmt == "ais":
            return self._ais(ligne, prefixe, maintenant)
        if fmt == "nmea":
            return self._nmea(ligne, maintenant)
        if fmt == "sbs":
            return self._sbs(ligne.split(","), maintenant)
        if fmt == "json":
            return self._json(json.loads(ligne), maintenant)
        return self._csv(ligne, maintenant)

    @staticmethod
    def _deviner(ligne):
        if ligne.startswith("!"):
            return "ais"
        if ligne.startswith("$"):
            return "nmea"
        if ligne.startswith("MSG,"):
            return "sbs"
        return "json" if ligne[:1] in ("{", "[") else "csv"

    def _ts(self, source, maintenant):
        return source if (source is not None and self.horodatage == "source") else maintenant

    # -- AIS ------------------------------------------------------------------
    def _ais(self, ligne, prefixe, maintenant):
        phrase = ligne[ligne.index("!"):]
        if not _controle_ok(phrase):
            raise ValueError(t("engine/flux_positions.py:g:222:30"))
        f = phrase[1:].split("*")[0].split(",")
        if len(f) < 7 or not f[0].endswith(("VDM", "VDO")):
            return []
        total, num, seq, canal, charge = int(f[1]), int(f[2]), f[3], f[4], f[5]
        remplissage = int(f[6] or 0)
        if total > 1:
            cle = (seq, canal)
            if len(self._fragments) > LIMITES["fragments"]:
                self._fragments.pop(next(iter(self._fragments)))
            morceaux = self._fragments.setdefault(cle, {})
            if num == 1:
                morceaux.clear()
            morceaux[num] = charge
            if len(morceaux) < total:
                return []
            charge = "".join(morceaux[k] for k in sorted(morceaux))
            del self._fragments[cle]
        return self._message_ais(_bits(charge, remplissage), self._ts(prefixe, maintenant))

    def _message_ais(self, b, ts):
        type_, mmsi = _u(b, 0, 6), _u(b, 8, 30)
        if type_ is None or mmsi is None:
            return []
        id_ = str(mmsi)
        if type_ == 5:
            self.noms[id_] = _texte6(b, 112, 120) or self.noms.get(id_, "")
            return []
        if type_ == 24:
            if _u(b, 38, 2) == 0:
                self.noms[id_] = _texte6(b, 40, 120) or self.noms.get(id_, "")
            return []
        if type_ in (1, 2, 3):
            sog, lon, lat, cog = _u(b, 50, 10), _i(b, 61, 28), _i(b, 89, 27), _u(b, 116, 12)
        elif type_ in (18, 19):
            sog, lon, lat, cog = _u(b, 46, 10), _i(b, 57, 28), _i(b, 85, 27), _u(b, 112, 12)
            if type_ == 19:
                self.noms[id_] = _texte6(b, 143, 120) or self.noms.get(id_, "")
        else:
            return []
        if lat is None or lon is None:
            return []
        p = _position(id_, lat / 600000.0, lon / 600000.0, ts, "ais", self.noms.get(id_),
                      sog / 10.0 * NOEUD if sog is not None and sog < 1023 else None, cog / 10.0 if cog is not None and cog < 3600 else None)
        return [p] if p else []   # 181° / 91° = « position indisponible » : hors bornes, donc rejetée par _position

    # -- GPS (NMEA) ------------------------------------------------------------
    def _nmea(self, ligne, maintenant):
        if not _controle_ok(ligne):
            raise ValueError(t("engine/flux_positions.py:g:271:30"))
        f = ligne[1:].split("*")[0].split(",")
        genre = f[0][-3:]
        if genre == "RMC" and len(f) >= 10 and f[2] == "A":
            jour = self._date_gps(f[9])
            ts = self._heure_gps(f[1], jour)
            vit = _nombre(f[7])
            return self._gps(f[3], f[4], f[5], f[6], ts, maintenant, vit * NOEUD if vit is not None else None, _nombre(f[8]))
        if genre == "GGA" and len(f) >= 7 and f[6] not in ("", "0"):
            return self._gps(f[2], f[3], f[4], f[5], self._heure_gps(f[1], self._jour_gps), maintenant)
        if genre == "GLL" and len(f) >= 7 and f[6] == "A":
            return self._gps(f[1], f[2], f[3], f[4], self._heure_gps(f[5], self._jour_gps), maintenant)
        return []

    def _gps(self, lat, ns, lon, ew, ts, maintenant, vitesse_ms=None, cap=None):
        p = _position(self.id_defaut, _ddmm(lat, ns), _ddmm(lon, ew), self._ts(ts, maintenant), "gps", None, vitesse_ms, cap)
        return [p] if p else []

    def _date_gps(self, ddmmyy):
        if len(ddmmyy) == 6 and ddmmyy.isdigit():
            aa = int(ddmmyy[4:6])   # fenêtre à deux chiffres : le GPS date de 1980, donc 80-99 = 19xx, 00-79 = 20xx
            self._jour_gps = ((1900 if aa >= 80 else 2000) + aa, int(ddmmyy[2:4]), int(ddmmyy[0:2]))
        return self._jour_gps

    @staticmethod
    def _heure_gps(hhmmss, jour):
        if jour is None or len(hhmmss) < 6 or not hhmmss[:6].isdigit():
            return None   # sans date GPS connue on ne devine pas le jour : heure de réception
        s = float(hhmmss[4:])
        try:
            return datetime(*jour, int(hhmmss[:2]), int(hhmmss[2:4]), int(s), int((s % 1) * 1e6), tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None

    # -- ADS-B BaseStation ---------------------------------------------------------
    def _sbs(self, c, maintenant):
        def g(i):
            return c[i].strip() if i < len(c) else ""
        if g(0) != "MSG" or not g(4):
            return []
        icao = g(4).upper()
        avion = self._avions.setdefault(icao, {})
        if g(10):
            avion["nom"] = g(10)
            self.noms[icao] = g(10)
        if _nombre(g(12)) is not None:
            avion["vitesse_ms"] = _nombre(g(12)) * NOEUD
        if _nombre(g(13)) is not None:
            avion["cap"] = _nombre(g(13))
        if _nombre(g(14)) is None or _nombre(g(15)) is None:
            return []
        p = _position(icao, g(14), g(15), self._ts(_instant(f"{g(6)} {g(7)}") if g(6) and g(7) else None, maintenant), "adsb",
                      avion.get("nom"), avion.get("vitesse_ms"), avion.get("cap"))
        return [p] if p else []

    # -- JSON (aircraft.json, OpenSky, GeoJSON, générique) --------------------------
    def _json(self, d, maintenant):
        if isinstance(d, list):
            return [p for x in d for p in self._json(x, maintenant)]
        if not isinstance(d, dict):
            return []
        if isinstance(d.get("features"), list):
            return [p for x in d["features"] for p in self._json(x, maintenant)]
        if d.get("type") == "Feature" and isinstance(d.get("geometry"), dict):
            xy = d["geometry"].get("coordinates") or []
            if d["geometry"].get("type") == "Point" and len(xy) >= 2:
                return self._enregistrement({**(d.get("properties") or {}), "lat": xy[1], "lon": xy[0]}, maintenant)
            return []
        if isinstance(d.get("aircraft"), list):   # dump1090 / readsb
            base = _nombre(d.get("now"))
            sortie = []
            for a in d["aircraft"]:
                if not isinstance(a, dict) or a.get("lat") is None or a.get("lon") is None:
                    continue
                vue = _nombre(a.get("seen_pos")) or 0.0
                gs = _nombre(a.get("gs"))
                p = _position(str(a.get("hex") or "").upper(), a.get("lat"), a.get("lon"), self._ts(base - vue if base else None, maintenant), "adsb",
                              a.get("flight"), gs * NOEUD if gs is not None else None, _nombre(a.get("track")))
                if p:
                    sortie.append(p)
            return sortie
        if isinstance(d.get("states"), list):   # OpenSky /states/all (vitesse déjà en m/s)
            sortie = []
            for s in d["states"]:
                if not isinstance(s, list) or len(s) < 11:
                    continue
                p = _position(str(s[0] or "").upper(), s[6], s[5], self._ts(_instant(s[3]) or _instant(s[4]) or _instant(d.get("time")), maintenant),
                              "adsb", s[1], _nombre(s[9]), _nombre(s[10]))
                if p:
                    sortie.append(p)
            return sortie
        return self._enregistrement(d, maintenant)

    def _enregistrement(self, d, maintenant):
        """Un objet {id, lat, lon, ts, vitesse, cap...} (noms de clés usuels, en minuscules), éventuellement coordonnées sous `position`."""
        d = {str(k).strip().lower(): v for k, v in d.items()}
        lat, lon = self._premier(d, "lat"), self._premier(d, "lon")
        for sous in ("position", "location", "coords", "gps"):
            if (lat is None or lon is None) and isinstance(d.get(sous), dict):
                s = {str(k).lower(): v for k, v in d[sous].items()}
                lat, lon = self._premier(s, "lat"), self._premier(s, "lon")
        nom = self._premier(d, "nom")
        id_ = self._premier(d, "id") or self.id_defaut
        vit = next((_nombre(d[k]) * f for k, f in (("vitesse_ms", 1.0), ("speed_ms", 1.0), ("vitesse_kmh", 1 / 3.6), ("speed_kmh", 1 / 3.6),
                                                    ("sog", NOEUD), ("speed_kn", NOEUD), ("vitesse_kn", NOEUD), ("speed", 1.0), ("vitesse", 1.0))
                    if _nombre(d.get(k)) is not None), None)
        cap = _nombre(self._premier(d, "cap"))
        p = _position(id_, lat, lon, self._ts(_instant(self._premier(d, "ts")), maintenant), "json", nom, vit, cap)
        return [p] if p else []

    @staticmethod
    def _premier(d, groupe):
        return next((d[k] for k in _CLES[groupe] if d.get(k) not in (None, "")), None)

    # -- CSV ---------------------------------------------------------------------
    def _csv(self, ligne, maintenant):
        if self._entete is None:
            for delim in (",", ";", "\t"):
                champs = [c.strip().lower() for c in next(csv.reader([ligne], delimiter=delim))]
                if any(c in _CLES["lat"] for c in champs) and any(c in _CLES["lon"] for c in champs):
                    self._entete, self._delim = champs, delim
                    return []
            raise ValueError(t("engine/flux_positions.py:g:393:30"))
        valeurs = next(csv.reader([ligne], delimiter=self._delim))
        return self._enregistrement(dict(zip(self._entete, valeurs)), maintenant)


def analyser_texte(texte: str, format: str = "auto", id_defaut: str = "gps", maintenant: float | None = None, horodatage: str = "source"):
    """(positions, rapport) pour un texte complet ; rapport = {lignes, positions, rejetees, ignorees, objets}."""
    import time
    dec = Decodeur(format, id_defaut, horodatage)
    positions = dec.alimenter_texte(texte, time.time() if maintenant is None else maintenant)
    rapport = dict(dec.compteurs)
    rapport["objets"] = len({p["id"] for p in positions})
    return positions, rapport


# ── Regroupement en pas de temps (une ligne de table = un pas) ───────────────────
def grouper(positions: list, pas_s: float) -> list:
    """[(début_du_pas, {id: dernière position du pas})] trié par temps. Deux mesures d'un même objet dans un pas : la dernière gagne."""
    pas = max(float(pas_s), 0.001)
    seaux: dict = {}
    for p in sorted(positions, key=lambda x: x["ts"]):
        seaux.setdefault(math.floor(p["ts"] / pas) * pas, {})[p["id"]] = p
    return [(k, seaux[k]) for k in sorted(seaux)]


def valeur_position(p: dict) -> str:
    """Valeur d'une cellule « geolocalisation » (même format que la saisie manuelle : « lat, lon »)."""
    return f"{p['lat']:.6f}, {p['lon']:.6f}"


def valeur_temps(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def construire_lignes(groupes: list, colonne_par_objet: dict, slug_temps: str) -> list:
    """[{cle, t, valeurs}] : `cle` identifie le pas de temps (jeton d'idempotence : rejouer le même flux ne duplique rien) ; `valeurs` = {slug: texte}.
    Les objets sans colonne (limite atteinte, création refusée) sont ignorés."""
    lignes = []
    for debut, objets in groupes:
        valeurs = {slug_temps: valeur_temps(debut)}
        for id_, p in objets.items():
            if id_ in colonne_par_objet:
                valeurs[colonne_par_objet[id_]] = valeur_position(p)
        if len(valeurs) > 1:
            lignes.append({"cle": f"flux:{int(round(debut * 1000))}", "t": debut, "valeurs": valeurs})
    return lignes


def _ddmm(valeur, hemisphere):
    x = _nombre(valeur)
    if x is None:
        return None
    deg = int(x // 100)
    r = deg + (x - deg * 100) / 60.0
    return -r if hemisphere in ("S", "W") else r
