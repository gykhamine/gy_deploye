"""Palette des graphiques du rapport général : la MÊME que celle des pages d'analyse.

Miroir exact de static/engine/js/gy_couleur.js (PALETTE de 24 couleurs, mêmes teintes, même ordre) et de son compteur partagé :
  - suite(n)  = gyPaletteSuite(n) : les n couleurs qui SUIVENT celles déjà données (chaque graphique a donc d'autres couleurs que le précédent) ;
  - raz()     = gyPaletteRaz()    : remet le compteur au début (début de chaque bloc d'analyse du rapport, comme chaque nouvelle analyse de la page) ;
  - palette(n, decalage) = gyPalette(n, decalage).
Le compteur est propre au fil d'exécution (le rapport peut se générer dans un thread) : deux rapports simultanés ne se mélangent pas.
"""
import threading

PALETTE = (
    "#e6194b", "#3cb44b", "#f2c500", "#4363d8", "#f58231", "#911eb4", "#17becf", "#f032e6",
    "#8cc63f", "#ff8fa3", "#008b8b", "#b57edc", "#a0522d", "#00a8ff", "#2ecc9a", "#9e9d24",
    "#80cbc4", "#a1887f", "#aa00ff", "#0d47a1", "#ffcc80", "#607d8b", "#bf360c", "#64dd17",
)

_etat = threading.local()


def couleur_n(k):
    """Couleur n° k (entier >= 0) ; la palette se répète tous les 24."""
    return PALETTE[max(0, int(k or 0)) % len(PALETTE)]


def palette(n, decalage=0):
    n, decalage = max(0, int(n or 0)), max(0, int(decalage or 0))
    return [couleur_n(decalage + i) for i in range(n)]


def raz():
    _etat.curseur = 0


def suite(n):
    p = palette(n, getattr(_etat, "curseur", 0))
    _etat.curseur = (getattr(_etat, "curseur", 0) + len(p)) % len(PALETTE)
    return p
