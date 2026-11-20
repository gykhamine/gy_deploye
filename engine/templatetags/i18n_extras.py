"""Balises {% t "cle" %} / {% tf "cle" nom=... %} : exposent engine/i18n.py
aux templates HTML (l'interface graphique servie au navigateur), sur le
même principe que t() côté GTK.

Usage dans un template :
    {% load i18n_extras %}
    <h1>{% t "engine/templates/engine/login.html:14:8" %}</h1>
    <p>{% tf "engine/templates/engine/x.html:5:4" nom=objet.nom %}</p>

Ne pas confondre avec {% load i18n %} (le système de traduction natif de
Django, gettext) : ces balises passent par nos propres catalogues JSON
(engine/i18n_defaults/), pas par les fichiers .po de Django.
"""
from django import template

from ..i18n import t as _t
from ..i18n import tf as _tf

register = template.Library()


@register.simple_tag
def t(cle):
    return _t(cle)


@register.simple_tag
def tf(cle, **kwargs):
    return _tf(cle, **kwargs)


# ── {% i18n_js %} (ajouté par i18n_patch_js_html.py) ────────────────────────────
# Injecte dans la page le catalogue de la langue courante pour les textes passés en JS
# (clés « engine/static/… », « …html:js:L:C » et « …html:a:L:nom ») et définit window.tj(clé, vars).
import json as _json
from django.utils.safestring import mark_safe as _mark_safe

_JS_CACHE = {}

_TJ_SRC = (
    "window.tj=function(c,v){var d=window.I18N_JS||{},s=(d[c]!==undefined&&d[c]!==\"\")?d[c]:c;"
    "if(v){s=s.replace(/\\{(v\\d+)\\}/g,function(m,k){return v[k]!==undefined?v[k]:m;});}return s;};"
)


# Clés tj() hors des préfixes habituels (affichage des onglets d'index, pages flux/wallet) : sinon tj() renvoie la clé brute.
_CLES_JS_EXTRA = frozenset(['engine/templates/engine/agent_flux.html:bloc_manquant', 'engine/templates/engine/agent_flux.html:enregistre', 'engine/templates/engine/agent_flux.html:nouveau_solde', 'engine/templates/engine/agent_flux.html:recevoir', 'engine/templates/engine/agent_flux.html:remettre', 'engine/templates/engine/comptabilite_wallet.html:blocs_manquants', 'engine/templates/engine/comptabilite_wallet.html:charges', 'engine/templates/engine/comptabilite_wallet.html:enregistree', 'engine/templates/engine/comptabilite_wallet.html:habiliter', 'engine/templates/engine/comptabilite_wallet.html:nb_ecritures', 'engine/templates/engine/comptabilite_wallet.html:transferts_internes', 'engine/views_analyse_media.py:titre'])


def _catalogue_js(langue):
    from ..i18n import _charger_catalogues, LANGUE_PAR_DEFAUT, LANGUE_SOURCE, appliquer_devise as _appliquer_devise
    cats = _charger_catalogues()
    from ..i18n import devise_affichage as _dev
    cle_cache = (id(cats), langue, _dev())
    if cle_cache not in _JS_CACHE:
        _JS_CACHE.clear()
        fr, cur = cats.get(LANGUE_SOURCE, {}), cats.get(langue, {})
        defaut = cats.get(LANGUE_PAR_DEFAUT, {})
        _JS_CACHE[cle_cache] = {
            c: _appliquer_devise(cur.get(c) or defaut.get(c) or texte)
            for c, texte in fr.items()
            if c.startswith("engine/static/") or ":js:" in c or ":a:" in c or c.startswith("groupes.") or c.startswith("decoupage.ui.") or c in _CLES_JS_EXTRA
        }
    return _JS_CACHE[cle_cache]


@register.simple_tag(takes_context=True)
def i18n_js(context):
    """À poser une fois dans la page, AVANT les scripts qui appellent tj()."""
    racine = context.dicts[0]
    if racine.get("_i18n_js_emis"):
        return ""
    racine["_i18n_js_emis"] = True
    from ..i18n import langue_courante, devise_affichage as _dev_aff
    data = _json.dumps(_catalogue_js(langue_courante()), ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    loc = _json.dumps(langue_courante())
    return _mark_safe("<script>window.I18N_JS=" + data + ";window.I18N_LOCALE=" + loc + ";window.DEVISE_LABEL=" + _json.dumps(_dev_aff()).replace("<", "\\u003c") + ";" + _TJ_SRC + "</script>")
