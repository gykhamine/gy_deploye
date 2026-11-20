"""Wallet FCFA — le WALLET PRINCIPAL de l'entreprise est l'ensemble des wallets, répartis par agent (chaque client appartient à l'agent
responsable de sa fiche, chaque membre de l'équipe a le sien) : l'administrateur l'analyse en un seul bloc (wallet_principal,
synchroniser_wallet_vers_table) alors que l'argent reste réparti par agent. Voir repartition_par_agent.

Wallet FCFA — CAISSE de l'entreprise = wallet de son administrateur (le super admin Django) : tout ce que paie un
client (service, abonnement, taxe de dépôt/retrait/transfert) y est crédité dans la MÊME transaction que le débit, donc
aucun FCFA ne disparaît ; l'administrateur paie ensuite agents et clients depuis cette caisse (payer_depuis_caisse).
Wallet FCFA — recharge via dépôt USSD vérifié, transfert interne entre
membres, retrait renvoyé par USSD (sans attente de confirmation : on
affiche le retour brut de l'opérateur). Conversion multi-devises et frais
(dépôt/retrait/transfert/intérêt) configurables par l'agent. Toute écriture
passe par models.Bloc (la blockchain principale) — pas de second système
d'historique dupliqué.

§sécurité (audit 2026-09-26) : chaque fonction qui lit PUIS écrit
wallet.solde le fait maintenant sous transaction.atomic() + un verrou de
ligne (select_for_update(), voir obtenir_wallet(verrou=True)) — sans ce
verrou, deux requêtes concurrentes sur le même wallet (double-clic, retry
réseau, deux appareils connectés en même temps) peuvent lire le même solde
de départ avant qu'aucune n'ait sauvegardé : un retrait/transfert peut
alors dépasser le solde réel (découvert), ou un crédit concurrent peut être
silencieusement perdu (lost update). Limite déjà documentée ailleurs dans
ce module (Bloc._committer_bloc) : PostgreSQL sérialise réellement les
processus concurrents via ce verrou, SQLite ne le fait pas (pas de verrou
inter-process), donc reste vulnérable en déploiement multi-worker sur
SQLite — limitation connue de SQLite, pas de ce correctif."""
from engine.i18n import t, tf, tl
from engine.montant import Montant
from . import comptabilite as _compta
from django.db import transaction as _db_transaction
from .models import Wallet, TauxChange, FraisWallet, Bloc, MembreEntreprise
from .frais_taxes import EVENEMENTS_FRAIS, calculer_frais, prix_service, service_payant, tva_sur, valider_services  # noqa: F401  (calcul pur, testé sans base)
from engine.i18n import t as _gt  # noqa: E402


MSG_PRESTATAIRES = tl("rg8.msg.003")


def prestataires_actifs() -> bool:
    """Faux par défaut : tout ce qui dépend d'un prestataire externe échappe au contrôle de l'entreprise. Ne se réactive que par
    settings.WALLET_PRESTATAIRES_ACTIFS = True (reprise de l'ancien parcours, tests)."""
    from django.conf import settings
    return bool(getattr(settings, "WALLET_PRESTATAIRES_ACTIFS", False))


def _exiger_prestataires():
    if not prestataires_actifs():
        raise ValueError(MSG_PRESTATAIRES)


def obtenir_frais(entreprise) -> FraisWallet:
    frais, _ = FraisWallet.objects.get_or_create(entreprise=entreprise)
    return frais


def obtenir_wallet(membre, verrou: bool = False) -> Wallet:
    """`verrou=True` : verrouille la ligne (SELECT ... FOR UPDATE) pour toute
    fonction qui va ensuite lire ET écrire le solde — DOIT être appelé à
    l'intérieur d'un `transaction.atomic()` englobant tout le
    lire-vérifier-écrire (voir docstring du module)."""
    if verrou:
        wallet, _ = Wallet.objects.select_for_update().get_or_create(membre=membre)
        return wallet
    wallet, _ = Wallet.objects.get_or_create(membre=membre)
    return wallet


def convertir_vers_fcfa(montant: float, devise: str, entreprise) -> float:
    """FCFA (XOF) est la devise pivot par défaut ; sinon applique le taux
    configuré par l'agent (TauxChange)."""
    devise = (devise or "XOF").upper()
    if devise in ("XOF", "FCFA"):
        return Montant(montant)
    taux = TauxChange.objects.filter(entreprise=entreprise, devise=devise).first()
    if not taux:
        raise ValueError(tf("engine/wallet.py:g:57:26", v1=devise))
    return Montant(montant) * taux.taux_vers_fcfa


def _verifier_montant(montant):
    """Montant strictement positif ET fini : float('nan') passe `montant <= 0` et rendait le solde NaN."""
    import math
    if not montant or not math.isfinite(montant) or montant <= 0:
        raise ValueError(t("engine/wallet.py:g:65:26"))


def membre_caisse(entreprise):
    """Titulaire de la caisse : membre technique (rôle « caisse », sans connexion possible) propre à l'entreprise, créé au premier besoin ;
    son wallet est celui de l'entreprise, distinct des wallets personnels des administrateurs."""
    m = MembreEntreprise.objects.filter(entreprise=entreprise, role="caisse").order_by("id").first()
    if m is None and MembreEntreprise.objects.filter(entreprise=entreprise, role="admin").exists():
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user(username=f"caisse-{entreprise.id}", first_name="Caisse", last_name="entreprise", password=None)
        m = MembreEntreprise.objects.create(utilisateur=u, entreprise=entreprise, role="caisse")
    return m


def _caisse_ou_erreur(entreprise):
    caisse = membre_caisse(entreprise)
    if caisse is None:
        raise ValueError(t("engine/wallet.py:g:76:26"))
    return caisse


def _verrouiller(*membres):
    """Verrouille (SELECT ... FOR UPDATE) les wallets de ces membres TOUJOURS dans l'ordre des pk : deux flux croisés (client -> caisse
    et caisse -> client) ne peuvent donc jamais s'interbloquer. À appeler dans un transaction.atomic(). Retourne {membre.pk: wallet} ;
    deux membres identiques partagent le même objet (la caisse qui se paie une taxe à elle-même ne compte qu'une fois)."""
    from . import securite_dates
    securite_dates.controler(membres[0].entreprise_id)   # argent : refus AVANT de toucher un solde si l'horloge a reculé
    ids = {m.pk: obtenir_wallet(m).pk for m in membres}
    verrouilles = {pk: Wallet.objects.select_for_update().get(pk=pk) for pk in sorted(set(ids.values()))}
    return {mpk: verrouilles[wpk] for mpk, wpk in ids.items()}


def _enregistrer(*wallets):
    for w in {id(w): w for w in wallets}.values():
        w.solde = round(w.solde, 2)   # point d'écriture unique : évite la dérive des flottants (0.1+0.2…) cumulée sur les soldes
        w.save(update_fields=["solde", "maj_le"])


def services_payants(entreprise) -> dict:
    """Prix des services payants, enregistrés en base (ils ne vivaient jusqu'ici que dans le cache, perdu au redémarrage)."""
    from django.core.cache import cache
    return obtenir_frais(entreprise).services or cache.get(f'services_payants_{entreprise.id}', {}) or {}


def enregistrer_services(entreprise, donnees) -> dict:
    """Valide (ValueError si invalide) puis enregistre la configuration des prix."""
    services = valider_services(donnees)
    frais = obtenir_frais(entreprise)
    frais.services = services
    frais.save(update_fields=["services", "maj_le"])
    return services


def crediter_depot(entreprise, membre, montant: float, devise: str = "XOF", reference: str = None, extra: dict = None):
    """Recharge du wallet suite à un dépôt USSD vérifié ; la taxe de dépôt va à la caisse. ABANDONNÉ par défaut (voir prestataires_actifs)."""
    _exiger_prestataires()
    _verifier_montant(montant)
    montant_fcfa = convertir_vers_fcfa(montant, devise, entreprise)
    cfg = obtenir_frais(entreprise)
    frais_montant = calculer_frais(cfg, "depot", montant_fcfa)
    tva = min(tva_sur(cfg, frais_montant), montant_fcfa - frais_montant)   # TVA sur la taxe, jamais plus que le dépôt
    frais_total = round(frais_montant + tva, 2)
    caisse = _caisse_ou_erreur(entreprise)
    with _db_transaction.atomic():
        w = _verrouiller(membre, caisse)
        wallet = w[membre.pk]
        wallet.solde += montant_fcfa - frais_total
        w[caisse.pk].solde += frais_total
        _enregistrer(wallet, w[caisse.pk])
        _compta.noter(entreprise, "depot_prestataire", "entree", montant_fcfa, frais=frais_total, client=membre, dest=membre, motif=str(reference or "")[:200])
    Bloc.ajouter_transaction(entreprise.id, {
        "type": "wallet_depot", "agent_id": membre.utilisateur_id,
        "enregistrement_id": membre.enregistrement_client_id,
        "donnees": {"montant": montant_fcfa, "devise_origine": devise,
                    "frais_montant": frais_montant, "tva": tva, "caisse_membre_id": caisse.id, "reference": reference, **(extra or {})},
    })
    return wallet, frais_total






_TYPE_ECRITURE = {"wallet_salaire": "salaire", "wallet_paiement_caisse": "paiement_caisse"}


def transferer(entreprise, membre_source, membre_dest, montant: float, avec_frais: bool = True, motif: str = "", type_bloc: str = "wallet_paiement_caisse", extra: dict = None,
               cle: str = None, operateur=None):
    """Transfert interne wallet à wallet — instantané, aucun USSD. La taxe de transfert va à la caisse. `avec_frais=False` + `motif` :
    paiement fait depuis la caisse (payer_depuis_caisse), sans taxe, tracé par un bloc « wallet_paiement_caisse »."""
    # §sécurité (audit 2026-09-26) : un montant négatif ou nul inverserait le sens du flux (vol de fonds) -> toujours > 0 et fini.
    _verifier_montant(montant)
    montant = Montant(montant)
    caisse = _caisse_ou_erreur(entreprise)
    with _db_transaction.atomic():
        w = _verrouiller(membre_source, membre_dest, caisse)
        wallet_src, wallet_dst = w[membre_source.pk], w[membre_dest.pk]
        cfg = obtenir_frais(entreprise)
        frais_montant = calculer_frais(cfg, "transfert", montant) if avec_frais else 0.0
        tva = tva_sur(cfg, frais_montant)
        frais_total = round(frais_montant + tva, 2)
        if wallet_src.solde < montant + frais_total:
            raise ValueError(t("engine/wallet.py:g:165:30"))
        wallet_src.solde -= (montant + frais_total)
        wallet_dst.solde += montant
        w[caisse.pk].solde += frais_total
        _enregistrer(wallet_src, wallet_dst, w[caisse.pk])
        # Écriture comptable DANS la transaction : pas de transfert sans ligne au journal.
        _compta.noter(entreprise, "transfert" if avec_frais else _TYPE_ECRITURE.get(type_bloc, "paiement_caisse"), "interne", montant, frais=frais_total,
                      source=membre_source, dest=membre_dest, operateur=operateur, motif=motif, cle=cle)
    if not avec_frais:
        Bloc.ajouter_transaction(entreprise.id, {
            "type": type_bloc, "agent_id": membre_source.utilisateur_id,
            "donnees": {"montant": montant, "beneficiaire_membre_id": membre_dest.id, "motif": motif, **(extra or {})},
        })
        return wallet_src, wallet_dst
    Bloc.ajouter_transaction(entreprise.id, {
        "type": "wallet_transfert_envoye", "agent_id": membre_source.utilisateur_id,
        "donnees": {"montant": montant, "frais_montant": frais_montant, "tva": tva, "caisse_membre_id": caisse.id, "destinataire_membre_id": membre_dest.id},
    })
    Bloc.ajouter_transaction(entreprise.id, {
        "type": "wallet_transfert_recu", "agent_id": membre_dest.utilisateur_id,
        "donnees": {"montant": montant, "expediteur_membre_id": membre_source.id},
    })
    return wallet_src, wallet_dst


def payer_depuis_caisse(entreprise, membre_dest, montant: float, motif: str = ""):
    """L'entreprise paie un agent ou un client depuis sa caisse (sans taxe). Solde de la caisse vérifié sous verrou."""
    caisse = _caisse_ou_erreur(entreprise)
    if membre_dest.entreprise_id != entreprise.id or membre_dest.pk == caisse.pk:
        raise ValueError(t("engine/wallet.py:g:191:26"))
    return transferer(entreprise, caisse, membre_dest, montant, avec_frais=False, motif=motif)




def _encaisser_service(entreprise, membre, categorie: str, type_bloc: str):
    """Débite le client du prix du service et crédite la caisse dans la MÊME transaction. Retourne (ok, message, solde_apres, prix).
    Prix = montant fixe ou % du solde du wallet (config de l'agent) ; un service payant ne devient jamais gratuit parce que le wallet est vide."""
    caisse = _caisse_ou_erreur(entreprise)
    config = services_payants(entreprise).get(categorie)
    from .remuneration import commissionnaire
    agent, taux = commissionnaire(membre)   # agent payé au pourcentage des paiements de ses clients, sinon (None, 0)
    with _db_transaction.atomic():
        w = _verrouiller(*[m for m in (membre, caisse, agent) if m is not None])
        wallet = w[membre.pk]
        prix = prix_service(config, wallet.solde)
        tva = tva_sur(obtenir_frais(entreprise), prix)   # prix du service = HT : le client paie HT + TVA
        total = round(prix + tva, 2)
        if prix <= 0 or wallet.solde < total:
            return False, tf("engine/wallet.py:g:235:27", v1=wallet.solde, v2=total), wallet.solde, total
        wallet.solde -= total
        w[caisse.pk].solde += total
        commission = round(prix * taux / 100, 2) if agent is not None else 0.0
        if commission:   # la part de l'agent sort de la caisse dans la MÊME transaction : aucun FCFA ne disparaît
            w[caisse.pk].solde -= commission
            w[agent.pk].solde += commission
        _enregistrer(wallet, w[caisse.pk], *([w[agent.pk]] if commission else []))
        _compta.noter(entreprise, "service", "interne", total, source=membre, dest=caisse, client=membre, categorie=categorie, motif=type_bloc)
        if commission:
            _compta.noter(entreprise, "commission", "interne", commission, source=caisse, dest=agent, client=membre, categorie=categorie, motif=f"{taux} %")
        solde_apres = wallet.solde
    Bloc.ajouter_transaction(entreprise.id, {
        "type": type_bloc, "agent_id": membre.utilisateur_id, "enregistrement_id": membre.enregistrement_client_id,
        "donnees": {"categorie": categorie, "montant": prix, "tva": tva, "caisse_membre_id": caisse.id, "solde_apres": solde_apres},
    })
    if commission:
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "wallet_commission", "agent_id": agent.utilisateur_id,
            "donnees": {"montant": commission, "taux": taux, "categorie": categorie, "client_membre_id": membre.id, "caisse_membre_id": caisse.id},
        })
    return True, "", solde_apres, prix


def _service_du_par(entreprise, membre, categorie: str) -> bool:
    """Seuls les clients (role « lecteur ») paient ; l'administrateur (la caisse) et les agents n'ont rien à payer. Service gratuit si non payant."""
    return membre.role == "lecteur" and service_payant(services_payants(entreprise).get(categorie))


def verifier_et_debiter_service(entreprise, membre, categorie: str):
    """Vérifie si `categorie` est payante pour cette entreprise et, si oui, débite le wallet du client (crédite la caisse).
    Retourne (autorisé: bool, message: str). Catégories : voir frais_taxes.SERVICES_CATEGORIES."""
    if not _service_du_par(entreprise, membre, categorie):
        return True, ""
    ok, message, _solde, _prix = _encaisser_service(entreprise, membre, categorie, "wallet_debit_service")
    return ok, message


def souscrire_abonnement(entreprise, membre, categorie: str):
    """Débite le wallet du prix configuré (fixe ou % du wallet), le crédite à la caisse et prolonge (ou crée) l'AbonnementMensuel
    de 30 jours glissants depuis maintenant. Catégories : AbonnementMensuel.CATEGORIES."""
    from django.utils import timezone
    import datetime
    if _service_du_par(entreprise, membre, categorie):
        ok, message, _solde, _prix = _encaisser_service(entreprise, membre, categorie, "wallet_abonnement")
        if not ok:
            return False, message
    from .models import AbonnementMensuel
    expire = timezone.now() + datetime.timedelta(days=30)
    AbonnementMensuel.objects.update_or_create(
        membre=membre, categorie=categorie,
        defaults={"expire_le": expire},
    )
    return True, ""


def abonnement_valide(membre, categorie: str) -> bool:
    """Vérifie si le membre a un abonnement actif pour cette catégorie."""
    from .models import AbonnementMensuel
    ab = AbonnementMensuel.objects.filter(membre=membre, categorie=categorie).first()
    return ab is not None and ab.est_valide()


PAGES_CLIENT = ("dashboard_bi", "ontologie", "geolocalisation", "series_temporelles", "statistiques", "anova", "analyse_factorielle")


def pages_client_fermees(entreprise) -> set:
    """Pages d'analyse que l'agent a fermées aux clients."""
    return {c for c in (obtenir_frais(entreprise).pages_client_fermees or []) if c in PAGES_CLIENT}


def page_ouverte_client(entreprise, categorie: str) -> bool:
    return categorie not in pages_client_fermees(entreprise)


def enregistrer_pages_client(entreprise, fermees) -> list:
    """Enregistre les pages fermées aux clients (ValueError si la forme ou une catégorie est invalide)."""
    if not isinstance(fermees, list) or any(c not in PAGES_CLIENT for c in fermees):
        raise ValueError(_gt("rg8.msg.033"))
    frais = obtenir_frais(entreprise)
    frais.pages_client_fermees = sorted(set(fermees))
    frais.save(update_fields=["pages_client_fermees", "maj_le"])
    return frais.pages_client_fermees


def acces_page_client(entreprise, membre, categorie: str) -> bool:
    """Page d'analyse ouverte à un client en lecture seule : libre si l'agent n'en a pas fixé le prix, sinon abonnement mensuel valide requis."""
    return not _service_du_par(entreprise, membre, categorie) or abonnement_valide(membre, categorie)


def repartition_par_agent(entreprise):
    """Répartition du wallet principal par agent. Chaque wallet de l'entreprise est compté UNE fois : un membre de l'équipe (administrateur
    ou agent) pour son propre wallet, chaque client pour l'agent responsable de sa fiche (messagerie.agent_de ; sans agent identifiable :
    l'administrateur). Retourne {"total": float, "agents": [{"membre_id","utilisateur_id","nom","username","role","solde_propre",
    "nb_clients","solde_clients","solde_total"}...]} trié par solde total décroissant ; total = somme de tous les wallets."""
    from . import messagerie as _mg
    membres = list(MembreEntreprise.objects.filter(entreprise=entreprise).select_related("utilisateur", "enregistrement_client"))
    soldes = {w.membre_id: w.solde for w in Wallet.objects.filter(membre__entreprise=entreprise)}
    equipe = {m.id: m for m in membres if m.role in ("admin", "agent", "caisse")}
    lignes = {m.id: {
        "membre_id": m.id, "utilisateur_id": m.utilisateur_id,
        "nom": f"{m.utilisateur.first_name} {m.utilisateur.last_name}".strip() or m.utilisateur.username,
        "username": m.utilisateur.username, "role": m.role,
        "solde_propre": soldes.get(m.id, 0.0), "nb_clients": 0, "solde_clients": 0.0,
    } for m in equipe.values()}
    chef = _mg.chef(entreprise)
    par_utilisateur = {m.utilisateur_id: m.id for m in equipe.values()}
    for m in membres:
        if m.role != "lecteur":
            continue
        enr = m.enregistrement_client
        cible = par_utilisateur.get(enr.cree_par_id) if enr is not None and enr.cree_par_id else None
        if cible is None and chef is not None:
            cible = chef.id
        if cible not in lignes:
            continue
        lignes[cible]["nb_clients"] += 1
        lignes[cible]["solde_clients"] += soldes.get(m.id, 0.0)
    agents = []
    for l in lignes.values():
        l["solde_total"] = l["solde_propre"] + l["solde_clients"]
        agents.append(l)
    agents.sort(key=lambda l: l["solde_total"], reverse=True)
    return {"total": sum(l["solde_total"] for l in agents), "agents": agents}


def agent_responsable_wallet(membre, noms_equipe=None):
    """Nom de l'agent auquel appartient le wallet de `membre` dans le wallet principal (lui-même pour l'équipe)."""
    from . import messagerie as _mg
    a = membre if membre.role in ("admin", "agent") else _mg.agent_de(membre)
    if a is None:
        return ""
    return f"{a.utilisateur.first_name} {a.utilisateur.last_name}".strip() or a.utilisateur.username


def synchroniser_wallet_vers_table(entreprise, progression_cb=None):
    """Rend le WALLET PRINCIPAL (ensemble des wallets, réparti par agent via la colonne « agent ») « analysable » comme n'importe quelle autre
    (calculs, KPI, dashboards, ontologie, IA, résumé PDF) : construit un
    instantané (DataFrame) de tous les wallets de l'entreprise et le fait
    entrer dans le moteur d'import générique déjà utilisé par
    import_fichier.py/connexion_externe.py (_importer_dataframe_vers_tables),
    au lieu d'un second moteur d'analyse dédié au wallet. provient_fichier=True
    car ces lignes sont un instantané en lecture seule (comme un import de
    fichier), jamais éditées à la main — à re-synchroniser (nouvelle table)
    pour un instantané à jour."""
    import pandas as pd
    from .import_fichier import _importer_dataframe_vers_tables

    wallets = Wallet.objects.filter(membre__entreprise=entreprise).select_related("membre", "membre__utilisateur", "membre__enregistrement_client")
    lignes = [{
        "agent": agent_responsable_wallet(w.membre),
        "numero_client": w.membre.numero_client,
        "telephone": f"{w.membre.indicatif_pays}{w.membre.telephone}" if w.membre.telephone else "",
        "role": w.membre.get_role_display(),
        "solde_fcfa": float(w.solde),   # table d'analyse (pandas/BI) : colonne numérique, pas des objets Decimal
        "maj_le": w.maj_le,
    } for w in wallets]
    if not lignes:
        raise ValueError(t("engine/wallet.py:g:368:26"))
    from .moteur_donnees import dataframe_depuis
    dataframe = dataframe_depuis(lignes)
    return _importer_dataframe_vers_tables(
        entreprise=entreprise, nom_base="Wallet principal", dataframe=dataframe,
        nb_lignes=len(dataframe), colonnes=list(dataframe.columns),
        description_base=t("engine/wallet.py:g:374:26"),
        provient_fichier=True, nom_fichier_origine="Wallet principal (synchronisation)",
        connexion_externe_origine=None, nom_source_distante="",
        progression_cb=progression_cb, type_stockage="sql",
    )


def lister_wallets_clients(entreprise, agent=None):
    """Liste les wallets de tous les clients (role='lecteur') de
    `entreprise`, avec leur solde — c'est ce qui manquait : jusqu'ici seul
    `mon_wallet` existait, limité au wallet du membre connecté, aucune vue
    d'ensemble des clients n'était possible pour un agent/admin.

    Si `agent` est fourni (utilisateur non-admin), restreint aux clients
    dont la ligne d'enregistrement a été créée par CET agent — même
    isolation que `_isoler_lignes_agent`/`_ligne_accessible` (views.py) :
    chaque agent ne voit que "ses" clients, l'admin (agent=None) voit tous
    les clients de l'entreprise.

    Retourne une liste de tuples (membre, wallet) triée par solde
    décroissant."""
    from .models import MembreEntreprise
    qs = MembreEntreprise.objects.filter(entreprise=entreprise, role="lecteur").select_related(
        "utilisateur", "enregistrement_client", "table_cliente"
    )
    if agent is not None:
        qs = qs.filter(enregistrement_client__cree_par=agent)
    resultats = [(membre, obtenir_wallet(membre)) for membre in qs]
    resultats.sort(key=lambda paire: paire[1].solde, reverse=True)
    return resultats
