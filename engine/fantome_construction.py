"""CONSTRUCTION EN COULISSES : le modèle fantôme observe ce que l'utilisateur crée et prépare l'interface à sa place.

Il détecte le métier de l'utilisateur (santé, finances, ventes…) et le rôle de chaque colonne (date, mesure, catégorie…), puis
construit, À PARTIR DE CE QUI EXISTE DÉJÀ (graphiques et formules prédéfinis, jamais de code nouveau), des graphiques, des KPI,
des tableaux de bord sur une ou plusieurs pages et des tables de formules. L'utilisateur les retrouve prêts ; il en est averti
par une recommandation (Notification « recommandation »), jamais par une mention du modèle.

LE MODÈLE FANTÔME N'A PAS ACCÈS AUX FONCTIONS DE BASE. Ses actions lui sont propres (ACTIONS ci-dessous) :
  - il n'appelle ni engine.actions, ni les vues, ni l'exécuteur de scripts : il crée des lignes de modèles, rien d'autre ;
  - il ne SUPPRIME jamais rien (ni les objets de l'utilisateur, ni les siens : seul l'utilisateur retire) ;
  - il n'écrit jamais dans les tables de données de l'utilisateur et n'exécute aucun code (formules/graphiques prédéfinis seulement) ;
  - il n'ajoute à un tableau de bord existant qu'EN BAS (rien n'est déplacé).
tests_fantome_construction.py vérifie ces interdits sur le texte même de ce module.

APPRENTISSAGE SUR L'UTILISATEUR (registre JSON à côté du modèle, voir _chemin_registre) :
  - habitudes : les graphiques/formules que l'utilisateur utilise déjà comptent comme succès (a priori Beta par choix) ;
  - retours : un élément garde > RETENU_JOURS jours = succès ; retiré par l'utilisateur = échec, et n'est JAMAIS reconstruit ;
  - choix entre alternatives équivalentes (camembert ou anneau…) par Thompson sur ces compteurs.
Prudence : au plus un lot par passage et par table, MAX_EN_ATTENTE lots non encore « retenus », uniquement sur une table calme
(aucune activité depuis CALME_S secondes : l'utilisateur a fini de saisir), jamais sur les tables de clients.

Module PUR pour tout ce qui décide (profil, domaine, candidats, choix) : testable avec des dictionnaires (états de fusion.py).
Les imports de modèles Django sont différés dans les fonctions de construction. Désactivable : GYPLATFORM_FANTOME_CONSTRUIT=0.
"""
from __future__ import annotations
from engine.i18n import tf as _i18n_tf, td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tfr as _gtfr  # noqa: E402
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tl

import json
import logging
import os
import random
import re
import time
import unicodedata

from . import fantome_analyse as fa
from . import fantome_langue as fl
from .stockage import dossier_systeme


def _ids_groupe_fc(table):
    from .vue_groupe import ids_groupe
    return ids_groupe(table)


def _ancre_fc(table):
    from .vue_groupe import ancre_groupe
    return ancre_groupe(table)


def nom_vue(table):
    from .vue_groupe import nom_vue as _n
    return _n(table)

logger = logging.getLogger(__name__)

ACTIONS = ("ajouter_graphique", "ajouter_kpi", "creer_tableau_bord", "creer_table_formules")   # LISTE BLANCHE : rien d'autre

MIN_LIGNES = 8             # en dessous, une table est trop petite pour juger ses colonnes
REMPLISSAGE_MIN = 0.5
CALME_S = 300              # table calme : aucune modification depuis 5 minutes
MAX_EN_ATTENTE = 3         # lots récents pas encore « retenus » : on n'en ajoute pas d'autre
RETENU_JOURS = 3
MAX_WIDGETS, MAX_KPI = 6, 3
MAX_ELEMENTS_TABLE = 14

TYPES_NUM = {"nombre", "decimal", "monnaie", "pourcentage", "note", "barre_progression"}
TYPES_DATE = {"date", "datetime", "horodatage"}
TYPES_CATEGORIEL = {"choix_unique", "couleur", "texte"}   # types compatibles de camembert / comptage (voir graphiques_bi)

# ── Connaissance des métiers : mots-clés (sans accents, minuscules) et colonnes clés ──────────────────────────────────────
DOMAINES = {
    "sante": {"libelle": _gtfr("engine/fantome_construction.py:75:26"), "mots": ("patient", "malade", "consultation", "diagnostic", "traitement", "medicament", "ordonnance",
                                            "hopital", "clinique", "soin", "symptome", "vaccin", "analyse", "tension", "poids", "sante"),
              "mesures": ("poids", "tension", "glycemie", "temperature", "dose", "duree", "age", "cout"),
              "dimensions": ("service", "diagnostic", "medecin", "sexe", "traitement", "statut")},
    "finance": {"libelle": "finances", "mots": ("budget", "depense", "recette", "revenu", "charge", "tresorerie", "facture", "paiement",
                                                "compte", "solde", "credit", "dette", "banque", "montant", "comptabilite", "taux", "impot"),
                "mesures": ("montant", "solde", "total", "depense", "recette", "revenu", "charge", "valeur"),
                "dimensions": ("categorie", "compte", "type", "poste", "statut", "mode")},
    "ventes": {"libelle": "ventes", "mots": ("vente", "client", "commande", "produit", "article", "prix", "quantite", "stock", "panier",
                                            "boutique", "magasin", "commerce", "chiffre", "remise", "devis"),
               "mesures": ("total", "prix", "montant", "quantite", "chiffre", "marge", "remise"),
               "dimensions": ("produit", "categorie", "client", "vendeur", "region", "statut")},
    "logistique": {"libelle": "logistique", "mots": ("livraison", "colis", "transport", "vehicule", "chauffeur", "tournee", "entrepot",
                                                    "expedition", "trajet", "distance", "itineraire", "flotte", "carburant"),
                   "mesures": ("distance", "duree", "poids", "cout", "carburant", "retard"),
                   "dimensions": ("vehicule", "chauffeur", "zone", "statut", "destination")},
    "rh": {"libelle": "ressources humaines", "mots": ("employe", "salarie", "salaire", "personnel", "poste", "conge", "absence", "recrutement",
                                                     "contrat", "paie", "departement", "formation", "agent"),
           "mesures": ("salaire", "anciennete", "heures", "absence", "note", "age"),
           "dimensions": ("departement", "poste", "contrat", "sexe", "statut", "service")},
    "education": {"libelle": _gtfr("engine/fantome_construction.py:95:30"), "mots": ("eleve", "etudiant", "classe", "note", "cours", "ecole", "examen", "matiere", "enseignant",
                                                  "inscription", "bulletin", "moyenne", "promotion", "niveau"),
                  "mesures": ("note", "moyenne", "absence", "age", "heures"),
                  "dimensions": ("classe", "matiere", "niveau", "enseignant", "sexe", "promotion")},
    "agriculture": {"libelle": "agriculture", "mots": ("recolte", "culture", "parcelle", "champ", "semis", "rendement", "betail", "elevage",
                                                     "irrigation", "engrais", "semence", "ferme", "pluie", "saison"),
                    "mesures": ("rendement", "surface", "recolte", "pluie", "poids", "cout"),
                    "dimensions": ("culture", "parcelle", "saison", "espece", "zone")},
    "immobilier": {"libelle": "immobilier", "mots": ("loyer", "logement", "locataire", "bail", "immeuble", "appartement", "propriete",
                                                   "bien", "surface", "charges", "agence"),
                   "mesures": ("loyer", "surface", "charges", "prix", "duree"),
                   "dimensions": ("type", "quartier", "statut", "immeuble")},
    "service": {"libelle": tl("engine/fantome_construction.py:g:103:28"), "mots": ("ticket", "reclamation", "incident", "demande", "support", "satisfaction", "plainte",
                                                     "intervention", "priorite", "resolution"),
                "mesures": ("duree", "delai", "satisfaction", "note", "cout"),
                "dimensions": ("priorite", "statut", "categorie", "agent", "canal")},
}

# Les mots ci-dessus sont les GRAINES françaises ; le lexique multilingue (fantome_lexique.json : en, es, pt, ar, ru, zh…) s'y ajoute. Une langue de
# plus = une entrée dans le JSON, sans toucher au code ; les mots absents des deux sont appris par le réseau (fantome_metier).
DOMAINES_BASE = DOMAINES
DOMAINES = fl.fusionner(DOMAINES_BASE)

# Alternatives équivalentes par RÔLE de colonnes : le choix se fait par Thompson sur les retours de l'utilisateur.
ALTERNATIVES = {
    "repartition": ("camembert", "anneau", "comptage_categoriel"),   # 1 catégorie
    "comparaison": ("barres_verticales", "barres_horizontales", "lollipop"),   # catégorie, mesure
    "evolution": ("courbe", "aire"),   # temps, mesure
    "distribution": ("histogramme", "boite_moustaches"),   # 1 mesure
    "relation": ("nuage_points",),   # 2 mesures
}
NOMS_FORMULES = {"somme": tl("engine/fantome_construction.py:g:117:27"), "moyenne": tl("engine/fantome_construction.py:g:117:47"), "mediane": tl("engine/fantome_construction.py:g:117:69"), "ecart_type": _gtfr("engine/fantome_construction.py:126:204")}
THEMES = ((tl("engine/fantome_construction.py:g:118:12"), ("repartition", "comparaison")), (tl("engine/fantome_construction.py:g:118:64"), ("evolution",)),
          ("Statistiques", ("distribution", "relation", "correlation")))


def _norm(s) -> str:
    """Normalisation Unicode (toutes les écritures : arabe, cyrillique, chinois…) : voir fantome_langue. Le fantôme n'a pas de langue."""
    return fl.normaliser(s)


def _jetons(s) -> list[str]:
    return fl.jetons(s)


def _contient(jetons, mot) -> bool:
    """Un mot-clé correspond à un jeton qui le contient ou le prolonge (pluriel, accord) ; sous-chaîne pour les écritures sans espaces."""
    return fl.contient(jetons, mot)


# ── Observation : profil d'une table ──────────────────────────────────────────────────────────────────────────────────────
def profil_table(fiche: dict) -> dict:
    """Rôle de chaque colonne d'une fiche de table (état fusionné) : temps, mesures, catégories, identifiants."""
    nb = fiche.get("nb_lignes", 0)
    p = {"temps": [], "mesures": [], "categories": [], "ignorees": []}
    for c in fiche.get("champs", []):
        rempli = c.get("rempli") or 0
        if nb < MIN_LIGNES or c.get("remplissage", 0) < REMPLISSAGE_MIN or rempli < MIN_LIGNES:
            p["ignorees"].append(c)
            continue
        t, d = c.get("type"), c.get("distincts") or 0
        est_id = d >= rempli and bool(set(_jetons(c.get("nom"))) & {"id", "numero", "code", "matricule", "reference", "ref", "number", "no", "key", "uuid", "identifier", "numero", "ид", "رقم", "编号"})
        if t in TYPES_DATE:
            p["temps"].append(c)
        elif t in TYPES_NUM and not est_id and (c.get("num") or {}).get("max") != (c.get("num") or {}).get("min"):
            p["mesures"].append(c)
        elif t in TYPES_CATEGORIEL and 2 <= d <= min(12, max(2, rempli // 2)) and not est_id:
            p["categories"].append(c)
        else:
            p["ignorees"].append(c)
    return p


def detecter_domaine(fiche: dict) -> tuple[str, float]:
    """(domaine, confiance 0..1) d'après les noms de la table (poids 2) et de ses colonnes ; ('general', 0) si rien ne ressort."""
    jetons_table = _jetons(fiche.get("nom"))
    jetons_cols = [j for c in fiche.get("champs", []) for j in _jetons(c.get("nom"))]
    meilleur, score_max = "general", 0
    for nom, d in DOMAINES.items():
        score = sum(2 for m in d["mots"] if _contient(jetons_table, m)) + sum(1 for m in d["mots"] if _contient(jetons_cols, m))
        if score > score_max:
            meilleur, score_max = nom, score
    return (meilleur, score_max / (score_max + 3.0)) if score_max else ("general", 0.0)


def _priorite(colonne, mots) -> tuple:
    """Tri : colonnes dont le nom évoque le métier d'abord, puis les mieux remplies."""
    j = _jetons(colonne.get("nom"))
    return (0 if any(_contient(j, m) for m in mots) else 1, -(colonne.get("remplissage") or 0), colonne.get("id") or 0)


# ── Apprentissage : Beta par choix + Thompson ─────────────────────────────────────────────────────────────────────────────
def _beta(registre: dict, cle: str) -> tuple[float, float]:
    b = registre.get("beta", {}).get(cle) or [1.0, 1.0]
    return float(b[0]), float(b[1])


def choisir(registre: dict, action: str, options, rng=None) -> str:
    """Tire un succès plausible par option (loi Beta de ses retours) et prend le meilleur : explore tant que l'on sait peu."""
    rng = rng or random
    return max(options, key=lambda o: rng.betavariate(*_beta(registre, f"{action}:{o}")))


def choisir_nn(registre: dict, action: str, options, x_de, rng=None) -> str:
    """Choix par le RÉSEAU (utilité prédite d'après les statistiques de la table, l'audience et les retours passés) ; les retours Beta
    ne servent plus que de léger bruit d'exploration. `x_de(option)` rend le vecteur du candidat."""
    rng = rng or random
    return max(options, key=lambda o: fa.score(registre, x_de(o)) + 0.1 * rng.betavariate(*_beta(registre, f"{action}:{o}")))


def _incrementer(registre: dict, cle: str, succes: bool):
    a, b = _beta(registre, cle)
    registre.setdefault("beta", {})[cle] = [a + (1 if succes else 0), b + (0 if succes else 1)]


# ── Décision : ce qu'il y a à construire pour UNE table ───────────────────────────────────────────────────────────────────
def planifier_table(fiche: dict, registre: dict, rng=None, audience: dict | None = None, metier: dict | None = None) -> dict | None:
    """Lot à construire pour une table (ou None s'il n'y a rien de nouveau) :
    {"table_id", "domaine", "kpis": [...], "pages": [{"nom", "widgets": [...]}], "table_formules": {...}|None}.
    Les clés déjà dans le registre (construites, retenues, retirées) ne sont jamais reproposées."""
    p = profil_table(fiche)
    if not (p["mesures"] or p["categories"] or p["temps"]):
        return None
    dom, conf = detecter_domaine(fiche)
    if conf < 0.4 and metier:   # table ambiguë : le métier compris par le réseau pour l'ENTREPRISE tranche (métiers mixtes : le plus affine)
        dom_m = max(metier, key=metier.get)
        if metier[dom_m] >= 0.4:
            dom, conf = dom_m, max(conf, metier[dom_m] * 0.6)
    vm = [(metier or {}).get(c, 0.0) for c in DOMAINES]
    d = DOMAINES.get(dom, {"mesures": (), "dimensions": ()})
    deja = {e["cle"] for e in registre.get("elements", [])}
    deja_roles = {e["role_cle"] for e in registre.get("elements", []) if e.get("role_cle")}   # un graphique de ce rôle sur ces colonnes existe/fut retiré
    reste = MAX_ELEMENTS_TABLE - sum(1 for e in registre.get("elements", []) if e.get("table_id") == fiche["id"] and e.get("statut") != "ignore")
    if reste <= 0:
        return None
    tid = fiche["id"]
    mesures = sorted(p["mesures"], key=lambda c: _priorite(c, d["mesures"]))
    cats = sorted(p["categories"], key=lambda c: _priorite(c, d["dimensions"]))
    temps = sorted(p["temps"], key=lambda c: (-(c.get("remplissage") or 0), c.get("id") or 0))
    lot = {"table_id": tid, "table_nom": fiche["nom"], "domaine": dom, "confiance": round(conf, 2), "kpis": [], "pages": [], "table_formules": None, "x": {}}
    audience = audience or {"admin": 1.0}

    def cle(*morceaux):
        return f"t{tid}:" + ":".join(str(m) for m in morceaux)

    def neuf(k):
        return k not in deja

    # KPI : un par mesure principale (somme pour un total, moyenne pour une mesure d'état)
    for m in mesures[:MAX_KPI]:
        formule = "somme" if (m.get("type") == "monnaie" or any(_contient(_jetons(m.get("nom")), w) for w in ("total", "montant", "quantite", "recette", "depense", "revenu", "charge", "chiffre", "amount", "quantity", "revenue", "expense", "income", "cost", "monto", "cantidad", "сумма", "مبلغ", "金额"))) else "moyenne"
        k = cle("kpi", formule, m["id"])
        if neuf(k):
            lot["kpis"].append({"cle": k, "champ_id": m["id"], "champ_nom": m["nom"], "formule": formule})

    def graphique(role, colonnes, extra=""):
        rc = cle("r", role, *[c["id"] for c in colonnes])
        if rc in deja_roles:   # retiré par l'utilisateur (ou déjà là) : on ne propose pas non plus une variante du même graphique
            return None
        def x_de(s):
            return fa.vecteur(role, s, colonnes, audience, vm, fiche.get("comportement"), fiche.get("usage"), None, fiche.get("schema"))
        slug = choisir_nn(registre, "ajouter_graphique", [s for s in ALTERNATIVES[role] if neuf(cle("g", s, *[c["id"] for c in colonnes]))] or [None],
                          lambda s: x_de(s) if s else [0.0] * fa.D, rng)
        if slug is None:
            return None
        lot["x"][cle("g", slug, *[c["id"] for c in colonnes])] = (x_de(slug), fa.a_priori(role, colonnes))
        return {"cle": cle("g", slug, *[c["id"] for c in colonnes]), "role_cle": rc, "role": role, "slug": slug,
                "champs_ids": [c["id"] for c in colonnes], "titre": extra or " × ".join(c["nom"] for c in colonnes)}

    widgets = {"repartition": [], "comparaison": [], "evolution": [], "distribution": [], "relation": [], "correlation": []}
    if cats:
        w = graphique("repartition", [cats[0]], _i18n_tf("engine/fantome_construction.py:g:259:49", v1=cats[0]['nom']))
        if w:
            widgets["repartition"].append(w)
    if cats and mesures:
        w = graphique("comparaison", [cats[0], mesures[0]], f"{mesures[0]['nom']} par {cats[0]['nom']}")
        if w:
            widgets["comparaison"].append(w)
    if temps and mesures:
        for m in mesures[:2]:
            w = graphique("evolution", [temps[0], m], _i18n_tf("engine/fantome_construction.py:g:268:55", v1=m['nom']))
            if w:
                widgets["evolution"].append(w)
    if mesures:
        w = graphique("distribution", [mesures[0]], f"Distribution — {mesures[0]['nom']}")
        if w:
            widgets["distribution"].append(w)
    rc_corr = cle("r", "correlation", *[m["id"] for m in mesures[:6]])
    if len(mesures) >= 3 and neuf(cle("g", "heatmap_correlation", *[m["id"] for m in mesures[:6]])) and rc_corr not in deja_roles:
        ms = mesures[:6]
        lot["x"][cle("g", "heatmap_correlation", *[m["id"] for m in ms])] = (fa.vecteur("correlation", "heatmap_correlation", ms, audience, vm, fiche.get("comportement"), fiche.get("usage"), None, fiche.get("schema")), fa.a_priori("correlation", ms))
        widgets["correlation"].append({"cle": cle("g", "heatmap_correlation", *[m["id"] for m in ms]), "role_cle": rc_corr, "role": "correlation", "slug": "heatmap_correlation",
                                       "champs_ids": [m["id"] for m in ms], "titre": _i18n_t("engine/fantome_construction.py:g:278:86")})
    elif len(mesures) >= 2:
        w = graphique("relation", mesures[:2], f"{mesures[0]['nom']} et {mesures[1]['nom']}")
        if w:
            widgets["relation"].append(w)

    tous = [w for ws in widgets.values() for w in ws]
    gardes = {w["cle"] for w in sorted(tous, key=lambda w: -fa.score(registre, lot["x"][w["cle"]][0]) if w["cle"] in lot["x"] else 0.0)[:MAX_WIDGETS]}
    widgets = {r: [w for w in ws if w["cle"] in gardes or w["cle"] not in lot["x"]] for r, ws in widgets.items()}   # le réseau écarte les moins utiles
    budget = MAX_WIDGETS
    for nom_page, roles in THEMES:
        ws = []
        for r in roles:
            ws += widgets[r]
        ws = ws[:max(0, budget)]
        budget -= len(ws)
        if ws:
            lot["pages"].append({"nom": nom_page, "widgets": ws})
    # Table de formules : synthèse chiffrée des mesures principales + complétude de la colonne la plus utile
    lignes = [{"nom": f"{NOMS_FORMULES[f]} — {m['nom']}", "formule": f, "champ_id": m["id"]}
              for m in mesures[:3] for f in NOMS_FORMULES]
    if lignes and neuf(cle("tf")):
        lot["table_formules"] = {"cle": cle("tf"), "nom": td("engine/fantome_construction.py:g:302:59"), "lignes": lignes[:12]}
    total = (len(lot["kpis"]) + sum(len(pg["widgets"]) for pg in lot["pages"]) + (1 if lot["table_formules"] else 0))
    if total == 0:
        return None
    return lot


def _ignorer(registre: dict, lot: dict, maintenant: float):
    cles = [k["cle"] for k in lot["kpis"]] + [w["cle"] for pg in lot["pages"] for w in pg["widgets"]]
    if lot.get("table_formules"):
        cles.append(lot["table_formules"]["cle"])
    roles = {w["role_cle"]: w["cle"] for pg in lot["pages"] for w in pg["widgets"] if w.get("role_cle")}
    for c in cles:
        registre.setdefault("elements", []).append({"cle": c, "role_cle": next((r for r, k in roles.items() if k == c), None), "action": "rien",
                                                   "choix": "rien", "genre": "rien", "objet_id": 0, "table_id": lot["table_id"],
                                                   "tableau_id": None, "ts": maintenant, "statut": "ignore"})


def table_prete(fiche: dict, maintenant: float) -> bool:
    """Table adulte et calme : assez de lignes, jamais une table de clients, aucune modification récente."""
    if fiche.get("clients") or fiche.get("nosql") or fiche.get("nb_lignes", 0) < MIN_LIGNES:
        return False
    derniere = fiche.get("derniere_ts")
    return derniere is not None and maintenant - derniere >= CALME_S


def _en_attente(registre: dict, maintenant: float) -> int:
    return sum(1 for l in registre.get("lots", []) if maintenant - l["ts"] < RETENU_JOURS * 86400 and l.get("statut") == "construit")


# ── Registre (JSON atomique, verrou de fichier) ───────────────────────────────────────────────────────────────────────────
def _chemin_registre(entreprise) -> str:
    from django.conf import settings
    return str(dossier_systeme("modeles_fantomes", f"construction_{entreprise.id}.json"))


def charger_registre(chemin: str) -> dict:
    try:
        with open(chemin, encoding="utf-8") as f:
            r = json.load(f)
        return r if isinstance(r, dict) else {}
    except (OSError, ValueError):
        return {}


def sauver_registre(chemin: str, registre: dict):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registre, f, ensure_ascii=False)
    os.replace(tmp, chemin)


class _Verrou:
    def __init__(self, chemin):
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        self.f = open(chemin + ".verrou", "a")

    def __enter__(self):
        try:
            import fcntl
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            self.f.close()
            return None
        return self

    def __exit__(self, *a):
        try:
            self.f.close()
        except OSError:
            pass


# ── Retours de l'utilisateur ──────────────────────────────────────────────────────────────────────────────────────────────
def appliquer_retours(registre: dict, existants: dict, tables_presentes: set, maintenant: float) -> int:
    """Met à jour statut et compteurs d'après ce qui existe encore. `existants` = {"tableau": {ids}, "widget": {ids}, "kpi": {ids},
    "table_calcul": {ids}}. Retiré (table encore là) = échec ; gardé RETENU_JOURS = succès ; table disparue = aucun jugement."""
    changes = 0
    for e in registre.get("elements", []):
        if e.get("statut") not in ("construit",):
            continue
        genre, oid = e["genre"], e["objet_id"]
        if oid in existants.get(genre, set()):
            if maintenant - e["ts"] >= RETENU_JOURS * 86400:
                e["statut"] = "retenu"
                _incrementer(registre, f"{e['action']}:{e['choix']}", True)
                if e.get("x"):
                    fa.observer(registre, e["x"], 1.0, 1.0)
                changes += 1
            continue
        if e["table_id"] not in tables_presentes:
            e["statut"] = "table_disparue"
        elif genre == "widget" and e.get("tableau_id") is not None and e["tableau_id"] not in existants.get("tableau", set()):
            e["statut"] = "page_retiree"   # la page entière a été retirée : jugée sur la page, pas sur chaque graphique
        else:
            e["statut"] = "retire"
            _incrementer(registre, f"{e['action']}:{e['choix']}", False)
            if e.get("x"):
                fa.observer(registre, e["x"], 0.0, 1.0)
        changes += 1
    for l in registre.get("lots", []):
        if l.get("statut") == "construit" and maintenant - l["ts"] >= RETENU_JOURS * 86400:
            l["statut"] = "retenu"
    return changes


# ── Construction (seule partie qui écrit : lignes de modèles, rien d'autre) ───────────────────────────────────────────────
def _placer(widgets_existants, largeur, hauteur, curseur):
    """Case suivante d'une grille de 12 colonnes, en bas de l'existant : (x, y) ; `curseur` = [x, y, hauteur_de_ligne]."""
    if curseur[0] + largeur > 12:
        curseur[0], curseur[1], curseur[2] = 0, curseur[1] + curseur[2], 0
    x, y = curseur[0], curseur[1]
    curseur[0] += largeur
    curseur[2] = max(curseur[2], hauteur)
    return x, y


def _curseur_bas(tableau):
    bas = 0
    for w in tableau.widgets.all():
        bas = max(bas, w.position_y + w.hauteur)
    return [0, bas, 0]


def construire(entreprise, lot: dict, registre: dict, maintenant: float) -> dict:
    """Exécute un lot par les SEULES actions propres au modèle. Rend le bilan {kpis, graphiques, pages, tables_formules}."""
    from .models import (ChampDynamique, FormuleCalcul, GraphiqueBI, IndicateurKPI, LigneCalcul, TableCalcul, TableDynamique,
                         TableauBordBI, WidgetGraphiqueBI)
    table = TableDynamique.objects.filter(id=lot["table_id"], entreprise=entreprise).first()
    if table is None:
        return {}
    champs = {c.id: c for c in ChampDynamique.objects.filter(table=table)}
    formules = {f.slug: f for f in FormuleCalcul.objects.filter(entreprise__isnull=True, cible_type="colonne")}
    graphiques = {g.slug: g for g in GraphiqueBI.objects.filter(entreprise__isnull=True)}
    bilan = {"kpis": 0, "graphiques": 0, "pages": 0, "tables_formules": 0}

    def noter(action, choix, genre, objet_id, cle, tableau_id=None, role_cle=None):
        registre.setdefault("elements", []).append({"cle": cle, "role_cle": role_cle, "action": action, "choix": choix, "genre": genre,
                                                   "objet_id": objet_id, "table_id": table.id, "tableau_id": tableau_id, "ts": maintenant,
                                                   "statut": "construit", "x": (lot.get("x", {}).get(cle) or [None])[0]})

    # 1) KPI (formules d'agrégation prédéfinies sur une colonne numérique)
    kpis = {}
    deja_kpi = {(k.champ_cible_id, k.formule.slug) for k in IndicateurKPI.objects.filter(table_source_id__in=_ids_groupe_fc(table)).select_related("formule")}
    for k in lot["kpis"]:
        f, c = formules.get(k["formule"]), champs.get(k["champ_id"])
        if f is None or c is None or (c.id, f.slug) in deja_kpi or not f.compatible_avec_type_champ(c.type_champ):
            continue
        obj = IndicateurKPI.objects.create(entreprise=entreprise, table_source=_ancre_fc(table), nom=f"{f.nom} — {c.nom}", champ_cible=c, formule=f,
                                           ordre=IndicateurKPI.objects.filter(table_source_id__in=_ids_groupe_fc(table)).count())
        kpis[k["cle"]] = obj
        noter("ajouter_kpi", k["formule"], "kpi", obj.id, k["cle"])
        lot.setdefault("details", []).append({"genre": "kpi", "titre": obj.nom, "formule": k["formule"], "colonnes": [c.nom]})   # pour le message clair
        bilan["kpis"] += 1

    # 2) Pages : sur un tableau de bord existant de la table (en bas) ou, sinon, une page par thème
    existants = list(TableauBordBI.objects.filter(entreprise=entreprise, table_source_id__in=_ids_groupe_fc(table)).order_by("ordre", "id"))
    pages = []   # (tableau, curseur, widgets, créé_par_le_modèle)
    elements_pages = lot["pages"]
    if existants:
        pages.append((existants[0], _curseur_bas(existants[0]), [w for pg in elements_pages for w in pg["widgets"]], False))
    else:
        for pg in elements_pages:
            tb = TableauBordBI.objects.create(entreprise=entreprise, table_source=_ancre_fc(table), nom=f"{pg['nom']} — {nom_vue(table)}", ordre=len(existants) + len(pages))
            noter("creer_tableau_bord", pg["nom"], "tableau", tb.id, _gtf("engine/fantome_construction.py:475:70", v1=(table.id), v2=(pg['nom'])))
            bilan["pages"] += 1
            pages.append((tb, [0, 0, 0], pg["widgets"], True))
    if pages and kpis:   # les KPI vont sur la première page, en tête
        tb, curseur, ws, cree = pages[0]
        deja_w = set(tb.widgets.filter(type_widget="kpi").values_list("kpi_id", flat=True))
        for cle_k, obj in kpis.items():
            if obj.id in deja_w:
                continue
            x, y = _placer(None, 3, 2, curseur)
            w = WidgetGraphiqueBI.objects.create(tableau_bord=tb, type_widget="kpi", kpi=obj, position_x=x, position_y=y, largeur=3, hauteur=2,
                                                 ordre_z=tb.widgets.count(), statut="en_attente")
            noter("ajouter_kpi", "widget", "widget", w.id, cle_k + ":w", tb.id)
    for tb, curseur, ws, cree in pages:
        deja_g = {(w.graphique.slug, tuple(sorted(c.id for c in w.champs_cibles.all())))
                  for w in tb.widgets.filter(type_widget="graphique").select_related("graphique").prefetch_related("champs_cibles")}
        for w in ws:
            g = graphiques.get(w["slug"])
            ids = [i for i in w["champs_ids"] if i in champs]
            if g is None or len(ids) < g.nombre_colonnes_min or (g.nombre_colonnes_max and len(ids) > g.nombre_colonnes_max):
                continue
            types = g.liste_types_compatibles
            if types and any(champs[i].type_champ not in types for i in ids):
                continue   # jamais un graphique sur un type de colonne qu'il ne sait pas dessiner
            if (g.slug, tuple(sorted(ids))) in deja_g:
                continue   # l'utilisateur l'a déjà
            x, y = _placer(None, 4, 3, curseur)
            obj = WidgetGraphiqueBI.objects.create(tableau_bord=tb, type_widget="graphique", graphique=g, titre=w["titre"][:200], description=(w.get("description") or g.description or "")[:2000], position_x=x,
                                                   position_y=y, largeur=4, hauteur=3, ordre_z=tb.widgets.count(), ordre_champs=ids,
                                                   statut="en_attente")
            obj.champs_cibles.set([champs[i] for i in ids])
            noter("ajouter_graphique", w["slug"], "widget", obj.id, w["cle"], tb.id, w.get("role_cle"))
            lot.setdefault("details", []).append({"genre": "graphique", "titre": obj.titre, "role": w.get("role"), "page": tb.nom,
                                                  "colonnes": [champs[i].nom for i in ids]})
            bilan["graphiques"] += 1

    # 3) Table de formules (formules prédéfinies, une ligne par calcul ; résultats calculés à l'ouverture, comme pour l'utilisateur)
    tf = lot.get("table_formules")
    if tf:
        formules_tout = {f.slug: f for f in FormuleCalcul.objects.filter(entreprise__isnull=True)}
        tc = TableCalcul.objects.create(entreprise=entreprise, table_source=table, nom=tf["nom"], ordre=table.tables_calcul.count())
        n = 0
        for i, l in enumerate(tf["lignes"]):
            f, c = formules_tout.get(l["formule"]), champs.get(l["champ_id"])
            if f is None or c is None or f.cible_type != "colonne" or not f.compatible_avec_type_champ(c.type_champ):
                continue
            LigneCalcul.objects.create(table_calcul=tc, nom=l["nom"][:150], formule=f, type_cible="champ", champ_cible=c, ordre=i)
            n += 1
        noter("creer_table_formules", "synthese", "table_calcul", tc.id, tf["cle"])
        lot.setdefault("details", []).append({"genre": "table_calcul", "titre": tc.nom, "nb": n})
        bilan["tables_formules"] = 1 if n else 0
    return bilan


def recommander(entreprise, lot: dict, bilan: dict):
    """Communique par recommandation, en termes simples (fantome_message) : chaque nouveauté est nommée, expliquée en une phrase et située.
    Adressée aux agents/administrateurs qui travaillent réellement sur la table (d'après l'audit) + aux administrateurs ; à tous si personne
    n'y a encore travaillé. Sans relais SMS : ce n'est pas urgent. Jamais de mention du système : c'est une suggestion préparée d'après les données."""
    from . import fantome_message as fmsg
    from .models import Notification, TableDynamique
    if not sum(bilan.values()):
        return 0
    table = TableDynamique.objects.filter(id=lot["table_id"], entreprise=entreprise).first()
    actifs, membres = fmsg.membres_actifs(entreprise, lot["table_id"])
    ids_actifs = {m.id for m in actifs}
    n = 0
    for m in membres:
        if actifs and m.id not in ids_actifs and m.role != "admin":
            continue   # un agent qui ne touche pas à cette table n'est pas noyé de propositions
        titre, message = fmsg.composer(lot["table_nom"], lot.get("details", []), m.id in ids_actifs)
        from .notifications import pousser_notification
        pousser_notification(Notification.objects.create(entreprise=entreprise, destinataire=m, type_notification="recommandation", titre=titre, message=message, table=table))
        n += 1
    return n


def _existants(entreprise) -> tuple[dict, set]:
    from .models import IndicateurKPI, PageOntologie, TableCalcul, TableDynamique, TableauBordBI, WidgetGraphiqueBI
    return ({"tableau": set(TableauBordBI.objects.filter(entreprise=entreprise).values_list("id", flat=True)),
             "widget": set(WidgetGraphiqueBI.objects.filter(tableau_bord__entreprise=entreprise).values_list("id", flat=True)),
             "kpi": set(IndicateurKPI.objects.filter(entreprise=entreprise).values_list("id", flat=True)),
             "table_calcul": set(TableCalcul.objects.filter(entreprise=entreprise).values_list("id", flat=True)),
             "table": set(TableDynamique.objects.filter(entreprise=entreprise).values_list("id", flat=True)),
             "page_ontologie": set(PageOntologie.objects.filter(entreprise=entreprise).values_list("id", flat=True))},
            set(TableDynamique.objects.filter(entreprise=entreprise).values_list("id", flat=True)))


def observer_habitudes(entreprise, registre: dict):
    """A priori Beta : ce que l'utilisateur utilise DÉJÀ (hors éléments du modèle) compte comme succès. Recalculé à chaque passage
    à partir d'un compteur absolu (registre["habitudes"]) pour ne jamais compter deux fois."""
    from .models import WidgetGraphiqueBI
    miens = {e["objet_id"] for e in registre.get("elements", []) if e["genre"] == "widget"}
    compte = {}
    for wid, slug in WidgetGraphiqueBI.objects.filter(tableau_bord__entreprise=entreprise, type_widget="graphique").values_list("id", "graphique__slug"):
        if wid not in miens and slug:
            compte[slug] = compte.get(slug, 0) + 1
    vus = registre.setdefault("habitudes", {})
    for slug, n in compte.items():
        if n > vus.get(slug, 0):
            a, b = _beta(registre, f"ajouter_graphique:{slug}")
            registre.setdefault("beta", {})[f"ajouter_graphique:{slug}"] = [a + (n - vus.get(slug, 0)), b]
            vus[slug] = n


def passer(entreprise, etat: dict, rng=None, maintenant: float | None = None) -> dict:
    """Un passage (appelé par memoire_decision.entretenir) : retours de l'utilisateur, habitudes, puis AU PLUS UN lot sur une table
    calme. Jamais bloquant pour l'appelant (qui journalise les erreurs). Rend un petit bilan."""
    bilan = {"construit": 0, "retours": 0}
    if os.environ.get("GYPLATFORM_FANTOME_CONSTRUIT", "1") == "0":
        return bilan
    maintenant = maintenant if maintenant is not None else time.time()
    chemin = _chemin_registre(entreprise)
    with _Verrou(chemin) as verrou:
        if verrou is None:
            return bilan
        registre = charger_registre(chemin)
        existants, tables_presentes = _existants(entreprise)
        try:   # propositions géo / tâches / documents : jugées comme le reste (gardé = succès, retiré = échec) ; en cas d'erreur, rien n'est jugé retiré
            from . import fantome_propositions as fp
            for g, ids in fp.existants(entreprise, registre).items():
                existants[g] = ids
        except Exception:
            logger.exception(_i18n_t("engine/fantome_construction.py:g:587:30"), entreprise.id)
            for g in ("annotation_geo", "tache", "document", "carte_rapport"):
                existants[g] = {e["objet_id"] for e in registre.get("elements", []) if e.get("genre") == g}
        bilan["retours"] = appliquer_retours(registre, existants, tables_presentes, maintenant)
        observer_habitudes(entreprise, registre)
        try:
            audience = fa.audiences(entreprise)
        except Exception:
            audience = None
        try:   # comprendre le métier du client (tout le vocabulaire de la plateforme) ; jamais bloquant
            from . import fantome_metier as fm
            voc = fm.vocabulaire(entreprise)
            bilan["metier"] = fm.affiner(registre, etat, voc)
            metier = fm.affinites(registre, etat, voc)
            registre["metier"] = {k: round(v, 3) for k, v in metier.items()}
        except Exception:
            logger.exception(_i18n_t("engine/fantome_construction.py:g:603:30"), entreprise.id)
            metier = None
        try:   # comportement RÉEL (audit) : où les gens travaillent ; simples entrées du réseau, aucune règle ; jamais bloquant
            from . import fantome_comportement as fcomp
            reel = fcomp.observer(entreprise)
            for f in etat.get("tables", []):
                f["comportement"] = reel.get(f.get("id"), [0.0] * fcomp.NB_COMPORTEMENT)
            from . import fantome_usage as fu
            usage, usage_tables = fu.observer(entreprise)   # où l'entreprise (et chaque table) passe son temps : entrées du réseau, comme le reste
            etat["usage"] = usage
            for f in etat.get("tables", []):
                f["usage"] = usage_tables.get(f.get("id"), usage)
        except Exception:
            logger.exception(_i18n_t("engine/fantome_construction.py:g:616:30"), entreprise.id)
        try:   # structure COMPLÈTE de la plateforme (toutes les familles de tables) + signaux de l'argent (TVA, wallets) : entrées du réseau, jamais bloquant
            from . import fantome_schema as fs
            schema, _ = fs.observer(entreprise)
            etat["schema"] = schema
            for f in etat.get("tables", []):
                f["schema"] = schema
        except Exception:
            logger.exception("fantome_schema entreprise=%s", entreprise.id)
        if _en_attente(registre, maintenant) < MAX_EN_ATTENTE:
            for fiche in sorted(etat.get("tables", []), key=lambda t: (-(t.get("comportement") or [0.0])[0], -t.get("nb_lignes", 0))):   # d'abord là où l'on travaille vraiment
                if not table_prete(fiche, maintenant):
                    continue
                lot = planifier_table(fiche, registre, rng, audience, metier)
                if lot is None:
                    continue
                from django.db import transaction
                n0 = len(registre.get("elements", []))
                try:
                    with transaction.atomic():   # un lot est tout ou rien : jamais une page à moitié construite
                        res = construire(entreprise, lot, registre, maintenant)
                except Exception:
                    logger.exception(_i18n_t("engine/fantome_construction.py:g:630:38"), entreprise.id, fiche["id"])
                    del registre["elements"][n0:]   # annulé en base : rien ne doit rester au registre
                    res = {}
                if sum(res.values()) == 0:
                    _ignorer(registre, lot, maintenant)   # rien de neuf (déjà fait par l'utilisateur) ou échec : on ne le replanifie pas
                    continue
                registre.setdefault("lots", []).append({"table_id": fiche["id"], "ts": maintenant, "statut": "construit", "domaine": lot["domaine"]})
                for x, prior in lot.get("x", {}).values():   # résultat statistique = étiquette faible, avant tout jugement de l'utilisateur
                    fa.observer(registre, x, prior, 0.3)
                recommander(entreprise, lot, res)
                bilan["construit"] = sum(res.values())
                break   # un seul lot par passage
        if not bilan["construit"]:   # aucun lot BI : une proposition dans les onglets Données / Ontologie, puis carte / tâches / documents
            try:
                from . import fantome_onglets as fo
                bilan["propose"] = fo.passer(entreprise, etat, registre, audience, metier, maintenant)["propose"]
            except Exception:
                logger.exception(_i18n_t("engine/fantome_construction.py:g:647:34"), entreprise.id)
            if not bilan.get("propose"):
                try:
                    from . import fantome_propositions as fp
                    bilan["propose"] = fp.passer(entreprise, etat, registre, audience, metier, maintenant)["propose"]
                except Exception:
                    logger.exception(_i18n_t("engine/fantome_construction.py:g:653:38"), entreprise.id)
        bilan["perte"] = fa.affiner(registre)   # fine-tuning paresseux du réseau, à chaque passage
        sauver_registre(chemin, registre)
    return bilan


def resume(entreprise) -> dict:
    """Pour la commande de maintenance (jamais exposé à l'interface)."""
    r = charger_registre(_chemin_registre(entreprise))
    statuts = {}
    for e in r.get("elements", []):
        statuts[e["statut"]] = statuts.get(e["statut"], 0) + 1
    return {"elements": statuts, "lots": len(r.get("lots", [])), "apprentissage": r.get("beta", {})}
