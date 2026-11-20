"""IMPORT DE FICHIERS CARTOGRAPHIQUES : GeoJSON, KML (et KMZ), GPX, shapefile (.shp seul ou .zip avec .dbf/.prj) → annotations de la carte (module pur, stdlib).

Rien n'est écrit ici : `lire(nom, octets)` renvoie {elements: [{type, titre, geometrie [[lat, lon]...], note}], avertissements: [...]} que le serveur enregistre ensuite par
engine/geo_annotations.creer_lot (mêmes droits, mêmes limites). Points → « point », lignes/routes/traces → « trajectoire », polygones (anneau extérieur) → « zone ».
Sécurité : taille limitée, XML refusé s'il déclare une DTD ou une entité (pas d'expansion), archives limitées à quelques membres de taille bornée.
Limite connue : coordonnées géographiques WGS84 uniquement (shapefile projeté refusé avec un message clair) ; les trous des polygones sont ignorés.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf

import io
import json
import re
import struct
import zipfile
from xml.etree import ElementTree as ET

LIMITES = {"octets": 8_000_000, "elements": 1000, "sommets": 2000, "octets_membre": 20_000_000}
_EXT = (".geojson", ".json", ".kml", ".kmz", ".gpx", ".shp", ".zip")


class ErreurImport(ValueError):
    """Fichier illisible ou non pris en charge : message prêt à afficher."""


def _paire(lon, lat):
    try:
        lon, lat = float(lon), float(lat)
    except (TypeError, ValueError):
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:29:28"))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:31:28"))
    return [round(lat, 6), round(lon, 6)]


def _simplifier(pts, avert, titre):
    """Plus de 2000 sommets : sous-échantillonnage régulier (début et fin conservés), signalé."""
    maxi = LIMITES["sommets"]
    if len(pts) <= maxi:
        return pts
    pas = (len(pts) - 1) / (maxi - 1)
    avert.append(tf("engine/geo_import.py:g:41:18", v1=titre or _i18n_t("engine/geo_import.py:g:42:65"), v2=len(pts), v3=maxi))
    return [pts[round(i * pas)] for i in range(maxi)]


def _element(elements, avert, type_, pts, titre="", note=""):
    if type_ == "zone" and len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    mini = {"point": 1, "trajectoire": 2, "zone": 3}[type_]
    if len(pts) < mini:
        avert.append(tf("engine/geo_import.py:g:50:22", v1=titre or type_, v2=len(pts), v3=mini))
        return
    if type_ == "point":
        pts = pts[:1]
    elements.append({"type": type_, "titre": (titre or type_.capitalize()).strip()[:200], "note": (note or "").strip()[:2000],
                     "geometrie": _simplifier(pts, avert, titre)})


# ── GeoJSON ──────────────────────────────────────────────────────────────────
def _geojson_geometrie(g, elements, avert, titre, note, profondeur=0):
    if not isinstance(g, dict) or profondeur > 4:
        return
    t, c = g.get("type"), g.get("coordinates")
    if t == "GeometryCollection":
        for s in g.get("geometries") or []:
            _geojson_geometrie(s, elements, avert, titre, note, profondeur + 1)
    elif t == "Point":
        _element(elements, avert, "point", [_paire(c[0], c[1])], titre, note)
    elif t == "MultiPoint":
        for p in c:
            _element(elements, avert, "point", [_paire(p[0], p[1])], titre, note)
    elif t == "LineString":
        _element(elements, avert, "trajectoire", [_paire(p[0], p[1]) for p in c], titre, note)
    elif t == "MultiLineString":
        for ligne in c:
            _element(elements, avert, "trajectoire", [_paire(p[0], p[1]) for p in ligne], titre, note)
    elif t == "Polygon":
        _element(elements, avert, "zone", [_paire(p[0], p[1]) for p in c[0]], titre, note)
    elif t == "MultiPolygon":
        for poly in c:
            _element(elements, avert, "zone", [_paire(p[0], p[1]) for p in poly[0]], titre, note)
    else:
        avert.append(tf("engine/geo_import.py:g:82:22", v1=t))


def _nom_proprietes(props) -> tuple:
    props = props if isinstance(props, dict) else {}
    bas = {str(k).lower(): v for k, v in props.items()}
    titre = next((str(bas[k]) for k in ("name", "nom", "title", "titre", "label", "libelle") if bas.get(k) not in (None, "")), "")
    note = next((str(bas[k]) for k in ("description", "desc", "note", "comment") if bas.get(k) not in (None, "")), "")
    return titre, note


def _geojson(octets, elements, avert):
    try:
        doc = json.loads(octets.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:97:28"))
    objets = doc.get("features") if isinstance(doc, dict) and doc.get("type") == "FeatureCollection" else [doc]
    for f in objets or []:
        if not isinstance(f, dict):
            continue
        if f.get("type") == "Feature":
            titre, note = _nom_proprietes(f.get("properties"))
            _geojson_geometrie(f.get("geometry"), elements, avert, titre, note)
        else:
            _geojson_geometrie(f, elements, avert, "", "")


# ── KML / GPX (XML) ──────────────────────────────────────────────────────────
def _xml(octets):
    if re.search(rb"<!(DOCTYPE|ENTITY)", octets[:20000], re.I):
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:112:28"))
    try:
        racine = ET.fromstring(octets)
    except ET.ParseError:
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:116:28"))
    for e in racine.iter():
        e.tag = e.tag.rsplit("}", 1)[-1] if isinstance(e.tag, str) else e.tag     # espaces de noms ignorés
    return racine


def _txt(e, nom) -> str:
    n = e.find(nom)
    return (n.text or "").strip() if n is not None and n.text else ""


def _coords_kml(texte):
    pts = []
    for tok in (texte or "").split():
        v = tok.split(",")
        if len(v) >= 2:
            pts.append(_paire(v[0], v[1]))
    return pts


def _kml_geometrie(g, elements, avert, titre, note):
    if g.tag == "Point":
        _element(elements, avert, "point", _coords_kml(_txt(g, "coordinates")), titre, note)
    elif g.tag == "LineString":
        _element(elements, avert, "trajectoire", _coords_kml(_txt(g, "coordinates")), titre, note)
    elif g.tag == "Polygon":
        c = g.find("outerBoundaryIs/LinearRing/coordinates")
        _element(elements, avert, "zone", _coords_kml(c.text if c is not None else ""), titre, note)
    elif g.tag == "MultiGeometry":
        for s in g:
            _kml_geometrie(s, elements, avert, titre, note)


def _kml(octets, elements, avert):
    racine = _xml(octets)
    for pm in racine.iter("Placemark"):
        titre, note = _txt(pm, "name"), _txt(pm, "description")
        for g in pm:
            if g.tag in ("Point", "LineString", "Polygon", "MultiGeometry"):
                _kml_geometrie(g, elements, avert, titre, note)


def _gpx(octets, elements, avert):
    racine = _xml(octets)
    if racine.tag != "gpx":
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:161:28"))
    for w in racine.findall("wpt"):
        _element(elements, avert, "point", [_paire(w.get("lon"), w.get("lat"))], _txt(w, "name"), _txt(w, "desc"))
    for trk in racine.findall("trk"):
        pts = [_paire(p.get("lon"), p.get("lat")) for seg in trk.findall("trkseg") for p in seg.findall("trkpt")]
        _element(elements, avert, "trajectoire", pts, _txt(trk, "name"), _txt(trk, "desc"))
    for rte in racine.findall("rte"):
        _element(elements, avert, "trajectoire", [_paire(p.get("lon"), p.get("lat")) for p in rte.findall("rtept")], _txt(rte, "name"), _txt(rte, "desc"))


# ── Shapefile ────────────────────────────────────────────────────────────────
def _dbf_noms(octets) -> list:
    """Premier champ texte nommé name/nom/label/title (sinon le premier champ texte) de chaque enregistrement ; [] si illisible."""
    try:
        n, long_en_tete, long_ligne = struct.unpack("<IHH", octets[4:12])
        champs, pos = [], 32
        while pos + 32 <= len(octets) and octets[pos] != 0x0D:
            champs.append((octets[pos:pos + 11].split(b"\0")[0].decode("latin-1").lower(), chr(octets[pos + 11]), octets[pos + 16]))
            pos += 32
        textes = [(i, c) for i, c in enumerate(champs) if c[1] == "C"]
        choix = next((i for i, c in textes if c[0] in ("name", "nom", "label", "title", "libelle")), textes[0][0] if textes else None)
        if choix is None:
            return []
        debut = 1 + sum(c[2] for c in champs[:choix])
        return [octets[long_en_tete + k * long_ligne + debut: long_en_tete + k * long_ligne + debut + champs[choix][2]].decode("latin-1").strip() for k in range(n)]
    except (struct.error, IndexError, UnicodeDecodeError):
        return []


def _shp(octets, noms, elements, avert):
    if len(octets) < 100 or struct.unpack(">i", octets[:4])[0] != 9994:
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:192:28"))
    pos, k = 100, 0
    while pos + 8 <= len(octets):
        longueur = struct.unpack(">i", octets[pos + 4:pos + 8])[0] * 2
        c = octets[pos + 8:pos + 8 + longueur]
        pos += 8 + longueur
        titre = noms[k] if k < len(noms) else ""
        k += 1
        if len(c) < 4:
            continue
        base = struct.unpack("<i", c[:4])[0]
        genre = {1: 1, 11: 1, 21: 1, 8: 8, 18: 8, 28: 8, 3: 3, 13: 3, 23: 3, 5: 5, 15: 5, 25: 5}.get(base, 0)
        if base == 0:
            continue
        if genre == 0:
            avert.append(tf("engine/geo_import.py:g:207:26", v1=base))
            continue
        if genre == 1:
            _element(elements, avert, "point", [_paire(*struct.unpack("<dd", c[4:20]))], titre)
        elif genre == 8:
            n = struct.unpack("<i", c[36:40])[0]
            for i in range(n):
                _element(elements, avert, "point", [_paire(*struct.unpack("<dd", c[40 + 16 * i:56 + 16 * i]))], titre)
        else:
            parties, n = struct.unpack("<ii", c[36:44])
            idx = struct.unpack(f"<{parties}i", c[44:44 + 4 * parties]) + (n,)
            debut = 44 + 4 * parties
            pts = [_paire(*struct.unpack("<dd", c[debut + 16 * i:debut + 16 * i + 16])) for i in range(n)]
            anneaux = [pts[idx[i]:idx[i + 1]] for i in range(parties)]
            if genre == 3:
                for a in anneaux:
                    _element(elements, avert, "trajectoire", a, titre)
            else:   # anneaux extérieurs = sens horaire (aire signée négative en x=lon, y=lat) ; les trous sont ignorés
                ext = [a for a in anneaux if len(a) > 3 and sum((q[1] - p[1]) * (q[0] + p[0]) for p, q in zip(a, a[1:])) < 0] or anneaux[:1]
                for a in ext:
                    _element(elements, avert, "zone", a, titre)
        if len(elements) > LIMITES["elements"] * 3:
            raise ErreurImport(_i18n_t("engine/geo_import.py:g:229:32"))


def _zip_membres(octets):
    try:
        z = zipfile.ZipFile(io.BytesIO(octets))
    except zipfile.BadZipFile:
        raise ErreurImport("Archive ZIP illisible.")
    infos = [i for i in z.infolist() if not i.is_dir() and i.file_size <= LIMITES["octets_membre"]][:50]
    return {i.filename.lower(): z.read(i) for i in infos}


def _shapefile(membres, elements, avert):
    nom = next((n for n in membres if n.endswith(".shp")), None)
    if nom is None:
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:244:28"))
    prj = membres.get(nom[:-4] + ".prj", b"").decode("latin-1", "ignore").upper()
    if prj and ("PROJCS" in prj or "GEOGCS" not in prj):
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:247:28"))
    if not prj:
        avert.append(_i18n_t("engine/geo_import.py:g:249:22"))
    _shp(membres[nom], _dbf_noms(membres.get(nom[:-4] + ".dbf", b"")), elements, avert)


# ── Point d'entrée ───────────────────────────────────────────────────────────
def lire(nom: str, octets: bytes) -> dict:
    nom = (nom or "").lower().strip()
    if not nom.endswith(_EXT):
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:257:28"))
    if not octets or len(octets) > LIMITES["octets"]:
        raise ErreurImport(tf("engine/geo_import.py:g:259:28", v1=LIMITES['octets'] // 1_000_000))
    elements, avert = [], []
    if nom.endswith((".geojson", ".json")):
        _geojson(octets, elements, avert)
    elif nom.endswith(".gpx"):
        _gpx(octets, elements, avert)
    elif nom.endswith(".kml"):
        _kml(octets, elements, avert)
    elif nom.endswith(".shp"):
        _shp(octets, [], elements, avert)
        avert.append(_i18n_t("engine/geo_import.py:g:269:22"))
    else:
        membres = _zip_membres(octets)
        if any(n.endswith(".shp") for n in membres):
            _shapefile(membres, elements, avert)
        else:
            cible = next((n for n in membres if n.endswith((".kml", ".gpx", ".geojson", ".json"))), None)
            if cible is None:
                raise ErreurImport(_i18n_t("engine/geo_import.py:g:277:36"))
            return lire(cible, membres[cible])
    if not elements:
        raise ErreurImport(_i18n_t("engine/geo_import.py:g:280:28"))
    return {"elements": elements, "avertissements": avert[:20]}
