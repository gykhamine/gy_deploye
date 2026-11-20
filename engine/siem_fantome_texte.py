"""TEXTE DES MESSAGES pour le modèle fantôme du SIEM : normalisation anti-contournement, mesures de forme, mots et n-grammes hachés.

Module PUR (numpy + bibliothèque standard, aucun import Django). Le texte n'est JAMAIS conservé : seulement des mesures et des empreintes hachées.

Pourquoi une normalisation : un sac de mots haché brut se laisse contourner par quiconque écrit « g@gn3z », « g a g n e z », « gаgnez » (a cyrillique),
ou glisse des caractères invisibles dans un mot. On ramène donc le texte à une forme canonique AVANT de hacher, et on MESURE ces contournements
(ce sont eux-mêmes des signaux : un message honnête n'a presque aucune lettre cyrillique au milieu d'un mot latin).

Trois familles de mesures, toutes nommées (les colonnes du réseau sont retrouvées par leur nom : on peut en ajouter sans perdre ce qui est appris) :
  contournement   invisibles, p_script_mixte, p_leet, lettres_espacees
  liens           liens_raccourcis, liens_ip, liens_suspects, liens_arobase (le lien qui cache sa vraie destination)
  intention       p_urgence, p_secret (demande d'identifiants ou de paiement), p_appat (gain, cadeau)
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
import zlib

import numpy as np
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7

HACHAGE = 48          # dimensions du sac de mots haché
GRAMMES = 64          # dimensions du sac de trigrammes de caractères haché (résiste aux fautes et aux mots déformés)

_MOT = re.compile(r"[^\W\d_]{2,}|https?://[^\s/]+|www\.[^\s/]+", re.IGNORECASE)
_LIEN = re.compile(r"(?:https?://|www\.)([^\s/?#]+)", re.IGNORECASE)
_LIEN_COMPLET = re.compile(r"(?:https?://|www\.)[^\s]+", re.IGNORECASE)
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?$")
_ESPACEES = re.compile(r"(?<![^\W_])(?:[^\W_][ .\-_*]){3,}[^\W_](?![^\W_])")
_MOT_BRUT = re.compile(r"\S+")

# Caractères sans largeur, de contrôle d'orientation, trait d'union conditionnel : jamais présents dans un message honnête en quantité.
_INVISIBLES = dict.fromkeys(list(range(0x200B, 0x2010)) + list(range(0x202A, 0x202F)) + list(range(0x2060, 0x2065)) + [0xFEFF, 0x00AD, 0x180E])

# Lettres cyrilliques ou grecques identiques à des lettres latines : ramenées au latin pour le hachage, et comptées comme contournement.
_HOMOGLYPHES = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ѕ": "s", "ј": "j", "һ": "h", "ԁ": "d",
                              "ɡ": "g", "ο": "o", "α": "a", "ν": "v", "ε": "e", "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "χ": "x"})
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "|": "l"})
_CAR_LEET = set("013457@$!|")

RACCOURCISSEURS = frozenset({"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "cutt.ly", "rb.gy", "shorturl.at", "tiny.cc", "buff.ly",
                             "rebrand.ly", "t.ly", "bl.ink", "s.id", "lnkd.in", "v.gd", "shorte.st", "adf.ly"})
TLD_SUSPECTS = frozenset({"zip", "mov", "xyz", "top", "click", "work", "support", "country", "gq", "tk", "ml", "cf", "ga", "buzz", "rest", "icu", "cam"})

# Radicaux (comparés au DÉBUT du mot normalisé) : français et anglais. Ce sont des INDICES donnés au réseau, jamais des règles qui bloquent.
URGENCE = ("urgent", "immediat", "maintenant", "expire", "dernier", "vite", "suspendu", "bloque", "verrouill", "fermeture", "sous 24", "action requise",
           "immediately", "expires", "suspended", "locked", "limited", "final", "required", "asap", "deadline")
SECRET = (_gtfr("engine/siem_fantome_texte.py:50:11"), "motdepasse", "password", "identifiant", "login", "otp", "cvv", "iban", "rib", "carte bancaire", "card", "virement", "transfer",
          "wire", "paiement", "payment", "bitcoin", "crypto", "wallet", "portefeuille", "verifier", "verify", "confirmez", "confirm", "connectez", "signin",
          _gtfr("engine/siem_fantome_texte.py:52:11"), "pin")
APPAT = ("gagn", "gratuit", "free", "prize", "prix", "bonus", "offre", "promo", "loterie", "winner", "cadeau", "gift", "recompense", "reward",
         "felicitation", "congrat", "remboursement", "refund", "heritage", "inherit")


# ── Forme canonique ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _script(c: str) -> str:
    try:
        return unicodedata.name(c).split(" ", 1)[0]
    except ValueError:
        return ""


def normaliser(texte: str) -> str:
    """Forme canonique pour hacher : NFKC (pleine largeur, ligatures), sans invisibles, homoglyphes ramenés au latin, minuscules, sans accents,
    lettres espacées recollées (« g a g n e z » → « gagnez »), chiffres et symboles-lettres dans un mot lu comme des lettres (« g@gn3z » → « gagnez »)."""
    t = unicodedata.normalize("NFKC", texte or "").translate(_INVISIBLES).lower().translate(_HOMOGLYPHES)
    t = "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c))
    t = _ESPACEES.sub(lambda m: re.sub(r"[ .\-_*]", "", m.group(0)), t)
    return _MOT_BRUT.sub(lambda m: _desleeter(m.group(0)), t)


def _desleeter(mot: str) -> str:
    """Un mot qui mêle lettres et chiffres/symboles-lettres est lu comme du leetspeak ; un nombre, un lien ou un mot sans lettre reste tel quel."""
    if mot.startswith(("http://", "https://", "www.")):
        return mot
    lettres = sum(c.isalpha() for c in mot)
    if lettres >= 2 and any(c in _CAR_LEET for c in mot) and lettres >= len(mot) / 2:
        return mot.translate(_LEET)
    return mot


# ── Mesures de forme ────────────────────────────────────────────────────────────────────────────────────────────────────
def _hotes(texte: str) -> list[str]:
    return [h.lower().rstrip(".,;:!?)") for h in _LIEN.findall(texte or "")]


def _tld(hote: str) -> str:
    return hote.split(":")[0].rsplit(".", 1)[-1] if "." in hote else ""


def _part(mots: list[str], radicaux: tuple, texte_norm: str) -> float:
    """min(1, occurrences / 3) des radicaux : un seul mot d'urgence n'est pas un signal, trois le sont."""
    touches = sum(1 for m in mots if m.startswith(radicaux)) + sum(texte_norm.count(r) for r in radicaux if " " in r)
    return min(1.0, touches / 3)


def caracteristiques_texte(texte: str) -> dict:
    """Mesures de forme d'un texte (jamais le texte lui-même) : forme, liens, contournements, intention."""
    texte = texte or ""
    n = max(1, len(texte))
    lettres = [c for c in texte if c.isalpha()]
    mots = [m.lower() for m in _MOT.findall(texte)]
    hotes = _hotes(texte)
    domaines = set(hotes)
    norm = normaliser(texte)
    mots_norm = [m.lower() for m in _MOT.findall(norm)]
    bruts = [m for m in _MOT_BRUT.findall(texte) if sum(c.isalpha() for c in m) >= 2]
    mixtes = sum(1 for m in bruts if len({_script(c) for c in m if c.isalpha()} - {""}) > 1)
    leets = sum(1 for m in bruts if not m.startswith(("http", "www")) and _desleeter(m.lower()) != m.lower())
    d = {"longueur": len(texte), "p_majuscules": sum(c.isupper() for c in lettres) / max(1, len(lettres)),
         "p_chiffres": sum(c.isdigit() for c in texte) / n, "p_ponctuation": sum(c in "!?$€%*#" for c in texte) / n,
         "liens": len(hotes), "liens_distincts": len(domaines),
         "p_repetition": 1 - len(set(mots)) / len(mots) if len(mots) > 3 else 0.0}
    d["invisibles"] = sum(1 for c in texte if ord(c) in _INVISIBLES)
    d["p_script_mixte"] = mixtes / max(1, len(bruts))
    d["p_leet"] = leets / max(1, len(bruts))
    d["lettres_espacees"] = len(_ESPACEES.findall(unicodedata.normalize("NFKC", texte).lower()))
    d["liens_raccourcis"] = sum(1 for h in hotes if h.split(":")[0] in RACCOURCISSEURS)
    d["liens_ip"] = sum(1 for h in hotes if _IPV4.match(h))
    d["liens_suspects"] = sum(1 for h in hotes if _tld(h) in TLD_SUSPECTS or h.startswith("xn--") or ".xn--" in h)
    d["liens_arobase"] = sum(1 for m in _LIEN_COMPLET.findall(texte) if "@" in m.split("://", 1)[-1].split("/", 1)[0])
    d["p_urgence"] = _part(mots_norm, URGENCE, norm)
    d["p_secret"] = _part(mots_norm, SECRET, norm)
    d["p_appat"] = _part(mots_norm, APPAT, norm)
    return d


# ── Empreintes ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _signe_log_norme(v: np.ndarray) -> np.ndarray:
    v = np.sign(v) * np.log1p(np.abs(v))
    norme = float(np.linalg.norm(v))
    return v / norme if norme else v


def hacher(texte: str, dim: int = HACHAGE) -> np.ndarray:
    """Sac de mots haché signé (crc32) sur le texte NORMALISÉ, compté en log puis normalisé : le texte ne peut pas être reconstruit,
    un mot nouveau n'est pas ignoré, un mot déformé retombe sur le même mot."""
    v = np.zeros(dim)
    for mot in _MOT.findall(normaliser(texte)):
        h = zlib.crc32(mot.encode())
        v[h % dim] += 1.0 if (h >> 16) & 1 else -1.0
    return _signe_log_norme(v)


def grammes(texte: str, dim: int = GRAMMES) -> np.ndarray:
    """Sac de trigrammes de caractères haché (texte normalisé, sans espaces ni ponctuation) : « gagnnez » ou « gagnez-vite » ressemblent encore
    à « gagnez », ce qu'un sac de mots ne voit pas. Les liens sont ignorés (ils ont leurs propres mesures)."""
    s = re.sub(r"[^a-z0-9]", "", _LIEN_COMPLET.sub(" ", normaliser(texte)))
    v = np.zeros(dim)
    for i in range(len(s) - 2):
        h = zlib.crc32(s[i:i + 3].encode())
        v[h % dim] += 1.0 if (h >> 16) & 1 else -1.0
    return _signe_log_norme(v)


def empreinte_texte(texte: str) -> str:
    """Repère de doublon (même texte envoyé à plusieurs discussions) : empreinte du texte NORMALISÉ, jamais le texte. Un spammeur qui change une
    lettre invisible ou une majuscule entre deux envois reste un doublon."""
    return hashlib.sha256(re.sub(r"[^a-z0-9]", "", normaliser(texte)).encode()).hexdigest()[:16]


# ── Professeur de forme (étiquette faible) ──────────────────────────────────────────────────────────────────────────────
def etiquette_faible_message(m: dict) -> float:
    """Ce que dit la FORME d'un message, à partir de ses mesures : 0 (rien d'anormal) à 1 (cumul d'indices). Sert de professeur FAIBLE, avant
    qu'aucun humain n'ait donné d'avis : le réseau apprend à reconnaître aussi, par ses mots hachés, ce que ces indices n'écrivent pas.
    Poids d'apprentissage toujours petit : un avis humain pèse six fois plus."""
    g = lambda k: float(m.get(k, 0) or 0)   # noqa: E731 — lecture d'une mesure absente = 0
    lien = g("liens") > 0
    indices = (1.0 * (g("doublons_heure") >= 3) + 0.8 * (g("rafale_expediteur") >= 15) + 0.6 * (g("conversations_heure") >= 8)
               + 1.0 * (g("liens_ip") > 0) + 0.8 * (g("liens_raccourcis") > 0 and g("age_expediteur_j") < 2) + 0.6 * (g("liens_distincts") >= 4)
               + 0.8 * (g("liens_arobase") > 0) + 0.5 * (g("liens_suspects") > 0 and lien)
               + 0.8 * (g("invisibles") > 0 and lien) + 0.8 * (g("p_script_mixte") > 0.15) + 0.4 * (g("lettres_espacees") > 0 and lien)
               + 1.0 * (g("p_secret") >= 0.34 and lien) + 0.8 * (g("p_appat") >= 0.34 and lien) + 0.6 * (g("p_urgence") >= 0.34 and lien)
               + 0.4 * (g("p_majuscules") > 0.6 and g("longueur") > 30) + 0.4 * (g("p_ponctuation") > 0.15))
    return 1 - math.exp(-indices)
