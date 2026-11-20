from engine.i18n import tf
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from .. import delegation as _delegation  # noqa: E402
from ..delegation import a_pouvoir as _a_pouvoir  # noqa: E402
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from ._part_004 import *  # noqa
from ._part_005 import *  # noqa
from ._part_006 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402


@login_required
@_pdf_erreur_json
def api_generer_cartes_zip(request, table_id):
    """Genere les cartes de TOUTE la table (ou des ids fournis) en les
    decoupant par lots de 10 (comme api_generer_cartes, un PDF = 10 cartes
    max), et renvoie un .zip telechargeable contenant un PDF par lot — le
    dernier lot incomplet est genere quand meme avec ce qu'il reste."""
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        actions.verifier_permission("generer_cartes_zip", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:22:41")}, status=403)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=membre.entreprise)
    if not table.config_carte:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:25:41")}, status=400)

    ids_brut = request.GET.get("ids", "").strip()
    if ids_brut:
        ids = [int(v) for v in ids_brut.split(",") if v.strip()]
        from ..vue_groupe import enregistrements_vue   # les ids viennent de la grille : tout le groupe
        par_id = {e.id: e for e in enregistrements_vue(table).filter(id__in=ids)}
        enregistrements = [par_id[i] for i in ids if i in par_id]
    else:
        from ..vue_groupe import enregistrements_vue
        enregistrements = list(enregistrements_vue(table).order_by("id"))
    if not enregistrements:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:35:41")}, status=404)

    champs_par_slug = {c.slug: c for c in table.champs.all()}
    if (table.config_carte or {}).get("sortie") == "images":   # « cartes seulement » : un PNG par carte et par face, sans lots de 10
        contenu, mime, nom = actions.executer_action(
            "generer_cartes_images", membre, table,
            enregistrements=enregistrements, champs_par_slug=champs_par_slug,
        )
        from django.http import HttpResponse
        reponse = HttpResponse(contenu, content_type=mime)
        reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
        return reponse
    zip_bytes = actions.executer_action(
        "generer_cartes_zip", membre, table,
        enregistrements=enregistrements, champs_par_slug=champs_par_slug,
    )

    from django.http import HttpResponse
    reponse = HttpResponse(zip_bytes, content_type="application/zip")
    reponse["Content-Disposition"] = f'attachment; filename="cartes_{table.slug}.zip"'
    return reponse


@login_required
def api_cloturer_cycle(request):
    """Bouton « Clôturer » : zippe le résumé général PDF de chaque table +
    un instantané des données, archive ce zip, puis vide les tables pour
    redémarrer l'interface de zéro (nouveau cycle)."""
    if request.method != 'POST':
        return JsonResponse({'erreur': t("engine/views/_part_007.py:55:41")}, status=405)
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        archive = actions.executer_action("cloturer_cycle", membre, membre.entreprise if membre else None, utilisateur=request.user)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:60:41")}, status=403)
    return JsonResponse({"archive_id": archive.id})


@login_required
def api_restaurer_cycle(request):
    """Bouton « Restaurer une archive » : reconstruit tables/champs/lignes/
    valeurs depuis une archive de clôture déjà enregistrée (archive_id) ou
    un zip téléversé (champ fichier)."""
    if request.method != 'POST':
        return JsonResponse({'erreur': t("engine/views/_part_007.py:70:41")}, status=405)
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        actions.verifier_permission("restaurer_cycle", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:75:41")}, status=403)
    remplacer = request.POST.get("remplacer") == "1"   # par défaut la restauration est NON destructive : elle crée une saison à part
    if request.FILES.get("fichier"):
        source = request.FILES["fichier"]
    else:
        corps = json.loads(request.body or '{}')
        archive_id, remplacer = corps.get("archive_id"), bool(corps.get("remplacer"))
        source = get_object_or_404(ArchiveCycle, id=archive_id, entreprise=membre.entreprise).fichier if archive_id else None
    if not source:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:82:41")}, status=400)
    try:
        actions.executer_action("restaurer_cycle", membre, membre.entreprise, source=source, utilisateur=request.user, remplacer=remplacer)
    except actions.ArchiveInvalide as exc:
        return JsonResponse({"erreur": str(exc)}, status=400)
    return JsonResponse({"ok": True})


@login_required
def api_exporter_modele(request):
    """Exporte la STRUCTURE de l'entreprise (tables, champs, formules, tableaux de bord, ontologies, configuration ; aucune donnée, aucun wallet,
    aucun membre, aucune clé) dans un zip à envoyer à un client : il le restaure avec « Restaurer ce fichier » et retrouve tout préconstruit.
    Ne modifie rien. Réservé aux administrateurs."""
    import tempfile
    from django.http import FileResponse
    from .. import cycle as _cycle
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        actions.verifier_permission("cloturer_cycle", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:106:40")}, status=403)
    tmp = tempfile.TemporaryFile()
    _cycle.generer_zip_modele_vers(membre.entreprise, tmp)
    tmp.seek(0)
    return FileResponse(tmp, as_attachment=True, filename=f"modele_{membre.entreprise.slug}.zip", content_type="application/zip")


@login_required
def api_liste_archives_cycle(request):
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        archives = actions.executer_action("lister_archives_cycle", membre, membre.entreprise if membre else None)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:96:41")}, status=403)
    return JsonResponse({"archives": archives}, safe=False)


@login_required
def api_images_auto(request):
    """Images automatiques (voir image_auto.py). GET : réglage + liste des images ;
    POST {actif, intervalle?} : active/désactive. Réservé aux administrateurs."""
    from .. import image_auto
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        actions.verifier_permission("restaurer_cycle", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:132:40")}, status=403)
    if request.method == 'POST':
        try:
            donnees = json.loads(request.body or '{}')
            image_auto.regler(membre.entreprise, donnees.get("actif"), donnees.get("intervalle"))
        except (ValueError, TypeError):
            return JsonResponse({"erreur": t("engine/views/_part_007.py:g:138:44")}, status=400)
    cfg = image_auto.lire_config(membre.entreprise)
    return JsonResponse({"actif": cfg["actif"], "intervalle": cfg["intervalle"],
                         "garder": image_auto.GARDER, "images": image_auto.lister(membre.entreprise)})


@login_required
def api_restaurer_image_auto(request):
    """Restaure une image automatique (état à l'identique, membres compris, sauf l'administrateur)."""
    from .. import image_auto
    if request.method != 'POST':
        return JsonResponse({"erreur": _gt("rg8.msg.016")}, status=405)
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        actions.verifier_permission("restaurer_cycle", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:154:40")}, status=403)
    nom = (json.loads(request.body or '{}')).get("nom")
    if image_auto.chemin_image(membre.entreprise, nom) is None:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:158:39")}, status=404)
    try:
        image_auto.restaurer_image(membre.entreprise, nom, request.user)
    except ValueError:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:161:40")}, status=400)
    except KeyError:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:163:40")}, status=400)
    return JsonResponse({"ok": True})


# ─────────────────────────────────────────────────────────────
# Clés API — accès externe (solutions indépendantes), voir models.CleAPI
# et engine/middleware.py::CleAPIAuthMiddleware. Réservé aux admins.
# ─────────────────────────────────────────────────────────────
def _admin_courant(request, entreprise):
    """Membre courant s'il peut gérer les clés API (droit `cles_api`), sinon None."""
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    return membre if _a_pouvoir(membre, "cles_api") else None


@login_required
@require_http_methods(["GET"])
def api_cles_api_liste(request):
    """Liste les clés API de l'entreprise — jamais le secret, seulement son
    préfixe (voir models.CleAPI.prefixe)."""
    from ..models import CleAPI
    entreprise = _entreprise_courante(request)
    if not _admin_courant(request, entreprise):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:117:41")}, status=403)
    cles = CleAPI.objects.filter(entreprise=entreprise).select_related("membre__utilisateur")
    return JsonResponse({"cles": [
        {
            "id": c.id, "nom": c.nom, "prefixe": c.prefixe, "actif": c.actif,
            "membre_id": c.membre_id,
            "membre_nom": (c.membre.utilisateur.get_full_name() or c.membre.utilisateur.get_username())
                          if c.membre.utilisateur_id else "",
            "cree_le": c.cree_le.isoformat(),
            "derniere_utilisation": c.derniere_utilisation.isoformat() if c.derniere_utilisation else None,
        }
        for c in cles
    ]})


@login_required
@require_http_methods(["POST"])
def api_creer_cle_api(request):
    """Crée une clé API incarnant un MembreEntreprise (soi-même par défaut,
    ou un autre membre via `membre_id`). Le secret en clair n'est renvoyé
    qu'UNE SEULE FOIS, dans cette réponse — il ne pourra plus jamais être
    relu ensuite, ni par cet admin ni par personne."""
    from ..models import CleAPI
    entreprise = _entreprise_courante(request)
    admin = _admin_courant(request, entreprise)
    if not admin:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:143:41")}, status=403)
    corps = json.loads(request.body or "{}")
    membre_id = corps.get("membre_id") or admin.id
    membre_cible = get_object_or_404(MembreEntreprise, id=membre_id, entreprise=entreprise)
    if not membre_cible.utilisateur_id:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:148:41")}, status=400)
    instance, secret = CleAPI.generer(entreprise, membre_cible, corps.get("nom", ""), request.user)
    return JsonResponse({"id": instance.id, "prefixe": instance.prefixe, "cle": secret}, status=201)


@login_required
@require_http_methods(["POST"])
def api_revoquer_cle_api(request, cle_id):
    """Révoque immédiatement une clé API (elle reste dans l'historique,
    mais toute requête l'utilisant renverra désormais 401)."""
    from ..models import CleAPI
    entreprise = _entreprise_courante(request)
    if not _admin_courant(request, entreprise):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:161:41")}, status=403)
    cle = get_object_or_404(CleAPI, id=cle_id, entreprise=entreprise)
    cle.actif = False
    cle.save(update_fields=["actif"])
    return JsonResponse({"ok": True})


@login_required
def api_membre_permissions(request, membre_id):
    """Lit/ecrit les vues_autorisees d'un membre (agent) — le bouton
    « Configurer les droits » de la liste des membres. Reserve a l'admin."""
    admin = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=request.user
    ).first()
    try:
        actions.verifier_permission("lire_permissions_membre", admin)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:178:41")}, status=403)
    cible = get_object_or_404(MembreEntreprise, utilisateur_id=membre_id, entreprise=admin.entreprise)

    if request.method == 'GET':
        resultat = actions.executer_action("lire_permissions_membre", admin, cible)
        from ..ontologie import DESCRIPTIONS_RELATIONS
        return JsonResponse({
            "vues_disponibles": [{"slug": s, "nom": n} for s, n in VUES_DISPONIBLES],
            "vues_autorisees": resultat["vues_autorisees"],
            "delegations_groupes": _delegation.groupes(),   # droits confiables à cet agent, par famille (delegation.py)
            "delegation_absolue": _delegation.absolu(),
            "delegations": sorted(_delegation.droits_effectifs_de_liste(cible.delegations)),   # ancien format (familles) développé en droits individuels
            "delegations_du_delegant": sorted(_delegation.droits_effectifs(admin)),   # ce que le connecté peut confier lui-même
            "role": resultat["role"],
            "tables_autorisees": resultat["tables_autorisees"],
            "champs_masques": resultat["champs_masques"],
            "relations_masquees": resultat["relations_masquees"],
            "relations_disponibles": [{"slug": r, "description": d} for r, d in DESCRIPTIONS_RELATIONS.items()],
        })

    if request.method == 'POST':
        corps = json.loads(request.body or '{}')
        cible = actions.executer_action(
            "modifier_permissions_membre", admin, cible, vues=corps.get("vues_autorisees"),
            tables=corps.get("tables_autorisees"), champs_masques=corps.get("champs_masques"),
            relations=corps.get("relations_masquees"),
        )
        if "delegations" in corps and cible.role == "agent":
            cible.delegations = _delegation.limiter_au_delegant(admin, cible.delegations, corps.get("delegations")) or None   # on ne confie que ce qu'on a
            cible.save(update_fields=["delegations"])
        return JsonResponse({
            "vues_autorisees": cible.vues_autorisees,
            "delegations": sorted(_delegation.droits_effectifs_de_liste(cible.delegations)),
            "tables_autorisees": cible.tables_autorisees,
            "champs_masques": cible.champs_masques,
            "relations_masquees": cible.relations_masquees,
        })

    return JsonResponse({'erreur': t("engine/views/_part_007.py:208:37")}, status=405)


@login_required
@_pdf_erreur_json
def api_exporter_employes_pdf(request):
    """Exporte la liste des membres de l'equipe en PDF (meme donnees que
    l'export CSV existant, mise en page tabulaire, sans IA)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=request.user
    ).first()
    try:
        pdf_bytes = actions.executer_action("exporter_employes_pdf", membre, membre.entreprise if membre else None)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:224:41")}, status=403)

    from django.http import HttpResponse
    reponse = HttpResponse(pdf_bytes, content_type="application/pdf")
    reponse["Content-Disposition"] = 'attachment; filename="membres_equipe.pdf"'
    return reponse


@login_required
@require_http_methods(['POST'])
def api_supprimer_employe(request, user_id):
    """Supprime un employe de l'entreprise."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=request.user
    ).first()
    try:
        actions.verifier_permission("supprimer_employe", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:245:41")}, status=403)
    if user_id == request.user.id:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:247:41")}, status=400)
    from .. import reprise_poste
    try:
        supprime = actions.executer_action("supprimer_employe", membre, membre.entreprise, user_id=user_id, admin_membre=membre)
    except reprise_poste.ReprisePosteErreur as e:
        return JsonResponse({'erreur': str(e)}, status=400)
    return JsonResponse({'ok': True, 'supprime': supprime})


@login_required
@require_http_methods(['GET'])
def api_reprises_poste(request):
    """Postes d'agents retirés dont l'administrateur détient les ressources, et agents auxquels les céder."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    if not _a_pouvoir(membre, "reprises_poste"):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:333:40")}, status=403)
    from ..models import ReprisePoste
    reprises = ReprisePoste.objects.filter(entreprise=membre.entreprise, statut='en_attente')
    agents = MembreEntreprise.objects.select_related('utilisateur').filter(entreprise=membre.entreprise, role='agent')
    return JsonResponse({
        'reprises': [{
            'id': r.id, 'nom_agent': r.nom_agent, 'identifiant': r.identifiant_ancien, 'nb_lignes': r.nb_lignes,
            'solde_verse': r.solde_verse, 'cree_le': r.cree_le.isoformat(),
            'nb_elements': sum(len(v) for v in (r.elements or {}).values()),
        } for r in reprises],
        'agents': [{'id': a.utilisateur_id, 'nom': (f"{a.utilisateur.first_name} {a.utilisateur.last_name}".strip() or a.utilisateur.username),
                    'username': a.utilisateur.username} for a in agents],
    })


@login_required
@require_http_methods(['POST'])
def api_ceder_poste(request, reprise_id):
    """Cède en un clic au nouvel agent tout ce que l'administrateur a repris d'un agent retiré."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    if not _a_pouvoir(membre, "reprises_poste"):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:357:40")}, status=403)
    from .. import reprise_poste
    from ..models import ReprisePoste
    reprise = ReprisePoste.objects.filter(id=reprise_id, entreprise=membre.entreprise).first()
    if reprise is None:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:363:39")}, status=404)
    try:
        corps = json.loads(request.body or '{}')
        n = reprise_poste.ceder_poste(reprise, int(corps.get('user_id')), membre)
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:367:40")}, status=400)
    except reprise_poste.ReprisePosteErreur as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    return JsonResponse({'ok': True, 'lignes_cedees': n})


@login_required
@require_http_methods(['POST'])
def api_modifier_role_employe(request, user_id):
    """Modifie le role d'un employe."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=request.user
    ).first()
    try:
        actions.verifier_permission("modifier_role_employe", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:265:41")}, status=403)
    corps = json.loads(request.body or '{}')
    nouveau_role = corps.get('role', '')
    if nouveau_role not in ('admin', 'agent', 'lecteur'):
        return JsonResponse({'erreur': t("engine/views/_part_007.py:269:41")}, status=400)
    if nouveau_role == 'admin' and membre.role != 'admin' and not _delegation.a_liberte_absolue(membre):   # un agent délégué ne nomme pas d'administrateur
        return JsonResponse({"erreur": t("engine/views/_part_007.py:265:41")}, status=403)
    actions.executer_action(
        "modifier_role_employe", membre, membre.entreprise,
        user_id=user_id, nouveau_role=nouveau_role,
    )
    return JsonResponse({'ok': True, 'role': nouveau_role})


@login_required
@require_http_methods(["POST"])
def api_modifier_employe(request, user_id):
    """Modifie toutes les informations d'un employé (admin uniquement)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre_admin = MembreEntreprise.objects.select_related("entreprise").filter(
        utilisateur=request.user
    ).first()
    try:
        actions.verifier_permission("modifier_employe", membre_admin)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:290:41")}, status=403)

    User = get_user_model()
    try:
        cible = User.objects.get(id=user_id)
        membre_cible = MembreEntreprise.objects.get(
            entreprise=membre_admin.entreprise, utilisateur=cible
        )
    except (User.DoesNotExist, MembreEntreprise.DoesNotExist):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:299:41")}, status=404)

    corps = json.loads(request.body or "{}")

    # Vérifier l'unicité du username (sauf si c'est le même) avant mutation
    if "username" in corps and corps["username"].strip():
        nouveau_username = corps["username"].strip()
        if User.objects.filter(username=nouveau_username).exclude(id=cible.id).exists():
            return JsonResponse({"erreur": tf("engine/views/_part_007.py:g:428:44", v1=nouveau_username)}, status=400)

    membre_cible = actions.executer_action("modifier_employe", membre_admin, membre_cible, corps=corps)

    return JsonResponse({
        "ok": True,
        "employe": {
            "id": membre_cible.utilisateur.id,
            "username": membre_cible.utilisateur.username,
            "first_name": membre_cible.utilisateur.first_name,
            "last_name": membre_cible.utilisateur.last_name,
            "role": membre_cible.role,
            "telephone": membre_cible.telephone,
            "indicatif_pays": membre_cible.indicatif_pays,
        }
    })


@login_required
@require_http_methods(["POST"])
def api_inscription_ouverte(request):
    """Toggle l'inscription publique de l'entreprise (admin uniquement)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related("entreprise").filter(
        utilisateur=request.user
    ).first()
    corps = json.loads(request.body or "{}")
    ouverte = bool(corps.get("inscription_ouverte", False))
    try:
        entreprise = actions.executer_action("modifier_inscription_ouverte", membre, membre.entreprise if membre else None, ouverte=ouverte)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:340:41")}, status=403)
    lien = f"/inscription/?rejoindre={entreprise.slug}"
    return JsonResponse({"ok": True, "inscription_ouverte": ouverte, "lien": lien})


@login_required
@require_http_methods(["POST"])
def api_api_ouverte(request):
    """Ouvre / ferme l'accès API externe (clés Bearer) de l'entreprise (admin uniquement)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related("entreprise").filter(
        utilisateur=request.user
    ).first()
    corps = json.loads(request.body or "{}")
    ouverte = bool(corps.get("api_ouverte", False))
    try:
        actions.executer_action("modifier_api_ouverte", membre, membre.entreprise if membre else None, ouverte=ouverte)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:340:41")}, status=403)
    return JsonResponse({"ok": True, "api_ouverte": ouverte})


@login_required
@require_http_methods(["POST"])
def api_toggle_expose_client(request, table_id):
    """Active/désactive l'exposition d'une table aux clients (lecteurs)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:355:41")}, status=403)
    try:
        table = TableDynamique.objects.get(id=table_id, entreprise=entreprise)
    except TableDynamique.DoesNotExist:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:359:41")}, status=404)
    corps = json.loads(request.body or "{}")
    table.expose_client = bool(corps.get("expose_client", not table.expose_client))
    table.save(update_fields=["expose_client"])
    return JsonResponse({"ok": True, "expose_client": table.expose_client})


@login_required
@require_http_methods(["POST"])
def api_toggle_table_produits(request, table_id):
    """Bascule une table agents/clients existante en table de PRODUITS (et inversement), sans rien supprimer : matricule, QR, cartes,
    lignes et valeurs restent ; seul l'identifiant de connexion (code d'accès client) est ignoré tant que la table est « produits »
    (les codes déjà générés sont conservés et redeviennent actifs au retour en table clients)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:355:41")}, status=403)
    table = TableDynamique.objects.filter(id=table_id, entreprise=entreprise).first()
    if table is None:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:359:41")}, status=404)
    if not table.est_table_membres:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:g:table_produits_membres")}, status=400)
    refus = _interdit_table_non_exposee(request, table)   # un agent restreint ne touche qu'aux tables qui lui sont permises
    if refus:
        return refus
    corps = json.loads(request.body or "{}")
    table.est_table_produits = bool(corps.get("produits", not table.est_table_produits))
    table.save(update_fields=["est_table_produits"])
    return JsonResponse({"ok": True, "est_table_produits": table.est_table_produits})


@login_required
@require_http_methods(["GET"])
def api_statut_denaturee(request, table_id):
    """Lecture seule : plus aucun toggle manuel (voir TableDynamique.denaturee /
    recalculer_denaturee) — le statut 'dénaturée' est entièrement dérivé des
    fusions de cellules existantes et recalculé automatiquement par
    api_fusions_table / api_fusion_detail. Conservé pour un éventuel affichage
    d'état côté client."""
    entreprise = _entreprise_courante(request)
    try:
        table = TableDynamique.objects.get(id=table_id, entreprise=entreprise)
    except TableDynamique.DoesNotExist:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:378:41")}, status=404)
    return JsonResponse({"denaturee": table.denaturee})


@login_required
@require_http_methods(["POST"])
def api_toggle_masquer_formulaire_client(request, table_id):
    """Active/désactive le bloc "recherche" côté client (engine/client.html) :
    recherche dans les données déjà enregistrées pour cette table. Bascule
    totalement indépendante de expose_client (formulaire) — les deux se
    combinent librement : formulaire seul, recherche seule, les deux, ou
    aucun des deux (la table devient alors inaccessible côté client, voir
    TableDynamique.accessible_client)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:397:41")}, status=403)
    try:
        table = TableDynamique.objects.get(id=table_id, entreprise=entreprise)
    except TableDynamique.DoesNotExist:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:401:41")}, status=404)
    corps = json.loads(request.body or "{}")
    table.masquer_formulaire_client = bool(
        corps.get("masquer_formulaire_client", not table.masquer_formulaire_client)
    )
    table.save(update_fields=["masquer_formulaire_client"])
    return JsonResponse({"ok": True, "masquer_formulaire_client": table.masquer_formulaire_client})


@login_required
@require_http_methods(["POST"])
def api_upload_logo(request):
    """Upload le logo de l'entreprise."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related("entreprise"))
    fichier = request.FILES.get("logo")
    if not fichier:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:420:41")}, status=400)
    import os
    ext = os.path.splitext(fichier.name)[1].lower()
    if ext not in EXTENSIONS_IMAGES:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:424:41")}, status=400)
    try:
        entreprise = actions.executer_action("uploader_logo", membre, membre.entreprise if membre else None, fichier=fichier)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:428:41")}, status=403)
    return JsonResponse({"ok": True, "logo_url": request.build_absolute_uri(entreprise.logo.url)})


@login_required
@require_http_methods(["GET"])
def api_entreprise(request):
    """Retourne les informations publiques de l'entreprise de l'utilisateur connecté."""
    membre = MembreEntreprise.objects.select_related("entreprise").filter(
        utilisateur=request.user
    ).first()
    if not membre:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:440:41")}, status=404)
    e = membre.entreprise
    logo_url = None
    try:
        if e.logo:
            logo_url = request.build_absolute_uri(e.logo.url)
    except Exception:
        pass
    return JsonResponse({
        "nom": e.nom,
        "slug": e.slug,
        "inscription_ouverte": e.inscription_ouverte,
        "api_ouverte": e.api_ouverte,
        "lien_inscription": f"/inscription/?rejoindre={e.slug}",
        "role": membre.role,
        "logo_url": logo_url,
        "adresse": e.adresse,
        "telephone_service_client": e.telephone_service_client,
        "email_contact": e.email_contact,
        "site_web": e.site_web,
    })


@require_http_methods(["GET"])
@limiter("entreprise_publique", max_appels=30, periode_secondes=60)
def api_entreprise_publique(request):
    """Retourne les infos publiques d'une entreprise avec inscription ouverte (sans auth).
    Paramètre optionnel ?slug=<slug>. Sans slug, retourne la première entreprise publique trouvée.
    """
    slug = request.GET.get("slug", "")
    try:
        if slug:
            e = Entreprise.objects.get(slug=slug, inscription_ouverte=True)
        else:
            from django.conf import settings as _reglages
            # Multi-entreprises : sans slug, jamais « une entreprise au hasard » (ce serait en désigner une parmi les autres).
            e = None if getattr(_reglages, "GYPLATFORM_MULTI_ENTREPRISE", False) else Entreprise.objects.filter(inscription_ouverte=True).first()
            if not e:
                return JsonResponse({"erreur": t("engine/views/_part_007.py:475:49")}, status=404)
    except Entreprise.DoesNotExist:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:477:41")}, status=404)
    logo_url = None
    try:
        if e.logo: logo_url = request.build_absolute_uri(e.logo.url)
    except Exception: pass
    return JsonResponse({
        "nom": e.nom,
        "slug": e.slug,
        "logo_url": logo_url,
        "lien_inscription": f"/inscription/?rejoindre={e.slug}",
    })


@require_http_methods(["GET"])
@limiter("entreprise_publique_liste", max_appels=30, periode_secondes=60)
def api_entreprise_publique_liste(request):
    """Liste toutes les entreprises avec inscription ouverte (sans auth)."""
    from django.urls import reverse
    entreprises = Entreprise.objects.filter(inscription_ouverte=True)
    result = []
    for e in entreprises:
        logo_url = None
        qr_url = None
        try:
            if e.logo: logo_url = request.build_absolute_uri(e.logo.url)
        except Exception: pass
        try:
            qr_url = request.build_absolute_uri(f"/inscription/qrcode/?slug={e.slug}")
        except Exception: pass
        result.append({
            "nom": e.nom,
            "slug": e.slug,
            "logo_url": logo_url,
            "lien_client": f"/client/{e.slug}/",
            "lien_connexion": f"/login/?e={e.slug}",
        })
    # « courante » : entreprise déjà choisie dans cette session (sélecteur de la page de connexion) ; vide = thème par défaut.
    return JsonResponse({"entreprises": result, "courante": str(request.session.get("entreprise_publique") or "")})


_LIMITE_LISTE_CONNEXION = 30   # une page de résultats ; au-delà, la recherche s'affine (« plus » = True)


@require_http_methods(["GET"])
@limiter("entreprise_connexion_liste", max_appels=90, periode_secondes=60)
def api_entreprise_connexion_liste(request):
    """Étape 1 de la connexion (sans auth) : les entreprises à choisir, filtrées par ?q= (nom ou identifiant).

    Chaque entrée porte de quoi la reconnaître d'un coup d'œil : nom, logo et couleurs de son thème (accent + fond).
    Thème désactivé par l'entreprise : identité d'origine (couleurs par défaut, pas de logo), comme sur le reste de l'application.
    Les saisons restaurées (entreprises « sœurs », lecture seule) ne se choisissent pas ici."""
    import re as _re
    from django.db.models import Q as _Q
    from .. import marque as _marque
    q = (request.GET.get("q") or "").strip()[:100]
    qs = Entreprise.objects.filter(saison_de__isnull=True)
    if q:
        qs = qs.filter(_Q(nom__icontains=q) | _Q(slug__icontains=q))
    qs = qs.order_by("nom")
    trouvees = list(qs[:_LIMITE_LISTE_CONNEXION + 1])
    defauts = dict(_marque._DEFAUTS)
    hexa = _re.compile(r"^#[0-9a-fA-F]{6}$")

    def _couleur(c, cle):
        v = str(c.get(cle) or "")[:7]
        return v if hexa.match(v) else defauts[cle]

    result = []
    for e in trouvees[:_LIMITE_LISTE_CONNEXION]:
        theme = _marque.actif(e)
        c = _marque._couleurs(e) if theme else {}
        accent, fond = _couleur(c, "accent"), _couleur(c, "bg")
        logo_url = None
        try:
            if theme and e.logo:
                logo_url = e.logo.url
        except Exception:
            pass
        result.append({
            "nom": _marque.nom_marque(e) or e.slug,
            "slug": e.slug,
            "logo_url": logo_url,
            "couleur": accent,
            "fond": fond,
            "texte_couleur": _marque._texte_sur(accent),
            "lien_connexion": f"/login/?e={e.slug}",
        })
    return JsonResponse({
        "entreprises": result,
        "plus": len(trouvees) > _LIMITE_LISTE_CONNEXION,
        "courante": str(request.session.get("entreprise_publique") or ""),
    })


@require_http_methods(["GET"])
def vue_inscription_qrcode(request):
    """Retourne un PNG du QR code pointant vers le lien d'inscription d'une entreprise."""
    import io, base64
    try:
        import qrcode
    except ImportError:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:522:41")}, status=500)

    slug = request.GET.get("slug", "")
    if not slug:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:526:41")}, status=400)

    try:
        entreprise = Entreprise.objects.get(slug=slug)
    except Entreprise.DoesNotExist:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:531:41")}, status=404)

    lien = request.build_absolute_uri(f"/client/{slug}/")
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(lien)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#e8a33d", back_color="#0d0f12")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    from django.http import HttpResponse
    return HttpResponse(buf.read(), content_type="image/png")


# ── LIMITE DE LIGNES (reglage global, panneau lateral) ───────────────────────
# Remplace l'ancien reglage disperse par table (barre d'outils de chaque
# table + champ dans la modal d'import de fichier) : un seul controle,
# au-dessus des boutons "+ nouvelle table" / "+ importer un fichier" /
# "Configuration IA", qui s'applique immediatement a TOUTE table de
# l'entreprise (native ou importee) — voir TableDynamique.plafond_lignes.

@login_required
@require_http_methods(["GET"])
def api_limite_lignes_lire(request):
    """Retourne le plafond de lignes global actuel de l'entreprise."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    return JsonResponse({"limite_lignes": cfg.limite_lignes})


@login_required
@require_http_methods(["POST"])
def api_limite_lignes_sauvegarder(request):
    """Modifie le plafond de lignes global de l'entreprise : impacte
    immediatement TOUTE table, native ou importee (aucune valeur par table)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = request.user.membreentreprise_set.filter(entreprise=entreprise).first()

    corps = json.loads(request.body or "{}")
    brut = corps.get("limite_lignes")
    try:
        limite = int(brut)
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:580:41")}, status=400)
    if limite < 0:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:582:41")}, status=400)

    try:
        cfg = actions.executer_action("modifier_limite_lignes", membre, entreprise, limite=limite)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:587:41")}, status=403)
    return JsonResponse({"ok": True, "limite_lignes": cfg.limite_lignes})


@login_required
@require_http_methods(["GET"])
def api_limite_colonnes_lire(request):
    """Retourne le plafond de colonnes global actuel de l'entreprise."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    return JsonResponse({"limite_colonnes": cfg.limite_colonnes})


@login_required
@require_http_methods(["POST"])
def api_limite_colonnes_sauvegarder(request):
    """Modifie le plafond de colonnes global de l'entreprise : impacte
    immédiatement TOUTE table, native ou importée. Réservé à l'admin."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = request.user.membreentreprise_set.filter(entreprise=entreprise).first()

    corps = json.loads(request.body or "{}")
    brut = corps.get("limite_colonnes")
    try:
        limite = int(brut)
    except (TypeError, ValueError):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:619:41")}, status=400)
    if limite < 0:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:621:41")}, status=400)

    try:
        cfg = actions.executer_action("modifier_limite_colonnes", membre, entreprise, limite=limite)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:626:41")}, status=403)
    return JsonResponse({"ok": True, "limite_colonnes": cfg.limite_colonnes})


# ── CONFIGURATION IA ─────────────────────────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def api_config_ia_lire(request):
    """Retourne la configuration IA de l'entreprise (ou les valeurs par défaut)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    from ..executor import modules_modele_ia_a_jour
    from ..inference import (exemple_a_jour, exemple_modele_ia_a_jour, regle_modele_ia_a_jour, contraintes_modele_ia_a_jour,
                             EXEMPLE_SCRIPT_PYTHON, EXEMPLE_SCRIPT_DOCUMENT, EXEMPLE_TACHE, EXEMPLE_GRAPHIQUE_BI_MULTIPLES)
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)

    return JsonResponse({
        "system_prompt":        cfg.system_prompt,
        "role_analysis_script": cfg.role_analysis_script,
        "role_skill_script":    cfg.role_skill_script,
        "role_report_summary":  cfg.role_report_summary,
        "role_other":           cfg.role_other,
        "role_document":        cfg.role_document,
        "regle_script":         cfg.regle_script,
        "regle_document":       cfg.regle_document,
        "contraintes_syntaxe":  cfg.contraintes_syntaxe,
        "contraintes_document": cfg.contraintes_document,
        "script_exemple":          exemple_a_jour("script_exemple", cfg.script_exemple, EXEMPLE_SCRIPT_PYTHON),
        "script_exemple_document": exemple_a_jour("script_exemple_document", cfg.script_exemple_document, EXEMPLE_SCRIPT_DOCUMENT),
        "modules_autorises_script":   cfg.modules_autorises_script,
        "modules_autorises_document": cfg.modules_autorises_document,
        # ── Canal formule (tables de calcul) ──
        "role_formule":        cfg.role_formule,
        "regle_formule":       cfg.regle_formule,
        "contraintes_formule": cfg.contraintes_formule,
        "script_exemple_formule_colonne": cfg.script_exemple_formule_colonne,
        "script_exemple_formule_table":   cfg.script_exemple_formule_table,
        "modules_autorises_formule": cfg.modules_autorises_formule,
        # ── Canal graphique BI (tableaux de bord) ──
        "role_graphique_bi":        cfg.role_graphique_bi,
        "regle_graphique_bi":       cfg.regle_graphique_bi,
        "contraintes_graphique_bi": cfg.contraintes_graphique_bi,
        "script_exemple_graphique_bi_colonne":   cfg.script_exemple_graphique_bi_colonne,
        "script_exemple_graphique_bi_multiples": exemple_a_jour("script_exemple_graphique_bi_multiples", cfg.script_exemple_graphique_bi_multiples, EXEMPLE_GRAPHIQUE_BI_MULTIPLES),
        "modules_autorises_graphique_bi": cfg.modules_autorises_graphique_bi,
        # ── Canal couche d'ontologie (graphe d'entourage) ──
        "role_couche_ontologie":        cfg.role_couche_ontologie,
        "regle_couche_ontologie":       cfg.regle_couche_ontologie,
        "contraintes_couche_ontologie": cfg.contraintes_couche_ontologie,
        "script_exemple_couche_ontologie": cfg.script_exemple_couche_ontologie,
        "modules_autorises_couche_ontologie": cfg.modules_autorises_couche_ontologie,
        "role_modele_ia":               cfg.role_modele_ia,
        "regle_modele_ia":              regle_modele_ia_a_jour(cfg.regle_modele_ia),
        "contraintes_modele_ia":        contraintes_modele_ia_a_jour(cfg.contraintes_modele_ia),
        "script_exemple_modele_ia":     exemple_modele_ia_a_jour(cfg.script_exemple_modele_ia),
        "modules_autorises_modele_ia":  modules_modele_ia_a_jour(cfg.modules_autorises_modele_ia),
        # ── Canal tâches (scripts de tâches planifiées / conditionnelles) ──
        "role_tache":               cfg.role_tache,
        "regle_tache":              cfg.regle_tache,
        "contraintes_tache":        cfg.contraintes_tache,
        "script_exemple_tache":     exemple_a_jour("script_exemple_tache", cfg.script_exemple_tache, EXEMPLE_TACHE),
        "modules_autorises_tache":  cfg.modules_autorises_tache,
        # ── Moteur d'inférence (local / externe) ──
        "moteur_type":         cfg.moteur_type,
        "moteur_host":         cfg.moteur_host,
        "moteur_port":         cfg.moteur_port,
        "moteur_url_externe":  cfg.moteur_url_externe,
        "moteur_modele":       cfg.moteur_modele,
        "moteur_cle_api_definie": bool(cfg.moteur_cle_api),  # jamais la clé en clair
    })


@login_required
@require_http_methods(["POST"])
def api_config_ia_sauvegarder(request):
    """Sauvegarde la configuration IA (tous les champs editables)."""
    _bloque_lecteur = _interdit_non_admin(request, "config_ia")
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    membre = request.user.membreentreprise_set.filter(entreprise=entreprise).first()
    corps = json.loads(request.body or "{}")
    try:
        actions.executer_action("modifier_config_ia", membre, entreprise, corps=corps)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:706:41")}, status=403)
    return JsonResponse({"ok": True})


# ══════════════════════════════════════════════════════════════
#  IMAGES DU DOCUMENT — signature(s)
#  Uploads multipart séparés du JSON ci-dessus (comme api_upload_logo).
#  Les images à emplacement fixe (cachet) sont gérées exclusivement par
#  api_configuration_documents_image (page /configuration-documents/) —
#  voir _CHAMPS_IMAGE_CONFIG_DOC_CONTEXTE plus bas.
# ══════════════════════════════════════════════════════════════
def _admin_requis(request, entreprise, droit):
    """403 sauf pour l'administrateur ou un agent à qui le `droit` de délégation demandé a été confié (delegation.py)."""
    membre = request.user.membreentreprise_set.filter(entreprise=entreprise).first()
    if not _a_pouvoir(membre, droit):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:720:41")}, status=403)
    return None


def _signatures_visibles(entreprise, agent):
    """Administrateur : toutes les signatures. Agent : celles de l'entreprise (lecture seule) et les siennes."""
    qs = SignatureDocument.objects.filter(entreprise=entreprise).select_related("table", "document", "demande")
    if agent is not None:
        qs = qs.filter(Q(membre__isnull=True) | Q(membre=agent))
    return qs


def _agent_document(request, entreprise):
    """Le MembreEntreprise si l'utilisateur est un AGENT (il personnalise ses propres documents : signatures, cachet),
    sinon None (l'administrateur règle ce qui s'applique à toute l'entreprise)."""
    m = request.user.membreentreprise_set.filter(entreprise=entreprise).first()
    return m if m is not None and m.role == "agent" else None


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
def _contexte_valide(contexte):
    return contexte in dict(ConfigurationDocumentContexte.CONTEXTE_CHOICES)


@login_required
def vue_configuration_documents(request):
    """Page de configuration de la mise en page & du branding des documents,
    par contexte (PDF de table, carte de document IA, reçus/système).
    Accessible aux admins ET aux agents (un agent peut gérer les réglages
    propres à ses tables) — seule la sauvegarde des réglages GLOBAUX de
    branding (couleurs, logo, nom affiché — communs à tous les documents)
    reste réservée aux admins, contrôlé côté API (voir
    api_configuration_documents_sauvegarder)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    _bloque_vue = _interdit_vue(request, "configuration_documents")
    if _bloque_vue:
        return _bloque_vue
    membre = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=request.user
    ).first()
    if not membre:
        return redirect('/profile/')

    return render(request, 'engine/configuration_documents.html', {
        'contextes': ConfigurationDocumentContexte.CONTEXTE_CHOICES,
        'entreprise': membre.entreprise,
        'est_admin': membre.role == 'admin',
    })


@login_required
def api_configuration_documents_lire(request, contexte):
    """Retourne la configuration document fusionnée (globale + propre au
    contexte demandé), ainsi que les listes utiles aux sélecteurs du front."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    if not _contexte_valide(contexte):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:774:41")}, status=404)

    entreprise = _entreprise_courante(request)
    cfg_ia, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    sous_config, _ = ConfigurationDocumentContexte.objects.get_or_create(
        entreprise=entreprise, contexte=contexte,
    )

    champs_statut_disponibles = [
        {"slug": c.slug, "label": f"{c.table.nom} → {c.nom}"}
        for c in ChampDynamique.objects
            .filter(table__entreprise=entreprise, type_champ__in=ChampDynamique.TYPES_BOOLEEN)
            .select_related("table").order_by("table__nom", "ordre")
    ]

    tables_disponibles = [
        {
            "id": t.id, "nom": t.nom,
            "afficher_photo_identite_document": t.afficher_photo_identite_document,
            "photo_identite_url": request.build_absolute_uri(t.photo_identite_document.url) if t.photo_identite_document else None,
        }
        for t in TableDynamique.objects.filter(entreprise=entreprise).order_by("nom")
    ]

    agent = _agent_document(request, entreprise)
    perso = (PersonnalisationDocumentMembre.objects.filter(membre=agent, contexte=contexte).first()
             if agent is not None else None)

    def _eff(nom):   # valeur effective : celle de l'agent si renseignée, sinon celle de l'entreprise
        v = getattr(perso, nom, None) if perso is not None else None
        return getattr(sous_config, nom) if v in (None, "") else v

    def _img(nom):
        f = getattr(perso, nom, None) if perso is not None else None
        f = f or getattr(sous_config, nom)
        return request.build_absolute_uri(f.url) if f else None

    signatures = [
        {
            "id": s.id,
            "nom": s.nom,
            "fonction": s.fonction,
            "types_document": s.liste_types(),
            "actif": s.actif,
            "ordre": s.ordre,
            "table_id": s.table_id,
            "table_nom": s.table.nom if s.table_id else None,
            "document_id": s.document_id,
            "document_nom": (s.document.titre.strip() if s.document.titre else f"{s.document.get_type_document_display()} #{s.document.id}") if s.document_id else None,
            "demande_id": s.demande_id,
            "demande_nom": (s.demande.texte or "").strip()[:80] if s.demande_id else None,
            "image_url": request.build_absolute_uri(s.image.url) if s.image else None,
            "membre_id": s.membre_id,                                   # vide = signature de l'entreprise
            "modifiable": agent is None or s.membre_id == agent.id,     # l'agent ne modifie que les siennes
        }
        for s in _signatures_visibles(entreprise, agent)
    ]

    return JsonResponse({
        "contexte": contexte,
        "est_agent": agent is not None,   # l'agent ne personnalise que signatures et cachet ; le reste vient de l'entreprise
        "est_admin": (lambda m: bool(m) and m.role == "admin")(
            request.user.membreentreprise_set.filter(entreprise=entreprise).first()
        ),
        # ── Obligatoire sur tout document (ConfigurationIA) ──
        "nom_affichage_document":  cfg_ia.nom_affichage_document,
        "afficher_logo_document":  cfg_ia.afficher_logo_document,
        "position_logo_document":  cfg_ia.position_logo_document,
        "couleur_accent_document": cfg_ia.couleur_accent_document,
        "couleur_entete_document": cfg_ia.couleur_entete_document,
        "couleur_tableau_entete_document":   cfg_ia.couleur_tableau_entete_document,
        "couleur_lignes_alternees_document": cfg_ia.couleur_lignes_alternees_document,
        "couleur_texte_document":            cfg_ia.couleur_texte_document,
        "couleur_fond_document":      cfg_ia.couleur_fond_document,
        "couleur_corps_document":     cfg_ia.couleur_corps_document,
        "couleur_pied_page_document": cfg_ia.couleur_pied_page_document,
        "police_document":            cfg_ia.police_document,
        "texte_pied_page_document":   cfg_ia.texte_pied_page_document,
        "numeroter_pages_document":   cfg_ia.numeroter_pages_document,
        "nom_entreprise": entreprise.nom,
        "logo_url": request.build_absolute_uri(entreprise.logo.url) if entreprise.logo else None,
        # ── Propre au contexte (ConfigurationDocumentContexte) ──
        "afficher_entete_officiel":    sous_config.afficher_entete_officiel,
        "afficher_coordonnees_entete": sous_config.afficher_coordonnees_entete,
        "afficher_photo_entite": sous_config.afficher_photo_entite,
        "image_entite_url":      request.build_absolute_uri(sous_config.image_entite.url) if sous_config.image_entite else None,
        "position_photo_entite": sous_config.position_photo_entite,
        "sections_document":    sous_config.sections_document_actives(),
        "sections_disponibles": ConfigurationIA.SECTIONS_DEFAUT,
        "sections_libelles":    ConfigurationIA.SECTIONS_LIBELLES,
        "afficher_signatures_document": _eff("afficher_signatures_document"),
        "position_signatures_document": _eff("position_signatures_document"),
        "signatures": signatures,
        "afficher_cachet_document": _eff("afficher_cachet_document"),
        "cachet_texte_actif":       _eff("cachet_texte_actif"),
        "cachet_texte_inactif":     _eff("cachet_texte_inactif"),
        "cachet_image_actif_url":   _img("cachet_image_actif"),
        "cachet_image_inactif_url": _img("cachet_image_inactif"),
        "champ_statut_cachet":      sous_config.champ_statut_cachet,
        "position_cachet_document": _eff("position_cachet_document"),
        "champs_statut_disponibles": champs_statut_disponibles,
        "table_cachet_document_id": sous_config.table_cachet_document_id,
        "tables_disponibles": tables_disponibles,
        "type_document_cachet": sous_config.type_document_cachet,
        "types_document_disponibles": [
            {"slug": slug, "label": label} for slug, label in DocumentGenere.TYPE_CHOICES
        ],
        "document_cachet_id": sous_config.document_cachet_id,
        "demande_cachet_id": sous_config.demande_cachet_id,
        # Sélecteur "carte précise" UNIQUE côté front, combinant les deux
        # pipelines PDF mutuellement exclusifs (carte de document / carte
        # d'analyse) sous un préfixe ("doc-<id>" / "ana-<id>") — voir
        # api_configuration_documents_sauvegarder pour le sens inverse.
        "carte_precise_cachet": (
            f"doc-{sous_config.document_cachet_id}" if sous_config.document_cachet_id
            else (f"ana-{sous_config.demande_cachet_id}" if sous_config.demande_cachet_id else "")
        ),
        "documents_disponibles": [
            {
                "id": d.id,
                "label": d.titre.strip() if d.titre else f"{d.get_type_document_display()} #{d.id}",
                "date": d.cree_le.strftime("%d/%m/%Y"),
            }
            for d in DocumentGenere.objects
                .filter(entreprise=entreprise)
                .order_by("-cree_le")[:200]
        ] if contexte == "ia" else [],
        # Cartes d'analyse : elles aussi génèrent un PDF (generer_rapport_pdf,
        # canal analyse) et doivent donc pouvoir être ciblées comme une carte
        # précise, au même titre que les cartes de document.
        "demandes_disponibles": [
            {
                "id": d.id,
                "label": (d.texte or "").strip()[:80] or tf("engine/views/_part_007.py:g:1019:58", v1=d.id),
                "date": d.cree_le.strftime("%d/%m/%Y"),
            }
            for d in DemandeAgent.objects
                .filter(entreprise=entreprise)
                .order_by("-cree_le")[:200]
        ] if contexte == "ia" else [],
    })


@login_required
@require_http_methods(["GET", "POST"])
def api_structure_formulaire(request, table_id):
    """GET : structure du formulaire conditionnel de la table (sections + renvois) et champs saisissables.
    POST {actif, sections:[…]} : l'enregistre. La table elle-même (colonnes, lignes) n'est jamais modifiée."""
    from .. import formulaire_conditionnel as _fc
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"erreur": t("engine/views/_part_007.py:397:41")}, status=403)
    try:
        table = TableDynamique.objects.get(id=table_id, entreprise=entreprise)
    except TableDynamique.DoesNotExist:
        return JsonResponse({"erreur": t("engine/views/_part_007.py:401:41")}, status=404)
    champs = _fc.champs_saisis(table)
    if request.method == "POST":
        corps = json.loads(request.body or "{}")
        table.structure_formulaire = _fc.normaliser(corps, champs)
        table.save(update_fields=["structure_formulaire"])
    enregistree = table.structure_formulaire or {}
    if enregistree.get("sections"):
        structure = _fc.normaliser(enregistree, champs)   # colonnes ajoutées/supprimées depuis la dernière sauvegarde
        structure["actif"] = bool(enregistree.get("actif"))
    else:
        structure = _fc.structure_par_defaut(champs)
    structure["theme"] = _fc.normaliser_theme(enregistree.get("theme"))
    return JsonResponse({
        "structure": structure,
        "polices": list(_fc.POLICES),
        "champs": [{"slug": c.slug, "nom": c.nom, "type": c.type_champ, "obligatoire": c.obligatoire,
                    "valeurs": [{"valeur": v, "libelle": l} for v, l in _fc.valeurs_branchables(c)]} for c in champs],
    })


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
