"""GÉOLOCALISATION : fond de carte (tuiles OpenStreetMap) servi PAR LE SERVEUR avec cache disque, construit peu à peu (module pur, stdlib uniquement).

Le navigateur ne contacte jamais OpenStreetMap : il demande la tuile à la plateforme, qui la lit sur disque ou la télécharge UNE fois.
  * le parcours manuel de la carte remplit le cache ; en plus, un « moissonneur » (fil d'arrière-plan lancé avec le serveur) complète et
    renouvelle ce cache LENTEMENT et sans interruption : (1) autour des tuiles que les utilisateurs ont regardées (voisines + zoom plus fin),
    (2) la pyramide mondiale de base (zoom 0 à `zoom_base`), (3) le renouvellement des tuiles périmées (requête conditionnelle : 304 = inchangée).
    Avec le temps, la carte fonctionne sans internet ;
  * usage conforme à la politique d'OpenStreetMap : User-Agent identifié, 2 téléchargements simultanés au plus, UNE tuile toutes les
    `pause_s` secondes pour le moissonneur, zoom plafonné, plafond de fichiers, mise en pause possible (et GYPLATFORM_TUILES_AUTO=0 pour le
    désactiver). Pour une récolte plus large, utiliser un serveur de tuiles à soi : GYPLATFORM_TUILES_URL (modèle avec {z} {x} {y}) ;
  * zipper() / restaurer() : tout le cache dans un zip portable (dossier « tuiles_geo/z/x/y.png »), repris aussi par l'archive de clôture ;
  * hors ligne : repli exponentiel (15 s → 10 min), reprise automatique ; les tuiles déjà là restent servies.
Rien n'est stocké en base : les tuiles sont des fichiers dans <MEDIA_ROOT>/systeme/geo_tuiles/z/x/y.png.
"""
from __future__ import annotations
from engine.i18n import tl

import email.utils
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections import OrderedDict
from pathlib import Path
from engine.i18n import t as _gt  # noqa: E402

URL_DEFAUT = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
AGENT = tl("engine/geo_tuiles.py:g:19:9")
LIMITES = {"zoom_max": 19, "octets_tuile": 400_000, "delai_s": 6.0, "duree_cache_s": 30 * 86400, "echec_s": 120.0, "fichiers_max": 150_000,
           "zoom_base": 6, "zoom_auto_max": 15, "pause_s": 1.5, "file_max": 20_000}
PREFIXE_ZIP = "tuiles_geo/"            # même dossier dans le zip des tuiles et dans l'archive de clôture : une seule restauration pour les deux
_lot = threading.Semaphore(2)          # politique OSM : au plus 2 connexions simultanées
_echecs: dict = {}                     # tuile -> instant du dernier échec (on ne martèle pas un réseau absent)
_ecritures = [0]


def valide(z, x, y) -> bool:
    try:
        z, x, y = int(z), int(x), int(y)
    except (TypeError, ValueError):
        return False
    return 0 <= z <= LIMITES["zoom_max"] and 0 <= x < 2 ** z and 0 <= y < 2 ** z


def _nettoyer(racine: Path) -> None:
    """Plafond de fichiers : au-delà, les 20 % de tuiles les plus anciennes sont supprimées."""
    fichiers = sorted(racine.rglob("*.png"), key=lambda f: f.stat().st_mtime)
    if len(fichiers) > LIMITES["fichiers_max"]:
        for f in fichiers[: len(fichiers) // 5]:
            f.unlink(missing_ok=True)


_nb = [None]                           # nombre de tuiles sur disque (None = à recompter)
_stats = {"t": 0.0, "nb": 0, "octets": 0}


def _modele(url: str | None = None) -> str:
    return url or os.environ.get("GYPLATFORM_TUILES_URL", "").strip() or URL_DEFAUT


def _plafond() -> int:
    try:
        return max(1000, int(os.environ.get("GYPLATFORM_TUILES_MAX", "") or LIMITES["fichiers_max"]))
    except ValueError:
        return LIMITES["fichiers_max"]


def compter(racine: Path) -> int:
    """Nombre de tuiles sur disque (recompté seulement si inconnu)."""
    if _nb[0] is None:
        _nb[0] = sum(1 for _ in Path(racine).rglob("*.png"))
    return _nb[0]


def statistiques(racine: Path, ttl: float = 60.0) -> dict:
    """{nb, octets} du cache, recalculé au plus une fois par `ttl` secondes (le parcours de dizaines de milliers de fichiers n'est pas gratuit)."""
    if time.time() - _stats["t"] > ttl:
        nb = octets = 0
        for f in Path(racine).rglob("*.png"):
            try:
                octets += f.stat().st_size
                nb += 1
            except OSError:
                continue
        _stats.update(t=time.time(), nb=nb, octets=octets)
        _nb[0] = nb
    return {"nb": _stats["nb"], "octets": _stats["octets"]}


def _echec(cle) -> None:
    _echecs[cle] = time.time()
    if len(_echecs) > 5000:
        _echecs.clear()


def _ecrire(chemin: Path, donnees: bytes, mtime: float | None = None) -> None:
    """Écriture atomique : jamais de tuile à moitié écrite ; tient le compteur de tuiles à jour."""
    neuve = not chemin.is_file()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(donnees)
    tmp.replace(chemin)
    if mtime is not None:
        os.utime(chemin, (mtime, mtime))
    if neuve and _nb[0] is not None:
        _nb[0] += 1


def _recuperer(racine: Path, z: int, x: int, y: int, modele: str, ancien: Path | None = None) -> tuple:
    """Télécharge UNE tuile (2 connexions simultanées au plus, politique OSM). Renvoie (octets | None, reseau_ok) ; `reseau_ok` = False quand le
    réseau est absent ou le serveur sature (429, 5xx) : le moissonneur ralentit alors. `ancien` : tuile périmée à renouveler (requête
    conditionnelle : 304 = inchangée, on rafraîchit seulement sa date)."""
    cle = (z, x, y)
    en_tete = {"User-Agent": AGENT}
    if ancien is not None:
        try:
            en_tete["If-Modified-Since"] = email.utils.formatdate(ancien.stat().st_mtime, usegmt=True)
        except OSError:
            ancien = None
    with _lot:
        try:
            req = urllib.request.Request(modele.format(z=z, x=x, y=y), headers=en_tete)
            with urllib.request.urlopen(req, timeout=LIMITES["delai_s"]) as r:
                donnees = r.read(LIMITES["octets_tuile"] + 1)
        except urllib.error.HTTPError as e:
            if e.code == 304 and ancien is not None:
                os.utime(ancien, None)
                return ancien.read_bytes(), True
            _echec(cle)
            return None, not (e.code == 429 or e.code >= 500)
        except (OSError, ValueError, KeyError, IndexError):         # réseau absent, refus, modèle d'URL invalide
            _echec(cle)
            return None, False
    if not donnees.startswith(b"\x89PNG") or len(donnees) > LIMITES["octets_tuile"]:
        _echec(cle)
        return None, True
    _ecrire(Path(racine) / str(z) / str(x) / f"{y}.png", donnees)
    _ecritures[0] += 1
    if _ecritures[0] % 500 == 0:
        _nettoyer(Path(racine))
        _nb[0] = None
    return donnees, True


def tuile(racine: Path, z, x, y, url: str | None = None) -> bytes | None:
    """Octets PNG de la tuile (cache d'abord, puis téléchargement), ou None si indisponible (hors ligne, refus, coordonnées invalides)."""
    if not valide(z, x, y):
        return None
    z, x, y = int(z), int(x), int(y)
    chemin = Path(racine) / str(z) / str(x) / f"{y}.png"
    ancien = None
    if chemin.is_file():
        if time.time() - chemin.stat().st_mtime < LIMITES["duree_cache_s"]:
            return chemin.read_bytes()
        ancien = chemin                                            # périmée : on tente de la renouveler, sinon on la sert quand même
    if time.time() - _echecs.get((z, x, y), 0.0) < LIMITES["echec_s"]:
        return ancien.read_bytes() if ancien else None
    modele = _modele(url)
    if not modele.startswith(("http://", "https://")):
        return ancien.read_bytes() if ancien else None
    donnees, _ = _recuperer(racine, z, x, y, modele, ancien)
    if donnees is None and ancien is not None:
        return ancien.read_bytes()
    return donnees


# ── Moissonneur : le cache se construit tout seul, lentement ─────────────────
_file: "OrderedDict[tuple, None]" = OrderedDict()   # tuiles d'intérêt (vues par les utilisateurs → voisines, zoom plus fin) ; la plus récente d'abord
_verrou = threading.Lock()
_arret = threading.Event()
_pause = threading.Event()
_etat = {"actif": False, "hors_ligne": False, "telecharges": 0, "phase": "", "dernier": 0.0}
_fil: list = [None]
_verrou_fichier: list = [None]


def signaler(z, x, y) -> None:
    """Appelée à chaque tuile servie : met en file les 8 voisines et les 4 tuiles de zoom plus fin (le moissonneur les téléchargera plus tard).
    Sans effet si le moissonneur ne tourne pas dans ce processus."""
    if not _etat["actif"] or not valide(z, x, y):
        return
    z, x, y = int(z), int(x), int(y)
    n = 2 ** z
    voisines = [(z, (x + dx) % n, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx or dy) and 0 <= y + dy < n]
    fines = [(z + 1, 2 * x + i, 2 * y + j) for i in (0, 1) for j in (0, 1)] if z + 1 <= LIMITES["zoom_auto_max"] else []
    with _verrou:
        for t in voisines + fines:
            if t not in _file:
                _file[t] = None
        while len(_file) > LIMITES["file_max"]:
            _file.popitem(last=False)                               # on oublie les plus anciennes


def _pyramide():
    for z in range(0, LIMITES["zoom_base"] + 1):
        for x in range(2 ** z):
            for y in range(2 ** z):
                yield (z, x, y)


def _balayage(racine: Path):
    for f in Path(racine).rglob("*.png"):
        try:
            z, x, y = int(f.parent.parent.name), int(f.parent.name), int(f.stem)
        except ValueError:
            continue
        yield (z, x, y), f


def _prochaine(racine: Path, base, etat_balayage: dict):
    """Prochaine tuile à récupérer : (clé, tuile périmée à renouveler | None), ou None s'il n'y a rien à faire pour l'instant."""
    racine = Path(racine)
    while True:
        with _verrou:
            cle = _file.popitem(last=True)[0] if _file else None
        if cle is None:
            break
        if valide(*cle) and not (racine / str(cle[0]) / str(cle[1]) / f"{cle[2]}.png").is_file():
            _etat["phase"] = "rg6.msg.019"
            return cle, None
    for cle in base:
        if not (racine / str(cle[0]) / str(cle[1]) / f"{cle[2]}.png").is_file():
            _etat["phase"] = "rg6.msg.020"
            return cle, None
    if time.time() >= etat_balayage["apres"]:                        # renouvellement des tuiles périmées (un balayage au plus par heure)
        if etat_balayage["it"] is None:
            etat_balayage["it"] = _balayage(racine)
        for cle, f in etat_balayage["it"]:
            try:
                if time.time() - f.stat().st_mtime > LIMITES["duree_cache_s"]:
                    _etat["phase"] = "rg6.msg.021"
                    return cle, f
            except OSError:
                continue
        etat_balayage.update(it=None, apres=time.time() + 3600)
    _etat["phase"] = "rg6.msg.022"
    return None


def _boucle(racine: Path, modele: str) -> None:
    racine = Path(racine)
    try:
        pause = max(0.2, float(os.environ.get("GYPLATFORM_TUILES_PAUSE", "") or LIMITES["pause_s"]))
    except ValueError:
        pause = LIMITES["pause_s"]
    base, balayage, attente, reprise = _pyramide(), {"it": None, "apres": 0.0}, 0.0, None
    while not _arret.is_set():
        if (racine / ".pause").exists() or _pause.is_set():
            _etat["phase"] = "rg6.msg.023"
            _arret.wait(2.0)
            continue
        if compter(racine) >= int(_plafond() * 0.9):
            _etat["phase"] = "rg6.msg.024"
            _arret.wait(60.0)
            continue
        cible = reprise or _prochaine(racine, base, balayage)
        reprise = None
        if cible is None:
            _arret.wait(15.0)
            continue
        (z, x, y), ancien = cible
        if time.time() - _echecs.get((z, x, y), 0.0) < LIMITES["echec_s"]:
            continue                                                 # échec récent : on passe à la suivante
        donnees, reseau = _recuperer(racine, z, x, y, modele, ancien)
        if not reseau:
            _etat["hors_ligne"] = True
            attente = min(max(attente * 2, 15.0), 600.0)             # hors ligne : 15 s, 30 s, … 10 min, puis reprise automatique
            reprise = cible
            _echecs.pop((z, x, y), None)
            _arret.wait(attente)
            continue
        _etat["hors_ligne"], attente = False, 0.0
        if donnees is not None:
            _etat["telecharges"] += 1
            _etat["dernier"] = time.time()
        _arret.wait(pause)                                           # UNE tuile toutes les `pause` secondes : jamais de téléchargement en rafale
    _etat["actif"] = False


def demarrer(racine: Path, url: str | None = None) -> bool:
    """Lance le moissonneur (un seul par serveur : verrou de fichier, utile avec plusieurs processus). False s'il tourne déjà ou ne peut pas démarrer."""
    if _fil[0] is not None and _fil[0].is_alive():
        return False
    modele = _modele(url)
    try:
        if not modele.startswith(("http://", "https://")):
            return False
        modele.format(z=0, x=0, y=0)
        Path(racine).mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, KeyError, IndexError):
        return False
    try:
        import fcntl
        fh = open(Path(racine) / ".moissonneur.lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False                                             # un autre processus moissonne déjà
        _verrou_fichier[0] = fh
    except ImportError:
        pass                                                         # pas de fcntl (Windows) : un seul processus suppose
    _arret.clear()
    _etat.update(actif=True, hors_ligne=False, phase="rg6.msg.025")
    _fil[0] = threading.Thread(target=_boucle, args=(Path(racine), modele), name="geo-tuiles", daemon=True)
    _fil[0].start()
    return True


def arreter() -> None:
    _arret.set()


def en_pause(racine: Path, valeur: bool | None = None) -> bool:
    """Lit (ou fixe) la pause ; mémorisée dans un fichier : survit au redémarrage."""
    marque = Path(racine) / ".pause"
    if valeur is True:
        Path(racine).mkdir(parents=True, exist_ok=True)
        marque.write_text("1")
    elif valeur is False:
        marque.unlink(missing_ok=True)
    return marque.exists()


def etat(racine: Path) -> dict:
    s = statistiques(racine)
    with _verrou:
        attente = len(_file)
    return {"nb": s["nb"], "octets": s["octets"], "plafond": _plafond(), "actif": bool(_etat["actif"]), "pause": en_pause(racine),
            "hors_ligne": bool(_etat["hors_ligne"]), "phase": (_gt(_etat["phase"]) if str(_etat["phase"]).startswith("rg6.") else _etat["phase"]), "telecharges": _etat["telecharges"], "file": attente}


def demarrer_au_boot() -> None:
    """Appelé par apps.ready : seulement pour un vrai serveur (pas migrate/test/shell…), comme image_auto. GYPLATFORM_TUILES_AUTO=0 le désactive."""
    if os.environ.get("GYPLATFORM_TUILES_AUTO", "1").strip() == "0":
        return
    argv = sys.argv
    if any(a in argv for a in ("migrate", "makemigrations", "test", "collectstatic", "shell", "check")):
        return
    if argv and argv[0].endswith("manage.py"):
        if len(argv) < 2 or argv[1] != "runserver" or os.environ.get("RUN_MAIN") != "true":
            return
    from .stockage import dossier_systeme
    demarrer(dossier_systeme("geo_tuiles"))


# ── Zip portable / restauration ──────────────────────────────────────────────
_NOM_ZIP = re.compile(r"^" + re.escape(PREFIXE_ZIP) + r"(\d{1,2})/(\d{1,7})/(\d{1,7})\.png$")


def ecrire_dans_zip(racine: Path, zf: zipfile.ZipFile) -> int:
    """Ajoute toutes les tuiles à un zip ouvert (non recompressées : un PNG l'est déjà), sous « tuiles_geo/z/x/y.png ». Renvoie leur nombre."""
    n = 0
    for f in sorted(Path(racine).rglob("*.png")):
        try:
            z, x, y = int(f.parent.parent.name), int(f.parent.name), int(f.stem)
        except ValueError:
            continue
        if not valide(z, x, y):
            continue
        try:
            zf.write(f, f"{PREFIXE_ZIP}{z}/{x}/{y}.png", compress_type=zipfile.ZIP_STORED)
        except OSError:
            continue
        n += 1
    return n


def zipper(racine: Path, cible) -> int:
    """Écrit dans `cible` (fichier binaire) un zip autonome des tuiles + un petit manifeste. Renvoie le nombre de tuiles."""
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        n = ecrire_dans_zip(racine, zf)
        zf.writestr("manifeste_tuiles.json", json.dumps({"type": "tuiles_geo", "tuiles": n, "source": _modele(),
                                                          "cree_le": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False, indent=2))
    return n


def restaurer(racine: Path, zf: zipfile.ZipFile) -> dict:
    """Remet dans le cache les tuiles d'un zip de tuiles OU d'une archive de clôture. Sûr : seuls les noms « tuiles_geo/z/x/y.png » valides sont
    lus (jamais d'écriture ailleurs), signature PNG et taille contrôlées, plafond de fichiers respecté, une tuile plus récente déjà là est gardée."""
    racine = Path(racine)
    ajoutees = ignorees = refusees = 0
    total, plafond = compter(racine), _plafond()
    for info in zf.infolist():
        m = _NOM_ZIP.match(info.filename)
        if not m:
            continue
        z, x, y = (int(g) for g in m.groups())
        if not valide(z, x, y) or info.file_size > LIMITES["octets_tuile"]:
            refusees += 1
            continue
        cible = racine / str(z) / str(x) / f"{y}.png"
        try:
            date_zip = time.mktime(tuple(info.date_time) + (0, 0, -1))
        except (OverflowError, ValueError):
            date_zip = time.time()
        existe = cible.is_file()
        if existe and cible.stat().st_mtime >= date_zip:
            ignorees += 1
            continue
        if not existe and total >= plafond:
            refusees += 1
            continue
        with zf.open(info) as src:
            donnees = src.read(LIMITES["octets_tuile"] + 1)
        if not donnees.startswith(b"\x89PNG") or len(donnees) > LIMITES["octets_tuile"]:
            refusees += 1
            continue
        _ecrire(cible, donnees, mtime=date_zip)
        _echecs.pop((z, x, y), None)
        total += 0 if existe else 1
        ajoutees += 1
    _stats["t"] = 0.0
    return {"ajoutees": ajoutees, "ignorees": ignorees, "refusees": refusees}
