"""Transformation d'une colonne entière façon Power BI (« Remplacer les valeurs », « Extraire », « Mettre en forme »).

Module PUR : aucune dépendance Django, testable seul. La vue (views_transformation.py) lit les cellules, appelle
`transformer_valeur` sur chacune, puis écrit.

Opérations (clé `operation`) :
  remplacer       motif → remplacement (regex ou texte brut ; groupes \\1 ou $1 ; première occurrence ou toutes)
  supprimer       retire chaque occurrence du motif
  extraire        garde le groupe `groupe` (0 = toute la correspondance) de la 1re occurrence ;
                  `si_absent` = "garder" (cellule inchangée) | "vider"
  majuscules / minuscules / titre / rogner / espaces (réduit les espaces multiples)
  prefixe / suffixe   ajoute `texte`

Garde-fous : motif ≤ 500 caractères, cellule ≤ 20 000 caractères (au-delà : cellule ignorée), détection des
quantificateurs imbriqués catastrophiques `(a+)+`, délai global (le module `regex` impose un timeout par cellule s'il
est installé, sinon seul le délai global de la boucle protège).
"""
from engine.i18n import t, tf
import re
import time

try:  # module tiers optionnel : timeout natif par appel
    import regex as _regex  # type: ignore
except Exception:  # pragma: no cover
    _regex = None

OPERATIONS = (
    "remplacer", "supprimer", "extraire", "majuscules", "minuscules", "titre",
    "rogner", "espaces", "prefixe", "suffixe",
)
OPERATIONS_AVEC_MOTIF = ("remplacer", "supprimer", "extraire")

MAX_MOTIF = 500
MAX_CELLULE = 20000
DELAI_GLOBAL_S = 20.0
TIMEOUT_CELLULE_S = 0.5

# (x+)+  (x*)*  (x+)*  (x|y+)+ … : forme classique de backtracking exponentiel
_IMBRIQUE = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*[+*{]")


class ErreurTransformation(ValueError):
    """Paramètre invalide (motif illisible, opération inconnue…) : message prêt à afficher à l'utilisateur."""


def _convertir_remplacement(texte: str) -> str:
    """`$1` / `${1}` / `${nom}` (style Power BI / JS) → `\\g<1>` / `\\g<nom>` ; `\\1` reste valide ; `$$` = `$`."""
    texte = texte.replace("$$", "\x00")
    texte = re.sub(r"\$\{(\w+)\}", r"\\g<\1>", texte)
    texte = re.sub(r"\$(\d+)", r"\\g<\1>", texte)
    return texte.replace("\x00", "$")


def compiler_motif(motif: str, est_regex: bool = True, casse: bool = False):
    """Motif compilé. Texte brut → échappé ; regex → validée (longueur, imbrication dangereuse, syntaxe)."""
    if not motif:
        raise ErreurTransformation(t("engine/transformation_colonne.py:g:56:36"))
    if len(motif) > MAX_MOTIF:
        raise ErreurTransformation(tf("engine/transformation_colonne.py:g:58:36", v1=MAX_MOTIF))
    if est_regex and _IMBRIQUE.search(motif):
        raise ErreurTransformation(
            t("engine/transformation_colonne.py:g:61:13"))
    source = motif if est_regex else re.escape(motif)
    moteur = _regex if _regex is not None else re
    drapeaux = 0 if casse else moteur.IGNORECASE
    try:
        return moteur.compile(source, drapeaux | moteur.MULTILINE)
    except Exception as exc:  # re.error / regex.error
        raise ErreurTransformation(tf("engine/transformation_colonne.py:g:68:36", v1=exc)) from exc


class Transformation:
    """Opération prête à appliquer ; `appliquer(valeur)` → nouvelle valeur (str) ou None si la cellule est ignorée."""

    def __init__(self, params: dict):
        self.operation = str(params.get("operation") or "").strip()
        if self.operation not in OPERATIONS:
            raise ErreurTransformation(tf("engine/transformation_colonne.py:g:77:40", v1=self.operation))
        self.est_regex = bool(params.get("est_regex", True))
        self.casse = bool(params.get("casse", False))
        self.tout = bool(params.get("tout", True))
        self.remplacement = str(params.get("remplacement") or "")
        self.texte = str(params.get("texte") or "")
        self.si_absent = "vider" if params.get("si_absent") == "vider" else "garder"
        try:
            self.groupe = int(params.get("groupe") or 0)
        except (TypeError, ValueError):
            raise ErreurTransformation(t("engine/transformation_colonne.py:g:87:40"))
        self.motif = None
        if self.operation in OPERATIONS_AVEC_MOTIF:
            self.motif = compiler_motif(str(params.get("motif") or ""), self.est_regex, self.casse)
            if self.groupe < 0 or self.groupe > self.motif.groups:
                raise ErreurTransformation(
                    tf("engine/transformation_colonne.py:g:93:21", v1=self.groupe, v2=self.motif.groups))
        if self.operation in ("prefixe", "suffixe") and self.texte == "":
            raise ErreurTransformation(t("engine/transformation_colonne.py:g:95:40"))
        # Un remplacement qui référence un groupe absent est détecté ICI (une fois), pas cellule par cellule.
        self._repl = self._preparer_remplacement()

    def _preparer_remplacement(self):
        if self.operation != "remplacer":
            return ""
        repl = _convertir_remplacement(self.remplacement) if self.est_regex else self.remplacement.replace("\\", "\\\\")
        try:
            self.motif.sub(repl, "")  # valide la syntaxe du remplacement (le gabarit est compilé même sans correspondance)
            for ref in re.findall(r"\\g<(\d+)>|\\(\d+)", repl):
                numero = int(ref[0] or ref[1])
                if numero > self.motif.groups:
                    raise ErreurTransformation(
                        tf("engine/transformation_colonne.py:g:109:25", v1=numero, v2=self.motif.groups))
        except Exception as exc:
            raise ErreurTransformation(tf("engine/transformation_colonne.py:g:111:40", v1=exc)) from exc
        return repl

    def appliquer(self, valeur):
        if valeur is None:
            return None
        s = str(valeur)
        if len(s) > MAX_CELLULE:
            return None
        op = self.operation
        if op == "majuscules":
            return s.upper()
        if op == "minuscules":
            return s.lower()
        if op == "titre":
            return s.title()
        if op == "rogner":
            return s.strip()
        if op == "espaces":
            return re.sub(r"\s+", " ", s).strip()
        if op == "prefixe":
            return self.texte + s
        if op == "suffixe":
            return s + self.texte
        extra = {"timeout": TIMEOUT_CELLULE_S} if _regex is not None else {}
        try:
            if op == "remplacer":
                return self.motif.sub(self._repl, s, count=0 if self.tout else 1, **extra)
            if op == "supprimer":
                return self.motif.sub("", s, count=0 if self.tout else 1, **extra)
            if op == "extraire":
                m = self.motif.search(s, **extra)
                if m is None:
                    return "" if self.si_absent == "vider" else s
                return m.group(self.groupe) or ""
        except TimeoutError:
            return None
        except Exception as exc:
            if exc.__class__.__name__ == "TimeoutError":
                return None
            raise ErreurTransformation(tf("engine/transformation_colonne.py:g:151:40", v1=exc)) from exc
        return s


def transformer_colonne(valeurs, params: dict, max_exemples: int = 20):
    """Applique l'opération à `valeurs` : itérable de (cle, valeur_brute).

    Retourne (changements, resume) :
      changements : liste de (cle, avant, apres) pour les cellules RÉELLEMENT modifiées
      resume      : {"examinees", "modifiees", "ignorees_vides", "ignorees_trop_longues_ou_lentes",
                     "exemples": [{"cle", "avant", "apres"}], "interrompu": bool}
    """
    transfo = Transformation(params)
    debut = time.monotonic()
    changements, exemples = [], []
    examinees = vides = ignorees = 0
    interrompu = False
    for cle, valeur in valeurs:
        if time.monotonic() - debut > DELAI_GLOBAL_S:
            interrompu = True
            break
        examinees += 1
        if valeur is None or str(valeur) == "":
            vides += 1
            continue
        apres = transfo.appliquer(valeur)
        if apres is None:
            ignorees += 1
            continue
        if apres != str(valeur):
            changements.append((cle, str(valeur), apres))
            if len(exemples) < max_exemples:
                exemples.append({"cle": cle, "avant": str(valeur), "apres": apres})
    return changements, {
        "examinees": examinees, "modifiees": len(changements), "ignorees_vides": vides,
        "ignorees_trop_longues_ou_lentes": ignorees, "exemples": exemples, "interrompu": interrompu,
    }


def decouper_valeur(motif, valeur, dernier: bool = False, seulement_concernees: bool = False):
    """Découpe une cellule en (partie1, partie2). Motif à ≥ 2 groupes : groupes 1 et 2 ; sinon le motif est le séparateur,
    coupé à sa 1re (ou dernière) occurrence. Motif absent : (cellule, \"\") ; avec `seulement_concernees`, la cellule n'est pas concernée et la fonction renvoie False
    (elle n'entre ni dans l'aperçu ni dans l'écriture). None si cellule trop longue ou motif trop lent."""
    s = str(valeur)
    if len(s) > MAX_CELLULE:
        return None
    extra = {"timeout": TIMEOUT_CELLULE_S} if _regex is not None else {}
    m = None
    try:
        if dernier:
            for m in motif.finditer(s, **extra):
                pass
        else:
            m = motif.search(s, **extra)
    except Exception as exc:  # TimeoutError du module `regex`
        if exc.__class__.__name__ == "TimeoutError":
            return None
        raise ErreurTransformation(tf("engine/transformation_colonne.py:g:207:36", v1=exc)) from exc
    if m is None:
        return False if seulement_concernees else (s, "")
    if motif.groups >= 2:
        return m.group(1) or "", m.group(2) or ""
    return s[:m.start()], s[m.end():]
