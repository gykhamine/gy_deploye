"""IDENTIFICATION DES PERSONNES — enregistrer un individu (photo + fiche) puis le reconnaître sur les flux caméra.

Principe (tout passe par les TABLES DYNAMIQUES, aucun nouveau modèle Django, aucune migration) :
  1. Une table « Personnes » (employés, visiteurs, clients, personnes surveillées…) a une colonne `image` (la photo), une colonne
     de nom, éventuellement des colonnes d'information, une colonne `json` « empreinte » (remplie automatiquement) et, si on veut,
     une colonne booléenne de consentement.
  2. À l'enregistrement de la photo, l'empreinte du visage est calculée et rangée dans la colonne `json` de la MÊME ligne :
     les analyses (recherche, export, ontologie, KPI…) la voient comme n'importe quelle colonne.
  3. Sur un flux (caméra locale, RTSP, drone, téléphone), le détecteur « identification » de flux_vision.py compare chaque visage
     vu aux empreintes enregistrées et notifie « Jean Dupont — table Employés, ligne 12 » ou « personne inconnue ».

Deux moteurs, choisis automatiquement (réglage `moteur` : auto | sface | lbph) :
  - « sface » : YuNet (détection) + SFace (empreinte de 128 valeurs). Précis. Il faut déposer les deux fichiers ONNX d'OpenCV Zoo dans
    engine/modeles_vision/ (voir LISEZMOI.md) : `face_detection_yunet_*.onnx` et `face_recognition_sface_*.onnx`.
  - « lbph » : repli SANS téléchargement (Haar + LBPH d'opencv-contrib). Beaucoup moins fiable (éclairage, angle, distance) : à
    traiter comme un INDICE, jamais comme une preuve. Désactivé comme moteur « sûr » : son score est affiché « indicatif ».

Ce module ne dépend pas de Django (seulement cv2 + numpy) ; la partie base de données est en bas, avec imports paresseux.
Configuration (fichier JSON par entreprise, à côté des archives des flux : identification.json) :
  {"moteur": "auto", "seuil": null, "inconnus": "alerte" | "info" | "critique" | "aucun",
   "tables": [{"table_id": 3, "categorie": "employe" | "visiteur" | "client" | "surveille" | "autre",
               "colonne_photo": "photo", "colonne_nom": "nom", "colonne_empreinte": "empreinte_visage",
               "colonne_consentement": "consentement" | null, "colonnes_info": ["poste", "service"]}]}
"""
from engine.i18n import t as _t, tf
import base64
import json
import logging
import threading
import time
from pathlib import Path
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

NIVEAUX_CATEGORIE = {"employe": "info", "visiteur": "info", "client": "info", "surveille": "critique", "autre": "info"}
CATEGORIES = tuple(NIVEAUX_CATEGORIE)
INCONNUS = ("aucun", "info", "alerte", "critique")
MOTEURS = ("auto", "sface", "lbph")
SEUIL_SFACE = 0.363          # similarité cosinus recommandée par OpenCV pour SFace
SEUIL_LBPH = 55.0            # distance LBPH (plus petit = plus proche)
TAILLE_LBPH = 100
MIN_VISAGE_PX = 40           # visage plus petit que ça : trop pauvre pour être identifié


def dossier_modeles():
    try:
        from django.conf import settings
        d = getattr(settings, "VISION_MODELES_DIR", None)
    except Exception:
        d = None
    return Path(d) if d else Path(__file__).parent / "modeles_vision"


def _fichier_modele(motif):
    d = dossier_modeles()
    trouves = sorted(d.glob(motif)) if d.is_dir() else []
    return str(trouves[-1]) if trouves else None


def _cascade_visage():
    try:
        from django.conf import settings
        base = Path(getattr(settings, "CASCADES_DIR", None) or Path(__file__).parent / "cascades")
    except Exception:
        base = Path(__file__).parent / "cascades"
    p = base / "haarcascades" / "haarcascade_frontalface_default.xml"
    return str(p) if p.is_file() else None


# ── moteurs ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
class Moteur:
    """Interface : visages(img) → [{"boite": (x,y,w,h), "score": f, ...}] ; empreintes(img, visage) → [dict JSON] ;
    similarite(empreinte, empreinte) → f ; seuil ; plus_grand_est_meilleur."""
    nom = ""
    fiable = True


class MoteurSFace(Moteur):
    nom = "sface"
    seuil = SEUIL_SFACE

    def __init__(self, f_detection, f_reconnaissance):
        import cv2
        self.cv2 = cv2
        self._det = cv2.FaceDetectorYN.create(f_detection, "", (320, 320), 0.8, 0.3, 5000)
        self._rec = cv2.FaceRecognizerSF.create(f_reconnaissance, "")
        self._verrou = threading.Lock()          # le détecteur garde un état interne (taille d'entrée) : un seul fil à la fois

    def visages(self, img):
        h, w = img.shape[:2]
        with self._verrou:
            self._det.setInputSize((w, h))
            _, faces = self._det.detect(img)
        sortie = []
        for f in (faces if faces is not None else []):
            x, y, bw, bh = (int(v) for v in f[:4])
            sortie.append({"boite": (max(0, x), max(0, y), bw, bh), "score": float(f[14]), "_ligne": f})
        return sortie

    def empreintes(self, img, visage):
        aligne = self._rec.alignCrop(img, visage["_ligne"])
        v = self._rec.feature(aligne).flatten().astype("float32")
        return [{"moteur": "sface", "v": [round(float(a), 6) for a in v]}]

    def similarite(self, a, b):
        import numpy as np
        x, y = np.asarray(a["v"], dtype="float32"), np.asarray(b["v"], dtype="float32")
        n = float(np.linalg.norm(x) * np.linalg.norm(y))
        return float(np.dot(x, y) / n) if n else 0.0


class MoteurLBPH(Moteur):
    nom = "lbph"
    fiable = False
    seuil = SEUIL_LBPH

    def __init__(self, f_cascade):
        import cv2
        self.cv2 = cv2
        self._c = cv2.CascadeClassifier(f_cascade)
        if self._c.empty() or not hasattr(cv2, "face"):
            raise RuntimeError(_gt("rg5.msg.018"))
        self._verrou = threading.Lock()

    def visages(self, img):
        cv2 = self.cv2
        gris = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        mini = max(MIN_VISAGE_PX // 2, int(img.shape[1] * 0.04))
        with self._verrou:
            boites = self._c.detectMultiScale(gris, 1.1, 5, minSize=(mini, mini))
        return [{"boite": tuple(int(v) for v in b), "score": 1.0, "_gris": gris} for b in boites]

    def _vignette(self, gris, boite):
        x, y, w, h = boite
        return self.cv2.resize(gris[y:y + h, x:x + w], (TAILLE_LBPH, TAILLE_LBPH))

    def _encoder(self, vignette):
        ok, png = self.cv2.imencode(".png", vignette)
        return base64.b64encode(png.tobytes()).decode("ascii")

    def decoder(self, texte):
        import numpy as np
        return self.cv2.imdecode(np.frombuffer(base64.b64decode(texte), np.uint8), self.cv2.IMREAD_GRAYSCALE)

    def vignette_de(self, visage):
        return self._vignette(visage["_gris"], visage["boite"])

    def empreintes(self, img, visage):
        """Photo d'enregistrement : le visage + quelques variantes (miroir, luminosité, légère rotation) — LBPH n'apprend rien d'un seul exemple."""
        cv2, np = self.cv2, __import__("numpy")
        base = self.vignette_de(visage)
        variantes = [base, cv2.flip(base, 1)]
        for g in (0.8, 1.2):
            variantes.append(np.clip(base.astype("float32") * g, 0, 255).astype("uint8"))
        for ang in (-8, 8):
            m = cv2.getRotationMatrix2D((TAILLE_LBPH / 2, TAILLE_LBPH / 2), ang, 1.0)
            variantes.append(cv2.warpAffine(base, m, (TAILLE_LBPH, TAILLE_LBPH), borderMode=cv2.BORDER_REPLICATE))
        return [{"moteur": "lbph", "png": self._encoder(v)} for v in variantes]


def moteur_disponible(choix="auto"):
    """(moteur, None) ou (None, message). `auto` : SFace si ses deux fichiers sont présents, sinon LBPH."""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return None, _t("engine/identification.py:err_opencv")
    det, rec = _fichier_modele("face_detection_yunet*.onnx"), _fichier_modele("face_recognition_sface*.onnx")
    if choix in ("auto", "sface") and det and rec and hasattr(__import__("cv2"), "FaceRecognizerSF"):
        try:
            return MoteurSFace(det, rec), None
        except Exception as e:
            logger.warning(_gt("rg8.msg.048"), e)
            if choix == "sface":
                return None, tf("engine/identification.py:err_sface", v1=str(e)[:120])
    elif choix == "sface":
        return None, _t("engine/identification.py:err_sface_absent")
    casc = _cascade_visage()
    if casc is None:
        return None, _t("engine/identification.py:err_aucun_moteur")
    try:
        return MoteurLBPH(casc), None
    except Exception as e:
        return None, tf("engine/identification.py:err_lbph", v1=str(e)[:120])


# ── enregistrement d'une photo ─────────────────────────────────────────────────────────────────────────────────────────
def empreintes_depuis_photo(moteur, img):
    """(liste d'empreintes JSON, None) ou (None, code d'erreur : « aucun_visage » | « plusieurs_visages » | « trop_petit »).
    Une photo d'enregistrement doit montrer UNE personne : s'il y en a plusieurs, on garde le plus grand visage seulement s'il
    domine nettement (≥ 2× la surface du suivant), sinon on refuse plutôt que d'enregistrer la mauvaise personne."""
    if img is None:
        return None, "aucun_visage"
    vs = sorted(moteur.visages(img), key=lambda v: v["boite"][2] * v["boite"][3], reverse=True)
    if not vs:
        return None, "aucun_visage"
    if len(vs) > 1 and vs[0]["boite"][2] * vs[0]["boite"][3] < 2 * vs[1]["boite"][2] * vs[1]["boite"][3]:
        return None, "plusieurs_visages"
    if min(vs[0]["boite"][2:]) < MIN_VISAGE_PX:
        return None, "trop_petit"
    return moteur.empreintes(img, vs[0]), None


# ── registre en mémoire + identification ───────────────────────────────────────────────────────────────────────────────
class Registre:
    """Toutes les personnes enregistrées d'une entreprise. `ajouter` puis `identifier(img)` ; sans état partagé avec la base."""

    def __init__(self, moteur, seuil=None):
        self.moteur = moteur
        self.seuil = float(seuil) if seuil is not None else moteur.seuil
        self.personnes = {}                   # clé → {"nom", "categorie", "infos", "empreintes": [...]}
        self._lbph, self._cles_lbph, self._sale = None, [], True
        self._verrou = threading.Lock()

    def ajouter(self, cle, nom, categorie, infos, empreintes):
        empreintes = [e for e in empreintes if e.get("moteur") == self.moteur.nom]     # empreintes d'un autre moteur : à recalculer
        if not empreintes:
            return False
        with self._verrou:
            self.personnes[cle] = {"nom": nom, "categorie": categorie, "infos": infos, "empreintes": empreintes}
            self._sale = True
        return True

    def retirer(self, cle):
        with self._verrou:
            self.personnes.pop(cle, None)
            self._sale = True

    def __len__(self):
        return len(self.personnes)

    def identifier(self, img):
        """[{boite, score, cle, nom, categorie, infos, connu}] — un élément par visage vu (connu=False : personne inconnue)."""
        if not self.personnes:
            return [{"boite": v["boite"], "connu": False, "score": 0.0, "cle": None, "nom": "", "categorie": "", "infos": {}}
                    for v in self._grands(self.moteur.visages(img))]
        resultat = []
        for v in self._grands(self.moteur.visages(img)):
            cle, score = self._meilleur(img, v)
            p = self.personnes.get(cle) if cle is not None else None
            resultat.append({"boite": v["boite"], "connu": p is not None, "score": round(score, 3), "cle": cle if p else None,
                             "nom": p["nom"] if p else "", "categorie": p["categorie"] if p else "", "infos": p["infos"] if p else {}})
        return resultat

    @staticmethod
    def _grands(visages):
        return [v for v in visages if min(v["boite"][2:]) >= MIN_VISAGE_PX]

    def _meilleur(self, img, visage):
        if self.moteur.nom == "sface":
            vu = self.moteur.empreintes(img, visage)[0]
            meilleur, cle = -1.0, None
            with self._verrou:
                items = list(self.personnes.items())
            for k, p in items:
                s = max(self.moteur.similarite(vu, e) for e in p["empreintes"])
                if s > meilleur:
                    meilleur, cle = s, k
            return (cle, meilleur) if meilleur >= self.seuil else (None, max(meilleur, 0.0))
        return self._meilleur_lbph(visage)

    def _entrainer_lbph(self):
        import numpy as np
        cv2 = self.moteur.cv2
        vignettes, etiquettes, cles = [], [], []
        for k, p in self.personnes.items():
            cles.append(k)
            for e in p["empreintes"]:
                vignettes.append(self.moteur.decoder(e["png"]))
                etiquettes.append(len(cles) - 1)
        rec = cv2.face.LBPHFaceRecognizer_create(radius=1, neighbors=8, grid_x=8, grid_y=8)
        rec.train(vignettes, np.asarray(etiquettes, dtype=np.int32))
        self._lbph, self._cles_lbph, self._sale = rec, cles, False

    def _meilleur_lbph(self, visage):
        with self._verrou:
            if self._sale or self._lbph is None:
                self._entrainer_lbph()
            rec, cles = self._lbph, self._cles_lbph
        etiquette, distance = rec.predict(self.moteur.vignette_de(visage))
        similarite = max(0.0, 1.0 - distance / 100.0)
        if distance <= self.seuil and 0 <= etiquette < len(cles):
            return cles[etiquette], similarite
        return None, similarite


# ── glue base de données (imports paresseux : rien de Django au chargement du module) ──────────────────────────────────
_CACHE_TTL_S = 15
_registres = {}                      # entreprise_id → (instant, signature, registre)
_verrou_registres = threading.Lock()


def _fichier_config(entreprise_id):
    from . import flux_media as fm
    return fm.dossier(entreprise_id) / "identification.json"


def lire_config(entreprise_id):
    try:
        d = json.loads(_fichier_config(entreprise_id).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def valider_config(corps):
    """(config nettoyée, None) ou (None, message)."""
    if not isinstance(corps, dict):
        return None, _t("engine/identification.py:err_config")
    cfg = {"moteur": corps.get("moteur", "auto"), "seuil": corps.get("seuil"), "inconnus": corps.get("inconnus", "alerte"), "tables": []}
    if cfg["moteur"] not in MOTEURS:
        return None, _t("engine/identification.py:err_moteur")
    if cfg["inconnus"] not in INCONNUS:
        return None, _t("engine/identification.py:err_inconnus")
    if cfg["seuil"] is not None:
        try:
            cfg["seuil"] = float(cfg["seuil"])
        except (TypeError, ValueError):
            return None, _t("engine/identification.py:err_seuil")
    for e in corps.get("tables") or []:
        if not isinstance(e, dict) or not isinstance(e.get("table_id"), int):
            return None, _t("engine/identification.py:err_table")
        cat = e.get("categorie", "autre")
        if cat not in CATEGORIES or not all(isinstance(e.get(k), str) and e.get(k) for k in ("colonne_photo", "colonne_nom", "colonne_empreinte")):
            return None, _t("engine/identification.py:err_table")
        cfg["tables"].append({"table_id": e["table_id"], "categorie": cat, "colonne_photo": e["colonne_photo"], "colonne_nom": e["colonne_nom"],
                              "colonne_empreinte": e["colonne_empreinte"], "colonne_consentement": e.get("colonne_consentement") or None,
                              "colonnes_info": [s for s in (e.get("colonnes_info") or []) if isinstance(s, str)][:12]})
    return cfg, None


def ecrire_config(entreprise_id, cfg):
    _fichier_config(entreprise_id).write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    with _verrou_registres:
        _registres.pop(entreprise_id, None)


def _lire_image(valeur):
    """Image locale sous MEDIA_ROOT uniquement (jamais d'URL distante : pas de requête sortante déclenchée par une donnée)."""
    import cv2
    from django.conf import settings
    if not valeur or str(valeur).lower().startswith(("http://", "https://")):
        return None
    racine = Path(settings.MEDIA_ROOT).resolve()
    chemin = (racine / str(valeur).lstrip("/").removeprefix(str(settings.MEDIA_URL).lstrip("/"))).resolve()
    if racine != chemin and racine not in chemin.parents:
        return None
    return cv2.imread(str(chemin)) if chemin.is_file() else None


def enregistrer_ligne(entreprise_id, table_id, enregistrement_id, moteur=None):
    """Calcule et range l'empreinte d'UNE ligne. (nb d'empreintes, None) ou (0, code d'erreur). Appelée à l'ajout/modification d'une photo."""
    from .models import ChampDynamique, ValeurChamp
    cfg = next((t for t in lire_config(entreprise_id).get("tables", []) if t["table_id"] == table_id), None)
    if cfg is None:
        return 0, "table_non_configuree"
    if moteur is None:
        moteur, err = moteur_disponible(lire_config(entreprise_id).get("moteur", "auto"))
        if moteur is None:
            return 0, err
    champs = {c.slug: c for c in ChampDynamique.objects.filter(table_id=table_id, slug__in=[cfg["colonne_photo"], cfg["colonne_empreinte"]])}
    if cfg["colonne_photo"] not in champs or cfg["colonne_empreinte"] not in champs:
        return 0, "colonne_absente"
    vp = ValeurChamp.objects.filter(enregistrement_id=enregistrement_id, champ=champs[cfg["colonne_photo"]]).first()
    empreintes, err = empreintes_depuis_photo(moteur, _lire_image(vp.valeur if vp else ""))
    ValeurChamp.objects.update_or_create(enregistrement_id=enregistrement_id, champ=champs[cfg["colonne_empreinte"]],
                                         defaults={"valeur": json.dumps(empreintes) if empreintes else ""})
    return (len(empreintes), None) if empreintes else (0, err)


def _signature(entreprise_id, tables):
    from django.db.models import Max
    from .models import Enregistrement
    sig = []
    for t in tables:
        a = Enregistrement.objects.filter(table_id=t["table_id"]).aggregate(n=Max("maj_le"), c=Max("id"))
        sig.append((t["table_id"], str(a["n"]), a["c"]))
    return tuple(sig)


def _construire(entreprise_id, cfg, moteur):
    from .models import Enregistrement
    reg = Registre(moteur, cfg.get("seuil"))
    for t in cfg.get("tables", []):
        slugs = [t["colonne_nom"], t["colonne_empreinte"], t["colonne_photo"]] + t["colonnes_info"] + ([t["colonne_consentement"]] if t["colonne_consentement"] else [])
        for e in Enregistrement.objects.filter(table_id=t["table_id"]).prefetch_related("valeurs__champ"):
            v = {x.champ.slug: x.valeur for x in e.valeurs.all() if x.champ.slug in slugs}
            if t["colonne_consentement"] and str(v.get(t["colonne_consentement"], "")).lower() not in ("1", "true", "vrai"):
                continue                                           # pas de consentement : jamais dans le registre
            try:
                empreintes = json.loads(v.get(t["colonne_empreinte"]) or "[]")
            except ValueError:
                empreintes = []
            if not any(x.get("moteur") == moteur.nom for x in empreintes):                  # empreinte absente ou d'un autre moteur
                empreintes, _ = empreintes_depuis_photo(moteur, _lire_image(v.get(t["colonne_photo"]))) or ([], None)
                if empreintes:
                    from .models import ValeurChamp, ChampDynamique
                    champ = ChampDynamique.objects.filter(table_id=t["table_id"], slug=t["colonne_empreinte"]).first()
                    if champ:
                        ValeurChamp.objects.update_or_create(enregistrement=e, champ=champ, defaults={"valeur": json.dumps(empreintes)})
            infos = {s: v.get(s, "") for s in t["colonnes_info"]}
            reg.ajouter(f"{t['table_id']}:{e.id}", v.get(t["colonne_nom"]) or f"#{e.id}", t["categorie"], dict(infos, table_id=t["table_id"], ligne_id=e.id), empreintes or [])
    return reg


def registre(entreprise_id):
    """(Registre, None) ou (None, message). Mis en cache 15 s et reconstruit seulement si une table configurée a changé."""
    cfg = lire_config(entreprise_id)
    if not cfg.get("tables"):
        return None, _t("engine/identification.py:err_non_configure")
    with _verrou_registres:
        c = _registres.get(entreprise_id)
        if c and time.time() - c[0] < _CACHE_TTL_S:
            return c[2], None
    sig = _signature(entreprise_id, cfg["tables"])
    with _verrou_registres:
        c = _registres.get(entreprise_id)
        if c and c[1] == sig:
            _registres[entreprise_id] = (time.time(), sig, c[2])
            return c[2], None
    moteur, err = moteur_disponible(cfg.get("moteur", "auto"))
    if moteur is None:
        return None, err
    reg = _construire(entreprise_id, cfg, moteur)
    with _verrou_registres:
        _registres[entreprise_id] = (time.time(), sig, reg)
    return reg, None


def niveau_identite(cfg, r):
    """Niveau d'alerte d'une identité vue : celui de sa catégorie ; pour un inconnu, le réglage `inconnus` (« aucun » = ignoré)."""
    if r["connu"]:
        return NIVEAUX_CATEGORIE.get(r["categorie"], "info")
    return cfg.get("inconnus", "alerte")


def analyser(entreprise_id, img):
    """Pour flux_vision : (liste de détections au format de flux_vision, None) ou ([], message d'erreur).
    Une détection par personne reconnue (id « identification:<table>:<ligne> ») + une pour l'ensemble des inconnus."""
    reg, err = registre(entreprise_id)
    if reg is None:
        return [], err
    cfg = lire_config(entreprise_id)
    vus = reg.identifier(img)
    sortie, inconnus = [], []
    for r in vus:
        if r["connu"]:
            fiabilite = "" if reg.moteur.fiable else " " + _t("engine/identification.py:indicatif")
            sortie.append({"id": "identification:" + r["cle"], "nom": r["nom"] + fiabilite, "famille_nom": _t("engine/identification.py:famille"),
                           "niveau": niveau_identite(cfg, r), "boites": [list(r["boite"])], "score": r["score"], "identite": dict(r["infos"], categorie=r["categorie"])})
        elif niveau_identite(cfg, r) != "aucun":
            inconnus.append(list(r["boite"]))
    if inconnus:
        sortie.append({"id": "identification:inconnu", "nom": _t("engine/identification.py:inconnu"), "famille_nom": _t("engine/identification.py:famille"),
                       "niveau": cfg.get("inconnus", "alerte"), "boites": inconnus})
    return sortie, None


def assurer_colonne_empreinte(table_id, slug="empreinte_visage"):
    """Crée (si absente) la colonne `json` qui reçoit l'empreinte du visage ; retourne son slug. Colonne facultative, jamais saisie à la main."""
    from django.db.models import Max
    from .models import ChampDynamique
    existant = ChampDynamique.objects.filter(table_id=table_id, slug=slug).first()
    if existant is not None:
        return existant.slug
    suivant = (ChampDynamique.objects.filter(table_id=table_id).aggregate(m=Max("ordre"))["m"] or 0) + 1
    ChampDynamique.objects.create(table_id=table_id, slug=slug, nom=_t("engine/identification.py:nom_colonne"), type_champ="json", obligatoire=False, ordre=suivant)
    return slug


def lire_image_octets(donnees):
    """Octets JPEG/PNG → image BGR (ou None). Pour les photos envoyées directement et les captures de flux."""
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(donnees, np.uint8), cv2.IMREAD_COLOR) if donnees else None


def identifier_image(entreprise_id, img):
    """Identification directe sur une image, SANS rien enregistrer. (liste de visages, moteur, message d'erreur).
    Si aucune table n'est configurée, les visages sont détectés et renvoyés comme inconnus (présence + position seulement)."""
    cfg = lire_config(entreprise_id)
    reg, err = registre(entreprise_id) if cfg.get("tables") else (None, None)
    if reg is None:
        moteur, err = moteur_disponible(cfg.get("moteur", "auto"))
        if moteur is None:
            return [], None, err
        reg = Registre(moteur, cfg.get("seuil"))
    return reg.identifier(img), reg.moteur, None
