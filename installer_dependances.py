#!/usr/bin/env python3
"""Installe, met à jour et prépare Agentgy sur PythonAnywhere (ou tout autre hébergement).

    python3 installer_dependances.py            # menu
    python3 installer_dependances.py 1|2|3|4    # ou : primaire | secondaire | full | maj

  1  PRIMAIRE    requirements_primaire.txt : lancer et utiliser sans trop charger (compte gratuit)
  2  SECONDAIRE  primaire + requirements_secondaire.txt : statistiques avancées, cartes, formats, son
  3  FULL        primaire + secondaire + requirements_full.txt : IA, vision, vidéo, bases externes (compte payant)
  4  METTRE À JOUR (GitHub, dossier ou zip, au choix à l'exécution) : ne remplace que les fichiers qui ont changé ; les migrations
     passent par migrations_sures.py, qui refuse tout ce qui pourrait détruire des données ; puis recharge le site
     options : --source github|dossier|zip  --chemin <dossier ou zip>  --portee tout|scripts  --autoriser-destructif

Tout se règle dans installer.conf (créé et complété par questions au premier lancement ; droits 600, jamais envoyé sur GitHub) :
dépôt, branche, jeton d'accès, niveau, dossier, virtualenv, fichiers à ne jamais écraser. Si le projet n'est pas là, il est cloné.
Rejouable à volonté. Premier compte administrateur : ~/venv-agentgy/bin/python manage.py createsuperuser
"""
import argparse
import base64
import configparser
import filecmp
import fnmatch
import getpass
import os
import shutil
import subprocess
import sys
import time
import venv
import zipfile
import zlib
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
CONF = SCRIPT / "installer.conf"
NIVEAUX = ("primaire", "secondaire", "full")
GARDER_COPIES = 2   # jeux d'anciennes versions gardés (quota)
MARGE_MO = 25       # place libre à laisser en plus de ce qu'on écrit
MO = 1_048_576
SCRIPTS = ("installer_dependances.py", "migrations_sures.py")   # ce que couvre la portée « scripts »
DEFAUTS = {
    "depot": {"url": "", "branche": "main", "token": ""},
    "installation": {"niveau": "primaire", "dossier": "", "venv": "~/venv-agentgy", "base": ""},
    "mise_a_jour": {"conserver": "media, logs, staticfiles, *.sqlite3, .secret_key, installer.conf", "recharger": "auto", "quota_mo": "512"},
}
AIDE = {
    "url": "dépôt GitHub : https://github.com/utilisateur/depot.git (ou utilisateur/depot)",
    "branche": "branche suivie par la mise à jour",
    "token": "jeton d'accès GitHub (dépôt privé : droit « Contents : lecture seule ») ; vide = dépôt public",
    "niveau": "primaire | secondaire | full (mémorisé au dernier choix 1-3 ; la mise à jour le réutilise)",
    "dossier": "dossier du projet ; vide = dossier de ce script (cloné ici s'il n'y a pas de manage.py)",
    "venv": "environnement virtuel (à indiquer dans l'onglet Web de PythonAnywhere)",
    "base": "valeur de GYPLATFORM_BASE pour les migrations (sqlite ou postgresql) ; vide = variable d'environnement ou défaut",
    "conserver": "fichiers/dossiers jamais écrasés par la mise à jour (séparés par des virgules, jokers permis)",
    "quota_mo": "quota disque du compte en Mo (512 = compte gratuit PythonAnywhere) : une mise à jour ou une migration qui ne tiendrait pas est refusée AVANT toute écriture ; 0 = pas de contrôle",
    "recharger": "auto = recharge le site PythonAnywhere (/var/www/*_wsgi.py) ; non = ne rien recharger ; ou un chemin",
}


# ── Configuration ────────────────────────────────────────────────────────────────────────────────────────────────────────

def lire():
    c = configparser.ConfigParser(interpolation=None)
    c.read_dict(DEFAUTS)
    if CONF.is_file():
        c.read(CONF, encoding="utf-8")
    return c


def ecrire(c):
    lignes = ["# Configuration de l'installateur — contient le jeton GitHub : ne jamais l'envoyer sur GitHub."]
    for section, cles in DEFAUTS.items():
        lignes.append(f"\n[{section}]")
        for k in cles:
            lignes += [f"# {AIDE[k]}", f"{k} = {c[section][k]}"]
    CONF.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    os.chmod(CONF, 0o600)


def demander(question, defaut="", secret=False):
    if not sys.stdin.isatty():
        return defaut
    invite = f"{question}" + (f" [{defaut}]" if defaut and not secret else "") + " : "
    return ((getpass.getpass if secret else input)(invite).strip()) or defaut


def completer_depot(c):
    """Pose les questions du dépôt tant que l'adresse manque, puis les mémorise dans installer.conf."""
    d = c["depot"]
    if d["url"].strip():
        return
    d["url"] = demander("Dépôt GitHub (utilisateur/depot ou adresse complète)")
    if not d["url"].strip():
        sys.exit("Dépôt GitHub manquant : renseignez [depot] url dans installer.conf.")
    d["branche"] = demander("Branche", d["branche"])
    d["token"] = demander("Jeton d'accès GitHub (vide si le dépôt est public)", secret=True)
    ecrire(c)


def url_depot(c):
    u = c["depot"]["url"].strip()
    return u if "://" in u else f"https://github.com/{u.removesuffix('.git')}.git"


# ── Git (le jeton passe par un en-tête, jamais écrit dans .git/config ni dans l'adresse) ───────────────────────────────────

def git(c, *args, cwd=None, capture=False, check=True, entree=None):
    cmd = ["git"]
    token = c["depot"]["token"].strip()
    if token:
        cmd += ["-c", "http.extraheader=Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()]
    r = subprocess.run(cmd + list(args), cwd=cwd, input=entree, text=True, stdout=subprocess.PIPE if capture else None,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and r.returncode:
        sys.exit(f"Échec de « git {args[0]} » (code {r.returncode}). Dépôt privé : vérifiez l'adresse, la branche et le jeton.")
    return r.stdout if capture else ""


def dossier_projet(c):
    """(dossier, vient_d_etre_clone). Sans manage.py à l'endroit prévu, le dépôt est cloné."""
    base = Path(c["installation"]["dossier"] or SCRIPT).expanduser()
    if (base / "manage.py").is_file():
        return base, False
    completer_depot(c)
    cible = base if base != SCRIPT else SCRIPT / url_depot(c).rstrip("/").removesuffix(".git").split("/")[-1]
    if cible.exists() and any(cible.iterdir()):
        sys.exit(f"{cible} existe et n'est pas le projet (pas de manage.py) : videz-le ou réglez [installation] dossier.")
    print(f"Clonage de {url_depot(c)} ({c['depot']['branche']}) dans {cible}")
    git(c, "clone", "--depth", "1", "--branch", c["depot"]["branche"], url_depot(c), str(cible))
    c["installation"]["dossier"] = str(cible)
    ecrire(c)
    return cible, True


# ── Environnement et paquets ─────────────────────────────────────────────────────────────────────────────────────────────

def python_venv(c):
    v = Path(c["installation"]["venv"]).expanduser()
    return v, v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def creer_venv(c):
    v, py = python_venv(c)
    if not py.exists():
        print(f"Création du virtualenv : {v} (Python {sys.version.split()[0]})")
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(v)   # réutilise les paquets de PythonAnywhere : 0 Mo de quota
    cfg = v / "pyvenv.cfg"   # venv d'une version précédente : accès aux paquets système activé sans le recréer
    if cfg.is_file() and "include-system-site-packages = false" in cfg.read_text():
        cfg.write_text(cfg.read_text().replace("include-system-site-packages = false", "include-system-site-packages = true"))
    return py


def lancer(py, projet, env, *args, check=True):
    if subprocess.call([str(py), *args], cwd=str(projet), env=env) != 0 and check:
        sys.exit(f"Échec de : {' '.join(args)}")


def paquets(fichier):
    """Lignes utiles d'un fichier requirements (sans commentaires)."""
    return [l.split("#")[0].strip() for l in fichier.read_text(encoding="utf-8").splitlines() if l.split("#")[0].strip()]


def installer_paquets(py, projet, env, niveau):
    """Installe les niveaux jusqu'à `niveau`. Un paquet qui échoue (quota, compilation) est signalé sans arrêter les autres."""
    base = ["-m", "pip", "install", "--no-cache-dir"]   # le cache pip compte dans le quota
    lancer(py, projet, env, *base, "--upgrade", "pip")
    echecs = []
    for nom in NIVEAUX[:NIVEAUX.index(niveau) + 1]:
        fichier = projet / f"requirements_{nom}.txt"
        print(f"\n── Niveau {nom} ──")
        if subprocess.call([str(py), *base, "-r", str(fichier)], cwd=str(projet), env=env) == 0:
            continue
        for ligne in paquets(fichier):   # repli : un par un, pour savoir lequel échoue
            if subprocess.call([str(py), *base, ligne], cwd=str(projet), env=env) != 0:
                echecs.append(ligne)
    shutil.rmtree(Path.home() / ".cache" / "pip", ignore_errors=True)
    return echecs


def utilise_mo(c):
    """Espace occupé dans le quota : dossier personnel (+ le projet s'il est ailleurs)."""
    projet = Path(c["installation"]["dossier"] or SCRIPT).expanduser().resolve()
    racines = [Path.home().resolve()]
    if not any(r == projet or r in projet.parents for r in racines):
        racines.append(projet)
    total = 0
    for r in racines:
        for d, _, fichiers in os.walk(r):
            for f in fichiers:
                try:
                    total += os.lstat(os.path.join(d, f)).st_size
                except OSError:
                    pass
    return total / MO


def verifier_place(c, besoin_mo, quoi):
    """Refuse AVANT d'écrire si le quota ne laisse pas la place (un disque plein en cours d'écriture abîme fichiers et bases)."""
    quota = float(c["mise_a_jour"]["quota_mo"] or 0)
    if quota <= 0:
        return
    libre = quota - utilise_mo(c)
    if libre < besoin_mo + MARGE_MO:
        sys.exit(f"Place insuffisante pour {quoi} : {libre:.0f} Mo libres sur {quota:.0f} Mo, {besoin_mo + MARGE_MO:.0f} Mo nécessaires "
                 f"(marge comprise). Rien n'a été modifié. Libérez de la place (anciennes versions dans ../agentgy_sauvegardes_maj, sauvegardes/, logs/).")


def env_projet(c):
    env = {**os.environ, "GYPLATFORM_QUOTA_MO": str(c["mise_a_jour"]["quota_mo"] or 0)}
    if c["installation"]["base"].strip():
        env["GYPLATFORM_BASE"] = c["installation"]["base"]
    return env


def taille_mo(dossier):
    return sum(f.stat().st_size for f in Path(dossier).rglob("*") if f.is_file() and not f.is_symlink()) / 1_048_576


def appliquer(py, projet, env, extra=()):
    """Migrations (via migrations_sures.py : analyse, copie, contrôle des données), tables de cache, fichiers statiques."""
    if not (projet / "migrations_sures.py").is_file():
        sys.exit("migrations_sures.py est absent du projet : les migrations ne sont pas appliquées.")
    lancer(py, projet, env, "migrations_sures.py", *extra)   # bloque (code 2) tout ce qui peut détruire des données ; le site n'est alors pas rechargé
    lancer(py, projet, env, "manage.py", "createcachetable")
    lancer(py, projet, env, "manage.py", "collectstatic", "--noinput")


def recharger(c):
    choix = c["mise_a_jour"]["recharger"].strip()
    if choix.lower() == "non":
        return
    cibles = sorted(Path("/var/www").glob("*_wsgi.py")) if choix.lower() == "auto" and Path("/var/www").is_dir() else \
        ([Path(choix).expanduser()] if choix.lower() != "auto" else [])
    for f in cibles:
        f.touch()   # PythonAnywhere recharge le site quand le fichier WSGI change
        print(f"Site rechargé ({f.name}).")
    if not cibles:
        print("Rechargez le site depuis l'onglet Web de PythonAnywhere (bouton Reload).")


def installer(c, niveau, extra=()):
    projet, _ = dossier_projet(c)
    c["installation"]["niveau"] = niveau
    ecrire(c)
    py = creer_venv(c)
    env = env_projet(c)
    echecs = installer_paquets(py, projet, env, niveau)
    appliquer(py, projet, env, extra)
    recharger(c)
    print(f"\nTerminé (niveau {niveau}). Virtualenv : {python_venv(c)[0]} ({taille_mo(python_venv(c)[0]):.0f} Mo, hors paquets système).")
    if echecs:
        print("Non installés (quota ou compilation) — les fonctionnalités concernées afficheront « fonctionnalité bloquée » :\n  " + "\n  ".join(echecs))
    print("Premier compte administrateur (une seule fois) : " + f"{python_venv(c)[1]} manage.py createsuperuser")


# ── Mise à jour : GitHub, dossier ou zip (choisi à l'exécution) ───────────────────────────────────────────────────────────

def fichier_libre(c):
    """Fonction « ce fichier peut-il être remplacé ? » : faux pour tout ce que [mise_a_jour] conserver protège."""
    gardes = [p.strip() for p in c["mise_a_jour"]["conserver"].split(",") if p.strip()]
    return lambda f: not any(fnmatch.fnmatch(f, p) or f.startswith(p.rstrip("/") + "/") or fnmatch.fnmatch(Path(f).name, p) for p in gardes)


def copier_anciennes(projet, fichiers):
    """Copie les versions actuelles dans ../agentgy_sauvegardes_maj/<date> (deux jeux gardés) et retourne ce dossier."""
    sauvegarde = projet.parent / "agentgy_sauvegardes_maj" / time.strftime("%Y%m%d-%H%M%S")
    for f in fichiers:
        if (projet / f).is_file():
            (sauvegarde / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(projet / f, sauvegarde / f)
    for vieille in (sorted(sauvegarde.parent.iterdir()) if sauvegarde.parent.exists() else [])[:-GARDER_COPIES]:   # le quota est limité
        shutil.rmtree(vieille, ignore_errors=True)
    return sauvegarde


def finaliser(c, projet, a_ecrire, niveau, extra):
    """Paquets si requirements_*.txt a changé, migrations sûres, fichiers statiques, rechargement du site."""
    py = creer_venv(c)
    env = env_projet(c)
    echecs = installer_paquets(py, projet, env, niveau) if any(f.startswith("requirements") for f in a_ecrire) else []
    appliquer(py, projet, env, extra)
    recharger(c)
    if echecs:
        print("Non installés :\n  " + "\n  ".join(echecs))


def mettre_a_jour(c, source="github", chemin="", portee="tout", extra=()):
    """source : github | dossier | zip. portee : tout (projet complet) | scripts (seulement SCRIPTS). Les fichiers de
    [mise_a_jour] conserver (media, bases, clés…) ne sont jamais écrasés, et les anciennes versions sont d'abord copiées."""
    niveau = c["installation"]["niveau"] if c["installation"]["niveau"] in NIVEAUX else "primaire"
    if source != "github":
        return maj_locale(c, source, chemin, portee, niveau, extra)
    if portee == "scripts":
        projet = Path(c["installation"]["dossier"] or SCRIPT).expanduser()
        if not (projet / "manage.py").is_file():
            sys.exit("Mise à jour des scripts seuls : projet introuvable (pas de manage.py).")
        completer_depot(c)
    else:
        completer_depot(c)
        projet, clone = dossier_projet(c)
        if clone or not python_venv(c)[1].exists():
            return installer(c, niveau, extra)   # première installation : tout est à faire
    g = lambda *a, **k: git(c, *a, cwd=projet, **k)
    branche = c["depot"]["branche"]
    if not (projet / ".git").is_dir():
        g("init", "-q")   # projet livré en zip : on le rattache au dépôt sans toucher à ses fichiers
    g("config", "remote.origin.url", url_depot(c))
    # Dépôt « partiel » : on ne télécharge d'abord que l'arborescence (quelques centaines de Ko), jamais le contenu du dépôt.
    g("config", "extensions.partialClone", "origin")
    g("config", "remote.origin.promisor", "true")
    g("config", "remote.origin.partialclonefilter", "blob:none")
    verifier_place(c, 40, "la mise à jour")
    g("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", branche)
    ancien = g("rev-parse", "-q", "--verify", "HEAD", capture=True, check=False).strip()
    g("symbolic-ref", "HEAD", f"refs/heads/{branche}")
    g("read-tree", "FETCH_HEAD")   # index = arborescence du dépôt (sans contenu) : sert à écrire les seuls fichiers qui diffèrent
    libre = fichier_libre(c)
    distant = {f: o for f, o in arbre(g, "FETCH_HEAD").items() if libre(f)}
    if portee == "scripts":
        distant = {f: o for f, o in distant.items() if f in SCRIPTS}   # on ne lit que les deux scripts
    # Comparaison par empreintes : on calcule celle de chaque fichier local (disque seulement) et on la compare à celle du dépôt.
    # Aucun contenu n'est téléchargé pour savoir ce qui a changé.
    locaux = [f for f in distant if (projet / f).is_file()]
    empreintes = dict(zip(locaux, g("hash-object", "--no-filters", "--stdin-paths", capture=True, entree="\n".join(locaux) + "\n").split())) if locaux else {}
    a_ecrire = [f for f, o in distant.items() if empreintes.get(f) != o]
    a_supprimer = [f for f in arbre(g, ancien) if f not in distant and libre(f) and (projet / f).is_file()] if ancien and portee == "tout" else []
    commit = g("rev-parse", "--short", "FETCH_HEAD", capture=True).strip()
    if not a_ecrire and not a_supprimer:
        if portee == "tout":
            g("update-ref", "HEAD", "FETCH_HEAD")
        return print(f"Déjà à jour (version {commit}).")
    verifier_place(c, sum((projet / f).stat().st_size for f in a_ecrire + a_supprimer if (projet / f).is_file()) / MO + 40, "la mise à jour")
    sauvegarde = copier_anciennes(projet, a_ecrire + a_supprimer)
    if a_ecrire:   # seuls les fichiers différents sont téléchargés, au moment d'être écrits
        g("checkout-index", "-f", "-z", "--stdin", entree="\0".join(a_ecrire) + "\0")
    for f in a_supprimer:
        (projet / f).unlink(missing_ok=True)
    if portee == "tout":
        g("update-ref", "HEAD", "FETCH_HEAD")
    g("reflog", "expire", "--expire=now", "--all", check=False)   # le dossier .git ne grossit pas à chaque mise à jour
    g("gc", "-q", "--prune=now", check=False)
    print(f"Mise à jour vers {commit} : {len(a_ecrire)} fichier(s) remplacé(s), {len(a_supprimer)} supprimé(s) (anciennes versions : {sauvegarde}).")
    for f in (a_ecrire + a_supprimer)[:40]:
        print("  ", f)
    if portee == "scripts":
        return print("Scripts à jour (la nouvelle version de ce script agit dès le prochain lancement). Projet, base et site non touchés.")
    finaliser(c, projet, a_ecrire, niveau, extra)


def arbre(g, ref):
    """{chemin: empreinte} des fichiers d'un état du dépôt, lu dans l'arborescence seule (aucun contenu de fichier)."""
    res = {}
    for ligne in g("ls-tree", "-r", "-z", ref, capture=True, check=False).split("\0"):
        if "\t" in ligne:
            meta, chemin = ligne.split("\t", 1)
            mode, genre, oid = meta.split()
            if genre == "blob":
                res[chemin] = oid
    return res


def racine_dans(base, marqueur):
    """Dossier le moins profond de `base` (lui-même compris) qui contient `marqueur` ; None s'il n'y en a pas."""
    if (base / marqueur).is_file():
        return base
    trouves = sorted((p.parent for p in base.rglob(marqueur) if ".git" not in p.parts), key=lambda d: len(d.parts))
    return trouves[0] if trouves else None


def crc32_fichier(chemin):
    crc = 0
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            crc = zlib.crc32(bloc, crc)
    return crc & 0xFFFFFFFF


def ecrire_sans_risque(destination, ecrire):
    """Écrit sous un nom temporaire puis renomme : un disque plein ne laisse jamais un fichier du projet à moitié écrit."""
    tmp = destination.with_name(destination.name + ".maj")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        ecrire(tmp)
        os.replace(tmp, destination)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        sys.exit(f"Écriture impossible ({destination.name} : {exc}). Fichiers déjà remplacés : voir ../agentgy_sauvegardes_maj ; relancez après avoir libéré de la place.")


def maj_locale(c, source, chemin, portee, niveau, extra):
    """Mise à jour depuis un dossier ou un zip : on compare d'abord (taille + empreinte, sans rien extraire) et seuls les fichiers
    qui diffèrent sont copiés. Rien n'est supprimé (un dossier ou un zip partiel ne doit jamais effacer de fichiers du projet)."""
    if not chemin:
        chemin = demander(f"Chemin du {'zip' if source == 'zip' else 'dossier'}")
    chemin = Path(chemin).expanduser()
    if not chemin.exists():
        sys.exit(f"{chemin} introuvable.")
    projet = Path(c["installation"]["dossier"] or SCRIPT).expanduser()
    if not (projet / "manage.py").is_file():
        sys.exit(f"Projet introuvable dans {projet} (pas de manage.py) : indiquez [installation] dossier dans installer.conf.")
    marqueur = "manage.py" if portee == "tout" else SCRIPTS[0]
    if source == "zip":
        if not zipfile.is_zipfile(chemin):
            sys.exit(f"{chemin} n'est pas un zip valide.")
        z = zipfile.ZipFile(chemin)
        noms = {i.filename: i for i in z.infolist() if not i.filename.endswith("/")}
        for nom in noms:
            if nom.startswith(("/", "\\")) or ".." in Path(nom).parts or ":" in nom.split("/")[0]:
                sys.exit(f"Zip refusé : chemin hors de l'archive ({nom}).")
        entetes = sorted((n for n in noms if n == marqueur or n.endswith("/" + marqueur)), key=len)
        if not entetes:
            sys.exit(f"Source refusée : {marqueur} introuvable (mauvais zip ?).")
        prefixe = entetes[0][:-len(marqueur)]
        fichiers = {n[len(prefixe):]: i for n, i in noms.items() if n.startswith(prefixe)}
        taille = lambda f: fichiers[f].file_size
        identique = lambda f, dst: dst.stat().st_size == fichiers[f].file_size and crc32_fichier(dst) == fichiers[f].CRC

        def ecrire(f):
            def vers(tmp):
                with z.open(fichiers[f]) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            return vers
    else:
        if not chemin.is_dir():
            sys.exit(f"{chemin} n'est pas un dossier.")
        racine = racine_dans(chemin.resolve(), marqueur)
        if racine is None:
            sys.exit(f"Source refusée : {marqueur} introuvable (mauvais dossier ?).")
        fichiers = {p.relative_to(racine).as_posix(): p for p in racine.rglob("*") if p.is_file()}
        taille = lambda f: fichiers[f].stat().st_size
        identique = lambda f, dst: filecmp.cmp(fichiers[f], dst, shallow=False)
        ecrire = lambda f: (lambda tmp: shutil.copy2(fichiers[f], tmp))
    libre = fichier_libre(c)
    candidats = [f for f in SCRIPTS if f in fichiers] if portee == "scripts" else sorted(fichiers)
    a_ecrire = [f for f in candidats if libre(f) and ".git/" not in f and "__pycache__" not in f and not f.endswith(".pyc")
                and not ((projet / f).is_file() and identique(f, projet / f))]
    if not a_ecrire:
        return print("Déjà à jour (aucun fichier ne diffère).")
    verifier_place(c, sum(taille(f) + ((projet / f).stat().st_size if (projet / f).is_file() else 0) for f in a_ecrire) / MO, "cette mise à jour")
    sauvegarde = copier_anciennes(projet, a_ecrire)
    for f in a_ecrire:
        ecrire_sans_risque(projet / f, ecrire(f))
    print(f"Mise à jour depuis {chemin} : {len(a_ecrire)} fichier(s) remplacé(s) (anciennes versions : {sauvegarde}).")
    for f in a_ecrire[:40]:
        print("  ", f)
    if portee == "scripts":
        return print("Scripts à jour (la nouvelle version de ce script agit dès le prochain lancement). Projet, base et site non touchés.")
    finaliser(c, projet, a_ecrire, niveau, extra)


# ── Menu ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

CHOIX = {"1": "primaire", "primaire": "primaire", "2": "secondaire", "secondaire": "secondaire", "3": "full", "full": "full",
         "4": "maj", "maj": "maj", "update": "maj"}
SOURCES = {"1": "github", "github": "github", "2": "dossier", "dossier": "dossier", "3": "zip", "zip": "zip"}


def main():
    ap = argparse.ArgumentParser(description="Installe et met à jour Agentgy.")
    ap.add_argument("choix", nargs="?", help="1 | 2 | 3 | 4 (primaire | secondaire | full | maj)")
    ap.add_argument("--source", choices=("github", "dossier", "zip"), help="mise à jour : d'où viennent les fichiers (défaut : demandé, sinon github)")
    ap.add_argument("--chemin", default="", help="mise à jour depuis un dossier ou un zip : son chemin")
    ap.add_argument("--portee", choices=("tout", "scripts"), help="tout = le projet complet ; scripts = seulement installer_dependances.py et migrations_sures.py")
    ap.add_argument("--autoriser-destructif", action="store_true", help="applique aussi les migrations que migrations_sures.py a bloquées")
    a = ap.parse_args()
    extra = ("--autoriser-destructif",) if a.autoriser_destructif else ()
    c = lire()
    choix = CHOIX.get(a.choix.lower()) if a.choix else None
    if a.choix and not choix:
        sys.exit("Choix inconnu. Utiliser : 1 | 2 | 3 | 4 (primaire | secondaire | full | maj).")
    if not choix:
        actuel = c["installation"]["niveau"]
        print("Agentgy — installation et mise à jour\n"
              f"  1) Primaire     lancer et utiliser sans trop charger (compte gratuit){'   ← actuel' if actuel == 'primaire' else ''}\n"
              f"  2) Secondaire   + statistiques avancées, cartes, formats, son{'   ← actuel' if actuel == 'secondaire' else ''}\n"
              f"  3) Full         + IA, vision, vidéo, bases externes (compte payant){'   ← actuel' if actuel == 'full' else ''}\n"
              "  4) Mettre à jour (GitHub, dossier ou zip ; migrations protégées)")
        choix = CHOIX.get(demander("Votre choix", "4" if CONF.is_file() else "1").lower())
        if not choix:
            sys.exit("Choix inconnu.")
    if choix == "maj":
        source, portee = a.source, a.portee
        if not source:
            print("Source de la mise à jour :\n  1) GitHub\n  2) Un dossier\n  3) Un zip")
            source = SOURCES.get(demander("Source", "1").lower())
            if not source:
                sys.exit("Source inconnue.")
        if not portee:
            print("Que mettre à jour :\n  1) Le projet complet (+ migrations)\n  2) Les scripts seulement (installer_dependances.py, migrations_sures.py)")
            portee = {"1": "tout", "2": "scripts"}.get(demander("Portée", "1"), "tout")
        mettre_a_jour(c, source, a.chemin, portee, extra)
    else:
        if choix == "full":
            print("Attention : le niveau full dépasse le quota d'un compte gratuit (512 Mo).")
        installer(c, choix, extra)


if __name__ == "__main__":
    main()
