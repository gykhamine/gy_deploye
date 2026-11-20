"""SÉPARATION DES DONNÉES — le SEUL endroit où un jeu de données est découpé pour l'entraînement.

Règle (toute taille de jeu, tout algorithme supervisé) :
  * 80 % des lignes servent à ENTRAÎNER le modèle, jamais plus ;
  * les 20 % restants ne sont JAMAIS vus à l'entraînement : ils servent aux tests automatiques qui suivent l'entraînement
    (`tester_modele`) pour confirmer que le modèle est bon. Sinon l'algorithme est signalé « pas approprié ».

Qui s'en sert : `engine/ia_integration` (avant `Trainer.train`, pour tous les types supervisés) et `IA/train/sklearn_models.py`
(`_split_train_test` délègue ici : plus aucun découpage ailleurs). Pour ne pas redécouper les 80 %, l'appelant déclare le jeu de test
déjà mis de côté avec `jeu_de_test_impose(...)` ; les entraîneurs sklearn le reprennent tel quel.

Module volontairement sans dépendance (numpy seul) et sans accès à la base : testable seul.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field

import numpy as np

FRACTION_ENTRAINEMENT = 0.8
FRACTION_TEST = 0.2
GRAINE = 42

# Seuils des tests automatiques (réglables ici, nulle part ailleurs).
TEST_MIN_LIGNES = 5          # en dessous, le score sur le jeu de test ne prouve rien : verdict « non vérifiable »
SEUIL_ACCURACY = 0.6         # classification : précision minimale sur les 20 % ...
SEUIL_GAIN = 0.2             # ... ET gain minimal sur la réponse « toujours la classe la plus fréquente » (0 = pas mieux, 1 = parfait)
SEUIL_R2 = 0.5               # régression : R² minimal sur les 20 %
SEUIL_HASARD = 0.05          # gain sur la réponse naïve (classification) ou R² (régression) en dessous duquel le modèle est « au niveau du hasard »
ECART_SURAPPRENTISSAGE = 0.25  # score entraînement − score test au-delà duquel on signale que le modèle a mémorisé
MAX_LIGNES_SCORE_ENTRAINEMENT = 5000   # le score « entraînement » (comparaison) est calculé sur au plus ce nombre de lignes

VERDICT_BON = "bon"
VERDICT_INAPPROPRIE = "inapproprie"
VERDICT_NON_VERIFIABLE = "non_verifiable"


@dataclass
class Separation:
    X_train: object
    X_test: object
    y_train: object
    y_test: object
    info: dict = field(default_factory=dict)


def _indices_test(n, y, classification, graine):
    """Indices (triés) du jeu de test : exactement round(20 % · n) lignes, au moins 1 dès que n >= 2.

    Classification : échantillonnage systématique sur les lignes groupées par classe → chaque classe est représentée dans le test
    en proportion, et une classe n'ayant qu'UNE ligne reste toujours dans l'entraînement (le modèle ne rencontre jamais une classe inconnue)."""
    rng = np.random.default_rng(graine)
    n_test = max(1, int(round(FRACTION_TEST * n)))
    if classification and y is not None and np.asarray(y).ndim == 1:
        y = np.asarray(y)
        valeurs, inverse, nb = np.unique(y, return_inverse=True, return_counts=True)
        candidats = np.flatnonzero(nb[inverse] >= 2)               # lignes dont la classe a au moins 2 exemples
        n_test = min(n_test, len(candidats) // 2)
        if n_test <= 0:
            return np.array([], dtype=int)
        melange = rng.permutation(candidats)
        ordre = melange[np.argsort(inverse[melange], kind="stable")]   # groupé par classe, mélangé à l'intérieur de chaque classe
        pas = len(ordre) / n_test
        positions = np.floor(pas * (np.arange(n_test) + 0.5)).astype(int)
        return np.sort(ordre[np.minimum(positions, len(ordre) - 1)])
    n_test = min(n_test, n // 2)
    return np.sort(rng.permutation(n)[:n_test])


def separer(X, y=None, *, classification=True, graine=GRAINE) -> Separation:
    """80 % entraînement / 20 % test, reproductible (même graine = même découpage). `y` peut être None (jeu sans cible)."""
    n = len(X)
    sel = _indices_test(n, y, classification, graine) if n >= 2 else np.array([], dtype=int)
    masque = np.zeros(n, dtype=bool)
    masque[sel] = True
    train, test = np.flatnonzero(~masque), np.flatnonzero(masque)
    pris = (lambda a, idx: a[idx]) if isinstance(X, np.ndarray) else (lambda a, idx: [a[i] for i in idx])
    X_train, X_test = pris(X, train), pris(X, test)
    if y is None:
        y_train = y_test = None
    else:
        y_arr = y if isinstance(y, np.ndarray) else np.asarray(y)
        y_train, y_test = y_arr[train], y_arr[test]
    return Separation(X_train, X_test, y_train, y_test, {
        "split_effectue": len(test) > 0, "n_train": int(len(train)), "n_test": int(len(test)), "n_total": int(n),
        "fraction_entrainement": FRACTION_ENTRAINEMENT, "fraction_test": FRACTION_TEST,
    })


# ── Jeu de test déjà mis de côté (pour que les entraîneurs ne redécoupent pas les 80 %) ─────────────────────────
_TEST_IMPOSE = contextvars.ContextVar("gy_jeu_de_test_impose", default=None)


@contextlib.contextmanager
def jeu_de_test_impose(X_test, y_test):
    """Pendant ce bloc, `separer_pour_entrainement` rend les données reçues comme ENTRAÎNEMENT et ce jeu comme TEST."""
    jeton = _TEST_IMPOSE.set(None if X_test is None else (X_test, y_test))
    try:
        yield
    finally:
        _TEST_IMPOSE.reset(jeton)


def separer_pour_entrainement(X, y, *, graine=GRAINE, classification=True):
    """Point d'entrée des entraîneurs (`_split_train_test`) : (X_train, X_test, y_train, y_test, info).
    Si l'appelant a déjà mis les 20 % de côté (`jeu_de_test_impose`), les données reçues SONT les 80 % : aucun second découpage."""
    impose = _TEST_IMPOSE.get()
    if impose is not None:
        X_test, y_test = impose
        return X, X_test, y, y_test, {"split_effectue": True, "n_train": int(len(X)), "n_test": int(len(X_test)),
                                      "fraction_entrainement": FRACTION_ENTRAINEMENT, "fraction_test": FRACTION_TEST, "impose": True}
    s = separer(X, y, classification=classification, graine=graine)
    return s.X_train, s.X_test, s.y_train, s.y_test, s.info


# ── Tests automatiques après l'entraînement ────────────────────────────────────────────────────────────────────
def _classes_predites(sortie, valeurs_classes=None, nb_classes=None):
    """Sortie brute d'un modèle (probabilités, indices ou valeurs) → valeur de classe prédite, ligne par ligne.
    Même lecture que l'inférence : softmax (k > 1 colonnes) → argmax, sigmoïde (1 colonne) → seuil 0,5, puis position → valeur de classe."""
    a = np.asarray(sortie, dtype=np.float64)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    a = a.reshape(a.shape[0], -1)
    if a.shape[1] > 1:
        pos = np.argmax(a, axis=1)
    else:
        p = a[:, 0]
        binaire = (nb_classes == 2) and bool(np.all((p >= 0) & (p <= 1))) and bool(np.any(p != np.round(p)))
        pos = (p > 0.5).astype(int) if binaire else np.round(p).astype(int)
    if valeurs_classes is not None and len(valeurs_classes):
        v = np.asarray(valeurs_classes, dtype=np.float64)
        return v[np.clip(pos, 0, len(v) - 1)]
    return pos.astype(np.float64)


def _score_classification(predire, X, y, valeurs_classes, nb_classes):
    pred = _classes_predites(predire(X), valeurs_classes, nb_classes)
    return float(np.mean(pred == np.asarray(y, dtype=np.float64).reshape(-1)))


def _score_regression(predire, X, y):
    pred = np.asarray(predire(X), dtype=np.float64).reshape(len(y), -1)[:, 0]
    y = np.asarray(y, dtype=np.float64).reshape(-1)
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    ss_res = float(np.sum((y - pred) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else (1.0 if ss_res == 0 else 0.0)


def tester_modele(predire, X_test, y_test, *, X_train=None, y_train=None, classification=True,
                  valeurs_classes=None, nb_classes=None, seed=GRAINE) -> dict:
    """Test automatique sur les 20 % jamais vus. Renvoie un dict PUREMENT NUMÉRIQUE / à codes (les textes sont affichés ensuite, traduits) :

        verdict      : "bon" | "inapproprie" | "non_verifiable"
        metrique     : "accuracy" | "r2"
        score        : score sur le jeu de test      reference : score de la réponse naïve (classe la plus fréquente / moyenne)
        gain         : progrès sur la réponse naïve (0 = aucun, 1 = parfait)
        score_entrainement, surapprentissage (bool), n_train, n_test, seuil
    `predire(X)` : fonction du modèle entraîné (ex. `modele.predict`). Une erreur de prédiction ne casse jamais l'entraînement : verdict « non vérifiable »."""
    n_test = 0 if X_test is None else len(X_test)
    metrique = "accuracy" if classification else "r2"
    res = {"verdict": VERDICT_NON_VERIFIABLE, "metrique": metrique, "score": None, "reference": None, "gain": None,
           "score_entrainement": None, "surapprentissage": False, "n_train": None if X_train is None else int(len(X_train)),
           "n_test": int(n_test), "seuil": SEUIL_ACCURACY if classification else SEUIL_R2, "raison": None}
    if n_test < TEST_MIN_LIGNES or y_test is None:
        res["raison"] = "peu_de_lignes"
        return res
    try:
        if classification:
            score = _score_classification(predire, X_test, y_test, valeurs_classes, nb_classes)
            ref_vals = np.asarray(y_train if y_train is not None else y_test).reshape(-1)
            val, nb = np.unique(ref_vals, return_counts=True)
            majoritaire = val[int(np.argmax(nb))]
            reference = float(np.mean(np.asarray(y_test, dtype=np.float64).reshape(-1) == float(majoritaire)))
            gain = (score - reference) / (1.0 - reference) if reference < 1.0 else (1.0 if score >= 1.0 else 0.0)
            bon = score >= SEUIL_ACCURACY and gain >= SEUIL_GAIN
        else:
            score, reference, gain = _score_regression(predire, X_test, y_test), 0.0, None
            bon = score >= SEUIL_R2
        res.update(score=round(score, 4), reference=round(reference, 4), gain=None if gain is None else round(gain, 4))
        res["verdict"] = VERDICT_BON if bon else VERDICT_INAPPROPRIE
        if X_train is not None and y_train is not None and len(X_train):
            n = min(len(X_train), MAX_LIGNES_SCORE_ENTRAINEMENT)
            idx = np.random.default_rng(seed).permutation(len(X_train))[:n]
            Xs = X_train[idx] if isinstance(X_train, np.ndarray) else [X_train[i] for i in idx]
            ys = np.asarray(y_train)[idx]
            s_train = (_score_classification(predire, Xs, ys, valeurs_classes, nb_classes) if classification
                       else _score_regression(predire, Xs, ys))
            res["score_entrainement"] = round(float(s_train), 4)
            res["surapprentissage"] = bool(s_train - score > ECART_SURAPPRENTISSAGE)
    except Exception as e:   # un test qui échoue ne doit jamais faire échouer l'entraînement
        res.update(verdict=VERDICT_NON_VERIFIABLE, raison="echec_test", detail=str(e)[:200])
    return res


def est_au_hasard(test: dict) -> bool:
    """Verdict « inapproprié » ET aucun progrès réel : classification = gain sur la réponse naïve < SEUIL_HASARD, régression = R² < SEUIL_HASARD.
    Dans ce cas ni plus d'époques ni plus de lignes n'y changeront rien : l'algorithme ne convient pas, ou les données n'expliquent pas la cible."""
    if not test or test.get("verdict") != VERDICT_INAPPROPRIE:
        return False
    valeur = test.get("gain") if test.get("metrique") == "accuracy" else test.get("score")
    return valeur is not None and valeur < SEUIL_HASARD
