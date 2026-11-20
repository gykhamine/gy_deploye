from engine.i18n import libelle_slug
from ..ontologie_systeme import libelle_systeme, type_noeud_table  # noqa: E402
from engine.i18n import t as _i18n_t, tf
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa


def construire_graphe_manuel(page, entreprise) -> "nx.DiGraph":
    """Construit le graphe d'une PageOntologie en mode manuel (page.mode ==
    "manuel") : uniquement les tables et colonnes choisies par l'utilisateur
    (page.tables_manuelles / page.champs_manuels), reliées uniquement par les
    relations qu'il a lui-même tracées (page.relations_manuelles) — aucune
    inférence automatique (ni relation_vers, ni couches), à l'inverse de
    construire_graphe."""
    g = nx.DiGraph()
    racine = f"entreprise:{entreprise.id}"
    g.add_node(racine, type="entreprise", label=entreprise.nom)

    for t in page.tables_manuelles.select_related("connexion_externe_origine").all():
        noeud_table = f"table:{t.id}"
        g.add_node(noeud_table, type=type_noeud_table(t.source_systeme), label=t.nom, slug=t.slug,
                   nb_enregistrements=t.enregistrements.count(),
                   est_table_membres=t.est_table_membres, **_provenance_table(t))
        g.add_edge(racine, noeud_table, relation="possede")

    for c in page.champs_manuels.select_related("table").all():
        if g.has_node(f"table:{c.table_id}"):
            noeud_champ = f"champ:{c.id}"
            g.add_node(noeud_champ, type="champ", label=c.nom, table_id=c.table_id)
            g.add_edge(f"table:{c.table_id}", noeud_champ, relation="colonne")

    for r in page.relations_manuelles:
        source, cible = r.get("source"), r.get("cible")
        if g.has_node(source) and g.has_node(cible):
            g.add_edge(source, cible, relation=r.get("relation") or "lien",
                       label=r.get("label", ""), manuel=True)

    return g


def metriques(graphe: "nx.DiGraph") -> dict:
    """Mesures topologiques pures du graphe (aucune interprétation) :
    densité, diamètre et clustering sur la plus grande composante connexe,
    nombre de communautés détectées."""
    n = graphe.number_of_nodes()
    res = {"noeuds": n, "aretes": graphe.number_of_edges(), "densite": 0.0,
           "diametre": 0, "clustering": 0.0, "communautes": 0}
    if n < 2:
        return res
    try:
        res["densite"] = round(nx.density(graphe), 4)
        non_oriente = graphe.to_undirected()
        composantes = list(nx.connected_components(non_oriente))
        if composantes:
            plus_grande = non_oriente.subgraph(max(composantes, key=len))
            if plus_grande.number_of_nodes() > 1:
                # Diamètre exact en O(N·E) : impossible à 32 000 nœuds. Au-delà de 1 500 nœuds : borne basse par double balayage (quasi exacte en pratique).
                res["diametre"] = nx.diameter(plus_grande) if plus_grande.number_of_nodes() <= 1500 else nx.approximation.diameter(plus_grande)
        res["clustering"] = round(nx.average_clustering(non_oriente), 4)
        # Modularité gloutonne en O(N·E) elle aussi : propagation d'étiquettes (quasi linéaire) au-delà de 5 000 nœuds.
        res["communautes"] = (len(list(nx.community.greedy_modularity_communities(non_oriente))) if n <= 5000
                              else len(list(nx.community.label_propagation_communities(non_oriente))))
    except Exception as exc:
        logger.warning(_i18n_t("engine/ontologie/_part_003.py:g:61:24"), exc)
    return res


def _couleurs_personnalisees(entreprise) -> tuple[dict, dict]:
    """Couleurs personnalisées par l'entreprise (voir models.CouleurOntologie),
    séparées par cible ({type: couleur}, {relation: couleur}). Vide si aucune
    entreprise fournie (ex. génération de contexte hors requête)."""
    if entreprise is None:
        return {}, {}
    from ..models import CouleurOntologie
    noeud, arete = {}, {}
    for c in CouleurOntologie.objects.filter(entreprise=entreprise):
        (noeud if c.cible == "noeud" else arete)[c.cle] = c.couleur
    return noeud, arete


def _couleurs_noeuds_individuels(entreprise) -> dict:
    """{id de nœud: couleur} choisie sur UN nœud précis (voir models.CouleurNoeudOntologie). Vide sans entreprise."""
    if entreprise is None:
        return {}
    from ..models import CouleurNoeudOntologie
    return dict(CouleurNoeudOntologie.objects.filter(entreprise=entreprise).values_list("noeud", "couleur"))


def graphe_vers_json(graphe: "nx.DiGraph", entreprise=None, page=None) -> dict:
    """Sérialise le graphe pour le rendu navigable côté client (canvas
    JS) : liste de nœuds (id, type, label, taille, slug, couleur) et
    d'arêtes (source, cible, poids, couleur).

    `page` (models.PageOntologie, optionnel) : si fourni, les notes libres
    enregistrées par l'utilisateur (page.descriptions_noeuds /
    descriptions_relations, voir vue api_page_ontologie_description) sont
    ajoutées à chaque nœud/arête sous la clé "description" — purement
    informatif, n'affecte ni le calcul ni la mise en page du graphe.

    Priorité de couleur : couleur choisie sur CE nœud précis (models.CouleurNoeudOntologie) > couleur personnalisée en base
    pour le TYPE, pour CETTE entreprise (voir models.CouleurOntologie, éditable depuis l'interface,
    y compris pour un type déjà prédéfini) > couleur explicite posée par une
    CoucheOntologie (g.add_node(..., couleur=...)) > couleur générique par
    défaut du type/de la relation > gris."""
    couleurs_noeud_perso, couleurs_arete_perso = _couleurs_personnalisees(entreprise)
    couleurs_noeud = {**_COULEURS, **couleurs_noeud_perso}
    couleurs_noeuds_ind = _couleurs_noeuds_individuels(entreprise)
    couleurs_arete = {**_COULEURS_ARETES, **couleurs_arete_perso}
    descr_noeuds = page.descriptions_noeuds if page is not None else {}
    descr_relations = page.descriptions_relations if page is not None else {}

    noeuds = []
    for n, attrs in graphe.nodes(data=True):
        volume = attrs.get("nb_enregistrements", attrs.get("nb_lignes", 0)) or 0
        t = attrs.get("type", "?")
        noeuds.append({
            "id": n, "type": t, "type_libelle": libelle_systeme("ontologie.type", t) or libelle_slug("ontologie.type", t), "label": libelle_noeud(attrs, n),
            "slug": attrs.get("slug", ""),
            "taille": attrs.get("taille") or (10 + min(volume, 200) ** 0.5 * 2.2),
            "couleur": couleurs_noeuds_ind.get(n) or couleurs_noeud_perso.get(t) or attrs.get("couleur") or couleurs_noeud.get(t, "#888888"),
            "couleur_propre": n in couleurs_noeuds_ind,
            "est_table_membres": attrs.get("est_table_membres", False),
            "systeme": bool(attrs.get("systeme", False)), "domaine": attrs.get("domaine", ""),
            "description": descr_noeuds.get(n, ""),
            # Aperçu hiérarchique (voir ontologie_apercu) : absents d'un graphe non tronqué, sans effet pour les autres appelants.
            "profondeur": attrs.get("profondeur"), "parent": attrs.get("parent"), "nb_caches": attrs.get("nb_caches", 0),
        })
    aretes = []
    for u, v, a in graphe.edges(data=True):
        rel = a.get("relation", "")
        cle = f"{u}→{v}:{rel}"
        aretes.append({
            "source": u, "cible": v, "relation": rel, "libelle": libelle_slug("ontologie.relation", rel),
            "poids": a.get("poids", 1), "champ": a.get("champ", ""),
            "date": a.get("date", ""), "modifie_le": a.get("modifie_le") or "", "role": a.get("role", ""),
            "couleur": couleurs_arete_perso.get(rel) or a.get("couleur") or couleurs_arete.get(rel, "#888888"),
            "cle": cle, "description": descr_relations.get(cle, ""),
        })
    return {"noeuds": noeuds, "aretes": aretes, "couleurs": couleurs_noeud, "couleurs_aretes": couleurs_arete}


# ── Catalogue des types de nœuds et de relations déjà connus de la
# plateforme (au-delà de ce qui est réellement présent dans le graphe d'UNE
# entreprise à un instant donné) : sert de contexte à la génération de
# couche via IA (voir inference.MoteurInference.generer_couche_ontologie)
# ET de source pour un sélecteur côté interface — "tout ce qui est possible
# comme événement/entité dans la plateforme", pour composer une nouvelle
# relation sans avoir à deviner les préfixes d'id/attributs déjà utilisés. ──
def lister_types_evenements(entreprise=None) -> dict:
    """Catalogue des types de nœuds et de relations connus de la plateforme
    (catalogue statique), ÉTENDU des types/relations propres à `entreprise` :
    soit une couleur personnalisée sur un type déjà connu (voir
    models.CouleurOntologie), soit un TOUT NOUVEAU type/relation déclaré via
    l'interface (avec label/description) avant même d'être utilisé par une
    CoucheOntologie — pour apparaître immédiatement dans les sélecteurs côté
    interface (créer une nouvelle relation en choisissant une entité
    source/cible parmi tout ce qui existe ou est prévu sur la plateforme,
    sans avoir à écrire de code) et informer la génération de couche via IA."""
    types = [{"type": t, "libelle": libelle_systeme("ontologie.type", t) or libelle_slug("ontologie.type", t), "description": DESCRIPTIONS_TYPES.get(t, ""), "couleur": c, "personnalise": False}
             for t, c in _COULEURS.items()]
    relations = [{"relation": r, "libelle": libelle_slug("ontologie.relation", r), "description": DESCRIPTIONS_RELATIONS.get(r, ""), "couleur": c, "personnalise": False}
                 for r, c in _COULEURS_ARETES.items()]
    if entreprise is not None:
        from ..models import CouleurOntologie
        connus_types = {t["type"] for t in types}
        connus_relations = {r["relation"] for r in relations}
        for c in CouleurOntologie.objects.filter(entreprise=entreprise):
            if c.cible == "noeud":
                if c.cle in connus_types:
                    for t in types:
                        if t["type"] == c.cle:
                            t["couleur"] = c.couleur
                else:
                    types.append({"type": c.cle, "description": c.description or c.label,
                                  "couleur": c.couleur, "personnalise": True})
                    connus_types.add(c.cle)
            else:
                if c.cle in connus_relations:
                    for r in relations:
                        if r["relation"] == c.cle:
                            r["couleur"] = c.couleur
                else:
                    relations.append({"relation": c.cle, "description": c.description or c.label,
                                      "couleur": c.couleur, "personnalise": True})
                    connus_relations.add(c.cle)
    return {"types": types, "relations": relations}


def contexte_pour_generation_couche(entreprise) -> str:
    """Construit le texte de contexte transmis au prompt de génération de
    couche via IA (voir inference.MoteurInference.generer_couche_ontologie) :
    d'une part le catalogue complet des types/relations déjà connus de la
    plateforme (voir lister_types_evenements — "tout ce qui est possible
    comme événement dans la plateforme"), d'autre part un aperçu de ce qui
    est réellement présent dans le graphe de CETTE entreprise (types
    effectivement peuplés, avec un exemple de label et de nombre
    d'occurrences), pour que le script généré réutilise les bons préfixes
    d'id plutôt que d'en inventer."""
    graphe = construire_graphe(entreprise)
    catalogue = lister_types_evenements(entreprise)

    lignes = [_i18n_t("engine/ontologie/_part_003.py:g:196:15")]
    for t in catalogue["types"]:
        lignes.append(f"  - {t['type']} : {t['description']}")
    lignes.append("")
    lignes.append(_i18n_t("engine/ontologie/_part_003.py:g:200:19"))
    for r in catalogue["relations"]:
        lignes.append(f"  - {r['relation']} : {r['description']}")

    lignes.append("")
    lignes.append(_i18n_t("engine/ontologie/_part_003.py:g:206:19"))
    par_type = {}
    for n, attrs in graphe.nodes(data=True):
        t = attrs.get("type", "?")
        par_type.setdefault(t, []).append(attrs.get("label", n))
    if par_type:
        for t, labels in par_type.items():
            exemples = ", ".join(str(l) for l in labels[:3])
            lignes.append(tf("engine/ontologie/_part_003.py:g:214:27", v1=t, v2=len(labels), v3=exemples))
    else:
        lignes.append(_i18n_t("engine/ontologie/_part_003.py:g:216:23"))

    return "\n".join(lignes)


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.


def restreindre_pour_client(g, entreprise, membre):
    """Graphe vu par un client (membre.role == "lecteur") : l'entreprise, les tables exposées aux clients et leurs
    colonnes seulement — jamais les autres tables, agents, administrateurs, clients ni objets internes. Sans membre, ou
    pour un autre rôle, le graphe est renvoyé tel quel."""
    if membre is None or membre.role != "lecteur":
        return g
    from django.db.models import Q
    from ..models import TableDynamique
    exposees = {f"table:{i}" for i in TableDynamique.objects.filter(entreprise=entreprise).filter(
        Q(expose_client=True) | Q(masquer_formulaire_client=True)).exclude(est_table_membres=True).values_list("id", flat=True)}
    gardes = {n for n, d in g.nodes(data=True)
              if d.get("type") == "entreprise" or (d.get("type") == "table" and n in exposees)
              or (d.get("type") == "champ" and f"table:{d.get('table_id')}" in exposees)}
    return g.subgraph(gardes).copy()


# Doit rester EN DERNIER : calculé avant, il laissait sortir de l'export toute fonction définie plus bas
# (restreindre_pour_client → AttributeError sur /ontologie/).
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
