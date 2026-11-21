"""Synthèses rédigées — liste (recherche, filtres), cycle de vie (brouillon -> finalisée -> rouverte / archivée) et traçabilité.

Même esprit que emission_documents.py (documents administratifs), en plus léger : une synthèse n'est pas une pièce officielle numérotée, mais le PDF
d'une synthèse FINALISÉE est généré une seule fois et figé avec son empreinte SHA-256 — les chiffres ne changent plus quand les données de la table
bougent. Le contenu (corps, blocs « analyse ») est décrit dans reports/_part_008.py.

  * `visibles`   : l'administrateur voit toutes les synthèses de l'entreprise, un agent les siennes ;
  * `filtrer`    : titre, statut, archives — appliqués à un queryset DÉJÀ restreint aux synthèses visibles ;
  * `finaliser`  : PDF définitif généré UNE fois, figé + SHA-256 ; la synthèse n'est plus modifiable ;
  * `rouvrir`    : retour en brouillon (le PDF figé est supprimé) ; `archiver` : masquée de la liste par défaut ;
  * `octets_figes` : le PDF figé, empreinte recontrôlée à chaque téléchargement (emission_documents.octets_figes, même contrôle).
Les droits : l'administrateur et l'auteur modifient, finalisent, rouvrent, archivent et suppriment ; personne d'autre.
"""
import logging

from engine.i18n import t as _t, tf as _tf

logger = logging.getLogger(__name__)

LIMITE_DEFAUT, LIMITE_MAX = 100, 300
STATUTS = ("brouillon", "finalisee")


class ErreurSynthese(Exception):
    """Erreur métier (message affichable). `code` sert au front : interdit, statut, vide, introuvable."""
    def __init__(self, message, code="erreur"):
        super().__init__(message)
        self.code = code


def _m(cle, **kw):
    cle = "engine/syntheses.py:" + cle
    return _tf(cle, **kw) if kw else _t(cle)


# ─────────────────────────────────────────────────────────────
# Lecture
# ─────────────────────────────────────────────────────────────
def visibles(entreprise, membre):
    """Administrateur : toutes les synthèses de l'entreprise. Agent : les siennes."""
    from .models import SyntheseRedigee
    qs = SyntheseRedigee.objects.filter(entreprise=entreprise)
    if membre is not None and membre.role == "agent":
        qs = qs.filter(auteur=membre)
    return qs


def filtrer(qs, params):
    """`params` : QueryDict ou dict — q (titre), statut, archive (« 0 » par défaut : hors archives ; « 1 » : archives seulement ; « tout »)."""
    q = " ".join(str(params.get("q") or "").split())[:100]
    if q:
        qs = qs.filter(titre__icontains=q)
    statut = params.get("statut")
    if statut in STATUTS:
        qs = qs.filter(statut=statut)
    archive = str(params.get("archive") or "0")
    if archive == "1":
        qs = qs.filter(archive=True)
    elif archive != "tout":
        qs = qs.filter(archive=False)
    return qs


def limite(params):
    try:
        return max(1, min(int(params.get("limite") or LIMITE_DEFAUT), LIMITE_MAX))
    except ValueError:
        return LIMITE_DEFAUT


def peut_agir(s, membre):
    """Administrateur ou auteur de la synthèse."""
    return membre is not None and (membre.role == "admin" or s.auteur_id == membre.id)


def serialiser(s, complet=True, membre=None):
    from .reports import normaliser_corps_synthese
    base = {"id": s.id, "table": s.table_id, "titre": s.titre, "statut": s.statut, "modele_entete": s.modele_entete_id,
            "modifie_le": s.modifie_le.isoformat(), "cree_le": s.cree_le.isoformat(),
            "finalisee_le": s.finalisee_le.isoformat() if s.finalisee_le else "", "archive": s.archive,
            "auteur": (s.auteur.utilisateur.get_full_name() or s.auteur.utilisateur.get_username()) if s.auteur_id else "",
            "est_auteur": membre is not None and s.auteur_id == membre.id, "peut_agir": peut_agir(s, membre),
            "nb_blocs": len(s.corps) if isinstance(s.corps, list) else 0}
    if complet:
        base["corps"] = normaliser_corps_synthese(s.corps)
    return base


# ─────────────────────────────────────────────────────────────
# Écriture
# ─────────────────────────────────────────────────────────────
def appliquer_saisie(s, saisie):
    """Titre, corps et entête saisis -> synthèse (bornés, nettoyés). Retourne la liste des champs modifiés (pour update_fields).
    L'entête doit être un modèle de l'entreprise (vide = modèle par défaut) ; sinon ErreurSynthese('entete')."""
    from .reports import normaliser_corps_synthese
    from .reports._part_005 import MAX_TITRE
    maj = []
    if "titre" in saisie:
        s.titre = " ".join(str(saisie.get("titre") or "").split())[:MAX_TITRE]
        maj.append("titre")
    if "corps" in saisie:
        s.corps = normaliser_corps_synthese(saisie.get("corps"))
        maj.append("corps")
    if "modele_entete" in saisie:
        from .models import ModeleEntete
        brut = saisie.get("modele_entete")
        if brut in (None, "", 0, "0"):
            s.modele_entete = None
        else:
            try:
                modele = ModeleEntete.objects.filter(entreprise_id=s.entreprise_id, pk=int(brut)).first()
            except (TypeError, ValueError):
                modele = None
            if modele is None:
                raise ErreurSynthese(_m("entete_inconnu"), "entete")
            s.modele_entete = modele
        maj.append("modele_entete")
    return maj


def journaliser(s, membre, action, ip="", details=None, resultat="succes"):
    """Une ligne de JournalAudit par événement (qui, quoi, quand, d'où). Une erreur d'écriture du journal ne bloque jamais l'action."""
    try:
        from .models import JournalAudit
        JournalAudit.enregistrer(
            entreprise=s.entreprise_id, utilisateur=membre.utilisateur if membre is not None else None, action=f"synthese.{action}",
            details=details, source="api", cible_type="synthese", cible_id=s.pk, ip=ip or "", resultat=resultat)
    except Exception:
        logger.exception("synthese: journal d'audit illisible (action=%s)", action)


def finaliser(s, membre, ip=""):
    """Brouillon -> finalisée : PDF définitif (sans filigrane) généré UNE fois, stocké avec son SHA-256. Tout ou rien : si le PDF échoue, rien ne change."""
    from django.core.files.base import ContentFile
    from django.db import transaction
    from django.utils import timezone
    from .emission_documents import empreinte_sha256
    from .models import SyntheseRedigee
    from .reports import generer_pdf_synthese

    nom_fichier = stockage = None
    try:
        with transaction.atomic():
            doc = SyntheseRedigee.objects.select_for_update().select_related("entreprise", "table").get(pk=s.pk)
            if not peut_agir(doc, membre):
                raise ErreurSynthese(_m("interdit"), "interdit")
            if doc.statut != SyntheseRedigee.STATUT_BROUILLON:
                raise ErreurSynthese(_m("deja_finalisee"), "statut")
            if not doc.corps:
                raise ErreurSynthese(_m("vide"), "vide")
            doc.statut, doc.finalisee_le, doc.finalisee_par = SyntheseRedigee.STATUT_FINALISEE, timezone.now(), membre
            octets = generer_pdf_synthese(doc, final=True)
            doc.empreinte_sha256 = empreinte_sha256(octets)
            doc.pdf_fige.save(f"{doc.entreprise_id}-synthese-{doc.pk}.pdf", ContentFile(octets), save=False)
            nom_fichier, stockage = doc.pdf_fige.name, doc.pdf_fige.storage
            doc.save()     # la ligne en base est encore « brouillon » : le garde-fou laisse passer cette écriture
            journaliser(doc, membre, "finaliser", ip=ip, details={"sha256": doc.empreinte_sha256, "table": doc.table_id})
    except Exception:
        if nom_fichier and stockage is not None:
            try:
                stockage.delete(nom_fichier)       # pas de PDF orphelin d'une finalisation annulée
            except Exception:
                pass
        raise
    return doc


def rouvrir(s, membre, ip=""):
    """Finalisée -> brouillon. Le PDF figé est supprimé (UPDATE direct : le garde-fou de save() interdit toute autre voie)."""
    from django.db import transaction
    from django.utils import timezone
    from .models import SyntheseRedigee
    with transaction.atomic():
        doc = SyntheseRedigee.objects.select_for_update().get(pk=s.pk)
        if not peut_agir(doc, membre):
            raise ErreurSynthese(_m("interdit"), "interdit")
        if doc.statut != SyntheseRedigee.STATUT_FINALISEE:
            raise ErreurSynthese(_m("pas_finalisee"), "statut")
        ancien_pdf = (doc.pdf_fige.name, doc.pdf_fige.storage) if doc.pdf_fige else None
        SyntheseRedigee.objects.filter(pk=doc.pk).update(
            statut=SyntheseRedigee.STATUT_BROUILLON, finalisee_le=None, finalisee_par=None, pdf_fige="", empreinte_sha256="",
            archive=False, archive_le=None, modifie_le=timezone.now())
        journaliser(doc, membre, "rouvrir", ip=ip, details={"sha256": doc.empreinte_sha256})
        if ancien_pdf:
            def _effacer():
                try:
                    ancien_pdf[1].delete(ancien_pdf[0])
                except Exception:
                    logger.exception("synthese: PDF figé non supprimé (%s)", ancien_pdf[0])
            transaction.on_commit(_effacer)
    return SyntheseRedigee.objects.get(pk=s.pk)


def archiver(s, membre, archive=True, ip=""):
    """Masque (ou ré-affiche) une synthèse dans la liste. Rien n'est supprimé."""
    from django.db import transaction
    from django.utils import timezone
    from .models import SyntheseRedigee
    with transaction.atomic():
        doc = SyntheseRedigee.objects.select_for_update().get(pk=s.pk)
        if not peut_agir(doc, membre):
            raise ErreurSynthese(_m("interdit"), "interdit")
        doc.archive, doc.archive_le = bool(archive), (timezone.now() if archive else None)
        doc.save(update_fields=["archive", "archive_le", "modifie_le"])
        journaliser(doc, membre, "archiver" if archive else "desarchiver", ip=ip)
    return doc


def octets_figes(s):
    """Octets du PDF figé, empreinte recontrôlée. Lève emission_documents.ErreurEmission('integrite') si le fichier a disparu ou a été altéré."""
    from .emission_documents import octets_figes as _octets_figes
    return _octets_figes(s)
