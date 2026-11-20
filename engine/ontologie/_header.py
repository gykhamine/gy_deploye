"""
engine/ontologie.py — Graphe d'ontologie des données de l'entreprise.

Ceci n'est PAS un agent : c'est une cartographie, construite à la volée
depuis l'état réel de la base, de la structure ET du comportement des
données d'une entreprise — tables, relations effectivement utilisées
(volume de valeurs renseignées, pas seulement le schéma), configuration
IA, tables de calcul, skills générés. Le graphe est calculé à partir des
modèles Django existants, puis gardé dans un fichier d'état que le serveur
resynchronise en arrière-plan toutes les quelques secondes : l'ouverture de
la page relit cet état au lieu de tout recalculer (voir ontologie/_part_002.py,
« Fichier d'état du graphe »).

networkx est déjà une dépendance du projet (voir la liste des modules
autorisés dans engine/executor.py, canal "graphique_bi").
"""

from __future__ import annotations
from engine.i18n import td
from engine.i18n import tl

import logging

import networkx as nx

logger = logging.getLogger("gyplatform.ontologie")

# Palette régénérée pour maximiser l'écart perceptuel entre les 42 types de
# nœuds (l'ancienne palette avait des groupes de couleurs trop proches —
# ex. admin/agent, document/enregistrement, ou tout le cluster jaune/orange
# de la gestion de tables). Principe : 7 familles réparties en arc-en-ciel
# (rouge=danger, orange/jaune=flux, vert=succès, cyan=opération, bleu=
# structure, violet=IA/agent) + 3 gris neutres différenciés uniquement par
# luminosité — chaque membre d'une famille varie en teinte ET en luminosité/
# saturation, jamais seulement en teinte. Distance perceptuelle minimale
# entre deux couleurs quelconques du set : ~48 (contre ~24 avant).
_COULEURS = {
    # neutres (gris, différenciés par luminosité)
    "document":            "#505662",
    "enregistrement":      "#788191",
    "cellule":             "#f2c14e",
    "archive":             "#abb0ba",
    # danger (rouge)
    "rejet":               "#c11540",
    "suppression":         "#d85a6a",
    "purge":               "#fa8585",
    "alerte_gestion":      "#e63319",
    "rupture_chaine":      "#e0947b",
    # flux (orange/jaune)
    "client":              "#c17c15",
    "partage":             "#d8b05a",
    "permission":          "#fadf85",
    "tokens":              "#e6c719",
    "sauvegarde":          "#e0d97b",
    "bloc":                "#cace09",
    "transaction":         "#daeb47",
    # attention (jaune-vert)
    "corbeille":           "#71c115",
    "verrou":              "#8dd85a",
    "quota_stockage":      "#a6fa85",
    "brouillon":           "#3ae619",
    "doublon":             "#7ee07b",
    # succès (vert)
    "validation":          "#15c14c",
    "soumission":          "#5ad88e",
    "restauration":        "#85fac1",
    "preuve_authenticite": "#19e697",
    "import_donnees":      "#7be0c3",
    "export_donnees":      "#09cead",
    # opération (cyan)
    "calcul":              "#15a1c1",
    "config_ia":           "#5ab1d8",
    "kpi":                 "#85c7fa",
    "dashboard":           "#1974e6",
    "autorite_validation": "#7b9be0",
    # structure (bleu)
    "entreprise":          "#1b15c1",
    "table":               "#6e5ad8",
    "champ":               "#a685fa",
    "version_table":       "#6d19e6",
    "modele_table":        "#b17be0",
    # IA / agent (violet)
    "agent":               "#b015c1",
    "admin":               "#d85ad8",
    "skill":               "#fa85ee",
    "modele_ia":           "#e619bd",
    "chaine_blocs":        "#e07bc2",
    "tache_gestion":       "#ce097f",
}

_COULEURS_ARETES = {
    # Palette régénérée (même principe que _COULEURS ci-dessus) : 10
    # familles réparties en arc-en-ciel, chaque relation d'une famille
    # variant en teinte ET en luminosité/saturation (jamais une seule
    # dimension) — 111 relations, 111 couleurs strictement distinctes,
    # écart perceptuel minimal ~24 (contre des doublons exacts avant).
    # ── danger (suppression/corbeille/restauration/purge/archive) ──
    "declenche_suppression": "#b30f2d",
    "suppression_table": "#d22d46",
    "suppression_ligne": "#fa4259",
    "suppression_champ": "#e1989f",
    "envoie_corbeille": "#c41c28",
    "corbeille_contient": "#ee2f37",
    "corbeille_de": "#e2787a",
    "restaure": "#fc9d9c",
    "restauration_de": "#96302c",
    "purge_corbeille": "#df2d20",
    "purge_ligne": "#f05c4c",
    "purge_par": "#e89d92",
    "archive_de": "#db2706",
    "archive_par": "#cd6851",
    # ── commerce (commandes, comportement client) ──
    "commande": "#f96b06",
    "traite": "#d39969",
    "achete": "#eebc8b",
    "consulte": "#d07511",
    "abandonne": "#d89846",
    "recurrence_client": "#fbbd60",
    "fidelite": "#e8d4b0",
    "parraine": "#aa7a18",
    "segment_comportemental": "#ecad13",
    "escalade": "#ddbd5f",
    "declenche": "#fce07e",
    # ── attention (brouillon/verrou/quota) ──
    "brouillon_de": "#e4e755",
    "brouillon_schema": "#edf584",
    "verrou_pose_par": "#a7b927",
    "verrou_table": "#d4f924",
    "verrou_ligne": "#c6da81",
    "quota_entreprise": "#ddf2a6",
    "quota_table": "#7db30f",
    "alerte_quota": "#93d22d",
    # ── analytics (similarité, mesure, tendance, dashboards) ──
    "co_occurrence": "#b3e892",
    "similarite": "#52db06",
    "sim_champ": "#7acd51",
    "frequence": "#96eb70",
    "affiche": "#b9f7a1",
    "mesure": "#42a022",
    "consomme": "#3df906",
    "operation": "#7ed369",
    "tendance": "#9cee8b",
    "anomalie": "#2dd011",
    "structure_proche": "#58d846",
    "quasi_doublon": "#6ffb60",
    "derive_dashboard": "#b4e8b0",
    "renouvelle": "#1eaa18",
    "pic_activite": "#17ec13",
    # ── soumission (formulaire → validation/rejet) ──
    "soumet": "#32ae4d",
    "cible_soumission": "#3be365",
    "genere_enregistrement": "#68f390",
    "instruit_soumission": "#abedc1",
    "valide": "#05bd48",
    "valide_soumission": "#39c671",
    "rejette": "#55e795",
    "rejet_de": "#84f5ba",
    "motif_rejet": "#27b972",
    "ecrit_dans": "#24f999",
    "depend_de": "#81dab5",
    # ── ia_calcul (IA, calcul, génération) ──
    "calcule": "#2feecb",
    "champ_calcul": "#78e2d4",
    "genere": "#9cfcf4",
    "entraine": "#2c9691",
    "inference": "#20dfdf",
    "configure": "#4ce9f0",
    "produit": "#92e0e8",
    "emploie": "#06bddb",
    "identifie": "#51b6cd",
    # ── structure (identité, liens de base) ──
    "authentifie": "#60bafb",
    "possede": "#b0cee8",
    "lien": "#185eaa",
    "lien_effectif": "#1370ec",
    "colonne": "#5f8edd",
    "meme_nom": "#7ea6fc",
    "contient": "#3254ae",
    "valeur": "#f2c14e",
    "valeur_de_colonne": "#e08e2b",
    "meme_valeur": "#ff6b6b",
    "cycle_vie": "#3b5fe3",
    # ── gestion_table (versions, modèles, imports/exports, sauvegardes, tâches) ──
    "version_de": "#a7a6f2",
    "precede_version": "#160fb3",
    "cree_depuis_modele": "#392dd2",
    "modele_propose_par": "#5542fa",
    "import_vers": "#a298e1",
    "import_lance_par": "#371cc4",
    "import_echec_champ": "#532fee",
    "export_depuis": "#8f78e2",
    "export_demande_par": "#b39cfc",
    "sauvegarde_de": "#482c96",
    "sauvegarde_lancee_par": "#5920df",
    "tache_assignee": "#824cf0",
    "tache_concerne": "#b092e8",
    "doublon_de": "#5706db",
    # ── gestion_acces (partage, permissions, délégation) ──
    "partage": "#7022a0",
    "autorise": "#a506f9",
    "gere": "#b269d3",
    "supervise": "#d38bee",
    "collabore": "#a211d0",
    "concurrence": "#bb46d8",
    "delegue": "#e260fb",
    "remplace": "#e1b0e8",
    "partage_widget": "#9c18aa",
    "type_partage": "#e013ec",
    "type_compatible": "#db5fdd",
    # ── blockchain ──
    "precede_bloc": "#c639a7",
    "appartient_chaine": "#e755c1",
    "ancre_chaine": "#f584d3",
    "valide_bloc": "#b92788",
    "contient_transaction": "#f924a8",
    "porte_sur": "#da81b5",
    "opere_transaction": "#f2a6cf",
    "emet_preuve": "#b30f61",
    "beneficie_preuve": "#d22d78",
    "rompt_entre": "#fa428f",
}

# Nombre maximum d'enregistrements individuels ajoutés au graphe PAR TABLE
# (les plus récents), valeur par défaut UNIQUEMENT si l'entreprise n'a pas
# encore de ConfigurationIA (cas limite) — le réglage effectif et éditable
# vit sur ConfigurationIA.limite_enregistrements_ontologie (même principe
# que TableDynamique.LIMITE_LIGNES_DEFAUT/ConfigurationIA.limite_lignes) :
# un nœud par ligne exploserait la taille du graphe sur une grosse table, on
# se limite donc par défaut à un échantillon récent — assez pour voir le
# comportement, jamais la table entière.
LIMITE_ENREGISTREMENTS_GRAPHE = 0   # 0 = illimité : TOUTES les lignes de chaque table entrent dans le graphe (seul l'affichage est borné, voir ontologie_apercu)


DESCRIPTIONS_TYPES = {
    "entreprise":  tl("engine/ontologie/_header.py:g:228:20"),
    "table":       tl("engine/ontologie/_header.py:g:229:20"),
    "config_ia":   tl("engine/ontologie/_header.py:g:233:20"),
    "calcul":      tl("engine/ontologie/_header.py:g:234:20"),
    "skill":       tl("engine/ontologie/_header.py:g:236:20"),
    "agent":       tl("engine/ontologie/_header.py:g:237:20"),
    "admin":       tl("engine/ontologie/_header.py:g:238:20"),
    "client":      tl("engine/ontologie/_header.py:g:239:20"),
    "modele_ia":   tl("engine/ontologie/_header.py:g:240:20"),
    "document":    tl("engine/ontologie/_header.py:g:242:20"),
    "partage":     tl("engine/ontologie/_header.py:g:243:20"),
    "dashboard":   tl("engine/ontologie/_header.py:g:244:20"),
    "kpi":         tl("engine/ontologie/_header.py:g:245:20"),
    "permission":  tl("engine/ontologie/_header.py:g:247:20"),
    "tokens":      tl("engine/ontologie/_header.py:g:249:20"),
    "enregistrement": tl("engine/ontologie/_header.py:g:251:23"),
    "champ":       tl("engine/ontologie/_header.py:g:253:20"),
    # ── Ajout : soumissions client, suppressions/corbeille, gestion des
    # tables, imports/exports/sauvegardes, quotas et alertes. ──
    "soumission":     tl("engine/ontologie/_header.py:g:256:23"),
    "brouillon":      tl("engine/ontologie/_header.py:g:258:23"),
    "validation":     tl("engine/ontologie/_header.py:g:260:23"),
    "rejet":          tl("engine/ontologie/_header.py:g:262:23"),
    "suppression":    tl("engine/ontologie/_header.py:g:263:23"),
    "corbeille":      tl("engine/ontologie/_header.py:g:265:23"),
    "restauration":   tl("engine/ontologie/_header.py:g:267:23"),
    "purge":          tl("engine/ontologie/_header.py:g:269:23"),
    "archive":        tl("engine/ontologie/_header.py:g:271:23"),
    "version_table":  tl("engine/ontologie/_header.py:g:273:23"),
    "modele_table":   tl("engine/ontologie/_header.py:g:275:23"),
    "import_donnees": tl("engine/ontologie/_header.py:g:277:23"),
    "export_donnees": tl("engine/ontologie/_header.py:g:279:23"),
    "sauvegarde":     tl("engine/ontologie/_header.py:g:281:23"),
    "verrou":         tl("engine/ontologie/_header.py:g:283:23"),
    "quota_stockage": tl("engine/ontologie/_header.py:g:285:23"),
    "alerte_gestion": tl("engine/ontologie/_header.py:g:287:23"),
    "tache_gestion":  tl("engine/ontologie/_header.py:g:289:23"),
    "doublon":        tl("engine/ontologie/_header.py:g:291:23"),
    "bloc":           tl("engine/ontologie/_header.py:g:293:23"),
    # ── Ajout : cartographie fine de la blockchain, au-delà du seul nœud
    # "bloc" — voir DESCRIPTIONS_RELATIONS ci-dessous pour les 10 nouvelles
    # relations qui les connectent entre eux et aux clients/agents/admins/
    # enregistrements/entreprise. ──
    "chaine_blocs":        tl("engine/ontologie/_header.py:g:299:28"),
    "autorite_validation": tl("engine/ontologie/_header.py:g:302:28"),
    "transaction":         tl("engine/ontologie/_header.py:g:305:28"),
    "preuve_authenticite": tl("engine/ontologie/_header.py:g:308:28"),
    "rupture_chaine":      tl("engine/ontologie/_header.py:g:311:28"),
}
DESCRIPTIONS_RELATIONS = {
    "possede":       tl("engine/ontologie/_header.py:g:315:22"),
    "lien":          tl("engine/ontologie/_header.py:g:316:22"),
    "lien_effectif": tl("engine/ontologie/_header.py:g:317:22"),
    "partage":       tl("engine/ontologie/_header.py:g:319:22"),
    "calcule":       tl("engine/ontologie/_header.py:g:320:22"),
    "champ_calcul":  tl("engine/ontologie/_header.py:g:321:22"),
    "genere":        tl("engine/ontologie/_header.py:g:322:22"),
    "entraine":      tl("engine/ontologie/_header.py:g:324:22"),
    "inference":     tl("engine/ontologie/_header.py:g:325:22"),
    "produit":       tl("engine/ontologie/_header.py:g:327:22"),
    "emploie":       tl("engine/ontologie/_header.py:g:328:22"),
    "identifie":     tl("engine/ontologie/_header.py:g:330:22"),
    "configure":     tl("engine/ontologie/_header.py:g:331:22"),
    "co_occurrence": tl("engine/ontologie/_header.py:g:332:22"),
    "similarite":    tl("engine/ontologie/_header.py:g:333:22"),
    "sim_champ":     tl("engine/ontologie/_header.py:g:334:22"),
    "frequence":     tl("engine/ontologie/_header.py:g:336:22"),
    "affiche":       tl("engine/ontologie/_header.py:g:337:22"),
    "mesure":        tl("engine/ontologie/_header.py:g:338:22"),
    "autorise":      tl("engine/ontologie/_header.py:g:339:22"),
    "consomme":      tl("engine/ontologie/_header.py:g:340:22"),
    "contient":      tl("engine/ontologie/_header.py:g:342:22"),
    "colonne":       td("engine/ontologie/_header.py:g:300:22"),
    "meme_nom":      tl("engine/ontologie/_header.py:g:344:22"),
    "operation":     td("engine/ontologie/_header.py:g:302:22"),
    "authentifie":   tl("engine/ontologie/_header.py:g:349:22"),
    # ── Ajout : cartographie fine de la blockchain — chaînage explicite des
    # blocs, autorité de validation, transactions individuelles (avec leur
    # auteur agent/admin/client et leur cible), preuves d'authenticité pour
    # les clients, ruptures d'intégrité détectées. ──
    "precede_bloc":         tl("engine/ontologie/_header.py:g:356:29"),
    "appartient_chaine":    tl("engine/ontologie/_header.py:g:358:29"),
    "ancre_chaine":         tl("engine/ontologie/_header.py:g:360:29"),
    "valide_bloc":          tl("engine/ontologie/_header.py:g:362:29"),
    "contient_transaction": tl("engine/ontologie/_header.py:g:364:29"),
    "porte_sur":            tl("engine/ontologie/_header.py:g:365:29"),
    "opere_transaction":    tl("engine/ontologie/_header.py:g:367:29"),
    "emet_preuve":          tl("engine/ontologie/_header.py:g:369:29"),
    "beneficie_preuve":     tl("engine/ontologie/_header.py:g:371:29"),
    "rompt_entre":          tl("engine/ontologie/_header.py:g:373:29"),
    # ── Ajout : agent ↔ client ↔ commandes, comportements, calculs en
    # cascade, similitudes supplémentaires, dashboards, types de données. ──
    "gere":                   tl("engine/ontologie/_header.py:g:378:31"),
    "supervise":              tl("engine/ontologie/_header.py:g:379:31"),
    "collabore":              tl("engine/ontologie/_header.py:g:380:31"),
    "concurrence":            tl("engine/ontologie/_header.py:g:381:31"),
    "commande":               tl("engine/ontologie/_header.py:g:383:31"),
    "traite":                 tl("engine/ontologie/_header.py:g:385:31"),
    "achete":                 tl("engine/ontologie/_header.py:g:387:31"),
    "consulte":               tl("engine/ontologie/_header.py:g:389:31"),
    "abandonne":              tl("engine/ontologie/_header.py:g:391:31"),
    "recurrence_client":      tl("engine/ontologie/_header.py:g:393:31"),
    "fidelite":               tl("engine/ontologie/_header.py:g:395:31"),
    "parraine":               tl("engine/ontologie/_header.py:g:396:31"),
    "segment_comportemental": tl("engine/ontologie/_header.py:g:397:31"),
    "escalade":               tl("engine/ontologie/_header.py:g:398:31"),
    "ecrit_dans":             tl("engine/ontologie/_header.py:g:399:31"),
    "depend_de":              tl("engine/ontologie/_header.py:g:401:31"),
    "delegue":                tl("engine/ontologie/_header.py:g:403:31"),
    "remplace":               tl("engine/ontologie/_header.py:g:405:31"),
    "tendance":               tl("engine/ontologie/_header.py:g:407:31"),
    "anomalie":               tl("engine/ontologie/_header.py:g:409:31"),
    "cycle_vie":              tl("engine/ontologie/_header.py:g:411:31"),
    "declenche":              tl("engine/ontologie/_header.py:g:413:31"),
    "pic_activite":           tl("engine/ontologie/_header.py:g:415:31"),
    "quasi_doublon":          tl("engine/ontologie/_header.py:g:417:31"),
    "structure_proche":       tl("engine/ontologie/_header.py:g:419:31"),
    "partage_widget":         tl("engine/ontologie/_header.py:g:421:31"),
    "derive_dashboard":       tl("engine/ontologie/_header.py:g:423:31"),
    "type_compatible":        tl("engine/ontologie/_header.py:g:425:31"),
    "type_partage":           tl("engine/ontologie/_header.py:g:427:31"),
    "renouvelle":             tl("engine/ontologie/_header.py:g:429:31"),
    # ── Ajout : soumissions client, suppressions/corbeille/purge, gestion
    # des tables (versions, modèles), imports/exports/sauvegardes,
    # verrous, quotas, alertes, tâches de gestion, doublons. ──
    "soumet":                 tl("engine/ontologie/_header.py:g:434:31"),
    "cible_soumission":       tl("engine/ontologie/_header.py:g:435:31"),
    "genere_enregistrement":  tl("engine/ontologie/_header.py:g:436:31"),
    "instruit_soumission":    tl("engine/ontologie/_header.py:g:438:31"),
    "brouillon_de":           tl("engine/ontologie/_header.py:g:440:31"),
    "brouillon_schema":       tl("engine/ontologie/_header.py:g:442:31"),
    "valide":                 tl("engine/ontologie/_header.py:g:444:31"),
    "valide_soumission":      tl("engine/ontologie/_header.py:g:445:31"),
    "rejette":                tl("engine/ontologie/_header.py:g:447:31"),
    "rejet_de":                tl("engine/ontologie/_header.py:g:448:32"),
    "motif_rejet":            tl("engine/ontologie/_header.py:g:449:31"),
    "declenche_suppression":  tl("engine/ontologie/_header.py:g:451:31"),
    "suppression_table":      tl("engine/ontologie/_header.py:g:453:31"),
    "suppression_ligne":      tl("engine/ontologie/_header.py:g:455:31"),
    "suppression_champ":      tl("engine/ontologie/_header.py:g:457:31"),
    "envoie_corbeille":       tl("engine/ontologie/_header.py:g:458:31"),
    "corbeille_contient":     tl("engine/ontologie/_header.py:g:460:31"),
    "corbeille_de":           tl("engine/ontologie/_header.py:g:462:31"),
    "restaure":               tl("engine/ontologie/_header.py:g:464:31"),
    "restauration_de":        tl("engine/ontologie/_header.py:g:465:31"),
    "purge_corbeille":        tl("engine/ontologie/_header.py:g:467:31"),
    "purge_ligne":            tl("engine/ontologie/_header.py:g:469:31"),
    "purge_par":              tl("engine/ontologie/_header.py:g:471:31"),
    "archive_de":             tl("engine/ontologie/_header.py:g:473:31"),
    "archive_par":            tl("engine/ontologie/_header.py:g:474:31"),
    "version_de":             tl("engine/ontologie/_header.py:g:476:31"),
    "precede_version":        tl("engine/ontologie/_header.py:g:478:31"),
    "cree_depuis_modele":     tl("engine/ontologie/_header.py:g:480:31"),
    "modele_propose_par":     tl("engine/ontologie/_header.py:g:482:31"),
    "import_vers":            tl("engine/ontologie/_header.py:g:484:31"),
    "import_lance_par":       tl("engine/ontologie/_header.py:g:485:31"),
    "import_echec_champ":     tl("engine/ontologie/_header.py:g:486:31"),
    "export_depuis":          tl("engine/ontologie/_header.py:g:488:31"),
    "export_demande_par":     tl("engine/ontologie/_header.py:g:490:31"),
    "sauvegarde_de":          tl("engine/ontologie/_header.py:g:492:31"),
    "sauvegarde_lancee_par":  tl("engine/ontologie/_header.py:g:494:31"),
    "verrou_pose_par":        tl("engine/ontologie/_header.py:g:496:31"),
    "verrou_table":           tl("engine/ontologie/_header.py:g:497:31"),
    "verrou_ligne":           tl("engine/ontologie/_header.py:g:499:31"),
    "quota_entreprise":       tl("engine/ontologie/_header.py:g:501:31"),
    "quota_table":            tl("engine/ontologie/_header.py:g:503:31"),
    "alerte_quota":           tl("engine/ontologie/_header.py:g:505:31"),
    "tache_assignee":         tl("engine/ontologie/_header.py:g:507:31"),
    "tache_concerne":         tl("engine/ontologie/_header.py:g:509:31"),
    "doublon_de":             tl("engine/ontologie/_header.py:g:511:31"),
}

# ── Couche « science des données » (engine/ontologie_science.py) : 25 types de nœuds, 30 relations,
# calculés sur les vraies données de chaque table. Couleurs et descriptions fusionnées ici pour la
# légende, la personnalisation des couleurs et le contexte donné à l'IA.
from ..ontologie_science import TYPES_NOEUDS_SCIENCE, RELATIONS_SCIENCE  # noqa: E402
_COULEURS.update({k: v[0] for k, v in TYPES_NOEUDS_SCIENCE.items()})
_COULEURS_ARETES.update({k: v[0] for k, v in RELATIONS_SCIENCE.items()})
DESCRIPTIONS_TYPES.update({k: v[1] for k, v in TYPES_NOEUDS_SCIENCE.items()})
DESCRIPTIONS_RELATIONS.update({k: v[1] for k, v in RELATIONS_SCIENCE.items()})

from ..ontologie_communication import TYPES_NOEUDS_COMMUNICATION, RELATIONS_COMMUNICATION  # noqa: E402
_COULEURS.update({k: v[0] for k, v in TYPES_NOEUDS_COMMUNICATION.items()})
_COULEURS_ARETES.update({k: v[0] for k, v in RELATIONS_COMMUNICATION.items()})
DESCRIPTIONS_TYPES.update({k: v[1] for k, v in TYPES_NOEUDS_COMMUNICATION.items()})
DESCRIPTIONS_RELATIONS.update({k: v[1] for k, v in RELATIONS_COMMUNICATION.items()})

from ..ontologie_siem import TYPES_NOEUDS_SIEM, RELATIONS_SIEM  # noqa: E402
_COULEURS.update({k: v[0] for k, v in TYPES_NOEUDS_SIEM.items()})
_COULEURS_ARETES.update({k: v[0] for k, v in RELATIONS_SIEM.items()})
DESCRIPTIONS_TYPES.update({k: v[1] for k, v in TYPES_NOEUDS_SIEM.items()})
DESCRIPTIONS_RELATIONS.update({k: v[1] for k, v in RELATIONS_SIEM.items()})

# Tables système (wallet, paiement, messagerie, audit…) : un type et une couleur par domaine, relations déduites du code — voir ontologie_systeme.
from ..ontologie_systeme import TYPES_NOEUDS_SYSTEME, RELATIONS_SYSTEME  # noqa: E402
_COULEURS.update({k: v[0] for k, v in TYPES_NOEUDS_SYSTEME.items()})
_COULEURS_ARETES.update({k: v[0] for k, v in RELATIONS_SYSTEME.items()})
DESCRIPTIONS_TYPES.update({k: v[1] for k, v in TYPES_NOEUDS_SYSTEME.items()})
DESCRIPTIONS_RELATIONS.update({k: v[1] for k, v in RELATIONS_SYSTEME.items()})


# ── Modules récents : types de nœuds et relations ajoutés par
# enrichir_relations_modules (_part_001.py). Couleurs choisies hors des
# familles déjà utilisées pour rester distinguables dans la légende. ──
_NOUVEAUX_TYPES = {
    "groupe_partage_ligne":   ("#0d9488", tl("engine/ontologie/_header.py:g:460:43")),
    "groupe_partage_cellule": ("#7c2d12", tl("engine/ontologie/_header.py:g:462:43")),
    "abonnement":             ("#4d7c0f", tl("engine/ontologie/_header.py:g:464:43")),
    "connexion_externe":      ("#0f766e", tl("engine/ontologie/_header.py:g:466:43")),
    "groupe_tables":          ("#4338ca", tl("engine/ontologie/_header.py:g:468:43")),
    "type_champ":             ("#64748b", tl("engine/ontologie/_header.py:g:470:43")),
    "famille_type_champ":     ("#334155", tl("engine/ontologie/_header.py:g:473:43")),
    "tache_planifiee":        ("#a21caf", tl("engine/ontologie/_header.py:g:475:43")),
}
_NOUVELLES_RELATIONS = {
    "est_de_type":       ("#94a3b8", tl("engine/ontologie/_header.py:g:554:38")),
    "famille_de_type":   ("#cbd5e1", tl("engine/ontologie/_header.py:g:555:38")),
    "reference":         ("#2dd4bf", tl("engine/ontologie/_header.py:g:556:38")),
    "alimente_formule":  ("#f97316", tl("engine/ontologie/_header.py:g:557:38")),
    "calcul_ligne":      ("#ea580c", tl("engine/ontologie/_header.py:g:559:38")),
    "partage_ligne":     ("#14b8a6", tl("engine/ontologie/_header.py:g:561:38")),
    "partage_cellule":   ("#b45309", tl("engine/ontologie/_header.py:g:562:38")),
    "affiche_champ":     ("#38bdf8", tl("engine/ontologie/_header.py:g:564:38")),
    "affiche_kpi":       ("#0ea5e9", tl("engine/ontologie/_header.py:g:566:38")),
    "mesure_champ":      ("#7dd3fc", tl("engine/ontologie/_header.py:g:567:38")),
    "souscrit":          ("#65a30d", tl("engine/ontologie/_header.py:g:568:38")),
    "debloque":          ("#a3e635", tl("engine/ontologie/_header.py:g:569:38")),
    "importe_vers":      ("#115e59", tl("engine/ontologie/_header.py:g:571:38")),
    "regroupe":          ("#6366f1", tl("engine/ontologie/_header.py:g:572:38")),
    "surveille":         ("#d946ef", tl("engine/ontologie/_header.py:g:573:38")),
    "planifie":          ("#c026d3", tl("engine/ontologie/_header.py:g:575:38")),
    "sollicite":         ("#f59e0b", tl("engine/ontologie/_header.py:g:576:38")),
}
_COULEURS.update({k: v[0] for k, v in _NOUVEAUX_TYPES.items()})
_COULEURS_ARETES.update({k: v[0] for k, v in _NOUVELLES_RELATIONS.items()})
DESCRIPTIONS_TYPES.update({k: v[1] for k, v in _NOUVEAUX_TYPES.items()})
DESCRIPTIONS_RELATIONS.update({k: v[1] for k, v in _NOUVELLES_RELATIONS.items()})




# Sans __all__, "import *" ignore par defaut les noms prefixes "_" -
# hors ce module (et l ensemble du package) s appuie sur ce prefixe comme
# simple convention "prive au fichier", pas "prive au package" : tout ce
# qui est defini ici doit rester visible via import * en aval (fichiers
# suivants du package, __init__.py). On force donc la reexportation complete.
__all__ = [_nom for _nom in dir() if not _nom.startswith("__")]
