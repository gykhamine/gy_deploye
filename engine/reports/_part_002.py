from engine.i18n import t as _pt, tl as _ptl, tf as _ptf
from engine.i18n import t, tf, dec as _dec
from ._header import *  # noqa
from ..couleur_colonne import sur_blanc as _flat  # noqa  (couleur « #rrggbbaa » -> « #rrggbb » lisible par reportlab)

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa


def _bloc_pied_doc(pied, styles, elements):
    """Pied de page du document."""
    if pied:
        elements.append(HRFlowable(width="100%", thickness=0.3, color=colors.HexColor("#374151")))
        elements.append(Spacer(1, 0.2 * cm))
        elements.append(Paragraph(pied, styles["pied"]))


def _bloc_cachet_doc(cfg, actif, styles, elements, table=None, type_document=None, document_id=None):
    """Cachet à deux états (ex. Payé/Non payé — textes modifiables pour
    d'autres usages). Utilise l'image téléversée pour l'état si présente,
    sinon retombe sur une pastille colorée avec le texte configuré.
    `table` (optionnel) : si le cachet est réservé à une table précise
    (`cfg.table_cachet_document`), ne s'affiche que pour cette table.
    `type_document` (optionnel) : si le cachet est réservé à un type de
    carte précis (`cfg.type_document_cachet`, ex. 'facture'), ne s'affiche
    que pour ce type.
    `document_id` (optionnel) : si le cachet est réservé à UNE carte
    précise déjà générée (`cfg.document_cachet_id`), ne s'affiche que pour
    ce document précis. Les trois filtres se combinent (ET logique)."""
    if actif is None or not cfg.afficher_cachet_document:
        return
    if cfg.table_cachet_document_id and cfg.table_cachet_document_id != getattr(table, "id", None):
        return
    if cfg.type_document_cachet and cfg.type_document_cachet != type_document:
        return
    if cfg.document_cachet_id and cfg.document_cachet_id != document_id:
        return
    texte, champ_image = cfg.cachet_pour_etat(actif)
    couleur = colors.HexColor("#16a34a") if actif else colors.HexColor("#dc2626")

    chemin_image = None
    if champ_image:
        try:
            if champ_image and hasattr(champ_image, "path"):
                p = champ_image.path
                chemin_image = p if _os_isfile(p) else None
        except Exception:
            chemin_image = None

    if chemin_image:
        try:
            img = RLImage(chemin_image, width=_cmx(3.2), height=_cmx(3.2), kind="proportional")
            img.hAlign = "RIGHT"
            elements.append(img)
            elements.append(Spacer(1, 0.3 * cm))
            return
        except Exception:
            pass

    style_cachet = ParagraphStyle(
        "Cachet", parent=styles["total_label"], fontSize=12,
        textColor=colors.white, alignment=TA_CENTER, fontName=_pol("gras"),
    )
    tbl = Table([[Paragraph(texte, style_cachet)]], colWidths=[_cmx(4.5)], hAlign="RIGHT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), couleur),
        ("BOX",          (0, 0), (-1, -1), 1.5, couleur),
        ("PADDING",      (0, 0), (-1, -1), 8),
        ("ROUNDEDCORNERS", [4]),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 0.3 * cm))


def _bloc_signatures_doc(cfg, styles, elements, table=None, document_id=None):
    """Bloc signature(s) : une colonne par signature active, image
    au-dessus d'une ligne, nom/fonction en dessous. Le nombre de
    signatures affichées est variable (deux ou plus, ou aucune).
    `table` (optionnel) restreint aux signatures assignées à cette table
    précise + celles sans table (toutes tables). `document_id` (optionnel)
    restreint en plus aux signatures sans carte précise assignée + celles
    assignées à CETTE carte précise — voir `_filtre_signatures`."""
    if not cfg.afficher_signatures_document:
        return
    actives = list(
        _filtre_signatures(signatures_document_pour(cfg.entreprise).filter(actif=True), table, document_id)
        .order_by("ordre", "id")
    )
    if not actives:
        return

    style_nom = ParagraphStyle(
        "SignatureNom", parent=styles["label"], alignment=TA_CENTER, fontSize=9,
    )
    largeur_case = min(_cmx(5), _largeur_utile() / len(actives))
    cellules = []
    for sig in actives:
        contenu = []
        try:
            chemin = sig.image.path if sig.image and _os_isfile(sig.image.path) else None
        except Exception:
            chemin = None
        if chemin:
            try:
                contenu.append(RLImage(chemin, width=largeur_case - 0.4 * cm, height=_cmx(2), kind="proportional"))
            except Exception:
                pass
        contenu.append(HRFlowable(width="80%", thickness=0.5, color=colors.HexColor("#9ca3af")))
        if sig.nom:
            contenu.append(Paragraph(sig.nom, style_nom))
        cellules.append(contenu)

    elements.append(Spacer(1, 0.6 * cm))
    tbl = Table([cellules], colWidths=[largeur_case] * len(actives))
    tbl.setStyle(TableStyle([
        ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(tbl)


def _os_isfile(chemin):
    import os
    return bool(chemin) and os.path.isfile(chemin)


def _emetteur_reel(entreprise, nom_affichage=None) -> dict:
    """Construit le bloc « DE » (émetteur) depuis les VRAIES coordonnées de
    l'entreprise (adresse, téléphone service client, email, site web —
    configurées une seule fois dans la page employés), plutôt que depuis ce
    que le script IA a pu inventer pour `resultat['emetteur']`. Garantit
    que CHAQUE document généré porte les mêmes coordonnées, cohérentes,
    sans dépendre de la fiabilité du script généré à la volée."""
    return {
        "nom": nom_affichage or entreprise.nom,
        "adresse": entreprise.adresse or "",
        "contact": entreprise.coordonnees_document(),
    }


# ─────────────────────────────────────────────────────────────
# COMPOSITION LIBRE PAR BLOCS — dispatcher générique
# Rend la clé optionnelle "blocs" du résultat du script IA (contrat complet
# dans inference.DEFAULT_REGLE_DOCUMENT : texte, image, tableau, graphique,
# section récursive, signature, cachet, saut_page). Rien ici ne remplace
# les fonctions historiques ci-dessus (_bloc_lignes, _bloc_cachet_doc,
# _bloc_signatures_doc...) : "signature"/"cachet" les réutilisent telles
# quelles pour rester cohérents avec le rendu facture/devis existant, et
# le rendu classique (10 clés) reste le chemin emprunté quand "blocs" est
# absent ou vide — voir l'aiguillage dans generer_pdf_document.
# ─────────────────────────────────────────────────────────────
def _style_texte_bloc(style_nom, styles_base, styles_doc):
    """Choisit le ParagraphStyle d'un bloc 'texte' selon son champ 'style'."""
    if style_nom == "titre":
        return styles_doc.get("titre") or styles_base["Title"]
    if style_nom == "soustitre":
        return styles_base["Heading2"]
    if style_nom == "note":
        return styles_doc.get("notes") or styles_base["Italic"]
    return styles_doc.get("valeur") or styles_base["Normal"]


def _couleur_bloc(bloc, heritee=""):
    """Couleur « #rrggbb » d'un bloc de document (clé facultative "couleur" : nom, hexadécimal), sinon celle de sa section.
    Une couleur illisible est ignorée : un bloc mal formé ne fait jamais échouer le document."""
    from ..couleur_colonne import normaliser_couleur
    try:
        return normaliser_couleur(bloc.get("couleur")) or heritee
    except ValueError:
        return heritee


def _trait_couleur(couleur, elements):
    """Filet épais en couleur au-dessus d'un bloc image/graphique (qui ne peut pas être teinté sans le déformer)."""
    if couleur:
        elements.append(HRFlowable(width="100%", thickness=3, color=colors.HexColor(_flat(couleur)), spaceAfter=4))


def _rendre_bloc_texte(bloc, styles_base, styles_doc, elements, couleur=""):
    contenu = bloc.get("contenu")
    if not contenu:
        return
    style = _style_texte_bloc(bloc.get("style"), styles_base, styles_doc)
    if couleur:   # carte colorée : fond en teinte claire, cadre en couleur pleine
        from ..couleur_colonne import teinte_claire
        style = ParagraphStyle("TexteBlocColore", parent=style, backColor=colors.HexColor(teinte_claire(couleur)),
                               borderColor=colors.HexColor(_flat(couleur)), borderWidth=1, borderPadding=5)
    elements.append(Paragraph(str(contenu), style))
    elements.append(Spacer(1, 0.4 * cm if couleur else 0.3 * cm))


def _resoudre_chemin_image_bloc(bloc, ctx):
    """Chemin disque de l'image d'un bloc 'image', selon sa 'source' :
    'champ' (valeur lue sur l'enregistrement cible), 'logo' (entreprise),
    'signature' (première signature active applicable) ou 'cachet' (image
    de l'état courant du cachet configuré)."""
    import os
    source = bloc.get("source") or "champ"
    entreprise = ctx.get("entreprise")

    if source == "logo":
        try:
            if entreprise and entreprise.logo and os.path.isfile(entreprise.logo.path):
                return entreprise.logo.path
        except Exception:
            pass
        return None

    if source == "signature":
        if not entreprise:
            return None
        actives = list(_filtre_table(
            signatures_document_pour(entreprise).filter(actif=True), ctx.get("table")
        ).order_by("ordre", "id"))
        for sig in actives:
            try:
                if sig.image and os.path.isfile(sig.image.path):
                    return sig.image.path
            except Exception:
                continue
        return None

    if source == "cachet":
        cfg = ctx.get("cfg")
        if not cfg or not cfg.afficher_cachet_document:
            return None
        _texte, champ_image = cfg.cachet_pour_etat(ctx.get("cachet_actif", True))
        try:
            if champ_image and os.path.isfile(champ_image.path):
                return champ_image.path
        except Exception:
            pass
        return None

    # source == "champ" (défaut) : valeur lue sur l'enregistrement cible —
    # même règle de généricité que le reste du moteur, aucun slug supposé.
    champ = bloc.get("champ")
    enregistrement = ctx.get("enregistrement") or {}
    if not champ or champ not in enregistrement:
        return None
    return _chemin_local_media(str(enregistrement.get(champ) or ""))


def _rendre_bloc_image(bloc, elements, ctx, couleur=""):
    chemin = _resoudre_chemin_image_bloc(bloc, ctx)
    if not chemin:
        return
    _trait_couleur(couleur, elements)
    largeur = float(bloc.get("largeur_cm") or 4) * cm
    hauteur = float(bloc.get("hauteur_cm") or 4) * cm
    if largeur > _largeur_utile():      # petite feuille : on réduit en gardant les proportions
        hauteur *= _largeur_utile() / largeur
        largeur = _largeur_utile()
    try:
        img = RLImage(chemin, width=largeur, height=hauteur, kind="proportional")
    except Exception:
        return
    elements.append(img)
    legende = bloc.get("legende")
    if legende:
        style_legende = ParagraphStyle(
            "LegendeImageBloc", fontSize=8, textColor=_COULEUR_MUTED,
            fontName=_pol("italique"), alignment=TA_CENTER,
        )
        elements.append(Paragraph(str(legende), style_legende))
    elements.append(Spacer(1, 0.3 * cm))


def _rendre_bloc_tableau(bloc, styles_base, elements, cfg=None, couleur=""):
    """Tableau générique : les colonnes sont explicitement fournies par le
    bloc ('champ'/'libelle'/'alignement'), dans l'ordre d'affichage voulu —
    contrairement à _bloc_lignes(), aucun champ n'est privilégié à l'avance
    (plus de priorité description/quantite/prix_unitaire/montant)."""
    colonnes = bloc.get("colonnes") or []
    lignes = bloc.get("lignes") or []
    if not colonnes or not lignes:
        return

    titre = bloc.get("titre")
    if titre:
        elements.append(Paragraph(str(titre), styles_base["Heading3"]))
        elements.append(Spacer(1, 0.15 * cm))

    couleur_entete_tbl = _hex_ou_repli(getattr(cfg, "couleur_tableau_entete_document", None), _COULEUR_HEADER)
    couleur_ligne_paire = _hex_ou_repli(getattr(cfg, "couleur_lignes_alternees_document", None), _COULEUR_LIGNE_P)
    couleur_texte_entete = _BLANC
    if couleur:   # tableau coloré : en-tête en couleur pleine (texte lisible), lignes paires en teinte claire
        from ..couleur_colonne import couleur_texte, teinte_claire
        couleur_entete_tbl = colors.HexColor(_flat(couleur))
        couleur_texte_entete = colors.HexColor(couleur_texte(couleur))
        couleur_ligne_paire = colors.HexColor(teinte_claire(couleur))

    entetes = [str(c.get("libelle") or _titre_lisible(c.get("champ", ""))) for c in colonnes]
    aligns = [_ALIGN_RL.get(c.get("alignement"), "LEFT") for c in colonnes]

    def _cellule(v):
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:,.2f}"
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return str(v)

    data = [entetes]
    for ligne in lignes:
        data.append([_cellule(ligne.get(c.get("champ"))) for c in colonnes])

    largeur_col = _largeur_utile() / max(len(colonnes), 1)
    tbl = Table(data, colWidths=[largeur_col] * len(colonnes), repeatRows=1)
    style_tbl = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), couleur_entete_tbl),
        ("TEXTCOLOR",    (0, 0), (-1, 0), couleur_texte_entete),
        ("FONTNAME",     (0, 0), (-1, 0), _pol("gras")),
        ("FONTSIZE",     (0, 0), (-1, -1), _fs(9)),
        ("TOPPADDING",   (0, 0), (-1, -1), 6 * _echelle()),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6 * _echelle()),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#374151")),
    ])
    for i, align in enumerate(aligns):
        style_tbl.add("ALIGN", (i, 0), (i, -1), align)
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_tbl.add("BACKGROUND", (0, i), (-1, i), couleur_ligne_paire)
    tbl.setStyle(style_tbl)
    elements.append(tbl)
    elements.append(Spacer(1, 0.4 * cm))


def _rendre_bloc_graphique(bloc, styles_base, elements, couleur=""):
    """Réutilise visualization.generer_graphiques (déjà utilisé pour les
    rapports/skills) : même moteur matplotlib, pas de code dupliqué."""
    from ..visualization import generer_graphiques
    spec = {
        "type": bloc.get("style") or "line",
        "x": bloc.get("x") or [],
        "y": bloc.get("y") or [],
        "titre": bloc.get("titre") or "",
    }
    try:
        graphiques = generer_graphiques([spec])
    except Exception:
        return
    for graphique in graphiques:
        _trait_couleur(couleur, elements)
        if graphique.get("titre"):
            elements.append(Paragraph(graphique["titre"], styles_base["Heading3"]))
        image_bytes = base64.b64decode(graphique["image_base64"])
        elements.append(RLImage(io.BytesIO(image_bytes), width=min(14 * cm, _largeur_utile()), height=min(14 * cm, _largeur_utile()) * 9 / 14))
        elements.append(Spacer(1, 0.4 * cm))


def _rendre_blocs(blocs, styles_base, styles_doc, elements, ctx, profondeur=0, couleur_section=""):
    """Rend récursivement une liste de blocs (contrat dans
    inference.DEFAULT_REGLE_DOCUMENT). `ctx` transporte le contexte commun :
    cfg (ConfigurationIA), entreprise, table, enregistrement (dict de
    l'entité cible), cachet_actif. `profondeur` borne la récursion des
    sections imbriquées (protection contre un script mal formé)."""
    if profondeur > 6 or not blocs:
        return
    for bloc in blocs:
        if not isinstance(bloc, dict):
            continue
        type_bloc = bloc.get("type")
        couleur = _couleur_bloc(bloc, couleur_section)   # carte colorée : la sienne, sinon celle de sa section

        if type_bloc == "texte":
            _rendre_bloc_texte(bloc, styles_base, styles_doc, elements, couleur)
        elif type_bloc == "image":
            _rendre_bloc_image(bloc, elements, ctx, couleur)
        elif type_bloc == "tableau":
            _rendre_bloc_tableau(bloc, styles_base, elements, ctx.get("cfg"), couleur)
        elif type_bloc == "graphique":
            _rendre_bloc_graphique(bloc, styles_base, elements, couleur)
        elif type_bloc == "section":
            titre = bloc.get("titre")
            if titre:
                style_titre = styles_base["Heading2"]
                if couleur:   # titre de section en bandeau de couleur pleine, texte lisible
                    from ..couleur_colonne import couleur_texte
                    style_titre = ParagraphStyle("TitreSectionColore", parent=style_titre, backColor=colors.HexColor(_flat(couleur)),
                                                 textColor=colors.HexColor(couleur_texte(couleur)), borderPadding=5)
                elements.append(Paragraph(str(titre), style_titre))
                elements.append(Spacer(1, 0.3 * cm if couleur else 0.2 * cm))
            _rendre_blocs(bloc.get("blocs") or [], styles_base, styles_doc, elements, ctx, profondeur + 1, couleur)
        elif type_bloc == "signature":
            cfg = ctx.get("cfg")
            if cfg is not None:
                _bloc_signatures_doc(cfg, styles_doc, elements, ctx.get("table"), ctx.get("document_id"))
        elif type_bloc == "cachet":
            cfg = ctx.get("cfg")
            if cfg is not None:
                _bloc_cachet_doc(cfg, ctx.get("cachet_actif"), styles_doc, elements, ctx.get("table"))
        elif type_bloc == "saut_page":
            elements.append(PageBreak())
        # Type de bloc inconnu : ignoré silencieusement — un bloc mal formé
        # ne doit jamais faire échouer tout le document.


def _sauvegarder_pdf_document(doc, tampon: io.BytesIO) -> str:
    """Écrit les octets PDF (déjà construits, curseur en position 0) sur
    disque au même emplacement que les documents classiques, retourne le
    chemin relatif dans MEDIA_ROOT. Factorisé pour être utilisé aussi bien
    par le rendu classique (10 clés) que par le rendu par blocs."""
    import os
    from django.conf import settings as dj_settings

    dossier_rel = os.path.join("documents",
                               str(doc.cree_le.year), str(doc.cree_le.month).zfill(2))
    dossier_abs = os.path.join(dj_settings.MEDIA_ROOT, dossier_rel)
    os.makedirs(dossier_abs, exist_ok=True)
    nom_fichier = f"document_{doc.id}.pdf"
    chemin_abs  = os.path.join(dossier_abs, nom_fichier)
    chemin_rel  = os.path.join(dossier_rel, nom_fichier)

    with open(chemin_abs, "wb") as f:
        f.write(tampon.read())

    # Événement "DocumentGenerated" (voir evenements.py) : point unique,
    # utilisé par le rendu classique ET le rendu par blocs (voir les deux
    # appelants ci-dessus).
    from .. import evenements as _evenements
    _evenements.emettre(
        doc.entreprise_id, "DocumentGenerated",
        table_id=doc.table_cible_id, document_id=doc.id,
    )

    return chemin_rel


def _rendre_images_position(images, position, elements, ctx):
    """Rend les images du flux CLASSIQUE (10 clés) rattachées à `position`
    (une section parmi 'entete'/'parties'/'lignes'/'totaux'/'notes'/
    'pied_page'). Additif : n'existe qu'en complément des 10 clés, contrairement
    à "blocs" qui remplace tout le rendu. Réutilise le même résolveur/rendu
    générique que le mode blocs (_resoudre_chemin_image_bloc /
    _rendre_bloc_image) — les images sont un élément de première classe
    quel que soit le mode de rendu du document."""
    for bloc in images or []:
        if not isinstance(bloc, dict):
            continue
        if (bloc.get("position") or "notes") != position:
            continue
        _rendre_bloc_image(bloc, elements, ctx)


def generer_pdf_document(doc, resultat: dict) -> str:
    """Document IA (facture, reçu, bon…). Un document produit pour UNE ligne (enregistrement_cible) sort en A6 portrait, comme le reçu ;
    tout autre document reste en A4. La largeur utile de la feuille est posée pour la durée de la génération (blocs adaptés à A6)."""
    pour_ligne = bool(getattr(doc, "enregistrement_cible_id", None))
    taille, marges = _format_page("ligne" if pour_ligne else "standard")
    _ETAT_PAGE.largeur = taille[0] - marges["leftMargin"] - marges["rightMargin"] if pour_ligne else None
    _ETAT_PAGE.page_doc = (taille, marges)
    try:
        return _generer_pdf_document_interne(doc, resultat)
    finally:
        _ETAT_PAGE.largeur = None
        _ETAT_PAGE.page_doc = None


def _generer_pdf_document_interne(doc, resultat: dict) -> str:
    """
    Génère le PDF du document depuis le résultat du script IA, habillé selon
    la configuration IA de l'entreprise (branding, couleurs, sections,
    logo, photo de l'entité).
    Retourne le chemin relatif dans MEDIA_ROOT (pour doc.fichier_pdf.name).
    """
    _police_activer(getattr(doc, "entreprise", None))
    from ..models import ConfigurationIA
    import os

    cfg, _ = ConfigurationIA.objects.get_or_create(entreprise=doc.entreprise)
    # Contexte 'ia' : carte de document unique générée par le moteur IA —
    # config indépendante des exports de table et des reçus/liste employés
    # (voir ConfigurationIA.configuration_pour). `cfg` reste utilisable
    # exactement comme avant (même interface) pour le reste de la fonction.
    cfg = cfg.configuration_pour("ia")

    tampon = io.BytesIO()
    _taille_doc, _marges_doc = _ETAT_PAGE.page_doc
    if _taille_doc == A4:   # A4 : marges historiques de ce document (haut 1,5 cm)
        _marges_doc = {"leftMargin": 2 * cm, "rightMargin": 2 * cm, "topMargin": 1.5 * cm, "bottomMargin": 2 * cm}
    doc_rl = SimpleDocTemplate(tampon, pagesize=_taille_doc, **_marges_doc)
    styles_base = _feuille_styles()
    styles = _style_doc(styles_base, cfg)
    elements = []

    type_libelle = {
        "facture": _pt("engine/reports/_part_002.py:pdf:489:19"), "recu": t("engine/reports/_part_002.py:g:472:39"), "bon": t("engine/reports/_part_002.py:g:472:54"),
        "devis": _pt("engine/reports/_part_002.py:pdf:490:17"), "rapport": _pt("engine/reports/_part_002.py:pdf:490:37"), "autre": _pt("engine/reports/_part_002.py:pdf:490:57"),
    }.get(resultat.get("type_document", doc.type_document), _pt("engine/reports/_part_002.py:pdf:491:60"))

    # ── Contexte enregistrement (utilisé par le résolveur d'images générique
    # ci-dessous, et par le rendu par blocs le cas échéant) ──
    # NB : le champ réel sur DocumentGenere est `table_cible` (pas `table`) —
    # `getattr(doc, "table", None)` retournait toujours None ici, ce qui
    # rendait le filtre "Table concernée" du cachet inopérant pour ce
    # contexte (le cachet ne s'affichait alors jamais dès qu'une table était
    # sélectionnée). Corrigé pour lire le bon champ.
    _table_doc = getattr(doc, "table_cible", None)
    _type_doc = getattr(doc, "type_document", None)
    enregistrement_ctx = {}
    if doc.enregistrement_cible:
        enregistrement_ctx = doc.enregistrement_cible.to_dict()
        enregistrement_ctx["id"] = doc.enregistrement_cible.id
    elif doc.parametres:
        enregistrement_ctx = dict(doc.parametres)
    ctx_image = {
        "cfg": cfg, "entreprise": doc.entreprise, "table": _table_doc,
        "enregistrement": enregistrement_ctx, "document_id": doc.id,
    }

    # ── Branding : nom affiché, logo, photo de l'entité ──
    # Le logo respecte ConfigurationIA.afficher_logo_document — même toggle
    # que celui appliqué sur tous les autres PDF de la plateforme (voir
    # _bandeau_entreprise) pour un comportement cohérent partout.
    # Logo et photo passent tous les deux par le même résolveur générique
    # que le mode blocs (_resoudre_chemin_image_bloc) : une seule logique
    # de résolution d'image, quel que soit l'endroit où elle est utilisée.
    nom_entreprise = cfg.nom_affichage_document or doc.entreprise.nom
    chemin_logo = None
    if cfg.afficher_logo_document:
        chemin_logo = _resoudre_chemin_image_bloc({"source": "logo"}, ctx_image)

    # Photo de l'entité : image FIXE propre à la carte de document (contexte
    # 'ia'), même mécanisme que la photo de table mais sur sa propre
    # configuration — jamais de lecture dynamique sur l'enregistrement.
    chemin_photo_entite = None
    if cfg.afficher_photo_entite and cfg.image_entite:
        try:
            if os.path.isfile(cfg.image_entite.path):
                chemin_photo_entite = cfg.image_entite.path
        except Exception:
            chemin_photo_entite = None

    photo_entete = chemin_photo_entite if cfg.position_photo_entite == "entete" else None
    photo_destinataire = chemin_photo_entite if cfg.position_photo_entite == "partie_destinataire" else None

    # ── État du cachet : lu depuis la colonne booléenne configurée sur
    # l'enregistrement lié, si disponible (statut réel, ex. Payé/Non payé) ;
    # sinon repli sur l'état "actif" fixe pour un usage en tampon générique
    # (texte/image modifiables, ex. CONFIDENTIEL) — même logique que
    # _bloc_cachet_simple utilisée par les autres exports. ──
    cachet_actif = None
    if cfg.afficher_cachet_document:
        cachet_actif = True
        if cfg.champ_statut_cachet and cfg.champ_statut_cachet in enregistrement_ctx:
            val = enregistrement_ctx.get(cfg.champ_statut_cachet)
            cachet_actif = val if isinstance(val, bool) else str(val).strip().lower() in ("1", "true", "oui", "yes", "vrai")
    ctx_image["cachet_actif"] = cachet_actif

    # Émetteur = TOUJOURS les vraies coordonnées de l'entreprise (page
    # employés), jamais celles que le script IA aurait pu inventer — même
    # sans logo/coordonnées configurés, le nom de l'entreprise reste garanti.
    emetteur_reel = _emetteur_reel(doc.entreprise, nom_entreprise)

    # ── Composition libre par blocs (optionnelle) ──
    # Si le script a fourni une clé "blocs" non vide, elle prend la place du
    # rendu classique 10-clés (lignes/totaux/notes/pied_page rendus par
    # l'entreprise sous forme de blocs si besoin). Sinon, comportement
    # historique inchangé (fallback de sécurité — voir DEFAULT_REGLE_DOCUMENT).
    blocs = resultat.get("blocs") or []
    if blocs:
        _bloc_entete_doc(resultat, styles, elements, type_libelle, cfg,
                          nom_entreprise, chemin_logo, photo_entete)
        if cfg.position_cachet_document == "entete":
            _bloc_cachet_doc(cfg, cachet_actif, styles, elements, _table_doc, _type_doc, doc.id)

        _rendre_blocs(blocs, styles_base, styles, elements, ctx_image)

        # Cachet/signatures : comme pour le rendu classique, s'ils sont
        # activés côté entreprise mais qu'aucun bloc "cachet"/"signature"
        # n'a été inclus par le script, on les ajoute quand même en fin de
        # document plutôt que de les faire disparaître silencieusement.
        types_rendus = {b.get("type") for b in blocs if isinstance(b, dict)}
        if cfg.afficher_cachet_document and "cachet" not in types_rendus \
                and cfg.position_cachet_document != "entete":
            _bloc_cachet_doc(cfg, cachet_actif, styles, elements, _table_doc, _type_doc, doc.id)
        if cfg.afficher_signatures_document and "signature" not in types_rendus:
            _bloc_signatures_doc(cfg, styles, elements, _table_doc, doc.id)

        _fond_page = _dessiner_fond_page(cfg)
        doc_rl.build(elements, onFirstPage=_fond_page, onLaterPages=_fond_page)
        tampon.seek(0)
        return _sauvegarder_pdf_document(doc, tampon)

    # ── Rendu classique (10 clés), inchangé — chemin emprunté quand
    # "blocs" est absent ou vide (voir aiguillage ci-dessus). ──
    def _entete():
        _bloc_entete_doc(resultat, styles, elements, type_libelle, cfg,
                          nom_entreprise, chemin_logo, photo_entete)
        if cfg.position_cachet_document == "entete":
            _bloc_cachet_doc(cfg, cachet_actif, styles, elements, _table_doc, _type_doc, doc.id)

    def _totaux():
        _bloc_totaux(resultat.get("totaux"), styles, elements, cfg)
        if cfg.position_cachet_document == "totaux":
            _bloc_cachet_doc(cfg, cachet_actif, styles, elements, _table_doc, _type_doc, doc.id)

    def _notes():
        _bloc_notes_doc(resultat.get("notes") or "", styles, elements)
        if cfg.position_signatures_document == "notes":
            _bloc_signatures_doc(cfg, styles, elements, _table_doc, doc.id)

    def _pied_page():
        if cfg.position_cachet_document == "pied_page":
            _bloc_cachet_doc(cfg, cachet_actif, styles, elements, _table_doc, _type_doc, doc.id)
        if cfg.position_signatures_document == "pied_page":
            _bloc_signatures_doc(cfg, styles, elements, _table_doc, doc.id)
        _bloc_pied_doc(resultat.get("pied_page") or "", styles, elements)

    constructeurs = {
        "entete": _entete,
        "parties": lambda: _bloc_parties(
            emetteur_reel, resultat.get("destinataire"), styles, elements,
            photo_destinataire, cfg,
        ),
        "lignes": lambda: _bloc_lignes(resultat.get("lignes") or [], styles, elements, cfg),
        "totaux": _totaux,
        "notes": _notes,
        "pied_page": _pied_page,
    }
    # Images additives (clé optionnelle "images", indépendante de "blocs") :
    # chaque image cible une section via "position" (défaut "notes") et est
    # rendue juste après le rendu classique de cette section — permet
    # d'attacher une photo/un visuel même dans un document facture/devis
    # classique, sans passer par la composition libre complète.
    images_extra = resultat.get("images") or []

    # L'en-tête (logo + nom + coordonnées entreprise) est OBLIGATOIRE et
    # rendue en premier, que l'entreprise l'ait retirée ou non de sa
    # configuration de sections — aucune exception possible.
    constructeurs["entete"]()
    _rendre_images_position(images_extra, "entete", elements, ctx_image)
    sections_rendues = set()
    for section in cfg.sections_document_actives():
        if section == "entete":
            continue
        constructeur = constructeurs.get(section)
        if constructeur:
            constructeur()
            _rendre_images_position(images_extra, section, elements, ctx_image)
            sections_rendues.add(section)

    # Si les positions choisies pour le cachet/les signatures pointent
    # vers une section désactivée par l'entreprise (ex. "totaux" retiré
    # des sections actives), on les rend quand même en fin de document
    # plutôt que de les faire disparaître silencieusement. Même logique
    # pour les images additives ciblant une section non rendue.
    if cfg.afficher_cachet_document and cfg.position_cachet_document not in sections_rendues \
            and cfg.position_cachet_document != "entete":
        _bloc_cachet_doc(cfg, cachet_actif, styles, elements, _table_doc, _type_doc, doc.id)
    if cfg.afficher_signatures_document and cfg.position_signatures_document not in sections_rendues:
        _bloc_signatures_doc(cfg, styles, elements, _table_doc, doc.id)
    for position_manquante in {(b.get("position") or "notes") for b in images_extra if isinstance(b, dict)} - sections_rendues - {"entete"}:
        _rendre_images_position(images_extra, position_manquante, elements, ctx_image)

    _fond_page = _dessiner_fond_page(cfg)
    doc_rl.build(elements, onFirstPage=_fond_page, onLaterPages=_fond_page)
    tampon.seek(0)
    return _sauvegarder_pdf_document(doc, tampon)


# ─────────────────────────────────────────────────────────────
# REÇU PDF — CONFIRMATION DE SOUMISSION CLIENT
# ─────────────────────────────────────────────────────────────

def generer_recu_pdf_ligne(enregistrement, table) -> bytes:
    """PDF d'UNE ligne (reçu), imprimé en A6 portrait — « petit, mais pas trop » : juste de quoi contenir l'essentiel. Même PDF côté client
    (portail) et côté utilisateur ; couleurs, police, logo, cachet, signatures et pied de page viennent de la configuration de l'entreprise,
    comme tous les autres documents. Une ligne très longue continue sur une 2e feuille A6 (jamais de texte rogné).
    Retourne les bytes PDF bruts (pas de modèle Django — juste fichier en mémoire)."""
    _police_activer(getattr(table, "entreprise", None))
    from datetime import datetime
    from xml.sax.saxutils import escape
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle

    taille, marges = _format_page("recu")
    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=taille, **marges)
    styles = _feuille_styles()
    entreprise_recu = getattr(table, "entreprise", None)
    couleurs = _couleurs_rapport(entreprise_recu)
    accent, fond_header, fond_paire, texte = couleurs["accent"], couleurs["entete"], couleurs["ligne_paire"], couleurs["texte"]
    gris = colors.HexColor("#555555")
    elements = []
    _bandeau_entreprise(entreprise_recu, doc.width, elements, enregistrement=enregistrement, table=table, compact=True)

    titre = ParagraphStyle("recu_titre", parent=styles["Normal"], fontName=_pol("gras"), fontSize=11, leading=13, textColor=accent, spaceAfter=1)
    sous = ParagraphStyle("recu_sous", parent=styles["Normal"], fontSize=7, leading=9, textColor=gris)
    ok = ParagraphStyle("recu_ok", parent=styles["Normal"], fontName=_pol("gras"), fontSize=8, leading=10, textColor=colors.HexColor("#1f8a4c"), alignment=TA_CENTER)
    cell = ParagraphStyle("recu_cell", parent=styles["Normal"], fontSize=7, leading=8.6, textColor=texte)
    cell_g = ParagraphStyle("recu_cell_g", parent=cell, fontName=_pol("gras"))
    tete = ParagraphStyle("recu_tete", parent=cell, fontName=_pol("gras"), textColor=colors.white)
    pied = ParagraphStyle("recu_pied", parent=styles["Normal"], fontSize=6, leading=7.5, textColor=gris, alignment=TA_CENTER)

    maintenant = getattr(enregistrement, "cree_le", None) or datetime.now()
    date_str = maintenant.strftime("%d/%m/%Y %H:%M")
    elements.append(Paragraph(t("engine/reports/_part_002.py:g:683:31"), titre))
    elements.append(Paragraph(escape(str(table.nom)), sous))
    elements.append(Spacer(1, 0.15 * cm))
    elements.append(Paragraph(t("engine/reports/_part_002.py:g:686:31"), ok))
    elements.append(Spacer(1, 0.15 * cm))

    largeur = doc.width
    ref = Table([[Paragraph(t("engine/reports/_part_002.py:g:690:29"), cell_g), Paragraph(f"#{enregistrement.id}", cell)],
                 [Paragraph(t("engine/reports/_part_002.py:g:691:29"), cell_g), Paragraph(date_str, cell)]], colWidths=[2.2 * cm, largeur - 2.2 * cm])
    ref.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), fond_paire), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
                             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 4)]))
    elements.append(ref)
    elements.append(Spacer(1, 0.2 * cm))

    ligne_dict = enregistrement.to_dict()
    lignes = [[Paragraph(_pt("engine/reports/_part_002.py:pdf:715:25"), tete), Paragraph(t("engine/reports/_part_002.py:g:698:52"), tete)]]
    for champ in table.champs.order_by("ordre"):
        val = ligne_dict.get(champ.slug, "")
        if val is None:
            val = ""
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        if champ.type_champ in ("image", "fichier", "son", "video") and val:
            chemin = _chemin_local_media(str(val))
            try:
                valeur = RLImage(chemin or str(val), width=1.8 * cm, height=1.8 * cm, kind="proportional")
            except Exception:
                valeur = Paragraph(t("engine/reports/_part_002.py:g:710:36"), cell)
        else:
            valeur = Paragraph(escape(str(val)).replace("\n", "<br/>"), cell)
        lignes.append([Paragraph(escape(str(champ.nom)), cell_g), valeur])
    tab = Table(lignes, colWidths=[largeur * 0.38, largeur * 0.62], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), fond_header), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    style += [("BACKGROUND", (0, i), (-1, i), fond_paire) for i in range(2, len(lignes), 2)]
    tab.setStyle(TableStyle(style))
    elements.append(tab)
    elements.append(Spacer(1, 0.2 * cm))
    elements.append(Paragraph(tf("engine/reports/_part_002.py:g:721:31", v1=date_str, v2=enregistrement.id), pied))
    _bloc_cachet_simple(entreprise_recu, elements, enregistrement, table=table, compact=True)
    _bloc_signatures_pied(entreprise_recu, elements, table=table, largeur_max=largeur)
    _construire(doc, elements, entreprise_recu)
    tampon.seek(0)
    return tampon.read()


# ─────────────────────────────────────────────────────────────
# EXPORT PDF D'UNE TABLE : liste brute des lignes, telle qu'affichée
# dans l'onglet "Données". Pas d'IA ici — juste une mise en page
# tabulaire directe des enregistrements, en respectant les colonnes
# et leur ordre.
# ─────────────────────────────────────────────────────────────
def _rendre_valeur_calcul(valeur):
    """Convertit le resultat_json d'une LigneCalcul (scalaire, liste ou
    dict) en chaîne lisible pour une cellule de tableau PDF."""
    if valeur is None:
        return "—"
    if isinstance(valeur, float):
        return _dec(f"{valeur:,.2f}".replace(",", " "))
    if isinstance(valeur, dict):
        return ", ".join(f"{k} : {_rendre_valeur_calcul(v)}" for k, v in valeur.items())
    if isinstance(valeur, list):
        if len(valeur) > 5:
            return tf("engine/reports/_part_002.py:g:746:20", v1=len(valeur))
        return ", ".join(_rendre_valeur_calcul(v) for v in valeur)
    return str(valeur)


def _bloc_tables_calcul(tables_calcul, styles, elements, couleurs=None):
    """Ajoute, pour chaque TableCalcul fournie, un petit tableau (nom /
    résultat) à la suite de la liste principale. Une ligne en erreur affiche
    le message d'erreur au lieu du résultat, sans faire planter l'export.
    `couleurs` (optionnel) : dict issu de `_couleurs_rapport(entreprise)`."""
    if not tables_calcul:
        return

    couleurs = couleurs or {}
    fond_entete = couleurs.get("entete", colors.HexColor("#1f2937"))
    couleur_titre = couleurs.get("accent", colors.HexColor("#1f2937"))

    titre_style = ParagraphStyle("TitreCalcul", parent=styles["Heading2"], fontSize=12,
        textColor=couleur_titre, spaceBefore=10)
    cell_style = ParagraphStyle("CellCalcul", parent=styles["Normal"], fontSize=8, leading=10)
    head_style = ParagraphStyle("HeadCalcul", parent=styles["Normal"], fontSize=8,
        leading=10, textColor=colors.white, fontName=_pol("gras"))

    for table_calcul in tables_calcul:
        lignes_calcul = list(table_calcul.lignes.all())
        if not lignes_calcul:
            continue

        elements.append(Spacer(1, 0.3 * cm))
        elements.append(Paragraph(f"🧮 {table_calcul.nom}", titre_style))
        if table_calcul.description:
            elements.append(Paragraph(table_calcul.description, styles["Normal"]))
        elements.append(Spacer(1, 0.2 * cm))

        # Couleur de la table de calcul (TableCalcul.couleur) : en-tête en couleur pleine + cadre ; couleur d'un calcul (LigneCalcul.couleur) :
        # sa ligne en teinte claire avec un filet de couleur à gauche. Sans couleur, l'apparence habituelle.
        from ..couleur_colonne import couleur_texte, teinte_claire
        c_table = getattr(table_calcul, "couleur", "") or ""
        head_t = ParagraphStyle("HeadCalculCouleur", parent=head_style, textColor=colors.HexColor(couleur_texte(c_table))) if c_table else head_style
        data = [[Paragraph(_pt("engine/reports/_part_002.py:pdf:802:27"), head_t), Paragraph(t("engine/reports/_part_002.py:g:785:57"), head_t)]]
        for l in lignes_calcul:
            valeur = _rendre_valeur_calcul(l.resultat_json) if not l.erreur else tf("engine/reports/_part_002.py:g:787:82", v1=l.erreur[:80])
            data.append([Paragraph(l.nom, cell_style), Paragraph(str(valeur), cell_style)])

        tbl = Table(data, colWidths=[8 * cm, 8 * cm], repeatRows=1)
        style_tbl = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_flat(c_table)) if c_table else fond_entete),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]
        if c_table:
            style_tbl.append(("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor(_flat(c_table))))
        for i, l in enumerate(lignes_calcul, start=1):
            c_ligne = getattr(l, "couleur", "") or ""
            if c_ligne:
                style_tbl += [("BACKGROUND", (0, i), (-1, i), colors.HexColor(teinte_claire(c_ligne))),
                              ("LINEBEFORE", (0, i), (0, i), 3, colors.HexColor(_flat(c_ligne)))]
        tbl.setStyle(TableStyle(style_tbl))
        elements.append(tbl)




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
