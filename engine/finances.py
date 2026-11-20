"""Finances de l'entreprise — la caisse au jour le jour. Tout vient de l'existant ; ce module n'ajoute que ce qui manquait.

  * Entrée de caisse : de l'argent qui entre dans l'entreprise sans passer par le wallet d'un client (vente au comptoir, apport,
    subvention, remboursement…). Même schéma que les charges : le solde de la caisse, UNE écriture du journal (comptabilite.noter) et le
    bloc de la blockchain, dans l'ordre habituel. `cle` rend l'enregistrement idempotent.
  * Sortie de caisse : c'est la charge existante (wallet_flux.enregistrer_charge) — catégorie, libellé, bénéficiaire, jamais de découvert.
  * Relevé : le journal existant, filtré par période, sens et membre. Les analyses restent dans les pages d'analyse.
  * Portefeuilles : qui a quoi (solde), depuis quand (premier mouvement) et jusqu'à quand (dernier mouvement, fin de l'abonnement).

Droit d'accès : l'administrateur, ou un agent à qui il a délégué un ou plusieurs droits de la famille « finances » (Employés > Droits ; voir delegation.py) ; chaque fonction a son droit. L'argent ne
s'ouvre jamais par défaut.
"""
import datetime
import logging

from django.db import transaction as _tx
from django.db.models import Count, Max, Min, Sum

from engine.i18n import t as _t
from . import comptabilite as _c, wallet as _w
from .models import AbonnementMensuel, ChargeWallet, EcritureWallet, MembreEntreprise, Wallet

logger = logging.getLogger(__name__)

CATEGORIES_ENTREE = ("vente", "apport", "subvention", "remboursement", "autre")
CATEGORIES_SORTIE = tuple(c for c, _l in ChargeWallet.CATEGORIES)
# Même définition que comptabilite.bilan : ce qui entre dans l'entreprise et ce qui en sort.
TYPES_ENTREE = ("depot_especes", "depot_prestataire", "entree_caisse")
TYPES_SORTIE = ("retrait_especes", "retrait_prestataire", "charge")


def peut_gerer(membre) -> bool:
    from .delegation import a_un_pouvoir_de
    return a_un_pouvoir_de(membre, "finances")


def categories() -> dict:
    return {"entrees": [{"code": c, "libelle": _t(f"engine/finances.py:cat_entree:{c}")} for c in CATEGORIES_ENTREE],
            "sorties": [{"code": c, "libelle": _t(f"engine/finances.py:cat_sortie:{c}")} for c in CATEGORIES_SORTIE]}


def enregistrer_entree(entreprise, auteur, categorie, motif, montant, cle=None):
    """Retourne (écriture, deja). Le montant doit être strictement positif et fini ; la cause (motif) est obligatoire."""
    _w._verifier_montant(montant)
    if categorie not in CATEGORIES_ENTREE:
        raise ValueError(_t("engine/finances.py:categorie_inconnue"))
    motif = str(motif or "").strip()[:200]
    if not motif:
        raise ValueError(_t("engine/finances.py:motif_obligatoire"))
    deja = _c.ecriture_par_cle(entreprise, cle)
    if deja is not None:
        if deja.type != "entree_caisse":
            raise ValueError(_t("engine/finances.py:cle_prise"))
        return deja, True
    caisse = _w._caisse_ou_erreur(entreprise)
    with _tx.atomic():
        wallet = _w._verrouiller(caisse)[caisse.pk]
        wallet.solde += montant
        _w._enregistrer(wallet)
        e = _c.noter(entreprise, "entree_caisse", "entree", montant, dest=caisse, operateur=auteur, categorie=categorie, motif=motif, cle=cle)
    _c.ecrire_bloc(e, agent_user_id=getattr(auteur, "utilisateur_id", None))   # ne casse jamais l'opération ; `reparer_blocs` rattrape
    return e, False


def _date(valeur):
    try:
        return datetime.date.fromisoformat(str(valeur)[:10]) if valeur else None
    except ValueError:
        return None


def releve(entreprise, du=None, au=None, sens=None, membre=None, limite=300) -> dict:
    """Écritures de la période (bornes incluses), avec les totaux d'entrées et de sorties de l'entreprise sur la même période."""
    du, au = _date(du), _date(au)
    base = EcritureWallet.objects.filter(entreprise=entreprise)
    if du:
        base = base.filter(cree_le__date__gte=du)
    if au:
        base = base.filter(cree_le__date__lte=au)
    tot = {r["sens"]: r for r in base.filter(type__in=TYPES_ENTREE + TYPES_SORTIE).values("sens").annotate(s=Sum("montant"), n=Count("id"))}
    entrees, sorties = round((tot.get("entree") or {}).get("s") or 0.0, 2), round((tot.get("sortie") or {}).get("s") or 0.0, 2)
    qs = base.select_related("membre_source__utilisateur", "membre_dest__utilisateur", "client__utilisateur",
                             "agent_responsable__utilisateur", "operateur__utilisateur")
    if sens in ("entree", "sortie", "interne"):
        qs = qs.filter(sens=sens)
    if membre is not None:
        from django.db.models import Q
        qs = qs.filter(Q(membre_source=membre) | Q(membre_dest=membre) | Q(client=membre))
    limite = max(1, min(int(limite), 1000))
    return {"du": du.isoformat() if du else None, "au": au.isoformat() if au else None,
            "totaux": {"entrees": entrees, "sorties": sorties, "net": round(entrees - sorties, 2)},
            "ecritures": [_c.ecriture_en_dict(e) for e in qs[:limite]]}


def portefeuilles(entreprise) -> list:
    """Chaque wallet : titulaire, rôle, solde, premier et dernier mouvement du journal, abonnements en cours (jusqu'à quand)."""
    bornes = {}
    for champ in ("membre_source", "membre_dest", "client"):
        for r in (EcritureWallet.objects.filter(entreprise=entreprise, **{champ + "__isnull": False})
                  .values(champ).annotate(premier=Min("cree_le"), dernier=Max("cree_le"))):
            b = bornes.setdefault(r[champ], [r["premier"], r["dernier"]])
            b[0], b[1] = min(b[0], r["premier"]), max(b[1], r["dernier"])
    abos = {}
    for a in AbonnementMensuel.objects.filter(membre__entreprise=entreprise):
        abos.setdefault(a.membre_id, []).append({"categorie": a.categorie, "expire_le": a.expire_le.isoformat()})
    soldes = dict(Wallet.objects.filter(membre__entreprise=entreprise).values_list("membre_id", "solde"))
    sortie = []
    for m in MembreEntreprise.objects.filter(entreprise=entreprise).select_related("utilisateur"):
        b = bornes.get(m.id)
        if m.id not in soldes and b is None:
            continue
        sortie.append({"membre_id": m.id, "nom": _c._nom(m) or str(m.id), "role": m.role, "solde": round(soldes.get(m.id, 0.0), 2),
                       "depuis": b[0].isoformat() if b else None, "jusqua": b[1].isoformat() if b else None, "abonnements": abos.get(m.id, [])})
    return sorted(sortie, key=lambda x: -x["solde"])
