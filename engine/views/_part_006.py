from engine.i18n import tf
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from ._part_003 import *  # noqa
from ._part_004 import *  # noqa
from ._part_005 import *  # noqa
from ..delegation import a_pouvoir as _a_pouvoir, a_un_pouvoir_de as _a_un_pouvoir_de, a_liberte_absolue as _a_liberte_absolue  # noqa: E402
from .. import delegation as _delegation  # noqa: E402


@login_required
@require_http_methods(["POST"])
def api_lancer(request, demande_id):
    """Bouton 'Lancer' : execute le script deja genere pour cette demande."""
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
def api_insister(request, demande_id):
    """Bouton 'Insister' : le script precedent a echoue (format non respecte ou
    erreur d'execution). Regenere depuis l'IA avec l'erreur exacte comme feedback.
    Le nouveau script est pret ; il faut re-cliquer 'Lancer' pour l'executer.
    """
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    demande.statut = "en_cours"
    demande.save(update_fields=["statut"])
    _taches.insister_sur_demande_tache.delay(demande.id)
    return JsonResponse({"demande": _serialiser_demande(demande)})


@login_required
@require_http_methods(["POST"])
def api_sauvegarder_script(request, demande_id):
    """Bouton 'Sauvegarder le script' : persiste le script genere en fichier .py
    sur le disque serveur (media/scripts_agent/<table>/<demande_id>.py).
    Retourne le chemin relatif depuis MEDIA_ROOT.
    """
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    try:
        chemin = sauvegarder_script_demande(demande)
    except ValueError as e:
        return JsonResponse({"erreur": str(e)}, status=400)

    # Chemin relatif depuis MEDIA_ROOT pour l'afficher cote client
    try:
        chemin_relatif = os.path.relpath(chemin, dj_settings.MEDIA_ROOT)
    except ValueError:
        chemin_relatif = chemin

    return JsonResponse({"chemin": chemin_relatif, "chemin_absolu": chemin})


@login_required
def api_demandes(request):
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    demandes = DemandeAgent.objects.filter(entreprise=entreprise).order_by("-cree_le")[:50]
    return JsonResponse({"demandes": [_serialiser_demande(d) for d in demandes]})


@login_required
@require_http_methods(["POST"])
@_pdf_erreur_json
def api_generer_pdf(request, demande_id):
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    demande = get_object_or_404(DemandeAgent, id=demande_id, entreprise=entreprise)
    rapport = generer_pdf_demande(demande)
    if rapport is None:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:90:41")}, status=400)
    return JsonResponse({"url": rapport.fichier.url})


# ── SKILLS ────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def api_skills_table(request, table_id):
    """Liste tous les skills d'une table."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    skills = Skill.objects.filter(entreprise=entreprise, table=table).order_by("-cree_le")
    return JsonResponse({"skills": [serialiser_skill(s) for s in skills]})


@login_required
@require_http_methods(["POST"])
def api_generer_skill(request, table_id):
    """
    Déclenche la génération d'un skill pour une table :
    l'IA génère le script structuré, qui est sauvegardé sur disque.
    Le script n'est PAS encore exécuté (bouton 'Analyser' déclenche les deux).
    """
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    corps = json.loads(request.body or "{}")
    nom = corps.get("nom", "").strip()
    description = corps.get("description", "").strip()

    skill = generer_skill(table=table, entreprise=entreprise, utilisateur=request.user, nom=nom, description=description)

    # Exécution immédiate si la génération a réussi
    if skill.statut == "genere":
        skill = executer_skill(skill)

    return JsonResponse({"skill": serialiser_skill(skill)}, status=201)


@login_required
@require_http_methods(["POST"])
def api_executer_skill(request, skill_id):
    """Ré-exécute le script d'un skill (les données ont changé)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    skill = get_object_or_404(Skill, id=skill_id, entreprise=entreprise)
    skill = executer_skill(skill)
    return JsonResponse({"skill": serialiser_skill(skill)})


@login_required
@require_http_methods(["POST"])
def api_regenerer_skill(request, skill_id):
    """Supprime et régénère le script depuis l'IA (modification de l'analyse)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    skill = get_object_or_404(Skill, id=skill_id, entreprise=entreprise)
    skill = regenerer_skill(skill)
    if skill.statut == "genere":
        skill = executer_skill(skill)
    return JsonResponse({"skill": serialiser_skill(skill)})


@login_required
@require_http_methods(["POST"])
def api_supprimer_skill(request, skill_id):
    """Supprime un skill et son fichier sur disque."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    import os
    entreprise = _entreprise_courante(request)
    skill = get_object_or_404(Skill, id=skill_id, entreprise=entreprise)
    # Nettoyer les fichiers
    for chemin in [skill.fichier_script, skill.fichier_csv_table]:
        if chemin and os.path.exists(chemin):
            try:
                os.remove(chemin)
            except OSError:
                pass
    skill.delete()
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
@_pdf_erreur_json
def api_generer_pdf_skill(request, skill_id):
    """Génère un PDF du rapport skill (structure complète : titre, pourquoi, etc.)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    from ..reports import generer_rapport_pdf_skill
    entreprise = _entreprise_courante(request)
    skill = get_object_or_404(Skill, id=skill_id, entreprise=entreprise)
    if not skill.resultat_json:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:204:41")}, status=400)
    rapport = generer_rapport_pdf_skill(skill)
    return JsonResponse({"url": rapport.fichier.url})


# ── PHOTO DE PROFIL ────────────────────────────────────────────────────────

@login_required
@require_http_methods(["POST"])
def api_upload_photo_profil(request):
    """Upload la photo de profil de l'utilisateur connecté."""
    membre = membre_de_requete(request, MembreEntreprise.objects)
    if not membre:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:217:41")}, status=404)

    photo = request.FILES.get("photo")
    if not photo:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:221:41")}, status=400)

    # Vérification du type
    types_autorises = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    if photo.content_type not in types_autorises:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:226:41")}, status=400)

    # Aucune limite de taille : seul l'espace disque borne l'envoi.

    # Supprimer l'ancienne photo si présente
    if membre.photo_profil:
        try:
            membre.photo_profil.delete(save=False)
        except Exception:
            pass

    membre.photo_profil = photo
    membre.save(update_fields=["photo_profil"])

    return JsonResponse({"url": membre.photo_profil.url})


# ── serialisation ──
def _url_image(champ):
    """URL relative d'un champ ImageField, ou None si vide. Fonction globale
    (utilisée par _serialiser_table et par la config IA — chacun n'a pas
    forcément accès à `request` à cet endroit du code)."""
    return champ.url if champ else None


def _lot_serialisation_tables(tables):
    """Valeurs que _serialiser_table calculerait par une requête SÉPARÉE pour chaque table (nombre de lignes, nombre de lignes du
    groupe, « dernière du groupe »), obtenues ici en 3 requêtes pour toute la liste. À passer en 2e argument de
    _serialiser_table quand on sérialise plusieurs tables d'un coup (api_tables). Résultats identiques aux propriétés du modèle."""
    from django.db.models import Count
    from ..models import Enregistrement, TableDynamique
    entreprises = {t.entreprise_id for t in tables}
    nb_lignes = dict(
        Enregistrement.objects.filter(table__entreprise_id__in=entreprises)
        .order_by().values("table_id").annotate(n=Count("id")).values_list("table_id", "n")
    )
    nb_groupe = dict(   # même périmètre que TableDynamique.enregistrements_groupe : parties du groupe, hors tables dénaturées
        Enregistrement.objects.filter(table__entreprise_id__in=entreprises, table__groupe__isnull=False, table__denaturee=False)
        .order_by().values("table__groupe_id").annotate(n=Count("id")).values_list("table__groupe_id", "n")
    )
    derniere, cle_min = {}, {}   # même ordre que est_derniere_du_groupe : partie_index décroissant (NULL en dernier), puis id décroissant
    for gid, tid, idx in TableDynamique.objects.filter(entreprise_id__in=entreprises, groupe__isnull=False).values_list("groupe_id", "id", "partie_index"):
        k = (idx is None, -(idx or 0), -tid)
        if gid not in cle_min or k < cle_min[gid]:
            cle_min[gid], derniere[gid] = k, tid
    return {"nb_lignes": nb_lignes, "nb_groupe": nb_groupe, "derniere": derniere}


def _serialiser_table(table, lot=None):
    """`lot` (optionnel) : résultat de _lot_serialisation_tables(tables), pour éviter 2 à 3 requêtes par table en liste."""
    if lot is None:
        est_derniere = table.est_derniere_du_groupe
        nombre_lignes = table.enregistrements.count()
        nombre_lignes_groupe = table.enregistrements_groupe.count() if table.groupe_id else None
    else:
        est_derniere = (not table.groupe_id) or lot["derniere"].get(table.groupe_id, table.pk) == table.pk
        nombre_lignes = lot["nb_lignes"].get(table.id, 0)
        nombre_lignes_groupe = lot["nb_groupe"].get(table.groupe_id, 0) if table.groupe_id else None
    return {
        "id": table.id, "nom": table.nom, "slug": table.slug,
        "type_stockage": table.type_stockage,
        "description": table.description,
        "taille_echantillon": table.taille_echantillon,
        # Reglage global desormais (panneau lateral) — plafond_lignes reste
        # ici a titre informatif (badge "0 / plafond" dans la vue Donnees).
        "plafond_lignes": table.plafond_lignes,
        "prompt_rapport": table.prompt_rapport,
        "prompt_rapport_document": table.prompt_rapport_document,
        "timeout_execution_secondes": table.timeout_execution_secondes,
        "timeout_inference_secondes": table.timeout_inference_secondes,
        "provient_fichier": table.provient_fichier,
        "nom_fichier_origine": table.nom_fichier_origine, "url_source": table.url_source, "source_securisee": bool(table.jeton_source), "jeton_a_reconfigurer": table.jeton_a_reconfigurer, "intervalle_source": table.intervalle_source,
        "connexion_externe_origine_id": table.connexion_externe_origine_id,
        "nom_source_distante": table.nom_source_distante,
        # Rattachement à un ensemble logique de tables (découpage
        # automatique d'un import trop volumineux, ou rattachement manuel
        # de tables indépendantes) — voir GroupeTables dans models.py.
        "groupe_id": table.groupe_id,
        "groupe_nom": table.groupe.nom if table.groupe_id else None,
        "partie_index": table.partie_index,
        "partie_total": table.partie_total,
        "partie_precedente_id": table.partie_precedente_id,
        "partie_suivante_id": getattr(getattr(table, "partie_suivante", None), "id", None) if table.groupe_id else None,   # relation inverse OneToOne : lève RelatedObjectDoesNotExist (sous-classe d'AttributeError) sur la DERNIÈRE partie d'un groupe — getattr(table, ..., None) la convertit en None
        # Fusion d'un groupe (groupe_fusion.py) : cette table est le résultat de la fusion du groupe `groupe_fusionne_id`.
        "groupe_fusionne_id": table.groupe_fusionne_id,
        "peut_ajouter_lignes": est_derniere and not table.source_systeme,
        "systeme": bool(table.source_systeme), "lecture_seule": bool(table.source_systeme),   # table système : lecture seule (engine/tables_systeme.py)
        "est_table_membres": table.est_table_membres,
        "est_table_produits": table.est_table_produits,
        # Plus de code unique partage par table (faille : tous les clients
        # avaient le meme code). Chaque ligne/client a desormais son propre
        # code (Enregistrement.code_acces_client) — on ne renvoie ici que le
        # nombre de clients deja codes, a titre informatif pour l'agent.
        "nb_clients_codes": (
            table.enregistrements_groupe.exclude(code_acces_client="").count()
            if table.a_identifiants_connexion else 0
        ),
        "theme_carte_recto_url": table.theme_carte_recto.url if table.theme_carte_recto else None,
        "theme_carte_verso_url": table.theme_carte_verso.url if table.theme_carte_verso else None,
        "carte_signature_url": table.carte_signature_image.url if table.carte_signature_image else None,
        "carte_signature_nom": table.carte_signature_nom,
        "carte_cachet_url": table.carte_cachet_image.url if table.carte_cachet_image else None,
        "carte_qrcode_url": table.carte_qrcode_image.url if table.carte_qrcode_image else None,
        "matricule_prefixe": table.matricule_prefixe,
        "config_carte": table.config_carte,
        "champs": [
            {
                "id": c.id, "nom": c.nom, "slug": c.slug, "type": c.type_champ, "obligatoire": c.obligatoire,
                "taille_max_mo": c.taille_max_mo,
                "options": c.options_choix,
                "couleur": c.couleur or "",
                "formule_expression": c.formule_expression,
                "formule_etapes_json": c.formule_etapes_json,
                "relation_vers": c.relation_vers.slug if c.relation_vers else None,
                "relation_vers_id": c.relation_vers_id,
                "relation_vers_nom": c.relation_vers.nom if c.relation_vers else None,
                "relation_champ_affichage": c.relation_champ_affichage or "",
                "relation_colonne_source": c.relation_colonne_source or "",
                "relation_bidirectionnelle": c.relation_bidirectionnelle,
                # Colonne partagée (GroupePartage) — indépendant de relation_*.
                "groupe_partage_id": c.groupe_partage_id,
                "partage_membres": [
                    {"table_id": autre.table_id, "table_nom": autre.table.nom,
                     "table_slug": autre.table.slug,
                     "champ_nom": autre.nom, "champ_slug": autre.slug}
                    for autre in (
                        c.groupe_partage.champs.exclude(pk=c.pk).select_related("table")
                        if c.groupe_partage_id else []
                    )
                ],
            }
            for c in table.champs.all()
        ],
        "nombre_lignes": nombre_lignes,
        "nombre_lignes_groupe": nombre_lignes_groupe,
        "expose_client": table.expose_client,
        "masquer_formulaire_client": table.masquer_formulaire_client,
        "formulaire_conditionnel": bool((table.structure_formulaire or {}).get("actif")),
        "structure_formulaire": __import__("engine.formulaire_conditionnel", fromlist=["structure_pour_client"]).structure_pour_client(table),
        "theme_formulaire": __import__("engine.formulaire_conditionnel", fromlist=["theme_pour_client"]).theme_pour_client(table),
        "denaturee": table.denaturee,
        "afficher_photo_identite_document": table.afficher_photo_identite_document,
        "photo_identite_url": _url_image(table.photo_identite_document),
    }


def _serialiser_groupe_tables(groupe):
    """Sérialise un GroupeTables et la liste ordonnée de ses tables membres
    (découpage automatique ou rattachement manuel — voir models.py)."""
    tables = list(groupe.tables.order_by("partie_index", "id"))
    from ..groupe_fusion import modifiee_depuis_fusion
    fusion = groupe.table_fusionnee
    return {
        "id": groupe.id,
        "nom": groupe.nom,
        "description": groupe.description,
        "cree_automatiquement": groupe.cree_automatiquement,
        "nb_tables": len(tables),
        "nb_lignes_total": sum(t.enregistrements.count() for t in tables),
        "cree_le": groupe.cree_le.isoformat(),
        # Fusion en une seule table (groupe_fusion.py) : tant qu'elle existe, les parties ne sont plus listées, seule la table fusionnée l'est.
        "fusionne": fusion is not None,
        "table_fusionnee_id": fusion.id if fusion else None,
        "table_fusionnee_slug": fusion.slug if fusion else None,
        "fusionne_le": groupe.fusionne_le.isoformat() if groupe.fusionne_le else None,
        "modifiee_depuis_fusion": modifiee_depuis_fusion(groupe) if fusion else False,
        "tables": [
            {
                "id": t.id, "nom": t.nom, "slug": t.slug,
                "partie_index": t.partie_index, "partie_total": t.partie_total,
                "partie_precedente_id": t.partie_precedente_id,
                "nombre_lignes": t.enregistrements.count(),
            }
            for t in tables
        ],
    }


def _serialiser_connexion_externe(connexion):
    """Sérialise une ConnexionExterne — le mot de passe n'est JAMAIS renvoyé
    au client, seul un booléen indique s'il est renseigné."""
    return {
        "id": connexion.id,
        "nom": connexion.nom,
        "moteur": connexion.moteur,
        "moteur_libelle": connexion.get_moteur_display(),
        "hote": connexion.hote,
        "port": connexion.port,
        "port_effectif": connexion.port_effectif,
        "nom_base": connexion.nom_base,
        "utilisateur": connexion.utilisateur,
        "mot_de_passe_renseigne": bool(connexion.mot_de_passe),
        "ssl_actif": connexion.ssl_actif,
        "parametres_supplementaires": connexion.parametres_supplementaires,
        "actif": connexion.actif,
        "derniere_synchro_le": connexion.derniere_synchro_le.isoformat() if connexion.derniere_synchro_le else None,
        "derniere_synchro_statut": connexion.derniere_synchro_statut,
        "derniere_synchro_erreur": connexion.derniere_synchro_erreur,
        "nb_tables_importees": connexion.tables_importees.count(),
    }


def _serialiser_demande(demande):
    return {
        "id": demande.id,
        "texte": demande.texte,
        "table_slug": demande.table_cible.slug if demande.table_cible else None,
        "statut": demande.statut,
        "erreur": demande.erreur,
        "modifie_manuellement": demande.modifie_manuellement,
        "script_genere": demande.script_genere,
        "fichier_script": demande.fichier_script,
        "fichier_resultat": demande.fichier_resultat,
        "resultat": demande.resultat_json,
        "couleurs": demande.couleurs or {},
        "graphiques": [g.image_base64 for g in demande.graphiques.all()],
        "rapports": [{"format": r.format_fichier, "url": r.fichier.url} for r in demande.rapports.all()],
        "cree_le": demande.cree_le.isoformat(),
    }


# ── INSCRIPTION ───────────────────────────────────────────
@limiter("inscription", max_appels=5, periode_secondes=60, methodes={"POST"})
def vue_inscription(request):
    """Page d'inscription.

    Uniquement ?rejoindre=<slug> → rejoindre l'entreprise unique (si inscription_ouverte=True) ; on ne crée plus d'entreprise.
    """
    if request.user.is_authenticated:
        return redirect('/')

    slug_rejoindre = request.GET.get('rejoindre') or request.POST.get('rejoindre', '')
    entreprise_cible = None
    if slug_rejoindre:
        try:
            entreprise_cible = Entreprise.objects.get(slug=slug_rejoindre, inscription_ouverte=True)
        except Entreprise.DoesNotExist:
            entreprise_cible = None

    from django.conf import settings as _reglages
    if entreprise_cible is None and not getattr(_reglages, "GYPLATFORM_MULTI_ENTREPRISE", False):
        # Installation à entreprise unique : le super administrateur Django en est l'administrateur et crée les autres comptes.
        return redirect('/login/')
    # Sinon (multi-entreprises) : sans lien « rejoindre », l'inscription CRÉE une entreprise et son compte administrateur.
    if entreprise_cible is not None:
        from ..securite import noter_entreprise_publique
        noter_entreprise_publique(request, entreprise_cible.slug)   # thème, logo et langue de cette entreprise sur la page

    if request.method == 'POST':
        formulaire = InscriptionForm(request.POST, rejoindre_entreprise=bool(entreprise_cible))
        if formulaire.is_valid() and _inscription_possible(formulaire, entreprise_cible):
            User = get_user_model()
            donnees = formulaire.cleaned_data
            try:
                with transaction.atomic():   # compte + entreprise + rattachement : tout ou rien (jamais un compte orphelin)
                    utilisateur = User.objects.create_user(
                        username=donnees['username'],
                        password=donnees['password'],
                        first_name=donnees['first_name'],
                        last_name=donnees['last_name'],
                    )
                    if entreprise_cible:
                        # Rejoindre une entreprise existante en tant que lecteur
                        dernier = (
                            MembreEntreprise.objects.filter(entreprise=entreprise_cible, role='lecteur')
                            .exclude(numero_client=None)
                            .order_by('-numero_client')
                            .values_list('numero_client', flat=True)
                            .first()
                        )
                        MembreEntreprise.objects.create(
                            utilisateur=utilisateur, entreprise=entreprise_cible, role='lecteur',
                            indicatif_pays=donnees.get('indicatif_pays', '+242'),
                            telephone=donnees.get('telephone', ''),
                            numero_client=(dernier or 0) + 1,
                        )
                    else:
                        # Nouvelle entreprise : son créateur en est l'administrateur (il règle ensuite thème, langue, équipe).
                        from ..securite import slug_entreprise_libre
                        nom_entreprise = donnees['entreprise_nom'].strip()
                        nouvelle = Entreprise.objects.create(nom=nom_entreprise, slug=slug_entreprise_libre(nom_entreprise))
                        MembreEntreprise.objects.create(
                            utilisateur=utilisateur, entreprise=nouvelle, role='admin',
                            indicatif_pays=donnees.get('indicatif_pays', '+242'),
                            telephone=donnees.get('telephone', ''),
                        )
            except IntegrityError:   # même nom d'utilisateur / d'entreprise créé à l'instant par quelqu'un d'autre
                formulaire.add_error(None, t("engine/views/_part_006.py:inscription_doublon"))
            else:
                utilisateur.backend = 'django.contrib.auth.backends.ModelBackend'
                login(request, utilisateur)
                return redirect('/')
    else:
        formulaire = InscriptionForm()

    return render(request, 'engine/inscription.html', {
        'formulaire': formulaire,
        'entreprise_cible': entreprise_cible,
        'slug_rejoindre': slug_rejoindre,
    })


# ── AUTHENTIFICATION ──────────────────────────────────────────
def _contexte_login(request, formulaire):
    """Contexte de la page de connexion : `entreprise_choisie` = étape 2 (identifiants) ; sinon étape 1 (liste des entreprises).
    `suite` : adresse « next » conservée à travers le choix (jamais un autre site : seul un chemin interne est gardé)."""
    slug = str(request.session.get("entreprise_publique") or "")[:60]
    choisie = bool(slug) and Entreprise.objects.filter(slug=slug).exists()
    suite = str(request.GET.get("next") or "")[:500]
    if not suite.startswith("/") or suite.startswith("//") or "\\" in suite:
        suite = ""
    return {"formulaire": formulaire, "entreprise_choisie": choisie, "suite": suite}


@limiter("login", max_appels=10, periode_secondes=60, methodes={"POST"})
def vue_login(request):
    """Page de connexion."""
    if request.user.is_authenticated:
        return redirect('/')
    # Connexion en DEUX temps : 1) liste des entreprises (recherche, logo, nom, couleur) ; 2) identifiants, une fois l'entreprise choisie.
    # /login/?e=<slug> : choisit l'entreprise ; la page se charge à ses couleurs (thème, logo, nom, langue) dès le login.
    # /login/ ou /login/?e= en GET : aucune entreprise retenue, liste + thème par défaut. Un POST garde l'entreprise choisie.
    from ..securite import noter_entreprise_publique, oublier_entreprise_publique
    if request.GET.get('e'):
        noter_entreprise_publique(request, request.GET.get('e'))
    elif request.method != 'POST':
        oublier_entreprise_publique(request)

    if request.method == 'POST':
        formulaire = LoginForm(request.POST)
        if formulaire.is_valid():
            nom = formulaire.cleaned_data['username']
            mot_de_passe = formulaire.cleaned_data['password']
            import time
            from django.core.cache import cache
            from ..securite import journal, url_suivante_sure
            cle_echecs = f"login_echecs:{nom.lower()}"
            # Verrou par COMPTE : 3 échecs d'affilée → 30 s d'attente (fin du verrou stockée, pas de TTL à deviner).
            cle_verrou = f"login_verrou:{nom.lower()}"
            try:
                reste = int((cache.get(cle_verrou) or 0) - time.time()) + 1
            except Exception:
                reste = 0
            if reste > 0:
                journal.warning(t("engine/views/_part_006.py:g:488:33"), nom, adresse_ip_client(request))
                formulaire.add_error(None, tf("engine/views/_part_006.py:g:489:44", v1=reste))
                return render(request, 'engine/login.html', _contexte_login(request, formulaire))
            utilisateur = authenticate(request, username=nom, password=mot_de_passe)
            if utilisateur is not None:
                try:
                    cache.delete(cle_echecs)
                except Exception:
                    pass
                from ..securite import choisir_entreprise
                if not choisir_entreprise(request, utilisateur):   # entreprise choisie sur la page : le compte doit en être membre
                    formulaire.add_error(None, t("engine/views/_part_006.py:entreprise_non_membre"))
                    return render(request, 'engine/login.html', _contexte_login(request, formulaire))
                login(request, utilisateur)
                journal.info(t("engine/views/_part_006.py:g:508:30"), nom, adresse_ip_client(request))
                # Jamais de redirection vers un autre site (lien piégé « /login/?next=https://… »).
                return redirect(url_suivante_sure(request, request.GET.get('next')))
            try:
                try:
                    cache.incr(cle_echecs)
                except ValueError:
                    cache.set(cle_echecs, 1, timeout=15 * 60)
            except Exception:
                journal.exception(t("engine/views/_part_006.py:g:517:35"))
            journal.warning(t("engine/views/_part_006.py:g:518:29"), nom, adresse_ip_client(request))
            try:
                if (cache.get(cle_echecs) or 0) >= 3:
                    cache.set(cle_verrou, time.time() + 30, timeout=30)
                    cache.delete(cle_echecs)
                    formulaire.add_error(None, t("engine/views/_part_006.py:g:523:48"))
                    return render(request, 'engine/login.html', _contexte_login(request, formulaire))
            except Exception:
                pass
            # Message unique : ne révèle jamais si le nom d'utilisateur existe (énumération de comptes).
            # Seul un compte désactivé AVEC le bon mot de passe est signalé comme tel (déjà prouvé légitime).
            from django.contrib.auth import get_user_model
            _u = get_user_model().objects.filter(username=nom).first()
            if _u is not None and not _u.is_active and _u.check_password(mot_de_passe):
                formulaire.add_error(None, t("engine/views/_part_006.py:g:532:44"))
            else:
                formulaire.add_error(None, t("engine/views/_part_006.py:g:534:44"))
    else:
        formulaire = LoginForm()
        if request.GET.get('annule'):
            formulaire.add_error(None, t("engine/views/_part_006.py:g:538:40"))

    return render(request, 'engine/login.html', _contexte_login(request, formulaire))




def vue_logout(request):
    """Deconnecte l'utilisateur."""
    logout(request)
    return redirect('/login/')


# ── PROFIL ────────────────────────────────────────────────────
@login_required
def vue_profile(request):
    """Page de profil de l'utilisateur connecte."""
    utilisateur = request.user

    # Infos entreprise
    membre = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=utilisateur
    ).first()
    entreprise = membre.entreprise if membre else None
    role = membre.get_role_display() if membre else None

    if request.method == 'POST':
        action = request.POST.get('action', 'profil')

        if action == 'profil':
            formulaire = ProfileForm(request.POST)
            if formulaire.is_valid():
                utilisateur.first_name = formulaire.cleaned_data['first_name']
                utilisateur.last_name = formulaire.cleaned_data['last_name']
                utilisateur.save(update_fields=['first_name', 'last_name'])
                if membre:
                    membre.indicatif_pays = formulaire.cleaned_data.get('indicatif_pays', '+242')
                    membre.telephone = formulaire.cleaned_data.get('telephone', '')
                    membre.save(update_fields=['indicatif_pays', 'telephone'])
                    if membre.role == 'admin' and entreprise:
                        pass  # Le nom d'entreprise se modifie dans la gestion des équipes
                return redirect('/profile/?ok=profil')

        elif action == 'mot_de_passe':
            if membre and membre.role == 'agent':
                # Un agent ne choisit jamais son mot de passe : il lui est donné (et changé) par l'administrateur.
                return redirect('/profile/?err=mdp_admin')
            formulaire_mdp = ChangePasswordForm(request.POST)
            if formulaire_mdp.is_valid():
                ancien = formulaire_mdp.cleaned_data['ancien_mot_de_passe']
                nouveau = formulaire_mdp.cleaned_data['nouveau_mot_de_passe']
                if not utilisateur.check_password(ancien):
                    formulaire_mdp.add_error('ancien_mot_de_passe', t("engine/views/_part_006.py:g:627:69"))
                else:
                    utilisateur.set_password(nouveau)
                    utilisateur.save()
                    from django.contrib.auth import update_session_auth_hash
                    update_session_auth_hash(request, utilisateur)
                    return redirect('/profile/?ok=mot_de_passe')

    # Pre-remplir le formulaire avec les donnees actuelles
    formulaire = ProfileForm(initial={
        'entreprise_nom': entreprise.nom if entreprise else '',
        'first_name': utilisateur.first_name,
        'last_name': utilisateur.last_name,
        'indicatif_pays': membre.indicatif_pays if membre else '+242',
        'telephone': membre.telephone if membre else '',
    })
    formulaire_mdp = ChangePasswordForm()

    # Stats utilisateur
    nombre_demandes = DemandeAgent.objects.filter(utilisateur=utilisateur).count()
    nombre_tables = TableDynamique.objects.filter(entreprise=entreprise, source_systeme="").count() if entreprise else 0

    photo_profil_url = None
    if membre and membre.photo_profil:
        try:
            photo_profil_url = membre.photo_profil.url
        except Exception:
            pass

    jeton_api = ''
    if membre and membre.role in ('admin', 'agent'):
        from ..models import CleAPI
        jeton_api = CleAPI.jeton_profil(membre, utilisateur).cle_visible

    return render(request, 'engine/profile.html', {
        'jeton_api': jeton_api,
        'formulaire': formulaire,
        'formulaire_mdp': formulaire_mdp,
        'utilisateur': utilisateur,
        'entreprise': entreprise,
        'role': role,
        'nombre_demandes': nombre_demandes,
        'nombre_tables': nombre_tables,
        'message_ok': request.GET.get('ok'),
        'photo_profil_url': photo_profil_url,
        'est_superuser': utilisateur.is_superuser,
    })


@login_required
@require_http_methods(["POST"])
def api_regenerer_jeton_profil(request):
    """Bouton « Régénérer » de la page profil : l'ancien jeton est révoqué, le nouveau est renvoyé."""
    from ..models import CleAPI
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    if not membre or membre.role not in ('admin', 'agent'):
        return JsonResponse({"erreur": t("engine/views/_part_006.py:g:684:40")}, status=403)
    instance = CleAPI.regenerer_profil(membre, request.user)
    return JsonResponse({"jeton": instance.cle_visible, "prefixe": instance.prefixe})


# ── API profil (endpoint JSON pour le header du dashboard) ──
@login_required
def api_profile(request):
    """Retourne les infos sommaires du profil pour le header."""
    membre = request.user.membreentreprise_set.select_related('entreprise').first()
    photo_url = None
    if membre and membre.photo_profil:
        try:
            photo_url = membre.photo_profil.url
        except Exception:
            pass

    logo_url = None
    if membre and membre.entreprise.logo:
        try:
            logo_url = request.build_absolute_uri(membre.entreprise.logo.url)
        except Exception:
            pass

    return JsonResponse({
        'username': request.user.username,
        'first_name': request.user.first_name,
        'last_name': request.user.last_name,
        'telephone': ((membre.indicatif_pays + ' ' + membre.telephone).strip() if membre and membre.telephone else ''),
        'entreprise': membre.entreprise.nom if membre else None,
        'role': membre.get_role_display() if membre else None,
        'est_admin': _a_pouvoir(membre, "employes"),
        'droits': _delegation.liste_droits(membre),   # droits de délégation effectifs : chaque bouton réservé s'affiche selon SON droit
        'est_lecteur': (membre.role == 'lecteur') if membre else False,
        'est_super_admin': bool(request.user.is_superuser),   # le bouton « SIEM » de la barre latérale ne s'affiche que pour lui
        'role_code': membre.role if membre else None,
        # Droits stricts : le bouton « Documents administratifs » n'apparaît que si la vue est autorisée (même règle que la page et l'API).
        'documents_administratifs_autorise': _vue_autorisee(membre, 'documents_administratifs') if membre else False,
        # Bouton « Finances » : le droit financier ET la page dans la liste des pages de l'agent (ce que l'administrateur ne permet pas, l'agent ne le voit plus).
        'finances_autorise': bool(membre and _a_un_pouvoir_de(membre, 'finances') and _vue_autorisee(membre, 'finances')),
        'photo_profil': photo_url,
        'logo_entreprise': logo_url,
    })


# ── GESTION DES EMPLOYES ─────────────────────────────────────
def _inscription_possible(formulaire, entreprise):
    """Plafond de personnes inscrites (acces_entreprise.place_disponible) : au plafond, l'inscription est refusée avec le message du plafond."""
    from .. import acces_entreprise as acces
    if entreprise is None or acces.place_disponible(entreprise):
        return True
    formulaire.add_error(None, acces.message_limite(entreprise))
    return False


@login_required
def vue_employes(request):
    """Page de gestion des employes de l'entreprise."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=request.user
    ).first()
    if not _a_pouvoir(membre, "employes"):
        return redirect('/profile/')

    entreprise = membre.entreprise
    membres = MembreEntreprise.objects.filter(entreprise=entreprise).exclude(role='caisse').select_related('utilisateur').order_by('role', 'utilisateur__username')

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'creer_employe':
            formulaire = CreerEmployeForm(request.POST)
            if formulaire.is_valid():
                from .. import acces_entreprise as _acces
                if formulaire.cleaned_data.get('role') != 'admin' and not _acces.place_disponible(entreprise):
                    return redirect('/employes/?err=limite')
                User = get_user_model()
                donnees = formulaire.cleaned_data
                employe = User.objects.create_user(
                    username=donnees['username'],
                    password=donnees['password'],
                    first_name=donnees['first_name'],
                    last_name=donnees['last_name'],
                )
                role_nouveau = donnees['role']
                numero_client_nouveau = None
                if role_nouveau == 'lecteur':
                    dernier = (
                        MembreEntreprise.objects.filter(entreprise=entreprise, role='lecteur')
                        .exclude(numero_client=None)
                        .order_by('-numero_client')
                        .values_list('numero_client', flat=True)
                        .first()
                    )
                    numero_client_nouveau = (dernier or 0) + 1
                MembreEntreprise.objects.create(
                    utilisateur=employe, entreprise=entreprise, role=role_nouveau,
                    indicatif_pays=donnees.get('indicatif_pays', '+242'),
                    telephone=donnees.get('telephone', ''),
                    numero_client=numero_client_nouveau,
                )
                return redirect('/employes/?ok=creer')
        elif action == 'supprimer_employe':
            user_id = request.POST.get('user_id')
            if user_id and int(user_id) != request.user.id:
                from .. import reprise_poste
                try:
                    reprise_poste.retirer_agent(entreprise, membre, int(user_id))
                except reprise_poste.ReprisePosteErreur:
                    return redirect('/employes/?err=suppression')
                return redirect('/employes/?ok=supprimer')
        elif action == 'maj_cle_chiffrement_qr':
            # Cle de chiffrement des QR codes (recherche securisee) —
            # reservee a l'admin, voir engine/qr_secure.py.
            entreprise.cle_chiffrement_qr = request.POST.get('cle_chiffrement_qr', '').strip()
            entreprise.save(update_fields=['cle_chiffrement_qr'])
            return redirect('/employes/?ok=cle_qr')
            user_id = request.POST.get('user_id')
            nouveau_role = request.POST.get('nouveau_role')
            if user_id and nouveau_role in ('admin', 'agent', 'lecteur') and (nouveau_role != 'admin' or membre.role == 'admin' or _a_liberte_absolue(membre)):   # un agent délégué ne se promeut pas administrateur
                MembreEntreprise.objects.filter(
                    entreprise=entreprise, utilisateur_id=user_id
                ).update(role=nouveau_role)
                return redirect('/employes/?ok=role')
    else:
        formulaire = CreerEmployeForm()

    return render(request, 'engine/employes.html', {
        'formulaire': formulaire,
        'entreprise': entreprise,
        'membres': membres,
        'user_id': request.user.id,
        'message_ok': request.GET.get('ok'),
    })


@login_required
def api_employes(request):
    """API JSON : liste et creation des employes."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related('entreprise').filter(
        utilisateur=request.user
    ).first()

    if request.method == 'GET':
        # ── Pagination : ?page=1&taille=200 (memes conventions que api_lignes) ──
        try:
            page = max(1, int(request.GET.get("page", 1)))
            taille = min(max(1, int(request.GET.get("taille", 200))), 1000)
        except (ValueError, TypeError):
            page, taille = 1, 200

        try:
            resultat = actions.executer_action(
                "lister_employes", membre, membre.entreprise if membre else None,
                page=page, taille=taille,
            )
        except actions.ActionRefusee:
            return JsonResponse({"erreur": t("engine/views/_part_006.py:771:45")}, status=403)

        return JsonResponse({
            'employes': [
                {
                    'id': m.utilisateur.id,
                    'username': m.utilisateur.username,
                    'first_name': m.utilisateur.first_name,
                    'last_name': m.utilisateur.last_name,
                    'email': m.utilisateur.email,
                    'indicatif_pays': m.indicatif_pays,
                    'telephone': m.telephone,
                    'role': m.role,
                    'numero_client': m.numero_client,
                    'role_display': m.get_role_display(),
                    'vues_autorisees': m.vues_autorisees,
                    'date_rejoindre': m.utilisateur.date_joined.isoformat(),
                    'est_actif': m.utilisateur.is_active,
                }
                for m in resultat['membres']
            ],
            'pagination': {
                'page': resultat['page'],
                'taille': resultat['taille'],
                'total': resultat['total'],
                'pages': resultat['pages'],
                'a_suivant': resultat['page'] < resultat['pages'],
                'a_precedent': resultat['page'] > 1,
            },
        })

    if request.method == 'POST':
        formulaire = CreerEmployeForm(json.loads(request.body or '{}'))
        if not formulaire.is_valid():
            return JsonResponse({'erreurs': formulaire.errors}, status=400)
        # Le formulaire d'ajout ne propose plus le choix du role : un employe
        # ajoute depuis cette page est toujours un "agent". Les roles admin/
        # lecteur (client) sont attribues ailleurs, pas via cette creation
        # rapide — on ignore donc toute valeur de role potentiellement
        # envoyee (y compris via un appel direct a l'API).
        from .. import acces_entreprise as _acces
        if membre and not _acces.place_disponible(membre.entreprise):
            return JsonResponse({"erreur": _acces.message_limite(membre.entreprise)}, status=402)
        try:
            employe = actions.executer_action(
                "creer_employe", membre, membre.entreprise if membre else None,
                donnees=formulaire.cleaned_data,
            )
        except actions.ActionRefusee:
            return JsonResponse({"erreur": t("engine/views/_part_006.py:817:45")}, status=403)
        return JsonResponse({
            'ok': True,
            'employe': {
                'id': employe.id, 'username': employe.username,
                'first_name': employe.first_name, 'last_name': employe.last_name,
                'role': 'agent',
            }
        }, status=201)

    return JsonResponse({'erreur': t("engine/views/_part_006.py:827:37")}, status=405)


@login_required
@require_http_methods(["POST"])
def api_table_config_carte(request, table_id):
    """Theme (recto/verso) + signature/cachet + configuration de la carte
    generee pour cette table d'agents (colonnes affichees, toggles logo/QR/
    signature/cachet par face, couleurs). Reserve a l'admin, meme principe
    que api_table_photo_identite. POST multipart : 'theme_recto'/'theme_verso'/
    'signature'/'cachet' (fichiers, optionnels), 'config_carte' (JSON)."""
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        actions.verifier_permission("configurer_carte_table", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:842:41")}, status=403)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=membre.entreprise)

    fichiers = {
        cle: request.FILES[cle]
        for cle in ("theme_recto", "theme_verso", "signature", "cachet", "qrcode")
        if cle in request.FILES
    }
    champs_texte = {
        cle: request.POST[cle]
        for cle in ("signature_nom", "matricule_prefixe")
        if cle in request.POST
    }
    config_carte = None
    if "config_carte" in request.POST:
        try:
            config_carte = json.loads(request.POST["config_carte"])
        except (ValueError, TypeError):
            return JsonResponse({"erreur": t("engine/views/_part_006.py:860:45")}, status=400)

    actions.executer_action(
        "configurer_carte_table", membre, table,
        fichiers=fichiers, champs_texte=champs_texte, config_carte=config_carte,
    )
    return JsonResponse(_serialiser_table(table))


@login_required
@_pdf_erreur_json
def api_generer_cartes(request, table_id):
    """Genere et telecharge le PDF recto+verso des cartes des agents
    SELECTIONNES (parametre GET "ids", liste d'identifiants d'enregistrements
    separes par des virgules — 1 a 10 maximum). PDF A4 portrait, format ID-1
    exact, exactement 2 pages (10 rectos puis 10 versos alignes) — voir
    reports.py."""
    membre = membre_de_requete(request, MembreEntreprise.objects.select_related('entreprise'))
    try:
        actions.verifier_permission("generer_cartes", membre)
    except actions.ActionRefusee:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:880:41")}, status=403)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=membre.entreprise)
    if not table.config_carte:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:883:41")}, status=400)

    ids_brut = request.GET.get("ids", "").strip()
    if not ids_brut:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:887:41")}, status=400)
    try:
        ids = [int(v) for v in ids_brut.split(",") if v.strip()]
    except ValueError:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:891:41")}, status=400)
    if not ids:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:893:41")}, status=400)
    if len(ids) > 10:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:895:41")}, status=400)

    from ..vue_groupe import enregistrements_vue
    par_id = {e.id: e for e in enregistrements_vue(table).filter(id__in=ids)}
    enregistrements = [par_id[i] for i in ids if i in par_id]  # garde l'ordre de selection choisi
    if not enregistrements:
        return JsonResponse({"erreur": t("engine/views/_part_006.py:900:41")}, status=404)

    champs_par_slug = {c.slug: c for c in table.champs.all()}
    if (table.config_carte or {}).get("sortie") == "images":   # « cartes seulement » : PNG individuels (ou un seul PNG)
        contenu, mime, nom = actions.executer_action(
            "generer_cartes_images", membre, table,
            enregistrements=enregistrements, champs_par_slug=champs_par_slug,
        )
        from django.http import HttpResponse
        reponse = HttpResponse(contenu, content_type=mime)
        reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
        return reponse
    pdf_bytes = actions.executer_action(
        "generer_cartes", membre, table,
        enregistrements=enregistrements, champs_par_slug=champs_par_slug,
    )

    from django.http import HttpResponse
    reponse = HttpResponse(pdf_bytes, content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="cartes_{table.slug}.pdf"'
    return reponse




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
