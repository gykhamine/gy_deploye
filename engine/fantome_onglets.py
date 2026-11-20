"""PROPOSITIONS DU FANTÔME DANS LES ONGLETS : il construit, l'utilisateur garde ou supprime. Il ne supprime jamais, n'exécute rien.

Complète fantome_construction.py (Dashboard/KPI, Calcul, BI — donc aussi Stats, Séries et Géo dont les graphiques sont des widgets BI) :
  * DONNÉES  — « creer_table_referentiel » : une colonne catégorielle (Produit, Classe, Service…) sans table du même nom devient la
               proposition d'une TABLE VIDE (structure seule : Nom, Description) ; aucune ligne, la colonne source n'est pas touchée ;
  * ONTOLOGIE — « creer_page_ontologie » : pour chaque métier compris du client (fantome_metier, métiers mixtes = plusieurs pages), une
               page d'ontologie (mode manuel) qui réunit ses tables ; les nœuds restent ceux des tables, rien n'est inventé.
Non constructibles sans exécuter de code ni écrire dans les données : Documents (génération), Entraînement IA (lance un apprentissage),
Blockchain (journal en ajout seul), Tâches (scripts). Le fantôme n'y touche pas.
Même mécanique que le reste : liste blanche, clés jamais rejouées, retours (gardé 3 jours = succès, retiré = échec et JAMAIS reconstruit),
choix par le réseau (statistiques + audience + métier), une proposition par passage, notification « recommandation » sans mention du modèle.
Les décisions sont PURES (propositions) ; seules construire_proposition/passer importent Django. Désactivable : GYPLATFORM_FANTOME_CONSTRUIT=0.
"""
from __future__ import annotations
from engine.i18n import t, tf

import logging

from . import fantome_analyse as fa
from . import fantome_construction as fc
from . import fantome_langue as fl

logger = logging.getLogger(__name__)
ACTIONS = ("creer_table_referentiel", "creer_page_ontologie")   # LISTE BLANCHE
SEUIL_METIER, MIN_TABLES_PAGE = 0.5, 2


def _singulier(n: str) -> str:
    return n[:-1] if n.endswith("s") and len(n) > 3 else n


def propositions(etat: dict, registre: dict, maintenant: float, audience: dict, metier: dict | None) -> list[dict]:
    """Candidats triés (le meilleur d'abord) : [{"cle","action","table_id","x","prior","score",...}]. Les clés déjà au registre
    (construites, retenues, retirées, ignorées) ne reviennent jamais."""
    deja = {e["cle"] for e in registre.get("elements", [])}
    fiches = [f for f in etat.get("tables", []) if not f.get("clients") and not f.get("nosql")]
    noms = {_singulier(fc._norm(f.get("nom")).replace(" ", "")) for f in etat.get("tables", [])}
    vm = [(metier or {}).get(c, 0.0) for c in fc.DOMAINES]
    sortie = []
    for f in fiches:   # DONNÉES : colonne catégorielle sans table de référence
        if not fc.table_prete(f, maintenant):
            continue
        for c in fc.profil_table(f)["categories"]:
            cle = f"t{f['id']}:ref:{c['id']}"
            if cle in deja or _singulier(fc._norm(c.get("nom")).replace(" ", "")) in noms:
                continue
            x = fa.vecteur("referentiel", "", [c], audience, vm, f.get("comportement"), f.get("usage"), None, f.get("schema"))
            sortie.append({"cle": cle, "action": "creer_table_referentiel", "table_id": f["id"], "nom": str(c["nom"]).strip()[:100],
                           "source": f["nom"], "x": x, "prior": fa.a_priori("referentiel", [c])})
    par_dom = {}
    for f in fiches:   # ONTOLOGIE : une page par métier compris (affinité du réseau) qui réunit les tables de ce métier
        dom, conf = fc.detecter_domaine(f)
        if dom in fc.DOMAINES and conf >= 0.2:
            par_dom.setdefault(dom, []).append(f)
    for dom, fs in par_dom.items():
        cle = f"onto:{dom}"
        if cle in deja or len(fs) < MIN_TABLES_PAGE or (metier or {}).get(dom, 0.0) < SEUIL_METIER:
            continue
        sortie.append({"cle": cle, "action": "creer_page_ontologie", "table_id": fs[0]["id"], "nom": f"Ontologie — {fl.libelle(fc.DOMAINES[dom], (registre.get('langue') or {}).get('langue'))}",
                       "tables_ids": [f["id"] for f in fs], "x": fa.vecteur("ontologie", "", [], audience, vm, None, None, None, fs[0].get("schema")), "prior": min(1.0, (metier or {})[dom])})
    for p in sortie:
        p["score"] = 0.5 * fa.score(registre, p["x"]) + 0.5 * p["prior"]
    return sorted(sortie, key=lambda p: -p["score"])


def construire_proposition(entreprise, p: dict, registre: dict, maintenant: float) -> dict:
    """Crée UNE proposition (lignes de modèles seulement). Rend {\"tables\": n, \"pages_ontologie\": n}."""
    from django.utils.text import slugify
    from .models import ChampDynamique, PageOntologie, TableDynamique
    bilan = {"tables": 0, "pages_ontologie": 0}
    slug = slugify(p["nom"])[:50] or "proposition"
    if p["action"] == "creer_table_referentiel":
        if TableDynamique.objects.filter(entreprise=entreprise, slug=slug).exists():
            return bilan   # l'utilisateur (ou une autre table) l'a déjà : rien à proposer
        obj = TableDynamique.objects.create(entreprise=entreprise, nom=p["nom"], slug=slug,
                                            description=tf("engine/fantome_onglets.py:g:74:57", v1=p['nom'], v2=p['source']))
        for i, (nom, type_champ) in enumerate(((t("engine/fantome_onglets.py:g:75:49"), "texte"), ("Description", "texte_long"))):
            ChampDynamique.objects.create(table=obj, nom=nom, slug=slugify(nom), type_champ=type_champ, obligatoire=False, ordre=i)
        genre, bilan["tables"] = "table", 1
    else:
        if PageOntologie.objects.filter(entreprise=entreprise, slug=slug).exists():
            return bilan
        obj = PageOntologie.objects.create(entreprise=entreprise, nom=p["nom"], slug=slug, mode="manuel", visible_client=False,
                                           description=t("engine/fantome_onglets.py:g:82:56"),
                                           ordre=PageOntologie.objects.filter(entreprise=entreprise).count())
        obj.tables_manuelles.set(TableDynamique.objects.filter(entreprise=entreprise, id__in=p["tables_ids"]))
        genre, bilan["pages_ontologie"] = "page_ontologie", 1
    registre.setdefault("elements", []).append({"cle": p["cle"], "role_cle": None, "action": p["action"], "choix": "proposition", "genre": genre,
                                                "objet_id": obj.id, "table_id": p["table_id"], "tableau_id": None, "ts": maintenant,
                                                "statut": "construit", "x": p["x"]})   # table_id = table SOURCE (qui existe encore) : un retrait est jugé
    return bilan


def notifier(entreprise, p: dict) -> int:
    """Recommandation (agents et administrateurs, jamais les clients), sans mention de l'automatisme ni relais SMS."""
    from .models import MembreEntreprise, Notification, TableDynamique
    table = TableDynamique.objects.filter(id=p["table_id"], entreprise=entreprise).first()
    texte = (tf("engine/fantome_onglets.py:g:96:14", v1=p['nom'], v2=p['source'])
             if p["action"] == "creer_table_referentiel" else
             tf("engine/fantome_onglets.py:g:98:14", v1=p['nom']))
    n = 0
    for m in MembreEntreprise.objects.filter(entreprise=entreprise, role__in=("agent", "admin")):
        from .notifications import pousser_notification
        pousser_notification(Notification.objects.create(entreprise=entreprise, destinataire=m, type_notification="recommandation", titre=f"Proposition : {p['nom']}",
                                                         message=texte + t("engine/fantome_onglets.py:g:102:53"), table=table))
        n += 1
    return n


def passer(entreprise, etat: dict, registre: dict, audience: dict, metier: dict | None, maintenant: float) -> dict:
    """UNE proposition au plus (tout ou rien). Appelé par fantome_construction.passer quand aucun lot BI n'a été construit."""
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
            logger.exception(t("engine/fantome_onglets.py:g:120:30"), entreprise.id, p["cle"])
            del registre.get("elements", [])[n0:]
            res = {}
        if not sum(res.values()):   # déjà là ou échec : mémorisé comme ignoré, jamais rejoué
            registre.setdefault("elements", []).append({"cle": p["cle"], "role_cle": None, "action": "rien", "choix": "rien", "genre": "rien", "objet_id": 0,
                                                        "table_id": p["table_id"], "tableau_id": None, "ts": maintenant, "statut": "ignore"})
            continue
        fa.observer(registre, p["x"], p["prior"], 0.3)   # résultat statistique = étiquette faible avant tout jugement de l'utilisateur
        registre.setdefault("lots", []).append({"table_id": p["table_id"], "ts": maintenant, "statut": "construit", "domaine": p["action"]})
        notifier(entreprise, p)
        bilan["propose"] = 1
        break
    return bilan
