"""MODÈLE FANTÔME D'ONTOLOGIE : un petit réseau de neurones qui apprend, à l'insu de l'utilisateur, COMMENT il parcourt le graphe
d'ontologie (ce qu'il cherche, où il va après tel nœud), puis, une fois qu'il l'a compris, mène l'exploration à sa place.

Il ne dialogue JAMAIS avec l'utilisateur : il observe, puis il anime l'écran (caméra, mise au focus, trace des liens, recherche).
Même esprit que le modèle fantôme de décision (engine/modele_fantome.py, engine/memoire_decision.py) : entraînement incrémental,
paresseux, fichier signé, aucune règle écrite à la main sur ce que « chercher » veut dire.

Principe
1. OBSERVATION D'ABORD — à l'installation le réseau ne comprend rien (poids aléatoires). À chaque pas réel de l'utilisateur (clic sur
   un nœud, recherche, voisin ouvert), la page (static/engine/js/fantome_ontologie.js) classe d'abord tous les nœuds avec le réseau
   courant (prédire), PUIS envoie le nœud réellement choisi + quelques nœuds non choisis (apprendre). Le rang que le réseau avait donné
   au nœud réellement choisi mesure sa COMPRÉHENSION (prequential : on n'évalue jamais sur ce qu'on vient d'apprendre).
2. SEUIL — la compréhension est le gain sur le hasard : (taux de réussite dans les 3 premiers − hasard) / (1 − hasard), sur les
   FENETRE derniers pas. En dessous de SEUIL_ASSISTANCE (ou avant MIN_EPISODES pas) le modèle reste en phase « observation » : la page
   garde son animation générique. Au-dessus il passe en phase « assistance » ; il n'en ressort que sous SEUIL_RETOUR (hystérésis).
3. ASSISTANCE — la page évalue le réseau pour proposer ce que l'utilisateur CHERCHE probablement, DEVRAIT regarder (un nœud central
   qu'il n'a jamais vu, dans sa zone d'intérêt) et POURRAIT explorer (tirage pondéré), et l'écran s'anime seul en conséquence.

Où est le calcul : l'ENTRAÎNEMENT est ici (numpy pur, sur le serveur, jamais plus de PAS_ENTRAINEMENT pas d'Adam par envoi) ; l'INFÉRENCE
est dans la page (le graphe y est déjà chargé : les poids, ~200 nombres, lui sont envoyés). Les caractéristiques sont toutes dans [0, 1],
calculées dans la page à partir du graphe et de la mémoire de parcours renvoyée par `etat_public` ; le serveur ne lit jamais le graphe
ni le contenu des tables : seulement des identifiants de nœuds, leur type et des vecteurs de nombres.

UN SEUL modèle par entreprise, appris de tous ses utilisateurs ensemble. Fichier : <MEDIA_ROOT>/systeme/modeles_fantomes/ontologie_e<entreprise>.agentgy
(signature « AGENTGYO\\0 » + version + HMAC-SHA256 + archive npz, aucun pickle, écriture atomique ; un fichier altéré est mis de côté
(`.invalide`) et le modèle repart de zéro). COMPATIBILITÉ : le fichier mémorise le NOM de ses colonnes d'entrée ; au chargement chaque
poids rejoint sa colonne par son nom (colonne ajoutée = poids nuls), comme pour le modèle fantôme de décision.
Module PUR (aucun import Django) : testable avec des dictionnaires.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import tempfile
import time

import numpy as np

from . import reseau_neuronal as rn
from engine.i18n import t as _gt  # noqa: E402

# Colonnes d'entrée du réseau, TOUTES dans [0, 1] ; leur calcul vit dans static/engine/js/fantome_ontologie.js (même ordre, mêmes noms).
FEATURES = (
    "voisin_direct",          # le candidat est lié au nœud actuellement au focus
    "voisin_du_precedent",    # le candidat est lié au nœud vu juste avant (le contexte à deux pas)
    "distance_deux",          # le candidat est à deux liens du focus
    "degre",                  # nombre de liens du candidat, normalisé par le maximum du graphe
    "meme_type",              # même type de nœud que le focus
    "freq_noeud",             # part des visites passées (de tous les utilisateurs) sur ce nœud
    "freq_type",              # part des visites passées sur des nœuds de ce type
    "transition",             # part des départs du focus qui ont déjà mené à ce candidat
    "recence",                # visite récente de ce candidat (décroît avec le temps ; 0 s'il n'a jamais été visité)
    "correspond_recherche",   # le libellé du candidat contient la dernière recherche
    "jamais_vu",              # le candidat n'a encore jamais été visité
)
D = len(FEATURES)
LARGEUR = 8

MIN_EPISODES = 20          # pas réels observés avant toute assistance
SEUIL_ASSISTANCE = 0.35    # compréhension à atteindre pour passer en assistance
SEUIL_RETOUR = 0.15        # compréhension sous laquelle on retourne en observation
FENETRE = 40               # pas récents pris en compte pour la compréhension
TOP = 3                    # « compris » = le nœud réellement choisi était dans les TOP premiers du classement

MAX_EPISODES_PAR_ENVOI, MAX_PAS_PAR_ENVOI, MAX_RECHERCHES_PAR_ENVOI, MAX_NEGATIFS = 40, 100, 20, 12
MAX_REJEU, TAILLE_LOT, PAS_ENTRAINEMENT, LR = 600, 96, 6, 0.03
MAX_NOEUDS_MEMOIRE, MAX_TRANSITIONS, MAX_TERMES, LONGUEUR_ID, LONGUEUR_TERME = 500, 800, 60, 120, 40
SEP = "\t"                 # séparateur de la clé d'une transition « de<SEP>vers »

_MAGIE = b"AGENTGYO\0"
_VERSION = 1
PHASE_OBSERVATION, PHASE_ASSISTANCE = "observation", "assistance"


# ── Valeurs d'entrée : jamais de confiance dans ce que la page envoie ──
def _vecteur(brut) -> list | None:
    """Liste de D nombres finis ramenés dans [0, 1], ou None si la forme n'est pas la bonne."""
    if not isinstance(brut, (list, tuple)) or len(brut) != D:
        return None
    sortie = []
    for v in brut:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        sortie.append(0.0 if not math.isfinite(f) else min(1.0, max(0.0, f)))
    return sortie


def _texte(brut, longueur: int) -> str:
    return str(brut).strip()[:longueur] if isinstance(brut, (str, int)) else ""


def _elaguer(compteurs: dict, maximum: int) -> None:
    """Garde les `maximum` entrées les plus fréquentes (mémoire bornée)."""
    if len(compteurs) > maximum:
        for cle, _n in sorted(compteurs.items(), key=lambda kv: kv[1])[: len(compteurs) - maximum]:
            del compteurs[cle]


class Fantome:
    def __init__(self, reseau: rn.Reseau, meta: dict, X=None, Y=None, W=None):
        self.reseau, self.meta = reseau, meta
        self.X = np.zeros((0, D)) if X is None else X
        self.Y = np.zeros((0, 1)) if Y is None else Y
        self.W = np.zeros((0, 1)) if W is None else W
        self.sale = False   # à réécrire sur disque

    # ── Naissance ──
    @classmethod
    def nouveau(cls, graine: int = 4242, maintenant: float | None = None, proprietaire: str | None = None) -> "Fantome":
        maintenant = time.time() if maintenant is None else maintenant
        f = cls(rn.Reseau.nouveau(D, LARGEUR, 1, graine=graine), {**_meta_defaut(maintenant), "proprietaire": proprietaire})
        f.sale = True
        return f

    # ── Compréhension ──
    def comprehension(self) -> float:
        """Gain sur le hasard dans [0, 1], pondéré par le nombre de pas observés (un modèle qui n'a rien vu ne comprend rien)."""
        h = self.meta["historique"][-FENETRE:]
        if len(h) < 5:
            return 0.0
        reussite = sum(x[0] for x in h) / len(h)
        hasard = sum(x[1] for x in h) / len(h)
        gain = (reussite - hasard) / (1.0 - hasard) if hasard < 1.0 else 0.0
        return round(max(0.0, min(1.0, gain)) * min(1.0, len(h) / MIN_EPISODES), 4)

    def _maj_phase(self) -> None:
        c, n = self.comprehension(), len(self.meta["historique"])
        if self.meta["phase"] == PHASE_OBSERVATION and n >= MIN_EPISODES and c >= SEUIL_ASSISTANCE:
            self.meta["phase"] = PHASE_ASSISTANCE
        elif self.meta["phase"] == PHASE_ASSISTANCE and n >= MIN_EPISODES and c < SEUIL_RETOUR:
            self.meta["phase"] = PHASE_OBSERVATION

    # ── Observation (apprentissage) ──
    def observer(self, episodes, pas=None, recherches=None, maintenant: float | None = None) -> dict:
        """`episodes` : [{positif: [D], negatifs: [[D], …], rang: int|None, candidats: int|None}] — un épisode = un choix réel de
        l'utilisateur (le nœud choisi et des nœuds qu'il n'a pas choisis, dans le même contexte) ; `rang` = place que le réseau avait
        donnée au nœud choisi AVANT de l'apprendre. `pas` : [{de, vers, type}] — le parcours réel (mémoire des visites).
        `recherches` : [terme] — ce que l'utilisateur a tapé. Retourne {episodes_appris, perte}."""
        maintenant = time.time() if maintenant is None else maintenant
        nouveaux_x, nouveaux_y, nouveaux_w = [], [], []
        appris = 0
        for e in list(episodes or [])[:MAX_EPISODES_PAR_ENVOI]:
            if not isinstance(e, dict):
                continue
            positif = _vecteur(e.get("positif"))
            negatifs = [v for v in (_vecteur(x) for x in list(e.get("negatifs") or [])[:MAX_NEGATIFS]) if v is not None]
            if positif is None or not negatifs:
                continue
            nouveaux_x.append(positif), nouveaux_y.append([1.0]), nouveaux_w.append([2.0])
            for v in negatifs:
                nouveaux_x.append(v), nouveaux_y.append([0.0]), nouveaux_w.append([1.0])
            appris += 1
            try:
                rang, candidats = int(e.get("rang")), int(e.get("candidats"))
            except (TypeError, ValueError):
                rang = candidats = 0
            if rang >= 1 and candidats >= 2:
                self.meta["historique"].append([1 if rang <= TOP else 0, round(min(1.0, TOP / candidats), 4)])
        del self.meta["historique"][:-FENETRE * 5]   # la mémoire de compréhension reste bornée
        self.meta["episodes"] += appris
        perte = None
        if nouveaux_x:
            perte = self._entrainer(np.array(nouveaux_x), np.array(nouveaux_y), np.array(nouveaux_w))
        self._memoriser(list(pas or [])[:MAX_PAS_PAR_ENVOI], list(recherches or [])[:MAX_RECHERCHES_PAR_ENVOI], maintenant)
        self._maj_phase()
        self.meta["maj_ts"] = maintenant
        self.sale = True
        return {"episodes_appris": appris, "perte": perte}

    def _entrainer(self, x, y, w) -> float | None:
        """Quelques pas d'Adam sur le nouveau + un rejeu d'échantillons passés (contre l'oubli). Paresseux : jamais plus de PAS_ENTRAINEMENT."""
        self.X, self.Y, self.W = (np.vstack([self.X, x]), np.vstack([self.Y, y]), np.vstack([self.W, w]))
        if len(self.X) > MAX_REJEU:
            self.X, self.Y, self.W = self.X[-MAX_REJEU:], self.Y[-MAX_REJEU:], self.W[-MAX_REJEU:]
        rng = np.random.default_rng(self.meta["episodes"] + len(self.X))
        perte = None
        for _ in range(PAS_ENTRAINEMENT):
            n_ancien = max(0, len(self.X) - len(x))
            reste = max(0, TAILLE_LOT - len(x))
            idx = rng.choice(n_ancien, size=min(reste, n_ancien), replace=False) if n_ancien and reste else np.array([], dtype=int)
            lot_x = np.vstack([x, self.X[idx]]) if len(idx) else x
            lot_y = np.vstack([y, self.Y[idx]]) if len(idx) else y
            lot_w = np.vstack([w, self.W[idx]]) if len(idx) else w
            perte = float(self.reseau.pas(lot_x, lot_y, lot_w, LR))
        self.meta["perte"] = None if perte is None or not math.isfinite(perte) else round(perte, 6)
        return self.meta["perte"]

    def _memoriser(self, pas: list, recherches: list, maintenant: float) -> None:
        m = self.meta
        for p in pas:
            if not isinstance(p, dict):
                continue
            vers, de, type_ = _texte(p.get("vers"), LONGUEUR_ID), _texte(p.get("de"), LONGUEUR_ID), _texte(p.get("type"), LONGUEUR_ID)
            if not vers:
                continue
            m["visites"][vers] = m["visites"].get(vers, 0) + 1
            m["derniere"][vers] = round(maintenant, 1)
            if type_:
                m["types"][type_] = m["types"].get(type_, 0) + 1
            if de and de != vers:
                cle = de + SEP + vers
                m["transitions"][cle] = m["transitions"].get(cle, 0) + 1
        for terme in recherches:
            terme = _texte(terme, LONGUEUR_TERME).lower()
            if len(terme) >= 2:
                m["termes"][terme] = m["termes"].get(terme, 0) + 1
        for cle, maximum in (("visites", MAX_NOEUDS_MEMOIRE), ("transitions", MAX_TRANSITIONS), ("termes", MAX_TERMES)):
            _elaguer(m[cle], maximum)
        for cle in [k for k in m["derniere"] if k not in m["visites"]]:
            del m["derniere"][cle]

    # ── Ce que la page reçoit ──
    def etat_public(self) -> dict:
        return {
            "version": _VERSION, "entrees": list(FEATURES), "phase": self.meta["phase"],
            "comprehension": self.comprehension(), "episodes": self.meta["episodes"],
            "seuil": SEUIL_ASSISTANCE, "minimum": MIN_EPISODES,
            "poids": {k: np.round(self.reseau.p[k], 5).tolist() for k in ("W1", "b1", "W2", "b2", "W3", "b3")},
            "memoire": {k: self.meta[k] for k in ("visites", "types", "transitions", "derniere", "termes")},
        }

    def predire(self, x) -> np.ndarray:
        """Score de pertinence de chaque ligne de `x` (n, D) — identique à l'inférence faite par la page."""
        return self.reseau.predire(np.atleast_2d(np.asarray(x, dtype=float)))[:, 0]


def _meta_defaut(maintenant: float = 0.0) -> dict:
    return {"cree_ts": maintenant, "maj_ts": maintenant, "episodes": 0, "phase": PHASE_OBSERVATION, "perte": None, "historique": [],
            "visites": {}, "types": {}, "transitions": {}, "derniere": {}, "termes": {}, "proprietaire": None}


# ── Fichier signé ──
def _signer(cle: bytes, corps: bytes, version: int = _VERSION) -> bytes:
    return hmac.new(cle, _MAGIE + bytes([version]) + corps, hashlib.sha256).digest()


def serialiser(f: Fantome, cle: bytes) -> bytes:
    tableaux = f.reseau.tableaux()
    tableaux.update({k: getattr(f, k).astype(np.float32) for k in ("X", "Y", "W")})
    meta = {**f.meta, "format": _VERSION, "entrees": list(FEATURES)}
    tableaux["meta"] = np.frombuffer(json.dumps(meta, separators=(",", ":")).encode("utf-8"), dtype=np.uint8)
    corps = rn.vers_octets(tableaux)
    return _MAGIE + bytes([_VERSION]) + _signer(cle, corps) + corps


def deserialiser(octets: bytes, cle: bytes, proprietaire: str | None = None, cles_anciennes: tuple = ()) -> Fantome:
    """ValueError si l'en-tête, la signature, le contenu ou les dimensions sont invalides.
    ISOLATION DES ENTREPRISES : `proprietaire` (ex. « entreprise:7 ») est inscrit dans le fichier ; un fichier qui déclare un AUTRE propriétaire
    est refusé. `cles_anciennes` : clés d'avant la clé par entreprise ; un fichier ainsi signé est accepté une fois puis réécrit avec la bonne."""
    tete = len(_MAGIE) + 1
    if len(octets) < tete + 32 or octets[:len(_MAGIE)] != _MAGIE or octets[len(_MAGIE)] != _VERSION:
        raise ValueError(_gt("rg5.msg.037"))
    signature, corps = octets[tete:tete + 32], octets[tete + 32:]
    ancienne_cle = False
    if not hmac.compare_digest(signature, _signer(cle, corps)):
        if not any(hmac.compare_digest(signature, _signer(k, corps)) for k in cles_anciennes):
            raise ValueError(_gt("rg5.msg.038"))
        ancienne_cle = True
    z = rn.depuis_octets(corps)
    try:
        meta = json.loads(np.array(z["meta"], dtype=np.uint8).tobytes().decode("utf-8"))
        reseau = rn.Reseau.depuis_tableaux(z)
        X, Y, W = (np.array(z[k], dtype=float) for k in ("X", "Y", "W"))
    except (KeyError, UnicodeDecodeError) as exc:
        raise ValueError(_gt("rg8.msg.041")) from exc
    entrees = meta.get("entrees") if isinstance(meta, dict) else None
    if not isinstance(entrees, list) or len(entrees) != reseau.d_in or X.ndim != 2 or X.shape[1] != len(entrees) \
            or Y.shape != (len(X), 1) or W.shape != Y.shape or reseau.d_out != 1:
        raise ValueError(_gt("rg8.msg.040"))
    migre = False
    try:
        if reseau.remapper_entrees(entrees, FEATURES):   # colonnes ajoutées / déplacées / retirées : réalignées par leur nom
            X, migre = rn.reordonner_colonnes(X, entrees, FEATURES), True
    except ValueError as exc:
        raise ValueError(_gt("rg5.msg.039")) from exc
    f = Fantome(reseau, {**_meta_defaut(), **{k: v for k, v in meta.items() if k in _meta_defaut()}}, X, Y, W)
    if proprietaire is not None:
        declare = f.meta.get("proprietaire")
        if declare is not None and declare != proprietaire:
            raise ValueError(_gt("rg8.msg.040"))   # fichier d'une AUTRE entreprise : refusé, jamais chargé
        if declare is None or ancienne_cle:
            migre = True   # fichier d'avant l'isolation : réécrit avec son propriétaire et la clé de son entreprise
        f.meta["proprietaire"] = proprietaire
    f.sale = migre
    return f


def ecrire(chemin: str, f: Fantome, cle: bytes) -> None:
    """Écriture atomique (fichier temporaire + remplacement), droits 0600."""
    dossier = os.path.dirname(chemin) or "."
    os.makedirs(dossier, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dossier, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fichier:
            fichier.write(serialiser(f, cle))
            fichier.flush()
            os.fsync(fichier.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, chemin)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    f.sale = False


def charger(chemin: str, cle: bytes, proprietaire: str | None = None, cles_anciennes: tuple = ()) -> Fantome:
    """Modèle lu depuis son fichier ; modèle neuf (qui ne comprend rien) s'il n'existe pas. Un fichier altéré, ou qui n'est pas celui de
    cette entreprise, est mis de côté (`.invalide`) et jamais chargé."""
    try:
        with open(chemin, "rb") as fichier:
            return deserialiser(fichier.read(), cle, proprietaire, cles_anciennes)
    except FileNotFoundError:
        return Fantome.nouveau(proprietaire=proprietaire)
    except (ValueError, OSError):
        try:
            os.replace(chemin, chemin + ".invalide")
        except OSError:
            pass
        return Fantome.nouveau(proprietaire=proprietaire)
