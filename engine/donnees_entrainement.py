"""DONNÉES D'ENTRAÎNEMENT : construites AUTOMATIQUEMENT depuis toute la structure de la plateforme.

Personne ne prépare, ne rassemble ni ne nettoie de jeu de données. À chaque passage, ce module relit l'état fusionné
(engine/fusion.py) et la toile de dépendances (engine/portail.py) et en tire des échantillons étiquetés pour le
réseau du modèle fantôme (engine/modele_fantome.py). Un échantillon = un vecteur `x` de D nombres bornés + une étiquette.

Trois familles d'échantillons, un seul format de vecteur (les indicateurs `flag_*` disent de quoi il s'agit) :
  * TABLE  — chaque table, à plusieurs dates passées de son propre historique : structure (lignes, colonnes, types,
             remplissage, cardinalité), activité récente, fraîcheur, degré dans l'ontologie et dans la toile, KPI et
             tableaux de bord rattachés. Étiquette : l'ÉLAN observé la semaine suivante (variation relative). Ce que le
             modèle apprend ici, il le connaît sans qu'aucune décision n'ait encore été prise.
  * KPI    — chaque indicateur, à plusieurs points de son historique : tendance, volatilité, écart à la cible, sens,
             alerte. Étiquette : le progrès vers l'objectif au point suivant.
  * OPTION — une option de décision (le φ du moteur stratégique) placée dans son contexte de structure. Étiquette :
             la RÉCOMPENSE mesurée des conséquences (engine/memoire_decision.py). C'est ce qui pilote la recommandation.
Chaque vecteur contient aussi le CONTEXTE global de la plateforme : nombre de tables, liens, densité de l'ontologie,
tendance de la blockchain, modèles IA et leurs inférences, tâches en erreur, notifications en attente.

Chaque échantillon a une clé stable : le même fait n'est jamais appris deux fois. Ce n'est donc pas un « gros jeu de
données » qu'on attend : chaque nouveauté observable (un jour de plus, une valeur de KPI de plus, une mesure de
décision de plus) devient un petit lot qui sert au fine-tuning du modèle.
Module PUR (aucun import Django).
"""
from __future__ import annotations

import math
import statistics
import zlib
from collections import Counter
from datetime import date, datetime, timezone

from . import criteres as _cr

FLAG_OPTION, FLAG_TABLE, FLAG_KPI = 0, 1, 2
I_PHI, NB_PHI = 3, 8
I_TBL, NB_TBL = 11, 18
I_KPI, NB_KPI = 29, 5
I_CTX, NB_CTX = 34, 8
D_AVANT_CRITERES = I_CTX + NB_CTX            # 42 : taille des vecteurs et des modèles d'avant le bloc des critères
I_CRIT, NB_CRIT = D_AVANT_CRITERES, _cr.NB   # 33 intensités continues (engine/criteres.py), en FIN de vecteur : les colonnes existantes ne bougent pas
D = I_CRIT + NB_CRIT


def noms_entrees() -> list[str]:
    """NOM de chacune des D colonnes du vecteur d'entrée. Un fichier .agentgy mémorise cette liste : au chargement, chaque poids rejoint sa
    colonne PAR SON NOM (reseau_neuronal.Reseau.remapper_entrees), si bien qu'une mise à jour qui ajoute, déplace ou retire une
    colonne ne casse jamais un modèle déjà appris. Règle d'évolution : on AJOUTE en fin de vecteur (nouveau bloc ou nouveau critère en fin
    de `criteres.CODES`) ; on ne renomme jamais une colonne existante (ce serait un autre signal : l'ancien poids ne la suivrait pas)."""
    return (["flag_option", "flag_table", "flag_kpi"] + [f"phi_{i}" for i in range(NB_PHI)] + [f"tbl_{i}" for i in range(NB_TBL)]
            + [f"kpi_{i}" for i in range(NB_KPI)] + [f"ctx_{i}" for i in range(NB_CTX)] + [f"crit:{c}" for c in _cr.CODES])


IDX_TENDANCE = 10   # position, dans le bloc TABLE, de la tendance log(n7 / n7_prec) (sert à l'a priori du noyau)
# Critères que l'on sait RECONSTRUIRE à une date passée sans rien emprunter au futur : ils ne dépendent que des lignes créées par jour
# (`jours`) ou de l'historique de valeurs du KPI. Tous les autres (remplissage des colonnes, config des KPI, blockchain, tâches...)
# n'ont pas d'état historique fiable : ils restent à zéro dans les échantillons du passé.
CRITERES_TABLE_PASSE = ("manque_table_vide", "surplus_volume", "tendance_hausse_activite", "diminution_activite", "concentration_volume",
                        "anomalie_rafale_creation", "obsolescence_table")
CRITERES_KPI_PASSE = ("tendance_kpi_defavorable", "tendance_kpi_favorable")
COLONNES_CRIT_TABLE = tuple(_cr.CODES.index(c) for c in CRITERES_TABLE_PASSE)   # positions (dans le bloc des 33) renseignées pour une table
DECALAGES_JOURS = tuple(range(7, 29, 3))   # dates de coupe passées (jours avant aujourd'hui) pour les échantillons TABLE
_NUM = {"nombre", "decimal", "monnaie", "pourcentage", "note", "duree"}
_DATE = {"date", "heure", "datetime", "horodatage"}


def _tanh_log(a: float, b: float) -> float:
    return math.tanh(math.log((a + 1) / (b + 1)))


def _entier(cle):
    try:
        return int(cle)
    except (TypeError, ValueError):
        return cle


def _activite(jours: dict, coupe: date) -> tuple[int, int, int, int, int, float]:
    """(n7, n7_prec, n30, prochain7, lignes créées dès la coupe, jours depuis la dernière ligne) vus depuis la date de coupe."""
    n7 = n7p = n30 = suivant = apres = 0
    dernier = None
    for cle, n in jours.items():
        age = (coupe - date.fromisoformat(cle)).days   # ≥ 1 : créée avant la coupe
        if 1 <= age <= 7:
            n7 += n
        elif 8 <= age <= 14:
            n7p += n
        if 1 <= age <= 30:
            n30 += n
        if age <= 0:
            apres += n
            if age >= -6:
                suivant += n
        elif n and (dernier is None or age < dernier):
            dernier = age
    return n7, n7p, n30, suivant, apres, float(dernier if dernier is not None else 60)


def _feat_table(t: dict, n7: int, n7p: int, n30: int, nb: int, jours_depuis: float, deg_rel: int, deg_graphe: int) -> list[float]:
    champs = t.get("champs") or []
    nc = len(champs)
    part = lambda types: sum(1 for c in champs if c.get("type") in types) / nc if nc else 0.0
    remp = sum(c.get("remplissage", 0.0) for c in champs) / nc if nc else 0.0
    dist = sum(min(1.0, c.get("distincts", 0) / max(nb, 1)) for c in champs) / nc if nc else 0.0
    return [min(1.0, math.log1p(max(nb, 0)) / 8), math.tanh(nc / 20), remp, part(_NUM), part({"relation"}), part(_DATE), dist,
            min(1.0, math.log1p(n7) / 6), min(1.0, math.log1p(n7p) / 6), min(1.0, math.log1p(n30) / 8), _tanh_log(n7, n7p),
            math.exp(-max(jours_depuis, 0.0) / 30), math.tanh(deg_rel / 10), math.tanh(t.get("nb_kpis", 0) / 3),
            math.tanh(t.get("nb_tableaux_bord", 0) / 3), 1.0 if t.get("clients") else 0.0, 1.0 if t.get("nosql") else 0.0,
            math.tanh(deg_graphe / 30)]


def _nombres(valeurs) -> list[float]:
    sortie = []
    for v in valeurs or []:
        try:
            if v is not None and math.isfinite(float(v)):
                sortie.append(float(v))
        except (TypeError, ValueError):
            continue
    return sortie


def _feat_kpi(h: list[float], cible, sens, alerte) -> list[float]:
    dernier, prec, moy = h[-1], h[-2], statistics.fmean(h)
    ec = math.tanh((dernier - cible) / max(abs(cible), 1e-9)) if isinstance(cible, (int, float)) else 0.0
    return [math.tanh((dernier - prec) / max(abs(prec), 1e-9)), math.tanh(statistics.pstdev(h) / max(abs(moy), 1e-9)), ec,
            1.0 if sens == "hausse" else -1.0 if sens == "baisse" else 0.0, 1.0 if alerte else 0.0]


def _progres(avant: float, apres: float, cible, sens) -> float:
    """Progrès vers l'objectif entre deux valeurs successives, dans ]-1, 1[."""
    if sens == "cible" and isinstance(cible, (int, float)):
        e0 = abs(avant - cible)
        return math.tanh((e0 - abs(apres - cible)) / max(e0, 1e-9))
    return math.tanh((-1.0 if sens == "baisse" else 1.0) * (apres - avant) / max(abs(avant), 1e-9))


def etat_a_la_coupe(etat: dict, coupe: date) -> dict:
    """État minimal des TABLES tel qu'on pouvait le connaître à la date de coupe (lignes créées AVANT ce jour seulement) : sert à
    mesurer, à cette date, les `CRITERES_TABLE_PASSE`. Une table qui n'existait pas encore en est absente. Rien de postérieur à la
    coupe n'y entre : les lignes créées dès la coupe sont retirées du volume, de l'activité et de la date de dernière ligne."""
    ts = datetime(coupe.year, coupe.month, coupe.day, tzinfo=timezone.utc).timestamp()
    tables = []
    for t in etat.get("tables", []):
        jours = t.get("jours") or {}
        n7, n7p, n30, _suivant, apres, depuis = _activite(jours, coupe)
        nb = t.get("nb_lignes", 0) - apres
        if nb <= 0:
            continue
        avant = {k: n for k, n in jours.items() if (coupe - date.fromisoformat(k)).days >= 1}
        tables.append({"id": t["id"], "nb_lignes": nb, "n7": n7, "n7_prec": n7p, "n30": n30, "jours": avant,
                       "derniere_ts": ts - depuis * 86400 if any(avant.values()) else None})
    return {"ts": ts, "tables": tables}


def completer(x) -> list[float]:
    """Vecteur enregistré par une version plus ancienne (au moins le socle de 42 nombres, jamais plus que D) -> vecteur courant :
    les colonnes apparues depuis valent zéro (aucune mesure connue à l'époque). Les traces de décisions déjà stockées restent donc
    apprenables après une mise à jour qui ajoute des colonnes en fin de vecteur. Toute autre longueur est rendue telle quelle
    (l'appelant la refuse : un vecteur étranger n'est jamais deviné)."""
    x = [float(v) for v in x]
    return x + [0.0] * (D - len(x)) if D_AVANT_CRITERES <= len(x) < D else x


def assembler(flag: int, phi=None, tbl=None, kpi=None, ctx=None, crit=None) -> list[float]:
    """`crit` : 33 intensités de critères. Options : mesurées AU MOMENT de la décision. Échantillons TABLE/KPI du passé : seuls les
    critères reconstructibles sans fuite du futur (`CRITERES_TABLE_PASSE`, `CRITERES_KPI_PASSE`) sont renseignés, les autres restent à zéro."""
    x = [0.0] * D
    x[flag] = 1.0
    for debut, bloc, n in ((I_PHI, phi, NB_PHI), (I_TBL, tbl, NB_TBL), (I_KPI, kpi, NB_KPI), (I_CTX, ctx, NB_CTX), (I_CRIT, crit, NB_CRIT)):
        if bloc is not None and len(bloc) == n:
            x[debut:debut + n] = [float(v) for v in bloc]
    return x


def x_depuis_phi(phi: list[float]) -> list[float]:
    """Vecteur d'une option dont on ne connaît que le φ (traces d'avant le réseau) : structure et contexte à zéro."""
    return assembler(FLAG_OPTION, phi=phi if len(phi) == NB_PHI else None)


class Vue:
    """Lecture de la structure de l'entreprise (état fusionné + toile), calculée UNE fois puis réutilisée."""

    def __init__(self, etat: dict, graphe: dict | None, reglages: dict | None = None):
        graphe = graphe or {}
        self.etat, self.ts = etat, etat.get("ts") or 0.0
        self.reglages = _cr.normaliser_echelles(reglages)[0]   # échelles réglées par l'entreprise (vide = défaut / calibrage)
        self.criteres = _cr.Index(etat, graphe.get("noeuds"), reglages=self.reglages)
        self._passes: dict = {}
        self.tables = {t["id"]: t for t in etat.get("tables", [])}
        rel = (etat.get("relations") or {}).get("degres_tables") or {}
        self.deg_rel = {_entier(k): v for k, v in rel.items()}
        noeuds, degres = graphe.get("noeuds", {}), Counter()
        for a in graphe.get("aretes", []):
            for nid in (a.get("source"), a.get("cible")):
                n = noeuds.get(nid, {})
                tid = n.get("table_id") if n.get("table_id") is not None else (n.get("id") if n.get("type") == "table" else None)
                if tid is not None:
                    degres[tid] += 1
        self.deg_graphe = degres
        bc, notif = etat.get("blockchain") or {}, etat.get("notifications") or {}
        modeles, taches = etat.get("modeles_ia", []), etat.get("taches", [])
        self.ctx = [min(1.0, math.log1p(len(self.tables)) / 4), min(1.0, math.log1p((etat.get("relations") or {}).get("total_liens", 0)) / 8),
                    min(1.0, (len(graphe.get("aretes", [])) / len(noeuds) if noeuds else 0.0) / 3),
                    _tanh_log(bc.get("blocs_7j", 0), bc.get("blocs_7j_prec", 0)),
                    math.tanh(len(modeles) / 5), min(1.0, math.log1p(sum(m.get("inferences", 0) for m in modeles)) / 8),
                    sum(1 for t in taches if t.get("erreur")) / len(taches) if taches else 0.0, math.tanh(notif.get("non_lues", 0) / 20)]

    def tbl(self, t: dict, coupe: date | None = None) -> list[float]:
        """Bloc TABLE de `t` aujourd'hui, ou tel qu'il était à la date de coupe (activité reconstruite depuis `jours`)."""
        deg_rel, deg_g = self.deg_rel.get(t["id"], 0), self.deg_graphe.get(t["id"], 0)
        if coupe is None:
            derniere = t.get("derniere_ts")
            jours = (self.ts - derniere) / 86400 if (self.ts and derniere) else 60.0
            return _feat_table(t, t.get("n7", 0), t.get("n7_prec", 0), t.get("n30", 0), t.get("nb_lignes", 0), jours, deg_rel, deg_g)
        n7, n7p, n30, _s, apres, jours = _activite(t.get("jours") or {}, coupe)
        return _feat_table(t, n7, n7p, n30, t.get("nb_lignes", 0) - apres, jours, deg_rel, deg_g)

    def crit_table(self, t: dict, coupe: date) -> list[float]:
        """33 intensités de la table `t` à la date de coupe : critères reconstructibles seulement, mesurés sur l'état de cette date
        (mis en cache par date : le volume relatif des tables, lui, dépend de toutes les tables)."""
        if coupe not in self._passes:
            self._passes[coupe] = _cr.Index(etat_a_la_coupe(self.etat, coupe), codes=CRITERES_TABLE_PASSE, echelles={**_cr.ECHELLES_DEFAUT, **self.reglages})
        return self._passes[coupe].vecteur(tables=[t["id"]])

    @staticmethod
    def crit_kpi(k: dict, historique_connu: list[float], reglages: dict | None = None) -> list[float]:
        """33 intensités du KPI `k` connaissant seulement `historique_connu` (les valeurs antérieures au point à prédire)."""
        etat = {"kpis": [{"id": k["id"], "table_id": k.get("table_id"), "historique": historique_connu, "sens": k.get("sens")}]}
        return _cr.Index(etat, codes=CRITERES_KPI_PASSE, echelles={**_cr.ECHELLES_DEFAUT, **_cr.normaliser_echelles(reglages)[0]}).vecteur(cibles=[f"kpi:{k['id']}"])

    def x_table(self, t: dict) -> list[float]:
        """Vecteur d'une table AUJOURD'HUI (pour la tête d'élan) : même bloc de critères que les échantillons d'entraînement du passé,
        c'est-à-dire les seuls critères reconstructibles (les autres à zéro), sinon l'entraînement et l'usage ne verraient pas la même chose."""
        actuel = self.criteres.vecteur(tables=[t["id"]])
        return assembler(FLAG_TABLE, tbl=self.tbl(t), ctx=self.ctx, crit=[v if j in COLONNES_CRIT_TABLE else 0.0 for j, v in enumerate(actuel)])

    def x_option(self, phi: list[float], tables_ids, cibles=None) -> list[float]:
        """φ + structure des tables visées + contexte + les 33 critères mesurés sur les objets visés (`cibles`, colonne ou table ;
        à défaut, les tables entières)."""
        blocs = [self.tbl(self.tables[i]) for i in dict.fromkeys(tables_ids) if i in self.tables]
        moyenne = [sum(c) / len(blocs) for c in zip(*blocs)] if blocs else None
        crit = self.criteres.vecteur(cibles=cibles) if cibles else self.criteres.vecteur(tables=tables_ids)
        return assembler(FLAG_OPTION, phi=phi, tbl=moyenne, ctx=self.ctx, crit=crit)


# ── Échantillons ────────────────────────────────────────────────────────────
def echantillon_recompense(cle: str, x: list[float], recompense: float) -> dict:
    return {"cle": cle, "x": x, "type": "recompense", "valeur": max(-1.0, min(1.0, recompense))}


def echantillons_structure(vue: Vue, deja: set, maximum: int) -> list[dict]:
    """Nouveaux échantillons TABLE et KPI de la structure actuelle (clés inconnues de `deja`), les plus récents d'abord."""
    aujourdhui = datetime.fromtimestamp(vue.ts, timezone.utc).date() if vue.ts else date.today()
    candidats = []
    for t in vue.tables.values():
        jours = t.get("jours") or {}
        for decalage in DECALAGES_JOURS:
            coupe = date.fromordinal(aujourdhui.toordinal() - decalage)
            cle = f"t{t['id']}:{coupe.isoformat()}"
            if cle in deja:
                continue
            n7, _p, _n30, suivant, apres, _j = _activite(jours, coupe)
            if t.get("nb_lignes", 0) - apres <= 0:   # la table n'existait pas encore à cette date
                continue
            candidats.append((decalage, {"cle": cle, "x": assembler(FLAG_TABLE, tbl=vue.tbl(t, coupe), ctx=vue.ctx, crit=vue.crit_table(t, coupe)),
                                         "type": "elan", "valeur": _tanh_log(suivant, n7)}))
    for k in vue.etat.get("kpis", []):
        h = _nombres(k.get("historique"))
        tbl = vue.tbl(vue.tables[k["table_id"]]) if k.get("table_id") in vue.tables else None
        for i in range(max(2, len(h) - 6), len(h)):
            cle = f"k{k['id']}:{zlib.crc32(repr(tuple(round(v, 6) for v in h[max(0, i - 3):i + 1])).encode())}"
            if cle not in deja:
                x = assembler(FLAG_KPI, tbl=tbl, kpi=_feat_kpi(h[:i], k.get("cible"), k.get("sens"), k.get("etat_alerte")), ctx=vue.ctx,
                              crit=vue.crit_kpi(k, h[:i], vue.reglages))
                candidats.append((0, {"cle": cle, "x": x, "type": "elan", "valeur": _progres(h[i - 1], h[i], k.get("cible"), k.get("sens"))}))
    candidats.sort(key=lambda c: c[0])
    return [e for _d, e in candidats[:maximum]]


def echantillons_communication(vue: Vue, deja: set, maximum: int) -> list[dict]:
    """Échantillons KPI tirés de la COMMUNICATION (engine/fusion.py, section « communication ») : pour l'ensemble de la messagerie et
    pour chaque membre de l'équipe, l'historique hebdomadaire du nombre de messages écrits est traité comme un indicateur
    (tendance, volatilité) et l'étiquette est le progrès de la semaine suivante. Aucun texte de message n'y entre : le modèle
    apprend le rythme des échanges (qui s'éteint, qui s'emballe), pas leur contenu. Même format de vecteur, même clé stable."""
    res = []
    for serie in (vue.etat.get("communication") or {}).get("series", []):
        h = _nombres(serie.get("historique"))
        for i in range(max(2, len(h) - 5), len(h)):
            cle = f"c{serie['id']}:{zlib.crc32(repr(tuple(round(v, 6) for v in h[max(0, i - 3):i + 1])).encode())}"
            if cle in deja:
                continue
            x = assembler(FLAG_KPI, kpi=_feat_kpi(h[:i], None, "hausse", None), ctx=vue.ctx)
            res.append({"cle": cle, "x": x, "type": "elan", "valeur": _progres(h[i - 1], h[i], None, "hausse")})
    return res[:maximum]


def empreinte(etat: dict) -> int:
    """Signature de la structure (lignes et colonnes par table, liens, blocs, KPI, modèles IA) : change dès qu'une donnée bouge."""
    corps = repr((sorted((t["id"], t.get("nb_lignes"), len(t.get("champs") or [])) for t in etat.get("tables", [])),
                  (etat.get("relations") or {}).get("total_liens"), (etat.get("blockchain") or {}).get("total"),
                  [(k["id"], k.get("valeur")) for k in etat.get("kpis", [])], len(etat.get("modeles_ia", [])),
                  (etat.get("communication") or {}).get("messages_7j")))
    return zlib.crc32(corps.encode())
