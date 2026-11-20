from engine.i18n import t as _i18n_t, tf
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402


def _kwargs_parametres(modele_ia, sig):
    """Hyperparametres saisis -> (kwargs castes, valeurs EFFECTIVES = defauts
    de la signature + saisie). Une saisie non convertible est une erreur
    de configuration claire (avant : exception brute hors de tout try, donc
    entrainement bloque indefiniment sur 'en cours')."""
    accepte = set(sig.parameters)
    infos = {t["code"]: t for t in types_modeles_disponibles()}[modele_ia.type_modele]
    defauts = {p["nom"]: p["defaut"] for p in infos["parametres"]}
    kwargs, erreurs = {}, []
    for nom, valeur in (modele_ia.parametres or {}).items():
        if nom not in accepte or nom in _PARAMS_MASQUES:
            continue
        try:
            casted = _caster_parametre(nom, valeur, defauts.get(nom))
            if isinstance(casted, str) and nom in ("max_depth", "max_features", "gamma"):
                try:  # defaut None/texte : "5" -> 5, "0.5" -> 0.5
                    f = float(casted)
                    casted = int(f) if (nom == "max_depth" or (nom == "max_features" and f >= 1)) and f.is_integer() else f
                except ValueError:
                    pass
        except (TypeError, ValueError):
            erreurs.append(tf("engine/ia_integration/_part_003.py:g:29:28", v1=nom, v2=valeur))
            continue
        if casted is not None:
            kwargs[nom] = casted
    if erreurs:
        raise ContrainteModeleError("Parametres invalides :\n" + "\n".join("• " + x for x in erreurs))
    valeurs = {n: p.default for n, p in sig.parameters.items()
               if p.default is not inspect._empty and n not in _PARAMS_MASQUES}
    valeurs.update(kwargs)
    return kwargs, valeurs


def _message_type_indisponible(T, type_modele):
    """Message precis quand un type n'est pas dans le registre : soit le type n'existe pas, soit son module
    d'entrainement n'a pas pu etre charge (dependance manquante, version incompatible) — avant, ce second cas
    donnait « Type de modele inconnu » pour TOUS les modeles des qu'un seul import echouait."""
    raison = getattr(T, "_INDISPONIBLES", {}).get(type_modele)
    if raison:
        libelle = LIBELLES_TYPES.get(type_modele, type_modele)
        return (tf("engine/ia_integration/_part_003.py:g:48:17", v1=libelle, v2=raison))
    if not T._REGISTRY:
        return (_i18n_t("engine/ia_integration/_part_003.py:g:52:17"))
    return tf("engine/ia_integration/_part_003.py:g:54:12", v1=type_modele)


def _moteur_ia():
    """Charge le moteur IA ; une dependance manquante (jax/flax/optax/sklearn)
    devient une erreur explicite au lieu d'un entrainement qui ne demarre jamais."""
    try:
        return _trainer_module()
    except ImportError as e:
        raise EntrainementError(
            tf("engine/ia_integration/_part_003.py:g:64:13", v1=e.name or e)) from e


def valider_configuration(modele_ia):
    """Controle RAPIDE (sans charger les donnees), appele par la vue AVANT
    d'envoyer la tache : hyperparametres + colonnes EFFECTIVES (y compris
    quand `colonnes_entree` est vide = toutes les colonnes) vs types
    acceptes par l'algorithme. Leve ContrainteModeleError, toutes
    violations collectees."""
    T = _moteur_ia()
    type_modele = modele_ia.type_modele
    entry = T._REGISTRY.get(type_modele)
    if entry is None:
        raise ContrainteModeleError(_message_type_indisponible(T, type_modele))
    sig = inspect.signature(entry["train_fn"])
    accepte = set(sig.parameters)
    libelle = LIBELLES_TYPES.get(type_modele, type_modele)
    _, valeurs = _kwargs_parametres(modele_ia, sig)
    verifier_parametres(type_modele, valeurs, libelle)
    if not (("X" in accepte and "y" in accepte) or _est_supervise_texte(accepte)):
        return  # generatif : aucune table requise
    tables = list(modele_ia.tables_source.all())
    if not tables:
        raise ContrainteModeleError(tf("engine/ia_integration/_part_003.py:g:88:37", v1=libelle))
    n_lignes, noms = _nb_lignes_entrainement(_tables_sans_lignes_exclues(modele_ia, tables))
    verifier_volume_entrainement(n_lignes, libelle, noms)   # plus de 10 000 lignes : refusé ICI, avant la mise en file, avec la marche à suivre (couper)
    cibles = list(modele_ia.colonnes_sortie or ([modele_ia.colonne_cible] if modele_ia.colonne_cible else []))
    if _est_supervise_texte(accepte) and not cibles:
        raise ContrainteModeleError(tf("engine/ia_integration/_part_003.py:g:91:37", v1=libelle))
    ok_in, ok_out = types_entree_compatibles(type_modele), types_sortie_compatibles(type_modele)
    e = []
    sans_cible = type_modele in TYPES_SANS_CIBLE
    if sans_cible and cibles:
        e.append(tf("ia.nonsup.err_cible_inutile"))
        cibles = []   # le reste du controle traite alors toutes les colonnes comme des entrees
    for table in (tables[:1] if cibles else tables):
        champs = {c.slug: c for c in table.champs.all()}
        entrees = list(modele_ia.colonnes_entree or [s for s in champs if s not in cibles])
        if not entrees:
            e.append(tf("engine/ia_integration/_part_003.py:g:98:22", v1=table.nom))
        libs = None
        for slug in entrees + (cibles if table is tables[0] else []):
            c = champs.get(slug)
            if c is None:
                e.append(tf("engine/ia_integration/_part_003.py:g:103:26", v1=table.nom, v2=slug))
                continue
            libs = libs or dict(c.TYPE_CHOICES)
            ok, role = (ok_in, "entree") if slug in entrees else (ok_out, "cible")
            if ok and c.type_champ not in ok:
                e.append(_gtf("engine/ia_integration/_part_003.py:110:26", v1=(c.nom), v2=(libs.get(c.type_champ, c.type_champ)), v3=(role)) + ", ".join(libs.get(t, t) for t in ok) + ".")
        champs_in = [champs[s] for s in entrees if s in champs]
        if len(champs_in) > 1:
            # Texte + nombres (+ choix, dates...) en entree : autorise. Chaque colonne est encodee selon son
            # type (encodage_champs.ajuster : TF-IDF pour le texte, one-hot pour les choix...). Les types qui
            # n'acceptent pas le texte sont deja refuses plus haut par le controle de compatibilite des types.
            if any(c.type_champ in c.TYPES_TENSEUR or c.type_champ in c.TYPES_IMAGE_REFERENCE
                   or c.type_champ in c.TYPES_AUDIO_REFERENCE or c.type_champ in c.TYPES_VIDEO_REFERENCE
                   for c in champs_in):
                e.append(_i18n_t("engine/ia_integration/_part_003.py:g:117:26"))
        if cibles and set(entrees) & set(cibles):
            e.append(_i18n_t("engine/ia_integration/_part_003.py:g:120:22"))
        if (cibles or sans_cible) and _TableFiltree(table, ids_exclus=_ids_lignes_exclues(modele_ia, table), plage=_plage_lignes(modele_ia)).enregistrements_groupe.count() < 4:
            e.append(tf("engine/ia_integration/_part_003.py:g:122:22", v1=table.nom))
    if e:
        raise ContrainteModeleError(tf("engine/ia_integration/_part_003.py:g:124:37", v1=libelle) + "\n".join("• " + x for x in e))


def _slug_fichier(modele_ia):
    """Nom de dossier/fichier SUR pour le .gy d'un modele : le slug stocke peut venir de la saisie (ancien code :
    `nom.lower().replace(" ", "_")`) et contenir « ../ » ou des separateurs, ce qui ecrivait le modele hors de son
    dossier. Le slug d'un modele deja entraine garde son emplacement habituel quand il est deja sur."""
    from django.utils.text import slugify
    brut = str(modele_ia.slug or "")
    if brut and re.fullmatch(r"[A-Za-z0-9_-]+", brut):
        return brut
    return slugify(brut) or f"modele-{modele_ia.pk}"


def _supprimer(chemin):
    try:
        os.remove(chemin)
    except OSError:
        pass


TYPES_FINETUNE = {"mlp"}


def finetune_supporte(type_modele):
    """Types dont le .gy peut servir de point de depart (warm start)."""
    return type_modele in TYPES_FINETUNE


def _restreindre_plage(qs, debut, fin):
    """Lignes de `qs` situees aux positions debut..fin (1-based, bornes incluses) dans SON ordre. Les positions se comptent sur la
    table COMPLETE (avant ecartement des lignes) : « lignes 10 001 a 20 000 » reste stable. Un queryset decoupe ne pouvant plus etre
    filtre, on relit seulement la 1re et la derniere ligne de la tranche et on borne par leurs cles de tri (ordre lexicographique,
    ex. cree_le puis id) : aucune liste d'ids, donc aucune limite de parametres SQL."""
    from django.db.models import Q
    ordre = list(qs.query.order_by)
    if not ordre or any(o.startswith("-") or o == "?" for o in ordre):
        ids = list(qs.values_list("id", flat=True)[max(0, debut - 1):fin])   # tri inattendu : repli sur les ids de la tranche
        return qs.filter(id__in=ids)
    premiere = list(qs.values_list(*ordre)[max(0, debut - 1):max(0, debut - 1) + 1])
    if not premiere:
        return qs.none()                                  # la plage commence apres la derniere ligne
    derniere = list(qs.values_list(*ordre)[max(0, fin - 1):max(0, fin - 1) + 1])

    def borne(valeurs, minimum):
        q = Q()
        for i, (champ, v) in enumerate(zip(ordre, valeurs)):
            cond = {ordre[j]: valeurs[j] for j in range(i)}
            cond[champ + ("__gt" if minimum else "__lt")] = v
            q |= Q(**cond)
        return q | Q(**dict(zip(ordre, valeurs)))        # egalite complete (le tri finit par id : cle unique)

    qs = qs.filter(borne(premiere[0], True))
    if derniere:                                          # sinon la plage depasse la fin : jusqu'a la derniere ligne
        qs = qs.filter(borne(derniere[0], False))
    return qs


class _TableFiltree:
    """Vue d'une TableDynamique restreinte aux lignes NOUVELLES **ou MODIFIEES** :
    seule `enregistrements_groupe` change, tout le reste est delegue a la table.

    - nouvelle : id > jalon (ou, sans jalon, cree_le > date du dernier entrainement) ;
    - modifiee : maj_le > debut du dernier entrainement (maj_le est rafraichi a
      chaque edition de cellule, cf. apps.toucher_maj_ligne_sur_valeur)."""

    def __init__(self, table, id_min=None, depuis=None, maj_depuis=None, ids_exclus=None, plage=None):
        self._table, self._id_min, self._depuis, self._maj_depuis = table, id_min, depuis, maj_depuis
        # Plage de positions (debut, fin) 1-based, bornes incluses, dans l'ordre de enregistrements_groupe ; None = tout.
        self._plage = plage
        # Feature engineering : lignes retirees de la COPIE de travail (la vraie table n'est jamais modifiee).
        self._ids_exclus = frozenset(ids_exclus or ())

    def __getattr__(self, nom):
        return getattr(self._table, nom)

    @property
    def enregistrements_groupe(self):
        from django.db.models import Q
        qs = self._table.enregistrements_groupe
        if self._plage:
            qs = _restreindre_plage(qs, *self._plage)
        cond = None
        if self._id_min is not None:
            cond = Q(id__gt=self._id_min)
        elif self._depuis is not None:
            cond = Q(cree_le__gt=self._depuis)
        if self._maj_depuis is not None:
            m = Q(maj_le__gt=self._maj_depuis)
            cond = m if cond is None else (cond | m)
        if cond is not None:
            qs = qs.filter(cond)
        if self._ids_exclus:
            qs = qs.exclude(id__in=list(self._ids_exclus))
        return qs


def _id_max_lignes(table):
    from django.db.models import Max
    return table.enregistrements_groupe.aggregate(m=Max("id"))["m"] or 0


_CLE_DEBUT = "_debut"   # cle reservee du JSON jalon_lignes : instant de DEBUT du dernier entrainement (pas de migration)


def _debut_derniere_maj(modele_ia, jalon):
    """Date a partir de laquelle une ligne est consideree comme modifiee : le DEBUT
    du dernier entrainement (ainsi une edition faite pendant l'entrainement est
    reprise la fois d'apres). Modeles anterieurs : date de fin d'entrainement."""
    brut = (jalon or {}).get(_CLE_DEBUT)
    if brut:
        try:
            import datetime
            d = datetime.datetime.fromisoformat(brut)
            return d if timezone.is_aware(d) else timezone.make_aware(d)
        except (TypeError, ValueError):
            pass
    return modele_ia.entraine_le


def _tables_delta(modele_ia, tables=None):
    """Vues filtrees (lignes nouvelles ou modifiees) des tables source du modele."""
    jalon = modele_ia.jalon_lignes or {}
    maj = _debut_derniere_maj(modele_ia, jalon)
    return [
        _TableFiltree(t, jalon.get(str(t.pk)),
                      None if str(t.pk) in jalon else modele_ia.entraine_le, maj,
                      ids_exclus=_ids_lignes_exclues(modele_ia, t))
        for t in (modele_ia.tables_source.all() if tables is None else tables)]


def _ids_lignes_exclues(modele_ia, table):
    """Ids des lignes retirees par l'utilisateur (feature engineering) pour une table source."""
    from .. import ia_features
    return ia_features.ids_exclus(modele_ia, table.pk)


def _plage_lignes(modele_ia):
    """(debut, fin) 1-based, bornes incluses, de la plage de lignes choisie pour l'entrainement ; None = toutes les lignes.
    `fin` absente = jusqu'a la derniere ligne."""
    brut = getattr(modele_ia, "plage_lignes", None) or {}
    try:
        debut = int(brut.get("debut") or 1)
        fin = int(brut["fin"]) if brut.get("fin") not in (None, "") else None
    except (TypeError, ValueError):
        return None
    debut = max(1, debut)
    if debut == 1 and fin is None:
        return None
    # « a partir de la ligne N » sans fin = autant de lignes que le plafond d'entrainement ; une fin plus lointaine est ramenee au plafond.
    plafond = debut + LIMITE_LIGNES_ENTRAINEMENT - 1
    return debut, (plafond if fin is None else min(fin, plafond))


def _tables_sans_lignes_exclues(modele_ia, tables):
    """Entrainement initial : vue de chaque table source restreinte a la plage de lignes choisie et SANS les lignes ecartees
    (table inchangee si ni plage ni ecartement)."""
    plage = _plage_lignes(modele_ia)
    sortie = []
    for t in tables:
        ids = _ids_lignes_exclues(modele_ia, t)
        sortie.append(_TableFiltree(t, ids_exclus=ids, plage=plage) if (ids or plage) else t)
    return sortie


def _nb_lignes_entrainement(tables):
    """Lignes que l'entraînement lira dans `tables` (enregistrements_groupe : toutes les parties d'un groupe, comptées UNE fois même si
    plusieurs tables source en font partie) et noms des tables concernées."""
    groupes, total, noms = set(), 0, []
    for t in tables:
        g = getattr(t, "groupe_id", None)
        if g:
            if g in groupes:
                continue
            groupes.add(g)
        total += t.enregistrements_groupe.count()
        noms.append(t.nom)
    return total, noms


def compter_nouvelles_lignes(modele_ia):
    """Nombre de lignes ajoutees OU modifiees dans les tables source depuis le
    dernier entrainement (0 si le modele n'a jamais ete entraine)."""
    if not modele_ia.fichier_modele:
        return 0
    return sum(v.enregistrements_groupe.count() for v in _tables_delta(modele_ia))


def lancer_entrainement(modele_ia, finetune=None):
    """Orchestration complete, TOUT sous un seul garde-fou : n'importe quelle
    exception (moteur IA absent, parametre invalide, donnees, entrainement)
    devient statut 'erreur' + message + journal visibles dans l'interface."""
    journal = Journal(modele_ia.pk)
    journal.demarrer_battement()
    jeton = _VECTORISATION.set(vectorisation_active(modele_ia, finetune))
    try:
        return _executer_entrainement(modele_ia, journal, finetune=finetune)
    except Exception as e:
        journal.arreter()
        journal.echec(e)
        modele_ia.statut, modele_ia.erreur = "erreur", str(e)
        type(modele_ia).objects.filter(pk=modele_ia.pk).update(
            statut="erreur", erreur=str(e), modifie_le=timezone.now())
        if isinstance(e, (DatasetError, EntrainementError)):
            raise
        raise EntrainementError(str(e)) from e
    finally:
        _VECTORISATION.reset(jeton)
        journal.arreter()


def vectorisation_voulue(modele_ia):
    """Ce modele apprend-il sur des vecteurs de caracteristiques (images / sons) ? Oui par defaut pour les types
    discriminatifs (TYPES_MODELES_VECTORISES) ; `parametres["vectorisation"] = "brut"` garde les pixels / echantillons."""
    if modele_ia.type_modele not in TYPES_MODELES_VECTORISES:
        return False
    return str((modele_ia.parametres or {}).get("vectorisation") or "auto").lower() != "brut"


def vectorisation_active(modele_ia, finetune=None):
    """Un affinage suit la recette du modele d'origine (jamais un melange de vecteurs et de pixels)."""
    if finetune is not None:
        return bool((modele_ia.structure_entree or {}).get("vectorisation"))
    return vectorisation_voulue(modele_ia)


def _est_classification(type_modele, accepte, kwargs, dataset):
    """Tâche supervisée (classification ou régression) : même règle que l'entraînement (IA/tache_cible.py), pour choisir la mesure du test."""
    if type_modele in ("sklearn_ridge", "sklearn_lasso"):
        return False
    tache = kwargs.get("task")
    if tache in ("classification", "regression"):
        return tache == "classification"
    if dataset.get("classes_cible") is not None:
        return True
    if "num_classes" in accepte and type_modele != "mlp":
        return True
    from IA.tache_cible import detecter_tache
    return detecter_tache(dataset["y"]) == "classification"


def _tester_apres_entrainement(modele, type_modele, X_train, y_train, jeu_test, classification, nb_classes, texte=False):
    """Test automatique sur les 20 % jamais vus (IA/separation.py : seul endroit qui découpe ET qui juge). Ne lève jamais."""
    from IA import separation as _sep
    X_test, y_test = jeu_test
    try:
        if texte:   # phrases → séquences d'indices, avec le MÊME préprocesseur que celui sauvegardé dans le modèle
            from IA.train.slm import TextPreprocessor
            prep, seq_len = TextPreprocessor.from_params(modele.config.get("preprocessor_params")), modele.config.get("seq_len")
            X_train = np.array([prep.encode(p, seq_len) for p in X_train[:_sep.MAX_LIGNES_SCORE_ENTRAINEMENT]])
            y_train = np.asarray(y_train)[:len(X_train)]
            X_test = np.array([prep.encode(p, seq_len) for p in X_test])
        return _sep.tester_modele(
            modele.predict, X_test, y_test, X_train=X_train, y_train=y_train, classification=classification,
            valeurs_classes=_valeurs_classes_modele(type_modele, modele.config), nb_classes=nb_classes)
    except Exception as e:
        return {"verdict": _sep.VERDICT_NON_VERIFIABLE, "metrique": "accuracy" if classification else "r2", "score": None,
                "n_test": int(len(y_test)), "raison": "echec_test", "detail": str(e)[:200]}


def _signaler_test_automatique(journal, test):
    """Texte (traduit) du verdict dans le journal d'entraînement : succès, algorithme non approprié, surapprentissage ou test impossible."""
    from IA import separation as _sep
    v, acc = test.get("verdict"), test.get("metrique") == "accuracy"
    if v == _sep.VERDICT_BON:
        journal.log(tf("ia.test_auto.bon_acc", v1=round(test["score"] * 100, 1), v2=test["n_test"], v3=round(test["reference"] * 100, 1)) if acc
                    else tf("ia.test_auto.bon_r2", v1=round(test["score"], 3), v2=test["n_test"]), phase="termine")
    elif v == _sep.VERDICT_INAPPROPRIE and _sep.est_au_hasard(test):
        journal.log(tf("ia.test_auto.hasard_acc", v1=round(test["score"] * 100, 1), v2=test["n_test"], v3=round(test["reference"] * 100, 1)) if acc
                    else tf("ia.test_auto.hasard_r2", v1=round(test["score"], 3), v2=test["n_test"]), "warn")
    elif v == _sep.VERDICT_INAPPROPRIE:
        journal.log(tf("ia.test_auto.mauvais_acc", v1=round(test["score"] * 100, 1), v2=test["n_test"], v3=round(test["reference"] * 100, 1)) if acc
                    else tf("ia.test_auto.mauvais_r2", v1=round(test["score"], 3), v2=test["n_test"], v3=_sep.SEUIL_R2), "warn")
    elif test.get("raison") == "peu_de_lignes":
        journal.log(tf("ia.test_auto.non_verifiable", v1=test.get("n_test", 0), v2=_sep.TEST_MIN_LIGNES), "warn")
    else:
        journal.log(_i18n_t("ia.test_auto.echec"), "warn")
    if test.get("surapprentissage") and test.get("score_entrainement") is not None and test.get("score") is not None:
        journal.log(tf("ia.test_auto.surapprentissage", v1=round(test["score_entrainement"] * 100, 1), v2=round(test["score"] * 100, 1)), "warn")


def _executer_entrainement(modele_ia, journal, finetune=None):
    """Resout le dataset depuis les tables source, caste les hyperparametres,
    impose les contraintes de l'algorithme, entraine via IA.Trainer vers un
    fichier temporaire (le modele precedent n'est remplace qu'en cas de
    succes), met a jour ModeleIA."""
    t0 = time.time()
    journal.log(_i18n_t("engine/ia_integration/_part_003.py:g:239:17"), phase="preparation")
    T = _moteur_ia()
    type_modele = modele_ia.type_modele
    libelle = LIBELLES_TYPES.get(type_modele, type_modele)
    entry = T._REGISTRY.get(type_modele)
    if entry is None:
        raise EntrainementError(_message_type_indisponible(T, type_modele))
    sig = inspect.signature(entry["train_fn"])
    accepte = set(sig.parameters.keys())
    supervise = "X" in accepte and "y" in accepte
    supervise_texte = _est_supervise_texte(accepte)

    kwargs, valeurs = _kwargs_parametres(modele_ia, sig)
    verifier_parametres(type_modele, valeurs, libelle)
    journal.log(f"Parametres valides ({libelle}).")

    # Jalon : plus grand id de ligne present AU DEBUT, memorise a la fin pour
    # que le prochain fine-tuning ne reprenne que ce qui arrive apres.
    tables_source = list(modele_ia.tables_source.all())
    debut_entrainement = timezone.now()   # avant toute lecture : sert de repere aux lignes modifiees
    jalon_actuel = {str(t.pk): _id_max_lignes(t) for t in tables_source}
    jalon_actuel[_CLE_DEBUT] = debut_entrainement.isoformat()
    init_model = None
    if finetune is None:
        tables_source = _tables_sans_lignes_exclues(modele_ia, tables_source)
        n_lignes, noms = _nb_lignes_entrainement(tables_source)
        verifier_volume_entrainement(n_lignes, libelle, noms)   # même garde que valider_configuration : les données ont pu grossir depuis
    plage = _plage_lignes(modele_ia) if finetune is None else None
    if plage:
        journal.log(tf("engine/ia_integration/_part_003.py:plage_journal", v1=plage[0], v2=plage[1], v3=n_lignes))
    n_ecartees = sum(len(_ids_lignes_exclues(modele_ia, t)) for t in modele_ia.tables_source.all())
    if n_ecartees:
        journal.log(tf("engine/ia_integration/_part_003.py:fe_journal", v1=n_ecartees))
    if finetune is not None:
        if not finetune_supporte(type_modele):
            raise EntrainementError(tf("engine/ia_integration/_part_003.py:g:264:37", v1=libelle))
        if not modele_ia.fichier_modele:
            raise EntrainementError(_i18n_t("engine/ia_integration/_part_003.py:g:266:37"))
        tables_source = _tables_delta(modele_ia, tables_source)
        n_new = sum(t.enregistrements_groupe.count() for t in tables_source)
        if n_new == 0:
            raise EntrainementError(_i18n_t("engine/ia_integration/_part_003.py:g:270:37"))
        journal.log(tf("engine/ia_integration/_part_003.py:g:271:21", v1=n_new))
        ancien = T.Trainer.load(os.path.join(dj_settings.MEDIA_ROOT, modele_ia.fichier_modele))
        init_model = {"weights": ancien.weights, "config": ancien.config}
        # Pas plus doux que l'entrainement initial, pour ne pas ecraser ce qui est appris.
        kwargs["epochs"] = int(finetune.get("epochs") or max(5, int(valeurs.get("epochs") or 100) // 5))
        kwargs["lr"] = float(finetune.get("lr") or float(valeurs.get("lr") or 0.01) * 0.3)
        kwargs["init_model"] = init_model
        valeurs.update(epochs=kwargs["epochs"], lr=kwargs["lr"])

    if supervise_texte:
        journal.log(_i18n_t("engine/ia_integration/_part_003.py:g:281:21"))
        tache = (modele_ia.parametres or {}).get("tache") or sig.parameters["tache"].default
        dataset = _resoudre_dataset_texte_slm(
            tables_source, tache,
            colonnes_entree=modele_ia.colonnes_entree,
            colonne_cible=modele_ia.colonne_cible,
            colonnes_sortie=modele_ia.colonnes_sortie,
        )
    elif supervise:
        journal.log(_i18n_t("engine/ia_integration/_part_003.py:g:290:21"))
        sans_cible = type_modele in TYPES_SANS_CIBLE
        dataset = resoudre_dataset(
            tables_source, "" if sans_cible else modele_ia.colonne_cible,
            colonnes_entree=modele_ia.colonnes_entree,
            colonnes_sortie=None if sans_cible else modele_ia.colonnes_sortie,
            sans_cible=sans_cible,
            # Fine-tuning : encodages et classes du modele d'origine reutilises (jamais recalcules sur le delta).
            structure_figee=(modele_ia.structure_entree if finetune is not None else None),
        )
    else:
        # Modele generatif/non supervise (gan, ldm, slm) : aucune table n'est
        # requise (il s'entraine sur ses propres donnees/hyperparametres).
        journal.log(_i18n_t("engine/ia_integration/_part_003.py:g:299:21"))
        dataset = {"X": None, "y": None, "documents": None, "n_lignes": None}
    _valider_compatibilite_modele(type_modele, sig, dataset)

    X_final = None
    if dataset["X"] is not None:
        # Compatibilite de forme entre le dataset resolu et les hyperparametres
        # de forme (feature_dim, input_shape) : deduits des donnees reelles
        # plutot que de forcer l'utilisateur a les calculer a la main.
        X = dataset["X"]
        if "feature_dim" in accepte and "feature_dim" not in kwargs:
            X = X.reshape(X.shape[0], -1)
            kwargs["feature_dim"] = X.shape[1]
        elif "input_shape" in accepte and "input_shape" not in kwargs and X.ndim > 1:
            kwargs["input_shape"] = X.shape[1:]
        if type_modele == "transformer" and "seq_len" not in kwargs and X.ndim == 2:
            kwargs["seq_len"] = X.shape[1]
        if "X" in accepte:
            kwargs["X"] = X
        X_final = X
    if "y" in accepte and dataset["y"] is not None:
        kwargs["y"] = dataset["y"]
    if "num_classes" in accepte and "num_classes" not in kwargs and dataset.get("num_classes"):
        kwargs["num_classes"] = dataset["num_classes"]
    elif "num_classes" in accepte and "num_classes" not in kwargs and dataset["y"] is not None:
        kwargs["num_classes"] = int(np.unique(dataset["y"]).size)
    if "task" in accepte and "task" not in kwargs:
        # MLP : une cible categorielle (encodee en classes) reste une classification ; sinon le modele choisit
        # classification ou regression d'apres la cible (IA/tache_cible.py).
        kwargs["task"] = "classification" if dataset.get("classes_cible") is not None else "auto"
    if supervise_texte:
        kwargs["sentences"] = dataset["sentences"]
        kwargs["labels"] = dataset["labels"]
    # NB: pas d'injection de `vocab` pour 'slm' (next_word) : train_slm_next_word
    # s'entraine sur des exemples synthetiques codes en dur (voir docstring de
    # resoudre_dataset) — une table JSON n'a aucun effet utile sur ce type.

    if type_modele == "transformer" and dataset["X"] is not None:
        _valider_tokens_transformer(dataset["X"], kwargs.get("vocab_size", sig.parameters["vocab_size"].default))

    # ── Contraintes de l'algorithme sur les donnees reellement resolues ──
    valeurs.update({k: v for k, v in kwargs.items() if k not in ("X", "y", "init_model")})
    if finetune is None:
        avertissements = verifier_dataset(
            type_modele, {"X": X_final if X_final is not None else dataset["X"], "y": dataset["y"]},
            valeurs, libelle)
    else:
        # Fine-tuning : quelques lignes nouvelles (parfois d'une seule classe) suffisent. Les seuils de volume
        # (>= 4 lignes, >= 2 classes, classes <= n/2) valent pour un entrainement complet, pas pour un delta :
        # seule la validite des valeurs est controlee ici.
        avertissements = []
        X_delta = X_final if X_final is not None else dataset["X"]
        if X_delta is not None and np.size(X_delta) and not np.isfinite(np.asarray(X_delta, dtype=np.float64)).all():
            raise DatasetError(_gt("rg5.msg.075"))
    verifier_parametres(type_modele, valeurs, libelle)  # re-verifie les formes deduites (ex: noyau CNN)
    if dataset["X"] is not None:
        y = dataset["y"]
        classes = f", {int(np.unique(y).size)} classe(s)" if y is not None else ""
        journal.log(_gtf("engine/ia_integration/_part_003.py:418:21", v1=(dataset['X'].shape[0]), v2=(tuple(dataset['X'].shape)), v3=(classes)))
    elif dataset.get("sentences"):
        journal.log(f"Dataset texte pret : {len(dataset['sentences'])} phrase(s).")
    for a in avertissements:
        journal.log(a, "warn")

    # ── Séparation 80 % / 20 % (IA/separation.py, le SEUL endroit qui découpe) : le modèle n'apprend que sur 80 % du jeu, quelle que
    # soit sa taille ; les 20 % restants servent au test automatique qui suit l'entraînement. Jeux sans cible (groupes, projection,
    # anomalies) et génératifs : pas de vérité à comparer, aucun découpage.
    from IA import separation as _sep
    jeu_test, classification_test, donnees_train, nb_classes_test = None, True, None, None
    if supervise_texte and dataset.get("sentences"):
        s = _sep.separer(dataset["sentences"], dataset["labels"], classification=True)
        if s.info["split_effectue"]:
            kwargs["sentences"], kwargs["labels"] = list(s.X_train), [int(v) for v in s.y_train]
            jeu_test, donnees_train, nb_classes_test = (s.X_test, s.y_test), (s.X_train, s.y_train), len(dataset.get("classes_cible") or [])
        journal.log(tf("ia.separation.journal", v1=s.info["n_train"], v2=s.info["n_test"]) if s.info["split_effectue"] else _i18n_t("ia.separation.trop_petit"), "info" if s.info["split_effectue"] else "warn")
    elif supervise and type_modele not in TYPES_SANS_CIBLE and kwargs.get("X") is not None and kwargs.get("y") is not None:
        classification_test = _est_classification(type_modele, accepte, kwargs, dataset)
        s = _sep.separer(kwargs["X"], kwargs["y"], classification=classification_test)
        if s.info["split_effectue"]:
            kwargs["X"], kwargs["y"] = s.X_train, s.y_train
            jeu_test, donnees_train = (s.X_test, s.y_test), (s.X_train, s.y_train)
            nb_classes_test = kwargs.get("num_classes") or (len(dataset["classes_cible"]) if dataset.get("classes_cible") is not None else int(np.unique(dataset["y"]).size))
        journal.log(tf("ia.separation.journal", v1=s.info["n_train"], v2=s.info["n_test"]) if s.info["split_effectue"] else _i18n_t("ia.separation.trop_petit"), "info" if s.info["split_effectue"] else "warn")

    slug_fichier = _slug_fichier(modele_ia)
    dossier = _dossier_modele(modele_ia.entreprise, slug_fichier)
    chemin_abs = os.path.join(dossier, f"{slug_fichier}.gy")
    chemin_tmp = os.path.join(dossier, f"{slug_fichier}.encours.gy")

    modele_ia.statut = "entrainement"
    modele_ia.erreur = ""
    # Repart toujours a False : un drapeau leve lors d'un entrainement
    # precedent (deja consomme) ne doit jamais annuler celui-ci.
    modele_ia.demande_annulation = False
    modele_ia.demande_pause = False
    modele_ia.save(update_fields=["statut", "erreur", "demande_annulation", "demande_pause", "modifie_le"])

    trainer = T.Trainer(verbose=False)
    progress_cb = _CallbackProgression(journal)
    annulation_cb = _CallbackAnnulation(modele_ia.pk)
    pause_cb = _CallbackPause(modele_ia.pk, journal, annulation=annulation_cb)
    try:
        with _sep.jeu_de_test_impose(*(jeu_test or (None, None))):   # les entraîneurs sklearn reprennent ces 20 % tels quels (aucun second découpage)
            modele_entraine = trainer.train(type=type_modele, save_path=chemin_tmp,
                                            callbacks=[progress_cb, annulation_cb, pause_cb], **kwargs)
        if progress_cb.diverge:
            raise EntrainementError(
                tf("engine/ia_integration/_part_003.py:g:372:17", v1=progress_cb.diverge))
        if annulation_cb.annule:
            # Le fichier partiel n'est pas retenu : le modele precedent reste intact.
            _supprimer(chemin_tmp)
            journal.arreter()
            journal.log("Entrainement annule a ta demande.", "warn", phase="annule")
            modele_ia.statut, modele_ia.erreur = "annule", ""
            modele_ia.demande_annulation = False
            modele_ia.demande_pause = False
            modele_ia.progression = dict(journal.etat)
            modele_ia.save(update_fields=["statut", "erreur", "demande_annulation", "demande_pause",
                                          "progression", "modifie_le"])
            return modele_ia
        if finetune is not None and os.path.exists(chemin_abs):
            shutil.copyfile(chemin_abs, os.path.join(dossier, f"{slug_fichier}.avant-finetune.gy"))
        os.replace(chemin_tmp, chemin_abs)
    finally:
        _supprimer(chemin_tmp)

    # Test automatique sur les 20 % jamais vus : le modèle est-il bon ? Sinon, l'algorithme est signalé « pas approprié ».
    test_auto = None
    if jeu_test is not None:
        test_auto = _tester_apres_entrainement(modele_entraine, type_modele, donnees_train[0], donnees_train[1], jeu_test,
                                               classification_test, nb_classes_test, texte=bool(supervise_texte and dataset.get("sentences")))
        test_auto.update(fraction_entrainement=_sep.FRACTION_ENTRAINEMENT, fraction_test=_sep.FRACTION_TEST)
        _signaler_test_automatique(journal, test_auto)
    journal.arreter()
    journal.log(tf("engine/ia_integration/_part_003.py:g:393:17", v1=time.time() - t0), phase="termine")
    modele_ia.statut = "entraine"
    modele_ia.fichier_modele = os.path.relpath(chemin_abs, dj_settings.MEDIA_ROOT)
    modele_ia.metriques = {
        "accuracy": trainer.final_accuracy,
        "epoques_effectuees": len(trainer.history),
        "derniere_epoque": trainer.history[-1] if trainer.history else None,
    }
    if test_auto is not None:
        modele_ia.metriques["test_automatique"] = test_auto
        if test_auto.get("metrique") == "accuracy" and test_auto.get("score") is not None:
            # La précision affichée est celle des 20 % jamais vus (honnête), pas celle de l'entraînement.
            modele_ia.metriques["accuracy_entrainement"] = modele_ia.metriques["accuracy"]
            modele_ia.metriques["accuracy"] = test_auto["score"]
    if type_modele in TYPES_SANS_CIBLE:
        # Ce que le modele a trouve (affiche par ia.html) : nombre de groupes, taille de chacun, bruit, silhouette.
        cfg = getattr(modele_entraine, "config", None) or {}
        if famille_sans_cible(type_modele) == "groupes":
            modele_ia.metriques["groupes"] = {
                "n": cfg.get("n_groupes"),
                "tailles": {str(g): n for g, n in (cfg.get("tailles_groupes") or {}).items()},
                "n_bruit": cfg.get("n_bruit") or 0,
                "taux_bruit": cfg.get("taux_bruit") or 0.0,
                "silhouette": cfg.get("silhouette"),
            }
        else:
            # Projection (information conservee, variance par composante) ou anomalies (nombre, taux, part attendue).
            modele_ia.metriques["resume"] = cfg.get("resume") or {}
            modele_ia.metriques["famille"] = famille_sans_cible(type_modele)
    if finetune is None:
        _sans = type_modele in TYPES_SANS_CIBLE
        modele_ia.structure_entree = _detecter_structure_entree(
            modele_ia.tables_source.all(), "" if _sans else modele_ia.colonne_cible, dataset, X_final,
            colonnes_entree=modele_ia.colonnes_entree, colonnes_sortie=None if _sans else modele_ia.colonnes_sortie,
        )
    else:
        # La structure d'entree reste celle du modele d'origine (memes colonnes).
        modele_ia.metriques["finetune"] = True
        modele_ia.metriques["lignes_finetune"] = int(dataset["X"].shape[0]) if dataset["X"] is not None else None
    modele_ia.jalon_lignes = jalon_actuel
    modele_ia.entraine_le = timezone.now()
    modele_ia.progression = dict(journal.etat)
    modele_ia.demande_pause = False
    modele_ia.save()
    return modele_ia


def _valeurs_classes_modele(type_modele, config):
    """Valeurs de classe du modele, dans l'ordre des colonnes de sa sortie probabiliste (None si non applicable :
    regression, binaire a sortie unique...). sklearn : `classes` (clf.classes_) ; MLP multiclasse : `unique_classes`."""
    config = config or {}
    if type_modele in TYPES_SANS_CIBLE:
        return None   # numeros de groupe, pas des classes de cible : voir libelles_groupes() dans lancer_inference
    if str(type_modele).startswith("sklearn_"):
        brut = config.get("classes")
    elif type_modele == "mlp" and config.get("multiclass"):
        brut = config.get("unique_classes")
    else:
        return None
    return list(brut) if isinstance(brut, (list, tuple)) and len(brut) > 0 else None


def _est_image_memoire(entree):
    """Image deja decodee : ndarray 2D/3D ou image PIL (une chaine reste un chemin/URL, une liste un JSON de valeurs)."""
    if isinstance(entree, np.ndarray):
        return entree.ndim in (2, 3)
    return _PILImage is not None and isinstance(entree, _PILImage.Image)


def lancer_inference(modele_ia, entree, *, bgr=False, rate=None, modele_charge=None):
    """Charge le .gy entraine et effectue une prediction sur `entree`.

    Respecte la structure d'entree memorisee a l'entrainement
    (`modele_ia.structure_entree`) :
    - mode "image"/"audio"/"video" : `entree` peut etre directement une
      URL/chemin (string), decodee exactement comme a l'entrainement (meme
      forme_champ) — plus besoin de fournir un JSON de pixels/features bruts.
    - mode "tenseur"/"numerique" : `entree` reste un JSON (liste ou liste de
      listes) de valeurs numeriques, comme avant.
    Dans tous les cas, la forme finale est validee contre celle utilisee a
    l'entrainement (`forme_modele`/`n_features`) avant l'appel a predict(),
    pour echouer avec un message clair plutot qu'une erreur numpy opaque.
    """
    if not modele_ia.fichier_modele:
        raise EntrainementError(_i18n_t("engine/ia_integration/_part_003.py:g:433:33"))
    T = _trainer_module()
    chemin_abs = os.path.join(dj_settings.MEDIA_ROOT, modele_ia.fichier_modele)
    # `modele_charge` : modele deja en memoire (flux camera / micro : on ne relit pas le fichier a chaque image).
    modele = modele_charge if modele_charge is not None else T.Trainer.load(chemin_abs)

    structure = modele_ia.structure_entree or {}
    mode = structure.get("mode", "numerique")
    forme_champ = tuple(structure["forme_champ"]) if structure.get("forme_champ") else None
    n_features = structure.get("n_features")

    if mode == "numerique" and structure.get("encodeurs") and isinstance(entree, dict):
        # Vrais champs de la table ({slug: valeur}) : mêmes recettes qu'à l'entraînement.
        from .. import encodage_champs as _enc
        try:
            X = _enc.encoder_entree(structure["encodeurs"], entree)
        except ValueError as e:
            raise EntrainementError(str(e))
    elif mode == "image" and isinstance(entree, str):
        if structure.get("vectorisation"):
            X = _vectoriser_image(entree).reshape(1, -1)
        else:
            X = _charger_pixels_image(entree, forme_champ).reshape(1, -1)
    elif mode == "audio" and isinstance(entree, str):
        if structure.get("vectorisation"):
            X = _vectoriser_audio(entree).reshape(1, -1)
        else:
            X = _charger_features_audio(entree, forme_champ).reshape(1, -1)
    elif mode == "image" and _est_image_memoire(entree):
        # Image deja decodee (image d'un flux camera, ndarray ou PIL) : meme recette qu'a l'entrainement.
        try:
            if structure.get("vectorisation"):
                X = _vecto.vecteur_image(entree, bgr=bgr).reshape(1, -1)
            else:
                X = _pixels_normalises(_vecto.vers_pil(entree, bgr=bgr), forme_champ).reshape(1, -1)
        except _vecto.VectorisationError as e:
            raise EntrainementError(tf("ia.vecto.err_image", v1="", v2=e))
    elif mode == "audio" and isinstance(entree, (bytes, bytearray, memoryview, np.ndarray)):
        # Son deja decode : PCM 16 bits little-endian mono (flux micro) ou onde flottante dans [-1, 1].
        if not rate:
            raise EntrainementError(tf("ia.vecto.err_rate"))
        onde = (np.frombuffer(bytes(entree)[: len(entree) - len(entree) % 2], dtype="<i2").astype(np.float64) / 32768.0
                if not isinstance(entree, np.ndarray) else np.asarray(entree, dtype=np.float64).ravel())
        try:
            if structure.get("vectorisation"):
                X = _vecto.vecteur_audio(onde, rate).reshape(1, -1)
            else:
                segments = np.array_split(onde, forme_champ[0] if forme_champ else 26)
                X = np.array([np.abs(s).mean() if s.size else 0.0 for s in segments], dtype=np.float64).reshape(1, -1)
        except _vecto.VectorisationError as e:
            raise EntrainementError(tf("ia.vecto.err_audio", v1="", v2=e))
    elif mode == "video" and isinstance(entree, str):
        X = _charger_features_video(entree, forme_champ).reshape(1, -1)
    elif mode == "texte" and isinstance(entree, str):
        # Vectorise la nouvelle ligne de texte avec le MEME vocabulaire/IDF
        # qu'a l'entrainement (jamais recalcules ici) — voir
        # _construire_vocabulaire_texte / _vectoriser_texte_tfidf.
        meta = structure.get("vocabulaire_texte") or {}
        vocabulaire = meta.get("vocabulaire") or []
        idf = meta.get("idf") or []
        if not vocabulaire:
            raise EntrainementError(
                _i18n_t("engine/ia_integration/_part_003.py:g:465:17")
            )
        X = _vectoriser_texte_tfidf([entree], vocabulaire, idf)
    elif mode == "texte_slm" and isinstance(entree, str):
        # slm_texte : PAS de TF-IDF (contrairement au mode "texte" ci-dessus)
        # — le modele attend une sequence d'indices de tokens, encodee avec
        # le TextPreprocessor sauvegarde dans le .gy a l'entrainement (voir
        # train_slm_classifier / _extract_slm : preprocessor_params/seq_len
        # dans modele.config). Reutiliser un vocabulaire different (ex:
        # recalcule ici) produirait des indices incoherents avec les poids
        # d'embedding entraines.
        from IA.train.slm import TextPreprocessor
        preprocessor_params = modele.config.get("preprocessor_params")
        seq_len = modele.config.get("seq_len")
        if not preprocessor_params or not seq_len:
            raise EntrainementError(
                _i18n_t("engine/ia_integration/_part_003.py:g:483:17")
            )
        preprocessor = TextPreprocessor.from_params(preprocessor_params)
        X = preprocessor.encode(entree, seq_len).reshape(1, -1)
    else:
        try:
            X = np.asarray(entree, dtype=np.float64)
        except (TypeError, ValueError) as e:
            raise EntrainementError(
                tf("engine/ia_integration/_part_003.py:g:494:17", v1=e)
                + (tf("engine/ia_integration/_part_003.py:g:495:20", v1=mode) if mode in ("image", "audio", "video")
                   else _i18n_t("engine/ia_integration/_part_003.py:g:496:25") if mode in ("texte", "texte_slm")
                   else _i18n_t("engine/ia_integration/_part_003.py:g:497:25"))
            )
        if X.ndim == 1:
            X = X.reshape(1, -1)

    if n_features is not None:
        taille_fournie = int(np.prod(X.shape[1:])) if X.ndim > 1 else X.shape[0]
        if taille_fournie != n_features:
            raise EntrainementError(
                tf("engine/ia_integration/_part_003.py:g:506:17", v1=n_features, v2=structure.get('forme_modele'), v3=structure.get('forme_champ'), v4=taille_fournie)
            )

    sortie = modele.predict(X)
    if isinstance(sortie, dict):
        resultat = {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in sortie.items()}
    elif isinstance(sortie, np.ndarray):
        resultat = {"output": sortie.tolist()}
    else:
        resultat = {"output": sortie}

    if modele_ia.type_modele in TYPES_SANS_CIBLE and "output" in resultat:
        # Groupes : « Groupe 2 » / « Hors groupe (bruit) » ; projection : coordonnees ; anomalies : « Anomalie (score ...) ».
        from IA.train.sklearn_nonsup import libelles_predictions  # import tardif : charge scikit-learn seulement ici
        resultat["predictions_labels"] = libelles_predictions(modele_ia.type_modele, resultat["output"])
        return resultat

    classes_cible = structure.get("classes_cible")
    valeurs_classes = _valeurs_classes_modele(modele_ia.type_modele, modele.config)
    # Cle portant les predictions brutes : "predictions" si un predict_fn
    # dict-like la fournit explicitement, sinon "output" — c'est la cle
    # utilisee par TOUS les predict_fn actuels du module IA (ils retournent
    # un np.ndarray, jamais un dict), donc c'est le cas reel en pratique.
    cle_preds = "predictions" if "predictions" in resultat else (
        "output" if "output" in resultat else None
    )
    if (classes_cible or valeurs_classes) and cle_preds:
        # Decode la sortie du modele vers le label d'origine : position de la colonne de probabilite ->
        # valeur de classe du modele (`valeurs_classes` : classes_ sklearn, unique_classes du MLP ; la
        # colonne i n'est PAS toujours la classe i : une classe absente du jeu d'entrainement decale les
        # colonnes de predict_proba) -> libelle de la cible categorielle (classes_cible) si elle en a un,
        # sinon la valeur numerique de la classe.
        def _indice_classe(p):
            """Sortie brute du modele -> indice de classe. Les modeles renvoient des PROBABILITES
            (softmax k>1 colonnes -> argmax ; sigmoide 1 colonne -> seuil 0.5) ou deja un indice entier :
            avant, int([0.93]) levait TypeError et le « label » renvoye etait la probabilite brute."""
            if isinstance(p, (list, tuple)):
                if len(p) > 1:
                    return int(np.argmax(p))
                p = p[0] if p else 0
                if isinstance(p, (list, tuple)):
                    return _indice_classe(p)
            p = float(p)
            nb_classes = len(classes_cible) if classes_cible else (len(valeurs_classes) if valeurs_classes else 0)
            return int(p > 0.5) if (nb_classes == 2 and 0.0 <= p <= 1.0 and p != int(p)) else int(round(p))

        def _decoder(idx):
            try:
                pos = _indice_classe(idx)
                valeur = valeurs_classes[pos] if (valeurs_classes and 0 <= pos < len(valeurs_classes)) else pos
                if isinstance(valeur, float) and valeur.is_integer():
                    valeur = int(valeur)
                return classes_cible[int(valeur)] if classes_cible else valeur
            except (TypeError, ValueError, IndexError):
                return idx
        preds = resultat[cle_preds]
        if isinstance(preds, list):
            resultat["predictions_labels"] = [_decoder(p) for p in preds]
        else:
            resultat["predictions_labels"] = _decoder(preds)
    return resultat


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
