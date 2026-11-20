"""TRANSFERT DE STRUCTURE (clôture « modèle ») : logique pure, sans Django, donc testable seule.

Cas d'usage : je construis l'entreprise d'un client chez moi (tables, champs, formules, tableaux de bord, ontologies, configuration),
j'exporte la STRUCTURE sans aucune donnée, je lui envoie le zip ; en le restaurant il retrouve tout préconstruit, vide de données.
Contrairement à l'image fidèle (cycle.generer_zip_cloture_vers : tout, wallets compris, restauration à l'identique), l'import de structure
est ADDITIF : il n'efface rien chez le client, donne de nouveaux identifiants à tout ce qu'il crée (aucune collision avec ses objets) et
rattache le tout à SON entreprise.

`meta` décrit les modèles, construit par cycle.py à partir de Django :
  meta[étiquette] = {"fk": {champ: (étiquette_cible, nullable)}, "m2m": {champ: étiquette_cible}, "pk_int": bool}
avec des étiquettes en minuscules « app.modele » comme dans la sérialisation Django (ex. « engine.tabledynamique »).
"""
from engine.i18n import t as _gt, tf as _gtf, tfr as _gtfr, tl as _gtl  # noqa: E402,F401  # i18n-p7

# Modèles de STRUCTURE (liste blanche : un futur modèle de données n'est jamais exporté par mégarde).
STRUCTURELS = (
    "engine.groupetables", "engine.conteneurtables", "engine.tabledynamique", "engine.champdynamique", "engine.fusioncelluletable",
    "engine.formulecalcul", "engine.formulechamp", "engine.tablecalcul", "engine.lignecalcul",
    "engine.graphiquebi", "engine.tableaubordbi", "engine.widgetgraphiquebi", "engine.mesurewidget", "engine.indicateurkpi",
    "engine.coucheontologie", "engine.pageontologie", "engine.couleurontologie", "engine.couleurnoeudontologie",
    "engine.configurationia",
    "engine.modeleentete", "engine.modeledocument",   # modèles de documents administratifs (jamais les documents émis, ni les compteurs, ni les réglages d'émission)
)
ENTREPRISE = "engine.entreprise"
# Secrets de configuration : vidés dans l'export (le client saisit ses propres clés, les miennes ne partent jamais chez lui).
SECRETS = {"engine.configurationia": ("moteur_cle_api", "moteur_url_externe")}


def purger_secrets(objets):
    """Vide (en place) les champs secrets des objets de structure."""
    for o in objets:
        for champ in SECRETS.get(o["model"], ()):
            if champ in o["fields"]:
                o["fields"][champ] = ""
    return objets


def neutraliser(objets, meta, retenus=STRUCTURELS, comptes=()):
    """Garde les objets de structure ; une clé étrangère vers un objet non exporté (ligne, connexion externe, document généré…)
    est mise à None si le champ l'accepte, sinon l'objet est écarté, et ceux qui en dépendent à leur tour (jusqu'à stabilité).
    Les clés vers l'entreprise et vers les `comptes` (étiquettes, ex. le modèle utilisateur) sont conservées : `remapper` les réécrit.
    Renvoie (objets, écartés : liste d'étiquettes)."""
    gardes = [dict(o, fields=dict(o["fields"])) for o in objets if o["model"] in retenus]
    ecartes = []
    while True:
        presents = {(o["model"], o["pk"]) for o in gardes}
        suivant = []
        for o in gardes:
            m = meta.get(o["model"], {"fk": {}, "m2m": {}})
            ok = True
            for champ, (cible, nullable) in m["fk"].items():
                v = o["fields"].get(champ)
                if v is None or cible == ENTREPRISE or cible in comptes:
                    continue
                if cible not in retenus or (cible, v) not in presents:
                    if nullable:
                        o["fields"][champ] = None
                    else:
                        ok = False
            for champ, cible in m["m2m"].items():
                o["fields"][champ] = [v for v in (o["fields"].get(champ) or []) if (cible, v) in presents]
            if ok:
                suivant.append(o)
            else:
                ecartes.append(o["model"])
        if len(suivant) == len(gardes):
            return suivant, ecartes
        gardes = suivant


def remapper(objets, meta, entreprise_pk, premier_pk, substituts=None):
    """Nouveaux identifiants pour tous les objets (`premier_pk(étiquette)` = premier identifiant libre), clés étrangères et M2M réécrites.
    `substituts` : {étiquette_cible: pk} pour les clés vers l'entreprise et vers un compte (ex. le compte qui importe).
    Un modèle à clé primaire non entière garde ses clés (uuid). Renvoie la nouvelle liste (l'entrée n'est pas modifiée)."""
    substituts = dict(substituts or {})
    substituts[ENTREPRISE] = entreprise_pk
    carte, suivants = {}, {}
    for o in objets:
        etiq = o["model"]
        if meta.get(etiq, {}).get("pk_int", True):
            if etiq not in suivants:
                suivants[etiq] = premier_pk(etiq)
            carte[(etiq, o["pk"])] = suivants[etiq]
            suivants[etiq] += 1
        else:
            carte[(etiq, o["pk"])] = o["pk"]
    resultat = []
    for o in objets:
        m = meta.get(o["model"], {"fk": {}, "m2m": {}})
        champs = dict(o["fields"])
        for champ, (cible, nullable) in m["fk"].items():
            v = champs.get(champ)
            if v is None:
                continue
            if cible in substituts:
                champs[champ] = substituts[cible]
            elif (cible, v) in carte:
                champs[champ] = carte[(cible, v)]
            else:
                champs[champ] = None
        for champ, cible in m["m2m"].items():
            champs[champ] = [carte[(cible, v)] for v in (champs.get(champ) or []) if (cible, v) in carte]
        resultat.append({"model": o["model"], "pk": carte[(o["model"], o["pk"])], "fields": champs})
    return resultat


def rendre_uniques(objets, etiquette, champ, deja_pris, suffixe=_gtfr("engine/transfert_modele.py:106:65")):
    """Renomme (en place) les objets `etiquette` dont `champ` est déjà pris chez le client (ou répété dans l'archive) ; renvoie les changements."""
    pris, changes = set(deja_pris), []
    for o in objets:
        if o["model"] != etiquette:
            continue
        v = o["fields"].get(champ)
        if v is None:
            continue
        nouveau, i = v, 2
        while nouveau in pris:
            nouveau = f"{v}{suffixe}" if i == 2 else f"{v}{suffixe} {i}"
            if champ == "slug":
                nouveau = f"{v}_{i}"
            i += 1
        if nouveau != v:
            o["fields"][champ] = nouveau
            changes.append((v, nouveau))
        pris.add(nouveau)
    return changes


# ── Périmètre d'un agent (confinement) ───────────────────────────────────────
# Un agent ne transfère QUE la structure de son espace (ses tables autorisées) : jamais la configuration de l'entreprise
# (ConfigurationIA), ni les ontologies et leurs couleurs, ni les objets d'une table qui n'est pas la sienne.
# Les modèles dont un champ désigne la table à laquelle ils appartiennent (la clé étrangère « propriétaire ») :
PROPRIETAIRE_TABLE = {
    "engine.champdynamique": "table", "engine.fusioncelluletable": "table", "engine.formulechamp": "table",
    "engine.tablecalcul": "table_source", "engine.tableaubordbi": "table_source", "engine.indicateurkpi": "table_source",
}
# Dépendent d'un objet propriétaire (leur sort est décidé par la cascade de `neutraliser`).
DEPENDANTS_ESPACE = ("engine.lignecalcul", "engine.widgetgraphiquebi", "engine.mesurewidget")
# Rangements et objets d'entreprise gardés SEULEMENT s'ils sont référencés par un objet de l'espace.
LIBRES_ESPACE = ("engine.groupetables", "engine.conteneurtables", "engine.graphiquebi", "engine.formulecalcul")
TABLE = "engine.tabledynamique"
RETENUS_ESPACE = (TABLE,) + tuple(PROPRIETAIRE_TABLE) + DEPENDANTS_ESPACE + LIBRES_ESPACE


def restreindre_a_tables(objets, meta, tables_pks, comptes=()):
    """Ne garde que la structure de l'ESPACE défini par `tables_pks` (identifiants de tables).
    Garde : ces tables ; ce qui leur appartient (champs, fusions, formules de champ, tables de calcul, tableaux de bord et KPI dont la
    table source est l'une d'elles) ; ce qui en dépend (lignes de calcul, widgets, mesures) tant que tout ce dont ils dépendent est gardé ;
    et, parmi les objets d'entreprise, seulement ceux qu'un objet gardé référence (formule, graphique, groupe de tables) ou que
    regroupe un conteneur contenant au moins une table gardée. Écarte tout le reste : configuration IA, ontologies, couleurs,
    objets des autres tables. Un lien vers une table hors espace (ex. champ relation) est mis à None. Renvoie (objets, écartés)."""
    tables_pks = set(tables_pks)
    base, libres = [], []
    for o in objets:
        m = o["model"]
        if m == TABLE:
            if o["pk"] in tables_pks:
                base.append(o)
        elif m in PROPRIETAIRE_TABLE:
            if o["fields"].get(PROPRIETAIRE_TABLE[m]) in tables_pks:
                base.append(o)
        elif m in DEPENDANTS_ESPACE:
            base.append(o)
        elif m in LIBRES_ESPACE:
            libres.append(o)
    # Passe 1 : tous les objets libres sont admis, pour que la cascade décide du sort des objets de l'espace.
    passe1, _ = neutraliser(base + libres, meta, retenus=RETENUS_ESPACE, comptes=comptes)
    surs = [o for o in passe1 if o["model"] not in LIBRES_ESPACE]
    presents = {(o["model"], o["pk"]) for o in surs}
    refs = set()
    for o in surs:
        for champ, (cible, _nul) in meta.get(o["model"], {"fk": {}})["fk"].items():
            v = o["fields"].get(champ)
            if v is not None and cible in LIBRES_ESPACE:
                refs.add((cible, v))
    gardes_libres = [o for o in libres if (o["model"], o["pk"]) in refs]
    # Un conteneur est gardé s'il regroupe au moins une table (ou un groupe) gardée ; ses autres membres seront retirés par `neutraliser`.
    deja = {(o["model"], o["pk"]) for o in gardes_libres}
    for o in libres:
        if o["model"] != "engine.conteneurtables" or (o["model"], o["pk"]) in deja:
            continue
        for champ, cible in meta.get(o["model"], {"m2m": {}})["m2m"].items():
            if any((cible, v) in presents or (cible, v) in deja for v in (o["fields"].get(champ) or [])):
                gardes_libres.append(o)
                break
    resultat, ecartes = neutraliser(surs + gardes_libres, meta, retenus=RETENUS_ESPACE, comptes=comptes)
    return resultat, ecartes
