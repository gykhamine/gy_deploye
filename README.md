# Déploiement : PythonAnywhere ou votre propre système

Rien dans ce projet ne dépend plus d'un service à part (Spark, Redis, WebSocket, modem SMS, USSD, ffmpeg, LibreOffice).
Ce qui reste : Django + SQLite (mode WAL) + fichiers sous `media/`.

## 1. Installation et mise à jour

Une seule commande, avec un menu :

```bash
cd ~/agentgy_groupe_une_table
python3 installer_dependances.py          # ou : 1 | 2 | 3 | 4
```

| Choix | Contenu | Pour |
|---|---|---|
| 1 Primaire | `requirements_primaire.txt` : lancer et utiliser sans trop charger | compte gratuit (512 Mo) |
| 2 Secondaire | primaire + `requirements_secondaire.txt` : statistiques avancées, apprentissage automatique, cartes, son, formats | |
| 3 Full | + `requirements_full.txt` : IA (JAX), vision, vidéo, bases externes | compte payant |
| 4 Mettre à jour | GitHub, dossier ou zip (au choix) : ne remplace que les fichiers modifiés ; migrations protégées | |

Le virtualenv réutilise les paquets déjà installés par PythonAnywhere, le cache pip est désactivé, et un paquet qui échoue n'arrête pas les autres
(il est listé à la fin ; la fonctionnalité concernée affiche « fonctionnalité bloquée »).

**Mise à jour** : `python3 installer_dependances.py 4` demande la source (GitHub, dossier ou zip) et la portée (projet complet, ou seulement les deux scripts).
Sans question : `--source github|dossier|zip --chemin <dossier ou zip> --portee tout|scripts`. Seuls les fichiers qui diffèrent sont remplacés
(les anciennes versions sont copiées dans `../agentgy_sauvegardes_maj/`, trois jeux gardés) ; depuis un dossier ou un zip, rien n'est supprimé. Depuis GitHub, seule l'arborescence est lue d'abord (le dépôt n'est jamais rapatrié en entier) : les empreintes des fichiers locaux sont comparées à celles du dépôt,
et seuls les fichiers qui diffèrent sont téléchargés (`.git` reste de l'ordre du Mo). Depuis un zip, rien n'est extrait en entier : seuls les fichiers qui diffèrent sont écrits.
Les paquets sont
réinstallés si `requirements_*.txt` a changé, puis migrations, fichiers statiques et rechargement du site. Les données (`media/`, bases, clés, `logs/`) ne sont jamais écrasées.
Sans projet à l'endroit prévu, le dépôt est cloné puis installé.

**Migrations protégées** (`migrations_sures.py`, appelé à chaque mise à jour ; utilisable seul, `--plan` pour voir sans rien faire) : analyse d'abord les migrations en attente, sur la base
principale et sur chaque fichier d'entreprise. Suppression de colonne ou de table, SQL brut, code Python sans annulation, colonne rétrécie ou NULL interdit sont **bloqués** :
aucune donnée n'est touchée et le site n'est pas rechargé ; après relecture, `--autoriser-destructif`. Sinon : copie « avant_migration » des bases seules (dossier `sauvegardes/`, une seule copie gardée), migration,
puis contrôle du nombre de lignes de chaque table avant/après (alerte si une table en perd). Une base neuve n'est pas bloquée.

**Quota disque (compte gratuit PythonAnywhere : 512 Mo)** : `[mise_a_jour] quota_mo = 512` dans `installer.conf` (0 = aucun contrôle). Avant d'écrire quoi que ce soit, la mise à jour et les migrations
vérifient qu'il reste la place (fichiers à remplacer + leurs anciennes versions, ou copie des bases + reconstruction de table par SQLite + marge) ; sinon elles s'arrêtent sans rien modifier.
Deux jeux d'anciennes versions gardés (`../agentgy_sauvegardes_maj/`), une copie de base (`sauvegardes/`), `.git` nettoyé après chaque mise à jour, chaque fichier écrit sous un nom temporaire puis renommé.

Tout est dans `installer.conf` (créé au premier lancement par des questions ; droits 600 ; ignoré par git) : adresse du dépôt, branche, jeton d'accès
(dépôt privé : jeton à lecture seule du contenu), niveau, dossier, virtualenv, fichiers à conserver, rechargement du site. Le jeton ne passe que par un en-tête
git : il n'est écrit ni dans `.git/config` ni dans une adresse.

Puis `~/venv-agentgy/bin/python manage.py createsuperuser` (une seule fois), l'onglet Web et Reload. Équivalent à la main :

```bash
cd ~/agentgy_groupe_une_table
python3.12 -m venv --system-site-packages ~/venv-agentgy && source ~/venv-agentgy/bin/activate   # Python >= 3.12
pip install --no-cache-dir -r requirements_primaire.txt
python manage.py migrate
python manage.py createcachetable          # tables de cache (limitation de débit, SIEM)
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## 2. PythonAnywhere (onglet Web)

- Source code : `/home/<vous>/agentgy_groupe_une_table` ; Virtualenv : `/home/<vous>/venv-agentgy`.
- Fichier WSGI : remplacer le contenu par

```python
import os, sys
chemin = "/home/<vous>/agentgy_groupe_une_table"
if chemin not in sys.path:
    sys.path.insert(0, chemin)
os.environ["DJANGO_SETTINGS_MODULE"] = "gyplatform.settings"
os.environ["GYPLATFORM_HTTPS"] = "1"
os.environ["GYPLATFORM_DERRIERE_PROXY"] = "1"
os.environ["GYPLATFORM_HOSTS"] = "<vous>.pythonanywhere.com"
os.environ["GYPLATFORM_CSRF_ORIGINS"] = "https://<vous>.pythonanywhere.com"
os.environ["GYPLATFORM_SECRET_KEY"] = "<une longue clé aléatoire>"
# IA : adresse du serveur llama.cpp (voir §4)
# os.environ["LLAMA_HOST"] = "203.0.113.10"
# os.environ["LLAMA_PORT"] = "8080"
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

- Static files (facultatif, WhiteNoise les sert déjà) : `/static/` → `.../staticfiles`.
- Bouton **Reload** après chaque modification.

## 3. Votre propre système (là où ça tournait avant)

Mêmes commandes, puis `python manage.py runserver 0.0.0.0:8000` ou Gunicorn
(`gunicorn gyplatform.wsgi`). Aucun réglage supplémentaire : sans variables d'environnement, le projet
se comporte comme avant (HTTP, tous les hôtes acceptés, clé secrète générée dans `media/systeme/`).

## 4. IA de génération : désigner l'adresse IP

Le moteur parle à un serveur llama.cpp (API compatible OpenAI) par adresse IP et port :

- globalement : variables `LLAMA_HOST` et `LLAMA_PORT` (défaut `127.0.0.1:8080`) ;
- par entreprise : champs « moteur » (hôte, port, modèle) de la configuration IA, qui l'emportent sur les variables.

Sans serveur joignable, seules les fonctions de génération par IA échouent, avec un message ; le reste fonctionne.
Le serveur doit accepter les connexions entrantes depuis PythonAnywhere (port ouvert, IP publique).
Note : les comptes gratuits PythonAnywhere ne sortent que vers une liste blanche de sites ; un serveur
IA sur IP libre exige un compte payant.

## 5. Fonctions facultatives

| Fonction | Sans le module |
|---|---|
| Aperçu des fichiers Office en PDF | LibreOffice absent → téléchargement proposé |
| Contrôle d'intégrité des médias | ffmpeg absent → décodage sauté, autres contrôles et antivirus maintenus |
| Mixage audio+vidéo des flux | ffmpeg absent → deux fichiers séparés |
| Son MP3/OGG, vision, caméra | `pydub`, `opencv-python-headless` à installer (voir `requirements.txt`) |
| Aperçu vidéo des PPTX | `imageio-ffmpeg` à installer |
| Polars, cartes folium, SVG, GeoIP | facultatifs, repli automatique (voir `requirements.txt`) |
| Temps réel (WebSocket) | désactivé : cloche et statuts se rafraîchissent par requêtes HTTP |

Module facultatif non installé : le démarrage n'est jamais bloqué. Quand une fonctionnalité en a besoin, un pop-up traduit annonce qu'elle est
bloquée et propose de contacter l'administrateur par WhatsApp et/ou e-mail. Les deux contacts se règlent dans la page « Entreprises » du
super-administrateur (numéro WhatsApp, adresse e-mail ; un canal vide n'est pas proposé). Code : `engine/modules_manquants.py`.

## 6. Retiré dans cette version

Paiements par opérateur et par carte, USSD, SMS (envoi, double authentification SMS), Spark et calcul réparti,
suivi continu de flux par processus détaché, Celery/Redis/Channels/Daphne. Le wallet interne (recharge en espèces
chez un agent, transferts) est conservé. Les anciennes tables restent en base pour ne pas casser les migrations.


## Base de données et sauvegarde (super-administrateur)

Tout est conditionné dans `gyplatform/settings.py` par la variable d'environnement `GYPLATFORM_BASE` :

- `auto` (défaut) : PostgreSQL si `DATABASE_URL` (ou `GYPLATFORM_PG_NOM`) est défini, sinon SQLite ;
- `postgresql` : UNE base unifiée pour toutes les entreprises (`DATABASE_URL=postgres://utilisateur:mot_de_passe@hôte:5432/base`,
  pilote : `pip install --user "psycopg[binary]"`). L'isolation entre entreprises est assurée par l'application ;
- `sqlite` : un fichier `media/systeme/db.sqlite3`.

Le super-administrateur dispose de la page **Sauvegarde et restauration** (`/plateforme/sauvegarde/`) : un seul zip contient la base et tous
les fichiers ; la restauration refait d'abord une sauvegarde de sécurité de l'état actuel. Le zip contient les clés secrètes : le garder confidentiel.
Une sauvegarde SQLite se restaure sur SQLite, une sauvegarde PostgreSQL sur PostgreSQL.

## SQLite : un fichier par entreprise (optionnel)

Réglage : `GYPLATFORM_BASE=sqlite_par_entreprise` (jamais choisi par `auto`).

- Base système `media/systeme/db.sqlite3` : comptes, sessions, entreprises, membres, clés API, SIEM, partages inter-agents.
- Une base par entreprise `media/systeme/entreprises/<id>.sqlite3` : ses tables, enregistrements, graphiques, ontologie, documents…
  Créée et migrée automatiquement à la première utilisation.
- Passage d'une base existante : sauvegarder, arrêter l'application, puis
  `GYPLATFORM_BASE=sqlite_par_entreprise python manage.py decouper_base_par_entreprise` (copie + contrôle des comptes ;
  `--purger` retire ensuite les données de la base système).
- Après une nouvelle migration du code : `python manage.py migrate` puis, pour chaque fichier, `python manage.py migrate --database ent_<id>`.
- Partage inter-agents : dans ce mode, seul le partage d'une **table** en lecture seule est disponible.
- La sauvegarde/restauration du super administrateur inclut tous les fichiers d'entreprise (copies à chaud cohérentes).

### Contrôles du mode « un fichier par entreprise »
- Avant la mise en service : `python manage.py verifier_base_par_entreprise` (liste les jointures entre fichiers à corriger).
- Après chaque mise à jour du code : `python manage.py migrer_entreprises` (système + chaque fichier ; une entreprise en échec n'arrête pas les autres).
- Les comptes sont copiés SANS mot de passe dans chaque fichier (jointures seulement) ; les identifiants des nouvelles lignes
  d'une entreprise commencent à `id_entreprise × 10⁹` ; le partage d'une table est refusé (409) si le destinataire a déjà une table de même numéro.
- Sauvegardes : `manage.py sauvegarder` et la page du super administrateur copient chaque fichier à chaud, de façon cohérente.

## Essai d'une semaine, blocage et déblocage des entreprises

- Une **nouvelle entreprise** a un essai (7 jours par défaut). À l'échéance, son interface se bloque : ses utilisateurs voient la page
  « Accès suspendu » avec le bouton **Contacter sur WhatsApp** (numéro et message pré-remplis) ; les appels API reçoivent un 402.
- **Page du super-administrateur** : `/plateforme/entreprises/` — liste des entreprises (essai, actif, échu, bloqué), boutons
  *Débloquer (30 jours)*, *Prolonger de N jours*, *Bloquer*, et réglages : numéro WhatsApp (format international), message
  pré-rempli (`{entreprise}` et `{fin}` sont remplacés), durée de l'essai, durée d'un déblocage.
- Un déblocage part de la fin actuelle si elle est future, sinon de maintenant. Les entreprises **d'avant** cette mise à jour n'ont pas
  d'échéance (sans limite) : leur donner une échéance avec « Prolonger ». Le super-administrateur et les entreprises dont il est membre ne sont jamais bloqués.
- Migration : `python manage.py migrate` (0006). Tous les textes sont dans le catalogue i18n (`plateforme.acces.*`, `plateforme.entreprises.*`).
