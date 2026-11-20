"""USAGE RÉEL de la plateforme observé par le modèle fantôme : où l'entreprise (et chaque table) passe son temps — carte, calculs,
ontologie, BI, rapport (onglet Dashboard), documents, tâches.

Ce sont de simples ENTRÉES du réseau (aucune règle) : à mesure que l'utilisateur garde ou retire ce que le fantôme propose, le réseau
apprend ce que ces habitudes signifient. Pur sauf observer() (lit MesureUsage : pages, actions et ouvertures d'onglets, jamais un contenu).
Par table : les « ouvertures » (cible « table:<id>:<onglet> », envoyées par usabilite.js à chaque ouverture d'un onglet d'une table).
"""
from __future__ import annotations

import re

JOURS = 30
SECTIONS = ("geo", "calcul", "ontologie", "bi", "rapport", "document", "tache")   # bi AVANT rapport : « tableaux-bord-bi » est de la BI
NB_USAGE = len(SECTIONS)
MOTS = {"geo": ("geo", "carte", "position", "geolocalisation"), "calcul": ("calcul", "formule", "stat", "statistiques"),
        "ontologie": ("ontolog", "lineage", "relation"), "bi": ("bi", "graphique", "kpi"),
        "rapport": ("tableau", "rapport", "analyse", "demande"), "document": ("document", "facture"), "tache": ("tache", "task", "planif")}
DUREE_ACTION_MS = 5000   # une action ou une ouverture compte comme quelques secondes de présence


def section(page: str) -> str | None:
    """Section d'un chemin ou d'une cible : mot entier, ou mot d'au moins 4 lettres qui en prolonge un (pluriel, accord)."""
    jetons = [j for j in re.split(r"[^a-z0-9]+", str(page or "").lower()) if j]
    for s in SECTIONS:
        if any(j == m or (len(m) >= 4 and j.startswith(m)) for j in jetons for m in MOTS[s]):
            return s
    return None


def caracteristiques(evenements: list[tuple]) -> list[float]:
    """evenements = [(page ou cible, durée en ms)] -> part du temps de chaque section, dans [0, 1] (zéros s'il n'y a rien)."""
    temps = dict.fromkeys(SECTIONS, 0.0)
    for page, duree in evenements:
        s = section(page)
        if s:
            temps[s] += max(0.0, float(duree or 0))
    total = sum(temps.values())
    return [round(temps[s] / total, 4) if total else 0.0 for s in SECTIONS]


def par_table(ouvertures: list[str]) -> dict:
    """ouvertures = cibles « table:<id>:<onglet> » -> {table_id: parts par section}. PUR."""
    evs = {}
    for c in ouvertures:
        m = re.fullmatch(r"table:(\d+):([A-Za-z0-9_-]+)", str(c or ""))
        if m:
            evs.setdefault(int(m.group(1)), []).append((m.group(2), DUREE_ACTION_MS))
    return {tid: caracteristiques(ev) for tid, ev in evs.items()}


def observer(entreprise) -> tuple[list[float], dict]:
    """(usage de l'entreprise, {table_id: usage de la table}) sur les JOURS derniers jours."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import MesureUsage
    depuis = timezone.now() - timedelta(days=JOURS)
    qs = MesureUsage.objects.filter(entreprise=entreprise, cree_le__gte=depuis)
    ouvertures = list(qs.filter(type_mesure="ouverture").values_list("cible", flat=True)[:20000])
    ev = [(p, d) for p, d in qs.filter(type_mesure="page").values_list("page", "duree_ms")[:20000]]
    ev += [(c or p, DUREE_ACTION_MS) for c, p in qs.filter(type_mesure="action").values_list("cible", "page")[:20000]]
    ev += [(c.split(":")[-1], DUREE_ACTION_MS) for c in ouvertures if c]
    return caracteristiques(ev), par_table(ouvertures)
