"""Santé du MOTEUR (page SIEM, section « Santé du moteur ») : ce que le SIEM ne surveille pas — base, caches, Redis, fichiers Parquet,
mémoire de recherche, erreurs d'exécution récentes. Le SIEM répond à « est-on attaqué ? », ici « tout tourne-t-il bien ? ».

Chaque mesure est isolée : une panne (base muette, journal illisible…) devient {"ok": False, "erreur": …} pour CETTE mesure, jamais une
exception qui vide la page. Lecture seule, sans effet de bord, sauf une clé de test (« siem:sante:ping ») écrite puis relue dans les caches.
"""
import datetime
import logging
import os
import re
import socket
import time

from django.conf import settings
from .i18n import t
from engine.i18n import tf as _siem_tf  # noqa: E402

logger = logging.getLogger(__name__)

LIGNES_ERREURS = 15              # erreurs récentes affichées
OCTETS_JOURNAL = 400 * 1024      # fin du fichier journal relue (les erreurs récentes sont à la fin)
MAX_TABLES_VERIFICATION = 300    # tables vérifiées par clic (le reste au clic suivant : la vérification relit tout le fichier de chaque table)
_ENTETE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]?\d* (ERROR|CRITICAL) (\S+) : (.*)$")


def _mesure(fonction):
    """Exécute une mesure ; en cas d'exception : {"ok": False, "erreur": …}."""
    try:
        return fonction()
    except Exception as e:
        logger.exception(_siem_tf("rg4.siem.msg.290"))
        return {"ok": False, "erreur": f"{type(e).__name__}: {e}"[:200]}


def _base():
    from django.db import connection
    debut = time.perf_counter()
    with connection.cursor() as c:
        c.execute("SELECT 1")
        c.fetchone()
    return {"ok": True, "ms": round((time.perf_counter() - debut) * 1000, 1), "moteur": connection.vendor}


def _entrees(nom_cache):
    """Nombre d'entrées d'un cache en base (None si ce n'est pas un cache en base ou si le comptage échoue)."""
    reglage = settings.CACHES.get(nom_cache, {})
    if not str(reglage.get("BACKEND", "")).endswith("DatabaseCache"):
        return None
    try:
        from django.db import connection
        with connection.cursor() as c:
            c.execute(f"SELECT COUNT(*) FROM {connection.ops.quote_name(reglage['LOCATION'])}")
            return int(c.fetchone()[0])
    except Exception:
        return None


def _caches():
    from django.core.cache import caches
    sortie = []
    for nom, reglage in settings.CACHES.items():
        element = {"nom": nom, "ok": False, "ms": None, "entrees": None, "maximum": (reglage.get("OPTIONS") or {}).get("MAX_ENTRIES")}
        try:
            cle, valeur = "siem:sante:ping", str(time.time())
            debut = time.perf_counter()
            caches[nom].set(cle, valeur, timeout=60)
            element["ok"] = caches[nom].get(cle) == valeur
            element["ms"] = round((time.perf_counter() - debut) * 1000, 1)
            element["entrees"] = _entrees(nom)
        except Exception as e:
            element["erreur"] = f"{type(e).__name__}: {e}"[:200]
        sortie.append(element)
    return sortie


def _redis():
    """Redis sert aux WebSocket (Channels). Pas de couche Redis configurée = rien à surveiller (couche mémoire, un seul processus)."""
    couche = (getattr(settings, "CHANNEL_LAYERS", {}) or {}).get("default", {})
    if "redis" not in str(couche.get("BACKEND", "")).lower():
        return {"configure": False, "ok": True}
    hotes = (couche.get("CONFIG") or {}).get("hosts") or []
    hote = hotes[0] if hotes else None
    if isinstance(hote, (list, tuple)) and len(hote) >= 2:
        adresse = (str(hote[0]), int(hote[1]))
    elif isinstance(hote, str) and re.search(r"//([^/:@]+)(?::(\d+))?", hote):
        m = re.search(r"//(?:[^@/]*@)?([^/:@]+)(?::(\d+))?", hote)
        adresse = (m.group(1), int(m.group(2) or 6379))
    else:
        return {"configure": True, "ok": False, "erreur": t("engine/siem_sante.py:g:87:60")}
    debut = time.perf_counter()
    try:
        with socket.create_connection(adresse, timeout=1.0):
            pass
        return {"configure": True, "ok": True, "hote": f"{adresse[0]}:{adresse[1]}", "ms": round((time.perf_counter() - debut) * 1000, 1)}
    except OSError as e:
        return {"configure": True, "ok": False, "hote": f"{adresse[0]}:{adresse[1]}", "erreur": f"{type(e).__name__}: {e}"[:200]}


def _parquet():
    from . import parquet_store as ps
    from .models import TableDynamique
    sortie = {"pyarrow": bool(ps.disponible()), "en_pause": bool(ps.en_pause()), "fichiers": 0, "deltas": 0, "octets": 0,
              "tables_eav": TableDynamique.objects.exclude(type_stockage=TableDynamique.STOCKAGE_NOSQL).count(), "ok": True}
    racine = os.path.join(os.path.realpath(str(settings.MEDIA_ROOT)), "parquet_tables")
    for dossier, _, noms in os.walk(racine):
        for nom in noms:
            if nom.endswith(".parquet.delta"):
                sortie["deltas"] += 1
            elif nom.endswith(".parquet"):
                sortie["fichiers"] += 1
            else:
                continue
            try:
                sortie["octets"] += os.path.getsize(os.path.join(dossier, nom))
            except OSError:
                pass
    sortie["ok"] = sortie["pyarrow"]
    return sortie


def _memoire_recherche():
    from . import parquet_recherche as pr
    with pr._VERROU:
        entrees = list(pr._CACHE.values())
    return {"tables": len(entrees), "lignes": sum(len(e[1]) for e in entrees), "maximum": pr._maximum_cache(),
            "recherche_parquet": bool(pr.actif()), "ok": True}


def lire_erreurs(chemin, maintenant=None, limite=LIGNES_ERREURS):
    """Erreurs (ERROR/CRITICAL) de la fin du fichier journal : {"fichier", "total_24h", "recentes": [{"date","niveau","source","message"}]}, plus
    récentes d'abord. Les lignes de trace (traceback) ne sont pas reprises : la première ligne du message suffit à repérer, le détail est dans le fichier."""
    sortie = {"fichier": os.path.basename(chemin or ""), "total_24h": 0, "recentes": [], "ok": True}
    if not chemin or not os.path.exists(chemin):
        return sortie
    maintenant = maintenant or datetime.datetime.now()
    with open(chemin, "rb") as f:
        f.seek(0, os.SEEK_END)
        taille = f.tell()
        f.seek(max(0, taille - OCTETS_JOURNAL))
        texte = f.read().decode("utf-8", errors="replace")
    trouvees = []
    for ligne in texte.splitlines():
        m = _ENTETE.match(ligne)
        if not m:
            continue
        try:
            quand = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        trouvees.append((quand, m.group(2), m.group(3), m.group(4)[:300]))
    sortie["total_24h"] = sum(1 for q, *_ in trouvees if maintenant - q <= datetime.timedelta(hours=24))
    for quand, niveau, source, message in reversed(trouvees[-limite:]):
        sortie["recentes"].append({"date": quand.strftime("%Y-%m-%d %H:%M:%S"), "niveau": niveau, "source": source, "message": message})
    return sortie


def _erreurs():
    chemin = ((getattr(settings, "LOGGING", {}) or {}).get("handlers", {}).get("fichier") or {}).get("filename")
    return lire_erreurs(chemin)


def collecter() -> dict:
    return {"maintenant": time.time(), "base": _mesure(_base), "caches": _mesure(_caches), "redis": _mesure(_redis), "parquet": _mesure(_parquet),
            "memoire_recherche": _mesure(_memoire_recherche), "erreurs": _mesure(_erreurs)}


def verifier_parquet(reparer=False, apres=0) -> dict:
    """Compare les fichiers Parquet à l'EAV (source de vérité) pour au plus MAX_TABLES_VERIFICATION tables EAV ; répare les écarts si `reparer`.
    `apres` : id de la dernière table déjà vérifiée (suite d'un passage tronqué).
    Retourne {"etats": {état: nombre}, "ecarts": [rapports non « ok »], "verifiees", "tronque", "suite": id à passer en `apres` ou None}."""
    from . import parquet_store as ps
    from .models import TableDynamique
    tables = list(TableDynamique.objects.exclude(type_stockage=TableDynamique.STOCKAGE_NOSQL).filter(id__gt=int(apres or 0)).order_by("id")[:MAX_TABLES_VERIFICATION + 1])
    tronque = len(tables) > MAX_TABLES_VERIFICATION
    etats, ecarts = {}, []
    for table in tables[:MAX_TABLES_VERIFICATION]:
        r = ps.verifier_table(table, reparer=bool(reparer))
        etats[r["etat"]] = etats.get(r["etat"], 0) + 1
        if r["etat"] not in ("ok", "ignoree"):
            ecarts.append({"table": r["table"], "nom": r["nom"], "etat": r["etat"], "lignes_eav": r["lignes_eav"], "lignes_fichier": r["lignes_fichier"],
                           "colonnes": r["colonnes"][:10], "non_convertibles": r.get("non_convertibles", 0)})
    return {"etats": etats, "ecarts": ecarts[:50], "verifiees": sum(etats.values()), "tronque": tronque, "suite": tables[MAX_TABLES_VERIFICATION - 1].pk if tronque else None, "reparer": bool(reparer)}
