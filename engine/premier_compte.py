"""Premier compte (super administrateur) : son entreprise et son rattachement existent dès la création du compte.

Avant : le rattachement (MembreEntreprise « admin ») n'était créé qu'à la première visite de la page « / ». Tant qu'il manquait, la liste
des employés, le profil et les autres vues qui cherchent le membre du compte ne le trouvaient pas : le premier compte n'apparaissait nulle part.
Maintenant : créé à la création du compte (createsuperuser, admin…) ET à chaque connexion d'un super administrateur encore sans rattachement
(comptes déjà créés avant cette version). Idempotent : sans effet si le rattachement existe.
"""
import logging

from django.conf import settings
from django.db import transaction

journal = logging.getLogger("engine.premier_compte")


def garantir_rattachement(utilisateur):
    """Retourne le MembreEntreprise du super administrateur, en le créant (avec son entreprise) s'il manque. None pour tout autre compte."""
    if utilisateur is None or not getattr(utilisateur, "pk", None) or not getattr(utilisateur, "is_superuser", False):
        return None
    from .models import Entreprise, MembreEntreprise
    from .securite import slug_entreprise_libre
    with transaction.atomic():
        membre = MembreEntreprise.objects.select_related("entreprise").filter(utilisateur=utilisateur).first()
        if membre is not None:
            return membre
        if getattr(settings, "GYPLATFORM_MULTI_ENTREPRISE", False):
            # Plateforme multi-entreprises : l'opérateur n'est JAMAIS rattaché d'office à l'entreprise d'un client : il a la sienne, créée une fois.
            # Le nom est unique en base : si le nom vide (identité d'origine) est déjà pris, on lui donne un nom TECHNIQUE avec le préfixe
            # « automatique » que l'interface ignore (marque.nom_affiche) : le nom d'utilisateur n'est jamais montré comme nom d'entreprise.
            from .marque import _PREFIXE_NOM_AUTO
            nom = ""   # première installation : nom vide = identité d'origine
            if Entreprise.objects.filter(nom="").exists():
                nom = f"{_PREFIXE_NOM_AUTO}{utilisateur.get_username()}"
                n = 1
                while Entreprise.objects.filter(nom__iexact=nom).exists():
                    n += 1
                    nom = f"{_PREFIXE_NOM_AUTO}{utilisateur.get_username()} {n}"
            entreprise = Entreprise.objects.create(nom=nom, slug=slug_entreprise_libre(f"entreprise-{utilisateur.get_username()}"))
        else:
            entreprise = Entreprise.objects.order_by("id").first() or Entreprise.objects.create(
                nom="", slug=f"entreprise-{utilisateur.get_username()}")
        membre, _ = MembreEntreprise.objects.get_or_create(utilisateur=utilisateur, entreprise=entreprise, defaults={"role": "admin"})
        return membre


def _apres_creation(sender, instance, created=False, raw=False, **kw):
    if created and not raw and getattr(instance, "is_superuser", False):
        _essayer(instance)


def _apres_connexion(sender, user=None, **kw):
    _essayer(user)


def _essayer(utilisateur):
    try:
        garantir_rattachement(utilisateur)
    except Exception:   # jamais bloquer la création du compte ni la connexion : la page « / » refera la tentative
        journal.exception("rattachement du super administrateur non créé")


def installer():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.signals import user_logged_in
    from django.db.models.signals import post_save
    post_save.connect(_apres_creation, sender=get_user_model(), dispatch_uid="gy_premier_compte_creation")
    user_logged_in.connect(_apres_connexion, dispatch_uid="gy_premier_compte_connexion")
