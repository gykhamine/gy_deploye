"""RÉSEAU NEURONAL du modèle fantôme : perceptron profond à deux couches cachées (tanh), entraîné par rétropropagation
et optimiseur Adam, en numpy pur (numpy est déjà une dépendance du projet : aucune nouvelle dépendance).

Ce que ce module apporte au modèle fantôme (engine/modele_fantome.py) :
  * un vrai apprentissage profond, à sorties multiples (plusieurs têtes) et perte masquée : une observation n'entraîne
    que les têtes dont elle porte l'étiquette ;
  * un FINE-TUNING incrémental : l'état de l'optimiseur (moments d'Adam) est conservé d'un passage à l'autre ;
  * une croissance SANS PERTE : `elargir()` ajoute des neurones dont les poids de sortie sont nuls, la fonction apprise
    est donc exactement conservée au moment de l'élargissement (principe Net2Net) ;
  * un garde-fou de taille : `largeurs_permises()` ne propose que des largeurs dont le nombre de paramètres reste sous
    le plafond (10 000 par défaut) ; au-delà, c'est le modèle fantôme qui ouvre un autre réseau complémentaire.
Module PUR (aucun import Django).
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from engine.i18n import t as _i18n_t

import io
import math

import numpy as np

PARAMETRES_MAX = 10_000
_NOMS = ("W1", "b1", "W2", "b2", "W3", "b3")


def nb_parametres(d_in: int, largeur: int, d_out: int, couches: int = 0) -> int:
    """`couches` = couches cachées résiduelles supplémentaires (Reseau.ajouter_couche), chacune largeur² + largeur paramètres."""
    return d_in * largeur + largeur + largeur * largeur + largeur + largeur * d_out + d_out + couches * (largeur * largeur + largeur)


def largeurs_permises(d_in: int, d_out: int, plafond: int = PARAMETRES_MAX, depart: int = 8, couches: int = 0) -> list[int]:
    """Paliers de largeur (croissance ×1,5) dont le nombre de paramètres reste ≤ plafond ; le dernier palier est la
    plus grande largeur qui tient exactement sous le plafond."""
    maximum = max((h for h in range(depart, 512) if nb_parametres(d_in, h, d_out, couches) <= plafond), default=depart)
    paliers, h = [], float(depart)
    while round(h) < maximum:
        paliers.append(round(h))
        h *= 1.5
    return sorted(set(paliers + [maximum]))


def _etendre(ancien: np.ndarray, forme: tuple) -> np.ndarray:
    neuf = np.zeros(forme)
    neuf[tuple(slice(0, s) for s in ancien.shape)] = ancien
    return neuf


def correspondance(anciens, nouveaux) -> list[int]:
    """Pour chaque nom de `nouveaux`, sa position dans `anciens` (-1 s'il est nouveau). ValueError si un nom est en double :
    une disposition sans noms uniques ne peut pas être rapprochée sans risque d'une autre."""
    anciens, nouveaux = list(anciens), list(nouveaux)
    if len(set(anciens)) != len(anciens) or len(set(nouveaux)) != len(nouveaux):
        raise ValueError(_i18n_t("rg2.reseau_neuronal.1"))
    position = {nom: i for i, nom in enumerate(anciens)}
    return [position.get(nom, -1) for nom in nouveaux]


def reordonner_colonnes(a: np.ndarray, anciens, nouveaux) -> np.ndarray:
    """Tableau (n, len(anciens)) -> (n, len(nouveaux)) : chaque colonne rejoint son nom ; un nom nouveau vaut 0, un nom disparu est retiré."""
    a = np.asarray(a, dtype=float)
    source = correspondance(anciens, nouveaux)
    neuf = np.zeros((a.shape[0], len(source)))
    for j, s in enumerate(source):
        if s >= 0:
            neuf[:, j] = a[:, s]
    return neuf


class Reseau:
    """Poids `p`, moments d'Adam `m`/`v` et compteur `t` : tout ce qu'il faut pour reprendre l'entraînement plus tard."""

    def __init__(self, p: dict, m: dict | None = None, v: dict | None = None, t: int = 0):
        self.p = p
        self.m = m or {k: np.zeros_like(a) for k, a in p.items()}
        self.v = v or {k: np.zeros_like(a) for k, a in p.items()}
        self.t = t

    @classmethod
    def nouveau(cls, d_in: int, largeur: int, d_out: int, graine: int, sortie_nulle: bool = False) -> "Reseau":
        """Initialisation de Xavier. `sortie_nulle` : couche de sortie à zéro, le réseau répond 0 partout (un réseau
        complémentaire ne perturbe alors en rien la somme des réseaux précédents)."""
        rng = np.random.default_rng(graine)
        return cls({
            "W1": rng.normal(0, math.sqrt(1 / d_in), (d_in, largeur)), "b1": np.zeros(largeur),
            "W2": rng.normal(0, math.sqrt(1 / largeur), (largeur, largeur)), "b2": np.zeros(largeur),
            "W3": np.zeros((largeur, d_out)) if sortie_nulle else rng.normal(0, 0.5 * math.sqrt(1 / largeur), (largeur, d_out)),
            "b3": np.zeros(d_out)})

    @property
    def largeur(self) -> int:
        return self.p["W1"].shape[1]

    @property
    def d_in(self) -> int:
        return self.p["W1"].shape[0]

    @property
    def d_out(self) -> int:
        return self.p["W3"].shape[1]

    @property
    def n_couches(self) -> int:
        """Couches cachées résiduelles supplémentaires (0 pour tout réseau d'avant leur existence)."""
        return sum(1 for k in self.p if k.startswith("R"))

    def noms(self) -> tuple:
        return _NOMS + tuple(n for k in range(1, self.n_couches + 1) for n in (f"R{k}", f"rb{k}"))

    def ajouter_couche(self) -> None:
        """Ajoute une couche cachée SANS changer la fonction apprise, dans le MÊME réseau (même fichier) : h <- h + tanh(h·R + b), R et b
        nuls au départ, donc h <- h exactement ; la couche apprend ensuite à s'en servir. Un réseau écrit avant l'ajout se relit tel quel."""
        k, h = self.n_couches + 1, self.largeur
        for table in (self.p, self.m, self.v):
            table[f"R{k}"], table[f"rb{k}"] = np.zeros((h, h)), np.zeros(h)

    def nb_parametres(self) -> int:
        return int(sum(a.size for a in self.p.values()))

    def _avant(self, x: np.ndarray):
        p = self.p
        a1 = np.tanh(x @ p["W1"] + p["b1"])
        a2 = np.tanh(a1 @ p["W2"] + p["b2"])
        sommets, tanhs = [a2], []
        for k in range(1, self.n_couches + 1):   # couches résiduelles supplémentaires
            t = np.tanh(sommets[-1] @ p[f"R{k}"] + p[f"rb{k}"])
            tanhs.append(t)
            sommets.append(sommets[-1] + t)
        return a1, a2, sommets[-1] @ p["W3"] + p["b3"], sommets, tanhs

    def predire(self, x) -> np.ndarray:
        return self._avant(np.atleast_2d(np.asarray(x, dtype=float)))[2]

    def gradients(self, x: np.ndarray, y: np.ndarray, w: np.ndarray, decroissance: float = 0.0):
        """(perte, gradients). Perte = somme, sur les sorties, de la moyenne pondérée des carrés des écarts de leurs seuls
        échantillons étiquetés (poids `w` ; 0 = étiquette absente)."""
        p = self.p
        a1, a2, sortie, sommets, tanhs = self._avant(x)
        ecart = sortie - y
        perte = float(np.sum(w * ecart ** 2) / max(np.sum(w), 1e-9))
        d3 = 2.0 * w * ecart / np.maximum(w.sum(axis=0, keepdims=True), 1.0)
        dh, gr = d3 @ p["W3"].T, {}
        for k in range(self.n_couches, 0, -1):   # rétropropagation à travers les couches résiduelles (identité + branche tanh)
            dz = dh * (1 - tanhs[k - 1] ** 2)
            gr[f"R{k}"], gr[f"rb{k}"] = sommets[k - 1].T @ dz + decroissance * p[f"R{k}"], dz.sum(0)
            dh = dh + dz @ p[f"R{k}"].T
        dz2 = dh * (1 - a2 ** 2)
        dz1 = (dz2 @ p["W2"].T) * (1 - a1 ** 2)
        g = {"W3": sommets[-1].T @ d3, "b3": d3.sum(0), "W2": a1.T @ dz2, "b2": dz2.sum(0), "W1": x.T @ dz1, "b1": dz1.sum(0), **gr}
        for nom in ("W1", "W2", "W3"):
            g[nom] = g[nom] + decroissance * p[nom]
        return perte, g

    def pas(self, x: np.ndarray, y: np.ndarray, w: np.ndarray, lr: float, decroissance: float = 1e-4) -> float:
        """UN pas d'Adam sur le lot (x, y) avec poids/masque `w`. Rend la perte moyenne pondérée avant le pas."""
        perte, g = self.gradients(x, y, w, decroissance)
        norme = math.sqrt(sum(float(np.sum(a * a)) for a in g.values()))
        if norme > 1.0:  # écrêtage : un échantillon aberrant ne casse jamais ce qui a été appris
            g = {k: a / norme for k, a in g.items()}
        self.t += 1
        for k in self.noms():
            self.m[k] = 0.9 * self.m[k] + 0.1 * g[k]
            self.v[k] = 0.999 * self.v[k] + 0.001 * g[k] ** 2
            corrige_m = self.m[k] / (1 - 0.9 ** self.t)
            corrige_v = self.v[k] / (1 - 0.999 ** self.t)
            self.p[k] -= lr * corrige_m / (np.sqrt(corrige_v) + 1e-8)
        return perte

    def elargir(self, largeur: int) -> None:
        """Passe à `largeur` neurones par couche cachée SANS changer la fonction apprise : les nouveaux neurones de la
        seconde couche ont des poids de sortie nuls, les nouveaux neurones de la première n'alimentent que ceux-là."""
        h = self.largeur
        if largeur <= h:
            return
        n, d, o = largeur - h, self.d_in, self.d_out
        rng = np.random.default_rng(h * 7919 + largeur)
        p = self.p
        w2 = np.zeros((largeur, largeur))
        w2[:h, :h] = p["W2"]
        w2[:, h:] = rng.normal(0, math.sqrt(1 / largeur), (largeur, n))
        neuf = {"W1": np.hstack([p["W1"], rng.normal(0, math.sqrt(1 / d), (d, n))]), "b1": np.concatenate([p["b1"], np.zeros(n)]),
                "W2": w2, "b2": np.concatenate([p["b2"], np.zeros(n)]),
                "W3": np.vstack([p["W3"], np.zeros((n, o))]), "b3": p["b3"]}
        for k in range(1, self.n_couches + 1):   # nouveaux neurones : poids nuls dans les couches résiduelles (fonction inchangée)
            neuf[f"R{k}"], neuf[f"rb{k}"] = _etendre(p[f"R{k}"], (largeur, largeur)), np.concatenate([p[f"rb{k}"], np.zeros(n)])
        self.m = {k: _etendre(self.m[k], a.shape) for k, a in neuf.items()}
        self.v = {k: _etendre(self.v[k], a.shape) for k, a in neuf.items()}
        self.p = neuf

    def completer_entrees(self, d_in: int) -> None:
        """Ajoute des entrées SANS changer la fonction apprise : les nouvelles lignes de W1 (et de ses moments Adam) sont nulles,
        le réseau répond donc exactement comme avant tant que les nouvelles entrées valent zéro, puis apprend à s'en servir."""
        if d_in <= self.d_in:
            return
        self.p["W1"] = _etendre(self.p["W1"], (d_in, self.largeur))
        self.m["W1"] = _etendre(self.m["W1"], (d_in, self.largeur))
        self.v["W1"] = _etendre(self.v["W1"], (d_in, self.largeur))

    def remapper_entrees(self, anciens, nouveaux) -> bool:
        """Réaligne les entrées SUR LEURS NOMS (et non sur leur position) : une entrée connue garde ses poids où qu'elle soit
        désormais, une entrée nouvelle démarre à poids nuls (le réseau répond comme avant tant qu'elle vaut zéro, puis apprend à
        s'en servir), une entrée disparue est retirée. Les moments d'Adam suivent. Rend True si quelque chose a changé."""
        if list(anciens) == list(nouveaux):
            return False
        if len(anciens) != self.d_in:
            raise ValueError(_i18n_t("rg2.reseau_neuronal.2"))
        source = correspondance(anciens, nouveaux)
        for table in (self.p, self.m, self.v):
            neuf = np.zeros((len(source), self.largeur))
            for j, s in enumerate(source):
                if s >= 0:
                    neuf[j] = table["W1"][s]
            table["W1"] = neuf
        return True

    def remapper_sorties(self, anciennes, nouvelles) -> bool:
        """Même principe pour les têtes de sortie : une tête connue garde ses poids et son biais, une tête nouvelle démarre à zéro
        (elle répond 0 : la somme des réseaux et l'apport a priori restent inchangés), une tête disparue est retirée."""
        if list(anciennes) == list(nouvelles):
            return False
        if len(anciennes) != self.d_out:
            raise ValueError(_i18n_t("rg2.reseau_neuronal.3"))
        source = correspondance(anciennes, nouvelles)
        for table in (self.p, self.m, self.v):
            w3, b3 = np.zeros((self.largeur, len(source))), np.zeros(len(source))
            for j, s in enumerate(source):
                if s >= 0:
                    w3[:, j], b3[j] = table["W3"][:, s], table["b3"][s]
            table["W3"], table["b3"] = w3, b3
        return True

    # ── Sérialisation (npz sans pickle : rien d'exécutable dans un fichier de modèle) ──
    def tableaux(self) -> dict:
        return {**{k: a.astype(np.float32) for k, a in self.p.items()},
                **{"m_" + k: a.astype(np.float32) for k, a in self.m.items()},
                **{"v_" + k: a.astype(np.float32) for k, a in self.v.items()}, "t": np.array([self.t], dtype=np.int64)}

    @classmethod
    def depuis_tableaux(cls, z) -> "Reseau":
        extra, k = (), 1
        while f"R{k}" in z.files:   # couches résiduelles présentes dans le fichier (absentes d'un fichier plus ancien)
            extra, k = extra + (f"R{k}", f"rb{k}"), k + 1
        lire = lambda pre: {k: np.array(z[pre + k], dtype=float) for k in _NOMS + extra}
        reseau = cls(lire(""), lire("m_"), lire("v_"), int(z["t"][0]))
        h = reseau.largeur
        if reseau.p["W1"].shape[1] != reseau.p["W2"].shape[0] or reseau.p["W2"].shape[1] != reseau.p["W3"].shape[0] or \
                any(reseau.p[f"R{k}"].shape != (h, h) or reseau.p[f"rb{k}"].shape != (h,) for k in range(1, reseau.n_couches + 1)):
            raise ValueError(_i18n_t("engine/reseau_neuronal.py:g:159:30"))
        return reseau


def vers_octets(tableaux: dict) -> bytes:
    tampon = io.BytesIO()
    np.savez_compressed(tampon, **tableaux)
    return tampon.getvalue()


def depuis_octets(octets: bytes):
    """npz chargé sans pickle (allow_pickle=False) ; ValueError si le contenu n'est pas un archive de tableaux."""
    try:
        return np.load(io.BytesIO(octets), allow_pickle=False)
    except Exception as exc:  # zip corrompu, pickle refusé, etc.
        raise ValueError(_i18n_t("engine/reseau_neuronal.py:g:174:26")) from exc
