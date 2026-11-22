"""STATISTIQUES PAR TERME d'une colonne (analyse exploratoire) + CACHE DÉFINITIF dans un fichier.

Module PUR (aucune dépendance Django hors `settings.MEDIA_ROOT`, lu à l'appel). Chaque valeur distincte de la colonne est un « terme »,
compté tel qu'écrit (espaces rognés/réduits) : « bz », « BZ » et « Brazzavile » apparaissent donc séparément, ce qui permet de repérer
les variantes d'un même mot avant de les fusionner (transformation « fusionner », transformation_colonne.py).

Cache : un fichier JSON par (table, colonne) dans MEDIA_ROOT/termes_colonnes/<entreprise>/<table>_<colonne>.json. Il est gardé tant que la
`signature` des données (nombre de lignes, dernière modification, dernier id) ne change pas, et supprimé dès qu'une transformation écrit dans
la table : la colonne n'est donc relue et recomptée qu'une seule fois par état des données.
"""
import glob
import json
import os
from collections import Counter

MAX_TERMES = 5000   # termes renvoyés (les plus fréquents d'abord) ; `distincts` donne toujours le nombre réel


def compter(valeurs) -> dict:
    """{total (valeurs renseignées), vides, distincts, tronque, termes: [{terme, n}]} triés par fréquence décroissante."""
    compteur, vides = Counter(), 0
    for v in valeurs:
        s = " ".join(str(v).split()) if v is not None else ""
        if s == "":
            vides += 1
        else:
            compteur[s] += 1
    termes = sorted(compteur.items(), key=lambda x: (-x[1], x[0].casefold(), x[0]))
    return {"total": sum(compteur.values()), "vides": vides, "distincts": len(termes), "tronque": len(termes) > MAX_TERMES,
            "termes": [{"terme": k, "n": n} for k, n in termes[:MAX_TERMES]]}


def _dossier(entreprise_id) -> str:
    from django.conf import settings
    return os.path.join(settings.MEDIA_ROOT, "termes_colonnes", str(entreprise_id))


def _chemin(table, champ) -> str:
    return os.path.join(_dossier(table.entreprise_id), f"{table.id}_{champ.id}.json")


def lire(table, champ, signature):
    """Données en cache si la signature est inchangée, sinon None (fichier absent, illisible ou périmé)."""
    try:
        with open(_chemin(table, champ), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("signature") == signature else None


def ecrire(table, champ, signature, donnees: dict) -> None:
    """Écriture atomique (fichier temporaire puis remplacement) ; un échec disque n'empêche jamais la réponse."""
    try:
        os.makedirs(_dossier(table.entreprise_id), exist_ok=True)
        chemin = _chemin(table, champ)
        tmp = f"{chemin}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({**donnees, "signature": signature}, f, ensure_ascii=False)
        os.replace(tmp, chemin)
    except OSError:
        pass


def invalider(table) -> None:
    """Supprime le cache de TOUTES les colonnes de la table (appelé quand une transformation écrit dedans)."""
    for f in glob.glob(os.path.join(glob.escape(_dossier(table.entreprise_id)), f"{table.id}_*.json")):
        try:
            os.remove(f)
        except OSError:
            pass
