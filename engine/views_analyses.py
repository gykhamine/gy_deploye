"""ANOVA ET ANALYSES FACTORIELLES : deux pages dans le châssis du dashboard BI (même modèle que Statistiques) + une API chacune.
Tout est automatique : la personne coche des colonnes, le type de chaque variable est reconnu (statistiques.classer, corrigeable) et engine/analyses_auto.py
choisit, justifie et exécute la méthode. Aucune formule à écrire. Rien n'est enregistré (lecture seule).

  GET /tables/<id>/anova/                                       page ANOVA
  GET /api/tables/<id>/anova/analyse/                           colonnes=<id>:<slug>,... (numériques = variables étudiées, qualitatives = facteurs), alpha=0.05,
                                                                types=<id>:<slug>:<genre>,..., tables=<id,id>, echantillon=… (comme Statistiques)
  GET /tables/<id>/analyse-factorielle/                         page ACP / ACM / AFC
  GET /api/tables/<id>/analyse-factorielle/analyse/             colonnes=…, methode=auto|acp|acm|afc, reduire=1|0, types=…, tables=…, echantillon=…

Mêmes droits que les autres analyses (views_geo._acces ; vues « anova » et « analyse_factorielle » de VUES_DISPONIBLES, abonnement client si prix fixé).
"""
from engine.i18n import t, tf
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import analyses_auto as auto, statistiques, statistiques_donnees as donnees
from .vue_groupe import table_generale
from .views_geo import _acces, _fiches_pour
from .views_statistiques import _echantillon, _flottant, _forces, _liste, _reponse, _tables

VUE_ANOVA, VUE_FACTORIELLE = "anova", "analyse_factorielle"
MAX_COLONNES = 60


def _page(request, table_id, vue, gabarit, extra=None):
    table, lecture_seule, entreprise, refus = _acces(request, table_id, vue=vue)
    if refus:
        return refus
    fiches = _fiches_pour(request, entreprise, donnees.tables_disponibles(entreprise))
    courante = donnees.decrire(table_generale(table))
    if courante and not any(f["id"] == courante["id"] for f in fiches):   # table partagée par une autre entreprise : analysable, jamais listée avec les autres
        fiches.insert(0, courante)
    return render(request, gabarit, {"entreprise": entreprise, "table": table, "lecture_seule": lecture_seule, "tables_disponibles": fiches, "table_compatible": courante is not None,
                                     "message_structure": donnees.MESSAGE_STRUCTURE, "catalogue": {"genres": statistiques.GENRES, **(extra or {})}})


def _charger(memo, refs, forces):
    """([variables lisibles], [colonnes écartées avec la raison]) : une colonne inutilisable (vide, identifiant, dates) est signalée sans bloquer les autres."""
    variables, ignorees = [], []
    for ref in refs[:MAX_COLONNES]:
        try:
            v = donnees.variable(memo, ref, forces)
        except ValueError as e:
            ignorees.append({"colonne": ref, "raison": str(e)})
            continue
        if v["genre"] == "date":
            ignorees.append({"colonne": v["nom"], "raison": t("engine/views_analyses.py:g:49:61")})
        else:
            variables.append(v)
    return variables, ignorees


def _resume(memo, ignorees, refusees):
    return {**donnees.resume_memoire(memo), "ignorees": ignorees, "refusees": list(refusees)}


@login_required
@ensure_csrf_cookie
def vue_anova(request, table_id):
    return _page(request, table_id, VUE_ANOVA, "engine/anova.html")


@login_required
@ensure_csrf_cookie
def vue_analyse_factorielle(request, table_id):
    return _page(request, table_id, VUE_FACTORIELLE, "engine/analyse_factorielle.html", {"methodes": {"auto": "Automatique", "acp": "ACP", "acm": "ACM", "afc": "AFC"}})


def calculer_anova(tables, refs, forces=None, alpha=0.05, echantillon=None, refusees=()):
    """Calcul de la page ANOVA : UNE seule implémentation, appelée par l'API de la page ET par le rapport général (qui reprend la page telle quelle)."""
    if len(refs) < 2:
        raise ValueError(t("engine/views_analyses.py:g:83:30"))
    memo = donnees.memoire(tables, echantillon)
    variables, ignorees = _charger(memo, refs, forces or {})
    reponses = [v for v in variables if v["genre"].startswith("quanti")]
    facteurs = [v for v in variables if v["genre"].startswith("quali")]
    if not reponses:
        raise ValueError(t("engine/views_analyses.py:g:90:30"))
    if not facteurs:
        raise ValueError(t("engine/views_analyses.py:g:92:30"))
    analyses, echecs = [], []
    for r in reponses[:12]:
        memes = [f for f in facteurs if f["classe"] == r["classe"]]    # lignes alignées : même table de calcul
        if not memes:
            echecs.append({"reponse": r["nom"], "raison": t("engine/views_analyses.py:g:97:63")})
            continue
        try:
            analyses.append(auto.anova_auto(r, memes, alpha))
        except ValueError as e:
            echecs.append({"reponse": r["nom"], "raison": str(e)})
    if not analyses:
        raise ValueError(echecs[0]["raison"] if echecs else t("engine/views_analyses.py:g:104:65"))
    return {"analyses": analyses, "echecs": echecs, **_resume(memo, ignorees, refusees)}


@login_required
@require_http_methods(["GET"])
def api_anova_analyse(request, table_id):
    table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True, vue=VUE_ANOVA)
    if refus:
        return refus
    q = request.GET
    tables, refusees = _tables(request, table, q, VUE_ANOVA)

    return _reponse(lambda: calculer_anova(tables, _liste(q.get("colonnes")), _forces(q.get("types")), _flottant(q, "alpha", 0.0001, 0.5) or 0.05, _echantillon(q), refusees), refusees)


def calculer_factorielle(tables, refs, forces=None, methode="auto", reduire=True, echantillon=None, refusees=()):
    """Calcul de la page Analyse factorielle : UNE seule implémentation, appelée par l'API de la page ET par le rapport général."""
    if len(refs) < 2:
        raise ValueError(t("engine/views_analyses.py:g:121:30"))
    methode = methode if methode in ("acp", "acm", "afc") else "auto"
    memo = donnees.memoire(tables, echantillon)
    variables, ignorees = _charger(memo, refs, forces or {})
    if len({v["classe"] for v in variables}) > 1:
        raise ValueError(t("engine/views_analyses.py:g:126:30"))
    quanti = [v for v in variables if v["genre"].startswith("quanti")]
    quali = [v for v in variables if v["genre"].startswith("quali")]
    if methode == "auto":
        if len(quanti) >= 2:
            methode = "acp"
        elif not quanti and len(quali) == 2:
            methode = "afc"
        elif not quanti and len(quali) >= 3:
            methode = "acm"
        else:
            raise ValueError(t("engine/views_analyses.py:g:137:34"))
        pourquoi = {"acp": tf("engine/views_analyses.py:g:138:32", v1=len(quanti)) + (tf("engine/views_analyses.py:g:138:116", v1=len(quali)) if quali else "."),
                    "afc": t("engine/views_analyses.py:g:139:32"),
                    "acm": tf("engine/views_analyses.py:g:140:32", v1=len(quali))}[methode]
    else:
        pourquoi = t("engine/views_analyses.py:g:142:24")
    notes = []
    if methode == "acp":
        analyse = auto.acp(quanti, quali, reduire)
    elif methode == "acm":
        if quanti:
            notes.append(tf("engine/views_analyses.py:g:148:30", v1=len(quanti)))
        analyse = auto.acm(quali)
    else:
        if len(quali) < 2:
            raise ValueError(t("engine/views_analyses.py:g:152:34"))
        if len(quali) > 2 or quanti:
            notes.append(t("engine/views_analyses.py:g:154:30"))
        analyse = auto.afc(quali[0], quali[1])
    analyse["avertissements"] = notes + analyse.get("avertissements", [])
    return {"methode": methode, "pourquoi": pourquoi, "analyse": analyse, **_resume(memo, ignorees, refusees)}


@login_required
@require_http_methods(["GET"])
def api_factorielle_analyse(request, table_id):
    table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True, vue=VUE_FACTORIELLE)
    if refus:
        return refus
    q = request.GET
    tables, refusees = _tables(request, table, q, VUE_FACTORIELLE)

    return _reponse(lambda: calculer_factorielle(tables, _liste(q.get("colonnes")), _forces(q.get("types")), q.get("methode"), q.get("reduire") != "0", _echantillon(q), refusees), refusees)
