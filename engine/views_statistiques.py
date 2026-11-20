"""STATISTIQUES : page dans le châssis du dashboard BI + API (calculs : engine/statistiques.py et engine/statistiques_lois.py, données : engine/statistiques_donnees.py).

  GET  /tables/<id>/statistiques/                              la page (trois parties : descriptives, inférence, probabilités)
  GET  /api/tables/<id>/statistiques/descriptives/             statistiques descriptives complètes, lecture seule. Paramètres :
        tables=<id,id>                   autres tables analysées avec celle-ci (même structure = empilées, sinon séparées)
        colonnes=<id>:<slug>,...         colonnes décrites (l'identifiant de table est obligatoire)
        types=<id>:<slug>:<genre>,...    genre forcé (quanti_continue, quanti_discrete, quali_nominale, quali_ordinale) ; sinon reconnu automatiquement
        par=<id>:<slug>                  découpage par les modalités d'une colonne qualitative
        echantillon=aleatoire|systematique|stratifie|premieres, taille=<n>, graine=<n>, remise=0|1, strate=<id>:<slug>
  GET  /api/tables/<id>/statistiques/test/                     un test d'inférence (test=auto|t1|t2|anova|…, voir statistiques.CATALOGUE_TESTS) :
        a=, b=, groupe=, groupe2=, explicatives= (références <id>:<slug>) + les paramètres du test (alpha, alt, mu0…) + tables/types/échantillon comme ci-dessus
  GET  /api/tables/<id>/statistiques/probabilites/             type=loi|evenements|bayes|combinatoire|ajustement
        loi : loi=<clé>, p_<paramètre>=…, x, bas, haut, q, niveau ; evenements : pa, pb, relation, valeur ; bayes : hypotheses=<JSON> ;
        combinatoire : n, k ; ajustement : colonne=<id>:<slug>, x (+ tables/types/échantillon)

Mêmes droits que la carte et les séries (engine/views_geo._acces, vue « statistiques ») : table propre ou partagée en lecture seule, vue autorisée pour
l'agent ; les clients (lecteurs) y accèdent en lecture seule (tables exposées, abonnement si prix fixé). Une table demandée mais interdite est listée dans `refusees`, jamais lue. Rien n'est enregistré.
"""
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import statistiques, statistiques_lois as lois
from . import statistiques_donnees as donnees
from .vue_groupe import table_generale
from .views_geo import _fiches_pour, _Encodeur, _acces, _nombre

VUE = "statistiques"
logger = logging.getLogger(__name__)


def _liste(texte) -> list:
    return [x.strip() for x in str(texte or "").split(",") if x.strip()]


def _flottant(q, cle, bas=None, haut=None):
    """Nombre de la requête (virgule décimale acceptée) ; None si absent ; ValueError lisible si invalide ou hors bornes."""
    brut = q.get(cle)
    if brut is None or str(brut).strip() == "":
        return None
    try:
        v = float(str(brut).replace(",", ".").strip())
    except ValueError:
        raise ValueError(tf("engine/views_statistiques.py:g:49:26", v1=cle))
    if v != v or v in (float("inf"), float("-inf")) or (bas is not None and v < bas) or (haut is not None and v > haut):
        raise ValueError(tf("engine/views_statistiques.py:g:51:26", v1=cle))
    return v


def _forces(texte) -> dict:
    """« 12:note:quali_ordinale,12:age:quanti_discrete » -> {"12:note": "quali_ordinale", ...}."""
    sortie = {}
    for jeton in _liste(texte):
        t = jeton.split(":")
        if len(t) == 3 and t[2] in statistiques.GENRES:
            sortie[f"{t[0]}:{t[1]}"] = t[2]
    return sortie


def _echantillon(q):
    methode = q.get("echantillon")
    if methode not in statistiques.METHODES_ECHANTILLON:
        return None
    taille = _nombre(q, "taille", int, 1, 100000)
    if taille is None:
        raise ValueError(_i18n_t("engine/views_statistiques.py:g:71:26"))
    return {"methode": methode, "taille": taille, "graine": _nombre(q, "graine", int, 0, 10 ** 9), "remise": q.get("remise") == "1", "strate": (q.get("strate") or "").partition(":")[2] or None}


def _tables(request, table, q, vue=VUE):
    """(tables accessibles, ids refusés) : la table ouverte + celles de `tables=` dont l'accès est permis (droits de la vue `vue`)."""
    tables, refusees = [table], []
    for tid in [int(x) for x in _liste(q.get("tables")) if x.isdigit()]:
        if any(t.id == tid for t in tables):
            continue
        try:
            autre, _ls, _e, refus = _acces(request, tid, api=True, vue=vue)
        except Http404:
            refusees.append(tid)
            continue
        if refus:
            refusees.append(tid)
        else:
            tables.append(autre)
    return tables, refusees


def _reponse(fonction, refusees=()):
    """Exécute une analyse : ValueError → 400 avec le message, autre erreur → 500 sans détail technique."""
    try:
        resultat = fonction()
    except ValueError as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    except Exception:
        logger.exception(_i18n_t("engine/views_statistiques.py:g:101:26"))
        return JsonResponse({"erreur": _i18n_t("engine/views_statistiques.py:g:101:40")}, status=500)
    resultat["refusees"] = list(refusees)
    return JsonResponse(resultat, encoder=_Encodeur)


def calculer_descriptives(tables, refs, forces=None, echantillon=None, par=None):
    """Calcul de la page Statistiques descriptives : UNE seule implémentation, appelée par l'API de la page ET par le rapport général
    (qui reprend le résultat tel quel, sans rien recalculer de son côté — même principe que `calculer_anova`)."""
    memo = donnees.memoire(tables, echantillon, colonnes=refs + ([par] if par else []))   # lignes à valeur vide rares : retirées, table de calcul par table de calcul
    return donnees.descriptives(memo, refs, forces, par)


@login_required
@ensure_csrf_cookie
def vue_statistiques(request, table_id):
    table, lecture_seule, entreprise, refus = _acces(request, table_id, vue=VUE)
    if refus:
        return refus
    fiches = _fiches_pour(request, entreprise, donnees.tables_disponibles(entreprise))
    courante = donnees.decrire(table_generale(table))
    if courante and not any(f["id"] == courante["id"] for f in fiches):   # table partagée par une autre entreprise : analysable, jamais listée avec les autres
        fiches.insert(0, courante)
    return render(request, "engine/statistiques.html", {
        "entreprise": entreprise, "table": table, "lecture_seule": lecture_seule, "tables_disponibles": fiches, "table_compatible": courante is not None,
        "message_structure": donnees.MESSAGE_STRUCTURE,
        "catalogue": {"tests": statistiques.CATALOGUE_TESTS, "lois": lois.catalogue_lois(), "genres": statistiques.GENRES, "relations": lois.RELATIONS,
                      "echantillons": statistiques.METHODES_ECHANTILLON}})


@login_required
@require_http_methods(["GET"])
def api_stat_descriptives(request, table_id):
    table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True, vue=VUE)
    if refus:
        return refus
    q = request.GET
    tables, refusees = _tables(request, table, q)

    def _faire():
        refs = _liste(q.get("colonnes"))
        if not refs:
            raise ValueError(_i18n_t("engine/views_statistiques.py:g:135:30"))
        return calculer_descriptives(tables, refs, _forces(q.get("types")), _echantillon(q), q.get("par") or None)
    return _reponse(_faire, refusees)


@login_required
@require_http_methods(["GET"])
def api_stat_test(request, table_id):
    table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True, vue=VUE)
    if refus:
        return refus
    q = request.GET
    tables, refusees = _tables(request, table, q)

    def _faire():
        fiche = next((t for t in statistiques.CATALOGUE_TESTS if t["cle"] == q.get("test")), None)
        if fiche is None:
            raise ValueError(_i18n_t("engine/views_statistiques.py:g:153:30"))
        params = {p["cle"]: q.get(p["cle"]) for p in fiche["params"] if q.get(p["cle"]) not in (None, "")}
        refs = {role: q.get(role) or None for role in ("a", "b", "groupe", "groupe2")}
        refs["explicatives"] = _liste(q.get("explicatives"))
        colonnes = [r for k in ("a", "b", "groupe", "groupe2") for r in [refs.get(k)] if r] + list(refs["explicatives"][:12])
        memo = donnees.memoire(tables, _echantillon(q), colonnes=colonnes) if fiche["roles"] else {"classes": [], "homogene": True, "avertissements": [], "echantillon": None}
        return donnees.tester(memo, fiche["cle"], refs, params, _forces(q.get("types")))
    return _reponse(_faire, refusees)


@login_required
@require_http_methods(["GET"])
def api_stat_probabilites(request, table_id):
    table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True, vue=VUE)
    if refus:
        return refus
    q = request.GET
    genre = q.get("type") or "loi"

    def _faire():
        if genre == "loi":
            cle = q.get("loi")
            if cle not in lois.LOIS:
                raise ValueError(_i18n_t("engine/views_statistiques.py:g:175:34"))
            brut = {s["cle"]: q.get("p_" + s["cle"]) for s in lois.LOIS[cle]["params"] if q.get("p_" + s["cle"]) not in (None, "")}
            brut = {k: str(v).replace(",", ".") for k, v in brut.items()}
            return lois.calculer_loi(cle, brut, x=_flottant(q, "x"), a=_flottant(q, "bas"), b=_flottant(q, "haut"), q=_flottant(q, "q", 0.0, 1.0), niveau=_flottant(q, "niveau", 0.5, 0.9999))
        if genre == "evenements":
            return lois.evenements(_flottant(q, "pa"), _flottant(q, "pb"), q.get("relation") or "independants", _flottant(q, "valeur"))
        if genre == "bayes":
            try:
                hyp = json.loads(q.get("hypotheses") or "[]")
            except ValueError:
                raise ValueError(_i18n_t("engine/views_statistiques.py:g:185:34"))
            if not isinstance(hyp, list) or len(str(q.get("hypotheses") or "")) > 6000:
                raise ValueError(_i18n_t("engine/views_statistiques.py:g:187:34"))
            return lois.bayes([h for h in hyp if isinstance(h, dict)])
        if genre == "combinatoire":
            return lois.combinatoire(q.get("n"), q.get("k"))
        if genre == "ajustement":
            ref = q.get("colonne")
            if not ref:
                raise ValueError(_i18n_t("engine/views_statistiques.py:g:194:34"))
            tables, _refusees = _tables(request, table, q)
            memo = donnees.memoire(tables, _echantillon(q), colonnes=[ref])
            return donnees.ajuster_colonne(memo, ref, _forces(q.get("types")), _flottant(q, "x"))
        raise ValueError(_i18n_t("engine/views_statistiques.py:g:198:26"))
    return _reponse(_faire)
