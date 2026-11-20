"""URLs pour blockchain et paiement des données. À ajouter à urls.py principal."""
from django.urls import path
from .views_blockchain import (
    
    historique_blockchain_table, historique_action_cible, export_blockchain
)

urlpatterns = [
    path('api/blockchain/historique', historique_blockchain_table, name='blockchain_historique'),
    path('api/blockchain/historique-cible', historique_action_cible, name='blockchain_historique_cible'),
    path('api/blockchain/export', export_blockchain, name='blockchain_export'),
]
