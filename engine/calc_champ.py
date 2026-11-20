"""
FORMULES DE CHAMP — calcul "à la Excel" directement dans la vue Données.

Contrairement à `calculs.py` (tables de calcul indépendantes, affichées à
part), ce module calcule la valeur d'UN champ, et le résultat est écrit
directement dans la case de la table source — comme une formule Excel.

Deux usages, une seule fonction de calcul (`calculer_valeur_champ`) :

  • Persistant (colonne entière) : un `FormuleChamp` est enregistré sur la
    table — `appliquer_formules_champs` recalcule alors automatiquement le
    champ concerné à chaque ajout/modification de ligne (voir views.py).

  • Ponctuel (une ligne) : aucun enregistrement — l'appelant fournit
    directement l'opérateur/les opérandes pour calculer et écrire la valeur
    d'un seul champ, une seule fois (voir api_calculer_champ_ligne).

Deux portées possibles, comme sur Excel — la cible n'est PAS limitée à la
ligne courante :

  • Opérateurs prédéfinis (somme, moyenne, min, max, ...) : par défaut
    restreints à la MÊME ligne (comme `=SOMME(B2:D2)`), car ce sont des
    opérateurs arithmétiques simples entre colonnes d'un même enregistrement.
    Exception — mode « zones » : si `parametres["zones"]` contient au moins
    une plage de lignes (sélecteur de zones du bouton ∑), l'opérateur agrège
    TOUTES les cellules numériques des zones (colonnes entières et plages,
    dans l'ordre de sélection, comme `=SOMME(B2:B9; D1:D4)`) et le résultat
    scalaire est écrit dans la cible (même valeur sur toute la colonne =
    référence absolue Excel). Voir `_zones_agregat`.

  • Formule personnalisée (`operateur="personnalisee"`) : la portée suit
    `FormuleCalcul.cible_type` de la formule choisie — EXACTEMENT le même
    mécanisme que les tables de calcul indépendantes (`calculs.py`) :
      - "colonne" : la formule reçoit un pandas.Series de la colonne
        opérande choisie, sur TOUTES les lignes de la table (permet un
        rang, un cumul, un écart à la moyenne, une comparaison à la ligne
        précédente, etc.) ;
      - "table"   : la formule reçoit un pandas.DataFrame de plusieurs
        colonnes, sur TOUTES les lignes de la table (permet de croiser
        plusieurs colonnes sur toute la table, comme une vraie formule
        matricielle Excel).
    Dans les deux cas, `parametres["_index_ligne"]` (0-based, ordre
    chronologique — même convention que `calculs._dataframe_table_source`)
    indique à la formule la position de la ligne en cours de calcul dans la
    cible reçue, pour qu'elle puisse se situer par rapport aux autres
    lignes. La seule limite désormais est ce que la formule Python choisit
    de faire — pas une restriction technique du champ de calcul.
"""
from engine.i18n import t, tf
from .models import ValeurChamp


def _valeur_pour_ligne(resultat, index_ligne):
    """Un résultat de formule 'colonne'/'table' peut revenir sous forme
    scalaire (même valeur écrite dans toutes les lignes — l'équivalent
    d'une référence absolue Excel `$B$2`, ex: une moyenne globale) ou sous
    forme de liste alignée sur les lignes de la cible (une valeur par
    ligne, ex: un rang ou un cumul). Dans ce second cas, on extrait la
    valeur qui correspond à la ligne en cours de calcul."""
    if isinstance(resultat, list):
        if index_ligne is None or not (0 <= index_ligne < len(resultat)):
            return resultat
        valeur = resultat[index_ligne]
        if isinstance(valeur, dict) and len(valeur) == 1:
            return next(iter(valeur.values()))
        return valeur
    return resultat


def _contexte_formule_personnalisee(table, enregistrement, cible_type, operandes_slugs,
                                     df_table=None, ids_ordonnes=None):
    """Construit (cible, index_ligne, erreur) pour une formule personnalisée
    dont la portée est 'colonne' (pandas.Series, toutes les lignes) ou
    'table' (pandas.DataFrame, toutes les lignes) — réutilise le DataFrame
    typé selon le schéma EAV (`calculs._dataframe_table_source`), le même
    que celui des tables de calcul indépendantes, pour offrir exactement la
    même flexibilité "à la Excel" directement dans la vue Données."""
    from .calculs import _dataframe_table_source
    if df_table is None:
        df_table = _dataframe_table_source(table, ecriture=True)
    if ids_ordonnes is None:
        ids_ordonnes = list(table.enregistrements.order_by("cree_le").values_list("id", flat=True))

    if df_table.empty:
        return None, None, t("engine/calc_champ.py:g:84:28")

    index_ligne = ids_ordonnes.index(enregistrement.id) if enregistrement.id in ids_ordonnes else None

    if cible_type == "colonne":
        if not operandes_slugs:
            return None, None, t("engine/calc_champ.py:g:90:32")
        slug = operandes_slugs[0]
        if slug not in df_table.columns:
            return None, None, tf("engine/calc_champ.py:g:93:32", v1=slug)
        return df_table[slug].reset_index(drop=True), index_ligne, None

    # cible_type == "table" : toutes les colonnes, ou seulement celles
    # choisies comme opérandes si l'utilisateur en a sélectionné.
    colonnes = [s for s in operandes_slugs if s in df_table.columns] if operandes_slugs else list(df_table.columns)
    if operandes_slugs and not colonnes:
        return None, None, t("engine/calc_champ.py:g:100:28")
    return df_table[colonnes].reset_index(drop=True), index_ligne, None


def _vers_nombre(valeur):
    """Convertit une valeur de champ (déjà typée par valeur_typee(), donc
    souvent déjà int/float) en float. Retourne None si non convertible."""
    if valeur is None or valeur == "":
        return None
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)
    try:
        return float(str(valeur).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _texte_valeur(valeur):
    """Texte stocké en base : JSON pour une liste/un dict (vecteur, matrice), str() sinon."""
    import json
    return json.dumps(valeur) if isinstance(valeur, (list, dict)) else str(valeur)


def _ecrire_valeur(enregistrement, champ, valeur_str):
    """Écrit la valeur calculée d'un champ — table SQL (ValeurChamp) ou
    NoSQL (bloc JSON de la ligne), au même endroit pour tous les appelants."""
    if enregistrement.table.est_nosql:
        from . import nosql_store
        nosql_store.ecrire_valeur_calculee(enregistrement, champ, valeur_str)
    else:
        ValeurChamp.objects.update_or_create(
            enregistrement=enregistrement, champ=champ, defaults={"valeur": valeur_str},
        )


def _zones_agregat(parametres):
    """Zones de sélection (`parametres["zones"]`, liste de
    {"slug", "debut", "fin", "zone"} — debut/fin 1-based inclusifs, None =
    colonne entière) si au moins une est une plage de lignes : l'opérateur
    prédéfini agrège alors les cellules des zones au lieu de calculer ligne
    par ligne. Retourne None si le calcul reste « ligne par ligne »."""
    zones = (parametres or {}).get("zones") if isinstance(parametres, dict) else None
    if not isinstance(zones, list):
        return None
    zones = [z for z in zones if isinstance(z, dict) and z.get("slug")]
    if not any(z.get("debut") is not None for z in zones):
        return None
    return zones


def _depend_des_autres_lignes(operateur, parametres):
    """True si le résultat peut changer quand UNE AUTRE ligne change (formule
    personnalisée colonne/table, ou opérateur prédéfini en mode zones)."""
    if operateur in ("personnalisee", "matrice"):
        return True
    if operateur == "programme":  # seulement si une étape lit d'autres lignes (zones / formule personnalisée)
        import json
        brut = json.dumps(parametres or {})
        return '"zones"' in brut or '"personnalisee"' in brut
    return operateur != "concatenation" and _zones_agregat(parametres) is not None


def _nombres_des_zones(table, zones, df_table=None):
    """Nombres des cellules des zones, dans l'ordre de sélection (vides et
    non numériques ignorés, comme SOMME sur Excel). Retourne (nombres, erreur)."""
    from .calculs import _dataframe_table_source
    if df_table is None:
        df_table = _dataframe_table_source(table, ecriture=True)
    if df_table.empty:
        return None, t("engine/calc_champ.py:g:171:22")
    nombres = []
    for z in zones:
        slug = z.get("slug")
        if slug not in df_table.columns:
            return None, tf("engine/calc_champ.py:g:176:26", v1=slug)
        valeurs = df_table[slug].reset_index(drop=True).tolist()
        if z.get("debut") is not None:
            try:
                debut = max(int(z["debut"]) - 1, 0)
                fin = int(z["fin"]) if z.get("fin") is not None else debut + 1
            except (TypeError, ValueError):
                return None, t("engine/calc_champ.py:g:183:30")
            valeurs = valeurs[debut:max(fin, debut)]
        for v in valeurs:
            n = _vers_nombre(v)
            if n is not None and n == n:  # n == n écarte NaN
                nombres.append(n)
    if not nombres:
        return None, t("engine/calc_champ.py:g:190:22")
    return nombres, None


def verifier_types_formule(table, operateur, operandes_slugs, formule=None, parametres=None, cibles=None):
    """Garde-fou : une formule ne s'applique JAMAIS à une donnée d'une autre classe que celle stockée en base —
    colonnes opérandes, cellules/plages des zones et colonnes lues par les étapes d'un programme (types mélangés =
    refus total). Retourne un message d'erreur, ou None si tout est compatible."""
    from .formules_science import TYPES_NUMERIQUES
    from .models import FormuleCalcul
    types = dict(table.champs.values_list("slug", "type_champ"))

    def cible_ok():  # classe du champ cible : « matrice » <-> vecteur/matrice, forme attendue respectée
        zs = [z for z in (parametres or {}).get("zones") or [] if isinstance(z, dict)] if isinstance(parametres, dict) else []
        for c in cibles or []:
            tenseur = c.type_champ in ("vecteur", "matrice")
            if tenseur != (operateur == "matrice") and operateur in ("matrice", "somme", "difference", "produit", "quotient", "moyenne", "pourcentage", "min", "max", "concatenation"):
                return tf("engine/calc_champ.py:g:207:24", v1=c.nom, v2=c.type_champ)
            forme = c.forme_tenseur if tenseur and operateur == "matrice" else None
            if forme and zs and all(z.get("debut") is not None and z.get("fin") is not None for z in zs):
                h = int(zs[0]["fin"]) - int(zs[0]["debut"]) + 1
                attendu = (h,) if len(zs) == 1 else (h, len(zs))
                if tuple(forme) != attendu:
                    return tf("engine/calc_champ.py:g:213:28", v1=c.nom, v2=tuple(forme), v3=attendu)

    if (r := cible_ok()):
        return r

    def _zones(params):
        zones = params.get("zones") if isinstance(params, dict) else None
        return [z.get("slug") for z in zones or [] if isinstance(z, dict)]

    def verifier(op, f, slugs):
        for s in slugs:
            tc = types.get(s) if isinstance(s, str) else None
            if tc in (None, "formule") or op == "concatenation":  # variable/littéral, colonne calculée, texte libre
                continue
            if not (f.compatible_avec_type_champ(tc) if op == "personnalisee" and f else tc in TYPES_NUMERIQUES.split(",")):
                return tf("engine/calc_champ.py:g:228:24", v1=s, v2=tc)

    def etapes(liste):
        for e in liste if isinstance(liste, list) else []:
            if not isinstance(e, dict):
                continue
            if e.get("t") == "calc":
                f = FormuleCalcul.objects.filter(id=e.get("formule_id")).first() if e.get("op") == "personnalisee" else None
                r = verifier(e.get("op") or "somme", f, list(e.get("ops") or []) + _zones(e.get("params")))
            else:
                from .formules_programme import sous_listes
                r = next(filter(None, map(etapes, sous_listes(e))), None)
            if r:
                return r

    if operateur == "programme":
        return etapes((parametres or {}).get("programme") if isinstance(parametres, dict) else None)
    return verifier(operateur, formule, list(operandes_slugs or []) + _zones(parametres))


def _matrice_des_zones(table, parametres, operandes_slugs, df_table=None, memo=None):
    """Opérateur « matrice » : rend la sélection AVEC sa forme (liste de lignes ; une seule colonne = vecteur) au lieu
    d'un agrégat. Une seule zone rectangulaire (colonnes de même hauteur, cellules numériques). (valeur, erreur)."""
    zones = (parametres or {}).get("zones") if isinstance(parametres, dict) else None
    zones = [z for z in zones or [] if isinstance(z, dict) and z.get("slug")] or [{"slug": x} for x in operandes_slugs or []]
    cle = ("matrice", repr(zones))
    if memo is not None and cle in memo:
        return memo[cle]
    if not zones or (table is None and df_table is None):
        return None, t("engine/calc_champ.py:g:257:22")
    if len({z.get("zone", "a") for z in zones}) > 1:
        return None, t("engine/calc_champ.py:g:259:22")
    if df_table is None:
        from .calculs import _dataframe_table_source
        df_table = _dataframe_table_source(table, ecriture=True)
    df = df_table
    if df.empty:
        return None, t("engine/calc_champ.py:g:265:22")
    colonnes = []
    for z in zones:
        if z["slug"] not in df.columns:
            return None, tf("engine/calc_champ.py:g:269:26", v1=z['slug'])
        v = df[z["slug"]].reset_index(drop=True).tolist()
        try:
            if z.get("debut") is not None:
                debut = max(int(z["debut"]) - 1, 0)
                v = v[debut:max(int(z["fin"]) if z.get("fin") is not None else debut + 1, debut)]
        except (TypeError, ValueError):
            return None, t("engine/calc_champ.py:g:276:26")
        n = [_vers_nombre(x) for x in v]
        if not n or any(x is None or x != x for x in n):
            return None, tf("engine/calc_champ.py:g:279:26", v1=z['slug'])
        colonnes.append([int(x) if x.is_integer() else round(x, 6) for x in n])
    if len({len(c) for c in colonnes}) > 1:
        return None, t("engine/calc_champ.py:g:282:22")
    resultat = (colonnes[0] if len(colonnes) == 1 else [list(r) for r in zip(*colonnes)], None)
    if memo is not None:
        memo[cle] = resultat
    return resultat


def _appliquer_operateur(operateur, nombres):
    """Opérateur prédéfini sur une liste de nombres. Retourne (valeur, erreur)."""
    try:
        if operateur == "somme":
            valeur = sum(nombres)
        elif operateur == "produit":
            valeur = 1.0
            for n in nombres:
                valeur *= n
        elif operateur == "difference":
            valeur = nombres[0] - sum(nombres[1:])
        elif operateur == "quotient":
            valeur = nombres[0]
            for n in nombres[1:]:
                if n == 0:
                    return None, t("engine/calc_champ.py:g:304:34")
                valeur /= n
        elif operateur == "moyenne":
            valeur = sum(nombres) / len(nombres)
        elif operateur == "pourcentage":
            if len(nombres) < 2:
                return None, t("engine/calc_champ.py:g:310:30")
            if nombres[1] == 0:
                return None, t("engine/calc_champ.py:g:312:30")
            valeur = (nombres[0] / nombres[1]) * 100
        elif operateur == "min":
            valeur = min(nombres)
        elif operateur == "max":
            valeur = max(nombres)
        else:
            return None, tf("engine/calc_champ.py:g:319:26", v1=operateur)
    except Exception as exc:
        return None, str(exc)

    # Nombre entier propre si le résultat n'a pas de partie décimale
    # significative (évite d'afficher "10.0" pour une somme d'entiers).
    if isinstance(valeur, float) and valeur.is_integer():
        valeur = int(valeur)
    else:
        valeur = round(valeur, 6)
    return valeur, None


def calculer_valeur_champ(ligne_dict, operateur, operandes_slugs, formule=None, parametres=None,
                           table=None, enregistrement=None, df_table=None, ids_ordonnes=None, memo=None):
    """Calcule la valeur d'un champ. Pour les opérateurs prédéfinis, à
    partir des colonnes `operandes_slugs` DE LA MÊME LIGNE (`ligne_dict`,
    sortie de Enregistrement.to_dict()). Pour une formule personnalisée,
    portée 'colonne'/'table' sur toute la table (voir `table`/`enregistrement`
    ci-dessous). Retourne (valeur, erreur) — l'un des deux est toujours None.

    `table`/`enregistrement` : requis pour `operateur="personnalisee"` (la
    formule a besoin d'accéder aux autres lignes de la table). `df_table`/
    `ids_ordonnes` : optionnels, pour éviter de reconstruire le DataFrame de
    la table à chaque appel quand la fonction est appelée en boucle sur
    plusieurs lignes (voir `recalculer_colonne_entiere`)."""
    valeurs_brutes = [ligne_dict.get(slug) for slug in operandes_slugs]

    if operateur == "programme":
        from .formules_programme import executer_programme
        return executer_programme((parametres or {}).get("programme"), ligne_dict, table=table, enregistrement=enregistrement,
                                  df_table=df_table, ids_ordonnes=ids_ordonnes, memo=memo)

    if operateur == "personnalisee":
        if not formule:
            return None, t("engine/calc_champ.py:g:354:26")
        try:
            import pandas as pd  # noqa: F401 — verifie juste que pandas est installe
        except Exception:
            return None, t("engine/calc_champ.py:g:358:26")
        from .executor import executer_formule_securisee

        if table is None or enregistrement is None:
            return None, t("engine/calc_champ.py:g:362:26")

        cible, index_ligne, erreur = _contexte_formule_personnalisee(
            table, enregistrement, formule.cible_type, operandes_slugs,
            df_table=df_table, ids_ordonnes=ids_ordonnes,
        )
        if erreur:
            return None, erreur

        params = dict(parametres or {})
        # Position (0-based, ordre chronologique) de la ligne en cours de
        # calcul dans `cible` — permet à une formule qui reçoit toute la
        # colonne/table de se situer par rapport aux autres lignes (ligne
        # précédente, rang, écart à la moyenne, cumul...) via
        # `cible.iloc[parametres["_index_ligne"]]`.
        params["_index_ligne"] = index_ligne

        resultat = executer_formule_securisee(formule.code, cible, parametres=params)
        if not resultat.get("ok"):
            return None, resultat.get("erreur") or t("engine/calc_champ.py:g:381:52")
        valeur = _valeur_pour_ligne(resultat.get("resultat"), index_ligne)
        return valeur, None

    if operateur == "concatenation":
        sep = (parametres or {}).get("separateur", ", ")
        texte = sep.join(str(v) for v in valeurs_brutes if v not in (None, ""))
        return texte, None

    if operateur == "matrice":
        return _matrice_des_zones(table, parametres, operandes_slugs, df_table=df_table, memo=memo)

    zones = _zones_agregat(parametres)
    if zones is not None:
        if table is None:
            return None, t("engine/calc_champ.py:g:396:26")
        # Résultat indépendant de la ligne : calculé une seule fois par boucle
        # de recalcul (`memo` partagé), pas une fois par ligne.
        cle_memo = (operateur, repr(zones))
        if memo is not None and cle_memo in memo:
            return memo[cle_memo]
        nombres, erreur = _nombres_des_zones(table, zones, df_table=df_table)
        resultat = (None, erreur) if erreur else _appliquer_operateur(operateur, nombres)
        if memo is not None:
            memo[cle_memo] = resultat
        return resultat

    if not operandes_slugs:
        return None, t("engine/calc_champ.py:g:409:22")

    nombres = [_vers_nombre(v) for v in valeurs_brutes]
    if any(n is None for n in nombres):
        return None, t("engine/calc_champ.py:g:413:22")

    return _appliquer_operateur(operateur, nombres)


def _trier_cascade(formules):
    """Cascade : une formule de colonne est calculée APRÈS celles qui produisent ses opérandes, quel que soit
    l'ordre de création (un cycle retombe sur l'ordre de création, sans boucle infinie)."""
    par_slug = {f.champ_cible.slug: f for f in formules}
    ordre, vues = [], set()

    def visiter(f):
        if f.id in vues:
            return
        vues.add(f.id)  # marqué avant la descente : coupe les cycles
        for s in f.operandes_slugs or []:
            if s in par_slug:
                visiter(par_slug[s])
        ordre.append(f)

    for f in formules:
        visiter(f)
    return ordre


def appliquer_formules_champs(table, enregistrement):
    """Recalcule et enregistre tous les FormuleChamp actifs de `table` pour
    cette ligne, à partir des valeurs actuelles des autres colonnes. Traite
    les formules dans l'ordre pour permettre un simple chaînage (une formule
    peut utiliser comme opérande un champ calculé par une formule
    précédente, à condition qu'elle soit listée avant dans `table`)."""
    formules = _trier_cascade(list(table.formules_champs.select_related("champ_cible", "formule").order_by("id")))
    if not formules:
        return
    ligne_dict = enregistrement.to_dict()
    df_table, ids_ordonnes = None, None
    if any(_depend_des_autres_lignes(fc.operateur, fc.parametres) for fc in formules):
        from .calculs import _dataframe_table_source
        df_table = _dataframe_table_source(table, ecriture=True)
        ids_ordonnes = list(table.enregistrements.order_by("cree_le").values_list("id", flat=True))
    for fc in formules:
        valeur, erreur = calculer_valeur_champ(
            ligne_dict, fc.operateur, fc.operandes_slugs, fc.formule, fc.parametres,
            table=table, enregistrement=enregistrement, df_table=df_table, ids_ordonnes=ids_ordonnes,
        )
        valeur_str = "" if (erreur or valeur is None) else _texte_valeur(valeur)
        _ecrire_valeur(enregistrement, fc.champ_cible, valeur_str)
        # Rend la valeur immédiatement visible pour une éventuelle formule
        # suivante qui l'utiliserait comme opérande (chaînage simple).
        ligne_dict[fc.champ_cible.slug] = valeur_str if valeur is None else valeur


def appliquer_formules_lignes(table, enregistrement):
    """Recalcule toutes les FormuleLigne actives sur CETTE ligne — mode
    "ligne", symétrique à `appliquer_formules_champs` (mode "colonne") :
    un ou plusieurs champs de cette même ligne partagent la même formule et
    reçoivent tous la même valeur calculée. Branché dans api_modifier_ligne
    juste après `appliquer_formules_champs`, pour un recalcul automatique à
    chaque modification d'un autre champ de la ligne."""
    formules = list(
        table.formules_lignes.filter(enregistrement=enregistrement)
        .select_related("formule").prefetch_related("cibles__champ_cible")
    )
    if not formules:
        return
    ligne_dict = enregistrement.to_dict()
    df_table, ids_ordonnes = None, None
    if any(_depend_des_autres_lignes(fl.operateur, fl.parametres) for fl in formules):
        from .calculs import _dataframe_table_source
        df_table = _dataframe_table_source(table, ecriture=True)
        ids_ordonnes = list(table.enregistrements.order_by("cree_le").values_list("id", flat=True))
    for fl in formules:
        valeur, erreur = calculer_valeur_champ(
            ligne_dict, fl.operateur, fl.operandes_slugs, fl.formule, fl.parametres,
            table=table, enregistrement=enregistrement, df_table=df_table, ids_ordonnes=ids_ordonnes,
        )
        valeur_str = "" if (erreur or valeur is None) else _texte_valeur(valeur)
        for cible in fl.cibles.all():
            _ecrire_valeur(enregistrement, cible.champ_cible, valeur_str)
            ligne_dict[cible.champ_cible.slug] = valeur_str if valeur is None else valeur


def recalculer_formule_ligne(formule_ligne):
    """Réapplique immédiatement `formule_ligne` sur tous ses champs cibles —
    utilisé à la création/mise à jour d'une FormuleLigne pour que la valeur
    apparaisse tout de suite (comme recalculer_colonne_entiere pour le mode
    colonne, mais restreint à la ligne de la formule). Retourne (ok, erreur)."""
    ligne_dict = formule_ligne.enregistrement.to_dict()
    valeur, erreur = calculer_valeur_champ(
        ligne_dict, formule_ligne.operateur, formule_ligne.operandes_slugs,
        formule_ligne.formule, formule_ligne.parametres,
        table=formule_ligne.table, enregistrement=formule_ligne.enregistrement,
    )
    valeur_str = "" if (erreur or valeur is None) else _texte_valeur(valeur)
    for cible in formule_ligne.cibles.all():
        _ecrire_valeur(formule_ligne.enregistrement, cible.champ_cible, valeur_str)
    return (erreur is None), erreur


def recalculer_formules_personnalisees_table(table, exclure_enregistrement_id=None):
    """Recalcul en cascade, façon Excel : une formule personnalisée de
    portée "colonne"/"table" (FormuleChamp OU FormuleLigne) peut dépendre de
    N'IMPORTE QUELLE ligne de la table — un rang, un cumul, un écart à la
    moyenne, une comparaison à la ligne précédente... Modifier, ajouter ou
    supprimer UNE seule ligne peut donc changer le résultat affiché sur
    TOUTES les autres, pas seulement celle qui vient d'être touchée.

    Recalcule donc, sur TOUTES les lignes de `table`, chaque formule
    "personnalisee" active (colonne ou ligne) — comme un classeur Excel qui
    recalcule tout ce qui dépend d'une cellule modifiée, pas seulement la
    cellule éditée. `exclure_enregistrement_id` évite de refaire un calcul
    déjà fait juste avant pour la ligne en cours d'édition (appelants :
    api_modifier_ligne, création de ligne, suppression de ligne).

    Les opérateurs prédéfinis (somme, moyenne...) n'ont pas besoin de ce
    recalcul en cascade tant qu'ils ne lisent que des colonnes DE LA MÊME
    ligne ; en mode « zones » (plages de lignes), ils en ont besoin comme une
    formule personnalisée (voir `_depend_des_autres_lignes`)."""
    formules_champs = [
        fc for fc in _trier_cascade(list(table.formules_champs.select_related("champ_cible", "formule").order_by("id")))
        if _depend_des_autres_lignes(fc.operateur, fc.parametres)
    ]
    formules_lignes = [
        fl for fl in table.formules_lignes.select_related("formule").prefetch_related("cibles__champ_cible")
        if _depend_des_autres_lignes(fl.operateur, fl.parametres)
    ]
    if not formules_champs and not formules_lignes:
        return

    from .calculs import _dataframe_table_source
    df_table = _dataframe_table_source(table, ecriture=True)
    ids_ordonnes = list(table.enregistrements.order_by("cree_le").values_list("id", flat=True))

    enregistrements = table.enregistrements.all()
    if exclure_enregistrement_id is not None:
        enregistrements = enregistrements.exclude(id=exclure_enregistrement_id)

    memo = {}
    for enr in enregistrements:
        ligne_dict = enr.to_dict()
        for fc in formules_champs:
            valeur, erreur = calculer_valeur_champ(
                ligne_dict, fc.operateur, fc.operandes_slugs, fc.formule, fc.parametres,
                table=table, enregistrement=enr, df_table=df_table, ids_ordonnes=ids_ordonnes, memo=memo,
            )
            valeur_str = "" if (erreur or valeur is None) else _texte_valeur(valeur)
            _ecrire_valeur(enr, fc.champ_cible, valeur_str)
            ligne_dict[fc.champ_cible.slug] = valeur_str if valeur is None else valeur
        for fl in formules_lignes:
            if fl.enregistrement_id != enr.id:
                continue  # une FormuleLigne ne s'applique qu'à SA ligne
            valeur, erreur = calculer_valeur_champ(
                ligne_dict, fl.operateur, fl.operandes_slugs, fl.formule, fl.parametres,
                table=table, enregistrement=enr, df_table=df_table, ids_ordonnes=ids_ordonnes, memo=memo,
            )
            valeur_str = "" if (erreur or valeur is None) else _texte_valeur(valeur)
            for cible in fl.cibles.all():
                _ecrire_valeur(enr, cible.champ_cible, valeur_str)
                ligne_dict[cible.champ_cible.slug] = valeur_str if valeur is None else valeur


def recalculer_colonne_entiere(formule_champ):
    """Réapplique `formule_champ` (persistant, ciblant une colonne) à
    TOUTES les lignes existantes de sa table — utilisé à la création/mise à
    jour d'un FormuleChamp pour combler les lignes déjà présentes.
    Retourne (nb_ok, nb_erreurs)."""
    nb_ok, nb_erreurs = 0, 0
    df_table, ids_ordonnes = None, None
    if _depend_des_autres_lignes(formule_champ.operateur, formule_champ.parametres):
        from .calculs import _dataframe_table_source
        df_table = _dataframe_table_source(formule_champ.table, ecriture=True)
        ids_ordonnes = list(formule_champ.table.enregistrements.order_by("cree_le").values_list("id", flat=True))
    memo = {}
    for enregistrement in formule_champ.table.enregistrements.all():
        ligne_dict = enregistrement.to_dict()
        valeur, erreur = calculer_valeur_champ(
            ligne_dict, formule_champ.operateur, formule_champ.operandes_slugs,
            formule_champ.formule, formule_champ.parametres,
            table=formule_champ.table, enregistrement=enregistrement,
            df_table=df_table, ids_ordonnes=ids_ordonnes, memo=memo,
        )
        valeur_str = "" if (erreur or valeur is None) else _texte_valeur(valeur)
        _ecrire_valeur(enregistrement, formule_champ.champ_cible, valeur_str)
        if erreur:
            nb_erreurs += 1
        else:
            nb_ok += 1
    return nb_ok, nb_erreurs


def recalculer_formules_table(table, enregistrement=None, toutes_les_lignes=False):
    """Point d'entrée unique du recalcul des formules ∑ (FormuleChamp) et
    « = » (FormuleLigne) après une écriture sur `table` — appelé par les
    vues de modification, création et suppression de ligne (et par la
    transformation de colonne).

      • `enregistrement` : la ligne qui vient d'être créée/modifiée — ses
        formules « ligne par ligne » sont recalculées d'abord ;
      • `toutes_les_lignes=True` : toutes les lignes sont recalculées
        (transformation de colonne : n'importe quelle valeur a pu changer) ;
      • puis, pour toutes les AUTRES lignes, seules les formules qui
        dépendent des autres lignes (personnalisées colonne/table ou
        opérateur prédéfini en mode zones) sont recalculées — c'est le seul
        cas où modifier ou supprimer une ligne change le résultat ailleurs
        (voir `recalculer_formules_personnalisees_table`).

    Sans effet (aucune requête de calcul) si la table n'a aucune formule ∑/« = ».
    Ne lève jamais : un échec de recalcul ne doit pas annuler l'écriture déjà faite."""
    try:
        if not (table.formules_champs.exists() or table.formules_lignes.exists()):
            return
        if toutes_les_lignes:
            for enr in table.enregistrements.all():
                appliquer_formules_champs(table, enr)
                appliquer_formules_lignes(table, enr)
            return
        exclure = None
        if enregistrement is not None:
            appliquer_formules_champs(table, enregistrement)
            appliquer_formules_lignes(table, enregistrement)
            exclure = enregistrement.id
        recalculer_formules_personnalisees_table(table, exclure_enregistrement_id=exclure)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(t("engine/calc_champ.py:g:638:47"), getattr(table, "id", None))
