"""Règles de corrélation — famille « administration » : rôles, permissions, membres, réglages SIEM, effacement de traces."""
import re
from collections import defaultdict
from .base import (FENETRE_S, PRIVILEGES, Regle, S, SENSIBLES, _hors_heures, _par_uid, _profil, _rafale)
from engine.i18n import tf as _siem_tf  # noqa: E402


# ── 4. Élévation de privilèges ──────────────────────────────────────────────

def detecter_elevation(evts, historique) -> list:
    """Chaque action d'administration est comparée à l'historique du compte : jamais faite, ou faite à une heure absente de son profil."""
    alertes = []
    hist = _par_uid(historique)
    for e in sorted(evts, key=lambda x: x["t"]):
        if e["kind"] not in PRIVILEGES:
            continue
        h = hist.get(e["uid"], [])
        inedite = not any(x["kind"] == e["kind"] for x in h)
        hors = _hors_heures(_profil(h), e["t"].hour)
        if not (inedite or hors):
            continue
        raisons = ([_siem_tf("rg4.siem.msg.092")] if inedite else []) + ([_siem_tf("rg4.siem.msg.093", hour=e['t'].hour)] if hors else [])
        alertes.append({"regle": "correlation_elevation_privileges", "gravite": "haute" if inedite and hors else "moyenne", "uid": e["uid"],
                        "ip": e["ip"], "ent": e.get("ent"), "t": e["t"], "message": _siem_tf("rg4.siem.msg.242", v1=e['kind']) + ", ".join(raisons),
                        "details": {"action": e.get("chemin", ""), "genre": e["kind"]}})
    return alertes


# ── 9. Affaiblissement de la sécurité ───────────────────────────────────────
AFFAIBLISSEMENTS = {"siem_config", "2fa_off"}


def detecter_affaiblissement(evts, historique, fenetre_s=FENETRE_S) -> list:
    """Réglage SIEM ou 2FA coupé, suivi d'une action sensible ou d'administration : on baisse la garde avant d'agir. Plus grave si le compte ne l'avait jamais fait."""
    alertes = []
    deja = {(h["uid"], h["kind"]) for h in historique}
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] not in AFFAIBLISSEMENTS:
                continue
            suite = next((s for s in liste[i + 1:] if s["kind"] in SENSIBLES | PRIVILEGES and (s["t"] - a["t"]).total_seconds() <= fenetre_s), None)
            if suite:
                alertes.append({"regle": "correlation_affaiblissement_securite", "gravite": "haute" if (uid, a["kind"]) in deja else "critique", "uid": uid,
                                "ip": suite["ip"], "ent": suite.get("ent"), "t": suite["t"],
                                "message": _siem_tf("rg4.siem.msg.096", v1=a['kind'], v2=suite['kind'], v3=int((suite['t'] - a['t']).total_seconds() // 60)),
                                "details": {"declencheur": a.get("chemin", ""), "action": suite.get("chemin", "")}})
                break
    return alertes


def detecter_membres_en_masse(evts, seuil=5, fenetre_s=FENETRE_S) -> list:
    return [{"regle": "correlation_membres_en_masse", "gravite": "haute", "uid": uid, "ip": d["ip"], "ent": d.get("ent"), "t": d["t"],
             "message": _siem_tf("rg4.siem.msg.090", n=n, v2=fenetre_s // 60), "details": {"nb": n}}
            for uid, n, d in _rafale(evts, "membre_cree", seuil, fenetre_s)]


# ── 15. Tentative d'élévation ───────────────────────────────────────────────
ROUTES_PROTEGEES = SENSIBLES | PRIVILEGES | AFFAIBLISSEMENTS


def detecter_tentative_privilege(evts, seuil=3, fenetre_s=600) -> list:
    """Refus 403 répétés sur des routes d'administration ou sensibles : on essaie ce que le rôle n'autorise pas. Si la même famille d'actions réussit
    ensuite (rôle changé, permission élargie…), le contournement a abouti : critique."""
    alertes = []
    for uid, liste in _par_uid([e for e in evts if (e["kind"] == "refus" and e.get("statut") == 403 and e.get("genre") in ROUTES_PROTEGEES)
                                or e["kind"] in ROUTES_PROTEGEES]).items():
        refus = [e for e in liste if e["kind"] == "refus"]
        for i, r in enumerate(refus):
            groupe = [x for x in refus[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            if len(groupe) < seuil:
                continue
            abouti = next((e for e in liste if e["kind"] != "refus" and e["t"] >= r["t"] and (e["t"] - r["t"]).total_seconds() <= FENETRE_S), None)
            alertes.append({"regle": "correlation_tentative_privilege", "gravite": "critique" if abouti else "haute", "uid": uid, "ip": groupe[-1]["ip"],
                            "ent": groupe[-1].get("ent"), "t": (abouti or groupe[-1])["t"],
                            "message": _siem_tf("rg4.siem.msg.097", v1=len(groupe), v2=fenetre_s // 60) + (_siem_tf("rg4.siem.msg.104", v1=abouti['kind']) if abouti else ""),
                            "details": {"genres": sorted({x["genre"] for x in groupe}), "chemins": sorted({x["chemin"] for x in groupe})[:8]}})
            break
    return alertes


# ── 22. Droit accordé puis utilisé ──────────────────────────────────────────
DECLENCHEURS_ELEVATION = {"role", "permissions", "membre_cree", "cle_reactivee"}


def detecter_elevation_puis_usage(evts, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] not in DECLENCHEURS_ELEVATION:
                continue
            suite = next((x for x in liste[i + 1:] if x["kind"] in ("retrait", "export") and (x["t"] - a["t"]).total_seconds() <= fenetre_s), None)
            if suite:
                alertes.append({"regle": "correlation_elevation_puis_usage", "gravite": "haute", "uid": uid, "ip": suite["ip"], "ent": suite.get("ent"), "t": suite["t"],
                                "message": _siem_tf("rg4.siem.msg.098", v1=a['kind'], v2=suite['kind'], v3=int((suite['t'] - a['t']).total_seconds() // 60)),
                                "details": {"droit": a.get("chemin", ""), "usage": suite.get("chemin", "")}})
                break
    return alertes


# ── 26. Nettoyage après action ──────────────────────────────────────────────

def detecter_nettoyage(evts, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] not in ("retrait", "export"):
                continue
            suite = next((x for x in liste[i + 1:] if x["kind"] in ("cle_revoquee", "membre_supprime") and (x["t"] - a["t"]).total_seconds() <= fenetre_s), None)
            if suite:
                alertes.append({"regle": "correlation_nettoyage_apres_action", "gravite": "haute", "uid": uid, "ip": suite["ip"], "ent": suite.get("ent"), "t": suite["t"],
                                "message": _siem_tf("rg4.siem.msg.099", v1=a['kind'], v2=suite['kind'], v3=int((suite['t'] - a['t']).total_seconds() // 60)),
                                "details": {"action": a.get("chemin", ""), "nettoyage": suite.get("chemin", "")}})
                break
    return alertes


# ── 31. Auto-élévation ──────────────────────────────────────────────────────
_RE_CIBLE_ROLE = re.compile(r"/employes/(\d+)/role/")


def detecter_auto_elevation(evts) -> list:
    alertes, vus = [], set()
    for e in sorted((x for x in evts if x["kind"] == "role"), key=lambda x: x["t"]):
        m = _RE_CIBLE_ROLE.search(e.get("chemin", ""))
        if m and int(m.group(1)) == e["uid"] and e["uid"] not in vus:
            vus.add(e["uid"])
            alertes.append({"regle": "correlation_auto_elevation", "gravite": "critique", "uid": e["uid"], "ip": e["ip"], "ent": e.get("ent"), "t": e["t"],
                            "message": _siem_tf("rg4.siem.msg.094"), "details": {"action": e.get("chemin", "")}, "dedup": 86400})
    return alertes


# ── 32. Administrateur fantôme ──────────────────────────────────────────────

def detecter_admin_fantome(evts, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, a in enumerate(liste):
            if a["kind"] != "membre_cree":
                continue
            suite = next((x for x in liste[i + 1:] if x["kind"] in ("role", "permissions") and (x["t"] - a["t"]).total_seconds() <= fenetre_s), None)
            if suite:
                alertes.append({"regle": "correlation_administrateur_fantome", "gravite": "haute", "uid": uid, "ip": suite["ip"], "ent": suite.get("ent"), "t": suite["t"],
                                "message": _siem_tf("rg4.siem.msg.100", v1=suite['kind'], v2=int((suite['t'] - a['t']).total_seconds() // 60)),
                                "details": {"creation": a.get("chemin", ""), "droit": suite.get("chemin", "")}, "dedup": 7200})
                break
    return alertes


# ── 36. Purge de comptes ────────────────────────────────────────────────────
_RE_CIBLE_USER = re.compile(r"/employes/(\d+)/(?:role|supprimer)/")


def detecter_purge_comptes(evts, seuil=3, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid([e for e in evts if e["kind"] in ("role", "membre_supprime")]).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            cibles = set()
            for x in groupe:
                m = _RE_CIBLE_USER.search(x.get("chemin", ""))
                if m and int(m.group(1)) != uid:
                    cibles.add(int(m.group(1)))
            if len(cibles) >= seuil:
                alertes.append({"regle": "correlation_purge_comptes", "gravite": "critique" if len(cibles) >= 2 * seuil else "haute", "uid": uid, "ip": groupe[-1]["ip"],
                                "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.101", v1=len(cibles), v2=fenetre_s // 60),
                                "details": {"comptes": sorted(cibles)[:12]}, "dedup": 7200})
                break
    return alertes


# ── 37. Membre supprimé encore actif ────────────────────────────────────────

def detecter_supprime_encore_actif(evts, fenetre_s=FENETRE_S) -> list:
    alertes, vus = [], set()
    for a in sorted((e for e in evts if e["kind"] == "membre_supprime"), key=lambda x: x["t"]):
        m = _RE_CIBLE_USER.search(a.get("chemin", ""))
        if not m or int(m.group(1)) in vus:
            continue
        cible = int(m.group(1))
        suite = [x for x in evts if x["uid"] == cible and x["kind"] != "refus" and 0 < (x["t"] - a["t"]).total_seconds() <= fenetre_s]
        if suite:
            vus.add(cible)
            grave = any(x["kind"] in SENSIBLES | PRIVILEGES or x["cle"] for x in suite)
            dernier = suite[-1]
            alertes.append({"regle": "correlation_supprime_encore_actif", "gravite": "critique" if grave else "haute", "uid": cible, "ip": dernier["ip"], "ent": dernier.get("ent"),
                            "t": dernier["t"], "message": _siem_tf("rg4.siem.msg.095", v1=a['uid'], v2=len(suite), v3=dernier['kind'] or _siem_tf("rg4.siem.msg.107")),
                            "details": {"supprime_par": a["uid"], "geste": dernier.get("chemin", "")}, "dedup": 86400})
    return alertes


# ── 38. Droit prêté ─────────────────────────────────────────────────────────

def detecter_droit_pret(evts, fenetre_s=FENETRE_S) -> list:
    par_cible = defaultdict(list)
    for e in sorted((x for x in evts if x["kind"] == "role"), key=lambda x: x["t"]):
        m = _RE_CIBLE_ROLE.search(e.get("chemin", ""))
        if m:
            par_cible[int(m.group(1))].append(e)
    alertes = []
    for cible, roles in par_cible.items():
        trouve = False
        for i, r1 in enumerate(roles):
            for r2 in roles[i + 1:]:
                if (r2["t"] - r1["t"]).total_seconds() > fenetre_s:
                    break
                usage = next((x for x in evts if x["uid"] == cible and x["kind"] in SENSIBLES | PRIVILEGES and r1["t"] < x["t"] < r2["t"]), None)
                if usage:
                    alertes.append({"regle": "correlation_droit_pret", "gravite": "haute", "uid": cible, "ip": usage["ip"], "ent": usage.get("ent"), "t": r2["t"],
                                    "message": _siem_tf("rg4.siem.msg.105", v1=usage['kind'], v2=int((r2['t'] - r1['t']).total_seconds() // 60)),
                                    "details": {"accorde_par": r1["uid"], "retire_par": r2["uid"], "usage": usage.get("chemin", "")}, "dedup": 86400})
                    trouve = True
                    break
            if trouve:
                break
    return alertes


# ── 40. Accumulation d'accès durables ───────────────────────────────────────
ACCES_DURABLES = {"cle_creee", "membre_cree", "role", "permissions", "cle_reactivee"}


def detecter_accumulation_acces(evts, seuil=3, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid([e for e in evts if e["kind"] in ACCES_DURABLES]).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            genres = {x["kind"] for x in groupe}
            if len(genres) >= seuil:
                alertes.append({"regle": "correlation_accumulation_acces", "gravite": "critique" if len(genres) >= seuil + 2 else "haute", "uid": uid, "ip": groupe[-1]["ip"],
                                "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"], "message": _siem_tf("rg4.siem.msg.102", v1=len(genres), v2=fenetre_s // 60, v3=', '.join(sorted(genres))),
                                "details": {"genres": sorted(genres)}, "dedup": 7200})
                break
    return alertes


# ── 46. Aveuglement du SIEM ─────────────────────────────────────────────────
_RE_REGLAGE_SIEM = re.compile(r"^/api/siem/(config|lockdown|rebaser)")


def detecter_aveuglement_siem(evts, seuil=3, fenetre_s=FENETRE_S) -> list:
    reglages = [e for e in evts if e["kind"] == "siem_config" and _RE_REGLAGE_SIEM.search(e.get("chemin", ""))]
    return [{"regle": "correlation_aveuglement_siem", "gravite": "critique" if n >= 2 * seuil else "haute", "uid": uid, "ip": d["ip"], "ent": d.get("ent"), "t": d["t"],
             "message": _siem_tf("rg4.siem.msg.091", n=n, v2=fenetre_s // 60), "details": {"nb": n}, "dedup": 7200}
            for uid, n, d in _rafale(reglages, "siem_config", seuil, fenetre_s)]


# ── 49. Bénéficiaire actif ──────────────────────────────────────────────────

def detecter_beneficiaire_actif(evts, fenetre_s=FENETRE_S) -> list:
    alertes, vus = [], set()
    for a in sorted((e for e in evts if e["kind"] == "role"), key=lambda x: x["t"]):
        m = _RE_CIBLE_ROLE.search(a.get("chemin", ""))
        if not m or int(m.group(1)) == a["uid"] or int(m.group(1)) in vus:
            continue
        cible = int(m.group(1))
        suite = next((x for x in evts if x["uid"] == cible and x["kind"] in ("retrait", "export") and 0 < (x["t"] - a["t"]).total_seconds() <= fenetre_s), None)
        if suite:
            vus.add(cible)
            meme_ip = bool(a["ip"]) and suite["ip"] == a["ip"]
            alertes.append({"regle": "correlation_beneficiaire_actif", "gravite": "haute" if meme_ip else "moyenne", "uid": cible, "ip": suite["ip"], "ent": suite.get("ent"), "t": suite["t"],
                            "message": _siem_tf("rg4.siem.msg.103", v1=a['uid'], v2=suite['kind'], v3=int((suite['t'] - a['t']).total_seconds() // 60)) + (_siem_tf("rg4.siem.msg.106") if meme_ip else ""),
                            "details": {"accorde_par": a["uid"], "usage": suite.get("chemin", "")}, "dedup": 86400})
    return alertes


REGLES = [
    Regle(4, detecter_elevation, ("recents", "historique",)),
    Regle(9, detecter_affaiblissement, ("recents", "historique",)),
    Regle(13, detecter_membres_en_masse, ("recents", S("membres_en_masse", 5),)),
    Regle(15, detecter_tentative_privilege, ("recents", S("refus_privilege", 3),)),
    Regle(22, detecter_elevation_puis_usage, ("recents",)),
    Regle(26, detecter_nettoyage, ("recents",)),
    Regle(31, detecter_auto_elevation, ("recents",)),
    Regle(32, detecter_admin_fantome, ("recents",)),
    Regle(36, detecter_purge_comptes, ("recents", S("purge_cibles", 3),)),
    Regle(37, detecter_supprime_encore_actif, ("recents",)),
    Regle(38, detecter_droit_pret, ("recents",)),
    Regle(40, detecter_accumulation_acces, ("recents", S("acces_distincts", 3),)),
    Regle(46, detecter_aveuglement_siem, ("recents", S("reglages_siem_rafale", 3),)),
    Regle(49, detecter_beneficiaire_actif, ("recents",)),
]
