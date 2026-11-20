"""Délégation : l'administrateur confie un ou plusieurs pouvoirs qui lui sont propres à un agent précis (MembreEntreprise.delegations).

Un seul point de décision, `a_pouvoir(membre, droit, …)` : vrai pour l'administrateur, et pour un agent à qui CE droit a été confié.
Chaque droit correspond à UNE fonctionnalité réservée à l'administrateur : on peut en confier un seul, deux ou trois, une famille entière,
ou tout (« administration », la liberté absolue). Les droits sont regroupés en familles uniquement pour l'affichage et pour cocher d'un coup.

  finances          caisse_entrees, caisse_sorties, payer_caisse, portefeuilles, journal_bilan, remuneration, comptabilite,
                    especes_agent, especes_habilitations, especes_mouvements, politique_transfert, paiements_config
  configuration     config_ia, modeles_ia
  documents         documents_modeles, documents_apparence, documents_emettre, documents_annuler, documents_archiver
  personnalisation  marque, couleurs, usabilite, identification
  gestion           employes, reglages_entreprise, cloture_cycle, cartes, cles_api, reprises_poste, saisons, messagerie_admin,
                    publicites_equipe, toutes_tables, decisions, sms, resume_general, blockchain, spark, actions_admin

  administration    liberté absolue : tous les droits ci-dessus, y compris ceux qui seront ajoutés plus tard. À ne confier qu'à un agent de confiance.

Droits sensibles (SENSIBLES) : `employes`, `cles_api` et `actions_admin` permettent de s'accorder d'autres droits ou d'agir à la place de quelqu'un ;
`spark` ouvre la page « Calcul distribué » (configuration du cluster de calcul : chemins et processus que Python lance, secret du cluster), réservée
sinon au super-administrateur Django : seul un AGENT à qui il est confié (ou la liberté absolue) y accède en plus du super-administrateur.
Un agent qui détient `employes` ne peut jamais confier à un autre agent un droit qu'il n'a pas lui-même (voir `limiter_au_delegant`).

Compatibilité : une valeur déjà enregistrée sous l'ancien format (nom de famille : « finances », « documents »…) vaut tous les droits de cette famille.
"""
from engine.i18n import t as _t
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401

ABSOLU = "administration"

GROUPES = ("finances", "configuration", "documents", "personnalisation", "gestion")

# droit -> famille (l'ordre d'insertion est l'ordre d'affichage)
DROITS = {
    "caisse_entrees": "finances",
    "caisse_sorties": "finances",
    "payer_caisse": "finances",
    "portefeuilles": "finances",
    "journal_bilan": "finances",
    "remuneration": "finances",
    "comptabilite": "finances",
    "especes_agent": "finances",
    "especes_habilitations": "finances",
    "especes_mouvements": "finances",
    "politique_transfert": "finances",
    "paiements_config": "finances",
    "config_ia": "configuration",
    "modeles_ia": "configuration",
    "algorithmes_ia": "configuration",   # CRITIQUE : installer du code d'algorithme (voir CRITIQUES) — super-administrateur seulement
    "documents_modeles": "documents",
    "documents_apparence": "documents",
    "documents_emettre": "documents",
    "documents_annuler": "documents",
    "documents_archiver": "documents",
    "marque": "personnalisation",
    "couleurs": "personnalisation",
    "usabilite": "personnalisation",
    "identification": "personnalisation",
    "employes": "gestion",
    "reglages_entreprise": "gestion",
    "cloture_cycle": "gestion",
    "cartes": "gestion",
    "cles_api": "gestion",
    "reprises_poste": "gestion",
    "saisons": "gestion",
    "messagerie_admin": "gestion",
    "publicites_equipe": "gestion",
    "toutes_tables": "gestion",
    "decisions": "gestion",
    "sms": "gestion",
    "resume_general": "gestion",
    "blockchain": "gestion",
    "spark": "gestion",
    "actions_admin": "gestion",
}

SENSIBLES = frozenset({"employes", "cles_api", "actions_admin", "spark"})

# §étanchéité (audit 2026-11-20) — droits CRITIQUES : ils ouvrent l'exécution de code hors du bac à sable ou la configuration du
# serveur hôte (installation d'un nouvel ALGORITHME : du code écrit dans un dossier PARTAGÉ par toutes les entreprises et exécuté
# dans le processus serveur — créer un modèle avec un algorithme existant reste le droit « modeles_ia » ; chemins et processus que Python lance
# pour le cluster de calcul). Seul le super-administrateur Django les exerce (les vues testent `request.user.is_superuser`) :
# ni l'administrateur d'entreprise, ni un agent délégué, ni la « liberté absolue » ne les obtiennent, et ils ne se délèguent pas.
# Les noms restent dans DROITS pour que `a_pouvoir(membre, "spark")` ne lève pas d'erreur : il répond simplement False.
CRITIQUES = frozenset({"algorithmes_ia", "spark"})

_TOUS = (frozenset(DROITS) - CRITIQUES) | {ABSOLU}


def _droits_de_groupe(groupe) -> list:
    return [d for d, g in DROITS.items() if g == groupe and d not in CRITIQUES]


def droits_effectifs(membre) -> frozenset:
    """Tous les droits dont `membre` dispose : l'administrateur et la liberté absolue ouvrent tout (y compris ABSOLU)."""
    if membre is None:
        return frozenset()
    if membre.role == "admin":
        return _TOUS
    if membre.role != "agent":
        return frozenset()
    confies = set()
    for valeur in membre.delegations or []:
        if valeur == ABSOLU:
            return _TOUS
        if valeur in DROITS:
            confies.add(valeur)
        elif valeur in GROUPES:   # ancien format : une famille entière
            confies.update(_droits_de_groupe(valeur))
    return frozenset(confies) - CRITIQUES


def a_pouvoir(membre, droit, *autres) -> bool:
    """Vrai si `membre` a le droit demandé ; avec plusieurs droits, vrai s'il en a AU MOINS UN (accès à une page qui regroupe plusieurs fonctions).
    Un nom inconnu lève ValueError : une faute de frappe ne doit jamais ouvrir ni fermer un accès en silence."""
    voulus = (droit,) + autres
    for d in voulus:
        if d not in DROITS:
            raise ValueError(_i18n_tf("rg2.delegation.1", v1=d))
    effectifs = droits_effectifs(membre)
    return any(d in effectifs for d in voulus)


def a_un_pouvoir_de(membre, groupe) -> bool:
    """Vrai si `membre` a au moins un droit de la famille `groupe` (afficher une page ou un bouton qui regroupe la famille)."""
    if groupe not in GROUPES:
        raise ValueError(_i18n_tf("rg2.delegation.2", v1=groupe))
    return a_pouvoir(membre, *_droits_de_groupe(groupe))


def a_liberte_absolue(membre) -> bool:
    return ABSOLU in droits_effectifs(membre)


def liste_droits(membre) -> list:
    """Droits effectifs de `membre`, dans l'ordre canonique (pour le profil et l'interface). ABSOLU ne figure que s'il est détenu."""
    effectifs = droits_effectifs(membre)
    return [d for d in DROITS if d in effectifs] + ([ABSOLU] if ABSOLU in effectifs else [])


def groupes() -> list:
    """Catalogue affiché dans Employés > Droits : familles, droits de chaque famille, et la liberté absolue à part."""
    return [{
        "slug": g,
        "nom": _t(f"engine/delegation.py:groupe:{g}"),
        "description": _t(f"engine/delegation.py:groupe_desc:{g}"),
        "droits": [{"slug": d, "nom": _t(f"engine/delegation.py:droit:{d}"), "description": _t(f"engine/delegation.py:desc:{d}"),
                    "sensible": d in SENSIBLES} for d in _droits_de_groupe(g)],
    } for g in GROUPES]


def absolu() -> dict:
    return {"slug": ABSOLU, "nom": _t("engine/delegation.py:droit:administration"), "description": _t("engine/delegation.py:desc:administration")}


def nettoyer(valeur) -> list:
    """Ne garde que les droits connus, sans doublon, dans l'ordre canonique. Un nom de famille (ancien format) devient ses droits ;
    la liberté absolue, si elle est demandée, remplace tout le reste."""
    voulus = {str(v) for v in (valeur or []) if isinstance(v, (str, int))} - CRITIQUES
    if ABSOLU in voulus:
        return [ABSOLU]
    for g in GROUPES:
        if g in voulus:
            voulus.update(_droits_de_groupe(g))
    return [d for d in DROITS if d in voulus and d not in CRITIQUES]


def limiter_au_delegant(delegant, anciens, voulus) -> list:
    """Droits à enregistrer pour l'agent cible quand `delegant` modifie sa délégation : `delegant` ne peut ni ajouter ni retirer
    un droit qu'il n'a pas lui-même. Le résultat est (anciens hors de sa portée) + (voulus dans sa portée)."""
    portee = droits_effectifs(delegant)
    if ABSOLU in portee:
        return nettoyer(voulus)
    anciens_effectifs = droits_effectifs_de_liste(anciens)
    voulus_nets = set(nettoyer(voulus))
    garde = {d for d in anciens_effectifs if d not in portee}
    ajoute = {d for d in voulus_nets if d in portee}
    return [d for d in DROITS if d in garde | ajoute and d not in CRITIQUES] + ([ABSOLU] if ABSOLU in anciens_effectifs and ABSOLU not in portee else [])


def droits_effectifs_de_liste(valeurs) -> frozenset:
    """Même expansion que droits_effectifs, pour une liste brute (ancien ou nouveau format) sans membre."""
    if ABSOLU in (valeurs or []):
        return _TOUS
    return frozenset(nettoyer(valeurs))
