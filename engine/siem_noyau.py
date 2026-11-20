"""SIEM — noyau (« assume breach » : le SIEM est lui-même la cible n°1, la première action d'un intrus est d'effacer les journaux).

AUCUN NOUVEAU MODÈLE, AUCUNE MIGRATION : le SIEM surveille et s'intègre à ce qui existe déjà.
  - collecte        : models.JournalAudit (déjà alimenté par AuditMiddleware) + les événements ajoutés par siem_collecte.py ;
  - infalsifiable   : scellement HMAC chaîné de JournalAudit, écrit dans la blockchain système (models.BlocSysteme, elle-même chaînée
                      par hash, répliquée par le cluster) ; le dernier bloc est ancré hors de la machine (fichier en ajout seul
                      `chattr +a`, et/ou serveur distant signé) ; toute modification ou suppression d'une ligne scellée est détectée ;
  - alertes         : BlocSysteme (plateforme) + models.Bloc (entreprise, avec cible_type/cible_id donc lisibles par
                      Bloc.historique_cible et par la page d'ontologie) + ws_notify (cloche) + SMS mmcli (critique) ;
  - réglages        : MEDIA_ROOT/systeme/siem.json (même principe que double_facteur.json) ; chaque changement est journalisé ;
  - clés            : MEDIA_ROOT/systeme/siem_cles.json (rotation) ou GYPLATFORM_SIEM_CLE (variable d'environnement, à préférer).

Réponse graduée : observation (alertes seules, 7 jours minimum) → actif (verrouillage de compte, révocation des sessions et clés API,
blocage d'IP à délai croissant). Garde-fous : jamais le dernier super-administrateur, jamais un compte « bris de glace »
(GYPLATFORM_SIEM_BRIS_DE_GLACE), jamais une IP exemptée. Bouton lockdown : plateforme en lecture seule et retraits du wallet coupés.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from .siem_cache import cache

from .stockage import dossier_systeme
from .i18n import t, tf
from engine.i18n import tf as _siem_tf  # noqa: E402

journal = logging.getLogger("engine.siem")

OBSERVATION_JOURS = 7
ACTIONS_SIEM = ("siem_scellement", "siem_purge", "siem_alerte", "siem_config", "siem_consultation", "siem_reference_code",
                "siem_reference_ports", "siem_rotation_cle", "siem_lockdown", "siem_reponse", "siem_incident", "siem_reference_serveur", "siem_masquage", "siem_reaffichage")
GRAVITES = ("info", "moyenne", "haute", "critique")
_VIE_SOURCES = ("web", "surveillance")

CONFIG_DEFAUT = {
    "mode": "observation",              # "observation" (alertes seules) | "actif" (réponse automatique)
    "debut": 0.0,                       # début de l'observation (posé au premier accès)
    "lockdown": False,                  # plateforme en lecture seule, retraits coupés
    "session_stricte": True,            # changement d'IP ou de user-agent en pleine session → session révoquée
    "ips_exemptes": [],                 # jamais bloquées
    "bris_de_glace": [],                # noms d'utilisateur jamais verrouillés automatiquement
    "ancrage_minutes": 15,
    "silence_collecteur_s": 180,
    "seuils": {
        "echecs_connexion": 3, "echecs_fenetre_s": 600,
        "refus": 20, "refus_fenetre_s": 300,
        "lectures_sensibles": 10, "lectures_fenetre_s": 300,
        "volume_x": 5,                  # activité du jour > x fois la moyenne du compte
        "ips_partagees": 3,             # nombre de comptes différents vus depuis une même IP
        "score_verrou": 60, "score_revocation": 80, "score_ip": 40,
        "derive_horloge_s": 5,
        # habitudes des comptes (siem_collecte.py)
        "tables_distinctes": 15,                        # tables différentes ouvertes par un compte en 10 min (exploration / aspiration)
        "requetes_minute": 150,                         # requêtes par minute d'un compte sans clé API (script, robot)
        "suppressions": 20,                             # suppressions par un compte en 10 min
        "echecs_avant_succes": 3,                       # échecs depuis la même IP avant une connexion réussie (corrélation)
        "ids_enumeres": 10,                             # identifiants distincts refusés (403/404) en 10 min
        "suppressions_apres_export": 5,                 # suppressions dans l'heure qui suit un export
        "compte_neuf_heures": 24,                       # un compte plus jeune que cela qui fait une action sensible est suspect
        "moyen_recent_minutes": 60,                     # retrait vers un moyen de retrait enregistré ou modifié depuis moins que cela
        "membres_en_masse": 5,                          # membres créés par un même compte en 1 h
        "cles_multiples": 3,                            # clés API créées par un même compte en 1 h
        "refus_privilege": 3,                           # refus 403 sur routes d'administration / sensibles en 10 min
        "dest_partagee_comptes": 3,                     # comptes différents retirant vers un même numéro
        "ips_rotation": 4,                              # réseaux (/24) différents pour un même compte en 1 h
        "bourrage_distribue": 5,                        # IP distinctes sur un compte, ou comptes distincts depuis une IP, en 30 min
        "eparpillement_dest": 4,                        # numéros de retrait différents pour un même compte en 1 h
        "reglages_siem_rafale": 3,                      # réglages SIEM (config, lockdown, rebasage) modifiés par un même compte en 1 h
        "recharges_rafale": 5,                          # recharges ou transferts par un même compte en 1 h
        "comptes_meme_ip": 3,                           # comptes différents qui retirent depuis une même IP en 1 h
        "usurpation_ip_partagee": 1,                    # autres comptes de l'entreprise vus sur l'IP de l'administrateur au-delà desquels l'usurpation passe de critique à haute (0 = jamais)
        "continu_oubli_heures": 3.0,                    # règles wallet continues : un geste pèse 1 pendant son palier (1 h, 24 h ou 30 min) puis moitié moins toutes les N heures (0 = fenêtres de la v17)
        "montants_identiques": 3,                       # retraits exactement du même montant en 1 h
        "revocations_rafale": 3,                        # clés API révoquées par un même compte en 1 h
        "escalade_retraits": 4,                         # retraits aux montants strictement croissants en 1 h
        "identite_rafale": 3,                           # changements d'identifiants d'un compte sur lui-même en 1 h
        "purge_cibles": 3,                              # comptes différents dont le rôle change ou que l'on supprime en 1 h
        "exports_rafale": 4,                            # exports sur des chemins différents en 1 h
        "acces_distincts": 3,                           # sortes d'accès durables créées en 1 h (clé, membre, rôle, permissions)
        "cadence_retraits": 5,                          # retraits à intervalles presque égaux dans l'heure (script)
        "familles_lues": 4,                             # familles de données sensibles lues en 1 h (clients, employés, wallets…)
        "dormant_jours": 60,                            # silence d'un compte avant qu'un réveil sensible soit suspect
        "tables_correlation": 8,                       # tables parcourues dans l'heure qui, suivies d'un export, forment une alerte de corrélation
        # modèle fantôme du SIEM (siem_fantome.py) : il se tait tant qu'il n'a pas appris `fantome_maturite` échantillons (et `fantome_avis_min` avis pour les messages)
        "fantome_seuil": 0.75, "fantome_seuil_message": 0.7, "fantome_maturite": 30,
        # … ces deux seuils sont le POINT DE DÉPART : tant qu'on ne sait rien le modèle s'en sert tel quel ; ensuite il les calibre (siem_fantome_juge.seuil_calibre)
        # sur les avis reçus (jamais sous 0,5) et sur un quantile des scores récents. 0 = désactiver ce calibrage.
        "fantome_calibrage": 1,
        "fantome_avis_min": 3,                          # avis humains qu'un modèle de messages doit avoir reçus avant d'alerter (avec fantome_classe_min de chaque classe)
        "fantome_classe_min": 5,                        # étiquettes « calme » ET « suspect » au moins, toutes sources : un modèle qui n'a vu qu'une classe se tait
        "fantome_budget_compte": 10, "fantome_budget_message": 20, "fantome_budget_systeme": 3,   # alertes du modèle par heure et par tête (0 = sans plafond)
        "fantome_quota_votant": 20,                     # avis par jour au-delà desquels un même votant compte de moins en moins
        "fantome_votants_min": 3,                       # votants distincts de confiance avant qu'un expéditeur soit dit « mal noté »
        "connexions_rafale": 5,                         # connexions réussies d'un même compte en 10 min                 # processus inconnu au-dessus de ce CPU pendant ce nombre de contrôles
    },
}
_CLES_MODIFIABLES = ("mode", "lockdown", "session_stricte", "ips_exemptes", "bris_de_glace", "ancrage_minutes",
                     "silence_collecteur_s", "seuils")
_memo_config = {"t": 0.0, "v": None}


# ── Réglages ────────────────────────────────────────────────────────────────

def _chemin_config() -> Path:
    return dossier_systeme("siem.json")


def _fusion(base, ajout):
    sortie = dict(base)
    for k, v in (ajout or {}).items():
        if isinstance(v, dict) and isinstance(sortie.get(k), dict):
            sortie[k] = _fusion(sortie[k], v)
        else:
            sortie[k] = v
    return sortie


def _ecrire_json_atomique(chemin: Path, donnees):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(chemin.suffix + ".tmp")
    tmp.write_text(json.dumps(donnees, indent=1, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, chemin)


def config(forcer=False) -> dict:
    """Réglages courants (relus au plus toutes les 5 s : le middleware les consulte à chaque requête)."""
    maintenant = time.time()
    if not forcer and _memo_config["v"] is not None and maintenant - _memo_config["t"] < 5:
        return _memo_config["v"]
    try:
        lu = json.loads(_chemin_config().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lu = {}
    if not isinstance(lu, dict):
        # JSON valide mais pas un objet (liste, texte, nombre) : fichier corrompu ou trafiqué -> réglages par défaut, jamais d'exception
        journal.error(_siem_tf("rg4.siem.msg.263"))
        lu = {}
    try:
        cfg = _fusion(CONFIG_DEFAUT, lu)
    except Exception:
        journal.exception(_siem_tf("rg4.siem.msg.264"))
        cfg = _fusion(CONFIG_DEFAUT, {})
    if not cfg["debut"]:
        cfg["debut"] = maintenant
        try:
            _ecrire_json_atomique(_chemin_config(), cfg)
        except OSError:
            journal.exception(_siem_tf("rg4.siem.msg.265"))
    _memo_config["t"], _memo_config["v"] = maintenant, cfg
    return cfg


def jours_observation() -> float:
    return (time.time() - config()["debut"]) / 86400.0


def modifier_config(changements: dict, utilisateur=None, ip="", forcer_actif=False) -> dict:
    """Applique des réglages validés ; un changement de règle est lui-même journalisé dans la blockchain système."""
    cfg = config(forcer=True)
    propres = {k: v for k, v in (changements or {}).items() if k in _CLES_MODIFIABLES}
    if propres.get("mode") not in (None, "observation", "actif"):
        raise ValueError(t("engine/siem_noyau.py:g:168:27"))
    if propres.get("mode") == "actif" and cfg["mode"] != "actif" and jours_observation() < OBSERVATION_JOURS and not forcer_actif:
        raise ValueError(tf("engine/siem_noyau.py:g:170:28", v1=OBSERVATION_JOURS, v2=jours_observation()))
    if "seuils" in propres:
        seuils = {}
        for cle, val in dict(propres["seuils"]).items():
            if cle in CONFIG_DEFAUT["seuils"]:
                seuils[cle] = float(val) if isinstance(CONFIG_DEFAUT["seuils"][cle], float) else int(val)
        propres["seuils"] = seuils
    for liste in ("ips_exemptes", "bris_de_glace"):
        if liste in propres:
            propres[liste] = [str(x).strip() for x in propres[liste] if str(x).strip()]
    nouvelle = _fusion(cfg, propres)
    _ecrire_json_atomique(_chemin_config(), nouvelle)
    _memo_config["v"] = None
    _bloc_systeme(utilisateur, "siem_config", {"changements": propres, "ip": ip})
    return nouvelle


# ── Clés HMAC (rotation) ────────────────────────────────────────────────────

def _chemin_cles() -> Path:
    return dossier_systeme("siem_cles.json")


def _lire_cles() -> list:
    env = os.environ.get("GYPLATFORM_SIEM_CLE", "").strip()
    if env:
        return [{"id": "env", "cle": env, "depuis": 0}]
    try:
        cles = json.loads(_chemin_cles().read_text(encoding="utf-8"))
        if isinstance(cles, list) and cles:
            return cles
    except (OSError, ValueError):
        pass
    cles = [{"id": secrets.token_hex(4), "cle": secrets.token_hex(32), "depuis": time.time()}]
    try:
        _ecrire_json_atomique(_chemin_cles(), cles)
    except OSError:
        journal.exception(_siem_tf("rg4.siem.msg.266"))
    return cles


def cle_courante() -> tuple:
    c = _lire_cles()[-1]
    return c["id"], c["cle"].encode()


def cle_par_id(cle_id):
    for c in _lire_cles():
        if c["id"] == cle_id:
            return c["cle"].encode()
    return None


def toutes_les_cles() -> list:
    return [c["cle"].encode() for c in _lire_cles()]


def faire_tourner_cle(utilisateur=None, ip="") -> str:
    """Nouvelle clé ; les anciennes restent lisibles (les scellements passés indiquent la clé qui les a signés)."""
    if os.environ.get("GYPLATFORM_SIEM_CLE", "").strip():
        raise ValueError(t("engine/siem_noyau.py:g:230:27"))
    cles = _lire_cles()
    nouvelle = {"id": secrets.token_hex(4), "cle": secrets.token_hex(32), "depuis": time.time()}
    _ecrire_json_atomique(_chemin_cles(), cles + [nouvelle])
    _bloc_systeme(utilisateur, "siem_rotation_cle", {"cle_id": nouvelle["id"], "ip": ip})
    return nouvelle["id"]


def signer(donnees: bytes, cle=None) -> str:
    return hmac.new(cle or cle_courante()[1], donnees, hashlib.sha256).hexdigest()


# ── Écritures dans la blockchain système / entreprise ──────────────────────

def _bloc_systeme(utilisateur, action, details):
    try:
        from .models import BlocSysteme
        return BlocSysteme.enregistrer(utilisateur if getattr(utilisateur, "pk", None) else None, action, details)
    except Exception:
        journal.exception(_siem_tf("rg4.siem.msg.267"), action)
        return None


def _transactions_siem(action, limite=5000):
    """Transactions SIEM `action`, de la plus récente à la plus ancienne.

    `limite` compte les blocs de CETTE action, pas les blocs de la chaîne : une référence (code, ports, serveur) ou une purge posée il y a
    longtemps reste retrouvable quelle que soit l'activité écrite depuis (un bloc par scellement, alerte ou réponse). Un parcours borné
    aux N derniers blocs de la chaîne la faisait « disparaître » au bout de quelques jours, et les contrôles la reposaient alors en
    silence comme un premier passage : le code modifié devenait la nouvelle référence. Un bloc système porte une seule transaction
    (BlocSysteme.enregistrer), donc l'action se lit à l'indice 0.
    """
    from django.db import DatabaseError
    from .models import BlocSysteme
    rendu = False
    try:
        for b in BlocSysteme.objects.filter(transactions__0__action=action).order_by("-index")[:limite].iterator(chunk_size=200):
            for tx in b.transactions or []:
                if isinstance(tx, dict) and tx.get("action") == action:
                    rendu = True
                    yield b, tx
        return
    except DatabaseError:
        if rendu:
            raise
        journal.warning(_siem_tf("rg4.siem.msg.268"), action, exc_info=True)
    trouves = 0   # repli (base sans requête sur le JSON) : parcours large, jamais celui de 5000 blocs qui perdait les références
    for b in BlocSysteme.objects.order_by("-index")[:200000].iterator(chunk_size=500):
        for tx in b.transactions or []:
            if isinstance(tx, dict) and tx.get("action") == action:
                yield b, tx
                trouves += 1
                if trouves >= limite:
                    return


def dernier_dans_chaine(action):
    for b, tx in _transactions_siem(action):
        return b, tx
    return None, None


def lots_masques() -> list:
    """Lots d'historique masqués sur la page SIEM, encore actifs, du plus récent au plus ancien : [{id, nom, c, criteres, jusqua, ts, par}].

    Masquer ne supprime RIEN : la blockchain système et le journal d'audit scellé restent intacts (« assume breach » : un super-administrateur
    piraté ne doit pas pouvoir faire disparaître ses traces). Chaque masquage (`siem_masquage`) et chaque réaffichage (`siem_reaffichage`,
    d'un lot ou de « tout ») est un bloc de la chaîne, avec le nom de qui l'a demandé. Un lot est actif tant qu'aucun réaffichage ne le
    vise après lui."""
    from . import siem_recherche
    masquages = [(float(tx.get("ts") or 0), tx.get("details") or {}, tx.get("utilisateur") or "") for _b, tx in _transactions_siem("siem_masquage", 500)]
    if not masquages:
        return []
    reaffichages = [(float(tx.get("ts") or 0), (tx.get("details") or {}).get("lot")) for _b, tx in _transactions_siem("siem_reaffichage", 500)]
    par = {(ts, d.get("lot")): u for ts, d, u in masquages}
    lots = siem_recherche.lots_actifs([(ts, d) for ts, d, _u in masquages], reaffichages)
    for lot in lots:
        lot["par"] = par.get((lot["ts"], lot["id"]), "")
    return lots


MAX_LOTS = 100


def masquer_lot(utilisateur, criteres_bruts, ip="") -> dict:
    """Masque le lot décrit par `criteres_bruts` (vide = tout l'historique existant). Inscrit dans la chaîne ; {} si refusé ou impossible."""
    from . import siem_recherche
    if len(lots_masques()) >= MAX_LOTS:
        return {}
    cr = siem_recherche.nettoyer(criteres_bruts)
    lot = {"lot": secrets.token_hex(4), "criteres": cr, "nom": siem_recherche.resume(cr), "jusqua": time.time(), "ip": ip}
    return lot if _bloc_systeme(utilisateur, "siem_masquage", lot) is not None else {}


def reafficher_lot(utilisateur, lot, ip="") -> bool:
    """Réaffiche un lot (id) ou tout (« tout »). Inscrit dans la chaîne ; False si l'inscription échoue."""
    return _bloc_systeme(utilisateur, "siem_reaffichage", {"lot": str(lot)[:32], "ip": ip}) is not None


# ── Scellement HMAC chaîné de JournalAudit ──────────────────────────────────

def _empreinte_ligne(ligne) -> bytes:
    return json.dumps(
        [ligne.id, ligne.entreprise_id, ligne.utilisateur_id, ligne.action, ligne.horodatage.isoformat(), ligne.resultat, ligne.ip,
         ligne.type_acteur, ligne.source, ligne.cible_type, ligne.cible_id, ligne.details, ligne.ancienne_valeur,
         ligne.nouvelle_valeur, ligne.agent_utilise, ligne.fonction_utilisee],
        sort_keys=True, default=str, ensure_ascii=False).encode()


def _racine_sur(lignes, racine_prec: str, cle: bytes) -> tuple:
    """(racine, nombre de lignes) : h_i = HMAC(cle, h_{i-1} | ligne_i)."""
    h, n = racine_prec, 0
    for ligne in lignes:
        h = hmac.new(cle, h.encode() + b"|" + _empreinte_ligne(ligne), hashlib.sha256).hexdigest()
        n += 1
    return h, n


def _base_purge() -> tuple:
    """(id de base, racine de base) : tout ce qui est ≤ id de base a été purgé par une purge signée."""
    _b, tx = dernier_dans_chaine("siem_purge")
    if tx:
        return int(tx["details"].get("base_id", 0)), tx["details"].get("racine", "0" * 64)
    return 0, "0" * 64


def sceller(delai_s=30):
    """Scelle les lignes de JournalAudit apparues depuis le dernier scellement (en retrait de `delai_s` s : une transaction encore
    en cours de validation ne doit pas se glisser sous un scellement). Retourne le bloc ou None."""
    from django.utils import timezone
    from .models import JournalAudit
    _b, dernier = dernier_dans_chaine("siem_scellement")
    base_id, base_racine = _base_purge()
    if dernier:
        a_prec, racine_prec = int(dernier["details"]["a"]), dernier["details"]["racine"]
        if a_prec < base_id:
            a_prec, racine_prec = base_id, base_racine
    else:
        a_prec, racine_prec = base_id, base_racine
    limite = timezone.now() - timezone.timedelta(seconds=delai_s)
    lignes = []
    for ligne in JournalAudit.objects.filter(id__gt=a_prec).order_by("id").iterator(chunk_size=2000):
        if ligne.horodatage > limite:
            break
        lignes.append(ligne)
    if not lignes:
        return None
    cle_id, cle = cle_courante()
    racine, n = _racine_sur(lignes, racine_prec, cle)
    return _bloc_systeme(None, "siem_scellement", {"de": a_prec, "a": lignes[-1].id, "n": n, "racine": racine,
                                                    "prec": racine_prec, "cle_id": cle_id})


def verifier_scellements() -> tuple:
    """(ok, anomalies). Recalcule chaque scellement depuis la base : ligne modifiée, supprimée ou insérée sous un scellement,
    scellement manquant ou remplacé → anomalie. Ne regarde que ce qui suit la dernière purge signée."""
    from .models import BlocSysteme, JournalAudit
    base_id, base_racine = _base_purge()
    anomalies, attendu, a_prec = [], base_racine, base_id
    for b in BlocSysteme.objects.order_by("index").iterator(chunk_size=500):
        for tx in b.transactions or []:
            if not (isinstance(tx, dict) and tx.get("action") == "siem_scellement"):
                continue
            d = tx.get("details") or {}
            if int(d.get("a", 0)) <= base_id:
                continue
            if d.get("prec") != attendu or int(d.get("de", -1)) != a_prec:
                anomalies.append({"bloc": b.index, "motif": _siem_tf("rg4.siem.msg.185")})
            cle = cle_par_id(d.get("cle_id"))
            if cle is None:
                anomalies.append({"bloc": b.index, "motif": _siem_tf("rg4.siem.msg.186")})
            else:
                lignes = JournalAudit.objects.filter(id__gt=int(d["de"]), id__lte=int(d["a"])).order_by("id").iterator(chunk_size=2000)
                racine, n = _racine_sur(lignes, d["prec"], cle)
                if n != d.get("n"):
                    anomalies.append({"bloc": b.index, "motif": _siem_tf("rg4.siem.msg.189", v1=d.get('n'), n=n)})
                elif racine != d.get("racine"):
                    anomalies.append({"bloc": b.index, "motif": _siem_tf("rg4.siem.msg.190")})
            attendu, a_prec = d.get("racine"), int(d.get("a", a_prec))
    return (not anomalies), anomalies


@contextmanager
def purge_autorisee():
    """Seule porte de sortie pour supprimer des lignes d'audit scellées (voir purger)."""
    cache.set("siem:purge_autorisee", True, timeout=120)
    try:
        yield
    finally:
        cache.delete("siem:purge_autorisee")


def purge_est_autorisee() -> bool:
    return bool(cache.get("siem:purge_autorisee"))


def signature_purge(jusqua_iso: str) -> str:
    """Signature à fournir à purger() ; calculée par l'opérateur qui a accès à la clé (commande siem_surveiller --signer-purge)."""
    return signer(f"purge:{jusqua_iso}".encode())


def purger(jusqua_iso: str, signature: str, utilisateur=None) -> int:
    """Purge de rétention signée : seule suppression possible. Elle s'arrête à une frontière de scellement, est inscrite dans la
    blockchain système AVANT d'agir, et la vérification repart de cette frontière."""
    from django.utils.dateparse import parse_datetime
    from .models import JournalAudit
    if not any(hmac.compare_digest(signature or "", signer(f"purge:{jusqua_iso}".encode(), c)) for c in toutes_les_cles()):
        raise PermissionError(t("rg4.siem.purge_signature"))
    limite = parse_datetime(jusqua_iso)
    if limite is None:
        raise ValueError(t("rg4.siem.date_illisible"))
    frontiere, racine = _base_purge()
    for _b, tx in reversed(list(_transactions_siem("siem_scellement"))):   # du plus ancien au plus récent
        d = tx["details"]
        if int(d["a"]) <= frontiere:
            continue
        derniere = JournalAudit.objects.filter(id=int(d["a"])).first()
        if derniere is None or derniere.horodatage >= limite:
            break
        frontiere, racine = int(d["a"]), d["racine"]
    if frontiere <= _base_purge()[0]:
        return 0
    if _bloc_systeme(utilisateur, "siem_purge", {"base_id": frontiere, "racine": racine, "jusqua": jusqua_iso}) is None:
        raise RuntimeError(t("rg4.siem.purge_refusee"))
    with purge_autorisee():
        supprimes, _ = JournalAudit.objects.filter(id__lte=frontiere).delete()
    return supprimes


# ── Ancrage hors machine ────────────────────────────────────────────────────

def ancrer():
    """Écrit le dernier bloc de la chaîne système hors de la machine : fichier en ajout seul et/ou serveur
    distant (POST signé). Retourne un dict {cible: ok}."""
    from .models import BlocSysteme
    dernier = BlocSysteme.objects.order_by("-index").first()
    if dernier is None:
        return {}
    ligne = f"{int(time.time())} {dernier.index} {dernier.hash}"
    ligne = f"{ligne} {signer(ligne.encode())}\n"
    resultat = {}
    chemin = getattr(settings, "GYPLATFORM_SIEM_ANCRAGE_FICHIER", "")
    if chemin:
        try:
            p = Path(chemin)
            nouveau = not p.exists()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(ligne)
            resultat["fichier"] = True
        except OSError:
            journal.exception(_siem_tf("rg4.siem.msg.271"))
            resultat["fichier"] = False
    url = getattr(settings, "GYPLATFORM_SIEM_ANCRAGE_URL", "")
    if url:
        resultat["distant"] = _poster_ancrage(url, ligne)
    cache.set("siem:dernier_ancrage", {"t": time.time(), "index": dernier.index, "hash": dernier.hash, "cibles": resultat}, timeout=None)
    return resultat


def _poster_ancrage(url, ligne) -> bool:
    import urllib.request
    from . import journal_hors_ligne as cluster
    from .securite import url_externe_sure
    secret = getattr(settings, "GYPLATFORM_SIEM_ANCRAGE_SECRET", "")
    if not secret:
        return False
    if not getattr(settings, "GYPLATFORM_SIEM_ANCRAGE_URL_INTERNE", False) and not url_externe_sure(url):
        journal.warning(_siem_tf("rg4.siem.msg.269"))
        return False
    corps = ligne.encode()
    ts, mac = cluster.signer(secret, corps)
    req = urllib.request.Request(url, data=corps, method="POST",
                                 headers={"X-Siem-Ts": str(ts), "X-Siem-Mac": mac, "Content-Type": "text/plain"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception:
        journal.warning(_siem_tf("rg4.siem.msg.270"), exc_info=True)
        return False


def verifier_ancrage() -> tuple:
    """(ok, anomalies) : chaque ligne du fichier d'ancrage doit retrouver SON bloc, de même hash, dans la chaîne système. Une chaîne
    réécrite ou tronquée depuis l'ancrage est détectée même si elle est cohérente en interne."""
    from .models import BlocSysteme
    chemin = getattr(settings, "GYPLATFORM_SIEM_ANCRAGE_FICHIER", "")
    if not chemin or not Path(chemin).exists():
        return True, []
    anomalies = []
    try:
        lignes = Path(chemin).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False, [{"motif": _siem_tf("rg4.siem.msg.261")}]
    hashes = dict(BlocSysteme.objects.values_list("index", "hash"))
    for brute in lignes:
        morceaux = brute.split(" ")
        if len(morceaux) != 4:
            anomalies.append({"motif": _siem_tf("rg4.siem.msg.182"), "ligne": brute[:80]})
            continue
        _t, index, h, mac = morceaux
        if not any(hmac.compare_digest(mac, signer(f"{_t} {index} {h}".encode(), c)) for c in toutes_les_cles()):
            anomalies.append({"bloc": int(index), "motif": _siem_tf("rg4.siem.msg.183")})
        elif hashes.get(int(index)) != h:
            anomalies.append({"bloc": int(index), "motif": _siem_tf("rg4.siem.msg.187")})
    return (not anomalies), anomalies


def verifier_chaines() -> tuple:
    """Vérification complète (horaire) : chaîne système, chaîne de chaque entreprise, scellements, ancrage."""
    from .models import Bloc, BlocSysteme, Entreprise
    anomalies = []
    ok, premier = BlocSysteme.chaine_valide()
    if not ok:
        anomalies.append({"source": _siem_tf("rg4.siem.msg.176"), "motif": _siem_tf("rg4.siem.msg.177", premier=premier)})
    for eid in Entreprise.objects.values_list("id", flat=True):
        if not Bloc.chaine_valide(eid):
            anomalies.append({"source": _siem_tf("rg4.siem.msg.260", v1=eid), "motif": _siem_tf("rg4.siem.msg.184")})
    ok, an = verifier_scellements()
    anomalies += [dict(a, source=_siem_tf("rg4.siem.msg.258")) for a in an]
    ok, an = verifier_ancrage()
    anomalies += [dict(a, source=_siem_tf("rg4.siem.msg.259")) for a in an]
    return (not anomalies), anomalies


# ── Adresses, compteurs, battements de vie ──────────────────────────────────

def adresse_ip(request) -> str:
    from .ratelimit import _adresse_ip
    return _adresse_ip(request)


def ip_exemptee(ip: str) -> bool:
    try:
        adr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if adr.is_loopback:
        return True
    for entree in config()["ips_exemptes"]:
        try:
            if adr in ipaddress.ip_network(entree, strict=False):
                return True
        except ValueError:
            continue
    return False


def compter(cle: str, fenetre_s: int) -> int:
    """Compteur à fenêtre fixe dans le cache partagé (même principe que ratelimit.py)."""
    k = f"siem:cpt:{cle}:{int(time.time() // fenetre_s)}"
    try:
        try:
            return cache.incr(k)
        except ValueError:
            cache.set(k, 1, timeout=fenetre_s + 5)
            return 1
    except Exception:
        journal.exception(_siem_tf("rg4.siem.msg.272"))
        return 0


def valeur_compteur(cle: str, fenetre_s: int) -> int:
    """Valeur courante d'un compteur de compter() sans l'incrémenter."""
    try:
        return int(cache.get(f"siem:cpt:{cle}:{int(time.time() // fenetre_s)}") or 0)
    except Exception:
        return 0


def incrementer(cle: str, duree_s: int) -> int:
    """Compteur à longue durée (profils : 35 jours). La durée est posée à la création ; l'incrément ne la prolonge pas."""
    k = f"siem:prof:{cle}"
    try:
        try:
            return cache.incr(k)
        except ValueError:
            cache.set(k, 1, timeout=duree_s)
            return 1
    except Exception:
        return 0


def battement(source: str):
    """Signal de vie d'un collecteur : son silence est une alerte (interrupteur de l'homme mort)."""
    try:
        cache.set(f"siem:vie:{source}", time.time(), timeout=None)
    except Exception:
        pass


def derniers_battements() -> dict:
    return {s: cache.get(f"siem:vie:{s}") for s in _VIE_SOURCES}


# ── Alertes ─────────────────────────────────────────────────────────────────

def _superusers():
    from django.contrib.auth import get_user_model
    return list(get_user_model().objects.filter(is_superuser=True, is_active=True))


def alerter(regle: str, gravite: str, message: str, *, utilisateur=None, ip="", entreprise_id=None, details=None, dedoublonner_s=600):
    """Une alerte = une preuve dans la blockchain système (+ celle de l'entreprise concernée) + cloche des super-administrateurs
    + SMS si critique. Retourne True si émise, False si dédoublonnée."""
    cle = f"siem:alerte:{regle}:{getattr(utilisateur, 'pk', '')}:{ip}"
    try:
        if not cache.add(cle, 1, timeout=dedoublonner_s):
            _incident_rappel(regle, ip)
            return False
    except Exception:
        pass
    gravite = gravite if gravite in GRAVITES else "moyenne"
    corps = {"regle": regle, "gravite": gravite, "message": message[:400], "ip": ip, "mode": config()["mode"],
             "cible_utilisateur": getattr(utilisateur, "pk", None), "details": details or {}}
    _bloc_systeme(utilisateur, "siem_alerte", corps)
    if entreprise_id:
        try:
            from .models import Bloc
            Bloc.ajouter_transaction(entreprise_id, {
                "type": "siem_alerte", "action": f"siem:{regle}", "statut": gravite,
                "cible_type": "utilisateur" if utilisateur is not None else "ip",
                "cible_id": getattr(utilisateur, "pk", None) or ip, "utilisateur_id": getattr(utilisateur, "pk", None),
                "ip": ip, "donnees": {"message": message[:200]}})
        except Exception:
            journal.exception(_siem_tf("rg4.siem.msg.273"), entreprise_id)
    journal.warning("SIEM [%s/%s] %s (%s)", gravite, regle, message, ip)
    # Incident : l'alerte est un signal. Si l'incident est en « attaque », il envoie lui-même UNE notification groupée : pas de doublon ici.
    if not _incident_signal(regle, gravite, message, ip, getattr(utilisateur, "pk", None)):
        _notifier(gravite, regle, message)
    return True


def _incident_signal(regle, gravite, message, ip, utilisateur_id) -> bool:
    try:
        from . import siem_incident
        return siem_incident.signal(regle, gravite, message, ip, utilisateur_id)
    except Exception:
        journal.exception(_siem_tf("rg4.siem.msg.274"))
        return False


def _incident_rappel(regle, ip):
    try:
        from . import siem_incident
        siem_incident.rappel(regle, ip)
    except Exception:
        pass


def _notifier(gravite, regle, message):
    """Cloche (ws_notify) pour tous les super-administrateurs."""
    try:
        from . import ws_notify
        texte = f"SIEM {gravite.upper()} — {regle} : {message}"[:240]
        for su in _superusers():
            ws_notify.notifier_utilisateur(su.pk, message=texte)
    except Exception:
        journal.exception(_siem_tf("rg4.siem.msg.276"))


def evenement(type_: str, *, utilisateur=None, entreprise_id=None, ip="", details=None, resultat="siem", cible_type="", cible_id=None):
    """Événement collecté, stocké dans JournalAudit (déjà scellé par sceller()). Sans entreprise (visiteur anonyme), l'événement n'a
    pas de ligne d'audit : il alimente les compteurs et, s'il dépasse un seuil, une alerte."""
    if not entreprise_id:
        return None
    try:
        from .models import JournalAudit
        return JournalAudit.enregistrer(entreprise=int(entreprise_id), utilisateur=utilisateur, action=f"SIEM:{type_}"[:255],
                                        details=details or {}, type_acteur="systeme", source="siem", resultat=resultat,
                                        cible_type=cible_type, cible_id=cible_id, ip=ip)
    except Exception:
        journal.exception(_siem_tf("rg4.siem.msg.277"), type_)
        return None


# ── Score de risque et réponse graduée ──────────────────────────────────────

def ajouter_risque(sujet: str, cle, points: float) -> float:
    """Score par compte (sujet « compte ») ou par IP (« ip »), qui s'évapore de moitié toutes les 30 minutes."""
    k, maintenant = f"siem:risque:{sujet}:{cle}", time.time()
    try:
        etat = cache.get(k) or {"s": 0.0, "t": maintenant}
        score = etat["s"] * (0.5 ** ((maintenant - etat["t"]) / 1800.0)) + points
        cache.set(k, {"s": score, "t": maintenant}, timeout=86400)
        return score
    except Exception:
        return points


def score_risque(sujet: str, cle) -> float:
    etat = cache.get(f"siem:risque:{sujet}:{cle}")
    return 0.0 if not etat else etat["s"] * (0.5 ** ((time.time() - etat["t"]) / 1800.0))


def est_protege(utilisateur) -> bool:
    """Garde-fou : un blocage automatique ne doit jamais verrouiller le dernier admin ni un compte « bris de glace »."""
    from django.contrib.auth import get_user_model
    if utilisateur.get_username() in set(config()["bris_de_glace"]) | set(getattr(settings, "GYPLATFORM_SIEM_BRIS_DE_GLACE", [])):
        return True
    if utilisateur.is_superuser:
        autres = get_user_model().objects.filter(is_superuser=True, is_active=True).exclude(pk=utilisateur.pk).count()
        return autres == 0
    return False


def revoquer_sessions(user_id) -> int:
    """Déconnecte toutes les sessions d'un utilisateur (table de sessions de Django, aucun modèle ajouté)."""
    from django.contrib.sessions.models import Session
    from django.utils import timezone
    n = 0
    for s in Session.objects.filter(expire_date__gt=timezone.now()).iterator(chunk_size=500):
        try:
            if str(s.get_decoded().get("_auth_user_id")) == str(user_id):
                s.delete()
                n += 1
        except Exception:
            continue
    return n


def revoquer_cles_api(user_id) -> int:
    from .models import CleAPI
    return CleAPI.objects.filter(membre__utilisateur_id=user_id, actif=True).update(actif=False)


def compte_verrouille(user_id) -> bool:
    fin = cache.get(f"siem:verrou:{user_id}")
    return bool(fin and fin > time.time())


def ip_bloquee(ip: str) -> float:
    """Secondes restantes de blocage (0 si libre)."""
    fin = cache.get(f"siem:ip_bloc:{ip}") or 0
    return max(0.0, fin - time.time())


def deverrouiller_compte(user_id):
    cache.delete(f"siem:verrou:{user_id}")


def debloquer_ip(ip):
    cache.delete(f"siem:ip_bloc:{ip}")
    cache.delete(f"siem:ip_niv:{ip}")


def appliquer_reponse(sujet: str, cle, score: float, *, utilisateur=None, ip="", entreprise_id=None):
    """Réponse graduée selon le score. En observation : seulement journalisée (« simulée »), jamais appliquée."""
    cfg = config()
    s = cfg["seuils"]
    actions = []
    if sujet == "compte" and utilisateur is not None:
        if score >= s["score_revocation"]:
            actions += ["verrou", "sessions", "cles_api"]
        elif score >= s["score_verrou"]:
            actions += ["verrou", "sessions"]
    elif sujet == "ip" and score >= s["score_ip"]:
        actions += ["blocage_ip"]
    if not actions:
        return []
    if cfg["mode"] != "actif":
        try:   # une réponse simulée par sujet et par 10 min : un bloc par cycle remplirait la chaîne pour rien
            premiere = cache.add(f"siem:simulee:{sujet}:{cle}:{'+'.join(actions)}", 1, timeout=600)
        except Exception:
            premiere = True
        if premiere:
            _bloc_systeme(None, "siem_reponse", {"sujet": sujet, "cle": str(cle), "score": round(score, 1), "actions": actions, "simulee": True})
        return [_siem_tf("rg4.siem.msg.175", a=a) for a in actions]
    faites = []
    if sujet == "compte" and utilisateur is not None:
        if est_protege(utilisateur):
            _bloc_systeme(None, "siem_reponse", {"sujet": sujet, "cle": str(cle), "score": round(score, 1), "actions": [], "protege": True})
            return [_siem_tf("rg4.siem.msg.178")]
        if "verrou" in actions:
            cache.set(f"siem:verrou:{utilisateur.pk}", time.time() + 3600, timeout=3600)
            faites.append(_siem_tf("rg4.siem.msg.262"))
        if "sessions" in actions:
            faites.append(_siem_tf("rg4.siem.msg.179", v1=revoquer_sessions(utilisateur.pk)))
        if "cles_api" in actions:
            faites.append(_siem_tf("rg4.siem.msg.180", v1=revoquer_cles_api(utilisateur.pk)))
    elif sujet == "ip" and "blocage_ip" in actions and not ip_exemptee(str(cle)):
        try:
            niveau = cache.incr(f"siem:ip_niv:{cle}")
        except ValueError:
            cache.set(f"siem:ip_niv:{cle}", 1, timeout=7 * 86400)
            niveau = 1
        duree = min(60 * 2 ** (niveau - 1), 86400)   # 1 min, 2, 4… plafonné à 24 h
        cache.set(f"siem:ip_bloc:{cle}", time.time() + duree, timeout=duree)
        faites.append(_siem_tf("rg4.siem.msg.181", v1=duree // 60))
    if faites:
        _bloc_systeme(None, "siem_reponse", {"sujet": sujet, "cle": str(cle), "score": round(score, 1), "actions": faites})
        alerter("reponse_automatique", "haute", f"{sujet} {cle} : " + ", ".join(faites), utilisateur=utilisateur, ip=ip,
                entreprise_id=entreprise_id, dedoublonner_s=60)
    return faites


# ── Lockdown ────────────────────────────────────────────────────────────────

def lockdown_actif() -> bool:
    return bool(config()["lockdown"])


def definir_lockdown(actif: bool, utilisateur=None, ip=""):
    modifier_config({"lockdown": bool(actif)}, utilisateur, ip)
    _bloc_systeme(utilisateur, "siem_lockdown", {"actif": bool(actif), "ip": ip})
    alerter("lockdown", "critique", "Lockdown " + (_siem_tf("rg4.siem.msg.24") if actif else _siem_tf("rg4.siem.msg.25")), utilisateur=utilisateur,
            ip=ip, dedoublonner_s=1)


_MOTIFS_LOCKDOWN = {   # libellé passé par les appelants -> clé i18n (un motif inconnu s'affiche tel quel)
    "retrait_wallet": "rg4.siem.motif.retrait_wallet",
    "retrait_especes": "rg4.siem.motif.retrait_especes",
    "ecriture": "rg4.siem.motif.ecriture",
}


def exiger_hors_lockdown(motif="ecriture"):
    """À appeler en tête de toute opération sensible hors requête HTTP (retraits du wallet, tâches Celery)."""
    if lockdown_actif():
        raise ValueError(tf("rg4.siem.lockdown_actif", motif=t(_MOTIFS_LOCKDOWN[motif]) if motif in _MOTIFS_LOCKDOWN else motif))   # ValueError : message affiché tel quel par les vues du wallet
