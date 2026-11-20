"""COMPORTEMENT RÉEL observé : où l'entreprise travaille vraiment, d'après l'audit (qui a modifié quelle table, quand).

Ce module ne décide RIEN. Il ne fait que décrire, pour chaque table, ce que les gens en font ; ces 4 nombres (dans [0, 1]) sont des
ENTRÉES du réseau (fantome_analyse.vecteur). Au départ leurs poids sont nuls (Reseau.completer_entrees) : le réseau répond comme avant,
puis, à mesure que l'utilisateur garde ou retire ce qui est proposé, il apprend LUI-MÊME si l'activité d'une table, son rythme récent,
le nombre de personnes qui y travaillent et la part des agents comptent pour décider quoi proposer. Aucune règle écrite à la main.
Les tables ne sont jamais lues dans leur contenu : seulement le chemin d'API et l'auteur de chaque écriture.
"""
from __future__ import annotations

import re

NB_COMPORTEMENT = 4   # part de l'activité, rythme récent, nombre de personnes, part d'agents
JOURS, JOURS_RECENT, PLAFOND_PERSONNES, MAX_LIGNES_AUDIT = 30, 7, 5, 20000
_CHEMIN = re.compile(r"^(?:POST|PUT|PATCH|DELETE) /api/tables/(\d+)/")


def caracteristiques(evenements: list[tuple]) -> dict:
    """PUR. `evenements` = [(table_id, utilisateur_id, role, age_en_jours)] → {table_id: [part, récent, personnes, agents]}."""
    total = len(evenements) or 1
    par_table: dict = {}
    for tid, uid, role, age in evenements:
        par_table.setdefault(tid, []).append((uid, role, age))
    sortie = {}
    for tid, ev in par_table.items():
        n = len(ev)
        sortie[tid] = [round(n / total, 4), round(sum(1 for _u, _r, a in ev if a <= JOURS_RECENT) / n, 4),
                       round(min(1.0, len({u for u, _r, _a in ev}) / PLAFOND_PERSONNES), 4), round(sum(1 for _u, r, _a in ev if r == "agent") / n, 4)]
    return sortie


def observer(entreprise) -> dict:
    """Lit l'audit réel de l'entreprise (écritures des JOURS derniers jours, bornées) → {table_id: caractéristiques}. Ne crée rien."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import JournalAudit, MembreEntreprise
    maintenant = timezone.now()
    roles = dict(MembreEntreprise.objects.filter(entreprise=entreprise).values_list("utilisateur_id", "role"))
    lignes = (JournalAudit.objects.filter(entreprise=entreprise, type_acteur="utilisateur", horodatage__gte=maintenant - timedelta(days=JOURS),
                                          action__contains="/api/tables/").order_by("-horodatage").values_list("action", "utilisateur_id", "horodatage")[:MAX_LIGNES_AUDIT])
    evenements = []
    for action, uid, quand in lignes:
        m = _CHEMIN.match(action or "")
        if m:
            evenements.append((int(m.group(1)), uid, roles.get(uid, ""), (maintenant - quand).total_seconds() / 86400))
    return caracteristiques(evenements)
