"""Taches de fond (engine/taches_fond.py, Python pur) pour tout ce qui est lent et bloquant dans le moteur :
appel au LLM local (llama.cpp), entrainement des modeles IA, generation de
documents. Chaque tache travaille sur un objet deja cree en base (avec un
statut "en_cours"/"entrainement" pose AVANT l'appel .delay(), cote vue, pour
que le polling voie tout de suite l'etat transitoire) et se contente de
mettre a jour ce meme objet -- exactement le pattern deja utilise par
l'ancien thread d'entrainement : un fil d'arriere-plan du process web.

Import des fonctions metier a l'interieur de chaque tache (pas au niveau du
module) : ce fichier peut etre importe avant que l'app Django soit entierement chargee -- on evite ainsi tout risque
d'import circulaire avec engine.views / engine.agent.
"""
from engine.i18n import t, tf
import logging
import os

from .taches_fond import shared_task
from django.db import connection as _db_connection
from engine.i18n import t as _gt  # noqa: E402

logger = logging.getLogger(__name__)


def _fermer_connexion_db():
    """Chaque tache de fond obtient sa propre connexion DB (comme chaque
    thread avant elle) : on la ferme explicitement en fin de tache pour ne
    pas laisser de connexions SQLite ouvertes indefiniment."""
    _db_connection.close()


# ─────────────────────────────────────────────────────────────
# ENTRAINEMENT IA — remplace l'ancien threading.Thread de
# api_ia_entrainer (engine/views.py).
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.entrainer_modele_ia")
def entrainer_modele_ia(modele_id):
    from . import ia_integration as _ia
    from .exceptions_ia import DatasetError, EntrainementError
    from .journal_ia import Journal
    from .models import ModeleIA

    try:
        modele = ModeleIA.objects.get(id=modele_id)
    except ModeleIA.DoesNotExist:
        logger.warning(_gt("rg6.msg.113"), modele_id)
        return
    if modele.statut != "entrainement":
        # Tache perimee (annulee/refusee pendant qu'elle attendait un worker).
        logger.info(t("engine/tasks.py:g:51:21"), modele_id, modele.statut)
        return

    try:
        _ia.lancer_entrainement(modele)
    except (DatasetError, EntrainementError):
        # Deja journalise (statut='erreur' + erreur + journal) par lancer_entrainement.
        logger.info(t("engine/tasks.py:g:58:21"), modele_id)
    except Exception as e:
        # Dernier filet : jamais de modele bloque sur 'entrainement' sans explication.
        logger.exception(t("engine/tasks.py:g:61:26"), modele_id)
        try:
            ModeleIA.objects.filter(id=modele_id).update(statut="erreur", erreur=tf("engine/tasks.py:g:62:82", v1=e))
            Journal(modele_id).log(tf("engine/tasks.py:g:63:36", v1=e), "err", phase="erreur")
        except Exception:
            logger.exception(t("engine/tasks.py:g:66:30"), modele_id)
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.affiner_modele_ia")
def affiner_modele_ia(modele_id, options=None):
    """Fine-tuning : repart du .gy existant et n'apprend que les lignes
    ajoutees depuis le dernier entrainement. En cas d'echec ou d'annulation,
    le modele precedent est intact : on le remet au statut 'entraine'."""
    from . import ia_integration as _ia
    from .exceptions_ia import DatasetError, EntrainementError
    from .journal_ia import Journal
    from .models import ModeleIA

    try:
        modele = ModeleIA.objects.get(id=modele_id)
    except ModeleIA.DoesNotExist:
        return
    if modele.statut != "entrainement":
        return
    try:
        _ia.lancer_entrainement(modele, finetune=dict(options or {}))
    except (DatasetError, EntrainementError):
        logger.info(t("engine/tasks.py:g:90:21"), modele_id)
    except Exception as e:
        logger.exception(t("engine/tasks.py:g:92:26"), modele_id)
        try:
            Journal(modele_id).log(tf("engine/tasks.py:g:93:36", v1=e), "err", phase="erreur")
        except Exception:
            logger.exception(t("engine/tasks.py:g:96:30"), modele_id)
    finally:
        try:
            m = ModeleIA.objects.get(id=modele_id)
            if m.statut in ("erreur", "annule", "entrainement") and m.fichier_modele:
                message = m.erreur
                m.statut, m.erreur, m.demande_pause, m.demande_annulation = "entraine", "", False, False
                m.save(update_fields=["statut", "erreur", "demande_pause", "demande_annulation", "modifie_le"])
                Journal(modele_id).log(
                    t("engine/tasks.py:g:104:21")
                    + (f" ({message})" if message else ""), "warn")
        except Exception:
            logger.exception(t("engine/tasks.py:g:108:30"), modele_id)
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# AGENT — generation de script IA + execution, pour une DemandeAgent
# (analyse a la demande). Remplace les appels synchrones dans
# api_demander / api_regenerer_script / api_lancer / api_actualiser_analyse
# / api_insister (engine/views.py).
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.generer_script_demande")
def generer_script_demande_tache(demande_id):
    from .agent import generer_script_demande
    from .models import DemandeAgent

    try:
        demande = DemandeAgent.objects.get(id=demande_id)
    except DemandeAgent.DoesNotExist:
        logger.warning(_gt("rg6.msg.114"), demande_id)
        return
    try:
        generer_script_demande(demande)
    except Exception:
        logger.exception(t("engine/tasks.py:g:132:26"), demande_id)
        demande.statut = "echec"
        demande.erreur = t("engine/tasks.py:g:134:26")
        demande.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.executer_demande")
def executer_demande_tache(demande_id):
    from .agent import executer_demande
    from .models import DemandeAgent

    try:
        demande = DemandeAgent.objects.get(id=demande_id)
    except DemandeAgent.DoesNotExist:
        logger.warning(_gt("rg6.msg.115"), demande_id)
        return
    try:
        executer_demande(demande)
    except Exception:
        logger.exception(t("engine/tasks.py:g:153:26"), demande_id)
        demande.statut = "echec"
        demande.erreur = t("engine/tasks.py:g:155:26")
        demande.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.insister_sur_demande")
def insister_sur_demande_tache(demande_id):
    from .agent import insister_sur_demande
    from .models import DemandeAgent

    try:
        demande = DemandeAgent.objects.get(id=demande_id)
    except DemandeAgent.DoesNotExist:
        logger.warning(t("engine/tasks.py:g:169:24"), demande_id)
        return
    try:
        insister_sur_demande(demande)
    except Exception:
        logger.exception(t("engine/tasks.py:g:174:26"), demande_id)
        demande.statut = "echec"
        demande.erreur = t("engine/tasks.py:g:176:26")
        demande.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# DOCUMENTS GENERES PAR IA — remplace les appels synchrones dans
# api_creer_document / api_regenerer_document / api_executer_document
# (engine/views.py). Une seule tache enchaine generation + execution : elle
# tourne de toute facon sur le worker, pas sur la requete HTTP, donc pas
# besoin d'un vrai chain() Celery pour ce cas simple.
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.generer_et_executer_document")
def generer_et_executer_document_tache(doc_id):
    from .agent import executer_document, generer_script_document
    from .models import DocumentGenere

    try:
        doc = DocumentGenere.objects.get(id=doc_id)
    except DocumentGenere.DoesNotExist:
        logger.warning(t("engine/tasks.py:g:198:24"), doc_id)
        return
    try:
        doc = generer_script_document(doc)
        if doc.statut != "echec":
            executer_document(doc)
    except Exception:
        logger.exception(t("engine/tasks.py:g:205:26"), doc_id)
        doc.statut = "echec"
        doc.erreur = t("engine/tasks.py:g:207:22")
        doc.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.regenerer_document")
def regenerer_document_tache(doc_id):
    from .agent import executer_document, regenerer_script_document
    from .models import DocumentGenere

    try:
        doc = DocumentGenere.objects.get(id=doc_id)
    except DocumentGenere.DoesNotExist:
        logger.warning(_gt("rg6.msg.116"), doc_id)
        return
    try:
        doc = regenerer_script_document(doc)
        if doc.statut == "script_genere":
            executer_document(doc)
    except Exception:
        logger.exception(t("engine/tasks.py:g:228:26"), doc_id)
        doc.statut = "echec"
        doc.erreur = t("engine/tasks.py:g:230:22")
        doc.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.executer_document")
def executer_document_tache(doc_id):
    from .agent import executer_document
    from .models import DocumentGenere

    try:
        doc = DocumentGenere.objects.get(id=doc_id)
    except DocumentGenere.DoesNotExist:
        logger.warning(_gt("rg6.msg.117"), doc_id)
        return
    try:
        executer_document(doc)
    except Exception:
        logger.exception(t("engine/tasks.py:g:249:26"), doc_id)
        doc.statut = "echec"
        doc.erreur = t("engine/tasks.py:g:251:22")
        doc.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# IMPORT FICHIER ASYNC — lit le fichier sauvegardé sur disque,
# crée la table, met à jour TacheImport en temps réel.
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.importer_fichier_async")
def importer_fichier_async(tache_id: int, chemin_fichier: str,
                            feuille=None, separateur=None, encodage=None,
                            type_stockage="sql", mode_restauration="copie", url_source=None, jeton_chiffre=None):
    from django.utils import timezone
    from .models import TacheImport, Entreprise

    try:
        tache = TacheImport.objects.get(id=tache_id)
    except TacheImport.DoesNotExist:
        logger.warning(_gt("rg6.msg.118"), tache_id)
        return

    tache.statut = "en_cours"
    tache.progression = 0
    tache.save(update_fields=["statut", "progression"])

    def progression_cb(pct: int):
        TacheImport.objects.filter(id=tache_id).update(progression=pct)

    try:
        from .import_fichier import importer_fichier_vers_table

        if not chemin_fichier and url_source:   # lien Google Sheet / fichier en ligne : téléchargé ICI (hors requête web), puis lu comme un fichier téléversé
            import shutil, tempfile
            from django.conf import settings as _dj
            from . import maj_table
            from . import source_en_ligne
            jeton = source_en_ligne.dechiffrer_jeton(jeton_chiffre) if jeton_chiffre else None
            if jeton_chiffre and jeton is None: raise ValueError(_gt("maj_table.en_ligne_jeton_illisible"))
            telecharge = maj_table.fichier_source(url_source, None, jeton)
            os.makedirs(_dj.MEDIA_ROOT, exist_ok=True)
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(telecharge.name)[-1] or ".tmp", dir=_dj.MEDIA_ROOT) as tmp:
                shutil.copyfileobj(telecharge, tmp); chemin_fichier = tmp.name
            tache.nom_fichier = telecharge.name[:255]; tache.save(update_fields=["nom_fichier"])

        class _FichierDisque:
            """Fichier disque qui imite Django InMemoryUploadedFile : `name` + tout l'objet fichier binaire.
            pandas (>= 2.x/3.x) exige un objet « file-like » complet pour read_excel / ExcelFile (seekable, readable,
            __iter__...) : on délègue donc tout au fichier ouvert au lieu de n'exposer que read/seek/tell."""
            def __init__(self, path, nom=None):
                self.name = nom or os.path.basename(path)   # nom d'origine (le disque porte un nom temporaire « tmpXXXX »)
                self._path = path
                self._fh = open(path, "rb")
            def __getattr__(self, nom):          # seekable, readable, closed, readline, ...
                return getattr(self._fh, nom)
            def __iter__(self): return iter(self._fh)
            def __enter__(self): return self
            def __exit__(self, *exc): self._fh.close()

        fichier = _FichierDisque(chemin_fichier, tache.nom_fichier)
        try:
            table = importer_fichier_vers_table(
                fichier, tache.entreprise,
                feuille=feuille, separateur=separateur, encodage=encodage,
                progression_cb=progression_cb,
                type_stockage=type_stockage if type_stockage in ("sql", "nosql") else "sql",
                mode_restauration=mode_restauration if mode_restauration in ("copie", "remplacer") else "copie",
            )
        finally:
            fichier.close()
            try:
                os.remove(chemin_fichier)
            except OSError:
                pass

        if url_source:
            from .maj_table import _parts
            _parts(table).update(url_source=url_source, jeton_source=jeton_chiffre or "", lignes_source=sum(p.enregistrements.count() for p in _parts(table)))   # lien d'origine (Google Sheet ou fichier en ligne) mémorisé sur toutes les parties, avec le nombre de lignes déjà lues
        tache.statut = "termine"
        tache.progression = 100
        tache.table = table
        tache.groupe = table.groupe  # renseigné uniquement si le fichier a dû être découpé
        tache.termine_le = timezone.now()
        # Restauration fidèle : ce qui n'a pas pu être rétabli à l'identique (relation vers une table absente, matricule déjà pris) est dit ici.
        tache.erreur = " ".join(getattr(table, "avertissements_restauration", []) or [])[:2000]
        tache.save(update_fields=["statut", "progression", "table", "groupe", "termine_le", "erreur"])

    except Exception as exc:
        logger.exception("Task file import error %s.", tache_id)
        tache.statut = "echec"
        tache.erreur = str(exc)
        tache.termine_le = timezone.now()
        tache.save(update_fields=["statut", "erreur", "termine_le"])
    finally:
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# SYNCHRONISATION BASE DE DONNÉES EXTERNE ASYNC — même pattern que
# l'import fichier ci-dessus (TacheImport partagée, polling identique),
# mais la source est une table/collection sur un serveur distant
# (ConnexionExterne) plutôt qu'un fichier téléversé.
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.maj_gsheet_async")
def maj_gsheet_async(table_id):   # relit le lien d'origine (Google Sheet ou fichier en ligne) de la table et ajoute les nouvelles lignes ; hors ligne : rien ne change, nouvel essai à la prochaine ouverture
    from . import maj_table
    from .models import TableDynamique
    try:
        table = TableDynamique.objects.get(id=table_id)
        if table.jeton_a_reconfigurer: return   # jeton refusé par le serveur distant : inutile de réessayer tant qu'un nouveau n'a pas été saisi (🔑)
        maj_table.maj_depuis_gsheet(table)
    except (ValueError, OSError, TableDynamique.DoesNotExist): logger.info(_gt("rg6.msg.119"), table_id)


@shared_task(name="engine.synchroniser_connexion_externe_async")
def synchroniser_connexion_externe_async(tache_id: int, connexion_id: int, nom_table_distante: str):
    from django.utils import timezone
    from .models import ConnexionExterne, TacheImport

    try:
        tache = TacheImport.objects.get(id=tache_id)
    except TacheImport.DoesNotExist:
        logger.warning(_gt("rg6.msg.118"), tache_id)
        return

    tache.statut = "en_cours"
    tache.progression = 0
    tache.save(update_fields=["statut", "progression"])

    def progression_cb(pct: int):
        TacheImport.objects.filter(id=tache_id).update(progression=pct)

    try:
        connexion = ConnexionExterne.objects.get(id=connexion_id)
        from .connexion_externe import importer_table_distante_vers_table

        table = importer_table_distante_vers_table(
            connexion, nom_table_distante, tache.entreprise,
            progression_cb=progression_cb,
        )

        tache.statut = "termine"
        tache.progression = 100
        tache.table = table
        tache.groupe = table.groupe
        tache.termine_le = timezone.now()
        tache.save(update_fields=["statut", "progression", "table", "groupe", "termine_le"])

    except Exception as exc:
        logger.exception(_gt("rg6.msg.120"), tache_id)
        tache.statut = "echec"
        tache.erreur = str(exc)
        tache.termine_le = timezone.now()
        tache.save(update_fields=["statut", "erreur", "termine_le"])
        ConnexionExterne.objects.filter(id=connexion_id).update(
            derniere_synchro_le=timezone.now(),
            derniere_synchro_statut="echec",
            derniere_synchro_erreur=str(exc),
        )
    finally:
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# GENERATION DE FORMULE — remplace l'appel synchrone dans
# api_generer_formule_calcul (engine/views.py). Meme moteur d'IA que les
# scripts d'analyse (MoteurInference.generer_formule_calcul), mais sur un
# objet GenerationFormule jetable (brouillon avant "Enregistrer la formule").
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.generer_formule_calcul")
def generer_formule_calcul_tache(generation_id):
    from .inference import MoteurInference
    from .metadata import contexte_pour_agent
    from .models import GenerationFormule

    try:
        generation = GenerationFormule.objects.get(id=generation_id)
    except GenerationFormule.DoesNotExist:
        logger.warning(_gt("rg6.msg.121"), generation_id)
        return
    try:
        table = generation.table_reference
        contexte = contexte_pour_agent(table) if table else (
            t("engine/tasks.py:g:410:13")
        )
        moteur = MoteurInference(entreprise=generation.entreprise)
        timeout = table.timeout_inference_secondes if table else None
        code, erreur = moteur.generer_formule_calcul(
            generation.description, generation.cible_type, contexte, timeout=timeout
        )
        if erreur:
            generation.statut = "echec"
            generation.erreur = erreur
            generation.save(update_fields=["statut", "erreur"])
        else:
            generation.code = code or ""
            generation.statut = "pret"
            generation.save(update_fields=["code", "statut"])
    except Exception:
        logger.exception(t("engine/tasks.py:g:427:26"), generation_id)
        generation.statut = "echec"
        generation.erreur = t("engine/tasks.py:g:429:29")
        generation.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.generer_graphique_bi")
def generer_graphique_bi_tache(generation_id):
    from .inference import MoteurInference
    from .metadata import contexte_pour_agent
    from .models import GenerationGraphiqueBI

    try:
        generation = GenerationGraphiqueBI.objects.get(id=generation_id)
    except GenerationGraphiqueBI.DoesNotExist:
        logger.warning(_gt("rg6.msg.122"), generation_id)
        return
    try:
        table = generation.table_reference
        contexte = contexte_pour_agent(table) if table else (
            t("engine/tasks.py:g:449:13")
        )
        moteur = MoteurInference(entreprise=generation.entreprise)
        timeout = table.timeout_inference_secondes if table else None
        code, erreur = moteur.generer_graphique_bi(
            generation.description, generation.cible_type, contexte, timeout=timeout
        )
        if erreur:
            generation.statut = "echec"
            generation.erreur = erreur
            generation.save(update_fields=["statut", "erreur"])
        else:
            generation.code = code or ""
            generation.statut = "pret"
            generation.save(update_fields=["code", "statut"])
    except Exception:
        logger.exception(t("engine/tasks.py:g:465:26"), generation_id)
        generation.statut = "echec"
        generation.erreur = t("engine/tasks.py:g:467:29")
        generation.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.generer_couche_ontologie")
def generer_couche_ontologie_tache(generation_id):
    from .inference import MoteurInference
    from .ontologie import contexte_pour_generation_couche
    from .models import GenerationCoucheOntologie

    try:
        generation = GenerationCoucheOntologie.objects.get(id=generation_id)
    except GenerationCoucheOntologie.DoesNotExist:
        logger.warning(_gt("rg6.msg.123"), generation_id)
        return
    try:
        contexte = contexte_pour_generation_couche(generation.entreprise)
        moteur = MoteurInference(entreprise=generation.entreprise)
        code, erreur = moteur.generer_couche_ontologie(generation.description, contexte)
        if erreur:
            generation.statut = "echec"
            generation.erreur = erreur
            generation.save(update_fields=["statut", "erreur"])
        else:
            generation.code = code or ""
            generation.statut = "pret"
            generation.save(update_fields=["code", "statut"])
    except Exception:
        logger.exception(t("engine/tasks.py:g:497:26"), generation_id)
        generation.statut = "echec"
        generation.erreur = t("engine/tasks.py:g:499:29")
        generation.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# TABLEAUX DE BORD BI — rendu asynchrone d'un widget graphique (voir
# models.WidgetGraphiqueBI / tableau_bord_bi.calculer_widget). Le statut
# "en_cours" est posé AVANT l'appel .delay() côté vue (même pattern que
# DemandeAgent/GenerationFormule), pour que le polling voie tout de suite
# l'état transitoire ; cette tâche se contente de mettre à jour le widget.
# Aucune image n'est jamais écrite sur disque : le résultat (PNG base64)
# reste un champ texte en base — voir tableau_bord_bi.py.
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.calculer_widget_bi")
def calculer_widget_bi_tache(widget_id):
    from .models import WidgetGraphiqueBI
    from .tableau_bord_bi import calculer_widget

    try:
        widget = WidgetGraphiqueBI.objects.get(id=widget_id)
    except WidgetGraphiqueBI.DoesNotExist:
        logger.warning(_gt("rg6.msg.124"), widget_id)
        return
    try:
        calculer_widget(widget)
    except Exception:
        logger.exception(t("engine/tasks.py:g:528:26"), widget_id)
        widget.statut = "echec"
        widget.erreur = t("engine/tasks.py:g:530:25")
        widget.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.recalculer_tableau_bord_bi")
def recalculer_tableau_bord_bi_tache(tableau_bord_id, seulement_perimes=True):
    """Recalcule les widgets d'un tableau de bord BI. seulement_perimes=True
    (par défaut) ne relance que les widgets dont le cache n'est plus à jour
    par rapport aux données actuelles de la table source — c'est ce qui
    permet l'interaction live (une modification de la table déclenche un
    recalcul ciblé, pas un rendu complet à chaque fois)."""
    from .models import TableauBordBI, WidgetGraphiqueBI
    from .tableau_bord_bi import calculer_widget, widgets_a_recalculer

    try:
        tableau_bord = TableauBordBI.objects.get(id=tableau_bord_id)
    except TableauBordBI.DoesNotExist:
        logger.warning(_gt("rg6.msg.125"), tableau_bord_id)
        return

    try:
        widgets = widgets_a_recalculer(tableau_bord) if seulement_perimes else list(
            tableau_bord.widgets.select_related("graphique").prefetch_related("champs_cibles")
        )
        for widget in widgets:
            WidgetGraphiqueBI.objects.filter(id=widget.id).update(statut="en_cours")
            try:
                calculer_widget(widget)
            except Exception:
                logger.exception(t("engine/tasks.py:g:561:34"), widget.id, tableau_bord_id)
                widget.statut = "echec"
                widget.erreur = t("engine/tasks.py:g:563:33")
                widget.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# TACHES PLANIFIEES — generation du script (IA) puis execution, ou execution
# seule du script existant (voir engine/agent.py::generer_script_tache /
# executer_tache et les vues api_creer_tache / api_executer_tache).
# ─────────────────────────────────────────────────────────────

def _lancer_tache_planifiee(tache_id, generer, contexte_execution=None):
    from .agent import executer_tache, generer_script_tache
    from .models import TachePlanifiee

    try:
        tache = TachePlanifiee.objects.get(id=tache_id)
    except TachePlanifiee.DoesNotExist:
        logger.warning(_gt("rg6.msg.126"), tache_id)
        return
    if not tache.actif:
        # Tâche suspendue (⏸) entre-temps : une exécution planifiée peut déjà
        # être en file d'attente Celery (countdown posé avant la suspension,
        # voir agent.executer_tache) — on l'annule ici plutôt que de relancer
        # une tâche que l'utilisateur a explicitement mise en pause.
        return
    try:
        if generer:
            generer_script_tache(tache)
            if tache.statut != "script_genere":
                return  # echec_generation : deja consigne par generer_script_tache
        executer_tache(tache, contexte_execution)
    except Exception:
        logger.exception(t("engine/tasks.py:g:597:26"), tache_id)
        tache.statut = "echec"
        tache.erreur = t("engine/tasks.py:g:599:24")
        tache.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.generer_et_executer_tache")
def generer_et_executer_tache_tache(tache_id, contexte_execution=None):
    _lancer_tache_planifiee(tache_id, generer=True, contexte_execution=contexte_execution)


@shared_task(name="engine.executer_tache_planifiee")
def executer_tache_planifiee_tache(tache_id, contexte_execution=None):
    _lancer_tache_planifiee(tache_id, generer=False, contexte_execution=contexte_execution)


@shared_task(name="engine.generer_script_tache_seul")
def generer_script_tache_seul(tache_id):
    """Génère le script (phase 1 seulement, sans exécuter) — utilisé à la
    création d'une tâche CONDITIONNELLE (voir engine/declencheurs.py) : le
    script doit être prêt à l'avance, mais ne s'exécute qu'au premier
    événement réel qui remplit la condition, pas immédiatement."""
    from .agent import generer_script_tache
    from .models import TachePlanifiee

    try:
        tache = TachePlanifiee.objects.get(id=tache_id)
    except TachePlanifiee.DoesNotExist:
        logger.warning(_gt("rg6.msg.126"), tache_id)
        return
    try:
        generer_script_tache(tache)
    except Exception:
        logger.exception(t("engine/tasks.py:g:632:26"), tache_id)
        tache.statut = "echec_generation"
        tache.erreur = t("engine/tasks.py:g:634:24")
        tache.save(update_fields=["statut", "erreur"])
    finally:
        _fermer_connexion_db()


@shared_task(name="engine.entretenir_modele_fantome")
def entretenir_modele_fantome(entreprise_id):
    """Fine-tuning paresseux du modèle fantôme après une nouvelle donnée (voir memoire_decision.planifier_entretien)."""
    from . import memoire_decision, portail
    from .fusion import fusionner_etat
    from .models import Entreprise
    from .recommandations import evaluer

    try:
        entreprise = Entreprise.objects.get(id=entreprise_id)
        from . import base_par_entreprise as _bpe
        with _bpe.dans_entreprise(entreprise.id):   # ISOLATION : l'état fusionné et le graphe sont lus dans la base de cette entreprise seulement
            etat = fusionner_etat(entreprise, forcer=True)
            memoire_decision.entretenir(entreprise, etat, portail.graphe_dependances(entreprise, forcer=True), evaluer(etat))
    except Entreprise.DoesNotExist:
        return
    except Exception:
        logger.exception(t("engine/tasks.py:g:655:26"), entreprise_id)
    finally:
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# FUSION D'UN GROUPE DE TABLES ASYNC — même suivi que l'import fichier
# (TacheImport + polling /api/import/<id>/statut/) : une grosse fusion ne bloque plus la requête HTTP.
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.fusionner_groupe_async")
def fusionner_groupe_async(tache_id: int, groupe_id: int, nom=None):
    from django.utils import timezone
    from . import groupe_fusion
    from .models import GroupeTables, TacheImport

    try:
        tache = TacheImport.objects.get(id=tache_id)
    except TacheImport.DoesNotExist:
        logger.warning(_gt("rg6.msg.118"), tache_id)
        groupe_fusion.liberer_verrou(groupe_id)
        return
    tache.statut = "en_cours"
    tache.progression = 0
    tache.save(update_fields=["statut", "progression"])
    try:
        groupe = GroupeTables.objects.get(id=groupe_id, entreprise=tache.entreprise)
        table = groupe_fusion.fusionner(
            groupe, nom=nom, progression_cb=lambda pct: TacheImport.objects.filter(id=tache_id).update(progression=pct))
        tache.statut = "termine"
        tache.progression = 100
        tache.table = table
        tache.erreur = " ".join(getattr(table, "avertissements_fusion", []) or [])[:2000]
        tache.termine_le = timezone.now()
        tache.save(update_fields=["statut", "progression", "table", "erreur", "termine_le"])
    except Exception as exc:
        logger.exception(_gt("rg6.msg.127"), tache_id)
        tache.statut = "echec"
        tache.erreur = str(exc)
        tache.termine_le = timezone.now()
        tache.save(update_fields=["statut", "erreur", "termine_le"])
    finally:
        groupe_fusion.liberer_verrou(groupe_id)
        _fermer_connexion_db()


# ─────────────────────────────────────────────────────────────
# SUPPRESSION EN MASSE (tables / groupe de tables) — voir engine/suppression_masse.py ; même suivi que l'import fichier.
# ─────────────────────────────────────────────────────────────

@shared_task(name="engine.supprimer_tables_async")
def supprimer_tables_async(tache_id: int, table_ids, groupe_id=None):
    from django.utils import timezone
    from . import suppression_masse
    from .models import GroupeTables, TableDynamique, TacheImport

    try:
        tache = TacheImport.objects.get(id=tache_id)
    except TacheImport.DoesNotExist:
        logger.warning("Suppression en masse : tâche %s introuvable", tache_id)
        return
    tache.statut = "en_cours"
    tache.progression = 0
    tache.save(update_fields=["statut", "progression"])
    try:
        tables = list(TableDynamique.objects.filter(id__in=table_ids, entreprise=tache.entreprise))   # reprise : celles déjà parties n'y sont plus
        groupe = GroupeTables.objects.filter(id=groupe_id, entreprise=tache.entreprise).first() if groupe_id else None
        suppression_masse.supprimer_tables(
            tables, groupe, tache.utilisateur_id,
            progression_cb=lambda pct: TacheImport.objects.filter(id=tache_id).update(progression=pct))
        tache.statut, tache.progression = "termine", 100
        tache.termine_le = timezone.now()
        tache.save(update_fields=["statut", "progression", "termine_le"])
    except Exception as exc:
        logger.exception("Suppression en masse : tâche %s en échec", tache_id)
        tache.statut, tache.erreur, tache.termine_le = "echec", str(exc)[:2000], timezone.now()
        tache.save(update_fields=["statut", "erreur", "termine_le"])
    finally:
        _fermer_connexion_db()

