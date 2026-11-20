// Contexte de page : mémorise, pour une page d'analyse et une table ouverte, tous les champs saisis
// (cases cochées, listes, nombres, textes) et l'état de la page (onglet actif, dernière analyse lancée),
// puis les remet en place quand on revient sur la page — comme la page de géolocalisation.
// Stockage : localStorage du navigateur (il survit au changement d'onglet, au rechargement et au redémarrage du serveur).
// Usage :  const ctx = GYContexte.init({page: "series", tableId: 12});
//          const sauve = ctx.restaurer();   // remet les champs, renvoie les « extras » enregistrés
//          ctx.noter({demo: false});        // fusionne et enregistre un petit état de page
(function () {
"use strict";
const IGNORES = new Set(["button", "submit", "reset", "file", "password", "hidden", "image"]);
function cleChamp(e) {
  if (e.hasAttribute("data-sans-contexte")) return null;
  if (e.id) return "#" + e.id;
  const d = e.dataset, parts = Object.keys(d).sort().map(k => k + "=" + d[k]);
  if (parts.length) return "[" + (e.type || e.tagName.toLowerCase()) + "|" + parts.join("|") + "]";
  return e.name ? "n:" + e.name : null;
}
function champs(racine) {
  const vus = {}, liste = [];
  for (const e of racine.querySelectorAll("input, select, textarea")) {
    if (IGNORES.has(e.type)) continue;
    const base = cleChamp(e); if (!base) continue;
    const rang = vus[base] = (vus[base] || 0) + 1;
    liste.push([base + "@" + rang, e]);   // @rang : champs répétés (lignes dynamiques) distingués par leur ordre
  }
  return liste;
}
const valeur = e => (e.type === "checkbox" || e.type === "radio") ? e.checked : e.value;
function init(opt) {
  const cle = "gy_ctx_" + opt.page + "_" + opt.tableId;
  const racine = () => opt.racine ? document.querySelector(opt.racine) : document.body;
  let etat = {champs: {}, extras: {}}, restauration = false, minuteur = null;
  try { const m = JSON.parse(localStorage.getItem(cle) || "null"); if (m && m.champs) etat = {champs: m.champs, extras: m.extras || {}}; } catch (e) { /* stockage indisponible */ }
  function ecrire() { try { localStorage.setItem(cle, JSON.stringify(etat)); } catch (e) { /* plein ou indisponible : contexte valable pour cette page seulement */ } }
  function sauver() {
    if (restauration) return;
    const c = {}; for (const [k, e] of champs(racine())) c[k] = valeur(e);
    etat.champs = c; ecrire();
  }
  const differe = () => { if (restauration) return; clearTimeout(minuteur); minuteur = setTimeout(sauver, 150); };
  document.addEventListener("change", differe, true);
  document.addEventListener("input", differe, true);
  window.addEventListener("pagehide", () => { clearTimeout(minuteur); sauver(); });
  // Remet les champs ; plusieurs passes car un champ remis (ex. le test choisi) peut en fabriquer d'autres.
  function restaurer() {
    restauration = true;
    try {
      for (let passe = 0; passe < 6; passe++) {
        let change = false;
        for (const [k, e] of champs(racine())) {
          if (!(k in etat.champs)) continue;
          const v = etat.champs[k];
          if (e.type === "checkbox" || e.type === "radio") {
            if (e.checked !== !!v) { e.checked = !!v; e.dispatchEvent(new Event("change", {bubbles: true})); change = true; }
          } else if (e.tagName === "SELECT") {
            if (e.value !== v && Array.from(e.options).some(o => o.value === v)) { e.value = v; e.dispatchEvent(new Event("change", {bubbles: true})); change = true; }
          } else if (e.value !== v && typeof v === "string") { e.value = v; change = true; }
        }
        if (!change) break;
      }
    } finally { restauration = false; }
    return etat.extras;
  }
  function noter(extras) { Object.assign(etat.extras, extras); ecrire(); }
  function oublier() { etat = {champs: {}, extras: {}}; try { localStorage.removeItem(cle); } catch (e) { /* ignoré */ } }
  return {restaurer, noter, sauver, oublier, extras: () => etat.extras};
}
window.GYContexte = {init};
})();
