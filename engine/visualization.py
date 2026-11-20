"""Les resultats graphiques ne sont pas forcement stockes : ils peuvent etre
recalcules a la demande. On les encode en base64 pour affichage direct."""
from engine.i18n import t
import base64
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from engine.graphiques_degrade import trace_degrade


def figure_vers_base64(figure) -> str:
    tampon = io.BytesIO()
    figure.savefig(tampon, format="png", bbox_inches="tight")
    plt.close(figure)
    tampon.seek(0)
    return base64.b64encode(tampon.read()).decode("ascii")


def generer_graphiques(specs_figures: list[dict]) -> list[dict]:
    """specs_figures : [{"type": "line"|"bar"|"scatter"|"hist", "x": [...], "y": [...], "titre": "..."}]"""
    graphiques = []
    for spec in specs_figures:
        fig, ax = plt.subplots(figsize=(6, 4))
        type_graphe = spec.get("type", "line")
        x = spec.get("x", [])
        y = spec.get("y", [])

        if type_graphe == "bar":
            ax.bar(x, y)
        elif type_graphe == "scatter":
            ax.scatter(x, y)
        elif type_graphe == "hist":
            ax.hist(y or x, bins=spec.get("bins", 20))
        else:
            trace_degrade(ax, x, y, "C0")   # courbe = évolution : dégradé de la couleur, du plus sombre au plus clair

        ax.set_title(spec.get("titre", ""))
        graphiques.append({
            "titre": spec.get("titre", ""),
            "image_base64": figure_vers_base64(fig),
        })
    return graphiques


# ── Fonctions fusionnées depuis gscode ──

def _points_valides(spec: dict) -> list:
    """Filtre spec["points"] : ne garde que les points avec lat/lon
    numeriques exploitables (silencieusement ignore le reste plutot que de
    planter toute la carte pour une valeur mal formee)."""
    points_bruts = spec.get("points", []) or []
    points = []
    for p in points_bruts:
        lat, lon = p.get("lat"), p.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            points.append({"lat": lat, "lon": lon, "libelle": str(p.get("libelle", "") or "")})
    return points



def _parse_geo(valeur):
    """Valeur d'un champ 'geolocalisation' -> (lat, lon, libelle) ou None si invalide. Formats : "lat, lon"
    (saisie manuelle, GPS, bouton navigateur) ou JSON/dict {lat, lon[, libelle]} (import, systeme externe ;
    latitude/longitude/lng admis). Source unique : validation (views) ET carte (tableau_bord_bi)."""
    import ast
    try:
        d = ast.literal_eval(valeur) if isinstance(valeur, str) and valeur.lstrip().startswith("{") else valeur
        if not isinstance(d, dict):
            d = dict(zip(("lat", "lon"), str(d).replace(";", ",").split(",")))
        lat, lon = float(d.get("lat", d.get("latitude"))), float(d.get("lon", d.get("lng", d.get("longitude"))))
    except (TypeError, ValueError, SyntaxError):
        return None
    return (lat, lon, str(d.get("libelle") or "")) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _generer_carte_geo_html(spec: dict, points: list, table_id=None) -> str:
    """Carte interactive via folium/Leaflet (module optionnel : sinon repli matplotlib, voir calculer_widget).
    Tuiles du serveur public OpenStreetMap ; chaque point propose « Itinéraire jusqu'ici » (openstreetmap.org/directions, voiture),
    « Partir d'ici » (choisit le départ ; le point passe en vert et devient l'origine des itinéraires suivants) et « Voir le point ».

    Points : liste [lat, lon, libelle] dessinee par une petite couche JS en L.circleMarker sur un rendu canvas
    (leger meme avec des milliers de points, aucun objet folium par ligne). AUCUN regroupement en pastilles : la
    carte garde les points individuels a tout niveau de zoom, l'utilisateur dezoome sans que la vue se resume.
    Seul le cadrage initial (fit_bounds) est automatique, donc un filtre recentre la carte sur les points restants.
    Panneau « Lieu et itinéraire » (haut droite, REPLIÉ au chargement : un bouton l'ouvre/le réduit, il se referme seul après un choix ou un clic sur la carte ; la recherche transmet la fenêtre visible pour classer d'abord les lieux proches) : recherche d'un lieu par son NOM (la page parente interroge le serveur et répond par postMessage :
    l'iframe est isolée, elle ne contacte jamais de service externe), « Je suis ici » / « Livrer ici », position actuelle mise à jour par GPS
    (polling 10 s), par clic sur la carte ou en saisissant « lat, lon » ; distance et cap restants vers la destination (ligne droite).
    Les libelles viennent des donnees utilisateur : echappes en HTML, serialises en JSON (« </ » neutralise) ;
    carte rendue dans un iframe sandbox."""
    import json
    import folium
    from folium.elements import MacroElement
    from html import escape
    from jinja2 import Template

    class _Points(MacroElement):
        _template = Template(t("engine/visualization.py:g:97:30"))

        def __init__(self, donnees, table_id=None):
            super().__init__()
            self._name = "PointsSansRegroupement"
            self.table_id_json = json.dumps(table_id if isinstance(table_id, int) else None)
            self.donnees_json = json.dumps(donnees).replace("</", "<\\/")

    carte = folium.Map(location=(0, 0), zoom_start=spec.get("zoom", 2), width="100%", height="100%", prefer_canvas=True)
    # La politique du site (same-origin) supprime le Referer : OpenStreetMap refuse alors les tuiles (fond blanc).
    carte.get_root().header.add_child(folium.Element('<meta name="referrer" content="strict-origin-when-cross-origin">'))
    if points:
        donnees = [[round(p["lat"], 6), round(p["lon"], 6), escape(p["libelle"])] for p in points]
        _Points(donnees, table_id).add_to(carte)
        lats, lons = [d[0] for d in donnees], [d[1] for d in donnees]
        carte.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], max_zoom=16)
    return (f'<iframe srcdoc="{escape(carte.get_root().render())}" sandbox="allow-scripts allow-popups '
            'allow-popups-to-escape-sandbox" allow="geolocation" referrerpolicy="strict-origin-when-cross-origin" style="width:100%;height:100%;border:0"></iframe>')


def _generer_carte_geo_image(spec: dict, points: list) -> str:
    """Repli SANS dependance supplementaire : matplotlib (deja requis pour
    tous les autres types de graphiques). Pas de fond de carte reel (pas de
    tuiles/geographie), juste les points places sur des axes gradues
    latitude/longitude — mais fonctionne immediatement, sans rien installer."""
    fig, ax = plt.subplots(figsize=(6, 5))
    if points:
        lons = [p["lon"] for p in points]
        lats = [p["lat"] for p in points]
        ax.scatter(lons, lats, c="#2e86de", s=60, zorder=3, edgecolors="white", linewidths=0.5)
        for p in (points if len(points) <= 30 else []):   # au-dela, les etiquettes se chevauchent (et coutent cher) : points seuls
            if p["libelle"]:
                ax.annotate(p["libelle"], (p["lon"], p["lat"]),
                             textcoords="offset points", xytext=(6, 6), fontsize=8)
        marge_lat = max((max(lats) - min(lats)) * 0.15, 0.5)
        marge_lon = max((max(lons) - min(lons)) * 0.15, 0.5)
        ax.set_xlim(min(lons) - marge_lon, max(lons) + marge_lon)
        ax.set_ylim(min(lats) - marge_lat, max(lats) + marge_lat)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(True, linestyle="--", alpha=0.4, zorder=0)
    ax.set_title(spec.get("titre", ""))
    return figure_vers_base64(fig)


