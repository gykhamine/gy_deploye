"""Registre des règles périodiques : chaque famille déclare ses `REGLES` (numéro, fonction, arguments) ; ce fichier les assemble et les applique.

Un argument est soit une clé du contexte (str), soit un `Seuil` réglable lu dans siem_noyau.config()["seuils"].
Les règles 6, 7 et 23 sont évaluées en temps réel (temps_reel.py), celles du wallet (2, 12, 17, 20, 24, 27, 28, 34, 42, 47, 50) par le moteur continu (continu.py) : aucune ne figure ici.
Pour ajouter une règle : écrire la fonction pure dans le fichier de sa famille, l'inscrire dans la liste `REGLES` de ce fichier, ajouter ses tests.
"""
from . import administration, cles_api, compte, donnees, wallet
from .base import Seuil

FAMILLES = (compte, wallet, donnees, administration, cles_api)
REGLES = sorted((r for f in FAMILLES for r in f.REGLES), key=lambda r: r.numero)

# Clés du contexte construit par l'adaptateur (construire_contexte).
CONTEXTE = ("recents", "historique", "exports", "creations", "reveils", "derniere", "premieres", "profils", "tables")


def _valeur(v, ctx, seuils):
    return v.valeur(seuils) if isinstance(v, Seuil) else ctx[v]


def calculer_alertes(ctx: dict, seuils: dict, regles=None) -> list:
    """Applique les règles périodiques au contexte (calcul pur) ; l'ordre est celui des numéros. `regles` : autre liste (tests d'équivalence)."""
    alertes = []
    for r in (REGLES if regles is None else regles):
        alertes += r.fn(*[_valeur(a, ctx, seuils) for a in r.args], **{k: _valeur(v, ctx, seuils) for k, v in r.kwargs.items()})
    return alertes
