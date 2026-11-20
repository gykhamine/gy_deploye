"""LOGISTIQUE : tournées de livraison optimisées, flotte, dépôt (module PUR : numpy seulement, aucun import Django).

GÉNÉRALISATION (2026-09-29, moteur stratégique) : flotte hétérogène (capacité, vitesse, autonomie, spécialités, mode route|rail|mer|air, dépôt de retour et
disponibilité par véhicule), fenêtres horaires complètes (ouverture : le véhicule attend ; échéance), priorités, durées d'arrêt par commande, spécialités exigées,
zones interdites (cercles contournés par leurs tangentes, par mode), et jusqu'à 5 000 commandes / 500 véhicules par décomposition géographique en groupes résolus
séparément (bissections k-means sur la sphère : d'un quartier à la planète). Les paramètres facultatifs de chaque objet gardent le comportement d'origine quand ils manquent.

Modèle : les OBJETS SUIVIS de la table courante (colonnes de position) sont les VÉHICULES ; les lignes d'une autre table (une colonne de
position + une quantité + une échéance facultative) sont les COMMANDES à livrer. Le dépôt est un point (choisi sur la carte, sinon le
point qui minimise la distance pondérée aux commandes).

  planifier(commandes, vehicules, depot, **params) -> plan complet (tournées, étapes avec heure d'arrivée, retards, non-affectées,
                                                     gain contre l'ordre de saisie, taille de flotte, dépôt conseillé, alertes)

Méthode : distances grand cercle × coefficient de détour du mode (0,3 de plus que la droite par défaut pour la route), insertion la moins
coûteuse (commandes lourdes d'abord) puis amélioration locale (2-opt dans une tournée, déplacement et échange entre tournées) tant
que le coût baisse, dans un budget de temps. Coût = distance + PENALITE_RETARD × secondes de retard : une livraison tardive peut
se payer par un détour, jamais gratuitement. Un véhicule qui a déjà roulé repart de sa dernière position connue (`depuis_position`).
Rien n'est stocké ici : la persistance est dans engine/geo_donnees.py.
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t as _i18n_t, tf

import math
import time

import numpy as np

from . import geospatial as _g

LIMITES = {"commandes": 5000, "vehicules": 500, "zones": 30, "budget_s": 2.5, "budget_max_s": 20.0, "passes": 25,
           "groupe": 80, "seuil_groupes": 150, "rotations": 3, "flotte_reprise": 120}
PARAMETRES = {
    "capacite": None,        # charge maximale d'un véhicule ; None = estimée (total / véhicules + 15 %, au moins la plus grosse commande)
    "service_s": 300.0,      # durée d'un arrêt de livraison
    "detour": 1.3,           # route / vol d'oiseau (modes terrestres « route » seulement ; mer, air et rail ont le leur, voir MODES)
    "budget_s": None,        # temps de calcul : None = 2,5 s, et jusqu'à 20 s selon la taille du problème
    "vitesse_ms": None,      # None = vitesse médiane observée des véhicules (bornée), sinon 40 km/h
    "retour_depot": True,
    "depuis_position": True,
    "penalite_retard": 10.0, # mètres équivalents par seconde de retard
    "alerte_depot": 0.15,    # gain de dépôt qui justifie une alerte
}
VITESSE_DEFAUT_MS = 40 / 3.6
# Modes de transport : coefficient de détour (parcours réel / grand cercle), vitesse par défaut, bornes de vitesse observée plausibles (m/s).
MODES = {"route": {"detour": 1.3, "vitesse_ms": VITESSE_DEFAUT_MS, "borne": (3.0, 40.0)},
         "rail": {"detour": 1.15, "vitesse_ms": 22.0, "borne": (3.0, 90.0)},
         "mer": {"detour": 1.08, "vitesse_ms": 7.7, "borne": (1.0, 25.0)},
         "air": {"detour": 1.0, "vitesse_ms": 70.0, "borne": (20.0, 400.0)}}


def vitesse_flotte(vehicules) -> float:
    v = [x["vitesse_ms"] for x in vehicules if x.get("vitesse_ms") and 3.0 <= x["vitesse_ms"] <= 40.0]
    return float(np.median(v)) if v else VITESSE_DEFAUT_MS


# ── Dépôt : médiane géométrique pondérée (Weiszfeld) ─────────────────────────
def depot_conseille(commandes: list, depot: tuple | None = None) -> dict | None:
    pts = [(c["lat"], c["lon"], max(float(c.get("demande") or 1.0), 1e-6)) for c in commandes]
    if not pts:
        return None
    lat, lon = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    w = np.array([p[2] for p in pts])
    X, Y, o = _g._plan(lat, lon)
    x, y = float(np.average(X, weights=w)), float(np.average(Y, weights=w))
    for _ in range(60):
        d = np.maximum(np.hypot(X - x, Y - y), 1.0)
        nx, ny = float(np.sum(w * X / d) / np.sum(w / d)), float(np.sum(w * Y / d) / np.sum(w / d))
        if math.hypot(nx - x, ny - y) < 0.5:
            x, y = nx, ny
            break
        x, y = nx, ny
    la, lo = _g._geo(x, y, o)
    sortie = {"lat": round(float(la), 6), "lon": round(float(lo), 6)}
    if depot:
        actuel = float(np.sum(w * _g.haversine(lat, lon, depot[0], depot[1])))
        propose = float(np.sum(w * _g.haversine(lat, lon, sortie["lat"], sortie["lon"])))
        sortie["gain"] = round(1 - propose / actuel, 3) if actuel > 0 else 0.0
    return sortie


# ── Zones interdites (cercles) : contournement par les tangentes ─────────────
def _plan_zone(lat, lon, z):
    """Mètres autour du centre de la zone (plan local : exact près de la zone, là où le contournement se joue)."""
    dl = (np.asarray(lon, float) - z["lon"] + 180.0) % 360.0 - 180.0
    return R_TERRE_M * math.cos(math.radians(z["lat"])) * np.radians(dl), R_TERRE_M * np.radians(np.asarray(lat, float) - z["lat"])


R_TERRE_M = _g.R_TERRE


def zones_valides(zones) -> list:
    """Zones {lat, lon, rayon_m, nom?, modes?} bien formées (bornées) ; le reste est ignoré."""
    sortie = []
    for z in (zones or [])[:LIMITES["zones"]]:
        try:
            lat, lon, r = float(z["lat"]), float(z["lon"]), float(z["rayon_m"])
        except (TypeError, ValueError, KeyError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180 and 1.0 <= r <= 5_000_000.0:
            modes = [m for m in (z.get("modes") or []) if m in MODES]
            sortie.append({"lat": lat, "lon": lon, "rayon_m": r, "nom": str(z.get("nom") or f"Zone {len(sortie) + 1}"), "modes": modes})
    return sortie


def _dans_zones(lat, lon, zones) -> np.ndarray:
    dedans = np.zeros(len(lat), bool)
    for z in zones:
        X, Y = _plan_zone(lat, lon, z)
        dedans |= np.hypot(X, Y) < z["rayon_m"]
    return dedans


def _facteurs_zones(lat, lon, zones) -> np.ndarray:
    """Matrice (n, n) ≥ 1 : longueur du plus court chemin qui contourne les zones / longueur de la droite (tangentes + arc de cercle).
    Un point déjà dans une zone (un véhicule qui en sort) n'est pas contraint par elle."""
    n = len(lat)
    F = np.ones((n, n))
    for z in zones:
        X, Y = _plan_zone(lat, lon, z)
        r = z["rayon_m"]
        d = np.hypot(X, Y)
        dehors = d >= r
        dx, dy = X[None, :] - X[:, None], Y[None, :] - Y[:, None]
        L2 = dx * dx + dy * dy
        t = np.clip(-(X[:, None] * dx + Y[:, None] * dy) / np.maximum(L2, 1e-9), 0.0, 1.0)
        coupe = (np.hypot(X[:, None] + t * dx, Y[:, None] + t * dy) < r) & dehors[:, None] & dehors[None, :] & (L2 > 1.0)
        if not coupe.any():
            continue
        di = np.maximum(d, r * 1.0000001)
        ang = np.arctan2(Y, X)
        dth = np.abs((ang[None, :] - ang[:, None] + np.pi) % (2 * np.pi) - np.pi)
        tang = np.sqrt(di * di - r * r)
        a = np.arccos(np.clip(r / di, -1.0, 1.0))
        chemin = tang[:, None] + tang[None, :] + r * np.maximum(dth - a[:, None] - a[None, :], 0.0)
        F = np.where(coupe, F * np.maximum(chemin / np.maximum(np.sqrt(L2), 1e-9), 1.0), F)
    return F


# ── Flotte hétérogène ────────────────────────────────────────────────────────
def _flotte(vehicules, p, dep_pos, cap_defaut) -> list:
    """Attributs résolus de chaque véhicule : mode, vitesse, capacité, spécialités, autonomie, départ, retour, disponibilité."""
    route = [v for v in vehicules if v.get("mode") not in MODES or v.get("mode") == "route"]
    med = vitesse_flotte(route)
    sortie = []
    for v in vehicules:
        mode = v.get("mode") if v.get("mode") in MODES else "route"
        m = MODES[mode]
        x = v.get("vitesse_ms")
        if p["vitesse_ms"]:
            vit = float(p["vitesse_ms"])
        elif x and m["borne"][0] <= float(x) <= m["borne"][1]:
            vit = float(x)
        else:
            vit = med if mode == "route" else m["vitesse_ms"]
        cap = float(v["capacite"]) if v.get("capacite") and float(v["capacite"]) > 0 else cap_defaut
        depot = tuple(v["depot"]) if v.get("depot") and len(v["depot"]) == 2 else dep_pos
        depart = (float(v["lat"]), float(v["lon"])) if p["depuis_position"] and v.get("lat") is not None and v.get("lon") is not None else depot
        sortie.append({"id": v["id"], "nom": v.get("nom") or v["id"], "mode": mode, "vitesse": vit, "capacite": cap,
                       "tags": set(v.get("tags") or []), "autonomie": float(v["autonomie_m"]) if v.get("autonomie_m") and float(v["autonomie_m"]) > 0 else None,
                       "depart": depart, "fin": depot, "dispo": max(float(v.get("disponible_s") or 0.0), 0.0)})
    return sortie


# ── Évaluation d'une tournée ─────────────────────────────────────────────────
class _Contexte:
    """Un sous-problème : V véhicules, C commandes. Nœuds : 0..V-1 départs, V..2V-1 retours, 2V.. commandes."""

    def __init__(self, fl, cmds, p, zones):
        V, C = len(fl), len(cmds)
        self.V, self.C, self.off, self.retour, self.pen = V, C, 2 * V, bool(p["retour_depot"]), float(p["penalite_retard"])
        self.lat = np.array([f["depart"][0] for f in fl] + [f["fin"][0] for f in fl] + [c["lat"] for c in cmds], float)
        self.lon = np.array([f["depart"][1] for f in fl] + [f["fin"][1] for f in fl] + [c["lon"] for c in cmds], float)
        base = _g.haversine(self.lat[:, None], self.lon[:, None], self.lat[None, :], self.lon[None, :])
        mats = {}
        for f in fl:
            cle = f["mode"]
            if cle not in mats:
                zs = [z for z in zones if not z["modes"] or cle in z["modes"]]
                det = float(p["detour"]) if cle == "route" else MODES[cle]["detour"]
                mats[cle] = base * det * (_facteurs_zones(self.lat, self.lon, zs) if zs else 1.0)
        self.D = [mats[f["mode"]] for f in fl]
        self.vit, self.cap, self.aut = [f["vitesse"] for f in fl], [f["capacite"] for f in fl], [f["autonomie"] for f in fl]
        self.dispo = [f["dispo"] for f in fl]
        self.dem = [max(float(c.get("demande") or 1.0), 0.0) for c in cmds]
        self.ech = [c.get("echeance_s") for c in cmds]
        self.deb = [c.get("debut_s") for c in cmds]
        self.prio = [max(float(c.get("priorite") or 1.0), 0.0) for c in cmds]
        self.srv = [float(c["service_s"]) if c.get("service_s") is not None else float(p["service_s"]) for c in cmds]
        self.compat = [[set(c.get("exige") or []) <= f["tags"] for c in cmds] for f in fl]

    def eval(self, v, seq):
        """(coût, distance, retard_s, charge, fin_s, attente_s) de la tournée `seq` du véhicule v. Coût infini si l'autonomie est dépassée."""
        D, cur, dist, t, retard, pond, attente, charge = self.D[v], v, 0.0, self.dispo[v], 0.0, 0.0, 0.0, 0.0
        vit = self.vit[v]
        for i in seq:
            n = self.off + i
            d = D[cur, n]
            dist += d
            t += d / vit
            if self.deb[i] is not None and t < self.deb[i]:
                attente += self.deb[i] - t
                t = self.deb[i]
            e = self.ech[i]
            if e is not None and t > e:
                retard += t - e
                pond += (t - e) * self.prio[i]
            t += self.srv[i]
            charge += self.dem[i]
            cur = n
        if self.retour and seq:
            d = D[cur, self.V + v]
            dist += d
            t += d / vit
        if self.aut[v] is not None and dist > self.aut[v] + 1e-6:
            return math.inf, dist, retard, charge, t, attente
        return dist + self.pen * pond, dist, retard, charge, t, attente

    def place(self, v, seq, i):
        """La commande i peut-elle rejoindre `seq` du véhicule v (compatibilité et capacité) ?"""
        return self.compat[v][i] and sum(self.dem[k] for k in seq) + self.dem[i] <= self.cap[v] + 1e-9


def _construire(ctx, ordre):
    """Insertion la moins coûteuse : positions présélectionnées par surcoût de distance (O(1)), les 2 meilleures évaluées exactement."""
    V = ctx.V
    routes, couts, charges, non = [[] for _ in range(V)], [0.0] * V, [0.0] * V, []
    for i in ordre:
        meilleur, n = None, ctx.off + i
        for v in range(V):
            if not ctx.compat[v][i] or charges[v] + ctx.dem[i] > ctx.cap[v] + 1e-9:
                continue
            D, r = ctx.D[v], routes[v]
            noeuds = [v] + [ctx.off + k for k in r]
            cands = []
            for pos in range(len(r) + 1):
                a = noeuds[pos]
                if pos < len(r):
                    delta = D[a, n] + D[n, noeuds[pos + 1]] - D[a, noeuds[pos + 1]]
                else:
                    delta = D[a, n] + ((D[n, V + v] - D[a, V + v]) if ctx.retour else 0.0)
                cands.append((delta, pos))
            cands.sort()
            for _d, pos in cands[:2]:
                cand = r[:pos] + [i] + r[pos:]
                c = ctx.eval(v, cand)[0]
                if c != math.inf and (meilleur is None or c - couts[v] < meilleur[0]):
                    meilleur = (c - couts[v], v, cand, c)
        if meilleur is None:
            non.append(i)
        else:
            _d, v, cand, c = meilleur
            routes[v], couts[v] = cand, c
            charges[v] += ctx.dem[i]
    return routes, non


def _amelioration(ctx, routes, budget_s, passes):
    V = len(routes)
    couts = [ctx.eval(v, r)[0] for v, r in enumerate(routes)]
    fin = time.monotonic() + budget_s
    for _ in range(passes):
        ameliore = False
        for v in range(V):                                            # 2-opt dans chaque tournée
            r, n = routes[v], len(routes[v])
            for a in range(n - 1):
                for b in range(a + 1, n):
                    cand = r[:a] + r[a:b + 1][::-1] + r[b + 1:]
                    c = ctx.eval(v, cand)[0]
                    if c < couts[v] - 1e-6:
                        routes[v], couts[v], r, ameliore = cand, c, cand, True
            if time.monotonic() > fin:
                return
        for v in range(V):                                            # déplacement d'une commande vers une autre tournée / position
            for pos in range(len(routes[v]) - 1, -1, -1):
                if pos >= len(routes[v]):
                    continue
                i = routes[v][pos]
                base = routes[v][:pos] + routes[v][pos + 1:]
                cb = ctx.eval(v, base)[0]
                meilleur = None
                for w in range(V):
                    cible = base if w == v else routes[w]
                    if w != v and not ctx.place(w, cible, i):
                        continue
                    cw = cb if w == v else couts[w]
                    for p in range(len(cible) + 1):
                        cand = cible[:p] + [i] + cible[p:]
                        c = ctx.eval(w, cand)[0]
                        delta = (c - cw) + (cb - couts[v] if w != v else 0.0)
                        if delta < -1e-6 and (meilleur is None or delta < meilleur[0]):
                            meilleur = (delta, w, cand, c)
                if meilleur:
                    _d, w, cand, c = meilleur
                    if w == v:
                        routes[v], couts[v] = cand, c
                    else:
                        routes[v], couts[v], routes[w], couts[w] = base, cb, cand, c
                    ameliore = True
                if time.monotonic() > fin:
                    return
        if not ameliore:
            return


# ── Décomposition des gros problèmes ─────────────────────────────────────────
def _groupes(lat, lon, taille: int) -> list:
    """Indices des commandes répartis en groupes géographiques de `taille` au plus (bissections k-means sur la sphère : valable d'un quartier à la planète)."""
    la, lo = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    xyz = np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=1)
    fait, pile = [], [np.arange(len(la))]
    while pile:
        idx = pile.pop()
        if len(idx) <= taille:
            fait.append(idx)
            continue
        P = xyz[idx]
        a = P[np.argmax(np.linalg.norm(P - P.mean(0), axis=1))]
        b = P[np.argmax(np.linalg.norm(P - a, axis=1))]
        m = np.linalg.norm(P - a, axis=1) <= np.linalg.norm(P - b, axis=1)
        for _ in range(8):
            if m.all() or (~m).all():
                break
            a, b = P[m].mean(0), P[~m].mean(0)
            m = np.linalg.norm(P - a, axis=1) <= np.linalg.norm(P - b, axis=1)
        if m.all() or (~m).all():                                     # points confondus : coupe en deux moitiés
            h = len(idx) // 2
            pile += [idx[:h], idx[h:]]
        else:
            pile += [idx[m], idx[~m]]
    return sorted(fait, key=lambda g: int(g.min()))


def _allouer(groupes, cmds, fl) -> list:
    """Véhicules de chaque groupe : d'abord une spécialité manquante, puis les plus proches jusqu'à couvrir 115 % de la demande ; le reste va aux groupes les plus chargés."""
    V, G = len(fl), len(groupes)
    demande = [sum(max(float(cmds[i].get("demande") or 1.0), 0.0) for i in g) for g in groupes]
    centre = [(float(np.mean([cmds[i]["lat"] for i in g])), float(np.mean([cmds[i]["lon"] for i in g]))) for g in groupes]
    libres, alloc, capa = set(range(V)), [[] for _ in groupes], [0.0] * G

    def proche(g, parmi):
        return min(parmi, key=lambda v: (float(_g.haversine(fl[v]["depart"][0], fl[v]["depart"][1], centre[g][0], centre[g][1])), v))

    for g in sorted(range(G), key=lambda k: -demande[k]):
        for tag in sorted({t for i in groupes[g] for t in (cmds[i].get("exige") or [])}):
            if not any(tag in fl[v]["tags"] for v in alloc[g]):
                cand = [v for v in libres if tag in fl[v]["tags"]]
                if cand:
                    v = proche(g, cand)
                    libres.discard(v)
                    alloc[g].append(v)
                    capa[g] += fl[v]["capacite"]
        while libres and (not alloc[g] or capa[g] < 1.15 * demande[g]):
            v = proche(g, libres)
            libres.discard(v)
            alloc[g].append(v)
            capa[g] += fl[v]["capacite"]
    for v in sorted(libres):
        g = max(range(G), key=lambda k: (demande[k] / max(capa[k], 1e-9), -k))
        alloc[g].append(v)
        capa[g] += fl[v]["capacite"]
    return [sorted(a) for a in alloc]


# ── Point d'entrée ───────────────────────────────────────────────────────────
def _reference(ctx, C):
    """Ordre de saisie, commandes réparties à tour de rôle dans les véhicules qui ont de la place : (distance, complet)."""
    V, ref, charges, rr = ctx.V, [[] for _ in range(ctx.V)], [0.0] * ctx.V, 0
    for i in range(C):
        for k in range(V):
            v = (rr + k) % V
            if ctx.compat[v][i] and charges[v] + ctx.dem[i] <= ctx.cap[v] + 1e-9:
                ref[v].append(i)
                charges[v] += ctx.dem[i]
                rr = (v + 1) % V
                break
    return sum(ctx.eval(v, r)[1] for v, r in enumerate(ref) if r), sum(len(r) for r in ref) == C


def planifier(commandes: list, vehicules: list, depot: tuple | None = None, **params) -> dict:
    """commandes : [{id, nom, lat, lon, demande, echeance_s (secondes à partir de maintenant, ou None),
                     + facultatifs : debut_s (ouverture : le véhicule attend), priorite (pondère le retard), service_s, exige [spécialités]}]
    vehicules : [{id, nom, lat, lon (dernière position ou None), vitesse_ms,
                  + facultatifs : mode (route|rail|mer|air), capacite, tags [spécialités], autonomie_m, depot (lat, lon), disponible_s}]
    depot : (lat, lon) ou None. params en plus de PARAMETRES : zones [{lat, lon, rayon_m, nom?, modes?}] (zones à contourner)."""
    t_debut = time.monotonic()
    p = {**PARAMETRES, **{k: v for k, v in params.items() if v is not None}}
    zones = zones_valides(params.get("zones"))
    commandes, vehicules = commandes[:LIMITES["commandes"]], vehicules[:LIMITES["vehicules"]]
    if not commandes:
        return {"vide": True, "message": _i18n_t("engine/logistique.py:g:390:42")}
    if not vehicules:
        return {"vide": True, "message": _i18n_t("engine/logistique.py:g:392:42")}
    C0, V = len(commandes), len(vehicules)
    en_zone = _dans_zones(np.array([c["lat"] for c in commandes]), np.array([c["lon"] for c in commandes]), zones) if zones else np.zeros(C0, bool)
    a_servir = [i for i in range(C0) if not en_zone[i]]
    conseil = depot_conseille([commandes[i] for i in a_servir] or commandes, depot)
    dep_pos = tuple(depot) if depot else (conseil["lat"], conseil["lon"])
    demandes = [max(float(c.get("demande") or 1.0), 0.0) for c in commandes]
    cap_defaut = float(p["capacite"]) if p["capacite"] else max(max(demandes), sum(demandes) / V * 1.15)
    fl = _flotte(vehicules, p, dep_pos, cap_defaut)
    budget = min(float(p["budget_s"]) if p["budget_s"] else (LIMITES["budget_s"] if len(a_servir) <= LIMITES["seuil_groupes"]
                 else LIMITES["budget_s"] + 0.005 * len(a_servir)), LIMITES["budget_max_s"])

    # groupes : un seul si le problème est petit, sinon découpage géographique
    if len(a_servir) <= LIMITES["seuil_groupes"] or V == 1:
        groupes = [np.array(a_servir, int)]
        allocs = [list(range(V))]
    else:
        taille = max(LIMITES["groupe"], -(-len(a_servir) // V))
        while True:
            sous = _groupes([commandes[i]["lat"] for i in a_servir], [commandes[i]["lon"] for i in a_servir], taille)
            if len(sous) <= V or len(sous) == 1:
                break
            taille *= 2
        groupes = [np.array([a_servir[k] for k in g], int) for g in sous]
        allocs = _allouer(groupes, commandes, fl) if len(groupes) > 1 else [list(range(V))]

    routes_glob, non_aff, sans_flotte = [], [], set()
    dist_ref, ref_complete, nb_groupes = 0.0, True, 0
    for g, alloc in zip(groupes, allocs):
        if len(g) == 0:
            continue
        nb_groupes += 1
        if not alloc:
            non_aff += list(g)
            sans_flotte |= set(int(i) for i in g)
            continue
        sous_cmd = [commandes[i] for i in g]
        ctx = _Contexte([fl[v] for v in alloc], sous_cmd, p, zones)
        ordre = sorted(range(len(g)), key=lambda k: (-len(sous_cmd[k].get("exige") or []), -ctx.dem[k], k))   # spécialités d'abord : sinon les commandes ordinaires saturent les véhicules spécialisés
        routes, non = _construire(ctx, ordre)
        _amelioration(ctx, routes, max(budget * len(g) / max(len(a_servir), 1), 0.05), LIMITES["passes"])
        d_ref, complet = _reference(ctx, len(g))
        dist_ref += d_ref
        ref_complete = ref_complete and complet
        non_aff += [int(g[k]) for k in non]
        for lv, seq in enumerate(routes):
            if seq:
                routes_glob.append((alloc[lv], ctx, lv, [int(g[k]) for k in seq], seq, 1))

    # reprise : les commandes restées sans place (secteur saturé) sont proposées à toute la flotte proche en rotation suivante
    # (retour au dépôt, rechargement, nouveau départ : capacité et autonomie de nouveau pleines) ; seulement si le retour au dépôt est demandé
    fin_v, rotation = {}, {}
    for v, ctx, lv, _sg, sq, rot in routes_glob:
        fin_v[v] = max(fin_v.get(v, 0.0), ctx.eval(lv, sq)[4])
        rotation[v] = max(rotation.get(v, 0), rot)
    for _tour in range(LIMITES["rotations"] - 1):
        reste = list(non_aff)
        if not reste or not p["retour_depot"]:
            break
        cla, clo = float(np.mean([commandes[i]["lat"] for i in reste])), float(np.mean([commandes[i]["lon"] for i in reste]))
        cands = sorted(range(V), key=lambda v: (float(_g.haversine(fl[v]["fin"][0], fl[v]["fin"][1], cla, clo)), v))[:LIMITES["flotte_reprise"]]
        fl2 = [{**fl[v], "depart": fl[v]["fin"] if v in fin_v else fl[v]["depart"], "dispo": fin_v.get(v, fl[v]["dispo"])} for v in cands]
        sous_cmd = [commandes[i] for i in reste]
        ctx = _Contexte(fl2, sous_cmd, p, zones)
        routes, non = _construire(ctx, sorted(range(len(reste)), key=lambda k: (-len(sous_cmd[k].get("exige") or []), -ctx.dem[k], k)))
        _amelioration(ctx, routes, min(budget * 0.25, 2.0), LIMITES["passes"])
        if len(non) == len(reste):
            break
        for lv, seq in enumerate(routes):
            if seq:
                v = cands[lv]
                rotation[v] = rotation.get(v, 0) + 1
                routes_glob.append((v, ctx, lv, [reste[k] for k in seq], seq, rotation[v]))
                fin_v[v] = ctx.eval(lv, seq)[4]
        non_aff = [reste[k] for k in non] + [i for i in non_aff if i not in reste]

    # sortie
    libelle = [c.get("nom") or str(c.get("id")) for c in commandes]
    sortie_routes, dist_tot, retard_tot, nb_retards, charge_tot, dur_max, cap_utilisee, attente_tot = [], 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0
    for v, ctx, lv, seq_glob, seq, rot in routes_glob:
        f = fl[v]
        _c, dist, retard, charge, duree, attente = ctx.eval(lv, seq)
        cur, t, cum, etapes = lv, ctx.dispo[lv], 0.0, []
        chemin = [[round(float(ctx.lat[lv]), 6), round(float(ctx.lon[lv]), 6)]]
        for ordre, (i, k) in enumerate(zip(seq_glob, seq), 1):
            n = ctx.off + k
            d = float(ctx.D[lv][cur, n])
            cum += d
            t += d / ctx.vit[lv]
            att = max(0.0, ctx.deb[k] - t) if ctx.deb[k] is not None else 0.0
            t += att
            ret = max(0.0, t - ctx.ech[k]) if ctx.ech[k] is not None else 0.0
            etapes.append({"ordre": ordre, "id": commandes[i].get("id"), "nom": libelle[i], "lat": round(float(ctx.lat[n]), 6), "lon": round(float(ctx.lon[n]), 6),
                           "demande": round(ctx.dem[k], 3), "eta_s": round(t, 1), "retard_s": round(ret, 1), "attente_s": round(att, 1), "distance_cumulee_m": round(cum, 1)})
            chemin.append([etapes[-1]["lat"], etapes[-1]["lon"]])
            nb_retards += 1 if ret > 0 else 0
            t += ctx.srv[k]
            cur = n
        if ctx.retour:
            chemin.append([round(float(f["fin"][0]), 6), round(float(f["fin"][1]), 6)])
        sortie_routes.append({"vehicule": f["id"], "nom": f["nom"], "mode": f["mode"], "etapes": etapes, "chemin": chemin, "distance_m": round(dist, 1), "duree_s": round(duree, 1),
                              "charge": round(charge, 3), "capacite": round(f["capacite"], 3), "retard_s": round(retard, 1), "attente_s": round(attente, 1),
                              "autonomie_m": f["autonomie"], "vitesse_ms": round(f["vitesse"], 2), "rotation": rot})
        dist_tot += float(dist)
        retard_tot += float(retard)
        charge_tot += float(charge)
        cap_utilisee += f["capacite"]
        dur_max = max(dur_max, float(duree))
        attente_tot += float(attente)
    utilises = len({t["vehicule"] for t in sortie_routes})
    a_livrer = {i for _v, _c, _l, sg, _s, _r in routes_glob for i in sg}
    non_aff_tous = sorted(set(non_aff) | {i for i in range(C0) if en_zone[i]})
    total_dem = sum(demandes[i] for i in a_livrer)
    gain = round(1 - dist_tot / dist_ref, 3) if ref_complete and not non_aff_tous and dist_ref > 0 else None
    caps = sorted((f["capacite"] for f in fl), reverse=True)
    flotte_min, acc = V, 0.0
    for k, c in enumerate(caps, 1):
        acc += c
        if acc >= sum(demandes) - 1e-9:
            flotte_min = k
            break
    cap_med = float(np.median(caps))
    utilisation = round(charge_tot / cap_utilisee, 3) if utilises and cap_utilisee > 0 else 0.0
    vit_med = float(np.median([f["vitesse"] for f in fl]))

    def _raison(i):
        if en_zone[i]:
            return "zone_interdite"
        if not any(f["capacite"] >= demandes[i] - 1e-9 and set(commandes[i].get("exige") or []) <= f["tags"] for f in fl):
            return "aucun_vehicule_adapte"
        return "flotte_insuffisante" if i in sans_flotte else "flotte_saturee_ou_autonomie"

    raisons = {i: _raison(i) for i in non_aff_tous}
    resume = {"commandes": C0, "vehicules": V, "vehicules_utilises": utilises, "flotte_min": flotte_min, "capacite": round(cap_med, 3),
              "distance_m": round(dist_tot, 1), "duree_max_s": round(dur_max, 1), "retard_total_s": round(retard_tot, 1), "retards": nb_retards,
              "non_affectees": len(non_aff_tous), "charge_affectee": round(total_dem, 3), "utilisation": utilisation, "gain": gain,
              "distance_reference_m": round(dist_ref, 1) if ref_complete and dist_ref > 0 else None, "vitesse_ms": round(vit_med, 2),
              "depot_gain": conseil.get("gain") if conseil else None, "attente_total_s": round(attente_tot, 1), "groupes": nb_groupes,
              "modes": {m: sum(1 for f in fl if f["mode"] == m) for m in MODES if any(f["mode"] == m for f in fl)}, "temps_calcul_s": round(time.monotonic() - t_debut, 2)}
    alertes = []
    if non_aff_tous:
        par = {}
        for r in raisons.values():
            par[r] = par.get(r, 0) + 1
        libelles = {"zone_interdite": td("engine/logistique.py:g:537:39"), "aucun_vehicule_adapte": td("engine/logistique.py:g:537:91"),
                    "flotte_insuffisante": td("engine/logistique.py:g:538:44"), "flotte_saturee_ou_autonomie": td("engine/logistique.py:g:538:110")}
        alertes.append({"type": "non_affectee", "gravite": "critique", "valeur": len(non_aff_tous), "raisons": par,
                        "detail": tf("engine/logistique.py:g:539:35", v1=len(non_aff_tous)) + ", ".join(f"{n} {libelles[r]}" for r, n in sorted(par.items())) + _i18n_t("engine/logistique.py:g:539:168")})
    if nb_retards:
        alertes.append({"type": "retard", "gravite": "attention", "valeur": nb_retards,
                        "detail": tf("engine/logistique.py:g:543:35", v1=nb_retards, v2=retard_tot / 60)})
    if utilises > flotte_min and utilisation < 0.5 and not nb_retards:
        alertes.append({"type": "flotte_sous_utilisee", "gravite": "info", "valeur": utilisation,
                        "detail": tf("engine/logistique.py:g:546:35", v1=utilises, v2=utilisation * 100, v3=flotte_min)})
    if conseil and conseil.get("gain") is not None and conseil["gain"] >= p["alerte_depot"]:
        alertes.append({"type": "depot_mal_place", "gravite": "info", "valeur": conseil["gain"], "lat": conseil["lat"], "lon": conseil["lon"],
                        "detail": tf("engine/logistique.py:g:549:35", v1=conseil['gain'] * 100)})
    return {"vide": False, "depot": {"lat": round(float(dep_pos[0]), 6), "lon": round(float(dep_pos[1]), 6), "choisi": bool(depot)},
            "depot_conseille": conseil, "routes": sortie_routes, "resume": resume, "alertes": alertes, "zones": zones,
            "commandes": [{"id": c.get("id"), "nom": libelle[i], "lat": round(float(c["lat"]), 6), "lon": round(float(c["lon"]), 6), "demande": round(demandes[i], 3),
                           "affectee": i in a_livrer, **({"raison": raisons[i]} if i in raisons else {})} for i, c in enumerate(commandes)],
            "parametres": {"capacite": round(cap_med, 3), "service_s": p["service_s"], "detour": p["detour"], "retour_depot": bool(p["retour_depot"]),
                           "depuis_position": bool(p["depuis_position"]), "vitesse_ms": round(vit_med, 2), "zones": len(zones), "budget_s": round(budget, 2)}}


def scenario(n_commandes: int = 25, n_vehicules: int = 3, graine: int = 11, centre=(48.8566, 2.3522), etendue_m: float = 6000.0):
    """Commandes et véhicules simulés autour de `centre` (quantités 1 à 9 ; un tiers a une échéance serrée)."""
    rng = np.random.default_rng(graine)
    n_commandes, n_vehicules = max(1, min(n_commandes, LIMITES["commandes"])), max(1, min(n_vehicules, LIMITES["vehicules"]))
    X, Y = rng.normal(0, etendue_m / 3, n_commandes), rng.normal(0, etendue_m / 3, n_commandes)
    lat, lon = _g._geo(X, Y, centre)
    commandes = [{"id": f"cmd_{i + 1}", "nom": f"Commande {i + 1}", "lat": float(lat[i]), "lon": float(lon[i]), "demande": int(rng.integers(1, 10)),
                  "echeance_s": float(rng.uniform(1800, 5400)) if i % 3 == 0 else None} for i in range(n_commandes)]
    vx, vy = rng.normal(0, 800, n_vehicules), rng.normal(0, 800, n_vehicules)
    vl, vo = _g._geo(vx, vy, centre)
    vehicules = [{"id": f"veh_{i + 1}", "nom": tf("engine/logistique.py:g:568:48", v1=i + 1), "lat": float(vl[i]), "lon": float(vo[i]), "vitesse_ms": 9.0} for i in range(n_vehicules)]
    return commandes, vehicules, (float(centre[0]), float(centre[1]))


def scenario_strategique(n_commandes: int = 300, n_vehicules: int = 60, graine: int = 21, centre=(48.8566, 2.3522), etendue_m: float = 300_000.0):
    """Théâtre étendu simulé : flotte mixte (camions lourds et légers, citernes à spécialité, avions rapides à faible capacité, autonomies limitées),
    commandes avec fenêtres horaires, priorités et spécialités exigées, et une zone interdite au centre. Sert aux tests de charge et à la démonstration."""
    rng = np.random.default_rng(graine)
    n_commandes, n_vehicules = max(1, min(n_commandes, LIMITES["commandes"])), max(1, min(n_vehicules, LIMITES["vehicules"]))
    X, Y = rng.uniform(-etendue_m, etendue_m, n_commandes), rng.uniform(-etendue_m, etendue_m, n_commandes)
    lat, lon = _g._geo(X, Y, centre)
    commandes = []
    for i in range(n_commandes):
        tot = float(rng.uniform(4 * 3600, 20 * 3600))
        commandes.append({"id": f"cmd_{i + 1}", "nom": f"Point {i + 1}", "lat": float(lat[i]), "lon": float(lon[i]), "demande": int(rng.integers(1, 9)),
                          "debut_s": float(rng.uniform(0, 6 * 3600)) if i % 4 == 0 else None, "echeance_s": tot if i % 3 == 0 else None,
                          "priorite": float(rng.choice([1, 1, 1, 3, 5])), "service_s": float(rng.choice([300, 600, 1800])),
                          "exige": ["citerne"] if i % 10 == 0 else []})
    vehicules = []
    for k in range(n_vehicules):
        x, y = rng.normal(0, etendue_m / 4), rng.normal(0, etendue_m / 4)
        la, lo = _g._geo(x, y, centre)
        t = k % 10
        if t < 4:
            v = {"mode": "route", "capacite": 60, "vitesse_ms": 20.0}
        elif t < 8:
            v = {"mode": "route", "capacite": 20, "vitesse_ms": 25.0, "autonomie_m": 900_000.0}
        elif t == 8:
            v = {"mode": "route", "capacite": 40, "vitesse_ms": 18.0, "tags": ["citerne"]}
        else:
            v = {"mode": "air", "capacite": 15, "vitesse_ms": 60.0}
        vehicules.append({"id": f"veh_{k + 1}", "nom": f"Vecteur {k + 1}", "lat": float(la), "lon": float(lo), **v})
    return commandes, vehicules, (float(centre[0]), float(centre[1])), [{"lat": float(centre[0]), "lon": float(centre[1]), "rayon_m": 15_000.0, "nom": "Zone interdite", "modes": ["route"]}]
