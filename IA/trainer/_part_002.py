from engine.i18n import t
from engine.i18n import tf
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa


class Trainer:
    """Point d'entrée unifié pour entraîner tous les modèles IA.

    Args:
        verbose: si True, affiche la progression (callback ProgressPrinter).
        callbacks: liste de callbacks additionnels à ajouter.
        default_save_dir: répertoire de sauvegarde par défaut.

    Exemple :
        >>> trainer = Trainer(verbose=True)
        >>> model = trainer.train(type='rnn', epochs=500, lr=0.01)
        >>> y = model.predict(X_test)
        >>> model.save('rnn.gy')

        >>> # Rechargement
        >>> model = Trainer.load('rnn.gy')
        >>> y = model.predict(X_test)
    """

    SUPPORTED_TYPES = ['mlp', 'rnn', 'cnn', 'transformer', 'gan', 'ldm',
                        'slm', 'slm_texte', 'image_classifier', 'speech_classifier',
                        'sklearn_rf', 'sklearn_gbm', 'sklearn_svm', 'sklearn_knn',
                        'sklearn_lr', 'sklearn_nb', 'sklearn_dt',
                        'sklearn_ridge', 'sklearn_lasso',
                        'sklearn_kmeans', 'sklearn_dbscan', 'sklearn_hierarchique', 'sklearn_gmm',
                        'sklearn_acp', 'sklearn_ica', 'sklearn_isoforest', 'sklearn_ocsvm', 'sklearn_lof']

    def __init__(self, verbose: bool = True,
                 callbacks: Optional[List[Callback]] = None,
                 default_save_dir: Optional[str] = None):
        _register_all()
        self.verbose = verbose
        self.user_callbacks = callbacks or []
        self.default_save_dir = default_save_dir or MODELS_DIR
        # Etat interne mis à jour pendant train()
        self.model_type: Optional[str] = None
        self.epochs: int = 0
        self.lr: float = 0.0
        self.stop_training: bool = False
        self.history: List[Dict[str, float]] = []
        self.model_path: Optional[str] = None
        self.final_accuracy: Optional[float] = None
        self._current_model_data: Optional[Dict[str, Any]] = None
        self._current_config: Optional[Dict[str, Any]] = None
        self._current_extract_weights: Optional[Callable] = None

    # ------------------------------------------------------------------
    # API principale
    # ------------------------------------------------------------------
    def train(self, type: str, X=None, y=None, dataset=None,
              dataset_target: Optional[str] = None,
              epochs: int = 1000,
              lr: float = 0.01, batch_size: Optional[int] = None,
              seed: Optional[int] = 42,
              callbacks: Optional[List[Callback]] = None,
              save: bool = True, save_path: Optional[str] = None,
              early_stopping_patience: int = 0,
              bits: Optional[int] = None,
              tensor_bits: Optional[Dict[str, int]] = None,
              **kwargs) -> Model:
        """Entraîne un modèle du type spécifié.

        Args:
            type: type de modèle ('mlp', 'rnn', 'cnn', 'transformer', 'gan',
                  'ldm', 'slm', 'image_classifier', 'speech_classifier').
            X, y: données d'entraînement (None = données démo).
            dataset: chemin vers un fichier ou dossier de données.
                     Formats fichier : .csv, .tsv, .json, .jsonl, .npy,
                     .npz, .txt, .h5, .parquet, .xlsx, .pkl, .gz
                     Dossier : structure label/image ou images brutes.
                     Si fourni, X et y sont ignorés.
            dataset_target: nom de la colonne cible (pour fichiers tabulaires).
            epochs: nombre d'époques.
            lr: taux d'apprentissage.
            batch_size: taille de batch (ignoré par certains modèles).
            seed: graine aléatoire.
            callbacks: liste de callbacks additionnels (Keras-like).
            save: si True, sauvegarde le modèle en .gy à la fin.
            save_path: chemin de sauvegarde. Si None, généré automatiquement.
            early_stopping_patience: patience EarlyStopping (0 = désactivé).
            bits: largeur de bits réelle des paramètres sauvegardés
                  (4, 8, 16, 32 ou 64). None = config globale IA_PARAM_BITS
                  (64 par défaut). Le calcul reste toujours en float64 ;
                  seule la persistance sur disque est affectée.
            tensor_bits: override par nom de tenseur (ex. {'W_0': 8}).
            **kwargs: hyperparams additionnels passés au train_xxx.

            Paramètres de profondeur (passés via **kwargs) :
              mlp  : hidden_sizes=[64, 32]       (liste libre, par défaut [64, 32])
              rnn  : num_layers=1                (couches récurrentes empilées)
              cnn  : num_conv_layers=1           (couches de convolution empilées)
              transformer : num_blocks=1         (blocs attention+FFN empilés)
              gan  : generator_layers=None, discriminator_layers=None
                     (listes d'ints, ex: [latent, 128, 64, data_dim])
              ldm  : hidden_sizes=None           (listes, ex: [128, 64])
              slm  : num_blocks=1                (blocs attention+FFN empilés)
              image_classifier : hidden_layers=None (liste, ex: [128, 64])

        Returns:
            Un objet Model avec predict_fn enregistrée.

        Examples:
            trainer.train(type='cnn', epochs=100)                       # démo interne
            trainer.train(type='cnn', dataset='mnist.csv', epochs=100)  # fichier CSV
            trainer.train(type='cnn', dataset='images/', epochs=100)    # dossier images
        """
        # --- Résolution du dataset -------------------------------------------
        if dataset is not None:
            from ..dataset import load_dataset, load_folder
            if os.path.isdir(dataset):
                logger.info(t("IA/trainer/_part_002.py:g:116:29"), dataset)
                X, y = load_folder(dataset)
            else:
                logger.info(t("IA/trainer/_part_002.py:g:119:29"), dataset)
                result = load_dataset(dataset, target=dataset_target)
                if isinstance(result, tuple):
                    X, y = result
                else:
                    X, y = result, None
            logger.info(t("IA/trainer/_part_002.py:g:125:25"), X.shape,
                        y.shape if y is not None else None)
        # ---------------------------------------------------------------------

        if type not in _REGISTRY:
            raise ConfigurationError(
                tf("IA/trainer/_part_002.py:g:130:17", v1=type, v2=list(_REGISTRY.keys()))
            )

        entry = _REGISTRY[type]
        train_fn = entry['train_fn']

        # Préparation de l'état
        self.model_type = type
        self.epochs = epochs
        self.lr = lr
        self.stop_training = False
        self.history = []
        self.final_accuracy = None
        self._current_extract_weights = entry['extract_weights']

        # Callbacks
        all_callbacks = list(self.user_callbacks)
        if callbacks:
            all_callbacks.extend(callbacks)
        if self.verbose:
            # On n'ajoute ProgressPrinter que si l'utilisateur n'en a pas fourni un.
            # (Avant : `type(ProgressPrinter)` appelait le parametre `type` (str) -> TypeError, et
            # ProgressPrinter etait lu avant son import local -> UnboundLocalError, des que verbose=True
            # et qu'au moins un callback etait fourni.)
            from ..callbacks import ProgressPrinter
            if not any(c.__class__.__name__ == 'ProgressPrinter' for c in all_callbacks):
                all_callbacks.append(ProgressPrinter(interval=max(1, epochs // 10)))
        if early_stopping_patience > 0:
            from ..callbacks import EarlyStopping
            all_callbacks.append(EarlyStopping(patience=early_stopping_patience))

        cb_list = CallbackList(all_callbacks)
        cb_list.on_train_begin(self)

        # ------------------------------------------------------------
        # Hook epoch-par-epoch : branché sur le paramètre on_epoch de
        # chaque train_xxx. Permet à EarlyStopping/ModelCheckpoint/
        # ProgressPrinter/CSVLogger (et tout callback utilisateur) de
        # s'exécuter réellement à chaque époque, et pas seulement en
        # début/fin d'entraînement.
        # ------------------------------------------------------------
        def _epoch_hook(epoch, metrics, model_dict=None):
            if model_dict is not None:
                self._current_model_data = model_dict
                self._current_config = {}
            self.history.append(metrics)
            cb_list.on_epoch_begin(epoch, self)
            cb_list.on_epoch_end(epoch, metrics, self)
            return self.stop_training

        # Construction des kwargs pour train_xxx
        train_kwargs = {
            'lr': lr,
            'epochs': epochs,
            'seed': seed,
            'on_epoch': _epoch_hook,
            **entry['default_kwargs'],
            **kwargs,
        }
        if X is not None:
            train_kwargs['X'] = X
        if y is not None:
            train_kwargs['y'] = y
        # batch_size n'est accepté que par certains train_xxx (mlp, gan_*...).
        # On l'injecte seulement si la fonction cible le supporte réellement,
        # sinon un TypeError (unexpected keyword argument) serait levé pour
        # les types qui n'ont ni paramètre batch_size ni **kwargs (cnn, rnn,
        # transformer, ldm, slm, image_classifier, speech_classifier).
        if batch_size is not None and 'batch_size' in inspect.signature(train_fn).parameters:
            train_kwargs['batch_size'] = batch_size
        # Résoudre le save_path pour le train function
        if save:
            if save_path is None:
                resolved_path = self._default_save_path(type)
            else:
                dirname = os.path.dirname(save_path)
                if not dirname:
                    resolved_path = os.path.join(MODELS_DIR, save_path)
                else:
                    resolved_path = save_path
                if not resolved_path.endswith('.gy'):
                    resolved_path += '.gy'
            train_kwargs['save_path'] = resolved_path
        else:
            resolved_path = None

        # Entraînement
        try:
            result = train_fn(**train_kwargs)
        except Exception as e:
            logger.error(t("IA/trainer/_part_002.py:g:221:26"), type, e)
            raise TrainingError(
                tf("IA/trainer/_part_002.py:g:223:17", v1=type, v2=e)
            )

        # Accuracy finale : tous les train_xxx la retournent au niveau racine
        # du dict result (contrairement à `config`, où sa présence varie selon
        # le type — cf. extract_weights). C'est la seule source fiable pour
        # la remonter jusqu'à l'UI.
        if isinstance(result, dict) and 'accuracy' in result:
            self.final_accuracy = result['accuracy']
            # Échelle unique : fraction 0–1 (celle du .gy, de l'API et de l'écran, qui multiplie par 100). Les train_xxx renvoient
            # un POURCENTAGE à la racine (mlp, rnn, cnn, vision, transformer, slm, sklearn…) sauf gan et ldm (déjà une fraction) :
            # sans cette conversion un score de 100 s'affichait « 10000.0 % de précision ».
            if isinstance(self.final_accuracy, (int, float)) and type not in ('gan', 'ldm'):
                self.final_accuracy = float(self.final_accuracy) / 100.0

        # Extraction des poids et config
        weights, config = entry['extract_weights'](result)

        # ── Persistance des métriques d'entraînement ─────────────────────
        # Jusqu'ici, l'accuracy finale (self.final_accuracy, ci-dessus) et
        # l'historique epoch-par-epoch (self.history, alimenté par
        # _epoch_hook pour TOUS les types, deep learning comme sklearn)
        # n'étaient jamais recopiés dans `config` : ils ne vivaient qu'en
        # mémoire sur l'objet Trainer et disparaissaient dès la fin de
        # train() (jamais écrits dans le .gy par model.save() ci-dessous).
        # Un seul endroit à corriger : ce point est commun à tous les types
        # (register_model_type), inutile de toucher aux 17 train_xxx.
        if isinstance(config, dict):
            if self.final_accuracy is not None and 'accuracy' not in config:
                config['accuracy'] = self.final_accuracy
            config.setdefault('metrics_history', self.history)
            # Certains train_xxx renvoient aussi des métriques additionnelles
            # au niveau racine de result (accuracy_train/accuracy_test et
            # split_info pour les modèles sklearn, etc.) : on les recopie si
            # absentes de config, sans écraser ce qu'extract_weights gardait déjà.
            if isinstance(result, dict):
                for cle, valeur in result.items():
                    if cle in ('model', 'save_path') or cle in config:
                        continue
                    config[cle] = valeur

        # Construction du Model
        model = Model(type, config, weights, predict_fn=entry['predict_fn'])

        # Le train_xxx a sauvegardé avec son format, on re-sauvegarde avec
        # le format Model (type, config, weights_meta) sur le même fichier
        if resolved_path:
            model.save(resolved_path, bits=bits, tensor_bits=tensor_bits)
            logger.info(t("IA/trainer/_part_002.py:g:266:25"), resolved_path)
            self.model_path = resolved_path
        else:
            self.model_path = None

        cb_list.on_train_end(self)
        return model

    # ------------------------------------------------------------------
    # Chargement
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str) -> Model:
        """Charge un modèle .gy et retourne un Model prêt à inférer.

        Args:
            path: chemin du fichier .gy. Si le fichier n'existe pas
                  directement, cherche dans le dossier models/ du module.

        Returns:
            Model avec predict_fn récupérée depuis le registre.
        """
        _register_all()
        # Forcer l'extension .gy
        base, ext = os.path.splitext(path)
        if ext and ext != '.gy':
            path = base + '.gy'
        elif not ext:
            path = path + '.gy'
        # Résolution du chemin : chercher dans models/ si non trouvé
        if not os.path.isfile(path):
            candidate = os.path.join(MODELS_DIR, os.path.basename(path))
            if os.path.isfile(candidate):
                path = candidate
        return Model.load(path)

    @classmethod
    def read_metrics(cls, path: str) -> Dict[str, Any]:
        """Relit les métriques d'un modèle déjà entraîné (accuracy finale,
        historique epoch-par-epoch, métriques additionnelles type
        accuracy_train/accuracy_test) SANS charger les poids ni ré-entraîner.

        Fonctionne pour tous les types, deep learning comme sklearn : depuis
        la correction de train() ci-dessus, ces métriques sont toujours
        stockées dans `config` (donc dans le header .gy), jamais dans les
        tenseurs — model_info() suffit, léger, pas de lecture des poids.

        Args:
            path: chemin du fichier .gy (mêmes règles de résolution que load()).

        Returns:
            Dict avec au moins 'type', 'accuracy', 'metrics_history' (liste de
            dicts, un par époque) ; contient aussi toute autre métrique
            propre au type entraîné (ex. accuracy_train/accuracy_test,
            split_info pour les modèles sklearn).
        """
        base, ext = os.path.splitext(path)
        if ext and ext != '.gy':
            path = base + '.gy'
        elif not ext:
            path = path + '.gy'
        if not os.path.isfile(path):
            candidate = os.path.join(MODELS_DIR, os.path.basename(path))
            if os.path.isfile(candidate):
                path = candidate
        header = ia_format.model_info(path)
        config = header.get('config', {}) or {}
        return {
            'type': header.get('type'),
            'accuracy': config.get('accuracy'),
            'metrics_history': config.get('metrics_history', []),
            'config': config,
        }

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------
    def _default_save_path(self, model_type: str) -> str:
        ensure_directories()
        entry = _REGISTRY.get(model_type, {})
        name = entry.get('default_save_name', f'mini_{model_type}')
        return os.path.join(self.default_save_dir, name)

    def _save_current(self, filepath: str):
        """Appelé par ModelCheckpoint pendant l'entraînement.
        Sauvegarde l'état courant du modèle en .gy.
        """
        if self._current_model_data is None or self._current_config is None:
            return
        if self._current_extract_weights is None:
            return
        # Conversion : _current_model_data est le dict 'model' retourné par train_xxx
        # On doit l'encapsuler comme un 'result' pour extract_weights
        result = {'model': self._current_model_data, 'config': self._current_config}
        weights, config = self._current_extract_weights(result)
        m = Model(self.model_type, config, weights)
        m.save(filepath)

    # ------------------------------------------------------------------
    # Représentation
    # ------------------------------------------------------------------
    def __repr__(self):
        return (f"Trainer(types={list(_REGISTRY.keys())}, "
                f"verbose={self.verbose})")


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
