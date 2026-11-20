"""Sécurité transversale (audit entreprise 2026-09-23) — une seule implémentation partagée.

- chemin_media_sur(rel) : résout un chemin relatif SOUS MEDIA_ROOT, ou None (« ../ », chemin absolu,
  lien symbolique qui sort du dossier). À utiliser pour TOUTE valeur de cellule/fichier venant d'un
  utilisateur ou d'un script IA avant d'ouvrir un fichier (ia_integration.py, reports.py).
- servir_media : sert /media/... derrière authentification, jamais les dossiers internes (scripts IA,
  modèles entraînés), et rapports/documents générés seulement à l'entreprise propriétaire (noms
  prévisibles : rapport_<id>.pdf). Remplace django.conf.urls.static (public, et muet sans DEBUG).
- url_suivante_sure(request, url) : anti-redirection ouverte (paramètre ?next=).
- url_externe_sure(url) : anti-SSRF avant tout téléchargement d'une URL saisie dans une cellule.
- verifier_fichier(f, type_champ) : refuse un fichier corrompu ou piégé À L'ENREGISTREMENT d'un formulaire (avant toute écriture).
- journal : logger « engine.securite » (fichier logs/securite.log, voir settings.LOGGING).
"""
from engine.i18n import t, tf
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
import logging
import mimetypes
import os
import re
from pathlib import Path

from django.conf import settings
from .stockage import SYSTEME
from django.http import FileResponse, Http404
from django.utils.http import url_has_allowed_host_and_scheme
from engine.i18n import t as _gt  # noqa: E402

journal = logging.getLogger("engine.securite")

# Jamais servis par HTTP : code généré par l'IA, modèles entraînés, fichiers de travail.
_DOSSIERS_INTERNES = ("scripts_agent/", "scripts_documents/", "modeles_ia/")
# Servis sans connexion : logos affichés sur les pages publiques (connexion, inscription, accès client).
_DOSSIERS_PUBLICS = ("logos/", "marque/")   # marque/ : images d'apparence (nom uuid) affichées par les pages publiques de chaque entreprise


def chemin_media_sur(rel) -> Path | None:
    """Chemin absolu de `rel` s'il reste strictement sous MEDIA_ROOT, sinon None."""
    if not rel:
        return None
    racine = Path(settings.MEDIA_ROOT).resolve()
    rel = str(rel).replace("\\", "/")
    if rel.startswith(settings.MEDIA_URL):
        rel = rel[len(settings.MEDIA_URL):]
    try:
        cible = (racine / rel.lstrip("/")).resolve()
    except (OSError, ValueError):
        return None
    if cible == racine or racine not in cible.parents:
        journal.warning(t("engine/securite.py:g:44:25"), rel)
        return None
    if cible.relative_to(racine).parts[0] == SYSTEME:   # base, modèle fantôme, images à clés API : jamais lus ni servis via un chemin média
        journal.warning(t("engine/securite.py:g:47:25"), rel)
        return None
    return cible


def url_externe_sure(url: str) -> bool:
    """Anti-SSRF : une URL venant d'une cellule ne doit jamais faire interroger par le serveur une
    adresse interne (127.0.0.1, réseau local, métadonnées cloud 169.254.x.x…)."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        for info in socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                journal.warning(t("engine/securite.py:g:65:33"), url)
                return False
        return True
    except (OSError, ValueError):
        return False


def url_suivante_sure(request, url, defaut="/"):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return defaut


_DOSSIERS_EQUIPE = ("rapports/", "documents/", "archives_cycle/", "flux_media/")
_ACTIFS_ENTITE = ("documents/photo_entite/", "documents/cachets/", "documents/signatures/")


def _proprietaire_ok(request, rel) -> bool:
    """Fichier d'une entreprise : lisible par ses seuls membres. REFUS PAR DÉFAUT : un fichier que la base ne rattache à aucune des
    entreprises de l'utilisateur n'est jamais servi (avant, tout dossier hors rapports/ et documents/ l'était à n'importe quel connecté)."""
    from .models import MembreEntreprise
    entreprises = set(MembreEntreprise.objects.filter(utilisateur=request.user).values_list("entreprise_id", flat=True))
    if rel.startswith(_DOSSIERS_EQUIPE) and not rel.startswith(_ACTIFS_ENTITE):
        # Rapports, documents, archives de cycle et enregistrements des flux : l'équipe seulement (un client est membre de
        # l'entreprise mais ne doit pas lire les factures des autres en devinant « rapport_<id>.pdf »).
        entreprises = set(MembreEntreprise.objects.filter(utilisateur=request.user).exclude(role="lecteur")
                          .values_list("entreprise_id", flat=True))
        if rel.startswith("flux_media/"):   # flux_media/<id de l'entreprise>/… : cette entreprise seulement
            morceaux = rel.split("/")
            return len(morceaux) > 2 and morceaux[1] in {str(i) for i in entreprises}
    return fichier_de_entreprises(rel, entreprises)


_CHEMINS_ENTREPRISE = {}


def _chemin_entreprise(modele, profondeur=3, vus=()):
    """Chemin ORM (« entreprise », « table__entreprise »…) qui mène d'un modèle à son entreprise, None s'il n'y en a pas."""
    if modele._meta.label == "engine.Entreprise":
        return "id"
    cles = [f for f in modele._meta.get_fields() if getattr(f, "many_to_one", False) and getattr(f, "concrete", False) and f.related_model is not None]
    for f in cles:
        if f.related_model._meta.label == "engine.Entreprise":
            return f.name
    if profondeur:
        for f in cles:
            if f.related_model is modele or f.related_model in vus:
                continue
            sous = _chemin_entreprise(f.related_model, profondeur - 1, vus + (modele,))
            if sous:
                return f"{f.name}__{sous}"
    return None


def _fichier_de_cellule(rel, ids):
    """champs/<table>/<colonne>/<fichier> : le fichier est la valeur d'une cellule d'une table de ces entreprises, ou d'une table
    qu'une autre entreprise leur a mise à disposition (PartageInterAgent, lecture seule)."""
    from django.db.models import Q
    from .models import Enregistrement, PartageInterAgent, ValeurChamp
    morceaux = rel.split("/")
    if len(morceaux) < 4:
        return False
    url = settings.MEDIA_URL + rel
    partagees = list(PartageInterAgent.objects.filter(entreprise_cible_id__in=ids, actif=True, type_objet="table",
                                                      table__slug=morceaux[1]).values_list("table_id", flat=True))
    portee = Q(champ__table__entreprise_id__in=ids) | Q(champ__table_id__in=partagees)
    cellules = ValeurChamp.objects.filter(portee, champ__table__slug=morceaux[1], champ__slug=morceaux[2])
    if cellules.filter(valeur__in=[url, rel]).exists() or cellules.filter(valeur__contains=rel).exists():
        return True
    lignes = Enregistrement.objects.filter(Q(table__entreprise_id__in=ids) | Q(table_id__in=partagees), table__slug=morceaux[1])
    return lignes.filter(valeurs_json__icontains=rel).exists()   # tables NoSQL


def fichier_de_entreprises(rel, entreprises) -> bool:
    """Un fichier par entreprise : chaque entreprise est interrogée dans son propre fichier SQLite."""
    from . import base_par_entreprise as _bpe
    if not _bpe.actif():
        return _fichier_de_entreprises_base(rel, entreprises)
    for eid in list(entreprises or ()):
        with _bpe.dans_entreprise(eid):
            if _fichier_de_entreprises_base(rel, [eid]):
                return True
    return False


def _fichier_de_entreprises_base(rel, entreprises) -> bool:
    """True si /media/<rel> est rattaché, EN BASE, à l'une de ces entreprises (champ fichier d'un modèle de l'application, ou cellule
    d'une table). Tout le reste est refusé."""
    ids = list(entreprises or ())
    if not ids:
        return False
    try:
        if rel.startswith("champs/"):
            return _fichier_de_cellule(rel, ids)
        from django.apps import apps
        from django.db import models as dm
        for modele in apps.get_app_config("engine").get_models():
            for champ in modele._meta.get_fields():
                if not isinstance(champ, dm.FileField):
                    continue
                dossier = champ.upload_to
                if isinstance(dossier, str) and not rel.startswith(dossier.split("%")[0]):
                    continue
                if modele not in _CHEMINS_ENTREPRISE:
                    _CHEMINS_ENTREPRISE[modele] = _chemin_entreprise(modele)
                lien = _CHEMINS_ENTREPRISE[modele]
                if lien and modele.objects.filter(**{champ.name: rel, f"{lien}__in": ids}).exists():
                    return True
    except Exception:
        journal.exception("fichier_de_entreprises: contrôle impossible, accès refusé (%s)", rel)
    return False


# ── Entreprise de la requête (thème, logo, langue) ────────────────────────────────────────────────────────────────────
def entreprise_de_requete(request):
    """Entreprise dont l'identité s'applique à CETTE requête : celle du compte connecté (environnement actif en premier), sinon celle de la
    page publique visitée (/client/<slug>/, /login/?e=<slug>, lien d'inscription : indice gardé en session), sinon — installation à
    entreprise unique seulement — cette entreprise. None = identité d'origine de l'application. Ne dépend pas de request.user (la langue
    est posée avant l'authentification) et n'est calculée qu'une fois par requête."""
    memo = getattr(request, "_entreprise_requete", None)
    if memo is not None:
        return memo or None
    e = None
    try:
        from .models import Entreprise, MembreEntreprise
        session = getattr(request, "session", None)
        uid = session.get("_auth_user_id") if session is not None else None
        if uid:
            membres = MembreEntreprise.objects.filter(utilisateur_id=uid).select_related("entreprise")
            choisie = session.get("entreprise_choisie")   # entreprise choisie sur la page de connexion (voir choisir_entreprise)
            m = (membres.filter(entreprise_id=choisie).first() if choisie else None) or membres.first()
            e = m.entreprise if m else None
        elif session is not None and session.get("entreprise_publique"):
            e = Entreprise.objects.filter(slug=session.get("entreprise_publique")).first()
        if e is None and not getattr(settings, "GYPLATFORM_MULTI_ENTREPRISE", False):
            une = list(Entreprise.objects.order_by("id")[:2])
            e = une[0] if len(une) == 1 else None
    except Exception:
        journal.debug("entreprise_de_requete: base indisponible", exc_info=True)
    request._entreprise_requete = e or False
    return e


def noter_entreprise_publique(request, slug):
    """Pages publiques d'une entreprise : retient son slug en session pour que le thème, le logo et la langue de CETTE entreprise
    s'affichent (connexion, inscription, accès client). Slug inconnu : ignoré."""
    try:
        from .models import Entreprise
        slug = str(slug or "")[:60]
        if slug and Entreprise.objects.filter(slug=slug).exists():
            request.session["entreprise_publique"] = slug
            request._entreprise_requete = None
    except Exception:
        journal.debug("noter_entreprise_publique: ignoré", exc_info=True)


def oublier_entreprise_publique(request):
    """Retour au thème par défaut sur les pages publiques : l'entreprise notée est oubliée (choix « — Choisir une entreprise — »)."""
    try:
        request.session.pop("entreprise_publique", None)
        request._entreprise_requete = None
    except Exception:
        journal.debug("oublier_entreprise_publique: ignoré", exc_info=True)


def choisir_entreprise(request, utilisateur):
    """À la connexion : l'entreprise choisie sur la page de connexion devient l'entreprise de la session, si le compte en est membre.

    Rend True si la connexion peut continuer ; False si un choix existe et que ce compte n'appartient PAS à cette entreprise (il ne se connecte
    pas « dans » l'entreprise d'un autre). Sans choix, ou pour le super administrateur sans rattachement : l'entreprise du compte, comme avant."""
    try:
        from .models import Entreprise, MembreEntreprise
        slug = str(request.session.get("entreprise_publique") or "")[:60]
        request.session.pop("entreprise_choisie", None)
        if not slug:
            return True
        entreprise = Entreprise.objects.filter(slug=slug).only("id").first()
        if entreprise is None:
            return True
        if MembreEntreprise.objects.filter(utilisateur=utilisateur, entreprise=entreprise).exists():
            request.session["entreprise_choisie"] = entreprise.id
            request._entreprise_requete = None
            return True
        # Aucun rattachement du tout (super administrateur avant sa première entreprise) : rien à départager.
        return not MembreEntreprise.objects.filter(utilisateur=utilisateur).exists() and bool(getattr(utilisateur, "is_superuser", False))
    except Exception:
        journal.debug("choisir_entreprise: base indisponible, entreprise du compte conservée", exc_info=True)
        return True


def slug_entreprise_libre(base):
    """Slug d'entreprise unique dérivé de `base` (le slug sert d'adresse publique : jamais celui d'une autre entreprise)."""
    from django.utils.text import slugify
    from .models import Entreprise
    racine = (slugify(base or "") or "entreprise")[:40]
    slug, n = racine, 1
    while Entreprise.objects.filter(slug=slug).exists():
        n += 1
        slug = f"{racine}-{n}"
    return slug


def _fichier_de_repli(rel):
    """Cluster : le fichier `rel` dans les dossiers de lecture/secours déclarés (page Cluster), dans l'ordre.
    Même garde-fou que chemin_media_sur : le résultat doit rester strictement sous la racine de repli."""
    for dossier in getattr(settings, "CLUSTER_MEDIA_REPLI", []):
        try:
            racine = Path(dossier).resolve()
            cible = (racine / rel).resolve()
            if racine in cible.parents and cible.is_file():
                return cible
        except (OSError, ValueError):
            continue
    return None


# Messages vocaux enregistrés par le navigateur : types que mimetypes ne connaît pas toujours.
_TYPES_REPLI = {".weba": "audio/webm", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".webm": "video/webm"}


def _reponse_plage(request, cible, type_mime):
    """206 Partial Content pour un son/une vidéo (en-tête « Range: bytes=a-b ») ; plage illisible : 416."""
    import re
    from django.http import HttpResponse
    taille = cible.stat().st_size
    m = re.match(r"bytes=(\d*)-(\d*)$", request.headers.get("Range", "").strip())
    if not m or not (m.group(1) or m.group(2)):
        return HttpResponse(status=416, headers={"Content-Range": f"bytes */{taille}"})
    debut = int(m.group(1)) if m.group(1) else max(0, taille - int(m.group(2)))
    fin = min(int(m.group(2)), taille - 1) if (m.group(1) and m.group(2)) else taille - 1
    if debut > fin or debut >= taille:
        return HttpResponse(status=416, headers={"Content-Range": f"bytes */{taille}"})
    with open(cible, "rb") as f:
        f.seek(debut)
        donnees = f.read(min(fin - debut + 1, 8 * 1024 * 1024))   # tranche de 8 Mo au plus : le lecteur redemande la suite
    fin = debut + len(donnees) - 1
    return HttpResponse(donnees, status=206, content_type=type_mime, headers={
        "Content-Range": f"bytes {debut}-{fin}/{taille}", "Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600"})


_EXT_OFFICE = {".docx", ".doc", ".pptx", ".ppt", ".odt", ".odp"}


def _apercu_pdf(cible):
    """Aperçu d'un document Office : copie convertie en PDF par LibreOffice (service OPTIONNEL : absent, échec ou délai dépassé → None,
    l'interface propose alors le téléchargement). Le fichier a déjà passé verifier_fichier ; la conversion tourne dans un dossier jetable,
    avec un profil vierge (macros désactivées par défaut), et le résultat est mis en cache (clé = chemin + taille + date)."""
    import hashlib
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    try:
        st = cible.stat()
        dossier = Path(tempfile.gettempdir()) / "agentgy_apercus"
        dossier.mkdir(mode=0o700, exist_ok=True)
        sortie = dossier / (hashlib.sha256(f"{cible}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest() + ".pdf")
        if sortie.is_file():
            return sortie
        with tempfile.TemporaryDirectory(dir=dossier) as tmp:
            src = Path(tmp) / ("source" + cible.suffix.lower())
            shutil.copyfile(cible, src)
            subprocess.run([exe, "--headless", "--norestore", "--nolockcheck", f"-env:UserInstallation=file://{tmp}/profil",
                            "--convert-to", "pdf", "--outdir", tmp, str(src)], capture_output=True, timeout=90, stdin=subprocess.DEVNULL)
            pdf = Path(tmp) / "source.pdf"
            if not pdf.is_file():
                return None
            os.replace(pdf, sortie)
        return sortie
    except (OSError, subprocess.SubprocessError):
        journal.exception(_gt("rg5.msg.015"), cible.name)
        return None


_EXT_DIAPO = {".pptx"}


def _apercu_video(cible):
    """PPTX → MP4 natif (python-pptx + Pillow, sans LibreOffice) : durées, fondus, effets d'entrée / sortie / emphase / trajet ; aucune limite de diapositives ni d'étapes."""
    import hashlib, io, math, tempfile, textwrap, imageio_ffmpeg
    from PIL import Image, ImageDraw, ImageFont
    from pptx import Presentation
    ns, st = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}, cible.stat()
    mp4 = Path(tempfile.gettempdir(), "agentgy_apercus", hashlib.sha256(f"{cible}|{st.st_size}|{st.st_mtime_ns}|natif".encode()).hexdigest() + ".mp4")
    if cible.suffix.lower() not in _EXT_DIAPO or mp4.is_file(): return mp4 if mp4.is_file() else None
    try:
        mp4.parent.mkdir(mode=0o700, exist_ok=True); prs = Presentation(str(cible)); W = 960; H = round(W * prs.slide_height / prs.slide_width) // 2 * 2; k = W / prs.slide_width
        def calque(s):   # RGBA pleine diapositive : image, remplissage et texte de la forme
            im = Image.new("RGBA", (W, H)); d = ImageDraw.Draw(im); x, y, w, h = (int((v or 0) * k) for v in (s.left, s.top, s.width, s.height)); rgb = lambda c: "#" + str(c.rgb) if c.type == 1 else None
            r = [r for p in (s.text_frame.paragraphs if s.has_text_frame else []) for r in p.runs]; px = max(8, int((r[0].font.size or 228600) * k)) if r else 12
            if s.shape_type == 13: im.paste(Image.open(io.BytesIO(s.image.blob)).convert("RGBA").resize((max(w, 1), max(h, 1))), (x, y))
            if getattr(s, "fill", None) is not None and s.fill.type == 1: d.rectangle((x, y, x + w, y + h), fill=rgb(s.fill.fore_color))
            if r: d.multiline_text((x + 4, y + 4), "\n".join(textwrap.fill(p.text, max(1, int(w / (px * .55)))) for p in s.text_frame.paragraphs), font=ImageFont.load_default(px), fill=rgb(r[0].font.color) or "#000")
            return im
        def vue(d, L, vis, off, act):   # image de la diapositive : états acquis (vis, off) + effets en cours act = {id de forme: (effet, avancement 0→1)}
            fond = d.background.fill; im = Image.new("RGBA", (W, H), "#" + str(fond.fore_color.rgb) if fond.type == 1 and fond.fore_color.type == 1 else "white")
            for s in d.shapes:
                i = s.shape_id; a, sc, (dx, dy), box = float(vis.get(i, True)), 1.0, off.get(i, (0, 0)), (0, 0, W, H)
                if i in act:
                    (c, pre, sub, _, _, v), p = act[i]; u = {"entr": 1 - p, "exit": p}.get(c, 0)   # u = part « cachée » de l'objet
                    if c in ("entr", "exit"): a = float(u < 1) if pre in (1, 22) else 1 - u   # apparition / volet : tout ou rien ; sinon fondu
                    if pre in (23, 31, 47, 51, 52, 53): sc = max(.02, 1 - u)   # zoom
                    if pre == 2: dx, dy = dx + u * W * ((sub == 2) - (sub == 8)), dy + u * H * ((sub == 4) - (sub == 1))   # volée depuis le bord
                    if pre == 22 and c != "emph": box = (int(W * u * (sub == 2)), int(H * u * (sub == 4)), int(W * (1 - u * (sub == 8))), int(H * (1 - u * (sub == 1))))   # volet
                    if c == "emph": sc = 1 + .15 * math.sin(math.pi * p)   # emphase : pulsation
                    if c == "path" and len(v) == 2: dx, dy = dx + p * W * v[0], dy + p * H * v[1]   # trajectoire : point d'arrivée
                cx, cy = ((s.left or 0) + (s.width or 0) / 2) * k, ((s.top or 0) + (s.height or 0) / 2) * k; m = Image.new("RGBA", (W, H)); m.paste(L[i].crop(box), box[:2]); m.putalpha(m.getchannel("A").point(lambda q: int(q * a)))
                im.alpha_composite(m.transform((W, H), Image.AFFINE, (1 / sc, 0, cx * (1 - 1 / sc) - dx / sc, 0, 1 / sc, cy * (1 - 1 / sc) - dy / sc)))
            return im.convert("RGB")
        sortie = mp4.with_name("t_" + mp4.name); ecrit = imageio_ffmpeg.write_frames(str(sortie), (W, H), fps=10, macro_block_size=2, output_params=["-preset", "ultrafast"]); ecrit.send(None); prec = None
        for d in prs.slides:
            x, pas, vis, off, L = d._element.xml, [], {}, {}, {s.shape_id: calque(s) for s in d.shapes}; t = re.search(r'advTm="(\d+)"', x); duree = int(t[1]) / 1000 if t else 3.0
            for e in d._element.xpath(".//p:cTn[@presetClass]"):   # effets dans l'ordre du XML ; « avec le précédent » = même étape que le précédent
                f = (e.get("presetClass"), int(e.get("presetID", 0)), int(e.get("presetSubtype", 0)), sorted({int(i) for i in e.xpath(".//p:spTgt/@spid", namespaces=ns)}), max([int(v) for v in e.xpath(".//p:cBhvr/p:cTn/@dur", namespaces=ns) if v.isdigit()] or [500]) / 1000, [float(n) for n in re.findall(r"-?\d[\d.]*", (e.xpath(".//p:animMotion/@path", namespaces=ns) or [""])[0])][-2:])
                pas[-1].append(f) if pas and e.get("nodeType") == "withEffect" else pas.append([f]); [vis.setdefault(i, f[0] != "entr") for i in f[3]]
            for j in range(len(pas) + 1):   # j = 0 : état de départ ; j ≥ 1 : l'étape j se joue image par image, puis son état est tenu
                fs = pas[j - 1] if j else []; N = int(max([f[4] for f in fs], default=0) * 10)
                for q in range(N): ecrit.send(vue(d, L, vis, off, {i: (f, (q + 1) / N) for f in fs for i in f[3]}).tobytes())
                for f in fs:
                    for i in f[3]:
                        if f[0] in ("entr", "exit"): vis[i] = f[0] == "entr"
                        if f[0] == "path" and len(f[5]) == 2: off[i] = (off.get(i, (0, 0))[0] + W * f[5][0], off.get(i, (0, 0))[1] + H * f[5][1])
                img = vue(d, L, vis, off, {})
                if j == 0 and prec and "<p:transition" in x: [ecrit.send(Image.blend(prec, img, q / 5).tobytes()) for q in range(1, 5)]
                for _ in range(max(1, int((duree if j == len(pas) else 1.2) * 10))): ecrit.send(img.tobytes())
                prec = img
        ecrit.close(); os.replace(sortie, mp4)
        return mp4
    except Exception:
        journal.exception(_gt("rg5.msg.016"), cible.name)
        return None


def servir_media(request, chemin):
    cible = chemin_media_sur(chemin)
    if cible is None:
        raise Http404
    # Contrôles sur le chemin NORMALISÉ (sinon « logos/../scripts_agent/x » passerait pour public).
    rel = cible.relative_to(Path(settings.MEDIA_ROOT).resolve()).as_posix()
    if not cible.is_file():
        cible = _fichier_de_repli(rel)  # cluster : dossier principal injoignable ou réplication en retard
        if cible is None:
            raise Http404
    if rel.startswith(_DOSSIERS_INTERNES):
        journal.warning(t("engine/securite.py:g:154:25"), rel, request.user)
        raise Http404
    if not rel.startswith(_DOSSIERS_PUBLICS):
        if not request.user.is_authenticated:
            raise Http404
        if rel.startswith(("messagerie/", "publicites/")):   # discussions privées : même le super-administrateur passe par la règle d'accès
            from .messagerie import fichier_accessible
            autorise = fichier_accessible(request.user, rel)
        else:
            autorise = request.user.is_superuser or _proprietaire_ok(request, rel)
        if not autorise:
            journal.warning(t("engine/securite.py:g:165:29"), rel, request.user)
            raise Http404
    mode = request.GET.get("apercu")
    if mode in ("pdf", "video") and cible.suffix.lower() in _EXT_OFFICE:   # APRÈS les contrôles d'accès ci-dessus
        cible = (_apercu_video if mode == "video" else _apercu_pdf)(cible)
        if cible is None:
            raise Http404
    type_mime = (mimetypes.guess_type(cible.name)[0] or _TYPES_REPLI.get(cible.suffix.lower())
                 or "application/octet-stream")
    en_ligne = type_mime.startswith(("image/", "audio/", "video/")) or type_mime == "application/pdf"
    if type_mime.startswith(("audio/", "video/")) and request.headers.get("Range"):
        return _reponse_plage(request, cible, type_mime)   # Safari et la barre de lecture exigent les requêtes par plage d'octets
    reponse = FileResponse(open(cible, "rb"), content_type=type_mime, as_attachment=not en_ligne,
                           filename=os.path.basename(cible))
    reponse["X-Content-Type-Options"] = "nosniff"
    reponse["Cache-Control"] = "private, max-age=3600"
    if type_mime in ("image/svg+xml", "text/html"):
        reponse["Content-Security-Policy"] = "sandbox"  # un SVG/HTML téléversé ne peut pas exécuter de script
    return reponse


def vue_sante(request):
    """/sante/ : supervision (Uptime Kuma, Zabbix, systemd…). Sans authentification mais sans aucune
    donnée métier : état de la base, espace disque, fichier de journal accessible. 503 si un point échoue."""
    import shutil
    from django.db import connection
    from django.http import JsonResponse
    etat = {}
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        etat["base"] = "ok"
    except Exception:
        etat["base"] = "erreur"
    libre = shutil.disk_usage(settings.MEDIA_ROOT if os.path.isdir(settings.MEDIA_ROOT) else settings.BASE_DIR).free
    etat["disque"] = "ok" if libre > 1024 ** 3 else "faible"  # moins de 1 Go libre
    ok = etat["base"] == "ok" and etat["disque"] == "ok"
    return JsonResponse({"statut": "ok" if ok else "degrade", **etat}, status=200 if ok else 503)


# ── Contrôle des fichiers à l'enregistrement d'un formulaire ──────────────────────────────────────────────────────────
# Un fichier corrompu ou piégé est refusé AVANT toute écriture (les vues de création/modification de ligne l'appellent dans leur
# phase de validation). Contrôles structurels + antivirus ClamAV si settings.ANTIVIRUS_CLAMD est réglé (voir DEPLOIEMENT.md).
_EXT_PROGRAMMES = {".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi", ".vbs", ".vbe", ".wsf", ".ps1", ".jar", ".apk", ".lnk", ".hta", ".sys", ".so", ".dylib"}
_EXT_DANGEREUSES = _EXT_PROGRAMMES | {".js", ".jse", ".mjs", ".sh", ".bash", ".php", ".phtml", ".phar", ".py", ".pl", ".rb", ".html", ".htm", ".xhtml", ".shtml", ".swf"}
_EXT_TOLEREES = lambda: {e.lower() for e in getattr(settings, "FICHIERS_EXTENSIONS_TOLEREES", ())}   # ex. {".html"} : levées par l'administrateur dans settings.py
_EXT_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg"}
_EXT_MEDIAS = {".mp3", ".wav", ".ogg", ".oga", ".opus", ".weba", ".m4a", ".aac", ".flac", ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
_DEBUTS_PROGRAMMES = (b"\x7felf", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!", b"<?php", b"<%@", b"<script", b"<html", b"<!doctype html")
_ACTIF_PDF = re.compile(rb"/(JavaScript|Launch|EmbeddedFile|RichMedia)(?![A-Za-z])")   # « /JS » seul retiré : toute action JavaScript porte aussi « /S /JavaScript » ; « /JS » tombait au hasard dans les octets d'images/polices (≈ 45 % des PDF de 20 Mo refusés à tort)
_ACTIF_SVG = re.compile(rb"<script|<foreignobject|<iframe|<embed|<object|\bon\w+\s*=|javascript:|data:text/html", re.I)
_ACTIF_RTF = re.compile(rb"\\(objdata|objupdate|objautlink)", re.I)
# Garde-fous contre les BOMBES (pas des limites de taille d'envoi) : une image aux dimensions démesurées ou une archive qui se
# décompresse sans fin saturent la mémoire du serveur. Réglables dans settings (FICHIERS_MAX_PIXELS) ; 0 / None = pas de plafond.
# Aucun plafond ABSOLU sur le poids d'un fichier ou d'une archive : seul le rapport de compression d'une bombe est refusé.
_MAX_PIXELS = int(getattr(settings, "FICHIERS_MAX_PIXELS", 0) or 0) or float("inf")   # défaut : AUCUN plafond
if _MAX_PIXELS == float("inf"):
    try:
        from PIL import Image as _PIL
        _PIL.MAX_IMAGE_PIXELS = None   # Pillow refuse sinon les images de plus de ~178 Mpx
    except ImportError:
        pass
_MAX_DECOMPRESSE = float("inf")
_MAX_LECTURE = 64 << 20


def _lire(f, n=None):
    f.seek(0)
    try:
        return f.read(n)
    finally:
        f.seek(0)


def _controle_image(f, tete):
    if b"<svg" in tete.lower():   # SVG : du XML qui peut porter du script
        return "fichier_actif" if _ACTIF_SVG.search(_lire(f, _MAX_LECTURE)) else None
    from PIL import Image
    try:
        with Image.open(f) as im:
            if im.width * im.height > _MAX_PIXELS:
                return "fichier_trop_gros"
            im.verify()   # structure
        f.seek(0)
        with Image.open(f) as im:
            im.load()     # décodage complet : détecte une image tronquée
        if re.search(rb"<\?php|<script|<%@", _lire(f, _MAX_LECTURE), re.I):   # image valide qui cache du code (polyglotte)
            return "fichier_actif"
    except Image.DecompressionBombError:
        return "fichier_trop_gros"
    except Exception:
        return "fichier_corrompu"
    finally:
        f.seek(0)


def _exe_ffmpeg():
    """ffmpeg du système, sinon celui qu'embarque imageio-ffmpeg s'il est installé, sinon None (hébergement sans ffmpeg, ex. PythonAnywhere)."""
    import shutil
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _controle_media(f, ext):
    """Décode les 30 premières secondes avec ffmpeg quand il existe. Sans ffmpeg, le contrôle d'intégrité par décodage est
    simplement sauté : les contrôles d'extension, de programme déguisé et l'antivirus s'appliquent toujours avant et après."""
    import subprocess
    import tempfile
    exe = _exe_ffmpeg()
    if not exe:
        return None
    ext = ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ""
    try:
        with tempfile.NamedTemporaryFile(suffix=ext) as tmp:
            f.seek(0)
            for morceau in iter(lambda: f.read(1 << 20), b""):
                tmp.write(morceau)
            tmp.flush()
            r = subprocess.run([exe, "-v", "error", "-threads", "1", "-t", "30", "-i", tmp.name, "-f", "null", "-"],
                               capture_output=True, timeout=60, stdin=subprocess.DEVNULL)   # lit l'en-tête et décode les 30 premières secondes
    except FileNotFoundError:
        return None   # ffmpeg disparu entre-temps : même repli que l'absence
    except subprocess.TimeoutExpired:
        return "fichier_corrompu"
    finally:
        f.seek(0)
    return None if r.returncode == 0 else "fichier_corrompu"


def _pdf_actif(d):
    """Noms écrits en hexadécimal (/J#61vaScript) et flux compressés (Flate, y compris les flux d'objets) inclus ; décompression bornée."""
    import zlib
    net = lambda x: re.sub(rb"#([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), x)
    morceaux, reste = [net(d)], 100 << 20
    for m in list(re.finditer(rb"stream\r?\n(.*?)endstream", d, re.S))[:3000]:
        if b"/ObjStm" not in net(d[max(0, m.start() - 600):m.start()]):   # seuls les flux d'objets peuvent cacher un dictionnaire d'action : images, polices et pages ne sont pas fouillées
            continue
        try:
            x = zlib.decompressobj().decompress(m.group(1), min(reste, 20 << 20))
        except zlib.error:
            continue
        reste -= len(x)
        morceaux.append(net(x))
        if reste <= 0:
            break
    return any(_ACTIF_PDF.search(x) for x in morceaux)


def _controle_document(f, tete, ext):
    if b"%PDF-" in tete[:1024] or ext == ".pdf":
        d = _lire(f, _MAX_LECTURE)
        if b"%PDF-" not in d[:1024] or b"%%EOF" not in d[-65536:]:
            return "fichier_corrompu"
        return "fichier_actif" if _pdf_actif(d) else None
    if tete[:4] == b"\xd0\xcf\x11\xe0":   # ancien Word/Excel/PowerPoint : macros VBA
        d = _lire(f, _MAX_LECTURE)
        return "fichier_actif" if "VBA".encode("utf-16le") in d or b"_VBA_PROJECT" in d else None
    if tete[:5] == b"{\\rtf":   # RTF : objets OLE embarqués
        return "fichier_actif" if _ACTIF_RTF.search(_lire(f, _MAX_LECTURE)) else None
    if tete[:4] == b"PK\x03\x04":   # zip, docx, xlsx, odt… : bombe de décompression, chemins piégés, macros, programmes embarqués
        import zipfile
        try:
            with zipfile.ZipFile(f) as z:
                infos = z.infolist()
                total = sum(i.file_size for i in infos)
                f.seek(0, 2)
                taille = f.tell()
                if total > _MAX_DECOMPRESSE or len(infos) > 10000 or (total > 50 << 20 and total > 200 * max(1, taille)):
                    return "fichier_trop_gros"
                for i in infos:
                    n = i.filename.lower().replace("\\", "/")
                    if n.startswith("/") or ".." in n.split("/") or n.endswith("vbaproject.bin") or os.path.splitext(n)[1] in _EXT_PROGRAMMES:
                        return "fichier_actif"
                if z.testzip():
                    return "fichier_corrompu"
        except zipfile.BadZipFile:
            return "fichier_corrompu"
        finally:
            f.seek(0)
    return None


def _antivirus(f):
    """None (sain ou antivirus non réglé) | (« fichier_virus », menace) | (« fichier_controle_ko », None). Protocole INSTREAM de clamd."""
    cfg = getattr(settings, "ANTIVIRUS_CLAMD", None)
    if not cfg:
        return None
    import socket
    import struct
    try:
        with socket.socket(socket.AF_UNIX if cfg.get("socket") else socket.AF_INET) as c:
            c.settimeout(cfg.get("delai", 30))
            c.connect(cfg["socket"] if cfg.get("socket") else (cfg.get("hote", "127.0.0.1"), int(cfg.get("port", 3310))))
            c.sendall(b"zINSTREAM\0")
            f.seek(0)
            for morceau in iter(lambda: f.read(1 << 16), b""):
                c.sendall(struct.pack("!I", len(morceau)) + morceau)
            c.sendall(struct.pack("!I", 0))
            rep = c.recv(4096).decode("utf-8", "replace").strip("\0\n ")
    except OSError:
        return "fichier_controle_ko", None
    finally:
        f.seek(0)
    if rep.endswith("FOUND"):
        return "fichier_virus", rep.split(":", 1)[-1].rsplit(" FOUND", 1)[0].strip()
    return None if rep.endswith("OK") else ("fichier_controle_ko", None)


def verifier_taille_champ(f, champ):
    """None si le fichier respecte la taille maximale réglée sur le champ (champ.taille_max_mo, en Mo), sinon le message de refus.
    Pas de réglage (None / 0) = AUCUNE limite : seul l'espace disque borne le téléversement."""
    mo = getattr(champ, "taille_max_mo", None)
    if not mo or getattr(f, "size", 0) <= int(mo) * 1024 * 1024:
        return None
    nom = os.path.basename(str(getattr(f, "name", "") or "fichier"))
    return tf("engine/securite.py:fichier_depasse_taille_champ", v1=nom, v2=int(mo))


def verifier_fichier(f, type_champ="fichier"):
    """None si le fichier est sain, sinon le message de refus (traduit, avec le nom du fichier). Le fichier est rembobiné."""
    nom = os.path.basename(str(getattr(f, "name", "") or "fichier"))
    ext = os.path.splitext(nom.lower())[1]
    cle, menace = None, ""
    genre = "image" if type_champ == "image" or ext in _EXT_IMAGES else "media" if type_champ in ("son", "video") or ext in _EXT_MEDIAS else "document"
    if genre in getattr(f, "_controles", ()):
        return None
    try:
        if "\x00" in nom or any("." + p in _EXT_DANGEREUSES - _EXT_TOLEREES() for p in nom.lower().split(".")[1:]):   # « photo.php.jpg » compte aussi
            cle = "fichier_dangereux"
        else:
            tete = _lire(f, 4096)
            if (tete[:2] == b"MZ" and b"PE\0\0" in tete) or tete.lstrip()[:15].lower().startswith(_DEBUTS_PROGRAMMES):
                cle = "fichier_deguise"   # programme ou script sous une autre apparence
            elif genre == "image":
                cle = _controle_image(f, tete)
            elif genre == "media":
                cle = _controle_media(f, ext)
            else:
                cle = _controle_document(f, tete, ext)
            if cle is None:
                av = _antivirus(f)
                cle, menace = av if av else (None, "")
    except Exception:
        journal.exception(_gt("engine/securite.py:495:27"), nom)
        cle = "fichier_controle_ko"
    if not cle:
        try:
            f._controles = {*getattr(f, "_controles", ()), genre}
        except AttributeError:
            pass
    if cle:
        journal.warning(t("engine/securite.py:fichier_journal"), nom, cle, menace or "")
        return tf(f"engine/securite.py:{cle}", v1=nom, v2=menace)
    return None


# ── Membre de la requête : isolation atomique par entreprise ─────────────────────────────────────────────────────────
def membre_de_requete(request, queryset=None):
    """MembreEntreprise sous lequel CETTE requête s'exécute, jamais celui d'une autre entreprise.

    - Requête par clé API (Bearer) : exactement le membre incarné par la clé, dans l'entreprise de la clé.
    - Session navigateur : le membre de l'entreprise choisie à la connexion (session[\"entreprise_choisie\"]), sinon le premier
      (environnement actif). Même règle que views._entreprise_courante, pour qu'un compte présent dans plusieurs entreprises
      ne lise jamais le membre d'une entreprise et les données d'une autre.
    `queryset` : MembreEntreprise.objects avec ses select_related / only habituels."""
    from .models import MembreEntreprise
    qs = (queryset if queryset is not None else MembreEntreprise.objects.all()).filter(utilisateur=request.user)
    cle = getattr(request, "_cle_api", None)
    if cle is not None:
        return qs.filter(pk=cle.membre_id, entreprise_id=cle.entreprise_id).first()
    session = getattr(request, "session", None)
    choisie = session.get("entreprise_choisie") if session is not None else None
    return (qs.filter(entreprise_id=choisie).first() if choisie else None) or qs.first()
