"""Internationalisation côté Django, sur le même principe que i18n.py côté
GTK (app/) : catalogues JSON par langue, clés opaques "fichier.py:ligne:col"
posées par un script d'extraction (à écrire séparément, sur le modèle de
i18n_extract.py côté GTK — hors périmètre ici), valeur = texte source
(français) tant qu'aucune traduction n'a été fournie côté en.json.

Différence avec le GTK : là-bas la langue est un état global unique (un
utilisateur = un process = une langue). Ici, un process Django sert
potentiellement plusieurs utilisateurs en parallèle (threads/workers), donc
la langue courante ne peut pas être une simple variable globale : elle est
portée par un contextvar, positionné par LangueMiddleware au début de
chaque requête à partir de :
  1. la session (si l'utilisateur a choisi une langue, voir definir_langue) ;
  2. sinon la langue de l'INTERFACE réglée par l'administrateur (base de données, page Personnalisation ; langue_admin) ;
  3. sinon la langue du SERVEUR, détectée automatiquement (detecter_langue_serveur :
     LANGUAGE, LC_ALL, LC_MESSAGES, LANG, /etc/locale.conf) si un catalogue
     existe pour elle ;
  4. sinon LANGUE_PAR_DEFAUT ("en") : langue absente = anglais.

Pour du code qui tourne HORS requête (tâche Celery, commande de gestion) :
aucun contexte de requête n'a positionné le contextvar, donc t()/tf()
retombent sur LANGUE_PAR_DEFAUT. Si une tâche a besoin d'écrire dans la
langue d'un utilisateur précis (ex. notification), appeler explicitement
utiliser_langue(code) en tout début de tâche.

Usage :
    from engine.i18n import t, tf
    t("engine/views/documents.py:42:8")
    tf("engine/tasks.py:118:12", nom=doc.nom)
"""
import contextvars
import functools
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# Langue par défaut de l'interface : l'anglais. Le français reste la langue
# SOURCE des textes (filet de secours ultime : fr.json est complet).
LANGUE_PAR_DEFAUT = "en"
LANGUE_SOURCE = "fr"

_DOSSIER_CATALOGUES = os.path.join(os.path.dirname(__file__), "i18n_defaults")


def langues_disponibles():
    """Liste des langues proposées = un fichier <nom>.json dans
    i18n_defaults/ ; le nom du fichier (sans .json) EST le nom de la langue.
    Pour ajouter une langue, il suffit de déposer un fichier dans ce dossier
    (copie de en.json traduite). La langue par défaut est toujours listée
    en premier, les autres par ordre alphabétique."""
    try:
        noms = sorted(
            f[:-5] for f in os.listdir(_DOSSIER_CATALOGUES)
            if f.endswith(".json") and not f.startswith(".")
        )
    except OSError:
        noms = []
    if LANGUE_PAR_DEFAUT in noms:
        noms.remove(LANGUE_PAR_DEFAUT)
        noms.insert(0, LANGUE_PAR_DEFAUT)
    return tuple(noms) or (LANGUE_PAR_DEFAUT,)


# Conservé pour compatibilité (valeur au chargement du module) ; le code
# interne passe par langues_disponibles(), qui relit le dossier.
LANGUES_DISPONIBLES = langues_disponibles()

_langue_courante = contextvars.ContextVar("langue_courante", default=None)   # None = aucune requête n'a choisi : voir langue_courante()

_catalogues = None  # dict {"fr": {...}, "en": {...}}, chargé paresseusement


def _charger_catalogues():
    """Charge et met en cache les catalogues JSON. Un catalogue manquant ou
    illisible n'empêche pas le démarrage : on log et on continue avec un
    dict vide pour cette langue (t() retombera alors sur la clé brute)."""
    global _catalogues
    if _catalogues is not None:
        return _catalogues

    catalogues = {}
    for langue in langues_disponibles():
        chemin = os.path.join(_DOSSIER_CATALOGUES, f"{langue}.json")
        try:
            with open(chemin, encoding="utf-8") as f:
                catalogues[langue] = json.load(f)
        except FileNotFoundError:
            logger.warning("i18n: catalog not found (%s), using an empty catalog.", chemin)
            catalogues[langue] = {}
        except (OSError, json.JSONDecodeError) as e:
            logger.error("i18n: unreadable catalog (%s): %s", chemin, e)
            catalogues[langue] = {}
    _catalogues = catalogues
    return _catalogues


def recharger_catalogues():
    """Invalide le cache pour forcer une relecture des fichiers JSON au
    prochain t()/tf() — utile après une édition des catalogues sans
    redémarrer le serveur (ex. via un futur éditeur de traductions, comme
    widgets/i18n_translations_editor.py côté GTK)."""
    global _catalogues
    _catalogues = None


def utiliser_langue(code):
    """Positionne la langue courante pour le contexte d'exécution en cours
    (une requête, ou le début d'une tâche Celery). Retombe silencieusement
    sur LANGUE_PAR_DEFAUT si le code n'est pas supporté."""
    if code not in langues_disponibles():
        code = LANGUE_PAR_DEFAUT
    _langue_courante.set(code)
    return code


def langue_courante():
    """Langue de la requête en cours (posée par LangueMiddleware). Hors requête (tâche Celery, commande de gestion) : la langue réglée
    par l'administrateur si elle existe, sinon LANGUE_PAR_DEFAUT."""
    return _langue_courante.get() or langue_admin() or LANGUE_PAR_DEFAUT


def _t_brut(cle):
    """Traduit `cle` dans la langue courante.

    CORRECTIF (2026-09) : contrairement à la version précédente (et au
    comportement côté GTK), une valeur vide ("") dans le catalogue de la
    langue courante est traitée EXACTEMENT comme une clé absente : repli
    sur le catalogue fr, puis sur la clé brute. L'ancien comportement
    (valeur vide = choix délibéré du traducteur, retournée telle quelle
    sans avertissement) était un piège silencieux : tant qu'un catalogue
    (ex. en.json) n'est pas entièrement traduit, ses clés vides rendaient
    l'application illisible pour tout visiteur dans cette langue, sans
    aucune erreur ni log. Le fichier le plus complet (fr, LANGUE_PAR_DEFAUT)
    est donc toujours utilisé comme filet de sécurité tant qu'une
    traduction n'a pas de vrai texte."""
    catalogues = _charger_catalogues()
    langue = langue_courante()

    catalogue_langue = catalogues.get(langue, {})
    valeur = catalogue_langue.get(cle)
    if valeur:
        return valeur

    for repli in (LANGUE_PAR_DEFAUT, LANGUE_SOURCE):
        if langue != repli:
            valeur_repli = catalogues.get(repli, {}).get(cle)
            if valeur_repli:
                return valeur_repli

    if cle not in catalogue_langue:
        logger.warning("i18n: key missing from all catalogs: %s", cle)
    return cle


def devise_affichage():
    """Libellé de la devise du wallet (« FCFA » par défaut) : réglage DEVISE_AFFICHAGE (variable GYPLATFORM_DEVISE). Toute apparition
    de « FCFA » dans un texte traduit est remplacée par ce libellé à l'affichage ; les catalogues ne changent pas."""
    try:
        from django.conf import settings
        return (getattr(settings, "DEVISE_AFFICHAGE", "") or "FCFA").strip() or "FCFA"
    except Exception:
        return "FCFA"


def appliquer_devise(texte):
    d = devise_affichage()
    return texte.replace("FCFA", d) if d != "FCFA" and isinstance(texte, str) and "FCFA" in texte else texte


def t(cle):
    """Traduit `cle` dans la langue courante (voir _t_brut), avec le libellé de devise configuré."""
    return appliquer_devise(_t_brut(cle))


_t_paresseux = None


def tl(cle):
    """Variante PARESSEUSE de t() pour les textes posés à l'import (constantes de module, corps de classe,
    valeurs par défaut) : la langue n'est connue qu'au moment de l'affichage, pas au chargement du module.
    Retourne un proxy de str (django.utils.functional.lazy) résolu à chaque str()/rendu/JsonResponse."""
    global _t_paresseux
    if _t_paresseux is None:
        from django.utils.functional import lazy
        _t_paresseux = lazy(t, str)
    return _t_paresseux(cle)


# CORRECTIF (2026-10-10) : un texte tl() est un proxy paresseux, que json.dumps (JSONField, graphe d'ontologie…)
# refusait (« Object of type __proxy__ is not JSON serializable »). On l'apprend UNE fois à l'encodeur JSON
# standard : le proxy est résolu en str dans la langue courante, partout, sans toucher aux 1299 appels de tl().
import json as _json
if not getattr(_json.JSONEncoder, "_gy_paresseux", False):
    _json_default_origine = _json.JSONEncoder.default

    def _json_default_paresseux(self, o):
        from django.utils.functional import Promise
        return str(o) if isinstance(o, (Promise, _TexteDonnee)) else _json_default_origine(self, o)

    _json.JSONEncoder.default = _json_default_paresseux
    _json.JSONEncoder._gy_paresseux = True


class _TexteDonnee:
    """Texte d'affichage QUI SERT AUSSI DE DONNÉE (constante comparée ailleurs) : str() donne la traduction courante,
    mais ==, hash, in et clés de dict restent ceux du texte FRANÇAIS d'origine — « x == "Moyenne" », « x in LISTE » et
    LISTE[x] continuent donc de marcher quelle que soit la langue. Jamais à écrire en base ni à passer à un filtre ORM
    (str() y serait traduit) : ces cas restent en dur."""
    __slots__ = ("_cle", "_fr")

    def __init__(self, cle):
        self._cle = cle
        self._fr = _charger_catalogues().get(LANGUE_SOURCE, {}).get(cle, cle)

    def __str__(self): return t(self._cle)
    def __repr__(self): return repr(self._fr)
    def __eq__(self, o): return self._fr == (o._fr if isinstance(o, _TexteDonnee) else o) or str(self) == o
    def __ne__(self, o): return not self.__eq__(o)
    def __hash__(self): return hash(self._fr)
    def __lt__(self, o): return str(self) < str(o)
    def __bool__(self): return bool(self._fr)
    def __len__(self): return len(str(self))
    def __iter__(self): return iter(str(self))
    def __contains__(self, x): return x in str(self)
    def __getitem__(self, i): return str(self)[i]
    def __add__(self, o): return str(self) + str(o)
    def __radd__(self, o): return str(o) + str(self)
    def __mod__(self, o): return str(self) % o
    def __format__(self, spec): return format(str(self), spec)
    def __getattr__(self, nom):
        # CORRECTIF (2026-10-12) : __getattr__ n'est appelé que si l'attribut manque. Sur un objet recréé SANS __init__
        # (copy/deepcopy/pickle : Django copie les champs/defaults), _cle n'existe pas -> str(self) -> self._cle ->
        # __getattr__('_cle') -> str(self)... = RecursionError (500 sur /api/ontologie/fiche/). On coupe la boucle.
        if nom in ("_cle", "_fr") or (nom.startswith("__") and nom.endswith("__")):
            raise AttributeError(nom)
        return getattr(str(self), nom)

    def __copy__(self): return self
    def __deepcopy__(self, memo): return self
    def __reduce__(self): return (_TexteDonnee, (self._cle,))


try:  # DjangoJSONEncoder, gettext & co. reconnaissent les textes paresseux par cette classe
    from django.utils.functional import Promise as _Promise
    # CORRECTIF (2026-10-27) : la sous-classe s'appelait "TexteDonnee" alors que son nom d'attribut dans le module est
    # « _TexteDonnee » -> pickle la cherchait sous engine.i18n.TexteDonnee (« Can't pickle <class 'engine.i18n.TexteDonnee'>:
    # it's not found as engine.i18n.TexteDonnee ») et l'écriture du fichier d'état de l'ontologie échouait. Le nom, le qualname et
    # le module de la classe finale DOIVENT correspondre à l'attribut du module, pour pickle, copy, multiprocessing, cache Django…
    _TexteDonnee = type("_TexteDonnee", (_TexteDonnee, _Promise), {"__slots__": (), "__module__": __name__,
                                                                  "__qualname__": "_TexteDonnee"})
except Exception:  # pragma: no cover - hors Django
    pass

# Alias public (anciens pickles / états écrits sous l'ancien nom) : même classe, deux noms résolubles par pickle.
TexteDonnee = _TexteDonnee


def td(cle):
    """Variante de t() pour un texte d'affichage qui sert aussi de donnée (voir _TexteDonnee)."""
    return _TexteDonnee(cle)


def tfr(cle):
    """Texte FRANÇAIS d'origine de `cle`, quelle que soit la langue courante : pour les textes qui sont des DONNÉES (écrites en base,
    comparées, corpus d'entraînement, préfixes de noms) et ne doivent jamais changer avec la langue d'affichage. Même valeur qu'un littéral en dur."""
    return _charger_catalogues().get(LANGUE_SOURCE, {}).get(cle, cle)


_LANGUES_VIRGULE = ("fr", "es", "pt", "de", "it", "nl", "ru")


def dec(texte):
    """Séparateur décimal de la langue courante : un nombre déjà formaté avec un point (« 20.1 ») devient « 20,1 » pour les langues à virgule
    (français…), reste inchangé sinon (anglais). À appliquer au résultat de f"{v:.2f}" au lieu d'un .replace(".", ",") en dur."""
    texte = str(texte)
    return texte.replace(".", ",") if langue_courante().split("-")[0].lower() in _LANGUES_VIRGULE else texte


def tf(cle, **kwargs):
    """Variante formatée de t() : `t(cle).format(**kwargs)`, avec repli sur
    le texte non formaté si un placeholder attendu manque (évite qu'une
    faute de frappe dans le catalogue fasse planter la page)."""
    texte = t(cle)
    try:
        return texte.format(**kwargs)
    except (KeyError, IndexError):
        logger.warning("i18n: formatting error for key %s (kwargs=%s)", cle, kwargs)
        return texte


def detecter_langue_navigateur(request):
    """Choisit la première langue supportée dans l'en-tête Accept-Language
    du navigateur (ex. "en-US,en;q=0.9,fr;q=0.8" -> "en"), sinon
    LANGUE_PAR_DEFAUT. Analyse volontairement simple (pas de q-values) :
    on ne fait que prendre les codes dans l'ordre où le navigateur les
    liste, ce qui suffit pour départager entre "fr" et "en"."""
    entete = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    for fragment in entete.split(","):
        code = fragment.split(";")[0].strip().lower()[:2]
        if code in langues_disponibles():
            return code
    return LANGUE_PAR_DEFAUT


def definir_langue_session(request, code):
    """Mémorise le choix explicite de langue de l'utilisateur en session
    (prioritaire sur Accept-Language pour toutes ses requêtes suivantes).
    À appeler depuis une future vue "changer de langue" (non fournie ici,
    hors périmètre de cette tâche)."""
    code = code if code in langues_disponibles() else LANGUE_PAR_DEFAUT
    request.session["langue"] = code
    return code


@functools.lru_cache(maxsize=1)
def detecter_langue_serveur():
    """Langue du système sur lequel tourne le serveur : premier code de
    LANGUAGE (liste « fr:en »), LC_ALL, LC_MESSAGES, LANG, puis
    /etc/locale.conf (un service systemd/gunicorn n'hérite pas de la session
    de l'utilisateur), pour lequel un catalogue existe dans i18n_defaults/.
    « C »/« POSIX » (aucune langue) sont ignorés. Aucune langue reconnue ->
    LANGUE_PAR_DEFAUT (« en »). Calculée une seule fois par process."""
    candidats = []
    for nom in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        candidats += os.environ.get(nom, "").split(":")
    try:
        with open("/etc/locale.conf", encoding="utf-8") as f:
            for ligne in f:
                m = re.match(r"\s*(?:LANGUAGE|LC_MESSAGES|LANG)\s*=\s*\"?([^\"\s]+)", ligne)
                if m:
                    candidats.append(m.group(1))
    except OSError:
        pass
    disponibles = langues_disponibles()
    for brut in candidats:
        code = re.split(r"[_.@-]", brut.strip().lower())[0]
        if code in disponibles:
            return code
    return LANGUE_PAR_DEFAUT


# ── Langue de l'interface réglée par l'administrateur ─────────────────────────────────────────────────────────────────────
# Persistée en base (Entreprise.langue_interface, une seule entreprise par installation : la première) pour survivre aux redémarrages.
# Lue UNE fois au démarrage du serveur (engine/apps.py) puis relue au plus toutes les quelques secondes : plusieurs workers (gunicorn,
# Celery) restent ainsi d'accord sans redémarrage quand l'administrateur change la langue. Vide = automatique (langue du serveur détectée).
NOMS_LANGUES = {"fr": "Français", "en": "English", "es": "Español", "pt": "Português", "de": "Deutsch", "it": "Italiano", "ar": "العربية"}   # noms propres, jamais traduits
_DUREE_CACHE_LANGUE_ADMIN = 5.0
_cache_langue_admin = {"code": None, "lu_a": 0.0}


def _lire_langue_admin_en_base():
    from .models import Entreprise
    une = list(Entreprise.objects.order_by("id").only("langue_interface")[:2])
    e = une[0] if len(une) == 1 else None   # plusieurs entreprises : chacune a sa langue (langue_entreprise), jamais celle de la première pour toutes
    code = (getattr(e, "langue_interface", "") or "").strip()
    return code if code in langues_disponibles() else ""


def langue_admin():
    """Code de la langue choisie par l'administrateur, « » si aucune (= automatique). Ne lève jamais : base indisponible, migration
    pas encore appliquée ou application pas encore prête -> dernière valeur connue (ou « »)."""
    import time
    c = _cache_langue_admin
    maintenant = time.monotonic()
    if c["code"] is not None and maintenant - c["lu_a"] < _DUREE_CACHE_LANGUE_ADMIN:
        return c["code"]
    try:
        from django.apps import apps
        if not apps.ready:   # import d'un module avant la fin du démarrage : pas d'accès à la base
            return c["code"] or ""
        c["code"] = _lire_langue_admin_en_base()
    except Exception:
        logger.debug("i18n: administrator language unreadable, previous value kept.", exc_info=True)
        c["code"] = c["code"] or ""
    c["lu_a"] = maintenant
    return c["code"]


def langue_entreprise(request):
    """Langue par défaut réglée par l'administrateur de l'entreprise de CETTE requête, « » si aucune. Ne lève jamais."""
    try:
        from .securite import entreprise_de_requete
        code = (getattr(entreprise_de_requete(request), "langue_interface", "") or "").strip()
        return code if code in langues_disponibles() else ""
    except Exception:
        logger.debug("i18n: company language unreadable.", exc_info=True)
        return ""


def langue_serveur_effective(request=None):
    """Langue par défaut du système : celle de l'entreprise de la requête (si `request` est donnée), puis celle de l'administrateur
    d'une installation mono-entreprise, sinon celle détectée sur le système (LANG…), sinon l'anglais."""
    return (langue_entreprise(request) if request is not None else "") or langue_admin() or detecter_langue_serveur()


def enregistrer_langue_admin(code, entreprise=None):
    """Enregistre en base la langue de l'interface (« » = automatique) et l'applique tout de suite dans ce processus. ValueError si
    le code n'a pas de catalogue."""
    code = (code or "").strip()
    if code and code not in langues_disponibles():
        raise ValueError(code)
    from .models import Entreprise
    e = entreprise or Entreprise.objects.order_by("id").first()   # entreprise de l'administrateur qui règle (jamais « la première » pour tous)
    if e is None:
        raise ValueError("entreprise")
    e.langue_interface = code
    e.save(update_fields=["langue_interface"])
    import time
    if Entreprise.objects.count() == 1:
        _cache_langue_admin.update(code=code, lu_a=time.monotonic())
    else:
        oublier_langue_admin()
    return code


def langue_utilisateur(utilisateur_id):
    """Langue enregistrée sur le compte de cet utilisateur (Profil), « » si aucune. Ne lève jamais (migration pas encore appliquée…)."""
    try:
        from .models import LangueUtilisateur
        code = LangueUtilisateur.objects.filter(utilisateur_id=utilisateur_id).values_list("code", flat=True).first() or ""
        return code if code in langues_disponibles() else ""
    except Exception:
        logger.debug("i18n: unreadable user language.", exc_info=True)
        return ""


def enregistrer_langue_utilisateur(utilisateur_id, code):
    """Enregistre (ou, avec « », efface) la langue personnelle d'un utilisateur. ValueError si le code n'a pas de catalogue."""
    code = (code or "").strip()
    if code and code not in langues_disponibles():
        raise ValueError(code)
    from .models import LangueUtilisateur
    if code:
        LangueUtilisateur.objects.update_or_create(utilisateur_id=utilisateur_id, defaults={"code": code})
    else:
        LangueUtilisateur.objects.filter(utilisateur_id=utilisateur_id).delete()
    return code


def langue_choisie(request):
    """Choix PERSONNEL de la requête, « » si aucun (= langue par défaut du système). Utilisateur connecté : la langue de son compte (base),
    puis celle de sa session ; le cookie d'un navigateur ne compte PAS pour lui (un autre compte a pu s'en servir avant). Visiteur
    (page de connexion) : session, puis cookie."""
    disponibles = langues_disponibles()
    session = getattr(request, "session", None)
    uid = session.get("_auth_user_id") if session is not None else None
    if uid:
        code = langue_utilisateur(uid)
        if code not in disponibles:
            code = session.get("langue")
    else:
        code = session.get("langue") if session is not None else None
        if code not in disponibles:
            code = request.COOKIES.get("langue")
    return code if code in disponibles else ""


def oublier_langue_admin():
    """Force la relecture en base au prochain appel (tests, restauration d'une archive)."""
    _cache_langue_admin.update(code=None, lu_a=0.0)


def libelle_slug(prefixe, slug):
    """Libellé d'AFFICHAGE d'un identifiant d'ontologie (relation ou type), traduit dans la langue courante.
    L'identifiant lui-même (stocké, filtré, comparé) n'est jamais modifié. Inconnu du catalogue (relation ou type
    créé par l'utilisateur) : identifiant lisible (« _ » → espace)."""
    if not slug:
        return slug
    cle = f"{prefixe}.{slug}"
    if _charger_catalogues().get(LANGUE_SOURCE, {}).get(cle):
        return t(cle)
    return str(slug).replace("_", " ")
