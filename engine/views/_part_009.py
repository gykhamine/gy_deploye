from engine.i18n import tf
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from ._part_004 import *  # noqa
from ._part_005 import *  # noqa
from ._part_006 import *  # noqa
from ._part_007 import *  # noqa
from ._part_008 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402

# Un worker vivant ecrit un battement de coeur (progression["maj"]) toutes les
# ~2 s ; au-dela de ce seuil, plus aucun worker ne travaille sur ce modele.
SEUIL_INACTIVITE_S = 20


def _inactif_depuis(modele):
    import time
    maj = (modele.progression or {}).get("maj")
    return max(0.0, time.time() - maj) if maj else 0.0


@login_required
def api_ia_modeles(request):
    entreprise = _entreprise_courante(request)

    if request.method == "GET":
        modeles = ModeleIA.objects.filter(entreprise=entreprise).prefetch_related("tables_source").order_by("-cree_le")
        return JsonResponse({"modeles": [_serialiser_modele_ia(m) for m in modeles]})

    if request.method == "POST":
        bloque = _interdit_non_admin(request, "modeles_ia")
        if bloque:
            return bloque
        try:
            corps = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"erreur": t("engine/views/_part_009.py:corps_json_invalide")}, status=400)
        nom = (corps.get("nom") or "").strip()
        type_modele = corps.get("type_modele") or ""
        table_ids = corps.get("tables_source") or []
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_009.py:31:45")}, status=400)
        if type_modele not in dict(ModeleIA.TYPE_CHOICES) and type_modele not in {x["code"] for x in _ia.types_modeles_disponibles()}:
            return JsonResponse({"erreur": t("engine/views/_part_009.py:33:45")}, status=400)
        if not table_ids:
            return JsonResponse({"erreur": t("engine/views/_part_009.py:35:45")}, status=400)

        # Slug sur (il nomme le dossier/fichier .gy : jamais de « ../ ») et UNIQUE par entreprise
        # (contrainte unique_together : un doublon provoquait une erreur 500 a la creation).
        from django.utils.text import slugify
        base_slug = (slugify(corps.get("slug") or "") or slugify(nom) or "modele")[:140]
        slug, n_slug = base_slug, 2
        while ModeleIA.objects.filter(entreprise=entreprise, slug=slug).exists():
            slug = f"{base_slug}-{n_slug}"
            n_slug += 1
        tables = TableDynamique.objects.filter(entreprise=entreprise, id__in=table_ids)
        if tables.count() != len(table_ids):
            return JsonResponse({"erreur": t("engine/views/_part_009.py:40:45")}, status=400)

        try:
            _ia.valider_colonnes_entree(type_modele, tables, corps.get("colonnes_entree") or [])
            _ia.valider_colonnes_sortie(type_modele, tables, corps.get("colonnes_sortie") or [])
        except DatasetError as e:
            return JsonResponse({"erreur": str(e)}, status=400)

        try:
            plage = _lire_plage_lignes(corps.get("plage_lignes"))
        except ValueError as e:
            return JsonResponse({"erreur": str(e)}, status=400)

        modele = ModeleIA.objects.create(
            entreprise=entreprise, nom=nom, slug=slug, type_modele=type_modele,
            plage_lignes=plage,
            colonne_cible=corps.get("colonne_cible", ""),
            colonnes_entree=corps.get("colonnes_entree") or [],
            colonnes_sortie=corps.get("colonnes_sortie") or [],
            parametres=corps.get("parametres") or {},
        )
        modele.tables_source.set(tables)
        return JsonResponse({"modele": _serialiser_modele_ia(modele)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_009.py:58:37")}, status=405)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_ia_modele_detail(request, modele_id):
    entreprise = _entreprise_courante(request)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)

    if request.method == "DELETE":
        modele.delete()
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")
    if "nom" in corps:
        modele.nom = corps["nom"].strip() or modele.nom
    if "colonne_cible" in corps:
        modele.colonne_cible = corps["colonne_cible"]
    if "colonnes_entree" in corps:
        modele.colonnes_entree = corps["colonnes_entree"] or []
    if "colonnes_sortie" in corps:
        modele.colonnes_sortie = corps["colonnes_sortie"] or []
    if "parametres" in corps:
        modele.parametres = corps["parametres"] or {}
    if "tables_source" in corps:
        tables = TableDynamique.objects.filter(entreprise=entreprise, id__in=corps["tables_source"])
        modele.tables_source.set(tables)
    if "visible_client" in corps:
        modele.visible_client = bool(corps["visible_client"])
    if "plage_lignes" in corps:
        try:
            modele.plage_lignes = _lire_plage_lignes(corps["plage_lignes"])
        except ValueError as e:
            return JsonResponse({"erreur": str(e)}, status=400)

    try:
        if set(corps) - {"visible_client"}:   # la simple case « visible chez le client » ne revalide pas les colonnes (sinon un ancien modèle refusait la case)
            _ia.valider_colonnes_entree(modele.type_modele, modele.tables_source.all(), modele.colonnes_entree)
            _ia.valider_colonnes_sortie(modele.type_modele, modele.tables_source.all(), modele.colonnes_sortie)
    except DatasetError as e:
        return JsonResponse({"erreur": str(e)}, status=400)

    modele.save()
    return JsonResponse({"modele": _serialiser_modele_ia(modele)})


@login_required
@require_http_methods(["POST"])
def api_ia_entrainer(request, modele_id):
    """Lance l'entrainement dans un thread de fond et repond immediatement
    (statut "entrainement") : le module IA est du numpy pur sans GPU, donc
    rapide sur les tailles de dataset gerees par le moteur de tables, mais
    un entrainement a beaucoup d'epoques peut tout de meme prendre plusieurs
    secondes — on ne bloque plus la requete HTTP pour laisser l'interface
    afficher une barre de progression en interrogeant periodiquement
    api_ia_progression pendant que ca tourne. Reutilise _entreprise_courante
    / _interdit_lecteur comme partout ailleurs dans l'API."""
    entreprise = _entreprise_courante(request)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    if modele.statut == "entrainement":
        return JsonResponse(
            {"erreur": t("engine/views/_part_009.py:117:25")}, status=409
        )

    from ..journal_ia import etat_initial

    # Contraintes de l'algorithme (hyperparametres, colonnes effectives vs
    # types acceptes) verifiees ICI, avant toute mise en file : un refus est
    # immediat, explicite et reste affiche sur la ligne du modele.
    try:
        _ia.valider_configuration(modele)
    except (DatasetError, EntrainementError) as e:
        etat = etat_initial(t("engine/views/_part_009.py:g:141:29"))
        etat["phase"] = "refuse"
        etat["journal"].append({"t": etat["journal"][0]["t"], "n": "err", "m": str(e)})
        modele.statut, modele.erreur, modele.progression = "erreur", str(e), etat
        modele.save(update_fields=["statut", "erreur", "progression", "modifie_le"])
        return JsonResponse({"erreur": str(e), "modele": _serialiser_modele_ia(modele)}, status=400)

    # Marque le statut tout de suite (avant meme la tache) pour que la
    # reponse HTTP et le premier polling voient l'etat "entrainement" ;
    # la progression initiale (phase file_attente + battement de coeur)
    # permet a l'interface de detecter l'absence de worker Celery.
    modele.statut = "entrainement"
    modele.erreur = ""
    modele.progression = etat_initial()
    modele.save(update_fields=["statut", "erreur", "progression", "modifie_le"])

    # Anciennement un threading.Thread local au process web ; desormais une
    # tache Celery (voir engine/tasks.py::entrainer_modele_ia), qui applique
    # la meme logique (lancer_entrainement + gestion des exceptions) sur un
    # worker separe.
    _taches.entrainer_modele_ia.delay(modele.id)
    return JsonResponse({"modele": _serialiser_modele_ia(modele)}, status=202)


@login_required
@require_http_methods(["POST"])
def api_ia_pause(request, modele_id):
    """Met l'entrainement en pause a la fin de l'epoque courante."""
    entreprise = _entreprise_courante(request)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    p = modele.progression or {}
    if modele.statut != "entrainement" or p.get("phase") not in ("entrainement", "pause") \
            or (p.get("epochs_total") or 0) <= 1:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:177:40")}, status=409)
    modele.demande_pause = True
    modele.save(update_fields=["demande_pause", "modifie_le"])
    return JsonResponse({"modele": _serialiser_modele_ia(modele)})


@login_required
@require_http_methods(["POST"])
def api_ia_reprendre(request, modele_id):
    """Reprend un entrainement mis en pause."""
    entreprise = _entreprise_courante(request)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    if modele.statut != "entrainement":
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:193:40")}, status=409)
    modele.demande_pause = False
    modele.save(update_fields=["demande_pause", "modifie_le"])
    return JsonResponse({"modele": _serialiser_modele_ia(modele)})


@login_required
@require_http_methods(["POST"])
def api_ia_finetuner(request, modele_id):
    """Affine le modele deja entraine sur les lignes ajoutees depuis son dernier
    entrainement (corps JSON optionnel : {epochs, lr})."""
    entreprise = _entreprise_courante(request)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    if modele.statut == "entrainement":
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:210:40")}, status=409)
    if not _ia.finetune_supporte(modele.type_modele):
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:212:40")}, status=400)
    if not modele.fichier_modele:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:214:40")}, status=400)
    if _ia.compter_nouvelles_lignes(modele) == 0:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:216:40")}, status=400)
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        corps = {}
    options = {}
    try:
        if corps.get("epochs"):
            options["epochs"] = max(1, int(corps["epochs"]))
        if corps.get("lr"):
            options["lr"] = float(corps["lr"])
    except (TypeError, ValueError):
        return JsonResponse({"erreur": _gt("rg8.msg.017")}, status=400)

    from ..journal_ia import etat_initial
    modele.statut, modele.erreur, modele.demande_pause = "entrainement", "", False
    modele.progression = etat_initial(t("engine/views/_part_009.py:g:232:39"))
    modele.save(update_fields=["statut", "erreur", "demande_pause", "progression", "modifie_le"])
    _taches.affiner_modele_ia.delay(modele.id, options)
    return JsonResponse({"modele": _serialiser_modele_ia(modele)}, status=202)


@login_required
@require_http_methods(["GET", "POST"])
def api_ia_features(request, modele_id):
    """Feature engineering : table de features (colonnes choisies du modele) en COPIE de travail, avec lignes vides /
    aberrantes signalees. GET ?table=&filtre=&page= : apercu. POST {table_id, ajouter:[ids], retirer:[ids],
    exclure_filtre:"vides"|"aberrantes"|"signalees", reinitialiser:true} : choisit les lignes a ecarter. La vraie table
    n'est jamais modifiee ; les lignes ecartees sont ignorees a l'entrainement ET au fine-tuning."""
    from .. import ia_features as _feat
    entreprise = _entreprise_courante(request)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    corps = {}
    if request.method == "POST":
        if modele.statut == "entrainement":
            return JsonResponse({"erreur": t("engine/views/_part_009.py:fe_en_cours")}, status=409)
        try:
            corps = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"erreur": t("engine/views/_part_009.py:corps_json_invalide")}, status=400)
    src = corps if request.method == "POST" else request.GET
    tables = list(modele.tables_source.all())
    if not tables:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:fe_sans_table")}, status=400)
    try:
        table_id = int(src.get("table_id") or src.get("table") or tables[0].id)
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_009.py:fe_table_inconnue")}, status=400)
    table = next((x for x in tables if x.id == table_id), None)
    if table is None:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:fe_table_inconnue")}, status=400)
    if request.method == "POST":
        _feat.mettre_a_jour(
            modele, table, ajouter=corps.get("ajouter") or [], retirer=corps.get("retirer") or [],
            exclure_filtre=corps.get("exclure_filtre"), reinitialiser=bool(corps.get("reinitialiser")))
    try:
        page = int(src.get("page") or 0)
    except (TypeError, ValueError):
        page = 0
    donnees = _feat.apercu(modele, table, src.get("filtre") or "toutes", page)
    donnees["tables"] = [{"id": x.id, "nom": x.nom} for x in tables]
    return JsonResponse({"features": donnees, "modele": _serialiser_modele_ia(modele)})


@login_required
@require_http_methods(["POST"])
def api_ia_annuler_entrainement(request, modele_id):
    """Demande l'annulation d'un entrainement en cours : pose juste le
    drapeau demande_annulation, lu a chaque epoque par _CallbackAnnulation
    dans le thread de fond (voir ia_integration.py) qui arrete proprement
    le Trainer des que possible. Ne bloque pas la requete : l'interface
    continue de suivre l'etat via le polling api_ia_progression existant,
    qui refletera le statut "annule" une fois le thread arrete."""
    entreprise = _entreprise_courante(request)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    if modele.statut != "entrainement":
        return JsonResponse(
            {"erreur": t("engine/views/_part_009.py:152:25")}, status=409
        )
    inactif = _inactif_depuis(modele)
    if inactif > SEUIL_INACTIVITE_S:
        # Aucun worker ne travaille (jamais demarre, ou mort en cours de route) :
        # le drapeau ne serait jamais lu -> on termine l'annulation directement.
        from ..journal_ia import Journal
        modele.statut, modele.erreur, modele.demande_annulation = "annule", "", False
        modele.save(update_fields=["statut", "erreur", "demande_annulation", "modifie_le"])
        Journal(modele.id).log(tf("engine/views/_part_009.py:g:263:32", v1=int(inactif)),
                               "warn", phase="annule")
        modele.refresh_from_db()
        return JsonResponse({"modele": _serialiser_modele_ia(modele)})
    modele.demande_annulation = True
    modele.save(update_fields=["demande_annulation", "modifie_le"])
    return JsonResponse({"modele": _serialiser_modele_ia(modele)})


@login_required
def api_ia_progression(request, modele_id):
    """Etat live d'un entrainement en cours, pour la barre de progression :
    interroge par polling depuis l'interface pendant que api_ia_entrainer
    tourne en tache de fond. Lecture seule, pas de _interdit_lecteur (les
    clients lecteurs peuvent suivre un entrainement sans le lancer)."""
    entreprise = _entreprise_courante(request)
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    return JsonResponse({
        "statut": modele.statut,
        "erreur": modele.erreur,
        "progression": modele.progression,
        "metriques": modele.metriques,
        "inactif_depuis": _inactif_depuis(modele) if modele.statut == "entrainement" else 0,
    })


@login_required
@require_http_methods(["POST"])
def api_ia_inferer(request, modele_id):
    entreprise = _entreprise_courante(request)
    # Les lecteurs (clients) ont accès à l'inférence IA si abonné
    membre_ia = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if membre_ia and membre_ia.role == "lecteur":
        from ..wallet import abonnement_valide
        if not abonnement_valide(membre_ia, "ia"):
            return JsonResponse({"erreur": t("engine/views/_part_009.py:184:45"), "service": "ia", "redirect": "/espace-client/paiement/?service=ia"}, status=402)
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    corps = json.loads(request.body or "{}")
    entree = corps.get("entree")
    if entree is None:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:192:41")}, status=400)

    log = InferenceIA(modele=modele, entree=entree)
    try:
        sortie = _ia.lancer_inference(modele, entree)
        log.sortie = sortie
        log.save()
        # Meme convention que api_lignes (POST /api/tables/<id>/lignes/,
        # utilise par client.html) : une ressource creee est renvoyee sous
        # une cle nommee (ici "inference", la-bas "ligne"), avec son id et
        # sa date de creation, statut 201 — adapte au contexte inference
        # (pas de "ligne" ici, mais un log InferenceIA qui vient d'etre
        # cree). "sortie" reste imbriquee (meme forme que dans l'historique
        # /api/ia/modeles/<id>/inferences/) pour ne rien casser cote appelant
        # qui lisait deja `h.sortie` sur les entrees d'historique.
        return JsonResponse({
            "inference": {
                "id": log.id,
                "entree": log.entree,
                "sortie": sortie,
                "cree_le": log.cree_le.isoformat(),
            },
        }, status=201)
    except EntrainementError as e:
        log.erreur = str(e)
        log.save()
        return JsonResponse({"erreur": str(e)}, status=400)
    except Exception as e:
        log.erreur = str(e)
        log.save()
        return JsonResponse({"erreur": tf("engine/views/_part_009.py:g:336:40", v1=e)}, status=500)


@login_required
def api_ia_inferences(request, modele_id):
    entreprise = _entreprise_courante(request)
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    logs = modele.inferences.all()[:30]
    return JsonResponse({"inferences": [
        {"id": l.id, "entree": l.entree, "sortie": l.sortie, "erreur": l.erreur, "cree_le": l.cree_le.isoformat()}
        for l in logs
    ]})


# ══════════════════════════════════════════════════════════════════════════════
# COMPTEUR DE TOKENS
# ══════════════════════════════════════════════════════════════════════════════

@login_required
@require_http_methods(["GET"])
def api_tokens_statut(request):
    """Retourne l'état courant du compteur de tokens de l'entreprise."""
    entreprise = _entreprise_courante(request)
    compteur = CompteurTokens.pour_entreprise(entreprise)
    return JsonResponse({
        "tokens_total":  compteur.tokens_total,
        "tokens_entree": compteur.tokens_entree,
        "tokens_sortie": compteur.tokens_sortie,
        "periode":       compteur.periode,
        "debut_periode": compteur.debut_periode.isoformat(),
        "alerte_seuil":  compteur.alerte_seuil,
        "alerte_active": compteur.alerte_active,
    })


@login_required
@require_http_methods(["POST"])
def api_tokens_configurer(request):
    """Configure le compteur : période et seuil d'alerte."""
    entreprise = _entreprise_courante(request)
    # seul admin/agent peut configurer
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"erreur": t("engine/views/_part_009.py:267:41")}, status=403)

    data = json.loads(request.body)
    compteur, _ = CompteurTokens.objects.get_or_create(entreprise=entreprise, utilisateur=None)

    periode = data.get("periode")
    if periode and periode in dict(CompteurTokens.PERIODES):
        compteur.periode = periode
    seuil = data.get("alerte_seuil")
    if seuil is not None:
        compteur.alerte_seuil = max(0, int(seuil))

    compteur.save(update_fields=["periode", "alerte_seuil"])
    return JsonResponse({"ok": True, "periode": compteur.periode, "alerte_seuil": compteur.alerte_seuil})


@login_required
@require_http_methods(["POST"])
def api_tokens_reinitialiser(request):
    """Réinitialise manuellement le compteur (admin uniquement)."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    try:
        actions.executer_action("reinitialiser_tokens", membre, entreprise)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:294:41")}, status=403)
    return JsonResponse({"ok": True})


# ══════════════════════════════════════════════════════════════════════════════
# IMPORT FICHIER ASYNC — suivi de la tâche
# ══════════════════════════════════════════════════════════════════════════════

@login_required
@require_http_methods(["GET"])
def api_statut_import(request, tache_id):
    """Polling du statut d'une tâche d'import fichier."""
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TacheImport, id=tache_id, entreprise=entreprise)
    data = {
        "diagnostic": "",
        "tache_id":   tache.id,
        "statut":     tache.statut,
        "progression": tache.progression,
        "source":     tache.source,
        "nom_fichier": tache.nom_fichier,
        "erreur":     tache.erreur,
        "fini":       tache.statut in ("termine", "echec"),
        "groupe_id":  tache.groupe_id,
    }
    if tache.statut == "termine" and tache.table:
        data["table"] = _serialiser_table(tache.table)
    if tache.statut == "termine" and tache.groupe_id:
        data["groupe"] = _serialiser_groupe_tables(tache.groupe)
    return JsonResponse(data)


# ══════════════════════════════════════════════════════════════════════════════
# CONNEXIONS BASES DE DONNÉES EXTERNES — CRUD + test + tables distantes + import
# ══════════════════════════════════════════════════════════════════════════════

@login_required
@require_http_methods(["GET", "POST"])
def api_connexions_externes(request):
    """GET : liste des connexions de l'entreprise. POST : création."""
    entreprise = _entreprise_courante(request)

    if request.method == "GET":
        connexions = ConnexionExterne.objects.filter(entreprise=entreprise)
        moteurs = [{"id": k, "libelle": l, "port": ConnexionExterne.PORTS_DEFAUT[k]} for k, l in ConnexionExterne.MOTEURS]
        return JsonResponse({"connexions": [_serialiser_connexion_externe(c) for c in connexions], "moteurs": moteurs})

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:346:41")}, status=400)

    nom = (corps.get("nom") or "").strip()
    hote = (corps.get("hote") or "").strip()
    nom_base = (corps.get("nom_base") or "").strip()
    moteur = corps.get("moteur") or ConnexionExterne.MOTEUR_POSTGRESQL
    if not nom or not hote or not nom_base:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:353:41")}, status=400)
    if moteur not in dict(ConnexionExterne.MOTEURS):
        return JsonResponse({"erreur": _gtf("engine/views/_part_009.py:532:40", v1=(moteur))}, status=400)

    try:
        connexion = ConnexionExterne.objects.create(
            entreprise=entreprise, nom=nom, moteur=moteur, hote=hote,
            port=corps.get("port") or None, nom_base=nom_base,
            utilisateur=corps.get("utilisateur") or "",
            mot_de_passe=corps.get("mot_de_passe") or "",
            ssl_actif=bool(corps.get("ssl_actif")),
            parametres_supplementaires=corps.get("parametres_supplementaires") or {},
        )
    except IntegrityError:
        return JsonResponse({"erreur": tf("engine/views/_part_009.py:g:486:40", v1=nom)}, status=400)

    return JsonResponse({"connexion": _serialiser_connexion_externe(connexion)}, status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_connexion_externe_detail(request, connexion_id):
    entreprise = _entreprise_courante(request)
    connexion = get_object_or_404(ConnexionExterne, id=connexion_id, entreprise=entreprise)

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        connexion.delete()
        return JsonResponse({"ok": True})

    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:389:41")}, status=400)

    champs_modifiables = (
        "nom", "moteur", "hote", "port", "nom_base", "utilisateur",
        "ssl_actif", "parametres_supplementaires", "actif",
    )
    for champ in champs_modifiables:
        if champ in corps:
            setattr(connexion, champ, corps[champ])
    # Mot de passe : uniquement modifié si explicitement fourni et non vide,
    # pour ne jamais l'effacer accidentellement lors d'une mise à jour partielle.
    if corps.get("mot_de_passe"):
        connexion.mot_de_passe = corps["mot_de_passe"]
    try:
        connexion.full_clean(exclude=["entreprise"])
        connexion.save()
    except IntegrityError:
        return JsonResponse({"erreur": tf("engine/views/_part_009.py:g:525:40", v1=connexion.nom)}, status=400)
    except Exception as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)

    return JsonResponse({"connexion": _serialiser_connexion_externe(connexion)})


@login_required
@require_http_methods(["POST"])
def api_connexion_externe_tester(request, connexion_id):
    """Tente une connexion réelle (hôte/IP + port configurés) et renvoie
    immédiatement le résultat — synchrone, pas de tâche Celery ici : un
    test de connexion doit être quasi instantané ou échouer proprement."""
    entreprise = _entreprise_courante(request)
    connexion = get_object_or_404(ConnexionExterne, id=connexion_id, entreprise=entreprise)

    from ..connexion_externe import ErreurConnexionExterne, tester_connexion
    try:
        tester_connexion(connexion)
    except ErreurConnexionExterne as exc:
        return JsonResponse({"ok": False, "erreur": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "erreur": tf("engine/views/_part_009.py:g:547:53", v1=exc)}, status=400)
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["GET"])
def api_connexion_externe_tables(request, connexion_id):
    """Liste les tables/vues SQL ou collections MongoDB accessibles, pour
    que l'utilisateur choisisse quoi importer."""
    entreprise = _entreprise_courante(request)
    connexion = get_object_or_404(ConnexionExterne, id=connexion_id, entreprise=entreprise)

    from ..connexion_externe import ErreurConnexionExterne, lister_tables_distantes
    try:
        tables = lister_tables_distantes(connexion)
    except ErreurConnexionExterne as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erreur": tf("engine/views/_part_009.py:g:565:40", v1=exc)}, status=400)
    return JsonResponse({"tables": tables})


@login_required
@require_http_methods(["GET"])
def api_connexion_externe_apercu(request, connexion_id):
    """Aperçu (colonnes + quelques lignes) d'une table/collection distante
    avant de lancer l'import complet."""
    entreprise = _entreprise_courante(request)
    connexion = get_object_or_404(ConnexionExterne, id=connexion_id, entreprise=entreprise)
    nom_table = request.GET.get("table") or ""
    if not nom_table:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:459:41")}, status=400)

    from ..connexion_externe import ErreurConnexionExterne, apercu_table_distante
    try:
        colonnes, lignes = apercu_table_distante(connexion, nom_table, limite=50)
    except ErreurConnexionExterne as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"erreur": tf("engine/views/_part_009.py:g:586:40", v1=exc)}, status=400)
    return JsonResponse({"colonnes": colonnes, "lignes": lignes})


@login_required
@require_http_methods(["POST"])
def api_connexion_externe_importer(request, connexion_id):
    """Lance en tâche de fond (Celery) l'import complet d'une table/
    collection distante — même pattern de polling que api_importer_fichier :
    répond immédiatement avec {tache_id}."""
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    entreprise = _entreprise_courante(request)
    connexion = get_object_or_404(ConnexionExterne, id=connexion_id, entreprise=entreprise)

    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        corps = {}
    nom_table = (corps.get("table") or "").strip()
    if not nom_table:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:489:41")}, status=400)

    tache = TacheImport.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        source=TacheImport.SOURCE_BASE_EXTERNE,
        nom_fichier=f"{connexion.nom} — {nom_table}",
        connexion_externe=connexion,
        table_distante=nom_table,
        statut="en_attente",
    )
    _taches.synchroniser_connexion_externe_async.delay(tache.id, connexion.id, nom_table)

    return JsonResponse({"tache_id": tache.id, "nom_fichier": tache.nom_fichier}, status=202)


# ══════════════════════════════════════════════════════════════════════════════
# GROUPES DE TABLES — rattachement manuel de tables indépendantes comme un
# seul ensemble logique (« grande table »), même mécanisme que le découpage
# automatique — voir GroupeTables dans models.py.
# ══════════════════════════════════════════════════════════════════════════════

def _recalculer_calculs_tables(tables):
    """Composer/défaire un groupe change les lignes vues par les calculs (calculs._dataframe_table_source,
    groupe=True) : on les recalcule aussitôt, sinon les résultats restent périmés jusqu'à la prochaine saisie."""
    from ..calculs import recalculer_lignes_pour_table_source
    for t in tables:
        t.refresh_from_db()
        recalculer_lignes_pour_table_source(t)


def _refus_groupe_fusionne(groupe):
    """Tant qu'un groupe est fusionné en une table, ses parties sont figées (rattacher, détacher, dissoudre) : défusionner d'abord."""
    if groupe is not None and groupe.est_fusionne:
        return JsonResponse({"erreur": _gt("rg5.msg.066")}, status=400)
    return None


@login_required
@require_http_methods(["GET", "POST"])
def api_groupes_tables(request):
    entreprise = _entreprise_courante(request)

    if request.method == "GET":
        groupes = GroupeTables.objects.filter(entreprise=entreprise)
        return JsonResponse({"groupes": [_serialiser_groupe_tables(g) for g in groupes]})

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:535:41")}, status=400)

    nom = (corps.get("nom") or "").strip()
    if not nom:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:539:41")}, status=400)

    table_ids = corps.get("table_ids") or []
    tables = list(TableDynamique.objects.filter(id__in=table_ids, entreprise=entreprise)) if table_ids else []
    from .. import hierarchie
    erreur_groupe = (hierarchie.erreur_entree_groupe(None, tables) or _erreur_structure_groupe(tables)
                     or _erreur_partage_entre_tables([t.id for t in tables]))
    if erreur_groupe:  # même structure, et aucun partage de colonne/cellule entre elles
        return JsonResponse({"erreur": erreur_groupe}, status=400)

    groupe = GroupeTables.objects.create(
        entreprise=entreprise, nom=nom,
        description=corps.get("description", ""),
        cree_automatiquement=False,
    )

    if tables:
        hierarchie.ranger_parties(groupe, tables)   # les tables pleines d'abord, la non pleine en dernier
        for table in tables:
            table.refresh_from_db()
        hierarchie.placer_tables_dans_groupe(groupe, tables)  # plus visibles seules : seulement dans le groupe
        _recalculer_calculs_tables(tables[:1])

    return JsonResponse({"groupe": _serialiser_groupe_tables(groupe)}, status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_groupe_tables_detail(request, groupe_id):
    """PATCH : renommer/décrire le groupe. DELETE : dissout le groupe (les tables membres redeviennent indépendantes, rien n'est
    supprimé) ; avec ?donnees=1, supprime le groupe ET toutes ses tables avec leurs données (voir engine/suppression_masse.py)."""
    entreprise = _entreprise_courante(request)
    groupe = get_object_or_404(GroupeTables, id=groupe_id, entreprise=entreprise)

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE" and request.GET.get("donnees") == "1":
        from .. import suppression_masse, tables_systeme
        tables = suppression_masse.tables_du_groupe(groupe)
        for tb in tables:
            refus_systeme = tables_systeme.refus_ecriture(tb)
            if refus_systeme:
                return refus_systeme
        reponse, statut = suppression_masse.lancer(entreprise, request.user, tables, groupe)
        return JsonResponse(reponse, status=statut)

    if request.method == "DELETE":
        refus = _refus_groupe_fusionne(groupe)
        if refus:
            return refus
        membres = list(groupe.tables.all())
        from .. import hierarchie
        hierarchie.sortir_tables_du_groupe(groupe, membres)  # rangé dans un conteneur : les tables y restent
        groupe.delete()  # TableDynamique.groupe est SET_NULL : les tables survivent
        _recalculer_calculs_tables(membres)
        return JsonResponse({"ok": True})

    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:585:41")}, status=400)
    renommees = []
    if "nom" in corps and str(corps["nom"]) != groupe.nom:
        # Renommer le groupe : ses tables enfants (parties, table fusionnée) suivent directement (decoupage_table.renommer_groupe).
        from .. import decoupage_table as _dec
        from ..models import Bloc
        ancien = groupe.nom
        try:
            _, renommees = _dec.renommer_groupe(groupe, corps["nom"])
        except _dec.DecoupageError as e:
            return JsonResponse({"erreur": _gtf(e.cle, **e.params), "code": e.cle}, status=400)
        Bloc.ajouter_transaction(groupe.entreprise_id, {"type": "renommer_groupe_tables", "groupe_id": groupe.id, "ancien_nom": ancien,
                                                        "nom": groupe.nom, "tables": [{"table_id": t.id, "ancien_nom": avant, "nom": t.nom} for t, avant in renommees]})
    if "description" in corps:
        groupe.description = corps["description"]
        groupe.save(update_fields=["description"])
    return JsonResponse({"groupe": _serialiser_groupe_tables(groupe), "tables_renommees": [{"id": t.id, "nom": t.nom} for t, _ in renommees]})


@login_required
@require_http_methods(["POST"])
def api_groupe_tables_rattacher(request, groupe_id):
    """Rattache une ou plusieurs tables INDÉPENDANTES existantes à ce
    groupe (ou les en détache si table_ids est vide pour ces tables déjà
    membres) — c'est le mécanisme manuel demandé en complément du
    découpage automatique : lier des tables déjà là comme un seul ensemble."""
    entreprise = _entreprise_courante(request)
    groupe = get_object_or_404(GroupeTables, id=groupe_id, entreprise=entreprise)

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:610:41")}, status=400)

    refus = _refus_groupe_fusionne(groupe)
    if refus:
        return refus
    table_ids = corps.get("table_ids") or []
    tables = list(TableDynamique.objects.filter(id__in=table_ids, entreprise=entreprise))
    if not tables:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:615:41")}, status=400)
    from .. import hierarchie
    erreur_groupe = (
        hierarchie.erreur_entree_groupe(groupe, tables)
        or _erreur_structure_groupe(list(groupe.tables.exclude(id__in=[t.id for t in tables])[:1]) + tables)
        or _erreur_partage_entre_tables(list(groupe.tables.values_list("id", flat=True)) + [t.id for t in tables])
    )
    if erreur_groupe:
        return JsonResponse({"erreur": erreur_groupe}, status=400)

    hierarchie.ranger_parties(groupe, tables)   # une table pleine se place avant la dernière : celle qui n'est pas pleine reste la dernière
    for table in tables:
        table.refresh_from_db()
    hierarchie.placer_tables_dans_groupe(groupe, tables)
    _recalculer_calculs_tables(tables[:1])

    return JsonResponse({"groupe": _serialiser_groupe_tables(groupe)})


@login_required
@require_http_methods(["POST"])
def api_table_detacher_groupe(request, table_id):
    """Détache une table de son GroupeTables (elle redevient autonome) sans
    la supprimer ni toucher à ses données."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    refus = _refus_groupe_fusionne(table.groupe if table.groupe_id else None)
    if refus:
        return refus
    reste = table.groupe.tables.exclude(id=table.id).first() if table.groupe_id else None
    if table.groupe_id:
        from .. import hierarchie
        hierarchie.sortir_tables_du_groupe(table.groupe, [table])  # groupe rangé : la table reste dans ce conteneur
    table.groupe = None
    table.partie_index = None
    table.partie_precedente = None   # chaîne du groupe quitté (déjà coupée en base par sortir_tables_du_groupe)
    table.partie_total = None
    table.save(update_fields=["groupe", "partie_index", "partie_precedente", "partie_total"])
    _recalculer_calculs_tables([table] + ([reste] if reste else []))
    return JsonResponse({"table": _serialiser_table(table)})


@login_required
@require_http_methods(["POST"])
def api_groupe_tables_fusionner(request, groupe_id):
    """Fusionne toutes les tables du groupe en UNE table (donc un seul fichier Parquet). Les tables d'origine et leurs fichiers ne sont
    pas supprimés : on peut défusionner (voir engine/groupe_fusion.py). Corps JSON optionnel : {"nom": "nom de la table fusionnée"}."""
    entreprise = _entreprise_courante(request)
    groupe = get_object_or_404(GroupeTables, id=groupe_id, entreprise=entreprise)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        corps = {}
    from .. import groupe_fusion
    nom = (corps.get("nom") or "").strip() or None
    erreur = groupe_fusion.erreur_fusion(groupe)
    if erreur:
        return JsonResponse({"erreur": erreur}, status=400)
    if not groupe_fusion.prendre_verrou(groupe.id):
        return JsonResponse({"erreur": _gt("rg5.msg.067")}, status=409)
    verrou_confie_a_la_tache = False
    try:
        # Grosse fusion (FUSION_ASYNC_A_PARTIR_DE lignes, 50 000 par défaut) ou {"async": true} : tâche de fond suivie comme un import ;
        # {"async": false} force le mode synchrone. Si Celery est injoignable, on fusionne dans la requête plutôt que d'échouer.
        demande = corps.get("async")
        seuil = getattr(dj_settings, "FUSION_ASYNC_A_PARTIR_DE", 50000)
        asynchrone = bool(demande) if demande is not None else sum(
            t.enregistrements.count() for t in groupe_fusion.parties_du_groupe(groupe)) >= seuil
        if asynchrone:
            from .. import tasks as _t
            tache = TacheImport.objects.create(entreprise=entreprise, utilisateur=request.user, nom_fichier=groupe.nom[:255], statut="en_attente")
            try:
                _t.fusionner_groupe_async.delay(tache.id, groupe.id, nom)
                verrou_confie_a_la_tache = True   # la tâche libère le verrou à sa fin
                return JsonResponse({"tache_id": tache.id, "nom_fichier": groupe.nom, "asynchrone": True}, status=202)
            except Exception:
                tache.delete()
        table = groupe_fusion.fusionner(groupe, nom=nom)
    except groupe_fusion.FusionImpossible as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    finally:
        if not verrou_confie_a_la_tache:
            groupe_fusion.liberer_verrou(groupe.id)
    return JsonResponse({
        "groupe": _serialiser_groupe_tables(groupe), "table": _serialiser_table(table),
        "nb_lignes": getattr(table, "nb_lignes_fusion", None), "avertissements": getattr(table, "avertissements_fusion", []),
    }, status=201)


@login_required
@require_http_methods(["POST"])
def api_groupe_tables_defusionner(request, groupe_id):
    """Défusionne : supprime la table fusionnée et son fichier, rend les tables d'origine (restées intactes). Si la table fusionnée a été
    modifiée depuis la fusion, la réponse est 409 {"modifiee": true} tant que le corps JSON ne contient pas {"confirmer": true}."""
    entreprise = _entreprise_courante(request)
    groupe = get_object_or_404(GroupeTables, id=groupe_id, entreprise=entreprise)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        corps = {}
    from .. import groupe_fusion
    try:
        parties = groupe_fusion.defusionner(groupe, forcer=bool(corps.get("confirmer")))
    except groupe_fusion.ModificationsPerdues as exc:
        return JsonResponse({"erreur": str(exc), "modifiee": True}, status=409)
    except groupe_fusion.FusionImpossible as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    _recalculer_calculs_tables(parties[:1])
    return JsonResponse({"groupe": _serialiser_groupe_tables(groupe)})


def _serialiser_conteneur(c):
    """Un conteneur et ses membres : tables autonomes + groupes (un groupe compte pour UNE table ;
    `slug` = celle de sa première partie, ancre stable : tout traitement agrégé couvre déjà le groupe entier)."""
    groupes = []
    for g in c.groupes.all():
        premiere = g.tables.order_by(F("partie_index").asc(nulls_last=True), "id").first()
        groupes.append({"id": g.id, "nom": g.nom, "nb_tables": g.tables.count(),
                        "slug": premiere.slug if premiere else ""})
    return {
        "id": c.id, "nom": c.nom, "description": c.description,
        "tables": [{"id": t.id, "nom": t.nom, "slug": t.slug} for t in c.tables.filter(groupe__isnull=True)],
        "groupes": groupes,
    }


def _erreur_membres_conteneur(entreprise, corps, conteneur=None):
    """Un membre (table ou groupe) ne se range que dans UN conteneur : sinon il réapparaît ailleurs (doublons)."""
    autres = ConteneurTables.objects.filter(entreprise=entreprise)
    if conteneur:
        autres = autres.exclude(pk=conteneur.pk)
    noms = set()
    for cle, champ in (("table_ids", "tables"), ("groupe_ids", "groupes")):
        ids = corps.get(cle) or []
        if ids:
            noms.update(autres.filter(**{champ + "__in": ids}).values_list("nom", flat=True))
    if noms:
        return t("engine/views/_part_009.py:g:809:16") + ", ".join(sorted(noms)) + t("engine/views/_part_009.py:g:809:83")


def _appliquer_membres_conteneur(conteneur, entreprise, corps):
    """Remplace les membres du conteneur par table_ids/groupe_ids reçus (clés absentes : inchangées).
    Une table déjà dans un groupe ne se range pas seule : c'est le groupe qui se range."""
    if "table_ids" in corps:
        tables = TableDynamique.objects.filter(id__in=corps["table_ids"] or [], entreprise=entreprise, groupe__isnull=True)
        conteneur.tables.set(tables)
    if "groupe_ids" in corps:
        conteneur.groupes.set(GroupeTables.objects.filter(id__in=corps["groupe_ids"] or [], entreprise=entreprise))
    # Un groupe rangé ici emporte ses tables : elles ne restent pas rangées seules (ni ici ni ailleurs).
    ids_groupees = TableDynamique.objects.filter(entreprise=entreprise, groupe__in=conteneur.groupes.all()).values_list("id", flat=True)
    for c in ConteneurTables.objects.filter(entreprise=entreprise, tables__in=list(ids_groupees)).distinct():
        c.tables.remove(*ids_groupees)


@login_required
@require_http_methods(["GET", "POST"])
def api_conteneurs_tables(request):
    entreprise = _entreprise_courante(request)
    if request.method == "GET":
        from .. import hierarchie
        hierarchie.normaliser(entreprise)  # corrige d'anciennes données (doublons table/groupe/conteneur)
        qs = ConteneurTables.objects.filter(entreprise=entreprise).prefetch_related("tables", "groupes")
        return JsonResponse({"conteneurs": [_serialiser_conteneur(c) for c in qs]})
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:841:40")}, status=400)
    nom = (corps.get("nom") or "").strip()
    if not nom:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:844:40")}, status=400)
    erreur = _erreur_membres_conteneur(entreprise, corps)
    if erreur:
        return JsonResponse({"erreur": erreur}, status=400)
    conteneur = ConteneurTables.objects.create(entreprise=entreprise, nom=nom, description=corps.get("description", ""))
    _appliquer_membres_conteneur(conteneur, entreprise, corps)
    return JsonResponse({"conteneur": _serialiser_conteneur(conteneur)}, status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_conteneur_tables_detail(request, conteneur_id):
    """PATCH : renommer et/ou remplacer les membres. DELETE : supprime le conteneur (tables et groupes conservés)."""
    entreprise = _entreprise_courante(request)
    conteneur = get_object_or_404(ConteneurTables, id=conteneur_id, entreprise=entreprise)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    if request.method == "DELETE":
        conteneur.delete()
        return JsonResponse({"ok": True})
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:g:868:40")}, status=400)
    erreur = _erreur_membres_conteneur(entreprise, corps, conteneur)
    if erreur:
        return JsonResponse({"erreur": erreur}, status=400)
    if (corps.get("nom") or "").strip():
        conteneur.nom = corps["nom"].strip()
    if "description" in corps:
        conteneur.description = corps["description"] or ""
    conteneur.save(update_fields=["nom", "description"])
    _appliquer_membres_conteneur(conteneur, entreprise, corps)
    return JsonResponse({"conteneur": _serialiser_conteneur(conteneur)})


# ══════════════════════════════════════════════════════════════════════════════
# TÂCHES ACTIVES — bannière de reprise à la reconnexion
# ══════════════════════════════════════════════════════════════════════════════

@login_required
@require_http_methods(["GET"])
def api_taches_actives(request):
    """Retourne toutes les opérations longues en cours ou récemment terminées
    pour l'entreprise : analyses (DemandeAgent), documents (DocumentGenere),
    imports (TacheImport). Utilisé au chargement de page pour afficher le
    bandeau de reprise des travaux en cours."""
    entreprise = _entreprise_courante(request)

    # Imports en cours ou terminés depuis < 5 min
    from django.utils import timezone
    from datetime import timedelta
    seuil_recent = timezone.now() - timedelta(minutes=5)

    imports = TacheImport.objects.filter(
        entreprise=entreprise,
        statut__in=("en_attente", "en_cours"),
    ).order_by().union(
        TacheImport.objects.filter(
            entreprise=entreprise,
            statut__in=("termine", "echec"),
            termine_le__gte=seuil_recent,
        ).order_by()
    ).order_by("-cree_le")[:20]

    analyses = DemandeAgent.objects.filter(
        table_cible__entreprise=entreprise,
        statut__in=("en_cours", "generation"),
    ).select_related("table_cible")[:20]

    documents = DocumentGenere.objects.filter(
        table_cible__entreprise=entreprise,
        statut__in=("en_cours", "generation"),
    ).select_related("table_cible")[:20]

    return JsonResponse({
        "imports": [
            {
                "id": t.id,
                "nom_fichier": t.nom_fichier,
                "statut": t.statut,
                "progression": t.progression,
                "erreur": t.erreur,
                "table_id": t.table_id,
            } for t in imports
        ],
        "analyses": [
            {
                "id": a.id,
                "statut": a.statut,
                "table_id": a.table_cible_id,
                "table_nom": a.table_cible.nom if a.table_cible else "",
            } for a in analyses
        ],
        "documents": [
            {
                "id": d.id,
                "statut": d.statut,
                "table_id": d.table_cible_id,
                "table_nom": d.table_cible.nom if d.table_cible else "",
            } for d in documents
        ],
    })


# ── Views fusionnées depuis gscode ──



def _interdit_modele_non_expose(request, modele):
    """403 si un client (lecteur) n'a pas accès à toutes les tables sources du modèle
    (même règle d'exposition que _interdit_table_non_exposee), sinon None."""
    for table in modele.tables_source.all():
        bloque = _interdit_table_non_exposee(request, table)
        if bloque:
            return bloque
    return None


def _serialiser_tache(tache, voir_code=False):
    """Représentation JSON d'une TachePlanifiee (planifiée ou conditionnelle —
    voir models.TachePlanifiee.mode_declenchement / engine/declencheurs.py).

    `voir_code` : le script (et son chemin disque) n'est renvoyé qu'au
    super-administrateur ; masqué par défaut pour tout autre utilisateur."""
    iso = lambda d: d.isoformat() if d else None
    return {
        "id": tache.id, "nom": tache.nom, "instructions": tache.instructions,
        "statut": tache.statut, "erreur": tache.erreur, "resume": tache.resume,
        "intervalle_type": tache.intervalle_type, "intervalle_valeur": tache.intervalle_valeur,
        "actif": tache.actif, "fichier_script": tache.fichier_script if voir_code else "",
        "modifie_manuellement": tache.modifie_manuellement, "fichiers_sortie": tache.fichiers_sortie, "script_genere": tache.script_genere if voir_code else "",
        "prochaine_execution": iso(tache.prochaine_execution),
        "derniere_execution": iso(tache.derniere_execution), "cree_le": iso(tache.cree_le),
        "mode_declenchement": tache.mode_declenchement,
        "condition_categorie": tache.condition_categorie,
        "condition_table_id": tache.condition_table_id,
        "condition_table_nom": tache.condition_table.nom if tache.condition_table_id else "",
        "condition_parametres": tache.condition_parametres,
    }


def _serialiser_partage_ligne(entreprise, enregistrement):
    """Sérialise l'état de partage de LIGNE de cet enregistrement : le
    groupe courant (s'il existe) et les autres lignes reliées (dans
    n'importe quelle table de l'entreprise)."""
    membre = (
        LignePartagee.objects
        .filter(enregistrement=enregistrement, groupe__entreprise=entreprise)
        .select_related("groupe")
        .first()
    )
    if not membre:
        return {"groupe_id": None, "membres": []}
    autres = (
        LignePartagee.objects
        .filter(groupe_id=membre.groupe_id)
        .exclude(pk=membre.pk)
        .select_related("enregistrement__table")
    )
    return {
        "groupe_id": membre.groupe_id,
        "membres": [
            {
                "table_id": m.enregistrement.table_id,
                "table_nom": m.enregistrement.table.nom,
                "table_slug": m.enregistrement.table.slug,
                "ligne_id": m.enregistrement_id,
                "ligne_label": _label_enregistrement(m.enregistrement),
            }
            for m in autres
        ],
    }


def _serialiser_partage_cellule(entreprise, enregistrement, champ):
    """Sérialise l'état de partage de CELLULE (ligne + champ précis) de
    cette valeur : le groupe courant (s'il existe) et les autres cellules
    reliées (dans n'importe quelle table de l'entreprise)."""
    membre = (
        CellulePartagee.objects
        .filter(enregistrement=enregistrement, champ=champ, groupe__entreprise=entreprise)
        .select_related("groupe")
        .first()
    )
    if not membre:
        return {"groupe_id": None, "membres": []}
    autres = (
        CellulePartagee.objects
        .filter(groupe_id=membre.groupe_id)
        .exclude(pk=membre.pk)
        .select_related("enregistrement__table", "champ")
    )
    return {
        "groupe_id": membre.groupe_id,
        "membres": [
            {
                "table_id": m.enregistrement.table_id,
                "table_nom": m.enregistrement.table.nom,
                "table_slug": m.enregistrement.table.slug,
                "ligne_id": m.enregistrement_id,
                "ligne_label": _label_enregistrement(m.enregistrement),
                "champ_id": m.champ_id,
                "champ_nom": m.champ.nom,
                "champ_slug": m.champ.slug,
            }
            for m in autres
        ],
    }


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def api_partage_ligne(request, table_id, ligne_id):
    """Gère le partage de LIGNE d'un enregistrement précis.

    GET    : renvoie le groupe courant (s'il existe) et les lignes reliées.
    POST    body {"cibles": [{"table_id", "ligne_id"}, ...]} (ou l'ancien
              {"table_id", "ligne_id"}) : relie la ligne courante à toutes les
              lignes cibles en un seul groupe (pas seulement de pair à pair). Si l'une des deux
              appartient déjà à un groupe, l'autre groupe est FUSIONNÉ
              dedans (mêmes règles que le partage de colonne) au lieu de
              créer un second groupe séparé.
    DELETE  : retire la ligne courante de son groupe de partage (le groupe
              est supprimé s'il ne reste qu'un seul membre après coup).
    """
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    enregistrement = _ligne_accessible(request, table, ligne_id, ecriture=request.method != "GET")
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne

    if request.method == "GET":
        return JsonResponse(_serialiser_partage_ligne(entreprise, enregistrement))

    if request.method == "DELETE":
        membre = LignePartagee.objects.filter(enregistrement=enregistrement).select_related("groupe").first()
        if membre:
            groupe = membre.groupe
            membre.delete()
            if groupe.lignes.count() <= 1:
                groupe.lignes.all().delete()
                groupe.delete()
        return JsonResponse(_serialiser_partage_ligne(entreprise, enregistrement))

    # POST : relier à UNE OU PLUSIEURS lignes cibles (partage en groupe, comme les colonnes) :
    # body {"cibles": [{"table_id", "ligne_id"}, ...]} ; ancien format {"table_id", "ligne_id"} accepté.
    corps = json.loads(request.body or "{}")
    cibles_brutes = list(corps.get("cibles") or [])
    if corps.get("table_id") and corps.get("ligne_id"):
        cibles_brutes.append({"table_id": corps["table_id"], "ligne_id": corps["ligne_id"]})
    if not cibles_brutes:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:882:41")}, status=400)
    cibles = {}
    for c in cibles_brutes:
        if not c.get("table_id") or not c.get("ligne_id"):
            return JsonResponse({"erreur": t("engine/views/_part_009.py:882:41")}, status=400)
        cible = get_object_or_404(
            Enregistrement, id=c["ligne_id"], table_id=c["table_id"], table__entreprise=entreprise,
        )
        if cible.id != enregistrement.id:
            cibles[cible.id] = cible
    if not cibles:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:887:41")}, status=400)

    membres = {m.enregistrement_id: m for m in LignePartagee.objects.filter(
        enregistrement__in=[enregistrement, *cibles.values()]).select_related("groupe")}
    existants = [membres[e.id].groupe for e in (enregistrement, *cibles.values()) if e.id in membres]
    groupe = existants[0] if existants else GroupePartageLigne.objects.create(entreprise=entreprise)

    # Fusion : tout autre groupe déjà présent parmi les membres est absorbé dans le groupe canonique.
    for autre in {g.id for g in existants if g.id != groupe.id}:
        LignePartagee.objects.filter(groupe_id=autre).update(groupe_id=groupe.id)
        GroupePartageLigne.objects.filter(id=autre).delete()

    for e in (enregistrement, *cibles.values()):
        LignePartagee.objects.get_or_create(groupe=groupe, enregistrement=e)

    return JsonResponse(_serialiser_partage_ligne(entreprise, enregistrement))


@login_required
@require_http_methods(["GET", "POST"])
def api_ia_types_modeles_ajouter(request):
    """Ajoute un nouvel algorithme (type de modele) depuis la page d'entrainement : meme mecanisme que la commande
    `manage.py ajouter_modele_ia` (IA/plugins_modeles.py::installer), reserve au SUPER-ADMINISTRATEUR Django (code ecrit dans un dossier
    partage par toutes les entreprises : droit critique « algorithmes_ia », non delegable).

    GET                      -> {exemple, modules} : script d'exemple et modules autorises (prefill du formulaire).
    POST {description}       -> {code} : l'IA ecrit le script (canal modele_ia) ; RIEN n'est installe, l'administrateur relit.
    POST {code}              -> {nom, types} : valide le script, l'ecrit dans IA/modeles_scripts/ et l'enregistre."""
    bloque = _interdit_non_admin(request, "algorithmes_ia")
    if bloque:
        return bloque
    entreprise = _entreprise_courante(request)
    cfg = ConfigurationIA.objects.filter(entreprise=entreprise).first()
    from ..executor import modules_modele_ia_a_jour
    modules = modules_modele_ia_a_jour(cfg.modules_autorises_modele_ia if cfg else "")

    if request.method == "GET":
        from ..inference import exemple_modele_ia_a_jour
        from ..executor import DEFAULT_MODULES_AUTORISES_MODELE_IA
        return JsonResponse({
            "exemple": exemple_modele_ia_a_jour(cfg.script_exemple_modele_ia if cfg else ""),
            "modules": modules.strip() or ", ".join(sorted(DEFAULT_MODULES_AUTORISES_MODELE_IA)),
        })

    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views/_part_009.py:corps_json_invalide")}, status=400)
    code = (corps.get("code") or "").strip()
    description = (corps.get("description") or "").strip()
    if bool(code) == bool(description):
        return JsonResponse({"erreur": t("engine/views/_part_009.py:algo_code_ou_description")}, status=400)

    if description:
        from ..inference import MoteurInference
        code, erreur = MoteurInference(entreprise=entreprise).generer_modele_ia(description)
        if erreur:
            return JsonResponse({"erreur": str(erreur)}, status=400)
        return JsonResponse({"code": code})

    from IA.plugins_modeles import installer
    nom, erreur = installer(code, modules, corps.get("profil") if isinstance(corps.get("profil"), dict) else None)
    if erreur:
        return JsonResponse({"erreur": str(erreur)}, status=400)
    try:
        types = _ia.types_modeles_disponibles(forcer=True)
    except Exception as e:
        return JsonResponse({"erreur": f"Module IA indisponible : {e}"}, status=500)
    return JsonResponse({"nom": nom, "types": types}, status=201)




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
