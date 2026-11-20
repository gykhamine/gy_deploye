"""SIEM — corrélation : des gestes banals pris seuls, dangereux enchaînés (étape « correlations » du cycle de siem_regles).

  1 prise_de_controle      changement d'identité (mot de passe, e-mail, téléphone 2FA) puis action sensible (retrait, export, clé API) en < 1 h
  2 fraude_wallet          retrait très supérieur à l'habitude, vers un numéro jamais utilisé, fractionné sous un plafond, ou depuis une IP jamais vue
  3 aspiration_donnees     balayage de nombreuses tables puis export / PDF en < 1 h (chaque seuil seul ne suffit pas)
  4 elevation_privileges   actions d'administration rares (rôle, permissions, création de membre, clé API) : jamais faites ou hors des heures du compte
  5 cle_api_detournee      la clé API profilée comme un compte : IP nouvelle, clé dormante réveillée, clé neuve utilisée d'ailleurs, volume / horaire inhabituels

  6 succes_apres_echecs    connexion réussie après une rafale d'échecs depuis la même IP (bourrage d'identifiants abouti)
  7 enumeration_ids        rafale de 403/404 sur des identifiants différents d'une même ressource ; en temps réel (noter_refus), par IP, visiteurs anonymes compris
  8 exfiltration_destruction  export / PDF puis suppressions en série (vol puis sabotage)
  9 affaiblissement        réglage SIEM, 2FA coupé, purge ou clôture d'incident, puis action sensible dans l'heure (effacement de traces)
 10 compte_dormant         compte muet depuis plus de 60 jours, réveillé dans les 3 derniers jours, qui fait une action sensible
 11 compte_neuf_actif      compte dont la toute première trace date de moins de 24 h et qui fait déjà une action sensible
 12 moyen_retrait_recent   retrait vers un moyen de retrait enregistré ou modifié dans l'heure
 13 membres_en_masse       création de membres en rafale (comptes fantômes préparés)
 14 cles_api_multiples     plusieurs clés API créées dans l'heure par un même compte
 15 tentative_privilege    refus 403 répétés sur des routes d'administration / sensibles, puis réussite (contournement abouti)
 16 sessions_paralleles    même compte, deux réseaux différents, à quelques minutes d'écart, dont une action sensible
 17 destination_partagee   plusieurs comptes retirent vers le même numéro (compte mule, entonnoir)
 18 rotation_ip            un compte vu depuis de nombreux réseaux dans l'heure (proxy, VPN en rotation, relais)
 19 sensible_hors_heures   retrait ou export à une heure absente de l'historique du compte
 20 cumul_retraits         somme des retraits de 24 h très supérieure aux journées habituelles (le fractionnement étalé sur la journée)
 21 cle_usage_inedit       clé API qui fait une action sensible qu'elle n'avait jamais faite
 22 elevation_puis_usage   droit accordé (rôle, permissions, membre, clé réactivée) puis retrait ou export par le même compte dans l'heure
 23 bourrage_distribue     un compte attaqué depuis de nombreuses IP, ou une IP qui essaie de nombreux comptes (temps réel, dans noter_echec)
 24 config_wallet_retrait  réglage du wallet (frais, taux, politique, principal) puis retrait dans l'heure
 25 compte_marionnette     membre créé puis premier usage sensible depuis la même IP que son créateur
 26 nettoyage_apres_action retrait ou export puis révocation d'une clé API ou suppression d'un membre dans l'heure (on efface ce qui mène à soi)
 27 eparpillement          retraits vers de nombreux numéros différents dans l'heure (distribution des fonds)
 28 aller_retour_wallet    recharge ou transfert puis retrait dans la demi-heure, répété (lessivage)
 29 collecte_multi_familles  lectures sensibles dans de nombreuses familles de données (clients, employés, wallets…) en 1 h
 30 usurpation_par_admin   compte modifié par un administrateur puis utilisé pour une action sensible depuis la même IP que cet administrateur (haute si l'IP est partagée par d'autres comptes de l'entreprise)
 31 auto_elevation         un compte change son propre rôle (seul /role/ est comparable : /permissions/ porte un membre_id, pas un user_id)
 32 administrateur_fantome membre créé puis rôle ou permissions accordés dans l'heure par le même compte (compte de secours préparé)
 33 verrouillage_du_compte 2FA coupé puis identifiants changés dans l'heure : on enferme le propriétaire dehors
 34 cadence_mecanique      retraits à intervalles presque égaux (script) : au moins 5 dans l'heure, écarts à moins de 15 % de dispersion
 35 cle_jetable            clé API créée, utilisée pour retirer ou exporter, puis révoquée dans l'heure (outil à usage unique)
 36 purge_comptes          rôles modifiés ou membres supprimés pour au moins 3 comptes différents dans l'heure (prise de pouvoir, sabotage)
 37 supprime_encore_actif  membre supprimé qui agit encore dans l'heure (session ou clé API restée vivante)
 38 droit_pret             rôle accordé à un compte, qui fait une action sensible ou d'administration, puis rôle modifié à nouveau dans l'heure
 39 exports_en_rafale      exports sur au moins 4 chemins différents dans l'heure, sans balayage de tables ni lectures multiples
 40 accumulation_acces     au moins 3 sortes d'accès durables créés dans l'heure (clé, membre, rôle, permissions, clé réactivée)
 41 revocations_en_masse   au moins 3 clés API révoquées dans l'heure par un même compte (coupure des accès légitimes, des intégrations)
 42 escalade_montants      au moins 4 retraits aux montants strictement croissants dans l'heure (on sonde la limite)
 43 identite_en_rafale     au moins 3 changements d'identifiants du compte lui-même dans l'heure (mot de passe, e-mail, téléphone, 2FA)
 44 identite_apres_sortie  retrait ou export puis changement des identifiants du compte dans l'heure (on ferme la porte derrière soi)
 45 export_inedit          export d'une famille de chemins que le compte n'avait jamais exportée, alors qu'il avait déjà un historique d'exports
 46 aveuglement_siem       au moins 3 réglages SIEM (config, lockdown, rebasage) par un même compte dans l'heure : on tourne les boutons de la surveillance
 47 recharges_en_rafale    au moins 5 recharges ou transferts par un même compte dans l'heure (essais de cartes, approvisionnement fractionné)
 48 comptes_meme_ip        au moins 3 comptes différents qui retirent depuis la même IP dans l'heure (ferme de comptes, mules pilotées par une seule main)
 49 beneficiaire_actif     rôle accordé à un compte par un autre, qui retire ou exporte aussitôt dans l'heure (droit exploité sur-le-champ)
 50 montants_identiques    au moins 3 retraits exactement du même montant dans l'heure (fractionnement sans plafond configuré)

Calcul pur (listes de dicts, aucun accès base) → testable sans Django ; l'adaptateur `surveiller_correlations()` lit JournalAudit, RetraitWallet
et les fenêtres de tables du cache, puis émet les alertes par siem.alerter() (même chemin que les autres règles : blockchain système, incident, cloche).
Les chemins reconnus sont réglables : settings.GYPLATFORM_SIEM_CHEMINS = {"identite": [...], "retrait": [...], ...} (regex, ajoutées aux défauts).

Organisation : un fichier par famille dans engine/siem_corr/ (compte, wallet, données, administration, clés API), le registre des règles périodiques
(registre.py : liste `REGLES`, fonction pure `calculer_alertes`), les règles temps réel (temps_reel.py), le moteur continu de la famille wallet (continu.py : état par compte,
plus de fenêtre de calcul, oubli progressif réglé par `continu_oubli_heures`) et l'adaptateur base / cache (adaptateur.py).
Ce module reste la façade historique : tout ce qui s'importait d'ici s'importe encore d'ici.
"""
from .siem_corr import adaptateur, administration, base, cles_api, compte, continu, donnees, registre, temps_reel, wallet

for _module in (base, compte, wallet, donnees, administration, cles_api, temps_reel, continu, adaptateur, registre):
    globals().update({_nom: _valeur for _nom, _valeur in vars(_module).items() if not _nom.startswith("__")})
