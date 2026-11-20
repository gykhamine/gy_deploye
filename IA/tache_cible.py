"""IA/tache_cible.py -- tache (classification ou regression) d'un MLP selon sa cible.

Module pur (numpy seulement, aucun import jax) : partage entre l'entrainement
(IA/train/mlp.py) et le controle des donnees (engine/contraintes_ia.py), pour
que la meme regle decide partout.

Regle (cible y d'UNE colonne) :
  - cible categorielle deja encodee en classes (`forcer_classes`) -> classification ;
  - valeurs non entieres -> regression ;
  - valeurs entieres : <= 2 valeurs distinctes -> classification ;
    3 a MAX_CLASSES_AUTO valeurs distinctes ET au plus n/2 -> classification ;
    sinon (age, quantite, montant entier...) -> regression ;
  - plusieurs colonnes de sortie -> regression multi-sorties.
"""
import numpy as np

MAX_CLASSES_AUTO = 10
TACHES = ("classification", "regression")


def detecter_tache(y, forcer_classes=False):
    """'classification' ou 'regression' pour la cible `y` (n,) ou (n, k)."""
    y = np.asarray(y, dtype=np.float64)
    if y.ndim > 1 and y.shape[1] != 1:
        return "regression"
    if forcer_classes:
        return "classification"
    v = y.reshape(-1)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return "classification"
    if not np.allclose(v, np.round(v)):
        return "regression"
    k = np.unique(v).size
    if k <= 2:
        return "classification"
    return "classification" if (k <= MAX_CLASSES_AUTO and k <= v.size / 2) else "regression"
