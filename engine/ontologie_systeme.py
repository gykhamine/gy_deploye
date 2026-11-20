"""Tables système DANS l'ontologie : elles se mélangent aux nœuds existants au lieu de former un îlot à part.

Avant : les ~100 tables système étaient des nœuds « table » identiques (même couleur), reliés à l'entreprise un par un, avec des centaines de
nœuds « colonne » et des milliers de liens « même nom ». Aucun lien ne les rattachait aux agents, clients, KPI, dashboards, modèles IA, blocs…

Maintenant, chaque table système est reliée AUX VRAIS NŒUDS du graphe, par des relations déduites du code et des données :
  * personnes : un agent, un administrateur ou un client est relié aux tables système où il a réellement des lignes (écritures du wallet,
    messages, retraits, audit, documents…), poids = nombre de lignes ; la table des membres recense chaque personne ;
  * objets : dashboard, KPI, table de calcul, skill, modèle IA, document, compteur de tokens, publicité, discussion, abonnement, connexion
    externe, groupe de tables, tâche planifiée, adresse IP / alerte SIEM, bloc de la blockchain → la table système qui les consigne ;
  * tables métier : les tables système d'analyse, de document, de publicité… sont reliées aux tables métier qu'elles visent (valeurs réelles
    des clés étrangères) ; la table des membres l'est à la table des clients ;
  * entre tables système : clés étrangères réelles (composition, rattachement à un membre, croisement de domaines), flux du wallet (un
    dépôt CRÉDITE, un retrait DÉBITE, les frais s'APPLIQUENT…) et ancrage dans la blockchain (colonne `bloc_index`).
Chaque table système porte la couleur de son domaine (finance, paiement, communication, audit…), un anneau en pointillés, son volume réel.
Pas de nœud « domaine » ni de regroupement à part : le graphe reste UNE seule ontologie. Les colonnes système forment un type discret et ne
se relient plus entre elles par leur nom.

Tout est lu en base à chaque calcul du graphe (jamais de copie). Pas de texte de message ni de secret : seulement des volumes.
Fonctions de calcul pur (sans base) : domaine_de, type_noeud_table, relation_fk, planifier_liens — testables sans Django.
"""
import logging

from engine.i18n import td, tl
from engine.i18n import tfr as _gtfr  # noqa: E402
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

PREFIXE_TYPE = "table_systeme_"
TYPE_CHAMP_SYSTEME = "champ_systeme"
PREFIXE_NOM_TABLE = _gtfr("engine/tables_systeme.py:31:14")   # même valeur que tables_systeme.PREFIXE_NOM (non importé : ce module reste sans dépendance)
MAX_PERSONNES = 200

# domaine → (libellé, couleur, description)
DOMAINES = {
    "identite":      (tl("rg3.onto_sys.dom.identite.lib"), "#3b82f6", td("rg3.onto_sys.dom.identite.descr")),
    "finance":       (tl("rg3.onto_sys.dom.finance.lib"), "#eab308", td("rg3.onto_sys.dom.finance.descr")),
    "paiement":      (tl("rg3.onto_sys.dom.paiement.lib"), "#f97316", td("rg3.onto_sys.dom.paiement.descr")),
    "communication": (tl("rg3.onto_sys.dom.communication.lib"), "#ec4899", td("rg3.onto_sys.dom.communication.descr")),
    "publicite":     (tl("rg3.onto_sys.dom.publicite.lib"), "#d946ef", td("rg3.onto_sys.dom.publicite.descr")),
    "audit":         (tl("rg3.onto_sys.dom.audit.lib"), "#ef4444", td("rg3.onto_sys.dom.audit.descr")),
    "blockchain":    (tl("rg3.onto_sys.dom.blockchain.lib"), "#84cc16", td("rg3.onto_sys.dom.blockchain.descr")),
    "ia":            (tl("rg3.onto_sys.dom.ia.lib"), "#8b5cf6", td("rg3.onto_sys.dom.ia.descr")),
    "analytique":    (tl("rg3.onto_sys.dom.analytique.lib"), "#14b8a6", td("rg3.onto_sys.dom.analytique.descr")),
    "documents":     (tl("rg3.onto_sys.dom.documents.lib"), "#22c55e", td("rg3.onto_sys.dom.documents.descr")),
    "structure":     (tl("rg3.onto_sys.dom.structure.lib"), "#78716c", td("rg3.onto_sys.dom.structure.descr")),
}
DOMAINE_PAR_DEFAUT = "structure"

# clé de source (tables_systeme.registre : clé explicite ou nom de modèle en minuscules) → domaine
_DOMAINE_PAR_CLE = {
    "membres": "identite", "repriseposte": "identite",
    "wallets": "finance", "ecritures_wallet": "finance", "charges_wallet": "finance", "taux_change": "finance", "frais_wallet": "finance",
    "remunerations": "finance", "habilitations_flux": "finance", "politique_transfert": "finance", "abonnements": "finance",
    "depots_paiement": "paiement", "retraits_wallet": "paiement", "moyens_retrait": "paiement", "evenements_prestataire": "paiement",
    "fournisseurs_paiement": "paiement", "modemsms": "paiement", "prefixeoperateur": "paiement",
    "conversations": "communication", "participants": "communication", "messages": "communication", "reactions_messages": "communication",
    "favorimessage": "communication", "avismessage": "communication", "sourdine": "communication", "chatbotagent": "communication",
    "notification": "communication", "reconotifiee": "communication", "tacheagenda": "communication",
    "publicites": "publicite", "reactions_publicites": "publicite",
    "journalaudit": "audit", "journaltransformationcolonne": "audit", "evaluationsus": "audit", "mesureusage": "audit",
    "bloc": "blockchain", "blocsysteme": "blockchain", "couleurbloc": "blockchain",
    "modeleia": "ia", "inferenceia": "ia", "configurationia": "ia", "skill": "ia", "generationformule": "ia", "generationgraphiquebi": "ia",
    "generationcoucheontologie": "ia", "compteurtokens": "ia", "demandeagent": "ia", "graphiquedemande": "ia",
    "formulecalcul": "analytique", "formulechamp": "analytique", "formuleligne": "analytique", "formulelignecible": "analytique",
    "tablecalcul": "analytique", "lignecalcul": "analytique", "graphiquebi": "analytique", "tableaubordbi": "analytique",
    "widgetgraphiquebi": "analytique", "mesurewidget": "analytique", "indicateurkpi": "analytique", "valeurkpihistorique": "analytique",
    "documentgenere": "documents", "signaturedocument": "documents", "enteteofficiel": "documents", "modeleentete": "documents",
    "reglageentete": "documents", "documentadministratif": "documents", "modeledocument": "documents", "compteurreference": "documents",
    "reglageemission": "documents", "personnalisationdocumentmembre": "documents", "configurationdocumentcontexte": "documents",
    "rapportgenere": "documents",
}
# Modèle ajouté plus tard et inconnu de la table ci-dessus : le nom de la clé le range (premier mot-clé trouvé).
_MOTS_CLES = (
    ("wallet", "finance"), ("paiement", "paiement"), ("retrait", "paiement"), ("message", "communication"), ("conversation", "communication"),
    ("notif", "communication"), ("audit", "audit"), ("journal", "audit"), ("bloc", "blockchain"), ("chain", "blockchain"),
    ("modeleia", "ia"), ("inference", "ia"), ("token", "ia"), ("kpi", "analytique"), ("formule", "analytique"), ("graphique", "analytique"),
    ("tableau", "analytique"), ("document", "documents"), ("entete", "documents"), ("membre", "identite"), ("pub", "publicite"),
)

TYPES_NOEUDS_SYSTEME = {
    **{PREFIXE_TYPE + d: (c, td(f"rg3.onto_sys.dom.{d}.type")) for d, (_lib, c, _descr) in DOMAINES.items()},
    TYPE_CHAMP_SYSTEME: ("#a8a29e", td("rg3.onto_sys.champ.desc")),
}
LIBELLES_TYPES = {
    **{PREFIXE_TYPE + d: tl(f"rg3.onto_sys.dom.{d}.libtype") for d in DOMAINES},
    TYPE_CHAMP_SYSTEME: tl("rg3.onto_sys.champ.lib"),
}

# relation → (couleur, description)
RELATIONS_SYSTEME = {
    "detail_de":            ("#0ea5e9", td("rg3.onto_sys.rel.detail_de")),
    "concerne_membre":      ("#6366f1", td("rg3.onto_sys.rel.concerne_membre")),
    "trace_acteur":         ("#fb7185", td("rg3.onto_sys.rel.trace_acteur")),
    "reference_systeme":    ("#e879f9", td("rg3.onto_sys.rel.reference_systeme")),
    "genere_par_ia":        ("#a78bfa", td("rg3.onto_sys.rel.genere_par_ia")),
    "partage_objet":        ("#f472b6", td("rg3.onto_sys.rel.partage_objet")),
    "credite_wallet":       ("#22c55e", td("rg3.onto_sys.rel.credite_wallet")),
    "debite_wallet":        ("#f43f5e", td("rg3.onto_sys.rel.debite_wallet")),
    "verse_remuneration":   ("#06b6d4", td("rg3.onto_sys.rel.verse_remuneration")),
    "impute_charge":        ("#fb923c", td("rg3.onto_sys.rel.impute_charge")),
    "facture_abonnement":   ("#65a30d", td("rg3.onto_sys.rel.facture_abonnement")),
    "applique_frais":       ("#c026d3", td("rg3.onto_sys.rel.applique_frais")),
    "convertit_devise":     ("#0891b2", td("rg3.onto_sys.rel.convertit_devise")),
    "encadre_transfert":    ("#7c3aed", td("rg3.onto_sys.rel.encadre_transfert")),
    "habilite_flux":        ("#ea580c", td("rg3.onto_sys.rel.habilite_flux")),
    "met_a_jour_solde":     ("#16a34a", td("rg3.onto_sys.rel.met_a_jour_solde")),
    "ancre_blockchain":     ("#bef264", td("rg3.onto_sys.rel.ancre_blockchain")),
    "analyse_table":        ("#059669", td("rg3.onto_sys.rel.analyse_table")),
    "porte_sur_table":      ("#f59e0b", td("rg3.onto_sys.rel.porte_sur_table")),
    "table_clients":        ("#fdba74", td("rg3.onto_sys.rel.table_clients")),
    "recense_personne":     ("#818cf8", td("rg3.onto_sys.rel.recense_personne")),
    "mouvemente":           ("#facc15", td("rg3.onto_sys.rel.mouvemente")),
    "transige_via":         ("#c2410c", td("rg3.onto_sys.rel.transige_via")),
    "echange_dans":         ("#be185d", td("rg3.onto_sys.rel.echange_dans")),
    "diffuse":              ("#c084fc", td("rg3.onto_sys.rel.diffuse")),
    "laisse_trace":         ("#f87171", td("rg3.onto_sys.rel.laisse_trace")),
    "redige":               ("#4ade80", td("rg3.onto_sys.rel.redige")),
    "utilise_ia":           ("#6d28d9", td("rg3.onto_sys.rel.utilise_ia")),
    "construit":            ("#2dd4bf", td("rg3.onto_sys.rel.construit")),
    "administre":           ("#a8a29e", td("rg3.onto_sys.rel.administre")),
    "signe_bloc":           ("#a3e635", td("rg3.onto_sys.rel.signe_bloc")),
    "defini_dans":          ("#38bdf8", td("rg3.onto_sys.rel.defini_dans")),
    "inscrit_dans":         ("#4d7c0f", td("rg3.onto_sys.rel.inscrit_dans")),
    "trace_dans":           ("#ef4444", td("rg3.onto_sys.rel.trace_dans")),
}

# Objet du graphe (type de nœud) → (clé de la table système qui le consigne, relation). Le nœud pointe VERS sa table.
ENTITES_VERS_TABLE = {
    "dashboard": ("tableaubordbi", "defini_dans"), "kpi": ("indicateurkpi", "defini_dans"), "calcul": ("tablecalcul", "defini_dans"),
    "skill": ("skill", "defini_dans"), "modele_ia": ("modeleia", "defini_dans"), "config_ia": ("configurationia", "defini_dans"),
    "groupe_tables": ("groupetables", "defini_dans"), "tache_planifiee": ("tacheplanifiee", "defini_dans"),
    "connexion_externe": ("connexionexterne", "defini_dans"), "groupe_partage_ligne": ("groupepartageligne", "defini_dans"),
    "groupe_partage_cellule": ("groupepartagecellule", "defini_dans"),
    "document": ("documentgenere", "inscrit_dans"), "tokens": ("compteurtokens", "inscrit_dans"), "publicite": ("publicites", "inscrit_dans"),
    "conversation": ("conversations", "inscrit_dans"), "abonnement": ("abonnements", "inscrit_dans"), "bloc": ("bloc", "inscrit_dans"),
    "ip_source": ("journalaudit", "trace_dans"), "alerte_siem": ("journalaudit", "trace_dans"),
}
MAX_OBJETS_PAR_TYPE = 40    # blocs, documents… : les premiers seulement, pour ne pas noyer le graphe
MAX_PERSONNES_PAR_TABLE = 30
# Relation personne → table selon le domaine de la table
RELATION_PERSONNE = {
    "identite": "recense_personne", "finance": "mouvemente", "paiement": "transige_via", "communication": "echange_dans",
    "publicite": "diffuse", "audit": "laisse_trace", "documents": "redige", "ia": "utilise_ia", "analytique": "construit",
    "structure": "administre", "blockchain": "signe_bloc",
}

# Flux métier du wallet et du paiement : (source, cible, relation). Les clés sont celles de tables_systeme.registre (testées).
LIENS_METIER = (
    ("depots_paiement", "ecritures_wallet", "credite_wallet"),
    ("retraits_wallet", "ecritures_wallet", "debite_wallet"),
    ("remunerations", "ecritures_wallet", "verse_remuneration"),
    ("charges_wallet", "ecritures_wallet", "impute_charge"),
    ("abonnements", "ecritures_wallet", "facture_abonnement"),
    ("frais_wallet", "ecritures_wallet", "applique_frais"),
    ("taux_change", "ecritures_wallet", "convertit_devise"),
    ("politique_transfert", "ecritures_wallet", "encadre_transfert"),
    ("habilitations_flux", "ecritures_wallet", "habilite_flux"),
    ("ecritures_wallet", "wallets", "met_a_jour_solde"),
)
CLE_BLOC = "bloc"
COLONNE_BLOC = "bloc_index"

# Relation d'une clé étrangère qui croise deux domaines : (domaine source, domaine cible) → relation. Défaut : reference_systeme.
_CROISEES = {("documents", "ia"): "genere_par_ia", ("structure", "analytique"): "partage_objet", ("structure", "structure"): "partage_objet"}
_DOMAINES_ANALYSE = {"analytique", "ia"}


# ── Calcul pur ─────────────────────────────────────────────────────────────────────────────────────────────────
def domaine_de(cle: str) -> str:
    """Domaine d'une source de tables_systeme.registre (clé explicite ou nom de modèle en minuscules). Jamais d'erreur : un modèle
    inconnu est rangé par mot-clé, à défaut dans « structure »."""
    cle = (cle or "").lower()
    if cle in _DOMAINE_PAR_CLE:
        return _DOMAINE_PAR_CLE[cle]
    for mot, domaine in _MOTS_CLES:
        if mot in cle:
            return domaine
    return DOMAINE_PAR_DEFAUT


def type_noeud_table(source_systeme: str) -> str:
    """Type de nœud d'une table : « table » pour une table métier, `table_systeme_<domaine>` pour une table système."""
    return PREFIXE_TYPE + domaine_de(source_systeme) if source_systeme else "table"


def est_type_table(type_noeud: str) -> bool:
    return type_noeud == "table" or str(type_noeud).startswith(PREFIXE_TYPE)


def libelle_systeme(prefixe: str, slug: str):
    """Libellé d'affichage d'un type propre à cette couche (None pour tout autre identifiant : le catalogue i18n s'en charge)."""
    if prefixe == "ontologie.type" and slug in LIBELLES_TYPES:
        return LIBELLES_TYPES[slug]
    return None


def relation_fk(domaine_src: str, domaine_dst: str, vers_membre: bool) -> str:
    """Relation portée par une clé étrangère entre deux tables système."""
    if vers_membre:
        return "trace_acteur" if domaine_src == "audit" else "concerne_membre"
    if domaine_src == domaine_dst:
        return "detail_de"
    return _CROISEES.get((domaine_src, domaine_dst), "reference_systeme")


def relation_pont(domaine_src: str, cle_src: str) -> str:
    """Relation d'une table système vers une table MÉTIER qu'elle référence."""
    if cle_src == "membres":
        return "table_clients"
    return "analyse_table" if domaine_src in _DOMAINES_ANALYSE else "porte_sur_table"


def _ajouter_lien(liens: list, vues: set, sources: dict, a: str, b: str, relation: str, detail: str) -> None:
    """Ajoute (a → b) si les deux sources existent et si la paire orientée n'a pas déjà un lien (le premier posé l'emporte)."""
    if a in sources and b in sources and a != b and (a, b) not in vues:
        vues.add((a, b))
        liens.append((a, b, relation, detail))


def planifier_liens(sources: dict, cles_etrangeres: list) -> list:
    """Liens entre tables système : [(cle_source, cle_cible, relation, détail)], sans doublon de paire.
    `sources` : {cle: {"colonnes": [slugs]}} ; `cles_etrangeres` : [(cle_source, nom_du_champ, cle_cible, vers_membre)].
    Ordre de priorité (une seule arête par paire orientée) : flux métier, ancrage blockchain, puis clés étrangères."""
    liens, vues = [], set()
    for a, b, relation in LIENS_METIER:
        _ajouter_lien(liens, vues, sources, a, b, relation, "")
    if CLE_BLOC in sources:
        for cle, src in sources.items():
            if COLONNE_BLOC in src.get("colonnes", ()):
                _ajouter_lien(liens, vues, sources, cle, CLE_BLOC, "ancre_blockchain", COLONNE_BLOC)
    regroupees: dict = {}
    for cle_src, champ, cle_dst, vers_membre in cles_etrangeres:
        regroupees.setdefault((cle_src, cle_dst, vers_membre), []).append(champ)
    for (cle_src, cle_dst, vers_membre), champs in regroupees.items():
        if cle_src in sources and cle_dst in sources:
            _ajouter_lien(liens, vues, sources, cle_src, cle_dst, relation_fk(domaine_de(cle_src), domaine_de(cle_dst), vers_membre),
                          ", ".join(sorted(set(champs))))
    return liens


# ── Lecture du code et de la base ─────────────────────────────────────────────────────────────────────────────────
def _cles_etrangeres(registre: list) -> tuple:
    """(clés étrangères entre sources du registre, clés étrangères vers TableDynamique) lues dans les modèles Django.
    Les clés vers l'utilisateur Django comptent comme « membre » (audit, journaux). Retourne ([(cle, champ, cle_cible, vers_membre)],
    [(cle, attname, nom_du_champ)])."""
    from django.conf import settings
    from .models import Entreprise, MembreEntreprise, TableDynamique
    cle_par_modele = {s["modele"]: s["cle"] for s in registre}
    cle_membres = cle_par_modele.get(MembreEntreprise)
    modele_utilisateur = settings.AUTH_USER_MODEL.lower()
    entre_tables, vers_tables = [], []
    for src in registre:
        for f in src["modele"]._meta.concrete_fields:
            cible = getattr(f, "related_model", None)
            if cible is None or not (f.many_to_one or f.one_to_one):
                continue
            if cible is Entreprise:
                continue   # toute table en dépend : un lien par table noierait le graphe
            if cible is TableDynamique:
                vers_tables.append((src["cle"], f.attname, str(f.name)))
            elif cible in cle_par_modele and cible is not src["modele"]:
                entre_tables.append((src["cle"], str(f.name), cle_par_modele[cible], cible is MembreEntreprise))
            elif cle_membres and cible._meta.label_lower == modele_utilisateur:
                entre_tables.append((src["cle"], str(f.name), cle_membres, True))
    return entre_tables, vers_tables


def _ajouter_arete(g, u, v, **attrs) -> bool:
    if g.has_node(u) and g.has_node(v) and u != v and not g.has_edge(u, v):
        g.add_edge(u, v, **attrs)
        return True
    return False


def _proteger(nom: str, fonction, *args) -> None:
    """Un bloc qui échoue n'empêche ni les autres ni le reste du graphe."""
    try:
        fonction(*args)
    except Exception:
        logger.exception(_gt("rg6.msg.072"), nom)


def _poser_noeuds(g, entreprise, noeud_par_cle: dict, registre: list) -> None:
    """Volume réel de chaque table système, domaine, libellé court (la couleur vient du type de nœud, donc du domaine)."""
    par_cle = {s["cle"]: s for s in registre}
    for cle, nid in noeud_par_cle.items():
        attrs = g.nodes[nid]
        try:
            nb = par_cle[cle]["filtre"](entreprise).count()
        except Exception:
            nb = 0
        attrs.update(nb_enregistrements=nb, domaine=domaine_de(cle), systeme=True, vide=(nb == 0))
        if str(attrs.get("label", "")).startswith(PREFIXE_NOM_TABLE):
            attrs["label"] = attrs["label"][len(PREFIXE_NOM_TABLE):]


def _poser_liens_systeme(g, noeud_par_cle: dict, registre: list) -> None:
    """Entre tables système : flux du wallet, ancrage blockchain, clés étrangères réelles."""
    sources = {s["cle"]: {"colonnes": [c["slug"] for c in s["colonnes"]]} for s in registre if s["cle"] in noeud_par_cle}
    entre_tables, _vers = _cles_etrangeres(registre)
    for cle_src, cle_dst, relation, detail in planifier_liens(sources, entre_tables):
        u, v = noeud_par_cle[cle_src], noeud_par_cle[cle_dst]
        _ajouter_arete(g, u, v, relation=relation, poids=max(1, g.nodes[u].get("nb_enregistrements", 0)), champ=detail)


def _poser_ponts_metier(g, entreprise, noeud_par_cle: dict, registre: list) -> None:
    """Table système → table MÉTIER réellement visée : valeurs des clés étrangères vers TableDynamique (un COUNT groupé par colonne)."""
    from django.db.models import Count
    par_cle = {s["cle"]: s for s in registre}
    _entre, vers_tables = _cles_etrangeres(registre)
    for cle, attname, nom_champ in vers_tables:
        if cle not in noeud_par_cle:
            continue
        relation = relation_pont(domaine_de(cle), cle)
        lignes = (par_cle[cle]["filtre"](entreprise).exclude(**{attname: None}).order_by()
                  .values_list(attname).annotate(n=Count("pk")))
        for table_id, n in lignes:
            _ajouter_arete(g, noeud_par_cle[cle], f"table:{table_id}", relation=relation, poids=n, champ=nom_champ)


def _poser_entites(g, noeud_par_cle: dict) -> None:
    """Chaque objet du graphe (dashboard, KPI, modèle IA, bloc, document, discussion, IP…) pointe vers la table système qui le consigne."""
    compte: dict = {}
    for nid, attrs in list(g.nodes(data=True)):
        type_objet = attrs.get("type")
        cible = ENTITES_VERS_TABLE.get(type_objet)
        if cible is None or cible[0] not in noeud_par_cle:
            continue
        compte[type_objet] = compte.get(type_objet, 0) + 1
        if compte[type_objet] <= MAX_OBJETS_PAR_TYPE:
            _ajouter_arete(g, nid, noeud_par_cle[cible[0]], relation=cible[1], poids=1)


def _fks_personnes(registre: list) -> list:
    """[(cle, attname, nom_du_champ, "membre" | "utilisateur")] : clés étrangères vers un membre de l'entreprise ou vers l'utilisateur Django."""
    from django.conf import settings
    from .models import MembreEntreprise
    modele_utilisateur = settings.AUTH_USER_MODEL.lower()
    sortie = []
    for src in registre:
        for f in src["modele"]._meta.concrete_fields:
            cible = getattr(f, "related_model", None)
            if cible is None or not (f.many_to_one or f.one_to_one):
                continue
            if cible is MembreEntreprise:
                sortie.append((src["cle"], f.attname, str(f.name), "membre"))
            elif cible._meta.label_lower == modele_utilisateur:
                sortie.append((src["cle"], f.attname, str(f.name), "utilisateur"))
    return sortie


def _index_personnes(g, entreprise, prefixe_role: dict) -> tuple:
    """({id de membre: nœud personne}, {id d'utilisateur: nœud personne}) pour les personnes présentes dans ce graphe."""
    from .models import MembreEntreprise
    par_membre, par_utilisateur = {}, {}
    for m in MembreEntreprise.objects.filter(entreprise=entreprise):
        nid = f"{prefixe_role.get(m.role, 'agent')}:{m.id}"
        if g.has_node(nid):
            par_membre[m.id] = nid
            if m.utilisateur_id:
                par_utilisateur[m.utilisateur_id] = nid
    return par_membre, par_utilisateur


def _poser_activite_personnes(g, entreprise, noeud_par_cle: dict, registre: list, prefixe_role: dict) -> None:
    """Personne → tables système où elle a RÉELLEMENT des lignes (poids = nombre de lignes), relation selon le domaine de la table.
    Plusieurs clés d'une même table vers la même personne (source, destinataire, opérateur…) s'additionnent en un seul lien."""
    from django.db.models import Count
    par_membre, par_utilisateur = _index_personnes(g, entreprise, prefixe_role)
    if not par_membre:
        return
    par_cle = {s["cle"]: s for s in registre}
    cumul: dict = {}
    for cle, attname, nom_champ, genre in _fks_personnes(registre):
        if cle not in noeud_par_cle or cle == "membres":
            continue
        index = par_membre if genre == "membre" else par_utilisateur
        lignes = (par_cle[cle]["filtre"](entreprise).exclude(**{attname: None}).order_by()
                  .values_list(attname).annotate(n=Count("pk")))
        for valeur, n in lignes:
            nid_personne = index.get(valeur)
            if nid_personne:
                entree = cumul.setdefault((cle, nid_personne), [0, set()])
                entree[0] += n
                entree[1].add(nom_champ)
    par_table: dict = {}
    for (cle, nid_personne), (n, champs) in cumul.items():
        par_table.setdefault(cle, []).append((n, nid_personne, champs))
    for cle, liste in par_table.items():
        relation = RELATION_PERSONNE.get(domaine_de(cle), "administre")
        for n, nid_personne, champs in sorted(liste, key=lambda x: -x[0])[:MAX_PERSONNES_PAR_TABLE]:
            _ajouter_arete(g, nid_personne, noeud_par_cle[cle], relation=relation, poids=n, champ=", ".join(sorted(champs)))


def _poser_personnes(g, noeud_par_cle: dict, prefixe_role: dict) -> None:
    """La table système des membres recense chaque personne du graphe (administrateurs, agents, clients)."""
    nid_membres = noeud_par_cle.get("membres")
    if not nid_membres:
        return
    prefixes = tuple(f"{p}:" for p in set(prefixe_role.values()))
    personnes = [n for n in g.nodes if n.startswith(prefixes)][:MAX_PERSONNES]
    for n in personnes:
        _ajouter_arete(g, nid_membres, n, relation="recense_personne", poids=1)


def rattacher_orphelines(g, racine: str, noeud_par_cle: dict) -> None:
    """Une table système restée sans aucun lien (aucune donnée, aucun objet) reste rattachée à l'entreprise : jamais invisible."""
    for nid in noeud_par_cle.values():
        if g.has_node(nid) and g.degree(nid) == 0:
            g.add_edge(racine, nid, relation="possede")


def enrichir_systeme(g, entreprise, racine, prefixe_role, membre=None) -> None:
    """Mélange les tables système au graphe existant. À appeler APRÈS la pose des personnes, objets, blocs, discussions et SIEM : elle relie
    les nœuds `table:<id>` typés `table_systeme_*` (voir type_noeud_table) à ces nœuds. Sans table système dans ce graphe, ne fait rien.
    Jamais bloquante."""
    from . import tables_systeme
    from .models import TableDynamique
    try:
        registre = tables_systeme.registre()
        ids = dict(TableDynamique.objects.filter(entreprise=entreprise).exclude(source_systeme="").values_list("source_systeme", "id"))
    except Exception:
        logger.exception(_gt("rg6.msg.073"))
        return
    noeud_par_cle = {cle: f"table:{i}" for cle, i in ids.items() if g.has_node(f"table:{i}")}
    if not noeud_par_cle:
        return
    prefixe_role = prefixe_role or {}
    _proteger(_gtfr("engine/ontologie_systeme.py:430:15"), _poser_noeuds, g, entreprise, noeud_par_cle, registre)
    _proteger(_gt("rg6.msg.074"), _poser_liens_systeme, g, noeud_par_cle, registre)
    _proteger(_gt("rg6.msg.075"), _poser_ponts_metier, g, entreprise, noeud_par_cle, registre)
    _proteger(_gt("rg6.msg.076"), _poser_entites, g, noeud_par_cle)
    _proteger(_gt("rg6.msg.077"), _poser_activite_personnes, g, entreprise, noeud_par_cle, registre, prefixe_role)
    _proteger("membres", _poser_personnes, g, noeud_par_cle, prefixe_role)
    rattacher_orphelines(g, racine, noeud_par_cle)


__all__ = ["TYPES_NOEUDS_SYSTEME", "RELATIONS_SYSTEME", "DOMAINES", "TYPE_CHAMP_SYSTEME", "ENTITES_VERS_TABLE", "domaine_de", "type_noeud_table",
           "est_type_table", "libelle_systeme", "relation_fk", "relation_pont", "planifier_liens", "enrichir_systeme"]
