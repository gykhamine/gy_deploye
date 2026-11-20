"""engine/actions_fichiers.py — 4 actions fichier pour l'agent (base64 / ids).
Aucun fichier existant touché. Délègue aux fonctions actions.py déjà vérifiées.
"""
from __future__ import annotations
from engine.i18n import tl
import base64, io, json, zipfile
from .actions import declarer_action


def _b64_file(b64, nom, ct="image/png"):
    from django.core.files.uploadedfile import InMemoryUploadedFile
    data = base64.b64decode(b64)
    return InMemoryUploadedFile(io.BytesIO(data), "file", nom, ct, len(data), None)


def _uploader_logo_base64(entreprise, contenu_b64, nom_fichier="logo.png", content_type="image/png"):
    from .actions import _uploader_logo
    return _uploader_logo(entreprise, _b64_file(contenu_b64, nom_fichier, content_type))


def _configurer_carte_base64(table, fichiers_b64=None, champs_texte=None, config_carte_json=None):
    from .actions import _configurer_carte_table
    fichiers = {k: _b64_file(v["b64"], v.get("nom", k), v.get("type", "image/png"))
                for k, v in (fichiers_b64 or {}).items()}
    return _configurer_carte_table(table, fichiers, champs_texte or {},
                                   json.loads(config_carte_json) if config_carte_json else None)


def _champs_enreg(table, ligne_ids, slugs):
    from .models import Enregistrement, ChampDynamique
    enreg = list(Enregistrement.objects.filter(table=table, pk__in=ligne_ids))
    qs = ChampDynamique.objects.filter(table=table, **({"slug__in": slugs} if slugs else {}))
    return enreg, {c.slug: c for c in qs}


def _generer_cartes_agent(table, ligne_ids, champs_slugs=None):
    from .reports import generer_pdf_cartes_agents
    enreg, champs = _champs_enreg(table, ligne_ids, champs_slugs)
    return generer_pdf_cartes_agents(table, champs, enreg, table.config_carte)


def _generer_cartes_zip_agent(table, ligne_ids, champs_slugs=None):
    from .reports import generer_pdf_cartes_agents
    enreg, champs = _champs_enreg(table, ligne_ids, champs_slugs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, i in enumerate(range(0, len(enreg), 10), 1):
            zf.writestr(f"cartes_{table.slug}_p{n}.pdf",
                        generer_pdf_cartes_agents(table, champs, enreg[i:i+10], table.config_carte))
    return buf.getvalue()


declarer_action("uploader_logo_base64",
    tl("engine/actions_fichiers.py:g:53:5"),
    role_minimum="admin", types_objet=("entreprise",))(_uploader_logo_base64)

declarer_action("configurer_carte_base64",
    tl("engine/actions_fichiers.py:g:57:5"),
    role_minimum="admin", types_objet=("table",))(_configurer_carte_base64)

declarer_action("generer_cartes_agent",
    tl("engine/actions_fichiers.py:g:61:5"),
    role_minimum="admin", types_objet=("table",))(_generer_cartes_agent)

declarer_action("generer_cartes_zip_agent",
    tl("engine/actions_fichiers.py:g:65:5"),
    role_minimum="admin", types_objet=("table",))(_generer_cartes_zip_agent)
