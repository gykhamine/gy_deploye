from engine.i18n import tf
from engine.i18n import t
from ._header import *  # noqa

# --- recablage auto (parties precedentes) ---
from ._part_001 import *  # noqa



def _taille_max_mo(valeur):
    """Taille maximale saisie (Mo) -> entier >= 1, ou None = AUCUNE limite (vide, 0, texte illisible ou négatif)."""
    try:
        n = int(float(str(valeur).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _appliquer_structure_champs(table, entreprise, nouveaux_champs, principale=True):
    """Applique la liste de colonnes `nouveaux_champs` à UNE table (ajout / modification / suppression,
    valeurs des colonnes retirées effacées). Appelée pour la table ouverte ET, si elle appartient à un
    groupe, pour chacune des autres tables du groupe (mêmes slugs : la structure reste identique).
    `principale=False` : le partage de colonne (GroupePartage) n'est pas propagé aux autres parties."""
    slugs_nouveaux = {c.get("slug") or c["nom"].lower().replace(" ", "_") for c in nouveaux_champs}
    # Supprimer les champs retirés (+ leurs valeurs)
    champs_supprimes = table.champs.exclude(slug__in=slugs_nouveaux)
    retirees = dict(table.couleurs_retirees or {})
    retirees.update({c.slug: c.couleur for c in champs_supprimes if c.couleur})   # mémorisées pour une recréation ultérieure
    from ..models import ValeurChamp as VC
    VC.objects.filter(champ__in=champs_supprimes).delete()
    champs_supprimes.delete()

    # Ajouter ou mettre à jour les champs
    for ordre, champ_data in enumerate(nouveaux_champs):
        slug = champ_data.get("slug") or champ_data["nom"].lower().replace(" ", "_")
        # Résoudre la table liée si fournie
        relation_vers = None
        if champ_data.get("relation_vers_id"):
            relation_vers = TableDynamique.objects.filter(
                id=champ_data["relation_vers_id"], entreprise=entreprise
            ).first()
        rel_champ_aff = champ_data.get("relation_champ_affichage", "")
        rel_col_src   = champ_data.get("relation_colonne_source", "")
        rel_bidir     = bool(champ_data.get("relation_bidirectionnelle", False))
        # "Colonne partagée" (GroupePartage) — indépendant de relation_*
        # ci-dessus. `partage_table_cible_id`/`partage_colonne_cible`
        # absents ou vides => on retire le champ de son groupe le cas
        # échéant (bouton "rendre partagé" désactivé côté UI).
        groupe_partage = _resoudre_groupe_partage(entreprise, champ_data) if principale else None
        champ_existant = table.champs.filter(slug=slug).first()
        if champ_existant:
            champ_existant.nom = champ_data.get("nom", champ_existant.nom)
            champ_existant.type_champ = champ_data.get("type", champ_existant.type_champ)
            champ_existant.obligatoire = champ_data.get("obligatoire", champ_existant.obligatoire)
            champ_existant.options_choix = champ_data.get("options", champ_existant.options_choix)
            if champ_existant.type_champ == "constante":
                champ_existant.options_choix = ChampDynamique.normaliser_constante(champ_existant.options_choix) or "0"
            champ_existant.ordre = ordre
            if "taille_max_mo" in champ_data:   # absente = inchangée ; vide / 0 = aucune limite
                champ_existant.taille_max_mo = _taille_max_mo(champ_data["taille_max_mo"])
            if "couleur" in champ_data:   # fenêtre de structure : 🎨 de la ligne ("" = retirée) ; absente = inchangée
                champ_existant.couleur = normaliser_couleur(champ_data["couleur"])
            _appliquer_formule_champ(champ_existant, champ_data)
            if relation_vers is not None or champ_data.get("relation_vers_id") == "":
                champ_existant.relation_vers = relation_vers
            if rel_champ_aff or champ_data.get("relation_vers_id") == "":
                champ_existant.relation_champ_affichage = rel_champ_aff
            if rel_col_src or champ_data.get("relation_vers_id") == "":
                champ_existant.relation_colonne_source = rel_col_src
            champ_existant.relation_bidirectionnelle = rel_bidir
            if principale:
                champ_existant.groupe_partage = groupe_partage
            champ_existant.save()
            if champ_existant.type_champ == "formule":
                from ..calculs import recalculer_champ_formule_table
                recalculer_champ_formule_table(champ_existant)
            elif champ_existant.type_champ == "constante":
                from ..calculs import propager_constante
                propager_constante(champ_existant)
        else:
            champ_nouveau = ChampDynamique.objects.create(
                table=table, nom=champ_data["nom"], slug=slug,
                type_champ=champ_data.get("type", "texte"),
                obligatoire=champ_data.get("obligatoire", True),
                options_choix=(ChampDynamique.normaliser_constante(champ_data.get("options", "")) or "0")
                if champ_data.get("type") == "constante" else champ_data.get("options", ""),
                relation_vers=relation_vers,
                relation_champ_affichage=rel_champ_aff,
                relation_colonne_source=rel_col_src,
                relation_bidirectionnelle=rel_bidir,
                groupe_partage=groupe_partage,
                ordre=ordre,
                taille_max_mo=_taille_max_mo(champ_data.get("taille_max_mo")),
                couleur=normaliser_couleur(champ_data.get("couleur")) or retirees.get(slug, ""),
            )
            retirees.pop(slug, None)
            _appliquer_formule_champ(champ_nouveau, champ_data)
            champ_nouveau.save(update_fields=["formule_expression", "formule_etapes_json"])
            if champ_nouveau.type_champ == "formule":
                from ..calculs import recalculer_champ_formule_table
                recalculer_champ_formule_table(champ_nouveau)
            elif champ_nouveau.type_champ == "constante":
                from ..calculs import propager_constante
                propager_constante(champ_nouveau)
    if retirees != (table.couleurs_retirees or {}):
        table.couleurs_retirees = retirees
        table.save(update_fields=["couleurs_retirees"])


@login_required
@require_http_methods(["POST"])
def api_restructurer_table(request, table_id):
    """
    Restructure la table : permet d'ajouter, supprimer ou modifier des champs
    d'une table existante sans perdre les données (les valeurs des champs
    supprimés sont effacées, les nouveaux champs sont vides sur les lignes existantes).
    Si la table appartient à un groupe, la même structure est appliquée à TOUTES les tables du groupe.
    """
    from ._part_006 import _serialiser_table  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme
    corps = json.loads(request.body or "{}")

    # Table d'un groupe : la structure s'applique à TOUT le groupe (jamais à une seule partie). Un groupe
    # hérité aux tables de structures différentes est refusé avant toute modification : rien ne serait perdu.
    membres = list(table.groupe.tables.exclude(id=table.id)) if table.groupe_id and corps.get("champs") is not None else []
    erreur_groupe = _erreur_structure_groupe([table] + membres)
    if erreur_groupe:
        return JsonResponse({"erreur": erreur_groupe + t("engine/views/_part_002.py:g:121:56")}, status=400)

    plafond_col = table.plafond_colonnes  # refus AVANT toute écriture (sinon le nom/description seraient déjà enregistrés)
    if corps.get("champs") is not None and plafond_col is not None and len(corps["champs"]) > plafond_col:
        return JsonResponse(
            {"erreur": tf("engine/views/_part_002.py:g:126:24", v1=plafond_col),
             "peut_etendre": True, "plafond_colonnes": plafond_col},
            status=400,
        )

    erreur_constante = _erreur_constantes(corps.get("champs")) or _erreur_partage_vers_constante(corps.get("champs")) \
        or _erreur_couleurs(corps.get("champs")) or _erreur_colonnes(corps.get("champs"), entreprise)
    if erreur_constante:
        return JsonResponse({"erreur": erreur_constante}, status=400)

    # Restructuration des champs (avant les métadonnées : un refus ne laisse rien d'écrit)
    nouveaux_champs = corps.get("champs")
    if nouveaux_champs is not None:
        with transaction.atomic():
            for cible in [table] + membres:
                _appliquer_structure_champs(cible, entreprise, nouveaux_champs, principale=cible is table)
            # Deux tables d'un même groupe ne partagent ni colonne ni cellule : sinon tout est annulé
            erreur_groupe = _erreur_partage_entre_tables([t.id for t in [table] + membres]) if membres else None
            if erreur_groupe:
                transaction.set_rollback(True)
        if erreur_groupe:
            return JsonResponse({"erreur": erreur_groupe + t("engine/views/_part_002.py:g:148:60")}, status=400)
        if membres:  # structure du groupe changée : les parties redeviennent des tables natives, comme la table ouverte
            TableDynamique.objects.filter(id__in=[m.id for m in membres]).update(provient_fichier=False)

    # Mise à jour des métadonnées de la table
    if "nom" in corps:
        table.nom = corps["nom"].strip() or table.nom
    if "description" in corps:
        table.description = corps.get("description", "")
    if "taille_echantillon" in corps:
        try:
            table.taille_echantillon = max(1, int(corps["taille_echantillon"]))
        except (TypeError, ValueError):
            pass
    if "prompt_rapport" in corps:
        table.prompt_rapport = corps.get("prompt_rapport", "")
    if "prompt_rapport_document" in corps:
        table.prompt_rapport_document = corps.get("prompt_rapport_document", "")
    # Après restructuration, la table devient éditable comme une table native
    table.provient_fichier = False
    table.save()

    if nouveaux_champs is not None:
        from ..models import Bloc
        Bloc.ajouter_transaction(entreprise.id, {
            "type": "restructuration_table", "table_id": table.id, "table": table.nom,
            "agent_id": request.user.id,
            "nouveaux_champs": [c.get("slug") or c.get("nom") for c in nouveaux_champs],
            "tables_du_groupe_impactees": [m.id for m in membres],
        })

    return JsonResponse({"table": _serialiser_table(table)})


@login_required
@require_http_methods(["POST"])
def api_modifier_table(request, table_id):
    """Reglages de la table qu'on modifie apres coup : taille de l'echantillon,
    prompt permanent, et les deux timeouts (execution du script, appel au
    moteur d'inference)."""
    from ._part_006 import _serialiser_table  # import local (réf. avant sa définition)
    _bloque_lecteur = _interdit_lecteur(request)
    if _bloque_lecteur:
        return _bloque_lecteur
    entreprise = _entreprise_courante(request)
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    from .. import tables_systeme
    _refus_systeme = tables_systeme.refus_ecriture(table)
    if _refus_systeme:
        return _refus_systeme
    corps = json.loads(request.body or "{}")
    if "taille_echantillon" in corps:
        try:
            taille = int(corps["taille_echantillon"])
        except (TypeError, ValueError):
            return JsonResponse({"erreur": t("engine/views/_part_002.py:189:45")}, status=400)
        if taille < 1:
            return JsonResponse({"erreur": t("engine/views/_part_002.py:191:45")}, status=400)
        table.taille_echantillon = taille
        table.save(update_fields=["taille_echantillon"])
    # NB : la limite de lignes n'est plus reglable par table — voir
    # api_limite_lignes_lire/sauvegarder (reglage global, panneau lateral).
    if "prompt_rapport" in corps:
        table.prompt_rapport = corps["prompt_rapport"] or ""
        table.save(update_fields=["prompt_rapport"])
    if "prompt_rapport_document" in corps:
        table.prompt_rapport_document = corps["prompt_rapport_document"] or ""
        table.save(update_fields=["prompt_rapport_document"])
    if "timeout_execution_secondes" in corps:
        try:
            timeout_exec = int(corps["timeout_execution_secondes"])
        except (TypeError, ValueError):
            return JsonResponse({"erreur": t("engine/views/_part_002.py:206:45")}, status=400)
        if timeout_exec < 1:
            return JsonResponse({"erreur": t("engine/views/_part_002.py:208:45")}, status=400)
        table.timeout_execution_secondes = timeout_exec
        table.save(update_fields=["timeout_execution_secondes"])
    if "timeout_inference_secondes" in corps:
        try:
            timeout_inf = int(corps["timeout_inference_secondes"])
        except (TypeError, ValueError):
            return JsonResponse({"erreur": t("engine/views/_part_002.py:215:45")}, status=400)
        if timeout_inf < 1:
            return JsonResponse({"erreur": t("engine/views/_part_002.py:217:45")}, status=400)
        table.timeout_inference_secondes = timeout_inf
        table.save(update_fields=["timeout_inference_secondes"])
    if "est_table_membres" in corps:
        table.est_table_membres = bool(corps["est_table_membres"])
        table.save(update_fields=["est_table_membres"])
        if table.est_table_membres:
            _assurer_table_membres(table)
    return JsonResponse({"table": _serialiser_table(table)})


# ── LIGNES : le tableau de donnees est reconstruit a chaque appel ──
def _est_vide_valeur(valeur):
    """Une case remplie uniquement d'espaces compte comme vide,
    quel que soit le formulaire ou le champ concerne."""
    if isinstance(valeur, str):
        return valeur.strip() == ""
    if isinstance(valeur, list):
        return len(valeur) == 0
    return valeur is None


def _lieu_vers_coordonnees(champ, corps):
    """Champ « geolocalisation » : si la personne a saisi un LIEU (ruelle, quartier, endroit) au lieu de coordonnées, le serveur le cherche
    et remplace le nom par « lat, lon » dans `corps` (ce sont les coordonnées qui servent à l'analyse). Rien n'est modifié si la valeur
    est déjà une position valide. Retourne un message d'erreur (lieu introuvable, service indisponible) ou None."""
    if champ.type_champ != "geolocalisation" or champ.slug not in corps:
        return None
    valeur = corps[champ.slug]
    if _est_vide_valeur(valeur) or not isinstance(valeur, str):
        return None
    from ..visualization import _parse_geo
    if _parse_geo(valeur):
        return None
    from .. import geo_adresses
    try:
        lieu = geo_adresses.premier_lieu(valeur)
    except geo_adresses.ErreurAdresse as e:
        return f"« {champ.nom} » : {e}"
    if not lieu:
        return tf("engine/views/_part_002.py:g:270:16", v1=champ.nom, v2=valeur.strip()[:60])
    corps[champ.slug] = f"{round(lieu[0], 6)}, {round(lieu[1], 6)}"
    return None


def _valider_type_valeur(champ, valeur):
    """Verifie que `valeur` correspond bien au type_champ attendu et retourne
    un message d'erreur explicite si ce n'est pas le cas (ex: du texte pour
    un champ nombre), ou None si la valeur est valide. Les cases vides ne
    sont pas concernees ici (gerees separement par la regle 'obligatoire')."""
    if _est_vide_valeur(valeur):
        return None

    t = champ.type_champ
    texte = str(valeur).strip()

    if t in ("nombre", "note"):
        try:
            int(texte)
        except (ValueError, TypeError):
            return tf("engine/views/_part_002.py:g:290:20", v1=champ.nom)
        return None

    if t == "barre_progression":
        import math
        try:
            f = float(texte)
            if not math.isfinite(f):
                raise ValueError
            n = int(f)
        except (ValueError, TypeError, OverflowError):
            return tf("engine/views/_part_002.py:g:301:20", v1=champ.nom)
        if n < 0 or n > 100:
            return tf("engine/views/_part_002.py:g:303:20", v1=champ.nom)
        return None

    if t in ("decimal", "pourcentage", "monnaie"):
        import math
        try:
            # float() accepte "nan"/"inf"/"-inf" comme valeurs VALIDES en
            # Python — sans ce contrôle explicite, elles passaient la
            # validation et corrompaient ensuite tri/agrégats/formules.
            f = float(texte.replace(",", "."))
            if not math.isfinite(f):
                raise ValueError
        except (ValueError, TypeError):
            return tf("engine/views/_part_002.py:g:316:20", v1=champ.nom)
        return None

    if t == "date":
        import datetime as _dt
        try:
            _dt.date.fromisoformat(texte)
        except ValueError:
            return tf("engine/views/_part_002.py:g:324:20", v1=champ.nom)
        return None

    if t == "datetime":
        import datetime as _dt
        try:
            _dt.datetime.fromisoformat(texte)
        except ValueError:
            return tf("engine/views/_part_002.py:g:332:20", v1=champ.nom)
        return None

    if t == "heure":
        import datetime as _dt
        try:
            _dt.time.fromisoformat(texte)
        except ValueError:
            return tf("engine/views/_part_002.py:g:340:20", v1=champ.nom)
        return None

    if t == "email":
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", texte):
            return tf("engine/views/_part_002.py:g:345:20", v1=champ.nom)
        return None

    if t == "url":
        if not re.match(r"^https?://[^\s]+\.[^\s]+", texte):
            return tf("engine/views/_part_002.py:g:350:20", v1=champ.nom)
        return None

    if t == "couleur":
        if not re.match(r"^#[0-9a-fA-F]{6}$", texte):
            return tf("engine/views/_part_002.py:g:355:20", v1=champ.nom)
        return None

    if t == "duree":
        if not re.match(r"^\d{1,3}:[0-5]\d:[0-5]\d$", texte):
            return tf("engine/views/_part_002.py:g:360:20", v1=champ.nom)
        return None

    if t == "telephone":
        chiffres = re.sub(r"[^\d]", "", texte)
        if len(chiffres) < 7 or not re.match(r"^[\d\s()+\-.]+$", texte):
            return tf("engine/views/_part_002.py:g:366:20", v1=champ.nom)
        return None

    if t == "geolocalisation":
        from ..visualization import _parse_geo
        if not _parse_geo(valeur):
            return tf("engine/views/_part_002.py:g:372:20", v1=champ.nom)
        return None

    if t in ("choix_unique", "choix_multiple"):
        options = {o.strip() for o in (champ.options_choix or "").split(",") if o.strip()}
        if not options:
            return None  # aucune option configurée : rien à valider contre
        valeurs = valeur if isinstance(valeur, list) else str(valeur).split(",")
        for v in valeurs:
            v = v.strip()
            if v and v not in options:
                return tf("engine/views/_part_002.py:g:383:24", v1=champ.nom, v2=v)
        return None

    if t == "json":
        # Document JSON libre (objet ou liste) — meme principe que
        # vecteur/matrice : stocke en texte, mais on verifie ici que c'est
        # du JSON valide plutot que de laisser une valeur cassee arriver
        # jusqu'a l'entrainement IA (ia_integration.py), ou l'erreur serait
        # bien moins claire pour l'utilisateur.
        try:
            json.loads(texte)
        except (ValueError, TypeError):
            return tf("engine/views/_part_002.py:g:395:20", v1=champ.nom)
        return None

    return None


# ── Helpers liaisons inter-tables ─────────────────────────────────────────────

def _label_depuis_valeurs(enr_id, d, champ_affichage_slug=None):
    """Label lisible d'une ligne à partir de ses valeurs (dict {slug: valeur}) : le champ d'affichage choisi, sinon la première valeur non vide."""
    if champ_affichage_slug and champ_affichage_slug in d and d[champ_affichage_slug] not in (None, ""):
        return str(d[champ_affichage_slug])
    # Premier champ non-vide
    for v in d.values():
        if v not in (None, ""):
            return str(v)
    return f"#{enr_id}"


def _label_enregistrement(enr, champ_affichage_slug=None):
    """Retourne le label lisible d'un enregistrement (pour les dropdowns relation).
    Utilise champ_affichage_slug si fourni, sinon le premier champ texte/nom."""
    return _label_depuis_valeurs(enr.id, enr.to_dict(), champ_affichage_slug)


def _creer_lien_champ(valeur_enfant, champ, enr_source_id):
    """Crée/met à jour un LienChamp et copie la valeur source dans valeur_enfant.

    Le champ.relation_colonne_source indique QUEL champ de la table liée
    on copie. Si non défini, on prend le premier champ de la table liée.
    """
    try:
        source_id = int(str(enr_source_id).strip())
    except (ValueError, TypeError):
        return
    table_liee = champ.relation_vers
    if not table_liee:
        return

    # Champ dont la VALEUR est copiée
    slug_source = champ.relation_colonne_source or ""
    champ_source = None
    if slug_source:
        champ_source = table_liee.champs.filter(slug=slug_source).first()
    if not champ_source:
        champ_source = table_liee.champs.first()
    if not champ_source:
        return

    try:
        enr_source = Enregistrement.objects.get(id=source_id, table=table_liee)
    except Enregistrement.DoesNotExist:
        return

    # Copier la valeur source dans la cellule enfant
    valeur_copiee = enr_source.to_dict().get(champ_source.slug, "")
    valeur_attendue = str(valeur_copiee) if valeur_copiee is not None else ""
    if valeur_enfant.valeur != valeur_attendue:
        ValeurChamp.objects.filter(pk=valeur_enfant.pk).update(valeur=valeur_attendue)
        valeur_enfant.valeur = valeur_attendue
        from django.utils import timezone as _tz_maj
        Enregistrement.objects.filter(pk=valeur_enfant.enregistrement_id).update(maj_le=_tz_maj.now())   # .update() ne touche pas maj_le : sinon le fichier Parquet resterait périmé

    LienChamp.objects.update_or_create(
        valeur_enfant=valeur_enfant,
        defaults={
            "enregistrement_source": enr_source,
            "champ_source": champ_source,
        },
    )


def _resoudre_labels_relation(table, lignes_dicts):
    """Pour chaque champ relation de la table, résout l'_id stocké en label
    lisible et l'ajoute sous la clé '<slug>__label'."""
    champs_relation = [
        c for c in table.champs.select_related("relation_vers").all()
        if c.type_champ == "relation" and c.relation_vers_id
    ]
    if not champs_relation:
        return lignes_dicts

    # Seules les lignes liées RÉELLEMENT référencées par les lignes exportées sont lues (et en une fois, dans le fichier Parquet de la table
    # cible) : avant, toute la table cible était relue ligne par ligne (une requête EAV par ligne) pour chaque colonne relation.
    from .. import parquet_store
    cache_labels = {}  # (table_id, enr_id, champ d'affichage) → label
    for champ in champs_relation:
        tl = champ.relation_vers
        slug_aff = champ.relation_champ_affichage or ""
        ids = set()
        for ligne in lignes_dicts:
            val = ligne.get(champ.slug)
            if val not in (None, ""):
                try:
                    ids.add(int(str(val)))
                except (ValueError, TypeError):
                    pass
        a_lire = sorted(i for i in ids if (tl.id, i, slug_aff) not in cache_labels)
        lot = []
        for debut_lot in range(0, len(a_lire), 500):   # requêtes par paquets de 500 identifiants (limite de variables des moteurs SQL)
            lot.extend(tl.enregistrements.filter(id__in=a_lire[debut_lot:debut_lot + 500]))
        for enr, valeurs in zip(lot, parquet_store.valeurs_lignes(tl, lot)):   # UNE lecture du fichier Parquet de la table cible
            cache_labels[(tl.id, enr.id, slug_aff)] = _label_depuis_valeurs(enr.id, valeurs, slug_aff)

    for ligne in lignes_dicts:
        for champ in champs_relation:
            val = ligne.get(champ.slug)
            if val not in (None, ""):
                try:
                    enr_id = int(str(val))
                    ligne[f"{champ.slug}__label"] = cache_labels.get(
                        (champ.relation_vers_id, enr_id, champ.relation_champ_affichage or ""), f"#{enr_id}"
                    )
                except (ValueError, TypeError):
                    ligne[f"{champ.slug}__label"] = str(val)
    return lignes_dicts


@login_required
@require_http_methods(["POST"])
def api_qrcode_rechercher(request, table_id):
    """Recherche securisee par QR code : lit l'image (camera agent/client),
    decode le QR (OpenCV), dechiffre son contenu avec la cle de l'entreprise,
    puis retrouve et renvoie la ligne correspondante EN BASE — jamais le
    contenu du QR directement. Reste soumis aux memes permissions (table de
    l'entreprise, isolation agent, exposition client) que la recherche classique."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    bloque = _interdit_table_non_exposee(request, table)
    if bloque:
        return bloque
    if not table.est_table_membres:
        return JsonResponse(
            {"erreur": t("engine/views/_part_002.py:473:25")},
            status=403,
        )
    fichier = request.FILES.get("image")
    if not fichier:
        return JsonResponse({"erreur": t("engine/views/_part_002.py:478:41")}, status=400)

    import cv2
    import numpy as np
    from ..qr_secure import dechiffrer_identifiant

    img = cv2.imdecode(np.frombuffer(fichier.read(), np.uint8), cv2.IMREAD_COLOR)
    contenu = ""
    if img is not None:
        contenu, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    if not contenu:
        return JsonResponse({"erreur": t("engine/views/_part_002.py:489:41")}, status=400)

    identifiant = dechiffrer_identifiant(entreprise, contenu)
    if not identifiant:
        return JsonResponse({"erreur": t("engine/views/_part_002.py:493:41")}, status=400)

    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from ..vue_groupe import enregistrements_vue
    lignes_visibles = _isoler_lignes_agent(request, membre, enregistrements_vue(table))
    # Toutes les methodes doivent pouvoir retrouver l'enregistrement : on
    # tente successivement le matricule (mode "securise"), l'empreinte
    # persistee (mode "hash"), puis la valeur brute d'un champ (mode
    # "champ") — un seul de ces 3 modes aura produit ce contenu chiffre.
    enr = (
        lignes_visibles.filter(matricule=identifiant).first()
        or lignes_visibles.filter(empreinte_carte=identifiant).first()
        or lignes_visibles.filter(valeurs__valeur=identifiant).first()
    )
    if not enr:
        return JsonResponse({"erreur": t("engine/views/_part_002.py:507:41")}, status=404)

    ligne = enr.to_dict() | {"_id": enr.id}
    ligne = _resoudre_labels_relation(table, [ligne])[0]
    if membre and membre.role == "lecteur":
        from ..grille import nettoyer_pour_client
        nettoyer_pour_client([ligne])
    from ..grille import masquer_champs
    masquer_champs([ligne], _champs_masques_pour(membre, table))
    return JsonResponse({"ligne": ligne})


@login_required
def api_lignes(request, table_id):
    from ._part_006 import _serialiser_table  # import local (réf. avant sa définition)
    table, lecture_seule_partage = _table_via_partage(request, table_id)
    if lecture_seule_partage and request.method != "GET":
        return JsonResponse({"erreur": t("engine/views/_part_002.py:523:41")}, status=403)
    entreprise = _entreprise_courante(request)
    _bloque_lecteur = _interdit_table_non_exposee(request, table) if not lecture_seule_partage else None
    if _bloque_lecteur:
        return _bloque_lecteur

    if table.source_systeme:   # table système (membres, wallet, paiement, messagerie) : lecture seule, lignes lues dans le modèle du code
        from .. import tables_systeme
        if request.method != "GET":
            return tables_systeme.refus_ecriture(table)
        try:
            page = max(1, int(request.GET.get("page", 1)))
            taille = min(max(1, int(request.GET.get("taille", 200))), 1000)
        except (ValueError, TypeError):
            page, taille = 1, 200
        lignes_sys, total = tables_systeme.page_grille(table, page, taille, (request.GET.get("q") or "").strip())
        pages = max(1, (total + taille - 1) // taille)
        return JsonResponse({"table": _serialiser_table(table), "lignes": lignes_sys, "criteres": {},
                             "pagination": {"page": min(page, pages), "taille": taille, "total": total, "pages": pages,
                                            "a_suivant": page < pages, "a_precedent": page > 1}})

    if request.method == "GET":
        from django.core.cache import cache
        if table.url_source and table.intervalle_source and not table.jeton_a_reconfigurer and cache.add(f"gsheet_maj_{table.id}", 1, table.intervalle_source * 60): _taches.maj_gsheet_async.delay(table.id)   # table née d'un Google Sheet : relue en tâche de fond selon son intervalle
        entreprise = _entreprise_courante(request)
        membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()

        from .. import grille
        est_client = bool(membre and membre.role == "lecteur")
        q = (request.GET.get("q") or "").strip()

        # ── Pagination : ?page=1&taille=200 (defaut 200 lignes/page) ──────
        try:
            page  = max(1, int(request.GET.get("page",  1)))
            taille = min(max(1, int(request.GET.get("taille", 200))), 1000)
        except (ValueError, TypeError):
            page, taille = 1, 200

        # Client (lecteur) : RECHERCHE UNIQUEMENT. Avant, l'API lui renvoyait toutes les lignes de la table
        # (la page client filtrait ensuite dans le navigateur) : n'importe quel client pouvait lire tout le
        # tableau, codes d'accès des autres clients compris. Désormais : rien sans mot-clé (2 caractères
        # minimum), 50 résultats au plus, pas de pagination, jamais les codes d'accès.
        if est_client:
            page, taille = 1, min(taille, 50)
            if len(q) < 2:
                return JsonResponse({"table": _serialiser_table(table), "lignes": [], "recherche_requise": True,
                                     "pagination": {"page": 1, "taille": taille, "total": 0, "pages": 1,
                                                    "a_suivant": False, "a_precedent": False}})

        # ── Contrôle d'accès service payant (recherche) : débité UNE fois par recherche (page 1), pas à
        # chaque page parcourue ni à chaque changement de tri.
        if membre and q and page == 1:
            from ..wallet import verifier_et_debiter_service
            _ok, _msg = verifier_et_debiter_service(entreprise, membre, "recherche")
            if not _ok:
                return JsonResponse({"erreur": _msg}, status=402)

        # Tâches conditionnelles catégorie "recherche" (voir engine/declencheurs.py) —
        # une recherche facturable venant d'être exécutée (ligne ci-dessus).
        if q and page == 1:
            from .. import declencheurs as _declencheurs
            _declencheurs.evaluer_conditions(entreprise.id, "recherche", table_id=table.id)

        # Recherche, filtres par colonne et tri sur TOUTES les lignes visibles (engine/grille.py).
        # Un groupe s'ouvre comme UNE table : la grille couvre les lignes de TOUTES ses parties (engine/vue_groupe.py).
        from ..vue_groupe import enregistrements_vue
        if not est_client:
            grille.completer_identifiants(enregistrements_vue(table))
        qs_base, criteres = grille.appliquer(
            _isoler_lignes_agent(request, membre, enregistrements_vue(table)), table, request.GET)
        total   = qs_base.count()
        pages   = max(1, (total + taille - 1) // taille)
        page    = min(page, pages)
        offset  = (page - 1) * taille
        enregistrements = list(qs_base.select_related("table")[offset: offset + taille])

        # Valeurs lues dans le fichier Parquet de la table (seules les lignes de la page), jamais dans l'EAV ligne par ligne.
        # Couleurs de la ligne ("_couleur") et de ses cellules ("_couleurs") : mise en forme de la grille, jamais mêlée aux valeurs.
        from .. import parquet_store
        valeurs_page = parquet_store.valeurs_page(table, enregistrements)
        lignes = [v | {"_id": e.id} | e.couleurs_mise_en_forme()
                  | ({"_table_id": e.table_id} if table.groupe_id else {})
                  | ({"_liste_noire": True} if e.liste_noire else {}) for v, e in zip(valeurs_page, enregistrements)]
        lignes = _resoudre_labels_relation(table, lignes)
        if est_client:
            grille.nettoyer_pour_client(lignes)
        grille.masquer_champs(lignes, _champs_masques_pour(membre, table))
        return JsonResponse({
            "table":  _serialiser_table(table),
            "lignes": lignes,
            "criteres": criteres,
            "liste_noire_total": len(parquet_store.ids_liste_noire(table)),   # lignes écartées des analyses (toute la table / tout le groupe)
            "pagination": {
                "page":  page,
                "taille": taille,
                "total": total,
                "pages": pages,
                "a_suivant":   page < pages,
                "a_precedent": page > 1,
            },
        })

    if request.method == "POST":
        # Un groupe s'ouvre comme UNE table : toute nouvelle ligne arrive dans sa dernière partie (règle d'ajout du groupe).
        from ..vue_groupe import derniere_partie
        _table_formulaire = table   # la structure du formulaire conditionnel est portée par la table ouverte (pas par la dernière partie d'un groupe)
        table = derniere_partie(table)
        from ..flux_media import table_de_flux
        if table_de_flux(table):  # table de flux temps réel : une seule cellule, jamais de ligne
            return JsonResponse({"erreur": t("engine/views/_part_002.py:g:646:44")}, status=400)
        _m_post = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
        if _m_post and _m_post.role == "lecteur" and not table.expose_client:
            # accessible_client = formulaire OU recherche : une table en recherche seule ne reçoit aucune saisie client
            return JsonResponse({"erreur": t("engine/views/_part_002.py:g:650:44")}, status=403)
        # Multipart (necessaire des qu'un champ fichier/image est present) ou
        # JSON classique (cas le plus courant) : les deux sont acceptes.
        if request.content_type and request.content_type.startswith("multipart/"):
            corps, fichiers = request.POST, request.FILES
        else:
            corps, fichiers = json.loads(request.body or "{}"), {}

        # ── Idempotence anti-doublon : le frontend envoie un token unique par
        # tentative d'insertion (header X-Idempotency-Key OU clé "_idem_key"
        # dans le corps JSON/multipart). Si le même token arrive une deuxième
        # fois (double-clic, retry réseau, worker dupliqué), on retourne la
        # ligne déjà créée sans rien insérer.
        idem_key = (
            request.headers.get("X-Idempotency-Key")
            or (corps.get("_idem_key") if isinstance(corps, dict) else corps.get("_idem_key"))
        )
        if idem_key:
            enr_existant = (
                Enregistrement.objects
                .filter(table=table, idem_key=idem_key)
                .first()
            )
            if enr_existant:
                ligne_dict = enr_existant.to_dict() | {"_id": enr_existant.id}
                ligne_dict = _resoudre_labels_relation(table, [ligne_dict])[0]
                return JsonResponse({"ligne": ligne_dict}, status=200)

        def _est_vide(valeur):
            """Une case remplie uniquement d'espaces compte comme vide,
            quel que soit le formulaire ou le champ concerne."""
            if isinstance(valeur, str):
                return valeur.strip() == ""
            if isinstance(valeur, list):
                return len(valeur) == 0
            return valeur is None

        # Capture d'un flux média (photo, vidéo, son) : le formulaire du flux envoie le nom du fichier déjà archivé
        # (`_flux_fichier`, répétable) et le flux (`_flux_champ`). Le fichier devient la valeur de la colonne média de la ligne,
        # exactement comme un fichier téléversé (obligatoire satisfait, fichier enregistré dans champs/…, valeur écrite dans la même
        # transaction) ; l'original archivé n'est retiré qu'une fois la ligne créée (`_flux_garder` : on le garde pour une autre ligne).
        _captures_flux = corps.getlist("_flux_fichier") if hasattr(corps, "getlist") else (corps.get("_flux_fichier") or [])
        if isinstance(_captures_flux, str):
            _captures_flux = [_captures_flux]
        if _captures_flux:
            _refus_flux = _interdit_lecteur(request)      # les archives d'un flux ne sont jamais ouvertes à un client
            if _refus_flux:
                return _refus_flux
            from .. import flux_media as _fm
            try:
                fichiers = {**dict(fichiers.items()),
                            **_fm.rattacher_captures(entreprise.id, table, corps.get("_flux_champ"), _captures_flux)}
            except ValueError as e:
                return JsonResponse({"erreur": str(e)}, status=400)

        # Formulaire conditionnel : les champs des sections non traversées (d'après les réponses) restent vides et ne sont
        # jamais exigés — la table garde les mêmes colonnes, seules certaines cellules restent vides.
        from .. import formulaire_conditionnel as _fc
        _hors_parcours = _fc.champs_hors_parcours(_table_formulaire, corps)
        if _hors_parcours:
            if hasattr(corps, "getlist"):
                corps = corps.copy()
                for _s in _hors_parcours:
                    corps.pop(_s, None)
            else:
                corps = {k: v for k, v in corps.items() if k not in _hors_parcours}
            fichiers = {k: v for k, v in dict(fichiers.items()).items() if k not in _hors_parcours}

        # Validation des champs obligatoires (les espaces seuls ne comptent pas comme une valeur)
        erreurs_validation = {}
        for champ in table.champs.filter(obligatoire=True).exclude(type_champ__in=("formule", "constante", "horodatage", "id_client")).exclude(slug__in=_hors_parcours):
            if champ.type_champ in ChampDynamique.TYPES_FICHIER:
                valeur_presente = champ.slug in fichiers
            else:
                valeur_presente = champ.slug in corps and not _est_vide(corps[champ.slug])
            if not valeur_presente:
                erreurs_validation[champ.slug] = tf("engine/views/_part_002.py:g:695:50", v1=champ.nom)

        # Validation du type attendu (ex: refuser du texte pour un champ nombre)
        for champ in table.champs.all():
            if champ.slug in erreurs_validation or champ.type_champ in ChampDynamique.TYPES_FICHIER:
                continue
            if champ.slug in corps:
                erreur_type = _lieu_vers_coordonnees(champ, corps) or _valider_type_valeur(champ, corps[champ.slug])
                if erreur_type:
                    erreurs_validation[champ.slug] = erreur_type

        # Fichiers corrompus ou piégés (programme, script, macro, PDF actif, virus) : refusés ICI, avant toute écriture (securite.verifier_fichier)
        from ..securite import verifier_fichier, verifier_taille_champ
        for champ in table.champs.filter(type_champ__in=ChampDynamique.TYPES_FICHIER):
            if champ.slug in fichiers and champ.slug not in erreurs_validation:
                _refus = verifier_taille_champ(fichiers[champ.slug], champ) or verifier_fichier(fichiers[champ.slug], champ.type_champ)
                if _refus:
                    erreurs_validation[champ.slug] = _refus

        if erreurs_validation:
            return JsonResponse({
                "erreur": " · ".join(erreurs_validation.values()),
                "champs": erreurs_validation,
            }, status=400)

        # Table d'un groupe : on n'y ajoute des lignes que dans la DERNIÈRE table du groupe (les précédentes
        # restent éditables et leurs lignes supprimables, mais n'en reçoivent plus).
        if not table.est_derniere_du_groupe:
            return JsonResponse({
                "erreur": tf("engine/views/_part_002.py:g:716:27", v1=table.nom, v2=table.groupe.nom),
                "groupe_ajout_refuse": True,
            }, status=400)

        # Plafond de lignes, configurable par table (native ou issue d'un
        # import de fichier) — voir TableDynamique.plafond_lignes.
        plafond = table.plafond_lignes
        if plafond is not None and table.enregistrements.count() >= plafond:
            return JsonResponse({
                "erreur": tf("engine/views/_part_002.py:g:726:27", v1=plafond),
                "peut_etendre": True, "plafond_lignes": plafond,
            }, status=400)

        # ── Création de la ligne : tout dans UNE SEULE transaction, y
        # compris le débit du service payant. Avant ce correctif, le débit
        # (verifier_et_debiter_service) était effectué AVANT le bloc
        # atomic() et la boucle de remplissage des ValeurChamp vivait
        # ENTIÈREMENT hors transaction : un échec en cours de boucle
        # laissait une ligne partielle (certains champs écrits, d'autres
        # non) alors que le wallet avait déjà été débité, sans aucun
        # rollback possible. Désormais : débit + création de la ligne +
        # tous les champs + recalcul de formules + bloc blockchain forment
        # un seul tout-ou-rien — si quoi que ce soit échoue, rien n'est
        # committé (le wallet n'est pas débité).
        from django.db import transaction as _tx, IntegrityError
        from .. import acces_entreprise as _acces
        if table.a_identifiants_connexion and not _acces.place_disponible(entreprise):   # plafond de trafic de l'entreprise (page super-admin)
            return JsonResponse({"erreur": _acces.message_limite(entreprise)}, status=402)
        try:
            with _tx.atomic():
                _membre_post = MembreEntreprise.objects.filter(
                    utilisateur=request.user, entreprise=entreprise
                ).first()
                if _membre_post:
                    from ..wallet import verifier_et_debiter_service
                    _ok, _msg = verifier_et_debiter_service(entreprise, _membre_post, "enregistrement")
                    if not _ok:
                        return JsonResponse({"erreur": _msg}, status=402)

                enregistrement = Enregistrement.objects.create(
                    table=table,
                    idem_key=idem_key or "",
                    cree_par=request.user,
                )
                if table.est_nosql:
                    # ── Table NoSQL : toutes les valeurs de la ligne dans un
                    # seul bloc JSON (nosql_store.py) — pas de ValeurChamp,
                    # pas de relations/champ bidirectionnel (non supportés
                    # pour ce type de stockage pour l'instant).
                    from .. import nosql_store
                    valeurs_nosql = {}
                    for champ in table.champs.all():
                        if champ.type_champ in ("formule", "constante", "horodatage", "id_client"):
                            continue
                        if champ.type_champ in ChampDynamique.TYPES_FICHIER and champ.slug in fichiers:
                            from django.core.files.storage import default_storage
                            fichier = fichiers[champ.slug]
                            chemin = default_storage.save(f"champs/{table.slug}/{champ.slug}/{fichier.name}", fichier)
                            valeurs_nosql[champ.slug] = default_storage.url(chemin)
                        elif champ.type_champ == "choix_multiple" and hasattr(corps, "getlist") and corps.getlist(champ.slug):
                            valeurs_nosql[champ.slug] = corps.getlist(champ.slug)
                        elif champ.slug in corps:
                            valeur = corps[champ.slug]
                            if champ.obligatoire and _est_vide(valeur):
                                continue
                            valeurs_nosql[champ.slug] = nosql_store.coercer_valeur(champ, valeur)
                    nosql_store.ecrire_valeurs(enregistrement, valeurs_nosql)
                    enregistrement.refresh_from_db()
                    # Pas de ValeurChamp ⇒ pas de signal post_save (apps.
                    # recalculer_champs_formule) : on déclenche donc ici
                    # explicitement ce que le signal ferait pour une table SQL.
                    from .. import calculs as _calculs
                    _calculs.assurer_constantes(enregistrement)
                    _calculs.recalculer_champs_formule_ligne(enregistrement)
                else:
                    for champ in table.champs.all():
                        if champ.type_champ in ("formule", "constante", "horodatage", "id_client"):
                            # Jamais saisi manuellement : toujours recalculé par le
                            # signal post_save (voir apps.recalculer_champs_formule),
                            # après la boucle ci-dessous — ignorer toute valeur reçue.
                            continue
                        if champ.type_champ in ChampDynamique.TYPES_FICHIER and champ.slug in fichiers:
                            from django.core.files.storage import default_storage
                            fichier = fichiers[champ.slug]
                            chemin = default_storage.save(f"champs/{table.slug}/{champ.slug}/{fichier.name}", fichier)
                            ValeurChamp.objects.create(
                                enregistrement=enregistrement, champ=champ, valeur=default_storage.url(chemin)
                            )
                        elif champ.type_champ == "choix_multiple" and hasattr(corps, "getlist") and corps.getlist(champ.slug):
                            ValeurChamp.objects.create(
                                enregistrement=enregistrement, champ=champ, valeur=",".join(corps.getlist(champ.slug))
                            )
                        elif champ.slug in corps:
                            valeur = corps[champ.slug]
                            if champ.type_champ == "choix_multiple" and isinstance(valeur, list):
                                valeur = ",".join(valeur)
                            elif isinstance(valeur, str):
                                valeur = valeur.strip()
                            if champ.obligatoire and _est_vide(valeur):
                                continue
                            # str(None) == "None" : on stocke "" pour un null JSON
                            # plutôt que la chaîne littérale "None".
                            vc = ValeurChamp.objects.create(
                                enregistrement=enregistrement, champ=champ,
                                valeur="" if valeur is None else str(valeur),
                            )
                            # ── Enregistrer le lien si c'est un champ relation ──
                            if champ.type_champ == "relation" and champ.relation_vers_id and valeur:
                                _creer_lien_champ(vc, champ, valeur)
                                # Surcharge optionnelle : la ligne a été choisie via le
                                # picker (pour créer le lien) MAIS l'utilisateur a aussi
                                # tapé une valeur différente dans le champ d'édition
                                # directe (colonne bidirectionnelle) — cette valeur prime
                                # sur la copie automatique et remonte vers la source.
                                cle_directe = f"{champ.slug}__valeur_directe"
                                if champ.relation_bidirectionnelle and cle_directe in corps:
                                    nouvelle_valeur = corps[cle_directe]
                                    if isinstance(nouvelle_valeur, str):
                                        nouvelle_valeur = nouvelle_valeur.strip()
                                    vc.refresh_from_db(fields=["valeur"])
                                    if vc.valeur != str(nouvelle_valeur):
                                        vc.valeur = str(nouvelle_valeur)
                                        # save() (pas update()) pour déclencher le signal
                                        # qui repropage vers la ligne source liée.
                                        vc.save(update_fields=["valeur"])
                # Toute nouvelle ligne peut faire partie de la cible d'une formule de
                # calcul (colonne entière, plage incluant cette ligne...) : on
                # recalcule tout de suite pour que les tables de calcul reflètent
                # immédiatement la donnée ajoutée.
                from ..calculs import assurer_constantes as _assurer
                _assurer(enregistrement)   # horodatage meme si aucune valeur n'a ete ecrite (aucun signal)
                from ..calc_champ import recalculer_formules_table
                recalculer_formules_table(table, enregistrement)   # formules ∑ / « = » (ligne neuve + lignes dépendantes)
                from ..calculs import recalculer_lignes_pour_table_source
                recalculer_lignes_pour_table_source(table)

                # Blockchain (voir models.Bloc) : on journalise ICI, apres que
                # toutes les ValeurChamp de la ligne aient ete ecrites (et les
                # formules recalculees) — un signal post_save sur Enregistrement
                # se declencherait trop tot et produirait un bloc sans donnees.
                # Toujours active : aucune desactivation possible, table par table
                # ou pour l'entreprise (voir vue_blockchain).
                from ..models import Bloc
                Bloc.ajouter_transaction(entreprise.id, {
                        "type": "creation_ligne", "table_id": table.id, "table": table.nom,
                        "enregistrement_id": enregistrement.id, "agent_id": request.user.id,
                        "donnees": enregistrement.to_dict_lisible(),
                    })
        except IntegrityError:
            # La contrainte BD (table, idem_key) a rejeté un doublon :
            # un worker concurrent vient de créer la même ligne juste avant.
            # On retourne la ligne existante sans erreur. Comme tout était
            # dans la même transaction, un éventuel débit wallet fait plus
            # haut dans CE bloc a été annulé (rollback) — pas de double-débit.
            enr_existant = Enregistrement.objects.filter(
                table=table, idem_key=idem_key
            ).first()
            if enr_existant:
                ligne_dict = enr_existant.to_dict() | {"_id": enr_existant.id}
                ligne_dict = _resoudre_labels_relation(table, [ligne_dict])[0]
                return JsonResponse({"ligne": ligne_dict}, status=200)
            raise  # idem_key vide ou autre cause — relancer

        # Fichier Parquet de la table (tables EAV uniquement, voir parquet_store.py) : la nouvelle ligne y est synchronisée une
        # fois la transaction validée (valeurs, formules et valeurs liées comprises). Meilleur effort : ne fait jamais échouer la saisie.
        from .. import parquet_store
        _tx.on_commit(lambda: parquet_store.synchroniser_ligne(enregistrement))

        # Notification des agents/admin : uniquement quand la ligne vient
        # d'un CLIENT (rôle lecteur) sur un formulaire qui lui est exposé —
        # jamais quand un agent/admin saisit lui-même une ligne (il n'a pas
        # besoin d'être notifié de sa propre action). Voir notifications.py ;
        # meilleur effort, ne doit jamais faire échouer la soumission.
        auteur_membre = MembreEntreprise.objects.filter(
            utilisateur=request.user, entreprise=entreprise
        ).first()
        if auteur_membre is not None and auteur_membre.role == "lecteur":
            try:
                _notifications.notifier_nouvelle_ligne_client(
                    table, enregistrement, client_membre=auteur_membre,
                )
            except Exception:
                pass

        if _captures_flux:
            from .. import flux_media as _fm
            _fm.finaliser_captures(entreprise.id, _captures_flux, str(corps.get("_flux_garder") or "") in ("1", "true"), fichiers)

        ligne_dict = enregistrement.to_dict() | {"_id": enregistrement.id}
        ligne_dict = _resoudre_labels_relation(table, [ligne_dict])[0]
        return JsonResponse({"ligne": ligne_dict}, status=201)

    return JsonResponse({"erreur": t("engine/views/_part_002.py:855:37")}, status=405)


@login_required
@_pdf_erreur_json
def api_exporter_table_pdf(request, table_id):
    """Exporte la liste brute des lignes de la table en PDF (pas d'IA,
    juste un tableau — même donnée que l'export CSV, en PDF)."""
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    _bloque_lecteur = _interdit_table_non_exposee(request, table)
    if _bloque_lecteur:
        return _bloque_lecteur

    from ..reports import generer_pdf_liste_table

    champs = list(table.champs.order_by("ordre"))
    # Même périmètre que la grille : un agent n'exporte que SES lignes ; un client n'exporte que les
    # résultats de SA recherche (q obligatoire), jamais la table entière. Recherche/filtres/tri de la grille
    # (engine/grille.py) repris tels quels : l'export correspond à ce qui est affiché.
    from .. import grille
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if membre and membre.role == "lecteur" and len((request.GET.get("q") or "").strip()) < 2:
        return JsonResponse({"erreur": t("engine/views/_part_002.py:877:41")},
                            status=400)
    from ..vue_groupe import enregistrements_vue   # un groupe de tables s'exporte comme UNE table
    with grille.chrono("filtre et tri", table.slug + " (pdf)"):
        enregistrements, _ = grille.appliquer(
            _isoler_lignes_agent(request, membre, enregistrements_vue(table)), table, request.GET)
    if membre and membre.role == "lecteur":
        enregistrements = enregistrements[:50]
    # Aucun plafond de lignes par défaut : toutes les lignes de la vue sont imprimées (un plafond reste possible via
    # GYPLATFORM_PDF_MAX_LIGNES, voir reports.limite_lignes_pdf).
    from ..reports._part_004 import limite_lignes_pdf
    with grille.chrono("comptage et lecture des lignes", table.slug + " (pdf)"):
        try:
            total_lignes = enregistrements.count()
        except TypeError:   # liste Python : list.count() exige un argument
            total_lignes = len(enregistrements)
        enregistrements = list(enregistrements[:limite_lignes_pdf()])
    from .. import parquet_store
    with grille.chrono("valeurs (Parquet)", table.slug + " (pdf)"):
        lignes, mises_en_forme = parquet_store.lignes_affichage_pdf(table, enregistrements)   # valeurs : Parquet → texte CSV (pandas) → table par regex (pdf_texte.py) ; couleurs de lignes/cellules, dans le même ordre que `lignes`
    if membre and membre.role == "lecteur":
        grille.nettoyer_pour_client(lignes)
    grille.masquer_champs(lignes, _champs_masques_pour(membre, table))

    tables_calcul = list(
        table.tables_calcul.filter(inclure_dans_pdf=True)
        .prefetch_related("lignes__formule", "lignes__champ_cible", "lignes__champs_cibles")
    )
    # Structure réelle : cellules fusionnées reproduites dans le PDF, comme sur la grille, QUELLE QUE SOIT LA VUE (recherche, filtre, tri,
    # lignes limitées, groupe) : une fusion est ancrée sur des lignes, on la retrouve dans la vue exportée (voir engine/export_structure.py).
    from .. import export_structure
    fusions = list(table.fusions_cellules.all())
    fusions_vue = []
    if fusions:
        from ..fusion_cellule import resynchroniser
        resynchroniser(table, fusions)
        fusions_vue = export_structure.fusions_vue(
            table, fusions, [e.id for e in enregistrements], champs,
            calculs=export_structure.calculs_formules(table, fusions, champs))
    with grille.chrono("mise en page du PDF", table.slug + " (pdf)"):
        pdf_bytes = generer_pdf_liste_table(table, champs, lignes, tables_calcul=tables_calcul, mises_en_forme=mises_en_forme, total_lignes=total_lignes, fusions_vue=fusions_vue)

    from django.http import HttpResponse
    reponse = HttpResponse(pdf_bytes, content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="{table.slug}.pdf"'
    return reponse




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
