"""GÉOSPATIAL : suivi de trajectoires, distances, collisions, prévision et géostatistique (module PUR, aucun import Django).

Modèle de données de la plateforme : UNE COLONNE de type « geolocalisation » = UN OBJET SUIVI ; UNE LIGNE = sa position à un instant.
Toutes les colonnes partagent donc le même axe du temps (les lignes, ou une colonne date/datetime si la table en a une).

  construire(lignes, colonnes, parse_geo, cle_temps, pas_s) -> matrice {t, ids, noms, lat, lon}  (NaN = position absente)
  fusionner([matrice, ...])                                  -> UNE matrice sur un axe de temps commun (une table = un objet, chacune avec sa date)
  scenario(...)                                              -> même matrice, trajectoires simulées (aucune donnée requise)
  analyser(matrice, **params)                                -> tout ce que la carte affiche et que la décision consomme

Calculs (plan local en mètres autour du centre : x = R cos(lat0) dlon, y = R dlat — exact à l'échelle d'une ville à une région) :
  * par objet : distance, vitesse, cap, tortuosité, arrêts, sauts de position (vitesse aberrante, score robuste médiane/MAD) ;
  * rencontres : distance minimale EXACTE entre deux objets sur chaque segment (interpolation linéaire), fusion des segments contigus ;
    un trou de positions court est enjambé (rencontre marquée « interpole ») ; une date absente est estimée entre ses voisines (alerte « date_manquante ») ;
  * prévision : IMM (4 filtres de Kalman en parallèle : rectiligne, virage à gauche, virage à droite, accélération ; probabilités de mode
    mises à jour à chaque mesure) ; `imm=False` = Kalman à vitesse constante (OpenCV `cv2.KalmanFilter`, repli linéaire sans cv2) ; puis Monte-Carlo sur la
    covariance pour une PROBABILITÉ de collision et un rayon d'incertitude à 90 % ;
  * validation : la prévision est rejouée sur le passé (on cache les k dernières positions) et comparée à « l'objet ne bouge plus » ;
  * géostatistique : centre et ellipse de dispersion, densité lissée, points chauds, indice de Clark-Evans, I de Moran et variogramme
    de la vitesse.
Rien n'est stocké ici : la persistance est dans engine/geo_donnees.py.
"""
from __future__ import annotations

import bisect
import math
from datetime import datetime

import numpy as np

R_TERRE = 6371008.8
# None = AUCUN plafond (objets lus, pas lus, cellules, colonnes de position par table) ; un nombre remet un plafond (réglage du serveur).
# « objets » / « pas » ne bornent PLUS la lecture : ils ne servent qu'au scénario simulé (scenario) et à la fenêtre d'estimation du bruit.
LIMITES = {"fenetre_filtre": 200, "objets": 60, "pas": 2000, "objets_max": None, "pas_max": None, "cellules_max": None, "points_geostat": 400, "pas_sortie": None, "horizon_max": 120, "budget_mc": 2_500_000, "fenetre_imm": 60, "points_affiches": None}
PARAMETRES = {
    "seuil_collision_m": 50.0,   # distance sous laquelle deux objets « se percutent »
    "horizon_pas": 30,           # nombre de pas de temps prévus
    "sigma_mesure_m": 5.0,       # bruit de mesure de la position (GPS)
    "sigma_accel": None,         # bruit d'accélération (m/s²) du modèle à vitesse constante ; None = estimé sur les données
    "echantillons": 200,         # tirages Monte-Carlo
    "seuil_arret_ms": 0.3,       # sous cette vitesse l'objet est à l'arrêt
    "facteur_saut": 6.0,         # un saut = vitesse > médiane + facteur × échelle robuste
    "proba_min": 0.05,           # probabilité de collision en dessous de laquelle on ne signale rien
    "pas_defaut_s": 60.0,        # durée d'une ligne quand la table n'a pas de colonne de temps
    "trou_max_pas": 5.0,         # un trou de positions plus court que ce nombre de pas (médian) est enjambé pour détecter les rencontres ; 0 = jamais
    "mode_positions": "auto",    # « auto » : chaque colonne est jugée (trajectoire d'UN objet, ou positions de plusieurs objets / suite incohérente) ; « trajectoire » ou « points » force le choix
    "vitesse_max_ms": 280.0,     # au-delà, le déplacement entre deux lignes datées est impossible (≈ 1000 km/h) : la suite n'est pas la trajectoire d'un seul objet
    "imm": True,                 # filtre multi-modèles (rectiligne / virage gauche / virage droit / accélération) ; False = Kalman rectiligne seul
    "graine": 20260929,
}


# ── Construction de la matrice ───────────────────────────────────────────────
def _temps(valeur):
    if isinstance(valeur, datetime):
        return valeur.timestamp()
    try:
        return float(valeur)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(valeur).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _completer_temps(brut, valides, pas_s: float):
    """Dates estimées pour les lignes qui n'en ont pas : interpolation entre les voisines datées, et pas médian aux extrémités."""
    idx = np.arange(len(brut), dtype=float)
    out = np.interp(idx, idx[valides], brut[valides])
    dt, di = np.diff(brut[valides]), np.diff(idx[valides])
    pas = float(np.median(dt[dt > 0] / di[dt > 0])) if (dt > 0).any() else pas_s
    premier, dernier = int(idx[valides][0]), int(idx[valides][-1])
    out[:premier] = brut[premier] - pas * (premier - idx[:premier])
    out[dernier + 1:] = brut[dernier] + pas * (idx[dernier + 1:] - dernier)
    return out


def construire(lignes: list, colonnes: list, parse_geo, cle_temps: str | None = None, pas_s: float = 60.0,
               max_objets: int | None = None, max_pas: int | None = None) -> dict:
    """lignes : [dict par slug] dans l'ordre voulu ; colonnes : [(slug, nom)] ; parse_geo(valeur) -> (lat, lon, libellé) | None.
    `max_objets` / `max_pas` : combien d'objets et de pas de temps (les derniers) sont lus ; absents : TOUS (aucun plafond)."""
    colonnes = colonnes[:max_objets] if max_objets else colonnes
    lignes = lignes[-max_pas:] if max_pas else lignes
    T, N = len(lignes), len(colonnes)
    lat, lon = np.full((T, N), np.nan), np.full((T, N), np.nan)
    for k, ligne in enumerate(lignes):
        for j, (slug, _nom) in enumerate(colonnes):
            g = parse_geo(ligne.get(slug))
            if g:
                lat[k, j], lon[k, j] = g[0], g[1]
    t, reel, sans_date, inversions = np.arange(T, dtype=float) * pas_s, False, 0, 0.0
    if cle_temps and T:
        brut = np.array([np.nan if (v := _temps(ligne.get(cle_temps))) is None else v for ligne in lignes], dtype=float)
        brut[~np.isfinite(brut)] = np.nan
        valides = np.isfinite(brut)
        sans_date = int((~valides).sum())
        if valides.sum() >= 2:                                   # des dates sur au moins 2 lignes : les lignes sans date en reçoivent une estimée
            brut = _completer_temps(brut, valides, pas_s)        # (sinon UNE date absente remettait tout l'axe à pas fixe, sans message)
            inversions = float(np.mean(np.diff(brut) < 0)) if T > 1 else 0.0   # part des lignes dont la date RECULE par rapport à la ligne précédente
            ordre = np.argsort(brut, kind="stable")
            t, lat, lon, reel = brut[ordre], lat[ordre], lon[ordre], True
        else:
            sans_date = T                                        # axe de temps inutilisable : pas fixe, signalé par une alerte
    return {"t": t, "ids": [c[0] for c in colonnes], "noms": [c[1] for c in colonnes], "lat": lat, "lon": lon, "temps_reel": reel, "dates_manquantes": sans_date,
            "inversions": [inversions] * N}


def _reechantillonner(t, lat, lon, grille, trou_max_pas: float):
    """Une piste (t, lat, lon) ramenée sur `grille` : interpolation linéaire entre SES positions, vide hors de sa période d'observation et
    dans ses trous plus longs que `trou_max_pas` × son pas médian. Retourne (lat, lon, nombre de positions réelles)."""
    la_g, lo_g = np.full(len(grille), np.nan), np.full(len(grille), np.nan)
    ok = np.isfinite(lat) & np.isfinite(lon)
    ts, la, lo = np.asarray(t)[ok], np.asarray(lat)[ok], np.asarray(lon)[ok]
    if not len(ts):
        return la_g, lo_g, 0
    garde = np.r_[ts[1:] != ts[:-1], True]                             # deux positions au même instant : on garde la dernière
    ts, la, lo = ts[garde], la[garde], lo[garde]
    pas_grille = float(grille[1] - grille[0]) if len(grille) > 1 else 1.0
    if len(ts) == 1:
        k = int(np.argmin(np.abs(grille - ts[0])))
        if abs(grille[k] - ts[0]) <= pas_grille / 2:
            la_g[k], lo_g[k] = la[0], lo[0]
        return la_g, lo_g, 1
    lo_u = np.degrees(np.unwrap(np.radians(lo)))                        # pas de faux saut de 360° au méridien de changement de date
    ecarts = np.diff(ts)
    pas_propre = float(np.median(ecarts)) if len(ecarts) else pas_grille
    tolerance = max(trou_max_pas * pas_propre, pas_grille)
    suiv = np.clip(np.searchsorted(ts, grille, side="left"), 1, len(ts) - 1)
    prec = suiv - 1
    sur_mesure = np.minimum(np.abs(grille - ts[suiv]), np.abs(grille - ts[prec])) < 1e-6
    valide = (grille >= ts[0] - 1e-6) & (grille <= ts[-1] + 1e-6) & (((ts[suiv] - ts[prec]) <= tolerance) | sur_mesure)
    la_g[valide] = np.interp(grille[valide], ts, la)
    lo_g[valide] = (np.interp(grille[valide], ts, lo_u) + 180.0) % 360.0 - 180.0
    return la_g, lo_g, int(len(ts))


def fusionner(mats: list, trou_max_pas: float | None = None, max_objets: int | None = None, max_pas: int | None = None) -> dict | None:
    """Plusieurs matrices (une par table : un objet = une table, avec SA colonne de date et SA colonne de position) -> une seule matrice sur un
    axe de temps COMMUN. Chaque matrice d'entrée porte `source` (nom de la table).

      * toutes les tables sont datées : grille régulière du premier au dernier instant, au pas le plus fin (au plus `max_pas` instants s'il est donné, sinon autant que de lignes lues) ;
        chaque objet est interpolé entre ses propres positions, jamais prolongé avant sa première ni après sa dernière, ni à travers un trou long ;
      * certaines tables sont datées, d'autres non : les tables sans date sont ÉCARTÉES (leur axe n'est pas comparable) et signalées ;
      * aucune table n'est datée : les lignes de même rang sont traitées comme simultanées (comme pour une table seule).
    La matrice rendue ajoute : `sources` (nombre de tables), `sources_ignorees` [noms], `axe_par_lignes`, `positions_reelles`."""
    trou = PARAMETRES["trou_max_pas"] if trou_max_pas is None else float(trou_max_pas)
    mats = [m for m in mats if m and len(m.get("ids", [])) and len(m.get("t", []))]
    if not mats:
        return None
    ignorees, par_lignes = [], False
    datees = [m for m in mats if m.get("temps_reel")]
    if datees and len(datees) < len(mats):
        ignorees = [m.get("source") or "?" for m in mats if not m.get("temps_reel")]
        mats = datees
    elif not datees:
        par_lignes = len(mats) > 1
    dates_manquantes = int(sum(m.get("dates_manquantes", 0) for m in mats))
    if len(mats) == 1:
        m = dict(mats[0])
        m.update({"sources": 1, "sources_ignorees": ignorees, "axe_par_lignes": False, "dates_manquantes": dates_manquantes})
        return m
    ids, noms, cols_lat, cols_lon, reelles, inv = [], [], [], [], 0, []
    if par_lignes:                                                       # même rang de ligne = même instant
        T = max(len(m["t"]) for m in mats)
        t = max(mats, key=lambda m: len(m["t"]))["t"]
        for m in mats:
            for j in range(len(m["ids"])):
                la, lo = np.full(T, np.nan), np.full(T, np.nan)
                la[:len(m["t"])], lo[:len(m["t"])] = m["lat"][:, j], m["lon"][:, j]
                cols_lat.append(la)
                cols_lon.append(lo)
                reelles += int((np.isfinite(la) & np.isfinite(lo)).sum())
            ids.extend(m["ids"])
            noms.extend(m["noms"])
            inv.extend(m.get("inversions") or [0.0] * len(m["ids"]))
    else:
        t0, t1 = min(float(m["t"][0]) for m in mats), max(float(m["t"][-1]) for m in mats)
        pas = [float(np.median(d[d > 0])) for d in (np.diff(m["t"]) for m in mats) if (d > 0).any()]
        dt = min(pas) if pas else float(PARAMETRES["pas_defaut_s"])
        n = int(np.ceil((t1 - t0) / dt)) + 1 if t1 > t0 else 1
        n = min(n, max_pas or sum(len(m["t"]) for m in mats))   # sans plafond demandé : jamais plus d'instants que de lignes lues
        t = np.linspace(t0, t1, n) if n > 1 else np.array([t0])
        for m in mats:
            for j in range(len(m["ids"])):
                la, lo, nb = _reechantillonner(m["t"], m["lat"][:, j], m["lon"][:, j], t, trou)
                cols_lat.append(la)
                cols_lon.append(lo)
                reelles += nb
            ids.extend(m["ids"])
            noms.extend(m["noms"])
            inv.extend(m.get("inversions") or [0.0] * len(m["ids"]))
    cap = max_objets or None   # aucun plafond sauf celui demandé
    return {"t": np.asarray(t, dtype=float), "ids": ids[:cap], "noms": noms[:cap], "lat": np.column_stack(cols_lat[:cap]), "lon": np.column_stack(cols_lon[:cap]),
            "temps_reel": not par_lignes, "dates_manquantes": dates_manquantes, "positions_reelles": reelles, "sources": len(mats),
            "sources_ignorees": ignorees, "axe_par_lignes": par_lignes, "inversions": inv[:cap]}


def scenario(n_objets: int = 6, n_pas: int = 120, graine: int = 7, centre=(48.8566, 2.3522), pas_s: float = 30.0,
             etendue_m: float = 4000.0) -> dict:
    """Trajectoires simulées : les deux premiers objets convergent vers un point de rendez-vous encore à venir (collision à prévoir), le
    quatrième subit un saut de position isolé, le dernier s'immobilise. Bruit de mesure : 3 m."""
    rng = np.random.default_rng(graine)
    n_objets, n_pas = max(2, min(n_objets, LIMITES["objets"])), max(10, min(n_pas, LIMITES["pas"]))
    t = np.arange(n_pas) * pas_s
    t_rdv = (n_pas + 8) * pas_s                       # rendez-vous juste APRÈS la dernière position : collision à prévoir
    X, Y = np.zeros((n_pas, n_objets)), np.zeros((n_pas, n_objets))
    for i in range(n_objets):
        angle = rng.uniform(0, 2 * math.pi)
        depart = np.array([math.cos(angle), math.sin(angle)]) * etendue_m * rng.uniform(0.3, 0.5)
        if i < 2:
            cible, v = np.zeros(2), np.linalg.norm(depart) / t_rdv
            cap = math.atan2(*(cible - depart)[::-1])
            X[:, i], Y[:, i] = depart[0] + math.cos(cap) * v * t, depart[1] + math.sin(cap) * v * t
            continue
        cap, v = rng.uniform(0, 2 * math.pi), rng.uniform(3, 14)
        derive = np.cumsum(rng.normal(0, 0.04, n_pas))
        X[:, i] = depart[0] + np.cumsum(np.cos(cap + derive) * v * pas_s) - np.cos(cap) * v * pas_s
        Y[:, i] = depart[1] + np.cumsum(np.sin(cap + derive) * v * pas_s) - np.sin(cap) * v * pas_s
    if n_objets > 3:
        X[n_pas // 3, 3] += 900.0                       # saut de position isolé
    if n_objets > 4:
        X[-15:, n_objets - 1], Y[-15:, n_objets - 1] = X[-16, n_objets - 1], Y[-16, n_objets - 1]   # arrêt final
    X, Y = X + rng.normal(0, 3, X.shape), Y + rng.normal(0, 3, Y.shape)
    o = (float(centre[0]), float(centre[1]))
    lat, lon = _geo(X, Y, o)
    return {"t": t, "ids": [f"obj_{i + 1}" for i in range(n_objets)], "noms": [f"Objet {i + 1}" for i in range(n_objets)],
            "lat": lat, "lon": lon, "temps_reel": False}


# ── Géométrie ────────────────────────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R_TERRE * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def cap_deg(lat1, lon1, lat2, lon2):
    p1, p2, dl = np.radians(lat1), np.radians(lat2), np.radians(lon2 - lon1)
    return (np.degrees(np.arctan2(np.sin(dl) * np.cos(p2), np.cos(p1) * np.sin(p2) - np.sin(p1) * np.cos(p2) * np.cos(dl))) + 360) % 360


def _plan(lat, lon):
    """(X, Y, origine) : mètres autour du centre des positions connues."""
    o = (float(np.nanmean(lat)), float(np.nanmean(lon)))
    return R_TERRE * math.cos(math.radians(o[0])) * np.radians(lon - o[1]), R_TERRE * np.radians(lat - o[0]), o


def _geo(x, y, o):
    return o[0] + np.degrees(np.asarray(y) / R_TERRE), o[1] + np.degrees(np.asarray(x) / (R_TERRE * math.cos(math.radians(o[0]))))


def _r(v, n=2):
    return None if v is None or not np.isfinite(v) else round(float(v), n)


# ── Par objet ────────────────────────────────────────────────────────────────
def _stats_objet(t, lat, lon, p) -> dict:
    ok = np.isfinite(lat) & np.isfinite(lon)
    idx = np.flatnonzero(ok)
    manquantes_fin = int(len(lat) - 1 - idx[-1]) if len(idx) else int(len(lat))
    base = {"positions": int(len(idx)), "manquantes": int(len(lat) - len(idx)), "manquantes_fin": manquantes_fin, "distance_m": 0.0,
            "droite_m": 0.0, "tortuosite": 1.0, "duree_s": 0.0, "vitesse_moy_ms": 0.0, "vitesse_max_ms": 0.0, "vitesse_med_ms": 0.0,
            "cap_moy_deg": None, "cap_actuel_deg": None, "sauts": 0, "indices_sauts": [], "immobile_depuis_s": 0.0, "rayon_giration_m": 0.0}
    if len(idx) < 2:
        return base
    la, lo, tt = lat[idx], lon[idx], t[idx]
    d = haversine(la[:-1], lo[:-1], la[1:], lo[1:])
    dt = np.maximum(np.diff(tt), 1e-9)
    v = d / dt
    total, droite = float(d.sum()), float(haversine(la[0], lo[0], la[-1], lo[-1]))
    med = float(np.median(v))
    echelle = max(float(np.median(np.abs(v - med))) * 1.4826, 0.25 * med, 0.5)
    saut = v > med + p["facteur_saut"] * echelle
    caps = np.radians(cap_deg(la[:-1], lo[:-1], la[1:], lo[1:]))[~saut] if (~saut).any() else np.radians(cap_deg(la[:-1], lo[:-1], la[1:], lo[1:]))
    arret = v < p["seuil_arret_ms"]
    fin = 0
    for a in arret[::-1]:
        if not a:
            break
        fin += 1
    x, y, _o = _plan(la, lo)
    duree = float(tt[-1] - tt[0])
    base.update(distance_m=_r(total), droite_m=_r(droite), tortuosite=_r(droite / total if total > 0 else 1.0, 3), duree_s=_r(duree),
                vitesse_moy_ms=_r(total / duree if duree > 0 else 0.0, 3), vitesse_max_ms=_r(v[~saut].max() if (~saut).any() else v.max(), 3),
                vitesse_med_ms=_r(med, 3), cap_moy_deg=_r((math.degrees(math.atan2(np.sin(caps).mean(), np.cos(caps).mean())) + 360) % 360, 1),
                cap_actuel_deg=_r(cap_deg(la[-2], lo[-2], la[-1], lo[-1]), 1), sauts=int(saut.sum()),
                indices_sauts=[int(idx[i + 1]) for i in np.flatnonzero(saut)][:20],
                immobile_depuis_s=_r(float(tt[-1] - tt[-1 - fin])) if fin else 0.0,
                rayon_giration_m=_r(math.sqrt(float(np.mean((x - x.mean()) ** 2 + (y - y.mean()) ** 2)))))
    return base


def _vitesses(t, X, Y):
    """(T, N) : vitesse en m/s arrivée à chaque ligne (NaN si l'une des deux positions manque)."""
    v = np.full(X.shape, np.nan)
    if len(t) > 1:
        dt = np.maximum(np.diff(t), 1e-9)[:, None]
        v[1:] = np.hypot(np.diff(X, axis=0), np.diff(Y, axis=0)) / dt
    return v


# ── Rencontres (distance minimale exacte sur chaque segment) ────────────────
def rencontres(t, X, Y, ids, seuil: float, trou_max_s: float | None = None):
    """([rencontre sous le seuil], [paires les plus proches]) — une rencontre = groupe de segments contigus, on garde le minimum.

    Un trou de positions (cellule vide) plus court que `trou_max_s` est enjambé par une interpolation linéaire : la rencontre est alors marquée
    « interpole » (trajet estimé, pas mesuré), et une rencontre à cheval sur le trou reste UN seul événement. Au-delà, le trou coupe la piste.
    None = 5 × le pas médian ; 0 = ne rien enjamber."""
    T, N = X.shape
    if trou_max_s is None:
        dts = np.diff(t)
        dts = dts[dts > 0]
        trou_max_s = 5.0 * float(np.median(dts)) if len(dts) else 0.0
    evts, paires = [], []                                          # (événement, i, j) : l'ordre final est celui de l'ancienne double boucle (distance, puis i, puis j)
    fin = np.isfinite(X) & np.isfinite(Y)
    xmin, xmax = np.where(fin, X, np.inf).min(axis=0), np.where(fin, X, -np.inf).max(axis=0)
    ymin, ymax = np.where(fin, Y, np.inf).min(axis=0), np.where(fin, Y, -np.inf).max(axis=0)
    utile = fin.sum(axis=0) >= 2
    gx = np.maximum(np.maximum(xmin[None, :] - xmax[:, None], xmin[:, None] - xmax[None, :]), 0.0)
    gy = np.maximum(np.maximum(ymin[None, :] - ymax[:, None], ymin[:, None] - ymax[None, :]), 0.0)
    with np.errstate(invalid="ignore"):
        borne = np.hypot(gx, gy)                                   # distance minimale POSSIBLE entre deux trajectoires (leurs cadres englobants)
    borne[~utile, :] = np.inf
    borne[:, ~utile] = np.inf
    iu, ju = np.triu_indices(N, 1)
    b_tous = borne[iu, ju]
    meilleurs = []                                                 # distances minimales des paires les plus proches vues jusqu'ici (triées)
    for k in np.argsort(b_tous, kind="stable"):                    # des plus proches (en cadres) aux plus lointaines
        bk = float(b_tous[k])
        if not np.isfinite(bk) or (len(meilleurs) >= 12 and bk >= seuil and bk >= meilleurs[11]):
            break                                                  # le reste est plus loin que le seuil ET que la 12e paire : rien à y trouver
        i, j = int(iu[k]), int(ju[k])
        ok = np.isfinite(X[:, i]) & np.isfinite(Y[:, i]) & np.isfinite(X[:, j]) & np.isfinite(Y[:, j])
        idx = np.flatnonzero(ok)
        if len(idx) < 2:
            continue
        a, b = idx[:-1], idx[1:]                                   # segments entre deux positions communes consécutives
        garde = (b - a == 1) | (t[b] - t[a] <= trou_max_s)         # lignes voisines, ou trou assez court pour être enjambé
        a, b = a[garde], b[garde]
        if not len(a):
            continue
        pont = (b - a) > 1
        rx0, ry0 = X[a, j] - X[a, i], Y[a, j] - Y[a, i]
        vx, vy = (X[b, j] - X[b, i]) - rx0, (Y[b, j] - Y[b, i]) - ry0
        vv = vx * vx + vy * vy
        s = np.where(vv > 0, np.clip(-(rx0 * vx + ry0 * vy) / np.where(vv > 0, vv, 1.0), 0, 1), 0.0)
        d = np.hypot(rx0 + vx * s, ry0 + vy * s)
        m = int(np.argmin(d))
        paires.append(({"a": ids[i], "b": ids[j], "dmin_m": _r(d[m]), "t": _r(t[a[m]] + s[m] * (t[b[m]] - t[a[m]])), "interpole": bool(pont[m])}, i, j))
        bisect.insort(meilleurs, float(d[m]))
        proche = np.flatnonzero(d < seuil)
        if not len(proche):
            continue
        suite = (np.diff(proche) == 1) & (a[proche[1:]] == b[proche[:-1]])   # contigus : segments voisins qui partagent leur extrémité
        for groupe in np.split(proche, np.flatnonzero(~suite) + 1):
            g = groupe[int(np.argmin(d[groupe]))]
            k0, k1, ss = a[g], b[g], s[g]
            xm = 0.5 * ((X[k0, i] + ss * (X[k1, i] - X[k0, i])) + (X[k0, j] + ss * (X[k1, j] - X[k0, j])))
            ym = 0.5 * ((Y[k0, i] + ss * (Y[k1, i] - Y[k0, i])) + (Y[k0, j] + ss * (Y[k1, j] - Y[k0, j])))
            evts.append(({"a": ids[i], "b": ids[j], "t": _r(t[k0] + ss * (t[k1] - t[k0])), "dmin_m": _r(d[g]), "x": float(xm), "y": float(ym),
                          "interpole": bool(pont[g])}, i, j))
    evts.sort(key=lambda e: (e[0]["dmin_m"], e[1], e[2]))
    paires.sort(key=lambda e: (e[0]["dmin_m"], e[1], e[2]))
    return [e[0] for e in evts], [e[0] for e in paires[:12]]


# ── Prévision : Kalman (OpenCV) puis Monte-Carlo ─────────────────────────────
def _F_Q(dt, sa):
    F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float64)
    q = sa * sa
    Q = q * np.array([[dt ** 4 / 4, 0, dt ** 3 / 2, 0], [0, dt ** 4 / 4, 0, dt ** 3 / 2], [dt ** 3 / 2, 0, dt ** 2, 0], [0, dt ** 3 / 2, 0, dt ** 2]])
    return F, Q


def _filtre(x, y, t, sm, sa):
    """Filtre de Kalman à vitesse constante sur une piste : (état [x, y, vx, vy], covariance 4x4) ou None si < 2 positions.
    OpenCV si disponible ; sinon repli linéaire (dernière vitesse observée, covariance de mesure)."""
    w = LIMITES["fenetre_filtre"]        # l'état courant ne dépend que du mouvement récent : le filtre converge en quelques pas
    x, y, t = x[-w:], y[-w:], t[-w:]
    idx = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if len(idx) < 2:
        return None
    try:
        import cv2
    except ImportError:
        i0, i1 = idx[-2], idx[-1]
        dt = max(t[i1] - t[i0], 1e-9)
        etat = np.array([x[i1], y[i1], (x[i1] - x[i0]) / dt, (y[i1] - y[i0]) / dt])
        return etat, np.diag([sm ** 2, sm ** 2, 2 * sm ** 2 / dt ** 2, 2 * sm ** 2 / dt ** 2])
    kf = cv2.KalmanFilter(4, 2, 0, cv2.CV_64F)
    kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
    kf.measurementNoiseCov = np.eye(2) * sm * sm
    kf.statePost = np.array([[x[idx[0]]], [y[idx[0]]], [0.0], [0.0]], dtype=np.float64)
    kf.errorCovPost = np.diag([sm * sm, sm * sm, 100.0, 100.0]).astype(np.float64)   # vitesse initiale inconnue : ±10 m/s
    dt_prec = None
    for a in range(idx[0] + 1, len(t)):
        dt = t[a] - t[a - 1]
        if dt <= 0:
            continue
        if dt_prec is None or abs(dt - dt_prec) > 1e-9 * max(dt, 1.0):
            kf.transitionMatrix, kf.processNoiseCov = (m.astype(np.float64) for m in _F_Q(dt, sa))
            dt_prec = dt
        kf.predict()
        if np.isfinite(x[a]) and np.isfinite(y[a]):
            kf.correct(np.array([[x[a]], [y[a]]], dtype=np.float64))
        else:                                             # position absente : on garde la prédiction
            kf.statePost, kf.errorCovPost = kf.statePre.copy(), kf.errorCovPre.copy()
    return kf.statePost.ravel().astype(float), kf.errorCovPost.astype(float)


def _sigma_accel_auto(t, X, Y, sm) -> float:
    """Bruit d'accélération estimé sur les données : écart robuste des différences secondes (Δ²x ≈ dt·Δv, variance q·dt³),
    débarrassé du bruit de mesure (6·sm²). Plancher 0,01 m/s² : jamais un modèle parfaitement rigide."""
    dts = np.diff(t)[np.diff(t) > 0]
    if len(t) < 5 or not len(dts):
        return 0.1
    dt = float(np.median(dts))
    d2 = np.concatenate([(A[2:] - 2 * A[1:-1] + A[:-2]).ravel() for A in (X[-LIMITES["pas"]:], Y[-LIMITES["pas"]:])])
    d2 = d2[np.isfinite(d2)]
    if len(d2) < 10:
        return 0.1
    var = (1.4826 * float(np.median(np.abs(d2 - np.median(d2))))) ** 2
    return float(max(math.sqrt(max(var - 6 * sm * sm, 0.0) / dt ** 3), 0.01))


NOMS_MODES = ("rectiligne", "virage_gauche", "virage_droite", "acceleration")


def _F_ct(dt, w):
    """Transition à taux de virage constant w (rad/s, > 0 = à gauche) ; w = 0 redonne le mouvement rectiligne."""
    if abs(w) < 1e-9:
        return _F_Q(dt, 0.0)[0]
    s, c = math.sin(w * dt), math.cos(w * dt)
    return np.array([[1, 0, s / w, -(1 - c) / w], [0, 1, (1 - c) / w, s / w], [0, 0, c, -s], [0, 0, s, c]], dtype=np.float64)


def _imm(x, y, t, sm, sa):
    """IMM (Interacting Multiple Model) sur une piste : rectiligne, virage à gauche, virage à droite (taux = 90e centile des changements de cap
    observés sur la fenêtre) et accélération (bruit ×8), mélangés à chaque pas par leurs probabilités.
    Retourne (probabilités (n,), états (n, 4), covariances (n, 4, 4), [(taux de virage, facteur de bruit)]) ou None si < 2 positions."""
    w = LIMITES["fenetre_imm"]
    x, y, t = x[-w:], y[-w:], t[-w:]
    idx = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if len(idx) < 2:
        return None
    dts = np.maximum(np.diff(t[idx]), 1e-9)
    dx, dy = np.diff(x[idx]), np.diff(y[idx])
    v = np.hypot(dx, dy) / dts
    om = 0.02
    if len(v) >= 3 and ((v[1:] > 0.5) & (v[:-1] > 0.5)).any():
        cap = np.arctan2(dy, dx)
        taux = np.abs((np.diff(cap) + math.pi) % (2 * math.pi) - math.pi) / (0.5 * (dts[1:] + dts[:-1]))
        om = float(np.clip(np.percentile(taux[(v[1:] > 0.5) & (v[:-1] > 0.5)], 90), 0.01, 0.5))
    mods = [(0.0, 1.0), (om, 1.0), (-om, 1.0), (0.0, 8.0)]
    n = len(mods)
    Pi = np.full((n, n), 0.05 / (n - 1))
    np.fill_diagonal(Pi, 0.95)
    R = np.eye(2) * sm * sm
    xs = np.tile([x[idx[0]], y[idx[0]], 0.0, 0.0], (n, 1))
    Ps = np.tile(np.diag([sm * sm, sm * sm, 100.0, 100.0]), (n, 1, 1))     # vitesse initiale inconnue : ±10 m/s
    mu = np.array([0.7, 0.1, 0.1, 0.1])
    for a in range(idx[0] + 1, len(t)):
        dt = t[a] - t[a - 1]
        if dt <= 0:
            continue
        c = Pi.T @ mu                                                        # mélange : chaque modèle repart d'un état combiné
        mij = Pi * mu[:, None] / c[None, :]
        x0, d = mij.T @ xs, xs[None] - (mij.T @ xs)[:, None]
        P0 = np.einsum("ij,ikl->jkl", mij, Ps) + np.einsum("ij,jik,jil->jkl", mij, d, d)
        ll = np.zeros(n)
        for j, (om_j, fq) in enumerate(mods):
            F, Q = _F_ct(dt, om_j), _F_Q(dt, sa * fq)[1]
            xp, Pp = F @ x0[j], F @ P0[j] @ F.T + Q
            if np.isfinite(x[a]) and np.isfinite(y[a]):
                r, S = np.array([x[a], y[a]]) - xp[:2], Pp[:2, :2] + R
                det = max(float(S[0, 0] * S[1, 1] - S[0, 1] * S[1, 0]), 1e-300)          # mesure 2x2 : inverse et déterminant écrits à la main
                Si = np.array([[S[1, 1], -S[0, 1]], [-S[1, 0], S[0, 0]]]) / det
                K = Pp[:, :2] @ Si
                xp, Pp = xp + K @ r, Pp - K @ Pp[:2]
                ll[j] = -0.5 * float(r @ Si @ r) - 0.5 * math.log(det)
            xs[j], Ps[j] = xp, 0.5 * (Pp + Pp.T)          # la mise à jour « P − K·H·P » perd la symétrie à l'arrondi
        mu = c * np.exp(ll - ll.max())
        mu = np.maximum(mu / mu.sum(), 1e-4)
        mu /= mu.sum()
    return mu, xs, Ps, mods


def _racine_cov(M):
    """Facteur A tel que A @ A.T = M pour une covariance 4x4 (tirages : z @ A.T). Cholesky d'abord ; si la matrice n'est pas
    strictement définie positive (Q de bruit d'accélération de rang 2, grands dt, covariance du filtre dégradée par les
    arrondis), repli sur la décomposition propre en ramenant les valeurs propres négatives à 0 : jamais d'exception."""
    M = np.asarray(M, dtype=np.float64)
    M = 0.5 * (M + M.T)
    if not np.isfinite(M).all():
        return np.zeros_like(M)
    try:
        return np.linalg.cholesky(M + 1e-9 * np.eye(M.shape[0]))
    except np.linalg.LinAlgError:
        val, vec = np.linalg.eigh(M)
        return vec * np.sqrt(np.clip(val, 0.0, None))


def _modes(x, y, t, p):
    """(probabilités, états, covariances, modèles) : IMM, ou le seul Kalman rectiligne si p["imm"] est faux ; None si < 2 positions."""
    if p.get("imm", True):
        return _imm(x, y, t, p["sigma_mesure_m"], p["sigma_accel"])
    f = _filtre(x, y, t, p["sigma_mesure_m"], p["sigma_accel"])
    return None if f is None else (np.ones(1), f[0][None], f[1][None], [(0.0, 1.0)])


def _moy_modes(m, dt):
    """État moyen [x, y, vx, vy] de la piste dans dt secondes : les modèles, pondérés par leurs probabilités."""
    return sum(m[0][j] * (_F_ct(dt, om) @ m[1][j]) for j, (om, _fq) in enumerate(m[3]))


def _proba_modes(m):
    d = dict(zip(NOMS_MODES, (_r(v, 3) for v in m[0])))
    d["manoeuvre"] = _r(1 - m[0][0], 3)
    return d


def prevoir(t, X, Y, ids, p) -> dict:
    """Trajectoires prévues (moyenne + rayon à 90 %) et collisions probables par Monte-Carlo sur la covariance du filtre."""
    N = X.shape[1]
    H = int(max(1, min(p["horizon_pas"], LIMITES["horizon_max"])))
    dts = np.diff(t)[np.diff(t) > 0]
    dt = float(np.median(dts)) if len(dts) else float(p["pas_defaut_s"])
    filtres = {i: _modes(X[:, i], Y[:, i], t, p) for i in range(N)}
    actifs = [i for i, f in filtres.items() if f is not None]
    if not actifs:
        return {"horizon_pas": H, "pas_s": dt, "objets": [], "collisions": [], "filtre": "aucun"}
    S = int(max(20, min(p["echantillons"], LIMITES["budget_mc"] // (H * len(actifs)))))
    rng = np.random.default_rng(int(p["graine"]))
    etat, prop = np.zeros((S, len(actifs), 4)), []           # prop[a] = pour chaque modèle : [tirages concernés, F, Lq, moyenne du modèle]
    for a, i in enumerate(actifs):
        mu, xs, Ps, mods = filtres[i]
        choix = rng.choice(len(mu), size=S, p=mu)              # chaque tirage suit UN mode, tiré selon sa probabilité
        prop.append([])
        for j, (om, fq) in enumerate(mods):
            lg = np.flatnonzero(choix == j)
            etat[lg, a] = xs[j] + rng.standard_normal((len(lg), 4)) @ _racine_cov(Ps[j]).T
            prop[a].append([lg, _F_ct(dt, om), _racine_cov(_F_Q(dt, p["sigma_accel"] * fq)[1]), xs[j].copy()])
    pos = np.zeros((H, S, len(actifs), 2))
    chemins = np.zeros((H, len(actifs), 2))
    for k in range(H):
        for a, pa in enumerate(prop):
            for e in pa:
                etat[e[0], a] = etat[e[0], a] @ e[1].T + rng.standard_normal((len(e[0]), 4)) @ e[2].T
                e[3] = e[1] @ e[3]
            chemins[k, a] = sum(w * e[3][:2] for w, e in zip(filtres[actifs[a]][0], pa))
        pos[k] = etat[..., :2]
    var = pos.var(axis=1)                                    # (H, n, 2)
    r90 = np.sqrt(4.605 * 0.5 * var.sum(axis=2))             # (H, n)
    sortie = {"horizon_pas": H, "pas_s": _r(dt), "echantillons": S, "filtre": "imm" if p.get("imm", True) else ("opencv" if _cv2_ok() else "lineaire"),
              "objets": [{"id": ids[i], "xy": chemins[:, a].tolist(), "rayon90_m": [_r(v, 1) for v in r90[:, a]], "modes": _proba_modes(filtres[i])} for a, i in enumerate(actifs)],
              "collisions": []}
    seuil = float(p["seuil_collision_m"])
    bas, haut = pos.min(axis=1), pos.max(axis=1)              # (H, n, 2) : cadre englobant des tirages de chaque objet, à chaque pas
    for a in range(len(actifs)):
        suivants = np.arange(a + 1, len(actifs))
        if not len(suivants):
            break
        ecart = np.maximum(np.maximum(bas[:, suivants, :] - haut[:, a, :][:, None, :], bas[:, a, :][:, None, :] - haut[:, suivants, :]), 0.0)
        # deux tirages ne peuvent se toucher que si leurs cadres sont à moins du seuil : les autres paires ont une probabilité exactement nulle
        for b in suivants[(np.hypot(ecart[..., 0], ecart[..., 1]) < seuil).any(axis=0)]:
            d = np.hypot(pos[:, :, a, 0] - pos[:, :, b, 0], pos[:, :, a, 1] - pos[:, :, b, 1])   # (H, S)
            touche = d < seuil
            atteint = touche.any(axis=0)
            proba = float(atteint.mean())
            if proba < p["proba_min"]:
                continue
            premier = np.argmax(touche, axis=0)[atteint]
            k = int(np.median(premier))
            dm = np.hypot(chemins[:, a, 0] - chemins[:, b, 0], chemins[:, a, 1] - chemins[:, b, 1])
            sortie["collisions"].append({"a": ids[actifs[a]], "b": ids[actifs[b]], "proba": _r(proba, 3), "dans_s": _r((k + 1) * dt),
                                         "dmin_moyen_m": _r(dm.min()), "x": float(0.5 * (chemins[k, a, 0] + chemins[k, b, 0])),
                                         "y": float(0.5 * (chemins[k, a, 1] + chemins[k, b, 1]))})
    sortie["collisions"].sort(key=lambda c: -c["proba"])
    return sortie


def _cv2_ok() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def erreur_prevision(t, X, Y, p, k: int = 3, ids=None) -> dict | None:
    """Validation sur le passé : on masque les k dernières lignes, on prévoit, on compare à la réalité et à « l'objet ne bouge plus »."""
    T = len(t)
    if T < 8:
        return None
    k = max(1, min(k, T // 4))
    e_mod, e_cv, e_stat, par_objet = [], [], [], []
    for i in range(X.shape[1]):
        m = _modes(X[:T - k, i], Y[:T - k, i], t[:T - k], p)
        f = _filtre(X[:T - k, i], Y[:T - k, i], t[:T - k], p["sigma_mesure_m"], p["sigma_accel"])
        if m is None or f is None or not (np.isfinite(X[-1, i]) and np.isfinite(Y[-1, i])):
            continue
        el, s0 = t[-1] - t[T - k - 1], f[0]
        pm, ps = _moy_modes(m, el), _moy_modes(m, 0.0)
        e_mod.append(math.hypot(pm[0] - X[-1, i], pm[1] - Y[-1, i]))
        e_cv.append(math.hypot(s0[0] + s0[2] * el - X[-1, i], s0[1] + s0[3] * el - Y[-1, i]))
        e_stat.append(math.hypot(ps[0] - X[-1, i], ps[1] - Y[-1, i]))
        par_objet.append({"id": ids[i] if ids is not None else i, "erreur_m": _r(e_mod[-1]), "erreur_stationnaire_m": _r(e_stat[-1])})
    if not e_mod:
        return None
    rm, rc, rs = (math.sqrt(float(np.mean(np.square(e)))) for e in (e_mod, e_cv, e_stat))
    return {"pas_masques": k, "rmse_m": _r(rm), "rmse_cv_m": _r(rc), "rmse_stationnaire_m": _r(rs), "gain": _r(1 - rm / rs if rs > 0 else 0.0, 3), "objets": len(e_mod),
            "par_objet": par_objet}      # écart de chaque objet (gardé avec la simulation enregistrée : matière d'un futur correcteur)


# ── Géostatistique ───────────────────────────────────────────────────────────
def _lisser(z, sigma=1.5):
    r = int(3 * sigma)
    noyau = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    noyau /= noyau.sum()
    for axe in (0, 1):
        z = np.apply_along_axis(lambda v: np.convolve(v, noyau, mode="same"), axe, z)
    return z


def _echantillon(n, maxi):
    return np.arange(n) if not maxi or n <= maxi else np.linspace(0, n - 1, maxi).astype(int)   # maxi None = tout, sans échantillonnage


def geostatistiques(X, Y, V, o, grille: int = 40) -> dict:
    ok = np.isfinite(X) & np.isfinite(Y)
    px, py, pv = X[ok], Y[ok], V[ok]
    if len(px) < 3:
        return {"points": int(len(px))}
    cov = np.cov(np.vstack([px, py]))
    val, vec = np.linalg.eigh(cov)
    gx, gy = _geo(px.mean(), py.mean(), o)
    sortie = {"points": int(len(px)), "centre": [_r(gx, 6), _r(gy, 6)],
              "ellipse": {"demi_grand_m": _r(math.sqrt(max(val[1], 0))), "demi_petit_m": _r(math.sqrt(max(val[0], 0))),
                          "angle_deg": _r(math.degrees(math.atan2(vec[1, 1], vec[0, 1])), 1)}}
    # densité lissée sur une grille régulière (dans le plan, donc linéaire en lat/lon)
    mx, my = max(px.max() - px.min(), 1.0) * 0.1, max(py.max() - py.min(), 1.0) * 0.1
    x0, x1, y0, y1 = px.min() - mx, px.max() + mx, py.min() - my, py.max() + my
    h, _, _ = np.histogram2d(py, px, bins=grille, range=[[y0, y1], [x0, x1]])
    z = _lisser(h)
    zmax = float(z.max()) or 1.0
    la0, lo0 = _geo(x0, y0, o)
    la1, lo1 = _geo(x1, y1, o)
    sortie["densite"] = {"lat0": _r(la0, 6), "lon0": _r(lo0, 6), "lat1": _r(la1, 6), "lon1": _r(lo1, 6), "n": grille,
                         "z": [_r(v, 2) for v in (z / zmax).ravel()]}
    # points chauds : composantes connexes des cellules > 50 % du maximum
    chaud, vu, chauds = z / zmax > 0.5, np.zeros(z.shape, bool), []
    cx, cy = (x1 - x0) / grille, (y1 - y0) / grille
    for r0 in range(grille):
        for c0 in range(grille):
            if not chaud[r0, c0] or vu[r0, c0]:
                continue
            pile, cellules = [(r0, c0)], []
            vu[r0, c0] = True
            while pile:
                r, c = pile.pop()
                cellules.append((r, c))
                for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                    if 0 <= rr < grille and 0 <= cc < grille and chaud[rr, cc] and not vu[rr, cc]:
                        vu[rr, cc] = True
                        pile.append((rr, cc))
            poids = np.array([h[r, c] for r, c in cellules])
            if poids.sum() <= 0:
                continue
            xs = x0 + (np.array([c for _r_, c in cellules]) + 0.5) * cx
            ys = y0 + (np.array([r for r, _c in cellules]) + 0.5) * cy
            hx, hy = _geo(np.average(xs, weights=poids), np.average(ys, weights=poids), o)
            chauds.append({"lat": _r(hx, 6), "lon": _r(hy, 6), "part": _r(poids.sum() / len(px), 3),
                           "rayon_m": _r(math.sqrt(len(cellules) * cx * cy / math.pi))})
    chauds.sort(key=lambda c: -c["part"])
    sortie["points_chauds"] = chauds[:5]
    # I de Moran et variogramme de la vitesse (sous-échantillon : O(n²))
    ok_v = np.isfinite(pv)
    ix = np.flatnonzero(ok_v)[_echantillon(int(ok_v.sum()), LIMITES["points_geostat"])] if ok_v.sum() >= 5 else []
    if len(ix) >= 5:
        qx, qy, qv = px[ix], py[ix], pv[ix]
        D = np.hypot(qx[:, None] - qx[None, :], qy[:, None] - qy[None, :])
        W = np.where(D > 0, 1.0 / np.where(D > 0, D, 1.0), 0.0)
        W /= np.maximum(W.sum(axis=1, keepdims=True), 1e-12)
        zc = qv - qv.mean()
        den = float((zc ** 2).sum())
        sortie["moran_vitesse"] = {"I": _r((zc @ W @ zc) / den if den > 0 else 0.0, 3), "attendu": _r(-1 / (len(qv) - 1), 3), "n": int(len(qv))}
        iu = np.triu_indices(len(qv), 1)
        dd, gg = D[iu], 0.5 * (qv[iu[0]] - qv[iu[1]]) ** 2
        bornes = np.linspace(0, max(dd.max() / 2, 1.0), 9)
        vario = []
        for a, b in zip(bornes[:-1], bornes[1:]):
            m = (dd >= a) & (dd < b)
            if m.sum() >= 5:
                vario.append({"h_m": _r((a + b) / 2), "gamma": _r(gg[m].mean(), 4), "n": int(m.sum())})
        sortie["variogramme_vitesse"] = vario
    return sortie


def _clark_evans(X, Y) -> dict | None:
    """Dispersion ACTUELLE du groupe : dernière position connue de chaque objet (R < 1 groupé, ≈ 1 aléatoire, > 1 dispersé)."""
    px, py = [], []
    for i in range(X.shape[1]):
        ok = np.flatnonzero(np.isfinite(X[:, i]) & np.isfinite(Y[:, i]))
        if len(ok):
            px.append(X[ok[-1], i])
            py.append(Y[ok[-1], i])
    n = len(px)
    px, py = np.array(px), np.array(py)
    aire = (px.max() - px.min()) * (py.max() - py.min()) if n else 0.0
    if n < 5 or aire <= 0:
        return None
    D = np.hypot(px[:, None] - px[None, :], py[:, None] - py[None, :])
    np.fill_diagonal(D, np.inf)
    obs, attendu = float(D.min(axis=1).mean()), 0.5 / math.sqrt(n / aire)
    return {"R": _r(obs / attendu, 3), "z": _r((obs - attendu) / (0.26136 / math.sqrt(n * n / aire)), 2), "n": n}


# ── Point d'entrée ───────────────────────────────────────────────────────────
# ── Une colonne = la trajectoire d'UN objet, ou des positions de PLUSIEURS objets ? ──────────────────────────────────
MODES = ("trajectoire", "incoherente", "positions_distinctes")


def diagnostiquer(t, x, y, temps_reel: bool, inversion: float, p: dict) -> dict:
    """Une colonne de position peut être la trajectoire d'un seul objet (une ligne = un instant) OU contenir des positions d'objets différents
    (table « client » : chaque ligne est un client, chacun avec SA position). Ce diagnostic juge la suite des positions de la colonne, sans rien supposer :

      * contradictions DURES (la suite ne peut pas être celle d'un seul objet) : dates identiques sur une ligne sur cinq au moins, dates qui reculent
        d'une ligne à l'autre (≥ 20 %), déplacement impossible entre deux lignes datées (> `vitesse_max_ms` sur ≥ 10 % des déplacements) ;
      * indices DOUX : sauts de vitesse fréquents (≥ 15 %), changements de direction aléatoires (médiane > 60° sur au moins 4 virages), trace erratique (sur 4 déplacements de suite, la distance parcourue en ligne droite est moins de la moitié du chemin) ;
      * verdict : « trajectoire » (aucune contradiction : on trace, on prévoit) · « positions_distinctes » (une contradiction dure ou deux indices :
        plusieurs objets, on affiche les positions, ni trajectoire ni prévision) · « incoherente » (un seul indice doux : suite douteuse, on affiche les
        positions et on dit pourquoi, sans prévision).
    `mode_positions` = « trajectoire » ou « points » force le verdict (la personne sait ce que contient sa colonne). Les dates ne sont jugées que si
    elles existent (`temps_reel`) : sans date, seule la géométrie parle. Retourne aussi `ecarts` : indices (dans l'axe du temps) des positions dont la
    date ne correspond pas à celle d'une trajectoire."""
    ok = np.isfinite(x) & np.isfinite(y)
    idx = np.flatnonzero(ok)
    forcer = p.get("mode_positions")
    sortie = {"mode": "trajectoire", "raisons": [], "forcee": False, "ecarts": []}
    if forcer in ("trajectoire", "points"):
        sortie.update(mode="trajectoire" if forcer == "trajectoire" else "positions_distinctes", forcee=True)
        return sortie
    if len(idx) < 3:
        return sortie
    xs, ys, ts = x[idx], y[idx], np.asarray(t)[idx]
    d, dt = np.hypot(np.diff(xs), np.diff(ys)), np.diff(ts)
    dures, douces, ecarts = [], [], set()
    if temps_reel:
        egales = dt <= 0
        if egales.mean() >= 0.2:
            dures.append({"code": "dates_identiques", "valeur": _r(float(egales.mean()), 3)})
            ecarts.update(int(i) for i in idx[1:][egales])
        if inversion >= 0.2:
            dures.append({"code": "dates_desordonnees", "valeur": _r(float(inversion), 3)})
        positif = dt > 0
        if positif.any():
            impossible = (d[positif] / dt[positif]) > float(p.get("vitesse_max_ms", 280.0))
            if impossible.mean() >= 0.1:
                dures.append({"code": "deplacement_impossible", "valeur": _r(float(impossible.mean()), 3)})
                ecarts.update(int(i) for i in idx[1:][positif][impossible])
    positif = dt > 0
    if positif.sum() >= 5:
        v = d[positif] / dt[positif]
        med = float(np.median(v))
        echelle = max(float(np.median(np.abs(v - med))) * 1.4826, 0.25 * med, 0.5)
        sauts = float((v > med + p.get("facteur_saut", 6.0) * echelle).mean())
        if sauts >= 0.15:
            douces.append({"code": "sauts_frequents", "valeur": _r(sauts, 3)})
    bouge = d > max(2.0 * float(p.get("sigma_mesure_m", 5.0)), 1.0)
    if bouge.sum() >= 5:
        cap = np.arctan2(np.diff(ys)[bouge], np.diff(xs)[bouge])
        virage = np.degrees(np.abs((np.diff(cap) + np.pi) % (2 * np.pi) - np.pi))
        if len(virage) >= 4 and float(np.median(virage)) > 60.0:
            douces.append({"code": "cap_aleatoire", "valeur": _r(float(np.median(virage)), 1)})
    if bouge.sum() >= 8 and len(xs) >= 10:                                       # trace erratique : sur 4 déplacements consécutifs, un objet qui roule avance ; des objets différents ne vont nulle part
        cum = np.r_[0.0, np.cumsum(d)]
        net, chemin = np.hypot(xs[4:] - xs[:-4], ys[4:] - ys[:-4]), cum[4:] - cum[:-4]
        efficacite = float(np.median(net[chemin > 0] / chemin[chemin > 0])) if (chemin > 0).any() else 1.0
        if efficacite < 0.5:
            douces.append({"code": "trace_erratique", "valeur": _r(efficacite, 3)})
    sortie["raisons"] = dures + douces
    sortie["ecarts"] = sorted(ecarts)[:200]
    if dures or len(douces) >= 2:
        sortie["mode"] = "positions_distinctes"
    elif douces:
        sortie["mode"] = "incoherente"
    return sortie


def plage_temps(t, lat, lon, temps_reel: bool, ids=None, noms=None, lignes_total: int | None = None) -> dict:
    """Plage de temps RÉELLE des données lues : date la plus ancienne, la plus récente, durée, pas médian, et pour chaque objet sa première et sa
    dernière position datée (= sa position actuelle). Sans dates exploitables (axe par pas fixe) : seulement le nombre d'instants.
    `lignes_total` > instants lus : la lecture est tronquée aux dernières lignes, la plage commence donc après la vraie première date de la table."""
    t = np.asarray(t, dtype=float)
    out = {"temps_reel": bool(temps_reel), "instants": int(len(t)), "debut_ts": None, "fin_ts": None, "duree_s": 0.0, "pas_median_s": None,
           "tronquee": bool(lignes_total and lignes_total > len(t)), "lignes_total": lignes_total, "objets": []}
    if len(t):
        out["duree_s"] = _r(float(t[-1] - t[0]), 3)
        d = np.diff(t)
        d = d[d > 0]
        out["pas_median_s"] = _r(float(np.median(d)), 3) if len(d) else None
        if temps_reel:
            out["debut_ts"], out["fin_ts"] = _r(float(np.nanmin(t)), 3), _r(float(np.nanmax(t)), 3)
    ok = np.isfinite(lat) & np.isfinite(lon) if getattr(lat, "ndim", 0) == 2 else np.zeros((len(t), 0), dtype=bool)
    for j in range(ok.shape[1]):
        v = np.flatnonzero(ok[:, j])
        if not len(v):
            out["objets"].append({"id": ids[j] if ids else j, "nom": noms[j] if noms else None, "positions": 0, "premiere_ts": None, "derniere_ts": None, "actuelle": None})
            continue
        k = int(v[-1])
        out["objets"].append({"id": ids[j] if ids else j, "nom": noms[j] if noms else None, "positions": int(len(v)),
                              "premiere_ts": _r(float(t[v[0]]), 3) if temps_reel else None, "derniere_ts": _r(float(t[k]), 3) if temps_reel else None,
                              "actuelle": [_r(float(lat[k, j]), 6), _r(float(lon[k, j]), 6)], "indice_actuel": k})
    return out


def analyser(mat: dict, **params) -> dict:
    p = {**PARAMETRES, **{k: v for k, v in params.items() if v is not None}}
    t, lat, lon, ids, noms = mat["t"], mat["lat"], mat["lon"], mat["ids"], mat["noms"]
    T, N = lat.shape if lat.ndim == 2 else (0, 0)
    if T < 2 or N < 1 or not np.isfinite(lat).any():
        return {"vide": True, "resume": {"objets": N, "positions": 0}, "objets": [], "t": [], "alertes": [], "rencontres": [], "prevision": {}}
    X, Y, o = _plan(lat, lon)
    inv = list(mat.get("inversions") or []) + [0.0] * N
    diags = [diagnostiquer(t, X[:, i], Y[:, i], bool(mat.get("temps_reel")), inv[i], p) for i in range(N)]
    traj = [i for i, dg in enumerate(diags) if dg["mode"] == "trajectoire"]      # seules ces colonnes sont de vraies trajectoires : trace, rencontres, prévision
    ids_traj = [ids[i] for i in traj]
    Xt, Yt = X[:, traj], Y[:, traj]
    if p["sigma_accel"] is None:
        p["sigma_accel"] = _sigma_accel_auto(t, Xt, Yt, p["sigma_mesure_m"]) if traj else 1.0
    V = _vitesses(t, X, Y)
    V[:, [i for i in range(N) if i not in traj]] = np.nan                        # la « vitesse » entre deux objets différents n'existe pas
    stats = [_stats_objet(t, lat[:, i], lon[:, i], p) for i in range(N)]
    for i in range(N):
        if i not in traj:
            stats[i].update(distance_m=0.0, droite_m=0.0, tortuosite=1.0, duree_s=0.0, vitesse_moy_ms=0.0, vitesse_max_ms=0.0, vitesse_med_ms=0.0,
                            cap_moy_deg=None, cap_actuel_deg=None, sauts=0, indices_sauts=[], immobile_depuis_s=0.0)
    dts = np.diff(t)
    dts = dts[dts > 0]
    trou_s = float(p["trou_max_pas"]) * float(np.median(dts)) if len(dts) else 0.0
    evts, proches = rencontres(t, Xt, Yt, ids_traj, float(p["seuil_collision_m"]), trou_s) if len(traj) > 1 else ([], [])
    for e in evts:
        e["lat"], e["lon"] = (_r(v, 6) for v in _geo(e.pop("x"), e.pop("y"), o))
    prev = prevoir(t, Xt, Yt, ids_traj, p) if traj else {"horizon_pas": int(p["horizon_pas"]), "pas_s": 0.0, "objets": [], "collisions": [], "filtre": "aucun", "echantillons": 0}
    for objet in prev["objets"]:
        xy = np.array(objet.pop("xy"))
        la, lo = _geo(xy[:, 0], xy[:, 1], o)
        objet["chemin"] = [[_r(a, 6), _r(b, 6)] for a, b in zip(la, lo)]
    for c in prev["collisions"]:
        c["lat"], c["lon"] = (_r(v, 6) for v in _geo(c.pop("x"), c.pop("y"), o))
    geostat = geostatistiques(X, Y, V, o)
    ce = _clark_evans(X, Y)
    if ce:
        geostat["clark_evans"] = ce
    err = erreur_prevision(t, Xt, Yt, p, ids=ids_traj) if traj else None
    # sortie : tous les instants et toutes les positions (LIMITES['pas_sortie'] / ['points_affiches'] = None) ; un nombre remet un échantillonnage
    sel = _echantillon(T, LIMITES["pas_sortie"])
    objets = [{"id": ids[i], "nom": noms[i], "stats": stats[i], "mode": diags[i]["mode"], "raisons": diags[i]["raisons"], "forcee": diags[i]["forcee"],
               "lat": [_r(v, 6) for v in lat[sel, i]], "lon": [_r(v, 6) for v in lon[sel, i]]} for i in range(N)]
    for i in range(N):
        if i in traj:
            continue
        valides = np.flatnonzero(np.isfinite(lat[:, i]) & np.isfinite(lon[:, i]))      # toutes les positions (pas seulement les instants décimés), avec leur date
        ecarts = set(diags[i]["ecarts"])
        objets[i]["points"] = [[_r(lat[k, i], 6), _r(lon[k, i], 6), _r(float(t[k]), 3) if mat.get("temps_reel") else None, k in ecarts, int(k)]
                               for k in valides[_echantillon(len(valides), LIMITES["points_affiches"])]]
        objets[i]["positions_total"] = int(len(valides))
    alertes = []
    for e in evts[:20]:
        alertes.append({"type": "rencontre", "gravite": "critique" if e["dmin_m"] < p["seuil_collision_m"] / 2 else "attention", "a": e["a"], "b": e["b"],
                        "t": e["t"], "valeur": e["dmin_m"], "interpole": e.get("interpole", False), "lat": e["lat"], "lon": e["lon"]})
    for c in prev["collisions"][:20]:
        alertes.append({"type": "collision_prevue", "gravite": "critique" if c["proba"] >= 0.6 else "attention", "a": c["a"], "b": c["b"],
                        "valeur": c["proba"], "dans_s": c["dans_s"], "lat": c["lat"], "lon": c["lon"]})
    nb_alertes_mode = 0
    for i in range(N):
        if i not in traj and diags[i]["mode"] in ("positions_distinctes", "incoherente") and nb_alertes_mode < 20:
            nb_alertes_mode += 1
            alertes.append({"type": "positions_distinctes" if diags[i]["mode"] == "positions_distinctes" else "suite_incoherente", "gravite": "attention" if not diags[i]["forcee"] else "info",
                            "a": ids[i], "valeur": objets[i]["positions_total"], "raisons": [r["code"] for r in diags[i]["raisons"]], "forcee": diags[i]["forcee"]})
    for i, s in enumerate(stats):
        if i not in traj:
            continue
        if s["sauts"]:
            alertes.append({"type": "saut_position", "gravite": "attention", "a": ids[i], "valeur": s["sauts"]})
        if s["immobile_depuis_s"] and s["immobile_depuis_s"] >= 5 * (float(np.median(np.diff(t))) if T > 1 else 1):
            alertes.append({"type": "immobile", "gravite": "info", "a": ids[i], "valeur": s["immobile_depuis_s"]})
        if s["manquantes_fin"] >= 3:
            alertes.append({"type": "position_manquante", "gravite": "attention", "a": ids[i], "valeur": s["manquantes_fin"]})
    if mat.get("sources_ignorees"):
        alertes.append({"type": "source_ignoree", "gravite": "attention", "valeur": len(mat["sources_ignorees"]), "noms": list(mat["sources_ignorees"])})
    if mat.get("axe_par_lignes"):
        alertes.append({"type": "axe_par_lignes", "gravite": "info", "valeur": int(mat.get("sources", 0))})
    if mat.get("dates_manquantes"):
        alertes.append({"type": "date_manquante", "gravite": "attention", "valeur": int(mat["dates_manquantes"]), "pas_fixe": not mat.get("temps_reel")})
    if N >= 4 and geostat.get("points_chauds") and geostat["points_chauds"][0]["part"] >= 0.5:
        h = geostat["points_chauds"][0]
        alertes.append({"type": "concentration", "gravite": "info", "valeur": h["part"], "lat": h["lat"], "lon": h["lon"]})
    tot = [stats[i]["distance_m"] for i in traj]
    resume = {"objets": N, "positions": int(mat["positions_reelles"]) if mat.get("positions_reelles") is not None else int(np.isfinite(lat).sum()), "sources": int(mat.get("sources", 1)), "pas": T, "distance_m": _r(sum(tot)),
              "vitesse_moy_ms": _r(float(np.mean([stats[i]["vitesse_moy_ms"] for i in traj])), 3) if traj else 0.0,
              "trajectoires": len(traj), "positions_distinctes": sum(1 for dg in diags if dg["mode"] == "positions_distinctes"),
              "incoherentes": sum(1 for dg in diags if dg["mode"] == "incoherente"),
              "rencontres": len(evts), "rencontre_min_m": evts[0]["dmin_m"] if evts else (proches[0]["dmin_m"] if proches else None),
              "collisions_prevues": len(prev["collisions"]), "proba_max": prev["collisions"][0]["proba"] if prev["collisions"] else 0.0,
              "sauts": sum(s["sauts"] for s in stats), "immobiles": sum(1 for a in alertes if a["type"] == "immobile"),
              "manquantes": sum(1 for a in alertes if a["type"] == "position_manquante"),
              "concentration_part": (geostat.get("points_chauds") or [{}])[0].get("part", 0.0),
              "clark_evans": (ce or {}).get("R"), "erreur_prevision_m": (err or {}).get("rmse_m"), "gain_prevision": (err or {}).get("gain"),
              "dates_manquantes": int(mat.get("dates_manquantes", 0)),
              "fin_ts": _r(float(t[-1]), 3) if mat.get("temps_reel") else None}
    return {"vide": False, "temps_reel": bool(mat.get("temps_reel")), "t": [_r(float(v), 3) for v in t[sel]], "origine": [_r(o[0], 6), _r(o[1], 6)],
            "parametres": {k: _r(p[k], 4) if isinstance(p[k], float) else p[k] for k in ("seuil_collision_m", "horizon_pas", "sigma_mesure_m", "sigma_accel", "echantillons", "mode_positions")},
            "objets": objets, "rencontres": evts[:50], "paires_proches": proches, "prevision": prev, "geostat": geostat,
            "validation": err, "alertes": alertes, "resume": resume,
            "plage": plage_temps(t, lat, lon, bool(mat.get("temps_reel")), ids, noms, mat.get("lignes_total"))}
