"""
Le code genere par l'IA ne doit JAMAIS etre execute directement sans controle.
Sandbox minimal :
- whitelist stricte des modules importables ;
- builtins restreints (pas de open/exec/eval/__import__ libre) ;
- timeout dur via un processus separe ;
- seule la fonction `executer(dataframe)` est appelee, rien d'autre.

Le DataFrame passe a executer(dataframe) a les bons dtype pandas appliques
colonne par colonne selon le schema EAV de la table (via EAV_DTYPE_MAP).
Le script genere par l'IA NE doit JAMAIS retyper les colonnes lui-meme.
"""
from engine.i18n import td
from engine.i18n import tf, tl
from engine.i18n import t
import ast
import logging
import multiprocessing
import os
import re
import traceback
from engine.i18n import t as _gt  # noqa: E402

_LOGGER = logging.getLogger(__name__)

# Identifiants refusés par _VisiteurSecuriteAST (introspection / accès système) ;
# les dunder (__x__) sont déjà refusés séparément dans visit_Name.
IDENTIFIANTS_INTERDITS = frozenset({
    "getattr", "setattr", "delattr", "type", "object", "eval", "exec", "compile",
    "open", "input", "breakpoint", "globals", "locals", "vars", "dir",
})
# Fonctions de bibliothèques AUTORISÉES qui lisent des fichiers/URL ou désérialisent du code
# (pd.read_pickle = exécution de code arbitraire, pd.read_csv("/etc/passwd"), np.load, pd.eval…).
# RLIMIT_FSIZE bloque déjà l'écriture ; ceci bloque la LECTURE et la désérialisation (audit 2026-09-23).
ATTRIBUTS_INTERDITS = frozenset({
    "load", "loadtxt", "genfromtxt", "fromfile", "memmap", "fromregex", "ctypeslib",
    "eval", "system", "popen", "subprocess", "urlopen", "unpickle", "read_pickle", "imread",
    "to_pickle", "savefig", "tofile", "save", "savez", "savetxt", "DataSource",
    # §sécurité (audit 2026-09-26) — évasion du bac à sable via str.format() :
    # "{0.__class__.__mro__[1].__subclasses__()}".format(obj) atteint les
    # attributs dunder d'un objet SANS jamais produire de nœud ast.Attribute
    # (le chemin pointé vit dans une chaîne littérale, invisible à
    # visit_Attribute/visit_Name), contournant donc entièrement le filtre
    # anti-introspection ci-dessous — vérifié : ça expose bien
    # __subclasses__ et permet de remonter jusqu'à subprocess.Popen etc.
    # format_map() partage la même mécanique et est bloqué pour la même
    # raison.
    "format", "format_map",
})
# §étanchéité (audit 2026-11-20) — trous restants du filtre AST :
#  - cadres de pile / générateurs / traceback : `gen.gi_frame.f_back.f_globals` remonte jusqu'aux globals du processus
#    (donc aux vrais builtins) SANS aucun nom dunder ; `.mro()` remonte aux classes de base ;
#  - modules d'infrastructure atteignables PAR ATTRIBUT depuis pandas/numpy/scipy/matplotlib (`pd.io.common.os.open`,
#    `np.lib.npyio.os`…) : lecture de fichiers (/proc/<ppid>/environ), exécution (`os.execv`, `os.spawn*`) ;
#  - `DataFrame.query()` évalue une CHAÎNE (attributs résolus par pandas, invisibles au filtre AST).
ATTRIBUTS_INTERDITS = ATTRIBUTS_INTERDITS | frozenset({
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await", "ag_frame", "ag_code", "ag_await",
    "f_back", "f_globals", "f_locals", "f_builtins", "f_code", "f_trace", "tb_frame", "tb_next",
    "co_code", "co_consts", "co_names", "func_globals", "func_code", "mro", "query",
    "os", "sys", "io", "builtins", "subprocess", "socket", "ctypes", "shutil", "pathlib", "importlib", "posix", "nt",
    "pickle", "marshal", "tempfile", "inspect", "gc", "threading", "multiprocessing", "sysconfig",
    "open", "fdopen", "get_handle", "file_exists", "urlretrieve", "Popen", "fork", "forkpty",
    "execl", "execle", "execlp", "execv", "execve", "execvp", "spawnl", "spawnv", "spawnlp", "spawnvp", "posix_spawn",
    "listdir", "scandir", "environ", "getenv", "putenv", "chdir", "chmod", "chown", "symlink", "unlink", "mkdir", "makedirs",
    "import_module", "load_module", "find_module", "modules",
})
# Modules qu'AUCUNE configuration (ConfigurationIA.modules_autorises_*, en-tête « # modules: » d'un plugin) ne peut rouvrir :
# la liste blanche est un réglage d'entreprise, jamais un droit d'accès au système hôte. Testé sur chaque segment du nom
# (« pandas.io.common » est refusé comme « os »).
MODULES_JAMAIS_AUTORISES = frozenset({
    "os", "sys", "io", "builtins", "subprocess", "socket", "ssl", "select", "selectors", "asyncio", "ctypes", "cffi",
    "shutil", "pathlib", "glob", "tempfile", "fcntl", "mmap", "resource", "pty", "signal", "threading", "multiprocessing",
    "concurrent", "importlib", "imp", "runpy", "code", "codeop", "inspect", "gc", "marshal", "pickle", "dill", "cloudpickle",
    "joblib", "shelve", "dbm", "sqlite3", "http", "urllib", "ftplib", "smtplib", "telnetlib", "requests", "httpx", "aiohttp",
    "webbrowser", "platform", "sysconfig", "site", "posix", "nt", "pwd", "grp", "zipimport", "pkgutil", "pdb", "trace",
    "django", "celery", "redis", "psycopg2", "pymysql", "engine", "gyplatform", "IA",
})


def _module_interdit(nom: str) -> bool:
    """Vrai si un segment du nom de module (« a.b.c ») figure dans MODULES_JAMAIS_AUTORISES."""
    return any(segment in MODULES_JAMAIS_AUTORISES for segment in (nom or "").split("."))


# Chemins de fichiers (2+ segments) masqués dans les messages d'erreur renvoyés à l'utilisateur.
_MOTIF_CHEMIN_INTERNE = re.compile(r"""(?:[A-Za-z]:)?(?:[\\/][^\s\\/:'"]+){2,}""")

# ── Erreurs recurrentes du modele ────────────────────────────────────────────
MOTIFS_INTERDITS = [
    (re.compile(r"(?<![\"'\w])(true|false|null)(?![\"'\w])"),
     tl("engine/executor.py:g:52:6")),
    (re.compile(r"\binput\s*\("),
     tl("engine/executor.py:g:54:6")),
    (re.compile(r"\bopen\s*\("),
     tl("engine/executor.py:g:56:6")),
]

DEFAULT_MODULES_AUTORISES_SCRIPT = {
    "pandas", "numpy", "scipy", "statsmodels", "sklearn",
    "matplotlib", "matplotlib.pyplot", "networkx", "math", "statistics", "json",
    "prophet",
}
DEFAULT_MODULES_AUTORISES_DOCUMENT = {"datetime", "math", "json", "decimal"}
# Canal "formule" (tables de calcul) : mêmes libs statistiques que le canal
# script, plus decimal (précision monétaire) et prophet (séries temporelles).
DEFAULT_MODULES_AUTORISES_FORMULE = {
    "pandas", "numpy", "scipy", "statsmodels", "math", "statistics",
    "decimal", "prophet",
}
# Canal "graphique_bi" (tableaux de bord BI) : socle statistique + matplotlib,
# plus scipy (distributions, régressions, lissage) et prophet (prévisions) —
# un widget ne fait que dessiner, jamais d'accès externe.
DEFAULT_MODULES_AUTORISES_GRAPHIQUE_BI = {
    "pandas", "numpy", "scipy", "matplotlib", "matplotlib.pyplot", "math", "statistics",
    "prophet",
}
# Canal "ontologie" (couches personnalisées du graphe d'entourage) : le
# script ne fait que lire/enrichir un networkx.DiGraph déjà construit,
# aucun accès aux données brutes n'est nécessaire.
DEFAULT_MODULES_AUTORISES_ONTOLOGIE = {
    "networkx", "math", "statistics", "collections", "itertools",
}
# Canal "modele_ia" (scripts de modèles DL/ML ajoutés au Trainer, voir IA/plugins_modeles.py) :
# calcul numérique pur, sans pandas ni accès externe. Les deux moteurs réels du projet : scikit-learn (ML classique)
# et Flax + optax sur JAX (deep learning) ; numpy ne sert qu'à préparer les données et à stocker les poids.
DEFAULT_MODULES_AUTORISES_MODELE_IA = {"numpy", "sklearn", "jax", "flax", "optax", "math", "statistics", "json"}
# Liste par défaut d'avant l'ajout de jax/flax/optax : elle reste enregistrée telle quelle dans ConfigurationIA des
# entreprises existantes (valeur par défaut du champ au moment de la création de la ligne).
_ANCIEN_DEFAUT_MODULES_MODELE_IA = {"numpy", "sklearn", "math", "statistics", "json"}


def modules_modele_ia_a_jour(chaine) -> str:
    """Modules du canal modele_ia à utiliser. Une liste enregistrée identique à l'ancien défaut (sans jax/flax/optax)
    est remplacée par le défaut actuel ; une liste vide ou personnalisée par l'administrateur est rendue telle quelle."""
    chaine = (chaine or "").strip()
    if chaine and {m.strip() for m in chaine.split(",") if m.strip()} == _ANCIEN_DEFAUT_MODULES_MODELE_IA:
        return ", ".join(sorted(DEFAULT_MODULES_AUTORISES_MODELE_IA))
    return chaine

TIMEOUT_SECONDES = 120  # valeur par defaut si aucune configuration par table n'est fournie


def parse_modules_autorises(chaine: str, defaut: set) -> set:
    """Parse une liste de modules separes par des virgules (typiquement le
    champ ConfigurationIA.modules_autorises_script/document) en un set de
    noms de modules racine. Retombe sur `defaut` si la chaine est vide."""
    if not chaine or not chaine.strip():
        return set(defaut)
    modules = {m.strip() for m in chaine.split(",") if m.strip() and not _module_interdit(m.strip())}   # §étanchéité : voir MODULES_JAMAIS_AUTORISES
    return modules or set(defaut)


def _valider_code(code: str) -> str | None:
    """Verification statique avant exec(). Retourne un message d'erreur ou None."""
    for motif, description in MOTIFS_INTERDITS:
        if motif.search(code):
            return tf("engine/executor.py:g:105:20", v1=description)
    # Analyse AST (introspection, dunder, lecture de fichiers, désérialisation) : existait mais n'était
    # appelée nulle part — les 5 canaux d'exécution passent tous par ici (audit 2026-09-23).
    return _verifier_securite_ast(code)


def _builtins_surs(modules_autorises: set) -> dict:
    """Builtins de TOUS les canaux (une seule définition au lieu de 5 copies) : fonctions pures sans
    accès système. Ajouts 2026-09-23 : any/all/map/filter/reversed/divmod/pow/frozenset et les
    exceptions usuelles — sans elles, un « except Exception » ou un any() écrit par l'IA plantait
    (NameError) au lieu de fonctionner."""
    return {
        "__import__": _creer_import_restreint(modules_autorises),
        "len": len, "range": range, "min": min, "max": max, "sum": sum,
        "abs": abs, "round": round, "sorted": sorted, "enumerate": enumerate,
        "zip": zip, "list": list, "dict": dict, "set": set, "tuple": tuple, "frozenset": frozenset,
        "str": str, "int": int, "float": float, "bool": bool, "complex": complex, "print": print,
        "isinstance": isinstance, "any": any, "all": all, "map": map, "filter": filter,
        "reversed": reversed, "divmod": divmod, "pow": pow, "slice": slice, "iter": iter, "next": next,
        "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
        "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError, "ArithmeticError": ArithmeticError,
        "RuntimeError": RuntimeError, "StopIteration": StopIteration, "OverflowError": OverflowError,
        "True": True, "False": False, "None": None,
    }


def _creer_import_restreint(modules_autorises: set):
    """Fabrique une fonction __import__ restreinte fermee sur l'ensemble de
    modules autorises fourni (configurable par entreprise/canal, plutot
    qu'une whitelist figee dans le code). §étanchéité : les modules de
    MODULES_JAMAIS_AUTORISES sont refusés même s'ils figurent dans la liste
    configurée, et les imports relatifs (level > 0) sont refusés."""
    racines_autorisees = {m.split(".")[0] for m in modules_autorises if not _module_interdit(m)}

    def _import_restreint(nom, globals=None, locals=None, fromlist=(), level=0):
        racine = nom.split(".")[0]
        if level or racine not in racines_autorisees or _module_interdit(nom):
            raise ImportError(tf("engine/executor.py:g:140:31", v1=nom))
        return __import__(nom, globals, locals, fromlist, level)

    return _import_restreint


# ── Mapping type_eav → fonction de cast pandas ───────────────────────────────
# Chaque fonction prend une pd.Series et retourne une pd.Series castee.
# Les erreurs de cast sont silencieuses (NaN/NaT/pd.NA) pour ne pas planter
# sur des valeurs mal saisies dans le formulaire.

def _cast_serie(serie, type_eav: str):
    """Applique le cast pandas correct selon le type EAV. Retourne la serie
    castee. Ne leve jamais d'exception : les valeurs non-convertibles
    deviennent NaN / NaT / pd.NA selon le type."""
    import pandas as pd

    if type_eav in ("nombre", "note", "barre_progression"):
        return pd.to_numeric(serie, errors="coerce").astype("Int64")

    if type_eav in ("decimal", "monnaie", "pourcentage", "constante"):
        return pd.to_numeric(serie, errors="coerce").astype("Float64")

    if type_eav in ("booleen", "switch"):
        # valeur_typee() retourne deja True/False/None, mais on renforce
        def _to_bool(v):
            if v is None:
                return pd.NA
            if isinstance(v, bool):
                return v
            s = str(v).lower().strip()
            if s in ("1", "true", "vrai", "oui", "yes"):
                return True
            if s in ("0", "false", "faux", "non", "no", ""):
                return False
            return pd.NA
        return serie.map(_to_bool).astype("boolean")

    if type_eav in ("date", "datetime", "horodatage"):
        return pd.to_datetime(serie, errors="coerce", dayfirst=True)

    if type_eav == "heure":
        # On garde en string ; le script peut faire pd.to_datetime si besoin
        return serie.astype(object)

    if type_eav == "duree":
        # Tentative timedelta, sinon string brute
        try:
            return pd.to_timedelta(serie, errors="coerce")
        except Exception:
            return serie.astype(object)

    if type_eav == "choix_multiple":
        # valeur_typee() retourne une list ; on la garde telle quelle
        return serie.astype(object)

    # Tous les types texte / email / url / telephone / couleur / choix_unique
    # / fichier / image → string pandas nullable
    return serie.where(serie.notna(), other=None).astype(object)


def _appliquer_schema(dataframe, schema_eav: dict):
    """Cast chaque colonne du DataFrame selon son type EAV.
    schema_eav = {slug: type_eav} tel que retourne par metadata.schema_pour_executor().
    Les colonnes absentes du schema sont laissees intactes (object)."""
    import pandas as pd

    for col in dataframe.columns:
        type_eav = schema_eav.get(col)
        if type_eav is None:
            continue
        try:
            dataframe[col] = _cast_serie(dataframe[col], type_eav)
        except Exception:
            # Derniere securite : si le cast echoue pour une raison inattendue,
            # on laisse la colonne en l'etat plutot que de planter.
            pass
    return dataframe


CLES_RESULTAT = {
    "titre": str,
    "pourquoi": str,
    "comment": str,
    "table_statistiques": dict,
    "figures": list,
    "explication": str,
    "analyse_predictive": str,
    "kpi": dict,
    "conclusion": str,
    "recommandations": list,
}


def _valider_structure_resultat(resultat, structure_attendue) -> str | None:
    """Verifie que le dict retourne par executer(dataframe) respecte
    EXACTEMENT la structure attendue : memes cles (ni manquante ni en trop),
    dans le meme ordre, types corrects, jamais vide. Retourne un message
    d'erreur precis (reutilisable comme feedback pour 'Insister'), ou None
    si la structure est valide."""
    cles_attendues = list(structure_attendue)

    if not isinstance(resultat, dict):
        return (
            tf("engine/executor.py:g:244:13", v1=cles_attendues, v2=type(resultat).__name__)
        )

    cles_manquantes = [c for c in cles_attendues if c not in resultat]
    if cles_manquantes:
        return (
            tf("engine/executor.py:g:251:13", v1=', '.join(cles_manquantes), v2=cles_attendues)
        )

    cles_en_trop = [c for c in resultat if c not in structure_attendue]
    if cles_en_trop:
        return (
            tf("engine/executor.py:g:258:13", v1=', '.join(cles_en_trop), v2=cles_attendues)
        )

    cles_obtenues = list(resultat.keys())
    if cles_obtenues != cles_attendues:
        return (
            tf("engine/executor.py:g:265:13", v1=cles_attendues, v2=cles_obtenues)
        )

    for cle, type_attendu in structure_attendue.items():
        valeur = resultat.get(cle)
        if not isinstance(valeur, type_attendu):
            return (
                tf("engine/executor.py:g:273:17", v1=cle, v2=type_attendu.__name__, v3=type(valeur).__name__)
            )
        if valeur in ({}, [], ""):
            return tf("engine/executor.py:g:277:20", v1=cle)
    return None


def _forcer_backend_matplotlib_agg():
    """A appeler en tout DEBUT de chaque sandbox, avant _valider_code/exec().

    Le backend matplotlib doit etre fixe a "Agg" AVANT le tout premier import
    de matplotlib.pyplot dans ce processus : matplotlib choisit son backend
    (potentiellement interactif, Tk/Qt) des cet import, une seule fois par
    processus. Sur un serveur sans interface graphique, un backend interactif
    fait planter le rendu ("no display", erreur Tcl/Tk...) au lieu de
    produire l'image. Comme le code utilisateur peut importer matplotlib.pyplot
    lui-meme (canal graphique_bi, et canal script generique qui l'autorise
    aussi par defaut), on fixe Agg ici plutot que d'esperer que chaque script
    genere le fasse dans le bon ordre. Sans effet si matplotlib n'est pas
    installe ou pas utilise par ce canal."""
    try:
        import matplotlib
        matplotlib.use("Agg")
    except Exception:
        pass


def _executer_dans_sandbox(code, donnees_dict, schema_eav, queue, structure_attendue, modules_autorises,
                            timeout_secondes=None):
    try:
        _isoler_processus_enfant(timeout_secondes)
        _forcer_backend_matplotlib_agg()
        from engine.moteur_donnees import activer_bascule_scripts
        activer_bascule_scripts()  # bascule Polars des scripts (active par défaut, voir moteur_donnees.BASCULE_SCRIPTS)
        erreur_validation = _valider_code(code)
        if erreur_validation:
            queue.put({"ok": False, "erreur": erreur_validation})
            return

        import pandas as pd

        from engine.moteur_donnees import dataframe_depuis
        dataframe = dataframe_depuis(donnees_dict)
        dataframe = _appliquer_schema(dataframe, schema_eav)

        builtins_restreints = _builtins_surs(modules_autorises)
        espace_global = {"__builtins__": builtins_restreints}
        espace_local = {}

        exec(code, espace_global, espace_local)

        fonction = espace_local.get("executer") or espace_global.get("executer")
        if fonction is None:
            queue.put({"ok": False, "erreur": t("engine/executor.py:296:48")})
            return

        resultat = fonction(dataframe)

        erreur_structure = _valider_structure_resultat(resultat, structure_attendue)
        if erreur_structure:
            queue.put({"ok": False, "erreur": erreur_structure})
            return

        queue.put({"ok": True, "resultat": resultat})
    except Exception:
        queue.put({"ok": False, "erreur": traceback.format_exc(limit=3)})


def _attendre_resultat(processus, queue, delai):
    """Lit le résultat PENDANT que l'enfant tourne, puis attend sa fin (audit 2026-09-23).
    L'ancien ordre (join() puis queue.get()) bloquait dès que le résultat dépassait le tampon du tube
    (~64 Ko : grande image PNG, gros dictionnaire, graphe d'ontologie) : l'enfant ne pouvait pas finir
    tant que personne ne lisait → faux « dépassement du délai ». Un enfant mort sans résultat est
    détecté tout de suite (pas d'attente du délai complet)."""
    import queue as _file
    import time as _temps
    fin = _temps.monotonic() + delai
    resultat = None
    while _temps.monotonic() < fin:
        try:
            resultat = queue.get(timeout=0.2)
            break
        except _file.Empty:
            if not processus.is_alive():
                try:
                    resultat = queue.get(timeout=0.5)  # dernier message éventuellement en transit
                except _file.Empty:
                    pass
                break
    processus.join(5)
    if processus.is_alive():
        processus.terminate()
        processus.join()
    if resultat is None:
        if _temps.monotonic() >= fin:
            return {"ok": False, "_delai": True, "erreur": tf("engine/executor.py:g:366:60", v1=delai)}
        return {"ok": False, "erreur": t("engine/executor.py:339:41")}
    return resultat


def executer_script_securise(code: str, donnees_dict: dict, schema_eav: dict | None = None,
                              timeout_secondes: int | None = None,
                              modules_autorises: set | None = None) -> dict:
    """Lance le script dans un processus isole avec timeout dur.
    schema_eav = {slug: type_eav} — si None, aucun cast n'est applique (compat. descendante).
    timeout_secondes : delai max configurable par table (TableDynamique.timeout_execution_secondes) ;
    si non fourni, utilise TIMEOUT_SECONDES par defaut.
    modules_autorises : set de modules importables dans le bac a sable (configurable par
    entreprise via ConfigurationIA.modules_autorises_script) ; si None, retombe sur
    DEFAULT_MODULES_AUTORISES_SCRIPT.
    Le dict retourne par executer(dataframe) doit respecter EXACTEMENT CLES_RESULTAT
    (une seule structure, quel que soit le contexte d'appel : analyse ponctuelle ou skill).
    Retourne {"ok": bool, "resultat"|"erreur": ...}."""
    delai = timeout_secondes if timeout_secondes and timeout_secondes > 0 else TIMEOUT_SECONDES
    queue = _CONTEXTE_SANDBOX.Queue()
    processus = _CONTEXTE_SANDBOX.Process(
        target=_executer_dans_sandbox,
        args=(code, donnees_dict, schema_eav or {}, queue, CLES_RESULTAT,
              modules_autorises or DEFAULT_MODULES_AUTORISES_SCRIPT, delai),
    )
    processus.start()
    return _attendre_resultat(processus, queue, delai)

# ── Canal document : executer(enregistrement) reçoit un dict, pas un dataframe ──

CLES_DOCUMENT = {
    "type_document":  str,
    "titre":          str,
    "reference":      str,
    "date":           str,
    "emetteur":       dict,
    "destinataire":   dict,
    "lignes":         list,
    "totaux":         dict,
    "notes":          str,
    "pied_page":      str,
}


def _executer_dans_sandbox_dict(code, enregistrement_dict, queue, structure_attendue, modules_autorises,
                                 timeout_secondes=None):
    """Sandbox pour executer(enregistrement) — reçoit un dict Python, pas un dataframe."""
    try:
        _isoler_processus_enfant(timeout_secondes)
        _forcer_backend_matplotlib_agg()
        erreur_validation = _valider_code(code)
        if erreur_validation:
            queue.put({"ok": False, "erreur": erreur_validation})
            return

        builtins_restreints = _builtins_surs(modules_autorises)
        espace_global = {"__builtins__": builtins_restreints}
        espace_local = {}

        exec(code, espace_global, espace_local)

        fonction = espace_local.get("executer") or espace_global.get("executer")
        if fonction is None:
            queue.put({"ok": False, "erreur": t("engine/executor.py:400:48")})
            return

        resultat = fonction(enregistrement_dict)

        erreur_structure = _valider_structure_resultat(resultat, structure_attendue)
        if erreur_structure:
            queue.put({"ok": False, "erreur": erreur_structure})
            return

        queue.put({"ok": True, "resultat": resultat})
    except Exception:
        import traceback as _tb
        queue.put({"ok": False, "erreur": _tb.format_exc(limit=3)})


def executer_script_securise_dict(code: str, enregistrement: dict,
                                   timeout_secondes: int | None = None,
                                   modules_autorises: set | None = None) -> tuple:
    """Exécute executer(enregistrement) dans un sandbox isolé.
    modules_autorises : set de modules importables (configurable par entreprise via
    ConfigurationIA.modules_autorises_document) ; si None, retombe sur
    DEFAULT_MODULES_AUTORISES_DOCUMENT.
    Retourne (resultat_dict_ou_None, erreur_str_ou_None).
    """
    delai = timeout_secondes if timeout_secondes and timeout_secondes > 0 else TIMEOUT_SECONDES
    queue = _CONTEXTE_SANDBOX.Queue()
    processus = _CONTEXTE_SANDBOX.Process(
        target=_executer_dans_sandbox_dict,
        args=(code, enregistrement, queue, CLES_DOCUMENT,
              modules_autorises or DEFAULT_MODULES_AUTORISES_DOCUMENT, delai),
    )
    processus.start()
    res = _attendre_resultat(processus, queue, delai)
    if res.get("ok"):
        return res["resultat"], None
    return None, res.get("erreur", td("engine/executor.py:g:461:36"))


# ── Canal formule (tables de calcul) : def formule(cible) reçoit une
# pandas.Series (une colonne) ou un pandas.DataFrame (plusieurs colonnes),
# selon FormuleCalcul.cible_type. Retour libre (scalaire/liste/dict), tant
# que c'est sérialisable en JSON — voir _normaliser_resultat_formule. ──

def _normaliser_resultat_formule(valeur):
    """Convertit récursivement le retour de formule(cible) en quelque chose
    de sérialisable en JSON (numpy/pandas -> types Python natifs)."""
    try:
        import numpy as np
        import pandas as pd
    except Exception:
        np = None
        pd = None

    if valeur is None or isinstance(valeur, (bool, int, float, str)):
        return valeur
    if np is not None and isinstance(valeur, np.generic):
        return valeur.item()
    if pd is not None and isinstance(valeur, (pd.Timestamp,)):
        return valeur.isoformat()
    if pd is not None and isinstance(valeur, pd.Series):
        return {str(k): _normaliser_resultat_formule(v) for k, v in valeur.items()}
    if pd is not None and isinstance(valeur, pd.DataFrame):
        return valeur.applymap(lambda v: _normaliser_resultat_formule(v)).to_dict(orient="records")
    if np is not None and isinstance(valeur, np.ndarray):
        return [_normaliser_resultat_formule(v) for v in valeur.tolist()]
    if isinstance(valeur, dict):
        return {str(k): _normaliser_resultat_formule(v) for k, v in valeur.items()}
    if isinstance(valeur, (list, tuple, set)):
        return [_normaliser_resultat_formule(v) for v in valeur]
    # Dernier recours : représentation texte (évite de planter sur un type
    # exotique retourné par une formule personnalisée mal écrite).
    return str(valeur)


def _executer_dans_sandbox_formule(code, cible, queue, modules_autorises, timeout_secondes=None):
    """Sandbox pour def formule(cible) — cible est un pandas.Series ou
    pandas.DataFrame déjà résolu et casté (voir calculs.resoudre_cible)."""
    try:
        _isoler_processus_enfant(timeout_secondes)
        _forcer_backend_matplotlib_agg()
        from engine.moteur_donnees import activer_bascule_scripts
        activer_bascule_scripts()  # bascule Polars des scripts (active par défaut, voir moteur_donnees.BASCULE_SCRIPTS)
        erreur_validation = _valider_code(code)
        if erreur_validation:
            queue.put({"ok": False, "erreur": erreur_validation})
            return

        builtins_restreints = _builtins_surs(modules_autorises)
        espace_global = {"__builtins__": builtins_restreints}
        espace_local = {}

        exec(code, espace_global, espace_local)

        fonction = espace_local.get("formule") or espace_global.get("formule")
        if fonction is None:
            queue.put({"ok": False, "erreur": t("engine/executor.py:493:48")})
            return

        resultat = fonction(cible)
        resultat = _normaliser_resultat_formule(resultat)
        queue.put({"ok": True, "resultat": resultat})
    except Exception:
        queue.put({"ok": False, "erreur": traceback.format_exc(limit=3)})


def executer_formule_securisee(code: str, cible, timeout_secondes: int | None = None,
                                modules_autorises: set | None = None) -> dict:
    """Exécute def formule(cible) dans un processus isolé avec timeout dur.
    `cible` : pandas.Series ou pandas.DataFrame déjà résolu(e).
    Retourne {"ok": bool, "resultat"|"erreur": ...}."""
    delai = timeout_secondes if timeout_secondes and timeout_secondes > 0 else TIMEOUT_SECONDES
    queue = _CONTEXTE_SANDBOX.Queue()
    processus = _CONTEXTE_SANDBOX.Process(
        target=_executer_dans_sandbox_formule,
        args=(code, cible, queue, modules_autorises or DEFAULT_MODULES_AUTORISES_FORMULE, delai),
    )
    processus.start()
    return _attendre_resultat(processus, queue, delai)

# ── Canal "ontologie" (couches personnalisées, voir models.CoucheOntologie
# et ontologie.construire_graphe) : def etendre(graphe) reçoit le
# networkx.DiGraph déjà construit, l'enrichit avec les mêmes g.add_node /
# g.add_edge que le graphe standard, et retourne graphe. ──

def _executer_dans_sandbox_ontologie(code, graphe, queue, modules_autorises, timeout_secondes=None):
    try:
        _isoler_processus_enfant(timeout_secondes)
        _forcer_backend_matplotlib_agg()
        erreur_validation = _valider_code(code)
        if erreur_validation:
            queue.put({"ok": False, "erreur": erreur_validation})
            return

        builtins_restreints = _builtins_surs(modules_autorises)
        espace_global = {"__builtins__": builtins_restreints}
        espace_local = {}

        exec(code, espace_global, espace_local)

        fonction = espace_local.get("etendre") or espace_global.get("etendre")
        if fonction is None:
            queue.put({"ok": False, "erreur": t("engine/executor.py:538:48")})
            return

        import networkx as nx
        resultat = fonction(graphe)
        if not isinstance(resultat, nx.DiGraph):
            queue.put({"ok": False, "erreur": t("engine/executor.py:544:48")})
            return
        queue.put({"ok": True, "resultat": resultat})
    except Exception:
        queue.put({"ok": False, "erreur": traceback.format_exc(limit=3)})


def executer_couche_ontologie_securisee(code: str, graphe, timeout_secondes: int | None = None,
                                         modules_autorises: set | None = None) -> dict:
    """Exécute def etendre(graphe) dans un processus isolé avec timeout dur,
    exactement comme les autres canaux. `graphe` : networkx.DiGraph déjà
    construit (voir ontologie.construire_graphe). Retourne
    {"ok": bool, "resultat": <networkx.DiGraph>} ou {"ok": False, "erreur": str}."""
    delai = timeout_secondes if timeout_secondes and timeout_secondes > 0 else TIMEOUT_SECONDES
    queue = _CONTEXTE_SANDBOX.Queue()
    processus = _CONTEXTE_SANDBOX.Process(
        target=_executer_dans_sandbox_ontologie,
        args=(code, graphe, queue, modules_autorises or DEFAULT_MODULES_AUTORISES_ONTOLOGIE, delai),
    )
    processus.start()
    return _attendre_resultat(processus, queue, delai)

# ── Canal "graphique_bi" (tableaux de bord BI) : def dessiner(dataframe,
# colonnes) reçoit un pandas.DataFrame déjà casté (toutes les colonnes de la
# table source, comme pour le canal script) + la liste ordonnée des slugs de
# colonnes sélectionnées pour CE widget. Doit retourner une figure
# matplotlib. La conversion en PNG base64 se fait ICI, à l'intérieur du
# processus sandbox : aucune figure matplotlib (non fiable à pickler, et de
# toute façon jamais souhaitable de la faire sortir du bac à sable) ne
# traverse la Queue — seul le texte base64 en sort. Aucune écriture disque à
# aucun moment (voir models.WidgetGraphiqueBI). ──

def _executer_dans_sandbox_graphique(code, donnees_dict, schema_eav, colonnes, queue, modules_autorises,
                                      timeout_secondes=None):
    repertoire_sandbox = None
    try:
        repertoire_sandbox = _isoler_processus_enfant(timeout_secondes)
        # Backend Agg fixe ICI, AVANT exec()/dessiner() : dessiner() importe
        # presque toujours matplotlib.pyplot lui-meme (ou au niveau module,
        # ou dans son corps), et matplotlib fige son backend des ce tout
        # premier import. Le faire apres avoir deja appele dessiner() (comme
        # avant ce correctif) arrivait trop tard : matplotlib tentait un
        # backend interactif, absent sur ce serveur -> plantage a l'affichage
        # du graphique (cf. bug remonte sur ce fichier).
        _forcer_backend_matplotlib_agg()
        from engine.moteur_donnees import activer_bascule_scripts
        activer_bascule_scripts()  # bascule Polars des scripts (active par défaut, voir moteur_donnees.BASCULE_SCRIPTS)
        erreur_validation = _valider_code(code)
        if erreur_validation:
            queue.put({"ok": False, "erreur": erreur_validation})
            return

        import pandas as pd

        from engine.moteur_donnees import dataframe_depuis
        dataframe = dataframe_depuis(donnees_dict)
        dataframe = _appliquer_schema(dataframe, schema_eav)

        builtins_restreints = _builtins_surs(modules_autorises)
        from .graphiques_degrade import trace_degrade
        # gy_trace_degrade(ax, x, y, couleur, lw=…, marker=…, label=…) : tracé d'une ÉVOLUTION en dégradé de sa couleur, du plus
        # sombre (début) au plus clair (fin) — à utiliser à la place de ax.plot pour toute courbe d'évolution.
        espace_global = {"__builtins__": builtins_restreints, "gy_trace_degrade": trace_degrade}
        espace_local = {}

        exec(code, espace_global, espace_local)

        fonction = espace_local.get("dessiner") or espace_global.get("dessiner")
        if fonction is None:
            queue.put({"ok": False, "erreur": t("engine/executor.py:598:48")})
            return

        figure = fonction(dataframe, list(colonnes))

        import matplotlib.pyplot as plt  # backend Agg deja fixe plus haut

        if figure is None or not hasattr(figure, "savefig"):
            queue.put({"ok": False, "erreur": t("engine/executor.py:608:48")})
            return

        # Conversion base64 dans le sandbox : rien d'autre que du texte ne sort.
        from .visualization import figure_vers_base64
        image_base64 = figure_vers_base64(figure)
        plt.close("all")

        queue.put({"ok": True, "resultat": image_base64})
    except Exception:
        queue.put({"ok": False, "erreur": traceback.format_exc(limit=3)})
    finally:
        _nettoyer_repertoire_sandbox(repertoire_sandbox)


def executer_graphique_bi_securise(code: str, donnees_dict: dict, colonnes: list,
                                    schema_eav: dict | None = None,
                                    timeout_secondes: int | None = None,
                                    modules_autorises: set | None = None) -> dict:
    """Lance def dessiner(dataframe, colonnes) dans un processus isolé avec
    timeout dur, exactement comme les autres canaux. Retourne
    {"ok": bool, "resultat": <str base64 PNG>} ou {"ok": False, "erreur": str}.
    modules_autorises : configurable par entreprise (à ajouter à
    ConfigurationIA si besoin, sur le même principe que
    modules_autorises_script/document/formule) ; si None, retombe sur
    DEFAULT_MODULES_AUTORISES_GRAPHIQUE_BI."""
    delai = timeout_secondes if timeout_secondes and timeout_secondes > 0 else TIMEOUT_SECONDES
    queue = _CONTEXTE_SANDBOX.Queue()
    processus = _CONTEXTE_SANDBOX.Process(
        target=_executer_dans_sandbox_graphique,
        args=(code, donnees_dict, schema_eav or {}, colonnes, queue,
              modules_autorises or DEFAULT_MODULES_AUTORISES_GRAPHIQUE_BI, delai),
    )
    processus.start()
    return _attendre_resultat(processus, queue, delai)


# ── Fonctions fusionnées depuis gscode ──

class _VisiteurSecuriteAST(ast.NodeVisitor):
    """Parcourt l'AST du code utilisateur et rejette tout ce qui permettrait
    de contourner le bac a sable par introspection Python, independamment
    des builtins fournis a exec()."""

    def __init__(self):
        self.erreur = None

    def _signaler(self, message, node):
        if self.erreur is None:
            ligne = getattr(node, "lineno", "?")
            self.erreur = tf("engine/executor.py:g:690:27", v1=ligne, v2=message)

    def visit_Name(self, node):
        if node.id in IDENTIFIANTS_INTERDITS:
            self._signaler(
                tf("engine/executor.py:g:698:17", v1=node.id), node
            )
        elif node.id.startswith("__") and node.id.endswith("__"):
            self._signaler(
                tf("engine/executor.py:g:702:17", v1=node.id), node
            )
        if isinstance(node.ctx, ast.Store) and node.id == "__builtins__":
            self._signaler(t("engine/executor.py:g:705:28"), node)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self._signaler(
                tf("engine/executor.py:g:711:17", v1=node.attr), node
            )
        elif node.attr in ATTRIBUTS_INTERDITS or node.attr.startswith(("read_", "_")):
            self._signaler(
                tf("engine/executor.py:g:716:17", v1=node.attr), node
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        # « from pandas import read_pickle » contournerait visit_Attribute.
        for alias in node.names:
            if alias.name in ATTRIBUTS_INTERDITS or alias.name.startswith(("read_", "_")) or alias.name == "*":
                self._signaler(tf("engine/executor.py:g:720:32", v1=alias.name), node)
        self.generic_visit(node)

    def visit_Global(self, node):
        self._signaler("declaration 'global' interdite.", node)

    def visit_Nonlocal(self, node):
        self._signaler("declaration 'nonlocal' interdite.", node)

    def _bloquer(self, message):
        def _visit(node):
            self._signaler(message, node)
            self.generic_visit(node)
        return _visit



def _verifier_securite_ast(code: str) -> str | None:
    """Analyse statique de l'AST avant toute execution : bloque
    l'introspection Python (dunder, getattr/setattr, type/object, eval/exec...)
    meme si __builtins__ est filtre a l'execution — un objet quelconque
    expose deja son modele interne independamment des builtins disponibles.
    Retourne un message d'erreur explicite, ou None si le code est accepte.
    Complement indispensable a `_valider_code` et aux builtins restreints ;
    aucun des deux mecanismes n'est suffisant seul."""
    try:
        arbre = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return tf("engine/executor.py:g:753:16", v1=exc)
    visiteur = _VisiteurSecuriteAST()
    visiteur.visit(arbre)
    return visiteur.erreur



def _isoler_processus_enfant(timeout_secondes: int | None = None):
    """A appeler en tout premier, a l'interieur du processus enfant isole,
    avant d'executer la moindre ligne de code utilisateur.

    Limites appliquees (best effort, POSIX/Linux) :
    - CPU / memoire / nombre de processus-fils / descripteurs de fichiers /
      taille de fichier ecrit, via le module `resource` ;
    - variables d'environnement retirees (aucun secret exploitable meme si
      un module autorise venait a les lire) ;
    - repertoire de travail jetable et isole.

    IMPORTANT (limite honnete) : ceci reste le meme utilisateur systeme et le
    meme noyau que le processus Django (pas de nouvel espace de noms, pas de
    seccomp, pas de systeme de fichiers en lecture seule). C'est une defense
    en profondeur utile en complement de l'analyse AST et des builtins
    restreints, PAS une isolation au niveau systeme d'exploitation. Pour une
    isolation plus forte (systeme de fichiers), il faut un conteneur ou bubblewrap."""
    import os
    
    # pre-import des modeule internes 
    for _nom_module in ("engine.moteur_donnees","engine.graphiques_degrade","engine.visualization"):
        try :
            __import__(_nom_module)
        except Exception as e :
            print(_nom_module, e)
    # Repertoires d'ecriture prepares AVANT RLIMIT_FSIZE=0 : tempfile.gettempdir()
    # valide chaque candidat en y ECRIVANT quelques octets ; avec FSIZE=0 cette
    # ecriture echoue partout et on obtient « No usable temporary directory
    # found » (matplotlib en a besoin pour son cache de polices/config, et les
    # variables d'environnement, dont HOME, sont retirees plus bas).
    repertoire_travail, repertoire_cache_mpl = _preparer_repertoires_sandbox()

    try:
        import resource

        delai_cpu = int(timeout_secondes) + 10 if timeout_secondes else TIMEOUT_SECONDES + 10
        resource.setrlimit(resource.RLIMIT_CPU, (delai_cpu, delai_cpu))

        limite_memoire = 1536 * 1024 * 1024  # 1,5 Go d'espace d'adressage (virtuel) : pandas + numpy + matplotlib en reservent bien plus que la memoire reellement utilisee
        try:
            resource.setrlimit(resource.RLIMIT_AS, (limite_memoire, limite_memoire))
        except (ValueError, OSError):
            # RLIMIT_AS peut echouer sur certains systemes/allocateurs
            # (ex: allocateurs qui reservent de larges plages virtuelles) ;
            # les autres limites restent actives.
            pass

        # Aucune ecriture disque possible (formules n'en ont jamais besoin).
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        # Peu de descripteurs de fichiers (ceux deja ouverts par
        # multiprocessing pour la Queue ne sont pas affectes).
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        # Limite basse de sous-processus/threads : empeche une bombe fork ;
        # laisse une marge pour les threads internes eventuels de numpy/BLAS.
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    except Exception:
        # 'resource' est POSIX-only ; en cas d'echec on continue avec les
        # autres protections (AST + builtins restreints + timeout dur du
        # multiprocessing.Process.join()).
        pass

    # Isolation noyau sans droits root (Linux) : nouveaux espaces de noms utilisateur + RESEAU (le code n'a plus aucun
    # acces reseau : pas d'exfiltration, pas de connexion a la base ni aux services locaux) et no_new_privs (aucune
    # elevation de privileges).
    # §étanchéité (audit 2026-11-20) : l'échec était IGNORÉ en silence (noyau/AppArmor/seccomp de conteneur qui refuse
    # unshare) : le code utilisateur tournait alors avec le réseau et les fichiers du serveur, sans que personne le sache.
    # Désormais fail-closed sous Linux : pas d'isolation => pas d'exécution. Pour un poste de DÉVELOPPEMENT dont le noyau
    # refuse les espaces de noms : GYPLATFORM_SANDBOX_STRICT=0 (à ne jamais poser en production).
    import sys as _sys
    code_prctl, code_unshare = -1, -1
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        code_prctl = libc.prctl(38, 1, 0, 0, 0)            # PR_SET_NO_NEW_PRIVS
        code_unshare = libc.unshare(0x10000000 | 0x40000000)  # CLONE_NEWUSER | CLONE_NEWNET
    except Exception:
        pass
    if (code_prctl != 0 or code_unshare != 0) and _sys.platform.startswith("linux") \
            and os.environ.get("GYPLATFORM_SANDBOX_STRICT", "1") != "0":
        raise RuntimeError(
            "Isolation du bac a sable indisponible (no_new_privs/unshare refuses par le noyau) : execution refusee. "
            "Autorisez les espaces de noms utilisateur pour ce compte, ou, en developpement uniquement, "
            "definissez GYPLATFORM_SANDBOX_STRICT=0.")

    # Retire toute variable d'environnement du processus enfant : meme avec
    # le contexte 'spawn' (nouvel interpreteur), on evite qu'un module
    # autorise expose par erreur des secrets (cles API, chaine de connexion...).
    for cle in list(os.environ.keys()):
        os.environ.pop(cle, None)
    _forcer_blas_mono_thread()  # ni secret ni multi-thread BLAS : voir _VARIABLES_BLAS_MONO

    # Variables minimales reposees apres le nettoyage : tout ce que Python /
    # matplotlib doivent savoir pour trouver un endroit ou lire leur cache.
    if repertoire_cache_mpl:
        os.environ["MPLCONFIGDIR"] = repertoire_cache_mpl
    os.environ["MPLBACKEND"] = "Agg"
    if repertoire_travail:
        os.environ["TMPDIR"] = repertoire_travail
        os.environ["HOME"] = repertoire_travail
        try:
            os.chdir(repertoire_travail)
        except Exception:
            pass
    return repertoire_travail


def _preparer_repertoires_sandbox():
    """Retourne (repertoire_de_travail_jetable, repertoire_cache_matplotlib).
    A appeler AVANT toute limite `resource`. Le cache matplotlib est partage et
    persistant (1re execution : construction du cache de polices, ensuite
    simple lecture) ; le repertoire de travail est cree par execution et
    supprime par _nettoyer_repertoire_sandbox. Best effort : (None, None) si
    rien n'est utilisable."""
    import os
    import tempfile

    repertoire_travail = None
    repertoire_cache = None
    try:
        base = tempfile.gettempdir()
        repertoire_travail = tempfile.mkdtemp(prefix="gyplatform_sandbox_", dir=base)
        repertoire_cache = os.path.join(base, f"gyplatform_mplcache_{os.getuid()}")
        os.makedirs(repertoire_cache, mode=0o700, exist_ok=True)
        # Echauffement : construit/charge le cache de polices maintenant (ecriture
        # encore permise) pour que le code utilisateur n'ait plus rien a ecrire.
        os.environ["MPLCONFIGDIR"] = repertoire_cache
        os.environ["MPLBACKEND"] = "Agg"
        try:
            import matplotlib.font_manager  # noqa: F401
        except Exception:
            pass  # matplotlib absent : les autres canaux n'en ont pas besoin
    except Exception:
        pass
    return repertoire_travail, repertoire_cache


def _nettoyer_repertoire_sandbox(repertoire_travail):
    """Supprime le repertoire de travail jetable (appele en fin d'execution)."""
    if not repertoire_travail:
        return
    try:
        import os
        import shutil
        os.chdir("/")
        shutil.rmtree(repertoire_travail, ignore_errors=True)
    except Exception:
        pass


# Contexte multiprocessing dedie a l'execution de code utilisateur : 'spawn'
# demarre un interpreteur Python tout neuf pour chaque execution (pas de
# fork), donc le processus enfant ne recoit JAMAIS de copie memoire du
# processus Django parent (pas de threads/locks/connexions DB heritees, pas
# de secrets deja charges en memoire). Plus couteux que 'fork' (~100-300ms
# de demarrage) mais c'est une vraie separation de processus plutot qu'une
# simple restriction de builtins. N'affecte que ce module : le multiprocessing
# utilise ailleurs dans le projet garde son comportement par defaut.
# §étanchéité (audit 2026-11-20) : ce contexte était défini mais JAMAIS utilisé — les 5 canaux lançaient leur enfant avec le contexte
# par défaut (fork sous Linux), donc une copie mémoire de Django (secrets chargés, connexions et sockets de la base) dans le
# processus qui exécute le code utilisateur. Tous les canaux passent maintenant par ce contexte. Surcharge de développement
# uniquement : GYPLATFORM_SANDBOX_CONTEXTE=fork.
_CONTEXTE_SANDBOX = multiprocessing.get_context(os.environ.get("GYPLATFORM_SANDBOX_CONTEXTE", "spawn"))

# Bibliotheques BLAS/OpenMP : un seul thread. OpenBLAS reserve un gros tampon
# memoire VIRTUEL par thread (un par coeur) ; sous la limite RLIMIT_AS du
# sandbox (_isoler_processus_enfant) ces reservations echouent
# (« OpenBLAS error: Memory allocation still failed after 10 retries, giving
# up ») et le processus s'arrete sans resultat (« crash »). Ces variables
# doivent etre posees AVANT le premier import de numpy dans l'enfant.
_VARIABLES_BLAS_MONO = {
    "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1",
}


def _forcer_blas_mono_thread():
    """Pose les variables ci-dessus dans l'environnement courant : dans le
    parent elles sont heritees par tout processus 'spawn' lance ensuite (donc
    lues avant l'import de numpy de l'enfant) ; dans l'enfant elles sont
    reposees apres le nettoyage de l'environnement."""
    import os
    for cle, valeur in _VARIABLES_BLAS_MONO.items():
        os.environ[cle] = valeur


_forcer_blas_mono_thread()



def _message_erreur_propre(exc: BaseException, contexte: str) -> str:
    """Journalise la trace complete cote serveur (pour le developpeur, dans
    les logs) et retourne un message COURT, categorise et nettoye — jamais de
    chemin de fichier interne, de secret ou de trace Python brute — adapte a
    une reponse API destinee a l'utilisateur (voir exigence de gestion des
    erreurs)."""
    _LOGGER.exception(_gt("rg5.msg.017"), contexte)

    if isinstance(exc, ImportError):
        # Deja un message court et propre, fabrique par _creer_import_restreint
        # (nom du module seulement, aucun chemin).
        return f"Module interdit : {exc}"
    if isinstance(exc, MemoryError):
        return t("engine/executor.py:g:874:16")
    if isinstance(exc, RecursionError):
        return t("engine/executor.py:g:876:16")
    if isinstance(exc, (ValueError, TypeError, KeyError, IndexError, AttributeError,
                         ZeroDivisionError, ArithmeticError, NameError)):
        message = _MOTIF_CHEMIN_INTERNE.sub("<interne>", str(exc))
        return tf("engine/executor.py:g:880:16", v1=type(exc).__name__, v2=message)
    return tf("engine/executor.py:g:881:12", v1=type(exc).__name__)



def _message_crash_processus(processus) -> str:
    """Message propre lorsque le processus enfant s'est arrêté sans mettre de
    résultat dans la queue (tué par le noyau plutôt que par une exception
    Python — ex : dépassement d'une limite `resource` dure, OOM-killer)."""
    exitcode = processus.exitcode
    if exitcode is not None and exitcode < 0:
        signum = -exitcode
        if signum in (9, 24):  # SIGKILL, SIGXCPU
            return t("engine/executor.py:g:893:20")
        return t("engine/executor.py:g:894:16")
    return t("engine/executor.py:g:895:12")

