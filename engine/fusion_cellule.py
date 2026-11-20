"""Contenu typé des cellules fusionnées / retypées d'un DOCUMENT (FusionCelluleTable).

Une table avec fusions ou cellules retypées sert à composer des documents (reçus,
factures, bulletins…) : voir TableDynamique.recalculer_denaturee. Ce module donne un
sens au contenu écrit DANS ces cellules, selon leur type :

  - types texte / date / etc. : la valeur saisie est affichée telle quelle ;
  - types numériques          : la valeur saisie doit être un nombre ;
  - type « formule »          : expression commençant par « = », évaluée à chaque affichage
    (jamais stockée) : elle suit donc toute modification des valeurs qu'elle surveille.

Références (style tableur, sans eval) :
    B3            cellule : colonne B (2e colonne), ligne 3 (3e ligne CRÉÉE, 1 = la plus ancienne)
    B1:B5         plage rectangulaire
    [Prix]        colonne entière (par son nom)
    SELECTION     cellules couvertes par la fusion elle-même
Une référence à une autre cellule « formule » en utilise le résultat (chaînage) ; un cycle
donne une erreur. Opérateurs : + - * / et parenthèses.
Fonctions : SOMME, MOYENNE, MIN, MAX, NB, PRODUIT (plages/colonnes/nombres), ARRONDI(x; n).
"""
from engine.i18n import td
from engine.i18n import t, tf
import ast
import math
import re

from .formules_science import TYPES_NUMERIQUES as _TN  # source unique des types numériques

TYPES_NUMERIQUES = set(_TN.split(","))
FONCTIONS = {"SOMME", "MOYENNE", "MIN", "MAX", "NB", "PRODUIT", "ARRONDI"}
LONGUEUR_FORMULE_MAX = 2000   # au-delà : refus (analyse récursive, mémoire) ; lue par valider_valeur et _evaluer, jamais recopiée dans un texte
_REF_COL = re.compile(r"\[([^\]]+)\]")
_PLAGE = re.compile(r"\b([A-Za-z]{1,2})(\d+):([A-Za-z]{1,2})(\d+)\b")
_CELLULE = re.compile(r"\b([A-Za-z]{1,2})(\d+)\b")


def lettre_colonne(i):
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _index_colonne(lettres):
    n = 0
    for ch in lettres.upper():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _nombre(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        n = float(v if isinstance(v, (int, float)) else str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None   # « nan », « inf » saisis comme texte ne sont pas des nombres


def valider_valeur(type_affiche, valeur):
    """Message d'erreur si `valeur` est incompatible avec le type, sinon None."""
    valeur = str(valeur if valeur is not None else "").strip()
    if not valeur:
        return None
    if type_affiche == "formule":
        if not valeur.startswith("="):
            return t("engine/fusion_cellule.py:g:68:52")
        return tf("engine/fusion_cellule.py:a:formule_trop_longue", v1=LONGUEUR_FORMULE_MAX) if len(valeur) > LONGUEUR_FORMULE_MAX else None
    if type_affiche in TYPES_NUMERIQUES and _nombre(valeur) is None:
        return t("engine/fusion_cellule.py:g:70:16")
    return None


def _agreger(nom, valeurs):
    nombres = [n for n in (_nombre(v) for v in valeurs) if n is not None]
    if nom == "NB":
        return float(len(nombres))
    if not nombres:
        if nom in ("SOMME", "PRODUIT"):
            return 0.0
        raise ValueError(t("engine/fusion_cellule.py:g:81:26"))
    if nom == "SOMME":
        return math.fsum(nombres)
    if nom == "MOYENNE":
        return math.fsum(nombres) / len(nombres)
    if nom == "MIN":
        return min(nombres)
    if nom == "MAX":
        return max(nombres)
    p = 1.0
    for n in nombres:
        p *= n
    return p


class _Document:
    """Grille du document : valeurs des lignes + fusions, avec évaluation chaînée des formules."""

    def __init__(self, fusions, champs, lignes):
        self.champs, self.lignes, self.n = champs, lignes, len(lignes)
        self.nb_col = len(champs)
        self.ancres, self.couvertes = {}, set()
        for f in fusions:
            for chrono in range(f.ligne_debut, f.ligne_fin + 1):
                for col in range(f.col_debut, f.col_fin + 1):
                    if (chrono, col) == (f.ligne_debut, f.col_debut):
                        self.ancres[(chrono, col)] = f
                    else:
                        self.couvertes.add((chrono, col))
        self.resultats, self.en_cours = {}, []
        self.colonnes = {}
        for c in champs:
            vals = [l.get(c.slug) for l in lignes]
            self.colonnes[(c.nom or "").strip().lower()] = vals
            self.colonnes.setdefault((c.slug or "").strip().lower(), vals)

    def brut(self, chrono, col):
        idx = self.n - 1 - chrono
        if not (0 <= idx < self.n and 0 <= col < self.nb_col) or (chrono, col) in self.couvertes:
            return None
        f = self.ancres.get((chrono, col))
        if f is not None:
            if f.type_affiche == "formule":
                return self.calculer(f)
            return f.valeur_affichee
        return self.lignes[idx].get(self.champs[col].slug)

    def selection(self, f):
        return [self.lignes[self.n - 1 - ch].get(self.champs[col].slug)
                for ch in range(f.ligne_debut, f.ligne_fin + 1) if 0 <= self.n - 1 - ch < self.n
                for col in range(f.col_debut, min(f.col_fin, self.nb_col - 1) + 1)
                if (ch, col) != (f.ligne_debut, f.col_debut)]

    def calculer(self, f):
        """Résultat numérique d'une fusion « formule » (ValueError si erreur ; memoïsé)."""
        if f.id in self.resultats:
            r = self.resultats[f.id]
            if isinstance(r, ValueError):
                raise r
            return r
        if f.id in self.en_cours:
            raise ValueError(t("engine/fusion_cellule.py:g:142:30"))
        self.en_cours.append(f.id)
        try:
            r = _evaluer(f.valeur_affichee, self, f)
        except ValueError as exc:
            self.resultats[f.id] = exc
            raise
        finally:
            self.en_cours.pop()
        self.resultats[f.id] = r
        return r


def _evaluer(expression, doc, fusion):
    if len(expression) > LONGUEUR_FORMULE_MAX:
        raise ValueError(tf("engine/fusion_cellule.py:a:formule_trop_longue", v1=LONGUEUR_FORMULE_MAX))
    listes, corps = [], expression.lstrip("=").strip()

    def _col(m):
        cle = m.group(1).strip().lower()
        if cle not in doc.colonnes:
            raise ValueError(tf("engine/fusion_cellule.py:g:161:30", v1=m.group(1).strip()))
        listes.append(("liste", doc.colonnes[cle]))
        return f"__l{len(listes) - 1}__"

    def _plage(m):
        c1, c2 = sorted((_index_colonne(m.group(1)), _index_colonne(m.group(3))))
        r1, r2 = sorted((int(m.group(2)), int(m.group(4))))
        if r1 < 1 or c2 >= doc.nb_col:
            raise ValueError(tf("engine/fusion_cellule.py:g:169:30", v1=m.group(0).upper()))
        r2 = min(r2, doc.n)   # rien au-delà de la dernière ligne : « A1:Z99999999 » ne doit pas énumérer 10^8 cellules vides
        listes.append(("liste", [doc.brut(r - 1, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]))
        return f"__l{len(listes) - 1}__"

    def _cel(m):
        col, ligne = _index_colonne(m.group(1)), int(m.group(2))
        if ligne < 1 or col >= doc.nb_col:
            raise ValueError(tf("engine/fusion_cellule.py:g:176:30", v1=m.group(0).upper()))
        listes.append(("cellule", (ligne - 1, col, m.group(0).upper())))
        return f"__c{len(listes) - 1}__"

    corps = _REF_COL.sub(_col, corps).replace(";", ",")   # [Nom] d'abord : un nom de colonne peut contenir « ; »
    corps = _PLAGE.sub(_plage, corps)
    corps = _CELLULE.sub(_cel, corps)
    corps = re.sub(r"\bSELECTION\b", "__sel__", corps, flags=re.IGNORECASE)
    try:
        arbre = ast.parse(corps, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise ValueError(t("engine/fusion_cellule.py:g:187:26"))

    def valeurs(n):
        """Argument d'agrégat -> liste de valeurs."""
        if isinstance(n, ast.Name):
            if n.id == "__sel__":
                return doc.selection(fusion)
            if re.fullmatch(r"__l\d+__", n.id):
                return listes[int(n.id[3:-2])][1]
        return [ev(n)]

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if re.fullmatch(r"__c\d+__", n.id):
                ligne, col, nom = listes[int(n.id[3:-2])][1]
                brut = doc.brut(ligne, col)
                if brut is None or brut == "":
                    return 0.0
                nb = _nombre(brut)
                if nb is None:
                    raise ValueError(tf("engine/fusion_cellule.py:g:211:38", v1=nom))
                return nb
            raise ValueError(t("engine/fusion_cellule.py:g:213:30"))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if b == 0:
                raise ValueError(t("engine/fusion_cellule.py:g:226:34"))
            return a / b
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            nom = n.func.id.upper()
            if nom not in FONCTIONS:
                raise ValueError(tf("engine/fusion_cellule.py:a:fonction_inconnue", v1=n.func.id))
            if nom == "ARRONDI":
                if not 1 <= len(n.args) <= 2:
                    raise ValueError(t("engine/fusion_cellule.py:g:234:38"))
                return round(ev(n.args[0]), int(ev(n.args[1])) if len(n.args) == 2 else 0)
            if not n.args:
                raise ValueError(tf("engine/fusion_cellule.py:g:237:34", v1=n.func.id))
            return _agreger(nom, [v for a in n.args for v in valeurs(a)])
        raise ValueError(t("engine/fusion_cellule.py:g:239:26"))

    try:
        resultat = ev(arbre)
        if not math.isfinite(resultat):
            raise OverflowError
        return int(resultat) if float(resultat).is_integer() else round(resultat, 6)
    except (OverflowError, RecursionError):   # nombre trop grand, imbrication trop profonde : message lisible, jamais une erreur 500
        raise ValueError(t("engine/fusion_cellule.py:a:hors_limites"))


def resynchroniser(table, fusions):
    """Recale ligne_debut/ligne_fin des `fusions` (objets modifiés en place + persistés) sur leurs
    lignes d'ancrage (id + cree_le) : ni ajout ni suppression de ligne ne décale plus une fusion.
    - ligne d'ancrage présente : son rang actuel ;
    - supprimée (par n'importe quel moyen, sans signal nécessaire) : repli sur la survivante la plus
      proche DANS la fusion (première à sa suite pour le début, dernière avant elle pour la fin), et
      l'ancrage y est reporté ;
    - fusion ancienne sans ancrage : ancrée sur ses rangs actuels.
    Renvoie {fusion.id -> erreur} pour celles qui n'ont plus aucune ligne (jamais supprimées en silence).
    Ordre chronologique de référence : (cree_le, id), rang 0 = la plus ancienne."""
    fusions = list(fusions or [])
    if not fusions:
        return {}
    from bisect import bisect_left, bisect_right
    from .models import Enregistrement
    lignes = list(Enregistrement.objects.filter(table_id=table.id).order_by("cree_le", "id").values_list("cree_le", "id"))
    rang = {i: r for r, (_, i) in enumerate(lignes)}
    champs = ["ligne_debut", "ligne_fin", "enreg_debut", "enreg_fin", "ancre_debut_le", "ancre_fin_le"]
    orphelines = {}
    for f in fusions:
        if f.enreg_debut is None or f.enreg_fin is None:
            if f.ligne_fin < len(lignes):
                (f.ancre_debut_le, f.enreg_debut), (f.ancre_fin_le, f.enreg_fin) = lignes[f.ligne_debut], lignes[f.ligne_fin]
                f.save(update_fields=champs[2:])
            continue
        d, e = rang.get(f.enreg_debut), rang.get(f.enreg_fin)
        if d is None and f.ancre_debut_le is not None:
            d = bisect_left(lignes, (f.ancre_debut_le, f.enreg_debut))
        if e is None and f.ancre_fin_le is not None:
            e = bisect_right(lignes, (f.ancre_fin_le, f.enreg_fin)) - 1
        if d is None or e is None or e < 0 or d >= len(lignes) or d > e:
            orphelines[f.id] = {"valeur_calculee": "", "erreur_calcul": td("engine/fusion_cellule.py:g:277:73")}
            continue
        nouveau = (d, e, lignes[d][1], lignes[e][1], lignes[d][0], lignes[e][0])
        if nouveau != tuple(getattr(f, c) for c in champs):
            for c, v in zip(champs, nouveau):
                setattr(f, c, v)
            f.save(update_fields=champs)
    return orphelines


def fusions_hors_plage(fusions, nb_col, nb_lignes):
    """{fusion.id -> erreur} pour les fusions dont le rang dépasse la table (lignes/colonnes supprimées)."""
    msg = t("engine/fusion_cellule.py:g:288:11")
    return {f.id: {"valeur_calculee": "", "erreur_calcul": msg}
            for f in fusions if f.ligne_fin >= nb_lignes or f.col_fin >= nb_col}


def calculer_fusions(fusions, champs, lignes):
    """{fusion.id -> {"valeur_calculee", "erreur_calcul"}} pour les fusions de type formule.

    `champs` : ChampDynamique ordonnés (index = col_debut/col_fin) ; `lignes` : dicts
    (Enregistrement.to_dict()) du PLUS RÉCENT au plus ancien — ligne_debut/ligne_fin d'une
    fusion sont chronologiques (0 = plus ancienne). Passer TOUTES les fusions de la table
    (les formules peuvent se référencer entre elles)."""
    fusions = list(fusions or [])
    hors = fusions_hors_plage(fusions, len(champs), len(lignes))
    formules = [f for f in fusions if f.type_affiche == "formule" and (f.valeur_affichee or "").strip() and f.id not in hors]
    if not formules:
        return hors
    doc = _Document(fusions, champs, lignes)
    sorties = dict(hors)
    for f in formules:
        try:
            sorties[f.id] = {"valeur_calculee": str(doc.calculer(f)), "erreur_calcul": ""}
        except ValueError as exc:
            sorties[f.id] = {"valeur_calculee": "", "erreur_calcul": str(exc)}
        except RecursionError:   # longue chaîne de formules qui se référencent
            sorties[f.id] = {"valeur_calculee": "", "erreur_calcul": t("engine/fusion_cellule.py:a:hors_limites")}
    return sorties


def texte_affiche(fusion, calculs=None):
    """Texte à montrer/imprimer pour une fusion (résultat pour une formule, valeur saisie sinon)."""
    if fusion.type_affiche == "formule":
        r = (calculs or {}).get(fusion.id) or {}
        return r.get("valeur_calculee") or ((t("engine/fusion_cellule.py:g:319:46") + r["erreur_calcul"]) if r.get("erreur_calcul") else "")
    return fusion.valeur_affichee or ""
