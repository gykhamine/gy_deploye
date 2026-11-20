"""GÉOLOCALISATION : page carte plein canevas + API d'analyse/simulation (calculs : engine/geospatial.py, données : engine/geo_donnees.py).

  GET  /tables/<id>/geolocalisation/                    la page (la carte seule dans le canevas du dashboard)
  GET  /api/tables/<id>/geolocalisation/analyse/        analyse (lecture seule, rien n'est enregistré) ; ?demo=1 = trajectoires simulées ;
                                                        ?tables=12,15 ajoute d'autres tables (une table = un objet, avec sa date et sa position)
  POST /api/tables/<id>/geolocalisation/simuler/        même analyse, ENREGISTRÉE comme ligne de « Analyses géospatiales »
  GET  /api/geolocalisation/tuiles/<z>/<x>/<y>.png     fond de carte : tuile OpenStreetMap servie et mise en cache par le serveur (engine/geo_tuiles.py)
  POST /api/tables/<id>/geolocalisation/flux/           décode un flux de positions (AIS, GPS NMEA, ADS-B, JSON, CSV) et l'écrit dans la table ;
                                                        `apercu` = décodage seul, rien n'est écrit (engine/flux_positions.py, engine/geo_flux.py)
  POST /api/tables/<id>/geolocalisation/recevoir/       réception d'un tracker distant : le CORPS BRUT de la requête est le flux (NMEA, AIS, SBS, JSON, CSV),
                                                        réglages en paramètres d'adresse (?format=&id=&pas=&horodatage=&rotation=1) ; authentifié par la
                                                        clé API déjà en place (en-tête « Authorization: Bearer <clé> », engine/middleware.py::CleAPIAuthMiddleware)
  GET/POST /api/tables/<id>/geolocalisation/annotations/  points, trajectoires et zones tracés par l'utilisateur, ENREGISTRÉS (table « Annotations cartographiques », engine/geo_annotations.py)
  POST /api/tables/<id>/geolocalisation/outils/         outils tactiques, calculs seuls (rien n'est écrit) : conversions UTM/MGRS/DMS, mesures géodésiques, matrice de distances,
                                                        projection à l'estime, cercles et corridors, géorepérage, export KML/GPX (engine/geo_outils.py)
  POST /api/tables/<id>/geolocalisation/itineraire/     itinéraire PAR LES ROUTES (ruelles, avenues ; voiture, vélo, à pied), calculé par le serveur ; s'ajoute à la ligne droite (engine/geo_routage.py)
  POST /api/tables/<id>/geolocalisation/partager/       une personne PARTAGE sa position : écrite dans cette table de positions (une colonne par personne, une ligne par instant), relue ensuite par la carte et les itinéraires (engine/geo_partage.py)
  (suivi continu par processus détaché : retiré)  # rrêter (engine/geo_suivi.py)
  Lecture : ?objets=N et ?fenetre=N bornent la lecture si on le demande ; absents ou vides : TOUS les objets et TOUS les pas (aucun plafond).

Mêmes droits que le dashboard BI (table propre ou partagée en lecture seule, vue autorisée pour l'agent) ; les clients (lecteurs) y accèdent
en LECTURE SEULE, sur les seules tables exposées, avec abonnement si l'agent a fixé un prix (wallet.acces_page_client). Une table partagée par une autre entreprise s'analyse mais ne s'enregistre pas.
Les trajectoires de démonstration (?demo=1) ne sont jamais enregistrées : elles ne doivent pas entrer dans les données de décision.
"""
from engine.i18n import td
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from engine.i18n import t as _i18n_t, tf
import json
import tempfile
import zipfile

import numpy as np
from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder
from django.conf import settings
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import flux_positions, geo_adresses, geo_annotations, geo_donnees, geo_import, geo_flux, geo_outils, geo_partage, geo_routage, geo_tuiles
from .models import MembreEntreprise, TableDynamique
from .ratelimit import limiter
from .stockage import dossier_systeme
from .views import _admin_courant, _entreprise_courante, _interdit_table_non_exposee, _table_via_partage, _vue_autorisee
from engine.i18n import t as _gt  # noqa: E402


class _Encodeur(DjangoJSONEncoder):
    def default(self, o):
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
        return super().default(o)


def _acces(request, table_id, api=False, vue="geolocalisation", abonnement=True):
    """(table, lecture_seule, entreprise, refus) — `refus` est la réponse à renvoyer si l'accès est interdit. `vue` = la vue de
    VUES_DISPONIBLES que l'agent doit avoir le droit d'ouvrir (partagé avec l'onglet Séries temporelles : mêmes droits que la carte)."""
    entreprise = _entreprise_courante(request)
    table, lecture_seule = _table_via_partage(request, table_id)
    refus = None if lecture_seule else _interdit_table_non_exposee(request, table)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not refus and not _vue_autorisee(membre, vue):
        refus = JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:60:41")}, status=403) if api else redirect("/profile/")
    if not refus and membre and membre.role == "lecteur":
        # Client : TOUJOURS en lecture seule (aucune écriture, aucune sauvegarde) ; abonnement requis si l'agent a fixé un prix.
        from .wallet import acces_page_client, page_ouverte_client
        lecture_seule = True
        if not page_ouverte_client(entreprise, vue):   # page fermée aux clients par l'agent
            return table, lecture_seule, entreprise, (JsonResponse({"erreur": _gt("rg8.msg.022")}, status=403) if api else redirect("engine:espace_client"))
        if abonnement and not acces_page_client(entreprise, membre, vue):
            refus = JsonResponse({"erreur": _gt("rg8.msg.021"), "service": vue}, status=402) if api else redirect(f"/espace-client/paiement/?service={vue}")
    return table, lecture_seule, entreprise, refus


def _fiches_pour(request, entreprise, fiches):
    """Listes de tables proposées par une page d'analyse : un client (lecteur) n'y voit que les tables exposées aux clients."""
    from .models import TableDynamique
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if membre and membre.role == "agent":   # tables système (wallet, paiement…) : l'administrateur seul, sauf autorisation explicite
        systeme = set(TableDynamique.objects.filter(entreprise=entreprise).exclude(source_systeme="").values_list("id", flat=True))
        autorisees = set(membre.tables_autorisees or [])
        return [f for f in fiches if f["id"] not in systeme or f["id"] in autorisees]
    if not (membre and membre.role == "lecteur"):
        return fiches
    from django.db.models import Q
    from .views import _table_permise_client
    exposees = {t.id for t in TableDynamique.objects.filter(entreprise=entreprise).filter(
        Q(expose_client=True) | Q(masquer_formulaire_client=True)).exclude(est_table_membres=True)
        if _table_permise_client(membre, t)}   # + restriction posée par l'agent sur CE client   # accessible_client est une propriété, pas un champ
    return [f for f in fiches if f["id"] in exposees]


MAX_TABLES_AJOUTEES = 99   # + la table ouverte = 100 tables au plus dans une analyse


def _nombre(donnees, cle, cast, bas, haut):
    try:
        return max(bas, min(haut, cast(donnees.get(cle))))
    except (TypeError, ValueError):
        return None


def _parametres(donnees) -> dict:
    """Paramètres d'analyse bornés (tout ce qui est absent ou invalide retombe sur le défaut du moteur)."""
    p = {"seuil_collision_m": _nombre(donnees, "seuil", float, 1.0, 100000.0), "horizon_pas": _nombre(donnees, "horizon", int, 1, 120),
         "sigma_mesure_m": _nombre(donnees, "sigma_mesure", float, 0.1, 10000.0), "echantillons": _nombre(donnees, "echantillons", int, 20, 1000)}
    mode = str(donnees.get("mode") or "").lower()          # « auto » (défaut) : le moteur juge chaque colonne ; sinon la personne tranche pour toutes les colonnes choisies
    if mode in ("trajectoire", "points"):
        p["mode_positions"] = mode
    return {k: v for k, v in p.items() if v is not None}


def _liste(valeur) -> list:
    return [x.strip() for x in valeur.split(",")] if isinstance(valeur, str) else [str(x) for x in (valeur or [])]


def _sources(request, table, donnees):
    """([(table, slugs | None), ...], lecture_seule, refus, temps). `tables` = ids des AUTRES tables à analyser avec celle-ci (une table = un objet, avec sa
    date et sa position) ; `colonnes` = « slug » (table ouverte) ou « <id table>:slug » (autre table) ; `temps` = colonne de date de chaque table, même
    notation (« - » = ordre des lignes) ; `ouverte=0` écarte la table ouverte de l'analyse (la page n'en choisit aucune à la place de la personne).
    Sans `ouverte`, la table ouverte est incluse avec toutes ses positions (appels extérieurs). Chaque table ajoutée passe par les mêmes contrôles d'accès
    que la table ouverte ; si l'une n'est que partagée en lecture, rien ne s'enregistre. `temps` = {id table: slug}."""
    colonnes = [c for c in _liste(donnees.get("colonnes")) if c]
    propres = {c for c in colonnes if ":" not in c}
    par_table = {}
    for c in colonnes:
        if ":" in c:
            tid, _, slug = c.partition(":")
            par_table.setdefault(tid, set()).add(slug)
    temps = {}
    for c in [c for c in _liste(donnees.get("temps")) if c]:
        tid, sep, slug = c.partition(":")
        if sep:
            try:
                temps[int(tid)] = slug
            except ValueError:
                continue
        else:
            temps[table.id] = c
    ids = []
    for x in _liste(donnees.get("tables")):
        try:
            i = int(x)
        except ValueError:
            continue
        if i != table.id and i not in ids:
            ids.append(i)
    inclure = str(donnees.get("ouverte", "1")).lower() not in ("0", "false", "non")
    sources, lecture_seule = ([(table, propres or None)] if inclure else []), False
    for i in ids[:MAX_TABLES_AJOUTEES]:
        autre, seule, _e, refus = _acces(request, i, api=True)
        if refus:
            return None, False, refus, {}
        lecture_seule = lecture_seule or seule
        sources.append((autre, par_table.get(str(i)) or None))
    return sources, lecture_seule, None, temps


def _analyse(sources, donnees, temps=None):
    """(résultat, demo). `sources` et `temps` : voir `_sources`."""
    p = _parametres(donnees)
    if str(donnees.get("demo") or "") in ("1", "true", "True"):
        extra = {k: v for k, v in (("n_objets", _nombre(donnees, "n_objets", int, 2, 60)), ("graine", _nombre(donnees, "graine", int, 0, 10 ** 9))) if v is not None}
        return geo_donnees.analyser_scenario(**extra, **p), True
    if not sources:
        return {"vide": True, "message": _i18n_t("engine/views_geo.py:g:155:42")}, False
    objets, pas = geo_donnees.lecture(donnees.get("objets"), donnees.get("fenetre"))
    resultat = geo_donnees.analyser_sources(sources, limite=pas, objets=objets, temps=temps, **p)
    if resultat is None:
        resultat = {"vide": True, "message": td("engine/views_geo.py:g:160:46")}
    return resultat, False


@login_required
@ensure_csrf_cookie
def vue_geolocalisation(request, table_id):
    table, lecture_seule, entreprise, refus = _acces(request, table_id)
    if refus:
        return refus
    return render(request, "engine/geolocalisation.html", {
        "entreprise": entreprise, "table": table, "lecture_seule": lecture_seule, "flux_max": TAILLE_MAX_FLUX,
        "colonnes_geo": [{"slug": c.slug, "nom": c.nom} for c in geo_donnees.colonnes_geo(table)],
        "tables_geo": _fiches_pour(request, entreprise, geo_donnees.tables_geo(entreprise, table.id)),
        "sources_geo": [dict(geo_donnees.fiche_table(table), ouverte=True)] + _fiches_pour(request, entreprise, geo_donnees.tables_geo(entreprise, table.id)),
        "tables_commandes": _fiches_pour(request, entreprise, geo_donnees.tables_commandes(entreprise, table.id))})


@login_required
@require_http_methods(["GET", "POST"])
def api_geo_analyse(request, table_id):
    """GET (paramètres d'adresse) ou POST (mêmes paramètres en JSON : nécessaire dès que le choix des tables et colonnes dépasse la longueur
    d'une adresse). Dans les deux cas rien n'est enregistré."""
    table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if request.method == "POST":
        try:
            donnees = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:189:44")}, status=400)
        donnees = donnees if isinstance(donnees, dict) else {}
    else:
        donnees = request.GET
    sources, _seule, refus, temps = _sources(request, table, donnees)
    if refus:
        return refus
    if donnees.get("empreinte_seule"):   # contrôle léger du rafraîchissement : rien n'est calculé
        return JsonResponse({"empreinte": geo_donnees.empreinte_sources(sources, geo_donnees.lecture(donnees.get("objets"), donnees.get("fenetre"))[1]) if sources else None})
    resultat, _demo = _analyse(sources, donnees, temps)
    return JsonResponse(resultat, encoder=_Encodeur)


@login_required
@require_http_methods(["POST"])
def api_geo_simuler(request, table_id):
    table, lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    try:
        donnees = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:211:40")}, status=400)
    donnees = donnees if isinstance(donnees, dict) else {}
    sources, seule_autres, refus, temps = _sources(request, table, donnees)
    if refus:
        return refus
    resultat, demo = _analyse(sources, donnees, temps)
    resultat["enregistre"] = None
    if not demo and not lecture_seule and not seule_autres and not resultat.get("vide"):
        nom_source = " + ".join(t.nom for t, _s in sources)[:200]
        resultat["enregistre"] = geo_donnees.enregistrer(entreprise, request.user, table, resultat, nom_source)
    return JsonResponse(resultat, encoder=_Encodeur)


TAILLE_MAX_FLUX = 5 * 1024 * 1024   # par REQUÊTE : la page découpe un gros fichier en morceaux ; la commande `suivre_flux_positions` lit en continu, sans limite
_MO = f"{TAILLE_MAX_FLUX // 1048576} Mo"


@login_required
@require_http_methods(["POST"])
def api_geo_flux(request, table_id):
    """Corps JSON : {donnees (texte), format, pas (s), horodatage (source|reception), creer_colonnes, id_defaut, apercu, rotation}. Écriture réservée à une
    table propre à l'entreprise (une table partagée en lecture seule refuse), aux mêmes droits que la carte."""
    table, lecture_seule, _entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if lecture_seule:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:237:40")}, status=403)
    if len(request.body or b"") > TAILLE_MAX_FLUX:
        return JsonResponse({"erreur": tf("engine/views_geo.py:g:239:40", v1=_MO)}, status=413)
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:243:40")}, status=400)
    d = d if isinstance(d, dict) else {}
    texte = d.get("donnees")
    if not isinstance(texte, str) or not texte.strip():
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:247:40")}, status=400)
    oui = lambda cle, defaut: str(d.get(cle, defaut)).lower() in ("1", "true", "on")
    try:
        resultat = geo_flux.traiter_flux(
            table, request.user, texte, d.get("format") if d.get("format") in flux_positions.FORMATS else "auto", str(d.get("id_defaut") or "gps")[:60],
            _nombre(d, "pas", float, 0.5, 86400.0) or 10.0, "reception" if d.get("horodatage") == "reception" else "source",
            oui("creer_colonnes", "1"), oui("apercu", "0"), oui("rotation", "0"))
    except geo_flux.ErreurFlux as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse(resultat, encoder=_Encodeur)


@login_required
@require_http_methods(["POST"])
@limiter("geo_recevoir", max_appels=1200, periode_secondes=60, methodes={"POST"})
def api_geo_recevoir(request, table_id):
    """Tracker distant : le corps BRUT de la requête est le flux (un tracker n'enveloppe pas ses trames dans du JSON). Authentification : la clé API
    de l'entreprise, en-tête « Authorization: Bearer <clé> » (CleAPIAuthMiddleware), mêmes droits que la carte ; table partagée en lecture seule refusée.
    Réglages en paramètres d'adresse : format, id (identifiant d'un GPS sans identifiant propre), pas, horodatage (reception par défaut | source),
    creer_colonnes (1 par défaut), rotation (0 par défaut)."""
    table, lecture_seule, _entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if lecture_seule:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:271:40")}, status=403)
    if len(request.body or b"") > TAILLE_MAX_FLUX:
        return JsonResponse({"erreur": tf("engine/views_geo.py:g:273:40", v1=_MO)}, status=413)
    texte = (request.body or b"").decode("utf-8", "replace")
    if not texte.strip():
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:276:40")}, status=400)
    q = request.GET
    oui = lambda cle, defaut: str(q.get(cle, defaut)).lower() in ("1", "true", "on")
    try:
        resultat = geo_flux.traiter_flux(
            table, request.user, texte, q.get("format") if q.get("format") in flux_positions.FORMATS else "auto", str(q.get("id") or "gps")[:60],
            _nombre(q, "pas", float, 0.5, 86400.0) or 10.0, "source" if q.get("horodatage") == "source" else "reception", oui("creer_colonnes", "1"),
            False, oui("rotation", "0"))
    except geo_flux.ErreurFlux as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({"rapport": resultat["rapport"], "ecriture": resultat["ecriture"]}, encoder=_Encodeur)




@login_required
@require_http_methods(["GET", "POST"])
def api_geo_annotations(request, table_id):
    """GET : annotations de la carte (points, trajectoires, zones). POST {action: creer|modifier|supprimer, ...} : écriture réservée aux tables propres
    (jamais une table partagée en lecture seule) ; un agent ne modifie que ses annotations (engine/geo_annotations.py)."""
    table, lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if request.method == "GET":
        return JsonResponse({"annotations": geo_annotations.lister(entreprise, table.id), "ecriture": not lecture_seule})
    if lecture_seule:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:334:40")}, status=403)
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:338:40")}, status=400)
    d = d if isinstance(d, dict) else {}
    admin, action = bool(_admin_courant(request, entreprise)), d.get("action")
    try:
        if action == "supprimer":
            return JsonResponse({"supprimee": geo_annotations.supprimer(entreprise, request.user, table.id, d.get("id"), admin)})
        if action == "modifier":
            geo_annotations.modifier(entreprise, request.user, table.id, d.get("id"), d, admin)
            return JsonResponse({"modifiee": d.get("id")})
        if action != "creer":
            return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:348:44")}, status=400)
        return JsonResponse({"creee": geo_annotations.creer(entreprise, request.user, table.id, d)})
    except geo_annotations.ErreurDroit as e:
        return JsonResponse({"erreur": str(e)}, status=403)
    except geo_annotations.ErreurAnnotation as e:
        return JsonResponse({"erreur": str(e)}, status=400)


def _flottant(d, cle, bas, haut, defaut=None):
    try:
        v = float(d[cle]) if d.get(cle) not in (None, "") else defaut
    except (TypeError, ValueError):
        raise geo_outils.ErreurOutil(tf("engine/views_geo.py:g:360:38", v1=cle))
    if v is not None and not bas <= v <= haut:
        raise geo_outils.ErreurOutil(tf("engine/views_geo.py:g:362:38", v1=cle, v2=bas, v3=haut))
    return v


@login_required
@require_http_methods(["POST"])
@limiter("geo_outils", max_appels=600, periode_secondes=60, methodes={"POST"})
def api_geo_outils(request, table_id):
    """POST {action, ...} : calculs tactiques de la carte, JAMAIS d'écriture (les zones calculées s'enregistrent ensuite par l'API des annotations).
    Actions : lire {texte} | convertir {lat, lon} | mesurer {points, vitesse_kmh?} | matrice {points, noms?} | projeter {lat, lon, cap, vitesse_ms, horizons_s}
    | zones_points {points: [{id, nom, lat, lon}], zones?} | cercle {lat, lon, rayon_m} | corridor {points, largeur_m} | zones {objets, zones?} (zones absentes = annotations de type zone de cette carte)
    | exporter {format: kml|gpx, objets?, annotations?: true}. Mêmes droits que la carte, y compris en lecture seule."""
    table, _lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:380:40")}, status=400)
    d = d if isinstance(d, dict) else {}
    action = d.get("action")
    try:
        if action == "lire":
            lat, lon = geo_outils.lire_coordonnees(d.get("texte"))
            return JsonResponse(geo_outils.convertir(lat, lon))
        if action == "convertir":
            return JsonResponse(geo_outils.convertir(_flottant(d, "lat", -90, 90), _flottant(d, "lon", -180, 180)))
        if action == "mesurer":
            return JsonResponse(geo_outils.route(d.get("points") or [], _flottant(d, "vitesse_kmh", 0.001, 40000)))
        if action == "matrice":
            return JsonResponse(geo_outils.matrice(d.get("points") or [], d.get("noms")))
        if action == "projeter":
            return JsonResponse({"positions": geo_outils.projeter(_flottant(d, "lat", -90, 90), _flottant(d, "lon", -180, 180), _flottant(d, "cap", -360, 720),
                                                                  _flottant(d, "vitesse_ms", 0, 12000), d.get("horizons_s") or [])})
        if action == "cercle":
            return JsonResponse({"geometrie": geo_outils.cercle(_flottant(d, "lat", -90, 90), _flottant(d, "lon", -180, 180), _flottant(d, "rayon_m", 1, 5_000_000))})
        if action == "corridor":
            return JsonResponse({"geometrie": geo_outils.corridor(d.get("points") or [], _flottant(d, "largeur_m", 1, 1_000_000))})
        if action == "zones":
            zones = d.get("zones")
            if zones is None:
                zones = [{"id": a["id"], "nom": a["titre"], "geometrie": a["geometrie"]} for a in geo_annotations.lister(entreprise, table.id) if a["type"] == "zone"]
            return JsonResponse(geo_outils.evenements_zones(d.get("objets") or [], zones if isinstance(zones, list) else []))
        if action == "zones_points":   # géorepérage des « positions distinctes » : chaque point jugé seul
            zones = d.get("zones")
            if zones is None:
                zones = [{"id": a["id"], "nom": a["titre"], "geometrie": a["geometrie"]} for a in geo_annotations.lister(entreprise, table.id) if a["type"] == "zone"]
            return JsonResponse(geo_outils.points_zones(d.get("points") or [], zones if isinstance(zones, list) else []))
        if action == "exporter":
            annotations = geo_annotations.lister(entreprise, table.id) if d.get("annotations") else []
            return JsonResponse(geo_outils.exporter(d.get("format"), d.get("objets") or [], annotations, f"{table.nom}"))
    except geo_outils.ErreurOutil as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    except (AttributeError, TypeError, KeyError):
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:416:40")}, status=400)
    return JsonResponse({"erreur": _gt("rg8.msg.020")}, status=400)


@login_required
@require_http_methods(["POST"])
@limiter("geo_itineraire", max_appels=40, periode_secondes=60, methodes={"POST"})
def api_geo_itineraire(request, table_id):
    """POST {depart: [lat, lon], arrivee: [lat, lon], profil?: raccourcis|moto|bateau|voiture|velo|pied} : itinéraire PAR LES ROUTES (ruelles, avenues, sens des routes),
    calculé par le serveur (engine/geo_routage.py, OSRM d'OpenStreetMap ou serveur local). S'AJOUTE à la ligne droite, ne la remplace pas ; jamais d'écriture.
    Mer, île, point loin de toute route : 400 avec un message affichable. Mêmes droits que la carte, y compris en lecture seule."""
    _table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:433:40")}, status=400)
    d = d if isinstance(d, dict) else {}
    try:
        return JsonResponse(geo_routage.itineraire(d.get("depart"), d.get("arrivee"), str(d.get("profil") or "voiture")))
    except geo_routage.ErreurRoute as e:
        return JsonResponse({"erreur": str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
@limiter("geo_partager", max_appels=120, periode_secondes=60, methodes={"POST"})
def api_geo_partager(request, table_id):
    """POST {id (nom de la personne), nom?, lat, lon, precision_m?, cap?, vitesse_kmh?} : écrit la position dans la table de positions <id> (colonne de la personne créée au
    premier envoi, une ligne par instant). Mêmes droits que la carte ; une table partagée en lecture seule refuse ; position imprécise (> 200 m) refusée."""
    table, lecture_seule, _entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if lecture_seule:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:451:40")}, status=403)
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:455:40")}, status=400)
    try:
        p = geo_partage.construire(d)
        resultat = geo_flux.traiter_flux(table, request.user, p["texte"], "json", p["id"], 1.0, "reception", True, False, False)
    except (geo_partage.ErreurPartage, geo_flux.ErreurFlux) as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({"ok": True, "id": p["id"], "ecriture": resultat["ecriture"]}, encoder=_Encodeur)


def _logistique_parametres(d) -> dict:
    """Paramètres de tournée bornés ; ce qui manque ou est invalide retombe sur le défaut du moteur."""
    p = {"capacite": _nombre(d, "capacite", float, 0.001, 1e9), "service_s": (lambda m: None if m is None else m * 60)(_nombre(d, "service_min", float, 0, 600)),
         "detour": _nombre(d, "detour", float, 1.0, 3.0), "vitesse_ms": (lambda k: None if k is None else k / 3.6)(_nombre(d, "vitesse_kmh", float, 3, 140)),
         "budget_s": _nombre(d, "budget_s", float, 0.5, 20.0), "zones": d.get("zones") if isinstance(d.get("zones"), list) else None}
    for cle in ("retour_depot", "depuis_position"):
        if cle in d:
            p[cle] = str(d.get(cle)).lower() in ("1", "true", "on")
    return {k: v for k, v in p.items() if v is not None}


@login_required
@require_http_methods(["POST"])
def api_logistique_planifier(request, table_id):
    """Optimise les tournées (véhicules = colonnes de position de la table, commandes = lignes de `commandes_table`) ; `enregistrer` range le plan."""
    table, lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:485:40")}, status=400)
    d = d if isinstance(d, dict) else {}
    params = _logistique_parametres(d)
    depot = None
    try:
        if d.get("depot"):
            depot = (float(d["depot"][0]), float(d["depot"][1]))
            if not (-90 <= depot[0] <= 90 and -180 <= depot[1] <= 180):
                raise ValueError
    except (TypeError, ValueError, IndexError, KeyError):
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:495:40")}, status=400)
    if str(d.get("demo") or "") in ("1", "true", "True", "strategique"):
        plan = geo_donnees.planifier_scenario(depot, strategique=str(d.get("demo")) == "strategique", **params)
        plan["enregistre"] = None
        return JsonResponse(plan, encoder=_Encodeur)
    cmd = TableDynamique.objects.filter(id=d.get("commandes_table") or 0, entreprise=entreprise).first()
    if cmd is None or cmd.id == table.id:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:502:40")}, status=400)
    slugs = set(d.get("colonnes") or []) or None
    plan = geo_donnees.planifier_table(table, cmd, depot, slugs, d.get("colonne_demande") or None, flotte=d.get("flotte"), **params)
    plan["enregistre"] = None
    if d.get("enregistrer") and not lecture_seule and not plan.get("vide"):
        plan["enregistre"] = geo_donnees.enregistrer_plan(entreprise, request.user, table, cmd, plan)
    return JsonResponse(plan, encoder=_Encodeur)


@login_required
@require_http_methods(["GET"])
def api_geo_tuile(request, z, x, y):
    """Tuile du fond de carte (données publiques : connexion requise, pas de droit par table). 404 = indisponible : la carte garde sa grille."""
    octets = geo_tuiles.tuile(dossier_systeme("geo_tuiles"), z, x, y)
    if octets is None:
        return HttpResponse(status=404, headers={"Cache-Control": "no-store"})
    geo_tuiles.signaler(z, x, y)        # le moissonneur ira compléter les alentours (voisines, zoom plus fin) en arrière-plan
    return HttpResponse(octets, content_type="image/png", headers={"Cache-Control": "private, max-age=604800"})


def _admin_tuiles(request):
    return _admin_courant(request, _entreprise_courante(request))


@login_required
@require_http_methods(["GET", "POST"])
def api_geo_tuiles_etat(request):
    """GET : état du cache de tuiles (nombre, taille, récolte en cours / en pause / hors ligne). POST {"pause": true|false} : pause ou reprise de
    la récolte (administrateur). Les boutons Zipper / Restaurer ne sont montrés qu'à l'administrateur (`admin`)."""
    racine = dossier_systeme("geo_tuiles")
    admin = bool(_admin_tuiles(request))
    if request.method == "POST":
        if not admin:
            return JsonResponse({"erreur": _i18n_t("rg2.views_geo.1")}, status=403)
        try:
            voulu = bool(json.loads(request.body or "{}").get("pause"))
        except (ValueError, AttributeError):
            return JsonResponse({"erreur": _i18n_t("rg2.views_geo.2")}, status=400)
        geo_tuiles.en_pause(racine, voulu)
    return JsonResponse({**geo_tuiles.etat(racine), "admin": admin})


@login_required
@require_http_methods(["GET"])
@limiter("geo_tuiles_zip", max_appels=5, periode_secondes=60, methodes={"GET"})
def api_geo_tuiles_zip(request):
    """Bouton « Zipper » : tout le cache de tuiles dans un zip portable (administrateur). À restaurer sur un autre serveur avec « Restaurer »."""
    if not _admin_tuiles(request):
        return JsonResponse({"erreur": _i18n_t("rg2.views_geo.1")}, status=403)
    tmp = tempfile.TemporaryFile()                      # sur disque, jamais en mémoire : le cache peut peser plusieurs Go
    geo_tuiles.zipper(dossier_systeme("geo_tuiles"), tmp)
    tmp.seek(0)
    import datetime
    return FileResponse(tmp, as_attachment=True, content_type="application/zip",
                        filename=f"tuiles_geo_{datetime.date.today().isoformat()}.zip")


@login_required
@require_http_methods(["POST"])
@limiter("geo_tuiles_restaurer", max_appels=5, periode_secondes=60, methodes={"POST"})
def api_geo_tuiles_restaurer(request):
    """Bouton « Restaurer » (administrateur) : zip de tuiles OU archive de clôture (champ « fichier »). Additif : rien n'est supprimé,
    une tuile plus récente déjà présente est gardée."""
    if not _admin_tuiles(request):
        return JsonResponse({"erreur": _i18n_t("rg2.views_geo.1")}, status=403)
    f = request.FILES.get("fichier")
    if f is None:
        return JsonResponse({"erreur": _i18n_t("rg2.views_geo.3")}, status=400)
    try:
        with zipfile.ZipFile(f) as zf:
            resultat = geo_tuiles.restaurer(dossier_systeme("geo_tuiles"), zf)
    except zipfile.BadZipFile:
        return JsonResponse({"erreur": _i18n_t("rg2.views_geo.4")}, status=400)
    return JsonResponse({"ok": True, **resultat})


@login_required
@require_http_methods(["POST"])
@limiter("geo_import", max_appels=20, periode_secondes=60, methodes={"POST"})
def api_geo_importer(request, table_id):
    """POST multipart (champ « fichier » : GeoJSON, KML, KMZ, GPX, shapefile .shp ou .zip) : crée des annotations (points, trajectoires, zones) sur cette carte.
    Mêmes droits que l'écriture d'annotations (jamais une table partagée en lecture seule) ; `apercu=1` lit le fichier sans rien enregistrer."""
    table, lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if lecture_seule:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:531:40")}, status=403)
    f = request.FILES.get("fichier")
    if f is None or f.size > geo_import.LIMITES["octets"]:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:534:40")}, status=400)
    try:
        lu = geo_import.lire(f.name, f.read())
        if request.POST.get("apercu"):
            return JsonResponse({"nb": len(lu["elements"]), "avertissements": lu["avertissements"]})
        bilan = geo_annotations.creer_lot(entreprise, request.user, table.id, lu["elements"])
    except geo_import.ErreurImport as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({**bilan, "avertissements": lu["avertissements"]})


@login_required
@require_http_methods(["GET"])
@limiter("geo_adresse", max_appels=30, periode_secondes=60, methodes={"GET"})
def api_geo_adresse(request, table_id):
    """GET ?q=… [&vue=sud,ouest,nord,est] : recherche d'adresse faite par le serveur (engine/geo_adresses.py ; `vue` = fenêtre visible de la carte, classe d'abord les lieux proches). Mêmes droits que la carte, y compris en lecture seule."""
    # Sélecteur de lieu d'un champ « géolocalisation » (formulaire agent ET client) : le client y a droit sans l'abonnement de la page carte.
    _table, _lecture_seule, _entreprise, refus = _acces(request, table_id, api=True, abonnement=False)
    if refus:
        return refus
    try:
        vue = [float(v) for v in request.GET.get("vue", "").split(",")] if request.GET.get("vue") else None   # fenêtre visible : sud,ouest,nord,est
    except ValueError:
        vue = None
    try:
        return JsonResponse({"resultats": geo_adresses.rechercher(request.GET.get("q"), vue)})
    except geo_adresses.ErreurAdresse as e:
        return JsonResponse({"erreur": str(e)}, status=400)



@login_required
@require_http_methods(["GET"])
def api_geo_archives(request, table_id):
    """Archives des positions anciennes (réservé aux administrateurs : données d'enquête). GET sans paramètre : liste des fichiers. GET ?du=&au=
    (date ISO ou millisecondes) &objet=<slug de colonne> &limite=1000 : lignes archivées. GET ?fichier=AAAA-MM-JJ.jsonl.gz : téléchargement tel quel."""
    from datetime import datetime, timezone
    from django.http import FileResponse
    from . import geo_archives
    table, lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if not _admin_courant(request, entreprise):
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:576:40")}, status=403)
    dossier = geo_archives.dossier_table(table)
    nom = request.GET.get("fichier")
    if nom:
        if not geo_archives.NOM_FICHIER.match(nom) or not (dossier / nom).is_file():
            return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:583:43")}, status=404)
        return FileResponse(open(dossier / nom, "rb"), as_attachment=True, filename=f"table{table.id}_{nom}")

    def _instant(texte):
        if not texte:
            return None
        if str(texte).isdigit():
            return int(texte)
        d = datetime.fromisoformat(str(texte))
        return int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)
    if not any(k in request.GET for k in ("du", "au", "objet", "limite")):
        return JsonResponse({"fichiers": geo_archives.fichiers(dossier), "conserve": geo_archives.garder()})
    try:
        du, au = _instant(request.GET.get("du")), _instant(request.GET.get("au"))
        limite = min(max(int(request.GET.get("limite") or 1000), 1), 20000)
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:597:40")}, status=400)
    res = geo_archives.lire(dossier, du, au, (request.GET.get("objet") or "").strip() or None, limite)
    try:
        res["colonnes"] = json.loads((dossier / "colonnes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        res["colonnes"] = {}
    return JsonResponse(res)


def _etat_veille(table):
    from . import geo_veille
    return {"processus": geo_veille.processus(), "reglages": geo_veille.reglages(table.id), "etat": geo_veille.etat(table.id),
            "colonnes": [{"slug": c.slug, "nom": c.nom} for c in geo_donnees.colonnes_geo(table)]}


@login_required
@require_http_methods(["GET", "POST"])
def api_geo_veille(request, table_id):
    """Veille continue de la table (voir engine/geo_veille.py). GET : processus, réglages, dernière évaluation et alertes. POST (administrateur) :
    {active, exclus: [slug], parametres: {seuil, horizon, sigma_mesure, mode} (ceux de la page, enregistrés pour la veille), evaluer: 1 (évalue maintenant)}."""
    from . import geo_veille, geo_surveillance
    table, lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if request.method == "GET":
        return JsonResponse(_etat_veille(table))
    if lecture_seule or not _admin_courant(request, entreprise):
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:624:40")}, status=403)
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:628:40")}, status=400)
    d = d if isinstance(d, dict) else {}
    valides = {c.slug for c in geo_donnees.colonnes_geo(table)}
    exclus = [x for x in _liste(d["exclus"]) if x in valides] if "exclus" in d else None
    parametres = _parametres(d["parametres"]) if isinstance(d.get("parametres"), dict) else None
    geo_veille.enregistrer_reglages(table.id, d.get("active") if "active" in d else None, exclus, parametres)
    if d.get("evaluer"):
        geo_surveillance.surveiller(table, force=True)
    return JsonResponse(_etat_veille(table))


@login_required
@require_http_methods(["POST"])
def api_geo_archives_rejouer(request, table_id):
    """Rejoue une période archivée comme des données vivantes : même réponse que l'analyse (trajets, rencontres, collisions, prévision), plus
    `archive` {du, au, lignes}. Corps JSON : {du, au (date ISO ou millisecondes), colonnes: [slug], limite, + paramètres d'analyse}. Administrateurs seulement."""
    from datetime import datetime, timezone
    from . import geo_archives
    table, _lecture_seule, entreprise, refus = _acces(request, table_id, api=True)
    if refus:
        return refus
    if not _admin_courant(request, entreprise):
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:650:40")}, status=403)
    try:
        d = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:654:40")}, status=400)
    d = d if isinstance(d, dict) else {}

    def _instant(texte):
        if not texte:
            return None
        if str(texte).isdigit():
            return int(texte)
        x = datetime.fromisoformat(str(texte))
        return int((x if x.tzinfo else x.replace(tzinfo=timezone.utc)).timestamp() * 1000)
    try:
        du, au = _instant(d.get("du")), _instant(d.get("au"))
    except ValueError:
        return JsonResponse({"erreur": _i18n_t("engine/views_geo.py:g:669:39")}, status=400)
    dossier = geo_archives.dossier_table(table)
    lu = geo_archives.lire(dossier, du, au, None, min(max(int(_nombre(d, "limite", int, 10, 20000) or 5000), 10), 20000))
    if not lu["lignes"]:
        return JsonResponse({"vide": True, "message": _i18n_t("engine/views_geo.py:g:671:55"), "archive": {"lignes": 0}})
    try:
        noms = json.loads((dossier / "colonnes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        noms = {}
    slugs = [x for x in _liste(d.get("colonnes")) if x and ":" not in x] or None
    resultat = geo_donnees.analyser_archive(table, lu["lignes"], noms, slugs, objets=geo_donnees.lecture(d.get("objets"), None)[0], **_parametres(d))
    if resultat is None:
        return JsonResponse({"vide": True, "message": _i18n_t("engine/views_geo.py:g:679:55"), "archive": {"lignes": len(lu["lignes"])}})
    resultat["archive"] = {"lignes": len(lu["lignes"]), "total": lu["total_trouve"], "tronque": lu["tronque"], "du": d.get("du"), "au": d.get("au")}
    return JsonResponse(resultat, encoder=_Encodeur)
