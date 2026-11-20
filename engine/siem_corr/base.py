"""Socle commun des règles de corrélation : constantes, chemins reconnus, classement des gestes et aides partagées."""
import logging
import re
from collections import defaultdict


class Seuil:
    """Seuil réglable lu dans siem_noyau.config()['seuils'] au moment d'appliquer le registre."""
    __slots__ = ("cle", "defaut", "type")

    def __init__(self, cle, defaut, type_=int):
        self.cle, self.defaut, self.type = cle, defaut, type_

    def valeur(self, seuils):
        return self.type(seuils.get(self.cle, self.defaut))


S = Seuil


class Regle:
    """Une règle périodique : numéro, fonction pure, arguments (str = clé du contexte, Seuil = réglage) et arguments nommés."""
    __slots__ = ("numero", "fn", "args", "kwargs")

    def __init__(self, numero, fn, args, kwargs=None):
        self.numero, self.fn, self.args, self.kwargs = numero, fn, tuple(args), dict(kwargs or {})


logger = logging.getLogger("engine.siem")


FENETRE_S = 3600


HISTORIQUE_JOURS = 30


REVEIL_JOURS = 3   # un compte réveillé reste surveillé 3 jours


DORMANTE_JOURS = 14


# Chemins d'audit (JournalAudit.action = « MÉTHODE /chemin » ; lectures sensibles = « SIEM:lecture_sensible » + details.chemin).
CHEMINS = {
    "siem_config": [r"^/api/siem/(config|lockdown|rotation|rebaser|incident/clore|ingerer)"],
    "2fa_off": [r"/(2fa|double-facteur)/(desactiver|off|supprimer)"],
    "suppression": [r"/supprimer"],
    "identite": [r"/mot-de-passe", r"/password", r"/telephone", r"/2fa", r"/double-facteur", r"/email", r"^/api/employes/\d+/modifier/$"],
    "cle_revoquee": [r"^/api/cles-api/\d+/revoquer/"],
    "membre_supprime": [r"^/api/employes/\d+/supprimer/"],
    "recharge": [r"^/api/wallet/(recharger|transferer)"],
    "retrait": [r"^/api/wallet/retirer", r"^/api/wallet/agent/retrait"],
    "export": [r"/exporter", r"/export(\.|/|$)", r"/resume-general-pdf", r"/rapport-general-pdf"],
    "cle_creee": [r"^/api/cles-api/creer/"],
    "role": [r"^/api/employes/\d+/role/$"],
    "config_wallet": [r"^/api/wallet/(taux-change|frais|politique|principal|remuneration|synchroniser-table)"],
    "permissions": [r"^/api/employes/\d+/permissions/$", r"^/api/wallet/habilitations"],
    "membre_cree": [r"^/api/employes/$"],
    "cle_reactivee": [r"^/api/cles-api/\d+/(activer|reactiver)/$"],
}


_ORDRE = ("siem_config", "2fa_off", "cle_revoquee", "membre_supprime", "suppression", "identite", "recharge", "retrait", "export", "cle_creee", "role", "permissions", "config_wallet", "membre_cree", "cle_reactivee")


SENSIBLES = {"retrait", "export", "cle_creee"}


PRIVILEGES = {"role", "permissions", "membre_cree", "cle_creee", "cle_reactivee"}


_RE_TABLE = re.compile(r"^/api/tables/(\d+)")


_compile = {}


def _motifs(genre: str, extra=None):
    cle = (genre, tuple(extra or ()))
    if cle not in _compile:
        _compile[cle] = [re.compile(p) for p in CHEMINS.get(genre, []) + list(extra or [])]
    return _compile[cle]


def classer(methode: str, chemin: str, extra=None) -> str:
    """Genre d'un geste à partir de sa méthode et de son chemin ('' si banal). `extra` : {genre: [regex]} des réglages."""
    if methode == "DELETE":
        return "suppression"
    for genre in _ORDRE:
        if genre == "export" and methode not in ("GET", "HEAD", "POST"):
            continue
        if genre != "export" and methode in ("GET", "HEAD"):
            continue
        if any(m.search(chemin) for m in _motifs(genre, (extra or {}).get(genre))):
            return genre
    return ""


def _mediane(valeurs) -> float:
    s = sorted(valeurs)
    n = len(s)
    if not n:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _profil(evts):
    heures, ips, jours = defaultdict(int), defaultdict(int), defaultdict(int)
    for e in evts:
        heures[e["t"].hour] += 1
        ips[e["ip"]] += 1
        jours[e["t"].date()] += 1
    return {"heures": heures, "ips": ips, "par_jour": jours, "total": sum(heures.values())}


def _hors_heures(profil, heure) -> bool:
    return profil["total"] >= 50 and profil["heures"].get(heure, 0) / profil["total"] < 0.02


def _par_uid(evts):
    sortie = defaultdict(list)
    for e in sorted(evts, key=lambda x: x["t"]):
        sortie[e["uid"]].append(e)
    return sortie


# ── 13 et 14. Rafales d'administration ──────────────────────────────────────

def _rafale(evts, genre, seuil, fenetre_s):
    """[(uid, nombre, dernier événement)] pour chaque compte ayant fait au moins `seuil` gestes de ce genre dans la fenêtre."""
    sortie = []
    for uid, liste in _par_uid([e for e in evts if e["kind"] == genre]).items():
        for i, r in enumerate(liste):
            groupe = [x for x in liste[i:] if (x["t"] - r["t"]).total_seconds() <= fenetre_s]
            if len(groupe) >= seuil:
                sortie.append((uid, len(groupe), groupe[-1]))
                break
    return sortie
