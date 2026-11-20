"""CONNAISSANCE COMPLÈTE DE LA STRUCTURE par le modèle fantôme (le « stagiaire data scientist » qui finit par tout maîtriser).

La structure des tables ne bouge plus : le fantôme doit donc connaître CHAQUE table (modèle) de la plateforme, pas seulement
celles que l'état fusionné détaille. Ce module apporte deux choses, sans aucune règle métier :

1. CATALOGUE : chaque modèle de engine/models est rangé dans une FAMILLE (données, calcul, BI, ontologie, IA, argent, équipe,
   communication, automatisation, sécurité, système). Un test lit les fichiers de modèles et échoue si un modèle manque au
   catalogue : on ne peut plus ajouter une table que le fantôme ignore.
2. SIGNAUX : pour chaque famille, volume et activité récente de l'entreprise (échelle logarithmique), plus 8 signaux de l'ARGENT (TVA,
   taxes actives, services payants, part des blocs wallet, commissions, dépôts réussis, agents rémunérés). Ce sont de simples ENTRÉES
   du réseau (comme fantome_usage) : on lit des COMPTAGES et des RÉGLAGES, jamais une valeur de cellule, un montant individuel, un
   message ni un secret.

Choix délibéré : la structure de la plateforme n'est PAS injectée dans fantome_metier (les mots « solde », « salaire », « taux » du schéma
sont les mêmes chez tous les clients : ils fausseraient les affinités de métier). Elle sert à savoir quelles parties de la plateforme
l'entreprise utilise vraiment, donc quoi construire pour elle.

Module PUR sauf observer() (Django). Les entrées sont ajoutées EN FIN de vecteur (fantome_analyse) : le réseau déjà entraîné est conservé.
"""
from __future__ import annotations

import math
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7

JOURS = 30
PLAFOND = 100_000   # au-delà, l'échelle (10 000 lignes ≈ 1) est saturée de toute façon

FAMILLES: dict[str, tuple[str, ...]] = {
    "donnees": ("TableDynamique", "ChampDynamique", "Enregistrement", "ValeurChamp", "LienChamp", "GroupeTables", "ConteneurTables",
                "GroupePartage", "GroupePartageLigne", "LignePartagee", "GroupePartageCellule", "CellulePartagee",
                "FusionCelluleTable", "PartageInterAgent", "ConnexionExterne", "TacheImport", "JournalTransformationColonne"),
    "calcul": ("FormuleCalcul", "FormuleChamp", "FormuleLigne", "FormuleLigneCible", "GenerationFormule", "TableCalcul", "LigneCalcul"),
    "bi": ("GraphiqueBI", "TableauBordBI", "WidgetGraphiqueBI", "MesureWidget", "IndicateurKPI", "ValeurKPIHistorique",
           "GenerationGraphiqueBI", "DemandeAgent", "GraphiqueDemande", "RapportGenere", "DocumentGenere", "SignatureDocument",
           "ConfigurationDocumentContexte", "PersonnalisationDocumentMembre"),
    "ontologie": ("CoucheOntologie", "PageOntologie", "CouleurOntologie", "CouleurNoeudOntologie", "GenerationCoucheOntologie"),
    "ia": ("ModeleIA", "InferenceIA", "ConfigurationIA", "Skill", "CompteurTokens", "ChatbotAgent"),
    "argent": ("Wallet", "TauxChange", "FraisWallet", "RemunerationAgent", "FournisseurPaiement", "DepotPaiement", "AbonnementMensuel",
               "ModemSms", "PrefixeOperateur"),
    "equipe": ("Entreprise", "MembreEntreprise", "ReprisePoste"),
    "communication": ("Conversation", "Participant", _gtfr("engine/fantome_schema.py:40:54"), "ReactionMessage", "AvisMessage", "FavoriMessage", "Sourdine", "Publicite",
                      "ReactionPublicite", "Notification", "RecoNotifiee"),
    "automatisation": ("TachePlanifiee",),
    "securite": ("CleAPI", "JournalAudit", "Bloc", "ArchiveCycle"),
    "systeme": ("BlocSysteme", "MesureUsage", "EvaluationSUS"),
}
CODES = tuple(FAMILLES)
NB_FAMILLES = len(CODES)
NB_ARGENT = 8
NB_SCHEMA = 2 * NB_FAMILLES + NB_ARGENT   # (volume, activité récente) par famille, puis les signaux de l'argent
OPERATIONS = ("depot", "retrait", "transfert")


def modeles() -> list[str]:
    return [m for ms in FAMILLES.values() for m in ms]


def famille_de(modele: str) -> str | None:
    for f, ms in FAMILLES.items():
        if modele in ms:
            return f
    return None


def _borne(v: float) -> float:
    return round(min(1.0, max(0.0, float(v))), 4) if math.isfinite(float(v)) else 0.0


def _echelle(n: float) -> float:
    """Nombre de lignes -> [0, 1] (10 000 lignes ≈ 1) : la plateforme n'a pas la même taille chez tous les clients."""
    return _borne(math.log10(1 + max(0.0, float(n))) / 4)


def activite(lignes: dict[str, int], recents: dict[str, int]) -> list[float]:
    """{modèle: nombre de lignes} et {modèle: lignes des JOURS derniers jours} -> (volume, part récente) pour chaque famille. PUR.
    La part récente = lignes récentes / lignes de la famille : « cette partie de la plateforme est vivante », pas « grosse »."""
    sortie = []
    for f in CODES:
        n = sum(max(0, lignes.get(m, 0)) for m in FAMILLES[f])
        r = sum(max(0, recents.get(m, 0)) for m in FAMILLES[f])
        sortie += [_echelle(n), _borne(r / n) if n else 0.0]
    return sortie


def argent(frais: dict | None, types_blocs: dict | None, depots: dict | None, nb_membres: int = 0, nb_remuneres: int = 0) -> list[float]:
    """NB_ARGENT signaux comptables de l'entreprise (PUR) :
    0 TVA activée · 1 taux de TVA (0..100 % -> 0..1) · 2 part des opérations (dépôt, retrait, transfert) réellement taxées ·
    3 services payants configurés (tanh(n/5)) · 4 part des blocs du registre qui sont des mouvements de wallet ·
    5 part des commissions d'agent parmi ces mouvements · 6 part des dépôts crédités · 7 part des membres rémunérés."""
    f = frais or {}
    taxees = sum(1 for op in OPERATIONS
                 if (float(f.get(f"taux_{op}") or 0) > 0 and f.get(f"taux_{op}_actif", True))
                 or (float(f.get(f"fixe_{op}") or 0) > 0 and f.get(f"fixe_{op}_actif", False)))
    types = {str(k): max(0, int(v)) for k, v in (types_blocs or {}).items()}
    total = sum(types.values())
    wallet = sum(v for k, v in types.items() if k.startswith("wallet_"))
    d = depots or {}
    nb_depots = sum(max(0, int(v)) for v in d.values())
    return [
        1.0 if f.get("taux_tva_actif") else 0.0,
        _borne(float(f.get("taux_tva") or 0) / 100) if f.get("taux_tva_actif") else 0.0,
        _borne(taxees / len(OPERATIONS)),
        _borne(math.tanh(len(f.get("services") or {}) / 5)),
        _borne(wallet / total) if total else 0.0,
        _borne(types.get("wallet_commission", 0) / wallet) if wallet else 0.0,
        _borne(max(0, int(d.get("credite", 0))) / nb_depots) if nb_depots else 0.0,
        _borne(nb_remuneres / nb_membres) if nb_membres else 0.0,
    ]


def vecteur(lignes: dict[str, int], recents: dict[str, int], frais=None, types_blocs=None, depots=None, nb_membres=0, nb_remuneres=0) -> list[float]:
    return activite(lignes, recents) + argent(frais, types_blocs, depots, nb_membres, nb_remuneres)


def completer(v: list[float] | None) -> list[float]:
    """Vecteur de longueur exacte NB_SCHEMA (zéros si absent ou trop court)."""
    return (list(v or [])[:NB_SCHEMA] + [0.0] * NB_SCHEMA)[:NB_SCHEMA]


# ── Django (paresseux) ────────────────────────────────────────────────────────────────────────────────────────────────────
def chemin_entreprise(modele, profondeur: int = 3) -> str | None:
    """Chemin de relations (« membre__entreprise ») qui mène d'un modèle à l'Entreprise, trouvé par parcours en largeur ; None si le
    modèle est global (fournisseurs, préfixes d'opérateur…). Pas de table de correspondance à tenir à jour."""
    from .models import Entreprise
    file, vus = [((), modele)], {modele}
    while file:
        chemin, m = file.pop(0)
        for f in m._meta.get_fields():
            if not (getattr(f, "concrete", False) and (f.many_to_one or f.one_to_one) and f.related_model is not None):
                continue
            c = chemin + (f.name,)
            if f.related_model is Entreprise:
                return "__".join(c)
            if len(c) < profondeur and f.related_model not in vus:
                vus.add(f.related_model)
                file.append((c, f.related_model))
    return None


def _champ_date(modele) -> str | None:
    for nom in ("cree_le", "maj_le", "calcule_le"):
        try:
            modele._meta.get_field(nom)
            return nom
        except Exception:
            continue
    return None


def observer(entreprise) -> tuple[list[float], dict]:
    """(vecteur NB_SCHEMA, rapport {famille: {modèle: [lignes, récentes]}}). Comptages seulement ; un modèle en échec est ignoré
    (le fantôme ne bloque jamais). Le rapport sert à la commande modele_fantome --structure."""
    from datetime import timedelta

    from django.apps import apps
    from django.utils import timezone

    from .models import Bloc, DepotPaiement, FraisWallet, MembreEntreprise, RemunerationAgent
    depuis = timezone.now() - timedelta(days=JOURS)
    lignes, recents, rapport = {}, {}, {f: {} for f in CODES}
    for nom in modeles():
        try:
            m = apps.get_model("engine", nom)
            chemin = chemin_entreprise(m) if nom != "Entreprise" else None
            qs = m.objects.all() if nom == "Entreprise" else (m.objects.filter(**{chemin: entreprise}) if chemin else m.objects.all())
            if nom == "Entreprise":
                qs = qs.filter(pk=entreprise.pk)
            lignes[nom] = qs.values("pk")[:PLAFOND].count()   # comptage plafonné : jamais un COUNT sur des millions de cellules
            champ = _champ_date(m)
            recents[nom] = qs.filter(**{f"{champ}__gte": depuis}).values("pk")[:PLAFOND].count() if champ else 0
            rapport[famille_de(nom)][nom] = [lignes[nom], recents[nom]]
        except Exception:
            continue
    frais = FraisWallet.objects.filter(entreprise=entreprise).values().first()
    types: dict = {}
    for transactions in Bloc.objects.filter(entreprise=entreprise).order_by("-index").values_list("transactions", flat=True)[:200]:
        for tx in transactions or []:
            if isinstance(tx, dict):
                types[str(tx.get("type", "?"))] = types.get(str(tx.get("type", "?")), 0) + 1
    depots = {}
    for statut in DepotPaiement.objects.filter(membre__entreprise=entreprise).values_list("statut", flat=True)[:5000]:
        depots[statut] = depots.get(statut, 0) + 1
    nb_membres = MembreEntreprise.objects.filter(entreprise=entreprise).count()
    nb_remuneres = RemunerationAgent.objects.filter(membre__entreprise=entreprise).exclude(mode="aucun").count()
    return vecteur(lignes, recents, frais, types, depots, nb_membres, nb_remuneres), rapport
