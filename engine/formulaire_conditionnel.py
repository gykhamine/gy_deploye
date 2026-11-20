"""Formulaire conditionnel : sections et branchements « façon Google Forms ».

La TABLE ne change jamais : elle garde les mêmes colonnes. Seule la présentation du formulaire de saisie (côté client) change :
les champs sont répartis en SECTIONS (une « page » chacune) et, à la fin d'une section, la réponse à une question à choix
(choix unique, oui/non) peut renvoyer vers une autre section, ou vers l'envoi du formulaire. Les champs des sections qui ne sont
pas traversées restent simplement vides dans la ligne enregistrée (et ne sont jamais exigés, même s'ils sont « obligatoires »).

Structure stockée dans TableDynamique.structure_formulaire (JSON) :
    {"actif": bool,
     "sections": [{"id": "s1", "titre": str, "description": str, "champs": [slug, ...],
                   "apres": "suivante" | "soumettre" | <id d'une section plus loin>,
                   "branche": None | {"champ": slug, "regles": {<valeur>: "suivante" | "soumettre" | <id>}}}]}

Les renvois ne vont que VERS L'AVANT (jamais en arrière) : un parcours est donc toujours fini.
La logique du parcours est dupliquée à l'identique dans static/engine/js/formulaire_conditionnel_client.js (aperçu en direct) ;
le serveur fait foi à l'enregistrement de la ligne.
"""
import re

from engine.i18n import t

SOUMETTRE = "soumettre"
SUIVANTE = "suivante"
TYPES_NON_SAISIS = frozenset({
    "horodatage", "id_client", "formule", "constante", "micro", "camera", "diffusion_micro", "diffusion_camera",
})
TYPES_BRANCHABLES = ("choix_unique", "booleen", "switch")
MAX_SECTIONS = 50
_ID_OK = re.compile(r"^[a-z0-9_]{1,24}$")

# ── Thème visuel du formulaire (structure_formulaire["theme"]) ────────────────────────────────────────────────────────
# Indépendant de la nature (standard / conditionnel) : il ne change que le RENDU du formulaire de saisie côté client.
# Les polices sont des clés d'une liste blanche (piles de polices système : aucune dépendance externe, voir
# static/engine/js/formulaire_theme.js) ; les images sont des data-URI redimensionnées par le navigateur, plafonnées ici.
POLICES = ("systeme", "moderne", "humaniste", "serif", "elegante", "arrondie", "manuscrite", "affiche", "mono")
ROLES_TEXTE = ("titre", "description", "section", "question", "saisie")
PRESETS = ("flat", "verre", "papier", "nuit", "minimal")
ALIGNEMENTS = ("left", "center", "right")
MAX_IMAGE = 900_000   # caractères (data-URI base64 ≈ 650 Ko d'image)
MAX_IMAGE_SECTION = 400_000          # image de fond d'UNE section (data-URI)
MAX_IMAGES_SECTIONS = 2_400_000      # total des images de section d'un formulaire (au-delà, les dernières sont ignorées)
COULEURS_SECTION = ("fond", "bordure", "titre", "description", "question", "saisie")
TAILLES_CHAMP = ("taille_question", "taille_saisie")
_SLUG_OK = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_IMG = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$")


def _borne(v, mini, maxi, defaut):
    try:
        return max(mini, min(maxi, int(float(v))))
    except (TypeError, ValueError):
        return defaut


def _normaliser_style_section(st):
    """Apparence d'UNE section : fond, contour, couleurs de texte (#rrggbb), arrondi, image de fond (+ voile)."""
    if not isinstance(st, dict):
        return {}
    n = {}
    for cle in COULEURS_SECTION:
        v = st.get(cle)
        if isinstance(v, str) and _HEX.match(v):
            n[cle] = v.lower()
    if "rayon" in st:
        n["rayon"] = _borne(st["rayon"], 0, 40, 16)
    img = st.get("image")
    if isinstance(img, str) and len(img) <= MAX_IMAGE_SECTION and _IMG.match(img):
        n["image"] = img
        n["voile"] = _borne(st.get("voile"), 0, 90, 35)
    return n


def _normaliser_theme_sections(brut):
    """theme["sections"] : {id de section: apparence}. Plafonne le poids total des images."""
    res, total = {}, 0
    if not isinstance(brut, dict):
        return res
    for sid, st in list(brut.items())[:MAX_SECTIONS]:
        if not (isinstance(sid, str) and _ID_OK.match(sid)):
            continue
        n = _normaliser_style_section(st)
        img = n.get("image")
        if img:
            if total + len(img) > MAX_IMAGES_SECTIONS:
                n.pop("image", None)
                n.pop("voile", None)
            else:
                total += len(img)
        if n:
            res[sid] = n
    return res


def _normaliser_theme_champs(brut):
    """theme["champs"] : {slug: {taille_question, taille_saisie}} — taille des polices propre à chaque champ."""
    res = {}
    if not isinstance(brut, dict):
        return res
    for slug, st in list(brut.items())[:500]:
        if not (isinstance(slug, str) and _SLUG_OK.match(slug)) or not isinstance(st, dict):
            continue
        n = {cle: _borne(st[cle], 10, 48, 16) for cle in TAILLES_CHAMP if cle in st}
        if n:
            res[slug] = n
    return res


def normaliser_theme(brut):
    """Nettoie un thème reçu du navigateur : couleurs #rrggbb, polices de la liste blanche, images data-URI plafonnées."""
    if not isinstance(brut, dict):
        return {}
    th = {}
    for cle in ("fond", "carte", "texte", "accent", "garde_voile"):
        v = brut.get(cle)
        if isinstance(v, str) and _HEX.match(v):
            th[cle] = v.lower()
    for cle in ("image_garde", "image_fond"):
        v = brut.get(cle)
        if isinstance(v, str) and len(v) <= MAX_IMAGE and _IMG.match(v):
            th[cle] = v
    if brut.get("preset") in PRESETS:
        th["preset"] = brut["preset"]
    th["garde_hauteur"] = _borne(brut.get("garde_hauteur"), 120, 520, 260)
    th["voile"] = _borne(brut.get("voile"), 0, 90, 35)
    th["flou"] = _borne(brut.get("flou"), 0, 24, 0)
    th["rayon"] = _borne(brut.get("rayon"), 0, 40, 22)
    th["illustration"] = bool(brut.get("illustration", True))
    th["colonnes"] = 2 if str(brut.get("colonnes")) == "2" else 1
    th["garde_position"] = brut.get("garde_position") if brut.get("garde_position") in ("top", "center", "bottom") else "center"
    styles = {}
    for role in ROLES_TEXTE:
        st = (brut.get("styles") or {}).get(role) if isinstance(brut.get("styles"), dict) else None
        if not isinstance(st, dict):
            continue
        n = {}
        if st.get("police") in POLICES:
            n["police"] = st["police"]
        for b in ("gras", "italique", "souligne"):
            if b in st:
                n[b] = bool(st[b])
        if isinstance(st.get("couleur"), str) and _HEX.match(st["couleur"]):
            n["couleur"] = st["couleur"].lower()
        if "taille" in st:
            n["taille"] = _borne(st["taille"], 10, 72, 16)
        if st.get("align") in ALIGNEMENTS:
            n["align"] = st["align"]
        if n:
            styles[role] = n
    th["styles"] = styles
    sections = _normaliser_theme_sections(brut.get("sections"))
    if sections:
        th["sections"] = sections
    champs = _normaliser_theme_champs(brut.get("champs"))
    if champs:
        th["champs"] = champs
    return th


def champs_saisis(table):
    """Champs de la table que le client remplit (ordre du formulaire client : par id)."""
    return [c for c in table.champs.all().order_by("id") if c.type_champ not in TYPES_NON_SAISIS]


def valeurs_branchables(champ):
    """Valeurs sur lesquelles on peut brancher : [(valeur, libellé)]. Oui/non → 'true'/'false'."""
    if champ.type_champ == "choix_unique":
        return [(o.strip(), o.strip()) for o in (champ.options_choix or "").split(",") if o.strip()]
    if champ.type_champ in ("booleen", "switch"):
        return [("true", t("engine/formulaire_conditionnel.py:oui")), ("false", t("engine/formulaire_conditionnel.py:non"))]
    return []


def structure_par_defaut(champs):
    return {"actif": False, "sections": [
        {"id": "s1", "titre": "", "description": "", "champs": [c.slug for c in champs], "apres": SUIVANTE, "branche": None}]}


def normaliser(brut, champs):
    """Nettoie une structure reçue du navigateur : ne garde que des champs existants (une seule fois), répare les renvois
    invalides (retour en arrière, section inconnue, valeur inexistante) et rattache les champs oubliés à la dernière section."""
    if not isinstance(brut, dict):
        return structure_par_defaut(champs)
    theme = normaliser_theme(brut.get("theme"))
    res = _normaliser_sections(brut, champs)
    # L'apparence ne vise que des sections et des champs qui existent encore (une section / un champ supprimé ne laisse rien derrière lui).
    ids = {x["id"] for x in res["sections"]}
    slugs = {c.slug for c in champs}
    for cle, permis in (("sections", ids), ("champs", slugs)):
        if cle in theme:
            theme[cle] = {k: v for k, v in theme[cle].items() if k in permis}
            if not theme[cle]:
                del theme[cle]
    if theme:
        res["theme"] = theme
    return res


def _normaliser_sections(brut, champs):
    par_slug = {c.slug: c for c in champs}
    sections, ids, utilises = [], set(), set()
    for i, s in enumerate((brut.get("sections") or [])[:MAX_SECTIONS]):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "")
        if not _ID_OK.match(sid) or sid in ids or sid in (SOUMETTRE, SUIVANTE):
            n = i + 1
            while f"s{n}" in ids:
                n += 1
            sid = f"s{n}"
        ids.add(sid)
        slugs = []
        for slug in (s.get("champs") or []):
            if slug in par_slug and slug not in utilises:
                utilises.add(slug)
                slugs.append(slug)
        sections.append({
            "id": sid, "titre": str(s.get("titre") or "")[:200], "description": str(s.get("description") or "")[:1000],
            "champs": slugs, "apres": s.get("apres"), "branche": s.get("branche"),
        })
    if not sections:
        return structure_par_defaut(champs)
    oublies = [c.slug for c in champs if c.slug not in utilises]
    sections[-1]["champs"].extend(oublies)
    rang = {s["id"]: i for i, s in enumerate(sections)}

    def cible_valide(cible, i):
        if cible in (SOUMETTRE, SUIVANTE):
            return cible
        return cible if cible in rang and rang[cible] > i else SUIVANTE

    for i, s in enumerate(sections):
        s["apres"] = cible_valide(s["apres"], i)
        b = s["branche"]
        nouvelle = None
        if isinstance(b, dict) and b.get("champ") in s["champs"]:
            permises = {v for v, _ in valeurs_branchables(par_slug[b["champ"]])}
            regles = {str(v): cible_valide(c, i) for v, c in (b.get("regles") or {}).items() if str(v) in permises}
            if regles:
                nouvelle = {"champ": b["champ"], "regles": regles}
        s["branche"] = nouvelle
    return {"actif": bool(brut.get("actif")), "sections": sections}


def _canon(valeur):
    """Valeur reçue (JSON, formulaire, liste) → texte comparable aux clés des règles."""
    if isinstance(valeur, (list, tuple)):
        valeur = valeur[0] if valeur else ""
    if valeur is True:
        return "true"
    if valeur is False:
        return "false"
    texte = "" if valeur is None else str(valeur).strip()
    bas = texte.lower()
    if bas in ("1", "true", "oui", "yes", "on"):
        return "true" if bas != "1" else "1"
    if bas in ("0", "false", "non", "no", "off"):
        return "false" if bas != "0" else "0"
    return texte


def _cle_regle(regles, valeur):
    v = _canon(valeur)
    if v in regles:
        return v
    if v == "1" and "true" in regles:
        return "true"
    if v == "0" and "false" in regles:
        return "false"
    return None


def sections_parcourues(structure, valeurs):
    """Ids des sections traversées, dans l'ordre, d'après les réponses (`valeurs` : slug → valeur)."""
    sections = structure["sections"]
    rang = {s["id"]: i for i, s in enumerate(sections)}
    vues, i = [], 0
    while 0 <= i < len(sections) and len(vues) <= len(sections):
        s = sections[i]
        vues.append(s["id"])
        cible = s.get("apres") or SUIVANTE
        b = s.get("branche")
        if b:
            cle = _cle_regle(b["regles"], valeurs.get(b["champ"]))
            if cle is not None:
                cible = b["regles"][cle]
        if cible == SOUMETTRE:
            break
        i = i + 1 if cible == SUIVANTE else rang.get(cible, i + 1)
    return vues


def champs_hors_parcours(table, valeurs):
    """Slugs des champs à laisser vides (sections non traversées) ; ensemble vide si le formulaire n'est pas conditionnel."""
    st = getattr(table, "structure_formulaire", None) or {}
    if not st.get("actif") or not st.get("sections"):
        return set()
    traversees = set(sections_parcourues(st, valeurs))
    return {slug for s in st["sections"] if s["id"] not in traversees for slug in s["champs"]}


def structure_pour_client(table):
    """Structure à donner au formulaire client : None si la table n'a pas de formulaire conditionnel."""
    st = getattr(table, "structure_formulaire", None) or {}
    if not (st.get("actif") and st.get("sections")):
        return None
    return {k: v for k, v in st.items() if k != "theme"}


def theme_pour_client(table):
    """Thème visuel à appliquer au formulaire client ({} = rendu par défaut du thème de l'application)."""
    st = getattr(table, "structure_formulaire", None) or {}
    return normaliser_theme(st.get("theme")) if st.get("theme") else {}
