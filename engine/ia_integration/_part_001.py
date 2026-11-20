from engine.i18n import t as _i18n_t, tf
from engine.i18n import t as _i18n_t, tf as _i18n_tf, td as _i18n_td  # noqa: F401
from ._header import *  # noqa
from engine.i18n import t as _gt  # noqa: E402

def _tokeniser_texte(texte):
    """Tokenisation volontairement basique (minuscules, mots Unicode de 2+
    caracteres, pas de stemming/lemmatisation, pas de stop-words) : reste
    100% numpy/stdlib, coherent avec le reste du module IA qui n'a aucune
    dependance NLP lourde."""
    if not texte:
        return []
    return [t for t in _RE_TOKEN_TEXTE.findall(str(texte).lower()) if len(t) >= 2]


def _construire_vocabulaire_texte(textes, taille_max=_TAILLE_VOCAB_TEXTE_DEFAUT):
    """Construit, UNE SEULE FOIS a l'entrainement, un vocabulaire fixe (les
    `taille_max` termes les plus frequents du corpus — frequence
    DOCUMENTAIRE, pas totale — tries par frequence decroissante puis
    alphabetiquement a egalite pour un resultat deterministe) et leur IDF
    (Inverse Document Frequency lissee, comme scikit-learn :
    idf = ln((1+n)/(1+df)) + 1).

    Ce vocabulaire fait partie du modele entraine au meme titre que ses
    poids : il est persiste (ModeleIA.structure_entree) et reutilise TEL
    QUEL a l'inference, jamais recalcule sur une seule nouvelle ligne (ce
    qui n'aurait aucun sens pour une IDF)."""
    documents_tokens = [_tokeniser_texte(t) for t in textes]
    if not documents_tokens:
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_001.py:g:28:13")
        )
    df = {}
    for tokens in documents_tokens:
        for terme in set(tokens):
            df[terme] = df.get(terme, 0) + 1
    if not df:
        raise DatasetError(
            tf("engine/ia_integration/_part_001.py:g:38:13", v1=len(documents_tokens))
        )
    termes_tries = sorted(df.keys(), key=lambda t: (-df[t], t))
    vocabulaire = termes_tries[:taille_max]
    n = len(documents_tokens)
    idf = [float(np.log((1.0 + n) / (1.0 + df[t])) + 1.0) for t in vocabulaire]
    return vocabulaire, idf


def _vectoriser_texte_tfidf(textes, vocabulaire, idf):
    """Transforme une liste de textes en matrice TF-IDF (n, len(vocabulaire))
    en reutilisant un vocabulaire/IDF DEJA construits (a l'entrainement,
    via _construire_vocabulaire_texte) — c'est ce qui rend l'inference sur
    une seule nouvelle ligne de texte coherente avec l'entrainement : le
    vocabulaire ne bouge jamais apres l'entrainement, exactement comme les
    poids du MLP."""
    index = {t: i for i, t in enumerate(vocabulaire)}
    idf_arr = np.asarray(idf, dtype=np.float64)
    lignes = []
    for texte in textes:
        tokens = _tokeniser_texte(texte)
        vecteur = np.zeros(len(vocabulaire), dtype=np.float64)
        if tokens:
            for t in tokens:
                i = index.get(t)
                if i is not None:
                    vecteur[i] += 1.0
            vecteur /= len(tokens)  # frequence de terme (TF)
        vecteur *= idf_arr
        norme = np.linalg.norm(vecteur)
        if norme > 0:
            vecteur /= norme
        lignes.append(vecteur)
    if not lignes:
        return np.zeros((0, len(vocabulaire)), dtype=np.float64)
    return np.stack(lignes)


# ─────────────────────────────────────────────────────────────
# Import paresseux du module IA (evite tout cout au demarrage Django si la
# page d'entrainement n'est jamais visitee).
# ─────────────────────────────────────────────────────────────
def _trainer_module():
    from IA import trainer as T
    T._register_all()
    return T


def _slm_taches_classes():
    from IA.train.slm import SLM_TACHES
    return {tache: classes for tache, (classes, _sentences, _labels) in SLM_TACHES.items()}

# Parametres de plomberie interne, jamais exposes dans le formulaire :
# X/y/dataset viennent de la resolution des tables ; save_path est calcule
# par le serveur ; on_epoch est un hook Python interne au Trainer.
def _widget_pour(nom, defaut, code=None):
    if code == "slm_texte" and nom == "tache":
        return "choix"
    if nom in _PARAMS_LISTE_ENTIERS or nom in _PARAMS_TUPLE_ENTIERS:
        return "liste_entiers"
    if isinstance(defaut, bool):
        return "booleen"
    if isinstance(defaut, int):
        return "entier"
    if isinstance(defaut, float):
        return "decimal"
    if defaut is None:
        return "texte"
    return "texte"


def _est_supervise_texte(accepte):
    """Signature 'sentences'/'labels' (ex: slm_texte) : supervise au meme
    titre que X/y, mais sur un dataset texte brut + colonne cible
    categorielle plutot qu'une matrice numerique — voir
    _resoudre_dataset_texte_slm."""
    return "sentences" in accepte and "labels" in accepte


# Cache de types_modeles_disponibles() : l'introspection de toutes les fonctions train_xxx est refaite seulement quand
# l'ensemble des types enregistres ou le dossier des scripts ajoutes change (algorithme installe depuis cette page, la
# commande ajouter_modele_ia ou un autre processus). Sans cache, chaque appel de /api/ia/types-modeles/ bloquait le thread
# synchrone d'asgiref ; au premier appel, l'import de IA.trainer (torch, sklearn...) depassait 10 s et Daphne tuait la requete.
_CACHE_TYPES_MODELES = {"signature": None, "resultat": None}


def _signature_types_modeles(T):
    import os
    try:
        from IA.plugins_modeles import DOSSIER
        mtime = os.stat(DOSSIER).st_mtime_ns if os.path.isdir(DOSSIER) else 0
    except Exception:
        mtime = 0
    return (tuple(sorted(T._REGISTRY)), mtime)


def types_modeles_disponibles(forcer=False):
    """Introspection des fonctions train_xxx du module IA : retourne, pour
    chaque type de modele enregistre, la liste de TOUS ses hyperparametres
    (nom, valeur par defaut, widget suggere) — rien n'est code en dur, un
    nouveau parametre ajoute cote IA/train/*.py apparait automatiquement.
    Le resultat est mis en cache (voir _CACHE_TYPES_MODELES) ; forcer=True le recalcule."""
    T = _trainer_module()
    signature = _signature_types_modeles(T)
    if not forcer and _CACHE_TYPES_MODELES["signature"] == signature and _CACHE_TYPES_MODELES["resultat"] is not None:
        return _CACHE_TYPES_MODELES["resultat"]
    resultat = _calculer_types_modeles(T)
    _CACHE_TYPES_MODELES["signature"] = signature
    _CACHE_TYPES_MODELES["resultat"] = resultat
    return resultat


def prechauffer_types_modeles():
    """Charge le module IA et remplit le cache (appele en arriere-plan au demarrage, voir engine/apps.py)."""
    try:
        types_modeles_disponibles()
    except Exception:
        import logging
        logging.getLogger("engine").exception(_gt("rg5.msg.074"))


def _calculer_types_modeles(T):
    from IA.plugins_modeles import profil_plugin
    resultat = []
    for code, entry in T._REGISTRY.items():
        sig = inspect.signature(entry["train_fn"])
        parametres = []
        for nom, p in sig.parameters.items():
            if nom in _PARAMS_MASQUES or p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                continue
            defaut = None if p.default is inspect._empty else p.default
            param = {
                "nom": nom,
                "defaut": defaut,
                "widget": _widget_pour(nom, defaut, code),
            }
            if code == "slm_texte" and nom == "tache":
                param["options"] = list(_slm_taches_classes().keys())
            parametres.append(param)
        accepte = set(sig.parameters.keys())
        resultat.append({
            "code": code,
            "libelle": LIBELLES_TYPES.get(code, code),
            "parametres": parametres,
            "supervise": ("X" in accepte and "y" in accepte) or _est_supervise_texte(accepte),
            # Modele sans colonne cible (groupes...) : utilise les colonnes d'une table mais aucune cible n'est demandee.
            "sans_cible": code in TYPES_SANS_CIBLE,
            # Images / sons : ce type apprend sur des vecteurs de caracteristiques (case a cocher dans la page d'entrainement).
            "vectorisable": code in TYPES_MODELES_VECTORISES,
            "famille": famille_sans_cible(code),   # groupes | projection | anomalies | None (sert a adapter l'affichage)
            "types_entree_compatibles": list(types_entree_compatibles(code)),
            "types_sortie_compatibles": list(types_sortie_compatibles(code)),
            "contraintes": descriptions_contraintes(code),
            "profil": profil_plugin(code),   # fiche des modèles ajoutés (None pour les modèles d'origine)
        })
    return resultat


# ─────────────────────────────────────────────────────────────
# Compatibilite type de modele <-> types de colonne utilisables en entree.
# Sert a deux endroits : cote client (ia.html, via l'API types-modeles) pour
# ne rendre COCHABLES que les colonnes qui conviennent au type de modele
# choisi (le client n'a pas a deviner ce qu'un CNN attend), et cote serveur
# (api_ia_modeles/api_ia_modele_detail) pour refuser explicitement une
# combinaison incompatible plutot que de laisser echouer l'entrainement
# plus tard avec une erreur numpy peu comprehensible — voir
# _valeur_colonne_ndarray, qui est la source de verite technique de cette
# table (chaque type liste ici est un type que cette fonction sait convertir
# sans lever DatasetError). Exception : texte/texte_long (mlp uniquement),
# converti par _table_vers_matrice (vectorisation TF-IDF, voir
# _construire_vocabulaire_texte) et non par _valeur_colonne_ndarray — cette
# conversion n'est valide que si c'est l'UNIQUE colonne d'entree.
# ─────────────────────────────────────────────────────────────
def types_entree_compatibles(type_modele):
    """Types de champ (`ChampDynamique.type_champ`) utilisables comme colonne
    d'entree (X) pour un type de modele donne. Tuple vide = aucune
    restriction connue (type de modele absent de la table)."""
    return TYPES_ENTREE_COMPATIBLES.get(type_modele, ())


def valider_colonnes_entree(type_modele, tables, colonnes_entree):
    """Verifie que chaque slug de `colonnes_entree` correspond, sur la
    PREMIERE table fournie, a un champ dont le type est compatible avec
    `type_modele`. Leve DatasetError avec un message actionnable sinon.
    Ne fait rien si `colonnes_entree` est vide (comportement historique :
    toutes les colonnes) ou si `type_modele` n'a pas de restriction connue —
    la validation "colonne reellement convertible" reste de toute facon
    appliquee plus tard par _valeur_colonne_ndarray a l'entrainement."""
    autorises = types_entree_compatibles(type_modele)
    if not colonnes_entree or not autorises:
        return
    tables = list(tables)
    if not tables:
        return
    premiere = tables[0]
    champs_par_slug = {c.slug: c for c in premiere.champs.all()}
    for slug in colonnes_entree:
        champ = champs_par_slug.get(slug)
        if champ is None:
            continue  # signale ailleurs (slug introuvable) ; pas notre role ici
        if champ.type_champ not in autorises:
            libelles = dict(champ.TYPE_CHOICES)
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:201:17", v1=champ.nom, v2=libelles.get(champ.type_champ, champ.type_champ), v3=LIBELLES_TYPES.get(type_modele, type_modele))
                + ", ".join(libelles.get(t, t) for t in autorises) + "."
            )


# ─────────────────────────────────────────────────────────────
# Compatibilite type de modele <-> types de colonne utilisables en cible
# (y). Symetrique a TYPES_ENTREE_COMPATIBLES ci-dessus, memes deux usages
# (client : ia.html/renderColonnesCible ; serveur : valider_colonnes_sortie).
# Source de verite technique : resoudre_dataset, branche "cible unique" —
# _encoder_cible_categorielle pour les colonnes TYPES_CHOIX (choix_unique/
# choix_multiple), _valeur_colonne_ndarray pour le reste (memes types
# numeriques/tenseurs que TYPES_ENTREE_COMPATIBLES). Pas de texte
# vectorisable ici : contrairement a X, une colonne cible texte libre
# n'a pas de sens (ni encodage categoriel, ni TF-IDF cote y).
# ─────────────────────────────────────────────────────────────
def types_sortie_compatibles(type_modele):
    """Types de champ (`ChampDynamique.type_champ`) utilisables comme
    colonne cible (y) pour un type de modele donne. Tuple vide = aucune
    restriction connue (type de modele absent de la table)."""
    return TYPES_SORTIE_COMPATIBLES.get(type_modele, ())


def valider_colonnes_sortie(type_modele, tables, colonnes_sortie):
    """Symetrique a `valider_colonnes_entree` mais pour la cible (y) :
    verifie que chaque slug de `colonnes_sortie` correspond, sur la
    PREMIERE table fournie, a un champ dont le type est compatible avec
    `type_modele`. Leve DatasetError avec un message actionnable sinon.
    Ne fait rien si `colonnes_sortie` est vide (aucune cible explicite —
    ex: mode "une table/colonne = une classe") ou si `type_modele` n'a pas
    de restriction connue."""
    autorises = types_sortie_compatibles(type_modele)
    if not colonnes_sortie or not autorises:
        return
    tables = list(tables)
    if not tables:
        return
    premiere = tables[0]
    champs_par_slug = {c.slug: c for c in premiere.champs.all()}
    for slug in colonnes_sortie:
        champ = champs_par_slug.get(slug)
        if champ is None:
            continue  # signale ailleurs (slug introuvable) ; pas notre role ici
        if champ.type_champ not in autorises:
            libelles = dict(champ.TYPE_CHOICES)
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:249:17", v1=champ.nom, v2=libelles.get(champ.type_champ, champ.type_champ), v3=LIBELLES_TYPES.get(type_modele, type_modele))
                + ", ".join(libelles.get(t, t) for t in autorises) + "."
            )


# ─────────────────────────────────────────────────────────────
# Resolution dataset : TableDynamique(s) -> ndarray
# ─────────────────────────────────────────────────────────────
def _charger_pixels_image(source, forme):
    """Resout UNE valeur de champ image/url en ndarray de pixels normalises
    [0, 1], redimensionnee selon `forme` :

    - `forme` a 2 dims (H, W)    -> image convertie en niveaux de gris
    - `forme` a 3 dims (H, W, C) -> image convertie en RGB (C doit valoir 3)
    - `forme` absente/invalide   -> fallback `_FORME_IMAGE_DEFAUT` (niveaux de gris)

    `source` peut etre une URL http(s) (telechargee) ou un chemin relatif
    sous MEDIA_ROOT (fichier uploade via le champ "Image"/"Fichier").
    """
    image = _ouvrir_image_source(source)
    return _pixels_normalises(image, forme)


def _ouvrir_image_source(source):
    """URL http(s) ou chemin sous MEDIA_ROOT -> image PIL chargee (memes controles de securite qu'avant)."""
    if _PILImage is None:
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_001.py:g:272:13")
        )
    if not source or not str(source).strip():
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_001.py:g:278:13")
        )
    source = str(source).strip()
    try:
        from ..securite import chemin_media_sur, url_externe_sure
        if source.startswith(("http://", "https://")):
            if not url_externe_sure(source):
                raise DatasetError(tf("engine/ia_integration/_part_001.py:g:286:36", v1=source))
            reponse = requests.get(source, timeout=15, allow_redirects=False)
            reponse.raise_for_status()
            donnees = reponse.content
        else:
            chemin = chemin_media_sur(source)
            if chemin is None:
                raise DatasetError(tf("engine/ia_integration/_part_001.py:g:293:36", v1=source))
            with open(chemin, "rb") as f:
                donnees = f.read()
        image = _PILImage.open(io.BytesIO(donnees))
        image.load()
    except DatasetError:
        raise
    except Exception as e:
        raise DatasetError(tf("engine/ia_integration/_part_001.py:g:301:28", v1=source, v2=e))
    return image


def _vectoriser_image(source):
    """UNE valeur de champ image/url -> vecteur de caracteristiques (contours, ombres, couleurs), voir vectorisation_media."""
    image = _ouvrir_image_source(source)
    try:
        return _vecto.vecteur_image(image)
    except _vecto.VectorisationError as e:
        raise DatasetError(tf("ia.vecto.err_image", v1=str(source).strip(), v2=e))


def _pixels_normalises(image, forme):
    """Image PIL -> pixels normalises [0, 1] selon `forme` (voir _charger_pixels_image ; chemin des modeles bruts)."""
    if forme and len(forme) == 3:
        hauteur, largeur, canaux = forme
        if canaux != 3:
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:307:17", v1=forme)
            )
        image = image.convert("RGB").resize((largeur, hauteur))
    elif forme and len(forme) == 2:
        hauteur, largeur = forme
        image = image.convert("L").resize((largeur, hauteur))
    else:
        hauteur, largeur = _FORME_IMAGE_DEFAUT
        image = image.convert("L").resize((largeur, hauteur))

    return np.asarray(image, dtype=np.float64) / 255.0


def _charger_features_audio(source, forme):
    """Resout UNE valeur de champ 'son' en un vecteur de features numeriques.

    Formats supportes :
    - Si pydub est installe (et ffmpeg present sur le systeme) : MP3, OGG,
      M4A, WAV, ... (tout ce que ffmpeg sait decoder).
    - Sinon, repli sur le module stdlib `wave` : WAV (PCM) uniquement.

    La feature extraite est une version simplifiee : l'onde brute
    (normalisee entre -1 et 1) est decoupee en `forme[0]` segments (defaut
    26, comme `feature_dim` de train_speech_classifier) et on garde
    l'energie moyenne (|amplitude|) de chaque segment — CE N'EST PAS un vrai
    MFCC, mais un signal exploitable et deterministe pour un classifieur
    simple.

    `source` peut etre une URL http(s) (telechargee) ou un chemin relatif
    sous MEDIA_ROOT.
    """
    source = str(source).strip() if source else source
    donnees = _lire_octets_audio(source)
    onde, _rate = _decoder_onde(donnees, source)
    n_segments = forme[0] if forme else 26
    segments = np.array_split(onde, n_segments)
    features = np.array([np.abs(s).mean() if s.size else 0.0 for s in segments], dtype=np.float64)
    return features


def _lire_octets_audio(source):
    """URL http(s) ou chemin sous MEDIA_ROOT -> octets du fichier son."""
    if not source or not str(source).strip():
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_001.py:g:343:13")
        )
    source = str(source).strip()
    try:
        from ..securite import chemin_media_sur, url_externe_sure
        if source.startswith(("http://", "https://")):
            if not url_externe_sure(source):
                raise DatasetError(tf("engine/ia_integration/_part_001.py:g:351:36", v1=source))
            reponse = requests.get(source, timeout=15, allow_redirects=False)
            reponse.raise_for_status()
            return reponse.content
        chemin = chemin_media_sur(source)
        if chemin is None:
            raise DatasetError(tf("engine/ia_integration/_part_001.py:g:358:36", v1=source))
        with open(chemin, "rb") as f:
            return f.read()
    except DatasetError:
        raise
    except Exception as e:
        raise DatasetError(tf("engine/ia_integration/_part_001.py:g:364:28", v1=source, v2=e))


def _vectoriser_audio(source):
    """UNE valeur de champ son -> vecteur de caracteristiques (bandes de frequences, timbre, enveloppe)."""
    source = str(source).strip() if source else source
    onde, rate = _decoder_onde(_lire_octets_audio(source), source, centrer_8bits=True)
    try:
        return _vecto.vecteur_audio(onde, rate)
    except _vecto.VectorisationError as e:
        raise DatasetError(tf("ia.vecto.err_audio", v1=source, v2=e))


def _decoder_onde(donnees, source, centrer_8bits=False):
    """Octets d'un fichier son -> (onde mono flottante dans [-1, 1], frequence d'echantillonnage).
    `centrer_8bits` : un WAV 8 bits est non signe (silence = 128) ; les caracteristiques spectrales veulent le silence a 0.
    Laisse a False pour les modeles bruts (comportement historique inchange)."""
    import wave
    n_canaux = largeur = None
    brut = None
    erreur_pydub = None
    rate = None
    if _PydubAudioSegment is not None:
        try:
            segment = _PydubAudioSegment.from_file(io.BytesIO(donnees))
            n_canaux = segment.channels
            largeur = segment.sample_width
            brut = segment.raw_data
            rate = segment.frame_rate
        except Exception as e:
            # ffmpeg absent du systeme, ou fichier corrompu — on retente
            # avec `wave` (WAV brut) avant d'abandonner.
            erreur_pydub = e
    if brut is None:
        try:
            with wave.open(io.BytesIO(donnees), "rb") as w:
                n_canaux = w.getnchannels()
                largeur = w.getsampwidth()
                brut = w.readframes(w.getnframes())
                rate = w.getframerate()
        except Exception as e:
            detail = f" (pydub : {erreur_pydub})" if erreur_pydub else ""
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:388:17", v1=source, v2=e, v3=detail)
            )

    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(largeur)
    if dtype is None:
        raise DatasetError(tf("engine/ia_integration/_part_001.py:g:395:28", v1=largeur))
    onde = np.frombuffer(brut, dtype=dtype).astype(np.float64)
    if n_canaux > 1:
        onde = onde.reshape(-1, n_canaux).mean(axis=1)
    if dtype == np.uint8 and centrer_8bits:
        onde = onde - 128.0
        pic = 128.0
    else:
        pic = float(np.iinfo(dtype).max) if dtype != np.uint8 else 255.0
    onde = onde / pic
    if onde.size == 0:
        raise DatasetError(tf("engine/ia_integration/_part_001.py:g:402:28", v1=source))
    return onde, rate


def _charger_features_video(source, forme):
    """Resout UNE valeur de champ 'video' en ndarray de frames normalisees
    [0, 1], echantillonnees regulierement sur toute la duree :

    - `forme` a 3 dims (n_frames, H, W)    -> frames en niveaux de gris
    - `forme` a 4 dims (n_frames, H, W, C) -> frames en RGB (C doit valoir 3)
    - `forme` absente/invalide             -> repli `_FORME_VIDEO_DEFAUT`

    Meme principe que _charger_pixels_image/_charger_features_audio :
    `source` peut etre une URL http(s) (telechargee) ou un chemin relatif
    sous MEDIA_ROOT — le chemin/URL stocke EST le moyen d'acceder au
    fichier a l'entrainement, jamais une copie prealable des donnees.
    """
    if _cv2 is None:
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_001.py:g:425:13")
        )
    if not source or not str(source).strip():
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_001.py:g:431:13")
        )
    source = str(source).strip()
    try:
        from ..securite import chemin_media_sur, url_externe_sure
        if source.startswith(("http://", "https://")):
            if not url_externe_sure(source):
                raise DatasetError(tf("engine/ia_integration/_part_001.py:g:439:36", v1=source))
            reponse = requests.get(source, timeout=30, allow_redirects=False)
            reponse.raise_for_status()
            donnees = reponse.content
        else:
            chemin = chemin_media_sur(source)
            if chemin is None:
                raise DatasetError(tf("engine/ia_integration/_part_001.py:g:446:36", v1=source))
            with open(chemin, "rb") as f:
                donnees = f.read()
    except DatasetError:
        raise
    except Exception as e:
        raise DatasetError(tf("engine/ia_integration/_part_001.py:g:452:28", v1=source, v2=e))

    if forme and len(forme) == 4:
        n_frames, hauteur, largeur, canaux = forme
        if canaux != 3:
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:458:17", v1=forme)
            )
        gris = False
    elif forme and len(forme) == 3:
        n_frames, hauteur, largeur = forme
        gris = True
    else:
        n_frames, hauteur, largeur = _FORME_VIDEO_DEFAUT
        gris = True

    # cv2.VideoCapture n'accepte pas un buffer memoire directement -> fichier
    # temporaire, toujours supprime (succes ou erreur).
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    try:
        tmp.write(donnees)
        tmp.close()
        capture = _cv2.VideoCapture(tmp.name)
        total = int(capture.get(_cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            capture.release()
            raise DatasetError(tf("engine/ia_integration/_part_001.py:g:479:32", v1=source))
        frames = []
        for idx in np.linspace(0, total - 1, num=n_frames, dtype=int):
            capture.set(_cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = capture.read()
            if not ok:
                forme_defaut = (hauteur, largeur) if gris else (hauteur, largeur, 3)
                frames.append(frames[-1].copy() if frames else np.zeros(forme_defaut, dtype=np.float64))
                continue
            frame = _cv2.resize(frame, (largeur, hauteur))
            frame = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY if gris else _cv2.COLOR_BGR2RGB)
            frames.append(frame.astype(np.float64) / 255.0)
        capture.release()
    except DatasetError:
        raise
    except Exception as e:
        raise DatasetError(tf("engine/ia_integration/_part_001.py:g:495:28", v1=source, v2=e))
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return np.stack(frames)


def _valeur_colonne_ndarray(champ, valeurs_brutes):
    """Empile les valeurs d'UNE colonne pour toutes les lignes en un ndarray.

    - Colonne scalaire (nombre/decimal/...) -> vecteur 1D (n,)
    - Colonne vecteur/matrice -> empilee sur un nouvel axe -> (n, *forme)
    - Colonne image/url -> chaque valeur est chargee/decodee en pixels puis
      empilee sur un nouvel axe -> (n, *forme_tenseur)
    """
    if champ.type_champ in champ.TYPES_TENSEUR:
        lignes = []
        for v in valeurs_brutes:
            arr = np.asarray(v if v is not None else [], dtype=np.float64)
            lignes.append(arr)
        try:
            return np.stack(lignes)
        except ValueError as e:
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:521:17", v1=champ.nom, v2=champ.forme_tenseur or 'libre', v3=e)
            )
    if champ.type_champ in champ.TYPES_IMAGE_REFERENCE:
        forme = champ.forme_tenseur
        if _VECTORISATION.get():
            lignes = [_vectoriser_image(v) for v in valeurs_brutes]
        else:
            lignes = [_charger_pixels_image(v, forme) for v in valeurs_brutes]
        try:
            return np.stack(lignes)
        except ValueError as e:
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:531:17", v1=champ.nom, v2=e)
            )
    if champ.type_champ in champ.TYPES_AUDIO_REFERENCE:
        forme = champ.forme_tenseur
        if _VECTORISATION.get():
            lignes = [_vectoriser_audio(v) for v in valeurs_brutes]
        else:
            lignes = [_charger_features_audio(v, forme) for v in valeurs_brutes]
        try:
            return np.stack(lignes)
        except ValueError as e:
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:542:17", v1=champ.nom, v2=e)
            )
    if champ.type_champ in champ.TYPES_VIDEO_REFERENCE:
        forme = champ.forme_tenseur
        lignes = [_charger_features_video(v, forme) for v in valeurs_brutes]
        try:
            return np.stack(lignes)
        except ValueError as e:
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:553:17", v1=champ.nom, v2=e)
            )
    # Colonne scalaire classique : seuls les types reellement numeriques
    # sont convertis. Avant ce garde-fou, un champ non numerique (texte,
    # url, choix_unique, date, ...) qui echouait a `float(v)` retombait
    # silencieusement sur 0.0 pour TOUTES ses lignes — un modele pouvait
    # donc "s'entrainer" sur une colonne entierement nulle sans aucune
    # erreur. On prefere echouer explicitement.
    if champ.type_champ not in _TYPES_NUMERIQUES_DIRECTS:
        note_texte = ""
        if champ.type_champ in _TYPES_TEXTE_VECTORISABLES:
            note_texte = (
                _i18n_t("engine/ia_integration/_part_001.py:g:567:17")
            )
        raise DatasetError(
            tf("engine/ia_integration/_part_001.py:g:573:13", v1=champ.nom, v2=champ.type_champ, v3=', '.join(_TYPES_NUMERIQUES_DIRECTS)) + note_texte
        )

    def _nombre(v):
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    return np.asarray([_nombre(v) for v in valeurs_brutes], dtype=np.float64)


def _encoder_cible_categorielle(champ, valeurs_brutes, classes_figees=None):
    """Encode une colonne cible categorielle (ex: Choix unique avec des
    labels "chat"/"chien") en entiers 0..k-1 pour la classification.

    Retourne (y, classes) ou `classes` est la liste des labels tries,
    classes[i] etant le label d'origine correspondant a l'entier i (utile
    pour decoder une prediction a l'inference). Les valeurs manquantes
    (None/"") ne sont pas autorisees pour une cible."""
    labels = [str(v) if v is not None else "" for v in valeurs_brutes]
    if classes_figees:
        # Fine-tuning : les indices de classe doivent rester ceux du modele d'origine. Les recalculer sur les
        # seules lignes nouvelles decalerait les etiquettes (ex. « chien » deviendrait la classe 0).
        classes = [str(c) for c in classes_figees]
        inconnues = sorted(set(labels) - set(classes))
        if inconnues:
            raise DatasetError(
                _i18n_tf("rg2.ia_integration__part_001.1", v1=champ.nom, v2=', '.join(inconnues[:10]), v3=', '.join(classes))
            )
    else:
        classes = sorted(set(labels))
    index = {label: i for i, label in enumerate(classes)}
    y = np.asarray([index[l] for l in labels], dtype=np.float64)
    return y, classes


# ── Lecture des colonnes d'entraînement : TOUJOURS le fichier Parquet ───────────────────────────────────────
# Principe du projet : l'EAV reste le stockage des tables dynamiques, mais les traitements qui lisent toute une table pour
# l'analyser (ici : construire X / y d'un jeu d'entraînement) consultent le fichier Parquet (parquet_store), pour TOUS les types de
# colonnes. Aucune relecture de l'EAV cellule par cellule ici : le seul repli (table NoSQL, pyarrow absent) est dans parquet_store.
# Les types dont le fichier ne garde pas la forme brute sont ramenés à celle de ValeurChamp.valeur_typee() (dates -> texte ISO,
# durées -> secondes) : c'est ce que les encodeurs (encodage_champs) attendent.
_TYPES_PARQUET_NOMBRES = ("nombre", "note", "barre_progression")
_TYPES_PARQUET_DECIMAUX = ("decimal", "monnaie", "pourcentage", "constante")
_TYPES_PARQUET_TEXTES = ("texte", "texte_long")
_TYPES_PARQUET_DATES = ("date", "datetime", "horodatage")


def _texte_date(v):
    """Date du fichier -> texte ISO (jour seul quand il n'y a pas d'heure), comme la saisie d'origine."""
    return v.date().isoformat() if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0) else v.isoformat()


def _convertir_serie_parquet(type_champ, serie):
    """Série pandas (colonne du fichier Parquet) -> liste de valeurs Python, None pour les manquantes — la forme de valeur_typee()."""
    objets = serie.astype(object)
    valeurs = objets.where(serie.notna(), None).tolist()
    if type_champ in _TYPES_PARQUET_NOMBRES:
        return [None if v is None else int(v) for v in valeurs]
    if type_champ in _TYPES_PARQUET_DECIMAUX:
        return [None if v is None else float(v) for v in valeurs]
    if type_champ in ("booleen", "switch"):
        return [None if v is None else bool(v) for v in valeurs]
    if type_champ in _TYPES_PARQUET_DATES:
        return [None if v is None else _texte_date(v) for v in valeurs]
    if type_champ == "duree":
        return [None if v is None else (v.total_seconds() if hasattr(v, "total_seconds") else v) for v in valeurs]
    return valeurs


class _LecteurColonnes:
    """Valeurs d'une table (ou de son groupe, ou de sa vue filtrée `_TableFiltree`) colonne par colonne, dans l'ordre de
    `table.enregistrements_groupe`, lues dans le fichier Parquet de la table (une seule lecture pour toutes les colonnes)."""

    def __init__(self, table):
        self.table = table
        self._cadre = None           # DataFrame du fichier, réordonné sur les lignes voulues ; None tant qu'il n'est pas lu

    def _cadre_parquet(self):
        if self._cadre is not None:
            return self._cadre
        from .. import parquet_store
        base = self.table.__dict__.get("_table", self.table)    # _TableFiltree : la vraie table porte le fichier
        ids = list(self.table.enregistrements_groupe.values_list("id", flat=True))
        df = parquet_store.donnees_analytiques(base, avec_id=True)
        if not ids:
            self._cadre = df.iloc[0:0]
            return self._cadre
        if parquet_store.COLONNE_ID not in df.columns:
            raise DatasetError(_i18n_tf("rg2.ia_integration__part_001.2", v1=base.nom))
        positions = df.set_index(parquet_store.COLONNE_ID).index.get_indexer(ids)
        if (positions < 0).any():
            raise DatasetError(_i18n_tf("rg2.ia_integration__part_001.3", v1=base.nom))
        self._cadre = df.iloc[positions]
        return self._cadre

    def valeurs(self, champ):
        cadre = self._cadre_parquet()
        if champ.slug not in cadre.columns:
            return [None] * len(cadre)
        return _convertir_serie_parquet(champ.type_champ, cadre[champ.slug])


def _table_vers_matrice(table, exclure_slug=None, inclure_slugs=None, vectoriser_texte=True,
                        collecteur_encodeurs=None, specs_figes=None, vocab_fige=None, standardiser=False):
    """Construit (X, colonnes_utilisees, vocabulaire_texte) pour une table — ou pour tout son
    GroupeTables (lignes lues via table.enregistrements_groupe, valeurs retrouvées par SLUG de colonne :
    chaque partie a ses propres champs, avec d'autres ids).

    - Si `inclure_slugs` est fourni (liste non vide) : SEULES ces colonnes
      sont utilisees, dans l'ordre donne par `inclure_slugs` (pas l'ordre de
      la table) — c'est la selection explicite faite par l'utilisateur dans
      l'interface (colonnes_entree / colonnes_sortie de ModeleIA). Les slugs
      demandes mais introuvables sur la table levent une erreur explicite.
    - Sinon : comportement historique — toutes les colonnes de la table
      (ordre `ordre`), sauf `exclure_slug` (reserve a y ; accepte un slug
      unique (str) ou une liste de slugs).

    `vectoriser_texte` (defaut True) : si l'UNIQUE colonne resolue est de
    type texte/texte_long, elle est transformee en matrice TF-IDF plutot que
    de lever DatasetError (cas particulier, au meme titre que
    tenseur/image/audio ci-dessous) — voir _construire_vocabulaire_texte.
    Le 3e element retourne vaut alors {"colonne", "vocabulaire", "idf"}
    (None sinon) et DOIT etre persiste (ModeleIA.structure_entree) pour que
    l'inference vectorise une nouvelle ligne avec le meme vocabulaire.
    Passe a False pour resoudre une colonne CIBLE (y) : vectoriser un texte
    brut en cible n'a pas de sens, mieux vaut l'erreur explicite existante.

    `standardiser` : centre-reduit chaque colonne numerique (moyenne/ecart memorises dans sa recette, voir
    encodage_champs.spec_simple). A n'activer que si les recettes sont reellement persistees dans
    structure_entree (une seule table source) : l'inference rejoue alors la meme transformation. En
    fine-tuning (`specs_figes`), la recette du modele d'origine est reutilisee telle quelle.
    """
    if inclure_slugs:
        champs_par_slug = {c.slug: c for c in table.champs.all()}
        champs = []
        for slug in inclure_slugs:
            champ = champs_par_slug.get(slug)
            if champ is None:
                raise DatasetError(
                    tf("engine/ia_integration/_part_001.py:g:639:21", v1=slug, v2=table.nom)
                )
            champs.append(champ)
    else:
        qs = table.champs.all()
        if isinstance(exclure_slug, str):
            qs = qs.exclude(slug=exclure_slug) if exclure_slug else qs
        elif exclure_slug:
            qs = qs.exclude(slug__in=list(exclure_slug))
        champs = list(qs.order_by("ordre"))
    if not champs:
        return None, [], None

    # Cas particulier : une seule colonne texte/texte_long -> vectorisation
    # TF-IDF plutot que le garde-fou DatasetError de _valeur_colonne_ndarray
    # (reserve aux colonnes scalaires). Doit rester AVANT la boucle
    # generique ci-dessous, qui appellerait sinon _valeur_colonne_ndarray
    # sur ce champ et leverait DatasetError comme pour n'importe quelle
    # autre colonne non numerique.
    if vectoriser_texte and len(champs) == 1 and champs[0].type_champ in _TYPES_TEXTE_VECTORISABLES:
        champ = champs[0]
        textes = _LecteurColonnes(table).valeurs(champ)
        if vocab_fige and vocab_fige.get("vocabulaire"):
            # Fine-tuning : on reutilise le vocabulaire/IDF du modele d'origine (jamais recalcules sur les
            # seules nouvelles lignes, sinon les colonnes TF-IDF ne correspondraient plus aux poids appris).
            vocabulaire, idf = vocab_fige["vocabulaire"], vocab_fige["idf"]
        else:
            vocabulaire, idf = _construire_vocabulaire_texte(textes)
        X = _vectoriser_texte_tfidf(textes, vocabulaire, idf)
        vocabulaire_texte = {"colonne": champ.slug, "vocabulaire": vocabulaire, "idf": idf}
        return X, [champ.slug], vocabulaire_texte

    lecteur = _LecteurColonnes(table)
    colonnes = []
    blocs_encodes = set()  # indices des colonnes dont le bloc est 2D par construction (choix, date, texte...)
    for champ in champs:
        valeurs = lecteur.valeurs(champ)
        if collecteur_encodeurs is not None and champ.type_champ in _TYPES_ENCODES_PAR_CHAMP:
            # Vrai champ de la table (choix, date, texte...) : encodé selon son type,
            # recette mémorisée pour que l'inférence redemande ce même champ.
            from .. import encodage_champs as _enc
            try:
                spec_fige = (specs_figes or {}).get(champ.slug)
                if spec_fige is not None:
                    # Fine-tuning : recette (modalites, moyenne/ecart, vocabulaire) du modele d'origine, rejouee telle quelle.
                    spec = spec_fige
                    bloc = (np.vstack([_enc._appliquer(spec, v, tolerant=True) for v in valeurs])
                            if valeurs else np.zeros((0, _enc.largeur(spec))))
                else:
                    bloc, spec = _enc.ajuster(champ, valeurs)
            except ValueError as e:
                raise DatasetError(str(e))
            blocs_encodes.add(len(colonnes))
            colonnes.append(bloc)
            collecteur_encodeurs["specs"].append(spec)
            continue
        colonne = _valeur_colonne_ndarray(champ, valeurs)
        if collecteur_encodeurs is not None:
            if champ.type_champ in _TYPES_NUMERIQUES_DIRECTS:
                from .. import encodage_champs as _enc
                spec_fige = (specs_figes or {}).get(champ.slug)
                if spec_fige is not None:
                    spec = spec_fige            # fine-tuning : meme recette que le modele d'origine
                elif standardiser:
                    spec = _enc.spec_simple(champ, colonne)
                else:
                    spec = _enc.spec_simple(champ)
                if spec.get("kind") == "num" and colonne.ndim == 1:
                    colonne = _enc.appliquer_num(spec, colonne)
                collecteur_encodeurs["specs"].append(spec)
            else:
                collecteur_encodeurs["complet"] = False  # tenseur/image... : saisie spécifique
        colonnes.append(colonne)

    if len(champs) == 1 and (
        champs[0].type_champ in champs[0].TYPES_TENSEUR
        or champs[0].type_champ in champs[0].TYPES_IMAGE_REFERENCE
        or champs[0].type_champ in champs[0].TYPES_AUDIO_REFERENCE
        or champs[0].type_champ in champs[0].TYPES_VIDEO_REFERENCE
    ):
        # Une seule colonne tenseur/image/audio (ex: table d'images stockees
        # en matrice, ou table d'URLs/references) -> X = (n, *forme), pas de
        # column_stack qui aplatirait tout (le garde-fou ndim!=1 plus bas
        # est reserve au melange de PLUSIEURS colonnes).
        return colonnes[0], [c.slug for c in champs], None

    # Toutes les colonnes doivent etre 1D pour un column_stack classique.
    if any(c.ndim != 1 for i, c in enumerate(colonnes) if i not in blocs_encodes):
        raise DatasetError(
            _i18n_t("engine/ia_integration/_part_001.py:g:713:13")
        )
    return np.column_stack(colonnes), [c.slug for c in champs], None


def _table_documents_json(table, colonne_slug=None):
    """Table a une seule colonne (ou colonne explicitement selectionnee via
    `colonne_slug`) -> liste de documents JSON (un par ligne). Utilise pour
    les modeles texte/document (ex: slm) : chaque ligne est parsee en JSON
    si possible (les champs 'texte'/'texte_long' contenant du JSON valide,
    et les champs 'json' — toujours du JSON valide par construction — en
    beneficient egalement), sinon gardee telle quelle comme texte brut."""
    if colonne_slug:
        champ = table.champs.filter(slug=colonne_slug).first()
        if champ is None:
            raise DatasetError(
                tf("engine/ia_integration/_part_001.py:g:731:17", v1=colonne_slug, v2=table.nom)
            )
    else:
        champ = table.champs.order_by("ordre").first()
    documents = []
    sources = _LecteurColonnes(table).valeurs(champ)    # fichier Parquet ; une valeur déjà décodée (JSON) passe telle quelle dans json.loads ci-dessous (TypeError)
    for brut in sources:
        if brut is None:
            continue
        try:
            documents.append(json.loads(brut))
        except (ValueError, TypeError):
            documents.append(brut)
    return documents


def _famille_colonne_classe(champ):
    """Classe un champ dans l'une des 3 familles acceptees par
    `_table_colonnes_vers_classes` — les echantillons de toutes les
    colonnes-classes doivent etre du meme genre de donnee (X reste
    homogene) : "texte" (vectorisation TF-IDF PARTAGEE entre les
    classes), "tenseur" (Vecteur/Matrice/Image/Audio — memes formes
    entre colonnes), ou "scalaire" (Nombre, Choix unique... — le
    comportement historique)."""
    if champ.type_champ in _TYPES_TEXTE_VECTORISABLES:
        return "texte"
    if (champ.type_champ in champ.TYPES_TENSEUR
            or champ.type_champ in champ.TYPES_IMAGE_REFERENCE
            or champ.type_champ in champ.TYPES_AUDIO_REFERENCE
            or champ.type_champ in champ.TYPES_VIDEO_REFERENCE):
        return "tenseur"
    return "scalaire"




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (__init__.py,
# parties suivantes). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
