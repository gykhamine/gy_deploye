"""Endpoints API wallet : solde, recharge (dépôt USSD vérifié), transfert
interne, retrait (USSD sortant), configuration taux de change et frais."""
from engine.montant import saisie
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from engine.i18n import tf
from engine.i18n import t
from .delegation import a_pouvoir as _a_pouvoir
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from .models import MembreEntreprise, TauxChange, Bloc
from .ratelimit import limiter
from .views import _entreprise_courante
from . import wallet as _wallet, remuneration as _remuneration
from engine.i18n import t as _gt  # noqa: E402


def _membre_courant(request, entreprise):
    return MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()


@require_http_methods(["GET"])
@login_required
def mon_wallet(request):
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not membre:
        return JsonResponse({"error": t("engine/views_wallet.py:23:40")}, status=404)
    _remuneration.verser_dus(entreprise)   # un salaire échu est versé dès que quelqu'un consulte le wallet (idempotent)
    w = _wallet.obtenir_wallet(membre)
    reponse = {"solde": w.solde, "devise": "XOF"}
    if _a_pouvoir(membre, "portefeuilles"):
        reponse["wallet_principal"] = _wallet.repartition_par_agent(entreprise)["total"]
    return JsonResponse(reponse)


@require_http_methods(["GET"])
@login_required
def wallet_principal(request):
    """Wallet principal de l'entreprise (administrateur) : total de tous les wallets et sa répartition par agent."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not _a_pouvoir(membre, "portefeuilles"):
        return JsonResponse({"error": t("engine/views_wallet.py:g:40:39")}, status=403)
    _remuneration.verser_dus(entreprise)
    return JsonResponse({"devise": "XOF", **_wallet.repartition_par_agent(entreprise), "parts": _remuneration.parts(entreprise)})


@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def remuneration_agents(request):
    """Administrateur : GET = rémunération de chaque agent ; POST {membre_id, mode: aucun|salaire|pourcentage, salaire, jour_paie, pourcentage}."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not _a_pouvoir(membre, "remuneration"):
        return JsonResponse({"error": t("engine/views_wallet.py:g:53:39")}, status=403)
    if request.method == "POST":
        try:
            data = json.loads(request.body or b"{}")
            cible = MembreEntreprise.objects.filter(entreprise=entreprise, id=int(data.get("membre_id"))).first()
            if cible is None:
                return JsonResponse({"error": t("engine/views_wallet.py:g:59:47")}, status=404)
            _remuneration.configurer(entreprise, cible, data.get("mode"), data.get("salaire", 0), data.get("jour_paie", 28), data.get("pourcentage", 0))
        except (ValueError, TypeError) as e:
            return JsonResponse({"error": str(e) or t("engine/views_wallet.py:g:62:53")}, status=400)
        _remuneration.verser_dus(entreprise)
    return JsonResponse({"agents": _remuneration.liste_agents(entreprise)})


@require_http_methods(["GET"])
@login_required
def journal_wallet(request):
    """Administrateur : flux de l'entreprise (entrées, sorties, derniers mouvements) lus dans la blockchain."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not _a_pouvoir(membre, "journal_bilan"):
        return JsonResponse({"error": t("engine/views_wallet.py:g:74:39")}, status=403)
    return JsonResponse(_remuneration.journal(entreprise))




@require_http_methods(["POST"])
@csrf_protect
@login_required
def transferer_wallet(request):
    """Transfert interne wallet → wallet vers un autre client (par numéro)."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not membre:
        return JsonResponse({"error": t("engine/views_wallet.py:89:40")}, status=404)
    try:
        data = json.loads(request.body or b"{}")
        montant = saisie(data.get("montant", 0))
    except Exception:
        return JsonResponse({"error": t("engine/views_wallet.py:95:40")}, status=400)

    # Destinataire : `destinataire_id` (recherche), sinon l'ancien numéro de téléphone. Quel que soit son statut (client, agent, administrateur).
    dest = None
    if data.get("destinataire_id") not in (None, ""):
        try:
            dest = MembreEntreprise.objects.filter(entreprise=entreprise, pk=int(data.get("destinataire_id"))).first()
        except (TypeError, ValueError):
            dest = None
    else:
        numero_dest = (data.get("numero_destinataire") or "").strip()
        dest = MembreEntreprise.objects.filter(entreprise=entreprise, telephone=numero_dest).exclude(id=membre.id).first() if numero_dest else None
    if not dest:
        return JsonResponse({"error": t("engine/views_wallet.py:101:40")}, status=404)

    from . import wallet_especes as _especes
    try:
        w_src, _dst, rejeu = _especes.transferer_interne(entreprise, membre, dest, montant, data.get("motif") or "", data.get("cle"))
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"ok": True, "solde": w_src.solde, "deja_enregistre": rejeu})




@require_http_methods(["GET"])
@login_required
def wallets_clients(request):
    """Liste les wallets des clients (role='lecteur') — réservé aux
    agents/admin (voir wallet.lister_wallets_clients). Un agent ne voit que
    les clients qu'il a lui-même enregistrés ; l'admin voit tous les
    clients de l'entreprise."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"error": t("engine/views_wallet.py:145:40")}, status=403)

    from .views import _label_enregistrement
    agent_scopant = request.user if membre.role == "agent" else None
    clients = []
    for m, w in _wallet.lister_wallets_clients(entreprise, agent_scopant):
        label = _label_enregistrement(m.enregistrement_client) if m.enregistrement_client_id else ""
        clients.append({
            "membre_id": m.id,
            "nom": label or m.utilisateur.get_full_name() or m.utilisateur.get_username(),
            "telephone": f"{m.indicatif_pays}{m.telephone}" if m.telephone else "",
            "numero_client": m.numero_client,
            "matricule": m.enregistrement_client.matricule if m.enregistrement_client_id else "",
            "table_cliente": m.table_cliente.nom if m.table_cliente_id else "",
            "solde": w.solde,
        })
    return JsonResponse({"clients": clients, "total_solde": sum(c["solde"] for c in clients)})


@require_http_methods(["POST"])
@csrf_protect
@login_required
def synchroniser_wallet_table(request):
    """Bouton « Rendre analysable » : instantané des wallets de l'entreprise
    vers une TableDynamique normale (calculs/KPI/dashboards/ontologie/IA/PDF),
    réservé à l'admin (voir actions.synchroniser_wallet_table)."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    from . import actions
    try:
        table = actions.executer_action("synchroniser_wallet_table", membre, entreprise)
    except actions.ActionRefusee:
        return JsonResponse({"error": t("engine/views_wallet.py:g:237:39")}, status=403)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"ok": True, "table_id": table.id, "table_nom": table.nom})


@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def configurer_taux_change(request):
    """Taux de conversion vers le FCFA — lecture ouverte, écriture réservée
    à l'agent/admin."""
    entreprise = _entreprise_courante(request)
    if request.method == "GET":
        taux = TauxChange.objects.filter(entreprise=entreprise)
        return JsonResponse({t.devise: t.taux_vers_fcfa for t in taux})

    membre = _membre_courant(request, entreprise)
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"error": t("engine/views_wallet.py:177:40")}, status=403)
    try:
        data = json.loads(request.body)
        devise = data["devise"].upper()
        taux_valeur = saisie(data["taux_vers_fcfa"])
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
    TauxChange.objects.update_or_create(
        entreprise=entreprise, devise=devise, defaults={"taux_vers_fcfa": taux_valeur}
    )
    return JsonResponse({"ok": True})


_CHAMPS_FRAIS = ("taux_depot", "taux_retrait", "taux_transfert", "taux_interet", "taux_tva", "taux_tva_actif",
                 "taux_depot_actif", "taux_retrait_actif", "taux_transfert_actif",
                 "fixe_depot", "fixe_retrait", "fixe_transfert",
                 "fixe_depot_actif", "fixe_retrait_actif", "fixe_transfert_actif")


@require_http_methods(["GET", "POST"])
@csrf_protect
@login_required
def configurer_frais_wallet(request):
    """Taxes de dépôt/retrait/transfert et taux d'intérêt — modifiables
    par l'agent/admin uniquement."""
    entreprise = _entreprise_courante(request)
    frais = _wallet.obtenir_frais(entreprise)
    if request.method == "GET":
        return JsonResponse({c: getattr(frais, c) for c in _CHAMPS_FRAIS})

    membre = _membre_courant(request, entreprise)
    if not membre or membre.role not in ("admin", "agent"):
        return JsonResponse({"error": t("engine/views_wallet.py:206:40")}, status=403)
    try:
        data = json.loads(request.body)
    except Exception:
        data = {}
    import math
    for champ in _CHAMPS_FRAIS:
        if champ not in data:
            continue
        if champ.endswith("_actif"):
            setattr(frais, champ, bool(data[champ]))
            continue
        try:
            valeur = saisie(data[champ])
        except (TypeError, ValueError):
            valeur = math.nan
        # un taux ou un montant négatif / infini / NaN créditerait l'utilisateur au lieu de le taxer
        if not math.isfinite(valeur) or valeur < 0 or (champ.startswith("taux_") and valeur > 100):
            return JsonResponse({"error": t("engine/views_wallet.py:g:306:43") % champ}, status=400)
        setattr(frais, champ, valeur)
    frais.save()
    return JsonResponse({"ok": True})


@require_http_methods(["POST"])
@csrf_protect
@login_required
def souscrire_abonnement(request):
    """Débite le wallet et crée/prolonge l'abonnement mensuel 30j glissants.
    Body JSON : {"categorie": "dashboard_bi"|"ontologie"|"ia"}"""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not membre:
        return JsonResponse({"error": t("engine/views_wallet.py:227:40")}, status=404)
    try:
        data = json.loads(request.body or b"{}")
    except Exception:
        data = {}
    categorie = data.get("categorie", "")
    from .models import AbonnementMensuel
    cats = [c for c, _ in AbonnementMensuel.CATEGORIES]
    if categorie not in cats:
        return JsonResponse({"error": tf("engine/views_wallet.py:g:330:39", v1=cats)}, status=400)
    from . import wallet as _w
    ok, msg = _w.souscrire_abonnement(entreprise, membre, categorie)
    if not ok:
        return JsonResponse({"erreur": msg, "solde": _w.obtenir_wallet(membre).solde}, status=402)
    from .models import AbonnementMensuel as _Ab
    ab = _Ab.objects.get(membre=membre, categorie=categorie)
    return JsonResponse({"ok": True, "expire_le": ab.expire_le.isoformat(), "solde": _w.obtenir_wallet(membre).solde})


@require_http_methods(["POST"])
@csrf_protect
@login_required
def payer_depuis_caisse(request):
    """L'administrateur de l'entreprise paie un agent ou un client depuis la caisse (sans taxe).
    Body JSON : {"membre_id": int, "montant": nombre > 0, "motif": texte facultatif}."""
    entreprise = _entreprise_courante(request)
    membre = _membre_courant(request, entreprise)
    if not _a_pouvoir(membre, "payer_caisse"):
        return JsonResponse({"error": t("engine/views_wallet.py:g:349:39")}, status=403)
    try:
        data = json.loads(request.body or b"{}")
        cible = MembreEntreprise.objects.filter(entreprise=entreprise, id=int(data.get("membre_id"))).first()
        montant = saisie(data.get("montant"))
    except (ValueError, TypeError):
        return JsonResponse({"error": t("engine/views_wallet.py:g:355:39")}, status=400)
    if cible is None:
        return JsonResponse({"error": t("engine/views_wallet.py:g:357:39")}, status=404)
    try:
        wallet_caisse, _dest = _wallet.payer_depuis_caisse(entreprise, cible, montant, str(data.get("motif") or "")[:200])
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"ok": True, "solde_caisse": wallet_caisse.solde})
