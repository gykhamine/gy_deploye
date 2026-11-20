// OUTILS TACTIQUES de la carte de géolocalisation : relevé et saisie de coordonnées (décimal, DMS, UTM, MGRS), tracé mesuré (étapes, caps, durée),
// matrice des distances, projection à l'estime, cercles et corridors enregistrables en zones, géorepérage (entrées/sorties), itinéraire OpenStreetMap (ligne droite + tracé par les routes)
// (repris de la carte du dashboard BI : « partir d'ici » puis un clic par destination) et exports KML/GPX. Tous les calculs viennent du serveur
// (engine/geo_outils.py, API /api/tables/<id>/geolocalisation/outils/) : ici on ne fait que saisir, afficher et dessiner.
// S'appuie sur window.GEO_API (geolocalisation.js) ; chargé AVANT logistique.js et annotations_carte.js pour que son outil actif reçoive les clics en premier.
(function () {
"use strict";
const A = window.GEO_API, G = window.GEO;
if (!A || !document.getElementById("ot-resultat")) return;
const $ = id => document.getElementById(id), el = A.el, S = A.S;
const URL_OUTILS = "/api/tables/" + G.tableId + "/geolocalisation/outils/";
const URL_ITI = "/api/tables/" + G.tableId + "/geolocalisation/itineraire/";
const URL_AN = "/api/tables/" + G.tableId + "/geolocalisation/annotations/";
const T = {outil: null, repere: null, mesure: [], etapes: null, corridor: null, cercle: null, projection: null, depart: null, dest: null, route: null, nr: 0, court: null, nc: 0, suivi: null, hist: [], vitReelle: null, sr: 0, arrive: false, zones: null, dedansAvant: null, sn: 0};
let partagerPosition = () => {};   // défini par le bloc « Personnes » : envoie la position GPS de la personne affichée dans la table de positions choisie
let noterPosition = () => {};   // défini par le bloc « Personnes » : ajoute la position reçue au trajet parcouru de la personne affichée
let syncLiens = () => {};   // défini par le bloc « Personnes » plus bas (liens départ/destination <-> objets de la table)
const fmtCap = v => String(Math.round(v) % 360).padStart(3, "0") + "°";
const fmtT = t => t == null ? "" : S.D && S.D.temps_reel ? new Date(t * 1000).toLocaleString() : "+" + A.fmtDuree(t - (S.D ? S.D.t[0] : 0));
const calcul = corps => A.api(URL_OUTILS, {method: "POST", body: JSON.stringify(corps)});
const nombre = (id, defaut) => { const v = parseFloat(String($(id).value).replace(",", ".")); return Number.isFinite(v) ? v : defaut; };
const estTrajet = o => !o.mode || o.mode === "trajectoire";
const redessiner = () => A.redessiner();

// ── Positions des objets suivis ──────────────────────────────────────────────
function visibles() { return S.D && !S.D.vide ? S.D.objets.filter(o => S.visibles[o.id] && estTrajet(o) && o.lat) : []; }
function dernier(o, k) {   // dernière position connue à l'indice k (ou avant)
  for (let i = Math.min(o.lat.length - 1, Math.max(0, Math.round(k))); i >= 0; i--) if (o.lat[i] != null && o.lon[i] != null) return {i, lat: o.lat[i], lon: o.lon[i]};
  return null;
}
function positionsActuelles() { return visibles().map(o => { const p = dernier(o, S.k); return p && {id: o.id, nom: o.nom, lat: p.lat, lon: p.lon}; }).filter(Boolean); }
function trace(o, jusqua) {   // [[lat, lon, t]] jusqu'à l'indice demandé, 5000 derniers points au plus
  const pts = [];
  for (let i = 0; i <= Math.min(o.lat.length - 1, Math.round(jusqua)); i++) if (o.lat[i] != null && o.lon[i] != null) pts.push([o.lat[i], o.lon[i], S.D.t[i]]);
  return pts.slice(-5000);
}
function accrocher(px, py) {   // un clic près d'un objet suivi prend sa position exacte
  let meilleur = null, dmin = 14;
  for (const p of positionsActuelles()) { const [x, y] = A.versEcran(p.lat, p.lon), d = Math.hypot(x - px, y - py); if (d < dmin) { dmin = d; meilleur = p; } }
  return meilleur ? [meilleur.lat, meilleur.lon, meilleur.nom] : null;
}

// ── Résultats ────────────────────────────────────────────────────────────────
function ligneCopiable(titre, valeur) {
  const b = el("button", {class: "geo-btn", style: "padding:1px 6px;margin-left:4px", title: tj("engine/static/engine/js/geo_outils.js:44:95")}, "⧉");
  b.onclick = async () => { try { await navigator.clipboard.writeText(valeur); A.toast(tj("engine/static/engine/js/geo_outils.js:45:89") + valeur); } catch (e) { A.toast(valeur); } };
  return el("div", {}, el("b", {}, titre + " : "), valeur, b);
}
function montrerCoordonnees(c) {
  const r = $("ot-resultat"); r.textContent = "";
  r.append(ligneCopiable(tj("engine/static/engine/js/geo_outils.js:50:27"), c.decimal), ligneCopiable("DMS", c.dms));
  if (c.utm) r.append(ligneCopiable("UTM", c.utm), ligneCopiable("MGRS", c.mgrs_lisible));
  else r.append(el("div", {}, tj("engine/static/engine/js/geo_outils.js:52:32")));
}
async function releve(lat, lon) {
  T.repere = [lat, lon]; redessiner();
  try { montrerCoordonnees(await calcul({action: "convertir", lat, lon})); } catch (e) { /* toast déjà affiché */ }
}

// ── Panneau flottant sur la carte (matrice, zones) ───────────────────────────
function panneau(titre, ...contenu) {
  let p = $("ot-panneau");
  if (!p) {
    p = el("div", {id: "ot-panneau", style: "position:absolute;top:10px;right:10px;max-width:min(560px,calc(100% - 20px));max-height:62%;overflow:auto;z-index:6;background:color-mix(in srgb,var(--fond-sombre,#080e14) 94%,transparent);" +
      "border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:12px;color:var(--text,#cfe6f5)"});
    $("zone-carte").append(p);
  }
  p.textContent = ""; p.style.display = "block";
  p.append(el("div", {style: "display:flex;justify-content:space-between;gap:12px;margin-bottom:6px"}, el("b", {}, titre), el("button", {class: "geo-btn", style: "padding:0 8px", onclick: () => { p.style.display = "none"; }, title: tj("engine/static/engine/js/geo_outils.js:68:234")}, "✕")));
  contenu.forEach(c => p.append(c));
  return p;
}
function telecharger(nom, contenu, mime) {
  const l = el("a", {href: URL.createObjectURL(new Blob([contenu], {type: mime})), download: nom});
  document.body.append(l); l.click(); l.remove();
}

// ── Outils à clic ────────────────────────────────────────────────────────────
function activer(nom) {
  T.outil = T.outil === nom ? null : nom;
  if (T.outil) {   // un seul outil actif : on coupe ceux de la page (mesure simple, annotations)
    if (S.mesure.actif && $("btn-mesure")) $("btn-mesure").click();
    const b = document.querySelector("[data-outil].principal"); if (b) b.click();
  }
  document.querySelectorAll("[data-ot]").forEach(x => x.classList.toggle("principal", x.dataset.ot === T.outil));
  $("carte").classList.toggle("mesure", !!T.outil);
  const aide = {repere: tj("engine/static/engine/js/geo_outils.js:86:26"), mesure: tj("engine/static/engine/js/geo_outils.js:86:107"),
    cercle: tj("engine/static/engine/js/geo_outils.js:87:14"), itineraire: T.depart ? tj("engine/static/engine/js/geo_outils.js:87:93") : tj("engine/static/engine/js/geo_outils.js:87:232"),
    position: tj("engine/static/engine/js/geo_outils.js:88:16")};
  $("ot-aide").textContent = T.outil ? aide[T.outil] : "";
  redessiner();
}
async function mesurer() {
  if (T.mesure.length < 2) { T.etapes = null; montrerMesure(); redessiner(); return; }
  const n = ++T.sn;
  try {
    const v = nombre("ot-vitesse", 0), r = await calcul({action: "mesurer", points: T.mesure.map(p => [p[0], p[1]]), vitesse_kmh: v > 0 ? v : undefined});
    if (n === T.sn) { T.etapes = r; montrerMesure(); redessiner(); }
  } catch (e) { /* toast déjà affiché */ }
}
function montrerMesure() {
  const r = $("ot-resultat"); r.textContent = "";
  if (!T.mesure.length) { r.append(tj("engine/static/engine/js/geo_outils.js:103:37")); return; }
  if (!T.etapes) { r.append(T.mesure.length + tj("engine/static/engine/js/geo_outils.js:104:48")); return; }
  const m = T.etapes;
  r.append(el("div", {}, el("b", {}, tj("engine/static/engine/js/geo_outils.js:106:39")), A.fmtM(m.total_m) + (m.duree_s != null ? " · " + A.fmtDuree(m.duree_s) : "") + (m.detour != null && T.mesure.length > 2 ? tj("engine/static/engine/js/geo_outils.js:105:174") + m.detour : "")));
  m.etapes.forEach((e, i) => r.append(el("div", {class: "geo-note"}, (i + 1) + ". " + A.fmtM(e.distance_m) + " · cap " + fmtCap(e.cap_initial) + (Math.abs(e.cap_final - e.cap_initial) > 0.5 ? " → " + fmtCap(e.cap_final) : "") + (e.duree_s != null ? " · " + A.fmtDuree(e.duree_s) : ""))));
  r.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_outils.js:direct_premier_dernier") + A.fmtM(m.distance_directe_m)));
}
async function poserCercle(lat, lon) {
  const rayon = nombre("ot-rayon", 0);
  if (!(rayon >= 1)) { A.toast(tj("engine/static/engine/js/geo_outils.js:111:33"), true); return; }
  try { const r = await calcul({action: "cercle", lat, lon, rayon_m: rayon}); T.cercle = {lat, lon, rayon, geometrie: r.geometrie}; montrerZone(tj("engine/static/engine/js/geo_outils.js:112:146") + A.fmtM(rayon), r.geometrie); redessiner(); } catch (e) { /* toast déjà affiché */ }
}
function montrerZone(titre, geometrie) {
  const r = $("ot-resultat"); r.textContent = "";
  r.append(el("div", {}, el("b", {}, titre), " · " + geometrie.length + " sommets"));
}

function clic(px, py) {
  if (!T.outil) return false;
  const acc = accrocher(px, py), [lat, lon] = acc || A.versGeo(px, py);
  if (T.outil === "repere") { releve(lat, lon); return true; }
  if (T.outil === "mesure") { T.mesure.push([lat, lon]); T.corridor = null; mesurer(); redessiner(); return true; }
  if (T.outil === "cercle") { poserCercle(lat, lon); return true; }
  if (T.outil === "position") { arreterSuivi(); poserActuelle(lat, lon, acc ? acc[2] : tj("engine/static/engine/js/geo_outils.js:125:89")); return true; }
  if (T.outil === "itineraire") {
    if (!T.depart) { T.depart = {lat, lon, nom: acc ? acc[2] : null}; $("ot-aide").textContent = tj("engine/static/engine/js/geo_outils.js:127:99"); majDepart(); redessiner(); return true; }
    T.dest = {lat, lon, nom: acc ? acc[2] : null}; T.arrive = false; majReste(); redessiner();
    // plus d'ouverture automatique d'OpenStreetMap (onglet/fenêtre bloquée sur téléphone) : bouton « Ouvrir dans OpenStreetMap » (ot-osm) à la demande
    return true;
  }
  return false;
}
const MODES = {couloir: [tj("engine/static/engine/js/geo_outils.js:134:27"), "fossgis_osrm_foot"], raccourcis: [tj("engine/static/engine/js/geo_outils.js:lbl_raccourcis"), "fossgis_osrm_car"], moto: [tj("engine/static/engine/js/geo_outils.js:lbl_moto"), "fossgis_osrm_car"], bateau: [tj("engine/static/engine/js/geo_outils.js:lbl_bateau"), null], voiture: [tj("engine/static/engine/js/geo_outils.js:lbl_voiture"), "fossgis_osrm_car"], velo: [tj("engine/static/engine/js/geo_outils.js:134:279"), "fossgis_osrm_bike"], pied: [tj("engine/static/engine/js/geo_outils.js:134:316"), "fossgis_osrm_foot"]};
const modeRoute = () => { const s = $("ot-mode-route"); return s && MODES[s.value] ? s.value : "raccourcis"; };
function ouvrirItineraire(d, a) {
  if (!MODES[modeRoute()][1]) { A.toast(tj("engine/static/engine/js/geo_outils.js:137:42"), true); return; }   // même lien que « Itinéraire jusqu'ici » de la carte BI, selon le mode choisi (voiture par défaut), calculé par OpenStreetMap (OSRM)
  const f = p => p.lat.toFixed(6) + "," + p.lon.toFixed(6);
  window.open("https://www.openstreetmap.org/directions?engine=" + MODES[modeRoute()][1] + "&route=" + f(d) + ";" + f(a), "_blank", "noopener");
}

// ── Tracé PAR LES ROUTES (s'ajoute à la ligne droite, qui reste affichée) ────────────────────────────
// Calculé par le serveur (engine/geo_routage.py). Recalcul seulement si la destination ou le mode change, ou si le départ a bougé de plus de 25 m
// (suivi GPS toutes les 10 s : pas un appel par mise à jour). Échec (mer, île, hors ligne) : message, la ligne droite reste.
// Distance d'un point au tracé (m) et position le long du tracé (m depuis son début) : plan local équirectangulaire, exact à l'échelle d'une ville.
function surTrace(geom, lat, lon) {
  const kx = Math.cos(lat * Math.PI / 180) * 111320, ky = 110574;
  let meilleur = {d: Infinity, s: 0, i: 0}, cumul = 0;
  for (let i = 1; i < geom.length; i++) {
    const ax = (geom[i - 1][1] - lon) * kx, ay = (geom[i - 1][0] - lat) * ky, bx = (geom[i][1] - lon) * kx, by = (geom[i][0] - lat) * ky;
    const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy, long = Math.sqrt(l2);
    const u = l2 > 0 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / l2)) : 0, d = Math.hypot(ax + u * dx, ay + u * dy);
    if (d < meilleur.d) meilleur = {d, s: cumul + u * long, i: i - 1};
    cumul += long;
  }
  meilleur.total = cumul;
  return meilleur;
}
const routeActive = () => !$("ot-route-actif") || $("ot-route-actif").checked;
function routeTentee() {   // un calcul (réussi OU refusé) existe-t-il déjà pour ce départ (à 25 m près), cette destination et ce mode ? sinon un refus se répéterait à chaque mise à jour GPS
  const r = T.route;
  if (!(r && T.depart && T.dest && r.mode === modeRoute() && r.dest[0] === T.dest.lat && r.dest[1] === T.dest.lon)) return false;
  if (!T.livreur) return A.hav(r.depart[0], r.depart[1], T.depart.lat, T.depart.lon) <= 25;
  // Mode livreur (GPS continu) : pas un appel tous les 25 m. Route valide : recalcul seulement si l'on s'écarte de plus de 50 m du tracé (et pas plus d'un calcul
  // toutes les 15 s) ; refus ou réseau absent : nouvel essai toutes les 30 s (le réseau peut revenir en roulant).
  const age = Date.now() - (r.t || 0);
  if (!r.ok) return age < 30000;
  return age < 15000 || surTrace(r.geometrie, T.depart.lat, T.depart.lon).d <= 50;
}
const routeValide = () => routeTentee() && T.route.ok;
// Tracé CONSERVÉ : le dernier calcul réussi, gardé quand le serveur est injoignable ou la session terminée. Il reste affiché (même si le départ a bougé) jusqu'au recalcul.
const routeConservee = () => !!(T.route && T.route.ok && T.route.hors_ligne && T.route.geometrie && T.route.geometrie.length > 1 && memeCible(T.route));
const routeAffichable = () => routeValide() || routeConservee();
// ── Avis de changement d'itinéraire : message à l'écran, notification du système si la page est en arrière-plan (permission demandée au clic), vibration en mode livreur ──
function demanderNotifications() { try { if (window.GYNotif) GYNotif.demander(); else if (window.Notification && Notification.permission === "default") Notification.requestPermission(); } catch (e) { /* refusée ou indisponible : le message à l'écran reste */ } }
function aviser(type, message, voix) {
  T.dernierAvis = {type, message, t: Date.now()};
  A.toast(message, type === "perdu");
  try { document.dispatchEvent(new CustomEvent("geo-itineraire", {detail: {type, message, voix: voix || message}})); } catch (e) { /* sans effet */ }
  if (type !== "modifie" && type !== "perdu") return;
  try {
    if (window.GYNotif) GYNotif.afficher({titre: tj("engine/static/engine/js/geo_outils.js:179:108"), message, tag: "geo-itineraire", seulement_masquee: true});
    else if (document.hidden && window.Notification && Notification.permission === "granted") new Notification(tj("engine/static/engine/js/geo_outils.js:179:108"), {body: message, tag: "geo-itineraire"});
    if (T.livreur && navigator.vibrate) navigator.vibrate([200, 100, 200]);
  } catch (e) { /* notification système indisponible */ }
}
function memeCible(r) { return !!(r && T.dest && r.mode === modeRoute() && r.dest[0] === T.dest.lat && r.dest[1] === T.dest.lon); }
function parcoursChange(ancien, neuf) {   // le NOUVEAU tracé quitte-t-il l'ancien (plus de 60 m) après les 300 premiers mètres ? (le début change toujours quand on a bougé)
  let cumul = 0;
  for (let i = 1; i < neuf.length; i++) {
    cumul += A.hav(neuf[i - 1][0], neuf[i - 1][1], neuf[i][0], neuf[i][1]);
    if (cumul > 300 && surTrace(ancien, neuf[i][0], neuf[i][1]).d > 60) return true;
  }
  return false;
}
const minutes = s => Math.max(1, Math.round(s / 60)) + " min";
// ── Chemin le plus court dans le couloir de 80 m : deuxième tracé, affiché PAR DÉFAUT (comme la ligne droite), d'une autre couleur (violet) ──
// Indépendant du mode choisi ci-dessus ; case à décocher pour le masquer. Calculé par le serveur (mode « couloir » de geo_routage.py).
const COULEUR_COURT = gyCouleur("--forme-violet","#b084ff");
const courtActif = () => !$("ot-court-actif") || $("ot-court-actif").checked;
const courtVisible = () => !!(T.court && T.court.ok && T.dest && T.depart && courtActif() && T.court.dest[0] === T.dest.lat && T.court.dest[1] === T.dest.lon);
function courtTente() {   // même logique de recalcul que le tracé par les routes : destination inchangée et départ à moins de 25 m (livreur : 50 m du tracé, 15 s / 30 s)
  const r = T.court;
  if (!(r && T.depart && T.dest && r.dest[0] === T.dest.lat && r.dest[1] === T.dest.lon)) return false;
  if (!T.livreur) return A.hav(r.depart[0], r.depart[1], T.depart.lat, T.depart.lon) <= 25;
  const age = Date.now() - (r.t || 0);
  if (!r.ok) return age < 30000;
  return age < 15000 || surTrace(r.geometrie, T.depart.lat, T.depart.lon).d <= 50;
}
async function majCourt(force) {
  const zone = $("ot-court");
  if (!zone) return;
  if (!T.depart || !T.dest || !courtActif()) { T.court = null; zone.textContent = ""; ++T.nc; redessiner(); return; }
  if (!force && courtTente()) return;
  const n = ++T.nc, depart = [T.depart.lat, T.depart.lon], dest = [T.dest.lat, T.dest.lon];
  zone.textContent = tj("engine/static/engine/js/geo_outils.js:212:23");
  try {
    const r = await A.api(URL_ITI, {method: "POST", body: JSON.stringify({depart, arrivee: dest, profil: "couloir"})});
    if (n !== T.nc) return;
    T.court = {ok: true, depart, dest, geometrie: r.geometrie, distance_m: r.distance_m, duree_s: r.duree_s, t: Date.now()};
    zone.textContent = "";
    zone.append(el("div", {}, el("b", {style: "color:" + COULEUR_COURT}, r.hors_couloir ? tj("engine/static/engine/js/geo_outils.js:222:92") + Math.round(r.couloir_m) + " m) : " : tj("engine/static/engine/js/geo_outils.js:218:75") + Math.round(r.couloir_m) + " m) : "), A.fmtM(r.distance_m) + " · " + A.fmtDuree(r.duree_s) + tj("engine/static/engine/js/geo_outils.js:218:202") + Math.round(r.ecart_max_m) + " m"));
    (r.avertissements || []).forEach(m => zone.append(el("div", {class: "geo-note"}, "⚠ " + m)));
  } catch (e) {
    if (n !== T.nc) return;
    T.court = {ok: false, depart, dest, t: Date.now()};   // refus mémorisé : pas de nouvel appel tant que le départ ne bouge pas de plus de 25 m
    zone.textContent = tj("engine/static/engine/js/geo_outils.js:223:25") + (e && (e.erreur || e.message) ? (e.erreur || e.message) : tj("engine/static/engine/js/geo_outils.js:lbl_service_indispo"));
  }
  redessiner();
}
let veilleConnexion = null;
function surveillerConnexion() {   // tracé conservé : on retente tant qu'il le reste (toutes les 20 s), et tout de suite au retour du réseau ou de l'onglet
  if (veilleConnexion) return;
  veilleConnexion = setInterval(() => {
    if (!(T.route && T.route.hors_ligne)) { clearInterval(veilleConnexion); veilleConnexion = null; return; }
    if (!document.hidden) majRoute(true);
  }, 20000);
}
window.addEventListener("online", () => { if (T.route && T.route.hors_ligne) majRoute(true); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && T.route && T.route.hors_ligne) majRoute(true); });
async function majRoute(force) {
  const zone = $("ot-route");
  if (!zone) return;
  majCourt(force);
  if (!T.depart || !T.dest || !routeActive()) { T.route = null; zone.textContent = ""; ++T.nr; redessiner(); return; }
  if (!force && routeTentee() && !(T.route.hors_ligne && Date.now() - (T.route.t || 0) > 20000)) return;   // tracé conservé : nouvel essai au plus toutes les 20 s
  const n = ++T.nr, mode = modeRoute(), depart = [T.depart.lat, T.depart.lon], dest = [T.dest.lat, T.dest.lon];
  const resumeAvant = zone.innerHTML;   // pour le remettre tel quel si la connexion manque
  zone.textContent = tj("engine/static/engine/js/geo_outils.js:234:23");
  try {
    const r = await A.api(URL_ITI, {method: "POST", body: JSON.stringify({depart, arrivee: dest, profil: mode})});
    if (n !== T.nr) return;                                       // un calcul plus récent a pris le relais
    const ancien = T.route;
    T.route = {ok: true, mode, depart, dest, geometrie: r.geometrie, accroche: [r.depart_accroche, r.arrivee_accroche], etapes: r.etapes || [], distance_m: r.distance_m, duree_s: r.duree_s, t: Date.now(), calcule_a: Date.now()};
    zone.textContent = "";
    zone.append(el("div", {}, el("b", {}, tj("engine/static/engine/js/geo_outils.js:241:44") + MODES[mode][0].toLowerCase() + ") : "), A.fmtM(r.distance_m) + " · " + A.fmtDuree(r.duree_s) + (r.detour != null ? " · ×" + r.detour + tj("engine/static/engine/js/geo_outils.js:241:200") : "")),
      el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_outils.js:242:39") + A.fmtM(r.direct_m) + "."));
    if (r.couloir_m) zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_outils.js:243:66") + Math.round(r.couloir_m) + tj("engine/static/engine/js/geo_outils.js:243:152") + Math.round(r.ecart_max_m) + " m)."));
    r.avertissements.forEach(m => zone.append(el("div", {class: "geo-note"}, "⚠ " + m)));
    if (window.GEO_ANNOT && window.GEO_ANNOT.croise) {   // zones stratégiques / de danger traversées ou frôlées par ce tracé
      const croisees = window.GEO_ANNOT.croise(r.geometrie), natures = window.GEO_ANNOT.natures || {};
      croisees.forEach(x => zone.append(el("div", {class: "geo-note", style: x.a.nature === "danger" ? "color:var(--texte-erreur,#ff8a80)" : ""}, tj("engine/static/engine/js/geo_outils.js:247:128") + (x.d === 0 ? tj("engine/static/engine/js/geo_outils.js:dans") : tj("engine/static/engine/js/geo_outils.js:a_distance_de", {v1: A.fmtM(x.d)})) + tj("engine/static/engine/js/geo_outils.js:247:198") + ((natures[x.a.nature] || {}).lib || "").toLowerCase() + " « " + (x.a.titre || tj("engine/static/engine/js/geo_outils.js:247:290")) + " »." + (x.a.note ? " " + x.a.note.replace(/\s+/g, " ").slice(0, 120) : ""))));
      const cle = dest.join(",") + "|" + mode + "|" + croisees.filter(x => x.a.nature === "danger").map(x => x.a.id).join(",");
      if (croisees.some(x => x.a.nature === "danger") && T.dangerAvise !== cle) { T.dangerAvise = cle; aviser("danger", tj("engine/static/engine/js/geo_outils.js:249:122") + croisees.filter(x => x.a.nature === "danger").map(x => "« " + (x.a.titre || tj("engine/static/engine/js/geo_outils.js:249:251")) + " »").join(", ") + ".", tj("engine/static/engine/js/geo_outils.js:249:291")); }
    }
    const resume = A.fmtM(r.distance_m) + " · " + minutes(r.duree_s);
    if (!memeCible(ancien)) aviser("nouveau", tj("engine/static/engine/js/geo_outils.js:252:48") + (T.dest.nom || tj("engine/static/engine/js/geo_outils.js:252:84")) + " : " + resume + ".", tj("engine/static/engine/js/geo_outils.js:252:126"));
    else if (!ancien.ok || ancien.hors_ligne) aviser("retabli", tj("engine/static/engine/js/geo_outils.js:253:45") + resume + ".", tj("engine/static/engine/js/geo_outils.js:253:100"));
    else if (parcoursChange(ancien.geometrie, r.geometrie)) {
      const pr = surTrace(ancien.geometrie, T.depart.lat, T.depart.lon), d = r.duree_s - (ancien.duree_s || 0) * (1 - pr.s / (pr.total || 1));   // écart de durée : nouveau trajet - reste de l'ancien
      aviser("modifie", tj("engine/static/engine/js/geo_outils.js:256:26") + resume + (Math.abs(d) >= 60 ? " (" + (d > 0 ? "+" : "−") + minutes(Math.abs(d)) + tj("engine/static/engine/js/geo_outils.js:256:134") : "") + ".", tj("engine/static/engine/js/geo_outils.js:256:174"));
    }
    if (T.dernierAvis) zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_outils.js:dernier_avis", {v1: new Date(T.dernierAvis.t).toLocaleTimeString(), v2: T.dernierAvis.message})));
  } catch (e) {
    if (n !== T.nr) return;
    const ancien = T.route;
    if (e && (e.hors_session || e.hors_ligne) && ancien && ancien.ok && ancien.geometrie && ancien.geometrie.length > 1 && memeCible(ancien)) {
      // Connexion absente ou session terminée : le tracé déjà calculé n'est PAS perdu. Il reste affiché, daté, et sera recalculé dès que la connexion revient.
      const premiere = !ancien.hors_ligne;
      T.route = Object.assign({}, ancien, {hors_ligne: true, t: Date.now()});
      zone.innerHTML = resumeAvant && !ancien.hors_ligne ? resumeAvant : "";
      if (ancien.hors_ligne || !zone.firstChild) zone.append(el("div", {}, el("b", {}, tj("engine/static/engine/js/geo_outils.js:282:89")), A.fmtM(ancien.distance_m) + " · " + minutes(ancien.duree_s)));
      zone.append(el("div", {class: "geo-note", id: "ot-hors-ligne", style: "color:var(--texte-erreur,#ff8a80)"}, "⚠ " + (e.hors_session ? tj("engine/static/engine/js/geo_outils.js:283:141") : tj("engine/static/engine/js/geo_outils.js:283:162")) + tj("engine/static/engine/js/geo_outils.js:283:187") + new Date(ancien.calcule_a || ancien.t).toLocaleTimeString() + tj("engine/static/engine/js/geo_outils.js:283:273")));
      if (premiere) aviser("hors_ligne", (e.hors_session ? tj("engine/static/engine/js/geo_outils.js:284:61") : tj("engine/static/engine/js/geo_outils.js:284:82")) + tj("engine/static/engine/js/geo_outils.js:284:107"), tj("engine/static/engine/js/geo_outils.js:284:178"));
      surveillerConnexion();
      redessiner();
      return;
    }
    T.route = {ok: false, mode, depart, dest, t: Date.now()};   // refus mémorisé : pas de nouvel appel tant que le départ ne bouge pas de plus de 25 m
    zone.textContent = tj("engine/static/engine/js/geo_outils.js:263:25") + (e && (e.erreur || e.message) ? (e.erreur || e.message) : tj("engine/static/engine/js/geo_outils.js:lbl_service_indispo")) + tj("engine/static/engine/js/geo_outils.js:263:145");
    if (!memeCible(ancien) || ancien.ok) aviser("perdu", tj("engine/static/engine/js/geo_outils.js:264:59") + (e && (e.erreur || e.message) ? (e.erreur || e.message) : tj("engine/static/engine/js/geo_outils.js:lbl_service_indispo")) + tj("engine/static/engine/js/geo_outils.js:264:184"), tj("engine/static/engine/js/geo_outils.js:264:211"));
  }
  redessiner();
}

// ── Position actuelle (polling ou manuelle) et destination : on compare simplement où l'on est à où l'on va ──
function majSuivi() {
  const n = $("ot-suivi"); if (!n) return;
  const c = $("ot-auto-pos"); if (c) c.checked = !!(T.suivi && T.suivi.mode === "gps");   // la case reflète toujours l'état réel (les boutons manuels l'arrêtent)
  syncLiens();
  n.textContent = T.suivi ? tj("engine/static/engine/js/geo_outils.js:274:30") + (T.suivi.mode === "objet" ? tj("engine/static/engine/js/geo_outils.js:274:89") : tj("engine/static/engine/js/geo_outils.js:274:111")) + tj("engine/static/engine/js/geo_outils.js:274:134") + T.suivi.s + " s." : tj("engine/static/engine/js/geo_outils.js:274:172");
}
function messageGps(e) {
  return e && e.code === 1 ? tj("engine/static/engine/js/geo_outils.js:277:31")
    : tj("engine/static/engine/js/geo_outils.js:278:8") + (e && e.message ? e.message : tj("engine/static/engine/js/geo_outils.js:lbl_signal_absent")) + tj("engine/static/engine/js/geo_outils.js:278:89");
}
function ouSuisJe() {   // une seule lecture, sur demande : fonctionne même si la mise à jour automatique est décochée
  if (!navigator.geolocation) { A.toast(tj("engine/static/engine/js/geo_outils.js:281:42"), true); return; }
  A.toast(tj("engine/static/engine/js/geo_outils.js:282:12"));
  navigator.geolocation.getCurrentPosition(p => { poserActuelle(p.coords.latitude, p.coords.longitude, tj("engine/static/engine/js/geo_outils.js:283:105"), true); A.centrerSur(p.coords.latitude, p.coords.longitude, 15); A.toast(tj("engine/static/engine/js/geo_outils.js:283:192") + Math.round(p.coords.accuracy) + " m)."); },
    e => A.toast(messageGps(e), true), {enableHighAccuracy: true, timeout: 10000, maximumAge: 0});
}
function arreterSuivi() { if (T.suivi) { clearInterval(T.suivi.timer); T.suivi = null; } majSuivi(); }
// Vitesse réelle de déplacement (km/h) : longueur du chemin parcouru sur les dernières 90 s / durée écoulée (au moins 2 points, 3 s) ; sous 2 m de dérive on dit « à l'arrêt ».
function majVitesseReelle(lat, lon, vitesseConnue) {
  const t = Date.now(), h = T.hist;
  if (!h.length || A.hav(h[h.length - 1].lat, h[h.length - 1].lon, lat, lon) > 0 || t - h[h.length - 1].t > 1000) h.push({lat, lon, t});
  while (h.length > 2 && (t - h[0].t > 90000 || h.length > 30)) h.shift();
  if (vitesseConnue != null) { T.vitReelle = vitesseConnue; return; }   // objet suivi : vitesse tirée des horodatages de la table
  const dt = (h[h.length - 1].t - h[0].t) / 1000;
  if (h.length < 2 || dt < 3) { T.vitReelle = null; return; }
  let d = 0; for (let i = 1; i < h.length; i++) d += A.hav(h[i - 1].lat, h[i - 1].lon, h[i].lat, h[i].lon);
  T.vitReelle = d < 2 ? 0 : d / dt * 3.6;
}
function poserActuelle(lat, lon, nom, gps, vitesse) { noterPosition(lat, lon); majVitesseReelle(lat, lon, vitesse); T.depart = {lat, lon, nom: nom || tj("engine/static/engine/js/geo_outils.js:287:81"), t: Date.now()}; majDepart(); majReste(); redessiner(); if (gps) { partagerPosition(lat, lon); if (window.GEO_ANNOT && window.GEO_ANNOT.surveiller) window.GEO_ANNOT.surveiller("moi", tj("engine/static/engine/js/geo_outils.js:lbl_vous"), lat, lon); } }   // GPS : partagé dans la table, et confronté aux zones d'alerte
function lirePosition() {
  if (!T.suivi) return;
  if (T.suivi.mode === "objet") {
    const o = S.D && !S.D.vide && S.D.objets.find(x => x.id === T.suivi.id && x.lat), p = o && dernier(o, o.lat.length - 1);
    if (p) { const e = etatObjet(o); poserActuelle(p.lat, p.lon, o.nom, false, e && e.vitesse != null ? e.vitesse : undefined); }
    return;
  }
  if (!navigator.geolocation) { A.toast(tj("engine/static/engine/js/geo_outils.js:295:42"), true); arreterSuivi(); return; }
  navigator.geolocation.getCurrentPosition(p => poserActuelle(p.coords.latitude, p.coords.longitude, tj("engine/static/engine/js/geo_outils.js:296:103"), true),
    e => { A.toast(messageGps(e), true); if (e.code === 1) arreterSuivi(); }, {enableHighAccuracy: true, timeout: 8000, maximumAge: 0});
}
function demarrerSuivi(mode, id) {
  T.demarre = true; try { arreterSuivi(); } finally { T.demarre = false; }
  const sec = Math.max(2, nombre("ot-poll", 10));
  T.suivi = {mode, id, s: sec, timer: setInterval(lirePosition, sec * 1000)};
  majSuivi(); lirePosition();
}
async function majReste() {
  const r = $("ot-reste"); if (!r) return;
  if (!T.dest) { r.textContent = ""; majRoute(false); return; }
  if (!T.depart) { r.textContent = tj("engine/static/engine/js/geo_outils.js:position_actuelle_inconnue"); majRoute(false); return; }
  const n = ++T.sr;
  try {
    const vs = nombre("ot-vitesse", 0), vr = T.vitReelle, v = vs > 0 ? vs : (vr != null && vr >= 1 ? vr : 0), m = await calcul({action: "mesurer", points: [[T.depart.lat, T.depart.lon], [T.dest.lat, T.dest.lon]], vitesse_kmh: v > 0 ? v : undefined});
    if (n !== T.sr) return;
    const e = m.etapes[0]; r.textContent = "";
    r.append(el("div", {}, el("b", {}, "Reste : "), A.fmtM(m.total_m) + " · cap " + fmtCap(e.cap_initial) + (m.duree_s != null ? " · " + A.fmtDuree(m.duree_s) : "")),
      el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_outils.js:315:39") + new Date(T.depart.t || Date.now()).toLocaleTimeString() + tj("engine/static/engine/js/geo_outils.js:315:144")));
    // Temps nécessaire d'après la vitesse RÉELLE : distance restante (par le tracé des routes s'il est valide, sinon en ligne droite) / vitesse mesurée
    const ligne = el("div", {class: "geo-note", id: "ot-vit-reelle"});
    let restant = m.total_m, via = tj("engine/static/engine/js/geo_outils.js:356:37");
    if (routeAffichable() && T.route.geometrie && T.route.geometrie.length > 1) { const pr = surTrace(T.route.geometrie, T.depart.lat, T.depart.lon); if (pr.d <= 150) { restant = Math.max(0, pr.total - pr.s); via = "route"; } }
    if (vr == null) ligne.textContent = tj("engine/static/engine/js/geo_outils.js:358:42");
    else if (vr < 1) ligne.textContent = tj("engine/static/engine/js/geo_outils.js:359:43");
    else {
      const sec = restant / (vr / 3.6);
      ligne.append(el("b", {}, tj("engine/static/engine/js/geo_outils.js:362:33")), tj("engine/static/engine/js/geo_outils.js:lbl_kmh_reste", {v1: vr.toFixed(1), v2: A.fmtM(restant)}) + " (" + via + ") · " + A.fmtDuree(sec) + tj("engine/static/engine/js/geo_outils.js:363:179") + new Date(Date.now() + sec * 1000).toLocaleTimeString());
    }
    r.append(ligne);
    if (m.total_m < 30) { if (!T.arrive) { T.arrive = true; A.toast(tj("engine/static/engine/js/geo_outils.js:lbl_destination_atteinte")); } } else T.arrive = false;
  } catch (e) { /* toast déjà affiché */ }
  majRoute(false);
}
function majDepart() {
  $("ot-depart").textContent = T.depart ? tj("engine/static/engine/js/geo_outils.js:321:44") + (T.depart.nom ? T.depart.nom + " — " : "") + T.depart.lat.toFixed(5) + ", " + T.depart.lon.toFixed(5) : tj("engine/static/engine/js/geo_outils.js:321:162");
}

// ── Projection à l'estime ────────────────────────────────────────────────────
function etatObjet(o) {   // position, cap (°) et vitesse (km/h) d'après les deux derniers points datés avant le curseur
  const p = dernier(o, S.k); if (!p) return null;
  let q = null;
  for (let i = p.i - 1; i >= 0 && !q; i--) if (o.lat[i] != null && o.lon[i] != null && S.D.t[i] < S.D.t[p.i]) q = {lat: o.lat[i], lon: o.lon[i], t: S.D.t[i]};
  if (!q) return {lat: p.lat, lon: p.lon, cap: null, vitesse: null};
  const dt = S.D.t[p.i] - q.t, d = A.hav(q.lat, q.lon, p.lat, p.lon);
  const phi1 = q.lat * Math.PI / 180, phi2 = p.lat * Math.PI / 180, dl = (p.lon - q.lon) * Math.PI / 180;
  const cap = (Math.atan2(Math.sin(dl) * Math.cos(phi2), Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dl)) * 180 / Math.PI + 360) % 360;
  return {lat: p.lat, lon: p.lon, cap: d > 0.5 ? cap : null, vitesse: dt > 0 ? d / dt * 3.6 : null};
}
async function projeter() {
  const o = S.D && !S.D.vide && S.D.objets.find(x => x.id === S.selection && estTrajet(x) && x.lat);
  let origine = null, nom = tj("engine/static/engine/js/geo_outils.js:337:30");
  if (o) { const e = etatObjet(o); if (e) { origine = e; nom = o.nom; if ($("ot-cap").value === "" && e.cap != null) $("ot-cap").value = Math.round(e.cap); if ($("ot-vit").value === "" && e.vitesse != null) $("ot-vit").value = Math.round(e.vitesse); } }
  else if (T.repere) origine = {lat: T.repere[0], lon: T.repere[1]};
  if (!origine) { A.toast(tj("engine/static/engine/js/geo_outils.js:340:28"), true); return; }
  const cap = nombre("ot-cap", null), v = nombre("ot-vit", null);
  if (cap == null || v == null) { A.toast(tj("engine/static/engine/js/geo_outils.js:342:44"), true); return; }
  const horizons = String($("ot-horizons").value).split(/[\s,;]+/).map(Number).filter(h => Number.isFinite(h) && h >= 0);
  if (!horizons.length) { A.toast(tj("engine/static/engine/js/geo_outils.js:344:36"), true); return; }
  try {
    const r = await calcul({action: "projeter", lat: origine.lat, lon: origine.lon, cap, vitesse_ms: v / 3.6, horizons_s: horizons});
    T.projection = {origine, nom, positions: r.positions};
    const res = $("ot-resultat"); res.textContent = "";
    res.append(el("div", {}, el("b", {}, nom + " : "), "cap " + fmtCap(cap) + ", " + v.toFixed(0) + tj("engine/static/engine/js/geo_outils.js:349:102")));
    r.positions.forEach(p => res.append(el("div", {class: "geo-note"}, "+" + A.fmtDuree(p.t_s) + " : " + A.fmtM(p.distance_m) + " → " + p.lat.toFixed(5) + ", " + p.lon.toFixed(5))));
    redessiner();
  } catch (e) { /* toast déjà affiché */ }
}

// ── Matrice des distances ────────────────────────────────────────────────────
async function matrice() {
  const pos = positionsActuelles();
  if (pos.length < 2) { A.toast(tj("engine/static/engine/js/geo_outils.js:358:34"), true); return; }
  if (pos.length > 150) { A.toast(tj("engine/static/engine/js/geo_outils.js:359:36"), true); return; }
  try {
    const m = await calcul({action: "matrice", points: pos.map(p => [p.lat, p.lon]), noms: pos.map(p => p.nom)});
    const contenu = [];
    if (pos.length <= 25) {
      const tab = el("table", {style: "border-collapse:collapse"});
      tab.append(el("tr", {}, el("th", {}), ...m.noms.map(n => el("th", {style: "padding:2px 6px;text-align:right"}, n))));
      m.noms.forEach((n, i) => tab.append(el("tr", {}, el("th", {style: "padding:2px 6px;text-align:left"}, n),
        ...m.distances_m[i].map((d, j) => el("td", {style: "padding:2px 6px;text-align:right" + (i === j ? ";color:var(--muted)" : ""), title: i === j ? "" : n + " → " + m.noms[j] + " : cap " + fmtCap(m.caps[i][j])}, i === j ? "—" : A.fmtM(d))))));
      contenu.push(tab, el("div", {class: "geo-note", style: "margin-top:4px"}, tj("engine/static/engine/js/geo_outils.js:368:82")));
    } else contenu.push(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_outils.js:369:57")));
    contenu.push(el("div", {style: "margin-top:6px"}, el("b", {}, tj("engine/static/engine/js/geo_outils.js:370:68"))));
    m.plus_proche.forEach(p => contenu.push(el("div", {}, p.de + " → " + p.vers + " : " + A.fmtM(p.distance_m))));
    contenu.push(el("button", {class: "geo-btn", style: "margin-top:6px", onclick: () => telecharger("matrice_distances.csv", "\uFEFF;" + m.noms.map(n => '"' + n.replace(/"/g, '""') + '"').join(";") + "\n" +
      m.distances_m.map((l, i) => '"' + m.noms[i].replace(/"/g, '""') + '";' + l.join(";")).join("\n") + "\n", "text/csv")}, tj("engine/static/engine/js/geo_outils.js:373:127")));
    panneau(tj("engine/static/engine/js/geo_outils.js:374:14") + pos.length + " objets)", ...contenu);
  } catch (e) { /* toast déjà affiché */ }
}

// ── Géorepérage ──────────────────────────────────────────────────────────────
function distincts() {   // « positions distinctes » : chaque point d'une colonne est une position INDÉPENDANTE (jugée seule, jamais comme une trajectoire)
  const pts = [];
  if (S.D && !S.D.vide) S.D.objets.filter(o => S.visibles[o.id] && !estTrajet(o) && o.points).forEach(o => o.points.forEach((p, i) => { if (p && p[0] != null && p[1] != null) pts.push({id: o.id + "#" + i, nom: o.nom + " · " + (i + 1), lat: p[0], lon: p[1]}); }));
  return pts.slice(0, 5000);
}
async function verifierZones(silencieux) {
  const objets = visibles().map(o => ({id: o.id, nom: o.nom, points: trace(o, S.k)})).filter(o => o.points.length), seuls = distincts();
  if (!objets.length && !seuls.length) { if (!silencieux) A.toast(tj("engine/static/engine/js/geo_outils.js:386:68"), true); return; }
  try {
    const r = objets.length ? await calcul({action: "zones", objets}) : {resultats: [], dedans: []};
    const rp = seuls.length ? await calcul({action: "zones_points", points: seuls}) : {resultats: []};
    T.zones = r;
    const cles = new Set(r.dedans.map(x => x.objet + "|" + x.zone)), clesP = new Set();
    rp.resultats.forEach(x => x.dedans.forEach(q => clesP.add(q.objet + "|" + x.zone)));
    if (silencieux && T.dedansAvant) r.dedans.forEach(x => { if (!T.dedansAvant.has(x.objet + "|" + x.zone)) A.toast(x.nom + tj("engine/static/engine/js/geo_outils.js:393:127") + x.zone_nom + " ».", true); });
    if (silencieux && T.pointsAvant) rp.resultats.forEach(x => { const n = x.dedans.filter(q => !T.pointsAvant.has(q.objet + "|" + x.zone)).length; if (n) A.toast(n + tj("engine/static/engine/js/geo_outils.js:394:169") + x.zone_nom + " ».", true); });
    T.dedansAvant = cles; T.pointsAvant = clesP;
    const zone = $("ot-zones"); zone.textContent = "";
    if (!r.resultats.length && !rp.resultats.length) { zone.append(tj("engine/static/engine/js/geo_outils.js:447:69")); return; }
    if (!r.dedans.length && r.resultats.length) zone.append(el("div", {}, tj("engine/static/engine/js/geo_outils.js:398:76")));
    r.resultats.forEach(x => {
      const evts = x.evenements.map(e => ({entree: tj("engine/static/engine/js/geo_outils.js:450:53"), sortie: "sortie", present_au_debut: tj("engine/static/engine/js/geo_outils.js:400:99")}[e.type]) + (e.t != null ? " " + fmtT(e.t) : " (point " + (e.indice + 1) + ")")).slice(-3).join(" ; ");
      zone.append(el("button", {class: "geo-alerte " + (x.dedans ? "haute" : ""), title: evts, onclick: () => { const o = S.D.objets.find(q => q.id === x.objet), p = o && dernier(o, S.k); if (p) A.centrerSur(p.lat, p.lon, 14); }},
        (x.dedans ? "⚠ " : "") + x.nom + " · « " + x.zone_nom + " » : " + (x.dedans ? tj("engine/static/engine/js/geo_outils.js:dedans") : tj("engine/static/engine/js/geo_outils.js:a_distance", {v1: A.fmtM(x.distance_m)})) +
        (x.entrees ? " · " + x.entrees + tj("engine/static/engine/js/geo_outils.js:453:43") : "") + (x.duree_dedans_s ? " · " + A.fmtDuree(x.duree_dedans_s) + " dedans" : "")));
    });
    rp.resultats.forEach(x => {   // positions distinctes : combien dedans, lesquelles, et sinon la plus proche
      const premiers = x.dedans.slice(0, 4).map(q => q.nom).join(", ") + (x.nb_dedans > 4 ? "…" : "");
      zone.append(el("button", {class: "geo-alerte " + (x.nb_dedans ? "haute" : ""), title: tj("engine/static/engine/js/geo_outils.js:407:94"),
        onclick: () => { const q = x.dedans[0] || null; if (q) A.centrerSur(q.lat, q.lon, 14); }},
        (x.nb_dedans ? "⚠ " : "") + tj("engine/static/engine/js/geo_outils.js:positions_distinctes", {v1: x.zone_nom}) + (x.nb_dedans ? tj("engine/static/engine/js/geo_outils.js:n_dedans", {v1: x.nb_dedans, v2: premiers}) : x.plus_proche ? tj("engine/static/engine/js/geo_outils.js:409:167") + A.fmtM(x.plus_proche.distance_m) : tj("engine/static/engine/js/geo_outils.js:aucune"))));
    });
  } catch (e) { /* toast déjà affiché */ }
}

// ── Enregistrement en annotation (zone ou trajectoire) ───────────────────────
async function enregistrer(type, geometrie, titre) {
  try {
    await A.api(URL_AN, {method: "POST", body: JSON.stringify({action: "creer", type, geometrie, titre, couleur: $("an-couleur") ? $("an-couleur").value : gyCouleur("--forme-orange", "#f5a524"), note: ""})});
    A.toast(tj("engine/static/engine/js/geo_outils.js:418:14")); if (window.GEO_ANNOT) window.GEO_ANNOT.recharger();
  } catch (e) { /* toast déjà affiché */ }
}

// ── Exports ──────────────────────────────────────────────────────────────────
async function exporter(format) {
  const objets = visibles().map(o => ({id: o.id, nom: o.nom, points: trace(o, o.lat.length - 1).map(p => S.D.temps_reel ? p : [p[0], p[1]])})).filter(o => o.points.length);
  try {
    const r = await calcul({action: "exporter", format, objets, annotations: true});
    telecharger(r.nom_fichier, r.contenu, r.mime); A.toast("Export " + format.toUpperCase() + " : " + objets.length + tj("engine/static/engine/js/geo_outils.js:427:120"));
  } catch (e) { /* toast déjà affiché */ }
}

// ── Dessin ───────────────────────────────────────────────────────────────────
function chemin(ctx, pts, ferme) {
  ctx.beginPath(); pts.forEach((p, i) => { const [x, y] = A.versEcran(p[0], p[1]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); if (ferme) ctx.closePath();
}
// Trajet = évolution : tronçons de la couleur du plus sombre (départ) au plus clair (dernière position connue). Au plus ~80 tracés par trajet.
function traitDegrade(ctx, pts, couleur, largeur) {
  const n = pts.length - 1, par = Math.max(1, Math.ceil(n / 80));
  ctx.lineWidth = largeur;
  for (let i = 0; i < n; i += par) {
    const fin = Math.min(n, i + par);
    ctx.strokeStyle = gyNuance(couleur, (i + fin) / 2 / n);
    chemin(ctx, pts.slice(i, fin + 1), false); ctx.stroke();
  }
}
function dessiner(ctx) {
  ctx.save(); ctx.lineJoin = "round"; ctx.font = "12px sans-serif";
  if (T.cercle) { chemin(ctx, T.cercle.geometrie, true); ctx.strokeStyle = gyCouleur("--forme-vert", "#9ad06b"); ctx.fillStyle = gyCouleur("--forme-vert", "#9ad06b"); ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 0.12; ctx.fill(); ctx.globalAlpha = 1;
    const [x, y] = A.versEcran(T.cercle.lat, T.cercle.lon); ctx.beginPath(); ctx.arc(x, y, 3, 0, 2 * Math.PI); ctx.fill(); ctx.fillText(A.fmtM(T.cercle.rayon), x + 6, y - 6); }
  if (T.corridor) { chemin(ctx, T.corridor.geometrie, true); ctx.strokeStyle = gyCouleur("--forme-bleu", "#7aa2ff"); ctx.fillStyle = gyCouleur("--forme-bleu", "#7aa2ff"); ctx.lineWidth = 2; ctx.stroke(); ctx.globalAlpha = 0.12; ctx.fill(); ctx.globalAlpha = 1; }
  if (T.mesure.length) {
    ctx.strokeStyle = gyCouleur("--forme-jaune", "#ffd166"); ctx.fillStyle = gyCouleur("--forme-jaune", "#ffd166"); ctx.lineWidth = 2; ctx.setLineDash([7, 4]); chemin(ctx, T.mesure, false); ctx.stroke(); ctx.setLineDash([]);
    T.mesure.forEach((p, i) => { const [x, y] = A.versEcran(p[0], p[1]); ctx.beginPath(); ctx.arc(x, y, 4, 0, 2 * Math.PI); ctx.fill(); ctx.fillText(String(i + 1), x + 6, y - 6); });
    if (T.etapes && T.etapes.etapes.length === T.mesure.length - 1) T.etapes.etapes.forEach((e, i) => {
      const [x0, y0] = A.versEcran(T.mesure[i][0], T.mesure[i][1]), [x1, y1] = A.versEcran(T.mesure[i + 1][0], T.mesure[i + 1][1]);
      if (Math.hypot(x1 - x0, y1 - y0) > 70) { ctx.fillStyle = gyCouleur("--texte-sombre", "#fff"); ctx.fillText(A.fmtM(e.distance_m) + " · " + fmtCap(e.cap_initial), (x0 + x1) / 2 + 6, (y0 + y1) / 2 - 6); }
    });
  }
  if (T.projection) {
    const p = T.projection, pts = [[p.origine.lat, p.origine.lon]].concat(p.positions.map(q => [q.lat, q.lon]));
    ctx.strokeStyle = gyCouleur("--forme-orange", "#ff9f6b"); ctx.fillStyle = gyCouleur("--forme-orange", "#ff9f6b"); ctx.lineWidth = 2; ctx.setLineDash([3, 5]); chemin(ctx, pts, false); ctx.stroke(); ctx.setLineDash([]);
    p.positions.forEach(q => { const [x, y] = A.versEcran(q.lat, q.lon); ctx.beginPath(); ctx.arc(x, y, 4, 0, 2 * Math.PI); ctx.fill(); ctx.fillText("+" + A.fmtDuree(q.t_s), x + 7, y - 7); });
  }
  if (T.depart) { const [x, y] = A.versEcran(T.depart.lat, T.depart.lon); ctx.fillStyle = gyCouleur("--ok", "#2ecc71"); ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 8, 0, 2 * Math.PI); ctx.fill(); ctx.stroke(); ctx.fillStyle = gyCouleur("--texte-sombre", "#fff"); ctx.fillText(T.depart.nom === "Ma position" || T.suivi ? tj("engine/static/engine/js/geo_outils.js:lbl_moi") : tj("engine/static/engine/js/geo_outils.js:453:316"), x + 11, y + 4); }
  if (T.dest) {
    if (routeAffichable()) {   // tracé par les routes : trait plein bleu ; la ligne droite rouge en pointillés est dessinée juste après, par-dessus
      chemin(ctx, T.route.geometrie, false); const k = T.livreur ? 1.7 : 1; ctx.strokeStyle = gyCouleur("--forme-fond", "#0b1e33"); ctx.lineWidth = 6 * k; ctx.stroke(); ctx.strokeStyle = gyCouleur("--forme-bleu", "#3aa0ff"); ctx.lineWidth = 3.5 * k; ctx.stroke();
      T.route.accroche.forEach((a, i) => { const p = i ? T.dest : T.depart; if (a && a[2] > 20) { chemin(ctx, [[p.lat, p.lon], [a[0], a[1]]], false); ctx.strokeStyle = gyCouleur("--forme-bleu", "#3aa0ff"); ctx.lineWidth = 1.5; ctx.setLineDash([2, 3]); ctx.stroke(); ctx.setLineDash([]); } });
    }
    if (courtVisible()) {   // chemin le plus court dans le couloir : trait violet fin posé sur le tracé par les routes (bleu), sous la ligne droite rouge
      chemin(ctx, T.court.geometrie, false); const k = T.livreur ? 1.7 : 1; ctx.strokeStyle = gyCouleur("--forme-fond", "#0b1e33"); ctx.lineWidth = 5 * k; ctx.stroke(); ctx.strokeStyle = COULEUR_COURT; ctx.lineWidth = 2.5 * k; ctx.stroke();
    }
    if (T.depart) { ctx.strokeStyle = gyCouleur("--danger", "#e74c3c"); ctx.lineWidth = 2; ctx.setLineDash([8, 5]); chemin(ctx, [[T.depart.lat, T.depart.lon], [T.dest.lat, T.dest.lon]], false); ctx.stroke(); ctx.setLineDash([]); }
    const [x, y] = A.versEcran(T.dest.lat, T.dest.lon); ctx.fillStyle = gyCouleur("--danger", "#e74c3c"); ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 8, 0, 2 * Math.PI); ctx.fill(); ctx.stroke(); ctx.fillStyle = gyCouleur("--texte-sombre", "#fff"); ctx.fillText("Destination", x + 11, y + 4);
  }
  if (T.repere) { const [x, y] = A.versEcran(T.repere[0], T.repere[1]); ctx.strokeStyle = gyCouleur("--forme-clair", "#fff"); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x - 10, y); ctx.lineTo(x + 10, y); ctx.moveTo(x, y - 10); ctx.lineTo(x, y + 10); ctx.stroke(); ctx.beginPath(); ctx.arc(x, y, 5, 0, 2 * Math.PI); ctx.stroke(); }
  ctx.restore();
}

// ── Interface ────────────────────────────────────────────────────────────────
document.querySelectorAll("[data-ot]").forEach(b => { b.onclick = () => activer(b.dataset.ot); });
$("ot-aller").onclick = async () => {
  try {
    const c = await calcul({action: "lire", texte: $("ot-coord").value});
    A.centrerSur(c.lat, c.lon, 15); T.repere = [c.lat, c.lon]; montrerCoordonnees(c); redessiner();
  } catch (e) { /* toast déjà affiché */ }
};
$("ot-coord").addEventListener("keydown", e => { if (e.key === "Enter") $("ot-aller").click(); });
$("ot-vitesse").onchange = mesurer;
$("ot-mesure-retirer").onclick = () => { T.mesure.pop(); T.corridor = null; mesurer(); redessiner(); };
$("ot-mesure-effacer").onclick = () => { T.mesure = []; T.etapes = null; T.corridor = null; montrerMesure(); redessiner(); };
$("ot-corridor").onclick = async () => {
  if (T.mesure.length < 2) { A.toast(tj("engine/static/engine/js/geo_outils.js:482:39"), true); return; }
  const largeur = nombre("ot-largeur", 0);
  try { const r = await calcul({action: "corridor", points: T.mesure, largeur_m: largeur}); T.corridor = {geometrie: r.geometrie, largeur}; montrerZone(tj("engine/static/engine/js/geo_outils.js:484:154") + A.fmtM(largeur) + tj("engine/static/engine/js/geo_outils.js:484:189"), r.geometrie); redessiner(); } catch (e) { /* toast déjà affiché */ }
};
$("ot-cercle-effacer").onclick = () => { T.cercle = null; redessiner(); };
if ($("ot-enreg-trace")) {
  $("ot-enreg-trace").onclick = () => { if (T.mesure.length < 2) A.toast(tj("engine/static/engine/js/geo_outils.js:488:75"), true); else enregistrer("trajectoire", T.mesure, tj("engine/static/engine/js/geo_outils.js:488:154") + (T.etapes ? A.fmtM(T.etapes.total_m) : "")); };
  $("ot-enreg-zone").onclick = () => {
    const z = T.corridor || T.cercle;
    if (!z) A.toast(tj("engine/static/engine/js/geo_outils.js:491:22"), true); else enregistrer("zone", z.geometrie, T.corridor && z === T.corridor ? tj("engine/static/engine/js/geo_outils.js:titre_corridor", {v1: A.fmtM(z.largeur)}) : tj("engine/static/engine/js/geo_outils.js:titre_cercle", {v1: A.fmtM(z.rayon)}));
  };
}
$("ot-projeter").onclick = projeter;
$("ot-matrice").onclick = matrice;
$("ot-zones-verifier").onclick = () => verifierZones(false);
$("ot-depart-objet").onclick = () => {
  const o = S.D && !S.D.vide && S.D.objets.find(x => x.id === S.selection && estTrajet(x) && x.lat), p = o && dernier(o, S.k);
  if (!p) { A.toast(tj("engine/static/engine/js/geo_outils.js:499:22"), true); return; }
  T.depart = {lat: p.lat, lon: p.lon, nom: o.nom}; majDepart(); redessiner();
};
$("ot-depart-annuler").onclick = () => { arreterSuivi(); T.depart = null; T.hist = []; T.vitReelle = null; T.dest = null; T.route = null; majReste(); majDepart(); if (T.outil === "itineraire") $("ot-aide").textContent = tj("engine/static/engine/js/geo_outils.js:502:189"); redessiner(); };
$("ot-dest-annuler").onclick = () => { T.dest = null; T.route = null; majReste(); redessiner(); };
(function () {   // réglages du tracé par les routes, insérés juste avant « Reste » (créés ici : le gabarit et ses clés de traduction ne changent pas)
  const reste = $("ot-reste"); if (!reste) return;
  const mode = el("select", {id: "ot-mode-route", title: tj("engine/static/engine/js/geo_outils.js:506:59")}, ...Object.entries(MODES).map(([k, v]) => el("option", {value: k}, v[0])));
  const actif = el("input", {type: "checkbox", id: "ot-route-actif", checked: "checked"});
  mode.onchange = () => majRoute(true); actif.onchange = () => majRoute(true);
  const court = el("input", {type: "checkbox", id: "ot-court-actif", checked: "checked"});   // coché par défaut : toujours affiché, comme la ligne droite
  court.onchange = () => majCourt(true);
  reste.before(el("div", {class: "geo-note"}, el("label", {class: "geo-case"}, actif, tj("engine/static/engine/js/geo_outils.js:511:88"), mode)), el("div", {id: "ot-route", class: "geo-note"}),
    el("div", {class: "geo-note"}, el("label", {class: "geo-case", title: tj("engine/static/engine/js/geo_outils.js:512:76")}, court, tj("engine/static/engine/js/geo_outils.js:512:216"))), el("div", {id: "ot-court", class: "geo-note"}));
})();
(function () {   // « Où suis-je ? » + case « mise à jour automatique » (décochée par défaut) ; décochée, les boutons manuels ci-dessous servent à mettre la position à jour
  const case_ = el("input", {type: "checkbox", id: "ot-auto-pos"});
  case_.onchange = () => { if (case_.checked) { demanderNotifications(); demarrerSuivi("gps"); } else { arreterSuivi(); A.toast(tj("engine/static/engine/js/geo_outils.js:516:130")); } };
  const bloc = el("div", {}, el("button", {class: "geo-btn principal", id: "ot-ou-suis-je", title: tj("engine/static/engine/js/geo_outils.js:517:101"), onclick: ouSuisJe}, tj("engine/static/engine/js/geo_outils.js:517:156")),
    el("label", {class: "geo-case", title: tj("engine/static/engine/js/geo_outils.js:518:45")}, case_, tj("engine/static/engine/js/geo_outils.js:518:149")));
  const ref = ($("ot-poll") && $("ot-poll").parentNode) || $("ot-gps"); ref.before(bloc);
})();
// ── PERSONNES : autant d'itinéraires que l'on veut (liste illimitée), UN SEUL tracé affiché à la fois : celui de la personne choisie ──
// Chaque personne garde son départ, sa destination, son mode, son tracé et ses liens avec la table : départ et/ou destination « liés » à un objet suivi
// (la position se met à jour d'elle-même quand la table change). Changer de personne échange simplement ce qu'affichent T.depart / T.dest / T.route.
(function () {
  const ancre = $("ot-depart"); if (!ancre) return;
  const CLE = "geo-personnes-" + G.tableId, MAX = 5000;
  let pers = [], actif = null, compteur = 0, nomSeq = 0, tousTrajets = true;
  const courante = () => pers.find(p => p.id === actif);
  const nouvelle = nom => ({id: ++compteur, nom: nom || tj("engine/static/engine/js/geo_outils.js:nom_personne", {v1: ++nomSeq}), depart: null, dest: null, mode: modeRoute(), route: null, arrive: false, resume: "", reste: "", lienDep: "", lienDest: "", table: "", partage: false, trajet: [], cache: null, trajetActif: false, trajetVisible: true});
  const deLaTable = o => {   // la personne affichée a choisi une table de positions : on ne propose que ses objets (plusieurs tables analysées : identifiants « <id table>:<colonne> »)
    const p = courante(), t = p && p.table;
    if (!t || !S.D || !S.D.sources || S.D.sources.length < 2) return true;
    return String(t) === String(G.tableId) ? !String(o.id).includes(":") : String(o.id).startsWith(t + ":");
  };
  const objetsTable = () => S.D && !S.D.vide ? S.D.objets.filter(o => estTrajet(o) && o.lat && deLaTable(o)) : [];
  function sauvegarder() {   // coordonnées (clé principale) ; stockage indisponible : on continue en mémoire
    try { localStorage.setItem(CLE, JSON.stringify({actif, compteur, nomSeq, pers: pers.map(p => ({id: p.id, nom: p.nom, depart: p.depart, dest: p.dest, mode: p.mode, lienDep: p.lienDep, lienDest: p.lienDest, table: p.table, partage: p.partage, trajetActif: p.trajetActif, trajetVisible: p.trajetVisible})), tousTrajets})); } catch (e) { /* sans effet */ }
    sauvegarderTraces(); sauvegarderTrajets();
  }
  // Tracés déjà calculés : clé SÉPARÉE (volumineux) pour qu'un stockage plein ne fasse jamais perdre les coordonnées ci-dessus. Seuls les calculs réussis sont gardés,
  // la personne affichée d'abord, dans une limite de taille ; au rechargement ils réapparaissent tout de suite, puis sont recalculés si le départ a bougé (et si la connexion le permet).
  function sauvegarderTraces() {
    try {
      const lot = {}, LIMITE = 1500000;
      let taille = 0;
      const ordre = pers.slice().sort((a, b) => (b.id === actif) - (a.id === actif));
      for (const p of ordre) {
        const r = p.id === actif ? T.route : p.route;
        if (!(r && r.ok && r.geometrie && r.geometrie.length > 1)) continue;
        const x = {mode: r.mode, depart: r.depart, dest: r.dest, geometrie: r.geometrie, accroche: r.accroche, etapes: r.etapes, distance_m: r.distance_m, duree_s: r.duree_s, calcule_a: r.calcule_a || r.t};
        const n = JSON.stringify(x).length;
        if (taille + n > LIMITE) continue;
        taille += n; lot[p.id] = x;
      }
      localStorage.setItem(CLE + "-traces", JSON.stringify(lot));
    } catch (e) { /* stockage plein ou indisponible : les tracés se recalculeront */ }
  }
  function traceConcorde(r, p) { return !!(r && p.dest && p.depart && Array.isArray(r.geometrie) && r.geometrie.length > 1 && r.mode === p.mode && Array.isArray(r.dest) && r.dest[0] === p.dest.lat && r.dest[1] === p.dest.lon); }
  function fixer() {   // la personne affichée -> copie ce que montre la page
    const p = courante(); if (!p) return;
    p.depart = T.depart; p.dest = T.dest; p.route = T.route; p.arrive = T.arrive; p.mode = modeRoute();
    p.resume = $("ot-route") ? $("ot-route").innerHTML : ""; p.reste = $("ot-reste") ? $("ot-reste").innerHTML : "";
  }
  function afficher() {   // ce que montre la page <- la personne choisie
    const p = courante(); if (!p) return;
    T.demarre = true; try { arreterSuivi(); } finally { T.demarre = false; }
    ++T.nr; ++T.sr;
    T.depart = p.depart; T.dest = p.dest; T.route = p.route; T.arrive = p.arrive;
    const m = $("ot-mode-route"); if (m && MODES[p.mode]) m.value = p.mode;
    if ($("ot-route")) { $("ot-route").innerHTML = p.resume || ""; if (!p.resume && p.route && p.route.ok) $("ot-route").textContent = tj("engine/static/engine/js/geo_outils.js:619:137") + new Date(p.route.calcule_a || Date.now()).toLocaleTimeString() + ") : " + A.fmtM(p.route.distance_m) + " · " + minutes(p.route.duree_s) + tj("engine/static/engine/js/geo_outils.js:619:306"); }
    if ($("ot-reste")) $("ot-reste").innerHTML = p.reste || "";
    if (p.route && p.route.hors_ligne) surveillerConnexion();
    majDepart(); majListe(); majTrajetUI(); remplirTables(); majLiensListe(); majPartage();
    if (p.lienDep && objetsTable().some(o => o.id === p.lienDep)) demarrerSuivi("objet", p.lienDep);
    majLiensDest(true); majReste(); redessiner(); sauvegarder();
  }
  function choisir(id) { if (id === actif) return; fixer(); actif = id; afficher(); }
  function majListe() {
    const s = $("ot-pers"); if (!s) return;
    s.textContent = ""; pers.forEach(p => s.append(el("option", {value: String(p.id)}, p.nom + (p.dest ? " → " + (p.dest.nom || "destination") : ""))));
    s.value = String(actif); $("ot-pers-nb").textContent = pers.length + " personne" + (pers.length > 1 ? "s" : "");
  }
  function remplirLien(sel, valeur) {
    sel.textContent = ""; sel.append(el("option", {value: ""}, tj("engine/static/engine/js/geo_outils.js:562:65")));
    objetsTable().forEach(o => sel.append(el("option", {value: String(o.id)}, o.nom || String(o.id))));
    sel.value = valeur && objetsTable().some(o => String(o.id) === String(valeur)) ? String(valeur) : "";
  }
  function majLiensListe() { const p = courante(); if (!p) return; remplirLien($("ot-lien-dep"), p.lienDep); remplirLien($("ot-lien-dest"), p.lienDest); }
  const idObjet = v => { const o = objetsTable().find(x => String(x.id) === String(v)); return o ? o.id : ""; };
  function majLiensDest(force) {   // destination liée : suit la dernière position de l'objet choisi (relue toutes les 5 s, ou à la demande)
    const p = courante(); if (!p || !p.lienDest) return;
    const o = objetsTable().find(x => x.id === p.lienDest), q = o && dernier(o, o.lat.length - 1);
    if (!q) return;
    if (force || !T.dest || A.hav(T.dest.lat, T.dest.lon, q.lat, q.lon) > 10) { T.dest = {lat: q.lat, lon: q.lon, nom: o.nom}; T.arrive = false; majListe(); majReste(); redessiner(); }
  }
  syncLiens = () => {   // un clic manuel (position, coordonnées, « Arrêter ») coupe le lien du départ : la liste reflète toujours l'état réel
    const p = courante(); if (!p || !p.lienDep || T.demarre) return;
    if (!(T.suivi && T.suivi.mode === "objet" && T.suivi.id === p.lienDep)) { p.lienDep = ""; if ($("ot-lien-dep")) $("ot-lien-dep").value = ""; sauvegarder(); }
  };
  function ajouter(nom, extra) { fixer(); const p = Object.assign(nouvelle(nom), extra || {}); pers.push(p); actif = p.id; afficher(); return p; }
  function lierDepart(valeur) {
    const p = courante(); if (!p) return;
    p.lienDep = valeur ? idObjet(valeur) : "";
    if (p.lienDep) { const o = objetsTable().find(x => x.id === p.lienDep); if (o && /^Personne \d+$/.test(p.nom)) { p.nom = o.nom || p.nom; } demarrerSuivi("objet", p.lienDep); A.toast(tj("engine/static/engine/js/geo_outils.js:582:188") + (o ? o.nom : tj("engine/static/engine/js/geo_outils.js:582:221")) + tj("engine/static/engine/js/geo_outils.js:582:234")); }
    else { arreterSuivi(); A.toast(tj("engine/static/engine/js/geo_outils.js:583:37")); }
    majListe(); sauvegarder();
  }
  function lierDestination(valeur) {
    const p = courante(); if (!p) return;
    p.lienDest = valeur ? idObjet(valeur) : "";
    if (p.lienDest) { majLiensDest(true); const o = objetsTable().find(x => x.id === p.lienDest); A.toast(tj("engine/static/engine/js/geo_outils.js:589:108") + (o ? o.nom : tj("engine/static/engine/js/geo_outils.js:589:147")) + tj("engine/static/engine/js/geo_outils.js:589:160")); } else A.toast(tj("engine/static/engine/js/geo_outils.js:lbl_destination_libre"));
    sauvegarder();
  }
  const sel = el("select", {id: "ot-pers", title: tj("engine/static/engine/js/geo_outils.js:592:52")});
  sel.onchange = () => choisir(parseInt(sel.value, 10));
  const selDep = el("select", {id: "ot-lien-dep", title: tj("engine/static/engine/js/geo_outils.js:594:59")});
  const selDest = el("select", {id: "ot-lien-dest", title: tj("engine/static/engine/js/geo_outils.js:595:61")});
  [selDep, selDest].forEach(s => { s.onmousedown = s.onfocus = () => { const v = s.value; remplirLien(s, v); }; });
  selDep.onchange = () => lierDepart(selDep.value); selDest.onchange = () => lierDestination(selDest.value);
  // Table de positions : choisir une table (celles qui ont des colonnes de position) coche ses colonnes dans « Données analysées » ; ses objets deviennent les
  // positions proposées pour lier départ / destination, et la table où la personne partage la sienne.
  const blocsPos = () => Array.from(document.querySelectorAll("#geo-tables .geo-table[data-table]")).filter(b => b.querySelector("input[data-pos]"));
  const nomBloc = b => { const n = b.querySelector(".geo-table-nom"); return n ? (n.firstChild ? String(n.firstChild.textContent) : String(n.textContent)).trim() : tj("engine/static/engine/js/geo_outils.js:601:166") + b.dataset.table; };
  const selTable = el("select", {id: "ot-pers-table", title: tj("engine/static/engine/js/geo_outils.js:602:63")});
  function remplirTables() {
    const p = courante(), v = p ? p.table : "";
    selTable.textContent = ""; selTable.append(el("option", {value: ""}, tj("engine/static/engine/js/geo_outils.js:605:75")));
    blocsPos().forEach(b => selTable.append(el("option", {value: b.dataset.table}, nomBloc(b))));
    selTable.value = Array.from(selTable.options || selTable.children).some(o => o.value === v) ? v : "";
  }
  selTable.onmousedown = selTable.onfocus = remplirTables;
  selTable.onchange = () => {
    const p = courante(); if (!p) return;
    p.table = selTable.value;
    if (p.table) {
      const b = blocsPos().find(x => x.dataset.table === p.table), cases = b ? Array.from(b.querySelectorAll("input[data-pos]")) : [];
      if (cases.length && !cases.some(c => c.checked)) { cases.forEach(c => { c.checked = true; }); cases[0].dispatchEvent(new Event("change")); }   // le même geste que la page : mémorise puis recharge la carte
      A.toast(tj("engine/static/engine/js/geo_outils.js:616:16") + (b ? nomBloc(b) : p.table) + tj("engine/static/engine/js/geo_outils.js:616:73"));
    }
    majLiensListe(); majPartage(); sauvegarder();
  };
  const casePartage = el("input", {type: "checkbox", id: "ot-partager"}), etatPartage = el("div", {id: "ot-partage-etat", class: "geo-note"});
  casePartage.onchange = () => {
    const p = courante(); if (!p) return;
    p.partage = casePartage.checked;
    if (p.partage && p.lienDep) { p.partage = false; casePartage.checked = false; A.toast(tj("engine/static/engine/js/geo_outils.js:624:92"), true); }
    else if (p.partage) { demanderNotifications(); A.toast(tj("engine/static/engine/js/geo_outils.js:625:61") + p.nom + tj("engine/static/engine/js/geo_outils.js:625:147")); }
    dernierPartage = {t: 0, lat: null, lon: null}; majPartage(); sauvegarder();
  };
  let dernierPartage = {t: 0, lat: null, lon: null};
  function majPartage(message) {
    const p = courante(); if (!p) return;
    casePartage.checked = !!p.partage;
    etatPartage.textContent = message || (p.partage ? tj("engine/static/engine/js/geo_outils.js:632:56") + (nomTableCible(p) || tj("engine/static/engine/js/geo_outils.js:632:103")) + tj("engine/static/engine/js/geo_outils.js:632:125") + p.nom + " »." : tj("engine/static/engine/js/geo_outils.js:632:161"));
  }
  const nomTableCible = p => { const b = p.table && blocsPos().find(x => x.dataset.table === p.table); return b ? nomBloc(b) : (G.tableNom || ""); };
  partagerPosition = async (lat, lon) => {   // une position GPS : au plus une écriture / 10 s, et pas si l'on n'a pas bougé de 10 m depuis moins d'une minute
    const p = courante(); if (!p || !p.partage || p.lienDep) return;
    const maintenant = Date.now(), d = dernierPartage.lat == null ? Infinity : A.hav(dernierPartage.lat, dernierPartage.lon, lat, lon);
    if (maintenant - dernierPartage.t < 10000 || (d < 10 && maintenant - dernierPartage.t < 60000)) return;
    dernierPartage = {t: maintenant, lat, lon};
    try {
      await A.api("/api/tables/" + (p.table || G.tableId) + "/geolocalisation/partager/", {method: "POST", body: JSON.stringify({id: p.nom, nom: p.nom, lat, lon})});
      if (courante() === p) majPartage(tj("engine/static/engine/js/geo_outils.js:642:41") + new Date().toLocaleTimeString() + tj("engine/static/engine/js/geo_outils.js:dans_guillemet") + (nomTableCible(p) || tj("engine/static/engine/js/geo_outils.js:642:134")) + " ».");
    } catch (e) {   // refus (lecture seule, imprécision, droits) : le partage s'arrête, le message de la page dit pourquoi
      if (e && (e.hors_session || e.hors_ligne)) { dernierPartage.t = 0; if (courante() === p) majPartage((e.hors_session ? tj("engine/static/engine/js/geo_outils.js:715:126") : tj("engine/static/engine/js/geo_outils.js:715:147")) + tj("engine/static/engine/js/geo_outils.js:715:172")); return; }
      p.partage = false; if (courante() === p) majPartage(tj("engine/static/engine/js/geo_outils.js:644:60") + (e && (e.erreur || e.message) ? (e.erreur || e.message) : tj("engine/static/engine/js/geo_outils.js:644:140")) + "."); sauvegarder();
    }
  };
  const bout = (t, titre, f) => el("button", {class: "geo-btn", title: titre, onclick: f}, t);
  const bloc = el("div", {class: "geo-note", id: "ot-personnes"},
    el("div", {}, el("b", {}, "Personnes : "), sel, " ", el("span", {id: "ot-pers-nb"})),
    el("div", {}, bout(tj("engine/static/engine/js/geo_outils.js:lbl_plus_personne"), tj("engine/static/engine/js/geo_outils.js:650:39"), () => { if (pers.length >= MAX) A.toast(tj("engine/static/engine/js/geo_outils.js:650:156") + MAX + tj("engine/static/engine/js/geo_outils.js:lbl_personnes_atteinte"), true); else ajouter(); }),
      bout(tj("engine/static/engine/js/geo_outils.js:lbl_renommer"), tj("engine/static/engine/js/geo_outils.js:651:25"), async () => { const p = courante(), n = p && await GY.demander(tj("engine/static/engine/js/geo_outils.js:651:119"), p.nom); if (p && n && n.trim()) { p.nom = n.trim().slice(0, 60); majListe(); sauvegarder(); } }),
      bout(tj("engine/static/engine/js/geo_outils.js:652:13"), tj("engine/static/engine/js/geo_outils.js:652:26"), () => {
        const i = pers.findIndex(p => p.id === actif); if (i < 0) return;
        if (pers.length === 1) { pers[0] = Object.assign(nouvelle(pers[0].nom), {id: pers[0].id}); afficher(); return; }
        pers.splice(i, 1); actif = pers[Math.min(i, pers.length - 1)].id; afficher();
      }),
      bout(tj("engine/static/engine/js/geo_outils.js:657:13"), tj("engine/static/engine/js/geo_outils.js:657:39"), () => {
        const dispo = objetsTable().filter(o => !pers.some(p => p.lienDep === o.id)).slice(0, 200);
        if (!dispo.length) { A.toast(tj("engine/static/engine/js/geo_outils.js:659:39"), true); return; }
        fixer(); const vide = pers.length === 1 && !pers[0].depart && !pers[0].dest && !pers[0].lienDep;
        if (vide) { pers.length = 0; }
        dispo.forEach(o => { const p = Object.assign(nouvelle(o.nom || undefined), {lienDep: o.id}); pers.push(p); });
        actif = pers[0 + (vide ? 0 : pers.length - dispo.length)].id; afficher(); A.toast(dispo.length + tj("engine/static/engine/js/geo_outils.js:663:107"));
      })),
    el("div", {}, tj("engine/static/engine/js/geo_outils.js:665:20"), selTable),
    el("div", {}, el("label", {class: "geo-case", title: tj("engine/static/engine/js/geo_outils.js:666:59")}, casePartage, tj("engine/static/engine/js/geo_outils.js:666:259"))), etatPartage,
    el("div", {}, tj("engine/static/engine/js/geo_outils.js:667:20"), selDep), el("div", {}, tj("engine/static/engine/js/geo_outils.js:667:71"), selDest),
    el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_outils.js:668:37")));
  ancre.before(bloc);
  // ── TRAJETS PARCOURUS : le chemin que chaque personne a réellement suivi, sans départ ni destination ──
  // Les positions successives (GPS, position manuelle) s'ajoutent au trajet de la personne affichée tant que « Enregistrer » est coché ; une personne liée à un objet de la
  // table affiche l'historique de cet objet. Tous les trajets visibles sont dessinés ensemble (une couleur par personne). Tout est LOCAL : comme les tracés d'itinéraire,
  // un trajet déjà enregistré reste affiché si la session se termine ou si le réseau tombe (rien n'est demandé au serveur pour le dessiner).
  const MAX_PTS = 20000, PAS_M = 8, BUDGET = 2500000, COULEURS_TRAJET = ["#3aa0ff", "#ff9f6b", "#9ad06b", "#ff6bb5", "#b084ff", "#ffd166", "#4dd8c8", "#ff6b6b"];
  const couleurTrajet = p => COULEURS_TRAJET[Math.abs(p.id) % COULEURS_TRAJET.length];
  const objetLie = p => p.lienDep && S.D && !S.D.vide ? S.D.objets.find(x => x.id === p.lienDep && x.lat) : null;
  function pointsTrajet(p) {   // [[lat, lon, t_ms|null]] : historique de l'objet lié (table) s'il est chargé, sinon le dernier instantané, sinon les positions enregistrées
    const o = objetLie(p);
    if (o) return trace(o, o.lat.length - 1).map(q => [q[0], q[1], S.D.temps_reel ? q[2] * 1000 : null]);
    return p.lienDep ? (p.cache || []) : p.trajet;
  }
  function instantaneTable() { pers.forEach(p => { const o = objetLie(p); if (o) p.cache = pointsTrajet(p).filter((_, i, a) => a.length <= 3000 || i % Math.ceil(a.length / 3000) === 0 || i === a.length - 1); }); }
  let sauvTrajets = 0;
  function sauvegarderTrajets() {   // clé séparée (volumineuse) : un stockage plein ne fait jamais perdre les coordonnées des personnes
    clearTimeout(sauvTrajets); sauvTrajets = setTimeout(() => {
      try {
        const lot = {}; let reste = BUDGET;
        pers.slice().sort((a, b) => (b.id === actif) - (a.id === actif)).forEach(p => {
          const garde = a => { if (!a || !a.length) return null; const n = JSON.stringify(a).length, f = n > reste ? Math.ceil(n / Math.max(reste, 1)) : 1; const r = f > 1 ? a.filter((_, i) => i % f === 0 || i === a.length - 1) : a; reste -= JSON.stringify(r).length; return r; };
          const pts = garde(p.trajet), cache = garde(p.cache);
          if (pts || cache) lot[p.id] = {pts, cache};
        });
        localStorage.setItem(CLE + "-trajets", JSON.stringify(lot));
      } catch (e) { /* stockage plein ou indisponible : le trajet reste en mémoire */ }
    }, 3000);
  }
  noterPosition = (lat, lon) => {
    const p = courante(); if (!p || !p.trajetActif || p.lienDep) return;   // lié à un objet : c'est la table qui garde le trajet
    const d = p.trajet[p.trajet.length - 1];
    if (d && A.hav(d[0], d[1], lat, lon) < PAS_M) return;   // immobile : pas de point en double
    p.trajet.push([Math.round(lat * 1e6) / 1e6, Math.round(lon * 1e6) / 1e6, Date.now()]);
    if (p.trajet.length > MAX_PTS) { const m = p.trajet.length >> 1; p.trajet = p.trajet.slice(0, m).filter((_, i) => i % 2 === 0).concat(p.trajet.slice(m)); }   // trop long : le début est allégé, la fin reste précise
    sauvegarderTrajets(); majTrajetUI(); redessiner();
  };
  const caseRec = el("input", {type: "checkbox", id: "ot-trajet-rec"}), caseVis = el("input", {type: "checkbox", id: "ot-trajet-vis"}), caseTous = el("input", {type: "checkbox", id: "ot-trajet-tous"}), infoTrajet = el("div", {id: "ot-trajet-info", class: "geo-note"});
  function majTrajetUI() {
    const p = courante(); if (!p) return;
    caseRec.checked = !!p.trajetActif; caseVis.checked = p.trajetVisible !== false; caseTous.checked = tousTrajets;
    const pts = pointsTrajet(p);
    let d = 0; for (let i = 1; i < pts.length; i++) d += A.hav(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]);
    const t0 = pts.length && pts[0][2], t1 = pts.length && pts[pts.length - 1][2], duree = t0 && t1 && t1 - t0 >= 5000 ? (t1 - t0) / 1000 : 0;   // moins de 5 s : vitesse moyenne sans signification
    infoTrajet.textContent = p.lienDep ? tj("engine/static/engine/js/geo_outils.js:784:43", {v1: pts.length, v2: A.fmtM(d) + (duree ? " · " + A.fmtDuree(duree) : ""), v3: objetLie(p) ? "" : tj("engine/static/engine/js/geo_outils.js:784:177")})
      : pts.length ? tj("engine/static/engine/js/geo_outils.js:trajet_parcouru", {v1: pts.length, v2: A.fmtM(d) + (duree ? " · " + A.fmtDuree(duree) + (duree > 0 ? tj("engine/static/engine/js/geo_outils.js:785:136", {v1: (d / duree * 3.6).toFixed(1)}) : "") : ""), v3: p.trajetActif ? "" : tj("engine/static/engine/js/geo_outils.js:785:227")})
      : p.trajetActif ? tj("engine/static/engine/js/geo_outils.js:786:26") : tj("engine/static/engine/js/geo_outils.js:786:131");
  }
  caseRec.onchange = () => {
    const p = courante(); if (!p) return;
    p.trajetActif = caseRec.checked;
    if (p.trajetActif && p.lienDep) A.toast(tj("engine/static/engine/js/geo_outils.js:791:46"));
    else if (p.trajetActif && !T.suivi) A.toast(tj("engine/static/engine/js/geo_outils.js:792:50"));
    majTrajetUI(); sauvegarder();
  };
  caseVis.onchange = () => { const p = courante(); if (p) { p.trajetVisible = caseVis.checked; sauvegarder(); redessiner(); } };
  caseTous.onchange = () => { tousTrajets = caseTous.checked; sauvegarder(); redessiner(); };
  const echapper = x => String(x).replace(/[<>&"']/g, c => ({"<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;"}[c]));
  function exporterTrajets() {   // GPX construit ici : fonctionne aussi sans connexion
    const liste = pers.filter(p => (tousTrajets || p.id === actif) && p.trajetVisible !== false).map(p => ({nom: p.nom, pts: pointsTrajet(p)})).filter(x => x.pts.length > 1);
    if (!liste.length) { A.toast(tj("engine/static/engine/js/geo_outils.js:800:35"), true); return; }
    const o = ['<?xml version="1.0" encoding="UTF-8"?>', '<gpx version="1.1" creator="Agentgy" xmlns="http://www.topografix.com/GPX/1/1">'];
    liste.forEach(x => { o.push("<trk><name>" + echapper(x.nom) + "</name><trkseg>"); x.pts.forEach(q => o.push('<trkpt lat="' + q[0].toFixed(6) + '" lon="' + q[1].toFixed(6) + '">' + (q[2] > 1e11 ? "<time>" + new Date(q[2]).toISOString() + "</time>" : "") + "</trkpt>")); o.push("</trkseg></trk>"); });
    o.push("</gpx>");
    telecharger("trajets-" + new Date().toISOString().slice(0, 10) + ".gpx", o.join("\n"), "application/gpx+xml");
    A.toast(tj(liste.length > 1 ? "engine/static/engine/js/geo_outils.js:export_gpx_n" : "engine/static/engine/js/geo_outils.js:export_gpx_un", {v1: liste.length}));
  }
  bloc.append(el("div", {class: "geo-note"}, el("b", {}, tj("engine/static/engine/js/geo_outils.js:titre_trajets")), el("div", {}, el("label", {class: "geo-case", title: tj("engine/static/engine/js/geo_outils.js:807:134")}, caseRec, tj("engine/static/engine/js/geo_outils.js:807:240"))),
    el("div", {}, el("label", {class: "geo-case"}, caseVis, tj("engine/static/engine/js/geo_outils.js:808:62")), " ", el("label", {class: "geo-case"}, caseTous, tj("engine/static/engine/js/geo_outils.js:808:135"))), infoTrajet,
    el("div", {}, bout(tj("engine/static/engine/js/geo_outils.js:809:25"), tj("engine/static/engine/js/geo_outils.js:809:47"), async () => { const p = courante(); if (!p || p.lienDep || !p.trajet.length) { A.toast(tj("engine/static/engine/js/geo_outils.js:809:192"), true); return; } if (!await GY.confirmer(tj("engine/static/engine/js/geo_outils.js:809:287", {v1: p.nom}))) return; p.trajet = []; sauvegarderTrajets(); majTrajetUI(); redessiner(); }),
      bout(tj("engine/static/engine/js/geo_outils.js:810:13"), tj("engine/static/engine/js/geo_outils.js:810:29"), exporterTrajets))));
  (window.GEO_DESSIN = window.GEO_DESSIN || []).push(Object.assign(ctx => {
    ctx.save(); ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.font = "12px sans-serif"; ctx.textAlign = "left";
    pers.forEach(p => {
      if (p.trajetVisible === false || (!tousTrajets && p.id !== actif)) return;
      const pts = pointsTrajet(p); if (!pts.length) return;
      const c = couleurTrajet(p);
      if (pts.length > 1) { chemin(ctx, pts, false); ctx.strokeStyle = gyCouleur("--forme-fond", "#0b1e33"); ctx.lineWidth = 6; ctx.stroke(); traitDegrade(ctx, pts, c, 3); }
      const [x0, y0] = A.versEcran(pts[0][0], pts[0][1]), q = pts[pts.length - 1], [x1, y1] = A.versEcran(q[0], q[1]);
      ctx.fillStyle = gyCouleur("--ok", "#2ecc71"); ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x0, y0, 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();   // point de départ du trajet
      ctx.fillStyle = c; ctx.beginPath(); ctx.arc(x1, y1, 7, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();   // dernière position connue
      ctx.lineWidth = 3; ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.strokeText(p.nom, x1 + 10, y1 - 8); ctx.fillStyle = "#fff"; ctx.fillText(p.nom, x1 + 10, y1 - 8);
    });
    ctx.restore();
  }, {calque: "outils"}));
  // restauration (stockage navigateur) puis première personne
  try {
    const sv = JSON.parse(localStorage.getItem(CLE) || "null");
    if (sv && Array.isArray(sv.pers) && sv.pers.length) {
      compteur = sv.compteur || 0; nomSeq = sv.nomSeq || 0;
      pers = sv.pers.slice(0, MAX).map(x => Object.assign(nouvelle(x.nom), {id: x.id, nom: x.nom, depart: x.depart, dest: x.dest, mode: MODES[x.mode] ? x.mode : "raccourcis", lienDep: x.lienDep || "", lienDest: x.lienDest || "", table: x.table ? String(x.table) : "", partage: !!x.partage, trajetActif: !!x.trajetActif, trajetVisible: x.trajetVisible !== false}));
      tousTrajets = sv.tousTrajets !== false;
      compteur = Math.max(compteur, ...pers.map(p => p.id)); actif = pers.some(p => p.id === sv.actif) ? sv.actif : pers[0].id;
    }
    // trajets parcourus : relus tels quels (ils sont locaux, donc intacts même si la session s'est terminée entre-temps)
    const lt = JSON.parse(localStorage.getItem(CLE + "-trajets") || "null");
    if (lt && typeof lt === "object") pers.forEach(p => { const r = lt[p.id]; if (r) { p.trajet = Array.isArray(r.pts) ? r.pts : []; p.cache = Array.isArray(r.cache) ? r.cache : null; } });
    // tracés déjà calculés : remis tels quels (marqués « conservés » tant qu'un recalcul n'a pas réussi)
    const lot = JSON.parse(localStorage.getItem(CLE + "-traces") || "null");
    if (lot && typeof lot === "object") pers.forEach(p => { const r = lot[p.id]; if (traceConcorde(r, p)) p.route = {ok: true, mode: r.mode, depart: r.depart, dest: r.dest, geometrie: r.geometrie, accroche: r.accroche || [], etapes: r.etapes || [], distance_m: r.distance_m, duree_s: r.duree_s, t: 0, calcule_a: r.calcule_a, hors_ligne: true}; });
  } catch (e) { pers = []; }
  if (!pers.length) { const p = nouvelle(); pers = [p]; actif = p.id; }
  setTimeout(afficher, 0);
  setInterval(() => {   // table chargée ou mise à jour : liens relancés, listes rafraîchies, destination liée suivie
    const p = courante();
    if (p && p.lienDep && !T.suivi && objetsTable().some(o => o.id === p.lienDep)) demarrerSuivi("objet", p.lienDep);
    majLiensDest(false); instantaneTable();
    if (document.activeElement !== selDep && document.activeElement !== selDest) majLiensListe();
  }, 5000);
  // chaque changement de départ/destination est mémorisé pour la personne affichée
  const memo = () => { fixer(); majListe(); sauvegarder(); };
  ["ot-depart-annuler", "ot-dest-annuler"].forEach(i => $(i) && $(i).addEventListener("click", () => { const p = courante(); if (p) { if (i === "ot-depart-annuler") p.lienDep = ""; else p.lienDest = ""; } majLiensListe(); memo(); }));
  document.addEventListener("click", () => setTimeout(memo, 300), true);
  window.GEO_PERSONNES = {partager: (lat, lon) => partagerPosition(lat, lon), choisirTable: v => { selTable.value = String(v); selTable.onchange(); }, liste: () => pers.map(p => ({id: p.id, nom: p.nom, lienDep: p.lienDep, lienDest: p.lienDest})), actif: () => actif, choisir, ajouter: nom => ajouter(nom).id, lierDepart, lierDestination};
})();
$("ot-osm").onclick = () => { if (T.depart && T.dest) ouvrirItineraire(T.depart, T.dest); else A.toast(tj("engine/static/engine/js/geo_outils.js:693:105"), true); };
$("ot-gps").onclick = () => demarrerSuivi("gps");
$("ot-suivre-objet").onclick = () => {
  if (!S.D || S.D.vide || !S.D.objets.find(x => x.id === S.selection && estTrajet(x) && x.lat)) { A.toast(tj("engine/static/engine/js/geo_outils.js:696:108"), true); return; }
  demarrerSuivi("objet", S.selection);
};
$("ot-arreter-suivi").onclick = () => { arreterSuivi(); A.toast(tj("engine/static/engine/js/geo_outils.js:699:66")); };
$("ot-man-ok").onclick = () => {
  const la = nombre("ot-man-lat", NaN), lo = nombre("ot-man-lon", NaN);
  if (!(Math.abs(la) <= 90 && Math.abs(lo) <= 180)) { A.toast(tj("engine/static/engine/js/geo_outils.js:702:64"), true); return; }
  arreterSuivi(); poserActuelle(la, lo, tj("engine/static/engine/js/geo_outils.js:703:42"));
};
$("ot-kml").onclick = () => exporter("kml");
$("ot-gpx").onclick = () => exporter("gpx");
document.addEventListener("keydown", e => { if (!/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName) && e.key === "Escape" && T.outil) activer(T.outil); });
$("carte").addEventListener("pointermove", e => { const c = $("ot-curseur"); if (!c) return; const [la, lo] = A.versGeo(e.offsetX, e.offsetY); c.textContent = la.toFixed(5) + ", " + lo.toFixed(5); });
$("carte").addEventListener("pointerleave", () => { const c = $("ot-curseur"); if (c) c.textContent = ""; });
majDepart(); majSuivi();

window.GEO_ITI = {etat: () => T, demanderNotifications, ouSuisJe, surTrace, fmtCap, modeActuel: modeRoute, recalculer: force => majRoute(!!force), effacerDestination: () => $("ot-dest-annuler").click(),
  livreur: actif => { T.livreur = !!actif; if (T.dest) majRoute(false); redessiner(); },
  destination: (lat, lon, nom) => { T.dest = {lat, lon, nom: nom || null}; T.arrive = false; majReste(); redessiner(); },
  position: (lat, lon, nom, gps) => { arreterSuivi(); poserActuelle(lat, lon, nom || tj("engine/static/engine/js/geo_outils.js:715:87"), !!gps); }};

(window.GEO_DESSIN = window.GEO_DESSIN || []).push(Object.assign(dessiner, {calque: "outils"}));
(window.GEO_CLIC = window.GEO_CLIC || []).push(clic);
(window.GEO_APRES = window.GEO_APRES || []).push(silencieux => { if (silencieux && $("ot-zones-auto").checked) verifierZones(true); });
})();
