from engine.i18n import t
from ..delegation import a_pouvoir as _a_pouvoir
from ._header import *  # noqa
from ..calc_champ import verifier_types_formule  # noqa
# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402


def _couleurs_table_hex(entreprise):
    """Couleurs de marque de l'entreprise (en-tête, lignes alternées, texte, accent) en « #rrggbb » : les mêmes que celles du PDF de liste
    (reports._couleurs_rapport), pour que l'Excel exporté ait l'apparence de la table."""
    from ..reports._part_001 import _couleurs_rapport
    sortie = {}
    for cle, c in _couleurs_rapport(entreprise).items():
        try:
            sortie[cle] = "#" + c.hexval()[2:8]
        except Exception:
            pass
    return sortie


@login_required
@require_http_methods(["GET"])
@_pdf_erreur_json
def api_exporter_table_tableur(request, table_id):
    """Export Excel (.xlsx), CSV ou Parquet de la grille — ?format=xlsx|csv|parquet, plus les mêmes q / f_<slug> / tri / sens
    que l'API des lignes : on exporte exactement la vue filtrée et triée. Isolation agent appliquée ;
    réservé aux agents et admins (un client n'a que l'export PDF de sa recherche)."""
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    from .. import grille
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from ..vue_groupe import enregistrements_vue
    with grille.chrono("filtre et tri", table.slug):
        qs, _ = grille.appliquer(_isoler_lignes_agent(request, membre, enregistrements_vue(table)), table, request.GET)   # groupe = une table
    champs = [c for c in table.champs.order_by("ordre") if c.type_champ not in ("vecteur", "matrice")]
    from django.utils import timezone
    from .. import export_fidele
    horodatage = timezone.localtime().strftime("%Y%m%d-%H%M")
    format_demande = (request.GET.get("format") or "xlsx").lower()
    # Export FIDÈLE : manifeste (schéma + valeurs brutes) qui permet de restaurer la table à l'identique (engine/export_fidele.py).
    _liste = None
    if format_demande in ("gytable", "xlsx", "parquet"):
        with grille.chrono("lecture des lignes", table.slug):
            _liste = list(qs.select_related("table"))
        with grille.chrono("manifeste", table.slug):
            manifeste = export_fidele.construire(table, _liste, _champs_masques_pour(membre, table))
    if format_demande == "gytable":
        reponse = HttpResponse(export_fidele.en_json(manifeste), content_type="application/json; charset=utf-8")
        reponse["Content-Disposition"] = f'attachment; filename="{table.slug}_{horodatage}.gytable.json"'
        return reponse
    # Gros volumes ou groupe (32 000 lignes…) : to_dict() relit l'EAV ligne par ligne (une requête par ligne, un groupe = plusieurs tables) :
    # export interminable ou interrompu par le délai de la requête. Au-delà de SEUIL_LECTURE_RAPIDE lignes, les valeurs viennent du fichier
    # Parquet (une seule lecture, comme pour le PDF : parquet_store.lignes_affichage, qui retombe sur to_dict() si le fichier est inutilisable).
    SEUIL_LECTURE_RAPIDE = 2000
    if _liste is None:
        with grille.chrono("lecture des lignes", table.slug):
            _liste = list(qs.select_related("table"))
    # Un GROUPE (table géante découpée en tranches) passe TOUJOURS par Parquet, quel que soit le volume : il n'a pas de fichier propre, il est
    # assemblé depuis le fichier de chaque tranche (détacher / rattacher une table retire / ajoute sa plage sans rien resynchroniser).
    if table.groupe_id or len(_liste) > SEUIL_LECTURE_RAPIDE:
        from .. import parquet_store
        with grille.chrono("valeurs (Parquet)", table.slug):
            valeurs, _mises = parquet_store.lignes_affichage(table, _liste)
        brutes = [dict(v) | {"_id": e.id} for v, e in zip(valeurs, _liste)]
    else:
        from .. import parquet_store
        brutes = [v | {"_id": e.id} for v, e in zip(parquet_store.valeurs_lignes(table, _liste), _liste)]
    with grille.chrono("libellés des relations", table.slug):
        lignes = _resoudre_labels_relation(table, brutes)
    if format_demande == "parquet":   # même vue, mêmes masques et mêmes cellules que le CSV ; manifeste de restauration dans les métadonnées
        contenu = grille.en_parquet(champs, lignes, meta=export_fidele.en_cellules(manifeste))
        if contenu is not None:
            reponse = HttpResponse(contenu, content_type="application/vnd.apache.parquet")
            reponse["Content-Disposition"] = f'attachment; filename="{table.slug}_{horodatage}.parquet"'
            return reponse
        return JsonResponse({"erreur": _gt("rg5.msg.063")}, status=501)
    if format_demande != "csv" and format_demande != "json":
        # Couleurs (colonne, ligne, cellule) et cellules fusionnées : reprises pour N'IMPORTE QUELLE vue (voir engine/export_structure.py).
        from .. import export_structure
        toutes = list(table.champs.order_by("ordre"))
        fusions = list(table.fusions_cellules.all())
        fusions_vue = []
        if fusions:
            from ..fusion_cellule import resynchroniser
            resynchroniser(table, fusions)
            fusions_vue = export_structure.fusions_vue(table, fusions, [e.id for e in _liste], toutes,
                                                       calculs=export_structure.calculs_formules(table, fusions, toutes))
            fusions_vue = export_structure.decaler_colonnes(fusions_vue, [c.slug for c in toutes], [c.slug for c in champs])
        contenu = grille.en_xlsx(table.nom, champs, lignes, meta=export_fidele.en_cellules(manifeste),
                                 mises_en_forme=[e.couleurs_mise_en_forme() for e in _liste], fusions_vue=fusions_vue,
                                 couleurs_table=_couleurs_table_hex(entreprise))
        if contenu is not None:
            reponse = HttpResponse(
                contenu, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            reponse["Content-Disposition"] = f'attachment; filename="{table.slug}_{horodatage}.xlsx"'
            return reponse
    # Table NoSQL (voir nosql_store.py) : la donnée est nativement imbriquée,
    # un CSV à plat n'a pas de sens — JSON à la place, y compris si le
    # client demande encore "csv" (ancien bouton en cache). Table SQL,
    # inchangé : CSV comme avant, sauf demande explicite de JSON.
    if table.est_nosql or format_demande == "json":
        reponse = HttpResponse(grille.en_json(champs, lignes), content_type="application/json; charset=utf-8")
        reponse["Content-Disposition"] = f'attachment; filename="{table.slug}_{horodatage}.json"'
        return reponse
    reponse = HttpResponse(grille.en_csv(champs, lignes), content_type="text/csv; charset=utf-8")
    reponse["Content-Disposition"] = f'attachment; filename="{table.slug}_{horodatage}.csv"'
    return reponse


# ─────────────────────────────────────────────────────────────
# TABLES DE CALCUL : formules façon Excel appliquées à une table source,
# indépendantes de celle-ci (voir models.TableCalcul / FormuleCalcul /
# LigneCalcul et calculs.py pour la résolution/exécution).
# ─────────────────────────────────────────────────────────────

def _serialiser_formule(f):
    return {
        "id": f.id, "nom": f.nom, "slug": f.slug, "categorie": f.categorie,
        "cible_type": f.cible_type, "description": f.description,
        "types_cibles_compatibles": f.liste_types_compatibles,
        "predefinie": f.predefinie, "code": f.code,
    }


def _serialiser_ligne_calcul(l):
    return {
        "id": l.id, "nom": l.nom, "ordre": l.ordre, "couleur": l.couleur or "",
        "formule": _serialiser_formule(l.formule) if l.formule_id else None,
        "programme": l.programme,
        "type_cible": l.type_cible,
        "champ_cible_id": l.champ_cible_id,
        "champ_cible_nom": l.champ_cible.nom if l.champ_cible_id else None,
        "champs_cibles_ids": [c.id for c in l.champs_cibles.all()],
        "champs_cibles_noms": [c.nom for c in l.champs_cibles.all()],
        "plage_debut": l.plage_debut, "plage_fin": l.plage_fin,
        "cibles_json": l.cibles_json,
        "lignes_sources_ids": [s.id for s in l.lignes_sources.all()],
        "lignes_sources_noms": [s.nom for s in l.lignes_sources.all()],
        "resultat": l.resultat_json, "erreur": l.erreur,
        "resultat_calcule_le": l.resultat_calcule_le.isoformat() if l.resultat_calcule_le else None,
    }


def _serialiser_table_calcul(tc):
    return {
        "id": tc.id, "nom": tc.nom, "description": tc.description, "couleur": tc.couleur or "",
        "inclure_dans_pdf": tc.inclure_dans_pdf, "ordre": tc.ordre,
        "table_source_id": tc.table_source_id,
        "lignes": [_serialiser_ligne_calcul(l) for l in tc.lignes.select_related("formule", "champ_cible").prefetch_related("champs_cibles", "lignes_sources").order_by("ordre", "id")],
    }


@login_required
@require_http_methods(["POST"])
def api_generer_formule_calcul(request):
    """Lance en tache de fond la generation d'une formule personnalisee via
    le moteur d'IA deja utilise pour les scripts d'analyse (voir
    inference.MoteurInference.generer_formule_calcul, appele par la tache
    Celery engine.generer_formule_calcul). Corps attendu : {description,
    cible_type, table_id (optionnel, pour le contexte de schema/donnees)}.
    Ne sauvegarde aucune FormuleCalcul : cree juste un brouillon
    (GenerationFormule) a suivre via api_statut_generation_formule, dont le
    code une fois pret est a valider/modifier avant "Enregistrer la
    formule" cote client."""
    entreprise = _entreprise_courante(request)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    corps = json.loads(request.body or "{}")
    description = (corps.get("description") or "").strip()
    cible_type = corps.get("cible_type") or "colonne"
    if not description:
        return JsonResponse({"erreur": t("engine/views/_part_003.py:110:41")}, status=400)
    if cible_type not in dict(FormuleCalcul.CIBLE_TYPE_CHOICES):
        return JsonResponse({"erreur": t("engine/views/_part_003.py:112:41")}, status=400)

    table_id = corps.get("table_id")
    table = None
    if table_id:
        table = TableDynamique.objects.filter(id=table_id, entreprise=entreprise).first()

    generation = GenerationFormule.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        table_reference=table,
        description=description,
        cible_type=cible_type,
        statut="en_cours",
    )
    _taches.generer_formule_calcul_tache.delay(generation.id)
    return JsonResponse({"generation_id": generation.id}, status=202)


@login_required
@require_http_methods(["GET"])
def api_statut_generation_formule(request, generation_id):
    """Polling leger : retourne uniquement le statut tant que ce n'est pas
    termine, puis l'objet complet (memes conventions que
    api_statut_demande / _attendreFin cote JS : {fini, generation:{statut,
    code, erreur}})."""
    entreprise = _entreprise_courante(request)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    generation = get_object_or_404(GenerationFormule, id=generation_id, entreprise=entreprise)
    if generation.statut in ("pret", "echec"):
        return JsonResponse({"fini": True, "generation": {
            "statut": generation.statut,
            "code": generation.code,
            "erreur": generation.erreur,
        }})
    return JsonResponse({"fini": False, "statut": generation.statut})


@login_required
def api_formules_calcul(request):
    """GET : liste les formules disponibles (prédéfinies + personnalisées à
    l'entreprise), optionnellement filtrées par type de champ compatible
    (?type_champ=nombre).
    POST : crée une formule personnalisée pour l'entreprise courante."""
    from ..formules_calcul import synchroniser_formules_predefinies
    synchroniser_formules_predefinies()

    entreprise = _entreprise_courante(request)

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        code = corps.get("code") or ""
        cible_type = corps.get("cible_type") or "colonne"
        categorie = corps.get("categorie") or "personnalisee"
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:173:45")}, status=400)
        if "def formule(" not in code:
            return JsonResponse(
                {"erreur": t("engine/views/_part_003.py:176:29")}, status=400
            )
        if cible_type not in dict(FormuleCalcul.CIBLE_TYPE_CHOICES):
            return JsonResponse({"erreur": t("engine/views/_part_003.py:179:45")}, status=400)
        if categorie not in dict(FormuleCalcul.CATEGORIE_CHOICES):
            categorie = "personnalisee"

        from django.utils.text import slugify
        base_slug = slugify(nom) or "formule"
        slug = base_slug
        n = 2
        while FormuleCalcul.objects.filter(entreprise=entreprise, slug=slug).exists():
            slug = f"{base_slug}-{n}"
            n += 1

        types_cibles = corps.get("types_cibles_compatibles")
        if isinstance(types_cibles, list):
            types_cibles = ",".join(t.strip() for t in types_cibles if t.strip())
        else:
            types_cibles = (types_cibles or "").strip()

        formule = FormuleCalcul.objects.create(
            entreprise=entreprise, nom=nom, slug=slug,
            description=corps.get("description", ""),
            categorie=categorie, cible_type=cible_type, code=code,
            types_cibles_compatibles=types_cibles, predefinie=False,
        )
        return JsonResponse({"formule": _serialiser_formule(formule)}, status=201)

    formules = FormuleCalcul.objects.filter(
        Q(entreprise__isnull=True) | Q(entreprise=entreprise)
    ).order_by("categorie", "nom")

    type_champ = request.GET.get("type_champ")
    if type_champ:
        formules = [f for f in formules if f.compatible_avec_type_champ(type_champ)]

    return JsonResponse({"formules": [_serialiser_formule(f) for f in formules]})


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_formule_calcul_detail(request, formule_id):
    """PATCH : modifie une formule personnalisée (nom/description/categorie/
    cible_type/types_cibles_compatibles/code) — même principe d'édition que
    api_graphique_bi_detail. DELETE : supprime une formule personnalisée de
    l'entreprise (jamais une formule prédéfinie de la plateforme)."""
    entreprise = _entreprise_courante(request)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    formule = get_object_or_404(FormuleCalcul, id=formule_id, entreprise=entreprise, predefinie=False)
    if request.method == "DELETE":
        try:
            formule.delete()
        except Exception:
            return JsonResponse(
                {"erreur": t("engine/views/_part_003.py:233:29")},
                status=409,
            )
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")
    if "code" in corps and "def formule(" not in (corps["code"] or ""):
        return JsonResponse({"erreur": t("engine/views/_part_003.py:240:41")}, status=400)
    if "cible_type" in corps and corps["cible_type"] not in dict(FormuleCalcul.CIBLE_TYPE_CHOICES):
        return JsonResponse({"erreur": t("engine/views/_part_003.py:242:41")}, status=400)
    for champ in ("description", "categorie", "cible_type", "code"):
        if champ in corps:
            setattr(formule, champ, corps[champ])
    if "nom" in corps and (corps["nom"] or "").strip():
        formule.nom = corps["nom"].strip()
    if "types_cibles_compatibles" in corps:
        types_cibles = corps["types_cibles_compatibles"]
        if isinstance(types_cibles, list):
            types_cibles = ",".join(t.strip() for t in types_cibles if t.strip())
        formule.types_cibles_compatibles = (types_cibles or "").strip()
    formule.save()
    # Cascade : modifier le code (ou le type de cible) d'une formule change le résultat de tout ce qui
    # l'utilise — calculs (et ceux qui en dépendent) et KPI — qui sont recalculés tout de suite.
    if any(c in corps for c in ("code", "cible_type")):
        from ..calculs import recalculer_en_cascade
        from ..kpi import calculer_kpi
        from ..models import IndicateurKPI
        recalculer_en_cascade(list(LigneCalcul.objects.filter(formule=formule).values_list("id", flat=True)), ignorer_denaturee=False)
        for kpi in IndicateurKPI.objects.filter(formule=formule).select_related("formule", "champ_cible", "table_source"):
            try:
                calculer_kpi(kpi)
            except Exception:
                pass
    return JsonResponse({"formule": _serialiser_formule(formule)})


@login_required
def api_tables_calcul(request, table_id):
    """Liste / crée les tables de calcul liées à une table source."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    _bloque_lecteur = _interdit_table_non_exposee(request, table)
    if _bloque_lecteur:
        return _bloque_lecteur

    if request.method == "GET":
        from ..vue_groupe import ids_groupe
        tables_calcul = TableCalcul.objects.filter(entreprise=entreprise, table_source_id__in=ids_groupe(table)).order_by("ordre", "id").prefetch_related("lignes__formule", "lignes__champ_cible", "lignes__champs_cibles")
        return JsonResponse({"tables_calcul": [_serialiser_table_calcul(tc) for tc in tables_calcul]})

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:277:45")}, status=400)
        from ..vue_groupe import ancre_groupe
        table_calcul = TableCalcul.objects.create(
            entreprise=entreprise, table_source=ancre_groupe(table), nom=nom,
            description=corps.get("description", ""),
            inclure_dans_pdf=corps.get("inclure_dans_pdf", True),
        )
        return JsonResponse({"table_calcul": _serialiser_table_calcul(table_calcul)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_003.py:285:37")}, status=405)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_table_calcul_detail(request, calcul_id):
    entreprise = _entreprise_courante(request)
    table_calcul = get_object_or_404(TableCalcul, id=calcul_id, entreprise=entreprise)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        # Les calculs d'AUTRES tables qui s'appuyaient sur celle-ci sont recalculés (ils perdent leur source).
        dependantes = list(
            LigneCalcul.objects.filter(lignes_sources__table_calcul=table_calcul)
            .exclude(table_calcul=table_calcul).values_list("id", flat=True).distinct()
        )
        table_calcul.delete()
        if dependantes:
            from ..calculs import recalculer_en_cascade
            recalculer_en_cascade(dependantes, ignorer_denaturee=False)
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")
    for champ in ("nom", "description", "inclure_dans_pdf", "ordre"):
        if champ in corps:
            setattr(table_calcul, champ, corps[champ])
    table_calcul.save()
    return JsonResponse({"table_calcul": _serialiser_table_calcul(table_calcul)})


def _programme_calcul_valide(brut, entreprise):
    """Programme d'une ligne de table de calcul → (liste propre, erreur). Même validation que les KPI, plus les formules de
    la bibliothèque (leur visibilité par l'entreprise est vérifiée à l'exécution, formules_programme._formule)."""
    from ._part_005 import _programme_kpi_valide  # défini plus bas dans le paquet : import à l'appel (évite le cycle)
    programme, erreur = _programme_kpi_valide(brut, bibliotheque=True)
    return (None, erreur or t("engine/views/_part_003.py:g:349:29")) if erreur or not programme else (programme, None)


@login_required
def api_lignes_calcul(request, calcul_id):
    """Liste / crée les lignes d'une table de calcul. La création calcule
    immédiatement le résultat avant de répondre."""
    entreprise = _entreprise_courante(request)
    table_calcul = get_object_or_404(TableCalcul, id=calcul_id, entreprise=entreprise)

    if request.method == "GET":
        lignes = table_calcul.lignes.select_related("formule", "champ_cible").prefetch_related("champs_cibles").order_by("ordre", "id")
        return JsonResponse({"lignes": [_serialiser_ligne_calcul(l) for l in lignes]})

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        if corps.get("programme") is not None:   # ligne « programme » : éditeur visuel, pas de formule ni de cible propres
            programme, _e = _programme_calcul_valide(corps["programme"], entreprise)
            if _e or not nom:
                return JsonResponse({"erreur": _e or t("engine/views/_part_003.py:g:372:54")}, status=400)
            ligne = LigneCalcul.objects.create(
                table_calcul=table_calcul, nom=nom, programme=programme, ordre=corps.get("ordre", table_calcul.lignes.count()),
            )
            from ..calculs import recalculer_en_cascade, sources_du_programme
            ligne.lignes_sources.set(sources_du_programme(ligne))
            recalculer_en_cascade([ligne], ignorer_denaturee=False)
            ligne.refresh_from_db()
            return JsonResponse({"ligne": _serialiser_ligne_calcul(ligne)}, status=201)
        formule_id = corps.get("formule_id")
        if not nom or not formule_id:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:328:45")}, status=400)

        formule = FormuleCalcul.objects.filter(
            id=formule_id
        ).filter(Q(entreprise__isnull=True) | Q(entreprise=entreprise)).first()
        if not formule:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:334:45")}, status=404)

        type_cible = corps.get("type_cible", "champ")
        if type_cible not in dict(LigneCalcul.TYPE_CIBLE_CHOICES):
            return JsonResponse({"erreur": t("engine/views/_part_003.py:338:45")}, status=400)

        champ_cible = None
        champ_cible_id = corps.get("champ_cible_id")
        if champ_cible_id:
            champ_cible = ChampDynamique.objects.filter(
                id=champ_cible_id, table=table_calcul.table_source
            ).first()
            if not champ_cible:
                return JsonResponse({"erreur": t("engine/views/_part_003.py:347:49")}, status=400)
        elif formule.cible_type == "colonne" and type_cible != "resultats":
            return JsonResponse({"erreur": t("engine/views/_part_003.py:349:45")}, status=400)
        if champ_cible and (_e := verifier_types_formule(table_calcul.table_source, "personnalisee", [champ_cible.slug], formule)): return JsonResponse({"erreur": _e}, status=400)
        # Itération : lignes de calcul dont les résultats servent d'entrée
        # (ex: moyenne générale = moyenne des moyennes par matière).
        lignes_sources_valides = []
        if type_cible == "resultats":
            lignes_sources_ids = corps.get("lignes_sources_ids") or []
            lignes_sources_valides = list(
                LigneCalcul.objects.filter(id__in=lignes_sources_ids, table_calcul__entreprise=entreprise)
            )
            if not lignes_sources_valides:
                return JsonResponse({"erreur": t("engine/views/_part_003.py:360:49")}, status=400)

        # Sélection graphique multi-colonnes (formules cible_type="table"
        # uniquement) : liste d'ids de colonnes de la table source, capturée
        # en cliquant/glissant sur les en-têtes du tableau réel (voir
        # index.html, sélecteur de cible). Ignorée pour les formules
        # cible_type="colonne" (une seule Series attendue, via champ_cible).
        champs_cibles_ids = corps.get("champs_cibles_ids") or []
        champs_cibles_valides = []
        if champs_cibles_ids and formule.cible_type == "table":
            champs_cibles_valides = list(
                ChampDynamique.objects.filter(
                    id__in=champs_cibles_ids, table=table_calcul.table_source
                )
            )
            if not champs_cibles_valides:
                return JsonResponse({"erreur": t("engine/views/_part_003.py:376:49")}, status=400)

        # Sélections discontinues : [{champ_id, debut, fin}, …]
        cibles_json = corps.get("cibles_json") or None

        ligne = LigneCalcul.objects.create(
            table_calcul=table_calcul, nom=nom, formule=formule, type_cible=type_cible,
            champ_cible=champ_cible,
            plage_debut=corps.get("plage_debut") or None,
            plage_fin=corps.get("plage_fin") or None,
            cibles_json=cibles_json,
            ordre=corps.get("ordre", table_calcul.lignes.count()),
        )
        if champs_cibles_valides:
            ligne.champs_cibles.set(champs_cibles_valides)
        if lignes_sources_valides:
            ligne.lignes_sources.set(lignes_sources_valides)

        from ..calculs import recalculer_en_cascade
        recalculer_en_cascade([ligne], ignorer_denaturee=False)   # la ligne ET tout ce qui s'appuie sur son résultat (cascade)
        ligne.refresh_from_db()
        return JsonResponse({"ligne": _serialiser_ligne_calcul(ligne)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_003.py:399:37")}, status=405)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_ligne_calcul_detail(request, ligne_id):
    entreprise = _entreprise_courante(request)
    ligne = get_object_or_404(
        LigneCalcul, id=ligne_id, table_calcul__entreprise=entreprise
    )
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        # Les calculs qui s'appuyaient sur ce résultat sont recalculés (ils perdent leur source) — cascade.
        dependantes = list(LigneCalcul.objects.filter(lignes_sources=ligne).values_list("id", flat=True))
        ligne.delete()
        if dependantes:
            from ..calculs import recalculer_en_cascade
            recalculer_en_cascade(dependantes, ignorer_denaturee=False)
        return JsonResponse({"ok": True})
    corps = json.loads(request.body or "{}")
    if ligne.programme is not None:   # ligne « programme » : seuls le nom et le programme sont modifiables ; dépendances rederivées
        programme, _e = _programme_calcul_valide(corps["programme"], entreprise) if "programme" in corps else (ligne.programme, None)
        if _e:
            return JsonResponse({"erreur": _e}, status=400)
        ligne.programme, ligne.nom = programme, (corps.get("nom") or ligne.nom).strip()
        ligne.save()
        from ..calculs import recalculer_en_cascade, sources_du_programme
        ligne.lignes_sources.set(sources_du_programme(ligne))
        recalculer_en_cascade([ligne], ignorer_denaturee=False)
        ligne.refresh_from_db()
        return JsonResponse({"ligne": _serialiser_ligne_calcul(ligne)})
    if (_c := ChampDynamique.objects.filter(id=corps["champ_cible_id"] if "champ_cible_id" in corps else ligne.champ_cible_id).first()) and (_e := verifier_types_formule(_c.table, "personnalisee", [_c.slug], ligne.formule)): return JsonResponse({"erreur": _e}, status=400)
    for champ in ("nom", "type_cible", "plage_debut", "plage_fin", "ordre"):
        if champ in corps:
            setattr(ligne, champ, corps[champ])
    if "champ_cible_id" in corps:
        ligne.champ_cible_id = corps["champ_cible_id"] or None
    if "cibles_json" in corps:
        ligne.cibles_json = corps["cibles_json"] or None
    ligne.save()
    if "lignes_sources_ids" in corps:
        ids = corps.get("lignes_sources_ids") or []
        ligne.lignes_sources.set(
            LigneCalcul.objects.filter(id__in=ids, table_calcul__entreprise=entreprise)
        )
    if "champs_cibles_ids" in corps:
        ids = corps.get("champs_cibles_ids") or []
        if ids and ligne.formule.cible_type == "table":
            champs_valides = ChampDynamique.objects.filter(
                id__in=ids, table=ligne.table_calcul.table_source
            )
            ligne.champs_cibles.set(champs_valides)
        else:
            ligne.champs_cibles.clear()

    from ..calculs import recalculer_en_cascade
    recalculer_en_cascade([ligne], ignorer_denaturee=False)   # la ligne ET tout ce qui s'appuie sur son résultat (cascade)
    ligne.refresh_from_db()
    return JsonResponse({"ligne": _serialiser_ligne_calcul(ligne)})


@login_required
@require_http_methods(["POST"])
def api_recalculer_table_calcul(request, calcul_id):
    entreprise = _entreprise_courante(request)
    table_calcul = get_object_or_404(TableCalcul, id=calcul_id, entreprise=entreprise)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    try:
        lignes = actions.executer_action("recalculer_table_calcul", membre, table_calcul)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_003.py:456:41")}, status=403)
    return JsonResponse({"lignes": [_serialiser_ligne_calcul(l) for l in lignes]})


# ─────────────────────────────────────────────────────────────
# TABLEAUX DE BORD BI (style Power BI) : canevas plein écran purement
# graphique, lié à une table source (voir models.GraphiqueBI/TableauBordBI/
# WidgetGraphiqueBI et tableau_bord_bi.py). Le rendu de chaque widget est
# SYNCHRONE (appel direct de tableau_bord_bi.calculer_widget dans la vue,
# sans tâche Celery) : le graphique matplotlib est construit en mémoire et
# converti en PNG base64 en quelques centaines de millisecondes, donc pas
# besoin d'un worker séparé — le front reçoit directement le résultat.
# Aucune image n'est jamais écrite sur disque. Le tableau de bord se
# resynchronise avec la table source via un polling léger côté front (voir
# dashboard_bi.html) qui rappelle periodiquement /recalculer/ ; comme ce
# dernier ne recalcule que les widgets périmés (empreinte des données), un
# appel sans changement ne coûte quasiment rien.
# ─────────────────────────────────────────────────────────────

@login_required
@ensure_csrf_cookie
def vue_dashboard_bi(request, table_id):
    """Page dédiée : canevas plein écran des tableaux de bord BI d'une table
    source donnée (grille façon Power BI, widgets en drag & resize).
    Accessible en lecture seule sur la table d'une AUTRE entreprise si un
    PartageInterAgent(type_objet='table' ou 'dashboard') actif l'autorise —
    voir _table_via_partage."""
    entreprise = _entreprise_courante(request)
    table, lecture_seule_partage = _table_via_partage(request, table_id)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    est_client = bool(membre and membre.role == "lecteur")
    if not lecture_seule_partage:
        _bloque_lecteur = _interdit_table_bi(request, table)
        if _bloque_lecteur:
            # Un client refusé retourne à son espace plutôt que de voir une réponse JSON brute.
            return redirect("engine:espace_client") if est_client else _bloque_lecteur
    if not _vue_autorisee(membre, "dashboard_bi"):
        return redirect('/profile/')
    # ── Client (lecteur) : lecture seule, et uniquement les tableaux de bord que l'agent a mis à sa disposition ────
    if est_client:
        from ..vue_groupe import ids_groupe
        if not TableauBordBI.objects.filter(entreprise=entreprise, table_source_id__in=ids_groupe(table),
                                            visible_client=True).exists():
            return redirect("engine:espace_client")
        # ── Abonnement mensuel obligatoire pour les lecteurs (clients) ────
        from ..wallet import acces_page_client, page_ouverte_client
        if not page_ouverte_client(entreprise, "dashboard_bi"):   # page fermée aux clients par l'agent
            return redirect("engine:espace_client")
        if not acces_page_client(entreprise, membre, "dashboard_bi"):
            return redirect(f"/espace-client/paiement/?service=dashboard_bi")
        lecture_seule_partage = True   # jamais d'édition pour un client : le gabarit masque tout ce qui modifie
    return render(request, "engine/dashboard_bi.html", {
        "entreprise": entreprise, "table": table, "lecture_seule": lecture_seule_partage, "est_client": est_client,
    })


@login_required
@ensure_csrf_cookie
def vue_blockchain(request):
    """Page dédiée : visualisation de la blockchain d'entreprise (voir
    models.Bloc — un seul fil par entreprise, Proof of Authority).

    Toujours active pour toute table, sans exception : aucun réglage
    d'activation/désactivation n'existe (voir migration 0052). Ce qui
    varie, c'est la PORTEE affichée — trois vues navigables via ?portee= :
      - globale  : tous les blocs, toutes les transactions (admin
                   uniquement — c'est la vue d'ensemble de l'entreprise) ;
      - portable : le fil d'une table précise (?table=<id>), consultable
                   depuis n'importe où cette table est utilisée, y compris
                   partagée avec un autre agent — d'où « portable » ;
      - agent    : le fil des transactions d'un agent précis (?agent=<id>
                   pour l'admin qui choisit lequel regarder ; imposé et
                   verrouillé sur lui-même pour un agent, qui ne voit
                   jamais la portée globale ni celle d'un autre agent).
    """
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if membre and membre.role == "lecteur":
        return redirect('/profile/')
    est_admin = _a_pouvoir(membre, "blockchain")

    from django.core.paginator import Paginator
    from ..models import Bloc
    Bloc.combler_lacunes(entreprise.id)

    table_id = request.GET.get("table")
    agent_id_param = request.GET.get("agent")
    portee = request.GET.get("portee")

    if not est_admin:
        # Un agent ne navigue jamais vers la portée "globale" (entreprise)
        # ni vers celle d'un autre agent : il reste toujours cantonné à
        # SES PROPRES transactions. Par défaut (pas de ?table=) c'est
        # l'ensemble de son historique, toutes tables confondues — sa
        # version à lui de la vue "globale". S'il arrive via le bouton
        # ⛓️ Blockchain d'une table précise (?table=X), on garde ce
        # filtre en plus, pour ne montrer que SES transactions sur CETTE
        # table (jamais celles des autres agents sur la même table).
        portee = "agent"
        agent_id = request.user.id
    else:
        # Portée déduite si absente : un ?table= sans ?portee= explicite
        # vient forcément du bouton ⛓️ Blockchain d'une table (portable).
        if not portee:
            portee = "portable" if table_id else "globale"
        if portee != "portable":
            table_id = None
        agent_id = agent_id_param if (portee == "agent" and agent_id_param) else None

    blocs = []
    # Couleurs des blocs (CouleurBloc) : calculées sur le bloc ENTIER, pas sur les transactions filtrées, donc identiques dans
    # toutes les portées et sous tous les filtres. Bloc précis > type de transaction (premier type colorié du bloc).
    from ..models import CouleurBloc
    couleurs_blocs = {c.cle: c.couleur for c in CouleurBloc.objects.filter(entreprise=entreprise)}
    couleurs_types = {k[5:]: v for k, v in couleurs_blocs.items() if k.startswith("type:")}

    def _couleur_du_bloc(b):
        propre = couleurs_blocs.get(f"bloc:{b.index}")
        if propre:
            return propre
        return next((couleurs_types[x.get("type")] for x in b.transactions if x.get("type") in couleurs_types), "")

    attente_selection = (portee == "portable" and not table_id) or (portee == "agent" and est_admin and not agent_id)
    if not attente_selection:
        for b in Bloc.objects.filter(entreprise=entreprise).order_by("-index"):
            txs = b.transactions
            if table_id and (portee == "portable" or not est_admin):
                txs = [t for t in txs if str(t.get("table_id")) == str(table_id)]
            if portee == "agent":
                txs = [t for t in txs if str(t.get("agent_id")) == str(agent_id)]
            if not txs:
                continue
            blocs.append({
                "index": b.index, "hash": b.hash, "previous_hash": b.previous_hash,
                "cree_le": b.cree_le, "transactions": txs,
                "couleur": _couleur_du_bloc(b), "couleur_propre": couleurs_blocs.get(f"bloc:{b.index}", ""),
            })

    # Recherche (mêmes critères que l'onglet « Recherche » de l'ontologie, voir blockchain_recherche.py) : appliquée dans la portée courante.
    from ..blockchain_recherche import CLES_RECHERCHE, criteres, filtrer, types_presents
    types_dispo = types_presents(blocs)
    crit = criteres(request.GET)
    blocs = filtrer(blocs, crit)

    # Navigation entre blocs : la chaine peut vite compter des milliers de
    # blocs (un evenement metier = un bloc, voir models.Bloc), donc on
    # pagine plutot que de tout afficher d'un bloc — precedent/suivant +
    # saut direct a une page, comme le reste de la plateforme (voir
    # afficherTable paginée cote frontend).
    TAILLE_PAGE = 15
    paginateur = Paginator(blocs, TAILLE_PAGE)
    try:
        numero_page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        numero_page = 1
    page = paginateur.get_page(numero_page)

    contexte = {
        "entreprise": entreprise,
        "blocs": page.object_list,
        "page": page,
        "chaine_valide": Bloc.chaine_valide(entreprise.id),
        "portee": portee,
        "table_id": table_id,
        "agent_id": agent_id,
        "lecture_seule": not est_admin,
        "est_admin": est_admin, "couleurs_types": couleurs_types,
        "attente_selection": attente_selection, "recherche_active": crit["actifs"], "types_dispo": types_dispo, "types_sel": set(request.GET.getlist("types")),
        "rech": {k: request.GET.get(k, "") for k in CLES_RECHERCHE},
        "nb_blocs": len(blocs), "nb_tx": sum(len(b["transactions"]) for b in blocs),
        "mots_surlignes": json.dumps(crit["mots"] + [x for x in (crit["champ"], crit["valeur"], crit["table"]) if x]),
    }
    # Prefixe de query string a reporter sur les liens de pagination, pour
    # rester sur la meme portee/table/agent en changeant de page.
    qs_pager = f"portee={portee}"
    if table_id:
        qs_pager += f"&table={table_id}"
    if agent_id:
        qs_pager += f"&agent={agent_id}"
    contexte["qs_portee"] = qs_pager
    # Les critères de recherche suivent la pagination (liens et saut direct à une page).
    recherche_qs = request.GET.copy()
    for k in ("page", "portee", "table", "agent"):
        recherche_qs.pop(k, None)
    contexte["recherche_cachee"] = [(k, v) for k in recherche_qs for v in recherche_qs.getlist(k)]
    contexte["qs_pager"] = qs_pager + ("&" + recherche_qs.urlencode() if recherche_qs else "")
    if est_admin:
        # De quoi peupler les sélecteurs "portable" et "agent" dans le
        # header — uniquement calculé pour l'admin, seul rôle qui navigue.
        contexte["tables_nav"] = TableDynamique.objects.filter(entreprise=entreprise).order_by("nom")
        contexte["agents_nav"] = MembreEntreprise.objects.filter(
            entreprise=entreprise, role="agent"
        ).select_related("utilisateur").order_by("utilisateur__username")
    return render(request, "engine/blockchain.html", contexte)


@login_required
@ensure_csrf_cookie   # la case « visible chez le client » et toutes les écritures de la page lisent ce cookie : sans lui, refus 403 silencieux
def vue_ontologie(request):
    """Page dédiée : graphe d'ontologie navigable de TOUTE l'entreprise
    (pas limité à une table) — reprend le même principe de page que le
    canevas BI (plein écran, barre d'actions), mais le canevas affiche le
    graphe interactif au lieu de widgets — voir engine/ontologie.py.

    ?page=<id> (optionnel) sélectionne une PageOntologie précise : seules
    les couches rattachées à cette page (+ les couches globales) sont
    appliquées — voir ontologie.construire_graphe. Sans paramètre, la
    première page existante est utilisée par défaut (ou le graphe complet,
    comportement historique, si l'entreprise n'a encore aucune page)."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not _vue_autorisee(membre, "dashboard_bi"):
        return redirect('/profile/')

    pages_qs = PageOntologie.objects.filter(entreprise=entreprise)
    if membre and membre.role == "lecteur":
        # Un client ne voit que les pages d'ontologie explicitement mises à
        # disposition, jamais d'édition (aucun bouton d'édition rendu côté
        # template en mode lecture_seule).
        pages_qs = pages_qs.filter(visible_client=True)
        if not pages_qs.exists():
            return redirect("engine:espace_client")
        from ..wallet import acces_page_client, page_ouverte_client
        if not page_ouverte_client(entreprise, "ontologie"):   # page fermée aux clients par l'agent
            return redirect("engine:espace_client")
        if not acces_page_client(entreprise, membre, "ontologie"):
            return redirect("/espace-client/paiement/?service=ontologie")
    pages = list(pages_qs)
    page_id = request.GET.get("page")
    page = None
    entreprise_graphe = entreprise
    lecture_seule_partage = False
    if page_id == "principal" and not (membre and membre.role == "lecteur"):
        page = None   # page principale : graphe complet (comportement historique), toujours accessible même quand des pages existent
    elif page_id:
        page = next((p for p in pages if str(p.id) == str(page_id)), None)
        if page is None:
            # Peut-être une page d'une AUTRE entreprise, mise à disposition
            # en lecture seule (voir models.PartageInterAgent).
            page_partagee = PageOntologie.objects.filter(id=page_id).first()
            if page_partagee and _partage_actif(entreprise, "ontologie", page_partagee.id):
                page = page_partagee
                entreprise_graphe = page.entreprise
                lecture_seule_partage = True
            elif membre and membre.role == "lecteur":
                return redirect("engine:espace_client")   # page inconnue ou non mise à disposition : jamais un graphe de repli
    elif pages:
        page = pages[0]

    from .. import ontologie as _onto
    ontologie_prete = True
    membre_pour_filtre = None
    if page is not None and page.mode == "manuel":
        # Mode manuel : graphe entièrement défini par l'utilisateur, sans
        # passer par le moteur d'inférence automatique (voir
        # ontologie.construire_graphe_manuel).
        graphe = _onto.construire_graphe_manuel(page, entreprise_graphe)
    else:
        # Restriction agent (voir ontologie.construire_graphe) : uniquement sur
        # SA propre entreprise — un partage inter-agent explicite (page d'une
        # autre entreprise, lecture_seule_partage) reste géré par sa propre
        # permission (_partage_actif) et n'est pas re-filtré ici.
        membre_pour_filtre = membre if (not lecture_seule_partage and membre and membre.role in ("agent", "lecteur")) else None
        if request.GET.get("etat"):
            # Sondage léger du pop-up « pas encore prête » : l'état de la construction, rien d'autre.
            return JsonResponse(_onto.statut_ontologie(entreprise_graphe, page=page, membre=membre_pour_filtre))
        if request.GET.get("recalculer"):
            _onto.demander_recalcul(entreprise_graphe, page=page, membre=membre_pour_filtre)   # ?recalculer=1 : l'état est refait en arrière-plan
        # CONSULTATION de l'état construit en arrière-plan (comme le modèle fantôme) : jamais de calcul ici. None = pas encore prêt.
        graphe = _onto.consulter_graphe(entreprise_graphe, page=page, membre=membre_pour_filtre)
        if graphe is None:
            ontologie_prete = False
            graphe = _onto.nx.DiGraph()
            graphe.add_node(f"entreprise:{entreprise_graphe.id}", type="entreprise", label=entreprise_graphe.nom)   # canevas valide, rien de plus
    graphe = _onto.restreindre_pour_client(graphe, entreprise_graphe, membre)
    if not ontologie_prete:
        metriques_page = {"noeuds": 1, "aretes": 0, "densite": "…", "diametre": "…", "clustering": "…", "communautes": "…"}
    elif page is not None and page.mode == "manuel":
        metriques_page = _onto.metriques(graphe)
    else:
        metriques_page = _onto.metriques_etat(entreprise_graphe, page, membre_pour_filtre, graphe)   # état déjà calculé en arrière-plan
    # APERÇU HIÉRARCHIQUE : la page ne reçoit jamais plus de LIMITE_APERCU (200) nœuds / relations, pris en largeur à partir d'une racine
    # (l'entreprise par défaut, ou ?racine=<id>). L'ontologie complète reste intacte en arrière-plan ; les métriques ci-dessus portent sur elle.
    from .. import ontologie_apercu as _apercu
    # CELLULES : chaque valeur de table est un nœud (ontologie_cellules.py). Une racine « cellule » ou « ligne » affiche, autour de la ligne,
    # toutes ses cellules ; une racine « cellule » y ajoute les cellules de même valeur trouvées ailleurs (autres lignes, colonnes, tables).
    from .. import ontologie_cellules as _cell
    racine_demandee = request.GET.get("racine") or None
    nid_ligne_racine = _cell.ligne_de(racine_demandee) if racine_demandee else None
    ligne_ok = bool(nid_ligne_racine and nid_ligne_racine in graphe)
    # TRACE CONTINUE (?suite=1) : le dernier nœud de la trace est devenu la racine ; les derniers nœuds de la trace en cours (mémorisée côté serveur,
    # personnelle) restent dans l'aperçu avec leurs liens, pour que la trace se poursuive sans disparaître (voir ontologie_apercu.LIMITE_TRACE).
    trace_a_garder = None
    if request.GET.get("suite") == "1":
        from ..models import TraceOntologie
        trace_courante = TraceOntologie.objects.filter(entreprise=entreprise, utilisateur=request.user, page_ref=page.id if page else None, courante=True).first()
        trace_a_garder = list(trace_courante.noeuds) if trace_courante else None
    graphe_affiche, apercu_meta = _apercu.apercu_hierarchique(graphe, nid_ligne_racine if ligne_ok else racine_demandee, inclure=trace_a_garder)
    if ligne_ok:
        lectures_cellules = None
        if _cell.decoder(racine_demandee):
            try:
                from ..views_ontologie_apercu import _lectures_tables
                lectures_cellules = list(_lectures_tables(graphe, entreprise_graphe))
            except Exception:
                __import__("logging").getLogger(__name__).exception("ontologie : lecture des tables pour les cellules de même valeur")
        ajoutes = _cell.enrichir_apercu(graphe_affiche, graphe, entreprise_graphe, racine_demandee, lectures_cellules)
        if ajoutes:
            if _cell.decoder(racine_demandee) and racine_demandee in graphe_affiche:
                apercu_meta["racine"] = racine_demandee
                apercu_meta["chemin"] = list(apercu_meta["chemin"]) + [racine_demandee]
            apercu_meta["noeuds_affiches"] = graphe_affiche.number_of_nodes()
            apercu_meta["aretes_affichees"] = graphe_affiche.number_of_edges()
            apercu_meta["tronque"] = True
    apercu_meta["chemin"] = [
        {"id": i, "label": str(_onto.libelle_noeud((graphe.nodes[i] if i in graphe else graphe_affiche.nodes[i]), i)),
         "type": (graphe.nodes[i] if i in graphe else graphe_affiche.nodes[i]).get("type", "?")}
        for i in apercu_meta["chemin"] if i in graphe or i in graphe_affiche]
    apercu_meta["limite"] = _apercu.LIMITE_APERCU
    contexte = {
        "entreprise": entreprise,
        "ontologie_prete": ontologie_prete,
        "graphe_json": json.dumps(_onto.graphe_vers_json(graphe_affiche, entreprise=entreprise_graphe, page=page)),
        "apercu_json": json.dumps(apercu_meta).replace("<", "\\u003c"),
        "metriques": metriques_page,
        "pages_ontologie": [_serialiser_page_ontologie(p) for p in pages],
        "pages_ontologie_json": json.dumps([_serialiser_page_ontologie(p) for p in pages]).replace("<", "\\u003c"),
        "page_courante_id": page.id if page else None,
        "page_courante_mode": page.mode if page else "auto",
        "lecture_seule": lecture_seule_partage or bool(membre and membre.role == "lecteur"),
    }
    return render(request, "engine/ontologie.html", contexte)


def _serialiser_page_ontologie(p):
    return {
        "id": p.id, "nom": p.nom, "slug": p.slug, "description": p.description,
        "ordre": p.ordre, "visible_client": p.visible_client, "mode": p.mode,
    }


@login_required
def api_pages_ontologie(request):
    """Liste / crée les pages d'ontologie de l'entreprise (même principe
    que api_tableaux_bord_bi pour le dashboard BI)."""
    entreprise = _entreprise_courante(request)
    if request.method == "GET":
        pages = PageOntologie.objects.filter(entreprise=entreprise)
        membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
        if membre and membre.role == "lecteur":
            from ..wallet import page_ouverte_client
            pages = pages.filter(visible_client=True) if page_ouverte_client(entreprise, "ontologie") else pages.none()   # page fermée aux clients par l'agent
        return JsonResponse({"pages_ontologie": [_serialiser_page_ontologie(p) for p in pages]})

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:706:45")}, status=400)
        from django.utils.text import slugify
        slug = slugify(nom) or f"page-{PageOntologie.objects.filter(entreprise=entreprise).count() + 1}"
        base_slug, n = slug, 1
        while PageOntologie.objects.filter(entreprise=entreprise, slug=slug).exists():
            n += 1
            slug = f"{base_slug}-{n}"
        mode = corps.get("mode") if corps.get("mode") in ("auto", "manuel") else "auto"
        page = PageOntologie.objects.create(
            entreprise=entreprise, nom=nom, slug=slug,
            description=corps.get("description", ""), mode=mode,
        )
        if mode == "manuel":
            # Sélection initiale des tables (et, par défaut, toutes leurs
            # colonnes) : le reste du graphe manuel (relations) se construit
            # ensuite à la main sur le canevas — voir api_page_ontologie_manuel_relations.
            tables = TableDynamique.objects.filter(entreprise=entreprise, id__in=corps.get("tables") or [])
            page.tables_manuelles.set(tables)
            page.champs_manuels.set(ChampDynamique.objects.filter(table__in=tables))
        return JsonResponse({"page_ontologie": _serialiser_page_ontologie(page)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_003.py:727:37")}, status=405)


@login_required
def api_page_ontologie_manuel_tables(request, page_id):
    """Ajoute une table (et toutes ses colonnes) au graphe d'une page
    d'ontologie en mode manuel — voir ontologie.construire_graphe_manuel."""
    entreprise = _entreprise_courante(request)
    page = get_object_or_404(PageOntologie, id=page_id, entreprise=entreprise, mode="manuel")
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    corps = json.loads(request.body or "{}")
    table = get_object_or_404(TableDynamique, id=corps.get("table_id"), entreprise=entreprise)
    page.tables_manuelles.add(table)
    page.champs_manuels.add(*table.champs.all())
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST", "DELETE"])
def api_page_ontologie_manuel_relations(request, page_id):
    """Crée (POST) ou supprime (DELETE, body {id}) une relation manuelle
    entre deux nœuds d'une page d'ontologie en mode manuel."""
    entreprise = _entreprise_courante(request)
    page = get_object_or_404(PageOntologie, id=page_id, entreprise=entreprise, mode="manuel")
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    corps = json.loads(request.body or "{}")
    if request.method == "POST":
        source, cible = corps.get("source"), corps.get("cible")
        if not source or not cible:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:760:45")}, status=400)
        relation = {
            "id": uuid.uuid4().hex[:8], "source": source, "cible": cible,
            "relation": corps.get("relation") or "lien", "label": corps.get("label", ""),
        }
        page.relations_manuelles = page.relations_manuelles + [relation]
        page.save(update_fields=["relations_manuelles"])
        return JsonResponse({"relation": relation}, status=201)
    page.relations_manuelles = [r for r in page.relations_manuelles if r.get("id") != corps.get("id")]
    page.save(update_fields=["relations_manuelles"])
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_page_ontologie_description(request, page_id):
    """Enregistre la note libre d'un nœud ou d'une relation du graphe
    (page.descriptions_noeuds / descriptions_relations) — disponible en mode
    auto comme en mode manuel, purement informatif. body : {type: "noeud"
    ou "relation", cle: "table:5" ou "u→v:relation", texte: "..."}."""
    entreprise = _entreprise_courante(request)
    page = get_object_or_404(PageOntologie, id=page_id, entreprise=entreprise)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    corps = json.loads(request.body or "{}")
    cle, texte = corps.get("cle"), (corps.get("texte") or "").strip()
    if not cle or corps.get("type") not in ("noeud", "relation"):
        return JsonResponse({"erreur": t("engine/views/_part_003.py:788:41")}, status=400)
    champ = "descriptions_noeuds" if corps["type"] == "noeud" else "descriptions_relations"
    valeurs = dict(getattr(page, champ))
    if texte:
        valeurs[cle] = texte
    else:
        valeurs.pop(cle, None)
    setattr(page, champ, valeurs)
    page.save(update_fields=[champ])
    return JsonResponse({"ok": True, champ: valeurs})


@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def api_page_ontologie_detail(request, page_id):
    entreprise = _entreprise_courante(request)
    page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()
    partage = False
    if page is None:
        page = get_object_or_404(PageOntologie, id=page_id)
        if request.method != "GET" or not _partage_actif(entreprise, "ontologie", page.id):
            raise Http404
        partage = True  # page d'une autre entreprise, mise à disposition en lecture seule

    if request.method == "GET":
        membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
        if not partage and membre and membre.role == "lecteur" and not page.visible_client:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:815:45")}, status=403)
        if not partage and membre and membre.role == "lecteur" and page.visible_client:
            from ..wallet import acces_page_client, page_ouverte_client
            if not page_ouverte_client(entreprise, "ontologie"):   # page fermée aux clients par l'agent
                return JsonResponse({"erreur": t("engine/views/_part_003.py:815:45")}, status=403)
            if not acces_page_client(entreprise, membre, "ontologie"):
                return JsonResponse({"erreur": t("engine/views/_part_003.py:819:49"), "service": "ontologie", "redirect": "/espace-client/paiement/?service=ontologie"}, status=402)
        return JsonResponse({"page_ontologie": _serialiser_page_ontologie(page)})

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        page.delete()
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")
    for champ in ("nom", "description", "ordre", "visible_client"):
        if champ in corps:
            setattr(page, champ, bool(corps[champ]) if champ == "visible_client" else corps[champ])
    page.save()
    return JsonResponse({"page_ontologie": _serialiser_page_ontologie(page)})


def _serialiser_couleur_ontologie(c):
    return {
        "id": c.id, "cible": c.cible, "cle": c.cle, "couleur": c.couleur,
        "label": c.label, "description": c.description,
    }


@login_required
def api_couleurs_ontologie(request):
    """GET : liste les couleurs personnalisées de l'entreprise (overrides
    de types/relations existants + types/relations nouvellement déclarés).
    POST : crée ou met à jour (upsert sur cible+cle) une couleur — même
    endpoint pour repeindre un type déjà connu de la plateforme ET pour
    déclarer un tout nouveau type/relation (avec label/description), qui
    devient alors immédiatement utilisable dans une CoucheOntologie et
    apparaît dans le sélecteur d'événements (voir
    ontologie.lister_types_evenements). Corps attendu : {cible: "noeud"|
    "arete", cle, couleur, label?, description?}."""
    entreprise = _entreprise_courante(request)
    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        cible = corps.get("cible")
        cle = (corps.get("cle") or "").strip()
        couleur = (corps.get("couleur") or "").strip()
        if cible not in ("noeud", "arete"):
            return JsonResponse({"erreur": t("engine/views/_part_003.py:866:45")}, status=400)
        if not cle:
            return JsonResponse({"erreur": t("engine/views/_part_003.py:868:45")}, status=400)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", couleur):
            return JsonResponse({"erreur": t("engine/views/_part_003.py:870:45")}, status=400)

        from django.utils.text import slugify
        cle = slugify(cle).replace("-", "_")
        couleur_obj, _ = CouleurOntologie.objects.update_or_create(
            entreprise=entreprise, cible=cible, cle=cle,
            defaults={
                "couleur": couleur,
                "label": corps.get("label", "").strip(),
                "description": corps.get("description", "").strip(),
            },
        )
        return JsonResponse({"couleur": _serialiser_couleur_ontologie(couleur_obj)}, status=201)

    couleurs = CouleurOntologie.objects.filter(entreprise=entreprise)
    return JsonResponse({"couleurs": [_serialiser_couleur_ontologie(c) for c in couleurs]})


@login_required
@require_http_methods(["DELETE"])
def api_couleur_ontologie_detail(request, couleur_id):
    """Supprime une couleur personnalisée : le type/relation retombe sur sa
    couleur générique par défaut si prédéfini par la plateforme, ou
    disparaît du catalogue s'il avait été déclaré uniquement ici."""
    entreprise = _entreprise_courante(request)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    couleur = get_object_or_404(CouleurOntologie, id=couleur_id, entreprise=entreprise)
    couleur.delete()
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["GET"])
def api_limite_enregistrements_ontologie_lire(request):
    """Retourne le plafond actuel d'enregistrements par table affichés dans
    le graphe d'ontologie (voir ConfigurationIA.limite_enregistrements_ontologie)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    return JsonResponse({"limite_enregistrements_ontologie": cfg.limite_enregistrements_ontologie})




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
