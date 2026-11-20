"""COMPRENDRE LE MÉTIER DU CLIENT : réseau de neurones multi-étiquettes, entraîné paresseusement sur TOUT ce que le client a construit.

Le fantôme est un constructeur d'interface : il n'exécute ni n'écrit aucun code, il ne lit jamais une valeur de cellule ni un secret.
Il lit des NOMS (tables, colonnes, KPI, formules des tables de calcul, graphiques et tableaux de bord, pages et groupes de l'ontologie,
tâches, modèles IA, skills, analyses demandées, documents, connexions) et en déduit une AFFINITÉ pour chaque métier de
fantome_construction.DOMAINES (santé, finances, ventes, éducation…). Les affinités sont INDÉPENDANTES (multi-étiquettes) : une école qui
prend des fonctionnalités de banque obtient éducation ET finances élevées ; l'interface suit ce que le client semble vouloir.

DATASET construit par le script (pas de jeu préparé) : une entité = un objet nommé de la plateforme (+ les noms de ses colonnes) ; un
échantillon = sac de mots hachés (HACHAGE dimensions) -> étiquettes faibles par mots-clés des métiers (1 - exp(-nb de mots reconnus)).
Une entité sans mot reconnu n'a pas d'étiquette (masquée) : le réseau apprend les mots inconnus par COOCCURRENCE avec les connus
(une table « Écolage » à côté de « Élèves » finit par évoquer l'éducation). Clé stable par entité + contenu : jamais appris deux fois.
Entraînement paresseux : au plus MAX_NOUVEAUX nouvelles entités et PAS pas d'Adam par passage (moments conservés = fine-tuning).
Plancher déterministe : l'affinité finale = max(réseau, mots-clés de l'ensemble) — jamais moins que ce que les mots prouvent.
Le réseau vit dans le registre JSON de l'entreprise (registre["reseau_metier"], npz sans pickle en base64). Module PUR sauf vocabulaire().
"""
from __future__ import annotations
from engine.i18n import tl

import base64
import math
import zlib

import numpy as np

from . import fantome_analyse as fa
from . import fantome_construction as fc
from . import fantome_langue as fl
from . import reseau_neuronal as rn

CODES = tuple(fc.DOMAINES)
assert len(CODES) == fa.NB_DOM, tl("engine/fantome_metier.py:g:30:33")
HACHAGE, LARGEUR, MAX_NOUVEAUX, PAS, LR, MAX_ECH, MAX_VUS = 256, 16, 64, 25, 0.02, 600, 4000
VERSION = 2   # 2 : normalisation Unicode (toutes les écritures) + trigrammes de caractères + 256 dimensions ; un réseau d'une autre version est repris à zéro
CLES_NOMS = ("nom", "libelle", "titre")


def indices(jetons) -> set[int]:
    """Cases du sac hachées : le mot entier ET ses trigrammes de caractères. Les trigrammes font partager des cases à un mot jamais vu et à ses
    formes voisines (pluriels, dérivés, emprunts) : le réseau peut apprendre n'importe quelle langue sans dictionnaire complet."""
    return {zlib.crc32(f.encode()) % HACHAGE for j in jetons for f in [j] + fl.ngrammes(j)}


def hacher(jetons) -> np.ndarray:
    """Sac de mots haché binaire, normalisé par la racine du nombre de cases distinctes (même fonction à l'entraînement et à la prédiction)."""
    return hacher_depuis_indices(indices(jetons))


def etiquettes(jetons) -> list[float]:
    """Étiquette faible : 1 - exp(-mots reconnus) par métier."""
    return [1.0 - math.exp(-sum(1 for m in fc.DOMAINES[c]["mots"] if fc._contient(jetons, m))) for c in CODES]


def _jetons_de(textes) -> list[str]:
    return [j for t in textes for j in fc._jetons(t)]


def entites(obj, chemin="") -> list[tuple[str, list[str]]]:
    """Entités nommées trouvées dans n'importe quelle partie de l'état/vocabulaire : (clé stable, jetons). Un dict avec un nom devient une
    entité ; les noms de ses colonnes (« champs ») s'ajoutent à ses jetons ; on descend ensuite dans ses sous-éléments."""
    sortie = []
    if isinstance(obj, dict):
        noms = [obj[k] for k in CLES_NOMS if isinstance(obj.get(k), str) and obj[k]]
        if noms:
            sous = [c.get(k) for c in obj.get("champs", []) if isinstance(c, dict) for k in CLES_NOMS[:1] if c.get(k)]
            jetons = _jetons_de(noms + sous)
            if jetons:
                sortie.append((f"{chemin}:{obj.get('id', '')}:{zlib.crc32(' '.join(sorted(set(jetons))).encode())}", jetons))
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                sortie += entites(v, f"{chemin}/{k}")
    elif isinstance(obj, list):
        for v in obj[:500]:
            sortie += entites(v, chemin)
    return sortie


def _textes(etat, voc) -> list[str]:
    """Tous les noms lus (mêmes entités que le réseau) : base du profil de langue."""
    return [t for obj in (etat or {}, voc or {}) for t in _noms(obj)]


def _noms(obj):
    if isinstance(obj, dict):
        for k in CLES_NOMS:
            if isinstance(obj.get(k), str) and obj[k]:
                yield obj[k]
        for v in obj.values():
            if isinstance(v, (dict, list)):
                yield from _noms(v)
    elif isinstance(obj, list):
        for v in obj[:500]:
            yield from _noms(v)


def vocabulaire(entreprise) -> dict:
    """Noms seulement (jamais une valeur de ligne, jamais un secret) des parties que l'état fusionné ne détaille pas : ontologie, formules des
    tables de calcul, graphiques et tableaux de bord, skills, analyses demandées, documents, connexions."""
    from .models import (ConnexionExterne, DemandeAgent, DocumentGenere, GroupeTables, LigneCalcul, PageOntologie, Skill, TableauBordBI,
                         WidgetGraphiqueBI)
    e = entreprise

    def noms(qs, champ="nom", n=300):
        return [{"nom": (v or "")[:120]} for v in qs.values_list(champ, flat=True)[:n] if v]
    return {
        "ontologie": noms(PageOntologie.objects.filter(entreprise=e)) + noms(GroupeTables.objects.filter(entreprise=e)),
        "formules_tables": noms(LigneCalcul.objects.filter(table_calcul__entreprise=e)),
        "tableaux_bord": noms(TableauBordBI.objects.filter(entreprise=e)),
        "graphiques": noms(WidgetGraphiqueBI.objects.filter(tableau_bord__entreprise=e), "titre"),
        "skills": noms(Skill.objects.filter(entreprise=e)),
        "analyses": noms(DemandeAgent.objects.filter(entreprise=e).order_by("-cree_le"), "texte", 100),
        "documents": noms(DocumentGenere.objects.filter(entreprise=e), "titre"),
        "connexions": noms(ConnexionExterne.objects.filter(entreprise=e)),
    }


# ── Réseau ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _charger(registre: dict) -> rn.Reseau:
    try:
        r = rn.Reseau.depuis_tableaux(rn.depuis_octets(base64.b64decode(registre.get("reseau_metier"))))
        if r.d_in == HACHAGE and r.d_out == len(CODES):
            return r
    except Exception:
        pass
    return rn.Reseau.nouveau(HACHAGE, LARGEUR, len(CODES), graine=11)


def _migrer(registre: dict) -> None:
    """Réseau d'une autre version (autre hachage, autres dimensions) : repris à zéro ; il se ré-entraîne seul, paresseusement."""
    if registre.get("metier_v") != VERSION:
        for cle in ("reseau_metier", "metier_vus", "metier_ech"):
            registre.pop(cle, None)
        registre["metier_v"] = VERSION


def construire_dataset(etat: dict, voc: dict | None, registre: dict) -> list[dict]:
    """Nouveaux échantillons (clé jamais vue) tirés de TOUTE la plateforme : l'état fusionné (tables, KPI, tâches, modèles IA, blockchain…)
    et le vocabulaire (ontologie, formules, graphiques…). Un échantillon d'ensemble résume le client entier."""
    vus = set(registre.get("metier_vus", []))
    toutes = entites(etat or {}, "etat") + entites(voc or {}, "voc")
    tous_jetons = [j for _, js in toutes for j in js]
    toutes.append((f"ensemble:{zlib.crc32(' '.join(sorted(set(tous_jetons))).encode())}", tous_jetons))
    neufs = [(c, js) for c, js in toutes if c not in vus][:MAX_NOUVEAUX]
    registre["metier_vus"] = (list(vus) + [c for c, _ in neufs])[-MAX_VUS:]
    ech = []
    for _, js in neufs:
        y = etiquettes(js)
        if max(y) > 0:   # entité sans mot reconnu : pas d'étiquette, rien à apprendre d'elle seule
            ech.append({"h": sorted(indices(js)), "y": [round(v, 3) for v in y]})
    return ech


def affiner(registre: dict, etat: dict, voc: dict | None) -> dict:
    """Passage paresseux : construit le dataset, l'ajoute au tampon, fait PAS pas d'Adam. Rend un petit bilan."""
    _migrer(registre)
    nouveaux = construire_dataset(etat, voc, registre)
    registre["langue"] = fl.profil(_textes(etat, voc), fc.DOMAINES_BASE)   # écritures employées + langue de graines dominante : signal, jamais filtre
    tampon = registre.setdefault("metier_ech", [])
    tampon.extend(nouveaux)
    del tampon[:-MAX_ECH]
    if len(tampon) < 4:
        return {"nouveaux": len(nouveaux), "perte": None}
    r = _charger(registre)
    x = np.array([hacher_depuis_indices(e["h"]) for e in tampon])
    y = np.array([e["y"] for e in tampon])
    w = np.ones_like(y)
    perte = None
    for _ in range(PAS):
        perte = r.pas(x, y, w, LR)
    registre["reseau_metier"] = base64.b64encode(rn.vers_octets(r.tableaux())).decode()
    return {"nouveaux": len(nouveaux), "perte": perte}


def hacher_depuis_indices(indices) -> np.ndarray:
    x = np.zeros(HACHAGE)
    if indices:
        x[list(indices)] = 1.0 / math.sqrt(len(indices))
    return x


def affinites(registre: dict, etat: dict, voc: dict | None = None) -> dict:
    """Affinité (0..1) de l'entreprise pour chaque métier : max(réseau, mots-clés de l'ensemble). Plusieurs peuvent être élevés (métiers mixtes)."""
    jetons = [j for _, js in entites(etat or {}, "etat") + entites(voc or {}, "voc") for j in js]
    plancher = etiquettes(jetons)
    r = _charger(registre)
    pred = np.clip(r.predire(hacher(jetons))[0], 0.0, 1.0) if registre.get("reseau_metier") and registre.get("metier_v") == VERSION else np.zeros(len(CODES))
    return {c: float(max(p, f)) for c, p, f in zip(CODES, pred, plancher)}
