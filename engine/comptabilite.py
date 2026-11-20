"""Comptabilité du wallet — automatique, interne, sans prestataire.

Chaque mouvement d'argent (dépôt ou retrait en espèces, transfert, service payé, commission, salaire, charge) écrit UNE ligne
(EcritureWallet) dans la MÊME transaction que le changement de solde : il n'existe pas de mouvement sans écriture, et une écriture ne se
modifie ni ne se supprime. Le bloc de la blockchain est écrit après (même schéma que wallet.py) ; s'il échoue, `bloc_index` reste vide et
`reparer_blocs` le réécrit sans doublon.

Cohérence (voir bilan) : somme des wallets = somme des entrées - somme des sorties du journal. Tout écart est affiché, jamais caché.
"""
from engine.montant import saisie
import datetime
import logging
import math
import time

from django.db.models import Count, Sum
from django.utils import timezone

from engine.i18n import t as _t, tf as _tf
from .models import Bloc, EcritureWallet, HabilitationFlux, MembreEntreprise, Wallet
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

TYPES = dict(EcritureWallet.TYPES)


def libelle_type(code):
    """Libellé du type d'écriture dans la langue courante."""
    return _t("engine/comptabilite.py:type:" + code) if code in TYPES else code
# Types d'écriture dont l'effet sur le wallet principal est une entrée ou une sortie (le reste est interne ou sans effet sur un solde).
_BLOC_PAR_TYPE = {"depot_especes": "wallet_depot_especes", "retrait_especes": "wallet_retrait_especes",
                  "remise_especes": "wallet_remise_especes", "avance_especes": "wallet_avance_especes",
                  "entree_caisse": "wallet_entree_caisse"}


def _nombre(valeur) -> float:
    try:
        v = saisie(valeur)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def noter(entreprise, type_, sens, montant, *, frais=0.0, source=None, dest=None, client=None, agent=None, operateur=None,
          categorie="", motif="", cle=None, touche_wallet=True) -> EcritureWallet:
    """Écrit une ligne du journal. À appeler DANS le `transaction.atomic()` qui change les soldes."""
    if type_ not in TYPES:
        raise ValueError(_t("engine/comptabilite.py:g:40:26"))
    if sens not in ("entree", "sortie", "interne"):
        raise ValueError(_t("engine/comptabilite.py:g:42:26"))
    if not math.isfinite(_nombre(montant)) or _nombre(montant) < 0:
        raise ValueError(_t("engine/comptabilite.py:g:44:26"))
    return EcritureWallet.objects.create(
        entreprise=entreprise, type=type_, sens=sens, montant=_nombre(montant), frais=_nombre(frais), touche_wallet=touche_wallet,
        membre_source=source, membre_dest=dest, client=client, agent_responsable=agent, operateur=operateur,
        categorie=str(categorie or "")[:24], motif=str(motif or "")[:200], cle=(str(cle)[:64] if cle else None))


def ecriture_par_cle(entreprise, cle):
    return EcritureWallet.objects.filter(entreprise=entreprise, cle=cle).first() if cle else None


def _donnees_bloc(e: EcritureWallet) -> dict:
    return {"ecriture_id": e.id, "montant": e.montant, "frais_montant": e.frais, "motif": e.motif, "reference": e.cle or "",
            "client_membre_id": e.client_id, "agent_membre_id": e.agent_responsable_id, "operateur_membre_id": e.operateur_id,
            "source_membre_id": e.membre_source_id, "destinataire_membre_id": e.membre_dest_id}


def ecrire_bloc(e: EcritureWallet, type_bloc=None, agent_user_id=None):
    """Bloc de la blockchain pour une écriture (après la transaction d'argent). Ne casse JAMAIS l'opération : un échec laisse `bloc_index` vide."""
    type_bloc = type_bloc or _BLOC_PAR_TYPE.get(e.type)
    if not type_bloc:
        return None
    try:
        bloc = Bloc.ajouter_transaction(e.entreprise_id, {"type": type_bloc, "agent_id": agent_user_id, "donnees": _donnees_bloc(e)})
        EcritureWallet.objects.filter(pk=e.pk).update(bloc_index=bloc.index)
        e.bloc_index = bloc.index
        return bloc
    except Exception:
        logger.exception(_gt("rg6.msg.002"), e.pk)
        return None


def reparer_blocs(entreprise) -> dict:
    """Réécrit les blocs manquants des écritures d'espèces, sans doublon (on cherche d'abord `ecriture_id` dans la chaîne)."""
    manquantes = list(EcritureWallet.objects.filter(entreprise=entreprise, bloc_index__isnull=True, type__in=list(_BLOC_PAR_TYPE)).select_related("operateur"))
    if not manquantes:
        return {"manquants": 0, "repares": 0}
    deja = {}
    for b in Bloc.objects.filter(entreprise=entreprise).only("index", "transactions").iterator():
        for tx in b.transactions or []:
            eid = (tx.get("donnees") or {}).get("ecriture_id")
            if eid:
                deja[eid] = b.index
    repares = 0
    for e in manquantes:
        if e.id in deja:
            EcritureWallet.objects.filter(pk=e.pk).update(bloc_index=deja[e.id])
            repares += 1
        elif ecrire_bloc(e, agent_user_id=getattr(e.operateur, "utilisateur_id", None)):
            repares += 1
    return {"manquants": len(manquantes), "repares": repares}


# ── Lecture ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _nom(m) -> str:
    if m is None:
        return ""
    u = m.utilisateur
    return f"{u.first_name} {u.last_name}".strip() or u.username


def ecriture_en_dict(e) -> dict:
    return {"id": e.id, "date": e.cree_le.isoformat(), "type": e.type, "type_libelle": libelle_type(e.type), "sens": e.sens,
            "montant": e.montant, "frais": e.frais, "touche_wallet": e.touche_wallet, "motif": e.motif, "categorie": e.categorie,
            "source": _nom(e.membre_source), "destinataire": _nom(e.membre_dest), "client": _nom(e.client), "agent": _nom(e.agent_responsable),
            "operateur": _nom(e.operateur), "reference": e.cle or "", "bloc": e.bloc_index}


def lister(entreprise, jours=None, types=None, membre=None, operateur=None, limite=200) -> list:
    qs = EcritureWallet.objects.filter(entreprise=entreprise).select_related(
        "membre_source__utilisateur", "membre_dest__utilisateur", "client__utilisateur", "agent_responsable__utilisateur", "operateur__utilisateur")
    if jours:
        qs = qs.filter(cree_le__gte=timezone.now() - datetime.timedelta(days=int(jours)))
    if types:
        qs = qs.filter(type__in=types)
    if operateur is not None:
        qs = qs.filter(operateur=operateur)
    if membre is not None:
        from django.db.models import Q
        qs = qs.filter(Q(membre_source=membre) | Q(membre_dest=membre) | Q(client=membre))
    return [ecriture_en_dict(e) for e in qs[:max(1, min(int(limite), 500))]]


def especes_detenues(entreprise, agent) -> float:
    """Espèces que `agent` détient pour l'entreprise : dépôts reçus - retraits payés - remises faites + avances reçues. Négatif = l'entreprise lui doit."""
    base = EcritureWallet.objects.filter(entreprise=entreprise, operateur=agent)
    depots = base.filter(type="depot_especes").aggregate(s=Sum("montant"))["s"] or 0.0
    retraits = base.filter(type="retrait_especes").aggregate(s=Sum("montant"))["s"] or 0.0
    remises = EcritureWallet.objects.filter(entreprise=entreprise, type="remise_especes", membre_source=agent).aggregate(s=Sum("montant"))["s"] or 0.0
    avances = EcritureWallet.objects.filter(entreprise=entreprise, type="avance_especes", membre_dest=agent).aggregate(s=Sum("montant"))["s"] or 0.0
    return round(depots - retraits - remises + avances, 2)


def especes_par_agent(entreprise) -> list:
    ids = set(EcritureWallet.objects.filter(entreprise=entreprise, type__in=["depot_especes", "retrait_especes"]).values_list("operateur_id", flat=True))
    ids |= set(HabilitationFlux.objects.filter(membre__entreprise=entreprise, actif=True).values_list("membre_id", flat=True))
    ids.discard(None)
    out = [{"membre_id": m.id, "nom": _nom(m), "especes": especes_detenues(entreprise, m)}
           for m in MembreEntreprise.objects.filter(pk__in=ids).select_related("utilisateur")]
    return sorted(out, key=lambda a: -abs(a["especes"]))


def coherence(entreprise) -> dict:
    """Somme des wallets contre somme (entrées - sorties) du journal : `ecart` doit rester à 0."""
    wallets = Wallet.objects.filter(membre__entreprise=entreprise).aggregate(s=Sum("solde"))["s"] or 0.0
    qs = EcritureWallet.objects.filter(entreprise=entreprise, touche_wallet=True)
    entrees = qs.filter(sens="entree").aggregate(s=Sum("montant"))["s"] or 0.0
    sorties = qs.filter(sens="sortie").aggregate(s=Sum("montant"))["s"] or 0.0
    ecart = round(wallets - (entrees - sorties), 2)
    return {"total_wallets": round(wallets, 2), "total_journal": round(entrees - sorties, 2), "ecart": ecart, "coherent": abs(ecart) < 0.01}


def bilan(entreprise, jours=30) -> dict:
    """Page de comptabilité : entrées, sorties, transferts et frais sur `jours` jours, caisse, espèces par agent, cohérence, blocs manquants."""
    from . import wallet as _w
    jours = max(1, min(int(jours), 3650))
    qs = EcritureWallet.objects.filter(entreprise=entreprise, cree_le__gte=timezone.now() - datetime.timedelta(days=jours))
    par_type = {r["type"]: r for r in qs.values("type").annotate(total=Sum("montant"), nb=Count("id"), frais=Sum("frais"))}

    def tot(t):
        return round(par_type.get(t, {}).get("total") or 0.0, 2)

    def nb(t):
        return par_type.get(t, {}).get("nb") or 0
    charges_cat = {r["categorie"] or "autre": round(r["total"], 2) for r in qs.filter(type="charge").values("categorie").annotate(total=Sum("montant"))}
    entrees = tot("depot_especes") + tot("depot_prestataire") + tot("entree_caisse")
    sorties = tot("retrait_especes") + tot("retrait_prestataire") + tot("charge")
    interne = sum(tot(t) for t in ("transfert", "paiement_caisse", "salaire", "service", "commission"))
    frais = round(sum((r.get("frais") or 0.0) for r in par_type.values()), 2)
    caisse = _w.membre_caisse(entreprise)
    manquants = EcritureWallet.objects.filter(entreprise=entreprise, bloc_index__isnull=True, type__in=list(_BLOC_PAR_TYPE)).count()
    return {
        "jours": jours, "devise": "XOF",
        "entrees": {"depots_especes": tot("depot_especes"), "nb_depots_especes": nb("depot_especes"), "depots_prestataire": tot("depot_prestataire"), "entrees_caisse": tot("entree_caisse"), "total": round(entrees, 2)},
        "sorties": {"retraits_especes": tot("retrait_especes"), "nb_retraits_especes": nb("retrait_especes"), "retraits_prestataire": tot("retrait_prestataire"),
                    "charges": tot("charge"), "nb_charges": nb("charge"), "charges_par_categorie": charges_cat, "total": round(sorties, 2)},
        "net_externe": round(entrees - sorties, 2),
        "internes": {"transferts": tot("transfert"), "nb_transferts": nb("transfert"), "paiements_caisse": tot("paiement_caisse"), "salaires": tot("salaire"),
                     "services": tot("service"), "commissions": tot("commission"), "total": round(interne, 2)},
        "frais_percus": frais,
        "caisse": {"solde": _w.obtenir_wallet(caisse).solde if caisse else 0.0},
        "especes": {"remises": tot("remise_especes"), "avances": tot("avance_especes"), "par_agent": especes_par_agent(entreprise)},
        "coherence": coherence(entreprise), "blocs_manquants": manquants,
        "chaine_valide": Bloc.chaine_valide(entreprise.id),
    }
