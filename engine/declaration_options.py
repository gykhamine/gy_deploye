"""DÉCLARATION D'OPTIONS : le langage naturel n'est qu'un LIBELLÉ, le modèle ne lit que des champs déclarés.

Une option déclarée = {libelle, levier, cibles, effort}.
  * libelle — texte libre écrit pour l'utilisateur (clarté de l'écran, de l'historique, du journal). Il n'est JAMAIS
              interprété : aucune extraction de mots, aucune analyse de phrase. Deux libellés différents avec la même
              déclaration donnent exactement la même évaluation.
  * levier  — nature du geste, choisie dans un vocabulaire FERMÉ (LEVIERS). Chaque levier a son TRADUCTEUR intégré : la liste
              des catégories de recommandations (engine/recommandations.py) qu'il traite réellement. C'est ce qui décide
              quels risques observés dans les données comptent comme « traités » par l'option.
  * cibles  — objets de la toile visés (« table:1 », « kpi:3 »...) : au moins un, reconnu dans la toile.
  * effort  — 1 à 5.
Ce que le modèle peut réellement tirer d'une option, c'est donc : les risques mesurés sur ses cibles ET de la bonne catégorie,
l'importance et l'élan de ces cibles, la qualité de leurs données, la possibilité de mesurer l'effet (KPI) et l'effort.
Tout le reste (texte libre) est ignoré par construction.

Module PUR (aucun import Django) : testable avec des dictionnaires.
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import tf

LEVIERS = {
    "fiabiliser_donnees": {"libelle": td("engine/declaration_options.py:g:22:39"),
                           "categories": ("manque", "anomalie", "integrite", "obsolescence")},
    "relancer_activite": {"libelle": td("engine/declaration_options.py:g:24:38"),
                          "categories": ("diminution", "tendance")},
    "repartir_charge": {"libelle": td("engine/declaration_options.py:g:26:36"),
                        "categories": ("concentration", "surplus")},
    "relier_objets": {"libelle": td("engine/declaration_options.py:g:28:34"),
                      "categories": ("isolement", "couverture")},
    "atteindre_objectif": {"libelle": td("engine/declaration_options.py:g:30:39"),
                           "categories": ("objectif",)},
    "corriger_dysfonctionnement": {"libelle": td("engine/declaration_options.py:g:32:47"),
                                   "categories": ("dysfonctionnement",)},
    "developper": {"libelle": td("engine/declaration_options.py:g:34:31"),
                   "categories": ()},
}
MAX_LIBELLE = 80

# Critères (règles de engine/recommandations.py) qui décrivent une OPPORTUNITÉ, pas un problème : ils ne comptent jamais comme
# risque à traiter ; ils alimentent le levier « developper » (investir là où ça progresse).
OPPORTUNITES = frozenset({"tendance_hausse_activite", "tendance_kpi_favorable"})


def catalogue() -> list[dict]:
    """Vocabulaire proposé à l'interface : [{id, libelle}]."""
    return [{"id": k, "libelle": v["libelle"]} for k, v in LEVIERS.items()]


def categories_du_levier(levier: str) -> frozenset:
    return frozenset(LEVIERS[levier]["categories"])


def traduire(declaration, position: int = 1) -> tuple[dict | None, str | None]:
    """Déclaration brute -> (option propre sans les cibles résolues, erreur).
    `libelle` (ou `nom`, ancien nom du champ) est nettoyé mais jamais interprété. Le levier est imposé."""
    d = declaration if isinstance(declaration, dict) else {}
    libelle = str(d.get("libelle") or d.get("nom") or "").strip()[:MAX_LIBELLE]
    if not libelle:
        return None, tf("engine/declaration_options.py:g:58:22", v1=position)
    levier = str(d.get("levier") or "").strip()
    if levier not in LEVIERS:
        return None, tf("engine/declaration_options.py:g:61:22", v1=libelle, v2=', '.join(LEVIERS))
    try:
        effort = min(5, max(1, int(d.get("effort", 3))))
    except (TypeError, ValueError):
        effort = 3
    return {"nom": libelle, "levier": levier, "effort": effort, "cibles_brutes": d.get("cibles") or []}, None


def levier_de_categorie(categorie: str) -> str | None:
    """Traducteur inverse : le levier qui traite cette catégorie de recommandation (chaque catégorie n'en a qu'un)."""
    return next((k for k, v in LEVIERS.items() if categorie in v["categories"]), None)


def levier_de_critere(code: str | None, categorie: str | None) -> str | None:
    """Levier d'un critère : « developper » pour une opportunité, sinon le traducteur inverse de sa catégorie."""
    return "developper" if code in OPPORTUNITES else levier_de_categorie(categorie)


def libelle_court(levier: str) -> str:
    """« Fiabiliser les données » (le libellé du catalogue sans sa parenthèse) : base des libellés générés."""
    return LEVIERS[levier]["libelle"].split(" (")[0]
