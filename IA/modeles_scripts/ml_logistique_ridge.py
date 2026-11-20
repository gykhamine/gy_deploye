# profil: {"court": "Logistique / Ridge (ML)", "role": "Classification (régression logistique) ou régression (Ridge) avec réglage automatique de la régularisation par validation croisée.", "donnees": "Colonnes numériques + une colonne cible (classes ou valeur continue).", "conseil": "Rapide et robuste sur petits jeux de données ; bon point de départ avant un modèle profond.", "iteratif": false}
# Exemple ML (scikit-learn) : régression logistique (classification) ou Ridge (régression),
# standardisation + choix automatique de la régularisation par validation croisée.
# Les poids sauvegardés sont uniquement des tableaux numpy : predire() n'a pas besoin de scikit-learn.
import numpy
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.model_selection import cross_val_score

NOM = "ml_logistique_ridge"


def _est_classification(y):
    return bool(numpy.all(y == numpy.round(y))) and len(numpy.unique(y)) <= max(20, len(y) // 10)


def entrainer(X, y, regularisation=1.0, auto_reglage=1, seed=42, on_epoch=None):
    # X : lignes x colonnes numériques ; y : indice de classe ou valeur continue.
    X = numpy.asarray(X, dtype=float)
    X = X.reshape(len(X), -1)
    y = numpy.asarray(y).reshape(-1)
    classification = _est_classification(y)
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = (X - mu) / sd

    def fabriquer(c):
        if classification:
            return LogisticRegression(C=c, max_iter=1000)
        return Ridge(alpha=1.0 / max(c, 1e-9))

    reglage = [0.01, 0.1, 1.0, 10.0, 100.0]
    cv = int(max(2, min(5, len(Z) // 10)))
    meilleur_c, meilleur_cv = float(regularisation), None
    if int(auto_reglage) and len(Z) >= 20:
        for c in reglage:
            s = float(numpy.mean(cross_val_score(fabriquer(c), Z, y, cv=cv)))
            if meilleur_cv is None or s > meilleur_cv:
                meilleur_c, meilleur_cv = c, s

    # Mise de côté de 20 % des lignes pour mesurer la qualité du modèle retenu.
    ordre = numpy.random.default_rng(seed).permutation(len(Z))
    n_test = max(1, len(Z) // 5)
    test, appr = ordre[:n_test], ordre[n_test:]
    modele = fabriquer(meilleur_c).fit(Z[appr], y[appr])
    if classification:
        score = float(numpy.mean(modele.predict(Z[test]) == y[test]))
        perte = 1.0 - score
    else:
        pred = modele.predict(Z[test])
        perte = float(numpy.mean((pred - y[test]) ** 2))
        score = max(0.0, 1.0 - perte / (float(numpy.var(y[test])) + 1e-9))
    if on_epoch:
        on_epoch(0, {"loss": perte, "accuracy": score})

    final = fabriquer(meilleur_c).fit(Z, y)   # modèle final : toutes les lignes
    if classification:
        poids = {"coef": numpy.asarray(final.coef_, dtype=float),
                 "intercept": numpy.asarray(final.intercept_, dtype=float),
                 "classes": numpy.asarray(final.classes_).astype(int)}
    else:
        poids = {"coef": numpy.asarray(final.coef_, dtype=float).reshape(1, -1),
                 "intercept": numpy.asarray([float(final.intercept_)])}
    poids["mu"], poids["sd"] = mu, sd
    return {"weights": poids,
            "config": {"classification": classification, "n_features": int(Z.shape[1]),
                       "regularisation": float(meilleur_c)},
            "accuracy": score * 100.0}   # pourcentage (0-100)


def predire(weights, config, X):
    X = numpy.asarray(X, dtype=float)
    X = X.reshape(len(X), -1)
    Z = (X - weights["mu"]) / weights["sd"]
    scores = Z @ weights["coef"].T + weights["intercept"]
    if not config["classification"]:
        return scores[:, 0]
    if scores.shape[1] == 1:   # binaire : un seul score
        return weights["classes"][(scores[:, 0] > 0).astype(int)].astype(int)
    return weights["classes"][numpy.argmax(scores, axis=1)].astype(int)
