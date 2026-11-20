"""PAGE ET API DES FLUX MÉDIA TEMPS RÉEL (micro, caméra, diffusion) — logique dans flux_media.py.

  GET  /flux/                              page dédiée (liste, création, direct, captures)
  GET/POST /api/flux/                      liste des flux de l'entreprise / création (table à une seule cellule + son champ) ;
                                           la création EXIGE {"table_liee_id": id | "nouvelle"} : la table de données qui reçoit les captures
  GET  /api/flux/tables/                   tables auxquelles un flux peut être lié
  GET/POST /api/flux/<champ_id>/table/     table liée + colonnes du formulaire de la ligne ; POST {"table_id"} (re)lie le flux
  POST /api/flux/<champ_id>/config/        corps {"source","audio","rate"}
  GET  /api/flux/<champ_id>/video.mjpg     direct vidéo         GET /api/flux/<champ_id>/audio.wav   direct son
  POST /api/flux/<champ_id>/envoi/video/   le navigateur envoie une image JPEG (corps brut) ; .../envoi/audio/?rate=16000 un bloc PCM 16 bits mono
  GET/POST /api/flux/<champ_id>/fichiers/  archives du flux = captures en attente d'une ligne (liste) ; POST {"supprimer": nom}
  GET/POST /api/flux/<champ_id>/vision/    surveillance intelligente (flux_vision.py) : réglage {"actif","profil","detecteurs",...} + état + événements
  GET  /api/flux/vision/catalogue/         détecteurs disponibles (cascades Haar/LBP/personnalisées, HOG, mouvement, bruit)
  POST /api/flux/<champ_id>/capture/       photo ;  POST /api/flux/<champ_id>/enregistrer/  {"action":"start|stop","mode":"video|audio|av"}
  (photo et démarrage d'un enregistrement exigent un flux lié ; l'arrêt est toujours permis, pour ne jamais perdre un fichier)
  La capture est rattachée à une ligne par POST /api/tables/<id>/lignes/ avec "_flux_champ", "_flux_fichier" (répétable) et
  "_flux_garder" (voir views/_part_002.py) : elle est alors enregistrée comme un champ fichier ordinaire (flux_nettoyage.py : ménage).
Écoute (micro, caméra) et réglages : agents/admin. Diffusion : tout membre de l'entreprise, clients compris (lecture seule).
"""
from engine.i18n import t, tf
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from . import flux_media as fm
from .models import ChampDynamique, TableDynamique
from .views import _entreprise_courante, _interdit_lecteur
from .vue_groupe import nom_vue, tables_generales


def _champ(request, champ_id, ecoute=True):
    """(champ, None) ou (None, réponse d'erreur). `ecoute` : refuse les clients (lecteurs) hors diffusion."""
    champ = get_object_or_404(ChampDynamique, id=champ_id, type_champ__in=fm.TYPES_FLUX,
                              table__entreprise=_entreprise_courante(request))
    if ecoute and champ.type_champ not in fm.TYPES_DIFFUSION:
        return champ, _interdit_lecteur(request)
    return champ, (None if champ.type_champ in fm.TYPES_DIFFUSION else _interdit_lecteur(request))


def _fiche(c):
    cfg = fm.config_champ(c)
    liee = fm.table_liee(c)
    return {"id": c.id, "table_id": c.table_id, "nom": c.table.nom, "type": c.type_champ, "source": cfg.get("source", ""),
            "audio": cfg.get("audio", ""), "sources": cfg.get("sources", []), "rate": cfg.get("rate", 16000), "enregistrement": fm.en_cours(c.id),
            "vision": bool((cfg.get("vision") or {}).get("actif")),
            "table_liee": {"id": liee.id, "nom": nom_vue(liee)} if liee else None}


def _journaliser_colonnes(request, table, slugs):
    """Les colonnes média ajoutées à une table existante sont une restructuration : elles sont journalisées comme telle."""
    if slugs:
        from .models import Bloc
        Bloc.ajouter_transaction(table.entreprise_id, {"type": "restructuration_table", "table_id": table.id, "table": table.nom,
                                                       "agent_id": request.user.id, "nouveaux_champs": slugs,
                                                       "tables_du_groupe_impactees": []})


def _exige_lien(champ):
    """Réponse d'erreur si le flux n'est lié à aucune table (rien ne peut alors être enregistré sans risque d'égarement), sinon None."""
    if fm.table_liee(champ) is None:
        return JsonResponse({"erreur": t("engine/flux_media.py:err_non_lie"), "non_lie": True}, status=409)
    return None


@login_required
def page_flux(request):
    return render(request, "engine_pages/flux_media.html")


@login_required
def page_flux_mosaique(request):
    """Mosaïque : tous les flux caméra de l'entreprise en direct sur une page (liste via api_flux, déjà filtrée par droits)."""
    return render(request, "engine_pages/flux_mosaique.html")


@login_required
@require_http_methods(["GET", "POST"])
def api_flux(request):
    entreprise = _entreprise_courante(request)
    if request.method == "GET":
        types = fm.TYPES_DIFFUSION if _interdit_lecteur(request) else fm.TYPES_FLUX     # un client ne voit que les diffusions
        champs = ChampDynamique.objects.filter(type_champ__in=types, table__entreprise=entreprise).select_related("table")
        fiches = [_fiche(c) for c in champs]
        if types == fm.TYPES_DIFFUSION:      # un client regarde la diffusion : la table de données du flux ne le concerne pas
            for fiche in fiches:
                fiche["table_liee"] = None
        return JsonResponse({"flux": fiches})
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    corps = json.loads(request.body or "{}")
    nom, type_champ = (corps.get("nom") or "").strip(), corps.get("type")
    if not nom or type_champ not in fm.TYPES_FLUX:
        return JsonResponse({"erreur": t("engine/views_media.py:g:59:40")}, status=400)
    cible = corps.get("table_liee_id")
    if cible in (None, ""):          # un flux est TOUJOURS lié à une table : sinon ses captures n'appartiendraient à aucune ligne
        return JsonResponse({"erreur": t("engine/views_media.py:err_table_requise")}, status=400)
    from .import_fichier import _slug_table_unique
    try:
        with transaction.atomic():
            if cible == "nouvelle":      # table de données créée avec le flux : vide, hormis les colonnes média ajoutées plus bas
                donnees = TableDynamique.objects.create(entreprise=entreprise, nom=tf("engine/views_media.py:nom_table_donnees", v1=nom),
                                                        slug=_slug_table_unique(entreprise, nom + "_donnees"))
            else:
                donnees = TableDynamique.objects.filter(id=int(cible), entreprise=entreprise).first() \
                    if str(cible).isdigit() else None
            table = TableDynamique.objects.create(entreprise=entreprise, nom=nom, slug=_slug_table_unique(entreprise, nom))
            champ = ChampDynamique.objects.create(table=table, nom=nom, slug="flux", type_champ=type_champ, obligatoire=False,
                                                  options_choix="{}")
            ajoutees = fm.lier_table(champ, donnees)
    except ValueError as e:              # table refusée ou colonne impossible : rien n'est créé (ni flux, ni table, ni colonne)
        return JsonResponse({"erreur": str(e)}, status=400)
    _journaliser_colonnes(request, donnees, ajoutees)
    return JsonResponse(_fiche(champ), status=201)


@login_required
def api_flux_tables(request):
    """Tables auxquelles un flux peut être lié (un groupe de tables compte pour UNE table, sous le nom du groupe)."""
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    entreprise = _entreprise_courante(request)
    flux = set(ChampDynamique.objects.filter(type_champ__in=fm.TYPES_FLUX, table__entreprise=entreprise).values_list("table_id", flat=True))
    candidates = [x for x in TableDynamique.objects.filter(entreprise=entreprise, denaturee=False).order_by("nom")
                  if x.id not in flux and not fm.erreur_table_cible(x)]
    return JsonResponse({"tables": [{"id": x.id, "nom": nom_vue(x)} for x in tables_generales(candidates)]})


def _formulaire(champ):
    """Table liée + colonnes que le formulaire de la ligne ajoute à celui du flux."""
    liee = fm.table_liee(champ)
    return {"table_liee": {"id": liee.id, "nom": nom_vue(liee)} if liee else None,
            "champs": [{"slug": c.slug, "nom": c.nom, "type": c.type_champ, "obligatoire": c.obligatoire, "options": c.options_choix,
                        "relation_vers_id": c.relation_vers_id, "relation_champ_affichage": c.relation_champ_affichage or "",
                        "relation_colonne_source": c.relation_colonne_source or ""}
                       for c in fm.champs_formulaire(champ)],
            "genres": list(fm.genres_flux(champ))}


@login_required
@require_http_methods(["GET", "POST"])
def api_flux_table(request, champ_id):
    """GET : table liée et colonnes du formulaire. POST {"table_id"} : (re)lie le flux (agents/admin) — les colonnes média
    sont ajoutées à la nouvelle table ; l'ancienne garde ses colonnes et ses lignes, et les fichiers déjà rattachés le restent."""
    champ, bloque = _champ(request, champ_id, ecoute=False)
    bloque = bloque or _interdit_lecteur(request)
    if bloque:
        return bloque
    if request.method == "POST":
        corps = json.loads(request.body or "{}")
        cible = corps.get("table_id")
        table = TableDynamique.objects.filter(id=int(cible), entreprise=_entreprise_courante(request)).first() \
            if str(cible or "").isdigit() else None
        try:
            ajoutees = fm.lier_table(champ, table)
        except ValueError as e:
            return JsonResponse({"erreur": str(e)}, status=400)
        _journaliser_colonnes(request, table, ajoutees)
    return JsonResponse(dict(_formulaire(champ), flux=_fiche(champ)))


@login_required
@require_http_methods(["POST"])
def api_flux_config(request, champ_id):
    champ, bloque = _champ(request, champ_id, ecoute=False)
    bloque = bloque or _interdit_lecteur(request)
    if bloque:
        return bloque
    corps = json.loads(request.body or "{}")
    cfg = {"source": (corps.get("source") or "").strip(), "rate": int(corps.get("rate") or 16000)}
    video = champ.type_champ in fm.TYPES_VIDEO
    erreur = fm.valider_source(cfg["source"], video)
    if not erreur and video and corps.get("audio"):
        cfg["audio"] = corps["audio"].strip()
        erreur = fm.valider_source(cfg["audio"], False)
    if erreur:
        return JsonResponse({"erreur": erreur}, status=400)
    memoire = {"source": cfg["source"], "audio": cfg.get("audio", "")}
    anciennes = [x for x in fm.config_champ(champ).get("sources", []) if isinstance(x, dict) and x != memoire]
    cfg["sources"] = [memoire] + anciennes       # bascule rapide entre sources déjà utilisées (aucune limite de nombre)
    if fm.config_champ(champ).get("vision"):
        cfg["vision"] = fm.config_champ(champ)["vision"]      # le réglage de surveillance survit à un changement de source
    if fm.config_champ(champ).get("table_id"):
        cfg["table_id"] = fm.config_champ(champ)["table_id"]  # le lien à la table de données aussi
    champ.options_choix = json.dumps(cfg)
    champ.save(update_fields=["options_choix"])
    return JsonResponse(_fiche(champ))


def _direct(request, champ_id, audio):
    champ, bloque = _champ(request, champ_id)
    if bloque:
        return bloque
    cfg = fm.config_champ(champ)
    if fm.SOURCE_NAVIGATEUR in (cfg.get("source"), cfg.get("audio")):       # ce que filme un utilisateur : agents seulement
        bloque = _interdit_lecteur(request)
        if bloque:
            return bloque
    if audio == (champ.type_champ in fm.TYPES_VIDEO) and not cfg.get("audio"):
        return JsonResponse({"erreur": t("engine/views_media.py:g:101:40")}, status=404)
    try:
        h = fm.hub(champ, audio=audio)
    except (ValueError, ImportError, OSError) as e:
        return JsonResponse({"erreur": str(e)}, status=503)
    if audio:
        return StreamingHttpResponse(fm.flux_wav(h), content_type="audio/wav")
    return StreamingHttpResponse(fm.flux_mjpeg(h), content_type="multipart/x-mixed-replace; boundary=frame")


@login_required
def api_flux_video(request, champ_id):
    return _direct(request, champ_id, False)


@login_required
def api_flux_audio(request, champ_id):
    return _direct(request, champ_id, True)


def _captures(champ, chemins):
    """Réponse après une capture : URLs (`fichiers`, historique) + `captures` {nom, url, genre} à joindre à une ligne."""
    return {"fichiers": [fm.url_fichier(champ, p.name) for p in chemins],
            "captures": [{"nom": p.name, "url": fm.url_fichier(champ, p.name), "genre": fm.genre_fichier(p.name)} for p in chemins]}


@login_required
@require_http_methods(["POST"])
def api_flux_capture(request, champ_id):
    champ, bloque = _champ(request, champ_id)
    bloque = bloque or _interdit_lecteur(request) or _exige_lien(champ)
    if bloque:
        return bloque
    try:
        chemin = fm.capturer_image(champ)
    except (ValueError, ImportError, OSError) as e:
        return JsonResponse({"erreur": str(e)}, status=503)
    fm.notifier_fichiers(champ, [chemin])
    return JsonResponse(_captures(champ, [chemin]))


@login_required
@require_http_methods(["POST"])
def api_flux_enregistrer(request, champ_id):
    champ, bloque = _champ(request, champ_id)
    bloque = bloque or _interdit_lecteur(request)
    if bloque:
        return bloque
    corps = json.loads(request.body or "{}")
    try:
        if corps.get("action") == "stop":      # toujours permis, même si le lien a disparu : le fichier en cours ne doit pas se perdre
            chemins = fm.arreter_enregistrement(champ)
            fm.notifier_fichiers(champ, chemins)
            return JsonResponse(_captures(champ, chemins))
        bloque = _exige_lien(champ)
        if bloque:
            return bloque
        mode = corps.get("mode") or ("audio" if champ.type_champ not in fm.TYPES_VIDEO else "video")
        fm.demarrer_enregistrement(champ, mode)
    except (ValueError, ImportError, OSError) as e:
        return JsonResponse({"erreur": str(e)}, status=503)
    return JsonResponse({"enregistrement": True})


def _envoi(request, champ_id, audio):
    """Le navigateur (téléphone, PC) émet. Diffusion : tout membre, clients compris. Micro / caméra : agents."""
    champ, bloque = _champ(request, champ_id)
    if bloque:
        return bloque
    if not audio and champ.type_champ not in fm.TYPES_VIDEO:
        return JsonResponse({"erreur": t("engine/views_media.py:g:162:40")}, status=400)
    cfg = fm.config_champ(champ)
    cle = "audio" if (audio and champ.type_champ in fm.TYPES_VIDEO) else "source"
    if not cfg.get(cle):                      # flux encore vide : il devient « navigateur »
        cfg[cle] = fm.SOURCE_NAVIGATEUR
        champ.options_choix = json.dumps(cfg)
        champ.save(update_fields=["options_choix"])
    elif cfg[cle] != fm.SOURCE_NAVIGATEUR:
        return JsonResponse({"erreur": t("engine/views_media.py:g:170:40") % cfg[cle]}, status=409)
    try:
        rate = int(request.GET.get("rate") or 0)
        fm.hub(champ, audio=audio).pousser(request.body, request.user.id, rate)
    except (ValueError, ImportError, OSError) as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_flux_envoi_video(request, champ_id):
    return _envoi(request, champ_id, False)


@login_required
@require_http_methods(["POST"])
def api_flux_envoi_audio(request, champ_id):
    return _envoi(request, champ_id, True)


@login_required
@require_http_methods(["GET", "POST"])
def api_flux_fichiers(request, champ_id):
    """Archives du flux : l'agent relit, réutilise ou supprime ce qui a été enregistré."""
    champ, bloque = _champ(request, champ_id)
    bloque = bloque or _interdit_lecteur(request)
    if bloque:
        return bloque
    if request.method == "POST":
        try:
            fm.supprimer_fichier(champ, (json.loads(request.body or "{}").get("supprimer") or ""))
        except ValueError as e:
            return JsonResponse({"erreur": str(e)}, status=404)
    return JsonResponse({"fichiers": [dict(f, url=fm.url_fichier(champ, f["nom"]), genre=fm.genre_fichier(f["nom"]))
                                      for f in fm.lister_fichiers(champ)]})


@login_required
def api_vision_catalogue(request):
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    from . import flux_vision as fv
    entreprise = _entreprise_courante(request)
    return JsonResponse({"detecteurs": [{k: v for k, v in e.items() if k not in ("fichier", "reglages")}
                                        for e in fv.catalogue(entreprise.id if entreprise else None)],
                         "niveaux": list(fv.NIVEAUX), "profils": list(fv.PROFILS)})


@login_required
@require_http_methods(["GET", "POST"])
def api_flux_vision(request, champ_id):
    """Réglage + état + derniers événements de la surveillance d'un flux (agents/admin ; un client ne voit jamais la vision)."""
    from . import flux_vision as fv
    champ, bloque = _champ(request, champ_id, ecoute=False)
    bloque = bloque or _interdit_lecteur(request)
    if bloque:
        return bloque
    if request.method == "POST":
        try:
            corps = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"erreur": t("engine/views_media.py:json_invalide")}, status=400)
        cfg, erreur = fv.valider_config(corps, fv.config_vision(champ), entreprise_id=champ.table.entreprise_id)
        if erreur:
            return JsonResponse({"erreur": erreur}, status=400)
        fv.enregistrer_config(champ, cfg)
        if cfg.get("actif"):
            try:
                fv.demarrer(champ)
            except ValueError as e:
                fv.enregistrer_config(champ, dict(cfg, actif=False))
                return JsonResponse({"erreur": str(e)}, status=400)
        else:
            fv.arreter(champ.id)
    return JsonResponse({"config": fv.config_vision(champ), "etat": fv.etat(champ),
                         "evenements": fv.lire_journal(champ.table.entreprise_id, champ.id),
                         "base": settings.MEDIA_URL + "flux_media/%s/" % champ.table.entreprise_id})
