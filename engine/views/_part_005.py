from engine.i18n import tf
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t
from ._header import *  # noqa
from ..calc_champ import verifier_types_formule  # noqa
# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from ._part_004 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_widget_bi_detail(request, widget_id):
    """PATCH : déplace/redimensionne un widget (position_x/y, largeur,
    hauteur, ordre_z) sans recalcul — ou change ses colonnes/son graphique,
    ce qui relance un rendu asynchrone. DELETE : supprime le widget."""
    entreprise = _entreprise_courante(request)
    widget = get_object_or_404(WidgetGraphiqueBI, id=widget_id, tableau_bord__entreprise=entreprise)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        widget.delete()
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")

    # Déplacement/redimensionnement pur (drag & resize sur le canevas) :
    # aucune donnée affichée ne change, pas besoin de relancer le rendu.
    champs_position = ("position_x", "position_y", "largeur", "hauteur", "ordre_z", "titre", "description")
    for champ in champs_position:
        if champ in corps:
            setattr(widget, champ, corps[champ])

    # Couleur de la carte (mise en forme pure : aucun recalcul). Nom, hexadécimal ou "" (retirer) ; validée comme celle d'une colonne.
    if "couleur" in corps:
        from ..couleur_colonne import normaliser_couleur
        try:
            widget.couleur = normaliser_couleur(corps["couleur"])
        except ValueError as e:
            return JsonResponse({"erreur": str(e)}, status=400)

    # Zone de texte : édition du contenu/style, jamais de recalcul (rien à
    # calculer — voir tableau_bord_bi.calculer_widget).
    if widget.type_widget == "texte":
        if "contenu_texte" in corps:
            widget.contenu_texte = (corps.get("contenu_texte") or "").strip()
        if "style_texte" in corps and corps["style_texte"] in dict(WidgetGraphiqueBI.STYLE_TEXTE_CHOICES):
            widget.style_texte = corps["style_texte"]
        widget.save()
        return JsonResponse({"widget": _serialiser_widget_bi(widget)})

    doit_recalculer = False
    if "graphique_id" in corps and corps["graphique_id"] != widget.graphique_id:
        graphique = GraphiqueBI.objects.filter(
            id=corps["graphique_id"]
        ).filter(Q(entreprise__isnull=True) | Q(entreprise=entreprise)).first()
        if not graphique:
            return JsonResponse({"erreur": t("engine/views/_part_005.py:51:45")}, status=404)
        widget.graphique = graphique
        doit_recalculer = True
    if "options" in corps:
        widget.options_json = corps["options"] or None

    widget.save()

    if "champs_cibles_ids" in corps:
        ids = corps.get("champs_cibles_ids") or []
        champs_valides = list(ChampDynamique.objects.filter(id__in=[i for i in ids if isinstance(i, int) and i > 0], table=widget.tableau_bord.table_source))
        widget.champs_cibles.set(champs_valides)
        from .. import identifiants_analyse as _ident   # matricule / clé d'accès : ids négatifs, lecture seule (voir ce module)
        virtuelles = _ident.ids_valides(widget.tableau_bord.table_source, ids)
        ids = [i for i in ids if i in virtuelles or i in {c.id for c in champs_valides} or (isinstance(i, str) and i.startswith("mesure:"))]
        widget.ordre_champs = ids or None
        widget.save(update_fields=["ordre_champs"])
        doit_recalculer = True

    if doit_recalculer:
        from ..tableau_bord_bi import calculer_widget
        calculer_widget(widget)

    return JsonResponse({"widget": _serialiser_widget_bi(widget)})


@login_required
@require_http_methods(["GET"])
def api_statut_widget_bi(request, widget_id):
    """Polling léger : retourne uniquement le statut, ou le widget complet
    (avec l'image base64) une fois le rendu terminé — même convention que
    api_statut_demande."""
    entreprise = _entreprise_courante(request)
    widget = get_object_or_404(WidgetGraphiqueBI, id=widget_id, tableau_bord__entreprise=entreprise)
    _refus = _refus_tableau_bi_client(request, widget.tableau_bord)
    if _refus:
        return _refus
    if widget.statut in ("pret", "echec"):
        return JsonResponse({"fini": True, "widget": _serialiser_widget_bi(widget)})
    return JsonResponse({"fini": False, "statut": widget.statut})


@login_required
@require_http_methods(["POST"])
def api_recalculer_tableau_bord_bi(request, tableau_id):
    """Relance le rendu des widgets d'un tableau de bord. Par défaut, ne
    relance que les widgets périmés par rapport à l'état actuel de la table
    source (?tout=1 force le recalcul de tous les widgets). C'est ce
    endpoint que le front appelle pour l'interaction live : après toute
    modification de la table, il suffit de le rappeler pour que le canevas
    se remette à jour, widget par widget, sans bloquer la page."""
    entreprise = _entreprise_courante(request)
    tableau_bord = get_object_or_404(TableauBordBI, id=tableau_id, entreprise=entreprise)
    _refus = _refus_tableau_bi_client(request, tableau_bord)   # le rafraîchissement automatique d'un client reste permis, sur ses tableaux seulement
    if _refus:
        return _refus

    from ..tableau_bord_bi import calculer_widget, widgets_a_recalculer
    colonne_f, valeur_f = request.GET.get("colonne_filtre"), request.GET.get("valeur_filtre")
    if _client_sans_filtre(request, tableau_bord.table_source):
        colonne_f = valeur_f = None
    if colonne_f and valeur_f:
        # Filtre croisé actif : recalcul à la volée de TOUS les widgets sur le
        # sous-ensemble filtré, sans écraser le cache non filtré (sauvegarder=
        # False) — c'est ce qui fait "discuter" les graphiques entre eux.
        filtre = {"colonne": colonne_f, "valeur": valeur_f}
        widgets = list(tableau_bord.widgets.exclude(type_widget="carte").select_related("graphique").prefetch_related("champs_cibles"))   # la carte a quitté la BI
        resultats = []
        for w in widgets:
            image, erreur = calculer_widget(w, sauvegarder=False, filtre=filtre)
            est_carte = w.type_widget == "carte"  # pour une carte, le 1er retour est le HTML (ou l'image de repli), pas un PNG de graphique
            resultats.append({"id": w.id, "image_base64": "" if est_carte else (image or ""),
                              "html_carte": (image or "") if est_carte else "", "erreur": erreur or ""})
        return JsonResponse({"widgets_filtres": resultats})

    seulement_perimes = request.GET.get("tout") != "1"
    a_relancer = widgets_a_recalculer(tableau_bord) if seulement_perimes else list(
        tableau_bord.widgets.select_related("graphique").prefetch_related("champs_cibles")
    )
    ids = [w.id for w in a_relancer]
    # Rendu synchrone (voir api_widgets_bi) : chaque widget périmé est
    # recalculé tout de suite dans la requête, pas via Celery. Le front
    # reçoit la liste des widgets déjà à jour ; le petit polling qui reste
    # côté JS ne fait donc qu'une seule itération (le statut est déjà
    # "pret"/"echec" au moment où il interroge).
    for widget in a_relancer:
        try:
            calculer_widget(widget)
        except Exception:
            widget.statut = "echec"
            widget.erreur = t("engine/views/_part_005.py:g:140:29")
            widget.save(update_fields=["statut", "erreur"])

    return JsonResponse({"widgets_en_cours": ids})


@login_required
@require_http_methods(["GET"])
@_pdf_erreur_json
def api_exporter_dashboard_bi_pdf(request, tableau_id):
    """Exporte le tableau de bord BI en PDF.
    ?orientation=portrait|paysage|paysage_large|paysage_A5 (défaut: paysage)"""
    entreprise = _entreprise_courante(request)
    tableau_bord = get_object_or_404(TableauBordBI, id=tableau_id, entreprise=entreprise)
    _refus = _refus_tableau_bi_client(request, tableau_bord)
    if _refus:
        return _refus
    orientation = request.GET.get("orientation", "paysage")

    # Le PDF doit être une reconstruction fidèle de ce que l'utilisateur voit
    # à l'instant T dans le dashboard (widgets ET bandeau KPI) : on rafraîchit
    # d'abord tout ce qui est périmé par rapport aux données actuelles de la
    # table source (même logique que le rafraîchissement live du canevas),
    # avant de lire les valeurs/images en base pour les injecter dans le PDF.
    from ..tableau_bord_bi import calculer_widget, widgets_a_recalculer
    for widget in widgets_a_recalculer(tableau_bord):
        calculer_widget(widget)

    from ..kpi import calculer_kpi, kpis_a_recalculer
    for kpi in kpis_a_recalculer(tableau_bord.table_source):
        calculer_kpi(kpi)

    from ..reports import generer_pdf_dashboard_bi
    from django.http import HttpResponse
    pdf_bytes = generer_pdf_dashboard_bi(tableau_bord, orientation=orientation)
    reponse = HttpResponse(pdf_bytes, content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="dashboard_{tableau_bord.id}.pdf"'
    return reponse


# ── Indicateurs KPI (définition + suivi, voir models.IndicateurKPI /
# ValeurKPIHistorique et kpi.py) ─────────────────────────────────────────
_OPERATEURS_ETAPES_KPI = ("somme", "difference", "produit", "quotient", "moyenne", "pourcentage", "min", "max")


def _programme_kpi_valide(brut, bibliotheque=False):
    """Programme de formules d'un KPI ou d'une table de calcul (même format que la table dynamique, voir formules_programme.py :
    calc / poser / si-sinonsi-sinon / pour / tantque (+ sinon de boucle) / arreter / continuer / passer / retourner)
    → (liste propre, erreur). `bibliotheque=True` : une brique peut aussi être une formule de la bibliothèque (op « personnalisee »)."""
    from ..formules_programme import COMPARATEURS, ETAPES_SIMPLES
    compte = [0]

    def _cond(c, prof=0):
        if not isinstance(c, dict) or prof > 8: raise ValueError(t("engine/views/_part_005.py:g:189:66"))
        for k in ("et", "ou"):
            if k in c:
                if not isinstance(c[k], list) or not c[k]: raise ValueError(t("engine/views/_part_005.py:g:192:77"))
                return {k: [_cond(x, prof + 1) for x in c[k]]}
        if "non" in c: return {"non": _cond(c["non"], prof + 1)}
        if str(c.get("o", "==")) not in COMPARATEURS: raise ValueError(_gt("rg8.msg.015"))
        return {"g": str(c.get("g", "")), "o": str(c.get("o", "==")), "d": str(c.get("d", ""))}

    def _liste(liste, prof, boucle):
        if not isinstance(liste, list) or prof > 8: raise ValueError(t("engine/views/_part_005.py:g:199:70"))
        sortie = []
        for e in liste:
            compte[0] += 1
            if compte[0] > 200 or not isinstance(e, dict): raise ValueError(t("engine/views/_part_005.py:g:203:77"))
            typ = e.get("t")
            sous = lambda cle, dans_boucle=boucle: _liste(e.get(cle) or [], prof + 1, dans_boucle)   # noqa: E731
            if typ == "calc":
                ops = [str(o) for o in (e.get("ops") or [])]
                perso = bibliotheque and e.get("op") == "personnalisee"
                if (e.get("op") not in _OPERATEURS_ETAPES_KPI and not perso) or not ops:
                    raise ValueError(t("engine/views/_part_005.py:g:210:38"))
                brique = {"t": "calc", "op": e["op"], "ops": ops, "var": str(e.get("var") or "resultat")[:40]}
                if perso:
                    brique["formule_id"] = int(e.get("formule_id"))
                sortie.append(brique)
            elif typ == "poser":
                sortie.append({"t": "poser", "var": str(e.get("var") or "resultat")[:40], "val": str(e.get("val", ""))})
            elif typ == "si":
                branches = [{"cond": _cond(b.get("cond")), "alors": _liste(b.get("alors") or [], prof + 1, boucle)}
                            for b in e.get("sinonsi") or [] if isinstance(b, dict)]
                etape = {"t": "si", "cond": _cond(e.get("cond")), "alors": sous("alors"), "sinon": sous("sinon")}
                sortie.append({**etape, "sinonsi": branches} if branches else etape)
            elif typ in ("tantque", "pour"):
                if typ == "tantque":
                    etape = {"t": typ, "cond": _cond(e.get("cond"))}
                elif e.get("dans") is not None:
                    etape = {"t": typ, "var": str(e.get("var") or "i")[:40], "dans": [str(x) for x in e["dans"]]}
                else:
                    etape = {"t": typ, "var": str(e.get("var") or "i")[:40], "de": e.get("de", 1), "a": e.get("a", 1)}
                    if e.get("pas") not in (None, ""): etape["pas"] = e["pas"]
                etape["corps"] = sous("corps", True)
                if e.get("sinon"): etape["sinon"] = sous("sinon")   # for/else, while/else : s'exécute sans « arreter »
                sortie.append(etape)
            elif typ in ETAPES_SIMPLES or typ == "retourner":
                if typ in ("arreter", "continuer") and not boucle: raise ValueError(tf("engine/views/_part_005.py:g:234:83", v1=typ))
                sortie.append({"t": typ, **({"val": str(e.get("val", ""))} if typ == "retourner" and e.get("val") not in (None, "") else {})})
            else:
                raise ValueError(t("engine/views/_part_005.py:g:237:34"))
        return sortie

    try:
        propre = _liste(brut, 0, False)
    except (ValueError, TypeError) as exc:
        return None, str(exc)
    return propre, None


def _etapes_kpi_valides(brutes, table):
    """Valide les formules enchaînées d'un KPI → (liste propre, message d'erreur). Un opérande = colonne de la table
    ou étape précédente (r1, r2…) ; seuls les opérateurs prédéfinis de la gestion des données sont acceptés."""
    if not brutes:
        return [], None
    if isinstance(brutes, list) and any(isinstance(e, dict) and "t" in e for e in brutes):
        return _programme_kpi_valide(brutes)
    slugs, propres = set(table.champs.values_list("slug", flat=True)), []
    for n, e in enumerate(brutes, start=1):
        operandes = [str(o) for o in (e.get("operandes") or [])] if isinstance(e, dict) else []
        if not isinstance(e, dict) or e.get("operateur") not in _OPERATEURS_ETAPES_KPI or not operandes:
            return None, tf("engine/views/_part_005.py:g:258:26", v1=n)
        inconnus = [o for o in operandes if o not in slugs and o not in {f"r{i}" for i in range(1, n)}]
        if inconnus:
            return None, tf("engine/views/_part_005.py:g:261:26", v1=n, v2=', '.join(inconnus))
        propres.append({"operateur": e["operateur"], "operandes": operandes})
    return propres, None


def _serialiser_kpi(k, avec_historique=False, limite_historique=60):
    data = {
        "id": k.id, "nom": k.nom, "description": k.description,
        "table_source_id": k.table_source_id,
        "champ_cible_id": k.champ_cible_id,
        "champ_cible_nom": k.champ_cible.nom if k.champ_cible_id else None,
        "formule": _serialiser_formule(k.formule),
        "etapes": k.etapes or [],
        "unite": k.unite,
        "valeur_cible": k.valeur_cible,
        "seuil_alerte": k.seuil_alerte,
        "sens_objectif": k.sens_objectif,
        "ordre": k.ordre,
        "visible_client": k.visible_client,
        "statut": k.statut,
        "valeur_actuelle": k.valeur_actuelle,
        "etat": k.etat,
        "variation_pct": k.variation_pct,
        "erreur": k.erreur,
        "calcule_le": k.calcule_le.isoformat() if k.calcule_le else None,
    }
    if avec_historique:
        points = k.historique.order_by("-calcule_le")[:limite_historique]
        data["historique"] = [
            {"valeur": p.valeur, "calcule_le": p.calcule_le.isoformat()} for p in reversed(points)
        ]
    return data


@login_required
def api_kpis(request, table_id):
    """Liste / crée les indicateurs KPI définis sur une table source. La
    liste (GET) recalcule d'abord les KPI périmés (même principe que le
    canevas BI : rendu synchrone, pas de Celery — un calcul de formule sur
    une colonne prend quelques millisecondes)."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    _bloque_lecteur = _interdit_table_bi(request, table)
    if _bloque_lecteur:
        return _bloque_lecteur

    from ..vue_groupe import ancre_groupe, ids_groupe
    _ids_g, _ancre = ids_groupe(table), ancre_groupe(table)   # un groupe = UNE table : mêmes KPI depuis chaque partie, création sur l'ancre
    _kpis_groupe = IndicateurKPI.objects.filter(entreprise=entreprise, table_source_id__in=_ids_g)
    _membre_kpi = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if _membre_kpi and _membre_kpi.role == "lecteur":
        # Un client ne voit que les KPI mis à sa disposition, ou posés sur un tableau de bord qui l'est.
        _sur_tableaux_visibles = list(WidgetGraphiqueBI.objects.filter(
            tableau_bord__entreprise=entreprise, tableau_bord__visible_client=True, type_widget="kpi",
        ).values_list("kpi_id", flat=True))
        _kpis_groupe = _kpis_groupe.filter(Q(visible_client=True) | Q(id__in=_sur_tableaux_visibles))

    if request.method == "GET":
        from ..kpi import calculer_kpi, kpis_a_recalculer
        colonne_f, valeur_f = request.GET.get("colonne_filtre"), request.GET.get("valeur_filtre")
        if _client_sans_filtre(request, table):
            colonne_f = valeur_f = None
        if colonne_f and valeur_f:
            # Filtre croisé actif : recalcul à la volée (jamais persisté ni
            # historisé) de tous les KPI sur le sous-ensemble filtré.
            filtre = {"colonne": colonne_f, "valeur": valeur_f}
            kpis = _kpis_groupe.select_related("formule", "champ_cible").order_by("ordre", "id")
            for k in kpis:
                v, _erreur = calculer_kpi(k, sauvegarder=False, filtre=filtre)
                if v is not None:
                    k.valeur_actuelle = v
            return JsonResponse({"kpis": [_serialiser_kpi(k) for k in kpis]})
        for kpi in kpis_a_recalculer(table):
            try:
                calculer_kpi(kpi)
            except Exception:
                kpi.statut = "echec"
                kpi.erreur = t("engine/views/_part_005.py:g:330:30")
                kpi.save(update_fields=["statut", "erreur"])
        kpis = _kpis_groupe.select_related("formule", "champ_cible").order_by("ordre", "id")
        return JsonResponse({"kpis": [_serialiser_kpi(k) for k in kpis]})

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_005.py:236:45")}, status=400)

        champ = None
        champ_id = corps.get("champ_cible_id")
        if champ_id:
            champ = ChampDynamique.objects.filter(id=champ_id, table_id__in=_ids_g).first()
            if not champ:
                return JsonResponse({"erreur": t("engine/views/_part_005.py:243:49")}, status=404)

        formule_id = corps.get("formule_id")
        formule = FormuleCalcul.objects.filter(
            id=formule_id
        ).filter(Q(entreprise__isnull=True) | Q(entreprise=entreprise)).first()
        if not formule:
            return JsonResponse({"erreur": t("engine/views/_part_005.py:250:45")}, status=404)
        if formule.cible_type != "colonne":
            return JsonResponse(
                {"erreur": t("engine/views/_part_005.py:253:29")},
                status=400,
            )
        etapes, _e = _etapes_kpi_valides(corps.get("etapes"), _ancre)
        if _e: return JsonResponse({"erreur": _e}, status=400)
        if champ and not etapes and (_e := verifier_types_formule(table, "personnalisee", [champ.slug], formule)): return JsonResponse({"erreur": _e}, status=400)
        sens = corps.get("sens_objectif") or "hausse"
        if sens not in dict(IndicateurKPI.SENS_CHOICES):
            sens = "hausse"

        kpi = IndicateurKPI.objects.create(
            entreprise=entreprise, table_source=_ancre, nom=nom,
            description=corps.get("description", ""),
            champ_cible=champ, formule=formule, etapes=etapes,
            unite=(corps.get("unite") or "").strip(),
            valeur_cible=corps.get("valeur_cible"),
            seuil_alerte=corps.get("seuil_alerte"),
            sens_objectif=sens,
            ordre=corps.get("ordre", _kpis_groupe.count()),
            visible_client=bool(corps.get("visible_client", False)),
        )
        from ..kpi import calculer_kpi
        calculer_kpi(kpi)
        return JsonResponse({"kpi": _serialiser_kpi(kpi)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_005.py:276:37")}, status=405)


@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def api_kpi_detail(request, kpi_id):
    """GET : détail du KPI, avec son historique (pour la courbe de
    tendance). PATCH : modifie sa définition — tout changement portant sur
    ce qui influence la valeur (colonne, formule) relance immédiatement le
    calcul. DELETE : supprime le KPI (et son historique, en cascade)."""
    entreprise = _entreprise_courante(request)
    kpi = get_object_or_404(IndicateurKPI, id=kpi_id, entreprise=entreprise)

    if request.method == "GET":
        return JsonResponse({"kpi": _serialiser_kpi(kpi, avec_historique=True)})

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        kpi.delete()
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")
    doit_recalculer = False

    if "nom" in corps:
        nom = (corps.get("nom") or "").strip()
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_005.py:306:45")}, status=400)
        kpi.nom = nom
    for champ_simple in ("description", "unite"):
        if champ_simple in corps:
            setattr(kpi, champ_simple, corps[champ_simple])
    for champ_simple in ("valeur_cible", "seuil_alerte", "ordre"):
        if champ_simple in corps:
            setattr(kpi, champ_simple, corps[champ_simple])
    if "visible_client" in corps:
        kpi.visible_client = bool(corps["visible_client"])
    if "sens_objectif" in corps:
        sens = corps["sens_objectif"]
        if sens not in dict(IndicateurKPI.SENS_CHOICES):
            return JsonResponse({"erreur": t("engine/views/_part_005.py:319:45")}, status=400)
        kpi.sens_objectif = sens

    if "champ_cible_id" in corps:
        champ_id = corps["champ_cible_id"]
        champ = ChampDynamique.objects.filter(id=champ_id, table=kpi.table_source).first() if champ_id else None
        if champ_id and not champ:
            return JsonResponse({"erreur": t("engine/views/_part_005.py:326:45")}, status=404)
        kpi.champ_cible = champ
        doit_recalculer = True

    if "formule_id" in corps:
        formule = FormuleCalcul.objects.filter(
            id=corps["formule_id"]
        ).filter(Q(entreprise__isnull=True) | Q(entreprise=entreprise)).first()
        if not formule:
            return JsonResponse({"erreur": t("engine/views/_part_005.py:335:45")}, status=404)
        if formule.cible_type != "colonne":
            return JsonResponse(
                {"erreur": t("engine/views/_part_005.py:338:29")},
                status=400,
            )
        kpi.formule = formule
        doit_recalculer = True
    if "etapes" in corps:
        kpi.etapes, _e = _etapes_kpi_valides(corps["etapes"], kpi.table_source)
        if _e: return JsonResponse({"erreur": _e}, status=400)
        doit_recalculer = True
    if kpi.champ_cible and kpi.formule and not kpi.etapes and (_e := verifier_types_formule(kpi.table_source, "personnalisee", [kpi.champ_cible.slug], kpi.formule)): return JsonResponse({"erreur": _e}, status=400)
    kpi.save()

    if doit_recalculer:
        from ..kpi import calculer_kpi
        calculer_kpi(kpi)

    return JsonResponse({"kpi": _serialiser_kpi(kpi, avec_historique=True)})


@login_required
@require_http_methods(["POST"])
def api_recalculer_kpi(request, kpi_id):
    """Force le recalcul immédiat d'un KPI (bouton 🔄 côté front), même si
    l'empreinte des données de la table source n'a pas changé."""
    entreprise = _entreprise_courante(request)
    kpi = get_object_or_404(IndicateurKPI, id=kpi_id, entreprise=entreprise)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    try:
        actions.executer_action("recalculer_kpi", membre, kpi)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_005.py:364:41")}, status=403)
    return JsonResponse({"kpi": _serialiser_kpi(kpi, avec_historique=True)})


@login_required
@require_http_methods(["POST"])
def api_modifier_ligne(request, table_id, ligne_id):
    """Modifie une ou plusieurs cases d'une ligne existante. Seuls les champs
    presents dans le corps de la requete sont modifies (mise a jour partielle)."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme
    _bloque_lecteur = _interdit_table_non_exposee(request, table)
    if _bloque_lecteur:
        return _bloque_lecteur
    enregistrement = _ligne_accessible(request, table, ligne_id, ecriture=True)
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne
    # Blockchain (voir models.Bloc) : etat AVANT modification, capture
    # maintenant car les ValeurChamp vont etre ecrasees plus bas.
    donnees_avant = enregistrement.to_dict_lisible()
    if request.content_type and request.content_type.startswith("multipart/"):
        corps, fichiers = request.POST, request.FILES
    else:
        corps, fichiers = json.loads(request.body or "{}"), {}

    def _est_vide(valeur):
        if isinstance(valeur, str):
            return valeur.strip() == ""
        if isinstance(valeur, list):
            return len(valeur) == 0
        return valeur is None

    entreprise_membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    champs_par_slug = {c.slug: c for c in table.champs.all()}
    # Phase 9 — un champ masque (champs_masques) est aussi INECRIVABLE, pas
    # seulement invisible en lecture : retire du dict AVANT validation, comme
    # s'il n'avait jamais ete fourni (meme branche que "champ non fourni").
    for _slug_masque in _champs_masques_pour(entreprise_membre, table):
        champs_par_slug.pop(_slug_masque, None)

    erreurs_validation = {}
    for slug, champ in champs_par_slug.items():
        if champ.type_champ in ("formule", "constante", "horodatage", "id_client"):
            continue
        if champ.type_champ in ChampDynamique.TYPES_FICHIER:
            if slug not in fichiers:
                continue  # pas de nouveau fichier envoye : on garde l'existant
            from ..securite import verifier_fichier, verifier_taille_champ   # corrompu ou piégé : refusé avant toute écriture
            _refus = verifier_taille_champ(fichiers[slug], champ) or verifier_fichier(fichiers[slug], champ.type_champ)
            if _refus:
                erreurs_validation[slug] = _refus
        elif slug not in corps:
            continue  # champ non fourni : on ne le touche pas
        elif champ.obligatoire and _est_vide(corps[slug]):
            erreurs_validation[slug] = tf("engine/views/_part_005.py:g:525:40", v1=champ.nom)
        else:
            erreur_type = _lieu_vers_coordonnees(champ, corps) or _valider_type_valeur(champ, corps[slug])
            if erreur_type:
                erreurs_validation[slug] = erreur_type
    if erreurs_validation:
        return JsonResponse({
            "erreur": " · ".join(erreurs_validation.values()),
            "champs": erreurs_validation,
        }, status=400)

    if table.est_nosql:
        # ── Table NoSQL : mise à jour partielle du bloc JSON de la ligne
        # (nosql_store.py) — pas de ValeurChamp, pas de relations.
        from .. import nosql_store
        valeurs_nosql = dict(enregistrement.valeurs_json or {})
        for slug, champ in champs_par_slug.items():
            if champ.type_champ in ("formule", "constante", "horodatage", "id_client"):
                continue
            if champ.type_champ in ChampDynamique.TYPES_FICHIER and slug in fichiers:
                from django.core.files.storage import default_storage
                fichier = fichiers[slug]
                chemin = default_storage.save(f"champs/{table.slug}/{slug}/{fichier.name}", fichier)
                valeurs_nosql[slug] = default_storage.url(chemin)
            elif champ.type_champ == "choix_multiple" and hasattr(corps, "getlist") and slug in corps:
                valeurs_nosql[slug] = corps.getlist(slug)
            elif slug in corps:
                valeurs_nosql[slug] = nosql_store.coercer_valeur(champ, corps[slug])
        nosql_store.ecrire_valeurs(enregistrement, valeurs_nosql)
        enregistrement.refresh_from_db()
        # Même remarque qu'à la création (api_lignes) : pas de ValeurChamp ⇒
        # pas de signal post_save, donc recalcul explicite ici.
        from .. import calculs as _calculs
        _calculs.assurer_constantes(enregistrement)
        _calculs.recalculer_champs_formule_ligne(enregistrement)
    else:
        for slug, champ in champs_par_slug.items():
            if champ.type_champ in ("formule", "constante", "horodatage", "id_client"):
                continue  # jamais saisi manuellement (formule : ChampDynamique.formule_expression ; constante : options_choix)
            if champ.type_champ in ChampDynamique.TYPES_FICHIER and slug in fichiers:
                from django.core.files.storage import default_storage
                fichier = fichiers[slug]
                chemin = default_storage.save(f"champs/{table.slug}/{slug}/{fichier.name}", fichier)
                ValeurChamp.objects.update_or_create(
                    enregistrement=enregistrement, champ=champ,
                    defaults={"valeur": default_storage.url(chemin)},
                )
            elif champ.type_champ == "choix_multiple" and hasattr(corps, "getlist") and slug in corps:
                ValeurChamp.objects.update_or_create(
                    enregistrement=enregistrement, champ=champ,
                    defaults={"valeur": ",".join(corps.getlist(slug))},
                )
            elif slug in corps:
                valeur = corps[slug]
                if champ.type_champ == "choix_multiple" and isinstance(valeur, list):
                    valeur = ",".join(valeur)
                elif isinstance(valeur, str):
                    valeur = valeur.strip()
                # str(None) vaut la chaîne littérale "None" — sans ce garde-fou,
                # un null JSON (case explicitement vidée par le front) se
                # retrouvait stocké comme le texte "None" au lieu d'une valeur
                # vide, y compris dans des comparaisons/exports ultérieurs.
                valeur_str = "" if valeur is None else str(valeur)
                # ── Champ relation : distinguer "re-choisir une autre ligne
                # source" (on reçoit l'id de la ligne, via le picker) de
                # "modifier directement la valeur partagée" (colonne
                # bidirectionnelle : on reçoit la nouvelle valeur brute, avec un
                # marqueur `<slug>__edition_directe` envoyé par le widget dédié).
                edition_directe = str(corps.get(f"{slug}__edition_directe", "")).lower() in ("1", "true", "vrai")
                if champ.type_champ == "relation" and champ.relation_bidirectionnelle and edition_directe:
                    vc, _ = ValeurChamp.objects.update_or_create(
                        enregistrement=enregistrement, champ=champ, defaults={"valeur": valeur_str},
                    )
                    # save() (et pas seulement update_or_create) pour redéclencher
                    # le signal de propagation vers la ligne source liée.
                    vc.save(update_fields=["valeur"])
                    continue

                vc, _ = ValeurChamp.objects.update_or_create(
                    enregistrement=enregistrement, champ=champ, defaults={"valeur": valeur_str},
                )
                # ── Mettre à jour ou créer le lien si champ relation ──
                if champ.type_champ == "relation" and champ.relation_vers_id:
                    if valeur:
                        _creer_lien_champ(vc, champ, valeur)
                    else:
                        # Valeur effacée → supprimer le lien
                        from ..models import LienChamp
                        LienChamp.objects.filter(valeur_enfant=vc).delete()

    # Une case modifiée peut faire déborder (ou rentrer dans) la cible d'une
    # formule de calcul (colonne entière, ligne précise, plage...) : on
    # recalcule immédiatement pour que le résultat affiché dans l'onglet
    # Calcul change dynamiquement avec la donnée, sans clic manuel sur
    # « ↻ Recalculer ».
    # Formules ∑ (colonne) et « = » (ligne) : recalcul de cette ligne, puis des
    # autres lignes si une formule en dépend (calc_champ.recalculer_formules_table).
    from ..calc_champ import recalculer_formules_table
    recalculer_formules_table(table, enregistrement)
    from ..calculs import recalculer_lignes_pour_table_source
    recalculer_lignes_pour_table_source(table)

    # Blockchain (voir models.Bloc) : etat APRES modification, une fois les
    # nouvelles ValeurChamp ecrites et les formules recalculees. On ne
    # journalise que si quelque chose a reellement change (une requete PATCH
    # vide, ou qui ne touche que des champs non fournis, ne doit pas produire
    # un bloc identique avant/apres).
    donnees_apres = enregistrement.to_dict_lisible()
    if donnees_avant != donnees_apres:
        from ..models import Bloc
        champs_modifies = sorted(
            k for k in (set(donnees_avant) | set(donnees_apres))
            if donnees_avant.get(k) != donnees_apres.get(k)
        )
        # Liste prête à l'affichage (avant/après par champ modifié) — évite
        # au template d'avoir à faire une recherche par clé dynamique dans
        # deux dictionnaires distincts.
        modifications = [
            {"champ": k, "avant": donnees_avant.get(k), "apres": donnees_apres.get(k)}
            for k in champs_modifies
        ]
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "modification_ligne", "table_id": table.id, "table": table.nom,
            "enregistrement_id": enregistrement.id, "agent_id": request.user.id,
            "modifications": modifications,
        })

    from .. import parquet_store
    parquet_store.planifier_synchro(table)   # le fichier Parquet suit l'EAV tout de suite (voir parquet_store.planifier_synchro)
    ligne_dict = enregistrement.to_dict() | {"_id": enregistrement.id}
    ligne_dict = _resoudre_labels_relation(table, [ligne_dict])[0]
    return JsonResponse({"ligne": ligne_dict})


@login_required
@require_http_methods(["POST"])
def api_supprimer_ligne(request, table_id, ligne_id):
    """Supprime definitivement une ligne (et ses valeurs de champs)."""
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme
    enregistrement = _ligne_accessible(request, table, ligne_id, ecriture=True)
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne
    # Blockchain (voir models.Bloc) : la ligne (et ses ValeurChamp, en
    # cascade) va disparaitre de la base — on capture son contenu reel
    # AVANT suppression, sinon le bloc ne pourrait plus prouver que rien.
    donnees_supprimees = enregistrement.to_dict_lisible()
    enregistrement_id = enregistrement.id
    agent_id = request.user.id
    enregistrement.delete()
    if table.est_nosql:
        from .. import nosql_store
        nosql_store.apres_suppression(table)
    from ..models import Bloc
    Bloc.ajouter_transaction(entreprise.id, {
        "type": "suppression_ligne", "table_id": table.id, "table": table.nom,
        "enregistrement_id": enregistrement_id, "agent_id": agent_id,
        "donnees": donnees_supprimees,
    })
    # Supprimer une ligne change aussi l'assiette des formules (colonne
    # entière, plage de lignes...) : on recalcule pour rester à jour.
    from ..calc_champ import recalculer_formules_table
    recalculer_formules_table(table)
    from ..calculs import recalculer_lignes_pour_table_source
    recalculer_lignes_pour_table_source(table)
    from .. import parquet_store
    parquet_store.planifier_synchro(table)   # la ligne disparaît aussi du fichier Parquet
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_supprimer_lignes_lot(request, table_id):
    """Suppression de plusieurs lignes en une fois (sélection de la grille) : corps {"ids": [..]} (500 max).
    Mêmes droits que la suppression unitaire (isolation agent, refus client) ; les lignes non accessibles sont
    ignorées et comptées. Un bloc d'audit par ligne, UN SEUL recalcul des formules à la fin (au lieu d'un par
    ligne quand le navigateur enchaînait les suppressions)."""
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme
    corps = json.loads(request.body or "{}")
    try:
        ids = [int(i) for i in (corps.get("ids") or [])][:500]
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_005.py:589:41")}, status=400)
    if not ids:
        return JsonResponse({"erreur": t("engine/views/_part_005.py:591:41")}, status=400)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from ..vue_groupe import enregistrements_vue
    # Un groupe s'ouvre comme UNE table : la sélection peut couvrir plusieurs parties, chaque ligne est supprimée dans SA partie.
    lignes = list(_isoler_lignes_agent(request, membre, enregistrements_vue(table).filter(id__in=ids)).select_related("table"))
    from django.db import transaction
    from ..models import Bloc
    with transaction.atomic():
        for enr in lignes:
            donnees = enr.to_dict_lisible()
            enr_id = enr.id
            partie = enr.table
            enr.delete()
            Bloc.ajouter_transaction(entreprise.id, {
                "type": "suppression_ligne", "table_id": partie.id, "table": partie.nom,
                "enregistrement_id": enr_id, "agent_id": request.user.id, "donnees": donnees,
            })
    from ..calc_champ import recalculer_formules_table
    from ..calculs import recalculer_lignes_pour_table_source
    for partie in {enr.table_id: enr.table for enr in lignes}.values():
        if partie.est_nosql:
            from .. import nosql_store
            nosql_store.apres_suppression(partie)
        recalculer_formules_table(partie)
        recalculer_lignes_pour_table_source(partie)
        from .. import parquet_store
        parquet_store.planifier_synchro(partie)   # les lignes disparaissent aussi du fichier Parquet de leur partie
    return JsonResponse({"ok": True, "supprimees": len(lignes), "ignorees": len(set(ids)) - len(lignes)})


@login_required
@require_http_methods(["POST"])
def api_liste_noire_lignes(request, table_id):
    """Ajoute des lignes à la LISTE NOIRE de la table, ou les en retire : corps {"ids": [..], "actif": true|false} (500 max, actif par défaut).
    Une ligne de la liste noire n'est pas supprimée : elle reste dans le tableau, mais toutes les analyses l'ignorent (parquet_store).
    Mêmes droits que la suppression en lot (isolation agent, refus client, table système en lecture seule)."""
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme
    corps = json.loads(request.body or "{}")
    try:
        ids = [int(i) for i in (corps.get("ids") or [])][:500]
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_005.py:liste_noire_ids_invalides")}, status=400)
    if not ids:
        return JsonResponse({"erreur": t("engine/views/_part_005.py:liste_noire_ids_requis")}, status=400)
    actif = bool(corps.get("actif", True))
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from ..vue_groupe import enregistrements_vue
    cibles = list(_isoler_lignes_agent(request, membre, enregistrements_vue(table).filter(id__in=ids)).values_list("id", "table_id"))
    if cibles:
        Enregistrement.objects.filter(id__in=[i for i, _ in cibles]).update(liste_noire=actif)   # update() : ni maj_le ni signal, le fichier Parquet reste frais
    from .. import parquet_store
    return JsonResponse({"ok": True, "modifiees": len(cibles), "ignorees": len(set(ids)) - len(cibles), "actif": actif,
                         "total": len(parquet_store.ids_liste_noire(table))})


@login_required
@require_http_methods(["GET"])
def api_recu_pdf_ligne(request, table_id, ligne_id):
    """Génère et retourne un PDF de reçu pour une ligne soumise via le portail client."""
    from ..reports import generer_recu_pdf_ligne
    from django.http import HttpResponse
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    _bloque = _interdit_table_non_exposee(request, table)
    if _bloque:
        return _bloque
    enregistrement = _ligne_accessible(request, table, ligne_id)
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne
    try:
        pdf_bytes = generer_recu_pdf_ligne(enregistrement, table)
    except Exception as e:  # une erreur de mise en page devient un message lisible, pas une page 500 HTML
        import logging
        logging.getLogger(__name__).exception(_gt("engine/views/_part_005.py:802:47"), ligne_id, table_id)
        return JsonResponse({"erreur": tf("engine/views/_part_005.py:g:756:40", v1=e)}, status=500)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="recu_{table.slug}_{ligne_id}.pdf"'
    return response








# ── TABLEAU DE BORD : chaque demande deja executee (ou en echec) pour cette
# table, affichee en carte (graphe + stats + script + erreur eventuelle).
# Rien n'est re-execute ici : on affiche l'etat deja calcule. ──
@login_required
def api_tableau_bord(request, table_id):
    from ._part_006 import _serialiser_table  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table, lecture_seule_partage = _table_via_partage(request, table_id)

    empreinte_actuelle = empreinte_donnees(table)
    toutes = (
        DemandeAgent.objects.filter(entreprise=table.entreprise, table_cible=table)
        .exclude(statut="en_attente")
        .order_by("-cree_le")
        .prefetch_related("graphiques")
    )
    analyses = [a for a in toutes if a.statut != "en_cours"]
    en_cours = [a for a in toutes if a.statut == "en_cours"]

    def _ser(a):
        return {
            "id": a.id,
            "statut": a.statut,
            "intention": a.texte,
            "code": a.script_genere,
            "resultat": a.resultat_json,
            "couleurs": a.couleurs or {},
            "graphiques": [g.image_base64 for g in a.graphiques.all()],
            "graphiques_legendes": [g.legende for g in a.graphiques.all()],
            "calcule_le": a.cree_le.isoformat(),
            "a_jour": bool(a.empreinte_donnees) and a.empreinte_donnees == empreinte_actuelle,
            "modifie_manuellement": a.modifie_manuellement,
            "erreur_execution": a.erreur,
            "fichier_script": a.fichier_script,
            "fichier_resultat": a.fichier_resultat,
        }

    return JsonResponse({
        "table":    _serialiser_table(table),
        "analyses": [_ser(a) for a in analyses],
        "en_cours": [_ser(a) for a in en_cours],
    })


@login_required
@require_http_methods(["POST"])
def api_actualiser_analyse(request, demande_id):
    """Bouton 'Actualiser' : les donnees ont change, on re-execute le MEME
    script (celui deja ecrit sur disque pour cette demande) -- aucun appel IA."""
    from ._part_006 import _serialiser_demande  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    demande.statut = "en_cours"
    demande.save(update_fields=["statut"])
    _taches.executer_demande_tache.delay(demande.id)
    return JsonResponse({"demande": _serialiser_demande(demande)})


@login_required
@require_http_methods(["POST"])
def api_modifier_script(request, demande_id):
    """Edition manuelle directe du script de cette demande, puis re-execution.
    Le code est TOUJOURS sauvegarde sur disque, que l'execution qui suit
    reussisse ou echoue -- pour que l'utilisateur puisse continuer a
    l'ajuster sans jamais le perdre."""
    from ._part_006 import _serialiser_demande  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    corps = json.loads(request.body or "{}")
    nouveau_code = corps.get("code")
    if nouveau_code is None or not nouveau_code.strip():
        return JsonResponse({"erreur": t("engine/views/_part_005.py:714:41")}, status=400)

    modifier_script_demande(demande, nouveau_code)
    return JsonResponse({"demande": _serialiser_demande(demande)})


@login_required
@require_http_methods(["POST"])
def api_regenerer_script(request, demande_id):
    """Bouton 'Regenerer' : seule action qui rappelle l'IA pour cette demande.
    Ecrase le fichier .py existant avec la nouvelle version. L'execution reste
    a la charge de l'utilisateur via 'Lancer'.

    Premiere vue rebranchee sur la couche Action (engine/actions.py) : la
    permission (role >= agent) et l'audit (JournalAudit) passent desormais
    par executer_action au lieu d'un controle en dur ici."""
    from ._part_006 import _serialiser_demande  # import local (réf. avant sa définition)
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    try:
        actions.executer_action("regenerer_script_demande", membre, demande)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_005.py:736:41")}, status=403)
    return JsonResponse({"demande": _serialiser_demande(demande)})


@login_required
@require_http_methods(["POST"])
def api_confirmer_action_demande(request, demande_id):
    """Bouton 'Confirmer' : exécute réellement une Action que l'agent a
    seulement PROPOSÉE (statut "action_proposee", voir
    agent.py::_tenter_operation_connue et agent_ontology.py — Phase 5).

    Ne relit QUE la proposition déjà stockée dans demande.resultat_json :
    le corps de cette requête n'est jamais utilisé pour choisir l'action ou
    ses paramètres, afin qu'une confirmation détournée ne puisse pas exécuter
    autre chose que ce qui a été proposé. La permission/l'audit/la lignée
    passent par actions.executer_action comme pour tout appel existant."""
    from ._part_006 import _serialiser_demande  # import local (réf. avant sa définition)
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from .. import agent_ontology
    try:
        resultat = agent_ontology.confirmer_et_executer(demande, membre)
    except actions.ActionRefusee as exc:
        return JsonResponse({"erreur": str(exc)}, status=403)
    except Exception as exc:
        demande.statut = "echec"
        demande.erreur = str(exc)
        demande.save()
        return JsonResponse({"erreur": t("engine/views/_part_005.py:764:41"), "demande": _serialiser_demande(demande)}, status=400)

    demande.statut = "execute"
    demande.resultat_json = {
        "type": "action_executee", "action": resultat["nom"], "resume": resultat["resume"],
    }
    demande.save()
    return JsonResponse({"demande": _serialiser_demande(demande)})


# ── AGENT : la demande produit UNIQUEMENT un script (fichier pret) ; il faut
# ensuite explicitement "Lancer" pour l'executer -- rien n'est automatique. ──

@login_required
@require_http_methods(["GET"])
def api_statut_demande(request, demande_id):
    """Polling leger : retourne uniquement le statut + donnees completes si termine."""
    from ._part_006 import _serialiser_demande  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    if demande.statut in ("execute", "echec", "script_genere"):
        return JsonResponse({"fini": True, "demande": _serialiser_demande(demande)})
    return JsonResponse({"fini": False, "statut": demande.statut})


@login_required
@require_http_methods(["POST"])
def api_supprimer_demande(request, demande_id):
    """Supprime definitivement une analyse : BD + script .py + resultat .json + graphiques .png.
    Atomique : si la suppression BD echoue, les fichiers ne sont pas touches.
    Si la suppression BD reussit, les fichiers sont nettoyes (erreurs silencieuses)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    import glob
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)

    # Memoriser les chemins AVANT suppression
    chemin_script   = demande.fichier_script or ""
    chemin_resultat = demande.fichier_resultat or ""
    # Graphiques sur disque : demande_<id>_graphique_*.png dans le meme dossier
    dossier_media = dj_settings.MEDIA_ROOT
    slug_table = demande.table_cible.slug if demande.table_cible else None

    # Suppression BD en premier (atomique)
    demande.delete()

    # Nettoyage fichiers (best-effort, pas de rollback possible)
    for chemin in [chemin_script, chemin_resultat]:
        if chemin:
            try:
                if os.path.isfile(chemin):
                    os.remove(chemin)
            except OSError:
                pass

    if slug_table:
        pattern = os.path.join(dossier_media, "scripts_agent", slug_table,
                               f"demande_{demande_id}_graphique_*.png")
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except OSError:
                pass

    return JsonResponse({"ok": True})


def _table_pour_demande(request, corps):
    """Résout la table cible d'une demande d'analyse IA / d'un document :
    par slug dans l'entreprise courante (comportement historique), ou par
    table_id si c'est une table d'une AUTRE entreprise mise à disposition en
    lecture seule (PartageInterAgent, type_objet='table') — sûr car
    l'exécuteur (agent.executer_demande / executer_document) ne lit jamais
    que CETTE table/CET enregistrement précis, jamais le reste de
    l'entreprise source (voir agent.py: table_vers_dict(table) /
    enreg.to_dict())."""
    entreprise = _entreprise_courante(request)
    if corps.get("table_id"):
        table, lecture_seule = _table_via_partage(request, corps["table_id"])
        return table
    return TableDynamique.objects.filter(entreprise=entreprise, slug=corps.get("table_slug")).first()


@login_required
@require_http_methods(["POST"])
def api_creer_demande(request):
    """Crée une carte d'analyse vide (sans appel IA).
    Le script peut être écrit manuellement ou généré ensuite via /api/demandes/<id>/regenerer/.
    Body JSON :
      texte       (str) — libellé / titre de la demande
      table_slug  (str, optionnel)
    """
    from ._part_006 import _serialiser_demande  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    corps = json.loads(request.body or "{}")
    texte = (corps.get("texte") or "").strip()
    if not texte:
        return JsonResponse({"erreur": t("engine/views/_part_005.py:870:41")}, status=400)

    table = _table_pour_demande(request, corps)

    demande = DemandeAgent.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        table_cible=table,
        texte=texte,
        script_genere="",
        statut="script_genere",  # carte immédiatement éditable
    )
    return JsonResponse({"demande": _serialiser_demande(demande)}, status=201)


@login_required
@require_http_methods(["POST"])
def api_demander(request):
    from ._part_006 import _serialiser_demande  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    corps = json.loads(request.body or "{}")
    texte = (corps.get("texte") or "").strip()
    if not texte:
        return JsonResponse({"erreur": t("engine/views/_part_005.py:898:41")}, status=400)

    table = _table_pour_demande(request, corps)

    # Garde anti-doublon : si une demande identique est deja en attente ou
    # en attente de lancement, on la retourne sans relancer l'IA.
    demande_existante = DemandeAgent.objects.filter(
        entreprise=entreprise,
        table_cible=table,
        texte=texte,
        statut__in=["en_attente", "script_genere"],
    ).order_by("-cree_le").first()
    if demande_existante:
        return JsonResponse({"demande": _serialiser_demande(demande_existante)}, status=200)

    demande = DemandeAgent.objects.create(
        entreprise=entreprise, utilisateur=request.user, table_cible=table, texte=texte,
        statut="en_cours",
    )
    # Appel IA (generation du script) delegue a Celery : la reponse HTTP
    # part immediatement avec statut="en_cours", le front continue de
    # suivre l'avancement via le polling existant (api_statut_demande).
    _taches.generer_script_demande_tache.delay(demande.id)
    return JsonResponse({"demande": _serialiser_demande(demande)}, status=201)




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
