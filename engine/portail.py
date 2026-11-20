"""Portail de navigation par données liées.

Depuis n'importe quel objet (nœud de l'ontologie, table, colonne, dashboard BI, KPI, table de
calcul, skill, modèle IA, document, ligne), ce module répond à une seule question : « où puis-je
aller à partir d'ici, en suivant la donnée ? ». Exemple : un nœud « table:5 » mène à la page de
la table, aux dashboards BI construits sur elle (et à leurs graphiques), à ses KPI, à ses tables
de calcul et aux modèles IA qui s'entraînent dessus ; un nœud « champ:12 » mène aux dashboards
dont un graphique trace cette colonne et aux modèles qui la consomment.

Principes (voir aussi la demande d'origine : moteur en back-end, pas de nouvelle page) :

- Les liaisons sont FANTÔMES : aucune table de liens n'est stockée. Elles sont recalculées à la
  volée depuis les FK / M2M existants (TableauBordBI.table_source, WidgetGraphiqueBI.champs_cibles,
  ModeleIA.tables_source, IndicateurKPI.table_source...). Rien à migrer, rien à synchroniser :
  une donnée supprimée ou déplacée n'a jamais de lien périmé.
- Chaque destination est VÉRIFIÉE avant d'être proposée : elle doit exister, appartenir à
  l'entreprise courante et être accessible au membre (vues autorisées, tables autorisées).
  Les incohérences détectées (widget sans colonne, modèle IA dont une colonne n'existe plus
  dans ses tables sources, modèle sans table...) ne produisent pas de lien mort : elles sont
  remontées à part dans `liens_fantomes` pour pouvoir être corrigées.
- Aucun moteur existant n'est modifié : ce module ne fait que lire.

API publique :
    resoudre_destinations(entreprise, noeud_id, membre=None) -> dict
    explorer_toile(entreprise, noeud_id, membre=None, profondeur=2, limite=150) -> dict
"""
from __future__ import annotations
from engine.i18n import td
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t, tf

from collections import deque

from django.urls import NoReverseMatch, reverse

from .models import (
    ChampDynamique, DocumentGenere, Enregistrement, IndicateurKPI, ModeleIA,
    Skill, TableCalcul, TableDynamique, TableauBordBI, WidgetGraphiqueBI,
)

# Types de nœuds que le portail sait résoudre.
TYPES_SUPPORTES = (
    "table", "champ", "enregistrement", "dashboard", "kpi", "calcul", "skill",
    "modele_ia", "document",
)
PROFONDEUR_MAX = 3
LIMITE_DOCUMENTS = 10


# ── Helpers d'URL (jamais d'URL en dur : reverse() ou rien) ─────────────────
def _url(nom: str, args=None, query: str = "") -> str | None:
    try:
        base = reverse(f"engine:{nom}", args=args or [])
    except NoReverseMatch:
        return None
    return f"{base}?{query}" if query else base


def _url_table(table: TableDynamique, ancre: str = "") -> str | None:
    base = _url("vue_table_dediee", [table.slug])
    return f"{base}#{ancre}" if base and ancre else base


# ── Contrôle d'accès (mêmes règles que les vues concernées) ─────────────────
def _vue_ok(membre, slug: str) -> bool:
    """Même règle que views._vue_autorisee : admin/None = tout, agent restreint = liste."""
    if not membre or membre.role != "agent":
        return True
    vues = membre.vues_autorisees
    return not vues or slug in vues


def _table_ok(membre, table_id: int) -> bool:
    """Même règle que views (Phase 9) : agent avec tables_autorisees = liste blanche."""
    if membre is None or membre.role != "agent" or not membre.tables_autorisees:
        return True
    return table_id in membre.tables_autorisees


# ── Résolution des voisins « données liées » ────────────────────────────────
def _noeud(type_noeud: str, obj_id: int, label: str, relation: str, **extra) -> dict:
    return {"noeud_id": f"{type_noeud}:{obj_id}", "type": type_noeud, "id": obj_id,
            "label": label or f"{type_noeud} #{obj_id}", "relation": relation, **extra}


def _ids_groupe(table):
    from .vue_groupe import ids_groupe
    return ids_groupe(table)


def _voisins_table(entreprise, table: TableDynamique, fantomes: list) -> list[dict]:
    v = []
    for tb in TableauBordBI.objects.filter(entreprise=entreprise, table_source_id__in=_ids_groupe(table)).order_by("nom"):
        v.append(_noeud("dashboard", tb.id, tb.nom, "affiche"))
        v.extend(_verifier_dashboard(tb, fantomes, ajouter_noeuds=False))
    for k in IndicateurKPI.objects.filter(entreprise=entreprise, table_source_id__in=_ids_groupe(table)).order_by("nom"):
        v.append(_noeud("kpi", k.id, k.nom, "mesure"))
    for c in TableCalcul.objects.filter(entreprise=entreprise, table_source=table).order_by("nom"):
        v.append(_noeud("calcul", c.id, c.nom, "calcule"))
    for s in Skill.objects.filter(entreprise=entreprise, table=table).order_by("nom"):
        v.append(_noeud("skill", s.id, s.nom, "genere"))
    for m in ModeleIA.objects.filter(entreprise=entreprise, tables_source=table).order_by("nom").distinct():
        v.append(_noeud("modele_ia", m.id, m.nom, "entraine"))
        _verifier_modele(m, fantomes)
    for d in (DocumentGenere.objects.filter(entreprise=entreprise, table_cible=table)
              .order_by("-id")[:LIMITE_DOCUMENTS]):
        v.append(_noeud("document", d.id, d.titre or d.type_document, "produit"))
    for c in ChampDynamique.objects.filter(table=table, relation_vers__isnull=False).select_related("relation_vers"):
        if c.relation_vers.entreprise_id == entreprise.id:
            v.append(_noeud("table", c.relation_vers_id, c.relation_vers.nom, "reference"))
    return v


def _verifier_dashboard(tb: TableauBordBI, fantomes: list, ajouter_noeuds: bool = True) -> list[dict]:
    """Contrôle les widgets graphique d'un dashboard. Un widget graphique sans aucune colonne
    liée est une liaison fantôme (le graphique ne trace plus rien). Retourne les nœuds champ
    réellement tracés quand `ajouter_noeuds` est vrai."""
    noeuds = []
    for w in tb.widgets.filter(type_widget="graphique").prefetch_related("champs_cibles"):
        champs = list(w.champs_cibles.all())
        if not champs:
            fantomes.append({
                "source": f"dashboard:{tb.id}", "cible": f"widget:{w.id}",
                "raison": tf("engine/portail.py:g:121:27", v1=w.titre or w.id, v2=tb.nom),
            })
            continue
        if ajouter_noeuds:
            noeuds.extend(_noeud("champ", c.id, c.nom, "trace") for c in champs)
    return noeuds


def _verifier_modele(m: ModeleIA, fantomes: list) -> None:
    """Un modèle IA dont une colonne d'entrée/sortie n'existe plus dans ses tables sources,
    ou qui n'a plus aucune table source, est une liaison fantôme."""
    slugs_tables = set(
        ChampDynamique.objects.filter(table__in=m.tables_source.all()).values_list("slug", flat=True)
    )
    if not slugs_tables:
        fantomes.append({"source": f"modele_ia:{m.id}", "cible": "table:?",
                         "raison": tf("engine/portail.py:g:137:36", v1=m.nom)})
        return
    utilisees = list(m.colonnes_entree or []) + list(m.colonnes_sortie or [])
    if m.colonne_cible:
        utilisees.append(m.colonne_cible)
    manquantes = sorted({s for s in utilisees if s not in slugs_tables})
    if manquantes:
        fantomes.append({"source": f"modele_ia:{m.id}", "cible": "champ:?",
                         "raison": tf("engine/portail.py:g:145:36", v1=m.nom)
                                   + ", ".join(manquantes)})


def _voisins(entreprise, type_noeud: str, obj_id: int, fantomes: list) -> list[dict]:
    """Voisins directs d'un objet, par données liées. Toujours scopé à l'entreprise."""
    if type_noeud == "table":
        t = TableDynamique.objects.filter(id=obj_id, entreprise=entreprise).first()
        return _voisins_table(entreprise, t, fantomes) if t else []

    if type_noeud == "champ":
        c = ChampDynamique.objects.filter(id=obj_id, table__entreprise=entreprise).select_related("table").first()
        if not c:
            return []
        v = [_noeud("table", c.table_id, c.table.nom, "colonne_de")]
        deja = set()
        for w in (WidgetGraphiqueBI.objects.filter(champs_cibles=c, tableau_bord__entreprise=entreprise)
                  .select_related("tableau_bord")):
            if w.tableau_bord_id not in deja:
                deja.add(w.tableau_bord_id)
                v.append(_noeud("dashboard", w.tableau_bord_id, w.tableau_bord.nom, "trace_par"))
        for m in ModeleIA.objects.filter(entreprise=entreprise, tables_source=c.table).distinct():
            if c.slug in (m.colonnes_entree or []) or c.slug in (m.colonnes_sortie or []) or c.slug == m.colonne_cible:
                v.append(_noeud("modele_ia", m.id, m.nom, "consomme"))
        return v

    if type_noeud == "dashboard":
        tb = TableauBordBI.objects.filter(id=obj_id, entreprise=entreprise).select_related("table_source").first()
        if not tb:
            return []
        v = [_noeud("table", tb.table_source_id, tb.table_source.nom, "source")]
        v.extend(_verifier_dashboard(tb, fantomes))
        return v

    if type_noeud == "kpi":
        k = IndicateurKPI.objects.filter(id=obj_id, entreprise=entreprise).select_related("table_source", "champ_cible").first()
        if not k:
            return []
        v = [_noeud("table", k.table_source_id, k.table_source.nom, "source")]
        if k.champ_cible_id:
            v.append(_noeud("champ", k.champ_cible_id, k.champ_cible.nom, "mesure_champ"))
        return v

    if type_noeud == "calcul":
        c = TableCalcul.objects.filter(id=obj_id, entreprise=entreprise).select_related("table_source").first()
        return [_noeud("table", c.table_source_id, c.table_source.nom, "source")] if c else []

    if type_noeud == "skill":
        s = Skill.objects.filter(id=obj_id, entreprise=entreprise).select_related("table").first()
        return [_noeud("table", s.table_id, s.table.nom, "source")] if s else []

    if type_noeud == "modele_ia":
        m = ModeleIA.objects.filter(id=obj_id, entreprise=entreprise).first()
        if not m:
            return []
        _verifier_modele(m, fantomes)
        return [_noeud("table", t.id, t.nom, "entraine_sur")
                for t in m.tables_source.filter(entreprise=entreprise)]

    if type_noeud == "document":
        d = DocumentGenere.objects.filter(id=obj_id, entreprise=entreprise).select_related("table_cible").first()
        return [_noeud("table", d.table_cible_id, d.table_cible.nom, "produit_depuis")] if d and d.table_cible_id else []

    if type_noeud == "enregistrement":
        e = Enregistrement.objects.filter(id=obj_id, table__entreprise=entreprise).select_related("table").first()
        return [_noeud("table", e.table_id, e.table.nom, "ligne_de")] if e else []

    return []


# ── Construction des destinations navigables ────────────────────────────────
def _table_de(entreprise, type_noeud: str, obj_id: int) -> TableDynamique | None:
    """Table de rattachement d'un objet (pour bâtir le lien de sa page)."""
    if type_noeud == "table":
        return TableDynamique.objects.filter(id=obj_id, entreprise=entreprise).first()
    modele = {"champ": (ChampDynamique, "table__entreprise", "table"),
              "dashboard": (TableauBordBI, "entreprise", "table_source"),
              "kpi": (IndicateurKPI, "entreprise", "table_source"),
              "calcul": (TableCalcul, "entreprise", "table_source"),
              "skill": (Skill, "entreprise", "table"),
              "document": (DocumentGenere, "entreprise", "table_cible"),
              "enregistrement": (Enregistrement, "table__entreprise", "table")}.get(type_noeud)
    if not modele:
        return None
    classe, filtre, attr = modele
    obj = classe.objects.filter(id=obj_id, **{filtre: entreprise}).select_related(attr).first()
    return getattr(obj, attr, None) if obj else None


def _destination(entreprise, membre, n: dict) -> dict | None:
    """Transforme un voisin en destination cliquable, ou None si non accessible / sans page."""
    type_noeud, obj_id = n["type"], n["id"]
    table = _table_de(entreprise, type_noeud, obj_id)
    if type_noeud == "modele_ia":
        if not _vue_ok(membre, "ia"):
            return None
        url, categorie = _url("vue_ia", query=f"modele={obj_id}"), _i18n_t("engine/portail.py:g:241:68")
    elif type_noeud in ("dashboard",) or (type_noeud == "champ" and n["relation"] == "trace"):
        if table is None or not _table_ok(membre, table.id) or not _vue_ok(membre, "dashboard_bi"):
            return None
        tableau = obj_id if type_noeud == "dashboard" else None
        url = _url("vue_dashboard_bi", [table.id], query=f"tableau={tableau}" if tableau else "")
        categorie = _i18n_t("engine/portail.py:g:247:21")
    else:
        if table is None or not _table_ok(membre, table.id):
            return None
        ancres = {"kpi": f"kpi-{obj_id}", "calcul": f"calcul-{obj_id}", "skill": f"skill-{obj_id}",
                  "document": f"document-{obj_id}", "enregistrement": f"ligne-{obj_id}",
                  "champ": f"champ-{obj_id}"}
        url = _url_table(table, ancres.get(type_noeud, ""))
        categorie = {"table": td("engine/portail.py:g:256:31"), "champ": td("engine/portail.py:g:256:50"), "kpi": "KPI", "calcul": _i18n_t("engine/portail.py:g:255:86"),
                     "skill": "Skills", "document": _gt("engine/portail.py:258:53"), "enregistrement": _i18n_t("engine/portail.py:g:256:84")}.get(type_noeud, "Autres")
    if not url:
        return None
    return {**n, "url": url, "categorie": categorie}


def _analyser_noeud_id(noeud_id: str) -> tuple[str, int] | None:
    type_noeud, _, brut = str(noeud_id or "").partition(":")
    if type_noeud not in TYPES_SUPPORTES or not brut.isdigit():
        return None
    return type_noeud, int(brut)


def resoudre_destinations(entreprise, noeud_id: str, membre=None) -> dict:
    """Destinations navigables depuis `noeud_id` (ex. "table:5").

    Retourne {noeud_id, supporte, destinations:[{noeud_id,type,id,label,relation,categorie,url,
    via?}], liens_fantomes:[{source,cible,raison}]}. Niveau 1 = liaisons directes ; niveau 2 =
    ce qui est atteignable en passant par la table de rattachement (ex. depuis un champ :
    les KPI et modèles de sa table), marqué `via`. Un membre « lecteur » (client) n'obtient
    aucune destination : son espace dédié reste sa seule porte d'entrée."""
    base = {"noeud_id": noeud_id, "supporte": False, "destinations": [], "liens_fantomes": []}
    analyse = _analyser_noeud_id(noeud_id)
    if analyse is None:
        return base
    base["supporte"] = True
    if membre is not None and membre.role == "lecteur":
        return base

    type_noeud, obj_id = analyse
    fantomes: list[dict] = []
    vus = {noeud_id}
    destinations: list[dict] = []

    def _ajouter(n: dict, via: str | None = None) -> None:
        if n["noeud_id"] in vus:
            return
        d = _destination(entreprise, membre, n)
        if d is None:
            return
        vus.add(n["noeud_id"])
        if via:
            d["via"] = via
        destinations.append(d)

    directs = _voisins(entreprise, type_noeud, obj_id, fantomes)
    for n in directs:
        _ajouter(n)

    # Niveau 2 : à travers la table de rattachement (sauf si l'objet EST la table).
    if type_noeud != "table":
        table = _table_de(entreprise, type_noeud, obj_id)
        if table is not None and _table_ok(membre, table.id):
            _ajouter(_noeud("table", table.id, table.nom, "table_liee"))
            for n in _voisins(entreprise, "table", table.id, fantomes):
                _ajouter(n, via=table.nom)

    # Toujours proposer de revoir l'objet dans l'ontologie (retour de la toile).
    url_onto = _url("vue_ontologie", query=f"noeud={noeud_id}")
    base["ontologie_url"] = url_onto

    # dédoublonnage des fantômes (un même objet peut être visité deux fois)
    uniques, cles = [], set()
    for f in fantomes:
        cle = (f["source"], f["cible"], f["raison"])
        if cle not in cles:
            cles.add(cle)
            uniques.append(f)
    base["destinations"] = destinations
    base["liens_fantomes"] = uniques
    return base


def explorer_toile(entreprise, noeud_id: str, membre=None, profondeur: int = 2, limite: int = 150) -> dict:
    """Parcours en largeur de la « toile » de données liées autour de `noeud_id` : renvoie
    {noeuds:[{noeud_id,type,label,url?,niveau}], liens:[{source,cible,relation}], tronque}.
    Sert à cartographier d'un coup ce qui est atteignable (table ↔ dashboards ↔ colonnes ↔
    modèles IA ↔ KPI...) sans charger la page Ontologie complète. Bornée par `profondeur`
    (max PROFONDEUR_MAX) et `limite` de nœuds."""
    profondeur = max(1, min(int(profondeur or 1), PROFONDEUR_MAX))
    limite = max(1, min(int(limite or 1), 500))
    racine = _analyser_noeud_id(noeud_id)
    if racine is None or (membre is not None and membre.role == "lecteur"):
        return {"noeuds": [], "liens": [], "liens_fantomes": [], "tronque": False}

    fantomes: list[dict] = []
    noeuds: dict[str, dict] = {noeud_id: {"noeud_id": noeud_id, "type": racine[0], "niveau": 0}}
    liens, file, tronque = [], deque([(noeud_id, racine, 0)]), False
    while file:
        courant_id, (type_noeud, obj_id), niveau = file.popleft()
        if niveau >= profondeur:
            continue
        for n in _voisins(entreprise, type_noeud, obj_id, fantomes):
            d = _destination(entreprise, membre, n)
            if d is None:
                continue
            liens.append({"source": courant_id, "cible": n["noeud_id"], "relation": n["relation"]})
            if n["noeud_id"] in noeuds:
                continue
            if len(noeuds) >= limite:
                tronque = True
                continue
            noeuds[n["noeud_id"]] = {"noeud_id": n["noeud_id"], "type": n["type"], "label": n["label"],
                                     "url": d["url"], "categorie": d["categorie"], "niveau": niveau + 1}
            file.append((n["noeud_id"], (n["type"], n["id"]), niveau + 1))
    uniques, cles = [], set()
    for f in fantomes:
        cle = (f["source"], f["cible"], f["raison"])
        if cle not in cles:
            cles.add(cle)
            uniques.append(f)
    return {"noeuds": list(noeuds.values()), "liens": liens, "liens_fantomes": uniques, "tronque": tronque}


def _construire_graphe_dependances(entreprise) -> dict:
    """Graphe de dépendances de TOUTE l'entreprise, construit en bloc (une requête par type
    d'objet, jamais une par nœud) : {noeuds:{noeud_id:{type,label,table_id}}, aretes:[{source,
    cible,relation}]}. Le sens d'une arête est « dépend de » : source = ce dont on dépend,
    cible = ce qui en dépend (table → dashboard, table → modèle IA, colonne → graphique...).
    Fantôme : recalculé à chaque appel depuis les FK/M2M, rien n'est stocké. Sert de « toile »
    au moteur de décision (engine/decision.py)."""
    noeuds: dict[str, dict] = {}
    aretes: list[dict] = []
    vues: set = set()

    # Groupe fusionné en une table (groupe_fusion.py) : la table fusionnée remplace ses parties dans le graphe. Les parties n'ont pas de nœud ;
    # ce qui s'y rattachait (dashboards, KPI, calculs, modèles, colonnes tracées) est rattaché à la table fusionnée (colonne de même slug).
    toutes = list(TableDynamique.objects.filter(entreprise=entreprise))
    fusionnees = {t.groupe_fusionne_id: t.id for t in toutes if t.groupe_fusionne_id}
    cachees = {t.id: fusionnees[t.groupe_id] for t in toutes if t.groupe_id in fusionnees}   # id partie → id table fusionnée
    tables = [t for t in toutes if t.id not in cachees]
    champ_remplace = {}
    if cachees:
        slugs_fusion = {(c.table_id, c.slug): c.id for c in ChampDynamique.objects.filter(table_id__in=set(cachees.values())).only("id", "slug", "table_id")}
        for c in ChampDynamique.objects.filter(table_id__in=list(cachees)).only("id", "slug", "table_id"):
            cible_champ = slugs_fusion.get((cachees[c.table_id], c.slug))
            if cible_champ:
                champ_remplace[c.id] = cible_champ

    def _r(noeud_id):
        type_noeud, _, brut = noeud_id.partition(":")
        if type_noeud == "table" and brut.isdigit():
            return f"table:{cachees.get(int(brut), int(brut))}"
        if type_noeud == "champ" and brut.isdigit():
            return f"champ:{champ_remplace.get(int(brut), int(brut))}"
        return noeud_id

    def _n(type_noeud, obj, label, table_id=None):
        noeuds[f"{type_noeud}:{obj}"] = {"type": type_noeud, "id": obj, "label": label, "table_id": cachees.get(table_id, table_id)}

    def _a(source, cible, relation):
        source, cible = _r(source), _r(cible)
        cle = (source, cible, relation)
        if source in noeuds and cible in noeuds and cle not in vues:   # plusieurs parties → une seule table fusionnée : pas d'arête en double
            vues.add(cle)
            aretes.append({"source": source, "cible": cible, "relation": relation})

    for t in tables:
        _n("table", t.id, t.nom, t.id)
    for c in ChampDynamique.objects.filter(table__entreprise=entreprise).only("id", "nom", "table_id", "relation_vers_id"):
        if c.table_id in cachees:
            continue
        _n("champ", c.id, c.nom, c.table_id)
    for c in ChampDynamique.objects.filter(table__entreprise=entreprise, relation_vers__isnull=False).only("id", "table_id", "relation_vers_id"):
        _a(f"table:{c.relation_vers_id}", f"table:{c.table_id}", "reference")
    for nid, n in list(noeuds.items()):
        if n["type"] == "champ":
            _a(f"table:{n['table_id']}", nid, "colonne")

    for tb in TableauBordBI.objects.filter(entreprise=entreprise):
        _n("dashboard", tb.id, tb.nom, tb.table_source_id)
        _a(f"table:{tb.table_source_id}", f"dashboard:{tb.id}", "affiche")
    for w_id, tb_id, champ_id in (WidgetGraphiqueBI.objects
                                  .filter(tableau_bord__entreprise=entreprise, type_widget="graphique")
                                  .values_list("id", "tableau_bord_id", "champs_cibles")):
        if champ_id:
            _a(f"champ:{champ_id}", f"dashboard:{tb_id}", "trace")
    for k in IndicateurKPI.objects.filter(entreprise=entreprise):
        _n("kpi", k.id, k.nom, k.table_source_id)
        _a(f"table:{k.table_source_id}", f"kpi:{k.id}", "mesure")
        if k.champ_cible_id:
            _a(f"champ:{k.champ_cible_id}", f"kpi:{k.id}", "mesure_champ")
    for c in TableCalcul.objects.filter(entreprise=entreprise):
        _n("calcul", c.id, c.nom, c.table_source_id)
        _a(f"table:{c.table_source_id}", f"calcul:{c.id}", "calcule")
    for s_ in Skill.objects.filter(entreprise=entreprise):
        _n("skill", s_.id, s_.nom, s_.table_id)
        _a(f"table:{s_.table_id}", f"skill:{s_.id}", "genere")
    for m in ModeleIA.objects.filter(entreprise=entreprise).prefetch_related("tables_source"):
        tables_m = [t.id for t in m.tables_source.all()]
        _n("modele_ia", m.id, m.nom, tables_m[0] if tables_m else None)
        for tid in tables_m:
            _a(f"table:{tid}", f"modele_ia:{m.id}", "entraine")
    return {"noeuds": noeuds, "aretes": aretes}


CACHE_GRAPHE_SECONDES = 60


def graphe_dependances(entreprise, forcer: bool = False) -> dict:
    """Graphe de dépendances de l'entreprise, gardé 60 s en cache (comme l'état fusionné,
    engine/fusion.py, avec lequel le moteur de décision le lit). `forcer=True` l'ignore."""
    from django.core.cache import cache
    cle = f"portail_graphe:{entreprise.id}"
    if not forcer:
        en_cache = cache.get(cle)
        if en_cache is not None:
            return en_cache
    graphe = _construire_graphe_dependances(entreprise)
    cache.set(cle, graphe, CACHE_GRAPHE_SECONDES)
    return graphe
