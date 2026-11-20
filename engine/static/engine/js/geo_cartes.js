// GÉOLOCALISATION : CARTES FLOTTANTES. Les panneaux de la barre latérale (objets, alertes, statistiques, outils…) flottent sur la carte, comme la légende et
// l'explorateur flottent sur le graphe de la page Ontologie : fond transparent (plus opaque au survol), repliables, masquables, coloriables.
// La barre latérale ne garde que les boutons principaux et le menu qui ouvre / ferme chaque carte.
// FRONT SEULEMENT : aucune requête, aucun calcul serveur. La carte « Distances et vitesses » met en évidence les statistiques déjà fournies par l'analyse
// (stats de chaque trajectoire : distance_m, duree_s, vitesse_moy_ms, vitesse_max_ms) ; geolocalisation.js la rafraîchit via window.GEO_CARTES.maj().
// État (ouverte / repliée / couleur de chaque carte) mémorisé par table dans ce navigateur (localStorage, jamais bloquant).
(function () {
"use strict";
const G = window.GEO || {}, A = window.GEO_API || null;
const cartes = Array.from(document.querySelectorAll(".geo-carte[data-carte]"));
if (!cartes.length) return;
const $ = id => document.getElementById(id);
const CLE = "geo_cartes_" + (G.tableId || "x");
const parId = {};
cartes.forEach(c => { parId[c.dataset.carte] = c; });

// ── État mémorisé ────────────────────────────────────────────────────────────
let etat = {};
try { etat = JSON.parse(localStorage.getItem(CLE) || "{}") || {}; } catch (e) { etat = {}; }
const sauver = () => { try { localStorage.setItem(CLE, JSON.stringify(etat)); } catch (e) { /* stockage indisponible : valable pour cette session */ } };
const auto = new Set();   // cartes ouvertes d'office (sans choix mémorisé) : ex. « Données analysées » tant que la carte est vide
const ouverte = id => (id in etat && "o" in etat[id]) ? !!etat[id].o : (auto.has(id) || parId[id].hasAttribute("data-defaut"));
const replie = id => !!(etat[id] && etat[id].r);
const couleur = id => (etat[id] && /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(etat[id].c || "")) ? etat[id].c : "";
const memoriser = (id, cle, v) => { etat[id] = Object.assign(etat[id] || {}, {[cle]: v}); sauver(); };

function titreDe(c) { const t = c.querySelector(".geo-carte-titre"); return t ? t.textContent.trim() : c.dataset.carte; }
function appliquer(id) {
  const c = parId[id], ouv = ouverte(id), col = couleur(id);
  c.hidden = !ouv;
  c.classList.toggle("repliee", replie(id));
  if (col) c.style.setProperty("--gc", col); else c.style.removeProperty("--gc");
  const b = document.querySelector('.geo-menu-btn[data-ouvrir="' + id + '"]');
  if (b) { b.setAttribute("aria-pressed", ouv ? "true" : "false"); if (col) b.style.setProperty("--pastille", col); else b.style.removeProperty("--pastille"); }
  const r = c.querySelector('[data-gc="replier"]');
  if (r) r.textContent = replie(id) ? "+" : "–";
}
function ouvrir(id, v, garder) {
  if (garder !== false) { auto.delete(id); memoriser(id, "o", !!v); }
  else if (v) auto.add(id); else auto.delete(id);
  if (v && replie(id)) memoriser(id, "r", false);
  appliquer(id);
  if (v) { try { parId[id].scrollIntoView({block: "nearest", behavior: "smooth"}); } catch (e) { /* sans effet */ } }
}

// ── Évènements : boutons d'en-tête, menu de la barre latérale ────────────────
cartes.forEach(c => {
  const id = c.dataset.carte;
  c.querySelectorAll("[data-gc]").forEach(b => b.addEventListener("click", async e => {
    e.stopPropagation();
    const act = b.dataset.gc;
    if (act === "fermer") ouvrir(id, false);
    else if (act === "replier") { memoriser(id, "r", !replie(id)); appliquer(id); }
    else if (act === "couleur") {
      if (!window.GYCouleur) return;
      const v = await window.GYCouleur.choisir({titre: tj("engine/static/engine/js/geo_cartes.js:56:57") + titreDe(c) + " »", valeur: couleur(id), apercu: titreDe(c), retirable: true});
      if (v === null) return;            // fenêtre fermée sans choix
      memoriser(id, "c", v || "");       // "" = retour à la couleur par défaut
      appliquer(id);
    }
  }));
  const tete = c.querySelector(".geo-carte-tete");
  if (tete) tete.addEventListener("dblclick", e => { if (e.target.closest(".gc-btn")) return; memoriser(id, "r", !replie(id)); appliquer(id); });
});
document.querySelectorAll(".geo-menu-btn[data-ouvrir]").forEach(b => b.addEventListener("click", () => { const id = b.dataset.ouvrir; if (parId[id]) ouvrir(id, !ouverte(id)); }));
const toutAfficher = $("gc-tout-afficher"), toutMasquer = $("gc-tout-masquer");
if (toutAfficher) toutAfficher.onclick = () => cartes.forEach(c => { const id = c.dataset.carte; auto.delete(id); memoriser(id, "o", true); memoriser(id, "r", false); appliquer(id); });
if (toutMasquer) toutMasquer.onclick = () => cartes.forEach(c => { auto.delete(c.dataset.carte); memoriser(c.dataset.carte, "o", false); appliquer(c.dataset.carte); });
cartes.forEach(c => appliquer(c.dataset.carte));

// ── Carte « Distances et vitesses » ──────────────────────────────────────────
const tri = {cle: "distance", desc: true};
const COLS = [["nom", "Objet"], ["distance", "Distance"], ["moy", "Moy."], ["max", "Max"], ["duree", tj("engine/static/engine/js/geo_cartes.js:73:103")]];
const kmh = v => (v * 3.6).toFixed(v * 3.6 < 100 ? 1 : 0);
function morceaux(txt) { const i = txt.lastIndexOf(" "); return i < 0 ? [txt, ""] : [txt.slice(0, i), txt.slice(i + 1)]; }
function tuile(titre, valeur, unite, fort) {
  const t = document.createElement("div"); t.className = "mv-tuile" + (fort ? " fort" : "");
  const a = document.createElement("div"); a.className = "t"; a.textContent = titre;
  const b = document.createElement("div"); b.className = "v"; b.textContent = valeur;
  if (unite) { const u = document.createElement("span"); u.className = "u"; u.textContent = unite; b.append(u); }
  t.append(a, b); return t;
}
function rendreMouvement() {
  const tu = $("mv-tuiles"), tb = $("mv-table"), note = $("mv-note");
  if (!tu || !tb || !note) return;
  tu.textContent = ""; tb.textContent = ""; note.textContent = "";
  const S = A && A.S, D = S && S.D;
  if (!D || D.vide) { note.textContent = tj("engine/static/engine/js/geo_cartes.js:88:43"); return; }
  const lignes = D.objets.filter(o => (!o.mode || o.mode === "trajectoire") && o.stats && S.visibles[o.id] !== false).map(o => ({
    o, nom: o.nom, distance: o.stats.distance_m || 0, moy: o.stats.vitesse_moy_ms || 0, max: o.stats.vitesse_max_ms || 0, duree: o.stats.duree_s || 0}));
  if (!lignes.length) { note.textContent = tj("engine/static/engine/js/geo_cartes.js:91:45"); return; }
  const dist = lignes.reduce((s, l) => s + l.distance, 0);
  const avecDuree = lignes.filter(l => l.duree > 0);
  const vitMoy = avecDuree.length ? avecDuree.reduce((s, l) => s + l.distance, 0) / avecDuree.reduce((s, l) => s + l.duree, 0) : 0;
  const vitMax = Math.max(...lignes.map(l => l.max)), dureeMax = Math.max(...lignes.map(l => l.duree));
  const [dv, du] = morceaux(A.fmtM(dist));
  tu.append(tuile(tj("engine/static/engine/js/geo_cartes.js:lbl_distance_totale"), dv, du, true), tuile(tj("engine/static/engine/js/geo_cartes.js:97:60"), kmh(vitMoy), "km/h", true),
    tuile(tj("engine/static/engine/js/geo_cartes.js:lbl_vitesse_max"), kmh(vitMax), "km/h", false), tuile(tj("engine/static/engine/js/geo_cartes.js:98:62"), dureeMax > 0 ? A.fmtDuree(dureeMax) : "—", "", false));

  lignes.sort((a, b) => (tri.cle === "nom" ? String(a.nom).localeCompare(String(b.nom)) : a[tri.cle] - b[tri.cle]) * (tri.desc ? -1 : 1));
  const table = document.createElement("table"); table.className = "mv-table";
  const tete = document.createElement("tr");
  COLS.forEach(([cle, lib]) => {
    const th = document.createElement("th"); th.textContent = lib + (tri.cle === cle ? (tri.desc ? " ▾" : " ▴") : "");
    th.title = tj("engine/static/engine/js/geo_cartes.js:105:17"); if (tri.cle === cle) th.className = "tri";
    th.onclick = () => { if (tri.cle === cle) tri.desc = !tri.desc; else { tri.cle = cle; tri.desc = cle !== "nom"; } rendreMouvement(); };
    tete.append(th);
  });
  table.append(tete);
  const dMax = Math.max(...lignes.map(l => l.distance), 1e-9);
  lignes.forEach(l => {
    const tr = document.createElement("tr"); tr.className = "mv-ligne" + (S.selection === l.o.id ? " choisi" : ""); tr.title = tj("engine/static/engine/js/geo_cartes.js:112:129");
    const col = S.couleurs[l.o.id] || gyCouleur("--accent","#6bcfff");
    const nom = document.createElement("td"), n = document.createElement("div"); n.className = "mv-nom";
    const p = document.createElement("span"); p.className = "puce"; p.style.background = col;
    const t = document.createElement("span"); t.textContent = l.nom; n.append(p, t); nom.append(n);
    const d = document.createElement("td"); d.className = "mv-dist"; d.textContent = A.fmtM(l.distance);
    d.style.backgroundImage = "linear-gradient(" + col + "," + col + ")"; d.style.setProperty("--mv-part", Math.max(2, Math.round(100 * l.distance / dMax)) + "%");
    const m = document.createElement("td"); m.textContent = kmh(l.moy);
    const x = document.createElement("td"); x.textContent = kmh(l.max);
    const u = document.createElement("td"); u.textContent = l.duree > 0 ? A.fmtDuree(l.duree) : "—";
    tr.append(nom, d, m, x, u);
    tr.onclick = () => { if (A.selectionnerObjet) A.selectionnerObjet(l.o); rendreMouvement(); };
    table.append(tr);
  });
  tb.append(table);
  note.textContent = tj("engine/static/engine/js/geo_cartes.js:127:23");
}

// ── Rafraîchissement demandé par geolocalisation.js à chaque rendu des panneaux ──
let dejaOuvertAuto = false;
function maj() {
  const D = A && A.S && A.S.D;
  // Carte vide : on ouvre « Données analysées » (sans mémoriser le choix) pour que la première étape soit visible.
  if (D && D.vide && !dejaOuvertAuto && parId.donnees && !(etat.donnees && "o" in etat.donnees)) { dejaOuvertAuto = true; ouvrir("donnees", true, false); }
  try { rendreMouvement(); } catch (e) { const n = $("mv-note"); if (n) n.textContent = tj("engine/static/engine/js/geo_cartes.js:136:90"); }
}
window.GEO_CARTES = {maj, ouvrir: (id, v) => { if (parId[id]) ouvrir(id, v !== false); }};
maj();
})();
