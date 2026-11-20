"""URLs de la sauvegarde / restauration de la plateforme (super-administrateur) — fusionnées dans engine/urls.py."""
from django.urls import path

from . import views_plateforme as v

urlpatterns = [
    path("plateforme/sauvegarde/", v.vue_sauvegarde, name="vue_sauvegarde"),
    path("api/plateforme/sauvegarde/creer", v.api_sauvegarde_creer, name="api_sauvegarde_creer"),
    path("api/plateforme/sauvegarde/restaurer", v.api_sauvegarde_restaurer, name="api_sauvegarde_restaurer"),
    path("plateforme/entreprises/", v.vue_entreprises, name="vue_entreprises"),
    path("api/plateforme/entreprises/", v.api_entreprises, name="api_entreprises"),
    path("api/plateforme/entreprises/<int:entreprise_id>/action", v.api_entreprise_action, name="api_entreprise_action"),
    path("api/plateforme/reglages/", v.api_reglage_plateforme, name="api_reglage_plateforme"),
    path("acces-suspendu/", v.vue_acces_suspendu, name="acces_suspendu"),
]
