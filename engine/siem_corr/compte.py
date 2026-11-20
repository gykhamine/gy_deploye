"""Règles de corrélation — famille « compte » : prise de contrôle, sessions, réveils, comptes neufs ou pilotés, identifiants."""
import re
from collections import defaultdict
from datetime import timedelta
from .base import (FENETRE_S, PRIVILEGES, REVEIL_JOURS, Regle, S, SENSIBLES, _hors_heures, _par_uid, _rafale)
from engine.i18n import tf as _siem_tf  # noqa: E402


# ── 1. Prise de contrôle ────────────────────────────────────────────────────

def detecter_prise_de_controle(evts, fenetre_s=FENETRE_S) -> list:
    """evts : dicts {t, uid, ip, kind, ...}. Une alerte par changement d'identité suivi d'une action sensible dans la fenêtre."""
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, ch in enumerate(liste):
            if ch["kind"] != "identite":
                continue
            for suite in liste[i + 1:]:
                if (suite["t"] - ch["t"]).total_seconds() > fenetre_s:
                    break
                if suite["kind"] in SENSIBLES:
                    autre_ip = bool(ch["ip"] and suite["ip"] and ch["ip"] != suite["ip"])
                    alertes.append({"regle": "correlation_prise_de_controle", "gravite": "critique" if autre_ip else "haute", "uid": uid,
                                    "ip": suite["ip"], "ent": suite.get("ent"), "t": suite["t"],
                                    "message": _siem_tf("rg4.siem.msg.131", v1=suite['kind'], v2=int((suite['t'] - ch['t']).total_seconds() // 60))
                                               + (_siem_tf("rg4.siem.msg.134") if autre_ip else ""),
                                    "details": {"declencheur": ch.get("chemin", ""), "action": suite.get("chemin", ""), "ip_changement": ch["ip"],
                                                "ip_action": suite["ip"]}})
                    break
    return alertes


# ── 10. Compte dormant réveillé ─────────────────────────────────────────────

def detecter_compte_dormant(evts, reveils: dict, derniere_activite: dict, jours=60) -> list:
    """reveils : {uid: première trace des REVEIL_JOURS derniers jours} ; derniere_activite : {uid: dernière trace avant cette fenêtre}.
    Silence = écart entre les deux. Un compte sans trace antérieure (neuf) est ignoré ; un compte actif en continu n'a pas d'écart."""
    alertes, vus = [], set()
    for e in sorted(evts, key=lambda x: x["t"]):
        if e["kind"] not in SENSIBLES | PRIVILEGES or e["uid"] in vus:
            continue
        reveil, derniere = reveils.get(e["uid"]), derniere_activite.get(e["uid"])
        if reveil and derniere and (reveil - derniere).days >= jours:
            vus.add(e["uid"])
            alertes.append({"regle": "correlation_compte_dormant", "gravite": "haute", "uid": e["uid"], "ip": e["ip"], "ent": e.get("ent"), "t": e["t"],
                            "message": _siem_tf("rg4.siem.msg.119", days=(reveil - derniere).days, reveil=reveil, v3=e['kind']),
                            "details": {"action": e.get("chemin", ""), "derniere_activite": derniere.isoformat(), "reveil": reveil.isoformat()},
                            "dedup": REVEIL_JOURS * 86400})
    return alertes


# ── 11. Compte neuf déjà actif ──────────────────────────────────────────────

def detecter_compte_neuf(evts, premiere_activite: dict, heures=24) -> list:
    """premiere_activite : {uid: toute première trace du compte}. Un compte créé hier qui retire, exporte ou administre déjà : compte jetable ou préparé."""
    alertes, vus = [], set()
    for e in sorted(evts, key=lambda x: x["t"]):
        if e["kind"] not in SENSIBLES | PRIVILEGES or e["uid"] in vus:
            continue
        premiere = premiere_activite.get(e["uid"])
        if premiere and timedelta(0) <= e["t"] - premiere <= timedelta(hours=heures):
            vus.add(e["uid"])
            grave = e["kind"] in PRIVILEGES or e["kind"] == "retrait"
            alertes.append({"regle": "correlation_compte_neuf", "gravite": "haute" if grave else "moyenne", "uid": e["uid"], "ip": e["ip"], "ent": e.get("ent"),
                            "t": e["t"], "message": _siem_tf("rg4.siem.msg.120", v1=int((e['t'] - premiere).total_seconds() // 3600), v2=e['kind']),
                            "details": {"action": e.get("chemin", ""), "premiere_trace": premiere.isoformat()}, "dedup": 86400})
    return alertes


# ── 16. Sessions parallèles ─────────────────────────────────────────────────

def _reseau(ip: str) -> str:
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
        return str(ipaddress.ip_network(f"{ip}/{24 if a.version == 4 else 48}", strict=False))
    except ValueError:
        return ip


def detecter_sessions_paralleles(evts, fenetre_s=300) -> list:
    """Hors clés API (une clé tourne légitimement sur un serveur) : une action sensible et un autre geste du même compte depuis un autre réseau,
    à moins de 5 min. Un humain n'est pas à deux endroits ; un voleur de session, si."""
    alertes = []
    for uid, liste in _par_uid([e for e in evts if not e.get("cle") and e["ip"] and e["kind"] not in ("", "refus")]).items():
        for s_ in liste:
            if s_["kind"] not in SENSIBLES | PRIVILEGES:
                continue
            autre = next((e for e in liste if e is not s_ and abs((e["t"] - s_["t"]).total_seconds()) <= fenetre_s and _reseau(e["ip"]) != _reseau(s_["ip"])), None)
            if autre:
                alertes.append({"regle": "correlation_sessions_paralleles", "gravite": "critique" if autre["kind"] in SENSIBLES | PRIVILEGES else "haute",
                                "uid": uid, "ip": s_["ip"], "ent": s_.get("ent"), "t": max(s_["t"], autre["t"]),
                                "message": _siem_tf("rg4.siem.msg.122", v1=s_['kind'], v2=s_['ip'], v3=autre['kind'], v4=autre['ip'], v5=fenetre_s // 60),
                                "details": {"ips": [s_["ip"], autre["ip"]]}})
                break
    return alertes


# ── 18. Rotation d'IP ───────────────────────────────────────────────────────

def detecter_rotation_ip(evts, seuil=4, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid([e for e in evts if not e.get("cle") and e["ip"] and e["kind"] != ""]).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            reseaux = {_reseau(x["ip"]) for x in groupe}
            if len(reseaux) >= seuil:
                grave = any(x["kind"] in SENSIBLES | PRIVILEGES for x in groupe)
                alertes.append({"regle": "correlation_rotation_ip", "gravite": "haute" if grave else "moyenne", "uid": uid, "ip": groupe[-1]["ip"],
                                "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.126", v1=len(reseaux), v2=fenetre_s // 60) + (_siem_tf("rg4.siem.msg.132") if grave else ""),
                                "details": {"reseaux": sorted(reseaux)[:10]}})
                break
    return alertes


# ── 19. Action sensible hors des heures du compte ───────────────────────────

def detecter_sensible_hors_heures(evts, profils: dict) -> list:
    """profils : {uid: {"heures": {heure: nombre}, "total": n}} de toute l'activité des 30 jours précédents (pas seulement les gestes classés)."""
    alertes, vus = [], set()
    for e in sorted(evts, key=lambda x: x["t"]):
        if e["kind"] not in SENSIBLES or (e["uid"], e["kind"]) in vus:
            continue
        p = profils.get(e["uid"])
        if p and _hors_heures(p, e["t"].hour):
            vus.add((e["uid"], e["kind"]))
            alertes.append({"regle": "correlation_sensible_hors_heures", "gravite": "haute" if e["kind"] == "retrait" else "moyenne", "uid": e["uid"],
                            "ip": e["ip"], "ent": e.get("ent"), "t": e["t"], "message": _siem_tf("rg4.siem.msg.121", v1=e['kind'], hour=e['t'].hour),
                            "details": {"action": e.get("chemin", "")}, "dedup": 21600})
    return alertes


# ── 25. Compte marionnette ──────────────────────────────────────────────────

def detecter_marionnette(evts, premiere_activite: dict, fenetre_s=FENETRE_S) -> list:
    """Un compte créé par A dont la toute première trace est une action sensible, depuis la même IP que A : A pilote un compte qu'il vient de fabriquer."""
    alertes, vus = [], set()
    for cr in sorted((e for e in evts if e["kind"] == "membre_cree" and e["ip"]), key=lambda x: x["t"]):
        for e in sorted(evts, key=lambda x: x["t"]):
            if e["uid"] == cr["uid"] or e["uid"] in vus or e["ip"] != cr["ip"] or e["kind"] not in SENSIBLES | PRIVILEGES:
                continue
            premiere = premiere_activite.get(e["uid"])
            if premiere and premiere >= cr["t"] and 0 < (e["t"] - cr["t"]).total_seconds() <= fenetre_s:
                vus.add(e["uid"])
                alertes.append({"regle": "correlation_compte_marionnette", "gravite": "haute", "uid": e["uid"], "ip": e["ip"], "ent": e.get("ent"), "t": e["t"],
                                "message": _siem_tf("rg4.siem.msg.123", v1=e['kind'], v2=cr['uid']),
                                "details": {"createur": cr["uid"], "action": e.get("chemin", "")}, "dedup": 86400})
    return alertes


# ── 30. Usurpation par un administrateur ────────────────────────────────────
_RE_CIBLE = re.compile(r"/employes/(\d+)/modifier/")


def detecter_usurpation_par_admin(evts, autres_comptes=1, fenetre_s=FENETRE_S) -> list:
    """Un administrateur modifie le compte T (mot de passe, e-mail…), puis T fait une action sensible depuis la même IP que l'administrateur : il s'est mis à sa place.
    Critique quand l'IP n'est utilisée que par l'administrateur et T. Si au moins `autres_comptes` autres comptes de la même entreprise y sont vus dans la fenêtre
    (bureau, réseau partagé), l'IP ne prouve plus rien : la gravité descend à « haute ». `autres_comptes` à 0 désactive cette atténuation."""
    alertes, vus = [], set()
    for a in sorted((e for e in evts if e["kind"] == "identite" and e["ip"]), key=lambda x: x["t"]):
        m = _RE_CIBLE.search(a.get("chemin", ""))
        if not m or int(m.group(1)) == a["uid"]:
            continue
        cible = int(m.group(1))
        suite = next((e for e in evts if e["uid"] == cible and e["ip"] == a["ip"] and e["kind"] in SENSIBLES | PRIVILEGES
                      and 0 < (e["t"] - a["t"]).total_seconds() <= fenetre_s), None)
        if suite and cible not in vus:
            vus.add(cible)
            autres = {e["uid"] for e in evts if e["ip"] == a["ip"] and e["uid"] not in (a["uid"], cible) and abs((e["t"] - a["t"]).total_seconds()) <= fenetre_s
                      and (not a.get("ent") or not e.get("ent") or e["ent"] == a["ent"])}
            partagee = autres_comptes > 0 and len(autres) >= autres_comptes
            alertes.append({"regle": "correlation_usurpation_par_admin", "gravite": "haute" if partagee else "critique", "uid": cible, "ip": a["ip"], "ent": suite.get("ent"), "t": suite["t"],
                            "message": _siem_tf("rg4.siem.msg.124", v1=a['uid'], v2=suite['kind'])
                                       + (_siem_tf("rg4.siem.msg.127", v1=len(autres)) if partagee else ""),
                            "details": {"administrateur": a["uid"], "action": suite.get("chemin", ""), "autres_comptes_ip": len(autres)}, "dedup": 86400})
    return alertes


# ── 33. Verrouillage du compte ──────────────────────────────────────────────

def detecter_verrouillage(evts, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] != "2fa_off":
                continue
            ident = next((x for x in liste[i + 1:] if x["kind"] == "identite" and (x["t"] - a["t"]).total_seconds() <= fenetre_s), None)
            if not ident:
                continue
            sortie = next((x for x in liste[i + 1:] if x["kind"] in ("retrait", "export") and (x["t"] - a["t"]).total_seconds() <= fenetre_s), None)
            alertes.append({"regle": "correlation_verrouillage_du_compte", "gravite": "critique" if sortie else "haute", "uid": uid, "ip": ident["ip"], "ent": ident.get("ent"),
                            "t": ident["t"], "message": _siem_tf("rg4.siem.msg.128") + (f" puis {sortie['kind']}" if sortie else "") + _siem_tf("rg4.siem.msg.125"),
                            "details": {"identite": ident.get("chemin", ""), "usage": sortie.get("chemin", "") if sortie else ""}, "dedup": 7200})
            break
    return alertes


# ── 43. Identifiants changés en rafale ──────────────────────────────────────

def _modif_autrui(e) -> bool:
    m = _RE_CIBLE.search(e.get("chemin", ""))
    return bool(m and int(m.group(1)) != e["uid"])


def detecter_identite_en_rafale(evts, seuil=3, fenetre_s=FENETRE_S) -> list:
    propres = [e for e in evts if e["kind"] == "identite" and not _modif_autrui(e)]
    return [{"regle": "correlation_identite_en_rafale", "gravite": "haute", "uid": uid, "ip": d["ip"], "ent": d.get("ent"), "t": d["t"],
             "message": _siem_tf("rg4.siem.msg.118", n=n, v2=fenetre_s // 60), "details": {"nb": n}, "dedup": 7200}
            for uid, n, d in _rafale(propres, "identite", seuil, fenetre_s)]


# ── 44. Identifiants changés après une sortie ───────────────────────────────

def detecter_identite_apres_sortie(evts, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] not in ("retrait", "export"):
                continue
            apres = [x for x in liste[i + 1:] if (x["t"] - a["t"]).total_seconds() <= fenetre_s]
            ident = next((x for x in apres if x["kind"] == "identite" and not _modif_autrui(x)), None)
            if ident:
                coupe = any(x["kind"] == "2fa_off" for x in apres)
                alertes.append({"regle": "correlation_identite_apres_sortie", "gravite": "haute" if coupe else "moyenne", "uid": uid, "ip": ident["ip"], "ent": ident.get("ent"),
                                "t": ident["t"], "message": _siem_tf("rg4.siem.msg.129", v1=a['kind'], v2=int((ident['t'] - a['t']).total_seconds() // 60)) + (_siem_tf("rg4.siem.msg.133") if coupe else ""),
                                "details": {"sortie": a.get("chemin", ""), "identite": ident.get("chemin", "")}, "dedup": 7200})
                break
    return alertes


# ── 48. Plusieurs comptes, une seule IP ─────────────────────────────────────

def detecter_comptes_meme_ip(evts, seuil=3, fenetre_s=FENETRE_S) -> list:
    par_ip = defaultdict(list)
    for e in sorted((x for x in evts if x["kind"] == "retrait" and x["ip"] and not x["cle"]), key=lambda x: x["t"]):
        par_ip[e["ip"]].append(e)
    alertes = []
    for ip, liste in par_ip.items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            comptes = {x["uid"] for x in groupe}
            if len(comptes) >= seuil:
                for uid in sorted(comptes):
                    dernier = [x for x in groupe if x["uid"] == uid][-1]
                    alertes.append({"regle": "correlation_comptes_meme_ip", "gravite": "haute", "uid": uid, "ip": ip, "ent": dernier.get("ent"), "t": dernier["t"],
                                    "message": _siem_tf("rg4.siem.msg.130", v1=len(comptes), v2=fenetre_s // 60),
                                    "details": {"comptes": sorted(comptes)[:12]}, "dedup": 7200})
                break
    return alertes


REGLES = [
    Regle(1, detecter_prise_de_controle, ("recents",)),
    Regle(10, detecter_compte_dormant, ("recents", "reveils", "derniere", S("dormant_jours", 60),)),
    Regle(11, detecter_compte_neuf, ("recents", "premieres", S("compte_neuf_heures", 24),)),
    Regle(16, detecter_sessions_paralleles, ("recents",)),
    Regle(18, detecter_rotation_ip, ("recents", S("ips_rotation", 4),)),
    Regle(19, detecter_sensible_hors_heures, ("recents", "profils",)),
    Regle(25, detecter_marionnette, ("recents", "premieres",)),
    Regle(30, detecter_usurpation_par_admin, ("recents", S("usurpation_ip_partagee", 1),)),
    Regle(33, detecter_verrouillage, ("recents",)),
    Regle(43, detecter_identite_en_rafale, ("recents", S("identite_rafale", 3),)),
    Regle(44, detecter_identite_apres_sortie, ("recents",)),
    Regle(48, detecter_comptes_meme_ip, ("recents", S("comptes_meme_ip", 3),)),
]
