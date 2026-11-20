"""Mise à jour d'une table par import CSV / Excel / Parquet / JSON : les lignes sont AJOUTÉES, rien n'est écrasé.
Réutilise l'import existant (lecture, types, insertion SQL/NoSQL) et le découpage en GroupeTables (limite de lignes)."""
from engine.i18n import t, tf
import io, re, urllib.parse, urllib.request
import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from . import import_fichier as imp, nosql_store, source_en_ligne
from .models import ConfigurationIA, GroupeTables, TableDynamique

_NUM = {"nombre", "decimal"}


def _similaire(type_fichier, type_table):   # données « au moins similaires » : même type, nombres entre eux, ou colonne texte
    return type_fichier == type_table or {type_fichier, type_table} <= _NUM or type_table == "texte"


def _nouvelle_partie(derniere, champs):   # découpe : table suivante du groupe (groupe créé à la volée si la table était seule)
    if not derniere.groupe_id:
        g = GroupeTables.objects.create(entreprise=derniere.entreprise, nom=derniere.nom, cree_automatiquement=True)
        TableDynamique.objects.filter(id=derniere.id).update(groupe=g, partie_index=1)
        derniere.refresh_from_db()
    if derniere.partie_index is None:   # groupe créé à la main (ou table détachée) : pas encore de numérotation, on la démarre au rang de la dernière table
        TableDynamique.objects.filter(id=derniere.id).update(partie_index=derniere.groupe.tables.count()); derniere.refresh_from_db()
    n = TableDynamique.objects.create(entreprise=derniere.entreprise, nom=f"{derniere.groupe.nom} ({derniere.partie_index + 1})"[:120],
        slug=imp._slug_table_unique(derniere.entreprise, f"{derniere.groupe.nom}_{derniere.partie_index + 1}"), groupe=derniere.groupe,
        partie_index=derniere.partie_index + 1, partie_precedente=derniere, type_stockage=derniere.type_stockage, url_source=derniere.url_source, jeton_source=derniere.jeton_source, jeton_a_reconfigurer=derniere.jeton_a_reconfigurer, intervalle_source=derniere.intervalle_source)
    imp._creer_champs_pour_table(n, [(c.nom, c.slug, c.type_champ) for c in champs])
    n.groupe.tables.update(partie_total=derniere.partie_index + 1)
    return n


def _parts(table):   # toutes les parties du groupe (ou la table seule)
    return table.groupe.tables.all() if table.groupe_id else TableDynamique.objects.filter(id=table.id)


def _deja_lues(table):   # lignes de la SOURCE déjà lues (compteur mémorisé : suppressions ou ajouts manuels dans la table ne décalent plus rien) ; anciennes tables : nombre de lignes présentes
    return table.lignes_source or sum(p.enregistrements.count() for p in _parts(table))


def _est_gsheet(url):   # vrai lien Google Sheet (hôte + chemin), pas un simple texte présent dans les paramètres d'un autre lien
    p = urllib.parse.urlsplit((url or "").strip())
    return p.hostname == "docs.google.com" and p.path.startswith("/spreadsheets")


def verifier_source(url, jeton=None):   # refus immédiat d'un lien invalide, avant de lancer l'import en tâche de fond
    url = (url or "").strip()
    if len(url) > 500: raise ValueError(t("maj_table.en_ligne_long"))
    if not _est_gsheet(url):
        from . import source_en_ligne
        source_en_ligne.verifier_lien(url, source_en_ligne._reglages()[0])
    elif not re.search(r"/d/([\w-]+)", url): raise ValueError(t("maj_table.lien"))
    if jeton:   # Online Sheet sécurisé : jeton bien formé, fichier en ligne (pas Google Sheet), HTTPS obligatoire
        from . import source_en_ligne
        source_en_ligne.verifier_jeton(jeton)
        if _est_gsheet(url): raise ValueError(t("maj_table.en_ligne_jeton_gsheet"))
        if urllib.parse.urlsplit(url).scheme != "https": raise ValueError(t("maj_table.en_ligne_jeton_https"))


def fichier_gsheet(url, table=None):   # lien Google Sheet → CSV, lu ensuite comme n'importe quel fichier téléversé ; avec `table` : seulement les lignes pas encore importées
    m, g = re.search(r"/d/([\w-]+)", url or ""), re.search(r"gid=(\d+)", url or "")
    if not m: raise ValueError(t("maj_table.lien"))
    with urllib.request.urlopen(f"https://docs.google.com/spreadsheets/d/{m[1]}/export?format=csv&gid={g[1] if g else 0}", timeout=30) as r: brut, csv = r.read(), r.headers.get_content_type() == "text/csv"
    if not csv: raise ValueError(t("maj_table.prive"))   # feuille non partagée : Google renvoie sa page de connexion
    total = None
    if table: df = pd.read_csv(io.BytesIO(brut), dtype=str, keep_default_na=False); total = len(df); brut = df.iloc[_deja_lues(table):].to_csv(index=False).encode()
    f = SimpleUploadedFile(f"gsheet_{m[1]}_{g[1] if g else 0}.csv", brut); f.lignes_source_total = total   # total de lignes de la source (None : import initial)
    return f


def fichier_en_ligne(url, table=None, jeton=None):   # « Online Sheet » : lien direct vers un CSV / Excel / Parquet (/ JSON) en ligne, lu ensuite comme un fichier téléversé ; avec `table` : seulement les lignes pas encore importées
    from . import source_en_ligne
    nom, brut = source_en_ligne.telecharger(url, jeton=jeton)
    fichier = SimpleUploadedFile(nom, brut)
    if table:   # même règle que Google Sheet : la source ne fait qu'ajouter des lignes à la fin, on saute celles déjà importées
        df = imp._lire_dataframe(fichier)
        fichier = SimpleUploadedFile(nom.rsplit(".", 1)[0] + ".csv", df.iloc[_deja_lues(table):].to_csv(index=False).encode("utf-8")); fichier.lignes_source_total = len(df)
    return fichier


def fichier_source(url, table=None, jeton=None):   # lien d'origine d'une table : Google Sheet (…/spreadsheets/d/…) ou fichier en ligne ; même contrat dans les deux cas
    url = (url or "").strip()
    if len(url) > 500: raise ValueError(t("maj_table.en_ligne_long"))   # taille du champ url_source
    if _est_gsheet(url):
        if jeton: raise ValueError(t("maj_table.en_ligne_jeton_gsheet"))   # le jeton ne vaut que pour un fichier en ligne (Online Sheet sécurisé)
        return fichier_gsheet(url, table)
    return fichier_en_ligne(url, table, jeton)


def maj_depuis_gsheet(table, url=None, jeton=None):   # sans `url` : relit le lien d'origine de la table (champ url_source : Google Sheet OU fichier en ligne) et ajoute les nouvelles lignes, avec la même découpe en groupe
    from . import source_en_ligne
    nouveau = bool(jeton)   # `jeton` : NOUVEAU jeton saisi par l'utilisateur (régénéré côté application distante) ; il remplace celui mémorisé dès que le serveur distant l'accepte
    if nouveau: jeton = source_en_ligne.verifier_jeton(jeton)
    elif not url and table.jeton_source:   # source protégée : jeton mémorisé CHIFFRÉ, déchiffré seulement ici, au moment de la requête
        jeton = source_en_ligne.dechiffrer_jeton(table.jeton_source)
        if jeton is None: _parts(table).update(jeton_a_reconfigurer=True); raise source_en_ligne.JetonRefuse(t("maj_table.en_ligne_jeton_illisible"))
    try: fichier = fichier_source(url or table.url_source, None if url else table, jeton)
    except source_en_ligne.JetonRefuse:
        if not nouveau: _parts(table).update(jeton_a_reconfigurer=True)   # le jeton mémorisé n'ouvre plus la source : à reconfigurer (relecture automatique suspendue)
        raise
    enregistrer = lambda: _parts(table).update(jeton_source=source_en_ligne.chiffrer_jeton(jeton), jeton_a_reconfigurer=False, **({"url_source": url} if url else {}))   # nouveau jeton accepté : mémorisé chiffré sur toutes les parties
    if nouveau and not url: enregistrer()   # même adresse : le jeton est bon dès que le téléchargement a réussi, même si l'import échoue ensuite
    nb = mettre_a_jour_table(table, fichier)
    if nouveau and url: enregistrer()   # autre adresse : mémorisée avec son jeton seulement si l'import a réussi
    elif jeton and table.jeton_a_reconfigurer: _parts(table).update(jeton_a_reconfigurer=False)   # le jeton mémorisé fonctionne de nouveau
    total = getattr(fichier, "lignes_source_total", None)
    if total is not None:
        table.refresh_from_db()   # un découpage a pu créer le groupe en base : l'instance en mémoire ne le connaît pas encore
        _parts(table).update(lignes_source=total)   # source relue jusqu'à cette ligne
    return nb


def mettre_a_jour_table(table, fichier, **lecture):
    df = imp._lire_dataframe(fichier, **lecture)
    champs = list(table.champs.order_by("ordre", "id")); cols = list(df.columns)
    par_slug = {imp._slugifier(str(c)): c for c in cols}
    if len(cols) != len(champs): raise ValueError(tf("maj_table.colonnes", v1=len(cols), v2=len(champs)))   # condition 1 : même nombre de colonnes
    if all(c.slug in par_slug for c in champs): cols = [par_slug[c.slug] for c in champs]                      # compatible : appariement par nom
    elif not all(_similaire(imp._inferer_type_colonne(df[c].astype(str)), ch.type_champ) for c, ch in zip(cols, champs)): raise ValueError(t("maj_table.donnees"))   # condition 2 : données similaires, appariement par position
    lim = TableDynamique.plafond_pour_entreprise(table.entreprise)   # toujours borné par le plafond absolu (100 000) : l'excédent crée la table suivante
    cible, i = (table.groupe.tables.order_by(F("partie_index").asc(nulls_first=True), "id").last() if table.groupe_id else table), 0
    with transaction.atomic():
        while i < len(df):
            place = (lim - cible.enregistrements.count()) if lim else len(df)
            if place <= 0: cible = _nouvelle_partie(cible, champs); continue
            part = df.iloc[i:i + place]; i += place
            inserer = nosql_store.inserer_lignes_dataframe if cible.est_nosql else imp._inserer_lignes_pandas   # les deux stockages
            inserer(cible, list(cible.champs.order_by("ordre", "id")), part, cols, len(part), None)
    return len(df)


@login_required
@require_http_methods(["POST"])
def api_maj_table(request, table_id):   # /api/tables/<id>/lignes/importer/ : déjà couvert par VuesAutoriseesMiddleware (« donnees », écritures)
    from .views._part_001 import _entreprise_courante, _interdit_lecteur
    refus = _interdit_lecteur(request)
    if refus: return refus
    table = get_object_or_404(TableDynamique, id=table_id, entreprise=_entreprise_courante(request))
    from . import tables_systeme
    refus = tables_systeme.refus_ecriture(table)
    if refus: return refus
    fichier = request.FILES.get("fichier")
    if request.POST.get("intervalle", "").isdigit():   # réglage de l'intervalle de relecture automatique (minutes, 0 = manuel)
        _parts(table).update(intervalle_source=int(request.POST["intervalle"])); return JsonResponse({"ok": True, "message": t("maj_table.intervalle")})
    jeton = "" if fichier else (request.POST.get("jeton") or "").strip()   # 🔑 : nouveau jeton d'accès d'un Online Sheet (adresse mémorisée ou nouvelle adresse)
    try:
        if jeton: verifier_source(request.POST.get("url") or table.url_source, jeton)   # jeton bien formé, HTTPS, pas un Google Sheet
        nb = mettre_a_jour_table(table, fichier, feuille=request.POST.get("feuille") or None, separateur=request.POST.get("separateur") or None, encodage=request.POST.get("encodage") or None) if fichier else maj_depuis_gsheet(table, request.POST.get("url"), jeton or None)
    except source_en_ligne.JetonRefuse as exc: return JsonResponse({"erreur": str(exc), "code": "jeton_a_reconfigurer"}, status=400)   # le front propose aussitôt de saisir un nouveau jeton
    except (ValueError, OSError) as exc: return JsonResponse({"erreur": str(exc)}, status=400)
    return JsonResponse({"ok": True, "lignes_ajoutees": nb, "message": tf("maj_table.jeton_ok" if jeton else "maj_table.ok", v1=nb)})
