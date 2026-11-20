"""DÉCOUPAGE ET RENOMMAGE D'UNE TABLE (gestion des données).

Deux opérations, une seule source de règles :

  * renommer_table(table, nom)           — change le NOM affiché ; le slug (utilisé par les liens, les formules, les URL)
                                           ne bouge jamais, donc rien ne casse.
  * decouper_table(table, parties, ...)  — coupe une table en 2 morceaux ou plus, chacun avec son nom. Les lignes sont
                                           DÉPLACÉES (jamais copiées, jamais perdues) dans l'ordre de création ; les
                                           parties sont réunies dans un GroupeTables chaîné (partie_precedente), comme
                                           un import trop volumineux. La table d'origine devient la partie 1 (même id :
                                           formules, KPI, tableaux de bord et liens restent attachés à elle).

Pourquoi couper : un modèle ne s'entraîne pas sur plus de LIMITE_LIGNES_ENTRAINEMENT lignes (engine/contraintes_ia.py) et
refuse le jeu en demandant de le couper ; ce module est le moyen de le faire sans quitter la plateforme.

Par défaut les parties sont des tables INDÉPENDANTES (chacune ≤ plafond, donc chacune entraînable) rangées là où était la table ;
`grouper=True` les réunit en plus dans un GroupeTables chaîné (comme un import volumineux) — mais un groupe s'agrège pour l'IA
(enregistrements_groupe) : il ne règle donc pas la limite d'entraînement.

La partie PURE (planifier, repartir_egalement, nettoyer_nom, noms_par_defaut) n'importe pas Django : elle se teste seule.
Toutes les erreurs sont des DecoupageError(cle, **params) : `cle` est une clé i18n (catalogues fr/en, préfixe « decoupage. »),
`params` ses variables ; l'appelant écrit le message avec tf(e.cle, **e.params) dans la langue de l'utilisateur.
"""
from __future__ import annotations

import copy

PLAFOND_PARTIE = 100_000          # une partie ne dépasse jamais le plafond absolu d'une table (TableDynamique.PLAFOND_ABSOLU_LIGNES)
PARTIES_MAX = 50                 # garde-fou : au-delà, découper en deux fois plutôt qu'en une
NOM_MAX = 120                    # longueur de TableDynamique.nom
LOT = 500                        # ids par requête de déplacement (limite de variables SQL)


class DecoupageError(ValueError):
    """Erreur de règle : `cle` = clé i18n, `params` = variables du message."""

    def __init__(self, cle, **params):
        super().__init__(cle)
        self.cle = cle
        self.params = params


# ── Partie pure ──────────────────────────────────────────────────────────────────────────────────────────────────

def nettoyer_nom(nom, autres_noms=()):
    """Nom validé (espaces superflus retirés) ; DecoupageError s'il est vide, trop long ou déjà pris (casse ignorée)
    parmi `autres_noms` (les noms des AUTRES tables de l'entreprise)."""
    propre = " ".join(str(nom or "").split())
    if not propre:
        raise DecoupageError("decoupage.err_nom_vide")
    if len(propre) > NOM_MAX:
        raise DecoupageError("decoupage.err_nom_long", max=NOM_MAX)
    pris = {" ".join(str(n or "").split()).casefold() for n in autres_noms}
    if propre.casefold() in pris:
        raise DecoupageError("decoupage.err_nom_pris", nom=propre)
    return propre


def repartir_egalement(total, nb_parties):
    """Tailles de `nb_parties` morceaux de `total` lignes, au plus 1 d'écart (les premiers reçoivent le reste)."""
    total, nb_parties = int(total), int(nb_parties)
    if nb_parties < 1:
        raise DecoupageError("decoupage.err_nb_parties", min=2, max=PARTIES_MAX)
    base, reste = divmod(total, nb_parties)
    return [base + (1 if i < reste else 0) for i in range(nb_parties)]


def noms_par_defaut(nom, nb_parties, modele="{nom} ({i}/{n})"):
    """Noms proposés « Ventes (1/3) »… ; la base est raccourcie pour que chaque nom tienne dans NOM_MAX."""
    suffixe_max = len(modele.format(nom="", i=nb_parties, n=nb_parties))
    base = " ".join(str(nom or "").split())[:max(1, NOM_MAX - suffixe_max)]
    return [modele.format(nom=base, i=i, n=nb_parties) for i in range(1, nb_parties + 1)]


def planifier(total, parties, autres_noms=()):
    """Plan de découpage d'une table de `total` lignes.

    `parties` : liste de {"nom": str, "nb_lignes": int | None}. Une seule partie peut ne pas donner nb_lignes, et c'est la
    DERNIÈRE : elle reçoit tout le reste. Sinon la somme des tailles doit être exactement `total` (aucune ligne laissée de côté,
    aucune inventée). `autres_noms` : noms des AUTRES tables de l'entreprise (la table coupée n'en fait pas partie : sa partie 1
    peut donc garder son nom).
    Retourne [{"nom", "debut", "fin"}] (lignes debut..fin-1 dans l'ordre de création), ou lève DecoupageError."""
    if not isinstance(parties, (list, tuple)) or not 2 <= len(parties) <= PARTIES_MAX:
        raise DecoupageError("decoupage.err_nb_parties", min=2, max=PARTIES_MAX)
    if total < len(parties):
        raise DecoupageError("decoupage.err_trop_peu_lignes", n=total, parties=len(parties))

    tailles = []
    for i, p in enumerate(parties):
        brut = (p or {}).get("nb_lignes") if isinstance(p, dict) else None
        if brut in (None, ""):
            if i != len(parties) - 1:
                raise DecoupageError("decoupage.err_taille_manquante", i=i + 1)
            tailles.append(None)
            continue
        try:
            n = int(brut)
        except (TypeError, ValueError):
            raise DecoupageError("decoupage.err_taille_invalide", i=i + 1)
        if n < 1:
            raise DecoupageError("decoupage.err_taille_invalide", i=i + 1)
        tailles.append(n)

    if tailles[-1] is None:
        deja = sum(tailles[:-1])
        if deja >= total:
            raise DecoupageError("decoupage.err_reste_vide", n=total, deja=deja)
        tailles[-1] = total - deja
    elif sum(tailles) != total:
        raise DecoupageError("decoupage.err_somme", somme=sum(tailles), n=total)

    vus, plan, debut = set(), [], 0
    deja_pris = list(autres_noms)
    for i, (p, n) in enumerate(zip(parties, tailles), start=1):
        if n > PLAFOND_PARTIE:
            raise DecoupageError("decoupage.err_partie_trop_grande", i=i, n=n, max=PLAFOND_PARTIE)
        nom = nettoyer_nom(p.get("nom"), deja_pris)
        cle = nom.casefold()
        if cle in vus:
            raise DecoupageError("decoupage.err_noms_identiques", nom=nom)
        vus.add(cle)
        plan.append({"nom": nom, "debut": debut, "fin": debut + n})
        debut += n
    return plan


# ── Partie Django ────────────────────────────────────────────────────────────────────────────────────────────────

def _autres_noms(table):
    from .models import TableDynamique
    return list(TableDynamique.objects.filter(entreprise_id=table.entreprise_id).exclude(pk=table.pk).values_list("nom", flat=True))


def renommer_table(table, nom):
    """Renomme `table` (nom d'affichage uniquement ; le slug ne change pas). Retourne la table."""
    table.nom = nettoyer_nom(nom, _autres_noms(table))
    table.save(update_fields=["nom"])
    return table


def nom_enfant_apres_renommage(nom_table, ancien, nouveau):
    """Nom d'une table enfant quand son groupe passe de `ancien` à `nouveau` : le préfixe `ancien` (casse ignorée, suivi de la fin du nom,
    d'un espace ou d'une parenthèse) est remplacé par `nouveau` — « Ventes (partie 2/3) » devient « Achats (partie 2/3) ». None si le nom
    de la table ne commence pas par le nom du groupe : un nom choisi à la main est conservé, jamais écrasé."""
    ancien, nom_table = str(ancien or "").strip(), str(nom_table or "")
    if not ancien or not nom_table.casefold().startswith(ancien.casefold()):
        return None
    reste = nom_table[len(ancien):]
    if reste and reste[0] not in " (":
        return None
    return f"{nouveau}{reste}"


def renommer_groupe(groupe, nom):
    """Renomme `groupe` ; ses tables enfants (parties et table fusionnée) suivent directement (voir nom_enfant_apres_renommage).
    Tout dans UNE transaction : un nom d'enfant déjà pris ou trop long annule l'ensemble. Retourne (groupe, [(table, ancien_nom)])."""
    from django.db import transaction

    from .models import TableDynamique

    propre = nettoyer_nom(nom)
    ancien = groupe.nom
    with transaction.atomic():
        enfants = {t.pk: t for t in list(groupe.tables.all()) + list(groupe.tables_de_fusion.all())}
        pris = list(TableDynamique.objects.filter(entreprise_id=groupe.entreprise_id).exclude(pk__in=list(enfants)).values_list("nom", flat=True))
        changes = []
        for t in enfants.values():
            nouveau = nom_enfant_apres_renommage(t.nom, ancien, propre)
            if nouveau is None or nouveau == t.nom:
                pris.append(t.nom)
                continue
            nouveau = nettoyer_nom(nouveau, pris)   # DecoupageError : nom pris ou trop long → rien n'est modifié
            pris.append(nouveau)
            changes.append((t, t.nom, nouveau))
        for t, _avant, nouveau in changes:
            t.nom = nouveau
            t.save(update_fields=["nom"])
        groupe.nom = propre
        groupe.save(update_fields=["nom"])
    return groupe, [(t, avant) for t, avant, _ in changes]


def verifier_decoupable(table):
    """Lève DecoupageError si `table` ne peut pas être coupée telle quelle (sinon None)."""
    if table.est_table_membres:
        raise DecoupageError("decoupage.err_table_membres")
    if table.groupe_id or table.groupe_fusionne_id:
        raise DecoupageError("decoupage.err_deja_groupe")
    if table.partie_precedente_id or getattr(table, "partie_suivante", None) is not None:
        raise DecoupageError("decoupage.err_deja_groupe")


def _cloner(objet, **remplacements):
    """Nouvelle instance NON enregistrée de `objet` : tous ses champs copiés (JSON en copie profonde), sauf la clé primaire et les
    champs uniques ; `remplacements` impose les autres valeurs. Clés de `remplacements` = noms de COLONNES (`table_id`, `groupe_id`…),
    comme les valeurs copiées : mélanger `groupe=` et `groupe_id=` laisserait Django appliquer la mauvaise."""
    valeurs = {}
    for f in objet._meta.concrete_fields:
        if f.primary_key or f.unique:
            continue
        valeurs[f.attname] = copy.deepcopy(getattr(objet, f.attname))
    valeurs.update(remplacements)
    return type(objet)(**valeurs)


def decouper_table(table, parties, grouper=False, nom_groupe=None, modele_description="{nom}"):
    """Coupe `table` selon `parties` (voir planifier) et retourne (groupe ou None, [tables dans l'ordre des parties]).

    Tout est dans UNE transaction : un échec ne laisse ni table à moitié remplie ni groupe incomplet. La partie 1 est la table
    d'origine (renommée) ; les suivantes sont de nouvelles tables à la structure identique, qui reçoivent les lignes déplacées."""
    from django.db import transaction
    from django.db.models import Case, IntegerField, Value, When

    from . import hierarchie
    from .import_fichier import _slug_table_unique
    from .models import ChampDynamique, Enregistrement, GroupeTables, TableDynamique, ValeurChamp

    with transaction.atomic():
        table = TableDynamique.objects.select_for_update().get(pk=table.pk)
        verifier_decoupable(table)
        ids = list(table.enregistrements.order_by("id").values_list("id", flat=True))
        plan = planifier(len(ids), parties, autres_noms=_autres_noms(table))
        champs = list(table.champs.order_by("ordre", "id"))
        nb = len(plan)

        groupe = None
        if grouper:
            groupe = GroupeTables.objects.create(
                entreprise=table.entreprise,
                nom=(nom_groupe or table.nom)[:NOM_MAX],
                description=modele_description.format(nom=table.nom, n=len(ids), parties=nb),
                cree_automatiquement=False,
            )
        conteneurs = [] if grouper else hierarchie._conteneurs_de_tables(table.entreprise, [table.id])   # parties indépendantes : rangées avec l'original

        # 1) Les tables : la partie 1 EST la table d'origine, les autres naissent avec les mêmes colonnes.
        tables, correspondances = [], []   # correspondances[i] = {ancien champ.id: nouveau champ.id} (vide pour la partie 1)
        precedente = None
        for i, p in enumerate(plan, start=1):
            if i == 1:
                cible = table
                cible.nom = p["nom"]
                correspondances.append({})
            else:
                cible = _cloner(
                    table, nom=p["nom"], slug=_slug_table_unique(table.entreprise, p["nom"]),
                    groupe_id=None, groupe_fusionne_id=None, partie_precedente_id=None, table_extension_colonnes_id=None,
                    url_source="", jeton_source="", jeton_a_reconfigurer=False, lignes_source=0,
                )
                cible.save()
                correspondance = {}
                for c in champs:
                    nouveau = _cloner(c, table_id=cible.id)
                    nouveau.save()
                    correspondance[c.id] = nouveau.id
                correspondances.append(correspondance)
            if grouper:
                cible.groupe = groupe
                cible.partie_index, cible.partie_total = i, nb
                cible.partie_precedente = precedente
            cible.save()
            if i > 1:
                for c in conteneurs:
                    c.tables.add(cible)
            tables.append(cible)
            precedente = cible

        # 2) Les lignes : déplacées (jamais copiées) vers leur partie, valeurs de colonnes comprises.
        for p, cible, corr in zip(plan[1:], tables[1:], correspondances[1:]):
            morceau = ids[p["debut"]:p["fin"]]
            for k in range(0, len(morceau), LOT):
                lot = morceau[k:k + LOT]
                Enregistrement.objects.filter(pk__in=lot).update(table=cible)
                if table.type_stockage != TableDynamique.STOCKAGE_NOSQL and corr:   # NoSQL : valeurs_json par slug, rien à remapper
                    ValeurChamp.objects.filter(enregistrement_id__in=lot).update(champ_id=Case(
                        *[When(champ_id=ancien, then=Value(nouveau)) for ancien, nouveau in corr.items()],
                        default="champ_id", output_field=IntegerField()))

        try:   # le fichier Parquet de l'original décrivait TOUTES les lignes : il est refait à la prochaine lecture (meilleur effort, jamais bloquant)
            from . import parquet_store
            parquet_store.supprimer_fichier(table)
        except Exception:  # noqa: BLE001
            pass
        if grouper:
            hierarchie.placer_tables_dans_groupe(groupe, tables)
        return groupe, tables
