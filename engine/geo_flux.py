"""GÉOSPATIAL — flux de positions -> tables (décodage : engine/flux_positions.py, lecture continue : commande `suivre_flux_positions`).

Même modèle que la carte : UNE COLONNE « geolocalisation » = UN OBJET SUIVI (navire, avion, véhicule) ; UNE LIGNE = UN PAS DE TEMPS
(les positions arrivées dans le même pas, la dernière de chaque objet gagnant) ; la première colonne date et heure donne l'axe du temps.

  * Un objet dont la colonne n'existe pas la reçoit automatiquement (nom = nom du navire/indicatif s'il est connu, sinon l'identifiant ;
    AUCUN plafond d'objets (`geospatial.LIMITES['objets_max']` vaut None ; un nombre y remet un plafond) ; le plafond de colonnes réglé par l'entreprise
    ne s'applique pas : une colonne par objet suivi fait partie de la donnée). Une colonne existante est retrouvée par son identifiant ou son nom.
  * Idempotent : la ligne d'un pas de temps porte un jeton (`Enregistrement.idem_key = flux:<ms>`). Rejouer le même fichier ne duplique rien ;
    une ligne déjà là est seulement COMPLÉTÉE (cellules vides), jamais écrasée.
  * Écriture en lots (SQL : ValeurChamp ; NoSQL : valeurs_json + un seul fichier JSON resynchronisé), un bloc de blockchain par lot pour les
    lignes créées, formules et constantes recalculées ensuite (bulk_create ne déclenche pas le signal post_save), plafond de lignes respecté.
  * `rotation=True` : au plafond de lignes, les plus anciennes lignes DU FLUX (idem_key « flux:… », jamais une ligne saisie à la main) sont
    supprimées pour faire de la place aux nouvelles, au lieu de refuser les positions les plus récentes.
  * Ancien = archivé : au-delà des lignes récentes conservées (GEO_LIGNES_RECENTES), les plus anciennes sont écrites dans des fichiers d'archive
    (engine/geo_archives.py) avant d'être retirées de la table ; la rotation au plafond archive aussi.
  * Après chaque écriture, les cartes ouvertes en sont prévenues (WebSocket entreprise, evenement « geo_positions »).
"""
from __future__ import annotations
from engine.i18n import t
from engine.i18n import tf

import contextlib
import logging
import time

from django.db import transaction
from django.utils.text import slugify

from . import flux_positions as _f
from . import geo_donnees as _gd
from . import geospatial as _g
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)
LOT = 400   # marge sous la limite de variables SQL (SQLite : 999)


class ErreurFlux(Exception):
    """Message directement affichable à l'utilisateur."""


def _cle(texte) -> str:
    return slugify(str(texte), allow_unicode=True) or "objet"


def _par_lots(seq, n: int = LOT):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def colonne_temps(table):
    """(champ, créé). Même choix que geo_donnees.matrice_table : la première colonne date/datetime ; sans elle, « Horodatage » est créée."""
    from .models import ChampDynamique
    champ = table.champs.filter(type_champ__in=_gd.TYPES_TEMPS).order_by("ordre", "id").first()
    if champ is not None:
        if champ.type_champ != "datetime":
            raise ErreurFlux(tf("engine/geo_flux.py:g:55:30", v1=champ.nom))
        return champ, False
    tous = list(table.champs.all())
    pris = {c.slug for c in tous}
    slug, k = "horodatage", 2
    while slug in pris:
        slug, k = f"horodatage-{k}", k + 1
    return ChampDynamique.objects.create(table=table, nom="Horodatage", slug=slug, type_champ="datetime", obligatoire=False,
                                         ordre=max((c.ordre for c in tous), default=-1) + 1), True


def preparer_colonnes(table, objets: dict, creer: bool = True):
    """(colonne_par_objet {id: ChampDynamique}, noms des colonnes créées, nombre d'objets sans colonne). `objets` = {id: nom}."""
    from .models import ChampDynamique
    tous = list(table.champs.order_by("ordre", "id"))
    pris = {c.slug for c in tous}
    geo = [c for c in tous if c.type_champ == "geolocalisation"]
    index: dict = {}
    for c in geo:
        for k in (c.slug, _cle(c.nom), c.nom.strip().lower()):
            index.setdefault(k, c)
    plafond = _g.LIMITES["objets_max"]   # None = aucun plafond (le même réglage que l'analyse) ; plafond_colonnes règle la structure saisie à la main
    place = plafond - len(geo) if plafond else float("inf")
    ordre = max((c.ordre for c in tous), default=-1)
    par_objet, creees, ignores, utilises, a_creer = {}, [], 0, set(), []
    for id_, nom in objets.items():
        c = index.get(_cle(id_)) or index.get(id_.strip().lower()) or (index.get(_cle(nom)) if nom and nom != id_ else None)
        if c is None:
            if not creer or place <= 0:
                ignores += 1
                continue
            base, slug, k = _cle(id_), _cle(id_), 2
            while slug in pris:
                slug, k = f"{base}-{k}", k + 1
            ordre += 1
            c = ChampDynamique(table=table, nom=(nom or id_)[:120], slug=slug, type_champ="geolocalisation", obligatoire=False, ordre=ordre)
            a_creer.append(c)   # créées toutes ensemble plus bas (bulk_create), pas une requête par objet
            pris.add(slug)
            index[slug] = c
            place -= 1
            creees.append(c.nom)
        elif nom and nom != id_ and c.nom.strip() == id_:   # une colonne nommée par l'identifiant reçoit le vrai nom, un nom choisi est respecté
            c.nom = nom[:120]
            c.save(update_fields=["nom"])
        if c.slug in utilises:   # deux identifiants qui aboutiraient à la même colonne : le second est écarté plutôt que mélangé
            ignores += 1
            continue
        utilises.add(c.slug)
        par_objet[id_] = c
    if a_creer:
        for lot in _par_lots(a_creer, 500):
            ChampDynamique.objects.bulk_create(lot)
        from . import parquet_store
        parquet_store.planifier_synchro(table)   # bulk_create ne déclenche aucun signal : les nouvelles colonnes rejoignent le fichier Parquet ici
        if any(c.pk is None for c in a_creer):   # pilote sans RETURNING : les identifiants sont relus par slug
            relus = {r.slug: r.pk for lot in _par_lots([c.slug for c in a_creer]) for r in table.champs.filter(slug__in=lot)}
            for c in a_creer:
                c.pk = relus[c.slug]
    return par_objet, creees, ignores


_PAUSE_PUSH = {"jusqu_a": 0.0}


def _pousser(table):
    """Prévient les cartes ouvertes (ws_notify, jamais de group_send direct). Au mieux : sans Redis, elles se rafraîchissent toutes les 20 s ;
    après un échec on cesse d'essayer 60 s, pour qu'un Redis absent ne ralentisse pas l'écriture du flux."""
    if time.monotonic() < _PAUSE_PUSH["jusqu_a"]:
        return
    try:
        from .ws_notify import notifier_entreprise
        notifier_entreprise(table.entreprise_id, evenement="geo_positions", donnees={"table_id": table.id})
    except Exception as e:
        _PAUSE_PUSH["jusqu_a"] = time.monotonic() + 60
        logger.info(t("engine/geo_flux.py:g:119:21"), e)


def _faire_place(table, a_ajouter: int) -> int:
    """Rotation : supprime les plus anciennes lignes du flux pour que `a_ajouter` lignes tiennent sous le plafond ; renvoie le nombre supprimé."""
    from .models import Enregistrement
    exces = table.enregistrements.count() + a_ajouter - table.plafond_lignes
    if exces <= 0:
        return 0
    ids = list(table.enregistrements.filter(idem_key__startswith="flux:").order_by("id").values_list("id", flat=True)[:exces])
    from .geo_archives import archiver_et_retirer
    return archiver_et_retirer(table, ids)   # écrit dans l'archive PUIS supprime (si l'écriture échoue, rien n'est supprimé)


@contextlib.contextmanager
def _verrou_table(table):
    """Un seul écrivain à la fois par table (plusieurs suivis, page, partage de position) : verrou de fichier pris AVANT la transaction, pour que deux écrivains
    ne créent ni la même colonne ni la même ligne (contrainte unique table + jeton). Sans verrou possible (disque en lecture seule), on écrit quand même."""
    try:
        import fcntl
        from .stockage import dossier_systeme
        dossier = dossier_systeme("flux_actifs")
        dossier.mkdir(parents=True, exist_ok=True)
        f = open(dossier / f"ecriture-{int(table.id)}.verrou", "a")
        fcntl.flock(f, fcntl.LOCK_EX)
    except (OSError, ImportError, ValueError, AttributeError, TypeError) as e:
        logging.getLogger(__name__).warning(_gt("rg5.msg.060"), e)
        yield
        return
    try:
        yield
    finally:
        f.close()   # libère le verrou


def importer_positions(table, utilisateur, positions: list, pas_s: float = 10.0, creer_colonnes: bool = True, rotation: bool = False) -> dict:
    """Range les positions dans `table` (voir l'en-tête du module) et renvoie le compte rendu."""
    with _verrou_table(table):
        return _importer_positions(table, utilisateur, positions, pas_s, creer_colonnes, rotation)


def _importer_positions(table, utilisateur, positions: list, pas_s: float = 10.0, creer_colonnes: bool = True, rotation: bool = False) -> dict:
    rapport = {"lignes_creees": 0, "lignes_completees": 0, "positions": 0, "objets": 0, "colonnes_creees": [], "colonne_temps": None,
               "colonne_temps_creee": False, "objets_sans_colonne": 0, "lignes_tronquees": 0, "lignes_supprimees": 0}
    if not positions:
        return rapport
    from .models import Bloc, Enregistrement, ValeurChamp
    from . import calculs as _calculs, nosql_store
    objets: dict = {}
    for p in positions:
        if p["id"] not in objets or (objets[p["id"]] == p["id"] and p["nom"] != p["id"]):
            objets[p["id"]] = p["nom"]
    with transaction.atomic():
        champ_t, temps_cree = colonne_temps(table)
        par_objet, creees, ignores = preparer_colonnes(table, objets, creer_colonnes)
        champs = {c.slug: c for c in [champ_t, *par_objet.values()]}
        lignes = _f.construire_lignes(_f.grouper(positions, pas_s), {i: c.slug for i, c in par_objet.items()}, champ_t.slug)
        rapport.update(objets=len(par_objet), colonnes_creees=creees, colonne_temps=champ_t.slug, colonne_temps_creee=temps_cree, objets_sans_colonne=ignores)
        if not lignes:
            return rapport

        existantes: dict = {}
        for lot in _par_lots(l["cle"] for l in lignes):
            for e in Enregistrement.objects.filter(table=table, idem_key__in=lot).order_by("id"):
                existantes.setdefault(e.idem_key, e)
        nouvelles = [l for l in lignes if l["cle"] not in existantes]
        if table.plafond_lignes is not None:   # le plafond de la table s'applique aussi au flux (les plus anciens pas d'abord)
            if rotation and nouvelles:
                from .geo_archives import archiver_valeurs
                ecartees = nouvelles[:-table.plafond_lignes] if table.plafond_lignes else nouvelles
                archiver_valeurs(table, [{"cle": l["cle"], "valeurs": l["valeurs"]} for l in ecartees])   # les pas écartés (plus anciens que le plafond) sont archivés, pas perdus
                nouvelles = nouvelles[-table.plafond_lignes:]   # plus de nouvelles lignes que le plafond : les plus récentes seulement
                rapport["lignes_supprimees"] = _faire_place(table, len(nouvelles))
            libre = max(table.plafond_lignes - table.enregistrements.count(), 0)
            rapport["lignes_tronquees"] = max(len(nouvelles) - libre, 0)
            nouvelles = nouvelles[:libre]
        a_completer = [l for l in lignes if l["cle"] in existantes]
        touchees = []

        if table.est_nosql:
            Enregistrement.objects.bulk_create([Enregistrement(table=table, cree_par=utilisateur, idem_key=l["cle"], valeurs_json=l["valeurs"])
                                                for l in nouvelles], batch_size=2000)
            modifiees = []
            for l in a_completer:
                e = existantes[l["cle"]]
                actuel = dict(e.valeurs_json or {})
                ajout = {s: v for s, v in l["valeurs"].items() if actuel.get(s) in (None, "")}
                if ajout:
                    e.valeurs_json = {**actuel, **ajout}
                    modifiees.append(e)
            if modifiees:
                Enregistrement.objects.bulk_update(modifiees, ["valeurs_json", "maj_le"], batch_size=2000)   # maj_le : sinon l'empreinte KPI/BI ne bouge pas
            if nouvelles or modifiees:
                nosql_store.synchroniser_fichier(table)   # UNE resynchronisation pour tout le lot
        else:
            Enregistrement.objects.bulk_create([Enregistrement(table=table, cree_par=utilisateur, idem_key=l["cle"]) for l in nouvelles], batch_size=2000)
        creees_par_cle: dict = {}
        for lot in _par_lots(l["cle"] for l in nouvelles):
            for e in Enregistrement.objects.filter(table=table, idem_key__in=lot).order_by("id"):
                creees_par_cle.setdefault(e.idem_key, e)
        if not table.est_nosql:
            a_creer = [ValeurChamp(enregistrement=creees_par_cle[l["cle"]], champ=champs[s], valeur=v)
                       for l in nouvelles if l["cle"] in creees_par_cle for s, v in l["valeurs"].items()]
            deja = {}
            for lot in _par_lots(existantes[l["cle"]].id for l in a_completer):
                for vc in ValeurChamp.objects.filter(enregistrement_id__in=lot, champ__in=list(champs.values())):
                    deja[(vc.enregistrement_id, vc.champ_id)] = vc
            maj = []
            for l in a_completer:
                e = existantes[l["cle"]]
                for s, v in l["valeurs"].items():
                    vc = deja.get((e.id, champs[s].id))
                    if vc is None:
                        a_creer.append(ValeurChamp(enregistrement=e, champ=champs[s], valeur=v))
                    elif not vc.valeur:
                        vc.valeur = v
                        maj.append(vc)
            ValeurChamp.objects.bulk_create(a_creer, batch_size=2000)
            if maj:
                ValeurChamp.objects.bulk_update(maj, ["valeur"], batch_size=2000)
        touchees = [creees_par_cle[l["cle"]] for l in nouvelles if l["cle"] in creees_par_cle] + [existantes[l["cle"]] for l in a_completer]

        if table.champs.filter(type_champ__in=("formule", "constante", "horodatage")).exists():   # bulk_create ne déclenche pas post_save : on le fait ici
            for e in touchees:
                e.table = table
                _calculs.assurer_constantes(e)
                _calculs.recalculer_champs_formule_ligne(e)
        if touchees:
            # bulk_create/bulk_update ne déclenchent aucun signal : on rejoue ici la cascade de la gestion de données —
            # lignes existantes complétées marquées modifiées (empreinte KPI/BI), formules ∑ / « = », puis tables de
            # calcul et KPI (sans effet, aucune requête de calcul, si la table n'en a aucun).
            from django.utils import timezone as _tz
            from .models import Enregistrement as _Enr
            if a_completer and not table.est_nosql:
                _Enr.objects.filter(id__in=[existantes[l["cle"]].id for l in a_completer]).update(maj_le=_tz.now())
            from .calc_champ import recalculer_formules_table
            recalculer_formules_table(table, toutes_les_lignes=True)
            _calculs.recalculer_lignes_pour_table_source(table)
        tx = [{"type": "creation_ligne", "table_id": table.id, "table": table.nom, "enregistrement_id": creees_par_cle[l["cle"]].id,
               "agent_id": getattr(utilisateur, "id", None), "donnees": {champs[s].nom: v for s, v in l["valeurs"].items()}}
              for l in nouvelles if l["cle"] in creees_par_cle]
        for lot in _par_lots(tx, 500):
            Bloc.ajouter_transactions(table.entreprise_id, lot)

        if not table.est_nosql:
            from . import parquet_store
            parquet_store.planifier_synchro(table)   # fichier Parquet de la table, une fois le lot validé
        rapport["lignes_creees"] = len(creees_par_cle)
        rapport["lignes_completees"] = len(a_completer)
        rapport["positions"] = sum(len(l["valeurs"]) - 1 for l in nouvelles + a_completer)
        rapport["premiere"], rapport["derniere"] = lignes[0]["valeurs"][champ_t.slug], lignes[-1]["valeurs"][champ_t.slug]
    if rapport["lignes_creees"] or rapport["lignes_completees"]:
        try:
            from .geo_archives import conserver_recents
            rapport["lignes_archivees"] = conserver_recents(table)
        except Exception:   # l'archivage ne doit jamais faire échouer l'écriture des positions
            logger.exception(t("engine/geo_flux.py:g:246:30"), table.id)
        _pousser(table)   # hors transaction : les cartes relisent des données déjà validées
        from .geo_surveillance import surveiller
        rapport["notifications_geo"] = surveiller(table)   # alertes de position -> notifications, sans page ouverte (au plus 1 fois/min/table)
    return rapport


def traiter_flux(table, utilisateur, texte: str, format: str = "auto", id_defaut: str = "gps", pas_s: float = 10.0, horodatage: str = "source",
                 creer_colonnes: bool = True, apercu: bool = False, rotation: bool = False) -> dict:
    """Décode `texte` puis (sauf `apercu`) l'écrit dans `table`. Renvoie {rapport, objets, echantillon, ecriture}."""
    positions, rapport = _f.analyser_texte(texte, format, id_defaut, horodatage=horodatage)
    resume: dict = {}
    for p in positions:
        r = resume.setdefault(p["id"], {"id": p["id"], "nom": p["nom"], "positions": 0, "source": p["source"]})
        r["positions"] += 1
        if p["nom"] != p["id"]:
            r["nom"] = p["nom"]
    sortie = {"rapport": rapport, "objets": list(resume.values())[:100], "echantillon": positions[:8], "ecriture": None}
    if positions and not apercu:
        sortie["ecriture"] = importer_positions(table, utilisateur, positions, pas_s, creer_colonnes, rotation)
    return sortie
