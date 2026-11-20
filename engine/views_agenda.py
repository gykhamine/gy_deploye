"""Agenda (tâches datées pour soi et pour ses clients, avec rappels) — voir engine/agenda.py.

Une seule page, /agenda/, pour l'équipe comme pour les clients (le client ne voit et ne planifie que SES tâches).

  GET  /api/agenda/                    mes tâches (à faire puis closes) + personnes pour qui je peux planifier
  POST /api/agenda/creer/              {titre, echeance (ISO), description?, destinataire_id?}
  POST /api/agenda/<id>/modifier/      mêmes champs (créateur ou administrateur) ; une nouvelle échéance réarme les rappels
  POST /api/agenda/<id>/valider/       {fait: true|false} — le destinataire seul
  POST /api/agenda/<id>/supprimer/     créateur ou administrateur
  GET  /api/agenda/alertes/            rappels d'agenda non lus (cloche, bannière du client)
  POST /api/agenda/alertes/lire/       les marque lus
Chaque GET /api/agenda/ et /alertes/ est la « connexion » du membre : elle émet les rappels dus (agenda.emettre).
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from engine.i18n import langue_courante, t

from . import agenda as ag
from . import messagerie as mg
from .models import TacheAgenda
from .ratelimit import limiter
from .views_messagerie import _erreur, _membre


@login_required
@ensure_csrf_cookie
def page_agenda(request):
    m = _membre(request)
    return render(request, "engine/agenda.html", {"entreprise": m.entreprise, "est_equipe": mg.est_equipe(m), "langue": langue_courante()})


def _corps(request):
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return {}
    return corps if isinstance(corps, dict) else {}


def _echeance(valeur):
    """datetime aware depuis une chaîne ISO (le navigateur envoie un instant UTC) ; None si illisible."""
    if not isinstance(valeur, str):
        return None
    try:
        d = parse_datetime(valeur.strip())
    except ValueError:
        return None
    if d is None:
        return None
    return timezone.make_aware(d) if timezone.is_naive(d) else d


def _champs(corps, m, tache=None):
    """(valeurs validées, erreur) : titre, description, échéance, destinataire — seuls les champs fournis changent à la modification."""
    v = {}
    if tache is None or "titre" in corps:
        titre = str(corps.get("titre") or "").strip()[:200]
        if not titre:
            return None, t("engine/views_agenda.py:67:28")
        v["titre"] = titre
    if "description" in corps:
        v["description"] = str(corps.get("description") or "").strip()[:2000]
    if tache is None or "echeance" in corps:
        echeance = _echeance(corps.get("echeance"))
        if echeance is None:
            return None, t("engine/views_agenda.py:74:28")
        v["echeance"] = echeance
    if tache is None or "destinataire_id" in corps:
        voulu = corps.get("destinataire_id") or m.id
        destinataire = next((c for c in ag.cibles(m) if str(c.id) == str(voulu)), None)
        if destinataire is None:
            return None, t("engine/views_agenda.py:80:28")
        v["destinataire"] = destinataire
    return v, None


@login_required
@require_http_methods(["GET"])
def api_agenda(request):
    m = _membre(request)
    ag.emettre(m.entreprise, m)
    noms = mg.noms(ag.cibles(m))
    return JsonResponse({"moi": {"id": m.id, "nom": noms[m.id]}, "taches": ag.lister(m), "equipe": mg.est_equipe(m),
                         "cibles": [{"id": c.id, "nom": noms[c.id], "moi": c.id == m.id} for c in ag.cibles(m)]})


@login_required
@require_http_methods(["POST"])
@limiter("agenda_ecriture", max_appels=120, periode_secondes=60, methodes={"POST"})
def api_agenda_creer(request):
    m = _membre(request)
    v, erreur = _champs(_corps(request), m)
    if erreur:
        return _erreur(erreur)
    tache = TacheAgenda.objects.create(entreprise_id=m.entreprise_id, createur=m, **v)
    ag.emettre(m.entreprise, m)     # une échéance déjà passée est annoncée tout de suite
    return JsonResponse({"tache": ag.serialiser(tache, m, mg.noms([m, tache.destinataire]), timezone.now())})


def _tache(request, tache_id):
    m = _membre(request)
    return m, get_object_or_404(TacheAgenda.objects.select_related("destinataire__utilisateur", "createur__utilisateur"),
                                id=tache_id, entreprise_id=m.entreprise_id)


@login_required
@require_http_methods(["POST"])
@limiter("agenda_ecriture", max_appels=120, periode_secondes=60, methodes={"POST"})
def api_agenda_modifier(request, tache_id):
    m, tache = _tache(request, tache_id)
    if not ag.peut_modifier(m, tache):
        return _erreur(t("engine/views_agenda.py:120:26"), 403)
    v, erreur = _champs(_corps(request), m, tache)
    if erreur:
        return _erreur(erreur)
    nouvelle = v.pop("echeance", None)
    for champ, valeur in v.items():
        setattr(tache, champ, valeur)
    tache.save(update_fields=list(v))
    if "destinataire" in v or (nouvelle is not None and nouvelle != tache.echeance):
        ag.reprogrammer(tache, nouvelle or tache.echeance)      # autre date ou autre destinataire : les rappels repartent à zéro
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_agenda_valider(request, tache_id):
    m, tache = _tache(request, tache_id)
    if ag.valider(m, tache, bool(_corps(request).get("fait"))) is None:
        return _erreur(t("engine/views_agenda.py:138:26"), 403)
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_agenda_supprimer(request, tache_id):
    m, tache = _tache(request, tache_id)
    if not ag.peut_modifier(m, tache):
        return _erreur(t("engine/views_agenda.py:147:26"), 403)
    tache.delete()
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["GET"])
def api_agenda_alertes(request):
    m = _membre(request)
    ag.emettre(m.entreprise, m)
    liste, total = ag.alertes(m)
    return JsonResponse({"alertes": liste, "non_lues": total})


@login_required
@require_http_methods(["POST"])
def api_agenda_alertes_lire(request):
    return JsonResponse({"lues": ag.marquer_alertes_lues(_membre(request))})
