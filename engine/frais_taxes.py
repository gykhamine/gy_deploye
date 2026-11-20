"""Taxe des événements de paiement mobile — calcul pur (sans Django), utilisé par wallet.py."""
from engine.i18n import t, tf
from engine.montant import Montant   # Decimal tolérant aux float : tous les calculs d'argent ci-dessous se font en Decimal
from engine.i18n import tf as _gtf  # noqa: E402

EVENEMENTS_FRAIS = ("depot", "retrait", "transfert")


def calculer_frais(frais, evenement: str, montant: float) -> float:
    """Taxe d'un événement de paiement mobile (depot | retrait | transfert), en FCFA, selon les cases cochées par l'agent :
    pourcentage du montant (`taux_<evt>`) et/ou montant fixe (`fixe_<evt>`) ; les deux cochés s'additionnent, aucun coché = 0.
    Ne dépasse jamais le montant lui-même (un dépôt ne peut pas rendre le solde négatif)."""
    montant = Montant(montant)
    total = Montant(0)
    if getattr(frais, f"taux_{evenement}_actif"):
        total += montant * getattr(frais, f"taux_{evenement}") / 100
    if getattr(frais, f"fixe_{evenement}_actif"):
        total += getattr(frais, f"fixe_{evenement}")
    return round(min(max(total, 0), montant), 2)


def tva_sur(frais, base: float) -> float:
    """TVA (taux `taux_tva` %, si `taux_tva_actif`) due sur `base` : une taxe ou le prix HT d'un service. 0 si désactivée."""
    return round(max(Montant(base), 0) * frais.taux_tva / 100, 2) if getattr(frais, "taux_tva_actif", False) else Montant(0)


# ── Prix des services payants (pages et actions mises à disposition des clients) ──
SERVICES_CATEGORIES = ("tables", "media", "ia", "ontology", "recherche", "enregistrement", "dashboard_bi", "ontologie",
                       "geolocalisation", "series_temporelles", "statistiques", "anova", "analyse_factorielle")


def _valeur_service(config) -> float:
    valeur = config.get("valeur") if isinstance(config, dict) else config
    return Montant(valeur) if isinstance(valeur, (int, float)) and not isinstance(valeur, bool) and valeur == valeur else Montant(0)


def service_payant(config) -> bool:
    return _valeur_service(config) > 0


def prix_service(config, solde: float) -> float:
    """Prix en FCFA d'un service : `config` = nombre (montant fixe) ou {"mode": "taux", "valeur": %} = pourcentage du solde du wallet du client."""
    valeur = _valeur_service(config)
    return round(Montant(solde) * valeur / 100 if isinstance(config, dict) and config.get("mode") == "taux" else valeur, 2)


def valider_services(donnees) -> dict:
    """Configuration reçue de l'agent -> forme enregistrée (nombre = fixe ; {"mode": "taux", "valeur"} = % du wallet). ValueError si invalide."""
    import math
    if not isinstance(donnees, dict):
        raise ValueError(t("engine/frais_taxes.py:g:42:26"))
    sortie = {}
    for categorie, config in donnees.items():
        if categorie not in SERVICES_CATEGORIES:
            raise ValueError(_gtf("rg8.msg.035", categorie=categorie))
        mode = config.get("mode", "fixe") if isinstance(config, dict) else "fixe"
        valeur = config.get("valeur") if isinstance(config, dict) else config
        if mode not in ("fixe", "taux") or isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur) or valeur < 0:
            raise ValueError(tf("engine/frais_taxes.py:g:50:30", v1=categorie))
        if mode == "taux" and valeur > 100:
            raise ValueError(tf("engine/frais_taxes.py:g:52:30", v1=categorie))
        sortie[categorie] = {"mode": "taux", "valeur": float(valeur)} if mode == "taux" else float(valeur)
    return sortie
