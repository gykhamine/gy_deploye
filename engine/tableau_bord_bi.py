"""
Résolution des données et exécution des widgets pour les tableaux de bord BI
(voir models.TableauBordBI / GraphiqueBI / WidgetGraphiqueBI).

Même principe que calculs.py : on construit un DataFrame de la table source
(casté selon le schéma EAV), on le passe tel quel au bac à sable dédié
(executor.executer_graphique_bi_securise) avec la liste des colonnes
choisies pour CE widget — c'est la fonction dessiner(dataframe, colonnes) du
graphique qui se restreint elle-même aux colonnes qui l'intéressent.

Contrairement aux calculs (résultat JSON libre), le résultat d'un widget BI
est TOUJOURS une image PNG encodée en base64 : jamais de fichier sur disque,
uniquement du texte en base (voir WidgetGraphiqueBI.resultat_image_base64).
"""
from engine.i18n import td
from engine.i18n import t, tf
from django.utils import timezone

from . import identifiants_analyse as _ident
from .executor import _appliquer_schema, executer_graphique_bi_securise
from .metadata import empreinte_donnees, schema_pour_executor


def _dataframe_table_source(table_dynamique, filtre=None, colonnes=None):
    """DataFrame de la table — ou de tout son groupe (calculs._dataframe_table_source, une seule
    implémentation). `filtre` optionnel ({"colonne", "valeur"}) restreint les lignes pour le filtre croisé.
    `colonnes` (slugs) : OPTIMISATION — ne lit dans le Parquet que ces colonnes (+ celle du filtre croisé) au lieu de toute
    la table ; None = toutes les colonnes (graphique personnalisé ou mesures, qui peuvent lire n'importe laquelle)."""
    from .calculs import _dataframe_table_source as _source

    if colonnes is not None:
        from . import parquet_store
        utiles = set(colonnes) | ({filtre["colonne"]} if filtre and filtre.get("colonne") else set())
        df = parquet_store.donnees_analytiques(table_dynamique, groupe=True, identifiants=True, colonnes=utiles)
    else:
        df = _source(table_dynamique, groupe=True)   # lecture Parquet (parquet_store.donnees_analytiques)
    # Jamais de filtre croisé sur la clé d'accès : le tableau peut être vu par un client, qui pourrait sinon la sonder valeur par valeur.
    if filtre and filtre.get("colonne") in df.columns and filtre.get("colonne") != _ident.CLE:
        df = df[df[filtre["colonne"]].astype(str) == str(filtre.get("valeur"))]
    return df


def _colonnes_widget(widget):
    """Retourne la liste ordonnée des slugs de colonnes sélectionnées pour ce
    widget. L'ordre déclaré dans ordre_champs prime (le M2M ne garantit pas
    l'ordre d'insertion) ; à défaut, ordre naturel de champs_cibles.

    Les slugs de mesures (colonnes virtuelles, voir MesureWidget) sont encodés
    dans ordre_champs comme chaînes négatives «mesure:<slug>» — ils sont
    inclus à leur position sans passer par champs_cibles (qui ne stocke que
    des ChampDynamique réels)."""
    from .mesures import _slug_mesure
    mesures_par_slug = {_slug_mesure(m): m for m in widget.mesures.all()}

    champs = {c.id: c for c in widget.champs_cibles.all()}
    if widget.ordre_champs:
        slugs = []
        for entry in widget.ordre_champs:
            if isinstance(entry, str) and entry.startswith("mesure:"):
                slug = entry[len("mesure:"):]
                if slug in mesures_par_slug:
                    slugs.append(slug)
            elif entry in champs:
                slugs.append(champs[entry].slug)
            elif isinstance(entry, int) and entry in _ident.SLUG_PAR_ID:   # matricule / clé d'accès (ids négatifs, voir identifiants_analyse)
                slugs.append(_ident.SLUG_PAR_ID[entry])
        # Colonnes réelles absentes de ordre_champs (sécurité désynchronisation)
        vus_ids = {entry for entry in widget.ordre_champs if isinstance(entry, int) and entry in champs}
        slugs += [c.slug for cid, c in champs.items() if cid not in vus_ids]
        return slugs
    return [c.slug for c in champs.values()]


def calculer_widget(widget, sauvegarder: bool = True, filtre=None):
    """Exécute le graphique de `widget`, retourne (image_base64, erreur).
    `filtre` + sauvegarder=False : rendu à la volée pour le filtre croisé,
    sans persister ni écraser le cache non filtré.

    Une zone de texte (type_widget='texte') n'a rien à calculer : elle ne
    dépend d'aucune donnée de la table source, on se contente de marquer le
    widget prêt (voir models.WidgetGraphiqueBI.widget_a_jour)."""
    if widget.type_widget in ("texte", "kpi"):  # kpi : valeur calculée par kpi.calculer_kpi, rien à rendre ici
        if sauvegarder:
            widget.statut = "pret"
            widget.erreur = ""
            widget.rendu_le = timezone.now()
            widget.save(update_fields=["statut", "erreur", "rendu_le"])
        return None, None

    if widget.tableau_bord.table_source.denaturee:
        # Table dénaturée (voir TableDynamique.denaturee) : ignorée par les
        # calculs automatiques, y compris les widgets de dashboard BI.
        erreur = t("engine/tableau_bord_bi.py:g:80:18")
        if sauvegarder:
            widget.statut = "echec"
            widget.erreur = erreur
            widget.rendu_le = timezone.now()
            widget.save(update_fields=["statut", "erreur", "rendu_le"])
        return None, erreur

    if widget.type_widget == "carte":
        # Carte = simple situation de points : chaque colonne de type « geolocalisation » est une suite de points.
        # Mêmes lignes que les autres widgets (table ou groupe entier, enregistrements_groupe) et même filtre croisé.
        from .visualization import _generer_carte_geo_html, _generer_carte_geo_image, _parse_geo
        table_carte = widget.tableau_bord.table_source
        # Rendu filtre (filtre croise, rejoue a chaque tick d'auto-refresh) : meme donnees + meme filtre = meme HTML,
        # on le garde quelques minutes au lieu de relire toutes les lignes et de regenerer la carte.
        cle_cache_carte = None
        if filtre and not sauvegarder:
            try:
                import hashlib, json
                from django.core.cache import cache
                brut = json.dumps([widget.id, empreinte_donnees(table_carte), filtre.get("colonne"), str(filtre.get("valeur")),
                                   widget.options_json, sorted(c.id for c in widget.champs_cibles.all()), widget.ordre_champs], sort_keys=True, default=str)
                cle_cache_carte = "bi_carte:" + hashlib.sha256(brut.encode("utf-8")).hexdigest()
                html_en_cache = cache.get(cle_cache_carte)
                if html_en_cache:
                    return html_en_cache, None
            except Exception:
                cle_cache_carte = None  # cache indisponible : on calcule normalement
        colonnes_carte = list(widget.champs_cibles.filter(type_champ="geolocalisation")
                              or table_carte.champs.filter(type_champ="geolocalisation"))
        slugs_geo = {c.slug for c in colonnes_carte}
        points, erreur_carte = [], ""
        from . import geo_donnees as _gd
        col_id = _gd.colonne_id_client(table_carte)   # table client : une seule position par client (la plus récente), pas une par ligne
        derniers = {}
        if not colonnes_carte:
            erreur_carte = t("engine/tableau_bord_bi.py:g:116:28")
        else:
            # Lecture via Enregistrement.to_dict() (SQL : ValeurChamp / NoSQL : valeurs_json), comme calculs._dataframe_table_source.
            # Lecture en bloc : 2 requetes au total (lignes + valeurs) au lieu d'1 requete par ligne.
            from . import parquet_store
            for ligne in parquet_store.lignes_analytiques(table_carte):   # fichier Parquet de la table (ou du groupe)
                if filtre and filtre.get("colonne") in ligne and str(ligne.get(filtre["colonne"])) != str(filtre.get("valeur")):
                    continue
                # Repère de la ligne : première valeur lisible hors colonnes de position (jamais le code d'accès client).
                repere = next((str(v) for k, v in ligne.items()
                               if k not in slugs_geo and k != "code_acces_client" and v not in (None, "")), "")
                for champ in colonnes_carte:
                    g = _parse_geo(ligne.get(champ.slug))
                    if g:
                        libelle = g[2] or repere
                        if len(colonnes_carte) > 1:  # plusieurs colonnes de position : on dit laquelle
                            libelle = f"{champ.nom} — {libelle}" if libelle else champ.nom
                        client = _gd.client_de(ligne, col_id.slug if col_id else None)
                        if client:
                            derniers[(client, champ.slug)] = {"lat": g[0], "lon": g[1], "libelle": libelle}   # lignes en ordre chronologique : la dernière l'emporte
                        else:
                            points.append({"lat": g[0], "lon": g[1], "libelle": libelle})
            points.extend(derniers.values())
            if not points:
                erreur_carte = t("engine/tableau_bord_bi.py:g:140:32") + (t("engine/tableau_bord_bi.py:g:140:96") if filtre else ".")
        if erreur_carte:
            if sauvegarder:
                widget.statut = "echec"
                widget.erreur = erreur_carte
                widget.empreinte_donnees = empreinte_donnees(table_carte)  # évite le recalcul à chaque tick tant que les données ne changent pas
                widget.rendu_le = timezone.now()
                widget.save(update_fields=["statut", "erreur", "empreinte_donnees", "rendu_le"])
            return None, erreur_carte
        spec = widget.options_json or {}
        try:
            html_carte = _generer_carte_geo_html(spec, points, table_carte.id)
        except Exception:
            html_carte = _generer_carte_geo_image(spec, points)  # fallback matplotlib
        if cle_cache_carte:
            try:
                cache.set(cle_cache_carte, html_carte, 300)
            except Exception:
                pass
        if sauvegarder:
            widget.resultat_html = html_carte
            widget.empreinte_donnees = empreinte_donnees(table_carte)
            widget.statut = "pret"
            widget.erreur = ""
            widget.rendu_le = timezone.now()
            widget.save(update_fields=["resultat_html", "empreinte_donnees", "statut", "erreur", "rendu_le"])
        return html_carte, None

    table = widget.tableau_bord.table_source
    colonnes = _colonnes_widget(widget)

    n_min = widget.graphique.nombre_colonnes_min
    n_max = widget.graphique.nombre_colonnes_max
    if len(colonnes) < n_min or (n_max and len(colonnes) > n_max):
        erreur = (
            tf("engine/tableau_bord_bi.py:g:175:13", v1=n_min, v2=n_max or '∞', v3=len(colonnes))
        )
        if sauvegarder:
            widget.statut = "echec"
            widget.erreur = erreur
            widget.save(update_fields=["statut", "erreur"])
        return None, erreur

    # OPTIMISATION : un graphique prédéfini ne touche qu'aux colonnes passées dans `colonnes` (voir graphiques_bi.py) et, sans
    # mesure, rien d'autre n'est lu : on ne charge, ne convertit et n'envoie au bac à sable (pickle) que celles-là — sur 32 000
    # lignes × N colonnes, c'était l'essentiel du temps avant même de dessiner. Graphique personnalisé / mesures : tout est lu.
    colonnes_utiles = None
    if widget.graphique.entreprise_id is None and widget.graphique.predefini and not widget.mesures.exists():
        colonnes_utiles = list(colonnes)
    df = _dataframe_table_source(table, filtre=filtre, colonnes=colonnes_utiles)
    if df.empty:
        erreur = t("engine/tableau_bord_bi.py:g:186:18") if filtre else t("engine/tableau_bord_bi.py:g:186:92")
        if sauvegarder:
            widget.statut = "echec"
            widget.erreur = erreur
            widget.save(update_fields=["statut", "erreur"])
        return None, erreur

    # ── Mesures widget (colonnes virtuelles calculées, style Power BI) ──
    # Exécutées dans l'ordre avant dessiner() ; les slugs de mesure sont
    # injectés comme colonnes constantes dans le DataFrame, disponibles dans
    # `colonnes` si l'utilisateur les a sélectionnées (voir MesureWidget).
    from .mesures import calculer_mesures
    df, _erreurs_mesures = calculer_mesures(widget, df)
    # Erreurs non bloquantes : dessiner() reçoit le DataFrame avec les
    # colonnes calculées disponibles et sans celles en échec.

    donnees_dict = {col: df[col].tolist() for col in df.columns if col in df.columns}
    schema = schema_pour_executor(table)

    from .executor import parse_modules_autorises, DEFAULT_MODULES_AUTORISES_GRAPHIQUE_BI
    from .models import ConfigurationIA
    cfg = ConfigurationIA.objects.filter(entreprise=table.entreprise).first()
    modules_autorises = parse_modules_autorises(
        cfg.modules_autorises_graphique_bi if cfg else "",
        DEFAULT_MODULES_AUTORISES_GRAPHIQUE_BI,
    )

    res = executer_graphique_bi_securise(
        widget.graphique.code, donnees_dict, colonnes,
        schema_eav=schema,
        timeout_secondes=table.timeout_execution_secondes,
        modules_autorises=modules_autorises,
    )

    empreinte_actuelle = empreinte_donnees(table)

    if sauvegarder:
        if res.get("ok"):
            widget.resultat_image_base64 = res["resultat"]
            widget.erreur = ""
            widget.statut = "pret"
            widget.empreinte_donnees = empreinte_actuelle
        else:
            widget.erreur = res.get("erreur", td("engine/tableau_bord_bi.py:g:229:47"))
            widget.statut = "echec"
        widget.rendu_le = timezone.now()
        widget.save(update_fields=[
            "resultat_image_base64", "erreur", "statut", "empreinte_donnees", "rendu_le",
        ])

    if res.get("ok"):
        return res["resultat"], None
    return None, res.get("erreur", td("engine/tableau_bord_bi.py:g:238:36"))


def recalculer_tableau_bord(tableau_bord):
    """Recalcule tous les widgets d'un TableauBordBI (dans l'ordre)."""
    widgets = list(
        tableau_bord.widgets.select_related("graphique").prefetch_related("champs_cibles").order_by("ordre_z", "id")
    )
    for widget in widgets:
        calculer_widget(widget)
    return widgets


def widgets_a_recalculer(tableau_bord):
    """Retourne les widgets dont le rendu en cache n'est plus à jour par
    rapport à l'état actuel des données de la table source (voir
    WidgetGraphiqueBI.widget_a_jour) — sert au rafraîchissement live du
    canevas : seuls ces widgets sont relancés, pas tout le tableau de bord."""
    empreinte_actuelle = empreinte_donnees(tableau_bord.table_source)
    widgets = list(
        tableau_bord.widgets.exclude(type_widget="carte").select_related("graphique").prefetch_related("champs_cibles").order_by("ordre_z", "id")   # la carte a quitté la BI
    )
    return [w for w in widgets if not w.widget_a_jour(empreinte_actuelle)]

