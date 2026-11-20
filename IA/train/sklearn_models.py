"""
IA/train/sklearn_models.py — 9 modèles scikit-learn sauvegardés en format .gy natif.

Chaque modèle suit exactement le même contrat que les modèles deep-learning :
  - fonction train_sklearn_xxx(X, y, ..., save_path, on_epoch, ...) -> dict
  - sauvegarde via ia_format.save_model() → fichier .gy
  - compatible avec Trainer._REGISTRY / Model.predict()

Modèles disponibles :
  1. sklearn_rf       — Random Forest (classification)
  2. sklearn_gbm      — Gradient Boosting (classification)
  3. sklearn_svm      — SVM (classification)
  4. sklearn_knn      — k-Nearest Neighbors (classification)
  5. sklearn_lr       — Régression logistique (classification)
  6. sklearn_nb       — Naive Bayes gaussien (classification)
  7. sklearn_dt       — Arbre de décision (classification)
  8. sklearn_ridge    — Régression Ridge (régression)
  9. sklearn_lasso    — Régression Lasso (régression)

Sérialisation en .gy :
  Les modèles sklearn ne sont pas des réseaux de neurones et n'ont pas de
  tenseurs numpy natifs. On sérialise leurs paramètres appris dans le header
  JSON (scalaires, listes) et dans des tenseurs numpy quand c'est possible
  (poids des arbres, coefficients, etc.).
  Pour les structures complexes (arbres de décision, forêts), on sérialise
  via pickle encodé en base64 dans le header JSON.
"""

import base64
import io
import logging
import os
import pickle

import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.svm import SVC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.linear_model import LogisticRegression, Ridge, Lasso
from sklearn.naive_bayes import GaussianNB
from sklearn.tree import DecisionTreeClassifier
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

from ..config import MODELS_DIR, MODEL_EXTENSION, ensure_directories
from ..ia_format import save_model, load_model
from .forme_courbe import choisir_forme, replier_sur_colonnes, BUDGET_S_ARBRES, BUDGET_S_DEFAUT
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)

# Taille maximale (octets) d'un pickle sklearn dans un .gy : appliquée à la LECTURE et, pour les modèles non supervisés,
# aussi à l'ÉCRITURE (sinon un modèle s'enregistrerait puis ne pourrait plus servir).
TAILLE_MAX_PICKLE = 50 * 1024 * 1024

# ── Lecture sûre du pickle ──────────────────────────────────────────────────────────────────────────
# pickle.loads exécute du code arbitraire si le fichier est malveillant. Un .gy peut revenir par une archive de cycle
# restaurée : on n'autorise donc à la désérialisation que les classes attendues (scikit-learn, numpy, scipy et les
# enveloppeurs de IA.train.sklearn_nonsup). os.system, subprocess, builtins.eval/exec... sont refusés. Cela réduit
# fortement la surface d'attaque sans la supprimer : un .gy n'est fiable que s'il vient de GSCode.
_BUILTINS_SURS = frozenset({"set", "frozenset", "slice", "range", "complex", "bytearray", "bytes", "str", "int",
                            "float", "bool", "list", "dict", "tuple", "object"})
_COLLECTIONS_SURS = frozenset({"OrderedDict", "defaultdict", "deque", "Counter"})
_PREFIXES_SURS = ("sklearn.", "scipy.", "numpy.core.", "numpy._core.", "numpy.random", "numpy.dtypes")
# Modules maison dont les classes peuvent figurer dans un pickle : enveloppeurs non supervisés et transformateur de forme de courbe.
_MODULES_MAISON_SURS = (["IA", "train", "sklearn_nonsup"], ["IA", "train", "forme_courbe"])


class _DepicklerSur(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "builtins":
            autorise = name in _BUILTINS_SURS
        elif module == "collections":
            autorise = name in _COLLECTIONS_SURS
        elif module == "copyreg":
            autorise = name == "_reconstructor"
        elif module == "numpy":
            autorise = name in ("ndarray", "dtype")
        elif module.startswith(_PREFIXES_SURS):
            autorise = True
        else:   # enveloppeurs maison, quel que soit le préfixe du paquet qui contient IA (IA.train..., projet.IA.train...)
            autorise = module.split(".")[-3:] in _MODULES_MAISON_SURS
        if not autorise:
            raise pickle.UnpicklingError(_gtf("rg7.msg.027", v1=module, v2=name))
        return super().find_class(module, name)


def _charger_pickle(raw):
    """Désérialise les octets d'un modèle sklearn avec la liste blanche ci-dessus."""
    return _DepicklerSur(io.BytesIO(raw)).load()


def _versions_courantes():
    import sklearn
    return {"sklearn": sklearn.__version__, "numpy": np.__version__}


def _avertir_si_versions_differentes(versions):
    """Journalise un avertissement si le modèle a été produit avec une autre version majeure/mineure de sklearn ou numpy."""
    if not versions:
        return   # modèle enregistré avant l'ajout de ce suivi
    courantes = _versions_courantes()
    for nom, actuelle in courantes.items():
        ancienne = versions.get(nom)
        if ancienne and ancienne.split(".")[:2] != actuelle.split(".")[:2]:
            logger.warning(_gt("rg7.msg.028"), nom, ancienne, nom, actuelle)

# ─────────────────────────────────────────────────────────────
# Helpers communs
# ─────────────────────────────────────────────────────────────

def _sklearn_to_gy(clf, header_extra: dict, save_path: str,
                   coef_tensors: dict = None) -> None:
    """Sérialise un estimateur sklearn dans un fichier .gy.

    Stratégie :
      - Paramètres simples (scalaires, listes courtes) → header JSON.
      - Coefficients numpy (modèles linéaires, NB) → tenseurs .gy natifs.
      - Structures complexes (arbres, forêts, SVM) → pickle base64 dans le header.

    Args:
        clf:          L'estimateur sklearn entraîné.
        header_extra: Infos complémentaires à mettre dans le header (accuracy, etc.).
        save_path:    Chemin de sortie .gy.
        coef_tensors: Dict {nom: ndarray} de coefficients à stocker comme tenseurs.
    """
    pkl_b64 = base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii")
    if isinstance(header_extra.get("config"), dict):
        header_extra["config"].setdefault("versions", _versions_courantes())   # relu à l'inférence (avertissement)
    header = {
        **header_extra,
        "sklearn_pickle_b64": pkl_b64,
        "sklearn_class": type(clf).__name__,
    }
    tensors = {}
    if coef_tensors:
        tensors.update({k: np.asarray(v, dtype=np.float64)
                        for k, v in coef_tensors.items()})
    save_model(save_path, header, tensors)


def _gy_to_sklearn(path: str):
    """Charge un estimateur sklearn depuis un fichier .gy.

    Returns:
        (clf, header, tensors)
    """
    header, tensors = load_model(path)
    pkl_b64 = header.get("sklearn_pickle_b64")
    if not pkl_b64:
        raise ValueError(_gtf("rg7.msg.029", v1=path))
    # SÉCURITÉ : pickle peut exécuter du code arbitraire — ne charger que
    # des fichiers .gy émis par GSCode lui-même (jamais d'origine externe non vérifiée).
    raw = base64.b64decode(pkl_b64.encode("ascii"))
    if len(raw) > TAILLE_MAX_PICKLE:  # 50 Mo — limite raisonnable pour un modèle sklearn
        raise ValueError(_gt("rg7.msg.030"))
    _avertir_si_versions_differentes((header.get("config") or {}).get("versions"))
    clf = _charger_pickle(raw)
    return clf, header, tensors


def _demo_data(seed=42):
    """Données de démonstration : 4 features, classification binaire."""
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((200, 4))
    y = (X[:, 0] + X[:, 1] > 0).astype(int)
    return X, y


def _split_train_test(X, y, seed=42, test_size=0.2, classification=True):
    """Sépare X, y en entraînement (80 %) / test (20 %). Le découpage vit dans UN seul module commun (IA/separation.py) ;
    `test_size` est conservé pour la compatibilité des appels mais ignoré : la règle est toujours 80 / 20.

    Quand l'appelant a déjà mis les 20 % de côté (engine/ia_integration), les données reçues sont les 80 % et le jeu de test imposé
    est repris tel quel : aucun second découpage. Sous 2 lignes, aucun test n'est possible (train == test, signalé dans `info`).

    Returns:
        (X_train, X_test, y_train, y_test, info) — info contient split_effectue / n_train / n_test pour le header .gy."""
    from ..separation import separer_pour_entrainement
    X_train, X_test, y_train, y_test, info = separer_pour_entrainement(X, y, graine=seed, classification=classification)
    if not info.get("split_effectue"):
        logger.warning(_gt("rg7.msg.031"), len(X))
        return X, X, y, y, {"split_effectue": False, "n_train": len(X), "n_test": len(X)}
    return X_train, X_test, y_train, y_test, info


def _epoch_sim(on_epoch, epochs: int, loss_curve: list) -> bool:
    """Simule les callbacks epoch-by-epoch pour les modèles sklearn (fit unique).

    Les modèles sklearn n'ont pas d'époques réelles. On émet un seul
    event on_epoch(0, metrics) pour que la barre de progression s'affiche,
    puis on_epoch(epochs-1, metrics) pour signaler la fin.

    Returns:
        True si l'entraînement doit s'arrêter (stop demandé par callback).
    """
    if on_epoch is None:
        return False
    final_loss = loss_curve[-1] if loss_curve else 0.0
    # Époque initiale
    stop = on_epoch(0, {"loss": final_loss})
    if stop:
        return True
    # Époque finale
    stop = on_epoch(epochs - 1, {"loss": final_loss})
    return bool(stop)


# ─────────────────────────────────────────────────────────────
# Predict function générique pour tous les modèles sklearn
# ─────────────────────────────────────────────────────────────

def _predict_sklearn_generic(weights, config, X):
    """Inférence générique : recharge le pickle depuis config ou weights, et prédit.

    Le pickle_b64 peut se trouver :
      1. Dans `config['sklearn_pickle_b64']`  — cas normal après Trainer.train().
      2. Dans `config` à la racine (héritage du header .gy via Model.load()).
      Le Model.load() fusionne header['config'] + certaines clés racine selon
      l'implémentation ; on cherche dans les deux endroits.
    """
    pkl_b64 = (config.get("sklearn_pickle_b64")
               or config.get("config", {}).get("sklearn_pickle_b64"))
    if pkl_b64 is None:
        raise RuntimeError(
            _gt("rg7.msg.032")
        )
    # SÉCURITÉ : pickle peut exécuter du code arbitraire — ne charger que
    # des fichiers .gy émis par GSCode lui-même (jamais d'origine externe non vérifiée).
    raw = base64.b64decode(pkl_b64.encode("ascii"))
    if len(raw) > TAILLE_MAX_PICKLE:
        raise ValueError(_gt("rg7.msg.030"))
    _avertir_si_versions_differentes(config.get("versions") or (config.get("config") or {}).get("versions"))
    clf = _charger_pickle(raw)
    X = np.asarray(X, dtype=np.float64)
    if hasattr(clf, "predict_proba"):
        return clf.predict_proba(X)
    if getattr(clf, "sortie_matricielle", False):   # modeles sans cible (ACP, ICA, anomalies) : plusieurs colonnes par ligne
        return np.asarray(clf.predict(X))
    return clf.predict(X).reshape(-1, 1)


# ─────────────────────────────────────────────────────────────
# 1. Random Forest — classification
# ─────────────────────────────────────────────────────────────

def train_sklearn_rf(X=None, y=None, n_estimators=100, max_depth=None,
                     min_samples_split=2, min_samples_leaf=1,
                     max_features="sqrt", random_state=42,
                     epochs=1, lr=0.01, seed=42,
                     save_path=None, on_epoch=None, forme="auto"):
    """Entraîne un Random Forest sklearn et sauvegarde en .gy.

    Args:
        X, y:              Dataset (None = démo).
        n_estimators:      Nombre d'arbres.
        max_depth:         Profondeur max des arbres (None = illimitée).
        min_samples_split: Min échantillons pour splitter un nœud.
        min_samples_leaf:  Min échantillons dans une feuille.
        max_features:      Nb features par split ('sqrt', 'log2' ou float).
        random_state:      Graine.
        epochs:            Ignoré (sklearn fit unique) — conservé pour compatibilité.
        lr:                Ignoré — conservé pour compatibilité.
        seed:              Alias de random_state.
        save_path:         Chemin .gy (généré si None).
        on_epoch:          Callback Trainer.
    """
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_rf{MODEL_EXTENSION}")

    if X is None or y is None:
        X, y = _demo_data(seed)

    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=random_state or seed)

    algo = RandomForestClassifier(
        n_estimators=n_estimators,
        max_depth=max_depth if max_depth else None,
        min_samples_split=min_samples_split,
        min_samples_leaf=min_samples_leaf,
        max_features=max_features,
        random_state=random_state or seed,
        n_jobs=-1,
    )
    clf, rapport_forme = choisir_forme([("rf", algo)], X_train, y_train, True, forme=forme, seed=random_state or seed, budget_s=BUDGET_S_ARBRES)
    acc_train = accuracy_score(y_train, clf.predict(X_train)) * 100.0
    acc_test  = accuracy_score(y_test, clf.predict(X_test)) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.033"),
                acc_train, acc_test, split_info["n_train"], split_info["n_test"])

    # Importances comme tenseur natif (bonus)
    importances = replier_sur_colonnes(clf, clf[-1].feature_importances_)

    header_extra = {
        "type": "sklearn_rf",
        "accuracy": acc / 100.0,
        "accuracy_train": acc_train / 100.0,
        "accuracy_test": acc_test / 100.0,
        "split_info": split_info,
        "config": {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "min_samples_split": min_samples_split,
            "min_samples_leaf": min_samples_leaf,
            "max_features": max_features,
            "n_features": X.shape[1],
            "classes": clf.classes_.tolist(),
            "sklearn_pickle_b64": None,  # sera écrasé dans _sklearn_to_gy
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"feature_importances": importances})
    _epoch_sim(on_epoch, max(1, epochs), [1.0 - acc_test / 100.0])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "classes": clf.classes_.tolist(),
                  "n_features": X.shape[1]},
        "save_path": save_path,
        "accuracy": acc,
        "accuracy_train": acc_train,
        "accuracy_test": acc_test,
        "history": [1.0 - acc_test / 100.0],
    }


# ─────────────────────────────────────────────────────────────
# 2. Gradient Boosting — classification
# ─────────────────────────────────────────────────────────────

def train_sklearn_gbm(X=None, y=None, n_estimators=100, learning_rate=0.1,
                      max_depth=3, subsample=1.0, min_samples_split=2,
                      epochs=1, lr=0.01, seed=42,
                      save_path=None, on_epoch=None, forme="auto"):
    """Gradient Boosting Machines sklearn (classification)."""
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_gbm{MODEL_EXTENSION}")

    if X is None or y is None:
        X, y = _demo_data(seed)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed)

    algo = GradientBoostingClassifier(
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=max_depth,
        subsample=subsample,
        min_samples_split=min_samples_split,
        random_state=seed,
    )
    clf, rapport_forme = choisir_forme([("gbm", algo)], X_train, y_train, True, forme=forme, seed=seed, budget_s=BUDGET_S_ARBRES)
    acc_train = accuracy_score(y_train, clf.predict(X_train)) * 100.0
    acc_test  = accuracy_score(y_test, clf.predict(X_test)) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.034"),
                acc_train, acc_test, split_info["n_train"], split_info["n_test"])

    loss_curve = list(clf[-1].train_score_)  # score d'entraînement par estimateur
    # Convertir en loss décroissante (1 - score normalisé)
    if loss_curve:
        mx = max(abs(v) for v in loss_curve) or 1.0
        loss_curve = [abs(v) / mx for v in loss_curve]

    importances = replier_sur_colonnes(clf, clf[-1].feature_importances_)
    header_extra = {
        "type": "sklearn_gbm",
        "accuracy": acc / 100.0,
        "accuracy_train": acc_train / 100.0,
        "accuracy_test": acc_test / 100.0,
        "split_info": split_info,
        "config": {
            "n_estimators": n_estimators,
            "learning_rate": learning_rate,
            "max_depth": max_depth,
            "subsample": subsample,
            "n_features": X.shape[1],
            "classes": clf.classes_.tolist(),
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"feature_importances": importances})
    _epoch_sim(on_epoch, max(1, epochs), loss_curve or [1.0 - acc_test / 100.0])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "classes": clf.classes_.tolist(), "n_features": X.shape[1]},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": loss_curve or [1.0 - acc_test / 100.0],
    }


# ─────────────────────────────────────────────────────────────
# 3. SVM — classification
# ─────────────────────────────────────────────────────────────

def train_sklearn_svm(X=None, y=None, C=1.0, kernel="rbf", gamma="scale",
                      degree=3, probability=True,
                      epochs=1, lr=0.01, seed=42,
                      save_path=None, on_epoch=None, forme="auto"):
    """Support Vector Machine sklearn (classification, noyau configurable)."""
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_svm{MODEL_EXTENSION}")

    if X is None or y is None:
        X, y = _demo_data(seed)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed)

    # SVM est sensible à l'échelle des features — scaler intégré au pipeline
    # (fit sur X_train uniquement, pour ne pas laisser fuiter d'info du test).
    etapes = [
        ("scaler", StandardScaler()),
        ("svm", SVC(C=C, kernel=kernel, gamma=gamma, degree=degree,
                    probability=probability, random_state=seed)),
    ]
    clf, rapport_forme = choisir_forme(etapes, X_train, y_train, True, forme=forme, seed=seed, budget_s=BUDGET_S_DEFAUT)
    acc_train = accuracy_score(y_train, clf.predict(X_train)) * 100.0
    acc_test  = accuracy_score(y_test, clf.predict(X_test)) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.035"),
                acc_train, acc_test, split_info["n_train"], split_info["n_test"])

    # Pas de feature_importances pour SVM — on stocke n_support comme tenseur
    n_support = np.array(clf.named_steps["svm"].n_support_, dtype=np.float64)

    header_extra = {
        "type": "sklearn_svm",
        "accuracy": acc / 100.0,
        "accuracy_train": acc_train / 100.0,
        "accuracy_test": acc_test / 100.0,
        "split_info": split_info,
        "config": {
            "C": C, "kernel": kernel, "gamma": gamma, "degree": degree,
            "n_features": X.shape[1],
            "classes": clf.classes_.tolist(),
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"n_support": n_support})
    _epoch_sim(on_epoch, max(1, epochs), [1.0 - acc_test / 100.0])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "classes": clf.classes_.tolist(), "n_features": X.shape[1]},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": [1.0 - acc_test / 100.0],
    }


# ─────────────────────────────────────────────────────────────
# 4. k-Nearest Neighbors — classification
# ─────────────────────────────────────────────────────────────

def train_sklearn_knn(X=None, y=None, n_neighbors=5, weights="uniform",
                      metric="minkowski", p=2,
                      epochs=1, lr=0.01, seed=42,
                      save_path=None, on_epoch=None, forme="auto"):
    """k-Nearest Neighbors sklearn (classification)."""
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_knn{MODEL_EXTENSION}")

    if X is None or y is None:
        X, y = _demo_data(seed)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed)

    # KNN est sensible à l'échelle (distances) — scaler intégré au pipeline.
    etapes = [
        ("scaler", StandardScaler()),
        ("knn", KNeighborsClassifier(n_neighbors=n_neighbors, weights=weights, metric=metric, p=p)),
    ]
    clf, rapport_forme = choisir_forme(etapes, X_train, y_train, True, forme=forme, seed=seed, budget_s=BUDGET_S_DEFAUT)
    acc_train = accuracy_score(y_train, clf.predict(X_train)) * 100.0
    acc_test  = accuracy_score(y_test, clf.predict(X_test)) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.036"),
                acc_train, acc_test, split_info["n_train"], split_info["n_test"])

    # Stocker les vecteurs d'entraînement (mis à l'échelle) comme tenseurs natifs
    X_train_tensor = clf[:-1].transform(X_train).astype(np.float64)

    header_extra = {
        "type": "sklearn_knn",
        "accuracy": acc / 100.0,
        "accuracy_train": acc_train / 100.0,
        "accuracy_test": acc_test / 100.0,
        "split_info": split_info,
        "config": {
            "n_neighbors": n_neighbors, "weights": weights,
            "metric": metric, "p": p,
            "n_features": X.shape[1],
            "classes": clf.classes_.tolist(),
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"X_train": X_train_tensor})
    _epoch_sim(on_epoch, max(1, epochs), [1.0 - acc_test / 100.0])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "classes": clf.classes_.tolist(), "n_features": X.shape[1]},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": [1.0 - acc_test / 100.0],
    }


# ─────────────────────────────────────────────────────────────
# 5. Régression Logistique — classification
# ─────────────────────────────────────────────────────────────

def train_sklearn_lr(X=None, y=None, C=1.0, penalty="l2",
                     solver="lbfgs", max_iter=1000,
                     epochs=1, lr=0.01, seed=42,
                     save_path=None, on_epoch=None, forme="auto"):
    """Régression Logistique sklearn (classification linéaire).

    Supporte la classification binaire et multi-classes (OvR automatique).
    """
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_lr{MODEL_EXTENSION}")

    if X is None or y is None:
        X, y = _demo_data(seed)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed)

    # Les solveurs (lbfgs, etc.) convergent mieux avec des features à l'échelle.
    etapes = [
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(C=C, penalty=penalty, solver=solver,
                                   max_iter=max_iter, random_state=seed)),
    ]
    clf, rapport_forme = choisir_forme(etapes, X_train, y_train, True, forme=forme, seed=seed, budget_s=BUDGET_S_DEFAUT)
    acc_train = accuracy_score(y_train, clf.predict(X_train)) * 100.0
    acc_test  = accuracy_score(y_test, clf.predict(X_test)) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.037"),
                acc_train, acc_test, split_info["n_train"], split_info["n_test"])

    # Coefficients comme tenseurs natifs
    lr_step = clf.named_steps["lr"]
    coef = lr_step.coef_.astype(np.float64)        # (n_classes, n_features)
    intercept = lr_step.intercept_.astype(np.float64)  # (n_classes,)

    header_extra = {
        "type": "sklearn_lr",
        "accuracy": acc / 100.0,
        "accuracy_train": acc_train / 100.0,
        "accuracy_test": acc_test / 100.0,
        "split_info": split_info,
        "config": {
            "C": C, "penalty": penalty, "solver": solver,
            "max_iter": max_iter,
            "n_features": X.shape[1],
            "classes": clf.classes_.tolist(),
            "coef_shape": list(coef.shape),
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"coef": coef, "intercept": intercept})
    _epoch_sim(on_epoch, max(1, epochs), [1.0 - acc_test / 100.0])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "classes": clf.classes_.tolist(), "n_features": X.shape[1]},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": [1.0 - acc_test / 100.0],
    }


# ─────────────────────────────────────────────────────────────
# 6. Naive Bayes Gaussien — classification
# ─────────────────────────────────────────────────────────────

def train_sklearn_nb(X=None, y=None, var_smoothing=1e-9,
                     epochs=1, lr=0.01, seed=42,
                     save_path=None, on_epoch=None, forme="auto"):
    """Naive Bayes Gaussien sklearn (classification probabiliste)."""
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_nb{MODEL_EXTENSION}")

    if X is None or y is None:
        X, y = _demo_data(seed)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed)

    algo = GaussianNB(var_smoothing=var_smoothing)
    clf, rapport_forme = choisir_forme([("nb", algo)], X_train, y_train, True, forme=forme, seed=seed, budget_s=BUDGET_S_DEFAUT)
    acc_train = accuracy_score(y_train, clf.predict(X_train)) * 100.0
    acc_test  = accuracy_score(y_test, clf.predict(X_test)) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.038"),
                acc_train, acc_test, split_info["n_train"], split_info["n_test"])

    # Paramètres appris : moyennes et variances par classe
    theta = clf[-1].theta_.astype(np.float64)     # (n_classes, n_features) — moyennes
    var   = clf[-1].var_.astype(np.float64)       # (n_classes, n_features) — variances
    priors = np.array(clf[-1].class_prior_, dtype=np.float64)

    header_extra = {
        "type": "sklearn_nb",
        "accuracy": acc / 100.0,
        "accuracy_train": acc_train / 100.0,
        "accuracy_test": acc_test / 100.0,
        "split_info": split_info,
        "config": {
            "var_smoothing": var_smoothing,
            "n_features": X.shape[1],
            "classes": clf.classes_.tolist(),
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"theta": theta, "var": var, "class_prior": priors})
    _epoch_sim(on_epoch, max(1, epochs), [1.0 - acc_test / 100.0])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "classes": clf.classes_.tolist(), "n_features": X.shape[1]},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": [1.0 - acc_test / 100.0],
    }


# ─────────────────────────────────────────────────────────────
# 7. Arbre de Décision — classification
# ─────────────────────────────────────────────────────────────

def train_sklearn_dt(X=None, y=None, max_depth=None, criterion="gini",
                     min_samples_split=2, min_samples_leaf=1,
                     max_features=None,
                     epochs=1, lr=0.01, seed=42,
                     save_path=None, on_epoch=None, forme="auto"):
    """Arbre de Décision sklearn (classification)."""
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_dt{MODEL_EXTENSION}")

    if X is None or y is None:
        X, y = _demo_data(seed)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed)

    algo = DecisionTreeClassifier(
        max_depth=max_depth if max_depth else None,
        criterion=criterion,
        min_samples_split=min_samples_split,
        min_samples_leaf=min_samples_leaf,
        max_features=max_features if max_features else None,
        random_state=seed,
    )
    clf, rapport_forme = choisir_forme([("dt", algo)], X_train, y_train, True, forme=forme, seed=seed, budget_s=BUDGET_S_DEFAUT)
    acc_train = accuracy_score(y_train, clf.predict(X_train)) * 100.0
    acc_test  = accuracy_score(y_test, clf.predict(X_test)) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.039"),
                acc_train, acc_test, split_info["n_train"], split_info["n_test"])

    importances = replier_sur_colonnes(clf, clf[-1].feature_importances_)

    header_extra = {
        "type": "sklearn_dt",
        "accuracy": acc / 100.0,
        "accuracy_train": acc_train / 100.0,
        "accuracy_test": acc_test / 100.0,
        "split_info": split_info,
        "config": {
            "max_depth": max_depth, "criterion": criterion,
            "min_samples_split": min_samples_split,
            "min_samples_leaf": min_samples_leaf,
            "n_features": X.shape[1],
            "classes": clf.classes_.tolist(),
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"feature_importances": importances})
    _epoch_sim(on_epoch, max(1, epochs), [1.0 - acc_test / 100.0])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "classes": clf.classes_.tolist(), "n_features": X.shape[1]},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": [1.0 - acc_test / 100.0],
    }


# ─────────────────────────────────────────────────────────────
# 8. Ridge — régression
# ─────────────────────────────────────────────────────────────

def train_sklearn_ridge(X=None, y=None, alpha=1.0, fit_intercept=True,
                        epochs=1, lr=0.01, seed=42,
                        save_path=None, on_epoch=None, forme="auto"):
    """Régression Ridge sklearn (régression linéaire régularisée L2)."""
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_ridge{MODEL_EXTENSION}")

    if X is None or y is None:
        rng = np.random.default_rng(seed)
        X = rng.standard_normal((200, 4))
        y = (X[:, 0] * 2 + X[:, 1] * 0.5 + rng.standard_normal(200) * 0.1)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed, classification=False)

    algo = Ridge(alpha=alpha, fit_intercept=fit_intercept)
    clf, rapport_forme = choisir_forme([("ridge", algo)], X_train, y_train, False, forme=forme, seed=seed, budget_s=BUDGET_S_DEFAUT)

    def _r2_mse(Xs, ys):
        pred = clf.predict(Xs)
        mse_ = float(np.mean((ys - pred) ** 2))
        r2_ = float(1 - np.sum((ys - pred)**2) / (np.sum((ys - np.mean(ys))**2) + 1e-10))
        return r2_, mse_

    r2_train, mse_train = _r2_mse(X_train, y_train)
    r2_test, mse_test = _r2_mse(X_test, y_test)
    # "accuracy" = R² exprimé en % pour uniformité avec le reste du Trainer
    acc_train = max(0.0, r2_train) * 100.0
    acc_test = max(0.0, r2_test) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.040"),
                r2_train, r2_test, split_info["n_train"], split_info["n_test"])

    coef = clf[-1].coef_.astype(np.float64)
    intercept = np.array([clf[-1].intercept_], dtype=np.float64)

    header_extra = {
        "type": "sklearn_ridge",
        "accuracy": r2_test,
        "accuracy_train": r2_train,
        "accuracy_test": r2_test,
        "r2": r2_test,
        "r2_train": r2_train,
        "r2_test": r2_test,
        "mse": mse_test,
        "mse_train": mse_train,
        "mse_test": mse_test,
        "split_info": split_info,
        "config": {
            "alpha": alpha, "fit_intercept": fit_intercept,
            "n_features": X.shape[1],
            "task": "regression",
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"coef": coef, "intercept": intercept})
    _epoch_sim(on_epoch, max(1, epochs), [mse_test])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "n_features": X.shape[1], "task": "regression"},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": [mse_test],
    }


# ─────────────────────────────────────────────────────────────
# 9. Lasso — régression
# ─────────────────────────────────────────────────────────────

def train_sklearn_lasso(X=None, y=None, alpha=0.1, fit_intercept=True,
                        max_iter=1000,
                        epochs=1, lr=0.01, seed=42,
                        save_path=None, on_epoch=None, forme="auto"):
    """Régression Lasso sklearn (régression linéaire régularisée L1, sélection de features)."""
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_sklearn_lasso{MODEL_EXTENSION}")

    if X is None or y is None:
        rng = np.random.default_rng(seed)
        X = rng.standard_normal((200, 4))
        y = (X[:, 0] * 2 + X[:, 1] * 0.5 + rng.standard_normal(200) * 0.1)
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).ravel()
    X_train, X_test, y_train, y_test, split_info = _split_train_test(X, y, seed=seed, classification=False)

    algo = Lasso(alpha=alpha, fit_intercept=fit_intercept, max_iter=max_iter)
    clf, rapport_forme = choisir_forme([("lasso", algo)], X_train, y_train, False, forme=forme, seed=seed, budget_s=BUDGET_S_DEFAUT)

    def _r2_mse(Xs, ys):
        pred = clf.predict(Xs)
        mse_ = float(np.mean((ys - pred) ** 2))
        r2_ = float(1 - np.sum((ys - pred)**2) / (np.sum((ys - np.mean(ys))**2) + 1e-10))
        return r2_, mse_

    r2_train, mse_train = _r2_mse(X_train, y_train)
    r2_test, mse_test = _r2_mse(X_test, y_test)
    acc_train = max(0.0, r2_train) * 100.0
    acc_test = max(0.0, r2_test) * 100.0
    acc = acc_test
    logger.info(_gt("rg7.msg.041"),
                r2_train, r2_test, split_info["n_train"], split_info["n_test"])

    coef = clf[-1].coef_.astype(np.float64)
    intercept = np.array([clf[-1].intercept_], dtype=np.float64)

    header_extra = {
        "type": "sklearn_lasso",
        "accuracy": r2_test,
        "accuracy_train": r2_train,
        "accuracy_test": r2_test,
        "r2": r2_test,
        "r2_train": r2_train,
        "r2_test": r2_test,
        "mse": mse_test,
        "mse_train": mse_train,
        "mse_test": mse_test,
        "n_nonzero_coef": int(np.sum(coef != 0)),
        "split_info": split_info,
        "config": {
            "alpha": alpha, "fit_intercept": fit_intercept,
            "max_iter": max_iter, "n_features": X.shape[1],
            "task": "regression",
        },
        "sklearn_pickle_b64": None,
    }
    header_extra["config"]["forme_courbe"] = rapport_forme
    _sklearn_to_gy(clf, header_extra, save_path,
                   coef_tensors={"coef": coef, "intercept": intercept})
    _epoch_sim(on_epoch, max(1, epochs), [mse_test])

    return {
        "model": {"forme_courbe": rapport_forme, "sklearn_pickle_b64": base64.b64encode(pickle.dumps(clf, protocol=4)).decode("ascii"),
                  "n_features": X.shape[1], "task": "regression"},
        "save_path": save_path, "accuracy": acc,
        "accuracy_train": acc_train, "accuracy_test": acc_test,
        "history": [mse_test],
    }


# ─────────────────────────────────────────────────────────────
# Extract helpers (compatibilité Trainer._REGISTRY)
# ─────────────────────────────────────────────────────────────

def _extract_sklearn(result):
    """Extrait (weights, config) depuis le dict retourné par un train_sklearn_xxx.

    Pour les modèles sklearn, il n'y a pas de tenseurs numpy « poids » à
    proprement parler. On remet le pickle_b64 dans le config pour que
    Model.predict() puisse le récupérer via `config['sklearn_pickle_b64']`,
    et on retourne des tenseurs vides (le Trainer ne les utilisera pas).

    Important : le pickle_b64 doit être dans `config` (pas seulement dans
    `model`) parce que Model.save() reconstruit le header depuis self.config,
    et Model.load() peuple self.config depuis header['config'].
    """
    model_dict = result.get("model", {})
    # On copie tout le model_dict dans config — cela inclut sklearn_pickle_b64
    config = {k: v for k, v in model_dict.items()}
    config.setdefault("versions", _versions_courantes())   # relu à l'inférence : avertit si sklearn/numpy ont changé
    # Pas de tenseurs séparés — le pickle est auto-suffisant dans config
    weights = {}
    return weights, config
