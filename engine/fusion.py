"""FUSION de l'état de l'entreprise (moteur de recommandation, volet 1/2).

Réunit en UN SEUL dictionnaire JSON-sérialisable tout ce que le moteur sait de
l'entreprise à l'instant T : tables et volumes, activité jour par jour,
colonnes (remplissage, valeurs dominantes, statistiques numériques), graphe de
relations, KPI et leur historique, blockchain, tâches, modèles IA,
notifications, membres, tableaux de bord.

Rien n'est cherché en dehors du moteur : uniquement les modèles de engine.
Le résultat est lu par engine/recommandations.py (règles pures, sans accès à
la base) — voir ce module pour la suite. Aucun modèle ni migration ajoutés :
l'état est calculé à la demande et gardé 60 s en cache.

Chaque section est isolée (try/except) : une section en échec est signalée
dans `avertissements` et n'empêche jamais les autres d'être fusionnées.
"""
from engine.i18n import t as _i18n_t
from engine.i18n import tf
import logging
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from django.core.cache import cache
from django.db.models import Count, Max, Min
from django.db.models.functions import TruncDate
from django.utils import timezone

logger = logging.getLogger(__name__)

FENETRE_JOURS = 60            # profondeur de l'historique d'activité fusionné
CACHE_SECONDES = 60
LIMITE_VALEURS = None         # None = toutes les valeurs de la colonne (plus d'échantillon de 5 000)
DISTINCTS_MAX_TOP = 50        # au-delà, la colonne n'est pas « catégorielle » : pas de valeur dominante
BLOCS_ANALYSES = 100          # derniers blocs lus pour la répartition par type d'événement
BLOCS_MAX_VERIFICATION = 2000  # au-delà, la chaîne n'est pas re-vérifiée à chaque fusion
TYPES_NUM = {"nombre", "decimal", "monnaie", "pourcentage", "note", "barre_progression"}
TYPES_NON_CATEGORIELS = TYPES_NUM | {
    "texte_long", "fichier", "image", "son", "video", "vecteur", "matrice", "json", "geolocalisation",
    "datetime", "horodatage", "date", "heure", "duree", "relation", "booleen", "switch",
}


def _ts(dt):
    return dt.timestamp() if dt else None


def _somme_jours(jours, debut, fin, maintenant):
    """Total des lignes créées entre `maintenant - debut` jours et `maintenant - fin` jours."""
    somme = 0
    for cle, n in jours.items():
        age = (maintenant.date() - date.fromisoformat(cle)).days
        if fin <= age < debut:
            somme += n
    return somme


def _stats_numeriques(valeurs):
    nums = []
    for v in valeurs:
        try:
            nums.append(float(str(v).replace(",", ".")))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None
    moy = statistics.fmean(nums)
    ecart = statistics.pstdev(nums) if len(nums) > 1 else 0.0
    aberrants = sum(1 for x in nums if ecart > 0 and abs(x - moy) > 3 * ecart) if len(nums) >= 8 else 0
    return {"n": len(nums), "min": min(nums), "max": max(nums), "moy": moy, "ecart": ecart,
            "mediane": statistics.median(nums), "aberrants": aberrants}


def _stats_colonne(champ, valeurs, nb_lignes, rempli=None, distincts=None):
    """Fiche d'une colonne. `rempli`/`distincts` viennent de l'agrégat SQL quand il existe
    (exacts) ; sinon calculés sur l'échantillon `valeurs`."""
    vides = ("", None)
    non_vides = [v for v in valeurs if v not in vides]
    if rempli is None:
        rempli = len(non_vides)
    if distincts is None:
        distincts = len({str(v) for v in non_vides})
    fiche = {"id": champ.id, "nom": champ.nom, "slug": champ.slug, "type": champ.type_champ,
             "obligatoire": bool(champ.obligatoire), "rempli": rempli,
             "remplissage": (rempli / nb_lignes) if nb_lignes else 0.0, "distincts": distincts,
             "top_valeur": None, "top_part": 0.0, "num": None}
    if champ.type_champ in TYPES_NUM:
        fiche["num"] = _stats_numeriques(non_vides)
    elif non_vides and champ.type_champ not in TYPES_NON_CATEGORIELS and distincts <= DISTINCTS_MAX_TOP:
        valeur, n = Counter(str(v) for v in non_vides).most_common(1)[0]
        fiche.update(top_valeur=valeur[:80], top_part=n / len(non_vides))
    return fiche


def _profil_colonne(champ, cadre):
    """(lignes renseignées, valeurs distinctes, échantillon de valeurs) de la colonne `champ`, lus dans `cadre` (DataFrame Parquet de
    sa table). L'échantillon n'est extrait que si les statistiques en ont besoin (colonne numérique ou catégorielle)."""
    from . import parquet_store
    if champ.slug not in cadre.columns:
        return 0, 0, []
    rempli, distincts = parquet_store.comptes_colonne(cadre[champ.slug])
    besoin = champ.type_champ in TYPES_NUM or (
        champ.type_champ not in TYPES_NON_CATEGORIELS and 0 < distincts <= DISTINCTS_MAX_TOP)
    valeurs = parquet_store.textes_colonne(cadre[champ.slug], LIMITE_VALEURS) if (besoin and rempli) else []
    return rempli, distincts, valeurs


def _fusion_tables(entreprise, maintenant, etat):
    from . import parquet_store
    from .models import ChampDynamique, Enregistrement, IndicateurKPI, TableDynamique, TableauBordBI

    toutes = list(TableDynamique.objects.filter(entreprise=entreprise))
    # Groupe fusionné en une table (groupe_fusion.py) : la table fusionnée porte déjà les lignes de toutes les parties. Les parties restent en base
    # (pour défusionner) mais n'ont pas de fiche, sinon leurs lignes seraient comptées deux fois (volumes, comparaisons, totaux des recommandations).
    groupes_fusionnes = {t.groupe_fusionne_id for t in toutes if t.groupe_fusionne_id}
    tables = [t for t in toutes if t.groupe_id not in groupes_fusionnes]
    parties_cachees = [t for t in toutes if t.groupe_id in groupes_fusionnes]
    champs_par_table = defaultdict(list)
    for c in ChampDynamique.objects.filter(table__entreprise=entreprise).order_by("ordre", "id"):
        champs_par_table[c.table_id].append(c)

    base = Enregistrement.objects.filter(table__entreprise=entreprise)
    volumes = {r["table_id"]: r for r in base.values("table_id").annotate(
        n=Count("id"), premiere=Min("cree_le"), derniere=Max("maj_le"))}
    debut = maintenant - timedelta(days=FENETRE_JOURS)
    jours = defaultdict(dict)
    for r in base.filter(cree_le__gte=debut).annotate(j=TruncDate("cree_le")).values("table_id", "j").annotate(n=Count("id")):
        jours[r["table_id"]][r["j"].isoformat()] = r["n"]
    createurs = defaultdict(dict)
    for r in base.exclude(cree_par=None).values("table_id", "cree_par_id").annotate(n=Count("id")):
        createurs[r["table_id"]][str(r["cree_par_id"])] = r["n"]
    kpis = Counter(IndicateurKPI.objects.filter(entreprise=entreprise).values_list("table_source_id", flat=True))
    tableaux = Counter(TableauBordBI.objects.filter(entreprise=entreprise).values_list("table_source_id", flat=True))
    # KPI et tableaux de bord posés sur une partie (souvent la première, l'ancre du groupe) comptent pour la table fusionnée qui la remplace.
    for t in tables:
        if t.groupe_fusionne_id:
            for p in parties_cachees:
                if p.groupe_id == t.groupe_fusionne_id:
                    kpis[t.id] += kpis.get(p.id, 0)
                    tableaux[t.id] += tableaux.get(p.id, 0)

    # Colonnes des tables SQL (lignes renseignées, valeurs distinctes, échantillon) : lues dans le fichier Parquet de la table (voir
    # _profil_colonne), jamais cellule par cellule dans l'EAV.
    for t in tables:
        nb = (volumes.get(t.id) or {}).get("n", 0)
        v = volumes.get(t.id) or {}
        j = jours.get(t.id, {})
        fiche = {"id": t.id, "nom": t.nom, "groupe_id": t.groupe_id, "clients": bool(t.est_table_membres),
                 "nosql": bool(t.est_nosql), "nb_lignes": nb, "premiere_ts": _ts(v.get("premiere")),
                 "derniere_ts": _ts(v.get("derniere")), "jours": j,
                 "n7": _somme_jours(j, 7, 0, maintenant), "n7_prec": _somme_jours(j, 14, 7, maintenant),
                 "n30": _somme_jours(j, 30, 0, maintenant), "n30_prec": _somme_jours(j, 60, 30, maintenant),
                 "createurs": createurs.get(t.id, {}), "nb_kpis": kpis.get(t.id, 0),
                 "nb_tableaux_bord": tableaux.get(t.id, 0), "champs": []}
        lignes_nosql = None
        cadre = None
        if t.est_nosql and nb:
            lignes_nosql = [d or {} for d in Enregistrement.objects.filter(table=t).values_list("valeurs_json", flat=True)[:LIMITE_VALEURS]]
        for c in champs_par_table.get(t.id, []):
            if not nb:
                valeurs, rempli, distincts = [], 0, 0
            elif t.est_nosql:
                valeurs, rempli, distincts = [d.get(c.slug) for d in lignes_nosql], None, None
            else:
                if cadre is None:
                    cadre = parquet_store.donnees_analytiques(t, groupe=False)   # fichier Parquet de CETTE table (une fiche = une table physique)
                rempli, distincts, valeurs = _profil_colonne(c, cadre)
            fiche["champs"].append(_stats_colonne(c, valeurs, nb, rempli, distincts))
        etat["tables"].append(fiche)


def _fusion_relations(entreprise, etat):
    from .models import ChampDynamique, LienChamp

    tables = {t["id"]: t["nom"] for t in etat["tables"]}
    aretes = defaultdict(lambda: {"champs": 0, "liens": 0})
    for c in ChampDynamique.objects.filter(table__entreprise=entreprise, type_champ="relation", relation_vers__isnull=False):
        aretes[(c.table_id, c.relation_vers_id)]["champs"] += 1
    liens = LienChamp.objects.filter(valeur_enfant__champ__table__entreprise=entreprise).exclude(enregistrement_source=None)
    for r in liens.values("valeur_enfant__champ__table_id", "enregistrement_source__table_id").annotate(n=Count("id")):
        aretes[(r["valeur_enfant__champ__table_id"], r["enregistrement_source__table_id"])]["liens"] += r["n"]
    degres = Counter()
    for (src, dst), a in aretes.items():
        degres[src] += 1
        degres[dst] += 1
    hubs = [{"enregistrement_id": r["enregistrement_source_id"], "table_id": r["enregistrement_source__table_id"],
             "table": tables.get(r["enregistrement_source__table_id"], ""), "degre": r["n"]}
            for r in liens.values("enregistrement_source_id", "enregistrement_source__table_id")
            .annotate(n=Count("id")).order_by("-n")[:20]]
    etat["relations"] = {
        "aretes": [{"source": s, "cible": d, "champs": a["champs"], "liens": a["liens"]} for (s, d), a in aretes.items()],
        "total_liens": sum(a["liens"] for a in aretes.values()),
        "degres_tables": dict(degres), "hubs": hubs,
    }


def _fusion_kpis(entreprise, etat):
    from .models import IndicateurKPI, ValeurKPIHistorique

    kpis = list(IndicateurKPI.objects.filter(entreprise=entreprise).select_related("table_source"))
    historique = defaultdict(list)
    for kpi_id, valeur in ValeurKPIHistorique.objects.filter(kpi__entreprise=entreprise).order_by("kpi_id", "calcule_le").values_list("kpi_id", "valeur"):
        historique[kpi_id].append(valeur)
    etat["kpis"] = [{
        "id": k.id, "nom": k.nom, "table_id": k.table_source_id, "table": k.table_source.nom,
        "champ_cible_id": k.champ_cible_id, "formule_id": k.formule_id, "statut": k.statut,
        "valeur": k.valeur_actuelle, "cible": k.valeur_cible, "seuil_alerte": k.seuil_alerte,
        "sens": k.sens_objectif, "etat": k.etat, "etat_alerte": k.dernier_etat_alerte, "erreur": k.erreur,
        "calcule_ts": _ts(k.calcule_le), "historique": historique.get(k.id, [])[-30:],
    } for k in kpis]


def _fusion_blockchain(entreprise, maintenant, etat):
    from .models import Bloc

    blocs = Bloc.objects.filter(entreprise=entreprise)
    total = blocs.count()
    types = Counter()
    for transactions in blocs.order_by("-index").values_list("transactions", flat=True)[:BLOCS_ANALYSES]:
        types.update(str(tx.get("type", "?")) for tx in (transactions or []) if isinstance(tx, dict))
    d7, d14 = maintenant - timedelta(days=7), maintenant - timedelta(days=14)
    etat["blockchain"] = {
        "total": total, "dernier_ts": blocs.aggregate(m=Max("timestamp"))["m"],
        "blocs_7j": blocs.filter(cree_le__gte=d7).count(),
        "blocs_7j_prec": blocs.filter(cree_le__gte=d14, cree_le__lt=d7).count(),
        "types": dict(types),
        "chaine_valide": Bloc.chaine_valide(entreprise.id) if total <= BLOCS_MAX_VERIFICATION else None,
    }


def _fusion_activite(entreprise, maintenant, etat):
    from .models import InferenceIA, MembreEntreprise, ModeleIA, Notification, TachePlanifiee

    etat["taches"] = [{
        "id": t.id, "nom": t.nom or tf("engine/fusion.py:g:213:37", v1=t.id), "mode": t.mode_declenchement, "actif": t.actif,
        "statut": t.statut, "erreur": (t.erreur or "")[:200], "derniere_ts": _ts(t.derniere_execution),
        "cree_ts": _ts(t.cree_le), "a_script": bool(t.script_genere),
    } for t in TachePlanifiee.objects.filter(entreprise=entreprise)]
    usage = {r["modele_id"]: r for r in InferenceIA.objects.filter(modele__entreprise=entreprise)
             .values("modele_id").annotate(n=Count("id"), dernier=Max("cree_le"))}
    etat["modeles_ia"] = [{
        "id": m.id, "nom": m.nom, "statut": m.statut, "inferences": (usage.get(m.id) or {}).get("n", 0),
        "derniere_inference_ts": _ts((usage.get(m.id) or {}).get("dernier")),
    } for m in ModeleIA.objects.filter(entreprise=entreprise)]
    notifs = Notification.objects.filter(entreprise=entreprise, lue=False)
    etat["notifications"] = {"non_lues": notifs.count(),
                             "non_lues_anciennes": notifs.filter(cree_le__lt=maintenant - timedelta(days=7)).count()}
    etat["membres"] = dict(Counter(MembreEntreprise.objects.filter(entreprise=entreprise).values_list("role", flat=True)))


SEMAINES_COMMUNICATION = 8


def _fusion_communication(entreprise, maintenant, etat):
    """STATISTIQUES de communication (jamais le contenu des messages) : volume et rythme de la messagerie, par membre de l'équipe
    (messages écrits chaque semaine, 8 semaines) et au total. Le modèle fantôme s'en nourrit comme d'indicateurs :
    voir donnees_entrainement.echantillons_communication. Les réponses du chatbot sont comptées à part (`auto`)."""
    from .models import Message, Notification

    debut = maintenant - timedelta(days=7 * SEMAINES_COMMUNICATION)
    lignes = list(Message.objects.filter(conversation__entreprise=entreprise, supprime=False, cree_le__gte=debut)
                  .values_list("expediteur_id", "cree_le", "auto", "conversation_id")[:50000])
    semaines = {}
    total = [0] * SEMAINES_COMMUNICATION
    auto = actives_7j = 0
    convs_7j = set()
    for exp, quand, est_auto, conv in lignes:
        i = SEMAINES_COMMUNICATION - 1 - int((maintenant - quand).total_seconds() // (7 * 86400))
        if not 0 <= i < SEMAINES_COMMUNICATION:
            continue
        total[i] += 1
        if exp is not None:
            semaines.setdefault(exp, [0] * SEMAINES_COMMUNICATION)[i] += 1
        if i == SEMAINES_COMMUNICATION - 1:
            convs_7j.add(conv)
            auto += 1 if est_auto else 0
    non_lus = Notification.objects.filter(entreprise=entreprise, type_notification="message_recu", lue=False).count()   # discussions en attente
    etat["communication"] = {
        "messages_7j": total[-1], "messages_7j_prec": total[-2], "messages_30j": sum(total[-4:]),
        "conversations_actives_7j": len(convs_7j), "reponses_auto_7j": auto, "non_lus_30j": non_lus,
        "series": [{"id": "0", "historique": [float(x) for x in total]}]
                  + [{"id": str(m), "historique": [float(x) for x in h]} for m, h in sorted(semaines.items())],
    }


def _fusion_geo(entreprise, etat):
    """Trajectoires (colonnes « geolocalisation ») : fiche compacte par table — voir engine/geo_donnees.resume_entreprise."""
    from . import geo_donnees
    etat["geo"] = {"tables": geo_donnees.resume_entreprise(entreprise), "logistique": geo_donnees.resume_logistique(entreprise)}


def _fusion_architecture(entreprise, maintenant, etat):
    """Tout le reste de l'architecture que le modèle fantôme (donc la décision et la recommandation) doit voir : documents, skills,
    analyses, imports, connexions externes, clés API, partages, fusions de cellules, ontologie, wallets, cycles. Comptages et états
    seulement (jamais un secret, jamais une valeur de ligne). Chaque bloc est isolé : un bloc en échec est signalé dans `avertissements`
    sans vider les autres. Lu par les règles `regle_archi` de engine/recommandations.py ; le réseau (33 critères) n'en est pas modifié."""
    from .models import (ArchiveCycle, CleAPI, ConnexionExterne, DemandeAgent, DocumentGenere, FusionCelluleTable,
                         GroupePartage, GroupePartageCellule, GroupePartageLigne, GroupeTables, PageOntologie,
                         PartageInterAgent, Skill, TacheImport, TableDynamique, Wallet)

    def statuts(queryset, champ="statut"):
        return dict(Counter(queryset.values_list(champ, flat=True)))

    def par_table(queryset, champ="table_cible_id"):
        return dict(Counter(queryset.values_list(champ, flat=True)))

    archi = {}
    blocs = {
        "documents": lambda: {
            "total": DocumentGenere.objects.filter(entreprise=entreprise).count(),
            "statuts": statuts(DocumentGenere.objects.filter(entreprise=entreprise)),
            "echecs": [{"id": d.id, "titre": d.titre or d.type_document, "table_id": d.table_cible_id, "erreur": (d.erreur or "")[:200]}
                       for d in DocumentGenere.objects.filter(entreprise=entreprise, statut__startswith="echec").order_by("-cree_le")[:20]],
            "par_table": par_table(DocumentGenere.objects.filter(entreprise=entreprise)),
        },
        "skills": lambda: {
            "total": Skill.objects.filter(entreprise=entreprise).count(),
            "statuts": statuts(Skill.objects.filter(entreprise=entreprise)),
            "echecs": [{"id": k.id, "nom": k.nom, "table_id": k.table_id, "erreur": (k.erreur or "")[:200]}
                       for k in Skill.objects.filter(entreprise=entreprise, statut="echec").order_by("-cree_le")[:20]],
        },
        "analyses": lambda: {
            "total": DemandeAgent.objects.filter(entreprise=entreprise).count(),
            "statuts": statuts(DemandeAgent.objects.filter(entreprise=entreprise)),
            "executees_par_table": par_table(DemandeAgent.objects.filter(entreprise=entreprise, statut="execute")),
            "echecs": [{"id": a.id, "texte": (a.texte or "")[:80], "table_id": a.table_cible_id, "erreur": (a.erreur or "")[:200]}
                       for a in DemandeAgent.objects.filter(entreprise=entreprise, statut="echec").order_by("-cree_le")[:20]],
        },
        "imports": lambda: {
            "total": TacheImport.objects.filter(entreprise=entreprise).count(),
            "statuts": statuts(TacheImport.objects.filter(entreprise=entreprise)),
            "echecs": [{"id": i.id, "nom": i.nom_fichier, "source": i.source, "erreur": (i.erreur or "")[:200]}
                       for i in TacheImport.objects.filter(entreprise=entreprise, statut="echec").order_by("-cree_le")[:20]],
        },
        "connexions_externes": lambda: [
            {"id": c.id, "nom": c.nom, "moteur": c.moteur, "actif": c.actif, "synchro_ts": _ts(c.derniere_synchro_le),
             "synchro_statut": c.derniere_synchro_statut, "erreur": (c.derniere_synchro_erreur or "")[:200],
             "tables_importees": TableDynamique.objects.filter(connexion_externe_origine=c).count()}
            for c in ConnexionExterne.objects.filter(entreprise=entreprise)],
        "cles_api": lambda: [
            {"id": c.id, "membre_id": c.membre_id, "profil": c.profil, "cree_ts": _ts(c.cree_le), "utilisee_ts": _ts(c.derniere_utilisation)}
            for c in CleAPI.objects.filter(entreprise=entreprise, actif=True)],
        "partages": lambda: {
            "groupes_colonnes": GroupePartage.objects.filter(entreprise=entreprise).count(),
            "groupes_lignes": GroupePartageLigne.objects.filter(entreprise=entreprise).count(),
            "groupes_cellules": GroupePartageCellule.objects.filter(entreprise=entreprise).count(),
            "emis_actifs": PartageInterAgent.objects.filter(entreprise_source=entreprise, actif=True).count(),
            "recus_actifs": PartageInterAgent.objects.filter(entreprise_cible=entreprise, actif=True).count(),
        },
        "fusions_cellules": lambda: {
            "total": FusionCelluleTable.objects.filter(table__entreprise=entreprise).count(),
            "par_table": par_table(FusionCelluleTable.objects.filter(table__entreprise=entreprise), "table_id"),
            "tables_denaturees": TableDynamique.objects.filter(entreprise=entreprise, denaturee=True).count(),
        },
        "ontologie": lambda: {
            "pages": PageOntologie.objects.filter(entreprise=entreprise).count(),
            "groupes_tables": GroupeTables.objects.filter(entreprise=entreprise).count(),
        },
        "wallets": lambda: {"total": Wallet.objects.filter(membre__entreprise=entreprise).count()},
        "cycles": lambda: {
            "archives": ArchiveCycle.objects.filter(entreprise=entreprise).count(),
        },
    }
    for nom, construire in blocs.items():
        try:
            archi[nom] = construire()
        except Exception as exc:   # un bloc en échec ne vide jamais les autres
            logger.exception(_i18n_t("engine/fusion.py:g:347:30"), nom, entreprise.id)
            etat["avertissements"].append(f"architecture/{nom} : {type(exc).__name__}")
    etat["architecture"] = archi


_SECTIONS = (("tables", _fusion_tables, True), ("relations", _fusion_relations, False), ("kpis", _fusion_kpis, False),
             ("blockchain", _fusion_blockchain, True), ("activite", _fusion_activite, True), ("geo", _fusion_geo, False),
             ("architecture", _fusion_architecture, True), ("communication", _fusion_communication, True))


def fusionner_etat(entreprise, forcer=False):
    """État fusionné complet de `entreprise` (dict JSON-sérialisable). Cache 60 s ; `forcer=True` l'ignore."""
    cle = f"fusion:{entreprise.id}"
    if not forcer:
        en_cache = cache.get(cle)
        if en_cache is not None:
            return en_cache
    maintenant = timezone.now()
    etat = {"genere_le": maintenant.isoformat(), "ts": maintenant.timestamp(), "entreprise": {"id": entreprise.id, "nom": entreprise.nom},
            "tables": [], "relations": {}, "kpis": [], "blockchain": {}, "taches": [], "modeles_ia": [],
            "notifications": {}, "membres": {}, "geo": {"tables": [], "logistique": None}, "architecture": {}, "communication": {}, "avertissements": []}
    for nom, section, avec_date in _SECTIONS:
        try:
            section(entreprise, maintenant, etat) if avec_date else section(entreprise, etat)
        except Exception as exc:  # une section en échec ne bloque jamais les autres
            logger.exception(_i18n_t("engine/fusion.py:g:372:30"), nom, entreprise.id)
            etat["avertissements"].append(f"{nom} : {type(exc).__name__}")
    etat["resume"] = {
        "tables": len(etat["tables"]), "lignes": sum(t["nb_lignes"] for t in etat["tables"]),
        "colonnes": sum(len(t["champs"]) for t in etat["tables"]),
        "liens": (etat["relations"] or {}).get("total_liens", 0), "kpis": len(etat["kpis"]),
        "blocs": (etat["blockchain"] or {}).get("total", 0), "taches": len(etat["taches"]),
        "modeles_ia": len(etat["modeles_ia"]),
    }
    cache.set(cle, etat, CACHE_SECONDES)
    return etat
