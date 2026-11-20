"""Entête officiel des documents administratifs : bloc GAUCHE (ministère, direction, unité…) et bloc DROITE
(République, devise…), avec le logo au centre.

Principe — même schéma que le cachet et les signatures :
  * l'ADMINISTRATEUR définit des MODÈLES D'ENTÊTE nommés (ModeleEntete : « Note de service », « Attestation »…), dont un par
    défaut ; les lignes du modèle utilisé passent TOUJOURS en premier ;
  * l'AGENT choisit le modèle d'un document (DocumentAdministratif.modele_entete ; vide = modèle par défaut), ne peut pas en
    modifier les lignes, et complète à la suite (EnteteOfficiel de son membre — jamais à la place) ;
  * le résultat est appliqué à tous les PDF de la plateforme via `_bandeau_entreprise` (reports/_part_001.py), pour les
    contextes où l'administrateur l'a activé, et toujours pour les documents du contexte « courrier » (éditeur).

Deux réglages personnels s'y ajoutent (ReglageEntete, par administrateur ou agent) :
  * l'interrupteur « en-têtes gauche et droite » (onglet Action de la barre latérale) : éteint, les blocs ne sont pas pris en
    compte (les lignes restent enregistrées, le bandeau habituel est utilisé) ;
  * la NUMÉROTATION des documents administratifs : une ligne en DERNIÈRE position du bloc gauche. Pas de moteur générique : le
    texte est celui que la personne écrit ; l'aide facultative `numerotation_auto` y remplace {n} et {année} par un compteur qui
    n'avance qu'à l'émission (`attribuer_numero`), jamais à l'aperçu (`numero_apercu`).

Ce module ne dépend ni de Django ni des gabarits : la mise en page (`construire_entete`) ne reçoit que des données
simples, ce qui permet de la tester seule. Seules `lignes_effectives` / `entete_pour` lisent la base (import tardif).
"""
from xml.sax.saxutils import escape as _esc

from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

MAX_LIGNES = 12            # lignes par bloc (gauche ou droite)
MAX_TEXTE = 160            # caractères par ligne
ALIGNEMENTS = {"centre": TA_CENTER, "gauche": TA_LEFT, "droite": TA_RIGHT}


def normaliser_lignes(brut):
    """Liste quelconque (textes ou dictionnaires) -> liste propre de lignes
    {"texte", "gras", "italique", "souligne", "align"}. Les lignes vides sont ignorées, les valeurs hors limites
    ramenées aux valeurs permises : jamais d'erreur, jamais de balise injectée (l'échappement se fait au rendu)."""
    if not isinstance(brut, (list, tuple)):
        return []
    propres = []
    for item in brut:
        if isinstance(item, str):
            item = {"texte": item}
        if not isinstance(item, dict):
            continue
        texte = " ".join(str(item.get("texte") or "").split())[:MAX_TEXTE]
        if not texte:
            continue
        align = item.get("align")
        propres.append({
            "texte": texte,
            "gras": bool(item.get("gras")),
            "italique": bool(item.get("italique")),
            "souligne": bool(item.get("souligne")),
            "align": align if align in ALIGNEMENTS else "centre",
        })
        if len(propres) >= MAX_LIGNES:
            break
    return propres


def fusionner(imposees, complement):
    """Lignes de l'administrateur d'abord, puis celles de l'agent (dans la limite de MAX_LIGNES)."""
    return (normaliser_lignes(imposees) + normaliser_lignes(complement))[:MAX_LIGNES]


# ─────────────────────────────────────────────────────────────
# Lecture en base (import tardif : ce module reste utilisable sans Django)
# ─────────────────────────────────────────────────────────────
def modele_entete(entreprise_id, modele_id=None):
    """ModeleEntete à imprimer : celui demandé s'il appartient à l'entreprise, sinon le modèle par défaut, sinon None
    (aucun modèle : seules les lignes de l'agent s'impriment). Un identifiant inconnu ou supprimé retombe donc sur le défaut."""
    from .models import ModeleEntete
    modeles = ModeleEntete.objects.filter(entreprise_id=entreprise_id)
    choisi = modeles.filter(pk=modele_id).first() if modele_id else None
    return choisi or modeles.filter(par_defaut=True).first()


def lignes_effectives(entreprise_id, membre_id=None, modele_id=None):
    """((gauche, droite) du modèle d'entête, (gauche, droite) de l'agent `membre_id`) — listes normalisées."""
    from .models import EnteteOfficiel
    imposee = modele_entete(entreprise_id, modele_id)
    perso = (EnteteOfficiel.objects.filter(entreprise_id=entreprise_id, membre_id=membre_id).first()
             if membre_id else None)
    return (
        (normaliser_lignes(imposee.lignes_gauche) if imposee else [], normaliser_lignes(imposee.lignes_droite) if imposee else []),
        (normaliser_lignes(perso.lignes_gauche) if perso else [], normaliser_lignes(perso.lignes_droite) if perso else []),
    )


def entete_pour(entreprise, modele_id=None):
    """(gauche, droite) à imprimer pour le document en cours : lignes du modèle d'entête (`modele_id`, sinon le modèle par
    défaut) puis celles de l'agent qui génère le document (s'il y en a un ; tâche automatique ou administrateur : seulement
    celles du modèle)."""
    from .models import membre_agent_document
    agent = membre_agent_document(entreprise.id)
    (ig, id_), (pg, pd) = lignes_effectives(entreprise.id, agent.id if agent is not None else None, modele_id)
    return fusionner(ig, pg), fusionner(id_, pd)


# ─────────────────────────────────────────────────────────────
# Mise en page
# ─────────────────────────────────────────────────────────────
def _paragraphes(lignes, polices, taille, couleur, nom):
    """Une ligne = un Paragraph (échappé), police selon gras/italique, soulignement par <u>."""
    resultat = []
    for i, l in enumerate(lignes):
        style_police = "gras_italique" if (l["gras"] and l["italique"]) else "gras" if l["gras"] else "italique" if l["italique"] else "normal"
        style = ParagraphStyle(
            f"{nom}{i}", fontName=polices.get(style_police) or polices["normal"], fontSize=taille,
            leading=taille * 1.25, textColor=couleur, alignment=ALIGNEMENTS[l["align"]],
        )
        texte = _esc(l["texte"])
        resultat.append(Paragraph(f"<u>{texte}</u>" if l["souligne"] else texte, style))
    return resultat


def ligne_numero(numero):
    """Ligne du numéro de document : toujours la DERNIÈRE du bloc gauche (en gras, centrée comme le reste du bloc)."""
    texte = " ".join(str(numero or "").split())[:MAX_TEXTE]
    return [{"texte": texte, "gras": True, "italique": False, "souligne": False, "align": "centre"}] if texte else []


def construire_entete(gauche, droite, largeur, polices, logo=None, photo=None, couleur=None,
                      compact=False, ligne_infos="", style_infos=None, numero=""):
    """Liste de flowables de l'entête officiel : [bloc gauche | logo (+ photo) | bloc droite], puis — si `ligne_infos`
    est fourni — le nom et les coordonnées de l'entreprise en petit sous le bloc. Retourne [] si les deux blocs sont
    vides (l'appelant garde alors le bandeau habituel).

    `polices` : {"normal", "gras", "italique", "gras_italique"} (noms de polices ReportLab, ceux de l'entreprise) ;
    `logo` / `photo` : images ReportLab déjà construites, ou None. Le logo est TOUJOURS au centre quand l'entête
    officiel est actif : la position « gauche / droite » du bandeau classique n'a plus de sens entre deux blocs."""
    gauche, droite = normaliser_lignes(gauche) + ligne_numero(numero), normaliser_lignes(droite)   # le numéro vient APRÈS toutes les lignes (même au-delà de MAX_LIGNES)
    if not gauche and not droite:
        return []
    from reportlab.lib import colors
    couleur = couleur or colors.HexColor("#1a1a2e")
    taille = 7 if compact else 9

    images = [x for x in (logo, photo) if x is not None]
    largeur_centre = 0 if not images else (len(images) * (1.5 if compact else 2.3) + 0.4) * cm
    cote = (largeur - largeur_centre) / 2
    centre = None
    if images:
        centre = Table([images], colWidths=[(1.5 if compact else 2.3) * cm] * len(images))
        centre.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("PADDING", (0, 0), (-1, -1), 0)]))

    bloc_g = _paragraphes(gauche, polices, taille, couleur, "EnteteG")
    bloc_d = _paragraphes(droite, polices, taille, couleur, "EnteteD")
    if centre is not None:
        tbl = Table([[bloc_g, centre, bloc_d]], colWidths=[cote, largeur_centre, cote])
    else:
        tbl = Table([[bloc_g, bloc_d]], colWidths=[largeur / 2, largeur / 2])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("VALIGN", (1, 0), (1, 0), "MIDDLE") if centre is not None else ("VALIGN", (0, 0), (0, 0), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements = [tbl]
    if ligne_infos:
        elements.append(Spacer(1, 0.15 * cm))
        elements.append(Paragraph(_esc(ligne_infos), style_infos or ParagraphStyle(
            "EnteteInfos", fontName=polices["normal"], fontSize=taille - 1.5, alignment=TA_CENTER, textColor=colors.HexColor("#6b7280"))))
    elements.append(Spacer(1, (0.2 if compact else 0.5) * cm))
    return elements


# ─────────────────────────────────────────────────────────────
# Réglages personnels : interrupteur des en-têtes et numérotation (import tardif)
# ─────────────────────────────────────────────────────────────
def _membre_reglage(entreprise_id, membre_id=None):
    """`membre_id` s'il est donné (ex. l'auteur d'un document), sinon l'administrateur / l'agent qui génère le PDF en cours."""
    if membre_id:
        return membre_id
    from .models import MembreEntreprise, _utilisateur_document
    uid = _utilisateur_document.get()
    if not uid:
        return None
    return MembreEntreprise.objects.filter(utilisateur_id=uid, entreprise_id=entreprise_id,
                                           role__in=("admin", "agent")).values_list("id", flat=True).first()


def reglage_de(entreprise_id, membre_id):
    from .models import ReglageEntete
    return ReglageEntete.objects.filter(entreprise_id=entreprise_id, membre_id=membre_id).first() if membre_id else None


def entetes_actifs(entreprise_id, membre_id=None):
    """Faux seulement si la personne a éteint l'interrupteur « en-têtes gauche et droite » (par défaut : allumé)."""
    r = reglage_de(entreprise_id, _membre_reglage(entreprise_id, membre_id))
    return True if r is None else bool(r.entetes_actifs)


def valider_numerotation(modele, auto):
    """Chaîne vide si le texte de numérotation est acceptable. Sans l'aide automatique, c'est un texte libre (rien à valider).
    Avec elle : mêmes champs que le format de référence d'émission ({n} obligatoire, {année}, {type} ; format entier simple)."""
    modele = str(modele or "")
    if len(modele.strip()) > MAX_TEXTE:
        from engine.i18n import tf
        return tf("engine/entete_officiel.py:numero_depasse", v1=MAX_TEXTE)
    if auto:
        from .emission_documents import valider_modele_reference
        return valider_modele_reference(modele)
    return ""


def _numero_formate(r, n, annee, type_document):
    if r.numerotation_auto:
        from .emission_documents import formater_reference
        return formater_reference(r.numerotation_modele.strip(), n, annee, type_document)
    return " ".join(r.numerotation_modele.split())[:MAX_TEXTE]


def _numero_actif(r):
    return r is not None and r.entetes_actifs and r.numerotation_active and bool(r.numerotation_modele.strip())


def numero_apercu(entreprise_id, membre_id, type_document="courrier", annee=None):
    """Le numéro tel qu'il s'imprimerait maintenant, SANS consommer le compteur (épreuve de brouillon, aide de l'éditeur)."""
    from django.utils import timezone
    r = reglage_de(entreprise_id, membre_id)
    if not _numero_actif(r):
        return ""
    annee = annee or timezone.localdate().year
    prochain = (r.numerotation_dernier if r.numerotation_annee == annee else 0) + 1
    return _numero_formate(r, prochain, annee, type_document)


def attribuer_numero(entreprise_id, membre_id, type_document="courrier", annee=None):
    """Numéro à imprimer sur un document qu'on ÉMET : avec l'aide automatique, le compteur avance (sous verrou de ligne : à appeler
    dans la transaction d'émission, un numéro n'est donc consommé que si le document est émis) et repart de 1 chaque année ;
    sinon le texte libre tel quel. Chaîne vide si la numérotation ou les en-têtes sont éteints."""
    from django.utils import timezone
    from .models import ReglageEntete
    r = (ReglageEntete.objects.select_for_update().filter(entreprise_id=entreprise_id, membre_id=membre_id).first()
         if membre_id else None)
    if not _numero_actif(r):
        return ""
    annee = annee or timezone.localdate().year
    if not r.numerotation_auto:
        return _numero_formate(r, 0, annee, type_document)
    if r.numerotation_annee != annee:
        r.numerotation_annee, r.numerotation_dernier = annee, 0
    r.numerotation_dernier += 1
    numero = _numero_formate(r, r.numerotation_dernier, annee, type_document)   # erreur de format : levée AVANT l'enregistrement
    r.save(update_fields=["numerotation_annee", "numerotation_dernier"])
    return numero


def entete_complet(entreprise, largeur, polices, logo=None, photo=None, couleur=None, compact=False, ligne_infos="",
                   modele_id=None, membre_id=None, numero=""):
    """UNIQUE point d'entrée de l'entête officiel pour tous les PDF : lit les lignes du modèle d'entête (`modele_id` ; vide =
    modèle par défaut) et celles de l'agent qui génère (`entete_pour`), puis compose la mise en page (`construire_entete`).
    Retourne [] quand aucune ligne n'est définie (l'appelant garde alors son bandeau habituel). Appelée par le bandeau de
    `_bandeau_entreprise` (contextes table / système / courrier ; seul « courrier » transmet le modèle choisi du document)
    et par le bloc d'entête des documents générés par l'IA (modèle par défaut). `membre_id` : la personne dont l'interrupteur compte
    (l'auteur d'un document administratif ; sinon celle qui génère le PDF) ; `numero` : ligne de numérotation, en dernier du bloc gauche."""
    if not entetes_actifs(entreprise.id, membre_id):      # interrupteur éteint : les en-têtes gauche et droite ne comptent pas
        return []
    gauche, droite = entete_pour(entreprise, modele_id)
    return construire_entete(gauche, droite, largeur, polices, logo=logo, photo=photo, couleur=couleur,
                             compact=compact, ligne_infos=ligne_infos, numero=numero)
