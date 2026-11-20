"""Adaptateur base / cache : lit JournalAudit, RetraitWallet et le cache, construit le contexte, applique le registre et émet les alertes."""
import time
from collections import defaultdict
from datetime import timedelta
from .base import (FENETRE_S, HISTORIQUE_JOURS, PRIVILEGES, REVEIL_JOURS, SENSIBLES, classer)
from .continu import MoteurWallet
from .registre import calculer_alertes


# ── Adaptateur base / cache ─────────────────────────────────────────────────

def _reglages():
    from django.conf import settings
    return dict(getattr(settings, "GYPLATFORM_SIEM_CHEMINS", {}) or {})


def _normaliser(t, uid, ent, action, ip, type_acteur, resultat, details, extra):
    """Ligne de JournalAudit → événement, ou None si ni classé ni porté par une clé API."""
    details = details if isinstance(details, dict) else {}
    cle = type_acteur == "cle_api"
    if action.startswith("SIEM:lecture_sensible"):
        methode, chemin = "GET", str(details.get("chemin", ""))
    elif action.startswith("SIEM:refus"):
        statut, chemin = details.get("statut"), str(details.get("chemin", ""))[:120]
        return {"t": t, "uid": uid, "ent": ent, "ip": ip or details.get("ip", ""), "kind": "refus", "cle": cle, "chemin": chemin,
                "statut": int(statut) if str(statut).isdigit() else 0, "genre": classer(str(details.get("methode", "POST")), chemin, extra)}
    elif action.startswith("SIEM:"):
        return None
    else:
        methode, _, chemin = action.partition(" ")
        if resultat == "echec":
            return None
    kind = classer(methode, chemin, extra)
    if not kind and action.startswith("SIEM:lecture_sensible"):
        kind = "lecture"
    if not kind and not cle:
        return None
    return {"t": t, "uid": uid, "ent": ent, "ip": ip or details.get("ip", ""), "kind": kind, "cle": cle, "chemin": chemin[:120]}


def _lire(depuis, jusqu_a=None):
    from ..models import JournalAudit
    extra = _reglages()
    qs = JournalAudit.objects.filter(horodatage__gte=depuis, utilisateur__isnull=False)
    if jusqu_a is not None:
        qs = qs.filter(horodatage__lt=jusqu_a)
    sortie = []
    for t, uid, ent, action, ip, ta, res, det in qs.values_list("horodatage", "utilisateur_id", "entreprise_id", "action", "ip", "type_acteur",
                                                                  "resultat", "details").iterator(chunk_size=2000):
        e = _normaliser(t, uid, ent, action, ip, ta, res, det, extra)
        if e:
            sortie.append(e)
    return sortie


CLE_ETAT = "siem:continu:wallet"
REJEU_JOURS = 30        # reconstruction de l'état si le cache l'a perdu (l'état courant, lui, n'a aucune borne arrière)
ATTENTE_RETRAIT_S = 86400


def _ip_du_retrait(uid, t, extra) -> str:
    """IP de la ligne d'audit du retrait la plus proche (±2 min) du même compte."""
    from ..models import JournalAudit
    for ip, action, det in JournalAudit.objects.filter(utilisateur_id=uid, horodatage__gte=t - timedelta(seconds=120), horodatage__lte=t + timedelta(seconds=120)) \
            .values_list("ip", "action", "details")[:20]:
        methode, _, chemin = str(action).partition(" ")
        if classer(methode, chemin, extra) == "retrait":
            return ip or (det.get("ip", "") if isinstance(det, dict) else "")
    return ""


def _evenements_wallet(curseurs, depuis=None):
    """Événements neufs pour le moteur continu : audit (recharges, réglages, retraits vus par l'audit) et retraits effectués, triés par date.
    Renvoie (événements, curseurs mis à jour). Un retrait pas encore « effectué » reste en attente (24 h) au lieu d'être perdu derrière le curseur."""
    from django.utils import timezone
    from ..models import JournalAudit, RetraitWallet
    extra = _reglages()
    curseurs = dict(curseurs)
    evts, dernier_audit = [], curseurs.get("audit", 0)
    qs = JournalAudit.objects.filter(utilisateur__isnull=False, pk__gt=dernier_audit)
    if depuis is not None:
        qs = qs.filter(horodatage__gte=depuis)
    for pk, t, uid, ent, action, ip, ta, res, det in qs.order_by("pk").values_list("pk", "horodatage", "utilisateur_id", "entreprise_id", "action", "ip", "type_acteur",
                                                                                 "resultat", "details").iterator(chunk_size=2000):
        dernier_audit = pk
        e = _normaliser(t, uid, ent, action, ip, ta, res, det, extra)
        if e and e["kind"] in ("recharge", "config_wallet", "retrait"):
            if e["kind"] == "retrait":
                e["kind"] = "retrait_audit"
            evts.append(e)
    dernier_retrait, attente = curseurs.get("retrait", 0), set(curseurs.get("attente", []))
    nouveaux = RetraitWallet.objects.filter(pk__gt=dernier_retrait)
    if depuis is not None:
        nouveaux = nouveaux.filter(cree_le__gte=depuis)
    lignes = list(nouveaux.select_related("membre", "moyen").order_by("pk"))
    if attente:
        lignes += list(RetraitWallet.objects.filter(pk__in=attente).select_related("membre", "moyen"))
    limite = timezone.now() - timedelta(seconds=ATTENTE_RETRAIT_S)
    for r in lignes:
        if r.pk > dernier_retrait:
            dernier_retrait = r.pk
        if r.statut != "effectue":
            if r.cree_le >= limite:
                attente.add(r.pk)
            else:
                attente.discard(r.pk)
            continue
        attente.discard(r.pk)
        uid = r.membre.utilisateur_id
        if uid is None:
            continue
        evts.append({"t": r.cree_le, "uid": uid, "ent": r.entreprise_id if hasattr(r, "entreprise_id") else None, "kind": "retrait", "montant": float(r.montant),
                     "dest": r.numero_destination, "ip": _ip_du_retrait(uid, r.cree_le, extra), "moyen_maj": r.moyen.maj_le if r.moyen is not None else None})
    evts.sort(key=lambda e: e["t"])
    curseurs.update({"audit": dernier_audit, "retrait": dernier_retrait, "attente": sorted(attente)[-200:]})
    return evts, curseurs


def _profils_heures(uids, depuis, jusqu_a) -> dict:
    """{uid: {"heures": {heure: n}, "total": n}} de toute l'activité d'audit (même convention d'heure que les autres règles)."""
    from ..models import JournalAudit
    sortie = {u: {"heures": defaultdict(int), "total": 0} for u in uids}
    for uid, t in JournalAudit.objects.filter(utilisateur_id__in=uids, horodatage__gte=depuis, horodatage__lt=jusqu_a).order_by() \
            .values_list("utilisateur_id", "horodatage").iterator(chunk_size=5000):
        sortie[uid]["heures"][t.hour] += 1
        sortie[uid]["total"] += 1
    return sortie


def _tables_de_l_heure(uids) -> dict:
    """Tables ouvertes dans l'heure par compte, d'après les fenêtres de 10 min de siem_collecte (aucun suivi supplémentaire)."""
    from ..siem_cache import cache
    base = int(time.time() // 600)
    sortie = {}
    for uid in uids:
        lu = cache.get_many([f"siem:hab:tw:{uid}:{base - i}" for i in range(6)])
        vues = set()
        for v in lu.values():
            vues.update(v or ())
        sortie[uid] = vues
    return sortie


def construire_contexte(maintenant):
    """Lit la base et le cache pour la dernière heure et les 30 jours précédents ; None s'il n'y a eu aucun geste classé dans l'heure."""
    from django.db.models import Max, Min
    from ..models import JournalAudit
    debut = maintenant - timedelta(seconds=FENETRE_S)
    recents = _lire(debut)
    if not recents:
        return None
    uids = {e["uid"] for e in recents}
    historique = [h for h in _lire(maintenant - timedelta(days=HISTORIQUE_JOURS), debut) if h["uid"] in uids and h["kind"] != "refus"]
    exports = [e for e in recents if e["kind"] == "export"]
    creations = [e for e in recents + historique if e["kind"] == "cle_creee"]
    actifs = {e["uid"] for e in recents if e["kind"] in SENSIBLES | PRIVILEGES}
    reveils, derniere = {}, {}
    if actifs:
        coupe = maintenant - timedelta(days=REVEIL_JOURS)
        base = JournalAudit.objects.filter(utilisateur_id__in=actifs).order_by()
        reveils = dict(base.filter(horodatage__gte=coupe).values_list("utilisateur_id").annotate(m=Min("horodatage")))
        derniere = dict(base.filter(horodatage__lt=coupe).values_list("utilisateur_id").annotate(m=Max("horodatage")))
        premieres = dict(base.values_list("utilisateur_id").annotate(m=Min("horodatage")))
    else:
        premieres = {}
    sensibles_uids = {e["uid"] for e in recents if e["kind"] in SENSIBLES}
    profils = _profils_heures(sensibles_uids, maintenant - timedelta(days=HISTORIQUE_JOURS), debut) if sensibles_uids else {}
    return {"recents": recents, "historique": historique, "exports": exports, "creations": creations, "reveils": reveils, "derniere": derniere, "premieres": premieres,
            "profils": profils, "tables": _tables_de_l_heure({e["uid"] for e in exports})}


def _emettre(alertes, siem):
    """Même chemin pour les règles périodiques et continues : alerte, points de risque, réponse automatique."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    for a in alertes:
        u = User.objects.filter(pk=a["uid"]).first()
        if u is None:
            continue
        siem.alerter(a["regle"], a["gravite"], f"{u.get_username()} : {a['message']}", utilisateur=u, ip=a["ip"], entreprise_id=a.get("ent"),
                     details=a["details"], dedoublonner_s=a.get("dedup", 3600))
        points = {"critique": 60, "haute": 30, "moyenne": 10}.get(a["gravite"], 5)
        score = siem.ajouter_risque("compte", u.pk, points)
        siem.appliquer_reponse("compte", u.pk, score, utilisateur=u, ip=a["ip"], entreprise_id=a.get("ent"))


def surveiller_continu():
    """Moteur continu (wallet) : ne lit que les événements arrivés depuis le dernier passage et met à jour l'état conservé dans le cache.
    État perdu (premier passage, cache vidé) : les 30 derniers jours sont rejoués sans alerte pour le reconstruire."""
    from django.conf import settings
    from ..siem_cache import cache
    from django.utils import timezone
    from .. import siem_noyau as siem
    plafond = float(getattr(settings, "GYPLATFORM_SIEM_PLAFOND_RETRAIT", 0) or 0)
    moteur = MoteurWallet.depuis_json(cache.get(CLE_ETAT), siem.config()["seuils"], plafond)
    rejeu = "audit" not in moteur.etat["curseurs"]
    evts, curseurs = _evenements_wallet(moteur.etat["curseurs"], depuis=timezone.now() - timedelta(days=REJEU_JOURS) if rejeu else None)
    alertes = []
    for e in evts:
        a = moteur.observer(e)
        if not rejeu:
            alertes += a
    moteur.etat["curseurs"] = curseurs
    cache.set(CLE_ETAT, moteur.vers_json(), timeout=None)
    _emettre(alertes, siem)


def surveiller_correlations():
    """Étape du cycle (toutes les 60 s) : les règles périodiques sur la dernière heure, comparées aux 30 jours précédents, puis le moteur continu."""
    from django.utils import timezone
    from .. import siem_noyau as siem
    ctx = construire_contexte(timezone.now())
    if ctx is not None:
        _emettre(calculer_alertes(ctx, siem.config()["seuils"]), siem)
    surveiller_continu()
