from engine.i18n import t as _pt, tl as _ptl, tf as _ptf
from engine.i18n import t as _i18n_t, tf
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa
from ._part_002 import *  # noqa
from engine.i18n import t as _gt  # noqa: E402


def generer_pdf_liste_table(table, champs: list, lignes: list, tables_calcul: list | None = None, fusions: list | None = None, mises_en_forme: list | None = None, total_lignes: int | None = None, fusions_vue: list | None = None) -> bytes:
    """Construit un PDF listant les lignes d'une table dynamique telles
    quelles (pas d'analyse IA). `champs` = liste de ChampDynamique (ordre
    d'affichage), `lignes` = liste de dicts {slug: valeur}, du plus récent au plus ancien.
    `tables_calcul` (optionnel) = TableCalcul à ajouter après la liste principale.
    `fusions_vue` (optionnel) = rectangles de fusion de la vue exportée (export_structure.fusions_vue) : les cellules fusionnées sont
    reproduites (SPAN) comme à l'écran pour N'IMPORTE QUELLE vue (recherche, filtre, tri, lignes limitées).
    `fusions` (ancien mode) = FusionCelluleTable, à ne fournir que si `lignes` est la table ENTIÈRE du plus récent au plus ancien.
    `mises_en_forme` (optionnel) = une couleur de ligne / de cellules par élément de `lignes`, dans le même ordre
    (Enregistrement.couleurs_mise_en_forme) : la coloration de la grille « Données » est reprise dans le PDF.
    Même rendu que la première section du « Résumé général » : un seul code (_elements_liste_table),
    plus de copie qui oublie la structure. Retourne les octets du PDF."""
    from ._part_004 import _elements_liste_table
    _police_activer(getattr(table, "entreprise", None))
    tampon = io.BytesIO()
    # Format paysage si beaucoup de colonnes, portrait sinon, pour limiter les colonnes tronquées.
    from reportlab.lib.pagesizes import landscape
    pagesize = landscape(A4) if len(champs) > 6 else A4

    doc = SimpleDocTemplate(
        tampon, pagesize=pagesize,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    entreprise_table = getattr(table, "entreprise", None)
    couleurs = _couleurs_rapport(entreprise_table)
    elements = _elements_liste_table(table, champs, lignes, pagesize, couleurs, tables_calcul=tables_calcul, fusions=fusions, mises_en_forme=mises_en_forme, total_lignes=total_lignes, fusions_vue=fusions_vue)
    _construire(doc, elements, entreprise_table)
    tampon.seek(0)
    return tampon.read()


# ─────────────────────────────────────────────────────────────
# EXPORT PDF DES MEMBRES DE L'ÉQUIPE (page "employes")
# Même principe que generer_pdf_liste_table : rendu tabulaire brut,
# une ligne par membre, colonnes fixes (pas de champs dynamiques ici).
# ─────────────────────────────────────────────────────────────
def generer_pdf_liste_employes(entreprise, membres) -> bytes:
    """Construit un PDF listant les membres de l'équipe d'une entreprise.
    `membres` = queryset/liste de MembreEntreprise (avec .utilisateur
    pré-chargé via select_related). Retourne les octets du PDF."""
    _police_activer(entreprise)
    tampon = io.BytesIO()
    pagesize = A4

    doc = SimpleDocTemplate(
        tampon, pagesize=pagesize,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    styles = _feuille_styles()
    elements = []

    couleurs = _couleurs_rapport(entreprise)
    bleu = couleurs["entete"]
    fond_ligne_paire = couleurs["ligne_paire"]

    titre_style = ParagraphStyle("TitreListeEmployes", parent=styles["Title"], fontSize=16,
        textColor=couleurs["accent"])
    sous_titre_style = ParagraphStyle("SousTitreListeEmployes", parent=styles["Normal"],
        fontSize=9, textColor=colors.HexColor("#6b7280"))

    membres = list(membres)
    nom_entreprise = getattr(entreprise, "nom", None) or str(entreprise)

    _bandeau_entreprise(entreprise, doc.width, elements)

    elements.append(Paragraph(tf("engine/reports/_part_003.py:g:75:31", v1=nom_entreprise), titre_style))
    elements.append(Paragraph(
        tf("engine/reports/_part_003.py:g:77:9", v1=len(membres), v2=__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')),
        sous_titre_style,
    ))
    elements.append(Spacer(1, 0.5 * cm))

    if not membres:
        elements.append(Paragraph(_i18n_t("engine/reports/_part_003.py:g:84:35"), styles["Normal"]))
    else:
        entetes = [_i18n_t("engine/reports/_part_003.py:g:86:20"), _i18n_t("engine/reports/_part_003.py:g:86:30"), _pt("engine/reports/_part_003.py:pdf:86:115"), _i18n_t("engine/reports/_part_003.py:g:86:52"), _i18n_t("engine/reports/_part_003.py:g:86:65"), _i18n_t("engine/reports/_part_003.py:g:86:73")]
        largeur_dispo = (pagesize[0] - 3 * cm)
        largeurs_relatives = [1.3, 1.3, 1.5, 1.3, 1.1, 0.7]
        total_relatif = sum(largeurs_relatives)
        largeurs_col = [largeur_dispo * (l / total_relatif) for l in largeurs_relatives]

        cell_style = ParagraphStyle("CellEmployes", parent=styles["Normal"], fontSize=8, leading=10)
        head_style = ParagraphStyle("HeadEmployes", parent=styles["Normal"], fontSize=8,
            leading=10, textColor=colors.white, fontName=_pol("gras"))

        data_wrapped = [[Paragraph(v, head_style) for v in entetes]]
        for m in membres:
            u = m.utilisateur
            identifiant = f"@client_{m.numero_client or u.id}" if m.role == "lecteur" else f"@{u.username}"
            telephone = f"{m.indicatif_pays or ''} {m.telephone}".strip() if m.telephone else "—"
            ligne = [
                u.first_name or "",
                u.last_name or "",
                identifiant,
                telephone,
                m.get_role_display(),
                _i18n_t("engine/reports/_part_003.py:g:107:17") if u.is_active else _i18n_t("engine/reports/_part_003.py:g:107:43"),
            ]
            data_wrapped.append([Paragraph(str(v), cell_style) for v in ligne])

        tbl = Table(data_wrapped, colWidths=largeurs_col, repeatRows=1)
        style_tbl = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), bleu),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ])
        for i in range(1, len(data_wrapped)):
            if i % 2 == 0:
                style_tbl.add("BACKGROUND", (0, i), (-1, i), fond_ligne_paire)
        tbl.setStyle(style_tbl)
        elements.append(tbl)

    _bloc_cachet_simple(entreprise, elements)
    _bloc_signatures_pied(entreprise, elements)

    _construire(doc, elements, entreprise)
    tampon.seek(0)
    return tampon.read()


# ─────────────────────────────────────────────────────────────
# CARTES D'IDENTITÉ AGENTS (recto/verso, A4 paysage, 10/feuille)
# Chaque face est composée comme UNE SEULE IMAGE avec OpenCV (cv2) : le
# theme fourni par l'admin sert de fond, et tous les elements (photo, logo,
# colonnes de texte, QR code, signature, cachet) sont directement superposes
# dessus par collage/alpha-blending cv2 — PAS de dessin vectoriel empile sur
# le canevas ReportLab. Le PDF ne fait plus que deposer cette image finale
# telle quelle (un seul drawImage par carte). Le texte est lui aussi dessine
# directement sur l'image avec cv2.putText (police vectorielle Hershey
# integree a OpenCV) — les caracteres accentues sont translitteres au
# prealable (cv2 ne les gere pas), voir _sans_accents().
# Grille 2 colonnes x 5 lignes ; le verso est mis en miroir horizontal pour
# rester aligne avec le recto en impression recto-verso (retournement bord
# long).
# ─────────────────────────────────────────────────────────────
def generer_pdf_cartes_agents(table, champs_par_slug, enregistrements, config, images=False):
    """Genere le PDF A4 (portrait) des cartes d'agents, format ID-1 exact
    (85,60 x 53,98 mm — carte bancaire / Visa / carte d'identite), 2 colonnes
    x 5 rangees = EXACTEMENT 10 cartes par page, separees d'un espace fixe
    pour permettre la decoupe.

    `enregistrements` doit deja avoir ete limite a 10 maximum par l'appelant
    (voir views.api_generer_cartes) : le PDF ne contient jamais plus de
    2 pages au total (1 page rectos + 1 page versos), les versos etant
    positionnes aux memes coordonnees que les rectos (miroir horizontal
    par rangee) pour une impression recto-verso alignee sur une seule
    feuille A4.

    La DISPOSITION des elements sur la carte est FIXE et calculee
    automatiquement (aucune position libre a configurer) : photo en haut a
    gauche, logo entreprise en haut a droite, colonnes selectionnees
    empilees en lignes "Nom : valeur" separees d'un filet, et une bande
    basse reservee au QR code / signature / cachet (repartis a parts egales
    selon ceux actives pour la face concernee).

    Chaque face (recto, verso) est composee en UNE IMAGE bitmap (cv2,
    300 dpi) avant d'etre deposee dans le PDF : le theme reste l'image de
    fond fournie par l'admin, tous les autres elements sont superposes
    directement dessus.

    `config` (JSON de TableDynamique.config_carte) :
      {
        "colonnes": [slug, ...]            # ordre = priorite d'affichage
        "logo_recto"/"logo_verso": bool,
        "qrcode_recto"/"qrcode_verso": bool,
        "signature_recto"/"signature_verso": bool,
        "cachet_recto"/"cachet_verso": bool,
        "faces": "recto_verso" (defaut) | "recto" — avec "recto", le PDF ne contient que la page des rectos (1 page au lieu de 2),
        "sortie": "pdf" (defaut) | "images" — « images » : une image PNG par carte et par face (voir generer_cartes_images),
        "couleurs": {"texte": "#hex", "bordure": "#hex", "separateur": "#hex"},
        "taille_police": nombre (pt, 5-14, defaut 8) — taille du texte des
          colonnes affichees sur la carte ; la hauteur de ligne s'adapte
          automatiquement pour ne jamais faire chevaucher le texte.
      }"""
    _police_activer(getattr(table, "entreprise", None))
    import os

    import cv2
    import hashlib
    import numpy as np
    from .. import image_universelle
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from django.conf import settings
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    config = config or {}
    # Cohérence avec les autres documents : sans couleur choisie pour la carte, elle reprend celles de l'entreprise
    # (texte, accent pour la bordure, lignes alternées pour le séparateur) ; une couleur propre à la carte l'emporte toujours.
    _pal = _couleurs_rapport(getattr(table, "entreprise", None))
    _hex = lambda c: "#" + c.hexval()[2:].lower()
    couleurs = {"texte": _hex(_pal["texte"]), "bordure": _hex(_pal["accent"]), "separateur": _hex(_pal["ligne_paire"]),
                **{k: v for k, v in (config.get("couleurs") or {}).items() if v}}

    def _hex_vers_rgb(valeur, defaut):
        h = (valeur or defaut).lstrip("#")
        if len(h) != 6:
            h = defaut.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    rgb_texte = _hex_vers_rgb(couleurs.get("texte"), "#1a1a1a")
    bgr_bordure = _hex_vers_rgb(couleurs.get("bordure"), "#999999")[::-1]
    bgr_separateur = _hex_vers_rgb(couleurs.get("separateur"), "#dddddd")[::-1]

    # ── Dimensions ID-1 (ISO/IEC 7810) : cm pour le placement dans la page
    # PDF, pixels (300 dpi) pour la composition cv2 de chaque face. ──
    DPI = 300

    def _px(valeur_cm):
        return max(1, round(valeur_cm / 2.54 * DPI))

    def _pt_px(valeur_pt):
        return max(1, round(valeur_pt / 72 * DPI))

    W_CM, H_CM = 8.560, 5.398
    W_PX, H_PX = _px(W_CM), _px(H_CM)
    W, H = W_CM * cm, H_CM * cm
    cols, ranges = 2, 5
    ESPACE = 0.35 * cm  # espace entre cartes, pour la decoupe

    largeur_page, hauteur_page = A4  # portrait : 21,0 x 29,7 cm
    largeur_grille = cols * W + (cols - 1) * ESPACE
    hauteur_grille = ranges * H + (ranges - 1) * ESPACE
    marge_x = (largeur_page - largeur_grille) / 2
    marge_y = (hauteur_page - hauteur_grille) / 2

    # ── Constantes de la mise en page fixe (converties en pixels 300dpi) ──
    PAD = _px(0.25)
    PHOTO_L, PHOTO_H = _px(2.0), _px(2.4)
    LOGO_L = _px(1.1)
    BANDE_BASSE_H = _px(1.5)
    # Taille de police des colonnes (config_carte["taille_police"], en pt) —
    # configurable depuis l'interface, 8pt par defaut. Bornee 5-14pt pour
    # rester lisible sans deborder une carte ID-1. La legende sous la
    # signature reste 2pt plus petite (min 5pt), et la hauteur de ligne
    # s'adapte a la taille choisie pour ne jamais faire chevaucher le texte.
    TAILLE_POLICE_PT = config.get("taille_police") or 8
    try:
        TAILLE_POLICE_PT = float(TAILLE_POLICE_PT)
    except (TypeError, ValueError):
        TAILLE_POLICE_PT = 8
    TAILLE_POLICE_PT = max(5, min(14, TAILLE_POLICE_PT))
    TAILLE_POLICE_PETITE_PT = max(5, TAILLE_POLICE_PT - 2)
    TAILLE_POLICE = _pt_px(TAILLE_POLICE_PT)
    TAILLE_POLICE_PETITE = _pt_px(TAILLE_POLICE_PETITE_PT)
    HAUTEUR_LIGNE = _pt_px(TAILLE_POLICE_PT * 1.45)

    # ── Texte rendu directement avec cv2.putText (police vectorielle
    # integree a OpenCV, Hershey) au lieu de PIL/ImageFont : plus aucune
    # dependance a un fichier de police present ou non sur le serveur, donc
    # plus aucun risque de retomber sur une police de secours de taille
    # fixe. La taille configuree (taille_police) est traduite en une
    # "echelle" cv2 par etalonnage (cv2.getTextSize) pour que le rendu
    # corresponde bien a la hauteur en pixels voulue a 300dpi.
    # ATTENTION : les polices Hershey d'OpenCV ne gerent PAS les caracteres
    # accentues (é, à, ç, ù...) — _sans_accents() les translitere donc en
    # equivalents sans accent avant tout dessin, pour eviter des glyphes
    # manquants/corrompus sur la carte.
    # Bibliotheque de polices Hershey d'OpenCV (10 variantes, dont 2
    # italiques via le flag FONT_ITALIC) — choisie par l'utilisateur cote
    # UI (config_carte["police"], modal de gestion des agents/cartes).
    POLICES_CV = {
        "simplex": cv2.FONT_HERSHEY_SIMPLEX, "plain": cv2.FONT_HERSHEY_PLAIN,
        "duplex": cv2.FONT_HERSHEY_DUPLEX, "complex": cv2.FONT_HERSHEY_COMPLEX,
        "triplex": cv2.FONT_HERSHEY_TRIPLEX, "complex_small": cv2.FONT_HERSHEY_COMPLEX_SMALL,
        "script_simplex": cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, "script_complex": cv2.FONT_HERSHEY_SCRIPT_COMPLEX,
        "simplex_italique": cv2.FONT_HERSHEY_SIMPLEX | cv2.FONT_ITALIC,
        "complex_italique": cv2.FONT_HERSHEY_COMPLEX | cv2.FONT_ITALIC,
    }
    FONT_CV = POLICES_CV.get(config.get("police"), cv2.FONT_HERSHEY_DUPLEX)

    import unicodedata

    def _sans_accents(texte):
        return "".join(
            c for c in unicodedata.normalize("NFKD", str(texte))
            if not unicodedata.combining(c)
        )

    def _etalonner(taille_px_cible):
        epaisseur = max(1, round(taille_px_cible / 26))
        (_, h_ref), _ = cv2.getTextSize("Ag0", FONT_CV, 1.0, epaisseur)
        echelle = taille_px_cible / h_ref if h_ref else 1.0
        return echelle, epaisseur

    ECHELLE_TEXTE, EPAISSEUR_TEXTE = _etalonner(TAILLE_POLICE)
    ECHELLE_PETITE, EPAISSEUR_PETITE = _etalonner(TAILLE_POLICE_PETITE)

    def chemin_media(champ_fichier):
        if not champ_fichier:
            return None
        try:
            chemin = default_storage.path(champ_fichier.name)
        except Exception:
            chemin = os.path.join(settings.MEDIA_ROOT, champ_fichier.name)
        return chemin if os.path.exists(chemin) else None

    def chemin_valeur_image(url):
        if not url:
            return None
        from ..securite import chemin_media_sur
        chemin = chemin_media_sur(url)  # jamais hors de MEDIA_ROOT (« ../ » dans une cellule)
        return str(chemin) if chemin is not None and chemin.exists() else None

    def _ligne_texte(slug, donnees):
        champ = champs_par_slug.get(slug)
        if not champ or champ.type_champ in ("image", "audio", "video"):
            return None
        valeur = donnees.get(slug)
        return f"{champ.nom} : {valeur}" if valeur not in (None, "") else None

    # ── Contenu du QR code : 3 methodes possibles (config_carte.qrcode_mode),
    # en plus du matricule (toujours en 1ere ligne, identifiant obligatoire) :
    #  - "hash"   : empreinte courte (sha256) du contenu de la carte, pour
    #               verifier son authenticite sans exposer les donnees.
    #  - "resume" : colonnes choisies a part (config_carte.qrcode_colonnes),
    #               typiquement les colonnes NON affichees sur la carte.
    #  - "champ"  : la valeur brute d'un seul champ choisi (ex: une URL de
    #               verification), sans rien y ajouter d'autre.
    def resume_qrcode(enr, donnees):
        entete = f"{table.nom} #{enr.id}" + (f" · {enr.matricule}" if enr.matricule else "")
        mode = config.get("qrcode_mode") or "resume"
        from ..qr_secure import chiffrer_identifiant
        if mode == "securise":
            # Aucune donnee lisible encodee : uniquement le matricule (ou a
            # defaut l'id), chiffre avec la cle de l'entreprise. La lecture
            # ne fait que retrouver ensuite la ligne en base — voir
            # api_qrcode_rechercher (views.py) / qr_secure.py.
            identifiant = enr.matricule or str(enr.id)
            return chiffrer_identifiant(table.entreprise, identifiant)
        if mode == "champ":
            # Retrouvable par recherche (valeur du champ deja stockee dans
            # ValeurChamp) : toujours chiffre avant d'etre encode, comme les
            # autres modes securises — seul le mode "resume" reste en clair.
            valeur = donnees.get(config.get("qrcode_champ"))
            identifiant = str(valeur) if valeur not in (None, "") else entete
            return chiffrer_identifiant(table.entreprise, identifiant)
        if mode == "hash":
            base = "|".join(_ligne_texte(s, donnees) or "" for s in slugs_colonnes)
            import hashlib
            empreinte = hashlib.sha256(base.encode('utf-8')).hexdigest()[:16]
            # Persiste l'empreinte sur la ligne (sinon introuvable au moment
            # du scan, puisqu'elle n'existait auparavant qu'en memoire).
            if enr.empreinte_carte != empreinte:
                enr.empreinte_carte = empreinte
                enr.save(update_fields=["empreinte_carte"])
            return chiffrer_identifiant(table.entreprise, empreinte)
        slugs_resume = [s for s in (config.get("qrcode_colonnes") or []) if s in champs_par_slug] \
            or [s for s in champs_par_slug if s not in slugs_colonnes]
        lignes = [entete] + [l for l in (_ligne_texte(s, donnees) for s in slugs_resume) if l]
        texte_brut = "\n".join(lignes)
        # Persiste le texte brut comme empreinte pour retrouver la ligne au
        # scan (le QR lui-meme ne contient que la version chiffree).
        if enr.empreinte_carte != texte_brut:
            enr.empreinte_carte = texte_brut
            enr.save(update_fields=["empreinte_carte"])
        return chiffrer_identifiant(table.entreprise, texte_brut)

    def image_qrcode_bgra(texte):
        """QR code rendu en tableau numpy BGRA, pret a etre colle avec les
        memes helpers que les autres images (photo/logo/signature/cachet)."""
        try:
            import qrcode
        except ImportError:
            return None
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
        qr.add_data(texte)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGBA2BGRA)

    def qrcode_stocke_bgra(enr, texte):
        """QR code genere dynamiquement mais PERSISTE comme une vraie image
        (enr.qrcode_genere), au lieu d'etre recalcule a chaque generation de
        PDF : ne regenere/re-enregistre que si le contenu encode a change
        (enr.qrcode_hash), sinon relit simplement le fichier deja stocke."""
        empreinte = hashlib.sha1(texte.encode("utf-8")).hexdigest()
        chemin_existant = chemin_media(enr.qrcode_genere) if enr.qrcode_genere else None
        if enr.qrcode_hash == empreinte and chemin_existant:
            return _charger_image_bgra(chemin_existant)
        qr_bgra = image_qrcode_bgra(texte)
        if qr_bgra is None:
            return None
        ok, buf = cv2.imencode(".png", qr_bgra)
        if ok:
            enr.qrcode_genere.save(f"qr_{enr.id}.png", ContentFile(buf.tobytes()), save=False)
            enr.qrcode_hash = empreinte
            enr.save(update_fields=["qrcode_genere", "qrcode_hash"])
        return qr_bgra

    def _charger_image_bgra(chemin):
        """Charge une image disque en BGRA (canal alpha force a 255 si
        l'original n'en a pas) pour un collage uniforme avec cv2."""
        if not chemin:
            return None
        img = cv2.imread(chemin, cv2.IMREAD_UNCHANGED)
        if img is None:   # SVG & co : converti en PNG (image_universelle) puis decode
            png = image_universelle.lisible(chemin)
            img = cv2.imdecode(np.frombuffer(png.getvalue(), np.uint8), cv2.IMREAD_UNCHANGED) if hasattr(png, "getvalue") else None
        if img is None:
            return None
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        return img

    def _coller(fond, source_bgra, x, y, largeur_max, hauteur_max, garder_ratio=True, couvrir=False):
        """Superpose (alpha-blending) source_bgra sur fond aux coordonnees
        (x, y) [origine haut-gauche], redimensionnee pour tenir dans
        largeur_max x hauteur_max. Retourne (x, y, w, h) reellement utilises,
        ou None si rien n'a ete colle.

        `couvrir` (comme CSS object-fit: cover) : au lieu de CONTENIR l'image
        dans le cadre (ratio conserve, mais le cadre peut rester
        partiellement vide si le ratio source ne correspond pas), la
        redimensionne pour COUVRIR exactement largeur_max x hauteur_max puis
        RECADRE (crop centre) le surplus. Le rectangle colle occupe alors
        toujours exactement la taille attendue — utilise pour la photo
        d'agent, dont le ratio d'origine (portrait, carre, etc.) ne
        correspond pas forcement au cadre PHOTO_L x PHOTO_H de la carte."""
        if source_bgra is None:
            return None
        h0, w0 = source_bgra.shape[:2]
        largeur_max, hauteur_max = int(largeur_max), int(hauteur_max)
        if couvrir:
            ratio = max(largeur_max / w0, hauteur_max / h0)
            w_complet, h_complet = max(1, round(w0 * ratio)), max(1, round(h0 * ratio))
            redim_complet = cv2.resize(source_bgra, (w_complet, h_complet), interpolation=cv2.INTER_AREA)
            x_off = max(0, (w_complet - largeur_max) // 2)
            y_off = max(0, (h_complet - hauteur_max) // 2)
            redim = redim_complet[y_off:y_off + hauteur_max, x_off:x_off + largeur_max]
            h, w = redim.shape[:2]
        elif garder_ratio:
            ratio = min(largeur_max / w0, hauteur_max / h0)
            w, h = max(1, round(w0 * ratio)), max(1, round(h0 * ratio))
            redim = cv2.resize(source_bgra, (w, h), interpolation=cv2.INTER_AREA)
        else:
            w, h = largeur_max, hauteur_max
            redim = cv2.resize(source_bgra, (w, h), interpolation=cv2.INTER_AREA)
        x2, y2 = min(x + w, fond.shape[1]), min(y + h, fond.shape[0])
        w, h = max(0, x2 - x), max(0, y2 - y)
        if w == 0 or h == 0:
            return None
        zone = fond[y:y2, x:x2]
        source_zone = redim[:h, :w]
        alpha = source_zone[:, :, 3:4].astype(np.float32) / 255.0
        zone[:] = (source_zone[:, :, :3].astype(np.float32) * alpha
                   + zone.astype(np.float32) * (1 - alpha)).astype(np.uint8)
        return (x, y, w, h)

    # ── Champs "colonnes" selectionnes (texte), dans l'ordre choisi ──
    slugs_colonnes = [s for s in (config.get("colonnes") or []) if s in champs_par_slug]
    champ_photo = champs_par_slug.get("photo")
    if champ_photo is not None and champ_photo.type_champ != "image":
        champ_photo = None

    logo_chemin = chemin_media(table.entreprise.logo) if getattr(table.entreprise, "logo", None) else None
    signature_chemin = chemin_media(table.carte_signature_image)
    cachet_chemin = chemin_media(table.carte_cachet_image)
    logo_img = _charger_image_bgra(logo_chemin)
    signature_img = _charger_image_bgra(signature_chemin)
    cachet_img = _charger_image_bgra(cachet_chemin)
    # QR "upload" : image fixe fournie par l'admin (comme logo/signature/
    # cachet), en secours si la generation dynamique (lib qrcode) est
    # capricieuse ou indisponible sur le serveur — meme rendu identique sur
    # toutes les cartes dans ce cas (pas de contenu par agent).
    qrcode_upload_img = (_charger_image_bgra(chemin_media(table.carte_qrcode_image))
                          if config.get("qrcode_source") == "upload" else None)

    def _composer_bande_basse(fond, textes_a_dessiner, elements):
        """Repartit a parts egales, sur la largeur de la carte, les elements
        actifs pour cette face (QR code / signature / cachet). Colle les
        images avec cv2 ; ajoute la legende de signature a la liste de
        textes a dessiner (dessinee ensuite avec cv2.putText)."""
        actifs = [e for e in elements if e[0]]
        if not actifs:
            return
        largeur_dispo = W_PX - 2 * PAD
        largeur_case = largeur_dispo // len(actifs)
        y0_bande = H_PX - PAD - BANDE_BASSE_H
        for i, (_actif, kind, donnee) in enumerate(actifs):
            cx0 = PAD + i * largeur_case
            taille = min(largeur_case - _px(0.1), BANDE_BASSE_H)
            cx = cx0 + (largeur_case - taille) // 2
            if kind == "qrcode":
                if donnee is not None:
                    _coller(fond, donnee, cx, y0_bande, taille, taille, garder_ratio=False)
                    cv2.rectangle(fond, (cx, y0_bande), (cx + taille, y0_bande + taille), bgr_bordure, 1)
            elif donnee is not None:
                _coller(fond, donnee, cx, y0_bande, taille, round(taille * 0.6))
                if kind == "signature" and table.carte_signature_nom:
                    textes_a_dessiner.append({
                        "centre_x": cx0 + largeur_case / 2,
                        "y": y0_bande + round(taille * 0.6) + _px(0.03),
                        "texte": table.carte_signature_nom,
                        "echelle": ECHELLE_PETITE, "epaisseur": EPAISSEUR_PETITE,
                        "couleur": rgb_texte,
                        "centre": True,
                    })

    def _composer_face(theme_chemin, face, enr):
        """Compose l'image complete (numpy BGR, 300 dpi) d'une face de carte
        pour un enregistrement donne : le theme sert de fond, tout le reste
        (photo, logo, colonnes, QR/signature/cachet, texte) est directement
        superpose dessus avec cv2."""
        theme = cv2.imread(theme_chemin) if theme_chemin else None
        if theme is None:
            fond = np.full((H_PX, W_PX, 3), 255, dtype=np.uint8)
        else:
            fond = cv2.resize(theme, (W_PX, H_PX), interpolation=cv2.INTER_AREA)

        donnees = enr.to_dict()
        textes_a_dessiner = []  # dicts : {x/centre_x, y, texte, echelle, epaisseur, couleur}

        # Photo agent : uniquement sur le RECTO (comme le verso d'une carte
        # d'identite reelle, dedie au theme + logo/QR/signature/cachet).
        x_texte = PAD
        if face == "recto" and champ_photo is not None:
            photo_img = _charger_image_bgra(chemin_valeur_image(donnees.get("photo")))
            if photo_img is not None:
                pos = _coller(fond, photo_img, PAD, PAD, PHOTO_L, PHOTO_H, couvrir=True)
                if pos:
                    cv2.rectangle(fond, (pos[0], pos[1]), (pos[0] + PHOTO_L, pos[1] + PHOTO_H), bgr_bordure, 1)
                x_texte = PAD + PHOTO_L + _px(0.15)

        actif_logo = bool(config.get(f"logo_{face}"))
        actif_qr = bool(config.get(f"qrcode_{face}"))
        actif_signature = bool(config.get(f"signature_{face}")) and signature_img is not None
        actif_cachet = bool(config.get(f"cachet_{face}")) and cachet_img is not None
        bande_active = actif_qr or actif_signature or actif_cachet

        # Logo entreprise — coin haut-droit.
        x_texte_fin = W_PX - PAD
        if actif_logo and logo_img is not None:
            lx = W_PX - PAD - LOGO_L
            _coller(fond, logo_img, lx, PAD, LOGO_L, LOGO_L)
            x_texte_fin = lx - _px(0.1)

        # Colonnes selectionnees — empilees "Nom : valeur", filet separateur
        # (recto uniquement). La position du filet est calculee a partir de
        # la hauteur REELLEMENT mesuree du texte (cv2.getTextSize, avec la
        # baseline pour couvrir les descendantes g/y/p/q) et non plus d'un
        # ratio fixe de HAUTEUR_LIGNE — sinon, a certaines tailles de
        # police, le texte pouvait deborder sous le filet et se retrouver
        # dessine par-dessus lui. L'espacement entre lignes s'agrandit aussi
        # au besoin si le texte mesure est plus haut que prevu.
        if face == "recto":
            y_ligne = PAD + _px(0.32)
            y_max = H_PX - PAD - (BANDE_BASSE_H + _px(0.1) if bande_active else 0)
            marge_sep = max(_px(0.03), round(TAILLE_POLICE * 0.12))
            # Matricule : identifiant obligatoire, toujours affiche en 1ere
            # ligne quelles que soient les colonnes choisies (voir
            # Enregistrement.generer_matricule).
            lignes_carte = ([(_pt("engine/reports/_part_003.py:pdf:567:30"), enr.matricule)] if enr.matricule else []) + [
                (champs_par_slug[s].nom, donnees.get(s)) for s in slugs_colonnes
                if champs_par_slug.get(s) and champs_par_slug[s].type_champ not in ("image", "audio", "video")
                and donnees.get(s) not in (None, "")
            ]
            for nom_champ, valeur in lignes_carte:
                texte_ligne = _sans_accents(f"{nom_champ} : {valeur}")
                (_, hauteur_txt), baseline_cv = cv2.getTextSize(
                    texte_ligne, FONT_CV, ECHELLE_TEXTE, EPAISSEUR_TEXTE)
                hauteur_reelle = hauteur_txt + baseline_cv
                y_sep = y_ligne + hauteur_reelle + marge_sep
                if y_sep > y_max:
                    break
                textes_a_dessiner.append({
                    "x": x_texte, "y": y_ligne, "texte": f"{nom_champ} : {valeur}",
                    "echelle": ECHELLE_TEXTE, "epaisseur": EPAISSEUR_TEXTE,
                    "couleur": rgb_texte, "centre": False,
                })
                cv2.line(fond, (x_texte, y_sep), (x_texte_fin, y_sep), bgr_separateur, 1)
                y_ligne += max(HAUTEUR_LIGNE, hauteur_reelle + marge_sep + _px(0.05))

        # Bande basse : QR code / signature / cachet, repartis a parts egales.
        if bande_active:
            if actif_qr and config.get("qrcode_source") == "upload" and qrcode_upload_img is not None:
                qr_bgra = qrcode_upload_img
            elif actif_qr:
                qr_bgra = qrcode_stocke_bgra(enr, resume_qrcode(enr, donnees))
            else:
                qr_bgra = None
            _composer_bande_basse(fond, textes_a_dessiner, [
                (actif_qr, "qrcode", qr_bgra),
                (actif_signature, "signature", signature_img if actif_signature else None),
                (actif_cachet, "cachet", cachet_img if actif_cachet else None),
            ])

        # ── Texte : dessine directement sur l'image avec cv2.putText
        # (police vectorielle Hershey d'OpenCV, redimensionnable via
        # ECHELLE_TEXTE/ECHELLE_PETITE — voir _etalonner ci-dessus). ──
        for t in textes_a_dessiner:
            texte = _sans_accents(t["texte"])
            echelle, epaisseur, couleur_bgr = t["echelle"], t["epaisseur"], t["couleur"][::-1]
            (largeur_txt, hauteur_txt), _ = cv2.getTextSize(texte, FONT_CV, echelle, epaisseur)
            if t["centre"]:
                x = round(t["centre_x"] - largeur_txt / 2)
            else:
                x = t["x"]
            y_base = round(t["y"] + hauteur_txt)
            cv2.putText(fond, texte, (x, y_base), FONT_CV, echelle, couleur_bgr,
                        epaisseur, cv2.LINE_AA)

        return fond

    if images:
        # Sortie « cartes seulement » : chaque face est exportée telle quelle (image cv2 300 dpi), sans mise en page PDF
        # ni limite de 10 cartes par page. Liste [(nom de fichier, octets PNG)] : recto, puis verso sauf si faces == « recto ».
        faces = ("recto",) if config.get("faces") == "recto" else ("recto", "verso")
        fichiers = []
        for enr in enregistrements:
            for face in faces:
                theme = chemin_media(table.theme_carte_recto if face == "recto" else table.theme_carte_verso)
                ok, buf_png = cv2.imencode(".png", _composer_face(theme, face, enr))
                if ok:
                    fichiers.append((f"{table.slug}_{enr.id}_{face}.png", buf_png.tobytes()))
        return fichiers

    tampon = io.BytesIO()
    c = canvas.Canvas(tampon, pagesize=(largeur_page, hauteur_page))

    def dessiner_page(theme_chemin, face, groupe, miroir):
        for i, enr in enumerate(groupe):
            rangee, colonne = divmod(i, cols)
            if miroir:
                colonne = cols - 1 - colonne
            x0 = marge_x + colonne * (W + ESPACE)
            y0 = hauteur_page - marge_y - rangee * (H + ESPACE) - H

            image_bgr = _composer_face(theme_chemin, face, enr)
            ok, buf_png = cv2.imencode(".png", image_bgr)
            if ok:
                c.drawImage(ImageReader(io.BytesIO(buf_png.tobytes())), x0, y0, width=W, height=H)
        c.showPage()

    theme_recto = chemin_media(table.theme_carte_recto)
    theme_verso = chemin_media(table.theme_carte_verso)
    par_page = cols * ranges
    groupe = enregistrements[:par_page]  # deja limite a 10 par l'appelant, garde-fou ici aussi

    dessiner_page(theme_recto, "recto", groupe, miroir=False)
    if config.get("faces") != "recto":   # « recto » seul : une page de rectos, pas de page de versos
        dessiner_page(theme_verso, "verso", groupe, miroir=True)

    c.save()
    return tampon.getvalue()


def generer_cartes_images(table, champs_par_slug, enregistrements, config):
    """Cartes EXPORTÉES INDIVIDUELLEMENT en images PNG (composées avec cv2, comme le PDF) : (octets, type MIME, nom de fichier).
    Une seule image (une carte, recto seul) = ce PNG directement ; sinon un .zip avec un PNG par carte et par face."""
    import io as _io
    import zipfile
    fichiers = generer_pdf_cartes_agents(table, champs_par_slug, enregistrements, config, images=True)
    if len(fichiers) == 1:
        nom, octets = fichiers[0]
        return octets, "image/png", nom
    tampon = _io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_STORED) as zf:   # PNG déjà compressés
        for nom, octets in fichiers:
            zf.writestr(nom, octets)
    return tampon.getvalue(), "application/zip", f"cartes_{table.slug}_images.zip"


def _elements_kpis_table(table_source, largeur, kpis=None, colonnes=4) -> list:
    """Bandeau des indicateurs KPI d'une table (models.IndicateurKPI : valeur actuelle, objectif, état,
    variation). Liste vide si la table n'a aucun KPI. Sert à la section « KPI » du Résumé général (rendue une
    seule fois) et, avec `kpis=[un_kpi]` et `colonnes=1`, à la carte d'un KPI posé sur le canevas d'un tableau
    de bord BI (_elements_dashboard_bi)."""
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from xml.sax.saxutils import escape

    styles = _feuille_styles()
    couleurs = _couleurs_rapport(getattr(table_source, "entreprise", None))
    elements = []
    if table_source is None:
        return elements
    if kpis is None:
        kpis = list(table_source.kpis.select_related("champ_cible").order_by("ordre", "id"))
    if not kpis:
        return elements
    kpi_titre_style = ParagraphStyle("TitreKPI", parent=styles["Normal"], fontSize=9,
                                     textColor=colors.HexColor("#6b7280"), spaceAfter=0)
    kpi_valeur_style = ParagraphStyle("ValeurKPI", parent=styles["Normal"], fontSize=16,
                                      leading=18, textColor=couleurs["accent"], spaceBefore=1)
    kpi_objectif_style = ParagraphStyle("ObjectifKPI", parent=styles["Normal"], fontSize=7.5,
                                        textColor=colors.HexColor("#6b7280"))
    kpi_badge_style = ParagraphStyle("BadgeKPI", parent=styles["Normal"], fontSize=7.5,
                                     textColor=colors.white, alignment=1)
    kpi_erreur_style = ParagraphStyle("ErreurKPI", parent=styles["Normal"], fontSize=7.5,
                                      textColor=colors.HexColor("#dc2626"))
    COULEUR_ETAT = {
        "succes": colors.HexColor("#16a34a"),
        "alerte": colors.HexColor("#e6b800"),
        "danger": colors.HexColor("#dc2626"),
        None: colors.HexColor("#9ca3af"),
    }
    BADGE_TXT = {"succes": _pt("engine/reports/_part_003.py:pdf:714:27"), "alerte": _pt("engine/reports/_part_003.py:pdf:714:61"), "danger": _i18n_t("engine/reports/_part_003.py:g:678:82")}

    def _fmt_kpi(v):
        if v is None:
            return "—"
        return f"{v:,.2f}".rstrip("0").rstrip(".").replace(",", " ")

    COLS_KPI = max(int(colonnes), 1)
    w_kpi = (largeur - 0.4*cm) / COLS_KPI
    kpi_rows, kpi_row = [], []
    for k in kpis:
        lignes_cellule = [Paragraph(escape(k.nom or ""), kpi_titre_style)]

        if k.statut == "echec":
            lignes_cellule.append(Paragraph(f"⚠ {escape(k.erreur or _gt("rg5.msg.078"))}", kpi_erreur_style))
        elif k.valeur_actuelle is None:
            lignes_cellule.append(Paragraph(_i18n_t("engine/reports/_part_003.py:g:694:45"), kpi_objectif_style))
        else:
            valeur_txt = _fmt_kpi(k.valeur_actuelle) + (f" {escape(k.unite)}" if k.unite else "")
            lignes_cellule.append(Paragraph(valeur_txt, kpi_valeur_style))

            # Badge d'état (succès/alerte/danger), même texte/couleur que le front
            if k.etat:
                badge = Table([[Paragraph(BADGE_TXT[k.etat], kpi_badge_style)]],
                              colWidths=[w_kpi - 0.5*cm])
                badge.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), COULEUR_ETAT[k.etat]),
                    ("PADDING", (0, 0), (-1, -1), 3),
                ]))
                lignes_cellule.append(badge)

            # Objectif visé (valeur_cible)
            if k.valeur_cible is not None:
                objectif_txt = _ptf("engine/reports/_part_003.py:pdf:747:31", v1=_fmt_kpi(k.valeur_cible), v2=escape(k.unite or ''))
                lignes_cellule.append(Paragraph(objectif_txt, kpi_objectif_style))

            # Variation (différence) vs le point d'historique précédent
            v = k.variation_pct
            if v is not None:
                fleche = "▲" if v > 0 else ("▼" if v < 0 else "→")
                couleur_var = ("#16a34a" if v > 0 else "#dc2626" if v < 0 else "#9ca3af")
                style_var = ParagraphStyle("VariationKPI", parent=kpi_objectif_style,
                                           textColor=colors.HexColor(couleur_var))
                lignes_cellule.append(Paragraph(
                    tf("engine/reports/_part_003.py:g:722:21", v1=fleche, v2=abs(v)), style_var))

        cellule = Table([[l] for l in lignes_cellule], colWidths=[w_kpi - 0.3*cm])
        cellule.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.8, COULEUR_ETAT.get(k.etat)),
            ("LINEBEFORE", (0, 0), (0, -1), 2.5, COULEUR_ETAT.get(k.etat)),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -2), 2),
            ("TOPPADDING", (0, 1), (-1, -1), 2),
        ]))
        kpi_row.append(cellule)
        if len(kpi_row) == COLS_KPI:
            kpi_rows.append(kpi_row); kpi_row = []
    if kpi_row:
        while len(kpi_row) < COLS_KPI:
            kpi_row.append("")
        kpi_rows.append(kpi_row)
    grille_kpi = Table(kpi_rows, colWidths=[w_kpi]*COLS_KPI)
    grille_kpi.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(grille_kpi)
    elements.append(Spacer(1, 0.5*cm))
    return elements


# ─────────────────────────────────────────────────────────────
# EXPORT PDF DU TABLEAU DE BORD BI
# Reprend le même bandeau/signatures/cachet que generer_pdf_liste_table.
# orientation : "paysage" (défaut), "portrait", "paysage_large" (A3), "paysage_A5"
# ─────────────────────────────────────────────────────────────


# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
