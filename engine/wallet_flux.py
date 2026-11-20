"""Flux du wallet : ce qui sort, ce qui vient des prestataires — rien ne se perd.

  * Moyens de retrait : chaque membre (agent, client, administrateur) enregistre lui-même le compte mobile money où il reçoit son
    argent (MoyenRetrait). Jamais de numéro de carte : il est refusé (valider_numero).
  * Retraits : chaque retrait est tracé en base (RetraitWallet, effectué ou en échec) ET dans la blockchain (bloc « wallet_retrait »,
    écrit par wallet.retirer). Un échec ne débite rien.
  * Charges et coûts : l'entreprise ne fait pas qu'encaisser. Une charge (loyer, fournisseur, impôt…) sort de la caisse, en base
    (ChargeWallet) ET dans la blockchain (bloc « wallet_charge »). Pas de découvert : caisse insuffisante = refus.
  * Prestataires : chaque événement d'un prestataire de paiement (dépôt initié, wallet crédité, échec, mis en attente de décision)
    est une ligne en base (EvenementPrestataire) ET un bloc « wallet_prestataire ». Le bloc « wallet_depot » garde aussi le prestataire,
    la référence et l'identifiant de transaction chez lui : de quoi rapprocher et rembourser.
  * Bilan : entrées et sorties de l'entreprise sur une période, lues dans la blockchain (voir bilan).

Le bloc est écrit APRÈS la transaction qui bouge l'argent (même schéma que wallet.py : le verrou de blocs ne doit pas être tenu dans une
transaction de base). Si l'écriture du bloc échoue, la ligne en base garde `bloc_index` vide : le manque se voit, il n'est pas caché.
"""
from engine.montant import saisie
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
import datetime
import logging
import math
import re
import time

from django.db import transaction as _tx
from django.utils import timezone

from . import wallet as _wallet
from .models import Bloc, ChargeWallet, EvenementPrestataire, MembreEntreprise, MoyenRetrait, RetraitWallet
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

MAX_MOYENS = 5
CATEGORIES_CHARGE = dict(ChargeWallet.CATEGORIES)
_CHIFFRES = re.compile(r"^[0-9]{6,15}$")
MSG_CARTE = _i18n_td("rg2.wallet_flux.1")


def _nombre(valeur) -> float:
    try:
        v = saisie(valeur)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


# ── Moyens de retrait ───────────────────────────────────────────────────────────────────────────────────────────────────



















# ── Retraits ────────────────────────────────────────────────────────────────────────────────────────────────────────────







# ── Charges et coûts ────────────────────────────────────────────────────────────────────────────────────────────────────

def enregistrer_charge(entreprise, auteur, categorie, libelle, montant, date_charge=None, beneficiaire="") -> ChargeWallet:
    """L'entreprise paie une charge depuis sa caisse : débit de la caisse sous verrou (jamais de découvert), ligne en base, bloc
    « wallet_charge ». `auteur` : le membre administrateur qui l'enregistre."""
    _wallet._verifier_montant(montant)
    if categorie not in CATEGORIES_CHARGE:
        raise ValueError(_i18n_t("rg2.wallet_flux.6"))
    libelle = str(libelle or "").strip()[:120]
    if not libelle:
        raise ValueError(_i18n_t("rg2.wallet_flux.7"))
    if date_charge in (None, ""):
        jour = timezone.localdate()
    elif isinstance(date_charge, datetime.date):
        jour = date_charge
    else:
        try:
            jour = datetime.date.fromisoformat(str(date_charge)[:10])
        except ValueError:
            raise ValueError(_gt("rg5.msg.003"))
    if jour > timezone.localdate():
        raise ValueError(_i18n_t("rg2.wallet_flux.8"))
    caisse = _wallet._caisse_ou_erreur(entreprise)
    with _tx.atomic():
        w = _wallet._verrouiller(caisse)[caisse.pk]
        if w.solde < montant:
            raise ValueError(_i18n_t("rg2.wallet_flux.9"))
        w.solde -= montant
        _wallet._enregistrer(w)
        charge = ChargeWallet.objects.create(entreprise=entreprise, categorie=categorie, libelle=libelle, montant=montant, date_charge=jour,
                                             beneficiaire=str(beneficiaire or "").strip()[:120], cree_par=auteur)
        from . import comptabilite as _compta
        _compta.noter(entreprise, "charge", "sortie", montant, source=caisse, operateur=auteur, categorie=categorie, motif=libelle)
    try:
        bloc = Bloc.ajouter_transaction(entreprise.id, {
            "type": "wallet_charge", "agent_id": getattr(auteur, "utilisateur_id", None),
            "donnees": {"montant": montant, "categorie": categorie, "motif": libelle, "beneficiaire": charge.beneficiaire,
                        "charge_id": charge.id, "date_charge": jour.isoformat(), "caisse_membre_id": caisse.id}})
        charge.bloc_index = bloc.index
        charge.save(update_fields=["bloc_index"])
    except Exception:
        logger.exception(_gt("rg6.msg.136"), charge.id)
    _notifier_sortie(entreprise, f"Charge {CATEGORIES_CHARGE[categorie]} : {libelle}", montant)
    return charge


def _notifier_sortie(entreprise, libelle, montant):
    try:
        from . import notifications as _n
        _n.notifier_sortie_especes(entreprise, libelle, montant)
    except Exception:
        logger.exception(_gt("rg6.msg.137"))


def charge_en_dict(c) -> dict:
    return {"id": c.id, "date": c.date_charge.isoformat(), "categorie": c.categorie, "categorie_libelle": CATEGORIES_CHARGE.get(c.categorie, c.categorie),
            "libelle": c.libelle, "beneficiaire": c.beneficiaire, "montant": c.montant, "bloc": c.bloc_index}


def lister_charges(entreprise, jours=None, limite=200) -> list:
    qs = ChargeWallet.objects.filter(entreprise=entreprise)
    if jours:
        qs = qs.filter(date_charge__gte=timezone.localdate() - datetime.timedelta(days=int(jours)))
    return [charge_en_dict(c) for c in qs[:max(1, min(int(limite), 500))]]


# ── Prestataires de paiement ────────────────────────────────────────────────────────────────────────────────────────────









# ── Bilan : entrées et sorties ──────────────────────────────────────────────────────────────────────────────────────────

def bilan(entreprise, jours=30) -> dict:
    """Entrées et sorties de l'entreprise sur `jours` jours, lues dans la blockchain (tous les blocs de la période, sans plafond) :
      - entrées externes : les dépôts (tous prestataires et USSD) ;
      - sorties externes : les retraits et les charges ;
      - caisse : ce qui entre (taxes, paiements de services) et sort (salaires, commissions, versements, charges) de la caisse.
    Les salaires et versements aux agents ne quittent pas l'entreprise : ils sont dans la caisse, pas dans les sorties externes."""
    from .remuneration import EFFET_CAISSE
    jours = max(1, min(int(jours), 3650))
    depuis = time.time() - jours * 86400
    depots = retraits = charges = entrees_caisse = sorties_caisse = 0.0
    nb = {"depots": 0, "retraits": 0, "charges": 0}
    par_categorie = {}
    for b in Bloc.objects.filter(entreprise=entreprise, timestamp__gte=depuis).only("transactions").iterator():
        for tx in b.transactions or []:
            type_ = tx.get("type", "")
            if not type_.startswith("wallet_"):
                continue
            d = tx.get("donnees") or {}
            cle, signe = EFFET_CAISSE.get(type_, (None, 0))
            effet = signe * _nombre(d.get(cle)) if cle else 0.0
            entrees_caisse, sorties_caisse = entrees_caisse + max(effet, 0), sorties_caisse + max(-effet, 0)
            m = _nombre(d.get("montant"))
            if type_ in ("wallet_depot", "wallet_depot_especes"):
                depots, nb["depots"] = depots + m, nb["depots"] + 1
            elif type_ in ("wallet_retrait", "wallet_retrait_especes"):
                retraits, nb["retraits"] = retraits + m, nb["retraits"] + 1
            elif type_ == "wallet_charge":
                charges, nb["charges"] = charges + m, nb["charges"] + 1
                cat = d.get("categorie") or "autre"
                par_categorie[cat] = par_categorie.get(cat, 0.0) + m
    caisse = _wallet.membre_caisse(entreprise)
    since = timezone.now() - datetime.timedelta(days=jours)
    presta = {}
    for e in EvenementPrestataire.objects.filter(entreprise=entreprise, cree_le__gte=since).only("fournisseur_code", "type", "montant"):
        p = presta.setdefault(e.fournisseur_code, {"fournisseur": e.fournisseur_code, "nb_inities": 0, "nb_credites": 0, "montant_credite": 0.0, "nb_echecs": 0, "nb_suspects": 0})
        if e.type == "credite":
            p["nb_credites"] += 1
            p["montant_credite"] += _nombre(e.montant)
        else:
            p[{"initie": "nb_inities", "echec": "nb_echecs", "suspect": "nb_suspects"}[e.type]] += 1
    return {
        "jours": jours, "devise": "XOF",
        "entrees_externes": {"depots": depots, "nb_depots": nb["depots"]},
        "sorties_externes": {"retraits": retraits, "nb_retraits": nb["retraits"], "charges": charges, "nb_charges": nb["charges"], "charges_par_categorie": par_categorie},
        "net_externe": depots - retraits - charges,
        "caisse": {"solde": _wallet.obtenir_wallet(caisse).solde if caisse else 0.0, "entrees": entrees_caisse, "sorties": sorties_caisse},
        "prestataires": sorted(presta.values(), key=lambda p: p["fournisseur"]),
        "chaine_valide": Bloc.chaine_valide(entreprise.id),
    }
