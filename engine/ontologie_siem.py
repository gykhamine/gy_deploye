"""Couche « sécurité » de l'ontologie : d'où viennent les comptes et ce que le SIEM a levé sur eux.

Nœuds : ip_source (une adresse vue dans JournalAudit, avec son nombre d'événements), alerte_siem (une alerte inscrite dans la blockchain de
l'entreprise, type de transaction « siem_alerte »).
Relations : connecte_depuis (compte → IP, poids = événements), partage_ip (compte ↔ compte qui partagent une IP — la même règle que
siem_regles.ips_partagees), vise (alerte → compte), origine_alerte (IP → alerte).

Rien n'est inventé : ce sont des volumes OBSERVÉS dans l'audit et des alertes réellement émises. Réservé à l'administrateur de l'entreprise
(ou à la vue d'ensemble) : un agent ou un client ne voit jamais les adresses des autres comptes. Aucune IP interne n'est masquée : la page
d'ontologie reste réservée aux utilisateurs autorisés, comme le reste du graphe.
"""
import logging
from collections import defaultdict
from engine.i18n import tf as _siem_tf  # noqa: E402
from engine.i18n import tl as _siem_tl  # noqa: E402

logger = logging.getLogger(__name__)

MAX_EVENEMENTS = 5000      # lignes d'audit les plus récentes lues
MAX_ALERTES = 60           # alertes les plus récentes
MAX_IP = 80                # IP les plus actives affichées

TYPES_NOEUDS_SIEM = {
    "ip_source":   ("#ef4444", _siem_tl("rg4.siem.msg.217")),
    "alerte_siem": ("#b91c1c", _siem_tl("rg4.siem.msg.218")),
}
RELATIONS_SIEM = {
    "connecte_depuis": ("#f97316", _siem_tl("rg4.siem.msg.219")),
    "partage_ip":      ("#dc2626", _siem_tl("rg4.siem.msg.220")),
    "vise":            ("#b91c1c", _siem_tl("rg4.siem.msg.221")),
    "origine_alerte":  ("#c2410c", _siem_tl("rg4.siem.msg.222")),
}


def liens_siem(evenements, seuil_partage: int) -> dict:
    """Calcul pur (sans base). `evenements` : itérable de (utilisateur_id, ip). Retourne
    {"connecte_depuis": {(uid, ip): n}, "partage_ip": {(uid_a, uid_b): nombre d'IP communes}}."""
    from itertools import combinations
    vus, par_ip = defaultdict(int), defaultdict(set)
    for uid, ip in evenements:
        if uid and ip:
            vus[(uid, ip)] += 1
            par_ip[ip].add(uid)
    partage = defaultdict(int)
    for comptes in par_ip.values():
        if len(comptes) >= seuil_partage:
            for a, b in combinations(sorted(comptes)[:30], 2):
                partage[(a, b)] += 1
    return {"connecte_depuis": dict(vus), "partage_ip": dict(partage)}


def _noeud(membres, prefixe_role, uid):
    """Identifiant du nœud « personne » d'un utilisateur Django, ou None s'il n'est pas membre de l'entreprise."""
    m = membres.get(uid)
    return f"{prefixe_role.get(m.role, 'agent')}:{m.id}" if m is not None else None


def enrichir_siem(g, entreprise, racine, prefixe_role, membre=None):
    """Ajoute la couche sécurité au graphe. S'accroche uniquement aux nœuds « personne » déjà présents. Jamais bloquante."""
    if membre is not None and getattr(membre, "role", None) != "admin":
        return
    from .models import Bloc, JournalAudit, MembreEntreprise
    from . import siem_noyau as siem

    membres = {m.utilisateur_id: m for m in MembreEntreprise.objects.filter(entreprise=entreprise, utilisateur__isnull=False)}

    try:
        evenements = list(JournalAudit.objects.filter(entreprise=entreprise, utilisateur__isnull=False).exclude(ip="")
                          .order_by("-id").values_list("utilisateur_id", "ip")[:MAX_EVENEMENTS])
        liens = liens_siem(evenements, siem.config()["seuils"]["ips_partagees"])
        volume_ip = defaultdict(int)
        for (_uid, ip), n in liens["connecte_depuis"].items():
            volume_ip[ip] += n
        gardees = {ip for ip, _n in sorted(volume_ip.items(), key=lambda kv: -kv[1])[:MAX_IP]}
        for ip in gardees:
            g.add_node(f"ip:{ip}", type="ip_source", label=ip, evenements=volume_ip[ip])
        for (uid, ip), n in liens["connecte_depuis"].items():
            u = _noeud(membres, prefixe_role, uid)
            if ip in gardees and u is not None and g.has_node(u):
                g.add_edge(u, f"ip:{ip}", relation="connecte_depuis", poids=n)
        for (a, b), n in liens["partage_ip"].items():
            ua, ub = _noeud(membres, prefixe_role, a), _noeud(membres, prefixe_role, b)
            if ua and ub and g.has_node(ua) and g.has_node(ub) and not g.has_edge(ua, ub):
                g.add_edge(ua, ub, relation="partage_ip", poids=n)
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.223"))

    try:
        alertes = []
        for bloc in Bloc.objects.filter(entreprise=entreprise).order_by("-index")[:400]:
            for tx in bloc.transactions or []:
                if isinstance(tx, dict) and tx.get("type") == "siem_alerte":
                    alertes.append((bloc, tx))
        for bloc, tx in alertes[:MAX_ALERTES]:
            n = f"alerte_siem:{bloc.id}"
            g.add_node(n, type="alerte_siem", label=str(tx.get("action", "siem")).replace("siem:", ""), gravite=tx.get("statut"),
                       message=(tx.get("donnees") or {}).get("message", ""), bloc=bloc.index)
            u = _noeud(membres, prefixe_role, tx.get("utilisateur_id")) if tx.get("utilisateur_id") else None
            if u is not None and g.has_node(u):
                g.add_edge(n, u, relation="vise")
            ip = tx.get("ip")
            if ip and g.has_node(f"ip:{ip}"):
                g.add_edge(f"ip:{ip}", n, relation="origine_alerte")
    except Exception:
        logger.exception(_siem_tf("rg4.siem.msg.224"))


__all__ = ["TYPES_NOEUDS_SIEM", "RELATIONS_SIEM", "liens_siem", "enrichir_siem"]
