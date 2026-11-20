"""MODÈLE FANTÔME DU SIEM : apprend, à côté des 50 règles de base, ce qui est anormal ou malveillant dans CETTE plateforme.

Les règles (siem_corr/) ne sont pas remplacées : elles restent la vérité de départ. Elles servent de PROFESSEUR (étiquette faible : un compte
sur lequel une règle vient de sonner vaut « suspect ») et l'humain, de juge final (étiquette forte : un message jugé mauvais ou bon, un compte
déverrouillé à tort). Le réseau apprend alors, à partir de mesures BRUTES et non des sorties des règles, à reconnaître ce que les règles
ne savent pas écrire. Trois têtes, même mécanique :

  systeme   l'état de la machine (latences, erreurs, silence d'un collecteur, rafales de refus)
  compte    le comportement d'un utilisateur (gestes sensibles, nouveauté de l'IP, heures rares, volume, tables parcourues) et ce que sa
            messagerie dit de lui (score de ses messages, avis négatifs reçus)
  message   un message de la messagerie : forme du texte (liens, majuscules, répétition), mots hachés (jamais le texte), rafale et doublons
            de l'expéditeur, avis de ses lecteurs

« SES PROPRES BASES » : chaque tête garde, par mesure, une moyenne et une variance glissantes de ce qui est CALME (jamais de ce qu'elle juge
suspect : un attaquant ne déplace pas la référence). Le réseau reçoit la mesure ET son écart à sa propre base.
Au départ la sortie vaut 0 partout : il se tait tant qu'il n'a pas appris. Module PUR (numpy, aucun import Django), testable avec des dictionnaires.

Fichier `siem_fantome.agentgy` (MEDIA_ROOT/systeme/) : signature « SIEMFANT\\0 » + version + HMAC-SHA256 (clé du SIEM, rotation comprise) + archive
npz sans pickle ; écriture atomique. Un fichier altéré est refusé, jamais chargé. Les colonnes sont retrouvées par leur NOM : on ajoute une
mesure sans casser ce qui a été appris.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import tempfile

import numpy as np

from . import reseau_neuronal as rn
from .siem_fantome_texte import (GRAMMES, HACHAGE, caracteristiques_texte, empreinte_texte, grammes, hacher,  # noqa: F401 — réexportés
                                 etiquette_faible_message)
from engine.i18n import tf as _siem_tf  # noqa: E402

GRAVITE = {"info": 0.2, "moyenne": 0.5, "haute": 0.8, "critique": 1.0}
LARGEUR, MAX_ECH, PAS, LR, ALPHA = 16, 300, 20, 0.02, 0.05
MAGIE, VERSION = b"SIEMFANT\x00", 1
GRAMMES_TETES = {"message": GRAMMES}          # dimensions du sac de trigrammes, par tête (colonnes g0…)

# Mesures nommées. « p_ » = proportion déjà dans [0, 1] ; le reste est un compte ou une durée, écrasé par log (tanh(log1p(v)/3)).
NOMS_SYSTEME = ("base_ms", "cache_ms", "redis_ms", "erreurs", "collecteur_retard_s", "refus_5min", "p_cache_plein", "parquet_deltas")
NOMS_COMPTE = ("gestes_10min", "tables_distinctes", "sensibles_heure", "administration_heure", "exports_heure", "retraits_heure", "refus_heure",
               "ips_heure", "p_ip_nouvelle", "p_heure_rare", "volume_jour_x", "age_compte_j",
               "messages_heure", "p_message_suspect_max", "p_message_suspect_moy", "p_avis_negatifs_recus")
NOMS_MESSAGE = ("longueur", "p_majuscules", "p_chiffres", "p_ponctuation", "liens", "liens_distincts", "p_repetition", "fichier",
                "rafale_expediteur", "conversations_heure", "doublons_heure", "age_expediteur_j", "p_avis_negatifs_expediteur", "avis_positifs_expediteur",
                # contournements, liens trompeurs, intention (siem_fantome_texte) : ajoutés après coup, retrouvés par leur nom
                "invisibles", "p_script_mixte", "p_leet", "lettres_espacees", "liens_raccourcis", "liens_ip", "liens_suspects", "liens_arobase",
                "p_urgence", "p_secret", "p_appat")
TETES = {"systeme": (NOMS_SYSTEME, 0), "compte": (NOMS_COMPTE, 0), "message": (NOMS_MESSAGE, HACHAGE)}

# ── Une tête : réseau + ses propres bases + mémoire de rejeu ─────────────────────────────────────────────────────────────
def _ecraser(nom: str, v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return min(1.0, max(0.0, v)) if nom.startswith("p_") else math.tanh(math.log1p(max(0.0, v)) / 3)


class Tete:
    def __init__(self, noms, nb_hash: int = 0, graine: int = 7, nb_gram: int = 0):
        self.noms, self.nb_hash, self.nb_gram = tuple(noms), nb_hash, nb_gram
        self.reseau = rn.Reseau.nouveau(self.d, LARGEUR, 1, graine, sortie_nulle=True)   # répond 0 : silence tant qu'il n'a rien appris
        self.moy, self.var, self.n_base = np.zeros(len(self.noms)), np.full(len(self.noms), 0.05), 0
        self.X, self.Y, self.W = np.zeros((0, self.d)), np.zeros((0, 1)), np.zeros((0, 1))
        self.n = self.n_fort = 0
        self.n_pos = self.n_neg = 0   # étiquettes apprises de chaque classe (≥ 0,5 / < 0,5), toutes sources : un modèle n'est pas prêt s'il n'a vu qu'une classe
        self.perte = None

    @property
    def d(self) -> int:
        return 2 * len(self.noms) + self.nb_hash + self.nb_gram

    def colonnes(self) -> list[str]:
        return ([f"v:{n}" for n in self.noms] + [f"d:{n}" for n in self.noms] + [f"h{k}" for k in range(self.nb_hash)]
                + [f"g{k}" for k in range(self.nb_gram)])

    def ecart(self, valeurs: dict) -> np.ndarray:
        """Écart de chaque mesure à SA base, dans ]-1, 1[ (0 tant que la base n'existe pas)."""
        s = np.array([_ecraser(n, valeurs.get(n, 0.0)) for n in self.noms])
        if not self.n_base:
            return np.zeros(len(s))
        return np.tanh((s - self.moy) / (np.sqrt(self.var) + 0.05) / 3)

    def vecteur(self, valeurs: dict, texte: str = "") -> np.ndarray:
        s = np.array([_ecraser(n, valeurs.get(n, 0.0)) for n in self.noms])
        mots = hacher(texte, self.nb_hash) if self.nb_hash else np.zeros(0)
        trigrammes = grammes(texte, self.nb_gram) if self.nb_gram else np.zeros(0)
        return np.concatenate([s, self.ecart(valeurs), mots, trigrammes])

    def predire(self, x) -> float:
        return float(np.clip(self.reseau.predire(x)[0, 0], 0.0, 1.0))

    def mature(self, minimum: int, fort: int = 0, classe: int = 0) -> bool:
        """Assez d'échantillons, assez d'avis humains (`fort`) et au moins `classe` étiquettes de CHAQUE classe (calme / suspect)."""
        return self.n >= minimum and self.n_fort >= fort and min(self.n_pos, self.n_neg) >= classe

    def observer(self, valeurs: dict, score: float) -> None:
        """Met à jour SA base avec ce qui est calme (score < 0.5), jamais avec ce que le réseau juge suspect."""
        if score >= 0.5:
            return
        s = np.array([_ecraser(n, valeurs.get(n, 0.0)) for n in self.noms])
        if not self.n_base:
            self.moy, self.n_base = s.copy(), 1
            return
        delta = s - self.moy
        self.moy = self.moy + ALPHA * delta
        self.var = (1 - ALPHA) * (self.var + ALPHA * delta * delta)
        self.n_base += 1

    def apprendre(self, x, y: float, poids: float, fort: bool = False) -> None:
        x = np.asarray(x, dtype=float).reshape(1, -1)
        self.X, self.Y, self.W = (np.vstack([self.X, x])[-MAX_ECH:], np.vstack([self.Y, [[float(y)]]])[-MAX_ECH:],
                                  np.vstack([self.W, [[float(poids)]]])[-MAX_ECH:])
        self.n += 1
        self.n_fort += int(fort)
        if y >= 0.5:
            self.n_pos += 1
        else:
            self.n_neg += 1

    def affiner(self) -> float | None:
        """Quelques pas d'Adam sur la mémoire de rejeu (l'état de l'optimiseur est conservé : fine-tuning continu)."""
        if len(self.X) < 4:
            return None
        for _ in range(PAS):
            self.perte = self.reseau.pas(self.X, self.Y, self.W, LR)
        return self.perte

    def raisons(self, valeurs: dict, k: int = 3) -> list[dict]:
        """Les mesures les plus éloignées de leur base : de quoi expliquer une alerte (jamais de texte ni de contenu)."""
        e = self.ecart(valeurs)
        ordre = np.argsort(-np.abs(e))[:k]
        return [{"mesure": self.noms[i], "valeur": round(float(valeurs.get(self.noms[i], 0.0)), 3), "ecart": round(float(e[i]), 2)}
                for i in ordre if abs(e[i]) >= 0.2]


class Cerveau:
    """Les trois têtes + des curseurs (jusqu'où les avis, messages et alertes ont déjà été appris) + le dernier état montré à la page SIEM."""
    def __init__(self):
        self.tetes = {nom: Tete(noms, h, graine=7 + i, nb_gram=GRAMMES_TETES.get(nom, 0)) for i, (nom, (noms, h)) in enumerate(TETES.items())}
        self.meta: dict = {"curseurs": {}, "derniers": {"compte": {}, "message": {}, "systeme": None}}


# ── Rejeu : apprendre d'exemples déjà vus (historique, préchauffage) ────────────────────────────────────────────────────────
def rejouer(tete: Tete, exemples, scores: list | None = None, affiner_tous: int = 0) -> list:
    """Fait apprendre à `tete` une suite d'exemples {mesures, texte (facultatif), y, poids, fort (facultatif)}, dans l'ordre.
    Rend les couples (score donné AVANT d'apprendre l'exemple, y) des exemples forts : une mesure honnête de ce que le modèle valait, pour calibrer
    les seuils. `scores` (liste) reçoit le score de chaque exemple (de quoi alimenter le quantile). `affiner_tous` : un affinage tous les N exemples
    (la mémoire de rejeu ne garde que MAX_ECH exemples : sans cela, un long historique n'enseignerait que ses derniers instants).
    Même chemin que l'apprentissage en direct (base alimentée par ce qui est calme, jamais par ce qui est jugé suspect)."""
    mesures_fortes = []
    for i, e in enumerate(exemples, 1):
        x = tete.vecteur(e["mesures"], e.get("texte", ""))
        score = tete.predire(x)
        tete.observer(e["mesures"], score)
        tete.apprendre(x, e["y"], e["poids"], fort=bool(e.get("fort")))
        if scores is not None:
            scores.append(score)
        if e.get("fort"):
            mesures_fortes.append((score, e["y"]))
        if affiner_tous and i % affiner_tous == 0:
            tete.affiner()
    return mesures_fortes


# ── Fichier signé ───────────────────────────────────────────────────────────────────────────────────────────────────────
class FichierAltere(ValueError):
    pass


def _octets_tete(t: Tete) -> bytes:
    return rn.vers_octets({**t.reseau.tableaux(), "moy": t.moy, "var": t.var, "X": t.X, "Y": t.Y, "W": t.W,
                           "colonnes": np.array(t.colonnes(), dtype="U40"), "stats": np.array([t.n_base, t.n, t.n_fort, -1 if t.perte is None else t.perte, t.n_pos, t.n_neg])})


def serialiser(c: Cerveau, cle: bytes) -> bytes:
    corps = rn.vers_octets({**{nom: np.frombuffer(_octets_tete(t), dtype=np.uint8) for nom, t in c.tetes.items()},
                            "meta": np.frombuffer(json.dumps(c.meta, separators=(",", ":")).encode(), dtype=np.uint8)})
    return MAGIE + bytes([VERSION]) + hmac.new(cle, corps, hashlib.sha256).digest() + corps


def deserialiser(octets: bytes, cles: list[bytes]) -> Cerveau:
    """FichierAltere si la signature ne correspond à aucune clé du SIEM (anciennes clés comprises) ou si le contenu est illisible."""
    entete = len(MAGIE) + 1 + 32
    if len(octets) <= entete or not octets.startswith(MAGIE) or octets[len(MAGIE)] != VERSION:
        raise FichierAltere(_siem_tf("rg4.siem.msg.168"))
    signature, corps = octets[len(MAGIE) + 1:entete], octets[entete:]
    if not any(hmac.compare_digest(signature, hmac.new(k, corps, hashlib.sha256).digest()) for k in cles):
        raise FichierAltere(_siem_tf("rg4.siem.msg.246"))
    try:
        z = rn.depuis_octets(corps)
        c = Cerveau()
        c.meta = json.loads(bytes(z["meta"]).decode())
        for nom, t in c.tetes.items():
            if nom not in z.files:
                continue   # tête ajoutée par une version plus récente du logiciel : elle part à neuf
            zt = rn.depuis_octets(bytes(z[nom]))
            anciennes = [str(s) for s in zt["colonnes"]]
            reseau = rn.Reseau.depuis_tableaux(zt)
            if reseau.d_in != len(anciennes):
                raise ValueError(_siem_tf("rg4.siem.msg.169"))
            nouvelles = t.colonnes()
            reseau.remapper_entrees(anciennes, nouvelles)   # chaque poids rejoint sa colonne par son NOM
            t.reseau = reseau
            noms_anciens = [a[2:] for a in anciennes if a.startswith("v:")]   # les bases suivent aussi leur mesure par son nom
            for attr in ("moy", "var"):
                ancien = np.array(zt[attr], dtype=float)
                valeur = getattr(t, attr).copy()
                for j, n in enumerate(t.noms):
                    if n in noms_anciens:
                        valeur[j] = ancien[noms_anciens.index(n)]
                setattr(t, attr, valeur)
            for attr in ("X", "Y", "W"):
                a = np.array(zt[attr], dtype=float)
                setattr(t, attr, rn.reordonner_colonnes(a, anciennes, nouvelles) if attr == "X" else a)
            stats = zt["stats"]
            t.n_base, t.n, t.n_fort = int(stats[0]), int(stats[1]), int(stats[2])
            t.perte = None if stats[3] < 0 else float(stats[3])
            if len(stats) >= 6:
                t.n_pos, t.n_neg = int(stats[4]), int(stats[5])
            else:   # fichier d'une version précédente : on estime les classes d'après la mémoire de rejeu
                t.n_pos = int((t.Y >= 0.5).sum())
                t.n_neg = int((t.Y < 0.5).sum())
        return c
    except FichierAltere:
        raise
    except Exception as exc:
        raise FichierAltere(_siem_tf("rg4.siem.msg.247", v1=exc)) from exc


def ecrire(chemin: str, c: Cerveau, cle: bytes) -> None:
    os.makedirs(os.path.dirname(chemin) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(chemin) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(serialiser(c, cle))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, chemin)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lire(chemin: str, cles: list[bytes]) -> Cerveau:
    with open(chemin, "rb") as f:
        return deserialiser(f.read(), cles)
