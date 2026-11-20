from engine.i18n import t
from engine.i18n import tf
from ..delegation import a_pouvoir as _a_pouvoir
from ._header import *  # noqa
from ..calc_champ import verifier_types_formule  # noqa
# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from ._part_004 import *  # noqa
from ._part_005 import *  # noqa
from ._part_006 import *  # noqa
from ._part_007 import *  # noqa
from ._part_008 import *  # noqa
from ._part_009 import *  # noqa


@login_required
@require_http_methods(["GET", "POST", "DELETE"])

def api_partage_cellule(request, table_id, ligne_id, champ_id):
    """Gère le partage de CELLULE d'une valeur précise (ligne + champ).

    Même logique que `api_partage_ligne`, mais au niveau d'une cellule
    précise plutôt que d'une ligne entière : aucune contrainte de nom de
    colonne ni de type de champ entre les deux cellules reliées.
    """
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    enregistrement = _ligne_accessible(request, table, ligne_id, ecriture=request.method != "GET")
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne
    champ = get_object_or_404(ChampDynamique, id=champ_id, table=table)

    if request.method == "GET":
        return JsonResponse(_serialiser_partage_cellule(entreprise, enregistrement, champ))

    if request.method == "DELETE":
        membre = (
            CellulePartagee.objects
            .filter(enregistrement=enregistrement, champ=champ)
            .select_related("groupe")
            .first()
        )
        if membre:
            groupe = membre.groupe
            membre.delete()
            if groupe.cellules.count() <= 1:
                groupe.cellules.all().delete()
                groupe.delete()
        return JsonResponse(_serialiser_partage_cellule(entreprise, enregistrement, champ))

    # POST : relier à UNE OU PLUSIEURS cellules cibles (partage en groupe, comme les colonnes) :
    # body {"cibles": [{"table_id", "ligne_id", "champ_id"}, ...]} ; ancien format à une cible accepté.
    corps = json.loads(request.body or "{}")
    cibles_brutes = list(corps.get("cibles") or [])
    if corps.get("table_id") and corps.get("ligne_id") and corps.get("champ_id"):
        cibles_brutes.append({k: corps[k] for k in ("table_id", "ligne_id", "champ_id")})
    if not cibles_brutes:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:58:25")}, status=400)
    cibles = {}
    for c in cibles_brutes:
        if not c.get("table_id") or not c.get("ligne_id") or not c.get("champ_id"):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:58:25")}, status=400)
        cible_ligne = get_object_or_404(
            Enregistrement, id=c["ligne_id"], table_id=c["table_id"], table__entreprise=entreprise,
        )
        cible_champ = get_object_or_404(ChampDynamique, id=c["champ_id"], table_id=c["table_id"])
        if {"constante", "horodatage", "id_client"} & {champ.type_champ, cible_champ.type_champ}:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:g:70:44")}, status=400)
        if (cible_ligne.id, cible_champ.id) != (enregistrement.id, champ.id):
            cibles[(cible_ligne.id, cible_champ.id)] = (cible_ligne, cible_champ)
    if not cibles:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:65:41")}, status=400)

    tous = [(enregistrement, champ), *cibles.values()]
    membres = {}
    for ligne_m, champ_m in tous:
        m = CellulePartagee.objects.filter(enregistrement=ligne_m, champ=champ_m).select_related("groupe").first()
        if m:
            membres[(ligne_m.id, champ_m.id)] = m
    existants = [membres[(l.id, c.id)].groupe for l, c in tous if (l.id, c.id) in membres]

    # Tables que ce lien réunirait (toutes les cellules + les groupes de cellules existants qu'il fusionne)
    liees = TableDynamique.objects.filter(
        Q(id__in=[c.table_id for _l, c in tous])
        | Q(champs__partages_cellule__groupe_id__in=[g.id for g in existants])
    ).distinct()
    erreur_groupe = _erreur_partage_meme_groupe(liees)
    if erreur_groupe:
        return JsonResponse({"erreur": erreur_groupe}, status=400)

    groupe = existants[0] if existants else GroupePartageCellule.objects.create(entreprise=entreprise)
    for autre in {g.id for g in existants if g.id != groupe.id}:
        CellulePartagee.objects.filter(groupe_id=autre).update(groupe_id=groupe.id)
        GroupePartageCellule.objects.filter(id=autre).delete()

    for ligne_m, champ_m in tous:
        CellulePartagee.objects.get_or_create(groupe=groupe, enregistrement=ligne_m, champ=champ_m)

    return JsonResponse(_serialiser_partage_cellule(entreprise, enregistrement, champ))


def _portees_rapport_general(request):
    """Portées que l'utilisateur peut demander pour le « Rapport général » : une table, toute son activité (toutes ses tables) et,
    pour le super-administrateur seulement, toute l'entreprise (y compris les tables confiées aux agents et les tables système)."""
    portees = [
        {"id": "table", "libelle": t("engine/views/_part_010.py:rg_portee_table_lib"), "aide": t("engine/views/_part_010.py:rg_portee_table_aide")},
        {"id": "mes_tables", "libelle": t("engine/views/_part_010.py:rg_portee_mes_lib"), "aide": t("engine/views/_part_010.py:rg_portee_mes_aide")},
    ]
    if request.user.is_superuser:
        portees.append({"id": "entreprise", "libelle": t("engine/views/_part_010.py:rg_portee_ent_lib"),
                        "aide": t("engine/views/_part_010.py:rg_portee_ent_aide")})
    return portees


def _tables_pour_portee(request, entreprise, membre, portee, table):
    """Tables couvertes par `portee`. « mes_tables » applique la même visibilité que la liste des tables (tables système exclues,
    tables confiées à d'autres agents fermées) ; « entreprise » (super-administrateur) prend tout. Une table fusionnée remplace ses parties."""
    if portee == "table":
        from ..vue_groupe import table_generale
        return [table_generale(table)]   # une partie d'une grande table : le rapport porte sur TOUTE la table (le groupe), pas sur la partie ouverte
    from .. import propriete_tables as _propriete_rg
    qs = (TableDynamique.objects.filter(entreprise=entreprise)
          .filter(Q(groupe__isnull=True) | Q(groupe__tables_de_fusion__isnull=True)).order_by("nom", "id"))
    tables = list(qs)
    if portee == "entreprise":
        return tables
    tables = [t_ for t_ in tables if not t_.source_systeme]
    if membre and membre.role == "agent":
        fermees = _propriete_rg.ids_fermees(request.user)
        tables = [t_ for t_ in tables if t_.id not in fermees]
    return tables


@login_required
@require_http_methods(["GET"])
def api_rapport_general_portees(request):
    """Portées proposées par la fenêtre « Rapport général » (la portée « toute l'entreprise » n'existe que pour le super-administrateur)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    from ..reports import sections_rapport_general
    return JsonResponse({"portees": _portees_rapport_general(request),
                         "sections": [{"id": i, "libelle": lib} for i, lib in sections_rapport_general()]})


@login_required
@require_http_methods(["POST"])
@_pdf_erreur_json
def api_exporter_table_rapport_general_pdf(request, table_id):
    """Bouton « Rapport général » : UN seul PDF qui réunit toutes les analyses (ou seulement les sections cochées, `?sections=`) (dashboards et graphiques, statistiques, ANOVA, analyses
    factorielles, séries temporelles, géospatial, tableaux de bord BI, KPI, skills). Portée au choix (`?portee=`) : « table » (défaut),
    « mes_tables » (toute son activité) ou « entreprise » (super-administrateur seulement)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    portee = (request.GET.get("portee") or request.POST.get("portee") or "table").strip()
    if portee not in {p["id"] for p in _portees_rapport_general(request)}:
        if portee in ("table", "mes_tables", "entreprise"):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:rg_err_reserve_admin")}, status=403)
        return JsonResponse({"erreur": t("engine/views/_part_010.py:rg_err_portee")}, status=400)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    tables = _tables_pour_portee(request, entreprise, membre, portee, table)
    if not tables:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:rg_err_aucune_table")}, status=400)
    from ..reports import generer_pdf_rapport_general_tables, sections_valides
    from django.http import HttpResponse
    # Sections cochées (?sections=a,b,c ou sections=a&sections=b) : rien de coché (ou rien de reconnu) = tout, comme avant.
    brutes = request.GET.getlist("sections") + request.POST.getlist("sections")
    sections = sections_valides([x for b in brutes for x in b.split(",") if x.strip()])
    if portee == "table":
        intitule, libelle, fichier = tf("engine/reports/_part_001.py:rg_titre", v1=tables[0].groupe.nom if tables[0].groupe_id else table.nom), t("engine/reports/_part_007.py:pdf:661:111"), f"{table.slug}_rapport_general.pdf"
    elif portee == "mes_tables":
        intitule, libelle, fichier = tf("engine/views/_part_010.py:rg_titre_mes", v1=entreprise.nom), t("engine/views/_part_010.py:rg_libelle_mes"), "rapport_general_mon_activite.pdf"
    else:
        intitule, libelle, fichier = tf("engine/views/_part_010.py:rg_titre_ent", v1=entreprise.nom), t("engine/views/_part_010.py:rg_libelle_ent"), "rapport_general_entreprise.pdf"
    pdf_bytes = generer_pdf_rapport_general_tables(entreprise, tables, intitule, libelle, sections=sections)
    reponse = HttpResponse(pdf_bytes, content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="{fichier}"'
    return reponse


@login_required
@require_http_methods(["POST"])
@_pdf_erreur_json
def api_exporter_table_resume_general_pdf(request, table_id):
    """Bouton "Résumé général" (une table, n'importe quel type) : PDF
    paysage combinant structure de la table, dashboards (analyses exécutées,
    un par page) et tableaux de bord BI (un par page), ontologie (graphe complet
    des tables de l'entreprise + densité) liée à la blockchain d'entreprise et
    aux tables de calcul actives, résumé statistique descriptif. Sans cartes.
    Réservé aux agents et admins : le résumé contient l'ontologie de TOUTES les tables de l'entreprise, la
    blockchain et les tables de calcul — jamais pour un client, même sur une table exposée."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)

    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from ..reports import generer_pdf_resume_general
    from django.http import HttpResponse

    if not (request.user.is_superuser or _a_pouvoir(membre, "resume_general")):
        # Agent : uniquement son environnement (les lignes qu'il a créées), un seul PDF.
        pdf_bytes = generer_pdf_resume_general(table, membre=membre, utilisateur=request.user)
        reponse = HttpResponse(pdf_bytes, content_type="application/pdf")
        reponse["Content-Disposition"] = f'attachment; filename="{table.slug}_resume_general.pdf"'
        return reponse

    # Administrateur : tout, y compris les lignes des agents. Un ZIP : le résumé général (toutes les lignes) + un PDF par agent
    # (agents ayant au moins une ligne dans cette table / ce groupe), chacun limité à l'environnement de l'agent.
    import io
    import re
    import zipfile
    createurs = set(table.enregistrements_groupe.exclude(cree_par=None).values_list("cree_par_id", flat=True))
    agents = [m for m in MembreEntreprise.objects.filter(entreprise=entreprise, role="agent", utilisateur_id__in=createurs)
              .select_related("utilisateur").order_by("id")]
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{table.slug}_resume_general.pdf", generer_pdf_resume_general(table, membre=membre))
        pris = set()
        for a in agents:
            u = a.utilisateur
            nom = re.sub(r"[^\w.-]+", "_", (u.get_full_name() or u.get_username() or f"agent_{u.id}")).strip("_") or f"agent_{u.id}"
            if nom in pris:
                nom = f"{nom}_{u.id}"
            pris.add(nom)
            zf.writestr(f"agents/{table.slug}_resume_{nom}.pdf", generer_pdf_resume_general(table, membre=a, utilisateur=u))
    reponse = HttpResponse(tampon.getvalue(), content_type="application/zip")
    reponse["Content-Disposition"] = f'attachment; filename="{table.slug}_resume_general.zip"'
    return reponse


# ─────────────────────────────────────────────────────────────
# TABLES DE CALCUL : formules façon Excel appliquées à une table source,
# indépendantes de celle-ci (voir models.TableCalcul / FormuleCalcul /
# LigneCalcul et calculs.py pour la résolution/exécution).
# ─────────────────────────────────────────────────────────────


def _serialiser_formule_champ(fc):
    return {
        "id": fc.id, "champ_cible_id": fc.champ_cible_id,
        "operateur": fc.operateur, "operandes_slugs": fc.operandes_slugs,
        "parametres": fc.parametres,
        "formule": _serialiser_formule(fc.formule) if fc.formule_id else None,
    }


def _serialiser_formule_ligne(fl):
    return {
        "id": fl.id, "enregistrement_id": fl.enregistrement_id,
        "operateur": fl.operateur, "operandes_slugs": fl.operandes_slugs,
        "parametres": fl.parametres,
        "formule": _serialiser_formule(fl.formule) if fl.formule_id else None,
        "champs_cibles_ids": [c.champ_cible_id for c in fl.cibles.all()],
    }


@login_required
@require_http_methods(["POST"])
def api_calculer_champ_ligne(request, table_id, ligne_id):
    """Calcul ponctuel : applique un opérateur (ou une formule personnalisée)
    à une seule ligne, à partir d'autres colonnes de cette même ligne, et
    écrit le résultat dans le champ cible de CETTE ligne uniquement — sans
    créer de FormuleChamp persistant (pas de recalcul automatique futur).
    Correspond au cas « on sélectionne la ligne pour le calcul »."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    if table.denaturee:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:173:41")}, status=400)
    enregistrement = _ligne_accessible(request, table, ligne_id, ecriture=True)
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne
    corps = json.loads(request.body or "{}")

    champ_cible_id = corps.get("champ_cible_id")
    champ_cible = get_object_or_404(ChampDynamique, id=champ_cible_id, table=table)
    operateur = corps.get("operateur") or "somme"
    operandes_slugs = corps.get("operandes_slugs") or []
    parametres = corps.get("parametres")

    formule = None
    if operateur == "personnalisee":
        formule_id = corps.get("formule_id")
        if not formule_id:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:187:45")}, status=400)
        formule = get_object_or_404(
            FormuleCalcul, id=formule_id, cible_type__in=["table", "colonne"],
        )
        if formule.entreprise_id not in (None, entreprise.id):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:192:45")}, status=404)
    if (_e := verifier_types_formule(table, operateur, operandes_slugs, formule, parametres, [champ_cible])): return JsonResponse({"erreur": _e}, status=400)
    from ..calc_champ import calculer_valeur_champ, _texte_valeur
    ligne_dict = enregistrement.to_dict()
    valeur, erreur = calculer_valeur_champ(
        ligne_dict, operateur, operandes_slugs, formule, parametres,
        table=table, enregistrement=enregistrement,
    )
    if erreur:
        return JsonResponse({"erreur": erreur}, status=400)

    ValeurChamp.objects.update_or_create(
        enregistrement=enregistrement, champ=champ_cible,
        defaults={"valeur": "" if valeur is None else _texte_valeur(valeur)},
    )
    ligne_dict = enregistrement.to_dict() | {"_id": enregistrement.id}
    ligne_dict = _resoudre_labels_relation(table, [ligne_dict])[0]
    return JsonResponse({"ligne": ligne_dict})


@login_required

def api_formule_champ_detail(request, table_id, champ_id):
    """Formule persistante d'une colonne (bouton "∑" sur l'en-tête, vue
    Données). POST crée/remplace la formule de `champ_id` et recalcule
    immédiatement toutes les lignes existantes. DELETE retire la formule
    (le champ redevient une colonne normale, éditable manuellement — les
    valeurs déjà calculées restent inchangées)."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _refus_client(request)
    if refus:
        return refus
    champ_cible = get_object_or_404(ChampDynamique, id=champ_id, table=table)

    if request.method == "DELETE":
        FormuleChamp.objects.filter(champ_cible=champ_cible).delete()
        return JsonResponse({"ok": True})

    if request.method == "POST":
        corps = json.loads(request.body or "{}")
        operateur = corps.get("operateur") or "somme"
        operandes_slugs = corps.get("operandes_slugs") or []
        parametres = corps.get("parametres")

        formule = None
        if operateur == "personnalisee":
            formule_id = corps.get("formule_id")
            if not formule_id:
                return JsonResponse({"erreur": t("engine/views/_part_010.py:241:49")}, status=400)
            formule = get_object_or_404(FormuleCalcul, id=formule_id, cible_type__in=["table", "colonne"])
            if formule.entreprise_id not in (None, entreprise.id):
                return JsonResponse({"erreur": t("engine/views/_part_010.py:244:49")}, status=404)
        elif operateur not in ("concatenation", "programme") and not operandes_slugs:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:246:45")}, status=400)

        if champ_cible.slug in operandes_slugs:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:249:45")}, status=400)
        if (_e := verifier_types_formule(table, operateur, operandes_slugs, formule, parametres, [champ_cible])): return JsonResponse({"erreur": _e}, status=400)
        formule_champ, _creee = FormuleChamp.objects.update_or_create(
            champ_cible=champ_cible,
            defaults={
                "table": table, "operateur": operateur,
                "operandes_slugs": operandes_slugs, "formule": formule,
                "parametres": parametres,
            },
        )
        from ..calc_champ import recalculer_colonne_entiere
        nb_ok, nb_erreurs = recalculer_colonne_entiere(formule_champ)
        return JsonResponse({
            "formule_champ": _serialiser_formule_champ(formule_champ),
            "nb_ok": nb_ok, "nb_erreurs": nb_erreurs,
        })

    return JsonResponse({"erreur": t("engine/views/_part_010.py:266:37")}, status=405)


@login_required
@require_http_methods(["GET", "POST"])

def api_formule_ligne_liste(request, table_id, ligne_id):
    """Formule(s) persistante(s) d'UNE ligne — mode "ligne" de la formule à
    la Excel, symétrique au mode "colonne" de FormuleChamp (bouton ∑) : un
    ou plusieurs champs de CETTE ligne partagent la même formule et sont
    recalculés automatiquement à chaque modification d'un autre champ de la
    ligne (voir calc_champ.appliquer_formules_lignes, branché dans
    api_modifier_ligne). Déclenchée côté client en tapant "=" dans une
    cellule, comme sur Excel. POST crée une nouvelle formule de ligne et
    recalcule immédiatement ses champs cibles."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    enregistrement = _ligne_accessible(request, table, ligne_id, ecriture=request.method != "GET")
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne

    if request.method == "GET":
        formules = table.formules_lignes.filter(enregistrement=enregistrement).select_related(
            "formule").prefetch_related("cibles__champ_cible")
        return JsonResponse({"formules": [_serialiser_formule_ligne(fl) for fl in formules]})

    corps = json.loads(request.body or "{}")
    champs_cibles_ids = corps.get("champs_cibles_ids") or []
    if not champs_cibles_ids:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:293:41")}, status=400)
    champs_cibles = list(ChampDynamique.objects.filter(id__in=champs_cibles_ids, table=table))
    if len(champs_cibles) != len(set(champs_cibles_ids)):
        return JsonResponse({"erreur": t("engine/views/_part_010.py:296:41")}, status=400)

    operateur = corps.get("operateur") or "somme"
    operandes_slugs = corps.get("operandes_slugs") or []
    parametres = corps.get("parametres")

    slugs_cibles = {c.slug for c in champs_cibles}
    if slugs_cibles & set(operandes_slugs):
        return JsonResponse({"erreur": t("engine/views/_part_010.py:304:41")}, status=400)

    formule = None
    if operateur == "personnalisee":
        formule_id = corps.get("formule_id")
        if not formule_id:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:310:45")}, status=400)
        formule = get_object_or_404(FormuleCalcul, id=formule_id, cible_type__in=["table", "colonne"])
        if formule.entreprise_id not in (None, entreprise.id):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:313:45")}, status=404)
    elif operateur not in ("concatenation", "programme") and not operandes_slugs:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:315:41")}, status=400)
    if (_e := verifier_types_formule(table, operateur, operandes_slugs, formule, parametres, champs_cibles)): return JsonResponse({"erreur": _e}, status=400)
    # Un champ d'une ligne donnée ne peut être ciblé que par une seule
    # formule de ligne active à la fois — on retire silencieusement l'ancien
    # ciblage pour permettre de "retransformer" un champ déjà calculé (ex:
    # changer l'opérateur en re-tapant "=" dessus).
    anciennes_cibles = FormuleLigneCible.objects.filter(enregistrement=enregistrement, champ_cible__in=champs_cibles)
    anciennes_formules_ids = set(anciennes_cibles.values_list("formule_ligne_id", flat=True))
    anciennes_cibles.delete()
    FormuleLigne.objects.filter(id__in=anciennes_formules_ids, cibles__isnull=True).delete()

    formule_ligne = FormuleLigne.objects.create(
        table=table, enregistrement=enregistrement, operateur=operateur,
        operandes_slugs=operandes_slugs, formule=formule, parametres=parametres,
    )
    FormuleLigneCible.objects.bulk_create([
        FormuleLigneCible(formule_ligne=formule_ligne, enregistrement=enregistrement, champ_cible=c)
        for c in champs_cibles
    ])
    from ..calc_champ import recalculer_formule_ligne
    ok, erreur = recalculer_formule_ligne(formule_ligne)
    return JsonResponse({"formule_ligne": _serialiser_formule_ligne(formule_ligne), "ok": ok, "erreur": erreur})


@login_required
@require_http_methods(["POST"])

def api_formule_ligne_copier(request, table_id, ligne_id):
    """Copie la formule de la ligne source vers plusieurs lignes destinataires.
    Corps JSON : même champs que le POST de api_formule_ligne_liste, plus
    `lignes_ids` (liste d'ids d'Enregistrement destinataires, sans la source).
    Crée une FormuleLigne identique pour chaque destinataire et recalcule."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    _src = _ligne_accessible(request, table, ligne_id, ecriture=True)  # source : isolation agent / refus client
    table = _src.table   # partie du groupe qui porte réellement la ligne source

    corps = json.loads(request.body or "{}")
    lignes_ids = corps.get("lignes_ids") or []
    if not lignes_ids:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:354:41")}, status=400)

    champs_cibles_ids = corps.get("champs_cibles_ids") or []
    if not champs_cibles_ids:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:358:41")}, status=400)
    champs_cibles = list(ChampDynamique.objects.filter(id__in=champs_cibles_ids, table=table))
    if len(champs_cibles) != len(set(champs_cibles_ids)):
        return JsonResponse({"erreur": t("engine/views/_part_010.py:361:41")}, status=400)

    operateur = corps.get("operateur") or "somme"
    operandes_slugs = corps.get("operandes_slugs") or []
    parametres = corps.get("parametres")

    formule = None
    if operateur == "personnalisee":
        formule_id = corps.get("formule_id")
        if not formule_id:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:371:45")}, status=400)
        formule = get_object_or_404(FormuleCalcul, id=formule_id, cible_type__in=["table", "colonne"])
        if formule.entreprise_id not in (None, entreprise.id):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:374:45")}, status=404)
    elif operateur not in ("concatenation", "programme") and not operandes_slugs:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:376:41")}, status=400)
    if (_e := verifier_types_formule(table, operateur, operandes_slugs, formule, parametres, champs_cibles)): return JsonResponse({"erreur": _e}, status=400)
    membre_copie = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    enregistrements = list(_isoler_lignes_agent(
        request, membre_copie, Enregistrement.objects.filter(id__in=lignes_ids, table=table)))
    if not enregistrements:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:382:41")}, status=400)

    from ..calc_champ import recalculer_formule_ligne
    resultats = []
    for enr in enregistrements:
        # Même logique de remplacement silencieux que le POST simple
        anciennes_cibles = FormuleLigneCible.objects.filter(enregistrement=enr, champ_cible__in=champs_cibles)
        anciennes_formules_ids = set(anciennes_cibles.values_list("formule_ligne_id", flat=True))
        anciennes_cibles.delete()
        FormuleLigne.objects.filter(id__in=anciennes_formules_ids, cibles__isnull=True).delete()

        fl = FormuleLigne.objects.create(
            table=table, enregistrement=enr, operateur=operateur,
            operandes_slugs=operandes_slugs, formule=formule, parametres=parametres,
        )
        FormuleLigneCible.objects.bulk_create([
            FormuleLigneCible(formule_ligne=fl, enregistrement=enr, champ_cible=c)
            for c in champs_cibles
        ])
        ok, erreur = recalculer_formule_ligne(fl)
        resultats.append({"ligne_id": enr.id, "ok": ok, "erreur": erreur})

    return JsonResponse({"resultats": resultats, "nb": len(resultats)})


@login_required
@require_http_methods(["DELETE"])

def api_formule_ligne_champ_detail(request, table_id, ligne_id, champ_id):
    """Retire UN champ d'une formule de ligne — ce champ redevient une
    cellule normale, éditable à la main (la valeur déjà calculée reste en
    place, même logique que la suppression d'une FormuleChamp). Si c'était
    le dernier champ ciblé par cette formule, la formule elle-même est
    supprimée."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    enregistrement = _ligne_accessible(request, table, ligne_id, ecriture=True)
    table = enregistrement.table   # partie du groupe qui porte réellement la ligne
    champ = get_object_or_404(ChampDynamique, id=champ_id, table=table)

    cible = FormuleLigneCible.objects.filter(
        enregistrement=enregistrement, champ_cible=champ).select_related("formule_ligne").first()
    if not cible:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:424:41")}, status=404)
    formule_ligne = cible.formule_ligne
    cible.delete()
    if not formule_ligne.cibles.exists():
        formule_ligne.delete()
    return JsonResponse({"ok": True})


def _serialiser_fusion(f, calculs=None):
    """`calculs` : sortie de fusion_cellule.calculer_fusions (résultat des fusions de type formule)."""
    r = (calculs or {}).get(f.id) or {}
    return {
        "id": f.id, "ligne_debut": f.ligne_debut, "ligne_fin": f.ligne_fin,
        "col_debut": f.col_debut, "col_fin": f.col_fin,
        "type_affiche": f.type_affiche, "valeur_affichee": f.valeur_affichee,
        "valeur_calculee": r.get("valeur_calculee", ""), "erreur_calcul": r.get("erreur_calcul", ""),
    }


def _calculs_fusions(table, fusions):
    """Évalue les fusions de type formule (rien de lu en base s'il n'y en a aucune)."""
    from django.core.cache import cache
    from ..fusion_cellule import calculer_fusions, fusions_hors_plage, resynchroniser
    if not fusions:
        return {}
    orphelines = resynchroniser(table, fusions)  # rangs recalés sur les lignes d'ancrage (en place)
    champs = list(table.champs.order_by("ordre"))
    stats = table.enregistrements.aggregate(n=Count("id"), m=Max("maj_le"))
    if not any(f.type_affiche == "formule" for f in fusions):
        return {**fusions_hors_plage(fusions, len(champs), stats["n"]), **orphelines}
    # Empreinte : lignes (nombre + dernière modif.), colonnes et fusions — le résultat n'est relu que si elle change.
    import hashlib  # hash() varie d'un processus à l'autre : clé stable pour un cache partagé
    cle = "fusioncalc_%s_%s" % (table.id, hashlib.sha1(repr((stats["n"], str(stats["m"]),
          tuple((c.id, c.slug, c.ordre) for c in champs),
          tuple((f.id, f.ligne_debut, f.ligne_fin, f.col_debut, f.col_fin, f.type_affiche, f.valeur_affichee, f.enreg_debut, f.enreg_fin)
                for f in fusions))).encode()).hexdigest())
    res = cache.get(cle)
    if res is None:
        from ..vue_groupe import enregistrements_vue
        from .. import parquet_store
        lignes = parquet_store.valeurs_lignes(table, list(enregistrements_vue(table).order_by("-cree_le", "-id")))   # la grille montre tout le groupe : les fusions aussi
        res = calculer_fusions(fusions, champs, lignes)
        cache.set(cle, res, 300)
    return {**res, **orphelines}


def _corps_fusion(request):
    """Corps JSON d'une requête de fusion : toujours un dict (JSON invalide ou autre forme -> None, jamais d'erreur 500)."""
    try:
        corps = json.loads(request.body or b"{}")
    except ValueError:
        return None
    return corps if isinstance(corps, dict) else None


@login_required
@require_http_methods(["GET", "POST"])
def api_fusions_table(request, table_id):
    """Fusion de cellules façon Excel dans la grille de données (voir
    FusionCelluleTable) : GET liste les fusions existantes, POST en crée
    une nouvelle à partir d'un rectangle de sélection."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    _bloque_lecteur = _interdit_table_non_exposee(request, table)
    if _bloque_lecteur:
        return _bloque_lecteur

    if request.method != "GET":
        refus = _refus_client(request)
        if refus:
            return refus
    if request.method == "GET":
        liste = list(table.fusions_cellules.all())
        calculs = _calculs_fusions(table, liste)
        return JsonResponse({"fusions": [_serialiser_fusion(f, calculs) for f in liste],
                             "denaturation": table.detail_denaturation()})

    if request.method == "POST":
        refus = _interdit_lecteur(request)
        if refus:
            return refus
        corps = _corps_fusion(request)
        if corps is None:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:a:json_invalide")}, status=400)
        try:
            ligne_debut = int(corps.get("ligne_debut"))
            ligne_fin = int(corps.get("ligne_fin"))
            col_debut = int(corps.get("col_debut"))
            col_fin = int(corps.get("col_fin"))
        except (TypeError, ValueError):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:471:45")}, status=400)

        if ligne_debut < 0 or ligne_fin < ligne_debut or col_debut < 0 or col_fin < col_debut:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:474:45")}, status=400)
        nb_champs = table.champs.count()
        if col_fin >= nb_champs:
            return JsonResponse({"erreur": t("engine/views/_part_010.py:477:45")}, status=400)
        # Une sélection d'une seule cellule est acceptée : c'est le même
        # mécanisme qui sert à changer la nature d'une cellule seule
        # (type_affiche), pas seulement à fusionner plusieurs cellules.

        from ..fusion_cellule import resynchroniser
        existantes = list(table.fusions_cellules.all())
        resynchroniser(table, existantes)
        # Comme Excel : un rectangle qui ENGLOBE entièrement des fusions existantes les remplace ; seul un chevauchement
        # partiel (une fusion coupée en deux) est refusé. L'interface étend déjà la sélection pour que ce cas n'arrive pas.
        englobees = []
        for existante in existantes:
            if existante.chevauche(ligne_debut, ligne_fin, col_debut, col_fin):
                if (ligne_debut <= existante.ligne_debut and existante.ligne_fin <= ligne_fin
                        and col_debut <= existante.col_debut and existante.col_fin <= col_fin):
                    englobees.append(existante)
                else:
                    return JsonResponse({"erreur": t("engine/views/_part_010.py:484:49")}, status=400)

        type_affiche = str(corps.get("type_affiche") or "texte")
        if type_affiche not in dict(ChampDynamique.TYPE_CHOICES):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:488:45")}, status=400)

        from ..fusion_cellule import valider_valeur
        valeur = str(corps.get("valeur_affichee") or "")
        erreur_valeur = valider_valeur(type_affiche, valeur)
        if erreur_valeur:
            return JsonResponse({"erreur": erreur_valeur}, status=400)

        # Dénaturation automatique (voir TableDynamique.recalculer_denaturee) : fusion réelle ou
        # cellule au type modifié => la table sert à composer un document, plus aux calculs.
        fusion = FusionCelluleTable.objects.create(
            table=table, ligne_debut=ligne_debut, ligne_fin=ligne_fin,
            col_debut=col_debut, col_fin=col_fin,
            type_affiche=type_affiche, valeur_affichee=valeur,
        )
        resynchroniser(table, [fusion])  # ancre la fusion sur ses enregistrements
        if fusion.enreg_debut is None:  # lignes inexistantes : impossible à ancrer, jamais créée sans id
            fusion.delete()
            return JsonResponse({"erreur": t("engine/views/_part_010.py:g:548:44")}, status=400)
        for ancienne in englobees:  # supprimées seulement une fois la nouvelle fusion bien créée
            ancienne.delete()
        table.recalculer_denaturee()
        return JsonResponse({"fusion": _serialiser_fusion(fusion, _calculs_fusions(table, list(table.fusions_cellules.all()))), "table_denaturee": table.denaturee}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_010.py:501:37")}, status=405)


@login_required
@require_http_methods(["POST", "DELETE"])
def api_fusion_detail(request, table_id, fusion_id):
    """Modifie (nature/contenu) ou supprime (défusionne) une fusion de
    cellules existante. DELETE défusionne — les cellules d'origine
    redeviennent individuelles, sans perte des valeurs sous-jacentes
    (jamais touchées par la fusion, seul l'affichage était remplacé)."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _refus_client(request)
    if refus:
        return refus
    fusion = get_object_or_404(FusionCelluleTable, id=fusion_id, table=table)
    _bloque_lecteur = _interdit_table_non_exposee(request, table)
    if _bloque_lecteur:
        return _bloque_lecteur
    refus = _interdit_lecteur(request)
    if refus:
        return refus

    if request.method == "DELETE":
        fusion.delete()
        # Si c'était la dernière fusion/surcharge, la table redevient normalement structurée.
        table.recalculer_denaturee()
        return JsonResponse({"ok": True, "table_denaturee": table.denaturee})

    corps = _corps_fusion(request)
    if corps is None:
        return JsonResponse({"erreur": t("engine/views/_part_010.py:a:json_invalide")}, status=400)
    if "type_affiche" in corps:
        type_affiche = str(corps["type_affiche"] or "texte")
        if type_affiche not in dict(ChampDynamique.TYPE_CHOICES):
            return JsonResponse({"erreur": t("engine/views/_part_010.py:536:45")}, status=400)
        fusion.type_affiche = type_affiche
    if "valeur_affichee" in corps:
        fusion.valeur_affichee = str(corps["valeur_affichee"] or "")
    from ..fusion_cellule import valider_valeur
    erreur_valeur = valider_valeur(fusion.type_affiche, fusion.valeur_affichee)
    if erreur_valeur:
        return JsonResponse({"erreur": erreur_valeur}, status=400)
    fusion.save(update_fields=["type_affiche", "valeur_affichee"])
    table.recalculer_denaturee()  # le type a pu changer (ex. cellule devenue formule)
    return JsonResponse({"fusion": _serialiser_fusion(fusion, _calculs_fusions(table, list(table.fusions_cellules.all()))),
                         "table_denaturee": table.denaturee})




















# ══════════════════════════════════════════════════════════════
#  CONFIGURATION DES DOCUMENTS — page dédiée, réglages par contexte
#  ('table', 'ia', 'systeme' — voir ConfigurationIA.configuration_pour
#  et ConfigurationDocumentContexte dans models.py). Remplace l'ancien
#  bloc "Mise en page & branding" de la modale Config IA : les réglages
#  obligatoires sur tout document (logo, couleurs, nom d'affichage)
#  restent sur ConfigurationIA, les réglages indépendants par contexte
#  (photo, sections, signatures, cachet) vivent sur
#  ConfigurationDocumentContexte, un par contexte et par entreprise.
# ══════════════════════════════════════════════════════════════
def api_ia_inference_statut(request, modele_id, inference_id):
    """Etat live d'une inference en cours, pour le polling depuis
    l'interface pendant que api_ia_inferer tourne en tache de fond — meme
    principe que api_ia_progression pour l'entrainement. Meme regle
    d'exposition qu'api_ia_inferer : un client (role Lecteur) ne peut
    suivre que l'inference d'un modele qu'il a le droit d'utiliser."""
    entreprise = _entreprise_courante(request)
    modele = get_object_or_404(ModeleIA, id=modele_id, entreprise=entreprise)
    bloque = _interdit_modele_non_expose(request, modele)
    if bloque:
        return bloque
    log = get_object_or_404(InferenceIA, id=inference_id, modele=modele)
    return JsonResponse({
        "inference": {
            "id": log.id,
            "entree": log.entree,
            "sortie": log.sortie,
            "statut": log.statut,
            "erreur": log.erreur,
            "cree_le": log.cree_le.isoformat(),
        },
    })


@login_required

def vue_taches(request):
    """Page dédiée à la gestion des tâches — planifiées (heure/intervalle) et
    conditionnelles (déclenchées par un événement de données, voir
    engine/declencheurs.py)."""
    entreprise = _entreprise_courante(request)
    from ..inference import EXEMPLE_TACHE
    cfg = ConfigurationIA.objects.filter(entreprise=entreprise).first()
    exemple = ((cfg.script_exemple_tache or "").strip() if cfg else "")
    return render(request, "engine/taches.html", {
        "entreprise": entreprise,
        "exemple_tache": exemple if "def executer(" in exemple else EXEMPLE_TACHE,
    })


@login_required
@require_http_methods(["GET"])
def api_taches_options_condition(request):
    """Options nécessaires au formulaire de tâche conditionnelle : tables de
    l'entreprise (avec repérage des tables clients), colonnes de type
    "relation" par table, et KPI définis. Chargé une fois à l'ouverture de la
    page /taches/."""
    from ..models import ChampDynamique, IndicateurKPI, TableDynamique
    entreprise = _entreprise_courante(request)
    tables = TableDynamique.objects.filter(entreprise=entreprise).order_by("nom")
    champs_relation = ChampDynamique.objects.filter(
        table__entreprise=entreprise, type_champ="relation"
    ).select_related("table", "relation_vers")
    kpis = IndicateurKPI.objects.filter(entreprise=entreprise).select_related("table_source")
    return JsonResponse({
        "tables": [{"id": t.id, "nom": t.nom, "est_table_membres": t.est_table_membres} for t in tables],
        "champs_relation": [
            {"id": c.id, "nom": c.nom, "table_id": c.table_id, "table_nom": c.table.nom,
             "vers_nom": c.relation_vers.nom if c.relation_vers_id else ""}
            for c in champs_relation
        ],
        "kpis": [{"id": k.id, "nom": k.nom, "table_id": k.table_source_id, "table_nom": k.table_source.nom} for k in kpis],
    })


@login_required
@require_http_methods(["GET"])

def api_taches_liste(request):
    """Liste toutes les tâches de l'entreprise."""
    from ..models import TachePlanifiee
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    taches = TachePlanifiee.objects.filter(entreprise=entreprise).order_by("-cree_le")
    return JsonResponse({"taches": [_serialiser_tache(t, request.user.is_superuser) for t in taches]})




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
