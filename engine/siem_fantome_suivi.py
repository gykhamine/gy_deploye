"""Suivi du modèle fantôme du SIEM : lit la base, mesure, apprend et alerte (étape « fantome » du cycle de siem_regles, une fois par minute).

Rien de nouveau à surveiller : le modèle réutilise ce qui existe (JournalAudit, messagerie, santé, alertes des 50 règles dans la blockchain système)
et ses alertes passent par siem.alerter() (preuve inscrite, incident, cloche) comme celles des règles.

  PROFESSEUR  les règles de base : un compte ou un message de quelqu'un sur qui une règle vient de sonner vaut « suspect » (étiquette faible)
  JUGE        l'humain : AvisMessage (bon / mauvais, côté messagerie) et compte déverrouillé à tort (étiquette forte)
  DÉTECTION   le réseau juge ensuite seul ; un message suspect est signalé à ses lecteurs (cache du SIEM), son expéditeur alerté

Quatre garde-fous (siem_fantome_juge.py, siem_fantome_texte.py) :
  DÉMARRAGE   professeur de FORME pour les messages (liens, doublons, contournements…) dès le premier jour, et `prechauffer()` qui rejoue l'historique
  SEUILS      calibrés sur les avis et sur les scores récents, jamais sous 0,5 ; le réglage de l'administrateur est le point de départ
  BUDGET      plafond d'alertes par heure et par tête ; le surplus est retenu et compté sur la page SIEM
  VOTANTS     un avis pèse moins quand son auteur en donne beaucoup, ou contredit les autres lecteurs ; un expéditeur n'est « mal noté » que par
              plusieurs votants distincts

Les alertes du modèle lui-même (fantome_*) ne servent jamais d'étiquette : il ne s'apprend pas à lui-même. Le texte d'un message n'est jamais
conservé ni journalisé : seulement des mesures de forme et des mots hachés (siem_fantome.hacher).
"""
import logging
import os
import time
from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from . import siem_fantome as sf
from . import siem_fantome_juge as juge
from . import siem_noyau as siem
from .siem_cache import cache
from .stockage import dossier_systeme
from engine.i18n import tf as _siem_tf  # noqa: E402

journal = logging.getLogger("engine.siem")
FICHIER = "siem_fantome.agentgy"
MAX_AVIS, MAX_MESSAGES, MAX_COMPTES, MAX_SCORES, GARDE_PAGE = 200, 200, 150, 600, 30
FENETRE_S, DUREE_SUSPECT_S = 600, 30 * 86400
_CLE_SUSPECT, _CLE_RETOURS = "siem:fantome:msg:{}", "siem:fantome:retours"
_INCIDENT = {"calme": 0.0, "suspect": 0.5, "attaque": 1.0}


# ── Fichier ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _chemin() -> str:
    return str(dossier_systeme(FICHIER))


def charger(reagir: bool = True) -> sf.Cerveau:
    """Le modèle appris, ou un modèle neuf (silencieux) s'il n'existe pas. Un fichier altéré est REFUSÉ : conservé à côté, alerte critique
    (`reagir=False` pour une simple lecture, sans effet de bord)."""
    chemin = _chemin()
    if not os.path.exists(chemin):
        return sf.Cerveau()
    try:
        return sf.lire(chemin, siem.toutes_les_cles())
    except (sf.FichierAltere, OSError) as e:
        if reagir:
            try:
                os.replace(chemin, f"{chemin}.altere_{int(time.time())}")
            except OSError:
                pass
            siem.alerter("fantome_altere", "critique", _siem_tf("rg4.siem.msg.23", e=e),
                         dedoublonner_s=3600)
        return sf.Cerveau()


def sauver(c: sf.Cerveau) -> None:
    sf.ecrire(_chemin(), c, siem.cle_courante()[1])


# ── Réglages, seuils calibrés, budget, registre des votants ─────────────────────────────────────────────────────────────
def _reglage(seuils: dict, cle: str):
    return seuils.get(cle, siem.CONFIG_DEFAUT["seuils"][cle])


def _registre(c, seuils: dict) -> juge.RegistreVotants:
    """Registre des votants : son état vit dans c.meta (fichier signé du modèle), il est modifié en place."""
    return juge.RegistreVotants(c.meta.setdefault("votants", {}), quota_jour=int(_reglage(seuils, "fantome_quota_votant")),
                                min_votants=int(_reglage(seuils, "fantome_votants_min")))


def _seuil(c, nom: str, plancher, seuils: dict) -> float:
    """Seuil d'alerte d'une tête : le réglage de l'administrateur tant qu'on ne sait rien, puis le seuil calibré (avis, quantile des scores)."""
    plancher = float(plancher)
    etiquettes = c.meta.get("etiquettes", {}).get(nom, [])
    if not int(_reglage(seuils, "fantome_calibrage")):
        c.meta.setdefault("calibrage", {})[nom] = {"seuil": plancher, "source": "reglage", "reglage": plancher, "etiquettes": len(etiquettes)}
        return plancher
    seuil, source = juge.seuil_calibre(plancher, c.meta.get("scores", {}).get(nom, []), etiquettes)
    c.meta.setdefault("calibrage", {})[nom] = {"seuil": seuil, "source": source, "reglage": plancher, "etiquettes": len(etiquettes)}
    return seuil


def _budget_libre(c, nom: str, seuils: dict) -> bool:
    """Reste-t-il de la place pour une alerte de cette tête dans l'heure ? Sinon l'alerte est retenue (comptée, visible sur la page SIEM)."""
    maximum = int(_reglage(seuils, "fantome_budget_" + nom))
    libre, _historique = juge.budget_ok(c.meta.setdefault("budget", {}).get(nom, []), time.time(), maximum)
    if not libre:   # compteur sur 24 h glissantes, montré sur la page SIEM
        r = c.meta.setdefault("retenus", {}).setdefault(nom, {"n": 0, "depuis": time.time()})
        if time.time() - r["depuis"] > 86400:
            r["n"], r["depuis"] = 0, time.time()
        r["n"] += 1
    return libre


def _budget_noter(c, nom: str) -> None:
    """Une alerte est réellement partie : elle compte dans le budget de l'heure."""
    _libre, historique = juge.budget_ok(c.meta.setdefault("budget", {}).get(nom, []), time.time(), 0)
    c.meta["budget"][nom] = historique


# ── Ce que les règles ont vu (professeur) et ce que l'humain a jugé (juge) ────────────────────────────────────────────────
def _historique_alertes(depuis_ts: float, limite: int = 600) -> list:
    """[(horodatage, utilisateur_id (None = sans compte), gravité 0..1), ...] des alertes des RÈGLES depuis `depuis_ts` (jamais celles du modèle)."""
    sortie = []
    for b, tx in siem._transactions_siem("siem_alerte", limite=limite):
        ts = tx.get("ts") or b.timestamp
        if ts < depuis_ts:
            break
        d = tx.get("details") or {}
        if str(d.get("regle", "")).startswith(("fantome_", "etape_")):
            continue
        sortie.append((ts, d.get("cible_utilisateur"), sf.GRAVITE.get(d.get("gravite"), 0.5)))
    return sortie


def _alertes_recentes(depuis_ts: float) -> dict:
    """{utilisateur_id (None = sans compte) : gravité max 0..1} des alertes des RÈGLES depuis `depuis_ts` (jamais celles du modèle)."""
    sortie = {}
    for _ts, uid, grav in _historique_alertes(depuis_ts):
        sortie[uid] = max(sortie.get(uid, 0.0), grav)
    return sortie


def retour_humain(sujet: str, cle, valeur: float) -> None:
    """Jugement de l'humain sur un compte (0 = c'était normal : verrou levé à tort). Appris au prochain passage, avec le poids fort."""
    try:
        file = cache.get(_CLE_RETOURS) or []
        cache.set(_CLE_RETOURS, (file + [{"sujet": sujet, "cle": int(cle), "valeur": float(valeur)}])[-200:], timeout=None)
    except Exception:
        journal.exception(_siem_tf("rg4.siem.msg.248"))


# ── Messages ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _avis_expediteur(uid, sans_message=None, registre=None) -> tuple:
    """(part d'avis négatifs sur ses messages, poids des avis positifs). Chaque VOTANT compte une fois, pesé par sa confiance ; sous `min_votants`
    votants distincts la part vaut 0 : un seul compte, même à cent 👎, ne fait pas passer quelqu'un pour mal noté. `sans_message` : le message qu'on
    est en train d'apprendre, dont l'avis est justement l'étiquette : le compter ferait copier l'étiquette au réseau."""
    from .models import AvisMessage
    votes = list(AvisMessage.objects.filter(message__expediteur__utilisateur_id=uid, message__auto=False).exclude(message_id=sans_message)
                 .values_list("membre_id", "valeur")[:1000])
    return juge.part_negative(votes, registre if registre is not None else juge.RegistreVotants())


def mesures_message(msg, uid, date_utilisateur, registre=None) -> dict:
    """Mesures d'un message, vues de son expéditeur à l'instant de l'envoi : forme du texte, rafale, doublons, ancienneté, avis reçus."""
    from .models import Message
    mesures = sf.caracteristiques_texte(msg.texte)
    mesures["fichier"] = 1 if msg.fichier else 0
    avant = Message.objects.filter(expediteur__utilisateur_id=uid, auto=False, supprime=False, cree_le__lte=msg.cree_le,
                                   cree_le__gt=msg.cree_le - timedelta(hours=1)).order_by("-id")[:150]
    liste = [(x.id, x.conversation_id, x.cree_le, x.texte) for x in avant]
    empreinte = sf.empreinte_texte(msg.texte)
    mesures["rafale_expediteur"] = sum(1 for _i, _c, quand, _t in liste if (msg.cree_le - quand).total_seconds() <= 600)
    mesures["conversations_heure"] = len({c for _i, c, _q, _t in liste})
    mesures["doublons_heure"] = sum(1 for i, _c, _q, texte in liste if i != msg.id and texte and sf.empreinte_texte(texte) == empreinte)
    mesures["age_expediteur_j"] = max(0.0, (msg.cree_le - date_utilisateur).total_seconds() / 86400) if date_utilisateur else 0.0
    mesures["p_avis_negatifs_expediteur"], mesures["avis_positifs_expediteur"] = _avis_expediteur(uid, sans_message=msg.id, registre=registre)
    return mesures


def suspects(ids) -> set:
    """Messages signalés par le modèle (pour la messagerie : un avertissement discret chez le lecteur)."""
    ids = list(ids)
    trouves = cache.get_many([_CLE_SUSPECT.format(i) for i in ids]) if ids else {}
    return {i for i in ids if _CLE_SUSPECT.format(i) in trouves}


def _apprendre_avis(c, curseurs, seuils, limite=MAX_AVIS) -> int:
    """Apprend les avis nouveaux (le juge). Rend le nombre d'avis examinés. Un avis pèse : la confiance de son auteur × le quota du jour ÷ √(rang de
    l'avis pour cet expéditeur) ; l'auteur est ensuite jugé sur son accord avec les AUTRES lecteurs du même message (siem_fantome_juge)."""
    from .models import AvisMessage
    tete, registre, examines = c.tetes["message"], _registre(c, seuils), 0
    for a in AvisMessage.objects.filter(id__gt=curseurs.get("avis", 0)).select_related("message__expediteur__utilisateur").order_by("id")[:limite]:
        examines += 1
        curseurs["avis"] = a.id
        msg = a.message
        if msg.supprime or not msg.texte or msg.expediteur is None:
            continue
        poids = registre.poids(a.membre_id, msg.expediteur_id, timezone.localdate(a.cree_le).isoformat())
        autres = list(AvisMessage.objects.filter(message_id=a.message_id).exclude(membre_id=a.membre_id).values_list("valeur", flat=True))
        registre.juger(a.membre_id, juge.accord_consensus(a.valeur, autres))
        if poids < 0.05:   # votant sans crédit (réputation basse) ou déjà très écouté sur cet expéditeur : l'avis est jugé, il n'enseigne plus
            continue
        u = msg.expediteur.utilisateur
        x = tete.vecteur(mesures_message(msg, u.pk, u.date_joined, registre), msg.texte)
        y = 1.0 if a.valeur < 0 else 0.0   # le juge : mauvais = 1, bon = 0
        juge.noter_etiquette(c.meta, "message", tete.predire(x), y)
        tete.apprendre(x, y, poids, fort=True)
    registre.elaguer()
    return examines


def _passer_messages(c, curseurs, alertes, seuils, maintenant):
    from .models import Message
    tete, registre = c.tetes["message"], _registre(c, seuils)
    seuil = _seuil(c, "message", seuils["fantome_seuil_message"], seuils)
    if "message" not in curseurs:   # premier passage : pas de rattrapage de tout l'historique
        dernier = Message.objects.order_by("-id").values_list("id", flat=True).first() or 0
        curseurs["message"] = max(0, dernier - MAX_MESSAGES)
    scores = c.meta.setdefault("scores_msg", {})
    nouveaux = Message.objects.filter(id__gt=curseurs["message"], auto=False, supprime=False).exclude(texte="") \
        .select_related("expediteur__utilisateur", "conversation").order_by("id")[:MAX_MESSAGES]
    vus = {}
    for msg in nouveaux:
        curseurs["message"] = msg.id
        if msg.expediteur is None:
            continue
        u = msg.expediteur.utilisateur
        mesures = mesures_message(msg, u.pk, u.date_joined, registre)
        x = tete.vecteur(mesures, msg.texte)
        score = tete.predire(x)
        tete.observer(mesures, score)
        juge.noter_score(c.meta, "message", score)
        scores[str(msg.id)] = round(score, 3)
        # deux professeurs : les règles de compte (une règle vient de sonner sur cet expéditeur) et la forme du message (liens, doublons, contournements)
        appris = juge.etiquette_apprentissage(sf.etiquette_faible_message(mesures), alertes.get(u.pk, 0.0), msg.id % 4 == 0)
        if appris:   # les messages calmes sont sous-échantillonnés : ils ne noient pas les rares signaux
            tete.apprendre(x, appris[0], appris[1])
        if score >= seuil and tete.mature(int(seuils["fantome_maturite"]), fort=int(_reglage(seuils, "fantome_avis_min")),
                                          classe=int(_reglage(seuils, "fantome_classe_min"))):
            cache.set(_CLE_SUSPECT.format(msg.id), round(score, 3), timeout=DUREE_SUSPECT_S)   # l'avertissement au lecteur n'est pas plafonné
            retenu = not _budget_libre(c, "message", seuils)
            vus[str(msg.id)] = {"score": round(score, 3), "expediteur": u.get_username(), "t": time.time(), "raisons": tete.raisons(mesures),
                                "retenu": retenu}
            if retenu:
                continue
            if siem.alerter("fantome_message", "moyenne" if score < 0.9 else "haute",
                            _siem_tf("rg4.siem.msg.21", v1=u.get_username(), score=score, id=msg.id),
                            utilisateur=u, entreprise_id=msg.conversation.entreprise_id, dedoublonner_s=3600,
                            details={"message_id": msg.id, "score": round(score, 3), "raisons": tete.raisons(mesures)}):
                _budget_noter(c, "message")
    for k in sorted(scores, key=int)[:-MAX_SCORES]:
        del scores[k]
    c.meta["derniers"]["message"].update(vus)


# ── Comptes ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def mesures_compte(uid, u, lignes, passe, maintenant, scores, registre=None) -> dict:
    """Mesures brutes d'un compte : gestes de la dernière heure (`lignes` = (action, ip, horodatage)), historique de 30 jours (`passe`),
    et ce que sa messagerie dit de lui (scores déjà jugés de ses messages, avis négatifs reçus)."""
    from .models import Message
    from . import siem_regles
    from .siem_corr.base import PRIVILEGES, SENSIBLES, _RE_TABLE, classer
    recents = [(a, ip) for a, ip, quand in lignes if (maintenant - quand).total_seconds() <= FENETRE_S]
    genres = [classer(*a.partition(" ")[::2]) for a, _ip, _q in lignes if not a.startswith("SIEM:")]
    profil = siem_regles.profil_comportement(passe)
    total = profil["total"]
    ips_recentes = {ip for _a, ip in recents if ip}
    heure = timezone.localtime(maintenant).hour
    jours = list(profil["par_jour"].values())
    aujourdhui = sum(1 for _a, _i, quand in lignes if timezone.localdate(quand) == timezone.localdate(maintenant))
    m = {"gestes_10min": len(recents), "tables_distinctes": len({t.group(1) for a, _ip in recents for t in [_RE_TABLE.search(a.partition(" ")[2])] if t}),
         "sensibles_heure": sum(g in SENSIBLES for g in genres), "administration_heure": sum(g in PRIVILEGES for g in genres),
         "exports_heure": genres.count("export"), "retraits_heure": genres.count("retrait"),
         "refus_heure": sum(1 for a, _i, _q in lignes if a.startswith("SIEM:refus")), "ips_heure": len({ip for _a, ip, _q in lignes if ip}),
         "p_ip_nouvelle": (len(ips_recentes - set(profil["ips"])) / len(ips_recentes)) if total >= 20 and ips_recentes else 0.0,
         "p_heure_rare": 1.0 if total >= 50 and profil["heures"].get(heure, 0) / total < 0.02 else 0.0,
         "volume_jour_x": aujourdhui / (sum(jours) / len(jours)) if len(jours) >= 5 and sum(jours) else 0.0,
         "age_compte_j": max(0.0, (maintenant - u.date_joined).total_seconds() / 86400)}
    envoyes = list(Message.objects.filter(expediteur__utilisateur_id=uid, auto=False, cree_le__gte=maintenant - timedelta(hours=1)).values_list("id", flat=True)[:200])
    notes = [scores[str(i)] for i in envoyes if str(i) in scores]
    m["messages_heure"], m["p_message_suspect_max"], m["p_message_suspect_moy"] = len(envoyes), max(notes, default=0.0), sum(notes) / len(notes) if notes else 0.0
    m["p_avis_negatifs_recus"] = _avis_expediteur(uid, registre=registre)[0]
    return m


def _passer_comptes(c, retours, alertes, seuils, maintenant):
    from django.contrib.auth import get_user_model
    from .models import JournalAudit
    tete, scores, vus = c.tetes["compte"], c.meta.get("scores_msg", {}), {}
    registre, seuil = _registre(c, seuils), _seuil(c, "compte", seuils["fantome_seuil"], seuils)
    User = get_user_model()
    actifs = list(JournalAudit.objects.filter(horodatage__gte=maintenant - timedelta(seconds=FENETRE_S), utilisateur__isnull=False)
                  .exclude(action__startswith="SIEM:").values_list("utilisateur_id", flat=True).distinct()[:MAX_COMPTES])
    cibles = {uid: None for uid in actifs}
    cibles.update({r["cle"]: r["valeur"] for r in retours if r["sujet"] == "compte"})   # un retour humain force l'examen du compte, avec SON étiquette
    for u in User.objects.filter(pk__in=list(cibles)):
        lignes = list(JournalAudit.objects.filter(utilisateur=u, horodatage__gte=maintenant - timedelta(hours=1)).order_by("-id")
                      .values_list("action", "ip", "horodatage")[:2000])
        passe = list(JournalAudit.objects.filter(utilisateur=u, horodatage__gte=maintenant - timedelta(days=30), horodatage__lt=maintenant - timedelta(hours=1))
                     .exclude(ip="").values_list("horodatage", "ip")[:20000])
        mesures = mesures_compte(u.pk, u, lignes, passe, maintenant, scores, registre)
        x = tete.vecteur(mesures)
        score = tete.predire(x)
        tete.observer(mesures, score)
        juge.noter_score(c.meta, "compte", score)
        if cibles[u.pk] is not None:
            juge.noter_etiquette(c.meta, "compte", score, cibles[u.pk])
            tete.apprendre(x, cibles[u.pk], 1.0, fort=True)   # le juge
            continue
        grav = alertes.get(u.pk, 0.0)   # le professeur
        if grav or u.pk % 2 == 0:
            tete.apprendre(x, grav, 0.5 if grav else 0.1)
        if score >= 0.3:
            vus[str(u.pk)] = {"nom": u.get_username(), "score": round(score, 3), "t": time.time(), "raisons": tete.raisons(mesures)}
        if score >= seuil and not grav and tete.mature(int(seuils["fantome_maturite"]), classe=int(_reglage(seuils, "fantome_classe_min"))):   # une règle a déjà sonné : pas de doublon
            if not _budget_libre(c, "compte", seuils):
                if str(u.pk) in vus:
                    vus[str(u.pk)]["retenu"] = True
                continue
            ip = next((i for _a, i, _q in lignes if i), "")
            ent = JournalAudit.objects.filter(utilisateur=u).order_by("-id").values_list("entreprise_id", flat=True).first()
            raisons = tete.raisons(mesures)
            if siem.alerter("fantome_compte", "moyenne" if score < 0.9 else "haute",
                            _siem_tf("rg4.siem.msg.22", v1=u.get_username(), score=score)
                            + ", ".join(r["mesure"] for r in raisons), utilisateur=u, ip=ip, entreprise_id=ent, dedoublonner_s=3600,
                            details={"score": round(score, 3), "raisons": raisons}):
                _budget_noter(c, "compte")
                siem.appliquer_reponse("compte", u.pk, siem.ajouter_risque("compte", u.pk, 15), utilisateur=u, ip=ip, entreprise_id=ent)
    c.meta["derniers"]["compte"].update(vus)


# ── Système ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def mesures_systeme(maintenant) -> dict:
    from . import siem_sante
    from .models import JournalAudit
    s = siem_sante.collecter()
    caches = s["caches"] if isinstance(s.get("caches"), list) else []
    plein = [x["entrees"] / x["maximum"] for x in caches if x.get("entrees") and x.get("maximum")]
    battements = [maintenant.timestamp() - v for v in siem.derniers_battements().values() if v]
    return {"base_ms": (s["base"] or {}).get("ms") or 0, "cache_ms": max([x.get("ms") or 0 for x in caches], default=0),
            "redis_ms": (s["redis"] or {}).get("ms") or 0, "erreurs": len((s["erreurs"] or {}).get("recentes", [])),
            "collecteur_retard_s": max(battements, default=0), "p_cache_plein": max(plein, default=0.0),
            "parquet_deltas": (s["parquet"] or {}).get("deltas") or 0,
            "refus_5min": JournalAudit.objects.filter(action__startswith="SIEM:refus", horodatage__gte=maintenant - timedelta(minutes=5)).count()}


def _passer_systeme(c, seuils, maintenant):
    from . import siem_incident
    tete = c.tetes["systeme"]
    mesures = mesures_systeme(maintenant)
    x = tete.vecteur(mesures)
    score = tete.predire(x)
    tete.observer(mesures, score)
    juge.noter_score(c.meta, "systeme", score)
    seuil = _seuil(c, "systeme", seuils["fantome_seuil"], seuils)
    tete.apprendre(x, _INCIDENT.get(siem_incident.etat()["etat"], 0.0), 0.3)   # le professeur : l'état d'incident que les règles ont fini par déclarer
    raisons = tete.raisons(mesures)
    c.meta["derniers"]["systeme"] = {"score": round(score, 3), "t": time.time(), "raisons": raisons}
    if score >= seuil and tete.mature(int(seuils["fantome_maturite"]), classe=int(_reglage(seuils, "fantome_classe_min"))) and _budget_libre(c, "systeme", seuils):
        if siem.alerter("fantome_systeme", "haute", _siem_tf("rg4.siem.msg.20", score=score)
                        + ", ".join(r["mesure"] for r in raisons), dedoublonner_s=1800, details={"score": round(score, 3), "raisons": raisons}):
            _budget_noter(c, "systeme")


# ── Un passage ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def passage() -> dict:
    """Apprend de ce qui est nouveau, juge, alerte, puis enregistre. Chaque partie est indépendante : une panne n'arrête pas les autres."""
    seuils = siem.config()["seuils"]
    c, maintenant = charger(), timezone.now()
    curseurs, avant = c.meta.setdefault("curseurs", {}), {n: t.n for n, t in c.tetes.items()}
    for cle in ("compte", "message"):
        c.meta.setdefault("derniers", {}).setdefault(cle, {})
    c.meta["derniers"].setdefault("systeme", None)
    alertes = _alertes_recentes(time.time() - FENETRE_S * 1.5)
    retours = cache.get(_CLE_RETOURS) or []
    if retours:
        cache.delete(_CLE_RETOURS)
    fait = {}
    for nom, fonction in (("avis", lambda: _apprendre_avis(c, curseurs, seuils)), ("messages", lambda: _passer_messages(c, curseurs, alertes, seuils, maintenant)),
                          ("comptes", lambda: _passer_comptes(c, retours, alertes, seuils, maintenant)),
                          ("systeme", lambda: cache.add("siem:fantome:systeme", 1, timeout=300) and _passer_systeme(c, seuils, maintenant))):
        try:
            fonction()
            fait[nom] = True
        except Exception:
            journal.exception(_siem_tf("rg4.siem.msg.249"), nom)
            fait[nom] = False
    limite = time.time() - 86400
    for genre in ("compte", "message"):   # la page ne garde que les 30 cas les plus forts des dernières 24 h
        d = {k: v for k, v in c.meta["derniers"][genre].items() if v["t"] >= limite}
        c.meta["derniers"][genre] = dict(sorted(d.items(), key=lambda kv: -kv[1]["score"])[:GARDE_PAGE])
    for nom, tete in c.tetes.items():
        if tete.n > avant[nom]:
            tete.affiner()
    sauver(c)
    return fait


# ── Démarrage à chaud ───────────────────────────────────────────────────────────────────────────────────────────────────
def prechauffer(jours: int = 30, max_messages: int = 5000, max_instants: int = 300, forcer: bool = False) -> dict:
    """Rejoue l'HISTORIQUE avant la première alerte : le modèle n'attend plus des semaines d'avis pour valoir mieux qu'un écho des règles.
      · messages  des `jours` derniers jours : professeur de forme + règles de compte (alertes d'alors), puis tous les avis déjà donnés
      · comptes   jusqu'à `max_instants` heures d'activité passées, étiquetées par les règles qui avaient sonné à ce moment-là
      · système   non rejoué (aucun historique de mesures n'est conservé) : il apprend en direct
    Refuse de rejouer sur un modèle qui a déjà appris (sinon l'historique compterait deux fois), sauf `forcer`. Rend un résumé."""
    from django.contrib.auth import get_user_model
    from .models import JournalAudit, Message
    seuils = siem.config()["seuils"]
    c, maintenant = charger(), timezone.now()
    if (c.tetes["message"].n or c.tetes["compte"].n) and not forcer:
        return {"deja_appris": True}
    depuis = maintenant - timedelta(days=jours)
    historique = _historique_alertes(depuis.timestamp() - 3600, limite=5000)
    registre, curseurs = _registre(c, seuils), c.meta.setdefault("curseurs", {})
    for cle in ("compte", "message"):
        c.meta.setdefault("derniers", {}).setdefault(cle, {})
    c.meta["derniers"].setdefault("systeme", None)
    scores_msg = c.meta.setdefault("scores_msg", {})
    resume = {"messages": 0, "avis": 0, "comptes": 0}

    exemples, tete = [], c.tetes["message"]
    for msg in (Message.objects.filter(cree_le__gte=depuis, auto=False, supprime=False).exclude(texte="")
                .select_related("expediteur__utilisateur").order_by("id")[:max_messages]):
        if msg.expediteur is None:
            continue
        u = msg.expediteur.utilisateur
        mesures = mesures_message(msg, u.pk, u.date_joined, registre)
        appris = juge.etiquette_apprentissage(sf.etiquette_faible_message(mesures), juge.gravite_proche(historique, u.pk, msg.cree_le.timestamp()),
                                              msg.id % 4 == 0)
        if appris:
            exemples.append({"mesures": mesures, "texte": msg.texte, "y": appris[0], "poids": appris[1]})
    tous = []
    sf.rejouer(tete, exemples, scores=tous, affiner_tous=50)
    resume["messages"] = len(exemples)
    for score in tous:
        juge.noter_score(c.meta, "message", score)
    for _ in range(6):
        tete.affiner()

    curseurs["avis"] = 0   # les avis, ensuite : la tête sait déjà ce que vaut la forme, leur étiquette calibre donc des scores déjà formés
    while True:
        examines = _apprendre_avis(c, curseurs, seuils, limite=500)
        resume["avis"] += examines
        if examines < 500:
            break
    for _ in range(6):
        tete.affiner()
    curseurs["message"] = Message.objects.order_by("-id").values_list("id", flat=True).first() or 0   # le direct ne rejoue pas ce qui vient de l'être

    tete = c.tetes["compte"]
    actifs = list(JournalAudit.objects.filter(horodatage__gte=depuis, utilisateur__isnull=False).exclude(action__startswith="SIEM:")
                  .values_list("utilisateur_id", flat=True).distinct()[:MAX_COMPTES])
    exemples = []
    for u in get_user_model().objects.filter(pk__in=actifs):
        quand = JournalAudit.objects.filter(utilisateur=u, horodatage__gte=depuis).exclude(action__startswith="SIEM:") \
            .values_list("horodatage", flat=True)[:20000]
        heures = sorted({q.replace(minute=0, second=0, microsecond=0) for q in quand})
        par_compte = max(1, max_instants // max(1, len(actifs)))
        for debut in heures[::max(1, len(heures) // par_compte)][-par_compte:]:
            t = min(debut + timedelta(hours=1), maintenant)
            lignes = list(JournalAudit.objects.filter(utilisateur=u, horodatage__gt=t - timedelta(hours=1), horodatage__lte=t).order_by("-id")
                          .values_list("action", "ip", "horodatage")[:2000])
            passe = list(JournalAudit.objects.filter(utilisateur=u, horodatage__gte=t - timedelta(days=30), horodatage__lt=t - timedelta(hours=1))
                         .exclude(ip="").values_list("horodatage", "ip")[:20000])
            grav = juge.gravite_proche(historique, u.pk, t.timestamp(), marge=1800.0)
            exemples.append({"t": t, "mesures": mesures_compte(u.pk, u, lignes, passe, t, scores_msg, registre), "y": grav, "poids": 0.5 if grav else 0.1})
    exemples.sort(key=lambda e: e["t"])
    tous = []
    sf.rejouer(tete, exemples, scores=tous, affiner_tous=50)
    resume["comptes"] = len(exemples)
    for score in tous:
        juge.noter_score(c.meta, "compte", score)
    for _ in range(6):
        tete.affiner()

    for k in sorted(scores_msg, key=int)[:-MAX_SCORES]:
        del scores_msg[k]
    sauver(c)
    return resume


# ── Pour la page SIEM ───────────────────────────────────────────────────────────────────────────────────────────────────
def etat() -> dict:
    c, seuils = charger(reagir=False), siem.config()["seuils"]
    maturite, avis_min, classe = int(seuils["fantome_maturite"]), int(_reglage(seuils, "fantome_avis_min")), int(_reglage(seuils, "fantome_classe_min"))
    calibrage = c.meta.get("calibrage", {})
    registre = _registre(c, seuils)
    return {"seuils": {"compte": seuils["fantome_seuil"], "message": seuils["fantome_seuil_message"], "maturite": maturite},
            "calibrage": calibrage,   # {tête : {seuil appliqué, source (reglage / avis / budget), réglage de départ, étiquettes}}
            "retenus": {n: r["n"] for n, r in c.meta.get("retenus", {}).items() if time.time() - r["depuis"] <= 86400},   # alertes retenues par le budget, 24 h
            "votants": {"suivis": len(registre.etat), "sans_credit": sum(1 for v in registre.etat if registre.confiance(v) < 0.5)},
            "tetes": {n: {"echantillons": t.n, "avis_ou_retours": t.n_fort, "bases": t.n_base, "perte": None if t.perte is None else round(t.perte, 4),
                          "parametres": t.reseau.nb_parametres(), "calmes": t.n_neg, "suspects": t.n_pos,
                          "mature": t.mature(maturite, avis_min if n == "message" else 0, classe)} for n, t in c.tetes.items()},
            "comptes": sorted(({"cle": k, **v} for k, v in c.meta.get("derniers", {}).get("compte", {}).items()), key=lambda x: -x["score"]),
            "messages": sorted(({"cle": k, **v} for k, v in c.meta.get("derniers", {}).get("message", {}).items()), key=lambda x: -x["score"]),
            "systeme": c.meta.get("derniers", {}).get("systeme")}
