"""
PROGRAMME DE FORMULES — plusieurs briques enchaînées, avec conditions et boucles imbriquées.

Un programme est une liste d'étapes (JSON, stockée dans `parametres["programme"]` d'un
FormuleChamp/FormuleLigne d'opérateur « programme »). Chaque étape range son résultat dans une
variable ; les étapes suivantes (et les conditions/boucles) réutilisent cette variable comme
n'importe quelle colonne de la ligne — c'est la cascade : résultat 1 → étape 2 → étape 3 → …

  {"t":"calc", "op":"somme", "ops":["prix","tva"], "var":"r1"}        # brique de calcul (opérateur ∑ ou formule_id)
  {"t":"poser", "var":"x", "val":"prix"}                              # affectation  x = valeur
  {"t":"si", "cond":C, "alors":[…], "sinonsi":[{"cond":C,"alors":[…]}, …], "sinon":[…]}   # if / elif / else
  {"t":"pour", "var":"i", "de":1, "a":"n", "pas":2, "corps":[…], "sinon":[…]}             # for i in range(de, a inclus, pas) … else
  {"t":"pour", "var":"x", "dans":["a","b"], "corps":[…], "sinon":[…]}                     # for x in […] … else
  {"t":"tantque", "cond":C, "corps":[…], "sinon":[…]}                                     # while … else
  {"t":"arreter"} {"t":"continuer"} {"t":"passer"} {"t":"retourner", "val":x}             # break / continue / pass / return

Le « sinon » d'une boucle s'exécute quand elle se termine sans « arreter » (for/else, while/else de Python).
Condition C : {"g":x, "o":"==|!=|<|<=|>|>=|contient|ne_contient_pas|dans|pas_dans|commence_par|finit_par|vide|non_vide", "d":y},
ou {"et":[C…]}, {"ou":[C…]}, {"non":C}. « dans » : g in d (d = liste « a ; b », liste calculée ou texte).
Un opérande est un nom (colonne ou variable) ou une valeur littérale (nombre, texte).
Résultat écrit dans la colonne : la variable « resultat » si le programme l'a posée, sinon la dernière variable posée.
Garde-fou : LIMITE_PAS étapes/itérations au total (boucle sans fin → erreur claire, jamais de blocage).

Deux contextes d'exécution, même format : (1) ligne par ligne (table dynamique, KPI) — les opérandes sont les valeurs de la ligne ;
(2) table de calcul (`df` fourni) — un opérande est un résultat déjà calculé (variable ou autre calcul) OU une colonne de la table
source, dont TOUTES les valeurs sont agrégées (comme =SOMME(B:B)) ; une brique « formule de bibliothèque » reçoit la colonne/les
colonnes entières, exactement comme une ligne de calcul classique.
"""
from engine.i18n import t as _i18n_t, tf
import operator
from engine.i18n import tf as _gtf  # noqa: E402

LIMITE_PAS = 1000
_CMP = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
COMPARATEURS = tuple(_CMP) + ("contient", "ne_contient_pas", "dans", "pas_dans", "commence_par", "finit_par", "vide", "non_vide")
ETAPES_SIMPLES = ("arreter", "continuer", "passer")


class _Arreter(Exception):    # break
    pass


class _Continuer(Exception):  # continue
    pass


class _Retour(Exception):     # return
    def __init__(self, valeur=None, pose=False):
        self.valeur, self.pose = valeur, pose


def _val(x, ctx):
    """Valeur d'un opérande : nom connu → sa valeur ; sinon nombre ; sinon texte tel quel."""
    if isinstance(x, str):
        if x in ctx:
            return ctx[x]
        try:
            return float(x.replace(",", "."))
        except ValueError:
            return x
    return x


def _nombre(x):
    from .calc_champ import _vers_nombre
    n = _vers_nombre(x)
    if n is None:
        raise ValueError(tf("engine/formules_programme.py:g:66:26", v1=x))
    return n


def _egal(a, b):
    from .calc_champ import _vers_nombre
    na, nb = _vers_nombre(a), _vers_nombre(b)
    return na == nb if na is not None and nb is not None else str(a) == str(b)


def _texte(x, ctx):
    return str(ctx[x]) if isinstance(x, str) and x in ctx else str(x if x is not None else "")


def _membres(d, ctx):
    """Second membre de « dans » : liste littérale « a ; b » (chaque élément résolu), liste calculée, ou texte."""
    if isinstance(d, str) and ";" in d:
        return [_val(x.strip(), ctx) for x in d.split(";")]
    return d


def _condition(c, ctx):
    if not isinstance(c, dict):
        raise ValueError(_i18n_t("engine/formules_programme.py:g:89:26"))
    if "et" in c:
        return all(_condition(x, ctx) for x in c["et"])
    if "ou" in c:
        return any(_condition(x, ctx) for x in c["ou"])
    if "non" in c:
        return not _condition(c["non"], ctx)
    o = c.get("o", "==")
    if o not in COMPARATEURS:
        raise ValueError(_gtf("rg8.msg.036", o=o))
    g, brut_d = _val(c.get("g"), ctx), c.get("d")
    d = _membres(brut_d, ctx) if o in ("dans", "pas_dans") else _val(brut_d, ctx)
    if o in ("vide", "non_vide"):
        return (g in (None, "")) == (o == "vide")
    if o in ("dans", "pas_dans"):
        dedans = any(_egal(g, x) for x in d) if isinstance(d, (list, tuple, set)) else str(g) in str(d)
        return dedans == (o == "dans")
    if o in ("contient", "ne_contient_pas", "commence_par", "finit_par"):   # opérations sur le texte : un littéral reste tel quel (« 001 » ≠ 1.0)
        t, u = _texte(c.get("g"), ctx), _texte(brut_d, ctx)
        return {"contient": u in t, "ne_contient_pas": u not in t, "commence_par": t.startswith(u), "finit_par": t.endswith(u)}[o]
    from .calc_champ import _vers_nombre
    a, b = _vers_nombre(g), _vers_nombre(d)
    return _CMP[o](a, b) if a is not None and b is not None else _CMP[o](str(g), str(d))


def _formule(etape, env):
    from .models import FormuleCalcul
    formule = FormuleCalcul.objects.filter(id=etape.get("formule_id")).first()
    if not formule or formule.entreprise_id not in (None, env["table"].entreprise_id):
        raise ValueError(_i18n_t("engine/formules_programme.py:g:118:26"))
    return formule


def _calc_ligne(etape, ctx, env):
    """Contexte « ligne » : opérandes = colonnes/variables de la ligne courante, ou littéraux."""
    from .calc_champ import calculer_valeur_champ
    op, d, slugs = etape.get("op") or "somme", dict(ctx), []
    for k, x in enumerate(etape.get("ops") or []):
        if isinstance(x, str) and x in ctx:
            slugs.append(x)
        else:  # valeur littérale : colonne temporaire, jamais écrite
            d[f"_l{k}"] = _val(x, ctx)
            slugs.append(f"_l{k}")
    formule = _formule(etape, env) if op == "personnalisee" else None
    return calculer_valeur_champ(
        d, op, slugs, formule, etape.get("params"), table=env["table"], enregistrement=env["enregistrement"],
        df_table=env["df_table"], ids_ordonnes=env["ids_ordonnes"], memo=env["memo"],
    )


def _calc_table(etape, ctx, env):
    """Contexte « table de calcul » : variable → sa valeur ; colonne de la table source → toutes ses valeurs numériques ; sinon littéral."""
    import pandas as pd
    from .calc_champ import _appliquer_operateur, _vers_nombre
    df, op, ops = env["df"], etape.get("op") or "somme", etape.get("ops") or []
    if op == "personnalisee":
        colonnes = [x for x in ops if x in df.columns]
        if not colonnes:
            return None, _i18n_t("engine/formules_programme.py:g:147:26")
        formule = _formule(etape, env)
        return env["executer_formule"](formule, df[colonnes[0]] if formule.cible_type == "colonne" else df[colonnes])
    nombres = []
    for x in ops:
        if isinstance(x, str) and x not in ctx and x in df.columns:
            valeurs = pd.to_numeric(df[x], errors="coerce").dropna().tolist()
        else:
            v = _val(x, ctx)
            valeurs = v if isinstance(v, list) else [v]
        for y in valeurs:
            n = _vers_nombre(y)
            if n is None:
                raise ValueError(tf("engine/formules_programme.py:g:160:34", v1=y))
            nombres.append(n)
    return _appliquer_operateur(op, nombres) if nombres else (None, _i18n_t("engine/formules_programme.py:g:162:69"))


def sous_listes(etape):
    """Toutes les listes d'étapes imbriquées d'une étape (alors, chaque « sinonsi », sinon, corps) — pour parcourir un programme."""
    yield from (etape.get("alors"), *(b.get("alors") for b in etape.get("sinonsi") or [] if isinstance(b, dict)),
                etape.get("sinon"), etape.get("corps"))


def _noms_condition(c, sortie):
    if isinstance(c, dict):
        for sous in (c.get("et") or []) + (c.get("ou") or []) + ([c["non"]] if "non" in c else []):
            _noms_condition(sous, sortie)
        for k in ("g", "d"):
            if c.get(k) is not None:
                sortie.update(x.strip() for x in str(c[k]).split(";"))


def noms_references(programme, sortie=None):
    """Tous les opérandes cités par un programme (colonnes, variables, autres calculs, littéraux) — sert à dériver les dépendances."""
    sortie = set() if sortie is None else sortie
    for e in programme or []:
        for cle in ("de", "a", "pas", "val"):
            if e.get(cle) is not None:
                sortie.add(str(e[cle]))
        sortie.update(str(x) for x in (e.get("ops") or []) + (e.get("dans") or []))
        _noms_condition(e.get("cond"), sortie)
        for branche in e.get("sinonsi") or []:
            _noms_condition(branche.get("cond"), sortie)
        for liste in sous_listes(e):
            noms_references(liste, sortie)
    return sortie


def _executer(etapes, ctx, env):
    for etape in etapes or []:
        _compter(env)
        t = etape.get("t")
        if t == "calc":
            valeur, erreur = _calc_table(etape, ctx, env) if env["df"] is not None else _calc_ligne(etape, ctx, env)
            if erreur:
                raise ValueError(tf("engine/formules_programme.py:g:203:34", v1=etape.get('op') or 'somme', v2=erreur))
            _poser(etape.get("var") or "resultat", valeur, ctx, env)
        elif t == "poser":
            _poser(etape.get("var") or "resultat", _val(etape.get("val"), ctx), ctx, env)
        elif t == "si":   # if / elif / else : première branche vraie, sinon le « sinon »
            branches = [etape] + [b for b in etape.get("sinonsi") or [] if isinstance(b, dict)]
            choisie = next((b.get("alors") for b in branches if _condition(b.get("cond"), ctx)), etape.get("sinon"))
            _executer(choisie, ctx, env)
        elif t == "pour":
            _boucle(etape, _valeurs_pour(etape, ctx), ctx, env)
        elif t == "tantque":
            _boucle(etape, _tant_que(etape, ctx, env), ctx, env)
        elif t == "arreter" or t == "continuer":
            if not env["boucles"]:
                raise ValueError(tf("engine/formules_programme.py:g:217:34", v1=t))
            raise _Arreter() if t == "arreter" else _Continuer()
        elif t == "retourner":
            raise _Retour(_val(etape.get("val"), ctx), etape.get("val") not in (None, ""))
        elif t != "passer":
            raise ValueError(tf("engine/formules_programme.py:g:222:30", v1=t))


def _poser(var, valeur, ctx, env):
    ctx[var] = valeur
    env["derniere"] = var
    env["posees"].add(var)


def _valeurs_pour(etape, ctx):
    """Séquence d'une boucle « pour » : liste de colonnes/variables, ou range(de, a inclus, pas) — le pas peut être négatif."""
    if etape.get("dans") is not None:
        return [_val(x, ctx) for x in etape["dans"]]
    de, a = int(_nombre(_val(etape.get("de", 1), ctx))), int(_nombre(_val(etape.get("a", 1), ctx)))
    brut = etape.get("pas")
    pas = 1 if brut in (None, "") else int(_nombre(_val(brut, ctx)))
    if pas == 0:
        raise ValueError(_i18n_t("engine/formules_programme.py:g:239:26"))
    return range(de, a + (1 if pas > 0 else -1), pas)


def _tant_que(etape, ctx, env):
    while _condition(etape.get("cond"), ctx):
        yield None


def _boucle(etape, valeurs, ctx, env):
    """for / while commun : « arreter » sort, « continuer » passe à l'itération suivante, « sinon » s'exécute sans « arreter »."""
    var, interrompue = etape.get("var") or "i", False
    for v in valeurs:
        _compter(env)
        if etape.get("t") == "pour":
            ctx[var] = v
        env["boucles"] += 1
        try:
            _executer(etape.get("corps"), ctx, env)
        except _Continuer:
            pass
        except _Arreter:
            interrompue = True
        finally:
            env["boucles"] -= 1
        if interrompue:
            break
    if not interrompue:
        _executer(etape.get("sinon"), ctx, env)


def _compter(env):
    env["pas"] -= 1
    if env["pas"] < 0:
        raise ValueError(tf("engine/formules_programme.py:g:273:26", v1=LIMITE_PAS))


def executer_programme(programme, ligne_dict, table=None, enregistrement=None, df_table=None, ids_ordonnes=None, memo=None,
                       df=None, executer_formule=None):
    """Exécute `programme` sur la ligne `ligne_dict`. Retourne (valeur, erreur) — comme calculer_valeur_champ.
    `df` (+ `executer_formule(formule, cible) → (valeur, erreur)`) : contexte table de calcul, voir l'en-tête du module."""
    if not isinstance(programme, list) or not programme:
        return None, _i18n_t("engine/formules_programme.py:g:281:22")
    ctx = dict(ligne_dict)
    env = {"table": table, "enregistrement": enregistrement, "df_table": df_table, "ids_ordonnes": ids_ordonnes,
           "memo": memo, "pas": LIMITE_PAS, "boucles": 0, "derniere": None, "posees": set(), "df": df, "executer_formule": executer_formule}
    try:
        _executer(programme, ctx, env)
    except _Retour as retour:
        if retour.pose:
            return retour.valeur, None
    except (_Arreter, _Continuer):
        return None, _i18n_t("engine/formules_programme.py:g:291:22")
    except Exception as exc:
        return None, str(exc) or _i18n_t("engine/formules_programme.py:g:293:34")
    cle = "resultat" if "resultat" in env["posees"] else env["derniere"]
    if cle is None:
        return None, _i18n_t("engine/formules_programme.py:g:296:22")
    return ctx.get(cle), None
