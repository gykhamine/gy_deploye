"""URLs du SIEM (page super-administrateur + ingestion signée) — fusionnées dans engine/urls.py."""
from django.urls import path

from . import views_siem as v

urlpatterns = [
    path("siem/", v.vue_siem, name="vue_siem"),
    path("api/siem/reauth", v.api_siem_reauth, name="api_siem_reauth"),
    path("api/siem/etat", v.api_siem_etat, name="api_siem_etat"),
    path("api/siem/alertes", v.api_siem_alertes, name="api_siem_alertes"),
    path("api/siem/evenements", v.api_siem_evenements, name="api_siem_evenements"),
    path("api/siem/risques", v.api_siem_risques, name="api_siem_risques"),
    path("api/siem/config", v.api_siem_config, name="api_siem_config"),
    path("api/siem/lockdown", v.api_siem_lockdown, name="api_siem_lockdown"),
    path("api/siem/verifier", v.api_siem_verifier, name="api_siem_verifier"),
    path("api/siem/sante", v.api_siem_sante, name="api_siem_sante"),
    path("api/siem/sante/parquet", v.api_siem_sante_parquet, name="api_siem_sante_parquet"),
    path("api/siem/cycle", v.api_siem_cycle, name="api_siem_cycle"),
    path("api/siem/rebaser", v.api_siem_rebaser, name="api_siem_rebaser"),
    path("api/siem/incident/clore", v.api_siem_incident_clore, name="api_siem_incident_clore"),
    path("api/siem/masquer", v.api_siem_masquer, name="api_siem_masquer"),
    path("api/siem/reafficher", v.api_siem_reafficher, name="api_siem_reafficher"),
    path("api/siem/rotation", v.api_siem_rotation, name="api_siem_rotation"),
    path("api/siem/fantome", v.api_siem_fantome, name="api_siem_fantome"),
    path("api/siem/deverrouiller", v.api_siem_deverrouiller, name="api_siem_deverrouiller"),
    path("api/siem/ingerer", v.api_siem_ingerer, name="api_siem_ingerer"),
]
