"""Paie et parts du wallet de l'entreprise — la numérisation d'une vraie entreprise.

Le wallet de l'entreprise est UN ensemble, réparti en parts :
  - part des CLIENTS : ce qu'ils ont rechargé pour pouvoir agir (débité quand ils font une action payante) ;
  - part des AGENTS  : leur salaire mensuel, OU leur pourcentage des paiements de leurs clients, réglés par l'administrateur ;
  - part de l'ENTREPRISE : tout le reste = la caisse (wallet de l'administrateur), qui encaisse les paiements et les taxes.
Tout mouvement passe par models.Bloc (la blockchain) : « wallet_salaire », « wallet_commission », « wallet_remuneration_config » s'ajoutent à
« wallet_depot », « wallet_debit_service », « wallet_abonnement », « wallet_paiement_caisse »… (voir wallet.py).

Salaire : versé de la caisse à l'agent à partir du jour de paie de chaque mois, UNE fois par mois (marqueur « AAAA-MM » sous verrou). Si la caisse
est vide ce jour-là, il n'y a pas de découvert : le salaire reste dû et est versé au premier passage suivant. Pas de rétroactif avant la mise en place.
Déclenchement : `python manage.py verser_salaires --boucle 3600` (en service) ET à chaque consultation du wallet (le travail est idempotent).
Pourcentage : prélevé sur la caisse à chaque service/abonnement payé par un client de l'agent (wallet._encaisser_service), dans la même transaction.
"""
from engine.montant import saisie
from engine.i18n import td
from engine.i18n import t, tf
import datetime
import math

from django.db import transaction as _tx
from django.db.models import Sum
from django.utils import timezone

from .models import Bloc, MembreEntreprise, RemunerationAgent, Wallet

MODES = ("aucun", "salaire", "pourcentage")
JOUR_MIN, JOUR_MAX = 1, 28
# Effet de chaque type de bloc sur la CAISSE (part de l'entreprise) : « frais » = la taxe seule, « montant » = tout, signe = entrée (+) ou sortie (-).
EFFET_CAISSE = {"wallet_depot": ("frais_montant", 1), "wallet_depot_especes": ("frais_montant", 1), "wallet_retrait_especes": ("frais_montant", 1), "wallet_transfert_envoye": ("frais_montant", 1), "wallet_retrait": ("frais_montant", 1),
                "wallet_debit_service": ("montant", 1), "wallet_abonnement": ("montant", 1),
                "wallet_salaire": ("montant", -1), "wallet_commission": ("montant", -1), "wallet_paiement_caisse": ("montant", -1),
                "wallet_charge": ("montant", -1), "wallet_entree_caisse": ("montant", 1)}


def periode(d):
    return f"{d.year}-{d.month:02d}"


def echeance(rem, d):
    return datetime.date(d.year, d.month, min(max(rem.jour_paie, JOUR_MIN), JOUR_MAX))


def est_du(rem, d):
    """Salaire de la période de `d` échu et pas encore versé (sans verrou : simple présélection)."""
    if rem.mode != "salaire" or rem.salaire <= 0 or rem.derniere_periode == periode(d):
        return False
    ech = echeance(rem, d)
    return d >= ech and not (rem.actif_depuis and ech < rem.actif_depuis)


def _nombre(v, mini, maxi, nom):
    try:
        v = saisie(v)
    except (TypeError, ValueError):
        raise ValueError(f"{nom} invalide.")
    if not math.isfinite(v) or not mini <= v <= maxi:
        raise ValueError(tf("engine/remuneration.py:g:54:26", v1=nom, v2=mini, v3=maxi))
    return v


def configurer(entreprise, membre, mode, salaire=0, jour=JOUR_MAX, pourcentage=0):
    """Fixe la rémunération d'un AGENT (ValueError si refusée), et la trace dans la blockchain."""
    from . import wallet as _w
    if membre.entreprise_id != entreprise.id or membre.role not in ("agent", "admin"):
        raise ValueError(t("engine/remuneration.py:g:62:26"))
    if mode not in MODES:
        raise ValueError(t("engine/remuneration.py:g:64:26"))
    salaire = _nombre(salaire, 0, 1e12, "Salaire") if mode == "salaire" else 0.0
    jour = int(_nombre(jour, JOUR_MIN, JOUR_MAX, td("engine/remuneration.py:g:67:50"))) if mode == "salaire" else JOUR_MAX
    pourcentage = _nombre(pourcentage, 0, 100, "Pourcentage") if mode == "pourcentage" else 0.0
    with _tx.atomic():
        rem, _ = RemunerationAgent.objects.select_for_update().get_or_create(membre=membre)
        if mode == "salaire" and (rem.mode != "salaire" or rem.jour_paie != jour):
            rem.actif_depuis = timezone.localdate()   # le nouveau régime ne paie pas le passé
        rem.mode, rem.salaire, rem.jour_paie, rem.pourcentage = mode, salaire, jour, pourcentage
        rem.save()
    caisse = _w.membre_caisse(entreprise)
    Bloc.ajouter_transaction(entreprise.id, {"type": "wallet_remuneration_config", "agent_id": caisse.utilisateur_id if caisse else None,
                                             "donnees": {"membre_id": membre.id, "mode": mode, "salaire": salaire, "jour_paie": jour, "pourcentage": pourcentage}})
    return rem


def verser_salaire(rem, aujourdhui=None):
    """Verse le salaire dû de ce mois (une seule fois, sous verrou). True s'il vient d'être versé ; False si rien n'est dû ou caisse insuffisante."""
    from . import wallet as _w
    d = aujourdhui or timezone.localdate()
    with _tx.atomic():
        rem = RemunerationAgent.objects.select_for_update().select_related("membre__entreprise").get(pk=rem.pk)
        if not est_du(rem, d):
            return False
        entreprise, p = rem.membre.entreprise, periode(d)
        try:
            _w.transferer(entreprise, _w._caisse_ou_erreur(entreprise), rem.membre, rem.salaire, avec_frais=False, motif=f"Salaire {p}",
                          type_bloc="wallet_salaire", extra={"periode": p})
        except ValueError:   # caisse insuffisante : rien n'est écrit, le salaire reste dû
            return False
        rem.derniere_periode = p
        rem.save(update_fields=["derniere_periode", "maj_le"])
    return True


def verser_dus(entreprise, aujourdhui=None):
    """Verse tous les salaires échus de l'entreprise ; rend le nombre de salaires versés."""
    d = aujourdhui or timezone.localdate()
    dus = [r for r in RemunerationAgent.objects.filter(membre__entreprise=entreprise, mode="salaire") if est_du(r, d)]
    return sum(1 for r in dus if verser_salaire(r, d))


def commissionnaire(client):
    """(agent, pourcentage) payé au pourcentage sur les paiements de ce client, sinon (None, 0)."""
    from . import messagerie as _mg
    agent = _mg.agent_de(client)
    if agent is None or agent.role not in ("agent", "admin"):
        return None, 0.0
    rem = RemunerationAgent.objects.filter(membre=agent, mode="pourcentage", pourcentage__gt=0).first()
    return (agent, rem.pourcentage) if rem else (None, 0.0)


def liste_agents(entreprise, aujourdhui=None):
    """Chaque agent avec sa rémunération, son solde et l'état de son salaire du mois (« verse », « du » = échu et impayé, « a_venir »)."""
    d = aujourdhui or timezone.localdate()
    soldes = {w.membre_id: w.solde for w in Wallet.objects.filter(membre__entreprise=entreprise, membre__role__in=("agent", "admin"))}
    sortie = []
    for m in MembreEntreprise.objects.filter(entreprise=entreprise, role__in=("agent", "admin")).select_related("utilisateur", "remuneration").order_by("id"):
        r = getattr(m, "remuneration", None)
        etat = "" if not r or r.mode != "salaire" else "verse" if r.derniere_periode == periode(d) else "du" if est_du(r, d) else "a_venir"
        sortie.append({"membre_id": m.id, "nom": f"{m.utilisateur.first_name} {m.utilisateur.last_name}".strip() or m.utilisateur.username,
                       "mode": r.mode if r else "aucun", "salaire": r.salaire if r else 0, "jour_paie": r.jour_paie if r else JOUR_MAX,
                       "pourcentage": r.pourcentage if r else 0, "role": m.role, "solde": soldes.get(m.id, 0.0), "etat_salaire": etat})
    return sortie


def parts(entreprise):
    """Le wallet de l'entreprise en parts : clients, agents, entreprise (caisse + autres administrateurs) ; total = leur somme."""
    somme = {l["membre__role"]: l["s"] or 0.0 for l in Wallet.objects.filter(membre__entreprise=entreprise).values("membre__role").annotate(s=Sum("solde"))}
    p = {"clients": somme.get("lecteur", 0.0), "agents": somme.get("agent", 0.0), "entreprise": somme.get("admin", 0.0) + somme.get("caisse", 0.0)}
    p["total"] = p["clients"] + p["agents"] + p["entreprise"]
    return p


def journal(entreprise, limite=30, blocs=500):
    """Flux de l'entreprise lus dans la blockchain : les `limite` derniers mouvements et, sur les `blocs` derniers blocs, les entrées et sorties de la caisse."""
    lignes, entrees, sorties = [], 0.0, 0.0
    for b in Bloc.objects.filter(entreprise=entreprise).order_by("-index")[:blocs]:
        for tx in reversed(b.transactions or []):
            type_ = tx.get("type", "")
            if not type_.startswith("wallet_"):
                continue
            d = tx.get("donnees") or {}
            cle, signe = EFFET_CAISSE.get(type_, (None, 0))
            effet = signe * saisie(d.get(cle) or 0) if cle else 0.0
            entrees, sorties = entrees + max(effet, 0), sorties + max(-effet, 0)
            if len(lignes) < limite:
                lignes.append({"bloc": b.index, "date": datetime.datetime.fromtimestamp(b.timestamp, datetime.timezone.utc).isoformat(), "type": type_,
                               "montant": saisie(d.get("montant") or 0), "effet_caisse": effet, "motif": d.get("motif") or d.get("categorie") or d.get("periode") or (f"{d.get('fournisseur')} · {d.get('evenement')}" if d.get("evenement") else "")})
    return {"mouvements": lignes, "entrees": entrees, "sorties": sorties, "chaine_valide": Bloc.chaine_valide(entreprise.id)}
