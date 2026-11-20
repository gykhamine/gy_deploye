"""RÉSEAU NEURONAL EN JAX / FLAX / OPTAX : même réseau, même API, mais plus aucune rétropropagation ni aucun Adam écrits à la main.

Ce que le module `engine/reseau_neuronal.py` faisait à la main, on le délègue à des bibliothèques éprouvées :
  * Flax   — définition du perceptron (2 couches cachées tanh) : `_MLP` ; les poids restent des tableaux numpy nommés
             W1, b1, W2, b2, W3, b3, donc le format de fichier `.agentgy`, la croissance sans perte (`elargir`) et le
             garde-fou de taille sont inchangés (rien à migrer, les modèles déjà appris se rechargent tels quels) ;
  * JAX    — le gradient est obtenu par différentiation automatique (`jax.value_and_grad`), plus de dérivées codées à la main ;
  * Optax  — écrêtage global du gradient (`clip_by_global_norm`) puis Adam (`scale_by_adam`) ; les moments et le compteur
             de pas sont ceux d'Optax, recopiés dans `m`, `v`, `t` pour que le fine-tuning reprenne d'un passage à l'autre.
`ReseauJax` hérite de `reseau_neuronal.Reseau` (numpy) et ne remplace que `gradients` et `pas` : la version numpy sert de
référence dans les tests (mêmes gradients, mêmes pas) et de repli si JAX n'est pas installé (voir modele_fantome.py).
Précision 64 bits activée (jax_enable_x64) pour rester identique à la référence numpy.
"""
from __future__ import annotations

from functools import partial

import jax
import jax.numpy as jnp
import numpy as np
import optax
from flax import linen as nn

from . import reseau_neuronal as rn

jax.config.update("jax_enable_x64", True)

ECRETAGE = 1.0   # norme globale maximale du gradient : un échantillon aberrant ne casse jamais ce qui a été appris


class _MLP(nn.Module):
    largeur: int
    d_out: int
    couches: int = 0   # couches cachées résiduelles supplémentaires (reseau_neuronal.Reseau.ajouter_couche) : h <- h + tanh(h·R + b)

    @nn.compact
    def __call__(self, x):
        x = jnp.tanh(nn.Dense(self.largeur, name="c1")(x))
        x = jnp.tanh(nn.Dense(self.largeur, name="c2")(x))
        for k in range(1, self.couches + 1):
            x = x + jnp.tanh(nn.Dense(self.largeur, name=f"r{k}")(x))
        return nn.Dense(self.d_out, name="sortie")(x)


_BASE = (("W1", "c1", "kernel"), ("b1", "c1", "bias"), ("W2", "c2", "kernel"),
         ("b2", "c2", "bias"), ("W3", "sortie", "kernel"), ("b3", "sortie", "bias"))


def _correspondance(couches: int) -> tuple:
    return _BASE + tuple(t for k in range(1, couches + 1) for t in ((f"R{k}", f"r{k}", "kernel"), (f"rb{k}", f"r{k}", "bias")))


_CORRESPONDANCE = _correspondance(0)


def _vers_flax(p: dict, couches: int = 0) -> dict:
    sortie: dict = {}
    for nom, couche, champ in _correspondance(couches):
        sortie.setdefault(couche, {})[champ] = jnp.asarray(p[nom])
    return sortie


def _perte(params, largeur: int, d_out: int, couches: int, x, y, w, decroissance):
    """Perte minimisée = Σ_sorties (Σ_i w_ij·écart_ij² / max(Σ_i w_ij, 1)) + ½·décroissance·‖W‖² ;
    rapportée (auxiliaire) : moyenne pondérée des carrés des écarts, comme le module numpy."""
    ecart = _MLP(largeur, d_out, couches).apply({"params": params}, x) - y
    par_sortie = jnp.maximum(w.sum(axis=0, keepdims=True), 1.0)
    attache = jnp.sum(w * ecart ** 2 / par_sortie)
    l2 = 0.5 * decroissance * sum(jnp.sum(params[c]["kernel"] ** 2) for c in params)
    rapportee = jnp.sum(w * ecart ** 2) / jnp.maximum(jnp.sum(w), 1e-9)
    return attache + l2, rapportee


@partial(jax.jit, static_argnames=("largeur", "d_out", "couches"))
def _valeur_et_gradient(params, largeur, d_out, couches, x, y, w, decroissance):
    (_total, rapportee), g = jax.value_and_grad(_perte, has_aux=True)(params, largeur, d_out, couches, x, y, w, decroissance)
    return rapportee, g


class ReseauJax(rn.Reseau):
    def gradients(self, x, y, w, decroissance: float = 0.0):
        """(perte, gradients) : différentiation automatique ; mêmes conventions que la version numpy."""
        n = self.n_couches
        perte, g = _valeur_et_gradient(_vers_flax(self.p, n), self.largeur, self.d_out, n, jnp.asarray(x, dtype=float),
                                       jnp.asarray(y, dtype=float), jnp.asarray(w, dtype=float), decroissance)
        return float(perte), {nom: np.asarray(g[couche][champ]) for nom, couche, champ in _correspondance(n)}

    def pas(self, x, y, w, lr: float, decroissance: float = 1e-4) -> float:
        """UN pas d'Adam (Optax) sur le lot (x, y) avec poids/masque `w`. Rend la perte avant le pas."""
        perte, g = self.gradients(x, y, w, decroissance)
        tx = optax.chain(optax.clip_by_global_norm(ECRETAGE), optax.scale_by_adam(b1=0.9, b2=0.999, eps=1e-8))
        etat = tx.init({k: jnp.asarray(a) for k, a in self.p.items()})
        etat = (etat[0], etat[1]._replace(count=jnp.asarray(self.t, dtype=jnp.int32),
                                          mu={k: jnp.asarray(a) for k, a in self.m.items()},
                                          nu={k: jnp.asarray(a) for k, a in self.v.items()}))
        maj, etat = tx.update({k: jnp.asarray(a) for k, a in g.items()}, etat)
        adam = etat[1]
        self.t = int(adam.count)
        self.m = {k: np.asarray(a) for k, a in adam.mu.items()}
        self.v = {k: np.asarray(a) for k, a in adam.nu.items()}
        for k in self.noms():
            self.p[k] = self.p[k] - lr * np.asarray(maj[k])
        return perte


Reseau = ReseauJax
