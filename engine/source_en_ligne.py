"""Source en ligne (« Online Sheet ») : télécharge un fichier de données (CSV, Excel, Parquet, JSON) publié à une adresse web, pour qu'il soit lu
ensuite comme n'importe quel fichier téléversé (même import, même découpe en groupe, mêmes mises à jour que Google Sheet — voir maj_table.py).

Le serveur va chercher une adresse donnée par un utilisateur : trois protections.
- Adresse http(s) seulement, sans identifiants dans le lien ; l'hôte (et chaque redirection) ne doit pas pointer vers le réseau interne du
  serveur (boucle locale, réseaux privés, liens locaux, adresses réservées) sauf si l'administrateur l'autorise (SOURCES_EN_LIGNE_RESEAU_PRIVE).
  L'adresse est revérifiée au moment même de la connexion (pas de « DNS rebinding »).
- Taille limitée (SOURCES_EN_LIGNE_TAILLE_MAX, en octets, 100 Mo par défaut) : le téléchargement s'arrête dès que la limite est franchie.
- Le contenu est reconnu par ses premiers octets (xlsx, xls, parquet), pas seulement par l'extension du lien ; une page web (HTML) est refusée
  avec un message clair (cas fréquent d'un lien « vue » au lieu du lien direct).
Aucune de ces fonctions n'exécute quoi que ce soit du contenu téléchargé."""
import base64
import hashlib
import http.client
import ipaddress
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from engine.i18n import t, tf

TAILLE_MAX_DEFAUT = 1 << 62   # aucune limite par défaut (settings.SOURCES_EN_LIGNE_TAILLE_MAX pour en fixer une)
DELAI = 30
LIEN_MAX = 500   # taille du champ TableDynamique.url_source
JETON_MAX = 4096   # longueur maximale d'un jeton d'accès (les jetons usuels, JWT compris, tiennent largement dedans)
_JETON_OK = re.compile(r"^[\x21-\x7e]+$")   # ASCII imprimable, sans espace ni retour à la ligne : aucune injection possible dans l'en-tête HTTP


class JetonRefuse(ValueError):
    """Le jeton d'accès n'ouvre plus la source (régénéré / révoqué / expiré) ou n'est plus lisible : il faut le reconfigurer (bouton 🔑)."""


class PageWeb(ValueError):
    """Le serveur a renvoyé une page web (HTML) au lieu d'un fichier de données."""


def verifier_jeton(jeton):
    """Jeton d'accès nettoyé (ValueError, message traduit, s'il est vide, trop long ou s'il contient un espace / retour à la ligne)."""
    jeton = (jeton or "").strip()
    if not jeton or len(jeton) > JETON_MAX or not _JETON_OK.match(jeton):
        raise ValueError(t("maj_table.en_ligne_jeton_invalide"))
    return jeton


def _fernet():
    from cryptography.fernet import Fernet
    from django.conf import settings
    cle = hashlib.sha256(("jeton-source-en-ligne:" + settings.SECRET_KEY).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(cle))


def chiffrer_jeton(jeton):
    """Jeton chiffré (Fernet : AES + HMAC, clé dérivée de SECRET_KEY) : seule forme qui traverse Celery et qui est stockée en base."""
    return _fernet().encrypt(verifier_jeton(jeton).encode("utf-8")).decode("ascii")


def dechiffrer_jeton(chiffre):
    """Jeton en clair, ou None si `chiffre` est vide ou illisible (SECRET_KEY changée, donnée altérée)."""
    if not chiffre:
        return None
    try:
        return _fernet().decrypt(chiffre.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 — InvalidToken, ValueError, UnicodeError : tous veulent dire « illisible »
        return None

_EXT_CSV = {"csv", "tsv", "txt"}
_EXT_CONNUES = _EXT_CSV | {"xlsx", "xls", "parquet", "json"}


def _reglages():
    """(réseau privé autorisé, taille maximale en octets) d'après settings.py ; valeurs par défaut hors Django."""
    try:
        from django.conf import settings
        return bool(getattr(settings, "SOURCES_EN_LIGNE_RESEAU_PRIVE", False)), int(getattr(settings, "SOURCES_EN_LIGNE_TAILLE_MAX", TAILLE_MAX_DEFAUT))
    except Exception:
        return False, TAILLE_MAX_DEFAUT


def _adresse_interdite(ip):
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def verifier_lien(url, reseau_prive=False):
    """Refuse (ValueError, message traduit) un lien qui n'est pas une adresse web publique. Renvoie le lien nettoyé."""
    url = (url or "").strip()
    if not url or len(url) > LIEN_MAX:
        raise ValueError(t("maj_table.en_ligne_long") if len(url) > LIEN_MAX else t("maj_table.en_ligne_lien"))
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError(t("maj_table.en_ligne_lien"))
    if reseau_prive:
        return url
    try:
        adresses = {i[4][0].split("%")[0] for i in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError):
        raise ValueError(t("maj_table.en_ligne_inaccessible"))
    if not adresses or any(_adresse_interdite(ipaddress.ip_address(a)) for a in adresses):
        raise ValueError(t("maj_table.en_ligne_prive"))
    return url


class _Redirections(urllib.request.HTTPRedirectHandler):
    """Chaque redirection est revérifiée : un site public ne peut pas renvoyer vers le réseau interne. Avec un jeton d'accès (`hote_jeton` = hôte
    d'origine), toute redirection vers un autre hôte ou hors HTTPS est REFUSÉE : le jeton ne quitte jamais l'adresse saisie."""
    def __init__(self, reseau_prive, hote_jeton=None):
        self._reseau_prive = reseau_prive
        self._hote_jeton = hote_jeton

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        cible = urllib.parse.urljoin(req.full_url, newurl)
        if self._hote_jeton:
            p = urllib.parse.urlsplit(cible)
            if p.scheme != "https" or (p.hostname or "").lower() != self._hote_jeton:
                raise ValueError(t("maj_table.en_ligne_jeton_redirection"))
        verifier_lien(cible, self._reseau_prive)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _connecter(hote, port, delai, reseau_prive):
    """Connexion TCP à une adresse vérifiée AU MOMENT de se connecter (et non résolue une seconde fois après la vérification : un domaine
    hostile ne peut pas répondre d'abord une IP publique, puis 127.0.0.1 — « DNS rebinding »)."""
    infos = socket.getaddrinfo(hote, port, type=socket.SOCK_STREAM)
    if not reseau_prive and any(_adresse_interdite(ipaddress.ip_address(i[4][0].split("%")[0])) for i in infos):
        raise ValueError(t("maj_table.en_ligne_prive"))
    derniere = OSError()
    for i in infos:
        try:
            return socket.create_connection(i[4][:2], delai)
        except OSError as exc:
            derniere = exc
    raise derniere


class _Http(http.client.HTTPConnection):
    def __init__(self, *a, reseau_prive=False, **kw):
        super().__init__(*a, **kw)
        self._reseau_prive = reseau_prive

    def connect(self):
        self.sock = _connecter(self.host, self.port, self.timeout, self._reseau_prive)


class _Https(http.client.HTTPSConnection):
    def __init__(self, *a, reseau_prive=False, **kw):
        super().__init__(*a, **kw)
        self._reseau_prive = reseau_prive

    def connect(self):
        self.sock = self._context.wrap_socket(_connecter(self.host, self.port, self.timeout, self._reseau_prive), server_hostname=self.host)


def _gestionnaires(reseau_prive):
    """Connexions qui revérifient l'adresse réellement jointe. Derrière un proxy (variables d'environnement) la connexion va vers le proxy,
    dont c'est alors le rôle de filtrer : on laisse urllib faire."""
    if urllib.request.getproxies():
        return []
    class H(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(lambda h, **kw: _Http(h, reseau_prive=reseau_prive, **kw), req)
    class S(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(lambda h, **kw: _Https(h, reseau_prive=reseau_prive, **kw), req, context=self._context)
    return [H(), S()]


def _extension_du_lien(url, disposition=""):
    """Extension annoncée : nom de fichier de Content-Disposition en priorité, sinon chemin du lien (sans paramètres)."""
    for source in (_nom_disposition(disposition), urllib.parse.unquote(urllib.parse.urlsplit(url).path)):
        e = os.path.splitext(source.rsplit("/", 1)[-1])[1].lstrip(".").lower()
        if e in _EXT_CONNUES:
            return e
    return ""


def _nom_disposition(disposition):
    for morceau in (disposition or "").split(";"):
        cle, _, val = morceau.strip().partition("=")
        if cle.lower() in ("filename", "filename*") and val:
            return urllib.parse.unquote(val.strip().strip('"').split("''")[-1])
    return ""


def detecter_format(url, brut, type_contenu="", disposition=""):
    """Extension à donner au fichier : csv, xlsx, xls, parquet ou json. Les premiers octets l'emportent sur l'extension du lien.
    ValueError si c'est une page web ou un format inconnu."""
    debut = brut[:2048]
    if debut.startswith(b"PAR1"):
        return "parquet"
    if debut.startswith(b"PK\x03\x04"):
        return "xlsx"
    if debut.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    texte = debut.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if texte.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or b"<html" in texte[:1024] or "html" in (type_contenu or "").lower():
        raise PageWeb(t("maj_table.en_ligne_html"))
    if b"\x00" in debut:
        raise ValueError(t("maj_table.en_ligne_format"))   # binaire inconnu
    e = _extension_du_lien(url, disposition)
    if e == "json" or (not e and texte[:1] in (b"{", b"[")):
        return "json"
    if e in ("xlsx", "xls", "parquet"):
        raise ValueError(t("maj_table.en_ligne_format"))   # annoncé comme binaire, mais le contenu n'en a pas la signature
    return "csv"


def telecharger(url, reseau_prive=None, taille_max=None, jeton=None):
    """Télécharge `url` et renvoie (nom de fichier, octets). ValueError (message traduit) si le lien est refusé, inaccessible, trop gros
    ou de format inconnu. Le nom porte la bonne extension : le reste du code s'en sert pour choisir le lecteur.

    `jeton` (accès protégé) : envoyé en « Authorization: Bearer », uniquement en HTTPS et jamais suivi vers un autre hôte (voir _Redirections) ;
    il n'est ni écrit dans le nom du fichier, ni dans un message d'erreur."""
    defaut_prive, defaut_taille = _reglages()
    reseau_prive = defaut_prive if reseau_prive is None else reseau_prive
    taille_max = defaut_taille if taille_max is None else taille_max
    url = verifier_lien(url, reseau_prive)
    entetes = {"User-Agent": "Agent-GY/1.0", "Accept": "text/csv, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, application/vnd.apache.parquet, application/octet-stream, */*;q=0.5"}
    hote_jeton = None
    if jeton:
        jeton = verifier_jeton(jeton)
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https":
            raise ValueError(t("maj_table.en_ligne_jeton_https"))
        hote_jeton = (p.hostname or "").lower()
        entetes["Authorization"] = "Bearer " + jeton
    ouvreur = urllib.request.build_opener(_Redirections(reseau_prive, hote_jeton), *_gestionnaires(reseau_prive))
    requete = urllib.request.Request(url, headers=entetes)
    try:
        with ouvreur.open(requete, timeout=DELAI) as r:
            annonce = r.headers.get("Content-Length")
            if annonce and annonce.isdigit() and int(annonce) > taille_max:
                raise ValueError(tf("maj_table.en_ligne_taille", v1=taille_max // (1024 * 1024)))
            morceaux, lu = [], 0
            while True:
                m = r.read(1024 * 256)
                if not m:
                    break
                lu += len(m)
                if lu > taille_max:
                    raise ValueError(tf("maj_table.en_ligne_taille", v1=taille_max // (1024 * 1024)))
                morceaux.append(m)
            type_contenu, disposition = r.headers.get_content_type(), r.headers.get("Content-Disposition", "")
    except ValueError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):   # accès refusé : avec jeton, il n'existe plus (régénéré côté application distante) ; sans jeton, la source est protégée
            raise JetonRefuse(t("maj_table.en_ligne_jeton_refuse")) if jeton else ValueError(t("maj_table.en_ligne_protege"))
        raise ValueError(t("maj_table.en_ligne_inaccessible"))
    except (urllib.error.URLError, OSError, UnicodeError):
        raise ValueError(t("maj_table.en_ligne_inaccessible"))
    brut = b"".join(morceaux)
    if not brut:
        raise ValueError(t("maj_table.en_ligne_inaccessible"))
    try:
        ext = detecter_format(url, brut, type_contenu, disposition)
    except PageWeb:
        if jeton: raise JetonRefuse(t("maj_table.en_ligne_jeton_refuse"))   # avec jeton, une page web = presque toujours la page de connexion de l'autre application (jeton inconnu)
        raise
    return f"enligne_{hashlib.sha1(url.encode()).hexdigest()[:10]}.{ext}", brut
