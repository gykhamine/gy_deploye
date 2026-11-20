"""Grille de données façon tableur : recherche, filtres, tri et export — côté SERVEUR.

Avant : la grille (index_1.js) ne cherchait que dans la page affichée (200 lignes) et ne savait pas trier ;
le paramètre `q` de l'API débitait le service payant « recherche » sans rien filtrer. Ici, tout se fait sur
l'ensemble des lignes visibles par l'utilisateur (isolation agent déjà appliquée au queryset reçu).

Paramètres GET compris par `appliquer()` :
  q=<texte>          recherche dans toutes les colonnes (insensible à la casse) ; « #12 » ou « 12 » trouve aussi la ligne n°12
  tri=<slug>|_id|_cree_le   colonne de tri ; sens=asc|desc (défaut : plus récentes d'abord)
  f_<slug>=<expr>    filtre sur une colonne : « texte » (contient), « =valeur », « !=valeur », « >10 », « <=2026-01-31 »,
                     « =(vide) » / « !=(vide) ». Comparaisons numériques pour les colonnes numériques, dans l'ordre
                     du texte (donc chronologique pour les dates ISO) pour les autres.
Le modèle est EAV (ValeurChamp.valeur est un texte) : tri et filtres passent par une sous-requête par colonne,
convertie en nombre (Cast) pour les types numériques — sinon « 9 » passerait après « 10 ».
"""
from engine.i18n import t
import contextlib
import csv
import io
import json
import logging
import os
import re
import time

from django.db.models import F, FloatField, OuterRef, Q, Subquery
from django.db.models.functions import Cast
from django.db.models.fields.json import KeyTextTransform

from .models import ValeurChamp

TYPES_NUMERIQUES = frozenset({"nombre", "decimal", "monnaie", "pourcentage", "note", "barre_progression", "constante"})
_OPERATEUR = re.compile(r"^\s*(>=|<=|!=|>|<|=)\s*(.*)$", re.S)
_LOOKUP = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "=": "exact", "!=": "exact"}
VIDE = "(vide)"
# Champs techniques jamais renvoyés à un client (lecteur) : le code d'accès d'une ligne permet de se
# connecter à l'espace client À LA PLACE de la personne qui l'a soumise.
CLES_PRIVEES_CLIENT = ("code_acces_client",)


class _ChampVue:
    """Colonne de la TABLE GÉNÉRALE d'un groupe : même slug/type dans chaque partie, mais un champ (id) par partie —
    la valeur d'une ligne est rattachée au champ de SA partie, donc recherche, filtre et tri visent tous ces ids."""
    def __init__(self, champ, ids):
        self.slug, self.nom, self.type_champ, self.ids = champ.slug, champ.nom, champ.type_champ, list(ids)


def _ids(champ):
    return getattr(champ, "ids", None) or [champ.pk]


def _valeur(champ):
    return Subquery(ValeurChamp.objects.filter(enregistrement=OuterRef("pk"), champ_id__in=_ids(champ)).values("valeur")[:1])


def _nombre(texte):
    try:
        return float(str(texte).replace(" ", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _filtrer(qs, champ, expression, n):
    m = _OPERATEUR.match(expression)
    op, v = (m.group(1), m.group(2).strip()) if m else ("~", expression.strip())
    alias = f"_gf{n}"
    if v == VIDE and op in ("=", "!="):
        qs = qs.annotate(**{alias: _valeur(champ)})
        vide = Q(**{f"{alias}__isnull": True}) | Q(**{alias: ""})
        return qs.filter(vide) if op == "=" else qs.exclude(vide)
    if op == "~":
        return qs.filter(id__in=ValeurChamp.objects.filter(champ_id__in=_ids(champ), valeur__icontains=v).values("enregistrement_id"))
    nombre = _nombre(v) if champ.type_champ in TYPES_NUMERIQUES else None
    if nombre is not None:
        qs = qs.annotate(**{alias: Cast(_valeur(champ), FloatField())})
        cond = Q(**{f"{alias}__{_LOOKUP[op]}": nombre})
    else:
        qs = qs.annotate(**{alias: _valeur(champ)})
        cond = Q(**{f"{alias}__iexact": v}) if op in ("=", "!=") else Q(**{f"{alias}__{_LOOKUP[op]}": v})
    return qs.exclude(cond) if op == "!=" else qs.filter(cond)


def _valeur_nosql(champ):
    return KeyTextTransform(champ.slug, "valeurs_json")


def _filtrer_nosql(qs, champ, expression, n):
    m = _OPERATEUR.match(expression)
    op, v = (m.group(1), m.group(2).strip()) if m else ("~", expression.strip())
    alias = f"_gfn{n}"
    if v == VIDE and op in ("=", "!="):
        qs = qs.annotate(**{alias: _valeur_nosql(champ)})
        vide = Q(**{f"{alias}__isnull": True}) | Q(**{alias: ""})
        return qs.filter(vide) if op == "=" else qs.exclude(vide)
    if op == "~":
        return qs.filter(**{f"valeurs_json__{champ.slug}__icontains": v})
    nombre = _nombre(v) if champ.type_champ in TYPES_NUMERIQUES else None
    if nombre is not None:
        qs = qs.annotate(**{alias: Cast(_valeur_nosql(champ), FloatField())})
        cond = Q(**{f"{alias}__{_LOOKUP[op]}": nombre})
    else:
        qs = qs.annotate(**{alias: _valeur_nosql(champ)})
        cond = Q(**{f"{alias}__iexact": v}) if op in ("=", "!=") else Q(**{f"{alias}__{_LOOKUP[op]}": v})
    return qs.exclude(cond) if op == "!=" else qs.filter(cond)


def _appliquer_nosql(qs, table, params):
    """Équivalent de `appliquer()` pour une table NoSQL : mêmes paramètres
    GET (q, f_<slug>, tri, sens), mais les lookups portent sur
    Enregistrement.valeurs_json (JSONField) plutôt que sur des sous-requêtes
    ValeurChamp (aucune ligne ValeurChamp n'existe pour ces tables)."""
    champs = {c.slug: c for c in table.champs.all()}
    criteres = {}
    q = (params.get("q") or "").strip()
    if q:
        cond = Q()
        for slug in champs:
            cond |= Q(**{f"valeurs_json__{slug}__icontains": q})
        cond |= Q(matricule__icontains=q)
        if q.lstrip("#").isdigit():
            cond |= Q(id=int(q.lstrip("#")))
        qs = qs.filter(cond)
        criteres["q"] = q
    n = 0
    for cle in sorted(params.keys()):
        if not cle.startswith("f_"):
            continue
        champ, expr = champs.get(cle[2:]), (params.get(cle) or "").strip()
        if champ is None or not expr:
            continue
        n += 1
        qs = _filtrer_nosql(qs, champ, expr, n)
        criteres.setdefault("filtres", {})[champ.slug] = expr
    tri, sens = (params.get("tri") or "").strip(), (params.get("sens") or "").strip().lower()
    desc = sens != "asc"
    if tri == "_id":
        qs = qs.order_by("-id" if desc else "id")
    elif tri in champs:
        champ = champs[tri]
        cle = Cast(_valeur_nosql(champ), FloatField()) if champ.type_champ in TYPES_NUMERIQUES else _valeur_nosql(champ)
        qs = qs.annotate(_gntri=cle)
        ordre = F("_gntri").desc(nulls_last=True) if desc else F("_gntri").asc(nulls_last=True)
        qs = qs.order_by(ordre, "-id")
    else:
        tri = "_cree_le"
        qs = qs.order_by("cree_le" if sens == "asc" else "-cree_le", "-id")
    criteres["tri"], criteres["sens"] = tri, ("desc" if desc else "asc")
    return qs, criteres


@contextlib.contextmanager
def chrono(etape, contexte=""):
    """Journal de temps OPTIONNEL des exports : avec la variable d'environnement GYPLATFORM_PROFIL_EXPORT=1, la durée de chaque étape est
    écrite dans les journaux (niveau WARNING, « export <contexte> | <étape> : N s ») pour voir où part le temps sur un gros volume.
    Sans la variable : aucun effet."""
    if not os.environ.get("GYPLATFORM_PROFIL_EXPORT"):
        yield
        return
    debut = time.monotonic()
    try:
        yield
    finally:
        logging.getLogger(__name__).warning("export %s | %s : %.1f s", contexte, etape, time.monotonic() - debut)


def appliquer(qs, table, params):
    """Retourne (queryset filtré et trié, description des critères appliqués)."""
    if table.est_nosql:
        return _appliquer_nosql(qs, table, params)
    from .vue_groupe import ids_champs_par_slug, tables_vue
    parties = tables_vue(table)                      # un groupe = UNE table : toutes ses parties
    ids_par_slug = ids_champs_par_slug(table)
    champs = {c.slug: (_ChampVue(c, ids_par_slug.get(c.slug, [c.id])) if table.groupe_id else c) for c in table.champs.all()}
    criteres = {}
    q = (params.get("q") or "").strip()
    filtres, n = [], 0
    for cle in sorted(params.keys()):
        if not cle.startswith("f_"):
            continue
        champ, expr = champs.get(cle[2:]), (params.get(cle) or "").strip()
        if champ is None or not expr:
            continue
        n += 1
        filtres.append((n, champ, expr))
        criteres.setdefault("filtres", {})[champ.slug] = expr
    # Recherche et filtres lus dans le fichier Parquet de la table (un seul passage) quand c'est possible ; sinon (NoSQL, pyarrow absent,
    # condition non prise en charge, trop de résultats) la requête SQL sur ValeurChamp d'avant, au résultat identique.
    from . import parquet_recherche
    predicats = ([parquet_recherche.predicat_texte(q)] if q else []) + [parquet_recherche.predicat_filtre(c, e) for _, c, e in filtres]
    pris_en_charge = [p for p in predicats if p is not None]
    trouves = iter(parquet_recherche.ids_correspondants(table, pris_en_charge) or []) if pris_en_charge else iter(())
    ids_q = next(trouves, None) if q else None
    if q:
        if ids_q is not None:
            cond = Q(id__in=list(ids_q))
        else:
            cond = Q(id__in=ValeurChamp.objects.filter(enregistrement__table__in=parties, valeur__icontains=q).values("enregistrement_id"))
        cond |= Q(matricule__icontains=q)
        if q.lstrip("#").isdigit():
            cond |= Q(id=int(q.lstrip("#")))
        qs = qs.filter(cond)
        criteres["q"] = q
    for (num, champ, expr), pred in zip(filtres, predicats[1 if q else 0:]):
        ids = next(trouves, None) if pred is not None else None
        qs = qs.filter(id__in=list(ids)) if ids is not None else _filtrer(qs, champ, expr, num)
    tri, sens = (params.get("tri") or "").strip(), (params.get("sens") or "").strip().lower()
    desc = sens != "asc"
    if tri == "_id":
        qs = qs.order_by("-id" if desc else "id")
    elif tri in champs:
        champ = champs[tri]
        ordre_parquet = _ordre_parquet(qs, table, champ, desc)
        if ordre_parquet is not None:
            qs = qs.order_by(ordre_parquet, "-id")
        else:
            cle = Cast(_valeur(champ), FloatField()) if champ.type_champ in TYPES_NUMERIQUES else _valeur(champ)
            qs = qs.annotate(_gtri=cle)
            ordre = F("_gtri").desc(nulls_last=True) if desc else F("_gtri").asc(nulls_last=True)
            qs = qs.order_by(ordre, "-id")
    else:
        tri = "_cree_le"
        qs = qs.order_by("cree_le" if sens == "asc" else "-cree_le", "-id")
    criteres["tri"], criteres["sens"] = tri, ("desc" if desc else "asc")
    return qs, criteres


def _ordre_parquet(qs, table, champ, desc):
    """Expression de tri lue dans le fichier Parquet de la table, ou None (le tri SQL par sous-requête s'applique alors)."""
    from . import parquet_recherche
    if not parquet_recherche.actif():
        return None
    try:
        ids = list(qs.values_list("id", flat=True)[:parquet_recherche.LIMITE_TRI + 1])
    except Exception:
        return None
    ordonnes = parquet_recherche.ids_tries(table, ids, champ.slug, champ.type_champ in TYPES_NUMERIQUES, desc)
    return None if ordonnes is None else parquet_recherche.expression_ordre(ordonnes)


def completer_identifiants(qs):
    """Lignes créées AVANT l'existence du matricule / du code d'accès individuel : Enregistrement.save() les génère
    seulement à la sauvegarde, donc elles s'affichaient vides (« — »). On les complète à l'ouverture de la grille
    (par lots de 500, rien à faire une fois à jour). Jamais pour un client (lecture seule), voir api_lignes."""
    manquantes = qs.filter(
        Q(matricule="", table__est_table_membres=True)
        | Q(matricule="", table__expose_client=True) | Q(matricule="", table__masquer_formulaire_client=True)
        | Q(code_acces_client="", table__est_table_membres=True, table__est_table_produits=False)
    ).select_related("table")[:500]
    for enr in list(manquantes):
        # .update() et non save() : pas de signaux (blockchain, recalculs) pour un simple identifiant technique.
        maj = {}
        if not enr.matricule:
            maj["matricule"] = enr.generer_matricule()
        if not enr.code_acces_client and enr.table.a_identifiants_connexion:
            maj["code_acces_client"] = enr.generer_code_acces_client()
        if maj:
            type(enr).objects.filter(pk=enr.pk).update(**maj)


def nettoyer_pour_client(lignes):
    for ligne in lignes:
        for cle in CLES_PRIVEES_CLIENT:
            ligne.pop(cle, None)
    return lignes


def masquer_champs(lignes, slugs_masques):
    """Phase 9 (cahier des charges Ontology, « SÉCURITÉ FINE ») : retire de
    chaque ligne (dict slug -> valeur, voir Enregistrement.to_dict) les
    slugs de `slugs_masques` (un agent restreint par champs_masques ne doit
    jamais recevoir la valeur d'un champ masqué, même en lecture seule).
    `slugs_masques` vide = aucun changement (mêmes appels, pas de branche
    supplémentaire côté appelant) — même forme que nettoyer_pour_client."""
    if not slugs_masques:
        return lignes
    for ligne in lignes:
        for slug in slugs_masques:
            ligne.pop(slug, None)
    return lignes


# ── Export tableur ────────────────────────────────────────────────────────────
def _texte_cellule(valeur):
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return t("engine/grille.py:g:218:16") if valeur else t("engine/grille.py:g:218:37")
    if isinstance(valeur, (list, tuple)):
        return ", ".join(_texte_cellule(v) for v in valeur)
    if isinstance(valeur, dict):
        return valeur.get("label") or valeur.get("nom") or ", ".join(f"{k}: {v}" for k, v in valeur.items())
    return str(valeur)


def _neutraliser_formule(texte):
    """Injection de formule CSV/Excel : une cellule commençant par = + - @ serait EXÉCUTÉE par le tableur
    (ex. =HYPERLINK(...) ou =cmd|...). Préfixe d'une apostrophe, comme le recommande l'OWASP."""
    if texte and texte[0] in "=+-@\t\r" and _nombre(texte) is None:
        return "'" + texte
    return texte


def entetes_et_lignes(champs, lignes):
    entetes = ["N°"] + [c.nom for c in champs]
    corps = []
    for ligne in lignes:
        rang = [ligne.get("_id")]
        for c in champs:
            v = ligne.get(c.slug)
            if c.type_champ in TYPES_NUMERIQUES and not isinstance(v, bool):
                n = _nombre(v) if v not in (None, "") else None
                rang.append(n if n is not None else _neutraliser_formule(_texte_cellule(v)))
            else:
                rang.append(_neutraliser_formule(_texte_cellule(v)))
        corps.append(rang)
    return entetes, corps


def en_csv(champs, lignes):
    """CSV lisible directement par Excel en français : BOM UTF-8, séparateur « ; », décimales avec virgule."""
    entetes, corps = entetes_et_lignes(champs, lignes)
    tampon = io.StringIO()
    w = csv.writer(tampon, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(entetes)
    for rang in corps:
        w.writerow([(f"{v:g}".replace(".", ",") if isinstance(v, float) else v) for v in rang])
    return ("﻿" + tampon.getvalue()).encode("utf-8")


def en_xlsx(nom_feuille, champs, lignes, meta=None, mises_en_forme=None, fusions_vue=None, couleurs_table=None):
    """Classeur .xlsx (openpyxl) : en-tête figé et en gras, filtre automatique, nombres typés, largeurs
    ajustées. Retourne None si openpyxl est absent (l'appelant se replie sur le CSV).

    COULEURS (comme la grille et le PDF) : en-tête d'une colonne colorée en couleur pleine (texte noir ou blanc selon le fond), cellules
    d'une colonne colorée en teinte claire ; une ligne ou une cellule coloriée à la main (`mises_en_forme`, une par élément de `lignes`,
    voir couleur_colonne.mise_en_forme) en teinte plus marquée : cellule > ligne > colonne.
    FUSIONS (`fusions_vue`, voir export_structure.fusions_vue) : chaque rectangle devient une plage fusionnée Excel (merge_cells), avec le
    texte de la fusion en haut à gauche et la couleur de sa cellule d'ancrage — pour n'importe quelle vue exportée (filtre, tri…).
    COULEURS DE LA TABLE (`couleurs_table` = {entete, ligne_paire, texte, accent} en « #rrggbb », celles de la marque de l'entreprise
    que le PDF utilise déjà) : fond de l'en-tête, lignes alternées (sous les couleurs de colonne, ligne et cellule), couleur du texte, onglet.
    Le filtre automatique est retiré quand il y a des fusions : Excel refuse de trier une plage de cellules fusionnées de tailles différentes."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, GradientFill, PatternFill
        from openpyxl.styles.fills import Stop
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None
    from .couleur_colonne import couleur_texte, sur_blanc
    from .export_structure import fond_cellule_export, fond_fusion, zones_fusion
    entetes, corps = entetes_et_lignes(champs, lignes)
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = re.sub(r"[\[\]:*?/\\]", " ", nom_feuille)[:31] or t("engine/grille.py:g:274:71")
    feuille.append(entetes)
    couleurs_col = [(getattr(c, "couleur", "") or "") for c in champs]
    ct = couleurs_table or {}
    _h = lambda v, defaut: (v or defaut).lstrip("#").upper()
    entete_fond, paire_fond, texte_couleur = _h(ct.get("entete"), "2F3A4A"), _h(ct.get("ligne_paire"), ""), _h(ct.get("texte"), "")
    if ct.get("accent"):
        feuille.sheet_properties.tabColor = _h(ct["accent"], "")
    for cellule in feuille[1]:
        cellule.font = Font(bold=True, color=couleur_texte("#" + entete_fond).lstrip("#").upper())
        cellule.fill = PatternFill("solid", fgColor=entete_fond)
    for j, k in enumerate(couleurs_col, start=2):   # colonne colorée : en-tête en couleur pleine, texte lisible dessus
        if k:
            cellule = feuille.cell(row=1, column=j)
            cellule.fill = PatternFill("solid", fgColor=sur_blanc(k).lstrip("#").upper())
            cellule.font = Font(bold=True, color=couleur_texte(k).lstrip("#").upper())
    for rang in corps:
        feuille.append(rang)
    fonds = {}   # un même fond n'est créé qu'une fois (openpyxl dédoublonne mal les styles sur de grosses tables)

    def _remplir(cellule, fond):
        if fond:
            if fond not in fonds:
                fonds[fond] = PatternFill("solid", fgColor=fond.lstrip("#").upper())
            cellule.fill = fonds[fond]

    police_texte = Font(color=texte_couleur) if texte_couleur else None
    for i in range(len(corps)):
        forme = mises_en_forme[i] if mises_en_forme and i < len(mises_en_forme) else None
        zebre = ("#" + paire_fond) if (paire_fond and i % 2 == 1) else ""   # même alternance que le PDF ; les couleurs posées par-dessus l'emportent
        premiere = feuille.cell(row=i + 2, column=1)
        _remplir(premiere, fond_cellule_export("", {"_couleur": forme["_couleur"]}, "") if forme and forme.get("_couleur") else zebre)
        for j, c in enumerate(champs):
            cellule = feuille.cell(row=i + 2, column=j + 2)
            _remplir(cellule, fond_cellule_export(couleurs_col[j], forme, c.slug) or zebre)
            if police_texte:
                cellule.font = police_texte
        if police_texte:
            premiere.font = police_texte
    for r in fusions_vue or []:
        if r.haut >= len(corps) or r.col_debut >= len(champs):
            continue
        bas, col_fin = min(r.bas, len(corps) - 1), min(r.col_fin, len(champs) - 1)
        ancre = feuille.cell(row=r.haut + 2, column=r.col_debut + 2)
        ancre.value = _neutraliser_formule(_texte_cellule(r.texte))
        # Chaque cellule fusionnée garde SA couleur. Excel ne montre d'une plage fusionnée que le format de sa 1re cellule : quand les couleurs
        # diffèrent, la plage reçoit un DÉGRADÉ À PALIERS NETS (une bande par colonne, ou par ligne si seules les lignes diffèrent) ; si les
        # deux varient, les bandes suivent les colonnes (couleurs de la ligne d'ancrage).
        def _fond_zone(i, c):
            forme_c = mises_en_forme[i] if mises_en_forme and i < len(mises_en_forme) else None
            return fond_fusion(couleurs_col[c], forme_c, champs[c].slug)
        from types import SimpleNamespace as _NS
        par_col = {}
        for z_haut, z_bas, z_col, z_fond in zones_fusion(_NS(haut=r.haut, bas=bas, col_debut=r.col_debut, col_fin=col_fin), _fond_zone):
            par_col.setdefault(z_col, []).append((z_bas - z_haut + 1, z_fond))
        tous = {f for lst in par_col.values() for (_, f) in lst}
        if len(tous) == 1:
            _remplir(ancre, next(iter(tous)))
        else:
            if all(len(lst) == 1 for lst in par_col.values()) or len({tuple(lst) for lst in par_col.values()}) > 1:
                degre, bandes = 0, [(1, lst[0][1]) for _, lst in sorted(par_col.items())]   # une bande par colonne
            else:
                degre, bandes = 90, par_col[r.col_debut]                                       # une bande par suite de lignes
            total, cumul, paliers = sum(n for n, _ in bandes), 0, []
            for n, f in bandes:
                debut, fin = cumul / total, (cumul + n) / total
                paliers += [Stop("00" + f.lstrip("#").upper(), debut), Stop("00" + f.lstrip("#").upper(), max(debut, fin - 1e-6))]
                cumul += n
            ancre.fill = GradientFill(type="linear", degree=degre, stop=tuple(paliers))
        ancre.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        if police_texte:
            ancre.font = Font(bold=True, color=texte_couleur)
        feuille.merge_cells(start_row=r.haut + 2, start_column=r.col_debut + 2, end_row=bas + 2, end_column=col_fin + 2)
    feuille.freeze_panes = "A2"
    if corps and not fusions_vue:
        feuille.auto_filter.ref = f"A1:{get_column_letter(len(entetes))}{len(corps) + 1}"
    for i, titre in enumerate(entetes, start=1):
        largeur = max([len(str(titre))] + [len(str(r[i - 1])) for r in corps[:500] if r[i - 1] is not None])
        feuille.column_dimensions[get_column_letter(i)].width = min(max(8, largeur + 2), 60)
    if meta:   # manifeste de restauration fidèle (voir export_fidele.py) : feuille CACHÉE, une cellule par tronçon
        cachee = classeur.create_sheet("_gy_meta")
        for troncon in meta:
            cachee.append([troncon])
        cachee.sheet_state = "hidden"
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


def en_parquet(champs, lignes, meta=None):
    """Fichier Parquet de la grille — mêmes lignes (filtrées, triées, masquées) et mêmes cellules que en_csv/en_xlsx : les nombres sont
    des nombres (entiers ou décimaux), tout le reste le texte exact du CSV, une cellule vide est un vide (null). Le manifeste de
    restauration fidèle (export_fidele.en_cellules) est porté par les métadonnées du fichier : réimporté, le fichier restaure la table
    à l'identique ; lu ailleurs (pandas, Spark, DuckDB…), c'est un Parquet ordinaire. Retourne None si pyarrow est absent."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None
    entetes, corps = entetes_et_lignes(champs, lignes)
    noms, vus = [], {}
    for titre in entetes:   # deux colonnes de même nom : Parquet les confondrait à la lecture
        base = str(titre)
        vus[base] = vus.get(base, 0) + 1
        noms.append(base if vus[base] == 1 else f"{base} ({vus[base]})")
    colonnes = []
    for j in range(len(noms)):
        valeurs = [rang[j] for rang in corps]
        presentes = [v for v in valeurs if v is not None and v != ""]
        if presentes and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in presentes):
            entier = all(isinstance(v, int) or float(v).is_integer() and abs(v) < 2 ** 53 for v in presentes)
            colonnes.append(pa.array([None if v in (None, "") else (int(v) if entier else float(v)) for v in valeurs],
                                     type=pa.int64() if entier else pa.float64()))
        else:
            colonnes.append(pa.array([None if v in (None, "") else str(v) for v in valeurs], type=pa.string()))
    table = pa.Table.from_arrays(colonnes, names=noms)
    if meta:
        extra = {b"gy_meta_n": str(len(meta)).encode()}
        extra.update({b"gy_meta_" + str(i).encode(): troncon.encode("ascii") for i, troncon in enumerate(meta)})
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), **extra})
    tampon = io.BytesIO()
    pq.write_table(table, tampon, compression="zstd")
    return tampon.getvalue()


def en_json(champs, lignes):
    """Export JSON de la vue (mêmes lignes filtrées/triées que en_csv/en_xlsx) —
    remplace le CSV pour une table NoSQL (voir nosql_store.py) : la donnée y est
    nativement imbriquée, un CSV à plat n'a pas de sens pour ce stockage. Une
    ligne redevient un objet {nom du champ: valeur}, comme le fichier miroir de
    nosql_store.synchroniser_fichier, avec les valeurs dans leur type natif
    (nombre, booléen, liste...) plutôt qu'aplaties en texte comme pour le
    CSV/Excel — plus lisible pour un import ailleurs ou une lecture humaine."""
    objets = []
    for ligne in lignes:
        objet = {"N°": ligne.get("_id")}
        for c in champs:
            objet[c.nom] = ligne.get(c.slug)
        objets.append(objet)
    return json.dumps(objets, ensure_ascii=False, indent=2, default=str).encode("utf-8")
