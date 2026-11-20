"""Journal d'entrainement visible : tout ce qui se passe (file d'attente,
preparation des donnees, epoques, avertissements, erreur + cause) est ecrit
dans ModeleIA.progression["journal"] et lu par l'interface via le polling
existant. Un battement de coeur ("maj") prouve que le worker travaille
vraiment, y compris pour les modeles sklearn (un seul fit, sans epoques).
Aucune migration : tout vit dans le JSONField `progression` existant."""
from engine.i18n import tf, tl
import os
import threading
import time
import traceback

from .exceptions_ia import DatasetError

MAX_LIGNES = 80


def _hms(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def etat_initial(message=tl("engine/journal_ia.py:g:21:26")):
    """Etat ecrit par la vue AVANT l'envoi de la tache (phase file_attente)."""
    now = time.time()
    return {"phase": "file_attente", "debut": now, "maj": now,
            "journal": [{"t": _hms(now), "n": "info", "m": message}]}


class Journal:
    def __init__(self, modele_id):
        from .models import ModeleIA
        self.modele_id = modele_id
        actuel = ModeleIA.objects.filter(pk=modele_id).values_list("progression", flat=True).first()
        self.etat = dict(actuel) if actuel else {}
        self.etat.setdefault("journal", [])
        self.etat["debut"] = self.etat.get("debut") or time.time()
        self._verrou = threading.Lock()
        self._stop = threading.Event()
        self._fil = None

    def ecrire(self):
        from .models import ModeleIA
        with self._verrou:
            self.etat["maj"] = time.time()
            ModeleIA.objects.filter(pk=self.modele_id).update(progression=dict(self.etat))

    def log(self, message, niveau="info", phase=None, **extra):
        with self._verrou:
            if phase:
                self.etat["phase"] = phase
            self.etat.update(extra)
            self.etat["journal"] = (self.etat["journal"] + [
                {"t": _hms(time.time()), "n": niveau, "m": str(message)}])[-MAX_LIGNES:]
        self.ecrire()

    def maj_epoque(self, **champs):
        """Mise a jour de la progression sans nouvelle ligne de journal."""
        with self._verrou:
            self.etat.update(champs)
        self.ecrire()

    def echec(self, exc):
        """Erreur finale : message lisible ; pour une erreur INATTENDUE (pas une
        violation de contrainte), ajoute l'origine technique (fichier:ligne)."""
        self.log(str(exc), "err", phase="erreur")
        if not isinstance(exc, DatasetError):
            cause = exc.__cause__ or exc
            frames = traceback.extract_tb(cause.__traceback__)
            if frames:
                f = frames[-1]
                self.log(tf("engine/journal_ia.py:g:70:26", v1=os.path.basename(f.filename), v2=f.lineno, v3=f.name, v4=type(cause).__name__), "err")

    def demarrer_battement(self, periode=2.0):
        def boucle():
            from django.db import connections
            while not self._stop.wait(periode):
                try:
                    self.ecrire()
                finally:
                    connections.close_all()
        self._fil = threading.Thread(target=boucle, daemon=True)
        self._fil.start()

    def arreter(self):
        self._stop.set()
        if self._fil:
            self._fil.join(timeout=3)
