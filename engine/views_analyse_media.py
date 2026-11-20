"""ANALYSE DES MÉDIAS (son / image / vidéo) — page dans le châssis des analyses + API. Aucun modèle ; résultats volatils (seuls les 5 derniers sont gardes 1 h pour que le PDF ne refasse pas le calcul).

  GET  /tables/<id>/analyse-media/                         la page (3 onglets : son, image, vidéo)
  GET  /api/tables/<id>/analyse-media/catalogue/?source=   fonctions exposées (introspection de pydub, soundfile, scipy, cv2)
  POST /api/tables/<id>/analyse-media/executer/            {type, etapes:[noeud…], colonne, ligne?, pas?, maxi?} ; noeud = {type:"action", source, fonction, args} | {type:"si", cond, alors:[noeud…], sinon:[noeud…]}
                                                           | {type:"repeter", mode:"fois"|"chaque"|"tant", n|liste|cond, max, corps:[noeud…]} (lib = libellé lisible, pour le PDF)

Aucune analyse n'est écrite ici : on appelle les fonctions des modules, en étapes enchaînées (le résultat de l'étape k = variable rk, et ses méthodes sont
appelables par la source « @rk »). Conditions et boucles : bornées (MAX_NOEUDS, MAX_PROFONDEUR, MAX_EXEC, MAX_TOURS) ; expressions = mêmes règles que les arguments + mean/max/min/len/sum/std/median/abs. La page les saisit sans Python (un champ par argument : analyse_media_saisie.js).
Arguments saisis comme en Python (littéraux, cv2.XXX, data[:, 0], img.shape[:2], + - * / // & | et comparaisons) avec les variables du média : son -> seg (AudioSegment), data, sr ; image/vidéo -> img, gris, prev, pgris (frame précédente). Une cellule = un fichier ; sans `ligne`, toute la colonne.
Le son d'une vidéo est extrait en mémoire (clone volatil). Mêmes droits que Statistiques (views_geo._acces, vue « analyse_media »)."""
from engine.i18n import t as _i18n_t
import ast, base64, importlib, importlib.util, inspect, io, itertools, json, math, os, re, tempfile, time
import numpy as np
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from . import parquet_store
from .securite import chemin_media_sur
from .views_geo import _acces

VUE = "analyse_media"
MEDIAS = ("son", "image", "video")
# Bibliothèques son facultatives : proposées seulement si elles sont installées (find_spec n'importe rien). Sous-modules « tableaux en entrée » uniquement :
# jamais le niveau supérieur de librosa (get_samplerate, stream… ouvriraient un chemin de fichier saisi).
OPTIONNELS = ([f"librosa.{m}" for m in ("feature", "onset", "beat", "effects", "decompose", "segment", "sequence")] if importlib.util.find_spec("librosa") else []) \
    + (["pywt"] if importlib.util.find_spec("pywt") else []) + (["noisereduce"] if importlib.util.find_spec("noisereduce") else [])
SCIPY_IMG = ["scipy.ndimage", "scipy.signal", "scipy.fft", "scipy.stats", "numpy.fft", "numpy.linalg"]
SOURCES = {"son": ["pydub.AudioSegment", "pydub.effects", "pydub.silence", "soundfile", "scipy.signal", "scipy.fft", "scipy.stats",
                   "scipy.ndimage", "scipy.interpolate", "scipy.spatial.distance", "numpy.fft", "numpy.linalg", "engine.separation_son", "engine.descripteurs_son"] + OPTIONNELS,
           "image": ["cv2"] + SCIPY_IMG, "video": ["cv2"] + SCIPY_IMG}
TOUS = sum(SOURCES.values(), [])
PRETS = {"son": [("p_spectrogramme", [("scipy.signal", "spectrogram", "mono, sr")]), ("p_hp", [("engine.separation_son", "harmonique_percussif", "seg")]),
                 ("p_composantes", [("engine.separation_son", "composantes", "seg, 3")]), ("p_bandes", [("engine.separation_son", "par_bandes", "seg, (300, 3000)")]),
                 ("p_fiche", [("engine.descripteurs_son", "caracteristiques", "mono, sr")]), ("p_mfcc", [("engine.descripteurs_son", "mfcc", "mono, sr")]),
                 ("p_hauteur", [("engine.descripteurs_son", "hauteur_yin", "mono, sr")])],
         "image": [("p_histogramme", [("cv2", "calcHist", "[gris], [0], None, [256], [0, 256]")]), ("p_contours", [("cv2", "Canny", "gris, 100, 200")])],
         "video": [("p_variation", [("cv2", "absdiff", "gris, pgris")]), ("p_densite", [("cv2", "Canny", "gris, 100, 200")])]}
MAX_LIGNES, MAX_OCTETS, MAX_ARGS = 200, 200 * 2**20, 500
# Étapes = ARBRE : action (appel d'une fonction), si (condition : alors / sinon) ou repeter (boucle : fois | chaque | tant). Bornes : jamais de calcul sans fin.
MAX_NOEUDS, MAX_PROFONDEUR, MAX_EXEC, MAX_TOURS = 30, 3, 400, 200
FONCTIONS = {"abs": np.abs, "len": len, "min": np.min, "max": np.max, "sum": np.sum, "round": round, "int": int, "float": float,   # seules fonctions appelables dans une expression
             "mean": np.mean, "median": np.median, "std": np.std}
# Chaque analyse tourne dans un PROCESSUS SÉPARÉ (fork) borné en mémoire, en temps CPU et en durée ; surchargeable : settings.ANALYSE_MEDIA_LIMITES
_LIM = {"delai": 60, "memoire_mo": 4096, "simultanes": 2, **(getattr(settings, "ANALYSE_MEDIA_LIMITES", None) or {})}


def _prendre_place():   # verrou ENTRE processus (gunicorn -w N) : un fichier verrou par analyse simultanée, relâché tout seul si le processus meurt
    import fcntl
    for i in range(max(1, int(_LIM["simultanes"]))):
        f = open(os.path.join(tempfile.gettempdir(), f"agentgy_analyse_media_{i}.lock"), "a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            f.close()


# Lecture/écriture disque, fenêtres : jamais exposés (un chemin saisi ne doit rien lire ni écrire). ÉTAT_GLOBAL : seulement pour les modules (les setters d'un objet résultat sont permis).
INTERDIT = re.compile(r"(write|export|save|read|open(?!ing)|load|file|dump|capture|writer|storage|imshow|waitkey|namedwindow|destroy|^from_|remove|^_)", re.I)
ETAT_GLOBAL = re.compile(r"^(set|register)", re.I)   # register_pydub_effect ajoute des méthodes à AudioSegment pour tout le processus
TEXTES = ["titre", "son", "image", "video", "colonne", "ligne", "module", "chercher", "fonction", "args", "lancer", "pas", "maxi", "son_extrait", "aide", "vide",
          "etape", "ajouter", "composante", "onde", "spectre_db", "temps_s", "freq_hz", "echelle_db", "synthese", "synthese_sons", "resume_son", "fort", "moyen", "faible", "exporter", "pic_variation", "prets", "avance", "p_spectrogramme", "p_hp", "p_composantes", "p_bandes", "p_fiche", "p_mfcc", "p_hauteur", "p_histogramme", "p_contours", "p_variation", "p_densite", "resultat", "arriere", "avant", "jusqu_ici", "delai", "interrompu", "occupe", "indisponible",
          "ajouter_si", "ajouter_rep", "si", "alors", "sinon", "et", "ou", "lien_et", "lien_ou", "ajouter_cond", "valeur_a", "mesure", "operateur", "valeur_b", "mes_val", "mes_mean", "mes_max", "mes_min", "mes_sum", "mes_len", "mes_std", "mes_median",
          "op_gt", "op_lt", "op_ge", "op_le", "op_eq", "op_ne", "repeter", "mode_fois", "mode_chaque", "mode_tant", "nb_fois", "la_liste", "max_tours", "aide_boucle", "var_seg", "var_mono", "var_data", "var_sr", "var_img", "var_gris", "var_prev", "var_pgris",
          "var_i", "var_x", "var_res", "var_bloc", "libre", "guide", "bool_defaut", "bool_oui", "bool_non", "manque", "facultatif", "defaut", "monter", "descendre", "supprimer", "sans_signature", "retirer_cond"]
OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b,
       ast.BitAnd: lambda a, b: a & b, ast.BitOr: lambda a, b: a | b, ast.Mod: lambda a, b: a % b}
CMP = {ast.Gt: lambda a, b: a > b, ast.Lt: lambda a, b: a < b, ast.GtE: lambda a, b: a >= b, ast.LtE: lambda a, b: a <= b,
       ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def _obj(src):
    return importlib.import_module("pydub").AudioSegment if src == "pydub.AudioSegment" else importlib.import_module(src)


def _membres(o, module=True):   # {nom: callable} d'un module/classe (module=True) ou d'un objet résultat (module=False)
    out = {}
    for n in dir(o):
        if INTERDIT.search(n) or (module and ETAT_GLOBAL.search(n)):
            continue
        try:
            m = getattr(o, n)
        except Exception:   # certains attributs (ex. .mT d'un tableau 1-D) lèvent : on les ignore
            continue
        if not inspect.isclass(m) and not inspect.ismodule(m) and (callable(m) or isinstance(m, property)):
            out[n] = m
    return out


def _signature(n, m):   # signature Python ; sinon celle de la 1re ligne de la doc (OpenCV : « nom(a, b[, c[, d]]) -> retour ») : arguments facultatifs = « nom=... »
    try:
        return str(inspect.signature(m))
    except (TypeError, ValueError):
        pass
    mo = re.match(rf"{re.escape(n)}\((.*)\)\s*(->.*)?$", (inspect.getdoc(m) or "").split("\n", 1)[0].strip())
    if not mo:
        return ""
    obligatoires, _, facultatifs = mo.group(1).partition("[")
    noms = lambda t: [x.strip() for x in re.sub(r"[\[\]]", "", t).split(",") if x.strip()]
    return "(" + ", ".join(noms(obligatoires) + [f"{x}=..." for x in noms(facultatifs)]) + ")"


def _fiches(membres):
    return [{"n": n, "sig": _signature(n, m), "doc": (inspect.getdoc(m) or "")[:600]} for n, m in membres.items()]


def _val(n, ctx):   # littéral, variable (img, data, r1…), constante cv2.XXX, attribut (.shape), indexation [:, 0], tranche, + - * /
    if isinstance(n, ast.Name) and n.id in ctx:
        return ctx[n.id]
    if isinstance(n, ast.Attribute):
        if isinstance(n.value, ast.Name) and n.value.id in ("cv2", "np"):
            v = getattr(importlib.import_module({"np": "numpy"}.get(n.value.id, "cv2")), n.attr)
        else:
            if INTERDIT.search(n.attr):
                raise ValueError(n.attr)
            v = getattr(_val(n.value, ctx), n.attr)
        if callable(v):
            raise ValueError(n.attr)
        return v
    if isinstance(n, ast.Subscript):
        return _val(n.value, ctx)[_val(n.slice, ctx)]
    if isinstance(n, ast.Slice):
        return slice(*(None if x is None else _val(x, ctx) for x in (n.lower, n.upper, n.step)))
    if isinstance(n, ast.BinOp) and type(n.op) in OPS:
        return OPS[type(n.op)](_val(n.left, ctx), _val(n.right, ctx))
    if isinstance(n, ast.Compare) and all(type(o) in CMP for o in n.ops):   # masques : gris > 100 ; chaînes : 1 < x < 5
        g, r = _val(n.left, ctx), True
        for o, c in zip(n.ops, n.comparators):
            d = _val(c, ctx)
            r, g = CMP[type(o)](g, d), d
            if len(n.ops) > 1 and not r:
                return False
        return r
    if isinstance(n, ast.BoolOp):   # et / ou, évalués dans l'ordre et sans calcul inutile
        for x in n.values:
            v = _val(x, ctx)
            if bool(v) != isinstance(n.op, ast.And):
                return v
        return v
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.Not)):
        return -_val(n.operand, ctx) if isinstance(n.op, ast.USub) else not _val(n.operand, ctx)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FONCTIONS and not n.keywords:   # mean(r1), max(data)… : liste blanche
        return FONCTIONS[n.func.id](*[_val(a, ctx) for a in n.args])
    if isinstance(n, ast.Name):
        raise ValueError(_i18n_t("engine/views_analyse_media.py:variable_inconnue").replace("{v}", n.id))
    if isinstance(n, (ast.Tuple, ast.List)):
        r = [_val(e, ctx) for e in n.elts]
        return tuple(r) if isinstance(n, ast.Tuple) else r
    return ast.literal_eval(n)


def _lancer(src, nom, texte, ctx):   # src = module, ou « @r1 » = méthode du résultat de l'étape 1
    if src.startswith("@"):
        o = ctx[src[1:]]
        if nom not in _membres(o, False):
            raise ValueError(nom)
        f = getattr(o, nom)
    else:
        if nom not in _membres(_obj(src)):
            raise ValueError(nom)
        f = getattr(ctx["seg"], nom) if src == "pydub.AudioSegment" else getattr(_obj(src), nom)
    c = ast.parse(f"f({texte or ''})", mode="eval").body
    a, k = [_val(x, ctx) for x in c.args], {x.arg: _val(x.value, ctx) for x in c.keywords}
    return f(*a, **k) if callable(f) else f


def _numeroter(noeuds, num, cpt=None):   # numéros des étapes d'analyse (r1, r2…) et des boucles (b1…) dans l'ordre d'écriture, comme l'affiche la page
    cpt = cpt or {"r": 0, "b": 0}
    for n in noeuds:
        t = n.get("type", "action")
        if t != "si":
            cpt["r" if t == "action" else "b"] += 1
            num[id(n)] = cpt["r" if t == "action" else "b"]
        for k in ("alors", "sinon", "corps"):
            _numeroter(n.get(k) or [], num, cpt)


def _test(texte, ctx):   # condition -> vrai / faux (une condition sur plusieurs valeurs à la fois est ambiguë : il faut une mesure)
    v = _val(ast.parse(texte, mode="eval").body, ctx)
    if isinstance(v, np.ndarray) and v.size != 1:
        raise ValueError(_i18n_t("engine/views_analyse_media.py:cond_ambigue"))
    return bool(v)


def _executer(noeuds, ctx, num, etat):
    """Exécute la liste d'étapes ; etat = {"n": nombre d'exécutions (borné), "dernier": dernier résultat}. Boucle : i = numéro du tour (1, 2…), x = élément (ou i) ; les résultats de chaque tour forment la liste bK."""
    for n in noeuds:
        etat["n"] += 1
        if etat["n"] > MAX_EXEC:
            raise ValueError(_i18n_t("engine/views_analyse_media.py:trop_long").replace("{n}", str(MAX_EXEC)))
        t = n.get("type", "action")
        if t == "action":
            etat["dernier"] = ctx["r"] = ctx[f"r{num[id(n)]}"] = _lancer(n["source"], n["fonction"], n.get("args"), ctx)
        elif t == "si":
            _executer(n.get("alors" if _test(n["cond"], ctx) else "sinon") or [], ctx, num, etat)
        else:
            mode, nom, sortie = n.get("mode"), f"b{num[id(n)]}", []
            if mode == "chaque":
                tours = list(itertools.islice(iter(_val(ast.parse(n["liste"], mode="eval").body, ctx)), MAX_TOURS))
            elif mode == "fois":
                tours = list(range(1, max(0, min(int(_val(ast.parse(n["n"], mode="eval").body, ctx)), MAX_TOURS)) + 1))
            else:
                tours = None

            def tour(i, x):
                ctx["i"], ctx["x"], etat["dernier"] = i, x, None
                _executer(n.get("corps") or [], ctx, num, etat)
                sortie.append(etat["dernier"])
                ctx[nom] = list(sortie)
            if tours is None:   # « tant que » : au plus « max » tours
                i = 0
                while i < max(1, min(int(n.get("max") or 50), MAX_TOURS)) and _test(n["cond"], ctx):
                    i += 1
                    tour(i, i)
            else:
                for i, x in enumerate(tours, 1):
                    tour(i, x)
            etat["dernier"] = ctx[nom] = sortie


def _pipeline(etapes, ctx, meth):   # le résultat de l'étape k est la variable rk (et r = le dernier) ; le résultat de la dernière boucle = la liste de ses tours
    ctx, etat, num = dict(ctx), {"n": 0, "dernier": None}, {}
    _numeroter(etapes, num)
    _executer(etapes, ctx, num, etat)
    if not meth:   # méthodes proposées pour l'étape suivante (lues sur la première cellule/frame)
        meth.update({k: _fiches(_membres(v, False)) for k, v in ctx.items() if re.fullmatch(r"r\d+", k)})
    return etat["dernier"]


def _valide(noeuds, typ, prof=0, cpt=None):   # structure, profondeur, taille et sources autorisées de tout l'arbre
    cpt = [0] if cpt is None else cpt
    if not isinstance(noeuds, list) or prof > MAX_PROFONDEUR:
        return False
    for n in noeuds:
        cpt[0] += 1
        if not isinstance(n, dict) or cpt[0] > MAX_NOEUDS:
            return False
        t, txt = n.get("type", "action"), lambda k, n=n: isinstance(n.get(k) or "", str) and len(n.get(k) or "") <= MAX_ARGS
        if not all(txt(k) for k in ("args", "cond", "lib", "liste", "n")) or len(str(n.get("max") or "")) > 6:
            return False
        if t == "action":
            if not (n["source"] in SOURCES.get(typ, []) or re.fullmatch(r"@r\d+", str(n["source"]))) or len(str(n.get("fonction"))) > 100:
                return False
        elif t == "si":
            if not n.get("cond") or not _valide(n.get("alors") or [], typ, prof + 1, cpt) or not _valide(n.get("sinon") or [], typ, prof + 1, cpt):
                return False
        elif t == "repeter":
            if not n.get("mode") in ("fois", "chaque", "tant") or not n.get({"fois": "n", "chaque": "liste", "tant": "cond"}[n["mode"]]) or not _valide(n.get("corps") or [], typ, prof + 1, cpt):
                return False
        else:
            return False
    return True


def _lignes(noeuds, niv=0):   # étapes en lignes de texte indentées (si / sinon / boucles), pour le PDF
    p, t, out = "\u00a0" * 4 * niv, lambda k: _i18n_t("engine/views_analyse_media.py:" + k), []
    for n in noeuds:
        k = n.get("type", "action")
        if k == "action":
            out.append(f"{p}{n['source']}.{n['fonction']}({n.get('args') or ''})")
        elif k == "si":
            out += [p + t("pdf_si").replace("{c}", n.get("lib") or n["cond"])] + _lignes(n.get("alors") or [], niv + 1)
            out += ([p + t("pdf_sinon")] + _lignes(n["sinon"], niv + 1)) if n.get("sinon") else []
        else:
            out += [p + t("pdf_" + n["mode"]).replace("{n}", str(n.get("n"))).replace("{l}", str(n.get("lib") or n.get("liste"))).replace("{c}", str(n.get("lib") or n.get("cond"))).replace("{m}", str(n.get("max") or 50))] + _lignes(n.get("corps") or [], niv + 1)
    return out


def _fl(x):
    x = float(x)
    return x if math.isfinite(x) else None


def _png(a):
    import cv2
    h, w = a.shape[:2]
    if max(h, w) > 640:
        a = cv2.resize(a, (max(1, w * 640 // max(h, w)), max(1, h * 640 // max(h, w))), interpolation=cv2.INTER_AREA)
    return {"png": base64.b64encode(cv2.imencode(".png", a)[1]).decode()}


def _spectre(seg):   # chaque son séparé (AudioSegment) = un son à part : onde + spectre FFT en dB, lisibles sans étape de plus
    a = np.array(seg.set_channels(1).get_array_of_samples(), float)
    if a.size < 8:
        return {}
    d = np.abs(np.fft.rfft(a * np.hanning(a.size)))
    d = np.maximum(20 * np.log10(d / (d.max() or 1) + 1e-9), -90)
    ech = lambda v: [_fl(x) for x in np.maximum.reduceat(v, np.linspace(0, v.size, min(v.size, 600) + 1)[:-1].astype(int))]   # réduit à 600 points EN GARDANT LES PICS (pas de repliement)
    return {"onde": ech(np.abs(a)), "spectre": ech(d), "fmax": seg.frame_rate / 2}   # onde = enveloppe d'amplitude ; spectre : une raie fine ne disparaît pas


def _wav(seg):   # pour l'écoute dans le navigateur : mono, 11 kHz, 30 s au plus (copie volatile)
    w = io.BytesIO()
    seg[:30000].set_channels(1).set_frame_rate(11025).export(w, format="wav")
    return base64.b64encode(w.getvalue()).decode()


def _sortie(r):   # n'importe quel résultat -> JSON : courbes (1-D), carte de chaleur (2-D flottants), image (uint8), résumé
    import cv2
    if isinstance(r, np.generic):
        r = r.item()
    if isinstance(r, float):
        return _fl(r)
    if isinstance(r, (int, str, bool, type(None))):
        return r
    if isinstance(r, np.ndarray):
        num = r.dtype.kind in "biufc"
        v = np.nan_to_num(np.abs(r).astype(float) if r.dtype.kind == "c" else r.astype(float)) if num else r
        out = {"shape": list(r.shape), "dtype": str(r.dtype)}
        if num and r.size:
            out.update({"min": _fl(v.min()), "max": _fl(v.max()), "mean": _fl(v.mean())})
            if r.ndim == 1 and r.size > 1 or r.ndim == 2 and min(r.shape) <= 8 < max(r.shape):
                lignes = [v] if r.ndim == 1 else list(v if r.shape[0] <= 8 else v.T)
                pics = lambda l: [_fl(c[np.argmax(np.abs(c))]) for c in np.array_split(l, min(l.size, 800))]   # par groupe, la valeur de plus grande amplitude : un pic ne disparaît pas
                out["series"] = [pics(l) for l in lignes[:8]]
                return out
            if r.ndim in (2, 3) and min(r.shape[:2]) > 8 and (r.ndim == 2 or r.shape[2] in (1, 3, 4)):
                if r.dtype == np.uint8:
                    return {**out, **_png(r)}
                if v.min() >= 0 and v.max() > 0 and r.ndim == 2 and v.max() > 1e3 * (np.median(v) + 1e-12):
                    v, out["echelle"] = np.maximum(20 * np.log10(v / v.max() + 1e-12), -90), "dB"
                g = ((v - v.min()) / ((v.max() - v.min()) or 1) * 255).astype(np.uint8)
                return {**out, **_png(cv2.applyColorMap(g, cv2.COLORMAP_VIRIDIS) if r.ndim == 2 else g)}
        out["apercu"] = [_sortie(x) for x in r.ravel()[:50]]
        return out
    if isinstance(r, (tuple, list)):
        return [_sortie(x) for x in r[:20]]
    if isinstance(r, dict):
        return {str(k): _sortie(v) for k, v in list(r.items())[:50]}
    if hasattr(r, "frame_rate"):
        return {"duree_ms": len(r), "dBFS": _sortie(r.dBFS), "canaux": r.channels, "hz": r.frame_rate, **_spectre(r), "wav": _wav(r)}
    return repr(r)[:500]


def _ctx_img(fr, prev=None):
    import cv2
    g = lambda x: None if x is None else cv2.cvtColor(x, cv2.COLOR_BGR2GRAY)
    return {"img": fr.copy(), "prev": prev, "gris": g(fr), "pgris": g(prev)}


def _contexte(typ, b):
    if typ == "son":   # fonctionne aussi sur une vidéo : la piste son est extraite en mémoire
        import soundfile as sf
        from pydub import AudioSegment
        seg, w = AudioSegment.from_file(io.BytesIO(b)), io.BytesIO()
        seg.export(w, format="wav")
        w.seek(0)
        data, sr = sf.read(w)
        return {"seg": seg, "data": data, "mono": data if data.ndim == 1 else data.mean(axis=1), "sr": sr}
    import cv2
    img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(_i18n_t("engine/views_analyse_media.py:illisible"))
    return _ctx_img(img)


def _par_frame(b, etapes, pas, maxi, meth):   # vidéo découpée en frames ; `prev` = frame précédente analysée (variation entre frames)
    import cv2
    out, prev, i = [], None, 0
    with tempfile.NamedTemporaryFile() as f:
        f.write(b)
        f.flush()
        cap = cv2.VideoCapture(f.name)
        while len(out) < maxi:
            ok, fr = cap.read()
            if not ok:
                break
            if i % pas == 0:
                if prev is not None or not re.search(r"prev|pgris", json.dumps(etapes)):
                    r = _pipeline(etapes, _ctx_img(fr, prev), meth)
                    out.append({"i": i, "s": float(np.mean(r)) if isinstance(r, (np.ndarray, int, float, np.generic)) else None, "r": _sortie(r)})
                prev = fr
            i += 1
        cap.release()
    return out


def _octets(ref):   # référence stockée dans la cellule = chemin sous MEDIA_ROOT (jamais d'URL ni de chemin libre)
    chemin = chemin_media_sur(str(ref or "").strip())
    if chemin is None or not chemin.is_file() or chemin.stat().st_size > MAX_OCTETS:
        raise ValueError(_i18n_t("engine/views_analyse_media.py:introuvable"))
    return chemin.read_bytes()


def _isole(travail):
    """("ok", résultat) ou (raison, None) : raison = delai | interrompu | occupe | indisponible. L'enfant ne touche jamais la base (tout est lu avant)
    et sort par os._exit ; si un décodeur plante ou dépasse les limites, seul l'enfant meurt. NB : ce n'est pas un bac à sable (voir DEPLOIEMENT)."""
    if not hasattr(os, "fork"):
        return "indisponible", None
    place = _prendre_place()
    if place is None:
        return "occupe", None
    try:
        import multiprocessing as mp, resource
        ctx = mp.get_context("fork")
        rx, tx = ctx.Pipe(False)

        def enfant():
            try:
                lim = int(_LIM["memoire_mo"]) * 2**20
                resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
                resource.setrlimit(resource.RLIMIT_CPU, (int(_LIM["delai"]), int(_LIM["delai"]) + 5))
                tx.send(("ok", travail()))
            except BaseException:
                tx.send(("interrompu", None))
            finally:
                os._exit(0)
        p = ctx.Process(target=enfant, daemon=True)
        p.start()
        tx.close()
        try:
            return rx.recv() if rx.poll(_LIM["delai"]) else ("delai", None)
        except (EOFError, OSError):   # l'enfant est mort (mémoire, signal, plantage du décodeur)
            return "interrompu", None
        finally:
            if p.is_alive():
                p.kill()
            p.join(5)
            rx.close()
    finally:
        place.close()


@login_required
@ensure_csrf_cookie
@require_http_methods(["GET"])
def vue_analyse_media(request, table_id):
    table, lecture_seule, entreprise, refus = _acces(request, table_id, vue=VUE)
    if refus:
        return refus
    cols = [{"slug": c.slug, "nom": c.nom, "type": c.type_champ} for c in table.champs.filter(type_champ__in=MEDIAS).order_by("ordre", "id")]
    return render(request, "engine/analyse_media.html", {
        "entreprise": entreprise, "table": table, "lecture_seule": lecture_seule, "sources": SOURCES, "colonnes": cols, "prets": {k: [{"k": n, "e": [{"m": m, "f": f, "a": a} for m, f, a in e]} for n, e in v] for k, v in PRETS.items()},
        "L": {k: _i18n_t(f"engine/views_analyse_media.py:{k}") for k in TEXTES}})


@login_required
@require_http_methods(["GET"])
def api_media_catalogue(request, table_id):
    _t, _l, _e, refus = _acces(request, table_id, api=True, vue=VUE)
    if refus:
        return refus
    src = request.GET.get("source", "")
    out = _fiches(_membres(_obj(src))) if src in TOUS else []
    return JsonResponse(out, safe=False)


# ── mémoire légère des derniers résultats : le PDF réutilise le calcul de l'affichage au lieu de le refaire ──────────────────
# Fichiers JSON dans le dossier média (un par analyse, nom = HMAC de la requête : introuvable sans la connaître), les 5 plus récents par table,
# valables 1 h. Clé = utilisateur + requête + (cellule, date du fichier) : une cellule modifiée ou un autre compte ne retombe jamais sur un ancien résultat.
MEMOIRE_MAX, MEMOIRE_DUREE_S, MEMOIRE_OCTETS = 5, 3600, 40 * 2**20


def _dossier_memoire(table_id):
    d = os.path.join(settings.MEDIA_ROOT, "analyse_media_cache", str(int(table_id)))
    os.makedirs(d, exist_ok=True)
    return d


def _cle_memoire(request, table_id, d, etapes, lignes):
    import hashlib, hmac
    def empreinte(ref):
        c = chemin_media_sur(str(ref or "").strip())
        try:
            return c.stat().st_mtime_ns if c is not None else 0
        except OSError:
            return 0
    corps = json.dumps([request.user.pk, table_id, d.get("type"), d.get("colonne"), etapes, d.get("ligne"), d.get("pas"), d.get("maxi"),
                        [(str(pk), str(ref), empreinte(ref)) for pk, ref in lignes]], sort_keys=True, default=str)
    return hmac.new(settings.SECRET_KEY.encode(), corps.encode(), hashlib.sha256).hexdigest()


def _memoire_lire(table_id, cle):
    f = os.path.join(_dossier_memoire(table_id), cle + ".json")
    try:
        if time.time() - os.path.getmtime(f) > MEMOIRE_DUREE_S:
            return None
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _memoire_ecrire(table_id, cle, r):
    try:
        texte = json.dumps(r)
        if len(texte) > MEMOIRE_OCTETS:
            return
        dossier = _dossier_memoire(table_id)
        tmp = os.path.join(dossier, f".{cle}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(texte)
        os.replace(tmp, os.path.join(dossier, cle + ".json"))
        anciens = sorted((os.path.join(dossier, n) for n in os.listdir(dossier) if n.endswith(".json")), key=os.path.getmtime, reverse=True)
        for f in anciens[MEMOIRE_MAX:]:
            os.remove(f)
    except (OSError, TypeError, ValueError):
        pass                      # la mémoire est un confort : son échec ne doit jamais faire échouer l'analyse


def _calculer(request, table_id):
    """(table, requête, résultat, erreur) : le MÊME calcul alimente l'affichage et le PDF ; le PDF reprend le résultat garde en mémoire par /executer/ (même requête, mêmes fichiers) et ne refait l'analyse que s'il a disparu ou expiré."""
    table, _l, _e, refus = _acces(request, table_id, api=True, vue=VUE)
    if refus:
        return None, None, None, refus
    try:
        d = json.loads(request.body or "{}")
        typ, col, etapes = d["type"], d["colonne"], d["etapes"]
        assert etapes and _valide(etapes, typ)
    except (ValueError, KeyError, TypeError, AssertionError):
        return None, None, None, JsonResponse({"erreur": _i18n_t("engine/views_analyse_media.py:requete")}, status=400)
    if not table.champs.filter(slug=col, type_champ__in=MEDIAS).exists():
        return None, None, None, JsonResponse({"erreur": _i18n_t("engine/views_analyse_media.py:requete")}, status=400)
    df = parquet_store.donnees_analytiques(table, avec_id=True, colonnes=[col])
    cle = parquet_store.COLONNE_ID
    lignes = [(l.get(cle), l.get(col)) for l in parquet_store.en_lignes(df) if not d.get("ligne") or str(l.get(cle)) == str(d["ligne"])][:MAX_LIGNES]
    cle = _cle_memoire(request, table.id, d, etapes, lignes)
    garde = _memoire_lire(table.id, cle)
    if garde is not None:
        return table, d, garde, None

    def travail():
        res, meth = [], {}
        for pk, ref in lignes:
            try:
                b = _octets(ref)
                if typ == "video":
                    res.append({"id": pk, "frames": _par_frame(b, etapes, max(1, int(d.get("pas") or 1)), min(500, int(d.get("maxi") or 50)), meth)})
                else:
                    res.append({"id": pk, "r": _sortie(_pipeline(etapes, _contexte(typ, b), meth))})
            except Exception as e:
                res.append({"id": pk, "erreur": f"{type(e).__name__}: {e}"})
        return {"resultats": res, "methodes": meth}

    statut, r = _isole(travail)
    if statut != "ok":
        return table, d, None, JsonResponse({"erreur": _i18n_t(f"engine/views_analyse_media.py:{statut}")}, status=429 if statut == "occupe" else 503)
    if not any("erreur" in x for x in r.get("resultats", [])):      # un résultat partiel (cellule en erreur) n'est pas gardé : le PDF le refera
        _memoire_ecrire(table.id, cle, r)
    return table, d, r, None


def _sons(x):   # sons (résultats avec spectre) contenus dans un résultat, dans l'ordre
    return [s for e in x for s in _sons(e)] if isinstance(x, list) else [x] if isinstance(x, dict) and "spectre" in x else []


def _resume_son(x):   # (phrase en clair, fréquence dominante en Hz) d'un son
    sp, db = x["spectre"], x.get("dBFS")
    pic = sp.index(max(sp)) / max(len(sp) - 1, 1) * x["fmax"]
    niv = "faible" if db is None else "fort" if db > -12 else "moyen" if db > -30 else "faible"
    return _i18n_t("engine/views_analyse_media.py:resume_son").replace("{d}", f"{x['duree_ms'] / 1000:.2f}").replace("{n}", _i18n_t("engine/views_analyse_media.py:" + niv)) \
        .replace("{db}", "" if db is None else f"{db:.1f}").replace("{f}", f"{pic:.0f}"), pic


def _synthese(res):   # phrase(s) de synthèse en langage clair pour le décideur
    S, out = [s for r in res for s in _sons(r.get("r"))], []
    if S:
        rang = lambda c: 1 + max(range(len(S)), key=lambda i: c(S[i]))
        out.append(_i18n_t("engine/views_analyse_media.py:synthese_sons").replace("{n}", str(len(S))).replace("{k}", str(rang(lambda s: -999 if s.get("dBFS") is None else s["dBFS"]))).replace("{j}", str(rang(lambda s: _resume_son(s)[1]))))
    for r in res:
        v = [(f["i"], f["s"]) for f in r.get("frames", []) if f.get("s") is not None]
        if len(v) > 1:
            out.append(_i18n_t("engine/views_analyse_media.py:pic_variation").replace("{i}", str(max(v, key=lambda p: p[1])[0])))
    return " ".join(out)


@login_required
@require_http_methods(["POST"])
def api_media_executer(request, table_id):
    _t, _d, r, erreur = _calculer(request, table_id)
    return erreur or JsonResponse(r)


@login_required
@require_http_methods(["POST"])
def api_media_pdf(request, table_id):
    """PDF du rapport (même corps que /executer/) : bandeau de l'entreprise, synthèse en clair, étapes, courbes avec axes."""
    table, d, r, erreur = _calculer(request, table_id)
    if erreur:
        return erreur
    from .reports import generer_pdf_analyse_media
    try:
        etapes = _lignes(d["etapes"])
        pdf = generer_pdf_analyse_media(table, r["resultats"], etapes, _synthese(r["resultats"]), d["type"])
    except Exception:
        return JsonResponse({"erreur": _i18n_t("engine/views_analyse_media.py:interrompu")}, status=500)
    reponse = HttpResponse(pdf, content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="analyse_media_{table.id}.pdf"'
    return reponse
