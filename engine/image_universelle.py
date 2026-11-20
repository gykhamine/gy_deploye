"""Lecture universelle des images (PDF, cartes) : toute image que ReportLab/PIL ne sait pas lire telle quelle
(SVG, HEIC, ICO, TIFF exotique, 16 bits…) est convertie en PNG, au point unique ou ReportLab ouvre une image
(ImageReader) et pour les cartes. Convertisseurs essayes dans l'ordre : PIL (+ greffons HEIF/AVIF), GdkPixbuf (gi),
OpenCV, cairosvg/svglib (SVG), rsvg-convert, ImageMagick, ffmpeg."""
import functools
import io
import os
import shutil
import subprocess


def _octets(src):
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    if hasattr(src, "getvalue"):
        return src.getvalue()
    if hasattr(src, "read") and hasattr(src, "seek"):
        pos = src.tell(); donnees = src.read(); src.seek(pos)
        return donnees
    if isinstance(src, (str, os.PathLike)) and os.path.isfile(src):
        with open(src, "rb") as f:
            return f.read()
    return None


# Formats acceptes a l'envoi (logo, cachet, signature, fond, photo de document) : une seule liste pour tout le projet.
EXTENSIONS = (".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".apng", ".svg", ".bmp", ".tif", ".tiff", ".ico",
              ".avif", ".heic", ".heif", ".jp2", ".tga", ".ppm", ".pgm")
ACCEPT = ",".join(EXTENSIONS)   # attribut accept= des champs de fichier
_MODES_PDF = ("RGB", "RGBA", "L", "P", "CMYK", "1")


def _pil_ouvrir(donnees):
    """Image PIL (greffons HEIF/AVIF actives s'ils sont installes) ou None."""
    try:
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        img = Image.open(io.BytesIO(donnees)); img.load()
        return img
    except Exception:
        return None


def _png_pil(img):
    sortie = io.BytesIO()
    img.convert("RGBA").save(sortie, "PNG")
    return sortie.getvalue()


@functools.lru_cache(maxsize=64)
def _vers_png(donnees):
    svg = b"<svg" in donnees[:4096].lower()
    img = _pil_ouvrir(donnees)
    if img is not None:   # lisible par PIL mais mode inhabituel (16 bits, flottant, LA…) ou greffon : normalise en RGBA
        try:
            return _png_pil(img)
        except Exception:
            pass
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        ch = GdkPixbuf.PixbufLoader()
        if svg:
            ch.connect("size-prepared", lambda l, w, h: l.set_size(max(1, int(w * 512 / max(w, h))), max(1, int(h * 512 / max(w, h)))))
        ch.write(donnees); ch.close()
        ok, buf = ch.get_pixbuf().save_to_bufferv("png", [], [])
        if ok:
            return bytes(buf)
    except Exception:
        pass
    try:
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(donnees, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is not None:
            if img.dtype != np.uint8:
                img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            ok, buf = cv2.imencode(".png", img)
            if ok:
                return buf.tobytes()
    except Exception:
        pass
    if svg:
        try:
            import cairosvg
            return cairosvg.svg2png(bytestring=donnees, output_width=512)
        except Exception:
            pass
        try:
            from reportlab.graphics import renderPM
            from svglib.svglib import svg2rlg
            return renderPM.drawToString(svg2rlg(io.BytesIO(donnees)), fmt="PNG")
        except Exception:
            pass
    commandes = ([["rsvg-convert", "-f", "png", "-d", "300", "-p", "300"]] if svg else []) + [
        ["magick", "-background", "none", "-density", "300", "-", "png:-"],
        ["convert", "-background", "none", "-density", "300", "-", "png:-"],
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"]]
    for cmd in commandes:
        if shutil.which(cmd[0]):
            try:
                r = subprocess.run(cmd, input=donnees, capture_output=True, timeout=30)
                if r.returncode == 0 and r.stdout:
                    return r.stdout
            except Exception:
                pass
    return None


def lisible(src):
    """`src` (chemin, octets ou flux) -> BytesIO PNG si l'image n'est pas lisible telle quelle par ReportLab
    (format inconnu de PIL : SVG, HEIC, ICO…, ou mode inhabituel) et qu'un convertisseur existe ; sinon `src`
    inchange (l'appelant garde son comportement d'erreur habituel)."""
    donnees = _octets(src)
    if not donnees:
        return src
    img = _pil_ouvrir(donnees)
    if img is not None and img.mode in _MODES_PDF and not getattr(img, "is_animated", False):
        return src
    png = _vers_png(donnees)
    return io.BytesIO(png) if png else src


def activer():
    """Fait passer TOUTE image ouverte par ReportLab (platypus.Image, drawImage…) par lisible()."""
    from reportlab.lib import utils
    lecteur = utils.ImageReader
    if getattr(lecteur, "_gy_universel", False):
        return
    init = lecteur.__init__

    def __init__(self, fileName, ident=None):
        if not (isinstance(fileName, str) and fileName.startswith(("http://", "https://"))):
            nouveau = lisible(fileName)
            if nouveau is not fileName:
                ident = ident or str(fileName)[:200]
                fileName = nouveau
        init(self, fileName, ident)
    lecteur.__init__ = __init__
    lecteur._gy_universel = True
