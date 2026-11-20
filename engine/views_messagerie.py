"""PAGES ET API DE LA MESSAGERIE ET DES PUBLICITÉS — logique et droits dans messagerie.py.

Pages :
  GET /messagerie/                    discussions (équipe, clients) : liste, recherche de personnes et de messages, fil, envoi
  GET /publicites/                    l'équipe CONFIGURE ses publicités (elle ne les voit pas défiler) ; un client est renvoyé vers la sienne
  GET /espace-client/publicites/      le client navigue dans les publicités de son agent (première page après sa connexion)
API (JSON) :
  GET  /api/messagerie/conversations/?sync=1             liste + total non lus        GET /api/messagerie/contacts/?q=&limite=
  POST /api/messagerie/salons/creer/  {titre?, membres: [id…]}   → {conversation_id} : discussion privée (huis clos), participants choisis
  GET/POST /api/messagerie/conversations/<id>/participants/   GET : liste ; POST {action: ajouter|retirer|quitter, membres|membre}
  POST /api/messagerie/ouvrir/  {membre_id} | {groupe: "entreprise"|"clients"}   → {conversation_id}
  GET  /api/messagerie/conversations/<id>/messages/?apres=<id_message>
  POST /api/messagerie/conversations/<id>/envoyer/       multipart : texte, fichier (image, son, vidéo, document)
  POST /api/messagerie/messages/<id>/supprimer/          GET /api/messagerie/recherche/?q=
  POST /api/messagerie/messages/<id>/modifier/           {texte} : corrige son message (24 h au plus ; affiché « modifié »)
  POST /api/messagerie/messages/<id>/transferer/         {conversations: [id…]} : transfère une copie (5 discussions au plus)
  POST /api/messagerie/messages/<id>/reaction/           {emoji} : réaction (la même une seconde fois la retire)
  POST /api/messagerie/messages/<id>/favori/             bascule l'étoile (privée) ;  GET /api/messagerie/favoris/
  POST /api/messagerie/sourdine/                         {membre_id | conversation_id, actif} : sourdine (jamais l'administrateur)
  GET  /api/messagerie/sourdines/                        personnes en sourdine
  (envoyer accepte aussi `reponse_a` = id d'un message de la même discussion : réponse avec citation)
  GET  /api/messagerie/structure/agents/                 agents dont l'administrateur peut envoyer l'espace (vide pour un agent)
  POST /api/messagerie/conversations/<id>/structure/     {agents?: [id membre], texte?} : envoie la structure (administrateur : tout ou quelques
                                                         agents ; agent : son espace seulement) à l'équipe
  POST /api/messagerie/messages/<id>/importer/           importe la structure reçue (libre ; l'agent est confiné à son espace)
  GET/POST /api/publicites/  (équipe : liste / création multipart ; pour un agent, la liste porte aussi « administration » : publicités publiées par
                              l'administrateur ou par un agent qui a le droit publicites_equipe, en consultation seule)   POST /api/publicites/<id>/modifier/ | supprimer/
  GET  /api/publicites/client/                           publicités publiées de l'agent du client
                                                         (+ `formulaire` : table liée par l'agent, seulement si elle est encore mise à disposition du client ; `agent_id` : pour ouvrir la conversation)
  Une publicité peut être liée à un formulaire (champ table_id à la création / modification) ; côté client : /espace-client/?table=<id> et /messagerie/?avec=<id>.
"""
from engine.i18n import t
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import appels
from . import chatbot as cb
from . import messagerie as mg
from .models import Conversation, MembreEntreprise, Message, Publicite
from .ratelimit import limiter
from .views import _entreprise_courante


def _membre(request):
    """MembreEntreprise de l'utilisateur dans l'entreprise unique (créé pour le super-administrateur à sa première visite)."""
    entreprise = _entreprise_courante(request)
    return (MembreEntreprise.objects.select_related("entreprise", "utilisateur", "enregistrement_client")
            .get(utilisateur=request.user, entreprise=entreprise))


def _erreur(texte, statut=400):
    return JsonResponse({"erreur": texte}, status=statut)


def _conversation(request, conversation_id):
    """(membre, conversation, None) ou (membre, None, réponse d'erreur) : toute API de fil passe ici."""
    m = _membre(request)
    conv = get_object_or_404(Conversation, id=conversation_id)
    if not mg.peut_acceder(m, conv):
        return m, None, _erreur(t("engine/views_messagerie.py:g:62:33"), 403)
    return m, conv, None


def _corps(request):
    try:
        return json.loads(request.body or "{}")
    except ValueError:
        return {}


# ── Pages ─────────────────────────────────────────────────────────────────────
@login_required
@ensure_csrf_cookie
def page_coque(request):
    """Coque persistante : contient les pages dans un cadre et porte l'appel audio/vidéo, pour qu'il survive à la navigation (appel_global.js)."""
    cible = request.GET.get("p") or "/"
    prefixe = reverse("engine:coque")
    # Chemin local uniquement (jamais d'adresse externe ni de boucle sur la coque elle-même)
    if not cible.startswith("/") or cible.startswith("//") or "\\" in cible or cible.startswith(prefixe) or "\n" in cible or "\r" in cible:
        cible = "/"
    return render(request, "engine/coque.html", {"cible": cible})


@login_required
@ensure_csrf_cookie
def page_messagerie(request):
    m = _membre(request)
    return render(request, "engine/messagerie.html", {"entreprise": m.entreprise, "est_client": not mg.est_equipe(m)})


@login_required
@ensure_csrf_cookie
def page_publicites(request):
    m = _membre(request)
    if not mg.est_equipe(m):
        return redirect("engine:publicites_client")
    return render(request, "engine/publicites.html", {"entreprise": m.entreprise})


@login_required
@ensure_csrf_cookie
def page_publicites_client(request):
    m = _membre(request)
    if mg.est_equipe(m):
        return redirect("engine:publicites")
    return render(request, "engine/publicites_client.html", {"entreprise": m.entreprise})


# ── Messagerie ────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["GET"])
def api_conversations(request):
    m = _membre(request)
    liste = mg.lister(m, synchroniser=request.GET.get("sync") == "1")
    return JsonResponse({"moi": {"id": m.id, "nom": mg.noms([m])[m.id], "role": m.role}, "conversations": liste,
                         "total_non_lus": sum(c["non_lus"] for c in liste),
                         "groupes": {"entreprise": mg.est_equipe(m), "clients": True}})


@login_required
@require_http_methods(["GET"])
def api_contacts(request):
    m = _membre(request)
    try:
        limite = min(max(int(request.GET.get("limite") or mg.LIMITE_CONTACTS), 1), 200)
    except ValueError:
        limite = mg.LIMITE_CONTACTS
    contacts = mg.interlocuteurs(m, request.GET.get("q", ""), limite=limite)
    cache, patron = mg.noms(contacts), mg.chef(m.entreprise)
    return JsonResponse({"contacts": [{"id": c.id, "nom": cache[c.id], "role": "client" if c.role == "lecteur" else
                                       ("chef" if patron is not None and patron.id == c.id else "agent")} for c in contacts]})


@login_required
@require_http_methods(["POST"])
def api_ouvrir(request):
    m = _membre(request)
    corps = _corps(request)
    groupe = corps.get("groupe")
    if groupe == "entreprise":
        if not mg.est_equipe(m):
            return _erreur(t("engine/views_messagerie.py:g:144:28"), 403)
        return JsonResponse({"conversation_id": mg.groupe_entreprise(m).id})
    if groupe == "clients":
        agent = m if mg.est_equipe(m) else mg.agent_de(m)
        return JsonResponse({"conversation_id": mg.groupe_clients(agent, m).id}) if agent else _erreur(t("engine/views_messagerie.py:g:148:104"), 404)
    try:
        cible = int(corps.get("membre_id") or 0)
    except (TypeError, ValueError):
        cible = 0
    autre = MembreEntreprise.objects.filter(id=cible, entreprise=m.entreprise_id).select_related("utilisateur").first()
    if autre is None or not mg.peut_parler(m, autre):
        return _erreur(t("engine/views_messagerie.py:g:155:24"), 403)
    return JsonResponse({"conversation_id": mg.conversation_directe(m, autre).id})


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_salon", max_appels=20, periode_secondes=60, methodes={"POST"})
def api_creer_salon(request):
    """Crée une discussion privée : le créateur décide qui y participe (messages et appels à huis clos)."""
    m = _membre(request)
    corps = _corps(request)
    conv, erreur = mg.creer_salon(m, corps.get("titre"), corps.get("membres"))
    if erreur:
        return _erreur(erreur, 400)
    return JsonResponse({"conversation_id": conv.id})


@login_required
@require_http_methods(["GET", "POST"])
@limiter("messagerie_salon", max_appels=20, periode_secondes=60, methodes={"POST"})
def api_participants(request, conversation_id):
    m, conv, refus = _conversation(request, conversation_id)
    if refus:
        return refus
    if conv.type != "salon":
        return _erreur(t("engine/views_messagerie.py:g:180:24"), 400)
    if request.method == "POST":
        corps = _corps(request)
        action = corps.get("action")
        if action == "ajouter":
            erreur = mg.ajouter_au_salon(m, conv, corps.get("membres"))
        elif action == "retirer":
            erreur = mg.retirer_du_salon(m, conv, corps.get("membre"))
        elif action == "quitter":
            erreur = mg.retirer_du_salon(m, conv, m.id)
            if not erreur:
                return JsonResponse({"ok": True, "quitte": True})
        else:
            erreur = "Action inconnue."
        if erreur:
            return _erreur(erreur, 403)
    return JsonResponse({"titre": conv.titre, "peut_gerer": mg.peut_gerer_salon(m, conv),
                         "participants": [{"id": x.id, "nom": nom, "createur": x.id == conv.createur_id, "moi": x.id == m.id}
                                          for x, nom in mg.participants_salon(conv)]})


@login_required
@require_http_methods(["GET"])
def api_messages(request, conversation_id):
    m, conv, refus = _conversation(request, conversation_id)
    if refus:
        return refus
    try:
        apres = int(request.GET.get("apres") or 0)
    except ValueError:
        apres = 0
    return JsonResponse({"titre": mg.titre_conversation(conv, m), "type": conv.type, "messages": mg.messages(m, conv, apres)})


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_envoi", max_appels=120, periode_secondes=60, methodes={"POST"})
def api_envoyer(request, conversation_id):
    m, conv, refus = _conversation(request, conversation_id)
    if refus:
        return refus
    fiche = None
    if request.POST.get("enregistrement_id"):
        fiche = mg.fiche_liable(m, request.POST.get("enregistrement_id"))
        if fiche is None:
            return _erreur(t("engine/views_messagerie.py:g:225:28"), 403)
    cite = None
    if request.POST.get("reponse_a"):
        cite = Message.objects.filter(id=request.POST.get("reponse_a") if str(request.POST.get("reponse_a")).isdigit() else 0, conversation=conv).first()
    msg, erreur = mg.envoyer(m, conv, request.POST.get("texte", ""), request.FILES.get("fichier"), enregistrement=fiche, reponse_a=cite)
    if erreur:
        return _erreur(erreur)
    return JsonResponse({"id": msg.id})


@login_required
@require_http_methods(["GET"])
def api_structure_agents(request):
    m = _membre(request)
    if not mg.est_equipe(m):
        return _erreur(t("engine/views_messagerie.py:g:240:24"), 403)
    return JsonResponse({"role": m.role, "agents": mg.agents_ciblables(m)})


@login_required
@require_http_methods(["GET"])
def api_appel_config(request, conversation_id):
    """Serveurs ICE (STUN/TURN) et taille maximale de la salle, pour un membre qui a accès à la discussion."""
    m, conv, refus = _conversation(request, conversation_id)
    if refus:
        return refus
    return JsonResponse({"ice": appels.serveurs_ice(m.id, studio=appels.reglages_studio(), hote=appels.hote_de(request.get_host())), "max": appels.MAX_PARTICIPANTS, "groupe": conv.type != "direct"})


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_appel", max_appels=10, periode_secondes=60, methodes={"POST"})
def api_appel_sonner(request, conversation_id):
    """Lance un appel : prévient les autres membres de la discussion (message dans le fil + sonnerie temps réel)."""
    m, conv, refus = _conversation(request, conversation_id)
    if refus:
        return refus
    n = appels.sonner(m, conv, bool(_corps(request).get("video", True)))
    return JsonResponse({"ok": True, "joints": n})


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_structure", max_appels=20, periode_secondes=60, methodes={"POST"})
def api_envoyer_structure(request, conversation_id):
    m, conv, refus = _conversation(request, conversation_id)
    if refus:
        return refus
    corps = _corps(request)
    agents = corps.get("agents") if isinstance(corps.get("agents"), list) else None
    msg, erreur = mg.envoyer_structure(m, conv, agents, corps.get("texte", ""))
    if erreur:
        return _erreur(erreur, 403 if not mg.conversation_structure_ok(m, conv) else 400)
    return JsonResponse({"id": msg.id})


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_structure_import", max_appels=20, periode_secondes=60, methodes={"POST"})
def api_importer_structure(request, message_id):
    m = _membre(request)
    msg = get_object_or_404(Message.objects.select_related("conversation"), id=message_id)
    if not mg.peut_acceder(m, msg.conversation):
        return _erreur(t("engine/views_messagerie.py:g:288:24"), 403)
    resultat, erreur = mg.importer_structure(m, msg)
    if erreur:
        return _erreur(erreur)
    return JsonResponse({"ok": True, "tables": resultat["tables"], "renommees": [{"de": a, "vers": b} for a, b in resultat["renommees"]],
                         "confine": resultat["confine"], "objets": sum(resultat["objets"].values())})


@login_required
@require_http_methods(["GET"])
def api_fiches(request):
    """Mes fiches (créées par moi) que je peux lier à un message."""
    return JsonResponse({"fiches": mg.fiches_de(_membre(request), request.GET.get("q", ""))})


@login_required
@require_http_methods(["POST"])
def api_supprimer_message(request, message_id):
    m = _membre(request)
    msg = get_object_or_404(Message.objects.select_related("conversation"), id=message_id)
    if not mg.peut_acceder(m, msg.conversation) or not mg.supprimer_message(m, msg):
        return _erreur(t("engine/views_messagerie.py:g:309:24"), 403)
    return JsonResponse({"ok": True})


def _message_accessible(request, message_id):
    """(membre, message, None) ou (membre, None, réponse d'erreur) : toute action sur un message passe ici."""
    m = _membre(request)
    msg = get_object_or_404(Message.objects.select_related("conversation"), id=message_id)
    if not mg.peut_acceder(m, msg.conversation):
        return m, None, _erreur(t("engine/views_messagerie.py:g:318:33"), 403)
    return m, msg, None


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_modifier", max_appels=60, periode_secondes=60, methodes={"POST"})
def api_modifier_message(request, message_id):
    m, msg, refus = _message_accessible(request, message_id)
    if refus:
        return refus
    ok, erreur = mg.modifier_message(m, msg, _corps(request).get("texte", ""))
    return JsonResponse({"ok": True}) if ok else _erreur(erreur, 403 if msg.expediteur_id != m.id else 400)


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_transfert", max_appels=30, periode_secondes=60, methodes={"POST"})
def api_transferer_message(request, message_id):
    m, msg, refus = _message_accessible(request, message_id)
    if refus:
        return refus
    nouveaux, erreur = mg.transferer(m, msg, _corps(request).get("conversations"))
    return _erreur(erreur) if erreur else JsonResponse({"ok": True, "copies": nouveaux})


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_reaction", max_appels=120, periode_secondes=60, methodes={"POST"})
def api_reaction_message(request, message_id):
    m, msg, refus = _message_accessible(request, message_id)
    if refus:
        return refus
    etat, erreur = mg.reagir_message(m, msg, _corps(request).get("emoji"))
    return _erreur(erreur) if erreur else JsonResponse({"ok": True, "reactions": etat})


@login_required
@require_http_methods(["POST"])
@limiter("messagerie_avis", max_appels=120, periode_secondes=60, methodes={"POST"})
def api_avis_message(request, message_id):
    """Bon / mauvais (privé) : {"valeur": 1 | -1 | 0}. Alimente le modèle fantôme du SIEM."""
    m, msg, refus = _message_accessible(request, message_id)
    if refus:
        return refus
    avis, erreur = mg.donner_avis(m, msg, _corps(request).get("valeur"))
    return _erreur(erreur) if erreur else JsonResponse({"ok": True, "mon_avis": avis})


@login_required
@require_http_methods(["POST"])
def api_favori_message(request, message_id):
    m, msg, refus = _message_accessible(request, message_id)
    if refus:
        return refus
    etat, erreur = mg.basculer_favori(m, msg)
    return _erreur(erreur) if erreur else JsonResponse({"ok": True, "favori": etat})


@login_required
@require_http_methods(["GET"])
def api_favoris(request):
    return JsonResponse({"resultats": mg.favoris_de(_membre(request))})


@login_required
@require_http_methods(["POST"])
def api_sourdine(request):
    corps = _corps(request)

    def entier(cle):
        try:
            return int(corps.get(cle) or 0)
        except (TypeError, ValueError):
            return 0
    ok, erreur = mg.mettre_en_sourdine(_membre(request), entier("membre_id"), entier("conversation_id"), bool(corps.get("actif", True)))
    return JsonResponse({"ok": True}) if ok else _erreur(erreur, 403 if "administrateur" in (erreur or "") else 400)


@login_required
@require_http_methods(["GET"])
def api_sourdines(request):
    return JsonResponse({"sourdines": mg.sourdines_liste(_membre(request))})


@login_required
@require_http_methods(["GET"])
def api_recherche(request):
    return JsonResponse({"resultats": mg.rechercher(_membre(request), request.GET.get("q", ""))})


# ── Publicités ────────────────────────────────────────────────────────────────
@login_required
@require_http_methods(["GET", "POST"])
def api_publicites(request):
    m = _membre(request)
    if not mg.est_equipe(m):
        return _erreur(t("engine/views_messagerie.py:g:403:24"), 403)
    if request.method == "POST":
        p, erreur = mg.creer_publicite(m, request.POST.get("titre"), request.POST.get("contexte"), request.FILES.get("fichier"),
                                       request.POST.get("table_id"))
        return _erreur(erreur) if erreur else JsonResponse({"id": p.id})
    liste = list(mg.publicites_gerees(m))
    cache = mg.noms([p.agent for p in liste])
    resumes = mg.resumes_reactions([p.id for p in liste])
    rep = {"publicites": [mg.serialiser_publicite(p, True, cache, resumes[p.id]) for p in liste],
           "tables": [{"id": x.id, "nom": getattr(x, "_nom_vue", None) or x.nom} for x in mg.tables_liables(m)]}
    if mg.chef(m.entreprise) is None or mg.chef(m.entreprise).id != m.id:
        # Agent : en plus des siennes, les publicités de l'administrateur (ou de qui il a délégué ce droit) — consultation seule, sans table liée.
        admin = mg.publicites_de_l_administration(m)
        noms_auteurs = mg.noms([p.agent for p in admin])
        rep["administration"] = [dict(mg.serialiser_publicite(p), agent=noms_auteurs[p.agent_id]) for p in admin]
    return JsonResponse(rep)


def _publicite_geree(request, publicite_id):
    m = _membre(request)
    if not mg.est_equipe(m):
        return None, _erreur(t("engine/views_messagerie.py:g:416:30"), 403)
    p = mg.publicites_gerees(m).filter(id=publicite_id).first()
    return (p, None) if p else (None, _erreur(t("engine/views_messagerie.py:g:418:47"), 404))


@login_required
@require_http_methods(["POST"])
def api_modifier_publicite(request, publicite_id):
    p, refus = _publicite_geree(request, publicite_id)
    if refus:
        return refus
    corps = _corps(request)
    if "publiee" in corps:
        p.publiee = bool(corps["publiee"])
    if "titre" in corps and str(corps["titre"]).strip():
        p.titre = str(corps["titre"]).strip()[:200]
    if "contexte" in corps:
        p.contexte = str(corps["contexte"]).strip()[:5000]
    if "table_id" in corps and str(corps["table_id"] or "") != str(p.table_id or ""):
        # None / "" = retirer le lien ; sinon une table que l'agent met à disposition des clients (un lien inchangé n'est pas revérifié)
        table, erreur = mg.table_liable(_membre(request), corps["table_id"])
        if erreur:
            return _erreur(erreur)
        p.table = table
    p.save()
    mg.journaliser(_membre(request), "PublicityUpdated", publicite_id=p.id, publicite=p.titre, publiee=p.publiee, texte_sha256=mg.empreinte(p.contexte),
                   table_id=p.table_id)
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["POST"])
def api_supprimer_publicite(request, publicite_id):
    p, refus = _publicite_geree(request, publicite_id)
    if refus:
        return refus
    mg.journaliser(_membre(request), "PublicityDeleted", publicite_id=p.id, publicite=p.titre)
    mg.supprimer_publicite(p)
    return JsonResponse({"ok": True})


@login_required
@require_http_methods(["GET"])
def api_publicites_client(request):
    m = _membre(request)
    liste = mg.publicites_pour(m)
    resumes = mg.resumes_reactions([p.id for p in liste], m)
    return JsonResponse({"publicites": [mg.serialiser_publicite(p, resume=resumes[p.id], client=m) for p in liste]})


@login_required
@require_http_methods(["POST"])
@limiter("publicite_reaction", max_appels=60, periode_secondes=60, methodes={"POST"})
def api_reagir_publicite(request, publicite_id):
    """Le client aime (1), n'aime pas (-1) ou commente (0, avec `texte`) une publicité de son agent."""
    corps = _corps(request)
    try:
        valeur = int(corps.get("valeur"))
    except (TypeError, ValueError):
        return _erreur(t("engine/views_messagerie.py:g:468:24"))
    if valeur not in (1, -1, 0):
        return _erreur(t("engine/views_messagerie.py:g:470:24"))
    m = _membre(request)
    _, erreur = mg.reagir(m, publicite_id, valeur, corps.get("texte"))
    if erreur:
        return _erreur(erreur)
    return JsonResponse({"ok": True, "resume": mg.resumes_reactions([publicite_id], m)[publicite_id]})


@login_required
@require_http_methods(["GET"])
def api_commentaires_publicite(request, publicite_id):
    liste = mg.commentaires_publicite(_membre(request), publicite_id)
    return _erreur(t("engine/views_messagerie.py:g:482:20"), 404) if liste is None else JsonResponse({"commentaires": liste})


# ── Notifications de messages (équipe ET clients) ─────────────────────────────
@login_required
@require_http_methods(["GET"])
def api_notifications_messages(request):
    """Messages non lus annoncés à la reconnexion (le client y accède, contrairement à /api/notifications/ réservé à l'équipe)."""
    liste, total = mg.notifications_non_lues(_membre(request))
    return JsonResponse({"notifications": liste, "non_lues": total})


@login_required
@require_http_methods(["POST"])
def api_notifications_messages_lire(request):
    return JsonResponse({"lues": mg.marquer_notifications_lues(_membre(request))})


# ── Chatbot de l'agent (équipe seulement) ─────────────────────────────────────
@login_required
@ensure_csrf_cookie
def page_chatbot(request):
    m = _membre(request)
    if not mg.est_equipe(m):
        return redirect("engine:messagerie")
    return render(request, "engine/chatbot.html", {"entreprise": m.entreprise})


@login_required
@require_http_methods(["GET", "POST"])
def api_chatbot(request):
    m = _membre(request)
    if not mg.est_equipe(m):
        return _erreur(t("engine/views_messagerie.py:g:515:24"), 403)
    if request.method == "GET":
        return JsonResponse({"chatbots": cb.lire(m)})
    corps = _corps(request)
    cfg, erreur = cb.enregistrer(m, corps.get("portee"), corps)
    return _erreur(erreur) if erreur else JsonResponse({"chatbot": cb.serialiser(cfg)})
