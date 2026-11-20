"""Dépôts d'un fournisseur EXTERNE (API mobile money, carte) mis en attente de décision : les faux positifs possibles.

Un fournisseur qui dit « payé » ne suffit pas à créditer un wallet quand sa réponse est incohérente. Rien n'est crédité ni
refusé : le dépôt reste « en attente » et figure dans la liste « Dépôts à vérifier » du super administrateur (Paiement >
Surveillance), qui l'accepte (crédit du montant DEMANDÉ, idempotent) ou le rejette (échec). Cas retenus :
  - montant_invalide        : montant annoncé illisible, nul, négatif ou non fini (avant, 0 se repliait sur le montant demandé) ;
  - montant_superieur       : montant annoncé supérieur au montant demandé (souvent un réglage d'unité : centimes au lieu de
                              francs — le wallet aurait été crédité 100 fois trop) ;
  - confirme_apres_echec    : confirmation reçue pour un dépôt que le fournisseur (ou l'initiation) avait déjà marqué en échec.
Un montant annoncé INFÉRIEUR au montant demandé n'est pas une anomalie : on crédite ce qui a été réellement reçu.

Mémoire : cache partagé de Django (comme les dépôts USSD, voir ussd_payment.py), une entrée par entreprise. Module sans modèle ni
migration."""
import math
from datetime import datetime

from django.core.cache import cache

from engine.montant import saisie

MARGE_RELATIVE = 0.01      # tolérance sur « supérieur » : arrondis du fournisseur
MARGE_ABSOLUE = 0.5
RAISONS = ("montant_invalide", "montant_superieur", "confirme_apres_echec")


def anomalie(montant_demande, statut_depot: str, montant_recu) -> str:
    """Raison pour laquelle une confirmation ne doit pas être créditée telle quelle, ou « » si elle est cohérente."""
    if statut_depot == "credite":
        return ""                                    # déjà crédité : un rejeu se contente de répondre « crédité »
    if montant_recu is not None:
        try:
            v = saisie(montant_recu)
        except (TypeError, ValueError):
            return "montant_invalide"
        if not math.isfinite(v) or v <= 0:
            return "montant_invalide"
        if v > saisie(montant_demande) * (1 + MARGE_RELATIVE) + MARGE_ABSOLUE:
            return "montant_superieur"
    if statut_depot == "echec":
        return "confirme_apres_echec"
    return ""


def _cle(entreprise_id) -> str:
    return f"depots_suspects_{entreprise_id}"


def signaler(depot, raison: str, montant_recu=None) -> None:
    """Met `depot` (DepotPaiement) en attente de décision ; rappeler pour le même dépôt met simplement l'entrée à jour."""
    eid = depot.membre.entreprise_id
    table = dict(cache.get(_cle(eid), {}))
    recu = None
    try:
        if montant_recu is not None and math.isfinite(saisie(montant_recu)):
            recu = saisie(montant_recu)
    except (TypeError, ValueError):
        pass
    table[str(depot.pk)] = {
        "depot_id": depot.pk, "reference": depot.reference, "fournisseur": depot.fournisseur.code,
        "membre_id": depot.membre_id, "montant_demande": depot.montant, "montant_recu": recu,
        "raison": raison, "statut_depot": depot.statut, "signale_le": datetime.now().isoformat(),
    }
    cache.set(_cle(eid), table, timeout=None)


def lister(entreprise_id) -> list:
    return sorted(cache.get(_cle(entreprise_id), {}).values(), key=lambda d: d["signale_le"])


def est_signale(entreprise_id, depot_id) -> bool:
    return str(depot_id) in cache.get(_cle(entreprise_id), {})


def retirer(entreprise_id, depot_id) -> None:
    table = dict(cache.get(_cle(entreprise_id), {}))
    if table.pop(str(depot_id), None) is not None:
        cache.set(_cle(entreprise_id), table, timeout=None)
