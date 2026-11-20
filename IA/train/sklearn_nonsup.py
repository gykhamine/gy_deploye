"""
IA/train/sklearn_nonsup.py — modèles scikit-learn NON SUPERVISÉS (sans colonne cible), sauvegardés en .gy.

Lot 1 : les 4 algorithmes de groupes (clustering).
  1. sklearn_kmeans       — K-Means
  2. sklearn_dbscan       — DBSCAN (les points isolés reçoivent le groupe -1 = « bruit »)
  3. sklearn_hierarchique — classification hiérarchique (AgglomerativeClustering)
  4. sklearn_gmm          — mélange gaussien (GaussianMixture)

Lot 2 : projection (réduction de dimension) et détection d'anomalies.
  5. sklearn_acp          — analyse en composantes principales (PCA) : predict() renvoie les coordonnées
  6. sklearn_ica          — analyse en composantes indépendantes (FastICA) : predict() renvoie les sources
  7. sklearn_isoforest    — Isolation Forest : predict() renvoie [anomalie 0/1, score]
  8. sklearn_ocsvm        — One-Class SVM : idem
  9. sklearn_lof          — Local Outlier Factor (mode novelty) : idem

Contrat identique aux 9 modèles supervisés de sklearn_models.py :
  - train_sklearn_xxx(X, y=None, ..., save_path, on_epoch) -> dict {model, save_path, accuracy, history, ...}
  - sauvegarde .gy via _sklearn_to_gy ; inférence via _predict_sklearn_generic (colonne de numéros de groupe).

ATTENTION — compatibilité des modèles déjà enregistrés : les classes ModeleGroupes, ModeleProjection et
ModeleAnomalies sont référencées PAR NOM dans les pickles des .gy (IA.train.sklearn_nonsup.<Classe>). Ne pas les
renommer ni déplacer ce module sans conserver un alias. Le .gy mémorise aussi les versions de scikit-learn/numpy
qui l'ont produit (config["versions"]) ; une différence est signalée dans les journaux à l'inférence.

Particularités :
  - `y` est accepté et ignoré (le Trainer peut le passer sans condition).
  - Les colonnes sont standardisées (moyenne 0, écart-type 1) AVANT le calcul, sinon la colonne à plus grande
    échelle écrase les autres. Le standardiseur fait partie du modèle sauvegardé.
  - DBSCAN et la classification hiérarchique n'ont pas de predict() natif : le modèle sauvegardé est un
    enveloppeur (ModeleGroupes) qui rattache un nouveau point au groupe le plus proche
    (cœur DBSCAN dans un rayon eps, sinon bruit ; centre de groupe le plus proche pour la hiérarchique).
  - Score « accuracy » = qualité 0–100 dérivée de la silhouette : (silhouette + 1) / 2 × 100.
    Il vaut 0 quand la silhouette n'est pas définie (moins de 2 groupes). La silhouette brute est aussi fournie.
  - Aucun texte d'interface en dur : les messages d'erreur et libellés passent par tf() (clés « ia.nonsup.* »,
    présentes en fr ET en dans engine/i18n_defaults/).
"""

import base64
import logging
import os
import pickle

import numpy as np
from sklearn.cluster import AgglomerativeClustering, DBSCAN, KMeans
from sklearn.decomposition import PCA, FastICA
from sklearn.ensemble import IsolationForest
from sklearn.metrics import silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.neighbors import LocalOutlierFactor, NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM

from engine.contraintes_ia import famille_sans_cible
from engine.i18n import tf

from ..config import MODELS_DIR, MODEL_EXTENSION, ensure_directories
from .forme_courbe import preparer_colonnes
from .sklearn_models import TAILLE_MAX_PICKLE, _epoch_sim, _sklearn_to_gy
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

# Les algorithmes à coût quadratique (matrice de distances) sont refusés au-delà de cette taille.
MAX_LIGNES_QUADRATIQUE = 20000
# DBSCAN et LOF ne bâtissent pas de matrice de distances complète, mais le modèle sauvegardé garde les points de
# référence (cœurs DBSCAN / données LOF) : au-delà de cette taille, le .gy risquerait de dépasser TAILLE_MAX_PICKLE
# (il serait alors enregistré mais impossible à relire). Dupliqué dans engine/contraintes_ia.py (module pur).
MAX_LIGNES_VOISINAGE = 100000
# Bornes hautes des réglages entiers (évitent un calcul démesuré sur une saisie aberrante).
MAX_ITERATIONS = 100000
MAX_ARBRES = 5000
# Silhouette estimée sur un échantillon au-delà de cette taille (le calcul exact est quadratique).
SILHOUETTE_ECHANTILLON = 5000

LIAISONS = ("ward", "complete", "average", "single")
COVARIANCES = ("full", "tied", "diag", "spherical")
ALGOS_ICA = ("parallel", "deflation")
NOYAUX_OCSVM = ("rbf", "linear", "poly", "sigmoid")

BRUIT = -1


# ─────────────────────────────────────────────────────────────
# Libellés lisibles (utilisés par l'intégration à l'inférence)
# ─────────────────────────────────────────────────────────────

def libelle_groupe(numero):
    """« Groupe 2 » (numérotation à partir de 1) ou « Hors groupe (bruit) » pour -1."""
    numero = int(numero)
    if numero == BRUIT:
        return tf("ia.nonsup.libelle_bruit")
    return tf("ia.nonsup.libelle_groupe", n=numero + 1)


def libelles_groupes(predictions):
    """Sortie brute de predict() (liste de [numero] ou de numeros, ou un seul) -> libelles lisibles."""
    def un(p):
        while isinstance(p, (list, tuple, np.ndarray)):
            p = p[0] if len(p) else 0
        return libelle_groupe(int(round(float(p))))
    if isinstance(predictions, (list, tuple, np.ndarray)):
        return [un(p) for p in predictions]
    return un(predictions)


def libelles_predictions(type_modele, sortie):
    """Sortie brute de predict() -> libellés lisibles, selon la famille du modèle sans cible.

    groupes : « Groupe 2 » ; projection : « Composante 1 = 0.52 ; Composante 2 = -1.30 » ;
    anomalies (colonnes [indicateur 0/1, score]) : « Anomalie (score 0.62) » / « Normal (score 0.41) ».
    """
    famille = famille_sans_cible(type_modele)
    if famille == "projection":
        return [" ; ".join(tf("ia.nonsup.libelle_composante", i=i + 1, v=f"{float(v):.3f}")
                           for i, v in enumerate(np.atleast_1d(ligne)))
                for ligne in np.atleast_2d(np.asarray(sortie, dtype=float))]
    if famille == "anomalies":
        return [tf("ia.nonsup.libelle_anomalie" if int(round(float(l[0]))) == 1 else "ia.nonsup.libelle_normal",
                   score=f"{float(l[1]):.3f}")
                for l in np.atleast_2d(np.asarray(sortie, dtype=float))]
    return libelles_groupes(sortie)


# ─────────────────────────────────────────────────────────────
# Enveloppeur picklable : standardisation + estimateur + règle d'attribution
# ─────────────────────────────────────────────────────────────

class ModeleGroupes:
    """Modèle de groupes prêt à prédire, sérialisé tel quel dans le .gy (pickle base64).

    Attributs : algo, scaler, est (estimateur sklearn ajusté ; None pour DBSCAN, dont seuls les points cœurs servent),
    centres (n_groupes × n_colonnes, espace standardisé), labels_groupes (numéros de groupe des lignes de `centres`),
    eps (DBSCAN) ; `noyaux` / `labels_noyaux` = points de référence (cœurs DBSCAN, ou lignes d'entraînement pour la
    hiérarchique en liaison single/average) rattachés par plus proche voisin. Ces points ne sont conservés qu'UNE fois
    (dans l'index de voisinage), pas en double dans le pickle.
    """

    BLOC_PREDICTION = 2000   # lignes traitées à la fois (borne la mémoire du calcul des distances aux centres)

    def __init__(self, algo, scaler, est, centres=None, labels_groupes=None, eps=None, noyaux=None, labels_noyaux=None):
        self.algo = algo
        self.scaler = scaler
        self.est = est
        self.centres = centres
        self.labels_groupes = labels_groupes
        self.eps = eps
        self._nn = None
        self._labels_noyaux = None
        if noyaux is not None and len(noyaux) > 0:
            self._nn = NearestNeighbors(n_neighbors=1).fit(noyaux)
            self._labels_noyaux = np.asarray(labels_noyaux, dtype=int)

    def predict(self, X):
        X = np.asarray(X, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        Z = self.scaler.transform(X)
        if self.algo in ("kmeans", "gmm"):
            return np.asarray(self.est.predict(Z), dtype=int)
        nn = getattr(self, "_nn", None)
        if self.algo == "hierarchique":
            if nn is not None:   # liaisons single/average : groupes allongés, le centre le plus proche ne les reproduit pas
                _, idx = nn.kneighbors(Z)
                return self._labels_noyaux[idx[:, 0]].astype(int)
            etiquettes = np.asarray(self.labels_groupes)
            sortie = np.empty(len(Z), dtype=int)
            for debut in range(0, len(Z), self.BLOC_PREDICTION):
                bloc = Z[debut:debut + self.BLOC_PREDICTION]
                d = np.linalg.norm(bloc[:, None, :] - self.centres[None, :, :], axis=2)
                sortie[debut:debut + len(bloc)] = etiquettes[np.argmin(d, axis=1)]
            return sortie
        if self.algo == "dbscan":
            if nn is None:  # aucun point cœur : tout est du bruit
                return np.full(len(Z), BRUIT, dtype=int)
            dist, idx = nn.kneighbors(Z)
            labels = self._labels_noyaux[idx[:, 0]].astype(int)
            labels[dist[:, 0] > self.eps] = BRUIT
            return labels
        raise ValueError(self.algo)


def _tableau_2d(X):
    X = np.asarray(X, dtype=np.float64)
    return X.reshape(1, -1) if X.ndim == 1 else X


class ModeleProjection:
    """ACP / ICA prêt à prédire : standardisation + estimateur. predict() renvoie les coordonnées (lignes × composantes).

    `sortie_matricielle` : _predict_sklearn_generic garde alors la matrice telle quelle (sinon il l'aplatit en une colonne).
    """
    sortie_matricielle = True

    def __init__(self, algo, scaler, est):
        self.algo = algo
        self.scaler = scaler
        self.est = est

    def predict(self, X):
        return np.asarray(self.est.transform(self.scaler.transform(_tableau_2d(X))), dtype=np.float64)


class ModeleAnomalies:
    """Détecteur d'anomalies prêt à prédire : standardisation + détecteur. predict() renvoie 2 colonnes :
    [1 si anomalie sinon 0, score d'anomalie (plus haut = plus anormal ; l'échelle dépend de l'algorithme)]."""
    sortie_matricielle = True

    def __init__(self, algo, scaler, est):
        self.algo = algo
        self.scaler = scaler
        self.est = est

    def predict(self, X):
        Z = self.scaler.transform(_tableau_2d(X))
        anomalie = (np.asarray(self.est.predict(Z)) == -1).astype(np.float64)   # sklearn : -1 = anomalie, +1 = normal
        return np.column_stack([anomalie, -np.asarray(self.est.score_samples(Z), dtype=np.float64)])


# ─────────────────────────────────────────────────────────────
# Validations communes
# ─────────────────────────────────────────────────────────────

def _preparer_donnees(X, seed, minimum=2):
    """Renvoie X en float64 2D, après vérification (forme, valeurs finies, nombre de lignes)."""
    if X is None:
        X = _donnees_demo(seed)
    X = np.asarray(X, dtype=np.float64)
    if X.ndim != 2 or X.shape[1] < 1:
        raise ValueError(tf("ia.nonsup.err_donnees_invalides", forme=str(X.shape)))
    if not np.all(np.isfinite(X)):
        raise ValueError(tf("ia.nonsup.err_valeurs_non_finies"))
    if X.shape[0] < minimum:
        raise ValueError(tf("ia.nonsup.err_trop_peu_lignes", minimum=minimum, n=X.shape[0]))
    return X


def _donnees_demo(seed=42):
    """3 nuages de points bien séparés en 4 dimensions (démonstration)."""
    rng = np.random.default_rng(seed)
    centres = np.array([[0, 0, 0, 0], [6, 6, 0, 0], [0, 6, 6, 6]], dtype=float)
    return np.vstack([c + rng.standard_normal((70, 4)) for c in centres])


def _exiger_entier_borne(valeur, param, borne_max, borne_min=2):
    try:
        v = int(valeur)
    except (TypeError, ValueError):
        v = None
    if v is None or v != valeur or v < borne_min or v > borne_max:
        raise ValueError(tf("ia.nonsup.err_borne", param=param, min=borne_min, max=borne_max, valeur=valeur))
    return v


def _exiger_positif(valeur, param):
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        v = None
    if v is None or not np.isfinite(v) or v <= 0:
        raise ValueError(tf("ia.nonsup.err_param_positif", param=param, valeur=valeur))
    return v


def _exiger_proportion(valeur, param, borne_max):
    """Nombre dans ]0, borne_max] (contamination, nu)."""
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        v = None
    if v is None or not np.isfinite(v) or not (0 < v <= borne_max):
        raise ValueError(tf("ia.nonsup.err_proportion", param=param, max=borne_max, valeur=valeur))
    return v


def _exiger_gamma(valeur):
    return valeur if valeur in ("scale", "auto") else _exiger_positif(valeur, "gamma")


def _exiger_choix(valeur, param, choix):
    if valeur not in choix:
        raise ValueError(tf("ia.nonsup.err_choix", param=param, valeurs=", ".join(choix), valeur=valeur))
    return valeur


def _exiger_pas_quadratique(n, algo, maximum=MAX_LIGNES_QUADRATIQUE):
    if n > maximum:
        raise ValueError(tf("ia.nonsup.err_trop_lignes", algo=algo, max=maximum, n=n))


def _serialiser(modele):
    """Pickle du modèle ; refuse un modèle que la lecture (limite TAILLE_MAX_PICKLE) rejetterait ensuite."""
    brut = pickle.dumps(modele, protocol=4)
    if len(brut) > TAILLE_MAX_PICKLE:
        raise ValueError(tf("ia.nonsup.err_modele_trop_gros", taille=len(brut) // (1024 * 1024),
                            max=TAILLE_MAX_PICKLE // (1024 * 1024)))
    return base64.b64encode(brut).decode("ascii")


# ─────────────────────────────────────────────────────────────
# Évaluation et sauvegarde communes
# ─────────────────────────────────────────────────────────────

def _evaluer(Z, labels, seed):
    """Silhouette (hors bruit) et répartition des groupes. silhouette = None si non définie."""
    labels = np.asarray(labels)
    masque = labels != BRUIT
    n_bruit = int((~masque).sum())
    groupes, tailles = np.unique(labels[masque], return_counts=True)
    silhouette = None
    n_utiles = int(masque.sum())
    if len(groupes) >= 2 and n_utiles > len(groupes):
        taille_ech = SILHOUETTE_ECHANTILLON if n_utiles > SILHOUETTE_ECHANTILLON else None
        try:
            silhouette = float(silhouette_score(Z[masque], labels[masque], sample_size=taille_ech, random_state=seed))
        except ValueError:
            # L'échantillon tiré peut ne contenir qu'un seul groupe (groupe minuscule face à un gros) : scikit-learn refuse.
            # La silhouette n'est qu'un indicateur : on la laisse non définie plutôt que d'échouer tout l'entraînement.
            logger.warning(_gt("rg7.msg.042"))
            silhouette = None
    qualite = 0.0 if silhouette is None else float(np.clip((silhouette + 1.0) / 2.0 * 100.0, 0.0, 100.0))
    return {
        "n_groupes": int(len(groupes)),
        "tailles_groupes": {int(g): int(t) for g, t in zip(groupes, tailles)},
        "n_bruit": n_bruit,
        "taux_bruit": n_bruit / len(labels),
        "silhouette": silhouette,
        "qualite": qualite,
    }


def _finaliser(code, modele, X_n_features, ev, extra_config, save_path, on_epoch, epochs, tenseurs=None):
    """Sérialise en .gy et construit le dict de retour attendu par le Trainer."""
    pkl_b64 = _serialiser(modele)
    config = {
        "n_features": X_n_features,
        "classes": sorted(ev["tailles_groupes"].keys()) + ([BRUIT] if ev["n_bruit"] else []),
        "n_groupes": ev["n_groupes"],
        "tailles_groupes": ev["tailles_groupes"],
        "n_bruit": ev["n_bruit"],
        "taux_bruit": ev["taux_bruit"],
        "silhouette": ev["silhouette"],
        "supervise": False,
        **extra_config,
        "sklearn_pickle_b64": None,  # écrasé dans _sklearn_to_gy
    }
    header_extra = {
        "type": code,
        "accuracy": ev["qualite"] / 100.0,
        "split_info": {"split_effectue": False, "n_train": None, "n_test": None},
        "config": config,
        "sklearn_pickle_b64": None,
    }
    _sklearn_to_gy(modele, header_extra, save_path, coef_tensors=tenseurs)
    perte = [1.0 - ev["qualite"] / 100.0]
    _epoch_sim(on_epoch, max(1, epochs), perte)
    return {
        "model": {
            "sklearn_pickle_b64": pkl_b64,
            "classes": config["classes"],
            "n_features": X_n_features,
            "n_groupes": ev["n_groupes"],
            "tailles_groupes": ev["tailles_groupes"],
            "n_bruit": ev["n_bruit"],
            "taux_bruit": ev["taux_bruit"],
            "silhouette": ev["silhouette"],
            "supervise": False,
            **extra_config,
        },
        "save_path": save_path,
        "accuracy": ev["qualite"],
        "silhouette": ev["silhouette"],
        "history": perte,
    }


def _finaliser_resume(code, modele, n_features, qualite, resume, extra_config, save_path, on_epoch, epochs, tenseurs=None):
    """Variante de _finaliser pour la projection et les anomalies : `resume` (dict JSON, valeurs Python natives) est stocké
    dans config['resume'] et relu par l'intégration (affiché dans l'encart « Ce que le modèle a trouvé »).
    `qualite` est le chiffre-clé 0–100 (information conservée pour la projection, % de lignes anormales pour les anomalies)."""
    pkl_b64 = _serialiser(modele)
    famille = famille_sans_cible(code)
    config = {
        "n_features": n_features,
        "supervise": False,
        "famille": famille,
        "resume": resume,
        **extra_config,
        "sklearn_pickle_b64": None,  # écrasé dans _sklearn_to_gy
    }
    header_extra = {
        "type": code,
        "accuracy": qualite / 100.0,
        "split_info": {"split_effectue": False, "n_train": None, "n_test": None},
        "config": config,
        "sklearn_pickle_b64": None,
    }
    _sklearn_to_gy(modele, header_extra, save_path, coef_tensors=tenseurs)
    perte = [1.0 - qualite / 100.0] if famille == "projection" else [0.0]
    _epoch_sim(on_epoch, max(1, epochs), perte)
    return {
        "model": {
            "sklearn_pickle_b64": pkl_b64,
            "n_features": n_features,
            "supervise": False,
            "famille": famille,
            "resume": resume,
            **extra_config,
        },
        "save_path": save_path,
        "accuracy": qualite,
        "history": perte,
    }


def _exiger_variation(Z):
    """Une projection n'a rien à résumer si toutes les colonnes sont constantes (Z, standardisé, est alors nul)."""
    if not np.any(Z):
        raise ValueError(tf("ia.nonsup.err_donnees_constantes"))


def _evaluer_projection(est, Z, variances=None):
    """Part d'information conservée = 1 − erreur de reconstruction / variance totale (espace standardisé), en 0–1."""
    ss_tot = float(((Z - Z.mean(axis=0)) ** 2).sum())
    if ss_tot <= 0:
        raise ValueError(tf("ia.nonsup.err_donnees_constantes"))
    ss_res = float(((Z - est.inverse_transform(est.transform(Z))) ** 2).sum())
    information = float(np.clip(1.0 - ss_res / ss_tot, 0.0, 1.0))
    return {
        "n_composantes": int(est.components_.shape[0]),
        "n_colonnes": int(Z.shape[1]),
        "information": information,
        "variance_composantes": None if variances is None else [float(v) for v in variances],
    }


def _evaluer_anomalies(est, Z, attendu, anormaux=None):
    """Anomalies trouvées sur les données d'entraînement ; `attendu` = part d'anomalies demandée par le réglage.

    `anormaux` : masque booléen déjà calculé par l'appelant. LOF en mode novelty ne doit PAS être évalué avec predict()
    sur ses propres données (chaque ligne se compte alors elle-même parmi ses voisins : le taux est sous-estimé)."""
    if anormaux is None:
        anormaux = np.asarray(est.predict(Z)) == -1
    n_anomalies = int(np.asarray(anormaux).sum())
    return {
        "n_lignes": int(len(Z)),
        "n_anomalies": n_anomalies,
        "taux_anomalies": n_anomalies / len(Z),
        "attendu": float(attendu),
    }


def _chemin(save_path, code):
    ensure_directories()
    if save_path is None:
        save_path = os.path.join(MODELS_DIR, f"mini_{code}{MODEL_EXTENSION}")
    return save_path


# ─────────────────────────────────────────────────────────────
# 1. K-Means
# ─────────────────────────────────────────────────────────────

def train_sklearn_kmeans(X=None, y=None, n_clusters=3, n_init=10, max_iter=300,
                         epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """K-Means : découpe les lignes en `n_clusters` groupes autour de centres.

    y est ignoré (non supervisé). Contraintes : 2 ≤ n_clusters ≤ nombre de lignes − 1 ; n_init, max_iter > 0.
    """
    X = _preparer_donnees(X, seed, minimum=3)
    n_clusters = _exiger_entier_borne(n_clusters, "n_clusters", X.shape[0] - 1)
    n_init = _exiger_entier_borne(n_init, "n_init", MAX_ITERATIONS, borne_min=1)
    max_iter = _exiger_entier_borne(max_iter, "max_iter", MAX_ITERATIONS, borne_min=1)
    save_path = _chemin(save_path, "sklearn_kmeans")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    est = KMeans(n_clusters=n_clusters, n_init=n_init, max_iter=max_iter, random_state=seed).fit(Z)
    modele = ModeleGroupes("kmeans", scaler, est, centres=est.cluster_centers_, labels_groupes=list(range(n_clusters)))
    ev = _evaluer(Z, est.labels_, seed)
    logger.info(_gt("rg7.msg.043"), ev["n_groupes"], ev["silhouette"])
    return _finaliser("sklearn_kmeans", modele, X.shape[1], ev,
                      {"n_clusters": n_clusters, "inertie": float(est.inertia_), "forme_courbe": rapport_forme},
                      save_path, on_epoch, epochs,
                      tenseurs={"centres": est.cluster_centers_})


# ─────────────────────────────────────────────────────────────
# 2. DBSCAN
# ─────────────────────────────────────────────────────────────

def train_sklearn_dbscan(X=None, y=None, eps=0.8, min_samples=5,
                         epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """DBSCAN : groupes par densité ; les points isolés sont du bruit (groupe -1). Le nombre de groupes est trouvé seul.

    y est ignoré. Contraintes : eps > 0 (dans l'espace standardisé) ; 1 ≤ min_samples ≤ nombre de lignes.
    """
    X = _preparer_donnees(X, seed, minimum=2)
    _exiger_pas_quadratique(X.shape[0], "DBSCAN", MAX_LIGNES_VOISINAGE)
    eps = _exiger_positif(eps, "eps")
    min_samples = _exiger_entier_borne(min_samples, "min_samples", X.shape[0], borne_min=1)
    save_path = _chemin(save_path, "sklearn_dbscan")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    est = DBSCAN(eps=eps, min_samples=min_samples).fit(Z)
    noyaux = Z[est.core_sample_indices_]
    labels_noyaux = est.labels_[est.core_sample_indices_]
    modele = ModeleGroupes("dbscan", scaler, None, eps=eps, noyaux=noyaux, labels_noyaux=labels_noyaux)
    ev = _evaluer(Z, est.labels_, seed)
    logger.info(_gt("rg7.msg.044"), ev["n_groupes"], ev["n_bruit"])
    return _finaliser("sklearn_dbscan", modele, X.shape[1], ev,
                      {"eps": eps, "min_samples": min_samples, "n_noyaux": int(len(noyaux)), "forme_courbe": rapport_forme},
                      save_path, on_epoch, epochs)


# ─────────────────────────────────────────────────────────────
# 3. Classification hiérarchique
# ─────────────────────────────────────────────────────────────

def train_sklearn_hierarchique(X=None, y=None, n_clusters=3, linkage="ward",
                               epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """Classification hiérarchique ascendante : fusionne progressivement les lignes les plus proches.

    y est ignoré. Contraintes : 2 ≤ n_clusters ≤ lignes − 1 ; linkage ∈ {ward, complete, average, single} ;
    jamais plus de 20 000 lignes (matrice de distances quadratique).
    """
    X = _preparer_donnees(X, seed, minimum=3)
    _exiger_pas_quadratique(X.shape[0], "AgglomerativeClustering")
    n_clusters = _exiger_entier_borne(n_clusters, "n_clusters", X.shape[0] - 1)
    linkage = _exiger_choix(linkage, "linkage", LIAISONS)
    save_path = _chemin(save_path, "sklearn_hierarchique")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    est = AgglomerativeClustering(n_clusters=n_clusters, linkage=linkage).fit(Z)
    groupes = np.unique(est.labels_)
    centres = np.vstack([Z[est.labels_ == g].mean(axis=0) for g in groupes])
    if linkage in ("single", "average"):
        # Groupes non sphériques (chaînage) : on rattache un nouveau point au groupe de son plus proche voisin d'entraînement.
        modele = ModeleGroupes("hierarchique", scaler, None, centres=centres, labels_groupes=[int(g) for g in groupes],
                               noyaux=Z, labels_noyaux=est.labels_)
    else:   # ward / complete : groupes compacts, le centre le plus proche suffit (et pèse très peu dans le .gy)
        modele = ModeleGroupes("hierarchique", scaler, None, centres=centres, labels_groupes=[int(g) for g in groupes])
    ev = _evaluer(Z, est.labels_, seed)
    logger.info(_gt("rg7.msg.045"), ev["n_groupes"], linkage)
    return _finaliser("sklearn_hierarchique", modele, X.shape[1], ev,
                      {"n_clusters": n_clusters, "linkage": linkage, "forme_courbe": rapport_forme},
                      save_path, on_epoch, epochs,
                      tenseurs={"centres": centres})


# ─────────────────────────────────────────────────────────────
# 4. Mélange gaussien
# ─────────────────────────────────────────────────────────────

def train_sklearn_gmm(X=None, y=None, n_components=3, covariance_type="full", n_init=3, max_iter=100,
                      epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """Mélange gaussien : chaque groupe est une « bosse » gaussienne ; les groupes peuvent être allongés.

    y est ignoré. Contraintes : 2 ≤ n_components ≤ lignes − 1 ; covariance_type ∈ {full, tied, diag, spherical} ;
    n_init, max_iter > 0.
    """
    X = _preparer_donnees(X, seed, minimum=3)
    n_components = _exiger_entier_borne(n_components, "n_components", X.shape[0] - 1)
    covariance_type = _exiger_choix(covariance_type, "covariance_type", COVARIANCES)
    n_init = _exiger_entier_borne(n_init, "n_init", MAX_ITERATIONS, borne_min=1)
    max_iter = _exiger_entier_borne(max_iter, "max_iter", MAX_ITERATIONS, borne_min=1)
    save_path = _chemin(save_path, "sklearn_gmm")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    est = GaussianMixture(n_components=n_components, covariance_type=covariance_type, n_init=n_init,
                          max_iter=max_iter, random_state=seed).fit(Z)
    labels = est.predict(Z)
    modele = ModeleGroupes("gmm", scaler, est, centres=est.means_, labels_groupes=list(range(n_components)))
    ev = _evaluer(Z, labels, seed)
    logger.info(_gt("rg7.msg.046"), ev["n_groupes"], est.bic(Z))
    return _finaliser("sklearn_gmm", modele, X.shape[1], ev,
                      {"n_components": n_components, "covariance_type": covariance_type,
                       "bic": float(est.bic(Z)), "converge": bool(est.converged_), "forme_courbe": rapport_forme},
                      save_path, on_epoch, epochs,
                      tenseurs={"centres": est.means_, "poids": est.weights_})


# ─────────────────────────────────────────────────────────────
# 5. ACP (analyse en composantes principales)
# ─────────────────────────────────────────────────────────────

def train_sklearn_acp(X=None, y=None, n_components=2, epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """ACP : résume les colonnes en `n_components` axes qui gardent le plus de variation.

    y est ignoré. Contraintes : 1 ≤ n_components ≤ min(lignes, colonnes). predict() renvoie les coordonnées de chaque ligne.
    """
    X = _preparer_donnees(X, seed, minimum=2)
    n_components = _exiger_entier_borne(n_components, "n_components", min(X.shape), borne_min=1)
    save_path = _chemin(save_path, "sklearn_acp")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    _exiger_variation(Z)
    est = PCA(n_components=n_components, random_state=seed).fit(Z)
    resume = _evaluer_projection(est, Z, variances=est.explained_variance_ratio_)
    modele = ModeleProjection("acp", scaler, est)
    logger.info(_gt("rg7.msg.047"), n_components, resume["information"] * 100)
    return _finaliser_resume("sklearn_acp", modele, X.shape[1], resume["information"] * 100.0, resume,
                             {"n_components": n_components, "forme_courbe": rapport_forme}, save_path, on_epoch, epochs,
                             tenseurs={"composantes": est.components_, "moyenne": est.mean_})


# ─────────────────────────────────────────────────────────────
# 6. ICA (analyse en composantes indépendantes)
# ─────────────────────────────────────────────────────────────

def train_sklearn_ica(X=None, y=None, n_components=2, algorithm="parallel", max_iter=500,
                      epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """ICA (FastICA) : retrouve des sources statistiquement indépendantes mélangées dans les colonnes.

    y est ignoré. Contraintes : 1 ≤ n_components ≤ min(lignes, colonnes) ; algorithm ∈ {parallel, deflation} ; max_iter > 0.
    """
    X = _preparer_donnees(X, seed, minimum=2)
    n_components = _exiger_entier_borne(n_components, "n_components", min(X.shape), borne_min=1)
    algorithm = _exiger_choix(algorithm, "algorithm", ALGOS_ICA)
    max_iter = _exiger_entier_borne(max_iter, "max_iter", MAX_ITERATIONS, borne_min=1)
    save_path = _chemin(save_path, "sklearn_ica")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    _exiger_variation(Z)
    est = FastICA(n_components=n_components, algorithm=algorithm, max_iter=max_iter,
                  whiten="unit-variance", random_state=seed).fit(Z)
    resume = _evaluer_projection(est, Z)
    modele = ModeleProjection("ica", scaler, est)
    logger.info(_gt("rg7.msg.048"), n_components, est.n_iter_)
    return _finaliser_resume("sklearn_ica", modele, X.shape[1], resume["information"] * 100.0, resume,
                             {"n_components": n_components, "algorithm": algorithm,
                              "n_iter": int(est.n_iter_), "converge": bool(est.n_iter_ < max_iter), "forme_courbe": rapport_forme},
                             save_path, on_epoch, epochs,
                             tenseurs={"composantes": est.components_})


# ─────────────────────────────────────────────────────────────
# 7. Isolation Forest
# ─────────────────────────────────────────────────────────────

def train_sklearn_isoforest(X=None, y=None, n_estimators=100, contamination=0.05,
                            epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """Isolation Forest : isole les lignes rares en les séparant au hasard ; celles isolées vite sont des anomalies.

    y est ignoré. Contraintes : n_estimators ≥ 1 ; 0 < contamination ≤ 0.5 (part d'anomalies attendue).
    """
    X = _preparer_donnees(X, seed, minimum=4)
    n_estimators = _exiger_entier_borne(n_estimators, "n_estimators", MAX_ARBRES, borne_min=1)
    contamination = _exiger_proportion(contamination, "contamination", 0.5)
    save_path = _chemin(save_path, "sklearn_isoforest")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    est = IsolationForest(n_estimators=n_estimators, contamination=contamination, random_state=seed).fit(Z)
    resume = _evaluer_anomalies(est, Z, contamination)
    logger.info(_gt("rg7.msg.049"), resume["n_anomalies"], resume["n_lignes"])
    return _finaliser_resume("sklearn_isoforest", ModeleAnomalies("isoforest", scaler, est), X.shape[1],
                             resume["taux_anomalies"] * 100.0, resume,
                             {"n_estimators": n_estimators, "contamination": contamination, "forme_courbe": rapport_forme}, save_path, on_epoch, epochs)


# ─────────────────────────────────────────────────────────────
# 8. One-Class SVM
# ─────────────────────────────────────────────────────────────

def train_sklearn_ocsvm(X=None, y=None, nu=0.05, kernel="rbf", gamma="scale",
                        epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """One-Class SVM : apprend la frontière qui entoure les lignes normales ; ce qui sort est une anomalie.

    y est ignoré. Contraintes : 0 < nu ≤ 1 (part maximale d'anomalies) ; kernel ∈ {rbf, linear, poly, sigmoid} ;
    gamma = scale, auto ou un nombre > 0 ; jamais plus de 20 000 lignes (calcul quadratique).
    """
    X = _preparer_donnees(X, seed, minimum=4)
    _exiger_pas_quadratique(X.shape[0], "OneClassSVM")
    nu = _exiger_proportion(nu, "nu", 1.0)
    kernel = _exiger_choix(kernel, "kernel", NOYAUX_OCSVM)
    gamma = _exiger_gamma(gamma)
    save_path = _chemin(save_path, "sklearn_ocsvm")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    est = OneClassSVM(nu=nu, kernel=kernel, gamma=gamma).fit(Z)
    resume = _evaluer_anomalies(est, Z, nu)
    logger.info(_gt("rg7.msg.050"), resume["n_anomalies"], resume["n_lignes"])
    return _finaliser_resume("sklearn_ocsvm", ModeleAnomalies("ocsvm", scaler, est), X.shape[1],
                             resume["taux_anomalies"] * 100.0, resume,
                             {"nu": nu, "kernel": kernel, "gamma": gamma, "forme_courbe": rapport_forme}, save_path, on_epoch, epochs)


# ─────────────────────────────────────────────────────────────
# 9. Local Outlier Factor
# ─────────────────────────────────────────────────────────────

def train_sklearn_lof(X=None, y=None, n_neighbors=20, contamination=0.05,
                      epochs=1, lr=0.01, seed=42, save_path=None, on_epoch=None, forme="auto"):
    """Local Outlier Factor : compare la densité autour d'une ligne à celle de ses voisins (mode « novelty » : il juge
    ensuite de nouvelles lignes). Les lignes bien moins denses que leurs voisins sont des anomalies.

    y est ignoré. Contraintes : 1 ≤ n_neighbors ≤ lignes − 1 ; 0 < contamination ≤ 0.5.
    """
    X = _preparer_donnees(X, seed, minimum=4)
    _exiger_pas_quadratique(X.shape[0], "LocalOutlierFactor", MAX_LIGNES_VOISINAGE)
    n_neighbors = _exiger_entier_borne(n_neighbors, "n_neighbors", X.shape[0] - 1, borne_min=1)
    contamination = _exiger_proportion(contamination, "contamination", 0.5)
    save_path = _chemin(save_path, "sklearn_lof")

    scaler, rapport_forme = preparer_colonnes(X, forme)
    Z = scaler.transform(X)
    est = LocalOutlierFactor(n_neighbors=n_neighbors, contamination=contamination, novelty=True).fit(Z)
    # Scores d'entraînement propres (negative_outlier_factor_) ; même règle de coupure que predict() : score < offset_.
    resume = _evaluer_anomalies(est, Z, contamination, anormaux=est.negative_outlier_factor_ < est.offset_)
    logger.info(_gt("rg7.msg.051"), resume["n_anomalies"], resume["n_lignes"])
    return _finaliser_resume("sklearn_lof", ModeleAnomalies("lof", scaler, est), X.shape[1],
                             resume["taux_anomalies"] * 100.0, resume,
                             {"n_neighbors": n_neighbors, "contamination": contamination, "forme_courbe": rapport_forme}, save_path, on_epoch, epochs)
