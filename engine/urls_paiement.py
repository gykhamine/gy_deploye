"""URLs des services payants internes (prix des services, pages fermées aux clients, abonnement, paiement depuis la caisse)."""
from django.urls import path
from .views_paiement import services_payants, pages_client
from .views_wallet import souscrire_abonnement, payer_depuis_caisse

urlpatterns = [
    path('api/paiement/services', services_payants, name='paiement_services'),
    path('api/paiement/pages-client', pages_client, name='paiement_pages_client'),
    path('api/wallet/abonnement', souscrire_abonnement, name='wallet_abonnement'),
    path('api/wallet/payer-caisse', payer_depuis_caisse, name='wallet_payer_caisse'),
]
