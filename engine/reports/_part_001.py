from engine.i18n import t as _pt, tl as _ptl, tf as _ptf
from engine.i18n import t, tf, tl
from ._header import *  # noqa
from ..couleur_colonne import sur_blanc as _flat  # noqa  (couleur « #rrggbbaa » -> « #rrggbb » lisible par reportlab)
from engine.i18n import t as _gt  # noqa: E402

# Contextes où l'entête officiel s'imprime TOUJOURS dès que des lignes sont définies (aucun réglage pour le désactiver) :
# « courrier » (éditeur de documents administratifs) et « table » (export de table + Résumé général). Les autres
# contextes (« ia », « systeme ») gardent l'activation par l'administrateur (afficher_entete_officiel).
CONTEXTES_ENTETE_PERMANENT = ("courrier", "table")


def _bandeau_entreprise(entreprise, largeur_disponible, elements, enregistrement=None, table=None, contexte="systeme", compact=False,
                       modele_entete_id=None, membre_entete_id=None, numero_entete=""):
    """Ajoute le bandeau logo + nom + coordonnées de l'entreprise en tête
    des `elements` fournis, avant tout autre contenu. `largeur_disponible`
    est la largeur utile de la page (points), typiquement `doc.width`
    après construction du SimpleDocTemplate — cela fonctionne aussi bien
    en portrait qu'en paysage. `enregistrement` (optionnel) : l'objet dont
    ce PDF traite précisément (ex. la ligne d'un reçu de soumission) — si
    fourni et que la photo d'entité est active POUR CE CONTEXTE (voir
    `contexte`), la photo tirée de la colonne configurée est affichée à
    l'opposé du logo. La photo identité fixe (par table) l'emporte si les
    deux sont actives.

    `contexte` sélectionne la configuration indépendante à appliquer pour
    la photo d'entité — 'table' (export de table), 'ia' (document/rapport
    généré par le moteur IA) ou 'systeme' (reçus, liste employés — défaut).
    Logo et couleurs restent partagés (ConfigurationIA), obligatoires sur
    tout document quel que soit le contexte — voir
    ConfigurationIA.configuration_pour."""
    import os
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import HRFlowable

    if entreprise is None:
        return

    cfg = getattr(entreprise, "configuration_ia", None)
    couleur_texte_bandeau = colors.HexColor("#1a1a2e")
    if cfg and getattr(cfg, "couleur_texte_document", None):
        try:
            couleur_texte_bandeau = colors.HexColor(cfg.couleur_texte_document)
        except Exception:
            pass

    styles_base = _feuille_styles()
    nom_style = ParagraphStyle(
        "BandeauEntrepriseNom", parent=styles_base["Normal"],
        fontSize=9.5 if compact else 12, fontName=_pol("gras"), textColor=couleur_texte_bandeau,
    )
    info_style = ParagraphStyle(
        "BandeauEntrepriseInfo", parent=styles_base["Normal"],
        fontSize=6.5 if compact else 8, leading=8 if compact else 10, textColor=colors.HexColor("#6b7280"),
    )

    nom = (getattr(entreprise, "nom", None) or str(entreprise) or "").strip()
    adresse = (getattr(entreprise, "adresse", "") or "").replace("\n", ", ").strip()
    try:
        contact = entreprise.coordonnees_document() or ""
    except Exception:
        contact = ""
    ligne_infos = "  ·  ".join(x for x in [adresse, contact] if x)

    bloc_texte = [Paragraph(nom or _pt("engine/reports/_part_001.py:pdf:64:35"), nom_style)]
    if ligne_infos:
        bloc_texte.append(Paragraph(ligne_infos, info_style))

    chemin_logo = None
    if not cfg or cfg.afficher_logo_document:
        try:
            if getattr(entreprise, "logo", None):
                chemin_logo = entreprise.logo.path
                if not os.path.isfile(chemin_logo):
                    chemin_logo = None
        except Exception:
            chemin_logo = None

    logo_img = None
    if chemin_logo:
        try:
            logo_img = RLImage(chemin_logo, width=(1.1 if compact else 1.8) * cm, height=(1.1 if compact else 1.8) * cm, kind="proportional")
        except Exception:
            logo_img = None

    # Photo identité : une image FIXE, toujours positionnée au même endroit
    # (opposé au logo), quelle que soit l'origine du document — seule la
    # CONFIGURATION change selon le contexte : 'table' -> photo propre à la
    # table (TableDynamique.photo_identite_document) ; 'ia' -> photo propre
    # à la carte de document (ConfigurationDocumentContexte.image_entite,
    # contexte 'ia') ; 'systeme' (reçus/liste employés) -> aucune photo.
    photo_img = None
    chemin_photo_bandeau = None
    if contexte == "table" and table and table.afficher_photo_identite_document and table.photo_identite_document:
        try:
            if os.path.isfile(table.photo_identite_document.path):
                chemin_photo_bandeau = table.photo_identite_document.path
        except Exception:
            chemin_photo_bandeau = None
    elif contexte == "ia" and cfg:
        cfg_ctx = cfg.configuration_pour("ia")
        if cfg_ctx.afficher_photo_entite and cfg_ctx.image_entite:
            try:
                if os.path.isfile(cfg_ctx.image_entite.path):
                    chemin_photo_bandeau = cfg_ctx.image_entite.path
            except Exception:
                chemin_photo_bandeau = None
    if chemin_photo_bandeau:
        try:
            photo_img = RLImage(chemin_photo_bandeau, width=1.6 * cm, height=1.6 * cm, kind="proportional")
        except Exception:
            photo_img = None

    # ── Entête OFFICIEL (lignes gauche / droite, logo au centre) : remplace le bandeau classique quand l'administrateur l'a
    # activé pour ce contexte — toujours actif pour « courrier » (éditeur de documents administratifs) — et que des lignes
    # existent (imposées par l'administrateur, complétées par l'agent). Sans lignes : bandeau habituel, rien ne change. ──
    try:
        cfg_ctx_entete = cfg.configuration_pour(contexte) if cfg else None
    except Exception:
        cfg_ctx_entete = None
    permanent = contexte in CONTEXTES_ENTETE_PERMANENT
    if permanent or (cfg_ctx_entete is not None and cfg_ctx_entete.afficher_entete_officiel):
        from ..entete_officiel import entete_complet
        avec_coordonnees = bool(cfg_ctx_entete is not None and cfg_ctx_entete.afficher_coordonnees_entete)
        officiel = entete_complet(
            entreprise, largeur_disponible, _polices_entete(),
            logo=logo_img, photo=photo_img, couleur=couleur_texte_bandeau, compact=compact,
            ligne_infos=" · ".join(x for x in [nom, ligne_infos] if x) if avec_coordonnees else "",
            modele_id=modele_entete_id, membre_id=membre_entete_id, numero=numero_entete,
        )
        if officiel:
            elements.extend(officiel)
            return

    largeur_case = (1.4 if compact else 2.2) * cm
    if logo_img and photo_img:
        tbl = Table(
            [[logo_img, bloc_texte, photo_img]],
            colWidths=[largeur_case, largeur_disponible - 2 * largeur_case, largeur_case],
        )
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",  (2, 0), (2, 0), "RIGHT"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
    elif logo_img or photo_img:
        image_seule = logo_img or photo_img
        tbl = Table(
            [[image_seule, bloc_texte]],
            colWidths=[largeur_case, largeur_disponible - largeur_case],
        )
        tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
    else:
        tbl = Table([[bloc_texte]], colWidths=[largeur_disponible])
        tbl.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

    elements.append(tbl)
    elements.append(Spacer(1, (0.12 if compact else 0.25) * cm))
    elements.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#d1d5db")))
    elements.append(Spacer(1, (0.2 if compact else 0.4) * cm))


def _filtre_table(queryset, table):
    """Restreint un queryset (signatures ou tout objet à FK `table` nullable)
    aux entrées valables pour la `table` donnée : celles sans table assignée
    (s'appliquent partout) + celles assignées précisément à cette table."""
    from django.db.models import Q
    if table is not None:
        return queryset.filter(Q(table__isnull=True) | Q(table=table))
    return queryset.filter(table__isnull=True)


def _filtre_signatures(queryset, table=None, document_id=None, demande_id=None):
    """Comme `_filtre_table`, mais pour les signatures : combine la
    restriction par TABLE (voir `_filtre_table`) ET par CARTE PRÉCISE, que
    ce soit une carte de document (`SignatureDocument.document`) OU une
    carte d'analyse (`SignatureDocument.demande`) — une signature réservée
    à une carte précise ne s'affiche que sur cette carte ; les autres (sans
    carte assignée, sur les deux dimensions) restent visibles partout où
    leur table le permet. `document_id` et `demande_id` correspondent à deux
    pipelines PDF distincts et ne sont normalement jamais fournis ensemble."""
    from django.db.models import Q
    qs = _filtre_table(queryset, table)
    if document_id is not None:
        qs = qs.filter(Q(document__isnull=True) | Q(document_id=document_id))
    else:
        qs = qs.filter(document__isnull=True)
    if demande_id is not None:
        qs = qs.filter(Q(demande__isnull=True) | Q(demande_id=demande_id))
    else:
        qs = qs.filter(demande__isnull=True)
    return qs


def _bloc_signatures_pied(entreprise, elements, table=None, contexte="systeme", demande_id=None, largeur_max=17 * cm,
                          type_document=None, detail=False, mention="", date_txt=""):
    """Ajoute le bloc signature(s) actives de l'entreprise en fin de
    N'IMPORTE QUEL pdf exporté (rapport, reçu, export de table, export
    employés), si la configuration document les a activées POUR CE
    CONTEXTE (voir ConfigurationIA.configuration_pour — 'table', 'ia' ou
    'systeme', indépendants les uns des autres). Indépendante des styles
    du canal Document (`_bloc_signatures_doc`) pour rester utilisable
    partout. `table` (optionnel) restreint aux signatures assignées à
    cette table précise + celles sans table (toutes tables). `demande_id`
    (optionnel) restreint en plus aux signatures sans carte d'analyse
    assignée + celles assignées à CETTE carte d'analyse précise (rapport
    généré par `generer_rapport_pdf`) — voir `_filtre_signatures`.

    Documents administratifs : `type_document` ne garde que les signataires de ce type de document (une signature
    réservée à certains types ne s'imprime sur aucun autre document) ; `detail=True` imprime sous chaque signature la
    fonction (précédée de la mention de délégation `mention` : « P.O. » ou « P/ »), le nom et la date `date_txt`."""
    if entreprise is None:
        return
    cfg = getattr(entreprise, "configuration_ia", None)
    cfg = cfg.configuration_pour(contexte) if cfg else None
    if not cfg or not cfg.afficher_signatures_document:
        return
    actives = [
        s for s in _filtre_signatures(signatures_document_pour(entreprise).filter(actif=True), table, demande_id=demande_id)
        .order_by("ordre", "id") if s.applique_au_type(type_document)
    ]
    if not actives:
        return

    import os
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import HRFlowable

    styles_base = _feuille_styles()
    style_nom = ParagraphStyle(
        "SignatureNomPied", parent=styles_base["Normal"],
        fontSize=9, alignment=TA_CENTER, textColor=colors.HexColor("#6b7280"),
    )

    largeur_case = min(5 * cm, largeur_max / len(actives))
    cellules = []
    for sig in actives:
        contenu = []
        try:
            chemin = sig.image.path if sig.image and os.path.isfile(sig.image.path) else None
        except Exception:
            chemin = None
        if chemin:
            try:
                contenu.append(RLImage(chemin, width=largeur_case - 0.4 * cm, height=2 * cm, kind="proportional"))
            except Exception:
                pass
        contenu.append(HRFlowable(width="80%", thickness=0.5, color=colors.HexColor("#9ca3af")))
        if detail:
            from xml.sax.saxutils import escape as _echapper
            style_fonction = ParagraphStyle("SignatureFonctionPied", parent=style_nom, fontName=_pol("gras"),
                                            textColor=colors.HexColor("#374151"))
            fonction = " ".join(x for x in (mention, sig.fonction) if x).strip()
            if fonction:
                contenu.append(Paragraph(_echapper(fonction), style_fonction))
            if sig.nom:
                contenu.append(Paragraph(_echapper(sig.nom), style_nom))
            if date_txt:
                contenu.append(Paragraph(_echapper(date_txt), style_nom))
        elif sig.nom:
            contenu.append(Paragraph(sig.nom, style_nom))
        cellules.append(contenu)

    elements.append(Spacer(1, 0.8 * cm))
    tbl = Table([cellules], colWidths=[largeur_case] * len(actives))
    tbl.setStyle(TableStyle([
        ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(tbl)


def _bloc_cachet_simple(entreprise, elements, enregistrement=None, table=None, contexte="systeme", demande_id=None, compact=False):
    """Ajoute le cachet configuré (textes/images modifiables, pas figés sur
    « Payé »/« Non payé ») sur n'importe quel PDF exporté, selon la
    configuration propre à `contexte` ('table', 'ia' ou 'systeme' — voir
    ConfigurationIA.configuration_pour).

    Deux modes, pour que le même cachet serve à la fois de statut PAR
    enregistrement et de tampon générique SANS enregistrement :
      - `enregistrement` fourni + `champ_statut_cachet` configuré ET
        présent sur cet enregistrement → l'état actif/inactif est lu
        depuis cette colonne (ex. reçu de ligne : Payé/Non payé réel).
      - sinon (liste, rapport, export employés… ou colonne non configurée)
        → repli sur l'état "actif" fixe, donc sur `cachet_texte_actif` /
        `cachet_image_actif` tels quels — exactement l'usage "texte modi-
        fiable pour d'autres usages" (ex. CONFIDENTIEL, BROUILLON…).

    `table` (optionnel) : le cachet configuré avec une table précise
    (`table_cachet_document`) ne s'affiche que pour cette table ; celui
    sans table assignée s'affiche partout (même principe que les
    signatures — voir `_filtre_table`). `demande_id` (optionnel) : si le
    cachet est réservé à une carte d'analyse précise (`cfg.demande_cachet_id`),
    ne s'affiche que pour cette carte d'analyse (rapport généré par
    `generer_rapport_pdf`)."""
    if entreprise is None:
        return
    cfg = getattr(entreprise, "configuration_ia", None)
    cfg = cfg.configuration_pour(contexte) if cfg else None
    if not cfg or not cfg.afficher_cachet_document:
        return
    if cfg.table_cachet_document_id and cfg.table_cachet_document_id != getattr(table, "id", None):
        return
    if cfg.demande_cachet_id and cfg.demande_cachet_id != demande_id:
        return

    actif = True
    if enregistrement is not None and cfg.champ_statut_cachet:
        donnees = enregistrement.to_dict()
        if cfg.champ_statut_cachet in donnees:
            val = donnees.get(cfg.champ_statut_cachet)
            actif = val if isinstance(val, bool) else str(val or "").strip().lower() in ("1", "true", "oui", "yes", "vrai")

    import os
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle

    texte, champ_image = cfg.cachet_pour_etat(actif)
    couleur = colors.HexColor("#16a34a") if actif else colors.HexColor("#dc2626")

    chemin_image = None
    try:
        if champ_image and os.path.isfile(champ_image.path):
            chemin_image = champ_image.path
    except Exception:
        chemin_image = None

    elements.append(Spacer(1, 0.4 * cm))
    if chemin_image:
        try:
            img = RLImage(chemin_image, width=(1.8 if compact else 3) * cm, height=(1.8 if compact else 3) * cm, kind="proportional")
            img.hAlign = "RIGHT"
            elements.append(img)
            return
        except Exception:
            pass

    styles_base = _feuille_styles()
    style_cachet = ParagraphStyle(
        "CachetSimple", parent=styles_base["Normal"], fontSize=11,
        textColor=colors.white, alignment=TA_CENTER, fontName=_pol("gras"),
    )
    tbl = Table([[Paragraph(texte, style_cachet)]], colWidths=[(3 if compact else 4) * cm], hAlign="RIGHT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), couleur),
        ("PADDING",    (0, 0), (-1, -1), 7),
        ("ROUNDEDCORNERS", [4]),
    ]))
    elements.append(tbl)


# Cles interpretees a part (texte long / titre) : elles ont un rendu dedie
# plutot qu'un tableau cle/valeur brut. 'figures' n'apparait jamais ici : le
# script la renvoie en specification, l'agent l'a deja transformee en images
# (parametre `graphiques`) avant d'appeler cette fonction.
def _titre_lisible(cle: str) -> str:
    return cle.replace("_", " ").capitalize()


def _rendre_valeur(cle, valeur, styles, elements, couleurs=None, couleur=""):
    """Rend UNE cle du resultat, quel que soit son type, sans rien filtrer.
    `couleurs` (optionnel) : dict issu de `_couleurs_rapport(entreprise)` —
    si fourni, l'entête des tableaux clé/valeur reprend la couleur
    « couleur_tableau_entete_document » de la config de l'entreprise
    (repli sur le gris-bleu par défaut de la plateforme sinon)."""
    if valeur in (None, "", [], {}):
        return

    fond_entete = (couleurs or {}).get("entete", colors.HexColor("#1f2937"))
    couleur = _couleur_valide(couleur)   # couleur choisie pour cette section (rapport IA) : bandeau, bloc teinté, en-tête de tableau
    if couleur:
        from ..couleur_colonne import couleur_texte, teinte_claire
        fond_entete = colors.HexColor(_flat(couleur))
        largeur = A4[0] - 4 * cm
        bandeau = Table([[Paragraph(_titre_lisible(cle), ParagraphStyle("BandeauRapport", parent=styles["Heading2"], textColor=colors.HexColor(couleur_texte(couleur)), spaceBefore=0, spaceAfter=0))]], colWidths=[largeur])
        bandeau.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fond_entete), ("PADDING", (0, 0), (-1, -1), 5)]))
        elements.append(bandeau)
        elements.append(Spacer(1, 0.15 * cm))
    else:
        elements.append(Paragraph(_titre_lisible(cle), styles["Heading2"]))

    if cle in CLES_TEXTE_LIBRE and isinstance(valeur, str) and couleur:
        bloc = Table([[Paragraph(valeur, styles["Normal"])]], colWidths=[largeur])
        bloc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(teinte_claire(couleur))),
                                  ("BOX", (0, 0), (-1, -1), 0.8, fond_entete), ("PADDING", (0, 0), (-1, -1), 6)]))
        elements.append(bloc)
    elif cle in CLES_TEXTE_LIBRE and isinstance(valeur, str):
        elements.append(Paragraph(valeur, styles["Normal"]))

    elif isinstance(valeur, dict):
        lignes = [[_pt("engine/reports/_part_001.py:pdf:407:19"), t("engine/reports/_part_001.py:g:362:34")]] + [[str(k), str(v)] for k, v in valeur.items()]
        table = Table(lignes, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), fond_entete),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        elements.append(table)

    elif isinstance(valeur, list):
        if valeur and isinstance(valeur[0], dict):
            entetes = list(valeur[0].keys())
            lignes = [entetes] + [[str(item.get(c, "")) for c in entetes] for item in valeur]
            table = Table(lignes, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), fond_entete),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]))
            elements.append(table)
        else:
            for item in valeur:
                elements.append(Paragraph(f"• {item}", styles["Normal"]))

    else:
        elements.append(Paragraph(str(valeur), styles["Normal"]))

    elements.append(Spacer(1, 0.5 * cm))


def _elements_rapport_demande(demande, titre, resultat, graphiques, largeur) -> list:
    """Éléments du rapport d'une carte d'analyse (sans document) : partagés par le PDF de la carte et par le rapport général."""
    styles = _feuille_styles()
    elements = []
    couleurs = _couleurs_rapport(demande.entreprise)

    _bandeau_entreprise(demande.entreprise, largeur, elements, contexte="ia")

    couleurs_dem = getattr(demande, "couleurs", None) or {}
    couleur_carte = _couleur_valide(couleurs_dem.get("_carte"))   # carte entière : titre + sections sans couleur propre
    titre_style = ParagraphStyle("RapportTitre", parent=styles["Title"], textColor=colors.HexColor(_flat(couleur_carte)) if couleur_carte else couleurs["accent"])
    elements.append(Paragraph(titre, titre_style))
    elements.append(Spacer(1, 0.5 * cm))
    elements.append(Paragraph(_ptf("engine/reports/_part_001.py:pdf:452:30", v1=demande.texte), styles["Normal"]))
    elements.append(Spacer(1, 0.5 * cm))

    for cle, valeur in (resultat or {}).items():
        if cle == "figures":
            continue  # deja rendu ci-dessous, converti en images
        _rendre_valeur(cle, valeur, styles, elements, couleurs=couleurs, couleur=_couleur_valide(couleurs_dem.get(cle)) or couleur_carte)

    for graphique in graphiques or []:
        elements.append(Paragraph(graphique.get("titre", ""), styles["Heading3"]))
        image_bytes = base64.b64decode(graphique["image_base64"])
        elements.append(RLImage(io.BytesIO(image_bytes), width=14 * cm, height=9 * cm))
        elements.append(Spacer(1, 0.4 * cm))

    _bloc_cachet_simple(demande.entreprise, elements, table=demande.table_cible, contexte="ia", demande_id=demande.id)
    _bloc_signatures_pied(demande.entreprise, elements, table=demande.table_cible, contexte="ia", demande_id=demande.id)
    return elements


def generer_rapport_pdf(demande, titre: str, resultat: dict, graphiques: list) -> RapportGenere:
    """Construit le PDF a partir du resultat EXACT du script (`resultat`,
    c'est `demande.resultat_json` tel quel) + les graphiques deja calcules.
    Aucune cle n'est choisie a l'avance : tout ce que le script a produit
    est rendu, dans l'ordre ou le script l'a renvoye."""
    _police_activer(getattr(demande, "entreprise", None))
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    elements = _elements_rapport_demande(demande, titre, resultat, graphiques, doc.width)

    _construire(doc, elements, getattr(demande, "entreprise", None))
    tampon.seek(0)

    rapport = RapportGenere(demande=demande, format_fichier="pdf")
    rapport.fichier.save(f"rapport_{demande.id}.pdf", ContentFile(tampon.read()), save=True)
    return rapport


def generer_export_csv(demande, lignes: list[dict]) -> RapportGenere:
    tampon = io.StringIO()
    if lignes:
        ecrivain = csv.DictWriter(tampon, fieldnames=list(lignes[0].keys()))
        ecrivain.writeheader()
        ecrivain.writerows(lignes)

    rapport = RapportGenere(demande=demande, format_fichier="csv")
    rapport.fichier.save(
        f"export_{demande.id}.csv", ContentFile(tampon.getvalue().encode("utf-8")), save=True
    )
    return rapport


# ─────────────────────────────────────────────────────────────
# RAPPORT PDF STRUCTURÉ POUR LES SKILLS
# Structure imposée : Titre, Pourquoi, Comment, Table Stats,
# Graphiques, Explication, Analyse Prédictive, KPI,
# Conclusion, Recommandations
# ─────────────────────────────────────────────────────────────

def _couleur_valide(valeur) -> str:
    """« #rrggbb » si la valeur en est une, sinon « » : une couleur illisible ne fait jamais échouer un PDF."""
    import re as _re
    return valeur if isinstance(valeur, str) and _re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", valeur) else ""


def _elements_rapport_skill(skill, largeur) -> list:
    """Éléments du rapport d'un skill (sans document) : partagés par son PDF et par le rapport général."""
    resultat = skill.resultat_json or {}
    graphiques = skill.graphiques_json or []
    table = skill.table

    styles = _feuille_styles()
    elements = []
    couleurs = _couleurs_rapport(skill.entreprise)
    couleurs_skill = getattr(skill, "couleurs", None) or {}
    couleur_carte = _couleur_valide(couleurs_skill.get("_carte"))   # couleur de la carte entière : titre + sections sans couleur propre

    _bandeau_entreprise(skill.entreprise, largeur, elements, contexte="ia")

    # En-tête
    titre_rapport = resultat.get("titre") or tf("engine/reports/_part_001.py:g:483:46", v1=table.nom)
    titre_style = ParagraphStyle("SkillTitre", parent=styles["Title"], textColor=colors.HexColor(_flat(couleur_carte)) if couleur_carte else couleurs["accent"])
    elements.append(Paragraph(titre_rapport, titre_style))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph(tf("engine/reports/_part_001.py:g:487:31", v1=table.nom, v2=skill.id), styles["Normal"]))
    if skill.execute_le:
        elements.append(Paragraph(tf("engine/reports/_part_001.py:g:489:35", v1=skill.execute_le.strftime('%d/%m/%Y %H:%M')), styles["Normal"]))
    elements.append(Spacer(1, 0.7 * cm))

    # Sections dans l'ordre imposé
    for cle in ORDRE_SECTIONS_SKILL:
        if cle == "titre":
            continue  # déjà affiché en en-tête
        valeur = resultat.get(cle)
        if not valeur and valeur != 0:
            continue
        titre_section = TITRES_SECTIONS.get(cle, _titre_lisible(cle))
        # Couleur de la section (Skill.couleurs[cle]) sinon celle de la carte (« _carte ») : bandeau de titre en couleur pleine,
        # contenu texte en bloc teinté, en-tête de tableau en couleur. Aucune couleur = apparence habituelle.
        couleur_sec = _couleur_valide(couleurs_skill.get(cle)) or couleur_carte
        if couleur_sec:
            from ..couleur_colonne import couleur_texte, teinte_claire
            bandeau = Table([[Paragraph(titre_section, ParagraphStyle("BandeauSkill", parent=styles["Heading2"], textColor=colors.HexColor(couleur_texte(couleur_sec)), spaceBefore=0, spaceAfter=0))]], colWidths=[largeur])
            bandeau.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_flat(couleur_sec))), ("PADDING", (0, 0), (-1, -1), 5)]))
            elements.append(bandeau)
            elements.append(Spacer(1, 0.15 * cm))
        else:
            elements.append(Paragraph(titre_section, styles["Heading2"]))

        if isinstance(valeur, str) and couleur_sec:
            bloc = Table([[Paragraph(valeur, styles["Normal"])]], colWidths=[largeur])
            bloc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(teinte_claire(couleur_sec))),
                                      ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(_flat(couleur_sec))), ("PADDING", (0, 0), (-1, -1), 6)]))
            elements.append(bloc)
        elif isinstance(valeur, str):
            elements.append(Paragraph(valeur, styles["Normal"]))
        elif isinstance(valeur, dict):
            lignes = [[_pt("engine/reports/_part_001.py:pdf:568:23"), t("engine/reports/_part_001.py:g:520:38")]] + [[str(k), str(v)] for k, v in valeur.items()]
            tbl = Table(lignes, hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_flat(couleur_sec)) if couleur_sec else couleurs["entete"]),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]))
            elements.append(tbl)
        elif isinstance(valeur, list):
            for item in valeur:
                if isinstance(item, dict):
                    _rendre_valeur(cle, item, styles, elements, couleurs={**couleurs, "entete": colors.HexColor(_flat(couleur_sec))} if couleur_sec else couleurs)
                else:
                    elements.append(Paragraph(f"• {item}", styles["Normal"]))
        else:
            elements.append(Paragraph(str(valeur), styles["Normal"]))

        elements.append(Spacer(1, 0.5 * cm))

    # Graphiques
    if graphiques:
        elements.append(Paragraph(t("engine/reports/_part_001.py:g:542:35"), styles["Heading2"]))
        for graphique in graphiques:
            if graphique.get("titre"):
                elements.append(Paragraph(graphique["titre"], styles["Heading3"]))
            image_bytes = base64.b64decode(graphique["image_base64"])
            elements.append(RLImage(io.BytesIO(image_bytes), width=14 * cm, height=9 * cm))
            elements.append(Spacer(1, 0.4 * cm))

    _bloc_cachet_simple(skill.entreprise, elements, table=table, contexte="ia")
    _bloc_signatures_pied(skill.entreprise, elements, table=table, contexte="ia")
    return elements


def generer_rapport_pdf_skill(skill) -> "RapportGenere":
    """PDF structuré d'un Skill avec les sections dans l'ordre imposé."""
    _police_activer(getattr(skill, "entreprise", None))
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    elements = _elements_rapport_skill(skill, doc.width)
    table = skill.table

    _construire(doc, elements, getattr(skill, "entreprise", None))
    tampon.seek(0)

    # Créer un RapportGenere lié à la demande factice via le skill
    # On crée un DemandeAgent minimal pour respecter la FK
    from ..models import DemandeAgent
    # filter().first() : get_or_create levait MultipleObjectsReturned des qu'un doublon existait (PDF impossible)
    texte_factice = f"[SKILL #{skill.id}] {skill.nom}"
    demande_factice = DemandeAgent.objects.filter(entreprise=skill.entreprise, texte=texte_factice).order_by("id").first()
    if demande_factice is None:
        demande_factice = DemandeAgent.objects.create(
            entreprise=skill.entreprise, texte=texte_factice,
            utilisateur=skill.cree_par, table_cible=skill.table, statut="execute",
        )
    rapport = RapportGenere(demande=demande_factice, format_fichier="pdf")
    rapport.fichier.save(f"skill_{skill.id}.pdf", ContentFile(tampon.read()), save=True)
    return rapport


def generer_pdf_rapport_general(table):
    """Bouton « Rapport général » : UN seul PDF qui combine tous les rapports de la table — chaque carte d'analyse
    exécutée puis chaque skill exécuté — précédés d'une page de garde et d'un sommaire. Chaque rapport garde son
    rendu habituel (couleurs, graphiques, cachet, signatures). Sans analyse ni skill exécuté, le rapport s'adapte : il décrit la table et ses données actuelles (voir _elements_rapport_sur_donnees).
    Renvoie les octets du PDF."""
    from xml.sax.saxutils import escape as _esc
    from datetime import datetime as _dt
    from ..models import Skill
    entreprise = table.entreprise
    analyses = [d for d in DemandeAgent.objects.filter(entreprise=entreprise, table_cible=table, statut="execute")
                .exclude(texte__startswith="[SKILL #").order_by("cree_le", "id") if d.resultat_json]
    skills = [s for s in Skill.objects.filter(entreprise=entreprise, table=table, statut="execute").order_by("id")
              if s.resultat_json]
    # Adaptation : une table sans analyse ni skill exécuté (typiquement une table système, lue en direct dans le code) n'est plus
    # un refus — le rapport se construit alors sur ce qu'elle contient : structure + analyses lues sur les données actuelles.
    sur_donnees = not analyses and not skills

    _police_activer(entreprise)
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm)
    styles = _feuille_styles()
    couleurs = _couleurs_rapport(entreprise)

    entrees = []   # (type, libellé, fonction qui construit les éléments du rapport)
    for d in analyses:
        libelle = (d.texte or "").strip()[:80] or f"#{d.id}"
        entrees.append((t("engine/reports/_part_001.py:rg_type_analyse"), libelle,
                        lambda d=d: _elements_rapport_demande(
                            d, tf("engine/agent.py:g:429:15", v1=table.nom), d.resultat_json,
                            [{"titre": g.legende, "image_base64": g.image_base64} for g in d.graphiques.all()], doc.width)))
    for s in skills:
        entrees.append((t("engine/reports/_part_001.py:rg_type_skill"), s.nom or f"#{s.id}",
                        lambda s=s: _elements_rapport_skill(s, doc.width)))

    elements = []
    _bandeau_entreprise(entreprise, doc.width, elements, contexte="ia")
    titre_style = ParagraphStyle("RapportGeneralTitre", parent=styles["Title"], textColor=couleurs["accent"])
    elements.append(Paragraph(_esc(tf("engine/reports/_part_001.py:rg_titre", v1=table.nom)), titre_style))
    elements.append(Spacer(1, 0.3 * cm))
    elements.append(Paragraph(_esc(tf("engine/reports/_part_001.py:rg_genere_le", v1=_dt.now().strftime("%d/%m/%Y %H:%M"))), styles["Normal"]))
    elements.append(Spacer(1, 0.7 * cm))
    elements.append(Paragraph(_esc(t("engine/reports/_part_001.py:rg_sommaire")), styles["Heading2"]))
    for n, (type_, libelle, _f) in enumerate(entrees, 1):
        elements.append(Paragraph(_esc(f"{n}. {type_} — {libelle}"), styles["Normal"]))
    if sur_donnees:
        elements.append(Paragraph(_esc(t("engine/reports/_part_001.py:rg_sommaire_donnees")), styles["Normal"]))
    for _type, _libelle, construire in entrees:
        elements.append(PageBreak())
        elements += construire()

    if sur_donnees:
        elements += _elements_rapport_sur_donnees(table, doc.width, styles, couleurs)

    _construire(doc, elements, entreprise)
    tampon.seek(0)
    return tampon.read()



def _elements_rapport_sur_donnees(table, largeur, styles, couleurs) -> list:
    """Rapport général d'une table SANS analyse ni skill exécuté (table système, table neuve) : page « Contenu de la table »
    (type, nombre de lignes, colonnes) puis les analyses lues EN DIRECT sur les données actuelles (séries temporelles, statistiques,
    géospatial — mêmes moteurs que le résumé général). Chaque bloc est isolé : un échec donne une ligne d'explication, jamais un PDF cassé."""
    from xml.sax.saxutils import escape as _esc
    elements = [PageBreak()]
    elements.append(Paragraph(_esc(t("engine/reports/_part_001.py:rg_donnees_titre")), styles["Heading2"]))
    elements.append(Paragraph(_esc(t("engine/reports/_part_001.py:rg_donnees_intro")), styles["Normal"]))
    elements.append(Spacer(1, 0.3 * cm))

    nb_lignes = None
    try:
        from .. import parquet_store
        nb_lignes = len(parquet_store.donnees_analytiques(table))
    except Exception:
        import logging
        logging.getLogger(__name__).exception(_gt("rg5.msg.076"), table.id)
    try:
        from .. import tables_systeme
        origine = t("engine/reports/_part_001.py:rg_origine_systeme") if tables_systeme.est_systeme(table) else t("engine/reports/_part_001.py:rg_origine_table")
    except Exception:
        origine = t("engine/reports/_part_001.py:rg_origine_table")
    elements.append(Paragraph(_esc(tf("engine/reports/_part_001.py:rg_origine", v1=origine)), styles["Normal"]))
    elements.append(Paragraph(_esc(tf("engine/reports/_part_001.py:rg_nb_lignes", v1="—" if nb_lignes is None else nb_lignes)), styles["Normal"]))
    champs = list(table.champs.order_by("ordre"))
    elements.append(Paragraph(_esc(tf("engine/reports/_part_001.py:rg_nb_colonnes", v1=len(champs))), styles["Normal"]))
    for c in champs:
        elements.append(Paragraph(_esc(f"• {c.nom} ({c.get_type_champ_display()})"), styles["Normal"]))

    try:
        from ._part_004 import _elements_analyses_donnees   # défini plus loin dans le paquet : import à l'appel
        elements += _elements_analyses_donnees(table, largeur, couleurs)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(_gt("rg5.msg.077"), table.id)
        elements.append(Paragraph(_esc(t("engine/reports/_part_001.py:rg_donnees_echec")), styles["Normal"]))
    return elements


# ─────────────────────────────────────────────────────────────
# PDF DOCUMENT FORMEL : facture, reçu, bon, devis, rapport
# Structure imposée par le script IA :
#   type_document, titre, reference, date, emetteur, destinataire,
#   lignes, totaux, notes, pied_page
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# MISE EN PAGE COMMUNE À TOUS LES DOCUMENTS (une seule source de vérité)
#   * FORMATS_DOCUMENT : format de feuille et marges PAR TYPE de document (reçu = A6, résumé général = A4 paysage, le reste = A4) ;
#   * police : choisie par l'entreprise (ConfigurationIA.police_document), activée au début de chaque génération (_police_activer) ;
#   * décor de page : fond, bande et texte de pied de page, numéro de page — mêmes réglages sur TOUS les PDF (_decor_page / _construire).
# ─────────────────────────────────────────────────────────────
import threading as _threading
from reportlab.lib.pagesizes import A6 as _A6, landscape as _landscape

FORMATS_DOCUMENT = {
    "recu":           {"libelle": tl("engine/reports/_part_001.py:g:589:35"),       "taille": _A6,                "marges": (0.9 * cm, 0.9 * cm, 0.8 * cm, 1.3 * cm)},   # gauche, droite, haut, bas
    "ligne":          {"libelle": _ptl("engine/reports/_part_001.py:pdf:746:34"), "taille": _A6,      "marges": (0.9 * cm, 0.9 * cm, 0.8 * cm, 1.3 * cm)},   # tout document produit pour UNE ligne : A6 portrait
    "resume_general": {"libelle": tl("engine/reports/_part_001.py:g:590:35"),         "taille": _landscape(A4),     "marges": (1.5 * cm, 1.5 * cm, 1.5 * cm, 1.8 * cm)},
    "standard":       {"libelle": _ptl("engine/reports/_part_001.py:pdf:748:34"),       "taille": A4,                 "marges": (2.0 * cm, 2.0 * cm, 2.0 * cm, 2.0 * cm)},
}
POLICES_DOCUMENT = {
    "helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "times":     ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "courier":   ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
}
LIBELLES_POLICES = {"helvetica": tl("engine/reports/_part_001.py:g:598:34"), "times": tl("engine/reports/_part_001.py:g:598:75"), "courier": _ptl("engine/reports/_part_001.py:pdf:755:141")}
_TRANSLATION_POLICE = {"Helvetica": 0, "Helvetica-Bold": 1, "Helvetica-Oblique": 2, "Helvetica-BoldOblique": 3,
                       "Times-Roman": 0, "Times-Bold": 1, "Times-Italic": 2, "Times-BoldItalic": 3,
                       "Courier": 0, "Courier-Bold": 1, "Courier-Oblique": 2, "Courier-BoldOblique": 3}
_ETAT_PAGE = _threading.local()
_LARGEUR_A4_UTILE = 17 * cm   # largeur utile de référence (A4 portrait, marges de 2 cm) pour laquelle les blocs de document sont dessinés


def _largeur_utile():
    """Largeur utile de la feuille en cours de génération (17 cm en A4 par défaut ; ~8,7 cm en A6)."""
    return getattr(_ETAT_PAGE, "largeur", None) or _LARGEUR_A4_UTILE


def _echelle():
    """Rapport feuille courante / A4 (1 pour A4, jamais plus) : sert à réduire les largeurs fixes des blocs sur une petite feuille."""
    return min(1.0, _largeur_utile() / _LARGEUR_A4_UTILE)


def _cmx(x):
    """x centimètres de la mise en page A4, ramenés à la feuille courante (identique à `x * cm` en A4)."""
    return x * cm * _echelle()


def _fs(n):
    """Corps de police ramené à la feuille courante : inchangé en A4, réduit (6 pt minimum) sur une petite feuille."""
    e = _echelle()
    return n if e >= 1 else max(6.0, round(n * (0.6 + 0.4 * e), 1))


def _format_page(type_document: str = "standard"):
    """(taille de feuille, dict des marges {leftMargin, rightMargin, topMargin, bottomMargin}) du type de document demandé."""
    f = FORMATS_DOCUMENT.get(type_document) or FORMATS_DOCUMENT["standard"]
    g, d, h, b = f["marges"]
    return f["taille"], {"leftMargin": g, "rightMargin": d, "topMargin": h, "bottomMargin": b}


def _police_activer(entreprise) -> str:
    """Active la police de l'entreprise pour la génération en cours (thread local, remise à Helvetica si rien n'est configuré :
    jamais la police d'une autre entreprise). Retourne la clé de police."""
    cfg = getattr(entreprise, "configuration_ia", None) if entreprise is not None else None
    cle = getattr(cfg, "police_document", None) or "helvetica"
    _ETAT_PAGE.police = cle if cle in POLICES_DOCUMENT else "helvetica"
    return _ETAT_PAGE.police


def _pol(style: str = "normal") -> str:
    """Nom de la police active pour `style` : normal, gras, italique, gras_italique."""
    famille = POLICES_DOCUMENT[getattr(_ETAT_PAGE, "police", "helvetica")]
    return famille[{"normal": 0, "gras": 1, "italique": 2, "gras_italique": 3}.get(style, 0)]


def _feuille_styles():
    """getSampleStyleSheet() dont toutes les polices suivent celle de l'entreprise (même police sur tout le document)."""
    feuille = getSampleStyleSheet()
    for st in feuille.byName.values():
        rang = _TRANSLATION_POLICE.get(getattr(st, "fontName", None))
        if rang is not None:
            st.fontName = POLICES_DOCUMENT[getattr(_ETAT_PAGE, "police", "helvetica")][rang]
    return feuille


def _polices_entete():
    """Polices ReportLab de l'entreprise pour l'entête officiel (une seule définition pour tous les appelants)."""
    return {"normal": _pol("normal"), "gras": _pol("gras"), "italique": _pol("italique"), "gras_italique": _pol("gras_italique")}


def _decor_page(entreprise):
    """Callback de page (fond, bande de pied, texte de pied, numéro) identique pour tous les PDF d'une même entreprise."""
    return _dessiner_fond_page(getattr(entreprise, "configuration_ia", None) if entreprise is not None else None)


def _construire(doc, elements, entreprise=None):
    """doc.build avec le décor de page de l'entreprise (remplace les `doc.build(elements)` nus)."""
    cb = _decor_page(entreprise)
    doc.build(elements, onFirstPage=cb, onLaterPages=cb)


def _hex_ou_repli(valeur, repli):
    """Convertit une couleur '#rrggbb' venant de la config en colors.Color ;
    retombe silencieusement sur `repli` si la valeur est absente/invalide."""
    try:
        return colors.HexColor(valeur) if valeur else repli
    except Exception:
        return repli


def _dessiner_fond_page(cfg):
    """Construit le callback onFirstPage/onLaterPages qui peint, avant tout le reste, les zones de branding qui ne sont pas des flowables :
    le fond de page (couleur_fond_document), la bande de pied de page (couleur_pied_page_document), le texte de pied de page
    (texte_pied_page_document) et le numéro de page (numeroter_pages_document). Blanc par défaut → visuellement absent tant que
    l'entreprise n'a rien configuré. Utilise la taille RÉELLE de la page (A6, A4, A4 paysage…), pas toujours A4."""
    couleur_fond = _hex_ou_repli(getattr(cfg, "couleur_fond_document", None), colors.white)
    couleur_pied = _hex_ou_repli(getattr(cfg, "couleur_pied_page_document", None), colors.white)
    texte_pied = (getattr(cfg, "texte_pied_page_document", "") or "").strip()
    numeroter = bool(getattr(cfg, "numeroter_pages_document", True)) if cfg is not None else False
    couleur_texte = _hex_ou_repli(getattr(cfg, "couleur_texte_document", None), _COULEUR_MUTED)

    def _peindre(canvas_, doc_rl):
        largeur, hauteur = doc_rl.pagesize
        if couleur_fond != colors.white:
            canvas_.saveState()
            canvas_.setFillColor(couleur_fond)
            canvas_.rect(0, 0, largeur, hauteur, stroke=0, fill=1)
            canvas_.restoreState()
        bande = min(1.4 * cm, hauteur * 0.07)     # proportionnée à la feuille (A6 : plus fine)
        if couleur_pied != colors.white:
            canvas_.saveState()
            canvas_.setFillColor(couleur_pied)
            canvas_.rect(0, 0, largeur, bande, stroke=0, fill=1)
            canvas_.restoreState()
        if (texte_pied or numeroter) and getattr(canvas_, "_gy_pied_page", None) != doc_rl.page:
            canvas_._gy_pied_page = doc_rl.page   # le pied (numéro compris) n'est peint qu'une fois par page : jamais deux numéros superposés
            canvas_.saveState()
            canvas_.setFillColor(couleur_texte)
            canvas_.setFont(_pol("normal"), 6.5 if largeur < 12 * cm else 8)
            y = bande * 0.38
            if texte_pied:
                _txt_pied = texte_pied[:150 if largeur > 15 * cm else 60]
                _place = largeur - (4.2 * cm if numeroter else 1.2 * cm)   # largeur laissée au texte : il ne recouvre jamais le numéro de page
                while len(_txt_pied) > 3 and canvas_.stringWidth(_txt_pied, _pol("normal"), 6.5 if largeur < 12 * cm else 8) > _place:
                    _txt_pied = _txt_pied[:-2].rstrip() + "…"
                canvas_.drawCentredString(largeur / 2, y, _txt_pied)
            if numeroter:
                canvas_.drawRightString(largeur - 0.6 * cm, y, _ptf("engine/reports/_part_001.py:pdf:873:63", v1=doc_rl.page))
            canvas_.restoreState()

    return _peindre


def _couleurs_rapport(entreprise):
    """Résout les couleurs de branding (ConfigurationIA de l'entreprise)
    pour TOUT PDF secondaire de la plateforme (rapport d'analyse IA,
    rapport de skill, reçu de soumission, export de liste, export
    employés) — pas seulement les documents formels (generer_pdf_document).
    Repli silencieux sur les couleurs par défaut si aucune config n'existe
    encore ou si un champ est vide/invalide (voir _hex_ou_repli)."""
    cfg = getattr(entreprise, "configuration_ia", None) if entreprise is not None else None
    return {
        "accent": _hex_ou_repli(getattr(cfg, "couleur_accent_document", None), _COULEUR_ACCENT),
        "entete": _hex_ou_repli(getattr(cfg, "couleur_tableau_entete_document", None), _COULEUR_HEADER),
        "texte": _hex_ou_repli(getattr(cfg, "couleur_texte_document", None), _COULEUR_TEXTE),
        "ligne_paire": _hex_ou_repli(getattr(cfg, "couleur_lignes_alternees_document", None), colors.HexColor("#f3f4f6")),
    }


def _chemin_local_media(url_relative: str):
    """Convertit une URL MEDIA ('/media/...') stockée en base en chemin
    disque absolu, pour que ReportLab puisse ouvrir le fichier. Retourne
    None si la valeur est vide ou introuvable sur disque."""
    import os
    from django.conf import settings as dj_settings

    if not url_relative:
        return None
    media_url = dj_settings.MEDIA_URL or "/media/"
    chemin = url_relative
    if chemin.startswith(media_url):
        chemin = chemin[len(media_url):]
    elif chemin.startswith("/"):
        chemin = chemin.lstrip("/")
    from ..securite import chemin_media_sur
    chemin_abs = chemin_media_sur(chemin)  # jamais hors de MEDIA_ROOT (« ../ » dans une cellule)
    return str(chemin_abs) if chemin_abs is not None and chemin_abs.is_file() else None


def _style_doc(base, cfg=None):
    """Retourne un dict de styles ReportLab pour les documents."""
    couleur_accent = _hex_ou_repli(getattr(cfg, "couleur_accent_document", None), _COULEUR_ACCENT)
    couleur_texte = _hex_ou_repli(getattr(cfg, "couleur_texte_document", None), _COULEUR_TEXTE)
    styles = {
        "titre": ParagraphStyle("DocTitre", parent=base["Title"],
                    fontSize=_fs(22), textColor=_BLANC, spaceAfter=2,
                    fontName=_pol("gras")),
        "ref":   ParagraphStyle("DocRef", parent=base["Normal"],
                    fontSize=_fs(10), textColor=couleur_accent,
                    fontName=_pol("gras")),
        "label": ParagraphStyle("DocLabel", parent=base["Normal"],
                    fontSize=_fs(8), textColor=_COULEUR_MUTED,
                    fontName=_pol("normal"), spaceAfter=1),
        "valeur":ParagraphStyle("DocValeur", parent=base["Normal"],
                    fontSize=_fs(10), textColor=couleur_texte,
                    fontName=_pol("normal")),
        "notes": ParagraphStyle("DocNotes", parent=base["Normal"],
                    fontSize=_fs(9), textColor=_COULEUR_MUTED,
                    fontName=_pol("italique"), spaceBefore=4),
        "pied":  ParagraphStyle("DocPied", parent=base["Normal"],
                    fontSize=_fs(8), textColor=_COULEUR_MUTED,
                    alignment=TA_CENTER, fontName=_pol("normal")),
        "total_label": ParagraphStyle("TotalLabel", parent=base["Normal"],
                    fontSize=_fs(10), textColor=couleur_texte,
                    fontName=_pol("gras"), alignment=TA_RIGHT),
        "total_valeur": ParagraphStyle("TotalVal", parent=base["Normal"],
                    fontSize=_fs(10), textColor=couleur_texte,
                    fontName=_pol("gras"), alignment=TA_RIGHT),
        "grand_total": ParagraphStyle("GrandTotal", parent=base["Normal"],
                    fontSize=_fs(13), textColor=couleur_accent,
                    fontName=_pol("gras"), alignment=TA_RIGHT),
    }
    return styles


def _bloc_entete_doc(doc_data, styles, elements, type_libelle, cfg=None,
                      nom_entreprise="", chemin_logo=None, chemin_photo_entite=None):
    """Construit le bloc en-tête (fond sombre) : logo/nom entreprise (si
    configurés) + type + titre + ref + date, avec la photo de l'entité si
    la configuration la positionne en en-tête."""
    # Entête officiel (contexte « ia » activé par l'administrateur) : bloc gauche / logo / bloc droite AVANT le bandeau du
    # document. Le logo et la photo y sont placés au centre : on les retire du bandeau pour ne pas les afficher deux fois.
    if cfg is not None and getattr(cfg, "afficher_entete_officiel", False):
        from ..entete_officiel import entete_complet
        def _img(chemin, taille):
            try:
                return RLImage(chemin, width=_cmx(taille), height=_cmx(taille), kind="proportional") if chemin else None
            except Exception:
                return None
        officiel = entete_complet(
            cfg.entreprise, _largeur_utile(), _polices_entete(),
            logo=_img(chemin_logo, 1.8), photo=_img(chemin_photo_entite, 1.8),
            couleur=_hex_ou_repli(getattr(cfg, "couleur_texte_document", None), _COULEUR_TEXTE),
        )
        if officiel:
            elements.extend(officiel)
            chemin_logo = None
            chemin_photo_entite = None
    titre_txt  = doc_data.get("titre") or type_libelle
    ref_txt    = doc_data.get("reference") or ""
    date_txt   = doc_data.get("date") or ""
    couleur_accent_hex = getattr(cfg, "couleur_accent_document", None) or "#e8a33d"
    couleur_entete = _hex_ou_repli(getattr(cfg, "couleur_entete_document", None), _COULEUR_HEADER)

    bloc_textuel = [
        Paragraph(f'<font color="{couleur_accent_hex}">{type_libelle.upper()}</font>', styles["ref"]),
        Paragraph(titre_txt, styles["titre"]),
    ]
    if nom_entreprise:
        bloc_textuel.insert(0, Paragraph(
            f'<font color="#e5e7eb"><b>{nom_entreprise}</b></font>', styles["notes"]
        ))
    if ref_txt or date_txt:
        ligne_ref = _ptf("engine/reports/_part_001.py:pdf:989:20", v1=ref_txt) if ref_txt else ""
        ligne_date = tf("engine/reports/_part_001.py:g:788:22", v1=date_txt) if date_txt else ""
        sep = "  |  " if (ligne_ref and ligne_date) else ""
        bloc_textuel.append(
            Paragraph(f'<font color="#9ca3af">{ligne_ref}{sep}{ligne_date}</font>',
                      styles["notes"])
        )

    logo_img = None
    if chemin_logo:
        try:
            logo_img = RLImage(chemin_logo, width=_cmx(2.8), height=_cmx(2.8), kind="proportional")
        except Exception:
            logo_img = None

    photo_img = None
    if chemin_photo_entite:
        try:
            photo_img = RLImage(chemin_photo_entite, width=_cmx(2.2), height=_cmx(2.2), kind="proportional")
        except Exception:
            photo_img = None

    imagette = logo_img or photo_img
    position = getattr(cfg, "position_logo_document", "gauche") or "gauche"

    style_commun = [
        ("BACKGROUND", (0, 0), (-1, -1), couleur_entete),
        ("PADDING",    (0, 0), (-1, -1), 14 * _echelle()),
        ("ROUNDEDCORNERS", [6]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    if logo_img and photo_img:
        # Logo ET photo de l'entité affichés ensemble : la photo est
        # toujours placée à l'opposé du logo (si le logo est à droite, la
        # photo passe à gauche ; sinon — gauche ou centre — la photo va à
        # droite, comme demandé pour les bulletins scolaires).
        largeur_case = _cmx(2.6)
        largeur_texte = _largeur_utile() - 2 * largeur_case
        if position == "droite":
            gauche_img, droite_img = photo_img, logo_img
        else:
            gauche_img, droite_img = logo_img, photo_img
        tbl = Table(
            [[gauche_img, bloc_textuel, droite_img]],
            colWidths=[largeur_case, largeur_texte, largeur_case],
        )
        tbl.setStyle(TableStyle(style_commun + [
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ]))
    elif imagette and position == "centre":
        # Logo au-dessus, centré, puis le texte
        tbl_img = Table([[imagette]], colWidths=[_largeur_utile()])
        tbl_img.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))
        contenu = [tbl_img, Spacer(1, 0.2 * cm)] + bloc_textuel
        tbl = Table([[contenu]], colWidths=[_largeur_utile()])
        tbl.setStyle(TableStyle(style_commun))
    elif imagette and position == "droite":
        tbl = Table([[bloc_textuel, imagette]], colWidths=[_largeur_utile() - _cmx(3.5), _cmx(3.5)])
        tbl.setStyle(TableStyle(style_commun + [("ALIGN", (1, 0), (1, -1), "RIGHT")]))
    elif imagette:
        # gauche (défaut)
        tbl = Table([[imagette, bloc_textuel]], colWidths=[_cmx(3.5), _largeur_utile() - _cmx(3.5)])
        tbl.setStyle(TableStyle(style_commun))
    else:
        tbl = Table([[bloc_textuel]], colWidths=[_largeur_utile()])
        tbl.setStyle(TableStyle(style_commun))

    elements.append(tbl)
    elements.append(Spacer(1, 0.5 * cm))


def _bloc_parties(emetteur, destinataire, styles, elements, chemin_photo_entite=None, cfg=None):
    """Bloc émetteur / destinataire côte à côte. Si une photo de l'entité est
    fournie et positionnée ici (config), elle est accolée au bloc destinataire."""
    def _partie(titre, d):
        if not d:
            return [Paragraph("—", styles["valeur"])]
        blocs = [Paragraph(f'<font color="#6b7280">{titre}</font>', styles["label"])]
        if d.get("nom"):
            blocs.append(Paragraph(f'<b>{d["nom"]}</b>', styles["valeur"]))
        if d.get("adresse"):
            for ligne in str(d["adresse"]).split("\n"):
                if ligne.strip():
                    blocs.append(Paragraph(ligne.strip(), styles["valeur"]))
        if d.get("contact"):
            blocs.append(Paragraph(d["contact"], styles["notes"]))
        return blocs

    emt = _partie(_pt("engine/reports/_part_001.py:pdf:1079:18"), emetteur or {})
    dst = _partie(_pt("engine/reports/_part_001.py:pdf:1080:18"), destinataire or {})

    if chemin_photo_entite:
        try:
            photo_img = RLImage(chemin_photo_entite, width=_cmx(2), height=_cmx(2), kind="proportional")
            dst = [Table([[dst, photo_img]], colWidths=[_cmx(6), _cmx(2)],
                         style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))]
        except Exception:
            pass

    tbl = Table([[emt, dst]], colWidths=[_largeur_utile() / 2, _largeur_utile() / 2])
    style_tbl = [
        ("VALIGN",  (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",  (1, 0), (1, -1), 20 * _echelle()),
    ]
    # Couleur de fond du « corps » (bloc émetteur/destinataire) : blanc par
    # défaut (invisible), configurable depuis la page de branding — voir
    # ConfigurationIA.couleur_corps_document.
    couleur_corps = _hex_ou_repli(getattr(cfg, "couleur_corps_document", None), colors.white)
    if couleur_corps != colors.white:
        style_tbl += [
            ("BACKGROUND", (0, 0), (-1, -1), couleur_corps),
            ("PADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (1, 0), (1, -1), 20 * _echelle()),
        ]
    tbl.setStyle(TableStyle(style_tbl))
    elements.append(tbl)
    elements.append(Spacer(1, 0.6 * cm))
    elements.append(HRFlowable(width="100%", thickness=1, color=_COULEUR_ACCENT))
    elements.append(Spacer(1, 0.4 * cm))


def _bloc_lignes(lignes, styles, elements, cfg=None):
    """Tableau principal des lignes du document."""
    if not lignes:
        return

    couleur_entete_tbl = _hex_ou_repli(getattr(cfg, "couleur_tableau_entete_document", None), _COULEUR_HEADER)
    couleur_ligne_paire = _hex_ou_repli(getattr(cfg, "couleur_lignes_alternees_document", None), _COULEUR_LIGNE_P)

    # Déterminer les colonnes dynamiquement depuis la première ligne
    premiere = lignes[0] if lignes else {}
    # Colonnes fixes attendues, sinon on prend tout
    cols_fixes = ["description", "quantite", "prix_unitaire", "montant"]
    cols = [c for c in cols_fixes if c in premiere]
    # Colonnes supplémentaires éventuelles
    cols_extra = [c for c in premiere.keys() if c not in cols_fixes]
    cols = cols + cols_extra

    libelles = {
        "description": _pt("engine/reports/_part_001.py:pdf:1131:23"), "quantite": t("engine/reports/_part_001.py:g:929:51"),
        "prix_unitaire": "P.U.", "montant": t("engine/reports/_part_001.py:g:930:45"),
    }
    entetes = [libelles.get(c, c.replace("_", " ").capitalize()) for c in cols]

    compact = _echelle() < 1            # petite feuille (A6) : colonnes resserrées, texte qui passe à la ligne
    largeur_desc = 8 * cm
    largeur_num  = 1.8 * cm if compact else 2.5 * cm
    largeur_extra = 1.5 * cm if compact else 2 * cm
    col_widths = []
    for c in cols:
        if c == "description":
            col_widths.append(largeur_desc)
        elif c in ("quantite", "prix_unitaire", "montant"):
            col_widths.append(largeur_num)
        else:
            col_widths.append(largeur_extra)
    if compact:
        autres = sum(w for c, w in zip(cols, col_widths) if c != "description")
        col_widths = [max(2.5 * cm, _largeur_utile() - autres) if c == "description" else w for c, w in zip(cols, col_widths)]
    total_l = sum(col_widths)
    if total_l > _largeur_utile():       # jamais plus large que la feuille
        col_widths = [w * _largeur_utile() / total_l for w in col_widths]

    data = [entetes]
    for i, ligne in enumerate(lignes):
        row = []
        for c in cols:
            v = ligne.get(c, "")
            if isinstance(v, float) and c != "quantite":
                row.append(f"{v:,.2f}")
            elif isinstance(v, (int, float)):
                row.append(str(v))
            else:
                row.append(str(v) if v is not None else "")
        data.append(row)
    if compact:
        from xml.sax.saxutils import escape as _esc
        st_c = ParagraphStyle("LigneCompacte", fontName=_pol("normal"), fontSize=_fs(9), leading=_fs(9) * 1.2)
        st_e = ParagraphStyle("LigneCompacteEnt", parent=st_c, fontName=_pol("gras"), textColor=_BLANC)
        data = [[Paragraph(_esc(str(v)), st_e if i == 0 else st_c) for v in row] for i, row in enumerate(data)]

    style_tbl = TableStyle([
        # Entête
        ("BACKGROUND",   (0, 0), (-1, 0), couleur_entete_tbl),
        ("TEXTCOLOR",    (0, 0), (-1, 0), _BLANC),
        ("FONTNAME",     (0, 0), (-1, 0), _pol("gras")),
        ("FONTSIZE",     (0, 0), (-1, 0), _fs(9)),
        ("BOTTOMPADDING",(0, 0), (-1, 0), 8 * _echelle()),
        ("TOPPADDING",   (0, 0), (-1, 0), 8 * _echelle()),
        # Corps
        ("FONTNAME",     (0, 1), (-1, -1), _pol("normal")),
        ("FONTSIZE",     (0, 1), (-1, -1), _fs(9)),
        ("TOPPADDING",   (0, 1), (-1, -1), 5 * _echelle()),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6 * _echelle() if _echelle() < 1 else 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6 * _echelle() if _echelle() < 1 else 6),
        ("BOTTOMPADDING",(0, 1), (-1, -1), 5 * _echelle()),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#374151")),
        ("ALIGN",        (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN",        (0, 0), (0, -1), "LEFT"),
    ])
    # Lignes alternées
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_tbl.add("BACKGROUND", (0, i), (-1, i), couleur_ligne_paire)

    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(style_tbl)
    elements.append(tbl)
    elements.append(Spacer(1, 0.4 * cm))


def _bloc_totaux(totaux, styles, elements, cfg=None):
    """Bloc totaux aligné à droite."""
    if not totaux:
        return

    couleur_accent = _hex_ou_repli(getattr(cfg, "couleur_accent_document", None), _COULEUR_ACCENT)

    libelles_totaux = {
        "sous_total": _pt("engine/reports/_part_001.py:pdf:1211:22"),
        "remise": _pt("engine/reports/_part_001.py:pdf:1212:18"),
        "tva": _pt("engine/reports/_part_001.py:pdf:1213:15"),
        "total_ht": _pt("engine/reports/_part_001.py:pdf:1214:20"),
        "total_ttc": _pt("engine/reports/_part_001.py:pdf:1215:21"),
        "total": _pt("engine/reports/_part_001.py:pdf:1216:17"),
        "acompte": t("engine/reports/_part_001.py:g:1001:20"),
        "reste_a_payer": t("engine/reports/_part_001.py:g:1002:26"),
    }

    lignes_tbl = []
    for cle, valeur in totaux.items():
        lib = libelles_totaux.get(cle, cle.replace("_", " ").capitalize())
        est_total = cle in ("total", "total_ttc", "reste_a_payer")
        v_str = f"{float(valeur):,.2f}" if isinstance(valeur, (int, float)) else str(valeur)
        if est_total:
            lignes_tbl.append([
                Paragraph(lib, styles["grand_total"]),
                Paragraph(v_str, styles["grand_total"]),
            ])
        else:
            lignes_tbl.append([
                Paragraph(lib, styles["total_label"]),
                Paragraph(v_str, styles["total_valeur"]),
            ])

    if not lignes_tbl:
        return

    tbl = Table(lignes_tbl, colWidths=([9 * cm, 4 * cm] if _echelle() >= 1 else [_largeur_utile() * 0.5, _largeur_utile() * 0.5]), hAlign="RIGHT")
    tbl.setStyle(TableStyle([
        ("ALIGN",   (0, 0), (-1, -1), "RIGHT"),
        ("PADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, -1), (-1, -1), 1, couleur_accent),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 0.5 * cm))


def _bloc_notes_doc(notes, styles, elements):
    """Bloc notes & conditions, précédé d'un séparateur."""
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#374151")))
    elements.append(Spacer(1, 0.3 * cm))
    if notes:
        elements.append(Paragraph(_pt("engine/reports/_part_001.py:pdf:1255:34"), styles["label"]))
        elements.append(Paragraph(notes, styles["notes"]))
        elements.append(Spacer(1, 0.3 * cm))




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
