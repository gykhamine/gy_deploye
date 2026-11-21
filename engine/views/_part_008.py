from engine.i18n import td
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from ..delegation import a_pouvoir as _a_pouvoir
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
from ..models import DocumentAdministratif  # noqa: E402
from .. import acces_entreprise as _acces_entreprise  # noqa: E402


def _sauvegarder_personnalisation(agent, contexte, corps):
    """Réglages personnels d'un agent (PersonnalisationDocumentMembre) : vide = hérite de l'entreprise."""
    perso, _ = PersonnalisationDocumentMembre.objects.get_or_create(membre=agent, contexte=contexte)
    if corps.get("reinitialiser"):
        for nom in ("afficher_signatures_document", "afficher_cachet_document"):
            setattr(perso, nom, None)
        for nom in ("position_signatures_document", "position_cachet_document", "cachet_texte_actif", "cachet_texte_inactif"):
            setattr(perso, nom, "")
        perso.save()
        return JsonResponse({"ok": True})
    maj = []
    for nom in ("afficher_signatures_document", "afficher_cachet_document"):
        if nom in corps:
            setattr(perso, nom, None if corps[nom] in (None, "") else bool(corps[nom]))
            maj.append(nom)
    for nom, valides in (("position_signatures_document", {"notes", "pied_page"}),
                         ("position_cachet_document", {"entete", "totaux", "pied_page"})):
        if nom in corps and (corps[nom] in valides or corps[nom] in (None, "")):
            setattr(perso, nom, corps[nom] or "")
            maj.append(nom)
    for nom in ("cachet_texte_actif", "cachet_texte_inactif"):
        if nom in corps:
            setattr(perso, nom, str(corps[nom] or "").strip()[:40])
            maj.append(nom)
    if maj:
        perso.save(update_fields=maj)
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_configuration_documents_sauvegarder(request, contexte):
    """Sauvegarde la configuration document. Deux niveaux de permission
    distincts dans UNE même requête possible :
      - le "branding" commun à TOUS les documents (couleurs, logo, position
        du logo, nom affiché — tout ce qui identifie l'entreprise par
        défaut) reste réservé aux administrateurs ;
      - les réglages PROPRES à ce contexte (photo, sections, cachet,
        signatures) sont ouverts aux agents : un agent peut désormais gérer
        ces réglages pour les tables qu'il gère.
    L'admin n'est donc exigé que si la requête touche au moins un champ
    global — jamais pour un enregistrement qui ne porte que des champs
    contextuels (c'est le cas du bouton "Sauvegarder ce contexte" du front)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    if not _contexte_valide(contexte):
        return JsonResponse({"erreur": t("engine/views/_part_008.py:31:41")}, status=404)

    entreprise = _entreprise_courante(request)
    corps = json.loads(request.body or "{}")

    champs_couleurs = {
        "couleur_accent_document", "couleur_entete_document",
        "couleur_tableau_entete_document", "couleur_lignes_alternees_document",
        "couleur_texte_document",
        "couleur_fond_document", "couleur_corps_document", "couleur_pied_page_document",
    }
    champs_globaux = champs_couleurs | {
        "nom_affichage_document", "afficher_logo_document", "position_logo_document",
        "police_document", "texte_pied_page_document", "numeroter_pages_document",
    }
    if champs_globaux & corps.keys():
        refus = _admin_requis(request, entreprise, "documents_apparence")
        if refus:
            return refus

    # Un agent ne modifie JAMAIS les réglages de l'entreprise : ses choix (cachet, signatures) ne valent que pour
    # les documents qu'il génère lui-même. Ce qui est imposé par l'entreprise est ignoré s'il le renvoie.
    agent = _agent_document(request, entreprise)
    if agent is not None:
        return _sauvegarder_personnalisation(agent, contexte, corps)

    cfg_ia, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    sous_config, _ = ConfigurationDocumentContexte.objects.get_or_create(
        entreprise=entreprise, contexte=contexte,
    )

    positions_valides = {"gauche", "centre", "droite"}
    positions_photo_valides = {"entete", "partie_destinataire"}
    positions_signatures_valides = {"notes", "pied_page"}
    positions_cachet_valides = {"entete", "totaux", "pied_page"}

    # ── Champs globaux (ConfigurationIA) ──
    maj_ia = []
    if "nom_affichage_document" in corps:
        cfg_ia.nom_affichage_document = str(corps["nom_affichage_document"] or "").strip()
        maj_ia.append("nom_affichage_document")
    if "afficher_logo_document" in corps:
        cfg_ia.afficher_logo_document = bool(corps["afficher_logo_document"])
        maj_ia.append("afficher_logo_document")
    if "position_logo_document" in corps and corps["position_logo_document"] in positions_valides:
        cfg_ia.position_logo_document = corps["position_logo_document"]
        maj_ia.append("position_logo_document")
    if "police_document" in corps and corps["police_document"] in {c for c, _l in ConfigurationIA.POLICES_DOCUMENT_CHOICES}:
        cfg_ia.police_document = corps["police_document"]
        maj_ia.append("police_document")
    if "texte_pied_page_document" in corps:
        cfg_ia.texte_pied_page_document = str(corps["texte_pied_page_document"] or "").strip()[:150]
        maj_ia.append("texte_pied_page_document")
    if "numeroter_pages_document" in corps:
        cfg_ia.numeroter_pages_document = bool(corps["numeroter_pages_document"])
        maj_ia.append("numeroter_pages_document")
    for champ in champs_couleurs:
        if champ in corps:
            val = str(corps[champ] or "").strip()
            if re.fullmatch(r"#[0-9a-fA-F]{6}", val):
                setattr(cfg_ia, champ, val)
                maj_ia.append(champ)
    if maj_ia:
        cfg_ia.save(update_fields=maj_ia)

    # ── Champs propres au contexte (ConfigurationDocumentContexte) ──
    maj_ctx = []
    for nom in ("afficher_entete_officiel", "afficher_coordonnees_entete"):
        if nom in corps:
            setattr(sous_config, nom, bool(corps[nom]))
            maj_ctx.append(nom)
    if "afficher_photo_entite" in corps:
        sous_config.afficher_photo_entite = bool(corps["afficher_photo_entite"])
        maj_ctx.append("afficher_photo_entite")
    if "position_photo_entite" in corps and corps["position_photo_entite"] in positions_photo_valides:
        sous_config.position_photo_entite = corps["position_photo_entite"]
        maj_ctx.append("position_photo_entite")
    if "sections_document" in corps:
        val = corps["sections_document"]
        sous_config.sections_document = [
            s for s in (val or []) if isinstance(s, str) and s in ConfigurationIA.SECTIONS_DEFAUT
        ]
        maj_ctx.append("sections_document")
    if "afficher_signatures_document" in corps:
        sous_config.afficher_signatures_document = bool(corps["afficher_signatures_document"])
        maj_ctx.append("afficher_signatures_document")
    if "position_signatures_document" in corps and corps["position_signatures_document"] in positions_signatures_valides:
        sous_config.position_signatures_document = corps["position_signatures_document"]
        maj_ctx.append("position_signatures_document")
    if "afficher_cachet_document" in corps:
        sous_config.afficher_cachet_document = bool(corps["afficher_cachet_document"])
        maj_ctx.append("afficher_cachet_document")
    if "cachet_texte_actif" in corps:
        sous_config.cachet_texte_actif = str(corps["cachet_texte_actif"] or "").strip()[:40]
        maj_ctx.append("cachet_texte_actif")
    if "cachet_texte_inactif" in corps:
        sous_config.cachet_texte_inactif = str(corps["cachet_texte_inactif"] or "").strip()[:40]
        maj_ctx.append("cachet_texte_inactif")
    if "champ_statut_cachet" in corps:
        sous_config.champ_statut_cachet = str(corps["champ_statut_cachet"] or "").strip()
        maj_ctx.append("champ_statut_cachet")
    if "position_cachet_document" in corps and corps["position_cachet_document"] in positions_cachet_valides:
        sous_config.position_cachet_document = corps["position_cachet_document"]
        maj_ctx.append("position_cachet_document")
    if "table_cachet_document" in corps:
        val = corps["table_cachet_document"]
        if val in (None, "", 0, "0"):
            sous_config.table_cachet_document = None
        else:
            table_cachet = TableDynamique.objects.filter(id=val, entreprise=entreprise).first()
            if table_cachet:
                sous_config.table_cachet_document = table_cachet
        maj_ctx.append("table_cachet_document")
    if "type_document_cachet" in corps:
        val = str(corps["type_document_cachet"] or "").strip()
        types_valides = {slug for slug, _ in DocumentGenere.TYPE_CHOICES}
        sous_config.type_document_cachet = val if val in types_valides else ""
        maj_ctx.append("type_document_cachet")
    if "document_cachet" in corps:
        # Sélecteur "carte précise" UNIQUE côté front : préfixe "doc-<id>"
        # (carte de document) ou "ana-<id>" (carte d'analyse), toujours
        # mutuellement exclusifs — voir api_configuration_documents_lire
        # pour le sens inverse (carte_precise_cachet).
        val = str(corps["document_cachet"] or "").strip()
        sous_config.document_cachet = None
        sous_config.demande_cachet = None
        if val.startswith("doc-"):
            doc_cachet = DocumentGenere.objects.filter(id=val[4:], entreprise=entreprise).first()
            if doc_cachet:
                sous_config.document_cachet = doc_cachet
        elif val.startswith("ana-"):
            demande_cachet = DemandeAgent.objects.filter(id=val[4:], entreprise=entreprise).first()
            if demande_cachet:
                sous_config.demande_cachet = demande_cachet
        elif val and val not in ("0",):
            # Repli de compatibilité : ancien format (id brut de DocumentGenere).
            doc_cachet = DocumentGenere.objects.filter(id=val, entreprise=entreprise).first()
            if doc_cachet:
                sous_config.document_cachet = doc_cachet
        maj_ctx.append("document_cachet")
        maj_ctx.append("demande_cachet")
    if maj_ctx:
        sous_config.save(update_fields=maj_ctx)

    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_configuration_documents_image(request, contexte):
    """Téléverse (ou supprime) une image à emplacement fixe de la
    configuration document PROPRE AU CONTEXTE : cachet_image_actif,
    cachet_image_inactif. Champ POST `cible` = nom du champ, `image` =
    fichier (omis pour supprimer l'image existante)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    if not _contexte_valide(contexte):
        return JsonResponse({"erreur": t("engine/views/_part_008.py:169:41")}, status=404)

    entreprise = _entreprise_courante(request)
    # Réglage propre au CONTEXTE (image cachet/photo entité) — ouvert aux
    # agents, seul le branding global (couleurs/logo/nom) reste admin-only.
    cible = request.POST.get("cible", "")

    if cible not in _CHAMPS_IMAGE_CONFIG_DOC_CONTEXTE:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:177:41")}, status=400)

    agent = _agent_document(request, entreprise)
    if agent is not None:
        # Un agent ne téléverse que SON cachet ; la photo de l'entité est imposée par l'entreprise.
        if cible not in ("cachet_image_actif", "cachet_image_inactif"):
            return JsonResponse({"erreur": _i18n_t("rg2.views__part_008.1")}, status=403)
        sous_config, _ = PersonnalisationDocumentMembre.objects.get_or_create(membre=agent, contexte=contexte)
    else:
        sous_config, _ = ConfigurationDocumentContexte.objects.get_or_create(
            entreprise=entreprise, contexte=contexte,
        )
    ancien = getattr(sous_config, cible)
    ancien_nom = ancien.name if ancien else None

    fichier = request.FILES.get("image")
    if fichier:
        import os
        ext = os.path.splitext(fichier.name)[1].lower()
        if ext not in _CHAMPS_IMAGE_CONFIG_DOC_CONTEXTE[cible]:
            return JsonResponse({"erreur": t("engine/views/_part_008.py:190:45")}, status=400)
        setattr(sous_config, cible, fichier)
    else:
        setattr(sous_config, cible, None)
    sous_config.save(update_fields=[cible])

    if ancien_nom:
        try:
            from django.core.files.storage import default_storage
            if default_storage.exists(ancien_nom):
                default_storage.delete(ancien_nom)
        except Exception:
            pass

    nouveau = getattr(sous_config, cible)
    return JsonResponse({
        "ok": True,
        "image_url": request.build_absolute_uri(nouveau.url) if nouveau else None,
    })


@login_required
@require_http_methods(["POST"])
def api_table_photo_identite(request, table_id):
    """Téléverse ou supprime la photo d'identité d'une table spécifique.
    POST multipart : champ 'image' = fichier (omis pour supprimer).
    Champ 'afficher' = '1'/'0' pour basculer l'activation (optionnel)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    # Photo propre à UNE table — un agent gère les tables, donc ouvert aux
    # agents (pas seulement à l'admin).

    try:
        table = TableDynamique.objects.get(pk=table_id, entreprise=entreprise)
    except TableDynamique.DoesNotExist:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:227:41")}, status=404)

    update_fields = []

    # Basculer afficher_photo_identite_document si demandé
    if "afficher" in request.POST:
        table.afficher_photo_identite_document = request.POST["afficher"] == "1"
        update_fields.append("afficher_photo_identite_document")

    # Upload / suppression image
    # "supprimer=1" ou "image" dans les FILES = action sur l'image
    if "supprimer" in request.POST or "image" in request.FILES:
        fichier = request.FILES.get("image")
        import os
        ancien = table.photo_identite_document
        ancien_nom = ancien.name if ancien else None
        if fichier:
            ext = os.path.splitext(fichier.name)[1].lower()
            if ext not in EXTENSIONS_IMAGES:
                return JsonResponse({"erreur": t("engine/views/_part_008.py:246:49")}, status=400)
            table.photo_identite_document = fichier
        else:
            table.photo_identite_document = None
        update_fields.append("photo_identite_document")
        if ancien_nom:
            try:
                from django.core.files.storage import default_storage
                if default_storage.exists(ancien_nom):
                    default_storage.delete(ancien_nom)
            except Exception:
                pass

    if update_fields:
        table.save(update_fields=update_fields)

    img = table.photo_identite_document
    return JsonResponse({
        "ok": True,
        "afficher_photo_identite_document": table.afficher_photo_identite_document,
        "photo_identite_url": request.build_absolute_uri(img.url) if img else None,
    })


@login_required
@require_http_methods(["POST"])
def api_signature_creer(request):
    """Ajoute une signature (image obligatoire, nom optionnel). Une
    entreprise peut en avoir plusieurs — chacune activable séparément."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    agent = _agent_document(request, entreprise)   # None = administrateur : signature de l'entreprise
    # Signatures : ouvert aux agents (comme les autres réglages propres au
    # contexte/à une table), seul le branding global reste admin-only.

    fichier = request.FILES.get("image")
    if not fichier:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:284:41")}, status=400)
    import os
    ext = os.path.splitext(fichier.name)[1].lower()
    if ext not in EXTENSIONS_IMAGES:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:288:41")}, status=400)

    table_sig = None
    table_id = request.POST.get("table_id") or ""
    if table_id:
        table_sig = TableDynamique.objects.filter(id=table_id, entreprise=entreprise).first()
        if not table_sig:
            return JsonResponse({"erreur": t("engine/views/_part_008.py:295:45")}, status=404)

    document_sig = None
    document_id = request.POST.get("document_id") or ""
    if document_id:
        document_sig = DocumentGenere.objects.filter(id=document_id, entreprise=entreprise).first()
        if not document_sig:
            return JsonResponse({"erreur": t("engine/views/_part_008.py:302:45")}, status=404)

    # Carte d'analyse précise (pipeline PDF distinct des cartes de document,
    # voir generer_rapport_pdf) — mutuellement exclusive avec document_sig.
    demande_sig = None
    demande_id = request.POST.get("demande_id") or ""
    if demande_id:
        demande_sig = DemandeAgent.objects.filter(id=demande_id, entreprise=entreprise).first()
        if not demande_sig:
            return JsonResponse({"erreur": t("engine/views/_part_008.py:311:45")}, status=404)

    ordre_max = SignatureDocument.objects.filter(entreprise=entreprise, membre=agent).count()
    sig = SignatureDocument.objects.create(
        entreprise=entreprise,
        table=table_sig,
        document=document_sig,
        demande=demande_sig,
        nom=(request.POST.get("nom") or "").strip()[:150],
        image=fichier,
        membre=agent,
        actif=True,
        ordre=ordre_max,
    )
    return JsonResponse({
        "ok": True,
        "signature": {
            "id": sig.id, "nom": sig.nom, "fonction": sig.fonction, "types_document": sig.liste_types(),
            "actif": sig.actif, "ordre": sig.ordre,
            "table_id": sig.table_id, "table_nom": sig.table.nom if sig.table_id else None,
            "document_id": sig.document_id,
            "document_nom": (sig.document.titre.strip() if sig.document.titre else f"{sig.document.get_type_document_display()} #{sig.document.id}") if sig.document_id else None,
            "demande_id": sig.demande_id,
            "demande_nom": (sig.demande.texte or "").strip()[:80] if sig.demande_id else None,
            "image_url": request.build_absolute_uri(sig.image.url),
            "membre_id": sig.membre_id, "modifiable": True,
        },
    })


@login_required
@require_http_methods(["POST"])
def api_signature_modifier(request, signature_id):
    """Modifie le nom et/ou l'état actif d'une signature existante (JSON)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    # Ouvert aux agents — voir api_signature_creer.

    sig = SignatureDocument.objects.filter(id=signature_id, entreprise=entreprise).first()
    agent = _agent_document(request, entreprise)
    if sig is not None and agent is not None and sig.membre_id != agent.id:
        return JsonResponse({"erreur": _i18n_t("rg2.views__part_008.2")}, status=403)
    if not sig:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:350:41")}, status=404)

    corps = json.loads(request.body or "{}")
    maj = []
    if "nom" in corps:
        sig.nom = str(corps["nom"] or "").strip()[:150]
        maj.append("nom")
    if "fonction" in corps:
        sig.fonction = " ".join(str(corps["fonction"] or "").split())[:150]
        maj.append("fonction")
    if "types_document" in corps:
        # Signataire par type de document : liste (ou texte « a,b ») de types connus ; vide = tous les types.
        brut = corps["types_document"]
        brut = brut.split(",") if isinstance(brut, str) else (brut if isinstance(brut, (list, tuple)) else [])
        valides = dict(DocumentAdministratif.TYPES_DOCUMENT)
        sig.types_document = ",".join(dict.fromkeys(str(x).strip() for x in brut if str(x).strip() in valides))
        maj.append("types_document")
    if "actif" in corps:
        sig.actif = bool(corps["actif"])
        maj.append("actif")
    if "table_id" in corps:
        table_id = corps["table_id"]
        if table_id in (None, "", 0, "0"):
            sig.table = None
        else:
            table_sig = TableDynamique.objects.filter(id=table_id, entreprise=entreprise).first()
            if not table_sig:
                return JsonResponse({"erreur": t("engine/views/_part_008.py:367:49")}, status=404)
            sig.table = table_sig
        maj.append("table")
    if "document_id" in corps:
        document_id = corps["document_id"]
        if document_id in (None, "", 0, "0"):
            sig.document = None
        else:
            document_sig = DocumentGenere.objects.filter(id=document_id, entreprise=entreprise).first()
            if not document_sig:
                return JsonResponse({"erreur": t("engine/views/_part_008.py:377:49")}, status=404)
            sig.document = document_sig
        maj.append("document")
    if maj:
        sig.save(update_fields=maj)
    return JsonResponse({
        "ok": True,
        "fonction": sig.fonction, "types_document": sig.liste_types(),
        "table_id": sig.table_id,
        "table_nom": sig.table.nom if sig.table_id else None,
        "document_id": sig.document_id,
        "document_nom": (sig.document.titre.strip() if sig.document.titre else f"{sig.document.get_type_document_display()} #{sig.document.id}") if sig.document_id else None,
    })


@login_required
@require_http_methods(["POST"])
def api_signature_supprimer(request, signature_id):
    """Supprime définitivement une signature (et son fichier image)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    # Ouvert aux agents — voir api_signature_creer.

    sig = SignatureDocument.objects.filter(id=signature_id, entreprise=entreprise).first()
    agent = _agent_document(request, entreprise)
    if sig is not None and agent is not None and sig.membre_id != agent.id:
        return JsonResponse({"erreur": _i18n_t("rg2.views__part_008.2")}, status=403)
    if not sig:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:403:41")}, status=404)
    nom_fichier = sig.image.name if sig.image else None
    sig.delete()
    if nom_fichier:
        try:
            from django.core.files.storage import default_storage
            if default_storage.exists(nom_fichier):
                default_storage.delete(nom_fichier)
        except Exception:
            pass
    return JsonResponse({"ok": True})



# ══════════════════════════════════════════════════════════════
# API DOCUMENTS : factures, reçus, bons, devis, etc.
# ══════════════════════════════════════════════════════════════

def _resume_enregistrement(enreg):
    """Résumé court d'un enregistrement (3 premières valeurs non nulles)."""
    if not enreg:
        return None
    items = [(k, v) for k, v in enreg.to_dict().items() if v is not None][:3]
    return " | ".join(f"{k}: {v}" for k, v in items)

def _serialiser_document(doc):
    return {
        "id":             doc.id,
        "type_document":  doc.type_document,
        "type_libelle":   doc.get_type_document_display(),
        "titre":          doc.titre,
        "instructions":   doc.instructions,
        "parametres":     doc.parametres or {},
        "statut":         doc.statut,
        "erreur":         doc.erreur,
        "script_genere":  doc.script_genere,
        "modifie_manuellement": doc.modifie_manuellement,
        "couleur":        doc.couleur,
        "resultat_json":  doc.resultat_json,
        "pdf_url":        doc.fichier_pdf.url if doc.fichier_pdf else None,
        "cree_le":        doc.cree_le.isoformat(),
        "execute_le":     doc.execute_le.isoformat() if doc.execute_le else None,
        "table_id":       doc.table_cible_id,
        "table_nom":      doc.table_cible.nom if doc.table_cible else None,
        "enregistrement_id":     doc.enregistrement_cible_id,
        "enregistrement_resume": _resume_enregistrement(doc.enregistrement_cible),
    }


@login_required
@require_http_methods(["GET"])
def api_documents_table(request, table_id):
    """Liste tous les documents d'une table (y compris une table mise à
    disposition en lecture seule via PartageInterAgent — voir _table_via_partage)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    table, _lecture_seule = _table_via_partage(request, table_id)
    docs = DocumentGenere.objects.filter(entreprise=table.entreprise, table_cible=table).order_by("-cree_le")
    return JsonResponse({"documents": [_serialiser_document(d) for d in docs]})


@login_required
@require_http_methods(["GET"])
def api_documents_liste(request):
    """Liste tous les documents de l'entreprise (toutes tables)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    docs = DocumentGenere.objects.filter(entreprise=entreprise).order_by("-cree_le")
    return JsonResponse({"documents": [_serialiser_document(d) for d in docs]})


@login_required
@require_http_methods(["POST"])
def api_creer_document(request):
    """
    Crée un document : génère le script IA puis exécute.
    Body JSON :
      table_id        (int, optionnel)
      type_document   (str, défaut "facture")
      titre           (str)
      instructions    (str)
      parametres      (dict, optionnel)
    """
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    corps = json.loads(request.body or "{}")

    table = None
    if corps.get("table_id"):
        table, _lecture_seule = _table_via_partage(request, corps["table_id"])

    # Entité cible — obligatoire pour la génération IA, optionnelle pour la carte manuelle
    enregistrement = None
    if corps.get("enregistrement_id"):
        from ..models import Enregistrement
        enregistrement = get_object_or_404(
            Enregistrement, id=corps["enregistrement_id"],
            table=table if table else None,
        )

    generer = corps.get("generer", False)

    doc = DocumentGenere.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        table_cible=table,
        enregistrement_cible=enregistrement,
        type_document=corps.get("type_document", "facture"),
        titre=corps.get("titre", "").strip(),
        instructions=corps.get("instructions", "").strip(),
        parametres=corps.get("parametres") or {},
        statut="script_genere" if not generer else "en_cours",
    )

    if not generer:
        # Carte vide : script éditable manuellement, aucun appel IA
        return JsonResponse({"document": _serialiser_document(doc)}, status=201)

    # Génération du script + exécution (PDF) déléguées à Celery : la
    # réponse HTTP part immédiatement avec statut="en_cours", le front
    # suit l'avancement via le polling api_statut_document.
    _taches.generer_et_executer_document_tache.delay(doc.id)
    return JsonResponse({"document": _serialiser_document(doc)}, status=201)


@login_required
@require_http_methods(["POST"])
def api_regenerer_document(request, doc_id):
    """Régénère le script IA et ré-exécute."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    doc = get_object_or_404(DocumentGenere, id=doc_id, entreprise=entreprise)
    corps = json.loads(request.body or "{}")
    # Permettre de mettre à jour les paramètres avant régénération
    if "instructions" in corps:
        doc.instructions = corps["instructions"].strip()
    if "parametres" in corps:
        doc.parametres = corps["parametres"] or {}
    if "titre" in corps:
        doc.titre = corps["titre"].strip()
    if "type_document" in corps:
        doc.type_document = corps["type_document"]
    doc.statut = "en_cours"
    doc.save()
    _taches.regenerer_document_tache.delay(doc.id)
    return JsonResponse({"document": _serialiser_document(doc)})


@login_required
@require_http_methods(["POST"])
def api_executer_document(request, doc_id):
    """Ré-exécute le script existant (données fraîches)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    doc = get_object_or_404(DocumentGenere, id=doc_id, entreprise=entreprise)
    doc.statut = "en_cours"
    doc.save(update_fields=["statut"])
    _taches.executer_document_tache.delay(doc.id)
    return JsonResponse({"document": _serialiser_document(doc)})


@login_required
@require_http_methods(["GET"])
def api_statut_document(request, doc_id):
    """Polling léger : même principe que api_statut_demande pour les
    analyses. Le front doit interroger cet endpoint après api_creer_document
    (generer=true), api_regenerer_document et api_executer_document, qui
    répondent désormais immédiatement avec statut="en_cours"."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    doc = get_object_or_404(DocumentGenere, id=doc_id, entreprise=entreprise)
    if doc.statut in ("execute", "echec", "echec_generation", "echec_execution", "script_genere"):
        return JsonResponse({"fini": True, "document": _serialiser_document(doc)})
    return JsonResponse({"fini": False, "statut": doc.statut})


@login_required
@require_http_methods(["POST"])
def api_modifier_script_document(request, doc_id):
    """Sauvegarde le script édité manuellement et re-exécute."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    doc = get_object_or_404(DocumentGenere, id=doc_id, entreprise=entreprise)
    corps = json.loads(request.body or "{}")
    nouveau_code = corps.get("code", "").strip()
    if not nouveau_code:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:604:41")}, status=400)
    doc = modifier_script_document(doc, nouveau_code)
    return JsonResponse({"document": _serialiser_document(doc)})


@login_required
@require_http_methods(["POST"])
def api_supprimer_document(request, doc_id):
    """Supprime un document et son PDF."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    import os
    entreprise = _entreprise_courante(request)
    doc = get_object_or_404(DocumentGenere, id=doc_id, entreprise=entreprise)
    # Nettoyer fichiers
    for chemin in [doc.fichier_script]:
        if chemin and os.path.exists(chemin):
            try: os.remove(chemin)
            except OSError: pass
    if doc.fichier_pdf:
        try: doc.fichier_pdf.delete(save=False)
        except Exception: pass
    doc.delete()
    return JsonResponse({"ok": True})


# ── Liaison inter-tables : dropdown ──────────────────────────────────────────
@login_required
@require_http_methods(["GET"])
def api_lignes_relation(request, table_id):
    """Retourne toutes les lignes et colonnes d'une table cible.
    Utilisé par le picker relation pour chercher et sélectionner une valeur.
    Réponse : { table_nom, champs:[{slug,nom,type}], lignes:[{_id, col1, col2...}] }
    """
    # Réservé à l'interface agents/admin (sélecteur de relation) : un client (lecteur) obtenait ici TOUTES
    # les lignes d'une table exposée — codes d'accès des autres clients compris —, contournant la règle
    # « recherche uniquement » de api_lignes.
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    champs = list(table.champs.all())
    from ..vue_groupe import enregistrements_vue   # relation vers un groupe = vers sa table générale (toutes ses parties)
    from .. import parquet_store   # valeurs lues dans le fichier Parquet (une lecture), pas dans l'EAV ligne par ligne
    enrs = list(enregistrements_vue(table).order_by("id"))
    lignes = [{"_id": enr.id, **v} for enr, v in zip(enrs, parquet_store.valeurs_lignes(table, enrs))]
    return JsonResponse({
        "table_nom": table.nom,
        "champs": [{"slug": c.slug, "nom": c.nom, "type": c.type_champ} for c in champs],
        "lignes": lignes,
    })


@login_required
@require_http_methods(["GET"])
def api_valeurs_colonne(request, table_id):
    """Champ « colonne » : valeurs distinctes ACTUELLES de la colonne `colonne` (les plus anciennes d'abord).
    `nominale` est exigée (même contrat que la configuration du champ) mais pas utilisée. Réservé agents/admin,
    comme api_lignes_relation : un client ne lit pas une autre table."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=_entreprise_courante(request))
    col, nominale = request.GET.get("colonne", ""), request.GET.get("nominale", "")
    slugs = set(table.champs.values_list("slug", flat=True))
    if col not in slugs or nominale not in slugs or col == nominale:
        return JsonResponse({"erreur": t("engine/views/_part_008.py:g:684:40")}, status=400)
    valeurs, vus = [], set()
    from ..vue_groupe import enregistrements_vue
    from .. import parquet_store
    enrs = list(enregistrements_vue(table).order_by("id")[:5000])
    for v in (d.get(col) for d in parquet_store.valeurs_lignes(table, enrs)):
        if v in (None, "") or str(v) in vus:
            continue
        vus.add(str(v))
        valeurs.append(str(v))
    return JsonResponse({"valeurs": valeurs})


# ── Accès client anonyme (sans inscription) ───────────────────────────────────

ECHECS_ACCES_CLIENT_PAR_HEURE = 100  # plafond TOUTES adresses IP confondues (le limiteur par IP seul se contourne en changeant d'IP)


def _cle_echecs_acces_client(entreprise):
    import time
    return f"acces_client_echecs:{entreprise.id}:{int(time.time() // 3600)}"


def _echecs_acces_client(entreprise):
    from django.core.cache import cache
    try:
        return int(cache.get(_cle_echecs_acces_client(entreprise)) or 0)
    except Exception:
        return 0  # cache indisponible : le service reste ouvert (comme ratelimit.py)


def _noter_echec_acces_client(entreprise):
    from django.core.cache import cache
    cle = _cle_echecs_acces_client(entreprise)
    try:
        try:
            cache.incr(cle)
        except ValueError:
            cache.set(cle, 1, timeout=3700)
    except Exception:
        pass


@limiter("acces_client", max_appels=10, periode_secondes=60, methodes={"POST"})
def vue_acces_client(request, slug_entreprise):
    """Point d'entrée client : saisie du code hex 6 caractères fourni par l'agent.

    Chaque CLIENT (chaque ligne d'une table cliente) a son propre code —
    plus de code unique partagé par toute la table (ancienne faille : tous
    les clients d'une même table cliente pouvaient se connecter avec le
    même code et donc, potentiellement, se faire passer les uns pour les
    autres). Le code identifie précisément l'entreprise, la table cliente
    ET le client (Enregistrement) correspondant. Le client se connecte sous
    son propre MembreEntreprise (role=lecteur), lié à la fois à la table
    cliente et à SA ligne (enregistrement_client) — voir models.py.
    """
    try:
        entreprise = Entreprise.objects.get(slug=slug_entreprise)
    except Entreprise.DoesNotExist:
        raise Http404
    from ..securite import noter_entreprise_publique
    noter_entreprise_publique(request, entreprise.slug)   # la page d'accès (et /marque.css) prend le thème, le logo et la langue de CETTE entreprise

    erreur = None

    if request.method == "POST":
        code = request.POST.get("code_acces", "").strip().upper()
        bloque = _echecs_acces_client(entreprise) >= ECHECS_ACCES_CLIENT_PAR_HEURE
        enregistrement = None if (bloque or not code) else Enregistrement.objects.filter(
            table__entreprise=entreprise, table__est_table_membres=True, table__est_table_produits=False,
            code_acces_client=code,
        ).select_related("table").first()
        if bloque:
            erreur = td("engine/views/_part_008.py:g:754:22")
        elif not enregistrement:
            erreur = "Code incorrect."
            _noter_echec_acces_client(entreprise)
        else:
            table = enregistrement.table
            # Le code fourni par l'agent EST l'identifiant de connexion du
            # client : on cree/retrouve son compte lecteur (lie a sa ligne
            # precise) et on le connecte directement, sans exiger de compte
            # prealable ni d'inscription separee.
            membre = MembreEntreprise.objects.filter(enregistrement_client=enregistrement).first()
            if not membre:
                from django.contrib.auth import get_user_model
                # Identifiant propre à la ligne (et non au code : un même code existe dans deux entreprises,
                # ce qui faisait échouer create_user en erreur 500) ; get_or_create : double-clic / compte orphelin.
                utilisateur_client, cree = get_user_model().objects.get_or_create(
                    username=f"client_e{entreprise.id}_r{enregistrement.id}")
                if cree:
                    utilisateur_client.set_unusable_password()
                    utilisateur_client.save(update_fields=["password"])
                membre, _ = MembreEntreprise.objects.get_or_create(
                    utilisateur=utilisateur_client, entreprise=entreprise,
                    defaults={"role": "lecteur", "table_cliente": table, "enregistrement_client": enregistrement},
                )
            membre.utilisateur.backend = "django.contrib.auth.backends.ModelBackend"
            login(request, membre.utilisateur)
            request.session["table_cliente_id"] = table.id
            return redirect("engine:publicites_client")   # le client arrive d'abord sur les publicités de son agent

    return render(request, "engine/acces_client.html", {
        "entreprise": entreprise,
        "erreur": erreur,
        "logo_url": entreprise.logo.url if entreprise.logo else None,
    })


# ── Espace client : formulaires exposés uniquement ────────────────────────────

@login_required
@ensure_csrf_cookie
def espace_client(request):
    """Page dédiée aux clients (lecteurs).
    Affiche uniquement les tables accessible_client=True (formulaire visible
    et/ou recherche visible — voir TableDynamique.accessible_client).
    Aucun accès au reste de l'application.
    """
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()

    # Sécurité : seuls les lecteurs accèdent à cette vue.
    # Les admins/agents sont redirigés vers l'interface complète.
    if not membre:
        return redirect("engine:login")
    if membre.role != "lecteur":
        return redirect("engine:interface_unique")

    # Restreindre à la table cliente identifiée par le code hex (session)
    table_cliente_id = request.session.get("table_cliente_id") or (
        membre.table_cliente_id if membre.table_cliente_id else None
    )
    # Le menu du client = les tables ordinaires que l'agent a mises à sa disposition (formulaire et/ou recherche).
    # La table des agents et clients (table de membres, dont la table cliente du code d'accès) lui est INTERDITE :
    # elle n'est jamais listée, elle reste réservée à l'agent. Avant, un code d'accès réduisait le menu à cette seule
    # table cliente, et les autres tables exposées n'apparaissaient jamais.
    tables = (
        TableDynamique.objects
        .filter(entreprise=entreprise)
        .filter(Q(expose_client=True) | Q(masquer_formulaire_client=True))
        .exclude(est_table_membres=True)
        .prefetch_related("champs")
        .order_by("nom")
    )

    tables_data = []
    for table in tables:
        if not _table_permise_client(membre, table):   # l'agent ne réserve pas cette table à ce client
            continue
        champs = list(table.champs.all().order_by("id"))
        tables_data.append({
            "id": table.id,
            "nom": table.nom,
            "description": table.description or "",
            "expose_client": table.expose_client,
            "masquer_formulaire_client": table.masquer_formulaire_client,
            "est_table_membres": table.est_table_membres,
            "structure_formulaire": _structure_formulaire_client(table),
            "theme_formulaire": _theme_formulaire_client(table),
            "champs": [
                {
                    "slug": c.slug,
                    "nom": c.nom,
                    "type": c.type_champ,
                    "obligatoire": c.obligatoire,
                    "choix": c.options_choix or "",
                }
                for c in champs
            ],
        })

    logo_url = None
    if entreprise.logo:
        logo_url = entreprise.logo.url

    # ── Mis à disposition en lecture seule : dashboards/KPI de LA table
    # cliente identifiée (si connecté via un code), graphe d'ontologie de
    # toute l'entreprise (transverse, pas lié à une seule table). ──
    # Tableaux de bord / KPI mis à disposition : ceux de la table cliente ET ceux des tables exposées (l'agent construit
    # souvent sa BI sur une table métier, pas sur la table des clients). Chaque tableau garde SA table pour le lien.
    dashboards_data, kpis_data = [], []
    from ..vue_groupe import ids_groupe
    _ids_bi = {t["id"] for t in tables_data}
    if table_cliente_id:
        _tc = TableDynamique.objects.filter(id=table_cliente_id, entreprise=entreprise).first()
        _ids_bi.update(ids_groupe(_tc) if _tc else [table_cliente_id])   # un groupe = UNE table : dashboards/KPI de toutes ses parties
    # Tout ce que l'agent a marqué « visible client » est listé, quelle que soit sa table source (exposée ou non) — sauf la table des
    # agents et clients d'un AUTRE client : seule la table propre du client (groupe de `_ids_bi`) reste permise.
    _portee_bi = Q(table_source_id__in=_ids_bi) | Q(table_source__est_table_membres=False)
    _permises_bi = _tables_permises_client(membre)
    if _permises_bi is not None:   # restriction par client : seules ses tables (et sa table propre) portent des tableaux et KPI
        _ids_ok = set(_permises_bi)
        for _t in TableDynamique.objects.filter(id__in=_permises_bi):
            _ids_ok.update(ids_groupe(_t))
        _ids_ok.update(ids_groupe(_tc) if table_cliente_id and _tc else ([table_cliente_id] if table_cliente_id else []))
        _portee_bi = Q(table_source_id__in=_ids_ok)
    dashboards_data = [
        {"id": t.id, "nom": t.nom, "description": t.description, "table_id": t.table_source_id}
        for t in TableauBordBI.objects.filter(_portee_bi, entreprise=entreprise, visible_client=True)
        .order_by("ordre", "id")
    ]
    kpis_data = [
        _serialiser_kpi(k) for k in
        IndicateurKPI.objects.filter(_portee_bi, entreprise=entreprise, visible_client=True)
    ]
    # Pages d'analyse (carte, séries, statistiques, ANOVA, analyse factorielle) : elles analysent des DONNÉES, donc s'ouvrent
    # sur une table exposée — jamais sur la table des clients, que le serveur refuse (lien mort auparavant). Chaque page
    # propose ensuite les autres tables exposées.
    table_analyses_id = tables_data[0]["id"] if tables_data else None
    from ..wallet import pages_client_fermees as _pcf
    _pages_fermees_client = _pcf(entreprise)   # pages que l'agent n'ouvre pas à ses clients
    if "dashboard_bi" in _pages_fermees_client:
        dashboards_data, kpis_data = [], []
    _analyses_ouvertes = bool(table_analyses_id) and not {"geolocalisation", "series_temporelles", "statistiques", "anova", "analyse_factorielle"} <= _pages_fermees_client
    pages_ontologie_data = [] if "ontologie" in _pages_fermees_client else [
        _serialiser_page_ontologie(p) for p in
        PageOntologie.objects.filter(entreprise=entreprise, visible_client=True)
    ]
    modeles_ia_data = [
        {"id": m.id, "nom": m.nom, "type_libelle": _ia.LIBELLES_TYPES.get(m.type_modele, m.type_modele)}
        for m in ModeleIA.objects.filter(entreprise=entreprise, visible_client=True, statut="entraine")
    ]

    from .. import marque as _marque
    return render(request, "engine/client.html", {
        "entreprise": entreprise,
        "nom_affiche": _marque.nom_affiche(entreprise),
        "tables": tables_data,
        "dashboards": dashboards_data,
        "kpis": kpis_data,
        "pages_ontologie": pages_ontologie_data,
        "modeles_ia": modeles_ia_data,
        "table_cliente_id": table_cliente_id,
        "table_analyses_id": table_analyses_id,
        "pages_fermees": _pages_fermees_client,
        "analyses_ouvertes": _analyses_ouvertes,
        "membre": membre,
        "logo_url": logo_url,
    })


@login_required
@ensure_csrf_cookie
def vue_paiement_config(request):
    """Page de configuration agent du paiement USSD (opérateurs, numéro de
    dépôt, services payants) — même contrôle d'accès que les autres pages
    de configuration (agents/admin uniquement, voir _interdit_lecteur)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    return render(request, "engine/paiement_config.html", {
        "entreprise": _entreprise_courante(request),
        "est_super_admin": request.user.is_superuser,   # seul le super administrateur Django voit et configure les opérateurs
        "evenements_taxe": [("depot", t("engine/views/_part_008.py:902:39")), ("retrait", t("engine/views/_part_008.py:902:61")), ("transfert", t("engine/views/_part_008.py:902:87"))],
    })


@login_required
@ensure_csrf_cookie
def vue_wallets_clients(request):
    """Page de visualisation des wallets clients — jusqu'ici impossible :
    seul le wallet du membre connecté était consultable (api/wallet/).
    Réservée aux agents/admin (voir _interdit_lecteur) ; le contenu réel
    (liste + soldes, filtrée par agent le cas échéant) vient de
    api/wallet/clients (views_wallet.wallets_clients)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    return render(request, "engine/wallets_clients.html", {
        "entreprise": _entreprise_courante(request),
    })


@login_required
@ensure_csrf_cookie
def vue_paiement_client(request):
    """Page dédiée client : vérification de dépôt (paiement USSD).

    Coordonnées (nom, téléphone) tirées du MembreEntreprise déjà en base —
    aucune ressaisie demandée au client. Le numéro utilisé pour vérifier le
    dépôt peut être surchargé côté formulaire si le client a payé depuis un
    autre numéro que celui enregistré.
    """
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if not membre:
        return redirect("engine:login")

    return render(request, "engine/paiement_client.html", {
        "entreprise": entreprise,
        "membre": membre,
    })


# ─────────────────────────────────────────────────────────────
# ENTRAINEMENT / INFERENCE IA — page dediee (engine/ia.html) + API.
# Toute la logique lourde (introspection des parametres, resolution des
# tables en dataset, entrainement, inference) vit dans ia_integration.py ;
# ces vues ne font que reutiliser les memes controles d'acces que le reste
# de l'API (_entreprise_courante, _interdit_lecteur) et serialiser.
# ─────────────────────────────────────────────────────────────
def _lire_plage_lignes(brut):
    """Valide la plage de lignes d'entrainement saisie ({debut, fin}, positions 1-based, bornes incluses, fin facultative).
    Retourne le dict a memoriser ({} = toutes les lignes) ; leve ValueError (message i18n) si la plage est invalide."""
    from ..contraintes_ia import LIMITE_LIGNES_ENTRAINEMENT as _max
    brut = brut if isinstance(brut, dict) else {}
    def entier(v):
        if v in (None, ""):
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ValueError(_i18n_t("engine/views/_part_008.py:plage_invalide"))
        if n < 1:
            raise ValueError(_i18n_t("engine/views/_part_008.py:plage_invalide"))
        return n
    debut, fin = entier(brut.get("debut")), entier(brut.get("fin"))
    if debut is None and fin is None:
        return {}
    debut = debut or 1
    if fin is not None and fin < debut:
        raise ValueError(_i18n_t("engine/views/_part_008.py:plage_invalide"))
    if fin is not None and fin - debut + 1 > _max:
        raise ValueError(_i18n_tf("engine/views/_part_008.py:plage_trop_grande", v1=_max))
    if debut == 1 and fin is None:
        return {}
    return {"debut": debut, **({"fin": fin} if fin is not None else {})}


def _serialiser_modele_ia(m):
    return {
        "id": m.id,
        "nom": m.nom,
        "slug": m.slug,
        "type_modele": m.type_modele,
        "type_libelle": _ia.LIBELLES_TYPES.get(m.type_modele, m.type_modele),
        "tables_source": [t.id for t in m.tables_source.all()],
        "tables_noms": [t.nom for t in m.tables_source.all()],
        "colonne_cible": m.colonne_cible,
        "colonnes_entree": m.colonnes_entree,
        "colonnes_sortie": m.colonnes_sortie,
        "parametres": m.parametres,
        "statut": m.statut,
        "erreur": m.erreur,
        "metriques": m.metriques,
        "progression": m.progression,
        "structure_entree": m.structure_entree,
        "visible_client": m.visible_client,
        "lignes_exclues": m.lignes_exclues or {},
        "plage_lignes": m.plage_lignes or {},
        "nb_lignes_exclues": sum(len(v) for v in (m.lignes_exclues or {}).values()),
        "a_un_fichier": bool(m.fichier_modele),
        "finetune_possible": bool(m.fichier_modele) and _ia.finetune_supporte(m.type_modele),
        "nouvelles_lignes": (_ia.compter_nouvelles_lignes(m)
                             if m.statut != "entrainement" and _ia.finetune_supporte(m.type_modele) else 0),
        "en_pause": bool(m.demande_pause) and m.statut == "entrainement",
        "cree_le": m.cree_le.isoformat(),
        "entraine_le": m.entraine_le.isoformat() if m.entraine_le else None,
    }


@login_required
def vue_ia(request):
    """Page dediee a l'entrainement / l'inference de modeles IA."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if membre and membre.role == "lecteur":
        return redirect("engine:espace_client")
    if not _vue_autorisee(membre, "ia"):
        return redirect('/profile/')
    peut_ajouter_algo = bool(request.user.is_superuser)   # critique : super-administrateur seulement (delegation.CRITIQUES)
    return render(request, "engine/ia.html", {"entreprise": entreprise, "peut_ajouter_algo": peut_ajouter_algo})


@login_required
def api_ia_types_modeles(request):
    """Liste des 9 types de modeles IA avec TOUS leurs hyperparametres,
    obtenue par introspection reelle des fonctions train_xxx (aucun
    parametre code en dur cote Django)."""
    bloque = _interdit_lecteur(request)
    if bloque:
        return bloque
    try:
        return JsonResponse({"types": _ia.types_modeles_disponibles()})
    except Exception as e:
        return JsonResponse({"erreur": _i18n_tf("engine/views/_part_008.py:g:ia_indisponible", v1=e)}, status=500)


def _structure_formulaire_client(table):
    from .. import formulaire_conditionnel as _fc
    return _fc.structure_pour_client(table)


def _theme_formulaire_client(table):
    from .. import formulaire_conditionnel as _fc
    return _fc.theme_pour_client(table)


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
