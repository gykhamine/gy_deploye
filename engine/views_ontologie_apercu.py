"""RECHERCHE DANS TOUTE L'ONTOLOGIE (page d'ontologie en aperçu hiérarchique, voir ontologie_apercu.py).

  POST /api/ontologie/apercu/recherche/   corps {texte, page_id?}  →  {resultats: [{id, label, type}], total}
  POST /api/ontologie/apercu/voisins/     corps {noeud, page_id?}  →  {voisins: [{id, label, type, relation, sens}], total}
      Options de navigation d'un nœud : TOUS ses voisins et leur relation, même ceux qui ne sont pas dans l'aperçu (lus ici, dans l'état complet,
      sans aucun rendu). Un voisin hors aperçu devient la racine du nouvel aperçu au clic, la trace se poursuivant (voir ontologie_apercu.py).

La page ne charge que 200 nœuds au plus ; un nom absent de l'aperçu est donc cherché ici, dans l'état complet construit en arrière-plan.
Si le cache ne contient pas l'élément (créé depuis la dernière construction), l'état est recalculé à la demande, comme « recharger », puis la
recherche est refaite. Choisir un résultat recharge la page avec ?racine=<id> : ce nœud devient la racine et l'aperçu part de lui. Mêmes droits, mêmes pages et même
restriction client que la page d'ontologie (aucun nœud interdit n'est jamais renvoyé).
"""
from engine.i18n import t
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .models import MembreEntreprise, PageOntologie
from .views import _entreprise_courante, _vue_autorisee


@login_required
@require_http_methods(["POST"])
def api_ontologie_apercu_recherche(request):
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not _vue_autorisee(membre, "dashboard_bi"):
        return JsonResponse({"erreur": t("engine/views_ontologie_apercu.py:interdit")}, status=403)
    try:
        params = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views_ontologie_apercu.py:json")}, status=400)
    texte = str(params.get("texte") or "").strip()
    if len(texte) < 2:
        return JsonResponse({"resultats": [], "total": 0})

    from . import ontologie as _onto
    from . import ontologie_apercu as _apercu
    page, entreprise_graphe, partage = None, entreprise, False
    page_id = params.get("page_id")
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()
        if page is None:   # page d'une autre entreprise, mise à disposition en lecture seule
            from .views import _partage_actif
            autre = PageOntologie.objects.filter(id=page_id).first()
            if autre and _partage_actif(entreprise, "ontologie", autre.id):
                page, entreprise_graphe, partage = autre, autre.entreprise, True
            elif membre and membre.role == "lecteur":
                return JsonResponse({"resultats": [], "total": 0}, status=403)
    if page is not None and page.mode == "manuel":
        graphe = _onto.construire_graphe_manuel(page, entreprise_graphe)
    else:
        membre_filtre = membre if (not partage and membre and membre.role in ("agent", "lecteur")) else None
        graphe = _onto.consulter_graphe(entreprise_graphe, page=page, membre=membre_filtre)
        if graphe is None:   # pas encore construit : rien à chercher, la page affiche déjà son pop-up
            return JsonResponse({"resultats": [], "total": 0, "pas_pret": True})
    libelle = lambda a, n: _onto.libelle_noeud(a, n)
    graphe = _onto.restreindre_pour_client(graphe, entreprise_graphe, membre)

    def chercher(g):
        # 1) CELLULES d'abord : chaque valeur de table est un nœud (ontologie_cellules.py) — chercher un nom tombe d'abord sur ce nom ;
        # 2) puis les libellés des nœuds (tables, colonnes, lignes par matricule…). Une cellule exacte passe avant tout le reste.
        cellules = []
        try:
            cellules = _apercu.rechercher_cellules(g, texte, _lectures_tables(g, entreprise_graphe), libelle=libelle)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("recherche ontologie : lecture des cellules impossible")
        noeuds = _apercu.rechercher_noeuds(g, texte, libelle=libelle)
        exactes = [r for r in cellules if str(r["label"]).strip().lower() == texte.strip().lower()]
        autres_cellules = [r for r in cellules if r not in exactes]
        # Un nœud dont le libellé est exactement le texte (ex. la ligne nommée par cette valeur) double la cellule : la cellule reste première.
        resultats = exactes + [r for r in noeuds if str(r["label"]).strip().lower() == texte.strip().lower()] + autres_cellules
        deja = {r["id"] for r in resultats}
        resultats += [r for r in noeuds if r["id"] not in deja]
        return resultats[:_apercu.LIMITE_RECHERCHE]

    # Aucun recalcul ici : l'état est entretenu en arrière-plan (synchro toutes les ~3 s) et reconstruire toute l'ontologie dans cette requête
    # figeait la page. (« demander_recalcul » n'est pas non plus une option : il efface l'état courant, la page redevient « pas prête ».)
    resultats = chercher(graphe)
    return JsonResponse({"resultats": resultats, "total": len(resultats)})


def _lectures_tables(graphe, entreprise):
    """(DataFrame avec `_id`, {slug: nom}, nom de la table) de chaque table dynamique présente dans le graphe, lue dans son fichier Parquet (une table physique,
    jamais le groupe). Les tables système et NoSQL sont ignorées, comme pour les autres analyses de valeurs de l'ontologie (lecture lente)."""
    from . import parquet_store
    from .models import TableDynamique
    ids = [int(n.split(":", 1)[1]) for n in graphe.nodes if isinstance(n, str) and n.startswith("table:") and n.split(":", 1)[1].isdigit()]
    for table in TableDynamique.objects.filter(entreprise=entreprise, id__in=ids).prefetch_related("champs"):
        if table.est_nosql or getattr(table, "source_systeme", ""):
            continue
        df = parquet_store.donnees_analytiques(table, groupe=False, avec_id=True)
        yield df, {c.slug: c.nom for c in table.champs.all()}, (getattr(table, "nom", "") or table.slug)


@login_required
@require_http_methods(["POST"])
def api_ontologie_apercu_voisins(request):
    """Voisins complets d'un nœud (aperçu hiérarchique) : mêmes droits, pages et restriction client que la recherche ; aucun nœud interdit n'est renvoyé."""
    entreprise = _entreprise_courante(request)
    membre = MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()
    if not _vue_autorisee(membre, "dashboard_bi"):
        return JsonResponse({"erreur": t("engine/views_ontologie_apercu.py:interdit")}, status=403)
    try:
        params = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"erreur": t("engine/views_ontologie_apercu.py:json")}, status=400)
    noeud = str(params.get("noeud") or "").strip()
    if not noeud or len(noeud) > 300:
        return JsonResponse({"voisins": [], "total": 0})

    from . import ontologie as _onto
    from . import ontologie_apercu as _apercu
    from . import ontologie_cellules as _cell
    page, entreprise_graphe, partage = None, entreprise, False
    page_id = params.get("page_id")
    if page_id:
        page = PageOntologie.objects.filter(id=page_id, entreprise=entreprise).first()
        if page is None:   # page d'une autre entreprise, mise à disposition en lecture seule
            from .views import _partage_actif
            autre = PageOntologie.objects.filter(id=page_id).first()
            if autre and _partage_actif(entreprise, "ontologie", autre.id):
                page, entreprise_graphe, partage = autre, autre.entreprise, True
            elif membre and membre.role == "lecteur":
                return JsonResponse({"voisins": [], "total": 0}, status=403)
    if page is not None and page.mode == "manuel":
        graphe = _onto.construire_graphe_manuel(page, entreprise_graphe)
    else:
        membre_filtre = membre if (not partage and membre and membre.role in ("agent", "lecteur")) else None
        graphe = _onto.consulter_graphe(entreprise_graphe, page=page, membre=membre_filtre)
        if graphe is None:   # pas encore construit : la page affiche déjà son pop-up
            return JsonResponse({"voisins": [], "total": 0, "pas_pret": True})
    graphe = _onto.restreindre_pour_client(graphe, entreprise_graphe, membre)
    if noeud not in graphe:   # une cellule n'est pas un nœud du graphe de base : ses voisins sont ceux de sa ligne
        ligne = _cell.ligne_de(noeud)
        noeud = ligne if ligne and ligne in graphe else noeud
    voisins, total = _apercu.voisins_complets(graphe, noeud, libelle=lambda a, n: _onto.libelle_noeud(a, n))
    return JsonResponse({"voisins": voisins, "total": total})
