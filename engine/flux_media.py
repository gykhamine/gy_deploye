"""FLUX MÉDIA TEMPS RÉEL — champs spéciaux « micro », « caméra », « diffusion micro », « diffusion caméra ».

Ces quatre champs ne sont JAMAIS des colonnes de données : une table qui en contient un n'a que ce champ (une seule cellule,
aucune ligne) et il n'est lu par aucun autre moteur (formules, IA, PDF...). Aucun enregistrement automatique : le flux passe en
direct, l'agent décide d'enregistrer (image, vidéo, son, vidéo + son) ; chaque fichier enregistré est notifié.

Configuration = JSON dans `ChampDynamique.options_choix` :
  {"source": "local:0" | "rtsp://..." | "http://..." | "udp://0.0.0.0:5000" | "tcp://0.0.0.0:5000",
   "audio": (caméra seulement, même syntaxe que `source` d'un micro), "rate": 16000}
  - caméra : « local:N » (webcam), URL RTSP/HTTP/RTMP/UDP (caméra IP, téléphone) → OpenCV.
  - micro  : « local:N » (PyAudio) ou « tcp://0.0.0.0:PORT » / « udp://0.0.0.0:PORT » = serveur d'écoute qui reçoit du PCM
             16 bits mono (little-endian) à `rate` Hz envoyé par un micro externe (ex. `ffmpeg -f alsa -i hw:1 -f s16le tcp://IP:PORT`).
Le serveur d'écoute / la capture ne démarre qu'à la première demande et s'arrête seul après 30 s sans spectateur.
  - « navigateur » (caméra ET micro) : le navigateur d'un téléphone / PC envoie lui-même des images JPEG et du PCM 16 bits
    (voir `Hub.pousser` et les routes `/api/flux/<id>/envoi/video|audio/`) ; même hub, donc même direct, même enregistrement.
  - `sources` : mémoire des sources déjà utilisées (bascule en un clic) ; `source` / `audio` restent la source ACTIVE.
  - `table_id` : TABLE DE DONNÉES liée au flux (obligatoire pour enregistrer, voir « TABLE LIÉE » en bas de ce fichier) : chaque
    capture devient la valeur d'une colonne média (image / vidéo / son, ajoutée à cette table) d'une ligne de cette table, plutôt
    qu'un fichier posé en vrac dans le dossier média.
"""
from engine.i18n import t as _i18n_t
from engine.i18n import t as _i18n_t, tf
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import wave
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

TYPES_FLUX = ("micro", "camera", "diffusion_micro", "diffusion_camera")
TYPES_VIDEO = ("camera", "diffusion_camera")
TYPES_DIFFUSION = ("diffusion_micro", "diffusion_camera")
SCHEMAS_VIDEO = ("rtsp", "rtsps", "rtmp", "http", "https", "udp", "tcp")  # rien d'autre (jamais un chemin de fichier)
SCHEMAS_AUDIO = ("udp", "tcp")
INACTIVITE_S = 30
SOURCE_NAVIGATEUR = "navigateur"      # le navigateur de l'utilisateur pousse lui-même le flux


def erreur_structure(champs):
    """Message si la structure demandée mélange un champ de flux avec autre chose (une seule cellule), sinon None."""
    types = [c.get("type") for c in champs or []]
    if any(t in TYPES_FLUX for t in types) and len(types) != 1:
        return _i18n_t("engine/flux_media.py:g:50:16")
    return None


def table_de_flux(table):
    """Le champ de flux de la table (ou None)."""
    return table.champs.filter(type_champ__in=TYPES_FLUX).first()


def config_champ(champ):
    try:
        cfg = json.loads(champ.options_choix or "{}")
    except ValueError:
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def valider_source(source, video):
    """Retourne un message d'erreur, ou None si la source est acceptable."""
    s = str(source or "").strip()
    if s == SOURCE_NAVIGATEUR:
        return None
    if s.startswith("local:"):
        return None if s[6:].isdigit() else _i18n_t("engine/flux_media.py:g:73:45")
    schema = urlparse(s).scheme
    if schema not in (SCHEMAS_VIDEO if video else SCHEMAS_AUDIO):
        return _i18n_t("engine/flux_media.py:g:76:16") + (_i18n_t("engine/flux_media.py:g:76:40") if video else _i18n_t("engine/flux_media.py:g:76:95"))
    return None


class _Hub:
    """Une capture partagée par tous les spectateurs d'un champ (vidéo ou audio)."""
    def __init__(self, source, video, rate):
        self.source, self.video, self.rate = source, video, rate
        self.arret = threading.Event()
        self.cond = threading.Condition()
        self.seq, self.dernier = 0, None            # vidéo : dernier JPEG ; audio : fenêtre de blocs PCM
        self.blocs = deque(maxlen=64)
        self.vu = time.time()
        self.erreur = ""
        self.enregistreur = None                    # callable(donnee) appelé à chaque image / bloc PCM
        self.taille = (0, 0)
        self.fps, self.t_prec = 0.0, 0.0            # cadence mesurée (pour la vidéo enregistrée)
        self.emetteur, self.t_poussee = None, 0.0    # source « navigateur » : qui émet
        threading.Thread(target=self._boucle, daemon=True).start()

    def _publier(self, donnee, brut=None):
        if self.video:
            maintenant = time.time()
            if self.t_prec:
                mesure = 1.0 / max(maintenant - self.t_prec, 0.001)
                self.fps = max(1.0, min(30.0, 0.9 * self.fps + 0.1 * mesure if self.fps else mesure))
            self.t_prec = maintenant
        with self.cond:
            self.seq += 1
            if self.video:
                self.dernier = donnee
            else:
                self.blocs.append((self.seq, donnee))
            self.cond.notify_all()
        rec = self.enregistreur
        if rec:
            try:
                rec(brut if brut is not None else donnee)
            except Exception:
                logger.exception(_i18n_t("engine/flux_media.py:g:116:34"))

    def attendre(self, apres, delai=5):
        """Bloque jusqu'à une donnée plus récente que `apres` ; retourne (seq, donnee|blocs) ou (apres, None)."""
        self.vu = time.time()
        with self.cond:
            self.cond.wait_for(lambda: self.seq > apres or self.arret.is_set(), delai)
            if self.seq <= apres:
                return apres, None
            if self.video:
                return self.seq, self.dernier
            return self.seq, b"".join(b for s, b in self.blocs if s > apres)

    def _boucle(self):
        try:
            if self.source == SOURCE_NAVIGATEUR:
                self._boucle_poussee()
            else:
                (self._boucle_video if self.video else self._boucle_audio)()
        except Exception as e:
            self.erreur = str(e)
            logger.warning("flux_media %s: %s", self.source, e)
        finally:
            self.arret.set()
            with self.cond:
                self.cond.notify_all()

    def _actif(self):
        return not self.arret.is_set() and time.time() - self.vu < INACTIVITE_S

    # ── source « navigateur » : rien à capturer, le navigateur envoie (voir pousser) ──
    def _boucle_poussee(self):
        while self._actif():
            self.arret.wait(1)

    def pousser(self, donnee, utilisateur_id, rate=None):
        """Reçoit une image JPEG (vidéo) ou un bloc PCM 16 bits mono (audio) envoyé par un navigateur."""
        maintenant = time.time()
        if self.emetteur not in (None, utilisateur_id) and maintenant - self.t_poussee < 10:
            raise ValueError(_i18n_t("engine/flux_media.py:g:154:30"))
        if self.video:
            if donnee[:2] != b"\xff\xd8" or len(donnee) > 2_000_000:
                raise ValueError(_i18n_t("engine/flux_media.py:g:157:34"))
            brut = None
            if self.enregistreur or not self.taille[0]:     # décodage seulement si nécessaire
                try:
                    import cv2
                    import numpy as np
                    brut = cv2.imdecode(np.frombuffer(donnee, np.uint8), cv2.IMREAD_COLOR)
                    if brut is not None:
                        self.taille = (brut.shape[1], brut.shape[0])
                except ImportError:
                    pass
        else:
            if len(donnee) > 262_144:
                raise ValueError(_gt("rg5.msg.033"))
            if rate and self.seq == 0 and 8000 <= rate <= 48000:
                self.rate = rate
            donnee, brut = donnee[: len(donnee) - len(donnee) % 2], None
            if not donnee:
                return
        self.emetteur, self.t_poussee, self.vu = utilisateur_id, maintenant, maintenant
        self._publier(donnee, brut)

    # ── vidéo (OpenCV) ──────────────────────────────────────────────────
    def _boucle_video(self):
        import cv2
        src = int(self.source[6:]) if self.source.startswith("local:") else self.source
        cap = cv2.VideoCapture(src)
        while self._actif():
            ok, img = cap.read()
            if not ok:
                self.erreur = "Flux interrompu, reconnexion..."
                cap.release()
                time.sleep(2)
                cap = cv2.VideoCapture(src)
                continue
            self.erreur = ""
            self.taille = (img.shape[1], img.shape[0])
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok:
                self._publier(jpg.tobytes(), img)
            time.sleep(0.03)
        cap.release()

    # ── audio (PyAudio local, ou serveur d'écoute TCP/UDP) ────────────────
    def _boucle_audio(self):
        if self.source.startswith("local:"):
            import pyaudio
            pa = pyaudio.PyAudio()
            flux = pa.open(format=pyaudio.paInt16, channels=1, rate=self.rate, input=True,
                           input_device_index=int(self.source[6:]), frames_per_buffer=1024)
            try:
                while self._actif():
                    self._publier(flux.read(1024, exception_on_overflow=False))
            finally:
                flux.close()
                pa.terminate()
            return
        u = urlparse(self.source)
        udp = u.scheme == "udp"
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((u.hostname or "0.0.0.0", u.port))
        srv.settimeout(1)
        conn = srv
        if not udp:
            srv.listen(1)
        try:
            while self._actif():
                try:
                    if not udp and conn is srv:
                        conn, _ = srv.accept()
                        conn.settimeout(1)
                    data = conn.recv(4096)
                except socket.timeout:
                    continue
                if not data:                      # l'émetteur s'est déconnecté : on réattend une connexion
                    conn.close()
                    conn = srv
                    continue
                self._publier(data[: len(data) - len(data) % 2])
        finally:
            if conn is not srv:
                conn.close()
            srv.close()


_hubs = {}
_verrou = threading.Lock()


def hub(champ, audio=False):
    """Le hub (démarré si besoin) de la vidéo ou de l'audio d'un champ."""
    cfg = config_champ(champ)
    video = champ.type_champ in TYPES_VIDEO and not audio
    source = cfg.get("audio" if audio and champ.type_champ in TYPES_VIDEO else "source")
    if not source:
        raise ValueError(_i18n_t("engine/flux_media.py:g:253:26"))
    erreur = valider_source(source, video)
    if erreur:
        raise ValueError(erreur)
    cle = (champ.id, audio)
    with _verrou:
        h = _hubs.get(cle)
        if h is None or h.arret.is_set() or h.source != source:
            if h:
                h.arret.set()
            h = _hubs[cle] = _Hub(source, video, int(cfg.get("rate") or 16000))
        h.vu = time.time()
    return h


def entete_wav(rate):
    """En-tête WAV de longueur inconnue (lecture en continu dans <audio>)."""
    import struct
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))


def flux_mjpeg(h):
    seq = 0
    while not h.arret.is_set():
        seq, jpg = h.attendre(seq)
        if jpg:
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n"


def flux_wav(h):
    yield entete_wav(h.rate)
    seq = h.seq
    while not h.arret.is_set():
        seq, pcm = h.attendre(seq)
        if pcm:
            yield pcm


# ── enregistrement À LA DEMANDE de l'agent ────────────────────────────────
_enregistrements = {}


def dossier(entreprise_id):
    d = Path(settings.MEDIA_ROOT) / "flux_media" / str(entreprise_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _nom(champ, ext):
    return f"{time.strftime('%Y%m%d_%H%M%S')}_c{champ.id}.{ext}"


def capturer_image(champ):
    """Une photo (frame) du flux vidéo ; retourne le chemin relatif sous MEDIA_ROOT."""
    h = hub(champ)
    _, jpg = h.attendre(0, delai=8)
    if not jpg:
        raise ValueError(h.erreur or _i18n_t("engine/flux_media.py:g:311:38"))
    chemin = dossier(champ.table.entreprise_id) / _nom(champ, "jpg")
    chemin.write_bytes(jpg)
    return chemin


def demarrer_enregistrement(champ, mode):
    """mode : video | audio | av (vidéo + son si la caméra a une source audio)."""
    if champ.id in _enregistrements:
        raise ValueError(_i18n_t("engine/flux_media.py:g:320:26"))
    base = dossier(champ.table.entreprise_id)
    rec = {"mode": mode, "fichiers": {}, "ecrivains": []}
    if mode in ("video", "av"):
        import cv2
        h = hub(champ)
        _, jpg = h.attendre(0, delai=8)
        if not h.taille[0]:
            raise ValueError(h.erreur or _i18n_t("engine/flux_media.py:g:328:42"))
        v = base / _nom(champ, "mp4")
        w = cv2.VideoWriter(str(v), cv2.VideoWriter_fourcc(*"mp4v"), round(h.fps or 20), h.taille)
        h.enregistreur = lambda img, w=w: w.write(img)
        rec["fichiers"]["video"] = v
        rec["ecrivains"].append((h, w.release))
    if mode == "audio" or (mode == "av" and config_champ(champ).get("audio")):
        ha = hub(champ, audio=True)
        a = base / _nom(champ, "wav")
        w = wave.open(str(a), "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(ha.rate)
        ha.enregistreur = w.writeframes
        rec["fichiers"]["audio"] = a
        rec["ecrivains"].append((ha, w.close))
    if not rec["fichiers"]:
        raise ValueError(_i18n_t("engine/flux_media.py:g:345:26"))
    _enregistrements[champ.id] = rec
    return rec


def arreter_enregistrement(champ):
    """Ferme les fichiers (mux vidéo + son avec ffmpeg si présent) ; retourne la liste des chemins enregistrés."""
    rec = _enregistrements.pop(champ.id, None)
    if not rec:
        raise ValueError(_i18n_t("engine/flux_media.py:g:354:26"))
    for h, fermer in rec["ecrivains"]:
        h.enregistreur = None
        fermer()
    f = rec["fichiers"]
    if "video" in f and "audio" in f and shutil.which("ffmpeg"):
        sortie = f["video"].with_name(f["video"].stem + "_av.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-i", str(f["video"]), "-i", str(f["audio"]), "-c:v", "copy", "-c:a", "aac",
                            "-shortest", str(sortie)], capture_output=True)
        if r.returncode == 0:
            os.remove(f["video"])
            os.remove(f["audio"])
            return [sortie]
    return list(f.values())


def en_cours(champ_id):
    return champ_id in _enregistrements


def notifier_fichiers(champ, chemins):
    from .notifications import _creer_notifications
    noms = ", ".join(p.name for p in chemins)
    _creer_notifications(champ.table.entreprise, "fichier_enregistre", tf("engine/flux_media.py:g:377:72", v1=champ.table.nom),
                         tf("engine/flux_media.py:g:378:26", v1=champ.nom, v2=noms), table=champ.table)


# ── archives : fichiers enregistrés d'un flux (lister, supprimer) ──────────
def _est_de(champ, nom):
    """Vrai si le fichier a été enregistré par ce champ (…_c<id>.ext ou …_c<id>_av.mp4), jamais par un autre (c9 ≠ c90)."""
    return re.search(r"_c%d(_av)?\.[a-z0-9]+$" % champ.id, nom) is not None


def lister_fichiers(champ):
    """Fichiers enregistrés par ce flux, du plus récent au plus ancien : [{nom, taille, date}]."""
    d = dossier(champ.table.entreprise_id)
    fichiers = sorted((p for p in d.iterdir() if _est_de(champ, p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"nom": p.name, "taille": p.stat().st_size, "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))}
            for p in fichiers if p.is_file()]


def supprimer_fichier(champ, nom):
    """Supprime un fichier de ce flux (le nom doit être exactement un fichier listé : pas de chemin)."""
    if nom not in [f["nom"] for f in lister_fichiers(champ)]:
        raise ValueError(_i18n_t("engine/flux_media.py:g:398:26"))
    os.remove(dossier(champ.table.entreprise_id) / nom)


# ── TABLE LIÉE : chaque flux est obligatoirement rattaché à une table de données ──────────────────────────────────────────
# Une capture (photo, vidéo, son) n'est plus un fichier orphelin : elle devient la valeur de la colonne média de la table liée
# (colonnes « flux_image », « flux_video », « flux_son », ajoutées à la table sans toucher à ses autres colonnes ni à ses lignes)
# sur une LIGNE, qui peut aussi porter les autres colonnes de la table (le formulaire de la table complète celui du flux).
# Le fichier est enregistré comme celui d'un champ fichier ordinaire (dossier champs/…, même affichage et mêmes droits d'accès
# côté agent et côté client) ; l'original archivé est retiré une fois la ligne créée (sauf si on le garde pour une autre ligne).
# Le ménage inverse (ligne supprimée, colonne supprimée) est dans flux_nettoyage.py.
GENRES = ("image", "video", "son")                                     # genre d'une capture = type de la colonne qui la reçoit
SLUG_COLONNE = {"image": "flux_image", "video": "flux_video", "son": "flux_son"}
EXT_GENRE = {"jpg": "image", "mp4": "video", "wav": "son"}


def genres_flux(champ):
    """Genres de captures que ce flux peut produire : caméra = photo + vidéo, micro = son."""
    return ("image", "video") if champ.type_champ in TYPES_VIDEO else ("son",)


def genre_fichier(nom):
    """Genre d'une capture d'après son extension (jpg, mp4, wav), sinon None."""
    m = re.search(r"\.([a-z0-9]+)$", str(nom or ""))
    return EXT_GENRE.get(m.group(1)) if m else None


def url_fichier(champ, nom):
    return settings.MEDIA_URL + "flux_media/%s/%s" % (champ.table.entreprise_id, nom)


def _parties(table):
    """La table et, si elle appartient à un groupe, toutes les parties du groupe (même structure, écritures dans la dernière)."""
    if not table.groupe_id:
        return [table]
    return [table] + list(table.groupe.tables.exclude(id=table.id))


def table_liee(champ):
    """La table de données liée au flux, ou None (jamais liée, ou supprimée depuis)."""
    from .models import TableDynamique
    try:
        tid = int(config_champ(champ).get("table_id") or 0)
    except (TypeError, ValueError):
        return None
    table = TableDynamique.objects.filter(id=tid, entreprise_id=champ.table.entreprise_id).first() if tid else None
    return table if table and not table_de_flux(table) else None


def erreur_table_cible(table):
    """Message si cette table ne peut pas recevoir de captures, sinon None."""
    if table is None:
        return _i18n_t("engine/flux_media.py:err_table_introuvable")
    if table_de_flux(table):
        return _i18n_t("engine/flux_media.py:err_table_flux")
    if table.est_nosql:
        return _i18n_t("engine/flux_media.py:err_table_nosql")
    if table.est_table_membres:
        return _i18n_t("engine/flux_media.py:err_table_membres")
    return None


def assurer_colonne(table, genre):
    """Ajoute à la table (et à chaque partie de son groupe : structure commune) la colonne média du genre si elle n'y est pas
    déjà — jamais de modification ni de suppression d'une colonne existante. Retourne le slug de la colonne."""
    from django.db.models import Max
    from .models import ChampDynamique
    slug = SLUG_COLONNE[genre]
    nom = {"image": _i18n_t("engine/flux_media.py:col_image"), "video": _i18n_t("engine/flux_media.py:col_video"),
           "son": _i18n_t("engine/flux_media.py:col_son")}[genre]
    for cible in _parties(table):
        existante = cible.champs.filter(slug=slug).first()
        if existante is not None:
            if existante.type_champ != genre:
                raise ValueError(tf("engine/flux_media.py:err_colonne_type", v1=slug))
            continue
        plafond = cible.plafond_colonnes
        if plafond is not None and cible.champs.count() + 1 > plafond:
            raise ValueError(tf("engine/flux_media.py:err_plafond_colonnes", v1=1, v2=plafond))
        dernier = cible.champs.aggregate(m=Max("ordre"))["m"]
        ChampDynamique.objects.create(table=cible, nom=nom, slug=slug, type_champ=genre, obligatoire=False,
                                      ordre=0 if dernier is None else dernier + 1, options_choix="")
        if cible.provient_fichier:      # comme une restructuration : la table devient éditable comme une table native
            cible.provient_fichier = False
            cible.save(update_fields=["provient_fichier"])
    return slug


def lier_table(champ, table):
    """Lie le flux à `table` (obligatoire) et ajoute les colonnes média dont il a besoin. Tout ou rien : en cas d'erreur
    (ValueError), ni colonne ni lien n'est écrit. Retourne les slugs des colonnes ajoutées à la table (pour le journal)."""
    from django.db import transaction
    erreur = erreur_table_cible(table)
    if erreur:
        raise ValueError(erreur)
    with transaction.atomic():
        avant = set(table.champs.values_list("slug", flat=True))
        for genre in genres_flux(champ):
            assurer_colonne(table, genre)
        cfg = config_champ(champ)
        cfg["table_id"] = table.id
        champ.options_choix = json.dumps(cfg)
        champ.save(update_fields=["options_choix"])
        return [SLUG_COLONNE[g] for g in genres_flux(champ) if SLUG_COLONNE[g] not in avant]


def champs_formulaire(champ):
    """Colonnes de la table liée que le formulaire du flux propose EN PLUS de la capture : tout sauf les colonnes calculées ou
    écrites par le serveur et les colonnes média gérées par le flux lui-même. [] si le flux n'est pas lié."""
    table = table_liee(champ)
    if table is None:
        return []
    from .vue_groupe import derniere_partie
    exclus = {"formule", "constante", "horodatage", "id_client"} | set(TYPES_FLUX)
    gerees = set(SLUG_COLONNE.values())
    return [c for c in derniere_partie(table).champs.all().order_by("ordre", "id")
            if c.type_champ not in exclus and c.slug not in gerees]


def rattacher_captures(entreprise_id, table, flux_id, noms):
    """Prépare le rattachement de captures archivées à une nouvelle ligne de `table` (celle qui reçoit la ligne) :
    retourne {slug de la colonne média: fichier}, à fusionner avec les fichiers téléversés du formulaire — la ligne les
    enregistre comme n'importe quel champ fichier. ValueError si le flux, la capture ou la table ne correspondent pas."""
    from django.core.files import File
    from .models import ChampDynamique
    champ = None
    if str(flux_id or "").isdigit():
        champ = ChampDynamique.objects.filter(id=int(flux_id), type_champ__in=TYPES_FLUX,
                                              table__entreprise_id=entreprise_id).select_related("table").first()
    liee = table_liee(champ) if champ else None
    if champ is None or liee is None:
        raise ValueError(_i18n_t("engine/flux_media.py:err_non_lie"))
    if table.id not in {p.id for p in _parties(liee)}:
        raise ValueError(_i18n_t("engine/flux_media.py:err_capture_table"))
    disponibles = {f["nom"] for f in lister_fichiers(champ)}
    genres = {}
    for nom in noms:
        genre = genre_fichier(nom)
        if nom not in disponibles or genre is None:
            raise ValueError(_i18n_t("engine/flux_media.py:err_capture_inconnue"))
        if genre in genres:
            raise ValueError(_i18n_t("engine/flux_media.py:err_capture_doublon"))
        genres[genre] = nom
    # la colonne a pu être supprimée de la table depuis le lien : elle est recréée (comme à la liaison), jamais d'erreur muette
    return {assurer_colonne(liee, genre): File(open(dossier(entreprise_id) / nom, "rb"), name=nom) for genre, nom in genres.items()}


def finaliser_captures(entreprise_id, noms, garder, fichiers):
    """Après la création de la ligne : ferme les fichiers lus et retire les originaux archivés (ils vivent désormais dans la
    ligne), sauf si la personne garde la capture pour une autre ligne."""
    for f in fichiers.values():
        try:
            f.close()
        except (AttributeError, OSError, ValueError):
            pass
    if garder:
        return
    for nom in noms:
        try:
            os.remove(dossier(entreprise_id) / nom)
        except OSError:
            logger.warning(_gt("rg6.msg.010"), nom)
