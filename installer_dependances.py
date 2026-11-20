#!/usr/bin/env python3
"""Installe et prépare Agentgy sur PythonAnywhere. Il n'y a plus qu'à lancer :

    python3 installer_dependances.py

1. crée l'environnement virtuel ~/venv-agentgy (s'il manque) et y installe requirements.txt ;
2. applique les migrations (en mode « un fichier par entreprise », GYPLATFORM_BASE=sqlite_par_entreprise : base système puis
   chaque entreprise) ;
3. crée les tables de cache et rassemble les fichiers statiques.

Rejouable à volonté (à chaque mise à jour du code). Le Python qui lance le script est celui du virtualenv
(autre version : python3.12 installer_dependances.py). Le premier compte administrateur se crée ensuite avec :
    ~/venv-agentgy/bin/python manage.py createsuperuser
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

RACINE = Path(__file__).resolve().parent
VENV = Path.home() / "venv-agentgy"
PYTHON_VENV = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def lancer(*args):
    if subprocess.call([str(PYTHON_VENV), *args], cwd=str(RACINE)) != 0:
        sys.exit(f"Échec de : {' '.join(args)}")


def main():
    if not (RACINE / "requirements.txt").is_file():
        sys.exit("requirements.txt introuvable : lancer le script depuis la racine du projet.")
    if not PYTHON_VENV.exists():
        print(f"Création du virtualenv : {VENV} (Python {sys.version.split()[0]})")
        venv.EnvBuilder(with_pip=True).create(VENV)
    lancer("-m", "pip", "install", "--upgrade", "pip")
    lancer("-m", "pip", "install", "-r", "requirements.txt")
    if os.environ.get("GYPLATFORM_BASE") == "sqlite_par_entreprise":
        lancer("manage.py", "migrer_entreprises")   # base système + un fichier par entreprise
    else:
        lancer("manage.py", "migrate", "--noinput")
    lancer("manage.py", "createcachetable")
    lancer("manage.py", "collectstatic", "--noinput")
    print(f"\nTerminé. Virtualenv : {VENV} (à indiquer dans l'onglet Web de PythonAnywhere), puis bouton Reload.")
    print("Premier compte administrateur (une seule fois) : ~/venv-agentgy/bin/python manage.py createsuperuser")


if __name__ == "__main__":
    main()
