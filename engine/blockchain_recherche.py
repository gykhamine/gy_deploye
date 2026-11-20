"""Recherche dans la blockchain (page /blockchain/) — mêmes critères que l'onglet « Recherche » de l'ontologie, adaptés aux blocs :
texte libre (tous les mots, sans accents ni casse), types de transaction, champ / valeur (contenu réel d'une ligne, y compris
avant → après d'une modification), table, plage de numéros de bloc, plage de dates. Module pur (aucun import Django) : testable seul.

Un bloc est un dict {index, hash, previous_hash, cree_le (date ou datetime), transactions[]} ; `filtrer` ne garde que les transactions
qui correspondent (comme la portée table/agent) et supprime les blocs devenus vides."""
import json
import unicodedata
from datetime import date

CLES_RECHERCHE = ("q", "types", "champ", "valeur", "table_rech", "bloc_min", "bloc_max", "du", "au")


def _norm(x):
    """Texte sans accents ni casse (« Électricité » → « electricite »)."""
    return unicodedata.normalize("NFKD", "" if x is None else str(x)).encode("ascii", "ignore").decode().lower()


def _entier(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _jour(x):
    try:
        return date.fromisoformat(str(x).strip())
    except ValueError:
        return None


def criteres(get):
    """Critères nettoyés depuis une QueryDict (ou tout objet avec get/getlist) ; `actifs` = au moins un critère renseigné."""
    c = {"mots": _norm(get.get("q", "")).split(), "types": {x for x in get.getlist("types") if x},
         "champ": _norm(get.get("champ", "").strip()), "valeur": _norm(get.get("valeur", "").strip()),
         "table": _norm(get.get("table_rech", "").strip()),
         "bloc_min": _entier(get.get("bloc_min")), "bloc_max": _entier(get.get("bloc_max")),
         "du": _jour(get.get("du")), "au": _jour(get.get("au"))}
    c["actifs"] = any(v not in (None, "", [], set()) for k, v in c.items())
    return c


def _paires(tx):
    """(champ, valeur) du contenu d'une transaction : données de la ligne, et avant / après de chaque modification."""
    donnees = tx.get("donnees")
    for k, v in (donnees.items() if isinstance(donnees, dict) else ()):
        yield k, v
    for m in tx.get("modifications") or []:
        if isinstance(m, dict):
            yield m.get("champ"), m.get("avant")
            yield m.get("champ"), m.get("apres")


def _bloc_ok(b, c):
    jour = b["cree_le"].date() if hasattr(b["cree_le"], "date") else b["cree_le"]
    return not ((c["bloc_min"] is not None and b["index"] < c["bloc_min"]) or (c["bloc_max"] is not None and b["index"] > c["bloc_max"])
                or (c["du"] and jour < c["du"]) or (c["au"] and jour > c["au"]))


def _tx_ok(tx, bloc_texte, c):
    if c["types"] and tx.get("type") not in c["types"]:
        return False
    if c["table"] and c["table"] not in _norm(f"{tx.get('table', '')} {tx.get('table_id', '')}"):
        return False
    if (c["champ"] or c["valeur"]) and not any((not c["champ"] or c["champ"] in _norm(k)) and (not c["valeur"] or c["valeur"] in _norm(v))
                                               for k, v in _paires(tx)):
        return False
    if c["mots"]:   # chaque mot doit se trouver dans la transaction OU dans le bloc (hash, numéro) : un hash trouvé garde tout le bloc
        texte = _norm(json.dumps(tx, ensure_ascii=False, default=str)) + " " + bloc_texte
        return all(m in texte for m in c["mots"])
    return True


def types_presents(blocs):
    return sorted({str(t.get("type")) for b in blocs for t in b["transactions"] if t.get("type")})


def filtrer(blocs, c):
    if not c["actifs"]:
        return blocs
    sortie = []
    for b in blocs:
        if not _bloc_ok(b, c):
            continue
        texte = _norm(f"bloc {b['index']} {b['hash']} {b['previous_hash']}")
        txs = [t for t in b["transactions"] if _tx_ok(t, texte, c)]
        if txs:
            sortie.append({**b, "transactions": txs})
    return sortie
