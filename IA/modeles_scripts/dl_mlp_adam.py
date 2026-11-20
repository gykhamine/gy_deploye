# profil: {"court": "Perceptron multicouche (DL)", "role": "Réseau à 2 couches cachées entraîné par Adam avec arrêt anticipé ; classification ou régression.", "donnees": "Colonnes numériques + une colonne cible (classes ou valeur continue).", "conseil": "Ajustez hidden, lr et epochs ; utile quand la relation entre colonnes n'est pas linéaire.", "iteratif": true}
# Exemple DL (numpy pur) : perceptron multicouche à 2 couches cachées, rétropropagation à la main,
# optimiseur Adam, arrêt anticipé sur un jeu de validation. Classification (softmax) ou régression (MSE).
import numpy

NOM = "dl_mlp_adam"


def _est_classification(y):
    return bool(numpy.all(y == numpy.round(y))) and len(numpy.unique(y)) <= max(20, len(y) // 10)


def _avant(P, Z):
    # Propagation avant : retourne les activations de chaque couche (ReLU sur les couches cachées).
    h1 = numpy.maximum(0.0, Z @ P["W0"] + P["b0"])
    h2 = numpy.maximum(0.0, h1 @ P["W1"] + P["b1"])
    return h1, h2, h2 @ P["W2"] + P["b2"]


def entrainer(X, y, hidden=32, lr=0.005, epochs=200, batch=32, patience=25, seed=42, on_epoch=None):
    X = numpy.asarray(X, dtype=float)
    X = X.reshape(len(X), -1)
    y = numpy.asarray(y).reshape(-1)
    classification = _est_classification(y)
    rng = numpy.random.default_rng(seed)
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = (X - mu) / sd
    if classification:
        classes = numpy.unique(y).astype(int)
        cible = numpy.searchsorted(classes, y.astype(int))
        n_sortie = int(len(classes))
        cmu, csd = 0.0, 1.0
    else:
        classes = numpy.asarray([0])
        cmu, csd = float(y.mean()), float(y.std()) + 1e-9
        cible = ((y - cmu) / csd).reshape(-1, 1)
        n_sortie = 1

    ordre = rng.permutation(len(Z))
    n_val = max(1, len(Z) // 5)
    val, appr = ordre[:n_val], ordre[n_val:]
    d, h = int(Z.shape[1]), int(hidden)
    P = {"W0": rng.normal(0, numpy.sqrt(2.0 / d), (d, h)), "b0": numpy.zeros(h),
         "W1": rng.normal(0, numpy.sqrt(2.0 / h), (h, h)), "b1": numpy.zeros(h),
         "W2": rng.normal(0, numpy.sqrt(2.0 / h), (h, n_sortie)), "b2": numpy.zeros(n_sortie)}
    m = {k: numpy.zeros_like(v) for k, v in P.items()}   # Adam : moments d'ordre 1 et 2
    v2 = {k: numpy.zeros_like(v) for k, v in P.items()}
    pas, meilleur, meilleurs_poids, attente = 0, None, None, 0
    history = []

    def evaluer(idx):
        sortie = _avant(P, Z[idx])[2]
        if classification:
            e = numpy.exp(sortie - sortie.max(axis=1, keepdims=True))
            p = e / e.sum(axis=1, keepdims=True)
            perte = float(-numpy.mean(numpy.log(p[numpy.arange(len(idx)), cible[idx]] + 1e-12)))
            return perte, float(numpy.mean(numpy.argmax(sortie, axis=1) == cible[idx]))
        perte = float(numpy.mean((sortie - cible[idx]) ** 2))
        return perte, max(0.0, 1.0 - perte)

    for epoch in range(int(epochs)):
        melange = rng.permutation(appr)
        for debut in range(0, len(melange), int(batch)):
            idx = melange[debut:debut + int(batch)]
            h1, h2, sortie = _avant(P, Z[idx])
            if classification:
                e = numpy.exp(sortie - sortie.max(axis=1, keepdims=True))
                g = e / e.sum(axis=1, keepdims=True)
                g[numpy.arange(len(idx)), cible[idx]] -= 1.0
            else:
                g = 2.0 * (sortie - cible[idx])
            g = g / len(idx)
            dh2 = (g @ P["W2"].T) * (h2 > 0)
            dh1 = (dh2 @ P["W1"].T) * (h1 > 0)
            grads = {"W2": h2.T @ g, "b2": g.sum(axis=0), "W1": h1.T @ dh2, "b1": dh2.sum(axis=0),
                     "W0": Z[idx].T @ dh1, "b0": dh1.sum(axis=0)}
            pas += 1
            for k in P:   # Adam
                m[k] = 0.9 * m[k] + 0.1 * grads[k]
                v2[k] = 0.999 * v2[k] + 0.001 * grads[k] ** 2
                P[k] = P[k] - lr * (m[k] / (1 - 0.9 ** pas)) / (numpy.sqrt(v2[k] / (1 - 0.999 ** pas)) + 1e-8)
        perte_val, score_val = evaluer(val)
        history.append(perte_val)
        if meilleur is None or perte_val < meilleur - 1e-6:
            meilleur, attente = perte_val, 0
            meilleurs_poids = {k: v.copy() for k, v in P.items()}
        else:
            attente += 1
        if on_epoch and on_epoch(epoch, {"loss": perte_val, "accuracy": score_val}):
            break   # arrêt demandé par l'interface
        if attente >= int(patience):
            break   # arrêt anticipé : la validation ne s'améliore plus
    P = meilleurs_poids
    _, score = evaluer(val)
    poids = dict(P)
    poids["mu"], poids["sd"], poids["classes"] = mu, sd, classes
    poids["cible"] = numpy.asarray([cmu, csd])
    return {"weights": poids,
            "config": {"classification": classification, "n_features": d, "hidden": h,
                       "epochs_effectuees": len(history)},
            "accuracy": score * 100.0}   # pourcentage (0-100)


def predire(weights, config, X):
    X = numpy.asarray(X, dtype=float)
    X = X.reshape(len(X), -1)
    sortie = _avant(weights, (X - weights["mu"]) / weights["sd"])[2]
    if config["classification"]:
        return weights["classes"][numpy.argmax(sortie, axis=1)].astype(int)
    return sortie[:, 0] * weights["cible"][1] + weights["cible"][0]
