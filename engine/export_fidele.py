"""EXPORT ET RESTAURATION FIDÈLES D'UNE TABLE.

Problème résolu : un export CSV/Excel est un fichier « à plat » (tout en texte, décimales françaises, « Oui/Non », listes jointes par des
virgules...). À la réimportation, les types étaient re-devinés : un champ de géolocalisation redevenait du texte, un choix unique un texte
libre, une relation un nombre, les couleurs, formules, options et ordre des colonnes disparaissaient.

Principe : le fichier exporté par la plateforme porte son propre MANIFESTE (version, schéma complet de la table et des champs, VALEURS BRUTES
telles que stockées, métadonnées de ligne). La restauration lit ce manifeste et ne devine rien. Trois supports, même contenu :
  - `.gytable.json` : le manifeste en clair (bouton « Exporter la table ») ;
  - `.xlsx` de la grille : feuille lisible + feuille CACHÉE `_gy_meta` portant le manifeste compressé (zlib + base64, 30 000 caractères
    par cellule, sous la limite d'Excel). 
  - `.parquet` : la grille en colonnes typées (nombres en nombres) + le manifeste compressé dans les métadonnées du fichier (clés
    `gy_meta_n`, `gy_meta_0`…). Un Parquet venu d'ailleurs n'a pas ces clés : il est importé comme un CSV (colonnes et types déduits).
Un fichier Excel venu d'ailleurs n'a pas cette feuille : il reste importé comme avant (types devinés).
Le manifeste est scellé par une EMPREINTE (SHA-256 du schéma et des valeurs) : un fichier modifié à la main est refusé plutôt que restauré faux.

Deux cas à l'importation d'un manifeste (voir `resume`) :
  - TABLE NATIVE : la table décrite existe encore dans l'entreprise (même slug) → on peut la RESTAURER SUR PLACE (mode « remplacer » : mêmes
    identifiants de ligne quand c'est possible, donc les relations d'autres tables restent valides) ou en créer une COPIE (mode « copie ») ;
  - TABLE INDÉPENDANTE : elle n'existe pas ici (autre entreprise, autre installation, table supprimée) → recréée à l'identique, nouvelle table.
Un JSON ordinaire (sans manifeste) n'est pas un export de la plateforme : il passe par l'import ordinaire (colonnes déduites des clés).

Les valeurs sont copiées SANS interprétation (ValeurChamp.valeur telle quelle ; valeurs_json pour le NoSQL) et créées en `bulk_create` : aucun
signal (formules recalculées, synchronisations, notifications) ne les modifie au passage. Les identifiants de ligne changent forcément (clé
primaire) ; les relations d'une table vers elle-même sont réécrites vers les nouveaux identifiants.
"""
from engine.i18n import t, tf
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7
import base64
import hashlib
import io
import json
import uuid
import zlib

FORMAT = "gyplatform-table"
VERSION = 1
FEUILLE_META = "_gy_meta"
CLE_META_N = b"gy_meta_n"            # Parquet : nombre de tronçons du manifeste (métadonnées du fichier)
CLE_META_PREFIXE = b"gy_meta_"       # Parquet : gy_meta_0, gy_meta_1… = tronçons du manifeste (même découpage que la feuille cachée d'Excel)
TAILLE_CELLULE = 30000

CHAMPS_TABLE = ("nom", "slug", "description", "type_stockage", "taille_echantillon", "prompt_rapport", "prompt_rapport_document",
                "couleurs_retirees", "timeout_execution_secondes", "timeout_inference_secondes", "expose_client",
                "masquer_formulaire_client", "matricule_prefixe", "provient_fichier", "nom_fichier_origine", "config_carte")
CHAMPS_CHAMP = ("nom", "slug", "type_champ", "obligatoire", "options_choix", "relation_champ_affichage", "relation_colonne_source",
                "relation_bidirectionnelle", "ordre", "couleur", "formule_expression", "formule_etapes_json")


def empreinte(manifeste):
    """SHA-256 du contenu qui compte (schéma + valeurs) : toute modification à la main change l'empreinte."""
    utile = {"table": manifeste.get("table"), "champs": manifeste.get("champs"), "lignes": manifeste.get("lignes")}
    return hashlib.sha256(json.dumps(utile, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


# ── Export ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def construire(table, enregistrements, slugs_masques=()):
    """Manifeste de `table` pour les `enregistrements` fournis (queryset ou liste). `slugs_masques` : champs interdits à l'exportateur
    (sécurité fine) — leurs valeurs ne sortent pas et le manifeste le signale."""
    from django.utils import timezone
    from .models import ValeurChamp

    masques = set(slugs_masques or ())
    champs = list(table.champs.select_related("relation_vers", "groupe_partage").order_by("ordre", "id"))
    liste = list(enregistrements)
    ids = [e.id for e in liste]
    brut = {i: {} for i in ids}
    if not table.est_nosql:
        for debut in range(0, len(ids), 5000):
            for eid, slug, valeur in ValeurChamp.objects.filter(enregistrement_id__in=ids[debut:debut + 5000]).values_list("enregistrement_id", "champ__slug", "valeur"):
                if slug not in masques:
                    brut[eid][slug] = valeur
    lignes = []
    for e in liste:
        valeurs = brut[e.id] if not table.est_nosql else {k: v for k, v in (e.valeurs_json or {}).items() if k not in masques}
        lignes.append({"id": e.id, "matricule": e.matricule, "code_acces_client": e.code_acces_client, "couleur": e.couleur,
                       "couleurs_cellules": e.couleurs_cellules or {}, "cree_le": e.cree_le.isoformat(), "maj_le": e.maj_le.isoformat(),
                       "valeurs": valeurs})
    manifeste = {
        "format": FORMAT, "version": VERSION, "exporte_le": timezone.now().isoformat(),
        "table": {k: getattr(table, k) for k in CHAMPS_TABLE},
        "champs": [dict({k: getattr(c, k) for k in CHAMPS_CHAMP},
                        relation_vers=(c.relation_vers.slug if c.relation_vers_id else None),
                        groupe_partage=(c.groupe_partage.nom if c.groupe_partage_id and hasattr(c.groupe_partage, "nom") else None))
                   for c in champs if c.slug not in masques],
        "lignes": lignes,
        "avertissements": ([tf("engine/export_fidele.py:g:80:29", v1=', '.join(sorted(masques)))] if masques else []),
    }
    manifeste["empreinte"] = empreinte(manifeste)
    return manifeste


def en_json(manifeste):
    return json.dumps(manifeste, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def en_cellules(manifeste):
    """Manifeste compressé et découpé pour la feuille cachée d'Excel (une chaîne par cellule)."""
    texte = base64.b64encode(zlib.compress(json.dumps(manifeste, ensure_ascii=False, default=str).encode("utf-8"), 9)).decode("ascii")
    return [texte[i:i + TAILLE_CELLULE] for i in range(0, len(texte), TAILLE_CELLULE)] or [""]


# ── Lecture ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def lire(fichier, ext):
    """Manifeste contenu dans `fichier` (objet fichier, extension sans point) ou None si ce n'est pas un export de la plateforme.
    Lève ValueError si c'en est un mais qu'il a été modifié ou qu'il vient d'une version plus récente."""
    ext = (ext or "").lower().lstrip(".")
    manifeste = None
    try:
        if ext == "json":
            fichier.seek(0)
            manifeste = json.loads(fichier.read().decode("utf-8-sig"))
        elif ext == "parquet":
            # Manifeste porté par les métadonnées du fichier (clés gy_meta_n et gy_meta_0…), même contenu que la feuille cachée d'Excel.
            import pyarrow.parquet as pq
            fichier.seek(0)
            meta = pq.ParquetFile(io.BytesIO(fichier.read())).schema_arrow.metadata or {}
            n = int(meta.get(CLE_META_N, b"0") or 0)
            if n:
                texte = "".join(meta[CLE_META_PREFIXE + str(i).encode()].decode("ascii") for i in range(n))
                manifeste = json.loads(zlib.decompress(base64.b64decode(texte)).decode("utf-8"))
        elif ext == "xlsx":
            from openpyxl import load_workbook
            fichier.seek(0)
            classeur = load_workbook(fichier, read_only=True, data_only=True)
            if FEUILLE_META in classeur.sheetnames:
                cellules = [str(r[0]) for r in classeur[FEUILLE_META].iter_rows(values_only=True) if r and r[0]]
                manifeste = json.loads(zlib.decompress(base64.b64decode("".join(cellules))).decode("utf-8"))
            classeur.close()
    except Exception:   # pas un export de la plateforme (CSV, Excel étranger, JSON quelconque) : import ordinaire
        manifeste = None
    finally:
        try:
            fichier.seek(0)
        except Exception:
            pass
    if not isinstance(manifeste, dict) or manifeste.get("format") != FORMAT:
        return None
    if int(manifeste.get("version") or 0) > VERSION:
        raise ValueError(t("engine/export_fidele.py:g:124:26"))
    if manifeste.get("empreinte") != empreinte(manifeste):
        raise ValueError(t("engine/export_fidele.py:g:126:26"))
    return manifeste


# ── Table native ou indépendante ────────────────────────────────────────────────────────────────────────────────────
MODES = ("copie", "remplacer")


def trouver_native(entreprise, manifeste):
    """Table EXISTANTE de l'entreprise que ce manifeste décrit (même slug) — la « table native » restaurable sur place — ou None
    (table indépendante : elle n'existe pas ici)."""
    from .models import TableDynamique
    slug = (manifeste.get("table") or {}).get("slug")
    return TableDynamique.objects.filter(entreprise=entreprise, slug=slug).first() if slug else None


def resume(entreprise, manifeste):
    """Ce que l'interface montre avant d'importer : la table décrite et, si elle existe encore ici, la table native à restaurer."""
    decrit = manifeste.get("table") or {}
    native = trouver_native(entreprise, manifeste)
    return {
        "nom": decrit.get("nom"), "slug": decrit.get("slug"), "exporte_le": manifeste.get("exporte_le"),
        "nb_lignes": len(manifeste.get("lignes") or []), "nb_champs": len(manifeste.get("champs") or []),
        "existante": ({"id": native.id, "nom": native.nom, "nb_lignes": native.enregistrements.count()} if native else None),
    }


# ── Restauration ────────────────────────────────────────────────────────────────────────────────────────────────────
def _nom_libre(entreprise, nom):
    from .models import TableDynamique
    candidat, i = nom, 2
    while TableDynamique.objects.filter(entreprise=entreprise, nom=candidat).exists():
        candidat = _gtf("engine/export_fidele.py:173:20", v1=(nom), v2=('' if i == 2 else ' ' + str(i)))
        i += 1
    return candidat


def _slug_libre(entreprise, slug):
    from .models import TableDynamique
    candidat, i = slug, 2
    while TableDynamique.objects.filter(entreprise=entreprise, slug=candidat).exists():
        candidat = f"{slug}_{i}"
        i += 1
    return candidat


def restaurer(entreprise, manifeste, progression_cb=None, mode="copie"):
    """`mode="copie"` (défaut) : crée une NOUVELLE table. `mode="remplacer"` : restaure SUR PLACE la table native (même slug) en remplaçant
    ses lignes ; sans table native, retombe sur la création (avertissement). Recrée la table du manifeste dans `entreprise` : mêmes champs (types, options, ordre, couleurs, formules, relations), mêmes valeurs
    brutes, mêmes matricules, codes d'accès, couleurs de ligne et dates. Renvoie la TableDynamique créée (nom et slug rendus uniques si la
    table d'origine existe encore). Un matricule ou un code d'accès déjà pris dans l'entreprise est régénéré (jamais deux clients avec le même
    code) : `table.avertissements_restauration` le dit."""
    from django.db import transaction
    from django.utils.dateparse import parse_datetime
    from django.db.models import Max
    from .models import ChampDynamique, Enregistrement, TableDynamique, ValeurChamp

    def _prog(p):
        if progression_cb:
            try:
                progression_cb(int(p))
            except Exception:
                pass

    avertissements = list(manifeste.get("avertissements") or [])
    decrit = manifeste["table"]
    nosql = decrit.get("type_stockage") == "nosql"
    if mode not in MODES:
        mode = "copie"
    with transaction.atomic():
        native = trouver_native(entreprise, manifeste) if mode == "remplacer" else None
        if mode == "remplacer" and native is None:
            avertissements.append(t("engine/export_fidele.py:g:200:35"))
        if native is not None and (native.type_stockage == "nosql") != nosql:
            raise ValueError(t("engine/export_fidele.py:g:202:30"))
        sur_place = native is not None
        if sur_place:
            table = native
            for k in CHAMPS_TABLE:
                v = decrit.get(k)
                if v is not None and k not in ("nom", "slug"):
                    setattr(table, k, v)
            table.save()
        else:
            table = TableDynamique(entreprise=entreprise)
            for k in CHAMPS_TABLE:
                v = decrit.get(k)
                if v is not None:
                    setattr(table, k, v)
            table.nom = _nom_libre(entreprise, decrit.get("nom") or t("engine/export_fidele.py:g:218:69"))[:120]
            table.slug = _slug_libre(entreprise, decrit.get("slug") or "table_restauree")
            table.save()
        champs = {}
        for d in manifeste["champs"]:
            c = (ChampDynamique.objects.filter(table=table, slug=d["slug"]).first() if sur_place else None) or ChampDynamique(table=table)
            for k in CHAMPS_CHAMP:
                if d.get(k) is not None:
                    setattr(c, k, d[k])
            cible = d.get("relation_vers")
            if cible:
                c.relation_vers = table if cible == decrit.get("slug") else TableDynamique.objects.filter(entreprise=entreprise, slug=cible).first()
                if c.relation_vers is None:
                    avertissements.append(tf("engine/export_fidele.py:g:231:43", v1=d['nom'], v2=cible))
            c.save()
            champs[d["slug"]] = c
        ids_fichier = [d.get("id") for d in manifeste["lignes"]]
        conserver_ids = False
        if sur_place:
            en_plus = table.champs.exclude(slug__in=list(champs)).count()
            if en_plus:
                avertissements.append(tf("engine/export_fidele.py:g:239:39", v1=en_plus))
            max_id = Enregistrement.objects.aggregate(m=Max("id"))["m"] or 0
            anciens = list(table.enregistrements.values_list("id", flat=True))
            for i in range(0, len(anciens), 500):
                Enregistrement.objects.filter(id__in=anciens[i:i + 500]).delete()
            # Mêmes identifiants de ligne si possible : les relations des AUTRES tables vers celle-ci restent alors valides.
            conserver_ids = (all(isinstance(i, int) for i in ids_fichier) and len(set(ids_fichier)) == len(ids_fichier)
                             and (not ids_fichier or max(ids_fichier) <= max_id)
                             and not any(Enregistrement.objects.filter(id__in=ids_fichier[i:i + 500]).exists() for i in range(0, len(ids_fichier), 500)))
            if not conserver_ids and ids_fichier:
                avertissements.append(t("engine/export_fidele.py:g:249:39"))
        _prog(15)

        pris_mat = set(Enregistrement.objects.filter(table__entreprise=entreprise).exclude(matricule="").values_list("matricule", flat=True))
        pris_code = set(Enregistrement.objects.filter(table__entreprise=entreprise).exclude(code_acces_client="").values_list("code_acces_client", flat=True))
        regen = 0
        lignes = manifeste["lignes"]
        uids, a_creer = [], []
        for d in lignes:
            mat, code = d.get("matricule") or "", d.get("code_acces_client") or ""
            if mat and mat in pris_mat:
                mat, regen = "", regen + 1
            if code and code in pris_code:
                code, regen = "", regen + 1
            e = Enregistrement(table=table, matricule=mat, code_acces_client=code, couleur=d.get("couleur") or "",
                               **({"id": d["id"]} if conserver_ids else {}),
                               couleurs_cellules=d.get("couleurs_cellules") or {}, uid=uuid.uuid4(),
                               valeurs_json=(d.get("valeurs") if nosql else None))
            uids.append(e.uid)
            a_creer.append(e)
            pris_mat.add(mat) if mat else None
            pris_code.add(code) if code else None
        for i in range(0, len(a_creer), 2000):
            Enregistrement.objects.bulk_create(a_creer[i:i + 2000])
        nouveaux = dict(Enregistrement.objects.filter(uid__in=uids).values_list("uid", "id"))
        correspondance = {d["id"]: nouveaux[u] for d, u in zip(lignes, uids)}
        _prog(45)

        # Dates d'origine (cree_le/maj_le sont automatiques : on les réécrit après coup, sans passer par save()).
        a_dater = []
        for d, u in zip(lignes, uids):
            e = Enregistrement(id=nouveaux[u])
            e.cree_le, e.maj_le = parse_datetime(d.get("cree_le") or ""), parse_datetime(d.get("maj_le") or "")
            if e.cree_le and e.maj_le:
                a_dater.append(e)
        for i in range(0, len(a_dater), 2000):
            Enregistrement.objects.bulk_update(a_dater[i:i + 2000], ["cree_le", "maj_le"])

        if not nosql:
            auto_relation = {s for s, c in champs.items() if c.relation_vers_id == table.id}
            valeurs = []
            for d in lignes:
                eid = correspondance[d["id"]]
                for slug, v in (d.get("valeurs") or {}).items():
                    if slug not in champs:
                        continue
                    if slug in auto_relation and v not in (None, ""):
                        v = ",".join(str(correspondance.get(int(x), x)) if str(x).strip().isdigit() else str(x) for x in str(v).split(","))
                    valeurs.append(ValeurChamp(enregistrement_id=eid, champ=champs[slug], valeur=v))
            for i in range(0, len(valeurs), 5000):
                ValeurChamp.objects.bulk_create(valeurs[i:i + 5000])
                _prog(45 + 50 * min(1, (i + 5000) / max(1, len(valeurs))))
            from . import parquet_store
            parquet_store.planifier_synchro(table)   # fichier Parquet de la table restaurée
        else:
            try:
                from . import nosql_store
                nosql_store.synchroniser_fichier(table)
            except Exception:
                avertissements.append(t("engine/export_fidele.py:g:306:39"))
    try:   # trace de la restauration dans la chaîne de l'entreprise (best-effort : ne bloque jamais la restauration)
        from .models import Bloc
        from .securite_dates import DateRetrograde
        Bloc.ajouter_transaction(entreprise.id, {"type": "restauration_table", "table_id": table.id, "table": table.nom,
                                                 "mode": "remplacer" if sur_place else "copie", "lignes": len(manifeste["lignes"])})
    except DateRetrograde:
        raise
    except Exception:
        pass
    if regen:
        avertissements.append(tf("engine/export_fidele.py:g:314:31", v1=regen))
    table.avertissements_restauration = avertissements
    _prog(100)
    return table
