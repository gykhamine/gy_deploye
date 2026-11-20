"""Stockage NoSQL des tables (TableDynamique.type_stockage == "nosql").

Principe : une table NoSQL garde exactement le même fonctionnement côté
interface qu'une table SQL (grille, recherche, tri, filtres, export,
formulaire de saisie) — voir grille.py et views.py (branches
`if table.est_nosql`) — mais côté back-end :

  1. Les valeurs de chaque ligne sont stockées en un seul bloc JSON sur
     l'Enregistrement lui-même (`valeurs_json`), et non plus dispersées en
     autant de lignes ValeurChamp (EAV) qu'il y a de champs. Django gère
     nativement les lookups (`valeurs_json__<slug>__icontains=...`) donc
     la grille peut continuer à filtrer/trier sans requête supplémentaire.

  2. En plus de ça, CHAQUE TABLE est reflétée sur le disque en un fichier
     JSON unique (un objet par ligne, chaque champ = une clé), tenu à jour
     à chaque écriture — c'est le fichier que réclame littéralement une
     table "NoSQL" et qui peut être consulté/exploité hors de l'application.

Formules de calcul (calculs.py / calc_champ.py) et tableaux de bord BI
(tableau_bord_bi.py) sont désormais branchés sur le NoSQL : ils continuent
à lire/calculer exactement comme pour une table SQL (aucune fonction de
calcul réécrite), seule l'écriture du résultat passe par
`ecrire_valeur_calculee` ci-dessous plutôt que par un ValeurChamp.

Fonctionnalités qui restent conçues pour le stockage SQL et ne sont PAS
(encore) branchées sur le NoSQL : relations bidirectionnelles (LienChamp/
colonne partagée, qui s'appuient sur la ligne ValeurChamp elle-même comme
ancre du lien), blockchain par valeur de champ. Elles continuent de
fonctionner normalement pour les tables SQL.
"""
import json
import os

from django.conf import settings


def _dossier_entreprise(entreprise_id) -> str:
    dossier = os.path.join(settings.MEDIA_ROOT, "nosql_tables", str(entreprise_id))
    os.makedirs(dossier, exist_ok=True)
    return dossier


def chemin_fichier(table) -> str:
    """Chemin du fichier JSON unique représentant cette table NoSQL."""
    return os.path.join(_dossier_entreprise(table.entreprise_id), f"{table.id}_{table.slug}.json")


def synchroniser_fichier(table) -> None:
    """Réécrit intégralement le fichier JSON de la table à partir de l'état
    actuel en base (source de vérité pour l'ordre/pagination/relations
    techniques) : {"<id_ligne>": {slug_champ: valeur, ...}, ...}.
    Écriture atomique (fichier temporaire + remplacement) pour ne jamais
    laisser un fichier à moitié écrit lisible par un tiers."""
    donnees = {}
    for enr in table.enregistrements.all().only("id", "valeurs_json", "matricule", "code_acces_client"):
        ligne = dict(enr.valeurs_json or {})
        if enr.matricule:
            ligne["matricule"] = enr.matricule
        if enr.code_acces_client:
            ligne["code_acces_client"] = enr.code_acces_client
        donnees[str(enr.id)] = ligne

    chemin = chemin_fichier(table)
    chemin_tmp = chemin + ".tmp"
    with open(chemin_tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2, default=str)
    os.replace(chemin_tmp, chemin)


def _patch_ligne_fichier(table, enregistrement) -> None:
    """Met à jour UNE SEULE ligne dans le fichier JSON sans relire la table.
    Si le fichier n'existe pas encore, fall-back sur synchroniser_fichier().
    Écriture atomique identique à synchroniser_fichier()."""
    chemin = chemin_fichier(table)
    if not os.path.exists(chemin):
        synchroniser_fichier(table)
        return

    with open(chemin, encoding="utf-8") as f:
        donnees = json.load(f)

    ligne = dict(enregistrement.valeurs_json or {})
    if enregistrement.matricule:
        ligne["matricule"] = enregistrement.matricule
    if enregistrement.code_acces_client:
        ligne["code_acces_client"] = enregistrement.code_acces_client
    donnees[str(enregistrement.id)] = ligne

    chemin_tmp = chemin + ".tmp"
    with open(chemin_tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2, default=str)
    os.replace(chemin_tmp, chemin)


def ecrire_valeurs(enregistrement, valeurs: dict) -> None:
    """Enregistre les valeurs d'une ligne NoSQL (création ou modification)
    et met à jour la ligne correspondante dans le fichier JSON (O(1) si le
    fichier existe déjà, O(n) uniquement à la création initiale du fichier)."""
    enregistrement.valeurs_json = valeurs
    # maj_le (auto_now) DOIT faire partie de update_fields : Django ne le met à jour que s'il y figure. Sans lui,
    # metadata.empreinte_donnees (nombre de lignes + max(maj_le)) ne change pas après l'édition d'une cellule
    # NoSQL — les KPI et widgets BI en cache restaient « à jour » avec d'anciennes valeurs (côté SQL, c'est le
    # signal toucher_maj_ligne_sur_valeur d'apps.py qui s'en charge).
    enregistrement.save(update_fields=["valeurs_json", "maj_le"])
    _patch_ligne_fichier(enregistrement.table, enregistrement)


def ecrire_valeur_calculee(enregistrement, champ, valeur) -> None:
    """Remplacement direct de
    `ValeurChamp.objects.update_or_create(enregistrement=.., champ=.., defaults={"valeur": ..})`
    pour UN champ calculé (formule de champ/ligne — calc_champ.py — ou champ
    "formule"/"constante" — calculs.py) sur une ligne NoSQL : range `valeur`
    sous la clé du champ dans valeurs_json et met à jour la ligne dans le
    fichier JSON de la table. Les modules appelants n'ont pas besoin de
    savoir comment la table stocke ses données — ils appellent ceci pour
    une table NoSQL, l'écriture ValeurChamp habituelle pour une table SQL."""
    valeurs = dict(enregistrement.valeurs_json or {})
    valeurs[champ.slug] = valeur
    ecrire_valeurs(enregistrement, valeurs)


def apres_suppression(table) -> None:
    """À appeler après suppression d'une ou plusieurs lignes d'une table
    NoSQL, pour que le fichier JSON reflète immédiatement l'état actuel.
    Resynchronisation complète nécessaire ici (les clés supprimées doivent
    disparaître du fichier)."""
    synchroniser_fichier(table)


def coercer_valeur(champ, valeur):
    """Convertit une valeur reçue du formulaire/API vers un type JSON natif
    adapté au type de champ (même logique que ValeurChamp.valeur_typee(),
    mais dans le sens écriture — la valeur est déjà en Python natif ici,
    on l'uniformise juste plutôt que de tout aplatir en texte comme le fait
    l'EAV)."""
    from .models import ChampDynamique
    if valeur is None:
        return None
    t = champ.type_champ
    try:
        if t == "nombre":
            return int(valeur)
        if t in ("decimal", "pourcentage", "constante", "monnaie"):
            return float(valeur)
        if t == "note":
            return int(valeur)
        if t in ChampDynamique.TYPES_BOOLEEN:
            if isinstance(valeur, bool):
                return valeur
            return str(valeur).strip().lower() in ("1", "true", "vrai", "on")
        if t == "barre_progression":
            return max(0, min(100, int(float(valeur))))
        if t == "choix_multiple":
            if isinstance(valeur, list):
                return valeur
            return [v for v in str(valeur).split(",") if v]
    except (TypeError, ValueError):
        return valeur
    if isinstance(valeur, str):
        return valeur.strip()
    return valeur


def inserer_lignes_dataframe(table, champs, dataframe, colonnes, nb_lignes, prog_cb=None):
    """Import en masse (fichier CSV/Excel ou clone d'une base externe, y
    compris MongoDB) vers une table NoSQL : bulk_create des Enregistrement
    (sans ValeurChamp) avec leur valeurs_json, puis UNE resynchronisation
    finale du fichier JSON (pas une par ligne, pour rester rapide sur de
    gros volumes)."""
    from .models import Enregistrement  # noqa: E402 (import tardif, évite tout cycle)
    enregistrements = [Enregistrement(table=table) for _ in range(nb_lignes)]
    Enregistrement.objects.bulk_create(enregistrements)
    enregistrements = list(table.enregistrements.order_by("id"))

    for i, (_, ligne) in enumerate(dataframe.iterrows()):
        enr = enregistrements[i]
        valeurs = {}
        for champ, colonne in zip(champs, colonnes):
            valeurs[champ.slug] = coercer_valeur(champ, ligne[colonne])
        enr.valeurs_json = valeurs
    Enregistrement.objects.bulk_update(enregistrements, ["valeurs_json"], batch_size=2000)

    if prog_cb:
        try:
            prog_cb(100)
        except Exception:
            pass
    synchroniser_fichier(table)
