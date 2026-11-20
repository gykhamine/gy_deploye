"""Valeur légale des documents administratifs : émission, annulation, version corrigée, vérification.

Cycle de vie : brouillon -> émis -> annulé.
  * BROUILLON : modifiable, sans numéro (aucun numéro n'est consommé tant que le document n'est pas émis).
  * ÉMIS      : numéro de référence, lieu et date fixés, PDF généré UNE fois, stocké avec son empreinte SHA-256 ;
                plus modifiable (garde-fou dans DocumentAdministratif.save). Le même fichier est re-téléchargé,
                son empreinte est recontrôlée à chaque téléchargement.
  * ANNULÉ    : le numéro reste attribué (pas de trou dans la suite) ; le PDF reste archivé et la page de
                vérification publique indique l'annulation. Un document émis ne se supprime jamais.
  * À VALIDER : circuit facultatif (réglage « validation obligatoire ») — l'agent rédige et `soumettre`, l'administrateur
                valide (= `emettre`) ou renvoie en brouillon avec un motif (`refuser`). Le contenu ne bouge plus tant que
                le document attend sa décision.
  * Verrou    : un brouillon n'est modifié que par une personne à la fois (`prendre_verrou`, expire seul après 2 minutes).
  * Archivage : un document émis ou annulé ne se supprime jamais ; l'administrateur l'`archiver` (masqué par défaut).
  * Correction : `corriger` crée un NOUVEAU brouillon qui renvoie à l'original ; à l'émission de la version corrigée,
                l'original est annulé avec la mention « remplacé par … ».

Ce module ne dépend que de ReportLab pour les parties pures (format du numéro, empreinte). Django et les modèles sont
importés au moment de l'usage, comme dans entete_officiel.py, pour pouvoir tester la logique seule.
"""
import hashlib
import secrets
import string
from engine.i18n import t as _gt  # noqa: E402
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
from engine.i18n import tf as _gtf  # noqa: E402

CHAMPS_REFERENCE = {"n", "annee", _gtfr("engine/emission_documents.py:alias_annee"), "type"}
MAX_REFERENCE = 120


DUREE_VERROU = 120      # secondes : l'éditeur renouvelle le verrou tant que la page est ouverte
MAX_EXPORT = 500        # documents par archive ZIP


def _m(cle, **kw):
    """Message affichable, traduit selon la langue de la requête (clés « engine/emission_documents.py:… »)."""
    from engine.i18n import t, tf
    cle = "engine/emission_documents.py:" + cle
    return tf(cle, **kw) if kw else t(cle)


class ErreurEmission(Exception):
    """Erreur métier (message affichable à l'utilisateur). `code` sert au front et aux tests."""
    def __init__(self, message, code="erreur"):
        super().__init__(message)
        self.code = code


# ─────────────────────────────────────────────────────────────
# Parties pures
# ─────────────────────────────────────────────────────────────
def valider_modele_reference(modele):
    """Chaîne vide si le modèle de numéro est acceptable, sinon le message d'erreur.
    Champs permis : {n} (avec un format entier, ex. {n:04d}), {annee} ou {année}, {type}. Rien d'autre : pas
    d'attribut, pas d'indexation, pas de format exotique — le modèle vient d'un formulaire."""
    modele = modele or ""
    if not modele.strip():
        return _m("format_vide")
    if len(modele) > MAX_REFERENCE:
        return _m("format_long", v1=MAX_REFERENCE)
    try:
        morceaux = list(string.Formatter().parse(modele))
    except ValueError:
        return _m("format_accolades")
    champs = [m for m in morceaux if m[1] is not None]
    if not any(m[1] == "n" for m in champs):
        return _m("format_sans_n")
    for _texte, champ, spec, conversion in champs:
        if champ not in CHAMPS_REFERENCE:
            return _m("champ_inconnu", v1=champ)
        if conversion:
            return _m("format_conversion")
        if champ in ("n", "annee", _gtfr("engine/emission_documents.py:alias_annee")):
            if spec and not _spec_entiere_ok(spec):
                return _m("format_numerique", v1=champ, v2=repr(spec))
        elif spec:
            return _m("champ_sans_format", v1=champ)
    return ""


def _spec_entiere_ok(spec):
    """Format entier simple : `d`, `4d`, `04d`, `010d` (au plus deux chiffres de largeur)."""
    import re
    return re.fullmatch(r"0?\d{0,2}d", spec) is not None


def formater_reference(modele, n, annee, type_document="courrier"):
    """Applique le modèle (déjà validé) : `N°{n:04d}/MFPP/DGID/{année}` -> `N°0012/MFPP/DGID/2026`."""
    erreur = valider_modele_reference(modele)
    if erreur:
        raise ErreurEmission(erreur, "format_invalide")
    return modele.format_map({"n": n, "annee": annee, _gtfr("engine/emission_documents.py:alias_annee"): annee, "type": type_document})[:MAX_REFERENCE]


def empreinte_sha256(octets):
    return hashlib.sha256(octets).hexdigest()


def nouveau_code_verification():
    """Code aléatoire (≈72 bits) du QR : non devinable, donc la page publique ne permet pas d'énumérer les documents."""
    return secrets.token_urlsafe(9)


# ─────────────────────────────────────────────────────────────
# Base de données (import tardif)
# ─────────────────────────────────────────────────────────────
def reglages_emission(entreprise):
    from .models import ReglageEmission
    r = ReglageEmission.objects.filter(entreprise=entreprise).first()
    if r is not None:
        return r.format_reference, r.lieu_emission
    return ReglageEmission.FORMAT_DEFAUT, ""


def validation_obligatoire(entreprise):
    """Vrai : un agent soumet ses documents et l'administrateur les valide (il ne les émet pas lui-même)."""
    from .models import ReglageEmission
    return ReglageEmission.objects.filter(entreprise=entreprise, validation_obligatoire=True).exists()


def enregistrer_reglages(entreprise, format_reference=None, lieu_emission=None, validation=None):
    """Administrateur seulement (contrôlé par la vue). Retourne le message d'erreur ou ''."""
    from .models import ReglageEmission
    r, _ = ReglageEmission.objects.get_or_create(entreprise=entreprise)
    if format_reference is not None:
        erreur = valider_modele_reference(format_reference)
        if erreur:
            return erreur
        r.format_reference = format_reference.strip()
    if lieu_emission is not None:
        r.lieu_emission = " ".join(str(lieu_emission).split())[:120]
    if validation is not None:
        r.validation_obligatoire = bool(validation)
    r.save()
    return ""


def _attribuer_reference(entreprise, type_document, annee, modele):
    """Numéro suivant, sous verrou de ligne, dans la transaction d'émission en cours (appelée dans `atomic`)."""
    from .models import CompteurReference
    CompteurReference.objects.get_or_create(entreprise=entreprise, type_document=type_document, annee=annee)
    compteur = CompteurReference.objects.select_for_update().get(
        entreprise=entreprise, type_document=type_document, annee=annee)
    compteur.dernier += 1
    compteur.save(update_fields=["dernier"])
    return compteur.dernier, formater_reference(modele, compteur.dernier, annee, type_document)


def _audit(d, membre, action, ip="", details=None, ancienne=None, nouvelle=None, resultat="succes"):
    """Une ligne de JournalAudit par événement du cycle de vie (qui, quoi, quand, d'où), en plus de la ligne générique
    d'AuditMiddleware qui ne connaît que la méthode HTTP et le chemin."""
    from .models import JournalAudit
    JournalAudit.enregistrer(
        entreprise=d.entreprise_id, utilisateur=membre.utilisateur if membre is not None else None,
        action=f"document_administratif.{action}", details=details, source="api",
        cible_type="document_administratif", cible_id=d.pk, ip=ip or "", resultat=resultat,
        ancienne_valeur=ancienne, nouvelle_valeur=nouvelle,
    )


def journaliser(d, membre, action, ip="", details=None, resultat="succes"):
    """Point d'entrée pour les vues (création, téléchargement, changement d'entête…)."""
    try:
        _audit(d, membre, action, ip=ip, details=details, resultat=resultat)
    except Exception:  # le journal ne doit jamais bloquer l'action (même règle qu'AuditMiddleware)
        import logging
        logging.getLogger(__name__).exception(_gt("engine/emission_documents.py:167:47"), action)


def journaliser_entete(entreprise, utilisateur, membre, entete, avant, ip=""):
    """Changement d'entête officiel : qui (administrateur : lignes imposées ; agent : son complément), avant / après."""
    from .models import JournalAudit
    try:
        JournalAudit.enregistrer(
            entreprise=entreprise, utilisateur=utilisateur, action="document_administratif.entete_modifie", source="api",
            cible_type="entete_officiel", cible_id=entete.pk, ip=ip or "",
            details={"role": membre.role if membre is not None else "", "portee": "agent" if entete.membre_id else "entreprise"},
            ancienne_valeur=avant, nouvelle_valeur={"gauche": entete.lignes_gauche, "droite": entete.lignes_droite})
    except Exception:
        import logging
        logging.getLogger(__name__).exception(_gt("rg5.msg.019"))


def peut_emettre(d, membre):
    """Administrateur : toujours. Auteur : seulement si la validation n'est pas obligatoire (sinon il soumet)."""
    if membre is None:
        return False
    from .delegation import a_pouvoir
    if a_pouvoir(membre, "documents_emettre"):
        return True
    return d.auteur_id == membre.id and not validation_obligatoire(d.entreprise)


def _verifier_contenu(doc):
    if not (doc.titre or "").strip() or not any(b.get("texte") for b in doc.corps if isinstance(b, dict)):   # un saut de page seul n'est pas un contenu
        raise ErreurEmission(_m("contenu_vide"), "vide")


def _nom_membre(membre):
    u = membre.utilisateur
    return u.get_full_name() or u.get_username()


def _verrou_valide(doc, maintenant=None):
    from django.utils import timezone
    return bool(doc.verrou_par_id and doc.verrou_jusqu_a and doc.verrou_jusqu_a > (maintenant or timezone.now()))


def _exiger_verrou_libre(doc, membre):
    """Refuse si QUELQU'UN D'AUTRE tient un verrou encore valide sur ce document."""
    if _verrou_valide(doc) and (membre is None or doc.verrou_par_id != membre.id):
        raise ErreurEmission(_m("verrouille", v1=_nom_membre(doc.verrou_par)), "verrouille")


def emettre(d, membre, construire_url, lieu=None, ip=""):
    """Brouillon -> émis, en UNE transaction : verrou du document, numéro, date, lieu, PDF figé + empreinte, audit.
    Si quoi que ce soit échoue (PDF compris), tout est annulé : le numéro n'est PAS consommé.
    `construire_url(code)` donne l'adresse absolue de la page de vérification (imprimée dans le QR)."""
    from django.core.files.base import ContentFile
    from django.db import transaction
    from django.utils import timezone
    from .models import DocumentAdministratif
    from .reports import generer_pdf_document_administratif

    nom_fichier = None
    stockage = None
    try:
        with transaction.atomic():
            doc = DocumentAdministratif.objects.select_for_update().select_related("entreprise").get(pk=d.pk)
            if doc.statut not in (DocumentAdministratif.STATUT_BROUILLON, DocumentAdministratif.STATUT_SOUMIS):
                raise ErreurEmission(_m("deja_emis"), "deja_emis")
            if not peut_emettre(doc, membre):
                if membre is not None and doc.auteur_id == membre.id:
                    raise ErreurEmission(_m("validation_requise"), "validation_requise")
                raise ErreurEmission(_m("emettre_interdit"), "interdit")
            etait_soumis = doc.statut == DocumentAdministratif.STATUT_SOUMIS
            _exiger_verrou_libre(doc, membre)
            _verifier_contenu(doc)
            modele, lieu_defaut = reglages_emission(doc.entreprise)
            lieu_final = " ".join(str(lieu if lieu is not None else (doc.lieu or lieu_defaut)).split())[:120]
            if not lieu_final:
                raise ErreurEmission(_m("lieu_requis"), "lieu_requis")

            aujourdhui = timezone.localdate()
            _numero, reference = _attribuer_reference(doc.entreprise, doc.type_document, aujourdhui.year, modele)
            code = nouveau_code_verification()
            # Numéro de l'entête (dernière ligne du bloc gauche) : réglages de l'auteur ; le compteur avance ici, dans la
            # transaction, comme la référence : rien n'est consommé si l'émission échoue.
            from .entete_officiel import attribuer_numero
            numero_entete = attribuer_numero(doc.entreprise_id, doc.auteur_id or membre.id, doc.type_document, aujourdhui.year)

            octets = generer_pdf_document_administratif(doc, emission={
                "reference": reference, "lieu": lieu_final, "date": aujourdhui, "url": construire_url(code),
                "numero_entete": numero_entete})
            empreinte = empreinte_sha256(octets)

            doc.reference, doc.lieu, doc.date_emission = reference, lieu_final, aujourdhui
            doc.numero_entete = numero_entete
            doc.emis_le, doc.emis_par, doc.code_verification = timezone.now(), membre, code
            doc.statut, doc.empreinte_sha256 = DocumentAdministratif.STATUT_EMIS, empreinte
            doc.verrou_par, doc.verrou_jusqu_a, doc.motif_refus = None, None, ""
            if etait_soumis:
                doc.valide_par, doc.valide_le = membre, doc.emis_le
            doc.pdf_fige.save(f"{doc.entreprise_id}-{reference.replace('/', '_')}.pdf"[:100], ContentFile(octets), save=False)
            nom_fichier, stockage = doc.pdf_fige.name, doc.pdf_fige.storage
            doc.save()     # la ligne en base est encore « brouillon » : le garde-fou laisse passer cette écriture

            if doc.version_de_id:
                _annuler_original_remplace(doc, membre, ip)
            _audit(doc, membre, "emettre", ip=ip, nouvelle={"reference": reference, "numero_entete": numero_entete, "sha256": empreinte, "lieu": lieu_final,
                                                          "date": aujourdhui.isoformat(), "version_de": doc.version_de_id})
            if etait_soumis:
                _audit(doc, membre, "valider", ip=ip, details={"reference": reference, "auteur": doc.auteur_id})
    except Exception:
        if nom_fichier and stockage is not None:
            try:
                stockage.delete(nom_fichier)       # pas de PDF orphelin d'une émission annulée
            except Exception:
                pass
        raise
    return doc


def _annuler_original_remplace(corrige, membre, ip):
    from django.utils import timezone
    from .models import DocumentAdministratif
    original = DocumentAdministratif.objects.select_for_update().filter(
        pk=corrige.version_de_id, statut=DocumentAdministratif.STATUT_EMIS).first()
    if original is None:
        return
    original.statut, original.annule_le, original.annule_par = DocumentAdministratif.STATUT_ANNULE, timezone.now(), membre
    original.motif_annulation = _gtf("rg8.msg.004", reference=corrige.reference)
    original.save(update_fields=["statut", "annule_le", "annule_par", "motif_annulation", "modifie_le"])
    _audit(original, membre, "annuler", ip=ip, details={"motif": original.motif_annulation, "remplace_par": corrige.pk})


def annuler(d, membre, motif, ip=""):
    """Document émis -> annulé (administrateur, motif obligatoire). Le numéro et le PDF restent."""
    from django.db import transaction
    from django.utils import timezone
    from .models import DocumentAdministratif
    motif = " ".join(str(motif or "").split())[:300]
    from .delegation import a_pouvoir
    if not a_pouvoir(membre, "documents_annuler"):
        raise ErreurEmission(_m("annuler_interdit"), "interdit")
    if len(motif) < 3:
        raise ErreurEmission(_m("annuler_motif"), "motif_requis")
    with transaction.atomic():
        doc = DocumentAdministratif.objects.select_for_update().get(pk=d.pk)
        if doc.statut != DocumentAdministratif.STATUT_EMIS:
            raise ErreurEmission(_m("annuler_statut"), "statut")
        doc.statut, doc.annule_le, doc.annule_par, doc.motif_annulation = (
            DocumentAdministratif.STATUT_ANNULE, timezone.now(), membre, motif)
        doc.save(update_fields=["statut", "annule_le", "annule_par", "motif_annulation", "modifie_le"])
        _audit(doc, membre, "annuler", ip=ip, details={"motif": motif, "reference": doc.reference})
    return doc


def corriger(d, membre, ip=""):
    """Nouveau BROUILLON copiant un document émis et renvoyant à lui (`version_de`). Une seule correction ouverte à la fois."""
    from django.db import transaction
    from .models import DocumentAdministratif
    with transaction.atomic():
        original = DocumentAdministratif.objects.select_for_update().get(pk=d.pk)
        if original.statut != DocumentAdministratif.STATUT_EMIS:
            raise ErreurEmission(_m("corriger_statut"), "statut")
        ouverte = original.corrections.filter(statut=DocumentAdministratif.STATUT_BROUILLON).first()
        if ouverte is not None:
            return ouverte
        copie = DocumentAdministratif.objects.create(
            entreprise_id=original.entreprise_id, auteur=membre, titre=original.titre, corps=original.corps,
            type_document=original.type_document, delegation=original.delegation, date_position=original.date_position,
            lieu=original.lieu, version_de=original,
            modele_entete_id=original.modele_entete_id)
        _audit(copie, membre, "corriger", ip=ip, details={"original": original.pk, "reference_originale": original.reference})
    return copie


# ─────────────────────────────────────────────────────────────
# Circuit de validation : brouillon -> à valider -> (émis | brouillon)
# ─────────────────────────────────────────────────────────────
def soumettre(d, membre, ip=""):
    """Brouillon -> à valider (auteur ou administrateur). Le contenu est alors figé en attendant la décision."""
    from django.db import transaction
    from django.utils import timezone
    from .models import DocumentAdministratif
    with transaction.atomic():
        doc = DocumentAdministratif.objects.select_for_update().get(pk=d.pk)
        if doc.statut != DocumentAdministratif.STATUT_BROUILLON:
            raise ErreurEmission(_m("soumettre_statut"), "statut")
        if membre is None or (membre.role != "admin" and doc.auteur_id != membre.id):
            raise ErreurEmission(_m("soumettre_interdit"), "interdit")
        _exiger_verrou_libre(doc, membre)
        _verifier_contenu(doc)
        doc.statut, doc.soumis_le, doc.motif_refus = DocumentAdministratif.STATUT_SOUMIS, timezone.now(), ""
        doc.verrou_par, doc.verrou_jusqu_a = None, None
        doc.save(update_fields=["statut", "soumis_le", "motif_refus", "verrou_par", "verrou_jusqu_a", "modifie_le"])
        _audit(doc, membre, "soumettre", ip=ip, details={"type": doc.type_document})
    return doc


def _vers_brouillon(doc, membre, action, motif, ip):
    from .models import DocumentAdministratif
    doc.statut, doc.soumis_le, doc.motif_refus = DocumentAdministratif.STATUT_BROUILLON, None, motif
    doc.save(update_fields=["statut", "soumis_le", "motif_refus", "modifie_le"])
    _audit(doc, membre, action, ip=ip, details={"motif": motif} if motif else None)


def refuser(d, membre, motif, ip=""):
    """À valider -> brouillon, avec le motif (administrateur) : l'auteur peut corriger et soumettre de nouveau."""
    from django.db import transaction
    from .models import DocumentAdministratif
    motif = " ".join(str(motif or "").split())[:300]
    from .delegation import a_pouvoir
    if not a_pouvoir(membre, "documents_emettre"):
        raise ErreurEmission(_m("refuser_interdit"), "interdit")
    if len(motif) < 3:
        raise ErreurEmission(_m("refuser_motif"), "motif_requis")
    with transaction.atomic():
        doc = DocumentAdministratif.objects.select_for_update().get(pk=d.pk)
        if doc.statut != DocumentAdministratif.STATUT_SOUMIS:
            raise ErreurEmission(_m("refuser_statut"), "statut")
        _vers_brouillon(doc, membre, "refuser", motif, ip)
    return doc


def rappeler(d, membre, ip=""):
    """À valider -> brouillon, sans motif : l'auteur (ou un administrateur) reprend son document avant la décision."""
    from django.db import transaction
    from .models import DocumentAdministratif
    with transaction.atomic():
        doc = DocumentAdministratif.objects.select_for_update().get(pk=d.pk)
        if doc.statut != DocumentAdministratif.STATUT_SOUMIS:
            raise ErreurEmission(_m("refuser_statut"), "statut")
        if membre is None or (membre.role != "admin" and doc.auteur_id != membre.id):
            raise ErreurEmission(_m("rappeler_interdit"), "interdit")
        _vers_brouillon(doc, membre, "rappeler", "", ip)
    return doc


# ─────────────────────────────────────────────────────────────
# Verrou d'édition (brouillons) : empêche deux personnes de s'écraser mutuellement
# ─────────────────────────────────────────────────────────────
def prendre_verrou(d, membre):
    """Prend ou renouvelle le verrou du brouillon pour `membre`. Lève ErreurEmission('verrouille') si une AUTRE personne
    le tient encore. Retourne l'instant d'expiration. N'écrit que les champs du verrou (le contenu reste intact)."""
    from datetime import timedelta
    from django.db import transaction
    from django.utils import timezone
    from .models import DocumentAdministratif
    with transaction.atomic():
        doc = DocumentAdministratif.objects.select_for_update().select_related("verrou_par__utilisateur").get(pk=d.pk)
        if doc.statut != DocumentAdministratif.STATUT_BROUILLON:
            raise ErreurEmission(_m("verrou_statut"), "statut")
        _exiger_verrou_libre(doc, membre)
        jusqu_a = timezone.now() + timedelta(seconds=DUREE_VERROU)
        DocumentAdministratif.objects.filter(pk=doc.pk).update(verrou_par=membre, verrou_jusqu_a=jusqu_a)   # update : ni modifie_le, ni garde-fou
    return jusqu_a


def liberer_verrou(d, membre):
    """Libère le verrou s'il est à `membre` (sans effet sinon : on ne libère jamais le verrou d'un autre)."""
    from .models import DocumentAdministratif
    DocumentAdministratif.objects.filter(pk=d.pk, verrou_par=membre).update(verrou_par=None, verrou_jusqu_a=None)


def verifier_verrou_ecriture(d, membre):
    """Avant d'enregistrer : refuse si quelqu'un d'autre tient un verrou valide (lecture fraîche en base)."""
    from .models import DocumentAdministratif
    doc = DocumentAdministratif.objects.select_related("verrou_par__utilisateur").get(pk=d.pk)
    _exiger_verrou_libre(doc, membre)


def description_verrou(d, membre):
    """{"par": nom, "moi": bool} si un verrou valide existe, sinon None (pour l'éditeur)."""
    if not _verrou_valide(d):
        return None
    return {"par": _nom_membre(d.verrou_par), "moi": membre is not None and d.verrou_par_id == membre.id}


# ─────────────────────────────────────────────────────────────
# Archivage
# ─────────────────────────────────────────────────────────────
def archiver(d, membre, archive=True, ip=""):
    """Masque (ou ré-affiche) un document émis ou annulé dans la liste. Administrateur seulement. Rien n'est supprimé."""
    from django.db import transaction
    from django.utils import timezone
    from .models import DocumentAdministratif
    from .delegation import a_pouvoir
    if not a_pouvoir(membre, "documents_archiver"):
        raise ErreurEmission(_m("archiver_interdit"), "interdit")
    with transaction.atomic():
        doc = DocumentAdministratif.objects.select_for_update().get(pk=d.pk)
        if doc.statut not in (DocumentAdministratif.STATUT_EMIS, DocumentAdministratif.STATUT_ANNULE):
            raise ErreurEmission(_m("archiver_statut"), "statut")
        doc.archive, doc.archive_le = bool(archive), (timezone.now() if archive else None)
        doc.save(update_fields=["archive", "archive_le", "modifie_le"])
        _audit(doc, membre, "archiver" if archive else "desarchiver", ip=ip, details={"reference": doc.reference})
    return doc


def renommer(d, membre, nom, ip=""):
    """Change le nom de classement d'un document, À TOUT MOMENT (brouillon, à valider, émis, annulé, archivé). Seul le nom
    change : le titre, le corps et le PDF figé (donc son empreinte) restent intacts. Administrateur ou auteur du document.
    Un nom vide revient au titre du document."""
    from django.db import transaction
    from .models import DocumentAdministratif
    if membre is None or (membre.role != "admin" and d.auteur_id != membre.id):
        raise ErreurEmission(_m("renommer_interdit"), "interdit")
    nom = " ".join(str(nom or "").split())[:120]
    with transaction.atomic():
        doc = DocumentAdministratif.objects.select_for_update().get(pk=d.pk)
        avant = doc.nom
        if nom != avant:
            doc.nom = nom
            doc.save(update_fields=["nom", "modifie_le"])
            _audit(doc, membre, "renommer", ip=ip, details={"reference": doc.reference}, ancienne={"nom": avant}, nouvelle={"nom": nom})
    return doc


def octets_figes(d):
    """Octets du PDF figé, après contrôle de l'empreinte. Lève ErreurEmission('integrite') si le fichier a été altéré
    ou a disparu : on ne sert jamais une pièce dont l'empreinte ne correspond plus."""
    if not d.pdf_fige or not d.empreinte_sha256:
        raise ErreurEmission(_m("pdf_introuvable"), "integrite")
    try:
        with d.pdf_fige.open("rb") as f:
            octets = f.read()
    except Exception:
        raise ErreurEmission(_m("pdf_illisible"), "integrite")
    if empreinte_sha256(octets) != d.empreinte_sha256:
        raise ErreurEmission(_m("pdf_empreinte"), "integrite")
    return octets


def donnees_verification(code):
    """Ce que la page PUBLIQUE a le droit de montrer pour un code, ou None. Jamais le contenu du document :
    seulement ce qui prouve son existence et son authenticité (référence, date, lieu, entité, statut, empreinte)."""
    from .models import DocumentAdministratif
    if not code or len(code) > 32:
        return None
    d = DocumentAdministratif.objects.select_related("entreprise").filter(code_verification=code).first()
    if d is None or d.statut in (DocumentAdministratif.STATUT_BROUILLON, DocumentAdministratif.STATUT_SOUMIS):
        return None
    remplace_par = d.corrections.filter(statut=DocumentAdministratif.STATUT_EMIS).values_list("reference", flat=True).first()
    return {
        "reference": d.reference, "entite": d.entreprise.nom, "titre": d.titre, "lieu": d.lieu,
        "date_emission": d.date_emission, "statut": d.statut, "empreinte": d.empreinte_sha256,
        "motif_annulation": d.motif_annulation, "remplace_par": remplace_par or "",
    }
