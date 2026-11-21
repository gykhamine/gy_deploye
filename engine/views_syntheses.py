"""SYNTHÈSES RÉDIGÉES : page éditeur + API, liées à UNE table. Même principe que les documents administratifs (views/_part_012.py) : la personne rédige,
avec ce que le moteur a déjà calculé pour la table (cartes, tableaux et graphiques des sections du rapport général) — rien n'est recalculé ni réécrit.

  GET    /tables/<id>/syntheses/                                         la page de l'éditeur
  GET    /api/tables/<id>/syntheses/                                     liste (q, statut, archive, limite) ; POST {titre, corps, modele_entete} : crée un brouillon
  GET    /api/tables/<id>/syntheses/<sid>/                               lecture ; PUT {titre, corps, modele_entete} : enregistre (brouillon) ; DELETE : supprime
  GET    /api/tables/<id>/syntheses/<sid>/pdf/                           brouillon : épreuve filigranée ; finalisée : le PDF FIGÉ (empreinte recontrôlée) ; ?apercu=1 : inline
  POST   /api/tables/<id>/syntheses/<sid>/finaliser/                     PDF définitif généré une fois, figé + SHA-256
  POST   /api/tables/<id>/syntheses/<sid>/rouvrir/                       retour en brouillon
  POST   /api/tables/<id>/syntheses/<sid>/archiver/                      {archive: true|false}
  GET    /api/tables/<id>/syntheses/catalogue/                           sans paramètre : les types d'analyse ; ?section=<id> : les cartes de cette analyse
                                                                          (titres, éléments tableau / graphique / texte, aperçus) ; ?rafraichir=1 : recalcul
  GET    /api/tables/<id>/syntheses/catalogue/image/                     ?section=&carte=&element= : l'image d'un graphique de l'analyse (PNG)
  GET    /api/tables/<id>/syntheses/entetes/                             modèles d'entête de l'entreprise (choix de l'entête du PDF)

Droits : vue « syntheses » de VUES_DISPONIBLES (l'agent doit l'avoir) ; administrateur et agents seulement (jamais un client) ; l'administrateur voit toutes les
synthèses de l'entreprise, un agent les siennes ; une table partagée en lecture seule par une autre entreprise n'a pas de synthèses.
"""
import json
import re

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from engine.i18n import t as _t

from . import emission_documents as _emission
from . import syntheses as _syn
from .models import DocumentFigeError, MembreEntreprise, ModeleEntete, SyntheseRedigee
from .ratelimit import adresse_ip_client
from .views import _entreprise_courante
from .views_geo import _acces
from .vue_groupe import nom_vue, table_generale

VUE = "syntheses"


def _contexte(request, table_id, api=True):
    """(refus, table, entreprise, membre) : table de l'entreprise (le groupe entier si c'est une partie d'une grande table), jamais un client, jamais une
    table partagée en lecture seule."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if membre is None or membre.role not in ("admin", "agent"):      # un client (lecteur) n'a jamais accès : vérifié AVANT toute lecture de la table
        return (JsonResponse({"erreur": _t("engine/views_syntheses.py:acces_refuse")}, status=403) if api else redirect("/profile/")), None, None, None
    table, lecture_seule, entreprise, refus = _acces(request, table_id, api=api, vue=VUE)
    if refus:
        return refus, None, None, None
    if lecture_seule:
        return (JsonResponse({"erreur": _t("engine/views_syntheses.py:acces_refuse")}, status=403) if api else redirect("/profile/")), None, None, None
    return None, table_generale(table), entreprise, membre


def _saisie(request):
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return None
    return corps if isinstance(corps, dict) else {}


def _erreur(exc):
    statut = {"interdit": 403, "statut": 409, "vide": 400, "entete": 400}.get(exc.code, 400)
    return JsonResponse({"erreur": str(exc), "code": exc.code}, status=statut)


def _synthese(entreprise, membre, table, synthese_id):
    return _syn.visibles(entreprise, membre).filter(id=synthese_id, table=table).select_related("auteur__utilisateur", "table", "entreprise").first()


def _introuvable():
    return JsonResponse({"erreur": _t("engine/views_syntheses.py:introuvable")}, status=404)


def _ip(request):
    return adresse_ip_client(request)


# ══════════════════════════════════════════════════════════════
#  PAGE
# ══════════════════════════════════════════════════════════════
@login_required
@ensure_csrf_cookie
@require_http_methods(["GET"])
def vue_syntheses(request, table_id):
    refus, table, entreprise, membre = _contexte(request, table_id, api=False)
    if refus:
        return refus
    return render(request, "engine/syntheses.html", {"entreprise": entreprise, "table": table, "nom_table": nom_vue(table), "est_admin": membre.role == "admin"})


# ══════════════════════════════════════════════════════════════
#  SYNTHÈSES : liste, création, lecture, enregistrement, suppression
# ══════════════════════════════════════════════════════════════
@login_required
@require_http_methods(["GET", "POST"])
def api_syntheses(request, table_id):
    refus, table, entreprise, membre = _contexte(request, table_id)
    if refus:
        return refus
    if request.method == "GET":
        visibles = _syn.filtrer(_syn.visibles(entreprise, membre).filter(table=table), request.GET)
        lignes = visibles.select_related("auteur__utilisateur")[:_syn.limite(request.GET)]
        return JsonResponse({"syntheses": [_syn.serialiser(s, complet=False, membre=membre) for s in lignes], "total": visibles.count()})
    saisie = _saisie(request)
    if saisie is None:
        return JsonResponse({"erreur": _t("engine/views_syntheses.py:corps_invalide")}, status=400)
    s = SyntheseRedigee(entreprise=entreprise, table=table, auteur=membre)
    try:
        _syn.appliquer_saisie(s, saisie)
    except _syn.ErreurSynthese as exc:
        return _erreur(exc)
    s.save()
    _syn.journaliser(s, membre, "creer", ip=_ip(request), details={"table": table.id})
    return JsonResponse(_syn.serialiser(s, membre=membre), status=201)


@login_required
@require_http_methods(["GET", "PUT", "DELETE"])
def api_synthese(request, table_id, synthese_id):
    refus, table, entreprise, membre = _contexte(request, table_id)
    if refus:
        return refus
    s = _synthese(entreprise, membre, table, synthese_id)
    if s is None:
        return _introuvable()
    if request.method in ("PUT", "DELETE"):
        if not _syn.peut_agir(s, membre):
            return JsonResponse({"erreur": _t("engine/syntheses.py:interdit"), "code": "interdit"}, status=403)
        if s.est_figee:
            return JsonResponse({"erreur": _t("engine/views_syntheses.py:figee"), "code": "figee"}, status=409)
    if request.method == "DELETE":
        _syn.journaliser(s, membre, "supprimer", ip=_ip(request), details={"titre": s.titre, "table": s.table_id})
        s.delete()
        return JsonResponse({"ok": True})
    if request.method == "PUT":
        saisie = _saisie(request)
        if saisie is None:
            return JsonResponse({"erreur": _t("engine/views_syntheses.py:corps_invalide")}, status=400)
        try:
            maj = _syn.appliquer_saisie(s, saisie)
        except _syn.ErreurSynthese as exc:
            return _erreur(exc)
        if maj:
            try:
                s.save(update_fields=maj + ["modifie_le"])
            except DocumentFigeError:
                return JsonResponse({"erreur": _t("engine/views_syntheses.py:figee"), "code": "figee"}, status=409)
    return JsonResponse(_syn.serialiser(s, membre=membre))


def _nom_fichier_pdf(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", s.titre or "").strip("-")[:60] or f"synthese-{s.pk}"


@login_required
@require_http_methods(["GET"])
def api_synthese_pdf(request, table_id, synthese_id):
    """Brouillon : épreuve filigranée, générée à la demande avec les données ACTUELLES. Finalisée : le PDF figé, octet pour octet, après contrôle de son
    empreinte SHA-256. `?apercu=1` : servi « inline » pour l'aperçu intégré à la page."""
    refus, table, entreprise, membre = _contexte(request, table_id)
    if refus:
        return refus
    s = _synthese(entreprise, membre, table, synthese_id)
    if s is None:
        return _introuvable()
    if s.est_figee:
        try:
            pdf = _syn.octets_figes(s)
        except _emission.ErreurEmission as exc:
            _syn.journaliser(s, membre, "integrite_echec", ip=_ip(request), resultat="echec")
            return JsonResponse({"erreur": str(exc), "code": exc.code}, status=500)
    else:
        from .reports import generer_pdf_synthese
        try:
            pdf = generer_pdf_synthese(s)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("synthese: PDF impossible (id=%s)", s.pk)
            return JsonResponse({"erreur": _t("engine/views_syntheses.py:pdf_impossible")}, status=500)
    apercu = request.GET.get("apercu") == "1"
    if s.est_figee or not apercu:        # l'épreuve d'un brouillon vue à l'écran n'est pas une sortie de document
        _syn.journaliser(s, membre, "telecharger", ip=_ip(request), details={"statut": s.statut, "sha256": s.empreinte_sha256})
    reponse = HttpResponse(pdf, content_type="application/pdf")
    reponse["Content-Disposition"] = f'{"inline" if apercu else "attachment"}; filename="{_nom_fichier_pdf(s)}.pdf"'
    return reponse


def _action(request, table_id, synthese_id, fonction, **options):
    refus, table, entreprise, membre = _contexte(request, table_id)
    if refus:
        return refus
    s = _synthese(entreprise, membre, table, synthese_id)
    if s is None:
        return _introuvable()
    try:
        resultat = fonction(s, membre, ip=_ip(request), **options)
    except _syn.ErreurSynthese as exc:
        return _erreur(exc)
    except _emission.ErreurEmission as exc:
        return JsonResponse({"erreur": str(exc), "code": exc.code}, status=500)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("synthese: action impossible (id=%s)", s.pk)
        return JsonResponse({"erreur": _t("engine/views_syntheses.py:action_impossible")}, status=500)
    return JsonResponse(_syn.serialiser(resultat, membre=membre))


@login_required
@require_http_methods(["POST"])
def api_synthese_finaliser(request, table_id, synthese_id):
    return _action(request, table_id, synthese_id, _syn.finaliser)


@login_required
@require_http_methods(["POST"])
def api_synthese_rouvrir(request, table_id, synthese_id):
    return _action(request, table_id, synthese_id, _syn.rouvrir)


@login_required
@require_http_methods(["POST"])
def api_synthese_archiver(request, table_id, synthese_id):
    saisie = _saisie(request) or {}
    return _action(request, table_id, synthese_id, _syn.archiver, archive=saisie.get("archive", True) is not False)


# ══════════════════════════════════════════════════════════════
#  CATALOGUE DES ANALYSES (ce que le moteur a déjà calculé pour la table)
# ══════════════════════════════════════════════════════════════
@login_required
@require_http_methods(["GET"])
def api_syntheses_catalogue(request, table_id):
    refus, table, _entreprise, _membre = _contexte(request, table_id)
    if refus:
        return refus
    from .reports import catalogue_cartes, catalogue_sections
    section = (request.GET.get("section") or "").strip()
    if not section:
        return JsonResponse({"sections": catalogue_sections()})
    try:
        donnees = catalogue_cartes(table, section, forcer=request.GET.get("rafraichir") == "1")
    except ValueError:
        return JsonResponse({"erreur": _t("engine/views_syntheses.py:section_inconnue")}, status=400)
    return JsonResponse({"section": section, **donnees})


@login_required
@require_http_methods(["GET"])
def api_syntheses_catalogue_image(request, table_id):
    refus, table, _entreprise, _membre = _contexte(request, table_id)
    if refus:
        return refus
    from .reports import image_element
    try:
        octets = image_element(table, (request.GET.get("section") or "").strip(), request.GET.get("carte") or "", request.GET.get("element") or "")
    except ValueError:
        octets = None
    if not octets:
        raise Http404
    reponse = HttpResponse(octets, content_type="image/png")
    reponse["Cache-Control"] = "private, max-age=300"
    return reponse


@login_required
@require_http_methods(["GET"])
def api_syntheses_entetes(request, table_id):
    """Modèles d'entête de l'entreprise (lecture pour tous les rôles ; la gestion reste sur la page des documents administratifs)."""
    refus, _table, entreprise, _membre = _contexte(request, table_id)
    if refus:
        return refus
    return JsonResponse({"entetes": [{"id": m.id, "nom": m.nom, "par_defaut": m.par_defaut} for m in ModeleEntete.objects.filter(entreprise=entreprise)]})
