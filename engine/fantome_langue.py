"""LE FANTÔME N'A PAS DE LANGUE : il s'adapte à celle de chaque entreprise (français, anglais, arabe, russe, chinois, swahili…).

Avant, la lecture des noms (tables, colonnes, KPI…) supprimait tout caractère non ASCII : une entreprise écrivant en arabe, en russe ou en
chinois était invisible au fantôme, et les mots-clés étaient seulement français. Ce module est PUR (sans Django) et fournit :

1. NORMALISATION UNICODE : minuscules, accents et signes combinants retirés, lettres de TOUTES les écritures conservées.
2. RECONNAISSANCE PAR PRÉFIXE (pluriel, accord) pour les écritures à mots séparés, par SOUS-CHAÎNE pour celles qui ne séparent pas les mots
   (chinois, japonais, thaï).
3. TRIGRAMMES de caractères : un mot jamais vu partage quand même des morceaux avec ses formes voisines (pluriels, dérivés, emprunts) ;
   c'est ce qui permet au réseau d'apprendre une langue sans dictionnaire complet.
4. LEXIQUE DE GRAINES multilingue (fantome_lexique.json) : de simples étiquettes faibles de départ, qui s'enrichissent en ajoutant une langue
   ou des mots dans le fichier JSON, SANS toucher au code. Ce ne sont pas des règles : le réseau apprend par cooccurrence les mots que le
   lexique ne connaît pas (« Écolage » à côté de « Élèves »).
5. PROFIL DE LANGUE de l'entreprise (écritures employées, langue de graines dominante) : un signal, jamais un filtre.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

CHEMIN_LEXIQUE = Path(__file__).with_name("fantome_lexique.json")
SANS_ESPACES = ("CJK", "HIRAGANA", "KATAKANA", "THAI", "LAO", "KHMER", "MYANMAR", "TIBETAN")   # écritures dont les mots ne sont pas séparés
MIN_PREFIXE = 4   # un mot de moins de 4 lettres doit correspondre exactement (évite « ai » dans « aime »)


def normaliser(s) -> str:
    """Minuscules, sans accents ni signes combinants, lettres et chiffres de toute écriture ; le reste devient une espace."""
    t = unicodedata.normalize("NFKD", str(s or "")).casefold()
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[\W_]+", " ", t, flags=re.UNICODE).strip()


def _sans_article(j: str) -> str:
    """Arabe : l'article défini « ال » se colle au mot (« الطلاب » = « les élèves ») ; sans lui, le mot reste reconnaissable."""
    return j[2:] if j.startswith("\u0627\u0644") and len(j) >= 5 else j


def jetons(s) -> list[str]:
    return [_sans_article(j) for j in normaliser(s).split() if j]


def _sans_espaces(texte: str) -> bool:
    return any(ecriture_de(c) in SANS_ESPACES for c in texte)


def contient(js, mot) -> bool:
    """Un mot-clé (déjà normalisé) correspond à un jeton qui le contient ou le prolonge (pluriel, accord) ; dans une écriture sans
    espaces, à un jeton qui le contient."""
    if not mot:
        return False
    if _sans_espaces(mot):
        return any(mot in j for j in js)
    return any(j == mot or (len(mot) >= MIN_PREFIXE and (j.startswith(mot) or (mot.startswith(j) and len(j) >= MIN_PREFIXE))) for j in js)


def ecriture_de(c: str) -> str:
    """Écriture d'un caractère (LATIN, ARABIC, CYRILLIC, CJK, DEVANAGARI…), « ? » si inconnue."""
    try:
        return unicodedata.name(c).split()[0]
    except ValueError:
        return "?"


def ecritures(textes) -> dict[str, float]:
    """Part de chaque écriture parmi les lettres des textes (somme = 1 ; vide s'il n'y a aucune lettre). PUR."""
    n = Counter(ecriture_de(c) for t in textes for c in str(t or "") if c.isalpha())
    total = sum(n.values())
    return {k: round(v / total, 3) for k, v in n.most_common()} if total else {}


def ngrammes(jeton: str, n: int = 3) -> list[str]:
    """Trigrammes de caractères avec bornes (« ^ » début, « $ » fin) pour un jeton d'au moins 4 caractères ; rien en dessous."""
    if len(jeton) < 4:
        return []
    m = f"^{jeton}$"
    return [m[i:i + n] for i in range(len(m) - n + 1)]


# ── Lexique de graines ────────────────────────────────────────────────────────────────────────────────────────────────────
def charger_lexique(chemin: Path | str | None = None) -> dict:
    """{langue: {domaine: {"libelle": str, "mots": [...normalisés...], "mesures": [...], "dimensions": [...]}}} ; {} si le fichier manque
    ou est invalide (le fantôme fonctionne alors avec les seuls mots français intégrés au code)."""
    try:
        brut = json.loads(Path(chemin or CHEMIN_LEXIQUE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sortie = {}
    for langue, domaines in (brut or {}).items():
        if not isinstance(domaines, dict) or langue.startswith("_"):
            continue
        sortie[langue] = {}
        for dom, d in domaines.items():
            if not isinstance(d, dict):
                continue
            sortie[langue][dom] = {"libelle": str(d.get("libelle") or ""),
                                   **{cle: sorted({normaliser(m) for m in d.get(cle, []) if normaliser(m)}) for cle in ("mots", "mesures", "dimensions")}}
    return sortie


def fusionner(domaines: dict, lexique: dict | None = None, langue_base: str = "fr") -> dict:
    """Copie de `domaines` (ceux du code, dans `langue_base`) dont mots / mesures / dimensions reçoivent ceux de TOUTES les langues du
    lexique, et qui gagne « libelles » {langue: libellé}. Un domaine inconnu du code est ignoré. PUR."""
    lexique = charger_lexique() if lexique is None else lexique
    sortie = {}
    for code, d in domaines.items():
        nouveau = {**d, "libelles": {langue_base: d.get("libelle", code)}}
        for cle in ("mots", "mesures", "dimensions"):
            tous = list(d.get(cle, ()))
            for langue, doms in lexique.items():
                tous += [m for m in doms.get(code, {}).get(cle, []) if m not in tous]
            nouveau[cle] = tuple(tous)
        for langue, doms in lexique.items():
            if doms.get(code, {}).get("libelle"):
                nouveau["libelles"][langue] = doms[code]["libelle"]
        sortie[code] = nouveau
    return sortie


def libelle(domaine: dict, langue: str | None) -> str:
    """Libellé d'un domaine dans la langue demandée, à défaut dans celle du code."""
    ls = domaine.get("libelles") or {}
    return ls.get(langue or "") or domaine.get("libelle") or next(iter(ls.values()), "")


def langue_des_graines(js, base: dict, lexique: dict | None = None, langue_base: str = "fr") -> str | None:
    """Langue dont le plus de mots-graines apparaissent dans les jetons : `base` = domaines du code (dans `langue_base`), `lexique` = les autres
    langues. None s'il n'y a aucun mot reconnu. En cas d'égalité, la langue du code l'emporte."""
    lexique = charger_lexique() if lexique is None else lexique
    scores = Counter()
    for code, d in base.items():
        scores[langue_base] += sum(1 for m in d.get("mots", ()) if contient(js, m))
        for langue, doms in lexique.items():
            scores[langue] += sum(1 for m in doms.get(code, {}).get("mots", []) if contient(js, m))
    scores = +scores
    return scores.most_common(1)[0][0] if scores else None


def profil(textes, base: dict | None = None, lexique: dict | None = None) -> dict:
    """Profil de langue d'une entreprise : {"ecritures": {...}, "langue": code ou None}. Un SIGNAL pour l'interface (libellés), jamais un filtre."""
    textes = [t for t in textes if t]
    js = [j for t in textes for j in jetons(t)]
    return {"ecritures": dict(list(ecritures(textes).items())[:4]),
            "langue": langue_des_graines(js, base, lexique) if base else None}
