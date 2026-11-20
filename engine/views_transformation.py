"""TRANSFORMATION DE COLONNE (façon Power BI) : modifier d'un coup toutes les cellules d'une colonne par regex.

  POST /api/tables/<id>/champs/<champ_id>/transformer/       corps JSON = paramètres de engine/transformation_colonne.py
        (operation, motif, remplacement, est_regex, casse, tout, groupe, si_absent, texte) + `apercu` (bool).
        apercu=true : AUCUNE écriture, renvoie le nombre de cellules touchées et des exemples avant → après.
        apercu=false : applique, journalise (JournalTransformationColonne) et écrit UN bloc de blockchain pour toute la colonne.
  POST /api/transformations-colonne/<id>/annuler/            restaure les anciennes valeurs (cellules intactes depuis la transformation) ou retire les colonnes créées.
  POST /api/transformations-colonne/<id>/refaire/             rétablit une transformation annulée (tant qu'aucune nouvelle n'a été faite).
  GET  /api/tables/<id>/transformations/                      historique de la table + état des boutons ; POST .../annuler/ et .../refaire/ = Ctrl+Z / Ctrl+Y (pile).
  GET  /api/tables/<id>/champs/<champ_id>/transformations/   les 10 dernières transformations de la colonne.
  POST /api/tables/<id>/champs/<champ_id>/deriver/           crée de NOUVELLES colonnes à partir de celle-ci (la source n'est jamais touchée) :
        mode "cloner" = copie pré-remplie ; mode "decouper" = deux colonnes (motif regex, `dernier` = coupe à la dernière occurrence).
        Mêmes droits, même `apercu` ; l'opération est journalisée : « Annuler » retire les colonnes créées (voir l'historique plus bas).

GROUPE DE TABLES : une colonne d'une table qui appartient à un GroupeTables ne se transforme jamais seule. Le groupe est UNE grande table
(ses tables enfants en sont les parties) : transformer la colonne d'une partie transforme la colonne de même nom (slug) de TOUTES les parties
du groupe, en un seul geste, un seul journal, un seul bloc de blockchain, un seul « Annuler ». L'historique est celui du groupe.

Droits = ceux de la modification d'une ligne (api_modifier_ligne) : jamais un client (lecteur), jamais une table reçue par
partage, jamais un champ masqué ; un agent ne transforme QUE les lignes qu'il a créées ; l'admin voit et modifie tout.
Les cellules dont le résultat ne passe pas la validation du type de la colonne (ex. du texte dans une colonne nombre) sont
laissées intactes et comptées dans `refusees_par_le_type` — la transformation n'abîme jamais une donnée.
"""
from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf
import json
import logging
import time

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from . import geo_adresses, transformation_colonne as tc
from .ratelimit import limiter
from .models import (
    Bloc, ChampDynamique, Enregistrement, JournalTransformationColonne, MembreEntreprise, TableDynamique, ValeurChamp,
)
from .views import (
    _champs_masques_pour, _entreprise_courante, _interdit_table_non_exposee, _valider_type_valeur,
)
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

# Types dont le contenu n'est pas un texte transformable (fichiers, liens, calculés, tenseurs, listes).
TYPES_EXCLUS = frozenset(
    set(ChampDynamique.TYPES_FICHIER) | set(ChampDynamique.TYPES_TENSEUR)
    | {"relation", "colonne", "choix_multiple", "formule", "constante", "horodatage", "id_client", "json"}
)
MAX_CELLULES = 100000
MAX_VALEURS_JOURNAL = 50000      # au-delà, la transformation s'applique mais n'est pas annulable (journal trop lourd)
MAX_EXEMPLES_BLOC = 20
MAX_EXEMPLES_APERCU = 10        # l'aperçu n'affiche qu'un échantillon : le reste de la colonne est traité à « Appliquer »


def _cibles(table, champ):
    """[(table, colonne)] réellement transformées quand on vise `champ` de `table` : la colonne seule hors groupe ; dans un groupe, la colonne de
    même nom (slug) et de même type de CHAQUE partie du groupe (hors tables dénaturées, comme enregistrements_groupe). La partie visée est
    toujours incluse, en premier."""
    if not table.groupe_id:
        return [(table, champ)]
    cibles = [(table, champ)]
    for t in table.groupe.tables.order_by("partie_index", "id"):
        if t.id == table.id or t.denaturee:
            continue
        c = t.champs.filter(slug=champ.slug, type_champ=champ.type_champ).first()
        if c is not None:
            cibles.append((t, c))
    return cibles


def _refus_groupe(request, membre, table, champ):
    """Transformer la colonne d'un groupe touche toutes ses parties : il faut y avoir droit partout, sinon refus (aucune écriture partielle)."""
    for t, c in _cibles(table, champ)[1:]:
        refus = _interdit_table_non_exposee(request, t)
        if refus is None and c.slug in _champs_masques_pour(membre, t):
            refus = JsonResponse({"erreur": td("engine/views_transformation.py:g:80:45")}, status=403)
        if refus is not None:
            return JsonResponse({"erreur": tf("engine/views_transformation.py:g:80:44", v1=t.nom)}, status=403)
    return None


def _contexte(request, table_id, champ_id):
    """(table, champ, membre, entreprise, refus). `refus` = JsonResponse à renvoyer si l'accès est interdit."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _interdit_table_non_exposee(request, table)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if refus is None and (membre is None or membre.role == "lecteur"):
        refus = JsonResponse({"erreur": td("engine/views_transformation.py:g:93:41")}, status=403)
    champ = get_object_or_404(ChampDynamique, id=champ_id, table=table)
    if refus is None and champ.slug in _champs_masques_pour(membre, table):
        refus = JsonResponse({"erreur": td("engine/views_transformation.py:g:96:41")}, status=403)
    if refus is None and champ.type_champ in TYPES_EXCLUS:
        refus = JsonResponse(
            {"erreur": tf("engine/views_transformation.py:g:100:24", v1=champ.type_champ)}, status=400)
    if refus is None and table.groupe_id:
        refus = _refus_groupe(request, membre, table, champ)
    return table, champ, membre, entreprise, refus


def _cellules(table, champ, request, membre, groupe=True):
    """Liste de (enregistrement_id, valeur_brute) des lignes que l'utilisateur a le droit de modifier. Dans un groupe (`groupe=True`) : les
    lignes de la colonne dans TOUTES les parties du groupe (les id de lignes sont uniques, une cellule = une seule partie)."""
    cellules = []
    for t, c in (_cibles(table, champ) if groupe else [(table, champ)]):
        reste = MAX_CELLULES + 1 - len(cellules)
        if reste <= 0:
            break
        qs_lignes = Enregistrement.objects.filter(table=t)
        if membre and membre.role == "agent":
            from . import propriete_tables
            qs_lignes = propriete_tables.filtrer_lignes(qs_lignes, request.user)
        if t.est_nosql:
            cellules += [(e.id, (e.valeurs_json or {}).get(c.slug)) for e in qs_lignes.only("id", "valeurs_json")[:reste]]
        else:
            cellules += list(ValeurChamp.objects.filter(champ=c, enregistrement__in=qs_lignes)
                             .values_list("enregistrement_id", "valeur")[:reste])
    return cellules


def _corps(request):
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return None
    return corps if isinstance(corps, dict) else None


def _filtrer_par_type(champ, changements):
    """Sépare les changements valides des refusés par la validation du type de la colonne (même type dans toutes les parties d'un groupe)."""
    valides, refuses = [], []
    for cle, avant, apres in changements:
        if apres == "" or _valider_type_valeur(champ, apres) is None:
            valides.append((cle, avant, apres))
        else:
            refuses.append((cle, avant, apres))
    return valides, refuses


@login_required
@require_http_methods(["POST"])
def api_transformer_colonne(request, table_id, champ_id):
    table, champ, membre, entreprise, refus = _contexte(request, table_id, champ_id)
    if refus:
        return refus
    corps = _corps(request)
    if corps is None:
        return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:150:40")}, status=400)
    apercu = bool(corps.get("apercu", True))

    cellules = _cellules(table, champ, request, membre)
    if len(cellules) > MAX_CELLULES:
        return JsonResponse({"erreur": tf("engine/views_transformation.py:g:155:40", v1=MAX_CELLULES)}, status=400)
    try:
        changements, resume = tc.transformer_colonne(cellules, corps)
    except tc.ErreurTransformation as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    if resume["interrompu"]:
        return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:161:40")}, status=400)

    valides, refuses = _filtrer_par_type(champ, changements)
    reponse = {
        "apercu": apercu, "champ": champ.nom, "total_lignes": len(cellules),
        "examinees": resume["examinees"], "modifiees": len(valides),
        "vides_ignorees": resume["ignorees_vides"],
        "trop_longues_ou_lentes": resume["ignorees_trop_longues_ou_lentes"],
        "refusees_par_le_type": len(refuses),
        # lignes non vides que l'opération ne touche pas (motif absent, valeur déjà conforme) : hors aperçu et hors écriture
        "non_concernees": max(0, resume["examinees"] - resume["ignorees_vides"] - resume["ignorees_trop_longues_ou_lentes"] - len(changements)),
        "groupe": _infos_groupe(table, champ),
        "exemples": [{"avant": a, "apres": b} for _c, a, b in valides[:MAX_EXEMPLES_APERCU]],
        "exemples_refuses": [{"avant": a, "apres": b} for _c, a, b in refuses[:MAX_EXEMPLES_APERCU // 2]],
    }
    if apercu or not valides:
        return JsonResponse(reponse)

    annulable = len(valides) <= MAX_VALEURS_JOURNAL
    if not annulable and not corps.get("confirmer_sans_annulation"):
        # Une transformation trop volumineuse ne peut pas être annulée : on le dit AVANT d'écrire, l'utilisateur confirme ou renonce.
        return JsonResponse({"erreur": tf("engine/views_transformation.py:g:180:40", v1=len(valides), v2=MAX_VALEURS_JOURNAL),
                             "code": "non_annulable", "modifiees": len(valides)}, status=409)
    with transaction.atomic():
        _vider_refaire(table)
        _ecrire(table, champ, valides)
        journal = JournalTransformationColonne.objects.create(
            entreprise=entreprise, table=table, champ=champ, utilisateur=request.user,
            libelle=_libelle_operation(corps, champ),
            operation={k: corps.get(k) for k in (
                "operation", "motif", "remplacement", "est_regex", "casse", "tout", "groupe", "si_absent", "texte")},
            anciennes_valeurs={str(c): a for c, a, _b in valides} if annulable else {},
            nouvelles_valeurs={str(c): b for c, _a, b in valides} if annulable else {},
            nb_modifiees=len(valides),
        )
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "transformation_colonne", "table_id": table.id, "table": table.nom, "champ": champ.nom,
            "agent_id": request.user.id, "operation": corps.get("operation"), "motif": corps.get("motif"),
            "groupe_id": table.groupe_id, "tables_du_groupe": [t.nom for t, _c in _cibles(table, champ)] if table.groupe_id else None,
            "nb_modifiees": len(valides), "journal_id": journal.id,
            "modifications": [{"champ": champ.nom, "avant": a, "apres": b} for _c, a, b in valides[:MAX_EXEMPLES_BLOC]],
        })
    _recalculer_dependances(table, champ)
    reponse.update({"journal_id": journal.id, "annulable": annulable})
    return JsonResponse(reponse)


def _infos_groupe(table, champ):
    """None hors groupe ; sinon {nom, tables:[noms]} : l'interface annonce que TOUT le groupe est transformé."""
    if not table.groupe_id:
        return None
    return {"id": table.groupe_id, "nom": getattr(table.groupe, "nom", ""), "tables": [t.nom for t, _c in _cibles(table, champ)]}


def _ecrire(table, champ, changements):
    """Écrit les nouvelles valeurs (cellule par cellule : les signaux de formules et de relations restent déclenchés). Dans un groupe,
    chaque cellule est écrite dans la colonne de SA partie : une colonne n'appartient qu'à une table, donc filtrer par colonne suffit."""
    ids = [c for c, _a, _b in changements]
    nouvelles = {c: b for c, _a, b in changements}
    for t, c in _cibles(table, champ):
        if t.est_nosql:
            from . import nosql_store
            for e in Enregistrement.objects.filter(table=t, id__in=ids):
                valeurs = dict(e.valeurs_json or {})
                valeurs[c.slug] = nosql_store.coercer_valeur(c, nouvelles[e.id]) if nouvelles[e.id] != "" else None
                nosql_store.ecrire_valeurs(e, valeurs)
            continue
        for vc in ValeurChamp.objects.filter(champ=c, enregistrement_id__in=ids):
            vc.valeur = nouvelles[vc.enregistrement_id]
            vc.save(update_fields=["valeur"])


def _recalculer_dependances(table, champ=None):
    """Comme api_modifier_ligne : les formules de calcul branchées sur cette table suivent la donnée. Dans un groupe, sur CHAQUE partie transformée."""
    for t in ([t for t, _c in _cibles(table, champ)] if champ is not None else [table]):
        _recalculer_table(t)


def _recalculer_table(table):
    try:
        from .calc_champ import recalculer_formules_table
        recalculer_formules_table(table, toutes_les_lignes=True)   # formules ∑ / « = » : n'importe quelle valeur a pu changer
        from .calculs import recalculer_lignes_pour_table_source
        recalculer_lignes_pour_table_source(table)
        if table.est_nosql:
            from . import calculs
            for e in Enregistrement.objects.filter(table=table):
                calculs.recalculer_champs_formule_ligne(e)
    except Exception:  # best-effort : la transformation elle-même est déjà enregistrée
        logger.exception(_i18n_t("engine/views_transformation.py:g:248:26"), table.id)


def _nouvelle_colonne(table, nom, type_champ, options):
    """Crée une colonne facultative en fin de table, au slug unique."""
    base = slugify(nom).replace("-", "_")[:40] or "colonne"
    slug, n = base, 1
    while table.champs.filter(slug=slug).exists():
        n += 1
        slug = f"{base}_{n}"
    ordre = (table.champs.aggregate(m=Max("ordre"))["m"] or 0) + 1
    return ChampDynamique.objects.create(table=table, nom=nom[:120], slug=slug, type_champ=type_champ,
                                         options_choix=options, obligatoire=False, ordre=ordre)


@login_required
@require_http_methods(["POST"])
def api_deriver_colonne(request, table_id, champ_id):
    table, champ, membre, entreprise, refus = _contexte(request, table_id, champ_id)
    corps = _corps(request)
    if refus or corps is None:
        return refus or JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:270:49")}, status=400)
    cloner = corps.get("mode") == "cloner"
    nb_col = 1 if cloner else 2
    plafond = table.plafond_colonnes
    if table.groupe_id:
        return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:275:40")}, status=400)
    if plafond is not None and table.champs.count() + nb_col > plafond:
        return JsonResponse({"erreur": tf("engine/views_transformation.py:g:277:40", v1=plafond)}, status=400)
    cellules = [(c, v) for c, v in _cellules(table, champ, request, membre, groupe=False) if v is not None and str(v) != ""]
    if len(cellules) > MAX_CELLULES:
        return JsonResponse({"erreur": tf("engine/views_transformation.py:g:280:40", v1=MAX_CELLULES)}, status=400)
    paires = []   # (ligne_id, [valeur colonne 1, valeur colonne 2], valeur d'origine) — UNIQUEMENT les lignes concernées
    non_concernees = 0
    try:
        motif = None if cloner else tc.compiler_motif(str(corps.get("motif") or ""), bool(corps.get("est_regex", True)), bool(corps.get("casse", False)))
        debut = time.monotonic()
        for cle, v in cellules:
            if time.monotonic() - debut > tc.DELAI_GLOBAL_S:
                return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:287:48")}, status=400)
            parts = [str(v)] if cloner else tc.decouper_valeur(motif, v, bool(corps.get("dernier", False)), seulement_concernees=True)
            if parts is False:       # séparateur absent : la ligne n'est pas concernée (ni aperçu, ni écriture, ni compte)
                non_concernees += 1
            elif parts is not None:
                paires.append((cle, parts, str(v)))
    except tc.ErreurTransformation as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    exemples = [{"avant": v, "apres": " │ ".join(p)} for _c, p, v in paires[:MAX_EXEMPLES_APERCU]]
    apercu = bool(corps.get("apercu", True))
    reponse = {"apercu": apercu, "champ": champ.nom, "total_lignes": len(cellules), "modifiees": len(paires),
               "vides_ignorees": 0, "refusees_par_le_type": 0, "non_concernees": non_concernees, "exemples": exemples}
    if apercu or not paires:
        return JsonResponse(reponse)
    noms = [f"{champ.nom} (copie)"] if cloner else [f"{champ.nom} 1", f"{champ.nom} 2"]
    with transaction.atomic():
        _vider_refaire(table)
        creees = []
        for i, nom in enumerate(noms):
            nc = _nouvelle_colonne(table, nom, champ.type_champ if cloner else "texte", champ.options_choix if cloner else "")
            valeurs = [(c, p[i]) for c, p, _v in paires]
            if table.est_nosql:
                _ecrire(table, nc, [(c, None, v) for c, v in valeurs])
            else:
                ValeurChamp.objects.bulk_create([ValeurChamp(enregistrement_id=c, champ=nc, valeur=v) for c, v in valeurs], batch_size=1000)
            creees.append((nc, valeurs))
        _journaliser_colonnes(request, entreprise, table, champ, "cloner" if cloner else "decouper", corps, creees,
                              f"{'Cloner' if cloner else _gt("engine/views_transformation.py:321:58")} « {champ.nom} » → {', '.join(noms)}")
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "derivation_colonne", "table_id": table.id, "table": table.nom, "champ": champ.nom, "agent_id": request.user.id,
            "mode": "cloner" if cloner else "decouper", "motif": corps.get("motif"), "nouvelles_colonnes": noms, "nb_lignes": len(paires)})
    _recalculer_dependances(table)
    reponse["nouvelles_colonnes"] = noms
    return JsonResponse(reponse)


# ── Annuler / Rétablir (historique façon Photoshop ou Word) ───────────────────────────────────────────────────────────────────────
# Chaque transformation écrit une entrée de journal. « Annuler » défait la plus RÉCENTE entrée appliquée de la table (pile) ; « Rétablir »
# refait la plus récente entrée annulée. Dès qu'une NOUVELLE transformation est faite, ce qui avait été annulé n'est plus rétablissable.
# Une cellule retouchée à la main depuis n'est jamais écrasée (ni à l'annulation, ni au rétablissement) : elle est comptée à part.
def _journaux_de(table):
    """Journaux de la table — ou de TOUT son groupe : une transformation de groupe est une seule action, quelle que soit la partie d'où on la lance."""
    if table.groupe_id:
        return JournalTransformationColonne.objects.filter(table__groupe_id=table.groupe_id)
    return JournalTransformationColonne.objects.filter(table=table)


def _vider_refaire(table):
    _journaux_de(table).filter(annule=True, refaisable=True).update(refaisable=False)


def _libelle_operation(corps, champ):
    op = corps.get("operation") or "?"
    motif = f" « {corps.get('motif')} »" if corps.get("motif") else ""
    return tf("engine/views_transformation.py:l:op_sur", v1=f"{op}{motif}", v2=champ.nom)[:200]


def _journaliser_colonnes(request, entreprise, table, champ, mode, corps, creees, libelle):
    """Entrée de journal pour des colonnes créées : `creees` = [(ChampDynamique, [(id_ligne, valeur)])]. Les valeurs sont gardées (pour
    rétablir) tant qu'elles ne dépassent pas MAX_VALEURS_JOURNAL ; annuler (retirer les colonnes) reste toujours possible."""
    total = sum(len(v) for _c, v in creees)
    garde = total <= MAX_VALEURS_JOURNAL
    JournalTransformationColonne.objects.create(
        entreprise=entreprise, table=table, champ=champ, utilisateur=request.user, genre="colonnes", libelle=libelle[:200],
        operation={"operation": mode, "motif": corps.get("motif")}, nb_modifiees=total, refaisable=garde,
        colonnes_creees=[{"slug": c.slug, "nom": c.nom, "type_champ": c.type_champ, "options_choix": c.options_choix} for c, _v in creees],
        nouvelles_valeurs={c.slug: {str(i): v for i, v in vals} for c, vals in creees} if garde else {},
    )


def _membre_table(request, table_id):
    """(table, membre, entreprise, refus) pour une opération de niveau TABLE (historique, annuler, rétablir)."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _interdit_table_non_exposee(request, table)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if refus is None and (membre is None or membre.role == "lecteur"):
        refus = JsonResponse({"erreur": td("engine/views_transformation.py:g:362:41")}, status=403)
    return table, membre, entreprise, refus


def _visibles(request, membre, table):
    qs = _journaux_de(table)
    return qs.filter(utilisateur=request.user) if membre.role == "agent" else qs


def _annulable(j):
    if j.annule:
        return False
    return bool(j.anciennes_valeurs) if j.genre == "valeurs" else True


def _etat_historique(request, membre, table):
    qs = _visibles(request, membre, table)
    dernier_applique = qs.filter(annule=False).order_by("-id").first()
    dernier_annule = qs.filter(annule=True, refaisable=True).order_by("-id").first()
    return {
        "peut_annuler": bool(dernier_applique and _annulable(dernier_applique)),
        "peut_refaire": bool(dernier_annule),
        "prochain_annuler": dernier_applique.libelle if dernier_applique else "",
        "prochain_refaire": dernier_annule.libelle if dernier_annule else "",
    }


def _serialiser_journal(j):
    return {"id": j.id, "operation": j.operation, "nb_modifiees": j.nb_modifiees, "annule": j.annule, "genre": j.genre,
            "libelle": j.libelle or (j.operation or {}).get("operation") or "", "champ": j.champ.nom, "champ_id": j.champ_id,
            "annulable": _annulable(j), "refaisable": j.annule and j.refaisable and (j.genre == "colonnes" and bool(j.nouvelles_valeurs) or bool(j.anciennes_valeurs)),
            "cree_le": j.cree_le.isoformat()}


def _journal_pour(request, journal_id):
    """(journal, table, champ, membre, entreprise, refus) — mêmes droits que la transformation d'origine."""
    entreprise = _entreprise_courante(request)
    journal = get_object_or_404(JournalTransformationColonne, id=journal_id, entreprise=entreprise)
    table, membre, _e, refus = _membre_table(request, journal.table_id)
    champ = journal.champ
    if refus is None and membre.role == "agent" and journal.utilisateur_id != request.user.id:
        refus = JsonResponse({"erreur": td("engine/views_transformation.py:g:403:41")}, status=403)
    if refus is None and champ.slug in _champs_masques_pour(membre, table):
        refus = JsonResponse({"erreur": td("engine/views_transformation.py:g:405:41")}, status=403)
    if refus is None and table.groupe_id:
        refus = _refus_groupe(request, membre, table, champ)
    return journal, table, champ, membre, entreprise, refus


def _defaire(request, journal, table, champ, membre, entreprise, forcer=False):
    """Annule UNE entrée. Renvoie (réponse JSON, statut)."""
    table, champ = journal.table, journal.champ        # la partie où la transformation de groupe a été lancée : c'est elle qui ancre le journal
    if journal.annule:
        return {"erreur": _i18n_t("engine/views_transformation.py:g:415:27")}, 400
    if journal.genre == "colonnes":
        slugs = [c["slug"] for c in journal.colonnes_creees]
        champs = list(table.champs.filter(slug__in=slugs))
        modifiees = 0
        if not table.est_nosql and journal.nouvelles_valeurs:
            for c in champs:
                attendu = journal.nouvelles_valeurs.get(c.slug, {})
                actuel = {str(i): v for i, v in ValeurChamp.objects.filter(champ=c).values_list("enregistrement_id", "valeur")}
                modifiees += sum(1 for i, v in actuel.items() if attendu.get(i) != v)
        if modifiees and not forcer:
            return {"erreur": tf("engine/views_transformation.py:g:426:31", v1=modifiees),
                    "code": "modifie_depuis", "modifiees": modifiees}, 409
        with transaction.atomic():
            if table.est_nosql:
                from . import nosql_store
                for e in Enregistrement.objects.filter(table=table):
                    vals = dict(e.valeurs_json or {})
                    if any(vals.pop(sl, None) is not None for sl in slugs):
                        nosql_store.ecrire_valeurs(e, vals)
            ValeurChamp.objects.filter(champ__in=champs).delete()
            for c in champs:
                c.delete()
            journal.annule = True
            journal.save(update_fields=["annule"])
            Bloc.ajouter_transaction(entreprise.id, {"type": "annulation_transformation_colonne", "table_id": table.id, "table": table.nom,
                                                      "champ": champ.nom, "agent_id": request.user.id, "journal_id": journal.id,
                                                      "colonnes_retirees": [c["nom"] for c in journal.colonnes_creees]})
        return {"annulee": True, "colonnes_retirees": [c["nom"] for c in journal.colonnes_creees], "restaurees": 0}, 200
    if not journal.anciennes_valeurs:
        return {"erreur": _i18n_t("engine/views_transformation.py:g:445:27")}, 400
    # Restaure uniquement les cellules INTACTES depuis la transformation (valeur actuelle == valeur écrite) :
    # une cellule retouchée à la main depuis n'est jamais écrasée.
    cellules = dict(_cellules(table, champ, request, membre))
    a_restaurer, conservees = [], 0
    for cle, avant in journal.anciennes_valeurs.items():
        cle_int = int(cle)
        if cle_int not in cellules:
            continue
        if str(cellules[cle_int] if cellules[cle_int] is not None else "") == journal.nouvelles_valeurs.get(cle):
            a_restaurer.append((cle_int, journal.nouvelles_valeurs.get(cle), avant))
        else:
            conservees += 1
    with transaction.atomic():
        _ecrire(table, champ, a_restaurer)
        journal.annule = True
        journal.save(update_fields=["annule"])
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "annulation_transformation_colonne", "table_id": table.id, "table": table.nom, "champ": champ.nom,
            "agent_id": request.user.id, "journal_id": journal.id, "restaurees": len(a_restaurer), "conservees": conservees})
    _recalculer_dependances(table, champ)
    return {"annulee": True, "restaurees": len(a_restaurer), "conservees_car_modifiees_depuis": conservees}, 200


def _refaire(request, journal, table, champ, membre, entreprise):
    """Rétablit UNE entrée annulée. Renvoie (réponse JSON, statut)."""
    table, champ = journal.table, journal.champ
    if not journal.annule:
        return {"erreur": _i18n_t("engine/views_transformation.py:g:473:27")}, 400
    if not journal.refaisable:
        return {"erreur": _i18n_t("engine/views_transformation.py:g:475:27")}, 400
    if journal.genre == "colonnes":
        if not journal.nouvelles_valeurs:
            return {"erreur": _i18n_t("engine/views_transformation.py:g:478:31")}, 400
        plafond = table.plafond_colonnes
        if plafond is not None and table.champs.count() + len(journal.colonnes_creees) > plafond:
            return {"erreur": tf("engine/views_transformation.py:g:481:31", v1=plafond)}, 400
        ids_existants = set(Enregistrement.objects.filter(table=table).values_list("id", flat=True))
        with transaction.atomic():
            for spec in journal.colonnes_creees:
                nc = _nouvelle_colonne(table, spec["nom"], spec["type_champ"], spec.get("options_choix", ""))
                valeurs = [(int(i), v) for i, v in (journal.nouvelles_valeurs.get(spec["slug"]) or {}).items() if int(i) in ids_existants]
                if table.est_nosql:
                    _ecrire(table, nc, [(c, None, v) for c, v in valeurs])
                else:
                    ValeurChamp.objects.bulk_create([ValeurChamp(enregistrement_id=c, champ=nc, valeur=v) for c, v in valeurs], batch_size=1000)
            journal.annule = False
            journal.save(update_fields=["annule"])
            Bloc.ajouter_transaction(entreprise.id, {"type": "retablissement_transformation_colonne", "table_id": table.id, "table": table.nom,
                                                      "champ": champ.nom, "agent_id": request.user.id, "journal_id": journal.id,
                                                      "colonnes": [c["nom"] for c in journal.colonnes_creees]})
        _recalculer_dependances(table)
        return {"retablie": True, "colonnes": [c["nom"] for c in journal.colonnes_creees], "restaurees": journal.nb_modifiees}, 200
    if not journal.anciennes_valeurs:
        return {"erreur": _i18n_t("engine/views_transformation.py:g:499:27")}, 400
    cellules = dict(_cellules(table, champ, request, membre))
    a_refaire, conservees = [], 0
    for cle, avant in journal.anciennes_valeurs.items():
        cle_int = int(cle)
        if cle_int not in cellules:
            continue
        if str(cellules[cle_int] if cellules[cle_int] is not None else "") == avant:
            a_refaire.append((cle_int, avant, journal.nouvelles_valeurs.get(cle, avant)))
        else:
            conservees += 1
    with transaction.atomic():
        _ecrire(table, champ, a_refaire)
        journal.annule = False
        journal.save(update_fields=["annule"])
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "retablissement_transformation_colonne", "table_id": table.id, "table": table.nom, "champ": champ.nom,
            "agent_id": request.user.id, "journal_id": journal.id, "retablies": len(a_refaire), "conservees": conservees})
    _recalculer_dependances(table, champ)
    return {"retablie": True, "restaurees": len(a_refaire), "conservees_car_modifiees_depuis": conservees}, 200


@login_required
@require_http_methods(["POST"])
def api_annuler_transformation_colonne(request, journal_id):
    journal, table, champ, membre, entreprise, refus = _journal_pour(request, journal_id)
    if refus:
        return refus
    corps = _corps(request) or {}
    donnees, statut = _defaire(request, journal, table, champ, membre, entreprise, forcer=bool(corps.get("forcer")))
    return JsonResponse(donnees | ({"historique": _etat_historique(request, membre, table)} if statut == 200 else {}), status=statut)


@login_required
@require_http_methods(["POST"])
def api_refaire_transformation_colonne(request, journal_id):
    journal, table, champ, membre, entreprise, refus = _journal_pour(request, journal_id)
    if refus:
        return refus
    donnees, statut = _refaire(request, journal, table, champ, membre, entreprise)
    return JsonResponse(donnees | ({"historique": _etat_historique(request, membre, table)} if statut == 200 else {}), status=statut)


@login_required
@require_http_methods(["GET"])
def api_transformations_colonne(request, table_id, champ_id):
    table, champ, membre, entreprise, refus = _contexte(request, table_id, champ_id)
    if refus:
        return refus
    qs = _visibles(request, membre, table)
    qs = qs.filter(champ__slug=champ.slug) if table.groupe_id else qs.filter(champ=champ)    # groupe : la même colonne dans toutes ses parties
    return JsonResponse({"transformations": [_serialiser_journal(j) for j in qs.select_related("champ")[:10]]})


@login_required
@require_http_methods(["GET"])
def api_historique_table(request, table_id):
    """Historique de TOUTES les transformations de la table (toutes colonnes) + état des boutons Annuler / Rétablir."""
    table, membre, _e, refus = _membre_table(request, table_id)
    if refus:
        return refus
    qs = _visibles(request, membre, table).select_related("champ")[:50]
    return JsonResponse({"historique": [_serialiser_journal(j) for j in qs], **_etat_historique(request, membre, table)})


@login_required
@require_http_methods(["POST"])
def api_annuler_derniere_transformation(request, table_id):
    """Ctrl+Z : annule la transformation appliquée la plus récente de la table (celles de l'utilisateur pour un agent)."""
    table, membre, entreprise, refus = _membre_table(request, table_id)
    if refus:
        return refus
    journal = _visibles(request, membre, table).filter(annule=False).select_related("champ").order_by("-id").first()
    if journal is None:
        return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:573:40")}, status=400)
    corps = _corps(request) or {}
    donnees, statut = _defaire(request, journal, table, journal.champ, membre, entreprise, forcer=bool(corps.get("forcer")))
    return JsonResponse(donnees | {"libelle": journal.libelle} | ({"historique": _etat_historique(request, membre, table)} if statut == 200 else {}), status=statut)


@login_required
@require_http_methods(["POST"])
def api_refaire_derniere_transformation(request, table_id):
    """Ctrl+Y / Ctrl+Maj+Z : rétablit la transformation annulée la plus récente (tant qu'aucune nouvelle n'a été faite)."""
    table, membre, entreprise, refus = _membre_table(request, table_id)
    if refus:
        return refus
    journal = _visibles(request, membre, table).filter(annule=True, refaisable=True).select_related("champ").order_by("-id").first()
    if journal is None:
        return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:588:40")}, status=400)
    donnees, statut = _refaire(request, journal, table, journal.champ, membre, entreprise)
    return JsonResponse(donnees | {"libelle": journal.libelle} | ({"historique": _etat_historique(request, membre, table)} if statut == 200 else {}), status=statut)


# ── Lieux → coordonnées : une colonne TEXTE (n'importe laquelle) alimente une NOUVELLE colonne « geolocalisation » ──────────────────
# La colonne source n'est jamais touchée (aucune donnée perdue). Le service de recherche limite à ~1 requête/s : le navigateur découpe
# le travail en lots (étape « resoudre », 15 lieux au plus), puis envoie le tout à l'étape « appliquer ». Un lieu est cherché UNE fois
# même s'il revient sur plusieurs lignes.
MAX_LIEUX = 300
LOT_LIEUX = 15


@login_required
@require_http_methods(["POST"])
@limiter("geocoder_colonne", max_appels=30, periode_secondes=60, methodes={"POST"})
def api_geocoder_colonne(request, table_id, champ_id):
    """Étapes (corps JSON {etape}) : « lieux » → lieux distincts de la colonne ; « resoudre » {lieux:[…]} → {lieu: [lat, lon] | null} ;
    « appliquer » {correspondances:{lieu:[lat, lon]}} → crée la colonne « <nom> (coordonnées) » et y écrit « lat, lon »."""
    table, champ, membre, entreprise, refus = _contexte(request, table_id, champ_id)
    corps = _corps(request)
    if refus or corps is None:
        return refus or JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:610:49")}, status=400)
    if champ.type_champ == "geolocalisation":
        return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:612:40")}, status=400)
    etape = corps.get("etape")
    if etape == "lieux":
        cellules = [(c, " ".join(str(v).split())) for c, v in _cellules(table, champ, request, membre, groupe=False) if v is not None and str(v).strip()]
        if len(cellules) > MAX_CELLULES:
            return JsonResponse({"erreur": tf("engine/views_transformation.py:g:617:44", v1=MAX_CELLULES)}, status=400)
        distincts = list(dict.fromkeys(v for _c, v in cellules if len(v) >= 3))
        return JsonResponse({"champ": champ.nom, "total_lignes": len(cellules), "lieux": distincts[:MAX_LIEUX],
                             "lieux_ignores": max(0, len(distincts) - MAX_LIEUX)})
    if etape == "resoudre":
        lieux = [str(x)[:200] for x in (corps.get("lieux") or [])][:LOT_LIEUX] if isinstance(corps.get("lieux"), list) else []
        resultats, erreur = {}, None
        for lieu in lieux:
            try:
                r = geo_adresses.premier_lieu(lieu)
            except geo_adresses.ErreurAdresse as exc:
                erreur = str(exc)
                break
            resultats[lieu] = [round(r[0], 6), round(r[1], 6)] if r else None
        return JsonResponse({"resultats": resultats, "erreur_service": erreur})
    if etape == "appliquer":
        corr = corps.get("correspondances")
        if not isinstance(corr, dict):
            return JsonResponse({"erreur": _gt("rg8.msg.023")}, status=400)
        valides = {}
        for lieu, ll in corr.items():
            try:
                lat, lon = float(ll[0]), float(ll[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                valides[" ".join(str(lieu).split())] = f"{lat}, {lon}"
        if table.groupe_id:
            return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:645:44")}, status=400)
        plafond = table.plafond_colonnes
        if plafond is not None and table.champs.count() + 1 > plafond:
            return JsonResponse({"erreur": tf("engine/views_transformation.py:g:648:44", v1=plafond)}, status=400)
        ecritures = [(c, v) for c, v in ((c, valides.get(" ".join(str(v).split()))) for c, v in _cellules(table, champ, request, membre, groupe=False)
                                         if v is not None) if v]
        if not ecritures:
            return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:652:44")}, status=400)
        nom = _gtf("engine/views_transformation.py:662:15", v1=(champ.nom))
        with transaction.atomic():
            _vider_refaire(table)
            nc = _nouvelle_colonne(table, nom, "geolocalisation", "")
            if table.est_nosql:
                _ecrire(table, nc, [(c, None, v) for c, v in ecritures])
            else:
                ValeurChamp.objects.bulk_create([ValeurChamp(enregistrement_id=c, champ=nc, valeur=v) for c, v in ecritures], batch_size=1000)
            _journaliser_colonnes(request, entreprise, table, champ, "geocoder", corps, [(nc, ecritures)], tf("engine/views_transformation.py:g:661:108", v1=nom))
            Bloc.ajouter_transaction(entreprise.id, {
                "type": "derivation_colonne", "table_id": table.id, "table": table.nom, "champ": champ.nom, "agent_id": request.user.id,
                "mode": "geocoder", "nouvelles_colonnes": [nom], "nb_lignes": len(ecritures)})
        _recalculer_dependances(table)
        return JsonResponse({"nouvelles_colonnes": [nom], "modifiees": len(ecritures), "lieux_convertis": len(valides)})
    return JsonResponse({"erreur": _i18n_t("engine/views_transformation.py:g:667:36")}, status=400)
