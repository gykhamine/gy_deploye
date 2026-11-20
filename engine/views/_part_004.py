from engine.i18n import tf
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402


@login_required
@require_http_methods(["POST"])
def api_limite_enregistrements_ontologie_sauvegarder(request):
    """Modifie le plafond d'enregistrements par table dans le graphe
    d'ontologie (0 = illimité — déconseillé sur une grosse table)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    corps = json.loads(request.body or "{}")
    brut = corps.get("limite_enregistrements_ontologie")
    try:
        limite = int(brut)
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_004.py:23:41")}, status=400)
    if limite < 0:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:25:41")}, status=400)
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    cfg.limite_enregistrements_ontologie = limite
    cfg.save(update_fields=["limite_enregistrements_ontologie"])
    return JsonResponse({"ok": True, "limite_enregistrements_ontologie": cfg.limite_enregistrements_ontologie})


def _serialiser_couche_ontologie(c):
    return {
        "id": c.id, "nom": c.nom, "slug": c.slug, "description": c.description,
        "code": c.code, "actif": c.actif, "predefinie": c.predefinie,
        "entreprise": c.entreprise_id is not None,
    }


@login_required
def api_couches_ontologie(request):
    """GET : liste les couches d'ontologie (prédéfinies + personnalisées à
    l'entreprise). POST : enregistre une nouvelle couche personnalisée —
    même principe qu'une formule : def etendre(graphe): ... ajoute des
    nœuds/arêtes au graphe (voir ontologie.construire_graphe) et est
    ensuite appliquée automatiquement à chaque affichage."""
    entreprise = _entreprise_courante(request)
    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        code = corps.get("code") or ""
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:56:45")}, status=400)
        if "def etendre(" not in code:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:58:45")}, status=400)

        from django.utils.text import slugify
        base_slug = slugify(nom) or "couche"
        slug = base_slug
        n = 2
        while CoucheOntologie.objects.filter(entreprise=entreprise, slug=slug).exists():
            slug = f"{base_slug}-{n}"
            n += 1

        couche = CoucheOntologie.objects.create(
            entreprise=entreprise, nom=nom, slug=slug,
            description=corps.get("description", ""), code=code,
        )
        return JsonResponse({"couche": _serialiser_couche_ontologie(couche)}, status=201)

    couches = CoucheOntologie.objects.filter(
        Q(entreprise__isnull=True) | Q(entreprise=entreprise)
    ).order_by("nom")
    return JsonResponse({"couches": [_serialiser_couche_ontologie(c) for c in couches]})


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_couche_ontologie_detail(request, couche_id):
    """PATCH : active/désactive une couche personnalisée (corps {"actif": bool})
    et/ou modifie son contenu (nom/description/code) — même principe d'édition
    que api_graphique_bi_detail : le code réenregistré doit toujours définir
    def etendre(graphe): ...
    DELETE : supprime une couche personnalisée (jamais une couche prédéfinie)."""
    entreprise = _entreprise_courante(request)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque
    couche = get_object_or_404(CoucheOntologie, id=couche_id, entreprise=entreprise, predefinie=False)
    if request.method == "DELETE":
        couche.delete()
        return JsonResponse({"ok": True})
    corps = json.loads(request.body or "{}")
    if "actif" in corps:
        couche.actif = bool(corps["actif"])
    if "code" in corps:
        if "def etendre(" not in (corps["code"] or ""):
            return JsonResponse({"erreur": t("engine/views/_part_004.py:101:45")}, status=400)
        couche.code = corps["code"]
    if "nom" in corps and (corps["nom"] or "").strip():
        couche.nom = corps["nom"].strip()
    if "description" in corps:
        couche.description = corps["description"]
    couche.save()
    return JsonResponse({"couche": _serialiser_couche_ontologie(couche)})


@login_required
def api_ontologie_types_evenements(request):
    """Catalogue complet (statique) des types de nœuds et de relations déjà
    connus de la plateforme — "tout ce qui est possible comme événement dans
    la plateforme" — utilisé par le sélecteur côté interface pour composer
    une nouvelle relation (entité source, entité cible, nom de relation)
    sans écrire de code, avant de passer cette description à la génération
    via IA. Voir ontologie.lister_types_evenements."""
    entreprise = _entreprise_courante(request)
    from .. import ontologie as _onto
    return JsonResponse(_onto.lister_types_evenements(entreprise))


@login_required
@require_http_methods(["POST"])
def api_generer_couche_ontologie(request):
    """Lance en tâche de fond la génération d'une couche d'ontologie
    personnalisée via le moteur d'IA déjà utilisé pour les formules/graphiques
    BI (voir inference.MoteurInference.generer_couche_ontologie, appelée par
    la tâche Celery engine.generer_couche_ontologie). Corps attendu :
    {description}, où la description peut être écrite librement ou composée
    depuis le sélecteur d'événements (api_ontologie_types_evenements) — ex.
    "quand un document est produit depuis une table dont le nom contient
    'facture', relie-le à un nœud 'à archiver'". Ne sauvegarde aucune
    CoucheOntologie : crée juste un brouillon (GenerationCoucheOntologie) à
    suivre via api_statut_generation_couche_ontologie, dont le code une fois
    prêt est à valider/modifier avant "Enregistrer et appliquer" côté client."""
    entreprise = _entreprise_courante(request)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    corps = json.loads(request.body or "{}")
    description = (corps.get("description") or "").strip()
    if not description:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:146:41")}, status=400)

    generation = GenerationCoucheOntologie.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        description=description,
        statut="en_cours",
    )
    _taches.generer_couche_ontologie_tache.delay(generation.id)
    return JsonResponse({"generation_id": generation.id}, status=202)


@login_required
@require_http_methods(["GET"])
def api_statut_generation_couche_ontologie(request, generation_id):
    """Polling léger : retourne uniquement le statut tant que ce n'est pas
    terminé, puis l'objet complet (mêmes conventions que
    api_statut_generation_graphique_bi / _attendreFin côté JS : {fini,
    generation:{statut, code, erreur}})."""
    entreprise = _entreprise_courante(request)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    generation = get_object_or_404(GenerationCoucheOntologie, id=generation_id, entreprise=entreprise)
    if generation.statut in ("pret", "echec"):
        return JsonResponse({"fini": True, "generation": {
            "statut": generation.statut,
            "code": generation.code,
            "erreur": generation.erreur,
        }})
    return JsonResponse({"fini": False, "statut": generation.statut})


# ── À insérer dans views.py juste après api_ontologie_types_evenements ──

@login_required
@require_http_methods(["POST"])
def api_ontologie_recherche(request):
    """Recherche multi-critères dans le graphe d'ontologie.

    Corps JSON (tous les champs sont optionnels, combinés en ET) :
      texte       str   — recherche libre dans label/slug/attributs
      types       list  — types de nœuds à inclure (ex. ["table","champ"])
      relations   list  — types de relations à inclure
      attribut    str   — nom d'un attribut à tester
      valeur      any   — valeur recherchée dans cet attribut
      valeur_min  num   — borne basse (attribut numérique)
      valeur_max  num   — borne haute (attribut numérique)
      cible       str   — "noeuds" | "aretes" | "tout" (défaut "tout")
      limit       int   — max résultats (défaut 200, max 1000)
      page_id     int   — restreindre à une PageOntologie précise

    Réponse : {noeuds, aretes, total_noeuds, total_aretes}
    """
    entreprise = _entreprise_courante(request)
    params = json.loads(request.body or "{}")
    page_id = params.pop("page_id", None)
    page = None
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()

    from .. import ontologie as _onto
    from ..ontologie_requetes import rechercher
    if page is not None and page.mode == "manuel":
        graphe = _onto.construire_graphe_manuel(page, entreprise)
    else:
        graphe = _onto.construire_graphe(entreprise, page=page)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    graphe = _onto.restreindre_pour_client(graphe, entreprise, membre)

    return JsonResponse(rechercher(graphe, params))


@login_required
@require_http_methods(["POST"])
def api_ontologie_explorer(request):
    """Exploration du graphe depuis un nœud central.

    Corps JSON :
      noeud_id    str   — identifiant du nœud central (ex. "table:5") REQUIS
      mode        str   — "voisins" | "chemins" | "recursion" | "stats"
      profondeur  int   — nombre de sauts (défaut 1, max 10)
      direction   str   — "sortant" | "entrant" | "tout"
      relations   list  — filtrer sur ces types de relations
      types       list  — filtrer les nœuds résultants par type
      cible_id    str   — nœud destination (mode "chemins" seulement)
      max_chemins int   — max chemins simples retournés (défaut 5)
      tri         str   — "label" | "type" | "degre"
      min_degre   int   — degré minimum des voisins
      max_degre   int   — degré maximum des voisins
      limit       int   — max nœuds retournés (défaut 200)
      stats_globales bool — si true, retourne aussi les stats globales du graphe
      page_id     int   — restreindre à une PageOntologie précise

    Réponse selon le mode :
      voisins   → {noeud_central, noeuds, aretes, profondeur_reelle, total}
      chemins   → {source, cible, chemins, total, plus_court}
      recursion → {noeud_racine, noeuds, aretes, total, tronque}
      stats     → {noeud, degre_*, types_voisins, relations_*, centralite, ...}
    """
    entreprise = _entreprise_courante(request)
    params = json.loads(request.body or "{}")
    noeud_id = params.pop("noeud_id", None)
    if not noeud_id:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:248:41")}, status=400)
    page_id = params.pop("page_id", None)
    avec_stats = bool(params.pop("stats_globales", False))
    page = None
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()

    from .. import ontologie as _onto
    from ..ontologie_requetes import explorer, stats_globales
    if page is not None and page.mode == "manuel":
        graphe = _onto.construire_graphe_manuel(page, entreprise)
    else:
        graphe = _onto.construire_graphe(entreprise, page=page)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    graphe = _onto.restreindre_pour_client(graphe, entreprise, membre)

    resultat = explorer(graphe, noeud_id, params)
    if avec_stats:
        resultat["stats_globales"] = stats_globales(graphe)
    return JsonResponse(resultat)


@login_required
@require_http_methods(["POST"])
def api_ontologie_fiche(request):
    """Fiche objet consolidée (Phase 6 du cahier des charges Ontology —
    « Applications object-centric ») : une application ne devrait pas être
    obligée d'assembler elle-même propriétés/relations/actions/documents
    pour un objet quand l'Ontology peut les fournir en un seul appel.

    N'introduit aucun nouveau moteur : assemble des briques déjà en place
    (aucune n'est modifiée) —
      - propriétés + relations directes : ontologie.construire_graphe +
        ontologie_requetes.explorer (mode "voisins", inchangés) ;
      - actions disponibles : actions._REGISTRE + catalogue_pour_agent
        (Phases 3+5, inchangés) ;
      - documents liés : DocumentGenere.table_cible / enregistrement_cible
        (champs déjà existants sur le modèle) ;
      - activité récente : JournalAudit (inchangé).

    Corps JSON :
      noeud_id  str  — identifiant du nœud (ex. "table:5", "enregistrement:12") REQUIS
      page_id   int  — restreindre le graphe à une PageOntologie précise

    Réponse : {objet, relations, actions_disponibles, documents, activite_recente}

    actions_disponibles est filtré à la fois par rôle (comme avant) et par
    type d'objet (actions.declarer_action(types_objet=...)) : une Action
    non catégorisée (types_objet=None, cas d'une Action sans nœud Ontology
    correspondant aujourd'hui, ex. DemandeAgent) reste incluse pour tout
    type de nœud, faute de mieux.

    Limite restante : documents/activite_recente sont résolus pour les
    nœuds "table:", "enregistrement:", "entreprise:" et "agent:"/"admin:"/
    "client:" (via le membre → son utilisateur) ; les autres types de
    nœud (config_ia, calcul, skill, modele_ia, dashboard, kpi, tokens...)
    renvoient encore des listes vides, faute de lien direct vers
    DocumentGenere/JournalAudit sur ces modèles.
    """
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    params = json.loads(request.body or "{}")
    noeud_id = params.get("noeud_id")
    if not noeud_id:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:310:41")}, status=400)

    from ..ontologie_fiche import assembler_fiche_objet, assainir_json
    resultat = assainir_json(assembler_fiche_objet(entreprise, noeud_id, membre=membre, page_id=params.get("page_id")))
    if "erreur" in resultat:
        return JsonResponse(resultat, status=404)
    return JsonResponse(resultat)


@login_required
@require_http_methods(["POST"])
def api_ontologie_portail(request):
    """Portail de navigation par données liées (voir engine/portail.py).

    Corps JSON :
      noeud_id   str  — objet de départ (ex. "table:5", "champ:12", "dashboard:3") REQUIS
      mode       str  — "destinations" (défaut) : où aller depuis cet objet ;
                        "toile" : parcours en largeur de tout ce qui est relié
      profondeur int  — mode "toile" uniquement (1 à 3, défaut 2)
      limite     int  — mode "toile" uniquement, nombre max de nœuds (défaut 150)

    Réponse : voir portail.resoudre_destinations / portail.explorer_toile. Chaque destination
    est vérifiée côté serveur (existence, entreprise, vues et tables autorisées du membre)."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    try:
        params = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:g:343:40")}, status=400)
    noeud_id = params.get("noeud_id")
    if not noeud_id:
        return JsonResponse({"erreur": _gt("rg8.msg.013")}, status=400)

    from .. import portail
    if params.get("mode") == "toile":
        try:
            profondeur, limite = int(params.get("profondeur", 2)), int(params.get("limite", 150))
        except (TypeError, ValueError):
            return JsonResponse({"erreur": _gt("rg8.msg.014")}, status=400)
        return JsonResponse(portail.explorer_toile(entreprise, noeud_id, membre=membre,
                                                   profondeur=profondeur, limite=limite))
    return JsonResponse(portail.resoudre_destinations(entreprise, noeud_id, membre=membre))


@login_required
@require_http_methods(["POST"])
def api_ontologie_liste(request):
    """Liste générique d'objets par TYPE (Phase 10 du cahier des charges
    Ontology — « Applications object-centric »), pendant de
    api_ontologie_fiche : une seule route pour lister n'importe quel type
    d'objet déjà décrit par l'Ontology (tables, calculs, KPI, dashboards,
    skills, modèles IA, agents/admins/clients...) au lieu d'une vue dédiée
    par type. Voir ontologie_fiche.assembler_liste_objets pour la limite
    documentée sur type="enregistrement" (grille dédiée conservée).

    Corps JSON :
      type       str  — type de nœud à lister (ex. "table", "kpi") REQUIS
      parent_id  str  — restreindre aux voisins directs de ce nœud (ex. "table:5")
      q          str  — filtre texte sur le label
      page_id    int  — restreindre à une PageOntologie précise
      limit      int  — max résultats (défaut 200, max 1000)

    Réponse : {type, total, objets}
    """
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    params = json.loads(request.body or "{}")
    type_noeud = params.get("type")
    if not type_noeud:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:344:41")}, status=400)
    limit = min(int(params.get("limit") or 200), 1000)

    from ..ontologie_fiche import assembler_liste_objets
    resultat = assembler_liste_objets(
        entreprise, type_noeud, membre=membre, page_id=params.get("page_id"),
        parent_id=params.get("parent_id"), q=params.get("q", ""), limit=limit,
    )
    return JsonResponse(resultat)


@login_required
@require_http_methods(["POST"])
def api_ontologie_lineage(request):
    """Lignée bout-en-bout (Phase 7 du cahier des charges Ontology —
    « d'où vient cette valeur ? »). Une seule requête qui remonte
    (import → table → calcul...) et descend (... → KPI → dashboard →
    document) toute la chaîne depuis un nœud, au lieu d'obliger l'appelant
    à enchaîner lui-même plusieurs appels à `explorer`.

    N'introduit aucun nouveau moteur : parcourt le graphe déjà construit
    (ontologie.construire_graphe, avec la même isolation par agent que le
    reste de l'Ontology) via ontologie_requetes.tracer_lineage (nouveau,
    simple BFS amont/aval).

    Corps JSON :
      noeud_id  str  — identifiant du nœud (ex. "kpi:3", "table:5") REQUIS
      page_id   int  — restreindre le graphe à une PageOntologie précise
      limite    int  — max nœuds par sens (défaut 100)

    Réponse : {noeud, amont, aval, chaine}
    """
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    params = json.loads(request.body or "{}")
    noeud_id = params.get("noeud_id")
    if not noeud_id:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:381:41")}, status=400)
    page_id = params.get("page_id")
    limite = min(int(params.get("limite") or 100), 500)
    page = None
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()

    from .. import ontologie as _onto
    from ..ontologie_requetes import tracer_lineage
    if page is not None and page.mode == "manuel":
        graphe = _onto.construire_graphe_manuel(page, entreprise)
    else:
        graphe = _onto.construire_graphe(entreprise, page=page, membre=membre)
    graphe = _onto.restreindre_pour_client(graphe, entreprise, membre)

    resultat = tracer_lineage(graphe, noeud_id, limite=limite)
    if "erreur" in resultat:
        return JsonResponse(resultat, status=404)
    return JsonResponse(resultat)


@login_required
@require_http_methods(["POST"])
def api_ontologie_recherche_nosql(request):
    """Recherche dans les valeurs JSON des tables NoSQL (Enregistrement.valeurs_json).

    Complément de api_ontologie_recherche : celle-ci ne porte que sur le
    graphe déjà construit en mémoire, qui ne contient qu'un échantillon de
    lignes récentes par table (voir ontologie.construire_graphe /
    LIMITE_ENREGISTREMENTS_GRAPHE) et, pour ces lignes, seulement un label
    (matricule ou première valeur non vide) — jamais le JSON complet. Pour
    trouver une ligne par son contenu réel, il faut donc interroger la base
    directement plutôt que le graphe.

    Corps JSON :
      texte       str  — recherche libre sur tout le bloc JSON (icontains).
                          Requis, sauf si champ_slug + valeur sont fournis.
      champ_slug  str  — restreint la recherche à UNE clé précise du JSON
                          (lookup exact sur cette clé plutôt que sur tout
                          le bloc) — ex. "nom", "entreprise_cliente".
      valeur      str  — terme cherché dans cette clé, utilisé seulement
                          si champ_slug est fourni.
      table_id    int  — restreindre à une table NoSQL précise.
      page_id     int  — page d'ontologie dont dépend le graphe utilisé
                          pour savoir si un résultat y figure déjà comme
                          nœud (mêmes règles que api_ontologie_recherche).
      limit       int  — max résultats (défaut 50, max 200).

    Réponse : {resultats, total, tronque}
      resultats[] : {id, table_id, table_nom, table_slug, matricule,
                     extrait, noeud_id (ou null si absent du graphe),
                     dans_graphe, lien_table}

    Chaque résultat, qu'il soit ou non déjà un nœud du graphe échantillonné,
    porte un lien direct vers sa table (lien_table) : le panneau Explorer
    peut ainsi faire un focus sur le nœud "enregistrement:X" quand il
    existe, ou proposer d'ouvrir la table sinon.
    """
    entreprise = _entreprise_courante(request)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()

    params = json.loads(request.body or "{}")
    terme = (params.get("texte") or "").strip()
    champ_slug = (params.get("champ_slug") or "").strip()
    valeur = params.get("valeur")
    table_id = params.get("table_id")
    page_id = params.get("page_id")
    limit = min(int(params.get("limit") or 50), 200)

    recherche_precise = bool(champ_slug) and valeur not in (None, "")
    if not terme and not recherche_precise:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:454:41")}, status=400)

    qs = Enregistrement.objects.filter(
        table__entreprise=entreprise, table__type_stockage=TableDynamique.STOCKAGE_NOSQL,
    ).select_related("table")
    if table_id:
        qs = qs.filter(table_id=table_id)
    if recherche_precise:
        qs = qs.filter(**{f"valeurs_json__{champ_slug}__icontains": str(valeur)})
    elif terme:
        qs = qs.filter(valeurs_json__icontains=terme)
    qs = _isoler_lignes_agent(request, membre, qs).order_by("-cree_le")

    total = qs.count()
    lignes = list(qs[:limit])

    # Le graphe (échantillon récent, voir docstring) sert uniquement à
    # savoir si CE résultat y figure déjà comme nœud — jamais reconstruit
    # à partir des résultats de cette recherche, qui restent hors-graphe
    # par construction dès qu'ils dépassent l'échantillon.
    from .. import ontologie as _onto
    page = None
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()
    if page is not None and page.mode == "manuel":
        graphe = _onto.construire_graphe_manuel(page, entreprise)
    else:
        graphe = _onto.construire_graphe(entreprise, page=page, membre=membre)

    def _extrait(e):
        valeurs = e.valeurs_json or {}
        if champ_slug and champ_slug in valeurs:
            return f"{champ_slug}: {valeurs[champ_slug]}"
        if terme:
            correspondance = next(
                ((k, v) for k, v in valeurs.items() if v not in (None, "") and terme.lower() in str(v).lower()),
                None,
            )
            if correspondance:
                return f"{correspondance[0]}: {correspondance[1]}"
        return e.matricule or f"#{e.id}"

    resultats = []
    for e in lignes:
        nid = f"enregistrement:{e.id}"
        dans_graphe = graphe.has_node(nid)
        resultats.append({
            "id": e.id,
            "table_id": e.table_id,
            "table_nom": e.table.nom,
            "table_slug": e.table.slug,
            "matricule": e.matricule,
            "extrait": _extrait(e)[:200],
            "noeud_id": nid if dans_graphe else None,
            "dans_graphe": dans_graphe,
            "lien_table": f"/tables/{e.table.slug}/",
        })

    return JsonResponse({"resultats": resultats, "total": total, "tronque": total > len(resultats)})


def _serialiser_graphique_bi(g):
    return {
        "id": g.id, "nom": g.nom, "slug": g.slug, "categorie": g.categorie,
        "cible_type": g.cible_type, "description": g.description,
        "nombre_colonnes_min": g.nombre_colonnes_min,
        "nombre_colonnes_max": g.nombre_colonnes_max,
        "types_cibles_compatibles": g.liste_types_compatibles,
        "predefini": g.predefini, "code": g.code,
        "entreprise": g.entreprise_id is not None,
    }


from .. import identifiants_analyse as _ident_mod
_IDENT_SLUG_PAR_ID = _ident_mod.SLUG_PAR_ID


def _ident_libelle(identifiant):
    return _ident_mod.libelle(_IDENT_SLUG_PAR_ID[identifiant])


def _serialiser_widget_bi(w):
    return {
        "id": w.id, "titre": w.titre, "description": w.description, "couleur": w.couleur or "",
        "type_widget": w.type_widget,
        "graphique": _serialiser_graphique_bi(w.graphique) if w.graphique_id else None,
        "kpi_id": w.kpi_id,
        "contenu_texte": w.contenu_texte,
        "style_texte": w.style_texte,
        "champs_cibles_ids": w.ordre_champs or [c.id for c in w.champs_cibles.all()],
        "champs_cibles_noms": [c.nom for c in w.champs_cibles.all()] + [_ident_libelle(i) for i in (w.ordre_champs or []) if i in _IDENT_SLUG_PAR_ID],
        "position_x": w.position_x, "position_y": w.position_y,
        "largeur": w.largeur, "hauteur": w.hauteur, "ordre_z": w.ordre_z,
        "options": w.options_json,
        "statut": w.statut,
        "image_base64": w.resultat_image_base64 if (w.statut == "pret" and w.type_widget == "graphique") else "",
        "html_carte": w.resultat_html if (w.statut == "pret" and w.type_widget == "carte") else "",
        "erreur": w.erreur,
        "rendu_le": w.rendu_le.isoformat() if w.rendu_le else None,
    }


def _serialiser_tableau_bord_bi(t, avec_widgets=True):
    data = {
        "id": t.id, "nom": t.nom, "description": t.description,
        "colonnes_grille": t.colonnes_grille, "ordre": t.ordre,
        "table_source_id": t.table_source_id,
        "visible_client": t.visible_client,
    }
    if avec_widgets:
        widgets = t.widgets.select_related("graphique").prefetch_related("champs_cibles").order_by("ordre_z", "id")
        data["widgets"] = [_serialiser_widget_bi(w) for w in widgets]
    return data


@login_required
@require_http_methods(["POST"])
def api_generer_graphique_bi(request):
    """Lance en tache de fond la generation d'un graphique BI personnalise via
    le moteur d'IA deja utilise pour les scripts d'analyse/formules (voir
    inference.MoteurInference.generer_graphique_bi, appele par la tache
    Celery engine.generer_graphique_bi). Corps attendu : {description,
    cible_type (colonne_unique|colonnes_multiples), table_id (optionnel, pour
    le contexte de schema/donnees)}. Ne sauvegarde aucun GraphiqueBI : cree
    juste un brouillon (GenerationGraphiqueBI) a suivre via
    api_statut_generation_graphique_bi, dont le code une fois pret est a
    valider/modifier avant "Enregistrer le graphique" cote client."""
    entreprise = _entreprise_courante(request)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    corps = json.loads(request.body or "{}")
    description = (corps.get("description") or "").strip()
    cible_type = corps.get("cible_type") or "colonne_unique"
    if not description:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:581:41")}, status=400)
    if cible_type not in dict(GraphiqueBI.CIBLE_TYPE_CHOICES):
        return JsonResponse({"erreur": t("engine/views/_part_004.py:583:41")}, status=400)

    table_id = corps.get("table_id")
    table = None
    if table_id:
        table = TableDynamique.objects.filter(id=table_id, entreprise=entreprise).first()

    generation = GenerationGraphiqueBI.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        table_reference=table,
        description=description,
        cible_type=cible_type,
        statut="en_cours",
    )
    _taches.generer_graphique_bi_tache.delay(generation.id)
    return JsonResponse({"generation_id": generation.id}, status=202)


@login_required
@require_http_methods(["GET"])
def api_statut_generation_graphique_bi(request, generation_id):
    """Polling leger : retourne uniquement le statut tant que ce n'est pas
    termine, puis l'objet complet (memes conventions que
    api_statut_generation_formule / _attendreFin cote JS : {fini,
    generation:{statut, code, erreur}})."""
    entreprise = _entreprise_courante(request)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    generation = get_object_or_404(GenerationGraphiqueBI, id=generation_id, entreprise=entreprise)
    if generation.statut in ("pret", "echec"):
        return JsonResponse({"fini": True, "generation": {
            "statut": generation.statut,
            "code": generation.code,
            "erreur": generation.erreur,
        }})
    return JsonResponse({"fini": False, "statut": generation.statut})


@login_required
def api_graphiques_bi(request):
    """GET : liste les graphiques BI disponibles (prédéfinis + personnalisés
    à l'entreprise), optionnellement filtrés par type de champ compatible
    (?type_champ=nombre). POST : crée un graphique personnalisé (code fourni
    par l'admin) pour l'entreprise courante."""
    from ..graphiques_bi import synchroniser_graphiques_bi_predefinis
    synchroniser_graphiques_bi_predefinis()

    entreprise = _entreprise_courante(request)

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        code = corps.get("code") or ""
        cible_type = corps.get("cible_type") or "colonne_unique"
        if not nom or not code:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:643:45")}, status=400)
        if "def dessiner(" not in code:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:645:45")}, status=400)
        if cible_type not in dict(GraphiqueBI.CIBLE_TYPE_CHOICES):
            return JsonResponse({"erreur": t("engine/views/_part_004.py:647:45")}, status=400)

        from django.utils.text import slugify
        base_slug = slugify(nom) or "graphique"
        slug = base_slug
        n = 2
        while GraphiqueBI.objects.filter(entreprise=entreprise, slug=slug).exists():
            slug = f"{base_slug}-{n}"
            n += 1

        graphique = GraphiqueBI.objects.create(
            entreprise=entreprise, nom=nom, slug=slug,
            # Jamais sans description : à défaut, on reprend ce qui a été demandé à l'IA, sinon le nom.
            description=(corps.get("description") or "").strip() or nom,
            categorie=corps.get("categorie", "personnalisee"),
            cible_type=cible_type,
            nombre_colonnes_min=corps.get("nombre_colonnes_min", 1),
            nombre_colonnes_max=corps.get("nombre_colonnes_max") or None,
            types_cibles_compatibles=corps.get("types_cibles_compatibles", ""),
            code=code, predefini=False,
        )
        return JsonResponse({"graphique": _serialiser_graphique_bi(graphique)}, status=201)

    graphiques = GraphiqueBI.objects.filter(
        Q(entreprise__isnull=True) | Q(entreprise=entreprise)
    )
    type_champ = request.GET.get("type_champ")
    if type_champ:
        graphiques = [g for g in graphiques if not g.liste_types_compatibles or type_champ in g.liste_types_compatibles]
    liste = [_serialiser_graphique_bi(g) for g in graphiques]
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if membre and membre.role == "lecteur":
        for g in liste:   # un client n'a pas à lire le code des graphiques de l'entreprise
            g["code"] = ""
    return JsonResponse({"graphiques": liste})


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_graphique_bi_detail(request, graphique_id):
    entreprise = _entreprise_courante(request)
    graphique = get_object_or_404(GraphiqueBI, id=graphique_id, entreprise=entreprise)
    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        if WidgetGraphiqueBI.objects.filter(graphique=graphique).exists():
            return JsonResponse(
                {"erreur": t("engine/views/_part_004.py:690:29")},
                status=409,
            )
        graphique.delete()
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")
    for champ in ("nom", "description", "categorie", "code", "types_cibles_compatibles",
                  "nombre_colonnes_min", "nombre_colonnes_max"):
        if champ in corps:
            setattr(graphique, champ, corps[champ])
    graphique.save()
    return JsonResponse({"graphique": _serialiser_graphique_bi(graphique)})


@login_required
def api_tableaux_bord_bi(request, table_id):
    """Liste / crée les tableaux de bord BI liés à une table source."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    _bloque_lecteur = _interdit_table_bi(request, table)
    if _bloque_lecteur:
        return _bloque_lecteur

    if request.method == "GET":
        from ..vue_groupe import ids_groupe
        tableaux = TableauBordBI.objects.filter(entreprise=entreprise, table_source_id__in=ids_groupe(table)).order_by("ordre", "id")   # un groupe = UNE table : mêmes dashboards depuis chaque partie
        membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
        if membre and membre.role == "lecteur":
            # Un client ne voit que les tableaux de bord explicitement mis à
            # disposition (visible_client) — jamais d'édition, voir POST plus
            # bas (bloqué par _interdit_lecteur) et le template en lecture seule.
            tableaux = tableaux.filter(visible_client=True)
        return JsonResponse({"tableaux_bord_bi": [_serialiser_tableau_bord_bi(t) for t in tableaux]})

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        from ..vue_groupe import ancre_groupe
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:731:45")}, status=400)
        tableau_bord = TableauBordBI.objects.create(
            entreprise=entreprise, table_source=ancre_groupe(table), nom=nom,
            description=corps.get("description", ""),
            colonnes_grille=corps.get("colonnes_grille", 12),
        )
        return JsonResponse({"tableau_bord_bi": _serialiser_tableau_bord_bi(tableau_bord)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_004.py:739:37")}, status=405)


@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def api_tableau_bord_bi_detail(request, tableau_id):
    entreprise = _entreprise_courante(request)
    tableau_bord = TableauBordBI.objects.filter(id=tableau_id, entreprise=entreprise).first()
    partage = False
    if tableau_bord is None:
        tableau_bord = get_object_or_404(TableauBordBI, id=tableau_id)
        if request.method != "GET" or not _partage_actif(entreprise, "dashboard", tableau_bord.id):
            raise Http404
        partage = True  # dashboard d'une autre entreprise, mis à disposition en lecture seule

    if request.method == "GET":
        membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
        if not partage and membre and membre.role == "lecteur" and not tableau_bord.visible_client:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:757:45")}, status=403)
        return JsonResponse({"tableau_bord_bi": _serialiser_tableau_bord_bi(tableau_bord)})

    _bloque = _interdit_lecteur(request)
    if _bloque:
        return _bloque

    if request.method == "DELETE":
        tableau_bord.delete()
        return JsonResponse({"ok": True})

    corps = json.loads(request.body or "{}")
    for champ in ("nom", "description", "colonnes_grille", "ordre", "visible_client"):
        if champ in corps:
            setattr(tableau_bord, champ, bool(corps[champ]) if champ == "visible_client" else corps[champ])
    tableau_bord.save()
    return JsonResponse({"tableau_bord_bi": _serialiser_tableau_bord_bi(tableau_bord)})


@login_required
def api_widgets_bi(request, tableau_id):
    """Liste / crée les widgets d'un tableau de bord BI. La création pose le
    widget en base (statut='en_attente'), délègue immédiatement le rendu à
    Celery (statut='en_cours') et répond sans attendre : le front suit
    l'avancement via api_statut_widget_bi (polling)."""
    entreprise = _entreprise_courante(request)
    tableau_bord = get_object_or_404(TableauBordBI, id=tableau_id, entreprise=entreprise)

    if request.method == "GET":
        _refus = _refus_tableau_bi_client(request, tableau_bord)   # un client ne lit que les tableaux mis à sa disposition
        if _refus:
            return _refus
        widgets = tableau_bord.widgets.select_related("graphique").prefetch_related("champs_cibles").order_by("ordre_z", "id")
        return JsonResponse({"widgets": [_serialiser_widget_bi(w) for w in widgets]})

    if request.method == "POST":
        _bloque = _interdit_lecteur(request)
        if _bloque:
            return _bloque
        corps = json.loads(request.body or "{}")
        type_widget = corps.get("type_widget", "graphique")

        if type_widget == "texte":
            contenu_texte = (corps.get("contenu_texte") or "").strip()
            if not contenu_texte:
                return JsonResponse({"erreur": t("engine/views/_part_004.py:799:49")}, status=400)
            style_texte = corps.get("style_texte", "paragraphe")
            if style_texte not in dict(WidgetGraphiqueBI.STYLE_TEXTE_CHOICES):
                style_texte = "paragraphe"

            widget = WidgetGraphiqueBI.objects.create(
                tableau_bord=tableau_bord, type_widget="texte",
                contenu_texte=contenu_texte, style_texte=style_texte,
                titre=corps.get("titre", ""),
                position_x=corps.get("position_x", 0), position_y=corps.get("position_y", 0),
                largeur=corps.get("largeur", 4), hauteur=corps.get("hauteur", 2),
                ordre_z=corps.get("ordre_z", tableau_bord.widgets.count()),
                options_json=corps.get("options") or None,
                statut="en_attente",
            )
            from ..tableau_bord_bi import calculer_widget
            calculer_widget(widget)  # aucun calcul réel, marque juste le widget "pret"
            return JsonResponse({"widget": _serialiser_widget_bi(widget)}, status=201)

        if type_widget == "kpi":  # KPI posé sur le canevas : la valeur reste celle de l'IndicateurKPI (kpi.py)
            kpi = IndicateurKPI.objects.filter(
                id=corps.get("kpi_id"), entreprise=entreprise, table_source=tableau_bord.table_source,
            ).first()
            if not kpi:
                return JsonResponse({"erreur": t("engine/views/_part_004.py:g:867:48")}, status=404)
            if tableau_bord.widgets.filter(type_widget="kpi", kpi=kpi).exists():
                return JsonResponse({"erreur": t("engine/views/_part_004.py:g:869:48")}, status=400)
            widget = WidgetGraphiqueBI.objects.create(
                tableau_bord=tableau_bord, type_widget="kpi", kpi=kpi,
                position_x=corps.get("position_x", 0), position_y=corps.get("position_y", 0),
                largeur=corps.get("largeur", 3), hauteur=corps.get("hauteur", 2),
                ordre_z=corps.get("ordre_z", tableau_bord.widgets.count()),
                statut="en_attente",
            )
            from ..tableau_bord_bi import calculer_widget
            calculer_widget(widget)  # marque juste le widget "pret"
            return JsonResponse({"widget": _serialiser_widget_bi(widget)}, status=201)

        if type_widget == "carte":  # points = colonnes geolocalisation de la table source (voir calculer_widget)
            widget = WidgetGraphiqueBI.objects.create(tableau_bord=tableau_bord, type_widget="carte", titre=corps.get("titre", ""),
                                                      largeur=6, hauteur=4, ordre_z=tableau_bord.widgets.count())
            from ..tableau_bord_bi import calculer_widget
            calculer_widget(widget)
            return JsonResponse({"widget": _serialiser_widget_bi(widget)}, status=201)

        graphique_id = corps.get("graphique_id")
        if not graphique_id:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:827:45")}, status=400)
        graphique = GraphiqueBI.objects.filter(
            id=graphique_id
        ).filter(Q(entreprise__isnull=True) | Q(entreprise=entreprise)).first()
        if not graphique:
            return JsonResponse({"erreur": t("engine/views/_part_004.py:832:45")}, status=404)

        champs_ids = corps.get("champs_cibles_ids") or []
        champs_valides = list(
            ChampDynamique.objects.filter(id__in=[i for i in champs_ids if isinstance(i, int) and i > 0], table=tableau_bord.table_source)
        ) if champs_ids else []
        # matricule / clé d'accès : colonnes de lecture (ids négatifs), comptées comme les autres ; les ids qui ne sont pas ceux d'un vrai
        # champ de la table ni d'une de ses colonnes d'identification sont écartés
        from .. import identifiants_analyse as _ident
        virtuelles = _ident.ids_valides(tableau_bord.table_source, champs_ids)
        champs_ids = [i for i in champs_ids if i in virtuelles or i in {c.id for c in champs_valides}]
        if len(champs_valides) + len(virtuelles) < graphique.nombre_colonnes_min:
            return JsonResponse(
                {"erreur": tf("engine/views/_part_004.py:g:903:28", v1=graphique.nombre_colonnes_min)},
                status=400,
            )

        widget = WidgetGraphiqueBI.objects.create(
            tableau_bord=tableau_bord, type_widget="graphique", graphique=graphique,
            titre=corps.get("titre", ""),
            # Description lisible (bouton 📝 du widget) : celle fournie, sinon celle du graphique de la bibliothèque.
            description=(corps.get("description") or "").strip() or graphique.description,
            position_x=corps.get("position_x", 0), position_y=corps.get("position_y", 0),
            largeur=corps.get("largeur", 4), hauteur=corps.get("hauteur", 3),
            ordre_z=corps.get("ordre_z", tableau_bord.widgets.count()),
            options_json=corps.get("options") or None,
            ordre_champs=champs_ids or None,
            statut="en_cours",
        )
        if champs_valides:
            widget.champs_cibles.set(champs_valides)

        # Rendu synchrone : le graphique (matplotlib -> PNG base64, tout en
        # mémoire) est calculé tout de suite dans la requête, sans passer par
        # une tâche Celery — évite de dépendre d'un worker séparé pour un
        # traitement qui ne prend de toute façon que quelques centaines de
        # millisecondes. Le front reçoit directement le widget avec son
        # statut final ("pret"/"echec") et l'image s'affiche sans polling.
        from ..tableau_bord_bi import calculer_widget
        calculer_widget(widget)
        return JsonResponse({"widget": _serialiser_widget_bi(widget)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_004.py:867:37")}, status=405)




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
