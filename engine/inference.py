"""
Moteur d'inference : les prompts, rôles, contraintes et paramètres sont
lus depuis ConfigurationIA (BD, un enregistrement par entreprise) plutôt
qu'en dur dans le code. Les valeurs codées en dur ci-dessous ne servent
que de fallback quand aucune configuration n'existe encore en base.
"""
from engine.i18n import t, tf, tl
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
import re
import requests


CLES_SCRIPT_DANS_LORDRE = (
    "titre", "pourquoi", "comment", "table_statistiques", "figures",
    "explication", "analyse_predictive", "kpi", "conclusion", "recommandations",
)


def _exemple_respecte_structure(texte: str, cles_dans_lordre) -> bool:
    """Verifie heuristiquement que le texte d'un script d'exemple mentionne
    bien, sous forme de cle de dict quotee, chacune des cles attendues, dans
    le bon ordre d'apparition. Protege contre un script_exemple configure en
    BD qui aurait derive vers une autre structure (ex: celle du mode skill)."""
    positions = []
    for cle in cles_dans_lordre:
        pos = texte.find(f'"{cle}"')
        if pos == -1:
            pos = texte.find(f"'{cle}'")
        if pos == -1:
            return False
        positions.append(pos)
    return positions == sorted(positions)


# ── Valeurs par défaut (fallback si ConfigurationIA inexistante) ──────────────

DEFAULT_SYSTEM_PROMPT = (
    tl("engine/inference.py:g:36:5")
)

DEFAULT_ROLES = {
    "analysis_script": (
        tl("engine/inference.py:g:41:9")
    ),
    "skill_script": (
        tl("engine/inference.py:g:45:9")
    ),
    "report_summary": (
        tl("engine/inference.py:g:52:9")
    ),
    "document": (
        tl("engine/inference.py:g:56:9")
    ),
    "formule": (
        tl("engine/inference.py:g:62:9")
    ),
    "graphique_bi": (
        tl("engine/inference.py:g:69:9")
    ),
    "couche_ontologie": (
        tl("engine/inference.py:g:76:9")
    ),
    "modele_ia": (
        tl("engine/inference.py:g:85:9")
    ),
    "tache": (
        tl("engine/inference.py:g:92:9")
    ),
    "other": tl("engine/inference.py:g:99:14"),
}

DEFAULT_REGLE_DOCUMENT = (
    tl("engine/inference.py:g:103:5")
)

DEFAULT_REGLE_FORMULE = (
    tl("engine/inference.py:g:178:5")
)

DEFAULT_CONTRAINTES_FORMULE = (
    tl("engine/inference.py:g:193:5")
)

DEFAULT_REGLE_GRAPHIQUE_BI = (
    tl("engine/inference.py:g:207:5")
)

DEFAULT_CONTRAINTES_GRAPHIQUE_BI = (
    tl("engine/inference.py:g:225:5")
)

DEFAULT_REGLE_COUCHE_ONTOLOGIE = (
    tl("engine/inference.py:g:242:5")
)

DEFAULT_CONTRAINTES_COUCHE_ONTOLOGIE = (
    tl("engine/inference.py:g:261:5")
)

DEFAULT_CONTRAINTES_DOCUMENT = (
    tl("engine/inference.py:g:277:5")
)


EXEMPLE_SCRIPT_PYTHON = tl("engine/inference.py:g:294:25")

EXEMPLE_SCRIPT_DOCUMENT = tl("engine/inference.py:g:428:27")

EXEMPLE_FORMULE_COLONNE = (
    "def formule(cible):\n"
    "    import pandas as pd\n"
    "    serie = pd.to_numeric(pd.Series(cible), errors='coerce').dropna()\n"
    "    if serie.empty:\n"
    "        return None\n"
    "    return float(serie.mean())\n"
)

EXEMPLE_FORMULE_TABLE = (
    "def formule(cible):\n"
    "    import pandas as pd\n"
    "    df = pd.DataFrame(cible)\n"
    "    colonnes_num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]\n"
    "    if not colonnes_num:\n"
    "        return None\n"
    "    return {c: float(pd.to_numeric(df[c], errors='coerce').sum()) for c in colonnes_num}\n"
)

# ── Canal GRAPHIQUE_BI (graphiques personnalisés des tableaux de bord BI,
# voir graphiques_bi.py / models.GraphiqueBI). Même principe que les
# constantes EXEMPLE_FORMULE_* ci-dessus : `def dessiner(dataframe, colonnes)`
# doit toujours retourner une figure matplotlib (jamais plt.show()). ──

EXEMPLE_GRAPHIQUE_BI_COLONNE = (
    tl("engine/inference.py:g:552:5")
)

EXEMPLE_GRAPHIQUE_BI_MULTIPLES = (
    "def dessiner(dataframe, colonnes):\n"
    "    import matplotlib\n"
    "    matplotlib.use(\"Agg\")\n"
    "    import matplotlib.pyplot as plt\n"
    "    import pandas as pd\n"
    "    if len(colonnes) < 2:\n"
    "        fig, ax = plt.subplots(figsize=(6, 4))\n"
    "        ax.text(0.5, 0.5, \"Choisir une colonne de catégories puis une colonne de valeurs\", ha=\"center\")\n"
    "        ax.axis(\"off\")\n"
    "        return fig\n"
    "    cat_col, val_col = colonnes[0], colonnes[1]\n"
    "    df = dataframe[[cat_col, val_col]].copy()\n"
    "    df[val_col] = pd.to_numeric(df[val_col], errors=\"coerce\")\n"
    "    df = df.dropna()\n"
    "    fig, ax = plt.subplots(figsize=(6, 4))\n"
    "    ax.bar(df[cat_col].astype(str), df[val_col], color=\"#4C72B0\")\n"
    "    ax.set_xlabel(cat_col); ax.set_ylabel(val_col)\n"
    "    ax.tick_params(axis=\"x\", rotation=45)\n"
    "    fig.tight_layout()\n"
    "    return fig\n"
)

EXEMPLE_COUCHE_ONTOLOGIE = (
    tl("engine/inference.py:g:585:5")
)

DEFAULT_REGLE_SCRIPT = (
    tl("engine/inference.py:g:602:5")
)

DEFAULT_CONTRAINTES = (
    tl("engine/inference.py:g:631:5")
)


DEFAULT_REGLE_MODELE_IA = (
    tl("engine/inference.py:g:654:5")
)

DEFAULT_CONTRAINTES_MODELE_IA = (
    tl("engine/inference.py:g:670:5")
)

# Exemples de reference du canal modele_ia : les deux moteurs reels du projet (scikit-learn et Flax/JAX).
# Dans les deux, predire() reconstruit le modele a partir des SEULS poids et config, et entrainer() mesure sa qualite en
# appelant predire() : l'inference est donc le prolongement exact de l'entrainement, jamais un code a part.
EXEMPLE_MODELE_IA = """import numpy
from sklearn.linear_model import LogisticRegression, Ridge

NOM = "sklearn_lineaire"


def _ajuster(Z, y, classification, C):
    # Entraine l'estimateur scikit-learn et en extrait ce qui permettra de le reconstruire : ses attributs appris.
    if classification:
        m = LogisticRegression(C=C, max_iter=500).fit(Z, y.astype(int))
        return {"coef": m.coef_, "intercept": m.intercept_, "classes": m.classes_.astype(float)}
    m = Ridge(alpha=1.0 / C).fit(Z, y)
    return {"coef": numpy.atleast_2d(m.coef_), "intercept": numpy.atleast_1d(m.intercept_).astype(float)}


def _construire(weights, config):
    # Reconstruit l'estimateur ENTRAINE a partir des seuls poids et de la config (rien d'autre n'est relu a l'inference).
    if config["classification"]:
        m = LogisticRegression(C=config["C"])
        m.classes_ = weights["classes"].astype(int)
        m.coef_, m.intercept_ = weights["coef"], weights["intercept"]
    else:
        m = Ridge(alpha=1.0 / config["C"])
        m.coef_, m.intercept_ = weights["coef"].reshape(-1), float(weights["intercept"][0])
    return m


def predire(weights, config, X):
    X = numpy.asarray(X, dtype=float).reshape(len(X), -1)
    sortie = _construire(weights, config).predict((X - weights["mu"]) / weights["sd"])
    return sortie.astype(int) if config["classification"] else sortie


def entrainer(X, y, C=1.0, seed=42, on_epoch=None):
    # X : lignes x colonnes numeriques ; y : une valeur par ligne (indice de classe ou nombre, selon la cible choisie).
    X = numpy.asarray(X, dtype=float).reshape(len(X), -1)
    y = numpy.asarray(y).reshape(-1)
    classification = bool(numpy.all(y == numpy.round(y)))
    C = float(max(C, 1e-9))
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = (X - mu) / sd
    # 20 % des lignes sont mises de cote pour mesurer la qualite du modele.
    ordre = numpy.random.default_rng(seed).permutation(len(Z))
    n_test = max(1, len(Z) // 5)
    test, appr = ordre[:n_test], ordre[n_test:]
    config = {"C": C, "classification": classification, "n_features": int(Z.shape[1])}
    # La qualite est mesuree avec predire() : exactement le chemin qu'empruntera l'inference sur le modele enregistre.
    poids = {**_ajuster(Z[appr], y[appr], classification, C), "mu": mu, "sd": sd}
    pred = predire(poids, config, X[test])
    if classification:
        score = float(numpy.mean(pred == y[test]))
        perte = 1.0 - score
    else:
        perte = float(numpy.mean((pred - y[test]) ** 2))
        score = max(0.0, 1.0 - perte / (float(numpy.var(y[test])) + 1e-9))
    if on_epoch:
        on_epoch(0, {"loss": perte, "accuracy": score})
    # Le modele final est ajuste sur toutes les lignes.
    poids = {**_ajuster(Z, y, classification, C), "mu": mu, "sd": sd}
    return {"weights": poids, "config": config, "accuracy": score * 100.0}   # pourcentage (0-100) : le Trainer le ramene a une fraction
"""

EXEMPLE_MODELE_IA_FLAX = """import numpy
import jax
import jax.numpy as jnp
import optax
import flax.linen as nn

NOM = "flax_perceptron"


def _reseau(config):
    # Meme architecture a l'entrainement et a l'inference : la config la decrit (pas de class dans le bac a sable).
    couches = []
    for h in config["hidden_sizes"]:
        couches += [nn.Dense(int(h)), nn.relu]
    couches.append(nn.Dense(int(config["n_out"])))
    return nn.Sequential(couches)


def _squelette(config):
    # Structure (arbre) des parametres du reseau, avec des valeurs sans importance : seules les cles comptent.
    modele = _reseau(config)
    return modele, modele.init(jax.random.PRNGKey(0), jnp.zeros((1, int(config["n_features"])), dtype=jnp.float32))


def _en_poids(params):
    # Parametres Flax -> poids numpy a plat (p0, p1, ...), stockables dans le .gy.
    feuilles = jax.tree_util.tree_flatten(params)[0]
    return {"p" + str(i): numpy.asarray(f, dtype=float) for i, f in enumerate(feuilles)}


def _en_params(weights, config):
    # Poids numpy -> parametres Flax : meme arbre que a l'entrainement, rempli avec les poids appris.
    modele, squelette = _squelette(config)
    feuilles, arbre = jax.tree_util.tree_flatten(squelette)
    neufs = [jnp.asarray(weights["p" + str(i)], dtype=jnp.float32) for i in range(len(feuilles))]
    return modele, jax.tree_util.tree_unflatten(arbre, neufs)


def _qualite(pred, y, classification):
    # Fraction de 0 a 1 : exactitude (classes) ou part de variance expliquee (valeurs continues).
    if classification:
        return float(numpy.mean(pred == y))
    return max(0.0, 1.0 - float(numpy.mean((pred - y) ** 2)) / (float(numpy.var(y)) + 1e-9))


def predire(weights, config, X):
    X = numpy.asarray(X, dtype=float).reshape(len(X), -1)
    modele, params = _en_params(weights, config)
    Z = jnp.asarray((X - weights["mu"]) / weights["sd"], dtype=jnp.float32)
    sortie = numpy.asarray(modele.apply(params, Z), dtype=float)
    if config["classification"]:
        return sortie.argmax(axis=1).astype(int)
    return sortie[:, 0] * float(weights["ys"][0]) + float(weights["ym"][0])


def entrainer(X, y, lr=0.01, epochs=200, hidden=16, seed=42, on_epoch=None):
    # X : lignes x colonnes numeriques ; y : une valeur par ligne (indice de classe ou nombre, selon la cible choisie).
    X = numpy.asarray(X, dtype=float).reshape(len(X), -1)
    y = numpy.asarray(y, dtype=float).reshape(-1)
    classification = bool(numpy.all(y == numpy.round(y)))
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    ym, ys = (0.0, 1.0) if classification else (float(y.mean()), float(y.std()) + 1e-9)
    cible = y.astype(int) if classification else (y - ym) / ys
    # 20 % des lignes sont mises de cote pour mesurer la qualite du modele.
    ordre = numpy.random.default_rng(seed).permutation(len(X))
    n_test = max(1, len(X) // 5)
    test, appr = ordre[:n_test], ordre[n_test:]
    config = {"hidden_sizes": [int(hidden)], "n_out": int(y.max()) + 1 if classification else 1,
              "n_features": int(X.shape[1]), "classification": classification}
    base = {"mu": mu, "sd": sd, "ym": numpy.array([ym]), "ys": numpy.array([ys])}
    modele, params = _squelette(config)
    Za = jnp.asarray((X[appr] - mu) / sd, dtype=jnp.float32)
    ca = jnp.asarray(cible[appr])

    def perte(p):
        sortie = modele.apply(p, Za)
        if classification:
            return optax.softmax_cross_entropy_with_integer_labels(sortie, ca).mean()
        return ((sortie[:, 0] - ca) ** 2).mean()

    pas = jax.jit(jax.value_and_grad(perte))
    optimiseur = optax.adam(float(lr))
    etat = optimiseur.init(params)
    for epoch in range(int(epochs)):
        valeur, grads = pas(params)
        maj, etat = optimiseur.update(grads, etat, params)
        params = optax.apply_updates(params, maj)
        # La qualite est mesuree avec predire() : exactement le chemin qu'empruntera l'inference sur le modele enregistre.
        if on_epoch and on_epoch(epoch, {"loss": float(valeur), "accuracy": _qualite(predire({**base, **_en_poids(params)}, config, X[test]), y[test], classification)}):
            break
    poids = {**base, **_en_poids(params)}
    score = _qualite(predire(poids, config, X[test]), y[test], classification)
    return {"weights": poids, "config": config, "accuracy": score * 100.0}   # pourcentage (0-100) : le Trainer le ramene a une fraction
"""


# Anciens exemples par defaut encore enregistres en configuration (ils ne correspondaient pas au moteur reel) :
# champ -> (marqueur propre a l'ancien exemple, marqueur propre au nouveau). Un exemple modifie par l'administrateur est garde.
_EXEMPLES_OBSOLETES = {
    "script_exemple": ('"xlabel": col', None),
    "script_exemple_document": (_gtfr("engine/inference.py:345:33"), None),
    "script_exemple_tache": ("Enregistrement.objects.get(id=enr_id)", None),
    "script_exemple_graphique_bi_multiples": ("cat_col, val_col = colonnes[0], colonnes[1]", "len(colonnes) < 2"),
}


def exemple_a_jour(champ, texte, defaut):
    """Exemple enregistre pour `champ`, ou `defaut` s'il est vide ou s'il s'agit de l'ancien exemple par defaut."""
    texte = (texte or "").strip()
    ancien, nouveau = _EXEMPLES_OBSOLETES.get(champ, (None, None))
    if not texte or (ancien and ancien in texte and not (nouveau and nouveau in texte)):
        return defaut
    return texte


def exemple_modele_ia_a_jour(texte):
    """Exemple enregistre en configuration. Les anciens exemples par defaut (regression logistique binaire, puis plus proches
    voisins en numpy pur) ne correspondaient pas aux moteurs reels (scikit-learn, Flax) : ils sont remplaces par l'exemple
    actuel. Un exemple modifie par l'administrateur est garde."""
    texte = (texte or "").strip()
    obsolete = "regression_logistique_np" in texte or "plus_proches_voisins_np" in texte
    return texte if ("def entrainer(" in texte and not obsolete) else EXEMPLE_MODELE_IA


def exemples_modele_ia_pour_prompt(texte):
    """Exemples de reference places dans le prompt du generateur : l'exemple personnalise de l'administrateur s'il existe,
    sinon les deux exemples par defaut (scikit-learn et Flax), pour que l'IA suive le moteur demande."""
    personnalise = exemple_modele_ia_a_jour(texte)
    if personnalise != EXEMPLE_MODELE_IA:
        return personnalise
    return ("EXEMPLE 1 - scikit-learn (modeles ML classiques) :\n```python\n" + EXEMPLE_MODELE_IA.strip() + "\n```\n\n"
            "EXEMPLE 2 - Flax + optax (deep learning) :\n```python\n" + EXEMPLE_MODELE_IA_FLAX.strip() + "\n```")


def regle_modele_ia_a_jour(texte):
    """Meme principe pour la regle : l'ancienne regle annoncait « accuracy: float » sans preciser l'echelle, puis ne parlait
    que de numpy sans dire comment predire() retrouve le modele entraine avec scikit-learn ou Flax."""
    texte = (texte or "").strip()
    ancienne = '"accuracy": float}. Chaque' in texte or ("{nom: numpy.ndarray}" in texte and "flax" not in texte.lower())
    return texte if (texte and not ancienne) else DEFAULT_REGLE_MODELE_IA


def contraintes_modele_ia_a_jour(texte):
    """Contraintes du canal modele_ia : l'ancien defaut n'autorisait que numpy et son point sur la reproductibilite ignorait Flax."""
    texte = (texte or "").strip()
    ancienne = "numpy.random.default_rng(seed)" in texte and "flax" not in texte.lower()
    return texte if (texte and not ancienne) else DEFAULT_CONTRAINTES_MODELE_IA


DEFAULT_REGLE_TACHE = (
    tl("engine/inference.py:g:710:5")
)

DEFAULT_CONTRAINTES_TACHE = (
    tl("engine/inference.py:g:724:5")
)

EXEMPLE_TACHE = tl("engine/inference.py:g:736:17")


def _charger_config(entreprise=None):
    """Retourne le ConfigurationIA de l'entreprise, ou None si inexistant/erreur."""
    if entreprise is None:
        return None
    try:
        from .models import ConfigurationIA
        return ConfigurationIA.objects.get(entreprise=entreprise)
    except Exception:
        return None


class MoteurInference:
    def __init__(self, host=None, port=None, entreprise=None):
        self._entreprise = entreprise
        from django.conf import settings
        self.host = host or getattr(settings, "LLAMA_HOST", "127.0.0.1")
        self.port = port or getattr(settings, "LLAMA_PORT", 8080)
        self._cfg = _charger_config(entreprise)

    def _get(self, attr, default):
        """Lit un attribut depuis cfg si disponible, sinon retourne default."""
        if self._cfg:
            return getattr(self._cfg, attr, default)
        return default

    def _build_prompt(self, tache, contexte_donnees, demande_utilisateur,
                      mode="script", instructions_table="", erreur_precedente=""):
        # ── Rôle ──
        role_map = {
            "analysis_script": self._get("role_analysis_script", DEFAULT_ROLES["analysis_script"]),
            "skill_script":    self._get("role_skill_script",    DEFAULT_ROLES["skill_script"]),
            "report_summary":  self._get("role_report_summary",  DEFAULT_ROLES["report_summary"]),
            "document":        self._get("role_document",        DEFAULT_ROLES["document"]),
            "other":           self._get("role_other",           DEFAULT_ROLES["other"]),
            "tache":           self._get("role_tache",           DEFAULT_ROLES["tache"]),
        }
        role = role_map.get(tache, role_map["other"])

        # ── Règle de format ──
        if mode == "script":
            regle = self._get("regle_script", DEFAULT_REGLE_SCRIPT)
        elif mode == "json":
            regle = (
                t("engine/inference.py:g:798:17")
            )
        elif mode == "document":
            regle = self._get("regle_document", DEFAULT_REGLE_DOCUMENT)
        elif mode == "tache":
            regle = self._get("regle_tache", DEFAULT_REGLE_TACHE)
        else:
            regle = t("engine/inference.py:g:806:21")

        if mode == "document":
            contraintes = self._get("contraintes_document", DEFAULT_CONTRAINTES_DOCUMENT)
            modules_config = self._get("modules_autorises_document", "")
        elif mode == "tache":
            contraintes = self._get("contraintes_tache", DEFAULT_CONTRAINTES_TACHE)
            modules_config = self._get("modules_autorises_tache", "")
        else:
            contraintes = self._get("contraintes_syntaxe", DEFAULT_CONTRAINTES)
            modules_config = self._get("modules_autorises_script", "")
        if modules_config and modules_config.strip():
            if mode == "tache":
                # Les tâches ne tournent pas dans un bac à sable (voir agent.executer_tache) :
                # la liste est une consigne, et les imports engine.* du contexte restent permis.
                contraintes += (
                    tf("engine/inference.py:g:822:21", v1=modules_config.strip())
                )
            else:
                contraintes += (
                    tf("engine/inference.py:g:827:21", v1=modules_config.strip())
                )
        # Deux canaux d'exemple distincts : dashboard et documents.
        # Chaque canal a son propre script_exemple en BD (configurable) avec
        # fallback sur la constante codée en dur si la BD est vide ou invalide.
        if mode == "document":
            script_exemple_config = (
                getattr(self._cfg, "script_exemple_document", "").strip()
                if self._cfg else ""
            )
            # Pour le canal document on n'impose pas de vérification d'ordre de clés
            # (la structure dict document est différente des 10 clés dashboard).
            script_exemple = exemple_a_jour("script_exemple_document", script_exemple_config, EXEMPLE_SCRIPT_DOCUMENT)
        elif mode == "tache":
            exemple_tache_config = exemple_a_jour("script_exemple_tache", self._get("script_exemple_tache", ""), "")
            script_exemple = (
                exemple_tache_config if "def executer(" in exemple_tache_config else EXEMPLE_TACHE
            )
        else:
            script_exemple_config = exemple_a_jour(
                "script_exemple", self._cfg.script_exemple if self._cfg else "", "")
            if script_exemple_config and _exemple_respecte_structure(
                script_exemple_config, CLES_SCRIPT_DANS_LORDRE
            ):
                script_exemple = script_exemple_config
            else:
                script_exemple = EXEMPLE_SCRIPT_PYTHON

        bloc_instructions_table = (
            tf("engine/inference.py:g:858:13", v1=instructions_table)
            if instructions_table else ""
        )

        bloc_correction = ""
        if erreur_precedente:
            bloc_correction = (
                tf("engine/inference.py:g:865:17", v1=erreur_precedente)
            )

        if mode == "document":
            # Canal document : contexte_donnees = schéma + données d'une seule entité
            # instructions_table = instructions utilisateur (titre, type, notes libres)
            bloc_correction_doc = ""
            if erreur_precedente:
                bloc_correction_doc = (
                    tf("engine/inference.py:g:882:21", v1=erreur_precedente)
                )
            return tf("engine/inference.py:g:891:20", v1=role, v2=contexte_donnees, v3=bloc_instructions_table, v4=script_exemple, v5=demande_utilisateur, v6=regle, v7=contraintes, v8=bloc_correction_doc)

        return tf("engine/inference.py:g:903:16", v1=role, v2=contexte_donnees, v3=bloc_instructions_table, v4=script_exemple, v5=demande_utilisateur, v6=regle, v7=contraintes, v8=bloc_correction)

    @staticmethod
    def _extraire_bloc(text, langage_regex=r"[a-zA-Z]*"):
        if not text:
            return None
        match = re.search(rf"```{langage_regex}\s*([\s\S]*?)\s*```", text)
        return match.group(1).strip() if match else None

    def generer(self, tache, contexte_donnees, demande_utilisateur,
                mode="script", instructions_table="", erreur_precedente="", timeout=None):
        prompt = self._build_prompt(
            tache, contexte_donnees, demande_utilisateur, mode=mode,
            instructions_table=instructions_table, erreur_precedente=erreur_precedente,
        )
        return self._appeler_llm(prompt, mode=mode, timeout=timeout)

    def generer_json(self, prompt, timeout=None):
        """Appel LLM avec un prompt ENTIÈREMENT fourni par l'appelant (pas de
        template _build_prompt — le besoin ne rentre pas dans le moule
        CONTEXTE/SCRIPT DE RÉFÉRENCE des autres canaux), réponse attendue en
        JSON strict entre balises ```json ... ```. Réutilise _appeler_llm tel
        quel (comptage tokens, moteur externe/local, mêmes réglages) : seule
        la source du prompt change. Introduit pour la classification
        d'intention de la Phase 5 (voir agent_ontology.py), réutilisable pour
        tout autre besoin de réponse JSON structurée hors script/document."""
        return self._appeler_llm(prompt, mode="json", timeout=timeout)

    def generer_formule_calcul(self, description, cible_type, contexte_donnees, timeout=None):
        """Génère une formule personnalisée (`def formule(cible): ...`) pour le
        gestionnaire de formules des tables de calcul (voir formules_calcul.py /
        LigneCalcul), avec sa propre configuration de prompt — rôle, règle de
        sortie, contraintes, modules autorisés et script d'exemple — entièrement
        éditable dans ConfigurationIA, au même titre que les canaux analyse
        (role_analysis_script / regle_script / contraintes_syntaxe /
        modules_autorises_script / script_exemple) et document (role_document /
        regle_document / contraintes_document / modules_autorises_document /
        script_exemple_document)."""
        role = self._get("role_formule", DEFAULT_ROLES["formule"])
        regle = self._get("regle_formule", DEFAULT_REGLE_FORMULE)
        contraintes = self._get("contraintes_formule", DEFAULT_CONTRAINTES_FORMULE)
        # Champ dédié au canal formule (voir calculs.calculer_ligne qui lit
        # exactement la même config côté exécution), pour que cette annonce
        # au modèle corresponde toujours à ce que le bac à sable autorise
        # réellement.
        modules_config = self._get("modules_autorises_formule", "")
        if modules_config and modules_config.strip():
            contraintes += (
                tf("engine/inference.py:g:961:17", v1=modules_config.strip())
            )

        # Deux formes de cible = deux scripts d'exemple distincts, chacun
        # configurable en BD (fallback sur la constante codée en dur si vide
        # ou si la fonction `formule(cible)` n'y est pas définie).
        if cible_type == "table":
            forme_cible = (
                t("engine/inference.py:g:970:17")
            )
            exemple_config = self._get("script_exemple_formule_table", "").strip()
            exemple = exemple_config if "def formule(cible)" in exemple_config else EXEMPLE_FORMULE_TABLE
        else:
            forme_cible = (
                t("engine/inference.py:g:981:17")
            )
            exemple_config = self._get("script_exemple_formule_colonne", "").strip()
            exemple = exemple_config if "def formule(cible)" in exemple_config else EXEMPLE_FORMULE_COLONNE

        prompt = tf("engine/inference.py:g:987:18", v1=role, v2=contexte_donnees, v3=forme_cible, v4=exemple, v5=description, v6=regle, v7=contraintes)
        return self._appeler_llm(prompt, mode="script", timeout=timeout)

    def generer_graphique_bi(self, description, cible_type, contexte_donnees, timeout=None):
        """Génère un graphique BI personnalisé (`def dessiner(dataframe, colonnes): ...`)
        pour la bibliothèque de graphiques des tableaux de bord BI (voir
        graphiques_bi.py / models.GraphiqueBI), avec sa propre configuration de
        prompt — rôle, règle de sortie, contraintes et scripts d'exemple —
        entièrement éditable dans ConfigurationIA, au même titre que le canal
        formule (role_formule / regle_formule / contraintes_formule /
        script_exemple_formule_*). Réutilise modules_autorises_graphique_bi
        (même bac à sable pour l'exécution et pour ce que l'IA a le droit
        d'annoncer — voir executor.executer_graphique_bi_securise)."""
        role = self._get("role_graphique_bi", DEFAULT_ROLES["graphique_bi"])
        regle = self._get("regle_graphique_bi", DEFAULT_REGLE_GRAPHIQUE_BI)
        contraintes = self._get("contraintes_graphique_bi", DEFAULT_CONTRAINTES_GRAPHIQUE_BI)
        modules_config = self._get("modules_autorises_graphique_bi", "")
        if modules_config and modules_config.strip():
            contraintes += (
                tf("engine/inference.py:g:1018:17", v1=modules_config.strip())
            )

        if cible_type == "colonnes_multiples":
            forme_cible = (
                t("engine/inference.py:g:1024:17")
            )
            exemple_config = exemple_a_jour("script_exemple_graphique_bi_multiples", self._get("script_exemple_graphique_bi_multiples", ""), "")
            exemple = exemple_config if "def dessiner(" in exemple_config else EXEMPLE_GRAPHIQUE_BI_MULTIPLES
        else:
            forme_cible = (
                t("engine/inference.py:g:1033:17")
            )
            exemple_config = self._get("script_exemple_graphique_bi_colonne", "").strip()
            exemple = exemple_config if "def dessiner(" in exemple_config else EXEMPLE_GRAPHIQUE_BI_COLONNE

        prompt = tf("engine/inference.py:g:1040:18", v1=role, v2=contexte_donnees, v3=forme_cible, v4=exemple, v5=description, v6=regle, v7=contraintes)
        return self._appeler_llm(prompt, mode="script", timeout=timeout)

    def generer_couche_ontologie(self, description, contexte_graphe, timeout=None):
        """Génère une couche d'ontologie personnalisée (`def etendre(graphe): ...`)
        pour le graphe d'entourage/ontologie (voir ontologie.py / models.CoucheOntologie),
        avec sa propre configuration de prompt — rôle, règle de sortie, contraintes et
        script d'exemple — entièrement éditable dans ConfigurationIA, au même titre que
        les canaux formule et graphique_bi. Contrairement à ces deux canaux, il n'y a pas
        de table de référence : `contexte_graphe` (voir
        ontologie.contexte_pour_generation_couche) décrit plutôt les types de nœuds et de
        relations déjà présents dans le graphe (tables, documents, modèles IA, clients,
        agents… et tout autre événement/entité de la plateforme déjà représenté), pour que
        le script généré réutilise les bons préfixes d'id et attributs plutôt que d'en
        inventer. Réutilise modules_autorises_couche_ontologie (même bac à sable pour
        l'exécution et pour ce que l'IA a le droit d'annoncer — voir
        executor.executer_couche_ontologie_securisee)."""
        role = self._get("role_couche_ontologie", DEFAULT_ROLES["couche_ontologie"])
        regle = self._get("regle_couche_ontologie", DEFAULT_REGLE_COUCHE_ONTOLOGIE)
        contraintes = self._get("contraintes_couche_ontologie", DEFAULT_CONTRAINTES_COUCHE_ONTOLOGIE)
        modules_config = self._get("modules_autorises_couche_ontologie", "")
        if modules_config and modules_config.strip():
            contraintes += (
                tf("engine/inference.py:g:1075:17", v1=modules_config.strip())
            )

        exemple_config = self._get("script_exemple_couche_ontologie", "").strip()
        exemple = exemple_config if "def etendre(" in exemple_config else EXEMPLE_COUCHE_ONTOLOGIE

        prompt = tf("engine/inference.py:g:1082:18", v1=role, v2=contexte_graphe, v3=exemple, v4=description, v5=regle, v6=contraintes)
        return self._appeler_llm(prompt, mode="script", timeout=timeout)

    def generer_modele_ia(self, description, timeout=None):
        """Génère un script de modèle DL/ML (NOM + entrainer + predire) pour IA/plugins_modeles.py,
        avec sa propre configuration de prompt (canal modele_ia, éditable dans ConfigurationIA)."""
        role = self._get("role_modele_ia", DEFAULT_ROLES["modele_ia"])
        regle = regle_modele_ia_a_jour(self._get("regle_modele_ia", DEFAULT_REGLE_MODELE_IA))
        contraintes = contraintes_modele_ia_a_jour(self._get("contraintes_modele_ia", DEFAULT_CONTRAINTES_MODELE_IA))
        from .executor import modules_modele_ia_a_jour, DEFAULT_MODULES_AUTORISES_MODELE_IA
        modules_config = (modules_modele_ia_a_jour(self._get("modules_autorises_modele_ia", ""))
                          or ", ".join(sorted(DEFAULT_MODULES_AUTORISES_MODELE_IA)))
        contraintes += (
            tf("engine/inference.py:g:1107:17", v1=modules_config)
        )
        exemple = exemples_modele_ia_pour_prompt(self._get("script_exemple_modele_ia", ""))
        from IA import trainer as T
        T._register_all()
        prompt = tf("engine/inference.py:g:1114:18", v1=role, v2=', '.join(sorted(T._REGISTRY)), v3=exemple, v4=description, v5=regle, v6=contraintes)
        return self._appeler_llm(prompt, mode="script", timeout=timeout)

    def generer_texte(self, prompt, systeme=None, timeout=None):
        """Réponse en TEXTE LIBRE (aucune balise de bloc exigée) avec un message système fourni par l'appelant.
        Introduit pour le chatbot des agents (engine/chatbot.py) ; renvoie (texte, erreur) comme les autres canaux."""
        return self._appeler_llm(prompt, mode="texte", timeout=timeout, systeme=systeme)

    def _appeler_llm(self, prompt, mode="script", timeout=None, systeme=None):
        from django.conf import settings as dj_settings

        externe = bool(self._cfg and self._cfg.moteur_type == "externe" and self._cfg.moteur_url_externe)
        headers = {}
        anthropic = False
        if externe:
            base = self._cfg.moteur_url_externe.rstrip("/")
            modele = self._cfg.moteur_modele or getattr(dj_settings, "LLAMA_MODEL", "qwen2.5-coder")
            anthropic = "anthropic.com" in base
            if anthropic:
                url = base if base.endswith("/messages") else f"{base}/messages"
                headers = {"x-api-key": self._cfg.moteur_cle_api or "", "anthropic-version": "2023-06-01"}
            else:
                url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
                if self._cfg.moteur_cle_api:
                    headers["Authorization"] = f"Bearer {self._cfg.moteur_cle_api}"
        else:
            host = (self._cfg.moteur_host if self._cfg and self._cfg.moteur_host else None) or self.host
            port = (self._cfg.moteur_port if self._cfg and self._cfg.moteur_port else None) or self.port
            url = f"http://{host}:{port}/v1/chat/completions"
            modele = getattr(dj_settings, "LLAMA_MODEL", "qwen2.5-coder")

        if timeout is None:
            timeout = getattr(dj_settings, "LLAMA_TIMEOUT", 120)

        system_prompt = systeme or self._get("system_prompt", DEFAULT_SYSTEM_PROMPT)

        if anthropic:
            payload = {"model": modele, "system": system_prompt,
                       "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": getattr(dj_settings, "LLAMA_MAX_TOKENS", 2048),
                       "temperature": min(getattr(dj_settings, "LLAMA_TEMPERATURE", 0.2), 1)}
        else:
            payload = {
                "model":       modele,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": getattr(dj_settings, "LLAMA_TEMPERATURE", 0.2),
                "top_p":       getattr(dj_settings, "LLAMA_TOP_P",       0.9),
                "max_tokens":  getattr(dj_settings, "LLAMA_MAX_TOKENS",  2048),
                "stream": False,
            }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.Timeout:
            return None, tf("engine/inference.py:g:1180:26", v1=timeout)
        except requests.RequestException as e:
            return None, tf("engine/inference.py:g:1182:26", v1=e)

        if response.status_code != 200:
            return None, tf("engine/inference.py:g:1185:26", v1=response.status_code)

        data_json = response.json()
        raw = data_json["content"][0]["text"] if anthropic else data_json["choices"][0]["message"]["content"]

        # ── Comptage tokens ────────────────────────────────────────────────
        try:
            usage = data_json.get("usage", {})
            tok_in  = usage.get("input_tokens",  0) or usage.get("prompt_tokens",     0)
            tok_out = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
            if tok_in or tok_out:
                from .models import CompteurTokens, Entreprise
                if self._cfg and hasattr(self._cfg, 'table') and self._cfg.table:
                    entreprise = self._cfg.table.entreprise
                else:
                    entreprise = getattr(self._cfg, 'entreprise', None)   # jamais « la première entreprise » : ce serait facturer les jetons à une autre
                if entreprise:
                    CompteurTokens.pour_entreprise(entreprise).ajouter(tok_in, tok_out)
        except Exception:
            pass  # Ne jamais bloquer l'inférence pour le comptage
        # ──────────────────────────────────────────────────────────────────

        if mode == "texte":
            return (raw or "").strip(), None
        lang_regex = "json" if mode == "json" else "python"
        bloc = self._extraire_bloc(raw, lang_regex)
        if bloc is None:
            return None, t("engine/inference.py:g:1212:26")
        return bloc, None
