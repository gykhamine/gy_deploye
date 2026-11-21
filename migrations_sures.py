#!/usr/bin/env python3
"""Applique les migrations en garantissant qu'elles ne détruisent rien de ce qui existe (multi-entreprises).

    python3 migrations_sures.py                      # analyse, sauvegarde, migre, vérifie
    python3 migrations_sures.py --plan               # montre seulement ce qui serait fait (rien n'est modifié)
    python3 migrations_sures.py --autoriser-destructif   # décision humaine : applique aussi ce qui est bloqué
    python3 migrations_sures.py --sans-sauvegarde    # n'écrit pas la copie « avant_migration »

Appelé automatiquement par installer_dependances.py à chaque mise à jour (GitHub, dossier ou zip).
Valable pour les trois modes de base : sqlite, sqlite_par_entreprise (la base système ET chaque fichier d'entreprise sont analysés)
et postgresql.

Étapes (dans l'ordre ; tant que l'étape 1 bloque, AUCUNE donnée n'est touchée) :
  1. Analyse des migrations en attente, sans rien écrire. BLOQUÉ : suppression de colonne ou de table, SQL brut, code Python sans
     annulation prévue, type de colonne réduit (texte plus court, nombre plus étroit, NULL interdit). Le reste est appliqué
     (ajout de table, de colonne, d'index ; élargissement ; renommage, qui conserve les données) avec un avertissement.
  2. Copie « avant_migration » des BASES seules (les migrations ne touchent jamais media/ : inutile de le copier, quota oblige).
     Avant de copier ou de migrer, la place est vérifiée (quota GYPLATFORM_QUOTA_MO, 512 sur PythonAnywhere gratuit ; 0 = pas de contrôle) :
     copie + reconstruction de table par SQLite + marge. Insuffisant : rien n'est touché. Seule la dernière copie est gardée.
     Si la copie échoue, on ne migre pas. (Bases non SQLite : sauvegarde complète d'engine/sauvegarde_plateforme.py.)
  3. Migration (migrate, ou migrer_entreprises en mode sqlite_par_entreprise).
  4. Contrôle : nombre de lignes de chaque table, base par base, avant et après. Une baisse est signalée en ALERTE.

Une base sans aucune migration appliquée est une installation neuve : rien à protéger, tout est appliqué sans blocage.
Codes de sortie : 0 = ok · 1 = erreur · 2 = migrations bloquées (rien n'a été touché) · 3 = place insuffisante ou copie impossible · 4 = lignes en baisse.
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent
os.chdir(RACINE)
sys.path.insert(0, str(RACINE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gyplatform.settings")

# Élargissements sans perte : le changement de type n'est pas bloqué.
TYPES_SURS = {("CharField", "TextField"), ("SmallIntegerField", "IntegerField"), ("SmallIntegerField", "BigIntegerField"),
              ("IntegerField", "BigIntegerField"), ("AutoField", "BigAutoField"), ("PositiveSmallIntegerField", "PositiveIntegerField")}
IGNORES = ("django_", "auth_permission")   # tables techniques dont le contenu varie légitimement (sessions, types de contenu…)


MARGE_MO = 20
MO = 1_048_576


def utilise_mo():
    """Espace occupé dans le quota : dossier personnel + projet s'il est ailleurs."""
    racines = [Path.home().resolve()]
    if not any(r == RACINE or r in RACINE.parents for r in racines):
        racines.append(RACINE)
    total = 0
    for r in racines:
        for d, _, fichiers in os.walk(r):
            for f in fichiers:
                try:
                    total += os.lstat(os.path.join(d, f)).st_size
                except OSError:
                    pass
    return total / MO


def dossier_copies():
    from django.conf import settings
    return Path(getattr(settings, "GYPLATFORM_SAUVEGARDES", RACINE / "sauvegardes"))


def taille_dossier(d):
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / MO if d.is_dir() else 0


def bases_sqlite(liste, proteges):
    """[(alias, fichier)] des bases SQLite à protéger."""
    from django.db import connections
    return [(a, Path(connections[a].settings_dict["NAME"])) for a, _ in liste
            if a in proteges and connections[a].vendor == "sqlite" and Path(connections[a].settings_dict["NAME"]).is_file()]


def verifier_place(liste, proteges, avec_copie):
    """Refuse (code 3) AVANT toute écriture si le quota ne laisse pas la place : copie + reconstruction d'une table + marge."""
    quota = float(os.environ.get("GYPLATFORM_QUOTA_MO", "0") or 0)
    if quota <= 0:
        return
    tailles = [f.stat().st_size / MO for _, f in bases_sqlite(liste, proteges)]
    anciennes = sum(taille_dossier(d) for d in dossier_copies().glob("avant_migration_*"))   # supprimées avant la nouvelle copie
    besoin = (sum(tailles) if avec_copie else 0) + (max(tailles) if tailles else 0) + MARGE_MO
    libre = quota - utilise_mo() + (anciennes if avec_copie else 0)
    if libre < besoin:
        print(f"Place insuffisante : {libre:.0f} Mo libres sur {quota:.0f} Mo, {besoin:.0f} Mo nécessaires (copie des bases, reconstruction de table, marge). "
              "Les migrations ne sont PAS appliquées ; libérez de la place (sauvegardes/, logs/, ../agentgy_sauvegardes_maj).")
        sys.exit(3)


def copier_bases(liste, proteges):
    """Copie à chaud (API de sauvegarde SQLite, cohérente même en mode WAL) des bases dans sauvegardes/avant_migration_<date>/."""
    anciennes = sorted(dossier_copies().glob("avant_migration_*"))
    for d in anciennes:   # une seule copie gardée : celle d'avant CETTE migration
        shutil.rmtree(d, ignore_errors=True)
    dossier = dossier_copies() / f"avant_migration_{time.strftime('%Y%m%d_%H%M%S')}"
    dossier.mkdir(parents=True, exist_ok=True)
    for alias, fichier in bases_sqlite(liste, proteges):
        source, copie = sqlite3.connect(str(fichier)), sqlite3.connect(str(dossier / (re.sub(r"[^\w.-]", "_", alias) + ".sqlite3")))
        try:
            source.backup(copie)
        finally:
            copie.close()
            source.close()
    return dossier


def bases():
    """[(alias, libellé)] : la base principale + chaque fichier d'entreprise existant (mode sqlite_par_entreprise)."""
    from django.db import Error
    from engine import base_par_entreprise as bpe
    res = [("default", "base principale")]
    if bpe.actif():
        try:
            from engine.models import Entreprise
            ids = list(Entreprise.objects.using("default").order_by("id").values_list("pk", flat=True))
        except Error:   # base principale encore vide : pas d'entreprise à protéger
            ids = []
        res += [(bpe.enregistrer(i), f"entreprise {i}") for i in ids if bpe.chemin(i).is_file()]
    return res


def en_attente(alias):
    """(executeur, [migrations en attente] ou None si la base est neuve)."""
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor
    ex = MigrationExecutor(connections[alias])
    if not ex.loader.applied_migrations:
        return ex, None
    return ex, [m for m, retour in ex.migration_plan(ex.loader.graph.leaf_nodes()) if not retour]


def risques(ex, migration):
    """(bloquants, avertissements) d'une migration, d'après ses opérations et l'état du schéma AVANT elle."""
    from django.db.migrations import operations as op
    avant = ex.loader.project_state((migration.app_label, migration.name), at_end=False)
    bloquants, avert = [], []
    for o in migration.operations:
        nom, cible = type(o).__name__, getattr(o, "name", "") or getattr(o, "old_name", "")
        modele = getattr(o, "model_name", "") or cible
        if isinstance(o, op.RemoveField):
            bloquants.append(f"RemoveField {modele}.{cible} : supprime une colonne et ses données")
        elif isinstance(o, op.DeleteModel):
            bloquants.append(f"DeleteModel {cible} : supprime une table et ses données")
        elif isinstance(o, op.RunSQL):
            bloquants.append("RunSQL : SQL brut, effet impossible à vérifier")
        elif isinstance(o, op.RunPython):
            (avert if o.reverse_code is not None else bloquants).append(
                "RunPython" + (" (annulable)" if o.reverse_code is not None else " sans annulation prévue (reverse_code) : peut modifier des données")
            )
        elif isinstance(o, op.AlterField):
            ancien = (avant.models.get((migration.app_label, o.model_name_lower)) or None)
            ancien = ancien.fields.get(o.name) if ancien else None
            nouveau = o.field
            if ancien is None:
                avert.append(f"AlterField {modele}.{cible} : état précédent introuvable")
                continue
            t0, t1 = ancien.get_internal_type(), nouveau.get_internal_type()
            if t0 != t1 and (t0, t1) not in TYPES_SURS:
                bloquants.append(f"AlterField {modele}.{cible} : type {t0} → {t1}")
            if (getattr(ancien, "max_length", None) or 0) > (getattr(nouveau, "max_length", None) or 10 ** 9):
                bloquants.append(f"AlterField {modele}.{cible} : longueur réduite ({ancien.max_length} → {nouveau.max_length})")
            if ancien.null and not nouveau.null:
                bloquants.append(f"AlterField {modele}.{cible} : NULL désormais interdit")
            if (getattr(ancien, "max_digits", 0) or 0) > (getattr(nouveau, "max_digits", 10 ** 9) or 10 ** 9) or \
                    (getattr(ancien, "decimal_places", 0) or 0) > (getattr(nouveau, "decimal_places", 10 ** 9) or 10 ** 9):
                bloquants.append(f"AlterField {modele}.{cible} : précision décimale réduite")
            if not ancien.unique and nouveau.unique:
                avert.append(f"AlterField {modele}.{cible} : devient unique (échoue, sans rien changer, si des doublons existent)")
        elif isinstance(o, op.AddField) and o.field.unique:
            avert.append(f"AddField {modele}.{cible} : unique (échoue, sans rien changer, si la table contient déjà plusieurs lignes)")
        elif isinstance(o, (op.RenameField, op.RenameModel, op.AlterModelTable)):
            avert.append(f"{nom} {modele or cible} : renommage (les données sont conservées)")
        elif isinstance(o, (op.AlterUniqueTogether, op.AlterIndexTogether, op.AddConstraint)):
            avert.append(f"{nom} {modele or cible} : échoue, sans rien changer, si les données existantes ne respectent pas la règle")
        elif isinstance(o, op.SeparateDatabaseAndState):
            avert.append("SeparateDatabaseAndState : à relire")
    if not migration.atomic:
        avert.append("migration non atomique : un échec en cours de route ne s'annule pas tout seul")
    return bloquants, avert


def comptes(alias):
    """{table: nombre de lignes} d'une base."""
    from django.conf import settings
    from django.db import connections
    c = connections[alias]
    caches = {str(v.get("LOCATION", "")) for v in getattr(settings, "CACHES", {}).values()}
    res = {}
    with c.cursor() as cur:
        for t in c.introspection.table_names(cur):
            if t.startswith(IGNORES) or t in caches:
                continue
            cur.execute(f"SELECT COUNT(*) FROM {c.ops.quote_name(t)}")
            res[t] = cur.fetchone()[0]
    return res


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--plan", action="store_true", help="analyse seulement : rien n'est modifié")
    p.add_argument("--autoriser-destructif", action="store_true", help="applique aussi les migrations bloquées")
    p.add_argument("--sans-sauvegarde", action="store_true", help="pas de copie avant_migration")
    a = p.parse_args()

    import django
    django.setup()
    from django.core.management import call_command
    from engine import base_par_entreprise as bpe

    # 1. Analyse (lecture seule)
    liste = bases()
    a_proteger, bloques, avertis, neuves = [], [], [], []
    for alias, libelle in liste:
        ex, attente = en_attente(alias)
        if attente is None:
            neuves.append(libelle)
            continue
        if not attente:
            continue
        a_proteger.append(alias)
        for m in attente:
            b, w = risques(ex, m)
            bloques += [f"{m.app_label}.{m.name} — {x}" for x in b]
            avertis += [f"{m.app_label}.{m.name} — {x}" for x in w]
    bloques, avertis = list(dict.fromkeys(bloques)), list(dict.fromkeys(avertis))   # les fichiers d'entreprise partagent les mêmes migrations

    if neuves:
        print(f"Base(s) neuve(s), rien à protéger : {', '.join(neuves)}")
    if a_proteger:
        print(f"Migrations en attente sur : {', '.join(l for al, l in liste if al in a_proteger)}")
    for x in avertis:
        print("  avertissement :", x)
    if bloques:
        print("\nBLOQUÉ (risque pour les données) :")
        for x in bloques:
            print("  ✗", x)
        if not a.autoriser_destructif:
            print("\nAucune donnée n'a été touchée. Relisez ces migrations ; pour les appliquer malgré tout (après vérification humaine) :\n"
                  "  python3 migrations_sures.py --autoriser-destructif")
            sys.exit(2)
        print("\n--autoriser-destructif : appliqué sous votre responsabilité.")
    if a.plan:
        return print("\nMode --plan : rien n'a été modifié.")

    # 2. Place suffisante ? puis copie des bases avant de migrer (seulement s'il y a des données à protéger)
    verifier_place(liste, a_proteger, avec_copie=bool(a_proteger) and not a.sans_sauvegarde)
    if a_proteger and not a.sans_sauvegarde:
        try:
            from django.db import connections
            if all(connections[x].vendor == "sqlite" for x in a_proteger):
                print("Copie des bases avant migration :", copier_bases(liste, a_proteger))
            else:
                from engine import sauvegarde_plateforme
                print("Copie avant migration :", sauvegarde_plateforme.creer(prefixe="avant_migration"))
        except Exception as exc:
            print(f"Copie impossible ({exc}) : les migrations ne sont PAS appliquées.")
            sys.exit(3)

    # 3. Comptes avant, migration
    avant = {alias: comptes(alias) for alias, _ in liste if alias in a_proteger}
    if bpe.actif():
        call_command("migrer_entreprises")
    else:
        call_command("migrate", interactive=False, verbosity=1)

    # 4. Contrôle : aucune table ne doit avoir perdu de lignes
    baisses = []
    for alias, libelle in liste:
        if alias not in avant:
            continue
        apres = comptes(alias)
        for table, n in avant[alias].items():
            if apres.get(table, 0) < n:
                baisses.append(f"[{libelle}] {table} : {n} → {apres.get(table, 'table absente')}")
    if baisses:
        print("\nALERTE — des lignes ont disparu pendant la migration :")
        for x in baisses:
            print("  !", x)
        print("La copie « avant_migration » est dans le dossier sauvegardes/ (voir plus haut) : ne rien relancer avant de l'avoir examinée.")
        sys.exit(4)
    print("Migrations appliquées ; aucune table n'a perdu de lignes." if a_proteger else "Migrations appliquées.")


if __name__ == "__main__":
    main()
