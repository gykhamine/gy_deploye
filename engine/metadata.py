"""
Le modele IA ne recoit jamais toute la base.
Il recoit uniquement : description des tables, types des champs, relations,
et un echantillon limite de donnees.
"""
from engine.i18n import td
from engine.i18n import t, tf
from engine.i18n import tl as _i18n_tl
from .models import TableDynamique


ECHANTILLON_MAX_ABSOLU = 50  # plafond de securite, quoi que le client configure sur la table


# ── Mapping type EAV → dtype pandas ──────────────────────────────────────────
# SOURCE UNIQUE : utilise a la fois pour typer le DataFrame dans l'executor
# ET pour documenter le schema exact dans le prompt IA.
#
# Cle   = valeur de ChampDynamique.type_champ
# Valeur = (dtype_pandas, description_humaine)
#   dtype_pandas : chaine passee a pd.Series.astype() ou a pd.to_datetime(),
#                  ou None pour les types non-numeriques sans conversion pandas.

EAV_DTYPE_MAP = {
    # ── Numeriques ────────────────────────────────────────────────────────────
    "nombre":           ("Int64",   "entier (nullable), ex: 42"),
    "decimal":          ("Float64", "flottant (nullable), ex: 3.14"),
    "monnaie":          ("Float64", _i18n_tl("engine/metadata.py:g:25:37")),
    "pourcentage":      ("Float64", _i18n_tl("engine/metadata.py:g:26:37")),
    "constante":        ("Float64", _i18n_tl("engine/metadata.py:g:27:37")),
    "note":             ("Int64",   "entier 1-5 (nullable)"),
    "barre_progression":("Int64",   "entier 0-100 (nullable)"),
    # ── Booleens ─────────────────────────────────────────────────────────────
    "booleen":          ("boolean", "booleen pandas (True/False/pd.NA)"),
    "switch":           ("boolean", "booleen pandas (True/False/pd.NA)"),
    # ── Dates / heures ───────────────────────────────────────────────────────
    "date":             ("datetime64[ns]", _i18n_tl("engine/metadata.py:g:34:44")),
    "heure":            (None,      _i18n_tl("engine/metadata.py:g:35:37")),
    "datetime":         ("datetime64[ns]", _i18n_tl("engine/metadata.py:g:36:44")),
    "horodatage":       ("datetime64[ns]", _i18n_tl("engine/metadata.py:g:37:44")),
    "colonne":          (None,      td("engine/metadata.py:g:40:37")),
    "duree":            (None,      _i18n_tl("engine/metadata.py:g:39:37")),
    # ── Textes et autres ─────────────────────────────────────────────────────
    "texte":            (None,      _i18n_tl("engine/metadata.py:g:41:37")),
    "texte_long":       (None,      _i18n_tl("engine/metadata.py:g:42:37")),
    "email":            (None,      _i18n_tl("engine/metadata.py:g:43:37")),
    "url":              (None,      td("engine/metadata.py:g:46:37")),
    "telephone":        (None,      _i18n_tl("engine/metadata.py:g:45:37")),
    "couleur":          (None,      td("engine/metadata.py:g:48:37#2")),
    "choix_unique":     (None,      _i18n_tl("engine/metadata.py:g:47:37")),
    "choix_multiple":   (None,      _i18n_tl("engine/metadata.py:g:48:37")),
    "fichier":          (None,      _i18n_tl("engine/metadata.py:g:49:37")),
    "image":            (None,      _i18n_tl("engine/metadata.py:g:50:37")),
    "son":              (None,      _i18n_tl("engine/metadata.py:g:51:37")),
    "video":            (None,      _i18n_tl("engine/metadata.py:g:52:37")),
}


def schema_colonnes(table: TableDynamique) -> list[dict]:
    """Retourne la liste des colonnes avec leur slug, type EAV, dtype pandas
    et une description humaine. Utilise a la fois dans le contexte IA et dans
    l'executor pour typer le DataFrame."""
    champs = list(table.champs.all())
    resultat = []
    for c in champs:
        dtype_info = EAV_DTYPE_MAP.get(c.type_champ, (None, td("engine/metadata.py:g:65:61")))
        dtype_pandas, description = dtype_info
        entree = {
            "slug":        c.slug,
            "type_eav":    c.type_champ,
            "dtype_pandas": dtype_pandas or "object",
            "description": description,
            "obligatoire": c.obligatoire,
        }
        if c.relation_vers:
            entree["relation_vers"] = c.relation_vers.slug
        if c.type_champ in ("choix_unique", "choix_multiple") and c.liste_options:
            entree["options"] = c.liste_options
        resultat.append(entree)
    return resultat


def decrire_table(table: TableDynamique) -> dict:
    return {
        "table": table.slug,
        "description": table.description,
        "champs": schema_colonnes(table),
        "nombre_lignes_total": table.enregistrements_groupe.count(),
    }


def _echantillon_vers_csv(table: TableDynamique) -> str:
    """Serialise l'echantillon de la table en CSV. Premiere ligne = en-tete
    avec les slugs exacts. Lignes suivantes = exemples de valeurs reelles
    (Decimal et listes converties en str lisible)."""
    import csv
    import io
    from decimal import Decimal

    colonnes = [c.slug for c in table.champs.all()]
    tampon = io.StringIO()
    ecrivain = csv.DictWriter(tampon, fieldnames=colonnes)
    ecrivain.writeheader()
    taille = min(table.taille_echantillon or 5, ECHANTILLON_MAX_ABSOLU)
    for enregistrement in table.enregistrements_groupe.order_by("-cree_le")[:taille]:
        ligne = enregistrement.to_dict()
        row = {}
        for col in colonnes:
            v = ligne.get(col)
            if isinstance(v, Decimal):
                v = float(v)
            elif isinstance(v, list):
                v = ",".join(str(x) for x in v)
            row[col] = v if v is not None else ""
        ecrivain.writerow(row)
    return tampon.getvalue()


def contexte_pour_agent(table: TableDynamique) -> str:
    """Contexte texte injecte dans le prompt IA.

    Contient :
    1. Le schema complet : slug, type EAV, dtype pandas exact, description.
    2. Un apercu CSV des vraies donnees (en-tete = slugs, lignes = exemples).
    3. Si la table a des champs relation, le schema + aperçu des tables liées
       sont aussi injectés, et l'IA est informée que le DataFrame reçu
       contiendra les colonnes jointes (préfixées par <table_liée>__<slug>).
    """
    import json

    schema = schema_colonnes(table)
    extrait_csv = _echantillon_vers_csv(table)
    nombre_lignes = table.enregistrements_groupe.count()

    lignes_schema = []
    for c in schema:
        ligne = f"  - {c['slug']} | type_eav={c['type_eav']} | dtype_pandas={c['dtype_pandas']} | {c['description']}"
        if c.get("options"):
            ligne += f" | options={c['options']}"
        if c.get("relation_vers"):
            ligne += tf("engine/metadata.py:g:139:22", v1=c['relation_vers'])
        lignes_schema.append(ligne)

    bloc_schema = (
        tf("engine/metadata.py:g:143:9", v1=table.slug, v2=table.description or '(aucune)', v3=nombre_lignes)
        + "\n".join(lignes_schema)
    )

    bloc_csv = (
        t("engine/metadata.py:g:151:9")
        + extrait_csv
    )

    contexte = bloc_schema + "\n\n" + bloc_csv

    # ── Tables liées (JOIN automatique) ──────────────────────────────────────
    champs_relation = [c for c in table.champs.select_related("relation_vers").all()
                       if c.type_champ == "relation" and c.relation_vers]
    if champs_relation:
        blocs_joins = []
        tables_vues = set()
        for champ in champs_relation:
            tl = champ.relation_vers
            if tl.slug in tables_vues:
                continue
            tables_vues.add(tl.slug)
            schema_liee = schema_colonnes(tl)
            csv_liee = _echantillon_vers_csv(tl)
            lignes_sl = [
                f"  - {tl.slug}__{c['slug']} | type_eav={c['type_eav']} | dtype_pandas={c['dtype_pandas']}"
                for c in schema_liee
            ]
            blocs_joins.append(
                tf("engine/metadata.py:g:179:17", v1=tl.slug, v2=champ.slug, v3=tl.slug) + "\n".join(lignes_sl) + tf("engine/metadata.py:g:180:88", v1=tl.slug, v2=csv_liee)
            )
        if blocs_joins:
            contexte += (
                t("engine/metadata.py:g:185:17")
                + "\n\n".join(blocs_joins)
            )

    return contexte


def empreinte_donnees(table: TableDynamique) -> str:
    """Empreinte legere de l'etat des donnees (nombre de lignes + derniere
    modification). Sert uniquement a savoir si un resultat en cache est encore
    valable."""
    import hashlib

    lignes = table.enregistrements_groupe
    dernier = lignes.order_by("-maj_le").values_list("maj_le", flat=True).first()
    nombre = lignes.count()
    empreinte = f"{nombre}:{dernier.isoformat() if dernier else 'vide'}"
    return hashlib.sha256(empreinte.encode("utf-8")).hexdigest()


def table_vers_dict(table: TableDynamique, enregistrements=None) -> dict:
    """Transforme les enregistrements EAV en dict {colonne: [valeurs...]} pour
    que l'executor puisse construire le DataFrame. Les Decimal sont convertis
    en float ici pour eviter des mixed-types dans pandas.
    `enregistrements` (optionnel) : lignes précises à convertir (parquet_store.py construit ainsi le fichier Parquet d'UNE
    table) ; par défaut, toutes les lignes de la table ou de son groupe."""
    from decimal import Decimal

    from .models import ValeurChamp

    def _coerce(v):
        if isinstance(v, Decimal):
            return float(v)
        return v

    enregistrements = list(table.enregistrements_groupe if enregistrements is None else enregistrements)
    colonnes = [c.slug for c in table.champs.all()]
    donnees = {colonne: [None] * len(enregistrements) for colonne in colonnes}
    if not enregistrements:
        return donnees

    # UNE SEULE requête pour toutes les ValeurChamp de tous les
    # enregistrements, au lieu d'une requête par ligne (e.to_dict() faisait
    # un SELECT séparé pour chaque enregistrement — très lent sur une table
    # de plusieurs milliers de lignes, avant chaque analyse IA/BI).
    index_ligne = {e.id: i for i, e in enumerate(enregistrements)}
    valeurs = (
        ValeurChamp.objects
        .filter(enregistrement_id__in=index_ligne.keys())
        .select_related("champ")
    )
    for v in valeurs:
        i = index_ligne.get(v.enregistrement_id)
        if i is None:
            continue
        slug = v.champ.slug
        if slug not in donnees:
            continue
        donnees[slug][i] = _coerce(v.valeur_typee())
    return donnees


def schema_pour_executor(table: TableDynamique) -> dict[str, str]:
    """Retourne {slug: type_eav} pour que l'executor sache comment caster
    chaque colonne du DataFrame. SOURCE UNIQUE du typage runtime."""
    return {c.slug: c.type_champ for c in table.champs.all()}


def signature_intention(table: TableDynamique, texte_demande: str) -> str:
    """Empreinte stable = intention normalisee + structure de la table.
    Deux demandes similaires sur une meme structure -> meme script reutilise."""
    import hashlib
    import json

    structure = [(c.slug, c.type_champ) for c in table.champs.order_by("slug")]
    intention_normalisee = " ".join(texte_demande.lower().split())
    empreinte = json.dumps(
        [intention_normalisee, table.slug, structure, table.prompt_rapport], ensure_ascii=False
    )
    return hashlib.sha256(empreinte.encode("utf-8")).hexdigest()
