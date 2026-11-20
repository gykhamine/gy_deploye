"""OBJETS — détection d'objets par réseau de neurones (YOLO au format ONNX) déposé par l'utilisateur.

Aucun réseau n'est livré : OpenCV seul ne sait détecter ni couteau, ni sac, ni téléphone, ni arme. Déposer dans engine/modeles_vision/ :
  - `objets.onnx`  : un détecteur YOLO (v5/v8/v11) exporté en ONNX, entrée carrée (640 par défaut) ;
  - `objets.json`  : {"labels": ["person", "bicycle", ...],            # noms des classes, dans l'ordre du modèle (COCO : 80 noms)
                      "niveaux": {"knife": "critique", "backpack": "info"},   # SEULES les classes listées déclenchent quelque chose
                      "noms":    {"knife": "Couteau"},                  # libellés affichés (facultatif)
                      "seuil": 0.45, "taille": 640}
Le détecteur « objets » n'apparaît dans le catalogue que si ces deux fichiers existent. Il consomme du processeur : prévoir un intervalle
d'analyse d'au moins 1 s. Module sans Django (cv2 + numpy).
"""
import json
import logging
import threading
from pathlib import Path
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)
NIVEAUX = ("info", "alerte", "critique")


def _dossier():
    try:
        from django.conf import settings
        d = getattr(settings, "VISION_MODELES_DIR", None)
    except Exception:
        d = None
    return Path(d) if d else Path(__file__).parent / "modeles_vision"


def fichiers():
    """(onnx, json) si les deux existent, sinon None."""
    d = _dossier()
    o, j = d / "objets.onnx", d / "objets.json"
    return (o, j) if o.is_file() and j.is_file() else None


def lire_reglages(chemin_json):
    try:
        r = json.loads(Path(chemin_json).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(r, dict) or not isinstance(r.get("labels"), list) or not isinstance(r.get("niveaux"), dict):
        return None
    r["niveaux"] = {k: v for k, v in r["niveaux"].items() if k in r["labels"] and v in NIVEAUX}
    return r


def lire_sortie_yolo(sortie, nb_classes, seuil, taille_entree, taille_image, nms=0.45):
    """Sortie brute d'un YOLO → [(classe, confiance, (x, y, w, h))] dans les pixels de l'image d'origine (largeur, hauteur).
    Gère les deux dispositions : (1, 4+nc, N) (v8/v11) et (1, N, 4+nc) (v5 sans objectness : mêmes colonnes)."""
    import cv2
    import numpy as np
    s = np.squeeze(np.asarray(sortie))
    if s.ndim != 2:
        return []
    if s.shape[0] == 4 + nb_classes and s.shape[1] != 4 + nb_classes:
        s = s.T
    if s.shape[1] < 4 + nb_classes:
        return []
    scores = s[:, 4:4 + nb_classes]
    classes = scores.argmax(axis=1)
    conf = scores[np.arange(len(s)), classes]
    garde = conf >= seuil
    if not garde.any():
        return []
    s, classes, conf = s[garde], classes[garde], conf[garde]
    w_img, h_img = taille_image
    kx, ky = w_img / float(taille_entree), h_img / float(taille_entree)
    boites = [[int((cx - w / 2) * kx), int((cy - h / 2) * ky), int(w * kx), int(h * ky)] for cx, cy, w, h in s[:, :4]]
    idx = cv2.dnn.NMSBoxes(boites, conf.astype(float).tolist(), float(seuil), nms)
    return [(int(classes[i]), float(conf[i]), tuple(boites[i])) for i in np.array(idx).flatten()]


class DetecteurObjets:
    def __init__(self, onnx, reglages):
        import cv2
        self.net = cv2.dnn.readNetFromONNX(str(onnx))
        self.r = reglages
        self.taille = int(reglages.get("taille", 640))
        self.seuil = float(reglages.get("seuil", 0.45))
        self._verrou = threading.Lock()

    def detecter(self, img):
        """[(label, confiance, boite)] — seulement les classes listées dans `niveaux`."""
        import cv2
        blob = cv2.dnn.blobFromImage(img, 1 / 255.0, (self.taille, self.taille), swapRB=True, crop=False)
        with self._verrou:
            self.net.setInput(blob)
            sortie = self.net.forward()
        labels = self.r["labels"]
        res = lire_sortie_yolo(sortie, len(labels), self.seuil, self.taille, (img.shape[1], img.shape[0]))
        return [(labels[c], conf, b) for c, conf, b in res if labels[c] in self.r["niveaux"]]


_instance = {"cle": None, "det": None}
_verrou = threading.Lock()


def detecteur():
    """(DetecteurObjets, None) ou (None, message). Chargé une fois, rechargé si les fichiers changent."""
    f = fichiers()
    if f is None:
        return None, "absent"
    cle = (f[0].stat().st_mtime, f[1].stat().st_mtime)
    with _verrou:
        if _instance["cle"] != cle:
            r = lire_reglages(f[1])
            if r is None:
                return None, "objets.json"
            try:
                _instance.update(cle=cle, det=DetecteurObjets(f[0], r))
            except Exception as e:
                logger.warning(_gt("rg6.msg.131"), e)
                _instance.update(cle=None, det=None)
                return None, str(e)[:150]
        return _instance["det"], None
