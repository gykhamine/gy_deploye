"""IA/plugins_modeles.py — scripts de modèles DL/ML ajoutés au Trainer (canal modele_ia).

Un script = un fichier modeles_scripts/<NOM>.py qui définit NOM, entrainer() et predire()
(contrat : engine/inference.py::DEFAULT_REGLE_MODELE_IA). Il est validé (executor._valider_code)
puis exécuté dans les builtins restreints du bac à sable (executor._builtins_surs) : fonctions
seulement, pas de classe. Les modules autorisés à l'installation sont mémorisés dans le fichier
(ligne « # modules: … ») pour que le rechargement au démarrage applique la même liste."""
import functools
import json
import logging
import os
import re

logger = logging.getLogger(__name__)
DOSSIER = os.path.join(os.path.dirname(__file__), "modeles_scripts")
_NOM = re.compile(r"^[a-z][a-z0-9_]{2,29}$")
_ENTETE = re.compile(r"^# modules: (.*)\n", re.M)
_PROFIL = re.compile(r"^# profil: (.*)\n", re.M)
_CLES_PROFIL = ("court", "role", "donnees", "conseil")


def profil_plugin(nom):
    """Fiche du modèle (nom court, rôle, colonnes requises, conseil, itératif=DL/ML) lue dans son script, ou None."""
    try:
        with open(os.path.join(DOSSIER, nom + ".py"), encoding="utf-8") as f:
            return json.loads(_PROFIL.search(f.read()).group(1))
    except (OSError, AttributeError, ValueError):
        return None


def _charger(code):
    """Valide puis exécute `code` ; retourne (espace de noms, erreur)."""
    from engine import executor as E
    # La ligne « # profil: {...} » est un commentaire JSON (true/false) : elle ne doit pas être soumise au motif
    # qui interdit true/false/null (sinon aucun script avec fiche ne pouvait s'installer depuis l'interface).
    erreur = E._valider_code(_PROFIL.sub("", code))
    if erreur:
        return None, erreur
    m = _ENTETE.search(code)
    modules = E.parse_modules_autorises(m.group(1) if m else "", E.DEFAULT_MODULES_AUTORISES_MODELE_IA)
    ns = {"__builtins__": E._builtins_surs(modules)}
    try:
        exec(compile(code, "<modele_ia>", "exec"), ns)
    except Exception as exc:
        from engine.i18n import tf
        return None, tf("IA/plugins_modeles.py:execution", erreur=exc)
    if not (isinstance(ns.get("NOM"), str) and _NOM.match(ns["NOM"])
            and callable(ns.get("entrainer")) and callable(ns.get("predire"))):
        from engine.i18n import t
        return None, t("IA/plugins_modeles.py:script_invalide")
    return ns, None


def _enregistrer(ns):
    from .trainer import register_model_type
    entrainer = ns["entrainer"]
    acceptes = entrainer.__code__.co_varnames[:entrainer.__code__.co_argcount]

    @functools.wraps(entrainer)  # le Trainer et l'interface lisent la signature de entrainer()
    def train(*args, **kw):
        return entrainer(*args, **{k: v for k, v in kw.items() if k in acceptes})

    extraire = lambda r: (r["weights"], r["config"])
    register_model_type(ns["NOM"], train, ns["predire"], extraire, extraire, ns["NOM"])


def charger_scripts():
    """Enregistre chaque script valide du dossier (un script invalide est journalisé et ignoré)."""
    from .trainer import _REGISTRY
    from engine.i18n import t  # (manquant avant : NameError des qu'un script etait invalide, ce qui stoppait le chargement des suivants)
    if not os.path.isdir(DOSSIER):
        return
    for fichier in sorted(os.listdir(DOSSIER)):
        if not fichier.endswith(".py") or fichier[:-3] in _REGISTRY:   # NOM == nom du fichier (voir installer) : deja connu
            continue
        with open(os.path.join(DOSSIER, fichier), encoding="utf-8") as f:
            ns, erreur = _charger(f.read())
        if erreur:
            logger.warning(t("IA/plugins_modeles.py:g:64:28"), fichier, erreur)
        elif ns["NOM"] not in _REGISTRY:
            _enregistrer(ns)


def installer(code, modules="", profil=None):
    """Valide `code`, l'écrit dans DOSSIER et l'enregistre ; retourne (NOM, erreur)."""
    from .trainer import _REGISTRY, _register_all
    _register_all()
    code = _PROFIL.sub("", _ENTETE.sub("", code)).strip() + "\n"
    if profil:
        fiche = {k: str(profil.get(k) or "")[:300] for k in _CLES_PROFIL}
        fiche["iteratif"] = bool(profil.get("iteratif"))
        code = f"# profil: {json.dumps(fiche, ensure_ascii=False)}\n{code}"
    if modules.strip():
        code = f"# modules: {modules.strip()}\n{code}"
    ns, erreur = _charger(code)
    if erreur:
        return None, erreur
    if ns["NOM"] in _REGISTRY:
        from engine.i18n import tf
        return None, tf("IA/plugins_modeles.py:existe", nom=ns["NOM"])
    os.makedirs(DOSSIER, exist_ok=True)
    with open(os.path.join(DOSSIER, ns["NOM"] + ".py"), "w", encoding="utf-8") as f:
        f.write(code)
    _enregistrer(ns)
    return ns["NOM"], None
