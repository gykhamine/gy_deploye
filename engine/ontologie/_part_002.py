from engine.i18n import tf
from engine.i18n import t
from engine.i18n import tfr
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ..ontologie_systeme import TYPE_CHAMP_SYSTEME, type_noeud_table  # noqa: E402
from engine.i18n import t as _gt  # noqa: E402


def _poids_lien(champ) -> int:
    """Nombre de cellules renseignées de la colonne `champ` (relation) de SA table, lu dans le fichier Parquet."""
    from .. import parquet_store
    if champ.table.est_nosql or getattr(champ.table, "source_systeme", ""):   # table système : lue en entier dans le code (lent), pas de poids de lien
        return 0
    df = parquet_store.donnees_analytiques(champ.table, groupe=False, colonnes=[champ.slug])
    if champ.slug not in df.columns:
        return 0
    return parquet_store.comptes_colonne(df[champ.slug])[0]


def _cellules_texte(tables, plafond, cache=None) -> list:
    """[(table_id, champ_id, texte)] : au plus `plafond` cellules renseignées des tables dynamiques (EAV) `tables`, lues dans leur
    fichier Parquet (une table = un fichier, jamais le groupe : l'appartenance est celle de la table physique).
    Les tables système (wallet, messagerie, audit…) sont ignorées : elles sont reconstruites ligne à ligne depuis le code à chaque
    lecture (très lent, bloque le serveur) et leurs valeurs ne portent pas de similarité utile. `cache` ({table_id: DataFrame}) évite
    de relire deux fois la même table pendant un même calcul de graphe. Sans plafond, le résultat de chaque table est AUSSI gardé en
    mémoire d'une construction à l'autre (voir _memo_lire) tant que la table n'a pas changé : une table inchangée n'est plus relue."""
    from .. import parquet_store
    sortie = []
    memoisable = plafond is None and cache is not None
    for table in tables:
        if table.est_nosql or getattr(table, "source_systeme", ""):
            continue
        signature = None
        if memoisable:
            if "__signatures__" not in cache:
                cache["__signatures__"] = _signatures_tables(table.entreprise_id)
            signature = cache["__signatures__"].get(table.id)
            memo = _memo_lire("cellules", table.id, signature)
            if memo is not None:
                sortie.extend(memo)
                _respirer()
                continue
        debut = len(sortie)
        if cache is not None and table.id in cache:
            df = cache[table.id]
        else:
            df = parquet_store.donnees_analytiques(table, groupe=False)
            if cache is not None:
                cache[table.id] = df
        for champ in table.champs.all():
            if champ.slug not in df.columns:
                continue
            reste = None if plafond is None else plafond - len(sortie)   # None = aucune limite de cellules
            if reste is not None and reste <= 0:
                return sortie
            sortie.extend((table.id, champ.id, texte) for texte in parquet_store.textes_colonne(df[champ.slug], reste))
            _respirer()
        if memoisable:
            _memo_ecrire("cellules", table.id, signature, sortie[debut:], len(sortie) - debut)
    return sortie


def _construire_graphe_calcul(entreprise, page=None, membre=None) -> "nx.DiGraph":
    """Construit le graphe orienté des données de l'entreprise. Les nœuds
    « table » portent leur volume réel d'enregistrements (nb_enregistrements)
    et les arêtes « lien » leur volume réel de valeurs renseignées
    (poids) : le graphe reflète le comportement effectif des données, pas
    seulement la structure déclarée.

    `page` (models.PageOntologie, optionnel) : si fourni, seules les
    CoucheOntologie explicitement rattachées à cette page (via
    CoucheOntologie.pages) s'appliquent, en plus des couches prédéfinies
    (toujours globales). Si `page` est None, comportement historique :
    TOUTES les couches actives de l'entreprise s'appliquent (utile pour un
    graphe "vue d'ensemble" ou tant qu'aucune page n'a été créée).

    `membre` (models.MembreEntreprise, optionnel) : si fourni ET que
    membre.role == "agent", le graphe est restreint à SES tables (celles où
    il a effectivement créé au moins une ligne, Enregistrement.cree_par) —
    un agent ne doit jamais voir la carte de données d'une autre entreprise
    NI celle des tables d'autres agents. Toutes les enrichissements en
    aval (relations, calculs, skills, similarités, échantillons de lignes…)
    héritent automatiquement de cette restriction car ils ne s'accrochent
    qu'à des nœuds table déjà présents dans `g`. Sans `membre` (ou pour un
    admin), comportement historique : toutes les tables de l'entreprise."""
    from django.db.models import Count
    from ..models import (
        ChampDynamique, CompteurTokens, ConfigurationIA, DemandeAgent, DocumentGenere,
        Enregistrement, GroupePartage, IndicateurKPI, JournalAudit, LienChamp, LigneCalcul,
        MembreEntreprise, ModeleIA, Skill, TableauBordBI, TableCalcul,
    )

    g = nx.DiGraph()
    racine = f"entreprise:{entreprise.id}"
    g.add_node(racine, type="entreprise", label=entreprise.nom)

    tables_qs = entreprise.tables.all()
    if membre is not None and membre.role == "agent":
        from .. import propriete_tables as _propriete
        ids_autorisees = set(
            _propriete.filtrer_lignes(Enregistrement.objects.filter(table__entreprise=entreprise), membre.utilisateur)
            .values_list("table_id", flat=True).distinct()
        ) | set(membre.utilisateur.tables_possedees.values_list("table_id", flat=True))
        tables_qs = tables_qs.filter(id__in=ids_autorisees)

    # Nombre de lignes par table : UNE requête groupée au lieu d'un COUNT par table.
    nb_lignes_table = dict(
        Enregistrement.objects.filter(table__entreprise=entreprise)
        .values("table_id").order_by().annotate(n=Count("id")).values_list("table_id", "n")
    )
    # Tables système (wallet, paiement, messagerie, audit…) : un type par domaine (donc une couleur par domaine), rattachées à
    # l'entreprise par un nœud « domaine » et non une par une ; leurs colonnes forment un type discret — voir ontologie_systeme.
    systeme_ids = set()
    for tbl in tables_qs.select_related("connexion_externe_origine"):
        noeud_table = f"table:{tbl.id}"
        type_table = type_noeud_table(tbl.source_systeme)
        g.add_node(noeud_table, type=type_table, label=tbl.nom, slug=tbl.slug,
                   nb_enregistrements=nb_lignes_table.get(tbl.id, 0),
                   est_table_membres=tbl.est_table_membres, **_provenance_table(tbl))
        if type_table == "table":
            g.add_edge(racine, noeud_table, relation="possede")
        else:
            systeme_ids.add(tbl.id)

    # ── Colonnes (ChampDynamique) : un nœud par colonne des tables visibles
    # ci-dessus, socle des relations colonne↔colonne ajoutées plus bas (même
    # nom, valeurs similaires, résultat de calcul).
    for c in ChampDynamique.objects.filter(table__in=tables_qs):
        noeud_champ = f"champ:{c.id}"
        if c.table_id in systeme_ids:
            g.add_node(noeud_champ, type=TYPE_CHAMP_SYSTEME, label=c.nom, table_id=c.table_id, taille=7)
        else:
            g.add_node(noeud_champ, type="champ", label=c.nom, table_id=c.table_id)
        g.add_edge(f"table:{c.table_id}", noeud_champ, relation="colonne")

    champs_relation = ChampDynamique.objects.filter(
        table__entreprise=entreprise, relation_vers__isnull=False
    ).select_related("table", "relation_vers")
    for c in champs_relation:
        if g.has_node(f"table:{c.relation_vers_id}"):
            poids = _poids_lien(c)
            g.add_edge(f"table:{c.table_id}", f"table:{c.relation_vers_id}",
                       relation="lien", champ=c.nom, poids=poids)

    config = ConfigurationIA.objects.filter(entreprise=entreprise).first()
    if config:
        noeud_ia = f"config_ia:{config.id}"
        g.add_node(noeud_ia, type="config_ia", label=tfr("engine/ontologie/_part_002.py:76:55"),
                   label_cle="engine/ontologie/_part_002.py:76:55", label_vars={})
        g.add_edge(racine, noeud_ia, relation="configure")

    nb_lignes_calcul = dict(
        LigneCalcul.objects.filter(table_calcul__entreprise=entreprise)
        .values("table_calcul_id").order_by().annotate(n=Count("id")).values_list("table_calcul_id", "n")
    )
    for tc in TableCalcul.objects.filter(entreprise=entreprise):
        noeud_calc = f"calcul:{tc.id}"
        g.add_node(noeud_calc, type="calcul", label=tc.nom,
                   nb_lignes=nb_lignes_calcul.get(tc.id, 0))
        g.add_edge(f"table:{tc.table_source_id}", noeud_calc, relation="calcule")

    for s in Skill.objects.filter(entreprise=entreprise):
        noeud_skill = f"skill:{s.id}"
        g.add_node(noeud_skill, type="skill", label=s.nom, statut=s.statut)
        g.add_edge(f"table:{s.table_id}", noeud_skill, relation="genere")


    # ── Agents (membres role=agent) ──────────────────────────────────────────
    for m in MembreEntreprise.objects.filter(entreprise=entreprise, role="agent").select_related("utilisateur"):
        nid = f"agent:{m.id}"
        g.add_node(nid, type="agent", label=m.utilisateur.get_full_name() or m.utilisateur.username)
        g.add_edge(racine, nid, relation="emploie")

    # ── Admins (membres role=admin) ───────────────────────────────────────────
    for m in MembreEntreprise.objects.filter(entreprise=entreprise, role="admin").select_related("utilisateur"):
        nid = f"admin:{m.id}"
        g.add_node(nid, type="admin", label=m.utilisateur.get_full_name() or m.utilisateur.username)
        g.add_edge(racine, nid, relation="emploie")

    # ── Clients (membres role=lecteur liés à une table cliente) ──────────────
    # Chaque client a desormais son propre code d'acces (Enregistrement.
    # code_acces_client, plus de code unique partage par toute la table) :
    # le nœud client est donc relie directement a SA ligne precise quand
    # elle est connue (enregistrement_client), ce qui permet de le
    # reconnaitre sans ambiguite dans le graphe — et pas seulement comme
    # "un client parmi d'autres" de la table.
    for m in MembreEntreprise.objects.filter(entreprise=entreprise, role="lecteur", table_cliente__isnull=False).select_related("utilisateur", "table_cliente", "enregistrement_client"):
        nid = f"client:{m.id}"
        g.add_node(nid, type="client", label=m.utilisateur.username)
        if m.enregistrement_client_id and g.has_node(f"enregistrement:{m.enregistrement_client_id}"):
            g.add_edge(f"enregistrement:{m.enregistrement_client_id}", nid, relation="identifie")
        elif g.has_node(f"table:{m.table_cliente_id}"):
            g.add_edge(f"table:{m.table_cliente_id}", nid, relation="identifie")

    # ── Modèles IA et leurs tables sources ───────────────────────────────────
    for mod in ModeleIA.objects.filter(entreprise=entreprise).prefetch_related("tables_source"):
        nid = f"modele_ia:{mod.id}"
        nb_inf = mod.inferences.count()
        g.add_node(nid, type="modele_ia", label=mod.nom, nb_lignes=nb_inf)
        for ts in mod.tables_source.all():
            if g.has_node(f"table:{ts.id}"):
                g.add_edge(f"table:{ts.id}", nid, relation="entraine")

    # ── Documents générés et leurs tables sources ─────────────────────────────
    for d in DocumentGenere.objects.filter(entreprise=entreprise).select_related("table_cible"):
        nid = f"document:{d.id}"
        if not g.has_node(nid):
            g.add_node(nid, type="document", label=d.titre or d.type_document)
        if d.table_cible_id and g.has_node(f"table:{d.table_cible_id}"):
            g.add_edge(f"table:{d.table_cible_id}", nid, relation="produit")


    # ── Colonnes partagées (GroupePartage) : synchronisation inter-tables ────
    for gp in GroupePartage.objects.filter(entreprise=entreprise).prefetch_related("champs__table"):
        tables_gp = {c.table_id for c in gp.champs.all() if g.has_node(f"table:{c.table_id}")}
        tables_gp = list(tables_gp)
        for i in range(len(tables_gp)):
            for j in range(i + 1, len(tables_gp)):
                g.add_edge(f"table:{tables_gp[i]}", f"table:{tables_gp[j]}",
                           relation="partage", groupe=gp.nom or f"groupe#{gp.pk}")

    # ── LienChamp : enregistrements effectivement liés entre tables ──────────
    liens_counts: dict = {}
    # UNE requête groupée (table source, table cible, nombre de liens) au lieu de charger chaque lien avec ses jointures.
    for src, dst, n_liens in (LienChamp.objects.filter(valeur_enfant__enregistrement__table__entreprise=entreprise)
                              .values("champ_source__table_id", "valeur_enfant__enregistrement__table_id").order_by()
                              .annotate(n=Count("id")).values_list("champ_source__table_id", "valeur_enfant__enregistrement__table_id", "n")):
        if src and src != dst:
            key = (f"table:{src}", f"table:{dst}")
            liens_counts[key] = liens_counts.get(key, 0) + n_liens
    for (src, dst), cnt in liens_counts.items():
        if g.has_node(src) and g.has_node(dst):
            if g.has_edge(src, dst):
                g[src][dst]["poids"] = g[src][dst].get("poids", 0) + cnt
            else:
                g.add_edge(src, dst, relation="lien_effectif", poids=cnt)

    # ── Co-occurrence temporelle (fenêtre 5 min entre tables) ────────────────
    from bisect import bisect_left
    from django.utils import timezone; from datetime import timedelta
    from django.db.models import Min, Max
    ids_tables = [n.split(":")[1] for n in g.nodes if n.startswith("table:")]
    signatures = _signatures_tables(entreprise.id)   # (nb de lignes, dernier id, dernière modification) par table : ce qui dit « cette table a changé »
    activite = {}

    def _activite_de(tid):
        if tid not in activite:
            activite[tid] = sorted(Enregistrement.objects.filter(table_id=tid).values_list("cree_le", flat=True).order_by("cree_le"))
            _respirer()
        return activite[tid]

    actifs = [tid for tid in ids_tables if g.nodes[f"table:{tid}"].get("nb_enregistrements")]   # aucune ligne : rien à lire (tables système, tables vides)
    for i, t1 in enumerate(actifs):
        for t2 in actifs[i+1:]:
            sig_paire = (signatures.get(int(t1)), signatures.get(int(t2)))
            overlap = _memo_lire("cooc", (t1, t2), sig_paire)   # deux tables inchangées : le recouvrement n'est pas recompté
            if overlap is None:
                set1, set2 = _activite_de(t1), _activite_de(t2)
                overlap = 0
                if set1 and set2:
                    for ts in set1:
                        lo, hi = ts - timedelta(minutes=5), ts + timedelta(minutes=5)
                        i_lo = bisect_left(set2, lo)   # set2 est trié : une recherche dichotomique remplace le balayage complet
                        if i_lo < len(set2) and set2[i_lo] <= hi: overlap += 1
                        _respirer()
                _memo_ecrire("cooc", (t1, t2), sig_paire, overlap, 1)
            if overlap >= 3:
                g.add_edge(f"table:{t1}", f"table:{t2}", relation="co_occurrence", poids=overlap)

    # ── Similarité de valeurs entre colonnes de tables différentes ───────────
    from collections import defaultdict
    _cache_df: dict = {}   # une seule lecture Parquet par table pour les deux balayages de similarité
    index_valeurs: dict = defaultdict(set)
    for table_id, _champ_id, texte in _cellules_texte(entreprise.tables.all().prefetch_related("champs"), None, _cache_df):   # fichiers Parquet des tables dynamiques
        v = texte.strip().lower()
        if len(v) >= 3: index_valeurs[v].add(table_id)
        _respirer()
    paires_sim: dict = defaultdict(int)
    for tids in index_valeurs.values():
        _respirer()
        lst = sorted(tids)
        for i in range(len(lst)):
            for j in range(i+1, len(lst)):
                paires_sim[(lst[i], lst[j])] += 1
    for (t1, t2), score in paires_sim.items():
        if score >= 5 and g.has_node(f"table:{t1}") and g.has_node(f"table:{t2}"):
            if not g.has_edge(f"table:{t1}", f"table:{t2}"):
                g.add_edge(f"table:{t1}", f"table:{t2}", relation="similarite", poids=score)


    # ── Champ→Calcul : LigneCalcul relie une colonne précise à son résultat
    # (nœud champ directement, plus seulement la table qui le contient). ────
    for lc in LigneCalcul.objects.filter(table_calcul__entreprise=entreprise).select_related("champ_cible__table", "table_calcul"):
        if lc.champ_cible_id and g.has_node(f"champ:{lc.champ_cible_id}") and g.has_node(f"calcul:{lc.table_calcul_id}"):
            g.add_edge(f"champ:{lc.champ_cible_id}", f"calcul:{lc.table_calcul_id}",
                       relation="champ_calcul", champ=lc.champ_cible.nom, formule=lc.formule_id)

    # ── Calcul→ModèleIA : même table source → résultat calcul alimente le modèle
    for mod in ModeleIA.objects.filter(entreprise=entreprise).prefetch_related("tables_source"):
        for ts in mod.tables_source.all():
            for tc in TableCalcul.objects.filter(table_source=ts):
                if g.has_node(f"calcul:{tc.id}") and g.has_node(f"modele_ia:{mod.id}"):
                    g.add_edge(f"calcul:{tc.id}", f"modele_ia:{mod.id}", relation="inference")

    # ── Fréquence client : JournalAudit — nb actions par membre sur chaque table
    for row in JournalAudit.objects.filter(entreprise=entreprise).values("utilisateur_id").annotate(nb=Count("id")).order_by():
        uid = row["utilisateur_id"]; nb = row["nb"]
        m = MembreEntreprise.objects.filter(entreprise=entreprise, utilisateur_id=uid, role="lecteur").first()
        if m and g.has_node(f"client:{m.id}") and m.table_cliente_id and g.has_node(f"table:{m.table_cliente_id}"):
            if g.has_edge(f"table:{m.table_cliente_id}", f"client:{m.id}"):
                g[f"table:{m.table_cliente_id}"][f"client:{m.id}"]["poids"] = nb
            else:
                g.add_edge(f"table:{m.table_cliente_id}", f"client:{m.id}", relation="frequence", poids=nb)

    # ── Fréquence entraînement : ModèleIA → nb inférences comme poids ────────
    for mod in ModeleIA.objects.filter(entreprise=entreprise).annotate(_nb_inferences=Count("inferences")):
        nid = f"modele_ia:{mod.id}"
        if g.has_node(nid):
            g.nodes[nid]["nb_entrainements"] = 1 if mod.entraine_le else 0
            g.nodes[nid]["nb_inferences"] = mod._nb_inferences

    # ── Similarité champ-à-champ (valeurs brutes partagées entre colonnes) ───
    # Relie directement les nœuds champ (pas seulement les tables) : c'est la
    # vraie relation "ces deux colonnes contiennent souvent les mêmes
    # valeurs", utile même quand deux tables partagent plusieurs colonnes à
    # la fois (chacune a sa propre arête, au lieu d'une seule arête
    # table↔table qui les confondait).
    from collections import defaultdict
    index_champ: dict = defaultdict(set)
    for table_id, champ_id, texte in _cellules_texte(tables_qs.prefetch_related("champs"), None, _cache_df):   # fichiers Parquet des tables dynamiques
        v = texte.strip().lower()
        if len(v) >= 3: index_champ[v].add((table_id, champ_id))
        _respirer()
    paires_champ: dict = defaultdict(int)
    for pairs in index_champ.values():
        _respirer()
        lst = sorted(pairs)
        for i in range(len(lst)):
            for j in range(i+1, len(lst)):
                if lst[i][0] != lst[j][0]: paires_champ[(lst[i], lst[j])] += 1
    for ((t1, c1), (t2, c2)), score in paires_champ.items():
        if score >= 3 and g.has_node(f"champ:{c1}") and g.has_node(f"champ:{c2}"):
            g.add_edge(f"champ:{c1}", f"champ:{c2}", relation="sim_champ", poids=score)

    # ── Colonnes de même nom (relation de schéma, indépendante du contenu) ──
    # Deux colonnes qui s'appellent pareil dans deux tables différentes sont
    # reliées même si elles n'ont encore aucune valeur en commun (utile pour
    # repérer d'emblée les colonnes candidates à un rapprochement, ex.
    # "email" présent dans plusieurs tables).
    noms_champs: dict = defaultdict(list)
    for c in ChampDynamique.objects.filter(table__in=tables_qs).only("id", "nom", "table_id"):
        if c.table_id in systeme_ids:
            continue   # « Identifiant », « Créé le »… existent dans toutes les tables système : des milliers de liens sans information
        noms_champs[c.nom.strip().lower()].append((c.table_id, c.id))
    for occurrences in noms_champs.values():
        for i in range(len(occurrences)):
            for j in range(i + 1, len(occurrences)):
                (t1, c1), (t2, c2) = occurrences[i], occurrences[j]
                if t1 != t2 and g.has_node(f"champ:{c1}") and g.has_node(f"champ:{c2}") \
                        and not g.has_edge(f"champ:{c1}", f"champ:{c2}"):
                    g.add_edge(f"champ:{c1}", f"champ:{c2}", relation="meme_nom")

    # ── Dashboards BI (TableauBordBI) ─────────────────────────────────────
    for tb in TableauBordBI.objects.filter(entreprise=entreprise):
        nid = f"dashboard:{tb.id}"
        g.add_node(nid, type="dashboard", label=tb.nom, nb_lignes=tb.widgets.count())
        if g.has_node(f"table:{tb.table_source_id}"):
            g.add_edge(f"table:{tb.table_source_id}", nid, relation="affiche")

    # ── KPI (IndicateurKPI) ────────────────────────────────────────────────
    for kpi in IndicateurKPI.objects.filter(entreprise=entreprise):
        nid = f"kpi:{kpi.id}"
        g.add_node(nid, type="kpi", label=kpi.nom, taille=12 if kpi.statut != "pret" else None,
                   valeur=kpi.valeur_actuelle)
        if g.has_node(f"table:{kpi.table_source_id}"):
            g.add_edge(f"table:{kpi.table_source_id}", nid, relation="mesure")

    # ── Permissions (MembreEntreprise.vues_autorisees) : un nœud par vue
    # distincte accordée à au moins un agent de l'entreprise, relié à chaque
    # agent qui en dispose — rôle admin/lecteur non concernés (voir modèle).
    for m in MembreEntreprise.objects.filter(entreprise=entreprise, role="agent").exclude(vues_autorisees__isnull=True):
        agent_nid = f"agent:{m.id}"
        if not g.has_node(agent_nid) or not m.vues_autorisees:
            continue
        for vue in m.vues_autorisees:
            perm_nid = f"permission:{entreprise.id}:{vue}"
            if not g.has_node(perm_nid):
                g.add_node(perm_nid, type="permission", label=vue)
            g.add_edge(agent_nid, perm_nid, relation="autorise")

    # ── Consommation de tokens (CompteurTokens) : un nœud par compteur,
    # taille proportionnelle au volume cumulé sur la période en cours.
    for ct in CompteurTokens.objects.filter(entreprise=entreprise):
        nid = f"tokens:{ct.id}"
        label = ct.utilisateur.get_full_name() or ct.utilisateur.username if ct.utilisateur_id else "Consommation globale"
        g.add_node(nid, type="tokens", label=label, taille=10 + min(ct.tokens_total, 500000) ** 0.5 * 0.05,
                   tokens_total=ct.tokens_total, periode=ct.periode, alerte_active=ct.alerte_active)
        source = None
        if ct.utilisateur_id:
            m = MembreEntreprise.objects.filter(entreprise=entreprise, utilisateur_id=ct.utilisateur_id, role="agent").first()
            if m and g.has_node(f"agent:{m.id}"):
                source = f"agent:{m.id}"
        g.add_edge(source or racine, nid, relation="consomme")

    # ── Enregistrements individuels (échantillon récent, voir
    # LIMITE_ENREGISTREMENTS_GRAPHE) : reflète le contenu réel d'une table,
    # pas seulement son volume — utile pour repérer une ligne précise sans
    # ouvrir la table. Chaque ligne est aussi reliée à qui l'a créée
    # (agent/admin/client), avec la date de création (et de dernière
    # modification si elle diffère) — source directe cree_par/cree_le/maj_le
    # d'Enregistrement (pas le JournalAudit, qui trace des actions plus
    # larges et pas forcément liées à une ligne précise).
    prefixe_role = {"agent": "agent", "admin": "admin", "lecteur": "client"}
    membres_par_utilisateur = {
        m.utilisateur_id: m for m in MembreEntreprise.objects.filter(entreprise=entreprise)
    }
    for tbl in tables_qs:
        noeud_table = f"table:{tbl.id}"
        if not g.has_node(noeud_table):
            continue
        if not g.nodes[noeud_table].get("nb_enregistrements"):   # table sans ligne : aucune requête à faire
            continue
        # Toute la table entre dans le graphe : aucun plafond à la construction (le réglage ConfigurationIA.limite_enregistrements_ontologie
        # n'est plus lu ici). Seul l'AFFICHAGE est borné (ontologie_apercu.LIMITE_APERCU) ; le graphe complet reste intact.
        limite = 0
        agent = membre is not None and membre.role == "agent"
        cle_memo = (tbl.id, membre.id if agent else None, limite)
        lignes = _memo_lire("lignes", cle_memo, signatures.get(tbl.id))   # table inchangée : ni requête ni lecture Parquet
        if lignes is None:
            qs = tbl.enregistrements.order_by("-cree_le")
            if agent:
                # Cohérent avec _isoler_lignes_agent (views.py) : dans une table
                # partagée, un agent ne voit que SES propres lignes, jamais
                # celles créées par un autre agent.
                from .. import propriete_tables as _propriete
                qs = _propriete.filtrer_lignes(qs, membre.utilisateur)
            recents = list(qs if limite == 0 else qs[:limite])
            if len(recents) <= 50:   # peu de lignes : valeurs préchargées en une requête ; au-delà : lues en une fois dans le fichier Parquet
                from django.db.models import prefetch_related_objects
                prefetch_related_objects(recents, "valeurs__champ")
            from .. import parquet_store as _pq
            lignes = []
            for e, valeurs in zip(recents, _pq.valeurs_lignes(tbl, recents)):
                premiere_valeur = next((str(v) for v in valeurs.values() if v not in (None, "")), None)
                lignes.append((e.id, e.matricule or premiere_valeur or f"#{e.id}", e.cree_par_id,
                               e.cree_le.isoformat() if e.cree_le else None,
                               e.maj_le.isoformat() if e.maj_le and e.maj_le != e.cree_le else None))
                _respirer()
            _memo_ecrire("lignes", cle_memo, signatures.get(tbl.id), lignes, len(lignes) * 5)
        for eid, label, cree_par_id, date_creation, modifie in lignes:
            nid = f"enregistrement:{eid}"
            g.add_node(nid, type="enregistrement", label=label, taille=6)
            g.add_edge(noeud_table, nid, relation="contient")

            mc = membres_par_utilisateur.get(cree_par_id) if cree_par_id else None
            if mc is not None:
                nid_personne = f"{prefixe_role.get(mc.role, 'agent')}:{mc.id}"
                if g.has_node(nid_personne):
                    g.add_edge(nid_personne, nid, relation="operation",
                               date=date_creation, modifie_le=modifie, role=mc.role)
            _respirer()

    # ── Blockchain (voir models.Bloc) : chaque bloc validé authentifie les
    # événements qu'il contient (création de table, création/modification/
    # suppression de ligne — y compris les lignes déjà supprimées, jamais
    # retirées de la chaîne). Un seul fil par entreprise : l'admin voit tous
    # les blocs, un agent ne voit que les événements touchant SES propres
    # nœuds (déjà filtrés ci-dessus) — même isolation que le reste du
    # graphe, sans chaîne séparée par agent ni par table.
    #
    # Au-delà du seul nœud "bloc", 5 nœuds supplémentaires cartographient la
    # blockchain plus finement (voir DESCRIPTIONS_TYPES) :
    #   - chaine_blocs         : la chaîne elle-même (1 par entreprise)
    #   - autorite_validation  : l'autorité Proof-of-Authority (1 par entreprise)
    #   - transaction          : chaque transaction individuelle d'un bloc
    #   - preuve_authenticite  : certificat délivré pour une ligne cliente
    #   - rupture_chaine       : anomalie d'intégrité entre deux blocs
    # reliés par 10 nouvelles relations (voir DESCRIPTIONS_RELATIONS).
    from ..models import Bloc
    Bloc.combler_lacunes(entreprise.id, intervalle_minimale=600)  # filet de sécurité (voir models.Bloc) : relit toute la chaîne, donc au plus une fois / 10 min

    nid_chaine = f"chaine:{entreprise.id}"
    nid_autorite = f"autorite:{entreprise.id}"
    # Ligne → client qui l'incarne (voir la boucle "Clients" plus haut) :
    # sert à relier chaque preuve d'authenticité au bon client.
    clients_par_enregistrement = {
        m.enregistrement_client_id: m
        for m in MembreEntreprise.objects.filter(
            entreprise=entreprise, role="lecteur", enregistrement_client__isnull=False,
        )
    }

    # ── Communication (publicités, réactions, discussions) : les nœuds doivent exister AVANT la boucle des blocs, qui ne
    # rattache une transaction qu'à une cible déjà présente. Portée du spectateur : voir ontologie_communication. ──
    try:
        from ..ontologie_communication import enrichir_communication
        enrichir_communication(g, entreprise, racine, prefixe_role, membre)
    except Exception:
        logger.exception(t("engine/ontologie/_part_002.py:g:385:26"))

    # ── Sécurité (SIEM) : adresses IP vues dans l'audit et alertes levées, réservées à l'administrateur — voir ontologie_siem. ──
    try:
        from ..ontologie_siem import enrichir_siem
        enrichir_siem(g, entreprise, racine, prefixe_role, membre)
    except Exception:
        logger.exception("ontologie : couche SIEM")

    blocs_entreprise = list(Bloc.objects.filter(entreprise=entreprise).order_by("index"))
    blocs_visibles: dict = {}  # bloc.id -> True si au moins une transaction est visible ici
    for i_bloc, bloc in enumerate(blocs_entreprise):
        nid_bloc = f"bloc:{bloc.id}"
        _respirer()
        ajoute = False
        for i_tx, tx in enumerate(bloc.transactions):
            nid_cible = (f"enregistrement:{tx['enregistrement_id']}" if tx.get("enregistrement_id")
                         else f"publicite:{tx['publicite_id']}" if tx.get("publicite_id")
                         else f"conversation:{tx['conversation_id']}" if tx.get("conversation_id")
                         else f"table:{tx.get('table_id')}")
            if not g.has_node(nid_cible):
                continue
            if not ajoute:
                g.add_node(nid_bloc, type="bloc", label=f"Bloc #{bloc.index} · {bloc.hash[:10]}",
                           label_cle="engine/ontologie/_part_002.py:lbl_bloc", label_vars={"index": bloc.index, "hash": bloc.hash[:10]})
                ajoute = True
            g.add_edge(nid_bloc, nid_cible, relation="authentifie", type_evenement=tx.get("type"))

            # ── Transaction individuelle : nœud fin entre le bloc et sa
            # cible, avec l'auteur réel (agent/admin/client) rattaché. ──
            nid_tx = f"transaction:{bloc.id}:{i_tx}"
            g.add_node(nid_tx, type="transaction", label=tx.get("type", "?"))
            g.add_edge(nid_bloc, nid_tx, relation="contient_transaction")
            g.add_edge(nid_tx, nid_cible, relation="porte_sur")

            agent_uid = tx.get("agent_id")
            mc = membres_par_utilisateur.get(agent_uid) if agent_uid else None
            if mc is not None:
                nid_personne = f"{prefixe_role.get(mc.role, 'agent')}:{mc.id}"
                if g.has_node(nid_personne):
                    g.add_edge(nid_personne, nid_tx, relation="opere_transaction")

            # ── Preuve d'authenticité : uniquement si la ligne authentifiée
            # est identifiée à un client réel (enregistrement_client). ──
            if tx.get("enregistrement_id"):
                client_membre = clients_par_enregistrement.get(tx["enregistrement_id"])
                if client_membre is not None:
                    nid_client = f"client:{client_membre.id}"
                    if g.has_node(nid_client):
                        nid_preuve = f"preuve:{bloc.id}:{tx['enregistrement_id']}"
                        if not g.has_node(nid_preuve):
                            g.add_node(nid_preuve, type="preuve_authenticite",
                                       label=f"Preuve · bloc #{bloc.index}",
                                       label_cle="engine/ontologie/_part_002.py:lbl_preuve", label_vars={"index": bloc.index})
                            g.add_edge(nid_bloc, nid_preuve, relation="emet_preuve")
                        g.add_edge(nid_client, nid_preuve, relation="beneficie_preuve")

        blocs_visibles[bloc.id] = ajoute
        if ajoute:
            if not g.has_node(nid_chaine):
                g.add_node(nid_chaine, type="chaine_blocs", label=f"Blockchain — {entreprise.nom}",
                           label_cle="engine/ontologie/_part_002.py:lbl_chaine", label_vars={"nom": entreprise.nom})
                g.add_edge(racine, nid_chaine, relation="ancre_chaine")
            if not g.has_node(nid_autorite):
                g.add_node(nid_autorite, type="autorite_validation",
                           label=tfr("engine/ontologie/_part_002.py:427:35"),
                           label_cle="engine/ontologie/_part_002.py:427:35", label_vars={})
            g.add_edge(nid_bloc, nid_chaine, relation="appartient_chaine")
            g.add_edge(nid_autorite, nid_bloc, relation="valide_bloc")

        # ── Chaînage réel bloc→bloc (et détection de rupture) : basé sur
        # l'adjacence VRAIE en base (index i-1, i), pas sur les derniers
        # blocs visibles dans cette portée filtrée — sinon deux blocs non
        # consécutifs de la vraie chaîne semblerait former une rupture
        # simplement parce qu'ils sont les deux seuls visibles par un agent.
        if i_bloc > 0:
            bloc_prec_reel = blocs_entreprise[i_bloc - 1]
            if ajoute and blocs_visibles.get(bloc_prec_reel.id):
                nid_bloc_prec = f"bloc:{bloc_prec_reel.id}"
                g.add_edge(nid_bloc_prec, nid_bloc, relation="precede_bloc")
                if bloc.previous_hash != bloc_prec_reel.hash:
                    nid_rupture = f"rupture:{entreprise.id}:{bloc.index}"
                    g.add_node(nid_rupture, type="rupture_chaine",
                               label=tfr("engine/ontologie/_part_002.py:g:455:38").format(v1=bloc.index),
                               label_cle="engine/ontologie/_part_002.py:g:455:38", label_vars={"v1": bloc.index})
                    g.add_edge(nid_rupture, nid_bloc_prec, relation="rompt_entre")
                    g.add_edge(nid_rupture, nid_bloc, relation="rompt_entre")

    # ── Modules récents (formules de champ/ligne, partage lignes/cellules, widgets,
    # abonnements, connexions externes…) : relations manquantes — voir
    # enrichir_relations_modules (_part_001.py). Jamais bloquant.
    enrichir_relations_modules(g, entreprise, tables_qs, racine, prefixe_role)

    # ── Tables système : reliées aux personnes, objets, blocs, tables métier et entre elles (flux du wallet, clés étrangères) — elles se
    # mélangent à l'ontologie existante. Après tous les nœuds ci-dessus, avant la couche science. Jamais bloquant. ──
    try:
        from ..ontologie_systeme import enrichir_systeme
        enrichir_systeme(g, entreprise, racine, prefixe_role, membre)
    except Exception:
        logger.exception(_gt("rg6.msg.063"))

    # ── Couche science des données (qualité, distributions, tendances, corrélations, segments,
    # anomalies, clés probables, recommandations) — engine/ontologie_science.py. Jamais bloquante.
    try:
        from ..ontologie_science import enrichir_science
        enrichir_science(g, entreprise)
    except Exception:
        logger.exception(t("engine/ontologie/_part_002.py:g:471:26"))

    # ── Couches personnalisées (voir models.CoucheOntologie) : chaque
    # entreprise peut enregistrer ses propres scripts def etendre(graphe)
    # qui ajoutent leurs propres nœuds/arêtes, comme une formule qu'on
    # enregistre une fois et qui s'applique ensuite à chaque affichage.
    from django.db.models import Q
    from ..executor import executer_couche_ontologie_securisee, parse_modules_autorises, DEFAULT_MODULES_AUTORISES_ONTOLOGIE
    from ..models import CoucheOntologie, ConfigurationIA
    couches = CoucheOntologie.objects.filter(actif=True).filter(
        Q(entreprise__isnull=True) | Q(entreprise=entreprise)
    )
    if page is not None:
        # Couches prédéfinies (globales) + couches de l'entreprise soit non
        # rattachées à une page précise (comportement historique, visible sur
        # toutes les pages), soit explicitement rattachées à CETTE page.
        couches = couches.filter(
            Q(entreprise__isnull=True) | Q(pages__isnull=True) | Q(pages=page)
        ).distinct()
    # Champ dédié modules_autorises_couche_ontologie sur ConfigurationIA (même
    # principe que modules_autorises_formule/modules_autorises_graphique_bi) :
    # lu ici pour l'exécution réelle, et par
    # inference.generer_couche_ontologie pour l'annonce faite au modèle — les
    # deux restent toujours synchronisés. Config vide = comportement par
    # défaut (DEFAULT_MODULES_AUTORISES_ONTOLOGIE).
    cfg = ConfigurationIA.objects.filter(entreprise=entreprise).first()
    modules_autorises = parse_modules_autorises(
        cfg.modules_autorises_couche_ontologie if cfg else "", DEFAULT_MODULES_AUTORISES_ONTOLOGIE
    )
    for couche in couches:
        resultat = executer_couche_ontologie_securisee(couche.code, g, modules_autorises=modules_autorises)
        if resultat.get("ok"):
            g = resultat["resultat"]
        else:
            logger.warning(t("engine/ontologie/_part_002.py:g:505:28"), couche.nom, resultat.get("erreur"))

    # ── Sécurité fine, granularité « Relation » (Phase 9 suite) : retire du
    # graphe les ARÊTES d'un type précis interdit à cet agent
    # (membre.relations_masquees), sans toucher aux nœuds eux-mêmes — un
    # agent peut ainsi voir qu'une table existe sans voir, par exemple, les
    # KPI qui la "mesure"nt ou les tables de calcul qui en "calcule"nt.
    # Dernière étape, après couches personnalisées incluses : aucune couche
    # ne peut donc réintroduire une relation explicitement masquée. Même
    # convention que tables_autorisees/champs_masques : ignoré pour
    # role="admin"/"lecteur", None/vide = aucune restriction.
    if membre is not None and membre.role == "agent" and membre.relations_masquees:
        interdites = set(membre.relations_masquees)
        aretes_a_retirer = [
            (u, v) for u, v, a in g.edges(data=True) if a.get("relation") in interdites
        ]
        g.remove_edges_from(aretes_a_retirer)

    return g




# ── Fichier d'état du graphe (construit paresseusement en arrière-plan) ─────────────────────────────────────────────────────────
# Même esprit que le modèle fantôme (engine/modele_fantome.py) : l'ontologie n'est JAMAIS construite pour répondre à une requête. Elle se
# construit toute seule, lentement, dans le temps, et l'interface ne fait que CONSULTER son état :
#   · l'état (graphe + métriques) vit dans un FICHIER signé (MEDIA_ROOT/systeme/etats_ontologie/, un par entreprise, page et spectateur) que
#     tous les processus du serveur partagent ;
#   · un fil d'arrière-plan le construit par petites tranches (_respirer : il ne garde jamais la main plus de TRANCHE_S secondes de suite),
#     et les lectures coûteuses (cellules Parquet, activité des tables, échantillon de lignes) sont mémorisées PAR TABLE tant que la table
#     n'a pas changé (_signatures_tables) : une construction ne relit que ce qui a bougé ;
#   · il surveille une empreinte légère des données ; elle a changé → l'état est reconstruit, au plus une fois par MIN_ENTRE_CALCULS_S
#     secondes ; sinon une reconstruction complète a lieu toutes les DUREE_CACHE_GRAPHE_S secondes (« de temps en temps ») ;
#   · pendant une reconstruction, l'ancien état continue d'être servi (jamais d'attente) ; les métriques topologiques, les plus lentes,
#     arrivent après l'état lui-même ;
#   · tant qu'AUCUN état n'existe (toute première construction), la consultation répond « pas encore prête » (OntologiePasPrete) : la page
#     affiche un pop-up (i18n) et se recharge toute seule dès que l'état est là. Aucune requête ne calcule.
# Réglages (settings) : ONTOLOGIE_ETAT_FICHIER (False = ancien calcul à la demande), ONTOLOGIE_SYNCHRO_S, ONTOLOGIE_ETAT_DUREE_S.
# Le fichier est signé (HMAC de SECRET_KEY) : un fichier modifié ou venu d'ailleurs est ignoré et reconstruit.
import hashlib as _hashlib
import hmac as _hmac
import os as _os
import pickle as _pickle
import sys as _sys
import threading as _threading
import time as _time
import zlib as _zlib
from contextlib import contextmanager as _contextmanager

DUREE_CACHE_GRAPHE_S = 900   # âge maximal d'un état avant reconstruction complète, même sans changement détecté (réglage : ONTOLOGIE_ETAT_DUREE_S)
INTERVALLE_SYNCHRO_S = 5     # période du fil d'arrière-plan (réglage : ONTOLOGIE_SYNCHRO_S ; 0 ou négatif = pas de fil, ancien calcul à la demande)
MIN_ENTRE_CALCULS_S = 60     # jamais deux reconstructions du même état plus rapprochées (import en masse : l'empreinte change sans cesse)
OUBLI_CLE_S = 900            # un état que personne n'a demandé depuis 15 minutes n'est plus entretenu
AGE_MAX_SERVI_S = 0         # 0 = illimité : un état existant est TOUJOURS servi, même très ancien (réglage : ONTOLOGIE_AGE_MAX_S)
TRANCHE_S = 0.05             # le constructeur travaille au plus 50 ms de suite…
PAUSE_S = 0.1                # …puis cède la main 100 ms (un tiers du temps de calcul au plus : le site reste fluide pendant la construction)
DUREE_MEMO_S = 1800          # une lecture mémorisée est refaite au bout de 30 minutes même si la table semble inchangée (modifications de cellules)
PLAFOND_MEMO = 0            # 0 = illimité : lectures mémorisées par table, sans plafond (réglage : ONTOLOGIE_MEMO_PLAFOND pour en remettre un)
_ETATS: dict = {}            # clé → {"graphe", "metriques", "calcule_le", "empreinte", "identite", "version"} (mémoire du processus)
_DEMANDES: dict = {}         # clé → (id entreprise, id page, id membre, dernier instant demandé) : ce que le fil entretient
_VERROU_CACHE_GRAPHE = _threading.Lock()
_VERROUS_CALCUL: dict = {}   # un verrou par clé : jamais deux calculs en parallèle dans ce processus
_EN_COURS: set = set()       # clés dont un recalcul de secours tourne déjà
_SYNCHRO = {"fil": None}
_REVEIL = _threading.Event()  # réveille le constructeur tout de suite (nouvelle demande, « Recalculer ») au lieu d'attendre le prochain tour
_CONSTRUCTIONS: dict = {}    # clé → début d'une construction en cours dans CE processus
_PAUSE = _threading.local()  # .actif : ce fil est un constructeur d'arrière-plan (les requêtes et les tests ne ralentissent jamais)
FENETRE_REGROUPEMENT_S = 0.15  # des modifications qui arrivent à moins de 150 ms l'une de l'autre sont traitées en UN seul passage
REGROUPEMENT_MAX_S = 2.0       # …mais une rafale continue (import) ne retarde jamais la mise à jour de plus de 2 s
_SALE = {"gen": 0}             # compteur de modifications signalées : le constructeur compare « vu » et « signalé »
_MEMO: dict = {"cellules": {}, "lignes": {}, "cooc": {}, "taille": 0}   # lectures par table, valables tant que la signature de la table ne change pas


class OntologiePasPrete(Exception):
    """L'ontologie n'a encore aucun état : elle se construit en arrière-plan. Jamais une panne : l'interface affiche un pop-up et réessaie."""


def _respirer():
    """Construction paresseuse : appelée dans les boucles du calcul, elle cède la main au site dès que le constructeur d'arrière-plan
    travaille depuis TRANCHE_S secondes. Sans effet dans une requête ou un test (le calcul direct garde sa vitesse)."""
    if not getattr(_PAUSE, "actif", False) or getattr(_PAUSE, "prioritaire", False):
        return   # pas de pause pour une mise à jour prioritaire (modification)
    maintenant = _time.monotonic()
    if maintenant - _PAUSE.dernier >= TRANCHE_S:
        _time.sleep(PAUSE_S)
        _PAUSE.dernier = _time.monotonic()


def _devenir_constructeur():
    _PAUSE.actif = True
    _PAUSE.dernier = _time.monotonic()


def _signatures_tables(entreprise_id) -> dict:
    """{id de table: (nombre de lignes, dernier id, dernière modification)} en UNE requête groupée : change dès qu'une ligne est ajoutée,
    retirée ou modifiée — ce qui invalide la lecture mémorisée de CETTE table seulement."""
    from django.db.models import Count, Max
    from ..models import Enregistrement
    return {r["table_id"]: (r["n"], r["m"], str(r["u"])) for r in Enregistrement.objects.filter(table__entreprise_id=entreprise_id)
            .values("table_id").order_by().annotate(n=Count("id"), m=Max("id"), u=Max("maj_le"))}


def _memo_lire(espace, cle, signature):
    with _VERROU_CACHE_GRAPHE:
        e = _MEMO[espace].get(cle)
    if e is not None and e[0] == signature and _time.time() - e[2] < DUREE_MEMO_S:
        return e[1]
    return None


def _memo_ecrire(espace, cle, signature, valeur, poids):
    with _VERROU_CACHE_GRAPHE:
        plafond = int(_reglage("ONTOLOGIE_MEMO_PLAFOND", PLAFOND_MEMO) or 0)
        if plafond > 0 and _MEMO["taille"] + poids > plafond:
            for k in ("cellules", "lignes", "cooc"):
                _MEMO[k].clear()
            _MEMO["taille"] = 0
        if plafond > 0 and poids > plafond:
            return
        _MEMO[espace][cle] = (signature, valeur, _time.time())
        _MEMO["taille"] += poids


def _vider_memos():
    with _VERROU_CACHE_GRAPHE:
        for k in ("cellules", "lignes", "cooc"):
            _MEMO[k].clear()
        _MEMO["taille"] = 0


def _construction_paresseuse_active() -> bool:
    """Vrai quand un fil d'arrière-plan peut construire l'état (fichier d'état actif, fil non désactivé) : alors aucune requête ne calcule."""
    return _persistance_active() and float(_reglage("ONTOLOGIE_SYNCHRO_S", INTERVALLE_SYNCHRO_S) or 0) > 0


def _reglage(nom, defaut):
    try:
        from django.conf import settings
        return getattr(settings, nom, defaut)
    except Exception:
        return defaut


def _persistance_active() -> bool:
    """Pas de fichier d'état ni de fil pendant les tests (une base de test ne doit rien laisser dans MEDIA_ROOT) ni si l'admin le coupe."""
    return not (_sys.argv[1:2] == ["test"] or not _reglage("ONTOLOGIE_ETAT_FICHIER", True))


def _cle_cache_graphe(entreprise, page, membre):
    # Le spectateur compte pour TOUS les rôles : les couches communication et SIEM règlent leur portée sur `membre`.
    return (entreprise.id, page.id if page is not None else None, membre.id if membre is not None else None)


def _identite(entreprise) -> str:
    """Empêche de relire l'état d'une AUTRE installation (base remise à zéro, mêmes numéros) : numéro, nom et date de création."""
    return f"{entreprise.id}|{entreprise.nom}|{getattr(entreprise, 'cree_le', '')}"


def _chemin_etat(cle):
    from ..stockage import dossier_systeme
    e, p, m = cle
    return dossier_systeme("etats_ontologie", f"e{e}_p{p or 0}_m{m or 0}.etat")


def _signature(corps: bytes) -> bytes:
    from django.conf import settings
    return _hmac.new(str(settings.SECRET_KEY).encode("utf-8"), corps, _hashlib.sha256).digest()


def _ecrire_etat(cle, etat):
    """Écrit l'état de façon atomique (fichier temporaire puis remplacement). Renvoie la version du fichier, ou None si l'écriture échoue."""
    if not _persistance_active():
        return None
    try:
        chemin = _chemin_etat(cle)
        _os.makedirs(chemin.parent, exist_ok=True)
        charge = {k: etat.get(k) for k in ("graphe", "metriques", "calcule_le", "empreinte", "empreinte_forte", "identite")}
        corps = _zlib.compress(_pickle.dumps(charge, protocol=4), 1)
        temp = f"{chemin}.{_os.getpid()}.{_threading.get_ident()}.tmp"
        fd = _os.open(temp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
        with _os.fdopen(fd, "wb") as f:
            f.write(_signature(corps) + corps)
        _os.replace(temp, chemin)
        return _os.stat(chemin).st_mtime_ns
    except Exception:
        logger.exception(_gt("rg6.msg.064"))
        return None


def _lire_etat(cle):
    """L'état du fichier (signature vérifiée AVANT de désérialiser), ou None s'il manque, est illisible ou n'est pas de nous."""
    if not _persistance_active():
        return None
    try:
        chemin = _chemin_etat(cle)
        version = _os.stat(chemin).st_mtime_ns
        with open(chemin, "rb") as f:
            brut = f.read()
        signature, corps = brut[:32], brut[32:]
        if not _hmac.compare_digest(signature, _signature(corps)):
            logger.warning(_gt("rg6.msg.065"), chemin)
            return None
        etat = _pickle.loads(_zlib.decompress(corps))
        etat["version"] = version
        return etat
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception(_gt("rg6.msg.066"))
        return None


def _version_fichier(cle):
    try:
        return _os.stat(_chemin_etat(cle)).st_mtime_ns
    except Exception:
        return None


def _etat_courant(cle, identite=None):
    """L'état le plus récent connu : celui de la mémoire, remplacé par le fichier si un autre processus l'a réécrit depuis. None si aucun."""
    with _VERROU_CACHE_GRAPHE:
        memoire = _ETATS.get(cle)
    version = _version_fichier(cle) if _persistance_active() else None
    if version is not None and (memoire is None or memoire.get("version") != version):
        lu = _lire_etat(cle)
        if lu is not None and (memoire is None or lu["calcule_le"] >= memoire["calcule_le"]):
            memoire = lu
            with _VERROU_CACHE_GRAPHE:
                _ETATS[cle] = lu
        elif lu is not None:
            memoire["version"] = version   # la mémoire est plus récente que le fichier (écriture ratée) : on ne relit pas à chaque appel
    if memoire is not None and identite is not None and memoire.get("identite") not in (None, identite):
        return None   # état d'une autre installation : comme s'il n'existait pas
    return memoire


def invalider_cache_graphe(entreprise_id=None):
    """Vide l'état du graphe (toute l'entreprise `entreprise_id`, ou tout si None) : mémoire ET fichiers ; le prochain affichage recalcule."""
    with _VERROU_CACHE_GRAPHE:
        for cle in [c for c in _ETATS if entreprise_id is None or c[0] == entreprise_id]:
            _ETATS.pop(cle, None)
    if _persistance_active():
        try:
            dossier = _chemin_etat((0, None, None)).parent
            for nom in _os.listdir(dossier):
                if nom.endswith(".etat") and (entreprise_id is None or nom.startswith(f"e{int(entreprise_id)}_")):
                    _os.unlink(dossier / nom)
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception(_gt("rg6.msg.067"))


@_contextmanager
def _verrou_fichier(cle, bloquant=True):
    """Verrou entre PROCESSUS (un seul calcul de cet état à la fois sur le serveur). Rend True si obtenu ; sans fichier d'état, toujours True."""
    if not _persistance_active():
        yield True
        return
    import fcntl
    chemin = _chemin_etat(cle)
    _os.makedirs(chemin.parent, exist_ok=True)
    f = open(f"{chemin}.verrou", "w")
    obtenu = True
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if bloquant else fcntl.LOCK_NB))
        except OSError:
            obtenu = False
        yield obtenu
    finally:
        if obtenu:
            try: fcntl.flock(f, fcntl.LOCK_UN)
            except OSError: pass
        f.close()


def _empreinte(entreprise_id):
    """Empreinte très légère de ce dont dépend le graphe (quelques agrégats sur clés primaires) : change dès qu'une table, une colonne, une ligne,
    une couche ou une page est ajoutée ; une simple modification ou suppression est rattrapée par le recalcul périodique (DUREE_CACHE_GRAPHE_S)."""
    try:
        from django.db.models import Count, Max
        from ..models import ChampDynamique, CoucheOntologie, Enregistrement, PageOntologie, TableDynamique
        def agr(qs):
            r = qs.aggregate(n=Count("id"), m=Max("id"))
            return (r["n"], r["m"])
        return (
            agr(TableDynamique.objects.filter(entreprise_id=entreprise_id)),
            agr(ChampDynamique.objects.filter(table__entreprise_id=entreprise_id)),
            Enregistrement.objects.aggregate(m=Max("id"))["m"],   # installation à une seule entreprise : maximum global, instantané sur la clé primaire
            agr(CoucheOntologie.objects.filter(entreprise_id=entreprise_id)),
            agr(PageOntologie.objects.filter(entreprise_id=entreprise_id)),
        )
    except Exception:
        logger.exception(_gt("rg6.msg.068"))
        return None


def _empreinte_forte(entreprise_id):
    """Empreinte qui voit AUSSI les modifications et suppressions : signature de chaque table (lignes, dernier id, dernière modification)
    en plus de l'empreinte légère. Une requête groupée, appelée seulement quand une modification a été signalée ou à la construction."""
    try:
        return (_empreinte(entreprise_id), tuple(sorted(_signatures_tables(entreprise_id).items())))
    except Exception:
        logger.debug("empreinte forte indisponible", exc_info=True)
        return None   # inconnue : traitée comme « changée »


def _calculer_metriques(graphe):
    from ._part_003 import metriques as _metriques   # défini plus bas dans le package
    return _metriques(graphe)


def _calculer_et_ecrire(cle, entreprise, page, membre, force=False, bloquant=True, avec_metriques=True, prioritaire=False):
    """ISOLATION DES ENTREPRISES : toute construction d'état se fait sur la base de L'entreprise dont c'est l'état, jamais sur celle que le fil ou la
    requête appelant se trouvait avoir en contexte (en mode « une base par entreprise », le fil de synchronisation hérite de l'entreprise de la
    requête qui l'a lancé : sans ceci, l'ontologie d'une entreprise pouvait être construite avec les données d'une autre)."""
    from .. import base_par_entreprise as _bpe
    with _bpe.dans_entreprise(entreprise.id):
        return _calculer_et_ecrire_dans_entreprise(cle, entreprise, page, membre, force=force, bloquant=bloquant,
                                                   avec_metriques=avec_metriques, prioritaire=prioritaire)


def _calculer_et_ecrire_dans_entreprise(cle, entreprise, page, membre, force=False, bloquant=True, avec_metriques=True, prioritaire=False):
    """Construit le graphe, l'écrit dans le fichier d'état et le met en mémoire. Rend le nouvel état, ou None si un autre processus construit déjà
    (mode non bloquant). `force=False` : si une construction vient d'aboutir pendant l'attente, son résultat est repris au lieu de refaire le travail.
    L'état est écrit AVANT les métriques topologiques (lentes) : la page est consultable dès que le graphe existe, les métriques suivent."""
    with _VERROUS_CALCUL.setdefault(cle, _threading.Lock()):
        with _verrou_fichier(cle, bloquant) as obtenu:
            if not obtenu:
                return None
            if not force:
                recent = _etat_courant(cle, _identite(entreprise))
                if recent is not None and _time.time() - recent["calcule_le"] < MIN_ENTRE_CALCULS_S:
                    return recent
            _CONSTRUCTIONS[cle] = _time.time()
            ancienne_priorite = getattr(_PAUSE, "prioritaire", False)
            _PAUSE.prioritaire = prioritaire
            try:
                empreinte = _empreinte(entreprise.id)   # relevée AVANT le calcul : un changement survenu pendant le calcul déclenchera le suivant
                forte = _empreinte_forte(entreprise.id)   # idem, mais voit aussi les modifications de lignes existantes
                debut = _time.monotonic()
                graphe = _construire_graphe_calcul(entreprise, page=page, membre=membre)
                duree = _time.monotonic() - debut
                if duree > 1.0:
                    logger.warning(_gt("rg6.msg.069"),
                                   duree, graphe.number_of_nodes(), graphe.number_of_edges(), entreprise.id)
                etat = {"graphe": graphe, "metriques": None, "calcule_le": _time.time(), "empreinte": empreinte, "empreinte_forte": forte, "identite": _identite(entreprise), "version": None}
                etat["version"] = _ecrire_etat(cle, etat)
                with _VERROU_CACHE_GRAPHE:
                    _ETATS[cle] = etat
                if avec_metriques:
                    etat["metriques"] = _calculer_metriques(graphe)
                    etat["version"] = _ecrire_etat(cle, etat)   # même graphe, métriques en plus
                return etat
            finally:
                _CONSTRUCTIONS.pop(cle, None)
                _PAUSE.prioritaire = ancienne_priorite


def _completer_metriques(cle, etat):
    """Un état écrit sans ses métriques (processus arrêté entre les deux écritures) : le constructeur les calcule, sans reconstruire le graphe."""
    with _VERROUS_CALCUL.setdefault(cle, _threading.Lock()):
        with _verrou_fichier(cle, False) as obtenu:
            if obtenu and etat.get("metriques") is None:
                etat["metriques"] = _calculer_metriques(etat["graphe"])
                etat["version"] = _ecrire_etat(cle, etat)


def _rafraichir_en_fond(cle, entreprise, page, membre):
    """Reconstruction de secours lancée depuis une requête quand le fil de synchronisation n'a pas entretenu l'état (fil absent ou bloqué)."""
    _devenir_constructeur()
    try:
        _calculer_et_ecrire(cle, entreprise, page, membre, bloquant=False)
    except Exception:
        logger.exception(_gt("rg6.msg.070"))
    finally:
        from django.db import connection
        connection.close()   # le fil a sa propre connexion à la base
        with _VERROU_CACHE_GRAPHE: _EN_COURS.discard(cle)


def _passe_synchro(prioritaire=False):
    """Un tour du fil d'arrière-plan : pour chaque état demandé récemment, recalcule ce qui a changé ou vieilli."""
    maintenant = _time.time()
    with _VERROU_CACHE_GRAPHE:
        suivis = list(_DEMANDES.items())
    if prioritaire:
        suivis.sort(key=lambda s: s[1][3], reverse=True)   # l'état regardé en dernier est remis à jour en premier
    duree_max = _reglage("ONTOLOGIE_ETAT_DUREE_S", DUREE_CACHE_GRAPHE_S)
    empreintes = {}
    fortes = {}
    incomplet = False
    from ..models import Entreprise, MembreEntreprise, PageOntologie
    for cle, (e_id, p_id, m_id, vu_le) in suivis:
        if maintenant - vu_le > OUBLI_CLE_S:
            with _VERROU_CACHE_GRAPHE: _DEMANDES.pop(cle, None)
            continue
        from .. import base_par_entreprise as _bpe
        _bpe.definir(e_id)   # contexte PROPRE au fil : les empreintes et le calcul lisent la base de l'entreprise de cet état, pas celle d'une autre
        entreprise = Entreprise.objects.filter(id=e_id).first()
        if entreprise is None:
            with _VERROU_CACHE_GRAPHE: _DEMANDES.pop(cle, None)
            continue
        etat = _etat_courant(cle, _identite(entreprise))
        if etat is not None and etat.get("metriques") is None and cle not in _CONSTRUCTIONS:
            _completer_metriques(cle, etat)
        if etat is not None:
            age = maintenant - etat["calcule_le"]
            if prioritaire:
                # Modification signalée : pas de délai minimum ; on ne reconstruit que si une table a réellement changé.
                if e_id not in fortes:
                    fortes[e_id] = _empreinte_forte(e_id)
                if age < duree_max and fortes[e_id] is not None and fortes[e_id] == etat.get("empreinte_forte"):
                    continue   # la modification n'a rien changé de ce dont l'ontologie dépend
            else:
                if age < MIN_ENTRE_CALCULS_S:
                    continue
                if e_id not in empreintes:
                    empreintes[e_id] = _empreinte(e_id)
                if age < duree_max and empreintes[e_id] is not None and empreintes[e_id] == etat.get("empreinte"):
                    continue   # rien n'a changé et l'état est encore jeune
        page = PageOntologie.objects.filter(id=p_id, entreprise_id=e_id).first() if p_id else None
        membre = MembreEntreprise.objects.filter(id=m_id, entreprise_id=e_id).first() if m_id else None
        if (p_id and page is None) or (m_id and membre is None):
            with _VERROU_CACHE_GRAPHE: _DEMANDES.pop(cle, None)
            continue
        resultat = _calculer_et_ecrire(cle, entreprise, page, membre, force=prioritaire, bloquant=False, prioritaire=prioritaire)
        if resultat is None:
            incomplet = True   # un autre processus construit déjà cet état : on repasse tout de suite après lui
    return incomplet


def signaler_modification(entreprise_id=None):
    """Appelée (par OntologieEtatMiddleware, ou par tout code qui écrit hors requête HTTP) dès que des données ont été modifiées.
    Instantané et sans effet de bord sur la requête : un compteur et un réveil. Plusieurs signaux simultanés = un seul passage prioritaire.
    Sans fil d'arrière-plan (tests, ONTOLOGIE_ETAT_FICHIER=False, ONTOLOGIE_SYNCHRO_S<=0) : les états en mémoire sont oubliés, le prochain
    affichage recalcule sur place (les lectures mémorisées par table rendent ce recalcul léger)."""
    with _VERROU_CACHE_GRAPHE:
        _SALE["gen"] += 1
        if not _construction_paresseuse_active():
            for cle in [c for c in _ETATS if entreprise_id is None or c[0] == entreprise_id]:
                _ETATS.pop(cle, None)
    _demarrer_synchro()
    _REVEIL.set()


def _regrouper_modifications(vue):
    """Laisse les modifications d'une même rafale s'accumuler (FENETRE_REGROUPEMENT_S sans nouveau signal) avant de reconstruire, sans jamais
    attendre plus de REGROUPEMENT_MAX_S. Rend le compteur atteint."""
    debut = _time.monotonic()
    while True:
        courant = _SALE["gen"]
        _time.sleep(FENETRE_REGROUPEMENT_S)
        if _SALE["gen"] == courant or _time.monotonic() - debut >= REGROUPEMENT_MAX_S:
            return _SALE["gen"]


def _boucle_synchro():
    from django.db import connection
    _devenir_constructeur()
    vue = 0
    while True:
        _REVEIL.wait(max(0.5, float(_reglage("ONTOLOGIE_SYNCHRO_S", INTERVALLE_SYNCHRO_S) or INTERVALLE_SYNCHRO_S)))   # un tour par période, ou tout de suite si on le réveille
        _REVEIL.clear()   # AVANT de lire le compteur : un signal arrivé pendant la construction relance un tour immédiat
        prioritaire = _SALE["gen"] != vue
        if prioritaire:
            vue = _regrouper_modifications(vue)
        incomplet = False
        try:
            incomplet = _passe_synchro(prioritaire=prioritaire)
        except Exception:
            logger.exception(_gt("rg6.msg.071"))
        finally:
            connection.close()   # pas de connexion gardée ouverte entre deux tours
        if incomplet:
            _time.sleep(0.25)
            _REVEIL.set()


def _demarrer_synchro():
    """Lance (une fois par processus) le fil qui construit et entretient les fichiers d'état."""
    if _SYNCHRO["fil"] is not None or not _persistance_active() or float(_reglage("ONTOLOGIE_SYNCHRO_S", INTERVALLE_SYNCHRO_S) or 0) <= 0:
        return
    with _VERROU_CACHE_GRAPHE:
        if _SYNCHRO["fil"] is not None:
            return
        _SYNCHRO["fil"] = _threading.Thread(target=_boucle_synchro, name="ontologie-synchro", daemon=True)
        _SYNCHRO["fil"].start()


def libelle_noeud(attrs, defaut=""):
    """Langue intermédiaire : le nœud porte une clé immuable (`label_cle`) et ses variables (`label_vars`) ; le texte est produit ICI, dans la
    langue courante, au moment de la sérialisation. L'état n'est jamais modifié : changer de langue ne coûte aucun calcul. Sans clé, le label
    stocké (nom de table, de champ, valeur : des données, pas des textes d'interface) est rendu tel quel."""
    cle = attrs.get("label_cle")
    if cle:
        try:
            return tf(cle, **(attrs.get("label_vars") or {})) + (attrs.get("label_suffixe") or "")   # suffixe immuable (ex. « #12 »)
        except Exception:
            pass   # clé inconnue : on retombe sur le label stocké
    return attrs.get("label", defaut)


def _vue_lecture(g):
    """Vue en lecture seule sur l'état (aucune copie) : la structure ne peut pas être modifiée par l'appelant."""
    return nx.restricted_view(g, [], [])


def _localiser_labels(g):
    """Les labels à clé (`label_cle`/`label_vars`) sont recalculés ICI, à chaque lecture, dans la langue de la requête :
    l'état en cache (mémoire + fichier) garde le français et sert toutes les langues."""
    for _nid, attrs in g.nodes(data=True):
        cle = attrs.get("label_cle")
        if cle:
            attrs["label"] = libelle_noeud(attrs)
    return g


def _etat_servable(cle, identite):
    """L'état à servir à l'utilisateur : le plus récent connu, même s'il est en train d'être renouvelé, sauf s'il est vraiment trop vieux."""
    etat = _etat_courant(cle, identite)
    age_max = float(_reglage("ONTOLOGIE_AGE_MAX_S", AGE_MAX_SERVI_S) or 0)
    if etat is not None and age_max > 0 and _time.time() - etat["calcule_le"] >= age_max:
        return None
    return etat


def _enregistrer_demande(cle, entreprise, page, membre):
    with _VERROU_CACHE_GRAPHE:
        _DEMANDES[cle] = (entreprise.id, page.id if page is not None else None, membre.id if membre is not None else None, _time.time())


def construire_graphe(entreprise, page=None, membre=None, frais=False, lecture=False) -> "nx.DiGraph":
    """Graphe de l'entreprise (voir _construire_graphe_calcul), relu depuis l'état construit en arrière-plan : aucun calcul sur le chemin de
    l'utilisateur. Un état existant est toujours servi, même ancien (le constructeur le renouvelle sans jamais faire attendre). Sans aucun état,
    lève OntologiePasPrete tant que la construction paresseuse est active (voir _construction_paresseuse_active) ; sans fil d'arrière-plan
    (tests, ONTOLOGIE_ETAT_FICHIER=False) le graphe est calculé sur place comme avant. `frais=True` calcule tout de suite et réécrit l'état.
    Le graphe rendu est une COPIE : l'appelant peut le modifier."""
    cle = _cle_cache_graphe(entreprise, page, membre)
    _enregistrer_demande(cle, entreprise, page, membre)
    _demarrer_synchro()
    if not frais:
        etat = _etat_servable(cle, _identite(entreprise))
        if etat is not None:
            age = _time.time() - etat["calcule_le"]
            # Secours si le fil de synchronisation ne tourne pas (désactivé, tests, bloqué) : l'état trop ancien est reconstruit en fond, et servi tel quel.
            if age >= max(2 * DUREE_CACHE_GRAPHE_S, 60) and (_SYNCHRO["fil"] is None or age >= 2 * DUREE_CACHE_GRAPHE_S):
                with _VERROU_CACHE_GRAPHE:
                    relancer = cle not in _EN_COURS
                    if relancer: _EN_COURS.add(cle)
                if relancer:
                    _threading.Thread(target=_rafraichir_en_fond, args=(cle, entreprise, page, membre), daemon=True).start()
            if lecture:
                return _vue_lecture(etat["graphe"])   # lecture pure : ni copie ni traduction en place (voir libelle_noeud)
            return _localiser_labels(etat["graphe"].copy())
        if _construction_paresseuse_active():
            _REVEIL.set()   # le constructeur s'y met tout de suite
            raise OntologiePasPrete(_gt("engine/ontologie/_part_002.py:pas_prete"))
    # Calcul sur place : demandé (frais=True) ou aucun fil d'arrière-plan pour construire l'état.
    etat = _calculer_et_ecrire(cle, entreprise, page, membre, force=frais, avec_metriques=False)
    if lecture:
        return _vue_lecture(etat["graphe"])
    return _localiser_labels(etat["graphe"].copy())


def consulter_graphe(entreprise, page=None, membre=None):
    """Consultation PURE de l'état : le graphe construit en arrière-plan, ou None s'il n'est pas encore prêt (la page affiche alors son
    pop-up). Ne calcule rien ; réveille seulement le constructeur."""
    try:
        return construire_graphe(entreprise, page=page, membre=membre, lecture=True)   # vue sans copie : les labels se traduisent à la sérialisation
    except OntologiePasPrete:
        return None


def statut_ontologie(entreprise, page=None, membre=None) -> dict:
    """État de la construction, pour la page (sondage léger) : {"pret", "en_construction", "age_s"}. Entretient aussi la demande (le constructeur
    n'oublie pas un état que quelqu'un attend) et le réveille s'il n'y a encore rien."""
    cle = _cle_cache_graphe(entreprise, page, membre)
    _enregistrer_demande(cle, entreprise, page, membre)
    _demarrer_synchro()
    etat = _etat_servable(cle, _identite(entreprise))
    if etat is None:
        _REVEIL.set()
    return {"pret": etat is not None, "en_construction": etat is None or cle in _CONSTRUCTIONS,
            "age_s": None if etat is None else round(_time.time() - etat["calcule_le"])}


def demander_recalcul(entreprise, page=None, membre=None):
    """« Recalculer » : oublie l'état de cette page (mémoire et fichier) ainsi que les lectures mémorisées par table, et réveille le constructeur.
    La page affiche « pas encore prête » jusqu'à la fin de la reconstruction, qui se fait en arrière-plan comme la première."""
    cle = _cle_cache_graphe(entreprise, page, membre)
    with _VERROU_CACHE_GRAPHE:
        _ETATS.pop(cle, None)
    _vider_memos()
    if _persistance_active():
        try:
            _os.unlink(_chemin_etat(cle))
        except OSError:
            pass
    _enregistrer_demande(cle, entreprise, page, membre)
    _REVEIL.set()


def metriques_etat(entreprise, page, membre, graphe):
    """Métriques topologiques de `graphe` : celles de l'état quand elles sont déjà calculées pour ce même graphe. Jamais de calcul long à
    l'ouverture : un grand graphe dont les métriques n'arrivent pas encore (le constructeur les calcule après l'état) affiche seulement ses
    effectifs, les autres valeurs suivent à la prochaine ouverture. Un petit graphe (vue client restreinte) est calculé sur place, instantanément."""
    cle = _cle_cache_graphe(entreprise, page, membre)
    etat = _etat_courant(cle, _identite(entreprise))
    m = etat.get("metriques") if etat is not None else None
    if m is not None and m.get("noeuds") == graphe.number_of_nodes() and m.get("aretes") == graphe.number_of_edges():
        return dict(m)
    if graphe.number_of_nodes() <= 2000:
        return dict(_calculer_metriques(graphe))
    return {"noeuds": graphe.number_of_nodes(), "aretes": graphe.number_of_edges(), "densite": "…", "diametre": "…", "clustering": "…", "communautes": "…"}


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
