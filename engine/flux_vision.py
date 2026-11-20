"""VISION SUR LES FLUX MÉDIA — surveillance intelligente des locaux (caméras fixes, drones, micros).

Ce module ne produit aucun flux : il LIT ceux de `flux_media.py` (même hub, donc mêmes sources : caméra locale, RTSP/HTTP,
caméra de drone, navigateur d'un téléphone) et les analyse avec OpenCV. Quand quelque chose est détecté : une image-preuve
annotée est enregistrée dans les archives du flux, une Notification est créée pour chaque agent/admin (relais SMS éventuel
déjà géré par notifications.py) et l'événement est diffusé en temps réel à l'entreprise.

Détecteurs (catalogue = ce qui est présent dans engine/cascades/, découvert automatiquement) :
  - cascades Haar / LBP (visages, corps, plaques, yeux, couverts…) + toute cascade déposée dans cascades/personnalises/ ;
  - « pietons_hog » : personnes (détecteur HOG intégré d'OpenCV — le XML hogcascade n'est plus lisible par OpenCV 4) ;
  - « mouvement » : soustraction de fond (MOG2), caméras FIXES seulement ;
  - « bruit » : niveau sonore anormal (micros) ;
  - modèles entraînés dans la page Entraînement (classifieurs d'images / de sons, id « ia_<id> ») : proposés pour l'entreprise du
    flux, appliqués avec la même recette qu'à l'entraînement (vecteurs de caractéristiques) ; réglage par modèle dans
    `modeles` : {"<id>": {"classes": ["chien", ...] (vide = toutes), "seuil": 0.7}}.
Détection de visages = présence seulement : aucune identification, aucune reconnaissance de personne.

Configuration = JSON `vision` dans `ChampDynamique.options_choix` (à côté de source/audio, que flux_media ne touche pas) :
  {"actif": true, "profil": "fixe" | "mobile" (drone : pas de détection de mouvement), "detecteurs": ["id", ...],
   "niveaux": {"id": "info|alerte|critique"}, "intervalle_s": 1.0, "delai_alerte_s": 60, "mouvement_pct": 1.5,
   "bruit_rms": 2500, "niveau_min": "info"}
L'analyse tourne dans un fil par flux ; elle maintient le hub en vie (sinon il s'arrêterait 30 s sans spectateur) et reprend
toute seule au démarrage du serveur pour les flux dont `actif` est vrai.
"""
from engine.i18n import t as _t, tf
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

from django.conf import settings

from . import flux_media as fm
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

NIVEAUX = ("info", "alerte", "critique")
PROFILS = ("fixe", "mobile")
MOUVEMENT, BRUIT, PIETONS_HOG = "mouvement", "bruit", "pietons_hog"
IDENTIFICATION, SITUATIONS, OBJETS = "identification", "situations", "objets"
MODELE_IA = "modele_ia"               # moteur des détecteurs « modèle entraîné » (id « ia_<id du modèle> »), voir modele_media.py
PREFIXE_MODELE = "ia_"
FENETRE_SON_S = 2.0                   # durée de son donnée d'un coup à un modèle audio (réglable : « fenetre_s »)
LARGEUR_ANALYSE = 640                 # les cascades travaillent sur une image réduite (rapide), les cadres sont remis à l'échelle
RECONFIG_S = 10                       # relecture du champ (source, config) en base
MAX_JOURNAL = 200

_FAMILLES = (("frontalface", "visage"), ("profileface", "visage"), ("eye", "yeux"), ("smile", "sourire"),
             ("upperbody", "personne"), ("fullbody", "personne"), ("lowerbody", "personne"), ("plate", "plaque"),
             ("catface", "chat"), ("silverware", "couverts"))
_NIVEAU_FAMILLE = {"visage": "alerte", "personne": "alerte", "mouvement": "alerte", "bruit": "alerte", "identification": "alerte", "situation": "alerte", "modele": "alerte"}


def dossier_cascades():
    return Path(getattr(settings, "CASCADES_DIR", None) or Path(__file__).parent / "cascades")


def _famille(stem):
    for motif, fam in _FAMILLES:
        if motif in stem:
            return fam
    return "objet"


def _reglages_fichier():
    f = dossier_cascades() / "cascades.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        logger.warning(_gt("rg8.msg.046"))
        return {}


def catalogue(entreprise_id=None):
    """Tous les détecteurs disponibles : [{id, nom, famille, famille_nom, niveau, moteur, video, fichier}].
    Avec `entreprise_id` : s'y ajoutent les modèles entraînés de cette entreprise (images -> caméras, sons -> micros)."""
    sortie = [
        {"id": MOUVEMENT, "nom": _t("engine/flux_vision.py:mouvement"), "famille": "mouvement", "moteur": "mog2", "video": True},
        {"id": PIETONS_HOG, "nom": _t("engine/flux_vision.py:pietons_hog"), "famille": "personne", "moteur": "hog", "video": True},
        {"id": BRUIT, "nom": _t("engine/flux_vision.py:bruit"), "famille": "bruit", "moteur": "rms", "video": False},
        {"id": IDENTIFICATION, "nom": _t("engine/flux_vision.py:identification"), "famille": "identification", "moteur": "identification", "video": True},
        {"id": SITUATIONS, "nom": _t("engine/flux_vision.py:situations"), "famille": "situation", "moteur": "situations", "video": True},
    ]
    try:
        from . import vision_objets
        if vision_objets.fichiers() is not None:             # proposé seulement si le réseau est déposé : pas de détecteur qui ne marchera pas
            sortie.append({"id": OBJETS, "nom": _t("engine/flux_vision.py:objets"), "famille": "objet", "moteur": "onnx", "video": True})
    except Exception:
        logger.debug("vision : objets indisponibles", exc_info=True)
    if entreprise_id:
        try:
            from . import modele_media
            for m in modele_media.modeles_disponibles(entreprise_id):
                sortie.append({"id": PREFIXE_MODELE + str(m["id"]), "nom": m["nom"], "famille": "modele", "moteur": MODELE_IA,
                               "video": m["media"] == modele_media.IMAGE, "modele_id": m["id"], "classes": m["classes"],
                               "vectorise": m["vectorise"], "media": m["media"]})
        except Exception:
            logger.debug(_gt("rg5.msg.043"), exc_info=True)
    reglages, base, vus = _reglages_fichier(), dossier_cascades(), {e["id"] for e in sortie}
    for sous in ("haarcascades", "lbpcascades", "personnalises"):
        d = base / sous
        for p in sorted(d.glob("*.xml")) if d.is_dir() else []:
            ident = p.stem if p.stem not in vus else f"{sous}_{p.stem}"
            vus.add(ident)
            r = reglages.get(f"{sous}/{p.name}", {})
            perso = sous == "personnalises"
            fam = "personnalise" if perso else _famille(p.stem)
            nom = str(r.get("label") or p.stem.replace("haarcascade_", "").replace("lbpcascade_", "").replace("_", " "))
            niveau = r.get("niveau") if r.get("niveau") in NIVEAUX else ("alerte" if perso else _NIVEAU_FAMILLE.get(fam, "info"))
            sortie.append({"id": ident, "nom": nom, "famille": fam, "moteur": "cascade", "video": True, "niveau": niveau,
                           "fichier": str(p), "reglages": r})
    for e in sortie:
        e.setdefault("niveau", _NIVEAU_FAMILLE.get(e["famille"], "info"))
        e["famille_nom"] = _t("engine/flux_vision.py:famille_" + e["famille"])
    return sortie


def _par_id(entreprise_id=None):
    return {e["id"]: e for e in catalogue(entreprise_id)}


def valider_config(corps, existante=None, entreprise_id=None):
    """(config nettoyée, None) ou (None, message d'erreur). `existante` : valeurs conservées pour les champs absents.
    `entreprise_id` : nécessaire pour accepter des détecteurs « modèle entraîné » (ils appartiennent à l'entreprise)."""
    corps = corps if isinstance(corps, dict) else {}
    cfg = dict(existante or {})
    cat = _par_id(entreprise_id)
    if "detecteurs" in corps:
        liste = corps["detecteurs"]
        if not isinstance(liste, list) or any(not isinstance(i, str) or i not in cat for i in liste):
            return None, _t("engine/flux_vision.py:err_detecteur")
        cfg["detecteurs"] = list(dict.fromkeys(liste))
    if "profil" in corps:
        if corps["profil"] not in PROFILS:
            return None, _t("engine/flux_vision.py:err_profil")
        cfg["profil"] = corps["profil"]
    if "niveaux" in corps:
        n = corps["niveaux"]
        if not isinstance(n, dict) or any(k not in cat or v not in NIVEAUX for k, v in n.items()):
            return None, _t("engine/flux_vision.py:err_niveau")
        cfg["niveaux"] = n
    if "niveau_min" in corps:
        if corps["niveau_min"] not in NIVEAUX:
            return None, _t("engine/flux_vision.py:err_niveau")
        cfg["niveau_min"] = corps["niveau_min"]
    for cle, bas, haut in (("intervalle_s", 0.2, 60), ("delai_alerte_s", 1, 86400), ("mouvement_pct", 0.05, 80), ("bruit_rms", 100, 30000)):
        if cle in corps:
            try:
                v = float(corps[cle])
            except (TypeError, ValueError):
                return None, _t("engine/flux_vision.py:err_nombre")
            if not bas <= v <= haut:
                return None, tf("engine/flux_vision.py:err_plage", v1=cle, v2=bas, v3=haut)
            cfg[cle] = v
    if "modeles" in corps:
        modeles = corps["modeles"]
        if not isinstance(modeles, dict):
            return None, _t("engine/flux_vision.py:err_modeles")
        propres = {}
        for ident, r in modeles.items():
            if ident not in cat or cat[ident]["moteur"] != MODELE_IA or not isinstance(r, dict):
                return None, _t("engine/flux_vision.py:err_modeles")
            classes = r.get("classes") or []
            if not isinstance(classes, list) or any(not isinstance(c, str) for c in classes):
                return None, _t("engine/flux_vision.py:err_modeles")
            try:
                seuil = float(r.get("seuil", 0.7))
            except (TypeError, ValueError):
                return None, _t("engine/flux_vision.py:err_nombre")
            if not 0.0 <= seuil <= 1.0:
                return None, tf("engine/flux_vision.py:err_plage", v1="seuil", v2=0, v3=1)
            propres[ident] = {"classes": classes, "seuil": seuil}
        cfg["modeles"] = propres
    if "fenetre_s" in corps:
        try:
            f = float(corps["fenetre_s"])
        except (TypeError, ValueError):
            return None, _t("engine/flux_vision.py:err_nombre")
        if not 0.5 <= f <= 10:
            return None, tf("engine/flux_vision.py:err_plage", v1="fenetre_s", v2=0.5, v3=10)
        cfg["fenetre_s"] = f
    if "situations" in corps:
        from . import vision_situations
        sit, erreur = vision_situations.valider(corps["situations"])
        if erreur:
            return None, erreur
        cfg["situations"] = sit
    if "actif" in corps:
        cfg["actif"] = bool(corps["actif"])
    cfg.setdefault("profil", "fixe")
    cfg.setdefault("detecteurs", [])
    if cfg.get("actif") and not cfg["detecteurs"]:
        return None, _t("engine/flux_vision.py:err_aucun")
    return cfg, None


def config_vision(champ):
    return fm.config_champ(champ).get("vision") or {}


def enregistrer_config(champ, vision):
    """Écrit `vision` dans options_choix SANS toucher à source / audio / sources."""
    cfg = fm.config_champ(champ)
    cfg["vision"] = vision
    champ.options_choix = json.dumps(cfg)
    champ.save(update_fields=["options_choix"])


# ── journal des événements (mémoire + fichier, relu après un redémarrage) ───────────────────────────────────────────────
def _fichier_journal(entreprise_id, champ_id):
    return fm.dossier(entreprise_id) / f"journal_vision_{champ_id}.jsonl"   # volontairement hors du motif « _c<id>.ext » des archives


def lire_journal(entreprise_id, champ_id, n=30):
    f = _fichier_journal(entreprise_id, champ_id)
    try:
        lignes = f.read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    sortie = []
    for ligne in reversed(lignes):
        try:
            sortie.append(json.loads(ligne))
        except ValueError:
            continue
    return sortie


def _ecrire_journal(entreprise_id, champ_id, evenement):
    f = _fichier_journal(entreprise_id, champ_id)
    try:
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(evenement, ensure_ascii=False) + "\n")
        if f.stat().st_size > 512_000:
            garde = f.read_text(encoding="utf-8").splitlines()[-MAX_JOURNAL:]
            f.write_text("\n".join(garde) + "\n", encoding="utf-8")
    except OSError:
        logger.warning(_gt("rg6.msg.013"), exc_info=True)


# ── preuve scellée : empreinte SHA-256 + alerte SIEM (qui l'inscrit dans les blockchains système et entreprise) ─────────
_GRAVITE_SIEM = {"info": "info", "alerte": "moyenne", "critique": "critique"}


def empreinte_preuve(entreprise_id, nom):
    """SHA-256 (hex) de l'image-preuve ; '' s'il n'y a pas de preuve ou si le fichier est illisible."""
    if not nom:
        return ""
    try:
        return hashlib.sha256((fm.dossier(entreprise_id) / nom).read_bytes()).hexdigest()
    except OSError:
        return ""


def verifier_preuve(entreprise_id, evenement):
    """True si l'image-preuve de l'événement du journal est toujours celle dont l'empreinte a été scellée."""
    attendu = evenement.get("sha256") or ""
    return bool(attendu) and hmac.compare_digest(attendu, empreinte_preuve(entreprise_id, evenement.get("fichier")))


def _vers_siem(champ, ev):
    """Une alerte vision devient une alerte SIEM : incident, corrélation, cloche, et bloc de blockchain portant l'empreinte de la preuve."""
    from . import siem_noyau as siem
    try:
        siem.alerter(f"vision_{ev['niveau']}", _GRAVITE_SIEM.get(ev["niveau"], "moyenne"),
                     f"{champ.table.nom} / {champ.nom} : {ev['resume']}", entreprise_id=champ.table.entreprise_id,
                     details={"champ_id": champ.id, "fichier": ev["fichier"], "sha256": ev["sha256"], "date": ev["date"],
                              "detections": [d["id"] for d in ev["detections"]]},
                     dedoublonner_s=1)           # l'anti-répétition est déjà faite par delai_alerte_s
    except Exception:
        logger.exception("vision : alerte SIEM")


# ── l'analyse d'un flux ─────────────────────────────────────────────────────────────────────────────────────────────────
_COULEURS = {"info": (200, 160, 40), "alerte": (30, 140, 255), "critique": (40, 40, 230)}   # BGR


class Analyse(threading.Thread):
    def __init__(self, champ):
        super().__init__(daemon=True, name=f"vision-{champ.id}")
        self.champ_id, self.champ = champ.id, champ
        self.arret = threading.Event()
        self.etat = {"derniere": 0.0, "erreur": "", "analyses": 0, "debut": time.time()}
        self._classifieurs, self._hog, self._fond, self._chauffe = {}, None, None, 0
        self._dernier_signal = {}                 # id détecteur → instant de la dernière alerte (anti-répétition)
        self._reconfig, self.cfg, self.cat = 0.0, config_vision(champ), {}
        self._situations, self._sit_cfg = None, None
        self._rate, self._tampon_son, self._dernier_modele_son = 16000, bytearray(), 0.0

    # configuration vivante (relue en base, appliquée sans redémarrage)
    def _recharger(self):
        from .models import ChampDynamique
        if time.time() - self._reconfig < RECONFIG_S:
            return True
        self._reconfig = time.time()
        champ = ChampDynamique.objects.select_related("table").filter(id=self.champ_id).first()
        if champ is None:
            return False
        self.champ, self.cfg, self.cat = champ, config_vision(champ), _par_id(champ.table.entreprise_id)
        return bool(self.cfg.get("actif"))

    def _classifieur(self, ident, entree):
        import cv2
        if ident not in self._classifieurs:
            try:
                c = cv2.CascadeClassifier(entree["fichier"])
                self._classifieurs[ident] = None if c.empty() else c
            except Exception:
                self._classifieurs[ident] = None
            if self._classifieurs[ident] is None:
                self.etat["erreur"] = tf("engine/flux_vision.py:err_cascade", v1=entree["nom"])
        return self._classifieurs[ident]

    def run(self):
        import cv2
        import numpy as np
        from django import db
        seq, h = 0, None
        try:
            while not self.arret.is_set():
                try:
                    if not self._recharger():
                        break
                    video = self.champ.type_champ in fm.TYPES_VIDEO
                    if h is None or h.arret.is_set():
                        h, seq = fm.hub(self.champ, audio=False), 0
                        self._rate, self._tampon_son = int(getattr(h, "rate", 0) or 16000), bytearray()
                        self.etat["erreur"] = ""
                    seq, donnee = h.attendre(seq, delai=5)      # attendre() maintient aussi le hub en vie
                    trop_tot = video and time.time() - self.etat["derniere"] < float(self.cfg.get("intervalle_s") or 1.0)
                    if not donnee or trop_tot:             # le son n'est jamais sauté : chaque bloc reçu est mesuré
                        if h.arret.is_set():
                            self.etat["erreur"] = h.erreur or _t("engine/flux_vision.py:err_flux")
                            self.arret.wait(3)
                        continue
                    self.etat["derniere"] = time.time()
                    if video:
                        img = cv2.imdecode(np.frombuffer(donnee, np.uint8), cv2.IMREAD_COLOR)
                        trouves = self._analyser_image(img) if img is not None else []
                    else:
                        img, trouves = None, self._analyser_son(donnee)
                    self.etat["analyses"] += 1
                    if trouves:
                        self._signaler(img, trouves)
                except Exception as e:                           # un flux qui échoue ne tue jamais le fil : nouvel essai
                    self.etat["erreur"] = str(e)[:200]
                    logger.warning("vision %s : %s", self.champ_id, e)
                    h = None
                    self.arret.wait(5)
                finally:
                    db.close_old_connections()
        finally:
            if _analyses.get(self.champ_id) is self:
                _analyses.pop(self.champ_id, None)

    # ── détection ───────────────────────────────────────────────────────────────────────────────────────────────────────
    def _analyser_image(self, img):
        import cv2
        cat, choisis = self.cat, self.cfg.get("detecteurs") or []
        r = min(1.0, LARGEUR_ANALYSE / img.shape[1])
        petit = cv2.resize(img, None, fx=r, fy=r) if r < 1 else img
        gris = cv2.equalizeHist(cv2.cvtColor(petit, cv2.COLOR_BGR2GRAY))
        trouves = []

        def ajouter(ident, boites):
            if boites:
                e = cat[ident]
                niveau = (self.cfg.get("niveaux") or {}).get(ident, e["niveau"])
                trouves.append({"id": ident, "nom": e["nom"], "famille_nom": e["famille_nom"], "niveau": niveau,
                                "boites": [[int(v / r) for v in b] for b in boites]})

        for ident in choisis:
            e = cat.get(ident)
            if not e or not e["video"]:
                continue
            if e["moteur"] == "cascade":
                c = self._classifieur(ident, e)
                if c is not None:
                    rg = e.get("reglages", {})
                    mini = max(20, int(petit.shape[1] * float(rg.get("min_size_pct", 4)) / 100))
                    ajouter(ident, [tuple(b) for b in c.detectMultiScale(
                        gris, float(rg.get("scale", 1.1)), int(rg.get("min_neighbors", 5)), minSize=(mini, mini))])
            elif e["moteur"] == "hog":
                if self._hog is None:
                    self._hog = cv2.HOGDescriptor()
                    self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                rects, _ = self._hog.detectMultiScale(petit, winStride=(8, 8), padding=(8, 8), scale=1.05)
                ajouter(ident, [tuple(b) for b in rects])
            elif e["moteur"] == "mog2" and self.cfg.get("profil", "fixe") == "fixe":
                ajouter(ident, self._mouvement(petit))
            elif e["moteur"] == "identification":
                trouves.extend(self._identifier(img))
            elif e["moteur"] == "onnx":
                trouves.extend(self._objets(img, cat[ident]))
            elif e["moteur"] == MODELE_IA:
                trouves.extend(self._modele_ia(e, image=img))
        if SITUATIONS in choisis:                  # en dernier : les situations se déduisent des personnes trouvées par les autres
            trouves.extend(self._situations_de(trouves, img.shape[1], img.shape[0], cat))
        return trouves

    def _identifier(self, img):
        """Visages reconnus (ou « inconnu ») : une détection par personne. Les boîtes sont déjà dans l'image d'origine."""
        from . import identification
        r = min(1.0, 1280 / img.shape[1])
        vue = img if r >= 1 else __import__("cv2").resize(img, None, fx=r, fy=r)
        trouves, erreur = identification.analyser(self.champ.table.entreprise_id, vue)
        if erreur:
            self.etat["erreur"] = erreur
        for d in trouves:
            d["boites"] = [[int(v / r) for v in b] for b in d["boites"]]
        return trouves

    def _objets(self, img, entree):
        from . import vision_objets
        det, erreur = vision_objets.detecteur()
        if det is None:
            self.etat["erreur"] = tf("engine/flux_vision.py:err_objets", v1=erreur)
            return []
        noms, groupes = det.r.get("noms", {}), {}
        for label, conf, boite in det.detecter(img):
            groupes.setdefault(label, []).append(list(boite))
        niveaux = det.r["niveaux"]
        return [{"id": f"{OBJETS}:{label}", "nom": str(noms.get(label, label)), "famille_nom": entree["famille_nom"],
                 "niveau": (self.cfg.get("niveaux") or {}).get(f"{OBJETS}:{label}", niveaux[label]), "boites": bs}
                for label, bs in groupes.items()]

    def _situations_de(self, trouves, largeur, hauteur, cat):
        from . import vision_situations
        sc = self.cfg.get("situations") or {}
        if self._situations is None or self._sit_cfg != sc:
            self._situations, self._sit_cfg = vision_situations.Situations(sc), sc
        fams = ("personne", "visage")
        boites = [tuple(b) for d in trouves if cat.get(d["id"], {}).get("famille") in fams or d["id"].startswith("identification:") for b in d["boites"]]
        return self._situations.mettre_a_jour(boites, largeur, hauteur, time.time())

    def _mouvement(self, petit):
        """Zones en mouvement (caméra fixe). Ignore les changements globaux (lumière, secousse) et la phase d'apprentissage."""
        import cv2
        if self._fond is None:
            self._fond = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=32, detectShadows=False)
        masque = self._fond.apply(cv2.GaussianBlur(petit, (5, 5), 0))
        self._chauffe += 1
        if self._chauffe < 15:
            return []
        part = 100.0 * cv2.countNonZero(masque) / masque.size
        if part < float(self.cfg.get("mouvement_pct") or 1.5) or part > 60:
            return []
        masque = cv2.dilate(cv2.threshold(masque, 200, 255, cv2.THRESH_BINARY)[1], None, iterations=2)
        contours = cv2.findContours(masque, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
        grandes = sorted((c for c in contours if cv2.contourArea(c) > 0.002 * masque.size), key=cv2.contourArea, reverse=True)[:8]
        return [cv2.boundingRect(c) for c in grandes]

    def _analyser_son(self, pcm):
        import numpy as np
        choisis = self.cfg.get("detecteurs") or []
        trouves = []
        if len(pcm) < 2:
            return trouves
        if BRUIT in choisis:
            x = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64)
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            if rms >= float(self.cfg.get("bruit_rms") or 2500):
                e = self.cat[BRUIT]
                trouves.append({"id": BRUIT, "nom": e["nom"], "famille_nom": e["famille_nom"],
                                "niveau": (self.cfg.get("niveaux") or {}).get(BRUIT, e["niveau"]), "boites": [], "mesure": round(rms)})
        modeles = [i for i in choisis if self.cat.get(i, {}).get("moteur") == MODELE_IA and not self.cat[i]["video"]]
        if modeles:
            # Un modèle de son juge une FENÊTRE de son (quelques secondes), pas un bloc de quelques millisecondes.
            fenetre = int(float(self.cfg.get("fenetre_s") or FENETRE_SON_S) * self._rate) * 2
            self._tampon_son += pcm[: len(pcm) - len(pcm) % 2]
            del self._tampon_son[:-fenetre]
            maintenant = time.time()
            if len(self._tampon_son) >= fenetre and maintenant - self._dernier_modele_son >= float(self.cfg.get("intervalle_s") or 1.0):
                self._dernier_modele_son = maintenant
                for ident in modeles:
                    trouves.extend(self._modele_ia(self.cat[ident], pcm=bytes(self._tampon_son)))
        return trouves

    def _modele_ia(self, entree, image=None, pcm=None):
        """Un modèle entraîné juge l'image (ou la fenêtre de son) : une détection si sa classe est de celles à signaler
        (toutes si aucune n'est choisie) avec une confiance au moins égale au seuil. Pas de boîte : le modèle classe l'ensemble."""
        from . import modele_media
        from .models import ModeleIA
        try:
            modele = ModeleIA.objects.filter(pk=entree["modele_id"], entreprise_id=self.champ.table.entreprise_id).first()
            if modele is None or modele_media.media_du_modele(modele) is None:
                return []
            p = modele_media.predicteur(modele)
            r = p.predire_image(image, bgr=True) if image is not None else p.predire_son(pcm, self._rate)
        except Exception as e:                  # un modèle qui échoue ne coupe pas les autres détecteurs du flux
            self.etat["erreur"] = tf("engine/flux_vision.py:err_modele", v1=entree["nom"], v2=str(e)[:120])
            logger.warning(_gt("rg6.msg.014"), self.champ_id, entree["id"], e)
            return []
        reglage = (self.cfg.get("modeles") or {}).get(entree["id"], {})
        classes, seuil = reglage.get("classes") or [], float(reglage.get("seuil", 0.7))
        if r["classe"] is None or (classes and r["classe"] not in classes):
            return []
        if r["confiance"] is not None and r["confiance"] < seuil:
            return []
        niveau = (self.cfg.get("niveaux") or {}).get(entree["id"], entree["niveau"])
        nom = "%s : %s" % (entree["nom"], r["classe"])
        # id par classe : l'anti-répétition (delai_alerte_s) est ainsi par classe reconnue, pas pour tout le modèle
        d = {"id": "%s:%s" % (entree["id"], r["classe"]), "nom": nom, "famille_nom": entree["famille_nom"], "niveau": niveau, "boites": [], "classe": r["classe"]}
        if r["confiance"] is not None:
            d["mesure"] = round(r["confiance"], 3)
        return [d]

    # ── réaction : preuve, notification, diffusion, journal ────────────────────────────────────────────────────────────
    def _signaler(self, img, trouves):
        maintenant, delai = time.time(), float(self.cfg.get("delai_alerte_s") or 60)
        mini = NIVEAUX.index(self.cfg.get("niveau_min") if self.cfg.get("niveau_min") in NIVEAUX else "info")
        neufs = [d for d in trouves if NIVEAUX.index(d["niveau"]) >= mini and maintenant - self._dernier_signal.get(d["id"], 0) >= delai]
        if not neufs:
            return
        for d in neufs:
            self._dernier_signal[d["id"]] = maintenant
        preuve = self._preuve(img, neufs) if img is not None else ""
        niveau = max((d["niveau"] for d in neufs), key=NIVEAUX.index)
        resume = ", ".join(f"{len(d['boites']) or 1} × {d['nom']}" if d["boites"] else d["nom"] for d in neufs)
        ev = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "niveau": niveau, "resume": resume, "fichier": preuve,
              "sha256": empreinte_preuve(self.champ.table.entreprise_id, preuve),
              "detections": [{k: d[k] for k in ("id", "nom", "niveau")} | {"nb": len(d["boites"])} for d in neufs]}
        identites = [d["identite"] | {"nom": d["nom"], "score": d.get("score")} for d in neufs if d.get("identite")]
        if identites:
            ev["identites"] = identites
        _ecrire_journal(self.champ.table.entreprise_id, self.champ_id, ev)
        _vers_siem(self.champ, ev)
        try:
            from .notifications import _creer_notifications
            _creer_notifications(self.champ.table.entreprise, "alerte_vision",
                                 tf("engine/flux_vision.py:notif_titre_" + niveau, v1=self.champ.table.nom),
                                 tf("engine/flux_vision.py:notif_message", v1=self.champ.nom, v2=resume, v3=ev["date"][11:]),
                                 table=self.champ.table)
        except Exception:
            logger.exception("vision : notification")
        try:
            from .ws_notify import notifier_entreprise
            notifier_entreprise(self.champ.table.entreprise_id, evenement="alerte_vision",
                                donnees={"champ_id": self.champ_id, "niveau": niveau, "resume": resume, "fichier": preuve, "identites": ev.get("identites", [])})
        except Exception:
            logger.debug(_gt("rg5.msg.044"), exc_info=True)

    def _preuve(self, img, trouves):
        """Image annotée dans les archives du flux (nom `…_c<id>.jpg` : listée avec les autres captures de ce flux)."""
        import cv2
        copie = img.copy()
        for d in trouves:
            couleur = _COULEURS[d["niveau"]]
            for x, y, w, h in d["boites"]:
                cv2.rectangle(copie, (x, y), (x + w, y + h), couleur, 2)
                cv2.putText(copie, d["nom"][:24], (x, max(14, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, couleur, 2)
        chemin = fm.dossier(self.champ.table.entreprise_id) / fm._nom(self.champ, "jpg")
        if chemin.exists():
            return chemin.name
        ok, jpg = cv2.imencode(".jpg", copie, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            chemin.write_bytes(jpg.tobytes())
            return chemin.name
        return ""


_analyses = {}
_verrou = threading.Lock()


def demarrer(champ):
    """Lance (ou relance) l'analyse du champ ; une seule par champ."""
    if champ.type_champ not in fm.TYPES_FLUX:
        raise ValueError(_t("engine/flux_vision.py:err_type"))
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        raise ValueError(_t("engine/flux_vision.py:err_opencv"))
    with _verrou:
        a = _analyses.get(champ.id)
        if a is not None and a.is_alive():
            a.champ, a.cfg, a._reconfig = champ, config_vision(champ), time.time()
            return a
        a = _analyses[champ.id] = Analyse(champ)
        a.start()
        return a


def arreter(champ_id):
    a = _analyses.pop(champ_id, None)
    if a is not None:
        a.arret.set()


def etat(champ):
    a = _analyses.get(champ.id)
    vivante = a is not None and a.is_alive()
    return {"en_marche": vivante, "erreur": a.etat["erreur"] if a else "", "analyses": a.etat["analyses"] if a else 0,
            "derniere": a.etat["derniere"] if a else 0}


def demarrer_au_boot():
    """Appelé par apps.ready : reprend la surveillance des flux dont `actif` est vrai (pas pendant migrate/test/manage hors runserver)."""
    argv = sys.argv
    if any(a in argv for a in ("migrate", "makemigrations", "test", "collectstatic", "shell", "check")):
        return
    if argv and argv[0].endswith("manage.py"):
        if len(argv) < 2 or argv[1] != "runserver" or os.environ.get("RUN_MAIN") != "true":
            return

    def _reprise():
        time.sleep(8)                                   # la base n'est pas interrogée pendant l'initialisation des applications
        try:
            from .models import ChampDynamique
            for c in ChampDynamique.objects.select_related("table").filter(type_champ__in=fm.TYPES_FLUX, options_choix__contains='"vision"'):
                if config_vision(c).get("actif"):
                    try:
                        demarrer(c)
                    except ValueError as e:
                        logger.warning(_gt("rg6.msg.015"), c.id, e)
        except Exception:
            logger.exception(_gt("rg6.msg.016"))

    threading.Thread(target=_reprise, daemon=True, name="vision-boot").start()
