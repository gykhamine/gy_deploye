"""Règles évaluées à chaque événement (échecs de connexion, refus), appelées par siem_collecte : le registre périodique ne les exécute pas."""
import re
import time
from .base import (_par_uid)
from engine.i18n import tf as _siem_tf  # noqa: E402


# ── 6. Succès après échecs ──────────────────────────────────────────────────

def detecter_succes_apres_echecs(echecs, ip, seuil=3):
    """echecs : [(t_epoch, ip)] des 30 dernières minutes pour ce nom d'utilisateur. Alerte seulement si les échecs viennent de l'IP qui réussit
    (un propriétaire légitime qui se connecte de chez lui pendant qu'un tiers essaie ailleurs n'est pas un signal)."""
    n = sum(1 for _t, i in echecs if i == ip)
    if not ip or n < seuil:
        return None
    return {"gravite": "critique" if n >= 3 * seuil else "haute", "message": _siem_tf("rg4.siem.msg.156", n=n),
            "details": {"echecs": n}}


# ── 7. Énumération d'identifiants ───────────────────────────────────────────
_RE_ID = re.compile(r"/(\d+)(?=/|$)")


def detecter_enumeration_ids(evts, seuil=10, fenetre_s=600) -> list:
    """Refus 403/404 sur des identifiants distincts d'une même ressource en 10 min : sondage d'objets qui ne sont pas à ce compte."""
    alertes = []
    for uid, liste in _par_uid([e for e in evts if e["kind"] == "refus" and e.get("statut") in (403, 404) and _RE_ID.search(e["chemin"])]).items():
        for i, r in enumerate(liste):
            ids = {(_RE_ID.sub("/<id>", s["chemin"]), _RE_ID.search(s["chemin"]).group(1))
                   for s in liste[i:] if (s["t"] - r["t"]).total_seconds() <= fenetre_s}
            if len(ids) >= seuil:
                alertes.append({"regle": "correlation_enumeration_ids", "gravite": "critique" if len(ids) >= 3 * seuil else "haute", "uid": uid,
                                "ip": r["ip"], "ent": r.get("ent"), "t": liste[-1]["t"],
                                "message": _siem_tf("rg4.siem.msg.157", v1=len(ids), v2=fenetre_s // 60),
                                "details": {"exemples": sorted(f"{g}#{n}" for g, n in ids)[:10]}})
                break
    return alertes


# ── 23. Bourrage distribué (temps réel) ─────────────────────────────────────

def attaque_distribuee(echecs, seuil=5) -> int:
    """echecs : [(t, ip)] sur un même nom d'utilisateur. Nombre d'IP distinctes si >= seuil, sinon 0."""
    n = len({i for _t, i in echecs if i})
    return n if n >= seuil else 0


def spraying(echecs, seuil=5) -> int:
    """echecs : [(t, nom)] depuis une même IP. Nombre de comptes distincts essayés si >= seuil, sinon 0."""
    n = len({nom for _t, nom in echecs if nom})
    return n if n >= seuil else 0


def noter_echec(nom: str, ip: str):
    """Appelé à chaque échec de connexion (siem_collecte) : mémoire courte des échecs par nom d'utilisateur ET par IP. Sert à juger le succès qui suit
    (règle 6) et à repérer le bourrage distribué (règle 23) : un compte attaqué depuis de nombreuses IP, ou une IP qui essaie de nombreux comptes."""
    from ..siem_cache import cache
    from .. import siem_noyau as siem
    maintenant = time.time()
    cle = f"siem:echecs:{nom}"
    liste = [e for e in (cache.get(cle) or []) if maintenant - e[0] <= 1800][-49:]
    liste.append((maintenant, ip))
    cache.set(cle, liste, timeout=1800)
    cle_ip = f"siem:echecs_ip:{ip}"
    par_ip = [e for e in (cache.get(cle_ip) or []) if maintenant - e[0] <= 1800][-99:]
    par_ip.append((maintenant, nom))
    cache.set(cle_ip, par_ip, timeout=1800)
    seuil = int(siem.config()["seuils"].get("bourrage_distribue", 5))
    n = attaque_distribuee(liste, seuil)
    if n:
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.filter(username__iexact=nom).first()
        siem.alerter("correlation_attaque_distribuee", "haute", _siem_tf("rg4.siem.msg.18", nom=nom, n=n), utilisateur=u, ip="",
                     details={"compte": nom, "ips": sorted({i for _t, i in liste})[:10]}, dedoublonner_s=1800)
        if u is not None:
            siem.appliquer_reponse("compte", u.pk, siem.ajouter_risque("compte", u.pk, 30), utilisateur=u, ip=ip)
    m = spraying(par_ip, seuil)
    if m:
        siem.alerter("correlation_spraying", "haute", _siem_tf("rg4.siem.msg.19", m=m, ip=ip), ip=ip,
                     details={"comptes": sorted({n_ for _t, n_ in par_ip})[:20]}, dedoublonner_s=1800)
        siem.appliquer_reponse("ip", ip, siem.ajouter_risque("ip", ip, 40), ip=ip)


def succes_apres_echecs(utilisateur, ip: str, entreprise_id=None):
    """Appelé à chaque connexion réussie (siem_collecte)."""
    from ..siem_cache import cache
    from .. import siem_noyau as siem
    cle = f"siem:echecs:{utilisateur.get_username().lower()}"
    echecs = [e for e in (cache.get(cle) or []) if time.time() - e[0] <= 1800]
    cache.delete(cle)
    r = detecter_succes_apres_echecs(echecs, ip, int(siem.config()["seuils"].get("echecs_avant_succes", 3)))
    if r:
        siem.alerter("correlation_succes_apres_echecs", r["gravite"], f"{utilisateur.get_username()} : {r['message']}", utilisateur=utilisateur, ip=ip,
                     entreprise_id=entreprise_id, details=r["details"], dedoublonner_s=1800)
        score = siem.ajouter_risque("compte", utilisateur.pk, 60 if r["gravite"] == "critique" else 30)
        siem.appliquer_reponse("compte", utilisateur.pk, score, utilisateur=utilisateur, ip=ip, entreprise_id=entreprise_id)


def noter_refus(utilisateur, ip: str, chemin: str, statut: int):
    """Appelé à chaque refus (siem_collecte), connecté ou non : mémoire des refus 403/404 par IP sur 10 min, évaluée tout de suite.
    Les visiteurs anonymes n'ont pas de ligne d'audit : c'est la seule voie qui les voit."""
    if statut not in (403, 404) or not ip or not _RE_ID.search(chemin):
        return
    from datetime import datetime
    from ..siem_cache import cache
    from .. import siem_noyau as siem
    cle, maintenant = f"siem:refus_ids:{ip}", time.time()
    liste = [r for r in (cache.get(cle) or []) if maintenant - r[0] <= 600][-199:]
    liste.append((maintenant, chemin[:120], statut))
    cache.set(cle, liste, timeout=660)
    evts = [{"t": datetime.fromtimestamp(t), "uid": ip, "ip": ip, "kind": "refus", "chemin": c, "statut": st, "ent": None} for t, c, st in liste]
    for a in detecter_enumeration_ids(evts, int(siem.config()["seuils"].get("ids_enumeres", 10))):
        siem.alerter(a["regle"], a["gravite"], (_siem_tf("rg4.siem.msg.245", v1=utilisateur.get_username()) if utilisateur is not None else _siem_tf("rg4.siem.msg.244")) + a["message"],
                     utilisateur=utilisateur, ip=ip, details=a["details"], dedoublonner_s=1800)
        score = siem.ajouter_risque("ip", ip, 30)
        siem.appliquer_reponse("ip", ip, score, utilisateur=utilisateur, ip=ip)
        if utilisateur is not None:
            siem.appliquer_reponse("compte", utilisateur.pk, siem.ajouter_risque("compte", utilisateur.pk, 30), utilisateur=utilisateur, ip=ip)
