"""Textes des commandes de gestion : catalogue i18n (clés « commandes.* »), avec ou sans catalogue.

Hors requête, la langue est celle réglée par l'administrateur, sinon la langue par défaut (voir engine/i18n.py).
Si le module i18n est inutilisable, la clé est affichée avec ses valeurs : la commande ne plante jamais pour un texte.
(Le nom commence par « _ » : Django ne le prend pas pour une commande.)
"""


def m(cle, **valeurs):
    try:
        from engine.i18n import tf
        return tf(cle, **valeurs)
    except Exception:
        reste = " ".join(f"{k}={v}" for k, v in valeurs.items())
        return f"{cle} {reste}".strip()
