"""FUSION D'UN GROUPE DE TABLES EN UNE SEULE TABLE — réversible.

Un GroupeTables relie plusieurs tables de même structure (« grande table ») sans jamais rien fusionner : une table EAV = un fichier
Parquet, et le groupe n'est assemblé qu'à la lecture (voir parquet_store, section GROUPES ET CONTENEURS). Ici l'utilisateur demande
l'inverse : UNE table, donc UN fichier Parquet, qui porte les lignes de toutes les parties.

Principe — rien n'est supprimé, on peut défusionner :
  * la fusion CRÉE une nouvelle table (TableDynamique.groupe_fusionne = le groupe) : mêmes colonnes que les parties, lignes de toutes les
    parties dans l'ordre du groupe (partie_index, puis ancienneté), valeurs recopiées TELLES QUELLES (ValeurChamp.valeur, sans
    interprétation ; dates de création/modification, couleurs de ligne et de cellule conservées). Son fichier Parquet est construit
    par parquet_store à partir de ces lignes : un seul fichier, image fidèle de la table fusionnée ;
  * les parties, leurs lignes et leurs fichiers Parquet restent INTACTS et le groupe existe toujours ; l'état « fusionné » est dérivé
    (une table fusionnée existe) : les parties ne sont alors plus listées (api_tables), seule la table fusionnée l'est, à la place du
    groupe dans son conteneur éventuel ;
  * DÉFUSIONNER supprime la table fusionnée (et son fichier) et rend les parties. Ce que l'on a modifié dans la table fusionnée depuis
    la fusion n'est pas reporté dans les parties : `modifiee_depuis_fusion` le détecte et la défusion exige alors une confirmation.

Les lignes sont créées en `bulk_create` (comme export_fidele.restaurer) : aucun signal (formules, synchronisations, notifications) ne
les modifie au passage. Un matricule ou un code d'accès (uniques dans l'entreprise) n'est pas dupliqué : les parties les gardent, la copie
fusionnée n'en reçoit pas (compté dans les avertissements).
"""
import uuid
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import ChampDynamique, Enregistrement, GroupeTables, TableDynamique, ValeurChamp

LOT = 2000


class FusionImpossible(ValueError):
    """La fusion ou la défusion demandée ne peut pas se faire (message destiné à l'utilisateur)."""


class ModificationsPerdues(FusionImpossible):
    """La table fusionnée a changé depuis la fusion : la défusion les abandonnerait (confirmation requise)."""


def parties_du_groupe(groupe):
    """Parties à fusionner : celles que le groupe présente comme une table (hors tables dénaturées), dans l'ordre du groupe."""
    return list(groupe.tables.filter(denaturee=False).order_by(F("partie_index").asc(nulls_last=True), "id"))


def sans_parties_fusionnees(queryset):
    """Retire d'un queryset de TableDynamique les parties d'un groupe fusionné : la table fusionnée les remplace, les compter aussi
    ferait deux fois les mêmes lignes. À utiliser pour les rapports et les statistiques, PAS pour ce qui doit rester exhaustif
    (sauvegardes, vérification des fichiers Parquet, graphe de dépendances) : les parties existent toujours, intactes."""
    return queryset.filter(Q(groupe__isnull=True) | Q(groupe__tables_de_fusion__isnull=True))


def prendre_verrou(groupe_id) -> bool:
    """Une seule fusion à la fois par groupe (deux clics, ou une tâche de fond déjà lancée) : False si une est déjà en cours.
    Expire seul après une heure si le processus meurt sans le libérer."""
    from django.core.cache import cache
    return cache.add(f"fusion_groupe_verrou:{groupe_id}", 1, 3600)


def liberer_verrou(groupe_id):
    from django.core.cache import cache
    cache.delete(f"fusion_groupe_verrou:{groupe_id}")


def _invalider_caches(entreprise_id):
    """Fusion et défusion changent les tables visibles : l'état fusionné (engine/fusion.py) et le graphe de dépendances (engine/portail.py),
    gardés 60 s en cache, sont refaits tout de suite au lieu de montrer l'ancien découpage."""
    try:
        from django.core.cache import cache
        cache.delete_many([f"fusion:{entreprise_id}", f"portail_graphe:{entreprise_id}"])
    except Exception:
        pass   # un cache indisponible ne doit jamais faire échouer la fusion


def erreur_fusion(groupe):
    """Message si ce groupe ne peut pas être fusionné maintenant, sinon None."""
    if groupe.est_fusionne:
        return _i18n_t("rg2.groupe_fusion.1")
    parties = parties_du_groupe(groupe)
    if len(parties) < 2:
        return _i18n_t("rg2.groupe_fusion.2")
    for t in parties:
        if t.est_nosql:
            return _i18n_tf("rg2.groupe_fusion.3", v1=t.nom)
        if t.source_systeme:
            return _i18n_tf("rg2.groupe_fusion.4", v1=t.nom)
        if t.est_table_membres:
            return _i18n_tf("rg2.groupe_fusion.5", v1=t.nom)
    signature = parties[0].signature_structure
    if any(t.signature_structure != signature for t in parties[1:]):
        return _i18n_t("rg2.groupe_fusion.6")
    return None


def _nom_libre(entreprise, nom):
    candidat, i = nom, 2
    while TableDynamique.objects.filter(entreprise=entreprise, nom=candidat).exists():
        candidat = f"{nom} ({i})"
        i += 1
    return candidat[:120]


def _copier_lignes(source, cible, champs_cible, progression):
    """Recopie les lignes de `source` dans `cible`, par lots (mémoire bornée). Retourne (nb lignes, nb identifiants non dupliqués)."""
    slug_par_champ = {c.id: c.slug for c in source.champs.all()}
    copiees, non_dupliques = 0, 0
    lignes = source.enregistrements.order_by("cree_le", "id")
    total = max(1, source.enregistrements.count())
    lot = []

    def _vider(lot):
        nonlocal copiees
        if not lot:
            return
        nouvelles, uids = [], []
        for e in lot:
            u = uuid.uuid4()
            uids.append(u)
            nouvelles.append(Enregistrement(
                table=cible, uid=u, cree_par_id=e.cree_par_id, couleur=e.couleur or "",
                couleurs_cellules=e.couleurs_cellules or {}, matricule="", code_acces_client="",
            ))
        Enregistrement.objects.bulk_create(nouvelles)
        ids_nouveaux = dict(Enregistrement.objects.filter(uid__in=uids).values_list("uid", "id"))
        a_dater = []
        for e, u in zip(lot, uids):
            n = Enregistrement(id=ids_nouveaux[u])
            n.cree_le, n.maj_le = e.cree_le, e.maj_le
            a_dater.append(n)
        Enregistrement.objects.bulk_update(a_dater, ["cree_le", "maj_le"])   # dates d'origine (automatiques à la création)
        correspondance = {e.id: ids_nouveaux[u] for e, u in zip(lot, uids)}
        valeurs = []
        for eid, champ_id, valeur in ValeurChamp.objects.filter(enregistrement_id__in=list(correspondance)).values_list(
                "enregistrement_id", "champ_id", "valeur"):
            slug = slug_par_champ.get(champ_id)
            if slug in champs_cible:
                valeurs.append(ValeurChamp(enregistrement_id=correspondance[eid], champ=champs_cible[slug], valeur=valeur))
        for i in range(0, len(valeurs), 5000):
            ValeurChamp.objects.bulk_create(valeurs[i:i + 5000])
        copiees += len(lot)
        progression(copiees)

    for e in lignes.iterator(chunk_size=LOT):
        if e.matricule or e.code_acces_client:
            non_dupliques += 1
        lot.append(e)
        if len(lot) >= LOT:
            _vider(lot)
            lot = []
    _vider(lot)
    return copiees, non_dupliques


def fusionner(groupe, nom=None, progression_cb=None):
    """Fusionne toutes les parties du groupe en UNE table (voir l'en-tête du module). Retourne la TableDynamique créée ;
    `table.avertissements_fusion` liste ce qui n'a pas pu être recopié à l'identique."""
    from . import export_fidele, hierarchie, parquet_store, securite_dates

    erreur = erreur_fusion(groupe)
    if erreur:
        raise FusionImpossible(erreur)
    securite_dates.controler(groupe.entreprise_id)   # horloge revenue en arrière : refus AVANT d'écrire la moindre ligne
    entreprise = groupe.entreprise
    parties = parties_du_groupe(groupe)
    modele = parties[0]
    total = sum(t.enregistrements.count() for t in parties) or 1
    avertissements = []

    def _prog(pct):
        if progression_cb:
            try:
                progression_cb(int(pct))
            except Exception:
                pass

    _prog(2)
    with transaction.atomic():
        table = TableDynamique(entreprise=entreprise)
        for k in export_fidele.CHAMPS_TABLE:
            setattr(table, k, getattr(modele, k))
        table.nom = _nom_libre(entreprise, (nom or groupe.nom or modele.nom).strip()[:120] or modele.nom)
        from .import_fichier import _slug_table_unique
        table.slug = _slug_table_unique(entreprise, table.nom)
        table.description = (groupe.description or "").strip() or _i18n_tf("rg2.groupe_fusion.7", v1=len(parties), v2=groupe.nom)
        table.groupe_fusionne = groupe
        table.save()

        champs = {}
        for c in modele.champs.select_related("relation_vers").order_by("ordre", "id"):
            nc = ChampDynamique(table=table)
            for k in export_fidele.CHAMPS_CHAMP:
                setattr(nc, k, getattr(c, k))
            if c.relation_vers_id:
                nc.relation_vers = c.relation_vers   # la relation pointe vers la même table qu'avant (les lignes citent les mêmes identifiants)
            nc.save()
            champs[c.slug] = nc

        fait, non_dupliques = 0, 0
        for partie in parties:
            def _progression(n, _base=fait):
                _prog(5 + 85 * min(1.0, (_base + n) / total))
            n, nd = _copier_lignes(partie, table, champs, _progression)
            fait += n
            non_dupliques += nd
        if non_dupliques:
            avertissements.append(
                _i18n_tf("rg2.groupe_fusion.8", v1=non_dupliques))
        if any(c.relation_vers_id for c in champs.values()):
            avertissements.append(_i18n_t("rg2.groupe_fusion.9"))

    # Étapes après la transaction. Si l'une échoue (hors Parquet et trace, déjà tolérants), la table fusionnée existe mais le groupe ne serait pas
    # marqué fusionné : on défusionne pour revenir à l'état d'avant plutôt que de laisser un état à moitié fait.
    try:
        # Fichier Parquet UNIQUE de la table fusionnée (construit depuis ses lignes), puis contrôle.
        try:
            parquet_store.synchroniser_table(table)
        except Exception:
            avertissements.append(_i18n_t("rg2.groupe_fusion.10"))
        _prog(95)
        groupe.fusionne_le = timezone.now()
        groupe.empreinte_fusion = parquet_store.empreinte_table(table)
        groupe.save(update_fields=["fusionne_le", "empreinte_fusion"])
        hierarchie.remplacer_groupe_par_table(groupe, table)   # dans son conteneur, la table fusionnée prend la place du groupe
    except Exception:
        try:
            defusionner(groupe, forcer=True)
        except Exception:
            pass
        _invalider_caches(entreprise.id)
        raise
    try:   # trace dans la chaîne de l'entreprise (best-effort : ne bloque jamais la fusion)
        from .models import Bloc
        from .securite_dates import DateRetrograde
        Bloc.ajouter_transaction(entreprise.id, {"type": "fusion_groupe_tables", "groupe_id": groupe.id, "groupe": groupe.nom,
                                                 "table_id": table.id, "parties": [t.id for t in parties], "lignes": fait})
    except DateRetrograde:
        raise
    except Exception:
        pass
    _invalider_caches(entreprise.id)
    table.avertissements_fusion = avertissements
    table.nb_lignes_fusion = fait
    _prog(100)
    return table


def modifiee_depuis_fusion(groupe) -> bool:
    """Vrai si une table fusionnée du groupe a changé depuis la fusion (ligne ajoutée, modifiée, supprimée ; colonne changée)."""
    from . import parquet_store
    tables = list(groupe.tables_de_fusion.all())
    if not tables or not groupe.empreinte_fusion:
        return False
    return parquet_store.empreinte_table(tables[0]) != groupe.empreinte_fusion


def defusionner(groupe, forcer=False):
    """Défusionne : supprime la table fusionnée (lignes et fichier Parquet) et rend les parties, restées intactes. Si la table fusionnée a
    été modifiée depuis la fusion, ces modifications seraient perdues : sans `forcer`, lève ModificationsPerdues. Retourne les parties."""
    from . import hierarchie, parquet_store, suppression_masse

    tables = list(groupe.tables_de_fusion.all())
    if not tables:
        raise FusionImpossible(_i18n_t("rg2.groupe_fusion.11"))
    if not forcer and modifiee_depuis_fusion(groupe):
        raise ModificationsPerdues(
            _i18n_t("rg2.groupe_fusion.12"))
    with transaction.atomic():
        hierarchie.remplacer_table_par_groupe(groupe, tables)   # dans son conteneur, le groupe reprend la place de la table fusionnée
        for table in tables:
            suppression_masse.purger_lignes(table)   # SQL direct par paquets : l'ORM chargeait chaque valeur en mémoire
            parquet_store.supprimer_fichier(table)
            table.delete()
        groupe.fusionne_le = None
        groupe.empreinte_fusion = ""
        groupe.save(update_fields=["fusionne_le", "empreinte_fusion"])
    _invalider_caches(groupe.entreprise_id)
    parties = parties_du_groupe(groupe)
    try:
        from .models import Bloc
        Bloc.ajouter_transaction(groupe.entreprise_id, {"type": "defusion_groupe_tables", "groupe_id": groupe.id, "groupe": groupe.nom})
    except Exception:
        pass
    return parties
