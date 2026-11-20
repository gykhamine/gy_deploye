#!/usr/bin/env python3
"""Installe, met à jour et prépare Agentgy sur PythonAnywhere (ou tout autre hébergement).

    python3 installer_dependances.py            # menu
    python3 installer_dependances.py 1|2|3|4    # ou : primaire | secondaire | full | maj

  1  PRIMAIRE    requirements_primaire.txt : lancer et utiliser sans trop charger (compte gratuit)
  2  SECONDAIRE  primaire + requirements_secondaire.txt : statistiques avancées, cartes, formats, son
  3  FULL        primaire + secondaire + requirements_full.txt : IA, vision, vidéo, bases externes (compte payant)
  4  METTRE À JOUR depuis GitHub : ne remplace que les fichiers qui ont changé, puis réapplique ce qu'il faut et recharge le site

Tout se règle dans installer.conf (créé et complété par questions au premier lancement ; droits 600, jamais envoyé sur GitHub) :
dépôt, branche, jeton d'accès, niveau, dossier, virtualenv, fichiers à ne jamais écraser. Si le projet n'est pas là, il est cloné.
Rejouable à volonté. Premier compte administrateur : ~/venv-agentgy/bin/python manage.py createsuperuser
"""
import base64
import configparser
import fnmatch
import getpass
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent
CONF = SCRIPT / "installer.conf"
NIVEAUX = ("primaire", "secondaire", "full")
DEFAUTS = {
    "depot": {"url": "", "branche": "main", "token": ""},
    "installation": {"niveau": "primaire", "dossier": "", "venv": "~/venv-agentgy", "base": ""},
    "mise_a_jour": {"conserver": "media, logs, staticfiles, *.sqlite3, .secret_key, installer.conf", "recharger": "auto"},
}
AIDE = {
    "url": "dépôt GitHub : https://github.com/utilisateur/depot.git (ou utilisateur/depot)",
    "branche": "branche suivie par la mise à jour",
    "token": "jeton d'accès GitHub (dépôt privé : droit « Contents : lecture seule ») ; vide = dépôt public",
    "niveau": "primaire | secondaire | full (mémorisé au dernier choix 1-3 ; la mise à jour le réutilise)",
    "dossier": "dossier du projet ; vide = dossier de ce script (cloné ici s'il n'y a pas de manage.py)",
    "venv": "environnement virtuel (à indiquer dans l'onglet Web de PythonAnywhere)",
    "base": "valeur de GYPLATFORM_BASE pour les migrations (ex. sqlite_par_entreprise) ; vide = variable d'environnement ou défaut",
    "conserver": "fichiers/dossiers jamais écrasés par la mise à jour (séparés par des virgules, jokers permis)",
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


def taille_mo(dossier):
    return sum(f.stat().st_size for f in Path(dossier).rglob("*") if f.is_file() and not f.is_symlink()) / 1_048_576


def appliquer(py, projet, env):
    """Migrations, tables de cache, fichiers statiques."""
    if env.get("GYPLATFORM_BASE") == "sqlite_par_entreprise":
        lancer(py, projet, env, "manage.py", "migrer_entreprises")   # base système + un fichier par entreprise
    else:
        lancer(py, projet, env, "manage.py", "migrate", "--noinput")
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


def installer(c, niveau):
    projet, _ = dossier_projet(c)
    c["installation"]["niveau"] = niveau
    ecrire(c)
    py = creer_venv(c)
    env = {**os.environ, **({"GYPLATFORM_BASE": c["installation"]["base"]} if c["installation"]["base"].strip() else {})}
    echecs = installer_paquets(py, projet, env, niveau)
    appliquer(py, projet, env)
    recharger(c)
    print(f"\nTerminé (niveau {niveau}). Virtualenv : {python_venv(c)[0]} ({taille_mo(python_venv(c)[0]):.0f} Mo, hors paquets système).")
    if echecs:
        print("Non installés (quota ou compilation) — les fonctionnalités concernées afficheront « fonctionnalité bloquée » :\n  " + "\n  ".join(echecs))
    print("Premier compte administrateur (une seule fois) : " + f"{python_venv(c)[1]} manage.py createsuperuser")


# ── Mise à jour depuis GitHub ────────────────────────────────────────────────────────────────────────────────────────────

def mettre_a_jour(c):
    """Récupère le dernier état de la branche (clone superficiel : léger pour le quota) et ne remplace que les fichiers qui diffèrent,
    sauf ceux de [mise_a_jour] conserver. Les fichiers remplacés sont d'abord copiés dans ../agentgy_sauvegardes_maj/."""
    completer_depot(c)
    projet, clone = dossier_projet(c)
    niveau = c["installation"]["niveau"] if c["installation"]["niveau"] in NIVEAUX else "primaire"
    if clone or not python_venv(c)[1].exists():
        return installer(c, niveau)   # première installation : tout est à faire
    g = lambda *a, **k: git(c, *a, cwd=projet, **k)
    branche = c["depot"]["branche"]
    if not (projet / ".git").is_dir():
        g("init", "-q")   # projet livré en zip : on le rattache au dépôt sans toucher à ses fichiers
    g("config", "remote.origin.url", url_depot(c))
    g("fetch", "-q", "--depth", "1", "origin", branche)
    ancien = g("rev-parse", "-q", "--verify", "HEAD", capture=True, check=False).strip()
    g("symbolic-ref", "HEAD", f"refs/heads/{branche}")
    g("read-tree", "FETCH_HEAD")   # index = dépôt ; « git diff » compare donc les fichiers locaux au dépôt
    gardes = [p.strip() for p in c["mise_a_jour"]["conserver"].split(",") if p.strip()]
    libre = lambda f: not any(fnmatch.fnmatch(f, p) or f.startswith(p.rstrip("/") + "/") or fnmatch.fnmatch(Path(f).name, p) for p in gardes)
    liste = lambda sortie: [f for f in sortie.split("\0") if f and libre(f)]
    a_ecrire = liste(g("diff", "--name-only", "-z", capture=True))
    a_supprimer = liste(g("diff", "--name-only", "--diff-filter=D", "-z", ancien, "FETCH_HEAD", capture=True)) if ancien else []
    commit = g("rev-parse", "--short", "FETCH_HEAD", capture=True).strip()
    if not a_ecrire and not a_supprimer:
        g("update-ref", "HEAD", "FETCH_HEAD")
        return print(f"Déjà à jour (version {commit}).")
    sauvegarde = projet.parent / "agentgy_sauvegardes_maj" / time.strftime("%Y%m%d-%H%M%S")
    for f in a_ecrire + a_supprimer:
        if (projet / f).is_file():
            (sauvegarde / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(projet / f, sauvegarde / f)
    if a_ecrire:
        g("checkout-index", "-f", "-z", "--stdin", entree="\0".join(a_ecrire) + "\0")
    for f in a_supprimer:
        (projet / f).unlink(missing_ok=True)
    g("update-ref", "HEAD", "FETCH_HEAD")
    anciennes = sorted(sauvegarde.parent.iterdir()) if sauvegarde.parent.exists() else []
    for vieille in anciennes[:-3]:   # trois sauvegardes au plus : le quota est limité
        shutil.rmtree(vieille, ignore_errors=True)
    print(f"Mise à jour vers {commit} : {len(a_ecrire)} fichier(s) remplacé(s), {len(a_supprimer)} supprimé(s) (anciennes versions : {sauvegarde}).")
    for f in (a_ecrire + a_supprimer)[:40]:
        print("  ", f)
    py = creer_venv(c)
    env = {**os.environ, **({"GYPLATFORM_BASE": c["installation"]["base"]} if c["installation"]["base"].strip() else {})}
    echecs = installer_paquets(py, projet, env, niveau) if any(f.startswith("requirements") for f in a_ecrire) else []
    appliquer(py, projet, env)
    recharger(c)
    if echecs:
        print("Non installés :\n  " + "\n  ".join(echecs))


# ── Menu ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

CHOIX = {"1": "primaire", "primaire": "primaire", "2": "secondaire", "secondaire": "secondaire", "3": "full", "full": "full",
         "4": "maj", "maj": "maj", "update": "maj"}


def main():
    c = lire()
    choix = CHOIX.get(sys.argv[1].lower()) if len(sys.argv) > 1 else None
    if len(sys.argv) > 1 and not choix:
        sys.exit("Choix inconnu. Utiliser : 1 | 2 | 3 | 4 (primaire | secondaire | full | maj).")
    if not choix:
        actuel = c["installation"]["niveau"]
        print("Agentgy — installation et mise à jour\n"
              f"  1) Primaire     lancer et utiliser sans trop charger (compte gratuit){'   ← actuel' if actuel == 'primaire' else ''}\n"
              f"  2) Secondaire   + statistiques avancées, cartes, formats, son{'   ← actuel' if actuel == 'secondaire' else ''}\n"
              f"  3) Full         + IA, vision, vidéo, bases externes (compte payant){'   ← actuel' if actuel == 'full' else ''}\n"
              "  4) Mettre à jour depuis GitHub (ne remplace que les fichiers modifiés)")
        choix = CHOIX.get(demander("Votre choix", "4" if CONF.is_file() else "1").lower())
        if not choix:
            sys.exit("Choix inconnu.")
    if choix == "maj":
        mettre_a_jour(c)
    else:
        if choix == "full":
            print("Attention : le niveau full dépasse le quota d'un compte gratuit (512 Mo).")
        installer(c, choix)


if __name__ == "__main__":
    main()
