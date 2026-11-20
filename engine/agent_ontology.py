"""Phase 5 du cahier des charges Ontology — Agent GY connecté à la couche
Ontology :

    Utilisateur -> Agent -> intention -> Ontology (Objects/Links déjà
    interrogeables via ontologie_requetes.py) -> Actions -> Résultat

Ceci n'introduit AUCUN nouveau moteur :
- "Objects/Links" = le graphe déjà construit par ontologie.construire_graphe,
  déjà interrogeable (ontologie_requetes.py, phase antérieure) ;
- "Actions" = le registre actions._REGISTRE (Phases 3+4, permission+audit+
  lignée déjà en place) ;
- seule la CLASSIFICATION D'INTENTION est nouvelle ici : un appel LLM
  supplémentaire, en JSON strict, posé AVANT le pipeline existant de
  génération de script (agent.py::generer_script_demande), qui reste
  totalement inchangé pour toute demande d'ANALYSE — c'est le chemin
  "complexe" du point 6 du cahier des charges :

        Demande
          ├── opération connue   -> Action (ce module)
          └── analyse complexe   -> Python/IA -> Sandbox (agent.py, inchangé)

Deux issues pour une demande reconnue comme une opération connue :

- EXÉCUTION DIRECTE ("_CIBLE_AUTO") : pour les Actions dont l'effet est
  sans risque et immédiatement réversible (lecture, recalcul idempotent,
  ou mutation de configuration mineure). La permission normale
  (actions.executer_action, donc JournalAudit + lignée Bloc comme n'importe
  quel appel existant) s'applique sans rien d'assoupli — y compris la
  granularité Objet (Phase 9) une fois la cible RÉELLE résolue (voir
  _resoudre_cible ci-dessous), jamais seulement l'entreprise.
- PROPOSITION avec confirmation humaine explicite ("_CIBLE_CONFIRMATION") :
  pour toute Action dont l'effet est sensible ou irréversible. Rien n'est
  exécuté : la proposition est stockée dans DemandeAgent.resultat_json et
  n'est exécutée que par confirmer_et_executer(), sur une action explicite
  de l'utilisateur (voir views.api_confirmer_action_demande) — jamais par
  l'agent seul (cahier des charges, section 15). La cible n'est JAMAIS
  conservée comme objet Python d'une requête à l'autre : seuls son type et
  son id sont stockés, et confirmer_et_executer la RE-résout par id au
  moment de la confirmation (jamais de confiance dans un objet vieux d'une
  requête précédente).

LOT 2 (30 Actions déclarées, 24 couvertes) : le lot précédent excluait
toute Action dont la cible n'est PAS l'entreprise elle-même (nécessitant
de retrouver un AUTRE enregistrement par nom — un membre, un KPI, une
table de calcul, une table). Ce lot a ajouté la RÉSOLUTION DE CIBLE PAR
NOM (_resoudre_cible) : le LLM identifie l'objet visé en texte libre
("Jean Dupont", "CA mensuel", "Table Clients"...), résolu ensuite côté
serveur par correspondance exacte puis partielle SEULEMENT si le résultat
est sans ambiguïté (sinon abandon silencieux, comme le reste de ce
module) — jamais une résolution devinée. A ajouté aussi la FINALISATION DE
PARAMÈTRES (_finaliser_parametres) pour les Actions dont un paramètre ne
doit jamais venir du texte libre : mot de passe généré aléatoirement
(creer_employe), fichier réel d'une archive déjà existante identifiée
par son id (restaurer_cycle).

LOT 3 (26 des 30 désormais couvertes, contre 24) : ajoute 2 des 6 Actions
jusqu'ici hors de portée, toutes deux réductibles au même mécanisme de
résolution que le lot précédent — via une extension : les PARAMÈTRES
VIRTUELS (_PARAMS_VIRTUELS), des clés montrées au LLM dans le prompt de
classification mais qui n'existent PAS dans la signature réelle de
l'Action ; _finaliser_parametres les consomme pour PRODUIRE le paramètre
réel (jamais l'inverse — le LLM ne voit et ne fournit jamais le paramètre
réel quand il est opaque) :
- tracer_lineage_valeur : `noeud_id` (ex. "table:5") reste un identifiant
  opaque JAMAIS exposé au LLM (masqué comme un _PARAMS_SERVEUR) ; le LLM
  fournit à la place deux paramètres virtuels — `type_objet` (un des types
  résolubles : table, calcul, kpi, agent) et `nom_objet` (son nom en texte)
  — résolus par les mêmes fonctions _resoudre_* que le reste du module,
  puis assemblés en "type:id" par _finaliser_parametres. Limite restante,
  assumée : seuls ces 4 types sont couverts (pas "champ", dont le nom seul
  est ambigu entre tables ; pas "skill"/"dashboard"/"modele_ia", non
  demandés dans le cahier) — un type hors liste fait échouer la
  résolution (abandon silencieux, même politique que le reste du module).
- regenerer_script_demande : sa cible EST une DemandeAgent — traitée
  maintenant comme n'importe quelle autre cible nommée (_CIBLE_TYPES =
  "demande_agent") : le LLM décrit la demande visée en texte libre (ex.
  "relance le script de ma demande sur les ventes de juin"), résolue par
  _resoudre_demande_agent (correspondance sur le texte de la demande,
  bornée aux demandes de CE membre si role=agent, uniquement parmi les
  demandes déjà TERMINÉES ou en ÉCHEC — jamais une demande en cours — et
  seulement si le résultat est unique, sinon abandon). Reste une Action à
  CONFIRMATION (relance un job Celery, effet non anodin).

Restent structurellement hors de portée d'un agent conversationnel en
TEXTE LIBRE (4 Actions, aucune n'est perdue : elles restent accessibles
comme avant, via leurs vues dédiées existantes, inchangées) :
- uploader_logo, configurer_carte_table, generer_cartes, generer_cartes_zip, generer_cartes_images
  : nécessitent un FICHIER (image/zip) — un texte ne transporte pas de
  bytes binaires. Contrairement aux deux ajouts ci-dessus, ceci n'est PAS
  réductible à un paramètre virtuel textuel : câbler ces 4 Actions
  demanderait un canal de PIÈCE JOINTE de bout en bout — un champ fichier
  sur DemandeAgent (migration), une proposition "en attente de pièce
  jointe" tant qu'aucun fichier n'est fourni, un endpoint de confirmation
  acceptant du multipart, et l'ajout correspondant côté widget de chat
  (index_1.js/template) — une extension d'INFRASTRUCTURE (pas seulement de
  classification) hors périmètre de ce module, volontairement non
  entamée ici plutôt que livrée à moitié et non vérifiable (pas
  d'exécution Django possible dans cet environnement).
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import t as _i18n_t, tf, tl

import json
import logging

from . import actions

logger = logging.getLogger("gyplatform.agent_ontology")

_CIBLE_AUTO = "auto"
_CIBLE_CONFIRMATION = "confirmation"

# Phase 5 suite (composition dynamique, critère de réussite du cahier des
# charges : "clients avec CA > 1M") — opérateurs de filtre acceptés, mêmes
# que grille._OPERATEUR/_LOOKUP (engine/grille.py), pour ne jamais accepter
# côté agent un opérateur que la grille elle-même ne saurait pas exécuter.
_OPERATEURS_FILTRE = {">", ">=", "<", "<=", "=", "!="}

# ── Actions dont la cible est l'ENTREPRISE elle-même : aucune résolution de
# cible n'est nécessaire, seulement (parfois) des paramètres. ─────────────
_CIBLE_AUTO_NOMS = {
    "modifier_limite_lignes", "modifier_limite_colonnes", "modifier_inscription_ouverte",
    "reinitialiser_tokens",
    "lister_archives_cycle", "exporter_employes_pdf", "lister_employes",
    # Lot 2 — lecture pure (trace_lineage=False) et recalculs idempotents :
    # aucun effet destructif, exécution directe une fois la cible résolue.
    "lire_permissions_membre", "recalculer_kpi", "recalculer_table_calcul",
    # Lot 3 — lecture pure (trace_lineage=False) : cible = entreprise (voir
    # _CIBLE_TYPES, absente pour cette Action), seul noeud_id est virtuel.
    "tracer_lineage_valeur",
}

_CIBLE_CONFIRMATION_NOMS = {
    "modifier_config_ia", "cloturer_cycle",
    "supprimer_employe", "modifier_role_employe",
    # Lot 2 — effet sensible (création de compte, restauration destructive
    # de cycle, modification d'identité/permissions d'un tiers, envoi
    # réel d'un SMS facturé) : jamais exécuté sans confirmation humaine.
    "creer_employe", "restaurer_cycle", "modifier_employe",
    "modifier_permissions_membre",
    # Lot 3 — relance un job Celery (script IA) sur une demande déjà
    # terminée/en échec : effet non anodin, jamais sans confirmation.
    "regenerer_script_demande",
}

_CLASSIFICATION_ACTIONS = {nom: _CIBLE_AUTO for nom in _CIBLE_AUTO_NOMS}
_CLASSIFICATION_ACTIONS.update({nom: _CIBLE_CONFIRMATION for nom in _CIBLE_CONFIRMATION_NOMS})

# Pour chaque Action dont la cible n'est PAS l'entreprise, le TYPE d'objet à
# résoudre par nom (voir _resoudre_cible) — absent de ce dict = cible
# entreprise (cas par défaut, aucune résolution).
_CIBLE_TYPES = {
    "lire_permissions_membre": "membre",
    "modifier_permissions_membre": "membre",
    "modifier_employe": "membre",
    "recalculer_kpi": "kpi",
    "recalculer_table_calcul": "table_calcul",
    # Lot 3 — cible = la DemandeAgent visée elle-même (voir
    # _resoudre_demande_agent), pas l'entreprise.
    "regenerer_script_demande": "demande_agent",
}

# Valeurs acceptées pour le paramètre virtuel `type_objet` de
# tracer_lineage_valeur (voir _PARAMS_VIRTUELS et _resoudre_noeud_lignee) —
# chaque valeur est aussi le préfixe exact du nœud dans le graphe Ontology
# (ontologie.construire_graphe). Volontairement restreint aux types dont la
# résolution par nom existe déjà et est sans ambiguïté structurelle (pas
# "champ" : un nom de colonne seul est ambigu entre plusieurs tables).
_TYPES_LIGNEE = {"table", "calcul", "kpi", "agent"}

# Actions dont un paramètre montré au LLM (dans le prompt de classification
# ET accepté dans sa réponse) n'existe PAS dans la signature réelle de la
# fonction : consommé uniquement par _finaliser_parametres pour PRODUIRE le
# ou les paramètres réels — jamais transmis tel quel à actions.executer_action.
# Permet de résoudre en LANGAGE NATUREL un paramètre qui serait autrement
# opaque (voir tracer_lineage_valeur), sans jamais exposer l'identifiant brut.
_PARAMS_VIRTUELS = {
    "tracer_lineage_valeur": ("type_objet", "nom_objet"),
}

# Paramètres qui ne doivent JAMAIS venir du texte libre (identité de
# l'exécutant, configuration serveur) : injectés/complétés côté serveur au
# moment de la FINALISATION (_finaliser_parametres), jamais exposés au LLM
# ni acceptés depuis la proposition stockée.
_PARAMS_SERVEUR = {
    "cloturer_cycle": {"utilisateur"},
    # noeud_id : identifiant opaque du graphe, jamais montré au LLM (voir
    # _PARAMS_VIRTUELS pour son équivalent en langage naturel) ; page_id :
    # portée d'une page Ontology précise, non pertinente pour une demande
    # formulée en texte libre (résolution sur l'ontologie complète).
    "tracer_lineage_valeur": {"noeud_id", "page_id"},
}

# Précisions textuelles ajoutées au prompt de classification pour les
# Actions dont un paramètre n'est pas un texte libre ordinaire — évite de
# dupliquer cette nuance dans la description métier de l'Action elle-même
# (actions.py), qui reste correcte pour ses autres appelants (vues Django).
_PARAMS_REQUIS = {
    # Actions dont la fonction n'a pas de valeur par défaut pour ce
    # paramètre : sans lui, l'exécution échouerait à coup sûr — mieux vaut
    # abandonner la proposition ici (comme le reste du module) que de la
    # montrer à l'utilisateur pour qu'elle échoue ensuite à la confirmation.
    "modifier_permissions_membre": {"vues"},
}

_NOTES_PARAMETRES = {
    "restaurer_cycle": (
        tl("engine/agent_ontology.py:g:211:9")
    ),
    "creer_employe": (
        tl("engine/agent_ontology.py:g:215:9")
    ),
    "tracer_lineage_valeur": (
        tl("engine/agent_ontology.py:g:221:9")
    ),
}


def _parametres_exposes(nom: str, parametres: list[str]) -> list[str]:
    """Paramètres réels montrés au LLM (masque _PARAMS_SERVEUR), plus les
    paramètres VIRTUELS de cette Action le cas échéant (_PARAMS_VIRTUELS) —
    ces derniers n'existent pas dans la signature réelle, voir leur
    définition ci-dessus et _finaliser_parametres pour leur résolution."""
    masques = _PARAMS_SERVEUR.get(nom, set())
    exposes = [p for p in parametres if p not in masques]
    exposes.extend(_PARAMS_VIRTUELS.get(nom, ()))
    return exposes


def _prompt_classification(catalogue: list[dict], texte_demande: str) -> str:
    lignes = []
    for a in catalogue:
        nom = a["nom"]
        expose = _parametres_exposes(nom, a["parametres"])
        ligne = tf("engine/agent_ontology.py:g:239:17", v1=nom, v2=", ".join(expose) or _i18n_t("rg.aucun"), v3=a["description"])
        cible_type = _CIBLE_TYPES.get(nom)
        if cible_type:
            ligne += tf("engine/agent_ontology.py:g:242:22", v1=cible_type)
        note = _NOTES_PARAMETRES.get(nom)
        if note:
            ligne += f" ({note})"
        lignes.append(ligne)
    lignes_txt = "\n".join(lignes)
    return tf("engine/agent_ontology.py:g:248:12", v1=lignes_txt, v2=texte_demande)


def _extraire_json(bloc: str) -> dict | None:
    try:
        return json.loads(bloc)
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning(_i18n_t("engine/agent_ontology.py:g:276:24"))
        return None


def _resoudre_par_nom(queryset, texte: str, champs: tuple[str, ...]):
    """Résout un objet unique par correspondance sur `champs` (noms de
    lookup Django, ex. "nom" ou "utilisateur__username") : essaie d'abord
    une correspondance EXACTE (insensible à la casse) ; si aucune, replie
    sur une correspondance PARTIELLE (icontains) — mais seulement si elle
    désigne un SEUL objet sans ambiguïté. Dans tous les autres cas (aucun
    résultat, ou plusieurs), retourne None : même politique que le reste de
    ce module, mieux vaut abandonner que deviner quel objet l'utilisateur
    visait réellement."""
    from django.db.models import Q

    texte = (texte or "").strip()
    if not texte:
        return None

    q_exact = Q()
    q_icontains = Q()
    for champ in champs:
        q_exact |= Q(**{f"{champ}__iexact": texte})
        q_icontains |= Q(**{f"{champ}__icontains": texte})

    exacts = list(queryset.filter(q_exact).distinct()[:2])
    if len(exacts) == 1:
        return exacts[0]
    if len(exacts) > 1:
        return None

    partiels = list(queryset.filter(q_icontains).distinct()[:2])
    return partiels[0] if len(partiels) == 1 else None


def _resoudre_membre(entreprise, texte: str):
    """Résout un MembreEntreprise par nom complet, prénom/nom séparés, ou
    identifiant de connexion — jamais hors de l'entreprise courante."""
    from django.db.models import Value
    from django.db.models.functions import Concat
    from .models import MembreEntreprise

    qs = (
        MembreEntreprise.objects.filter(entreprise=entreprise)
        .select_related("utilisateur")
        .annotate(nom_complet=Concat("utilisateur__first_name", Value(" "), "utilisateur__last_name"))
    )
    return _resoudre_par_nom(qs, texte, (
        "utilisateur__username", "utilisateur__first_name", "utilisateur__last_name", "nom_complet",
    ))


def _resoudre_kpi(membre_appelant, entreprise, texte: str):
    """Résout un IndicateurKPI par nom, restreint à ceux dont la table
    source est autorisée pour `membre_appelant` (même granularité Objet,
    Phase 9, que composer_requete_libre pour les tables)."""
    from .models import IndicateurKPI
    from .views import _table_autorisee_agent

    qs = IndicateurKPI.objects.filter(entreprise=entreprise).select_related("table_source")
    kpi = _resoudre_par_nom(qs, texte, ("nom",))
    if kpi is None or not _table_autorisee_agent(membre_appelant, kpi.table_source):
        return None
    return kpi


def _resoudre_table_calcul(membre_appelant, entreprise, texte: str):
    from .models import TableCalcul
    from .views import _table_autorisee_agent

    qs = TableCalcul.objects.filter(entreprise=entreprise).select_related("table_source")
    tc = _resoudre_par_nom(qs, texte, ("nom",))
    if tc is None or not _table_autorisee_agent(membre_appelant, tc.table_source):
        return None
    return tc


def _resoudre_table(membre_appelant, entreprise, texte: str):
    from .models import TableDynamique
    from .views import _table_autorisee_agent

    qs = TableDynamique.objects.filter(entreprise=entreprise)
    table = _resoudre_par_nom(qs, texte, ("nom", "slug"))
    if table is None or not _table_autorisee_agent(membre_appelant, table):
        return None
    return table


def _resoudre_cible(nom: str, membre_appelant, entreprise, texte_cible: str):
    """Point d'entrée unique de résolution de cible pour une Action hors
    « entreprise elle-même » (voir _CIBLE_TYPES). Retourne l'objet résolu,
    ou None si la résolution échoue ou est ambiguë (la demande abandonne
    alors silencieusement, comme _resoudre_par_nom)."""
    cible_type = _CIBLE_TYPES.get(nom)
    if cible_type is None:
        return entreprise
    if cible_type == "membre":
        return _resoudre_membre(entreprise, texte_cible)
    if cible_type == "kpi":
        return _resoudre_kpi(membre_appelant, entreprise, texte_cible)
    if cible_type == "table_calcul":
        return _resoudre_table_calcul(membre_appelant, entreprise, texte_cible)
    if cible_type == "table":
        return _resoudre_table(membre_appelant, entreprise, texte_cible)
    if cible_type == "demande_agent":
        return _resoudre_demande_agent(membre_appelant, entreprise, texte_cible)
    return None


def _resoudre_demande_agent(membre_appelant, entreprise, texte: str):
    """Résout la DemandeAgent VISÉE par une autre demande (Lot 3 —
    regenerer_script_demande) : correspondance sur le texte de la demande
    elle-même (une phrase, pas un nom court — pas de correspondance
    « exacte » utile ici, contrairement à _resoudre_par_nom), restreinte
    aux demandes déjà TERMINÉES ou en ÉCHEC (jamais une demande en cours :
    la relancer n'aurait pas de sens et créerait une double exécution
    Celery concurrente), et à celles du membre appelant s'il est un agent
    (même isolation que le reste du module — un agent ne régénère jamais
    le script d'un autre). Comme _resoudre_par_nom : None si aucune
    correspondance unique."""
    from .models import DemandeAgent

    texte = (texte or "").strip()
    if not texte:
        return None

    qs = DemandeAgent.objects.filter(
        entreprise=entreprise, statut__in=("termine", "echec"), texte__icontains=texte,
    )
    if membre_appelant is not None and membre_appelant.role == "agent":
        qs = qs.filter(utilisateur=membre_appelant.utilisateur)

    candidats = list(qs.order_by("-id")[:2])
    return candidats[0] if len(candidats) == 1 else None


def _resoudre_noeud_lignee(membre_appelant, entreprise, type_objet, nom_objet: str) -> str | None:
    """Traduit les paramètres virtuels (type_objet, nom_objet) de
    tracer_lineage_valeur en `noeud_id` réel du graphe Ontology ("type:id"),
    en réutilisant les résolveurs par nom déjà existants (mêmes règles
    d'ambiguïté, même isolation par agent) — voir _TYPES_LIGNEE pour les
    types couverts. None si le type est hors liste ou la résolution
    échoue/est ambiguë."""
    if type_objet not in _TYPES_LIGNEE or not (nom_objet or "").strip():
        return None
    if type_objet == "table":
        obj = _resoudre_table(membre_appelant, entreprise, nom_objet)
    elif type_objet == "calcul":
        obj = _resoudre_table_calcul(membre_appelant, entreprise, nom_objet)
    elif type_objet == "kpi":
        obj = _resoudre_kpi(membre_appelant, entreprise, nom_objet)
    elif type_objet == "agent":
        candidat = _resoudre_membre(entreprise, nom_objet)
        # Le préfixe de nœud "agent:" (ontologie.construire_graphe) ne
        # couvre QUE les membres role="agent" — un admin résolu par
        # _resoudre_membre n'a pas de nœud sous ce préfixe (le sien est
        # "admin:", hors périmètre de _TYPES_LIGNEE) : mieux vaut abandonner
        # que produire un noeud_id qui n'existera jamais dans le graphe.
        obj = candidat if candidat is not None and candidat.role == "agent" else None
    else:
        obj = None
    return f"{type_objet}:{obj.id}" if obj is not None else None


def _generer_mot_de_passe() -> str:
    import secrets
    return secrets.token_urlsafe(9)


def _finaliser_parametres(nom: str, entreprise, parametres: dict, membre=None) -> dict | None:
    """Dernière étape, appelée juste avant actions.executer_action (chemin
    AUTO comme chemin CONFIRMATION) : résout les paramètres qui ne doivent
    jamais venir tels quels du texte libre. Ne touche jamais à la CIBLE
    (voir _resoudre_cible, déjà résolue à ce stade) — uniquement des
    paramètres internes à l'appel. Retourne None si la finalisation est
    impossible (abandon silencieux, même politique que le reste du
    module)."""
    parametres = dict(parametres)

    if nom == "creer_employe":
        donnees = dict(parametres.get("donnees") or {})
        if not donnees.get("username") or not donnees.get("first_name") or not donnees.get("last_name"):
            return None
        donnees.pop("password", None)
        mot_de_passe = _generer_mot_de_passe()
        donnees["password"] = mot_de_passe
        parametres["donnees"] = donnees
        parametres["_mot_de_passe_genere"] = mot_de_passe  # lu par _resume_resultat, jamais transmis à _creer_employe

    elif nom == "restaurer_cycle":
        from .models import ArchiveCycle
        archive_id = parametres.get("source")
        if not archive_id or not str(archive_id).isdigit():
            return None
        archive = ArchiveCycle.objects.filter(id=archive_id, entreprise=entreprise).first()
        if archive is None:
            return None
        parametres["source"] = archive.fichier

    elif nom == "tracer_lineage_valeur":
        type_objet = parametres.pop("type_objet", None)
        nom_objet = parametres.pop("nom_objet", None)
        noeud_id = _resoudre_noeud_lignee(membre, entreprise, type_objet, nom_objet)
        if noeud_id is None:
            return None
        parametres["noeud_id"] = noeud_id
        parametres.setdefault("page_id", None)

    return parametres


def classifier_intention(demande, membre) -> dict | None:
    """Un appel LLM supplémentaire (JSON strict) pour savoir si `demande.texte`
    correspond à une Action connue. Retourne None si aucune correspondance
    nette n'est trouvée (le pipeline d'analyse habituel doit alors
    s'appliquer normalement), sinon {"nom": str, "parametres": dict,
    "cible": objet_resolu, "descripteur": ActionDescriptor}."""
    from .inference import MoteurInference

    noms_valides = set(_CLASSIFICATION_ACTIONS)
    catalogue = actions.catalogue_pour_agent(membre, noms_valides)
    if not catalogue:
        return None

    moteur = MoteurInference(entreprise=demande.entreprise)
    prompt = _prompt_classification(catalogue, demande.texte)
    bloc, erreur = moteur.generer_json(prompt, timeout=30)
    if erreur or not bloc:
        return None

    resultat = _extraire_json(bloc)
    if not resultat or not resultat.get("action"):
        return None

    nom = resultat["action"]
    if nom not in noms_valides:
        logger.warning(_i18n_t("engine/agent_ontology.py:g:524:24"), nom)
        return None

    descripteur = actions._REGISTRE.get(nom)
    if descripteur is None:
        return None

    cible = _resoudre_cible(nom, membre, demande.entreprise, resultat.get("cible") or "")
    if cible is None:
        # Soit l'entreprise (jamais None), soit une résolution de cible
        # nommée qui a échoué/est ambiguë : dans ce second cas, abandon.
        return None

    if not actions._permission_autorisee(membre, descripteur, cible):
        return None

    parametres_bruts = resultat.get("parametres") or {}
    exposes = set(_parametres_exposes(nom, list(inspect_parametres(descripteur))))
    parametres = {k: v for k, v in parametres_bruts.items() if k in exposes}

    requis = _PARAMS_REQUIS.get(nom)
    if requis and not requis.issubset(parametres):
        return None

    return {"nom": nom, "parametres": parametres, "cible": cible, "descripteur": descripteur}


def inspect_parametres(descripteur) -> list[str]:
    """Même règle que actions.catalogue_pour_agent (premier paramètre =
    cible, jamais un paramètre libre) — évite d'importer `inspect` deux fois
    pour la même règle."""
    import inspect
    try:
        return list(inspect.signature(descripteur.fonction).parameters)[1:]
    except (TypeError, ValueError):
        return []


def _resume_resultat(nom: str, resultat, parametres: dict | None = None) -> str:
    """Résumé textuel court — ne tente jamais de sérialiser l'objet Django
    retourné (modèles, bytes PDF...). Le détail d'audit complet (qui, quand,
    quel paramètre, succès/échec) est déjà tracé inconditionnellement par
    executer_action (JournalAudit + lignée Bloc) : rien n'est perdu à ne
    garder ici qu'un résumé pour l'affichage."""
    parametres = parametres or {}
    if nom == "lister_archives_cycle":
        return tf("engine/agent_ontology.py:g:575:16", v1=len(resultat))
    if nom == "lister_employes":
        return tf("engine/agent_ontology.py:g:577:16", v1=resultat['total'], v2=resultat['page'], v3=resultat['pages'])
    if nom == "exporter_employes_pdf":
        return _i18n_t("engine/agent_ontology.py:g:579:16")
    if nom == "creer_employe":
        mdp = parametres.get("_mot_de_passe_genere", "")
        return tf("engine/agent_ontology.py:g:582:16", v1=resultat.username, v2=mdp)
    if nom == "recalculer_kpi":
        if resultat.get("erreur"):
            return tf("engine/agent_ontology.py:g:585:20", v1=resultat['erreur'])
        return tf("engine/agent_ontology.py:g:586:16", v1=resultat.get('valeur'))
    if nom == "recalculer_table_calcul":
        return tf("engine/agent_ontology.py:g:588:16", v1=len(resultat))
    if nom == "lire_permissions_membre":
        return tf("engine/agent_ontology.py:g:590:16", v1=resultat['role'], v2=resultat['vues_autorisees'] or 'toutes')
    if nom == "modifier_permissions_membre":
        return _i18n_t("engine/agent_ontology.py:g:592:16")
    if nom == "modifier_employe":
        return _i18n_t("engine/agent_ontology.py:g:594:16")
    if nom == "restaurer_cycle":
        return _i18n_t("engine/agent_ontology.py:g:598:16")
    if nom == "tracer_lineage_valeur":
        if resultat.get("erreur"):
            return tf("engine/agent_ontology.py:g:601:20", v1=resultat['erreur'])
        return tf("engine/agent_ontology.py:g:602:16", v1=len(resultat.get('amont', [])), v2=len(resultat.get('aval', [])))
    if nom == "regenerer_script_demande":
        return _i18n_t("engine/agent_ontology.py:g:604:16")
    return _gt("engine/agent_ontology.py:580:12")


def _prompt_filtre(catalogue: list[dict], texte_demande: str) -> str:
    lignes = "\n".join(
        tf("engine/agent_ontology.py:g:605:9", v1=t["slug"], v2=t["nom"])
        + ", ".join(f'{c["slug"]} ({c["type"]})' for c in t["champs"])
        for t in catalogue
    )
    return tf("engine/agent_ontology.py:g:609:12", v1=lignes, v2=texte_demande)


def _catalogue_filtre_pour(membre, entreprise) -> list[dict]:
    from .models import TableDynamique
    from .views import _table_autorisee_agent, _champs_masques_pour

    catalogue = []
    for table in TableDynamique.objects.filter(entreprise=entreprise):
        if not _table_autorisee_agent(membre, table):
            continue
        masques = _champs_masques_pour(membre, table)
        champs = [c for c in table.champs.all() if c.slug not in masques]
        if not champs:
            continue
        catalogue.append({
            "table": table, "slug": table.slug, "nom": table.nom,
            "champs": [{"slug": c.slug, "type": c.type_champ} for c in champs],
        })
    return catalogue


def _generer_rapport_filtre(demande, membre, table, qs, resume_filtres) -> str | None:
    """Chaînage Filtre -> Action de génération de rapport, en une seule
    demande (critère de réussite explicite du cahier des charges — voir
    composer_requete_libre). N'introduit aucun nouveau moteur de document :
    délègue à reports.generer_pdf_liste_table, DÉJÀ utilisé par l'export PDF
    manuel d'une table (views.api_exporter_table_pdf) — seules les lignes en
    entrée changent (celles du filtre composé, pas la table entière).

    Le PDF est enregistré comme un DocumentGenere ordinaire (type "rapport",
    table_cible=table) : il apparaît donc dans la fiche Ontology de la
    table (ontologie_fiche.assembler_fiche_objet, section "documents"),
    exactement comme tout autre document généré. Retourne l'URL du fichier,
    ou None si la génération échoue (le filtre lui-même reste valide et
    résumé normalement — un échec de rapport ne doit pas cacher le
    dénombrement déjà obtenu)."""
    try:
        from django.core.files.base import ContentFile
        from django.utils import timezone
        from .reports import generer_pdf_liste_table
        from .models import DocumentGenere
        from . import grille
        from .views import _champs_masques_pour

        champs = list(table.champs.order_by("ordre"))
        enregistrements = list(qs)
        from . import parquet_store
        lignes, mises_en_forme = parquet_store.lignes_affichage_pdf(table, enregistrements)   # valeurs : Parquet → texte CSV (pandas) → table par regex (pdf_texte.py) ; même coloration que l'export PDF manuel
        # Même masquage que l'export PDF manuel (views.api_exporter_table_pdf) :
        # un champ masqué pour ce membre ne doit pas fuiter dans le rapport
        # chaîné, même s'il n'a jamais été exposé au LLM pour composer le filtre.
        grille.masquer_champs(lignes, _champs_masques_pour(membre, table))
        tables_calcul = list(
            table.tables_calcul.filter(inclure_dans_pdf=True)
            .prefetch_related("lignes__formule", "lignes__champ_cible", "lignes__champs_cibles")
        )
        pdf_bytes = generer_pdf_liste_table(table, champs, lignes, tables_calcul=tables_calcul, mises_en_forme=mises_en_forme)

        doc = DocumentGenere.objects.create(
            entreprise=demande.entreprise, utilisateur=getattr(membre, "utilisateur", None),
            table_cible=table, type_document="rapport",
            titre=tf("engine/agent_ontology.py:g:699:19", v1=table.nom),
            instructions=demande.texte,
            parametres={"filtres": resume_filtres, "origine": "composer_requete_libre"},
            statut="execute", execute_le=timezone.now(),
        )
        doc.fichier_pdf.save(f"rapport_filtre_{table.slug}_{doc.id}.pdf", ContentFile(pdf_bytes), save=True)
        return doc.fichier_pdf.url
    except Exception:
        logger.warning(_i18n_t("engine/agent_ontology.py:g:702:24"), exc_info=True)
        return None


def composer_requete_libre(demande, membre) -> dict | None:
    """Phase 5 suite — composition dynamique Objet(table) → Filtre(champ,
    opérateur, valeur) pour une phrase libre qui ne correspond à AUCUNE
    Action nommée (appelée seulement après l'échec de classifier_intention,
    voir tenter_action) : c'est le critère de réussite explicite du cahier
    des charges ("clients avec CA > 1M"), qui exigeait de composer
    Object→Filter au lieu de se limiter à un catalogue fixe d'opérations.

    N'introduit aucun nouveau moteur de filtre : délègue entièrement à
    engine.grille.appliquer (déjà utilisé par la grille de données —
    mêmes opérateurs, mêmes règles de cast numérique par type de colonne),
    seule la CLASSIFICATION (quelle table, quelle colonne, quel opérateur)
    est nouvelle, comme classifier_intention pour les Actions nommées.

    Lot 2 : le filtre peut désormais CHAÎNER vers une génération de rapport
    PDF des lignes filtrées, en une seule demande, dans la même réponse
    JSON du moteur de classification — voir _generer_rapport_filtre.

    Périmètre volontairement restreint (comme classifier_intention) : UNE
    seule table, un ou plusieurs filtres combinés en ET, résultat = un
    dénombrement des lignes correspondantes (+ rapport PDF optionnel) —
    pas encore de jointure entre tables via une colonne de relation ni
    d'agrégat (somme, moyenne) sur le résultat filtré ; ces compositions
    restent au pipeline d'analyse habituel (agent.py, script Python généré).

    Retourne None si la phrase ne peut pas être résolue avec certitude (la
    demande retombe alors sur le pipeline d'analyse habituel, exactement
    comme classifier_intention), sinon {"statut": "execute", "nom", ...}
    au même format que tenter_action pour un chemin _CIBLE_AUTO."""
    from .inference import MoteurInference

    catalogue = _catalogue_filtre_pour(membre, demande.entreprise)
    if not catalogue:
        return None

    try:
        moteur = MoteurInference(entreprise=demande.entreprise)
        bloc, erreur = moteur.generer_json(_prompt_filtre(catalogue, demande.texte), timeout=30)
        if erreur or not bloc:
            return None
        resultat = _extraire_json(bloc)
        if not resultat or not resultat.get("table"):
            return None

        table_info = next((t for t in catalogue if t["slug"] == resultat["table"]), None)
        if table_info is None:
            logger.warning(_i18n_t("engine/agent_ontology.py:g:752:28"), resultat.get("table"))
            return None

        slugs_valides = {c["slug"] for c in table_info["champs"]}
        params, resume_filtres = {}, []
        for f in (resultat.get("filtres") or [])[:5]:
            champ_slug, op, val = f.get("champ"), f.get("operateur"), f.get("valeur")
            if champ_slug not in slugs_valides or op not in _OPERATEURS_FILTRE or val in (None, ""):
                continue
            params[f"f_{champ_slug}"] = f"{op}{val}"
            resume_filtres.append(f"{champ_slug} {op} {val}")
        if not params:
            return None

        from . import grille
        table = table_info["table"]
        from .vue_groupe import enregistrements_vue   # un groupe de tables = UNE table pour l'agent aussi
        qs = enregistrements_vue(table)
        if membre is not None and membre.role == "agent":
            qs = qs.filter(cree_par=membre.utilisateur)
        qs, _criteres = grille.appliquer(qs, table, params)
        total = qs.count()

        url_rapport = None
        if resultat.get("rapport") is True:
            url_rapport = _generer_rapport_filtre(demande, membre, table, qs, resume_filtres)
    except Exception:
        # Comme le chemin _CIBLE_AUTO de tenter_action : toute composition
        # incertaine ou en échec abandonne silencieusement plutôt que de
        # risquer un filtre erroné — la demande retombe sur l'analyse
        # habituelle.
        logger.warning(_i18n_t("engine/agent_ontology.py:g:783:24"), exc_info=True)
        return None

    if demande.entreprise_id is not None:
        from .models import JournalAudit
        JournalAudit.enregistrer(
            entreprise=demande.entreprise, utilisateur=getattr(membre, "utilisateur", None),
            action="requete_filtre", details={"table": table.slug, "filtres": resume_filtres, "rapport": bool(url_rapport)},
            type_acteur="agent_ia", source="agent_ia", cible_type="table", cible_id=table.id,
            fonction_utilisee="composer_requete_libre", resultat="succes",
        )

    resume = tf("engine/agent_ontology.py:g:800:14", v1=total, v2=table_info['nom']) + " et ".join(resume_filtres) + "."
    if url_rapport:
        resume += tf("engine/agent_ontology.py:g:802:19", v1=url_rapport)
    elif resultat.get("rapport") is True:
        resume += _i18n_t("engine/agent_ontology.py:g:804:19")

    return {
        "statut": "execute", "nom": "requete_filtre",
        "description": tf("engine/agent_ontology.py:g:808:24", v1=table_info['nom']),
        "resume": resume,
    }


def tenter_action(demande, membre) -> dict | None:
    """Point d'entrée appelé par agent.py AVANT toute génération de script.
    Retourne None si la demande ne correspond à aucune Action connue NI à
    un filtre composable dynamiquement (composer_requete_libre) — le
    pipeline d'analyse habituel s'applique alors, sans aucun changement ;
    sinon un dict décrivant l'issue :
      {"statut": "execute",  "nom", "description", "resume"}
      {"statut": "proposee", "nom", "description", "parametres",
       "cible_type", "cible_id"}
    Ne touche jamais demande.statut/resultat_json elle-même — c'est à
    l'appelant de décider comment les stocker, exactement comme pour le
    pipeline de script existant."""
    match = classifier_intention(demande, membre)
    if match is None:
        return composer_requete_libre(demande, membre)

    nom = match["nom"]
    kind = _CLASSIFICATION_ACTIONS.get(nom)
    descripteur = match["descripteur"]
    cible = match["cible"]

    if kind == _CIBLE_AUTO:
        parametres = _finaliser_parametres(nom, demande.entreprise, match["parametres"], membre)
        if parametres is None:
            return None
        try:
            resultat = actions.executer_action(
                nom, membre, cible, origine="agent_ia", **parametres
            )
        except actions.ActionRefusee:
            return None
        except Exception:
            # Paramètres extraits incompatibles ou validation métier échouée :
            # on abandonne silencieusement plutôt que de risquer un appel
            # erroné — la demande retombe sur le pipeline d'analyse habituel.
            logger.warning(_i18n_t("engine/agent_ontology.py:g:843:28"), nom, exc_info=True)
            return None
        return {
            "statut": "execute", "nom": nom, "description": descripteur.description,
            "resume": _resume_resultat(nom, resultat, parametres),
        }

    if kind == _CIBLE_CONFIRMATION:
        cible_type, cible_id, _ = actions._reperer_cible(cible)
        return {
            "statut": "proposee", "nom": nom, "description": descripteur.description,
            "parametres": match["parametres"],
            "cible_type": cible_type, "cible_id": cible_id,
        }

    return None


def confirmer_et_executer(demande, membre) -> dict:
    """Exécute réellement une Action précédemment PROPOSÉE (voir
    tenter_action), sur confirmation explicite de l'utilisateur — relit
    UNIQUEMENT la proposition déjà stockée dans demande.resultat_json (jamais
    un nom/paramètre fourni librement par la requête de confirmation
    elle-même), pour qu'un appel de confirmation détourné ne puisse pas
    exécuter une Action arbitraire.

    La cible n'est JAMAIS reprise telle quelle d'une exécution précédente :
    seuls cible_type/cible_id (stockés par tenter_action) sont relus, puis
    RE-résolus par id dans l'entreprise courante — au cas où l'objet aurait
    été supprimé/modifié entre la proposition et la confirmation, et pour
    que la vérification de permission granulaire (Phase 9) s'applique sur
    l'état réel, pas sur un souvenir. Lève actions.ActionRefusee si aucune
    proposition valide n'est en attente, ou si la cible ne peut plus être
    retrouvée."""
    proposition = demande.resultat_json or {}
    if proposition.get("type") != "proposition_action":
        raise actions.ActionRefusee(_i18n_t("engine/agent_ontology.py:g:884:37"))

    nom = proposition.get("nom")
    if _CLASSIFICATION_ACTIONS.get(nom) != _CIBLE_CONFIRMATION:
        raise actions.ActionRefusee(_i18n_t("engine/agent_ontology.py:g:888:37"))

    cible = _reresoudre_cible_confirmation(nom, demande.entreprise, proposition)
    if cible is None:
        raise actions.ActionRefusee(_i18n_t("engine/agent_ontology.py:g:892:37"))

    parametres = dict(proposition.get("parametres") or {})
    for cle in _PARAMS_SERVEUR.get(nom, set()):
        if cle == "utilisateur":
            parametres["utilisateur"] = membre.utilisateur

    parametres = _finaliser_parametres(nom, demande.entreprise, parametres, membre)
    if parametres is None:
        raise actions.ActionRefusee(_i18n_t("engine/agent_ontology.py:g:901:37"))

    resultat = actions.executer_action(nom, membre, cible, **parametres)
    return {"nom": nom, "resume": _resume_resultat(nom, resultat, parametres)}


def _reresoudre_cible_confirmation(nom: str, entreprise, proposition: dict):
    """Relit cible_type/cible_id stockés par tenter_action et re-résout
    l'objet réel dans l'entreprise courante. Pour une Action sans type de
    cible déclaré (_CIBLE_TYPES), la cible est toujours l'entreprise
    elle-même (aucune résolution par id nécessaire)."""
    if _CIBLE_TYPES.get(nom) is None:
        return entreprise

    cible_type, cible_id = proposition.get("cible_type"), proposition.get("cible_id")
    if not cible_type or cible_id is None:
        return None

    if cible_type == "MembreEntreprise":
        from .models import MembreEntreprise
        return MembreEntreprise.objects.filter(id=cible_id, entreprise=entreprise).first()
    if cible_type == "IndicateurKPI":
        from .models import IndicateurKPI
        return IndicateurKPI.objects.filter(id=cible_id, entreprise=entreprise).first()
    if cible_type == "TableCalcul":
        from .models import TableCalcul
        return TableCalcul.objects.filter(id=cible_id, entreprise=entreprise).first()
    if cible_type == "TableDynamique":
        from .models import TableDynamique
        return TableDynamique.objects.filter(id=cible_id, entreprise=entreprise).first()
    if cible_type == "DemandeAgent":
        from .models import DemandeAgent
        return DemandeAgent.objects.filter(id=cible_id, entreprise=entreprise).first()
    return None
