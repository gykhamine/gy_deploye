"""Recherche et filtres de la grille LUS DANS LES FICHIERS PARQUET des tables EAV (voir parquet_store.py).

Avant : « q » et les filtres « f_<slug> » interrogeaient ValeurChamp (une ligne par cellule, `valeur ILIKE %…%`) : sur une grosse table,
chaque recherche balayait des millions de cellules. Ici, la recherche lit le fichier Parquet de la table (une ligne par enregistrement),
mis à jour au besoin comme pour toutes les lectures analytiques, et renvoie les `_id` qui correspondent ; `grille.appliquer` les
intersecte avec le queryset déjà isolé (agent, groupe, client) — la recherche ne peut donc jamais élargir la visibilité.

Fidélité : la recherche porte sur `_brut`, le texte EXACT des cellules dans l'EAV (« 1,5 » se trouve en cherchant « 1,5 »), avec la même
règle qu'avant : contient / égal sans tenir compte de la casse, « (vide) », comparaisons numériques pour les colonnes numériques.

Tri : `ids_tries` ordonne les lignes visibles d'après le fichier ; `expression_ordre` en fait un ORDER BY (PostgreSQL : array_position ; autres
moteurs : CASE, pour une liste courte).

Cache mémoire : le contenu décodé du fichier (`_brut` de chaque ligne) est gardé par processus, avec l'empreinte de la table au moment de la
lecture — même règle de fraîcheur que le fichier : empreinte différente (ligne ajoutée, modifiée, supprimée, colonne changée) = relu. Servir
depuis la mémoire vérifie AUSSI que le fichier existe et est à jour ; sinon il est créé / mis à jour (`_frame_a_jour`) AVANT tout traitement :
recherche, tri et contacts ne s'appuient jamais sur un fichier absent ou périmé. Limite : MAX_LIGNES_CACHE lignes en tout (réglage
GYPLATFORM_CACHE_RECHERCHE_LIGNES, 0 = pas de cache) ; la plus ancienne table lue est oubliée la première.

Repli SQL, toujours possible et sans changement de résultat : fichier non applicable (table NoSQL, pyarrow absent, erreur de lecture),
réglage GYPLATFORM_RECHERCHE_PARQUET = False, condition non prise en charge ici (comparaison d'ordre sur du texte : l'ordre dépend de la
collation de la base), ou trop de lignes trouvées pour une liste d'ids (LIMITE_IDS).
"""
import json
import logging
import re
import threading
import unicodedata
from collections import OrderedDict
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

LIMITE_TRI = 200000   # au-delà, le tri reste en SQL (liste d'ids trop lourde à lire et à transmettre)
LIMITE_CASE = 300     # moteurs sans array_position (SQLite…) : CASE WHEN par ligne, seulement pour une liste courte
MAX_LIGNES_CACHE = 100000   # lignes décodées gardées en mémoire, toutes tables confondues (≈ 1 Mo pour 500 lignes de 20 colonnes)
LIMITE_IDS = 20000    # au-delà, une condition `id IN (…)` n'a plus d'intérêt (et dépasse les limites de certains moteurs) : repli SQL
VIDE = "(vide)"
_OPERATEUR = re.compile(r"^\s*(>=|<=|!=|>|<|=)\s*(.*)$", re.S)
_ORDRE = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}


def actif() -> bool:
    from django.conf import settings
    return bool(getattr(settings, "GYPLATFORM_RECHERCHE_PARQUET", True))


def predicat_texte(q: str):
    """Vrai si UNE cellule de la ligne contient `q` (recherche « toutes colonnes »)."""
    cherche = (q or "").strip().lower()
    return lambda brut: any(cherche in str(v).lower() for v in brut.values() if v is not None)


def predicat_filtre(champ, expression: str):
    """Prédicat d'un filtre « f_<slug> » (mêmes opérateurs que grille._filtrer) ; None si la condition doit rester en SQL."""
    from .grille import TYPES_NUMERIQUES, _nombre
    m = _OPERATEUR.match(expression)
    op, v = (m.group(1), m.group(2).strip()) if m else ("~", expression.strip())
    slug, bas = champ.slug, v.lower()
    if v == VIDE and op in ("=", "!="):
        vide = lambda brut: brut.get(slug) in (None, "")
        return vide if op == "=" else (lambda brut: not vide(brut))
    if op == "~":
        return lambda brut: brut.get(slug) is not None and bas in str(brut[slug]).lower()
    nombre = _nombre(v) if champ.type_champ in TYPES_NUMERIQUES else None
    if nombre is not None:
        def numerique(brut):
            x = _nombre(brut.get(slug))
            if op == "=":
                return x is not None and x == nombre
            if op == "!=":
                return x is None or x != nombre        # comme exclude() en SQL : une cellule vide n'est pas « différente de » exclue
            return x is not None and _ORDRE[op](x, nombre)
        return numerique
    if op in ("=", "!="):
        egal = lambda brut: brut.get(slug) is not None and str(brut[slug]).lower() == bas
        return egal if op == "=" else (lambda brut: not egal(brut))
    return None    # >, >=, <, <= sur du texte : l'ordre dépend de la collation de la base, on laisse SQL


_CACHE = OrderedDict()      # clé (chemins des fichiers des parties) -> (empreintes, [(id, brut)], {id: brut})
_VERROU = threading.Lock()


def vider_cache():
    with _VERROU:
        _CACHE.clear()


def _maximum_cache() -> int:
    from django.conf import settings
    return int(getattr(settings, "GYPLATFORM_CACHE_RECHERCHE_LIGNES", MAX_LIGNES_CACHE))


def _decoder(df, ps):
    lignes = []
    for i, b in zip(df[ps.COLONNE_ID].tolist(), df[ps.COLONNE_BRUT].tolist()):
        try:
            brut = json.loads(b) if isinstance(b, str) else {}
        except ValueError:
            brut = {}
        lignes.append((int(i), brut if isinstance(brut, dict) else {}))
    return lignes


def _contenu(table):
    """(liste [(id, {slug: texte EAV exact})], index {id: …}) de la table ou de tout son groupe ; None si non applicable.
    Le fichier est créé / mis à jour s'il est absent ou périmé, qu'on lise le disque ou la mémoire."""
    from . import parquet_store as ps
    if not actif() or not ps._concerne(table):
        return None
    parties = ps._parties_analytiques(table, True)
    if not parties or any(t.est_nosql for t in parties):
        return None
    cle = tuple(ps.chemin_fichier(t) for t in parties)
    empreintes = tuple(ps.empreinte_table(t) for t in parties)           # AVANT la lecture : une écriture concurrente rend l'entrée périmée, jamais fausse
    maximum = _maximum_cache()
    if maximum > 0:
        with _VERROU:
            entree = _CACHE.get(cle)
            if entree is not None:
                _CACHE.move_to_end(cle)
        if entree is not None and entree[0] == empreintes and all(ps._fraicheur(c, e)[0] for c, e in zip(cle, empreintes)):
            return entree[1], entree[2]       # mémoire valide ET fichiers présents et à jour
    lignes = []
    for partie in parties:                    # fichier absent ou périmé : créé / mis à jour ici, puis lu
        lignes += _decoder(ps._frame_a_jour(partie, colonnes=[ps.COLONNE_ID, ps.COLONNE_BRUT]), ps)
    index = dict(lignes)
    if 0 < maximum and len(lignes) <= maximum:
        with _VERROU:
            _CACHE[cle] = (empreintes, lignes, index)
            _CACHE.move_to_end(cle)
            while len(_CACHE) > 1 and sum(len(e[1]) for e in _CACHE.values()) > maximum:
                _CACHE.popitem(last=False)
    return lignes, index


def _lignes_brutes(table):
    contenu = _contenu(table)
    return None if contenu is None else contenu[0]


def ids_correspondants(table, predicats):
    """Un seul passage sur le fichier : liste (même ordre que `predicats`) des ensembles d'`_id` qui satisfont chaque prédicat.
    Une entrée vaut None si trop de lignes correspondent (repli SQL pour cette condition). Retourne None si la recherche
    ne peut pas se faire dans le fichier : l'appelant garde alors la recherche SQL."""
    try:
        lignes = _lignes_brutes(table)
        if lignes is None:
            return None
        ensembles = [set() for _ in predicats]
        for ident, brut in lignes:
            for ens, pred in zip(ensembles, predicats):
                if pred(brut):
                    ens.add(ident)
        return [ens if len(ens) <= LIMITE_IDS else None for ens in ensembles]
    except Exception:
        logger.exception(_gt("rg6.msg.078"), getattr(table, "pk", "?"))
        return None


def _cle_tri(valeur, numerique):
    """Clé de tri d'une cellule (None = vide, rangé en dernier dans les deux sens, comme nulls_last en SQL)."""
    if valeur is None:
        return None
    if numerique:
        from .grille import _nombre
        return _nombre(valeur)
    texte = unicodedata.normalize("NFKD", str(valeur))
    return "".join(c for c in texte if not unicodedata.combining(c)).casefold()    # « é » se range avec « e », sans tenir compte de la casse


def ids_tries(table, ids, slug, numerique, decroissant):
    """Les `ids` (lignes visibles) rangés d'après la colonne `slug` du fichier ; égalités par id décroissant (comme `order_by(…, "-id")`),
    cellules vides en dernier. None si le tri ne peut pas se faire dans le fichier : l'appelant garde le tri SQL."""
    try:
        if len(ids) > LIMITE_TRI:
            return None
        contenu = _contenu(table)
        if contenu is None:
            return None
        index = contenu[1]
        avec, sans = [], []
        for i in sorted(ids, reverse=True):
            cle = _cle_tri(index.get(i, {}).get(slug), numerique)
            (sans if cle is None else avec).append((cle, i))
        avec.sort(key=lambda c: c[0], reverse=bool(decroissant))      # tri stable : les égalités restent par id décroissant
        return [i for _, i in avec] + [i for _, i in sans]
    except Exception:
        logger.exception(_gt("rg6.msg.079"), getattr(table, "pk", "?"))
        return None


def expression_ordre(ordonnes):
    """Expression ORDER BY qui range les lignes comme la liste d'ids `ordonnes` ; None si le moteur ne le permet pas pour cette taille."""
    from django.db import connection
    from django.db.models import BigIntegerField, Case, F, Func, IntegerField, Value, When
    from django.db.models.functions import Cast
    if connection.vendor == "postgresql":
        from django.contrib.postgres.fields import ArrayField
        tableau = Cast(Value(list(ordonnes), output_field=ArrayField(BigIntegerField())), ArrayField(BigIntegerField()))
        return Func(tableau, Cast(F("id"), BigIntegerField()), function="array_position", output_field=IntegerField())
    if len(ordonnes) <= LIMITE_CASE:
        return Case(*[When(id=i, then=Value(rang)) for rang, i in enumerate(ordonnes)], default=Value(len(ordonnes)), output_field=IntegerField())
    return None
