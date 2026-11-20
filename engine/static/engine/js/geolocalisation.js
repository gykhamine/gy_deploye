// GÉOLOCALISATION : la carte seule, dans le canevas du dashboard. Canvas 2D sans dépendance (fonctionne hors ligne ; le fond
// OpenStreetMap est une couche facultative). Les calculs (distances, collisions, prévision, géostatistique) viennent du serveur
// (engine/geospatial.py) : ici on ne fait qu'AFFICHER et REJOUER — sauf les distances en direct entre objets, recalculées à chaque image.
(function () {
"use strict";
const G = window.GEO, TS = 256, TRAINEE = 40;
const ECHELLE_DEFAUT_M = 1e6;  // vue par défaut : l'échelle graphique affiche 1000 km (l'utilisateur dézoome ou zoome ensuite)
const COULEURS = [gyCouleur("--accent", "#6bcfff"), gyCouleur("--forme-orange", "#f5a524"), gyCouleur("--ok", "#2ecc71"), gyCouleur("--danger", "#e74c3c"), gyCouleur("--forme-violet", "#b48ead"), gyCouleur("--forme-jaune", "#f7d774"), gyCouleur("--forme-cyan", "#5dd6c0"), gyCouleur("--forme-rose", "#ff7ab8"), gyCouleur("--forme-vert", "#9ad06b"), gyCouleur("--forme-orange", "#ff9f6b"), gyCouleur("--forme-bleu", "#7aa2ff"), gyCouleur("--forme-clair", "#d4d4d4")];
const cv = document.getElementById("carte"), ctx = cv.getContext("2d", {alpha: false}), zone = document.getElementById("zone-carte");
const S = {D: null, vue: {cx: 0.5, cy: 0.5, z: 2}, k: 0, lecture: false, vitesse: 3, demo: false, sale: true, anime: false, W: 1, H: 1, dpr: 1,
  couches: {trajets: true, trainee: true, etiquettes: true, prevision: true, collisions: true, densite: true, osm: true},
  visibles: {}, couleurs: {}, selection: null, point: null, mesure: {actif: false, pts: []}, tuiles: new Map()};

// ── Utilitaires ──────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
function el(tag, attrs, ...enfants) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) { if (k === "class") n.className = v; else if (k.startsWith("on")) n[k] = v; else n.setAttribute(k, v); }
  for (const e of enfants) n.append(e instanceof Node ? e : document.createTextNode(String(e)));
  return n;
}
function toast(msg, err) {
  const t = $("toast"); t.textContent = msg; t.className = err ? "err" : ""; t.style.display = "block";
  clearTimeout(t._t); t._t = setTimeout(() => { t.style.display = "none"; }, 3500);
}
function csrf() { const m = document.cookie.match(/(?:^|; )gy_csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
let dernierAvisConnexion = 0;
function avisConnexion(msg) {   // un seul message par minute : les recalculs automatiques (livreur) ne doivent pas harceler l'écran
  if (Date.now() - dernierAvisConnexion > 60000) { dernierAvisConnexion = Date.now(); toast(msg, true); }
}
async function api(url, opts) {
  let res;
  try { res = await fetch(url, Object.assign({headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}}, opts || {})); }
  catch (e) { const m = tj("engine/static/engine/js/geolocalisation.js:34:26"); avisConnexion(m); throw {erreur: m, hors_ligne: true}; }   // pas de réseau : l'appelant garde ce qu'il avait déjà
  // Session terminée : Django renvoie vers /login/ (redirection suivie par fetch → page HTML 200) ou 401. Ce n'est PAS une réponse du serveur sur la donnée demandée.
  if (res.status === 401 || (res.redirected && /\/login\/?(\?|$)/.test(res.url))) {
    const m = tj("engine/static/engine/js/geolocalisation.js:37:16");
    avisConnexion(m); throw {erreur: m, hors_session: true};
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { toast(data.erreur || (tj("engine/static/engine/js/geolocalisation.js:30:41") + res.status), true); throw data; }
  return data;
}
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmtM = v => v < 1000 ? Math.round(v) + " m" : (v / 1000).toFixed(v < 10000 ? 2 : 1) + " km";
const fmtDuree = s => s < 90 ? Math.round(s) + " s" : s < 5400 ? Math.round(s / 60) + " min" : (s / 3600).toFixed(1) + " h";
const nomDe = id => { const o = S.D && S.D.objets.find(x => x.id === id); return o ? o.nom : id; };

// ── Projection Web Mercator (le monde tient dans [0,1]²) ─────────────────────
const mxr = lon => (lon + 180) / 360;
const myr = lat => { const s = Math.sin(clamp(lat, -85.05, 85.05) * Math.PI / 180); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };
const echelle = () => TS * Math.pow(2, S.vue.z);
const versEcran = (lat, lon) => { const e = echelle(); return [(mxr(lon) - S.vue.cx) * e + S.W / 2, (myr(lat) - S.vue.cy) * e + S.H / 2]; };
function versGeo(px, py) {
  const e = echelle(), x = (px - S.W / 2) / e + S.vue.cx, y = (py - S.H / 2) / e + S.vue.cy;
  return [Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI, x * 360 - 180];
}
// Projection Mercator mise en cache par objet (x, y dans [0,1]²) : pendant un déplacement de la carte, chaque image n'a plus
// qu'une multiplication par point au lieu d'un sin() et d'un log(). Les données sont remplacées en bloc (jamais modifiées sur place).
function mercator(o) {
  let c = o._merc;
  if (!c || c.lat !== o.lat || c.lon !== o.lon || c.n !== o.lat.length) {
    const n = o.lat.length, mx = new Float64Array(n), my = new Float64Array(n);
    for (let k = 0; k < n; k++) {
      const la = o.lat[k], lo = o.lon[k];
      if (la == null || lo == null) { mx[k] = NaN; my[k] = NaN; } else { mx[k] = mxr(lo); my[k] = myr(la); }
    }
    c = o._merc = {lat: o.lat, lon: o.lon, n, mx, my};
  }
  return c;
}
const mParPx = lat => 156543.03392 * Math.cos(lat * Math.PI / 180) / Math.pow(2, S.vue.z);
function hav(a, b, c, d) {
  const p1 = a * Math.PI / 180, p2 = c * Math.PI / 180;
  const h = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin((d - b) * Math.PI / 360) ** 2;
  return 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(h)));
}
function pos(o, kf) {           // position interpolée à l'indice fractionnaire kf
  const T = o.lat.length, k0 = clamp(Math.floor(kf), 0, T - 1), k1 = Math.min(T - 1, k0 + 1), f = kf - k0;
  const a = o.lat[k0], b = o.lon[k0], c = o.lat[k1], d = o.lon[k1];
  if (a == null || b == null) return null;
  return (c == null || d == null || f === 0) ? [a, b] : [a + (c - a) * f, b + (d - b) * f];
}
function indiceDeTemps(t) {     // temps -> indice fractionnaire dans l'axe (décimé) affiché
  const ts = S.D.t; let lo = 0, hi = ts.length - 1;
  if (t <= ts[0]) return 0; if (t >= ts[hi]) return hi;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ts[m] <= t) lo = m; else hi = m; }
  return lo + (t - ts[lo]) / Math.max(ts[hi] - ts[lo], 1e-9);
}
const objetsVisibles = () => S.D.objets.filter(o => S.visibles[o.id]);
// Une colonne est soit la trajectoire d'UN objet (tracée, rejouée, prévue), soit des positions d'objets différents / une suite incohérente (points seuls, sans trace ni prévision).
const estTrajet = o => !o.mode || o.mode === "trajectoire";
const objetsTrajets = () => objetsVisibles().filter(estTrajet);
const objetsPoints = () => objetsVisibles().filter(o => !estTrajet(o) && o.points);
const RAISONS = {dates_identiques: tj("engine/static/engine/js/geolocalisation.js:70:37"), dates_desordonnees: tj("engine/static/engine/js/geolocalisation.js:70:101"), deplacement_impossible: tj("engine/static/engine/js/geolocalisation.js:70:172"),
  sauts_frequents: tj("engine/static/engine/js/geolocalisation.js:71:21"), cap_aleatoire: tj("engine/static/engine/js/geolocalisation.js:71:67"), trace_erratique: tj("engine/static/engine/js/geolocalisation.js:71:109")};
const texteRaisons = o => (o.raisons || []).map(r => RAISONS[r.code] || r.code).join(", ");
const fmtDate = t => t == null ? tj("engine/static/engine/js/geolocalisation.js:73:35") : new Date(t * 1000).toLocaleString();
const LIB_MODE = {positions_distinctes: tj("engine/static/engine/js/geolocalisation.js:lbl_positions_distinctes"), incoherente: tj("engine/static/engine/js/geolocalisation.js:74:79")};

// ── Chargement ───────────────────────────────────────────────────────────────
// Données analysées : AUCUN choix automatique. La personne coche elle-même les colonnes de position (dans la table ouverte et/ou d'autres tables)
// et choisit la colonne de date de chaque table ; le choix est mémorisé pour cette table ouverte. Une table compte dès qu'une de ses positions est cochée.
// Couleur d'un objet (position, trajectoire) : choisie avec le sélecteur commun, mémorisée par table dans ce navigateur.
const CLE_COULEURS = "geo_couleurs_" + G.tableId;
function couleursChoisies() { try { return JSON.parse(localStorage.getItem(CLE_COULEURS) || "{}"); } catch (e) { return {}; } }
async function choisirCouleurObjet(o) {
  const c = await GYCouleur.choisir({titre: tj("engine/static/engine/js/geolocalisation.js:83:46") + o.nom + " »", valeur: S.couleurs[o.id], apercu: o.nom, retirable: false});
  if (!c) return;
  S.couleurs[o.id] = c; S.sale = true;
  try { localStorage.setItem(CLE_COULEURS, JSON.stringify(Object.assign(couleursChoisies(), {[o.id]: c}))); } catch (e) { /* stockage indisponible : couleur valable pour cette session */ }
  rendreLateral();
}
const CLE_TABLES = "geo_choix_" + G.tableId;
const blocsTables = () => Array.from(document.querySelectorAll("#geo-tables .geo-table[data-table]"));
function choixTables() {   // [{id, positions: [slug], temps: slug | "-"}] pour les tables qui ont au moins une position cochée
  return blocsTables().map(b => ({id: b.dataset.table, positions: Array.from(b.querySelectorAll("input[data-pos]:checked")).map(c => c.dataset.pos),
    temps: (b.querySelector("select[data-temps]") || {}).value || "-"})).filter(c => c.positions.length);
}
function tablesCochees() { return choixTables().map(c => c.id).filter(id => id !== String(G.tableId)); }
(function restaurerTables() {
  let sauve = {};
  try { sauve = JSON.parse(localStorage.getItem(CLE_TABLES) || "{}") || {}; } catch (e) { sauve = {}; }
  const memoriser = () => {
    try { localStorage.setItem(CLE_TABLES, JSON.stringify(choixTables())); } catch (e) { /* stockage indisponible : le choix vaut pour cette page */ }
  };
  const sauves = Array.isArray(sauve) ? sauve : [];
  blocsTables().forEach(b => {
    const s = sauves.find(x => String(x.id) === b.dataset.table);
    b.querySelectorAll("input[data-pos]").forEach(c => { c.checked = !!(s && s.positions.includes(c.dataset.pos)); });
    const sel = b.querySelector("select[data-temps]");
    if (sel && s && Array.from(sel.options).some(o => o.value === s.temps)) sel.value = s.temps;
    b.querySelectorAll("input[data-pos], select[data-temps]").forEach(c => { c.onchange = () => { memoriser(); charger(false, false).catch(() => {}); }; });
  });
})();
function parametres() {
  const p = {seuil: $("p-seuil").value, horizon: $("p-horizon").value, sigma_mesure: $("p-sigma").value}, tables = tablesCochees();
  if ($("p-objets").value) p.objets = $("p-objets").value;
  if ($("p-fenetre").value) p.fenetre = $("p-fenetre").value;
  if ($("p-mode").value !== "auto") p.mode = $("p-mode").value;
  const choix = choixTables(), ouv = String(G.tableId);
  p.ouverte = choix.some(c => c.id === ouv) ? 1 : 0;   // la table ouverte n'entre dans l'analyse que si on y a coché une position
  if (tables.length) p.tables = tables.join(",");
  if (choix.length) {
    p.colonnes = choix.flatMap(c => c.positions.map(s => c.id === ouv ? s : c.id + ":" + s)).join(",");
    p.temps = choix.map(c => c.id === ouv ? c.temps : c.id + ":" + c.temps).join(",");
  }
  return p;
}
async function charger(simuler, silencieux) {
  const p = parametres();
  let d;
  if (S.archive) {   // rejeu d'une période archivée : mêmes analyses, sur les positions anciennes (voir « Archives »)
    const q = {du: S.archive.du, au: S.archive.au, seuil: p.seuil, horizon: p.horizon, sigma_mesure: p.sigma_mesure};
    if (p.mode) q.mode = p.mode;
    if (p.objets) q.objets = p.objets;
    d = await api("/api/tables/" + G.tableId + "/geolocalisation/archives/rejouer/", {method: "POST", body: JSON.stringify(q)});
    appliquer(d, silencieux);
    if (d.archive && !d.vide) $("geo-lecture").textContent = tj("engine/static/engine/js/geolocalisation.js:134:63") + d.archive.lignes + tj("engine/static/engine/js/geolocalisation.js:134:105") + (d.archive.tronque ? tj("engine/static/engine/js/geolocalisation.js:134:144") : "") + tj("engine/static/engine/js/geolocalisation.js:134:188");
    return d;
  }
  if (simuler) d = await api("/api/tables/" + G.tableId + "/geolocalisation/simuler/", {method: "POST", body: JSON.stringify(Object.assign({}, p, S.demo ? {demo: 1} : {}))});
  else d = await api("/api/tables/" + G.tableId + "/geolocalisation/analyse/", {method: "POST", body: JSON.stringify(Object.assign({}, p, S.demo ? {demo: 1} : {}))});
  appliquer(d, silencieux);
  return d;
}
// ── DIRECT : un point ne s'arrête JAMAIS (missile, avion, navire…) ───────────────────────────────────────────────────────────
// Entre deux mises à jour, la position affichée S.aff[id] continue sur sa lancée (vitesse des deux dernières positions, estimée à l'arrivée des
// données). À chaque nouvelle donnée, elle se recale vers la position réelle prolongée SANS jamais reculer ni s'arrêter : la correction est lissée
// et plafonnée pour qu'elle ne puisse pas inverser le mouvement. Le prolongement dure au plus PROLONGEMENT_MAX_S sans nouvelle donnée.
const PROLONGEMENT_MAX_S = 600, SEUIL_IMMOBILE_MS = 0.5;
S.aff = {};
function preparerExtrapolation(d) {
  const maintenant = performance.now();
  for (const o of d.objets) {
    o._ex = null;
    if (!d.temps_reel || !estTrajet(o)) continue;
    let k1 = o.lat.length - 1; while (k1 >= 0 && (o.lat[k1] == null || o.lon[k1] == null)) k1--;
    let k0 = k1 - 1; while (k0 >= 0 && (o.lat[k0] == null || o.lon[k0] == null || !(d.t[k1] > d.t[k0]))) k0--;
    if (k1 < 0) continue;
    let vlat = 0, vlon = 0;
    if (k0 >= 0) {
      const dt = d.t[k1] - d.t[k0], v = hav(o.lat[k0], o.lon[k0], o.lat[k1], o.lon[k1]) / dt;
      if (v >= SEUIL_IMMOBILE_MS) { vlat = (o.lat[k1] - o.lat[k0]) / dt; vlon = (o.lon[k1] - o.lon[k0]) / dt; }   // sous 0,5 m/s : gigue GPS, pas un mouvement
    }
    o._ex = {lat0: o.lat[k1], lon0: o.lon[k1], vlat, vlon, arrivee: maintenant, decalage: decalageDeRecalage(S.aff[o.id], o.lat[k1], o.lon[k1], vlat, vlon)};
  }
}
// La dernière position reçue a toujours un peu d'âge (réseau, cadence du capteur) : le point affiché est donc déjà EN AVANT d'elle. Si la nouvelle
// trajectoire repartait de cette position à l'instant d'arrivée, la cible se retrouverait derrière le point, et la correction le freinerait (jusqu'à
// 80 % de sa vitesse) le temps que la cible le rattrape : le point rampait puis repartait, à CHAQUE mise à jour. On cherche donc l'instant de la nouvelle
// trajectoire (en secondes après sa dernière position) qui passe au plus près du point affiché, et la cible reprend à cet instant : la mise à jour ne
// change rien au déplacement visible, seul l'écart latéral éventuel (virage) est ensuite résorbé en douceur.
function decalageDeRecalage(affiche, lat0, lon0, vlat, vlon) {
  if (!affiche || !(vlat || vlon)) return 0;
  const k = Math.cos(lat0 * Math.PI / 180), vx = vlat, vy = vlon * k, v2 = vx * vx + vy * vy;
  if (!(v2 > 0)) return 0;
  const l = ((affiche.lat - lat0) * vx + (affiche.lon - lon0) * k * vy) / v2;
  return l > 0 ? Math.min(l, PROLONGEMENT_MAX_S) : 0;   // jamais négatif : un point en retard rattrape, il ne recule pas
}
const enDirect = () => !!(S.D && !S.D.vide && S.D.temps_reel && !S.lecture && !S.archive && !S.demo && (S.cible != null || S.k >= S.D.t.length - 1.001));
function avancerDirect(dt) {      // true si au moins un point bouge encore (l'image doit être redessinée)
  if (!enDirect()) { for (const id in S.aff) delete S.aff[id]; return false; }
  let bouge = false;
  const maintenant = performance.now();
  for (const o of objetsTrajets()) {
    const ex = o._ex; if (!ex) continue;
    let a = S.aff[o.id];
    if (!a) { const p = pos(o, S.k); if (!p) continue; a = S.aff[o.id] = {lat: p[0], lon: p[1]}; }
    const age = (maintenant - ex.arrivee) / 1000 + (ex.decalage || 0), prolonge = age < PROLONGEMENT_MAX_S, k = Math.cos(a.lat * Math.PI / 180);
    const vlat = prolonge ? ex.vlat : 0, vlon = prolonge ? ex.vlon : 0;
    const eLat = ex.lat0 + vlat * Math.min(age, PROLONGEMENT_MAX_S) - a.lat, eLon = ex.lon0 + vlon * Math.min(age, PROLONGEMENT_MAX_S) - a.lon;   // écart à la position réelle prolongée
    const f = 1 - Math.exp(-dt / 0.4);
    let cLat = eLat * f, cLon = eLon * f;
    const vn = Math.hypot(vlat, vlon * k);
    if (vn > 0) {   // la correction ne peut pas freiner plus de 80 % de la vitesse (le point continue toujours d'avancer), ni l'accélérer de plus de 50 % (pas de saut)
      const ul = vlat / vn, uo = vlon * k / vn, along = cLat * ul + cLon * k * uo, plancher = -0.8 * vn * dt, plafond = 0.5 * vn * dt;
      const borne = clamp(along, plancher, plafond);
      if (borne !== along) { cLat -= (along - borne) * ul; cLon -= (along - borne) * uo / k; }
    }
    const nLat = a.lat + vlat * dt + cLat, nLon = a.lon + vlon * dt + cLon;
    if (Math.abs(nLat - a.lat) + Math.abs(nLon - a.lon) > 1e-10) bouge = true;
    a.lat = nLat; a.lon = nLon;
  }
  return bouge;
}
function appliquer(d, silencieux) {
  // « Suivre le direct » = être au bout des données OU être en pleine glissade vers lui (S.cible) : dans les deux cas l'instant montré doit se
  // poursuivre sans saut. Avant, une mise à jour reçue pendant la glissade l'abandonnait net (S.cible remis à null) : le point se figeait.
  const suitLeDirect = !S.D || S.D.vide || S.cible != null || S.k >= S.D.t.length - 1.001;
  const tAffiche = suitLeDirect && silencieux && S.D && !S.D.vide ? tempsA(S.k) : null;   // instant EXACT montré avant la mise à jour (interpolé)
  S.D = d;
  try { preparerExtrapolation(d); } catch (e) { console.error(e); }
  const vide = $("geo-vide-lateral");   // sidebar : message d'aide + bouton de test, pour ne rien superposer à la carte
  vide.textContent = "";
  $("geo-vide").style.display = "none";
  if (d.vide) {
    vide.style.display = "";
    vide.append(el("div", {class: "geo-note"}, d.message || tj("engine/static/engine/js/geolocalisation.js:149:45")));
    vide.append(el("button", {type: "button", class: "geo-btn principal", onclick: basculerDemo}, tj("engine/static/engine/js/geolocalisation.js:150:76")));
    S.anime = false; S.sale = true; rendreLateral(); return;
  }
  vide.style.display = "none";
  const lec = d.lecture, noteLec = $("geo-lecture");
  noteLec.textContent = !lec ? "" : lec.objets_total > lec.objets_lus ? lec.objets_lus + tj("engine/static/engine/js/geolocalisation.js:155:91") + lec.objets_total + tj("engine/static/engine/js/geolocalisation.js:155:136") : lec.objets_lus + " objet(s), " + lec.pas_lus + tj("engine/static/engine/js/geolocalisation.js:155:265") + lec.duree_s + " s.";
  d.objets.forEach((o, i) => { if (!(o.id in S.couleurs)) S.couleurs[o.id] = couleursChoisies()[o.id] || COULEURS[Object.keys(S.couleurs).length % COULEURS.length]; if (!(o.id in S.visibles)) S.visibles[o.id] = true; });
  const T = d.t.length;
  $("lec-curseur").max = String(T - 1);
  S.cible = null;
  if (!silencieux) { S.k = T - 1; S.lecture = false; majBoutonPlay(); ajuster(true); }
  else if (suitLeDirect) { if (tAffiche != null && d.t[T - 1] > tAffiche) { S.k = indiceDeTemps(tAffiche); S.cible = T - 1; } else S.k = T - 1; }   // glisse vers le nouvel instant (boucle)
  S.k = clamp(S.k, 0, T - 1);
  S.anime = !!(d.prevision && d.prevision.collisions && d.prevision.collisions.length);
  majCurseur(); S.sale = true;
  // Mise à jour silencieuse (la carte tourne) : panneaux latéraux et hooks des autres modules passent en ARRIÈRE-PLAN, aux moments
  // d'inactivité du navigateur, et fusionnés (seule la dernière version compte) — ils ne doivent jamais retarder une image du déplacement.
  if (silencieux) enArrierePlan(() => { rendreLateral(); (window.GEO_APRES || []).forEach(f => f(true)); });
  else { rendreLateral(); (window.GEO_APRES || []).forEach(f => f(false)); }
}
let tacheArrierePlan = null, idArrierePlan = 0;
function enArrierePlan(f) {
  tacheArrierePlan = f;
  if (idArrierePlan) return;
  const lancer = () => { idArrierePlan = 0; const t = tacheArrierePlan; tacheArrierePlan = null; if (t) try { t(); } catch (e) { console.error(e); } };
  idArrierePlan = window.requestIdleCallback ? requestIdleCallback(lancer, {timeout: 1500}) : setTimeout(lancer, 50);
}
function tempsA(k) {            // instant (s) à l'indice fractionnaire k des données affichées
  const ts = S.D.t, T = ts.length, k0 = clamp(Math.floor(k), 0, T - 1), k1 = Math.min(T - 1, k0 + 1);
  return ts[k0] + (ts[k1] - ts[k0]) * clamp(k - k0, 0, 1);
}
// defaut === true : ouverture de la carte (échelle 1000 km, centrée sur les données) ; sinon (bouton, touche F) : cadrage serré.
function ajuster(defaut) {
  if (!S.D || S.D.vide) return;
  let la0 = 91, la1 = -91, lo0 = 181, lo1 = -181;
  const ajout = (a, b) => { if (a == null || b == null) return; la0 = Math.min(la0, a); la1 = Math.max(la1, a); lo0 = Math.min(lo0, b); lo1 = Math.max(lo1, b); };
  for (const o of objetsVisibles()) { for (let k = 0; k < o.lat.length; k++) ajout(o.lat[k], o.lon[k]); (o.points || []).forEach(pt => ajout(pt[0], pt[1])); }
  if (S.couches.prevision && S.D.prevision) for (const po of S.D.prevision.objets || []) if (S.visibles[po.id]) po.chemin.forEach(c => ajout(c[0], c[1]));
  if (la0 > la1) return;
  const dx = mxr(lo1) - mxr(lo0), dy = myr(la0) - myr(la1);
  const zx = dx > 1e-9 ? Math.log2(S.W * 0.8 / (TS * dx)) : 18, zy = dy > 1e-9 ? Math.log2(S.H * 0.7 / (TS * dy)) : 18;
  S.vue.z = defaut === true
    ? clamp(Math.log2(156543.03392 * Math.cos((la0 + la1) / 2 * Math.PI / 180) * 100 / ECHELLE_DEFAUT_M), 1, 18)
    : clamp(Math.min(zx, zy, 17), 1, 18);
  S.vue.cx = (mxr(lo0) + mxr(lo1)) / 2; S.vue.cy = (myr(la0) + myr(la1)) / 2; S.sale = true;
}
function centrerSur(lat, lon, z) { S.vue.cx = mxr(lon); S.vue.cy = myr(lat); if (z) S.vue.z = Math.max(S.vue.z, z); S.sale = true; }

// ── Dessin ───────────────────────────────────────────────────────────────────
const PAS_GRILLE = [1e-4, 2e-4, 5e-4, 1e-3, 2e-3, 5e-3, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30];
function grille() {
  const dpp = 360 / echelle(), pas = PAS_GRILLE.find(s => s / dpp >= 90) || 30;
  const g0 = versGeo(0, 0), g1 = versGeo(S.W, S.H), dec = pas >= 1 ? 0 : Math.min(6, Math.ceil(-Math.log10(pas)));
  ctx.lineWidth = 1; ctx.strokeStyle = gyCouleur("--forme-fond", "#0e1b26"); ctx.fillStyle = gyCouleur("--forme-fond", "#3a5a70"); ctx.font = "10px monospace";
  let n = 0;
  for (let lon = Math.ceil(g0[1] / pas) * pas; lon <= g1[1] && n < 200; lon += pas, n++) {
    const x = Math.round(versEcran(0, lon)[0]) + 0.5; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, S.H); ctx.stroke(); ctx.fillText(lon.toFixed(dec) + "°", x + 3, 11);
  }
  n = 0;
  for (let lat = Math.ceil(g1[0] / pas) * pas; lat <= g0[0] && n < 200; lat += pas, n++) {
    const y = Math.round(versEcran(lat, 0)[1]) + 0.5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(S.W, y); ctx.stroke(); ctx.fillText(lat.toFixed(dec) + "°", 3, y - 3);
  }
}
function tuiles() {
  if (S.vue.z < 1.5) return;
  const tz = clamp(Math.round(S.vue.z), 0, 19), n = Math.pow(2, tz), e = echelle(), taille = e / n;
  const x0 = Math.floor((-S.W / 2 / e + S.vue.cx) * n), x1 = Math.floor((S.W / 2 / e + S.vue.cx) * n);
  const y0 = Math.floor((-S.H / 2 / e + S.vue.cy) * n), y1 = Math.floor((S.H / 2 / e + S.vue.cy) * n);
  if ((x1 - x0 + 1) * (y1 - y0 + 1) > 80) return;
  ctx.globalAlpha = S.opaciteFond != null ? S.opaciteFond : 0.55;   // réglable dans « Calques » (geo_calques.js)
  for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
    if (y < 0 || y >= n) continue;
    const cle = tz + "/" + (((x % n) + n) % n) + "/" + y;
    let im = S.tuiles.get(cle);
    if (!im) {
      if (S.tuiles.size > 400) S.tuiles.clear();
      im = new Image(); im.referrerPolicy = "strict-origin-when-cross-origin"; im.onload = () => { S.sale = true; };
      im.onerror = () => setTimeout(() => S.tuiles.delete(cle), 60000); im.src = "/api/geolocalisation/tuiles/" + cle + ".png"; S.tuiles.set(cle, im);
    }
    if (im.complete && im.naturalWidth) ctx.drawImage(im, (x / n - S.vue.cx) * e + S.W / 2, (y / n - S.vue.cy) * e + S.H / 2, taille + 1, taille + 1);
  }
  ctx.globalAlpha = 1; ctx.fillStyle = gyCouleur("--texte-discret", "#7d8b96"); ctx.font = "10px sans-serif"; ctx.textAlign = "right"; ctx.fillText("© OpenStreetMap", S.W - 8, S.H - 62); ctx.textAlign = "left";
}
function densite() {
  const g = S.D.geostat || {}, de = g.densite;
  if (de) {
    const n = de.n, chaleur = gyDegradeur("--chaleur-debut", "#ffaa28", "--chaleur-fin", "#ff3c28");   // couleurs du thème (zones chaudes)
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      const v = de.z[r * n + c]; if (v < 0.1) continue;
      const a = versEcran(de.lat0 + (de.lat1 - de.lat0) * r / n, de.lon0 + (de.lon1 - de.lon0) * c / n);
      const b = versEcran(de.lat0 + (de.lat1 - de.lat0) * (r + 1) / n, de.lon0 + (de.lon1 - de.lon0) * (c + 1) / n);
      ctx.fillStyle = chaleur(v, (0.08 + 0.34 * v).toFixed(2));
      ctx.fillRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]) + 1, Math.abs(b[1] - a[1]) + 1);
    }
  }
  ctx.font = "11px sans-serif";
  for (const h of g.points_chauds || []) {
    const [x, y] = versEcran(h.lat, h.lon), r = Math.max(6, h.rayon_m / mParPx(h.lat));
    ctx.setLineDash([4, 4]); ctx.strokeStyle = gyCouleur("--forme-orange", "#f5a524"); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = gyCouleur("--texte-orange", "#f5a524"); ctx.fillText(tj("engine/static/engine/js/geolocalisation.js:lbl_zone_chaude", {v1: Math.round(h.part * 100)}), x + r + 4, y + 4);
  }
  if (g.centre && g.ellipse && g.ellipse.demi_grand_m) {
    const [x, y] = versEcran(g.centre[0], g.centre[1]), m = mParPx(g.centre[0]);
    ctx.strokeStyle = gyCouleur("--accent", "#6bcfff", .5); ctx.lineWidth = 1; ctx.beginPath();
    ctx.ellipse(x, y, Math.max(2, g.ellipse.demi_grand_m / m), Math.max(2, g.ellipse.demi_petit_m / m), -g.ellipse.angle_deg * Math.PI / 180, 0, 2 * Math.PI); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x - 5, y); ctx.lineTo(x + 5, y); ctx.moveTo(x, y - 5); ctx.lineTo(x, y + 5); ctx.stroke();
  }
}
function trajets() {
  const objs = objetsTrajets(), k0 = Math.floor(S.k), T = S.D.t.length;
  const pas = Math.max(1, Math.ceil(T * objs.length / 8000));
  const nbTroncs = Math.max(6, Math.min(90, Math.floor(1200 / Math.max(1, objs.length))));   // nuances par historique : moins quand il y a beaucoup d'objets (fluidité)
  for (const o of objs) {
    const couleur = S.couleurs[o.id];
    if (S.couches.trajets) {
      // Historique du trajet = évolution : tronçons de la couleur de l'objet, du plus sombre (début) au plus clair (position la plus récente).
      ctx.globalAlpha = 0.5; ctx.lineWidth = 1.2; ctx.beginPath();
      let ouvert = false, tronc = -1;
      const m = mercator(o), e = echelle(), ox = S.W / 2 - S.vue.cx * e, oy = S.H / 2 - S.vue.cy * e, taille = Math.max(1, T / nbTroncs);
      for (let k = 0; k < T; k += pas) {
        const mx = m.mx[k];
        if (mx !== mx) { ouvert = false; continue; }   // NaN : position manquante
        const x = mx * e + ox, y = m.my[k] * e + oy, numTronc = Math.floor(k / taille);
        if (numTronc !== tronc) {   // nouveau tronçon : on termine le précédent sur ce point, puis on repart de lui avec la nuance suivante
          if (ouvert) { ctx.lineTo(x, y); ctx.stroke(); }
          tronc = numTronc; ctx.strokeStyle = gyNuance(couleur, k / Math.max(1, T - 1)); ctx.beginPath();
          if (ouvert) ctx.moveTo(x, y);
        }
        if (ouvert) ctx.lineTo(x, y); else { ctx.moveTo(x, y); ouvert = true; }
      }
      ctx.stroke(); ctx.globalAlpha = 1;
    }
    if (S.couches.trainee) {
      const debut = Math.max(0, k0 - TRAINEE);
      ctx.lineWidth = 2.2;
      for (let k = debut; k <= k0; k++) {
        const a = o.lat[k] != null ? [o.lat[k], o.lon[k]] : null, b = k === k0 ? pos(o, S.k) : (o.lat[k + 1] != null ? [o.lat[k + 1], o.lon[k + 1]] : null);
        if (!a || !b) continue;
        const p = versEcran(a[0], a[1]), q = versEcran(b[0], b[1]);
        ctx.globalAlpha = 1; ctx.strokeStyle = gyNuance(couleur, (k - debut + 1) / (k0 - debut + 1));   // traînée : du plus sombre (ancien) au plus clair (récent)
        ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
      }
      const af = S.aff[o.id], dp = af && pos(o, S.k);   // la traînée rejoint le point prolongé : pas de trou derrière lui
      if (af && dp) { const p = versEcran(dp[0], dp[1]), q = versEcran(af.lat, af.lon); ctx.globalAlpha = 1; ctx.strokeStyle = gyNuance(couleur, 1); ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
  }
}
function prevision() {
  const P = S.D.prevision; if (!P || !P.objets) return;
  const alpha = S.k >= S.D.t.length - 1.5 ? 1 : 0.25;
  for (const po of P.objets) {
    const o = S.D.objets.find(x => x.id === po.id); if (!o || !S.visibles[o.id] || !po.chemin.length) continue;
    let k = o.lat.length - 1; while (k >= 0 && o.lat[k] == null) k--;
    if (k < 0) continue;
    const couleur = S.couleurs[o.id], H = po.chemin.length;
    ctx.globalAlpha = alpha * 0.85; ctx.lineWidth = 1.5; ctx.setLineDash([6, 5]);
    let [x, y] = versEcran(o.lat[k], o.lon[k]);
    po.chemin.forEach((c, i) => {   // trajectoire prévue = évolution : du plus sombre (dernière position connue) au plus clair (horizon)
      const [x2, y2] = versEcran(c[0], c[1]);
      ctx.strokeStyle = gyNuance(couleur, (i + 1) / H); ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x2, y2); ctx.stroke();
      x = x2; y = y2;
    });
    ctx.setLineDash([]); ctx.strokeStyle = couleur;
    ctx.globalAlpha = alpha * 0.4; ctx.lineWidth = 1;
    for (const i of [Math.max(0, Math.ceil(H / 4) - 1), Math.max(0, Math.ceil(H / 2) - 1), H - 1]) {
      const c = po.chemin[i], [cx, cy] = versEcran(c[0], c[1]);
      ctx.beginPath(); ctx.arc(cx, cy, Math.max(2, po.rayon90_m[i] / mParPx(c[0])), 0, 2 * Math.PI); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
}
function collisions() {
  const seuil = S.D.parametres.seuil_collision_m, objs = objetsTrajets(), ps = objs.map(o => pos(o, S.k));
  ctx.font = "11px sans-serif";
  const dg = seuil * 4 / 111000, ordre = ps.map((p, i) => i).filter(i => ps[i]).sort((a, b) => ps[a][0] - ps[b][0]);   // seuil en degrés ; tri par latitude
  for (let u = 0; u < ordre.length; u++) for (let v = u + 1; v < ordre.length; v++) {     // distances en direct entre objets proches
    const i = ordre[u], j = ordre[v];
    if (ps[j][0] - ps[i][0] > dg) break;
    let dlo = Math.abs(ps[j][1] - ps[i][1]); if (dlo > 180) dlo = 360 - dlo;
    if (dlo * Math.cos(ps[i][0] * Math.PI / 180) > dg * 1.05) continue;
    const d = hav(ps[i][0], ps[i][1], ps[j][0], ps[j][1]);
    if (d > seuil * 4) continue;
    const a = versEcran(ps[i][0], ps[i][1]), b = versEcran(ps[j][0], ps[j][1]), danger = d < seuil;
    ctx.strokeStyle = danger ? gyCouleur("--forme-rouge", "#ff4d4d") : gyCouleur("--forme-orange", "#f5a524"); ctx.fillStyle = ctx.strokeStyle; ctx.lineWidth = danger ? 2 : 1; ctx.setLineDash(danger ? [] : [3, 3]);
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillText(fmtM(d), (a[0] + b[0]) / 2 + 5, (a[1] + b[1]) / 2 - 5);
  }
  for (const e of S.D.rencontres || []) {                                                  // rencontres passées
    const [x, y] = versEcran(e.lat, e.lon), proche = Math.abs(S.k - indiceDeTemps(e.t)) < 4, r = seuil / mParPx(e.lat);
    ctx.globalAlpha = proche ? 1 : 0.5; ctx.strokeStyle = gyCouleur("--forme-rouge", "#ff6b6b"); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x - 6, y - 6); ctx.lineTo(x + 6, y + 6); ctx.moveTo(x + 6, y - 6); ctx.lineTo(x - 6, y + 6); ctx.stroke();
    ctx.globalAlpha = 0.3; ctx.lineWidth = 1; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.arc(x, y, Math.max(3, r), 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }
  const pulse = Math.sin(performance.now() / 250);
  for (const c of (S.D.prevision && S.D.prevision.collisions) || []) {                     // collisions prévues
    const [x, y] = versEcran(c.lat, c.lon);
    ctx.strokeStyle = gyCouleur("--forme-rouge", "#ff4d4d"); ctx.fillStyle = gyCouleur("--forme-rouge", "#ff4d4d"); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 11 + 3 * pulse, 0, 2 * Math.PI); ctx.stroke();
    ctx.globalAlpha = 0.25; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x, y, Math.max(12, seuil / mParPx(c.lat)), 0, 2 * Math.PI); ctx.stroke(); ctx.globalAlpha = 1;
    ctx.fillText(Math.round(c.proba * 100) + " % · " + fmtDuree(c.dans_s), x + 16, y + 4);
  }
}
function pointsDistincts() {      // positions d'objets différents : toutes affichées avec leur date, celles dont la date ne colle pas sont cerclées de rouge
  ctx.font = "11px sans-serif";
  const e = echelle(), ox = S.W / 2 - S.vue.cx * e, oy = S.H / 2 - S.vue.cy * e;
  const contour = gyCouleur("--forme-contour", "#000"), rouge = gyCouleur("--forme-rouge", "#ff4d4d");
  const clair = gyCouleur("--forme-clair", "#fff"), lien = gyCouleur("--texte-lien", "#cfe6f5");
  for (const o of objetsPoints()) {
    const couleur = S.couleurs[o.id], anneaux = [];
    ctx.fillStyle = couleur; ctx.strokeStyle = contour; ctx.lineWidth = 1; ctx.beginPath();
    for (const pt of o.points) {
      let mx = pt.mx;
      if (mx === undefined) { mx = pt.mx = mxr(pt[1]); pt.my = myr(pt[0]); }   // projection posée une fois sur le point
      const x = mx * e + ox, y = pt.my * e + oy;
      if (x < -20 || y < -20 || x > S.W + 20 || y > S.H + 20) continue;
      ctx.moveTo(x + 4.5, y); ctx.arc(x, y, 4.5, 0, 2 * Math.PI);   // tous les disques de l'objet dans un seul tracé
      if (pt[3]) anneaux.push(x, y);
    }
    ctx.fill(); ctx.stroke();
    if (anneaux.length) {
      ctx.strokeStyle = rouge; ctx.lineWidth = 2; ctx.beginPath();
      for (let i = 0; i < anneaux.length; i += 2) { ctx.moveTo(anneaux[i] + 8, anneaux[i + 1]); ctx.arc(anneaux[i], anneaux[i + 1], 8, 0, 2 * Math.PI); }
      ctx.stroke();
    }
    if (S.point && S.point.id === o.id) { const [x, y] = versEcran(S.point.pt[0], S.point.pt[1]); ctx.strokeStyle = clair; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 12, 0, 2 * Math.PI); ctx.stroke(); }
    if (S.couches.etiquettes && o.points.length) { const [x, y] = versEcran(o.points[0][0], o.points[0][1]); ctx.fillStyle = lien; ctx.fillText(o.nom, x + 10, y - 9); }
  }
}
function positions() {
  ctx.font = "11px sans-serif";
  for (const o of objetsTrajets()) {
    const a = S.aff[o.id], ex = a && o._ex, p = a || pos(o, S.k); if (!p) continue;
    const [x, y] = versEcran(p.lat !== undefined ? p.lat : p[0], p.lat !== undefined ? p.lon : p[1]), couleur = S.couleurs[o.id];
    ctx.globalAlpha = 0.22; ctx.fillStyle = couleur; ctx.beginPath(); ctx.arc(x, y, 11, 0, 2 * Math.PI); ctx.fill(); ctx.globalAlpha = 1;
    const av = ex && (ex.vlat || ex.vlon) ? [a.lat - ex.vlat, a.lon - ex.vlon] : pos(o, Math.max(0, S.k - 0.6));   // cap : celui de la vitesse (prolongement) ou des 2 dernières positions
    if (av && (ex || S.k > 0)) {
      const [ax, ay] = versEcran(av[0], av[1]), n = Math.hypot(x - ax, y - ay);
      if (n > 0.5) { ctx.strokeStyle = couleur; ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + (x - ax) / n * 15, y + (y - ay) / n * 15); ctx.stroke(); }
    }
    ctx.fillStyle = couleur; ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    if (S.selection === o.id) { ctx.strokeStyle = gyCouleur("--forme-clair", "#fff"); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 14, 0, 2 * Math.PI); ctx.stroke(); }
    if (S.couches.etiquettes) { ctx.fillStyle = gyCouleur("--texte-lien", "#cfe6f5"); ctx.fillText(o.nom, x + 10, y - 9); }
  }
}
function mesure() {
  const pts = S.mesure.pts; if (!pts.length) return;
  ctx.strokeStyle = gyCouleur("--forme-clair", "#ffffff"); ctx.fillStyle = gyCouleur("--forme-clair", "#ffffff"); ctx.lineWidth = 1.5; ctx.font = "12px sans-serif";
  const e = pts.map(p => versEcran(p[0], p[1]));
  e.forEach(p => { ctx.beginPath(); ctx.arc(p[0], p[1], 4, 0, 2 * Math.PI); ctx.stroke(); });
  if (e.length === 2) {
    ctx.beginPath(); ctx.moveTo(e[0][0], e[0][1]); ctx.lineTo(e[1][0], e[1][1]); ctx.stroke();
    ctx.fillText(fmtM(hav(pts[0][0], pts[0][1], pts[1][0], pts[1][1])), (e[0][0] + e[1][0]) / 2 + 6, (e[0][1] + e[1][1]) / 2 - 6);
  }
}
function echelleGraphique() {
  const g = versGeo(S.W / 2, S.H / 2), m = mParPx(g[0]) * 100, p10 = Math.pow(10, Math.floor(Math.log10(m)));
  const joli = [5, 2, 1].map(f => f * p10).find(v => v <= m) || p10, px = joli / mParPx(g[0]), y = S.H - 66;
  ctx.strokeStyle = gyCouleur("--forme-gris", "#9fb4c2"); ctx.fillStyle = gyCouleur("--forme-gris", "#9fb4c2"); ctx.lineWidth = 1.5; ctx.font = "11px sans-serif";
  ctx.beginPath(); ctx.moveTo(12, y - 4); ctx.lineTo(12, y); ctx.lineTo(12 + px, y); ctx.lineTo(12 + px, y - 4); ctx.stroke(); ctx.fillText(fmtM(joli), 16, y - 7);
}
function dessiner() {
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0); ctx.globalAlpha = 1; ctx.textAlign = "left";
  ctx.fillStyle = gyCouleur("--forme-fond", "#03070b"); ctx.fillRect(0, 0, S.W, S.H);
  grille(); if (S.couches.osm) tuiles();
  if (S.D && !S.D.vide) {
    if (S.couches.densite) densite();
    if (S.couches.trajets || S.couches.trainee) trajets();
    if (S.couches.prevision) prevision();
    if (S.couches.collisions) collisions();
    pointsDistincts();
    positions();
  }
  if (window.GEO_CALQUES) window.GEO_CALQUES.dessiner(ctx); else (window.GEO_DESSIN || []).forEach(f => f(ctx));   // calques : ordre et opacité (geo_calques.js)
  mesure(); echelleGraphique();
}

// ── Lecture (rejeu temporel) ─────────────────────────────────────────────────
function libelleTemps() {
  const T = S.D.t.length, k0 = clamp(Math.floor(S.k), 0, T - 1), k1 = Math.min(T - 1, k0 + 1);
  const t = S.D.t[k0] + (S.D.t[k1] - S.D.t[k0]) * (S.k - k0);
  return S.D.temps_reel ? new Date(t * 1000).toLocaleString() : "instant " + (k0 + 1) + "/" + T + " · +" + fmtDuree(t - S.D.t[0]);
}
function textePlage() {   // plage RÉELLE des données : date la plus ancienne -> la plus récente, durée, et signal si la lecture est tronquée
  const P = S.D && S.D.plage; if (!P) return "";
  if (!P.temps_reel || P.debut_ts == null) return P.instants + tj("engine/static/engine/js/geolocalisation.js:389:65");
  const f = ts => new Date(ts * 1000).toLocaleString();
  return tj("engine/static/engine/js/geolocalisation.js:391:11") + f(P.debut_ts) + " au " + f(P.fin_ts) + " · " + fmtDuree(P.duree_s) + " · " + P.instants + " instants"
    + (P.tronquee ? tj("engine/static/engine/js/geolocalisation.js:392:22") + P.lignes_total + tj("engine/static/engine/js/geolocalisation.js:392:86") : "");
}
function majCurseur() { if (S.D && !S.D.vide) { $("lec-curseur").value = String(S.k); $("lec-temps").textContent = libelleTemps(); const pl = $("lec-plage"); if (pl) pl.textContent = textePlage(); } else $("lec-temps").textContent = "—"; }
function majBoutonPlay() { $("lec-play").textContent = S.lecture ? "⏸" : "▶"; }
let dernier = performance.now();
function boucle(now) {
  requestAnimationFrame(boucle);   // reprogrammée EN PREMIER : aucune erreur plus bas ne peut arrêter la boucle d'animation
  const dt = Math.min(0.25, (now - dernier) / 1000); dernier = now;
  let bouge = false;
  try {
    if (S.lecture && S.D && !S.D.vide) {
      S.k += S.vitesse * dt;
      if (S.k >= S.D.t.length - 1) { S.k = S.D.t.length - 1; S.lecture = false; majBoutonPlay(); }
      bouge = true;
    } else if (S.cible != null && S.D && !S.D.vide) {   // rattrapage doux de la dernière position reçue (constante de temps 0,3 s)
      S.k += (S.cible - S.k) * (1 - Math.exp(-dt / 0.3));
      if (S.cible - S.k < 0.002) { S.k = S.cible; S.cible = null; }
      bouge = true;
    }
  } catch (e) { console.error(e); }
  try { if (avancerDirect(dt)) bouge = true; } catch (e) { console.error(e); }
  for (const f of window.GEO_IMAGE || []) { try { if (f(dt)) S.sale = true; } catch (e) { console.error(e); } }   // éléments animés des autres fichiers (ex. position du livreur)
  if (bouge) { S.sale = true; if (now - (S.texteMaj || 0) > 200 || S.cible == null && !S.lecture) { S.texteMaj = now; try { majCurseur(); majInfo(); } catch (e) { console.error(e); } } }
  if (S.sale || S.anime) { S.sale = false; try { dessiner(); } catch (e) { console.error(e); S.sale = true; } }
}

// ── Panneaux (sidebar, alertes, statistiques, fiche d'objet) ─────────────────
// Cartes flottantes (geo_cartes.js, chargé après ce fichier) : rafraîchies à chaque rendu des panneaux et au (dé)cochage d'un objet.
function majCartes() { if (window.GEO_CARTES) window.GEO_CARTES.maj(); }
// Sélectionne un objet et centre la carte dessus (nom cliqué dans « Objets suivis » ou ligne de la carte « Distances et vitesses »).
function selectionnerObjet(o) {
  S.selection = o.id; S.point = null;
  const p = estTrajet(o) ? (pos(o, S.k) || pos(o, o.lat.length - 1)) : (o.points && o.points.length ? o.points[0] : null);
  if (p) centrerSur(p[0], p[1], estTrajet(o) ? 14 : 11);
  majInfo();
}
function rendreLateral() {
  const D = S.D, res = $("geo-resume"), liste = $("geo-objets"), al = $("geo-alertes"), st = $("geo-stats");
  liste.textContent = ""; al.textContent = ""; st.textContent = "";
  if (!D || D.vide) { res.textContent = D && D.message ? D.message : tj("engine/static/engine/js/geolocalisation.js:421:71"); al.textContent = "—"; st.textContent = "—"; majCurseur(); majCartes(); return; }
  const r = D.resume;
  const nbDistincts = (r.positions_distinctes || 0) + (r.incoherentes || 0);
  res.textContent = r.objets + tj("engine/static/engine/js/geolocalisation.js:424:33") + r.positions + " positions" + (r.trajectoires ? " · " + r.trajectoires + " trajectoire(s), " + fmtM(r.distance_m || 0) : "") + (nbDistincts ? " · " + nbDistincts + tj("engine/static/engine/js/geolocalisation.js:424:215") : "") + (S.demo ? tj("engine/static/engine/js/geolocalisation.js:424:256") : "");
  D.objets.forEach(o => {
    const c = el("input", {type: "checkbox", onchange: e => { S.visibles[o.id] = e.target.checked; S.sale = true; majCartes(); }});
    c.checked = !!S.visibles[o.id];
    liste.append(el("div", {class: "geo-objet"}, c, el("span", {class: "puce", title: tj("engine/static/engine/js/geolocalisation.js:428:88"), style: "cursor:pointer;background:" + S.couleurs[o.id], onclick: () => choisirCouleurObjet(o)}),
      el("span", {class: "nom", title: tj("engine/static/engine/js/geolocalisation.js:429:41"), onclick: () => selectionnerObjet(o)}, o.nom),
      el("span", {class: "geo-note", title: estTrajet(o) ? "" : texteRaisons(o)}, estTrajet(o) ? (o.stats.vitesse_moy_ms * 3.6).toFixed(0) + " km/h" : (o.positions_total || 0) + " pos. · " + LIB_MODE[o.mode])));
  });
  if (!D.alertes.length) al.textContent = tj("engine/static/engine/js/geolocalisation.js:432:44");
  D.alertes.forEach(a => al.append(el("button", {class: "geo-alerte " + a.gravite, onclick: () => { if (a.lat != null) centrerSur(a.lat, a.lon, 15); }}, texteAlerte(a))));
  const g = D.geostat || {}, v = D.validation, ln = [];
  if (!D.prevision.objets || !D.prevision.objets.length) ln.push([tj("engine/static/engine/js/geolocalisation.js:435:68"), tj("engine/static/engine/js/geolocalisation.js:435:81")]);
  if (v) ln.push([tj("engine/static/engine/js/geolocalisation.js:436:20"), "erreur " + fmtM(v.rmse_m) + " contre " + fmtM(v.rmse_stationnaire_m) + tj("engine/static/engine/js/geolocalisation.js:436:126") + Math.round(v.gain * 100) + tj("engine/static/engine/js/geolocalisation.js:436:188") + (v.rmse_cv_m != null ? ", " + fmtM(v.rmse_cv_m) + tj("engine/static/engine/js/geolocalisation.js:436:255") : "")]);
  if (D.prevision.objets && D.prevision.objets.length) ln.push([tj("engine/static/engine/js/geolocalisation.js:437:66"), ({imm: tj("engine/static/engine/js/geolocalisation.js:437:83"), opencv: "Kalman (OpenCV)"}[D.prevision.filtre] || tj("engine/static/engine/js/geolocalisation.js:437:175")) + tj("engine/static/engine/js/geolocalisation.js:437:203") + D.parametres.sigma_accel + " m/s², " + D.prevision.echantillons + " tirages"]);
  if (g.clark_evans) ln.push([tj("engine/static/engine/js/geolocalisation.js:clark_evans_groupe"), "R = " + g.clark_evans.R + (g.clark_evans.R < 0.85 ? tj("engine/static/engine/js/geolocalisation.js:438:116") : g.clark_evans.R > 1.15 ? tj("engine/static/engine/js/geolocalisation.js:438:155") : tj("engine/static/engine/js/geolocalisation.js:438:171")) + " (z = " + g.clark_evans.z + ")"]);
  if (g.moran_vitesse) ln.push([tj("engine/static/engine/js/geolocalisation.js:439:34"), "I = " + g.moran_vitesse.I + " (attendu " + g.moran_vitesse.attendu + ")" + (g.moran_vitesse.I > 0.3 ? tj("engine/static/engine/js/geolocalisation.js:vitesses_semblables") : "")]);
  if (g.ellipse) ln.push(["Dispersion", "ellipse " + fmtM(g.ellipse.demi_grand_m) + " × " + fmtM(g.ellipse.demi_petit_m)]);
  if (g.variogramme_vitesse && g.variogramme_vitesse.length) { const w = g.variogramme_vitesse; ln.push([tj("engine/static/engine/js/geolocalisation.js:441:107"), tj("engine/static/engine/js/geolocalisation.js:441:136") + tj("engine/static/engine/js/geolocalisation.js:variogramme_bornes", {v1: w[0].gamma, v2: fmtM(w[0].h_m), v3: w[w.length - 1].gamma, v4: fmtM(w[w.length - 1].h_m)})]); }
  ln.forEach(([t, x]) => st.append(el("div", {style: "margin-bottom:5px"}, el("b", {}, t + " : "), x)));
  majCurseur(); majInfo(); majCartes();
}
function texteAlerte(a) {
  switch (a.type) {
    case "rencontre": return nomDe(a.a) + " et " + nomDe(a.b) + tj("engine/static/engine/js/geolocalisation.js:447:66") + fmtM(a.valeur) + (a.interpole ? tj("engine/static/engine/js/geolocalisation.js:447:116") : "");
    case "collision_prevue": return tj("engine/static/engine/js/geolocalisation.js:collision_possible", {v1: nomDe(a.a), v2: nomDe(a.b), v3: Math.round(a.valeur * 100), v4: fmtDuree(a.dans_s)});
    case "positions_distinctes": return nomDe(a.a) + " : " + a.valeur + tj("engine/static/engine/js/geolocalisation.js:449:74") + (a.raisons || []).map(c => RAISONS[c] || c).join(", ") + tj("engine/static/engine/js/geolocalisation.js:449:198") + (a.forcee ? tj("engine/static/engine/js/geolocalisation.js:449:269") : "");
    case "suite_incoherente": return nomDe(a.a) + tj("engine/static/engine/js/geolocalisation.js:450:52") + a.valeur + tj("engine/static/engine/js/geolocalisation.js:lbl_positions_douteuses") + (a.raisons || []).map(c => RAISONS[c] || c).join(", ") + tj("engine/static/engine/js/geolocalisation.js:450:163");
    case "saut_position": return nomDe(a.a) + " : " + a.valeur + tj("engine/static/engine/js/geolocalisation.js:451:67");
    case "immobile": return nomDe(a.a) + tj("engine/static/engine/js/geolocalisation.js:452:43") + fmtDuree(a.valeur);
    case "position_manquante": return nomDe(a.a) + " : " + a.valeur + tj("engine/static/engine/js/geolocalisation.js:lbl_positions_manquantes");
    case "date_manquante": return a.pas_fixe ? tj("engine/static/engine/js/geolocalisation.js:454:49") : a.valeur + tj("engine/static/engine/js/geolocalisation.js:454:144");
    case "source_ignoree": return a.valeur + tj("engine/static/engine/js/geolocalisation.js:455:47") + (a.noms || []).join(", ") + tj("engine/static/engine/js/geolocalisation.js:455:113");
    case "axe_par_lignes": return a.valeur + tj("engine/static/engine/js/geolocalisation.js:456:47");
    case "concentration": return Math.round(a.valeur * 100) + tj("engine/static/engine/js/geolocalisation.js:457:64");
    default: return a.type;
  }
}
function majInfo() {
  const box = $("geo-info"), o = S.D && !S.D.vide && S.D.objets.find(x => x.id === S.selection);
  if (!o) { box.style.display = "none"; return; }
  const s = o.stats, p = pos(o, S.k);
  box.textContent = ""; box.style.display = "block";
  box.append(el("div", {class: "t"}, o.nom));
  const ligne = (t, v) => box.append(el("div", {}, t + " : " + v));
  if (!estTrajet(o)) {
    ligne(tj("engine/static/engine/js/geolocalisation.js:lbl_nature"), LIB_MODE[o.mode] + (o.forcee ? tj("engine/static/engine/js/geolocalisation.js:469:53") : ""));
    ligne("Positions", (o.positions_total || 0) + ((o.points || []).length < (o.positions_total || 0) ? " (" + o.points.length + tj("engine/static/engine/js/geolocalisation.js:606:131") : ""));
    if ((o.raisons || []).length) ligne(tj("engine/static/engine/js/geolocalisation.js:lbl_pourquoi"), texteRaisons(o));
    if (S.point && S.point.id === o.id) {
      ligne(tj("engine/static/engine/js/geolocalisation.js:lbl_position_choisie"), S.point.pt[0].toFixed(5) + ", " + S.point.pt[1].toFixed(5));
      ligne(tj("engine/static/engine/js/geolocalisation.js:474:14"), fmtDate(S.point.pt[2]) + (S.point.pt[3] ? tj("engine/static/engine/js/geolocalisation.js:474:64") : ""));
      ligne(tj("engine/static/engine/js/geolocalisation.js:475:14"), "n° " + (S.point.pt[4] + 1) + tj("engine/static/engine/js/geolocalisation.js:475:53"));
    } else ligne(tj("engine/static/engine/js/geolocalisation.js:lbl_astuce"), tj("engine/static/engine/js/geolocalisation.js:476:29"));
    return;
  }
  if (p) ligne("Position", p[0].toFixed(5) + ", " + p[1].toFixed(5));
  ligne(tj("engine/static/engine/js/geolocalisation.js:lbl_distance_parcourue"), fmtM(s.distance_m)); ligne(tj("engine/static/engine/js/geolocalisation.js:lbl_vitesse_moy_max"), (s.vitesse_moy_ms * 3.6).toFixed(1) + " / " + (s.vitesse_max_ms * 3.6).toFixed(1) + " km/h");
  if (s.cap_actuel_deg != null) ligne("Cap", Math.round(s.cap_actuel_deg) + "°");
  ligne(tj("engine/static/engine/js/geolocalisation.js:482:10"), s.tortuosite + tj("engine/static/engine/js/geolocalisation.js:482:39"));
  if (s.sauts) ligne(tj("engine/static/engine/js/geolocalisation.js:483:23"), s.sauts);
  if (s.manquantes) ligne(tj("engine/static/engine/js/geolocalisation.js:lbl_positions_manquantes"), s.manquantes);
}

// ── Interactions ─────────────────────────────────────────────────────────────
const pointeurs = new Map();
let deplace = 0, distPince = 0;
function zoomer(px, py, dz) {
  const g = versGeo(px, py); S.vue.z = clamp(S.vue.z + dz, 1, 19);
  const e = echelle(); S.vue.cx = mxr(g[1]) - (px - S.W / 2) / e; S.vue.cy = clamp(myr(g[0]) - (py - S.H / 2) / e, 0, 1); S.sale = true;
}
cv.addEventListener("pointerdown", e => { cv.setPointerCapture(e.pointerId); pointeurs.set(e.pointerId, [e.offsetX, e.offsetY]); deplace = 0; if (pointeurs.size === 2) { const [a, b] = [...pointeurs.values()]; distPince = Math.hypot(a[0] - b[0], a[1] - b[1]); } });
cv.addEventListener("pointermove", e => {
  const avant = pointeurs.get(e.pointerId); if (!avant) return;
  const now = [e.offsetX, e.offsetY];
  if (pointeurs.size === 1) {
    const dx = now[0] - avant[0], dy = now[1] - avant[1], sc = echelle(); deplace += Math.abs(dx) + Math.abs(dy);
    S.vue.cx -= dx / sc; S.vue.cy = clamp(S.vue.cy - dy / sc, 0, 1); S.sale = true;
  } else if (pointeurs.size === 2) {
    pointeurs.set(e.pointerId, now);
    const [a, b] = [...pointeurs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
    if (distPince > 0 && d > 0) zoomer((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.log2(d / distPince));
    distPince = d; deplace += 10;
  }
  pointeurs.set(e.pointerId, now);
});
function finPointeur(e) {
  const eraitClic = pointeurs.size === 1 && deplace < 5 && e.type === "pointerup";
  pointeurs.delete(e.pointerId); distPince = 0;
  if (eraitClic) clic(e.offsetX, e.offsetY);
}
cv.addEventListener("pointerup", finPointeur); cv.addEventListener("pointercancel", finPointeur);
cv.addEventListener("wheel", e => { e.preventDefault(); zoomer(e.offsetX, e.offsetY, -e.deltaY * 0.0015); }, {passive: false});
cv.addEventListener("dblclick", e => zoomer(e.offsetX, e.offsetY, 1));
function clic(px, py) {
  if ((window.GEO_CLIC || []).some(f => f(px, py))) return;
  if (S.mesure.actif) {
    if (S.mesure.pts.length >= 2) S.mesure.pts = [];
    S.mesure.pts.push(versGeo(px, py)); S.sale = true; return;
  }
  if (!S.D || S.D.vide) return;
  let meilleur = null, dmin = 16, choisi = null;
  for (const o of objetsPoints()) for (const pt of o.points) {
    const [x, y] = versEcran(pt[0], pt[1]), d = Math.hypot(x - px, y - py);
    if (d < dmin) { dmin = d; meilleur = o.id; choisi = {id: o.id, pt}; }
  }
  S.point = choisi;
  for (const o of objetsTrajets()) {
    const p = pos(o, S.k); if (!p) continue;
    const [x, y] = versEcran(p[0], p[1]), d = Math.hypot(x - px, y - py);
    if (d < dmin) { dmin = d; meilleur = o.id; S.point = null; }
  }
  S.selection = meilleur; majInfo(); S.sale = true;
}
function basculerDemo() {
  S.demo = !S.demo; S.visibles = {}; S.couleurs = {}; S.selection = null;
  $("btn-demo").textContent = S.demo ? tj("engine/static/engine/js/geolocalisation.js:539:41") : tj("engine/static/engine/js/geolocalisation.js:539:77");
  charger(false, false).catch(() => { S.demo = false; });
}

$("lec-play").onclick = () => {
  if (!S.D || S.D.vide) return;
  if (!S.lecture && S.k >= S.D.t.length - 1.001) S.k = 0;
  S.lecture = !S.lecture; majBoutonPlay();
};
$("lec-curseur").oninput = e => { S.k = parseFloat(e.target.value); S.lecture = false; majBoutonPlay(); majCurseur(); majInfo(); S.sale = true; };
$("lec-vitesse").onchange = e => { S.vitesse = parseFloat(e.target.value); };
document.querySelectorAll("[data-couche]").forEach(c => { c.onchange = () => { S.couches[c.dataset.couche] = c.checked; S.sale = true; }; });
// Tuiles locales : le serveur construit tout seul son cache de fond de carte (récolte lente en arrière-plan). Zipper / Restaurer / Pause : administrateur.
(function tuilesLocales() {
  const etat = document.getElementById("gt-etat"); if (!etat) return;
  const bPause = document.getElementById("gt-pause"), bZip = document.getElementById("gt-zip"), bRest = document.getElementById("gt-restaurer"), fich = document.getElementById("gt-fichier");
  const URL_T = "/api/geolocalisation/tuiles/";
  const mo = o => (o / 1048576).toFixed(o < 10485760 ? 1 : 0) + " Mo";
  let dernier = null;
  const montrer = d => {
    dernier = d;
    const phase = !d.actif ? tj("engine/static/engine/js/geolocalisation.js:614:31") : d.pause ? tj("engine/static/engine/js/geolocalisation.js:614:62") : d.hors_ligne ? tj("engine/static/engine/js/geolocalisation.js:614:98") : tj("engine/static/engine/js/geolocalisation.js:614:135") + (d.phase || "…");
    etat.textContent = d.nb + tj("engine/static/engine/js/geolocalisation.js:615:32") + mo(d.octets) + ") · " + phase + (d.actif && d.file ? " · " + d.file + tj("engine/static/engine/js/geolocalisation.js:615:125") : "");
    [bPause, bZip, bRest].forEach(b => { b.hidden = !d.admin; });
    bPause.hidden = !d.admin || !d.actif && !d.pause;
    bPause.textContent = d.pause ? tj("engine/static/engine/js/geolocalisation.js:618:37") : "⏸ Pause";
  };
  const lire = async () => { if (document.hidden) return; try { const r = await fetch(URL_T + "etat/"); if (r.ok) montrer(await r.json()); } catch (e) { /* hors ligne : on réessaiera */ } };
  bPause.onclick = async () => { if (!dernier) return; try { const r = await fetch(URL_T + "etat/", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()}, body: JSON.stringify({pause: !dernier.pause})}); if (r.ok) montrer(await r.json()); } catch (e) { /* ignoré */ } };
  fich.onchange = async () => {
    const f = fich.files && fich.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("fichier", f); etat.textContent = tj("engine/static/engine/js/geolocalisation.js:624:77");
    try {
      const r = await fetch(URL_T + "restaurer/", {method: "POST", headers: {"X-CSRFToken": csrf()}, body: fd}), d = await r.json();
      etat.textContent = r.ok ? d.ajoutees + tj("engine/static/engine/js/geolocalisation.js:627:47") + d.ignorees + tj("engine/static/engine/js/geolocalisation.js:627:83") + (d.refusees ? ", " + d.refusees + tj("engine/static/engine/js/geolocalisation.js:709:210") : "") : (d.erreur || tj("engine/static/engine/js/geolocalisation.js:627:167"));
      if (r.ok) { S.tuiles.clear(); S.sale = true; setTimeout(lire, 4000); }
    } catch (e) { etat.textContent = tj("engine/static/engine/js/geolocalisation.js:629:39"); }
    fich.value = "";
  };
  lire(); setInterval(lire, 15000);
})();
$("btn-analyser").onclick = () => charger(false, true).catch(() => {});
if ($("btn-simuler")) $("btn-simuler").onclick = async () => {
  if (S.demo) { toast(tj("engine/static/engine/js/geolocalisation.js:553:24")); return; }
  try {
    const d = await charger(true, true);
    toast(d.vide ? tj("engine/static/engine/js/geolocalisation.js:556:21") : d.enregistre ? tj("engine/static/engine/js/geolocalisation.js:556:60") + d.enregistre + tj("engine/static/engine/js/geolocalisation.js:556:107") : tj("engine/static/engine/js/geolocalisation.js:556:143"));
  } catch (e) { /* le toast d'erreur est déjà affiché par api() */ }
};
$("btn-demo").onclick = basculerDemo;
$("btn-mesure").onclick = () => {
  S.mesure.actif = !S.mesure.actif; S.mesure.pts = []; cv.classList.toggle("mesure", S.mesure.actif);
  $("btn-mesure").textContent = S.mesure.actif ? tj("engine/static/engine/js/geolocalisation.js:562:51") : tj("engine/static/engine/js/geolocalisation.js:562:74"); S.sale = true;
};
$("btn-ajuster").onclick = ajuster;
GYPleinEcran.init($("zone-carte"), $("btn-quitter-plein-ecran"));   // plein écran = la carte seule
$("btn-plein-ecran").onclick = GYPleinEcran.basculer;
$("btn-geojson").onclick = () => {
  if (!S.D || S.D.vide) { toast(tj("engine/static/engine/js/geolocalisation.js:568:34"), true); return; }
  const f = S.D.objets.map(o => ({type: "Feature", properties: Object.assign({id: o.id, nom: o.nom, mode: o.mode || "trajectoire"}, o.stats),
    geometry: estTrajet(o) ? {type: "LineString", coordinates: o.lat.map((a, k) => o.lat[k] == null ? null : [o.lon[k], a]).filter(Boolean)}
      : {type: "MultiPoint", coordinates: (o.points || []).map(pt => [pt[1], pt[0]])}}));
  (S.D.prevision.collisions || []).forEach(c => f.push({type: "Feature", properties: {type: "collision_prevue", a: c.a, b: c.b, proba: c.proba, dans_s: c.dans_s}, geometry: {type: "Point", coordinates: [c.lon, c.lat]}}));
  (S.D.rencontres || []).forEach(c => f.push({type: "Feature", properties: {type: "rencontre", a: c.a, b: c.b, dmin_m: c.dmin_m}, geometry: {type: "Point", coordinates: [c.lon, c.lat]}}));
  const a = el("a", {href: URL.createObjectURL(new Blob([JSON.stringify({type: "FeatureCollection", features: f})], {type: "application/geo+json"})), download: "geolocalisation.geojson"});
  document.body.append(a); a.click(); a.remove();
};
document.addEventListener("keydown", e => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
  if (e.key === " ") { e.preventDefault(); $("lec-play").click(); }
  else if (e.key === "f") ajuster();
  else if (e.key === "Escape") { GYPleinEcran.quitter(); S.selection = null; majInfo(); S.sale = true; }
});

// ── Dimensionnement : la carte prend TOUTE la taille du canevas ──────────────
function redim() {
  const r = zone.getBoundingClientRect(); S.dpr = window.devicePixelRatio || 1; S.W = Math.max(1, r.width); S.H = Math.max(1, r.height);
  cv.width = Math.round(S.W * S.dpr); cv.height = Math.round(S.H * S.dpr); S.sale = true;
}
if (window.ResizeObserver) new ResizeObserver(redim).observe(zone); else window.addEventListener("resize", redim);
redim();

// ── Rafraîchissement : la carte suit la table. Le serveur prévient par WebSocket (evenement « geo_positions » de l'entreprise) dès qu'un flux écrit dans une ──
// table affichée ; le contrôle léger de l'empreinte toutes les 20 s reste le secours (sans WebSocket ou sans Redis). Un seul contrôle à la fois : une rafale
// de messages ne doit pas empiler des analyses, elle en déclenche UNE de plus quand la précédente est finie.
let controleEnCours = false, controleSuivant = false;
async function rafraichir() {
  if (document.hidden || S.demo || S.archive || S.lecture || !S.D) return;
  if (controleEnCours) { controleSuivant = true; return; }
  controleEnCours = true;
  try {
    const r = await fetch("/api/tables/" + G.tableId + "/geolocalisation/analyse/", {method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
      body: JSON.stringify(Object.assign({empreinte_seule: 1}, parametres()))});
    const d = await r.json();
    if (r.ok && d.empreinte && d.empreinte !== S.D.empreinte) await charger(false, true);
  } catch (e) { /* réseau indisponible : on réessaiera */ }
  controleEnCours = false;
  if (controleSuivant) { controleSuivant = false; setTimeout(rafraichir, 1000); }
}
setInterval(rafraichir, 20000);
let essaisWs = 0;
function ecouterFlux() {
  if (!window.WebSocket || !G.entrepriseId) return;
  const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/entreprise/" + G.entrepriseId + "/");
  ws.onopen = () => { essaisWs = 0; };
  ws.onmessage = e => {
    try {
      const m = JSON.parse(e.data);
      if (m.evenement === "geo_positions" && m.donnees && (Number(m.donnees.table_id) === Number(G.tableId) || tablesCochees().includes(String(m.donnees.table_id)))) rafraichir();
    } catch (x) { /* message d'un autre usage : ignoré */ }
  };
  ws.onclose = () => setTimeout(ecouterFlux, Math.min(30000, 1000 * Math.pow(2, Math.min(essaisWs++, 5))));
}
ecouterFlux();

// ── Flux de positions (AIS, GPS, ADS-B, JSON, CSV) : décodage et rangement côté serveur (engine/flux_positions.py, engine/geo_flux.py) ──
// Un flux plus gros que la limite d'UNE requête (G.fluxMax, donnée par le serveur) est envoyé en morceaux, l'un après l'autre : rien ne s'écrit deux fois
// (une ligne déjà là est seulement complétée), donc un envoi interrompu se relance tel quel.
if ($("btn-flux-importer")) {
  const LIMITE = G.fluxMax || 5 * 1024 * 1024;
  let fichier = null;   // texte d'un fichier choisi (la zone de texte n'en garde que les petits)
  const mesure = t => new TextEncoder().encode(JSON.stringify(t)).length;   // taille réelle dans la requête : guillemets et sauts de ligne y sont échappés
  const PHRASE_AIS = /[!$][A-Z]{2}VD[MO],(\d+),(\d+),([^,]*),([^,]*),/;   // total, numéro, séquence, canal d'une phrase AIS
  function coupeAis(texte, debut, fin) {   // `fin` suit un saut de ligne : on la recule au début d'une trame AIS en plusieurs phrases restée ouverte (ses phrases partent ensemble)
    let a = fin;
    for (let k = 0; k < 40 && a > debut; k++) { const c = texte.lastIndexOf("\n", a - 2); a = c < debut ? debut : c + 1; }
    const ouvertes = new Map(); let p = a;
    for (const l of texte.slice(a, fin).split("\n")) {
      const m = PHRASE_AIS.exec(l);
      if (m && +m[1] > 1) {
        const cle = m[3] + "|" + m[4];
        if (+m[2] >= +m[1]) ouvertes.delete(cle); else if (+m[2] === 1 || !ouvertes.has(cle)) ouvertes.set(cle, p);
      }
      p += l.length + 1;
    }
    const ouvert = Math.min(fin, ...ouvertes.values());
    return ouvert > debut ? ouvert : fin;   // une trame qui remplit tout le morceau reste où elle est
  }
  function parLignes(texte) {
    const sortie = []; let debut = 0;
    while (debut < texte.length) {
      let fin = Math.min(texte.length, debut + 1000000);
      while (fin - debut > 1 && mesure(texte.slice(debut, fin)) > LIMITE * 0.9) fin = debut + ((fin - debut) >> 1);
      if (fin < texte.length) { const c = texte.lastIndexOf("\n", fin - 1); if (c > debut) fin = coupeAis(texte, debut, c + 1); }
      sortie.push(texte.slice(debut, fin)); debut = fin;
    }
    return sortie;
  }
  function parElements(d) {   // document JSON unique : découpé par éléments de son plus gros tableau (liste, GeoJSON, aircraft.json, OpenSky)
    const cle = Array.isArray(d) ? null : Object.keys(d || {}).filter(k => Array.isArray(d[k])).sort((a, b) => d[b].length - d[a].length)[0];
    const liste = Array.isArray(d) ? d : cle ? d[cle] : null;
    if (!liste) return null;
    const sortie = []; let lot = [], taille = 0;
    const emettre = () => { if (lot.length) { sortie.push(JSON.stringify(cle ? Object.assign({}, d, {[cle]: lot}) : lot)); lot = []; taille = 0; } };
    for (const x of liste) {
      const t = mesure(JSON.stringify(x)) + 1;
      if (lot.length && taille + t > LIMITE * 0.7) emettre();
      lot.push(x); taille += t;
    }
    emettre();
    return sortie;
  }
  function morceaux(texte) {
    if (mesure(texte) <= LIMITE * 0.9) return [texte];
    let d = null;
    if (/^\s*[\[{]/.test(texte)) { try { d = JSON.parse(texte); } catch (e) { d = null; } }   // un document JSON entier, sinon des lignes
    if (d === null) return parLignes(texte);
    const p = parElements(d);
    if (!p) throw new Error(tj("engine/static/engine/js/geolocalisation.js:678:30"));
    return p;
  }
  $("f-fichier").onchange = async e => {
    const f = e.target.files[0];
    if (!f) return;
    fichier = await f.text();
    $("f-texte").value = fichier.length <= 200000 ? fichier : "";
    if (fichier.length > 200000) $("flux-resultat").textContent = tj("engine/static/engine/js/geolocalisation.js:686:68") + f.name + " (" + (f.size / 1048576).toFixed(1) + tj("engine/static/engine/js/geolocalisation.js:686:136");
  };
  $("f-texte").oninput = () => { fichier = null; };
  const resumeFlux = (d, apercu) => {
    const r = d.rapport, w = d.ecriture, res = $("flux-resultat");
    res.textContent = "";
    const ligne = (...x) => res.append(el("div", {}, ...x));
    ligne(el("b", {}, r.positions + " position(s)"), tj("engine/static/engine/js/geolocalisation.js:693:55") + r.objets + tj("engine/static/engine/js/geolocalisation.js:693:88") + r.lignes + tj("engine/static/engine/js/geolocalisation.js:693:119"));
    if (r.rejetees) ligne(r.rejetees + tj("engine/static/engine/js/geolocalisation.js:694:41"));
    if (r.ignorees) ligne(r.ignorees + tj("engine/static/engine/js/geolocalisation.js:695:41"));
    (d.objets || []).slice(0, 8).forEach(o => ligne("• " + o.nom + (o.nom !== o.id ? " (" + o.id + ")" : "") + " : " + o.positions));
    if ((d.objets || []).length > 8) ligne(tj("engine/static/engine/js/geolocalisation.js:et_n_autres", {v1: d.objets.length - 8}));
    if (!r.positions) ligne(tj("engine/static/engine/js/geolocalisation.js:698:30"));
    else if (apercu) ligne(tj("engine/static/engine/js/geolocalisation.js:699:29"));
    else if (w) {
      ligne(el("b", {}, w.lignes_creees + tj("engine/static/engine/js/geolocalisation.js:701:44")), ", " + w.lignes_completees + tj("engine/static/engine/js/geolocalisation.js:701:96"));
      if (w.colonnes_creees.length) ligne(tj("engine/static/engine/js/geolocalisation.js:702:44") + w.colonnes_creees.join(", ") + ".");
      if (w.colonne_temps_creee) ligne(tj("engine/static/engine/js/geolocalisation.js:703:41"));
      if (w.objets_sans_colonne) ligne(w.objets_sans_colonne + tj("engine/static/engine/js/geolocalisation.js:704:65"));
      if (w.lignes_tronquees) ligne(w.lignes_tronquees + tj("engine/static/engine/js/geolocalisation.js:705:59"));
    }
  };
  const cumuler = (A, d) => {   // additionne les comptes rendus des morceaux
    if (!A) return d;
    const r = A.rapport, q = d.rapport, par = new Map((A.objets || []).map(o => [o.id, o]));
    ["positions", "lignes", "rejetees", "ignorees"].forEach(k => { r[k] = (r[k] || 0) + (q[k] || 0); });
    (d.objets || []).forEach(o => { const x = par.get(o.id); if (x) x.positions += o.positions; else par.set(o.id, o); });
    A.objets = [...par.values()];
    r.objets = Math.max(r.objets || 0, q.objets || 0, A.objets.length);
    if (d.ecriture) {
      const w = A.ecriture, v = d.ecriture;
      if (!w) A.ecriture = v;
      else {
        ["lignes_creees", "lignes_completees", "positions", "lignes_tronquees", "lignes_supprimees"].forEach(k => { w[k] = (w[k] || 0) + (v[k] || 0); });
        w.colonnes_creees = w.colonnes_creees.concat(v.colonnes_creees);
        w.objets_sans_colonne = Math.max(w.objets_sans_colonne, v.objets_sans_colonne);
        w.colonne_temps_creee = w.colonne_temps_creee || v.colonne_temps_creee;
      }
    }
    return A;
  };
  const envoyerFlux = async apercu => {
    const texte = $("f-texte").value || fichier || "", res = $("flux-resultat");
    if (!texte.trim()) { toast(tj("engine/static/engine/js/geolocalisation.js:729:33"), true); return; }
    let parts;
    try { parts = morceaux(texte); } catch (e) { res.textContent = e.message; return; }
    let total = null;
    for (let i = 0; i < parts.length; i++) {
      res.textContent = parts.length > 1 ? tj("engine/static/engine/js/geolocalisation.js:734:45") + (i + 1) + " / " + parts.length + "…" : tj("engine/static/engine/js/geolocalisation.js:734:95");
      try {
        total = cumuler(total, await api("/api/tables/" + G.tableId + "/geolocalisation/flux/", {method: "POST", body: JSON.stringify({
          donnees: parts[i], format: $("f-format").value, pas: $("f-pas").value, id_defaut: $("f-id").value, horodatage: $("f-horodatage").value,
          creer_colonnes: $("f-creer").checked ? 1 : 0, apercu: apercu ? 1 : 0})}));
      } catch (e) {
        res.textContent = ((e && e.erreur) || tj("engine/static/engine/js/geolocalisation.js:740:48")) + (i && !apercu ? " " + i + tj("engine/static/engine/js/geolocalisation.js:740:98") + parts.length + tj("engine/static/engine/js/geolocalisation.js:740:134") : "");
        return;
      }
    }
    resumeFlux(total, apercu);
    const w = total.ecriture;
    if (!apercu && w && (w.lignes_creees || w.lignes_completees)) {
      toast(w.lignes_creees + tj("engine/static/engine/js/geolocalisation.js:747:32") + w.lignes_completees + tj("engine/static/engine/js/geolocalisation.js:912:112"));
      if (S.demo) basculerDemo(); else await charger(false, true);   // la carte montre tout de suite les nouvelles trajectoires
    }
  };
  $("btn-flux-apercu").onclick = () => envoyerFlux(true);
  $("btn-flux-importer").onclick = () => envoyerFlux(false);
}

// ── Veille continue : le serveur surveille les tables de positions en arrière-plan (engine/geo_veille.py) ; ici on relit son état et on retrouve les alertes ──
{
  const URL_VEILLE = "/api/tables/" + G.tableId + "/geolocalisation/veille/", ICONE = {critique: "🚨", attention: "⚠️"};
  const voirAlerte = a => {   // se placer sur l'alerte : lieu et instant (rencontre), sinon lieu probable (collision prévue)
    if (a.lat != null && a.lon != null) centrerSur(a.lat, a.lon, 12);
    if (a.t != null && S.D && !S.D.vide) { S.k = clamp(indiceDeTemps(a.t), 0, S.D.t.length - 1); S.lecture = false; majBoutonPlay(); majCurseur(); }
    S.sale = true;
  };
  const montrerVeille = (v, reglages) => {
    const n = $("veille-etat"), liste = $("veille-alertes"), r = v.reglages, e = v.etat, p = v.processus;
    n.textContent = "";
    n.append(el("div", {}, el("b", {}, p.actif ? tj("engine/static/engine/js/geolocalisation.js:792:51") : tj("engine/static/engine/js/geolocalisation.js:792:73")), p.actif ? " depuis " + String(p.demarre || "").replace("T", " ") + "." : tj("engine/static/engine/js/geolocalisation.js:792:165")));
    if (!r.active) n.append(el("div", {}, tj("engine/static/engine/js/geolocalisation.js:793:44")));
    n.append(el("div", {}, e ? tj("engine/static/engine/js/geolocalisation.js:794:33") + e.ts.replace("T", " ") + " — " + e.objets + " objet(s), " + e.positions + " position(s)." : tj("engine/static/engine/js/geolocalisation.js:794:152")));
    if (reglages && $("veille-active")) {
      $("veille-active").checked = r.active;
      document.querySelectorAll("[data-veille-objet]").forEach(c => { c.checked = !r.exclus.includes(c.dataset.veilleObjet); });
    }
    liste.textContent = "";
    ((e && e.alertes) || []).forEach(a => liste.append(el("div", {class: "geo-note", style: "cursor:pointer", title: tj("engine/static/engine/js/geolocalisation.js:800:119"), onclick: () => voirAlerte(a)}, (ICONE[a.gravite] || "") + " " + a.titre + " — " + a.message)));
    if (e && !(e.alertes || []).length) liste.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geolocalisation.js:801:86")));
  };
  const lireVeille = async reglages => { try { const r = await fetch(URL_VEILLE); if (r.ok) montrerVeille(await r.json(), reglages); } catch (x) { /* hors ligne : on réessaiera */ } };
  const envoyerVeille = async extra => {
    const p = parametres();
    const corps = Object.assign({active: $("veille-active").checked, exclus: Array.from(document.querySelectorAll("[data-veille-objet]")).filter(c => !c.checked).map(c => c.dataset.veilleObjet),
      parametres: {seuil: p.seuil, horizon: p.horizon, sigma_mesure: p.sigma_mesure, mode: p.mode || "auto"}}, extra || {});
    try { montrerVeille(await api(URL_VEILLE, {method: "POST", body: JSON.stringify(corps)}), true); return true; } catch (x) { return false; }
  };
  if ($("btn-veille-enregistrer")) {
    $("btn-veille-enregistrer").onclick = async () => { if (await envoyerVeille()) toast(tj("engine/static/engine/js/geolocalisation.js:811:91")); };
    $("btn-veille-evaluer").onclick = async () => { if (await envoyerVeille({evaluer: 1})) toast(tj("engine/static/engine/js/geolocalisation.js:812:99")); };
  }
  lireVeille(true);
  setInterval(() => { if (!document.hidden) lireVeille(false); }, 15000);
  // Retour sur l'onglet : la carte et la veille se remettent à jour tout de suite (rien n'a été perdu pendant l'absence, tout a continué côté serveur).
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { lireVeille(false); rafraichir(); } });
}

// ── Archives : les positions anciennes quittent la table mais restent en fichiers (engine/geo_archives.py) ; on les consulte et on les rejoue ici ──
if ($("btn-archive-rejouer")) {
  const URL_ARCH = "/api/tables/" + G.tableId + "/geolocalisation/archives/";
  const majArchive = async () => {
    const n = $("archive-etat");
    try {
      const r = await fetch(URL_ARCH), d = await r.json();
      n.textContent = "";
      if (!r.ok) { n.textContent = d.erreur || tj("engine/static/engine/js/geolocalisation.js:lbl_archives_indispo"); return; }
      n.append(el("div", {}, tj("engine/static/engine/js/geolocalisation.js:829:31") + d.conserve + tj("engine/static/engine/js/geolocalisation.js:829:68")));
      n.append(el("div", {}, d.fichiers.length ? d.fichiers.length + tj("engine/static/engine/js/geolocalisation.js:830:71") : tj("engine/static/engine/js/geolocalisation.js:830:100")));
      d.fichiers.slice(-10).forEach(f => n.append(el("div", {}, el("a", {href: URL_ARCH + "?fichier=" + encodeURIComponent(f.nom)}, f.nom), " (" + tj("engine/static/engine/js/geolocalisation.js:unite_ko", {v1: Math.max(1, Math.round(f.octets / 1024))}) + ")")));
    } catch (x) { n.textContent = tj("engine/static/engine/js/geolocalisation.js:lbl_archives_indispo"); }
  };
  const ms = v => v ? String(new Date(v).getTime()) : "";   // date locale saisie -> millisecondes : aucun décalage de fuseau
  $("btn-archive-rejouer").onclick = async () => {
    const du = ms($("a-du").value), au = ms($("a-au").value);
    if (!du || !au) { toast(tj("engine/static/engine/js/geolocalisation.js:837:30"), true); return; }
    S.archive = {du, au}; S.demo = false;
    $("btn-archive-direct").style.display = "";
    try { await charger(false, false); } catch (x) { S.archive = null; $("btn-archive-direct").style.display = "none"; }
  };
  $("btn-archive-direct").onclick = async () => { S.archive = null; $("btn-archive-direct").style.display = "none"; try { await charger(false, false); } catch (x) { /* déjà signalé */ } };
  majArchive();
}

window.GEO_API = {S, selectionnerObjet, versEcran, versGeo, mParPx, api, toast, el, fmtM, fmtDuree, centrerSur, hav, ajuster, redessiner: () => { S.sale = true; }};
requestAnimationFrame(boucle);
charger(false, false).catch(() => {});
})();
