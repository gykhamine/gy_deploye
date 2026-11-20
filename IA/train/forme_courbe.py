"""
IA/train/forme_courbe.py — Forme de courbe automatique pour les modèles scikit-learn supervisés.

Un modèle linéaire (Ridge, Lasso, régression logistique) ne sait tracer qu'une droite ; Naive Bayes est presque dans le même cas.
Ce module leur présente les colonnes sous trois formes, et choisit seul la meilleure :

  - « droite »       : colonnes telles quelles ;
  - « convexe »      : colonnes + leur carré (centré, réduit) ;
  - « sinusoidale »  : colonnes + sinus et cosinus, avec la fréquence choisie dans une petite grille (1, 2, 3, 5 cycles sur la plage
                       de la colonne).

Les transformations s'appliquent colonne par colonne, sans produits croisés (pas d'explosion du nombre de colonnes).

Le choix se fait par validation croisée sur le jeu d'ENTRAÎNEMENT seulement (jamais sur le test : le score affiché reste honnête),
avec une règle de parcimonie : on garde la forme la plus simple dont le score est à moins d'une erreur-type du meilleur.

Compatibilité : le résultat est un `Pipeline` sklearn ordinaire, sérialisé dans le .gy comme avant. L'inférence ne change pas
(`_predict_sklearn_generic` appelle `predict` / `predict_proba`). Un ancien .gy, sans cette étape, se recharge et prédit comme avant.
"""
import logging
import time
import unicodedata

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin, clone
from sklearn.model_selection import KFold, StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)

FORMES = ("droite", "convexe", "sinusoidale")
CYCLES_GRILLE = (1, 2, 3, 5)
NOM_ETAPE = "forme"

# Garde-fous
MIN_LIGNES = 30            # en dessous : pas assez de lignes pour comparer des formes
MAX_COLONNES = 20          # au-dessus : trop de colonnes, la transformation gonflerait le modèle
N_PLIS = 5
SCORE_SUFFISANT = 0.98     # la droite est déjà quasi parfaite : rien à gagner, on n'essaie pas les autres formes
BUDGET_S_DEFAUT = 45.0     # plafond de temps de la recherche (validation croisée comprise)
BUDGET_S_ARBRES = 20.0     # plus court pour Random Forest / Gradient Boosting (coût ×3 à ×5 sinon)


def normaliser_forme(forme):
    """'auto' (défaut) ou l'une des FORMES. Accents et casse ignorés ('sinusoïdale' accepté)."""
    if forme is None:
        return "auto"
    texte = unicodedata.normalize("NFKD", str(forme)).encode("ascii", "ignore").decode("ascii").strip().lower()
    if texte in ("", "auto", "automatique"):
        return "auto"
    if texte in FORMES:
        return texte
    raise ValueError(_gtf("IA/train/forme_courbe.py:56:22", v1=(forme), v2=(', '.join(FORMES))))


class FormeCourbe(BaseEstimator, TransformerMixin):
    """Transformateur compatible sklearn : présente les colonnes en droite, convexe ou sinusoïdale.

    Les colonnes constantes ou non finies sont laissées telles quelles (aucune colonne ajoutée pour elles).
    """

    def __init__(self, forme="droite", cycles=1):
        self.forme = forme
        self.cycles = cycles

    def fit(self, X, y=None):
        forme = normaliser_forme(self.forme)
        self.forme_ = "droite" if forme == "auto" else forme
        self.cycles_ = max(1, int(self.cycles))
        X = np.asarray(X, dtype=np.float64)
        if X.ndim != 2:
            raise ValueError(_gt("rg7.msg.005"))
        n_col = X.shape[1]
        self.n_features_in_ = n_col
        finies = np.isfinite(X).all(axis=0)
        propre = np.where(np.isfinite(X), X, 0.0)
        ecart = propre.std(axis=0)
        self.utiles_ = np.flatnonzero(finies & (ecart > 1e-12))
        self.centre_ = propre.mean(axis=0)
        self.echelle_ = np.where(ecart > 1e-12, ecart, 1.0)
        mini = propre.min(axis=0)
        etendue = propre.max(axis=0) - mini
        self.mini_ = mini
        self.etendue_ = np.where(etendue > 1e-12, etendue, 1.0)
        # colonne d'origine de chaque colonne de sortie (sert à replier les importances sur les colonnes d'origine)
        sources = [np.arange(n_col)]
        if self.forme_ == "convexe":
            sources.append(self.utiles_)
        elif self.forme_ == "sinusoidale":
            sources.append(self.utiles_)
            sources.append(self.utiles_)
        self.colonnes_source_ = np.concatenate(sources).astype(int)
        return self

    def transform(self, X):
        X = np.asarray(X, dtype=np.float64)
        if X.ndim != 2 or X.shape[1] != self.n_features_in_:
            raise ValueError(_gtf("rg7.msg.006", v1=self.n_features_in_, v2=X.shape))
        if self.forme_ == "droite" or len(self.utiles_) == 0:
            return X
        u = self.utiles_
        if self.forme_ == "convexe":
            z = (X[:, u] - self.centre_[u]) / self.echelle_[u]
            return np.hstack([X, z ** 2])
        t = (X[:, u] - self.mini_[u]) / self.etendue_[u]
        angle = 2.0 * np.pi * self.cycles_ * t
        return np.hstack([X, np.sin(angle), np.cos(angle)])


def replier_sur_colonnes(modele, valeurs):
    """Ramène un vecteur défini sur les colonnes TRANSFORMÉES (importances, coefficients) aux colonnes d'ORIGINE (somme par colonne).

    `modele` : le Pipeline entraîné. Sans étape FormeCourbe (ou en « droite »), `valeurs` est rendu tel quel.
    """
    valeurs = np.asarray(valeurs, dtype=np.float64)
    etape = getattr(modele, "named_steps", {}).get(NOM_ETAPE)
    if etape is None or valeurs.ndim != 1 or valeurs.shape[0] != len(etape.colonnes_source_):
        return valeurs
    return np.bincount(etape.colonnes_source_, weights=valeurs, minlength=etape.n_features_in_)


def _construire(etapes, forme, cycles):
    """Pipeline neuf : [forme, *étapes]. Les étapes sont clonées (aucun état partagé entre deux essais)."""
    corps = [(nom, clone(est)) for nom, est in etapes]
    return Pipeline([(NOM_ETAPE, FormeCourbe(forme=forme, cycles=cycles))] + corps)


def _decoupage(y, classification, seed):
    if classification:
        _, effectifs = np.unique(y, return_counts=True)
        plis = int(min(N_PLIS, effectifs.min()))
        if plis >= 2:
            return StratifiedKFold(n_splits=plis, shuffle=True, random_state=seed)
    return KFold(n_splits=N_PLIS, shuffle=True, random_state=seed)


def _evaluer(etapes, forme, cycles, X, y, decoupage, scoring):
    """(moyenne, erreur-type) de la validation croisée ; (nan, nan) si l'essai échoue."""
    try:
        scores = cross_val_score(_construire(etapes, forme, cycles), X, y, cv=decoupage, scoring=scoring)
    except Exception as e:   # un essai qui plante n'empêche pas les autres
        logger.warning(_gt("rg7.msg.007"), forme, cycles, type(e).__name__, e)
        return float("nan"), float("nan")
    scores = np.asarray(scores, dtype=np.float64)
    if not np.isfinite(scores).all():
        return float("nan"), float("nan")
    return float(scores.mean()), float(scores.std(ddof=1) / np.sqrt(len(scores))) if len(scores) > 1 else 0.0


def _rapport(demandee, choisie, cycles, scores, raison, tronque=False):
    # JSON strict dans l'en-tête .gy : un score impossible (nan) devient null
    propres = {k: {m: (v if np.isfinite(v) else None) for m, v in sc.items()} for k, sc in scores.items()}
    return {"forme_demandee": demandee, "forme_choisie": choisie, "cycles": cycles if choisie == "sinusoidale" else None,
            "scores": propres, "raison": raison, "recherche_tronquee": tronque}


def choisir_forme(modele, X, y, classification, forme="auto", seed=42, budget_s=BUDGET_S_DEFAUT):
    """Choisit la forme de courbe, entraîne et rend (pipeline_entraîné, rapport).

    Args:
        modele:         estimateur sklearn NON entraîné, ou liste d'étapes [(nom, estimateur), ...] (ex. scaler puis algorithme).
        X, y:           jeu d'ENTRAÎNEMENT (le test n'est jamais vu ici).
        classification: True → score = précision ; False → R².
        forme:          'auto' (défaut), 'droite', 'convexe' ou 'sinusoidale'.
        budget_s:       plafond de temps de la recherche ; au-delà on s'arrête avec les formes déjà essayées.

    Le rapport : {forme_demandee, forme_choisie, cycles, scores: {étiquette: {moyenne, ecart_type}}, raison, recherche_tronquee}.
    """
    etapes = list(modele) if isinstance(modele, (list, tuple)) else [("modele", modele)]
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y).ravel()
    demandee = normaliser_forme(forme)
    scores = {}

    def _final(choisie, cycles, raison, tronque=False):
        pipe = _construire(etapes, choisie, cycles).fit(X, y)
        rapport = _rapport(demandee, choisie, cycles, scores, raison, tronque)
        rapport["n_colonnes_apres"] = int(pipe.named_steps[NOM_ETAPE].colonnes_source_.shape[0])
        return pipe, rapport

    if demandee != "auto":
        return _final(demandee, CYCLES_GRILLE[0] if demandee == "sinusoidale" else 1, _gt("rg7.msg.008"))
    n, p = X.shape
    if n < MIN_LIGNES:
        return _final("droite", 1, _gtf("rg7.msg.009", v1=MIN_LIGNES))
    if p > MAX_COLONNES:
        return _final("droite", 1, _gtf("rg7.msg.010", v1=MAX_COLONNES))

    scoring = "accuracy" if classification else "r2"
    decoupage = _decoupage(y, classification, seed)
    debut = time.perf_counter()

    moy, se = _evaluer(etapes, "droite", 1, X, y, decoupage, scoring)
    duree_essai = time.perf_counter() - debut
    scores["droite"] = {"moyenne": moy, "ecart_type": se}
    if not np.isfinite(moy):
        return _final("droite", 1, _gt("rg7.msg.011"))
    if moy >= SCORE_SUFFISANT:
        return _final("droite", 1, _gt("rg7.msg.012"))

    tronque = False
    candidats = [("convexe", 1)] + [("sinusoidale", c) for c in CYCLES_GRILLE]
    for nom, cycles in candidats:
        if time.perf_counter() - debut + duree_essai > budget_s:
            tronque = True
            logger.info(_gt("rg7.msg.013"), budget_s, nom)
            break
        etiquette = nom if nom == "convexe" else f"sinusoidale_{cycles}"
        m, s = _evaluer(etapes, nom, cycles, X, y, decoupage, scoring)
        scores[etiquette] = {"moyenne": m, "ecart_type": s}

    # meilleur essai de chaque famille (pour la sinusoïdale : la meilleure fréquence)
    familles = {}
    for etiquette, sc in scores.items():
        if not np.isfinite(sc["moyenne"]):
            continue
        nom = "sinusoidale" if etiquette.startswith("sinusoidale") else etiquette
        cycles = int(etiquette.split("_")[1]) if nom == "sinusoidale" else 1
        if nom not in familles or sc["moyenne"] > familles[nom][0]:
            familles[nom] = (sc["moyenne"], sc["ecart_type"], cycles)
    meilleur_nom = max(familles, key=lambda k: familles[k][0])
    seuil = familles[meilleur_nom][0] - familles[meilleur_nom][1]        # règle de parcimonie : à une erreur-type du meilleur
    choisie = next(nom for nom in FORMES if nom in familles and familles[nom][0] >= seuil)
    cycles = familles[choisie][2]
    raison = _gt("rg7.msg.014") if choisie != meilleur_nom else "meilleur score"
    logger.info("Curve shape selected: %s%s (%s)", choisie, f" ×{cycles}" if choisie == "sinusoidale" else "", raison)
    return _final(choisie, cycles, raison, tronque)


def preparer_colonnes(X, forme="auto"):
    """Préparateur de colonnes des modèles SANS cible (groupes, projection, anomalies) : rend (préparateur_entraîné, rapport).

    Le préparateur n'expose que `transform` (c'est tout ce que les enveloppeurs de sklearn_nonsup.py appellent) : standardisation
    seule pour « droite » (comportement d'avant, inchangé), ou FormeCourbe puis standardisation pour « convexe » / « sinusoidale ».

    Sans colonne cible, aucun score n'est comparable d'une forme à l'autre (chaque forme change l'espace où les distances, les densités et
    les variances se mesurent : la silhouette ou l'erreur de reconstruction y favoriseraient mécaniquement une forme). « auto » garde
    donc « droite » ; une autre forme est essayée seulement si on l'impose. Mêmes garde-fous que pour les modèles supervisés
    (moins de MIN_LIGNES lignes ou plus de MAX_COLONNES colonnes : droite).
    """
    X = np.asarray(X, dtype=np.float64)
    demandee = normaliser_forme(forme)
    if demandee in ("auto", "droite"):
        raison = (_gt("rg7.msg.015") if demandee == "auto"
                  else _gt("rg7.msg.008"))
        return StandardScaler().fit(X), _rapport(demandee, "droite", 1, {}, raison)
    if X.shape[0] < MIN_LIGNES or X.shape[1] > MAX_COLONNES:
        raison = _gtf("rg7.msg.016", v1=MIN_LIGNES, v2=MAX_COLONNES)
        return StandardScaler().fit(X), _rapport(demandee, "droite", 1, {}, raison)
    prep = Pipeline([(NOM_ETAPE, FormeCourbe(forme=demandee, cycles=1)), ("scaler", StandardScaler())]).fit(X)
    rapport = _rapport(demandee, demandee, 1, {}, _gt("rg7.msg.008"))
    rapport["n_colonnes_apres"] = int(prep.named_steps[NOM_ETAPE].colonnes_source_.shape[0])
    return prep, rapport
