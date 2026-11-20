"""Tâches de fond en Python pur — remplace Celery (aucun broker, aucun worker, aucun processus à superviser).

Même interface que `celery.shared_task` pour les seuls usages du projet :

    @shared_task(name="engine.ma_tache")
    def ma_tache(objet_id): ...

    ma_tache(1)                                  # appel direct, synchrone (comme une fonction normale)
    ma_tache.delay(1)                            # exécutée en arrière-plan, dans un fil du processus web
    ma_tache.apply_async(args=(1,), countdown=30)  # idem, après 30 secondes

Chaque tâche travaille déjà sur un objet en base dont le champ `statut` est lu par les écrans (polling) : aucun résultat
n'a donc besoin d'être renvoyé à l'appelant.

Garanties :
  • l'exécution démarre APRÈS la validation de la transaction en cours (`transaction.on_commit`) : le fil retrouve toujours
    l'objet que la vue vient de créer ;
  • au plus GYPLATFORM_TACHES_PARALLELES tâches en même temps (défaut 4, comme l'ancien pool du worker) ; les autres attendent ;
  • chaque fil a sa propre connexion à la base, fermée en fin de tâche ;
  • une exception dans une tâche est journalisée, jamais propagée à la requête qui l'a lancée ;
  • GYPLATFORM_TACHES_SYNCHRONES=1 : tout s'exécute immédiatement dans le fil appelant (tests, shell, commandes de gestion).

Limite connue de l'hébergement mutualisé : si l'hébergeur recycle le processus web, une tâche en cours est interrompue
(son objet reste alors à l'état « en cours » ; relancer l'action depuis l'écran).
"""
import logging
import os
import threading
import uuid

logger = logging.getLogger("engine.taches")

_PARALLELES = max(1, int(os.environ.get("GYPLATFORM_TACHES_PARALLELES", "4") or 4))
_places = threading.BoundedSemaphore(_PARALLELES)


def _synchrone():
    return os.environ.get("GYPLATFORM_TACHES_SYNCHRONES", "").strip().lower() in ("1", "true", "oui", "yes")


class Tache:
    """Fonction décorée : appelable directement, ou lancée en arrière-plan par `.delay()` / `.apply_async()`."""

    def __init__(self, fonction, nom=None):
        self._fonction = fonction
        self.name = nom or f"{fonction.__module__}.{fonction.__name__}"
        self.__name__ = fonction.__name__
        self.__doc__ = fonction.__doc__
        self.__wrapped__ = fonction

    # appel direct : exactement la fonction d'origine
    def __call__(self, *args, **kwargs):
        return self._fonction(*args, **kwargs)

    def _corps(self, args, kwargs, fil_dedie=True):
        with _places:
            try:
                self._fonction(*args, **kwargs)
            except Exception:
                logger.exception("Tâche de fond %s en échec", self.name)
            finally:
                if fil_dedie:
                    try:
                        from django.db import connections
                        connections.close_all()   # connexion propre à ce fil, jamais celle de l'appelant en mode synchrone
                    except Exception:
                        pass

    def _lancer(self, args, kwargs, attente):
        def demarrer():
            def cible():
                self._corps(args, kwargs)
            if attente and attente > 0:
                minuteur = threading.Timer(attente, cible)
                minuteur.daemon = True
                minuteur.name = f"tache-{self.name}"
                minuteur.start()
            else:
                fil = threading.Thread(target=cible, name=f"tache-{self.name}", daemon=True)
                fil.start()

        try:
            from django.db import transaction
            transaction.on_commit(demarrer)   # immédiat hors transaction ; après validation dans une transaction
        except Exception:
            demarrer()

    def apply_async(self, args=None, kwargs=None, countdown=None, **_ignores):
        args, kwargs = tuple(args or ()), dict(kwargs or {})
        identifiant = uuid.uuid4().hex
        if _synchrone():
            self._corps(args, kwargs, fil_dedie=False)
        else:
            self._lancer(args, kwargs, countdown)
        return _Resultat(identifiant)

    def delay(self, *args, **kwargs):
        return self.apply_async(args=args, kwargs=kwargs)

    def __repr__(self):
        return f"<Tache {self.name}>"


class _Resultat:
    """Valeur de retour de .delay() : seul l'identifiant est conservé (l'état se suit par le `statut` des modèles)."""

    def __init__(self, identifiant):
        self.id = identifiant


def shared_task(*dec_args, **dec_kwargs):
    """Utilisable en `@shared_task` ou `@shared_task(name="...")`."""
    if len(dec_args) == 1 and callable(dec_args[0]) and not dec_kwargs:
        return Tache(dec_args[0])

    def decorateur(fonction):
        return Tache(fonction, nom=dec_kwargs.get("name"))
    return decorateur
