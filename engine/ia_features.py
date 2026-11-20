"""Feature engineering de la page d'entraînement : retirer des LIGNES (valeurs vides, valeurs aberrantes, choix manuel) de la
table de features avant l'entraînement et le fine-tuning.

Principe : on travaille sur une COPIE VIRTUELLE de la table. La vraie table n'est JAMAIS modifiée : les lignes écartées sont
seulement mémorisées dans ModeleIA.lignes_exclues ({id_table: [id_ligne, ...]}) et ignorées au moment de construire le dataset
(ia_integration._TableFiltree). La table de features suit les colonnes choisies pour le modèle (entrées + sortie).
"""
import math

FILTRES = ("toutes", "signalees", "vides", "aberrantes", "exclues")
FILTRES_EN_BLOC = ("vides", "aberrantes", "signalees")   # sélections « toutes les lignes … » proposées à l'utilisateur
TAILLE_PAGE = 100
COEF_IQR = 1.5            # règle de Tukey : hors de [Q1 - 1,5·IQR ; Q3 + 1,5·IQR]
MIN_VALEURS_ABERRANT = 8  # en dessous, aucune détection : trop peu de valeurs pour parler d'aberration
LARGEUR_TEXTE = 80


def est_vide(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and v != v)


def nombre(v):
    """float si v est une vraie valeur numérique (jamais un booléen), sinon None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _quantile(tri, q):
    """Quantile par interpolation linéaire sur une liste TRIÉE non vide."""
    pos = (len(tri) - 1) * q
    bas = int(math.floor(pos))
    haut = min(bas + 1, len(tri) - 1)
    return tri[bas] + (tri[haut] - tri[bas]) * (pos - bas)


def bornes_iqr(valeurs, coef=COEF_IQR):
    """(borne_basse, borne_haute) ou None si la détection n'a pas de sens (trop peu de valeurs, ou écart interquartile nul :
    une colonne quasi constante ne signale pas d'aberrations)."""
    tri = sorted(valeurs)
    if len(tri) < MIN_VALEURS_ABERRANT:
        return None
    q1, q3 = _quantile(tri, 0.25), _quantile(tri, 0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return None
    return q1 - coef * iqr, q3 + coef * iqr


def _affichable(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    s = str(v)
    return s if len(s) <= LARGEUR_TEXTE else s[:LARGEUR_TEXTE - 1] + "…"


def ids_exclus(modele, table_id):
    """Ensemble des ids de lignes écartées pour une table (liste JSON -> set d'entiers, valeurs invalides ignorées)."""
    sortie = set()
    for x in ((modele.lignes_exclues or {}).get(str(table_id)) or []):
        try:
            sortie.add(int(x))
        except (TypeError, ValueError):
            continue
    return sortie


def colonnes_features(modele, table):
    """Slugs de la table de features, dans l'ordre : colonnes d'entrée choisies (toutes, hors sortie, si aucune n'est choisie),
    puis colonne(s) de sortie. Retourne (slugs, {slug: ChampDynamique})."""
    champs = {c.slug: c for c in table.champs.all().order_by("ordre")}
    cibles = list(modele.colonnes_sortie or ([modele.colonne_cible] if modele.colonne_cible else []))
    entrees = list(modele.colonnes_entree or [s for s in champs if s not in cibles])
    slugs = []
    for s in entrees + cibles:
        if s in champs and s not in slugs:
            slugs.append(s)
    return slugs, champs


def _lire(table, slugs):
    """[(id_ligne, {slug: valeur typée})] — lignes du groupe de tables, valeurs retrouvées par SLUG de colonne (fichier Parquet)."""
    from . import parquet_store
    return [(ligne["_id"], {s: ligne.get(s) for s in slugs}) for ligne in parquet_store.lignes_analytiques(table)]


def analyser(modele, table):
    """Analyse complète de la copie de travail : colonnes, lignes signalées (vides / aberrantes) et lignes écartées."""
    slugs, champs = colonnes_features(modele, table)
    brutes = _lire(table, slugs)
    ecartees = ids_exclus(modele, table.id)
    bornes, numeriques = {}, set()
    for s in slugs:
        nums = [n for _, v in brutes for n in [nombre(v[s])] if n is not None]
        if nums:
            numeriques.add(s)
        b = bornes_iqr(nums)
        if b:
            bornes[s] = b
    lignes = []
    for id_ligne, v in brutes:
        vides = [s for s in slugs if est_vide(v[s])]
        aberrantes = []
        for s, (bas, haut) in bornes.items():
            n = nombre(v[s])
            if n is not None and (n < bas or n > haut):
                aberrantes.append(s)
        lignes.append({"id": id_ligne, "v": v, "vides": vides, "aberrantes": aberrantes, "exclue": id_ligne in ecartees})
    return {"slugs": slugs, "champs": champs, "numeriques": numeriques, "bornes": bornes, "lignes": lignes}


def _retient(ligne, filtre):
    if filtre == "vides":
        return bool(ligne["vides"])
    if filtre == "aberrantes":
        return bool(ligne["aberrantes"])
    if filtre == "signalees":
        return bool(ligne["vides"] or ligne["aberrantes"])
    if filtre == "exclues":
        return ligne["exclue"]
    return True


def apercu(modele, table, filtre="toutes", page=0, taille=TAILLE_PAGE):
    """Page de la table de features, avec les compteurs globaux (indépendants du filtre et de la page)."""
    if filtre not in FILTRES:
        filtre = "toutes"
    a = analyser(modele, table)
    lignes = a["lignes"]
    retenues = [l for l in lignes if _retient(l, filtre)]
    taille = max(1, min(int(taille or TAILLE_PAGE), 500))
    pages = max(1, math.ceil(len(retenues) / taille))
    page = max(0, min(int(page or 0), pages - 1))
    return {
        "table": {"id": table.id, "nom": table.nom},
        "colonnes": [{"slug": s, "nom": a["champs"][s].nom, "type": a["champs"][s].type_champ,
                      "numerique": s in a["numeriques"],
                      "bornes": list(a["bornes"][s]) if s in a["bornes"] else None} for s in a["slugs"]],
        "filtre": filtre, "page": page, "pages": pages, "taille": taille, "affichees": len(retenues),
        "total": len(lignes),
        "exclues": sum(1 for l in lignes if l["exclue"]),
        "restantes": sum(1 for l in lignes if not l["exclue"]),
        "nb_vides": sum(1 for l in lignes if l["vides"]),
        "nb_aberrantes": sum(1 for l in lignes if l["aberrantes"]),
        "lignes": [{"id": l["id"], "exclue": l["exclue"], "vides": l["vides"], "aberrantes": l["aberrantes"],
                    "valeurs": [_affichable(l["v"][s]) for s in a["slugs"]]}
                   for l in retenues[page * taille:(page + 1) * taille]],
    }


def mettre_a_jour(modele, table, ajouter=(), retirer=(), exclure_filtre=None, reinitialiser=False):
    """Modifie la liste des lignes écartées de CETTE table (jamais la table elle-même) et l'enregistre sur le modèle.
    `exclure_filtre` : écarte d'un coup toutes les lignes « vides », « aberrantes » ou « signalees ». Les ids qui
    n'appartiennent pas à la table sont ignorés. Retourne le nombre de lignes écartées."""
    valides = set(table.enregistrements_groupe.values_list("id", flat=True))
    courant = ids_exclus(modele, table.id) & valides
    if reinitialiser:
        courant = set()

    def _ids(brut):
        sortie = set()
        for x in brut or ():
            try:
                sortie.add(int(x))
            except (TypeError, ValueError):
                continue
        return sortie & valides

    courant |= _ids(ajouter)
    courant -= _ids(retirer)
    if exclure_filtre in FILTRES_EN_BLOC:
        courant |= {l["id"] for l in analyser(modele, table)["lignes"] if _retient(l, exclure_filtre)}
    donnees = dict(modele.lignes_exclues or {})
    if courant:
        donnees[str(table.id)] = sorted(courant)
    else:
        donnees.pop(str(table.id), None)
    modele.lignes_exclues = donnees
    modele.save(update_fields=["lignes_exclues", "modifie_le"])
    return len(courant)
