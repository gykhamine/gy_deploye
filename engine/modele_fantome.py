"""MODÈLE FANTÔME : un vrai réseau de neurones (apprentissage profond) qui pilote l'arbitrage stratégique
(engine/strategie.py) et se fine-tune tout seul, à l'insu de l'utilisateur.

Aucun jeu de données n'est préparé par personne : engine/donnees_entrainement.py le construit automatiquement depuis
toute la structure de la plateforme (tables, colonnes, cellules, ontologie, KPI, modèles IA, blockchain, décisions).

Principes :
1. NOYAU — le modèle naît avec un petit réseau (engine/reseau_neuronal.py) pré-entraîné sur la formule d'utilité du moteur
   précédent : jamais vide, la première recommandation n'est pas aléatoire. Il suffit d'une table pour qu'il existe.
2. FINE-TUNING À CHAQUE NOUVEAUTÉ — chaque passage transforme ce qui est nouveau (un jour de plus dans l'historique d'une
   table, une valeur de KPI de plus, une conséquence de décision mesurée) en petit lot d'échantillons et affine le réseau
   quelques pas (Adam, avec rejeu d'échantillons récents contre l'oubli). Entraînement PARESSEUX : au plus `max_par_pas`
   échantillons et un passage toutes les `intervalle_min_s` secondes ; le modèle est utilisable à tout instant.
3. DEUX SORTIES UTILES — 4 têtes de RÉCOMPENSE (ensemble profond : leur désaccord mesure l'incertitude, la recommandation
   tire une tête au hasard, façon Thompson) et 1 tête d'ÉLAN (variation d'activité attendue, apprise sur l'historique de
   toutes les tables et de tous les KPI, même sans aucune décision).
4. GARDE-FOU DE TAILLE — chaque réseau reste sous `plafond_parametres` (10 000) : il grandit sans perte (neurones ajoutés à
   sortie nulle) puis, à la limite, il est GELÉ et un nouveau réseau, dans un autre fichier, apprend le RÉSIDU des précédents.
   La prédiction est la somme des réseaux : ils se complètent.
5. RYTHME — la récompense d'une décision est mesurée sur ses conséquences réelles (risque, KPI, activité, revenu).

Fichiers : `<nom>.agentgy` (premier réseau), `<nom>_1.agentgy`, `<nom>_2.agentgy`... = signature « AGENTGY\\0 » + version +
HMAC-SHA256 + archive npz (poids, moments d'Adam, échantillons de rejeu, méta en JSON ; aucun pickle). Écriture atomique.
Un fichier altéré est refusé (et tous les réseaux qui en dépendent) : jamais chargé.
COMPATIBILITÉ ASCENDANTE (une mise à jour du logiciel ne casse JAMAIS un modèle déjà appris) :
  * un fichier mémorise le NOM de chacune de ses colonnes d'entrée et de ses têtes de sortie (meta["entrees"], meta["sorties"]) ;
    au chargement, chaque poids rejoint sa colonne PAR SON NOM : colonne ajoutée = poids nuls (le réseau répond comme avant puis apprend
    à s'en servir), colonne déplacée = le poids la suit, colonne retirée = ignorée ; même principe pour les têtes de sortie ;
  * les versions de fichier antérieures restent lisibles (`_VERSIONS_LISIBLES`), et les clés de méta absentes reçoivent leur valeur par défaut ;
  * un fichier d'une version PLUS RÉCENTE que le logiciel n'est ni écarté ni écrasé : le modèle passe en lecture seule (`lecture_seule`) ;
  * avant de réécrire un fichier migré, l'original est copié à côté (`.avant_maj_<empreinte>`), une seule fois ;
  * les échantillons d'un type inconnu sont ignorés, jamais appris à la place d'un autre type (`TYPES_ECHANTILLON`) ;
  * le fine-tuning n'agit que sur le réseau actif et sur ce qui est nouveau : les poids déjà appris restent, les réseaux gelés ne bougent pas.
Module PUR (aucun import Django) : testable avec des dictionnaires.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from engine.i18n import t as _i18n_t

import hashlib
import hmac
import json
import math
import os
import random
import shutil
import tempfile
import zlib

import numpy as np

from . import donnees_entrainement as dn
from . import reseau_neuronal as rn
from engine.i18n import t as _gt  # noqa: E402

try:   # JAX / Flax / Optax : gradient par différentiation automatique (engine/reseau_jax.py) ; sinon repli numpy, même API
    from . import reseau_jax as _moteur
    MOTEUR = "jax"
except ImportError:
    _moteur, MOTEUR = rn, "numpy"

EXTENSION = ".agentgy"
_MAGIE, _VERSION = b"AGENTGY\x00", 3   # v3 = v2 + noms des colonnes/têtes dans la méta (le conteneur est inchangé : les v2 restent lisibles)
_VERSIONS_LISIBLES = (2, 3)
NB_TETES = 4                 # têtes de récompense (ensemble profond)
IDX_ELAN = NB_TETES          # tête d'élan
D_SORTIE = NB_TETES + 1
NOMS_SORTIES = tuple(f"recompense_{k}" for k in range(NB_TETES)) + ("elan",)   # NOM de chaque tête : on en AJOUTE en fin, on ne renomme jamais
PHI_NOMS = ("biais", "risque", "potentiel", "confiance", "risque_x_confiance", "potentiel_x_confiance", "cout", "elan")
PARAMETRES = {
    "largeur_depart": 8,               # neurones par couche cachée du noyau
    "plafond_parametres": rn.PARAMETRES_MAX,  # garde-fou : au-delà, un autre réseau complémentaire
    "couches_residuelles": 0,          # couches cachées AJOUTÉES au réseau actif (même réseau, même fichier), sans perte ; 0 = architecture d'origine
    "echantillons_par_niveau": 40,     # le réseau s'élargit d'un palier à 40, 120, 280... échantillons absorbés
    "noyau_n": 800, "noyau_pas": 2000,  # pré-entraînement du noyau (formule d'utilité + apport des critères), ~2 s une fois par processus
    "replay_max": 400,                 # échantillons récents rejoués à chaque passage (contre l'oubli)
    "lot_replay": 48, "pas_par_passage": 25,
    "lr": 0.01, "lr_min": 0.002,
    "max_par_pas": 64,                 # échantillons absorbés par passage
    "intervalle_min_s": 30,            # écart minimal entre deux passages
    "bruit_exploration": 0.25,         # exploration résiduelle, décroît avec les récompenses apprises
    "obs_exploration": 4.0,
    "elan_min_obs": 30,                # échantillons d'élan avant de se fier à la tête d'élan
    "cles_max": 6000,
    "poids_recompense": {"revenu": 0.6, "risque": 0.4, "kpi": 0.3, "activite": 0.15},
    "poids_critere": 0.05,             # utilité a priori ajoutée par critère d'intensité 1 (problème OU opportunité), avant confiance
    "plafond_criteres": 0.3,           # plafond de cet apport : les critères nuancent le risque et le potentiel, ils ne les remplacent pas
}


def phi(m: dict, elan_valeur: float) -> list[float]:
    """Vecteur de caractéristiques d'une option (composantes 0..1 de strategie._mesurer + élan)."""
    r, po, c, co = m["risque"], m["potentiel"], m["confiance"], min(3.0, max(0.0, m["cout"]))
    return [1.0, r, po, c, r * c, po * c, co, elan_valeur]


def apport_criteres(crit, parametres: dict | None = None) -> float:
    """Utilité a priori des 33 critères mesurés sur les objets visés (formule de départ, avant tout apprentissage) : chaque
    intensité pèse `poids_critere`, l'ensemble est plafonné. Un critère déjà déclenché compte aussi (son intensité dit à quel point
    on dépasse le seuil, ce que le risque de la règle ne dit pas) ; un critère silencieux compte en proportion de son intensité."""
    p = {**PARAMETRES, **(parametres or {})}
    return min(p["plafond_criteres"], p["poids_critere"] * float(sum(crit)))


# ── Récompense d'une décision (conséquences mesurées) ───────────────────────
def _variation_kpi(avant: dict, apres: dict) -> float:
    """Variation d'un KPI vers son objectif (sens hausse / baisse / cible), relative, écrasée dans ]-1, 1[."""
    v0, v1, cible, sens = avant.get("valeur"), apres.get("valeur"), avant.get("cible"), avant.get("sens")
    if v0 is None or v1 is None:
        raise ValueError
    if sens == "cible" and cible is not None:
        ecart0, ecart1 = abs(v0 - cible), abs(v1 - cible)
        return math.tanh((ecart0 - ecart1) / max(ecart0, 1e-9))
    signe = -1.0 if sens == "baisse" else 1.0
    return math.tanh(signe * (v1 - v0) / max(abs(v0), 1e-9))


def recompense(avant: dict, apres: dict, revenu: float | None = None, echelle_revenu: float = 0.0,
               parametres: dict | None = None) -> tuple[float | None, float]:
    """(récompense dans [-1, 1] ou None si rien de mesurable, nouvelle échelle de revenu).
    Composantes disponibles : risque traité (baisse), KPI visés, activité, revenu déclaré (le plus lourd) ;
    moyenne pondérée des composantes réellement mesurables."""
    w = {**PARAMETRES, **(parametres or {})}["poids_recompense"]
    parts = []
    if avant.get("risque"):
        parts.append((w["risque"], max(-1.0, min(1.0, avant["risque"] - apres.get("risque", avant["risque"])))))
    kpis_apres = {k["id"]: k for k in apres.get("kpis", [])}
    variations = []
    for k in avant.get("kpis", []):
        try:
            variations.append(_variation_kpi(k, kpis_apres[k["id"]]))
        except (KeyError, ValueError, TypeError):
            continue
    if variations:
        parts.append((w["kpi"], sum(variations) / len(variations)))
    if avant.get("activite") is not None and apres.get("activite") is not None:
        parts.append((w["activite"], math.tanh(math.log((apres["activite"] + 1) / (avant["activite"] + 1)))))
    if revenu is not None and math.isfinite(revenu):
        echelle_revenu = abs(revenu) if echelle_revenu <= 0 else 0.8 * echelle_revenu + 0.2 * abs(revenu)
        parts.append((w["revenu"], math.tanh(revenu / echelle_revenu)) if echelle_revenu > 0 else (0.0, 0.0))
    poids = sum(p for p, _x in parts)
    return (sum(p * x for p, x in parts) / poids if poids else None), echelle_revenu


# ── Coeur = un réseau + ses échantillons de rejeu + ses méta (un fichier .agentgy) ──
class Coeur:
    def __init__(self, reseau: _moteur.Reseau, meta: dict, X=None, Y=None, W=None):
        self.reseau, self.meta = reseau, meta
        self.X = np.zeros((0, dn.D)) if X is None else X
        self.Y = np.zeros((0, D_SORTIE)) if Y is None else Y
        self.W = np.zeros((0, D_SORTIE)) if W is None else W
        self.sale = False   # à réécrire sur disque
        self.migre = False  # relu depuis un fichier d'une autre disposition/version (les poids sont déjà réalignés)
        self.empreinte_fichier = ""   # empreinte du fichier d'origine (nom de la copie de sécurité d'avant migration)


_NOYAU: dict = {}   # noyaux pré-entraînés, par taille de pré-entraînement (l'apport a priori des critères n'y est PAS cuit : voir Modele.apport)


def _noyau(p: dict) -> dict:
    """Poids du réseau de départ, pré-entraînés UNE fois par processus sur des exemples synthétiques : la formule d'utilité
    du moteur précédent (0,6·r·c + 0,4·p·c − 0,25·coût) pour les têtes de récompense, un demi-tendance pour l'élan. L'apport a priori des
    critères n'est PAS appris ici : `Modele.apport` l'ajoute à chaque prédiction avec les réglages du moment, donc il peut varier dans le
    temps sans réentraîner ni recréer le réseau (les critères restent en entrée, le réseau en apprend le résidu)."""
    cle = tuple(p[k] for k in ("noyau_n", "noyau_pas", "largeur_depart"))
    if cle not in _NOYAU:
        rng, n, i = np.random.default_rng(7), p["noyau_n"], dn.I_PHI
        xo = rng.random((n, dn.D))
        xo[:, :3] = 0.0
        xo[:, dn.FLAG_OPTION] = 1.0
        r, po, c = xo[:, i + 1].copy(), xo[:, i + 2].copy(), xo[:, i + 3].copy()
        cout = rng.random(n) * 3
        xo[:, i], xo[:, i + 4], xo[:, i + 5], xo[:, i + 6], xo[:, i + 7] = 1.0, r * c, po * c, cout, rng.uniform(-1, 1, n)
        xt = rng.random((n, dn.D))
        xt[:, dn.I_CRIT:] = 0.0
        xt[:, :3] = 0.0
        xt[:, dn.FLAG_TABLE] = 1.0
        xt[:, i:i + dn.NB_PHI] = 0.0
        tendance = rng.uniform(-1, 1, n)
        xt[:, dn.I_TBL + dn.IDX_TENDANCE] = tendance
        # critères d'une option : creux comme dans la réalité (15 % des critères non nuls), intensité uniforme dans ]0, 1[
        # (une option sur deux n'a aucun critère mesuré : le noyau reste fidèle à la formule d'origine sur ce cas)
        crit = rng.random((n, dn.NB_CRIT)) * (rng.random((n, dn.NB_CRIT)) < 0.15) * (rng.random((n, 1)) < 0.5)
        xo[:, dn.I_CRIT:] = crit
        cols = [dn.I_CRIT + j for j in dn.COLONNES_CRIT_TABLE]   # critères d'une table : la tête d'élan doit y rester indifférente (cible inchangée)
        xt[:, cols] = rng.random((n, len(cols))) * (rng.random((n, len(cols))) < 0.3)
        x, y, w = np.vstack([xo, xt]), np.zeros((2 * n, D_SORTIE)), np.zeros((2 * n, D_SORTIE))
        y[:n, :NB_TETES], w[:n, :NB_TETES] = (0.6 * r * c + 0.4 * po * c - 0.25 * cout)[:, None], 1.0
        y[n:, IDX_ELAN], w[n:, IDX_ELAN] = 0.5 * tendance, 1.0
        reseau = _moteur.Reseau.nouveau(dn.D, p["largeur_depart"], D_SORTIE, graine=1000)
        for _ in range(p["noyau_pas"]):
            reseau.pas(x, y, w, 0.02)
        _NOYAU[cle] = reseau.p
    return _NOYAU[cle]


def _meta_defaut(maintenant: float = 0.0) -> dict:
    """Valeur par défaut de chaque clé de méta, hors `rang` et `apport_externe` : leur ABSENCE a un sens (position dans la chaîne ;
    noyau ancien qui porte déjà l'apport dans ses poids). Un fichier écrit avant l'ajout d'une clé se relit donc sans KeyError."""
    return {"cree_ts": maintenant, "maj_ts": maintenant, "dernier_pas_ts": 0.0, "n_abs": 0, "n_recomp": 0, "n_elan": 0, "gele": False,
            "cles": [], "empreinte": 0, "echelle_revenu": 0.0, "perte": None}


def nouveau_coeur(rang: int, maintenant: float, parametres: dict | None = None) -> Coeur:
    """Rang 0 : le noyau pré-entraîné. Rang > 0 : réseau à sortie nulle (n'altère pas la somme des précédents)."""
    p = {**PARAMETRES, **(parametres or {})}
    if rang == 0:
        reseau = _moteur.Reseau({k: a.copy() for k, a in _noyau(p).items()})
    else:
        reseau = _moteur.Reseau.nouveau(dn.D, p["largeur_depart"], D_SORTIE, graine=1000 + rang, sortie_nulle=True)
    coeur = Coeur(reseau, {**_meta_defaut(maintenant), "rang": rang,
                           "apport_externe": rang == 0})   # rang 0 : noyau SANS apport cuit ; un noyau plus ancien (clé absente) l'a dans ses poids
    coeur.sale = True
    return coeur


class Modele:
    """Ensemble de coeurs complémentaires : la prédiction est la SOMME des réseaux (chacun corrige le résidu des précédents)."""

    def __init__(self, coeurs: list[Coeur], parametres: dict | None = None):
        self.coeurs, self.p = coeurs, {**PARAMETRES, **(parametres or {})}
        self.lecture_seule = False   # vrai si un fichier d'une version plus récente du logiciel a été rencontré : ni entraîné ni réécrit

    @property
    def actif(self) -> Coeur:
        return self.coeurs[-1]

    @property
    def n_recomp(self) -> int:
        return sum(c.meta["n_recomp"] for c in self.coeurs)

    @property
    def n_elan(self) -> int:
        return sum(c.meta["n_elan"] for c in self.coeurs)

    def cles(self) -> set:
        return set().union(*(c.meta["cles"] for c in self.coeurs))

    def nb_parametres(self) -> int:
        return sum(c.reseau.nb_parametres() for c in self.coeurs)

    @property
    def apport_externe(self) -> bool:
        """Vrai si l'apport a priori est ajouté à la prédiction (noyau récent) ; faux si un noyau ancien l'a déjà dans ses poids."""
        return bool(self.coeurs and self.coeurs[0].meta.get("apport_externe"))

    def apport(self, x) -> np.ndarray:
        """Utilité a priori des critères, (n, D_SORTIE) : sur les têtes de récompense des lignes OPTION seulement, = `apport_criteres` x
        confiance de l'option. Calculée avec les paramètres COURANTS du modèle (`poids_critere`, `plafond_criteres`) : les changer
        agit à la prochaine prédiction, sur le modèle déjà entraîné (le réseau apprend le résidu de cet apport)."""
        x = np.atleast_2d(np.asarray(x, dtype=float))
        sortie = np.zeros((len(x), D_SORTIE))
        if not self.apport_externe:
            return sortie
        p = self.p
        utilite = np.minimum(p["plafond_criteres"], p["poids_critere"] * x[:, dn.I_CRIT:].sum(axis=1))
        sortie[:, :NB_TETES] = (x[:, dn.FLAG_OPTION] * x[:, dn.I_PHI + 3] * utilite)[:, None]
        return sortie

    def predire(self, x, jusqu_a: int | None = None) -> np.ndarray:
        """Somme des réseaux (jusqu'à `jusqu_a`, comme une tranche) + l'apport a priori, ajouté UNE fois quel que soit `jusqu_a`."""
        x = np.atleast_2d(np.asarray(x, dtype=float))
        return sum((c.reseau.predire(x) for c in self.coeurs[:jusqu_a]), self.apport(x))

    def resume(self) -> dict:
        return {"reseaux": len(self.coeurs), "parametres": [c.reseau.nb_parametres() for c in self.coeurs],
                "largeurs": [c.reseau.largeur for c in self.coeurs], "plafond": self.p["plafond_parametres"],
                "recompenses_apprises": self.n_recomp, "elans_appris": self.n_elan, "perte": self.actif.meta.get("perte")}


class Echantillonneur:
    """Tire des utilités plausibles pour les options (Thompson) : une tête de récompense au hasard + un bruit résiduel qui
    décroît avec le nombre de récompenses apprises. Les prédictions sont calculées UNE fois."""

    def __init__(self, modele: Modele, xs: list[list[float]]):
        self.mu = modele.predire(xs)[:, :NB_TETES]
        self.sigma = modele.p["bruit_exploration"] / math.sqrt(1 + modele.n_recomp / modele.p["obs_exploration"])

    def tirer(self, rng: random.Random) -> list[float]:
        k = rng.randrange(NB_TETES)
        return [float(m + self.sigma * rng.gauss(0.0, 1.0)) for m in self.mu[:, k]]


def elan(modele: Modele | None, t: dict, vue: dn.Vue | None = None) -> float:
    """Élan d'une table dans ]-1, 1[. Modèle assez appris : tête d'élan du réseau, calculée depuis la structure de la table.
    Sinon (modèle jeune) : log-ratio semaine / semaine précédente."""
    if modele is not None and vue is not None and modele.n_elan >= modele.p["elan_min_obs"]:
        return max(-0.99, min(0.99, float(modele.predire(vue.x_table(t))[0, IDX_ELAN])))
    return math.tanh(math.log((t.get("n7", 0) + 1) / (t.get("n7_prec", 0) + 1)))


# ── Entraînement paresseux ──────────────────────────────────────────────────
def peut_entrainer(modele: Modele, maintenant: float) -> bool:
    if modele.lecture_seule:
        return False
    return maintenant - modele.actif.meta["dernier_pas_ts"] >= modele.p["intervalle_min_s"]


def _poids_bootstrap(cle: str) -> np.ndarray:
    """Poids de Poisson(1) par tête, fixés par la clé : chaque tête voit un rééchantillonnage différent (diversité)."""
    w = np.array([float(np.random.default_rng(zlib.crc32(f"{cle}#{k}".encode())).poisson(1.0)) for k in range(NB_TETES)])
    if not w.any():
        w[zlib.crc32(cle.encode()) % NB_TETES] = 1.0
    return w


def _cible_recompense(e: dict, y: np.ndarray, w: np.ndarray) -> None:
    y[:NB_TETES], w[:NB_TETES] = e["valeur"], _poids_bootstrap(e["cle"])


def _cible_elan(e: dict, y: np.ndarray, w: np.ndarray) -> None:
    y[IDX_ELAN], w[IDX_ELAN] = e["valeur"], 1.0


# Types d'échantillons connus -> façon de remplir (y, w) : un NOUVEAU type d'entraînement s'ajoute ici (declarer_type_echantillon) sans
# toucher aux autres ; un type absent du registre est IGNORÉ (jamais appris à la place d'un autre, comme le faisait un « sinon »).
TYPES_ECHANTILLON = {"recompense": _cible_recompense, "elan": _cible_elan}


def declarer_type_echantillon(nom: str, cible) -> None:
    """Ajoute un type d'échantillon : `cible(e, y, w)` remplit la ligne d'étiquettes `y` et le masque `w` (D_SORTIE valeurs, 0 = pas d'étiquette)."""
    TYPES_ECHANTILLON[nom] = cible


def _ligne(e: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y, w = np.zeros(D_SORTIE), np.zeros(D_SORTIE)
    TYPES_ECHANTILLON[e["type"]](e, y, w)
    return np.asarray(e["x"], dtype=float), y, w


def _croitre(modele: Modele, maintenant: float) -> None:
    """Élargit le réseau actif d'un palier (sans perte) ; à la limite de paramètres, le gèle et ouvre un réseau complémentaire."""
    p, c = modele.p, modele.actif
    while c.reseau.n_couches < p["couches_residuelles"]:   # profondeur demandée : ajoutée au MÊME réseau, sans changer ce qu'il a appris
        h = c.reseau.largeur
        if c.reseau.nb_parametres() + h * h + h > p["plafond_parametres"]:
            break   # la couche ne tient pas sous le plafond à cette largeur
        c.reseau.ajouter_couche()
        c.sale = True
    paliers = rn.largeurs_permises(dn.D, D_SORTIE, p["plafond_parametres"], p["largeur_depart"], c.reseau.n_couches)
    niveau = int(math.log2(1 + c.meta["n_abs"] / p["echantillons_par_niveau"]))
    if niveau < len(paliers):
        if paliers[niveau] > c.reseau.largeur:
            c.reseau.elargir(paliers[niveau])
        return
    c.meta["gele"], c.sale = True, True
    c.X, c.Y, c.W = c.X[:0], c.Y[:0], c.W[:0]   # un réseau gelé n'apprend plus : plus de rejeu à garder
    suivant = nouveau_coeur(c.meta["rang"] + 1, maintenant, p)
    for k in ("empreinte", "echelle_revenu", "dernier_pas_ts"):
        suivant.meta[k] = c.meta[k]
    modele.coeurs.append(suivant)


def affiner(modele: Modele, echantillons: list[dict], maintenant: float) -> None:
    """FINE-TUNING : absorbe les nouveaux échantillons dans le réseau actif (quelques pas d'Adam sur le lot + rejeu), sur
    le RÉSIDU des réseaux gelés, puis le fait grandir si nécessaire."""
    p, c = modele.p, modele.actif
    connus = [e for e in echantillons if e.get("type") in TYPES_ECHANTILLON]
    if len(connus) != len(echantillons):   # type inconnu de cette version : ignoré, jamais appris à la place d'un autre
        c.meta["n_ignores"] = c.meta.get("n_ignores", 0) + len(echantillons) - len(connus)
        c.sale = True
    echantillons = connus
    if not echantillons:
        return
    lignes = [_ligne(e) for e in echantillons]
    c.X = np.vstack([c.X, [l[0] for l in lignes]])[-p["replay_max"]:]
    c.Y = np.vstack([c.Y, [l[1] for l in lignes]])[-p["replay_max"]:]
    c.W = np.vstack([c.W, [l[2] for l in lignes]])[-p["replay_max"]:]
    nouveaux = min(len(lignes), len(c.X))
    c.meta["cles"] = (c.meta["cles"] + [e["cle"] for e in echantillons])[-p["cles_max"]:]
    c.meta["n_abs"] += len(echantillons)
    c.meta["n_recomp"] += sum(1 for e in echantillons if e["type"] == "recompense")
    c.meta["n_elan"] += sum(1 for e in echantillons if e["type"] == "elan")
    for e in echantillons:
        if e["type"] not in ("recompense", "elan"):
            par_type = c.meta.setdefault("n_par_type", {})
            par_type[e["type"]] = par_type.get(e["type"], 0) + 1
    residu = c.Y - modele.predire(c.X, jusqu_a=-1)   # réseaux gelés + apport a priori (même à un seul réseau) : le réseau actif apprend le reste
    rng = np.random.default_rng(zlib.crc32(echantillons[-1]["cle"].encode()) + c.meta["n_abs"])
    nouveaux_idx = np.arange(len(c.X) - nouveaux, len(c.X))
    anciens = np.arange(0, len(c.X) - nouveaux)
    lr = max(p["lr_min"], p["lr"] / math.sqrt(1 + c.meta["n_abs"] / 200))
    perte = None
    for _ in range(p["pas_par_passage"]):
        extra = rng.choice(anciens, min(len(anciens), p["lot_replay"]), replace=False) if len(anciens) else anciens
        idx = np.concatenate([nouveaux_idx, extra])
        perte = c.reseau.pas(c.X[idx], residu[idx], c.W[idx], lr)
    c.meta["perte"] = None if perte is None else round(perte, 5)
    c.meta["maj_ts"], c.sale = maintenant, True
    _croitre(modele, maintenant)


def entrainer_pas(modele: Modele, observations: list[dict], vue: dn.Vue | None, maintenant: float) -> list[dict]:
    """UN passage paresseux. `observations` = conséquences mesurées de décisions [{cle, x, avant, apres, revenu}] ; `vue` =
    structure actuelle (état fusionné + toile). Construit automatiquement les échantillons nouveaux (décisions, tables,
    KPI), affine le réseau, et rend les observations absorbées avec leur récompense (None = sans signal).
    Sans effet si le dernier passage est trop récent."""
    p = modele.p
    if not peut_entrainer(modele, maintenant):
        return []
    actif = modele.actif
    absorbees, echantillons = [], []
    for o in observations[: p["max_par_pas"]]:
        r, actif.meta["echelle_revenu"] = recompense(o["avant"], o["apres"], o.get("revenu"), actif.meta["echelle_revenu"], p)
        x = dn.completer(o["x"]) if o.get("x") else []
        if r is not None and len(x) == dn.D:
            echantillons.append(dn.echantillon_recompense(o["cle"], x, r))
        absorbees.append({**o, "recompense": r})
    if vue is not None:
        # Un quart du pas est réservé aux signaux de COMMUNICATION (rythme de la messagerie, jamais le contenu), pour que la structure ne les affame pas.
        echantillons += dn.echantillons_communication(vue, modele.cles(), max(1, p["max_par_pas"] // 4))
        echantillons += dn.echantillons_structure(vue, modele.cles(), max(0, p["max_par_pas"] - len(echantillons)))
        actif.meta["empreinte"] = dn.empreinte(vue.etat)
    affiner(modele, echantillons, maintenant)
    actif = modele.actif   # peut avoir changé (réseau gelé, nouveau réseau ouvert)
    actif.meta["dernier_pas_ts"], actif.sale = maintenant, True
    return absorbees


# ── Fichiers .agentgy ───────────────────────────────────────────────────────
class ModeleTropRecent(ValueError):
    """Fichier écrit par une version PLUS RÉCENTE du logiciel : on ne sait pas le lire, mais il n'est ni invalide ni à écarter."""


def _signer(cle: bytes, corps: bytes, version: int = _VERSION) -> bytes:
    return hmac.new(cle, _MAGIE + bytes([version]) + corps, hashlib.sha256).digest()


def serialiser(coeur: Coeur, cle: bytes) -> bytes:
    tableaux = coeur.reseau.tableaux()
    tableaux.update({k: getattr(coeur, k).astype(np.float32) for k in ("X", "Y", "W")})
    # Le fichier porte le NOM de chacune de ses colonnes et de ses têtes : c'est ce qui permet à toute version ultérieure de réaligner les poids.
    meta = {**coeur.meta, "format": _VERSION}
    if coeur.reseau.d_in == dn.D:   # un coeur qui n'est pas (encore) dans la disposition courante ne s'en réclame pas : relu comme un fichier ancien
        meta["entrees"] = dn.noms_entrees()
    if coeur.reseau.d_out == D_SORTIE:
        meta["sorties"] = list(NOMS_SORTIES)
    tableaux["meta"] = np.frombuffer(json.dumps(meta, separators=(",", ":")).encode("utf-8"), dtype=np.uint8)
    corps = rn.vers_octets(tableaux)
    return _MAGIE + bytes([_VERSION]) + _signer(cle, corps) + corps


def _disposition_fichier(meta: dict, cle_meta: str, actuelles: list, taille: int) -> list:
    """Noms des colonnes (ou têtes) du fichier. Fichier d'avant la mémorisation des noms : sa disposition est un PRÉFIXE de l'actuelle
    (on n'a toujours ajouté qu'en fin) ; plus long que l'actuelle, il vient d'une version plus récente."""
    noms = meta.get(cle_meta)
    if noms is None:
        if taille > len(actuelles):
            raise ModeleTropRecent(_i18n_t("rg2.modele_fantome.1"))
        return actuelles[:taille]
    if not isinstance(noms, list) or len(noms) != taille:
        raise ValueError(_i18n_t("rg2.modele_fantome.2"))
    return noms


def deserialiser(octets: bytes, cle: bytes) -> Coeur:
    """Lève ValueError si l'en-tête, la version, la signature, le contenu ou les dimensions sont invalides, et ModeleTropRecent (sous-classe)
    si le fichier vient d'une version plus récente. Un fichier d'une version ou d'une disposition antérieure est MIGRÉ en mémoire
    (`coeur.migre`) : aucun poids n'est perdu."""
    tete = len(_MAGIE) + 1
    if len(octets) < tete + 32 or octets[: len(_MAGIE)] != _MAGIE:
        raise ValueError(_i18n_t("engine/modele_fantome.py:g:380:26"))
    version = octets[len(_MAGIE)]
    if version > _VERSION:
        raise ModeleTropRecent(_i18n_t("rg2.modele_fantome.1"))
    if version not in _VERSIONS_LISIBLES:
        raise ValueError(_i18n_t("engine/modele_fantome.py:g:380:26"))
    signature, corps = octets[tete: tete + 32], octets[tete + 32:]
    if not hmac.compare_digest(signature, _signer(cle, corps, version)):
        raise ValueError(_i18n_t("engine/modele_fantome.py:g:383:26"))
    z = rn.depuis_octets(corps)
    try:
        meta = json.loads(np.array(z["meta"], dtype=np.uint8).tobytes().decode("utf-8"))
        reseau = _moteur.Reseau.depuis_tableaux(z)
        X, Y, W = (np.array(z[k], dtype=float) for k in ("X", "Y", "W"))
    except (KeyError, UnicodeDecodeError) as exc:
        raise ValueError(_gt("rg8.msg.043")) from exc
    if not isinstance(meta, dict) or X.ndim != 2 or Y.ndim != 2 or W.shape != Y.shape or len(X) != len(Y):
        raise ValueError(_gt("rg8.msg.042"))
    entrees_actuelles, sorties_actuelles = dn.noms_entrees(), list(NOMS_SORTIES)
    entrees = _disposition_fichier(meta, "entrees", entrees_actuelles, reseau.d_in)
    sorties = _disposition_fichier(meta, "sorties", sorties_actuelles, reseau.d_out)
    if X.shape[1] != len(entrees) or Y.shape[1] != len(sorties):
        raise ValueError(_gt("rg8.msg.042"))
    migre = version != _VERSION
    try:
        if reseau.remapper_entrees(entrees, entrees_actuelles):   # entrées ajoutées / déplacées / retirées : réalignées par leur nom
            X, migre = rn.reordonner_colonnes(X, entrees, entrees_actuelles), True
        if reseau.remapper_sorties(sorties, sorties_actuelles):   # idem pour les têtes de sortie
            Y, W, migre = (rn.reordonner_colonnes(Y, sorties, sorties_actuelles), rn.reordonner_colonnes(W, sorties, sorties_actuelles), True)
    except ValueError as exc:   # noms en double, etc. : fichier incohérent
        raise ValueError(_gt("rg5.msg.020")) from exc
    if reseau.d_in != dn.D or reseau.d_out != D_SORTIE or X.shape[1:] != (dn.D,) or Y.shape[1:] != (D_SORTIE,) or W.shape != Y.shape or len(X) != len(Y):
        raise ValueError(_gt("rg8.msg.042"))
    # Clés de méta ajoutées depuis l'écriture du fichier : valeur par défaut (jamais de KeyError). `rang` et `apport_externe` : voir _meta_defaut.
    coeur = Coeur(reseau, {**_meta_defaut(), **meta}, X, Y, W)
    coeur.migre = coeur.sale = migre   # un fichier migré est réécrit au prochain enregistrement, dans la disposition courante
    coeur.empreinte_fichier = hashlib.sha256(octets).hexdigest()[:10]
    return coeur


def ecrire(chemin: str, coeur: Coeur, cle: bytes) -> None:
    """Écriture atomique (fichier temporaire + remplacement), droits 0600."""
    dossier = os.path.dirname(chemin) or "."
    os.makedirs(dossier, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dossier, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(serialiser(coeur, cle))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, chemin)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def lire(chemin: str, cle: bytes) -> Coeur:
    with open(chemin, "rb") as f:
        return deserialiser(f.read(), cle)


def chemin_coeur(base: str, rang: int) -> str:
    """`base` = <dossier>/entreprise_<id> ; rang 0 -> base.agentgy, rang n -> base_n.agentgy."""
    return f"{base}{EXTENSION}" if rang == 0 else f"{base}_{rang}{EXTENSION}"


def _copie_avant_maj(chemin: str, coeur: Coeur) -> None:
    """Copie de sécurité du fichier d'origine avant qu'une migration ne le réécrive : `<fichier>.avant_maj_<empreinte>`, créée une seule
    fois (le nom dépend du contenu d'origine, un rechargement ne la duplique pas). Échec silencieux : la migration en mémoire reste valable."""
    cible = f"{chemin}.avant_maj_{coeur.empreinte_fichier}"
    if os.path.exists(cible):
        return
    try:
        shutil.copy2(chemin, cible)
        os.chmod(cible, 0o600)
    except OSError:
        pass


def charger(base: str, cle: bytes, maintenant: float, parametres: dict | None = None) -> Modele:
    """Modèle lu depuis ses fichiers ; noyau neuf s'il n'existe pas. Un fichier altéré est mis de côté (`.invalide`) avec
    tous les réseaux qui le suivent (ils corrigeaient son résidu) : le modèle reprend au dernier réseau sain.
    Un fichier d'une version ou d'une disposition ANTÉRIEURE est migré (poids conservés, copie `.avant_maj_*` à côté) ; un fichier d'une
    version PLUS RÉCENTE n'est jamais écarté ni écrasé : le modèle est alors en lecture seule."""
    coeurs, trop_recent = [], False
    for rang in range(256):
        chemin = chemin_coeur(base, rang)
        try:
            coeur = lire(chemin, cle)
        except FileNotFoundError:
            break
        except ModeleTropRecent:
            trop_recent = True
            break
        except (ValueError, OSError):
            for suivant in range(rang, 256):
                if os.path.exists(chemin_coeur(base, suivant)):
                    try:
                        os.replace(chemin_coeur(base, suivant), chemin_coeur(base, suivant) + ".invalide")
                    except OSError:
                        pass
            break
        coeur.meta.setdefault("rang", rang)
        if coeur.migre:
            _copie_avant_maj(chemin, coeur)
        coeurs.append(coeur)
    if not coeurs:
        coeurs = [nouveau_coeur(0, maintenant, parametres)]
    elif coeurs[-1].meta["gele"] and not trop_recent:
        coeurs.append(nouveau_coeur(len(coeurs), maintenant, parametres))
    modele = Modele(coeurs, parametres)
    modele.lecture_seule = trop_recent
    return modele


def sauvegarder(modele: Modele, base: str, cle: bytes) -> None:
    """Réécrit uniquement les réseaux modifiés. Sans effet en lecture seule (un fichier plus récent que le logiciel n'est jamais écrasé)."""
    if modele.lecture_seule:
        return
    for c in modele.coeurs:
        if c.sale:
            ecrire(chemin_coeur(base, c.meta["rang"]), c, cle)
            c.sale = False
