"""engine/fonctions.py — Couche "Function" (cahier des charges Ontology,
section 3 : « FUNCTIONS »).

Ceci n'introduit AUCUN nouveau moteur de calcul : une Function ici est un
enregistrement mince autour d'une fonction Python déjà existante
(kpi.calculer_kpi, calculs.calculer_ligne, calculs.recalculer_table_calcul),
qui la rend identifiable/versionnée/documentée/traçable par son nom plutôt
que par son chemin d'import — sans dupliquer sa logique.

Distincte de la couche Action (actions.py) : une Function est un calcul
RÉUTILISABLE (lecture/calcul, pas de permission/audit propre) ; une Action
reste l'unique point de passage pour une opération soumise à
permission+audit+lignée (voir actions.executer_action). Architecture visée
(section 14 du cahier des charges) : Action = permission+audit+lignée
AUTOUR d'une Function = le calcul lui-même — d'où
actions._recalculer_kpi/_recalculer_table_calcul qui appellent désormais
`appeler(...)` ci-dessous plutôt que d'importer kpi.py/calculs.py
directement (même calcul, un seul point de vérité, appelable identiquement
par Django, l'agent, un rapport ou une future API).
"""
from __future__ import annotations
from engine.i18n import tl
from engine.i18n import tf

from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import calculs as _calculs
from . import kpi as _kpi
from engine.i18n import tf as _gtf  # noqa: E402


@dataclass(frozen=True)
class FonctionDescriptor:
    nom: str
    description: str
    fonction: Callable[..., Any]
    version: str = "1.0"
    types_objet: Optional[tuple[str, ...]] = None


_REGISTRE: dict[str, FonctionDescriptor] = {}


def declarer_fonction(nom, description, version="1.0", types_objet=None):
    """Décorateur : enregistre une fonction déjà existante comme Function
    nommée, sans changer son comportement pour ses appelants directs
    actuels (kpi.calculer_kpi reste appelable tel quel par ailleurs)."""
    def _decorateur(fonction):
        if nom in _REGISTRE:
            raise ValueError(tf("engine/fonctions.py:g:48:30", v1=nom))
        types = tuple(types_objet) if types_objet else None
        _REGISTRE[nom] = FonctionDescriptor(nom, description, fonction, version, types)
        return fonction
    return _decorateur


def appeler(nom: str, *args, **kwargs) -> Any:
    """Point de passage unique pour invoquer une Function par son nom —
    permet à l'agent/BI/rapport d'appeler le même calcul que Django sans
    connaître son module d'origine. Lève KeyError si `nom` est inconnu."""
    descripteur = _REGISTRE.get(nom)
    if descripteur is None:
        raise KeyError(_gtf("rg8.msg.037", nom=nom))
    return descripteur.fonction(*args, **kwargs)


def catalogue(type_objet: str | None = None) -> list[dict]:
    """Catalogue JSON-sérialisable des Functions, éventuellement filtré par
    type d'objet Ontology (même convention que actions.catalogue_pour_agent :
    une Function non catégorisée, types_objet=None, reste toujours incluse).
    Utilisé par ontologie_fiche.assembler_fiche_objet (fonctions_disponibles)."""
    resultat = []
    for descripteur in _REGISTRE.values():
        if (type_objet is not None and descripteur.types_objet is not None
                and type_objet not in descripteur.types_objet):
            continue
        resultat.append({
            "nom": descripteur.nom, "description": descripteur.description,
            "version": descripteur.version,
        })
    return resultat


# ── Functions déclarées : enveloppent les calculs déjà existants ──────────
declarer_fonction(
    "calculer_kpi",
    tl("engine/fonctions.py:g:86:5"),
    types_objet=("kpi",),
)(_kpi.calculer_kpi)

declarer_fonction(
    "calculer_ligne_calcul",
    tl("engine/fonctions.py:g:92:5"),
    types_objet=("calcul",),
)(_calculs.calculer_ligne)

declarer_fonction(
    "recalculer_table_calcul",
    tl("engine/fonctions.py:g:98:5"),
    types_objet=("calcul",),
)(_calculs.recalculer_table_calcul)
