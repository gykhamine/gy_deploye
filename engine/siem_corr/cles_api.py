"""Règles de corrélation — famille « clés API » : création, usage, révocation, détournement."""
from datetime import timedelta
from .base import (DORMANTE_JOURS, FENETRE_S, PRIVILEGES, Regle, S, SENSIBLES, _hors_heures, _par_uid, _profil, _rafale)
from engine.i18n import tf as _siem_tf  # noqa: E402


# ── 5. Clés API détournées ──────────────────────────────────────────────────

def detecter_cles_api(evts, historique, creations=(), volume_x=5.0) -> list:
    """evts / historique : événements portés par une clé API (cle=True). creations : événements `cle_creee` de la fenêtre et des jours précédents."""
    alertes = []
    hist = _par_uid([h for h in historique if h.get("cle")])
    for uid, liste in _par_uid([e for e in evts if e.get("cle")]).items():
        h = hist.get(uid, [])
        profil = _profil(h)
        dernier = liste[-1]
        signaux = []
        neuve = False
        if profil["total"] >= 20 and dernier["ip"] and dernier["ip"] not in profil["ips"]:
            signaux.append(_siem_tf("rg4.siem.msg.110", v1=dernier['ip']))
        if h and (liste[0]["t"] - h[-1]["t"]) > timedelta(days=DORMANTE_JOURS):
            signaux.append(_siem_tf("rg4.siem.msg.111", days=(liste[0]['t'] - h[-1]['t']).days))
        if _hors_heures(profil, dernier["t"].hour):
            signaux.append(_siem_tf("rg4.siem.msg.112", hour=dernier['t'].hour))
        jours = list(profil["par_jour"].values())
        aujourdhui = sum(1 for e in liste if e["t"].date() == dernier["t"].date())
        if len(jours) >= 5 and aujourdhui >= 30 and sum(jours) / len(jours) > 0 and aujourdhui > volume_x * sum(jours) / len(jours):
            signaux.append(_siem_tf("rg4.siem.msg.113", aujourdhui=aujourdhui, v2=sum(jours) / len(jours)))
        for c in (x for x in creations if x["uid"] == uid):
            ailleurs = next((e for e in liste if e["t"] > c["t"] and (e["t"] - c["t"]).total_seconds() <= FENETRE_S
                             and e["ip"] and c["ip"] and e["ip"] != c["ip"]), None)
            if ailleurs:
                signaux.append(_siem_tf("rg4.siem.msg.114", v1=c['ip'], v2=ailleurs['ip']))
                neuve = True
                break
        if signaux:
            alertes.append({"regle": "correlation_cle_api", "gravite": "haute" if neuve or len(signaux) >= 2 else "moyenne", "uid": uid,
                            "ip": dernier["ip"], "ent": dernier.get("ent"), "t": dernier["t"], "message": _siem_tf("rg4.siem.msg.116") + " ; ".join(signaux),
                            "details": {"signaux": signaux}})
    return alertes


def detecter_cles_multiples(evts, seuil=3, fenetre_s=FENETRE_S) -> list:
    return [{"regle": "correlation_cles_api_multiples", "gravite": "haute", "uid": uid, "ip": d["ip"], "ent": d.get("ent"), "t": d["t"],
             "message": _siem_tf("rg4.siem.msg.108", n=n, v2=fenetre_s // 60), "details": {"nb": n}}
            for uid, n, d in _rafale(evts, "cle_creee", seuil, fenetre_s)]


# ── 21. Usage inédit d'une clé API ──────────────────────────────────────────

def detecter_cle_usage_inedit(evts, historique) -> list:
    hist = _par_uid([h for h in historique if h.get("cle")])
    alertes, vus = [], set()
    for e in sorted(evts, key=lambda x: x["t"]):
        if not e.get("cle") or e["kind"] not in SENSIBLES | PRIVILEGES or (e["uid"], e["kind"]) in vus:
            continue
        h = hist.get(e["uid"], [])
        if len(h) >= 20 and not any(x["kind"] == e["kind"] for x in h):
            vus.add((e["uid"], e["kind"]))
            alertes.append({"regle": "correlation_cle_usage_inedit", "gravite": "critique" if e["kind"] in PRIVILEGES else "haute", "uid": e["uid"], "ip": e["ip"],
                            "ent": e.get("ent"), "t": e["t"], "message": _siem_tf("rg4.siem.msg.115", v1=e['kind'], v2=len(h)),
                            "details": {"action": e.get("chemin", "")}, "dedup": 21600})
    return alertes


# ── 35. Clé jetable ─────────────────────────────────────────────────────────

def detecter_cle_jetable(evts, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] != "cle_creee":
                continue
            reste = [x for x in liste[i + 1:] if (x["t"] - a["t"]).total_seconds() <= fenetre_s]
            usage = next((x for x in reste if x["cle"] and x["kind"] in ("retrait", "export")), None)
            revoc = next((x for x in reste if usage and x["kind"] == "cle_revoquee" and x["t"] >= usage["t"]), None)
            if usage and revoc:
                alertes.append({"regle": "correlation_cle_jetable", "gravite": "critique", "uid": uid, "ip": revoc["ip"], "ent": revoc.get("ent"), "t": revoc["t"],
                                "message": _siem_tf("rg4.siem.msg.117", v1=usage['kind'], v2=int((revoc['t'] - a['t']).total_seconds() // 60)),
                                "details": {"usage": usage.get("chemin", ""), "revocation": revoc.get("chemin", "")}, "dedup": 86400})
                break
    return alertes


# ── 41. Révocations en masse ────────────────────────────────────────────────

def detecter_revocations_en_masse(evts, seuil=3, fenetre_s=FENETRE_S) -> list:
    return [{"regle": "correlation_revocations_en_masse", "gravite": "haute", "uid": uid, "ip": d["ip"], "ent": d.get("ent"), "t": d["t"],
             "message": _siem_tf("rg4.siem.msg.109", n=n, v2=fenetre_s // 60), "details": {"nb": n}}
            for uid, n, d in _rafale(evts, "cle_revoquee", seuil, fenetre_s)]


REGLES = [
    Regle(5, detecter_cles_api, ("recents", "historique", "creations", S("volume_x", 5, float),)),
    Regle(14, detecter_cles_multiples, ("recents", S("cles_multiples", 3),)),
    Regle(21, detecter_cle_usage_inedit, ("recents", "historique",)),
    Regle(35, detecter_cle_jetable, ("recents",)),
    Regle(41, detecter_revocations_en_masse, ("recents", S("revocations_rafale", 3),)),
]
