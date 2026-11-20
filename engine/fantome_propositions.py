"""PROPOSITIONS DU FANTÔME : carte, tâches, documents. Il prépare, l'utilisateur garde ou supprime. Rien n'est exécuté.

Complète fantome_construction.py (BI, Calcul) et fantome_onglets.py (Données, Ontologie) pour les trois derniers domaines :
  * CARTE     — « proposer_geo » : d'après les positions réelles d'une table, un point de DÉPART probable (centre de gravité), jusqu'à trois
                DESTINATIONS (zones les plus fréquentées) et une trajectoire départ → destination principale (à vol d'oiseau ; l'itinéraire par
                les routes se calcule dans la page). Ce sont des annotations ordinaires, titrées « Proposition · … », de nature « contexte »
                (aucune alerte de proximité). Écrites dans la table d'annotations, comme celles de l'utilisateur ;
  * TÂCHES    — « proposer_tache » : une tâche CONDITIONNELLE sur une table active, créée INACTIVE, sans script (rien ne se génère ni ne
                s'exécute tant que l'utilisateur ne l'active pas) ;
  * DOCUMENTS — « proposer_document » : un brouillon de rapport lié à une table, statut « brouillon », sans script ni fichier (rien n'est généré) ;
  * CARTES DU DASHBOARD (onglet Dashboard = rapport) — « proposer_carte » : une carte d'analyse (DemandeAgent) au statut « proposée » : question
                seule, AUCUN script. L'utilisateur clique « Préparer l'analyse » (l'IA écrit alors le script, puis « Lancer ») ou la retire.
Aucune règle métier : ce qui compte (quelle table, quel domaine) vient du réseau, nourri par l'activité réelle, l'usage (fantome_usage),
le métier compris, l'audience et les retours. Il ne SUPPRIME jamais rien, n'écrit jamais dans les tables de données de l'utilisateur,
n'appelle ni les vues ni l'exécuteur de scripts. Même mécanique que le reste : liste blanche, clés jamais rejouées, une proposition par
passage, gardé 3 jours = succès, retiré = échec et jamais reconstruit, recommandation sans mention du système.
Les décisions sont PURES ; seules construire_proposition / existants / notifier / passer importent Django. Désactivable : GYPLATFORM_FANTOME_CONSTRUIT=0.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf, tl

import logging
import math
import statistics

from . import fantome_analyse as fa
from . import fantome_construction as fc

logger = logging.getLogger(__name__)
ACTIONS = ("proposer_geo", "proposer_tache", "proposer_document", "proposer_carte")   # LISTE BLANCHE
GENRES = ("annotation_geo", "tache", "document", "carte_rapport")
MIN_POSITIONS = 10
CELLULE = 0.01            # degrés (~1 km) : taille des cases qui servent à repérer les destinations fréquentes
MAX_DESTINATIONS = 3
DISTANCE_MIN_M = 500.0    # une destination plus proche du départ n'en est pas une
LECTURE_MAX = 2000        # dernières lignes lues pour une table
PREFIXE = "Proposition · "


def _dist_m(a, b) -> float:
    k = math.pi / 180 * 6371000.0
    return math.hypot((a[0] - b[0]) * k, (a[1] - b[1]) * k * math.cos(math.radians((a[0] + b[0]) / 2)))


def points_proposes(positions: list[tuple]) -> dict | None:
    """positions [(lat, lon)] -> {"depart": (lat, lon), "destinations": [(lat, lon, n)]} ou None si trop peu de positions. PUR."""
    pts = [(float(a), float(b)) for a, b in positions if math.isfinite(a) and math.isfinite(b)]
    if len(pts) < MIN_POSITIONS:
        return None
    depart = (round(statistics.median(p[0] for p in pts), 6), round(statistics.median(p[1] for p in pts), 6))
    cases = {}
    for p in pts:
        cases.setdefault((math.floor(p[0] / CELLULE), math.floor(p[1] / CELLULE)), []).append(p)
    seuil = max(3, int(0.05 * len(pts)))
    dest = []
    for membres in sorted(cases.values(), key=lambda m: (-len(m), m[0])):
        c = (round(statistics.fmean(p[0] for p in membres), 6), round(statistics.fmean(p[1] for p in membres), 6))
        if len(membres) >= seuil and _dist_m(depart, c) >= DISTANCE_MIN_M:
            dest.append((c[0], c[1], len(membres)))
        if len(dest) >= MAX_DESTINATIONS:
            break
    return {"depart": depart, "destinations": dest}


def annotations_a_creer(plan: dict) -> list[dict]:
    """Annotations de la carte (format de geo_annotations.creer) pour un plan de points_proposes. PUR."""
    base = {"nature": "contexte", "alerte": "non", "couleur": "#8e8e93", "origine": "proposition"}
    sortie = [{**base, "type": "point", "titre": PREFIXE + _i18n_t("engine/fantome_propositions.py:g:67:60"), "geometrie": [list(plan["depart"])],
               "note": _i18n_t("engine/fantome_propositions.py:g:68:24")}]
    for i, (la, lo, n) in enumerate(plan["destinations"], 1):
        sortie.append({**base, "type": "point", "titre": PREFIXE + f"Destination {i}", "geometrie": [[la, lo]],
                       "note": tf("engine/fantome_propositions.py:g:71:32", v1=i, v2=n)})
    if plan["destinations"]:
        la, lo, _n = plan["destinations"][0]
        sortie.append({"type": "trajectoire", "titre": PREFIXE + _i18n_t("engine/fantome_propositions.py:g:74:66"), "geometrie": [list(plan["depart"]), [la, lo]],
                       "couleur": "#8e8e93", "origine": "proposition", "note": _i18n_t("engine/fantome_propositions.py:g:75:80")})
    return sortie


def propositions(etat: dict, registre: dict, maintenant: float, audience: dict, metier: dict | None, usage: list | None = None) -> list[dict]:
    """Candidats triés (meilleur d'abord). Les clés déjà au registre (construites, retenues, retirées, ignorées) ne reviennent jamais. PUR."""
    deja = {e["cle"] for e in registre.get("elements", [])}
    vm = [(metier or {}).get(c, 0.0) for c in fc.DOMAINES]
    usage = usage if usage is not None else etat.get("usage")
    tables = {f["id"]: f for f in etat.get("tables", []) if not f.get("clients") and not f.get("nosql")}
    sortie = []

    def ajouter(cle, action, role, f, nom, prior, **extra):
        if cle in deja:
            return
        x = fa.vecteur("", "", [], audience, vm, f.get("comportement"), usage, role, f.get("schema"))
        sortie.append({"cle": cle, "action": action, "role": role, "table_id": f["id"], "nom": nom, "source": f["nom"], "x": x,
                       "prior": float(min(1.0, max(0.0, prior))), **extra})

    for g in (etat.get("geo") or {}).get("tables", []):   # CARTE : une table qui porte de vraies positions
        f = tables.get(g.get("id"))
        if f is not None and g.get("positions", 0) >= MIN_POSITIONS:
            ajouter(f"geo:{f['id']}", "proposer_geo", "geo", f, _i18n_t("engine/fantome_propositions.py:g:97:65"), min(1.0, g["positions"] / 200.0))
    for f in tables.values():   # TÂCHES et DOCUMENTS : une table adulte et calme, où l'on travaille vraiment
        if not fc.table_prete(f, maintenant):
            continue
        activite = (f.get("comportement") or [0.0, 0.0])[:2]
        if not any(activite):
            continue
        ajouter(f"tache:{f['id']}", "proposer_tache", "tache", f, "Surveillance", 0.5 * activite[0] + 0.5 * activite[1])
        ajouter(f"doc:{f['id']}", "proposer_document", "document", f, _i18n_t("engine/fantome_propositions.py:g:105:71"), 0.5 * activite[0] + 0.3 * activite[1] + 0.2 * min(1.0, f.get("nb_lignes", 0) / 500.0))
        ajouter(f"carte:{f['id']}", "proposer_carte", "rapport", f, _i18n_t("engine/fantome_propositions.py:g:106:69"), 0.5 * activite[0] + 0.5 * activite[1])
    for p in sortie:
        p["score"] = 0.5 * fa.score(registre, p["x"]) + 0.5 * p["prior"]
    return sorted(sortie, key=lambda p: -p["score"])


# ── Construction (seule partie qui écrit : annotations de la carte, une tâche inactive, un brouillon) ──────────────────
def _positions(table) -> list[tuple]:
    from .geo_donnees import colonnes_geo
    from .visualization import _parse_geo
    champs = colonnes_geo(table)
    pts = []
    from . import parquet_store
    for d in parquet_store.lignes_analytiques(table, LECTURE_MAX):
        for c in champs:
            g = _parse_geo(d.get(c.slug))
            if g:
                pts.append((g[0], g[1]))
    return pts


def construire_proposition(entreprise, p: dict, registre: dict, maintenant: float) -> dict:
    """Crée UNE proposition. Rend {"annotations": n, "taches": n, "documents": n}."""
    from .models import DemandeAgent, DocumentGenere, TableDynamique, TachePlanifiee
    bilan = {"annotations": 0, "taches": 0, "documents": 0, "cartes": 0}
    table = TableDynamique.objects.filter(id=p["table_id"], entreprise=entreprise).first()
    if table is None:
        return bilan

    def noter(genre, objet_id, cle, avec_x):
        registre.setdefault("elements", []).append({"cle": cle, "role_cle": None, "action": p["action"], "choix": "proposition", "genre": genre,
                                                    "objet_id": objet_id, "table_id": table.id, "tableau_id": None, "ts": maintenant,
                                                    "statut": "construit", "x": p["x"] if avec_x else None})

    if p["action"] == "proposer_geo":
        from . import geo_annotations as ga
        plan = points_proposes(_positions(table))
        if plan is None:
            return bilan
        for i, d in enumerate(annotations_a_creer(plan)):
            noter("annotation_geo", ga.creer(entreprise, None, table.id, d), p["cle"] if i == 0 else f"{p['cle']}:{i}", i == 0)
            bilan["annotations"] += 1
    elif p["action"] == "proposer_tache":
        t = TachePlanifiee.objects.create(
            entreprise=entreprise, utilisateur=None, nom=(PREFIXE + tf("rg8.msg.006", nom=table.nom))[:255], actif=False, statut="en_attente",
            instructions=(tf("engine/fantome_propositions.py:g:153:27", v1=table.nom)),
            mode_declenchement="condition", condition_categorie="enregistrement", condition_table=table, intervalle_type="minutes", intervalle_valeur=10)
        noter("tache", t.id, p["cle"], True)
        bilan["taches"] = 1
    elif p["action"] == "proposer_carte":
        d = DemandeAgent.objects.create(
            entreprise=entreprise, utilisateur=None, table_cible=table, statut="proposee",
            texte=PREFIXE + tf("engine/fantome_propositions.py:g:159:29", v1=table.nom))
        noter("carte_rapport", d.id, p["cle"], True)
        bilan["cartes"] = 1
    else:
        doc = DocumentGenere.objects.create(
            entreprise=entreprise, utilisateur=None, table_cible=table, type_document="rapport", titre=(PREFIXE + tf("engine/fantome_propositions.py:g:164:115", v1=table.nom))[:255],
            instructions=(tf("engine/fantome_propositions.py:g:167:27", v1=table.nom)),
            statut="brouillon")
        noter("document", doc.id, p["cle"], True)
        bilan["documents"] = 1
    return bilan


def existants(entreprise, registre: dict) -> dict:
    """{genre: ids encore présents} pour appliquer_retours (toujours les trois genres)."""
    from . import geo_annotations as ga
    from .models import DemandeAgent, DocumentGenere, TachePlanifiee
    tables = {e["table_id"] for e in registre.get("elements", []) if e.get("genre") == "annotation_geo"}
    annotations = set()
    for tid in tables:
        annotations |= {a["id"] for a in ga.lister(entreprise, tid)}
    return {"annotation_geo": annotations,
            "tache": set(TachePlanifiee.objects.filter(entreprise=entreprise).values_list("id", flat=True)),
            "document": set(DocumentGenere.objects.filter(entreprise=entreprise).values_list("id", flat=True)),
            "carte_rapport": set(DemandeAgent.objects.filter(entreprise=entreprise).values_list("id", flat=True))}


TEXTES = {
    "proposer_geo": tl("engine/fantome_propositions.py:g:188:21"),
    "proposer_tache": tl("engine/fantome_propositions.py:g:190:23"),
    "proposer_document": tl("engine/fantome_propositions.py:g:191:26"),
    "proposer_carte": tl("engine/fantome_propositions.py:g:192:23"),
}


def notifier(entreprise, p: dict) -> int:
    """Recommandation (agents qui travaillent sur la table + administrateurs ; à tous si personne), jamais les clients, sans mention du système."""
    from . import fantome_message as fmsg
    from .models import Notification, TableDynamique
    table = TableDynamique.objects.filter(id=p["table_id"], entreprise=entreprise).first()
    actifs, membres = fmsg.membres_actifs(entreprise, p["table_id"])
    ids = {m.id for m in actifs}
    texte = TEXTES[p["action"]].format(s=p["source"]) + _i18n_t("engine/fantome_propositions.py:g:203:57")
    n = 0
    for m in membres:
        if actifs and m.id not in ids and m.role != "admin":
            continue
        from .notifications import pousser_notification
        pousser_notification(Notification.objects.create(entreprise=entreprise, destinataire=m, type_notification="recommandation", titre=f"Proposition : {p['nom']} — {p['source']}",
                                                         message=texte, table=table))
        n += 1
    return n


def passer(entreprise, etat: dict, registre: dict, audience: dict, metier: dict | None, maintenant: float) -> dict:
    """UNE proposition au plus (tout ou rien). Appelé par fantome_construction.passer quand rien d'autre n'a été construit."""
    from django.db import transaction
    bilan = {"propose": 0}
    if fc._en_attente(registre, maintenant) >= fc.MAX_EN_ATTENTE:
        return bilan
    for p in propositions(etat, registre, maintenant, audience or {"admin": 1.0}, metier)[:3]:
        n0 = len(registre.get("elements", []))
        try:
            with transaction.atomic():
                res = construire_proposition(entreprise, p, registre, maintenant)
        except Exception:
            logger.exception(_i18n_t("engine/fantome_propositions.py:g:226:30"), entreprise.id, p["cle"])
            del registre.get("elements", [])[n0:]
            res = {}
        if not sum(res.values()):   # rien à proposer ou échec : mémorisé comme ignoré, jamais rejoué
            registre.setdefault("elements", []).append({"cle": p["cle"], "role_cle": None, "action": "rien", "choix": "rien", "genre": "rien", "objet_id": 0,
                                                        "table_id": p["table_id"], "tableau_id": None, "ts": maintenant, "statut": "ignore"})
            continue
        fa.observer(registre, p["x"], p["prior"], 0.3)   # résultat statistique = étiquette faible avant tout jugement de l'utilisateur
        registre.setdefault("lots", []).append({"table_id": p["table_id"], "ts": maintenant, "statut": "construit", "domaine": p["action"]})
        notifier(entreprise, p)
        bilan["propose"] = 1
        break
    return bilan
