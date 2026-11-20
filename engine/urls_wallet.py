"""URLs pour le système de wallet FCFA. À ajouter à urls.py principal."""
from django.urls import path
from .views_wallet import (
    mon_wallet, transferer_wallet,
    configurer_taux_change, configurer_frais_wallet, wallets_clients,
    synchroniser_wallet_table, wallet_principal, remuneration_agents, journal_wallet,
)
from .views_wallet_flux import charges_wallet, bilan_wallet
from . import views_wallet_especes as especes

urlpatterns = [
    path('api/wallet/', mon_wallet, name='wallet_solde'),
    path('api/wallet/transferer', transferer_wallet, name='wallet_transferer'),
    path('api/wallet/taux-change', configurer_taux_change, name='wallet_taux_change'),
    path('api/wallet/frais', configurer_frais_wallet, name='wallet_frais'),
    path('api/wallet/principal', wallet_principal, name='wallet_principal'),
    path('api/wallet/remuneration', remuneration_agents, name='wallet_remuneration'),
    path('api/wallet/journal', journal_wallet, name='wallet_journal'),
    path('api/wallet/clients', wallets_clients, name='wallet_clients'),
    path('api/wallet/synchroniser-table', synchroniser_wallet_table, name='wallet_synchroniser_table'),
    path('api/wallet/charges', charges_wallet, name='wallet_charges'),
    path('api/wallet/bilan', bilan_wallet, name='wallet_bilan'),
    path('api/wallet/agent/membres', especes.agent_membres, name='wallet_agent_membres'),
    path('api/wallet/agent/depot', especes.agent_depot, name='wallet_agent_depot'),
    path('api/wallet/agent/retrait', especes.agent_retrait, name='wallet_agent_retrait'),
    path('api/wallet/agent/operations', especes.agent_operations, name='wallet_agent_operations'),
    path('api/wallet/destinataires', especes.destinataires, name='wallet_destinataires'),
    path('api/wallet/politique', especes.politique_transfert, name='wallet_politique'),
    path('api/wallet/habilitations', especes.habilitations, name='wallet_habilitations'),
    path('api/wallet/especes', especes.especes_mouvement, name='wallet_especes'),
    path('api/wallet/comptabilite', especes.comptabilite, name='wallet_comptabilite'),
]
