from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t
from engine.i18n import tf, tl
from ._header import *  # noqa

def _provenance_table(t) -> dict:
    """Phase 11 (séparation TableDynamique/Stockage → Ontology) : dérive les
    propriétés d'objet ontologique (source, statut, dates) directement des
    champs de provenance déjà portés par TableDynamique — aucun nouvel état
    physique, la table reste l'unique source de vérité, l'Ontology ne fait
    que l'exposer sous la forme générique attendue par un Object (cahier des
    charges section 1 : source, statut, date de création/modification)."""
    from django.db.models import Max
    if t.connexion_externe_origine_id:
        source = (tf("engine/ontologie/_part_001.py:g:12:19", v1=t.nom_source_distante or '?', v2=t.connexion_externe_origine.get_moteur_display()))
    elif t.provient_fichier:
        source = _gtf("engine/ontologie/_part_001.py:17:18", v1=(t.nom_fichier_origine or '?'))
    else:
        source = "saisie manuelle"
    statut = "lecture seule (import)" if t.provient_fichier else _gt("engine/ontologie/_part_001.py:20:66")
    maj = t.enregistrements.aggregate(m=Max("maj_le"))["m"]
    return {
        "source": source, "statut": statut,
        "date_creation": t.cree_le.isoformat() if t.cree_le else "",
        "date_modification": (maj or t.cree_le).isoformat() if (maj or t.cree_le) else "",
    }


# Famille de chaque type de colonne (ChampDynamique.TYPE_CHOICES) : sert à relier
# les nœuds « type de champ » entre eux par famille. Un type absent de cette table
# (type ajouté plus tard) tombe dans « autre » sans casser le graphe.
FAMILLES_TYPES_CHAMP = {
    "texte": "texte", "texte_long": "texte", "email": "texte", "url": "texte",
    "telephone": "texte", "couleur": "texte",
    "nombre": _gtfr("engine/ontologie/_part_001.py:35:15"), "decimal": _gtfr("engine/ontologie/_part_001.py:35:40"), "monnaie": _gtfr("engine/ontologie/_part_001.py:35:65"),
    "pourcentage": _gtfr("engine/ontologie/_part_001.py:36:20"), "note": _gtfr("engine/ontologie/_part_001.py:36:42"), "barre_progression": _gtfr("engine/ontologie/_part_001.py:36:77"),
    "date": "temporel", "heure": "temporel", "datetime": "temporel", "duree": "temporel",
    "booleen": "choix", "switch": "choix", "choix_unique": "choix", "choix_multiple": "choix",
    "fichier": _gtfr("engine/ontologie/_part_001.py:39:16"), "image": _gtfr("engine/ontologie/_part_001.py:39:35"), "son": _gtfr("engine/ontologie/_part_001.py:39:52"), "video": _gtfr("engine/ontologie/_part_001.py:39:71"),
    "vecteur": "tenseur / IA", "matrice": "tenseur / IA", "json": "tenseur / IA",
    "relation": tl("engine/ontologie/_part_001.py:g:39:17"), "geolocalisation": tl("engine/ontologie/_part_001.py:g:39:57"),
    "formule": "automatique", "constante": "automatique",
    "horodatage": "automatique", "id_client": "automatique", "colonne": td("engine/ontologie/_part_001.py:g:42:73"),
}

def enrichir_relations_modules(g, entreprise, tables_qs, racine, prefixe_role=None) -> None:
    """Relie au graphe les modules ajoutés après la première version de
    l'ontologie, dont les nœuds restaient isolés ou sous-exprimés :

      - colonne → table référencée (champ à relation), type de colonne exposé
        (texte, nombre, son, video…) ;
      - formules de champ / de ligne : colonnes sources → colonne calculée ;
      - partage de lignes et de cellules : un nœud « groupe » par ensemble
        (jamais de pair à pair), relié à toutes ses lignes / cellules ;
      - dashboards BI : colonnes et KPI réellement affichés par les widgets ;
      - KPI : colonne mesurée ;
      - abonnements mensuels : membre → abonnement → service débloqué ;
      - connexions externes (provenance des tables importées), groupes de
        tables, tâches planifiées, demandes des agents sur les tables.

    Chaque bloc est indépendant et jamais bloquant : une erreur dans l'un
    n'empêche ni les autres ni le reste du graphe. Ne s'accroche qu'à des
    nœuds déjà présents (donc hérite de l'isolation par agent). Les soldes
    de wallet ne sont volontairement pas exposés.
    """
    from collections import defaultdict
    from ..models import (
        AbonnementMensuel, CellulePartagee, ChampDynamique, ConnexionExterne,
        DemandeAgent, FormuleChamp, FormuleLigne, FormuleLigneCible, GroupePartageCellule,
        GroupePartageLigne, GroupeTables, IndicateurKPI, LignePartagee, MembreEntreprise,
        TachePlanifiee, WidgetGraphiqueBI,
    )
    prefixe_role = prefixe_role or {"agent": "agent", "admin": "admin", "lecteur": "client"}

    def _bloc(nom, fn):
        try:
            fn()
        except Exception:
            logger.exception(_i18n_t("engine/ontologie/_part_001.py:g:77:30"), nom)

    def _arete(u, v, **attrs):
        if g.has_node(u) and g.has_node(v) and u != v and not g.has_edge(u, v):
            g.add_edge(u, v, **attrs)
            return True
        return False

    champs = list(ChampDynamique.objects.filter(table__in=tables_qs)
                  .only("id", "nom", "slug", "type_champ", "obligatoire", "table_id", "relation_vers"))
    champ_par_slug = {(c.table_id, c.slug): c.id for c in champs}

    # ── Colonnes : type, obligatoire, et renvoi direct vers la table référencée ──
    def colonnes():
        for c in champs:
            nid = f"champ:{c.id}"
            if g.has_node(nid):
                g.nodes[nid]["type_champ"] = c.type_champ
                g.nodes[nid]["obligatoire"] = bool(c.obligatoire)
            if c.relation_vers_id:
                _arete(nid, f"table:{c.relation_vers_id}", relation="reference", champ=c.nom)
    _bloc("colonnes", colonnes)

    # ── Types de champ : un nœud par type RÉELLEMENT utilisé, relié à toutes ses
    # colonnes (champ → type) et à sa famille (type → famille). Permet de suivre
    # « toutes les colonnes de type son/image/formule… » sans passer par le nom. ──
    def types_champ():
        libelles = dict(ChampDynamique.TYPE_CHOICES)
        familles_vues = set()
        for c in champs:
            nid_champ = f"champ:{c.id}"
            if not g.has_node(nid_champ) or g.nodes[nid_champ].get("type") == "champ_systeme":
                continue   # colonnes des tables système : leur type n'apprend rien et dominerait le décompte
            tc = c.type_champ or "inconnu"
            nid_type = f"type_champ:{tc}"
            if not g.has_node(nid_type):
                fam = FAMILLES_TYPES_CHAMP.get(tc, "autre")
                g.add_node(nid_type, type="type_champ", label=libelles.get(tc, tc), famille=fam)
                nid_fam = f"famille_type:{fam}"
                if not g.has_node(nid_fam):
                    g.add_node(nid_fam, type="famille_type_champ", label=fam.capitalize())
                g.add_edge(nid_type, nid_fam, relation="famille_de_type")
            g.add_edge(nid_champ, nid_type, relation="est_de_type")
        # poids = nombre de colonnes de ce type (taille du nœud proportionnelle)
        for n, a in list(g.nodes(data=True)):
            if a.get("type") == "type_champ":
                a["nb_lignes"] = g.in_degree(n)
    _bloc("types_champ", types_champ)

    # ── Formules de champ (colonne calculée) : sources → colonne cible ──
    def formules_champ():
        for fc in FormuleChamp.objects.filter(table__in=tables_qs):
            cible = f"champ:{fc.champ_cible_id}"
            for slug in (fc.operandes_slugs or []):
                cid = champ_par_slug.get((fc.table_id, slug))
                if cid:
                    _arete(f"champ:{cid}", cible, relation="alimente_formule",
                           operateur=fc.operateur, portee="colonne")
    _bloc("formules_champ", formules_champ)

    # ── Formules de ligne : sources → champ cible, et ligne → champ cible ──
    def formules_ligne():
        for fl in FormuleLigne.objects.filter(table__in=tables_qs):
            cibles = list(FormuleLigneCible.objects.filter(formule_ligne=fl))
            for ct in cibles:
                cible = f"champ:{ct.champ_cible_id}"
                for slug in (fl.operandes_slugs or []):
                    cid = champ_par_slug.get((fl.table_id, slug))
                    if cid:
                        _arete(f"champ:{cid}", cible, relation="alimente_formule",
                               operateur=fl.operateur, portee="ligne")
                _arete(f"enregistrement:{fl.enregistrement_id}", cible,
                       relation="calcul_ligne", operateur=fl.operateur)
    _bloc("formules_ligne", formules_ligne)

    # ── Partage de lignes : un hub par groupe, relié à chaque ligne ──
    def partage_lignes():
        for gp in GroupePartageLigne.objects.filter(entreprise=entreprise):
            hub = f"groupe_ligne:{gp.id}"
            lignes = [lp.enregistrement_id for lp in LignePartagee.objects.filter(groupe=gp)
                      if g.has_node(f"enregistrement:{lp.enregistrement_id}")]
            if not lignes:
                continue
            g.add_node(hub, type="groupe_partage_ligne",
                       label=gp.nom or tf("engine/ontologie/_part_001.py:g:161:40", v1=gp.id), nb_lignes=len(lignes),
                       **({} if gp.nom else {"label_cle": "engine/ontologie/_part_001.py:g:161:40", "label_vars": {"v1": gp.id}}))
            for eid in lignes:
                g.add_edge(f"enregistrement:{eid}", hub, relation="partage_ligne")
    _bloc("partage_lignes", partage_lignes)

    # ── Partage de cellules : un hub par groupe, relié aux colonnes et aux lignes ──
    def partage_cellules():
        for gp in GroupePartageCellule.objects.filter(entreprise=entreprise):
            hub = f"groupe_cellule:{gp.id}"
            cellules = list(CellulePartagee.objects.filter(groupe=gp))
            visibles = [c for c in cellules
                        if g.has_node(f"champ:{c.champ_id}")
                        or g.has_node(f"enregistrement:{c.enregistrement_id}")]
            if not visibles:
                continue
            g.add_node(hub, type="groupe_partage_cellule",
                       label=gp.nom or tf("engine/ontologie/_part_001.py:g:177:40", v1=gp.id), nb_lignes=len(visibles),
                       **({} if gp.nom else {"label_cle": "engine/ontologie/_part_001.py:g:177:40", "label_vars": {"v1": gp.id}}))
            for c in visibles:
                _arete(f"champ:{c.champ_id}", hub, relation="partage_cellule")
                _arete(f"enregistrement:{c.enregistrement_id}", hub, relation="partage_cellule")
    _bloc("partage_cellules", partage_cellules)

    # ── Dashboards BI : colonnes et KPI réellement affichés par les widgets ──
    def widgets():
        poids_champ = defaultdict(int)
        for w in (WidgetGraphiqueBI.objects.filter(tableau_bord__entreprise=entreprise)
                  .prefetch_related("champs_cibles")):
            dash = f"dashboard:{w.tableau_bord_id}"
            if w.kpi_id:
                _arete(f"kpi:{w.kpi_id}", dash, relation="affiche_kpi")
            for c in w.champs_cibles.all():
                poids_champ[(f"champ:{c.id}", dash)] += 1
        for (champ, dash), n in poids_champ.items():
            _arete(champ, dash, relation="affiche_champ", poids=n)
    _bloc("widgets", widgets)

    # ── KPI : colonne mesurée ──
    def kpi_champ():
        for kpi in IndicateurKPI.objects.filter(entreprise=entreprise, champ_cible__isnull=False):
            _arete(f"champ:{kpi.champ_cible_id}", f"kpi:{kpi.id}", relation="mesure_champ")
    _bloc("kpi_champ", kpi_champ)

    # ── Abonnements mensuels : membre → abonnement → service débloqué ──
    def abonnements():
        services = {
            "dashboard_bi": lambda: [n for n, a in g.nodes(data=True) if a.get("type") == "dashboard"],
            "ontologie": lambda: [racine],
            "ia": lambda: [n for n, a in g.nodes(data=True) if a.get("type") in ("config_ia", "modele_ia")],
        }
        for ab in (AbonnementMensuel.objects.filter(membre__entreprise=entreprise)
                   .select_related("membre")):
            m = ab.membre
            personne = f"{prefixe_role.get(m.role, 'agent')}:{m.id}"
            if not g.has_node(personne):
                continue
            nid = f"abonnement:{ab.id}"
            g.add_node(nid, type="abonnement", label=ab.get_categorie_display(),
                       valide=bool(ab.est_valide()), expire_le=ab.expire_le.isoformat())
            g.add_edge(personne, nid, relation="souscrit")
            for cible in services.get(ab.categorie, lambda: [])():
                _arete(nid, cible, relation="debloque")
    _bloc("abonnements", abonnements)

    # ── Connexions externes : provenance des tables importées ──
    def connexions():
        for cx in ConnexionExterne.objects.filter(entreprise=entreprise):
            nid = f"connexion:{cx.id}"
            tables = [t for t in tables_qs.filter(connexion_externe_origine=cx) if g.has_node(f"table:{t.id}")]
            if not tables:
                continue
            g.add_node(nid, type="connexion_externe", label=f"{cx.nom} ({cx.get_moteur_display()})")
            for t in tables:
                g.add_edge(nid, f"table:{t.id}", relation="importe_vers")
    _bloc("connexions", connexions)

    # ── Groupes de tables ──
    def groupes():
        for gt in GroupeTables.objects.filter(entreprise=entreprise):
            tables = [t.id for t in tables_qs.filter(groupe=gt) if g.has_node(f"table:{t.id}")]
            if not tables:
                continue
            nid = f"groupe_tables:{gt.id}"
            volume = sum(g.nodes[f"table:{tid}"].get("nb_enregistrements", 0) or 0 for tid in tables)   # un groupe = UNE table : volume de toutes ses parties
            g.add_node(nid, type="groupe_tables", label=gt.nom, nb_lignes=len(tables), nb_enregistrements=volume)
            for tid in tables:
                g.add_edge(nid, f"table:{tid}", relation="regroupe")
    _bloc("groupes", groupes)

    # ── Tâches planifiées : table surveillée → tâche ──
    def taches():
        for tp in TachePlanifiee.objects.filter(entreprise=entreprise):
            nid = f"tache_planifiee:{tp.id}"
            g.add_node(nid, type="tache_planifiee", label=tp.nom or tf("engine/ontologie/_part_001.py:g:253:69", v1=tp.id),
                       **({} if tp.nom else {"label_cle": "engine/ontologie/_part_001.py:g:253:69", "label_vars": {"v1": tp.id}}))
            if tp.condition_table_id and g.has_node(f"table:{tp.condition_table_id}"):
                g.add_edge(f"table:{tp.condition_table_id}", nid, relation="surveille")
            else:
                g.add_edge(racine, nid, relation="planifie")
    _bloc("taches", taches)

    # ── Demandes des agents : qui sollicite quelle table (volume en poids) ──
    def demandes():
        from django.db.models import Count
        membres = {m.utilisateur_id: m for m in MembreEntreprise.objects.filter(entreprise=entreprise)}
        for row in (DemandeAgent.objects.filter(entreprise=entreprise, table_cible__isnull=False)
                    .values("utilisateur_id", "table_cible_id").annotate(nb=Count("id")).order_by()):
            m = membres.get(row["utilisateur_id"])
            if m is not None:
                _arete(f"{prefixe_role.get(m.role, 'agent')}:{m.id}", f"table:{row['table_cible_id']}",
                       relation="sollicite", poids=row["nb"])
    _bloc("demandes", demandes)




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
