"""
engine/ontologie_3d.py — Rendu 3D du graphe d'ontologie, entièrement côté
serveur, sans moteur 3D JavaScript.

La géométrie (sphères pour les nœuds, cylindres pour les arêtes) est
générée avec trimesh, positionnée selon une disposition 3D à ressorts
(networkx.spring_layout(dim=3), même bibliothèque déjà utilisée par
ontologie.py), puis rasterisée en PNG avec matplotlib (backend Agg,
aucun GPU/contexte GL requis — même principe que graphiques_bi.py et
reports.generer_pdf_dashboard_bi). Le client (ontologie.html) demande
simplement une nouvelle image à chaque rotation/zoom, via
views.vue_ontologie_3d — voir la barre de contrôle "🧊 3D" du template.

Dépendance à ajouter manuellement (absente du zip fourni, comme les
autres ajouts de requirements.txt) : `pip install trimesh`.
"""

import io

import numpy as np


def _disposition_3d(graphe):
    import networkx as nx
    if graphe.number_of_nodes() == 0:
        return {}
    pos = nx.spring_layout(graphe, dim=3, seed=42)
    return {n: np.asarray(p, dtype=float) * 4.0 for n, p in pos.items()}


def _hex_vers_rgb01(hexa):
    hexa = (hexa or "#888888").lstrip("#")
    if len(hexa) not in (6, 8):
        hexa = "888888"
    return [int(hexa[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def _alpha_hex(hexa, defaut):
    """Opacité d'une couleur « #rrggbbaa » (0 à 1) ; `defaut` si elle n'a pas de canal alpha."""
    h = (hexa or "").lstrip("#")
    return int(h[6:8], 16) / 255.0 if len(h) == 8 else defaut


def _sphere_polygones(centre, rayon):
    import trimesh
    m = trimesh.creation.icosphere(subdivisions=1, radius=max(rayon, 0.01))
    m.apply_translation(centre)
    return m.vertices[m.faces]


def _cylindre_polygones(p1, p2, rayon):
    import trimesh
    vecteur = p2 - p1
    longueur = float(np.linalg.norm(vecteur))
    if longueur < 1e-6:
        return None
    m = trimesh.creation.cylinder(radius=rayon, height=longueur, sections=8)
    z = np.array([0.0, 0.0, 1.0])
    direction = vecteur / longueur
    axe = np.cross(z, direction)
    if np.linalg.norm(axe) < 1e-6:
        axe = np.array([1.0, 0.0, 0.0])  # vecteur déjà aligné (ou opposé) à Z
    angle = float(np.arccos(np.clip(np.dot(z, direction), -1.0, 1.0)))
    if angle > 1e-6:
        import trimesh.transformations as tf
        m.apply_transform(tf.rotation_matrix(angle, axe))
    m.apply_translation(p1 + vecteur / 2)
    return m.vertices[m.faces]


def rendre_graphe_3d(graphe, filtre_type: str | None = None, azimut: float = 45.0,
                      elevation: float = 20.0, zoom: float = 1.0,
                      largeur: int = 1100, hauteur: int = 800) -> bytes:
    """Construit la scène 3D (trimesh) puis la rasterise (matplotlib Agg)
    en PNG. Retourne les octets bruts du PNG (pas de base64 — voir
    views.vue_ontologie_3d qui les sert directement en HttpResponse)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    from .ontologie import _COULEURS, _COULEURS_ARETES

    positions = _disposition_3d(graphe)
    fig = plt.figure(figsize=(largeur / 100, hauteur / 100), dpi=100, facecolor="#050505")
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor("#050505")
    ax.set_axis_off()
    zoom = max(zoom, 0.05)

    for u, v, attrs in graphe.edges(data=True):
        if u not in positions or v not in positions:
            continue
        couleur = attrs.get("couleur") or _COULEURS_ARETES.get(attrs.get("relation", ""), "#888888")
        polygones = _cylindre_polygones(positions[u], positions[v], rayon=0.02)
        if polygones is not None:
            ax.add_collection3d(Poly3DCollection(
                polygones, facecolor=_hex_vers_rgb01(couleur), linewidths=0, alpha=_alpha_hex(couleur, 0.55),
            ))

    for n, attrs in graphe.nodes(data=True):
        if n not in positions:
            continue
        if filtre_type and attrs.get("type") not in (filtre_type, "entreprise"):
            continue
        volume = attrs.get("nb_enregistrements", attrs.get("nb_lignes", 0)) or 0
        rayon = attrs.get("taille_3d") or (0.07 + min(volume, 200) ** 0.5 * 0.006)
        couleur = attrs.get("couleur") or _COULEURS.get(attrs.get("type", ""), "#888888")
        ax.add_collection3d(Poly3DCollection(
            _sphere_polygones(positions[n], rayon), facecolor=_hex_vers_rgb01(couleur), linewidths=0,
            alpha=_alpha_hex(couleur, None),
        ))

    if positions:
        toutes = np.array(list(positions.values()))
        centre = toutes.mean(axis=0)
        portee = max(1.0, float(np.abs(toutes - centre).max())) / zoom
        ax.set_xlim(centre[0] - portee, centre[0] + portee)
        ax.set_ylim(centre[1] - portee, centre[1] + portee)
        ax.set_zlim(centre[2] - portee, centre[2] + portee)
    ax.view_init(elev=elevation, azim=azimut)

    tampon = io.BytesIO()
    fig.savefig(tampon, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    tampon.seek(0)
    return tampon.read()
