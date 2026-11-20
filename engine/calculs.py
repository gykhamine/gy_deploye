"""
Résolution des cibles et exécution des formules pour les tables de calcul
(voir models.TableCalcul / FormuleCalcul / LigneCalcul).

Principe : on construit un DataFrame de la table source (casté selon le
schéma EAV, comme pour le canal d'analyse — voir executor._appliquer_schema
et metadata.schema_pour_executor), on le restreint selon le type de cible
de la ligne de calcul (colonne entière / ligne précise / plage de lignes),
puis on l'exécute dans le bac à sable (executor.executer_formule_securisee).
"""
from engine.i18n import td
from engine.i18n import t, tf
from django.utils import timezone

from .executor import (_appliquer_schema, executer_formule_securisee, parse_modules_autorises,
                       DEFAULT_MODULES_AUTORISES_FORMULE)
from .metadata import schema_pour_executor


def recalculer_champ_formule(enregistrement, champ):
    """Recalcule la valeur d'UN champ calculé (type_champ='formule') pour
    UNE ligne, et la stocke comme un ValeurChamp normal — voir
    models.ChampDynamique.formule_expression. `ligne` passé au script est
    un dict des AUTRES champs de la même ligne (le champ calculé lui-même
    est exclu pour éviter toute confusion avec une valeur potentiellement
    obsolète). Table NoSQL (voir nosql_store.py) : même calcul, résultat
    stocké dans valeurs_json à la place d'un ValeurChamp."""
    import json as _json

    if champ.type_champ != "formule" or not champ.formule_expression.strip():
        return
    ligne = enregistrement.to_dict()
    ligne.pop(champ.slug, None)
    resultat = executer_formule_securisee(champ.formule_expression, ligne)
    valeur_texte = _json.dumps(resultat.get("resultat")) if resultat.get("ok") else ""
    if enregistrement.table.est_nosql:
        from . import nosql_store
        nosql_store.ecrire_valeur_calculee(enregistrement, champ, valeur_texte)
    else:
        from .models import ValeurChamp
        ValeurChamp.objects.update_or_create(
            enregistrement=enregistrement, champ=champ,
            defaults={"valeur": valeur_texte},
        )


def _assurer_horodatages(enregistrement):
    """Champs « horodatage » : date et heure du serveur (fuseau du projet), ecrites UNE fois si la ligne n'en a pas."""
    champs = list(enregistrement.table.champs.filter(type_champ="horodatage"))
    if not champs:
        return
    from django.utils import timezone
    maintenant = timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S")
    if enregistrement.table.est_nosql:
        from . import nosql_store
        deja = enregistrement.valeurs_json or {}
        for champ in champs:
            if not deja.get(champ.slug):
                nosql_store.ecrire_valeur_calculee(enregistrement, champ, maintenant)
    else:
        from .models import ValeurChamp
        deja = set(ValeurChamp.objects.filter(enregistrement=enregistrement, champ__in=champs)
                   .exclude(valeur="").values_list("champ_id", flat=True))
        for champ in champs:
            if champ.id not in deja:
                ValeurChamp.objects.update_or_create(enregistrement=enregistrement, champ=champ, defaults={"valeur": maintenant})


def _assurer_id_client(enregistrement):
    """Champs « id_client » : qui a enregistré la ligne — numéro du client (lecteur), « agent » ou « administrateur » ;
    écrit UNE fois si la ligne n'en a pas (même mécanique que l'horodatage)."""
    champs = list(enregistrement.table.champs.filter(type_champ="id_client"))
    auteur = enregistrement.cree_par
    if not champs or auteur is None:
        return
    from .models import MembreEntreprise, ValeurChamp
    m = MembreEntreprise.objects.filter(utilisateur=auteur, entreprise=enregistrement.table.entreprise).first()
    if m is None:
        return
    qui = {"admin": "administrateur", "agent": "agent"}.get(m.role) or str(m.numero_client or auteur.id)
    if enregistrement.table.est_nosql:
        from . import nosql_store
        deja = enregistrement.valeurs_json or {}
        for champ in champs:
            if not deja.get(champ.slug):
                nosql_store.ecrire_valeur_calculee(enregistrement, champ, qui)
    else:
        deja = set(ValeurChamp.objects.filter(enregistrement=enregistrement, champ__in=champs)
                   .exclude(valeur="").values_list("champ_id", flat=True))
        for champ in champs:
            if champ.id not in deja:
                ValeurChamp.objects.update_or_create(enregistrement=enregistrement, champ=champ, defaults={"valeur": qui})


def assurer_constantes(enregistrement):
    """Recopie la valeur de chaque champ « constante » de la table (models.ChampDynamique, type_champ='constante',
    valeur dans options_choix) sur CETTE ligne si elle manque ou diffère : appelé par le signal post_save de
    ValeurChamp (apps.py) pour une table SQL, et explicitement après chaque écriture pour une table NoSQL
    (voir views.py, api_lignes/api_modifier_ligne — pas de ValeurChamp donc pas de signal). N'écrit rien si
    tout est à jour."""
    _assurer_horodatages(enregistrement)
    _assurer_id_client(enregistrement)
    champs = list(enregistrement.table.champs.filter(type_champ="constante"))
    if not champs:
        return
    if enregistrement.table.est_nosql:
        from . import nosql_store
        valeurs_json = enregistrement.valeurs_json or {}
        for champ in champs:
            texte = champ.normaliser_constante(champ.options_choix)
            if texte is None:
                continue
            valeur = nosql_store.coercer_valeur(champ, texte)
            if valeurs_json.get(champ.slug) != valeur:
                nosql_store.ecrire_valeur_calculee(enregistrement, champ, valeur)
                valeurs_json = dict(valeurs_json)
                valeurs_json[champ.slug] = valeur
    else:
        from .models import ValeurChamp
        existantes = dict(ValeurChamp.objects.filter(enregistrement=enregistrement, champ__in=champs)
                          .values_list("champ_id", "valeur"))
        for champ in champs:
            valeur = champ.normaliser_constante(champ.options_choix)
            if valeur is not None and existantes.get(champ.id) != valeur:
                ValeurChamp.objects.update_or_create(enregistrement=enregistrement, champ=champ,
                                                     defaults={"valeur": valeur})


def propager_constante(champ):
    """Recopie la constante `champ` sur TOUTES les lignes existantes de sa table (création ou changement de valeur),
    puis recalcule les champs calculés qui en dépendent."""
    for enregistrement in champ.table.enregistrements.all():
        assurer_constantes(enregistrement)
        recalculer_champs_formule_ligne(enregistrement)


def recalculer_champs_formule_ligne(enregistrement):
    """Recalcule TOUS les champs calculés de la table de cette ligne — voir
    apps.py (signal post_save sur ValeurChamp) : déclenché automatiquement à
    chaque sauvegarde d'un champ NON calculé de la ligne, pour que toute
    formule qui en dépend reste à jour."""
    champs_formule = list(enregistrement.table.champs.filter(type_champ="formule"))
    for champ in champs_formule:
        recalculer_champ_formule(enregistrement, champ)
    if champs_formule:
        # Événement "CalculationCompleted" (voir evenements.py) : seulement si
        # la table a au moins une formule, pour ne pas journaliser à chaque
        # sauvegarde de champ des tables qui n'en ont aucune.
        from . import evenements as _evenements
        _evenements.emettre(
            enregistrement.table.entreprise_id, "CalculationCompleted",
            table_id=enregistrement.table_id, enregistrement_id=enregistrement.id,
        )


def recalculer_champ_formule_table(champ):
    """Recalcule un champ calculé donné pour TOUTES les lignes existantes de
    sa table — appelé une fois quand on crée/modifie la formule elle-même
    (voir views.api_table_detail), pour remplir/actualiser la colonne
    immédiatement sans attendre la prochaine modification de chaque ligne."""
    for enregistrement in champ.table.enregistrements.all():
        recalculer_champ_formule(enregistrement, champ)


def _dataframe_table_source(table_dynamique, groupe=False, ecriture=False):
    """DataFrame de tous les enregistrements de la table, casté selon le schéma EAV, en ordre chronologique croissant (ligne n°1 =
    plus ancienne — c'est la convention utilisée par LigneCalcul.plage_debut/plage_fin/type_cible='ligne').

    Lecture UNIQUEMENT par parquet_store (point d'entrée unique, repli EAV géré là-bas) :
    groupe=True : les lignes de TOUTES les tables du groupe forment un seul DataFrame (calculs, dashboards, KPI : la « grande table »).
    ecriture=True : formules exécutées PENDANT l'écriture d'une ligne (calc_champ.py) — UNE table, jamais de groupe : le fichier est
    complété des lignes ajoutées ou modifiées dans la transaction en cours (parquet_store.donnees_ecriture), sans rien écrire."""
    from . import parquet_store
    if ecriture:
        return parquet_store.donnees_ecriture(table_dynamique)
    return parquet_store.donnees_analytiques(table_dynamique, groupe=groupe, identifiants=True)   # lecture : + matricule / clé d'accès (identifiants_analyse.py)


def resoudre_cible(ligne_calcul):
    """Retourne (cible, erreur). `cible` est un pandas.Series (formule
    cible_type='colonne') ou un pandas.DataFrame (cible_type='table'),
    restreint selon type_cible/plage_debut/plage_fin. `erreur` est une
    chaîne explicative si la cible n'a pas pu être résolue, sinon None."""
    if ligne_calcul.type_cible == "resultats":
        return _resoudre_cible_resultats(ligne_calcul)

    table = ligne_calcul.table_calcul.table_source
    df = _dataframe_table_source(table, groupe=True)

    if df.empty:
        return None, t("engine/calculs.py:g:196:22")

    if ligne_calcul.type_cible == "ligne":
        numero = ligne_calcul.plage_debut or 1
        index = numero - 1
        if index < 0 or index >= len(df):
            return None, tf("engine/calculs.py:g:202:26", v1=numero, v2=len(df))
        sous_df = df.iloc[[index]]
    elif ligne_calcul.type_cible == "plage":
        debut = max((ligne_calcul.plage_debut or 1) - 1, 0)
        fin = ligne_calcul.plage_fin or len(df)
        fin = min(fin, len(df))
        if debut >= fin:
            return None, t("engine/calculs.py:g:209:26")
        sous_df = df.iloc[debut:fin]
    else:  # "champ" : toute la colonne / toute la table
        sous_df = df

    if ligne_calcul.formule.cible_type == "table":
        # Sélection discontinue (Ctrl+clic) : chaque colonne peut avoir sa
        # propre plage de lignes, stockée dans cibles_json.
        cibles_json = ligne_calcul.cibles_json
        if cibles_json:
            import pandas as pd
            champs_map = {c.id: c for c in ligne_calcul.champs_cibles.all()}
            parts_par_zone = {}
            for item in cibles_json:
                champ = champs_map.get(item.get("champ_id"))
                if champ is None or champ.slug not in df.columns:
                    continue
                debut = item.get("debut")
                fin = item.get("fin")
                if debut is not None or fin is not None:
                    d = max((debut or 1) - 1, 0)
                    f = min(fin or len(df), len(df))
                    serie = df[champ.slug].iloc[d:f].reset_index(drop=True)
                else:
                    serie = df[champ.slug].reset_index(drop=True)
                zone = item.get("zone") or "a"
                parts_par_zone.setdefault(zone, []).append(serie.rename(champ.slug))
            if not parts_par_zone:
                return None, t("engine/calculs.py:g:237:30")
            # Zones indépendantes sélectionnées (voir index.html, sélecteur de
            # cible, jusqu'à 10 zones a..j) : autant de matrices colonnes×lignes
            # distinctes que de zones utilisées, passées à la formule sous forme
            # de dict {"zone_a":…, "zone_b":…, "zone_c":…, …} au lieu d'une seule
            # table fusionnée, pour des calculs comparant plusieurs ensembles
            # (ex: corrélation, écart, correspondance) plutôt qu'un seul bloc.
            zones_utilisees = sorted(parts_par_zone.keys())
            if len(zones_utilisees) > 1:
                return {
                    f"zone_{z}": pd.concat(parts_par_zone[z], axis=1)
                    for z in zones_utilisees
                }, None
            # Une seule zone : comportement historique, une table fusionnée.
            # Aligner toutes les séries sur la plus longue (NaN pour les manquantes)
            result_df = pd.concat(parts_par_zone[zones_utilisees[0]], axis=1)
            return result_df, None

        champs_cibles = list(ligne_calcul.champs_cibles.all())
        if champs_cibles:
            slugs = [c.slug for c in champs_cibles if c.slug in sous_df.columns]
            if not slugs:
                return None, t("engine/calculs.py:g:259:30")
            sous_df = sous_df[slugs]
        return sous_df, None

    # cible_type == "colonne" : nécessite champ_cible.
    if not ligne_calcul.champ_cible_id:
        return None, t("engine/calculs.py:g:265:22")
    slug = ligne_calcul.champ_cible.slug
    if slug not in sous_df.columns:
        return None, tf("engine/calculs.py:g:268:22", v1=slug)
    return sous_df[slug], None


def _sources_pretes(ligne_calcul):
    """(sources, erreur) : les lignes dont dépend `ligne_calcul` (lignes_sources), lues FRAÎCHES en base — avec un
    prefetch_related("lignes_sources"), les sources seraient des copies figées au chargement qui ne voient jamais le résultat
    qu'une source vient de recevoir dans la même passe (la chaîne restait « en attente » ou en retard d'un recalcul).
    Une source sans résultat bloque temporairement : recalculer_en_cascade calcule les sources avant leurs dépendantes."""
    from .models import LigneCalcul

    sources = list(LigneCalcul.objects.filter(lignes_dependantes=ligne_calcul.pk).order_by("ordre", "id"))
    manquants = [s.nom for s in sources if s.resultat_json is None]
    if manquants:
        return None, tf("engine/calculs.py:g:282:22", v1=', '.join(manquants))
    return sources, None


def _resoudre_cible_resultats(ligne_calcul):
    """Cible = série des résultats d'autres lignes de calcul (lignes_sources),
    pour enchaîner les calculs (ex: moyennes par matière → moyenne générale
    de ces moyennes). Une source pas encore calculée bloque temporairement :
    voir recalculer_en_cascade, qui calcule les sources avant leurs dépendantes."""
    import pandas as pd

    sources, erreur = _sources_pretes(ligne_calcul)
    if not erreur and not sources:
        erreur = t("engine/calculs.py:g:295:18")
    if erreur:
        return None, erreur
    serie = pd.Series({s.nom: s.resultat_json for s in sources})
    return serie, None


def _executer_formule(formule, cible, table):
    """Exécute une formule de bibliothèque sur `cible` → (résultat, erreur). Modules autorisés : champ dédié
    modules_autorises_formule de ConfigurationIA (même principe que modules_autorises_script/document), lu ici pour
    l'exécution réelle et par inference.generer_formule_calcul pour l'annonce faite au modèle — toujours synchronisés.
    Config vide = DEFAULT_MODULES_AUTORISES_FORMULE (inclut decimal/prophet en plus du socle statistique commun)."""
    from .models import ConfigurationIA
    cfg = ConfigurationIA.objects.filter(entreprise=table.entreprise).first()
    modules_autorises = parse_modules_autorises(
        cfg.modules_autorises_formule if cfg else "", DEFAULT_MODULES_AUTORISES_FORMULE
    )
    res = executer_formule_securisee(
        formule.code, cible, timeout_secondes=table.timeout_execution_secondes, modules_autorises=modules_autorises,
    )
    return (res["resultat"], None) if res.get("ok") else (None, res.get("erreur", td("engine/calculs.py:g:316:83")))


def sources_du_programme(ligne_calcul):
    """Calculs de la même table de calcul cités (par leur nom) dans le programme de `ligne_calcul` : ses dépendances."""
    from .formules_programme import noms_references
    return list(ligne_calcul.table_calcul.lignes.filter(nom__in=noms_references(ligne_calcul.programme)).exclude(pk=ligne_calcul.pk))


def _resoudre_programme(ligne_calcul):
    """(résultat, erreur) d'une ligne « programme » : les résultats des calculs cités forment le contexte, les colonnes de la
    table source sont lues en entier (agrégats) — voir formules_programme.executer_programme, contexte « table de calcul »."""
    from .formules_programme import executer_programme

    table = ligne_calcul.table_calcul.table_source
    sources, erreur = _sources_pretes(ligne_calcul)
    if erreur:
        return None, erreur
    df = _dataframe_table_source(table, groupe=True)
    if df.empty:
        return None, t("engine/calculs.py:g:335:22")
    return executer_programme(
        ligne_calcul.programme, {s.nom: s.resultat_json for s in sources}, table=table, df=df,
        executer_formule=lambda formule, cible: _executer_formule(formule, cible, table),
    )


def calculer_ligne(ligne_calcul, sauvegarder: bool = True):
    """Exécute la formule (ou le programme) de `ligne_calcul` sur sa cible résolue et retourne
    (resultat, erreur). Met à jour resultat_json/erreur/resultat_calcule_le
    si sauvegarder=True (comportement par défaut)."""
    if ligne_calcul.programme:
        resultat, erreur = _resoudre_programme(ligne_calcul)
    else:
        cible, erreur = resoudre_cible(ligne_calcul)
        resultat = None
        if not erreur:
            resultat, erreur = _executer_formule(ligne_calcul.formule, cible, ligne_calcul.table_calcul.table_source)
    if sauvegarder:
        ligne_calcul.resultat_json = None if erreur else resultat
        ligne_calcul.erreur = erreur or ""
        ligne_calcul.resultat_calcule_le = timezone.now()
        ligne_calcul.save(update_fields=["resultat_json", "erreur", "resultat_calcule_le"])
    return (None, erreur) if erreur else (resultat, None)


def _fermeture_dependantes(ids_depart):
    """Ids des lignes de calcul de départ + de TOUTES celles qui en dépendent, de proche en proche
    (lignes_sources), y compris dans d'autres tables de calcul ou d'autres tables sources."""
    from .models import LigneCalcul

    ids = set(ids_depart)
    frontiere = set(ids)
    while frontiere:
        nouveaux = set(
            LigneCalcul.objects.filter(lignes_sources__in=frontiere).values_list("id", flat=True)
        ) - ids
        ids |= nouveaux
        frontiere = nouveaux
    return ids


def _marquer_erreur_ligne(ligne, message):
    ligne.resultat_json = None
    ligne.erreur = message
    ligne.resultat_calcule_le = timezone.now()
    ligne.save(update_fields=["resultat_json", "erreur", "resultat_calcule_le"])


def recalculer_en_cascade(lignes_depart, ignorer_denaturee=True):
    """Recalcul EN CASCADE des lignes de calcul `lignes_depart` et de tout ce qui en dépend — même principe que
    la gestion de données (calc_champ._trier_cascade : une formule n'est calculée qu'APRÈS celles qui produisent
    ses opérandes, quel que soit l'ordre de création) :

      • l'ensemble recalculé = les lignes de départ + leurs dépendantes de proche en proche (lignes_sources),
        même dans une autre table de calcul ou sur une autre table source ;
      • ordre topologique (une ligne passe après ses sources) ; chaque ligne relit le résultat FRAIS de ses
        sources, donc une chaîne se propage en un seul passage ;
      • dépendance circulaire : les lignes du cycle ne sont jamais exécutées et reçoivent un message explicite
        (celles qui n'en font que dépendre, un message « en attente ») — pas de boucle, pas de résultat périmé ;
      • table source dénaturée : calcul ignoré par le recalcul AUTOMATIQUE (ignorer_denaturee=True, défaut) ; une action
        explicite de l'utilisateur (créer/modifier/supprimer un calcul) passe ignorer_denaturee=False et calcule quand même.

    Retourne la liste des lignes recalculées, dans l'ordre d'exécution (instances à jour)."""
    from .models import LigneCalcul

    ids = _fermeture_dependantes([l.id if hasattr(l, "id") else l for l in lignes_depart])
    if not ids:
        return []
    lignes = {
        l.id: l for l in LigneCalcul.objects.filter(id__in=ids)
        .select_related("formule", "champ_cible", "table_calcul__table_source")
        .prefetch_related("champs_cibles")
    }
    cle = lambda i: (lignes[i].table_calcul.ordre, lignes[i].table_calcul_id, lignes[i].ordre, i)
    aretes = {
        i: {s for s in l.lignes_sources.values_list("id", flat=True) if s in lignes}
        for i, l in lignes.items()
    }

    # Tri topologique (Kahn), stable : à égalité, ordre d'affichage des tables puis des lignes.
    restantes = {i: set(a) for i, a in aretes.items()}
    pretes = sorted((i for i, a in restantes.items() if not a), key=cle)
    ordre = []
    while pretes:
        i = pretes.pop(0)
        ordre.append(i)
        for j, a in restantes.items():
            if i in a:
                a.discard(i)
                if not a:
                    pretes.append(j)
        pretes.sort(key=cle)

    # Lignes jamais prêtes : cycle(s) + lignes qui en dépendent. On épluche l'aval (lignes dont aucune autre
    # ligne restante ne dépend) pour ne garder que les vrais membres d'un cycle.
    jamais = [i for i in lignes if i not in set(ordre)]
    cycle = set(jamais)
    change = True
    while change:
        change = False
        utilisees = set()
        for i in cycle:
            utilisees |= aretes[i] & cycle
        for i in list(cycle):
            if i not in utilisees:
                cycle.discard(i)
                change = True
    # Un message par cycle (composantes connexes des lignes cycliques), pas un seul pour tous.
    composante_de, restants = {}, set(cycle)
    while restants:
        depart = restants.pop()
        composante, pile = {depart}, [depart]
        while pile:
            n = pile.pop()
            for m in [m for m in restants if m in aretes[n] or n in aretes[m]]:
                restants.discard(m)
                composante.add(m)
                pile.append(m)
        noms = ", ".join(lignes[i].nom for i in sorted(composante, key=cle))
        for i in composante:
            composante_de[i] = noms
    for i in sorted(jamais, key=cle):
        if i in cycle:
            _marquer_erreur_ligne(lignes[i], tf("engine/calculs.py:g:459:46", v1=composante_de[i]))
        else:
            _marquer_erreur_ligne(lignes[i], t("engine/calculs.py:g:461:46"))

    for i in ordre:
        ligne = lignes[i]
        if ignorer_denaturee and ligne.table_calcul.table_source.denaturee:
            # Table dénaturée (voir TableDynamique.denaturee) : ignorée par les calculs automatiques.
            ligne.erreur = t("engine/calculs.py:g:469:28")
            ligne.save(update_fields=["erreur"])
        else:
            calculer_ligne(ligne)
    return [lignes[i] for i in ordre] + [lignes[i] for i in sorted(jamais, key=cle)]


def recalculer_table_calcul(table_calcul):
    """Recalcule toutes les lignes d'une TableCalcul, EN CASCADE (voir recalculer_en_cascade) : certaines lignes
    (type_cible='resultats') dépendent du résultat d'autres lignes (ex: moyenne générale = moyenne des moyennes
    par matière) — elles sont calculées après leurs sources, et toute ligne d'une AUTRE table de calcul qui en
    dépend est recalculée à sa suite. Retourne les lignes de CETTE table (ordre d'affichage)."""
    ids_table = list(table_calcul.lignes.values_list("id", flat=True))
    recalculees = {l.id: l for l in recalculer_en_cascade(ids_table)}
    return [recalculees[i] for i in
            table_calcul.lignes.order_by("ordre", "id").values_list("id", flat=True) if i in recalculees]


def recalculer_lignes_pour_table_source(table_dynamique):
    """Cascade complète après toute modification des données de `table_dynamique` (ajout, modification ou
    suppression d'une ligne, composition d'un groupe...) — dernier maillon de la chaîne déclenchée par les
    formules de champ (calc_champ.recalculer_formules_table) :

      1. toutes les lignes de calcul (LigneCalcul) branchées sur la table — ou sur une table de son groupe —
         sont recalculées d'UN SEUL passage dans l'ordre de leurs dépendances (recalculer_en_cascade), y compris
         celles d'autres tables qui s'appuient sur leurs résultats ;
      2. puis les KPI périmés de ces tables sont recalculés (kpi.rafraichir_kpis_perimes) : plus besoin
         d'ouvrir le tableau de bord pour qu'ils se mettent à jour, ni pour que leurs alertes se déclenchent.

    Best-effort : une erreur de formule est déjà gérée par calculer_ligne (stockée dans `erreur`, sans lever) ;
    on protège quand même chaque étape pour ne jamais faire échouer la sauvegarde de la ligne source.
    Retourne les lignes de calcul recalculées."""
    lignes_recalculees = []
    try:
        from .models import LigneCalcul
        # Une ligne modifiée dans une partie d'un groupe change les calculs de TOUTES les tables du groupe.
        tables = list(table_dynamique.groupe.tables.all()) if table_dynamique.groupe_id else [table_dynamique]
        depart = list(LigneCalcul.objects.filter(table_calcul__table_source__in=tables).values_list("id", flat=True))
        lignes_recalculees = recalculer_en_cascade(depart)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/calculs.py:g:509:47"), getattr(table_dynamique, "id", None))
        tables = [table_dynamique]
    try:
        from .kpi import rafraichir_kpis_perimes
        rafraichir_kpis_perimes(tables)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/calculs.py:g:516:47"), getattr(table_dynamique, "id", None))
    return lignes_recalculees
