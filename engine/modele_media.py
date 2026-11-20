"""MODÈLES D'IMAGE ET DE SON RÉUTILISABLES — un modèle entraîné dans la page Entraînement sert partout où il y a une caméra ou
un micro (analyses de vision des flux, et tout autre endroit qui veut classer une image ou un son).

Principe : on ne rappelle jamais le fichier d'un modèle à chaque image. `predicteur(modele)` charge le modèle UNE fois (cache
par modèle, invalidé dès qu'il est réentraîné ou affiné) ; `predire_image` / `predire_son` appliquent la MÊME recette que
l'entraînement (vecteurs de caractéristiques si le modèle a été entraîné ainsi, pixels bruts pour un ancien modèle) grâce à
`ia_integration.lancer_inference`, qui lit la structure mémorisée avec le modèle.

Un modèle est utilisable sur un flux quand : statut « entraîné », fichier présent, et structure d'entrée en mode « image »
(caméra) ou « audio » (micro).
"""
import logging
import threading

from django.conf import settings  # noqa: F401  (le chemin des modèles est résolu par ia_integration)
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)

IMAGE, AUDIO = "image", "audio"

_cache = {}            # modele_id -> (clé, Predicteur)
_verrou = threading.Lock()


def media_du_modele(modele):
    """"image" | "audio" | None : ce que le modèle sait analyser (d'après la structure mémorisée à l'entraînement)."""
    if getattr(modele, "statut", "") != "entraine" or not getattr(modele, "fichier_modele", ""):
        return None
    mode = (modele.structure_entree or {}).get("mode")
    return mode if mode in (IMAGE, AUDIO) else None


def decrire(modele):
    """Fiche d'un modèle pour les listes de choix (id, nom, type, média, classes, vectorisé ou non)."""
    s = modele.structure_entree or {}
    return {"id": modele.pk, "nom": modele.nom, "type": modele.type_modele, "media": media_du_modele(modele),
            "classes": [str(c) for c in (s.get("classes_cible") or [])],
            "vectorise": bool(s.get("vectorisation"))}


def modeles_disponibles(entreprise_id, media=None):
    """Modèles de l'entreprise utilisables sur une caméra (media="image"), un micro (media="audio") ou les deux."""
    from .models import ModeleIA
    sortie = []
    for m in ModeleIA.objects.filter(entreprise_id=entreprise_id, statut="entraine").exclude(fichier_modele="").order_by("nom"):
        md = media_du_modele(m)
        if md and (media is None or md == media):
            sortie.append(decrire(m))
    return sortie


class Predicteur:
    """Un modèle chargé en mémoire. Les prédictions d'un même modèle sont sérialisées (le moteur n'est pas garanti réentrant)."""

    def __init__(self, modele):
        self.modele = modele
        self.media = media_du_modele(modele)
        self._verrou = threading.Lock()
        self._charge = None

    def _moteur(self):
        if self._charge is None:
            import os
            from . import ia_integration as ia
            chemin = os.path.join(settings.MEDIA_ROOT, self.modele.fichier_modele)
            self._charge = ia._trainer_module().Trainer.load(chemin)
        return self._charge

    def _predire(self, entree, **kw):
        from . import ia_integration as ia
        with self._verrou:
            brut = ia.lancer_inference(self.modele, entree, modele_charge=self._moteur(), **kw)
        return resume(brut)

    def predire_image(self, image, bgr=True):
        """Image d'un flux (ndarray OpenCV BGR par défaut, ou RGB avec bgr=False, ou PIL) -> {"classe", "confiance", ...}."""
        if self.media != IMAGE:
            raise ValueError(_gt("rg5.msg.041"))
        return self._predire(image, bgr=bgr)

    def predire_son(self, pcm, rate):
        """Son d'un flux : PCM 16 bits little-endian mono (octets) ou onde flottante [-1, 1] + fréquence -> {"classe", ...}."""
        if self.media != AUDIO:
            raise ValueError(_gt("rg5.msg.042"))
        return self._predire(pcm, rate=int(rate))


def _confiance(sortie, classe):
    """Probabilité de la classe annoncée : max d'un softmax, ou max(p, 1-p) pour une sortie sigmoïde unique."""
    ligne = sortie[0] if isinstance(sortie, list) and sortie else sortie
    if isinstance(ligne, list):
        if len(ligne) > 1:
            return float(max(ligne))
        ligne = ligne[0] if ligne else None
    if isinstance(ligne, float) and classe is not None and 0.0 <= float(ligne) <= 1.0:
        return max(float(ligne), 1.0 - float(ligne))
    return None


def resume(brut):
    """Sortie de lancer_inference -> {"classe": libellé|None, "confiance": 0..1|None, "brut": sortie complète}."""
    labels = brut.get("predictions_labels")
    classe = labels[0] if isinstance(labels, list) and labels else labels
    sortie = brut.get("predictions") if brut.get("predictions") is not None else brut.get("output")
    try:
        confiance = _confiance(sortie, classe)
    except (TypeError, ValueError):
        confiance = None
    return {"classe": None if classe is None else str(classe), "confiance": confiance, "brut": brut}


def predicteur(modele):
    """Prédicteur en cache pour ce modèle (rechargé si le fichier a changé : réentraînement, affinage)."""
    cle = (modele.fichier_modele, str(getattr(modele, "entraine_le", "") or ""))
    with _verrou:
        trouve = _cache.get(modele.pk)
        if trouve and trouve[0] == cle:
            return trouve[1]
        p = Predicteur(modele)
        _cache[modele.pk] = (cle, p)
        return p


def oublier(modele_id):
    with _verrou:
        _cache.pop(modele_id, None)
