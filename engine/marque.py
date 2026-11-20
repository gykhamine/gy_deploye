"""Personnalisation de la marque (marque blanche) : nom, logo, slogans et couleurs de l'interface.

Aucune logique métier ne change : ce ne sont que des PROPRIÉTÉS de l'Entreprise (une seule par installation) lues par
  - GET /marque.css  : surcharge des variables de couleur de app.css (liée dans toutes les pages, après app.css) ;
  - GET /api/marque/ : nom, logo et slogans, appliqués par static/engine/js/marque.js (logo, intro, barre latérale, titre).
Rien n'est défini = identité d'origine (logo, nom et slogans des catalogues i18n, couleurs de app.css).
Le logo et le nom restent ceux de l'Entreprise (déjà configurables : page Équipe) ; le nom ne remplace celui de l'application
que s'il a été choisi (le nom créé automatiquement « Entreprise de … » est ignoré).
"""
import re as _re

from . import couleur_colonne as cc
from .i18n import t
from .i18n import tfr as _tfr_marque  # i18n-p9

# Variables de couleur de app.css (valeurs d'origine de body.page-index / body.page-reco) — une ligne = un réglage de la page.
VARIABLES_FOND = (
    ("bg", "#000000"), ("panel", "#0a0a0a"), ("panel2", "#111111"), ("border", "#2a2a2a"), ("accent", "#6bcfff"), ("hover", "#10222e"),
    ("danger", "#e74c3c"), ("ok", "#2ecc71"), ("warn", "#f0b13a"), ("agir", "#e74c3c"), ("decider", "#f0b13a"), ("surveiller", "#6bcfff"),
)
# Couleurs de TEXTE : une ligne = un réglage ; la variable CSS porte le même nom (« _ » devient « - »), voir nom_variable().
# Valeur d'origine = celle qui était écrite en dur dans app.css / les gabarits (rien n'est posé tant que l'administrateur n'y touche pas).
VARIABLES_TEXTE = (
    ("text", "#4aa3df"), ("muted", "#555555"),
    ("slogan", "#555555"), ("slogan2", "#3a5060"), ("nom_app", "#6bcfff"),
    ("texte_accent", "#000000"), ("texte_danger", "#ffffff"), ("texte_ok", "#0d1f0d"), ("texte_avert", "#f5c26b"),
    ("texte_info", "#7dd3fc"), ("texte_succes", "#7fd99a"), ("texte_clair", "#e6e6e6"), ("texte_discret", "#888888"),
    ("texte_leger", "#555555"), ("texte_tertiaire", "#4a5060"), ("texte_nav", "#9fb4c2"), ("texte_sombre", "#ffffff"), ("texte_document", "#000000"),
    ("texte_lien", "#8ab4ff"), ("texte_erreur", "#ff8a80"), ("texte_orange", "#f59e0b"), ("texte_violet", "#a855f7"), ("texte_noir", "#000000"),
    ("texte_succes_fonce", "#166534"), ("texte_erreur_fonce", "#991b1b"),
)
# Couleurs des FORMES dessinées sur canvas (cartes, tracés, graphes) : lues par static/engine/js/gy_couleur.js (gyCouleur).
VARIABLES_FORMES = (
    ("forme_rouge", "#ff4d4d"), ("forme_orange", "#ff9f6b"), ("forme_jaune", "#ffd166"), ("forme_vert", "#9ad06b"), ("forme_bleu", "#3aa0ff"),
    ("forme_cyan", "#5dd6c0"), ("forme_violet", "#b084ff"), ("forme_rose", "#ff7ab8"), ("forme_contour", "#000000"), ("forme_clair", "#ffffff"),
    ("forme_fond", "#0b1e33"), ("forme_gris", "#888888"),
    # Séries des graphiques (anneaux, barres, courbes) : les 8 premières couleurs que le code calculait en teinte automatique.
    ("serie_1", "#cb4d4d"), ("serie_2", "#cbb04d"), ("serie_3", "#84cb4d"), ("serie_4", "#4dcb79"),
    ("serie_5", "#4dbbcb"), ("serie_6", "#4d58cb"), ("serie_7", "#a54dcb"), ("serie_8", "#cb4d8e"),
    # Palette étendue des tableaux de bord, dégradés des cartes de chaleur, annotation et roue du sélecteur de couleur.
    ("forme_indigo", "#6d5efc"), ("forme_turquoise", "#06b6d4"), ("forme_magenta", "#ec4899"), ("forme_lime", "#84cc16"),
    ("forme_orange_vif", "#f97316"), ("forme_violet_vif", "#8b5cf6"), ("forme_sarcelle", "#14b8a6"), ("forme_jaune_vif", "#fbbf24"),
    ("degrade_debut", "#1f3b73"), ("degrade_fin", "#f5a524"), ("chaleur_debut", "#ffaa28"), ("chaleur_fin", "#ff3c28"),
    ("forme_annotation", "#f5a524"), ("forme_selecteur", "#1e88e5"),
)
# Fonds secondaires, voiles et ombres (écrits en dur dans app.css / les gabarits / le JS avant la personnalisation) et BORDURES.
# Les voiles et l'ombre se combinent avec la transparence d'origine : color-mix(var(--voile) N %, transparent).
VARIABLES_FONDS = (
    ("fond_noir", "#000000"), ("fond_tres_sombre", "#0a0a0a"), ("fond_sombre", "#111111"), ("fond_releve", "#1a1a1a"),
    ("fond_info", "#0a2030"), ("fond_succes", "#0a2a1a"), ("fond_danger", "#3a1a1a"), ("fond_avert", "#332b00"),
    ("fond_clair", "#f5f5f5"), ("fond_avert_clair", "#fff3cd"), ("fond_danger_clair", "#fdecea"),
    ("voile", "#000000"), ("voile_clair", "#ffffff"), ("ombre", "#000000"),
    ("fond_bandeau", "#0f1216"), ("fond_danger_profond", "#2d1416"), ("badge_brouillon", "#a0a0a0"),
)
VARIABLES_BORDURES = (
    ("bordure_forte", "#3a3a3a"), ("bordure_info", "#3a5060"), ("bordure_danger", "#4a2b28"), ("bordure_avert", "#5a4a1a"),
    ("bordure_succes", "#2e4e2e"), ("bordure_claire", "#e3e6eb"), ("bordure_erreur", "#4e2e2e"),
)
# Feuille d'édition des documents (page blanche), aperçus de la configuration des documents et valeurs proposées par défaut aux champs « couleur ».
# Les PDF générés suivent, eux, la configuration de chaque document (page « Configuration des documents ») : elle se règle déjà librement.
VARIABLES_DOCUMENT = (
    ("doc_fond", "#ffffff"), ("doc_texte", "#1a1a1a"), ("doc_texte_saisie", "#111111"), ("doc_texte_discret", "#444444"),
    ("doc_texte_config", "#1a1a2e"), ("doc_texte_zone", "#000000"), ("doc_bordure", "#cbd5e1"), ("doc_survol", "#f8fafc"),
    ("doc_entete", "#1f2937"), ("doc_tableau_entete", "#0d1117"), ("doc_pied", "#f3f4f6"), ("doc_lignes", "#f3f4f6"),
    ("doc_accent", "#e8a33d"), ("doc_carte_bordure", "#999999"), ("doc_carte_separateur", "#dddddd"),
)
VARIABLES_COULEUR = VARIABLES_FOND + VARIABLES_TEXTE + VARIABLES_FORMES + VARIABLES_FONDS + VARIABLES_BORDURES + VARIABLES_DOCUMENT


def nom_variable(cle):
    """Nom de la variable CSS d'un réglage (« texte_accent » -> « --texte-accent »)."""
    return "--" + cle.replace("_", "-")
_DEFAUTS = dict(VARIABLES_COULEUR)
# Polices proposées (piles système : aucune police à télécharger) ; la clé est stockée, la pile est posée sur le <body>.
POLICES = {
    "systeme": 'system-ui,-apple-system,"Segoe UI",Roboto,sans-serif', "sans": 'Arial,Helvetica,sans-serif',
    "serif": 'Georgia,"Times New Roman",serif', "mono": 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace',
}
TAILLE_MIN, TAILLE_MAX = 12, 22          # taille du texte (px)
BARRE_MIN, BARRE_MAX = 180, 400          # largeur de la barre latérale (px)
LONGUEUR_CSS = 20000
# CSS libre : tout est permis sauf ce qui charge une ressource extérieure ou exécute du code.
_CSS_INTERDIT = _re.compile(r"@import|expression\s*\(|javascript:|behavior\s*:|-moz-binding|</|url\(\s*['\"]?\s*(?:https?:)?//", _re.I)
LONGUEUR_SLOGAN = 120
# ── Apparence étendue : fond d'écran, transparences, arrondis, règles par élément (tout reste de l'APPARENCE) ──
AJUSTEMENTS = {"cover": "background-size:cover;background-repeat:no-repeat;", "contain": "background-size:contain;background-repeat:no-repeat;",
               "repeat": "background-size:auto;background-repeat:repeat;"}
OMBRES = {"aucune": "none", "douce": "0 4px 14px rgba(0,0,0,.35)", "forte": "0 10px 30px rgba(0,0,0,.65)", "lueur": "0 0 16px var(--accent)"}
RAYON_MAX, FLOU_MAX, MAX_REGLES = 40, 40, 300
_DOSSIER_IMAGES = "marque/"
_SELECTEUR = _re.compile(r"[A-Za-z0-9_\-#.\s>+~:\[\]=\"'*,()^$|]{1,300}")
_PAGE = _re.compile(r"/[A-Za-z0-9_\-/.]{0,200}")
# propriété autorisée -> type de valeur (« px » entier, « pct » 0-100, « c » couleur, sinon liste de valeurs permises)
PROPRIETES = {
    "color": "c", "background-color": "c", "border-color": "c", "border-width": ("px", 20), "border-radius": ("px", 200),
    "font-size": ("px", 120), "letter-spacing": ("px", 30), "opacity": "pct", "backdrop-filter": ("flou", FLOU_MAX),
    "box-shadow": tuple(OMBRES), "text-shadow": tuple(OMBRES), "border-style": ("solid", "dashed", "dotted", "double", "none"),
    "font-weight": ("300", "400", "600", "700", "800"), "font-style": ("normal", "italic"),
    "text-transform": ("none", "uppercase", "lowercase", "capitalize"), "background-image": "img",
    "background-size": ("cover", "contain", "auto"), "background-repeat": ("no-repeat", "repeat"), "background-position": ("center", "top", "bottom", "left", "right"),
}


def _image_locale(v):
    """Seules les images déposées par l'administrateur (dossier marque/ du stockage) sont permises : jamais de ressource extérieure."""
    from django.conf import settings
    return isinstance(v, str) and v.startswith(settings.MEDIA_URL + _DOSSIER_IMAGES) and bool(_re.fullmatch(r"[A-Za-z0-9_\-/.]+", v)) and ".." not in v


def valeur_css(prop, v):
    """Valeur stockée -> texte CSS ; ValueError si elle est refusée."""
    kind = PROPRIETES.get(prop)
    if kind is None:
        raise ValueError(prop)
    if kind == "c":
        n = cc.normaliser_couleur(v)
        if not n:
            raise ValueError(prop)
        return n
    if kind == "img":
        if v == "none":
            return "none"
        if not _image_locale(v):
            raise ValueError(prop)
        return f'url("{v}")'
    if kind == "pct":
        if _entier(v, 0, 100) is None:
            raise ValueError(prop)
        return str(v / 100)
    if isinstance(kind, tuple) and kind[0] in ("px", "flou") and len(kind) == 2:
        if _entier(v, 0, kind[1]) is None:
            raise ValueError(prop)
        return f"{v}px" if kind[0] == "px" else f"blur({v}px)"
    if v not in kind:
        raise ValueError(prop)
    return OMBRES[v] if prop in ("box-shadow", "text-shadow") else v


def _regle_propre(r):
    """Une règle {page, sel, style} nettoyée, ou ValueError."""
    if not isinstance(r, dict) or not isinstance(r.get("style"), dict):
        raise ValueError("regle")
    page, sel = r.get("page", "*"), r.get("sel", "")
    if page != "*" and not (isinstance(page, str) and _PAGE.fullmatch(page)):
        raise ValueError("page")
    if not (isinstance(sel, str) and _SELECTEUR.fullmatch(sel) and not _CSS_INTERDIT.search(sel)):
        raise ValueError("sel")
    style = {}
    for prop, v in r["style"].items():
        valeur_css(prop, v)
        style[prop] = v
    return {"page": page, "sel": sel.strip(), "style": style} if style else None


def _regles(entreprise):
    sortie = []
    for r in ((getattr(entreprise, "marque", None) or {}).get("elements") or []):
        try:
            propre = _regle_propre(r)
        except (ValueError, TypeError):
            continue
        if propre:
            sortie.append(propre)
    return sortie


def libelles():
    """Textes de l'éditeur visuel (aucun texte d'interface n'est écrit dans le JavaScript)."""
    return {k: t("engine/marque.py:" + k) for k in ['ed_bouton', 'ed_titre', 'ed_parent', 'ed_semblables', 'ed_seul', 'ed_portee', 'ed_page', 'ed_toutes', 'ed_fermer', 'ed_retirer', 'ed_enregistrer', 'ed_enregistre', 'ed_texte', 'ed_fond', 'ed_bordure', 'ed_epaisseur', 'ed_arrondi', 'ed_opacite', 'ed_taille', 'ed_ombre', 'ed_flou', 'ed_image', 'ed_choisir', 'ed_graisse', 'ed_style', 'ed_motif', 'ed_aucune', 'ed_douce', 'ed_forte', 'ed_lueur', 'ed_invite', 'ed_transparence']}


def _rgb(hexa):
    return ",".join(str(int(hexa[i:i + 2], 16)) for i in (1, 3, 5))
_PREFIXE_NOM_AUTO = _tfr_marque("engine/marque.py:prefixe_nom_auto")   # ancien nom créé automatiquement (bases existantes) : ignoré, jamais recréé
# Textes d'origine (catalogue, déjà utilisés par les pages d'intro) : nom de l'application et deux slogans.
CLE_NOM, CLE_SLOGAN, CLE_SLOGAN2 = (f"engine/templates/engine/index.html:{c}" for c in ("16:27", "17:26", "18:30"))


def nom_marque(entreprise):
    """Nom choisi pour l'entreprise, ou None (= nom d'origine de l'application)."""
    nom = (getattr(entreprise, "nom", "") or "").strip()
    return None if not nom or nom.startswith(_PREFIXE_NOM_AUTO) else nom


def nom_affiche(entreprise):
    """Nom à montrer dans l'interface : uniquement celui que l'administrateur a donné (rien d'inventé, jamais « Entreprise de … »)."""
    return nom_marque(entreprise) or ""


def _entier(v, mini, maxi):
    return v if isinstance(v, int) and not isinstance(v, bool) and mini <= v <= maxi else None


def _couleurs(entreprise):
    brut = (getattr(entreprise, "marque", None) or {}).get("couleurs")
    propre = cc.normaliser_couleurs_cellules(brut if isinstance(brut, dict) else {})
    return {k: v for k, v in propre.items() if k in _DEFAUTS}


def actif(entreprise):
    """Thème de l'entreprise activé ? Clé absente = activé (comportement d'avant) ; seul « actif: false » rend l'identité d'origine."""
    return (getattr(entreprise, "marque", None) or {}).get("actif") is not False


def lire(entreprise, brut=False):
    """Tout ce que le navigateur doit savoir : None = valeur d'origine (la page garde son texte / sa couleur).

    Thème désactivé par l'entreprise : identité d'origine pour toutes les pages (réglages conservés, jamais effacés). `brut=True` rend quand même
    les réglages enregistrés : réservé à la page de personnalisation, pour que l'administrateur les voie et ne les écrase pas en enregistrant."""
    en_vigueur = actif(entreprise)
    if not en_vigueur and not brut:
        entreprise = None   # identité d'origine : ni couleurs, ni logo, ni nom, ni CSS de l'entreprise
    m = (getattr(entreprise, "marque", None) or {})
    logo = getattr(entreprise, "logo", None)
    return {
        "actif": en_vigueur,
        "nom": nom_marque(entreprise), "defaut_nom": t(CLE_NOM),
        "logo_url": logo.url if logo else None,
        "slogan": m.get("slogan") if isinstance(m.get("slogan"), str) else None,
        "slogan2": m.get("slogan2") if isinstance(m.get("slogan2"), str) else None,
        "defaut_slogan": t(CLE_SLOGAN), "defaut_slogan2": t(CLE_SLOGAN2),
        "couleurs": _couleurs(entreprise),
        "defaut_couleurs": _DEFAUTS,
        "police": m.get("police") if m.get("police") in POLICES else None,
        "taille": _entier(m.get("taille"), TAILLE_MIN, TAILLE_MAX),
        "largeur_barre": _entier(m.get("largeur_barre"), BARRE_MIN, BARRE_MAX),
        "css": m.get("css") if isinstance(m.get("css"), str) else "",
        "fond": _fond(m.get("fond")), "rayon": _entier(m.get("rayon"), 0, RAYON_MAX),
        "opacite_panneaux": _entier(m.get("opacite_panneaux"), 0, 100), "opacite_zones": _entier(m.get("opacite_zones"), 0, 100),
        "elements": _regles(entreprise),
    }


def _fond(f):
    """Fond d'écran : {url, ajustement, fixe, voile (0-100), voile_couleur, flou} ou None."""
    if not isinstance(f, dict) or not _image_locale(f.get("url")):
        return None
    return {"url": f["url"], "ajustement": f.get("ajustement") if f.get("ajustement") in AJUSTEMENTS else "cover", "fixe": bool(f.get("fixe", True)),
            "voile": _entier(f.get("voile"), 0, 100) or 0, "flou": _entier(f.get("flou"), 0, FLOU_MAX) or 0,
            "voile_couleur": cc.normaliser_couleur(f.get("voile_couleur") or "#000000")[:7] or "#000000"}


def _texte_sur(hexa):
    """Texte noir ou blanc selon la clarté du fond (même règle que selecteur_couleur.js)."""
    v = [int(hexa[i:i + 2], 16) for i in (1, 3, 5)]
    return "#07141c" if (0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]) > 150 else "#ffffff"


def css(entreprise, page=None):
    """Feuille de style : variables, police, taille, barre latérale posées sur le <body> de chaque page (après app.css, donc
    prioritaires), puis le CSS libre de l'administrateur."""
    m = lire(entreprise)
    c = m["couleurs"]
    lignes = [f"{nom_variable(k)}:{v};" for k, v in c.items()]
    if "bg" in c:
        lignes.append(f"--bg-plein:{c['bg']};")   # fond uni du thème (l'intro ne doit jamais être translucide)
    if "texte_accent" in c:
        lignes.append(f"--on-accent:{c['texte_accent']};")
    elif "accent" in c:
        lignes.append(f"--on-accent:{_texte_sur(c['accent'])};")
    if m["police"]:
        lignes.append(f"font-family:{POLICES[m['police']]};")
    if m["taille"]:
        lignes.append(f"font-size:{m['taille']}px;")
    if m["largeur_barre"]:
        lignes.append(f"--sidebar-w:{m['largeur_barre']}px;")
    if m["rayon"] is not None:   # la page client lit --rayon (cartes, confirmation) : elle suit l'arrondi réglé par l'administrateur, comme les autres pages
        lignes.append(f"--rayon:{m['rayon']}px;")
    sortie = ("body[class]{" + "".join(lignes) + "}\n") if lignes else ""
    if m["logo_url"]:   # logo de l'entreprise : celui d'origine ne s'affiche jamais, même un instant (marque.js lève le masque une fois le logo posé)
        sortie += "html:not(.marque-prete) body[class] :is(img.logo-gy,img.intro-logo-img,.login-logo img){visibility:hidden}\n"
    if m["nom"]:   # nom de l'application des pages de connexion / inscription : celui d'origine ne s'affiche jamais, même un instant
        sortie += "html:not(.marque-prete) body[class] .login-logo h1{visibility:hidden}\n"
    # transparences : --panel/--panel2 (cartes, volets) et --bg (zones de fond) deviennent translucides ; le fond du <body> reste plein
    for var, cle in (("panel", "opacite_panneaux"), ("panel2", "opacite_panneaux"), ("bg", "opacite_zones")):
        if m[cle] is not None:
            base = c.get(var, _DEFAUTS.get(var, c.get("panel", "#0a0a0a")))
            if var == "bg":
                sortie += f"body[class]{{--bg-plein:{base};}}\n"
            sortie += f"body[class]{{--{var}:color-mix(in srgb,{base} {m[cle]}%,transparent);" + (f"background-color:{c.get('bg', _DEFAUTS['bg'])} !important;" if var == "bg" else "") + "}\n"
    if m["rayon"] is not None:
        sortie += (f"body[class] :is(button,input,select,textarea,dialog,.carte,.card,[class*=modal],[class*=panneau],[class*=carte]){{border-radius:{m['rayon']}px !important}}\n")
    f = m["fond"]
    if f:
        sortie += (f'body[class]{{background-image:url("{f["url"]}") !important;background-position:center !important;{AJUSTEMENTS[f["ajustement"]]}'
                   f'background-attachment:{"fixed" if f["fixe"] else "scroll"} !important}}\n')
        if f["voile"] or f["flou"]:
            sortie += (f'body[class]::before{{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;background:rgba({_rgb(f["voile_couleur"])},{f["voile"] / 100});'
                       f'backdrop-filter:blur({f["flou"]}px)}}\n')
    for r in m["elements"]:
        if r["page"] in ("*", page):
            corps = "".join(f"{p}:{valeur_css(p, v)} !important;" for p, v in r["style"].items())
            sortie += ",".join("body " + part.strip() for part in r["sel"].split(",") if part.strip()) + "{" + corps + "}\n"
    return sortie + (m["css"] + "\n" if m["css"] else "")


def enregistrer(entreprise, corps):
    """Applique {slogan, slogan2, couleurs, police, taille, largeur_barre, css} reçus de la page. Une clé à null = retour à la valeur d'origine ; clé absente =
    inchangée. Lève ValueError(message traduit) si une valeur est refusée : rien n'est alors écrit."""
    m = dict(entreprise.marque or {})
    if "actif" in corps:
        if not isinstance(corps["actif"], bool):
            raise ValueError(t("engine/marque.py:erreur_actif"))
        if corps["actif"]:
            m.pop("actif", None)   # absent = activé
        else:
            m["actif"] = False
    for cle in ("slogan", "slogan2"):
        if cle in corps:
            v = corps[cle]
            if v is None:
                m.pop(cle, None)
            elif isinstance(v, str) and len(v.strip()) <= LONGUEUR_SLOGAN:
                m[cle] = v.strip()
            else:
                raise ValueError(t("engine/marque.py:erreur_slogan").format(max=LONGUEUR_SLOGAN))
    if "couleurs" in corps:
        recu = corps["couleurs"]
        if recu is None:
            m.pop("couleurs", None)
        elif isinstance(recu, dict):
            actuel = _couleurs(entreprise)
            for k, v in recu.items():
                if k not in _DEFAUTS:
                    continue
                try:
                    norm = cc.normaliser_couleur(v)
                except (ValueError, TypeError):
                    raise ValueError(t("engine/marque.py:erreur_couleur").format(nom=k))
                if norm:
                    actuel[k] = norm
                else:
                    actuel.pop(k, None)   # champ vidé = couleur d'origine
            if actuel:
                m["couleurs"] = actuel
            else:
                m.pop("couleurs", None)
        else:
            raise ValueError(t("engine/marque.py:erreur_couleur").format(nom="couleurs"))
    for cle, mini, maxi in (("taille", TAILLE_MIN, TAILLE_MAX), ("largeur_barre", BARRE_MIN, BARRE_MAX)):
        if cle in corps:
            v = corps[cle]
            if v is None:
                m.pop(cle, None)
            elif _entier(v, mini, maxi) is not None:
                m[cle] = v
            else:
                raise ValueError(t("engine/marque.py:erreur_nombre").format(mini=mini, maxi=maxi))
    if "police" in corps:
        v = corps["police"]
        if v is None:
            m.pop("police", None)
        elif v in POLICES:
            m["police"] = v
        else:
            raise ValueError(t("engine/marque.py:erreur_police"))
    if "css" in corps:
        v = corps["css"]
        if v is None or (isinstance(v, str) and not v.strip()):
            m.pop("css", None)
        elif isinstance(v, str) and len(v) <= LONGUEUR_CSS and not _CSS_INTERDIT.search(v):
            m["css"] = v.strip()
        else:
            raise ValueError(t("engine/marque.py:erreur_css").format(max=LONGUEUR_CSS))
    for cle, maxi in (("rayon", RAYON_MAX), ("opacite_panneaux", 100), ("opacite_zones", 100)):
        if cle in corps:
            v = corps[cle]
            if v is None:
                m.pop(cle, None)
            elif _entier(v, 0, maxi) is not None:
                m[cle] = v
            else:
                raise ValueError(t("engine/marque.py:erreur_nombre").format(mini=0, maxi=maxi))
    if "fond" in corps:
        v = corps["fond"]
        if v is None:
            m.pop("fond", None)
        elif isinstance(v, dict) and _fond({**(m.get("fond") or {}), **v}):
            m["fond"] = _fond({**(m.get("fond") or {}), **v})
        else:
            raise ValueError(t("engine/marque.py:erreur_fond"))
    if "elements" in corps:
        v = corps["elements"]
        if v is None:
            m.pop("elements", None)
        else:
            try:
                if not isinstance(v, list) or len(v) > MAX_REGLES:
                    raise ValueError
                m["elements"] = [r for r in map(_regle_propre, v) if r]
            except (ValueError, TypeError):
                raise ValueError(t("engine/marque.py:erreur_regle"))
    entreprise.marque = m
    entreprise.save(update_fields=["marque"])


from .image_universelle import EXTENSIONS as _EXT_IMAGES


def enregistrer_image(fichier):
    """Dépose une image (fond, motif d'un élément) dans marque/ du stockage et rend son adresse locale. ValueError si refusée."""
    import os
    import uuid
    from django.core.files.storage import default_storage
    ext = os.path.splitext(fichier.name)[1].lower()
    if ext not in _EXT_IMAGES:
        raise ValueError(t("engine/marque.py:erreur_image"))
    nom = default_storage.save(f"{_DOSSIER_IMAGES}{uuid.uuid4().hex}{ext}", fichier)
    return default_storage.url(nom)
