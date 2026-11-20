"""SÉRIES TEMPORELLES : page dans le châssis du dashboard BI + API d'analyse (calculs : engine/series_temporelles.py, données : engine/series_donnees.py).

  GET  /tables/<id>/series-temporelles/                     la page (la table ouverte est la cible ; d'autres tables peuvent s'y ajouter)
  GET  /api/tables/<id>/series-temporelles/analyse/         analyse complète, lecture seule, rien n'est enregistré. Paramètres facultatifs :
        tables=<id,id>            autres tables analysées avec celle-ci (chacune doit avoir la structure attendue et être accessible)
        temps=<id>:<slug>,...     colonne date choisie pour chaque table (absente = première colonne date de la table)
        colonnes=<id>:<slug>,...  colonnes de valeurs retenues (une table sans sélection = toutes ses colonnes numériques)
        horizon, periode, confiance (0.5–0.999), methode (mco|mm|hw|arima), modele (additif|multiplicatif), agregat (moyenne|somme|dernier)
        explicatives=<id>:<slug>,...  colonnes servant de variables explicatives (leurs lignes à venir, colonne analysée vide, servent aux estimations)
        feries=0|1, ruptures=0|1  jours fériés et ruptures de tendance, jusqu'à 3 (1 par défaut : retenus seulement s'ils sont significatifs)
        pays=FR|BE|LU|DE|AT|CH|ES|PT|IT|GB|US  pays des jours fériés (FR par défaut), ou une région : FR-AM DE-BW DE-BY DE-NW DE-HE DE-BE DE-ST ES-CT ES-MD IT-RM IT-MI
        lunaires=islam,juif,chine,orthodoxe  fêtes à date lunaire ajoutées (calculées, ±1 jour possible)
        glissante=<0–6>           validation glissante sur ce nombre d'origines successives (0 par défaut : plus long)
        demo=1                    séries simulées (aucune donnée lue)
      Réponse : en plus de `tables`, `comparaisons` (liens entre les séries : corrélations, décalage, matrice) et, pour chaque série,
      `exploration` (statistiques descriptives, croissance, atypiques, cycles… : engine/series_extras.py).

Mêmes droits que la carte (engine/views_geo._acces, vue « series_temporelles ») : table propre ou partagée en lecture seule, vue autorisée pour
l'agent ; les clients (lecteurs) y accèdent en lecture seule (tables exposées, abonnement si prix fixé). Une table demandée mais interdite est listée dans `refusees`, jamais analysée.
"""
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import series_donnees, series_temporelles
from .vue_groupe import table_generale
from .views_geo import _fiches_pour, _Encodeur, _acces, _nombre

VUE = "series_temporelles"


def _parametres(q) -> dict:
    """Paramètres d'analyse bornés ; tout ce qui est absent ou invalide retombe sur le choix automatique du moteur."""
    p = {"horizon": _nombre(q, "horizon", int, 1, 120), "periode": _nombre(q, "periode", int, 1, 366),
         "confiance": _nombre(q, "confiance", float, 0.5, 0.999), "glissante": _nombre(q, "glissante", int, 0, 6)}
    p.update({cle: q.get(cle) == "1" for cle in ("feries", "ruptures") if q.get(cle) in ("0", "1")})
    lune = [x for x in (q.get("lunaires") or "").lower().split(",") if x in series_temporelles.LUNAIRES]
    code = "+".join([(q.get("pays") or "FR").upper()] + lune)
    if q.get("feries") != "0" and (q.get("pays") or lune) and series_temporelles.code_feries(code):
        p["feries"] = code
    for cle, permis in (("methode", ("mco", "mm", "hw", "arima")), ("modele", ("additif", "multiplicatif")), ("agregat", ("moyenne", "somme", "dernier"))):
        if q.get(cle) in permis:
            p[cle] = q.get(cle)
    return {k: v for k, v in p.items() if v is not None}


def _entiers(texte) -> list:
    return [int(x) for x in str(texte or "").split(",") if x.strip().isdigit()]


def _selection(texte) -> dict:
    """« 12:ventes,12:cout,15:stock » -> {12: {ventes, cout}, 15: {stock}}."""
    sortie = {}
    for jeton in str(texte or "").split(","):
        tid, _sep, slug = jeton.partition(":")
        if tid.strip().isdigit() and slug.strip():
            sortie.setdefault(int(tid), set()).add(slug.strip())
    return sortie


@login_required
@ensure_csrf_cookie
def vue_series_temporelles(request, table_id):
    table, lecture_seule, entreprise, refus = _acces(request, table_id, vue=VUE)
    if refus:
        return refus
    fiches = _fiches_pour(request, entreprise, series_donnees.tables_compatibles(entreprise))
    courante = series_donnees.decrire(table_generale(table))
    if courante and not any(f["id"] == courante["id"] for f in fiches):   # table partagée par une autre entreprise : analysable, jamais listée avec les autres
        fiches.insert(0, courante)
    return render(request, "engine/series_temporelles.html", {
        "entreprise": entreprise, "table": table, "lecture_seule": lecture_seule, "tables_compatibles": fiches,
        "table_compatible": courante is not None, "message_structure": series_donnees.MESSAGE_STRUCTURE})


@login_required
@require_http_methods(["GET"])
def api_series_analyse(request, table_id):
    table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True, vue=VUE)
    if refus:
        return refus
    q = request.GET
    params = _parametres(q)
    if str(q.get("demo") or "") in ("1", "true", "True"):
        demo = [series_donnees.analyser_demo(**params)]
        return JsonResponse({"tables": demo, "refusees": [], "demo": True, "comparaisons": series_donnees.comparer_resultats(demo)}, encoder=_Encodeur)
    tables, refusees = [table], []
    for tid in _entiers(q.get("tables")):
        if tid == table.id or any(t.id == tid for t in tables):
            continue
        try:
            autre, _ls, _e, refus_autre = _acces(request, tid, api=True, vue=VUE)
        except Http404:
            refusees.append(tid)
            continue
        if refus_autre:
            refusees.append(tid)
        else:
            tables.append(autre)
    resultats = series_donnees.analyser_tables(tables, _selection(q.get("colonnes")), _selection(q.get("explicatives")), _selection(q.get("temps")), **params)
    return JsonResponse({"tables": resultats, "refusees": refusees, "demo": False, "comparaisons": series_donnees.comparer_resultats(resultats)}, encoder=_Encodeur)
