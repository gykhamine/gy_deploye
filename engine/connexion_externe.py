"""
Connexion aux bases de données EXTERNES (hors de la base de l'application
elle-même) : lister les tables/collections distantes, en prévisualiser le
contenu, et importer leurs données vers une ou plusieurs TableDynamique —
via exactement le même moteur de création/découpage que import_fichier.py
(voir _importer_dataframe_vers_tables), pour qu'un import « base externe »
se comporte à l'identique d'un import de fichier CSV/Excel : jamais de rejet
pour volume trop important, toujours un découpage tracé si nécessaire (voir
GroupeTables et TableDynamique.partie_precedente dans models.py).

Pilotes utilisés, chacun strictement optionnel — ImportError explicite et
directement affichable à l'utilisateur si le paquet n'est pas installé sur
le serveur :
  - PostgreSQL   : psycopg2 (ou psycopg2-binary)
  - MySQL/MariaDB: mysql-connector-python (ou PyMySQL en repli)
  - SQL Server   : pyodbc
  - Oracle       : oracledb (ou cx_Oracle en repli)
  - MongoDB      : pymongo

Toute la configuration (hôte/IP, port, base, identifiants, options avancées)
vit sur le modèle ConnexionExterne — c'est le SEUL endroit où elle est
saisie ; ce module ne fait que s'en servir pour ouvrir une connexion.
"""
from engine.i18n import tf
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
import pandas as pd

import logging
import time

from django.conf import settings
from django.db import transaction

from .import_fichier import (
    _importer_dataframe_vers_tables, _colonnes_avec_types, _creer_champs_pour_table,
    _inserer_lignes_pandas, _fenetre_progression, _slug_table_unique, _emettre_dataset_importe,
)
from .models import ConnexionExterne
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

# Garde-fous de volume (une table distante peut peser des milliers comme des millions de lignes) :
#  - jamais plus d'UNE partie (≤ seuil) en mémoire à la fois : la source est lue en flux, par lots ;
#  - seuil = ConfigurationIA.limite_lignes (même règle que l'import CSV/Excel) ; « illimité » (0) ou un seuil démesuré
#    sont ramenés à PARTIE_EXTERNE_MAX, car une table distante n'est JAMAIS chargée d'un bloc ;
#  - une transaction PAR partie (pas une transaction géante sur dix ans de données) ; en cas d'échec, tout le groupe créé
#    est retiré : jamais de groupe incomplet visible ;
#  - pause réglable entre deux parties (settings.GY_IMPORT_EXTERNE_PAUSE, secondes) : latence contrôlée, source ménagée.
PARTIE_EXTERNE_MAX = 200_000


class ErreurConnexionExterne(Exception):
    """Erreur de connexion, d'authentification ou de requête vers une base
    de données externe — toujours porteuse d'un message directement
    affichable à l'utilisateur (jamais la trace technique brute du pilote)."""


def _pilote_manquant(paquet: str, moteur: str) -> ErreurConnexionExterne:
    return ErreurConnexionExterne(
        tf("engine/connexion_externe.py:g:38:9", v1=paquet, v2=moteur, v3=paquet)
    )


# ── Connexion brute par moteur ───────────────────────────────────────────────

def _connexion_sql(connexion: ConnexionExterne):
    """Ouvre une connexion DB-API 2.0 vers le moteur SQL choisi, à partir
    des réglages (hôte/IP, port, base, utilisateur, mot de passe, SSL,
    paramètres avancés) enregistrés sur la ConnexionExterne."""
    moteur = connexion.moteur
    hote, port = connexion.hote, connexion.port_effectif
    options = dict(connexion.parametres_supplementaires or {})

    if moteur == ConnexionExterne.MOTEUR_POSTGRESQL:
        try:
            import psycopg2
        except ImportError:
            raise _pilote_manquant("psycopg2-binary", "PostgreSQL")
        try:
            return psycopg2.connect(
                host=hote, port=port, dbname=connexion.nom_base,
                user=connexion.utilisateur or None, password=connexion.mot_de_passe or None,
                sslmode=options.pop("sslmode", "require" if connexion.ssl_actif else "prefer"),
                **options,
            )
        except Exception as exc:
            raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:66:42", v1=exc)) from exc

    if moteur == ConnexionExterne.MOTEUR_MYSQL:
        try:
            import mysql.connector as pilote
            connecter = lambda **kw: pilote.connect(**kw)  # noqa: E731
        except ImportError:
            try:
                import pymysql as pilote
                connecter = lambda **kw: pilote.connect(**kw)  # noqa: E731
            except ImportError:
                raise _pilote_manquant("mysql-connector-python", "MySQL/MariaDB")
        try:
            return connecter(
                host=hote, port=port, database=connexion.nom_base,
                user=connexion.utilisateur or None, password=connexion.mot_de_passe or None,
                **options,
            )
        except Exception as exc:
            raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:85:42", v1=exc)) from exc

    if moteur == ConnexionExterne.MOTEUR_MSSQL:
        try:
            import pyodbc
        except ImportError:
            raise _pilote_manquant("pyodbc", "SQL Server")
        driver = options.pop("driver", "{ODBC Driver 18 for SQL Server}")
        chaine = (
            f"DRIVER={driver};SERVER={hote},{port};DATABASE={connexion.nom_base};"
            f"UID={connexion.utilisateur};PWD={connexion.mot_de_passe};"
            f"Encrypt={'yes' if connexion.ssl_actif else 'no'};TrustServerCertificate=yes;"
        )
        try:
            return pyodbc.connect(chaine, **options)
        except Exception as exc:
            raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:101:42", v1=exc)) from exc

    if moteur == ConnexionExterne.MOTEUR_ORACLE:
        try:
            import oracledb
        except ImportError:
            raise _pilote_manquant("oracledb", "Oracle")
        try:
            service_name = options.pop("service_name", connexion.nom_base)
            dsn = oracledb.makedsn(hote, port, service_name=service_name)
            return oracledb.connect(
                user=connexion.utilisateur, password=connexion.mot_de_passe, dsn=dsn, **options,
            )
        except Exception as exc:
            raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:115:42", v1=exc)) from exc

    raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:117:34", v1=moteur))


def _client_mongo(connexion: ConnexionExterne):
    try:
        import pymongo
    except ImportError:
        raise _pilote_manquant("pymongo", "MongoDB")
    options = dict(connexion.parametres_supplementaires or {})
    try:
        if connexion.utilisateur:
            uri = (
                f"mongodb://{connexion.utilisateur}:{connexion.mot_de_passe}"
                f"@{connexion.hote}:{connexion.port_effectif}/{connexion.nom_base}"
            )
        else:
            uri = f"mongodb://{connexion.hote}:{connexion.port_effectif}/{connexion.nom_base}"
        client = pymongo.MongoClient(
            uri, tls=connexion.ssl_actif, serverSelectionTimeoutMS=8000, **options,
        )
        client.admin.command("ping")
        return client
    except Exception as exc:
        raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:140:38", v1=exc)) from exc


# ── API publique ──────────────────────────────────────────────────────────

def tester_connexion(connexion: ConnexionExterne) -> None:
    """Tente une connexion réelle vers le serveur (IP/hôte + port
    configurés) ; ne renvoie rien si tout va bien, lève
    ErreurConnexionExterne (message directement affichable) sinon."""
    if connexion.est_mongodb:
        _client_mongo(connexion).close()
    else:
        _connexion_sql(connexion).close()


def lister_tables_distantes(connexion: ConnexionExterne) -> list:
    """Liste les tables/vues (SQL) ou collections (MongoDB) accessibles sur
    la base distante, pour que l'utilisateur choisisse quoi importer."""
    if connexion.est_mongodb:
        client = _client_mongo(connexion)
        try:
            return sorted(client[connexion.nom_base].list_collection_names())
        finally:
            client.close()

    conn = _connexion_sql(connexion)
    try:
        requetes = {
            ConnexionExterne.MOTEUR_POSTGRESQL:
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name",
            ConnexionExterne.MOTEUR_MYSQL:
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() ORDER BY table_name",
            ConnexionExterne.MOTEUR_MSSQL:
                "SELECT table_name FROM information_schema.tables ORDER BY table_name",
            ConnexionExterne.MOTEUR_ORACLE:
                "SELECT table_name FROM user_tables ORDER BY table_name",
        }
        cur = conn.cursor()
        cur.execute(requetes[connexion.moteur])
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _lire_dataframe_distant(connexion: ConnexionExterne, nom_table_ou_collection: str, limite: int = None) -> pd.DataFrame:
    """Récupère TOUTES les lignes de la table/collection distante sous
    forme de DataFrame texte (même convention que import_fichier.py :
    tout est ramené en chaînes, l'inférence de type EAV se fait ensuite)."""
    if connexion.est_mongodb:
        client = _client_mongo(connexion)
        try:
            curseur = client[connexion.nom_base][nom_table_ou_collection].find({})
            documents = list(curseur.limit(limite) if limite else curseur)
            for doc in documents:
                doc.pop("_id", None)  # ObjectId non pertinent pour l'EAV, exclu
            from .moteur_donnees import dataframe_depuis
            dataframe = dataframe_depuis(documents)
        finally:
            client.close()
    else:
        # Le nom vient du navigateur : jamais interpolé dans le SQL sans être
        # l'une des tables réellement listées par le serveur distant.
        if nom_table_ou_collection not in lister_tables_distantes(connexion):
            raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:204:42", v1=nom_table_ou_collection))
        requete = f"SELECT * FROM {nom_table_ou_collection}"
        if limite:
            n = int(limite)
            requete = {
                ConnexionExterne.MOTEUR_MSSQL: f"SELECT TOP {n} * FROM {nom_table_ou_collection}",
                ConnexionExterne.MOTEUR_ORACLE: f"{requete} FETCH FIRST {n} ROWS ONLY",
            }.get(connexion.moteur, f"{requete} LIMIT {n}")
        conn = _connexion_sql(connexion)
        try:
            dataframe = pd.read_sql(requete, conn)
        finally:
            conn.close()
    return dataframe.astype(str).where(dataframe.notna(), "")


def apercu_table_distante(connexion: ConnexionExterne, nom_table_ou_collection: str, limite: int = 50):
    """Renvoie (colonnes, lignes) — lignes = liste de listes JSON-safe —
    pour un aperçu rapide avant de lancer l'import complet."""
    dataframe = _lire_dataframe_distant(connexion, nom_table_ou_collection, limite)
    return list(dataframe.columns), dataframe.values.tolist()


def _taille_partie(entreprise) -> int:
    """Seuil de découpage : celui de l'entreprise (comme pour un fichier), borné par PARTIE_EXTERNE_MAX."""
    from .models import TableDynamique
    return min(TableDynamique.plafond_pour_entreprise(entreprise), PARTIE_EXTERNE_MAX)   # plafond absolu (100 000) respecté aussi pour une base distante


def _citer(connexion, nom: str) -> str:
    """Identifiant cité selon le moteur (le nom a déjà été validé contre la liste réelle des tables distantes)."""
    if connexion.moteur == ConnexionExterne.MOTEUR_MYSQL:
        return "`" + nom.replace("`", "``") + "`"
    if connexion.moteur == ConnexionExterne.MOTEUR_MSSQL:
        return "[" + nom.replace("]", "]]") + "]"
    return '"' + nom.replace('"', '""') + '"'


def compter_lignes_distantes(connexion: ConnexionExterne, nom: str):
    """Nombre de lignes de la table distante (progression et nombre de parties) ; None si le serveur ne le donne pas."""
    try:
        if connexion.est_mongodb:
            client = _client_mongo(connexion)
            try:
                return int(client[connexion.nom_base][nom].estimated_document_count())
            finally:
                client.close()
        conn = _connexion_sql(connexion)
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {_citer(connexion, nom)}")
            return int(cur.fetchone()[0])
        finally:
            conn.close()
    except Exception:
        logger.warning(_gt("rg6.msg.003"), nom, exc_info=True)
        return None


def _en_texte(dataframe: pd.DataFrame) -> pd.DataFrame:
    return dataframe.astype(str).where(dataframe.notna(), "")


def _lots_distants(connexion: ConnexionExterne, nom: str, taille: int):
    """Générateur : lit la table distante EN FLUX et rend des DataFrame texte d'au plus `taille` lignes. Curseur serveur
    (PostgreSQL) ou non tamponné (MySQL), `fetchmany` ailleurs : la table n'est jamais chargée en entier."""
    if connexion.est_mongodb:
        client = _client_mongo(connexion)
        try:
            from .moteur_donnees import dataframe_depuis
            lot = []
            for doc in client[connexion.nom_base][nom].find({}, batch_size=min(taille, 5000)):
                doc.pop("_id", None)
                lot.append(doc)
                if len(lot) >= taille:
                    yield _en_texte(dataframe_depuis(lot))
                    lot = []
            if lot:
                yield _en_texte(dataframe_depuis(lot))
        finally:
            client.close()
        return

    if nom not in lister_tables_distantes(connexion):
        raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:204:42", v1=nom))
    conn = _connexion_sql(connexion)
    try:
        requete = f"SELECT * FROM {_citer(connexion, nom)}"
        try:
            if connexion.moteur == ConnexionExterne.MOTEUR_POSTGRESQL:
                cur = conn.cursor(name="gy_import_externe")     # curseur côté serveur : pas de chargement complet côté client
                cur.itersize = min(taille, 10000)
            elif type(conn).__module__.startswith("pymysql"):
                import pymysql.cursors
                cur = conn.cursor(pymysql.cursors.SSCursor)      # non tamponné
            else:
                cur = conn.cursor()
            cur.execute(requete)
            pas = min(taille, 10000)
            cur.arraysize = pas
            colonnes = None
            lot = []
            while True:
                lignes = cur.fetchmany(pas)
                if not lignes:
                    break
                if colonnes is None:
                    colonnes = [d[0] for d in cur.description]
                lot.extend(tuple(l) for l in lignes)
                while len(lot) >= taille:
                    yield _en_texte(pd.DataFrame(lot[:taille], columns=colonnes))
                    lot = lot[taille:]
            if lot:
                yield _en_texte(pd.DataFrame(lot, columns=colonnes))
        except ErreurConnexionExterne:
            raise
        except GeneratorExit:
            raise
        except Exception as exc:
            raise ErreurConnexionExterne(_i18n_tf("rg2.connexion_externe.1", v1=nom, v2=exc)) from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _retirer_groupe_partiel(groupe, tables):
    """Échec en cours d'import : retire les parties déjà écrites (et leurs fichiers Parquet) puis le groupe."""
    from . import parquet_store
    for t in reversed(tables):
        try:
            parquet_store.supprimer_fichier(t)
            t.delete()
        except Exception:
            logger.exception(_gt("rg6.msg.004"), getattr(t, "pk", "?"))
    try:
        if groupe:
            groupe.delete()
    except Exception:
        logger.exception(_gt("rg6.msg.005"))


def importer_table_distante_vers_table(
    connexion: ConnexionExterne,
    nom_table_ou_collection: str,
    entreprise,
    progression_cb=None,
):
    """Importe la table/collection distante comme un fichier CSV/Excel de grande taille : lecture EN FLUX, une
    TableDynamique (donc un fichier Parquet) par tranche de `seuil` lignes, rattachées par un GroupeTables et chaînées
    (partie_precedente/partie_suivante) ; une seule table si tout tient dans le seuil. Voir les garde-fous en tête de module.
    La valeur de retour est la PREMIÈRE partie (`table.groupe` donne l'ensemble)."""
    from . import securite_dates
    from .models import GroupeTables, TableDynamique

    def _prog(pct):
        if progression_cb:
            try:
                progression_cb(int(pct))
            except Exception:
                pass

    securite_dates.controler(entreprise.id)   # horloge revenue en arrière : refus AVANT d'écrire la moindre ligne
    _prog(2)
    taille = _taille_partie(entreprise)
    total = compter_lignes_distantes(connexion, nom_table_ou_collection)
    pause = float(getattr(settings, "GY_IMPORT_EXTERNE_PAUSE", 0.2) or 0)
    _prog(4)

    nom_base = nom_table_ou_collection[:120]
    type_source = "collection" if connexion.est_mongodb else "table"
    description_base = tf("engine/connexion_externe.py:g:258:9", v1=connexion.nom, v2=connexion.get_moteur_display(), v3=connexion.hote, v4=connexion.port_effectif, v5=type_source, v6=nom_table_ou_collection)
    type_stockage = "nosql" if connexion.est_mongodb else "sql"   # MongoDB : stockage JSON (voir nosql_store.py)
    champs_communs = dict(
        provient_fichier=False, nom_fichier_origine="",
        connexion_externe_origine=connexion, nom_source_distante=nom_table_ou_collection,
    )

    lots = _lots_distants(connexion, nom_table_ou_collection, taille)
    groupe, creees, lignes_total = None, [], 0
    try:
        premier = next(lots, None)
        if premier is None or premier.empty or len(premier.columns) == 0:
            raise ErreurConnexionExterne(tf("engine/connexion_externe.py:g:252:13", v1=nom_table_ou_collection))
        colonnes = list(premier.columns)
        suivant = next(lots, None)

        if suivant is None:   # tout tient dans le seuil : une seule table, comportement identique à un petit fichier
            table = _importer_dataframe_vers_tables(
                entreprise=entreprise, nom_base=nom_base, dataframe=premier, nb_lignes=len(premier), colonnes=colonnes,
                description_base=description_base, progression_cb=_prog, type_stockage=type_stockage, **champs_communs,
            )
            lignes_total = len(premier)
        else:
            infos = _colonnes_avec_types(colonnes, premier.head(5000))   # types déduits de la 1re tranche, valables pour toutes
            absolu = taille >= TableDynamique.plafond_absolu_lignes(entreprise)   # plafond absolu de l'entreprise (100 000 par défaut) : tables INDÉPENDANTES (pas de groupe), renommables
            groupe = None if absolu else GroupeTables.objects.create(
                entreprise=entreprise, nom=nom_base, cree_automatiquement=True,
                description=_i18n_tf("rg2.connexion_externe.2", v1=description_base, v2=taille),
            )
            precedente = None

            def _tranches():
                yield premier
                yield suivant
                yield from lots

            for i, lot in enumerate(_tranches(), start=1):
                lot = lot.reindex(columns=colonnes, fill_value="")   # source hétérogène (NoSQL) : schéma de la 1re tranche
                nb = len(lot)
                with transaction.atomic():   # une transaction PAR partie : jamais une transaction géante
                    nom_partie = (nom_base if i == 1 else f"{nom_base} (suite {i})")[:120] if absolu else f"{nom_base} ({i})"[:120]
                    t = TableDynamique.objects.create(
                        entreprise=entreprise, nom=nom_partie, slug=_slug_table_unique(entreprise, nom_partie),
                        description=_i18n_tf("rg2.connexion_externe.3", v1=description_base, v2=lignes_total + 1, v3=lignes_total + nb),
                        **({} if absolu else dict(groupe=groupe, partie_index=i, partie_precedente=precedente)), type_stockage=type_stockage,
                        **champs_communs,
                    )
                    creees.append(t)
                    champs = _creer_champs_pour_table(t, infos)
                    cb = _fenetre_progression(_prog, 5, 90, lignes_total, nb, total or max(lignes_total + nb, 1))
                    if type_stockage == TableDynamique.STOCKAGE_NOSQL:
                        from . import nosql_store
                        nosql_store.inserer_lignes_dataframe(t, champs, lot, colonnes, nb, cb)
                    else:
                        _inserer_lignes_pandas(t, champs, lot, colonnes, nb, cb)
                precedente = t
                lignes_total += nb
                _prog(min(5 + 90 * lignes_total / (total or lignes_total * 2), 97))
                del lot
                if pause:
                    time.sleep(pause)   # latence contrôlée : on ménage la source et la base

            nb_parties = len(creees)   # nombre RÉEL de parties (la source a pu bouger depuis le comptage)
            with transaction.atomic():
                for t in (creees if groupe else []):
                    t.partie_total = nb_parties
                    t.nom = f"{nom_base} ({t.partie_index}/{nb_parties})"[:120]
                    t.save(update_fields=["partie_total", "nom"])
                if groupe:
                    groupe.description = _i18n_tf("rg2.connexion_externe.4", v1=description_base, v2=lignes_total, v3=nb_parties, v4=taille)
                    groupe.save(update_fields=["description"])
            table = creees[0]
            _emettre_dataset_importe(table, lignes_total, nom_table_ou_collection)
    except BaseException:
        if groupe is not None or creees:
            _retirer_groupe_partiel(groupe, creees)
        raise
    finally:
        lots.close()

    _prog(100)
    from django.utils import timezone
    ConnexionExterne.objects.filter(id=connexion.id).update(
        derniere_synchro_le=timezone.now(), derniere_synchro_statut="ok", derniere_synchro_erreur="",
    )
    return table
