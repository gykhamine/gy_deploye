"""Test d'utilisabilité statistique (2026-09-23).

Collecte (static/engine/js/usabilite.js, injecté par UsabiliteMiddleware dans toute page d'un utilisateur
connecté) : temps passé par page, actions (réussite/échec, durée), erreurs serveur et de page, clics
répétés (frustration), tâches chronométrées (window.gyUsabilite.tache). JAMAIS le contenu saisi, les
valeurs des champs ni les données métier : seulement chemins (identifiants remplacés par « :id »),
types d'évènement, durées et codes de réussite.

Questionnaire SUS (System Usability Scale, Brooke 1996 — 10 affirmations, échelle 1 à 5, score 0–100),
proposé après 3 jours d'utilisation distincts, au plus une fois tous les 90 jours, ou à la demande de
l'administrateur (« campagne »).

Statistiques (page /usabilite/, administrateur) : moyenne, médiane, écart-type et intervalle de
confiance à 95 % (loi de Student) du score SUS, note A+…F (échelle de Sauro-Lewis), qualificatif
(Bangor), rang centile face à la moyenne de référence 68 ; taux de réussite des actions avec intervalle
de Wilson ; médiane et 90e centile des durées ; erreurs et clics répétés pour 100 visites de page.
Export CSV des mesures brutes pour une analyse externe (R, Excel, SPSS).
"""
from engine.i18n import td
from engine.i18n import tl
from engine.i18n import t
import csv
import json
import math
import statistics
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import EvaluationSUS, MembreEntreprise, MesureUsage
from .ratelimit import limiter

SUS_QUESTIONS = [
    tl("engine/usabilite.py:g:37:5"),
    tl("engine/usabilite.py:g:38:5"),
    tl("engine/usabilite.py:g:39:5"),
    tl("engine/usabilite.py:g:40:5"),
    tl("engine/usabilite.py:g:41:5"),
    tl("engine/usabilite.py:g:42:5"),
    tl("engine/usabilite.py:g:43:5"),
    tl("engine/usabilite.py:g:44:5"),
    tl("engine/usabilite.py:g:45:5"),
    tl("engine/usabilite.py:g:46:5"),
]
_TYPES = {t for t, _ in MesureUsage.TYPE_CHOICES}
_CONSERVATION_JOURS = 400
_T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
        12: 2.18, 15: 2.13, 20: 2.09, 25: 2.06, 30: 2.04}


# ── Statistiques ──────────────────────────────────────────────────────
def score_sus(reponses):
    """Items impairs : r − 1 ; pairs : 5 − r ; somme × 2,5."""
    return sum((r - 1) if i % 2 == 0 else (5 - r) for i, r in enumerate(reponses)) * 2.5


def _t95(ddl):
    if ddl > 30:
        return 1.96
    return _T95.get(ddl) or _T95[min(k for k in _T95 if k >= ddl)]


def resume_numerique(valeurs):
    """n, moyenne, IC 95 % (Student), médiane, écart-type, 90e centile."""
    n = len(valeurs)
    if not n:
        return None
    moy = statistics.fmean(valeurs)
    et = statistics.stdev(valeurs) if n > 1 else 0.0
    marge = _t95(n - 1) * et / math.sqrt(n) if n > 1 else None
    triees = sorted(valeurs)
    p90 = triees[min(n - 1, math.ceil(0.9 * n) - 1)]
    return {"n": n, "moyenne": moy, "ic_bas": moy - marge if marge is not None else None,
            "ic_haut": moy + marge if marge is not None else None,
            "mediane": statistics.median(valeurs), "ecart_type": et, "p90": p90}


def wilson(succes, n, z=1.96):
    """Intervalle de Wilson à 95 % d'une proportion (fiable même pour de petits échantillons)."""
    if not n:
        return None
    p = succes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    marge = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"taux": p, "bas": max(0.0, centre - marge), "haut": min(1.0, centre + marge)}


def note_sus(score):
    for seuil, note in ((84.1, "A+"), (80.8, "A"), (78.9, "A-"), (77.2, "B+"), (74.1, "B"), (72.6, "B-"),
                        (71.1, "C+"), (65.0, "C"), (62.7, "C-"), (51.7, "D")):
        if score >= seuil:
            return note
    return "F"


def qualificatif_sus(score):
    for seuil, mot in ((85.5, "Excellent"), (72.6, "Bon"), (52.0, "Correct"), (39.2, t("engine/usabilite.py:g:100:86"))):
        if score >= seuil:
            return mot
    return "Mauvais"


def centile_sus(score):
    """Rang centile face à la référence (moyenne 68, écart-type 12,5 — Sauro, 500 études)."""
    return round(100 * 0.5 * (1 + math.erf((score - 68) / (12.5 * math.sqrt(2)))))


# ── Collecte ──────────────────────────────────────────────────────────
def _membre(request):
    from .securite import membre_de_requete
    return membre_de_requete(request, MembreEntreprise.objects.select_related("entreprise"))


@login_required
@require_http_methods(["POST"])
@limiter("usabilite", max_appels=60, periode_secondes=60)
def api_evenements(request):
    membre = _membre(request)
    if membre is None:
        return JsonResponse({"ok": False}, status=403)
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/usabilite.py:125:41")}, status=400)
    session = str(corps.get("session", ""))[:32]
    objets = []
    for e in (corps.get("evenements") or [])[:100]:
        if not isinstance(e, dict) or e.get("type") not in _TYPES:
            continue
        duree = e.get("duree_ms")
        duree = int(duree) if isinstance(duree, (int, float)) and 0 <= duree < 86_400_000 else None
        succes = e.get("succes") if isinstance(e.get("succes"), bool) else None
        objets.append(MesureUsage(
            entreprise=membre.entreprise, utilisateur=request.user, role=membre.role, session=session,
            type_mesure=e["type"], page=str(e.get("page", ""))[:200], cible=str(e.get("cible", ""))[:200],
            succes=succes, duree_ms=duree))
    MesureUsage.objects.bulk_create(objets)
    return JsonResponse({"ok": True, "enregistres": len(objets)})


def _cle_campagne(entreprise_id):
    return f"usabilite:campagne_sus:{entreprise_id}"


@login_required
@require_http_methods(["GET", "POST"])
@limiter("usabilite_sus", max_appels=20, periode_secondes=60, methodes={"POST"})
def api_sus(request):
    membre = _membre(request)
    if membre is None:
        return JsonResponse({"demander": False})
    if request.method == "GET":
        derniere = EvaluationSUS.objects.filter(utilisateur=request.user, entreprise=membre.entreprise) \
            .order_by("-cree_le").values_list("cree_le", flat=True).first()
        campagne = cache.get(_cle_campagne(membre.entreprise_id))
        if campagne and (derniere is None or derniere.timestamp() < campagne):
            demander = True
        elif derniere is not None and derniere > timezone.now() - timedelta(days=90):
            demander = False
        else:
            jours = MesureUsage.objects.filter(utilisateur=request.user, type_mesure="page") \
                .dates("cree_le", "day").count()
            demander = jours >= 3
        return JsonResponse({"demander": demander, "questions": SUS_QUESTIONS if demander else []})
    try:
        corps = json.loads(request.body or "{}")
        reponses = [int(r) for r in corps.get("reponses", [])]
    except (ValueError, TypeError):
        return JsonResponse({"erreur": t("engine/usabilite.py:170:41")}, status=400)
    if len(reponses) != 10 or any(r < 1 or r > 5 for r in reponses):
        return JsonResponse({"erreur": t("engine/usabilite.py:172:41")}, status=400)
    score = score_sus(reponses)
    EvaluationSUS.objects.create(entreprise=membre.entreprise, utilisateur=request.user, role=membre.role,
                                 reponses=reponses, score=score,
                                 commentaire=str(corps.get("commentaire", ""))[:2000])
    return JsonResponse({"ok": True, "score": score})


# ── Tableau de statistiques (administrateur) ──────────────────────────
def _admin(request):
    membre = _membre(request)
    from .delegation import a_pouvoir
    return membre if a_pouvoir(membre, "usabilite") else None


def _statistiques(entreprise, jours):
    depuis = timezone.now() - timedelta(days=jours)
    mesures = MesureUsage.objects.filter(entreprise=entreprise, cree_le__gte=depuis)
    evals = list(EvaluationSUS.objects.filter(entreprise=entreprise, cree_le__gte=depuis))

    sus = resume_numerique([e.score for e in evals])
    if sus:
        sus.update(note=note_sus(sus["moyenne"]), qualificatif=qualificatif_sus(sus["moyenne"]),
                   centile=centile_sus(sus["moyenne"]))
    par_role = []
    for role in sorted({e.role for e in evals}):
        r = resume_numerique([e.score for e in evals if e.role == role])
        par_role.append({"role": {"admin": "Administrateur", "agent": t("engine/usabilite.py:g:199:71"), "lecteur": t("engine/usabilite.py:g:199:91")}.get(role, role), **r})
    # Moyenne par affirmation (1–5) : repère les points faibles précis
    items = []
    if evals:
        for i, q in enumerate(SUS_QUESTIONS):
            moy = statistics.fmean(e.reponses[i] for e in evals)
            items.append({"n": i + 1, "question": q, "moyenne": moy, "positive": i % 2 == 0,
                          "alerte": (moy < 3.5) if i % 2 == 0 else (moy > 2.5)})

    pages = {}
    for page, duree in mesures.filter(type_mesure="page").values_list("page", "duree_ms"):
        pages.setdefault(page, {"durees": [], "erreurs": 0, "rageurs": 0})["durees"].append((duree or 0) / 1000)
    for page, type_m in mesures.filter(type_mesure__in=["erreur_js", "erreur_api", "clic_rageur"]).values_list("page", "type_mesure"):
        p = pages.setdefault(page, {"durees": [], "erreurs": 0, "rageurs": 0})
        p["rageurs" if type_m == "clic_rageur" else "erreurs"] += 1
    lignes_pages = []
    for page, p in pages.items():
        visites = len(p["durees"])
        r = resume_numerique(p["durees"]) if visites else None
        lignes_pages.append({"page": page or "/", "visites": visites,
                             "mediane": r["mediane"] if r else None, "p90": r["p90"] if r else None,
                             "erreurs_100": 100 * p["erreurs"] / visites if visites else None,
                             "rageurs_100": 100 * p["rageurs"] / visites if visites else None,
                             "erreurs": p["erreurs"], "rageurs": p["rageurs"]})
    lignes_pages.sort(key=lambda x: -x["visites"])

    actions = {}
    for type_m, cible, succes, duree in mesures.filter(type_mesure__in=["action", "tache"]) \
            .values_list("type_mesure", "cible", "succes", "duree_ms"):
        a = actions.setdefault((type_m, cible), {"ok": 0, "n": 0, "durees": []})
        a["n"] += 1
        a["ok"] += 1 if succes else 0
        if duree is not None:
            a["durees"].append(duree / 1000)
    lignes_actions = []
    for (type_m, cible), a in actions.items():
        w = wilson(a["ok"], a["n"])
        lignes_actions.append({"type": td("engine/usabilite.py:g:237:40") if type_m == "tache" else "Action", "cible": cible, "n": a["n"],
                               "taux": w["taux"], "bas": w["bas"], "haut": w["haut"],
                               "mediane": statistics.median(a["durees"]) if a["durees"] else None,
                               "alerte": w["taux"] < 0.9})
    lignes_actions.sort(key=lambda x: (x["taux"], -x["n"]))

    return {
        "jours": jours, "sus": sus, "par_role": par_role, "items": items, "evaluations": len(evals),
        "commentaires": [e.commentaire for e in sorted(evals, key=lambda e: e.cree_le, reverse=True) if e.commentaire][:20],
        "pages": lignes_pages[:40], "actions": lignes_actions[:60],
        "utilisateurs": mesures.exclude(utilisateur=None).values("utilisateur").distinct().count(),
        "sessions": mesures.exclude(session="").values("session").distinct().count(),
    }


@login_required
def vue_usabilite(request):
    membre = _admin(request)
    if membre is None:
        return redirect("/")
    MesureUsage.objects.filter(entreprise=membre.entreprise,
                               cree_le__lt=timezone.now() - timedelta(days=_CONSERVATION_JOURS)).delete()
    try:
        jours = max(1, min(365, int(request.GET.get("jours", 30))))
    except ValueError:
        jours = 30
    if request.method == "POST" and request.POST.get("action") == "campagne":
        cache.set(_cle_campagne(membre.entreprise_id), timezone.now().timestamp(), timeout=30 * 86400)
        return redirect(f"/usabilite/?jours={jours}&campagne=1")
    if request.method == "POST" and request.POST.get("action") == "reinitialiser":
        # Efface tout l'historique de l'entreprise (mesures + réponses SUS) et la campagne en cours : on repart de zéro.
        MesureUsage.objects.filter(entreprise=membre.entreprise).delete()
        EvaluationSUS.objects.filter(entreprise=membre.entreprise).delete()
        cache.delete(_cle_campagne(membre.entreprise_id))
        return redirect(f"/usabilite/?jours={jours}&reinit=1")
    contexte = _statistiques(membre.entreprise, jours)
    contexte.update(entreprise=membre.entreprise, campagne_lancee=request.GET.get("campagne") == "1",
                    reinitialise=request.GET.get("reinit") == "1")
    return render(request, "engine/usabilite.html", contexte)


@login_required
def export_usabilite_csv(request):
    membre = _admin(request)
    if membre is None:
        return HttpResponse(status=403)
    reponse = HttpResponse(content_type="text/csv; charset=utf-8")
    reponse["Content-Disposition"] = 'attachment; filename="usabilite.csv"'
    reponse.write("﻿")  # Excel ouvre l'UTF-8 correctement
    w = csv.writer(reponse, delimiter=";")
    w.writerow(["source", "date", "utilisateur_id", "role", "session", "type", "page", "cible", "succes",
                "duree_ms", "score_sus", "reponses_sus"])
    for m in MesureUsage.objects.filter(entreprise=membre.entreprise).order_by("cree_le").iterator():
        w.writerow(["mesure", m.cree_le.isoformat(), m.utilisateur_id or "", m.role, m.session, m.type_mesure,
                    m.page, m.cible, "" if m.succes is None else int(m.succes), m.duree_ms or "", "", ""])
    for e in EvaluationSUS.objects.filter(entreprise=membre.entreprise).order_by("cree_le"):
        w.writerow(["sus", e.cree_le.isoformat(), e.utilisateur_id or "", e.role, "", "", "", "", "", "",
                    e.score, " ".join(map(str, e.reponses))])
    return reponse
