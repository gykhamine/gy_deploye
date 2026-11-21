# ─────────────────────────────────────────────────────────────
# CLÔTURE DE CYCLE : bouton « Clôturer » et bouton « Restaurer une archive ».
#
# L'archive (zip) contient TOUT l'environnement de l'entreprise, pour qu'une
# restauration le retrouve à l'identique :
#   - donnees_entreprise.json : instantané de TOUS les objets rattachés à
#     l'entreprise (tables, champs, lignes, valeurs, fusions de cellules,
#     formules, KPI, tableaux de bord BI, pages d'ontologie, modèles IA et leur
#     historique d'inférences, configuration IA, documents, journaux, tâches,
#     connexions externes, skills, etc.) — découverts AUTOMATIQUEMENT par
#     introspection des modèles : un futur modèle rattaché à l'entreprise est
#     archivé sans modifier ce fichier ;
#   - media/… : les fichiers associés — modèles entraînés (.gy, y compris les
#     sauvegardes avant-finetune), fichiers uploadés dans les champs, tables
#     NoSQL, scripts générés par l'IA, rapports/documents PDF, logos, cachets,
#     signatures, cartes ;
#   - manifeste.json : version du format, date, entreprise, comptage par
#     modèle, liste des fichiers ;
#   - rapports/ : résumé général PDF de chaque table (jetable, ignoré à la
#     restauration).
# TOUT ce qui appartient à l'entreprise est archivé et restauré : y compris la
# fiche de l'entreprise, ses membres (rôles, droits d'accès), les clés API
# d'accès externe, les wallets, abonnements, frais et taux de change.
# Seules exceptions : les archives de cycle elles-mêmes (sinon une archive
# contiendrait les précédentes), et les comptes utilisateurs Django.
# La CLÔTURE conserve la fiche de l'entreprise et ses membres (on se verrouillerait
# dehors). La RESTAURATION est une image disque : tout revient à l'état de l'archive,
# membres compris, sauf l'administrateur qui restaure (jamais verrouillé dehors).
# Les anciennes archives (format 1 : donnees_tables.json seul) restent
# restaurables.
# Le MODÈLE FANTÔME de l'entreprise (réseaux .agentgy et registre de construction, stockés hors
# MEDIA_ROOT/systeme/modeles_fantomes) est archivé sous fantome/ et remis en place à la
# restauration : rien n'est perdu. Les réseaux sont signés avec la SECRET_KEY du serveur : sur un
# autre serveur, un réseau signé autrement est mis de côté (.invalide) au chargement et le modèle repart.
#
# DEUX SENS (même fenêtre, même bouton « Restaurer ce fichier », détectés par manifeste.json) :
#   - mode « image » (clôture, images automatiques) : tout, wallets compris, restauration à l'identique ;
#   - mode « modele » (generer_zip_modele_vers) : la STRUCTURE seule (tables, champs, formules, tableaux
#     de bord, ontologies, configuration ; ni lignes, ni wallets, ni membres, ni clés, ni journaux, ni
#     modèle fantôme) pour la transférer chez un client. Son import est ADDITIF (rien n'est effacé),
#     donne de nouveaux identifiants et se rattache à l'entreprise du client (transfert_modele.py).
# DROITS SUR LA STRUCTURE : seul l'administrateur peut tout (toute l'entreprise, ou l'espace de quelques agents). Un AGENT est
#   confiné à son espace (ses tables autorisées) : il n'exporte que celui-ci (jamais la configuration de l'entreprise, ni les
#   ontologies) et ce qu'il importe est limité aux tables et à ce qui en dépend, puis ajouté à son espace. Voir perimetre_tables.
# ─────────────────────────────────────────────────────────────
import contextlib
import datetime
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile

from django.apps import apps as django_apps
from django.conf import settings
from django.core import serializers
from django.db import models, transaction

from . import transfert_modele as _tm
from .stockage import SYSTEME, dossier_systeme
from .models import GroupeTables, MembreEntreprise, TableDynamique
from .reports import generer_pdf_resume_general
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402
from engine.i18n import tf as _gtf  # noqa: E402

logger = logging.getLogger(__name__)

NOM_JSON = "donnees_tables.json"            # format 1 (tables seules), lu pour compatibilité
NOM_JSON_COMPLET = "donnees_entreprise.json"  # format 2 (tout)
NOM_MANIFESTE = "manifeste.json"
NOM_JSON_MODELE = "structure_entreprise.json"  # archive « modèle » : structure sans aucune donnée (transfert chez un client)
FORMAT_VERSION = 2

# Non archivés (voir l'en-tête) : les archives elles-mêmes.
_EXCLUS = {"ArchiveCycle"}
# Archivés et restaurés, mais jamais supprimés par la clôture/restauration.
_CONSERVES = {"Entreprise", "MembreEntreprise"}
# Dossiers de MEDIA_ROOT propres à l'entreprise (hors fichiers référencés par un FileField).
_PROFONDEUR_MAX = 3


# ── Découverte des modèles rattachés à l'entreprise ──────────────────────
def _modeles_entreprise():
    """[(modèle, [chemins de filtre vers l'entreprise])] — introspection : un modèle est
    retenu s'il a une FK/O2O vers Entreprise, ou vers un modèle déjà retenu (jusqu'à
    _PROFONDEUR_MAX niveaux). Les M2M implicites sont sérialisés avec leur modèle."""
    from .models import Entreprise
    modeles = [m for m in django_apps.get_app_config("engine").get_models()
               if not m._meta.auto_created and m.__name__ not in _EXCLUS]
    chemins = {Entreprise: ["pk"]}  # la fiche de l'entreprise elle-même
    for m in modeles:
        if m is Entreprise:
            continue
        directs = [f.name for f in m._meta.concrete_fields
                   if isinstance(f, models.ForeignKey) and f.related_model is Entreprise]
        if directs:
            chemins[m] = list(directs)
    for _ in range(_PROFONDEUR_MAX):
        nouveau = False
        for m in modeles:
            if m in chemins:
                continue
            trouves = []
            for f in m._meta.concrete_fields:
                cible = getattr(f, "related_model", None)
                if isinstance(f, models.ForeignKey) and cible in chemins and cible is not Entreprise:
                    trouves += [f"{f.name}__{c}" for c in chemins[cible]]
            if trouves:
                chemins[m] = trouves
                nouveau = True
        if not nouveau:
            break
    return [(m, chemins[m]) for m in modeles if m in chemins]


def _chemin_filtre(c, entreprise):
    return {"pk": entreprise.pk} if c == "pk" else {c: entreprise}


def _queryset(modele, chemins, entreprise):
    filtre = models.Q()
    for c in chemins:
        filtre |= models.Q(**_chemin_filtre(c, entreprise))
    return modele._default_manager.filter(filtre).distinct()


def _objets_entreprise(entreprise):
    """Tous les objets archivables (liste plate) + comptage par modèle."""
    objets, comptage = [], {}
    for modele, chemins in _modeles_entreprise():
        liste = list(_queryset(modele, chemins, entreprise))
        if liste:
            comptage[modele._meta.label] = len(liste)
            objets += liste
    return objets, comptage


# ── Fichiers médias ──────────────────────────────────────────────────────
def _chemins_medias(entreprise, objets):
    """Chemins relatifs (sous MEDIA_ROOT) des fichiers à archiver : FileField des objets,
    dossiers des modèles IA / tables NoSQL / scripts / uploads de champs de l'entreprise."""
    racine = os.path.realpath(settings.MEDIA_ROOT)
    trouves = set()

    def _ajouter_fichier(rel):
        if not rel:
            return
        absolu = os.path.realpath(os.path.join(racine, str(rel)))
        if absolu.startswith(racine + os.sep) and os.path.isfile(absolu):
            trouves.add(os.path.relpath(absolu, racine).replace(os.sep, "/"))

    def _ajouter_dossier(rel):
        absolu = os.path.realpath(os.path.join(racine, rel))
        if not absolu.startswith(racine + os.sep) or not os.path.isdir(absolu):
            return
        for dossier, _sous, fichiers in os.walk(absolu):
            for nom in fichiers:
                _ajouter_fichier(os.path.relpath(os.path.join(dossier, nom), racine))

    for o in objets:
        for f in o._meta.concrete_fields:
            if isinstance(f, models.FileField):
                valeur = getattr(o, f.name)
                if valeur:
                    _ajouter_fichier(valeur.name)
        # Modèles IA : chemin du .gy stocké en texte simple.
        if o._meta.model_name == "modeleia":
            _ajouter_fichier(getattr(o, "fichier_modele", ""))

    _ajouter_dossier(f"modeles_ia/{entreprise.slug}")
    _ajouter_dossier(f"nosql_tables/{entreprise.id}")
    for slug in TableDynamique.objects.filter(entreprise=entreprise).values_list("slug", flat=True):
        for base in ("champs", "scripts_agent", "scripts_documents"):
            _ajouter_dossier(f"{base}/{slug}")
    return sorted(trouves)


# ── Fichiers Parquet des tables (image fidèle de l'EAV, voir parquet_store.py) ──────────────────────────
def _fichiers_parquet(entreprise):
    """Chemins relatifs (sous MEDIA_ROOT) des fichiers Parquet de l'entreprise : socles (.parquet) et deltas (.parquet.delta). Archivés tels
    quels pour que la restauration n'ait RIEN à reconstruire ; hors de l'empreinte de l'environnement (le fichier suit l'EAV, déjà dans
    l'empreinte : une reconstruction sans changement de données ne doit pas déclencher une image automatique)."""
    dossier = os.path.join(os.path.realpath(settings.MEDIA_ROOT), "parquet_tables", str(entreprise.id))
    if not os.path.isdir(dossier):
        return []
    return sorted(f"parquet_tables/{entreprise.id}/{n}" for n in os.listdir(dossier)
                  if n.endswith((".parquet", ".parquet.delta")) and os.path.isfile(os.path.join(dossier, n)))


def _purger_parquet(entreprise):
    """Restauration à l'identique : les fichiers Parquet actuels de l'entreprise disparaissent (ceux de l'archive les remplacent ; une table
    absente de l'archive ne laisse pas de fichier orphelin)."""
    racine = os.path.realpath(settings.MEDIA_ROOT)
    for rel in _fichiers_parquet(entreprise):
        try:
            os.remove(os.path.join(racine, rel))
        except OSError:
            pass


# ── Documents administratifs ─────────────────────────────────────────────────────────────────────────
def _controle_documents_administratifs(entreprise):
    """{"emis": nombre de PDF figés, "alteres": [références]} : chaque PDF figé est relu et son empreinte SHA-256 comparée à celle enregistrée
    à l'émission (emission_documents.octets_figes). Une pièce altérée ou disparue est NOMMÉE, jamais archivée ni restaurée en silence."""
    from .emission_documents import ErreurEmission, octets_figes
    from .models import DocumentAdministratif, SyntheseRedigee
    emis, alteres = 0, []
    for d in DocumentAdministratif.objects.filter(entreprise=entreprise).exclude(pdf_fige="").exclude(pdf_fige__isnull=True):
        emis += 1
        try:
            octets_figes(d)
        except ErreurEmission:
            alteres.append(d.reference or f"#{d.pk}")
    for s in SyntheseRedigee.objects.filter(entreprise=entreprise).exclude(pdf_fige="").exclude(pdf_fige__isnull=True):     # synthèses finalisées : même contrôle
        emis += 1
        try:
            octets_figes(s)
        except ErreurEmission:
            alteres.append(f"synthese-{s.pk}")
    return {"emis": emis, "alteres": alteres}


# ── Modèle fantôme (MEDIA_ROOT/systeme, jamais servi par HTTP) ──────────────────────────────────────
def _dossier_fantome():
    return str(dossier_systeme("modeles_fantomes"))


def _nom_fantome_valide(nom, entreprise):
    """Fichiers du modèle fantôme de CETTE entreprise : réseaux (entreprise_<id>[_n].agentgy), registre de construction et modèles
    fantôme d'ontologie de l'entreprise (ontologie_e<id>.agentgy ; anciens fichiers par utilisateur ontologie_e<id>_u<n>.agentgy acceptés)."""
    return bool(re.fullmatch(rf"entreprise_{entreprise.id}(_\d+)?\.agentgy|construction_{entreprise.id}\.json|ontologie_e{entreprise.id}(_u\d+)?\.agentgy", nom))


def _fichiers_fantome(entreprise):
    d = _dossier_fantome()
    if not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d) if _nom_fantome_valide(n, entreprise) and os.path.isfile(os.path.join(d, n)))


def _restaurer_fantome(zf, entreprise, manifeste):
    """Image disque : le modèle fantôme revient tel qu'il était à l'archive (les fichiers actuels qui n'y étaient pas disparaissent).
    Archive sans rubrique « fantome » (anciennes archives) : le modèle actuel est laissé tel quel."""
    if manifeste.get("fantome") is None:
        return
    d = _dossier_fantome()
    os.makedirs(d, exist_ok=True)
    for nom in _fichiers_fantome(entreprise):
        try:
            os.remove(os.path.join(d, nom))
        except OSError:
            pass
    for nom in zf.namelist():
        if not nom.startswith("fantome/") or nom.endswith("/"):
            continue
        court = nom[len("fantome/"):]
        if "/" in court or not _nom_fantome_valide(court, entreprise):
            continue  # jamais d'écriture hors du dossier, ni pour une autre entreprise
        tmp = os.path.join(d, "." + court + ".tmp")
        with zf.open(nom) as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(tmp, os.path.join(d, court))


# ── Génération ───────────────────────────────────────────────────────────
def generer_zip_cloture_vers(entreprise, cible, si_different_de=None, avec_tuiles=False):
    """Écrit l'archive complète dans `cible` (fichier binaire ouvert en écriture/seek) :
    les gros fichiers (modèles entraînés) sont copiés en flux, jamais chargés en mémoire.
    Renvoie l'empreinte de l'environnement archivé. Si `si_different_de` (empreinte de la
    dernière image) est donnée et que rien n'a changé depuis, n'écrit RIEN et renvoie None
    (images automatiques : pas de copie inutile des gros modèles à chaque passage).
    `avec_tuiles` (bouton « Clôturer ») : embarque aussi le cache de tuiles de la carte (tuiles_geo/z/x/y.png) pour que la carte reste utilisable
    hors ligne après restauration. Volontairement hors de l'empreinte : le cache grossit sans cesse, il ne doit pas déclencher d'images inutiles."""
    objets, comptage = _objets_entreprise(entreprise)
    medias = _chemins_medias(entreprise, objets)
    donnees = serializers.serialize("json", objets)
    racine = os.path.realpath(settings.MEDIA_ROOT)
    h = hashlib.sha256(donnees.encode("utf-8"))
    for rel in medias:
        st = os.stat(os.path.join(racine, rel))
        h.update(f"{rel}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8"))
    fantome = _fichiers_fantome(entreprise)
    for nom in fantome:
        st = os.stat(os.path.join(_dossier_fantome(), nom))
        h.update(f"fantome/{nom}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8"))
    empreinte = h.hexdigest()
    if si_different_de and si_different_de == empreinte:
        return None
    try:
        controle_documents = _controle_documents_administratifs(entreprise)
    except Exception:
        logger.exception(_gt("rg6.msg.007"), entreprise.pk)
        controle_documents = None
    if controle_documents and controle_documents["alteres"]:
        logger.warning(_gt("rg6.msg.008"),
                       len(controle_documents["alteres"]), controle_documents["alteres"])
    parquet_ecrits = []
    manifeste = {
        "format": FORMAT_VERSION,
        "mode": "image",
        "cree_le": datetime.datetime.now().isoformat(timespec="seconds"),
        "entreprise_id": entreprise.pk,
        "entreprise_slug": entreprise.slug,
        "objets": comptage,
        "medias": medias,
        "fantome": fantome,
        "documents_administratifs": controle_documents,
    }
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        from .groupe_fusion import sans_parties_fusionnees   # groupe fusionné : un seul rapport (celui de la table fusionnée), pas un par partie
        for table in sans_parties_fusionnees(TableDynamique.objects.filter(entreprise=entreprise)):
            try:
                zf.writestr(f"rapports/{table.slug}_resume_general.pdf", generer_pdf_resume_general(table))
            except Exception:
                continue  # un rapport PDF raté ne doit jamais empêcher l'archive de l'environnement
        zf.writestr(NOM_JSON_COMPLET, donnees)
        for rel in medias:
            zf.write(os.path.join(racine, rel), f"media/{rel}")
        for nom in fantome:
            zf.write(os.path.join(_dossier_fantome(), nom), f"fantome/{nom}")
        for rel in _fichiers_parquet(entreprise):
            try:
                zf.write(os.path.join(racine, rel), f"media/{rel}")
                parquet_ecrits.append(rel)
            except OSError:
                continue   # fichier remplacé ou retiré pendant la clôture : il sera reconstruit à la restauration, jamais bloquant
        manifeste["parquet"] = parquet_ecrits
        if avec_tuiles:
            try:
                from . import geo_tuiles
                manifeste["tuiles"] = geo_tuiles.ecrire_dans_zip(dossier_systeme("geo_tuiles"), zf)
            except Exception:
                pass  # un cache de tuiles illisible ne doit jamais empêcher la clôture
        zf.writestr(NOM_MANIFESTE, json.dumps(manifeste, ensure_ascii=False, indent=2))
    return empreinte


# ── Archive « modèle » : la structure sans données (transfert chez un client) ──
def _etiquette_utilisateur():
    from django.contrib.auth import get_user_model
    return get_user_model()._meta.label_lower


def _meta_modeles():
    """Description des modèles pour transfert_modele : clés étrangères (cible, nullable), M2M, clé primaire entière ou non."""
    meta = {}
    for m in django_apps.get_app_config("engine").get_models():
        fk = {f.name: (f.related_model._meta.label_lower, bool(f.null)) for f in m._meta.concrete_fields
              if isinstance(f, models.ForeignKey)}
        m2m = {f.name: f.related_model._meta.label_lower for f in m._meta.many_to_many}
        entier = m._meta.pk.get_internal_type() in ("AutoField", "BigAutoField", "SmallAutoField", "IntegerField", "BigIntegerField")
        meta[m._meta.label_lower] = {"fk": fk, "m2m": m2m, "pk_int": entier}
    return meta


def perimetre_tables(entreprise, membre, agents=None):
    """Périmètre de structure qu'un membre peut exporter : None = TOUTE l'entreprise (administrateur seulement, sans `agents`) ;
    sinon l'ensemble des identifiants de tables de l'espace. Un administrateur peut cibler quelques agents (`agents` : identifiants de
    membres) : l'espace est alors la réunion de leurs tables autorisées. Un agent n'a que le sien (ses tables autorisées).
    Refus (ValueError) : « espace_non_defini » (un agent sans liste de tables autorisées : l'administrateur doit d'abord borner son
    espace, il n'exporte jamais « tout par défaut »), « aucune_table » (les agents ciblés n'ont aucune table), « droits_insuffisants »
    (un client). PermissionError n'est pas utilisé : les vues traduisent ces messages."""
    if membre is None or membre.entreprise_id != entreprise.id:
        raise ValueError("droits_insuffisants")
    from .delegation import a_pouvoir
    if a_pouvoir(membre, "toutes_tables"):
        if not agents:
            return None
        tables = set()
        for a in MembreEntreprise.objects.filter(entreprise=entreprise, id__in=list(agents), role="agent"):
            tables |= set(a.tables_autorisees or ())
        if not tables:
            raise ValueError("aucune_table")
        return tables
    if membre.role == "agent":
        if not membre.tables_autorisees:
            raise ValueError("espace_non_defini")
        return set(membre.tables_autorisees)
    raise ValueError("droits_insuffisants")


def generer_zip_modele_vers(entreprise, cible, tables=None):
    """Écrit dans `cible` l'archive « modèle » : la STRUCTURE de l'entreprise (liste blanche transfert_modele.STRUCTURELS) sans aucune ligne,
    ni wallet, ni membre, ni clé, ni journal, ni modèle fantôme ; les secrets de configuration sont vidés. Ne modifie rien.
    `tables` (identifiants, voir perimetre_tables) : se limite à CET espace (transfert_modele.restreindre_a_tables : ni configuration de
    l'entreprise, ni ontologies, rien des tables hors espace). None : toute l'entreprise."""
    objets = []
    for modele, chemins in _modeles_entreprise():
        if modele._meta.label_lower in _tm.STRUCTURELS:
            objets += list(_queryset(modele, chemins, entreprise))
    dicts = json.loads(serializers.serialize("json", objets))
    if tables is None:
        dicts, ecartes = _tm.neutraliser(dicts, _meta_modeles(), comptes=(_etiquette_utilisateur(),))
    else:
        dicts, ecartes = _tm.restreindre_a_tables(dicts, _meta_modeles(), tables, comptes=(_etiquette_utilisateur(),))
    _tm.purger_secrets(dicts)
    comptage = {}
    for o in dicts:
        comptage[o["model"]] = comptage.get(o["model"], 0) + 1
    manifeste = {
        "format": FORMAT_VERSION, "mode": "modele",
        "cree_le": datetime.datetime.now().isoformat(timespec="seconds"),
        "entreprise_slug": entreprise.slug, "objets": comptage, "ecartes": sorted(set(ecartes)),
        "portee": "entreprise" if tables is None else "espace",
    }
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        zf.writestr(NOM_JSON_MODELE, json.dumps(dicts, ensure_ascii=False))
        zf.writestr(NOM_MANIFESTE, json.dumps(manifeste, ensure_ascii=False, indent=2))
    return comptage


def generer_zip_cloture(entreprise) -> bytes:
    """Compatibilité : archive en mémoire (préférer generer_zip_cloture_vers pour les gros volumes)."""
    tampon = io.BytesIO()
    generer_zip_cloture_vers(entreprise, tampon)
    return tampon.getvalue()


def _supprimer_donnees_entreprise(entreprise, membres_aussi=False, garder_utilisateur=None):
    """Supprime tout ce que l'archive contient (lignes en base), sauf _CONSERVES.
    `membres_aussi` (restauration « image disque ») : supprime aussi les membres, sauf celui de
    `garder_utilisateur` (l'administrateur qui restaure ne doit jamais se verrouiller dehors)."""
    for modele, chemins in _modeles_entreprise():
        if modele.__name__ in _CONSERVES:
            if membres_aussi and modele.__name__ == "MembreEntreprise":
                reste = _queryset(modele, chemins, entreprise)
                if garder_utilisateur is not None:
                    reste = reste.exclude(utilisateur=garder_utilisateur)
                reste.delete()
            continue
        _queryset(modele, chemins, entreprise).delete()


def reinitialiser_cycle(entreprise):
    """« Redémarrer l'interface de zéro » : remet TOUT l'environnement de l'entreprise à
    zéro (tables, modèles IA entraînés, configuration, tableaux de bord, ontologies,
    documents, wallets, fichiers…). Seules sont conservées la fiche de l'entreprise, ses
    membres (comptes et droits) et les archives de cycle. À n'appeler QU'APRÈS la création
    de l'archive (cycle.generer_zip_cloture_vers)."""
    objets, _ = _objets_entreprise(entreprise)
    garder = [o for o in objets if o._meta.object_name in _CONSERVES]
    fichiers = set(_chemins_medias(entreprise, [o for o in objets if o not in garder]))
    with transaction.atomic():
        _supprimer_donnees_entreprise(entreprise)
    racine = os.path.realpath(settings.MEDIA_ROOT)
    for rel in fichiers:
        try:
            os.remove(os.path.join(racine, rel))
        except OSError:
            pass


# ── Restauration ─────────────────────────────────────────────────────────
def _verifier_entreprise(objets, entreprise):
    """Refuse une archive d'une autre entreprise — vérifié avant toute écriture."""
    for o in objets:
        champs = o["fields"]
        if o["model"] == "engine.entreprise" and o["pk"] != entreprise.pk:
            raise ValueError("archive_entreprise_differente")
        if "entreprise" in champs and champs["entreprise"] != entreprise.pk:
            raise ValueError("archive_entreprise_differente")


def _vider_environnement(entreprise, garder_utilisateur=None):
    """Supprime ce que l'archive va recréer, MEMBRES COMPRIS (restauration à l'identique, comme
    une image disque : ceux ajoutés depuis l'archive disparaissent), sauf le membre de
    `garder_utilisateur`. Sans utilisateur identifié (appel automatique), les membres sont
    conservés par sécurité. La fiche de l'entreprise est simplement réécrite."""
    _supprimer_donnees_entreprise(entreprise, membres_aussi=garder_utilisateur is not None,
                                  garder_utilisateur=garder_utilisateur)


def _extraire_medias(zf, entreprise, ecraser=True):
    racine = os.path.realpath(settings.MEDIA_ROOT)
    for nom in zf.namelist():
        if not nom.startswith("media/") or nom.endswith("/"):
            continue
        rel = nom[len("media/"):]
        if rel.startswith(SYSTEME + "/"):
            continue  # la base et le modèle fantôme sont dans media/systeme/ : une archive ne les écrase jamais
        cible = os.path.realpath(os.path.join(racine, rel))
        if not cible.startswith(racine + os.sep):
            continue  # jamais d'écriture hors de MEDIA_ROOT (« ../ » dans l'archive)
        if not ecraser and os.path.exists(cible):
            continue  # saison restaurée : un fichier de la saison en cours n'est jamais écrasé
        os.makedirs(os.path.dirname(cible), exist_ok=True)
        with zf.open(nom) as src, open(cible, "wb") as dst:
            shutil.copyfileobj(src, dst)


# Jamais dupliqués dans une saison : identité, secrets et chaînes à unicité globale (clés d'accès, blocs, fournisseurs de paiement).
_HORS_SAISON = {"engine.entreprise", "engine.membreentreprise", "engine.cleapi", "engine.bloc", "engine.blocsysteme", "engine.fournisseurpaiement"}


def restaurer_en_saison(entreprise, zf, objets, utilisateur):
    """Restaure une clôture dans une entreprise SŒUR (« saison ») : la saison en cours n'est jamais touchée (ni lignes, ni fichiers).
    Identifiants renumérotés (transfert_modele.remapper), lecture seule jusqu'à ce que l'admin ouvre l'écriture. Les membres actuels y sont
    rattachés (admin → admin, les autres → lecteur) sans devenir actifs : chacun bascule quand il veut (views_saisons). Renvoie la saison."""
    from django.core.management.color import no_style
    from django.db import connection
    from .models import Entreprise, MembreEntreprise
    entreprise = entreprise.saison_de or entreprise   # restaurer depuis une saison : la nouvelle est sœur de la saison en cours, jamais fille d'une saison
    objets = [o for o in objets if o["model"] not in _HORS_SAISON]
    for o in objets:   # champs à unicité globale : régénérés (la saison en cours garde les siens)
        if o["model"] == "engine.enregistrement":
            o["fields"]["uid"] = str(uuid.uuid4())
        elif o["model"] == "engine.documentadministratif":
            o["fields"]["code_verification"] = None
    horodate = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    premier = lambda e: (django_apps.get_model(e)._default_manager.aggregate(m=models.Max("pk"))["m"] or 0) + 1
    with transaction.atomic():
        saison = Entreprise.objects.create(nom=_gtf("rg8.msg.005", nom=entreprise.nom, horodate=horodate), slug=f"{entreprise.slug}-s{horodate:.19}".replace(" ", "-").replace(":", ""), saison_de=entreprise)
        objets = _tm.remapper(objets, _meta_modeles(), saison.pk, premier, substituts={_etiquette_utilisateur(): getattr(utilisateur, "pk", None)})
        for obj in serializers.deserialize("json", json.dumps(objets)):
            obj.save()
        for m in entreprise.membres.all():
            MembreEntreprise.objects.create(utilisateur=m.utilisateur, entreprise=saison, role="admin" if m.role == "admin" else "lecteur", actif=False)
        with connection.cursor() as curseur:   # PostgreSQL : les séquences rattrapent les identifiants explicites
            for sql in connection.ops.sequence_reset_sql(no_style(), [django_apps.get_model(e) for e in {o["model"] for o in objets}]):
                curseur.execute(sql)
    _extraire_medias(zf, saison, ecraser=False)
    return saison


def restaurer_zip_cycle(entreprise, fichier_zip, garder_utilisateur=None, remplacer=True):
    """Reconstruit l'environnement depuis une archive de clôture : base de données
    (transaction unique — tout ou rien) PUIS fichiers médias. Format 1 (anciennes archives) :
    tables/champs/lignes/valeurs seulement, comme avant."""
    with zipfile.ZipFile(fichier_zip) as zf:
        manifeste = json.loads(zf.read(NOM_MANIFESTE).decode("utf-8")) if NOM_MANIFESTE in zf.namelist() else {}
        if manifeste.get("mode") == "modele":
            return importer_modele(entreprise, zf, garder_utilisateur)
        complet = NOM_JSON_COMPLET in zf.namelist()
        brut = zf.read(NOM_JSON_COMPLET if complet else NOM_JSON).decode("utf-8")
        objets = json.loads(brut)
        if complet and not remplacer:   # restauration NON destructive : nouvelle saison (entreprise sœur), la saison en cours n'est pas touchée
            return restaurer_en_saison(entreprise, zf, objets, garder_utilisateur)
        if complet:
            _verifier_entreprise(objets, entreprise)
            if garder_utilisateur is not None:
                # Le membre de l'administrateur qui restaure reste tel qu'il est (pas de doublon).
                objets = [o for o in objets if not (o["model"] == "engine.membreentreprise"
                                                    and o["fields"].get("utilisateur") == garder_utilisateur.pk)]
                brut = json.dumps(objets)
        else:
            for o in objets:
                if o["model"] in ("engine.groupetables", "engine.tabledynamique") \
                        and o["fields"].get("entreprise") != entreprise.pk:
                    raise ValueError("archive_entreprise_differente")
        from . import parquet_store
        # Pause du Parquet pendant toute la restauration : les fichiers archivés sont remis en place tels quels (rien ne les reconstruit
        # entre-temps) ; ils portent l'empreinte de la table, donc restent « frais » (maj_le n'est plus retouché par un chargement brut).
        with (parquet_store.en_pause() if complet else contextlib.nullcontext()):
            with transaction.atomic():
                if complet:
                    _vider_environnement(entreprise, garder_utilisateur)
                else:
                    reinitialiser_cycle(entreprise)
                for obj in serializers.deserialize("json", brut):
                    obj.save()
            if complet:
                _purger_parquet(entreprise)
                _extraire_medias(zf, entreprise)
                _restaurer_fantome(zf, entreprise, manifeste)
                try:  # tuiles de carte embarquées par la clôture : remises dans le cache (additif, jamais bloquant)
                    from . import geo_tuiles
                    geo_tuiles.restaurer(dossier_systeme("geo_tuiles"), zf)
                except Exception:
                    pass
        if complet:
            # Fichier archivé encore frais : conservé sans relecture ; absent (ancienne archive) ou périmé : reconstruit maintenant.
            parquet_store.synchroniser_entreprise(entreprise)
            try:
                controle = _controle_documents_administratifs(entreprise)
                if controle["alteres"]:
                    logger.warning(_gt("rg8.msg.045"),
                                   len(controle["alteres"]), controle["alteres"])
            except Exception:
                logger.exception(_gt("rg6.msg.009"), entreprise.pk)


def importer_modele(entreprise, zf, utilisateur, membre=None):
    """Import ADDITIF d'une archive « modèle » dans l'entreprise du client : rien n'est supprimé ni écrasé ; tout ce qui est créé reçoit
    de nouveaux identifiants et est rattaché à `entreprise` (les comptes référencés deviennent `utilisateur`, l'administrateur qui importe).
    Une table dont le nom ou le slug existe déjà est renommée. Tout ou rien (une transaction). Renvoie {"objets": comptage, "renommees": [...]}.
    `membre` (import par la messagerie) : un administrateur importe tout ; un AGENT est confiné : seules les tables de l'archive et ce qui en
    dépend sont importées (jamais la configuration de l'entreprise ni les ontologies), puis ajoutées à son espace (tables autorisées). Libre : aucune
    validation du chef. Un client n'importe rien. Sans `membre` (restauration depuis « Clôturer / restaurer », réservée aux administrateurs) : tout."""
    from django.core.management.color import no_style
    from django.db import connection
    if utilisateur is None:
        raise ValueError("utilisateur_requis")
    if NOM_JSON_MODELE not in zf.namelist():
        raise KeyError(NOM_JSON_MODELE)
    objets = json.loads(zf.read(NOM_JSON_MODELE).decode("utf-8"))
    comptes = _etiquette_utilisateur()
    meta = _meta_modeles()
    if membre is not None and membre.role not in ("admin", "agent"):
        raise ValueError("droits_insuffisants")
    from .delegation import a_pouvoir
    confine = membre is not None and not a_pouvoir(membre, "toutes_tables")
    if confine:
        tables_archive = {x["pk"] for x in objets if x["model"] == "engine.tabledynamique"}
        objets, _ = _tm.restreindre_a_tables(objets, meta, tables_archive, comptes=(comptes,))
        if not objets:
            raise ValueError("structure_vide")
    else:
        objets, _ = _tm.neutraliser(objets, meta, comptes=(comptes,))   # même règle qu'à l'export : une archive trafiquée n'apporte pas de données
    renommees = []
    from .models import ModeleDocument, ModeleEntete   # modèles de documents administratifs : le nom est unique par entreprise, un seul entête par défaut
    for etiq, modele in (("engine.modeleentete", ModeleEntete), ("engine.modeledocument", ModeleDocument)):
        renommees += _tm.rendre_uniques(objets, etiq, "nom", set(modele.objects.filter(entreprise=entreprise).values_list("nom", flat=True)))
    if ModeleEntete.objects.filter(entreprise=entreprise, par_defaut=True).exists():
        for o in objets:
            if o["model"] == "engine.modeleentete":
                o["fields"]["par_defaut"] = False
    for champ in ("slug", "nom"):
        pris = set(TableDynamique.objects.filter(entreprise=entreprise).values_list(champ, flat=True))
        renommees += _tm.rendre_uniques(objets, "engine.tabledynamique", champ, pris)

    def premier_pk(etiquette):
        maxi = django_apps.get_model(etiquette)._default_manager.aggregate(m=models.Max("pk"))["m"]
        return (maxi or 0) + 1

    objets = _tm.remapper(objets, meta, entreprise.pk, premier_pk, substituts={comptes: utilisateur.pk})
    nouvelles_tables = sorted(x["pk"] for x in objets if x["model"] == "engine.tabledynamique")
    comptage = {}
    with transaction.atomic():
        for obj in serializers.deserialize("json", json.dumps(objets)):
            obj.save()
            etiq = obj.object._meta.label_lower
            comptage[etiq] = comptage.get(etiq, 0) + 1
        if confine and nouvelles_tables:   # ce que l'agent reçoit entre dans SON espace (et seulement dans le sien)
            espace = sorted(set(membre.tables_autorisees or ()) | set(nouvelles_tables))
            MembreEntreprise.objects.filter(pk=membre.pk).update(tables_autorisees=espace)
            membre.tables_autorisees = espace
        modeles = [django_apps.get_model(e) for e in comptage]
        with connection.cursor() as curseur:   # PostgreSQL : les séquences rattrapent les identifiants explicites (sinon collision à la prochaine création)
            for sql in connection.ops.sequence_reset_sql(no_style(), modeles):
                curseur.execute(sql)
    return {"objets": comptage, "renommees": renommees, "tables": len(nouvelles_tables), "confine": confine}
