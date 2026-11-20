"""Modèles d'entête nommés (ModeleEntete) et modèles de documents (ModeleDocument) des documents administratifs.

  * L'ADMINISTRATEUR crée, modifie et supprime les modèles ; il fixe le modèle d'entête par défaut. L'AGENT les voit et les
    utilise (choix de l'entête d'un brouillon, « Nouveau à partir d'un modèle ») : aucune écriture de sa part.
  * Un seul modèle d'entête par défaut par entreprise, géré ici : le premier modèle créé l'est d'office ; si le modèle par
    défaut est supprimé, le premier restant (par ordre) le devient — l'entreprise garde toujours un défaut tant qu'elle a un
    modèle. L'entête réellement imprimé se calcule dans entete_officiel.py (UNE seule fonction), jamais ici.
  * Variables d'un modèle de document : {date}, {destinataire}, {objet}, {lieu}, {auteur}, remplacées UNE fois, à la création
    du brouillon (jamais à l'émission). Une variable inconnue, ou connue mais sans valeur, reste telle quelle : sans erreur.
  * Chaque création, modification et suppression écrit une ligne de JournalAudit : document_administratif.modele_cree /
    modele_modifie / modele_supprime (cible_type « modele_entete » ou « modele_document »).

Django et les modèles sont importés au moment de l'usage (comme entete_officiel.py et emission_documents.py) : les parties
pures (`appliquer_variables`, `valeurs_creation`) se testent seules.
"""
import re

from .entete_officiel import normaliser_lignes
from engine.i18n import t as _gt  # noqa: E402

MAX_NOM = 100
VARIABLES = ("date", "destinataire", "objet", "lieu", "auteur")
MAX_VALEUR = 200                       # destinataire / objet saisis à la création du brouillon
_VARIABLE = re.compile(r"\{(\w+)\}")


class ErreurModele(Exception):
    """Erreur métier (le message affichable vient de `code`, traduit par la vue). `statut` = code HTTP."""
    def __init__(self, code, statut=400):
        super().__init__(code)
        self.code, self.statut = code, statut


# ─────────────────────────────────────────────────────────────
# Variables (parties pures)
# ─────────────────────────────────────────────────────────────
def appliquer_variables(texte, valeurs):
    """Remplace {date}, {destinataire}, {objet}, {lieu}, {auteur} par `valeurs`. Une variable inconnue ({autre}) ou sans
    valeur reste telle quelle. Un seul passage : une valeur qui contient « {objet} » n'est pas remplacée à son tour."""
    def _remplacer(m):
        nom = m.group(1)
        valeur = valeurs.get(nom) if nom in VARIABLES else None
        return str(valeur) if valeur not in (None, "") else m.group(0)
    return _VARIABLE.sub(_remplacer, str(texte or ""))


def valeurs_creation(entreprise, membre, saisie=None):
    """Valeurs des variables pour un brouillon créé maintenant : {date} (jour du serveur), {auteur} (nom de l'agent),
    {lieu} (lieu d'émission par défaut de l'entreprise) viennent du système ; {destinataire} et {objet} de ce que l'agent a
    saisi à la création (vides : la variable reste dans le brouillon, l'agent la complète)."""
    from django.utils import timezone
    from .emission_documents import reglages_emission
    saisie = saisie if isinstance(saisie, dict) else {}
    nettoyer = lambda v: " ".join(str(v or "").split())[:MAX_VALEUR]
    utilisateur = membre.utilisateur if membre is not None else None
    return {
        "date": timezone.localdate().strftime("%d/%m/%Y"),
        "auteur": (utilisateur.get_full_name() or utilisateur.get_username()) if utilisateur is not None else "",
        "lieu": reglages_emission(entreprise)[1],
        "destinataire": nettoyer(saisie.get("destinataire")),
        "objet": nettoyer(saisie.get("objet")),
    }


def brouillon_depuis_modele(modele, valeurs):
    """Saisie (titre, corps, type, entête) d'un nouveau brouillon à partir d'un ModeleDocument, variables remplacées.
    Le résultat passe ensuite par la même normalisation que toute saisie de l'éditeur."""
    corps = []
    for bloc in (modele.corps if isinstance(modele.corps, list) else []):
        if isinstance(bloc, dict):
            corps.append({"type": bloc.get("type"), "texte": appliquer_variables(bloc.get("texte"), valeurs)})
    return {"titre": appliquer_variables(modele.titre, valeurs), "corps": corps,
            "type_document": modele.type_document, "modele_entete": modele.modele_entete_id}


# ─────────────────────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────────────────────
def journaliser_modele(entreprise, utilisateur, genre, action, cible_id, nom, avant=None, apres=None, ip="", details=None):
    """Une ligne de JournalAudit : action « document_administratif.modele_<cree|modifie|supprime> ». `genre` : « entete » ou
    « document ». Le journal ne bloque jamais l'action (même règle qu'AuditMiddleware)."""
    from .models import JournalAudit
    try:
        JournalAudit.enregistrer(
            entreprise=entreprise, utilisateur=utilisateur, action=f"document_administratif.modele_{action}", source="api",
            cible_type="modele_entete" if genre == "entete" else "modele_document", cible_id=cible_id, ip=ip or "",
            details={"nom": nom, **(details or {})}, ancienne_valeur=avant, nouvelle_valeur=apres)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(_gt("rg5.msg.001"), action)


# ─────────────────────────────────────────────────────────────
# Utilitaires communs
# ─────────────────────────────────────────────────────────────
def _nom(valeur):
    nom = " ".join(str(valeur or "").split())[:MAX_NOM]
    if not nom:
        raise ErreurModele("nom_requis")
    return nom


def _verifier_nom_libre(classe, entreprise_id, nom, sauf_pk=None):
    pris = classe.objects.filter(entreprise_id=entreprise_id, nom__iexact=nom)
    if sauf_pk:
        pris = pris.exclude(pk=sauf_pk)
    if pris.exists():
        raise ErreurModele("nom_pris", 409)


def entete_de_l_entreprise(entreprise_id, valeur):
    """ModeleEntete désigné par `valeur` (identifiant) s'il appartient à l'entreprise ; None si vide. Identifiant étranger
    ou inconnu : erreur (jamais un entête d'une autre entreprise)."""
    from .models import ModeleEntete
    if valeur in (None, "", 0, "0"):
        return None
    try:
        pk = int(valeur)
    except (TypeError, ValueError):
        raise ErreurModele("entete_inconnu")
    modele = ModeleEntete.objects.filter(pk=pk, entreprise_id=entreprise_id).first()
    if modele is None:
        raise ErreurModele("entete_inconnu")
    return modele


# ─────────────────────────────────────────────────────────────
# Modèles d'entête
# ─────────────────────────────────────────────────────────────
def serialiser_entete(m):
    return {"id": m.id, "nom": m.nom, "lignes_gauche": normaliser_lignes(m.lignes_gauche),
            "lignes_droite": normaliser_lignes(m.lignes_droite), "ordre": m.ordre, "par_defaut": m.par_defaut}


def _contenu_entete(m):
    return {"nom": m.nom, "gauche": m.lignes_gauche, "droite": m.lignes_droite, "ordre": m.ordre, "par_defaut": m.par_defaut}


def _fixer_defaut(m):
    """`m` devient le modèle par défaut (les autres le perdent d'abord : une seule ligne par défaut par entreprise)."""
    from .models import ModeleEntete
    ModeleEntete.objects.filter(entreprise_id=m.entreprise_id, par_defaut=True).exclude(pk=m.pk).update(par_defaut=False)
    m.par_defaut = True


def creer_entete(entreprise, utilisateur, saisie, ip=""):
    """Crée un modèle d'entête (administrateur). Le premier modèle de l'entreprise est le modèle par défaut d'office ;
    sinon, `par_defaut` vrai le fixe. L'ordre est le suivant disponible."""
    from django.db import IntegrityError, transaction
    from django.db.models import Max
    from .models import ModeleEntete
    nom = _nom(saisie.get("nom"))
    try:
        with transaction.atomic():
            _verifier_nom_libre(ModeleEntete, entreprise.id, nom)
            existants = ModeleEntete.objects.filter(entreprise=entreprise)
            dernier = existants.aggregate(d=Max("ordre"))["d"]
            m = ModeleEntete(entreprise=entreprise, nom=nom, ordre=0 if dernier is None else dernier + 1,
                             lignes_gauche=normaliser_lignes(saisie.get("lignes_gauche")),
                             lignes_droite=normaliser_lignes(saisie.get("lignes_droite")))
            if bool(saisie.get("par_defaut")) or not existants.filter(par_defaut=True).exists():
                _fixer_defaut(m)
            m.save()
    except IntegrityError:
        raise ErreurModele("nom_pris", 409)
    journaliser_modele(entreprise, utilisateur, "entete", "cree", m.pk, m.nom, apres=_contenu_entete(m), ip=ip)
    return m


def modifier_entete(m, utilisateur, saisie, ip=""):
    """Modifie un modèle d'entête (administrateur) : nom, lignes, ordre, et `par_defaut` vrai pour le fixer. On ne retire pas
    le défaut à un modèle : on fixe un autre modèle (l'entreprise garde toujours un défaut)."""
    from django.db import IntegrityError, transaction
    avant = _contenu_entete(m)
    try:
        with transaction.atomic():
            if "nom" in saisie:
                nom = _nom(saisie.get("nom"))
                _verifier_nom_libre(type(m), m.entreprise_id, nom, sauf_pk=m.pk)
                m.nom = nom
            if "lignes_gauche" in saisie:
                m.lignes_gauche = normaliser_lignes(saisie.get("lignes_gauche"))
            if "lignes_droite" in saisie:
                m.lignes_droite = normaliser_lignes(saisie.get("lignes_droite"))
            if "ordre" in saisie:
                try:
                    m.ordre = max(0, int(saisie.get("ordre")))
                except (TypeError, ValueError):
                    pass
            if saisie.get("par_defaut") and not m.par_defaut:
                _fixer_defaut(m)
            m.save()
    except IntegrityError:
        raise ErreurModele("nom_pris", 409)
    apres = _contenu_entete(m)
    if apres != avant:
        journaliser_modele(m.entreprise, utilisateur, "entete", "modifie", m.pk, m.nom, avant=avant, apres=apres, ip=ip)
    return m


def supprimer_entete(m, utilisateur, ip=""):
    """Supprime un modèle d'entête (administrateur). Les brouillons qui le choisissaient (et les modèles de documents qui le
    reprenaient) retombent sur le modèle par défaut ; les PDF déjà émis sont figés et ne changent pas. Si c'était le modèle
    par défaut, le premier modèle restant (par ordre) le devient."""
    from django.db import transaction
    from .models import ModeleEntete
    entreprise, pk, nom, avant = m.entreprise, m.pk, m.nom, _contenu_entete(m)
    etait_defaut, promu = m.par_defaut, None
    with transaction.atomic():
        m.delete()
        if etait_defaut:
            promu = ModeleEntete.objects.filter(entreprise=entreprise).first()
            if promu is not None:
                promu.par_defaut = True
                promu.save(update_fields=["par_defaut"])
    journaliser_modele(entreprise, utilisateur, "entete", "supprime", pk, nom, avant=avant, ip=ip,
                       details={"nouveau_defaut": promu.pk if promu is not None else None})
    if promu is not None:
        journaliser_modele(entreprise, utilisateur, "entete", "modifie", promu.pk, promu.nom, ip=ip,
                           avant={"par_defaut": False}, apres={"par_defaut": True}, details={"cause": "suppression_du_defaut"})


def reordonner_entetes(entreprise, utilisateur, ids, ip=""):
    """Nouvel ordre d'affichage : les modèles de `ids` d'abord, dans cet ordre ; les autres à leur suite, dans leur ordre actuel."""
    from django.db import transaction
    from .models import ModeleEntete
    modeles = list(ModeleEntete.objects.filter(entreprise=entreprise))
    par_id = {m.pk: m for m in modeles}
    voulus = []
    for valeur in ids if isinstance(ids, (list, tuple)) else []:
        try:
            pk = int(valeur)
        except (TypeError, ValueError):
            continue
        if pk in par_id and pk not in voulus:
            voulus.append(pk)
    final = [par_id[pk] for pk in voulus] + [m for m in modeles if m.pk not in voulus]
    with transaction.atomic():
        for rang, m in enumerate(final):
            if m.ordre != rang:
                ancien, m.ordre = m.ordre, rang
                m.save(update_fields=["ordre"])
                journaliser_modele(entreprise, utilisateur, "entete", "modifie", m.pk, m.nom, ip=ip,
                                   avant={"ordre": ancien}, apres={"ordre": rang})
    return final


def entete_par_defaut_ou_creer(entreprise, utilisateur, ip=""):
    """Modèle par défaut de l'entreprise ; s'il n'y en a aucun, crée « Par défaut » (vide). Sert l'éditeur de la page
    « Configuration des documents », qui modifie les lignes imposées du modèle par défaut."""
    from engine.i18n import t
    from .models import ModeleEntete
    m = ModeleEntete.objects.filter(entreprise=entreprise, par_defaut=True).first()
    return m or creer_entete(entreprise, utilisateur, {"nom": t("engine/modeles_documents.py:nom_par_defaut")}, ip=ip)


# ─────────────────────────────────────────────────────────────
# Modèles de documents
# ─────────────────────────────────────────────────────────────
def serialiser_document_modele(m):
    from .reports._part_005 import normaliser_corps
    return {"id": m.id, "nom": m.nom, "type_document": m.type_document, "titre": m.titre,
            "corps": normaliser_corps(m.corps), "modele_entete": m.modele_entete_id}


def _contenu_document(m):
    return {"nom": m.nom, "type_document": m.type_document, "titre": m.titre, "corps": m.corps, "modele_entete": m.modele_entete_id}


def _appliquer_saisie_document(m, saisie):
    from .models import DocumentAdministratif, ModeleDocument
    from .reports._part_005 import normaliser_corps, MAX_TITRE
    if "nom" in saisie or not m.pk:
        nom = _nom(saisie.get("nom"))
        _verifier_nom_libre(ModeleDocument, m.entreprise_id, nom, sauf_pk=m.pk)
        m.nom = nom
    if "type_document" in saisie:
        m.type_document = DocumentAdministratif.normaliser_type(saisie.get("type_document"))
    if "titre" in saisie:
        m.titre = " ".join(str(saisie.get("titre") or "").split())[:MAX_TITRE]
    if "corps" in saisie:
        m.corps = normaliser_corps(saisie.get("corps"))
    if "modele_entete" in saisie:
        m.modele_entete = entete_de_l_entreprise(m.entreprise_id, saisie.get("modele_entete"))


def creer_modele_document(entreprise, utilisateur, saisie, ip=""):
    from django.db import IntegrityError, transaction
    from .models import ModeleDocument
    m = ModeleDocument(entreprise=entreprise)
    try:
        with transaction.atomic():
            _appliquer_saisie_document(m, saisie)
            m.save()
    except IntegrityError:
        raise ErreurModele("nom_pris", 409)
    journaliser_modele(entreprise, utilisateur, "document", "cree", m.pk, m.nom, apres=_contenu_document(m), ip=ip)
    return m


def modifier_modele_document(m, utilisateur, saisie, ip=""):
    from django.db import IntegrityError, transaction
    avant = _contenu_document(m)
    try:
        with transaction.atomic():
            _appliquer_saisie_document(m, saisie)
            m.save()
    except IntegrityError:
        raise ErreurModele("nom_pris", 409)
    apres = _contenu_document(m)
    if apres != avant:
        journaliser_modele(m.entreprise, utilisateur, "document", "modifie", m.pk, m.nom, avant=avant, apres=apres, ip=ip)
    return m


def supprimer_modele_document(m, utilisateur, ip=""):
    entreprise, pk, nom, avant = m.entreprise, m.pk, m.nom, _contenu_document(m)
    m.delete()
    journaliser_modele(entreprise, utilisateur, "document", "supprime", pk, nom, avant=avant, ip=ip)
