from engine.i18n import tf
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from engine.i18n import t
from ._header import *  # noqa
from ..couleur_colonne import normaliser_couleur  # noqa
from .. import propriete_tables as _propriete  # noqa


def _taille_max_mo(valeur):
    """Taille maximale saisie (Mo) -> entier >= 1, ou None = AUCUNE limite (vide, 0, texte illisible ou négatif)."""
    try:
        n = int(float(str(valeur).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _partage_actif(entreprise_cible, type_objet, objet_id):
    """True si entreprise_cible a reçu un accès actif à cet objet précis
    (voir models.PartageInterAgent). Sert de base aux futures vues de
    lecture (table/dashboard/ontologie partagés) et à l'autorisation
    d'ajout cross-entreprise d'un ChampDynamique à un GroupePartage."""
    champ = PartageInterAgent.CHAMP_PAR_TYPE.get(type_objet)
    if not champ:
        return False
    return PartageInterAgent.objects.filter(
        entreprise_cible=entreprise_cible, type_objet=type_objet,
        actif=True, **{f"{champ}_id": objet_id},
    ).exists()


def _table_via_partage(request, table_id):
    """Retourne (table, lecture_seule) : la table de l'entreprise courante
    si elle lui appartient (lecture_seule=False, comportement normal), sinon
    une table d'une AUTRE entreprise mise à disposition en lecture seule via
    un PartageInterAgent(type_objet='table') actif — comme un client, mais
    utilisable "comme si c'était sa table" (recherche, cartes de calcul/
    documents), sans jamais pouvoir la modifier. 404 si ni l'un ni l'autre."""
    entreprise = _entreprise_courante(request)
    table = TableDynamique.objects.filter(id=table_id, entreprise=entreprise).first()
    if table:
        return table, False
    from .. import base_par_entreprise as _bpe
    if _bpe.actif():
        # Un fichier par entreprise : les identifiants ne sont plus uniques d'une entreprise à l'autre. Le partage (base système) dit
        # QUELLE entreprise détient la table ; la vue lit alors, en lecture seule, dans le fichier de cette entreprise.
        partage = PartageInterAgent.objects.filter(entreprise_cible=entreprise, type_objet="table", actif=True,
                                                   table_id=table_id).first()
        if partage is None:
            raise Http404
        _bpe.definir(partage.entreprise_source_id)
        table = get_object_or_404(TableDynamique, id=table_id, entreprise_id=partage.entreprise_source_id)
        return table, True
    table = get_object_or_404(TableDynamique, id=table_id)
    if not _partage_actif(entreprise, "table", table.id):
        raise Http404
    return table, True


@login_required
@ensure_csrf_cookie
@require_http_methods(["GET"])
def api_entreprise_nom(request, entreprise_id):
    """Résout uniquement le nom d'une entreprise à partir de son ID — pas de
    liste/recherche globale (éviterait d'exposer l'annuaire complet des
    entreprises de la plateforme). Sert à vérifier le destinataire avant de
    créer un PartageInterAgent."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    autre = get_object_or_404(Entreprise, id=entreprise_id)
    return JsonResponse({"id": autre.id, "nom": autre.nom})



@login_required
@require_http_methods(["GET"])
def api_entreprise_rechercher(request):
    """Recherche d'entreprises par nom (partiel, insensible à la casse) —
    utilisée par la page de partages inter-agents pour remplacer la saisie
    brute d'un ID numérique. Retourne uniquement id+nom, exclut l'entreprise
    courante. Réservée aux agents/admins."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"entreprises": []})
    entreprise_courante = _entreprise_courante(request)
    qs = Entreprise.objects.filter(nom__icontains=q).exclude(id=entreprise_courante.id)[:10]
    return JsonResponse({"entreprises": [{"id": e.id, "nom": e.nom} for e in qs]})


@ensure_csrf_cookie
def vue_partages_agents(request):
    """Page dédiée à la gestion des partages inter-agents (émis et reçus) —
    réservée aux agents/admins, jamais aux lecteurs (clients)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return redirect("engine:profile")
    entreprise = _entreprise_courante(request)
    return render(request, "engine/partages_agents.html", {"entreprise": entreprise})


def _serialiser_partage_inter_agent(p):
    return {"id": p.id, "entreprise_source_id": p.entreprise_source_id,
        "entreprise_cible_id": p.entreprise_cible_id, "type_objet": p.type_objet,
        "objet_id": getattr(p, f"{PartageInterAgent.CHAMP_PAR_TYPE[p.type_objet]}_id", None), "actif": p.actif}


@login_required
def api_partages_inter_agents(request):
    """Liste (émis+reçus) / crée un partage inter-agents (jamais un lecteur)."""
    entreprise = _entreprise_courante(request)
    if request.method == "GET":
        partages = PartageInterAgent.objects.filter(
            Q(entreprise_source=entreprise) | Q(entreprise_cible=entreprise))
        return JsonResponse({"partages": [_serialiser_partage_inter_agent(p) for p in partages]})
    if _interdit_lecteur(request):
        return _interdit_lecteur(request)
    corps = json.loads(request.body or "{}")
    cible = get_object_or_404(Entreprise, id=corps.get("entreprise_cible_id"))
    type_objet = corps.get("type_objet")
    champ = PartageInterAgent.CHAMP_PAR_TYPE.get(type_objet)
    if not champ or cible.id == entreprise.id:
        return JsonResponse({"erreur": t("engine/views/_part_001.py:99:41")}, status=400)

    objet_id = corps.get("objet_id")
    champ_id = corps.get("champ_id")
    if type_objet == "colonne" and champ_id and not objet_id:
        # Partage d'une colonne "normale" qui n'a pas encore de
        # GroupePartage : on le crée à la volée plutôt que d'exiger que
        # l'agent aille d'abord l'activer depuis la structure de table.
        champ_dyn = get_object_or_404(
            ChampDynamique, id=champ_id, table__entreprise=entreprise)
        if champ_dyn.type_champ == "constante":
            return JsonResponse({"erreur": t("engine/views/_part_001.py:110:45")},
                                status=400)
        if not champ_dyn.groupe_partage_id:
            champ_dyn.groupe_partage = GroupePartage.objects.create(
                entreprise=entreprise, nom=champ_dyn.nom)
            champ_dyn.save(update_fields=["groupe_partage"])
        objet_id = champ_dyn.groupe_partage_id
    elif type_objet == "colonne" and not objet_id:
        return JsonResponse({"erreur": t("engine/views/_part_001.py:118:41")}, status=400)

    # Isolation par entreprise : on ne partage QUE un objet qui appartient à l'entreprise émettrice
    # (sinon une entreprise pourrait « offrir » à une autre les données d'une troisième).
    modele_objet = {"colonne": GroupePartage, "table": TableDynamique,
                    "dashboard": TableauBordBI, "ontologie": PageOntologie}[type_objet]
    try:
        objet_id = int(objet_id)
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_001.py:118:41")}, status=400)
    from .. import base_par_entreprise as _bpe
    if _bpe.actif() and type_objet != "table":
        return JsonResponse({"erreur": t("plateforme.partage.seule_table")}, status=400)
    with _bpe.dans_entreprise(entreprise.id):
        if not modele_objet.objects.filter(id=objet_id, entreprise=entreprise).exists():
            raise Http404
    if _bpe.actif() and type_objet == "table":
        # Un fichier par entreprise : l'adresse /tables/<id>/ doit désigner UNE table pour le destinataire. Si cette entreprise a déjà
        # une table de même identifiant, la sienne gagnerait toujours : le partage serait inutilisable, on le refuse.
        with _bpe.dans_entreprise(cible.id):
            if TableDynamique.objects.filter(id=objet_id).exists():
                return JsonResponse({"erreur": t("plateforme.partage.conflit_id")}, status=409)

    # Création atomique : un partage identique déjà présent est réactivé, jamais dupliqué.
    with transaction.atomic():
        existant = PartageInterAgent.objects.select_for_update().filter(
            entreprise_source=entreprise, entreprise_cible=cible, type_objet=type_objet,
            **{f"{champ}_id": objet_id}).first()
        if existant:
            if not existant.actif:
                existant.actif = True
                existant.save(update_fields=["actif"])
            return JsonResponse({"partage": _serialiser_partage_inter_agent(existant)}, status=200)
        partage = PartageInterAgent(entreprise_source=entreprise, entreprise_cible=cible,
            type_objet=type_objet, cree_par=request.user)
        setattr(partage, f"{champ}_id", objet_id)
        partage.full_clean()
        partage.save()
    return JsonResponse({"partage": _serialiser_partage_inter_agent(partage)}, status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def api_partage_inter_agent_detail(request, partage_id):
    entreprise = _entreprise_courante(request)
    partage = get_object_or_404(PartageInterAgent, id=partage_id, entreprise_source=entreprise)
    if _interdit_lecteur(request):
        return _interdit_lecteur(request)
    if request.method == "DELETE":
        with transaction.atomic():
            partage.delete()
        return JsonResponse({"ok": True})
    corps = json.loads(request.body or "{}")
    if "actif" in corps:
        partage.actif = bool(corps["actif"])
        partage.save(update_fields=["actif"])
    return JsonResponse({"partage": _serialiser_partage_inter_agent(partage)})


def _interdit_lecteur(request):
    """Retourne une réponse 403 si l'utilisateur est un lecteur (client).
    Appeler en début de toute vue API qui ne doit pas être accessible aux clients.
    Retourne None si l'accès est autorisé.
    """
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if membre and membre.role == "lecteur":
        return JsonResponse({"erreur": t("engine/views/_part_001.py:155:41")}, status=403)
    return None


def _interdit_non_superuser(request):
    """Retourne une réponse 403 si l'utilisateur courant n'est pas
    super-administrateur Django (`is_superuser`).

    Une tâche exécute du code sans garde-fou (voir engine/agent.py::
    executer_tache) : seul le super-admin peut donc en DÉFINIR une (création,
    import/édition de script, régénération IA) et assume la responsabilité de
    ce qu'il intègre. Les autres utilisateurs ne peuvent qu'exécuter/consulter
    les tâches préétablies. Retourne None si l'accès est autorisé."""
    if request.user.is_superuser:
        return None
    return JsonResponse(
        {"erreur": t("engine/views/_part_001.py:g:173:20")},
        status=403,
    )


def _interdit_non_admin(request, droit="config_ia"):
    """403 sauf pour l'administrateur (rôle « admin » de l'entreprise, ou super-administrateur Django), ou un agent à qui l'administrateur a
    délégué ce `droit` (voir delegation.py).
    L'administrateur est le seul à configurer (IA, tarifs et paiements, signatures…) ; agents et clients ne font qu'utiliser."""
    if request.user.is_superuser:
        return None
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from ..delegation import a_pouvoir
    if a_pouvoir(membre, droit):
        return None
    return JsonResponse({"erreur": _i18n_t("rg2.views__part_008.1")}, status=403)


def _interdit_non_proprietaire_tache(request, tache):
    """Retourne une réponse 403 si l'utilisateur courant n'est ni le
    créateur de `tache` (TachePlanifiee) ni un admin de l'entreprise.

    À appeler dans toute vue qui DÉFINIT ou ÉDITE une tâche existante
    (métadonnées, script, régénération...) — jamais pour l'exécuter ou la
    consulter, ce qui reste ouvert à tout agent/admin comme avant. Une fois
    lancé, le script d'une tâche accède aux données de la table cible
    librement (voir engine/agent.py::executer_tache) : seule la personne qui
    a réellement écrit/autorisé ce script (ou un admin, déjà responsable de
    toute l'entreprise) doit pouvoir en changer la définition — pas
    n'importe quel autre agent de l'entreprise. Retourne None si l'accès est
    autorisé."""
    if request.user.is_superuser or tache.utilisateur_id == request.user.id:   # §étanchéité : le super-admin (opérateur de la plateforme) n'est pas forcément membre de l'entreprise
        return None
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if membre and membre.role == "admin":
        return None
    return JsonResponse(
        {"erreur": t("engine/views/_part_001.py:181:21")},
        status=403,
    )


def _table_autorisee_agent(membre, table) -> bool:
    """Phase 9 — granularité « Objet » : un agent dont tables_autorisees est
    renseigné (liste d'IDs TableDynamique) ne voit QUE ces tables-là, en plus
    de l'isolation de rôle déjà en place. None/vide = pas de restriction
    (comportement d'avant ce champ). Ignoré pour admin/lecteur — même
    convention que vues_autorisees (_vue_autorisee ci-dessus)."""
    if membre is not None and membre.role == "agent" and _propriete.table_fermee(membre.utilisateur, table):   # confiée à d'autres agents (propriété partageable)
        return False
    if membre is not None and membre.role == "agent" and getattr(table, "source_systeme", ""):
        return table.id in (membre.tables_autorisees or [])   # table système (wallet, paiement…) : réservée à l'administrateur sauf autorisation explicite
    if membre is None or membre.role != "agent" or not membre.tables_autorisees:
        return True
    return table.id in membre.tables_autorisees


def _tables_permises_client(membre):
    """Ids de tables que l'agent a réservés à CE client (MembreEntreprise.tables_autorisees posé sur un lecteur), ou None = pas de
    restriction (toutes les tables exposées). Tout ce qui dépend d'une table (lignes, BI, KPI, pages d'analyse) suit cette liste."""
    if membre is None or membre.role != "lecteur" or not membre.tables_autorisees:
        return None
    return {int(i) for i in membre.tables_autorisees}


def _table_permise_client(membre, table) -> bool:
    permises = _tables_permises_client(membre)
    if permises is None:
        return True
    from ..vue_groupe import ids_groupe
    return bool(permises & set(ids_groupe(table)))


def _interdit_table_non_exposee(request, table):
    """Retourne une réponse 403 si l'utilisateur est un lecteur (client) et que
    `table` n'est pas accessible (table.accessible_client), OU si l'utilisateur
    est un agent restreint (Phase 9 : tables_autorisees) qui n'a pas accès à
    CETTE table précise. Un client ne doit voir/modifier que les tables
    exposées, jamais celles réservées aux agents/admins — même en appelant
    l'API des lignes directement avec l'id d'une table non exposée. Retourne
    None si l'accès est autorisé."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if membre and membre.role == "lecteur" and not table.accessible_client:
        return JsonResponse({"erreur": t("engine/views/_part_001.py:210:41")}, status=403)
    if membre and membre.role == "lecteur" and not _table_permise_client(membre, table):   # réservée à d'autres clients par l'agent
        return JsonResponse({"erreur": t("engine/views/_part_001.py:210:41")}, status=403)
    if not _table_autorisee_agent(membre, table):
        return JsonResponse({"erreur": t("engine/views/_part_001.py:212:41")}, status=403)
    return None


def _est_table_du_client(request, membre, table) -> bool:
    """True si `table` (ou une partie de son groupe) est LA table cliente du client connecté (code d'accès en session, sinon
    MembreEntreprise.table_cliente). Cette table est la table des agents et clients : jamais « exposée » au sens de
    accessible_client, mais c'est sur elle que l'agent pose les tableaux de bord qu'il met à disposition du client."""
    if membre is None or membre.role != "lecteur":
        return False
    tid = request.session.get("table_cliente_id") or membre.table_cliente_id
    if not tid:
        return False
    from ..vue_groupe import ids_groupe
    cible = TableDynamique.objects.filter(id=tid, entreprise=membre.entreprise).first()
    return bool(cible) and table.id in ids_groupe(cible)


def _interdit_table_bi(request, table):
    """Garde des pages/API de BI : comme _interdit_table_non_exposee, sauf qu'un client (lecteur) peut AUSSI atteindre sa
    propre table cliente. Les tableaux de bord et KPI qu'il y voit restent limités à ceux que l'agent a mis à sa disposition
    (visible_client) — voir _refus_tableau_bi_client et les filtres des API. Retourne None si l'accès est autorisé."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if _est_table_du_client(request, membre, table):
        return None
    if _table_porte_bi_client(membre, table):
        return None
    return _interdit_table_non_exposee(request, table)


def _table_porte_bi_client(membre, table) -> bool:
    """True si un client (lecteur) peut ouvrir la BI de `table` : l'agent y a mis au moins un tableau de bord ou un KPI à sa
    disposition (visible_client), même si la table elle-même n'est pas exposée. La table des agents et clients reste interdite
    (hors la table propre du client, traitée avant). Ne donne accès qu'aux objets visible_client : les lignes, elles, restent
    protégées par _interdit_table_non_exposee, et le filtre croisé reste refusé (_client_sans_filtre)."""
    if membre is None or membre.role != "lecteur" or table.est_table_membres or not _table_permise_client(membre, table):
        return False
    from ..vue_groupe import ids_groupe
    ids = ids_groupe(table)
    return (TableauBordBI.objects.filter(entreprise=membre.entreprise, table_source_id__in=ids, visible_client=True).exists()
            or IndicateurKPI.objects.filter(entreprise=membre.entreprise, table_source_id__in=ids, visible_client=True).exists())


def _refus_tableau_bi_client(request, tableau_bord):
    """403 JSON si l'utilisateur est un client (lecteur) et que ce tableau de bord ne lui est pas mis à disposition."""
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=tableau_bord.entreprise).first()
    if membre and membre.role == "lecteur" and not tableau_bord.visible_client:
        return JsonResponse({"erreur": t("engine/views/_part_004.py:757:45")}, status=403)
    return None


def _client_sans_filtre(request, table) -> bool:
    """True si l'utilisateur est un client (lecteur) sur une table qui ne lui est pas exposée : le filtre croisé
    (colonne = valeur) lui est refusé, car faire varier les graphiques et KPI selon une valeur lui permettrait de
    deviner le contenu d'une table qu'il n'a pas le droit de lire."""
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=table.entreprise).first()
    return bool(membre and membre.role == "lecteur" and not table.accessible_client)


def _champs_masques_pour(membre, table) -> set:
    """Phase 9 — granularité « Champ » : slugs de ChampDynamique masqués en
    lecture ET écriture pour cet agent sur CETTE table (champs_masques,
    dict {str(table_id): [slugs]}). Vide pour admin/lecteur/agent non
    restreint — même convention que tables_autorisees ci-dessus."""
    if membre is None or membre.role != "agent" or not membre.champs_masques:
        return set()
    return set(membre.champs_masques.get(str(table.id)) or ())


# ── Isolation entre agents ────────────────────────────────────────────────
# Chaque agent ne voit que les lignes qu'il a lui-meme creees ; seul l'admin
# voit tout (le lecteur/client reste regi par accessible_client, inchange).
def _isoler_lignes_agent(request, membre, queryset):
    if membre and membre.role == "agent":
        return _propriete.filtrer_lignes(queryset, request.user)
    return queryset


def _ligne_accessible(request, table, ligne_id, ecriture=False):
    """Ligne `ligne_id` de `table` SI l'utilisateur y a droit, sinon 404 (403 pour une écriture de client).

    L'isolation entre agents n'était appliquée qu'aux LISTES (api_lignes) : en connaissant un id, un agent
    modifiait, supprimait ou téléchargeait le reçu d'une ligne d'un autre agent. Règle unique pour toutes les
    vues « par ligne » : admin → toutes les lignes ; agent → ses lignes ; client (lecteur) → ses propres
    soumissions, en lecture seulement. Table reçue par partage inter-entreprises : lecture seule."""
    from django.core.exceptions import PermissionDenied
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related("entreprise"))
    if membre is None:
        raise Http404
    if ecriture and (membre.role == "lecteur" or table.entreprise_id != membre.entreprise_id):
        raise PermissionDenied
    # Un groupe de tables s'ouvre comme UNE table : la ligne peut appartenir à n'importe quelle partie du groupe
    # (l'appelant reprend alors `enregistrement.table` pour ses champs, ses recalculs et son audit).
    qs = Enregistrement.objects.filter(table__groupe_id=table.groupe_id) if table.groupe_id else Enregistrement.objects.filter(table=table)
    if membre.role == "agent" and table.entreprise_id == membre.entreprise_id:
        qs = _propriete.filtrer_lignes(qs, request.user)
    elif membre.role == "lecteur" and table.entreprise_id == membre.entreprise_id:
        qs = qs.filter(cree_par=request.user)
    return get_object_or_404(qs, id=ligne_id)


def _refus_client(request):
    """403 JSON si l'utilisateur est un client (lecteur) — pour les écritures de structure (formules,
    fusions) que _interdit_table_non_exposee laissait passer sur une table exposée."""
    membre = membre_de_requete(request, MembreEntreprise.objects.only("role"))
    if membre is not None and membre.role == "lecteur":
        return JsonResponse({"erreur": t("engine/views/_part_001.py:259:41")}, status=403)
    return None


# ── Permissions dynamiques par vue ────────────────────────────────────────
# Slugs des vues configurables — reutilises tels quels par le front (onglets
# de interface_unique + pages dediees). None/absent = pas de restriction.
def _vue_autorisee(membre, slug):
    """True si le membre peut voir cette vue. Admin : toujours. Agent avec
    vues_autorisees=None/vide : pas de restriction (comportement d'avant)."""
    if not membre or membre.role != "agent":
        return True
    vues = membre.vues_autorisees
    return not vues or slug in vues


def _interdit_vue(request, slug):
    """Equivalent de _interdit_lecteur, pour les vues dynamiques par agent."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if not _vue_autorisee(membre, slug):
        return redirect('/profile/')
    return None


def _entreprise_courante(request):
    entreprise = _entreprise_courante_brut(request)
    from .. import base_par_entreprise as _bpe
    if _bpe.actif() and _bpe.courante() != entreprise.id:
        _bpe.definir(entreprise.id)   # SQLite par entreprise : toutes les lectures suivantes vont dans le fichier de cette entreprise
    return entreprise


def _entreprise_courante_brut(request):
    """Retourne l'entreprise de l'utilisateur.

    Une seule entreprise par installation. Sans MembreEntreprise, seul le super administrateur Django est rattaché
    (administrateur de l'entreprise unique, créée à sa première connexion) ; un client ou agent sans rattachement est refusé.
    """
    # Une requête par clé API reste TOUJOURS dans l'entreprise de sa clé (jamais celle d'une session ni le premier rattachement du compte).
    membre = membre_de_requete(request, request.user.membreentreprise_set.select_related("entreprise"))
    if membre is not None:
        from .. import base_par_entreprise as _bpe
        _bpe.definir(membre.entreprise_id)   # SQLite par entreprise : le fichier de l'entreprise du membre
        from .. import tables_systeme   # tables du code (membres, wallet, paiement, messagerie) accessibles aux analyses dès l'installation
        tables_systeme.garantir(request, membre.entreprise)
        return membre.entreprise

    # Aucun membre : une SEULE entreprise par installation. Seul le super administrateur Django peut en devenir l'administrateur
    # (il crée l'entreprise si elle n'existe pas encore) ; tout autre compte sans rattachement est refusé, jamais promu.
    from django.core.exceptions import PermissionDenied
    if not request.user.is_superuser:
        raise PermissionDenied
    from django.conf import settings as _reglages
    if getattr(_reglages, "GYPLATFORM_MULTI_ENTREPRISE", False):
        # Plateforme multi-entreprises : l'opérateur (super administrateur) n'est JAMAIS rattaché d'office à l'entreprise d'un client
        # (ce serait lui donner la main sur ses données) : il a la sienne, créée une fois.
        from ..securite import slug_entreprise_libre
        nom = request.user.username
        if not Entreprise.objects.filter(nom="").exists():
            nom = ""   # première installation : nom vide = identité d'origine
        elif Entreprise.objects.filter(nom__iexact=nom).exists():
            nom = f"{nom} (administration)"
        entreprise = Entreprise.objects.create(nom=nom, slug=slug_entreprise_libre(f"entreprise-{request.user.username}"))
    else:
        entreprise = Entreprise.objects.order_by("id").first() or Entreprise.objects.create(
            nom="", slug=f"entreprise-{request.user.username}")
    MembreEntreprise.objects.get_or_create(
        utilisateur=request.user, entreprise=entreprise,
        defaults={"role": "admin"},
    )
    from .. import base_par_entreprise as _bpe
    _bpe.definir(entreprise.id)   # SQLite par entreprise : les tables créées ci-dessous vont dans le fichier de cette entreprise
    from .. import tables_systeme
    tables_systeme.garantir(request, entreprise)
    return entreprise


@login_required
@ensure_csrf_cookie
def interface_unique(request, slug=None):
    """La seule page HTML. Tout le reste passe par l'API JSON ci-dessous."""
    # Les clients (lecteurs) n'ont pas accès à l'interface complète.
    # Ils sont redirigés vers leur espace formulaire dédié.
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if membre and membre.role == "lecteur":
        return redirect("engine:espace_client")
    if slug:   # dernière table ouverte : sert au bouton « Retour à {table} » des autres sidebars (context_processors.retour_table)
        _t = TableDynamique.objects.filter(entreprise=entreprise, slug=slug).only("slug", "nom").first()
        if _t:
            request.session["retour_table"] = {"slug": _t.slug, "nom": _t.nom}

    from ..inference import (
        EXEMPLE_SCRIPT_PYTHON, EXEMPLE_SCRIPT_DOCUMENT,
        EXEMPLE_FORMULE_COLONNE, EXEMPLE_FORMULE_TABLE,
        EXEMPLE_GRAPHIQUE_BI_COLONNE, EXEMPLE_GRAPHIQUE_BI_MULTIPLES,
        EXEMPLE_COUCHE_ONTOLOGIE, EXEMPLE_MODELE_IA, EXEMPLE_TACHE,
    )
    from ..formules_science import TYPES_NUMERIQUES
    return render(request, "engine/index.html", {
        "TYPES_NUMERIQUES": TYPES_NUMERIQUES,
        "EXEMPLE_SCRIPT_PYTHON": EXEMPLE_SCRIPT_PYTHON,
        "EXEMPLE_SCRIPT_DOCUMENT": EXEMPLE_SCRIPT_DOCUMENT,
        "EXEMPLE_FORMULE_COLONNE": EXEMPLE_FORMULE_COLONNE,
        "EXEMPLE_FORMULE_TABLE": EXEMPLE_FORMULE_TABLE,
        "EXEMPLE_GRAPHIQUE_BI_COLONNE": EXEMPLE_GRAPHIQUE_BI_COLONNE,
        "EXEMPLE_GRAPHIQUE_BI_MULTIPLES": EXEMPLE_GRAPHIQUE_BI_MULTIPLES,
        "EXEMPLE_COUCHE_ONTOLOGIE": EXEMPLE_COUCHE_ONTOLOGIE,
        "EXEMPLE_MODELE_IA": EXEMPLE_MODELE_IA,
        "EXEMPLE_TACHE": EXEMPLE_TACHE,
        "vues_autorisees_json": json.dumps(membre.vues_autorisees if membre and membre.role == "agent" else None),
    })


# ── TABLES : creation et liste, instantanees (metadonnees, pas de migration) ──
# ── Champ photo par défaut des tables d'agents ──────────────────────────────
# Le champ photo n'est qu'un champ "image" ordinaire du moteur de table
# dynamique existant : pas de type spécial, donc il hérite automatiquement
# du bouton "📷 Photo" (capture caméra) déjà branché sur tout champ image.
def _assurer_champ_photo_agent(table):
    if not table.champs.filter(slug="photo").exists():
        ChampDynamique.objects.create(
            table=table, nom="Photo", slug="photo", type_champ="image", obligatoire=False,
            ordre=(table.champs.aggregate(m=Max("ordre"))["m"] or 0) + 1,
        )


def _assurer_table_membres(table):
    """Table agents et clients (commune) : garantit « photo » (cartes, reports.py), « contact »
    (SMS, sms_mmcli.py) et le code d'accès individuel des lignes déjà présentes (les nouvelles
    le reçoivent dans Enregistrement.save())."""
    _assurer_champ_photo_agent(table)
    if not table.champs.filter(slug="contact").exists():
        ChampDynamique.objects.create(
            table=table, nom="Contact", slug="contact", type_champ="telephone", obligatoire=False,
            ordre=(table.champs.aggregate(m=Max("ordre"))["m"] or 0) + 1,
        )
    if not table.a_identifiants_connexion:
        return   # table de produits : aucun identifiant de connexion
    for enr in table.enregistrements.filter(code_acces_client=""):
        enr.code_acces_client = enr.generer_code_acces_client()
        enr.save(update_fields=["code_acces_client"])


def _dupliquer_champs_table(table_source, table_cible):
    """Recopie les ChampDynamique de table_source sur table_cible (même nom/slug/type/ordre).
    Exclut volontairement groupe_partage (éviter d'hériter d'un partage existant sur une
    table qui n'y a jamais consenti — voir _erreur_partage_entre_tables) ; relation_vers est
    conservé (pointe vers une table tierce, pas vers table_source ni table_cible).
    Utilisé par api_table_etendre_lignes et api_table_etendre_colonnes : une seule
    implémentation pour les deux, comme l'exige obligation_pour_les_ia_avant_de_commencer.txt."""
    for champ in table_source.champs.all():
        ChampDynamique.objects.create(
            table=table_cible, nom=champ.nom, slug=champ.slug, type_champ=champ.type_champ,
            obligatoire=champ.obligatoire, options_choix=champ.options_choix,
            relation_vers=champ.relation_vers,
            relation_champ_affichage=champ.relation_champ_affichage,
            relation_colonne_source=champ.relation_colonne_source,
            ordre=champ.ordre,
        )


@login_required
@require_http_methods(["POST"])
def api_table_etendre_lignes(request, table_id):
    """EXCEPTION 2026-09-24 (voir obligation_pour_les_ia_avant_de_commencer.txt) : la table a
    atteint la limite de lignes en saisie manuelle (voir api_lignes) — au lieu de bloquer,
    crée une nouvelle partie de même structure et la rattache au GroupeTables de `table`
    (ou en crée un si `table` était autonome), exactement comme le découpage automatique à
    l'import (import_fichier._importer_dataframe_vers_tables). Le front-end rejoue ensuite
    l'ajout de ligne refusé contre la table renvoyée ici."""
    from ._part_006 import _serialiser_table  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme

    # Seule la dernière table d'un groupe reçoit des lignes, donc s'étend : le groupe, ouvert comme une table, s'étend par sa fin.
    from ..vue_groupe import derniere_partie
    table = derniere_partie(table)
    from django.db import transaction as _tx
    from ..models import GroupeTables
    from ..import_fichier import _slug_table_unique
    with _tx.atomic():
        groupe = table.groupe
        if groupe is None:
            groupe = GroupeTables.objects.create(
                entreprise=entreprise, nom=table.nom,
                description=tf("engine/views/_part_001.py:g:440:29", v1=table.nom),
                cree_automatiquement=True,
            )
            table.groupe = groupe
            table.partie_index = 1
            table.save(update_fields=["groupe", "partie_index"])
            from .. import hierarchie
            hierarchie.placer_tables_dans_groupe(groupe, [table])  # le groupe prend la place de la table dans son conteneur

        derniere = groupe.tables.order_by("-partie_index").first() or table
        nb_parties = groupe.tables.count() + 1
        nom_partie = f"{groupe.nom} ({nb_parties}/{nb_parties})"[:120]
        nouvelle = TableDynamique.objects.create(
            entreprise=entreprise, nom=nom_partie,
            slug=_slug_table_unique(entreprise, nom_partie),
            description=tf("engine/views/_part_001.py:g:455:25", v1=nb_parties, v2=groupe.nom),
            groupe=groupe, partie_index=nb_parties, partie_total=nb_parties,
            partie_precedente=derniere, provient_fichier=table.provient_fichier,
        )
        _dupliquer_champs_table(table, nouvelle)
        groupe.tables.exclude(id=nouvelle.id).update(partie_total=nb_parties)

    return JsonResponse({"ok": True, "table": _serialiser_table(nouvelle)})


@login_required
@require_http_methods(["POST"])
def api_table_etendre_colonnes(request, table_id):
    """EXCEPTION 2026-09-24 (voir obligation_pour_les_ia_avant_de_commencer.txt) : la table a
    atteint la limite de colonnes (api_tables/api_restructurer_table) — crée une table liée
    (TableDynamique.table_extension_colonnes) pour accueillir les colonnes en trop, plutôt que
    de bloquer. Contrairement à api_table_etendre_lignes, PAS de GroupeTables (structures
    volontairement différentes) : simple lien organisationnel, chaîné si la table cible est
    elle-même déjà étendue."""
    from ._part_006 import _serialiser_table  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme

    bout = table
    while bout.table_extension_colonnes_id:
        bout = bout.table_extension_colonnes

    from django.db import transaction as _tx
    from ..import_fichier import _slug_table_unique
    with _tx.atomic():
        nom_suite = tf("engine/views/_part_001.py:g:488:21", v1=table.nom)[:120]
        nouvelle = TableDynamique.objects.create(
            entreprise=entreprise, nom=nom_suite,
            slug=_slug_table_unique(entreprise, nom_suite),
            description=tf("engine/views/_part_001.py:g:492:25", v1=bout.nom),
        )
        bout.table_extension_colonnes = nouvelle
        bout.save(update_fields=["table_extension_colonnes"])

    return JsonResponse({"ok": True, "table": _serialiser_table(nouvelle)})


@login_required
def api_tables(request):
    from ._part_006 import _serialiser_table, _lot_serialisation_tables  # import local (réf. avant sa définition)
    entreprise = _entreprise_courante(request)

    if request.method == "GET":
        membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
        tables_qs = TableDynamique.objects.filter(entreprise=entreprise).select_related("groupe", "partie_suivante").prefetch_related("champs__relation_vers")   # évite une requête par colonne relationnelle et par table (N+1)
        # Groupe fusionné en une table (groupe_fusionne.py) : ses parties restent en base mais ne sont plus listées, seule la table fusionnée l'est.
        tables_qs = tables_qs.filter(Q(groupe__isnull=True) | Q(groupe__tables_de_fusion__isnull=True))
        if membre and membre.role == "lecteur":
            # accessible_client = expose_client OR masquer_formulaire_client
            # (formulaire et recherche sont deux bascules independantes).
            tables_qs = tables_qs.filter(
                Q(expose_client=True) | Q(masquer_formulaire_client=True)
            ).exclude(est_table_membres=True)   # jamais la table agents et clients
            _permises = _tables_permises_client(membre)
            if _permises is not None:   # l'agent a restreint ce client à certaines tables
                tables_qs = [t for t in tables_qs if _table_permise_client(membre, t)]
        if request.GET.get("systeme") != "1":   # pages de données : les tables système n'y sont pas affichées ; les pages d'analyse les demandent (?systeme=1)
            tables_qs = [t for t in tables_qs if not t.source_systeme]
        if membre and membre.role == "agent":   # tables système (wallet, paiement…) : l'administrateur seul, sauf autorisation explicite
            tables_qs = [t for t in tables_qs if not t.source_systeme or _table_autorisee_agent(membre, t)]
        if membre and membre.role == "agent":   # tables confiées à d'autres agents : fermées
            _fermees = _propriete.ids_fermees(request.user)
            tables_qs = [t for t in tables_qs if t.id not in _fermees]
        tables_liste = list(tables_qs)
        lot = _lot_serialisation_tables(tables_liste)   # nombre de lignes / dernière du groupe : 3 requêtes au lieu de 2-3 par table
        return JsonResponse({"tables": [_serialiser_table(t, lot) for t in tables_liste]})

    if request.method == "POST":
        # La création de table est une action agent/admin : un client (lecteur)
        # ne doit jamais pouvoir en créer une, même en appelant l'API directement.
        _bloque_lecteur = _interdit_lecteur(request)
        if _bloque_lecteur:
            return _bloque_lecteur
        corps = json.loads(request.body or "{}")
        nom = (corps.get("nom") or "").strip()
        if not nom:
            return JsonResponse({"erreur": t("engine/views/_part_001.py:495:45")}, status=400)

        slug = base_slug = corps.get("slug") or nom.lower().replace(" ", "_")
        _n = 2
        while TableDynamique.objects.filter(entreprise=entreprise, slug=slug).exists():   # nom déjà pris : suffixe _2, _3… (sinon 500 sur la contrainte unique)
            slug, _n = f"{base_slug}_{_n}", _n + 1
        type_stockage = corps.get("type_stockage")
        if type_stockage not in (TableDynamique.STOCKAGE_SQL, TableDynamique.STOCKAGE_NOSQL):
            type_stockage = TableDynamique.STOCKAGE_SQL
        table = TableDynamique.objects.create(
            entreprise=entreprise, nom=nom, slug=slug, description=corps.get("description", ""),
            taille_echantillon=corps.get("taille_echantillon") or 5,
            est_table_membres=bool(corps.get("est_table_membres")),
            # Case « table de produits » : n'a de sens que pour une table agents/clients (sans identifiants de connexion).
            est_table_produits=bool(corps.get("est_table_membres")) and bool(corps.get("est_table_produits")),
            type_stockage=type_stockage,
        )
        champs_initiaux = corps.get("champs", [])
        plafond_col = table.plafond_colonnes
        if plafond_col is not None and len(champs_initiaux) > plafond_col:
            table.delete()
            return JsonResponse(
                {"erreur": tf("engine/views/_part_001.py:g:542:28", v1=plafond_col, v2=plafond_col),
                 "peut_etendre": True, "plafond_colonnes": plafond_col},
                status=400,
            )
        erreur_constante = _erreur_constantes(champs_initiaux) or _erreur_couleurs(champs_initiaux) or _erreur_colonnes(champs_initiaux, entreprise)
        if erreur_constante:
            table.delete()
            return JsonResponse({"erreur": erreur_constante}, status=400)
        for ordre, champ in enumerate(champs_initiaux):
            relation_vers = None
            if champ.get("relation_vers_id"):
                relation_vers = TableDynamique.objects.filter(
                    id=champ["relation_vers_id"], entreprise=entreprise
                ).first()
            ChampDynamique.objects.create(
                table=table, nom=champ["nom"], slug=champ.get("slug") or champ["nom"].lower().replace(" ", "_"),
                type_champ=champ.get("type", "texte"), obligatoire=champ.get("obligatoire", True), ordre=ordre,
                options_choix=(ChampDynamique.normaliser_constante(champ.get("options", "")) or "0")
                if champ.get("type") == "constante" else champ.get("options", ""),
                relation_vers=relation_vers,
                relation_champ_affichage=champ.get("relation_champ_affichage", ""),
                relation_colonne_source=champ.get("relation_colonne_source", ""),
                relation_bidirectionnelle=bool(champ.get("relation_bidirectionnelle", False)),
                couleur=normaliser_couleur(champ.get("couleur")),
                taille_max_mo=_taille_max_mo(champ.get("taille_max_mo")),
            )
        if table.est_table_membres:
            _assurer_table_membres(table)
        from ..models import Bloc
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "creation_table", "table_id": table.id, "table": table.nom,
            "agent_id": request.user.id, "nb_champs": len(champs_initiaux),
        })
        return JsonResponse({"table": _serialiser_table(table)}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_001.py:547:37")}, status=405)


@login_required
@require_http_methods(["POST"])
def api_importer_fichier(request):
    """Import a froid d'un CSV/Excel : sauvegarde le fichier sur disque,
    cree une TacheImport et delegue le traitement a Celery.
    Repond immediatement avec {tache_id} pour que le frontend puisse poller.
    """
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    from engine import maj_table
    fichier = request.FILES.get("fichier")
    jeton = "" if fichier else (request.POST.get("jeton") or "").strip()   # « Online Sheet sécurisé » : jeton d'accès, chiffré avant de partir en tâche de fond
    url = "" if fichier else (request.POST.get("url") or "").strip()   # lien Google Sheet ou fichier en ligne : téléchargé par la tâche de fond (pas dans cette requête), lu comme un fichier téléversé
    try:
        if url: maj_table.verifier_source(url, jeton or None)   # refus immédiat d'un lien (ou d'un jeton) invalide
    except (ValueError, OSError) as exc: return JsonResponse({"erreur": str(exc)}, status=400)
    if not fichier and not url:
        return JsonResponse({"erreur": t("engine/views/_part_001.py:562:41")}, status=400)

    entreprise = _entreprise_courante(request)
    feuille    = request.POST.get("feuille") or None
    separateur = request.POST.get("separateur") or None
    encodage   = request.POST.get("encodage") or None
    type_stockage = request.POST.get("type_stockage") or "sql"
    if type_stockage not in ("sql", "nosql"):
        type_stockage = "sql"
    mode_restauration = request.POST.get("mode_restauration") or "copie"   # export fidèle : "copie" ou "remplacer" (table native)
    if mode_restauration not in ("copie", "remplacer"):
        mode_restauration = "copie"

    import tempfile, shutil
    chemin = None   # lien : la tâche télécharge elle-même
    if fichier:
        suffix = os.path.splitext(fichier.name)[-1] or ".tmp"
        os.makedirs(dj_settings.MEDIA_ROOT, exist_ok=True)   # absent sur une installation neuve → FileNotFoundError (500)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                          dir=dj_settings.MEDIA_ROOT)
        try:
            shutil.copyfileobj(fichier, tmp)
            tmp.flush()
            chemin = tmp.name
        finally:
            tmp.close()
    jeton_chiffre = None
    if jeton:
        from engine import source_en_ligne
        jeton_chiffre = source_en_ligne.chiffrer_jeton(jeton)   # le jeton en clair ne va ni dans Celery ni en base
    nom = fichier.name if fichier else (url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or url)[:200]   # nom provisoire d'un lien (remplacé par celui du fichier téléchargé)

    tache = TacheImport.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        nom_fichier=nom,
        statut="en_attente",
    )

    _taches.importer_fichier_async.delay(
        tache.id, chemin,
        feuille=feuille, separateur=separateur, encodage=encodage,
        type_stockage=type_stockage, mode_restauration=mode_restauration,
        url_source=url or None, jeton_chiffre=jeton_chiffre,
    )
    return JsonResponse({"tache_id": tache.id, "nom_fichier": nom}, status=202)


@login_required
@require_http_methods(["POST"])
def api_importer_fichier_feuilles(request):
    """Previsualisation des feuilles (onglets) d'un fichier Excel AVANT
    l'import reel. Permet au frontend de proposer un dropdown de choix.

    Retourne :
      - {"feuilles": ["Feuil1", "Feuil2", ...], "est_excel": true}
      - {"feuilles": [], "est_excel": false}  pour un CSV
    """
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    fichier = request.FILES.get("fichier")
    if not fichier:
        return JsonResponse({"erreur": t("engine/views/_part_001.py:614:41")}, status=400)

    import os
    _, ext = os.path.splitext(fichier.name.lower())
    est_excel = ext in (".xlsx", ".xls")
    reponse = {"feuilles": [], "est_excel": est_excel, "export_fidele": None, "json_independant": False}

    # Export de la plateforme (manifeste) ? Alors table NATIVE (existe encore ici → restaurable sur place) ou INDÉPENDANTE (à recréer) ;
    # un JSON sans manifeste est une table indépendante ordinaire. Un export modifié à la main est signalé dès maintenant.
    if ext in (".xlsx", ".json", ".parquet"):
        from .. import export_fidele
        try:
            manifeste = export_fidele.lire(fichier, ext.lstrip("."))
            if manifeste is not None:
                reponse["export_fidele"] = export_fidele.resume(_entreprise_courante(request), manifeste)
            elif ext == ".json":
                reponse["json_independant"] = True
        except ValueError as exc:
            reponse["export_fidele"] = {"erreur": str(exc)}
        try:
            fichier.seek(0)
        except Exception:
            pass

    if not est_excel:
        return JsonResponse(reponse)

    try:
        feuilles = lire_noms_feuilles(fichier)
    except Exception:
        import traceback
        traceback.print_exc()
        return JsonResponse(
            {"erreur": t("engine/views/_part_001.py:629:25")},
            status=400,
        )

    reponse["feuilles"] = [f for f in feuilles if f != "_gy_meta"]   # feuille cachée du manifeste : pas un onglet de données
    return JsonResponse(reponse)


@login_required
@require_http_methods(["POST"])
def api_supprimer_table(request, table_id):
    """Supprime la table avec ses demandes, scripts, résultats, graphiques et lignes. Corps JSON optionnel {"groupe": true} : si la table
    appartient à un groupe (qui s'ouvre comme UNE table), c'est tout le groupe qui part, parties et table fusionnée comprises.
    Les lignes partent par paquets (engine/suppression_masse.py) ; un gros volume rend une tâche de fond {"tache_id": …} (202)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import suppression_masse, tables_systeme
    try:
        corps = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        corps = {}
    groupe = table.groupe if (corps.get("groupe") and table.groupe_id) else None
    tables = suppression_masse.tables_du_groupe(groupe) if groupe else [table]
    for t in tables:
        _refus_systeme = tables_systeme.refus_ecriture(t)
        if _refus_systeme:
            return _refus_systeme
    reponse, statut = suppression_masse.lancer(entreprise, request.user, tables, groupe)
    return JsonResponse(reponse, status=statut)


@login_required
def api_stats_table(request, table_id):
    """Stats rapides de la table : nb lignes, nb colonnes, types, dernière modif."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    champs = list(table.champs.all())
    nb_lignes = table.enregistrements_groupe.count()  # groupe = une seule table : tout son volume

    # Répartition des types de champs
    types_count = {}
    for c in champs:
        types_count[c.type_champ] = types_count.get(c.type_champ, 0) + 1

    # Champs obligatoires vs optionnels
    nb_obligatoires = sum(1 for c in champs if c.obligatoire)

    # Dernière ligne ajoutée
    derniere = table.enregistrements_groupe.order_by("-cree_le").values_list("cree_le", flat=True).first()

    return JsonResponse({
        "table_id": table.id,
        "table_nom": table.nom,
        "nb_lignes": nb_lignes,
        "nb_colonnes": len(champs),
        "nb_obligatoires": nb_obligatoires,
        "nb_optionnels": len(champs) - nb_obligatoires,
        "types_repartition": types_count,
        "champs": [
            {"nom": c.nom, "slug": c.slug, "type": c.type_champ, "obligatoire": c.obligatoire}
            for c in champs
        ],
        "derniere_entree": derniere.isoformat() if derniere else None,
        "nb_skills": table.skills.count(),
    })





def _resoudre_groupe_partage(entreprise, champ_data):
    """Résout le GroupePartage à assigner à un champ à partir du payload JS.

    Deux formats de cible sont acceptés (et peuvent être combinés) :
      - `partage_cibles` : liste de `{table_id, slug}`, une entrée par
        table+colonne choisie dans le popup "Rendre partagée" — permet de
        créer/rejoindre un groupe de PLUS DE DEUX membres en un seul
        enregistrement (partage "en groupe", pas seulement "en paire").
      - `partage_table_cible_id` / `partage_colonne_cible` : ancien format à
        une seule cible, conservé pour compatibilité.

    - Si une des colonnes cibles (ou le champ lui-même via
      `groupe_partage_id`) appartient déjà à un groupe : ce groupe devient
      le groupe "canonique" et tous les autres y sont FUSIONNÉS (tous les
      champs membres d'un groupe cible différent sont réaffectés au groupe
      canonique). Ça permet de relier entre eux, après coup, deux groupes
      de partage déjà existants.
    - Sinon : un nouveau GroupePartage est créé.
    - Si aucune cible n'est fournie mais que le champ est déjà membre d'un
      groupe (`groupe_partage_id` fourni tel quel), on y reste — cas du
      simple ré-enregistrement de la structure sans toucher au partage.
    Retourne None si le champ n'est pas/plus partagé (case décochée).
    """
    cibles = list(champ_data.get("partage_cibles") or [])
    table_cible_id = champ_data.get("partage_table_cible_id")
    slug_cible = champ_data.get("partage_colonne_cible")
    if table_cible_id and slug_cible:
        cibles.append({"table_id": table_cible_id, "slug": slug_cible})

    if not cibles:
        groupe_id = champ_data.get("groupe_partage_id")
        if groupe_id:
            return GroupePartage.objects.filter(id=groupe_id, entreprise=entreprise).first()
        return None

    champs_cibles = []
    for cible in cibles:
        tid = cible.get("table_id")
        slug = cible.get("slug")
        if not tid or not slug:
            continue
        # Une cible reste normalement dans l'entreprise courante ; elle peut
        # aussi appartenir à une AUTRE entreprise si celle-ci a explicitement
        # autorisé le partage de CE groupe précis vers l'entreprise courante
        # (voir models.PartageInterAgent, type_objet="colonne") — la colonne
        # partagée reste alors éditable des deux côtés, contrairement à une
        # table/dashboard/ontologie partagés qui restent en lecture seule.
        champ_cible = ChampDynamique.objects.filter(table_id=tid, slug=slug).filter(
            Q(table__entreprise=entreprise) |
            Q(groupe_partage__partages_inter_agents__entreprise_cible=entreprise,
              groupe_partage__partages_inter_agents__type_objet="colonne",
              groupe_partage__partages_inter_agents__actif=True)
        ).distinct().first()
        if champ_cible:
            champs_cibles.append(champ_cible)
    if not champs_cibles:
        groupe_id = champ_data.get("groupe_partage_id")
        if groupe_id:
            return GroupePartage.objects.filter(id=groupe_id, entreprise=entreprise).first()
        return None

    # ── Groupe canonique : le groupe déjà courant du champ en cours
    # d'édition, sinon celui de la première cible qui en a déjà un.
    groupe = None
    groupe_id = champ_data.get("groupe_partage_id")
    if groupe_id:
        groupe = GroupePartage.objects.filter(id=groupe_id, entreprise=entreprise).first()
    if groupe is None:
        for cc in champs_cibles:
            if cc.groupe_partage_id:
                groupe = cc.groupe_partage
                break
    if groupe is None:
        groupe = GroupePartage.objects.create(entreprise=entreprise)

    # ── Rattache chaque cible au groupe canonique, en fusionnant son
    # ancien groupe (s'il en avait un différent) plutôt que de l'y
    # arracher seule — ça garde synchronisés les autres champs qui en
    # étaient déjà membres.
    for cc in champs_cibles:
        if cc.groupe_partage_id and cc.groupe_partage_id != groupe.id:
            ChampDynamique.objects.filter(groupe_partage_id=cc.groupe_partage_id).update(
                groupe_partage_id=groupe.id
            )
        elif not cc.groupe_partage_id:
            cc.groupe_partage = groupe
            cc.save(update_fields=["groupe_partage"])

    return groupe


def _appliquer_formule_champ(champ, champ_data):
    """Met à jour formule_expression/formule_etapes_json sur `champ` (objet
    en mémoire, PAS sauvegardé ici — à l'appelant de faire .save() juste
    après, comme pour les autres attributs du champ).

    Deux modes, distingués par la VALEUR de "formule_etapes_json" envoyée
    (voir index.html, bouton "🧩 Basculer en code libre" / assistant) :
    - null (code libre) : utilise formule_expression telle quelle et efface
      les étapes stockées, pour qu'une future réouverture du champ retombe
      bien en mode code libre.
    - une liste, même vide ou incomplète (assistant visuel) : regénère
      formule_expression à partir des étapes ; toute formule_expression
      envoyée en même temps par le client est ignorée (le code généré fait
      foi). Si les étapes sont incomplètes (ex: aucun champ coché), on ne
      touche à rien plutôt que d'écraser une formule valide précédente avec
      une erreur de génération.
    """
    if "formule_etapes_json" not in champ_data:
        return
    etapes = champ_data.get("formule_etapes_json")
    if etapes is None:
        champ.formule_etapes_json = None
        if "formule_expression" in champ_data:
            champ.formule_expression = champ_data.get("formule_expression", "")
        return
    from ..assistant_formule import generer_code_formule
    try:
        champ.formule_expression = generer_code_formule(etapes)
        champ.formule_etapes_json = etapes
    except ValueError:
        pass


def _erreur_constantes(champs):
    """Message d'erreur si une colonne de type « constante » n'a pas une valeur numérique (ex. 0.2 ou 0,2), sinon None.
    Une constante sans `options` dans la requête (structure inchangée) garde sa valeur actuelle : pas d'erreur."""
    for c in champs or []:
        if c.get("type") != "constante":
            continue
        if "options" in c and ChampDynamique.normaliser_constante(c["options"]) is None:
            return tf("engine/views/_part_001.py:g:899:20", v1=c.get('nom', ''))
        # Colonne partagée (GroupePartage) : la propagation créerait des lignes dans les autres tables à chaque
        # recopie de la constante ; sa valeur est de toute façon propre à chaque table.
        if c.get("partage_cibles") or c.get("partage_table_cible_id") or c.get("groupe_partage_id"):
            return (tf("engine/views/_part_001.py:g:903:21", v1=c.get('nom', '')))
    return None


def _erreur_couleurs(champs):
    """Message d'erreur si la `couleur` envoyée avec une colonne n'est ni un nom connu ni un code hexadécimal, sinon None (absente = inchangée)."""
    from ..couleur_colonne import normaliser_couleur
    for c in champs or []:
        try:
            normaliser_couleur(c.get("couleur"))
        except ValueError as e:
            return f"« {c.get('nom', '')} » : {e}"
    return None


def _erreur_colonnes(champs, entreprise):
    """Champ « colonne » : table, colonne du menu et colonne NOMINALE exigées (la nominale n'est pas utilisée : elle
    évite de confondre une colonne de chiffres), distinctes et présentes dans la table choisie, sinon message."""
    from ..flux_media import erreur_structure  # micro/caméra/diffusion : seul champ de sa table
    if erreur_structure(champs):
        return erreur_structure(champs)
    for c in champs or []:
        if c.get("type") != "colonne":
            continue
        nom, col = c.get("nom", ""), c.get("relation_colonne_source") or ""
        nominale, tid = c.get("relation_champ_affichage") or "", c.get("relation_vers_id")
        if not (tid and col and nominale):
            return tf("engine/views/_part_001.py:g:931:20", v1=nom)
        if col == nominale:
            return tf("engine/views/_part_001.py:g:933:20", v1=nom)
        cible = TableDynamique.objects.filter(id=tid, entreprise=entreprise).first()
        slugs = set(cible.champs.values_list("slug", flat=True)) if cible else set()
        if col not in slugs or nominale not in slugs:
            return tf("engine/views/_part_001.py:g:937:20", v1=nom)
    return None


def _erreur_partage_vers_constante(champs):
    """Message d'erreur si une colonne en cours de partage a pour cible une colonne « constante » d'une autre table
    (la propagation écraserait la constante ou créerait des lignes), sinon None."""
    for c in champs or []:
        cibles = list(c.get("partage_cibles") or [])
        if c.get("partage_table_cible_id") and c.get("partage_colonne_cible"):
            cibles.append({"table_id": c["partage_table_cible_id"], "slug": c["partage_colonne_cible"]})
        for cible in cibles:
            if cible.get("table_id") and cible.get("slug") and ChampDynamique.objects.filter(
                    table_id=cible["table_id"], slug=cible["slug"], type_champ="constante").exists():
                return (tf("engine/views/_part_001.py:g:951:25", v1=c.get('nom', ''), v2=cible['slug']))
    return None


def _erreur_structure_groupe(tables):
    """Un GroupeTables ne relie que des tables de même structure (mêmes colonnes, mêmes types :
    TableDynamique.signature_structure). Renvoie le message d'erreur si `tables` diffèrent, sinon None."""
    reference = tables[0].signature_structure if tables else None
    autres = [t.nom for t in tables[1:] if t.signature_structure != reference]
    if not autres:
        return None
    return (tf("engine/views/_part_001.py:g:963:13", v1=tables[0].nom, v2=', '.join(autres)))


def _erreur_partage_entre_tables(table_ids):
    """Deux tables d'un même groupe ne partagent ni colonne (GroupePartage) ni cellule (GroupePartageCellule) :
    incohérent. Renvoie le message d'erreur si, parmi `table_ids`, deux tables sont déjà reliées par un tel partage."""
    for lignes in (
        ChampDynamique.objects.filter(table_id__in=table_ids, groupe_partage__isnull=False)
        .values_list("groupe_partage_id", "table_id", "table__nom"),
        CellulePartagee.objects.filter(champ__table_id__in=table_ids)
        .values_list("groupe_id", "champ__table_id", "champ__table__nom"),
    ):
        vus = {}
        for partage_id, table_id, nom in lignes:
            if vus.setdefault(partage_id, (table_id, nom))[0] != table_id:
                return (tf("engine/views/_part_001.py:g:979:25", v1=vus[partage_id][1], v2=nom))
    return None


def _erreur_partage_meme_groupe(tables):
    """Refus d'un NOUVEAU partage de cellule qui réunirait deux tables d'un même GroupeTables (incohérent)."""
    par_groupe = {}
    for t in tables:
        if t.groupe_id:
            if t.groupe_id in par_groupe:
                return (tf("engine/views/_part_001.py:g:990:25", v1=par_groupe[t.groupe_id], v2=t.nom))
            par_groupe[t.groupe_id] = t.nom
    return None




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
