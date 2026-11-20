from engine.i18n import tf
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
from ._part_008 import *  # noqa
from ._part_009 import *  # noqa
from ._part_010 import *  # noqa


@login_required
@require_http_methods(["POST"])

def api_creer_tache(request):
    """
    Crée une tâche, planifiée ou conditionnelle.
    Body JSON :
      nom                  (str)
      instructions         (str)
      generer              (bool, défaut False)
      mode_declenchement   (str, "planifie" (défaut) ou "condition")
      intervalle_type      (str, défaut "unique") — délai de repos anti-rafale en mode "condition"
      intervalle_valeur    (int, défaut 0)
      condition_categorie  (str, requis si mode_declenchement="condition")
      condition_table_id   (int, optionnel — vide = toutes les tables)
      condition_parametres (dict, optionnel — voir TachePlanifiee.condition_parametres)
    """
    from ..models import TachePlanifiee
    _bs = _interdit_non_superuser(request)
    if _bs:
        return _bs
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    corps = json.loads(request.body or "{}")

    generer = corps.get("generer", False)
    mode_declenchement = corps.get("mode_declenchement", "planifie")
    if mode_declenchement not in ("planifie", "condition"):
        mode_declenchement = "planifie"
    condition_categorie = corps.get("condition_categorie", "").strip()
    if mode_declenchement == "condition" and condition_categorie not in dict(TachePlanifiee.CONDITION_CATEGORIE_CHOICES):
        return JsonResponse({"erreur": t("engine/views/_part_011.py:46:41")}, status=400)
    condition_table_id = corps.get("condition_table_id") or None
    if condition_table_id:
        get_object_or_404(TableDynamique, id=condition_table_id, entreprise=entreprise)

    script_fourni = (corps.get("script") or "").strip()
    tache = TachePlanifiee.objects.create(
        entreprise=entreprise,
        utilisateur=request.user,
        nom=corps.get("nom", "").strip(),
        instructions=corps.get("instructions", "").strip(),
        intervalle_type=corps.get("intervalle_type", "unique"),
        intervalle_valeur=int(corps.get("intervalle_valeur", 0)),
        mode_declenchement=mode_declenchement,
        condition_categorie=condition_categorie if mode_declenchement == "condition" else "",
        condition_table_id=condition_table_id if mode_declenchement == "condition" else None,
        condition_parametres=corps.get("condition_parametres") or {} if mode_declenchement == "condition" else {},
        statut="en_attente",
    )

    # Script fourni (colle ou importe) : il devient le script de la tache, sans generation par l'IA.
    if script_fourni:
        from ..agent import _ecrire_script_tache
        tache.script_genere = script_fourni
        tache.modifie_manuellement = True
        tache.statut = "script_genere"
        tache.erreur = ""
        tache.save()
        tache.fichier_script = _ecrire_script_tache(tache, script_fourni)
        tache.save(update_fields=["fichier_script"])
        return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)}, status=201)

    if not generer:
        return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)}, status=201)

    # Mode conditionnel : on génère le script tout de suite (prêt à être lancé
    # dès le premier événement, voir engine/declencheurs.py) mais on ne
    # l'EXÉCUTE pas immédiatement — il n'y a pas encore eu d'événement.
    if mode_declenchement == "condition":
        tache.statut = "en_cours"
        tache.save(update_fields=["statut"])
        _taches.generer_script_tache_seul.delay(tache.id)
        return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)}, status=201)

    tache.statut = "en_cours"
    tache.save(update_fields=["statut"])
    _taches.generer_et_executer_tache_tache.delay(tache.id)
    return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)}, status=201)


@login_required
@require_http_methods(["POST"])

def api_importer_script_tache(request, tache_id):
    """Importe un script Python (.py) comme script de la tâche."""
    from ..models import TachePlanifiee
    _bs = _interdit_non_superuser(request)
    if _bs:
        return _bs
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TachePlanifiee, id=tache_id, entreprise=entreprise)
    _bp = _interdit_non_proprietaire_tache(request, tache)
    if _bp:
        return _bp
    tache.utilisateur = request.user   # §étanchéité : le script relu/produit par un super-admin devient SA tâche (seule condition d'exécution, voir agent.executer_tache)

    fichier = request.FILES.get("script")
    if not fichier:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:100:41")}, status=400)
    if not fichier.name.lower().endswith((".py", ".txt")):
        return JsonResponse({"erreur": t("engine/views/_part_011.py:102:41")}, status=400)

    code = fichier.read().decode("utf-8", errors="replace").strip()
    if not code:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:106:41")}, status=400)

    from ..agent import _ecrire_script_tache
    tache.script_genere = code
    tache.modifie_manuellement = True
    tache.statut = "script_genere"
    tache.erreur = ""
    tache.save()
    tache.fichier_script = _ecrire_script_tache(tache, code)
    tache.save(update_fields=["fichier_script"])
    return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)})


@login_required
@require_http_methods(["POST"])

def api_modifier_script_tache(request, tache_id):
    """Sauvegarde un script édité manuellement."""
    from ..models import TachePlanifiee
    from ..agent import _ecrire_script_tache
    _bs = _interdit_non_superuser(request)
    if _bs:
        return _bs
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TachePlanifiee, id=tache_id, entreprise=entreprise)
    _bp = _interdit_non_proprietaire_tache(request, tache)
    if _bp:
        return _bp
    tache.utilisateur = request.user   # §étanchéité : le script relu/produit par un super-admin devient SA tâche (seule condition d'exécution, voir agent.executer_tache)
    corps = json.loads(request.body or "{}")
    code = corps.get("code", "").strip()
    if not code:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:137:41")}, status=400)
    tache.script_genere = code
    tache.modifie_manuellement = True
    tache.statut = "script_genere"
    tache.erreur = ""
    tache.save()
    tache.fichier_script = _ecrire_script_tache(tache, code)
    tache.save(update_fields=["fichier_script"])
    return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)})


@login_required
@require_http_methods(["POST"])

def api_executer_tache(request, tache_id):
    """Lance l'exécution du script existant."""
    from ..models import TachePlanifiee
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TachePlanifiee, id=tache_id, entreprise=entreprise)
    tache.statut = "en_cours"
    tache.save(update_fields=["statut"])
    _taches.executer_tache_planifiee_tache.delay(tache.id)
    return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)})


@login_required
@require_http_methods(["POST"])

def api_regenerer_tache(request, tache_id):
    """Régénère le script IA et ré-exécute."""
    from ..models import TachePlanifiee
    _bs = _interdit_non_superuser(request)
    if _bs:
        return _bs
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TachePlanifiee, id=tache_id, entreprise=entreprise)
    _bp = _interdit_non_proprietaire_tache(request, tache)
    if _bp:
        return _bp
    tache.utilisateur = request.user   # §étanchéité : le script relu/produit par un super-admin devient SA tâche (seule condition d'exécution, voir agent.executer_tache)
    corps = json.loads(request.body or "{}")
    if "instructions" in corps:
        tache.instructions = corps["instructions"].strip()
    if "nom" in corps:
        tache.nom = corps["nom"].strip()
    if "intervalle_type" in corps:
        tache.intervalle_type = corps["intervalle_type"]
    if "intervalle_valeur" in corps:
        tache.intervalle_valeur = int(corps["intervalle_valeur"])
    tache.statut = "en_cours"
    tache.save()
    _taches.generer_et_executer_tache_tache.delay(tache.id)
    return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)})


@login_required
@require_http_methods(["GET"])

def api_statut_tache(request, tache_id):
    """Polling léger du statut d'une tâche."""
    from ..models import TachePlanifiee
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TachePlanifiee, id=tache_id, entreprise=entreprise)
    if tache.statut in ("execute", "echec", "echec_generation", "echec_execution", "script_genere"):
        return JsonResponse({"fini": True, "tache": _serialiser_tache(tache, request.user.is_superuser)})
    return JsonResponse({"fini": False, "statut": tache.statut})


@login_required
@require_http_methods(["POST"])

def api_modifier_tache(request, tache_id):
    """Modifie les métadonnées (nom, instructions, planification, actif)."""
    from ..models import TachePlanifiee
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TachePlanifiee, id=tache_id, entreprise=entreprise)
    _bp = _interdit_non_proprietaire_tache(request, tache)
    if _bp:
        return _bp
    corps = json.loads(request.body or "{}")
    if not request.user.is_superuser and set(corps) - {"actif"}:
        # §étanchéité : seule la mise en pause / reprise (« actif ») reste ouverte à l'administrateur. Nom, instructions (prompt de
        # génération du script), mode, condition, délais : DÉFINITION de la tâche, donc super-administrateur uniquement.
        return _interdit_non_superuser(request)
    champs = []
    if "nom" in corps:
        tache.nom = corps["nom"].strip(); champs.append("nom")
    if "instructions" in corps:
        tache.instructions = corps["instructions"].strip(); champs.append("instructions")
    if "intervalle_type" in corps:
        tache.intervalle_type = corps["intervalle_type"]; champs.append("intervalle_type")
    if "intervalle_valeur" in corps:
        tache.intervalle_valeur = int(corps["intervalle_valeur"]); champs.append("intervalle_valeur")
    if "actif" in corps:
        tache.actif = bool(corps["actif"]); champs.append("actif")
    if "mode_declenchement" in corps and corps["mode_declenchement"] in ("planifie", "condition"):
        tache.mode_declenchement = corps["mode_declenchement"]; champs.append("mode_declenchement")
    if "condition_categorie" in corps:
        cat = (corps["condition_categorie"] or "").strip()
        if cat and cat not in dict(TachePlanifiee.CONDITION_CATEGORIE_CHOICES):
            return JsonResponse({"erreur": t("engine/views/_part_011.py:241:45")}, status=400)
        tache.condition_categorie = cat; champs.append("condition_categorie")
    if "condition_table_id" in corps:
        cid = corps["condition_table_id"] or None
        if cid:
            get_object_or_404(TableDynamique, id=cid, entreprise=entreprise)
        tache.condition_table_id = cid; champs.append("condition_table_id")
    if "condition_parametres" in corps:
        tache.condition_parametres = corps["condition_parametres"] or {}; champs.append("condition_parametres")
    if champs:
        tache.save(update_fields=champs)
    return JsonResponse({"tache": _serialiser_tache(tache, request.user.is_superuser)})


@login_required
@require_http_methods(["POST"])

def api_supprimer_tache(request, tache_id):
    """Supprime une tâche et son script disque."""
    from ..models import TachePlanifiee
    import os
    _bs = _interdit_non_superuser(request)
    if _bs:
        return _bs
    _bl = _interdit_lecteur(request)
    if _bl:
        return _bl
    entreprise = _entreprise_courante(request)
    tache = get_object_or_404(TachePlanifiee, id=tache_id, entreprise=entreprise)
    if tache.fichier_script and os.path.exists(tache.fichier_script):
        try:
            os.remove(tache.fichier_script)
        except OSError:
            pass
    tache.delete()
    return JsonResponse({"ok": True})


# ─────────────────────────────────────────────────────────────
# Notifications (agents/admin) + relais SMS optionnel
# ─────────────────────────────────────────────────────────────
def _serialiser_notification(notif):
    from engine.notifications import lien_notification
    return {
        "id": notif.id,
        "type": notif.type_notification,
        "titre": notif.titre,
        "message": notif.message,
        "table_id": notif.table_id,
        "table_nom": notif.table.nom if notif.table_id else None,
        "enregistrement_id": notif.enregistrement_id,
        "lien": lien_notification(notif),
        "lue": notif.lue,
        "sms_envoye": notif.sms_envoye,
        "sms_erreur": notif.sms_erreur,
        "cree_le": notif.cree_le.isoformat(),
    }


def vue_notifications(request):
    """Page Notifications : liste des notifications de l'agent/admin connecté,
    et — pour l'admin uniquement — le réglage d'activation du relais SMS."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    membre = MembreEntreprise.objects.select_related("entreprise").filter(
        utilisateur=request.user
    ).first()
    if not membre:
        return redirect("/profile/")

    return render(request, "engine/notifications.html", {
        "entreprise": membre.entreprise,
        "est_admin": _a_pouvoir(membre, "sms"),
        "user_id": request.user.id,
    })


@login_required
def api_notifications(request):
    """Liste les notifications du membre connecté (les plus récentes en
    premier — voir Notification.Meta.ordering), avec le compte de
    non-lues. `?non_lues=1` ne retourne que les non-lues."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if not membre:
        return JsonResponse({"notifications": [], "non_lues": 0})

    from .. import agenda as _agenda   # la cloche est la « connexion » de l'agent : les rappels d'agenda dus partent maintenant
    _agenda.emettre(entreprise, membre)
    qs = Notification.objects.filter(destinataire=membre).select_related("table")
    if request.GET.get("non_lues") == "1":
        qs = qs.filter(lue=False)

    try:   # ?limite=N : la cloche de l'en-tête n'a besoin que des plus récentes
        limite = max(1, min(int(request.GET.get("limite") or 200), 200))
    except ValueError:
        limite = 200
    notifications = [_serialiser_notification(n) for n in qs[:limite]]
    non_lues = Notification.objects.filter(destinataire=membre, lue=False).count()
    return JsonResponse({"notifications": notifications, "non_lues": non_lues})


@login_required
@require_http_methods(["POST"])
def api_notification_marquer_lue(request, notification_id):
    """Marque UNE notification comme lue (uniquement la sienne)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if not membre:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:350:41")}, status=403)
    notif = get_object_or_404(Notification, id=notification_id, destinataire=membre)
    notif.marquer_lue()
    return JsonResponse({"notification": _serialiser_notification(notif)})


@login_required
@require_http_methods(["POST"])
def api_notifications_tout_lire(request):
    """Marque TOUTES les notifications non-lues du membre connecté comme lues."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if not membre:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:368:41")}, status=403)
    from django.utils import timezone as _tz
    Notification.objects.filter(destinataire=membre, lue=False).update(
        lue=True, lue_le=_tz.now(),
    )
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_notifications_effacer(request):
    """Efface TOUTES les notifications du membre connecté (uniquement les siennes : chaque membre a ses propres lignes)."""
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(
        utilisateur=request.user, entreprise=entreprise
    ).first()
    if not membre:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:368:41")}, status=403)
    supprimees = Notification.objects.filter(destinataire=membre).delete()[0]
    return JsonResponse({"ok": True, "supprimees": supprimees})




# ══════════════════════════════════════════════════════════════
#  RECOMMANDATIONS — fusion de l'état du moteur (engine/fusion.py) puis
#  règles préétablies (engine/recommandations.py). Réservé à l'administrateur :
#  l'état fusionné traverse toutes les tables de l'entreprise.
# ══════════════════════════════════════════════════════════════

@login_required
def vue_recommandations(request):
    """Page dédiée aux recommandations (administrateur seulement)."""
    entreprise = _entreprise_courante(request)
    if _admin_requis(request, entreprise, "decisions"):
        return redirect("/")
    return render(request, "engine/recommandations.html", {"entreprise": entreprise})


@login_required
@require_http_methods(["GET"])
def api_fusion_etat(request):
    """État fusionné complet (JSON). ?forcer=1 ignore le cache de 60 s."""
    from ..fusion import fusionner_etat
    entreprise = _entreprise_courante(request)
    refus = _admin_requis(request, entreprise, "decisions")
    if refus:
        return refus
    return JsonResponse(fusionner_etat(entreprise, forcer=request.GET.get("forcer") == "1"))


@login_required
@require_http_methods(["GET"])
def api_recommandations(request):
    """Fusionne l'état puis applique les règles. Rend le résumé de la fusion, les recommandations
    triées par score, leurs totaux par catégorie/gravité et le catalogue des règles."""
    from ..fusion import fusionner_etat
    from ..recommandations import catalogue, catalogue_archi, catalogue_geo, evaluer
    entreprise = _entreprise_courante(request)
    refus = _admin_requis(request, entreprise, "decisions")
    if refus:
        return refus
    etat = fusionner_etat(entreprise, forcer=request.GET.get("forcer") == "1")
    resultat = evaluer(etat)
    # Moteur de décision (engine/decision.py) : réordonne et annote les recommandations d'après
    # la toile de données liées. Additif et jamais bloquant : en cas d'échec, l'ordre d'origine
    # par score est conservé et "decision" vaut None.
    try:
        from .. import decision, portail
        pilote = decision.piloter(etat, portail.graphe_dependances(entreprise, forcer=request.GET.get("forcer") == "1"), resultat)
        resultat["recommandations"] = pilote["recommandations"]
        resultat["decision"] = {k: pilote[k] for k in ("comprehension", "objets_a_risque", "resume")}
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/views/_part_011.py:g:482:47"), entreprise.id)
        resultat["decision"] = None
    resultat["catalogue"] = catalogue() + catalogue_geo() + catalogue_archi()   # même total que « regles » (33 critères + géospatial + architecture)
    resultat["fusion"] = {"genere_le": etat["genere_le"], "resume": etat["resume"], "avertissements": etat["avertissements"]}
    from ..recommandations import mesurer_effets
    suivis = [{"code": (j.details or {}).get("code"), "nom": (j.details or {}).get("nom", ""), "cible_type": j.cible_type, "cible_id": j.cible_id,
               "ts": j.horodatage.timestamp(), "score": (j.ancienne_valeur or {}).get("score", 0)}
              for j in JournalAudit.objects.filter(entreprise=entreprise, action="recommandation.action").order_by("-horodatage")[:30]]
    resultat["suivi"] = mesurer_effets(suivis, resultat["recommandations"], etat["ts"])
    try:  # mesure des conséquences dues + un pas d'entraînement paresseux (silencieux, jamais bloquant)
        from .. import memoire_decision, portail as _portail
        memoire_decision.entretenir(entreprise, etat, _portail.graphe_dependances(entreprise), resultat)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/views/_part_011.py:g:496:47"), entreprise.id)
    return JsonResponse(resultat)


# Actions recommandables -> vue existante qui lance la tâche (aucune seconde implémentation du lancement).
_LANCEURS_RECO = {"tache": api_executer_tache, "kpi": api_recalculer_kpi}


@login_required
@require_http_methods(["POST"])
def api_recommandation_lancer(request):
    """ACTION recommandée : lance la tâche existante sur la cible, puis enregistre l'état avant (JournalAudit) ;
    api_recommandations en mesure ensuite l'effet. Une DÉCISION ne passe jamais ici : rien n'est lancé."""
    from ..fusion import fusionner_etat
    from ..recommandations import evaluer
    entreprise = _entreprise_courante(request)
    refus = _admin_requis(request, entreprise, "decisions")
    if refus:
        return refus
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:g:517:40")}, status=400)
    cible = corps.get("cible") or {}
    reco = next((r for r in evaluer(fusionner_etat(entreprise))["recommandations"]
                 if r["code"] == corps.get("code") and r["cible"].get("type") == cible.get("type") and r["cible"].get("id") == cible.get("id")), None)
    lanceur = _LANCEURS_RECO.get(cible.get("type"))
    if not reco or reco["type"] != "action" or not lanceur:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:g:523:40")}, status=409)
    reponse = lanceur(request, reco["cible"]["id"])
    if reponse.status_code < 400:
        JournalAudit.enregistrer(entreprise=entreprise, utilisateur=request.user, action="recommandation.action", source="reco",
                                 cible_type=reco["cible"]["type"], cible_id=reco["cible"]["id"],
                                 details={"code": reco["code"], "nom": reco["cible"].get("nom", ""), "categorie": reco["categorie"]},
                                 ancienne_valeur={"score": reco["score"], "gravite": reco["gravite"], "preuves": reco["preuves"]})
    return reponse


@login_required
@require_http_methods(["GET", "POST"])
def api_reglages_criteres(request):
    """RÉGLAGES DE LA MESURE DES CRITÈRES (engine/reglages_criteres.py), administrateur seulement.
    GET : définitions (libellé, aide, défaut, bornes) + valeurs actuelles ; GET ?diagnostic=1 ajoute, pour chaque critère, s'il est vivant, mort,
    saturé ou sans objet sur les données réelles avec ces réglages (c'est ce qui permet de juger les échelles) ;
    POST {reglages:{clé: nombre|""}} : enregistre (une valeur hors bornes est refusée, le reste n'est pas enregistré) ; POST {reinitialiser:true} : retour aux défauts.
    Chaque changement est journalisé (JournalAudit). Les réglages de mesure agissent sur les mesures futures ; l'apport a priori agit dès la prochaine prédiction, y compris sur un modèle déjà entraîné."""
    from .. import criteres, reglages_criteres
    from ..fusion import fusionner_etat
    entreprise = _entreprise_courante(request)
    refus = _admin_requis(request, entreprise, "decisions")
    if refus:
        return refus
    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=entreprise)
    if request.method == "POST":
        try:
            corps = json.loads(request.body or "{}")
        except ValueError:
            return JsonResponse({"erreur": t("engine/views/_part_011.py:g:552:44")}, status=400)
        if not isinstance(corps, dict) or not (corps.get("reinitialiser") or isinstance(corps.get("reglages"), dict)):
            return JsonResponse({"erreur": t("engine/views/_part_011.py:g:554:44")}, status=400)
        valeurs, erreurs = ({}, []) if corps.get("reinitialiser") else reglages_criteres.normaliser(corps["reglages"])
        if erreurs:
            return JsonResponse({"erreur": " ".join(erreurs)}, status=400)
        ancien = reglages_criteres.normaliser(cfg.reglages_criteres)[0]
        cfg.reglages_criteres = valeurs
        cfg.save(update_fields=["reglages_criteres"])
        JournalAudit.enregistrer(entreprise=entreprise, utilisateur=request.user, action="reglages.criteres", source="reco",
                                 ancienne_valeur=ancien, nouvelle_valeur=valeurs)
    valeurs = reglages_criteres.normaliser(cfg.reglages_criteres)[0]
    sortie = {"definitions": reglages_criteres.definitions(), "valeurs": valeurs}
    if request.method == "GET" and request.GET.get("diagnostic") == "1":
        try:
            etat = fusionner_etat(entreprise)
            echelles = reglages_criteres.echelles(valeurs)
            sortie["echelles_utilisees"] = criteres.calibrer(etat, reglages=echelles)
            sortie["diagnostic"] = criteres.diagnostic(etat, reglages=echelles)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(t("engine/views/_part_011.py:g:574:51"), entreprise.id)
            return JsonResponse({"erreur": t("engine/views/_part_011.py:g:574:44")}, status=500)
    return JsonResponse(sortie)


@login_required
@require_http_methods(["GET", "POST"])
def api_decision_strategique(request):
    """DÉCISION STRATÉGIQUE (engine/strategie.py). GET : objets de la toile qu'une option peut viser + vocabulaire des leviers.
    POST {generer:true, besoin:["table:1", ...] (facultatif : périmètre)} : le moteur GÉNÈRE lui-même les options depuis les 33 critères
    mesurés sur colonnes/tables (engine/besoins.py) puis arbitre ; ou
    POST {options:[{libelle, levier, effort 1-5, cibles:["table:1", ...]}]} : arbitre entre les options déclarées de
    l'utilisateur d'après l'état fusionné, la toile et les risques ; le libellé n'est qu'un texte d'affichage (jamais interprété),
    le levier et les cibles sont imposés (engine/declaration_options.py) ; la décision est journalisée (JournalAudit)."""
    from .. import declaration_options, memoire_decision, portail, strategie
    from ..fusion import fusionner_etat
    from ..recommandations import evaluer
    entreprise = _entreprise_courante(request)
    refus = _admin_requis(request, entreprise, "decisions")
    if refus:
        return refus
    graphe = portail.graphe_dependances(entreprise)
    if request.method == "GET":
        return JsonResponse({"leviers": declaration_options.catalogue(), "cibles": [{"id": nid, "type": n["type"], "nom": n.get("label") or nid}
                                        for nid, n in graphe["noeuds"].items() if n["type"] != "champ"]})
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:g:601:40")}, status=400)
    try:
        etat = fusionner_etat(entreprise)
        # Le modèle fantôme (engine/modele_fantome.py) pilote l'arbitrage ; l'interface ne le voit jamais.
        resultat_reco, options, generees = evaluer(etat), corps.get("options"), False
        if not options and (corps.get("besoin") is not None or corps.get("generer")):
            # Le moteur génère les options depuis les 33 critères mesurés (engine/besoins.py) ; `besoin` (facultatif) restreint le périmètre
            from .. import besoins
            options, erreurs = besoins.proposer(corps.get("besoin"), etat, graphe, resultat_reco)
            bilan = besoins.bilan_criteres(resultat_reco)
            if erreurs:
                return JsonResponse({"erreur": " ".join(erreurs)}, status=400)
            if not options:
                return JsonResponse({"verdict": "rien_a_faire", "recommandation": None, "options": [], "preparatifs": [], "avertissements": [],
                                     "bilan_criteres": bilan,
                                     "a_ta_place": [tf("engine/views/_part_011.py:g:616:53", v1=bilan['evalues'])]})
            generees = True
        sortie = strategie.arbitrer(options, etat, graphe, resultat_reco, {"reglages_criteres": memoire_decision.reglages_criteres(entreprise)},
                                    modele=memoire_decision.charger_modele(entreprise))
        if generees and "erreurs" not in sortie:
            sortie["generees"], sortie["bilan_criteres"] = True, bilan
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/views/_part_011.py:g:625:47"), entreprise.id)
        return JsonResponse({"erreur": t("engine/views/_part_011.py:g:625:40")}, status=500)
    if "erreurs" in sortie:
        return JsonResponse({"erreur": " ".join(sortie["erreurs"])}, status=400)
    try:  # la recommandation est une donnée (table, blockchain, ontologie) : sa perte ne doit pas faire échouer la réponse
        sortie["recommandation_id"] = memoire_decision.enregistrer_recommandation(entreprise, request.user, sortie)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/views/_part_011.py:g:633:47"), entreprise.id)
    sortie.pop("_trace", None)  # φ et « avant » : réservés à la mémoire de décision, jamais renvoyés au navigateur
    JournalAudit.enregistrer(entreprise=entreprise, utilisateur=request.user, action="decision.strategique", source="reco",
                             cible_type="entreprise", cible_id=entreprise.id,
                             details={"options": [o["nom"] for o in sortie["options"]], "verdict": sortie["verdict"],
                                      "recommandation": sortie["recommandation"], "probabilite": sortie["probabilite"]},
                             ancienne_valeur=sortie["situation"])
    return JsonResponse(sortie)


@login_required
@require_http_methods(["POST"])
def api_decision_choisir(request):
    """L'utilisateur tranche : POST {recommandation_id, option}. La décision devient une ligne de la table « Décisions »
    (reliée à la recommandation, journalisée dans la blockchain) ; ses conséquences seront mesurées automatiquement
    (engine/memoire_decision.py) puis apprises par le modèle fantôme."""
    from .. import memoire_decision
    entreprise = _entreprise_courante(request)
    refus = _admin_requis(request, entreprise, "decisions")
    if refus:
        return refus
    try:
        corps = json.loads(request.body or "{}")
        reco_id = int(corps.get("recommandation_id"))
    except (ValueError, TypeError):
        return JsonResponse({"erreur": t("engine/views/_part_011.py:g:657:40")}, status=400)
    enr = memoire_decision.enregistrer_choix(entreprise, request.user, reco_id, str(corps.get("option") or ""))
    if enr is None:
        return JsonResponse({"erreur": t("engine/views/_part_011.py:g:660:40")}, status=409)
    try:
        from .. import notifications
        notifications.notifier_decision_enregistree(entreprise, str(corps.get("option") or ""), request.user)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/views/_part_011.py:g:667:47"), entreprise.id)
    return JsonResponse({"decision_id": enr.id})


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
