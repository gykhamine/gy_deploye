"""COULEUR DE COLONNE (table dynamique) : choisie dans la grille « Données », reprise telle quelle dans les PDF.

  GET  /api/couleurs-colonne/                                  la palette proposée par l'écran : [{nom, couleur}] (couleur_colonne.PALETTE)
                                                               + `noms` {nom: couleur} pour reconnaître un nom saisi sans aller-retour serveur.
  POST /api/tables/<id>/champs/<champ_id>/couleur/             corps {"couleur": "bleu" | "#1e90ff" | "1e90ff" | ""} ("" = retirer la couleur).
        Le serveur valide et renvoie la couleur normalisée « #rrggbb ». Si la table appartient à un groupe, les colonnes de mêmes slugs
        des autres parties reçoivent la même couleur (la structure du groupe reste identique, comme pour la restructuration).

Droits = ceux de la restructuration d'une table (api_restructurer_table) : jamais un client (lecteur), jamais une table d'une autre
entreprise (donc jamais une table reçue par partage). La couleur est de la mise en forme : aucune valeur de cellule n'est touchée.

Même mécanisme (même fenêtre de choix, même validation, mêmes droits) pour :
  POST /api/calculs-couleur/<table|ligne>/<id>/   table de calcul ou calcul          (TableCalcul.couleur / LigneCalcul.couleur)
  POST /api/skills/<id>/couleur/  et  /api/analyses/<id>/couleur/   carte ou section du rapport IA (Skill/DemandeAgent.couleurs, clé « _carte » ou nom de section)
  POST /api/ontologie/noeud-couleur/              un nœud précis du graphe            (CouleurNoeudOntologie)
La palette (GET /api/couleurs-colonne/) propose aussi « charte » : les couleurs de marque de l'entreprise.

LIGNES et CELLULES de la grille « Données » (Enregistrement.couleur / Enregistrement.couleurs_cellules), INDÉPENDANTES l'une de
l'autre et de la colonne (cellule > ligne > colonne, voir couleur_colonne.py) :
  POST /api/tables/<id>/lignes-couleur/    corps {"couleur": ..., "lignes": [id, ...]}                  colorie ces lignes entières
  POST /api/tables/<id>/cellules-couleur/  corps {"couleur": ..., "lignes": [id, ...], "champs": [slug, ...]}   colorie ces cellules
        (le rectangle lignes × colonnes d'une sélection). "" = retirer la couleur ; retirer celle d'une cellule ne touche pas la ligne.
Droits : jamais un client (lecteur), jamais une table non exposée à l'utilisateur, jamais une ligne d'un autre agent (même isolation
que la liste des lignes). Seule la présentation change : aucune valeur n'est touchée.
"""
from engine.i18n import t, tf, tl
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import couleur_colonne as cc
from .models import Bloc, ChampDynamique, CouleurBloc, CouleurNoeudOntologie, DemandeAgent, DocumentGenere, Enregistrement, LigneCalcul, MembreEntreprise, Skill, TableCalcul, TableDynamique
from .views import _entreprise_courante, _interdit_lecteur, _interdit_table_non_exposee, _isoler_lignes_agent

# CHARTE DE L'ENTREPRISE : les couleurs de marque (ConfigurationIA, onglet « Configuration des documents »), proposées en tête de
# la fenêtre de choix sous « Ma charte » pour qu'une carte, une colonne ou un nœud reprenne exactement la couleur de la marque.
CHARTE = (
    ("Accent", "couleur_accent_document"), (_gtfr("engine/views_couleur.py:41:45"), "couleur_entete_document"),
    (tl("engine/views_couleur.py:g:41:6"), "couleur_tableau_entete_document"), (tl("engine/views_couleur.py:g:41:65"), "couleur_lignes_alternees_document"),
    (tl("engine/views_couleur.py:g:42:6"), "couleur_texte_document"), ("Fond", "couleur_fond_document"),
    ("Corps", "couleur_corps_document"), (tl("engine/views_couleur.py:g:43:43"), "couleur_pied_page_document"),
)


@login_required
@require_http_methods(["GET"])
def api_palette_couleurs(request):
    cfg = getattr(_entreprise_courante(request), "configuration_ia", None)
    charte = []
    for nom, champ in CHARTE:
        try:
            charte.append({"nom": nom, "couleur": cc.normaliser_couleur(getattr(cfg, champ, "")) or None})
        except ValueError:
            pass   # couleur de charte illisible : ignorée
    return JsonResponse({
        "palette": [{"nom": n, "couleur": cc.COULEURS_NOMMEES[n]} for n in cc.PALETTE],
        "noms": cc.COULEURS_NOMMEES,
        "charte": [c for c in charte if c["couleur"]],
    })


def _couleur_demandee(request):
    """(couleur normalisée, corps, None) ou (None, None, réponse d'erreur) : lecteur refusé, JSON illisible, couleur inconnue."""
    refus = _interdit_lecteur(request)
    if refus:
        return None, None, refus
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return None, None, JsonResponse({"erreur": t("engine/views_couleur.py:g:72:52")}, status=400)
    corps = corps if isinstance(corps, dict) else {}
    try:
        return cc.normaliser_couleur(corps.get("couleur")), corps, None
    except ValueError as e:
        return None, None, JsonResponse({"erreur": str(e)}, status=400)


def _journaliser(request, entreprise, genre, nom, couleur):
    Bloc.ajouter_transaction(entreprise.id, {"type": "couleur_" + genre, "nom": nom, "agent_id": request.user.id, "couleur": couleur})


@login_required
@require_http_methods(["POST"])
def api_couleur_calcul(request, genre, objet_id):
    """Couleur d'une TABLE de calcul (genre « table ») ou d'un CALCUL de cette table (genre « ligne »). Corps {"couleur": ...}."""
    couleur, _, erreur = _couleur_demandee(request)
    if erreur:
        return erreur
    entreprise = _entreprise_courante(request)
    if genre not in ("table", "ligne"):
        return JsonResponse({"erreur": t("engine/views_couleur.py:g:93:40")}, status=404)
    if genre == "table":
        objet = get_object_or_404(TableCalcul, id=objet_id, entreprise=entreprise)
    else:
        objet = get_object_or_404(LigneCalcul, id=objet_id, table_calcul__entreprise=entreprise)
    objet.couleur = couleur
    objet.save(update_fields=["couleur"])
    _journaliser(request, entreprise, "calcul_" + genre, objet.nom, couleur)
    return JsonResponse({"id": objet.id, "couleur": couleur})


def _couleur_rapport(request, objet, nom):
    couleur, corps, erreur = _couleur_demandee(request)
    if erreur:
        return erreur
    cle = str(corps.get("section") or "_carte")[:60]
    couleurs = dict(objet.couleurs or {})
    if couleur:
        couleurs[cle] = couleur
    else:
        couleurs.pop(cle, None)
    objet.couleurs = couleurs
    objet.save(update_fields=["couleurs"])
    _journaliser(request, _entreprise_courante(request), "rapport_ia", f"{nom} / {cle}", couleur)
    return JsonResponse({"id": objet.id, "couleurs": couleurs})


@login_required
@require_http_methods(["POST"])
def api_couleur_skill(request, skill_id):
    """Couleur d'une carte du rapport d'un skill : la carte entière (pas de « section ») ou une section (« kpi », « conclusion »…)."""
    return _couleur_rapport(request, get_object_or_404(Skill, id=skill_id, entreprise=_entreprise_courante(request)), "skill")


@login_required
@require_http_methods(["POST"])
def api_couleur_analyse(request, demande_id):
    """Même chose pour une carte d'analyse du tableau de bord d'une table (DemandeAgent) : corps {"couleur", "section"?}."""
    return _couleur_rapport(request, get_object_or_404(DemandeAgent, id=demande_id, entreprise=_entreprise_courante(request)), "analyse")


@login_required
@require_http_methods(["POST"])
def api_couleur_document(request, doc_id):
    """Couleur de la carte d'un document (onglet « Documents » d'une table) : corps {"couleur": ...}, "" = retirer. Même règle que les cartes de rapport."""
    couleur, _, erreur = _couleur_demandee(request)
    if erreur:
        return erreur
    entreprise = _entreprise_courante(request)
    doc = get_object_or_404(DocumentGenere, id=doc_id, entreprise=entreprise)
    doc.couleur = couleur
    doc.save(update_fields=["couleur"])
    _journaliser(request, entreprise, "document", doc.titre or str(doc.id), couleur)
    return JsonResponse({"id": doc.id, "couleur": couleur})


@login_required
@require_http_methods(["POST"])
def api_couleur_noeud(request):
    """Couleur d'UN nœud du graphe d'ontologie (ex. « table:5 »), prioritaire sur la couleur de son type. Corps {"noeud", "couleur"}."""
    couleur, corps, erreur = _couleur_demandee(request)
    if erreur:
        return erreur
    entreprise = _entreprise_courante(request)
    noeud = str(corps.get("noeud") or "").strip()[:120]
    if not noeud:
        return JsonResponse({"erreur": t("engine/views_couleur.py:g:144:40")}, status=400)
    if couleur:
        CouleurNoeudOntologie.objects.update_or_create(entreprise=entreprise, noeud=noeud, defaults={"couleur": couleur})
    else:
        CouleurNoeudOntologie.objects.filter(entreprise=entreprise, noeud=noeud).delete()
    _journaliser(request, entreprise, "noeud_ontologie", noeud, couleur)
    return JsonResponse({"noeud": noeud, "couleur": couleur})


@login_required
@require_http_methods(["POST"])
def api_couleur_bloc(request):
    """Couleur d'un BLOC de la blockchain (corps {"bloc": <index>, "couleur"}) ou de tous les blocs d'un TYPE de transaction
    (corps {"type": "creation_ligne", "couleur"}, administrateur seulement). "" = retirer. Le bloc précis l'emporte sur son type.
    La couleur est rangée à part (CouleurBloc) : le contenu scellé du bloc n'est jamais touché. Un agent ne colore que les blocs
    où figure une de ses transactions (même isolation que la page Blockchain)."""
    couleur, corps, erreur = _couleur_demandee(request)
    if erreur:
        return erreur
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    from .delegation import a_pouvoir
    est_admin = a_pouvoir(membre, "couleurs")
    if corps.get("type") not in (None, ""):
        if not est_admin:
            return JsonResponse({"erreur": t("engine/views_couleur.py:bloc_type_admin")}, status=403)
        type_tx = str(corps["type"]).strip()[:100]
        cle, nom = "type:" + type_tx, type_tx
    else:
        try:
            index = int(corps.get("bloc"))
        except (TypeError, ValueError):
            return JsonResponse({"erreur": t("engine/views_couleur.py:bloc_invalide")}, status=400)
        bloc = Bloc.objects.filter(entreprise=entreprise, index=index).first()
        if bloc is None:
            return JsonResponse({"erreur": t("engine/views_couleur.py:bloc_invalide")}, status=404)
        if not est_admin and not any(str(x.get("agent_id")) == str(request.user.id) for x in bloc.transactions):
            return JsonResponse({"erreur": t("engine/views_couleur.py:bloc_invalide")}, status=404)
        cle, nom = f"bloc:{index}", f"bloc #{index}"
    if couleur:
        CouleurBloc.objects.update_or_create(entreprise=entreprise, cle=cle, defaults={"couleur": couleur})
    else:
        CouleurBloc.objects.filter(entreprise=entreprise, cle=cle).delete()
    _journaliser(request, entreprise, "bloc", nom, couleur)
    return JsonResponse({"cle": cle, "couleur": couleur})


@login_required
@require_http_methods(["POST"])
def api_couleur_champ(request, table_id, champ_id):
    refus = _interdit_lecteur(request)
    if refus:
        return refus
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _interdit_table_non_exposee(request, table)   # un agent restreint (tables_autorisees) ne colore pas une table qui lui est fermée
    if refus:
        return refus
    champ = get_object_or_404(ChampDynamique, id=champ_id, table=table)
    try:
        corps = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views_couleur.py:g:168:40")}, status=400)
    try:
        couleur = cc.normaliser_couleur(corps.get("couleur") if isinstance(corps, dict) else None)
    except ValueError as e:
        return JsonResponse({"erreur": str(e)}, status=400)
    champs = ChampDynamique.objects.filter(slug=champ.slug, table=table)
    if table.groupe_id:
        champs = ChampDynamique.objects.filter(slug=champ.slug, table__in=table.groupe.tables.all())
    champs.update(couleur=couleur)
    Bloc.ajouter_transaction(entreprise.id, {
        "type": "couleur_colonne", "table_id": table.id, "table": table.nom, "champ": champ.nom,
        "agent_id": request.user.id, "couleur": couleur,
    })
    return JsonResponse({"champ_id": champ.id, "slug": champ.slug, "couleur": couleur})


LIGNES_MAX_PAR_REQUETE = 5000


def _lignes_et_table_colorables(request, table_id):
    """(table, corps, couleur, ids, None) ou (None, None, None, None, réponse d'erreur) : droits, JSON et couleur validés."""
    couleur, corps, erreur = _couleur_demandee(request)
    if erreur:
        return None, None, None, None, erreur
    entreprise = _entreprise_courante(request)
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=entreprise)
    refus = _interdit_table_non_exposee(request, table)
    if refus:
        return None, None, None, None, refus
    brut = corps.get("lignes")
    try:
        ids = sorted({int(i) for i in (brut if isinstance(brut, list) else [])})
    except (TypeError, ValueError):
        return None, None, None, None, JsonResponse({"erreur": t("engine/views_couleur.py:g:201:64")}, status=400)
    if not ids:
        return None, None, None, None, JsonResponse({"erreur": t("engine/views_couleur.py:g:203:64")}, status=400)
    if len(ids) > LIGNES_MAX_PAR_REQUETE:
        return None, None, None, None, JsonResponse({"erreur": tf("engine/views_couleur.py:g:205:64", v1=LIGNES_MAX_PAR_REQUETE)}, status=400)
    return table, corps, couleur, ids, None


def _lignes_visibles(request, table, ids):
    from .vue_groupe import enregistrements_vue as _enregistrements_vue   # ids issus de la grille : tout le groupe
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=table.entreprise).first()
    return _isoler_lignes_agent(request, membre, _enregistrements_vue(table).filter(id__in=ids))


@login_required
@require_http_methods(["POST"])
def api_couleur_lignes(request, table_id):
    """Colorie (ou, avec "", décolorie) des LIGNES entières. Les couleurs de cellules de ces lignes ne sont pas touchées."""
    table, _, couleur, ids, erreur = _lignes_et_table_colorables(request, table_id)
    if erreur:
        return erreur
    nb = _lignes_visibles(request, table, ids).update(couleur=couleur)
    Bloc.ajouter_transaction(table.entreprise_id, {
        "type": "couleur_lignes", "table_id": table.id, "table": table.nom, "nb": nb, "agent_id": request.user.id, "couleur": couleur,
    })
    return JsonResponse({"couleur": couleur, "lignes": nb})


@login_required
@require_http_methods(["POST"])
def api_couleur_cellules(request, table_id):
    """Colorie (ou, avec "", décolorie) le rectangle de CELLULES lignes × colonnes. La couleur de la ligne n'est pas touchée."""
    table, corps, couleur, ids, erreur = _lignes_et_table_colorables(request, table_id)
    if erreur:
        return erreur
    brut = corps.get("champs")
    existants = set(table.champs.values_list("slug", flat=True))
    slugs = sorted({str(s) for s in (brut if isinstance(brut, list) else []) if str(s) in existants})
    if not slugs:
        return JsonResponse({"erreur": t("engine/views_couleur.py:g:240:40")}, status=400)
    lignes = list(_lignes_visibles(request, table, ids).only("id", "couleurs_cellules"))
    for ligne in lignes:
        cellules = cc.normaliser_couleurs_cellules(ligne.couleurs_cellules)
        for slug in slugs:
            if couleur:
                cellules[slug] = couleur
            else:
                cellules.pop(slug, None)
        ligne.couleurs_cellules = cellules
    Enregistrement.objects.bulk_update(lignes, ["couleurs_cellules"], batch_size=500)
    Bloc.ajouter_transaction(table.entreprise_id, {
        "type": "couleur_cellules", "table_id": table.id, "table": table.nom, "nb_lignes": len(lignes), "nb_colonnes": len(slugs),
        "agent_id": request.user.id, "couleur": couleur,
    })
    return JsonResponse({"couleur": couleur, "lignes": len(lignes), "colonnes": len(slugs)})
