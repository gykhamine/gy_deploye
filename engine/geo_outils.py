"""OUTILS TACTIQUES DE LA CARTE : coordonnées (décimal, DMS, UTM, MGRS), géodésie, mesures, zones et exports.

Module PUR (math + re, aucun Django, aucune base) : il complète engine/geospatial.py (analyse des trajectoires) sans rien en réimplémenter.
  - lire_coordonnees / convertir : « 4°15'48"S 15°14'34"E », « -4.2634 15.2429 », « 33M 500000 9529000 », « 33MML0000000000 » -> lat/lon et les trois écritures ;
  - inverse / destination : distance et caps géodésiques sur l'ellipsoïde WGS84 (Vincenty ; repli sphérique si l'itération ne converge pas, points quasi antipodaux) ;
  - route / matrice / projeter : tracé mesuré (étapes, cumul, durée), matrice distances-caps entre N positions, position estimée à cap et vitesse constants ;
  - cercle / corridor : zones calculées (polygones [lat, lon]) enregistrables comme annotations « zone » ;
  - evenements_zones : entrées, sorties, présence et distance à la zone pour des objets suivis (géorepérage) ;
  - exporter : KML ou GPX (objets suivis et annotations).
Les zones et distances à la frontière sont calculées dans un plan local (équirectangulaire autour de la zone) : exact à l'échelle d'une ville ou d'une
région, approximatif pour une zone de plusieurs milliers de km. Aucune position n'est interpolée : une entrée est datée au premier point observé dedans.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t, tf, tl

import math
import re
from xml.sax.saxutils import escape

A_WGS84 = 6378137.0
F_WGS84 = 1 / 298.257223563
R_MOY = 6371008.8
LIMITES = {"points_matrice": 150, "points_route": 2000, "objets": 200, "points_objet": 5000, "zones": 200, "sommets_zone": 2000, "horizons": 24}
BANDES = "CDEFGHJKLMNPQRSTUVWX"                         # 20 bandes de 8° (la X en fait 12°) de 80°S à 84°N, sans I ni O
_NORD_MIN_KM = dict(zip(BANDES, (1100, 2000, 2800, 3700, 4600, 5500, 6400, 7300, 8200, 9100, 0, 800, 1700, 2600, 3500, 4400, 5300, 6200, 7000, 7900)))
_COLONNES = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_LIGNES = "ABCDEFGHJKLMNPQRSTUV"


class ErreurOutil(ValueError):
    """Entrée invalide : message prêt à afficher."""


# ── Distances et caps ────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_MOY * math.asin(min(1.0, math.sqrt(h)))


def _cap_sphere(lat1, lon1, lat2, lon2) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def inverse(lat1, lon1, lat2, lon2) -> dict:
    """Problème géodésique inverse (Vincenty) : {distance_m, cap_initial, cap_final, methode}. Caps en degrés, 0 = nord, sens horaire."""
    if abs(lat1 - lat2) < 1e-12 and abs(lon1 - lon2) < 1e-12:
        return {"distance_m": 0.0, "cap_initial": 0.0, "cap_final": 0.0, "methode": "ellipsoide"}
    a, f = A_WGS84, F_WGS84
    b = (1 - f) * a
    U1, U2 = math.atan((1 - f) * math.tan(math.radians(lat1))), math.atan((1 - f) * math.tan(math.radians(lat2)))
    L = math.radians(lon2 - lon1)
    lam = L
    sU1, cU1, sU2, cU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    for _ in range(200):
        sl, cl = math.sin(lam), math.cos(lam)
        ss = math.hypot(cU2 * sl, cU1 * sU2 - sU1 * cU2 * cl)
        if ss == 0:
            return {"distance_m": 0.0, "cap_initial": 0.0, "cap_final": 0.0, "methode": "ellipsoide"}
        cs = sU1 * sU2 + cU1 * cU2 * cl
        sigma = math.atan2(ss, cs)
        sa = cU1 * cU2 * sl / ss
        c2a = 1 - sa * sa
        c2sm = cs - 2 * sU1 * sU2 / c2a if c2a != 0 else 0.0
        C = f / 16 * c2a * (4 + f * (4 - 3 * c2a))
        lam_prev = lam
        lam = L + (1 - C) * f * sa * (sigma + C * ss * (c2sm + C * cs * (-1 + 2 * c2sm * c2sm)))
        if abs(lam - lam_prev) < 1e-12:
            break
    else:   # points quasi antipodaux : pas de convergence, on garde la sphère (erreur < 0,5 %)
        return {"distance_m": haversine(lat1, lon1, lat2, lon2), "cap_initial": _cap_sphere(lat1, lon1, lat2, lon2),
                "cap_final": (_cap_sphere(lat2, lon2, lat1, lon1) + 180) % 360, "methode": "sphere"}
    u2 = c2a * (a * a - b * b) / (b * b)
    A = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    ds = B * ss * (c2sm + B / 4 * (cs * (-1 + 2 * c2sm * c2sm) - B / 6 * c2sm * (-3 + 4 * ss * ss) * (-3 + 4 * c2sm * c2sm)))
    s = b * A * (sigma - ds)
    sl, cl = math.sin(lam), math.cos(lam)
    a1 = math.degrees(math.atan2(cU2 * sl, cU1 * sU2 - sU1 * cU2 * cl)) % 360
    a2 = math.degrees(math.atan2(cU1 * sl, -sU1 * cU2 + cU1 * sU2 * cl)) % 360
    return {"distance_m": s, "cap_initial": a1, "cap_final": a2, "methode": "ellipsoide"}


def destination(lat, lon, cap_deg, distance_m) -> tuple:
    """Problème géodésique direct (Vincenty) : (lat, lon) atteints en partant de (lat, lon) au cap donné sur la distance donnée."""
    a, f = A_WGS84, F_WGS84
    b = (1 - f) * a
    al1 = math.radians(cap_deg)
    sa1, ca1 = math.sin(al1), math.cos(al1)
    tU1 = (1 - f) * math.tan(math.radians(lat))
    cU1 = 1 / math.sqrt(1 + tU1 * tU1)
    sU1 = tU1 * cU1
    sig1 = math.atan2(tU1, ca1)
    sa = cU1 * sa1
    c2a = 1 - sa * sa
    u2 = c2a * (a * a - b * b) / (b * b)
    A = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    B = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    sig = distance_m / (b * A)
    for _ in range(200):
        c2sm = math.cos(2 * sig1 + sig)
        ss, cs = math.sin(sig), math.cos(sig)
        ds = B * ss * (c2sm + B / 4 * (cs * (-1 + 2 * c2sm * c2sm) - B / 6 * c2sm * (-3 + 4 * ss * ss) * (-3 + 4 * c2sm * c2sm)))
        nouveau = distance_m / (b * A) + ds
        if abs(nouveau - sig) < 1e-12:
            sig = nouveau
            break
        sig = nouveau
    ss, cs = math.sin(sig), math.cos(sig)
    c2sm = math.cos(2 * sig1 + sig)
    tmp = sU1 * ss - cU1 * cs * ca1
    phi2 = math.atan2(sU1 * cs + cU1 * ss * ca1, (1 - f) * math.hypot(sa, tmp))
    lam = math.atan2(ss * sa1, cU1 * cs - sU1 * ss * ca1)
    C = f / 16 * c2a * (4 + f * (4 - 3 * c2a))
    L = lam - (1 - C) * f * sa * (sig + C * ss * (c2sm + C * cs * (-1 + 2 * c2sm * c2sm)))
    lon2 = (lon + math.degrees(L) + 540) % 360 - 180
    return math.degrees(phi2), lon2


# ── UTM / MGRS (WGS84, séries de Krüger) ─────────────────────────────────────
_n = F_WGS84 / (2 - F_WGS84)
_A = A_WGS84 / (1 + _n) * (1 + _n ** 2 / 4 + _n ** 4 / 64 + _n ** 6 / 256)
_E = math.sqrt(F_WGS84 * (2 - F_WGS84))
_ALPHA = (None,
          _n / 2 - 2 * _n ** 2 / 3 + 5 * _n ** 3 / 16 + 41 * _n ** 4 / 180 - 127 * _n ** 5 / 288 + 7891 * _n ** 6 / 37800,
          13 * _n ** 2 / 48 - 3 * _n ** 3 / 5 + 557 * _n ** 4 / 1440 + 281 * _n ** 5 / 630 - 1983433 * _n ** 6 / 1935360,
          61 * _n ** 3 / 240 - 103 * _n ** 4 / 140 + 15061 * _n ** 5 / 26880 + 167603 * _n ** 6 / 181440,
          49561 * _n ** 4 / 161280 - 179 * _n ** 5 / 168 + 6601661 * _n ** 6 / 7257600,
          34729 * _n ** 5 / 80640 - 3418889 * _n ** 6 / 1995840,
          212378941 * _n ** 6 / 319334400)
_BETA = (None,
         _n / 2 - 2 * _n ** 2 / 3 + 37 * _n ** 3 / 96 - _n ** 4 / 360 - 81 * _n ** 5 / 512 + 96199 * _n ** 6 / 604800,
         _n ** 2 / 48 + _n ** 3 / 15 - 437 * _n ** 4 / 1440 + 46 * _n ** 5 / 105 - 1118711 * _n ** 6 / 3870720,
         17 * _n ** 3 / 480 - 37 * _n ** 4 / 840 - 209 * _n ** 5 / 4480 + 5569 * _n ** 6 / 90720,
         4397 * _n ** 4 / 161280 - 11 * _n ** 5 / 504 - 830251 * _n ** 6 / 7257600,
         4583 * _n ** 5 / 161280 - 108847 * _n ** 6 / 3991680,
         20648693 * _n ** 6 / 638668800)
K0 = 0.9996


def bande(lat: float) -> str:
    if not -80 <= lat <= 84:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:143:27"))
    return BANDES[min(19, int((lat + 80) // 8))]


def _zone(lat: float, lon: float) -> int:
    z = int((lon + 180) // 6) + 1
    b = bande(lat)
    if b == "V" and 3 <= lon < 12:        # Norvège
        z = 32
    if b == "X" and 0 <= lon < 42:        # Svalbard
        z = 31 if lon < 9 else 33 if lon < 21 else 35 if lon < 33 else 37
    return min(60, max(1, z))


def vers_utm(lat: float, lon: float) -> dict:
    """{zone, bande, hemisphere, est, nord} en mètres (est avec les 500 km de décalage, nord avec 10 000 km dans l'hémisphère sud)."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:160:27"))
    b, zone = bande(lat), _zone(lat, lon)
    lam0 = math.radians((zone - 1) * 6 - 180 + 3)
    phi, lam = math.radians(lat), math.radians(lon) - lam0
    lam = (lam + math.pi) % (2 * math.pi) - math.pi
    tau = math.tan(phi)
    sigma = math.sinh(_E * math.atanh(_E * tau / math.sqrt(1 + tau * tau)))
    taup = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
    xi, eta = math.atan2(taup, math.cos(lam)), math.asinh(math.sin(lam) / math.sqrt(taup * taup + math.cos(lam) ** 2))
    xi_s, eta_s = xi, eta
    for j in range(1, 7):
        xi += _ALPHA[j] * math.sin(2 * j * xi_s) * math.cosh(2 * j * eta_s)
        eta += _ALPHA[j] * math.cos(2 * j * xi_s) * math.sinh(2 * j * eta_s)
    x, y = K0 * _A * eta + 500000.0, K0 * _A * xi
    sud = lat < 0
    return {"zone": zone, "bande": b, "hemisphere": "S" if sud else "N", "est": x, "nord": y + 10000000.0 if sud else y}


def depuis_utm(zone: int, hemisphere: str, est: float, nord: float) -> tuple:
    if not 1 <= zone <= 60:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:180:27"))
    xi = ((nord - (10000000.0 if hemisphere == "S" else 0.0)) / (K0 * _A))
    eta = (est - 500000.0) / (K0 * _A)
    xi_p, eta_p = xi, eta
    for j in range(1, 7):
        xi_p -= _BETA[j] * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= _BETA[j] * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    sh = math.sinh(eta_p)
    taup = math.sin(xi_p) / math.sqrt(sh * sh + math.cos(xi_p) ** 2)
    tau = taup
    for _ in range(20):
        sigma = math.sinh(_E * math.atanh(_E * tau / math.sqrt(1 + tau * tau)))
        taup_i = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
        d = (taup - taup_i) / math.sqrt(1 + taup_i * taup_i) * (1 + (1 - _E ** 2) * tau * tau) / ((1 - _E ** 2) * math.sqrt(1 + tau * tau))
        tau += d
        if abs(d) < 1e-12:
            break
    lat = math.degrees(math.atan(tau))
    lon = math.degrees(math.atan2(sh, math.cos(xi_p))) + (zone - 1) * 6 - 180 + 3
    return lat, lon


def vers_mgrs(lat: float, lon: float, chiffres: int = 5) -> str:
    """MGRS à `chiffres` chiffres par axe (5 = 1 m, 4 = 10 m, 3 = 100 m, 2 = 1 km, 1 = 10 km) ; les chiffres sont tronqués, jamais arrondis."""
    if not 1 <= chiffres <= 5:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:205:27"))
    u = vers_utm(lat, lon)
    col = _COLONNES[(u["zone"] - 1) % 3][int(u["est"] // 100000) - 1]
    lig = _LIGNES[(int(u["nord"] // 100000) + (5 if u["zone"] % 2 == 0 else 0)) % 20]
    e, n = int(u["est"]) % 100000, int(u["nord"]) % 100000
    coupe = 10 ** (5 - chiffres)
    return f"{u['zone']:02d}{u['bande']}{col}{lig}{e // coupe:0{chiffres}d}{n // coupe:0{chiffres}d}"


_RE_MGRS = re.compile(r"^(\d{1,2})\s*([C-HJ-NP-X])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*((?:\d\s*){2,10})$", re.I)


def depuis_mgrs(texte: str) -> tuple:
    m = _RE_MGRS.match(texte.strip())
    if not m:
        raise ErreurOutil("MGRS illisible (exemple : 33M ML 00000 00000).")
    zone, b, col, lig = int(m.group(1)), m.group(2).upper(), m.group(3).upper(), m.group(4).upper()
    chiffres = re.sub(r"\s", "", m.group(5))
    if len(chiffres) % 2:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:224:27"))
    if not 1 <= zone <= 60:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:226:27"))
    set_col = _COLONNES[(zone - 1) % 3]
    if col not in set_col:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:229:27"))
    h = len(chiffres) // 2
    pas = 10 ** (5 - h)
    est = (set_col.index(col) + 1) * 100000 + int(chiffres[:h]) * pas
    n100 = (_LIGNES.index(lig) - (5 if zone % 2 == 0 else 0)) % 20 * 100000
    nord = n100 + int(chiffres[h:]) * pas
    mini = _NORD_MIN_KM[b] * 1000
    while nord < mini:
        nord += 2000000
    sud = BANDES.index(b) < BANDES.index("N")
    lat, lon = depuis_utm(zone, "S" if sud else "N", est + pas / 2, nord + pas / 2)   # centre de la maille : la précision est celle de l'écriture
    return lat, lon


def _dms(v: float, positif: str, negatif: str) -> str:
    h = positif if v >= 0 else negatif
    v = abs(v)
    d = int(v)
    mn = int((v - d) * 60)
    s = (v - d - mn / 60) * 3600
    if round(s, 2) >= 60:
        s, mn = 0.0, mn + 1
    if mn >= 60:
        mn, d = 0, d + 1
    return f"{d}°{mn:02d}'{s:05.2f}\"{h}"


def convertir(lat: float, lon: float) -> dict:
    """Toutes les écritures d'une position."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:259:27"))
    r = {"lat": round(lat, 6), "lon": round(lon, 6), "decimal": f"{lat:.6f}, {lon:.6f}", "dms": f"{_dms(lat, 'N', 'S')} {_dms(lon, 'E', 'O')}"}
    try:
        u = vers_utm(lat, lon)
        r["utm"] = f"{u['zone']}{u['bande']} {u['est']:.0f} {u['nord']:.0f}"
        r["utm_detail"] = u
        r["mgrs"] = vers_mgrs(lat, lon, 5)
        r["mgrs_lisible"] = f"{r['mgrs'][:3]} {r['mgrs'][3:5]} {r['mgrs'][5:10]} {r['mgrs'][10:]}"
    except ErreurOutil:
        r["utm"] = r["mgrs"] = r["mgrs_lisible"] = None   # zones polaires
    return r


_NB = r"[-+]?\d+(?:[.,]\d+)?"
_RE_UTM = re.compile(r"^(\d{1,2})\s*([C-HJ-NP-X])\s+(\d+(?:[.,]\d+)?)\s*(?:m\s*)?E?[\s,;]+(\d+(?:[.,]\d+)?)\s*(?:m\s*)?N?$", re.I)
_RE_HEMI = re.compile(r"[NSEWO]", re.I)


def _angle(brut: str) -> float:
    """« 4°15'48"S », « 15.2429 E », « -4,26 » -> degrés décimaux (S et O négatifs)."""
    t = brut.strip().replace("′", "'").replace("″", '"').replace("’", "'").replace("”", '"').replace("º", "°")
    h = _RE_HEMI.findall(t)
    nombres = re.findall(r"\d+(?:[.,]\d+)?", t)
    if not nombres or len(nombres) > 3 or len(h) > 1:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:283:27"))
    vals = [float(x.replace(",", ".")) for x in nombres]
    v = vals[0] + (vals[1] / 60 if len(vals) > 1 else 0) + (vals[2] / 3600 if len(vals) > 2 else 0)
    if t.lstrip().startswith("-") or (h and h[0].upper() in "SWO"):
        v = -v
    return v


def lire_coordonnees(texte: str) -> tuple:
    """Décimal, DMS (° ' \"), UTM « 33M 500000 9529000 » ou MGRS -> (lat, lon). Les hémisphères S, O et W rendent négatif."""
    t = str(texte or "").strip()
    if not t:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:295:27"))
    if len(t) > 120:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:297:27"))
    m = _RE_UTM.match(t)
    if m:
        zone, b = int(m.group(1)), m.group(2).upper()
        lat, lon = depuis_utm(zone, "S" if BANDES.index(b) < BANDES.index("N") else "N", float(m.group(3).replace(",", ".")), float(m.group(4).replace(",", ".")))
        return _verifier(lat, lon)
    if _RE_MGRS.match(t):
        return _verifier(*depuis_mgrs(t))
    # deux angles : séparés par virgule/point-virgule/espace, ou par une lettre d'hémisphère (4°15'S 15°14'E)
    morceaux = re.findall(r"[^,;]+", t)
    if len(morceaux) != 2:
        morceaux = re.findall(r".*?[NSEWO](?=\s|$)|.+", t, re.I) if len(_RE_HEMI.findall(t)) == 2 else t.split()
    if len(morceaux) != 2 and len(t.split()) == 2:
        morceaux = t.split()
    if len(morceaux) != 2:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:312:27"))
    a, b = morceaux[0], morceaux[1]
    ha, hb = _RE_HEMI.findall(a), _RE_HEMI.findall(b)
    if ha and ha[0].upper() in "EWO" and hb and hb[0].upper() in "NS":   # « 15°E 4°S » : longitude d'abord
        a, b = b, a
    return _verifier(_angle(a), _angle(b))


def _verifier(lat, lon) -> tuple:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:322:27"))
    return round(lat, 6), round(lon, 6)


# ── Mesures ──────────────────────────────────────────────────────────────────
def _points(brut, maxi, nom="points") -> list:
    try:
        pts = [(float(p[0]), float(p[1])) for p in brut]
    except (TypeError, ValueError, IndexError):
        raise ErreurOutil(tf("engine/geo_outils.py:g:331:27", v1=nom))
    if len(pts) > maxi:
        raise ErreurOutil(tf("engine/geo_outils.py:g:333:27", v1=nom, v2=maxi))
    if not all(-90 <= la <= 90 and -180 <= lo <= 180 for la, lo in pts):
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:335:27"))
    return pts


def route(points, vitesse_kmh: float | None = None) -> dict:
    """Tracé mesuré : étapes (distance, cap initial et final), cumul et, si une vitesse est donnée, durée de chaque étape et du total."""
    pts = _points(points, LIMITES["points_route"])
    if len(pts) < 2:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:343:27"))
    if vitesse_kmh is not None and not 0 < vitesse_kmh <= 40000:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:345:27"))
    etapes, cumul = [], 0.0
    for (a, b), (c, d) in zip(pts, pts[1:]):
        i = inverse(a, b, c, d)
        cumul += i["distance_m"]
        e = {"distance_m": round(i["distance_m"], 1), "cap_initial": round(i["cap_initial"], 1), "cap_final": round(i["cap_final"], 1), "cumul_m": round(cumul, 1)}
        if vitesse_kmh:
            e["duree_s"] = round(i["distance_m"] / (vitesse_kmh / 3.6), 1)
        etapes.append(e)
    droite = inverse(*pts[0], *pts[-1])["distance_m"]
    r = {"etapes": etapes, "total_m": round(cumul, 1), "distance_directe_m": round(droite, 1), "detour": round(cumul / droite, 3) if droite > 0 else None}
    if vitesse_kmh:
        r["duree_s"] = round(cumul / (vitesse_kmh / 3.6), 1)
    return r


def matrice(points, noms=None) -> dict:
    """Distances (m) et caps initiaux (°) entre toutes les paires de positions (N <= 150) ; `plus_proche` = voisin le plus proche de chacune."""
    pts = _points(points, LIMITES["points_matrice"])
    n = len(pts)
    if n < 2:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:366:27"))
    noms = [str(x)[:60] for x in (noms or [])][:n] + [f"P{i + 1}" for i in range(len(noms or []), n)]
    dist = [[0.0] * n for _ in range(n)]
    cap = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            r = inverse(*pts[i], *pts[j])
            dist[i][j] = dist[j][i] = round(r["distance_m"], 1)
            cap[i][j] = round(r["cap_initial"], 1)
            cap[j][i] = round((r["cap_final"] + 180) % 360, 1)
    proche = []
    for i in range(n):
        j = min((k for k in range(n) if k != i), key=lambda k: dist[i][k])
        proche.append({"de": noms[i], "vers": noms[j], "distance_m": dist[i][j]})
    return {"noms": noms, "distances_m": dist, "caps": cap, "plus_proche": proche}


def projeter(lat, lon, cap_deg, vitesse_ms, horizons_s) -> list:
    """Position estimée à cap et vitesse constants (navigation à l'estime) pour chaque horizon en secondes : [{t_s, distance_m, lat, lon}]."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:386:27"))
    if not 0 <= vitesse_ms <= 12000:
        raise ErreurOutil("Vitesse hors limites.")
    hs = sorted({float(h) for h in horizons_s})[:LIMITES["horizons"]]
    if not hs or hs[0] < 0 or hs[-1] > 30 * 86400:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:391:27"))
    sortie = []
    for h in hs:
        d = vitesse_ms * h
        la, lo = destination(lat, lon, cap_deg, d)
        sortie.append({"t_s": h, "distance_m": round(d, 1), "lat": round(la, 6), "lon": round(lo, 6)})
    return sortie


# ── Zones ────────────────────────────────────────────────────────────────────
def cercle(lat, lon, rayon_m, n: int = 72) -> list:
    """Polygone [lat, lon] d'un cercle géodésique (n sommets)."""
    if not 1 <= rayon_m <= 5_000_000:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:404:27"))
    n = max(8, min(360, int(n)))
    return [[round(x, 6) for x in destination(lat, lon, 360.0 * i / n, rayon_m)] for i in range(n)]


def _plan(pts):
    la0 = sum(p[0] for p in pts) / len(pts)
    lo0 = pts[0][1]
    k, c = math.radians(1) * R_MOY, math.cos(math.radians(la0))
    # longitudes ramenées près de la première pour ne pas casser une zone à cheval sur l'antiméridien
    def vers(p):
        dl = (p[1] - lo0 + 180) % 360 - 180
        return (dl * k * c, (p[0] - la0) * k)
    return vers


def corridor(trace, largeur_m, pas_arc: int = 12) -> list:
    """Polygone [lat, lon] du corridor de `largeur_m` (largeur totale) autour d'une trajectoire : bords gauche et droit, bouts arrondis."""
    pts = _points(trace, LIMITES["points_route"], "sommets")
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if len(pts) < 2:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:425:27"))
    if not 1 <= largeur_m <= 1_000_000:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:427:27"))
    r = largeur_m / 2
    caps = [inverse(*a, *b)["cap_initial"] for a, b in zip(pts, pts[1:])]
    gauche, droite = [], []
    for i, p in enumerate(pts):
        if i == 0:
            c_av = c_ap = caps[0]
        elif i == len(pts) - 1:
            c_av = c_ap = caps[-1]
        else:
            c_av, c_ap = caps[i - 1], caps[i]
        moy = math.radians(c_av) + (((math.radians(c_ap) - math.radians(c_av)) + math.pi) % (2 * math.pi) - math.pi) / 2
        demi = abs((math.radians(c_ap) - math.radians(c_av) + math.pi) % (2 * math.pi) - math.pi) / 2
        d = r / max(math.cos(demi), 0.3)    # joint en onglet, borné pour les demi-tours
        gauche.append(destination(p[0], p[1], math.degrees(moy) - 90, d))
        droite.append(destination(p[0], p[1], math.degrees(moy) + 90, d))
    fin = [destination(*pts[-1], caps[-1] - 90 + 180.0 * k / pas_arc, r) for k in range(1, pas_arc)]
    debut = [destination(*pts[0], caps[0] + 90 + 180.0 * k / pas_arc, r) for k in range(1, pas_arc)]
    poly = gauche + fin + droite[::-1] + debut
    return [[round(a, 6), round(b, 6)] for a, b in poly]


def _dans(poly_xy, x, y) -> bool:
    dedans = False
    j = len(poly_xy) - 1
    for i, (xi, yi) in enumerate(poly_xy):
        xj, yj = poly_xy[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            dedans = not dedans
        j = i
    return dedans


def _dist_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def evenements_zones(objets, zones) -> dict:
    """Géorepérage. objets = [{id, nom, points: [[lat, lon, t?], ...]}] (ordre chronologique), zones = [{id, nom, geometrie: [[lat, lon], ...]}].
    Pour chaque couple objet-zone : état au dernier point, distance du dernier point à la zone (0 dedans), nombre d'entrées/sorties, durée dedans
    (si les points sont datés) et la liste des événements {type: entree|sortie, indice, t}. Un objet déjà dedans au premier point : « present_au_debut »."""
    if len(objets or []) > LIMITES["objets"] or len(zones or []) > LIMITES["zones"]:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:471:27"))
    zs = []
    for z in zones or []:
        g = _points(z.get("geometrie") or [], LIMITES["sommets_zone"], "sommets")
        if len(g) < 3:
            raise ErreurOutil(tf("engine/geo_outils.py:g:476:31", v1=z.get('nom') or z.get('id')))
        zs.append((z, g, _plan(g)))
    sortie = []
    for o in objets or []:
        pts = []
        for p in (o.get("points") or [])[:LIMITES["points_objet"] + 1]:
            try:
                la, lo = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            if -90 <= la <= 90 and -180 <= lo <= 180:
                t = p[2] if len(p) > 2 and isinstance(p[2], (int, float)) and not isinstance(p[2], bool) else None
                pts.append((la, lo, t))
        if len(pts) > LIMITES["points_objet"]:
            raise ErreurOutil(tf("engine/geo_outils.py:g:490:31", v1=o.get('nom') or o.get('id'), v2=LIMITES['points_objet']))
        if not pts:
            continue
        for z, g, vers in zs:
            poly = [vers(p) for p in g]
            etat, evts, dedans_s, precedent = None, [], 0.0, None
            for k, (la, lo, t) in enumerate(pts):
                x, y = vers((la, lo))
                dedans = _dans(poly, x, y)
                if precedent is None:
                    if dedans:
                        evts.append({"type": "present_au_debut", "indice": k, "t": t})
                elif dedans != etat:
                    evts.append({"type": "entree" if dedans else "sortie", "indice": k, "t": t})
                if etat and k and t is not None and pts[k - 1][2] is not None:
                    dedans_s += max(0.0, t - pts[k - 1][2])
                etat, precedent = dedans, k
            x, y = vers(pts[-1][:2])
            if etat:
                d = 0.0
            else:
                d = min(_dist_seg(x, y, *poly[i], *poly[(i + 1) % len(poly)]) for i in range(len(poly)))
            temps_ok = any(p[2] is not None for p in pts)
            sortie.append({"objet": o.get("id"), "nom": o.get("nom") or o.get("id"), "zone": z.get("id"), "zone_nom": z.get("nom") or z.get("id"),
                           "dedans": bool(etat), "distance_m": round(d, 1), "entrees": sum(1 for e in evts if e["type"] == "entree"),
                           "sorties": sum(1 for e in evts if e["type"] == "sortie"), "duree_dedans_s": round(dedans_s, 1) if temps_ok else None, "evenements": evts[:200]})
    return {"resultats": sortie, "dedans": [r for r in sortie if r["dedans"]]}


# ── Exports ──────────────────────────────────────────────────────────────────
def _couleur_kml(hexa: str) -> str:
    h = (hexa or "#f5a524").lstrip("#")
    if len(h) not in (6, 8):
        return "ff24a5f5"
    return (h[6:8] if len(h) == 8 else "ff") + h[4:6] + h[2:4] + h[0:2]   # aabbggrr (alpha de « #rrggbbaa » repris)


def exporter(format: str, objets=None, annotations=None, titre: str = tl("engine/geo_outils.py:g:527:71")) -> dict:
    """KML ou GPX : trajectoires des objets suivis et annotations (points, trajectoires, zones ; une zone devient un polygone KML, une piste fermée en GPX)."""
    format = (format or "").lower()
    if format not in ("kml", "gpx"):
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:531:27"))
    titre = escape(str(titre)[:120])
    objs = [(str(o.get("nom") or o.get("id") or "objet")[:120], _points([p[:2] for p in (o.get("points") or [])][:LIMITES["points_objet"]], LIMITES["points_objet"]),
             [p[2] if len(p) > 2 else None for p in (o.get("points") or [])][:LIMITES["points_objet"]]) for o in (objets or [])[:LIMITES["objets"]]]
    ans = list(annotations or [])[:1000]

    def iso(t):
        if not isinstance(t, (int, float)) or isinstance(t, bool):
            return None
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return None
    if format == "kml":
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>', f"<name>{titre}</name>"]
        for nom, pts, _t in objs:
            if len(pts) == 1:
                out.append(f"<Placemark><name>{escape(nom)}</name><Point><coordinates>{pts[0][1]:.6f},{pts[0][0]:.6f},0</coordinates></Point></Placemark>")
            elif pts:
                out.append(f"<Placemark><name>{escape(nom)}</name><LineString><tessellate>1</tessellate><coordinates>"
                           + " ".join(f"{lo:.6f},{la:.6f},0" for la, lo in pts) + "</coordinates></LineString></Placemark>")
        for a in ans:
            g, c = a.get("geometrie") or [], _couleur_kml(a.get("couleur"))
            if not g:
                continue
            nom, note = escape(str(a.get("titre") or a.get("type") or "")[:200]), escape(str(a.get("note") or "")[:2000])
            style = f"<Style><LineStyle><color>{c}</color><width>3</width></LineStyle><PolyStyle><color>55{c[2:]}</color></PolyStyle><IconStyle><color>{c}</color></IconStyle></Style>"
            if a.get("type") == "point":
                geom = f"<Point><coordinates>{g[0][1]:.6f},{g[0][0]:.6f},0</coordinates></Point>"
            elif a.get("type") == "zone":
                anneau = list(g) + [g[0]]
                geom = "<Polygon><outerBoundaryIs><LinearRing><coordinates>" + " ".join(f"{p[1]:.6f},{p[0]:.6f},0" for p in anneau) + "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
            else:
                geom = "<LineString><tessellate>1</tessellate><coordinates>" + " ".join(f"{p[1]:.6f},{p[0]:.6f},0" for p in g) + "</coordinates></LineString>"
            out.append(f"<Placemark><name>{nom}</name><description>{note}</description>{style}{geom}</Placemark>")
        out.append("</Document></kml>")
        return {"nom_fichier": "carte.kml", "mime": "application/vnd.google-earth.kml+xml", "contenu": "\n".join(out)}
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<gpx version="1.1" creator="Agent Gy" xmlns="http://www.topografix.com/GPX/1/1">', f"<metadata><name>{titre}</name></metadata>"]
    for a in ans:
        g = a.get("geometrie") or []
        if a.get("type") == "point" and g:
            out.append(f'<wpt lat="{g[0][0]:.6f}" lon="{g[0][1]:.6f}"><name>{escape(str(a.get("titre") or "Point")[:200])}</name><desc>{escape(str(a.get("note") or "")[:2000])}</desc></wpt>')
    for a in ans:
        g = a.get("geometrie") or []
        if a.get("type") in ("trajectoire", "zone") and g:
            pts = list(g) + ([g[0]] if a.get("type") == "zone" else [])
            out.append(f'<rte><name>{escape(str(a.get("titre") or a.get("type"))[:200])}</name>' + "".join(f'<rtept lat="{p[0]:.6f}" lon="{p[1]:.6f}"/>' for p in pts) + "</rte>")
    for nom, pts, ts in objs:
        if not pts:
            continue
        seg = []
        for (la, lo), t in zip(pts, ts):
            h = iso(t)
            seg.append(f'<trkpt lat="{la:.6f}" lon="{lo:.6f}">' + (f"<time>{h}</time>" if h else "") + "</trkpt>")
        out.append(f"<trk><name>{escape(nom)}</name><trkseg>{''.join(seg)}</trkseg></trk>")
    out.append("</gpx>")
    return {"nom_fichier": "carte.gpx", "mime": "application/gpx+xml", "contenu": "\n".join(out)}


def points_zones(points, zones) -> dict:
    """Géorepérage des « positions distinctes » : chaque point est une position INDÉPENDANTE (pas une trajectoire), jugée seule contre chaque zone.
    points = [{id, nom, lat, lon}], zones = [{id, nom, geometrie}]. Retour : {resultats: [{zone, zone_nom, dedans: [{objet, nom, lat, lon}], nb_dedans, plus_proche:
    {objet, nom, distance_m} | None}], nb_points}. Zone sans point dedans : le plus proche est donné (distance à la frontière, plan local)."""
    if len(points or []) > 5000 or len(zones or []) > LIMITES["zones"]:
        raise ErreurOutil(_i18n_t("engine/geo_outils.py:g:596:27"))
    pts = []
    for p in points or []:
        try:
            la, lo = float(p["lat"]), float(p["lon"])
        except (TypeError, ValueError, KeyError):
            continue
        if -90 <= la <= 90 and -180 <= lo <= 180:
            pts.append((p.get("id"), p.get("nom") or p.get("id"), la, lo))
    sortie = []
    for z in zones or []:
        g = _points(z.get("geometrie") or [], LIMITES["sommets_zone"], "sommets")
        if len(g) < 3:
            raise ErreurOutil(tf("engine/geo_outils.py:g:609:31", v1=z.get('nom') or z.get('id')))
        vers = _plan(g)
        poly = [vers(q) for q in g]
        dedans, proche = [], None
        for oid, nom, la, lo in pts:
            x, y = vers((la, lo))
            if _dans(poly, x, y):
                dedans.append({"objet": oid, "nom": nom, "lat": la, "lon": lo})
            else:
                d = min(_dist_seg(x, y, *poly[i], *poly[(i + 1) % len(poly)]) for i in range(len(poly)))
                if proche is None or d < proche["distance_m"]:
                    proche = {"objet": oid, "nom": nom, "distance_m": round(d, 1)}
        sortie.append({"zone": z.get("id"), "zone_nom": z.get("nom") or z.get("id"), "dedans": dedans[:500], "nb_dedans": len(dedans), "plus_proche": proche})
    return {"resultats": sortie, "nb_points": len(pts)}

