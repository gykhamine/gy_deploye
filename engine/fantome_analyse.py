"""ANALYSE STATISTIQUE + RÉSEAU DE NEURONES qui décident de ce que le modèle fantôme construit (voir fantome_construction.py).

Le fantôme n'interagit JAMAIS avec l'utilisateur. Pour chaque candidat (graphique, KPI, page) il :
  1. ANALYSE les statistiques de la table (dispersion, asymétrie, valeurs aberrantes, équilibre des catégories, remplissage, volume) ;
  2. en tire un vecteur x (statistiques + rôle du graphique + type de graphique + AUDIENCE : super administrateur, administrateur,
     agent, client) ;
  3. ENTRAÎNE paresseusement un petit réseau (reseau_neuronal.Reseau, Adam conservé = fine-tuning quotidien, quelques pas par passage)
     sur ses propres résultats statistiques (a priori « utilité informative », poids faible) puis sur les retours de l'utilisateur
     (gardé/utilisé = 1, retiré = 0, poids fort) ;
  4. CONSTRUIT dans l'ordre de ce que le réseau prédit.
Le réseau apprend ainsi le fonctionnement de l'entreprise suivie, pas un motif figé. Module PUR (numpy, aucun import Django hors
audiences()). Le réseau vit dans le registre JSON de l'entreprise (registre["reseau_stat"], npz sans pickle en base64).
"""
from __future__ import annotations

import base64
import math

import numpy as np

from . import fantome_schema as fs
from . import reseau_neuronal as rn

ROLES_GRAPHIQUE = ("repartition", "comparaison", "evolution", "distribution", "relation", "correlation",
                   "referentiel", "ontologie")   # les deux derniers : propositions des onglets Données et Ontologie (fantome_onglets.py)
SLUGS = ("camembert", "anneau", "comptage_categoriel", "barres_verticales", "barres_horizontales", "lollipop", "courbe", "aire",
         "histogramme", "boite_moustaches", "nuage_points", "heatmap_correlation")
AUDIENCES = ("superadmin", "admin", "agent", "client")   # MembreEntreprise.role : admin / agent / lecteur(= client) ; superadmin = is_superuser
NB_DOM = 9   # nombre de métiers de fantome_construction.DOMAINES (vérifié à l'import de fantome_metier)
NB_COMPORTEMENT = 4   # fantome_comportement.NB_COMPORTEMENT : activité réelle de la table (ajouté EN FIN de vecteur : le réseau déjà entraîné est conservé)
ROLES_PROP = ("geo", "tache", "document", "rapport")   # propositions géospatiales, tâches, documents (fantome_propositions.py) : ajoutés EN FIN de vecteur
NB_USAGE = 7   # fantome_usage.NB_USAGE : où l'entreprise passe son temps (carte, calculs, ontologie, BI, rapport, documents, tâches)
NB_SCHEMA = fs.NB_SCHEMA   # fantome_schema : activité de chaque famille de tables de la plateforme + signaux de l'argent (TVA, wallets) — AJOUTÉ EN FIN de vecteur
NB_EXTRA = len(ROLES_PROP) + NB_USAGE   # ajouté après le comportement : le réseau déjà entraîné est conservé (poids nuls)
D = len(ROLES_GRAPHIQUE) + len(SLUGS) + len(AUDIENCES) + 6 + NB_DOM + NB_COMPORTEMENT + NB_EXTRA + NB_SCHEMA
LARGEUR, MAX_ECH, PAS_PAR_PASSAGE, LR = 12, 300, 20, 0.02


def audiences(entreprise) -> dict:
    """Part de chaque type d'utilisateur de l'entreprise (somme = 1) : le réseau reçoit ce profil d'audience en entrée."""
    from django.contrib.auth import get_user_model
    from .models import MembreEntreprise
    n = dict.fromkeys(AUDIENCES, 0)
    membres = MembreEntreprise.objects.filter(entreprise=entreprise).select_related("utilisateur")
    for m in membres:
        n["superadmin" if getattr(m.utilisateur, "is_superuser", False) else {"admin": "admin", "agent": "agent"}.get(m.role, "client")] += 1
    if not any(n.values()):
        n["admin"] = 1
    s = sum(n.values())
    return {k: v / s for k, v in n.items()}


def _f(v, d=0.0) -> float:
    try:
        v = float(v)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def statistiques(colonnes: list[dict]) -> dict:
    """Résultats statistiques moyens des colonnes d'un candidat (champs de fusion.py), tous ramenés dans [0, 1]."""
    cv = asym = aber = equi = remp = vol = 0.0
    for c in colonnes:
        num = c.get("num") or {}
        if num:
            moy, ec = _f(num.get("moy")), _f(num.get("ecart"))
            cv += math.tanh(ec / abs(moy)) if moy else math.tanh(ec)
            asym += math.tanh(abs(moy - _f(num.get("mediane"))) / ec) if ec > 0 else 0.0
            aber += min(1.0, _f(num.get("aberrants")) / max(1.0, _f(num.get("n")) * 0.05))
        equi += 1.0 - _f(c.get("top_part"), 1.0) if c.get("top_part") else 0.5
        remp += _f(c.get("remplissage"))
        vol += min(1.0, math.log10(1 + _f(c.get("rempli"))) / 4)
    n = max(1, len(colonnes))
    return {"cv": cv / n, "asym": asym / n, "aber": aber / n, "equi": equi / n, "remp": remp / n, "vol": vol / n}


def vecteur(role: str, slug: str, colonnes: list[dict], audience: dict, metier: list[float] | None = None, comportement: list[float] | None = None,
            usage: list[float] | None = None, role_prop: str | None = None, schema: list[float] | None = None) -> list[float]:
    """x (dimension D) d'un candidat : rôle, type de graphique, audience, statistiques, affinités métier de l'entreprise (NB_DOM)."""
    st = statistiques(colonnes)
    return ([1.0 if role == r else 0.0 for r in ROLES_GRAPHIQUE] + [1.0 if slug == s else 0.0 for s in SLUGS]
            + [audience.get(a, 0.0) for a in AUDIENCES] + [st[k] for k in ("cv", "asym", "aber", "equi", "remp", "vol")] + list((metier or [])[:NB_DOM]) + [0.0] * (NB_DOM - len(metier or []))
            + (list(comportement or [])[:NB_COMPORTEMENT] + [0.0] * NB_COMPORTEMENT)[:NB_COMPORTEMENT]
            + [1.0 if role_prop == r else 0.0 for r in ROLES_PROP] + (list(usage or [])[:NB_USAGE] + [0.0] * NB_USAGE)[:NB_USAGE]
            + fs.completer(schema))


def a_priori(role: str, colonnes: list[dict]) -> float:
    """Utilité informative selon les statistiques seules (étiquette faible tant que l'utilisateur n'a rien jugé)."""
    st = statistiques(colonnes)
    base = {"repartition": st["equi"], "comparaison": 0.5 * st["equi"] + 0.5 * st["cv"], "evolution": st["cv"],
            "distribution": min(1.0, st["asym"] + st["aber"]), "relation": st["cv"], "correlation": st["cv"], "referentiel": st["equi"] * 0.8, "ontologie": 0.5}.get(role, 0.5)
    return float(min(1.0, max(0.0, base * (0.5 + 0.5 * st["remp"]))))


# ── Réseau dans le registre ───────────────────────────────────────────────────────────────────────────────────────────────
def _charger(registre: dict) -> rn.Reseau:
    brut = registre.get("reseau_stat")
    try:
        z = rn.depuis_octets(base64.b64decode(brut))
        r = rn.Reseau.depuis_tableaux(z)
        if r.d_in < D and r.d_out == 1:
            r.completer_entrees(D)   # nouvelles entrées (comportement, usage, propositions) à poids nuls : ce qui a été appris est conservé
        if r.d_in == D and r.d_out == 1:
            return r
    except Exception:
        pass
    if brut:   # réseau stocké mais inutilisable ici (disposition étrangère, contenu illisible) : repart à neuf, SANS perdre l'ancien (copie, une seule fois)
        registre.setdefault("reseau_stat_ancien", brut)
    return rn.Reseau.nouveau(D, LARGEUR, 1, graine=7)


def _sauver(registre: dict, r: rn.Reseau) -> None:
    registre["reseau_stat"] = base64.b64encode(rn.vers_octets(r.tableaux())).decode()


def score(registre: dict, x: list[float]) -> float:
    """Utilité prédite (0 à 1) d'un candidat."""
    return float(np.clip(_charger(registre).predire(x)[0, 0], 0.0, 1.0))


def ordonner(registre: dict, candidats: list, cle_x) -> list:
    """Candidats triés du plus au moins utile selon le réseau (`cle_x(c)` rend x)."""
    r = _charger(registre)
    return sorted(candidats, key=lambda c: -float(r.predire(cle_x(c))[0, 0]))


def observer(registre: dict, x: list[float], y: float, poids: float) -> None:
    """Ajoute un échantillon (résultat statistique a priori ou retour de l'utilisateur) ; mémoire bornée."""
    ech = registre.setdefault("stat_ech", [])
    ech.append({"x": [round(float(v), 4) for v in x], "y": float(y), "w": float(poids)})
    del ech[:-MAX_ECH]


def affiner(registre: dict) -> float | None:
    """Fine-tuning paresseux : quelques pas d'Adam sur les échantillons récents ; rend la perte (None s'il n'y a rien à apprendre)."""
    ech = [{**e, "x": list(e["x"]) + [0.0] * (D - len(e["x"]))} for e in registre.get("stat_ech", []) if 0 < len(e.get("x", ())) <= D]   # anciens échantillons complétés par des zéros
    if len(ech) < 4:
        return None
    r = _charger(registre)
    x = np.array([e["x"] for e in ech])
    y = np.array([[e["y"]] for e in ech])
    w = np.array([[e["w"]] for e in ech])
    perte = None
    for _ in range(PAS_PAR_PASSAGE):
        perte = r.pas(x, y, w, LR)
    _sauver(registre, r)
    return perte
