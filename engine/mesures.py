"""
Calcul des mesures widget BI (MesureWidget).

Une mesure est une colonne virtuelle agrégée attachée à un WidgetGraphiqueBI,
calculée à partir d'une colonne réelle de la table source via une FormuleCalcul
(cible_type='colonne') — exactement le même bac à sable que les KPI
(kpi.py / executor.executer_formule_securisee).

Résultat : un scalaire numérique ajouté comme colonne constante dans le
DataFrame transmis à dessiner(dataframe, colonnes), ce qui permet au graphique
de tracer par exemple « Ventes » vs « Objectif (mesure SUM * 1.1) » sans
modifier la table source.

Exemple Power BI équivalent :
    Mesure DAX : Objectif = SUM(Ventes[Montant]) * 1.1
    → ici : FormuleCalcul Somme sur colonne « montant » → scalaire injecté
    comme colonne constante « objectif » dans le DataFrame.
"""
from engine.i18n import tf
from engine.i18n import td
from .executor import executer_formule_securisee, parse_modules_autorises, DEFAULT_MODULES_AUTORISES_FORMULE


def _slug_mesure(mesure) -> str:
    """Slug normalisé utilisé comme nom de colonne virtuelle."""
    import re
    slug = re.sub(r"[^a-z0-9_]", "_", mesure.nom.lower().strip())
    return slug or f"mesure_{mesure.id}"


def calculer_mesures(widget, df, modules_autorises=None):
    """Calcule toutes les MesureWidget de `widget` dans l'ordre et injecte
    chaque résultat comme colonne constante dans `df`.

    Retourne (df_enrichi, erreurs) :
    - df_enrichi : DataFrame avec les colonnes virtuelles ajoutées.
    - erreurs : dict {nom_mesure: message_erreur} pour les mesures en échec
      (les autres colonnes restent disponibles — partial failure tolérée).

    Une mesure peut utiliser une colonne virtuelle créée par une mesure
    précédente (ordre croissant) — même logique de propagation que
    recalculer_table_calcul() pour les dépendances en chaîne.
    """
    import pandas as pd

    mesures = list(widget.mesures.select_related("formule", "champ_cible").order_by("ordre", "id"))
    if not mesures:
        return df, {}

    if modules_autorises is None:
        from .models import ConfigurationIA
        table = widget.tableau_bord.table_source
        cfg = ConfigurationIA.objects.filter(entreprise=table.entreprise).first()
        modules_autorises = parse_modules_autorises(
            cfg.modules_autorises_formule if cfg else "",
            DEFAULT_MODULES_AUTORISES_FORMULE,
        )

    table = widget.tableau_bord.table_source
    erreurs = {}

    for mesure in mesures:
        slug = _slug_mesure(mesure)
        col_source = mesure.champ_cible.slug

        # La colonne source peut être réelle (table) ou virtuelle (mesure précédente)
        if col_source not in df.columns:
            erreurs[mesure.nom] = tf("engine/mesures.py:g:67:35", v1=col_source)
            continue

        serie = df[col_source]

        res = executer_formule_securisee(
            mesure.formule.code,
            serie,
            timeout_secondes=table.timeout_execution_secondes,
            modules_autorises=modules_autorises,
        )

        if not res.get("ok"):
            erreurs[mesure.nom] = res.get("erreur", td("engine/mesures.py:g:79:53"))
            continue

        try:
            valeur = float(res["resultat"])
        except (TypeError, ValueError):
            erreurs[mesure.nom] = td("engine/mesures.py:g:86:35")
            continue

        # Injection comme colonne constante (même longueur que le DataFrame)
        df = df.copy()
        df[slug] = valeur

    return df, erreurs
