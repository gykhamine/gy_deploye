"""
Greffe entre le moteur de tables dynamiques (engine/models.py) et le module
externe `IA` (racine du projet, a cote de manage.py — 100% Python/numpy,
voir IA/README.md).

Principe : aucune copie de donnees. Une TableDynamique EST un dataset — ses
enregistrements sont resolus en ndarray numpy a la volee au moment de
l'entrainement/l'inference. Un champ Matrice/Vecteur stocke deja un ndarray
(en JSON dans le meme champ texte EAV, voir ChampDynamique.forme_tenseur) ;
une table a UNE SEULE colonne est traitee comme un jeu de documents JSON
(un document par ligne), pour les modeles qui attendent du texte/JSON brut
plutot qu'une matrice de features (ex: slm).

Ce module ne reecrit rien de l'existant : il consomme TableDynamique /
ChampDynamique / Enregistrement tels quels, et les vues de views.py se
contentent d'appeler les fonctions ci-dessous en reutilisant leurs propres
helpers (_entreprise_courante, _interdit_lecteur, etc.).
"""
from engine.i18n import tl
import inspect
import io
import json
import os
import re
import shutil
import tempfile
import time

import numpy as np
import requests
from django.conf import settings as dj_settings
from django.utils import timezone

from ..exceptions_ia import ContrainteModeleError, DatasetError, EntrainementError  # noqa: F401 (re-export commode)
from ..contraintes_ia import (descriptions as descriptions_contraintes, verifier_dataset, verifier_volume_entrainement,  # noqa: F401
                              verifier_parametres, TYPES_SANS_CIBLE, famille_sans_cible, LIMITE_LIGNES_ENTRAINEMENT)
from ..journal_ia import Journal  # noqa: F401
from ..encodage_champs import TYPES_SUPPLEMENTAIRES as _TYPES_ENCODES_PAR_CHAMP  # noqa: F401

# Chargement paresseux de Pillow : uniquement necessaire si des champs
# image/url sont reellement utilises comme source d'entrainement.
try:
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover - Pillow absent de l'environnement
    _PILImage = None

# Chargement paresseux de pydub : permet de decoder MP3/OGG/M4A/... en plus
# du WAV natif (module stdlib `wave`). pydub s'appuie sur le binaire ffmpeg
# installe au niveau systeme (pas seulement le paquet pip) — si ffmpeg est
# absent, pydub leve son erreur a l'usage et on retombe sur `wave` (WAV
# uniquement). pyaudio (capture micro en temps reel) n'est pas utilise ici :
# l'entrainement consomme des fichiers/URLs deja enregistres, pas un flux live.
try:
    from pydub import AudioSegment as _PydubAudioSegment
except ImportError:  # pragma: no cover - pydub absent de l'environnement
    _PydubAudioSegment = None

# Chargement paresseux d'OpenCV (deja utilise par reports.py ailleurs dans le
# projet, donc dependance existante) : uniquement necessaire si des champs
# video sont reellement utilises comme source d'entrainement.
try:
    import cv2 as _cv2
except ImportError:  # pragma: no cover - opencv absent de l'environnement
    _cv2 = None

# Vectorisation des images et des sons AVANT l'entrainement (engine/vectorisation_media.py) : le modele apprend sur des
# caracteristiques (contours, ombres, differences et densites de couleurs ; bandes de frequences, timbre, enveloppe) et non
# sur des pixels ou des echantillons bruts. Active pendant un entrainement (positionnee par lancer_entrainement) ; les
# modeles deja entraines sur des donnees brutes continuent d'etre lus en brut a l'inference (structure_entree sans cle
# "vectorisation").
import contextvars
from .. import vectorisation_media as _vecto
_VECTORISATION = contextvars.ContextVar("vectorisation_media", default=False)

# Types de modeles qui apprennent sur des vecteurs de caracteristiques. Restent sur les donnees brutes : cnn (convolue
# sur la grille de pixels), gan / ldm (generent des images), rnn / transformer (sequences), slm (texte).
TYPES_MODELES_VECTORISES = (
    "image_classifier", "speech_classifier", "mlp",
    "sklearn_rf", "sklearn_gbm", "sklearn_svm", "sklearn_knn", "sklearn_lr", "sklearn_nb", "sklearn_dt",
    "sklearn_ridge", "sklearn_lasso", "sklearn_kmeans", "sklearn_dbscan", "sklearn_hierarchique", "sklearn_gmm",
    "sklearn_acp", "sklearn_ica", "sklearn_isoforest", "sklearn_ocsvm", "sklearn_lof",
)

# Forme par defaut appliquee quand un champ image/url n'a pas de
# `forme_tenseur` configure (options_choix vide) : 128 pixels a plat en
# niveaux de gris, ce qui correspond au `feature_dim` par defaut de
# train_image_classifier (voir IA/train/vision.py).
_FORME_IMAGE_DEFAUT = (16, 8)

# Forme par defaut pour un champ video sans forme_tenseur configure :
# 8 frames en niveaux de gris, 16x8 (meme convention que _FORME_IMAGE_DEFAUT).
_FORME_VIDEO_DEFAUT = (8, 16, 8)

# Types de champ convertibles directement en float() sans perte de sens.
# Tout le reste (texte, choix, relation, date/heure brutes, telephone,
# couleur, son, video, fichier generique...) doit etre explicitement exclu
# ou converti par l'utilisateur plutot que d'etre silencieusement mis a 0.
_TYPES_NUMERIQUES_DIRECTS = (
    "nombre", "decimal", "monnaie", "pourcentage", "note", "constante",
    "barre_progression", "booleen", "switch",
)

# Types de champ "texte" qu'on sait vectoriser automatiquement en TF-IDF
# (voir _construire_vocabulaire_texte/_vectoriser_texte_tfidf) quand c'est
# l'UNIQUE colonne d'entree selectionnee. Contrairement aux types ci-dessus,
# ce n'est PAS une conversion directe : un vocabulaire est construit depuis
# le corpus d'entrainement puis fige (persiste dans ModeleIA.structure_entree)
# pour que l'inference vectorise une nouvelle ligne exactement pareil.
_TYPES_TEXTE_VECTORISABLES = ("texte", "texte_long")

# Nombre max de termes retenus dans le vocabulaire TF-IDF (les plus
# frequents dans le corpus d'entrainement, cf. _construire_vocabulaire_texte).
_TAILLE_VOCAB_TEXTE_DEFAUT = 200

# Tokenisation : mots alphabetiques Unicode (pas de chiffres/ponctuation
# isoles), longueur >= 2 pour ecarter le bruit a une lettre.
_RE_TOKEN_TEXTE = re.compile(r"[^\W\d_]+", re.UNICODE)


LIBELLES_TYPES = dict(
    mlp="MLP — classification tabulaire (colonnes numeriques, choix, dates, texte)",
    rnn="RNN — sequences",
    cnn="CNN — images / grilles",
    transformer="Transformer — sequences (attention)",
    gan="GAN — generation adversariale",
    ldm=tl("engine/ia_integration/_header.py:g:107:9"),
    slm=tl("engine/ia_integration/_header.py:g:108:9"),
    slm_texte=tl("engine/ia_integration/_header.py:g:109:15"),
    image_classifier=tl("engine/ia_integration/_header.py:g:110:22"),
    speech_classifier="Classifieur audio",
    # scikit-learn
    sklearn_rf="sklearn — Random Forest (classification)",
    sklearn_gbm="sklearn — Gradient Boosting (classification)",
    sklearn_svm="sklearn — SVM (classification)",
    sklearn_knn="sklearn — k-Nearest Neighbors (classification)",
    sklearn_lr="sklearn — Regression Logistique (classification)",
    sklearn_nb="sklearn — Naive Bayes Gaussien (classification)",
    sklearn_dt=tl("engine/ia_integration/_header.py:g:119:16"),
    sklearn_ridge="sklearn — Ridge (regression L2)",
    sklearn_lasso="sklearn — Lasso (regression L1)",
    # scikit-learn non supervise (sans colonne cible)
    sklearn_kmeans=tl("ia.nonsup.type_kmeans"),
    sklearn_dbscan=tl("ia.nonsup.type_dbscan"),
    sklearn_hierarchique=tl("ia.nonsup.type_hierarchique"),
    sklearn_gmm=tl("ia.nonsup.type_gmm"),
    sklearn_acp=tl("ia.nonsup.type_acp"),
    sklearn_ica=tl("ia.nonsup.type_ica"),
    sklearn_isoforest=tl("ia.nonsup.type_isoforest"),
    sklearn_ocsvm=tl("ia.nonsup.type_ocsvm"),
    sklearn_lof=tl("ia.nonsup.type_lof"),
)

# Tache -> classes attendues, pour le type 'slm_texte' : la colonne cible
# choisie par l'utilisateur doit contenir EXACTEMENT ces libelles (voir
# _resoudre_dataset_texte_slm). Duplique volontairement IA.train.slm.SLM_TACHES
# (juste les classes, pas les phrases par defaut) pour eviter d'importer le
# module IA au chargement de ce fichier (import paresseux, cf _trainer_module).
_PARAMS_MASQUES = {"X", "y", "dataset", "dataset_target", "save_path", "on_epoch",
                    "save", "callbacks", "tensor_bits", "init_model", "task"}

# Parametres necessitant une saisie sous forme de liste (texte "64,32" ->
# [64, 32]) plutot qu'un simple nombre/texte.
_PARAMS_LISTE_ENTIERS = {"hidden_sizes", "hidden_layers", "generator_layers",
                          "discriminator_layers"}
_PARAMS_TUPLE_ENTIERS = {"input_shape", "kernel_shape"}


_TYPES_SKLEARN_TABULAIRE = (_TYPES_NUMERIQUES_DIRECTS + ("vecteur", "matrice") + _TYPES_TEXTE_VECTORISABLES
                            + tuple(t for t in _TYPES_ENCODES_PAR_CHAMP if t not in _TYPES_TEXTE_VECTORISABLES))

TYPES_ENTREE_COMPATIBLES = {
    "mlp":               _TYPES_SKLEARN_TABULAIRE,
    "rnn":               _TYPES_NUMERIQUES_DIRECTS + ("vecteur", "matrice"),
    "transformer":       _TYPES_NUMERIQUES_DIRECTS + ("vecteur", "matrice"),
    "cnn":               ("vecteur", "matrice", "url", "image", "video"),
    "image_classifier":  ("vecteur", "matrice", "url", "image", "video"),
    "speech_classifier": ("son",),
    "gan":               _TYPES_NUMERIQUES_DIRECTS + ("vecteur", "matrice", "url", "image", "video"),
    "ldm":               ("vecteur", "matrice", "url", "image", "video"),
    "slm":               ("texte", "texte_long", "json"),
    "slm_texte":         ("texte", "texte_long"),
    # scikit-learn : colonnes tabulaires numeriques + texte vectorise
    "sklearn_rf":    _TYPES_SKLEARN_TABULAIRE,
    "sklearn_gbm":   _TYPES_SKLEARN_TABULAIRE,
    "sklearn_svm":   _TYPES_SKLEARN_TABULAIRE,
    "sklearn_knn":   _TYPES_SKLEARN_TABULAIRE,
    "sklearn_lr":    _TYPES_SKLEARN_TABULAIRE,
    "sklearn_nb":    _TYPES_SKLEARN_TABULAIRE,
    "sklearn_dt":    _TYPES_SKLEARN_TABULAIRE,
    "sklearn_ridge": _TYPES_SKLEARN_TABULAIRE,
    "sklearn_lasso": _TYPES_SKLEARN_TABULAIRE,
    # scikit-learn non supervise : memes colonnes d'entree que les modeles tabulaires, aucune cible (cf. TYPES_SANS_CIBLE)
    **{t: _TYPES_SKLEARN_TABULAIRE for t in TYPES_SANS_CIBLE},
}


_TYPES_CIBLE_CATEGORIELLE = ("choix_unique", "choix_multiple")

_TYPES_SKLEARN_CIBLE = _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE + ("vecteur", "matrice")
_TYPES_SKLEARN_CIBLE_REGRESSION = _TYPES_NUMERIQUES_DIRECTS + ("vecteur", "matrice")

TYPES_SORTIE_COMPATIBLES = {
    "mlp":               _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE + ("vecteur", "matrice"),
    "rnn":               _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE + ("vecteur", "matrice"),
    "transformer":       _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE + ("vecteur", "matrice"),
    "cnn":               _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE,
    "image_classifier":  _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE,
    "speech_classifier": _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE,
    "gan":               _TYPES_NUMERIQUES_DIRECTS + _TYPES_CIBLE_CATEGORIELLE,
    "slm_texte":         _TYPES_CIBLE_CATEGORIELLE + _TYPES_TEXTE_VECTORISABLES,
    # scikit-learn classification
    "sklearn_rf":    _TYPES_SKLEARN_CIBLE,
    "sklearn_gbm":   _TYPES_SKLEARN_CIBLE,
    "sklearn_svm":   _TYPES_SKLEARN_CIBLE,
    "sklearn_knn":   _TYPES_SKLEARN_CIBLE,
    "sklearn_lr":    _TYPES_SKLEARN_CIBLE,
    "sklearn_nb":    _TYPES_SKLEARN_CIBLE,
    "sklearn_dt":    _TYPES_SKLEARN_CIBLE,
    # scikit-learn regression
    "sklearn_ridge": _TYPES_SKLEARN_CIBLE_REGRESSION,
    "sklearn_lasso": _TYPES_SKLEARN_CIBLE_REGRESSION,
}




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (__init__.py,
# parties suivantes). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
