"""Règles de corrélation — famille « données » : aspiration, exports, collecte de lectures sensibles."""
import re
from collections import defaultdict
from .base import (FENETRE_S, Regle, S, _par_uid)
from engine.i18n import tf as _siem_tf  # noqa: E402


# ── 3. Reconnaissance puis exfiltration ─────────────────────────────────────

def detecter_aspiration(tables_par_uid: dict, exports, seuil_tables=8, fenetre_s=FENETRE_S) -> list:
    """tables_par_uid : {uid: ensemble de tables ouvertes dans l'heure} ; exports : dicts {t, uid, ip, chemin}. Seuil volontairement
    inférieur à celui de profil_balayage_tables (15) : seul, il ne déclenche rien, corrélé à un export il compte."""
    alertes, vus = [], set()
    for e in sorted(exports, key=lambda x: x["t"]):
        tables = tables_par_uid.get(e["uid"], set())
        if len(tables) >= seuil_tables and e["uid"] not in vus:
            vus.add(e["uid"])
            alertes.append({"regle": "correlation_aspiration_donnees", "gravite": "haute", "uid": e["uid"], "ip": e["ip"], "ent": e.get("ent"),
                            "t": e["t"], "message": _siem_tf("rg4.siem.msg.151", v1=len(tables)),
                            "details": {"tables": sorted(tables)[:30], "export": e.get("chemin", "")}})
    return alertes


# ── 8. Exfiltration puis destruction ────────────────────────────────────────

def detecter_exfiltration_destruction(evts, seuil=5, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid(evts).items():
        for i, ex in enumerate(liste):
            if ex["kind"] != "export":
                continue
            sup = [s for s in liste[i + 1:] if s["kind"] in ("suppression", "membre_supprime") and (s["t"] - ex["t"]).total_seconds() <= fenetre_s]
            if len(sup) >= seuil:
                alertes.append({"regle": "correlation_exfiltration_destruction", "gravite": "critique", "uid": uid, "ip": sup[-1]["ip"],
                                "ent": sup[-1].get("ent"), "t": sup[-1]["t"], "message": _siem_tf("rg4.siem.msg.152", v1=len(sup)),
                                "details": {"export": ex.get("chemin", ""), "suppressions": len(sup)}})
                break
    return alertes


# ── 29. Collecte dans plusieurs familles de données ─────────────────────────

def _famille(chemin: str) -> str:
    segs = [x for x in chemin.split("/") if x]
    if not segs:
        return ""
    return "/".join(segs[:2]) if segs[0] == "api" and len(segs) > 1 else segs[0]


def detecter_collecte_multi_familles(evts, seuil=4, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid([e for e in evts if e["kind"] in ("lecture", "export")]).items():
        lectures = [e for e in liste if e["kind"] == "lecture"]
        for i, r in enumerate(lectures):
            groupe = [x for x in lectures[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            familles = {_famille(x["chemin"]) for x in groupe} - {""}
            if len(familles) >= seuil:
                export = any(e["kind"] == "export" and abs((e["t"] - r["t"]).total_seconds()) <= fenetre_s for e in liste)
                alertes.append({"regle": "correlation_collecte_multi_familles", "gravite": "haute" if export else "moyenne", "uid": uid, "ip": groupe[-1]["ip"],
                                "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.154", v1=len(familles), v2=fenetre_s // 60) + (_siem_tf("rg4.siem.msg.155") if export else ""),
                                "details": {"familles": sorted(familles)[:12]}, "dedup": 7200})
                break
    return alertes


# ── 39. Exports en rafale ───────────────────────────────────────────────────

def detecter_exports_en_rafale(evts, seuil=4, fenetre_s=FENETRE_S) -> list:
    alertes = []
    for uid, liste in _par_uid([e for e in evts if e["kind"] == "export"]).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            chemins = {x.get("chemin", "") for x in groupe} - {""}
            if len(chemins) >= seuil:
                alertes.append({"regle": "correlation_exports_en_rafale", "gravite": "haute" if len(chemins) >= 2 * seuil else "moyenne", "uid": uid, "ip": groupe[-1]["ip"],
                                "ent": groupe[-1].get("ent"), "t": groupe[-1]["t"], "message": _siem_tf("rg4.siem.msg.153", v1=len(chemins), v2=fenetre_s // 60),
                                "details": {"chemins": sorted(chemins)[:12]}, "dedup": 7200})
                break
    return alertes


# ── 45. Export inédit ───────────────────────────────────────────────────────

def _forme(chemin: str) -> str:
    return re.sub(r"\d+", "#", chemin)


def detecter_export_inedit(evts, historique, min_hist=5) -> list:
    connus, nb = defaultdict(set), defaultdict(int)
    for h in historique:
        if h["kind"] == "export":
            nb[h["uid"]] += 1
            connus[h["uid"]].add(_forme(h.get("chemin", "")))
    alertes, vus = [], set()
    for e in sorted((x for x in evts if x["kind"] == "export"), key=lambda x: x["t"]):
        uid = e["uid"]
        if uid in vus or nb[uid] < min_hist or _forme(e.get("chemin", "")) in connus[uid]:
            continue
        vus.add(uid)
        alertes.append({"regle": "correlation_export_inedit", "gravite": "moyenne", "uid": uid, "ip": e["ip"], "ent": e.get("ent"), "t": e["t"],
                        "message": _siem_tf("rg4.siem.msg.150", v1=nb[uid]),
                        "details": {"chemin": e.get("chemin", "")}, "dedup": 86400})
    return alertes


REGLES = [
    Regle(3, detecter_aspiration, ("tables", "exports", S("tables_correlation", 8),)),
    Regle(8, detecter_exfiltration_destruction, ("recents", S("suppressions_apres_export", 5),)),
    Regle(29, detecter_collecte_multi_familles, ("recents", S("familles_lues", 4),)),
    Regle(39, detecter_exports_en_rafale, ("recents", S("exports_rafale", 4),)),
    Regle(45, detecter_export_inedit, ("recents", "historique",)),
]
