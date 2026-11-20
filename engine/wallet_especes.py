"""Entrées, sorties et transferts du wallet, entièrement en interne : aucun prestataire.

  * Dépôt en espèces : le client remet l'argent à un agent HABILITÉ (permission donnée par l'administrateur), qui désigne le client et son
    agent puis enregistre. Le wallet du client est crédité (moins la taxe de dépôt, qui va à la caisse), l'écriture comptable et le bloc suivent.
  * Retrait en espèces : le client retire chez un agent habilité, qui lui remet l'argent. Le wallet est débité (plus la taxe de retrait).
    Un agent ne remet que les espèces qu'il détient ; l'administrateur peut lui en avancer (avance_especes) et enregistre ce qu'il reverse
    à l'entreprise (remise_especes).
  * Transfert interne : d'un wallet à un autre (client, agent, administrateur — quel que soit son statut), selon la politique de
    l'entreprise (types autorisés, minimum, maximum, plafond par jour).
Robustesse : verrou des wallets dans l'ordre des pk, contrôles faits SOUS verrou, écriture comptable dans la transaction de l'argent,
clé d'idempotence (un double clic ne dépose pas deux fois), aucun découvert, aucune auto-attribution par un agent.
"""
import datetime
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
import logging
import math

from django.db import transaction as _tx
from django.db.models import Q
from django.utils import timezone

from engine.i18n import t, tf
from . import comptabilite as _c, wallet as _w
from .frais_taxes import calculer_frais, tva_sur
from .models import Bloc, EcritureWallet, HabilitationFlux, MembreEntreprise, PolitiqueTransfert
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)

ROLES = ("admin", "agent", "lecteur")


class RefusPermission(ValueError):
    """L'opération demande une habilitation que le membre n'a pas (la vue répond 403)."""


# ── Politique de transfert ──────────────────────────────────────────────────────────────────────────────────────────────

def politique(entreprise) -> PolitiqueTransfert:
    pol, _ = PolitiqueTransfert.objects.get_or_create(entreprise=entreprise)
    return pol


def politique_en_dict(p) -> dict:
    return {"client_vers_client": p.client_vers_client, "client_vers_agent": p.client_vers_agent,
            "agent_vers_client": p.agent_vers_client, "agent_vers_agent": p.agent_vers_agent}


def enregistrer_politique(entreprise, donnees) -> PolitiqueTransfert:
    """Enregistre les types de transferts autorisés. Aucune limite de montant."""
    if not isinstance(donnees, dict):
        raise ValueError(t("engine/wallet_especes.py:g:49:26"))
    pol = politique(entreprise)
    for champ in ("client_vers_client", "client_vers_agent", "agent_vers_client", "agent_vers_agent"):
        if champ in donnees:
            setattr(pol, champ, bool(donnees[champ]))
    pol.save()
    return pol


def _categorie(m) -> str:
    return "agent" if m.role in ("admin", "agent") else "client"


# ── Habilitations ───────────────────────────────────────────────────────────────────────────────────────────────────────

def est_habilite(membre) -> bool:
    """L'administrateur l'est toujours ; un agent seulement si l'administrateur lui en a donné la permission."""
    if membre is None:
        return False
    from .delegation import a_pouvoir
    if a_pouvoir(membre, "especes_agent"):
        return True
    return membre.role == "agent" and HabilitationFlux.objects.filter(membre=membre, actif=True).exists()


def accorder(entreprise, admin, cible, actif=True) -> HabilitationFlux:
    """Donne ou retire à un agent le droit d'enregistrer des espèces. Réservé à l'administrateur ; tracé dans la blockchain."""
    from .delegation import a_pouvoir
    if not a_pouvoir(admin, "especes_habilitations") or admin.entreprise_id != entreprise.id:
        raise RefusPermission(t("engine/wallet_especes.py:g:76:31"))
    if cible is None or cible.entreprise_id != entreprise.id or cible.role != "agent":
        raise ValueError(t("engine/wallet_especes.py:g:78:26"))
    hab, _ = HabilitationFlux.objects.update_or_create(membre=cible, defaults={"actif": bool(actif), "accorde_par": admin})
    try:
        Bloc.ajouter_transaction(entreprise.id, {"type": "wallet_habilitation", "agent_id": admin.utilisateur_id,
                                                 "donnees": {"agent_membre_id": cible.id, "actif": bool(actif)}})
    except Exception:
        logger.exception(_gt("rg6.msg.132"), cible.pk)
    return hab


def lister_habilites(entreprise) -> list:
    actifs = set(HabilitationFlux.objects.filter(membre__entreprise=entreprise, actif=True).values_list("membre_id", flat=True))
    agents = MembreEntreprise.objects.filter(entreprise=entreprise, role="agent").select_related("utilisateur")
    return [{"membre_id": m.id, "nom": _c._nom(m), "habilite": m.id in actifs,
             "especes": _c.especes_detenues(entreprise, m) if m.id in actifs else 0.0} for m in agents]


# ── Recherche de membres ────────────────────────────────────────────────────────────────────────────────────────────────

def _agent_de(membre):
    if membre.role in ("admin", "agent"):
        return membre
    from . import messagerie as _mg
    return _mg.agent_de(membre)


def chercher_membres(entreprise, q, detail=False, exclure=None, limite=15) -> list:
    """Recherche par nom, identifiant, téléphone ou numéro de client (3 caractères au moins, sauf un numéro de client exact).
    `detail` (agent habilité seulement) ajoute téléphone, solde et agent responsable ; sinon : nom, numéro et rôle."""
    q = str(q or "").strip()
    crit = Q()
    if q.isdigit():
        crit |= Q(numero_client=int(q))
    if len(q) >= 3:
        crit |= Q(utilisateur__username__icontains=q) | Q(utilisateur__first_name__icontains=q) | Q(utilisateur__last_name__icontains=q) | Q(telephone__icontains=q)
    if not crit:
        return []
    qs = MembreEntreprise.objects.filter(entreprise=entreprise).exclude(role="caisse").filter(crit).select_related("utilisateur")
    if exclure is not None:
        qs = qs.exclude(pk=exclure.pk)
    out = []
    for m in qs[:max(1, min(int(limite), 50))]:
        ligne = {"id": m.id, "nom": _c._nom(m), "role": m.role, "role_libelle": t("engine/wallet_especes.py:role:" + m.role) if m.role in ROLES else m.role, "numero_client": m.numero_client}
        if detail:
            ag = _agent_de(m)
            ligne.update(telephone=f"{m.indicatif_pays}{m.telephone}" if m.telephone else "", solde=_w.obtenir_wallet(m).solde,
                         agent_id=ag.id if ag else None, agent=_c._nom(ag) if ag else "")
        out.append(ligne)
    return out


def agents_choisissables(entreprise) -> list:
    return [{"id": m.id, "nom": _c._nom(m), "role": m.role} for m in
            MembreEntreprise.objects.filter(entreprise=entreprise, role__in=("admin", "agent")).select_related("utilisateur")]


# ── Dépôts et retraits en espèces ───────────────────────────────────────────────────────────────────────────────────────

def _frais(entreprise, evenement, montant):
    cfg = _w.obtenir_frais(entreprise)
    brut = calculer_frais(cfg, evenement, montant)
    tva = tva_sur(cfg, brut)
    return round(brut + tva, 2)


def _preparer(entreprise, operateur, client, montant, agent, cle):
    """Contrôles communs aux dépôts et retraits en espèces ; retourne (agent responsable, clé)."""
    if operateur is None or operateur.entreprise_id != entreprise.id or not est_habilite(operateur):
        raise RefusPermission(t("engine/wallet_especes.py:g:146:31"))
    _w._verifier_montant(montant)
    if client is None or client.entreprise_id != entreprise.id:
        raise ValueError(t("engine/wallet_especes.py:g:149:26"))
    if client.pk == operateur.pk and operateur.role != "admin":
        raise ValueError(t("engine/wallet_especes.py:g:151:26"))
    if agent is None:
        agent = _agent_de(client)
    elif agent.entreprise_id != entreprise.id or agent.role not in ("admin", "agent"):
        raise ValueError(t("engine/wallet_especes.py:g:155:26"))
    return agent, (str(cle).strip()[:64] or None) if cle else None


def _rejeu(entreprise, cle, type_, client):
    """Une clé déjà utilisée pour la même opération renvoie l'écriture d'origine (idempotence) ; pour une autre : refus."""
    deja = _c.ecriture_par_cle(entreprise, cle)
    if deja is None:
        return None
    if deja.type != type_ or deja.client_id != client.pk:
        raise ValueError(t("engine/wallet_especes.py:g:165:26"))
    return deja


def _resultat(e, wallet, deja=False) -> dict:
    return {"ecriture": e, "solde": wallet.solde, "frais": e.frais, "deja": deja}


def enregistrer_depot_especes(entreprise, operateur, client, montant, agent=None, cle=None, motif="") -> dict:
    """Le client a remis `montant` FCFA en espèces à `operateur` : son wallet est crédité de `montant` moins la taxe de dépôt (à la caisse)."""
    agent, cle = _preparer(entreprise, operateur, client, montant, agent, cle)
    caisse = _w._caisse_ou_erreur(entreprise)
    frais = _frais(entreprise, "depot", montant)
    if frais >= montant:
        raise ValueError(t("engine/wallet_especes.py:g:179:26"))
    with _tx.atomic():
        w = _w._verrouiller(client, caisse)
        deja = _rejeu(entreprise, cle, "depot_especes", client)
        if deja:
            return _resultat(deja, w[client.pk], deja=True)
        w[client.pk].solde += montant - frais
        w[caisse.pk].solde += frais
        _w._enregistrer(w[client.pk], w[caisse.pk])
        e = _c.noter(entreprise, "depot_especes", "entree", montant, frais=frais, client=client, dest=client, agent=agent, operateur=operateur,
                     motif=str(motif or "").strip(), cle=cle)
    _c.ecrire_bloc(e, agent_user_id=operateur.utilisateur_id)
    return _resultat(e, w[client.pk])


def enregistrer_retrait_especes(entreprise, operateur, client, montant, agent=None, cle=None, motif="") -> dict:
    """Le client retire `montant` FCFA en espèces chez `operateur` : son wallet est débité de `montant` plus la taxe de retrait (à la caisse).
    Un agent (non administrateur) ne remet que les espèces qu'il détient."""
    from .siem_noyau import exiger_hors_lockdown   # SIEM : lockdown = retraits coupés
    exiger_hors_lockdown("retrait_especes")
    agent, cle = _preparer(entreprise, operateur, client, montant, agent, cle)
    caisse = _w._caisse_ou_erreur(entreprise)
    frais = _frais(entreprise, "retrait", montant)
    with _tx.atomic():
        w = _w._verrouiller(client, caisse)
        deja = _rejeu(entreprise, cle, "retrait_especes", client)
        if deja:
            return _resultat(deja, w[client.pk], deja=True)
        if w[client.pk].solde < montant + frais:
            raise ValueError(tf("engine/wallet_especes.py:g:206:30", v1=w[client.pk].solde, v2=montant + frais))
        if operateur.role != "admin":
            MembreEntreprise.objects.select_for_update().get(pk=operateur.pk)   # deux retraits simultanés du même agent se suivent
            detenues = _c.especes_detenues(entreprise, operateur)
            if detenues < montant:
                raise ValueError(tf("engine/wallet_especes.py:g:211:34", v1=max(detenues, 0)))
        w[client.pk].solde -= montant + frais
        w[caisse.pk].solde += frais
        _w._enregistrer(w[client.pk], w[caisse.pk])
        e = _c.noter(entreprise, "retrait_especes", "sortie", montant, frais=frais, client=client, source=client, agent=agent, operateur=operateur,
                     motif=str(motif or "").strip(), cle=cle)
    _c.ecrire_bloc(e, agent_user_id=operateur.utilisateur_id)
    from .wallet_flux import _notifier_sortie
    _notifier_sortie(entreprise, _gtf("rg6.msg.133", v1=_c._nom(client), v2=_c._nom(operateur)), montant)
    return _resultat(e, w[client.pk])


def mouvement_especes_agent(entreprise, admin, agent, montant, sens="remise", motif="", cle=None) -> EcritureWallet:
    """Administrateur : enregistre les espèces qu'un agent reverse à l'entreprise (`remise`) ou que l'entreprise lui avance (`avance`) pour
    payer des retraits. Aucun solde de wallet ne bouge : seule la détention d'espèces de l'agent change."""
    from .delegation import a_pouvoir
    if not a_pouvoir(admin, "especes_mouvements") or admin.entreprise_id != entreprise.id:
        raise RefusPermission(t("engine/wallet_especes.py:g:225:31"))
    if sens not in ("remise", "avance"):
        raise ValueError(t("engine/wallet_especes.py:g:227:26"))
    _w._verifier_montant(montant)
    if agent is None or agent.entreprise_id != entreprise.id or agent.role != "agent":
        raise ValueError(t("engine/wallet_especes.py:g:230:26"))
    cle = (str(cle).strip()[:64] or None) if cle else None
    type_ = "remise_especes" if sens == "remise" else "avance_especes"
    with _tx.atomic():
        MembreEntreprise.objects.select_for_update().get(pk=agent.pk)
        deja = _c.ecriture_par_cle(entreprise, cle)
        if deja:
            if deja.type != type_:
                raise ValueError(t("engine/wallet_especes.py:g:238:34"))
            return deja
        if sens == "remise" and _c.especes_detenues(entreprise, agent) < montant:
            raise ValueError(t("engine/wallet_especes.py:g:241:30"))
        e = _c.noter(entreprise, type_, "interne", montant, source=agent if sens == "remise" else admin, dest=admin if sens == "remise" else agent,
                     operateur=admin, motif=str(motif or "").strip(), cle=cle, touche_wallet=False)
    _c.ecrire_bloc(e, agent_user_id=admin.utilisateur_id)
    return e


# ── Transferts internes ─────────────────────────────────────────────────────────────────────────────────────────────────

def transferer_interne(entreprise, source, dest, montant, motif="", cle=None):
    """Transfert wallet à wallet selon la politique de l'entreprise. Les contrôles (types autorisés, solde) se font
    SOUS verrou des trois wallets (source, destinataire, caisse), dans l'ordre des pk. Retourne (wallet source, wallet destinataire, rejeu)."""
    _w._verifier_montant(montant)
    if source is None or dest is None or source.entreprise_id != entreprise.id or dest.entreprise_id != entreprise.id:
        raise ValueError(t("engine/wallet_especes.py:g:255:26"))
    if "caisse" in (source.role, dest.role):
        raise ValueError(_i18n_t("rg2.wallet_especes.1"))
    if source.pk == dest.pk:
        raise ValueError(t("engine/wallet_especes.py:g:257:26"))
    pol = politique(entreprise)
    genre = f"{_categorie(source)}_vers_{_categorie(dest)}"
    if not getattr(pol, genre):
        raise ValueError(tf("engine/wallet_especes.py:g:261:26", v1=genre.replace('_vers_', ' vers ')))
    cle = (str(cle).strip()[:64] or None) if cle else None
    caisse = _w._caisse_ou_erreur(entreprise)
    with _tx.atomic():
        w = _w._verrouiller(source, dest, caisse)
        deja = _c.ecriture_par_cle(entreprise, cle)
        if deja:
            if deja.type != "transfert" or deja.membre_source_id != source.pk or deja.membre_dest_id != dest.pk:
                raise ValueError(t("engine/wallet_especes.py:g:269:34"))
            return w[source.pk], w[dest.pk], True
        src, dst = _w.transferer(entreprise, source, dest, montant, avec_frais=True, motif=str(motif or "").strip()[:200], cle=cle)
    return src, dst, False


# ── Page de l'agent ─────────────────────────────────────────────────────────────────────────────────────────────────────

def resume_agent(entreprise, operateur) -> dict:
    """Ce que voit l'agent habilité : espèces qu'il détient et ses opérations du jour."""
    debut = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    jour = EcritureWallet.objects.filter(entreprise=entreprise, operateur=operateur, cree_le__gte=debut)
    depots = [e.montant for e in jour if e.type == "depot_especes"]
    retraits = [e.montant for e in jour if e.type == "retrait_especes"]
    return {"especes_detenues": _c.especes_detenues(entreprise, operateur), "depots_jour": sum(depots), "nb_depots_jour": len(depots),
            "retraits_jour": sum(retraits), "nb_retraits_jour": len(retraits)}
