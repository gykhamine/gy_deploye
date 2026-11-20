"""Synchronisation des catalogues prédéfinis (formules, graphiques BI) — une seule implémentation.

Ancien fonctionnement : un update_or_create par entrée (184 formules, 131 graphiques), chacun dans sa propre
transaction, relancé à CHAQUE requête pour les formules. Sous SQLite, deux requêtes simultanées (page du
tableau de bord qui charge plusieurs listes en parallèle) se disputaient le verrou d'écriture → « database
is locked » → erreur 500. Désormais : une lecture groupée, seules les entrées nouvelles ou modifiées sont
écrites, en UNE transaction, une seule fois par processus (verrou), rien d'écrit si rien n'a changé.
Les entrées d'une entreprise (entreprise renseignée) ne sont jamais touchées."""
from engine.i18n import t
import logging
import threading

logger = logging.getLogger(__name__)
_verrou = threading.Lock()
_faits: set = set()


def synchroniser_catalogue(modele, nom, entrees, valeurs_de):
    """`valeurs_de(entree)` → dict des champs à tenir à jour (hors entreprise/slug)."""
    if nom in _faits:
        return
    with _verrou:
        if nom in _faits:
            return
        from django.db import transaction
        existants = {}
        for o in modele.objects.filter(entreprise=None, slug__in=[e["slug"] for e in entrees]):
            existants.setdefault(o.slug, o)
        a_creer, a_maj = [], []
        for e in entrees:
            valeurs = valeurs_de(e)
            o = existants.get(e["slug"])
            if o is None:
                a_creer.append(modele(entreprise=None, slug=e["slug"], **valeurs))
            elif any(getattr(o, k) != v for k, v in valeurs.items()):
                for k, v in valeurs.items():
                    setattr(o, k, v)
                a_maj.append(o)
        if a_creer or a_maj:
            with transaction.atomic():
                if a_creer:
                    modele.objects.bulk_create(a_creer, batch_size=200)
                if a_maj:
                    modele.objects.bulk_update(a_maj, list(valeurs_de(entrees[0]).keys()), batch_size=200)
            logger.info(t("engine/catalogue_sync.py:g:44:25"), nom, len(a_creer), len(a_maj))
        _faits.add(nom)
