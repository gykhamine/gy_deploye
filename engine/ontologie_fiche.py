"""Fiche objet consolidée (cahier des charges Ontology, section 10 —
« Applications object-centric » / Phase 6-10 des sessions précédentes) :
une application ne devrait pas être obligée d'assembler elle-même
propriétés/relations/actions/documents pour un objet quand l'Ontology peut
les fournir en un seul appel.

Extrait de views.api_ontologie_fiche (2026-09-26) pour être appelable aussi
bien depuis la vue live (JSON, onglet "Fiche" de l'ontologie) que depuis le
PDF "Résumé général" (reports.generer_pdf_resume_general) — un seul point
d'assemblage, pas de logique dupliquée entre les deux interfaces.

N'introduit aucun nouveau moteur : assemble des briques déjà en place
(aucune n'est modifiée) — voir assembler_fiche_objet() ci-dessous pour le
détail de chaque brique.
"""
from __future__ import annotations
from engine.i18n import t

from . import actions
from . import fonctions
from . import ontologie as _onto
from .models import DocumentGenere, Enregistrement, JournalAudit, MembreEntreprise, PageOntologie, TableDynamique
from .ontologie_requetes import _noeud_dict, explorer

# ── Enrichissement optionnel (point 2, lot 3) ──────────────────────────────
# construire_graphe() ne porte, pour "agent"/"admin"/"client"/"kpi"/
# "dashboard", que le strict nécessaire au graphe lui-même (label, quelques
# scalaires) — enrichir CE nœud directement dans construire_graphe pénaliserait
# TOUS ses appelants (page Ontologie complète, PDF, lignée...) pour un besoin
# qui ne concerne que la fiche/liste d'UN objet ou d'une PAGE d'objets. Cette
# hydratation reste donc ICI, bornée aux seuls ids déjà résolus par le graphe
# (jamais une requête supplémentaire sur toute la table), et strictement
# additive : sans enrichir=True (assembler_liste_objets) ou pour tout type non
# listé ci-dessous, comportement inchangé (seuls les attrs du graphe).
def _enrichir_membres(id_bruts: list[str], entreprise) -> dict[str, dict]:
    membres = (
        MembreEntreprise.objects.filter(id__in=id_bruts, entreprise=entreprise)   # ISOLATION : jamais le membre d'une autre entreprise
        .select_related("utilisateur")
    )
    return {
        str(m.id): {
            "email": m.utilisateur.email, "telephone": m.telephone,
            "indicatif_pays": m.indicatif_pays, "role": m.role,
            "role_display": m.get_role_display(), "vues_autorisees": m.vues_autorisees,
            "est_actif": m.utilisateur.is_active,
            "date_rejoindre": m.utilisateur.date_joined.isoformat() if m.utilisateur.date_joined else None,
        }
        for m in membres
    }


def _enrichir_kpis(id_bruts: list[str], entreprise) -> dict[str, dict]:
    from .models import IndicateurKPI
    kpis = IndicateurKPI.objects.filter(id__in=id_bruts, entreprise=entreprise).select_related("table_source")
    return {
        str(k.id): {
            "description": k.description, "sens": k.sens_objectif, "statut": k.statut,
            "table_source": k.table_source.nom if k.table_source_id else None,
        }
        for k in kpis
    }


def _enrichir_dashboards(id_bruts: list[str], entreprise) -> dict[str, dict]:
    from .models import TableauBordBI
    tbs = TableauBordBI.objects.filter(id__in=id_bruts, entreprise=entreprise).select_related("table_source")
    return {
        str(tb.id): {
            "description": tb.description,
            "table_source": tb.table_source.nom if tb.table_source_id else None,
        }
        for tb in tbs
    }


_ENRICHISSEURS = {
    "agent": _enrichir_membres, "admin": _enrichir_membres, "client": _enrichir_membres,
    "kpi": _enrichir_kpis, "dashboard": _enrichir_dashboards,
}


def _enrichir_objets(type_noeud: str, objets: list[dict], entreprise) -> list[dict]:
    """Complète en place chaque dict de `objets` (déjà produits par
    _noeud_dict) avec les champs réels du modèle sous-jacent, un seul appel
    groupé par type — jamais un aller-retour DB par objet. Type non couvert
    (ou id non numérique) : objets renvoyés inchangés."""
    enrichisseur = _ENRICHISSEURS.get(type_noeud)
    if enrichisseur is None or not objets:
        return objets
    id_bruts = [o["id"].partition(":")[2] for o in objets if o["id"].partition(":")[2].isdigit()]
    if not id_bruts:
        return objets
    par_id = enrichisseur(id_bruts, entreprise)
    for o in objets:
        supplement = par_id.get(o["id"].partition(":")[2])
        if supplement:
            o.update(supplement)
    return objets


def assainir_json(valeur):
    """Rend `valeur` strictement JSON-valide : NaN/±inf -> None (JSON.parse du navigateur les refuse, alors que
    Python les écrit tels quels), textes paresseux/_TexteDonnee -> str, tuples/sets -> listes."""
    import math
    if isinstance(valeur, float):
        return valeur if math.isfinite(valeur) else None
    if isinstance(valeur, dict):
        return {(k if isinstance(k, (str, int, float, bool)) or k is None else str(k)): assainir_json(v)
                for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple, set, frozenset)):
        return [assainir_json(v) for v in valeur]
    if valeur is None or isinstance(valeur, (str, int, bool)):
        return valeur
    from django.utils.functional import Promise
    if isinstance(valeur, Promise) or type(valeur).__name__ in ("_TexteDonnee", "TexteDonnee"):
        return str(valeur)
    import datetime, decimal, uuid
    if isinstance(valeur, (datetime.datetime, datetime.date, datetime.time)):
        return valeur.isoformat()
    if isinstance(valeur, (decimal.Decimal, uuid.UUID)):
        return str(valeur)
    if isinstance(valeur, (bytes, bytearray)):
        return valeur.decode("utf-8", "replace")
    item = getattr(valeur, "item", None)   # scalaires numpy (int64...) : non sérialisables par json
    if callable(item):
        try:
            return assainir_json(item())
        except Exception:
            pass
    # Dernier filet : jamais de « TypeError: Object of type X is not JSON serializable » (HTTP 500) sur la fiche.
    return str(valeur)


def _ajouter_ligne_hors_limite(graphe, entreprise, noeud_id, membre):
    """Le graphe ne garde que les N lignes les plus récentes de chaque table : la fiche d'une ligne plus ancienne (bouton de la
    grille) n'y figure pas et répondait 404. On l'y ajoute à la demande, avec les droits de la grille (client : jamais ; agent : ses lignes)."""
    id_brut = noeud_id.partition(":")[2]
    if not id_brut.isdigit() or (membre is not None and membre.role == "lecteur"):
        return
    e = Enregistrement.objects.filter(id=id_brut, table__entreprise=entreprise).first()
    if e is None or not graphe.has_node(f"table:{e.table_id}"):
        return
    if membre is not None and membre.role == "agent" and e.cree_par_id != membre.utilisateur_id:
        return
    premiere = next((str(v) for v in e.to_dict().values() if v not in (None, "")), None)
    graphe.add_node(noeud_id, type="enregistrement", label=e.matricule or premiere or f"#{e.id}", taille=6)
    graphe.add_edge(f"table:{e.table_id}", noeud_id, relation="contient")


def assembler_fiche_objet(entreprise, noeud_id: str, membre=None, page_id=None) -> dict:
    """Retourne {objet, relations, actions_disponibles, documents,
    activite_recente} pour `noeud_id` (ex. "table:5", "enregistrement:12"),
    ou {"erreur": ...} si le nœud est introuvable dans le graphe de cette
    entreprise (page_id optionnel, comme l'onglet Ontologie).

    membre=None (ex. génération PDF hors requête utilisateur, voir
    cycle.py::cloture_cycle_entreprise) : actions_disponibles vide, même
    convention que la vue live quand aucun membre ne correspond.

    Limite restante (documentée, pas cachée) : documents/activite_recente
    ne sont résolus que pour les nœuds "table:", "enregistrement:",
    "entreprise:" et "agent:"/"admin:"/"client:" — les autres types
    (config_ia, calcul, skill, modele_ia, dashboard, kpi, tokens...)
    renvoient des listes vides, faute de lien direct vers
    DocumentGenere/JournalAudit sur ces modèles."""
    page = None
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()

    if page is not None and page.mode == "manuel":
        graphe = _onto.construire_graphe_manuel(page, entreprise)
    else:
        graphe = _onto.construire_graphe(entreprise, page=page)
    graphe = _onto.restreindre_pour_client(graphe, entreprise, membre)

    if noeud_id not in graphe and noeud_id.startswith("enregistrement:"):
        _ajouter_ligne_hors_limite(graphe, entreprise, noeud_id, membre)
    if noeud_id.startswith("cellule:"):
        # Une cellule est un nœud (ontologie_cellules.py) : sa fiche porte sa valeur, sa colonne, sa table, sa ligne et les cellules de même valeur.
        from . import ontologie_cellules as _cell
        lectures = None
        try:
            from .views_ontologie_apercu import _lectures_tables
            lectures = list(_lectures_tables(graphe, entreprise))
        except Exception:
            pass
        graphe = _cell.sous_graphe_cellule(graphe, entreprise, noeud_id, lectures)
    if noeud_id not in graphe:
        return {"erreur": t("engine/ontologie_fiche.py:126:28")}

    objet = _noeud_dict(noeud_id, dict(graphe.nodes[noeud_id]))
    relations = explorer(graphe, noeud_id, {"mode": "voisins", "profondeur": 1})
    type_noeud, _, id_brut = noeud_id.partition(":")
    _enrichir_objets(type_noeud, [objet], entreprise)
    actions_disponibles = (
        actions.catalogue_pour_agent(membre, list(actions._REGISTRE), type_objet=type_noeud)
        if membre else []
    )

    documents, activite_recente = [], []
    if type_noeud == "table" and id_brut.isdigit():
        documents = list(DocumentGenere.objects.filter(entreprise=entreprise, table_cible_id=id_brut)
                          .order_by("-id")[:10].values("id", "type_document", "titre", "statut"))
        table = TableDynamique.objects.filter(id=id_brut, entreprise=entreprise).first()
        if table is not None:
            activite_recente = list(JournalAudit.objects.filter(
                entreprise=entreprise, details__table=table.slug
            ).order_by("-horodatage")[:15].values("action", "details", "horodatage", "resultat", "type_acteur"))
    elif type_noeud == "enregistrement" and id_brut.isdigit():
        documents = list(DocumentGenere.objects.filter(entreprise=entreprise, enregistrement_cible_id=id_brut)
                          .order_by("-id")[:10].values("id", "type_document", "titre", "statut"))
    elif type_noeud == "entreprise" and id_brut.isdigit():
        documents = list(DocumentGenere.objects.filter(entreprise=entreprise)
                          .order_by("-id")[:10].values("id", "type_document", "titre", "statut"))
        activite_recente = list(JournalAudit.objects.filter(entreprise=entreprise)
                                 .order_by("-horodatage")[:15].values("action", "details", "horodatage", "resultat", "type_acteur"))
    elif type_noeud in ("agent", "admin", "client") and id_brut.isdigit():
        membre_cible = MembreEntreprise.objects.filter(
            id=id_brut, entreprise=entreprise
        ).values_list("utilisateur_id", flat=True).first()
        if membre_cible is not None:
            documents = list(DocumentGenere.objects.filter(entreprise=entreprise, utilisateur_id=membre_cible)
                              .order_by("-id")[:10].values("id", "type_document", "titre", "statut"))
            activite_recente = list(JournalAudit.objects.filter(entreprise=entreprise, utilisateur_id=membre_cible)
                                     .order_by("-horodatage")[:15].values("action", "details", "horodatage", "resultat", "type_acteur"))

    # Portail de navigation par données liées (engine/portail.py) : strictement additif, et jamais
    # bloquant — la fiche reste utilisable même si la résolution des liens échoue.
    try:
        from . import portail
        portail_liens = portail.resoudre_destinations(entreprise, noeud_id, membre=membre)
    except Exception:
        portail_liens = {"supporte": False, "destinations": [], "liens_fantomes": []}

    return {
        "objet": objet, "relations": relations,
        "actions_disponibles": actions_disponibles,
        "fonctions_disponibles": fonctions.catalogue(type_objet=type_noeud),
        "documents": documents, "activite_recente": activite_recente,
        "portail": portail_liens,
    }


def assembler_liste_objets(entreprise, type_noeud, membre=None, page_id=None,
                            parent_id=None, q="", limit=200, offset=0, enrichir=False) -> dict:
    """Liste générique des objets d'un TYPE de nœud de l'Ontology (section
    10 du cahier des charges — « Applications object-centric ») : le pendant
    "liste" de assembler_fiche_objet ci-dessus. Une interface métier ne
    devrait pas avoir besoin d'une vue/requête dédiée par type d'objet (une
    pour les KPI, une pour les dashboards, une pour les skills, une pour
    les modèles IA...) quand l'Ontology les décrit déjà tous de façon
    uniforme (type, label, attributs) — c'est le manque signalé : "les
    interfaces métier classiques (grilles, listes) n'ont pas toutes été
    reconstruites autour de l'Ontology".

    N'introduit aucun nouveau moteur : filtre par type les nœuds du graphe
    déjà construit (ontologie.construire_graphe, même isolation par agent
    que le reste de l'Ontology, mêmes couches). `parent_id` (optionnel,
    ex. "table:5") restreint aux seuls voisins directs de ce nœud — permet
    par exemple de lister les colonnes ("champ") ou les KPI ("kpi") d'UNE
    table précise sans reconstruire une requête ad hoc. `q` filtre par
    sous-chaîne insensible à la casse sur le label.

    `type_noeud` accepte aussi un ITÉRABLE de types (ex. ("agent", "admin"))
    — un même écran "employés" couvre plusieurs types de nœud (voir
    ontologie.construire_graphe : rôle admin/agent = deux types distincts),
    l'Ontology ne devrait pas forcer un appel par rôle pour ça. `offset`/
    `limit` paginent (même convention que engine/grille.py) ; `total` reste
    le compte AVANT pagination. `enrichir=True` complète chaque objet avec
    les champs réels du modèle sous-jacent (voir _enrichir_objets plus haut)
    — seulement pour les types couverts (agent/admin/client/kpi/dashboard à
    ce jour) ; False par défaut pour ne jamais changer le comportement des
    appelants existants de cette fonction sans le demander explicitement.

    Limite documentée (même esprit que assembler_fiche_objet) : pour
    type_noeud="enregistrement", le graphe ne porte qu'un ÉCHANTILLON
    récent de lignes (voir ontologie.LIMITE_ENREGISTREMENTS_GRAPHE) — cette
    fonction reste donc pensée pour les objets de STRUCTURE (tables,
    calculs, KPI, dashboards, skills, modèles IA, agents/admins/clients,
    documents...), pas pour parcourir tout le volume de données d'une
    table, qui garde sa grille dédiée (engine/grille.py, conçue pour ça)."""
    from .ontologie import construire_graphe, construire_graphe_manuel
    from .models import PageOntologie

    page = None
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()
    graphe = (
        construire_graphe_manuel(page, entreprise) if page is not None and page.mode == "manuel"
        else construire_graphe(entreprise, page=page, membre=membre)
    )
    from .ontologie import restreindre_pour_client
    graphe = restreindre_pour_client(graphe, entreprise, membre)

    if parent_id is not None and parent_id in graphe:
        candidats = set(graphe.successors(parent_id)) | set(graphe.predecessors(parent_id))
    else:
        candidats = graphe.nodes()

    types_voulus = {type_noeud} if isinstance(type_noeud, str) else set(type_noeud)

    q_normalise = (q or "").strip().lower()
    objets = []
    for nid in candidats:
        attrs = graphe.nodes[nid]
        if attrs.get("type") not in types_voulus:
            continue
        if q_normalise and q_normalise not in str(attrs.get("label", "")).lower():
            continue
        objets.append(_noeud_dict(nid, attrs))

    objets.sort(key=lambda o: str(o.get("label", "")).lower())
    total = len(objets)
    offset = max(0, offset)
    page = objets[offset:offset + max(1, limit)]
    if enrichir:
        # Un enrichissement groupé PAR TYPE (jamais mélangé) : une page peut
        # contenir plusieurs types voulus (ex. agent+admin) mais chaque appel
        # DB reste filtré à un seul type, comme _enrichir_objets l'exige.
        for t in types_voulus:
            _enrichir_objets(t, [o for o in page if o.get("type") == t], entreprise)
    return {
        "type": type_noeud if isinstance(type_noeud, str) else sorted(types_voulus),
        "total": total, "objets": page,
    }
