"""
Import a froid d'un fichier CSV/Excel/JSON/Parquet : cree une TableDynamique + ses
ChampDynamique + ses Enregistrement/ValeurChamp automatiquement a partir
des colonnes du fichier, SANS aucune intervention utilisateur.

Moteur de lecture : pandas, toujours, de bout en bout (entree comme sortie).
Polars n'intervient jamais ici : il n'est autorise que dans un calcul (voir moteur_donnees.calcul_polars).

La fonction importer_fichier_vers_table() accepte un callback optionnel
`progression_cb(pct: int)` pour mettre a jour un modele TacheImport en
temps reel depuis une tache Celery.
"""
from engine.i18n import t, tf
import json
import math
import os
import re
from decimal import Decimal, InvalidOperation

import pandas as pd
from django.db import transaction

from .models import ChampDynamique, Enregistrement, TableDynamique, ValeurChamp

EXTENSIONS_ACCEPTEES = ("csv", "xlsx", "xls", "json", "parquet")   # parquet : export fidèle de la plateforme (manifeste dans les métadonnées, restauré tel quel) OU Parquet ordinaire (colonnes = colonnes du fichier, même logique que CSV/Excel) ; json : export fidèle de la plateforme (restauré tel quel, voir export_fidele.py) OU JSON ordinaire (table indépendante : colonnes = clés)
from .moteur_donnees import SEUIL_POLARS, polars_disponible, utiliser_polars  # noqa: F401  (règle unique : moteur_donnees.py)
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402
BATCH_SIZE   = 500     # ValeurChamp bulk_create par batch

SEPARATEURS_CSV = {
    "auto": None,
    "virgule": ",",
    "point_virgule": ";",
    "tabulation": "\t",
    "pipe": "|",
}

ENCODAGES_CSV = {
    "auto": None,
    "utf-8": "utf-8",
    "utf-8-sig": "utf-8-sig",
    "latin-1": "latin-1",
    "cp1252": "cp1252",
}


# ── Utilitaires ──────────────────────────────────────────────────────────────

def _slugifier(texte: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", texte.strip().lower()).strip("_")
    return slug or "colonne"


def _slug_table_unique(entreprise, nom: str) -> str:
    base = _slugifier(nom)
    slug = base
    i = 2
    while TableDynamique.objects.filter(entreprise=entreprise, slug=slug).exists():
        slug = f"{base}_{i}"
        i += 1
    return slug


def _ext(fichier_django) -> str:
    _, ext = os.path.splitext(fichier_django.name.lower())
    return ext.lstrip(".")


def _moteur_excel(ext: str) -> str:
    if ext == "xlsx":
        return "openpyxl"
    if ext == "xls":
        try:
            import python_calamine  # noqa: F401
            return "calamine"
        except ImportError:
            pass
        try:
            import xlrd  # noqa: F401
            return "xlrd"
        except ImportError:
            pass
        raise ValueError(
            t("engine/import_fichier.py:g:85:13")
        )
    raise ValueError(tf("engine/import_fichier.py:g:87:22", v1=ext))


_polars_disponible = polars_disponible


# ── Lecture des feuilles ─────────────────────────────────────────────────────

def lire_noms_feuilles(fichier_django) -> list:
    ext = _ext(fichier_django)
    try:
        if ext in ("xlsx", "xls"):
            fichier_django.seek(0)
            xl = pd.ExcelFile(fichier_django, engine=_moteur_excel(ext))
            return list(xl.sheet_names)
        return []
    except Exception:
        return []
    finally:
        try:
            fichier_django.seek(0)
        except Exception:
            pass


# ── Lecture du DataFrame (pandas) ────────────────────────────────────────────

def _a_caracteres_remplaces(df) -> bool:
    """Vrai si l'en-tête ou les 100 premières lignes contiennent U+FFFD (octets non UTF-8 remplacés à la lecture)."""
    if any("\ufffd" in str(c) for c in df.columns):
        return True
    return any("\ufffd" in str(v) for v in df.head(100).to_numpy().ravel())


def _lire_csv_avec_sep(fichier_django, sep, enc) -> pd.DataFrame:
    import io
    fichier_django.seek(0)
    raw = fichier_django.read()
    if sep is None:
        wrapper = io.TextIOWrapper(io.BytesIO(raw), encoding=enc, errors="replace")
        return pd.read_csv(wrapper, dtype=str, sep=None, engine="python",
                           keep_default_na=True)
    return pd.read_csv(io.BytesIO(raw), dtype=str, sep=sep,
                       encoding=enc, keep_default_na=True)


def _json_vers_dataframe(brut) -> pd.DataFrame:
    """JSON ordinaire → DataFrame de texte (même convention que CSV/Excel : tout en str, vide = NaN). Accepte une liste d'objets, ou un objet
    portant UNE liste d'objets (ex. {"data": [...]}) ; les objets imbriqués sont aplatis (a.b), les listes gardées en JSON texte."""
    if isinstance(brut, dict):
        listes = [v for v in brut.values() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        brut = listes[0] if len(listes) == 1 else [brut]
    if not isinstance(brut, list) or not brut or not all(isinstance(x, dict) for x in brut):
        raise ValueError(t("engine/import_fichier.py:g:145:26"))

    def _aplatir(obj, prefixe=""):
        plat = {}
        for k, v in obj.items():
            cle = f"{prefixe}{k}"
            if isinstance(v, dict) and v:
                plat.update(_aplatir(v, cle + "."))
            else:
                plat[cle] = v
        return plat
    df = pd.DataFrame([_aplatir(x) for x in brut], dtype=object)   # dtype=object : un entier reste « 1 » (pas « 1.0 » à cause des vides)

    def _texte(v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return float("nan")
        if isinstance(v, (list, dict)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    return df.astype(object).apply(lambda col: col.map(_texte))


def _texte_parquet(v):
    """Une cellule Parquet → texte, même convention que CSV/Excel/JSON (tout en str, vide = NaN) : un entier reste « 1 », un décimal
    n'est jamais en notation scientifique, une date sans heure reste « AAAA-MM-JJ », les listes et objets imbriqués restent en JSON."""
    import datetime
    import numpy as np
    if v is None or v is pd.NaT or v is pd.NA:
        return float("nan")
    if isinstance(v, np.ndarray):
        v = v.tolist()
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    if isinstance(v, (bool, np.bool_)):
        return "True" if v else "False"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if math.isnan(v):
            return float("nan")
        v = float(v)
        if math.isinf(v):
            return str(v)
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else np.format_float_positional(v, trim="-")
    if isinstance(v, pd.Timestamp) or isinstance(v, datetime.datetime):
        v = pd.Timestamp(v)
        if v.tzinfo is not None:
            v = v.tz_localize(None)
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", errors="replace")
    return str(v)


def _parquet_vers_dataframe(octets: bytes) -> pd.DataFrame:
    """Parquet ordinaire → DataFrame de texte (voir _texte_parquet). Les colonnes gardent leur nom et leur ordre ; un index nommé que pandas
    avait enregistré redevient une colonne."""
    import io
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError:
        raise ValueError(_gt("rg5.msg.056"))
    try:
        df = pd.read_parquet(io.BytesIO(octets))
    except Exception as exc:
        raise ValueError(_gtf("rg8.msg.034", exc=exc))
    return _dataframe_vers_texte(df)


def _dataframe_vers_texte(df) -> pd.DataFrame:
    """DataFrame typé (lu d'un Parquet) → DataFrame de texte (voir _texte_parquet)."""
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    df.columns = [str(c) for c in df.columns]
    colonnes = {}
    for nom in df.columns:
        serie = df[nom]
        if pd.api.types.is_string_dtype(serie.dtype) and not pd.api.types.is_object_dtype(serie.dtype):
            colonnes[nom] = serie.astype(object).where(serie.notna(), float("nan"))
        elif pd.api.types.is_integer_dtype(serie.dtype):
            colonnes[nom] = serie.astype(object).map(lambda x: float("nan") if x is pd.NA or x is None else str(int(x)))
        else:
            colonnes[nom] = serie.astype(object).map(_texte_parquet)
    return pd.DataFrame(colonnes, columns=list(df.columns), dtype=object)


def _lire_dataframe(fichier_django, feuille=None, separateur=None,
                    encodage=None) -> pd.DataFrame:
    ext = _ext(fichier_django)
    if ext == "csv":
        sep = SEPARATEURS_CSV.get(separateur or "auto")
        enc = ENCODAGES_CSV.get(encodage or "auto")
        if enc is not None:
            fichier_django.seek(0)
            return _lire_csv_avec_sep(fichier_django, sep, enc)
        for tentative_enc in ("utf-8-sig", "latin-1"):   # utf-8-sig lit aussi l'UTF-8 sans BOM, et retire le BOM du 1er nom de colonne
            try:
                fichier_django.seek(0)
                df = _lire_csv_avec_sep(fichier_django, sep, tentative_enc)
                if tentative_enc != "latin-1" and _a_caracteres_remplaces(df):
                    continue                                      # décodage « errors=replace » : ce n'était pas de l'UTF-8
                return df
            except UnicodeDecodeError:
                continue
        fichier_django.seek(0)
        return _lire_csv_avec_sep(fichier_django, sep, "latin-1")

    if ext == "parquet":
        fichier_django.seek(0)
        return _parquet_vers_dataframe(fichier_django.read())

    if ext == "json":
        fichier_django.seek(0)
        try:
            brut = json.loads(fichier_django.read().decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            raise ValueError(t("engine/import_fichier.py:g:193:30"))
        return _json_vers_dataframe(brut)

    if ext in ("xlsx", "xls"):
        fichier_django.seek(0)
        kwargs = {"dtype": str, "engine": _moteur_excel(ext)}
        if feuille:
            kwargs["sheet_name"] = feuille
        return pd.read_excel(fichier_django, **kwargs)

    raise ValueError(tf("engine/import_fichier.py:g:203:22", v1=ext))


# ── Inférence de type ─────────────────────────────────────────────────────────

# Un entier brut (pas de séparateur décimal) : signe optionnel puis chiffres.
_RE_ENTIER_BRUT = re.compile(r"^[+-]?\d+$")
# Décimal à séparateur point (format US/technique : "12.5").
_RE_DECIMAL_POINT = re.compile(r"^[+-]?\d+\.\d+$")
# Décimal à séparateur virgule (format français : "12,5") — distinct d'une
# date, contrairement à ce que pd.to_datetime déduit parfois si on ne le
# détecte pas explicitement AVANT toute tentative de parsing de date.
_RE_DECIMAL_FR = re.compile(r"^[+-]?\d+,\d+$")
# Un float double ne représente exactement que ~15-17 chiffres décimaux :
# au-delà, deux entiers distincts (ex. deux identifiants de 17 chiffres)
# peuvent s'arrondir sur la MÊME valeur après un aller-retour par float().
# On refuse donc le typage "nombre" pour un entier brut plus long que ça —
# il reste "texte", stocké caractère pour caractère, sans perte.
MAX_CHIFFRES_NOMBRE_SUR = 15


def _a_zero_initial_suspect(texte_entier: str) -> bool:
    """'0612345678', '007'... : un entier brut avec un zéro non significatif
    en tête est presque toujours un identifiant/code (téléphone, matricule,
    code postal...), jamais une quantité — le convertir en nombre efface le
    zéro de façon irréversible. On le détecte donc et on refuse le typage
    numérique dans ce cas."""
    signe = texte_entier[0] in "+-"
    corps = texte_entier[1:] if signe else texte_entier
    return len(corps) > 1 and corps[0] == "0"


def _candidat_numerique(texte: str):
    """Classe `texte` en 'entier' / 'decimal' / None (pas numérique du tout,
    ou numérique mais délibérément refusé — zéro initial suspect, entier
    hors précision sûre). Utilisé colonne par colonne : si TOUTES les
    valeurs non vides obtiennent une catégorie non-None, la colonne est
    numérique ; sinon elle reste texte (ou tentera une date)."""
    if _RE_ENTIER_BRUT.match(texte):
        corps = texte[1:] if texte[0] in "+-" else texte
        if _a_zero_initial_suspect(texte) or len(corps) > MAX_CHIFFRES_NOMBRE_SUR:
            return None
        return "entier"
    if _RE_DECIMAL_POINT.match(texte) or _RE_DECIMAL_FR.match(texte):
        return "decimal"
    return None


def _inferer_type_colonne(serie: pd.Series) -> str:
    valeurs = serie.dropna().astype(str).str.strip()
    valeurs = valeurs[valeurs != ""]
    if valeurs.empty:
        return "texte"

    vocabulaire_bool = {"true","false","1","0","vrai","faux","oui","non","yes","no"}
    if valeurs.str.lower().isin(vocabulaire_bool).all():
        return "booleen"

    # ── Numérique : on scanne la colonne ENTIÈRE (pas un échantillon de
    # quelques centaines de lignes) — une seule valeur non numérique perdue
    # dans un échantillon suffisait auparavant à faire passer toute une
    # colonne pour "nombre" alors qu'elle contenait du texte. Les regex
    # ci-dessus sont vectorisables et restent rapides même sur de gros
    # volumes. ──
    categories = valeurs.map(_candidat_numerique)
    if categories.notna().all():
        return "nombre" if (categories == "entier").all() else "decimal"

    # Si (quasi) toutes les valeurs sont de purs entiers bruts (chiffres
    # seuls, sans séparateur), refusées ci-dessus comme "nombre" (zéro
    # initial suspect, ou trop grand) : ce sont des codes/identifiants, pas
    # des dates. Sans ce garde-fou, pd.to_datetime peut réinterpréter des
    # chaînes comme "007"/"042" comme des jours/mois valides et la colonne
    # devient "date" par accident au lieu de rester "texte".
    if valeurs.apply(lambda v: bool(_RE_ENTIER_BRUT.match(v))).all():
        return "texte"

    # ── Date : uniquement si la colonne n'a pas été reconnue comme
    # numérique ci-dessus (un décimal français "12,5" est donc déjà écarté
    # avant d'arriver ici, il ne sera jamais mal interprété comme une date).
    # dayfirst=True car les dates de ce format sont saisies à la française
    # (jour/mois/année) — sans ça, pandas suppose mois/jour/année (US) et
    # "03/04/2026" (3 avril) devenait le 4 mars. ──
    try:
        echantillon = valeurs if len(valeurs) <= 2000 else valeurs.sample(2000, random_state=0)
        converties = pd.to_datetime(echantillon, errors="coerce", dayfirst=True)
        if converties.notna().mean() > 0.95:
            a_heure = (converties.dt.hour != 0).any() or (converties.dt.minute != 0).any()
            return "datetime" if a_heure else "date"
    except Exception:
        pass

    return "texte"


def _inferer_type_polars(valeurs_str: list) -> str:
    """Inférence de type depuis une liste de strings (colonne polars)."""
    serie = pd.Series(valeurs_str, dtype=str)
    return _inferer_type_colonne(serie)


def _valeur_pour_stockage(valeur, type_champ: str) -> str:
    if valeur is None or (isinstance(valeur, float) and valeur != valeur):
        return ""
    texte = str(valeur).strip()
    if not texte:
        return ""
    if type_champ == "booleen":
        return "1" if texte.lower() in ("true","1","vrai","oui","yes") else "0"
    if type_champ == "date":
        d = pd.to_datetime(texte, errors="coerce", dayfirst=True)
        return d.date().isoformat() if pd.notna(d) else texte
    if type_champ == "datetime":
        d = pd.to_datetime(texte, errors="coerce", dayfirst=True)
        return d.isoformat() if pd.notna(d) else texte
    if type_champ == "nombre":
        # int(texte) directement (précision arbitraire, pas de zéro perdu ni
        # d'arrondi) plutôt que int(float(texte)) : un float double perd la
        # précision au-delà d'~15-17 chiffres et arrondirait deux
        # identifiants distincts sur la même valeur. On ne passe par
        # float() qu'en repli, pour une valeur inattendue du type "4.0".
        try:
            return str(int(texte))
        except ValueError:
            pass
        try:
            return str(int(float(texte)))
        except (ValueError, OverflowError):
            return texte
    if type_champ == "decimal":
        try:
            d = Decimal(texte.replace(",", "."))
        except InvalidOperation:
            return texte
        # Decimal("nan") / Decimal("inf") ne LÈVENT PAS d'exception : sans ce
        # contrôle explicite, ces valeurs passaient la conversion et étaient
        # stockées telles quelles ("NaN"/"Infinity"), corrompant ensuite
        # tris, agrégats et formules qui s'attendent à un nombre fini.
        if not d.is_finite():
            return texte
        return str(d)
    return texte


# ── Champs (partagé entre import fichier et import base externe) ────────────

def _colonnes_avec_types(colonnes, df_echantillon):
    """Calcule une seule fois (slug unique, type inféré) pour chaque colonne
    — réutilisé tel quel pour créer les ChampDynamique de CHAQUE partie quand
    un import est découpé, afin que toutes les parties partagent exactement
    le même schéma (mêmes slugs, mêmes types)."""
    slugs_utilises = set()
    infos = []
    for colonne in colonnes:
        slug = _slugifier(str(colonne))
        base = slug
        i = 2
        while slug in slugs_utilises:
            slug = f"{base}_{i}"
            i += 1
        slugs_utilises.add(slug)
        type_champ = _inferer_type_colonne(df_echantillon[colonne].astype(str))
        infos.append((str(colonne)[:120], slug, type_champ))
    return infos


def _creer_champs_pour_table(table, infos_colonnes):
    champs = []
    for ordre, (nom, slug, type_champ) in enumerate(infos_colonnes):
        champs.append(ChampDynamique.objects.create(
            table=table, nom=nom, slug=slug, type_champ=type_champ, ordre=ordre,
        ))
    return champs


def _fenetre_progression(prog_cb, depart_pct, largeur_pct, lignes_avant, nb_lignes_partie, nb_lignes_total):
    """Renvoie un callback prog_cb(pct_local 0-100) -> prog_cb global
    (depart_pct à depart_pct+largeur_pct), pour donner une progression
    lisse quand l'import est réparti sur plusieurs tables (parties)."""
    def _cb(pct_local):
        if not nb_lignes_total:
            return prog_cb(depart_pct + largeur_pct)
        lignes_traitees = lignes_avant + (pct_local / 100.0) * nb_lignes_partie
        prog_cb(depart_pct + (lignes_traitees / nb_lignes_total) * largeur_pct)
    return _cb


# ── Import principal (fichier CSV/Excel) ─────────────────────────────────────

def importer_fichier_vers_table(
    fichier_django,
    entreprise,
    feuille=None,
    separateur=None,
    encodage=None,
    progression_cb=None,   # callable(pct: int) ou None
    type_stockage="sql",   # "sql" (EAV, historique) ou "nosql" (JSON, voir nosql_store.py)
    mode_restauration="copie",   # export fidèle seulement : "copie" (nouvelle table) ou "remplacer" (restauration sur place de la table native)
) -> TableDynamique:
    """Lit le fichier et crée TableDynamique + champs + lignes.

    - progression_cb(pct) est appelé régulièrement si fourni (0-100).
    - Pandas lit et porte les données de bout en bout (Polars n'intervient jamais à l'entrée ni à la sortie).
    - bulk_create par batch de BATCH_SIZE lignes.
    - Si le fichier dépasse la limite de lignes configurée pour l'entreprise,
      le fichier n'est PLUS rejeté : il est automatiquement découpé en
      plusieurs TableDynamique liées entre elles par un GroupeTables (voir
      _importer_vers_tables_decoupees ci-dessous). La valeur de retour est
      alors la PREMIÈRE partie ; `table.groupe` donne accès à l'ensemble
      complet et `table.partie_suivante` permet de parcourir la chaîne.
    """
    ext = _ext(fichier_django)
    if ext not in EXTENSIONS_ACCEPTEES:
        raise ValueError(
            tf("engine/import_fichier.py:g:469:13", v1=ext or 'inconnu')
        )
    from . import securite_dates
    securite_dates.controler(entreprise.id)   # horloge revenue en arrière : refus AVANT d'écrire la moindre ligne
    # Export fait par la plateforme (manifeste intégré) : RESTAURATION FIDÈLE, aucun type n'est deviné.
    from . import export_fidele
    manifeste = export_fidele.lire(fichier_django, ext)
    if manifeste is not None:
        return export_fidele.restaurer(entreprise, manifeste, progression_cb=progression_cb, mode=mode_restauration)
    # Sinon (JSON ordinaire compris) : import ordinaire, table INDÉPENDANTE — colonnes et types déduits du contenu.

    def _prog(pct):
        if progression_cb:
            try:
                progression_cb(int(pct))
            except Exception:
                pass

    _prog(2)

    # ── Lecture ──────────────────────────────────────────────────────────────
    dataframe = _lire_dataframe(
        fichier_django, feuille=feuille, separateur=separateur, encodage=encodage
    )
    nb_lignes = len(dataframe)

    if dataframe.empty or len(dataframe.columns) == 0:
        raise ValueError(t("engine/import_fichier.py:g:505:26"))

    _prog(5)

    nom_fichier = fichier_django.name
    nom_base = os.path.splitext(nom_fichier)[0][:120] or t("engine/import_fichier.py:g:510:58")
    if feuille and ext in ("xlsx", "xls"):
        nom_base = f"{nom_base} — {feuille}"[:120]

    description_base = (
        tf("engine/import_fichier.py:g:515:9", v1=nom_fichier)
        + (_gtf("rg6.msg.026", v1=feuille) if feuille else "")
    )

    return _importer_dataframe_vers_tables(
        entreprise=entreprise,
        nom_base=nom_base,
        dataframe=dataframe,
        nb_lignes=nb_lignes,
        colonnes=list(dataframe.columns),
        description_base=description_base,
        provient_fichier=True,
        nom_fichier_origine=nom_fichier,
        connexion_externe_origine=None,
        nom_source_distante="",
        progression_cb=_prog,
        type_stockage=type_stockage,
    )


# ── Cœur commun (fichier ET base externe) ────────────────────────────────────

def _importer_dataframe_vers_tables(
    *, entreprise, nom_base, dataframe, nb_lignes, colonnes,
    description_base="", provient_fichier=False, nom_fichier_origine="",
    connexion_externe_origine=None, nom_source_distante="",
    progression_cb=None, type_stockage="sql",
) -> TableDynamique:
    """Cœur commun de création de TableDynamique à partir de données déjà
    chargées en mémoire (issues d'un fichier CSV/Excel OU d'une requête vers
    une base de données externe — voir connexion_externe.py).

    Comportement clé : la limite de lignes configurée pour l'entreprise
    (ConfigurationIA.limite_lignes) ne sert PLUS jamais à rejeter les
    données. Si le volume la dépasse, les données sont réparties en
    plusieurs TableDynamique de taille ≤ limite, rattachées entre elles par
    un GroupeTables et chaînées via partie_precedente/partie_suivante, de
    sorte que le système sache toujours explicitement que telle table est
    la suite de telle autre — sans jamais perdre une seule ligne.
    """
    from .models import ConfigurationIA, GroupeTables

    def _prog(pct):
        if progression_cb:
            try:
                progression_cb(pct)
            except Exception:
                pass

    # Limite de l'entreprise, TOUJOURS bornée par le plafond absolu (100 000) : « illimité » (0) ou un réglage plus grand ne produisent
    # jamais une table géante, l'excédent part dans une table suivante du même groupe (la précédente, pleine, ne reçoit plus de lignes).
    limite_effective = TableDynamique.plafond_pour_entreprise(entreprise)

    # Échantillon pour l'inférence de type (identique quel que soit le découpage) : 5000 premières lignes du DataFrame pandas
    df_echantillon = dataframe.head(5000)
    infos_colonnes = _colonnes_avec_types(colonnes, df_echantillon)

    _prog(10)

    champs_communs = dict(
        provient_fichier=provient_fichier,
        nom_fichier_origine=nom_fichier_origine,
        connexion_externe_origine=connexion_externe_origine,
        nom_source_distante=nom_source_distante,
    )

    # ── Cas simple : tout tient dans une seule table (comportement historique) ──
    # Tout le bloc (table + champs + TOUTES les lignes) est enveloppé dans
    # UNE transaction : avant ce correctif, un import qui échouait en cours
    # de route (fichier corrompu au milieu, coupure, erreur d'un batch...)
    # laissait une table à moitié remplie, visible et utilisable telle
    # quelle. Désormais soit l'import complet est committé, soit rien ne
    # l'est — jamais d'état intermédiaire visible.
    if not limite_effective or nb_lignes <= limite_effective:
        with transaction.atomic():
            table = TableDynamique.objects.create(
                entreprise=entreprise, nom=nom_base,
                slug=_slug_table_unique(entreprise, nom_base),
                description=description_base,
                type_stockage=type_stockage,
                **champs_communs,
            )
            champs = _creer_champs_pour_table(table, infos_colonnes)
            _prog(15)
            prog_insertion = _fenetre_progression(_prog, 15, 80, 0, nb_lignes, nb_lignes)
            if type_stockage == TableDynamique.STOCKAGE_NOSQL:
                from . import nosql_store
                nosql_store.inserer_lignes_dataframe(table, champs, dataframe, colonnes, nb_lignes, prog_insertion)
            else:
                _inserer_lignes_pandas(table, champs, dataframe, colonnes, nb_lignes, prog_insertion)
        _prog(100)
        _emettre_dataset_importe(table, nb_lignes, nom_fichier_origine or nom_source_distante)
        return table

    # ── Cas découpage : plusieurs tables liées par un GroupeTables ─────────────
    # Même principe : tout le groupe de tables est atomique. Un échec sur la
    # partie 7/12, par exemple, annule aussi les parties 1 à 6 déjà écrites
    # dans CETTE transaction — plutôt que de laisser un GroupeTables
    # incomplet, chaîné jusqu'à une partie qui n'existe pas.
    taille_partie = limite_effective
    nb_parties = math.ceil(nb_lignes / taille_partie)

    absolu = taille_partie >= TableDynamique.PLAFOND_ABSOLU_LIGNES   # plafond absolu (100 000) atteint : on s'arrête là, tables INDÉPENDANTES (pas de groupe), renommables
    # Un groupe ne dépasse jamais le plafond absolu : limite 1 000 → 10 tables de 1 000 lignes par groupe, donc 30 000 lignes = 3 groupes
    # de 10 tables. Si le plafond ne contient pas deux tables de cette taille (limite > 5 000), on garde un seul groupe (comportement historique).
    tables_par_groupe = max(1, TableDynamique.PLAFOND_ABSOLU_LIGNES // taille_partie)
    if absolu or tables_par_groupe < 2:
        tables_par_groupe = nb_parties
    nb_groupes = 1 if absolu else math.ceil(nb_parties / tables_par_groupe)
    with transaction.atomic():
        groupe = None
        table_precedente = None
        table_principale = None
        lignes_avant = 0

        for i in range(nb_parties):
            debut = i * taille_partie
            fin = min(debut + taille_partie, nb_lignes)
            nb_lignes_partie = fin - debut

            g, rang = divmod(i, tables_par_groupe)   # g = n° du groupe (0…), rang = position de la table dans SON groupe (0…)
            tables_du_groupe = min(tables_par_groupe, nb_parties - g * tables_par_groupe)
            if not absolu and rang == 0:   # début d'un groupe : nouveau GroupeTables, nouvelle chaîne
                lignes_du_groupe = min((g + 1) * tables_par_groupe * taille_partie, nb_lignes) - g * tables_par_groupe * taille_partie
                nom_groupe = nom_base if nb_groupes == 1 else f"{nom_base} (groupe {g + 1}/{nb_groupes})"
                groupe = GroupeTables.objects.create(
                    entreprise=entreprise,
                    nom=nom_groupe[:120],
                    description=(
                        tf("engine/import_fichier.py:g:638:17", v1=description_base, v2=lignes_du_groupe, v3=taille_partie, v4=tables_du_groupe)
                    ),
                    cree_automatiquement=True,
                )
                table_precedente = None

            if absolu:
                nom_table_partie = nom_base if i == 0 else f"{nom_base} (suite {i + 1})"
            elif nb_groupes == 1:
                nom_table_partie = f"{nom_base} (partie {i + 1}/{nb_parties})"
            else:
                nom_table_partie = f"{nom_base} (groupe {g + 1}/{nb_groupes}, partie {rang + 1}/{tables_du_groupe})"
            nom_table_partie = nom_table_partie[:120]
            table = TableDynamique.objects.create(
                entreprise=entreprise, nom=nom_table_partie,
                slug=_slug_table_unique(entreprise, nom_table_partie),
                description=(
                    tf("engine/import_fichier.py:g:659:21", v1=description_base, v2=i + 1, v3=nb_parties, v4=debut + 1, v5=fin, v6=nb_lignes)
                ),
                **({} if absolu else dict(groupe=groupe, partie_index=rang + 1, partie_total=tables_du_groupe, partie_precedente=table_precedente)),
                type_stockage=type_stockage,
                **champs_communs,
            )
            champs = _creer_champs_pour_table(table, infos_colonnes)

            prog_insertion = _fenetre_progression(_prog, 15, 80, lignes_avant, nb_lignes_partie, nb_lignes)
            if type_stockage == TableDynamique.STOCKAGE_NOSQL:
                from . import nosql_store
                nosql_store.inserer_lignes_dataframe(table, champs, dataframe.iloc[debut:fin], colonnes, nb_lignes_partie, prog_insertion)
            else:
                df_partie = dataframe.iloc[debut:fin]
                _inserer_lignes_pandas(table, champs, df_partie, colonnes, nb_lignes_partie, prog_insertion)

            table_precedente = table
            table_principale = table_principale or table
            lignes_avant += nb_lignes_partie

    _prog(100)
    _emettre_dataset_importe(table_principale, nb_lignes, nom_fichier_origine or nom_source_distante)
    return table_principale


def _emettre_dataset_importe(table, nb_lignes, source):
    """Événement "DatasetImported" (voir evenements.py) : un seul événement
    résumé par import (fichier OU base externe, voir connexion_externe.py qui
    appelle _importer_dataframe_vers_tables), pas un par ligne — les lignes
    individuelles sont déjà journalisées séparément ("creation_ligne", voir
    _inserer_lignes_pandas)."""
    from . import evenements as _evenements
    _evenements.emettre(
        table.entreprise_id, "DatasetImported",
        table_id=table.id, lignes=nb_lignes, source=source,
    )


def _inserer_lignes_pandas(table, champs, dataframe, colonnes, nb_lignes, prog_cb):
    """Insertion par batch depuis un DataFrame pandas."""
    from .models import Bloc

    nb_champs = len(champs)
    # bulk_create des Enregistrement
    enregistrements = [Enregistrement(table=table) for _ in range(nb_lignes)]
    Enregistrement.objects.bulk_create(enregistrements)
    enregistrements = list(table.enregistrements.order_by("id"))

    valeurs_batch = []
    # Un bloc blockchain PAR LOT (BATCH_SIZE lignes) au lieu d'un bloc par
    # ligne : avant ce correctif, chaque ligne importée déclenchait son
    # propre verrouillage + lecture du dernier bloc + hash + écriture,
    # rendant l'import très lent sur les gros fichiers (des milliers
    # d'allers-retours BD au lieu de quelques dizaines). Aucune preuve
    # n'est perdue : chaque transaction du lot reste individuellement
    # présente dans le bloc (voir Bloc.ajouter_transactions).
    tx_batch = []
    for ligne_idx, (_, ligne) in enumerate(dataframe.iterrows()):
        enr = enregistrements[ligne_idx]
        donnees_ligne = {}
        for champ, colonne in zip(champs, colonnes):
            valeur_stockee = _valeur_pour_stockage(ligne[colonne], champ.type_champ)
            valeurs_batch.append(ValeurChamp(
                enregistrement=enr, champ=champ, valeur=valeur_stockee,
            ))
            donnees_ligne[champ.nom] = valeur_stockee
        # bulk_create ne déclenche pas post_save (voir models.Bloc) : on
        # journalise nous-mêmes, avec les VRAIES valeurs importées (pas
        # seulement les identifiants — voir apps.py) pour que le bloc soit
        # une preuve exploitable de ce qui a été importé.
        tx_batch.append({
            "type": "creation_ligne", "table_id": table.id, "table": table.nom,
            "enregistrement_id": enr.id, "agent_id": None, "donnees": donnees_ligne,
        })
        if len(valeurs_batch) >= BATCH_SIZE * nb_champs:
            ValeurChamp.objects.bulk_create(valeurs_batch, batch_size=2000)
            valeurs_batch = []
            Bloc.ajouter_transactions(table.entreprise_id, tx_batch)
            tx_batch = []
            pct_local = int((ligne_idx + 1) / nb_lignes * 100) if nb_lignes else 100
            prog_cb(pct_local)

    if valeurs_batch:
        ValeurChamp.objects.bulk_create(valeurs_batch, batch_size=2000)
    if tx_batch:
        Bloc.ajouter_transactions(table.entreprise_id, tx_batch)
    from . import parquet_store
    parquet_store.planifier_synchro(table)   # fichier Parquet de la table, une fois l'import validé
