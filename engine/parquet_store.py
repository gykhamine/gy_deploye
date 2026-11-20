"""Miroir Parquet des tables dynamiques EAV (TableDynamique.type_stockage == "sql").

Principe : le modèle EAV (Enregistrement + ValeurChamp) reste le stockage d'ORIGINE, jamais remplacé. À côté, chaque table
EAV est reflétée sur le disque en UN fichier Parquet au format analytique — une ligne par enregistrement, une colonne par
champ dynamique, déjà typées selon le schéma EAV (metadata.schema_pour_executor + executor._appliquer_schema) — pour que
les analyses lisent ce fichier au lieu de reconstruire la table depuis les ValeurChamp à chaque lecture.

  * Écriture : à l'enregistrement d'une nouvelle ligne, `synchroniser_ligne` ajoute la ligne au fichier (le crée s'il est absent).
  * Création automatique : `lire_dataframe` crée le fichier à partir des données EAV existantes s'il est absent.
  * Lecture analytique : POINT D'ENTRÉE UNIQUE (voir la section « Lecture analytique » en bas du module) : toutes les
    fonctionnalités qui lisent une table pour l'analyser (formules, graphiques, KPI, statistiques, séries, agent, compétences,
    ontologie, cartes, IA) passent par `donnees_analytiques` / `donnees_dict` / `lignes_analytiques` / `donnees_ecriture`.

MODIFICATION À CHAUD (socle + delta) : une écriture ne réécrit JAMAIS le gros fichier. Le fichier de la table est un SOCLE ; les lignes
ajoutées ou modifiées depuis son écriture vont dans un petit fichier DELTA voisin (`<socle>.delta`, cumulatif, au coût proportionnel au
nombre de lignes changées) qui porte l'empreinte du socle auquel il s'applique. La lecture fusionne les deux grâce à l'index des `_id`
(l'ordre et les suppressions viennent de l'EAV : une seule colonne d'ids), une ligne du delta remplace celle du socle. Un delta qui ne
correspond plus au socle (compactage concurrent) est simplement ignoré. Le socle n'est réécrit (compactage, delta absorbé et supprimé)
que si les colonnes changent ou si le delta dépasse SEUIL_DELTA_MIN lignes et 1/4 de la table (5000 au plus).

Fraîcheur : chaque fichier porte l'empreinte de la table au moment de son écriture (nombre de lignes + dernière modification,
même principe que metadata.empreinte_donnees, mais PAR table). Si l'empreinte actuelle diffère (cellule modifiée, ligne
supprimée, import en masse...), le fichier est mis à jour depuis l'EAV avant d'être lu (INCRÉMENTAL : seules les lignes ajoutées ou modifiées sont relues ; reconstruction complète au-delà de la moitié des lignes) : une analyse ne lit jamais un fichier périmé.

Périmètre : UNIQUEMENT les tables EAV. Les tables NoSQL (déjà reflétées en JSON, voir nosql_store.py) ne sont jamais touchées :
chaque fonction publique renvoie None / ne fait rien pour elles. Idem si pyarrow n'est pas installé (dépendance déclarée
dans requirements.txt) ou si la lecture/écriture échoue : la lecture retombe alors sur l'EAV, À UN SEUL ENDROIT (`_lire_eav`), jamais dans les modules d'analyse.

GROUPES ET CONTENEURS : il n'existe JAMAIS de fichier de groupe — un fichier par table PHYSIQUE. Un groupe est assemblé à la lecture
(`_parties_analytiques` : mêmes parties, même ordre que TableDynamique.enregistrements_groupe — parties non dénaturées, par partie_index
puis id de table, lignes par date de création puis id), si bien que rattacher, détacher ou réordonner des parties ne touche à aucun
fichier et que l'appartenance ne peut pas être périmée. Les parties sont réalignées sur la structure de la table lue (colonnes
manquantes vides, types recalés) et toute divergence est signalée (`structure_groupe_divergente`). Un ConteneurTables ne range que des
tables et des groupes : il n'a aucun lien avec les fichiers.

IMAGE FIDÈLE : en plus des colonnes typées (celles des analyses), chaque ligne du fichier porte `_brut` : le texte EXACT de ses cellules dans
l'EAV (ValeurChamp.valeur, virgule ou point, zéros, espaces compris), en JSON. Rien de ce que l'EAV retient n'est perdu par le typage ; la
vérification compare `_brut` au texte EAV, cellule par cellule. Les analyses ne lisent jamais cette colonne (lecture par colonnes).

COHÉRENCE DES TYPES (EAV ↔ Parquet) : un SEUL constructeur (`_frame_eav`) fabrique le DataFrame typé d'une table depuis l'EAV ; il sert à
écrire le fichier ET au repli `_lire_eav` : les deux chemins de lecture donnent donc les mêmes colonnes, le même ordre et les mêmes types.
Chaque fichier (socle et delta) déclare dans ses métadonnées (`agentgy_schema`) le type EAV et la famille de dtype de chacune de ses
colonnes ; à la lecture, un fichier sans déclaration, dont un type EAV a changé ou dont une colonne ne revient pas dans la famille déclarée
est REFUSÉ et reconstruit depuis l'EAV (jamais lu de travers). `verifier_table` signale en plus les cellules dont le texte EAV n'est pas
convertible dans le type de la colonne (« 1,5 » dans un champ décimal) : l'analyse les voit vides, le rapport les nomme.

Colonnes à valeurs imbriquées (choix multiple, tenseurs, JSON libre) ou de types mêlés (champ formule) : stockées en texte JSON
dans le fichier (liste tenue dans les métadonnées Parquet) et décodées à la lecture — l'analyse retrouve les mêmes objets Python.
"""
import contextvars
import glob
import json
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from engine.i18n import tf as _gtf  # noqa: E402
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

COLONNE_ID = "_id"                      # id de l'Enregistrement (identifiant de l'Enregistrement)
_CLE_EMPREINTE = b"agentgy_empreinte"   # métadonnée Parquet : empreinte de la table à l'écriture
_CLE_JSON = b"agentgy_colonnes_json"    # métadonnée Parquet : colonnes stockées en texte JSON
_CLE_BASE = b"agentgy_empreinte_socle"  # métadonnée du fichier delta : empreinte du socle auquel il s'applique
_CLE_SCHEMA = b"agentgy_schema"         # métadonnée Parquet : type EAV et famille de dtype de chaque colonne, tels qu'écrits
COLONNE_BRUT = "_brut"                  # texte EAV exact de chaque cellule de la ligne (JSON) : le fichier est l'IMAGE FIDÈLE de la table
FORMAT_SCHEMA = 2                       # version du format de cette déclaration (changer = tous les fichiers sont reconstruits)
SEUIL_DELTA_MIN = 200                   # en deçà, un delta est toujours préféré ; au-delà : au plus 1/4 des lignes (et 5000), sinon compactage


def disponible() -> bool:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
        return True
    except ImportError:
        return False


_etat = threading.local()


@contextmanager
def en_pause():
    """Suspend, dans CE fil, toute synchronisation et toute lecture du Parquet (restauration d'une archive : les fichiers sont remis en
    place tels qu'archivés, rien ne doit les reconstruire entre-temps). Les lectures retombent sur l'EAV pendant la pause."""
    avant = getattr(_etat, "pause", False)
    _etat.pause = True
    try:
        yield
    finally:
        _etat.pause = avant


def _concerne(table) -> bool:
    """Seules les tables EAV (SQL) ont un fichier Parquet, et seulement si pyarrow est présent (et hors pause)."""
    return not table.est_nosql and not getattr(table, "source_systeme", "") and not getattr(_etat, "pause", False) and disponible()


def _dossier(table, creer=True) -> str:
    from django.conf import settings
    dossier = os.path.join(settings.MEDIA_ROOT, "parquet_tables", str(table.entreprise_id))
    if creer:
        os.makedirs(dossier, exist_ok=True)
    return dossier


def chemin_fichier(table) -> str:
    """Chemin du fichier Parquet de cette table EAV (même arborescence que nosql_store : media/parquet_tables/<entreprise>/)."""
    slug = re.sub(r"[^\w.-]", "_", str(table.slug or ""))
    return os.path.join(_dossier(table), f"{table.id}_{slug}.parquet")


def _fichiers_de(table) -> list:
    """TOUS les fichiers Parquet de la table, quel qu'ait été son slug : renommer une table change le nom du fichier, l'ancien ne doit
    pas rester orphelin."""
    return glob.glob(os.path.join(glob.escape(_dossier(table, creer=False)), f"{table.id}_*.parquet"))


def sur_validation(cle, fonction) -> None:
    """transaction.on_commit DÉDOUBLONNÉ par `cle` : une seule exécution par transaction, même si la lecture est répétée cinquante fois
    (le test porte sur les rappels réellement en attente, donc une transaction annulée ne bloque jamais les suivantes)."""
    from django.db import connection, transaction
    if any(getattr(e[1], "_cle_parquet", None) == cle for e in getattr(connection, "run_on_commit", [])):
        return
    fonction._cle_parquet = cle
    transaction.on_commit(fonction)


def empreinte_table(table, exclure_pk=None) -> str:
    """Empreinte légère de l'état des lignes DE CETTE TABLE (nombre + dernière modification) — voir metadata.empreinte_donnees
    (qui, elle, couvre tout le groupe). `exclure_pk` : calcule l'état sans une ligne donnée (contrôle de la synchronisation d'une ligne)."""
    from django.db.models import Count, Max
    lignes = table.enregistrements.all()
    if exclure_pk is not None:
        lignes = lignes.exclude(pk=exclure_pk)
    agg = lignes.aggregate(n=Count("id"), m=Max("maj_le"))
    return f"{agg['n']}:{agg['m'].isoformat() if agg['m'] else 'vide'}|{_signature_schema(table)}"


def _signature_schema(table) -> str:
    """Empreinte des COLONNES de la table (id, slug, type) : ajouter, supprimer, renommer ou retyper une colonne rend le fichier
    périmé même si aucune ligne n'a changé (sinon la nouvelle colonne manquerait silencieusement aux analyses)."""
    import hashlib
    champs = sorted(table.champs.values_list("id", "slug", "type_champ"))
    return hashlib.sha1(repr(champs).encode("utf-8")).hexdigest()[:12]


# ── Fichier ────────────────────────────────────────────────────────────────────────────────────────────────────

def _metadonnees(chemin):
    """Métadonnées Parquet (lecture du pied de fichier seulement). None : fichier absent ou illisible (il sera recréé)."""
    if not os.path.exists(chemin):
        return None
    try:
        import pyarrow.parquet as pq
        return pq.read_schema(chemin).metadata or {}
    except Exception:
        logger.warning(_gt("rg6.msg.080"), chemin)
        return None


def _non_nul(valeur) -> bool:
    import pandas as pd
    if isinstance(valeur, (list, dict, tuple, set)):
        return True
    try:
        return not pd.isna(valeur)
    except (TypeError, ValueError):
        return True


def _famille(dtype) -> str:
    """Famille d'un dtype pandas sans unité ni fuseau (« datetime64[ns] » et « datetime64[us] » : même famille) : la comparaison ne
    dépend pas de la version de pandas/pyarrow qui a relu le fichier."""
    return str(dtype).split("[")[0]


def _famille_attendue(type_champ) -> str:
    """Famille de dtype que executor._cast_serie donne à un type EAV quand les valeurs sont convertibles."""
    if type_champ in ("nombre", "note", "barre_progression"):
        return "Int64"
    if type_champ in ("decimal", "monnaie", "pourcentage", "constante"):
        return "Float64"
    if type_champ in ("booleen", "switch"):
        return "boolean"
    if type_champ in ("date", "datetime", "horodatage"):
        return "datetime64"
    if type_champ == "duree":
        return "timedelta64"
    return "object"


def _schema_declare(df, schema) -> bytes:
    """Déclaration écrite dans les métadonnées du fichier : {colonne: {type EAV, famille de dtype}} du DataFrame TYPÉ (avant l'encodage JSON)."""
    colonnes = {c: {"type": (schema or {}).get(c), "famille": _famille(df[c].dtype)} for c in df.columns if c not in (COLONNE_ID, COLONNE_BRUT)}
    return json.dumps({"version": FORMAT_SCHEMA, "colonnes": colonnes}, ensure_ascii=False).encode("utf-8")


def _schema_lu(meta, table, chemin) -> dict:
    """Déclaration de types d'un fichier, contrôlée AVANT décodage : absente, d'une autre version ou en désaccord avec les types EAV
    actuels de la table → ValueError (l'appelant reconstruit depuis l'EAV)."""
    from .metadata import schema_pour_executor
    brut = meta.get(_CLE_SCHEMA)
    if not brut:
        raise ValueError(_gtf("rg5.msg.092", v1=chemin))
    declare = json.loads(brut)
    if declare.get("version") != FORMAT_SCHEMA:
        raise ValueError(_gtf("rg5.msg.093", v1=chemin))
    colonnes = declare.get("colonnes", {})
    actuel = schema_pour_executor(table)
    ecarts = [c for c, d in colonnes.items() if c in actuel and d.get("type") != actuel[c]]
    if ecarts:
        raise ValueError(_gtf("rg5.msg.094", v1=chemin, v2=ecarts))
    return colonnes


def _controler_familles(df, declare, chemin) -> None:
    """Après recalage de typage : chaque colonne lue doit être dans la famille déclarée à l'écriture, sinon ValueError."""
    ecarts = [c for c, d in declare.items() if c in df.columns and _famille(df[c].dtype) != d.get("famille")]
    if ecarts:
        raise ValueError(_gtf("rg5.msg.095", v1=chemin, v2=ecarts))


def _encoder_json(df):
    """(DataFrame prêt pour Parquet, colonnes encodées en JSON) : valeurs imbriquées ou de types mêlés → texte JSON."""
    import pyarrow as pa
    df = df.copy()
    colonnes = []

    def encoder(col):
        df[col] = df[col].map(lambda v: json.dumps(v, ensure_ascii=False, default=str) if _non_nul(v) else None)
        colonnes.append(col)

    for col in df.columns:
        if df[col].dtype == object and any(isinstance(v, (list, dict, tuple, set)) for v in df[col]):
            encoder(col)
    for col in df.columns:
        if col in colonnes or df[col].dtype != object:
            continue
        try:
            pa.array(df[col], from_pandas=True)
        except Exception:   # types mêlés dans une même colonne (ex. champ formule : nombre et texte)
            encoder(col)
    return df, colonnes


def _ecrire_parquet(chemin, df, meta_extra, schema=None) -> None:
    """Écriture atomique : fichier temporaire PROPRE À CETTE ÉCRITURE (deux processus qui écrivent la même table en même temps ne se
    marchent plus dessus) puis remplacement ; jamais de fichier à moitié écrit lisible par un tiers, jamais de temporaire laissé
    derrière une erreur."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    declare = _schema_declare(df, schema)
    df, colonnes_json = _encoder_json(df)
    table_pa = pa.Table.from_pandas(df, preserve_index=False)
    meta = dict(table_pa.schema.metadata or {})
    meta.update(meta_extra)
    meta[_CLE_JSON] = json.dumps(colonnes_json).encode("utf-8")
    meta[_CLE_SCHEMA] = declare
    fd, chemin_tmp = tempfile.mkstemp(dir=os.path.dirname(chemin), prefix=os.path.basename(chemin) + ".", suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(table_pa.replace_schema_metadata(meta), chemin_tmp)
        os.chmod(chemin_tmp, 0o644)   # mkstemp crée en 0600 : les autres processus (workers) doivent pouvoir relire le fichier
        os.replace(chemin_tmp, chemin)
    except BaseException:
        _supprimer_fichier(chemin_tmp)
        raise


def _ecrire(table, df, empreinte) -> None:
    """(Ré)écrit le SOCLE (compactage : il absorbe tout delta). Retire les fichiers d'un ancien slug, les deltas devenus inutiles et les
    temporaires abandonnés depuis plus d'une heure."""
    chemin = chemin_fichier(table)
    from .metadata import schema_pour_executor
    _ecrire_parquet(chemin, df, {_CLE_EMPREINTE: empreinte.encode("utf-8")}, schema_pour_executor(table))
    dossier = glob.escape(os.path.dirname(chemin))
    for autre in _fichiers_de(table) + glob.glob(os.path.join(dossier, f"{table.id}_*.parquet.delta")):
        if autre != chemin:
            _supprimer_fichier(autre)
    for reste in glob.glob(os.path.join(dossier, f"{table.id}_*.parquet.*.tmp")):
        try:
            if time.time() - os.path.getmtime(reste) > 3600:
                os.remove(reste)
        except OSError:
            pass


def _ecrire_delta(table, delta, empreinte, empreinte_socle) -> None:
    """Écrit le petit fichier DELTA (lignes ajoutées ou modifiées depuis le socle) ; le socle n'est pas touché."""
    from .metadata import schema_pour_executor
    _ecrire_parquet(chemin_fichier(table) + ".delta", delta,
                    {_CLE_EMPREINTE: empreinte.encode("utf-8"), _CLE_BASE: empreinte_socle.encode("utf-8")},
                    schema_pour_executor(table))


def _lire_fichier(chemin, colonnes=None, table=None, ids=None):
    """DataFrame du fichier ; `colonnes` (slugs, `_id` compris) limite la lecture à ces colonnes (celles qui n'existent pas sont ignorées).
    Avec `table` : le fichier est lu SOUS CONTRÔLE de sa déclaration de types (voir _schema_lu / _controler_familles) puis recalé sur le
    typage des analyses ; ValueError s'il n'est pas fiable — l'appelant reconstruit alors depuis l'EAV. Sans `table` : lecture brute
    (ids seulement, par exemple). `ids` : ne lit que les lignes de ces `_id` (filtre poussé dans le fichier : les groupes de lignes sans\n    aucun de ces ids ne sont pas lus) — c'est ce qui permet d'afficher une page de 200 lignes d'une table de millions de lignes."""
    import pyarrow.parquet as pq
    if colonnes is not None:
        noms = set(pq.read_schema(chemin).names)
        colonnes = [c for c in colonnes if c in noms]
    filtres = None if ids is None else [(COLONNE_ID, "in", [int(i) for i in ids])]
    table_pa = pq.read_table(chemin, columns=colonnes, filters=filtres)
    meta = table_pa.schema.metadata or {}
    declare = _schema_lu(meta, table, chemin) if table is not None else None
    df = table_pa.to_pandas()
    for col in json.loads(meta.get(_CLE_JSON, b"[]")):
        if col in df.columns:
            df[col] = df[col].map(lambda v: json.loads(v) if isinstance(v, str) else None)
    if table is not None:
        df = _normaliser(table, df)
        _controler_familles(df, declare, chemin)
    return df


def _normaliser(table, df):
    """Même typage que celui des analyses (executor._appliquer_schema) : garantit les mêmes dtypes quelle que soit la
    version de pandas/pyarrow qui a relu le fichier."""
    from .executor import _appliquer_schema
    from .metadata import schema_pour_executor
    return _appliquer_schema(df, schema_pour_executor(table))


def _vide(table):
    """DataFrame SANS ligne mais avec toutes les colonnes (typées) et `_id` : le fichier d'une table vide existe dès sa création."""
    import pandas as pd
    df = _normaliser(table, pd.DataFrame({c.slug: pd.Series([], dtype=object) for c in table.champs.all()}))
    df.insert(0, COLONNE_ID, pd.Series([], dtype="int64"))
    df[COLONNE_BRUT] = pd.Series([], dtype=object)
    return df


def _frame_eav(table, enregistrements):
    """CONSTRUCTEUR UNIQUE du DataFrame typé des `enregistrements` depuis l'EAV (sans `_id`) : une colonne par champ de `table`, dans l'ordre
    de ses champs, typée comme les analyses. Sert à écrire les fichiers Parquet ET au repli `_lire_eav` : les deux chemins de lecture ne
    peuvent donc pas diverger. Réutilise metadata.table_vers_dict (une seule requête pour toutes les ValeurChamp)."""
    from .metadata import table_vers_dict
    from .moteur_donnees import dataframe_depuis
    return _normaliser(table, dataframe_depuis(table_vers_dict(table, enregistrements)))


def _bruts(table, enregistrements) -> list:
    """Texte EAV exact des cellules de chaque ligne : une chaîne JSON {slug: texte} par enregistrement (None si la ligne n'a aucune valeur)."""
    from .models import ValeurChamp
    ids = [e.id for e in enregistrements]
    par_ligne = {}
    valeurs = (ValeurChamp.objects.filter(enregistrement_id__in=ids, champ__table=table).exclude(valeur__isnull=True)
               .values_list("enregistrement_id", "champ__slug", "valeur"))
    for enregistrement_id, slug, valeur in valeurs.iterator():
        par_ligne.setdefault(enregistrement_id, {})[slug] = valeur
    return [json.dumps(par_ligne[i], ensure_ascii=False, sort_keys=True) if i in par_ligne else None for i in ids]


def _construire(table, enregistrements):
    """DataFrame typé des `enregistrements` (liste de CETTE table) construit depuis l'EAV, avec `_id` et `_brut` (texte EAV exact)."""
    df = _frame_eav(table, enregistrements)
    df.insert(0, COLONNE_ID, [e.id for e in enregistrements])
    df[COLONNE_BRUT] = pd_serie_objets(_bruts(table, enregistrements))
    return df


def pd_serie_objets(valeurs):
    import pandas as pd
    return pd.Series(valeurs, dtype=object)


# ── Synchronisation ────────────────────────────────────────────────────────────────────────────────────────────

def synchroniser_fichier(table):
    """(Re)construit le fichier Parquet de la table depuis l'EAV, qui reste la source de vérité. Sert aussi à créer le fichier
    absent. Retourne le DataFrame écrit (avec `_id`) ; une table sans ligne a un fichier vide (colonnes seules)."""
    empreinte = empreinte_table(table)   # AVANT la lecture des lignes : une écriture concurrente rend le fichier « périmé », jamais faussement « à jour »
    enregistrements = list(table.enregistrements.order_by("cree_le", "id"))
    df = _construire(table, enregistrements) if enregistrements else _vide(table)
    _ecrire(table, df, empreinte)
    return df


def synchroniser_table(table) -> None:
    """À appeler à la CRÉATION d'une table EAV et quand ses colonnes changent : crée le fichier Parquet (vide si la table n'a pas de
    ligne) ou le met à jour. Ne relit pas le fichier s'il est déjà frais. Meilleur effort : n'échoue jamais."""
    if not _concerne(table):
        return
    try:
        if _fraicheur(chemin_fichier(table), empreinte_table(table))[0]:
            return
        _frame_a_jour(table, ecrire=True, differer=False)
    except Exception:
        logger.exception(_gt("rg6.msg.081"), getattr(table, "pk", "?"))


def synchroniser_entreprise(entreprise) -> None:
    """Après une restauration : chaque table EAV de l'entreprise retrouve un fichier à jour. Un fichier archivé encore frais n'est PAS relu
    (contrôle des métadonnées seulement) ; seuls un fichier absent (ancienne archive) ou périmé sont (re)construits. Meilleur effort."""
    from .models import TableDynamique
    for table in TableDynamique.objects.filter(entreprise=entreprise).exclude(type_stockage=TableDynamique.STOCKAGE_NOSQL):
        synchroniser_table(table)


def planifier_synchro(table) -> None:
    """Met à jour le fichier Parquet de cette table EAV une fois la transaction en cours validée (tout de suite hors transaction) :
    UNE mise à jour par table et par transaction, incrémentale (seules les lignes ajoutées, modifiées ou supprimées comptent).
    À appeler après toute écriture dans l'EAV (modification, suppression, import, flux) : le fichier suit l'EAV sans attendre la
    prochaine lecture. Meilleur effort : n'échoue jamais — en cas d'erreur la prochaine lecture ou la veille (parquet_veille) rattrape."""
    if not _concerne(table):
        return
    table_id = table.pk

    def _faire():
        try:
            from .models import TableDynamique
            courante = TableDynamique.objects.filter(pk=table_id).first()
            if courante is not None:
                synchroniser_table(courante)
        except Exception:
            logger.exception(_gt("rg6.msg.082"), table_id)
    sur_validation(("table", table_id), _faire)


def supprimer_fichier(table) -> None:
    """À appeler quand une table EAV est supprimée : retire son fichier Parquet (sinon il resterait orphelin)."""
    dossier = glob.escape(_dossier(table, creer=False))
    for chemin in _fichiers_de(table) + glob.glob(os.path.join(dossier, f"{table.id}_*.parquet.delta")):   # sans condition : une table devenue NoSQL, ou pyarrow retiré, ne doit laisser aucun orphelin
        _supprimer_fichier(chemin)


def synchroniser_ligne(enregistrement) -> None:
    """À appeler quand une NOUVELLE ligne d'une table EAV vient d'être enregistrée (valeurs et formules écrites) : met le fichier
    Parquet à jour (incrémental : seule la nouvelle ligne est relue dans l'EAV ; fichier absent : création complète).
    Meilleur effort : n'échoue jamais — en cas d'erreur le fichier restera « périmé » et sera mis à jour à la prochaine lecture."""
    table = enregistrement.table
    if not _concerne(table):
        return
    try:
        _frame_a_jour(table, ecrire=True, differer=False)
    except Exception:
        logger.exception(_gt("rg6.msg.083"), table.pk, enregistrement.pk)


# ── Vérification de cohérence EAV ↔ Parquet ────────────────────────────────────────────────────────────────────

def _colonnes_divergentes(actuel, attendu) -> list:
    """Colonnes dont le CONTENU diffère entre deux DataFrames (`_id` compris, même ordre de lignes) ; ["*"] si la forme diffère
    (colonnes ou nombre de lignes). Les valeurs manquantes (NaN, NA, NaT, None) sont tenues pour égales."""
    if set(actuel.columns) != set(attendu.columns) or len(actuel) != len(attendu):
        return ["*"]
    divergentes = []
    for col in attendu.columns:
        a, b = actuel[col].astype(object), attendu[col].astype(object)
        if a.where(a.notna(), None).tolist() != b.where(b.notna(), None).tolist():
            divergentes.append(col)
    return divergentes


def _non_convertibles(table, attendu, limite=20) -> tuple:
    """(nombre, exemples) de cellules dont le texte EAV est renseigné mais que le type de la colonne n'a pas pu convertir : l'analyse les
    voit VIDES (ex. « 1,5 » dans un champ décimal, « 12.5 » dans un entier). L'EAV garde le texte tel quel ; ce rapport le nomme pour
    qu'une donnée mal saisie ne fausse pas un calcul sans que personne le sache."""
    from .models import ValeurChamp
    if not len(attendu):
        return 0, []
    position = {i: p for p, i in enumerate(attendu[COLONNE_ID].tolist())}
    vides = {c: attendu[c].isna().tolist() for c in attendu.columns if c != COLONNE_ID}
    n, exemples = 0, []
    lignes = (ValeurChamp.objects.filter(enregistrement__table=table, champ__table=table)
              .exclude(valeur__isnull=True).exclude(valeur="").values_list("enregistrement_id", "champ__slug", "valeur"))
    for enregistrement_id, slug, valeur in lignes.iterator():
        p = position.get(enregistrement_id)
        if p is None or slug not in vides or not vides[slug][p] or not valeur.strip() or valeur.strip().lower() in ("null", "none"):
            continue
        n += 1
        if len(exemples) < limite:
            exemples.append({"ligne": enregistrement_id, "colonne": slug, "brut": valeur[:40]})
    return n, exemples


def _colonnes_mal_typees(table, df) -> list:
    """Colonnes du DataFrame qui ne sont pas dans la famille de type attendue pour leur type EAV (ex. un entier resté en texte parce qu'une
    valeur n'est pas convertible) : tout le calcul sur cette colonne en est affecté."""
    return [c.slug for c in table.champs.all() if c.slug in df.columns and _famille(df[c.slug].dtype) != _famille_attendue(c.type_champ)]


def verifier_table(table, reparer=True) -> dict:
    """Compare le CONTENU du fichier Parquet de la table à l'EAV (source de vérité absolue) et, si `reparer`, reconstruit le fichier
    depuis l'EAV quand ils diffèrent. Rattrape ce que l'empreinte de fraîcheur ne voit pas (écriture directe en base sans passer par
    l'application, fichier abîmé ou effacé). Retourne {"table", "nom", "etat", "lignes_eav", "lignes_fichier", "colonnes"} ;
    etat : "ok", "reparee", "divergente" (écart constaté, non réparé), "absent", "ignoree" (table NoSQL, pyarrow absent) ou "erreur"."""
    rapport = {"table": table.pk, "nom": table.nom, "etat": "ignoree", "lignes_eav": 0, "lignes_fichier": 0, "colonnes": [],
               "non_convertibles": 0, "exemples_non_convertibles": [], "colonnes_mal_typees": []}
    if not _concerne(table):
        return rapport
    try:
        enregistrements = list(table.enregistrements.order_by("cree_le", "id"))
        rapport["lignes_eav"] = len(enregistrements)
        if not _fichiers_de(table):
            if reparer:
                synchroniser_fichier(table)
            rapport["etat"] = "reparee" if reparer else "absent"
            return rapport
        attendu = _construire(table, enregistrements) if enregistrements else _vide(table)
        actuel = _frame_a_jour(table, ecrire=True, differer=False)   # ce que les analyses liraient : le fichier, mis à jour s'il est périmé
        rapport["lignes_fichier"] = len(actuel)
        rapport["colonnes"] = _colonnes_divergentes(actuel, attendu)
        rapport["non_convertibles"], rapport["exemples_non_convertibles"] = _non_convertibles(table, attendu)
        rapport["colonnes_mal_typees"] = _colonnes_mal_typees(table, attendu)
        if rapport["non_convertibles"] or rapport["colonnes_mal_typees"]:
            logger.warning(_gt("rg6.msg.084"), table.pk, rapport["non_convertibles"], rapport["colonnes_mal_typees"], rapport["exemples_non_convertibles"][:5])
        if not rapport["colonnes"]:
            rapport["etat"] = "ok"
            return rapport
        if reparer:
            synchroniser_fichier(table)
        rapport["etat"] = "reparee" if reparer else "divergente"
        logger.warning(_gt("rg6.msg.085"), table.pk, rapport["colonnes"],
                       _gt("rg6.msg.086") if reparer else _gt("rg6.msg.087"))
    except Exception:
        logger.exception(_gt("rg6.msg.088"), table.pk)
        rapport["etat"] = "erreur"
    return rapport


def verifier_toutes(entreprise=None, reparer=True) -> list:
    """verifier_table sur toutes les tables dynamiques EAV (celles d'une entreprise, ou de toutes) ; la liste des rapports."""
    from .models import TableDynamique
    tables = TableDynamique.objects.exclude(type_stockage=TableDynamique.STOCKAGE_NOSQL)
    if entreprise is not None:
        tables = tables.filter(entreprise=entreprise)
    return [verifier_table(t, reparer=reparer) for t in tables.order_by("id")]


# ── Mise à jour d'UNE table ────────────────────────────────────────────────────────────────────────────────────

def _supprimer_fichier(chemin) -> None:
    try:
        os.remove(chemin)
    except OSError:
        pass


def _ids_ordre(table):
    """L'INDEX de la table : ids de ses lignes en ordre chronologique. Source unique de l'ordre et des suppressions."""
    return list(table.enregistrements.order_by("cree_le", "id").values_list("id", flat=True))


def _fraicheur(chemin, empreinte):
    """(état, empreinte du socle) : état = "socle" (le socle seul est à jour), "delta" (socle + delta à jour) ou None (périmé ou absent)."""
    meta = _metadonnees(chemin)
    socle = meta.get(_CLE_EMPREINTE, b"").decode() if meta is not None else ""
    if socle and socle == empreinte:
        return "socle", socle
    if socle:
        md = _metadonnees(chemin + ".delta")
        if md and md.get(_CLE_BASE, b"").decode() == socle and md.get(_CLE_EMPREINTE, b"").decode() == empreinte:
            return "delta", socle
    return None, socle


def _fusionner(table, chemin, delta, ids_ordre, colonnes=None):
    """Socle + delta = table à jour, SANS rien écrire : les lignes du delta remplacent celles du socle (par `_id`), l'ordre et les
    suppressions viennent de l'index `ids_ordre`. `colonnes` (avec `_id`) borne la lecture du socle."""
    import pandas as pd
    if not ids_ordre:
        return _vide(table)
    socle = _lire_fichier(chemin, None if colonnes is None else list({*colonnes, COLONNE_ID}), table)
    if delta is None:
        delta = _lire_fichier(chemin + ".delta", None if colonnes is None else list({*colonnes, COLONNE_ID}), table)
    garde = socle[~socle[COLONNE_ID].isin(set(delta[COLONNE_ID]))]
    morceaux = [m for m in (garde, delta) if len(m)]
    df = pd.concat(morceaux, ignore_index=True) if len(morceaux) > 1 else (morceaux[0] if morceaux else socle.iloc[0:0])
    if len(morceaux) > 1 and list(garde.dtypes) != list(delta.dtypes):
        df = _normaliser(table, df)   # types différents d'un morceau à l'autre (colonne vide d'un côté) : recalage
    return df.set_index(COLONNE_ID).loc[ids_ordre].reset_index()


def _planifier_delta(table, chemin, empreinte_socle):
    """MODIFICATION À CHAUD : (table à jour, delta à écrire) en ne relisant dans l'EAV que les lignes ajoutées ou modifiées depuis
    l'écriture du SOCLE (une cellule éditée ou une ligne enregistrée remonte Enregistrement.maj_le, voir
    apps.toucher_maj_ligne_sur_valeur) ; les lignes supprimées disparaissent par l'index. None : colonnes changées, trop de lignes
    touchées (le delta approcherait le socle : compactage) ou erreur → reconstruction complète du socle."""
    try:
        from django.utils.dateparse import parse_datetime
        base, _s, schema_socle = empreinte_socle.partition("|")
        if schema_socle != _signature_schema(table):
            return None
        borne = base.partition(":")[2]
        depuis = parse_datetime(borne) if borne and borne != "vide" else None
        ids_ordre = _ids_ordre(table)
        if depuis is None or not ids_ordre:
            return None
        ids_socle = set(_lire_fichier(chemin, [COLONNE_ID])[COLONNE_ID])
        a_lire = {i for i in ids_ordre if i not in ids_socle}
        a_lire.update(table.enregistrements.filter(maj_le__gte=depuis).values_list("id", flat=True))
        if len(a_lire) > 5000 or len(a_lire) > max(SEUIL_DELTA_MIN, len(ids_ordre) // 4):
            return None
        delta = _construire(table, list(table.enregistrements.filter(pk__in=a_lire).order_by("cree_le", "id"))) if a_lire else _vide(table)
        return _fusionner(table, chemin, delta, ids_ordre), delta
    except Exception:
        logger.warning(_gt("rg6.msg.089"), chemin)
        return None


def _projeter(df, colonnes):
    return df if colonnes is None else df[[c for c in colonnes if c in df.columns]]


_LECTURE_SEULE = contextvars.ContextVar("parquet_lecture_seule", default=False)


@contextmanager
def lecture_seule():
    """Tout ce qui s'exécute dans ce bloc lit les fichiers Parquet SANS jamais en écrire (ni socle, ni delta, ni compactage), même si le code
    appelé demande `ecrire=True` : c'est ce qui permet à un worker Spark d'exécuter une analyse existante, inchangée, sans qu'il écrive à côté
    de l'initiateur (un seul nœud écrit, comme pour la base). Un fichier périmé est alors reconstruit EN MÉMOIRE, jamais sur le disque."""
    jeton = _LECTURE_SEULE.set(True)
    try:
        yield
    finally:
        _LECTURE_SEULE.reset(jeton)


def _ecriture_permise(ecrire) -> bool:
    return bool(ecrire) and not _LECTURE_SEULE.get()


def _frame_a_jour(table, ecrire=True, differer=True, colonnes=None):
    """DataFrame (avec `_id`, ordre chronologique) d'UNE table physique, à jour de l'EAV : lu tel quel si le fichier est frais (socle,
    ou socle + delta), sinon mis à jour À CHAUD (delta : le socle n'est pas réécrit) ou, à défaut, reconstruit (compactage) ; puis écrit si `ecrire`.

    `ecrire=False` : lecture seule, rien n'est écrit sur le disque (formules exécutées PENDANT l'écriture d'une ligne : voir
    donnees_ecriture). `differer` : dans une transaction ouverte, l'écriture du fichier est repoussée à sa validation (une seule fois
    par table et par transaction) — un fichier ne contient jamais de données qu'une annulation (rollback) ferait disparaître.
    `colonnes` : ne lit que ces colonnes dans un fichier frais (économie de mémoire) ; `_id` n'y figure que s'il est demandé.
    L'écriture est du MEILLEUR EFFORT : un disque plein ou un dossier non inscriptible ne fait jamais perdre la lecture déjà faite."""
    from django.db import connection
    chemin = chemin_fichier(table)
    empreinte = empreinte_table(table)   # AVANT la lecture des lignes : une écriture concurrente rend le fichier « périmé », jamais faussement « à jour »
    etat, empreinte_socle = _fraicheur(chemin, empreinte)
    if etat:
        try:
            if etat == "socle":
                return _lire_fichier(chemin, colonnes, table)
            return _projeter(_fusionner(table, chemin, None, _ids_ordre(table), colonnes), colonnes)
        except Exception:
            logger.warning(_gt("rg6.msg.090"), chemin)
            empreinte_socle = ""
    plan = _planifier_delta(table, chemin, empreinte_socle) if empreinte_socle else None
    delta = None
    if plan is not None:
        df, delta = plan
    else:
        enregistrements = list(table.enregistrements.order_by("cree_le", "id"))
        df = _construire(table, enregistrements) if enregistrements else _vide(table)
    if _ecriture_permise(ecrire):
        if differer and connection.in_atomic_block:
            def _a_la_validation():
                try:
                    _frame_a_jour(table, ecrire=True, differer=False)
                except Exception:
                    logger.exception(_gt("rg6.msg.091"), table.pk)
            sur_validation(("ecriture", table.pk), _a_la_validation)
        else:
            try:
                if delta is not None:
                    _ecrire_delta(table, delta, empreinte, empreinte_socle)
                else:
                    _ecrire(table, df, empreinte)
            except Exception:
                logger.exception(_gt("rg6.msg.092"), table.pk)
    return _projeter(df, colonnes)


# ── Lecture analytique : POINT D'ENTRÉE UNIQUE ─────────────────────────────────────────────────────────────────
# Toute fonctionnalité qui lit une table dynamique pour l'analyser (formules, graphiques, KPI, statistiques, séries temporelles,
# agent, compétences, ontologie, cartes, IA) passe par les fonctions ci-dessous : AUCUN module ne reconstruit l'EAV lui-même.
#
#   donnees_analytiques(table, groupe=True)  → DataFrame typé (jamais None)         table ou groupe entier = UNE grande table
#   donnees_dict(table)                      → {colonne: [valeurs...]} (jamais None) format attendu par l'executor
#   lignes_analytiques(table, limite=None)   → [{"_id": id, **valeurs}] (jamais None)  les `limite` dernières lignes
#   lignes_affichage(table, enregistrements) → ([valeurs], [couleurs]) des lignes d'un PDF / export, dans l'ordre donné
#   lignes_affichage_pdf(table, enregistrements) → même contrat, pour les PDF : Parquet → texte CSV (pandas) → table par regex (pdf_texte.py)
#   donnees_ecriture(table)                  → DataFrame d'UNE table, pour les formules exécutées PENDANT l'écriture d'une ligne
#
# Le seul code qui lit encore l'EAV est ici : la construction/mise à jour du fichier (_construire) et le repli `_lire_eav`.

def _lire_eav(table, groupe=True, avec_id=False):
    """REPLI UNIQUE sur l'EAV (ou valeurs_json d'une table NoSQL) : table NoSQL, pyarrow absent, erreur de lecture. Pour une table EAV, il
    passe par le même constructeur que le fichier Parquet (`_frame_eav`) : mêmes colonnes, même ordre, mêmes types."""
    import pandas as pd
    if getattr(table, "source_systeme", ""):   # table système (membres, wallet, paiement, messagerie) : lue dans le modèle du code, jamais dans l'EAV
        from . import tables_systeme
        return tables_systeme.dataframe(table, avec_id=avec_id)
    enregistrements = list(table.enregistrements_groupe if groupe else table.enregistrements.order_by("cree_le", "id"))
    if table.est_nosql:
        from .executor import _appliquer_schema
        from .metadata import schema_pour_executor
        from .moteur_donnees import dataframe_depuis
        df = _appliquer_schema(dataframe_depuis([e.to_dict() for e in enregistrements]), schema_pour_executor(table))
    else:
        df = _frame_eav(table, enregistrements)
    if avec_id and len(df) == len(enregistrements):
        df.insert(0, COLONNE_ID, pd.Series([e.id for e in enregistrements], dtype="int64"))
    return df


def _parties_analytiques(table, groupe=True):
    """Tables PHYSIQUES lues pour analyser `table` : elle seule, ou — si elle appartient à un GroupeTables et que `groupe` — les parties
    NON dénaturées du groupe, par partie_index puis id. Exactement la règle de TableDynamique.enregistrements_groupe (référence de
    l'EAV) : un groupe entièrement dénaturé n'a aucune partie lue, comme dans l'EAV."""
    if not groupe or not table.groupe_id:
        return [table]
    return list(table.groupe.tables.filter(denaturee=False).order_by("partie_index", "id"))


def structure_groupe_divergente(table) -> list:
    """Écarts de structure entre `table` et les autres parties de son groupe : un groupe exige la même structure à son rattachement,
    mais rien ne l'impose ensuite (colonne ajoutée ou retypée sur une seule partie). Liste de messages, vide si tout concorde (ou si la
    table n'est pas groupée) — à appeler avant une analyse pour avertir, ou depuis une vue pour afficher le problème."""
    ecarts = []
    reference = table.signature_structure
    for partie in _parties_analytiques(table):
        if partie.pk == table.pk:
            continue
        autre = partie.signature_structure
        if autre != reference:
            manquantes = sorted(s for s, _t in reference - autre)
            en_trop = sorted(s for s, _t in autre - reference)
            ecarts.append(_gtf("rg6.msg.093", v1=partie.nom, v2=partie.pk, v3=manquantes, v4=en_trop))
    return ecarts


_DIVERGENCES_SIGNALEES: set = set()


def _assembler(table, frames, cibles):
    """Une seule table à partir des DataFrames des parties (dans l'ordre des parties), colonnes `cibles` : une colonne absente d'une
    partie y est vide, une colonne qui n'existe que dans une autre partie est écartée. Les parties vides sont ignorées (elles ne
    portent que des colonnes, et dégraderaient le type des autres). Si les types diffèrent d'une partie à l'autre (un entier ici,
    du texte là), le typage est recalé sur le schéma de `table`. Toute divergence de colonnes est signalée une fois dans les journaux."""
    import pandas as pd
    manquantes = {c for f in frames for c in cibles if c not in f.columns}
    if manquantes and (table.pk, frozenset(manquantes)) not in _DIVERGENCES_SIGNALEES:
        _DIVERGENCES_SIGNALEES.add((table.pk, frozenset(manquantes)))
        logger.warning(_gt("rg6.msg.094"), table.pk, sorted(manquantes))
    pleines = [f.reindex(columns=cibles) for f in frames if len(f)] or [frames[0].reindex(columns=cibles)]
    df = pd.concat(pleines, ignore_index=True) if len(pleines) > 1 else pleines[0]
    homogene = not manquantes and all(list(f.dtypes) == list(pleines[0].dtypes) for f in pleines)
    return df if homogene else _normaliser(table, df)


def lire_dataframe(table, groupe=True, avec_id=False, colonnes=None, ecrire=True):
    """DataFrame analytique de la table — ou de tout son groupe (comme TableDynamique.enregistrements_groupe : un groupe de tables est
    lu comme UNE grande table, assemblé ici depuis les fichiers de ses parties) — lu dans le(s) fichier(s) Parquet, mis à jour au
    besoin : une ligne par enregistrement, une colonne par champ dynamique, typé selon le schéma EAV. `avec_id` ajoute la colonne
    technique `_id` en tête. `colonnes` (slugs) limite la lecture aux colonnes utiles : c'est ce qui borne la mémoire d'un grand groupe.
    None : table NoSQL (ou groupe contenant une partie NoSQL), pyarrow absent ou erreur — les modules d'analyse appellent
    donnees_analytiques (jamais None) et c'est elle qui retombe sur l'EAV."""
    if not _concerne(table):
        return None
    try:
        parties = _parties_analytiques(table, groupe)
        if not parties or any(t.est_nosql for t in parties):
            return None
        slugs = [c.slug for c in table.champs.all()]
        if colonnes is not None:
            slugs = [c for c in slugs if c in colonnes]
        cibles = ([COLONNE_ID] if avec_id else []) + slugs
        frames = [_frame_a_jour(t, ecrire=ecrire, colonnes=cibles) for t in parties]
        if len(frames) == 1 and set(cibles) <= set(frames[0].columns):
            return frames[0][cibles]
        return _assembler(table, frames, cibles)
    except Exception:
        logger.exception(_gt("rg6.msg.095"), table.pk)
        return None


def rafraichir_fichiers(table, groupe=True) -> None:
    """Met à jour, CÔTÉ INITIATEUR, le fichier de chaque table physique (table ou groupe) sans en charger les données : seule la colonne `_id`
    est lue. Les workers Spark lisent ensuite des fichiers frais, en lecture seule — un seul nœud écrit, comme pour la base."""
    if not _concerne(table):
        return
    for t in _parties_analytiques(table, groupe):
        if not t.est_nosql:
            _frame_a_jour(t, colonnes=[COLONNE_ID])


def ids_liste_noire(table, groupe=True) -> set:
    """Identifiants des lignes de la LISTE NOIRE de la table (ou de tout son groupe) : écartées de toutes les analyses, jamais supprimées."""
    from .models import Enregistrement
    ids = [t.pk for t in _parties_analytiques(table, groupe)]
    return set(Enregistrement.objects.filter(table_id__in=ids, liste_noire=True).values_list("id", flat=True))


def _ecarter_liste_noire(df, noires):
    """DataFrame (avec `_id`) privé des lignes de la liste noire."""
    if not noires or df is None or COLONNE_ID not in df.columns:
        return df
    return df[~df[COLONNE_ID].isin(noires)].reset_index(drop=True)


def lire_dict(table):
    """Comme lire_dataframe, au format {colonne: [valeurs...]} attendu par l'executor (None pour les valeurs manquantes).
    None si le fichier n'est pas applicable — les modules d'analyse appellent donnees_dict (jamais None).
    Les lignes de la liste noire sont écartées."""
    noires = ids_liste_noire(table)
    df = lire_dataframe(table, avec_id=bool(noires))
    if df is None:
        return None
    if noires:
        df = _ecarter_liste_noire(df, noires).drop(columns=[COLONNE_ID])
    return _df_vers_dict(df)


def _df_vers_dict(df):
    """{colonne: [valeurs...]} d'un DataFrame, None pour les valeurs manquantes : UNE seule conversion pour le fichier et pour le repli."""
    objets = df.astype(object)
    objets = objets.where(objets.notna(), None)
    return {c: objets[c].tolist() for c in objets.columns}


def donnees_analytiques(table, groupe=True, avec_id=False, colonnes=None, ecrire=True, identifiants=False):
    """POINT D'ENTRÉE : DataFrame de la table (ou du groupe) lu dans le fichier Parquet ; jamais None (repli EAV géré ici).
    `colonnes` (slugs) : ne charge que celles-là — à préférer pour un grand groupe quand l'analyse n'en utilise que quelques-unes.
    `ecrire=False` : lecture seule, aucun fichier n'est écrit ni compacté (un worker Spark calcule, il n'écrit jamais).
    `identifiants=True` : LECTURE d'une table d'agents / clients / produits — ajoute les colonnes `matricule` et `code_acces_client`
    (voir identifiants_analyse.py), à la suite des champs ; sans effet sur une autre table. Les lectures qui servent à ÉCRIRE ne le
    demandent jamais."""
    from . import identifiants_analyse as _ident
    ajouter = [s for s in _ident.slugs_pour(table) if colonnes is None or s in colonnes] if identifiants else []
    reelles = colonnes if (colonnes is None or not identifiants) else [c for c in colonnes if c not in _ident.SLUGS]
    noires = ids_liste_noire(table, groupe)   # lignes écartées de TOUTES les analyses (liste noire) : l'`_id` sert à les retirer
    voir_id = avec_id or bool(ajouter) or bool(noires)
    df = lire_dataframe(table, groupe=groupe, avec_id=voir_id, colonnes=reelles, ecrire=ecrire)
    if df is None:
        df = _lire_eav(table, groupe=groupe, avec_id=voir_id)
        if reelles is not None:
            df = df[[c for c in df.columns if c in reelles or c == COLONNE_ID]]
    if noires:
        df = _ecarter_liste_noire(df, noires)
        if not (avec_id or ajouter) and COLONNE_ID in df.columns:
            df = df.drop(columns=[COLONNE_ID])
    if ajouter:
        df = _ident.ajouter(df, table, groupe)
        if colonnes is not None:
            df = df[[c for c in df.columns if c in colonnes or c == COLONNE_ID]]
        if not avec_id and COLONNE_ID in df.columns:
            df = df.drop(columns=[COLONNE_ID])
    return df


def valeurs_brutes(table) -> dict:
    """{id de ligne: {slug: texte EAV exact}} lu dans le fichier (image fidèle de la table) ; {} si le fichier n'est pas applicable."""
    if not _concerne(table):
        return {}
    try:
        df = _frame_a_jour(table, colonnes=[COLONNE_ID, COLONNE_BRUT])
        return {i: (json.loads(b) if isinstance(b, str) else {}) for i, b in zip(df[COLONNE_ID].tolist(), df[COLONNE_BRUT].tolist())}
    except Exception:
        logger.exception(_gt("rg6.msg.096"), table.pk)
        return {}


def donnees_dict(table):
    """POINT D'ENTRÉE : {colonne: [valeurs...]} lu dans le fichier Parquet ; jamais None (repli EAV géré ici)."""
    d = lire_dict(table)
    if d is not None:
        return d
    if not table.est_nosql:
        noires = ids_liste_noire(table)
        if noires:
            return _df_vers_dict(_ecarter_liste_noire(_lire_eav(table, avec_id=True), noires).drop(columns=[COLONNE_ID]))
        return _df_vers_dict(_lire_eav(table))   # même constructeur et même conversion que le fichier : mêmes types sur les deux chemins
    from .metadata import table_vers_dict
    return table_vers_dict(table)


def en_lignes(df):
    """DataFrame → liste de dicts de valeurs Python ordinaires : None pour les valeurs manquantes, dates en texte ISO (jour seul quand
    il n'y a pas d'heure) — la forme que les modules qui travaillent ligne par ligne recevaient de Enregistrement.to_dict()."""
    objets = df.astype(object)
    lignes = objets.where(objets.notna(), None).to_dict("records")
    for ligne in lignes:
        for cle, v in ligne.items():
            if hasattr(v, "isoformat"):
                try:
                    ligne[cle] = v.date().isoformat() if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v.isoformat()
                except AttributeError:   # un `date` sans heure
                    ligne[cle] = v.isoformat()
    return lignes


def lignes_analytiques(table, limite=None):
    """POINT D'ENTRÉE : [{"_id": id de la ligne, **valeurs par slug}] en ordre chronologique — les `limite` DERNIÈRES lignes (toutes si
    None) de la table ou du groupe, lues dans le fichier Parquet ; jamais None."""
    df = donnees_analytiques(table, avec_id=True)
    if limite is not None:
        df = df.tail(limite)
    return en_lignes(df)


def texte_cellule(valeur) -> str:
    """Texte d'une cellule du fichier tel que ValeurChamp.valeur l'aurait stocké ('' si la cellule est vide) : pour les analyses qui
    comparent, comptent ou regroupent des valeurs (ontologie, fusion de l'état de l'entreprise). Dates en texte ISO (jour seul sans heure),
    entiers sans « .0 », valeurs imbriquées en JSON."""
    import pandas as pd
    if isinstance(valeur, (list, dict, tuple, set)):
        return json.dumps(valeur, ensure_ascii=False, default=str)
    if valeur is None or pd.isna(valeur):
        return ""
    if isinstance(valeur, bool):
        return "1" if valeur else "0"
    if isinstance(valeur, float) and valeur.is_integer():
        return str(int(valeur))
    if hasattr(valeur, "total_seconds"):
        return str(valeur.total_seconds())
    if hasattr(valeur, "isoformat"):
        try:
            return valeur.date().isoformat() if (valeur.hour, valeur.minute, valeur.second, valeur.microsecond) == (0, 0, 0, 0) else valeur.isoformat()
        except AttributeError:   # un `date` sans heure
            return valeur.isoformat()
    return str(valeur)


def comptes_colonne(serie) -> tuple:
    """(cellules renseignées, valeurs distinctes) d'une colonne du fichier — vide = absente ou texte vide."""
    renseignees = serie.dropna()
    if renseignees.dtype == object:
        renseignees = renseignees.map(texte_cellule)
        renseignees = renseignees[renseignees != ""]
    return int(len(renseignees)), int(renseignees.nunique())


def textes_colonne(serie, limite=None) -> list:
    """Textes (voir texte_cellule) des cellules renseignées d'une colonne du fichier, dans l'ordre des lignes — les `limite` premiers."""
    sortie = []
    for valeur in serie.astype(object).tolist():
        texte = texte_cellule(valeur)
        if texte != "":
            sortie.append(texte)
            if limite is not None and len(sortie) >= limite:
                break
    return sortie


def _heures_minutes_secondes(duree) -> str:
    total = int(duree.total_seconds())
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _lignes_depuis_choisies(choisies, enregistrements):
    """DataFrame des lignes choisies (dans l'ordre de `enregistrements`, sans `_id`) → [{slug: valeur}] : durées en h:mm:ss, dates en texte ISO,
    matricule et code d'accès client ajoutés comme Enregistrement.to_dict(). Un seul code pour la grille, les exports et les PDF."""
    import pandas as pd
    for col in choisies.columns:
        if str(choisies[col].dtype).startswith("timedelta"):
            choisies[col] = choisies[col].map(lambda d: None if pd.isna(d) else _heures_minutes_secondes(d)).astype(object)
    lignes = en_lignes(choisies)
    for ligne, e in zip(lignes, enregistrements):
        if e.matricule:
            ligne["matricule"] = e.matricule
        if e.code_acces_client:
            ligne["code_acces_client"] = e.code_acces_client
    return lignes


def lignes_affichage(table, enregistrements):
    """POINT D'ENTRÉE des PDF et exports qui montrent des lignes : ([{slug: valeur}], [mise en forme]) pour les `enregistrements` donnés
    (objets Enregistrement déjà choisis — filtre, tri, périmètre de l'agent ou du client —, dans l'ordre voulu). Les valeurs viennent du
    fichier Parquet (même contenu que Enregistrement.to_dict : matricule et code d'accès client compris), au lieu d'une requête EAV par
    ligne ; les couleurs de ligne et de cellules viennent des objets eux-mêmes. Dates en texte ISO, durées en h:mm:ss.
    Table DÉNATURÉE (fusions de cellules, cellules retypées ou « formule » : voir TableDynamique.recalculer_denaturee), table NoSQL,
    pyarrow absent ou fichier inutilisable : to_dict() de chaque ligne, comme avant — la structure de l'EAV n'est alors plus celle du
    fichier Parquet, et le PDF doit reproduire l'EAV tel quel."""
    mises = [e.couleurs_mise_en_forme() for e in enregistrements]
    if not enregistrements:
        return [], mises
    try:
        # Le groupe n'est assemblé que si les lignes demandées viennent de plusieurs parties (la grille d'un groupe) : un PDF d'une seule
        # table ne lit pas les cinquante autres fichiers.
        groupe = any(e.table_id != table.id for e in enregistrements)
        if _concerne(table) and not table.denaturee and not any(t.est_nosql for t in _parties_analytiques(table, groupe) or [table]):
            import pandas as pd
            df = donnees_analytiques(table, groupe=groupe, avec_id=True)
            positions = df.set_index(COLONNE_ID).index.get_indexer([e.id for e in enregistrements])
            if not (positions < 0).any():
                choisies = df.iloc[positions].drop(columns=[COLONNE_ID]).copy()
                return _lignes_depuis_choisies(choisies, enregistrements), mises
    except Exception:
        logger.exception(_gt("rg6.msg.097"), getattr(table, "pk", "?"))
    return [e.to_dict() for e in enregistrements], mises


def lignes_affichage_pdf(table, enregistrements):
    """POINT D'ENTRÉE des PDF de table (liste, résumé général, rapport chaîné de l'agent) : même contrat que lignes_affichage —
    ([{slug: valeur}], [mise en forme]) —, mais les valeurs ne repassent jamais par la table : le fichier Parquet est lu UNE fois, converti
    en TEXTE CSV par pandas, et la table est reconstruite sur ce texte par regex (engine/pdf_texte.py). Matricule et code d'accès client
    sont ajoutés comme dans lignes_affichage. Table dénaturée, NoSQL, pyarrow absent, fichier inutilisable ou texte qui ne se redécoupe pas
    exactement : lignes_affichage (qui retombe elle-même sur to_dict()), comme avant."""
    mises = [e.couleurs_mise_en_forme() for e in enregistrements]
    if not enregistrements:
        return [], mises
    try:
        groupe = any(e.table_id != table.id for e in enregistrements)
        if _concerne(table) and not table.denaturee and not any(t.est_nosql for t in _parties_analytiques(table, groupe) or [table]):
            from . import pdf_texte
            df = donnees_analytiques(table, groupe=groupe, avec_id=True)
            positions = df.set_index(COLONNE_ID).index.get_indexer([e.id for e in enregistrements])
            if not (positions < 0).any():
                lignes = pdf_texte.lignes_depuis_dataframe(df.iloc[positions].drop(columns=[COLONNE_ID]))
                if len(lignes) == len(enregistrements):
                    for ligne, e in zip(lignes, enregistrements):
                        if e.matricule:
                            ligne["matricule"] = e.matricule
                        if e.code_acces_client:
                            ligne["code_acces_client"] = e.code_acces_client
                    return lignes, mises
    except Exception:
        logger.exception(_gt("rg6.msg.097"), getattr(table, "pk", "?"))
    return lignes_affichage(table, enregistrements)


def donnees_ecriture(table):
    """POINT D'ENTRÉE pour les formules exécutées PENDANT l'écriture d'une ligne (champs calculés, voir calc_champ.py) : DataFrame
    d'UNE table (pas de groupe), lu dans le fichier Parquet puis complété, sans rien écrire, des lignes ajoutées ou modifiées dans la
    transaction en cours (seules celles-là sont relues dans l'EAV). Jamais None."""
    if _concerne(table):
        try:
            df = _frame_a_jour(table, ecrire=False)
            if df is not None:
                return df[[c.slug for c in table.champs.all() if c.slug in df.columns]]
        except Exception:
            logger.exception(_gt("rg6.msg.095"), table.pk)
    return _lire_eav(table, groupe=False)


def valeurs_lignes(table, enregistrements, seuil=50) -> list:
    """[Enregistrement.to_dict() de chaque ligne], dans l'ordre donné. Au-delà de `seuil` lignes, les valeurs sont lues EN UNE FOIS dans le
    fichier Parquet (lignes_affichage : même contenu que to_dict) au lieu d'une requête EAV par ligne — c'est ce qui rend lisibles les
    tables de dizaines de milliers de lignes. Fichier inutilisable ou erreur : to_dict() de chaque ligne, comme avant."""
    enregistrements = list(enregistrements)
    if len(enregistrements) > seuil:
        try:
            valeurs, _mises = lignes_affichage(table, enregistrements)
            if len(valeurs) == len(enregistrements):
                return valeurs
        except Exception:
            logger.exception("valeurs_lignes : lecture groupée impossible pour la table %s, repli ligne par ligne", getattr(table, "pk", "?"))
    return [e.to_dict() for e in enregistrements]


def _frame_cible(table, ids):
    """DataFrame (`_id` + champs) des seules lignes `ids` d'UNE table physique, lu dans son fichier Parquet : socle, ou socle + delta (les\n    lignes modifiées depuis le socle). Un fichier périmé ou absent est d'abord mis à jour (maintenance du fichier, delta ou compactage —\n    jamais une relecture de la page dans l'EAV) puis relu. None si le fichier reste inutilisable : l'appelant retombe alors sur la lecture\n    complète. Seuls les groupes de lignes du fichier qui contiennent ces ids sont lus."""
    import pandas as pd
    chemin = chemin_fichier(table)
    etat, _socle = _fraicheur(chemin, empreinte_table(table))
    if etat is None:
        _frame_a_jour(table, colonnes=[COLONNE_ID])
        etat, _socle = _fraicheur(chemin, empreinte_table(table))
        if etat is None:
            return None
    df = _lire_fichier(chemin, None, table, ids=ids)
    if etat == "delta":
        delta = _lire_fichier(chemin + ".delta", None, table, ids=ids)
        garde = df[~df[COLONNE_ID].isin(set(delta[COLONNE_ID]))]
        morceaux = [m for m in (garde, delta) if len(m)]
        df = pd.concat(morceaux, ignore_index=True) if len(morceaux) > 1 else (morceaux[0] if morceaux else df.iloc[0:0])
        if len(morceaux) > 1 and list(garde.dtypes) != list(delta.dtypes):
            df = _normaliser(table, df)
    return df


def valeurs_page(table, enregistrements):
    """POINT D'ENTRÉE de la GRILLE : [{slug: valeur}] des `enregistrements` (une page, dans l'ordre donné), lues dans le fichier Parquet
    — jamais dans l'EAV ligne par ligne. Table simple OU groupe (« table générale » : les lignes d'une page peuvent venir de plusieurs
    parties) : pour chaque table physique concernée, seules ses lignes de la page sont lues (filtre sur `_id` poussé dans le fichier,
    socle + delta), les autres parties du groupe ne sont pas ouvertes ; une page de 200 lignes d'une table de 20 millions de lignes ne
    charge donc pas la table. Table dénaturée, NoSQL, pyarrow absent ou fichier inutilisable : lecture complète du fichier
    (lignes_affichage) puis, en dernier recours seulement, to_dict() de chaque ligne. Même contenu que to_dict (matricule et code d'accès
    client compris)."""
    enregistrements = list(enregistrements)
    if not enregistrements:
        return []
    try:
        parties = {}
        for e in enregistrements:
            parties.setdefault(e.table_id, table if e.table_id == table.id else e.table)
        if all(_concerne(p) and not p.denaturee for p in parties.values()):
            ids_par_partie = {}
            for e in enregistrements:
                ids_par_partie.setdefault(e.table_id, []).append(e.id)
            slugs = [c.slug for c in table.champs.all()]
            cibles = [COLONNE_ID] + slugs
            frames = []
            for pid, partie in parties.items():
                frame = _frame_cible(partie, ids_par_partie[pid])
                if frame is None:
                    raise ValueError("fichier Parquet inutilisable")
                frames.append(frame)
            df = _assembler(table, frames, cibles) if len(frames) > 1 else frames[0].reindex(columns=cibles)
            ids = [e.id for e in enregistrements]
            positions = df.set_index(COLONNE_ID).index.get_indexer(ids)
            if not (positions < 0).any():
                return _lignes_depuis_choisies(df.iloc[positions][slugs].copy(), enregistrements)
    except Exception:
        logger.exception("valeurs_page : lecture ciblée impossible pour la table %s, lecture complète du fichier", getattr(table, "pk", "?"))
    return valeurs_lignes(table, enregistrements, seuil=0)
