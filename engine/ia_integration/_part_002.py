from engine.i18n import td
from engine.i18n import t as _i18n_t, tf
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa


def _table_colonnes_vers_classes(table, colonnes_entree=None):
    """Mode "une colonne = une classe" : symetrique a "une table = une
    classe" (voir plus bas dans resoudre_dataset), mais AU SEIN d'une seule
    table plutot qu'entre plusieurs tables. Chaque colonne devient une
    classe distincte, nommee d'apres le nom de la colonne ; les valeurs de
    la colonne (une par ligne de la table) sont les echantillons de cette
    classe, y encode le numero de colonne (0, 1, 2, ...) — la notion X/y de
    l'intelligence supervisee reste donc la meme quel que soit le type de
    donnee.

    3 familles de colonnes acceptees (voir _famille_colonne_classe),
    JAMAIS melangees entre elles dans une meme selection :
    - scalaire (Nombre, Choix unique...) : X final (n_total, 1) — comportement
      historique.
    - tenseur/image/audio (Vecteur/Matrice/Image/Audio) : chaque colonne peut
      etre une classe de son/image comme les autres, a condition que toutes
      produisent la meme forme (meme forme_tenseur configuree) une fois
      aplaties.
    - texte/texte_long : un SEUL vocabulaire TF-IDF construit sur les textes
      de TOUTES les colonnes-classes ensemble (comparabilite entre classes),
      puis chaque colonne vectorisee avec ce vocabulaire partage.

    Retourne (X, y, classes_cible, vocabulaire_texte) ou classes_cible est
    la liste des noms de colonnes dans l'ordre (classes_cible[i] = nom de
    la colonne correspondant a la classe i), pour decoder une prediction a
    l'inference — meme role que _encoder_cible_categorielle mais pour des
    labels de COLONNES plutot que de valeurs d'une colonne cible.
    `vocabulaire_texte` vaut {"colonne": [slugs], "vocabulaire", "idf"} pour
    la famille texte (a persister comme pour _table_vers_matrice), None
    sinon."""
    if colonnes_entree:
        champs_par_slug = {c.slug: c for c in table.champs.all()}
        champs = []
        for slug in colonnes_entree:
            champ = champs_par_slug.get(slug)
            if champ is None:
                raise DatasetError(
                    tf("engine/ia_integration/_part_002.py:g:44:21", v1=slug, v2=table.nom)
                )
            champs.append(champ)
    else:
        champs = list(table.champs.order_by("ordre"))
    if len(champs) < 2:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:51:13", v1=table.nom)
        )

    familles = {_famille_colonne_classe(c) for c in champs}
    if len(familles) > 1:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:59:13", v1=table.nom)
        )
    famille = familles.pop()

    lecteur = _LecteurColonnes(table)
    # classes_cible garde UNE entree par colonne (meme les colonnes vides,
    # sautees ci-dessous) pour que son index reste aligne sur le numero de
    # classe `i` utilise dans y — meme logique que classes_cible = [t.nom
    # for t in tables] pour le mode "une table = une classe" plus bas.
    classes_cible = [c.nom for c in champs]

    def _valeurs_colonne(champ):
        return [v for v in lecteur.valeurs(champ) if v is not None and v != ""]

    if famille == "texte":
        textes_par_colonne = [_valeurs_colonne(c) for c in champs]
        vocabulaire, idf = _construire_vocabulaire_texte(
            [t for textes in textes_par_colonne for t in textes]
        )
        X_classes, y_classes = [], []
        for i, textes in enumerate(textes_par_colonne):
            if not textes:
                continue
            X_col = _vectoriser_texte_tfidf(textes, vocabulaire, idf)
            X_classes.append(X_col)
            y_classes.append(np.full(X_col.shape[0], i, dtype=np.float64))
        if not X_classes:
            raise DatasetError(tf("engine/ia_integration/_part_002.py:g:95:32", v1=table.nom))
        X = np.concatenate(X_classes, axis=0)
        y = np.concatenate(y_classes, axis=0)
        vocabulaire_texte = {"colonne": [c.slug for c in champs], "vocabulaire": vocabulaire, "idf": idf}
        return X, y, classes_cible, vocabulaire_texte

    X_classes, y_classes = [], []
    forme_ref = None
    for i, champ in enumerate(champs):
        valeurs = _valeurs_colonne(champ)
        if not valeurs:
            continue
        X_col = _valeur_colonne_ndarray(champ, valeurs)
        if famille == "scalaire":
            X_col = X_col.reshape(-1, 1)
        elif X_col.ndim > 2:
            X_col = X_col.reshape(X_col.shape[0], -1)
        if forme_ref is None:
            forme_ref = X_col.shape[1:]
        elif X_col.shape[1:] != forme_ref:
            raise DatasetError(
                tf("engine/ia_integration/_part_002.py:g:116:17", v1=champ.nom, v2=i, v3=X_col.shape[1:], v4=forme_ref)
            )
        X_classes.append(X_col)
        y_classes.append(np.full(X_col.shape[0], i, dtype=np.float64))

    if not X_classes:
        raise DatasetError(tf("engine/ia_integration/_part_002.py:g:125:28", v1=table.nom))
    X = np.concatenate(X_classes, axis=0)
    y = np.concatenate(y_classes, axis=0)
    return X, y, classes_cible, None


def resoudre_dataset(tables, colonne_cible="", colonnes_entree=None, colonnes_sortie=None,
                     structure_figee=None, sans_cible=False):
    """Point d'entree principal : tables (liste de TableDynamique, ordre =
    ordre de selection dans l'interface) -> dict {X, y, documents, n_lignes}.

    `colonnes_entree` : liste optionnelle de slugs (premiere table
    uniquement) restreignant X a ces colonnes precises, dans cet ordre —
    ex: une table client "produits" avec colonnes image/nom/prix/stock, ou
    seule la colonne 'image' interesse un CNN. Vide/None = comportement
    historique (toutes les colonnes, sauf cible).

    `colonnes_sortie` : liste optionnelle de slugs (premiere table) pour la
    cible y. Si fournie, remplace `colonne_cible` — 1 slug = cible unique
    (equivalent a colonne_cible mais explicite), plusieurs slugs = cible
    multi-colonnes (empilees en y). Vide/None = comportement historique
    (`colonne_cible`, une seule cible ou aucune).

    `sans_cible` : True pour les modeles sans colonne cible (groupes : TYPES_SANS_CIBLE). Toute cible est ignoree et
    X est toujours la matrice lignes x colonnes choisies (jamais les modes « une colonne/table = une classe »).

    - 1 seule table avec 1 seule colonne de type document (texte/texte_long/
      json), ou colonne de ce type explicitement selectionnee via
      `colonnes_entree` (1 slug) -> documents JSON (le champ 'X'/'y' reste
      None ; c'est a l'appelant de decider comment les utiliser selon le
      type de modele — ex: construire un vocabulaire pour un SLM).
    - Plusieurs tables SANS colonne cible -> mode "une table = une classe"
      (ex: vision/audio) : chaque table fournit les echantillons d'une
      classe, le numero de classe (0, 1, 2, ...) devient y automatiquement.
      Toutes les tables doivent produire des features de meme forme.
      `colonnes_entree` s'applique alors a CHAQUE table (memes slugs).
      `classes_cible` (noms des tables) est retourne pour decoder les
      predictions a l'inference.
    - UNE SEULE table SANS colonne cible ET sans colonne document unique ->
      mode symetrique "une colonne = une classe" : chaque colonne de la
      table (ou celles de `colonnes_entree`) devient une classe distincte,
      ses valeurs (une par ligne) sont les echantillons scalaires de cette
      classe. `classes_cible` (noms des colonnes) est retourne pour
      decoder les predictions a l'inference.
    `structure_figee` (fine-tuning) : `ModeleIA.structure_entree` du modele d'origine. Les recettes d'encodage
    des colonnes (modalites, moyenne/ecart, vocabulaire TF-IDF) et les classes de la cible y sont REUTILISEES
    telles quelles au lieu d'etre recalculees sur les seules lignes nouvelles : sinon les colonnes de X et les
    indices de classe de y ne correspondraient plus aux poids deja appris.

    - Sinon (1 table, ou plusieurs tables AVEC colonne(s) cible) : X/y
      classiques, cible extraite de la PREMIERE table. Si plusieurs tables
      sont fournies dans ce cas, leurs X respectifs sont concatenes
      horizontalement (memes nombre de lignes exige) — utile pour combiner
      des features issues de plusieurs tables alignees ligne a ligne.
    """
    from ..models import ChampDynamique  # import tardif : evite tout cycle avec models.py

    tables = list(tables)
    if not tables:
        raise DatasetError(_i18n_t("engine/ia_integration/_part_002.py:g:175:28"))
    colonnes_entree = list(colonnes_entree) if colonnes_entree else None
    colonnes_sortie = list(colonnes_sortie) if colonnes_sortie else None
    cible_slugs = colonnes_sortie or ([colonne_cible] if colonne_cible else [])
    if sans_cible:
        # Modeles sans colonne cible (groupes...) : X = lignes x colonnes choisies, y = None. Sans ce drapeau, une
        # absence de cible declenche les modes « une colonne = une classe » / « une table = une classe » ci-dessous.
        cible_slugs = []

    premiere = tables[0]
    champs_premiere = list(premiere.champs.all())
    champs_par_slug_premiere = {c.slug: c for c in champs_premiere}
    if len(tables) == 1 and not cible_slugs and not sans_cible:
        # Determine la colonne candidate a la resolution "documents" : celle
        # explicitement selectionnee via colonnes_entree (si une seule), ou
        # l'unique colonne de la table (comportement historique).
        if colonnes_entree and len(colonnes_entree) == 1:
            champ_doc = champs_par_slug_premiere.get(colonnes_entree[0])
        elif not colonnes_entree and len(champs_premiere) == 1:
            champ_doc = champs_premiere[0]
        else:
            champ_doc = None
        if champ_doc is not None and champ_doc.type_champ in ChampDynamique.TYPES_DOCUMENT:
            documents = _table_documents_json(premiere, colonne_slug=champ_doc.slug)
            return {"X": None, "y": None, "documents": documents, "n_lignes": len(documents)}

        # ── Une colonne = une classe (symetrique a "une table = une
        # classe" ci-dessous, mais au sein d'une seule table) ──
        X, y, classes_cible, vocabulaire_texte = _table_colonnes_vers_classes(
            premiere, colonnes_entree=colonnes_entree)
        resultat = {
            "X": X, "y": y, "documents": None, "n_lignes": X.shape[0],
            "num_classes": len(classes_cible), "classes_cible": classes_cible,
        }
        if vocabulaire_texte is not None:
            resultat["vocabulaire_texte"] = vocabulaire_texte
        return resultat

    if len(tables) > 1 and not cible_slugs and not sans_cible:
        # ── Une table = une classe (vision, audio, tout classifieur) ──
        classes_cible = [t.nom for t in tables]
        X_classes, y_classes = [], []
        forme_ref = None
        for i, table in enumerate(tables):
            # vectoriser_texte=False : un vocabulaire TF-IDF different par
            # table-classe n'aurait aucun sens (le mode "une table = une
            # classe" exige des features comparables entre tables) — une
            # colonne texte ici garde donc l'erreur DatasetError explicite.
            X_table, _slugs, _meta_ignoree = _table_vers_matrice(
                table, inclure_slugs=colonnes_entree, vectoriser_texte=False)
            if X_table is None or X_table.shape[0] == 0:
                continue
            X_table = X_table.reshape(X_table.shape[0], -1) if X_table.ndim > 2 else X_table
            if forme_ref is None:
                forme_ref = X_table.shape[1:]
            elif X_table.shape[1:] != forme_ref:
                raise DatasetError(
                    tf("engine/ia_integration/_part_002.py:g:228:21", v1=table.nom, v2=i, v3=X_table.shape[1:], v4=tables[0].nom, v5=forme_ref)
                )
            X_classes.append(X_table)
            y_classes.append(np.full(X_table.shape[0], i, dtype=np.float64))
        if not X_classes:
            raise DatasetError(_i18n_t("engine/ia_integration/_part_002.py:g:235:32"))
        X = np.concatenate(X_classes, axis=0)
        y = np.concatenate(y_classes, axis=0)
        return {
            "X": X, "y": y, "documents": None, "n_lignes": X.shape[0],
            "num_classes": len(tables), "classes_cible": classes_cible,
        }

    X_parts = []
    y = None
    n_lignes = None
    classes_cible = None
    vocabulaire_texte = None
    # Recettes d'encodage par vrai champ (choix, date, texte...) de la table d'entrée —
    # mémorisées dans structure_entree pour que l'inférence redemande ces mêmes champs.
    collecteur_enc = {"specs": [], "complet": True}
    structure_figee = structure_figee or {}
    specs_figes = {sp["slug"]: sp for sp in (structure_figee.get("encodeurs") or []) if isinstance(sp, dict) and sp.get("slug")}
    vocab_fige = structure_figee.get("vocabulaire_texte") or None
    classes_figees = structure_figee.get("classes_cible") or None
    for i, table in enumerate(tables):
        if i == 0:
            # Entree de la premiere table : selection explicite
            # (colonnes_entree) si fournie, sinon toutes les colonnes SAUF
            # la/les colonne(s) cible (comportement historique). Seule cette
            # table (i==0) peut produire un `vocabulaire_texte` : combiner
            # deux vocabulaires TF-IDF issus de tables differentes dans le
            # meme X n'est pas supporte pour l'instant.
            if colonnes_entree:
                entree_slugs = [s for s in colonnes_entree if s not in cible_slugs]
                X_table, _slugs, vocabulaire_texte = _table_vers_matrice(
                    table, inclure_slugs=entree_slugs, collecteur_encodeurs=collecteur_enc,
                    specs_figes=specs_figes, vocab_fige=vocab_fige, standardiser=(len(tables) == 1))
            else:
                X_table, _slugs, vocabulaire_texte = _table_vers_matrice(
                    table, exclure_slug=cible_slugs or None, collecteur_encodeurs=collecteur_enc,
                    specs_figes=specs_figes, vocab_fige=vocab_fige, standardiser=(len(tables) == 1))
        else:
            X_table, _slugs, _meta_ignoree = _table_vers_matrice(table, vectoriser_texte=False)
        if X_table is None:
            continue
        if n_lignes is None:
            n_lignes = X_table.shape[0]
        elif X_table.shape[0] != n_lignes:
            raise DatasetError(
                tf("engine/ia_integration/_part_002.py:g:274:17", v1=table.nom, v2=X_table.shape[0], v3=tables[0].nom, v4=n_lignes)
            )
        X_parts.append(X_table)
        if i == 0 and cible_slugs:
            if len(cible_slugs) == 1:
                # Cible unique : meme logique qu'avant, y compris
                # l'encodage categoriel automatique (ex: "chat"/"chien").
                champ_cible = champs_par_slug_premiere.get(cible_slugs[0])
                if not champ_cible:
                    raise DatasetError(tf("engine/ia_integration/_part_002.py:g:285:40", v1=cible_slugs[0], v2=premiere.nom))
                valeurs_cible = _LecteurColonnes(premiere).valeurs(champ_cible)
                if champ_cible.type_champ in champ_cible.TYPES_CHOIX:
                    y, classes_cible = _encoder_cible_categorielle(champ_cible, valeurs_cible, classes_figees=classes_figees)
                else:
                    y = _valeur_colonne_ndarray(champ_cible, valeurs_cible)
                    classes_cible = None
                if y.ndim == 1:
                    y = y.reshape(-1, 1)
            else:
                # Cible multi-colonnes (ex: regression multi-cible, ou une
                # colonne Matrice unique en sortie) : reutilise
                # _table_vers_matrice avec la selection explicite, pas
                # d'encodage categoriel automatique dans ce cas (rare et
                # ambigu sur plusieurs colonnes a la fois).
                y, _slugs_y, _meta_ignoree = _table_vers_matrice(
                    premiere, inclure_slugs=cible_slugs, vectoriser_texte=False)
                if y is None:
                    raise DatasetError(
                        tf("engine/ia_integration/_part_002.py:g:307:25", v1=cible_slugs, v2=premiere.nom)
                    )
                classes_cible = None
                if y.ndim == 1:
                    y = y.reshape(-1, 1)

    if not X_parts:
        raise DatasetError(_i18n_t("engine/ia_integration/_part_002.py:g:314:28"))
    X = X_parts[0] if len(X_parts) == 1 else np.concatenate(
        [p.reshape(p.shape[0], -1) for p in X_parts], axis=1
    )
    resultat = {"X": X, "y": y, "documents": None, "n_lignes": n_lignes}
    if classes_cible is not None:
        resultat["classes_cible"] = classes_cible
    if vocabulaire_texte is not None:
        resultat["vocabulaire_texte"] = vocabulaire_texte
    elif len(tables) == 1 and collecteur_enc["complet"] and collecteur_enc["specs"]:
        resultat["encodeurs_colonnes"] = collecteur_enc["specs"]
    return resultat


# ─────────────────────────────────────────────────────────────
# Cast des parametres bruts (JSON venant du front, donc str/number/bool)
# vers ce qu'attend reellement la fonction train_xxx (int, float, list...).
# ─────────────────────────────────────────────────────────────
def _caster_parametre(nom, valeur, defaut):
    if valeur is None or valeur == "":
        return None
    if nom in _PARAMS_LISTE_ENTIERS or nom in _PARAMS_TUPLE_ENTIERS:
        if isinstance(valeur, (list, tuple)):
            items = valeur
        else:
            items = [x.strip() for x in str(valeur).split(",") if x.strip()]
        entiers = [int(float(x)) for x in items]
        return tuple(entiers) if nom in _PARAMS_TUPLE_ENTIERS else list(entiers)
    if isinstance(defaut, bool):
        return bool(valeur) if not isinstance(valeur, str) else valeur.lower() in ("1", "true", "vrai", "on")
    if isinstance(defaut, int):
        return int(float(valeur))
    if isinstance(defaut, float):
        return float(valeur)
    return valeur


def _dossier_modele(entreprise, modele_slug):
    dossier = os.path.join(dj_settings.MEDIA_ROOT, "modeles_ia", entreprise.slug, modele_slug)
    os.makedirs(dossier, exist_ok=True)
    return dossier


def _resoudre_dataset_texte_slm(tables, tache, colonnes_entree=None,
                                 colonne_cible="", colonnes_sortie=None):
    """Resout un dataset {sentences, labels, n_lignes} pour le type
    'slm_texte' : une colonne texte/texte_long brute (PAS vectorisee TF-IDF —
    train_slm_classifier fait sa propre tokenisation via TextPreprocessor) +
    une colonne cible dont les valeurs doivent correspondre EXACTEMENT aux
    libelles de classe de la `tache` choisie (ex: 'joie'/'tristesse'/...
    pour tache='emotion') — mapping automatique demande explicitement,
    plutot que de deduire des classes arbitraires depuis les donnees comme
    le fait _encoder_cible_categorielle pour un X/y classique."""
    from ..models import ChampDynamique

    classes_par_tache = _slm_taches_classes()
    classes = classes_par_tache.get(tache)
    if classes is None:
        raise DatasetError(
            f"Tache SLM inconnue : '{tache}'. Taches disponibles : "
            + ", ".join(classes_par_tache.keys()) + "."
        )

    tables = list(tables)
    if not tables:
        raise DatasetError(_i18n_t("engine/ia_integration/_part_002.py:g:379:28"))
    premiere = tables[0]
    champs_par_slug = {c.slug: c for c in premiere.champs.all()}

    cible_slug = (colonnes_sortie or [None])[0] or colonne_cible or None
    if not cible_slug:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:386:13", v1=tache)
            + ", ".join(classes) + "."
        )
    champ_cible = champs_par_slug.get(cible_slug)
    if champ_cible is None:
        raise DatasetError(tf("engine/ia_integration/_part_002.py:g:392:28", v1=cible_slug, v2=premiere.nom))

    entree_slugs = [s for s in (colonnes_entree or []) if s != cible_slug]
    if entree_slugs:
        champ_texte = champs_par_slug.get(entree_slugs[0])
        if champ_texte is None:
            raise DatasetError(tf("engine/ia_integration/_part_002.py:g:398:32", v1=entree_slugs[0], v2=premiere.nom))
    else:
        candidats = [c for c in champs_par_slug.values()
                     if c.slug != cible_slug and c.type_champ in ChampDynamique.TYPES_DOCUMENT]
        if len(candidats) != 1:
            raise DatasetError(
                _i18n_t("engine/ia_integration/_part_002.py:g:404:17")
            )
        champ_texte = candidats[0]

    index_classe = {c: i for i, c in enumerate(classes)}
    sentences, labels = [], []
    valeurs_invalides = set()
    lecteur_paires = _LecteurColonnes(premiere)
    for texte, cible in zip(lecteur_paires.valeurs(champ_texte), lecteur_paires.valeurs(champ_cible)):
        if not texte or cible is None or str(cible) == "":
            continue
        cible_str = str(cible).strip()
        if cible_str not in index_classe:
            valeurs_invalides.add(cible_str)
            continue
        sentences.append(str(texte))
        labels.append(index_classe[cible_str])

    if valeurs_invalides:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:428:13", v1=champ_cible.nom, v2=tache)
            + ", ".join(sorted(valeurs_invalides)[:10])
            + tf("engine/ia_integration/_part_002.py:classes_attendues", v1=", ".join(classes))
        )
    if not sentences:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:435:13", v1=champ_texte.nom, v2=champ_cible.nom)
            + ", ".join(classes) + ")."
        )
    return {
        "X": None, "y": None, "documents": None, "n_lignes": len(sentences),
        "sentences": sentences, "labels": labels, "classes_cible": classes,
    }


def _valider_compatibilite_modele(type_modele, sig, dataset):
    """Verifications de bon sens entre le dataset resolu et le type de
    modele choisi, AVANT de lancer l'entrainement — pour echouer avec un
    message clair plutot qu'une trace numpy/Python illisible ou (pire) un
    entrainement qui "reussit" sur des donnees vides/incoherentes."""
    accepte = set(sig.parameters.keys())
    supervise = "X" in accepte and "y" in accepte

    if _est_supervise_texte(accepte):
        # slm_texte : deja entierement valide et construit par
        # _resoudre_dataset_texte_slm (mapping de labels, colonnes
        # manquantes, valeurs invalides...) — ne reste qu'une verification
        # de coherence de base ici.
        sentences = dataset.get("sentences") or []
        labels = dataset.get("labels") or []
        if not sentences or not labels:
            raise DatasetError(
                _i18n_t("engine/ia_integration/_part_002.py:g:462:17")
            )
        if len(sentences) != len(labels):
            raise DatasetError(
                tf("engine/ia_integration/_part_002.py:g:466:17", v1=len(sentences), v2=len(labels))
            )
        return

    if type_modele in TYPES_SANS_CIBLE:
        # Groupes : une matrice 2D de nombres suffit, aucune cible attendue (y reste None).
        if dataset["X"] is None:
            raise DatasetError(_i18n_t("engine/ia_integration/_part_002.py:g:480:13"))
        if dataset["X"].ndim != 2:
            raise DatasetError(tf("ia.nonsup.err_x_2d", ndim=dataset["X"].ndim))
        return

    if not supervise:
        # Modele generatif/non supervise (gan, ldm, slm) : s'entraine sur
        # ses propres donnees (synthetiques ou construites depuis les
        # hyperparametres), sans dataset tabulaire. Aucune table source
        # n'est requise ni utilisee pour ces types — voir lancer_entrainement
        # qui n'appelle meme pas resoudre_dataset dans ce cas.
        return

    if dataset["X"] is None and dataset["documents"] is None:
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_002.py:g:480:13")
        )

    if dataset["X"] is not None and dataset["y"] is None \
            and dataset.get("num_classes") is None:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:486:13", v1=type_modele)
        )

    if dataset["X"] is not None and dataset["y"] is not None:
        if dataset["X"].shape[0] != dataset["y"].shape[0]:
            raise DatasetError(
                tf("engine/ia_integration/_part_002.py:g:495:17", v1=dataset['X'].shape[0], v2=dataset['y'].shape[0])
            )

    if dataset["X"] is None:
        return
    ndim = dataset["X"].ndim
    forme = dataset["X"].shape

    # Forme EXACTE requise (pas juste "au moins N dimensions") : ces types
    # font un unpacking direct de X.shape dans le module IA (ex: train_mlp
    # fait `n_samples, n_features = X.shape`) — une forme differente ne
    # produit pas une erreur claire mais un ValueError/IndexError numpy
    # illisible en plein entrainement.
    forme_exacte = {
        "mlp": (2, td("engine/ia_integration/_part_002.py:g:491:20")),
        "rnn": (3, td("engine/ia_integration/_part_002.py:g:496:20")),
        "transformer": (2, td("engine/ia_integration/_part_002.py:g:500:28")),
    }
    if type_modele in forme_exacte:
        attendu, explication = forme_exacte[type_modele]
        if ndim != attendu:
            raise DatasetError(
                tf("engine/ia_integration/_part_002.py:g:528:17", v1=type_modele, v2=explication, v3=ndim, v4=forme)
            )

    # Forme MINIMALE (au moins N dimensions, la forme exacte au-dela est
    # libre — un CNN peut prendre une grille 2D ou un volume 3D+canaux).
    forme_min = {"cnn": 2, "image_classifier": 2, "speech_classifier": 2}
    if type_modele in forme_min and ndim < forme_min[type_modele]:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:537:13", v1=type_modele)
        )


def _valider_tokens_transformer(X, vocab_size):
    """Le transformer fait un lookup d'embedding par indice de token : une
    valeur non entiere ou hors de [0, vocab_size) plante silencieusement
    (embedding sur un mauvais indice) ou avec une erreur numpy opaque
    plutot qu'un message clair. Verifie AVANT l'entrainement."""
    arrondi = np.round(X)
    if not np.allclose(X, arrondi, atol=1e-6):
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_002.py:g:552:13")
        )
    mini, maxi = float(X.min()), float(X.max())
    if mini < 0 or maxi >= vocab_size:
        raise DatasetError(
            tf("engine/ia_integration/_part_002.py:g:560:13", v1=int(vocab_size) - 1, v2=mini, v3=maxi)
        )


def _detecter_structure_entree(tables, colonne_cible, dataset, X_final,
                                colonnes_entree=None, colonnes_sortie=None):
    """Determine, a partir des tables source et du dataset resolu, la
    structure d'entree exacte que l'inference devra respecter : mode
    (image/audio/tenseur/numerique/documents), forme du champ source (pour
    pouvoir decoder une NOUVELLE image/audio a l'inference exactement comme
    a l'entrainement) et forme finale attendue par le modele entraine."""
    if dataset.get("sentences") is not None:
        # slm_texte : encodage (tokens d'indices via TextPreprocessor) fait
        # a la volee a l'inference a partir de preprocessor_params/seq_len
        # deja persistes dans le .gy (config du modele, cf _extract_slm) —
        # rien de plus a memoriser ici que le mode et les classes pour
        # decoder la prediction (index -> libelle d'origine).
        structure = {"mode": "texte_slm", "forme_champ": None,
                     "forme_modele": None, "n_features": None}
        if dataset.get("classes_cible") is not None:
            structure["classes_cible"] = dataset["classes_cible"]
        return structure

    if dataset.get("documents") is not None:
        return {"mode": "documents", "forme_champ": None, "forme_modele": None, "n_features": None}

    if dataset.get("vocabulaire_texte") is not None:
        # Colonne texte/texte_long vectorisee en TF-IDF (cf. MLP) : le
        # vocabulaire/IDF construits a l'entrainement DOIVENT etre persistes
        # ici pour que lancer_inference vectorise une nouvelle ligne de
        # texte a l'identique (voir _construire_vocabulaire_texte).
        meta = dataset["vocabulaire_texte"]
        forme_modele = list(X_final.shape[1:]) if X_final is not None else [len(meta["vocabulaire"])]
        n_features = (int(np.prod(X_final.shape[1:])) if X_final is not None
                      else len(meta["vocabulaire"]))
        structure = {
            "mode": "texte", "forme_champ": None,
            "forme_modele": forme_modele, "n_features": n_features,
            "vocabulaire_texte": {"vocabulaire": meta["vocabulaire"], "idf": meta["idf"]},
        }
        if dataset.get("classes_cible") is not None:
            structure["classes_cible"] = dataset["classes_cible"]
        return structure

    tables = list(tables)
    champ_reference = None
    mode = "numerique"
    if tables:
        premiere = tables[0]
        cible_slugs = list(colonnes_sortie) if colonnes_sortie else ([colonne_cible] if colonne_cible else [])
        if colonnes_entree:
            champs_par_slug = {c.slug: c for c in premiere.champs.all()}
            champs = [champs_par_slug[s] for s in colonnes_entree
                      if s not in cible_slugs and s in champs_par_slug]
        else:
            champs = list(premiere.champs.exclude(slug__in=cible_slugs) if cible_slugs else premiere.champs.all())
        champs = sorted(champs, key=lambda c: c.ordre)
        for c in champs:
            if c.type_champ in c.TYPES_IMAGE_REFERENCE:
                mode, champ_reference = "image", c
                break
            if c.type_champ in c.TYPES_AUDIO_REFERENCE:
                mode, champ_reference = "audio", c
                break
            if c.type_champ in c.TYPES_VIDEO_REFERENCE:
                mode, champ_reference = "video", c
                break
            if c.type_champ in c.TYPES_TENSEUR:
                mode, champ_reference = "tenseur", c
                break

    forme_champ = list(champ_reference.forme_tenseur) if (champ_reference and champ_reference.forme_tenseur) else None
    forme_modele = list(X_final.shape[1:]) if X_final is not None else None
    n_features = int(np.prod(X_final.shape[1:])) if X_final is not None else None
    structure = {"mode": mode, "forme_champ": forme_champ, "forme_modele": forme_modele, "n_features": n_features}
    if mode in ("image", "audio") and _VECTORISATION.get():
        # Le modele a appris sur des vecteurs de caracteristiques : l'inference doit produire les memes (meme recette,
        # meme version). Absente = modele ancien entraine sur des donnees brutes, lu comme avant.
        structure["vectorisation"] = _vecto.descripteur(mode)
    if mode == "numerique" and dataset.get("encodeurs_colonnes"):
        # Saisie par vrais champs : l'inférence reçoit {slug: valeur} et rejoue ces recettes.
        structure["encodeurs"] = dataset["encodeurs_colonnes"]
    if dataset.get("classes_cible") is not None:
        # Cible categorielle encodee a l'entrainement (ex: "chat"/"chien" ->
        # 0/1) : on memorise les labels d'origine pour pouvoir decoder la
        # prediction (index de classe) a l'inference.
        structure["classes_cible"] = dataset["classes_cible"]
    return structure


class _CallbackProgression:
    """Callback IA.callbacks.Callback : ecrit l'avancement (epoque, %,
    metriques) dans ModeleIA.progression via le Journal, et trace les
    evenements utiles (1 ligne ~tous les 10 %). Detecte aussi les deux
    echecs SILENCIEUX classiques : perte NaN/infinie (divergence -> arret
    immediat, `self.diverge`) et perte qui ne baisse pas (avertissement).
    Les modeles sklearn (un seul fit, pas d'epoques) ne produisent qu'un
    etat "en cours" + battement de coeur, sans fausse barre d'epoques.
    Throttle : pas plus d'une ecriture DB toutes les `throttle_seconds`."""

    def __init__(self, journal, throttle_seconds=0.3):
        self.journal = journal
        self.throttle_seconds = throttle_seconds
        self._derniere_ecriture = 0.0
        self.iteratif = True
        self.diverge = None          # numero d'epoque (1-base) de la divergence
        self.perte_debut = self.perte_fin = None
        self.appels = 0

    def on_train_begin(self, trainer):
        self.iteratif = not str(getattr(trainer, "model_type", "")).startswith("sklearn_")
        if self.iteratif:
            self.journal.log(f"Entrainement lance ({trainer.epochs} epoque(s) prevue(s)).",
                             phase="entrainement", epoch=0, epochs_total=trainer.epochs,
                             pourcentage=0.0, metriques={})
        else:
            self.journal.log(_i18n_t("engine/ia_integration/_part_002.py:g:677:30"),
                             phase="entrainement", epoch=0, epochs_total=1, pourcentage=0.0, metriques={})

    def on_epoch_begin(self, epoch, trainer):
        pass

    def on_epoch_end(self, epoch, metrics, trainer):
        if not self.iteratif:
            return
        self.appels += 1
        mets = {k: (round(float(v), 6) if isinstance(v, (int, float)) else v)
                for k, v in (metrics or {}).items()}
        perte = (metrics or {}).get("loss")
        if isinstance(perte, (int, float)):
            if not np.isfinite(perte):
                self.diverge = epoch + 1
                self.journal.log(tf("engine/ia_integration/_part_002.py:g:693:34", v1=perte, v2=epoch + 1), "err")
                trainer.stop_training = True
                return
            self.perte_debut = perte if self.perte_debut is None else self.perte_debut
            self.perte_fin = perte
        total = trainer.epochs or 1
        derniere = (epoch == total - 1)
        pas = max(1, total // 10)
        if derniere or (epoch + 1) % pas == 0:
            txt = "  ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}" for k, v in mets.items())
            self.journal.log(f"Epoque {epoch + 1}/{total}" + (f" — {txt}" if txt else ""),
                             epoch=epoch + 1, epochs_total=total,
                             pourcentage=round(100 * (epoch + 1) / total, 1), metriques=mets)
            self._derniere_ecriture = time.time()
            return
        maintenant = time.time()
        if (maintenant - self._derniere_ecriture) < self.throttle_seconds:
            return
        self._derniere_ecriture = maintenant
        self.journal.maj_epoque(epoch=epoch + 1, epochs_total=total,
                                pourcentage=round(100 * (epoch + 1) / total, 1), metriques=mets)

    def on_train_end(self, trainer):
        if (self.iteratif and self.diverge is None and not trainer.stop_training and self.appels >= 3
                and self.perte_debut and self.perte_fin is not None
                and self.perte_fin >= self.perte_debut * 0.999):
            self.journal.log(tf("engine/ia_integration/_part_002.py:g:720:30", v1=self.perte_debut, v2=self.perte_fin), "warn")


class _CallbackAnnulation:
    """Callback IA.callbacks.Callback : verifie a chaque epoque si une
    annulation a ete demandee (ModeleIA.demande_annulation, pose par
    views.api_ia_annuler_entrainement depuis une requete HTTP separee,
    pendant que l'entrainement tourne dans son propre thread de fond).

    Des que le drapeau est detecte, pose `trainer.stop_training = True` :
    chaque train_xxx (IA/train/*.py) verifie ce retour a chaque epoque et
    interrompt proprement la boucle (pas d'exception, poids/etat courants
    conserves). L'attribut `self.annule` permet ensuite a
    lancer_entrainement de distinguer une fin normale d'une annulation,
    puisque Trainer.train() ne fait pas cette distinction lui-meme.

    Pas de throttle ici (contrairement a _CallbackProgression) : la lecture
    est une simple requete .values_list sur un champ indexe-like (booleen),
    negligeable comparee au cout d'une epoque d'entrainement.
    """

    def __init__(self, modele_ia_id):
        self.modele_ia_id = modele_ia_id
        self.annule = False

    def on_train_begin(self, trainer):
        pass

    def on_epoch_begin(self, epoch, trainer):
        pass

    def on_epoch_end(self, epoch, metrics, trainer):
        from ..models import ModeleIA
        demande = ModeleIA.objects.filter(pk=self.modele_ia_id).values_list(
            "demande_annulation", flat=True
        ).first()
        if demande:
            self.annule = True
            trainer.stop_training = True

    def on_train_end(self, trainer):
        pass


class _CallbackPause:
    """Met l'entrainement en attente a la fin de l'epoque courante tant que
    ModeleIA.demande_pause est vrai (poses par api_ia_pause / api_ia_reprendre).

    Le worker reste vivant pendant la pause : le battement de coeur du Journal
    continue, donc l'interface ne le croit pas mort. Une annulation demandee
    pendant la pause est honoree (on sort de l'attente et on arrete le Trainer).
    A placer APRES _CallbackAnnulation dans la liste des callbacks.
    """

    def __init__(self, modele_ia_id, journal, annulation=None, periode=1.0):
        self.modele_ia_id = modele_ia_id
        self.journal = journal
        self.annulation = annulation  # _CallbackAnnulation : pour marquer l'arret comme une annulation
        self.periode = periode

    def on_train_begin(self, trainer):
        pass

    def on_epoch_begin(self, epoch, trainer):
        pass

    def _drapeaux(self):
        from ..models import ModeleIA
        return ModeleIA.objects.filter(pk=self.modele_ia_id).values_list(
            "demande_pause", "demande_annulation").first() or (False, False)

    def on_epoch_end(self, epoch, metrics, trainer):
        pause, annule = self._drapeaux()
        if not pause or annule:
            return
        self.journal.log(tf("engine/ia_integration/_part_002.py:g:797:26", v1=epoch + 1), "warn", phase="pause")
        while True:
            time.sleep(self.periode)
            pause, annule = self._drapeaux()
            if annule:
                if self.annulation is not None:
                    self.annulation.annule = True
                trainer.stop_training = True
                return
            if not pause:
                break
        self.journal.log("Entrainement repris.", phase="entrainement")

    def on_train_end(self, trainer):
        pass




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (__init__.py,
# parties suivantes). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
