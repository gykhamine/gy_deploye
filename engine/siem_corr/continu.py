"""Moteur continu — règles de la famille « wallet », sans fenêtre de calcul.

Les règles périodiques relisent la dernière heure et comptent dans une fenêtre fixe : un geste qui sort de la fenêtre disparaît d'un coup, et un attaquant
qui espace ses gestes passe entre les mailles. Ici chaque événement met à jour un état par compte (et par numéro de destination), puis les règles s'évaluent
à l'arrivée de l'événement. Aucun geste ne disparaît brusquement : son poids vaut 1 pendant le palier historique de la règle (1 h, 24 h, 30 min), puis
diminue de moitié toutes les `continu_oubli_heures` heures (3 par défaut). Les règles de comptage additionnent des poids au lieu de compter des gestes ;
les règles d'enchaînement (réglage puis retrait, recharge puis retrait) restent armées jusqu'à consommation, et la gravité baisse avec l'âge.

`continu_oubli_heures` = 0 supprime l'oubli progressif : le comportement redevient celui des fenêtres de la v17 (les fonctions `detecter_*` de wallet.py
servent d'oracle aux tests d'équivalence).

L'état est un dict JSON-sérialisable (temps en secondes epoch) ; il se reconstruit en rejouant les événements (adaptateur.py).
Calcul pur : aucun accès base, testable sans Django.
"""
import json
from collections import defaultdict
from .base import FENETRE_S, S, _mediane
from .wallet import _dispersion
from engine.i18n import tf as _siem_tf  # noqa: E402

PALIER_S = FENETRE_S      # 1 h : palier des règles de comptage
PALIER_ALLER_RETOUR_S = 1800
JOUR_S = 86400
POIDS_MIN = 0.05          # en dessous, un geste ne compte plus
POIDS_OUBLI = 0.01        # en dessous, l'état l'élimine
EPS = 1e-9

SEUILS = {
    "oubli": S("continu_oubli_heures", 3.0, float),
    "volume_x": S("volume_x", 5, float),
    "moyen": S("moyen_recent_minutes", 60),
    "dest": S("dest_partagee_comptes", 3),
    "eparpillement": S("eparpillement_dest", 4),
    "cadence": S("cadence_retraits", 5),
    "escalade": S("escalade_retraits", 4),
    "recharges": S("recharges_rafale", 5),
    "identiques": S("montants_identiques", 3),
}

# Règles portées par ce moteur (numéro → nom de la corrélation), à retirer du registre périodique.
NUMEROS = (2, 12, 17, 20, 24, 27, 28, 34, 42, 47, 50)
KINDS = ("retrait", "retrait_audit", "recharge", "config_wallet")


def poids(age_s, palier_s=PALIER_S, demi_vie_s=0.0) -> float:
    """1 pendant le palier, puis décroissance continue (jamais de coupure nette). Sans demi-vie : fenêtre classique."""
    if age_s <= palier_s:
        return 1.0
    if demi_vie_s <= 0:
        return 0.0
    return 0.5 ** ((age_s - palier_s) / demi_vie_s)


def _ep(t) -> float:
    return t.timestamp() if hasattr(t, "timestamp") else float(t)


def _etat_vide() -> dict:
    return {"v": 1, "comptes": {}, "dest": {}, "curseurs": {}}


def _compte_vide() -> dict:
    return {"ret": [], "hist": [], "dests": {}, "ips": {}, "rec": [], "libres": [], "paires": [], "cfg": [], "cfg_t0": None}


class MoteurWallet:
    """Applique les règles continues à chaque événement : observer(evt) renvoie les alertes (mêmes dicts que les règles périodiques)."""

    def __init__(self, seuils=None, plafond=0.0, etat=None):
        self.seuils = dict(seuils or {})
        self.plafond = float(plafond or 0.0)
        self.etat = etat if etat is not None else _etat_vide()

    # ── état ────────────────────────────────────────────────────────────────
    def vers_json(self) -> str:
        return json.dumps(self.etat, separators=(",", ":"))

    @classmethod
    def depuis_json(cls, texte, seuils=None, plafond=0.0):
        try:
            etat = json.loads(texte) if texte else None
        except ValueError:   # état corrompu : on repart à vide, l'adaptateur le reconstruit en rejouant les événements
            etat = None
        return cls(seuils, plafond, etat if isinstance(etat, dict) and etat.get("v") == 1 and isinstance(etat.get("comptes"), dict) else None)

    def _s(self, cle):
        return SEUILS[cle].valeur(self.seuils)

    def _demi_vie(self) -> float:
        return max(0.0, self._s("oubli")) * 3600.0

    def _w(self, age_s, palier_s=PALIER_S) -> float:
        return poids(max(age_s, 0.0), palier_s, self._demi_vie())

    def _compte(self, uid) -> dict:
        return self.etat["comptes"].setdefault(str(uid), _compte_vide())

    # ── point d'entrée ──────────────────────────────────────────────────────
    def observer(self, evt) -> list:
        """evt : {t, uid, ent, ip, kind ∈ KINDS, ...} ; retrait : + montant, dest, moyen_maj (datetime ou None) ; config_wallet : + chemin."""
        f = {"retrait": self._retrait, "retrait_audit": self._retrait_audit, "recharge": self._recharge, "config_wallet": self._config}.get(evt.get("kind"))
        if f is None:
            return []
        c = self._compte(evt["uid"])
        ep = _ep(evt["t"])
        alertes = f(c, evt["uid"], ep, evt)
        self._elaguer(c, ep)
        return alertes

    def _alerte(self, regle, gravite, evt, message, details, dedup=None):
        a = {"regle": regle, "gravite": gravite, "uid": evt["uid"], "ip": evt.get("ip") or "", "ent": evt.get("ent"), "t": evt["t"], "message": message, "details": details}
        if dedup:
            a["dedup"] = dedup
        return a

    # ── retraits (RetraitWallet) : règles 2, 12, 17, 20, 27, 34, 42, 50 ─────
    def _retrait(self, c, uid, ep, evt) -> list:
        montant, dest, ip = float(evt["montant"]), evt.get("dest") or "", evt.get("ip") or ""
        r = {"t": ep, "m": montant, "d": dest, "ip": ip, "e": evt.get("ent")}
        alertes = []
        # Historique = ce qui précède le palier : même sens que « les 30 jours précédant la fenêtre » de la règle périodique, sans borne arrière.
        montants = [m for t, m in c["hist"] if ep - t > PALIER_S]
        dests = {d for d, t in c["dests"].items() if ep - t > PALIER_S}
        ips = {i for i, t in c["ips"].items() if ep - t > PALIER_S}
        # 2 — signaux sur le retrait lui-même
        signaux = []
        if len(montants) >= 5 and _mediane(montants) > 0 and montant > self._s("volume_x") * _mediane(montants):
            signaux.append(_siem_tf("rg4.siem.msg.135", montant=montant, v2=_mediane(montants)))
        if len(dests) >= 3 and dest not in dests:
            signaux.append(_siem_tf("rg4.siem.msg.136"))
        if len(ips) >= 3 and ip and ip not in ips:
            signaux.append(_siem_tf("rg4.siem.msg.137", ip=ip))
        if signaux:
            alertes.append(self._alerte("correlation_fraude_wallet", "critique" if len(signaux) >= 2 else "haute", evt, _siem_tf("rg4.siem.msg.243") + " ; ".join(signaux),
                                        {"montant": montant, "dest": dest[-4:], "signaux": signaux}))
        # 12 — moyen de retrait modifié avant ce retrait : la gravité baisse avec le temps écoulé, sans coupure
        maj = evt.get("moyen_maj")
        if maj:
            age = ep - _ep(maj)
            w = self._w(age, self._s("moyen") * 60) if age >= 0 else 0.0
            if w >= POIDS_MIN:
                alertes.append(self._alerte("correlation_moyen_retrait_recent", "haute" if w >= 0.5 else "moyenne", evt,
                                            _siem_tf("rg4.siem.msg.143", montant=montant, v2=int(age // 60)), {"montant": montant, "dest": dest[-4:]}))
        c["ret"].append(r)
        c["ret"].sort(key=lambda x: x["t"])
        # 17 — numéro partagé par plusieurs comptes (mémoire de tous les comptes vus, sans borne de temps)
        if dest:
            comptes = self.etat["dest"].setdefault(dest, {})
            comptes[str(uid)] = ep
            while len(comptes) > 50:
                comptes.pop(min(comptes, key=comptes.get))
            seuil = self._s("dest")
            if len(comptes) >= seuil:
                alertes.append(self._alerte("correlation_destination_partagee", "critique" if len(comptes) >= 2 * seuil else "haute", evt,
                                            _siem_tf("rg4.siem.msg.144", v1=len(comptes), v2=dest[-4:]),
                                            {"comptes": sorted(int(u) for u in comptes)[:20], "dest": dest[-4:]}, 86400))
        recents = [(x, self._w(ep - x["t"])) for x in c["ret"]]
        recents = [(x, w) for x, w in recents if w >= POIDS_MIN]
        n_eff = sum(w for _x, w in recents)
        total_eff = sum(w * x["m"] for x, w in recents)
        premier = min((x["t"] for x, _w in recents), default=ep)
        # 2 — fractionnement : toute suite de retraits récents qui se termine à celui-ci (un retrait atypique plus ancien ne doit pas masquer la suite)
        for i in range(max(0, len(recents) - 60), len(recents)):
            suite = recents[i:]
            n_suite = sum(w for _x, w in suite)
            if n_suite < 3 - EPS:
                break
            total_suite = sum(w * x["m"] for x, w in suite)
            if self.plafond > 0:
                cas = sum(w for x, w in suite if 0.8 * self.plafond <= x["m"] < self.plafond) >= 3 - EPS
            else:
                ms = [x["m"] for x, _w in suite]
                cas = n_suite >= 4 - EPS and min(ms) > 0 and max(ms) / min(ms) <= 1.2 and (not montants or total_suite > 3 * _mediane(montants))
            if cas:
                alertes.append(self._alerte("correlation_retraits_fractionnes", "haute", evt,
                                            _siem_tf("rg4.siem.msg.148", v1=round(n_suite), v2=int((ep - suite[0][0]['t']) // 60), total_suite=total_suite) + (_siem_tf("rg4.siem.msg.149") if self.plafond > 0 else ""),
                                            {"nb": round(n_suite), "total": total_suite}))
                break
        # 20 — cumul sur 24 h, comparé aux journées habituelles du compte
        r24 = [(x, self._w(ep - x["t"], JOUR_S)) for x in c["ret"]]
        r24 = [(x, w) for x, w in r24 if w >= POIDS_MIN]
        n24, total24 = sum(w for _x, w in r24), sum(w * x["m"] for x, w in r24)
        par_jour = defaultdict(float)
        for t, m in c["hist"]:
            if ep - t > JOUR_S:
                par_jour[int(t // JOUR_S)] += m
        mediane = _mediane(list(par_jour.values()))
        if len(par_jour) >= 5 and mediane > 0 and n24 >= 2 - EPS and total24 > self._s("volume_x") * mediane:
            alertes.append(self._alerte("correlation_cumul_retraits", "haute", evt, _siem_tf("rg4.siem.msg.138", v1=round(n24), total24=total24, mediane=mediane),
                                        {"nb": round(n24), "total": total24, "mediane_jour": mediane}, 21600))
        # 27 — éparpillement : numéros distincts, chacun pesé par son geste le plus récent
        par_dest = {}
        for x, w in recents:
            if x["d"]:
                par_dest[x["d"]] = max(par_dest.get(x["d"], 0.0), w)
        seuil = self._s("eparpillement")
        distincts = sum(par_dest.values())
        if distincts >= seuil - EPS:
            alertes.append(self._alerte("correlation_eparpillement_retraits", "critique" if distincts >= 2 * seuil - EPS else "haute", evt,
                                        _siem_tf("rg4.siem.msg.139", v1=round(distincts), v2=int((ep - premier) // 60), v3=sum((x['m'] for x, _w in recents))),
                                        {"destinations": sorted(d[-4:] for d in par_dest)[:12]}, 7200))
        # 34 — cadence mécanique : plus longue suite régulière qui se termine à ce retrait
        seuil = self._s("cadence")
        for i in range(len(c["ret"])):
            suite = c["ret"][i:]
            if len(suite) < seuil:
                break
            if self._w(ep - suite[0]["t"]) < POIDS_MIN:
                continue
            ecarts = [suite[k + 1]["t"] - suite[k]["t"] for k in range(len(suite) - 1)]
            if min(ecarts) > 0 and _dispersion(ecarts) <= 0.15:
                n = len(suite)
                alertes.append(self._alerte("correlation_cadence_mecanique", "haute" if n >= 2 * seuil else "moyenne", evt,
                                            _siem_tf("rg4.siem.msg.145", n=n, v2=int(sum(ecarts) / len(ecarts))), {"nb": n}, 7200))
                break
        # 42 — escalade : suite strictement croissante qui se termine à ce retrait
        j = len(c["ret"]) - 1
        while j > 0 and c["ret"][j - 1]["m"] < c["ret"][j]["m"]:
            j -= 1
        serie = [(x, self._w(ep - x["t"])) for x in c["ret"][j:]]
        serie = [(x, w) for x, w in serie if w >= POIDS_MIN]
        seuil = self._s("escalade")
        if serie and sum(w for _x, w in serie) >= seuil - EPS:
            debut, fin = serie[0][0]["m"], serie[-1][0]["m"]
            alertes.append(self._alerte("correlation_escalade_montants", "haute" if debut > 0 and fin >= 3 * debut else "moyenne", evt,
                                        _siem_tf("rg4.siem.msg.140", v1=len(serie), debut=debut, fin=fin), {"montants": [round(x["m"]) for x, _w in serie][:12]}, 7200))
        # 50 — montants identiques
        par_montant = defaultdict(float)
        for x, w in recents:
            par_montant[round(x["m"], 2)] += w
        if par_montant:
            m_max, n = max(par_montant.items(), key=lambda kv: kv[1])
            seuil = self._s("identiques")
            if m_max > 0 and n >= seuil - EPS:
                alertes.append(self._alerte("correlation_montants_identiques", "haute" if n >= seuil + 2 - EPS else "moyenne", evt,
                                            _siem_tf("rg4.siem.msg.146", v1=round(n), m_max=m_max), {"montant": m_max, "nb": round(n)}, 7200))
        # le retrait rejoint l'historique après avoir été jugé
        c["hist"].append([ep, montant])
        if dest:
            c["dests"].setdefault(dest, ep)
        if ip:
            c["ips"].setdefault(ip, ep)
        return alertes

    # ── recharges : règles 47 et 28 (premier temps) ─────────────────────────
    def _recharge(self, c, uid, ep, evt) -> list:
        c["rec"].append([ep, evt.get("ip") or "", evt.get("ent")])
        c["libres"].append(ep)
        n = sum(self._w(ep - t) for t, _ip, _e in c["rec"])
        seuil = self._s("recharges")
        if n >= seuil - EPS:
            return [self._alerte("correlation_recharges_en_rafale", "haute" if n >= 2 * seuil - EPS else "moyenne", evt, _siem_tf("rg4.siem.msg.141", v1=round(n)), {"nb": round(n)}, 7200)]
        return []

    # ── retraits vus dans l'audit : règles 24 et 28 (second temps) ──────────
    def _config(self, c, uid, ep, evt) -> list:
        c["cfg"].append([ep, str(evt.get("chemin", ""))])
        if c["cfg_t0"] is None:
            c["cfg_t0"] = ep
        return []

    def _retrait_audit(self, c, uid, ep, evt) -> list:
        alertes = []
        # 24 — un réglage armé attend le premier retrait, quel que soit le temps écoulé ; la gravité baisse avec l'âge
        armes = [(t, ch) for t, ch in c["cfg"] if self._w(ep - t) >= POIDS_MIN]
        if armes:
            t, chemin = armes[0]
            deja = c["cfg_t0"] is not None and c["cfg_t0"] < t - PALIER_S
            niveau = ["moyenne", "haute", "critique"].index("haute" if deja else "critique") - (0 if self._w(ep - t) >= 0.5 else 1)
            alertes.append(self._alerte("correlation_config_wallet_retrait", ["moyenne", "haute", "critique"][max(niveau, 0)], evt,
                                        _siem_tf("rg4.siem.msg.142", v1=int((ep - t) // 60)), {"reglage": chemin}))
            c["cfg"] = []
        # 28 — ce retrait solde la plus ancienne recharge encore libre
        libre = next((t for t in sorted(c["libres"]) if self._w(ep - t, PALIER_ALLER_RETOUR_S) >= POIDS_MIN), None)
        if libre is not None:
            c["libres"].remove(libre)
            c["paires"].append([ep, self._w(ep - libre, PALIER_ALLER_RETOUR_S)])
            cycles = sum(wg * self._w(ep - t) for t, wg in c["paires"])
            if cycles >= 1 - EPS:
                alertes.append(self._alerte("correlation_aller_retour_wallet", "haute" if cycles >= 2 - EPS else "moyenne", evt,
                                            _siem_tf("rg4.siem.msg.147", v1=round(cycles), v2=PALIER_ALLER_RETOUR_S // 60), {"cycles": round(cycles)}, 7200))
        return alertes

    # ── oubli : on élimine ce qui ne pèse plus rien ─────────────────────────
    def _elaguer(self, c, ep):
        c["ret"] = [x for x in c["ret"] if self._w(ep - x["t"], JOUR_S) >= POIDS_OUBLI][-300:]
        c["rec"] = [x for x in c["rec"] if self._w(ep - x[0]) >= POIDS_OUBLI][-200:]
        c["libres"] = [t for t in c["libres"] if self._w(ep - t, PALIER_ALLER_RETOUR_S) >= POIDS_OUBLI][-200:]
        c["paires"] = [p for p in c["paires"] if self._w(ep - p[0]) >= POIDS_OUBLI][-100:]
        c["cfg"] = [x for x in c["cfg"] if self._w(ep - x[0]) >= POIDS_OUBLI][-50:]
        c["hist"] = c["hist"][-1500:]
        for cle in ("dests", "ips"):
            while len(c[cle]) > 300:
                c[cle].pop(next(iter(c[cle])))
