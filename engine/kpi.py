"""
Calcul et suivi des indicateurs KPI (voir models.IndicateurKPI /
ValeurKPIHistorique).

Même principe que calculs.py : on construit un DataFrame de la table
source (casté selon le schéma EAV), on prend la colonne cible du KPI et on
exécute la formule choisie (FormuleCalcul, cible_type="colonne" — même
bibliothèque que les tables de calcul : Somme, Moyenne, COUNT...) dans le
bac à sable partagé (executor.executer_formule_securisee).

Contrairement aux lignes de calcul, chaque recalcul réussi dont la valeur
diffère du dernier point enregistré ajoute une entrée à l'historique du
KPI (ValeurKPIHistorique) : c'est ce qui permet de tracer sa tendance dans
le temps, indépendamment du cache "valeur actuelle" (même mécanique de
péremption que WidgetGraphiqueBI/widget_a_jour, voir empreinte_donnees).
"""
from engine.i18n import td
from engine.i18n import t, tf
from django.utils import timezone

from .executor import _appliquer_schema, executer_formule_securisee
from .metadata import empreinte_donnees, schema_pour_executor
from engine.i18n import t as _gt  # noqa: E402


def _dataframe_table_source(table_dynamique, filtre=None):
    """DataFrame de la table — ou de tout son groupe (calculs._dataframe_table_source, une seule
    implémentation). `filtre` optionnel ({"colonne", "valeur"}) restreint les lignes pour le filtre croisé."""
    from .calculs import _dataframe_table_source as _source

    df = _source(table_dynamique, groupe=True)   # lecture Parquet (parquet_store.donnees_analytiques)
    if filtre and filtre.get("colonne") in df.columns:
        df = df[df[filtre["colonne"]].astype(str) == str(filtre.get("valeur"))]
    return df


def _appliquer_etapes(df, etapes):
    """Formules enchaînées d'un KPI, ligne par ligne (même moteur que la gestion des données : calc_champ.calculer_valeur_champ).
    L'étape n écrit la colonne « rn » dans `df`, lisible par les étapes suivantes. Retourne (slug de la dernière, erreur)."""
    import pandas as pd
    from .calc_champ import calculer_valeur_champ

    lignes = df.to_dict("records")
    if any(isinstance(e, dict) and "t" in e for e in etapes):
        # Programme (même éditeur et même moteur que la table dynamique : formules_programme.py) : variables, conditions, boucles.
        from .formules_programme import executer_programme
        valeurs = []
        for i, ligne in enumerate(lignes, start=1):
            valeur, erreur = executer_programme(etapes, ligne)
            if erreur:
                return None, tf("engine/kpi.py:g:48:30", v1=i, v2=erreur)
            valeurs.append(valeur)
        df["resultat_kpi"] = pd.to_numeric(pd.Series(valeurs, index=df.index), errors="coerce")
        return "resultat_kpi", None
    for n, etape in enumerate(etapes, start=1):
        operandes = list(etape.get("operandes") or [])
        manquants = [o for o in operandes if o not in df.columns]
        if not operandes or manquants:
            return None, tf("engine/kpi.py:g:56:26", v1=n, v2=', '.join(manquants) or t("engine/kpi.py:g:58:87"))
        valeurs = []
        for ligne in lignes:
            valeur, _erreur = calculer_valeur_champ(ligne, etape.get("operateur"), operandes)
            ligne[f"r{n}"] = valeur
            valeurs.append(valeur)
        df[f"r{n}"] = pd.to_numeric(pd.Series(valeurs, index=df.index), errors="coerce")
    return f"r{len(etapes)}", None


def calculer_kpi(kpi, sauvegarder: bool = True, historiser: bool = True, filtre=None):
    """Exécute la formule de `kpi`, retourne (valeur, erreur). `filtre` +
    sauvegarder=False : calcul à la volée pour le filtre croisé, sans
    polluer le cache ni l'historique."""
    table = kpi.table_source

    def _echec(erreur):
        if sauvegarder:
            kpi.statut = "echec"
            kpi.erreur = erreur
            kpi.calcule_le = timezone.now()
            kpi.save(update_fields=["statut", "erreur", "calcule_le"])
        return None, erreur

    if not kpi.champ_cible_id and not kpi.etapes:
        return _echec(t("engine/kpi.py:g:81:23"))

    df = _dataframe_table_source(table, filtre=filtre)
    if df.empty:
        return _echec(t("engine/kpi.py:g:85:23") if filtre else t("engine/kpi.py:g:85:97"))

    if kpi.etapes:
        slug, erreur = _appliquer_etapes(df, kpi.etapes)
        if erreur:
            return _echec(erreur)
    else:
        slug = kpi.champ_cible.slug
    if slug not in df.columns:
        return _echec(tf("engine/kpi.py:g:94:23", v1=slug))

    from .executor import parse_modules_autorises, DEFAULT_MODULES_AUTORISES_FORMULE
    from .models import ConfigurationIA
    cfg = ConfigurationIA.objects.filter(entreprise=table.entreprise).first()
    modules_autorises = parse_modules_autorises(
        cfg.modules_autorises_formule if cfg else "", DEFAULT_MODULES_AUTORISES_FORMULE
    )

    res = executer_formule_securisee(
        kpi.formule.code, df[slug],
        timeout_secondes=table.timeout_execution_secondes,
        modules_autorises=modules_autorises,
    )

    if not res.get("ok"):
        return _echec(res.get("erreur", td("engine/kpi.py:g:111:41")))

    try:
        valeur = float(res["resultat"])
    except (TypeError, ValueError):
        return _echec(t("engine/kpi.py:g:115:23"))

    if sauvegarder:
        premier_calcul = kpi.valeur_actuelle is None   # 1re valeur jamais calculée : un KPI neuf déjà à l'objectif est notifié
        kpi.valeur_actuelle = valeur
        kpi.erreur = ""
        kpi.statut = "pret"
        kpi.empreinte_donnees = empreinte_donnees(table)
        kpi.calcule_le = timezone.now()
        kpi.save(update_fields=[
            "valeur_actuelle", "erreur", "statut", "empreinte_donnees", "calcule_le",
        ])

        if historiser:
            dernier = kpi.historique.order_by("-calcule_le").first()
            if dernier is None or dernier.valeur != valeur:
                kpi.historique.create(valeur=valeur)

        _notifier_si_entree_en_alerte(kpi, premier_calcul=premier_calcul)

    return valeur, None


def _notifier_si_entree_en_alerte(kpi, premier_calcul=False):
    """Crée une Notification quand `kpi.etat` (propriété du modèle) change d'état, une seule fois par ENTRÉE dans l'état :
    - « alerte » / « danger » : notifier_kpi_alerte, dédoublonné par `dernier_etat_alerte` ; si le KPI redevient « succes »
      ou None, on réarme (une future rechute renotifiera).
    - « succes » (objectif atteint) : notifier_kpi_objectif_atteint, dédoublonné par `objectif_notifie`. On notifie quand le KPI
      VIENT d'atteindre son objectif (il était en alerte/danger) ou au tout premier calcul. Un KPI déjà à l'objectif avant
      l'existence de cette notification est seulement mémorisé, sans notification (pas d'avalanche à la mise à jour)."""
    import logging
    etat = kpi.etat
    precedent = kpi.dernier_etat_alerte
    if etat in ("alerte", "danger"):
        champs = []
        if kpi.objectif_notifie:      # a quitté l'objectif : la prochaine atteinte sera notifiée
            kpi.objectif_notifie = False
            champs.append("objectif_notifie")
        if precedent != etat:
            from .notifications import notifier_kpi_alerte
            notifier_kpi_alerte(kpi)
            # Tâches conditionnelles catégorie "kpi" (voir engine/declencheurs.py) —
            # même dédoublonnage que la notification : une seule fois par ENTRÉE
            # dans l'état alerte/danger, pas à chaque recalcul tant qu'il y reste.
            from . import declencheurs as _declencheurs
            _declencheurs.evaluer_conditions(
                kpi.table_source.entreprise_id, "kpi", table_id=kpi.table_source_id,
                contexte={"kpi_id": kpi.id, "evenement": "alerte"},
            )
            kpi.dernier_etat_alerte = etat
            champs.append("dernier_etat_alerte")
        if champs:
            kpi.save(update_fields=champs)
    elif etat == "succes":
        a_notifier = not kpi.objectif_notifie and (bool(precedent) or premier_calcul)
        champs = []
        if precedent:                 # sort de l'alerte/danger : réarmement, comme avant
            kpi.dernier_etat_alerte = ""
            champs.append("dernier_etat_alerte")
        if not kpi.objectif_notifie:
            kpi.objectif_notifie = True
            champs.append("objectif_notifie")
        if champs:
            kpi.save(update_fields=champs)   # marqué AVANT d'envoyer : une panne d'envoi ne provoque jamais de doublon
        if a_notifier:
            try:
                from .notifications import notifier_kpi_objectif_atteint
                notifier_kpi_objectif_atteint(kpi)
            except Exception:
                logging.getLogger(__name__).exception(_gt("rg5.msg.021"), kpi.id)
            # Tâches conditionnelles catégorie "kpi" réglées sur « objectif atteint » (ou « les deux ») : même dédoublonnage
            # que la notification (une seule fois par atteinte). evaluer_conditions ne lève jamais.
            from . import declencheurs as _declencheurs
            _declencheurs.evaluer_conditions(
                kpi.table_source.entreprise_id, "kpi", table_id=kpi.table_source_id,
                contexte={"kpi_id": kpi.id, "evenement": "objectif"},
            )
    elif precedent:
        kpi.dernier_etat_alerte = ""
        kpi.save(update_fields=["dernier_etat_alerte"])


def recalculer_kpis_table(table_dynamique):
    """Recalcule tous les KPI définis sur cette table (dans l'ordre)."""
    from .models import IndicateurKPI
    kpis = list(
        IndicateurKPI.objects.filter(table_source_id__in=_ids_groupe_kpi(table_dynamique)).select_related("formule", "champ_cible").order_by("ordre", "id")
    )
    for kpi in kpis:
        calculer_kpi(kpi)
    return kpis


def _ids_groupe_kpi(table):
    from .vue_groupe import ids_groupe
    return ids_groupe(table)


def kpis_a_recalculer(table_dynamique):
    """Retourne les KPI dont la valeur en cache n'est plus à jour par
    rapport à l'état actuel des données de la table source — même usage que
    tableau_bord_bi.widgets_a_recalculer, pour ne relancer que ce qui a
    besoin de l'être."""
    empreinte_actuelle = empreinte_donnees(table_dynamique)
    kpis = list(
        table_dynamique.kpis.select_related("formule", "champ_cible").order_by("ordre", "id")
    )
    return [k for k in kpis if not k.kpi_a_jour(empreinte_actuelle)]


def surveiller_seuil_lignes(table):
    """KPI intégré « nombre de lignes » : notification (i18n) à 90 000 lignes (alerte) puis à 100 000 (plafond absolu), une seule fois par
    franchissement (dédoublonnage par cache, réarmé quand le nombre de lignes redescend sous le seuil). Tourne en arrière-plan avec la
    cascade d'écriture (ajout, import, suppression...), sans rien configurer."""
    from django.core.cache import cache
    nb = table.enregistrements.count()
    absolu = table.plafond_absolu_lignes(table.entreprise_id)   # plafond propre à l'entreprise ; alerte à 90 %
    seuils = (("alerte", absolu * 9 // 10), ("plafond", absolu))
    cles = {niveau: f"kpi_lignes_{niveau}_{table.id}" for niveau, _ in seuils}
    for niveau, seuil in seuils:
        if nb < seuil:
            cache.delete(cles[niveau])   # réarmement
    atteints = [niveau for niveau, seuil in seuils if nb >= seuil]
    nouveaux = [niveau for niveau in atteints if cache.add(cles[niveau], 1, None)]
    if nouveaux:   # un seul message : le plus grave des seuils franchis
        from .notifications import notifier_seuil_lignes
        notifier_seuil_lignes(table, nb)


def rafraichir_kpis_perimes(tables):
    """Recalcule, SANS attendre qu'on ouvre le tableau de bord, les KPI périmés des `tables` données — dernier
    maillon de la cascade d'écriture (voir calculs.recalculer_lignes_pour_table_source), comme les formules de
    champ et les tables de calcul : une donnée qui change met à jour les KPI, leur historique et leurs alertes
    (notifications, tâches conditionnelles) aussitôt. Seuls les KPI dont l'empreinte des données a changé sont
    relancés (kpis_a_recalculer) ; une table dénaturée (documents) est ignorée, comme par les calculs.
    Ne lève jamais : un KPI en échec est marqué « echec » sans empêcher les autres ni l'écriture d'origine.
    Retourne la liste des KPI recalculés."""
    import logging
    recalcules = []
    for table in tables:
        if getattr(table, "denaturee", False):
            continue
        try:
            surveiller_seuil_lignes(table)
        except Exception:
            logging.getLogger(__name__).exception("seuil lignes", getattr(table, "id", None))
        try:
            a_faire = kpis_a_recalculer(table)
        except Exception:
            logging.getLogger(__name__).exception(t("engine/kpi.py:g:241:51"), getattr(table, "id", None))
            continue
        for kpi in a_faire:
            try:
                calculer_kpi(kpi)
                recalcules.append(kpi)
            except Exception:
                logging.getLogger(__name__).exception(t("engine/kpi.py:g:248:55"), kpi.id)
                try:
                    kpi.statut = "echec"
                    kpi.erreur = t("engine/kpi.py:g:252:34")
                    kpi.save(update_fields=["statut", "erreur"])
                except Exception:
                    pass
    return recalcules
