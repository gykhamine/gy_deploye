"""Exceptions dediees a la greffe engine <-> module IA (ia_integration.py)."""


class DatasetError(Exception):
    """La resolution des tables source en ndarray a echoue (formes
    incompatibles, colonnes manquantes, tables desalignees, ...)."""


class EntrainementError(Exception):
    """L'entrainement ou l'inference a echoue cote module IA."""


class ContrainteModeleError(DatasetError):
    """Une contrainte propre a l'algorithme choisi n'est pas respectee
    (parametre invalide, cible incompatible, donnees inexploitables...).
    Herite de DatasetError : tous les handlers existants la traitent deja
    comme une erreur de configuration (statut \"erreur\" + message clair)."""
