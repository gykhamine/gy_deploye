"""Documents administratifs — liste (recherche, filtres) et export en lot (ZIP).

  * `filtrer`      : numéro / titre, statut, type, période, archives — appliqués à un queryset DÉJÀ restreint aux
                     documents que l'utilisateur a le droit de voir (la visibilité n'est jamais décidée ici) ;
  * `exporter_zip` : les PDF FIGÉS des documents émis ou annulés (jamais régénérés, empreinte recontrôlée) + un index CSV.
"""
import csv
import io
import re
import zipfile
from datetime import date

from .emission_documents import ErreurEmission, MAX_EXPORT, _m, octets_figes

STATUTS = ("brouillon", "soumis", "emis", "annule")
LIMITE_DEFAUT, LIMITE_MAX = 200, 500


def _date(texte):
    try:
        return date.fromisoformat(str(texte or "").strip())
    except ValueError:
        return None


def filtrer(qs, params):
    """`params` : un QueryDict (ou dict) — q, statut, type, du, au, archive (« 0 » par défaut : hors archives ; « 1 » :
    archives seulement ; « tout »), ids (liste d'identifiants séparés par des virgules). Une valeur inconnue est ignorée."""
    from django.db.models import Q
    q = " ".join(str(params.get("q") or "").split())[:100]
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(titre__icontains=q) | Q(nom__icontains=q))
    statut = params.get("statut")
    if statut in STATUTS:
        qs = qs.filter(statut=statut)
    type_document = str(params.get("type") or "").strip()
    if type_document:
        qs = qs.filter(type_document=type_document)
    # Période : date d'émission pour un document émis, sinon date de création.
    du, au = _date(params.get("du")), _date(params.get("au"))
    if du:
        qs = qs.filter(Q(date_emission__gte=du) | Q(date_emission__isnull=True, cree_le__date__gte=du))
    if au:
        qs = qs.filter(Q(date_emission__lte=au) | Q(date_emission__isnull=True, cree_le__date__lte=au))
    archive = str(params.get("archive") or "0")
    if archive == "1":
        qs = qs.filter(archive=True)
    elif archive != "tout":
        qs = qs.filter(archive=False)
    ids = [int(x) for x in re.findall(r"\d+", str(params.get("ids") or ""))][:LIMITE_MAX]
    if ids:
        qs = qs.filter(id__in=ids)
    return qs


def limite(params):
    try:
        return max(1, min(int(params.get("limite") or LIMITE_DEFAUT), LIMITE_MAX))
    except ValueError:
        return LIMITE_DEFAUT


def _nom_fichier(d, pris):
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", d.reference or "").strip("-")[:80] or f"document-{d.pk}"
    nom = base
    if nom in pris:
        nom = f"{base}_{d.pk}"
    pris.add(nom)
    return f"{nom}.pdf"


def exporter_zip(documents):
    """(octets du ZIP, nombre de PDF, nombre d'anomalies). Seuls les documents émis ou annulés sont exportés ; un PDF dont
    l'empreinte ne correspond plus n'est PAS ajouté : il figure dans l'index avec la mention d'anomalie."""
    documents = [d for d in documents if d.statut in ("emis", "annule")]
    if not documents:
        raise ErreurEmission(_m("export_vide"), "vide")
    if len(documents) > MAX_EXPORT:
        raise ErreurEmission(_m("export_trop", v1=MAX_EXPORT), "trop_de_documents")
    tampon, index, pris, anomalies = io.BytesIO(), io.StringIO(), set(), 0
    ecrivain = csv.writer(index, delimiter=";")
    ecrivain.writerow(["reference", "titre", "type", "statut", "date_emission", "lieu", "sha256", "fichier", "anomalie"])
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for d in documents:
            fichier, anomalie = "", ""
            try:
                octets = octets_figes(d)
                fichier = _nom_fichier(d, pris)
                archive.writestr(fichier, octets)
            except ErreurEmission as exc:
                anomalie, anomalies = str(exc), anomalies + 1
            ecrivain.writerow([d.reference, d.titre, d.type_document, d.statut,
                               d.date_emission.isoformat() if d.date_emission else "", d.lieu, d.empreinte_sha256,
                               fichier, anomalie])
        archive.writestr("index.csv", "\ufeff" + index.getvalue())      # BOM : s'ouvre correctement dans Excel
    return tampon.getvalue(), len(documents) - anomalies, anomalies
