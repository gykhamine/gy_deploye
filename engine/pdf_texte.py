"""PDF DE TABLE : valeurs du fichier Parquet → TEXTE CSV (pandas) → table reconstruite par expressions régulières.

Pourquoi : pour un PDF, on ne relit plus la table (une requête EAV par ligne, ou une conversion Python cellule par cellule). Le fichier
Parquet est lu UNE fois par pandas, converti d'un coup en texte CSV (`DataFrame.to_csv`), puis un script à base de regex redécoupe ce texte
en lignes {slug: valeur} que `reports.generer_pdf_liste_table` consomme telles quelles.

Ce module ne dépend ni de Django ni du fichier Parquet : il ne connaît qu'un DataFrame et un texte (testable seul).

Fidélité : le texte reconstruit s'imprime comme avant dans le PDF —
  - vide / NaN → None ; dates en texte ISO (jour seul sans heure) ; durées en h:mm:ss (comme parquet_store.en_lignes) ;
  - valeurs imbriquées (liste, dict) : écrites en JSON dans le CSV puis relues en liste / dict (colonnes fichier, choix multiples…) ;
  - tout le reste : le texte que pandas écrit (même rendu que str() pour les nombres, les booléens et le texte).
Le CSV écrit par pandas (séparateur « ; », fin de ligne « \\n », guillemets doublés) est relu SANS tolérance : si le texte ne se redécoupe pas
exactement (cellule orpheline, nombre de colonnes différent), `depuis_texte` lève ValueError et l'appelant retombe sur l'ancienne lecture.
"""
import json
import re

SEPARATEUR = ";"

# Un champ = soit « …texte avec "" pour un guillemet… », soit du texte sans « ; » ni retour à la ligne ; suivi de ce qui le termine :
# « ; » (champ suivant dans la ligne), fin de ligne, ou fin du texte. Les retours à la ligne et les « ; » à l'intérieur de guillemets sont
# donc des caractères ordinaires : une cellule sur plusieurs lignes ne casse jamais la reconstruction.
_CHAMP = re.compile(r'(?:"([^"]*(?:""[^"]*)*)"|([^;"\r\n]*))(;|\r\n|\n|\Z)')   # boucle « dépliée » : temps linéaire, jamais de retour arrière explosif
_GUILLEMETS_DOUBLES = re.compile(r'""')


def _heures_minutes_secondes(duree) -> str:
    total = int(duree.total_seconds())
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _texte_date(v) -> str:
    """Même règle que parquet_store.en_lignes : jour seul quand il n'y a pas d'heure, sinon ISO complet."""
    try:
        return v.date().isoformat() if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v.isoformat()
    except AttributeError:   # un `date` sans heure
        return v.isoformat()


def _vide(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def vers_texte(df):
    """(texte CSV, colonnes à relire en JSON) d'un DataFrame de valeurs d'affichage. Le DataFrame reçu n'est pas modifié."""
    import pandas as pd
    colonnes_json, remplacees = [], {}
    for col in df.columns:
        s = df[col]
        genre = str(s.dtype)
        if genre.startswith("timedelta"):
            remplacees[col] = s.map(lambda d: None if pd.isna(d) else _heures_minutes_secondes(d)).astype(object)
        elif genre.startswith("datetime"):
            remplacees[col] = s.map(lambda d: None if pd.isna(d) else _texte_date(d)).astype(object)
        elif s.dtype == object:
            valeurs = s.tolist()
            types = {type(v) for v in valeurs}
            if types <= {str, float, type(None)}:
                continue   # texte (et vides) : rien à convertir
            if any(isinstance(v, (list, dict, tuple, set)) for v in valeurs):
                colonnes_json.append(col)
                remplacees[col] = pd.Series([None if _vide(v) else json.dumps(v, ensure_ascii=False, default=str) for v in valeurs],
                                            index=s.index, dtype=object)
            elif any(hasattr(v, "isoformat") for v in valeurs):
                remplacees[col] = pd.Series([_texte_date(v) if hasattr(v, "isoformat") else v for v in valeurs], index=s.index, dtype=object)
    if remplacees:
        df = df.assign(**remplacees)
    texte = df.to_csv(index=False, sep=SEPARATEUR, na_rep="", lineterminator="\n")
    return texte, colonnes_json


def enregistrements_du_texte(texte):
    """Générateur de lignes (listes de cellules) du texte CSV, par regex ; ValueError si un caractère n'est pas couvert."""
    n = len(texte)
    courante, pos = [], 0
    for m in _CHAMP.finditer(texte):
        if m.start() != pos:
            raise ValueError(f"texte CSV non reconnu à la position {pos}")
        if m.start() == m.end() == n and not courante:
            break   # correspondance vide finale, après le dernier retour à la ligne : pas une ligne de plus
        pos = m.end()
        entre_guillemets, brut, fin = m.group(1), m.group(2), m.group(3)
        courante.append(_GUILLEMETS_DOUBLES.sub('"', entre_guillemets) if entre_guillemets is not None else brut)
        if fin != SEPARATEUR:
            yield courante
            courante = []
    if pos != n or courante:
        raise ValueError(f"texte CSV non reconnu à la position {pos}")


def depuis_texte(texte, colonnes_json=()):
    """Reconstruit [{colonne: valeur}] depuis le texte CSV de `vers_texte` : cellule vide → None, colonnes JSON relues en liste / dict."""
    lignes = enregistrements_du_texte(texte)
    try:
        entetes = next(lignes)
    except StopIteration:
        raise ValueError("texte CSV vide")
    a_decoder = set(colonnes_json)
    en_json = [i for i, nom in enumerate(entetes) if nom in a_decoder]
    sortie = []
    for cellules in lignes:
        if len(cellules) != len(entetes):
            raise ValueError(f"ligne de {len(cellules)} cellules pour {len(entetes)} colonnes")
        for i in en_json:
            if cellules[i]:
                try:
                    cellules[i] = json.loads(cellules[i])
                except ValueError:
                    pass
        sortie.append({nom: (v if v != "" else None) for nom, v in zip(entetes, cellules)})
    return sortie


def lignes_depuis_dataframe(df):
    """DataFrame → [{colonne: valeur}] en passant par le texte CSV : tout le chemin « Parquet → texte → table » en un appel."""
    texte, colonnes_json = vers_texte(df)
    lignes = depuis_texte(texte, colonnes_json)
    if len(lignes) != len(df):
        raise ValueError(f"{len(lignes)} lignes reconstruites pour {len(df)} lues")
    return lignes
