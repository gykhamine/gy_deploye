"""Règles de corrélation — famille « wallet » : retraits, recharges, destinations, montants, cadence.

Ces règles tournent désormais dans le moteur continu (continu.py), sans fenêtre de calcul. Les fonctions `detecter_*` ci-dessous sont la version à fenêtres (v17) :
elles ne sont plus appelées en production (`REGLES` est vide) et servent d'oracle aux tests d'équivalence — `REFERENCE` en garde la déclaration d'origine."""
from collections import defaultdict
from datetime import timedelta
from .base import (FENETRE_S, Regle, S, _mediane, _par_uid, _rafale)
from engine.i18n import tf as _siem_tf  # noqa: E402


# ── 2. Fraude wallet ────────────────────────────────────────────────────────

def detecter_fraude_wallet(retraits, historique, plafond=0.0, x=5.0, fenetre_s=FENETRE_S) -> list:
    """retraits : dicts {t, uid, montant, dest, ip} de la fenêtre ; historique : mêmes dicts des 30 jours précédents (ip peut être '').
    Plusieurs signaux sur un même retrait montent la gravité."""
    alertes = []
    hist = defaultdict(list)
    for h in historique:
        hist[h["uid"]].append(h)
    for uid, liste in _par_uid(retraits).items():
        h = hist.get(uid, [])
        montants = [r["montant"] for r in h]
        dests = {r["dest"] for r in h}
        ips = {r["ip"] for r in h if r.get("ip")}
        for r in liste:
            signaux = []
            if len(montants) >= 5 and _mediane(montants) > 0 and r["montant"] > x * _mediane(montants):
                signaux.append(_siem_tf("rg4.siem.msg.159", v1=r['montant'], v2=_mediane(montants)))
            if len(dests) >= 3 and r["dest"] not in dests:
                signaux.append(_siem_tf("rg4.siem.msg.136"))
            if len(ips) >= 3 and r.get("ip") and r["ip"] not in ips:
                signaux.append(_siem_tf("rg4.siem.msg.160", v1=r['ip']))
            if signaux:
                alertes.append({"regle": "correlation_fraude_wallet", "gravite": "critique" if len(signaux) >= 2 else "haute", "uid": uid,
                                "ip": r.get("ip", ""), "ent": r.get("ent"), "t": r["t"], "message": _siem_tf("rg4.siem.msg.243") + " ; ".join(signaux),
                                "details": {"montant": r["montant"], "dest": r["dest"][-4:], "signaux": signaux}})
        # fractionnement : plusieurs retraits proches du plafond, ou rafale de retraits quasi identiques dont le cumul dépasse l'habitude
        for i, r in enumerate(liste):
            groupe = [s for s in liste[i:] if (s["t"] - r["t"]).total_seconds() <= fenetre_s]
            if len(groupe) < 3:
                continue
            total = sum(s["montant"] for s in groupe)
            if plafond > 0:
                proches = [s for s in groupe if 0.8 * plafond <= s["montant"] < plafond]
                cas = len(proches) >= 3
            else:
                ms = [s["montant"] for s in groupe]
                cas = len(groupe) >= 4 and min(ms) > 0 and max(ms) / min(ms) <= 1.2 and (not montants or total > 3 * _mediane(montants))
            if cas:
                alertes.append({"regle": "correlation_retraits_fractionnes", "gravite": "haute", "uid": uid, "ip": groupe[-1].get("ip", ""),
                                "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.167", v1=len(groupe), v2=int((groupe[-1]['t'] - r['t']).total_seconds() // 60), total=total)
                                           + (_siem_tf("rg4.siem.msg.149") if plafond > 0 else ""),
                                "details": {"nb": len(groupe), "total": total}})
                break
    return alertes


# ── 12. Moyen de retrait récent ─────────────────────────────────────────────

def detecter_moyen_recent(retraits, minutes=60) -> list:
    """retraits : dicts {t, uid, montant, dest, ip, moyen_maj} ; moyen_maj = dernière modification du moyen de retrait utilisé (None si inconnu)."""
    alertes = []
    for r in sorted(retraits, key=lambda x: x["t"]):
        maj = r.get("moyen_maj")
        if maj and timedelta(0) <= r["t"] - maj <= timedelta(minutes=minutes):
            alertes.append({"regle": "correlation_moyen_retrait_recent", "gravite": "haute", "uid": r["uid"], "ip": r.get("ip", ""), "ent": r.get("ent"), "t": r["t"],
                            "message": _siem_tf("rg4.siem.msg.161", v1=r['montant'], v2=int((r['t'] - maj).total_seconds() // 60)),
                            "details": {"montant": r["montant"], "dest": r["dest"][-4:]}})
    return alertes


# ── 17. Destination partagée ────────────────────────────────────────────────

def detecter_destination_partagee(retraits, historique, seuil=3) -> list:
    par_dest = defaultdict(set)
    for r in list(retraits) + list(historique):
        if r.get("dest"):
            par_dest[r["dest"]].add(r["uid"])
    alertes, vus = [], set()
    for r in sorted(retraits, key=lambda x: x["t"]):
        comptes = par_dest.get(r["dest"], set())
        if len(comptes) >= seuil and r["dest"] not in vus:
            vus.add(r["dest"])
            alertes.append({"regle": "correlation_destination_partagee", "gravite": "critique" if len(comptes) >= 2 * seuil else "haute", "uid": r["uid"],
                            "ip": r.get("ip", ""), "ent": r.get("ent"), "t": r["t"],
                            "message": _siem_tf("rg4.siem.msg.144", v1=len(comptes), v2=r['dest'][-4:]),
                            "details": {"comptes": sorted(comptes)[:20], "dest": r["dest"][-4:]}, "dedup": 86400})
    return alertes


# ── 20. Cumul de retraits sur 24 h ──────────────────────────────────────────

def detecter_cumul_retraits(retraits_24h, historique_jours, x=5.0) -> list:
    """retraits_24h : retraits des dernières 24 h ; historique_jours : retraits des 30 jours qui précèdent ces 24 h. Alerte si le cumul des 24 h
    (au moins 2 retraits) dépasse x fois la journée médiane du compte, avec au moins 5 journées d'historique."""
    jours = defaultdict(lambda: defaultdict(float))
    for h in historique_jours:
        jours[h["uid"]][h["t"].date()] += h["montant"]
    cumul = defaultdict(list)
    for r in retraits_24h:
        cumul[r["uid"]].append(r)
    alertes = []
    for uid, liste in cumul.items():
        mediane = _mediane(list(jours.get(uid, {}).values()))
        total = sum(r["montant"] for r in liste)
        if len(jours.get(uid, {})) >= 5 and mediane > 0 and len(liste) >= 2 and total > x * mediane:
            dernier = max(liste, key=lambda r: r["t"])
            alertes.append({"regle": "correlation_cumul_retraits", "gravite": "haute", "uid": uid, "ip": dernier.get("ip", ""), "ent": dernier.get("ent"),
                            "t": dernier["t"], "message": _siem_tf("rg4.siem.msg.162", v1=len(liste), total=total, mediane=mediane),
                            "details": {"nb": len(liste), "total": total, "mediane_jour": mediane}, "dedup": 21600})
    return alertes


# ── 24. Réglage du wallet puis retrait ──────────────────────────────────────

def detecter_config_wallet_puis_retrait(evts, historique, fenetre_s=FENETRE_S) -> list:
    alertes = []
    deja = {(h["uid"], h["kind"]) for h in historique}
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] != "config_wallet":
                continue
            suite = next((x for x in liste[i + 1:] if x["kind"] == "retrait" and (x["t"] - a["t"]).total_seconds() <= fenetre_s), None)
            if suite:
                alertes.append({"regle": "correlation_config_wallet_retrait", "gravite": "haute" if (uid, "config_wallet") in deja else "critique", "uid": uid,
                                "ip": suite["ip"], "ent": suite.get("ent"), "t": suite["t"],
                                "message": _siem_tf("rg4.siem.msg.142", v1=int((suite['t'] - a['t']).total_seconds() // 60)),
                                "details": {"reglage": a.get("chemin", "")}})
                break
    return alertes


# ── 27. Éparpillement des retraits ──────────────────────────────────────────

def detecter_eparpillement(retraits, seuil=4, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(retraits).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            dests = {x["dest"] for x in groupe if x.get("dest")}
            if len(dests) >= seuil:
                alertes.append({"regle": "correlation_eparpillement_retraits", "gravite": "critique" if len(dests) >= 2 * seuil else "haute", "uid": uid,
                                "ip": groupe[-1].get("ip", ""), "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.139", v1=len(dests), v2=fenetre_s // 60, v3=sum((x['montant'] for x in groupe))),
                                "details": {"destinations": sorted(d[-4:] for d in dests)[:12]}, "dedup": 7200})
                break
    return alertes


# ── 28. Aller-retour de fonds ───────────────────────────────────────────────

def detecter_aller_retour_wallet(evts, delai_s=1800) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        cycles, derniere, libres = 0, None, [e for e in liste if e["kind"] == "retrait"]
        for rec in (e for e in liste if e["kind"] == "recharge"):
            sortie = next((r for r in libres if 0 < (r["t"] - rec["t"]).total_seconds() <= delai_s), None)
            if sortie:
                libres.remove(sortie)
                cycles, derniere = cycles + 1, sortie
        if cycles:
            alertes.append({"regle": "correlation_aller_retour_wallet", "gravite": "haute" if cycles >= 2 else "moyenne", "uid": uid, "ip": derniere["ip"],
                            "ent": derniere.get("ent"), "t": derniere["t"], "message": _siem_tf("rg4.siem.msg.163", cycles=cycles, v2=delai_s // 60),
                            "details": {"cycles": cycles}, "dedup": 7200})
    return alertes


# ── 34. Cadence mécanique des retraits ──────────────────────────────────────

def _dispersion(ecarts) -> float:
    moy = sum(ecarts) / len(ecarts)
    return (sum((x - moy) ** 2 for x in ecarts) / len(ecarts)) ** 0.5 / moy if moy > 0 else 9.9


def detecter_cadence_mecanique(retraits, seuil=5, tolerance=0.15, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(retraits).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            meilleur = 0
            for n in range(seuil, len(groupe) + 1):
                ecarts = [(groupe[k + 1]["t"] - groupe[k]["t"]).total_seconds() for k in range(n - 1)]
                if min(ecarts) > 0 and _dispersion(ecarts) <= tolerance:
                    meilleur = n
            if meilleur:
                run = groupe[:meilleur]
                alertes.append({"regle": "correlation_cadence_mecanique", "gravite": "haute" if meilleur >= 2 * seuil else "moyenne", "uid": uid,
                                "ip": run[-1].get("ip", ""), "ent": run[-1].get("ent"), "t": run[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.164", meilleur=meilleur, v2=int(sum(((run[k + 1]['t'] - run[k]['t']).total_seconds() for k in range(meilleur - 1))) / (meilleur - 1))),
                                "details": {"nb": meilleur}, "dedup": 7200})
                break
    return alertes


# ── 42. Escalade des montants ───────────────────────────────────────────────

def detecter_escalade_montants(retraits, seuil=4, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(retraits).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            meilleur, serie = [], [groupe[0]]
            for x in groupe[1:]:
                if x["montant"] > serie[-1]["montant"]:
                    serie.append(x)
                else:
                    meilleur = serie if len(serie) > len(meilleur) else meilleur
                    serie = [x]
            meilleur = serie if len(serie) > len(meilleur) else meilleur
            if len(meilleur) >= seuil:
                premier, dernier = meilleur[0]["montant"], meilleur[-1]["montant"]
                alertes.append({"regle": "correlation_escalade_montants", "gravite": "haute" if premier > 0 and dernier >= 3 * premier else "moyenne", "uid": uid,
                                "ip": meilleur[-1].get("ip", ""), "ent": meilleur[-1].get("ent"), "t": meilleur[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.165", v1=len(meilleur), premier=premier, dernier=dernier),
                                "details": {"montants": [round(x["montant"]) for x in meilleur][:12]}, "dedup": 7200})
                break
    return alertes


# ── 47. Recharges en rafale ─────────────────────────────────────────────────

def detecter_recharges_en_rafale(evts, seuil=5, fenetre_s=FENETRE_S) -> list:
    return [{"regle": "correlation_recharges_en_rafale", "gravite": "haute" if n >= 2 * seuil else "moyenne", "uid": uid, "ip": d["ip"], "ent": d.get("ent"), "t": d["t"],
             "message": _siem_tf("rg4.siem.msg.158", n=n, v2=fenetre_s // 60), "details": {"nb": n}, "dedup": 7200}
            for uid, n, d in _rafale(evts, "recharge", seuil, fenetre_s)]


# ── 50. Montants identiques ─────────────────────────────────────────────────

def detecter_montants_identiques(retraits, seuil=3, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(retraits).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            compte = defaultdict(int)
            for x in groupe:
                compte[round(x["montant"], 2)] += 1
            montant, n = max(compte.items(), key=lambda kv: kv[1])
            if montant > 0 and n >= seuil:
                alertes.append({"regle": "correlation_montants_identiques", "gravite": "haute" if n >= seuil + 2 else "moyenne", "uid": uid, "ip": groupe[-1].get("ip", ""),
                                "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"], "message": _siem_tf("rg4.siem.msg.166", n=n, montant=montant, v3=fenetre_s // 60),
                                "details": {"montant": montant, "nb": n}, "dedup": 7200})
                break
    return alertes


REFERENCE = [
    Regle(2, detecter_fraude_wallet, ("retraits", "hist_retraits",), {"plafond": "plafond", "x": S("volume_x", 5, float)}),
    Regle(12, detecter_moyen_recent, ("retraits", S("moyen_recent_minutes", 60),)),
    Regle(17, detecter_destination_partagee, ("retraits", "hist_retraits", S("dest_partagee_comptes", 3),)),
    Regle(20, detecter_cumul_retraits, ("retraits_24h", "hist_jours", S("volume_x", 5, float),)),
    Regle(24, detecter_config_wallet_puis_retrait, ("recents", "historique",)),
    Regle(27, detecter_eparpillement, ("retraits", S("eparpillement_dest", 4),)),
    Regle(28, detecter_aller_retour_wallet, ("recents",)),
    Regle(34, detecter_cadence_mecanique, ("retraits", S("cadence_retraits", 5),)),
    Regle(42, detecter_escalade_montants, ("retraits", S("escalade_retraits", 4),)),
    Regle(47, detecter_recharges_en_rafale, ("recents", S("recharges_rafale", 5),)),
    Regle(50, detecter_montants_identiques, ("retraits", S("montants_identiques", 3),)),
]


REGLES = []   # famille servie par le moteur continu
