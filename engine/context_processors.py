"""Contexte commun : dernière table ouverte, pour le bouton « ← Retour à {table} » des sidebars qui n'ont pas de table propre."""


def retour_table(request):
    try:
        return {"retour_table": request.session.get("retour_table")}
    except Exception:   # pas de session (requête interne, test…)
        return {}


def marque_entete(request):
    """Logo et nom ACTUELS de l'entreprise pour le bandeau des pages (engine/_marque_entete.html) : lus à chaque affichage, donc un
    changement de logo ou de nom est visible dès le rechargement de la page, sans attendre /api/marque/. Même entreprise que cette API
    (une seule par installation). Rien d'inventé : sans logo ni nom réglés, le gabarit garde l'identité d'origine."""
    try:
        from . import marque
        from .views_marque import _entreprise_publique
        e = _entreprise_publique(request)
        logo = getattr(e, "logo", None)
        return {"marque_logo_url": logo.url if logo else None, "marque_nom": marque.nom_marque(e) or ""}
    except Exception:   # base indisponible, migration en cours, test… : le bandeau garde le logo d'origine
        return {}


def langue_interface(request):
    """Sélecteur de langue personnel (Profil, espace client) : langues disponibles, choix de l'utilisateur et nom de la langue par défaut du
    système (celle de l'administrateur), pour afficher « Par défaut (…) »."""
    try:
        from . import i18n
        return {
            "langue_html": i18n.langue_courante(),
            "langues_interface": [{"code": c, "nom": i18n.NOMS_LANGUES.get(c, c)} for c in i18n.langues_disponibles()],
            "langue_choisie": i18n.langue_choisie(request),
            "langue_systeme_nom": (lambda c: i18n.NOMS_LANGUES.get(c, c))(i18n.langue_serveur_effective(request)),
        }
    except Exception:
        return {}

