// LOGISTIQUE sur la carte de géolocalisation : tournées optimisées (calcul serveur, engine/logistique.py), dessinées par-dessus les
// trajectoires. Véhicules = objets suivis de la table ; commandes = lignes d'une autre table. S'appuie sur window.GEO_API (geolocalisation.js).
(function () {
"use strict";
const A = window.GEO_API, G = window.GEO;
if (!A || !$("l-table")) return;
function $(id) { return document.getElementById(id); }
const COUL = [gyCouleur("--accent", "#6bcfff"), gyCouleur("--forme-orange", "#f5a524"), gyCouleur("--ok", "#2ecc71"), gyCouleur("--danger", "#e74c3c"), gyCouleur("--forme-violet", "#b48ead"), gyCouleur("--forme-jaune", "#f7d774"), gyCouleur("--forme-cyan", "#5dd6c0"), gyCouleur("--forme-rose", "#ff7ab8"), gyCouleur("--forme-vert", "#9ad06b"), gyCouleur("--forme-orange", "#ff9f6b"), gyCouleur("--forme-bleu", "#7aa2ff"), gyCouleur("--forme-clair", "#d4d4d4")];
const L = {plan: null, depot: null, demo: false, choixDepot: false, actif: false};
const coul = v => COUL[Math.max(0, (L.plan ? L.plan.routes.findIndex(r => r.vehicule === v) : 0)) % COUL.length];
const hm = s => { const m = Math.round(s / 60); return m < 60 ? m + " min" : Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0"); };

function corps(enregistrer) {
  const c = $("l-capacite").value;
  return JSON.stringify({commandes_table: $("l-table").value, capacite: c || null, service_min: $("l-service").value, retour_depot: $("l-retour").checked,
    depuis_position: $("l-position").checked, depot: L.depot, enregistrer: !!enregistrer, demo: L.demo === "strategique" ? "strategique" : L.demo ? 1 : 0});
}
async function optimiser(enregistrer, silencieux) {
  if (!L.demo && !$("l-table").value) { A.toast(tj("engine/static/engine/js/logistique.js:19:50"), true); return; }
  try {
    const p = await A.api("/api/tables/" + G.tableId + "/logistique/planifier/", {method: "POST", body: corps(enregistrer)});
    L.plan = p; L.actif = !p.vide; rendre();
    if (p.vide) { if (!silencieux) A.toast(p.message || tj("engine/static/engine/js/logistique.js:23:58"), true); }
    else if (enregistrer) A.toast(p.enregistre ? tj("engine/static/engine/js/logistique.js:24:51") + p.enregistre + tj("engine/static/engine/js/logistique.js:24:94") : tj("engine/static/engine/js/logistique.js:24:126"));
    A.redessiner();
  } catch (e) { /* toast déjà affiché par api() */ }
}

function rendre() {
  const r = $("logi-resume"), t = $("logi-tournees"), p = L.plan;
  r.textContent = ""; t.textContent = "";
  $("btn-depot-conseille").style.display = p && !p.vide && p.depot_conseille && p.alertes.some(a => a.type === "depot_mal_place") ? "block" : "none";
  if (!p || p.vide) { r.textContent = p && p.message ? p.message : ""; return; }
  const s = p.resume, lignes = [
    [tj("engine/static/engine/js/logistique.js:lbl_commandes"), s.commandes - s.non_affectees + tj("engine/static/engine/js/logistique.js:35:52") + s.commandes],
    [tj("engine/static/engine/js/logistique.js:36:7"), s.vehicules_utilises + tj("engine/static/engine/js/logistique.js:lbl_sur") + s.vehicules + " (mini. " + s.flotte_min + tj("engine/static/engine/js/logistique.js:36:95") + s.capacite + ")"],
    ["Distance", A.fmtM(s.distance_m) + (s.gain != null ? " (" + Math.round(s.gain * 100) + tj("engine/static/engine/js/logistique.js:37:94") : "")],
    [tj("engine/static/engine/js/logistique.js:38:7"), hm(s.duree_max_s)], [tj("engine/static/engine/js/logistique.js:lbl_remplissage"), Math.round(s.utilisation * 100) + " %"]];
  if (s.groupes > 1) lignes.push([tj("engine/static/engine/js/logistique.js:39:36"), s.groupes + tj("engine/static/engine/js/logistique.js:39:58") + s.temps_calcul_s + " s"]);
  if (s.attente_total_s) lignes.push([tj("engine/static/engine/js/logistique.js:40:40"), hm(s.attente_total_s) + tj("engine/static/engine/js/logistique.js:40:115")]);
  if (s.retards) lignes.push([tj("engine/static/engine/js/logistique.js:lbl_retards"), s.retards + " livraison(s), " + hm(s.retard_total_s) + tj("engine/static/engine/js/logistique.js:41:98")]);
  lignes.forEach(([k, v]) => r.append(A.el("div", {style: "margin-bottom:3px"}, A.el("b", {}, k + " : "), v)));
  p.alertes.forEach(a => r.append(A.el("div", {class: "geo-alerte " + a.gravite, style: "margin-top:4px"}, a.detail)));
  p.routes.forEach((rt, i) => t.append(A.el("button", {class: "geo-alerte", style: "border-color:" + COUL[i % COUL.length],
    onclick: () => { const e = rt.etapes[0]; if (e) A.centrerSur(e.lat, e.lon, 13); }},
    rt.nom + (rt.mode !== "route" ? " (" + rt.mode + ")" : "") + (rt.rotation > 1 ? " voyage " + rt.rotation : "") + " : " + rt.etapes.length + tj("engine/static/engine/js/logistique.js:46:146") + A.fmtM(rt.distance_m) + ", " + hm(rt.duree_s) + ", charge " + rt.charge + "/" + rt.capacite + (rt.retard_s ? tj("engine/static/engine/js/logistique.js:46:271") : ""))));
}

function dessin(ctx) {
  const p = L.plan; if (!L.actif || !p || p.vide) return;
  (p.zones || []).forEach(z => {                                        // zones interdites (cercles contournés par le moteur)
    const [x, y] = A.versEcran(z.lat, z.lon), [x2, y2] = A.versEcran(z.lat + z.rayon_m / 111320, z.lon), r = Math.hypot(x2 - x, y2 - y);
    ctx.setLineDash([6, 4]); ctx.strokeStyle = gyCouleur("--forme-rouge", "#ff4d4d"); ctx.fillStyle = gyCouleur("--forme-rouge", "#ff4d4d", 0.12); ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(x, y, r, 0, 2 * Math.PI); ctx.fill(); ctx.stroke(); ctx.setLineDash([]);
  });
  p.routes.forEach((rt, i) => {
    ctx.strokeStyle = COUL[i % COUL.length]; ctx.lineWidth = 2.5; ctx.globalAlpha = 0.9; ctx.beginPath();
    rt.chemin.forEach((c, k) => { const [x, y] = A.versEcran(c[0], c[1]); if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.stroke(); ctx.globalAlpha = 1;
    ctx.font = "bold 10px sans-serif"; ctx.textAlign = "center";
    rt.etapes.forEach(e => {
      const [x, y] = A.versEcran(e.lat, e.lon);
      ctx.fillStyle = e.retard_s ? gyCouleur("--forme-rouge","#ff4d4d") : COUL[i % COUL.length]; ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.rect(x - 8, y - 8, 16, 16); ctx.fill(); ctx.stroke();
      ctx.fillStyle = (getComputedStyle(document.body).getPropertyValue("--texte-noir").trim() || "#000"); ctx.fillText(String(e.ordre), x, y + 3.5);
    });
    ctx.textAlign = "left";
  });
  for (const c of p.commandes) if (!c.affectee) {                       // commandes sans véhicule
    const [x, y] = A.versEcran(c.lat, c.lon); ctx.strokeStyle = gyCouleur("--forme-rouge", "#ff4d4d"); ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.moveTo(x - 7, y - 7); ctx.lineTo(x + 7, y + 7); ctx.moveTo(x + 7, y - 7); ctx.lineTo(x - 7, y + 7); ctx.stroke();
  }
  const dc = p.depot_conseille;
  if (dc && !(dc.lat === p.depot.lat && dc.lon === p.depot.lon)) {       // dépôt conseillé
    const [x, y] = A.versEcran(dc.lat, dc.lon); ctx.setLineDash([3, 3]); ctx.strokeStyle = gyCouleur("--ok", "#2ecc71"); ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(x, y, 9, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = (getComputedStyle(document.body).getPropertyValue("--ok").trim() || "#2ecc71"); ctx.font = "10px sans-serif"; ctx.fillText(tj("engine/static/engine/js/logistique.js:77:76"), x + 12, y + 3);
  }
  const [dx, dy] = A.versEcran(p.depot.lat, p.depot.lon);               // dépôt
  ctx.fillStyle = gyCouleur("--forme-clair", "#fff"); ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.lineWidth = 1.5; ctx.beginPath();
  ctx.moveTo(dx, dy - 11); ctx.lineTo(dx + 10, dy + 7); ctx.lineTo(dx - 10, dy + 7); ctx.closePath(); ctx.fill(); ctx.stroke();
  ctx.fillStyle = (getComputedStyle(document.body).getPropertyValue("--texte-lien").trim() || "#cfe6f5"); ctx.font = "11px sans-serif"; ctx.fillText(tj("engine/static/engine/js/logistique.js:82:74"), dx + 13, dy + 4);
}
function clicCarte(px, py) {
  const p = L.plan;
  if (L.choixDepot) {
    const g = A.versGeo(px, py); L.depot = [Math.round(g[0] * 1e6) / 1e6, Math.round(g[1] * 1e6) / 1e6]; L.choixDepot = false;
    $("btn-depot").textContent = tj("engine/static/engine/js/logistique.js:88:35"); $("btn-depot").classList.remove("principal"); optimiser(false, true); return true;
  }
  if (!L.actif || !p || p.vide) return false;
  for (const rt of p.routes) for (const e of rt.etapes) {               // clic sur une étape : détail
    const [x, y] = A.versEcran(e.lat, e.lon);
    if (Math.abs(x - px) < 10 && Math.abs(y - py) < 10) {
      A.toast(rt.nom + tj("engine/static/engine/js/logistique.js:94:25") + e.ordre + " « " + e.nom + tj("engine/static/engine/js/logistique.js:94:65") + hm(e.eta_s) + tj("engine/static/engine/js/logistique.js:94:98") + e.demande + (e.attente_s ? tj("engine/static/engine/js/logistique.js:94:141") + hm(e.attente_s) : "") + (e.retard_s ? tj("engine/static/engine/js/logistique.js:94:194") + hm(e.retard_s) : ""), !!e.retard_s);
      return true;
    }
  }
  return false;
}

$("btn-optimiser").onclick = () => optimiser(false);
if ($("btn-optimiser-enreg")) $("btn-optimiser-enreg").onclick = () => {
  if (L.demo) { A.toast(tj("engine/static/engine/js/logistique.js:103:26"), true); return; }
  optimiser(true);
};
$("btn-depot").onclick = () => {
  L.choixDepot = !L.choixDepot; $("btn-depot").textContent = L.choixDepot ? tj("engine/static/engine/js/logistique.js:107:78") : tj("engine/static/engine/js/logistique.js:107:104"); $("btn-depot").classList.toggle("principal", L.choixDepot);
};
$("btn-depot-conseille").onclick = () => { const dc = L.plan && L.plan.depot_conseille; if (dc) { L.depot = [dc.lat, dc.lon]; optimiser(false, true); } };
$("btn-logi-demo").onclick = () => basculerDemo(true);
$("btn-logi-strat").onclick = () => basculerDemo("strategique");
function basculerDemo(mode) {
  L.demo = L.demo ? false : mode; L.depot = null; L.plan = null; L.actif = false;
  $("btn-logi-demo").textContent = L.demo === true ? tj("engine/static/engine/js/logistique.js:114:55") : tj("engine/static/engine/js/logistique.js:114:91");
  $("btn-logi-strat").textContent = L.demo === "strategique" ? tj("engine/static/engine/js/logistique.js:115:65") : tj("engine/static/engine/js/logistique.js:115:101");
  if (L.demo) { if (!A.S.demo) $("btn-demo").click(); optimiser(false); } else { rendre(); A.redessiner(); }
}
// la table des véhicules a changé (nouvelles positions) : la tournée repart des positions actuelles
(window.GEO_APRES = window.GEO_APRES || []).push(silencieux => { if (silencieux && L.actif && !L.demo && $("l-position").checked) optimiser(false, true); });
(window.GEO_DESSIN = window.GEO_DESSIN || []).push(Object.assign(dessin, {calque: "logistique"}));
(window.GEO_CLIC = window.GEO_CLIC || []).push(clicCarte);
window.GEO_LOG = {plan: () => L.plan};   // lecture seule : le mode livreur (geo_livreur.js) propose les arrêts de la tournée calculée
})();
