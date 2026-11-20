"""ANNOTATIONS DE LA CARTE : l'utilisateur marque des points, trace des trajectoires et des zones pour approfondir son analyse.

Mémoire durable : chaque annotation est une LIGNE de la table ordinaire « Annotations cartographiques » (même mécanisme que les analyses et les
plans, engine/memoire_decision.py : créée à la première utilisation, blockchain, ontologie, KPI, dashboards). Aucune migration. Géométrie = liste de
[latitude, longitude] : point (1), trajectoire (2 à 2000), zone (3 à 2000, polygone fermé implicitement). Longueur/périmètre et surface sont calculés ICI,
jamais fiés au navigateur. Droits : un agent ne modifie et ne supprime que ses annotations ; un administrateur toutes (voir views_geo.api_geo_annotations).
"""
from __future__ import annotations
from engine.i18n import t, tf, tl

import json
import math
import re

from . import geospatial as _g   # Django (base, mémoire de décision) importé dans les fonctions qui en ont besoin : validation et mesures se testent seules
from engine.i18n import t as _gt  # noqa: E402

TYPES = {"point": (1, 1), "trajectoire": (2, 2000), "zone": (3, 2000)}
MAX_PAR_TABLE = 1000
# Nature d'un POINT ou d'une ZONE : « stratégique » et « danger » préviennent quand un objet s'en approche (rayon d'alerte autour du point, ou autour du bord de la zone) ;
# « contexte » est une mémoire (description durable), sans alerte par défaut. Le calcul de l'approche se fait dans la page (positions en direct) : ici on valide et on conserve.
NATURES = {"strategique": tl("engine/geo_annotations.py:g:20:27"), "danger": "Danger", "contexte": "Contextuelle"}
RAYON_MAX_M = 100_000.0
RAYON_DEFAUT_M = {"point": 200.0, "zone": 100.0}
# Symbologie militaire (conventions APP-6 / MIL-STD-2525 : cadre selon l'affiliation, dimension, indicateur d'échelon) ; code SIDC 2525C généré pour l'échange.
AFFILIATIONS = {"en_attente": "P", "inconnu": "U", "presume_ami": "A", "ami": "F", "neutre": "N", "suspect": "S", "hostile": "H"}
DIMENSIONS = {"terre_unite": "GU", "terre_equipement": "GE", "terre_installation": "GI", "air": "A-", "mer": "S-", "sous_marin": "U-", "espace": "P-"}
ECHELONS = {"equipe": "A", "groupe": "B", "section": "C", "peloton": "D", "compagnie": "E", "bataillon": "F", "regiment": "G", "brigade": "H",
            "division": "I", "corps": "J", "armee": "K", "groupe_armees": "L", "region": "M"}
_COULEUR = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


class ErreurAnnotation(ValueError):
    """Entrée invalide : message prêt à afficher."""


class ErreurDroit(ErreurAnnotation):
    """Annotation d'un autre utilisateur (agent) : refus 403."""


def _geometrie(type_, brut) -> list:
    if type_ not in TYPES:
        raise ErreurAnnotation(t("engine/geo_annotations.py:g:41:32"))
    try:
        pts = [[round(float(p[0]), 6), round(float(p[1]), 6)] for p in brut]
    except (TypeError, ValueError, IndexError):
        raise ErreurAnnotation(t("engine/geo_annotations.py:g:45:32"))
    mini, maxi = TYPES[type_]
    if not mini <= len(pts) <= maxi:
        raise ErreurAnnotation(tf("engine/geo_annotations.py:g:48:32", v1=type_, v2=mini, v3=maxi))
    if not all(-90 <= la <= 90 and -180 <= lo <= 180 for la, lo in pts):
        raise ErreurAnnotation(t("engine/geo_annotations.py:g:50:32"))
    return pts


def mesures(type_, pts) -> tuple:
    """(longueur ou périmètre en m, surface en m² | None). Surface : plan local autour du centre de la zone (exact à l'échelle d'une ville)."""
    if type_ == "point":
        return None, None
    boucle = pts + pts[:1] if type_ == "zone" else pts
    longueur = round(float(sum(_g.haversine(a[0], a[1], b[0], b[1]) for a, b in zip(boucle, boucle[1:]))), 1)
    if type_ != "zone":
        return longueur, None
    k, c = math.pi / 180 * _g.R_TERRE, math.cos(math.radians(sum(p[0] for p in pts) / len(pts)))
    xy = [(p[1] * k * c, p[0] * k) for p in pts]
    return longueur, round(abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2, 1)


def _symbole(d, type_) -> dict:
    """Symbologie d'un POINT : {affiliation, dimension, echelon} validés ; vide si aucune affiliation (annotation ordinaire). Autres types : toujours vide."""
    aff, dim, ech = (_texte(d, k) for k in ("affiliation", "dimension", "echelon"))
    if type_ != "point" or not aff:
        return {"affiliation": "", "dimension": "", "echelon": ""}
    if aff not in AFFILIATIONS or (dim and dim not in DIMENSIONS) or (ech and ech not in ECHELONS):
        raise ErreurAnnotation(t("engine/geo_annotations.py:g:73:32"))
    return {"affiliation": aff, "dimension": dim or "terre_unite", "echelon": ech}


def _nature(d, type_) -> dict:
    """{nature, rayon_m, alerte} validés. Trajectoires : toujours vides. Sans nature : annotation ordinaire. Alerte par défaut : oui pour stratégique et danger, non pour contexte."""
    vide = {"nature": "", "rayon_m": 0.0, "alerte": "non"}
    nature = _texte(d, "nature").lower()
    if type_ not in ("point", "zone") or not nature:
        return vide
    if nature not in NATURES:
        raise ErreurAnnotation(t("engine/geo_annotations.py:g:84:32"))
    brut = d.get("rayon_m")
    try:
        rayon = float(brut) if brut not in (None, "") else RAYON_DEFAUT_M[type_]
    except (TypeError, ValueError):
        raise ErreurAnnotation(t("engine/geo_annotations.py:g:89:32"))
    if not math.isfinite(rayon) or not 0 <= rayon <= RAYON_MAX_M:
        raise ErreurAnnotation(tf("engine/geo_annotations.py:g:91:32", v1=RAYON_MAX_M))
    brut_alerte = d.get("alerte")
    alerte = nature != "contexte" if brut_alerte in (None, "") else str(brut_alerte).strip().lower() in ("1", "true", "oui", "on", "yes")
    return {"nature": nature, "rayon_m": round(rayon, 1), "alerte": "oui" if alerte else "non"}


def sidc(affiliation, dimension, echelon) -> str:
    """Code SIDC 15 caractères (MIL-STD-2525C, fonction générique) : S, affiliation, dimension, statut présent, fonction « ------ », échelon en position 12."""
    if affiliation not in AFFILIATIONS:
        return ""
    dim = DIMENSIONS.get(dimension or "terre_unite", "GU")
    return "S" + AFFILIATIONS[affiliation] + dim[0] + "P" + (dim[1] if dim[1] != "-" else "-") + "-----" + "-" + ECHELONS.get(echelon, "-") + "---"


def _vers_dict(ligne) -> dict:
    from . import memoire_decision as _md
    return {"id": ligne["_id"], "type": ligne.get("type"), "titre": ligne.get("titre") or "", "note": ligne.get("note") or "",
            "couleur": ligne.get("couleur") or "#f5a524", "geometrie": _md._json(ligne.get("geometrie"), []),
            "longueur_m": _md._nombre(ligne.get("longueur_m")), "surface_m2": _md._nombre(ligne.get("surface_m2")), "date": ligne["_cree_le"].isoformat(),
            "affiliation": ligne.get("affiliation") or "", "dimension": ligne.get("dimension") or "", "echelon": ligne.get("echelon") or "",
            "sidc": sidc(ligne.get("affiliation"), ligne.get("dimension"), ligne.get("echelon")),
            "nature": ligne.get("nature") or "", "rayon_m": _md._nombre(ligne.get("rayon_m")) or 0.0, "alerte": str(ligne.get("alerte") or "").lower() == "oui",
            "origine": ligne.get("origine") or ""}


def lister(entreprise, table_id) -> list:
    from . import memoire_decision as _md
    return [_vers_dict(l) for l in _md._lignes(entreprise, "carte") if _md._nombre(l.get("table_id")) == table_id]


def _texte(d, cle, defaut="", maxi=200) -> str:
    return str(d.get(cle) if d.get(cle) is not None else defaut).strip()[:maxi]


def _couleur(d) -> str:
    c = _texte(d, "couleur", "#f5a524")
    return c if _COULEUR.match(c) else "#f5a524"


def _ecrire(entreprise, utilisateur, table_id, d) -> int:
    from django.utils import timezone
    from . import memoire_decision as _md
    type_ = _texte(d, "type")
    pts = _geometrie(type_, d.get("geometrie"))
    longueur, surface = mesures(type_, pts)
    return _md._ajouter_ligne(entreprise, utilisateur, "carte", {
        "titre": _texte(d, "titre", type_.capitalize()) or type_.capitalize(), "type": type_, "table_id": table_id, "geometrie": pts, "couleur": _couleur(d),
        "note": _texte(d, "note", maxi=2000), "longueur_m": longueur, "surface_m2": surface, "date": timezone.now().isoformat(),
        "origine": "proposition" if _texte(d, "origine") == "proposition" else "", **_symbole(d, type_), **_nature(d, type_)}).id


def creer(entreprise, utilisateur, table_id, d) -> int:
    if len(lister(entreprise, table_id)) >= MAX_PAR_TABLE:
        raise ErreurAnnotation(tf("engine/geo_annotations.py:g:144:32", v1=MAX_PAR_TABLE))
    return _ecrire(entreprise, utilisateur, table_id, d)


def creer_lot(entreprise, utilisateur, table_id, elements) -> dict:
    """Import : crée les éléments valides jusqu'à la limite de la carte. Retour : {creees, ignorees, refus: [messages]} (un élément invalide n'arrête pas les autres)."""
    place = MAX_PAR_TABLE - len(lister(entreprise, table_id))
    creees, refus = 0, []
    for e in elements[:max(place, 0)]:
        try:
            _ecrire(entreprise, utilisateur, table_id, e)
            creees += 1
        except ErreurAnnotation as err:
            refus.append(f"« {e.get('titre') or e.get('type')} » : {err}")
    return {"creees": creees, "ignorees": len(elements) - creees, "refus": refus[:10]}


def _trouver(entreprise, table_id, ligne_id, utilisateur, admin):
    """(enregistrement, champs) de l'annotation de CETTE carte ; refus si elle appartient à un autre agent."""
    from . import memoire_decision as _md
    from .models import Enregistrement
    table, champs = _md._tables(entreprise, ("carte",))["carte"]
    try:
        enr = Enregistrement.objects.filter(table=table, id=int(ligne_id)).first()
    except (TypeError, ValueError):
        enr = None
    ligne = enr.to_dict() if enr else {}
    if enr is None or _md._nombre(ligne.get("table_id")) != table_id:
        raise ErreurAnnotation(_gt("rg5.msg.040"))
    if not admin and enr.cree_par_id != utilisateur.id:
        raise ErreurDroit(t("engine/geo_annotations.py:g:174:27"))
    return enr, champs, ligne


def modifier(entreprise, utilisateur, table_id, ligne_id, d, admin) -> None:
    from django.db import transaction
    from .models import Bloc, ValeurChamp
    enr, champs, avant = _trouver(entreprise, table_id, ligne_id, utilisateur, admin)
    nouveau = {c: (_couleur(d) if c == "couleur" else _texte(d, c, maxi=2000 if c == "note" else 200)) for c in ("titre", "note", "couleur") if c in d}
    if any(k in d for k in ("affiliation", "dimension", "echelon")):
        nouveau.update(_symbole(d, avant.get("type")))
    if any(k in d for k in ("nature", "rayon_m", "alerte")):
        nouveau.update(_nature(d, avant.get("type")))
    if "geometrie" in d:
        pts = _geometrie(avant.get("type"), d.get("geometrie"))
        nouveau["geometrie"] = pts
        nouveau["longueur_m"], nouveau["surface_m2"] = mesures(avant.get("type"), pts)
    with transaction.atomic():
        modifs = []
        for slug, v in nouveau.items():
            if v is None:
                continue
            texte = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else str(v)
            ValeurChamp.objects.update_or_create(enregistrement=enr, champ=champs[slug], defaults={"valeur": texte})
            modifs.append({"champ": champs[slug].nom, "avant": avant.get(slug), "apres": v})
        Bloc.ajouter_transaction(entreprise.id, {"type": "modification_ligne", "table_id": enr.table_id, "table": enr.table.nom, "enregistrement_id": enr.id,
                                                 "agent_id": utilisateur.id, "modifications": modifs})


def supprimer(entreprise, utilisateur, table_id, ligne_id, admin) -> int:
    from django.db import transaction
    from .models import Bloc
    enr, _champs, _ligne = _trouver(entreprise, table_id, ligne_id, utilisateur, admin)
    donnees, enr_id, table = enr.to_dict_lisible(), enr.id, enr.table
    with transaction.atomic():
        enr.delete()
        Bloc.ajouter_transaction(entreprise.id, {"type": "suppression_ligne", "table_id": table.id, "table": table.nom, "enregistrement_id": enr_id,
                                                 "agent_id": utilisateur.id, "donnees": donnees})
    return enr_id
