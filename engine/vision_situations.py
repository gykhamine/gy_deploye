"""SITUATIONS — ce que les personnes détectées FONT, pas seulement leur présence.

Entrée : les boîtes de personnes trouvées par les autres détecteurs de flux_vision.py (corps, visages, HOG) à chaque analyse.
Un suivi par plus proche voisin donne à chaque personne une trajectoire ; des règles en déduisent des situations :
  - « intrusion »  : une personne se trouve dans une zone interdite (polygone dessiné sur l'image) ;
  - « presence »   : une même personne reste plus de `presence_s` secondes dans le champ (ou dans une zone) ;
  - « foule »      : au moins `foule_min` personnes en même temps ;
  - « ligne »      : une personne franchit une ligne virtuelle (entrée / sortie) ; les compteurs sont gardés.
Configuration (clé `situations` du JSON `vision` du flux ; coordonnées normalisées 0..1, indépendantes de la résolution) :
  {"foule_min": 5, "presence_s": 30,
   "zones":  [{"nom": "Coffre", "polygone": [[0.1,0.2],[0.5,0.2],[0.5,0.9]], "niveau": "critique"}],
   "lignes": [{"nom": "Porte", "a": [0.5,0.0], "b": [0.5,1.0], "niveau": "info"}]}
Module sans Django : seulement cv2 + numpy, donc testable seul.
"""
from engine.i18n import t as _t, tf
import math

NIVEAUX = ("info", "alerte", "critique")
MAX_ZONES, MAX_POINTS = 12, 24


def valider(corps):
    """(config nettoyée, None) ou (None, message)."""
    if not isinstance(corps, dict):
        return None, _t("engine/vision_situations.py:err_config")
    cfg = {}
    for cle, bas, haut in (("foule_min", 2, 500), ("presence_s", 2, 86400)):
        if cle in corps and corps[cle] not in (None, ""):
            try:
                v = float(corps[cle])
            except (TypeError, ValueError):
                return None, _t("engine/vision_situations.py:err_nombre")
            if not bas <= v <= haut:
                return None, tf("engine/vision_situations.py:err_plage", v1=cle, v2=bas, v3=haut)
            cfg[cle] = v
    pt = lambda p: isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(c, (int, float)) and 0 <= c <= 1 for c in p)   # noqa: E731
    zones = corps.get("zones") or []
    if not isinstance(zones, list) or len(zones) > MAX_ZONES:
        return None, _t("engine/vision_situations.py:err_zones")
    cfg["zones"] = []
    for z in zones:
        poly = z.get("polygone") if isinstance(z, dict) else None
        if not isinstance(poly, list) or not 3 <= len(poly) <= MAX_POINTS or not all(pt(p) for p in poly) or z.get("niveau", "alerte") not in NIVEAUX:
            return None, _t("engine/vision_situations.py:err_zones")
        cfg["zones"].append({"nom": str(z.get("nom") or "zone")[:40], "polygone": [[float(a), float(b)] for a, b in poly], "niveau": z.get("niveau", "alerte")})
    lignes = corps.get("lignes") or []
    if not isinstance(lignes, list) or len(lignes) > MAX_ZONES:
        return None, _t("engine/vision_situations.py:err_lignes")
    cfg["lignes"] = []
    for ln in lignes:
        if not isinstance(ln, dict) or not pt(ln.get("a")) or not pt(ln.get("b")) or ln.get("niveau", "info") not in NIVEAUX or ln["a"] == ln["b"]:
            return None, _t("engine/vision_situations.py:err_lignes")
        cfg["lignes"].append({"nom": str(ln.get("nom") or "ligne")[:40], "a": [float(c) for c in ln["a"]], "b": [float(c) for c in ln["b"]], "niveau": ln.get("niveau", "info")})
    return cfg, None


def fusionner_personnes(boites):
    """Un corps et son visage = une seule personne : on garde les plus grandes boîtes et on écarte celles dont le centre est dedans."""
    gardees = []
    for b in sorted(boites, key=lambda b: b[2] * b[3], reverse=True):
        cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
        if not any(g[0] <= cx <= g[0] + g[2] and g[1] <= cy <= g[1] + g[3] for g in gardees):
            gardees.append(tuple(b))
    return gardees


class Situations:
    TTL_S = 4.0                    # une personne non revue pendant TTL_S secondes est oubliée (sa trajectoire s'arrête)

    def __init__(self, cfg=None):
        self.cfg = cfg or {}
        self._pistes, self._suivant = {}, 1
        self.compteurs = {}        # nom de ligne → {"entrees": n, "sorties": n}

    # ── suivi ───────────────────────────────────────────────────────────────────────────────────────────────────────────
    def _suivre(self, boites, w, h, maintenant):
        pieds = [(b[0] + b[2] / 2, b[1] + b[3]) for b in boites]                    # le point au sol : fiable pour les zones et les lignes
        portee = 0.18 * math.hypot(w, h)
        libres = set(range(len(pieds)))
        for pid, p in sorted(self._pistes.items()):
            if not libres:
                break
            j = min(libres, key=lambda k: math.dist(p["pos"], pieds[k]))
            if math.dist(p["pos"], pieds[j]) <= portee:
                p["avant"], p["pos"], p["vu"], p["boite"] = p["pos"], pieds[j], maintenant, boites[j]
                libres.discard(j)
        for j in libres:
            self._pistes[self._suivant] = {"pos": pieds[j], "avant": None, "debut": maintenant, "vu": maintenant, "boite": boites[j], "dans": {}}
            self._suivant += 1
        for pid in [k for k, p in self._pistes.items() if maintenant - p["vu"] > self.TTL_S]:
            del self._pistes[pid]

    # ── règles ──────────────────────────────────────────────────────────────────────────────────────────────────────────
    def mettre_a_jour(self, boites_personnes, largeur, hauteur, maintenant):
        """→ [{id, nom, niveau, boites, famille_nom, detail}] : les situations en cours à cet instant (l'anti-répétition est faite plus haut)."""
        import cv2
        import numpy as np
        boites = fusionner_personnes([tuple(b) for b in boites_personnes])
        self._suivre(boites, largeur, hauteur, maintenant)
        actives = [p for p in self._pistes.values() if p["vu"] == maintenant]
        sortie = []
        fam = _t("engine/vision_situations.py:famille")

        def ajouter(ident, nom, niveau, bs, detail=""):
            sortie.append({"id": "situation:" + ident, "nom": nom, "niveau": niveau, "boites": [list(b) for b in bs], "famille_nom": fam, "detail": detail})

        foule = self.cfg.get("foule_min")
        if foule and len(actives) >= foule:
            ajouter("foule", tf("engine/vision_situations.py:foule", v1=len(actives)), "alerte", [p["boite"] for p in actives])
        for z in self.cfg.get("zones", []):
            poly = np.array([[x * largeur, y * hauteur] for x, y in z["polygone"]], dtype=np.float32)
            dedans = [p for p in actives if cv2.pointPolygonTest(poly, (float(p["pos"][0]), float(p["pos"][1])), False) >= 0]
            for p in actives:
                if p in dedans:
                    p["dans"].setdefault(z["nom"], maintenant)
                else:
                    p["dans"].pop(z["nom"], None)
            if dedans:
                ajouter("intrusion:" + z["nom"], tf("engine/vision_situations.py:intrusion", v1=z["nom"]), z["niveau"], [p["boite"] for p in dedans])
            longs = [p for p in dedans if self.cfg.get("presence_s") and maintenant - p["dans"][z["nom"]] >= self.cfg["presence_s"]]
            if longs:
                ajouter("presence:" + z["nom"], tf("engine/vision_situations.py:presence_zone", v1=z["nom"], v2=int(self.cfg["presence_s"])), "critique" if z["niveau"] == "critique" else "alerte", [p["boite"] for p in longs])
        if self.cfg.get("presence_s"):
            longs = [p for p in actives if maintenant - p["debut"] >= self.cfg["presence_s"]]
            if longs:
                ajouter("presence", tf("engine/vision_situations.py:presence", v1=int(self.cfg["presence_s"])), "alerte", [p["boite"] for p in longs])
        for ln in self.cfg.get("lignes", []):
            a, b = (ln["a"][0] * largeur, ln["a"][1] * hauteur), (ln["b"][0] * largeur, ln["b"][1] * hauteur)
            for p in actives:
                if p["avant"] is None:
                    continue
                s0, s1 = self._cote(a, b, p["avant"]), self._cote(a, b, p["pos"])
                if s0 * s1 < 0 and self._entre(a, b, p["pos"]):
                    sens = "entrees" if s1 > 0 else "sorties"
                    self.compteurs.setdefault(ln["nom"], {"entrees": 0, "sorties": 0})[sens] += 1
                    ajouter(f"ligne:{ln['nom']}:{sens}", tf("engine/vision_situations.py:" + ("ligne_entree" if sens == "entrees" else "ligne_sortie"), v1=ln["nom"]),
                            ln["niveau"], [p["boite"]], detail=str(self.compteurs[ln["nom"]]))
        return sortie

    @staticmethod
    def _cote(a, b, p):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

    @staticmethod
    def _entre(a, b, p):
        """Le point est-il à hauteur du SEGMENT (pas de la droite infinie) ?"""
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / float(dx * dx + dy * dy)
        return -0.1 <= t <= 1.1
