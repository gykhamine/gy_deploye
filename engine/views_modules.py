"""Page « Modules » : l'annuaire des modules d'analyse et de pilotage d'une table, avec l'explication de chacun.

La barre d'onglets de la table a disparu : il ne reste qu'un bouton « Modules ». Toutes les vues y sont rangées :
les vues de travail (Dashboard, Données, Documents, Calcul) puis les modules d'analyse et de pilotage. Aucune donnée n'est lue ni modifiée : la page ne fait que lister et renvoyer vers les pages existantes,
lesquelles contrôlent elles-mêmes les droits. Un agent ne voit que les modules que son profil l'autorise à ouvrir (vues_autorisees).

  GET /tables/<id>/modules/
  GET /modules/[?table=<id ou slug>]   point d'entrée de l'icône « Modules » de l'en-tête (pages qui n'ont pas de table dans leur adresse)

Chez le client (rôle « lecteur ») la page se limite à sa réalité : seules les pages d'analyse que l'agent lui a laissées ouvertes
(wallet.PAGES_CLIENT moins pages_client_fermees), sur une table qui lui est exposée. Rien d'autre n'est listé : ni données, ni
documents, ni calcul, ni tâches, ni IA, ni blockchain, qu'il ne pourrait pas ouvrir.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from .i18n import t
from .models import MembreEntreprise, PageOntologie, TableDynamique
from .views import (_entreprise_courante, _interdit_table_non_exposee, _table_autorisee_agent, _table_permise_client,
                    _table_via_partage, _vue_autorisee)

# (identifiant, pictogramme, vue de VUES_DISPONIBLES, adresse ; {id} = identifiant de la table ouverte, {slug} = son slug)
GROUPES = [
    ("vues", [
        ("dashboard", "📊", "dashboard", "/tables/{slug}/?vue=dashboard"),
        ("donnees", "🗂️", "donnees", "/tables/{slug}/?vue=donnees"),
        ("documents", "🧾", "documents", "/tables/{slug}/?vue=documents"),
        ("calcul", "🧮", "calcul", "/tables/{slug}/?vue=calcul"),
    ]),
    ("analyse", [
        ("dashboard_bi", "📈", "dashboard_bi", "/tables/{id}/dashboard-bi/"),
        ("statistiques", "🧮", "statistiques", "/tables/{id}/statistiques/"),
        ("anova", "📐", "anova", "/tables/{id}/anova/"),
        ("analyse_factorielle", "🧭", "analyse_factorielle", "/tables/{id}/analyse-factorielle/"),
        ("series_temporelles", "📉", "series_temporelles", "/tables/{id}/series-temporelles/"),
        ("analyse_media", "🎞️", "analyse_media", "/tables/{id}/analyse-media/"),
        ("geolocalisation", "🌍", "geolocalisation", "/tables/{id}/geolocalisation/"),
    ]),
    ("confiance", [
        ("ontologie", "🕸️", "ontologie", "/ontologie/"),
        ("blockchain", "⛓️", "blockchain", "/blockchain/?table={id}"),
    ]),
    ("automatisation", [
        ("taches", "⏱", "taches", "/taches/"),
        ("entrainement_ia", "🧠", "ia", "/ia/"),
    ]),
]


def _pages_ouvertes_client(entreprise):
    """Pages d'analyse qu'un client peut ouvrir : toutes celles de PAGES_CLIENT que l'agent n'a pas fermées ;
    l'ontologie n'y compte que si au moins une de ses pages est mise à disposition des clients."""
    from .wallet import PAGES_CLIENT, pages_client_fermees
    ouvertes = set(PAGES_CLIENT) - pages_client_fermees(entreprise)
    if "ontologie" in ouvertes and not PageOntologie.objects.filter(entreprise=entreprise, visible_client=True).exists():
        ouvertes.discard("ontologie")
    return ouvertes


def construire_groupes(table_id, membre=None, slug="", entreprise=None):
    """[{titre, modules: [{id, icone, titre, description, url}]}] dans la langue courante, limité aux vues autorisées.
    Pour un client (lecteur) : uniquement les pages d'analyse ouvertes, sans les vues de travail de l'agent."""
    client = bool(membre and membre.role == "lecteur" and entreprise is not None)
    ouvertes = _pages_ouvertes_client(entreprise) if client else None
    vus = set()
    sortie = []
    for gid, modules in GROUPES:
        if client and gid == "vues":   # vues de travail de l'agent (données, documents, calcul) : le client n'y a pas accès ; son tableau de bord est dans « analyse »
            continue
        liste = []
        for mid, ico, vue, url in modules:
            if not _vue_autorisee(membre, vue):
                continue
            if client and (vue not in ouvertes or url in vus):
                continue
            vus.add(url)
            liste.append({"id": mid, "icone": ico, "url": url.format(id=table_id, slug=slug),
                          "titre": t(f"engine/views_modules.py:{mid}:titre"), "description": t(f"engine/views_modules.py:{mid}:desc")})
        if liste:
            sortie.append({"id": gid, "titre": t(f"engine/views_modules.py:groupe_{gid}"), "modules": liste})
    return sortie


def _membre(request, entreprise):
    return MembreEntreprise.objects.filter(utilisateur=request.user, entreprise=entreprise).first()


def _tables_pour_modules(entreprise, membre):
    """Tables (générateur, par nom) sur lesquelles la personne peut ouvrir la page Modules.
    Client : les tables que l'agent lui expose, jamais la table des agents et clients (même règle que son espace).
    Agent : ses tables autorisées, hors tables système. Paresseux : l'icône de l'en-tête n'a besoin que de la première."""
    qs = TableDynamique.objects.filter(entreprise=entreprise).order_by("nom")
    if membre and membre.role == "lecteur":
        qs = qs.filter(Q(expose_client=True) | Q(masquer_formulaire_client=True)).exclude(est_table_membres=True)
        return (x for x in qs if _table_permise_client(membre, x))
    return (x for x in qs if not getattr(x, "source_systeme", "") and _table_autorisee_agent(membre, x))


def table_modules_client(entreprise, membre):
    """Table sur laquelle s'ouvre la page Modules d'un client, ou None s'il n'a rien à y trouver
    (aucune table exposée, ou toutes les pages d'analyse fermées par l'agent) : l'icône est alors masquée."""
    if not _pages_ouvertes_client(entreprise):
        return None
    return next(_tables_pour_modules(entreprise, membre), None)


def icone_modules_visible(request, entreprise=None):
    """True si l'icône « Modules » doit figurer dans l'en-tête de la personne connectée."""
    if not request.user.is_authenticated:
        return False
    entreprise = entreprise or _entreprise_courante(request)
    membre = _membre(request, entreprise)
    if membre and membre.role == "lecteur":
        return table_modules_client(entreprise, membre) is not None
    return True


@login_required
@ensure_csrf_cookie
def vue_modules(request, table_id):
    entreprise = _entreprise_courante(request)
    table, lecture_seule = _table_via_partage(request, table_id)
    refus = None if lecture_seule else _interdit_table_non_exposee(request, table)
    if refus:
        return refus
    membre = _membre(request, entreprise)
    return render(request, "engine/modules.html", {"table": table, "groupes": construire_groupes(table.id, membre, table.slug, entreprise),
                                                   "client": bool(membre and membre.role == "lecteur")})


@login_required
def vue_modules_courant(request):
    """Entrée « Modules » de l'icône de l'en-tête : ouvre la page Modules de la table courante.
    ?table=<id ou slug> (la table de la page ou la dernière ouverte, mémorisée par le navigateur) ; si elle est absente, inconnue ou
    interdite à la personne, la première de ses tables. Aucune table utilisable : retour à l'accueil (client : son espace)."""
    entreprise = _entreprise_courante(request)
    membre = _membre(request, entreprise)
    client = bool(membre and membre.role == "lecteur")
    if client and table_modules_client(entreprise, membre) is None:
        return redirect("engine:espace_client")
    souhait = (request.GET.get("table") or "").strip()
    tables = list(_tables_pour_modules(entreprise, membre))
    choisie = next((x for x in tables if souhait and (str(x.id) == souhait or x.slug == souhait)), None) or (tables[0] if tables else None)
    if choisie is None:
        return redirect("engine:espace_client" if client else "/")
    return redirect("engine:vue_modules", table_id=choisie.id)
