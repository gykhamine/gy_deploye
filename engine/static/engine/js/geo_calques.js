// CALQUES, RECHERCHE D'ADRESSE et IMPORT de la carte de géolocalisation.
//  * Calques : ordre (haut/bas), visibilité et opacité de chaque couche dessinée (fond, annotations, outils tactiques, logistique, résultat de recherche).
//    Chaque couche est dessinée à part puis composée avec son opacité ; réglage mémorisé dans ce navigateur (par table). Pris en charge par geolocalisation.js
//    via window.GEO_CALQUES.dessiner(ctx) ; les hooks GEO_DESSIN portent un nom (propriété `calque`).
//  * Adresse : recherche faite par le serveur (engine/geo_adresses.py), clic sur un résultat = centrage + repère ; « Marquer » l'enregistre en annotation.
//  * Import : GeoJSON, KML/KMZ, GPX, shapefile (.shp ou .zip) → annotations (engine/geo_import.py, API …/geolocalisation/importer/).
(function () {
"use strict";
const A = window.GEO_API, G = window.GEO;
if (!A || !document.getElementById("cq-liste")) return;
const $ = id => document.getElementById(id), el = A.el, S = A.S;
const URL_ADR = "/api/tables/" + G.tableId + "/geolocalisation/adresse/", URL_IMP = "/api/tables/" + G.tableId + "/geolocalisation/importer/";
const CLE = "geo_calques_" + G.tableId;
const NOMS = {fond: tj("engine/static/engine/js/geo_calques.js:lbl_fond_openstreetmap"), annotations: "Annotations", outils: tj("engine/static/engine/js/geo_calques.js:lbl_outils_tactiques"), logistique: tj("engine/static/engine/js/geo_calques.js:lbl_logistique"), recherche: tj("engine/static/engine/js/geo_calques.js:14:137")};
const C = {ordre: ["fond", "annotations", "outils", "logistique", "recherche"], etat: {}, repere: null, hors: null};   // ordre : du dessous vers le dessus
const etat = n => C.etat[n] || (C.etat[n] = {visible: true, opacite: 1});

try { const m = JSON.parse(localStorage.getItem(CLE) || "null"); if (m && Array.isArray(m.ordre) && m.etat) { C.ordre = m.ordre.filter(n => NOMS[n]).concat(Object.keys(NOMS).filter(n => !m.ordre.includes(n))); C.etat = m.etat; } } catch (e) { /* stockage indisponible */ }
const garder = () => { try { localStorage.setItem(CLE, JSON.stringify({ordre: C.ordre, etat: C.etat})); } catch (e) { /* ignoré */ } };

// ── Composition ──────────────────────────────────────────────────────────────
function calque(ctx, f, opacite) {
  if (opacite >= 0.99) { f(ctx); return; }
  const cv = ctx.canvas;
  if (!C.hors) C.hors = document.createElement("canvas");
  if (C.hors.width !== cv.width || C.hors.height !== cv.height) { C.hors.width = cv.width; C.hors.height = cv.height; }
  const h = C.hors.getContext("2d"); h.setTransform(1, 0, 0, 1, 0, 0); h.clearRect(0, 0, C.hors.width, C.hors.height); h.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  f(h);
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = opacite; ctx.drawImage(C.hors, 0, 0); ctx.restore();
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0); ctx.globalAlpha = 1;
}
function dessinRecherche(ctx) {
  if (!C.repere) return;
  const [x, y] = A.versEcran(C.repere.lat, C.repere.lon);
  ctx.strokeStyle = gyCouleur("--forme-rose", "#ff3b6b"); ctx.fillStyle = gyCouleur("--forme-rose", "#ff3b6b"); ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 9, 0, 2 * Math.PI); ctx.stroke();
  ctx.beginPath(); ctx.arc(x, y, 3, 0, 2 * Math.PI); ctx.fill(); ctx.font = "12px sans-serif"; ctx.textAlign = "left"; ctx.lineWidth = 3; ctx.strokeStyle = gyCouleur("--forme-contour", "#000"); ctx.strokeText(C.repere.nom, x + 13, y + 4); ctx.fillStyle = (getComputedStyle(document.body).getPropertyValue("--texte-sombre").trim() || "#fff"); ctx.fillText(C.repere.nom, x + 13, y + 4);
}
window.GEO_CALQUES = {dessiner(ctx) {
  const hooks = window.GEO_DESSIN || [];
  C.ordre.forEach(n => {
    if (n === "fond" || !etat(n).visible) return;
    const fs = n === "recherche" ? [dessinRecherche] : hooks.filter(f => f.calque === n);
    fs.forEach(f => calque(ctx, f, etat(n).opacite));
  });
  hooks.filter(f => !f.calque).forEach(f => f(ctx));   // hooks sans nom : toujours dessinés par-dessus
}};

// ── Panneau des calques ──────────────────────────────────────────────────────
function rendre() {
  const l = $("cq-liste"); l.textContent = "";
  C.ordre.slice().reverse().forEach(n => {   // affichage : le dessus en premier
    const e = etat(n), i = C.ordre.indexOf(n);
    const case_ = el("input", {type: "checkbox", title: tj("engine/static/engine/js/geo_calques.js:53:58"), onchange: ev => { e.visible = ev.target.checked; appliquer(); }});
    case_.checked = e.visible;
    const curseur = el("input", {type: "range", min: "0", max: "1", step: "0.05", title: tj("engine/static/engine/js/geo_calques.js:55:91"), oninput: ev => { e.opacite = parseFloat(ev.target.value); appliquer(true); }});
    curseur.value = e.opacite;
    l.append(el("div", {class: "geo-objet", style: "flex-wrap:wrap"}, case_, el("span", {class: "nom"}, NOMS[n]),
      el("button", {class: "geo-btn", style: "width:auto;margin:0 2px", title: tj("engine/static/engine/js/geo_calques.js:58:81"), disabled: i === C.ordre.length - 1 ? "disabled" : null, onclick: () => deplacer(i, 1)}, "▲"),
      el("button", {class: "geo-btn", style: "width:auto;margin:0 2px", title: tj("engine/static/engine/js/geo_calques.js:lbl_descendre"), disabled: i === 0 ? "disabled" : null, onclick: () => deplacer(i, -1)}, "▼"), curseur));
  });
}
function deplacer(i, d) { const j = i + d; if (j < 0 || j >= C.ordre.length) return; [C.ordre[i], C.ordre[j]] = [C.ordre[j], C.ordre[i]]; appliquer(); rendre(); }
function appliquer(sansRendu) { S.opaciteFond = 0.55 * etat("fond").opacite; S.couches.osm = etat("fond").visible; const c = document.querySelector("[data-couche=osm]"); if (c) c.checked = S.couches.osm; garder(); S.sale = true; }
document.querySelectorAll("[data-couche=osm]").forEach(c => c.addEventListener("change", () => { etat("fond").visible = c.checked; garder(); rendre(); }));
S.opaciteFond = 0.55 * etat("fond").opacite; S.couches.osm = etat("fond").visible;
{ const c = document.querySelector("[data-couche=osm]"); if (c) c.checked = S.couches.osm; }
$("cq-reinit").onclick = () => { C.ordre = Object.keys(NOMS); C.etat = {}; appliquer(); rendre(); };
rendre();

// ── Recherche d'adresse ──────────────────────────────────────────────────────
function aller(r) {
  C.repere = {lat: r.lat, lon: r.lon, nom: r.nom.split(",").slice(0, 2).join(",")};
  let z = 15;
  if (r.emprise) { const dlon = Math.abs(r.emprise[3] - r.emprise[2]), dlat = Math.abs(r.emprise[1] - r.emprise[0]), span = Math.max(dlon, dlat * 1.4, 1e-4); z = Math.max(3, Math.min(17, Math.log2(S.W * 0.7 * 360 / (256 * span)))); }
  S.vue.z = z; A.centrerSur(r.lat, r.lon); etat("recherche").visible = true; rendre(); S.sale = true;
}
async function chercher() {
  const q = $("cq-adresse").value.trim(), zone = $("cq-resultats"); zone.textContent = "";
  if (q.length < 3) { A.toast(tj("engine/static/engine/js/geo_calques.js:79:32"), true); return; }
  zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_calques.js:80:47")));
  try {
    const rep = await fetch(URL_ADR + "?q=" + encodeURIComponent(q)), d = await rep.json().catch(() => ({}));
    zone.textContent = "";
    if (!rep.ok) { zone.append(el("div", {class: "geo-note"}, d.erreur || tj("engine/static/engine/js/geo_calques.js:84:76"))); return; }
    if (!d.resultats.length) { zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_calques.js:85:76"))); return; }
    d.resultats.forEach(r => {
      zone.append(el("button", {class: "geo-alerte", title: r.nom, onclick: () => aller(r)}, r.nom));
      const nomCourt = r.nom.split(",")[0];
      if (window.GEO_ITI) {   // le lieu cherché devient le point de livraison (ou la position actuelle) : ses coordonnées servent à l'itinéraire et à l'analyse
        zone.append(el("button", {class: "geo-btn", onclick: () => { window.GEO_ITI.destination(r.lat, r.lon, nomCourt); aller(r); }}, tj("engine/static/engine/js/geo_calques.js:lbl_livrer_ici")),
          el("button", {class: "geo-btn", onclick: () => { window.GEO_ITI.position(r.lat, r.lon, tj("engine/static/engine/js/geo_calques.js:91:99")); aller(r); }}, tj("engine/static/engine/js/geo_calques.js:lbl_je_suis_ici")));
      }
      if (!G.lectureSeule) zone.append(el("button", {class: "geo-btn", onclick: () => marquer(r)}, tj("engine/static/engine/js/geo_calques.js:93:101")));
    });
  } catch (e) { zone.textContent = ""; zone.append(el("div", {class: "geo-note"}, tj("engine/static/engine/js/geo_calques.js:95:84"))); }
}
async function marquer(r) {
  try {
    await A.api("/api/tables/" + G.tableId + "/geolocalisation/annotations/", {method: "POST", body: JSON.stringify({action: "creer", type: "point", geometrie: [[r.lat, r.lon]], titre: r.nom.split(",")[0], couleur: gyCouleur("--forme-rose", "#ff3b6b"), note: r.nom})});
    A.toast(tj("engine/static/engine/js/geo_calques.js:100:14")); if (window.GEO_ANNOT) window.GEO_ANNOT.recharger();
  } catch (e) { /* toast déjà affiché */ }
}
$("cq-chercher").onclick = chercher;
$("cq-adresse").addEventListener("keydown", e => { if (e.key === "Enter") chercher(); });
$("cq-repere-effacer").onclick = () => { C.repere = null; S.sale = true; };

// ── Import ───────────────────────────────────────────────────────────────────
function csrf() { const m = document.cookie.match(/(?:^|; )gy_csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ""; }
async function envoyer(fichier, apercu) {
  const f = new FormData(); f.append("fichier", fichier); if (apercu) f.append("apercu", "1");
  const rep = await fetch(URL_IMP, {method: "POST", headers: {"X-CSRFToken": csrf()}, body: f}), d = await rep.json().catch(() => ({}));
  if (!rep.ok) { A.toast(d.erreur || tj("engine/static/engine/js/geo_calques.js:112:39") + rep.status + ").", true); throw d; }
  return d;
}
if ($("cq-import")) $("cq-import").onclick = async () => {
  const fic = $("cq-fichier").files[0], zone = $("cq-import-bilan"); zone.textContent = "";
  if (!fic) { A.toast(tj("engine/static/engine/js/geo_calques.js:117:24"), true); return; }
  try {
    const ap = await envoyer(fic, true);
    if (!await GY.confirmer(tj("engine/static/engine/js/geo_calques.js:120:19") + ap.nb + tj("engine/static/engine/js/geo_calques.js:120:41") + fic.name + tj("engine/static/engine/js/geo_calques.js:120:78"))) return;
    const b = await envoyer(fic, false);
    A.toast(b.creees + tj("engine/static/engine/js/geo_calques.js:122:25") + (b.ignorees ? ", " + b.ignorees + tj("engine/static/engine/js/geo_calques.js:122:113") : "") + ".");
    [...(b.avertissements || []), ...(b.refus || [])].forEach(t => zone.append(el("div", {}, t)));
    $("cq-fichier").value = ""; if (window.GEO_ANNOT) window.GEO_ANNOT.recharger();
  } catch (e) { /* toast déjà affiché */ }
};
})();
