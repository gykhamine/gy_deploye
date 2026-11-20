"""GÉOSPATIAL — archivage des positions anciennes : la table ne garde que le récent, rien n'est perdu.

La table géospatiale vit dans le temps. Pour qu'elle reste rapide (cartes, analyses, surveillance), elle ne conserve que les LIGNES RÉCENTES du
flux (réglage `GEO_LIGNES_RECENTES`, 5000 par défaut, 0 = pas de limite). Les lignes plus anciennes ne sont JAMAIS supprimées sans trace :
elles sont d'abord écrites dans des fichiers d'archive, puis retirées de la table seulement si l'écriture a réussi.

Fichiers : `<MEDIA_ROOT>/systeme/archives_geo/<entreprise>/<table>/AAAA-MM-JJ.jsonl.gz` (jour de l'archivage), une ligne JSON par pas de temps
{"cle": "flux:<ms>", "archive_le": ISO, "valeurs": {slug_colonne: valeur}}, plus `colonnes.json` (slug -> nom de colonne, à jour). Le format est
ouvert (gzip + JSON lignes) : lisible sans l'application, ajout en fin de fichier (membres gzip successifs), consultable par l'API
`/api/tables/<id>/geolocalisation/archives/` (filtres date, objet) ou téléchargeable tel quel pour une enquête.

Seules les lignes du flux (idem_key « flux:… ») sont concernées, jamais une ligne saisie à la main. Le noyau (ecrire / lire) n'importe pas Django.
"""
from __future__ import annotations
from engine.i18n import t as _i18n_t

import gzip
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from engine.i18n import t as _gt  # noqa: E402

try:
    from .stockage import dossier_systeme
except ImportError:   # module chargé hors du paquet (tests isolés) : engine/ est alors sur sys.path
    from stockage import dossier_systeme

logger = logging.getLogger(__name__)

GARDER_PAR_DEFAUT = 5000
MARGE = 0.10          # on n'archive qu'au-delà de +10 % : pas un petit fichier à chaque vidage du flux
LOT_ARCHIVE = 1000    # lignes lues/supprimées par passage (mémoire bornée)
NOM_FICHIER = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl\.gz$")


# ── Noyau pur ───────────────────────────────────────────────────────────────
def ecrire(dossier: Path, lignes: list, colonnes: dict | None = None, maintenant: datetime | None = None) -> Path:
    """Ajoute `lignes` [{"cle", "valeurs"}] au fichier du jour. Lève OSError si l'écriture échoue (l'appelant ne supprime alors rien)."""
    maintenant = maintenant or datetime.now(timezone.utc)
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{maintenant:%Y-%m-%d}.jsonl.gz"
    horodatage = maintenant.isoformat()
    corps = "".join(json.dumps({"cle": l["cle"], "archive_le": horodatage, "valeurs": l["valeurs"]}, ensure_ascii=False, default=str) + "\n"
                    for l in lignes)
    with open(chemin, "ab") as f:   # membre gzip supplémentaire : les archives déjà écrites ne sont jamais réécrites
        f.write(gzip.compress(corps.encode("utf-8")))
        f.flush()
        os.fsync(f.fileno())
    if colonnes:
        tmp = dossier / "colonnes.json.tmp"
        actuel = {}
        try:
            actuel = json.loads((dossier / "colonnes.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        actuel.update(colonnes)
        tmp.write_text(json.dumps(actuel, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, dossier / "colonnes.json")
    return chemin


def fichiers(dossier: Path) -> list[dict]:
    dossier = Path(dossier)
    if not dossier.is_dir():
        return []
    return [{"nom": p.name, "octets": p.stat().st_size} for p in sorted(dossier.iterdir()) if NOM_FICHIER.match(p.name)]


def _ms(cle: str):
    try:
        return int(str(cle).split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def lire(dossier: Path, du_ms: int | None = None, au_ms: int | None = None, objet: str | None = None, limite: int = 1000) -> dict:
    """Lignes archivées dont l'instant du pas (clé « flux:<ms> ») est dans [du_ms, au_ms] ; `objet` (slug de colonne) ne garde que les pas
    où cet objet a une position. Tri chronologique. {"lignes": [...], "total_trouve": n, "tronque": bool}."""
    trouvees = []
    for f in fichiers(dossier):
        try:
            with gzip.open(Path(dossier) / f["nom"], "rt", encoding="utf-8") as fh:
                for ligne in fh:
                    try:
                        d = json.loads(ligne)
                    except ValueError:
                        continue   # ligne abîmée : on lit le reste
                    t = _ms(d.get("cle"))
                    if t is None or (du_ms is not None and t < du_ms) or (au_ms is not None and t > au_ms):
                        continue
                    if objet and d.get("valeurs", {}).get(objet) in (None, ""):
                        continue
                    trouvees.append((t, d))
        except (OSError, EOFError):
            logger.warning(_gt("rg8.msg.047"), f["nom"])
    trouvees.sort(key=lambda x: x[0])
    limite = max(1, int(limite))
    return {"lignes": [d for _, d in trouvees[:limite]], "total_trouve": len(trouvees), "tronque": len(trouvees) > limite}


# ── Branchement Django ──────────────────────────────────────────────────────
def dossier_table(table) -> Path:
    from django.conf import settings
    return dossier_systeme("archives_geo", str(table.entreprise_id), str(table.id))


def garder() -> int:
    from django.conf import settings
    try:
        return max(int(getattr(settings, "GEO_LIGNES_RECENTES", GARDER_PAR_DEFAUT)), 0)
    except (TypeError, ValueError):
        return GARDER_PAR_DEFAUT


def archiver_valeurs(table, lignes: list) -> bool:
    """Archive des lignes brutes [{"cle", "valeurs"}] (pas encore en base). True si tout est écrit."""
    if not lignes:
        return True
    try:
        ecrire(dossier_table(table), lignes, {c.slug: c.nom for c in table.champs.all()})
        return True
    except OSError:
        logger.exception(_i18n_t("engine/geo_archives.py:g:125:26"), table.id)
        return False


def archiver_et_retirer(table, ids: list) -> int:
    """Écrit les enregistrements `ids` dans l'archive PUIS les supprime ; si l'écriture échoue, ne supprime rien. Renvoie le nombre retiré."""
    from .models import Enregistrement
    retires = 0
    for i in range(0, len(ids), LOT_ARCHIVE):
        lot = ids[i:i + LOT_ARCHIVE]
        enrs = list(Enregistrement.objects.filter(id__in=lot, table=table).select_related("table").prefetch_related("valeurs__champ").order_by("id"))
        lignes = [{"cle": e.idem_key, "valeurs": {k: v for k, v in e.to_dict().items() if k not in ("matricule", "code_acces_client")}} for e in enrs]
        if not archiver_valeurs(table, lignes):
            break
        Enregistrement.objects.filter(id__in=[e.id for e in enrs]).delete()
        retires += len(enrs)
    return retires


def conserver_recents(table) -> int:
    """Au-delà de GEO_LIGNES_RECENTES (+ marge), archive puis retire les plus anciennes lignes du flux. Renvoie le nombre archivé."""
    limite = garder()
    if not limite:
        return 0
    flux = table.enregistrements.filter(idem_key__startswith="flux:")
    total = flux.count()
    if total <= limite * (1 + MARGE):
        return 0
    ids = list(flux.order_by("id").values_list("id", flat=True)[:total - limite])
    return archiver_et_retirer(table, ids)
