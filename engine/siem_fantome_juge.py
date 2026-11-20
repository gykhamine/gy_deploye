"""JUGEMENT du modèle fantôme du SIEM : qui a le droit d'enseigner, à quel poids, et à partir de quel score on alerte.

Module PUR (numpy + bibliothèque standard, aucun import Django) : testable avec des dictionnaires et des listes.

Trois protections, une par faiblesse d'un modèle qui apprend de ses utilisateurs :

  1. VOTANTS     Les 👍/👎 sont le juge, donc la cible : quelqu'un qui multiplie les 👎 voudrait faire taire ou accuser un autre.
                 `RegistreVotants` plafonne le poids d'un votant (une voix qui décroît pour un même expéditeur, un quota par jour), et le fait
                 baisser quand ses avis contredisent systématiquement ceux des autres lecteurs du même message. `part_negative` exige des
                 votants DISTINCTS : un seul compte, même à 100 avis, ne fait jamais passer un expéditeur pour « mal noté ».
  2. SEUILS      `seuil_calibre` : le réglage de l'administrateur est le point de départ ; dès que des avis existent, le seuil qui maximise
                 la précision (F-bêta) est cherché dessus, et il ne descend jamais sous 0,5 ; un quantile des scores récents le relève si le
                 réseau voit « suspect » trop souvent.
  3. BUDGET      `budget_ok` : un plafond d'alertes par heure et par tête. Au-delà, l'alerte est retenue (visible sur la page SIEM), jamais
                 noyée dans le bruit ni envoyée en rafale.
"""
from __future__ import annotations

import math

import numpy as np

PLAFOND_SEUIL, PLANCHER_SEUIL = 0.97, 0.5
MAX_VOTANTS, MAX_EXPEDITEURS_PAR_VOTANT = 2000, 50


# ── 1. Votants ──────────────────────────────────────────────────────────────────────────────────────────────────────────
class RegistreVotants:
    """État JSON des votants (`etat`, modifié en place, rangé dans le fichier signé du modèle). Un votant = un identifiant stable (membre).

    poids(...)   poids d'APPRENTISSAGE d'un avis nouveau : confiance × quota du jour ÷ √(rang de cet avis pour cet expéditeur)
    juger(...)   met à jour la réputation : l'avis allait-il dans le sens des autres lecteurs du même message ?
    confiance()  0 à 1 : 1 jusqu'à ce que la réputation tombe sous 0,7 ; 0 à 0,3 (le votant n'enseigne plus rien).
    """

    def __init__(self, etat: dict | None = None, quota_jour: int = 20, min_votants: int = 3):
        self.etat = etat if etat is not None else {}
        self.quota_jour = max(1, int(quota_jour))
        self.min_votants = max(1, int(min_votants))

    def _fiche(self, votant) -> dict:
        return self.etat.setdefault(str(votant), {"n": 0, "ok": 0, "jour": "", "c": 0, "exp": {}, "vu": ""})

    def reputation(self, votant) -> float:
        """Part des avis jugés qui allaient dans le sens des autres, lissée (3 accords d'avance : un nouveau votant n'est pas suspect)."""
        f = self.etat.get(str(votant))
        n, ok = (f["n"], f["ok"]) if f else (0, 0)
        return (ok + 3) / (n + 4)

    def confiance(self, votant) -> float:
        return min(1.0, max(0.0, (self.reputation(votant) - 0.3) / 0.4))

    def poids(self, votant, expediteur, jour: str) -> float:
        f = self._fiche(votant)
        if f["jour"] != jour:
            f["jour"], f["c"] = jour, 0
        f["vu"] = jour
        f["c"] += 1
        exp = f["exp"]
        rang = exp.pop(str(expediteur), 0) + 1   # pop + réinsertion : le plus récemment vu passe en dernier
        exp[str(expediteur)] = rang
        while len(exp) > MAX_EXPEDITEURS_PAR_VOTANT:
            exp.pop(next(iter(exp)))
        return self.confiance(votant) * min(1.0, self.quota_jour / f["c"]) / math.sqrt(rang)

    def juger(self, votant, accord: bool | None) -> None:
        """`accord` None = pas d'autre lecteur ou égalité : rien à juger (un votant seul n'est ni confirmé ni contredit)."""
        if accord is None:
            return
        f = self._fiche(votant)
        f["n"] += 1
        f["ok"] += int(bool(accord))
        if f["n"] > 100:   # mémoire glissante : un votant peut se racheter, ou se dégrader
            f["n"], f["ok"] = f["n"] // 2, f["ok"] // 2

    def elaguer(self) -> None:
        """Borne l'état : on garde les votants vus le plus récemment."""
        if len(self.etat) > MAX_VOTANTS:
            gardes = sorted(self.etat.items(), key=lambda kv: kv[1].get("vu", ""), reverse=True)[:MAX_VOTANTS]
            self.etat.clear()
            self.etat.update(gardes)


def accord_consensus(valeur: float, autres: list) -> bool | None:
    """L'avis `valeur` (±1) va-t-il dans le sens de la moyenne des AUTRES lecteurs du même message ? None sans autre lecteur ou à égalité."""
    autres = [float(v) for v in autres if v]
    if not autres:
        return None
    moyenne = sum(autres) / len(autres)
    if moyenne == 0:
        return None
    return (moyenne > 0) == (valeur > 0)


def part_negative(votes: list, registre: RegistreVotants, min_votants: int | None = None) -> tuple:
    """(part d'avis négatifs, poids des avis positifs) sur un expéditeur. `votes` = [(votant, valeur ±1), ...] sur TOUS ses messages.
    Chaque votant compte UNE fois (la moyenne de ses avis), pesé par sa confiance. Sous `min_votants` votants de confiance cumulée, la part vaut 0 :
    pas assez de monde pour accuser quelqu'un (`min_votants` : celui du registre si non précisé)."""
    min_votants = registre.min_votants if min_votants is None else min_votants
    par_votant: dict = {}
    for votant, valeur in votes:
        par_votant.setdefault(str(votant), []).append(float(valeur))
    poids = {v: registre.confiance(v) for v in par_votant}
    total = sum(poids.values())
    positifs = sum(poids[v] for v, vals in par_votant.items() if sum(vals) > 0)
    if total <= 0 or total < min_votants:   # total nul (min_votants = 0, aucun votant de confiance) : pas de division par zéro
        return 0.0, round(positifs, 3)
    neg = sum(poids[v] * (1.0 if sum(vals) < 0 else 0.5 if sum(vals) == 0 else 0.0) for v, vals in par_votant.items())
    return neg / total, round(positifs, 3)


# ── 2. Seuils ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def meilleur_fbeta(etiquettes: list, beta: float = 0.5):
    """Seuil (≥ 0,5) qui maximise F-bêta sur des couples (score, étiquette 0/1) ; bêta < 1 favorise la précision (une fausse alerte coûte plus
    cher qu'une alerte manquée, car elle dresse les gens contre le modèle). None si aucun seuil n'attrape un seul vrai positif."""
    paires = [(float(s), 1 if y >= 0.5 else 0) for s, y in etiquettes]
    candidats = sorted({s for s, _y in paires if s >= PLANCHER_SEUIL})
    meilleur, score_max, b2 = None, 0.0, beta * beta
    for t in candidats:
        tp = sum(1 for s, y in paires if s >= t and y)
        fp = sum(1 for s, y in paires if s >= t and not y)
        fn = sum(1 for s, y in paires if s < t and y)
        if not tp:
            continue
        p, r = tp / (tp + fp), tp / (tp + fn)
        f = (1 + b2) * p * r / (b2 * p + r)
        if f >= score_max:   # à égalité, le seuil le plus haut
            meilleur, score_max = t, f
    return meilleur


def seuil_calibre(plancher: float, scores: list, etiquettes: list, quantile: float = 0.995, beta: float = 0.5, min_etiquettes: int = 20,
                  min_classe: int = 5, min_scores: int = 100) -> tuple:
    """(seuil, source). `plancher` = réglage de l'administrateur, valable tant qu'on ne sait rien. Source : « reglage », « avis »,
    « budget » (quantile des scores récents) ou « avis+budget »."""
    seuil, source = float(plancher), "reglage"
    positifs = sum(1 for _s, y in etiquettes if y >= 0.5)
    if len(etiquettes) >= min_etiquettes and positifs >= min_classe and len(etiquettes) - positifs >= min_classe:
        choisi = meilleur_fbeta(etiquettes, beta)
        if choisi is not None:
            seuil, source = float(np.clip(choisi, PLANCHER_SEUIL, PLAFOND_SEUIL)), "avis"
    if len(scores) >= min_scores:
        q = float(np.quantile(np.asarray(scores, dtype=float), quantile))
        if q > seuil:
            seuil, source = min(q, PLAFOND_SEUIL), ("avis+budget" if source == "avis" else "budget")
    return round(min(seuil, PLAFOND_SEUIL), 3), source


# ── 3. Budget d'alertes ─────────────────────────────────────────────────────────────────────────────────────────────────
def budget_ok(historique: list, maintenant: float, maximum: int, fenetre: float = 3600.0) -> tuple:
    """(autorisé, nouvel historique). `maximum` 0 = illimité. L'historique ne garde que la fenêtre : il ne grossit pas."""
    recents = [t for t in historique if maintenant - t < fenetre]
    if maximum > 0 and len(recents) >= maximum:
        return False, recents
    return True, recents + [maintenant]


def noter_score(meta: dict, tete: str, score: float, plafond: int = 2000) -> None:
    """Garde les derniers scores d'une tête (de quoi calculer le quantile)."""
    liste = meta.setdefault("scores", {}).setdefault(tete, [])
    liste.append(round(float(score), 3))
    del liste[:-plafond]


def noter_etiquette(meta: dict, tete: str, score: float, y: float, plafond: int = 300) -> None:
    """Garde les derniers couples (score avant apprentissage, jugement humain) : de quoi calibrer sans se tromper sur ce qu'on vient d'apprendre."""
    liste = meta.setdefault("etiquettes", {}).setdefault(tete, [])
    liste.append([round(float(score), 3), 1 if y >= 0.5 else 0])
    del liste[:-plafond]


# ── Ce que les règles ont vu, rejoué dans le temps ──────────────────────────────────────────────────────────────────────────
def gravite_proche(historique: list, uid, ts: float, marge: float = 900.0) -> float:
    """Gravité (0..1) de la plus forte alerte des règles sur `uid` dans [ts - marge, ts + marge]. `historique` = [(ts, uid, gravité), ...].
    Sert au préchauffage : le professeur, rejoué sur le passé. 0 si aucune règle n'a sonné sur ce compte à ce moment-là."""
    return max((g for t, u, g in historique if u == uid and abs(t - ts) <= marge), default=0.0)


# ── Quelle étiquette apprendre pour un message ──────────────────────────────────────────────────────────────────────────
def etiquette_apprentissage(faible: float, grav: float, echantillon_calme: bool):
    """(étiquette, poids) d'un message que l'on apprend sans avis humain, ou None.
      · une règle a sonné sur l'expéditeur  → le plus fort des deux professeurs, poids 0,3
      · la forme du message est très suspecte → étiquette = indice de forme, poids 0,15
      · sinon, un message calme sur quatre   → poids 0,05 (les calmes ne noient pas les rares signaux)"""
    if grav:
        return max(grav, faible), 0.3
    if faible >= 0.5:
        return faible, 0.15
    if echantillon_calme:
        return faible, 0.05
    return None
