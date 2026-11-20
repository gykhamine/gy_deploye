"""Encodage des VRAIS champs d'une table en entrées numériques d'un modèle.

Principe : à l'entraînement, chaque colonne d'entrée est encodée selon son
type réel (choix → one-hot, date → nombre standardisé, texte → TF-IDF...) et
la « recette » de chaque colonne (modalités, moyenne/écart, vocabulaire) est
mémorisée dans `ModeleIA.structure_entree["encodeurs"]`.
À l'inférence, le décideur renseigne les MÊMES champs que dans la table
(`{slug: valeur réelle}`) : aucune représentation technique, aucun JSON — la
recette mémorisée est réappliquée à l'identique (`encoder_entree`).

Module autonome (aucun import du paquet ia_integration au chargement, pour
éviter tout cycle) ; seul le TF-IDF est importé à la demande.
"""
from engine.i18n import t as _i18n_t, tf
import datetime
import re

import numpy as np
from engine.i18n import tf as _gtf  # noqa: E402

# Types numériques déjà gérés tels quels par ia_integration (rappel local :
# évite l'import circulaire).
TYPES_NUMERIQUES = (
    "nombre", "decimal", "monnaie", "pourcentage", "note", "constante",
    "barre_progression",
)
TYPES_BOOLEENS = ("booleen", "switch")
TYPES_CHOIX_UNIQUE = ("choix_unique", "couleur", "colonne")
TYPES_CHOIX_MULTIPLE = ("choix_multiple",)
TYPES_TEMPORELS = ("date", "datetime", "horodatage", "heure", "duree")
TYPES_TEXTE = ("texte", "texte_long")

# Types que ce module ajoute à ce que ia_integration encodait déjà (le MLP et
# les modèles scikit-learn tabulaires les acceptent désormais en entrée).
TYPES_SUPPLEMENTAIRES = (
    TYPES_CHOIX_UNIQUE + TYPES_CHOIX_MULTIPLE + TYPES_TEMPORELS + TYPES_TEXTE
)
TYPES_SIMPLES = TYPES_NUMERIQUES + TYPES_BOOLEENS


# ── Conversions de valeurs réelles ───────────────────────────────────────
def _vide(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _en_bool(v):
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "vrai", "oui", "yes", "on")
    return bool(v)


def _en_nombre(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(str(v).replace(",", ".").strip())


def _secondes_depuis_texte(v):
    """« 14:30 », « 01:02:03 » ou un nombre de secondes → secondes."""
    texte = str(v).strip()
    if re.fullmatch(r"\d{1,3}(:\d{1,2}){1,2}", texte):
        parts = [int(p) for p in texte.split(":")]
        while len(parts) < 3:
            parts.append(0)
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return _en_nombre(texte)


def _scalaire_temporel(type_champ, v):
    """Date/heure/durée réelle → un nombre (jours depuis 1970, ou secondes)."""
    if type_champ in ("heure", "duree"):
        return _secondes_depuis_texte(v)
    texte = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(texte)
    except ValueError:
        try:
            dt = datetime.datetime.strptime(texte[:10], "%Y-%m-%d")
        except ValueError:
            raise ValueError(tf("engine/encodage_champs.py:g:78:30", v1=v))
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return (dt - datetime.datetime(1970, 1, 1)).total_seconds() / 86400.0


def _liste_multiple(v):
    if _vide(v):
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


# ── Entraînement : construire la recette + le bloc encodé d'UNE colonne ──
def largeur(spec):
    k = spec["kind"]
    if k in ("choix", "multi"):
        return max(1, len(spec["modalites"]))
    if k == "texte":
        return len(spec["vocabulaire"])
    return 1


def ajuster(champ, valeurs):
    """(bloc (n, k) float64, spec) pour une colonne de type 'supplémentaire'.
    `valeurs` = valeur_typee() de chaque ligne (None si absente)."""
    t = champ.type_champ
    base = {"slug": champ.slug, "nom": champ.nom, "type": t}
    if t in TYPES_CHOIX_UNIQUE:
        modalites = sorted({str(v) for v in valeurs if not _vide(v)})
        if not modalites:
            raise ValueError(tf("engine/encodage_champs.py:g:110:30", v1=champ.nom))
        spec = dict(base, kind="choix", modalites=modalites)
    elif t in TYPES_CHOIX_MULTIPLE:
        modalites = sorted({m for v in valeurs for m in _liste_multiple(v)})
        if not modalites:
            raise ValueError(tf("engine/encodage_champs.py:g:115:30", v1=champ.nom))
        spec = dict(base, kind="multi", modalites=modalites)
    elif t in TYPES_TEMPORELS:
        nombres = []
        for v in valeurs:
            if _vide(v):
                continue
            try:
                nombres.append(_scalaire_temporel(t, v))
            except ValueError:
                continue
        if not nombres:
            raise ValueError(tf("engine/encodage_champs.py:g:127:30", v1=champ.nom))
        moyenne = float(np.mean(nombres))
        ecart = float(np.std(nombres)) or 1.0
        spec = dict(base, kind="temps", moyenne=moyenne, ecart=ecart)
    elif t in TYPES_TEXTE:
        from . import ia_integration as _ia  # import tardif (pas de cycle au chargement)
        textes = ["" if _vide(v) else str(v) for v in valeurs]
        vocabulaire, idf = _ia._construire_vocabulaire_texte(textes)
        spec = dict(base, kind="texte", vocabulaire=vocabulaire, idf=idf)
    else:
        raise ValueError(tf("engine/encodage_champs.py:g:137:26", v1=t))
    bloc = np.vstack([_appliquer(spec, v, tolerant=True) for v in valeurs]) \
        if valeurs else np.zeros((0, largeur(spec)))
    return bloc, spec


def spec_simple(champ, valeurs=None):
    """Recette d'une colonne déjà numérique (nombre, booléen...).

    Pour une colonne numérique, `valeurs` (les nombres de l'entraînement) sert à mémoriser moyenne et écart-type :
    la colonne est ensuite centrée-réduite (appliquer_num / _appliquer). Sans cela, un nombre à grande échelle
    (un prix à 100000) mélangé à des colonnes texte (TF-IDF, valeurs entre 0 et 1) fait diverger les modèles à
    descente de gradient (MLP). Une recette SANS `moyenne` (modèle entraîné avant ce changement) reste appliquée
    telle quelle : un ancien modèle donne exactement les mêmes prédictions qu'avant."""
    spec = {"slug": champ.slug, "nom": champ.nom, "type": champ.type_champ,
            "kind": "bool" if champ.type_champ in TYPES_BOOLEENS else "num"}
    if spec["kind"] == "num" and valeurs is not None:
        v = np.asarray(valeurs, dtype=np.float64).ravel()
        v = v[np.isfinite(v)]
        if v.size:
            ecart = float(np.std(v))
            spec["moyenne"] = float(np.mean(v))
            spec["ecart"] = ecart if ecart > 1e-12 else 1.0
    return spec


def appliquer_num(spec, valeurs):
    """Colonne numérique (n,) -> (n,) centrée-réduite si la recette a une moyenne, sinon inchangée."""
    x = np.asarray(valeurs, dtype=np.float64)
    if spec.get("kind") == "num" and "moyenne" in spec:
        return (x - spec["moyenne"]) / (spec.get("ecart") or 1.0)
    return x


# ── Application d'une recette à UNE valeur réelle ────────────────────────
def _appliquer(spec, valeur, tolerant=False):
    """Vecteur (k,) pour une valeur réelle. `tolerant` (entraînement) : une
    valeur absente donne des zéros ; à l'inférence, un champ obligatoire vide
    lève ValueError avec le nom du champ."""
    k = spec["kind"]
    nom = spec.get("nom") or spec["slug"]
    if k == "bool":
        return np.array([1.0 if _en_bool(valeur) else 0.0])
    if k == "multi":
        out = np.zeros(largeur(spec))
        index = {m: i for i, m in enumerate(spec["modalites"])}
        for m in _liste_multiple(valeur):
            if m in index:
                out[index[m]] = 1.0
        return out
    if k == "texte":
        from . import ia_integration as _ia
        texte = "" if _vide(valeur) else str(valeur)
        return _ia._vectoriser_texte_tfidf([texte], spec["vocabulaire"], spec["idf"])[0]
    if _vide(valeur):
        if tolerant:
            return np.zeros(largeur(spec))
        raise ValueError(tf("engine/encodage_champs.py:g:172:26", v1=nom))
    if k == "num":
        try:
            nombre = _en_nombre(valeur)
        except ValueError:
            raise ValueError(tf("engine/encodage_champs.py:g:177:30", v1=nom, v2=valeur))
        if "moyenne" in spec:   # recette avec standardisation (voir spec_simple)
            nombre = (nombre - spec["moyenne"]) / (spec.get("ecart") or 1.0)
        return np.array([nombre])
    if k == "choix":
        out = np.zeros(largeur(spec))
        index = {m: i for i, m in enumerate(spec["modalites"])}
        i = index.get(str(valeur))
        if i is None:
            if tolerant:
                return out
            raise ValueError(
                tf("engine/encodage_champs.py:g:186:17", v1=nom, v2=valeur, v3=', '.join(spec['modalites']))
            )
        out[i] = 1.0
        return out
    if k == "temps":
        try:
            brut = _scalaire_temporel(spec["type"], valeur)
        except ValueError as e:
            if tolerant:
                return np.zeros(1)
            raise ValueError(f"« {nom} » : {e}")
        return np.array([(brut - spec["moyenne"]) / spec["ecart"]])
    raise ValueError(_gtf("rg8.msg.038", k=k))


def encoder_entree(specs, entree):
    """`entree` = {slug: valeur réelle} → matrice (1, K), dans l'ordre exact
    des colonnes d'entraînement. Lève ValueError (message destiné au
    décideur) si un champ manque ou est invalide."""
    if not isinstance(entree, dict):
        raise ValueError(_i18n_t("engine/encodage_champs.py:g:207:26"))
    blocs = []
    for spec in specs:
        if spec["slug"] not in entree and spec["kind"] not in ("multi", "texte"):
            raise ValueError(tf("engine/encodage_champs.py:g:211:30", v1=spec.get('nom') or spec['slug']))
        blocs.append(_appliquer(spec, entree.get(spec["slug"])))
    return np.concatenate(blocs).reshape(1, -1)
