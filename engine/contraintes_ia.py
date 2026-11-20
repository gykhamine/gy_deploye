"""Contraintes IMPOSEES par algorithme (deep learning ET machine learning).

Declaratif : un profil par type de modele + quelques regles generiques sur
les hyperparametres. Trois niveaux de controle, du moins au plus couteux :
  1. verifier_parametres : hyperparametres (types, bornes, combinaisons).
  2. (ia_integration.valider_configuration) : colonnes effectives vs types.
  3. verifier_dataset    : cible, valeurs manquantes, volume, formes — apres
     resolution des donnees. Renvoie des avertissements, leve sinon.
Toutes les violations sont collectees d'un coup (message en liste).
Module pur : numpy uniquement, aucun Django."""
from engine.i18n import t as _i18n_t, tf, tl
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
import math

import numpy as np

from .exceptions_ia import ContrainteModeleError
from engine.i18n import tf as _gtf  # noqa: E402

SK_CLASSIF = ("sklearn_rf", "sklearn_gbm", "sklearn_svm", "sklearn_knn",
              "sklearn_lr", "sklearn_nb", "sklearn_dt")
SK_REGRESSION = ("sklearn_ridge", "sklearn_lasso")
# Non supervises (aucune colonne cible) : lot 1 = groupes (clustering) ; lot 2 = projection (ACP, ICA) et anomalies.
SK_NONSUP_GROUPES = ("sklearn_kmeans", "sklearn_dbscan", "sklearn_hierarchique", "sklearn_gmm")
SK_NONSUP_PROJECTION = ("sklearn_acp", "sklearn_ica")
SK_NONSUP_ANOMALIES = ("sklearn_isoforest", "sklearn_ocsvm", "sklearn_lof")
TYPES_SANS_CIBLE = SK_NONSUP_GROUPES + SK_NONSUP_PROJECTION + SK_NONSUP_ANOMALIES


# Un modèle ne s'entraîne JAMAIS sur plus de LIMITE_LIGNES_ENTRAINEMENT lignes : au-delà, il refuse le jeu et demande de le couper
# (gestion des données → ✂ Découper, engine/decoupage_table.py). Même valeur que le plafond absolu d'une table
# (TableDynamique.PLAFOND_ABSOLU_LIGNES) : seule une table fusionnée ou un jeu venu d'ailleurs peut la dépasser.
LIMITE_LIGNES_ENTRAINEMENT = 100_000


def verifier_volume_entrainement(n, libelle=None, noms_tables=None):
    """Lève ContrainteModeleError si `n` lignes dépassent LIMITE_LIGNES_ENTRAINEMENT (message i18n : combien de lignes, quelle
    limite, comment couper). `noms_tables` : les tables concernées, citées dans le message quand on les connaît."""
    if n is None or n <= LIMITE_LIGNES_ENTRAINEMENT:
        return
    if noms_tables:
        msg = tf("decoupage.err_entrainement_trop_gros_tables", n=n, max=LIMITE_LIGNES_ENTRAINEMENT, tables=", ".join(noms_tables))
    else:
        msg = tf("decoupage.err_entrainement_trop_gros", n=n, max=LIMITE_LIGNES_ENTRAINEMENT)
    _lever([msg], libelle or "", "Donnees incompatibles")


def famille_sans_cible(type_modele):
    """'groupes' | 'projection' | 'anomalies' pour un type sans cible, None sinon (type supervise ou generatif)."""
    if type_modele in SK_NONSUP_GROUPES:
        return "groupes"
    if type_modele in SK_NONSUP_PROJECTION:
        return "projection"
    if type_modele in SK_NONSUP_ANOMALIES:
        return "anomalies"
    return None


# Duplique volontairement IA.train.sklearn_nonsup.MAX_LIGNES_QUADRATIQUE (ce module reste pur : pas d'import de sklearn).
MAX_LIGNES_QUADRATIQUE = 20000
# Idem pour IA.train.sklearn_nonsup.MAX_LIGNES_VOISINAGE / MAX_ARBRES (DBSCAN et LOF gardent des points de référence dans le .gy).
MAX_LIGNES_VOISINAGE = 100000
MAX_ARBRES = 5000

# Nature de la cible (y) attendue, par type supervise.
#   binaire : valeurs dans {0,1} (sortie sigmoide)   classes : entiers >= 0
#   binaire_ou_classes : 2 classes {0,1} ou K entiers   continue : reels
#   auto : MLP — classification OU regression selon la cible (IA/tache_cible.py)
CIBLE = {
    "mlp": "auto", "rnn": "binaire", "cnn": "binaire",
    "transformer": "binaire", "image_classifier": "classes",
    "speech_classifier": "classes",
    **{t: "classes" for t in SK_CLASSIF}, **{t: "continue" for t in SK_REGRESSION},
    **{t: "aucune" for t in TYPES_SANS_CIBLE},
}
_LIBELLE_CIBLE = {
    "binaire": tl("engine/contraintes_ia.py:g:31:16"),
    "classes": "cible = classes entieres (>= 2 classes, ~2 exemples/classe minimum)",
    "auto": tl("engine/contraintes_ia.py:g:33:27"),
    "continue": "cible numerique continue (regression)",
    "aucune": tl("ia.nonsup.contrainte_sans_cible"),
}
# Libelle « sans cible » propre a chaque famille (le texte de « aucune » parle de groupes).
_LIBELLE_SANS_CIBLE = {
    "groupes": _LIBELLE_CIBLE["aucune"],
    "projection": tl("ia.nonsup.contrainte_sans_cible_projection"),
    "anomalies": tl("ia.nonsup.contrainte_sans_cible_anomalies"),
}

_ENTIER_MIN = {
    "epochs": 1, "batch_size": 1, "hidden_size": 1, "hidden_dim": 1, "num_layers": 1,
    "num_blocks": 1, "num_conv_layers": 1, "embed_dim": 1, "ff_dim": 1, "seq_len": 1,
    "vocab_size": 2, "num_classes": 2, "timesteps": 1, "image_size": 2, "signal_length": 2,
    "latent_dim": 1, "data_dim": 1, "feature_dim": 1, "volume_size": 2, "channels": 1,
    "n_estimators": 1, "n_neighbors": 1, "max_iter": 1, "degree": 1, "p": 1,
    "min_samples_split": 2, "min_samples_leaf": 1, "dimensions": 1,
    "n_clusters": 2, "n_init": 1, "min_samples": 1,
}
_REEL_STRICT_POSITIF = ("lr", "learning_rate", "C", "grad_clip", "eps")
_REEL_POSITIF_OU_NUL = ("early_stop_loss", "var_smoothing")
_LISTES_ENTIERS = ("hidden_sizes", "hidden_layers", "generator_layers", "discriminator_layers")
_ENUMS = {
    "kernel": {"linear", "poly", "rbf", "sigmoid"},
    "weights": {"uniform", "distance"},
    "metric": {"minkowski", "euclidean", "manhattan", "chebyshev"},
    "criterion": {"gini", "entropy", "log_loss"},
    "penalty": {"l1", "l2"},
    "solver": {"lbfgs", "liblinear", "newton-cg", "sag", "saga"},
    "linkage": {"ward", "complete", "average", "single"},
    "covariance_type": {"full", "tied", "diag", "spherical"},
    "algorithm": {"parallel", "deflation"},
}
# Regression logistique : penalite supportee par solveur.
_SOLVEURS_PENALITE = {"lbfgs": {"l2"}, "newton-cg": {"l2"}, "sag": {"l2"},
                      "liblinear": {"l1", "l2"}, "saga": {"l1", "l2"}}


def _nombre(v):
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)


def verifier_parametres(type_modele, valeurs, libelle=None):
    """`valeurs` = hyperparametres EFFECTIFS (defauts de la signature + saisie)."""
    e = []
    for nom, v in valeurs.items():
        if v is None:
            continue
        if nom in _ENTIER_MIN:
            if not _nombre(v) or v != int(v) or v < _ENTIER_MIN[nom]:
                e.append(_gtf("engine/contraintes_ia.py:111:26", v1=(nom), v2=(v), v3=(_ENTIER_MIN[nom])))
        elif nom in _REEL_STRICT_POSITIF:
            if not _nombre(v) or not (v > 0) or not math.isfinite(v):
                e.append(_gtf("engine/contraintes_ia.py:114:26", v1=(nom), v2=(v)))
        elif nom in _REEL_POSITIF_OU_NUL:
            if not _nombre(v) or v < 0:
                e.append(tf("engine/contraintes_ia.py:g:79:26", v1=nom, v2=v))
        elif nom == "dropout":
            if not _nombre(v) or not (0 <= v < 1):
                e.append(tf("engine/contraintes_ia.py:g:82:26", v1=v))
        elif nom == "subsample":
            if not _nombre(v) or not (0 < v <= 1):
                e.append(tf("engine/contraintes_ia.py:g:85:26", v1=v))
        elif nom == "alpha":
            if not _nombre(v) or v < 0 or (type_modele == "sklearn_lasso" and v == 0):
                e.append(f"Parametre 'alpha' = {v!r} : "
                         + (_i18n_t("engine/contraintes_ia.py:g:89:29") if type_modele == "sklearn_lasso" else ">= 0 requis."))
        elif nom == "max_depth":
            if not _nombre(v) or v != int(v) or v < 1:
                e.append(tf("engine/contraintes_ia.py:g:92:26", v1=v))
        elif nom == "max_features":
            if not (v in ("sqrt", "log2") or (_nombre(v) and (v >= 1 or 0 < v <= 1))):
                e.append(_i18n_t("engine/contraintes_ia.py:g:95:26"))
        elif nom == "contamination":
            if not _nombre(v) or not (0 < v <= 0.5):
                e.append(tf("ia.nonsup.err_proportion", param=nom, max=0.5, valeur=v))
        elif nom == "nu":
            if not _nombre(v) or not (0 < v <= 1):
                e.append(tf("ia.nonsup.err_proportion", param=nom, max=1, valeur=v))
        elif nom == "gamma" and type_modele in ("sklearn_svm", "sklearn_ocsvm"):
            if not (v in ("scale", "auto") or (_nombre(v) and v > 0)):
                e.append(_i18n_t("engine/contraintes_ia.py:g:98:26"))
        elif nom in _ENUMS:
            if v not in _ENUMS[nom] and not (nom == "criterion" and type_modele not in ("sklearn_dt",)):
                e.append(tf("engine/contraintes_ia.py:g:101:26", v1=nom, v2=v, v3=', '.join(sorted(_ENUMS[nom]))))
        elif nom in _LISTES_ENTIERS:
            if not v or any((not _nombre(x)) or x < 1 for x in v):
                e.append(tf("engine/contraintes_ia.py:g:104:26", v1=nom))
    if type_modele == "sklearn_isoforest":
        ne = valeurs.get("n_estimators")
        if _nombre(ne) and ne == int(ne) and ne > MAX_ARBRES:
            e.append(tf("ia.nonsup.err_borne", param="n_estimators", min=1, max=MAX_ARBRES, valeur=int(ne)))
    if type_modele == "sklearn_gmm":
        nc = valeurs.get("n_components")
        if nc is not None and (not _nombre(nc) or nc != int(nc) or nc < 2):
            e.append(tf("ia.nonsup.err_entier_min", param="n_components", valeur=nc, min=2))
    if type_modele in SK_NONSUP_PROJECTION:
        nc = valeurs.get("n_components")
        if nc is not None and (not _nombre(nc) or nc != int(nc) or nc < 1):
            e.append(tf("ia.nonsup.err_entier_min", param="n_components", valeur=nc, min=1))
    if type_modele == "sklearn_lr":
        sol, pen = valeurs.get("solver"), valeurs.get("penalty")
        if sol in _SOLVEURS_PENALITE and pen in ("l1", "l2") and pen not in _SOLVEURS_PENALITE[sol]:
            e.append(tf("engine/contraintes_ia.py:g:108:22", v1=sol, v2=pen, v3=', '.join(sorted(_SOLVEURS_PENALITE[sol]))))
    if type_modele in ("cnn",):
        ks, ish = valeurs.get("kernel_shape"), valeurs.get("input_shape")
        if ks and ish and len(ks) == len(ish) and any(k > s for k, s in zip(ks, ish)):
            e.append(tf("engine/contraintes_ia.py:g:114:22", v1=tuple(ks), v2=tuple(ish)))
    _lever(e, libelle or type_modele, "Parametres invalides")


def verifier_dataset(type_modele, dataset, valeurs, libelle=None):
    """Controles apres resolution du dataset. Retourne la liste des
    avertissements non bloquants ; leve ContrainteModeleError sinon."""
    X, y = dataset.get("X"), dataset.get("y")
    if X is not None and np.ndim(X):
        verifier_volume_entrainement(np.shape(X)[0], libelle or type_modele)   # avant tout autre contrôle : trop gros = à couper d'abord
    cible = CIBLE.get(type_modele)
    if cible is None or X is None:
        return []
    e, w = [], []
    X = np.asarray(X)
    n = X.shape[0]
    if n < 4:
        e.append(tf("engine/contraintes_ia.py:g:130:18", v1=n))
    manquants = int((~np.isfinite(X.astype(np.float64))).sum()) if X.size else 0
    if manquants:
        e.append(tf("engine/contraintes_ia.py:g:133:18", v1=manquants))
    elif X.size and np.all(X == X.reshape(n, -1)[0].reshape(X.shape[1:])):
        w.append(_i18n_t("engine/contraintes_ia.py:g:136:18"))
    if type_modele in TYPES_SANS_CIBLE:
        _verifier_dataset_sans_cible(type_modele, X, n, valeurs, e, w)
    if y is not None and n >= 1 and cible != "aucune":
        y2 = np.asarray(y).reshape(n, -1)
        if y2.shape[1] != 1:
            e.append(tf("engine/contraintes_ia.py:g:140:22", v1=y2.shape[1]))
        else:
            cible_eff = cible
            if cible == "auto":
                from IA.tache_cible import detecter_tache  # import tardif : module pur, regle unique avec train_mlp
                regression = detecter_tache(
                    np.nan_to_num(y2[:, 0].astype(np.float64), nan=0.0),
                    forcer_classes=dataset.get("classes_cible") is not None) == "regression"
                cible_eff = "continue" if regression else "binaire_ou_classes"
            _verifier_cible(type_modele, cible_eff, y2[:, 0].astype(np.float64), n, valeurs, e, w)
    if type_modele == "sklearn_knn" and _nombre(valeurs.get("n_neighbors")):
        n_train = n if n < 10 else n - math.ceil(0.2 * n)
        if valeurs["n_neighbors"] > n_train:
            e.append(tf("engine/contraintes_ia.py:g:147:22", v1=valeurs['n_neighbors'], v2=n_train, v3=n_train))
    if type_modele == "transformer" and X.ndim == 2 and valeurs.get("seq_len") not in (None, X.shape[1]):
        e.append(tf("engine/contraintes_ia.py:g:150:18", v1=valeurs['seq_len'], v2=X.shape[1], v3=X.shape[1]))
    if type_modele in SK_CLASSIF + SK_REGRESSION and n < 10:
        w.append(tf("engine/contraintes_ia.py:g:153:18", v1=n))
    _lever(e, libelle or type_modele, "Donnees incompatibles")
    return w


def _verifier_dataset_sans_cible(type_modele, X, n, valeurs, e, w):
    """Controles propres aux modeles de groupes : forme 2D, bornes dependant du nombre de lignes."""
    if X.ndim != 2:
        e.append(tf("ia.nonsup.err_x_2d", ndim=X.ndim))
        return
    famille = famille_sans_cible(type_modele)
    if famille != "groupes":
        # Projection : au plus autant de composantes que de colonnes ET que de lignes. Anomalies : voisins de LOF < lignes ;
        # One-Class SVM a un cout quadratique.
        if famille == "projection":
            v = valeurs.get("n_components")
            borne_max = min(n, X.shape[1])
            if _nombre(v) and v == int(v) and not (1 <= v <= borne_max):
                e.append(tf("ia.nonsup.err_borne", param="n_components", min=1, max=borne_max, valeur=int(v)))
        elif type_modele == "sklearn_lof":
            v = valeurs.get("n_neighbors")
            if _nombre(v) and v == int(v) and not (1 <= v <= n - 1):
                e.append(tf("ia.nonsup.err_borne", param="n_neighbors", min=1, max=n - 1, valeur=int(v)))
            if n > MAX_LIGNES_VOISINAGE:
                e.append(tf("ia.nonsup.err_trop_lignes", algo="LocalOutlierFactor", max=MAX_LIGNES_VOISINAGE, n=n))
        elif type_modele == "sklearn_ocsvm" and n > MAX_LIGNES_QUADRATIQUE:
            e.append(tf("ia.nonsup.err_trop_lignes", algo="OneClassSVM", max=MAX_LIGNES_QUADRATIQUE, n=n))
        if n < 10:
            w.append(tf("ia.nonsup.avert_peu_lignes_resultat", n=n))
        return
    for param, borne_min in (("n_clusters", 2), ("n_components", 2), ("min_samples", 1)):
        v = valeurs.get(param)
        if type_modele == "sklearn_dbscan" and param != "min_samples":
            continue
        if type_modele in ("sklearn_kmeans", "sklearn_hierarchique") and param != "n_clusters":
            continue
        if type_modele == "sklearn_gmm" and param != "n_components":
            continue
        borne_max = n if param == "min_samples" else n - 1
        if _nombre(v) and v == int(v) and not (borne_min <= v <= borne_max):
            e.append(tf("ia.nonsup.err_borne", param=param, min=borne_min, max=borne_max, valeur=int(v)))
    if type_modele == "sklearn_dbscan" and n > MAX_LIGNES_VOISINAGE:
        e.append(tf("ia.nonsup.err_trop_lignes", algo="DBSCAN", max=MAX_LIGNES_VOISINAGE, n=n))
    if type_modele == "sklearn_hierarchique" and n > MAX_LIGNES_QUADRATIQUE:
        e.append(tf("ia.nonsup.err_trop_lignes", algo="AgglomerativeClustering", max=MAX_LIGNES_QUADRATIQUE, n=n))
    if n < 10:
        w.append(tf("ia.nonsup.avert_peu_lignes", n=n))


def _verifier_cible(type_modele, cible, y, n, valeurs, e, w):
    if not np.isfinite(y).all():
        e.append(_i18n_t("engine/contraintes_ia.py:g:161:18"))
        return
    if cible == "continue":
        if np.ptp(y) == 0:
            e.append(_i18n_t("engine/contraintes_ia.py:g:165:22"))
        return
    entiere = bool(np.allclose(y, np.round(y)))
    classes, comptes = np.unique(y, return_counts=True)
    k = len(classes)
    if not entiere:
        e.append(_i18n_t("engine/contraintes_ia.py:g:171:18"))
        return
    if k < 2:
        e.append(_i18n_t("engine/contraintes_ia.py:g:176:18"))
        return
    if cible == "binaire" and not set(classes.tolist()) <= {0.0, 1.0}:
        e.append(tf("engine/contraintes_ia.py:g:170:18", v1=', '.join(str(int(c)) for c in classes[:6]), v2='…' if k > 6 else ''))
    elif cible == "binaire_ou_classes" and k == 2 and not set(classes.tolist()) <= {0.0, 1.0}:
        e.append(tf("engine/contraintes_ia.py:g:182:18", v1=int(classes[0]), v2=int(classes[1])))
    elif cible == "classes" and classes.min() < 0:
        e.append(_i18n_t("engine/contraintes_ia.py:g:185:18"))
    if cible != "binaire" and k > n / 2:
        e.append(tf("engine/contraintes_ia.py:g:187:18", v1=k, v2=n))
    elif comptes.min() < 2:
        w.append(_i18n_t("engine/contraintes_ia.py:g:190:18"))
    nc = valeurs.get("num_classes")
    if cible == "classes" and _nombre(nc) and y.max() >= nc:
        e.append(tf("engine/contraintes_ia.py:g:193:18", v1=int(nc), v2=int(y.max()), v3=int(y.max()) + 1))


def _lever(erreurs, libelle, titre):
    if erreurs:
        raise ContrainteModeleError(
            _gtf("rg6.msg.006", v1=titre, v2=libelle) + "\n".join("• " + x for x in erreurs))


def descriptions(type_modele):
    """Contraintes lisibles (affichees dans l'interface sous le type choisi)."""
    if type_modele not in CIBLE:
        return []
    if type_modele in TYPES_SANS_CIBLE:
        d = [_LIBELLE_SANS_CIBLE[famille_sans_cible(type_modele)], _i18n_t("engine/contraintes_ia.py:g:208:10")]
        if type_modele in SK_NONSUP_PROJECTION:
            d.append(_i18n_t("ia.nonsup.contrainte_projection"))
        if type_modele in ("sklearn_isoforest", "sklearn_lof"):
            d.append(_i18n_t("ia.nonsup.contrainte_contamination"))
        if type_modele == "sklearn_ocsvm":
            d.append(_i18n_t("ia.nonsup.contrainte_nu"))
            d.append(tf("ia.nonsup.contrainte_quadratique", max=MAX_LIGNES_QUADRATIQUE))
        if type_modele == "sklearn_lof":
            d.append(_i18n_t("ia.nonsup.contrainte_lof"))
        if type_modele in ("sklearn_kmeans", "sklearn_hierarchique"):
            d.append(_i18n_t("ia.nonsup.contrainte_n_clusters"))
        if type_modele == "sklearn_gmm":
            d.append(_i18n_t("ia.nonsup.contrainte_n_components"))
        if type_modele == "sklearn_dbscan":
            d.append(_i18n_t("ia.nonsup.contrainte_dbscan"))
        if type_modele == "sklearn_hierarchique":
            d.append(tf("ia.nonsup.contrainte_quadratique", max=MAX_LIGNES_QUADRATIQUE))
        return d
    d = [_LIBELLE_CIBLE[CIBLE[type_modele]], _i18n_t("engine/contraintes_ia.py:g:207:46"),
         _i18n_t("engine/contraintes_ia.py:g:208:10")]
    if type_modele == "sklearn_knn":
        d.append(_i18n_t("engine/contraintes_ia.py:g:210:18"))
    if type_modele == "sklearn_lr":
        d.append(_i18n_t("engine/contraintes_ia.py:g:212:18"))
    if type_modele == "sklearn_lasso":
        d.append("alpha > 0")
    if type_modele == "transformer":
        d.append(_i18n_t("engine/contraintes_ia.py:g:216:18"))
    return d
