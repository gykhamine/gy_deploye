"""Recherche et masquage par lots dans l'historique du SIEM (page /siem/) — mêmes critères que la recherche de la page blockchain
(blockchain_recherche.py) : texte libre (tous les mots, sans accents ni casse), types, champ / valeur, plage de numéros, plage de dates.
Module pur (aucun import Django) : testable seul.

Un élément est un dict {ts, numero, type, champs{}} : une alerte (numéro = bloc de la chaîne), un événement collecté (numéro = ligne
d'audit) ou un incident clos. `criteres` lit une QueryDict (ou le dict d'un lot stocké) ; `correspond` dit si un élément répond.

Un LOT masqué = les critères de la recherche au moment du masquage + une date limite : il cache ce qui répondait alors, jamais ce qui
arrive ensuite. Masquer ne supprime rien (voir siem_noyau.lots_masques) ; réafficher retire un lot, ou tous."""
import json
from datetime import datetime

from .blockchain_recherche import _entier, _jour, _norm

CLES_RECHERCHE = ("q", "types", "champ", "valeur", "bloc_min", "bloc_max", "du", "au")
MAX_TEXTE = 200


class _Dict:
    """Façade get / getlist sur un dict simple (critères stockés dans un lot)."""
    def __init__(self, d):
        self.d = d if isinstance(d, dict) else {}

    def get(self, k, defaut=None):
        v = self.d.get(k, defaut)
        return (v[0] if v else defaut) if isinstance(v, list) else v

    def getlist(self, k):
        v = self.d.get(k, [])
        return [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])


def nettoyer(brut) -> dict:
    """Critères à stocker dans un lot : seulement les clés connues, texte borné, rien de vide."""
    g = brut if hasattr(brut, "getlist") else _Dict(brut)
    sortie = {}
    for k in CLES_RECHERCHE:
        if k == "types":
            v = [str(x)[:MAX_TEXTE] for x in g.getlist("types") if x][:50]
        else:
            v = str(g.get(k, "") or "").strip()[:MAX_TEXTE]
        if v:
            sortie[k] = v
    return sortie


def criteres(get) -> dict:
    """Critères prêts à l'emploi ; `actifs` = au moins un critère renseigné."""
    g = get if hasattr(get, "getlist") else _Dict(get)
    c = {"mots": _norm(g.get("q", "") or "").split(), "types": {x for x in g.getlist("types") if x},
         "champ": _norm(str(g.get("champ", "") or "").strip()), "valeur": _norm(str(g.get("valeur", "") or "").strip()),
         "bloc_min": _entier(g.get("bloc_min")), "bloc_max": _entier(g.get("bloc_max")),
         "du": _jour(g.get("du")), "au": _jour(g.get("au"))}
    c["actifs"] = any(v not in (None, "", [], set()) for k, v in c.items())
    return c


def _paires(el):
    for k, v in (el.get("champs") or {}).items():
        yield k, v


def _jour_de(ts):
    try:
        return datetime.fromtimestamp(float(ts)).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def correspond(el, c) -> bool:
    if not c["actifs"]:
        return True
    n = el.get("numero")
    if n is not None and ((c["bloc_min"] is not None and n < c["bloc_min"]) or (c["bloc_max"] is not None and n > c["bloc_max"])):
        return False
    if (c["bloc_min"] is not None or c["bloc_max"] is not None) and n is None:
        return False
    jour = _jour_de(el.get("ts"))
    if (c["du"] or c["au"]) and (jour is None or (c["du"] and jour < c["du"]) or (c["au"] and jour > c["au"])):
        return False
    if c["types"] and str(el.get("type")) not in c["types"]:
        return False
    if (c["champ"] or c["valeur"]) and not any((not c["champ"] or c["champ"] in _norm(k)) and (not c["valeur"] or c["valeur"] in _norm(v))
                                               for k, v in _paires(el)):
        return False
    if c["mots"]:
        texte = _norm(json.dumps(el, ensure_ascii=False, default=str))
        return all(m in texte for m in c["mots"])
    return True


def masque(el, lots) -> bool:
    """Vrai si un lot actif cache cet élément (il répondait aux critères du lot, et existait au moment du masquage)."""
    for lot in lots:
        try:
            if float(el.get("ts") or 0) <= lot["jusqua"] and correspond(el, lot["c"]):
                return True
        except (TypeError, ValueError):
            continue
    return False


def types_presents(elements):
    return sorted({str(e.get("type")) for e in elements if e.get("type")})


def filtrer(elements, vers_el, c, lots, avec_masques=False):
    """Garde ce qui répond à la recherche ET n'est pas masqué ; avec `avec_masques`, les masqués restent, marqués {"masque": True}.
    `vers_el` convertit une ligne de la page en élément de recherche. Retourne (lignes, nombre de lignes masquées écartées)."""
    sortie, caches = [], 0
    for ligne in elements:
        el = vers_el(ligne)
        if not correspond(el, c):
            continue
        if masque(el, lots):
            if not avec_masques:
                caches += 1
                continue
            ligne = {**ligne, "masque": True}
        sortie.append(ligne)
    return sortie, caches


def resume(brut) -> str:
    """Libellé court des critères d'un lot (« q=ssh · types=a,b · du=2026-10-01 »), vide = tout."""
    cr = nettoyer(brut)
    return " · ".join(f"{k}={','.join(v) if isinstance(v, list) else v}" for k, v in cr.items())


def lots_actifs(masquages, reaffichages) -> list:
    """Lots encore masqués. `masquages` : [(ts, details)] ; `reaffichages` : [(ts, lot)] où lot = identifiant ou « tout ».
    Un lot est actif tant qu'aucun réaffichage ne le vise (ou ne vise « tout ») APRÈS lui. Du plus récent au plus ancien."""
    sortie = []
    for ts, d in sorted(masquages, key=lambda x: -x[0]):
        if any(t >= ts and lot in ("tout", d.get("lot")) for t, lot in reaffichages):
            continue
        cr = d.get("criteres") or {}
        sortie.append({"id": d.get("lot"), "nom": d.get("nom", ""), "criteres": cr, "c": criteres(cr), "jusqua": float(d.get("jusqua") or ts), "ts": ts})
    return sortie
