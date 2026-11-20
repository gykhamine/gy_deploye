"""MESSAGES CLAIRS des propositions, écrits pour un décideur non technicien : chaque nouveauté est nommée, dite en une phrase
(« pour voir… », « pour comparer… ») et située (où la retrouver). Une personne qui travaille sur la table reçoit un message qui
le dit ; les autres ne sont pas noyées. `composer` est PUR (dictionnaires) ; `membres_actifs` lit l'audit (qui a vraiment modifié
cette table récemment) et ne crée rien.
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t, tf, tl

JOURS_ACTIVITE = 30
MAX_LIGNES = 8
POURQUOI = {
    "repartition": tl("engine/fantome_message.py:g:11:20"),
    "comparaison": tl("engine/fantome_message.py:g:12:20"),
    "evolution": tl("engine/fantome_message.py:g:13:18"),
    "distribution": tl("engine/fantome_message.py:g:14:21"),
    "relation": tl("engine/fantome_message.py:g:15:17"),
    "correlation": tl("engine/fantome_message.py:g:16:20"),
}
FORMULES = {"somme": tl("engine/fantome_message.py:g:18:22"), "moyenne": tl("engine/fantome_message.py:g:18:45"), "mediane": tl("engine/fantome_message.py:g:18:70"), "ecart_type": tl("engine/fantome_message.py:g:18:98")}


def _ligne(d: dict) -> str | None:
    cols = list(d.get("colonnes") or []) + ["", ""]
    if d.get("genre") == "graphique":
        raison = POURQUOI.get(d.get("role") or "", td("engine/fantome_message.py:g:25:52")).format(*cols)
        page = d.get("page") or t("engine/fantome_message.py:g:25:33")
        return tf("engine/fantome_message.py:g:26:16", v1=d['titre'], v2=raison, v3=page)
    if d.get("genre") == "kpi":
        return (tf("engine/fantome_message.py:g:28:17", v1=d['titre'], v2=FORMULES.get(d.get('formule'), td("engine/fantome_message.py:g:29:106")), v3=cols[0]))
    if d.get("genre") == "table_calcul":
        return tf("engine/fantome_message.py:g:31:16", v1=d['titre'], v2=d.get('nb', 0))
    return None


def composer(table_nom: str, details: list[dict], actif: bool) -> tuple[str, str]:
    """(titre, message) d'une recommandation. `actif` : la personne travaille elle-même sur cette table."""
    lignes = [l for l in (_ligne(d) for d in details) if l]
    reste = max(0, len(lignes) - MAX_LIGNES)
    intro = (tf("engine/fantome_message.py:g:39:14", v1=table_nom) if actif
             else tf("engine/fantome_message.py:g:40:19", v1=table_nom))
    fin = t("engine/fantome_message.py:g:41:11")
    corps = "\n".join(lignes[:MAX_LIGNES] + ([tf("engine/fantome_message.py:g:43:47", v1=reste, v2='s' if reste > 1 else '')] if reste else []))
    return tf("engine/fantome_message.py:g:43:12", v1=table_nom), f"{intro}\n{corps}\n{fin}"


def membres_actifs(entreprise, table_id: int):
    """(membres qui ont modifié cette table ces JOURS_ACTIVITE derniers jours, tous les agents/admins) — d'après l'audit réel."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import JournalAudit, MembreEntreprise
    depuis = timezone.now() - timedelta(days=JOURS_ACTIVITE)
    utilisateurs = set(JournalAudit.objects.filter(entreprise=entreprise, type_acteur="utilisateur", horodatage__gte=depuis,
                                                   action__contains=f"/api/tables/{table_id}/").values_list("utilisateur_id", flat=True))
    membres = list(MembreEntreprise.objects.filter(entreprise=entreprise, role__in=("agent", "admin")))
    return [m for m in membres if m.utilisateur_id in utilisateurs], membres
