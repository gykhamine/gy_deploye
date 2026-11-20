"""Documents administratifs : entête officiel (gauche / droite) et éditeur de documents (titre + corps).

  * /documents-administratifs/                      page de l'éditeur ;
  * /api/entete-officiel/                           lecture / enregistrement de l'entête (administrateur : lignes imposées,
                                                    agent : complément à la suite, jamais à la place) ;
  * /api/documents-administratifs/[<id>/[pdf/]]     liste, création, lecture, modification, suppression, PDF ;
  * /api/documents-administratifs/modeles-entete/   modèles d'entête nommés (lecture : tous ; écriture : administrateur) ;
  * /api/documents-administratifs/modeles/          modèles de documents (lecture : tous ; écriture : administrateur).
Mise en page et PDF : entete_officiel.py, reports/_part_005.py. Cachet et signatures : réglages du contexte « courrier ».
"""
from engine.i18n import t
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from ..delegation import a_pouvoir as _a_pouvoir, a_un_pouvoir_de as _a_un_pouvoir_de
from ._header import *  # noqa
from ._part_001 import *  # noqa
from ._part_007 import *  # noqa
from ._part_008 import *  # noqa

from .. import entete_officiel as _entete
from .. import emission_documents as _emission
from .. import gestion_documents as _gestion
from .. import modeles_documents as _modeles
from ..ratelimit import adresse_ip_client, limiter
from ..models import DocumentAdministratif, DocumentFigeError, EnteteOfficiel, ModeleDocument, ModeleEntete, ReglageEntete
from engine.i18n import t as _gt  # noqa: E402

SLUG_VUE_DOCUMENTS_ADMINISTRATIFS = "documents_administratifs"


def _membre_courant(request, entreprise):
    return request.user.membreentreprise_set.filter(entreprise=entreprise).first()


@login_required
def vue_documents_administratifs(request):
    """Page de l'éditeur : liste des documents, titre, corps (titres / sous-titres / paragraphes), aperçu de l'entête, PDF."""
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    refus = _interdit_vue(request, SLUG_VUE_DOCUMENTS_ADMINISTRATIFS)
    if refus:
        return refus
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not membre:
        return redirect("/profile/")
    return render(request, "engine/documents_administratifs.html", {"entreprise": entreprise, "est_admin": _a_un_pouvoir_de(membre, "documents")})


# ══════════════════════════════════════════════════════════════
#  ENTÊTE OFFICIEL
# ══════════════════════════════════════════════════════════════
def _lignes_de(entete):
    return {"gauche": _entete.normaliser_lignes(entete.lignes_gauche) if entete else [],
            "droite": _entete.normaliser_lignes(entete.lignes_droite) if entete else []}


def _reglages_de(entreprise, membre):
    """Réglages personnels (interrupteur des en-têtes, numérotation) + le numéro que l'on imprimerait maintenant (sans consommer)."""
    from django.utils import timezone
    r = _entete.reglage_de(entreprise.id, membre.id)
    d = {"entetes_actifs": True if r is None else r.entetes_actifs,
         "numerotation_active": bool(r and r.numerotation_active),
         "numerotation_modele": r.numerotation_modele if r else "",
         "numerotation_auto": bool(r and r.numerotation_auto),
         "dernier": r.numerotation_dernier if r and r.numerotation_annee == timezone.localdate().year else 0}
    try:
        d["exemple"] = _entete.numero_apercu(entreprise.id, membre.id)
    except Exception:       # modèle invalide : l'exemple est simplement vide (la validation a lieu à l'enregistrement)
        d["exemple"] = ""
    return d


def _enregistrer_reglages(entreprise, membre, brut, utilisateur, ip=""):
    """Applique `brut` ({entetes_actifs?, numerotation_active?, numerotation_modele?, numerotation_auto?}) aux réglages de `membre`.
    Retourne le message d'erreur (rien n'est alors enregistré) ou ''."""
    if not isinstance(brut, dict):
        return ""
    r, _ = ReglageEntete.objects.get_or_create(entreprise=entreprise, membre=membre)
    avant = _reglages_de(entreprise, membre)
    modele = " ".join(str(brut.get("numerotation_modele", r.numerotation_modele) or "").split())
    auto = bool(brut["numerotation_auto"]) if "numerotation_auto" in brut else r.numerotation_auto
    active = bool(brut["numerotation_active"]) if "numerotation_active" in brut else r.numerotation_active
    if active and not modele:
        return t("engine/views/_part_012.py:numerotation_vide")
    if modele:
        erreur = _entete.valider_numerotation(modele, auto)
        if erreur:
            return erreur
    if "entetes_actifs" in brut:
        r.entetes_actifs = bool(brut["entetes_actifs"])
    r.numerotation_active, r.numerotation_auto, r.numerotation_modele = active, auto, modele[:_entete.MAX_TEXTE]
    r.save()
    apres = _reglages_de(entreprise, membre)
    if apres != avant:
        from ..models import JournalAudit
        try:
            JournalAudit.enregistrer(
                entreprise=entreprise, utilisateur=utilisateur, action="document_administratif.entete_reglages", source="api",
                cible_type="reglage_entete", cible_id=r.pk, ip=ip or "", details={"role": membre.role},
                ancienne_valeur=avant, nouvelle_valeur=apres)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(_gt("rg5.msg.064"))
    return ""


@login_required
@require_http_methods(["GET", "POST"])
def api_entete_officiel(request):
    """GET : lignes imposées (modèle d'entête par défaut) + complément de l'agent connecté. POST {gauche?, droite?} : enregistre
    les lignes de l'utilisateur — celles du MODÈLE PAR DÉFAUT s'il est administrateur, SES lignes de complément s'il est agent.
    Un agent ne peut donc jamais modifier ni retirer ce que l'administrateur impose (il n'écrit que dans son propre jeu)."""
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    est_agent = membre is not None and membre.role == "agent"
    est_admin = _a_pouvoir(membre, "documents_modeles")
    if not est_agent and not est_admin:
        return JsonResponse({"erreur": t("engine/views/_part_012.py:acces_refuse")}, status=403)

    if request.method == "POST":
        try:
            corps = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"erreur": t("engine/views/_part_012.py:corps_invalide")}, status=400)
        if "reglages" in corps:
            erreur = _enregistrer_reglages(entreprise, membre, corps["reglages"], request.user, ip=_ip(request))
            if erreur:
                return JsonResponse({"erreur": erreur}, status=400)
        if est_agent:
            cible, _ = EnteteOfficiel.objects.get_or_create(entreprise=entreprise, membre=membre)
            maj = []
            avant = {"gauche": cible.lignes_gauche, "droite": cible.lignes_droite}
            for cle, champ in (("gauche", "lignes_gauche"), ("droite", "lignes_droite")):
                if cle in corps:
                    setattr(cible, champ, _entete.normaliser_lignes(corps[cle]))
                    maj.append(champ)
            if maj:
                cible.save(update_fields=maj + ["modifie_le"])
                if avant != {"gauche": cible.lignes_gauche, "droite": cible.lignes_droite}:
                    _emission.journaliser_entete(entreprise, request.user, membre, cible, avant, ip=_ip(request))
        else:
            # Administrateur : les lignes imposées sont celles du MODÈLE D'ENTÊTE PAR DÉFAUT (créé au besoin). Les autres
            # modèles se gèrent dans la carte « Modèles » de la page des documents administratifs.
            saisie = {f"lignes_{cote}": corps[cote] for cote in ("gauche", "droite") if cote in corps}
            if saisie:
                _modeles.modifier_entete(_modeles.entete_par_defaut_ou_creer(entreprise, request.user, ip=_ip(request)),
                                         request.user, saisie, ip=_ip(request))

    imposee = _entete.modele_entete(entreprise.id)           # lignes imposées = modèle d'entête par défaut
    perso = EnteteOfficiel.objects.filter(entreprise=entreprise, membre=membre).first() if est_agent else None
    imposees, perso_lignes = _lignes_de(imposee), _lignes_de(perso)
    return JsonResponse({
        "est_admin": est_admin, "est_agent": est_agent,
        "imposees": imposees,                       # administrateur : modifiables ; agent : lecture seule
        "perso": perso_lignes if est_agent else None,
        "apercu": {"gauche": _entete.fusionner(imposees["gauche"], perso_lignes["gauche"]),
                   "droite": _entete.fusionner(imposees["droite"], perso_lignes["droite"])},
        "limites": {"lignes": _entete.MAX_LIGNES, "texte": _entete.MAX_TEXTE},
        "reglages": _reglages_de(entreprise, membre),
    })


# ══════════════════════════════════════════════════════════════
#  DOCUMENTS ADMINISTRATIFS
# ══════════════════════════════════════════════════════════════
def _documents_visibles(entreprise, membre):
    """Administrateur : tous les documents de l'entreprise. Agent : les siens."""
    qs = DocumentAdministratif.objects.filter(entreprise=entreprise)
    if membre is not None and membre.role == "agent":
        qs = qs.filter(auteur=membre)
    return qs


def _ip(request):
    return adresse_ip_client(request)


def _serialiser_document(d, complet=True, membre=None):
    from ..reports._part_005 import normaliser_corps
    base = {"id": d.id, "titre": d.titre, "nom": d.nom, "libelle": d.libelle, "modifie_le": d.modifie_le.isoformat(), "cree_le": d.cree_le.isoformat(),
            "auteur": (d.auteur.utilisateur.get_full_name() or d.auteur.utilisateur.get_username()) if d.auteur_id else "",
            "statut": d.statut, "reference": d.reference, "lieu": d.lieu,
            "date_emission": d.date_emission.isoformat() if d.date_emission else "",
            "version_de": d.version_de_id, "motif_annulation": d.motif_annulation,
            "type_document": d.type_document, "delegation": d.delegation, "date_position": d.date_position, "motif_refus": d.motif_refus,
            "archive": d.archive, "est_auteur": membre is not None and d.auteur_id == membre.id,
            "modele_entete": d.modele_entete_id}
    if complet:
        base["corps"] = normaliser_corps(d.corps)
        base["verrou"] = _emission.description_verrou(d, membre) if d.statut == DocumentAdministratif.STATUT_BROUILLON else None
    return base


def _erreur_emission(exc):
    statut = {"interdit": 403, "validation_requise": 403, "integrite": 500, "verrouille": 423}.get(
        exc.code, 409 if exc.code in ("deja_emis", "statut") else 400)
    return JsonResponse({"erreur": str(exc), "code": exc.code}, status=statut)


def _appliquer_saisie(d, corps):
    """Titre et corps saisis -> document (bornés, nettoyés). Retourne la liste des champs modifiés. Lève ErreurModele si
    l'entête choisi n'est pas un modèle de l'entreprise."""
    from ..reports._part_005 import normaliser_corps, MAX_TITRE
    maj = []
    if "titre" in corps:
        d.titre = " ".join(str(corps.get("titre") or "").split())[:MAX_TITRE]
        maj.append("titre")
    if "corps" in corps:
        d.corps = normaliser_corps(corps.get("corps"))
        maj.append("corps")
    if "lieu" in corps:
        d.lieu = " ".join(str(corps.get("lieu") or "").split())[:120]
        maj.append("lieu")
    if "type_document" in corps:
        d.type_document = DocumentAdministratif.normaliser_type(corps.get("type_document"))
        maj.append("type_document")
    if "delegation" in corps:
        d.delegation = DocumentAdministratif.normaliser_delegation(corps.get("delegation"))
        maj.append("delegation")
    if "date_position" in corps:
        d.date_position = DocumentAdministratif.normaliser_date_position(corps.get("date_position"))
        maj.append("date_position")
    if "modele_entete" in corps:
        # Entête choisi (vide = modèle par défaut). Seuls les brouillons arrivent ici : un document émis ou à valider est refusé plus haut.
        d.modele_entete = _modeles.entete_de_l_entreprise(d.entreprise_id, corps.get("modele_entete"))
        maj.append("modele_entete")
    return maj


def _contexte_documents(request):
    """(refus, entreprise, membre) — refus = réponse 403 si l'accès est interdit, sinon None."""
    refus = _interdit_lecteur(request)
    if refus:
        return refus, None, None
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if membre is None or membre.role not in ("admin", "agent"):
        return JsonResponse({"erreur": t("engine/views/_part_012.py:acces_refuse")}, status=403), None, None
    return None, entreprise, membre


def _erreur_modele(exc):
    """ErreurModele -> réponse JSON traduite (400 par défaut, 409 pour un nom déjà pris)."""
    return JsonResponse({"erreur": t("engine/views/_part_012.py:modele_" + exc.code), "code": exc.code}, status=exc.statut)


def _saisie_json(request):
    """Corps JSON de la requête, toujours un dictionnaire (corps absent, invalide ou d'un autre type : {})."""
    corps = _corps_json(request)
    return corps if isinstance(corps, dict) else {}


@login_required
@require_http_methods(["GET", "POST"])
def api_documents_administratifs(request):
    """GET : liste (sans le corps). POST {titre, corps} : crée un document, l'utilisateur en est l'auteur."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    if request.method == "GET":
        # Recherche et filtres (numéro / titre, statut, type, période, archives) sur les seuls documents VISIBLES par l'utilisateur.
        visibles = _gestion.filtrer(_documents_visibles(entreprise, membre), request.GET)
        total = visibles.count()
        lignes = visibles.select_related("auteur__utilisateur")[:_gestion.limite(request.GET)]
        return JsonResponse({"documents": [_serialiser_document(d, complet=False, membre=membre) for d in lignes],
                             "total": total})
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views/_part_012.py:corps_invalide")}, status=400)
    if not isinstance(corps, dict):
        corps = {}
    d = DocumentAdministratif(entreprise=entreprise, auteur=membre)
    modele_doc = None
    if corps.get("modele_document"):
        # « Nouveau à partir d'un modèle » : titre, corps, type et entête repris du modèle ; variables remplacées MAINTENANT
        # (création du brouillon), jamais à l'émission. {destinataire} / {objet} : ce que l'agent a saisi, sinon la variable reste.
        try:
            modele_doc = ModeleDocument.objects.filter(entreprise=entreprise, pk=int(corps["modele_document"])).first()
        except (TypeError, ValueError):
            modele_doc = None
        if modele_doc is None:
            return JsonResponse({"erreur": t("engine/views/_part_012.py:modele_introuvable"), "code": "introuvable"}, status=404)
        corps = _modeles.brouillon_depuis_modele(modele_doc, _modeles.valeurs_creation(entreprise, membre, corps))
    try:
        _appliquer_saisie(d, corps)
    except _modeles.ErreurModele as exc:
        return _erreur_modele(exc)
    d.save()
    _emission.journaliser(d, membre, "creer", ip=_ip(request),
                          details={"modele_document": modele_doc.pk, "modele_document_nom": modele_doc.nom} if modele_doc is not None else None)
    return JsonResponse(_serialiser_document(d, membre=membre), status=201)


@login_required
@require_http_methods(["GET", "PUT", "DELETE"])
def api_document_administratif(request, doc_id):
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    d = _documents_visibles(entreprise, membre).filter(id=doc_id).select_related("auteur__utilisateur").first()
    if d is None:
        return JsonResponse({"erreur": t("engine/views/_part_012.py:introuvable")}, status=404)
    # Suppression d'un document déjà émis/annulé : réservée à l'administrateur (les agents gardent le refus 409).
    suppression_emis = request.method == "DELETE" and d.est_fige and _a_pouvoir(membre, "documents_annuler")
    if request.method in ("PUT", "DELETE") and d.est_fige and not suppression_emis:
        return JsonResponse({"erreur": t("engine/views/_part_012.py:doc_emis_fige"), "code": "fige"}, status=409)
    if request.method in ("PUT", "DELETE") and d.statut == DocumentAdministratif.STATUT_SOUMIS:
        return JsonResponse({"erreur": t("engine/views/_part_012.py:doc_soumis_fige"), "code": "soumis"}, status=409)
    if request.method in ("PUT", "DELETE") and not suppression_emis:
        try:
            _emission.verifier_verrou_ecriture(d, membre)       # quelqu'un d'autre édite ce brouillon : on n'écrase rien
        except _emission.ErreurEmission as exc:
            return _erreur_emission(exc)
    if request.method == "DELETE":
        if suppression_emis:      # trace avant effacement : référence et statut ne seront plus lisibles ensuite
            from ..models import JournalAudit
            try:
                JournalAudit.enregistrer(
                    entreprise=entreprise, utilisateur=request.user, action="document_administratif.supprime_emis", source="api",
                    cible_type="document_administratif", cible_id=d.pk, ip=adresse_ip_client(request),
                    details={"reference": d.reference, "titre": d.titre, "statut": d.statut})
            except Exception:
                import logging
                logging.getLogger(__name__).exception(_gt("rg5.msg.065"))
        d.delete(force=suppression_emis)
        return JsonResponse({"ok": True})
    if request.method == "PUT":
        try:
            corps = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"erreur": t("engine/views/_part_012.py:corps_invalide")}, status=400)
        try:
            maj = _appliquer_saisie(d, corps if isinstance(corps, dict) else {})
        except _modeles.ErreurModele as exc:
            return _erreur_modele(exc)
        if maj:
            try:
                d.save(update_fields=maj + ["modifie_le"])
            except DocumentFigeError:
                return JsonResponse({"erreur": t("engine/views/_part_012.py:doc_emis_fige"), "code": "fige"}, status=409)
    return JsonResponse(_serialiser_document(d, membre=membre))


def _nom_fichier_pdf(d):
    base = d.reference or d.titre or ""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-")[:60] or "document"


@login_required
@require_http_methods(["GET"])
def api_document_administratif_pdf(request, doc_id):
    """Brouillon : épreuve filigranée (jamais une pièce officielle). Émis ou annulé : le PDF FIGÉ à l'émission, octet pour
    octet, après contrôle de son empreinte SHA-256 — jamais régénéré. Chaque téléchargement est inscrit au journal.
    `?apercu=1` : même PDF servi « inline » pour l'aperçu intégré à la page de l'éditeur."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    d = _documents_visibles(entreprise, membre).filter(id=doc_id).first()
    if d is None:
        return JsonResponse({"erreur": t("engine/views/_part_012.py:introuvable")}, status=404)
    if d.est_fige:
        try:
            pdf = _emission.octets_figes(d)
        except _emission.ErreurEmission as exc:
            _emission.journaliser(d, membre, "integrite_echec", ip=_ip(request), details={"reference": d.reference}, resultat="echec")
            return _erreur_emission(exc)
    else:
        from ..reports import generer_pdf_document_administratif
        pdf = generer_pdf_document_administratif(d)
    apercu = request.GET.get("apercu") == "1"     # affichage dans la page (iframe) plutôt que téléchargement
    if d.est_fige or not apercu:                  # l'épreuve d'un brouillon vue à l'écran n'est pas une sortie de document
        _emission.journaliser(d, membre, "telecharger", ip=_ip(request),
                              details={"statut": d.statut, "reference": d.reference, "sha256": d.empreinte_sha256})
    reponse = HttpResponse(pdf, content_type="application/pdf")
    reponse["Content-Disposition"] = f'{"inline" if apercu else "attachment"}; filename="{_nom_fichier_pdf(d)}.pdf"'
    return reponse


def _document_pour_action(request, doc_id):
    """(refus, document, membre) pour les actions du cycle de vie : même visibilité que le reste (admin : tous, agent : les siens)."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus, None, None
    d = _documents_visibles(entreprise, membre).filter(id=doc_id).select_related("entreprise").first()
    if d is None:
        return JsonResponse({"erreur": t("engine/views/_part_012.py:introuvable")}, status=404), None, None
    return None, d, membre


def _corps_json(request):
    try:
        return json.loads(request.body or "{}")
    except ValueError:
        return {}


@login_required
@require_http_methods(["POST"])
def api_document_administratif_emettre(request, doc_id):
    """POST {lieu?} : émet le brouillon (numéro, date, lieu, PDF figé + SHA-256, QR). Irréversible."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    from django.urls import reverse
    try:
        emis = _emission.emettre(
            d, membre, lambda code: request.build_absolute_uri(reverse("engine:verifier_document", args=[code])),
            lieu=_corps_json(request).get("lieu"), ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(emis, membre=membre))


@login_required
@require_http_methods(["POST"])
def api_document_administratif_annuler(request, doc_id):
    """POST {motif} : annule un document émis (administrateur). Le numéro reste attribué, le PDF reste archivé."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    try:
        annule = _emission.annuler(d, membre, _corps_json(request).get("motif"), ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(annule, membre=membre))


@login_required
@require_http_methods(["POST"])
def api_document_administratif_corriger(request, doc_id):
    """POST : crée (ou rouvre) le brouillon de la version corrigée d'un document émis ; renvoie ce nouveau brouillon."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    try:
        copie = _emission.corriger(d, membre, ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(copie, membre=membre), status=201)


@login_required
@require_http_methods(["POST"])
def api_document_administratif_soumettre(request, doc_id):
    """POST : brouillon -> à valider (auteur ou administrateur). L'administrateur l'émet (valide) ou le renvoie."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    try:
        soumis = _emission.soumettre(d, membre, ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(soumis, membre=membre))


@login_required
@require_http_methods(["POST"])
def api_document_administratif_refuser(request, doc_id):
    """POST {motif} : renvoie un document à valider en brouillon (administrateur, motif obligatoire)."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    try:
        renvoye = _emission.refuser(d, membre, _corps_json(request).get("motif"), ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(renvoye, membre=membre))


@login_required
@require_http_methods(["POST"])
def api_document_administratif_rappeler(request, doc_id):
    """POST : l'auteur reprend son document encore à valider (retour en brouillon, sans motif)."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    try:
        repris = _emission.rappeler(d, membre, ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(repris, membre=membre))


@login_required
@require_http_methods(["POST", "DELETE"])
def api_document_administratif_verrou(request, doc_id):
    """POST : prend ou renouvelle le verrou d'édition du brouillon (423 + nom de l'éditeur s'il est tenu par un autre).
    DELETE : libère le verrou (sans effet s'il est à quelqu'un d'autre)."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    if request.method == "DELETE":
        _emission.liberer_verrou(d, membre)
        return JsonResponse({"ok": True})
    try:
        jusqu_a = _emission.prendre_verrou(d, membre)
    except _emission.ErreurEmission as exc:
        reponse = _erreur_emission(exc)
        if exc.code == "verrouille":
            d.refresh_from_db()
            info = _emission.description_verrou(d, membre)
            if info:
                donnees = json.loads(reponse.content)
                donnees["par"] = info["par"]
                return JsonResponse(donnees, status=423)
        return reponse
    return JsonResponse({"ok": True, "jusqu_a": jusqu_a.isoformat(), "duree": _emission.DUREE_VERROU})


@login_required
@require_http_methods(["POST"])
def api_document_administratif_archiver(request, doc_id):
    """POST {archive: true|false} : archive (ou ressort des archives) un document émis ou annulé — administrateur."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    try:
        archive = _emission.archiver(d, membre, archive=bool(_corps_json(request).get("archive", True)), ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(archive, membre=membre))


@login_required
@require_http_methods(["POST"])
def api_document_administratif_renommer(request, doc_id):
    """POST {nom} : renomme le document à tout moment (même émis ou archivé) ; ne touche ni au titre ni au PDF figé."""
    refus, d, membre = _document_pour_action(request, doc_id)
    if refus:
        return refus
    try:
        doc = _emission.renommer(d, membre, _corps_json(request).get("nom"), ip=_ip(request))
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    return JsonResponse(_serialiser_document(doc, membre=membre))


@login_required
@require_http_methods(["GET"])
def api_documents_administratifs_export(request):
    """GET (mêmes filtres que la liste, ou ?ids=1,2,3) : archive ZIP des PDF FIGÉS des documents émis ou annulés visibles
    + index.csv (référence, statut, date, lieu, empreinte). Brouillons et documents à valider ne sont jamais exportés."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    params = request.GET.copy()
    if not params.get("archive"):
        params["archive"] = "tout"                     # un export en lot couvre aussi les archives sauf demande contraire
    documents = list(_gestion.filtrer(_documents_visibles(entreprise, membre), params)
                     .filter(statut__in=("emis", "annule")).select_related("entreprise").order_by("date_emission", "id")[:_gestion.LIMITE_MAX + 1])
    try:
        octets, n_pdf, n_anomalies = _gestion.exporter_zip(documents)
    except _emission.ErreurEmission as exc:
        return _erreur_emission(exc)
    from django.utils import timezone
    from ..models import JournalAudit
    try:
        JournalAudit.enregistrer(
            entreprise=entreprise, utilisateur=request.user, action="document_administratif.exporter_lot", source="api",
            cible_type="document_administratif", ip=_ip(request), resultat="succes" if not n_anomalies else "partiel",
            details={"documents": [d.pk for d in documents][:200], "pdf": n_pdf, "anomalies": n_anomalies})
    except Exception:
        import logging
        logging.getLogger(__name__).exception(_gt("engine/views/_part_012.py:568:47"))
    reponse = HttpResponse(octets, content_type="application/zip")
    reponse["Content-Disposition"] = f'attachment; filename="documents-{timezone.localdate().isoformat()}.zip"'
    return reponse


@login_required
@require_http_methods(["GET", "POST"])
def api_reglages_emission(request):
    """GET : format du numéro et lieu par défaut (lecture pour tous, avec un exemple de rendu). POST : administrateur seulement."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    if request.method == "POST":
        if not _a_pouvoir(membre, "documents_modeles"):
            return JsonResponse({"erreur": t("engine/views/_part_012.py:acces_refuse")}, status=403)
        corps = _corps_json(request)
        erreur = _emission.enregistrer_reglages(entreprise, corps.get("format_reference"), corps.get("lieu_emission"),
                                                validation=corps.get("validation_obligatoire"))
        if erreur:
            return JsonResponse({"erreur": erreur, "code": "format_invalide"}, status=400)
    modele, lieu = _emission.reglages_emission(entreprise)
    from django.utils import timezone
    return JsonResponse({"format_reference": modele, "lieu_emission": lieu, "est_admin": _a_pouvoir(membre, "documents_modeles"),
                         "validation_obligatoire": _emission.validation_obligatoire(entreprise),
                         "types": [{"id": k, "nom": v} for k, v in DocumentAdministratif.TYPES_DOCUMENT],
                         "exemple": _emission.formater_reference(modele, 12, timezone.localdate().year)})


# ══════════════════════════════════════════════════════════════
#  MODÈLES D'ENTÊTE ET MODÈLES DE DOCUMENTS
#  Lecture : administrateur et agent (le lecteur est refusé, comme partout ici) ; écriture : administrateur seulement.
# ══════════════════════════════════════════════════════════════
def _refus_non_admin(membre):
    return None if _a_pouvoir(membre, "documents_modeles") else JsonResponse({"erreur": t("engine/views/_part_012.py:acces_refuse")}, status=403)


def _modele_de(classe, entreprise, modele_id):
    return classe.objects.filter(entreprise=entreprise, pk=modele_id).first()


def _introuvable_modele():
    return JsonResponse({"erreur": t("engine/views/_part_012.py:modele_introuvable"), "code": "introuvable"}, status=404)


@login_required
@require_http_methods(["GET", "POST"])
def api_modeles_entete(request):
    """GET : modèles d'entête de l'entreprise (dans leur ordre). POST {nom, lignes_gauche?, lignes_droite?, par_defaut?} : crée un modèle (administrateur)."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    if request.method == "POST":
        refus = _refus_non_admin(membre)
        if refus:
            return refus
        try:
            m = _modeles.creer_entete(entreprise, request.user, _saisie_json(request), ip=_ip(request))
        except _modeles.ErreurModele as exc:
            return _erreur_modele(exc)
        return JsonResponse(_modeles.serialiser_entete(m), status=201)
    return JsonResponse({"modeles": [_modeles.serialiser_entete(m) for m in ModeleEntete.objects.filter(entreprise=entreprise)],
                         "est_admin": _a_pouvoir(membre, "documents_modeles"),
                         "limites": {"lignes": _entete.MAX_LIGNES, "texte": _entete.MAX_TEXTE, "nom": _modeles.MAX_NOM}})


@login_required
@require_http_methods(["GET", "PUT", "DELETE"])
def api_modele_entete(request, modele_id):
    """GET : un modèle d'entête. PUT {nom?, lignes_gauche?, lignes_droite?, ordre?, par_defaut?} : modifie (administrateur).
    DELETE : supprime (administrateur) — le premier modèle restant devient le défaut si c'était lui."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    m = _modele_de(ModeleEntete, entreprise, modele_id)
    if m is None:
        return _introuvable_modele()
    if request.method != "GET":
        refus = _refus_non_admin(membre)
        if refus:
            return refus
    try:
        if request.method == "PUT":
            m = _modeles.modifier_entete(m, request.user, _saisie_json(request), ip=_ip(request))
        elif request.method == "DELETE":
            _modeles.supprimer_entete(m, request.user, ip=_ip(request))
            return JsonResponse({"ok": True})
    except _modeles.ErreurModele as exc:
        return _erreur_modele(exc)
    return JsonResponse(_modeles.serialiser_entete(m))


@login_required
@require_http_methods(["POST"])
def api_modeles_entete_ordre(request):
    """POST {ids: [...]} : nouvel ordre d'affichage des modèles d'entête (administrateur)."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    refus = _refus_non_admin(membre)
    if refus:
        return refus
    modeles = _modeles.reordonner_entetes(entreprise, request.user, _saisie_json(request).get("ids"), ip=_ip(request))
    return JsonResponse({"modeles": [_modeles.serialiser_entete(m) for m in modeles]})


@login_required
@require_http_methods(["GET", "POST"])
def api_modeles_documents(request):
    """GET : modèles de documents de l'entreprise (tous les rôles autorisés les voient). POST {nom, type_document?, titre?, corps?,
    modele_entete?} : crée un modèle (administrateur)."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    if request.method == "POST":
        refus = _refus_non_admin(membre)
        if refus:
            return refus
        try:
            m = _modeles.creer_modele_document(entreprise, request.user, _saisie_json(request), ip=_ip(request))
        except _modeles.ErreurModele as exc:
            return _erreur_modele(exc)
        return JsonResponse(_modeles.serialiser_document_modele(m), status=201)
    return JsonResponse({"modeles": [_modeles.serialiser_document_modele(m) for m in ModeleDocument.objects.filter(entreprise=entreprise)],
                         "est_admin": _a_pouvoir(membre, "documents_modeles")})


@login_required
@require_http_methods(["GET", "PUT", "DELETE"])
def api_modele_document(request, modele_id):
    """GET : un modèle de document. PUT {nom?, type_document?, titre?, corps?, modele_entete?} : modifie (administrateur).
    DELETE : supprime (administrateur) — les documents déjà créés à partir de ce modèle ne changent pas."""
    refus, entreprise, membre = _contexte_documents(request)
    if refus:
        return refus
    m = _modele_de(ModeleDocument, entreprise, modele_id)
    if m is None:
        return _introuvable_modele()
    if request.method != "GET":
        refus = _refus_non_admin(membre)
        if refus:
            return refus
    try:
        if request.method == "PUT":
            m = _modeles.modifier_modele_document(m, request.user, _saisie_json(request), ip=_ip(request))
        elif request.method == "DELETE":
            _modeles.supprimer_modele_document(m, request.user, ip=_ip(request))
            return JsonResponse({"ok": True})
    except _modeles.ErreurModele as exc:
        return _erreur_modele(exc)
    return JsonResponse(_modeles.serialiser_document_modele(m))


@limiter("verifier_document", max_appels=60, periode_secondes=60)
@require_http_methods(["GET"])
def vue_verifier_document(request, code):
    """Page PUBLIQUE (sans connexion), cible du QR : confirme qu'un numéro et une empreinte correspondent à un document
    émis. Ne montre jamais le contenu. Un code inconnu et un brouillon donnent la même réponse (404)."""
    donnees = _emission.donnees_verification(code)
    reponse = render(request, "engine/verifier_document.html", {"d": donnees}, status=200 if donnees else 404)
    reponse["Cache-Control"] = "no-store"
    reponse["X-Robots-Tag"] = "noindex"
    return reponse


__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
