"""RÉGLAGES DE LA MESURE DES CRITÈRES : les constantes de forme qui ne sont pas des seuils de règle, configurables par entreprise.

Six échelles de `criteres.ECHELLES_DEFAUT` (deux exposants de saturation, l'exposant d'isolement, le plafond de l'écart à l'objectif,
et les deux échelles que `criteres.calibrer` estime seule tant qu'on ne les fixe pas) + deux réglages de l'utilité a priori du modèle
(`modele_fantome.PARAMETRES`). Une valeur vide = valeur par défaut (ou calibrage automatique pour les deux échelles estimées).

  DEFINITIONS   : description de chaque réglage pour l'interface (clé, libellé, aide, défaut, bornes, groupe)
  normaliser(b) : ({clé: valeur} valides, erreurs) ; une valeur hors bornes est refusée, jamais corrigée en silence
  echelles(r) / parametres_modele(r) : la part de `r` destinée à la mesure / au modèle

Module PUR (aucun import Django) : testable avec des dictionnaires.
"""
from __future__ import annotations
from engine.i18n import tf, tl

from . import criteres as _cr
from . import modele_fantome as _mf

# Bornes de l'utilité a priori : (bas, haut). poids_critere : apport d'un critère d'intensité 1 ; plafond_criteres : plafond de l'apport total.
_BORNES_MODELE = {"poids_critere": (0.0, 0.2), "plafond_criteres": (0.0, 0.6)}

_TEXTES = {
    "exposant_vide": (tl("engine/reglages_criteres.py:g:22:23"), tl("engine/reglages_criteres.py:g:22:50")),
    "exposant_concentration": (tl("engine/reglages_criteres.py:g:23:32"), tl("engine/reglages_criteres.py:g:23:64")),
    "exposant_isolement": (tl("engine/reglages_criteres.py:g:24:28"), tl("engine/reglages_criteres.py:g:24:59")),
    "plafond_objectif": (tl("engine/reglages_criteres.py:g:25:26"), tl("engine/reglages_criteres.py:g:25:58")),
    "aberrants_taux": (tl("engine/reglages_criteres.py:g:26:24"), tl("engine/reglages_criteres.py:g:26:60")),
    "suivi_lignes": (tl("engine/reglages_criteres.py:g:27:22"), tl("engine/reglages_criteres.py:g:27:64")),
    "poids_critere": (tl("engine/reglages_criteres.py:g:28:23"), tl("engine/reglages_criteres.py:g:28:55")),
    "plafond_criteres": ("Apport a priori : plafond", tl("engine/reglages_criteres.py:g:29:55")),
}
_AUTO = ("aberrants_taux", "suivi_lignes")   # vides = calibrées automatiquement


def _defaut(cle):
    return _cr.ECHELLES_DEFAUT[cle] if cle in _cr.ECHELLES_DEFAUT else _mf.PARAMETRES[cle]


def _bornes(cle):
    return _cr._BORNES_ECHELLES[cle] if cle in _cr._BORNES_ECHELLES else _BORNES_MODELE[cle]


def definitions() -> list[dict]:
    """Description des réglages pour l'interface."""
    sortie = []
    for cle, (libelle, aide) in _TEXTES.items():
        bas, haut = _bornes(cle)
        sortie.append({"cle": cle, "libelle": libelle, "aide": aide, "defaut": _defaut(cle), "min": bas, "max": haut,
                       "groupe": "modele" if cle in _BORNES_MODELE else "mesure", "auto": cle in _AUTO,
                       "entier": isinstance(_defaut(cle), int)})
    return sortie


def normaliser(brut) -> tuple[dict, list[str]]:
    """Réglages saisis -> (valides, erreurs). Vide/absent = défaut ; hors bornes ou non numérique = erreur ; clé inconnue ignorée."""
    propre, erreurs = _cr.normaliser_echelles(brut)
    for cle, (bas, haut) in _BORNES_MODELE.items():
        v = brut.get(cle) if isinstance(brut, dict) else None
        if v is None or v == "":
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not bas <= v <= haut:
            erreurs.append(tf("engine/reglages_criteres.py:g:61:28", v1=cle, v2=bas, v3=haut))
        else:
            propre[cle] = float(v)
    return propre, erreurs


def echelles(reglages: dict | None) -> dict:
    """Part des réglages qui concerne la mesure (`criteres`)."""
    return {k: v for k, v in (reglages or {}).items() if k in _cr.ECHELLES_DEFAUT}


def parametres_modele(reglages: dict | None) -> dict:
    """Part des réglages qui concerne l'utilité a priori du modèle (`modele_fantome`)."""
    return {k: v for k, v in (reglages or {}).items() if k in _BORNES_MODELE}
